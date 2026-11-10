"""
Capsule branches, and the submodules that ``main`` holds for them.

Each capsule is a DataLad dataset on its own branch of the Dandiset repository, named exactly by
its image's tag (the validator version code, e.g. ``d0.82.0-s0.14.0-n0.7.2-p4.2.0-h6.2.0-b3.0.2``).
The branch holds ``code/``, ``logs/`` and ``derivatives/`` at its root, the registration of its
image (``.datalad/environments/``), and the ``datalad containers-run`` record of every run.
Branches are never merged: each is created for its image when that image first validates the
Dandiset, and later runs add commits to it, so earlier pins remain valid.

``main`` holds, besides the single ``dataset_description.json``, one git submodule
``pipeline-dandi+validate_tag-<tag>`` per such branch, pointing at the same repository and
branch, pinned to the branch's latest commit. A branch that is deleted loses its submodule.

All git traffic with the remote goes through ``main``'s clone (whose credentials the runner set
up): a capsule's commits are fetched into it and pushed from it. git-annex branches are never
pushed, since capsules initialize git-annex as private and record nothing in them.
"""

import pathlib
import re
import uuid

from ._capsule import Capsule, capsule_name, remove_capsule_provenance
from ._git import run
from ._version_code import is_version_code

#: Where ``main``'s clone keeps the capsule branches it fetches or pushes.
CAPSULE_REF_NAMESPACE = "refs/capsule-branches"

#: Annex only the image: everything else in a capsule is kept in git, readable on GitHub.
CAPSULE_GITATTRIBUTES = (
    "* annex.backend=SHA256E\n"
    "* annex.largefiles=nothing\n"
    "# The image is annexed: only its key is recorded; its content is published on GHCR.\n"
    ".datalad/environments/** annex.largefiles=anything\n"
)
#: Scratch space of a running job: the DataLad dataset built from the Dandiset's S3 manifest.
CAPSULE_GITIGNORE = "intermediate/\n"


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
    """Put the capsule's branch at the capsule's path, if the remote has it. Returns whether it did."""
    if (capsule.path / ".git").exists():
        return True
    if remote is None or capsule.tag not in remote_heads(main_root, remote):
        return False
    ref = f"{CAPSULE_REF_NAMESPACE}/{capsule.tag}"
    run(["git", "fetch", "--quiet", "--no-tags", remote, f"+refs/heads/{capsule.tag}:{ref}"], cwd=main_root)
    capsule.path.mkdir(parents=True, exist_ok=True)
    run(["git", "init", "--quiet", "-b", capsule.tag, str(capsule.path)], cwd=main_root)
    run(["git", "fetch", "--quiet", "--no-tags", str(main_root), ref], cwd=capsule.path)
    run(["git", "reset", "--quiet", "--hard", "FETCH_HEAD"], cwd=capsule.path)
    return True


def create_capsule(capsule: Capsule) -> None:
    """Start the capsule's branch: a new DataLad dataset, with git-annex initialized as private."""
    path = capsule.path
    path.mkdir(parents=True, exist_ok=True)
    run(["git", "init", "--quiet", "-b", capsule.tag, str(path)], cwd=path)
    run(["git", "config", "annex.private", "true"], cwd=path)
    run(["git", "annex", "init", "--quiet", "ephemeral job runner"], cwd=path)
    files = {**datalad_dataset_files(), ".gitattributes": CAPSULE_GITATTRIBUTES, ".gitignore": CAPSULE_GITIGNORE}
    for relative, content in files.items():
        (path / relative).parent.mkdir(parents=True, exist_ok=True)
        (path / relative).write_text(content)
    run(["git", "add", "--", *files], cwd=path)
    run(["git", "commit", "--quiet", "-m", f"Start the capsule of {capsule.tag}"], cwd=path)


def publish_capsule(main_root: pathlib.Path, capsule: Capsule, remote: str) -> str:
    """Push the capsule's branch, through main's clone. Returns the commit pushed."""
    ref = f"{CAPSULE_REF_NAMESPACE}/{capsule.tag}"
    run(
        ["git", "fetch", "--quiet", "--no-tags", str(capsule.path), f"+refs/heads/{capsule.tag}:{ref}"],
        cwd=main_root,
    )
    run(["git", "push", "--quiet", remote, f"{ref}:refs/heads/{capsule.tag}"], cwd=main_root)
    return run(["git", "rev-parse", ref], cwd=main_root).stdout.strip()


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
