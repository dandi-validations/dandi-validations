"""
Summarize the JSON Lines results of `dandi validate` into derivatives/summary.json.

Runs inside the dandi-validate container (called by run.sh) and uses only the standard library,
so that it reads the records exactly as dandi-cli wrote them.
"""

import argparse
import collections
import json
import pathlib

SEVERITIES = ("CRITICAL", "ERROR", "WARNING", "HINT", "INFO")
STREAMED_ID = "DANDI.FILE_CONTENT_STREAMED"
MISSING_ID = "DANDI.FILE_CONTENT_MISSING"


def _relative(path: str | None, dandiset_path: pathlib.Path) -> str | None:
    if path is None:
        return None
    try:
        return pathlib.Path(path).resolve().relative_to(dandiset_path).as_posix()
    except ValueError:
        return path


def summarize(results_file: pathlib.Path, dandiset_path: pathlib.Path, exit_code: int) -> dict:
    dandiset_path = dandiset_path.resolve()
    by_severity = collections.Counter()
    by_validator = collections.Counter()
    by_standard = collections.Counter()
    by_id = collections.Counter()
    by_id_severity = {}
    files_with_issues = set()
    streamed = 0
    missing = 0
    total = 0
    with results_file.open() as stream:
        for line in stream:
            if not line.strip():
                continue
            record = json.loads(line)
            total += 1
            severity = record.get("severity") or "NONE"
            origin = record.get("origin") or {}
            issue_id = record.get("id") or "UNKNOWN"
            by_severity[severity] += 1
            by_validator[origin.get("validator") or "N/A"] += 1
            by_standard[origin.get("standard") or "N/A"] += 1
            by_id[issue_id] += 1
            by_id_severity[issue_id] = severity
            if issue_id == STREAMED_ID:
                streamed += 1
            elif issue_id == MISSING_ID:
                missing += 1
            if severity in ("CRITICAL", "ERROR", "WARNING"):
                relative_path = _relative(record.get("path"), dandiset_path)
                if relative_path is not None:
                    files_with_issues.add(relative_path)

    return {
        "dandi_validate_exit_code": exit_code,
        "total_results": total,
        "by_severity": {severity: by_severity.get(severity, 0) for severity in SEVERITIES},
        "by_validator": dict(by_validator.most_common()),
        "by_standard": dict(by_standard.most_common()),
        "by_id": {
            issue_id: {"count": count, "severity": by_id_severity[issue_id]} for issue_id, count in by_id.most_common()
        },
        "files_streamed": streamed,
        "files_missing_content": missing,
        "files_with_issues": len(files_with_issues),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=pathlib.Path, required=True)
    parser.add_argument("--dandiset-path", type=pathlib.Path, required=True)
    parser.add_argument("--exit-code", type=int, required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    arguments = parser.parse_args()

    summary = summarize(arguments.results, arguments.dandiset_path, arguments.exit_code)
    arguments.output.write_text(json.dumps(summary, indent=2) + "\n")


if __name__ == "__main__":
    main()
