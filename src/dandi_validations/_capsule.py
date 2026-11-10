"""
Capsules: one per image tag in each Dandiset repository.

Each Dandiset repository is a standalone (type 2) BIDS derivative dataset of its Dandiset. Its
``main`` branch holds the single ``dataset_description.json`` and one submodule per capsule::

    https://github.com/dandi-validations/{dandiset_id}/   (main)
    ├── dataset_description.json  ← the derivative dataset, with every capsule's provenance
    └── pipeline-dandi+validate_tag-{tag}/  ← submodule: this repository's branch {tag}
        ├── code/  ← the exact code and inputs of its latest run
        ├── logs/  ← all runtime records of that run, for success or failure
        └── derivatives/  ← the validation results (only for a completed run)

``{tag}`` is the dandi-validation image's tag: its validator version code. Each capsule is a
DataLad dataset on the branch named by that tag (see ``_branches``). It holds the latest
validation of the Dandiset with that image; a new run with the same image (after the Dandiset's
files change, or a failure) replaces its contents in a new commit on the branch, so earlier runs
remain in the branch's history, each with its ``datalad run`` record.

``dataset_description.json`` has a ``GeneratedBy`` entry per capsule naming its image
(``Container``), and its ``DandiValidations`` key maps each capsule to the inputs and image of its
latest run.

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
import re
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

#: What a capsule's run writes, which a new run replaces.
CAPSULE_CONTENTS = ("code", "logs", "derivatives", "intermediate")

SUBMITTED_MARKER_PREFIX = "submitted_date-"
JOB_STATUSES = ("pending", "stalled", "failed", "successful", "unknown")
DATASET_DESCRIPTION = "dataset_description.json"
CAPSULE_NAME_RE = re.compile(r"pipeline-(?P<pipeline>[A-Za-z0-9+]+)_tag-(?P<tag>[A-Za-z0-9_.+-]+)")

#: Provenance fields that identify what a capsule's run validated: a successful capsule whose
#: fields all match is not run again.
_INPUT_FIELDS = ("dandiset_version", "version", "params", "source_digest")


def capsule_name(pipeline: str, tag: str) -> str:
    return f"pipeline-{pipeline}_tag-{tag}"


@dataclasses.dataclass(frozen=True)
class Capsule:
    repository_root: pathlib.Path
    dandiset_id: str
    pipeline: str
    tag: str

    @property
    def name(self) -> str:
        return capsule_name(self.pipeline, self.tag)

    @property
    def relative_path(self) -> pathlib.PurePosixPath:
        return pathlib.PurePosixPath(self.name)

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
    def submit_script_path(self) -> pathlib.Path:
        return self.code_directory / "submit.sh"


def list_capsules(repository_root: pathlib.Path) -> list[pathlib.Path]:
    return sorted(path for path in repository_root.iterdir() if path.is_dir() and CAPSULE_NAME_RE.fullmatch(path.name))


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


# --------------------------------------------------------------------------------------------- #
# The repository's dataset_description.json
# --------------------------------------------------------------------------------------------- #
def read_dataset_description(repository_root: pathlib.Path) -> dict:
    file_path = repository_root / DATASET_DESCRIPTION
    return json.loads(file_path.read_text()) if file_path.exists() else {}


def read_provenance(capsule_directory: pathlib.Path) -> dict:
    """The provenance of a capsule's latest run, from the repository's dataset_description.json."""
    description = read_dataset_description(capsule_directory.parent)
    return description.get(PROVENANCE_KEY, {}).get(capsule_directory.name, {})


def _generated_by(provenances: dict[str, dict]) -> list[dict]:
    """``GeneratedBy``: the orchestration code, then one entry per capsule naming its image."""
    latest = max(provenances.values(), key=lambda provenance: provenance.get("created_at", ""), default={})
    entries = [
        {
            "Name": "DANDI Validations",
            "Description": "Runs dandi validate per Dandiset on GitHub Actions and records each run in a capsule.",
            "Version": latest.get("codebase_version", "unknown"),
            "CodeURL": CODEBASE_URL,
        }
    ]
    for name, provenance in sorted(provenances.items()):
        tag = provenance.get("version", "")
        digest = provenance.get("image_digest")
        image = provenance.get("image", "")
        repository = image.rsplit(":", 1)[0]
        dandi_cli = provenance.get("dandi_cli", {})
        entries.append(
            {
                "Name": "dandi validate",
                "Description": (
                    f"Recorded in {name}/ (branch {tag}). DANDI CLI validation, streaming the content of annexed "
                    f"files through datalad-fuse adapters ({dandi_cli.get('reference', '')}), run with Apptainer "
                    "through datalad containers-run."
                ),
                "Version": tag,
                "CodeURL": dandi_cli.get("code_url", ""),
                "Container": {
                    "Type": "docker",
                    "Tag": tag,
                    "URI": f"docker://{repository}@{digest}" if digest else f"docker://{image}",
                },
            }
        )
    return entries


def _write_provenances(repository_root: pathlib.Path, description: dict, provenances: dict[str, dict]) -> None:
    description["DatasetType"] = "derivative"
    description["GeneratedBy"] = _generated_by(provenances)
    description[PROVENANCE_KEY] = dict(sorted(provenances.items()))
    (repository_root / DATASET_DESCRIPTION).write_text(json.dumps(description, indent=2) + "\n")


def write_capsule_provenance(repository_root: pathlib.Path, name: str, provenance: dict) -> None:
    """Record a capsule's provenance in the repository's single dataset_description.json."""
    description = read_dataset_description(repository_root)
    description.setdefault("Name", f"DANDI Validations: Dandiset {provenance['dandiset_id']}")
    description.setdefault("BIDSVersion", "1.10.1")
    description.setdefault("SourceDatasets", [{"URL": f"{DANDI_ARCHIVE_URL}/dandiset/{provenance['dandiset_id']}"}])
    provenances = dict(description.get(PROVENANCE_KEY, {}))
    provenances[name] = provenance
    _write_provenances(repository_root, description, provenances)


def remove_capsule_provenance(repository_root: pathlib.Path, name: str) -> None:
    description = read_dataset_description(repository_root)
    provenances = dict(description.get(PROVENANCE_KEY, {}))
    if provenances.pop(name, None) is not None:
        _write_provenances(repository_root, description, provenances)


# --------------------------------------------------------------------------------------------- #
# Forming a capsule
# --------------------------------------------------------------------------------------------- #
@dataclasses.dataclass(frozen=True)
class CapsulePlan:
    """What a capsule's next run validates, before anything is written."""

    capsule: Capsule
    pipeline: Pipeline
    parameters: Parameters
    inputs: dict
    dandiset_yaml: bytes
    assets_table: bytes
    skipped_table: bytes | None
    number_of_assets: int
    number_of_skipped_assets: int


@dataclasses.dataclass(frozen=True)
class PrepareResult:
    capsule: Capsule | None
    reason: str


def plan_capsule(
    *,
    repository_root: pathlib.Path,
    dandiset_id: str,
    dandiset_version: str = "draft",
    params_key: str = DEFAULT_PARAMS_KEY,
    pipeline: Pipeline | None = None,
    assets_jsonld: bytes | None = None,
    dandiset_yaml: bytes | None = None,
) -> CapsulePlan | str:
    """
    Read the Dandiset's manifest and work out the capsule of the pinned image.

    Returns the plan, or the reason there is nothing to validate. The inputs that identify a run
    are the Dandiset version, the image's version code, the parameter set and the digest of the
    Dandiset's registered assets and metadata.
    """
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
        return reason + "."

    assets_table = assets_table_bytes(rows)
    inputs = {
        "dandiset_version": dandiset_version,
        "version": pipeline.container_version,
        "params": parameters.identifier,
        "source_digest": source_digest(assets_table=assets_table, dandiset_yaml=dandiset_yaml),
    }
    capsule = Capsule(
        repository_root=repository_root, dandiset_id=dandiset_id, pipeline=pipeline.pipeline, tag=inputs["version"]
    )
    return CapsulePlan(
        capsule=capsule,
        pipeline=pipeline,
        parameters=parameters,
        inputs=inputs,
        dandiset_yaml=dandiset_yaml,
        assets_table=assets_table,
        skipped_table=skipped_table_bytes(skipped) if skipped else None,
        number_of_assets=len(rows),
        number_of_skipped_assets=len(skipped),
    )


def is_up_to_date(plan: CapsulePlan) -> bool:
    """Whether the capsule's latest run succeeded on exactly these inputs."""
    previous = read_provenance(plan.capsule.path)
    return capsule_status(plan.capsule.path) == "successful" and all(
        previous.get(field) == plan.inputs[field] for field in _INPUT_FIELDS
    )


def write_capsule(plan: CapsulePlan, *, now: datetime.datetime | None = None) -> None:
    """
    Replace the capsule's code (and results) with the next run's, and record its provenance.

    Only what a run writes is replaced: the capsule's dataset, image registration and history stay.
    """
    now = now if now is not None else datetime.datetime.now(tz=datetime.timezone.utc)
    capsule, pipeline = plan.capsule, plan.pipeline
    for name in CAPSULE_CONTENTS:
        target = capsule.path / name
        if target.exists():
            for path in target.rglob("*"):
                if path.is_dir():
                    path.chmod(0o755)
            shutil.rmtree(target)

    image_digest = resolve_digest(pipeline.container_image, pipeline.container_version)
    assets_file_name = _write_code(
        capsule=capsule,
        pipeline=pipeline,
        image_digest=image_digest,
        parameters=plan.parameters,
        dandiset_version=plan.inputs["dandiset_version"],
        dandiset_yaml=plan.dandiset_yaml,
        assets_table=plan.assets_table,
        skipped_table=plan.skipped_table,
        now=now,
    )
    provenance = {
        "dandiset_id": capsule.dandiset_id,
        **plan.inputs,
        # The six validator versions of the image (the shape of the archive's
        # `validatedRequiredVersions`, dandi/dandi-archive#2987).
        "validator_versions": parse_version_code(pipeline.container_version),
        "image": pipeline.image_reference,
        "image_digest": image_digest,
        "branch": capsule.tag,
        "pipeline": pipeline.pipeline,
        "params_key": plan.parameters.key,
        "assets": plan.number_of_assets,
        "skipped_assets": plan.number_of_skipped_assets,
        "assets_file": f"code/{assets_file_name}",
        "dandi_cli": {
            "commit": pipeline.dandi_cli_commit,
            "code_url": pipeline.dandi_cli_code_url,
            "reference": pipeline.dandi_cli_reference or pipeline.dandi_cli_repository,
        },
        "codebase": _codebase_version().split("+")[0],
        "codebase_version": _codebase_version(),
        "created_at": now.replace(microsecond=0).isoformat(),
    }
    write_capsule_provenance(capsule.repository_root, capsule.name, provenance)


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
    """Plan the capsule and write it, unless it already validated these inputs (no git involved)."""
    plan = plan_capsule(
        repository_root=repository_root,
        dandiset_id=dandiset_id,
        dandiset_version=dandiset_version,
        params_key=params_key,
        pipeline=pipeline,
        assets_jsonld=assets_jsonld,
        dandiset_yaml=dandiset_yaml,
    )
    if isinstance(plan, str):
        return PrepareResult(capsule=None, reason=plan)
    if not force and is_up_to_date(plan):
        reason = f"{plan.capsule.name} already validated these inputs; nothing to do (use --force to validate again)."
        return PrepareResult(capsule=None, reason=reason)
    write_capsule(plan, now=now)
    return PrepareResult(capsule=plan.capsule, reason=f"Formed {plan.capsule.name} for {plan.number_of_assets} assets.")


def _write_code(
    *,
    capsule: Capsule,
    pipeline: Pipeline,
    image_digest: str | None,
    parameters: Parameters,
    dandiset_version: str,
    dandiset_yaml: bytes,
    assets_table: bytes,
    skipped_table: bytes | None,
    now: datetime.datetime,
) -> str:
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
        capsule_name=capsule.name,
        branch=capsule.tag,
        dandiset_id=capsule.dandiset_id,
        dandiset_version=dandiset_version,
        organization=ORGANIZATION,
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

    marker_name = f"{SUBMITTED_MARKER_PREFIX}{now:%Y+%m+%d}_time-{now:%H+%M+%S}"
    (code_directory / marker_name).write_text(claim_identifier() + "\n")
    return assets_file_path.name
