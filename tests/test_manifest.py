import pytest
import requests

from dandi_validations import fetch_assets_jsonld, parse_assets_jsonld, source_digest

from .conftest import make_asset

MANIFEST_URL = "https://dandiarchive.s3.amazonaws.com/dandisets/000123/draft/assets.jsonld"


class _S3Response:
    """Answer a download as the archive's S3 bucket does."""

    status_code = 200

    def __init__(self, *, headers: dict[str, str]) -> None:
        self.content = b"[]"
        self.headers = headers

    def raise_for_status(self) -> None:
        """Pass, as the download succeeded."""


@pytest.mark.ai_generated
def test_parse_assets_jsonld(assets_jsonld: bytes) -> None:
    assets = parse_assets_jsonld(assets_jsonld)
    assert [asset["path"] for asset in assets] == ["sub-02/sub-02_ses-1.nwb", "sub-01/sub-01_ses-1.nwb"]


@pytest.mark.ai_generated
def test_parse_assets_jsonld_rejects_anything_but_a_list() -> None:
    with pytest.raises(TypeError, match="list of assets"):
        parse_assets_jsonld(b"{}")


@pytest.mark.ai_generated
@pytest.mark.parametrize(
    ("change", "changes_digest"),
    [
        ({"path": "sub-01/renamed.nwb"}, True),
        ({"contentSize": 11}, True),
        ({"digest": {"dandi:sha2-256": "d" * 64}}, True),
        # Metadata that leaves the file as it is.
        ({"encodingFormat": "application/octet-stream"}, False),
        ({"contentUrl": ["https://example.org/elsewhere"]}, False),
    ],
    ids=["path", "size", "content", "encoding format", "URL"],
)
def test_source_digest_depends_on_the_files_only(change: dict, changes_digest: bool) -> None:
    asset = make_asset(path="sub-01/sub-01_ses-1.nwb")
    digest = source_digest(assets=[asset], dandiset_yaml=b"y")
    assert (source_digest(assets=[{**asset, **change}], dandiset_yaml=b"y") != digest) == changes_digest


@pytest.mark.ai_generated
def test_source_digest_depends_on_the_metadata_and_not_the_order() -> None:
    assets = [make_asset(path="sub-01/a.nwb", sha256="a" * 64), make_asset(path="sub-02/b.nwb", sha256="b" * 64)]
    digest = source_digest(assets=assets, dandiset_yaml=b"y")
    assert source_digest(assets=assets[::-1], dandiset_yaml=b"y") == digest
    assert source_digest(assets=assets, dandiset_yaml=b"y2") != digest


@pytest.mark.ai_generated
@pytest.mark.parametrize(
    ("headers", "expected_url"),
    [
        ({"x-amz-version-id": "Ypet.vU_J"}, f"{MANIFEST_URL}?versionId=Ypet.vU_J"),
        # A bucket without versioning has no version to point at.
        ({}, None),
    ],
    ids=["versioned", "unversioned"],
)
def test_fetch_records_the_version_downloaded(
    monkeypatch: pytest.MonkeyPatch,
    headers: dict[str, str],
    expected_url: str | None,
) -> None:
    monkeypatch.setattr(requests, "get", lambda *_, **__: _S3Response(headers=headers))
    manifest = fetch_assets_jsonld(dandiset_id="000123")
    assert manifest.content == b"[]"
    assert manifest.url == expected_url


@pytest.mark.ai_generated
@pytest.mark.network
def test_fetch_real_manifest() -> None:
    manifest = fetch_assets_jsonld(dandiset_id="000006")
    assert len(parse_assets_jsonld(manifest.content)) >= 50
    # The version downloaded stays downloadable, whatever the draft becomes.
    assert manifest.url is not None
    assert manifest.url.startswith(
        "https://dandiarchive.s3.amazonaws.com/dandisets/000006/draft/assets.jsonld?versionId=",
    )
    assert requests.get(manifest.url, timeout=120).content == manifest.content
