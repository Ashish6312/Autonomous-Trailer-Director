import json
import shutil

import pytest
from support import DATA_DIR

from trailer_director.cli import EXIT_INVALID, EXIT_LOAD_ERROR, EXIT_OK, main
from trailer_director.data import load_episode_package, load_raw_dataset
from trailer_director.errors import DatasetLoadError, DatasetValidationError


@pytest.fixture
def data_copy(tmp_path):
    target = tmp_path / "data"
    shutil.copytree(DATA_DIR, target)
    return target


def test_missing_file_raises_load_error(data_copy):
    (data_copy / "rights" / "music.json").unlink()

    with pytest.raises(DatasetLoadError, match="missing dataset file"):
        load_raw_dataset(data_copy)


def test_invalid_json_reports_position(data_copy):
    (data_copy / "episode" / "scenes.json").write_text("[{", encoding="utf-8")

    with pytest.raises(DatasetLoadError, match="invalid JSON at line 1"):
        load_raw_dataset(data_copy)


def test_invalid_dataset_raises_with_report(data_copy):
    path = data_copy / "episode" / "scenes.json"
    scenes = json.loads(path.read_text(encoding="utf-8"))
    scenes[0]["location_id"] = "LOC_MOON"
    path.write_text(json.dumps(scenes), encoding="utf-8")

    with pytest.raises(DatasetValidationError) as excinfo:
        load_episode_package(data_copy)

    assert "unknown location id 'LOC_MOON'" in excinfo.value.report.format()


def test_cli_exit_codes(data_copy, tmp_path, capsys):
    assert main(["validate-data", "--data-dir", str(data_copy)]) == EXIT_OK
    assert "Dataset valid" in capsys.readouterr().out

    path = data_copy / "rights" / "actors.json"
    actors = json.loads(path.read_text(encoding="utf-8"))
    actors[0]["valid_until"] = "2020-01-01"
    path.write_text(json.dumps(actors), encoding="utf-8")
    assert main(["validate-data", "--data-dir", str(data_copy)]) == EXIT_INVALID
    assert "ERROR actor_rights.ACT_01" in capsys.readouterr().out

    assert main(["validate-data", "--data-dir", str(tmp_path / "absent")]) == EXIT_LOAD_ERROR
