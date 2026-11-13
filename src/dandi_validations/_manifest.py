"""
Download the DANDI Archive's per-Dandiset S3 manifests, from which a run mints the dataset it validates.

This is the strategy at the top of dandi/dandi-cli#1956. The DataLad dataset that is validated is
minted from ``assets.jsonld`` (see ``capsule_code/run.sh``), so each file gets its SHA-256 git-annex
key and its S3 URL without anything being downloaded. The manifest is kept in the capsule's
``sourcedata/``, with the URL of the S3 version it was downloaded from, as the run's input.
"""

import dataclasses
import hashlib
import json
import urllib.parse

import requests

from ._globals import ASSETS_JSONLD_URL_TEMPLATE, DANDISET_YAML_URL_TEMPLATE


class ManifestNotFoundError(LookupError):
    """Signal that the archive has no manifest for a Dandiset version (e.g., embargoed or empty)."""


@dataclasses.dataclass(frozen=True)
class ManifestFile:
    """
    Hold a file of a Dandiset's manifest (``assets.jsonld`` or ``dandiset.yaml``).

    Attributes
    ----------
    content : bytes
        The file, as downloaded.
    url : str or None
        A URL that serves exactly this content: that of the S3 object version it was downloaded
        from. It is ``None`` for a copy that was not downloaded from S3 (e.g., a frozen copy).

    """

    content: bytes
    url: str | None = None


def _fetch(*, url: str, timeout: float = 120.0) -> ManifestFile:
    response = requests.get(url, headers={"User-Agent": "dandi-validations"}, timeout=timeout)
    if response.status_code in (403, 404):
        message = f"{url} is not available (HTTP {response.status_code})."
        raise ManifestNotFoundError(message)
    response.raise_for_status()
    # The draft's manifest is overwritten as the Dandiset changes, but the bucket keeps every
    # version of it, so the version this response holds stays downloadable.
    version_id = response.headers.get("x-amz-version-id")
    version_url = f"{url}?{urllib.parse.urlencode({'versionId': version_id})}" if version_id else None
    return ManifestFile(content=response.content, url=version_url)


def fetch_assets_jsonld(*, dandiset_id: str, version: str = "draft") -> ManifestFile:
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
    ManifestFile
        The manifest, with the URL of the S3 version it was downloaded from.

    """
    return _fetch(url=ASSETS_JSONLD_URL_TEMPLATE.format(dandiset_id=dandiset_id, version=version))


def fetch_dandiset_yaml(*, dandiset_id: str, version: str = "draft") -> ManifestFile:
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
    ManifestFile
        The metadata, with the URL of the S3 version it was downloaded from.

    """
    return _fetch(url=DANDISET_YAML_URL_TEMPLATE.format(dandiset_id=dandiset_id, version=version))


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


def source_digest(*, assets: list[dict], dandiset_yaml: bytes) -> str:
    """
    Digest what the validation reads, which is each asset's path, size and content, and the Dandiset's metadata.

    An asset's content is named by its SHA-256 digest. Metadata-only changes to assets change
    ``assets.jsonld`` but none of these, so they do not form a new job.

    Parameters
    ----------
    assets : list[dict]
        The assets of the manifest.
    dandiset_yaml : bytes
        The Dandiset's metadata.

    Returns
    -------
    str
        The hexadecimal SHA-256 digest.

    """
    digest = hashlib.sha256()
    for asset in sorted(assets, key=lambda asset: asset.get("path", "")):
        fields = (
            asset.get("path", ""),
            str(asset.get("contentSize", "")),
            asset.get("digest", {}).get("dandi:sha2-256", ""),
        )
        digest.update("\t".join(fields).encode() + b"\n")
    digest.update(b"\0")
    digest.update(dandiset_yaml)
    return digest.hexdigest()
