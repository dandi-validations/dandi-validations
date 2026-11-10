import importlib.util
import json
import pathlib

import pytest

from dandi_validations import CAPSULE_CODE_DIRECTORY

# summarize.py is a script that runs inside the image, so it is loaded from its file.
_spec = importlib.util.spec_from_file_location("summarize", CAPSULE_CODE_DIRECTORY / "summarize.py")
assert _spec is not None
assert _spec.loader is not None
summarize_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(summarize_module)


def _record(
    *,
    issue_id: str,
    severity: str,
    path: str | pathlib.Path,
    validator: str = "nwbinspector",
    standard: str | None = None,
) -> dict:
    return {
        "id": issue_id,
        "severity": severity,
        "path": str(path),
        "origin": {"validator": validator, "standard": standard},
    }


@pytest.mark.ai_generated
def test_summarize(tmp_path: pathlib.Path) -> None:
    dandiset = tmp_path / "000006"
    (dandiset / "sub-01").mkdir(parents=True)
    records = [
        _record(
            issue_id="DANDI.FILE_CONTENT_STREAMED",
            severity="INFO",
            path=dandiset / "sub-01/a.nwb",
            validator="dandi",
            standard="DANDI-LAYOUT",
        ),
        _record(
            issue_id="DANDI.FILE_CONTENT_STREAMED",
            severity="INFO",
            path=dandiset / "sub-01/b.nwb",
            validator="dandi",
            standard="DANDI-LAYOUT",
        ),
        _record(
            issue_id="DANDI.FILE_CONTENT_MISSING",
            severity="ERROR",
            path=dandiset / "sub-01/c.nwb",
            validator="dandi",
            standard="DANDI-LAYOUT",
        ),
        _record(issue_id="NWBI.check_subject_age", severity="ERROR", path=dandiset / "sub-01/a.nwb"),
        _record(issue_id="NWBI.check_subject_age", severity="ERROR", path=dandiset / "sub-01/b.nwb"),
        _record(
            issue_id="pynwb.x",
            severity="WARNING",
            path=dandiset / "sub-01/a.nwb",
            validator="pynwb",
            standard="NWB",
        ),
    ]
    results = tmp_path / "validation.jsonl"
    results.write_text("\n".join(json.dumps(record) for record in records) + "\n\n")

    summary = summarize_module.summarize(results_file=results, dandiset_path=dandiset, exit_code=1)
    assert summary["total_results"] == 6
    assert summary["by_severity"] == {"CRITICAL": 0, "ERROR": 3, "WARNING": 1, "HINT": 0, "INFO": 2}
    assert summary["by_validator"] == {"dandi": 3, "nwbinspector": 2, "pynwb": 1}
    assert summary["by_id"]["NWBI.check_subject_age"] == {"count": 2, "severity": "ERROR"}
    assert summary["files_streamed"] == 2
    assert summary["files_missing_content"] == 1
    assert summary["files_with_issues"] == 3
    assert summary["dandi_validate_exit_code"] == 1


@pytest.mark.ai_generated
def test_sorted_results_do_not_depend_on_the_order_of_the_results(tmp_path: pathlib.Path) -> None:
    # `dandi validate --jobs N` writes each file's results as its worker finishes.
    records = [
        _record(
            issue_id="pynwb.x",
            severity="WARNING",
            path="/capsule/000006/sub-02/b.nwb",
            validator="pynwb",
            standard="NWB",
        ),
        _record(
            issue_id="DANDI.FILE_CONTENT_STREAMED",
            severity="INFO",
            path="/capsule/000006/sub-02/b.nwb",
            validator="dandi",
            standard="DANDI-LAYOUT",
        ),
        _record(issue_id="NWBI.check_subject_age", severity="ERROR", path="/capsule/000006/sub-01/a.nwb"),
        _record(
            issue_id="DANDI.FILE_CONTENT_STREAMED",
            severity="INFO",
            path="/capsule/000006/sub-01/a.nwb",
            validator="dandi",
            standard="DANDI-LAYOUT",
        ),
    ]
    lines = [json.dumps(record) for record in records]
    outputs = []
    for index, order in enumerate((lines, lines[::-1], lines[2:] + lines[:2])):
        results = tmp_path / f"validation-{index}.jsonl"
        results.write_text("\n".join(order) + "\n\n")
        output = tmp_path / f"sorted-{index}.jsonl"
        summarize_module.sort_results(results_file=results, output=output)
        outputs.append(output.read_text())
        summary = summarize_module.summarize(results_file=results, dandiset_path=tmp_path, exit_code=1)
        first_summary = summarize_module.summarize(
            results_file=tmp_path / "validation-0.jsonl",
            dandiset_path=tmp_path,
            exit_code=1,
        )
        assert summary == first_summary
    assert outputs[1] == outputs[0]
    assert outputs[2] == outputs[0]
    # By file, then by issue ID; each line as dandi-cli wrote it.
    assert outputs[0] == "".join(line + "\n" for line in (lines[3], lines[2], lines[1], lines[0]))
