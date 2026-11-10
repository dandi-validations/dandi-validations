import subprocess

from dandi_validations._branches import (
    capsule_branches,
    checkout_capsule,
    create_capsule,
    link_sourcedata,
    prune_submodules,
    remote_heads,
)
from dandi_validations._capsule import (
    PROVENANCE_KEY,
    Capsule,
    capsule_status,
    is_up_to_date,
    plan_capsule,
    read_dataset_description,
    write_capsule,
)
from dandi_validations._collect import collect
from dandi_validations._git import ensure_annex
from dandi_validations._repositories import sync_repository
from dandi_validations._run import _commit_and_publish


def _git(*arguments, cwd):
    return subprocess.run(["git", *arguments], cwd=cwd, capture_output=True, text=True, check=True).stdout


def _main_clone(tmp_path, name):
    """A Dandiset repository made by the template sync, cloned as a runner's checkout of main."""
    remote = tmp_path / "remotes" / "000123.git"
    if not remote.exists():
        remote.parent.mkdir(parents=True)
        _git("init", "--quiet", "--bare", "-b", "main", str(remote), cwd=tmp_path)
        work = tmp_path / "sync"
        work.mkdir()
        sync_repository(dandiset_id="000123", remote_url=f"file://{remote}", codebase_ref="main", work_directory=work)
    clone = tmp_path / name
    _git("clone", "--quiet", f"file://{remote}", str(clone), cwd=tmp_path)
    return remote, clone


def _mirror(tmp_path):
    """A stand-in for DANDI's DataLad mirror of the Dandiset (github.com/dandisets/000123)."""
    mirror = tmp_path / "dandisets" / "000123.git"
    if not mirror.exists():
        work = tmp_path / "mirror-work"
        _git("init", "--quiet", "-b", "draft", str(work), cwd=tmp_path)
        (work / "dandiset.yaml").write_text("identifier: DANDI:000123\n")
        _git("add", "dandiset.yaml", cwd=work)
        _git("commit", "--quiet", "-m", "draft", cwd=work)
        mirror.parent.mkdir(parents=True)
        _git("clone", "--quiet", "--bare", str(work), str(mirror), cwd=tmp_path)
    return mirror


def _submit(main, assets_jsonld, dandiset_yaml):
    """What a job does before running its capsule: check the branch out (or start it), write, publish."""
    plan = plan_capsule(
        repository_root=main, dandiset_id="000123", assets_jsonld=assets_jsonld, dandiset_yaml=dandiset_yaml
    )
    ensure_annex(main, remote=None)
    if not checkout_capsule(main, plan.capsule, "origin"):
        create_capsule(main, plan.capsule)
    mirrors = main.parent / "dandisets"
    sourcedata = link_sourcedata(plan.capsule, url_template=f"file://{mirrors}/{{dandiset_id}}.git")
    write_capsule(plan, sourcedata=sourcedata)
    _commit_and_publish(
        main, plan.capsule, paths=[plan.capsule.path], message="Submit", push_to="origin", branch="main"
    )
    return plan


def test_capsule_lives_on_its_tag_branch_and_main_holds_its_submodule(tmp_path, assets_jsonld, dandiset_yaml):
    remote, main = _main_clone(tmp_path, "runner")
    mirror = _mirror(tmp_path)
    plan = _submit(main, assets_jsonld, dandiset_yaml)
    capsule = plan.capsule

    # The capsule is a worktree of main's clone: one object store and one git-annex for both. (git-annex
    # turns a worktree's .git file into a symbolic link, so that annexed links resolve from it.)
    dot_git = capsule.path / ".git"
    assert dot_git.is_file() or dot_git.is_symlink()
    assert str(capsule.path) in _git("worktree", "list", cwd=main)
    common = _git("rev-parse", "--path-format=absolute", "--git-common-dir", cwd=capsule.path).strip()
    assert common == str(main / ".git")

    # The capsule is on the branch named exactly by the image's tag, with its contents at the root.
    heads = remote_heads(main, "origin")
    assert set(heads) == {"main", capsule.tag}
    files = _git("ls-tree", "-r", "--name-only", capsule.tag, cwd=remote).split()
    assert "code/submit.sh" in files and "code/assets.tsv" in files and ".datalad/config" in files
    assert not any(path.startswith("pipeline-") or path == "dataset_description.json" for path in files)
    assert _git("config", "--get", "annex.private", cwd=capsule.path).strip() == "true"

    # sourcedata/ links the mirror's draft head, as a subdataset that was never fetched.
    mirror_draft = _git("rev-parse", "draft", cwd=mirror).strip()
    assert _git("ls-tree", capsule.tag, "sourcedata", cwd=remote).split()[:3] == ["160000", "commit", mirror_draft]
    capsule_gitmodules = _git("show", f"{capsule.tag}:.gitmodules", cwd=remote)
    assert f"url = file://{mirror}" in capsule_gitmodules and "branch = draft" in capsule_gitmodules
    assert not any((capsule.path / "sourcedata").iterdir())

    # main holds the single dataset_description.json and a submodule pinned to the branch's head.
    main_files = _git("ls-tree", "--name-only", "main", cwd=remote).split()
    assert {"dataset_description.json", ".gitmodules", capsule.name} <= set(main_files)
    gitmodules = _git("show", "main:.gitmodules", cwd=remote)
    assert f'[submodule "{capsule.name}"]' in gitmodules
    assert f"path = {capsule.name}" in gitmodules and f"branch = {capsule.tag}" in gitmodules
    assert f"url = file://{remote}" in gitmodules and "datalad-id = " in gitmodules
    gitlink = _git("ls-tree", "main", capsule.name, cwd=remote).split()
    assert gitlink[:3] == ["160000", "commit", heads[capsule.tag]]
    description = read_dataset_description(main)
    assert list(description[PROVENANCE_KEY]) == [capsule.name]
    assert description[PROVENANCE_KEY][capsule.name]["branch"] == capsule.tag
    assert description[PROVENANCE_KEY][capsule.name]["sourcedata"]["commit"] == mirror_draft

    # Another runner gets the capsule back from its branch; git can clone it as a submodule too.
    _, other = _main_clone(tmp_path, "other-runner")
    other_capsule = Capsule(repository_root=other, dandiset_id="000123", pipeline=capsule.pipeline, tag=capsule.tag)
    assert checkout_capsule(other, other_capsule, "origin")
    assert capsule_status(other_capsule.path) == "stalled"
    recursive = tmp_path / "recursive"
    subprocess.run(
        [
            "git",
            "-c",
            "protocol.file.allow=always",
            "clone",
            "--quiet",
            "--recurse-submodules",
            f"file://{remote}",
            str(recursive),
        ],
        check=True,
    )
    assert (recursive / capsule.name / "code" / "submit.sh").exists()
    assert (recursive / capsule.name / "sourcedata" / "dandiset.yaml").exists()

    # The collect step reads the capsule through main's submodules.
    collect(
        dandiset_ids=["000123"],
        output_directory=tmp_path / "records",
        clone_url_template=f"file://{tmp_path}/remotes/{{repository}}.git",
    )
    rows = (tmp_path / "records" / "capsules.tsv").read_text().splitlines()
    assert len(rows) == 2 and f"\t{capsule.name}\tstalled\t" in rows[1]


def test_rerun_adds_to_the_branch_and_repins_main(tmp_path, assets_jsonld, dandiset_yaml):
    remote, main = _main_clone(tmp_path, "runner")
    _mirror(tmp_path)
    first = _submit(main, assets_jsonld, dandiset_yaml)
    first_head = remote_heads(main, "origin")[first.capsule.tag]

    # Pretend the run succeeded and was recorded, so the same inputs are up to date.
    capsule = first.capsule
    capsule.derivatives_directory.mkdir()
    (capsule.derivatives_directory / "summary.json").write_text("{}")
    _commit_and_publish(main, capsule, paths=[capsule.path], message="Record", push_to="origin", branch="main")
    _, next_runner = _main_clone(tmp_path, "next-runner")
    again = plan_capsule(
        repository_root=next_runner, dandiset_id="000123", assets_jsonld=assets_jsonld, dandiset_yaml=dandiset_yaml
    )
    assert checkout_capsule(next_runner, again.capsule, "origin") and is_up_to_date(again)

    # Changed content forms a new run on the same branch, which keeps the earlier commits.
    changed = _submit(next_runner, assets_jsonld, dandiset_yaml + b"description: changed\n")
    new_head = remote_heads(next_runner, "origin")[changed.capsule.tag]
    assert new_head != first_head
    history = _git("rev-list", changed.capsule.tag, cwd=remote).split()
    assert first_head in history
    assert _git("ls-tree", "main", changed.capsule.name, cwd=remote).split()[2] == new_head
    assert not (changed.capsule.derivatives_directory).exists()


def test_deleted_branches_lose_their_submodule(tmp_path, assets_jsonld, dandiset_yaml):
    remote, main = _main_clone(tmp_path, "runner")
    _mirror(tmp_path)
    plan = _submit(main, assets_jsonld, dandiset_yaml)
    _git("push", "--quiet", "origin", f":refs/heads/{plan.capsule.tag}", cwd=main)

    removed = prune_submodules(main, branches=capsule_branches(main, "origin"), pipeline=plan.capsule.pipeline)
    assert removed == [plan.capsule.name]
    assert not (main / ".gitmodules").exists()
    assert read_dataset_description(main)[PROVENANCE_KEY] == {}
    staged = _git("diff", "--cached", "--name-only", cwd=main).split()
    assert {".gitmodules", plan.capsule.name, "dataset_description.json"} <= set(staged)
