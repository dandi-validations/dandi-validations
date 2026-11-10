"""
The ``jobs.tsv`` table of a pipeline directory: one row per job capsule, rebuilt from the capsules.

The table holds nothing a capsule does not, so it is always regenerated rather than edited. Its
``jobs.json`` sidecar describes the columns, BIDS-style. The collect step concatenates these
tables across every Dandiset repository of the organization.
"""

import gzip
import json
import pathlib

from ._capsule import SUBMITTED_MARKER_PREFIX, capsule_status, read_provenance

COLUMNS = {
    "job_id": "The `job-{YYMMDD}{hash}` directory name of the capsule.",
    "status": "Lifecycle status: pending, stalled, failed, successful or unknown.",
    "dandiset_id": "The six-digit Dandiset that was validated.",
    "dandiset_version": "The Dandiset version that was validated (e.g. draft).",
    "pipeline": "The pipeline that formed the capsule.",
    "version": "The dandi-validation image's tag: its validator version code.",
    "image_digest": "Digest of that dandi-validation image on GHCR.",
    "codebase": "The version of dandi-validations that formed the capsule.",
    "params": "Short MD5 of the parameter set file.",
    "params_key": "Name of the registered parameter set.",
    "source_digest": "SHA-256 over the registered assets table and dandiset.yaml.",
    "assets": "Number of assets registered in the DataLad dataset that was validated.",
    "skipped_assets": "Number of assets not registered (Zarr, or no SHA-256 digest yet).",
    "created_at": "When the capsule was formed (UTC).",
    "claimed_by": "The GitHub Actions run attempt (or host) that ran the capsule.",
    "run_exit_code": "Exit code of the job (0 for a completed validation, whatever it found).",
    "wall_clock_seconds": "Wall-clock time of `datalad containers-run`, measured by duct.",
    "peak_rss_bytes": "Peak resident memory of the run's process tree, measured by duct.",
    "dandi_validate_exit_code": "Exit code of `dandi validate` itself (1 when it reported errors).",
    "total_results": "Number of validation results recorded.",
    "critical": "Results of severity CRITICAL.",
    "error": "Results of severity ERROR.",
    "warning": "Results of severity WARNING.",
    "hint": "Results of severity HINT.",
    "info": "Results of severity INFO.",
    "files_streamed": "Files whose content was streamed (DANDI.FILE_CONTENT_STREAMED).",
    "files_missing_content": "Files that could not be streamed (DANDI.FILE_CONTENT_MISSING).",
    "files_with_issues": "Files with at least one CRITICAL, ERROR or WARNING result.",
}


def _read_json(file_path: pathlib.Path) -> dict:
    if not file_path.exists():
        return {}
    try:
        return json.loads(file_path.read_text())
    except json.JSONDecodeError:
        return {}


def _claimed_by(capsule_directory: pathlib.Path) -> str:
    markers = sorted((capsule_directory / "code").glob(f"{SUBMITTED_MARKER_PREFIX}*"))
    return markers[0].read_text().strip() if markers else ""


def capsule_row(capsule_directory: pathlib.Path) -> dict:
    provenance = read_provenance(capsule_directory)
    duct_summary = _read_json(capsule_directory / "logs" / "duct_info.json").get("execution_summary", {})
    run = _read_json(capsule_directory / "logs" / "run.json")
    summary = _read_json(capsule_directory / "derivatives" / "summary.json")
    by_severity = summary.get("by_severity", {})

    row = {column: "" for column in COLUMNS}
    row.update({key: provenance[key] for key in COLUMNS if key in provenance})
    row["job_id"] = capsule_directory.name
    row["status"] = capsule_status(capsule_directory)
    row["claimed_by"] = _claimed_by(capsule_directory)
    row["run_exit_code"] = run.get("exit_code", "")
    if duct_summary.get("wall_clock_time") is not None:
        row["wall_clock_seconds"] = round(float(duct_summary["wall_clock_time"]), 1)
    row["peak_rss_bytes"] = duct_summary.get("peak_rss", "")
    for key in (
        "dandi_validate_exit_code",
        "total_results",
        "files_streamed",
        "files_missing_content",
        "files_with_issues",
    ):
        row[key] = summary.get(key, "")
    for severity in ("critical", "error", "warning", "hint", "info"):
        row[severity] = by_severity.get(severity.upper(), "")
    return row


def _format(value: object) -> str:
    text = "n/a" if value in ("", None) else str(value)
    return text.replace("\t", " ").replace("\n", " ")


def table_bytes(rows: list[dict]) -> bytes:
    lines = ["\t".join(COLUMNS)]
    lines.extend("\t".join(_format(row.get(column)) for column in COLUMNS) for row in rows)
    return ("\n".join(lines) + "\n").encode("utf-8")


def sidecar_bytes() -> bytes:
    sidecar = {column: {"Description": description} for column, description in COLUMNS.items()}
    sidecar["status"]["Levels"] = {
        "pending": "A code directory exists, but the capsule has not been submitted.",
        "stalled": "A submitted marker exists, but nothing has been logged since.",
        "failed": "Logs exist without results: the run failed, or is still in progress.",
        "successful": "A derivatives directory exists: the validation completed.",
        "unknown": "Nothing that would place the capsule in the lifecycle was observed.",
    }
    return (json.dumps(sidecar, indent=2) + "\n").encode("utf-8")


def write_jobs_table(pipeline_directory: pathlib.Path) -> list[pathlib.Path]:
    capsule_directories = sorted(
        path for path in pipeline_directory.iterdir() if path.is_dir() and path.name.startswith("job-")
    )
    rows = [capsule_row(path) for path in capsule_directories]
    table_path = pipeline_directory / "jobs.tsv"
    sidecar_path = pipeline_directory / "jobs.json"
    table_path.write_bytes(table_bytes(rows))
    sidecar_path.write_bytes(sidecar_bytes())
    return [table_path, sidecar_path]


def read_table(content: bytes) -> list[dict]:
    if content[:2] == b"\x1f\x8b":
        content = gzip.decompress(content)
    lines = content.decode("utf-8").splitlines()
    if not lines:
        return []
    header = lines[0].split("\t")
    return [dict(zip(header, line.split("\t"))) for line in lines[1:] if line]
