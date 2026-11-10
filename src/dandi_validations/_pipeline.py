"""Read the pinned pipeline (container image and dandi-cli commit) and its registered parameter sets."""

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
    """
    Describe what a job runs: the container and the dandi-cli inside it.

    Attributes
    ----------
    pipeline : str
        The pipeline's name, as in the capsule's name (``dandi+validate``).
    container_name : str
        The container's name in DataLad (``dandi-validation``).
    container_image : str
        The image, with its registry.
    container_version : str
        The image's tag, which is its validator version code.
    dandi_cli_repository : str
        The URL of dandi-cli's repository.
    dandi_cli_commit : str
        The dandi-cli commit installed in the image.
    dandi_cli_reference : str
        Where that commit comes from (e.g., a pull request).

    """

    pipeline: str
    container_name: str
    container_image: str
    container_version: str
    dandi_cli_repository: str
    dandi_cli_commit: str
    dandi_cli_reference: str

    @property
    def image_reference(self) -> str:
        """
        Name the image by its tag.

        Returns
        -------
        str
            The image's reference, ``<image>:<tag>``.

        """
        return f"{self.container_image}:{self.container_version}"

    @property
    def sif_url(self) -> str:
        """
        Locate the image as an Apptainer SIF, which the build workflow publishes with ORAS.

        Returns
        -------
        str
            The ``oras://`` URL of the SIF.

        """
        return f"oras://{self.container_image}:sif-{self.container_version}"

    @property
    def dandi_cli_code_url(self) -> str:
        """
        Locate the dandi-cli code installed in the image.

        Returns
        -------
        str
            The URL of the commit's tree on GitHub.

        """
        return f"{self.dandi_cli_repository}/tree/{self.dandi_cli_commit}"

    def docker_url(self, digest: str | None = None, /) -> str:
        """
        Locate the OCI image, by digest when it is known.

        Parameters
        ----------
        digest : str or None, default=None
            The image's digest.

        Returns
        -------
        str
            The ``docker://`` URL of the image.

        """
        if digest:
            return f"docker://{self.container_image}@{digest}"
        return f"docker://{self.image_reference}"


def load_pipeline(file_path: pathlib.Path = PIPELINE_FILE_PATH, /) -> Pipeline:
    """
    Read the pinned pipeline.

    Parameters
    ----------
    file_path : pathlib.Path, default=PIPELINE_FILE_PATH
        The pipeline's JSON file.

    Returns
    -------
    Pipeline
        The pipeline.

    """
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
class ParameterSet:
    """
    Describe a registered parameter set for ``dandi validate``.

    Attributes
    ----------
    key : str
        The name of the set, as in its file ``params/name-{key}.json``.
    file_path : pathlib.Path
        The set's file.
    values : dict
        The parameters.

    """

    key: str
    file_path: pathlib.Path
    values: dict

    @property
    def identifier(self) -> str:
        """
        Identify the content of the set, so that editing a parameter set forms new jobs.

        Returns
        -------
        str
            The first seven hexadecimal digits of the file's MD5.

        """
        return hashlib.md5(self.file_path.read_bytes(), usedforsecurity=False).hexdigest()[:7]


def load_parameters(key: str, /) -> ParameterSet:
    """
    Read a registered parameter set.

    Parameters
    ----------
    key : str
        The name of the set.

    Returns
    -------
    ParameterSet
        The parameter set.

    Raises
    ------
    ValueError
        If no parameter set of that name is registered.

    """
    file_path = PARAMS_DIRECTORY / f"name-{key}.json"
    if not file_path.exists():
        registered = sorted(path.stem.removeprefix("name-") for path in PARAMS_DIRECTORY.glob("name-*.json"))
        message = f"Parameter set {key!r} is not registered. Registered parameter sets: {registered}."
        raise ValueError(message)
    return ParameterSet(key=key, file_path=file_path, values=json.loads(file_path.read_text()))
