"""
The DANDI Archive's per-Dandiset S3 manifests, turned into what ``datalad addurls`` needs.

This is the strategy at the top of dandi/dandi-cli#1956: the DataLad dataset that is validated
is built from ``assets.jsonld`` so that each file gets its SHA-256 git-annex key and its S3 URL,
without anything being downloaded. The rows are written to a capsule's ``code/assets.tsv`` so
that the run records exactly which content it validated.
"""

import dataclasses
import gzip
import hashlib
import json
import pathlib
import urllib.error
import urllib.request

from ._globals import ASSETS_JSONLD_URL_TEMPLATE, DANDISET_YAML_URL_TEMPLATE

ASSETS_TSV_COLUMNS = ("path", "size", "sha256", "url", "asset_id")
SKIPPED_TSV_COLUMNS = ("path", "asset_id", "reason")

#: A table above this size is gzipped, keeping each committed file well under GitHub's limits.
GZIP_TABLE_ABOVE_BYTES = 25 * 1024 * 1024

_ZARR_ENCODING_FORMATS = {"application/x-zarr", "application/x-ngff-zarr"}
_ZARR_SUFFIXES = (".zarr", ".ngff", ".ome.zarr")


class ManifestNotFoundError(LookupError):
    """The archive has no manifest for this Dandiset version (e.g. embargoed or never populated)."""


@dataclasses.dataclass(frozen=True)
class AssetRow:
    path: str
    size: int
    sha256: str
    url: str
    asset_id: str


@dataclasses.dataclass(frozen=True)
class SkippedAsset:
    path: str
    asset_id: str
    reason: str


def _fetch(url: str, *, timeout: float = 120.0) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "dandi-validations"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as error:
        if error.code in (403, 404):
            message = f"{url} is not available (HTTP {error.code})."
            raise ManifestNotFoundError(message) from error
        raise


def fetch_assets_jsonld(dandiset_id: str, version: str = "draft") -> bytes:
    return _fetch(ASSETS_JSONLD_URL_TEMPLATE.format(dandiset_id=dandiset_id, version=version))


def fetch_dandiset_yaml(dandiset_id: str, version: str = "draft") -> bytes:
    return _fetch(DANDISET_YAML_URL_TEMPLATE.format(dandiset_id=dandiset_id, version=version))


def _select_url(content_urls: list[str]) -> str | None:
    """Prefer the S3 blob URL (as ``{contentUrl[1]}`` does in the PR), falling back to any URL."""
    for url in content_urls:
        if "/blobs/" in url and "amazonaws.com" in url:
            return url
    return content_urls[0] if content_urls else None


def _is_zarr(asset: dict) -> bool:
    if asset.get("encodingFormat") in _ZARR_ENCODING_FORMATS:
        return True
    if any("/zarr/" in url for url in asset.get("contentUrl", [])):
        return True
    return asset.get("path", "").rstrip("/").endswith(_ZARR_SUFFIXES)


def select_assets(assets: list[dict]) -> tuple[list[AssetRow], list[SkippedAsset]]:
    """
    Split a manifest into the assets ``datalad addurls`` can register and the ones it cannot.

    Zarr assets are skipped because ``dandi validate --missing-file-content=stream`` does not stream
    them yet (they would be uninstalled subdatasets), and blobs without a SHA-256 digest (not yet
    computed by the archive) are skipped because their git-annex key cannot be formed.
    """
    rows = []
    skipped = []
    for asset in assets:
        path = asset.get("path", "")
        asset_id = asset.get("identifier", "")
        if _is_zarr(asset):
            skipped.append(SkippedAsset(path=path, asset_id=asset_id, reason="zarr"))
            continue
        sha256 = asset.get("digest", {}).get("dandi:sha2-256")
        if not sha256:
            skipped.append(SkippedAsset(path=path, asset_id=asset_id, reason="no-sha256-digest"))
            continue
        url = _select_url(asset.get("contentUrl", []))
        if url is None:
            skipped.append(SkippedAsset(path=path, asset_id=asset_id, reason="no-content-url"))
            continue
        rows.append(AssetRow(path=path, size=int(asset["contentSize"]), sha256=sha256, url=url, asset_id=asset_id))

    rows.sort(key=lambda row: row.path)
    skipped.sort(key=lambda row: row.path)
    return rows, skipped


def parse_assets_jsonld(content: bytes) -> list[dict]:
    assets = json.loads(content)
    if not isinstance(assets, list):
        message = "Expected assets.jsonld to hold a list of assets."
        raise ValueError(message)
    return assets


def _table_bytes(columns: tuple[str, ...], rows: list) -> bytes:
    lines = ["\t".join(columns)]
    for row in rows:
        values = [str(getattr(row, column)) for column in columns]
        if any("\t" in value or "\n" in value for value in values):
            message = f"Cannot write {values!r} to a TSV: it contains a tab or a newline."
            raise ValueError(message)
        lines.append("\t".join(values))
    return ("\n".join(lines) + "\n").encode("utf-8")


def assets_table_bytes(rows: list[AssetRow]) -> bytes:
    return _table_bytes(ASSETS_TSV_COLUMNS, rows)


def skipped_table_bytes(rows: list[SkippedAsset]) -> bytes:
    return _table_bytes(SKIPPED_TSV_COLUMNS, rows)


def write_table(content: bytes, file_path: pathlib.Path) -> pathlib.Path:
    """Write a TSV, gzipped (with ``.gz`` appended to the name) when it is large."""
    if len(content) > GZIP_TABLE_ABOVE_BYTES:
        file_path = file_path.with_name(file_path.name + ".gz")
        file_path.write_bytes(gzip.compress(content, mtime=0))
    else:
        file_path.write_bytes(content)
    return file_path


def source_digest(*, assets_table: bytes, dandiset_yaml: bytes) -> str:
    """
    SHA-256 over what the validation reads: the registered assets and the Dandiset metadata.

    Metadata-only changes to assets (which change ``assets.jsonld`` but not the files) do not
    change the digest, so they do not form a new job.
    """
    digest = hashlib.sha256()
    digest.update(assets_table)
    digest.update(b"\0")
    digest.update(dandiset_yaml)
    return digest.hexdigest()
