"""DANDI Validations: streaming ``dandi validate`` per Dandiset on GitHub Actions, recorded as job capsules."""

import importlib.metadata

try:
    __version__ = importlib.metadata.version("dandi-validations")
except importlib.metadata.PackageNotFoundError:  # pragma: no cover - running from a source tree
    __version__ = "unknown"
