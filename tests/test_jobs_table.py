import json

from dandi_validations._capsule import prepare_capsule
from dandi_validations._collect import latest_rows, write_records
from dandi_validations._jobs_table import COLUMNS, read_table, write_jobs_table


def test_jobs_table_reads_capsule_records(git_repository, assets_jsonld, dandiset_yaml):
    capsule = prepare_capsule(
        repository_root=git_repository, dandiset_id="000123", assets_jsonld=assets_jsonld, dandiset_yaml=dandiset_yaml
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

    table_path, sidecar_path = write_jobs_table(capsule.path.parent)
    rows = read_table(table_path.read_bytes())
    assert len(rows) == 1
    row = rows[0]
    assert list(row) == list(COLUMNS)
    assert row["status"] == "successful" and row["job_id"] == capsule.job_id
    assert row["error"] == "576" and row["warning"] == "317" and row["wall_clock_seconds"] == "41.9"
    assert row["assets"] == "2" and row["skipped_assets"] == "2"
    assert row["claimed_by"].startswith("local:") or row["claimed_by"].startswith("https://")
    sidecar = json.loads(sidecar_path.read_text())
    assert set(sidecar) == set(COLUMNS) and "Levels" in sidecar["status"]


def test_collected_records(tmp_path):
    rows = [
        {"dandiset_id": "000006", "job_id": "job-261105aaaaaa", "status": "failed"},
        {"dandiset_id": "000006", "job_id": "job-261106aaaaaa", "status": "successful"},
        {"dandiset_id": "000003", "job_id": "job-261101bbbbbb", "status": "successful"},
    ]
    assert [row["job_id"] for row in latest_rows(rows)] == ["job-261101bbbbbb", "job-261106aaaaaa"]
    written = write_records(rows, tmp_path / "records")
    assert sorted(path.name for path in written) == ["jobs.json", "jobs.tsv", "latest.json", "latest.tsv"]
    jobs = read_table((tmp_path / "records" / "jobs.tsv").read_bytes())
    assert [row["job_id"] for row in jobs] == ["job-261101bbbbbb", "job-261105aaaaaa", "job-261106aaaaaa"]
    assert jobs[0]["error"] == "n/a"
