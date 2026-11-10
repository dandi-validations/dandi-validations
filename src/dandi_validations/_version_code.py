"""
Version codes: the tag of a dandi-validation image, following dandi/dandi-archive#2987.

A version code lists the exact versions of the six packages that affect validation results, each
behind its one-letter code, joined by ``-`` in a fixed order, e.g.
``d0.75.0-s0.12.0-n0.7.1-p3.1.0-h4.1.0-b2.1.0``. Every version must be a plain
``MAJOR.MINOR.PATCH`` release made of digits and periods, which keeps ``-`` out of every entry so
that a code is unambiguous, and keeps it a valid image tag.

One relaxation: a local version segment (everything from ``+``, such as the ``+28.gbec91fa4`` of a
dandi-cli installed from a git commit) is dropped rather than rejected, so an image built from an
unreleased commit still gets a code. Its full versions stay recorded in the image
(``/opt/dandi-validation/versions.json``) and in every job's ``logs/container_versions.json``.
Pre-release, dev and post-release versions are still rejected.
"""

import re
from collections.abc import Mapping

#: One-letter code of each validator distribution in a version code, in the code's order.
VALIDATOR_CODES = {
    "dandi": "d",
    "dandischema": "s",
    "nwbinspector": "n",
    "pynwb": "p",
    "hdmf": "h",
    "bids-validator-deno": "b",
}

_PLAIN_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
_VERSION_CODE_RE = re.compile("-".join(rf"{code}(\d+\.\d+\.\d+)" for code in VALIDATOR_CODES.values()))


class VersionCodeError(ValueError):
    """A version, or a version code, that does not follow the convention."""


def semantic_version(version: str) -> str:
    """The version without its local segment: ``0.82.0+28.gbec91fa4`` → ``0.82.0``."""
    return version.split("+", 1)[0]


def build_version_code(versions: Mapping[str, str]) -> str:
    missing = [name for name in VALIDATOR_CODES if name not in versions]
    if missing:
        message = f"No version given for {', '.join(missing)}."
        raise VersionCodeError(message)
    entries = []
    for name, code in VALIDATOR_CODES.items():
        version = semantic_version(versions[name])
        if not _PLAIN_VERSION_RE.fullmatch(version):
            message = f"{name} {versions[name]} is not a plain MAJOR.MINOR.PATCH release."
            raise VersionCodeError(message)
        entries.append(f"{code}{version}")
    return "-".join(entries)


def is_version_code(text: str) -> bool:
    return _VERSION_CODE_RE.fullmatch(text) is not None


def parse_version_code(version_code: str) -> dict[str, str]:
    match = _VERSION_CODE_RE.fullmatch(version_code)
    if match is None:
        expected = "-".join(f"{code}<MAJOR.MINOR.PATCH>" for code in VALIDATOR_CODES.values())
        message = f"{version_code!r} is not a version code of the form {expected}."
        raise VersionCodeError(message)
    return dict(zip(VALIDATOR_CODES, match.groups()))


def versions_from_pixi_lock(content: str) -> dict[str, str]:
    """
    The versions of the PyPI packages in a pixi lock, by normalized distribution name.

    Each PyPI package of a lock is an entry ``- pypi: <url>`` followed by indented ``name:`` and
    ``version:`` lines, which is all this reads, so no YAML parser is needed.
    """
    versions = {}
    name = None
    in_pypi_entry = False
    for line in content.splitlines():
        if line.startswith("- "):
            in_pypi_entry = line.startswith("- pypi: ")
            name = None
            continue
        if not in_pypi_entry:
            continue
        if line.startswith("  name: "):
            name = line.removeprefix("  name: ").strip()
        elif line.startswith("  version: ") and name is not None:
            versions[name] = line.removeprefix("  version: ").strip()
    return versions
