"""
Work on capsule branches as worktrees of main's clone, and keep main's submodules for them.

Each capsule is a DataLad dataset on its own branch of the Dandiset repository, named by its tag.
That is its image's tag (the validator version code), then ``-v`` and the version of
dandi-validations that formed it, e.g. ``d0.82.0-s0.14.0-n0.7.2-p4.2.0-h6.2.0-b3.0.2-v0.2.0``.
The branch holds ``code/``, ``logs/`` and ``derivatives/`` at its root. It also holds the
registration of its image (``envs/dandi-validation/image``), ``sourcedata/`` (the Dandiset as
validated: its S3 manifest and the dataset built from it), and the ``datalad containers-run``
record of every run. Branches are never merged. Each is created when its image and version of
dandi-validations first validate the Dandiset, and later runs add commits to it, so earlier pins
remain valid.

On a runner, every capsule branch is a ``git worktree`` of the one clone of the repository,
checked out at the path of its submodule in main. They all share that clone's objects and its
git-annex. The annex is initialized as private, so nothing about the ephemeral clone is recorded.
The repository's git-annex branch records only where content can be downloaded: each capsule's
image, each version of the Dandiset's manifest, and each of its files. ``datalad get`` and
``datalad rerun`` then work in any clone. The capsules of a Dandiset share most of its files, and
each file's URL is recorded once for all of them.

``main`` holds one git submodule ``pipeline-dandi+validate_tag-<tag>`` per capsule branch. Each
points at the same repository and branch, pinned to the branch's latest commit. A branch that is
deleted loses its submodule.
"""

import logging
import pathlib
import re
import urllib.request
import uuid

from ._capsule import DANDISET_YAML, SOURCEDATA, Capsule, capsule_name
from ._git import run
from ._manifest import AssetRow
from ._version_code import is_capsule_tag

_log = logging.getLogger(__name__)

# Only the image and the Dandiset are annexed, so everything else in a capsule can be read on GitHub.
CAPSULE_GITATTRIBUTES = (
    "* annex.backend=SHA256E\n"
    "* annex.largefiles=nothing\n"
    "# The image is annexed: only its key is recorded; its content is published on GHCR.\n"
    "envs/** annex.largefiles=anything\n"
    "# The Dandiset's manifest and files are annexed: only their keys are recorded; their content is on S3.\n"
    f"{SOURCEDATA}/** annex.largefiles=anything\n"
    f"{SOURCEDATA}/{DANDISET_YAML} annex.largefiles=nothing\n"
    "# The summary only counts what validation.jsonl holds, so GitHub collapses it in diffs.\n"
    "derivatives/validation_summary.txt linguist-generated=true\n"
)
CAPSULE_GITIGNORE = "intermediate/\n"

_SHA256_KEY_RE = re.compile(r"SHA256E?-s\d+--(?P<sha256>[0-9a-f]{64})(\..*)?")


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


def annex_sourcedata(*, capsule: Capsule, assets: list[AssetRow], manifest_url: str | None) -> None:
    """
    Annex a capsule's ``sourcedata/``: the Dandiset's manifest, and a file for each validated asset.

    Each asset is registered as ``datalad addurls`` registers it in the dataset that ``run.sh``
    builds to validate, which ``sourcedata/`` therefore holds too. The file is a link to the
    asset's SHA-256 key (with the extension of its path, as ``et:SHA256`` asks), and its S3 URL is
    recorded in the git-annex branch. Nothing is downloaded. The manifest, which was downloaded,
    is annexed with the URL of the S3 version it was downloaded from, if known.

    The capsule's worktree shares the clone's git-annex, and so the git-annex branch, with main and
    every other capsule. An asset that another capsule already registered is recorded only once.

    Parameters
    ----------
    capsule : Capsule
        The capsule, whose ``sourcedata/`` holds the manifest as written by ``write_capsule``.
    assets : list[AssetRow]
        The assets to register, which are those validated.
    manifest_url : str or None
        The URL of the manifest's S3 version, if it was downloaded from S3.

    Raises
    ------
    RuntimeError
        If git-annex does not give a key for every asset.

    """
    manifest = capsule.manifest_path.relative_to(capsule.path).as_posix()
    run(command=["git", "annex", "add", "--quiet", "--force-large", "--", manifest], cwd=capsule.path)
    if manifest_url is not None:
        key = run(command=["git", "annex", "lookupkey", "--", manifest], cwd=capsule.path).stdout.strip()
        run(command=["git", "annex", "registerurl", key, manifest_url], cwd=capsule.path)
    else:
        _log.warning("%s was not downloaded from S3, so no URL is recorded for it.", manifest)
    if not assets:
        return

    paths = [f"{SOURCEDATA}/{asset.path}" for asset in assets]
    examined = run(
        command=["git", "annex", "examinekey", "--batch", "--migrate-to-backend=SHA256E", "--format=${key}\\n"],
        cwd=capsule.path,
        stdin="".join(
            f"SHA256-s{asset.size}--{asset.sha256} {path}\n" for asset, path in zip(assets, paths, strict=True)
        ),
    )
    keys = examined.stdout.splitlines()
    if len(keys) != len(assets) or not all(keys):
        message = f"git-annex gave {len(keys)} keys for {len(assets)} assets:\n{examined.stdout}\n{examined.stderr}"
        raise RuntimeError(message)
    run(
        command=["git", "annex", "registerurl", "--batch"],
        cwd=capsule.path,
        stdin="".join(f"{key} {asset.url}\n" for key, asset in zip(keys, assets, strict=True)),
    )
    run(
        command=["git", "annex", "fromkey", "--force", "--batch"],
        cwd=capsule.path,
        stdin="".join(f"{key} {path}\n" for key, path in zip(keys, paths, strict=True)),
    )


def _read_url(url: str, /) -> str:
    # file:// URLs are read too, so that a local copy can stand in for a published one.
    with urllib.request.urlopen(url, timeout=60) as response:  # noqa: S310
        return response.read().decode()


def register_image_url(*, capsule: Capsule, container_name: str, url: str) -> bool:
    """
    Record where git-annex can download a capsule's image, if the image published there is the capsule's.

    The published image is identified by the SHA-256 published next to it, at ``<url>.sha256``.
    An image obtained some other way (e.g., built locally) has another key, and is left alone.

    Parameters
    ----------
    capsule : Capsule
        The capsule.
    container_name : str
        The name of the image's registration in the capsule's DataLad configuration.
    url : str
        Where the image is published.

    Returns
    -------
    bool
        Whether the URL was recorded.

    """
    image = run(
        command=["git", "config", "-f", ".datalad/config", "--get", f"datalad.containers.{container_name}.image"],
        cwd=capsule.path,
        check=False,
    ).stdout.strip()
    if not image:
        return False
    key = run(command=["git", "annex", "lookupkey", "--", image], cwd=capsule.path, check=False).stdout.strip()
    match = _SHA256_KEY_RE.fullmatch(key)
    if match is None:
        return False
    try:
        published = _read_url(f"{url}.sha256").split()
    except (OSError, ValueError) as error:
        _log.warning("Could not read the SHA-256 published with %s (%s), so its URL is not recorded.", url, error)
        return False
    if published[:1] != [match["sha256"]]:
        _log.info("The image published at %s is not %s's, so its URL is not recorded.", url, capsule.name)
        return False
    run(command=["git", "annex", "registerurl", key, url], cwd=capsule.path)
    return True


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
        if branch in branches or not is_capsule_tag(branch) or name != capsule_name(pipeline=pipeline, tag=branch):
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
    List the remote's branches that hold capsules, which are those named by a capsule's tag.

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
    return {branch for branch in remote_heads(root=main_root, remote=remote) if is_capsule_tag(branch)}


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
