"""Run one validation job in a Dandiset repository: form the capsule, execute it, record it, push it."""

import datetime
import json
import logging
import os
import pathlib
import platform
import subprocess
import sys

from ._capsule import Capsule, capsule_status, pipeline_relative_directory, prepare_capsule
from ._git import ensure_annex, push, repository_root, save
from ._globals import DEFAULT_BRANCH, DEFAULT_PARAMS_KEY
from ._jobs_table import write_jobs_table

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
            cwd=capsule.repository_root,
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
    root = repository_root(path)
    branch = branch or DEFAULT_BRANCH
    ensure_annex(root, remote=push_to or "origin")

    result = prepare_capsule(
        repository_root=root,
        dandiset_id=dandiset_id,
        dandiset_version=dandiset_version,
        params_key=params_key,
        force=force,
    )
    _log.info(result.reason)
    print(result.reason, flush=True)
    capsule = result.capsule
    if capsule is None:
        return 0

    # Claim the capsule before running it, so that a run that never reports back reads as stalled.
    save(
        root, [capsule.path], f"Submit {capsule.job_id}: dandi validate of Dandiset {dandiset_id} ({dandiset_version})"
    )
    if push_to:
        push(root, remote=push_to, branch=branch)

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
    pipeline_directory = root / pipeline_relative_directory(dandiset_id, capsule.pipeline)
    table_paths = write_jobs_table(pipeline_directory)
    save(root, [capsule.logs_directory, *table_paths], f"Record {capsule.job_id} ({status})")
    if push_to:
        push(root, remote=push_to, branch=branch)

    print(f"{capsule.job_id}: {status} (exit code {exit_code})", flush=True)
    if status == "successful":
        return 0
    return exit_code or 1
