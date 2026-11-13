"""Run git, git-annex and DataLad for committing and publishing job capsules."""

import collections.abc
import functools
import logging
import pathlib
import subprocess
import time

_log = logging.getLogger(__name__)

ANNEX_BRANCH = "git-annex"


def run(
    *,
    command: list[str],
    cwd: pathlib.Path,
    check: bool = True,
    stdin: str | None = None,
) -> subprocess.CompletedProcess:
    """
    Run a command and capture its output.

    Parameters
    ----------
    command : list[str]
        The command and its arguments.
    cwd : pathlib.Path
        The directory to run it in.
    check : bool, default=True
        Whether to raise when the command fails.
    stdin : str or None, default=None
        Text to give the command on its standard input (e.g., the lines of a git-annex ``--batch``).

    Returns
    -------
    subprocess.CompletedProcess
        The finished command, with its output as text.

    Raises
    ------
    RuntimeError
        If ``check`` is set and the command exits with a non-zero code.

    """
    _log.debug("Running %s in %s", command, cwd)
    result = subprocess.run(command, cwd=cwd, input=stdin, capture_output=True, text=True, check=False)
    if check and result.returncode != 0:
        message = (
            f"Command {command} failed in {cwd} with exit code {result.returncode}.\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        raise RuntimeError(message)
    return result


def repository_root(path: pathlib.Path, /) -> pathlib.Path:
    """
    Find the root of the git repository that contains a path.

    Parameters
    ----------
    path : pathlib.Path
        A path inside the repository.

    Returns
    -------
    pathlib.Path
        The repository's top-level directory.

    """
    return pathlib.Path(run(command=["git", "rev-parse", "--show-toplevel"], cwd=path).stdout.strip())


def ensure_annex(
    *,
    root: pathlib.Path,
    remote: str | None = "origin",
    description: str = "ephemeral job runner",
) -> None:
    """
    Initialize git-annex in a clone as a private repository, unless it is already initialized.

    Runners are ephemeral. With ``annex.private`` nothing about the clone is written to the
    git-annex branch, so its UUID never appears as a location of any content.

    Parameters
    ----------
    root : pathlib.Path
        The root of the clone.
    remote : str or None, default="origin"
        A remote whose git-annex branch to fetch first, if it has one.
    description : str, default="ephemeral job runner"
        The description of this git-annex repository.

    """
    if run(command=["git", "config", "--get", "annex.uuid"], cwd=root, check=False).returncode == 0:
        return
    if remote is not None and run(command=["git", "remote", "get-url", remote], cwd=root, check=False).returncode == 0:
        _fetch_annex(root=root, remote=remote, check=False)
    run(command=["git", "config", "annex.private", "true"], cwd=root)
    run(command=["git", "annex", "init", description], cwd=root)


def pull(*, root: pathlib.Path, remote: str = "origin", branch: str) -> None:
    """
    Rebase HEAD onto a remote branch, which fast-forwards a clone that is only behind it.

    Parameters
    ----------
    root : pathlib.Path
        The root of the clone.
    remote : str, default="origin"
        The remote to fetch from.
    branch : str
        The branch to rebase onto.

    """
    run(command=["git", "fetch", "--quiet", remote, branch], cwd=root)
    run(command=["git", "rebase", "--quiet", "FETCH_HEAD"], cwd=root)


def _push(
    *,
    root: pathlib.Path,
    remote: str,
    refspec: str,
    attempts: int,
    integrate: collections.abc.Callable[[], None],
) -> None:
    for attempt in range(1, attempts + 1):
        result = run(command=["git", "push", remote, refspec], cwd=root, check=False)
        if result.returncode == 0:
            return
        _log.warning("Push attempt %d failed:\n%s", attempt, result.stderr)
        if attempt == attempts:
            message = f"Could not push to {remote} after {attempts} attempts:\n{result.stderr}"
            raise RuntimeError(message)
        time.sleep(2**attempt)
        integrate()


def push(*, root: pathlib.Path, remote: str = "origin", branch: str, attempts: int = 4) -> None:
    """
    Push HEAD to a branch, rebasing onto concurrent pushes.

    Template updates from the central repository may land on main while a job runs. A rejected
    push is therefore retried after integrating the remote's changes.

    Parameters
    ----------
    root : pathlib.Path
        The root of the clone.
    remote : str, default="origin"
        The remote to push to.
    branch : str
        The branch to push to.
    attempts : int, default=4
        How many times to try.

    Raises
    ------
    RuntimeError
        If every attempt is rejected.

    """
    _push(
        root=root,
        remote=remote,
        refspec=f"HEAD:refs/heads/{branch}",
        attempts=attempts,
        integrate=functools.partial(pull, root=root, remote=remote, branch=branch),
    )


def _fetch_annex(*, root: pathlib.Path, remote: str, check: bool = True) -> None:
    run(
        command=["git", "fetch", "--quiet", remote, f"+refs/heads/{ANNEX_BRANCH}:refs/remotes/{remote}/{ANNEX_BRANCH}"],
        cwd=root,
        check=check,
    )


def _merge_annex(*, root: pathlib.Path, remote: str) -> None:
    _fetch_annex(root=root, remote=remote)
    run(command=["git", "annex", "merge"], cwd=root)


def push_annex(*, root: pathlib.Path, remote: str = "origin", attempts: int = 4) -> bool:
    """
    Push the git-annex branch once it records anything, merging what others pushed to it.

    The clone's own git-annex is private, so the branch holds only what is recorded about content
    itself, such as where to download it. git-annex merges its branch without conflicts.

    Parameters
    ----------
    root : pathlib.Path
        The root of the clone.
    remote : str, default="origin"
        The remote to push to.
    attempts : int, default=4
        How many times to try.

    Returns
    -------
    bool
        Whether the branch was pushed.

    """
    records = run(command=["git", "ls-tree", "--name-only", f"refs/heads/{ANNEX_BRANCH}"], cwd=root, check=False)
    if records.returncode != 0 or not records.stdout.strip():
        return False
    _push(
        root=root,
        remote=remote,
        refspec=f"refs/heads/{ANNEX_BRANCH}:refs/heads/{ANNEX_BRANCH}",
        attempts=attempts,
        integrate=functools.partial(_merge_annex, root=root, remote=remote),
    )
    return True
