"""
Capsule branches, worked on as worktrees of main's clone, and the submodules main holds for them.

Each capsule is a DataLad dataset on its own branch of the Dandiset repository, named exactly by
its image's tag (the validator version code, e.g. ``d0.82.0-s0.14.0-n0.7.2-p4.2.0-h6.2.0-b3.0.2``).
The branch holds ``code/``, ``logs/`` and ``derivatives/`` at its root, the registration of its
image (``envs/dandi-validation/image``), ``sourcedata/`` (a subdataset linking the DANDI DataLad
mirror of the Dandiset), and the ``datalad containers-run`` record of every run. Branches are
never merged: each is created for its image when that image first validates the Dandiset, and
later runs add commits to it, so earlier pins remain valid.

On a runner, every capsule branch is a ``git worktree`` of the one clone of the repository,
checked out at the path of its submodule in main. They all share that clone's objects and its
git-annex, which is initialized as private: nothing about the ephemeral clone is recorded, and no
git-annex branch is ever pushed.

``main`` holds, besides the single ``dataset_description.json``, one git submodule
``pipeline-dandi+validate_tag-<tag>`` per capsule branch, pointing at the same repository and
branch, pinned to the branch's latest commit. A branch that is deleted loses its submodule.
"""

import pathlib
import re
import uuid

from ._capsule import Capsule, capsule_name, remove_capsule_provenance
from ._git import run
from ._globals import DANDISETS_URL_TEMPLATE, SOURCEDATA_BRANCH
from ._version_code import is_version_code

#: Annex only the image: everything else in a capsule is kept in git, readable on GitHub.
CAPSULE_GITATTRIBUTES = (
    "* annex.backend=SHA256E\n"
    "* annex.largefiles=nothing\n"
    "# The image is annexed: only its key is recorded; its content is published on GHCR.\n"
    "envs/** annex.largefiles=anything\n"
)
#: Scratch space of a running job: the DataLad dataset built from the Dandiset's S3 manifest.
CAPSULE_GITIGNORE = "intermediate/\n"
SOURCEDATA = "sourcedata"


def datalad_dataset_files() -> dict[str, str]:
    """What ``datalad create`` would write, for a repository that is not yet a DataLad dataset."""
    return {
        ".datalad/config": f'[datalad "dataset"]\n\tid = {uuid.uuid4()}\n',
        ".datalad/.gitattributes": (
            "config annex.largefiles=nothing\n"
            "metadata/aggregate* annex.largefiles=nothing\n"
            "metadata/objects/** annex.largefiles=(anything)\n"
        ),
    }


def remote_url(root: pathlib.Path, remote: str) -> str | None:
    """The remote's URL, without any credentials in it."""
    result = run(["git", "remote", "get-url", remote], cwd=root, check=False)
    if result.returncode != 0:
        return None
    return re.sub(r"(://)[^/@]+@", r"\1", result.stdout.strip())


def remote_heads(root: pathlib.Path, remote: str) -> dict[str, str]:
    result = run(["git", "ls-remote", "--heads", remote], cwd=root, check=False)
    if result.returncode != 0:
        return {}
    heads = {}
    for line in result.stdout.splitlines():
        sha, _, ref = line.partition("\t")
        heads[ref.removeprefix("refs/heads/")] = sha
    return heads


def checkout_capsule(main_root: pathlib.Path, capsule: Capsule, remote: str | None) -> bool:
    """Check the capsule's branch out at its path as a worktree, if it exists. Returns whether it did."""
    if (capsule.path / ".git").exists():
        return True
    has_branch = run(
        ["git", "rev-parse", "--verify", "--quiet", f"refs/heads/{capsule.tag}"], cwd=main_root, check=False
    )
    if has_branch.returncode != 0:
        if remote is None or capsule.tag not in remote_heads(main_root, remote):
            return False
        run(
            ["git", "fetch", "--quiet", "--no-tags", remote, f"+refs/heads/{capsule.tag}:refs/heads/{capsule.tag}"],
            cwd=main_root,
        )
    run(["git", "worktree", "add", "--quiet", str(capsule.path), capsule.tag], cwd=main_root)
    return True


def create_capsule(main_root: pathlib.Path, capsule: Capsule) -> None:
    """Start the capsule's branch as a new DataLad dataset, in a worktree of main's clone."""
    run(["git", "worktree", "add", "--quiet", "--orphan", "-b", capsule.tag, str(capsule.path)], cwd=main_root)
    files = {**datalad_dataset_files(), ".gitattributes": CAPSULE_GITATTRIBUTES, ".gitignore": CAPSULE_GITIGNORE}
    for relative, content in files.items():
        (capsule.path / relative).parent.mkdir(parents=True, exist_ok=True)
        (capsule.path / relative).write_text(content)
    run(["git", "add", "--", *files], cwd=capsule.path)
    run(["git", "commit", "--quiet", "-m", f"Start the capsule of {capsule.tag}"], cwd=capsule.path)


def link_sourcedata(capsule: Capsule, *, url_template: str | None = None) -> dict | None:
    """
    Link DANDI's DataLad mirror of the Dandiset as the capsule's ``sourcedata/`` subdataset.

    The submodule is pinned to the mirror's ``draft`` head at this moment, read with
    ``git ls-remote``: nothing is fetched. It records what the archive's mirror held when the run
    was formed, for provenance and self-containment; the validation does not read it. Returns the
    link, or ``None`` when the mirror has no such branch (e.g. a Dandiset not mirrored yet).
    """
    url = (url_template or DANDISETS_URL_TEMPLATE).format(dandiset_id=capsule.dandiset_id)
    result = run(["git", "ls-remote", url, f"refs/heads/{SOURCEDATA_BRANCH}"], cwd=capsule.path, check=False)
    commit = result.stdout.split("\t", 1)[0].strip() if result.returncode == 0 else ""
    if not commit:
        return None
    for key, value in (("path", SOURCEDATA), ("url", url), ("branch", SOURCEDATA_BRANCH)):
        run(["git", "config", "-f", ".gitmodules", f"submodule.{SOURCEDATA}.{key}", value], cwd=capsule.path)
    (capsule.path / SOURCEDATA).mkdir(exist_ok=True)  # As git leaves an uninitialized submodule
    run(["git", "update-index", "--add", "--cacheinfo", f"160000,{commit},{SOURCEDATA}"], cwd=capsule.path)
    run(["git", "add", ".gitmodules"], cwd=capsule.path)
    return {"path": SOURCEDATA, "url": url, "branch": SOURCEDATA_BRANCH, "commit": commit}


def commit_capsule(capsule: Capsule, paths: list[pathlib.Path], message: str) -> bool:
    """Commit the given paths (and anything staged) on the capsule's branch; returns whether it did."""
    existing = [str(path) for path in paths if path.exists()]
    run(["git", "add", "--all", "--", *existing], cwd=capsule.path)
    if run(["git", "diff", "--cached", "--quiet"], cwd=capsule.path, check=False).returncode == 0:
        return False
    run(["git", "commit", "--quiet", "-m", message], cwd=capsule.path)
    return True


def publish_capsule(main_root: pathlib.Path, capsule: Capsule, remote: str) -> str:
    """Push the capsule's branch. Returns the commit pushed."""
    run(["git", "push", "--quiet", remote, f"refs/heads/{capsule.tag}:refs/heads/{capsule.tag}"], cwd=main_root)
    return run(["git", "rev-parse", f"refs/heads/{capsule.tag}"], cwd=main_root).stdout.strip()


def _registered_submodules(main_root: pathlib.Path) -> dict[str, str]:
    """Submodule name → branch, from main's .gitmodules."""
    result = run(
        ["git", "config", "-f", ".gitmodules", "--get-regexp", r"^submodule\..*\.branch$"], cwd=main_root, check=False
    )
    submodules = {}
    for line in result.stdout.splitlines():
        key, _, branch = line.partition(" ")
        submodules[key.removeprefix("submodule.").removesuffix(".branch")] = branch
    return submodules


def register_submodule(main_root: pathlib.Path, capsule: Capsule, *, url: str, commit: str) -> None:
    """Point main's submodule for the capsule at its branch, pinned to ``commit``."""
    name = capsule.name
    for key, value in (("path", name), ("url", url), ("branch", capsule.tag)):
        run(["git", "config", "-f", ".gitmodules", f"submodule.{name}.{key}", value], cwd=main_root)
    dataset_id = run(
        ["git", "config", "-f", ".datalad/config", "--get", "datalad.dataset.id"], cwd=capsule.path, check=False
    ).stdout.strip()
    if dataset_id:
        run(["git", "config", "-f", ".gitmodules", f"submodule.{name}.datalad-id", dataset_id], cwd=main_root)
    run(["git", "update-index", "--add", "--cacheinfo", f"160000,{commit},{name}"], cwd=main_root)
    run(["git", "add", ".gitmodules"], cwd=main_root)


def prune_submodules(main_root: pathlib.Path, *, branches: set[str], pipeline: str) -> list[str]:
    """Remove main's submodules whose branch no longer exists. Returns the removed submodules."""
    removed = []
    for name, branch in _registered_submodules(main_root).items():
        if branch in branches or not is_version_code(branch) or name != capsule_name(pipeline, branch):
            continue
        run(["git", "config", "-f", ".gitmodules", "--remove-section", f"submodule.{name}"], cwd=main_root)
        run(["git", "update-index", "--force-remove", name], cwd=main_root)
        remove_capsule_provenance(main_root, name)
        removed.append(name)
    if removed:
        gitmodules = main_root / ".gitmodules"
        if gitmodules.exists() and not gitmodules.read_text().strip():
            run(["git", "rm", "--quiet", "--cached", "--force", ".gitmodules"], cwd=main_root)
            gitmodules.unlink()
        else:
            run(["git", "add", ".gitmodules"], cwd=main_root)
        run(["git", "add", "dataset_description.json"], cwd=main_root)
    return removed


def capsule_branches(main_root: pathlib.Path, remote: str) -> set[str]:
    """The remote's branches that hold capsules: those named by an image tag."""
    return {branch for branch in remote_heads(main_root, remote) if is_version_code(branch)}


def commit_main(main_root: pathlib.Path, paths: list[str], message: str) -> bool:
    """Commit main's staged submodule changes and the given files; returns whether a commit was made."""
    existing = [path for path in paths if (main_root / path).exists()]
    if existing:
        run(["git", "add", "--", *existing], cwd=main_root)
    if run(["git", "diff", "--cached", "--quiet"], cwd=main_root, check=False).returncode == 0:
        return False
    run(["git", "commit", "--quiet", "-m", message], cwd=main_root)
    return True
