"""
Work on capsule branches as worktrees of main's clone, and keep main's submodules for them.

Each capsule is a DataLad dataset on its own branch of the Dandiset repository, named exactly by
its image's tag (the validator version code, e.g. ``d0.82.0-s0.14.0-n0.7.2-p4.2.0-h6.2.0-b3.0.2``).
The branch holds ``code/``, ``logs/`` and ``derivatives/`` at its root. It also holds the
registration of its image (``envs/dandi-validation/image``), ``sourcedata/`` (a subdataset
linking DANDI's DataLad mirror of the Dandiset), and the ``datalad containers-run`` record of
every run. Branches are never merged. Each is created when its image first validates the
Dandiset, and later runs add commits to it, so earlier pins remain valid.

On a runner, every capsule branch is a ``git worktree`` of the one clone of the repository,
checked out at the path of its submodule in main. They all share that clone's objects and its
git-annex. The annex is initialized as private, so nothing about the ephemeral clone is recorded
and no git-annex branch is ever pushed.

``main`` holds one git submodule ``pipeline-dandi+validate_tag-<tag>`` per capsule branch. Each
points at the same repository and branch, pinned to the branch's latest commit. A branch that is
deleted loses its submodule.
"""

import pathlib
import re
import uuid

from ._capsule import Capsule, capsule_name
from ._git import run
from ._globals import DANDISETS_URL_TEMPLATE, SOURCEDATA_BRANCH
from ._version_code import is_version_code

# Only the image is annexed, so everything else in a capsule can be read on GitHub.
CAPSULE_GITATTRIBUTES = (
    "* annex.backend=SHA256E\n"
    "* annex.largefiles=nothing\n"
    "# The image is annexed: only its key is recorded; its content is published on GHCR.\n"
    "envs/** annex.largefiles=anything\n"
    "# The summary only counts what validation.jsonl holds, so GitHub collapses it in diffs.\n"
    "derivatives/validation_summary.txt linguist-generated=true\n"
)
CAPSULE_GITIGNORE = "intermediate/\n"
SOURCEDATA = "sourcedata"


def datalad_dataset_files() -> dict[str, str]:
    """
    Write what ``datalad create`` would, for a repository that is not yet a DataLad dataset.

    Returns
    -------
    dict[str, str]
        The content of each file, by path, with a new dataset ID.

    """
    return {
        ".datalad/config": f'[datalad "dataset"]\n\tid = {uuid.uuid4()}\n',
        ".datalad/.gitattributes": (
            "config annex.largefiles=nothing\n"
            "metadata/aggregate* annex.largefiles=nothing\n"
            "metadata/objects/** annex.largefiles=(anything)\n"
        ),
    }


def remote_url(*, root: pathlib.Path, remote: str) -> str | None:
    """
    Read a remote's URL as configured, before any ``insteadOf`` rewriting, without credentials.

    Parameters
    ----------
    root : pathlib.Path
        The root of the clone.
    remote : str
        The remote's name.

    Returns
    -------
    str or None
        The URL, or ``None`` when there is no such remote.

    """
    result = run(command=["git", "config", "--get", f"remote.{remote}.url"], cwd=root, check=False)
    if result.returncode != 0:
        return None
    return re.sub(r"(://)[^/@]+@", r"\1", result.stdout.strip())


def remote_heads(*, root: pathlib.Path, remote: str) -> dict[str, str]:
    """
    List a remote's branches.

    Parameters
    ----------
    root : pathlib.Path
        The root of the clone.
    remote : str
        The remote's name or URL.

    Returns
    -------
    dict[str, str]
        The commit of each branch, by name, or nothing when the remote cannot be read.

    """
    result = run(command=["git", "ls-remote", "--heads", remote], cwd=root, check=False)
    if result.returncode != 0:
        return {}
    heads = {}
    for line in result.stdout.splitlines():
        sha, _, ref = line.partition("\t")
        heads[ref.removeprefix("refs/heads/")] = sha
    return heads


def checkout_capsule(*, main_root: pathlib.Path, capsule: Capsule, remote: str | None) -> bool:
    """
    Check a capsule's branch out at its path as a worktree, if the branch exists.

    Parameters
    ----------
    main_root : pathlib.Path
        The root of the clone of main.
    capsule : Capsule
        The capsule.
    remote : str or None
        The remote to fetch the branch from, when it is not local.

    Returns
    -------
    bool
        Whether the capsule is checked out.

    """
    if (capsule.path / ".git").exists():
        return True
    has_branch = run(
        command=["git", "rev-parse", "--verify", "--quiet", f"refs/heads/{capsule.tag}"],
        cwd=main_root,
        check=False,
    )
    if has_branch.returncode != 0:
        if remote is None or capsule.tag not in remote_heads(root=main_root, remote=remote):
            return False
        run(
            command=[
                "git",
                "fetch",
                "--quiet",
                "--no-tags",
                remote,
                f"+refs/heads/{capsule.tag}:refs/heads/{capsule.tag}",
            ],
            cwd=main_root,
        )
    run(command=["git", "worktree", "add", "--quiet", str(capsule.path), capsule.tag], cwd=main_root)
    return True


def create_capsule(*, main_root: pathlib.Path, capsule: Capsule) -> None:
    """
    Start a capsule's branch as a new DataLad dataset, in a worktree of main's clone.

    Parameters
    ----------
    main_root : pathlib.Path
        The root of the clone of main.
    capsule : Capsule
        The capsule.

    """
    run(
        command=["git", "worktree", "add", "--quiet", "--orphan", "-b", capsule.tag, str(capsule.path)],
        cwd=main_root,
    )
    files = {**datalad_dataset_files(), ".gitattributes": CAPSULE_GITATTRIBUTES, ".gitignore": CAPSULE_GITIGNORE}
    for relative, content in files.items():
        (capsule.path / relative).parent.mkdir(parents=True, exist_ok=True)
        (capsule.path / relative).write_text(content)
    run(command=["git", "add", "--", *files], cwd=capsule.path)
    run(command=["git", "commit", "--quiet", "-m", f"Start the capsule of {capsule.tag}"], cwd=capsule.path)


def link_sourcedata(*, capsule: Capsule, url_template: str | None = None) -> dict | None:
    """
    Link DANDI's DataLad mirror of the Dandiset as a capsule's ``sourcedata/`` subdataset.

    The submodule is pinned to the mirror's ``draft`` head at this moment, read with
    ``git ls-remote``, so nothing is fetched. It records what the archive's mirror held when the
    run was formed, for provenance and self-containment. The validation does not read it.

    Parameters
    ----------
    capsule : Capsule
        The capsule.
    url_template : str or None, default=None
        The mirror's URL, with a ``{dandiset_id}`` field, if not DANDI's.

    Returns
    -------
    dict or None
        The link, or ``None`` when the mirror has no such branch (e.g., a Dandiset not mirrored yet).

    """
    url = (url_template or DANDISETS_URL_TEMPLATE).format(dandiset_id=capsule.dandiset_id)
    result = run(command=["git", "ls-remote", url, f"refs/heads/{SOURCEDATA_BRANCH}"], cwd=capsule.path, check=False)
    commit = result.stdout.split("\t", 1)[0].strip() if result.returncode == 0 else ""
    if not commit:
        return None
    for key, value in (("path", SOURCEDATA), ("url", url), ("branch", SOURCEDATA_BRANCH)):
        run(command=["git", "config", "-f", ".gitmodules", f"submodule.{SOURCEDATA}.{key}", value], cwd=capsule.path)
    # As git leaves an uninitialized submodule.
    (capsule.path / SOURCEDATA).mkdir(exist_ok=True)
    run(command=["git", "update-index", "--add", "--cacheinfo", f"160000,{commit},{SOURCEDATA}"], cwd=capsule.path)
    run(command=["git", "add", ".gitmodules"], cwd=capsule.path)
    return {"path": SOURCEDATA, "url": url, "branch": SOURCEDATA_BRANCH, "commit": commit}


def commit_capsule(*, capsule: Capsule, paths: list[pathlib.Path], message: str) -> bool:
    """
    Commit paths, and anything staged, on a capsule's branch.

    Parameters
    ----------
    capsule : Capsule
        The capsule.
    paths : list[pathlib.Path]
        The paths to commit. Those that do not exist are skipped.
    message : str
        The commit message.

    Returns
    -------
    bool
        Whether a commit was made.

    """
    existing = [str(path) for path in paths if path.exists()]
    run(command=["git", "add", "--all", "--", *existing], cwd=capsule.path)
    if run(command=["git", "diff", "--cached", "--quiet"], cwd=capsule.path, check=False).returncode == 0:
        return False
    run(command=["git", "commit", "--quiet", "-m", message], cwd=capsule.path)
    return True


def publish_capsule(*, main_root: pathlib.Path, capsule: Capsule, remote: str) -> str:
    """
    Push a capsule's branch.

    Parameters
    ----------
    main_root : pathlib.Path
        The root of the clone of main.
    capsule : Capsule
        The capsule.
    remote : str
        The remote to push to.

    Returns
    -------
    str
        The commit pushed.

    """
    run(
        command=["git", "push", "--quiet", remote, f"refs/heads/{capsule.tag}:refs/heads/{capsule.tag}"],
        cwd=main_root,
    )
    return run(command=["git", "rev-parse", f"refs/heads/{capsule.tag}"], cwd=main_root).stdout.strip()


def _registered_submodules(main_root: pathlib.Path, /) -> dict[str, str]:
    result = run(
        command=["git", "config", "-f", ".gitmodules", "--get-regexp", r"^submodule\..*\.branch$"],
        cwd=main_root,
        check=False,
    )
    submodules = {}
    for line in result.stdout.splitlines():
        key, _, branch = line.partition(" ")
        submodules[key.removeprefix("submodule.").removesuffix(".branch")] = branch
    return submodules


def register_submodule(*, main_root: pathlib.Path, capsule: Capsule, url: str, commit: str) -> None:
    """
    Point main's submodule for a capsule at its branch, pinned to a commit.

    Parameters
    ----------
    main_root : pathlib.Path
        The root of the clone of main.
    capsule : Capsule
        The capsule.
    url : str
        The URL of the repository that holds the branch.
    commit : str
        The commit to pin.

    """
    name = capsule.name
    for key, value in (("path", name), ("url", url), ("branch", capsule.tag)):
        run(command=["git", "config", "-f", ".gitmodules", f"submodule.{name}.{key}", value], cwd=main_root)
    dataset_id = run(
        command=["git", "config", "-f", ".datalad/config", "--get", "datalad.dataset.id"],
        cwd=capsule.path,
        check=False,
    ).stdout.strip()
    if dataset_id:
        run(command=["git", "config", "-f", ".gitmodules", f"submodule.{name}.datalad-id", dataset_id], cwd=main_root)
    run(command=["git", "update-index", "--add", "--cacheinfo", f"160000,{commit},{name}"], cwd=main_root)
    run(command=["git", "add", ".gitmodules"], cwd=main_root)


def prune_submodules(*, main_root: pathlib.Path, branches: set[str], pipeline: str) -> list[str]:
    """
    Remove main's submodules whose branch no longer exists.

    Parameters
    ----------
    main_root : pathlib.Path
        The root of the clone of main.
    branches : set[str]
        The capsule branches that exist.
    pipeline : str
        The pipeline whose capsules to prune.

    Returns
    -------
    list[str]
        The submodules removed.

    """
    removed = []
    for name, branch in _registered_submodules(main_root).items():
        if branch in branches or not is_version_code(branch) or name != capsule_name(pipeline=pipeline, tag=branch):
            continue
        run(command=["git", "config", "-f", ".gitmodules", "--remove-section", f"submodule.{name}"], cwd=main_root)
        run(command=["git", "update-index", "--force-remove", name], cwd=main_root)
        removed.append(name)
    if removed:
        gitmodules = main_root / ".gitmodules"
        if gitmodules.exists() and not gitmodules.read_text().strip():
            run(command=["git", "rm", "--quiet", "--cached", "--force", ".gitmodules"], cwd=main_root)
            gitmodules.unlink()
        else:
            run(command=["git", "add", ".gitmodules"], cwd=main_root)
    return removed


def capsule_branches(*, main_root: pathlib.Path, remote: str) -> set[str]:
    """
    List the remote's branches that hold capsules, which are those named by an image tag.

    Parameters
    ----------
    main_root : pathlib.Path
        The root of the clone of main.
    remote : str
        The remote's name or URL.

    Returns
    -------
    set[str]
        The capsule branches.

    """
    return {branch for branch in remote_heads(root=main_root, remote=remote) if is_version_code(branch)}


def commit_main(*, main_root: pathlib.Path, paths: list[str], message: str) -> bool:
    """
    Commit main's staged submodule changes and the given files.

    Parameters
    ----------
    main_root : pathlib.Path
        The root of the clone of main.
    paths : list[str]
        Files to commit, relative to the root. Those that do not exist are skipped.
    message : str
        The commit message.

    Returns
    -------
    bool
        Whether a commit was made.

    """
    existing = [path for path in paths if (main_root / path).exists()]
    if existing:
        run(command=["git", "add", "--", *existing], cwd=main_root)
    if run(command=["git", "diff", "--cached", "--quiet"], cwd=main_root, check=False).returncode == 0:
        return False
    run(command=["git", "commit", "--quiet", "-m", message], cwd=main_root)
    return True
