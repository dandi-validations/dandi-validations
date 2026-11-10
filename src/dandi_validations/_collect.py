"""
Collect the capsules of every Dandiset repository into this repository's ``records/``.

Each repository's main is cloned shallowly, then each capsule branch its submodules point at,
and every capsule becomes one row of ``records/capsules.tsv``.
"""

import concurrent.futures
import logging
import pathlib
import subprocess
import tempfile

from ._capsule import list_capsules
from ._capsule_table import capsule_row, sidecar_bytes, table_bytes
from ._globals import ORGANIZATION

_log = logging.getLogger(__name__)

DEFAULT_CLONE_URL_TEMPLATE = "https://github.com/{organization}/{repository}.git"


def _clone(url: str, destination: pathlib.Path, *, branch: str | None = None) -> bool:
    command = ["git", "clone", "--quiet", "--depth", "1"]
    if branch is not None:
        command += ["--branch", branch, "--single-branch"]
    result = subprocess.run([*command, url, str(destination)], capture_output=True, text=True)
    if result.returncode != 0:
        _log.warning("Could not clone %s%s: %s", url, f" ({branch})" if branch else "", result.stderr.strip())
    return result.returncode == 0


def _submodule_branches(main: pathlib.Path) -> dict[str, str]:
    """Submodule path → branch, from main's .gitmodules."""
    result = subprocess.run(
        ["git", "config", "-f", ".gitmodules", "--get-regexp", r"^submodule\..*\.(path|branch)$"],
        cwd=main,
        capture_output=True,
        text=True,
    )
    settings = {}
    for line in result.stdout.splitlines():
        key, _, value = line.partition(" ")
        name, _, setting = key.removeprefix("submodule.").rpartition(".")
        settings.setdefault(name, {})[setting] = value
    return {entry["path"]: entry["branch"] for entry in settings.values() if "path" in entry and "branch" in entry}


def collect_repository(
    dandiset_id: str,
    *,
    work_directory: pathlib.Path,
    organization: str = ORGANIZATION,
    clone_url_template: str = DEFAULT_CLONE_URL_TEMPLATE,
) -> list[dict]:
    main = work_directory / dandiset_id
    url = clone_url_template.format(organization=organization, repository=dandiset_id)
    if not _clone(url, main):
        return []
    for path, branch in _submodule_branches(main).items():
        destination = main / path
        if destination.exists():
            destination.rmdir()  # The empty directory of an uninitialized submodule
        _clone(url, destination, branch=branch)
    return [capsule_row(capsule) for capsule in list_capsules(main)]


def write_records(rows: list[dict], output_directory: pathlib.Path) -> list[pathlib.Path]:
    output_directory.mkdir(parents=True, exist_ok=True)
    rows = sorted(rows, key=lambda row: (row.get("dandiset_id", ""), row["capsule"]))
    paths = {"capsules.tsv": table_bytes(rows), "capsules.json": sidecar_bytes()}
    written = []
    for name, content in paths.items():
        path = output_directory / name
        path.write_bytes(content)
        written.append(path)
    return written


def collect(
    *,
    dandiset_ids: list[str],
    output_directory: pathlib.Path,
    organization: str = ORGANIZATION,
    clone_url_template: str = DEFAULT_CLONE_URL_TEMPLATE,
    workers: int = 8,
) -> list[pathlib.Path]:
    with tempfile.TemporaryDirectory(prefix="dandi-validations-collect-") as temporary_directory:
        work_directory = pathlib.Path(temporary_directory)
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            tables = list(
                executor.map(
                    lambda dandiset_id: collect_repository(
                        dandiset_id,
                        work_directory=work_directory,
                        organization=organization,
                        clone_url_template=clone_url_template,
                    ),
                    dandiset_ids,
                )
            )
    rows = [row for table in tables for row in table]
    return write_records(rows, output_directory)
