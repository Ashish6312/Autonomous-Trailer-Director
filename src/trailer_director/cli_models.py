"""CLI command ``models``: list providers, show capabilities, and test one capability on a local file."""

import argparse
import sys
from pathlib import Path

from trailer_director.config import ConfigError, llm_settings, modality_models, run_limits
from trailer_director.data import load_episode_package
from trailer_director.errors import DatasetLoadError, DatasetValidationError
from trailer_director.multimodal import (
    Capability,
    GeminiProvider,
    InputPart,
    MockProvider,
    ModelInputError,
    ModelProvider,
    ModelRequest,
    ModelRouter,
    ModelUnavailableError,
    RecordingProvider,
    ReplayProvider,
    observation_from,
)
from trailer_director.multimodal.registry import providers
from trailer_director.repair.budget import BudgetLedger

EXIT_OK = 0
EXIT_UNAVAILABLE = 1
EXIT_CONFIG_ERROR = 2
FIXTURE_DIR = Path("tests/fixtures/media")
FIXTURES = {
    Capability.VISION: FIXTURE_DIR / "test_frame.jpg",
    Capability.AUDIO: FIXTURE_DIR / "test_audio.wav",
    Capability.VIDEO: FIXTURE_DIR / "test_video.mp4",
}
PROMPTS = {
    Capability.TEXT: "Reply with one short sentence confirming you received this test request.",
    Capability.VISION: "Describe what is visible in this image in one sentence.",
    Capability.AUDIO: "Describe what can be heard in this audio clip in one sentence.",
    Capability.VIDEO: "Describe what happens in this video clip in one sentence.",
}
DEFAULT_RECORD_DIR = Path("runs/model_calls")


def add_models_command(commands: argparse._SubParsersAction, dataset: argparse.ArgumentParser) -> None:
    models = commands.add_parser("models", help="multimodal model gateway: providers, capabilities, test call")
    actions = models.add_subparsers(dest="models_command", required=True)
    actions.add_parser("list", help="providers, their role, credentials status (never values) and models")
    actions.add_parser("capabilities", help="which capabilities each provider adapter implements")
    test = actions.add_parser("test", parents=[dataset], help="send one test input through the gateway")
    test.add_argument("--capability", required=True, choices=[c.value for c in Capability])
    test.add_argument("--mode", choices=["live", "mock", "replay"], default="live")
    test.add_argument("--input", type=Path, help="media file (default: the synthetic test fixture for the capability)")
    test.add_argument("--model", help="override the configured model for this call")
    test.add_argument("--record-dir", type=Path, help=f"save the response for replay (e.g. {DEFAULT_RECORD_DIR})")
    test.add_argument(
        "--replay-dir", type=Path, default=DEFAULT_RECORD_DIR, help="recorded responses for --mode replay"
    )
    test.add_argument("--fallback-replay", action="store_true", help="if the live provider fails, use the replay dir")
    test.add_argument("--max-model-calls", type=int, help="cap model calls below the cost sheet")
    test.add_argument("--max-cost", type=float, help="cap estimated cost below the cost sheet")


def run_models(args: argparse.Namespace) -> int:
    if args.models_command == "list":
        for info in providers():
            models = ", ".join(f"{k}={v}" for k, v in info.models.items())
            print(f"{info.provider:<7} {info.role}\n        credentials: {info.credentials}\n        models: {models}")
        return EXIT_OK
    if args.models_command == "capabilities":
        print(f"{'provider':<9}" + "".join(f"{c.value:<8}" for c in Capability))
        for info in providers():
            print(f"{info.provider:<9}" + "".join(f"{'yes' if c in info.capabilities else '-':<8}" for c in Capability))
        print("Only capabilities implemented by each adapter are shown.")
        return EXIT_OK
    return _test(args)


def _test(args: argparse.Namespace) -> int:
    capability = Capability(args.capability)
    try:
        package = load_episode_package(args.data_dir)
        limits = run_limits(package.cost_sheet, args.max_model_calls, args.max_cost)
        request = _request(capability, args.input, args.model)
        primary: ModelProvider = _provider(args)
    except (DatasetLoadError, DatasetValidationError, ConfigError, ValueError) as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    ledger = BudgetLedger(
        package.cost_sheet, max_repair_attempts=0, max_model_calls=limits.max_model_calls, max_cost=limits.max_cost
    )
    if args.record_dir and args.mode != "replay":
        primary = RecordingProvider(primary, args.record_dir)
    fallback = ReplayProvider(args.replay_dir) if args.fallback_replay and args.mode != "replay" else None
    router = ModelRouter({capability: primary}, ledger, fallback)
    try:
        response = router.run(request)
    except (ModelUnavailableError, ModelInputError) as exc:
        print(f"MODEL_UNAVAILABLE {exc}", file=sys.stderr)
        return EXIT_UNAVAILABLE
    observation = observation_from(response)
    media = next((i for i in response.inputs if i.type != "text"), None)
    source = "synthetic test fixture (not episode media)" if args.input is None else "user-supplied file"
    usage = ledger.usage()
    print(f"capability: {capability}  provider: {response.provider}  model: {response.model}")
    if media:
        print(f"input: {media.file_name} ({media.mime_type}, {media.size_bytes} bytes), {source}")
        print(f"input sha256: {media.sha256}")
    print(f"request fingerprint: {response.request_fingerprint}")
    latency = f"{response.latency_seconds}s" if response.latency_seconds is not None else "-"
    tokens = f"{response.usage.input_tokens} in / {response.usage.output_tokens} out" if response.usage else "-"
    print(f"replayed: {response.replayed}  latency: {latency}  tokens: {tokens}")
    spent = f"{usage.estimated_cost:.3f} {usage.currency}"
    print(f"budget: {usage.model_calls}/{usage.max_model_calls} calls, {spent} estimated (flat rate, not billing)")
    if router.fallbacks:
        print(f"fallback used: {'; '.join(router.fallbacks)}")
    print(f"observation {observation.evidence_id} ({observation.status}, not authoritative): {observation.observation}")
    return EXIT_OK


def _request(capability: Capability, path: Path | None, model: str | None) -> ModelRequest:
    inputs = [InputPart(type="text", content=PROMPTS[capability])]
    if capability is not Capability.TEXT:
        media_type = {Capability.VISION: "image", Capability.AUDIO: "audio", Capability.VIDEO: "video"}[capability]
        inputs.insert(0, InputPart(type=media_type, path=path or FIXTURES[capability]))
    return ModelRequest(capability=capability, inputs=inputs, model=model)


def _provider(args: argparse.Namespace) -> ModelProvider:
    if args.mode == "mock":
        return MockProvider()
    if args.mode == "replay":
        return ReplayProvider(args.replay_dir)
    return GeminiProvider(modality_models(), llm_settings().timeout_seconds)
