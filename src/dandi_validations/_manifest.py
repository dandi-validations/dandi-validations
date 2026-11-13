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
    Hold a Dandiset's ``assets.jsonld`` manifest.

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


def _request(*, method: str, url: str, timeout: float = 120.0) -> requests.Response:
    response = requests.request(method, url, headers={"User-Agent": "dandi-validations"}, timeout=timeout)
    if response.status_code in (403, 404):
        message = f"{url} is not available (HTTP {response.status_code})."
        raise ManifestNotFoundError(message)
    response.raise_for_status()
    return response


def _version_url(*, url: str, response: requests.Response) -> str | None:
    # The draft's files are overwritten as the Dandiset changes, but the bucket keeps every
    # version of them, so the version a response names stays downloadable.
    version_id = response.headers.get("x-amz-version-id")
    return f"{url}?{urllib.parse.urlencode({'versionId': version_id})}" if version_id else None


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
    url = ASSETS_JSONLD_URL_TEMPLATE.format(dandiset_id=dandiset_id, version=version)
    response = _request(method="GET", url=url)
    return ManifestFile(content=response.content, url=_version_url(url=url, response=response))


def locate_dandiset_yaml(*, dandiset_id: str, version: str = "draft") -> str:
    """
    Locate the ``dandiset.yaml`` of a Dandiset version on S3, without downloading it.

    The run downloads it, since ``dandi validate`` finds the Dandiset's root by it.

    Parameters
    ----------
    dandiset_id : str
        The six-digit identifier of the Dandiset.
    version : str, default="draft"
        The Dandiset version.

    Returns
    -------
    str
        The URL of the S3 version it has now, or its URL when the bucket gives no version.

    """
    url = DANDISET_YAML_URL_TEMPLATE.format(dandiset_id=dandiset_id, version=version)
    response = _request(method="HEAD", url=url)
    return _version_url(url=url, response=response) or url


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


def source_digest(*, assets: list[dict]) -> str:
    """
    Digest what the validation reads, which is each asset's path, size and content.

    An asset's content is named by its SHA-256 digest. Changes to the metadata of the assets, or
    of the Dandiset, which ``dandi validate`` does not read, do not change it, so they do not form a
    new job.

    Parameters
    ----------
    assets : list[dict]
        The assets of the manifest.

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
    return digest.hexdigest()
