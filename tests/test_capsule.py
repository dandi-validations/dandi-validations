import datetime
import json

from dandi_validations._capsule import capsule_status, list_capsule_names, prepare_capsule
from dandi_validations._globals import PROVENANCE_KEY
from dandi_validations._pipeline import load_parameters, load_pipeline

NOW = datetime.datetime(2026, 11, 5, 20, 31, 5, tzinfo=datetime.timezone.utc)


def _prepare(root, assets_jsonld, dandiset_yaml, **kwargs):
    return prepare_capsule(
        repository_root=root,
        dandiset_id="000123",
        assets_jsonld=assets_jsonld,
        dandiset_yaml=dandiset_yaml,
        now=kwargs.pop("now", NOW),
        **kwargs,
    )


def test_prepare_capsule_layout_and_provenance(git_repository, assets_jsonld, dandiset_yaml, monkeypatch):
    monkeypatch.setenv("GITHUB_RUN_ID", "42")
    monkeypatch.setenv("GITHUB_REPOSITORY", "dandi-validations/000123")
    result = _prepare(git_repository, assets_jsonld, dandiset_yaml)
    capsule = result.capsule
    assert capsule is not None
    assert capsule.relative_path.as_posix().startswith(
        "derivatives/dandisets-000/dandiset-000123/pipeline-dandi+validate/job-261105"
    )

    code = sorted(path.name for path in capsule.code_directory.iterdir())
    assert code == [
        "assets.tsv",
        "dandiset.yaml",
        "name-default.json",
        "run.sh",
        "skipped_assets.tsv",
        "submit.sh",
        "submitted_date-2026+11+05_time-20+31+05",
        "summarize.py",
    ]
    marker = capsule.code_directory / "submitted_date-2026+11+05_time-20+31+05"
    assert marker.read_text().strip() == "https://github.com/dandi-validations/000123/actions/runs/42/attempts/1"
    assert len((capsule.code_directory / "assets.tsv").read_text().splitlines()) == 3

    submit = capsule.submit_script_path.read_text()
    pipeline = load_pipeline()
    assert f'CAPSULE="{capsule.relative_path.as_posix()}"' in submit
    assert pipeline.docker_url in submit and pipeline.sif_url in submit
    assert "datalad containers-run" in submit and "duct --output-prefix" in submit
    assert "apptainer exec --cleanenv {img} {cmd}" in submit

    description = json.loads(capsule.dataset_description_path.read_text())
    provenance = description[PROVENANCE_KEY]
    assert provenance["job_id"] == capsule.job_id
    assert provenance["assets"] == 2 and provenance["skipped_assets"] == 2
    assert provenance["params"] == load_parameters("default").identifier
    assert description["DatasetType"] == "derivative"
    assert description["GeneratedBy"][0]["CodeURL"].endswith(pipeline.dandi_cli_commit)

    assert capsule_status(capsule.path) == "stalled"


def test_skip_successful_and_retry_failed(git_repository, assets_jsonld, dandiset_yaml):
    first = _prepare(git_repository, assets_jsonld, dandiset_yaml).capsule

    # A failed capsule (logs, no results) is retried in a new capsule of the same job.
    first.logs_directory.mkdir()
    (first.logs_directory / "run.json").write_text("{}")
    assert capsule_status(first.path) == "failed"
    second = _prepare(git_repository, assets_jsonld, dandiset_yaml).capsule
    assert second.job_id == first.job_id + "-2"

    # A successful capsule is not repeated, unless forced.
    second.derivatives_directory.mkdir()
    (second.derivatives_directory / "summary.json").write_text("{}")
    assert capsule_status(second.path) == "successful"
    skipped = _prepare(git_repository, assets_jsonld, dandiset_yaml)
    assert skipped.capsule is None and second.job_id in skipped.reason
    forced = _prepare(git_repository, assets_jsonld, dandiset_yaml, force=True).capsule
    assert forced.job_id == first.job_id + "-3"

    # Another day's run of the same job is still recognized as done.
    tomorrow = NOW + datetime.timedelta(days=1)
    assert _prepare(git_repository, assets_jsonld, dandiset_yaml, now=tomorrow).capsule is None

    # Changed content forms a different job.
    changed = _prepare(git_repository, assets_jsonld, dandiset_yaml + b"description: changed\n").capsule
    assert changed is not None and changed.job_id[10:16] != first.job_id[10:16]
    assert len(list_capsule_names(git_repository, "000123", "dandi+validate")) == 4


def test_nothing_to_stream(git_repository, dandiset_yaml):
    result = _prepare(git_repository, b"[]", dandiset_yaml)
    assert result.capsule is None and "no assets" in result.reason


def test_capsule_status_levels(tmp_path):
    capsule = tmp_path / "job-261105abcdef"
    assert capsule_status(capsule) == "unknown"
    (capsule / "code").mkdir(parents=True)
    assert capsule_status(capsule) == "pending"
    (capsule / "code" / "submitted_date-2026+11+05_time-00+00+00").write_text("x")
    assert capsule_status(capsule) == "stalled"
    (capsule / "logs").mkdir()
    assert capsule_status(capsule) == "stalled"  # An empty logs directory is not a log
    (capsule / "logs" / "submit.log").write_text("x")
    assert capsule_status(capsule) == "failed"
    (capsule / "derivatives").mkdir()
    (capsule / "derivatives" / "validation.jsonl").write_text("")
    assert capsule_status(capsule) == "successful"
