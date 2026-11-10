"""
How capsule branches and main's submodules behave across jobs, on the golden example.

The golden example is Dandiset 000006 (see tests/README.md). What a claimed repository holds is
test_golden_example.py's.
"""

import json
import logging
import pathlib
import shutil
import subprocess

import pytest

from dandi_validations import (
    Capsule,
    capsule_branches,
    capsule_status,
    checkout_capsule,
    collect,
    commit_and_publish,
    load_pipeline,
    prune_submodules,
    read_provenance,
    remote_heads,
    run_job,
    sync_repository,
)

from .golden import EXAMPLE_DANDISET_ID, EXAMPLE_MANIFEST_DIRECTORY, NOW, World, git, make_world

REPOSITORY_URL = f"https://github.com/dandi-validations/{EXAMPLE_DANDISET_ID}"


def _claim(runner: pathlib.Path, /, manifest_directory: pathlib.Path = EXAMPLE_MANIFEST_DIRECTORY) -> int:
    return run_job(
        path=runner,
        dandiset_id=EXAMPLE_DANDISET_ID,
        push_to="origin",
        manifest_directory=manifest_directory,
        execute=False,
        now=NOW,
    )


def _capsule(runner: pathlib.Path, /) -> Capsule:
    pipeline = load_pipeline()
    return Capsule(
        repository_root=runner,
        dandiset_id=EXAMPLE_DANDISET_ID,
        pipeline=pipeline.pipeline,
        tag=pipeline.container_version,
    )


def _clone(*, world: World, name: str) -> pathlib.Path:
    destination = world.runner.parent / name
    git("clone", "--quiet", REPOSITORY_URL, str(destination), cwd=world.runner.parent)
    return destination


@pytest.mark.ai_generated
def test_capsule_is_a_worktree_of_the_runners_clone(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    world = make_world(tmp_path=tmp_path, monkeypatch=monkeypatch)
    assert _claim(world.runner) == 0
    capsule = _capsule(world.runner)

    # One object store and one git-annex for main and the capsule. git-annex turns a worktree's
    # .git file into a symbolic link, so that annexed links resolve from it.
    dot_git = capsule.path / ".git"
    assert dot_git.is_file() or dot_git.is_symlink()
    assert str(capsule.path) in git("worktree", "list", cwd=world.runner)
    assert git("config", "--get", "annex.private", cwd=capsule.path).strip() == "true"
    # sourcedata/ is linked, never fetched, and no git-annex branch is pushed.
    assert list((capsule.path / "sourcedata").iterdir()) == []
    assert set(remote_heads(root=world.runner, remote="origin")) == {"main", capsule.tag}


@pytest.mark.ai_generated
def test_another_runner_gets_the_capsule_back(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    world = make_world(tmp_path=tmp_path, monkeypatch=monkeypatch)
    _claim(world.runner)
    capsule = _capsule(world.runner)

    other = _clone(world=world, name="other-runner")
    other_capsule = _capsule(other)
    assert checkout_capsule(main_root=other, capsule=other_capsule, remote="origin")
    assert capsule_status(other_capsule.path) == "stalled"
    assert read_provenance(other_capsule.path) == read_provenance(capsule.path)

    # git itself can clone the capsules and their sourcedata/ from main's submodules.
    recursive = tmp_path / "recursive"
    subprocess.run(["git", "clone", "--quiet", "--recurse-submodules", REPOSITORY_URL, str(recursive)], check=True)
    assert (recursive / capsule.name / "code" / "submit.sh").exists()
    assert (recursive / capsule.name / "sourcedata" / "dandiset.yaml").exists()


@pytest.mark.ai_generated
def test_reruns_skip_unchanged_inputs_and_add_to_the_branch(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    world = make_world(tmp_path=tmp_path, monkeypatch=monkeypatch)
    _claim(world.runner)
    capsule = _capsule(world.runner)
    first_head = remote_heads(root=world.runner, remote="origin")[capsule.tag]

    # Record a successful run, as a job does after the container exits.
    capsule.derivatives_directory.mkdir()
    (capsule.derivatives_directory / "summary.json").write_text("{}")
    commit_and_publish(
        main_root=world.runner,
        capsule=capsule,
        paths=[capsule.path],
        message="Record",
        push_to="origin",
        branch="main",
    )

    # Another runner skips the same inputs.
    other = _clone(world=world, name="other-runner")
    with caplog.at_level(logging.INFO):
        assert _claim(other) == 0
    assert "already validated these inputs" in caplog.text

    # Changed content is a new run on the same branch, which keeps the earlier commits, and main
    # is pinned to its new head.
    changed_manifest = tmp_path / "changed-manifest"
    shutil.copytree(EXAMPLE_MANIFEST_DIRECTORY, changed_manifest)
    with (changed_manifest / "dandiset.yaml").open("a") as stream:
        stream.write("description: changed\n")
    assert _claim(other, manifest_directory=changed_manifest) == 0
    new_head = remote_heads(root=other, remote="origin")[capsule.tag]
    assert new_head != first_head
    assert first_head in git("rev-list", capsule.tag, cwd=world.repository).split()
    assert git("ls-tree", "main", capsule.name, cwd=world.repository).split()[2] == new_head
    assert git("ls-tree", "-r", "--name-only", capsule.tag, "derivatives", cwd=world.repository) == ""


@pytest.mark.ai_generated
def test_deleted_branches_lose_their_submodule(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    world = make_world(tmp_path=tmp_path, monkeypatch=monkeypatch)
    _claim(world.runner)
    capsule = _capsule(world.runner)
    git("push", "--quiet", "origin", f":refs/heads/{capsule.tag}", cwd=world.runner)

    removed = prune_submodules(
        main_root=world.runner,
        branches=capsule_branches(main_root=world.runner, remote="origin"),
        pipeline=capsule.pipeline,
    )
    assert removed == [capsule.name]
    assert not (world.runner / ".gitmodules").exists()
    staged = git("diff", "--cached", "--name-only", cwd=world.runner).split()
    assert {".gitmodules", capsule.name} <= set(staged)


@pytest.mark.ai_generated
def test_collect_reads_capsules_through_mains_submodules(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = make_world(tmp_path=tmp_path, monkeypatch=monkeypatch)
    _claim(world.runner)
    capsule = _capsule(world.runner)

    written = collect(dandiset_ids=[EXAMPLE_DANDISET_ID, "999999"], output_directory=tmp_path / "records")
    rows = (tmp_path / "records" / "capsules.tsv").read_text().splitlines()
    # 999999 has no repository, so it is skipped with a warning.
    assert len(rows) == 2
    row = dict(zip(rows[0].split("\t"), rows[1].split("\t"), strict=True))
    assert {key: row[key] for key in ("capsule", "status", "assets")} == {
        "capsule": capsule.name,
        "status": "stalled",
        "assets": "53",
    }
    assert json.loads((tmp_path / "records" / "capsules.json").read_text())["status"]["Levels"]
    assert [path.name for path in written] == ["capsules.tsv", "capsules.json"]


@pytest.mark.ai_generated
def test_template_sync_keeps_mains_submodules(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    world = make_world(tmp_path=tmp_path, monkeypatch=monkeypatch)
    _claim(world.runner)
    capsule = _capsule(world.runner)
    before = git("ls-tree", "main", capsule.name, cwd=world.repository)

    work = tmp_path / "resync"
    work.mkdir()
    assert sync_repository(
        dandiset_id=EXAMPLE_DANDISET_ID,
        remote_url=REPOSITORY_URL,
        codebase_ref="v2",
        work_directory=work,
    )
    assert "setup@v2" in git("show", "main:.github/workflows/validate.yml", cwd=world.repository)
    assert git("ls-tree", "main", capsule.name, cwd=world.repository) == before
    assert f"branch = {capsule.tag}" in git("show", "main:.gitmodules", cwd=world.repository)
