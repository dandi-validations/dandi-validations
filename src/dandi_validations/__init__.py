"""
Validate every Dandiset on its own GitHub Actions runner and record each run as a job capsule.

Each Dandiset gets a repository, ``dandi-validations/{dandiset_id}``, whose runner validates it
with streaming ``dandi validate`` in the ``dandi-validation`` image (dandi/dandi-cli#1956). Each
run is recorded in a capsule on the branch named by the image's validator version code
(dandi/dandi-archive#2987) and the version of dandi-validations, and ``main`` holds a submodule
for each capsule branch.

- ``run_job`` validates a Dandiset in a clone of its repository, as its Validate workflow does.
- ``prepare_capsule`` and ``capsule_status`` form and read capsules.
- ``ensure_repositories``, ``dispatch_validations`` and ``collect`` manage the repositories.
- ``build_version_code`` and ``parse_version_code`` handle the images' tags, and
  ``build_capsule_tag`` the capsules' tags.
"""

from ._branches import capsule_branches, checkout_capsule, prune_submodules, register_image_url, remote_heads
from ._capsule import (
    Capsule,
    PrepareResult,
    capsule_status,
    list_capsules,
    prepare_capsule,
    read_dataset_description,
    read_provenance,
)
from ._capsule_table import CAPSULE_TABLE_COLUMNS, capsule_row, read_capsule_table
from ._collect import collect, write_records
from ._manifest import assets_table_bytes, fetch_assets_jsonld, parse_assets_jsonld, select_assets, source_digest
from ._pipeline import CAPSULE_CODE_DIRECTORY, PIPELINE_FILE_PATH, TEMPLATES_DIRECTORY, Pipeline, load_pipeline
from ._registry import DIGEST_OVERRIDE_VARIABLE, resolve_digest
from ._repositories import (
    GitHub,
    dispatch_validations,
    ensure_repositories,
    render_repository_template,
    sync_repository,
)
from ._run import commit_and_publish, run_job
from ._templates import render_template, template_placeholders
from ._version_code import (
    VALIDATOR_CODES,
    VersionCodeError,
    build_capsule_tag,
    build_version_code,
    parse_version_code,
    semantic_version,
    versions_from_pixi_lock,
)

__all__ = [
    "CAPSULE_CODE_DIRECTORY",
    "CAPSULE_TABLE_COLUMNS",
    "DIGEST_OVERRIDE_VARIABLE",
    "PIPELINE_FILE_PATH",
    "TEMPLATES_DIRECTORY",
    "VALIDATOR_CODES",
    "Capsule",
    "GitHub",
    "Pipeline",
    "PrepareResult",
    "VersionCodeError",
    "assets_table_bytes",
    "build_capsule_tag",
    "build_version_code",
    "capsule_branches",
    "capsule_row",
    "capsule_status",
    "checkout_capsule",
    "collect",
    "commit_and_publish",
    "dispatch_validations",
    "ensure_repositories",
    "fetch_assets_jsonld",
    "list_capsules",
    "load_pipeline",
    "parse_assets_jsonld",
    "parse_version_code",
    "prepare_capsule",
    "prune_submodules",
    "read_capsule_table",
    "read_dataset_description",
    "read_provenance",
    "register_image_url",
    "remote_heads",
    "render_repository_template",
    "render_template",
    "resolve_digest",
    "run_job",
    "select_assets",
    "semantic_version",
    "source_digest",
    "sync_repository",
    "template_placeholders",
    "versions_from_pixi_lock",
    "write_records",
]
