"""
How capsule branches and main's submodules behave across jobs, on the example Dandiset.

The example is Dandiset 000006 (see tests/README.md). What the repository holds is
test_expected_output.py's.
"""

import dataclasses
import hashlib
import json
import logging
import pathlib
import shutil
import subprocess

import pytest

from dandi_validations import (
    Capsule,
    ManifestFile,
    capsule_branches,
    capsule_status,
    checkout_capsule,
    collect,
    commit_and_publish,
    load_pipeline,
    prune_submodules,
    read_provenance,
    register_image_url,
    remote_heads,
    run_job,
    sync_repository,
)

from .test_expected_output import EXAMPLE_DANDISET_ID, EXAMPLE_MANIFEST_DIRECTORY, NOW, World, git, make_world

REPOSITORY_URL = f"https://github.com/dandi-validations/{EXAMPLE_DANDISET_ID}"


def _claim(runner: pathlib.Path, /, manifest_directory: pathlib.Path | None = EXAMPLE_MANIFEST_DIRECTORY) -> int:
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
        tag=pipeline.tag,
    )


def _clone(*, world: World, name: str) -> pathlib.Path:
    destination = world.runner.parent / name
    git("clone", "--quiet", REPOSITORY_URL, str(destination), cwd=world.runner.parent)
    return destination


def _add_image(*, capsule: Capsule, content: bytes) -> pathlib.Path:
    """Register a stand-in image in a capsule, as ``datalad containers-add`` does, and return its path."""
    name = load_pipeline().container_name
    image = pathlib.Path("envs") / name / "image"
    (capsule.path / image).parent.mkdir(parents=True)
    (capsule.path / image).write_bytes(content)
    git("annex", "add", "--quiet", str(image), cwd=capsule.path)
    git("config", "-f", ".datalad/config", f"datalad.containers.{name}.image", str(image), cwd=capsule.path)
    return image


def _publish(*, directory: pathlib.Path, content: bytes, checksum: bool = True) -> str:
    """Publish a stand-in image as the build workflow's release does, and return its URL."""
    directory.mkdir()
    sif = directory / "image.sif"
    sif.write_bytes(content)
    if checksum:
        (directory / "image.sif.sha256").write_text(hashlib.sha256(content).hexdigest() + "\n")
    return sif.as_uri()


def _record(runner: pathlib.Path, /) -> None:
    commit_and_publish(
        main_root=runner,
        capsule=_capsule(runner),
        paths=[_capsule(runner).path],
        message="Record",
        push_to="origin",
        branch="main",
    )


def _serve_manifest(*, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, ManifestFile]:
    """Stand in for the archive's S3 bucket, which serves the version of the manifest each download was."""
    bucket = tmp_path / "bucket"
    bucket.mkdir()
    downloads = {}
    for name in ("assets.jsonld", "dandiset.yaml"):
        shutil.copyfile(EXAMPLE_MANIFEST_DIRECTORY / name, bucket / name)
        downloads[name] = ManifestFile(content=(bucket / name).read_bytes(), url=(bucket / name).as_uri())
    monkeypatch.setattr("dandi_validations._capsule.fetch_assets_jsonld", lambda **_: downloads["assets.jsonld"])
    monkeypatch.setattr("dandi_validations._capsule.fetch_dandiset_yaml", lambda **_: downloads["dandiset.yaml"])
    return downloads


def _changed_manifest(tmp_path: pathlib.Path, /) -> pathlib.Path:
    changed_manifest = tmp_path / "changed-manifest"
    shutil.copytree(EXAMPLE_MANIFEST_DIRECTORY, changed_manifest)
    with (changed_manifest / "dandiset.yaml").open("a") as stream:
        stream.write("description: changed\n")
    return changed_manifest


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
    # sourcedata/ holds the run's inputs: the manifest and dandiset.yaml, as downloaded. The run
    # mints the dataset it validates beside them. The example's manifest is a frozen copy, which no
    # URL serves, so the git-annex branch records nothing and is not pushed.
    assert (capsule.sourcedata_directory / "dandiset.yaml").read_bytes() == (
        EXAMPLE_MANIFEST_DIRECTORY / "dandiset.yaml"
    ).read_bytes()
    assert capsule.manifest_path.read_bytes() == (EXAMPLE_MANIFEST_DIRECTORY / "assets.jsonld").read_bytes()
    assert capsule.manifest_path.is_symlink()
    assert not capsule.source_dataset_directory.exists()
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

    # git itself can clone the capsules, with their sourcedata/, from main's submodules.
    recursive = tmp_path / "recursive"
    subprocess.run(["git", "clone", "--quiet", "--recurse-submodules", REPOSITORY_URL, str(recursive)], check=True)
    assert (recursive / capsule.name / "code" / "submit.sh").exists()
    assert (recursive / capsule.name / "sourcedata" / "dandiset.yaml").exists()


@pytest.mark.ai_generated
def test_capsules_share_what_git_annex_records(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    world = make_world(tmp_path=tmp_path, monkeypatch=monkeypatch)
    _serve_manifest(tmp_path=tmp_path, monkeypatch=monkeypatch)
    _claim(world.runner, manifest_directory=None)
    first = _capsule(world.runner)
    recorded = git("rev-parse", "git-annex", cwd=world.runner)

    # Another image's capsule, in another worktree of the same clone, annexes the same manifest.
    newer = dataclasses.replace(load_pipeline(), container_version="d0.83.0-s0.14.0-n0.7.2-p4.2.0-h6.2.0-b3.0.2")
    monkeypatch.setattr("dandi_validations._capsule.load_pipeline", lambda: newer)
    assert _claim(world.runner, manifest_directory=None) == 0
    second = Capsule(
        repository_root=world.runner,
        dandiset_id=EXAMPLE_DANDISET_ID,
        pipeline=newer.pipeline,
        tag=newer.tag,
    )

    assert second.path != first.path
    assert git("annex", "lookupkey", "sourcedata/assets.jsonld", cwd=second.path) == git(
        "annex",
        "lookupkey",
        "sourcedata/assets.jsonld",
        cwd=first.path,
    )
    assert git("rev-parse", "git-annex", cwd=world.runner) == recorded
    assert set(remote_heads(root=world.runner, remote="origin")) == {"main", first.tag, second.tag, "git-annex"}


@pytest.mark.ai_generated
def test_a_fresh_clone_gets_the_manifest_from_the_version_downloaded(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = make_world(tmp_path=tmp_path, monkeypatch=monkeypatch)
    downloads = _serve_manifest(tmp_path=tmp_path, monkeypatch=monkeypatch)

    assert _claim(world.runner, manifest_directory=None) == 0
    capsule = _capsule(world.runner)
    sourcedata = read_provenance(capsule.path)["sourcedata"]
    assert sourcedata["manifest_url"] == downloads["assets.jsonld"].url
    assert sourcedata["dandiset_yaml_url"] == downloads["dandiset.yaml"].url

    # git-annex downloads file:// URLs, which stand in for S3 here, only once allowed.
    git("config", "--global", "annex.security.allowed-url-schemes", "http https ftp file", cwd=tmp_path)
    fresh = tmp_path / "fresh"
    git("clone", "--quiet", "--branch", capsule.tag, REPOSITORY_URL, str(fresh), cwd=tmp_path)
    git("annex", "init", "--quiet", "a reader", cwd=fresh)
    git("annex", "get", "--quiet", sourcedata["manifest"], cwd=fresh)
    assert (fresh / sourcedata["manifest"]).read_bytes() == downloads["assets.jsonld"].content


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
    (capsule.derivatives_directory / "validation.jsonl").write_text("")
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
    assert _claim(other, manifest_directory=_changed_manifest(tmp_path)) == 0
    new_head = remote_heads(root=other, remote="origin")[capsule.tag]
    assert new_head != first_head
    assert first_head in git("rev-list", capsule.tag, cwd=world.repository).split()
    assert git("ls-tree", "main", capsule.name, cwd=world.repository).split()[2] == new_head
    assert git("ls-tree", "-r", "--name-only", capsule.tag, "derivatives", cwd=world.repository) == ""


@pytest.mark.ai_generated
def test_a_runner_behind_main_claims_on_top_of_it(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # A rerun checks out the commit its first attempt was dispatched at, from before that attempt
    # added main's submodule.
    world = make_world(tmp_path=tmp_path, monkeypatch=monkeypatch)
    rerun = _clone(world=world, name="rerun")
    _claim(world.runner)
    first_main = git("rev-parse", "main", cwd=world.repository).strip()

    assert _claim(rerun, manifest_directory=_changed_manifest(tmp_path)) == 0
    capsule = _capsule(rerun)
    assert git("rev-parse", "main~1", cwd=world.repository).strip() == first_main
    new_head = remote_heads(root=rerun, remote="origin")[capsule.tag]
    assert git("ls-tree", "main", capsule.name, cwd=world.repository).split()[2] == new_head


@pytest.mark.ai_generated
def test_a_fresh_clone_gets_the_image_from_where_it_is_published(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = make_world(tmp_path=tmp_path, monkeypatch=monkeypatch)
    _claim(world.runner)
    capsule = _capsule(world.runner)
    image = _add_image(capsule=capsule, content=b"the image")
    url = _publish(directory=tmp_path / "release", content=b"the image")

    assert register_image_url(capsule=capsule, container_name=load_pipeline().container_name, url=url)
    _record(world.runner)
    assert "git-annex" in remote_heads(root=world.runner, remote="origin")
    # Nothing about the runner's own clone reaches the branch.
    runner_uuid = git("config", "--get", "annex.uuid", cwd=world.runner).strip()
    assert runner_uuid not in git("log", "--patch", "git-annex", cwd=world.repository)

    # git-annex downloads file:// URLs, which stand in for the release here, only once allowed.
    git("config", "--global", "annex.security.allowed-url-schemes", "http https ftp file", cwd=tmp_path)
    fresh = tmp_path / "fresh"
    git("clone", "--quiet", "--branch", capsule.tag, REPOSITORY_URL, str(fresh), cwd=tmp_path)
    git("annex", "init", "--quiet", "a reader", cwd=fresh)
    git("annex", "get", "--quiet", str(image), cwd=fresh)
    assert (fresh / image).read_bytes() == b"the image"


@pytest.mark.ai_generated
@pytest.mark.parametrize(
    ("published", "checksum"),
    [(b"another image", True), (b"the image", False)],
    ids=["another image", "no checksum"],
)
def test_an_image_not_known_to_be_the_published_one_is_not_pointed_at_it(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    published: bytes,
    checksum: bool,
) -> None:
    world = make_world(tmp_path=tmp_path, monkeypatch=monkeypatch)
    _claim(world.runner)
    capsule = _capsule(world.runner)
    _add_image(capsule=capsule, content=b"the image")
    url = _publish(directory=tmp_path / "release", content=published, checksum=checksum)

    assert not register_image_url(capsule=capsule, container_name=load_pipeline().container_name, url=url)
    _record(world.runner)
    assert "git-annex" not in remote_heads(root=world.runner, remote="origin")


@pytest.mark.ai_generated
def test_runners_merge_what_each_records_in_git_annex(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    world = make_world(tmp_path=tmp_path, monkeypatch=monkeypatch)
    other = _clone(world=world, name="other-runner")
    _claim(world.runner)
    _claim(other)
    # Each runner started its own git-annex branch, so neither push is a fast-forward of the other.
    urls = {}
    for runner, digit in ((world.runner, "1"), (other, "2")):
        key = f"SHA256E-s1--{digit * 64}"
        urls[runner] = f"https://example.org/{digit}"
        git("annex", "registerurl", key, urls[runner], cwd=runner)

    _record(world.runner)
    _record(other)
    for url in urls.values():
        assert git("grep", "--files-with-matches", url, "git-annex", cwd=world.repository)


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
def test_only_branches_named_by_a_capsule_tag_hold_capsules(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = make_world(tmp_path=tmp_path, monkeypatch=monkeypatch)
    _claim(world.runner)
    capsule = _capsule(world.runner)
    version_code = load_pipeline().container_version
    for branch in (version_code, f"{version_code}-vnext", "feature"):
        git("push", "--quiet", "origin", f"main:refs/heads/{branch}", cwd=world.runner)

    assert capsule_branches(main_root=world.runner, remote="origin") == {capsule.tag}


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
        "assets": "6",
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
