import json
import pathlib
import subprocess

import pytest

DANDISET_YAML = b"identifier: DANDI:000123\nname: A test Dandiset\n"


def make_asset(
    *,
    path: str,
    sha256: str | None = "a" * 64,
    size: int = 10,
    encoding_format: str = "application/x-nwb",
) -> dict:
    """Describe an asset as the archive's ``assets.jsonld`` does."""
    identifier = f"id-{path.replace('/', '-')}"
    digest = {"dandi:dandi-etag": "etag-1"}
    if sha256 is not None:
        digest["dandi:sha2-256"] = sha256
    return {
        "path": path,
        "identifier": identifier,
        "contentSize": size,
        "encodingFormat": encoding_format,
        "contentUrl": [
            f"https://api.dandiarchive.org/api/assets/{identifier}/download/",
            f"https://dandiarchive.s3.amazonaws.com/blobs/abc/def/{identifier}",
        ],
        "digest": digest,
    }


@pytest.fixture(autouse=True)
def git_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give the commits made by the code under test an identity, which CI runners lack."""
    for variable, value in {
        "GIT_AUTHOR_NAME": "dandi-validations tests",
        "GIT_AUTHOR_EMAIL": "tests@example.org",
        "GIT_COMMITTER_NAME": "dandi-validations tests",
        "GIT_COMMITTER_EMAIL": "tests@example.org",
    }.items():
        monkeypatch.setenv(variable, value)


@pytest.fixture(autouse=True)
def offline_image_digest(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep capsule preparation from asking GHCR for the image's digest."""
    monkeypatch.setenv("DANDI_VALIDATIONS_IMAGE_DIGEST", "sha256:" + "0" * 64)


@pytest.fixture
def assets_jsonld() -> bytes:
    """Describe four assets, two of which can be streamed."""
    assets = [
        make_asset(path="sub-02/sub-02_ses-1.nwb", sha256="b" * 64),
        make_asset(path="sub-01/sub-01_ses-1.nwb", sha256="c" * 64),
        make_asset(path="sub-03/sub-03.ome.zarr", sha256=None, encoding_format="application/x-zarr"),
        make_asset(path="sub-04/sub-04_ses-1.nwb", sha256=None),
    ]
    return json.dumps(assets).encode()


@pytest.fixture
def dandiset_yaml() -> bytes:
    """Describe the Dandiset."""
    return DANDISET_YAML


@pytest.fixture
def git_repository(tmp_path: pathlib.Path) -> pathlib.Path:
    """Start an empty git repository."""
    root = tmp_path / "000123"
    root.mkdir()
    subprocess.run(["git", "init", "--quiet", "-b", "main", str(root)], check=True)
    return root
