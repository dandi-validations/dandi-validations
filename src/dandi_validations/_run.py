"""Run one validation job in a Dandiset repository: form the capsule, execute it, record it, push it."""

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
    remote_url,
)
from ._capsule import DATASET_DESCRIPTION, Capsule, capsule_status, is_up_to_date, plan_capsule, write_capsule
from ._git import ensure_annex, push, repository_root, run
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


def _github_context() -> dict:
    context = {name: os.environ[variable] for variable, name in _GITHUB_ENVIRONMENT.items() if variable in os.environ}
    if "run_id" in context:
        server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
        context["run_url"] = f"{server}/{context.get('repository', '')}/actions/runs/{context['run_id']}"
    return context


def _execute(capsule: Capsule, *, time_limit_minutes: float, log_file_path: pathlib.Path) -> int:
    """Run the capsule's submit.sh, teeing its output into the capsule's logs."""
    command = ["bash", str(capsule.submit_script_path)]
    if time_limit_minutes > 0:
        # --foreground: signal only submit.sh, whose trap stops the run's own process group and
        # lets duct write its records. Without it, timeout signals its whole process group,
        # duct included, and the run's processes are orphaned.
        command = [
            "timeout",
            "--foreground",
            "--signal=TERM",
            "--kill-after=180",
            f"{time_limit_minutes:g}m",
            *command,
        ]
    log_file_path.parent.mkdir(parents=True, exist_ok=True)
    with log_file_path.open("w") as log_file:
        process = subprocess.Popen(
            command,
            cwd=capsule.path,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        assert process.stdout is not None
        for line in process.stdout:
            sys.stdout.write(line)
            sys.stdout.flush()
            log_file.write(line)
        return process.wait()


def run_job(
    *,
    path: pathlib.Path,
    dandiset_id: str,
    dandiset_version: str = "draft",
    params_key: str = DEFAULT_PARAMS_KEY,
    force: bool = False,
    push_to: str | None = None,
    branch: str | None = None,
    time_limit_minutes: float = 0,
) -> int:
    """
    Validate a Dandiset in the capsule of the pinned image, from a clone of the repository's main.

    The capsule's branch is checked out (or started) as a worktree of this clone, its run is
    committed to it, and main's submodule for it is pinned to the result, both when the run is
    claimed and when it is recorded. With ``push_to``, both branches are pushed at each point.
    """
    root = repository_root(path)
    branch = branch or DEFAULT_BRANCH
    remote = push_to or "origin"
    # One git-annex for main and every capsule worktree, private to this ephemeral clone.
    ensure_annex(root, remote=None)

    plan = plan_capsule(
        repository_root=root, dandiset_id=dandiset_id, dandiset_version=dandiset_version, params_key=params_key
    )
    if isinstance(plan, str):
        print(plan, flush=True)
        return 0
    capsule = plan.capsule
    if not checkout_capsule(root, capsule, remote if push_to else None):
        create_capsule(root, capsule)
    if not force and is_up_to_date(plan):
        print(f"{capsule.name} already validated these inputs; nothing to do (use --force to validate again).")
        return 0

    sourcedata = link_sourcedata(capsule)
    if sourcedata is None:
        print(f"Dandiset {dandiset_id} has no DataLad mirror to link as sourcedata/.", flush=True)
    write_capsule(plan, sourcedata=sourcedata)
    print(f"Formed {capsule.name} for {plan.number_of_assets} assets.", flush=True)

    # Claim the capsule before running it, so that a run that never reports back reads as stalled.
    # This also replaces the results of the capsule's previous run, which stay in its branch's history.
    _commit_and_publish(
        root,
        capsule,
        paths=[capsule.path],
        message=f"Submit {capsule.name}: dandi validate of Dandiset {dandiset_id} ({dandiset_version})",
        push_to=push_to,
        branch=branch,
    )

    started_at = datetime.datetime.now(tz=datetime.timezone.utc)
    exit_code = _execute(
        capsule, time_limit_minutes=time_limit_minutes, log_file_path=capsule.logs_directory / "submit.log"
    )
    finished_at = datetime.datetime.now(tz=datetime.timezone.utc)

    run_record = {
        "exit_code": exit_code,
        "timed_out": exit_code == 124,
        "started_at": started_at.replace(microsecond=0).isoformat(),
        "finished_at": finished_at.replace(microsecond=0).isoformat(),
        "host": {"python": platform.python_version(), "platform": platform.platform()},
        "github_actions": _github_context(),
    }
    (capsule.logs_directory / "run.json").write_text(json.dumps(run_record, indent=2) + "\n")

    status = capsule_status(capsule.path)
    _commit_and_publish(
        root,
        capsule,
        paths=[capsule.logs_directory],
        message=f"Record {capsule.name} ({status})",
        push_to=push_to,
        branch=branch,
    )

    print(f"{capsule.name}: {status} (exit code {exit_code})", flush=True)
    if status == "successful":
        return 0
    return exit_code or 1


def _commit_and_publish(
    root: pathlib.Path,
    capsule: Capsule,
    *,
    paths: list[pathlib.Path],
    message: str,
    push_to: str | None,
    branch: str,
) -> None:
    """Commit the capsule's branch, then pin main's submodule for it (pushing both with ``push_to``)."""
    commit_capsule(capsule, paths, message)
    if push_to:
        commit = publish_capsule(root, capsule, push_to)
        url = remote_url(root, push_to)
        prune_submodules(root, branches=capsule_branches(root, push_to), pipeline=capsule.pipeline)
    else:
        commit = run(["git", "rev-parse", f"refs/heads/{capsule.tag}"], cwd=root).stdout.strip()
        url = remote_url(root, "origin") or f"./{capsule.name}"
    register_submodule(root, capsule, url=url, commit=commit)
    commit_main(root, [DATASET_DESCRIPTION], message)
    if push_to:
        push(root, remote=push_to, branch=branch)
