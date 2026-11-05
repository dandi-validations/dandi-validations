"""Collect the ``jobs.tsv`` tables of every Dandiset repository into this repository's ``records/``."""

import concurrent.futures
import pathlib

import requests

from ._capsule import pipeline_relative_directory
from ._globals import DEFAULT_BRANCH, ORGANIZATION
from ._jobs_table import COLUMNS, read_table, sidecar_bytes, table_bytes
from ._pipeline import load_pipeline

RAW_URL_TEMPLATE = "https://raw.githubusercontent.com/{organization}/{repository}/{branch}/{path}"


def fetch_jobs_table(
    dandiset_id: str,
    *,
    organization: str = ORGANIZATION,
    branch: str = DEFAULT_BRANCH,
    session: requests.Session | None = None,
) -> list[dict]:
    session = session or requests.Session()
    pipeline = load_pipeline().pipeline
    path = pipeline_relative_directory(dandiset_id, pipeline) / "jobs.tsv"
    url = RAW_URL_TEMPLATE.format(organization=organization, repository=dandiset_id, branch=branch, path=path)
    response = session.get(url, timeout=60)
    if response.status_code == 404:
        return []
    response.raise_for_status()
    return read_table(response.content)


def latest_rows(rows: list[dict]) -> list[dict]:
    """The most recently formed capsule of each Dandiset (job IDs sort by date)."""
    latest = {}
    for row in rows:
        dandiset_id = row.get("dandiset_id", "")
        if dandiset_id not in latest or row["job_id"] > latest[dandiset_id]["job_id"]:
            latest[dandiset_id] = row
    return [latest[dandiset_id] for dandiset_id in sorted(latest)]


def write_records(rows: list[dict], output_directory: pathlib.Path) -> list[pathlib.Path]:
    output_directory.mkdir(parents=True, exist_ok=True)
    rows = sorted(rows, key=lambda row: (row.get("dandiset_id", ""), row["job_id"]))
    paths = {
        "jobs.tsv": table_bytes(rows),
        "jobs.json": sidecar_bytes(),
        "latest.tsv": table_bytes(latest_rows(rows)),
        "latest.json": sidecar_bytes(),
    }
    written = []
    for name, content in paths.items():
        path = output_directory / name
        path.write_bytes(content)
        written.append(path)
    return written


def collect(
    *, dandiset_ids: list[str], output_directory: pathlib.Path, organization: str = ORGANIZATION, workers: int = 16
) -> list[pathlib.Path]:
    session = requests.Session()
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        tables = executor.map(
            lambda dandiset_id: fetch_jobs_table(dandiset_id, organization=organization, session=session),
            dandiset_ids,
        )
        rows = [{column: row.get(column, "n/a") for column in COLUMNS} for table in tables for row in table]
    return write_records(rows, output_directory)
