import pytest

from dandi_validations import _manifest
from dandi_validations._manifest import (
    assets_table_bytes,
    parse_assets_jsonld,
    select_assets,
    source_digest,
)

from .conftest import make_asset


def test_select_assets(assets_jsonld):
    rows, skipped = select_assets(parse_assets_jsonld(assets_jsonld))
    assert [row.path for row in rows] == ["sub-01/sub-01_ses-1.nwb", "sub-02/sub-02_ses-1.nwb"]
    assert rows[0].url.startswith("https://dandiarchive.s3.amazonaws.com/blobs/")
    assert rows[0].sha256 == "c" * 64 and rows[0].size == 10
    assert {(row.path, row.reason) for row in skipped} == {
        ("sub-03/sub-03.ome.zarr", "zarr"),
        ("sub-04/sub-04_ses-1.nwb", "no-sha256-digest"),
    }


def test_zarr_is_recognized_by_url_or_suffix():
    by_suffix = make_asset("sub-01/x.zarr", encoding_format="application/octet-stream")
    by_url = make_asset("sub-01/y", encoding_format="application/octet-stream")
    by_url["contentUrl"] = ["https://dandiarchive.s3.amazonaws.com/zarr/1234/"]
    rows, skipped = select_assets([by_suffix, by_url])
    assert rows == [] and [row.reason for row in skipped] == ["zarr", "zarr"]


def test_falls_back_to_any_url():
    asset = make_asset("sub-01/x.nwb")
    asset["contentUrl"] = ["https://example.org/x.nwb"]
    rows, _ = select_assets([asset])
    assert rows[0].url == "https://example.org/x.nwb"


def test_assets_table_is_what_addurls_reads(assets_jsonld):
    rows, _ = select_assets(parse_assets_jsonld(assets_jsonld))
    lines = assets_table_bytes(rows).decode().splitlines()
    assert lines[0] == "path\tsize\tsha256\turl\tasset_id"
    assert lines[1].split("\t")[:3] == ["sub-01/sub-01_ses-1.nwb", "10", "c" * 64]


def test_tables_reject_tabs():
    asset = make_asset("sub-01/bad\tname.nwb")
    rows, _ = select_assets([asset])
    with pytest.raises(ValueError):
        assets_table_bytes(rows)


def test_source_digest_depends_on_assets_and_metadata():
    digest = source_digest(assets_table=b"x", dandiset_yaml=b"y")
    assert digest != source_digest(assets_table=b"x2", dandiset_yaml=b"y")
    assert digest != source_digest(assets_table=b"x", dandiset_yaml=b"y2")


@pytest.mark.network
def test_fetch_real_manifest():
    rows, skipped = select_assets(parse_assets_jsonld(_manifest.fetch_assets_jsonld("000006")))
    assert len(rows) >= 50 and not skipped
