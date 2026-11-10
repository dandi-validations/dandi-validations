"""Run one validation job in a Dandiset repository, by forming, executing, recording and pushing its capsule."""

import datetime
import json
import logging
import os
import pathlib
import platform
import subprocess
import sys

from ._branches import (
    capsule_branches,
    checkout_capsule,
    commit_capsule,
    commit_main,
    create_capsule,
    link_sourcedata,
    prune_submodules,
    publish_capsule,
    register_submodule,
    remote_heads,
    remote_url,
)
from ._capsule import Capsule, capsule_status, is_up_to_date, plan_capsule, write_capsule
from ._git import ensure_annex, pull, push, repository_root, run
from ._globals import DEFAULT_BRANCH, DEFAULT_PARAMS_KEY

_log = logging.getLogger(__name__)

_GITHUB_ENVIRONMENT = {
    "GITHUB_REPOSITORY": "repository",
    "GITHUB_RUN_ID": "run_id",
    "GITHUB_RUN_ATTEMPT": "run_attempt",
    "GITHUB_WORKFLOW": "workflow",
    "GITHUB_EVENT_NAME": "event_name",
    "GITHUB_SHA": "sha",
    "RUNNER_NAME": "runner_name",
    "RUNNER_OS": "runner_os",
    "RUNNER_ARCH": "runner_arch",
    "ImageOS": "image_os",
    "ImageVersion": "image_version",
}
_TIMEOUT_EXIT_CODE = 124


def _github_context() -> dict:
    context = {name: os.environ[variable] for variable, name in _GITHUB_ENVIRONMENT.items() if variable in os.environ}
    if "run_id" in context:
        server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
        context["run_url"] = f"{server}/{context.get('repository', '')}/actions/runs/{context['run_id']}"
    return context


def _execute(*, capsule: Capsule, time_limit_minutes: float, log_file_path: pathlib.Path) -> int:
    command = ["bash", str(capsule.submit_script_path)]
    if time_limit_minutes > 0:
        # With --foreground, timeout signals only submit.sh, whose trap stops the run's own process
        # group and lets duct write its records. Otherwise duct is signalled too and the run's
        # processes are orphaned.
        command = [
            "timeout",
            "--foreground",
            "--signal=TERM",
            "--kill-after=180",
            f"{time_limit_minutes:g}m",
            *command,
        ]
    log_file_path.parent.mkdir(parents=True, exist_ok=True)
    with (
        log_file_path.open("w") as log_file,
        subprocess.Popen(
            command,
            cwd=capsule.path,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        ) as process,
    ):
        for line in process.stdout or ():
            sys.stdout.write(line)
            sys.stdout.flush()
            log_file.write(line)
        return process.wait()


def run_job(  # noqa: PLR0913
    *,
    path: pathlib.Path,
    dandiset_id: str,
    dandiset_version: str = "draft",
    params_key: str = DEFAULT_PARAMS_KEY,
    force: bool = False,
    push_to: str | None = None,
    branch: str | None = None,
    time_limit_minutes: float = 0,
    manifest_directory: pathlib.Path | None = None,
    execute: bool = True,
    now: datetime.datetime | None = None,
) -> int:
    """
    Validate a Dandiset in the capsule of the pinned image, from a clone of the repository's main.

    The capsule's branch is checked out (or started) as a worktree of this clone, and its run is
    committed to it. Main's submodule for it is pinned to the result, both when the run is claimed
    and when it is recorded. With ``push_to``, the clone is first rebased onto the remote's main,
    and both branches are pushed at each point.

    Parameters
    ----------
    path : pathlib.Path
        A path in the clone of the repository's main.
    dandiset_id : str
        The six-digit identifier of the Dandiset.
    dandiset_version : str, default="draft"
        The Dandiset version.
    params_key : str, default="default"
        The registered parameter set.
    force : bool, default=False
        Whether to validate even if the capsule already validated these inputs.
    push_to : str or None, default=None
        The remote to push the capsule's branch and main to.
    branch : str or None, default=None
        The branch to push main to, if not ``main``.
    time_limit_minutes : float, default=0
        Stop the run after this many minutes (0 for no limit).
    manifest_directory : pathlib.Path or None, default=None
        A directory holding the Dandiset's ``assets.jsonld`` and ``dandiset.yaml`` to use instead
        of the archive's current ones on S3 (e.g., a frozen copy, to rerun a capsule or test).
    execute : bool, default=True
        Whether to run the capsule, rather than only form and claim it.
    now : datetime.datetime or None, default=None
        When the run is formed, if not now.

    Returns
    -------
    int
        The exit code, which is 0 for a successful (or skipped) validation.

    """
    root = repository_root(path)
    branch = branch or DEFAULT_BRANCH
    remote = push_to or "origin"
    if push_to and branch in remote_heads(root=root, remote=push_to):
        # A rerun checks out the commit its first attempt was dispatched at, as does a run queued
        # behind another. Main's submodule may have been added since, which a rebase of the job's
        # commit onto main could not merge.
        pull(root=root, remote=push_to, branch=branch)
    # One git-annex for main and every capsule worktree, private to this ephemeral clone.
    ensure_annex(root=root, remote=None)

    assets_jsonld = dandiset_yaml = None
    if manifest_directory is not None:
        assets_jsonld = (manifest_directory / "assets.jsonld").read_bytes()
        dandiset_yaml = (manifest_directory / "dandiset.yaml").read_bytes()
    plan = plan_capsule(
        repository_root=root,
        dandiset_id=dandiset_id,
        dandiset_version=dandiset_version,
        params_key=params_key,
        assets_jsonld=assets_jsonld,
        dandiset_yaml=dandiset_yaml,
    )
    if isinstance(plan, str):
        _log.info("%s", plan)
        return 0
    capsule = plan.capsule
    if not checkout_capsule(main_root=root, capsule=capsule, remote=remote if push_to else None):
        create_capsule(main_root=root, capsule=capsule)
    if not force and is_up_to_date(plan):
        _log.info("%s already validated these inputs; nothing to do (use --force to validate again).", capsule.name)
        return 0

    sourcedata = link_sourcedata(capsule=capsule)
    if sourcedata is None:
        _log.info("Dandiset %s has no DataLad mirror to link as sourcedata/.", dandiset_id)
    write_capsule(plan=plan, sourcedata=sourcedata, now=now)
    _log.info("Formed %s for %d assets.", capsule.name, plan.number_of_assets)

    # Claiming the capsule before running it makes a run that never reports back read as stalled.
    commit_and_publish(
        main_root=root,
        capsule=capsule,
        paths=[capsule.path],
        message=f"Submit {capsule.name}: dandi validate of Dandiset {dandiset_id} ({dandiset_version})",
        push_to=push_to,
        branch=branch,
    )
    if not execute:
        _log.info("%s: claimed, not run.", capsule.name)
        return 0

    started_at = datetime.datetime.now(tz=datetime.UTC)
    exit_code = _execute(
        capsule=capsule,
        time_limit_minutes=time_limit_minutes,
        log_file_path=capsule.logs_directory / "submit.log",
    )
    finished_at = datetime.datetime.now(tz=datetime.UTC)

    run_record = {
        "exit_code": exit_code,
        "timed_out": exit_code == _TIMEOUT_EXIT_CODE,
        "started_at": started_at.replace(microsecond=0).isoformat(),
        "finished_at": finished_at.replace(microsecond=0).isoformat(),
        "host": {"python": platform.python_version(), "platform": platform.platform()},
        "github_actions": _github_context(),
    }
    (capsule.logs_directory / "run.json").write_text(json.dumps(run_record, indent=2) + "\n")

    status = capsule_status(capsule.path)
    commit_and_publish(
        main_root=root,
        capsule=capsule,
        paths=[capsule.logs_directory],
        message=f"Record {capsule.name} ({status})",
        push_to=push_to,
        branch=branch,
    )

    _log.info("%s: %s (exit code %d)", capsule.name, status, exit_code)
    if status == "successful":
        return 0
    return exit_code or 1


def commit_and_publish(  # noqa: PLR0913
    *,
    main_root: pathlib.Path,
    capsule: Capsule,
    paths: list[pathlib.Path],
    message: str,
    push_to: str | None,
    branch: str,
) -> None:
    """
    Commit a capsule's branch, then pin main's submodule for it.

    Parameters
    ----------
    main_root : pathlib.Path
        The root of the clone of main.
    capsule : Capsule
        The capsule.
    paths : list[pathlib.Path]
        The paths of the capsule to commit.
    message : str
        The commit message, for both branches.
    push_to : str or None
        The remote to push both branches to, if any.
    branch : str
        The branch to push main to.

    """
    commit_capsule(capsule=capsule, paths=paths, message=message)
    if push_to:
        commit = publish_capsule(main_root=main_root, capsule=capsule, remote=push_to)
        url = remote_url(root=main_root, remote=push_to)
        prune_submodules(
            main_root=main_root,
            branches=capsule_branches(main_root=main_root, remote=push_to),
            pipeline=capsule.pipeline,
        )
    else:
        commit = run(command=["git", "rev-parse", f"refs/heads/{capsule.tag}"], cwd=main_root).stdout.strip()
        url = remote_url(root=main_root, remote="origin")
    register_submodule(main_root=main_root, capsule=capsule, url=url or f"./{capsule.name}", commit=commit)
    commit_main(main_root=main_root, paths=[], message=message)
    if push_to:
        push(root=main_root, remote=push_to, branch=branch)
