import pytest
import requests

from dandi_validations import assets_table_bytes, fetch_assets_jsonld, parse_assets_jsonld, select_assets, source_digest

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
def test_select_assets(assets_jsonld: bytes) -> None:
    rows, skipped = select_assets(parse_assets_jsonld(assets_jsonld))
    assert [row.path for row in rows] == ["sub-01/sub-01_ses-1.nwb", "sub-02/sub-02_ses-1.nwb"]
    assert rows[0].url.startswith("https://dandiarchive.s3.amazonaws.com/blobs/")
    assert rows[0].sha256 == "c" * 64
    assert rows[0].size == 10
    assert {(row.path, row.reason) for row in skipped} == {
        ("sub-03/sub-03.ome.zarr", "zarr"),
        ("sub-04/sub-04_ses-1.nwb", "no-sha256-digest"),
    }


@pytest.mark.ai_generated
def test_zarr_is_recognized_by_url_or_suffix() -> None:
    by_suffix = make_asset(path="sub-01/x.zarr", encoding_format="application/octet-stream")
    by_url = make_asset(path="sub-01/y", encoding_format="application/octet-stream")
    by_url["contentUrl"] = ["https://dandiarchive.s3.amazonaws.com/zarr/1234/"]
    rows, skipped = select_assets([by_suffix, by_url])
    assert rows == []
    assert [row.reason for row in skipped] == ["zarr", "zarr"]


@pytest.mark.ai_generated
def test_falls_back_to_any_url() -> None:
    asset = make_asset(path="sub-01/x.nwb")
    asset["contentUrl"] = ["https://example.org/x.nwb"]
    rows, _ = select_assets([asset])
    assert rows[0].url == "https://example.org/x.nwb"


@pytest.mark.ai_generated
def test_assets_table_is_what_addurls_reads(assets_jsonld: bytes) -> None:
    rows, _ = select_assets(parse_assets_jsonld(assets_jsonld))
    lines = assets_table_bytes(rows).decode().splitlines()
    assert lines[0] == "path\tsize\tsha256\turl\tasset_id"
    assert lines[1].split("\t")[:3] == ["sub-01/sub-01_ses-1.nwb", "10", "c" * 64]


@pytest.mark.ai_generated
def test_tables_reject_tabs() -> None:
    rows, _ = select_assets([make_asset(path="sub-01/bad\tname.nwb")])
    with pytest.raises(ValueError, match="contains a tab or a newline"):
        assets_table_bytes(rows)


@pytest.mark.ai_generated
@pytest.mark.parametrize(
    ("assets_table", "dandiset_yaml"),
    [(b"x2", b"y"), (b"x", b"y2")],
)
def test_source_digest_depends_on_assets_and_metadata(assets_table: bytes, dandiset_yaml: bytes) -> None:
    digest = source_digest(assets_table=assets_table, dandiset_yaml=dandiset_yaml)
    assert digest != source_digest(assets_table=b"x", dandiset_yaml=b"y")


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
    rows, skipped = select_assets(parse_assets_jsonld(manifest.content))
    assert len(rows) >= 50
    assert skipped == []
    # The version downloaded stays downloadable, whatever the draft becomes.
    assert manifest.url is not None
    assert manifest.url.startswith(
        "https://dandiarchive.s3.amazonaws.com/dandisets/000006/draft/assets.jsonld?versionId=",
    )
    assert requests.get(manifest.url, timeout=120).content == manifest.content
