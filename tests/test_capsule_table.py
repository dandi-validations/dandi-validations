import json

from dandi_validations._capsule import prepare_capsule
from dandi_validations._capsule_table import COLUMNS, capsule_row, read_table
from dandi_validations._collect import write_records


def _successful_capsule(root, assets_jsonld, dandiset_yaml):
    capsule = prepare_capsule(
        repository_root=root, dandiset_id="000123", assets_jsonld=assets_jsonld, dandiset_yaml=dandiset_yaml
    ).capsule
    capsule.logs_directory.mkdir()
    (capsule.logs_directory / "run.json").write_text(json.dumps({"exit_code": 0}))
    (capsule.logs_directory / "duct_info.json").write_text(
        json.dumps({"execution_summary": {"wall_clock_time": 41.948, "peak_rss": 1925668864}})
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
            }
        )
    )
    return capsule


def test_capsule_row(git_repository, assets_jsonld, dandiset_yaml):
    capsule = _successful_capsule(git_repository, assets_jsonld, dandiset_yaml)
    row = capsule_row(capsule.path)
    assert list(row) == list(COLUMNS)
    assert row["capsule"] == capsule.name and row["status"] == "successful"
    assert row["dandiset_id"] == "000123" and row["version"] == capsule.tag
    assert row["error"] == 576 and row["warning"] == 317 and row["wall_clock_seconds"] == 41.9
    assert row["assets"] == 2 and row["skipped_assets"] == 2
    assert row["claimed_by"].startswith(("local:", "https://"))


def test_write_records(tmp_path):
    rows = [
        {"dandiset_id": "000006", "capsule": "pipeline-dandi+validate_tag-b", "status": "failed"},
        {"dandiset_id": "000003", "capsule": "pipeline-dandi+validate_tag-a", "status": "successful"},
    ]
    written = write_records(rows, tmp_path / "records")
    assert sorted(path.name for path in written) == ["capsules.json", "capsules.tsv"]
    table = read_table((tmp_path / "records" / "capsules.tsv").read_bytes())
    assert [row["dandiset_id"] for row in table] == ["000003", "000006"]
    assert table[0]["error"] == "n/a"
    sidecar = json.loads((tmp_path / "records" / "capsules.json").read_text())
    assert set(sidecar) == set(COLUMNS) and "Levels" in sidecar["status"]
