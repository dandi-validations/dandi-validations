import json
import pathlib
import tomllib

import pytest

from dandi_validations._pipeline import PIPELINE_FILE_PATH, load_pipeline
from dandi_validations._version_code import (
    VALIDATOR_CODES,
    VersionCodeError,
    build_version_code,
    parse_version_code,
    semantic_version,
    versions_from_pixi_lock,
)

CONTAINER_DIRECTORY = pathlib.Path(__file__).parents[1] / "containers" / "dandi-validation"

VERSIONS = {
    "dandi": "0.75.0",
    "dandischema": "0.12.0",
    "nwbinspector": "0.7.1",
    "pynwb": "3.1.0",
    "hdmf": "4.1.0",
    "bids-validator-deno": "2.1.0",
}


def test_build_and_parse_the_example_of_the_convention():
    assert build_version_code(VERSIONS) == "d0.75.0-s0.12.0-n0.7.1-p3.1.0-h4.1.0-b2.1.0"
    assert parse_version_code("d0.75.0-s0.12.0-n0.7.1-p3.1.0-h4.1.0-b2.1.0") == VERSIONS
    assert list(VALIDATOR_CODES.values()) == ["d", "s", "n", "p", "h", "b"]


def test_order_does_not_depend_on_the_mapping():
    assert build_version_code(dict(reversed(VERSIONS.items()))) == build_version_code(VERSIONS)


def test_local_segment_is_dropped():
    assert semantic_version("0.82.0+28.gbec91fa4") == "0.82.0"
    code = build_version_code({**VERSIONS, "dandi": "0.82.0+28.gbec91fa4"})
    assert code.startswith("d0.82.0-s0.12.0-")


@pytest.mark.parametrize("version", ["1.0rc1", "1.0.0rc1", "0.83.0.dev3+g123", "1.0.0.post1", "1.0", "1.0.0.0"])
def test_other_versions_are_rejected(version):
    with pytest.raises(VersionCodeError, match="pynwb"):
        build_version_code({**VERSIONS, "pynwb": version})


def test_missing_and_malformed():
    with pytest.raises(VersionCodeError, match="hdmf"):
        build_version_code({name: version for name, version in VERSIONS.items() if name != "hdmf"})
    for code in ("d0.75.0", "d0.75.0+1-s0.12.0-n0.7.1-p3.1.0-h4.1.0-b2.1.0", "latest"):
        with pytest.raises(VersionCodeError):
            parse_version_code(code)


def test_versions_from_pixi_lock():
    lock = "\n".join(
        [
            "packages:",
            "- conda: https://conda.anaconda.org/conda-forge/linux-64/python-3.13.18-h_0.conda",
            "  sha256: abc",
            "- pypi: git+https://github.com/dandi/dandi-cli?rev=bec91fa#bec91fa",
            "  name: dandi",
            "  version: 0.82.0+28.gbec91fa4",
            "  requires_dist:",
            "  - name: not-a-package",
            "- pypi: https://files.pythonhosted.org/packages/pynwb-4.2.0-py3-none-any.whl",
            "  name: pynwb",
            "  version: 4.2.0",
        ]
    )
    assert versions_from_pixi_lock(lock) == {"dandi": "0.82.0+28.gbec91fa4", "pynwb": "4.2.0"}


def test_pipeline_pins_match_the_locked_image():
    """pipeline.json names the image that containers/dandi-validation builds."""
    pipeline = load_pipeline()
    lock = (CONTAINER_DIRECTORY / "pixi.lock").read_text()
    assert pipeline.container_version == build_version_code(versions_from_pixi_lock(lock))

    manifest = tomllib.loads((CONTAINER_DIRECTORY / "pixi.toml").read_text())
    dandi = manifest["pypi-dependencies"]["dandi"]
    assert dandi["git"] == pipeline.dandi_cli_repository
    assert dandi["rev"] == pipeline.dandi_cli_commit
    assert json.loads(PIPELINE_FILE_PATH.read_text())["container"]["image"].endswith("/dandi-validation")


@pytest.mark.network
def test_resolve_digest_from_ghcr(monkeypatch):
    from dandi_validations._registry import DIGEST_OVERRIDE_VARIABLE, resolve_digest

    monkeypatch.delenv(DIGEST_OVERRIDE_VARIABLE, raising=False)
    # The pixi base image of the Dockerfile, pinned there by this digest.
    dockerfile = (CONTAINER_DIRECTORY / "Dockerfile").read_text()
    reference = next(line.split()[1] for line in dockerfile.splitlines() if line.startswith("FROM ghcr.io/"))
    image_and_tag, digest = reference.split("@")
    image, tag = image_and_tag.rsplit(":", 1)
    assert resolve_digest(image, tag) == digest
    assert resolve_digest("ghcr.io/dandi-validations/no-such-image", "latest") is None
