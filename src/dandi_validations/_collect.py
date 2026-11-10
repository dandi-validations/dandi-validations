"""
Collect the capsules of every Dandiset repository into this repository's ``records/``.

Each repository's main is cloned shallowly, then each capsule branch its submodules point at.
Every capsule becomes one row of ``records/capsules.tsv``.
"""

import concurrent.futures
import logging
import pathlib
import subprocess
import tempfile

from ._capsule import CAPSULE_NAME_RE, list_capsules
from ._capsule_table import capsule_row, capsule_table_bytes, capsule_table_sidecar_bytes
from ._globals import ORGANIZATION

_log = logging.getLogger(__name__)

DEFAULT_CLONE_URL_TEMPLATE = "https://github.com/{organization}/{repository}"


def _clone(*, url: str, destination: pathlib.Path, branch: str | None = None) -> bool:
    command = ["git", "clone", "--quiet", "--depth", "1"]
    if branch is not None:
        command += ["--branch", branch, "--single-branch"]
    result = subprocess.run([*command, url, str(destination)], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        _log.warning("Could not clone %s%s: %s", url, f" ({branch})" if branch else "", result.stderr.strip())
    return result.returncode == 0


def _submodule_branches(main: pathlib.Path, /) -> dict[str, str]:
    result = subprocess.run(
        ["git", "config", "-f", ".gitmodules", "--get-regexp", r"^submodule\..*\.(path|branch)$"],
        cwd=main,
        capture_output=True,
        text=True,
        check=False,
    )
    settings: dict[str, dict[str, str]] = {}
    for line in result.stdout.splitlines():
        key, _, value = line.partition(" ")
        name, _, setting = key.removeprefix("submodule.").rpartition(".")
        settings.setdefault(name, {})[setting] = value
    return {entry["path"]: entry["branch"] for entry in settings.values() if "path" in entry and "branch" in entry}


def collect_repository(
    *,
    dandiset_id: str,
    work_directory: pathlib.Path,
    organization: str = ORGANIZATION,
    clone_url_template: str = DEFAULT_CLONE_URL_TEMPLATE,
) -> list[dict]:
    """
    Tabulate the capsules of one Dandiset repository.

    Parameters
    ----------
    dandiset_id : str
        The six-digit identifier of the Dandiset.
    work_directory : pathlib.Path
        Where to clone the repository.
    organization : str, default="dandi-validations"
        The organization that holds the repository.
    clone_url_template : str, default="https://github.com/{organization}/{repository}"
        The repository's URL, with ``{organization}`` and ``{repository}`` fields.

    Returns
    -------
    list[dict]
        One row per capsule, or none when the repository cannot be cloned.

    """
    main = work_directory / dandiset_id
    url = clone_url_template.format(organization=organization, repository=dandiset_id)
    if not _clone(url=url, destination=main):
        return []
    for path, branch in _submodule_branches(main).items():
        destination = main / path
        if destination.exists():
            # git leaves an empty directory for an uninitialized submodule.
            destination.rmdir()
        _clone(url=url, destination=destination, branch=branch)
    return [capsule_row(capsule) for capsule in list_capsules(main)]


def _write_table(*, rows: list[dict], output_directory: pathlib.Path) -> list[pathlib.Path]:
    output_directory.mkdir(parents=True, exist_ok=True)
    rows = sorted(rows, key=lambda row: (row.get("dandiset_id", ""), row["capsule"]))
    contents = {"capsules.tsv": capsule_table_bytes(rows), "capsules.json": capsule_table_sidecar_bytes()}
    written = []
    for name, content in contents.items():
        path = output_directory / name
        path.write_bytes(content)
        written.append(path)
    return written


def _capsule_tag(row: dict, /) -> str:
    match = CAPSULE_NAME_RE.fullmatch(row["capsule"])
    return match.group("tag") if match else row.get("version") or "unknown"


def write_records(*, rows: list[dict], output_directory: pathlib.Path, by_tag: bool = False) -> list[pathlib.Path]:
    """
    Write the capsule table and its sidecar.

    Parameters
    ----------
    rows : list[dict]
        The capsule rows.
    output_directory : pathlib.Path
        Where to write ``capsules.tsv`` and ``capsules.json``.
    by_tag : bool, default=False
        Whether to write one table per image tag instead, each in a directory named by the tag.

    Returns
    -------
    list[pathlib.Path]
        The files written.

    """
    if not by_tag:
        return _write_table(rows=rows, output_directory=output_directory)
    groups: dict[str, list[dict]] = {}
    for row in rows:
        groups.setdefault(_capsule_tag(row), []).append(row)
    written = []
    for tag, tag_rows in sorted(groups.items()):
        written += _write_table(rows=tag_rows, output_directory=output_directory / tag)
    return written


def collect(  # noqa: PLR0913
    *,
    dandiset_ids: list[str],
    output_directory: pathlib.Path,
    organization: str = ORGANIZATION,
    clone_url_template: str = DEFAULT_CLONE_URL_TEMPLATE,
    workers: int = 8,
    by_tag: bool = False,
) -> list[pathlib.Path]:
    """
    Tabulate the capsules of many Dandiset repositories into one capsule table, or one per image tag.

    Parameters
    ----------
    dandiset_ids : list[str]
        The Dandisets whose repositories to read.
    output_directory : pathlib.Path
        Where to write ``capsules.tsv`` and ``capsules.json``.
    organization : str, default="dandi-validations"
        The organization that holds the repositories.
    clone_url_template : str, default="https://github.com/{organization}/{repository}"
        The repositories' URL, with ``{organization}`` and ``{repository}`` fields.
    workers : int, default=8
        How many repositories to read at once.
    by_tag : bool, default=False
        Whether to write one table per image tag, each in a directory of ``output_directory`` named
        by the tag.

    Returns
    -------
    list[pathlib.Path]
        The files written.

    """
    with tempfile.TemporaryDirectory(prefix="dandi-validations-collect-") as temporary_directory:
        work_directory = pathlib.Path(temporary_directory)
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            tables = list(
                executor.map(
                    lambda dandiset_id: collect_repository(
                        dandiset_id=dandiset_id,
                        work_directory=work_directory,
                        organization=organization,
                        clone_url_template=clone_url_template,
                    ),
                    dandiset_ids,
                ),
            )
    rows = [row for table in tables for row in table]
    return write_records(rows=rows, output_directory=output_directory, by_tag=by_tag)
