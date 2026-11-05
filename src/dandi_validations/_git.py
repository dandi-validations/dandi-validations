"""Thin wrappers around git, git-annex and DataLad for committing and publishing job capsules."""

import logging
import pathlib
import subprocess
import time

_log = logging.getLogger(__name__)


def run(command: list[str], *, cwd: pathlib.Path, check: bool = True) -> subprocess.CompletedProcess:
    _log.debug("Running %s in %s", command, cwd)
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True)
    if check and result.returncode != 0:
        message = (
            f"Command {command} failed in {cwd} with exit code {result.returncode}.\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        raise RuntimeError(message)
    return result


def repository_root(path: pathlib.Path) -> pathlib.Path:
    return pathlib.Path(run(["git", "rev-parse", "--show-toplevel"], cwd=path).stdout.strip())


def current_branch(root: pathlib.Path) -> str:
    return run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=root).stdout.strip()


def has_ref(root: pathlib.Path, ref: str) -> bool:
    return run(["git", "rev-parse", "--verify", "--quiet", ref], cwd=root, check=False).returncode == 0


def ensure_annex(root: pathlib.Path, *, remote: str = "origin", description: str = "ephemeral job runner") -> None:
    """
    Initialize git-annex in this clone as a private repository, unless it already is initialized.

    Runners are ephemeral: with ``annex.private`` nothing about this clone is written to the
    git-annex branch, so its UUID never appears as a location of any content.
    """
    if run(["git", "config", "--get", "annex.uuid"], cwd=root, check=False).returncode == 0:
        return
    if run(["git", "remote", "get-url", remote], cwd=root, check=False).returncode == 0:
        run(["git", "fetch", remote, f"+refs/heads/git-annex:refs/remotes/{remote}/git-annex"], cwd=root, check=False)
    run(["git", "config", "annex.private", "true"], cwd=root)
    run(["git", "annex", "init", description], cwd=root)


def save(root: pathlib.Path, paths: list[pathlib.Path], message: str) -> bool:
    """``datalad save`` the given paths; returns whether a commit was made."""
    before = run(["git", "rev-parse", "HEAD"], cwd=root, check=False).stdout.strip()
    command = ["datalad", "save", "-d", str(root), "-m", message, *[str(path) for path in paths]]
    run(command, cwd=root)
    after = run(["git", "rev-parse", "HEAD"], cwd=root).stdout.strip()
    return before != after


def push(root: pathlib.Path, *, remote: str = "origin", branch: str, attempts: int = 4) -> None:
    """
    Push the branch (and the git-annex branch, when there is one), rebasing onto concurrent pushes.

    Template updates from the central repository may land while a job runs, so a rejected push
    is retried after integrating the remote's changes.
    """
    for attempt in range(1, attempts + 1):
        refspecs = [f"HEAD:refs/heads/{branch}"]
        if has_ref(root, "refs/heads/git-annex"):
            refspecs.append("refs/heads/git-annex:refs/heads/git-annex")
        result = run(["git", "push", remote, *refspecs], cwd=root, check=False)
        if result.returncode == 0:
            return
        _log.warning("Push attempt %d failed:\n%s", attempt, result.stderr)
        if attempt == attempts:
            message = f"Could not push to {remote} after {attempts} attempts:\n{result.stderr}"
            raise RuntimeError(message)
        time.sleep(2**attempt)
        run(["git", "fetch", remote], cwd=root)
        if has_ref(root, f"refs/remotes/{remote}/{branch}"):
            run(["git", "rebase", f"{remote}/{branch}"], cwd=root)
        if has_ref(root, "refs/heads/git-annex"):
            run(["git", "annex", "merge"], cwd=root)
