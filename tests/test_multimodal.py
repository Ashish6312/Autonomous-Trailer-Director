"""Model gateway: request checks, fingerprints, Gemini normalisation, routing, budget, fallback, record/replay.

The Gemini client is replaced by a fake; no test calls a live API.
"""

import shutil
import socket
from types import SimpleNamespace

import httpx
import pytest
from google.genai import errors
from support import DATA_DIR

from trailer_director.cli import main
from trailer_director.data import load_episode_package
from trailer_director.multimodal import (
    Capability,
    GeminiProvider,
    InputPart,
    MockProvider,
    ModelBudgetExceeded,
    ModelInputError,
    ModelRequest,
    ModelRouter,
    ModelUnavailableError,
    RecordingProvider,
    ReplayProvider,
    input_records,
    observation_from,
    request_fingerprint,
    sha256_of,
)
from trailer_director.multimodal.registry import providers
from trailer_director.repair.budget import BudgetLedger

FIXTURES = DATA_DIR.parent / "tests" / "fixtures" / "media"
IMAGE, AUDIO, VIDEO = FIXTURES / "test_frame.jpg", FIXTURES / "test_audio.wav", FIXTURES / "test_video.mp4"
MODELS = {c: f"gemini-test-{c}" for c in Capability}


class FakeModels:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(reply, BaseException):
            raise reply
        return reply


def _reply(text="A colourful test pattern.", served="gemini-test-001"):
    usage = SimpleNamespace(prompt_token_count=258, candidates_token_count=9)
    return SimpleNamespace(text=text, usage_metadata=usage, model_version=served)


def _gemini(*replies):
    models = FakeModels(*(replies or (_reply(),)))
    return GeminiProvider(MODELS, timeout_seconds=30, client=SimpleNamespace(models=models)), models


def _request(capability=Capability.VISION, path=IMAGE, media_type="image", model=None):
    return ModelRequest(
        capability=capability,
        inputs=[InputPart(type=media_type, path=path), InputPart(type="text", content="Describe it.")],
        model=model,
    )


def _router(provider, calls=24, fallback=None):
    sheet = load_episode_package(DATA_DIR).cost_sheet
    ledger = BudgetLedger(sheet, max_repair_attempts=0, max_model_calls=calls)
    return ModelRouter({c: provider for c in Capability}, ledger, fallback), ledger


class TestRequests:
    def test_capability_and_inputs_must_agree(self):
        with pytest.raises(ValueError, match="needs image"):
            ModelRequest(capability=Capability.VISION, inputs=[InputPart(type="text", content="hi")])
        with pytest.raises(ValueError, match="cannot carry media"):
            ModelRequest(capability=Capability.TEXT, inputs=[InputPart(type="image", path=IMAGE)])
        with pytest.raises(ValueError, match="needs audio"):
            ModelRequest(capability=Capability.AUDIO, inputs=[InputPart(type="video", path=VIDEO)])

    def test_missing_or_mismatched_media_fails_before_any_call(self, tmp_path):
        provider, models = _gemini()
        router, _ = _router(provider)

        with pytest.raises(ModelInputError, match="not found"):
            router.run(_request(path=tmp_path / "absent.jpg"))
        with pytest.raises(ModelInputError, match="not a supported image"):
            router.run(_request(path=AUDIO))
        assert models.calls == []

    def test_fingerprint_is_the_content_hash_not_the_path(self, tmp_path):
        copy = tmp_path / "renamed.jpg"
        shutil.copy(IMAGE, copy)
        changed = tmp_path / "changed.jpg"
        changed.write_bytes(IMAGE.read_bytes() + b"\0")

        def key(path):
            request = _request(path=path)
            return request_fingerprint(request, input_records(request))

        [record] = [r for r in input_records(_request()) if r.type == "image"]
        assert record.sha256 == sha256_of(IMAGE)
        assert key(IMAGE) == key(copy) != key(changed)


class TestGemini:
    @pytest.mark.parametrize(
        ("capability", "path", "media_type", "mime"),
        [
            (Capability.VISION, IMAGE, "image", "image/jpeg"),
            (Capability.AUDIO, AUDIO, "audio", "audio/wav"),
            (Capability.VIDEO, VIDEO, "video", "video/mp4"),
        ],
    )
    def test_media_request_is_sent_inline_and_normalised(self, capability, path, media_type, mime):
        provider, models = _gemini()
        router, _ = _router(provider)

        response = router.run(_request(capability, path, media_type))

        [call] = models.calls
        media_part, prompt = call["contents"]
        assert call["model"] == f"gemini-test-{capability}"
        assert (media_part.inline_data.mime_type, media_part.inline_data.data) == (mime, path.read_bytes())
        assert prompt == "Describe it."
        assert call["config"].automatic_function_calling.disable is True
        assert (response.provider, response.model, response.capability) == ("gemini", "gemini-test-001", capability)
        assert (response.usage.input_tokens, response.usage.output_tokens) == (258, 9)
        assert response.latency_seconds >= 0 and response.estimated_cost == 0.015

    def test_unconfigured_model_is_reported_before_any_call(self):
        provider = GeminiProvider({}, timeout_seconds=30, client=SimpleNamespace(models=FakeModels(_reply())))

        with pytest.raises(ModelUnavailableError, match="set TRAILER_VISION_MODEL") as err:
            _router(provider)[0].run(_request())

        assert err.value.retryable is False

    def test_missing_api_key_is_a_clear_non_retryable_error(self, monkeypatch):
        for name in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
            monkeypatch.delenv(name, raising=False)

        with pytest.raises(ModelUnavailableError, match="set GEMINI_API_KEY") as err:
            _router(GeminiProvider(MODELS, timeout_seconds=30))[0].run(_request())

        assert err.value.retryable is False

    @pytest.mark.parametrize(
        ("failure", "retryable"),
        [
            (errors.ClientError(429, {"error": {"message": "quota", "status": "RESOURCE_EXHAUSTED"}}), True),
            (errors.ServerError(503, {"error": {"message": "overloaded", "status": "UNAVAILABLE"}}), True),
            (errors.ClientError(400, {"error": {"message": "bad request", "status": "INVALID_ARGUMENT"}}), False),
            (httpx.ReadTimeout("slow"), True),
        ],
    )
    def test_provider_failures_are_classified(self, failure, retryable):
        provider, _ = _gemini(failure)

        with pytest.raises(ModelUnavailableError) as err:
            _router(provider)[0].run(_request())

        assert err.value.retryable is retryable

    def test_programming_errors_are_not_disguised_as_outages(self):
        provider, _ = _gemini(KeyError("bug"))

        with pytest.raises(KeyError):
            _router(provider)[0].run(_request())


class TestRouting:
    def test_capability_without_a_provider_is_refused(self):
        sheet = load_episode_package(DATA_DIR).cost_sheet
        router = ModelRouter({Capability.TEXT: MockProvider()}, BudgetLedger(sheet, 0))

        with pytest.raises(ModelUnavailableError, match="no provider configured for vision"):
            router.run(_request())

    def test_budget_is_checked_before_each_call(self):
        provider, models = _gemini()
        router, ledger = _router(provider, calls=1)

        router.run(_request())
        with pytest.raises(ModelBudgetExceeded, match="call not made"):
            router.run(_request(Capability.AUDIO, AUDIO, "audio"))

        assert len(models.calls) == 1
        assert (ledger.usage().model_calls, ledger.usage().input_tokens) == (1, 258)

    def test_no_fallback_unless_configured(self):
        provider, _ = _gemini(errors.ServerError(503, {"error": {"message": "down", "status": "UNAVAILABLE"}}))

        with pytest.raises(ModelUnavailableError, match="503"):
            _router(provider)[0].run(_request())

    def test_configured_fallback_is_used_and_recorded(self, tmp_path):
        recorded, _ = _gemini()
        _router(RecordingProvider(recorded, tmp_path))[0].run(_request())
        down, _ = _gemini(errors.ServerError(503, {"error": {"message": "down", "status": "UNAVAILABLE"}}))
        router, _ = _router(down, fallback=ReplayProvider(tmp_path))

        response = router.run(_request())

        assert response.replayed and response.provider == "gemini"
        assert router.fallbacks and router.fallbacks[0].startswith("vision: gemini unavailable")


class TestRecordAndReplay:
    def test_replay_returns_the_recorded_response_without_a_call(self, tmp_path, monkeypatch):
        live, models = _gemini()
        first = _router(RecordingProvider(live, tmp_path))[0].run(_request())

        def refuse(*args, **kwargs):
            raise AssertionError("network access attempted")

        for name in ("socket", "create_connection", "getaddrinfo"):
            monkeypatch.setattr(socket, name, refuse)
        again = [_router(ReplayProvider(tmp_path))[0].run(_request()) for _ in range(2)]

        assert len(models.calls) == 1
        assert again[0] == again[1]
        assert again[0].text == first.text and again[0].usage == first.usage
        assert (again[0].replayed, again[0].latency_seconds) == (True, None)

    def test_recording_holds_fingerprints_not_media_or_credentials(self, tmp_path, monkeypatch):
        canary = "planted-test-credential-0000"
        monkeypatch.setenv("GEMINI_API_KEY", canary)
        live, _ = _gemini()

        response = _router(RecordingProvider(live, tmp_path))[0].run(_request())

        [path] = list(tmp_path.glob("*.json"))
        stored = path.read_text(encoding="utf-8")
        assert path.stem == response.request_fingerprint
        assert sha256_of(IMAGE) in stored
        assert canary not in stored
        assert "inline_data" not in stored and len(stored) < 4000

    def test_missing_recording_is_reported(self, tmp_path):
        with pytest.raises(ModelUnavailableError, match="no recorded response"):
            _router(ReplayProvider(tmp_path))[0].run(_request())


class TestMockAndEvidence:
    def test_mock_output_is_deterministic_labelled_and_free(self):
        router, ledger = _router(MockProvider())

        first, second = router.run(_request()), router.run(_request())

        assert first == second and first.provider == "mock"
        assert "not a model observation" in first.text
        assert observation_from(first).status == "mock_output"
        assert ledger.usage().model_calls == 0

    def test_observations_are_never_authoritative(self):
        provider, _ = _gemini()
        observation = observation_from(_router(provider)[0].run(_request()))

        assert (observation.status, observation.authoritative) == ("model_observation", False)
        assert observation.source_sha256 == sha256_of(IMAGE)


def test_registry_reports_only_implemented_capabilities():
    table = {info.provider: info.capabilities for info in providers()}

    assert table["gemini"] == frozenset(Capability)
    assert table["claude"] == {Capability.TEXT}


class TestCli:
    def test_list_reports_credentials_without_values(self, monkeypatch, capsys):
        monkeypatch.setenv("GEMINI_API_KEY", "planted-test-credential-0000")

        assert main(["models", "list"]) == 0

        out = capsys.readouterr().out
        assert "GEMINI_API_KEY set" in out and "planted-test-credential-0000" not in out

    def test_mock_record_then_replay(self, tmp_path, capsys):
        base = ["models", "test", "--capability", "video", "--data-dir", str(DATA_DIR), "--input", str(VIDEO)]

        assert main([*base, "--mode", "mock", "--record-dir", str(tmp_path)]) == 0
        assert main([*base, "--mode", "replay", "--replay-dir", str(tmp_path)]) == 0

        out = capsys.readouterr().out
        assert "replayed: True" in out and "provider: mock" in out

    def test_live_without_configuration_is_unavailable_not_faked(self, monkeypatch, capsys):
        monkeypatch.delenv("TRAILER_VISION_MODEL", raising=False)

        code = main(["models", "test", "--capability", "vision", "--data-dir", str(DATA_DIR)])

        assert code == 1
        assert "MODEL_UNAVAILABLE" in capsys.readouterr().err

    def test_model_budget_flag_is_validated_before_any_call(self, capsys):
        args = ["models", "test", "--capability", "vision", "--max-model-calls", "99", "--data-dir", str(DATA_DIR)]

        assert main(args) == 2
        assert "cost sheet's 24" in capsys.readouterr().err


def test_fixture_files_are_small_and_documented():
    for path in (IMAGE, AUDIO, VIDEO):
        assert path.stat().st_size < 50_000
    assert "not episode media" in (FIXTURES / "README.md").read_text(encoding="utf-8").lower()


def test_invalid_llm_setting_is_a_configuration_error_not_a_crash(monkeypatch, capsys):
    monkeypatch.setenv("TRAILER_DIRECTOR_LLM_TIMEOUT_SECONDS", "soon")

    assert main(["models", "test", "--capability", "vision", "--data-dir", str(DATA_DIR)]) == 2
    assert "is not a number" in capsys.readouterr().err
