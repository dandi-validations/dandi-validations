"""
Form the capsules of a Dandiset repository, one per image tag.

Each Dandiset repository is a standalone (type 2) BIDS derivative dataset of its Dandiset. Its
``main`` branch holds its ``dataset_description.json`` and one submodule per capsule::

    https://github.com/dandi-validations/{dandiset_id}/   (main)
    ├── dataset_description.json  ← the derivative dataset that collects the capsules
    └── pipeline-dandi+validate_tag-{tag}/  ← submodule, this repository's branch {tag}
        ├── dataset_description.json  ← a study dataset with the provenance of its latest run
        ├── code/  ← the exact code and inputs of its latest run
        ├── logs/  ← all runtime records of that run, for success or failure
        ├── derivatives/  ← the validation results (only for a completed run)
        ├── envs/  ← the image's registration
        └── sourcedata/  ← the Dandiset as validated: its manifest, as downloaded from S3, and
                           the dataset built from it, each file annexed with its S3 URL

``{tag}`` is the dandi-validation image's tag (its validator version code), followed by ``-v`` and
the version of dandi-validations that formed the capsule. Each capsule is a DataLad dataset on the
branch named by that tag. It holds the latest validation of the Dandiset with that image and that
version of this code. A new run with both unchanged replaces its contents in a new commit on the
branch, so earlier runs remain in the branch's history, each with its ``datalad run`` record.

A capsule's lifecycle status is read from what it contains, as DANDI Compute does. It is
``pending`` with code only, ``stalled`` when submitted but nothing was logged, ``failed`` with logs
but no results, and ``successful`` with results under ``derivatives/``.
"""

import dataclasses
import datetime
import json
import os
import pathlib
import re
import shutil
import socket
import subprocess

from ._globals import CODEBASE_URL, DANDI_ARCHIVE_URL, DEFAULT_PARAMS_KEY, ORGANIZATION, PROVENANCE_KEY
from ._manifest import (
    AssetRow,
    ManifestFile,
    assets_table_bytes,
    fetch_assets_jsonld,
    fetch_dandiset_yaml,
    parse_assets_jsonld,
    select_assets,
    skipped_table_bytes,
    source_digest,
)
from ._pipeline import (
    CAPSULE_CODE_DIRECTORY,
    TEMPLATES_DIRECTORY,
    ParameterSet,
    Pipeline,
    load_parameters,
    load_pipeline,
)
from ._registry import resolve_digest
from ._templates import render_template
from ._version_code import parse_version_code

SOURCEDATA = "sourcedata"
CAPSULE_CONTENTS = ("code", "logs", "derivatives", "intermediate", SOURCEDATA)
SUBMITTED_MARKER_PREFIX = "submitted_date-"
# Beside the Dandiset's files, where dandi validate does not look, as in DANDI's own DataLad mirrors.
MANIFEST_PATH = ".dandi/assets.jsonld"
DANDISET_YAML = "dandiset.yaml"
DATASET_DESCRIPTION = "dataset_description.json"
CAPSULE_NAME_RE = re.compile(r"pipeline-(?P<pipeline>[A-Za-z0-9+]+)_tag-(?P<tag>[A-Za-z0-9_.+-]+)")

# A successful capsule whose latest run matches all of these is not run again.
_INPUT_FIELDS = ("dandiset_version", "version", "params", "source_digest")


def capsule_name(*, pipeline: str, tag: str) -> str:
    """
    Name the directory of a capsule.

    Parameters
    ----------
    pipeline : str
        The pipeline's name.
    tag : str
        The image's tag.

    Returns
    -------
    str
        The name, ``pipeline-{pipeline}_tag-{tag}``.

    """
    return f"pipeline-{pipeline}_tag-{tag}"


@dataclasses.dataclass(frozen=True)
class Capsule:
    """
    Locate a capsule in a clone of a Dandiset repository.

    Attributes
    ----------
    repository_root : pathlib.Path
        The root of the clone of the repository's main.
    dandiset_id : str
        The six-digit identifier of the Dandiset.
    pipeline : str
        The pipeline's name.
    tag : str
        The image's tag, then ``-v`` and the version of dandi-validations. It also names the capsule's branch.

    """

    repository_root: pathlib.Path
    dandiset_id: str
    pipeline: str
    tag: str

    @property
    def name(self) -> str:
        """
        Name the capsule's directory, which is also the path of its submodule in main.

        Returns
        -------
        str
            The name.

        """
        return capsule_name(pipeline=self.pipeline, tag=self.tag)

    @property
    def path(self) -> pathlib.Path:
        """
        Locate the capsule's worktree.

        Returns
        -------
        pathlib.Path
            The capsule's directory in the clone.

        """
        return self.repository_root / self.name

    @property
    def code_directory(self) -> pathlib.Path:
        """
        Locate the capsule's ``code/`` directory.

        Returns
        -------
        pathlib.Path
            The directory.

        """
        return self.path / "code"

    @property
    def logs_directory(self) -> pathlib.Path:
        """
        Locate the capsule's ``logs/`` directory.

        Returns
        -------
        pathlib.Path
            The directory.

        """
        return self.path / "logs"

    @property
    def derivatives_directory(self) -> pathlib.Path:
        """
        Locate the capsule's ``derivatives/`` directory.

        Returns
        -------
        pathlib.Path
            The directory.

        """
        return self.path / "derivatives"

    @property
    def sourcedata_directory(self) -> pathlib.Path:
        """
        Locate the capsule's ``sourcedata/`` directory, which holds the Dandiset as validated.

        Returns
        -------
        pathlib.Path
            The directory.

        """
        return self.path / SOURCEDATA

    @property
    def manifest_path(self) -> pathlib.Path:
        """
        Locate the Dandiset's ``assets.jsonld`` manifest in the capsule's ``sourcedata/``.

        Returns
        -------
        pathlib.Path
            The manifest.

        """
        return self.sourcedata_directory / MANIFEST_PATH

    @property
    def submit_script_path(self) -> pathlib.Path:
        """
        Locate the capsule's ``submit.sh``.

        Returns
        -------
        pathlib.Path
            The script.

        """
        return self.code_directory / "submit.sh"


def list_capsules(repository_root: pathlib.Path, /) -> list[pathlib.Path]:
    """
    List the capsule directories of a clone.

    Parameters
    ----------
    repository_root : pathlib.Path
        The root of the clone.

    Returns
    -------
    list[pathlib.Path]
        The capsule directories, sorted.

    """
    return sorted(path for path in repository_root.iterdir() if path.is_dir() and CAPSULE_NAME_RE.fullmatch(path.name))


def _has_files(directory: pathlib.Path, /) -> bool:
    return directory.is_dir() and any(path.is_file() or path.is_symlink() for path in directory.rglob("*"))


def capsule_status(capsule_directory: pathlib.Path, /) -> str:
    """
    Read a capsule's lifecycle status from what it contains.

    Parameters
    ----------
    capsule_directory : pathlib.Path
        The capsule's directory.

    Returns
    -------
    str
        One of ``pending``, ``stalled``, ``failed``, ``successful`` or ``unknown``.

    """
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


def _codebase_version(version: str, /) -> str:
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


def _claim_identifier() -> str:
    if os.environ.get("GITHUB_RUN_ID"):
        server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
        repository = os.environ.get("GITHUB_REPOSITORY", "")
        run_id = os.environ["GITHUB_RUN_ID"]
        attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "1")
        return f"{server}/{repository}/actions/runs/{run_id}/attempts/{attempt}"
    return f"local:{socket.gethostname()}:{os.getpid()}"


def read_dataset_description(directory: pathlib.Path, /) -> dict:
    """
    Read the ``dataset_description.json`` of a dataset.

    Parameters
    ----------
    directory : pathlib.Path
        The dataset's directory.

    Returns
    -------
    dict
        Its content, or an empty dictionary when it has none.

    """
    file_path = directory / DATASET_DESCRIPTION
    return json.loads(file_path.read_text()) if file_path.exists() else {}


def read_provenance(capsule_directory: pathlib.Path, /) -> dict:
    """
    Read the provenance of a capsule's latest run from its ``dataset_description.json``.

    Parameters
    ----------
    capsule_directory : pathlib.Path
        The capsule's directory.

    Returns
    -------
    dict
        The provenance, or an empty dictionary when the capsule has none.

    """
    return read_dataset_description(capsule_directory).get(PROVENANCE_KEY, {})


def capsule_dataset_description(*, capsule: Capsule, pipeline: Pipeline, provenance: dict) -> dict:
    """
    Describe a capsule as a BIDS study dataset of its latest run.

    A study dataset holds the raw data it was derived from under ``sourcedata/`` and the results
    under ``derivatives/``. Here the raw data is the Dandiset, as the archive's S3 manifest that
    the run validated describes it.

    Parameters
    ----------
    capsule : Capsule
        The capsule.
    pipeline : Pipeline
        The pipeline of its latest run.
    provenance : dict
        The provenance of its latest run.

    Returns
    -------
    dict
        The content of the capsule's ``dataset_description.json``.

    """
    digest = provenance["image_digest"]
    source_datasets = [
        {
            "URL": f"{DANDI_ARCHIVE_URL}/dandiset/{capsule.dandiset_id}/{provenance['dandiset_version']}",
            "Version": provenance["dandiset_version"],
        },
    ]
    return {
        "Name": f"DANDI Validations: dandi validate of Dandiset {capsule.dandiset_id} with {pipeline.image_reference}",
        "BIDSVersion": "1.10.1",
        "DatasetType": "study",
        "GeneratedBy": [
            {
                "Name": "dandi validate",
                "Description": (
                    "DANDI CLI validation, streaming the content of annexed files through datalad-fuse "
                    f"adapters ({provenance['dandi_cli']['reference']})."
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
                "CodeURL": f"{pipeline.image_reference}@{digest}" if digest else pipeline.image_reference,
                "Container": {
                    "Type": "docker",
                    "Tag": pipeline.container_version,
                    "URI": pipeline.docker_url(digest),
                },
            },
            {
                "Name": "DANDI Validations: Code",
                "Description": "Orchestration of validation jobs on GitHub Actions.",
                "Version": provenance["codebase_version"],
                "CodeURL": CODEBASE_URL,
            },
        ],
        "SourceDatasets": source_datasets,
        PROVENANCE_KEY: provenance,
    }


@dataclasses.dataclass(frozen=True)
class CapsulePlan:
    """
    Describe what a capsule's next run validates, before anything is written.

    Attributes
    ----------
    capsule : Capsule
        The capsule.
    pipeline : Pipeline
        The pipeline to run.
    parameters : ParameterSet
        The parameter set to run with.
    inputs : dict
        The inputs that identify the run.
    assets_jsonld : ManifestFile
        The Dandiset's manifest.
    dandiset_yaml : ManifestFile
        The Dandiset's metadata.
    assets : list[AssetRow]
        The assets to register, which are those validated.
    assets_table : bytes
        The TSV of the assets to register.
    skipped_table : bytes or None
        The TSV of the skipped assets, if any.
    number_of_assets : int
        How many assets are registered.
    number_of_skipped_assets : int
        How many assets are skipped.

    """

    capsule: Capsule
    pipeline: Pipeline
    parameters: ParameterSet
    inputs: dict
    assets_jsonld: ManifestFile
    dandiset_yaml: ManifestFile
    assets: list[AssetRow]
    assets_table: bytes
    skipped_table: bytes | None
    number_of_assets: int
    number_of_skipped_assets: int


@dataclasses.dataclass(frozen=True)
class PrepareResult:
    """
    Report what preparing a capsule did.

    Attributes
    ----------
    capsule : Capsule or None
        The capsule that was written, if any.
    reason : str
        What was done, or why nothing was.

    """

    capsule: Capsule | None
    reason: str


def plan_capsule(  # noqa: PLR0913
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
    Read a Dandiset's manifest and work out the capsule of the pinned image and this code's version.

    The inputs that identify a run are the Dandiset version, the image's version code, the
    parameter set, and the digest of the Dandiset's registered assets and metadata.

    Parameters
    ----------
    repository_root : pathlib.Path
        The root of the clone of the repository's main.
    dandiset_id : str
        The six-digit identifier of the Dandiset.
    dandiset_version : str, default="draft"
        The Dandiset version.
    params_key : str, default="default"
        The registered parameter set.
    pipeline : Pipeline or None, default=None
        The pipeline, if not the pinned one.
    assets_jsonld : bytes or None, default=None
        The manifest, if not the archive's current one.
    dandiset_yaml : bytes or None, default=None
        The metadata, if not the archive's current one.

    Returns
    -------
    CapsulePlan or str
        The plan, or the reason there is nothing to validate.

    """
    pipeline = pipeline if pipeline is not None else load_pipeline()
    parameters = load_parameters(params_key)
    manifest = (
        fetch_assets_jsonld(dandiset_id=dandiset_id, version=dandiset_version)
        if assets_jsonld is None
        else ManifestFile(content=assets_jsonld)
    )
    metadata = (
        fetch_dandiset_yaml(dandiset_id=dandiset_id, version=dandiset_version)
        if dandiset_yaml is None
        else ManifestFile(content=dandiset_yaml)
    )

    rows, skipped = select_assets(parse_assets_jsonld(manifest.content))
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
        "source_digest": source_digest(assets_table=assets_table, dandiset_yaml=metadata.content),
    }
    capsule = Capsule(
        repository_root=repository_root,
        dandiset_id=dandiset_id,
        pipeline=pipeline.pipeline,
        tag=pipeline.tag,
    )
    return CapsulePlan(
        capsule=capsule,
        pipeline=pipeline,
        parameters=parameters,
        inputs=inputs,
        assets_jsonld=manifest,
        dandiset_yaml=metadata,
        assets=rows,
        assets_table=assets_table,
        skipped_table=skipped_table_bytes(skipped) if skipped else None,
        number_of_assets=len(rows),
        number_of_skipped_assets=len(skipped),
    )


def is_up_to_date(plan: CapsulePlan, /) -> bool:
    """
    Check whether a capsule's latest run succeeded on exactly the planned inputs.

    Parameters
    ----------
    plan : CapsulePlan
        The plan of the capsule's next run.

    Returns
    -------
    bool
        Whether the run can be skipped.

    """
    previous = read_provenance(plan.capsule.path)
    return capsule_status(plan.capsule.path) == "successful" and all(
        previous.get(field) == plan.inputs[field] for field in _INPUT_FIELDS
    )


def write_capsule(*, plan: CapsulePlan, now: datetime.datetime | None = None) -> None:
    """
    Replace a capsule's code, source data and results with the next run's, and record its provenance.

    Only what a run writes is replaced. The capsule's dataset, image registration and history stay.
    The Dandiset's manifest and metadata are written to ``sourcedata/`` as they were downloaded.
    Annexing them, and registering the Dandiset's files beside them, needs git-annex (see
    ``annex_sourcedata``).

    Parameters
    ----------
    plan : CapsulePlan
        The plan of the run.
    now : datetime.datetime or None, default=None
        When the run is formed, if not now.

    """
    now = now if now is not None else datetime.datetime.now(tz=datetime.UTC)
    capsule, pipeline = plan.capsule, plan.pipeline
    for name in CAPSULE_CONTENTS:
        target = capsule.path / name
        if target.exists():
            # git-annex leaves directories read-only.
            for path in target.rglob("*"):
                if path.is_dir():
                    path.chmod(0o755)
            shutil.rmtree(target)

    image_digest = resolve_digest(image=pipeline.container_image, tag=pipeline.container_version)
    sourcedata = _write_sourcedata(capsule=capsule, assets_jsonld=plan.assets_jsonld, dandiset_yaml=plan.dandiset_yaml)
    assets_file_name = _write_code(
        capsule=capsule,
        pipeline=pipeline,
        image_digest=image_digest,
        parameters=plan.parameters,
        dandiset_version=plan.inputs["dandiset_version"],
        assets_table=plan.assets_table,
        skipped_table=plan.skipped_table,
        now=now,
    )
    codebase_version = _codebase_version(pipeline.codebase_version)
    provenance = {
        "dandiset_id": capsule.dandiset_id,
        **plan.inputs,
        # The shape of the archive's `validatedRequiredVersions` (dandi/dandi-archive#2987).
        "validator_versions": parse_version_code(pipeline.container_version),
        "image": pipeline.image_reference,
        "image_digest": image_digest,
        "branch": capsule.tag,
        "sourcedata": sourcedata,
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
        "codebase": codebase_version.split("+")[0],
        "codebase_version": codebase_version,
        "created_at": now.replace(microsecond=0).isoformat(),
    }
    description = capsule_dataset_description(capsule=capsule, pipeline=pipeline, provenance=provenance)
    (capsule.path / DATASET_DESCRIPTION).write_text(json.dumps(description, indent=2) + "\n")


def prepare_capsule(  # noqa: PLR0913
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
    Plan a capsule and write it, unless it already validated these inputs.

    No git is involved.

    Parameters
    ----------
    repository_root : pathlib.Path
        The root of the clone of the repository's main.
    dandiset_id : str
        The six-digit identifier of the Dandiset.
    dandiset_version : str, default="draft"
        The Dandiset version.
    params_key : str, default="default"
        The registered parameter set.
    force : bool, default=False
        Whether to write the capsule even if it already validated these inputs.
    now : datetime.datetime or None, default=None
        When the run is formed, if not now.
    pipeline : Pipeline or None, default=None
        The pipeline, if not the pinned one.
    assets_jsonld : bytes or None, default=None
        The manifest, if not the archive's current one.
    dandiset_yaml : bytes or None, default=None
        The metadata, if not the archive's current one.

    Returns
    -------
    PrepareResult
        The capsule that was written, if any, and what was done.

    """
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
    write_capsule(plan=plan, now=now)
    return PrepareResult(capsule=plan.capsule, reason=f"Formed {plan.capsule.name} for {plan.number_of_assets} assets.")


def _write_sourcedata(*, capsule: Capsule, assets_jsonld: ManifestFile, dandiset_yaml: ManifestFile) -> dict:
    capsule.manifest_path.parent.mkdir(parents=True)
    capsule.manifest_path.write_bytes(assets_jsonld.content)
    (capsule.sourcedata_directory / DANDISET_YAML).write_bytes(dandiset_yaml.content)
    return {
        "path": SOURCEDATA,
        "manifest": f"{SOURCEDATA}/{MANIFEST_PATH}",
        "manifest_url": assets_jsonld.url,
        "dandiset_yaml": f"{SOURCEDATA}/{DANDISET_YAML}",
        "dandiset_yaml_url": dandiset_yaml.url,
    }


def _write_code(  # noqa: PLR0913
    *,
    capsule: Capsule,
    pipeline: Pipeline,
    image_digest: str | None,
    parameters: ParameterSet,
    dandiset_version: str,
    assets_table: bytes,
    skipped_table: bytes | None,
    now: datetime.datetime,
) -> str:
    code_directory = capsule.code_directory
    code_directory.mkdir(parents=True)

    assets_file_path = code_directory / "assets.tsv"
    assets_file_path.write_bytes(assets_table)
    if skipped_table is not None:
        (code_directory / "skipped_assets.tsv").write_bytes(skipped_table)
    params_file_path = code_directory / parameters.file_path.name
    shutil.copyfile(parameters.file_path, params_file_path)
    shutil.copyfile(CAPSULE_CODE_DIRECTORY / "run.sh", code_directory / "run.sh")

    submit_script = render_template(
        template=(TEMPLATES_DIRECTORY / "submit.sh").read_text(),
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
        sif_release_url=pipeline.sif_release_url,
        params_file_name=params_file_path.name,
        assets_file_name=assets_file_path.name,
    )
    capsule.submit_script_path.write_text(submit_script)
    for name in ("submit.sh", "run.sh"):
        (code_directory / name).chmod(0o755)

    marker_name = f"{SUBMITTED_MARKER_PREFIX}{now:%Y+%m+%d}_time-{now:%H+%M+%S}"
    (code_directory / marker_name).write_text(_claim_identifier() + "\n")
    return assets_file_path.name
