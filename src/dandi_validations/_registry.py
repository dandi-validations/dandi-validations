"""Resolve an image tag to its digest in an OCI registry, anonymously as for a public GHCR package."""

import logging
import os
import re

import requests

_log = logging.getLogger(__name__)

DIGEST_OVERRIDE_VARIABLE = "DANDI_VALIDATIONS_IMAGE_DIGEST"

_MANIFEST_TYPES = (
    "application/vnd.oci.image.index.v1+json, "
    "application/vnd.oci.image.manifest.v1+json, "
    "application/vnd.docker.distribution.manifest.list.v2+json, "
    "application/vnd.docker.distribution.manifest.v2+json"
)


def _bearer_token(*, challenge: str, timeout: float) -> str:
    parameters = dict(re.findall(r'(\w+)="([^"]*)"', challenge))
    realm = parameters.pop("realm")
    response = requests.get(realm, params=parameters, timeout=timeout)
    response.raise_for_status()
    content = response.json()
    return content.get("token") or content["access_token"]


def resolve_digest(*, image: str, tag: str, timeout: float = 30.0) -> str | None:
    """
    Resolve the digest that ``image:tag`` currently names.

    The convention of dandi/dandi-archive#2987 records the digest next to an image's reference.
    Tags of dandi-validation images are never moved, so the digest is stable. Setting the
    ``DANDI_VALIDATIONS_IMAGE_DIGEST`` environment variable overrides it, for an image that is not
    in a registry (e.g., one built locally).

    Parameters
    ----------
    image : str
        The image, with its registry (e.g., ``ghcr.io/dandi-validations/dandi-validation``).
    tag : str
        The tag of the image.
    timeout : float, default=30.0
        The timeout of each request, in seconds.

    Returns
    -------
    str or None
        The digest (``sha256:...``), or ``None`` when it cannot be resolved.

    """
    override = os.environ.get(DIGEST_OVERRIDE_VARIABLE)
    if override:
        return override

    registry, _, repository = image.partition("/")
    url = f"https://{registry}/v2/{repository}/manifests/{tag}"
    headers = {"Accept": _MANIFEST_TYPES, "User-Agent": "dandi-validations"}
    try:
        response = requests.head(url, headers=headers, timeout=timeout, allow_redirects=True)
        challenge = response.headers.get("WWW-Authenticate", "")
        if response.status_code == 401 and challenge.lower().startswith("bearer"):
            headers["Authorization"] = f"Bearer {_bearer_token(challenge=challenge, timeout=timeout)}"
            response = requests.head(url, headers=headers, timeout=timeout, allow_redirects=True)
        response.raise_for_status()
    except (requests.RequestException, KeyError, ValueError) as error:
        _log.warning("Could not resolve the digest of %s:%s: %s", image, tag, error)
        return None
    return response.headers.get("Docker-Content-Digest")
