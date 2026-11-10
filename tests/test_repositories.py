import json
import subprocess

from dandi_validations._repositories import dispatch_validations, ensure_repositories, sync_repository


def _bare(tmp_path, name):
    remote = tmp_path / "remotes" / f"{name}.git"
    remote.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "--quiet", "--bare", "-b", "main", str(remote)], check=True)
    return remote


def _show(remote, path):
    return subprocess.run(
        ["git", "-C", str(remote), "show", f"main:{path}"], capture_output=True, text=True, check=True
    ).stdout


def test_sync_initializes_then_is_idempotent_then_updates(tmp_path):
    remote = _bare(tmp_path, "000123")
    work = tmp_path / "work"
    work.mkdir()
    url = f"file://{remote}"

    assert sync_repository(dandiset_id="000123", remote_url=url, codebase_ref="main", work_directory=work)
    dataset_id = _show(remote, ".datalad/config")
    assert "id = " in dataset_id
    assert json.loads(_show(remote, "dataset_description.json"))["DatasetType"] == "derivative"
    assert "annex.largefiles=nothing" in _show(remote, ".gitattributes")

    assert not sync_repository(dandiset_id="000123", remote_url=url, codebase_ref="main", work_directory=work)

    assert sync_repository(dandiset_id="000123", remote_url=url, codebase_ref="v1.2.3", work_directory=work)
    assert "setup@v1.2.3" in _show(remote, ".github/workflows/validate.yml")
    assert _show(remote, ".datalad/config") == dataset_id  # The dataset keeps its identity


def test_sync_leaves_the_provenance_jobs_wrote(tmp_path):
    remote = _bare(tmp_path, "000123")
    work = tmp_path / "work"
    work.mkdir()
    url = f"file://{remote}"
    sync_repository(dandiset_id="000123", remote_url=url, codebase_ref="main", work_directory=work)

    # A job records a capsule's provenance in dataset_description.json and pushes it.
    clone = tmp_path / "job"
    subprocess.run(["git", "clone", "-q", url, str(clone)], check=True)
    description = json.loads((clone / "dataset_description.json").read_text())
    description["DandiValidations"] = {"pipeline-dandi+validate_tag-x": {"version": "x"}}
    (clone / "dataset_description.json").write_text(json.dumps(description))
    git = ["git", "-C", str(clone), "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run([*git, "commit", "-q", "-am", "Submit"], check=True)
    subprocess.run([*git, "push", "-q", "origin", "HEAD:main"], check=True)

    assert sync_repository(dandiset_id="000123", remote_url=url, codebase_ref="v2", work_directory=work)
    synced = json.loads(_show(remote, "dataset_description.json"))
    assert synced["DandiValidations"] == {"pipeline-dandi+validate_tag-x": {"version": "x"}}


class FakeGitHub:
    def __init__(self, existing):
        self.existing = set(existing)
        self.created = []
        self.dispatched = []

    def repository_exists(self, name):
        return name in self.existing

    def create_repository(self, name):
        self.created.append(name)
        self.existing.add(name)

    def dispatch_workflow(self, repository, *, workflow, ref, inputs):
        self.dispatched.append((repository, workflow, ref, inputs))


def test_ensure_repositories(tmp_path):
    for name in ("000001", "000002"):
        _bare(tmp_path, name)
    github = FakeGitHub(existing={"000001"})
    outcomes = ensure_repositories(
        dandiset_ids=["000001", "000002"],
        token="secret-token",
        codebase_ref="main",
        remote_url_template=f"file://{tmp_path}/remotes/{{repository}}.git",
        github=github,
    )
    assert outcomes == {"000001": "updated", "000002": "created"}
    assert github.created == ["000002"]

    outcomes = ensure_repositories(
        dandiset_ids=["000001", "000003"],
        token="secret-token",
        codebase_ref="main",
        remote_url_template=f"file://{tmp_path}/remotes/{{repository}}.git?{{token}}",
        create=False,
        github=github,
    )
    assert outcomes["000003"] == "missing"
    # The bogus URL makes the clone fail; the error must not leak the token from the URL.
    assert outcomes["000001"].startswith("error") and "secret-token" not in outcomes["000001"]


def test_dispatch_validations():
    github = FakeGitHub(existing={"000001"})
    outcomes = dispatch_validations(dandiset_ids=["000001"], token="t", force=True, github=github)
    assert outcomes == {"000001": "dispatched"}
    assert github.dispatched == [("000001", "validate.yml", "main", {"force": "true", "dandiset_version": "draft"})]
