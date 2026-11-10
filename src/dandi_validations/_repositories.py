"""
List, create, template and dispatch the per-Dandiset repositories of the organization.

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
import xml.etree.ElementTree as ET

import requests

from ._branches import datalad_dataset_files
from ._capsule import capsule_name
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
from ._templates import render_template, template_placeholders

_log = logging.getLogger(__name__)

GITHUB_API_URL = "https://api.github.com"
DEFAULT_REMOTE_URL_TEMPLATE = "https://x-access-token:{token}@github.com/{organization}/{repository}.git"

_RENDERED_SUFFIXES = {".md", ".json", ".yml", ".yaml"}
_S3_NAMESPACE = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}


def list_dandisets_from_api(session: requests.Session | None = None, /) -> list[str]:
    """
    List the non-empty, non-embargoed Dandisets, from the DANDI API.

    Parameters
    ----------
    session : requests.Session or None, default=None
        The session to make the requests with.

    Returns
    -------
    list[str]
        The identifiers of the Dandisets, sorted.

    """
    session = session or requests.Session()
    url = f"{DANDI_API_URL}/dandisets/"
    params: dict | None = {"page_size": 1000, "empty": "false", "embargoed": "false", "ordering": "id"}
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


def list_dandisets_from_s3(session: requests.Session | None = None, /) -> list[str]:
    """
    List every Dandiset with a public manifest prefix in the archive's bucket, empty ones included.

    Parameters
    ----------
    session : requests.Session or None, default=None
        The session to make the requests with.

    Returns
    -------
    list[str]
        The identifiers of the Dandisets, sorted.

    """
    session = session or requests.Session()
    params = {"list-type": "2", "prefix": "dandisets/", "delimiter": "/"}
    identifiers = []
    while True:
        response = session.get(f"{S3_BASE_URL}/", params=params, timeout=120)
        response.raise_for_status()
        # The archive's own bucket listing, which carries no entity declarations.
        root = ET.fromstring(response.content)  # noqa: S314
        for prefix in root.findall("s3:CommonPrefixes/s3:Prefix", _S3_NAMESPACE):
            identifier = (prefix.text or "").strip("/").split("/")[-1]
            if DANDISET_ID_RE.match(identifier):
                identifiers.append(identifier)
        token = root.find("s3:NextContinuationToken", _S3_NAMESPACE)
        if token is None or token.text is None:
            break
        params = {**params, "continuation-token": token.text}
    return sorted(set(identifiers))


def list_dandisets(source: str = "api", /) -> list[str]:
    """
    List the Dandisets to validate.

    Parameters
    ----------
    source : str, default="api"
        Where to list them from. The DANDI API (``api``) lists only non-empty, open Dandisets, and
        falls back to S3 when it cannot be reached. S3 (``s3``) lists every Dandiset.

    Returns
    -------
    list[str]
        The identifiers of the Dandisets, sorted.

    """
    if source == "s3":
        return list_dandisets_from_s3()
    try:
        identifiers = list_dandisets_from_api()
    except requests.RequestException as error:
        _log.warning("Listing Dandisets from the DANDI API failed (%s); falling back to S3.", error)
        identifiers = list_dandisets_from_s3()
    return identifiers


class GitHub:
    """
    Call the GitHub API for an organization.

    Parameters
    ----------
    token : str
        A token that can administer the organization.
    organization : str, default="dandi-validations"
        The organization.
    api_url : str, default="https://api.github.com"
        The API's URL.

    """

    def __init__(self, *, token: str, organization: str = ORGANIZATION, api_url: str = GITHUB_API_URL) -> None:
        self.organization = organization
        self.api_url = api_url
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )

    def _request(
        self,
        *,
        method: str,
        path: str,
        payload: dict | None = None,
        params: dict | None = None,
    ) -> requests.Response:
        attempts = 5
        for attempt in range(attempts):
            response = self.session.request(
                method,
                f"{self.api_url}{path}",
                json=payload,
                params=params,
                timeout=60,
            )
            if response.status_code in (403, 429) and "rate limit" in response.text.lower():
                reset = int(response.headers.get("X-RateLimit-Reset", time.time() + 60))
                wait = max(5, min(reset - time.time(), 900)) if response.status_code == 403 else 60
                _log.warning("Rate limited by GitHub; waiting %.0f seconds.", wait)
                time.sleep(wait)
                continue
            if response.status_code >= 500 and attempt < attempts - 1:
                time.sleep(2**attempt)
                continue
            break
        return response

    def repository_exists(self, name: str, /) -> bool:
        """
        Check whether the organization has a repository.

        Parameters
        ----------
        name : str
            The repository's name.

        Returns
        -------
        bool
            Whether it exists.

        """
        response = self._request(method="GET", path=f"/repos/{self.organization}/{name}")
        if response.status_code == 404:
            return False
        response.raise_for_status()
        return True

    def create_repository(self, dandiset_id: str, /) -> None:
        """
        Create the public repository of a Dandiset, unless it exists.

        Parameters
        ----------
        dandiset_id : str
            The six-digit identifier of the Dandiset, which names the repository.

        """
        response = self._request(
            method="POST",
            path=f"/orgs/{self.organization}/repos",
            payload={
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
        """
        List the organization's repositories.

        Returns
        -------
        list[str]
            Their names, sorted.

        """
        names: list[str] = []
        page = 1
        while True:
            response = self._request(
                method="GET",
                path=f"/orgs/{self.organization}/repos",
                params={"per_page": 100, "page": page, "type": "all"},
            )
            response.raise_for_status()
            batch = response.json()
            names.extend(repository["name"] for repository in batch)
            if len(batch) < 100:
                break
            page += 1
        return sorted(names)

    def list_dandiset_repositories(self) -> list[str]:
        """
        List the organization's Dandiset repositories.

        Returns
        -------
        list[str]
            Their names, which are Dandiset identifiers, sorted.

        """
        return [name for name in self.list_repository_names() if DANDISET_ID_RE.match(name)]

    def dispatch_workflow(self, *, repository: str, workflow: str, ref: str, inputs: dict) -> None:
        """
        Start a workflow of one of the organization's repositories.

        Parameters
        ----------
        repository : str
            The repository's name.
        workflow : str
            The workflow's file name.
        ref : str
            The branch to run it on.
        inputs : dict
            The workflow's inputs.

        """
        response = self._request(
            method="POST",
            path=f"/repos/{self.organization}/{repository}/actions/workflows/{workflow}/dispatches",
            payload={"ref": ref, "inputs": inputs},
        )
        response.raise_for_status()


def render_repository_template(*, dandiset_id: str, codebase_ref: str) -> dict[str, bytes]:
    """
    Render the managed files of a Dandiset repository.

    Parameters
    ----------
    dandiset_id : str
        The six-digit identifier of the Dandiset.
    codebase_ref : str
        The ref of this repository that the repository's workflow uses.

    Returns
    -------
    dict[str, bytes]
        The content of each file, by path relative to the repository's root.

    """
    pipeline = load_pipeline()
    values = {
        "dandiset_id": dandiset_id,
        "dandiset_url": f"{DANDI_ARCHIVE_URL}/dandiset/{dandiset_id}",
        "organization": ORGANIZATION,
        "codebase_url": CODEBASE_URL,
        "codebase_ref": codebase_ref,
        "pipeline": pipeline.pipeline,
        "capsule_pattern": capsule_name(pipeline=pipeline.pipeline, tag="{tag}"),
        "image": pipeline.container_image,
        "branch": DEFAULT_BRANCH,
    }
    files = {}
    for source in sorted(REPOSITORY_TEMPLATE_DIRECTORY.rglob("*")):
        if not source.is_file():
            continue
        relative = source.relative_to(REPOSITORY_TEMPLATE_DIRECTORY).as_posix()
        # Dotfiles are stored without their leading dot, so that they do not act on this repository.
        relative = re.sub(r"(^|/)dot-", r"\1.", relative)
        content = source.read_text()
        if source.suffix in _RENDERED_SUFFIXES:
            names = template_placeholders(content)
            content = render_template(template=content, **{name: values[name] for name in names})
        files[relative] = content.encode("utf-8")
    return files


def _git(*, arguments: list[str], cwd: pathlib.Path, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(["git", *arguments], cwd=cwd, capture_output=True, text=True, check=False)
    if check and result.returncode != 0:
        # Never echo the command line, since the remote URL carries the token.
        message = f"git {arguments[0]} failed in {cwd} (exit {result.returncode}):\n{result.stderr}"
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
    Write the managed files into a Dandiset repository, initializing it when empty, and push.

    Only git is needed, since every managed file is text and the template's ``.gitattributes``
    keeps it in git.

    Parameters
    ----------
    dandiset_id : str
        The six-digit identifier of the Dandiset.
    remote_url : str
        The repository's URL, with any credentials.
    codebase_ref : str
        The ref of this repository that the repository's workflow uses.
    work_directory : pathlib.Path
        Where to clone the repository.
    branch : str, default="main"
        The branch to write to.

    Returns
    -------
    bool
        Whether anything was pushed.

    """
    clone = work_directory / dandiset_id
    if clone.exists():
        shutil.rmtree(clone)
    _git(arguments=["clone", "--quiet", "--depth", "1", remote_url, str(clone)], cwd=work_directory)
    is_empty = _git(arguments=["rev-parse", "--verify", "--quiet", "HEAD"], cwd=clone, check=False).returncode != 0
    if is_empty:
        _git(arguments=["checkout", "--quiet", "-b", branch], cwd=clone)

    files = render_repository_template(dandiset_id=dandiset_id, codebase_ref=codebase_ref)
    if not (clone / ".datalad" / "config").exists():
        files.update({path: content.encode() for path, content in datalad_dataset_files().items()})
    for relative, content in files.items():
        target = clone / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)

    _git(arguments=["add", "--", *files], cwd=clone)
    if _git(arguments=["diff", "--cached", "--quiet"], cwd=clone, check=False).returncode == 0:
        return False
    message = "Initialize DANDI Validations repository" if is_empty else "Sync repository template"
    _git(
        arguments=[
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
    _git(arguments=["push", "--quiet", "origin", f"HEAD:refs/heads/{branch}"], cwd=clone)
    return True


def ensure_repositories(  # noqa: PLR0913
    *,
    dandiset_ids: list[str],
    token: str,
    codebase_ref: str,
    organization: str = ORGANIZATION,
    remote_url_template: str = DEFAULT_REMOTE_URL_TEMPLATE,
    create: bool = True,
    github: GitHub | None = None,
) -> dict[str, str]:
    """
    Create any missing Dandiset repositories and sync the template into all of them.

    Parameters
    ----------
    dandiset_ids : list[str]
        The Dandisets whose repositories to ensure.
    token : str
        A token that can administer the organization.
    codebase_ref : str
        The ref of this repository that the repositories' workflow uses.
    organization : str, default="dandi-validations"
        The organization.
    remote_url_template : str, default=DEFAULT_REMOTE_URL_TEMPLATE
        The repositories' URL, with ``{token}``, ``{organization}`` and ``{repository}`` fields.
    create : bool, default=True
        Whether to create missing repositories, rather than only sync existing ones.
    github : GitHub or None, default=None
        The GitHub API client, if not one for ``token``.

    Returns
    -------
    dict[str, str]
        The outcome for each Dandiset (``created``, ``updated``, ``unchanged``, ``missing`` or
        ``error: ...``).

    """
    github = github or GitHub(token=token, organization=organization)
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
            # One broken repository must not stop the sweep.
            except Exception as error:  # noqa: BLE001
                outcomes[dandiset_id] = f"error: {str(error).replace(token, '***')}"
            _log.info("%s: %s", dandiset_id, outcomes[dandiset_id])
    return outcomes


def dispatch_validations(  # noqa: PLR0913
    *,
    dandiset_ids: list[str],
    token: str,
    organization: str = ORGANIZATION,
    force: bool = False,
    dandiset_version: str = "draft",
    github: GitHub | None = None,
) -> dict[str, str]:
    """
    Start the Validate workflow of Dandiset repositories.

    Parameters
    ----------
    dandiset_ids : list[str]
        The Dandisets whose repositories to dispatch.
    token : str
        A token that can run the repositories' workflows.
    organization : str, default="dandi-validations"
        The organization.
    force : bool, default=False
        Whether the jobs validate even if their capsule already validated the same inputs.
    dandiset_version : str, default="draft"
        The Dandiset version to validate.
    github : GitHub or None, default=None
        The GitHub API client, if not one for ``token``.

    Returns
    -------
    dict[str, str]
        The outcome for each Dandiset (``dispatched`` or ``error: ...``).

    """
    github = github or GitHub(token=token, organization=organization)
    outcomes = {}
    for dandiset_id in dandiset_ids:
        try:
            github.dispatch_workflow(
                repository=dandiset_id,
                workflow=VALIDATE_WORKFLOW_FILE,
                ref=DEFAULT_BRANCH,
                inputs={"force": json.dumps(force), "dandiset_version": dandiset_version},
            )
            outcomes[dandiset_id] = "dispatched"
        except requests.HTTPError as error:
            outcomes[dandiset_id] = f"error: {error.response.status_code} {error.response.text[:200]}"
        _log.info("%s: %s", dandiset_id, outcomes[dandiset_id])
    return outcomes
