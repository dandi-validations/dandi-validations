"""Resolve an image tag to its digest in an OCI registry (anonymously, as for a public GHCR package)."""

import json
import logging
import os
import re
import urllib.error
import urllib.parse
import urllib.request

_log = logging.getLogger(__name__)

#: Set to the digest to record, for an image that is not in a registry (e.g. built locally).
DIGEST_OVERRIDE_VARIABLE = "DANDI_VALIDATIONS_IMAGE_DIGEST"

_MANIFEST_TYPES = ", ".join(
    [
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    ]
)


def _bearer_token(challenge: str, *, timeout: float) -> str:
    parameters = dict(re.findall(r'(\w+)="([^"]*)"', challenge))
    realm = parameters.pop("realm")
    url = f"{realm}?{urllib.parse.urlencode(parameters)}"
    with urllib.request.urlopen(url, timeout=timeout) as response:
        content = json.load(response)
    return content.get("token") or content["access_token"]


def resolve_digest(image: str, tag: str, *, timeout: float = 30.0) -> str | None:
    """
    The digest that ``image:tag`` currently names, or ``None`` when it cannot be resolved.

    The digest is what the convention of dandi/dandi-archive#2987 records next to an image's
    reference. Tags of dandi-validation images are never moved, so this is stable.
    """
    override = os.environ.get(DIGEST_OVERRIDE_VARIABLE)
    if override:
        return override

    registry, _, repository = image.partition("/")
    url = f"https://{registry}/v2/{repository}/manifests/{tag}"
    headers = {"Accept": _MANIFEST_TYPES, "User-Agent": "dandi-validations"}
    try:
        try:
            response = urllib.request.urlopen(
                urllib.request.Request(url, method="HEAD", headers=headers), timeout=timeout
            )
        except urllib.error.HTTPError as error:
            challenge = error.headers.get("WWW-Authenticate", "")
            if error.code != 401 or not challenge.lower().startswith("bearer"):
                raise
            headers["Authorization"] = f"Bearer {_bearer_token(challenge, timeout=timeout)}"
            response = urllib.request.urlopen(
                urllib.request.Request(url, method="HEAD", headers=headers), timeout=timeout
            )
        with response:
            return response.headers.get("Docker-Content-Digest")
    except (urllib.error.URLError, KeyError, ValueError, OSError) as error:
        _log.warning("Could not resolve the digest of %s:%s: %s", image, tag, error)
        return None
