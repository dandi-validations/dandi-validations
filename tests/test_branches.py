"""
How capsule branches and main's submodules behave across jobs, on the golden example (Dandiset
000006; see tests/README.md). What a claimed repository holds is test_golden_example.py's.
"""

import json
import shutil
import subprocess

from dandi_validations._branches import capsule_branches, checkout_capsule, prune_submodules, remote_heads
from dandi_validations._capsule import Capsule, capsule_status, read_provenance
from dandi_validations._collect import collect
from dandi_validations._pipeline import load_pipeline
from dandi_validations._repositories import sync_repository
from dandi_validations._run import _commit_and_publish, run_job

from .golden import EXAMPLE_DANDISET_ID, EXAMPLE_MANIFEST_DIRECTORY, NOW, git, make_world


def _claim(runner, manifest_directory=EXAMPLE_MANIFEST_DIRECTORY, **kwargs):
    return run_job(
        path=runner,
        dandiset_id=EXAMPLE_DANDISET_ID,
        push_to="origin",
        manifest_directory=manifest_directory,
        execute=False,
        now=NOW,
        **kwargs,
    )


def _capsule(runner) -> Capsule:
    pipeline = load_pipeline()
    return Capsule(
        repository_root=runner,
        dandiset_id=EXAMPLE_DANDISET_ID,
        pipeline=pipeline.pipeline,
        tag=pipeline.container_version,
    )


def _clone(world, name):
    destination = world.runner.parent / name
    git(
        "clone",
        "--quiet",
        f"https://github.com/dandi-validations/{EXAMPLE_DANDISET_ID}",
        str(destination),
        cwd=world.runner.parent,
    )
    return destination


def test_capsule_is_a_worktree_of_the_runners_clone(tmp_path, monkeypatch):
    world = make_world(tmp_path, monkeypatch)
    assert _claim(world.runner) == 0
    capsule = _capsule(world.runner)

    # One object store and one git-annex for main and the capsule. (git-annex turns a worktree's
    # .git file into a symbolic link, so that annexed links resolve from it.)
    dot_git = capsule.path / ".git"
    assert dot_git.is_file() or dot_git.is_symlink()
    assert str(capsule.path) in git("worktree", "list", cwd=world.runner)
    assert git("config", "--get", "annex.private", cwd=capsule.path).strip() == "true"
    # sourcedata/ is linked, never fetched; no git-annex branch is pushed.
    assert not any((capsule.path / "sourcedata").iterdir())
    assert set(remote_heads(world.runner, "origin")) == {"main", capsule.tag}


def test_another_runner_gets_the_capsule_back(tmp_path, monkeypatch):
    world = make_world(tmp_path, monkeypatch)
    _claim(world.runner)
    capsule = _capsule(world.runner)

    other = _clone(world, "other-runner")
    other_capsule = _capsule(other)
    assert checkout_capsule(other, other_capsule, "origin")
    assert capsule_status(other_capsule.path) == "stalled"
    assert read_provenance(other_capsule.path) == read_provenance(capsule.path)

    # git itself can clone the capsules and their sourcedata/ from main's submodules.
    recursive = tmp_path / "recursive"
    subprocess.run(
        [
            "git",
            "clone",
            "--quiet",
            "--recurse-submodules",
            f"https://github.com/dandi-validations/{EXAMPLE_DANDISET_ID}",
            str(recursive),
        ],
        check=True,
    )
    assert (recursive / capsule.name / "code" / "submit.sh").exists()
    assert (recursive / capsule.name / "sourcedata" / "dandiset.yaml").exists()


def test_reruns_skip_unchanged_inputs_and_add_to_the_branch(tmp_path, monkeypatch, capsys):
    world = make_world(tmp_path, monkeypatch)
    _claim(world.runner)
    capsule = _capsule(world.runner)
    first_head = remote_heads(world.runner, "origin")[capsule.tag]

    # Record a successful run, as a job does after the container exits.
    capsule.derivatives_directory.mkdir()
    (capsule.derivatives_directory / "summary.json").write_text("{}")
    _commit_and_publish(world.runner, capsule, paths=[capsule.path], message="Record", push_to="origin", branch="main")

    # Another runner skips the same inputs.
    other = _clone(world, "other-runner")
    assert _claim(other) == 0
    assert "already validated these inputs" in capsys.readouterr().out

    # Changed content is a new run on the same branch, which keeps the earlier commits, and main
    # is pinned to its new head.
    changed_manifest = tmp_path / "changed-manifest"
    shutil.copytree(EXAMPLE_MANIFEST_DIRECTORY, changed_manifest)
    with (changed_manifest / "dandiset.yaml").open("a") as stream:
        stream.write("description: changed\n")
    assert _claim(other, manifest_directory=changed_manifest) == 0
    new_head = remote_heads(other, "origin")[capsule.tag]
    assert new_head != first_head
    assert first_head in git("rev-list", capsule.tag, cwd=world.repository).split()
    assert git("ls-tree", "main", capsule.name, cwd=world.repository).split()[2] == new_head
    assert git("ls-tree", "-r", "--name-only", capsule.tag, "derivatives", cwd=world.repository) == ""


def test_deleted_branches_lose_their_submodule(tmp_path, monkeypatch):
    world = make_world(tmp_path, monkeypatch)
    _claim(world.runner)
    capsule = _capsule(world.runner)
    git("push", "--quiet", "origin", f":refs/heads/{capsule.tag}", cwd=world.runner)

    removed = prune_submodules(
        world.runner, branches=capsule_branches(world.runner, "origin"), pipeline=capsule.pipeline
    )
    assert removed == [capsule.name]
    assert not (world.runner / ".gitmodules").exists()
    staged = git("diff", "--cached", "--name-only", cwd=world.runner).split()
    assert {".gitmodules", capsule.name} <= set(staged)


def test_collect_reads_capsules_through_mains_submodules(tmp_path, monkeypatch):
    world = make_world(tmp_path, monkeypatch)
    _claim(world.runner)
    capsule = _capsule(world.runner)

    written = collect(dandiset_ids=[EXAMPLE_DANDISET_ID, "999999"], output_directory=tmp_path / "records")
    rows = (tmp_path / "records" / "capsules.tsv").read_text().splitlines()
    assert len(rows) == 2  # 999999 has no repository: skipped with a warning
    row = dict(zip(rows[0].split("\t"), rows[1].split("\t")))
    assert row["capsule"] == capsule.name and row["status"] == "stalled" and row["assets"] == "53"
    assert json.loads((tmp_path / "records" / "capsules.json").read_text())["status"]["Levels"]
    assert [path.name for path in written] == ["capsules.tsv", "capsules.json"]


def test_template_sync_keeps_mains_submodules(tmp_path, monkeypatch):
    world = make_world(tmp_path, monkeypatch)
    _claim(world.runner)
    capsule = _capsule(world.runner)
    before = git("ls-tree", "main", capsule.name, cwd=world.repository)

    work = tmp_path / "resync"
    work.mkdir()
    assert sync_repository(
        dandiset_id=EXAMPLE_DANDISET_ID,
        remote_url=f"https://github.com/dandi-validations/{EXAMPLE_DANDISET_ID}",
        codebase_ref="v2",
        work_directory=work,
    )
    assert "setup@v2" in git("show", "main:.github/workflows/validate.yml", cwd=world.repository)
    assert git("ls-tree", "main", capsule.name, cwd=world.repository) == before
    assert f"branch = {capsule.tag}" in git("show", "main:.gitmodules", cwd=world.repository)
