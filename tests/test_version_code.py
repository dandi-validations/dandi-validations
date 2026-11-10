import json
import pathlib
import tomllib

import pytest

from dandi_validations import (
    DIGEST_OVERRIDE_VARIABLE,
    PIPELINE_FILE_PATH,
    VALIDATOR_CODES,
    VersionCodeError,
    build_version_code,
    load_pipeline,
    parse_version_code,
    resolve_digest,
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


@pytest.mark.ai_generated
def test_build_and_parse_the_example_of_the_convention() -> None:
    assert build_version_code(VERSIONS) == "d0.75.0-s0.12.0-n0.7.1-p3.1.0-h4.1.0-b2.1.0"
    assert parse_version_code("d0.75.0-s0.12.0-n0.7.1-p3.1.0-h4.1.0-b2.1.0") == VERSIONS
    assert list(VALIDATOR_CODES.values()) == ["d", "s", "n", "p", "h", "b"]


@pytest.mark.ai_generated
def test_order_does_not_depend_on_the_mapping() -> None:
    assert build_version_code(dict(reversed(VERSIONS.items()))) == build_version_code(VERSIONS)


@pytest.mark.ai_generated
def test_local_segment_is_dropped() -> None:
    assert semantic_version("0.82.0+28.gbec91fa4") == "0.82.0"
    code = build_version_code({**VERSIONS, "dandi": "0.82.0+28.gbec91fa4"})
    assert code.startswith("d0.82.0-s0.12.0-")


@pytest.mark.ai_generated
@pytest.mark.parametrize("version", ["1.0rc1", "1.0.0rc1", "0.83.0.dev3+g123", "1.0.0.post1", "1.0", "1.0.0.0"])
def test_other_versions_are_rejected(version: str) -> None:
    with pytest.raises(VersionCodeError, match="pynwb"):
        build_version_code({**VERSIONS, "pynwb": version})


@pytest.mark.ai_generated
def test_missing_version() -> None:
    with pytest.raises(VersionCodeError, match="hdmf"):
        build_version_code({name: version for name, version in VERSIONS.items() if name != "hdmf"})


@pytest.mark.ai_generated
@pytest.mark.parametrize("code", ["d0.75.0", "d0.75.0+1-s0.12.0-n0.7.1-p3.1.0-h4.1.0-b2.1.0", "latest"])
def test_malformed_version_code(code: str) -> None:
    with pytest.raises(VersionCodeError, match="is not a version code"):
        parse_version_code(code)


@pytest.mark.ai_generated
def test_versions_from_pixi_lock() -> None:
    lock = (
        "packages:\n"
        "- conda: https://conda.anaconda.org/conda-forge/linux-64/python-3.13.18-h_0.conda\n"
        "  sha256: abc\n"
        "- pypi: git+https://github.com/dandi/dandi-cli?rev=bec91fa#bec91fa\n"
        "  name: dandi\n"
        "  version: 0.82.0+28.gbec91fa4\n"
        "  requires_dist:\n"
        "  - name: not-a-package\n"
        "- pypi: https://files.pythonhosted.org/packages/pynwb-4.2.0-py3-none-any.whl\n"
        "  name: pynwb\n"
        "  version: 4.2.0\n"
    )
    assert versions_from_pixi_lock(lock) == {"dandi": "0.82.0+28.gbec91fa4", "pynwb": "4.2.0"}


@pytest.mark.ai_generated
def test_pipeline_pins_match_the_locked_image() -> None:
    """Check that pipeline.json names the image that containers/dandi-validation builds."""
    pipeline = load_pipeline()
    lock = (CONTAINER_DIRECTORY / "pixi.lock").read_text()
    assert pipeline.container_version == build_version_code(versions_from_pixi_lock(lock))

    manifest = tomllib.loads((CONTAINER_DIRECTORY / "pixi.toml").read_text())
    dandi = manifest["pypi-dependencies"]["dandi"]
    assert dandi["git"] == pipeline.dandi_cli_repository
    assert dandi["rev"] == pipeline.dandi_cli_commit
    assert json.loads(PIPELINE_FILE_PATH.read_text())["container"]["image"].endswith("/dandi-validation")


@pytest.mark.ai_generated
@pytest.mark.network
def test_resolve_digest_from_ghcr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(DIGEST_OVERRIDE_VARIABLE, raising=False)
    # The pixi base image of the Dockerfile, pinned there by this digest.
    dockerfile = (CONTAINER_DIRECTORY / "Dockerfile").read_text()
    reference = next(line.split()[1] for line in dockerfile.splitlines() if line.startswith("FROM ghcr.io/"))
    image_and_tag, digest = reference.split("@")
    image, tag = image_and_tag.rsplit(":", 1)
    assert resolve_digest(image=image, tag=tag) == digest
    assert resolve_digest(image="ghcr.io/dandi-validations/no-such-image", tag="latest") is None
