"""
The per-Dandiset repositories of the organization: listing, creating, templating and dispatching.

As in bids-dandisets/scripts, this repository creates one repository per Dandiset in its
organization (``dandi-validations/{dandiset_id}``) and keeps their shared files in sync. Unlike
there, each repository runs its own validation job on its own GitHub Actions runner.
"""

import json
import logging
import pathlib
import re
import shutil
import subprocess
import tempfile
import time
import xml.etree.ElementTree

import requests

from ._branches import datalad_dataset_files
from ._capsule import DATASET_DESCRIPTION, capsule_name
from ._globals import (
    CODEBASE_URL,
    DANDI_API_URL,
    DANDI_ARCHIVE_URL,
    DANDISET_ID_RE,
    DEFAULT_BRANCH,
    ORGANIZATION,
    S3_BASE_URL,
    VALIDATE_WORKFLOW_FILE,
)
from ._pipeline import REPOSITORY_TEMPLATE_DIRECTORY, load_pipeline
from ._templates import render

_log = logging.getLogger(__name__)

GITHUB_API_URL = "https://api.github.com"
DEFAULT_REMOTE_URL_TEMPLATE = "https://x-access-token:{token}@github.com/{organization}/{repository}.git"

#: Files of the repository template that are rendered (the rest are copied as they are).
_RENDERED_SUFFIXES = {".md", ".json", ".yml", ".yaml"}


# --------------------------------------------------------------------------------------------- #
# Listing Dandisets
# --------------------------------------------------------------------------------------------- #
def list_dandisets_from_api(*, session: requests.Session | None = None) -> list[str]:
    """Non-empty, non-embargoed Dandisets, from the DANDI API."""
    session = session or requests.Session()
    url = f"{DANDI_API_URL}/dandisets/"
    params = {"page_size": 1000, "empty": "false", "embargoed": "false", "ordering": "id"}
    identifiers = []
    while url:
        response = session.get(url, params=params, timeout=120)
        response.raise_for_status()
        content = response.json()
        for dandiset in content["results"]:
            draft = dandiset.get("draft_version") or {}
            if draft.get("asset_count", 1) == 0:
                continue
            if dandiset.get("embargo_status", "OPEN") != "OPEN":
                continue
            identifiers.append(dandiset["identifier"])
        url = content.get("next")
        params = None
    return sorted(set(identifiers))


def list_dandisets_from_s3(*, session: requests.Session | None = None) -> list[str]:
    """Every Dandiset with a public manifest prefix in the archive's bucket (empty ones included)."""
    session = session or requests.Session()
    namespace = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}
    params = {"list-type": "2", "prefix": "dandisets/", "delimiter": "/"}
    identifiers = []
    while True:
        response = session.get(f"{S3_BASE_URL}/", params=params, timeout=120)
        response.raise_for_status()
        root = xml.etree.ElementTree.fromstring(response.content)
        for prefix in root.findall("s3:CommonPrefixes/s3:Prefix", namespace):
            identifier = prefix.text.strip("/").split("/")[-1]
            if DANDISET_ID_RE.match(identifier):
                identifiers.append(identifier)
        token = root.find("s3:NextContinuationToken", namespace)
        if token is None:
            break
        params = {**params, "continuation-token": token.text}
    return sorted(set(identifiers))


def list_dandisets(source: str = "api") -> list[str]:
    if source == "s3":
        return list_dandisets_from_s3()
    try:
        return list_dandisets_from_api()
    except requests.RequestException as error:
        _log.warning("Listing Dandisets from the DANDI API failed (%s); falling back to S3.", error)
        return list_dandisets_from_s3()


# --------------------------------------------------------------------------------------------- #
# GitHub
# --------------------------------------------------------------------------------------------- #
class GitHub:
    def __init__(self, token: str, *, organization: str = ORGANIZATION, api_url: str = GITHUB_API_URL):
        self.organization = organization
        self.api_url = api_url
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            }
        )

    def _request(self, method: str, path: str, **kwargs) -> requests.Response:
        for attempt in range(5):
            response = self.session.request(method, f"{self.api_url}{path}", timeout=60, **kwargs)
            if response.status_code in (403, 429) and "rate limit" in response.text.lower():
                reset = int(response.headers.get("X-RateLimit-Reset", time.time() + 60))
                wait = max(5, min(reset - time.time(), 900)) if response.status_code == 403 else 60
                _log.warning("Rate limited by GitHub; waiting %.0f seconds.", wait)
                time.sleep(wait)
                continue
            if response.status_code >= 500 and attempt < 4:
                time.sleep(2**attempt)
                continue
            return response
        return response

    def repository_exists(self, name: str) -> bool:
        response = self._request("GET", f"/repos/{self.organization}/{name}")
        if response.status_code == 404:
            return False
        response.raise_for_status()
        return True

    def create_repository(self, dandiset_id: str) -> None:
        response = self._request(
            "POST",
            f"/orgs/{self.organization}/repos",
            json={
                "name": dandiset_id,
                "description": f"DANDI validation records for Dandiset {dandiset_id}.",
                "homepage": f"{DANDI_ARCHIVE_URL}/dandiset/{dandiset_id}",
                "visibility": "public",
                "has_issues": False,
                "has_projects": False,
                "has_wiki": False,
                "auto_init": False,
            },
        )
        if response.status_code == 422 and "already exists" in response.text:
            return
        response.raise_for_status()

    def list_repository_names(self) -> list[str]:
        names = []
        page = 1
        while True:
            response = self._request(
                "GET", f"/orgs/{self.organization}/repos", params={"per_page": 100, "page": page, "type": "all"}
            )
            response.raise_for_status()
            batch = response.json()
            names.extend(repository["name"] for repository in batch)
            if len(batch) < 100:
                break
            page += 1
        return sorted(names)

    def list_dandiset_repositories(self) -> list[str]:
        return [name for name in self.list_repository_names() if DANDISET_ID_RE.match(name)]

    def dispatch_workflow(self, repository: str, *, workflow: str, ref: str, inputs: dict) -> None:
        response = self._request(
            "POST",
            f"/repos/{self.organization}/{repository}/actions/workflows/{workflow}/dispatches",
            json={"ref": ref, "inputs": inputs},
        )
        response.raise_for_status()


# --------------------------------------------------------------------------------------------- #
# Repository template
# --------------------------------------------------------------------------------------------- #
def render_repository_template(*, dandiset_id: str, codebase_ref: str) -> dict[str, bytes]:
    """The managed files of a Dandiset repository, by path relative to its root."""
    pipeline = load_pipeline()
    values = {
        "dandiset_id": dandiset_id,
        "dandiset_url": f"{DANDI_ARCHIVE_URL}/dandiset/{dandiset_id}",
        "organization": ORGANIZATION,
        "codebase_url": CODEBASE_URL,
        "codebase_ref": codebase_ref,
        "pipeline": pipeline.pipeline,
        "capsule_pattern": capsule_name(pipeline.pipeline, "{tag}"),
        "image": pipeline.container_image,
        "branch": DEFAULT_BRANCH,
    }
    files = {}
    for source in sorted(REPOSITORY_TEMPLATE_DIRECTORY.rglob("*")):
        if not source.is_file():
            continue
        relative = source.relative_to(REPOSITORY_TEMPLATE_DIRECTORY).as_posix()
        # Dotfiles are stored without their leading dot so that they do not act on this repository.
        relative = re.sub(r"(^|/)dot-", r"\1.", relative)
        content = source.read_text()
        if source.suffix in _RENDERED_SUFFIXES:
            names = re.findall(r"(?<!\$)\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}", content)
            content = render(content, **{name: values[name] for name in set(names)})
        files[relative] = content.encode("utf-8")
    return files


def _git(command: list[str], cwd: pathlib.Path, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(["git", *command], cwd=cwd, capture_output=True, text=True)
    if check and result.returncode != 0:
        # Never echo the command line: the remote URL carries the token.
        message = f"git {command[0]} failed in {cwd} (exit {result.returncode}):\n{result.stderr}"
        raise RuntimeError(message)
    return result


def sync_repository(
    *,
    dandiset_id: str,
    remote_url: str,
    codebase_ref: str,
    work_directory: pathlib.Path,
    branch: str = DEFAULT_BRANCH,
) -> bool:
    """
    Write the managed files into a Dandiset repository (initializing it when empty) and push.

    Only git is needed: every managed file is text, and is kept in git by the template's
    ``.gitattributes``. Returns whether anything was pushed.
    """
    clone = work_directory / dandiset_id
    if clone.exists():
        shutil.rmtree(clone)
    _git(["clone", "--quiet", "--depth", "1", remote_url, str(clone)], cwd=work_directory)
    is_empty = _git(["rev-parse", "--verify", "--quiet", "HEAD"], cwd=clone, check=False).returncode != 0
    if is_empty:
        _git(["checkout", "--quiet", "-b", branch], cwd=clone)

    files = render_repository_template(dandiset_id=dandiset_id, codebase_ref=codebase_ref)
    if not (clone / ".datalad" / "config").exists():
        files.update({path: content.encode() for path, content in datalad_dataset_files().items()})
    # Jobs keep the provenance of every capsule in dataset_description.json, so the template only
    # starts it.
    if (clone / DATASET_DESCRIPTION).exists():
        files.pop(DATASET_DESCRIPTION)
    for relative, content in files.items():
        target = clone / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)

    _git(["add", "--", *files], cwd=clone)
    if _git(["diff", "--cached", "--quiet"], cwd=clone, check=False).returncode == 0:
        return False
    message = "Initialize DANDI Validations repository" if is_empty else "Sync repository template"
    _git(
        [
            "-c",
            "user.name=github-actions[bot]",
            "-c",
            "user.email=41898282+github-actions[bot]@users.noreply.github.com",
            "commit",
            "--quiet",
            "-m",
            f"{message}\n\nFrom {CODEBASE_URL} at {codebase_ref}.",
        ],
        cwd=clone,
    )
    _git(["push", "--quiet", "origin", f"HEAD:refs/heads/{branch}"], cwd=clone)
    return True


def ensure_repositories(
    *,
    dandiset_ids: list[str],
    token: str,
    codebase_ref: str,
    organization: str = ORGANIZATION,
    remote_url_template: str = DEFAULT_REMOTE_URL_TEMPLATE,
    create: bool = True,
    github: GitHub | None = None,
) -> dict[str, str]:
    """Create any missing Dandiset repositories and sync the template into all of them."""
    github = github or GitHub(token, organization=organization)
    outcomes = {}
    with tempfile.TemporaryDirectory(prefix="dandi-validations-sync-") as temporary_directory:
        work_directory = pathlib.Path(temporary_directory)
        for dandiset_id in dandiset_ids:
            try:
                created = False
                if not github.repository_exists(dandiset_id):
                    if not create:
                        outcomes[dandiset_id] = "missing"
                        continue
                    github.create_repository(dandiset_id)
                    created = True
                remote_url = remote_url_template.format(token=token, organization=organization, repository=dandiset_id)
                changed = sync_repository(
                    dandiset_id=dandiset_id,
                    remote_url=remote_url,
                    codebase_ref=codebase_ref,
                    work_directory=work_directory,
                )
                outcomes[dandiset_id] = "created" if created else ("updated" if changed else "unchanged")
            except Exception as error:  # Keep going: one broken repository must not stop the sweep.
                outcomes[dandiset_id] = f"error: {str(error).replace(token, '***')}"
            print(f"{dandiset_id}: {outcomes[dandiset_id]}", flush=True)
    return outcomes


def dispatch_validations(
    *,
    dandiset_ids: list[str],
    token: str,
    organization: str = ORGANIZATION,
    force: bool = False,
    dandiset_version: str = "draft",
    github: GitHub | None = None,
) -> dict[str, str]:
    github = github or GitHub(token, organization=organization)
    outcomes = {}
    for dandiset_id in dandiset_ids:
        try:
            github.dispatch_workflow(
                dandiset_id,
                workflow=VALIDATE_WORKFLOW_FILE,
                ref=DEFAULT_BRANCH,
                inputs={"force": json.dumps(force), "dandiset_version": dandiset_version},
            )
            outcomes[dandiset_id] = "dispatched"
        except requests.HTTPError as error:
            outcomes[dandiset_id] = f"error: {error.response.status_code} {error.response.text[:200]}"
        print(f"{dandiset_id}: {outcomes[dandiset_id]}", flush=True)
    return outcomes
