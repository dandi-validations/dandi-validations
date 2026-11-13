import json
import pathlib

import pytest

from dandi_validations import (
    CAPSULE_TABLE_COLUMNS,
    Capsule,
    capsule_row,
    load_pipeline,
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
    results = [
        {"id": "DANDI.FILE_CONTENT_STREAMED", "severity": "INFO", "path": "sub-01/a.nwb"},
        {"id": "DANDI.FILE_CONTENT_STREAMED", "severity": "INFO", "path": "sub-02/b.nwb"},
        {"id": "NWBI.check_subject_age", "severity": "ERROR", "path": "sub-01/a.nwb"},
        {"id": "pynwb.x", "severity": "ERROR", "path": "sub-01/a.nwb"},
        {"id": "NWBI.check_units_resolution_is_set", "severity": "WARNING", "path": "sub-02/b.nwb"},
    ]
    (capsule.derivatives_directory / "validation.jsonl").write_text(
        "".join(json.dumps(result) + "\n" for result in results),
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
        "version": load_pipeline().container_version,
        "total_results": 5,
        "critical": 0,
        "error": 2,
        "warning": 1,
        "hint": 0,
        "info": 2,
        "files_streamed": 2,
        "files_missing_content": 0,
        "files_with_issues": 2,
        "wall_clock_seconds": 41.9,
        "assets": 2,
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


@pytest.mark.ai_generated
def test_write_records_by_tag(tmp_path: pathlib.Path) -> None:
    older = "d0.82.0-s0.14.0-n0.7.2-p4.2.0-h6.2.0-b3.0.2-v0.2.0"
    newer = "d0.82.0-s0.14.0-n0.7.2-p4.2.0-h6.2.0-b3.0.2-v0.3.0"
    rows = [
        {"dandiset_id": "000006", "capsule": f"pipeline-dandi+validate_tag-{older}", "status": "successful"},
        {"dandiset_id": "000003", "capsule": f"pipeline-dandi+validate_tag-{older}", "status": "failed"},
        {"dandiset_id": "000006", "capsule": f"pipeline-dandi+validate_tag-{newer}", "status": "stalled"},
    ]
    written = write_records(rows=rows, output_directory=tmp_path / "records", by_tag=True)
    assert sorted(str(path.relative_to(tmp_path / "records")) for path in written) == sorted(
        f"{tag}/{name}" for tag in (older, newer) for name in ("capsules.tsv", "capsules.json")
    )
    older_table = read_capsule_table((tmp_path / "records" / older / "capsules.tsv").read_bytes())
    assert [row["dandiset_id"] for row in older_table] == ["000003", "000006"]
    newer_table = read_capsule_table((tmp_path / "records" / newer / "capsules.tsv").read_bytes())
    assert [row["status"] for row in newer_table] == ["stalled"]
