"""Constants shared across DANDI Validations."""

import re

#: The GitHub organization that holds one repository per Dandiset.
ORGANIZATION = "dandi-validations"

#: This repository: the code, the container recipe and the collected records.
CODEBASE_REPOSITORY = f"{ORGANIZATION}/dandi-validations"
CODEBASE_URL = f"https://github.com/{CODEBASE_REPOSITORY}"

#: Branch of each Dandiset repository that job capsules are committed to.
DEFAULT_BRANCH = "main"

#: Workflow file (in each Dandiset repository) that runs a validation job.
VALIDATE_WORKFLOW_FILE = "validate.yml"

DANDI_API_URL = "https://api.dandiarchive.org/api"
DANDI_ARCHIVE_URL = "https://dandiarchive.org"
S3_BASE_URL = "https://dandiarchive.s3.amazonaws.com"

#: The manifests the archive publishes for each Dandiset version. Building the DataLad dataset to
#: validate from these is the strategy at the top of dandi/dandi-cli#1956: every asset gets its
#: SHA-256 git-annex key and its S3 URL, and nothing is downloaded.
ASSETS_JSONLD_URL_TEMPLATE = S3_BASE_URL + "/dandisets/{dandiset_id}/{version}/assets.jsonld"
DANDISET_YAML_URL_TEMPLATE = S3_BASE_URL + "/dandisets/{dandiset_id}/{version}/dandiset.yaml"

#: DANDI's DataLad mirror of each Dandiset, which every capsule links as its ``sourcedata/``
#: subdataset (for provenance and self-containment; the validation does not read it).
DANDISETS_URL_TEMPLATE = "https://github.com/dandisets/{dandiset_id}"
SOURCEDATA_BRANCH = "draft"

#: Key of the capsules' provenance in a Dandiset repository's ``dataset_description.json``.
PROVENANCE_KEY = "DandiValidations"

DANDISET_ID_RE = re.compile(r"^\d{6}$")

#: The default parameter set (``params/name-default.json``).
DEFAULT_PARAMS_KEY = "default"
