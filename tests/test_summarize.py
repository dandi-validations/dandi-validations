import importlib.util
import json

from dandi_validations._pipeline import CAPSULE_CODE_DIRECTORY

spec = importlib.util.spec_from_file_location("summarize", CAPSULE_CODE_DIRECTORY / "summarize.py")
summarize_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(summarize_module)


def _record(issue_id, severity, path, validator="nwbinspector", standard=None):
    return {
        "id": issue_id,
        "severity": severity,
        "path": str(path),
        "origin": {"validator": validator, "standard": standard},
    }


def test_summarize(tmp_path):
    dandiset = tmp_path / "000006"
    (dandiset / "sub-01").mkdir(parents=True)
    records = [
        _record("DANDI.FILE_CONTENT_STREAMED", "INFO", dandiset / "sub-01/a.nwb", "dandi", "DANDI-LAYOUT"),
        _record("DANDI.FILE_CONTENT_STREAMED", "INFO", dandiset / "sub-01/b.nwb", "dandi", "DANDI-LAYOUT"),
        _record("DANDI.FILE_CONTENT_MISSING", "ERROR", dandiset / "sub-01/c.nwb", "dandi", "DANDI-LAYOUT"),
        _record("NWBI.check_subject_age", "ERROR", dandiset / "sub-01/a.nwb"),
        _record("NWBI.check_subject_age", "ERROR", dandiset / "sub-01/b.nwb"),
        _record("pynwb.x", "WARNING", dandiset / "sub-01/a.nwb", "pynwb", "NWB"),
    ]
    results = tmp_path / "validation.jsonl"
    results.write_text("\n".join(json.dumps(record) for record in records) + "\n\n")

    summary = summarize_module.summarize(results, dandiset, exit_code=1)
    assert summary["total_results"] == 6
    assert summary["by_severity"] == {"CRITICAL": 0, "ERROR": 3, "WARNING": 1, "HINT": 0, "INFO": 2}
    assert summary["by_validator"] == {"dandi": 3, "nwbinspector": 2, "pynwb": 1}
    assert summary["by_id"]["NWBI.check_subject_age"] == {"count": 2, "severity": "ERROR"}
    assert summary["files_streamed"] == 2 and summary["files_missing_content"] == 1
    assert summary["files_with_issues"] == 3
    assert summary["dandi_validate_exit_code"] == 1
