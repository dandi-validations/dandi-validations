"""
Tabulate capsules, one row per capsule, read from the capsule itself.

The collect step builds this table for every Dandiset repository of the organization into this
repository's ``records/capsules.tsv``. A BIDS-style ``capsules.json`` sidecar describes its columns.
"""

import json
import pathlib

from ._capsule import SUBMITTED_MARKER_PREFIX, capsule_status, read_provenance

CAPSULE_TABLE_COLUMNS = {
    "dandiset_id": "The six-digit Dandiset that was validated.",
    "capsule": "The capsule's directory: `pipeline-{pipeline}_tag-{image tag}`.",
    "status": "Lifecycle status of the capsule's latest run: pending, stalled, failed, successful or unknown.",
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
    "created_at": "When the capsule's latest run was formed (UTC).",
    "claimed_by": "The GitHub Actions run attempt (or host) that ran it.",
    "run_exit_code": "Exit code of the run (0 for a completed validation, whatever it found).",
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

_SUMMARY_COLUMNS = (
    "dandi_validate_exit_code",
    "total_results",
    "files_streamed",
    "files_missing_content",
    "files_with_issues",
)
_SEVERITY_COLUMNS = ("critical", "error", "warning", "hint", "info")


def _read_json(file_path: pathlib.Path, /) -> dict:
    if not file_path.exists():
        return {}
    try:
        return json.loads(file_path.read_text())
    except json.JSONDecodeError:
        return {}


def _claimed_by(capsule_directory: pathlib.Path, /) -> str:
    markers = sorted((capsule_directory / "code").glob(f"{SUBMITTED_MARKER_PREFIX}*"))
    return markers[0].read_text().strip() if markers else ""


def capsule_row(capsule_directory: pathlib.Path, /) -> dict:
    """
    Tabulate a capsule from its provenance, logs and results.

    Parameters
    ----------
    capsule_directory : pathlib.Path
        The capsule's directory.

    Returns
    -------
    dict
        The capsule's row, with a value (possibly empty) for every column.

    """
    provenance = read_provenance(capsule_directory)
    duct_summary = _read_json(capsule_directory / "logs" / "duct_info.json").get("execution_summary", {})
    run = _read_json(capsule_directory / "logs" / "run.json")
    summary = _read_json(capsule_directory / "derivatives" / "summary.json")
    by_severity = summary.get("by_severity", {})

    row: dict[str, object] = dict.fromkeys(CAPSULE_TABLE_COLUMNS, "")
    row.update({key: provenance[key] for key in CAPSULE_TABLE_COLUMNS if key in provenance})
    row["capsule"] = capsule_directory.name
    row["status"] = capsule_status(capsule_directory)
    row["claimed_by"] = _claimed_by(capsule_directory)
    row["run_exit_code"] = run.get("exit_code", "")
    if duct_summary.get("wall_clock_time") is not None:
        row["wall_clock_seconds"] = round(float(duct_summary["wall_clock_time"]), 1)
    row["peak_rss_bytes"] = duct_summary.get("peak_rss", "")
    for key in _SUMMARY_COLUMNS:
        row[key] = summary.get(key, "")
    for severity in _SEVERITY_COLUMNS:
        row[severity] = by_severity.get(severity.upper(), "")
    return row


def _format(value: object, /) -> str:
    text = "n/a" if value in ("", None) else str(value)
    return text.replace("\t", " ").replace("\n", " ")


def capsule_table_bytes(rows: list[dict], /) -> bytes:
    """
    Write capsule rows as a BIDS-style TSV.

    Parameters
    ----------
    rows : list[dict]
        The rows.

    Returns
    -------
    bytes
        The TSV, with a header, and ``n/a`` for empty values.

    """
    lines = ["\t".join(CAPSULE_TABLE_COLUMNS)]
    lines.extend("\t".join(_format(row.get(column)) for column in CAPSULE_TABLE_COLUMNS) for row in rows)
    return ("\n".join(lines) + "\n").encode("utf-8")


def capsule_table_sidecar_bytes() -> bytes:
    """
    Describe the columns of the capsule table, as a BIDS-style JSON sidecar.

    Returns
    -------
    bytes
        The sidecar.

    """
    sidecar: dict[str, dict] = {
        column: {"Description": description} for column, description in CAPSULE_TABLE_COLUMNS.items()
    }
    sidecar["status"]["Levels"] = {
        "pending": "A code directory exists, but the capsule has not been submitted.",
        "stalled": "A submitted marker exists, but nothing has been logged since.",
        "failed": "Logs exist without results: the run failed, or is still in progress.",
        "successful": "A derivatives directory exists: the validation completed.",
        "unknown": "Nothing that would place the capsule in the lifecycle was observed.",
    }
    return (json.dumps(sidecar, indent=2) + "\n").encode("utf-8")


def read_capsule_table(content: bytes, /) -> list[dict]:
    """
    Read a capsule table.

    Parameters
    ----------
    content : bytes
        The TSV.

    Returns
    -------
    list[dict]
        Its rows, by column, with values as text.

    """
    lines = content.decode("utf-8").splitlines()
    if not lines:
        return []
    header = lines[0].split("\t")
    return [dict(zip(header, line.split("\t"), strict=True)) for line in lines[1:] if line]
