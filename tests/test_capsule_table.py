import json
import pathlib

import pytest

from dandi_validations import (
    CAPSULE_TABLE_COLUMNS,
    Capsule,
    capsule_row,
    prepare_capsule,
    read_capsule_table,
    write_records,
)


def _successful_capsule(*, root: pathlib.Path, assets_jsonld: bytes, dandiset_yaml: bytes) -> Capsule:
    capsule = prepare_capsule(
        repository_root=root,
        dandiset_id="000123",
        assets_jsonld=assets_jsonld,
        dandiset_yaml=dandiset_yaml,
    ).capsule
    assert capsule is not None
    capsule.logs_directory.mkdir()
    (capsule.logs_directory / "run.json").write_text(json.dumps({"exit_code": 0}))
    (capsule.logs_directory / "duct_info.json").write_text(
        json.dumps({"execution_summary": {"wall_clock_time": 41.948, "peak_rss": 1925668864}}),
    )
    capsule.derivatives_directory.mkdir()
    (capsule.derivatives_directory / "summary.json").write_text(
        json.dumps(
            {
                "dandi_validate_exit_code": 1,
                "total_results": 946,
                "by_severity": {"CRITICAL": 0, "ERROR": 576, "WARNING": 317, "HINT": 0, "INFO": 53},
                "files_streamed": 2,
                "files_missing_content": 0,
                "files_with_issues": 2,
            },
        ),
    )
    return capsule


@pytest.mark.ai_generated
def test_capsule_row(git_repository: pathlib.Path, assets_jsonld: bytes, dandiset_yaml: bytes) -> None:
    capsule = _successful_capsule(root=git_repository, assets_jsonld=assets_jsonld, dandiset_yaml=dandiset_yaml)
    row = capsule_row(capsule.path)
    assert list(row) == list(CAPSULE_TABLE_COLUMNS)
    expected = {
        "capsule": capsule.name,
        "status": "successful",
        "dandiset_id": "000123",
        "version": capsule.tag,
        "error": 576,
        "warning": 317,
        "wall_clock_seconds": 41.9,
        "assets": 2,
        "skipped_assets": 2,
    }
    assert {key: row[key] for key in expected} == expected
    assert row["claimed_by"].startswith(("local:", "https://"))


@pytest.mark.ai_generated
def test_write_records(tmp_path: pathlib.Path) -> None:
    rows = [
        {"dandiset_id": "000006", "capsule": "pipeline-dandi+validate_tag-b", "status": "failed"},
        {"dandiset_id": "000003", "capsule": "pipeline-dandi+validate_tag-a", "status": "successful"},
    ]
    written = write_records(rows=rows, output_directory=tmp_path / "records")
    assert sorted(path.name for path in written) == ["capsules.json", "capsules.tsv"]
    table = read_capsule_table((tmp_path / "records" / "capsules.tsv").read_bytes())
    assert [row["dandiset_id"] for row in table] == ["000003", "000006"]
    assert table[0]["error"] == "n/a"
    sidecar = json.loads((tmp_path / "records" / "capsules.json").read_text())
    assert set(sidecar) == set(CAPSULE_TABLE_COLUMNS)
    assert "Levels" in sidecar["status"]
