import json
import pathlib
import subprocess

import pytest

from dandi_validations import GitHub, dispatch_validations, ensure_repositories, sync_repository

FAKE_CREDENTIAL = "secret-token"


def _bare(*, tmp_path: pathlib.Path, name: str) -> pathlib.Path:
    remote = tmp_path / "remotes" / f"{name}.git"
    remote.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "--quiet", "--bare", "-b", "main", str(remote)], check=True)
    return remote


def _show(*, remote: pathlib.Path, path: str) -> str:
    return subprocess.run(
        ["git", "-C", str(remote), "show", f"main:{path}"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout


class _FakeGitHub(GitHub):
    def __init__(self, existing: set[str], /) -> None:
        self.existing = set(existing)
        self.created: list[str] = []
        self.dispatched: list[tuple] = []

    def repository_exists(self, name: str, /) -> bool:
        return name in self.existing

    def create_repository(self, name: str, /) -> None:
        self.created.append(name)
        self.existing.add(name)

    def dispatch_workflow(self, *, repository: str, workflow: str, ref: str, inputs: dict) -> None:
        self.dispatched.append((repository, workflow, ref, inputs))


@pytest.mark.ai_generated
def test_sync_initializes_then_is_idempotent_then_updates(tmp_path: pathlib.Path) -> None:
    remote = _bare(tmp_path=tmp_path, name="000123")
    work = tmp_path / "work"
    work.mkdir()
    url = f"file://{remote}"

    assert sync_repository(dandiset_id="000123", remote_url=url, codebase_ref="main", work_directory=work)
    dataset_id = _show(remote=remote, path=".datalad/config")
    assert "id = " in dataset_id
    assert json.loads(_show(remote=remote, path="dataset_description.json"))["DatasetType"] == "derivative"
    assert "annex.largefiles=nothing" in _show(remote=remote, path=".gitattributes")

    assert not sync_repository(dandiset_id="000123", remote_url=url, codebase_ref="main", work_directory=work)

    assert sync_repository(dandiset_id="000123", remote_url=url, codebase_ref="v1.2.3", work_directory=work)
    assert "setup@v1.2.3" in _show(remote=remote, path=".github/workflows/validate.yml")
    # The dataset keeps its identity.
    assert _show(remote=remote, path=".datalad/config") == dataset_id


@pytest.mark.ai_generated
def test_ensure_repositories(tmp_path: pathlib.Path) -> None:
    for name in ("000001", "000002"):
        _bare(tmp_path=tmp_path, name=name)
    github = _FakeGitHub({"000001"})
    outcomes = ensure_repositories(
        dandiset_ids=["000001", "000002"],
        token=FAKE_CREDENTIAL,
        codebase_ref="main",
        remote_url_template=f"file://{tmp_path}/remotes/{{repository}}.git",
        github=github,
    )
    assert outcomes == {"000001": "updated", "000002": "created"}
    assert github.created == ["000002"]

    outcomes = ensure_repositories(
        dandiset_ids=["000001", "000003"],
        token=FAKE_CREDENTIAL,
        codebase_ref="main",
        remote_url_template=f"file://{tmp_path}/remotes/{{repository}}.git?{{token}}",
        create=False,
        github=github,
    )
    assert outcomes["000003"] == "missing"
    # The bogus URL makes the clone fail, and the error must not leak the token from the URL.
    assert outcomes["000001"].startswith("error")
    assert FAKE_CREDENTIAL not in outcomes["000001"]


@pytest.mark.ai_generated
def test_dispatch_validations() -> None:
    github = _FakeGitHub({"000001"})
    outcomes = dispatch_validations(dandiset_ids=["000001"], token=FAKE_CREDENTIAL, force=True, github=github)
    assert outcomes == {"000001": "dispatched"}
    assert github.dispatched == [("000001", "validate.yml", "main", {"force": "true", "dandiset_version": "draft"})]
