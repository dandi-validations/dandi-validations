"""
Turn the DANDI Archive's per-Dandiset S3 manifests into what ``datalad addurls`` needs.

This is the strategy at the top of dandi/dandi-cli#1956. The DataLad dataset that is validated is
built from ``assets.jsonld``, so each file gets its SHA-256 git-annex key and its S3 URL without
anything being downloaded. The rows are written to a capsule's ``code/assets.tsv``, so the run
records exactly which content it validated.
"""

import dataclasses
import hashlib
import json

import requests

from ._globals import ASSETS_JSONLD_URL_TEMPLATE, DANDISET_YAML_URL_TEMPLATE

ASSETS_TSV_COLUMNS = ("path", "size", "sha256", "url", "asset_id")
SKIPPED_TSV_COLUMNS = ("path", "asset_id", "reason")

_ZARR_ENCODING_FORMATS = {"application/x-zarr", "application/x-ngff-zarr"}
_ZARR_SUFFIXES = (".zarr", ".ngff", ".ome.zarr")


class ManifestNotFoundError(LookupError):
    """Signal that the archive has no manifest for a Dandiset version (e.g., embargoed or empty)."""


@dataclasses.dataclass(frozen=True)
class AssetRow:
    """
    Describe an asset that ``datalad addurls`` registers.

    Attributes
    ----------
    path : str
        The asset's path in the Dandiset.
    size : int
        The size of its content, in bytes.
    sha256 : str
        The SHA-256 digest of its content.
    url : str
        The URL to stream its content from.
    asset_id : str
        The asset's identifier in the archive.

    """

    path: str
    size: int
    sha256: str
    url: str
    asset_id: str


@dataclasses.dataclass(frozen=True)
class SkippedAsset:
    """
    Describe an asset that is not registered, and why.

    Attributes
    ----------
    path : str
        The asset's path in the Dandiset.
    asset_id : str
        The asset's identifier in the archive.
    reason : str
        Why it is skipped (``zarr``, ``no-sha256-digest`` or ``no-content-url``).

    """

    path: str
    asset_id: str
    reason: str


def _fetch(*, url: str, timeout: float = 120.0) -> bytes:
    response = requests.get(url, headers={"User-Agent": "dandi-validations"}, timeout=timeout)
    if response.status_code in (403, 404):
        message = f"{url} is not available (HTTP {response.status_code})."
        raise ManifestNotFoundError(message)
    response.raise_for_status()
    return response.content


def fetch_assets_jsonld(*, dandiset_id: str, version: str = "draft") -> bytes:
    """
    Download the ``assets.jsonld`` manifest of a Dandiset version.

    Parameters
    ----------
    dandiset_id : str
        The six-digit identifier of the Dandiset.
    version : str, default="draft"
        The Dandiset version.

    Returns
    -------
    bytes
        The manifest.

    """
    return _fetch(url=ASSETS_JSONLD_URL_TEMPLATE.format(dandiset_id=dandiset_id, version=version))


def fetch_dandiset_yaml(*, dandiset_id: str, version: str = "draft") -> bytes:
    """
    Download the ``dandiset.yaml`` metadata of a Dandiset version.

    Parameters
    ----------
    dandiset_id : str
        The six-digit identifier of the Dandiset.
    version : str, default="draft"
        The Dandiset version.

    Returns
    -------
    bytes
        The metadata.

    """
    return _fetch(url=DANDISET_YAML_URL_TEMPLATE.format(dandiset_id=dandiset_id, version=version))


def _select_url(content_urls: list[str], /) -> str | None:
    """Prefer the S3 blob URL (as ``{contentUrl[1]}`` does in dandi/dandi-cli#1956), falling back to any URL."""
    for url in content_urls:
        if "/blobs/" in url and "amazonaws.com" in url:
            return url
    return content_urls[0] if content_urls else None


def _is_zarr(asset: dict, /) -> bool:
    if asset.get("encodingFormat") in _ZARR_ENCODING_FORMATS:
        return True
    if any("/zarr/" in url for url in asset.get("contentUrl", [])):
        return True
    return asset.get("path", "").rstrip("/").endswith(_ZARR_SUFFIXES)


def select_assets(assets: list[dict], /) -> tuple[list[AssetRow], list[SkippedAsset]]:
    """
    Split a manifest into the assets ``datalad addurls`` can register and the ones it cannot.

    Zarr assets are skipped because ``dandi validate --missing-file-content=stream`` does not
    stream them yet. Blobs whose SHA-256 digest the archive has not computed yet are skipped too,
    because their git-annex key cannot be formed.

    Parameters
    ----------
    assets : list[dict]
        The assets of the manifest.

    Returns
    -------
    tuple[list[AssetRow], list[SkippedAsset]]
        The registered assets and the skipped ones, each sorted by path.

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


def parse_assets_jsonld(content: bytes, /) -> list[dict]:
    """
    Read the assets of an ``assets.jsonld`` manifest.

    Parameters
    ----------
    content : bytes
        The manifest.

    Returns
    -------
    list[dict]
        Its assets.

    Raises
    ------
    TypeError
        If the manifest does not hold a list.

    """
    assets = json.loads(content)
    if not isinstance(assets, list):
        message = "Expected assets.jsonld to hold a list of assets."
        raise TypeError(message)
    return assets


def _table_bytes(*, columns: tuple[str, ...], rows: list[AssetRow] | list[SkippedAsset]) -> bytes:
    lines = ["\t".join(columns)]
    for row in rows:
        values = [str(getattr(row, column)) for column in columns]
        if any("\t" in value or "\n" in value for value in values):
            message = f"Cannot write {values!r} to a TSV: it contains a tab or a newline."
            raise ValueError(message)
        lines.append("\t".join(values))
    return ("\n".join(lines) + "\n").encode("utf-8")


def assets_table_bytes(rows: list[AssetRow], /) -> bytes:
    """
    Write the registered assets as the TSV that ``datalad addurls`` reads.

    Parameters
    ----------
    rows : list[AssetRow]
        The registered assets.

    Returns
    -------
    bytes
        The TSV, with a header.

    """
    return _table_bytes(columns=ASSETS_TSV_COLUMNS, rows=rows)


def skipped_table_bytes(rows: list[SkippedAsset], /) -> bytes:
    """
    Write the skipped assets as a TSV.

    Parameters
    ----------
    rows : list[SkippedAsset]
        The skipped assets.

    Returns
    -------
    bytes
        The TSV, with a header.

    """
    return _table_bytes(columns=SKIPPED_TSV_COLUMNS, rows=rows)


def source_digest(*, assets_table: bytes, dandiset_yaml: bytes) -> str:
    """
    Digest what the validation reads, which is the registered assets and the Dandiset metadata.

    Metadata-only changes to assets change ``assets.jsonld`` but not the files. They do not
    change the digest, so they do not form a new job.

    Parameters
    ----------
    assets_table : bytes
        The TSV of the registered assets.
    dandiset_yaml : bytes
        The Dandiset's metadata.

    Returns
    -------
    str
        The hexadecimal SHA-256 digest.

    """
    digest = hashlib.sha256()
    digest.update(assets_table)
    digest.update(b"\0")
    digest.update(dandiset_yaml)
    return digest.hexdigest()
