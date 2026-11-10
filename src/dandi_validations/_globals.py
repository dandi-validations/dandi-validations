"""Hold the constants shared across DANDI Validations."""

import re

ORGANIZATION = "dandi-validations"
CODEBASE_REPOSITORY = f"{ORGANIZATION}/dandi-validations"
CODEBASE_URL = f"https://github.com/{CODEBASE_REPOSITORY}"

DEFAULT_BRANCH = "main"
VALIDATE_WORKFLOW_FILE = "validate.yml"

DANDI_API_URL = "https://api.dandiarchive.org/api"
DANDI_ARCHIVE_URL = "https://dandiarchive.org"
S3_BASE_URL = "https://dandiarchive.s3.amazonaws.com"

# The strategy at the top of dandi/dandi-cli#1956 builds the dataset to validate from these
# manifests. Every asset gets its SHA-256 git-annex key and its S3 URL, and nothing is downloaded.
ASSETS_JSONLD_URL_TEMPLATE = S3_BASE_URL + "/dandisets/{dandiset_id}/{version}/assets.jsonld"
DANDISET_YAML_URL_TEMPLATE = S3_BASE_URL + "/dandisets/{dandiset_id}/{version}/dandiset.yaml"

# Every capsule links DANDI's DataLad mirror of its Dandiset as `sourcedata/`, for provenance only.
DANDISETS_URL_TEMPLATE = "https://github.com/dandisets/{dandiset_id}"
SOURCEDATA_BRANCH = "draft"

PROVENANCE_KEY = "DandiValidations"

DANDISET_ID_RE = re.compile(r"^\d{6}$")

DEFAULT_PARAMS_KEY = "default"
