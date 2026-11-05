"""The pinned pipeline (container image and dandi-cli commit) and its registered parameter sets."""

import dataclasses
import hashlib
import json
import pathlib

PACKAGE_DIRECTORY = pathlib.Path(__file__).parent
PIPELINE_FILE_PATH = PACKAGE_DIRECTORY / "pipeline.json"
PARAMS_DIRECTORY = PACKAGE_DIRECTORY / "params"
TEMPLATES_DIRECTORY = PACKAGE_DIRECTORY / "templates"
CAPSULE_CODE_DIRECTORY = PACKAGE_DIRECTORY / "capsule_code"
REPOSITORY_TEMPLATE_DIRECTORY = PACKAGE_DIRECTORY / "repository_template"


@dataclasses.dataclass(frozen=True)
class Pipeline:
    """What a job runs: the container (by name, image and version) and the dandi-cli inside it."""

    pipeline: str
    container_name: str
    container_image: str
    container_version: str
    dandi_cli_repository: str
    dandi_cli_commit: str
    dandi_cli_reference: str

    @property
    def docker_url(self) -> str:
        return f"docker://{self.container_image}:{self.container_version}"

    @property
    def sif_url(self) -> str:
        """The same image as an Apptainer SIF, published with ORAS by the build workflow."""
        return f"oras://{self.container_image}:sif-{self.container_version}"

    @property
    def bidsy_version(self) -> str:
        """The version with ``-`` replaced by ``+``, as BIDS-style labels in capsule paths require."""
        return self.container_version.replace("-", "+")

    @property
    def dandi_cli_code_url(self) -> str:
        return f"{self.dandi_cli_repository}/tree/{self.dandi_cli_commit}"


def load_pipeline(file_path: pathlib.Path = PIPELINE_FILE_PATH) -> Pipeline:
    content = json.loads(file_path.read_text())
    return Pipeline(
        pipeline=content["pipeline"],
        container_name=content["container"]["name"],
        container_image=content["container"]["image"].lower(),
        container_version=content["container"]["version"],
        dandi_cli_repository=content["dandi_cli"]["repository"],
        dandi_cli_commit=content["dandi_cli"]["commit"],
        dandi_cli_reference=content["dandi_cli"].get("reference", ""),
    )


@dataclasses.dataclass(frozen=True)
class Parameters:
    """A registered parameter set for ``dandi validate``, read from ``params/name-{key}.json``."""

    key: str
    file_path: pathlib.Path
    values: dict

    @property
    def identifier(self) -> str:
        """Short MD5 of the file, so that editing a parameter set forms new jobs."""
        return hashlib.md5(self.file_path.read_bytes()).hexdigest()[:7]


def load_parameters(key: str) -> Parameters:
    file_path = PARAMS_DIRECTORY / f"name-{key}.json"
    if not file_path.exists():
        registered = sorted(path.stem.removeprefix("name-") for path in PARAMS_DIRECTORY.glob("name-*.json"))
        message = f"Parameter set {key!r} is not registered. Registered parameter sets: {registered}."
        raise ValueError(message)
    values = json.loads(file_path.read_text())
    return Parameters(key=key, file_path=file_path, values=values)
