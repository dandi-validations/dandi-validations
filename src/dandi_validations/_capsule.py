"""
Job capsules: one self-contained directory per validation run.

Each Dandiset repository is a standalone (type 2) BIDS derivative dataset of its Dandiset, holding
job capsules laid out like DANDI Compute's (see the README of dandi-compute/001697)::

    {dandiset_id}/
    ├── dataset_description.json  ← the derivative dataset
    └── pipeline-dandi+validate/
        ├── jobs.tsv, jobs.json  ← one row per capsule, rebuilt after every job
        └── job-{job ID}/
            ├── code/  ← the exact code and inputs the job ran with
            ├── logs/  ← all runtime records, for success or failure
            ├── derivatives/  ← the validation results (only for a completed run)
            └── dataset_description.json  ← provenance

Validation runs over a whole Dandiset, so capsules sit directly under the pipeline rather than
under ``sub-``/``ses-`` levels as DANDI Compute's per-asset pipelines do.

A capsule's lifecycle status is read from what it contains, as DANDI Compute does: ``pending``
(code only), ``stalled`` (submitted but nothing logged), ``failed`` (logs without results) and
``successful`` (results under ``derivatives/``).
"""

import dataclasses
import datetime
import importlib.metadata
import json
import os
import pathlib
import shutil
import socket
import subprocess

from ._globals import (
    CODEBASE_URL,
    DANDI_ARCHIVE_URL,
    DEFAULT_PARAMS_KEY,
    ORGANIZATION,
    PROVENANCE_KEY,
)
from ._job_id import capsules_for_hash, compute_job_hash, next_available_job_id
from ._manifest import (
    assets_table_bytes,
    fetch_assets_jsonld,
    fetch_dandiset_yaml,
    parse_assets_jsonld,
    select_assets,
    skipped_table_bytes,
    source_digest,
    write_table,
)
from ._pipeline import CAPSULE_CODE_DIRECTORY, TEMPLATES_DIRECTORY, Parameters, Pipeline, load_parameters, load_pipeline
from ._registry import resolve_digest
from ._templates import render
from ._version_code import parse_version_code

SUBMITTED_MARKER_PREFIX = "submitted_date-"
JOB_STATUSES = ("pending", "stalled", "failed", "successful", "unknown")


def pipeline_relative_directory(dandiset_id: str, pipeline: str) -> pathlib.PurePosixPath:
    """Where a pipeline's capsules live in a Dandiset repository: at its root, one per pipeline."""
    return pathlib.PurePosixPath(f"pipeline-{pipeline}")


@dataclasses.dataclass(frozen=True)
class Capsule:
    repository_root: pathlib.Path
    dandiset_id: str
    pipeline: str
    job_id: str

    @property
    def relative_path(self) -> pathlib.PurePosixPath:
        return pipeline_relative_directory(self.dandiset_id, self.pipeline) / self.job_id

    @property
    def path(self) -> pathlib.Path:
        return self.repository_root / self.relative_path

    @property
    def code_directory(self) -> pathlib.Path:
        return self.path / "code"

    @property
    def logs_directory(self) -> pathlib.Path:
        return self.path / "logs"

    @property
    def derivatives_directory(self) -> pathlib.Path:
        return self.path / "derivatives"

    @property
    def dataset_description_path(self) -> pathlib.Path:
        return self.path / "dataset_description.json"

    @property
    def submit_script_path(self) -> pathlib.Path:
        return self.code_directory / "submit.sh"


def list_capsule_names(repository_root: pathlib.Path, dandiset_id: str, pipeline: str) -> list[str]:
    directory = repository_root / pipeline_relative_directory(dandiset_id, pipeline)
    if not directory.is_dir():
        return []
    return sorted(path.name for path in directory.iterdir() if path.is_dir() and path.name.startswith("job-"))


def _has_files(directory: pathlib.Path) -> bool:
    return directory.is_dir() and any(path.is_file() or path.is_symlink() for path in directory.rglob("*"))


def capsule_status(capsule_directory: pathlib.Path) -> str:
    code_directory = capsule_directory / "code"
    if _has_files(capsule_directory / "derivatives"):
        return "successful"
    if _has_files(capsule_directory / "logs"):
        return "failed"
    if code_directory.is_dir() and any(code_directory.glob(f"{SUBMITTED_MARKER_PREFIX}*")):
        return "stalled"
    if code_directory.is_dir():
        return "pending"
    return "unknown"


def _codebase_version() -> str:
    try:
        version = importlib.metadata.version("dandi-validations")
    except importlib.metadata.PackageNotFoundError:
        version = "unknown"
    commit = os.environ.get("DANDI_VALIDATIONS_CODEBASE_COMMIT")
    if not commit:
        try:
            commit = subprocess.run(
                ["git", "-C", str(pathlib.Path(__file__).parent), "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            commit = "unknown"
    return f"v{version}+{commit}"


def claim_identifier() -> str:
    """Who claimed (submitted) a capsule: the GitHub Actions run attempt, or this host and process."""
    if os.environ.get("GITHUB_RUN_ID"):
        server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
        repository = os.environ.get("GITHUB_REPOSITORY", "")
        run_id = os.environ["GITHUB_RUN_ID"]
        attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "1")
        return f"{server}/{repository}/actions/runs/{run_id}/attempts/{attempt}"
    return f"local:{socket.gethostname()}:{os.getpid()}"


@dataclasses.dataclass(frozen=True)
class PrepareResult:
    capsule: Capsule | None
    reason: str


def prepare_capsule(
    *,
    repository_root: pathlib.Path,
    dandiset_id: str,
    dandiset_version: str = "draft",
    params_key: str = DEFAULT_PARAMS_KEY,
    force: bool = False,
    now: datetime.datetime | None = None,
    pipeline: Pipeline | None = None,
    assets_jsonld: bytes | None = None,
    dandiset_yaml: bytes | None = None,
) -> PrepareResult:
    """
    Form (and claim) the job capsule that validates a Dandiset, unless an equivalent one succeeded.

    The capsule's identity covers the container version, the parameter set and the digest of the
    Dandiset's registered assets and metadata, so an unchanged Dandiset is not validated twice by
    the same pipeline. A capsule of the same job that did not succeed is retried in a new capsule.
    """
    now = now if now is not None else datetime.datetime.now(tz=datetime.timezone.utc)
    pipeline = pipeline if pipeline is not None else load_pipeline()
    parameters = load_parameters(params_key)

    if assets_jsonld is None:
        assets_jsonld = fetch_assets_jsonld(dandiset_id, dandiset_version)
    if dandiset_yaml is None:
        dandiset_yaml = fetch_dandiset_yaml(dandiset_id, dandiset_version)

    rows, skipped = select_assets(parse_assets_jsonld(assets_jsonld))
    if not rows:
        reason = f"Dandiset {dandiset_id} ({dandiset_version}) has no assets that can be streamed"
        if skipped:
            reason += f" ({len(skipped)} skipped: {sorted({row.reason for row in skipped})})"
        return PrepareResult(capsule=None, reason=reason + ".")

    assets_table = assets_table_bytes(rows)
    digest = source_digest(assets_table=assets_table, dandiset_yaml=dandiset_yaml)
    job_hash = compute_job_hash(
        dandiset_id=dandiset_id,
        dandiset_version=dandiset_version,
        pipeline=pipeline.pipeline,
        version=pipeline.container_version,
        params=parameters.identifier,
        source_digest=digest,
    )

    existing_names = list_capsule_names(repository_root, dandiset_id, pipeline.pipeline)
    same_job = capsules_for_hash(existing_names, job_hash)
    pipeline_directory = repository_root / pipeline_relative_directory(dandiset_id, pipeline.pipeline)
    successful = [name for name in same_job if capsule_status(pipeline_directory / name) == "successful"]
    if successful and not force:
        reason = f"{successful[-1]} already validated these inputs; nothing to do (use --force to validate again)."
        return PrepareResult(capsule=None, reason=reason)

    job_id = next_available_job_id(job_hash=job_hash, taken_job_ids=existing_names, date=now.date())
    capsule = Capsule(
        repository_root=repository_root, dandiset_id=dandiset_id, pipeline=pipeline.pipeline, job_id=job_id
    )
    _write_capsule(
        capsule=capsule,
        pipeline=pipeline,
        image_digest=resolve_digest(pipeline.container_image, pipeline.container_version),
        parameters=parameters,
        dandiset_version=dandiset_version,
        dandiset_yaml=dandiset_yaml,
        assets_table=assets_table,
        skipped_table=skipped_table_bytes(skipped) if skipped else None,
        digest=digest,
        number_of_assets=len(rows),
        number_of_skipped_assets=len(skipped),
        now=now,
    )
    return PrepareResult(capsule=capsule, reason=f"Formed {job_id} for {len(rows)} assets.")


def _write_capsule(
    *,
    capsule: Capsule,
    pipeline: Pipeline,
    image_digest: str | None,
    parameters: Parameters,
    dandiset_version: str,
    dandiset_yaml: bytes,
    assets_table: bytes,
    skipped_table: bytes | None,
    digest: str,
    number_of_assets: int,
    number_of_skipped_assets: int,
    now: datetime.datetime,
) -> None:
    code_directory = capsule.code_directory
    code_directory.mkdir(parents=True)

    assets_file_path = write_table(assets_table, code_directory / "assets.tsv")
    if skipped_table is not None:
        write_table(skipped_table, code_directory / "skipped_assets.tsv")
    (code_directory / "dandiset.yaml").write_bytes(dandiset_yaml)
    params_file_path = code_directory / parameters.file_path.name
    shutil.copyfile(parameters.file_path, params_file_path)
    for name in ("run.sh", "summarize.py"):
        shutil.copyfile(CAPSULE_CODE_DIRECTORY / name, code_directory / name)

    submit_script = render(
        (TEMPLATES_DIRECTORY / "submit.sh").read_text(),
        job_id=capsule.job_id,
        dandiset_id=capsule.dandiset_id,
        dandiset_version=dandiset_version,
        organization=ORGANIZATION,
        capsule_path=capsule.relative_path.as_posix(),
        container_name=pipeline.container_name,
        container_version=pipeline.container_version,
        image_reference=pipeline.image_reference,
        image_digest=image_digest or "unknown",
        docker_url=pipeline.docker_url(image_digest),
        sif_url=pipeline.sif_url,
        params_file_name=params_file_path.name,
        assets_file_name=assets_file_path.name,
    )
    capsule.submit_script_path.write_text(submit_script)
    for name in ("submit.sh", "run.sh"):
        (code_directory / name).chmod(0o755)

    created_at = now.replace(microsecond=0).isoformat()
    dataset_description = {
        "Name": f"DANDI Validations: dandi validate of Dandiset {capsule.dandiset_id}",
        "BIDSVersion": "1.10.1",
        "DatasetType": "derivative",
        "GeneratedBy": [
            {
                "Name": "dandi validate",
                "Description": (
                    "DANDI CLI validation, streaming the content of annexed files through datalad-fuse "
                    f"adapters ({pipeline.dandi_cli_reference or pipeline.dandi_cli_repository})."
                ),
                "Version": pipeline.dandi_cli_commit,
                "CodeURL": pipeline.dandi_cli_code_url,
            },
            {
                "Name": "DANDI Validations: Container",
                "Description": (
                    "The dandi-validation image, tagged by its validator version code, run through Apptainer "
                    "with datalad containers-run."
                ),
                "Version": pipeline.container_version,
                "CodeURL": (f"{pipeline.image_reference}@{image_digest}" if image_digest else pipeline.image_reference),
                "Container": {
                    "Type": "docker",
                    "Tag": pipeline.container_version,
                    "URI": pipeline.docker_url(image_digest),
                },
            },
            {
                "Name": "DANDI Validations: Code",
                "Description": "Orchestration of validation jobs on GitHub Actions.",
                "Version": _codebase_version(),
                "CodeURL": CODEBASE_URL,
            },
        ],
        "SourceDatasets": [
            {
                "URL": f"{DANDI_ARCHIVE_URL}/dandiset/{capsule.dandiset_id}/{dandiset_version}",
                "Version": dandiset_version,
            }
        ],
        PROVENANCE_KEY: {
            "job_id": capsule.job_id,
            "dandiset_id": capsule.dandiset_id,
            "dandiset_version": dandiset_version,
            "pipeline": pipeline.pipeline,
            "version": pipeline.container_version,
            # The six validator versions of the image (the shape of the archive's
            # `validatedRequiredVersions`, dandi/dandi-archive#2987).
            "validator_versions": parse_version_code(pipeline.container_version),
            "image": pipeline.image_reference,
            "image_digest": image_digest,
            "codebase": _codebase_version().split("+")[0],
            "params": parameters.identifier,
            "params_key": parameters.key,
            "source_digest": digest,
            "assets": number_of_assets,
            "skipped_assets": number_of_skipped_assets,
            "created_at": created_at,
        },
    }
    capsule.dataset_description_path.write_text(json.dumps(dataset_description, indent=2) + "\n")

    marker_name = f"{SUBMITTED_MARKER_PREFIX}{now:%Y+%m+%d}_time-{now:%H+%M+%S}"
    (code_directory / marker_name).write_text(claim_identifier() + "\n")


def read_provenance(capsule_directory: pathlib.Path) -> dict:
    file_path = capsule_directory / "dataset_description.json"
    if not file_path.exists():
        return {}
    return json.loads(file_path.read_text()).get(PROVENANCE_KEY, {})
