"""
The expected output of a job on Dandiset 000006, whose S3 manifest is frozen in tests/example_dandiset.

``tests/expected_output`` holds, branch by branch, what the Dandiset's repository holds after one
validation. ``main/`` is the repository's main branch. The branch named by the capsule's tag holds
the capsule, with ``code/``, ``logs/``, ``derivatives/`` and ``sourcedata/`` side by side. The jobs
run against local stand-ins for GitHub. See tests/README.md.

To regenerate the expected output after an intended change, run these tests with
``DANDI_VALIDATIONS_REGENERATE_EXPECTED_OUTPUT=1`` and review the diff. Only the test that runs
the container writes ``logs/``, ``derivatives/`` and ``envs/``.
"""

import dataclasses
import datetime
import difflib
import itertools
import json
import logging
import os
import pathlib
import re
import shutil
import subprocess
import sys

import pytest

from dandi_validations import load_pipeline, run_job, sync_repository

TESTS_DIRECTORY = pathlib.Path(__file__).parent
EXAMPLE_DANDISET_ID = "000006"
EXAMPLE_MANIFEST_DIRECTORY = TESTS_DIRECTORY / "example_dandiset" / EXAMPLE_DANDISET_ID
EXPECTED_OUTPUT_DIRECTORY = TESTS_DIRECTORY / "expected_output"
IMAGE_CACHE_VARIABLE = "DANDI_VALIDATIONS_IMAGE_CACHE"
# Where to leave a copy of the repository the run made, as compared, e.g. for CI to upload.
ACTUAL_OUTPUT_VARIABLE = "DANDI_VALIDATIONS_ACTUAL_OUTPUT"

# Everything a job would read from its clock or its environment, fixed.
NOW = datetime.datetime(2026, 11, 5, 20, 31, 5, tzinfo=datetime.UTC)
ENVIRONMENT = {
    "GIT_AUTHOR_NAME": "github-actions[bot]",
    "GIT_AUTHOR_EMAIL": "41898282+github-actions[bot]@users.noreply.github.com",
    "GIT_COMMITTER_NAME": "github-actions[bot]",
    "GIT_COMMITTER_EMAIL": "41898282+github-actions[bot]@users.noreply.github.com",
    "GIT_AUTHOR_DATE": NOW.isoformat(),
    "GIT_COMMITTER_DATE": NOW.isoformat(),
    "GITHUB_SERVER_URL": "https://github.com",
    "GITHUB_REPOSITORY": f"dandi-validations/{EXAMPLE_DANDISET_ID}",
    "GITHUB_RUN_ID": "1",
    "GITHUB_RUN_ATTEMPT": "1",
    "GITHUB_WORKFLOW": "Validate",
    "GITHUB_EVENT_NAME": "workflow_dispatch",
    "GITHUB_SHA": "0" * 40,
    "RUNNER_NAME": "GitHub Actions 1",
    "RUNNER_OS": "Linux",
    "RUNNER_ARCH": "X64",
    "ImageOS": "ubuntu24",
    "ImageVersion": "20261101.1",
    "DANDI_VALIDATIONS_CODEBASE_COMMIT": "0" * 40,
    "DANDI_VALIDATIONS_IMAGE_DIGEST": "sha256:" + "0" * 64,
}

REGENERATE = os.environ.get("DANDI_VALIDATIONS_REGENERATE_EXPECTED_OUTPUT") == "1"

# What the run writes into a claimed capsule, and what it changes there: `datalad containers-add`
# registers the image in the capsule's DataLad configuration.
RUN_OUTPUTS = ("logs/", "derivatives/", "envs/", f"sourcedata/{EXAMPLE_DANDISET_ID}/")
CHANGED_BY_RUN = (".datalad/config",)
# These logs hold progress output and samples taken while the run goes. Their expected copies show
# one run, and only their presence is checked.
SHOWCASE_LOGS = ("logs/duct_stdout", "logs/duct_stderr", "logs/duct_usage.jsonl", "logs/submit.log")
# Measurements and host details in the other logs, which show as "<varies>".
VARYING_LOG_FIELDS = {
    "logs/duct_info.json": (
        "system",
        "execution_summary.wall_clock_time",
        "execution_summary.peak_rss",
        "execution_summary.average_rss",
        "execution_summary.peak_vsz",
        "execution_summary.average_vsz",
        "execution_summary.peak_pmem",
        "execution_summary.average_pmem",
        "execution_summary.peak_pcpu",
        "execution_summary.average_pcpu",
        "execution_summary.num_samples",
        "execution_summary.num_reports",
        "execution_summary.start_time",
        "execution_summary.end_time",
    ),
    "logs/run.json": ("started_at", "finished_at", "host"),
}
VARIES = "<varies>"

_DATASET_ID_RE = re.compile(
    r"((?:^|\s)(?:datalad-)?id = )[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.MULTILINE,
)
# Where the run's files are, in the results and logs, which a version bump would otherwise rename.
_CAPSULE_PATH_RE = re.compile(rf'"[^"]*?/(intermediate/|sourcedata/(?={EXAMPLE_DANDISET_ID}[/"]))')
_ANNEX_OBJECT_RE = re.compile(r"\.git/annex/objects/\S+")
# Only the image's key depends on how it was built. The Dandiset's files and manifest have theirs
# from the frozen example, so sourcedata/ shows them as they are.
IMAGE_DIRECTORY = "envs/"


def git(*arguments: str, cwd: pathlib.Path) -> str:
    """
    Run git.

    Returns
    -------
    str
        Its output.

    """
    return subprocess.run(["git", *arguments], cwd=cwd, capture_output=True, text=True, check=True).stdout


@dataclasses.dataclass(frozen=True)
class World:
    """
    Stand in for github.com locally, next to a runner's clone of the example's repository.

    Attributes
    ----------
    github : pathlib.Path
        The directory that stands in for https://github.com/.
    runner : pathlib.Path
        The runner's clone of the example's repository.

    """

    github: pathlib.Path
    runner: pathlib.Path

    @property
    def repository(self) -> pathlib.Path:
        """Locate the example's repository, https://github.com/dandi-validations/000006."""
        return self.github / "dandi-validations" / EXAMPLE_DANDISET_ID


def make_world(*, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> World:
    """
    Stand in for github.com with local repositories, so jobs run offline and deterministically.

    git rewrites every https://github.com/ URL to a local directory, so the URLs the code records
    are the real ones.

    Returns
    -------
    World
        The stand-ins, with the example's repository synced from the template and cloned.

    """
    github = tmp_path / "github"
    gitconfig = tmp_path / "gitconfig"
    gitconfig.write_text(
        "[init]\n\tdefaultBranch = main\n"
        '[protocol "file"]\n\tallow = always\n'
        f'[url "file://{github}/"]\n\tinsteadOf = https://github.com/\n',
    )
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(gitconfig))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for variable, value in ENVIRONMENT.items():
        monkeypatch.setenv(variable, value)

    world = World(github=github, runner=tmp_path / "runner")
    world.repository.parent.mkdir(parents=True)
    git("init", "--quiet", "--bare", "-b", "main", str(world.repository), cwd=tmp_path)
    sync_work = tmp_path / "sync"
    sync_work.mkdir()
    sync_repository(
        dandiset_id=EXAMPLE_DANDISET_ID,
        remote_url=f"https://github.com/dandi-validations/{EXAMPLE_DANDISET_ID}",
        codebase_ref="main",
        work_directory=sync_work,
    )
    git(
        "clone",
        "--quiet",
        f"https://github.com/dandi-validations/{EXAMPLE_DANDISET_ID}",
        str(world.runner),
        cwd=tmp_path,
    )
    return world


def labels(world: World, /) -> dict[str, str]:
    """
    Name what differs from run to run, as the expected output shows it.

    Returns
    -------
    dict[str, str]
        The placeholder of each commit, and of the directory the world is in.

    """
    names = {str(world.github.parent): "<tmp>"}
    for branch, sha in _heads(world.repository).items():
        if branch != "main":
            names[sha] = f"<head of branch {branch}>"
    return names


def _heads(repository: pathlib.Path, /) -> dict[str, str]:
    output = git("for-each-ref", "--format=%(refname:short) %(objectname)", "refs/heads", cwd=repository)
    return dict(line.split(" ", 1) for line in output.splitlines())


def normalize(*, text: str, names: dict[str, str]) -> str:
    """
    Replace what differs from run to run with the placeholders the expected output shows.

    Returns
    -------
    str
        The text, with placeholders.

    """
    for value, name in sorted(names.items(), key=lambda item: -len(item[0])):
        text = text.replace(value, name)
    text = _DATASET_ID_RE.sub(r"\1<datalad dataset id>", text)
    return _CAPSULE_PATH_RE.sub(r'"<capsule>/\1', text)


def _vary(value: object, /) -> object:
    if isinstance(value, dict):
        return {key: _vary(item) for key, item in value.items()}
    return VARIES


def _normalize_log(*, path: str, text: str) -> str:
    """Show a JSON log indented, with its measurements and host details as ``<varies>``."""
    content = json.loads(text)
    for field in VARYING_LOG_FIELDS.get(path, ()):
        *parents, key = field.split(".")
        parent = content
        for name in parents:
            parent = parent[name]
        parent[key] = _vary(parent[key])
    return json.dumps(content, indent=2) + "\n"


def _sort_results(text: str, /) -> str:
    """Order results by file, then issue ID, since ``dandi validate --jobs`` writes them as its workers finish."""
    lines = [line for line in text.splitlines() if line.strip()]
    keys = {line: (json.loads(line).get("path") or "", json.loads(line).get("id") or "", line) for line in lines}
    return "".join(line + "\n" for line in sorted(lines, key=keys.__getitem__))


def materialize(*, repository: pathlib.Path, ref: str, destination: pathlib.Path, names: dict[str, str]) -> None:
    """
    Write the files of a ref as GitHub shows them.

    Files hold their content, symbolic links show as ``-> target``, and submodules as
    ``Subproject commit <commit>``. JSON logs are indented, and results sorted.
    """
    output = git("ls-tree", "-r", "-z", ref, cwd=repository)
    for entry in filter(None, output.split("\0")):
        info, path = entry.split("\t", 1)
        mode, _, sha = info.split(" ")
        if mode == "160000":
            content = f"Subproject commit {sha}\n"
        elif mode == "120000":
            content = f"-> {git('cat-file', 'blob', sha, cwd=repository)}\n"
        else:
            content = git("cat-file", "blob", sha, cwd=repository)
        content = normalize(text=content, names=names)
        if path.startswith(IMAGE_DIRECTORY):
            content = _ANNEX_OBJECT_RE.sub(".git/annex/objects/<annex key of the image>", content)
        if path.startswith("logs/") and path.endswith(".json"):
            content = _normalize_log(path=path, text=content)
        if path == "derivatives/validation.jsonl":
            content = _sort_results(content)
        target = destination / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)


def _branch_path(relative: pathlib.Path, /) -> str:
    return pathlib.PurePosixPath(*relative.parts[1:]).as_posix()


def _regenerate(*, actual: pathlib.Path, expected: pathlib.Path, claimed: bool) -> None:
    if not claimed:
        if expected.exists():
            shutil.rmtree(expected)
        shutil.copytree(actual, expected)
        return
    for path in actual.rglob("*"):
        relative = path.relative_to(actual)
        if path.is_file() and not _branch_path(relative).startswith(CHANGED_BY_RUN):
            (expected / relative).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, expected / relative)


def _difference(*, actual: pathlib.Path, expected: pathlib.Path, relative: pathlib.Path) -> str | None:
    actual_content = (actual / relative).read_text()
    expected_content = (expected / relative).read_text()
    if actual_content == expected_content:
        return None
    diff = difflib.unified_diff(
        expected_content.splitlines(),
        actual_content.splitlines(),
        fromfile=f"expected/{relative}",
        tofile=f"actual/{relative}",
        lineterm="",
    )
    return "\n".join(itertools.islice(diff, 60))


def assert_matches_expected(*, actual: pathlib.Path, expected: pathlib.Path, claimed: bool = False) -> None:
    """
    Compare a materialized repository with the expected output, file by file.

    With ``claimed``, the capsule has been claimed but not run. It is compared with what the
    expected output holds besides the run's outputs, and the files the run changes are only
    checked to be there. Regenerating then writes only what the claimed capsule holds.

    Raises
    ------
    AssertionError
        If the files differ, showing the start of each difference so that it can be read from a
        CI runner's log.

    """
    if REGENERATE:
        _regenerate(actual=actual, expected=expected, claimed=claimed)

    actual_files = {path.relative_to(actual) for path in actual.rglob("*") if path.is_file()}
    expected_files = {path.relative_to(expected) for path in expected.rglob("*") if path.is_file()}
    if claimed:
        expected_files = {path for path in expected_files if not _branch_path(path).startswith(RUN_OUTPUTS)}
    assert actual_files == expected_files, (
        f"File trees differ.\nMissing: {sorted(map(str, expected_files - actual_files))}\n"
        f"Unexpected: {sorted(map(str, actual_files - expected_files))}"
    )
    unchecked = SHOWCASE_LOGS + (CHANGED_BY_RUN if claimed else ())
    checked = sorted(path for path in expected_files if not _branch_path(path).startswith(unchecked))
    differences = [
        difference
        for relative in checked
        if (difference := _difference(actual=actual, expected=expected, relative=relative)) is not None
    ]
    if differences:
        message = f"{len(differences)} files differ from {expected}:\n" + "\n".join(differences)
        raise AssertionError(message)


def _materialize_branches(*, world: World, names: dict[str, str], destination: pathlib.Path) -> None:
    for branch in ("main", load_pipeline().tag):
        materialize(repository=world.repository, ref=branch, destination=destination / branch, names=names)


@pytest.mark.ai_generated
def test_claimed_capsule_matches_expected_output(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    world = make_world(tmp_path=tmp_path, monkeypatch=monkeypatch)
    exit_code = run_job(
        path=world.runner,
        dandiset_id=EXAMPLE_DANDISET_ID,
        push_to="origin",
        manifest_directory=EXAMPLE_MANIFEST_DIRECTORY,
        execute=False,
        now=NOW,
    )
    assert exit_code == 0

    actual = tmp_path / "actual"
    _materialize_branches(world=world, names=labels(world), destination=actual)
    assert_matches_expected(actual=actual, expected=EXPECTED_OUTPUT_DIRECTORY, claimed=True)


@pytest.mark.ai_generated
@pytest.mark.container
def test_validated_capsule_matches_expected_output(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    pipeline = load_pipeline()
    image_cache = os.environ.get(IMAGE_CACHE_VARIABLE, "")
    image = pathlib.Path(image_cache) / f"{pipeline.container_name}_{pipeline.container_version}.sif"
    if not image_cache or not image.is_file() or shutil.which("apptainer") is None:
        pytest.skip(f"Needs Apptainer and the image in ${IMAGE_CACHE_VARIABLE}, as scripts/e2e_local.sh provides.")

    world = make_world(tmp_path=tmp_path, monkeypatch=monkeypatch)

    def validate() -> int:
        return run_job(
            path=world.runner,
            dandiset_id=EXAMPLE_DANDISET_ID,
            push_to="origin",
            time_limit_minutes=60,
            manifest_directory=EXAMPLE_MANIFEST_DIRECTORY,
            now=NOW,
        )

    assert validate() == 0

    names = {**labels(world), image_cache: "<image cache>", sys.prefix: "<python>"}
    actual = tmp_path / "actual"
    _materialize_branches(world=world, names=names, destination=actual)
    if os.environ.get(ACTUAL_OUTPUT_VARIABLE):
        shutil.copytree(actual, os.environ[ACTUAL_OUTPUT_VARIABLE], dirs_exist_ok=True)
    assert_matches_expected(actual=actual, expected=EXPECTED_OUTPUT_DIRECTORY)
    assert "[DATALAD RUNCMD]" in git("log", "--format=%s", pipeline.tag, cwd=world.repository)
    # The run registered each file it minted in the clone's one git-annex, whose branch records
    # where each file downloads from for every clone.
    recorded = git("log", "--patch", "git-annex", cwd=world.repository)
    validated = git("show", f"{pipeline.tag}:code/assets.tsv", cwd=world.repository).splitlines()[1:]
    assert [row.split("\t")[3] for row in validated if row.split("\t")[3] not in recorded] == []

    # A second run finds the successful capsule and does nothing.
    with caplog.at_level(logging.INFO):
        assert validate() == 0
    assert "already validated these inputs" in caplog.text
