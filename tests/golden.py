"""
The golden example: Dandiset 000006, with its S3 manifest frozen in tests/example_dandiset/000006.

Tests run jobs on it against local stand-ins for GitHub, then compare what the repository holds,
file by file, with tests/expected_output. See tests/README.md.

To regenerate the expected output after an intended change, run the tests with
``DANDI_VALIDATIONS_REGENERATE_GOLDEN=1`` and review the diff.
"""

import dataclasses
import datetime
import difflib
import itertools
import json
import os
import pathlib
import re
import shutil
import subprocess

TESTS_DIRECTORY = pathlib.Path(__file__).parent
EXAMPLE_DANDISET_ID = "000006"
EXAMPLE_MANIFEST_DIRECTORY = TESTS_DIRECTORY / "example_dandiset" / EXAMPLE_DANDISET_ID
EXPECTED_OUTPUT_DIRECTORY = TESTS_DIRECTORY / "expected_output"

#: Everything a job would read from its clock or its environment, fixed.
NOW = datetime.datetime(2026, 11, 5, 20, 31, 5, tzinfo=datetime.timezone.utc)
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
    "DANDI_VALIDATIONS_CODEBASE_COMMIT": "0" * 40,
    "DANDI_VALIDATIONS_IMAGE_DIGEST": "sha256:" + "0" * 64,
}

REGENERATE = os.environ.get("DANDI_VALIDATIONS_REGENERATE_GOLDEN") == "1"
_DATASET_ID_RE = re.compile(
    r"((?:^|\s)(?:datalad-)?id = )[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.M
)
_INTERMEDIATE_PATH_RE = re.compile(r'"[^"]*?/intermediate/')


def git(*arguments: str, cwd: pathlib.Path) -> str:
    return subprocess.run(["git", *arguments], cwd=cwd, capture_output=True, text=True, check=True).stdout


@dataclasses.dataclass(frozen=True)
class World:
    """Local stand-ins for github.com, and a runner's clone of the example's repository."""

    github: pathlib.Path
    runner: pathlib.Path

    @property
    def repository(self) -> pathlib.Path:
        """The example's dandi-validations repository (https://github.com/dandi-validations/000006)."""
        return self.github / "dandi-validations" / EXAMPLE_DANDISET_ID

    @property
    def mirror(self) -> pathlib.Path:
        """DANDI's DataLad mirror of the Dandiset (https://github.com/dandisets/000006)."""
        return self.github / "dandisets" / EXAMPLE_DANDISET_ID


def make_world(tmp_path: pathlib.Path, monkeypatch) -> World:
    """
    Stand in for github.com with local repositories, so jobs run offline and deterministically.

    git rewrites every https://github.com/ URL to a local directory, so the URLs the code records
    are the real ones. The mirror's draft is one commit of the example's dandiset.yaml.
    """
    from dandi_validations._repositories import sync_repository

    github = tmp_path / "github"
    gitconfig = tmp_path / "gitconfig"
    gitconfig.write_text(
        "[init]\n\tdefaultBranch = main\n"
        '[protocol "file"]\n\tallow = always\n'
        f'[url "file://{github}/"]\n\tinsteadOf = https://github.com/\n'
    )
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(gitconfig))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for variable, value in ENVIRONMENT.items():
        monkeypatch.setenv(variable, value)

    world = World(github=github, runner=tmp_path / "runner")
    mirror_work = tmp_path / "mirror-work"
    git("init", "--quiet", "-b", "draft", str(mirror_work), cwd=tmp_path)
    shutil.copyfile(EXAMPLE_MANIFEST_DIRECTORY / "dandiset.yaml", mirror_work / "dandiset.yaml")
    git("add", "dandiset.yaml", cwd=mirror_work)
    git("commit", "--quiet", "-m", "Draft of Dandiset 000006", cwd=mirror_work)
    world.mirror.parent.mkdir(parents=True)
    git("clone", "--quiet", "--bare", str(mirror_work), str(world.mirror), cwd=tmp_path)

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


def labels(world: World) -> dict[str, str]:
    """Readable names for the commits that differ from run to run, as the expected output shows them."""
    names = {}
    for branch, sha in _heads(world.repository).items():
        if branch != "main":
            names[sha] = f"<head of branch {branch}>"
    for branch, sha in _heads(world.mirror).items():
        names[sha] = f"<head of dandisets/{EXAMPLE_DANDISET_ID} {branch}>"
    return names


def _heads(repository: pathlib.Path) -> dict[str, str]:
    output = git("for-each-ref", "--format=%(refname:short) %(objectname)", "refs/heads", cwd=repository)
    return dict(line.split(" ", 1) for line in output.splitlines())


def normalize(text: str, names: dict[str, str]) -> str:
    """Replace what differs from run to run with the placeholders the expected output shows."""
    for sha, name in names.items():
        text = text.replace(sha, name)
    text = _DATASET_ID_RE.sub(r"\1<datalad dataset id>", text)
    return _INTERMEDIATE_PATH_RE.sub('"<capsule>/intermediate/', text)


def materialize(repository: pathlib.Path, ref: str, destination: pathlib.Path, names: dict[str, str]) -> None:
    """
    Write the files of ``ref`` as GitHub shows them: files with their content, symbolic links as
    ``-> target``, and submodules as ``Subproject commit <commit>``.
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
        target = destination / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(normalize(content, names))


def assert_matches_expected(actual: pathlib.Path, expected: pathlib.Path) -> None:
    """Compare two directories file by file (regenerating ``expected`` from ``actual`` if asked)."""
    if REGENERATE:
        if expected.exists():
            shutil.rmtree(expected)
        shutil.copytree(actual, expected)

    actual_files = {path.relative_to(actual) for path in actual.rglob("*") if path.is_file()}
    expected_files = {path.relative_to(expected) for path in expected.rglob("*") if path.is_file()}
    assert actual_files == expected_files, (
        f"File trees differ.\nMissing: {sorted(map(str, expected_files - actual_files))}\n"
        f"Unexpected: {sorted(map(str, actual_files - expected_files))}"
    )
    for relative in sorted(expected_files):
        actual_content = (actual / relative).read_text()
        expected_content = (expected / relative).read_text()
        if actual_content != expected_content:
            # Show where, so that a mismatch on a CI runner can be read from its log.
            diff = difflib.unified_diff(
                expected_content.splitlines(),
                actual_content.splitlines(),
                fromfile=f"expected/{relative}",
                tofile=f"actual/{relative}",
                lineterm="",
            )
            shown = list(itertools.islice(diff, 60))
            raise AssertionError(f"Content of {relative} differs from {expected / relative}:\n" + "\n".join(shown))


def check_validated(repository: pathlib.Path, tag: str, work: pathlib.Path) -> None:
    """Compare a capsule's results (after a real run) with expected_output/validated."""
    names = {}
    actual = work / "validated"
    for name in ("summary.json", "validation.jsonl"):
        content = git("show", f"{tag}:derivatives/{name}", cwd=repository)
        target = actual / tag / "derivatives" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(normalize(content, names))
    assert_matches_expected(actual, EXPECTED_OUTPUT_DIRECTORY / "validated")
    summary = json.loads((actual / tag / "derivatives" / "summary.json").read_text())
    print(json.dumps({key: summary[key] for key in ("total_results", "by_severity", "by_validator")}, indent=2))
