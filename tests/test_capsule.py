import datetime
import json

from dandi_validations._capsule import (
    capsule_status,
    list_capsules,
    prepare_capsule,
    read_dataset_description,
    read_provenance,
)
from dandi_validations._globals import PROVENANCE_KEY
from dandi_validations._pipeline import load_parameters, load_pipeline
from dandi_validations._registry import DIGEST_OVERRIDE_VARIABLE
from dandi_validations._repositories import render_repository_template
from dandi_validations._version_code import parse_version_code

NOW = datetime.datetime(2026, 11, 5, 20, 31, 5, tzinfo=datetime.timezone.utc)
DIGEST = "sha256:" + "f" * 64


def _prepare(root, assets_jsonld, dandiset_yaml, **kwargs):
    return prepare_capsule(
        repository_root=root,
        dandiset_id="000123",
        assets_jsonld=assets_jsonld,
        dandiset_yaml=dandiset_yaml,
        now=kwargs.pop("now", NOW),
        **kwargs,
    )


def _start_from_template(root):
    """A Dandiset repository as the template sync creates it."""
    files = render_repository_template(dandiset_id="000123", codebase_ref="main")
    (root / "dataset_description.json").write_bytes(files["dataset_description.json"])


def _complete(capsule):
    capsule.logs_directory.mkdir(exist_ok=True)
    (capsule.logs_directory / "run.json").write_text("{}")
    capsule.derivatives_directory.mkdir()
    (capsule.derivatives_directory / "summary.json").write_text("{}")


def test_capsule_layout_and_single_dataset_description(git_repository, assets_jsonld, dandiset_yaml, monkeypatch):
    monkeypatch.setenv("GITHUB_RUN_ID", "42")
    monkeypatch.setenv("GITHUB_REPOSITORY", "dandi-validations/000123")
    monkeypatch.setenv(DIGEST_OVERRIDE_VARIABLE, DIGEST)
    _start_from_template(git_repository)
    pipeline = load_pipeline()

    capsule = _prepare(git_repository, assets_jsonld, dandiset_yaml).capsule
    assert capsule is not None
    assert capsule.relative_path.as_posix() == f"pipeline-dandi+validate_tag-{pipeline.container_version}"
    assert list_capsules(git_repository) == [capsule.path]

    # The capsule holds code (and, once run, logs and derivatives), but no dataset_description.json.
    assert sorted(path.name for path in capsule.path.iterdir()) == ["code"]
    assert sorted(path.name for path in capsule.code_directory.iterdir()) == [
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
    assert 'CAPSULE="."' in submit and f"branch {capsule.tag}" in submit
    assert f"docker://{pipeline.container_image}@{DIGEST}" in submit and pipeline.sif_url in submit
    assert f'VERSION_CODE="{pipeline.container_version}"' in submit
    assert "datalad containers-run" in submit and "duct --output-prefix" in submit
    assert "apptainer exec --cleanenv {img} {cmd}" in submit

    # The repository's one dataset_description.json carries the capsule's provenance.
    description = read_dataset_description(git_repository)
    assert description["DatasetType"] == "derivative"
    assert description["Name"] == "DANDI Validations: Dandiset 000123"
    assert set(description[PROVENANCE_KEY]) == {capsule.name}
    provenance = read_provenance(capsule.path)
    assert provenance == description[PROVENANCE_KEY][capsule.name]
    assert provenance["assets"] == 2 and provenance["skipped_assets"] == 2
    assert provenance["params"] == load_parameters("default").identifier
    assert provenance["version"] == pipeline.container_version
    assert provenance["validator_versions"] == parse_version_code(pipeline.container_version)
    assert provenance["image"] == f"ghcr.io/dandi-validations/dandi-validation:{pipeline.container_version}"
    assert provenance["image_digest"] == DIGEST
    assert provenance["dandi_cli"]["commit"] == pipeline.dandi_cli_commit

    orchestration, validation = description["GeneratedBy"]
    assert orchestration["Name"] == "DANDI Validations"
    assert validation["Name"] == "dandi validate" and capsule.name in validation["Description"]
    assert validation["CodeURL"].endswith(pipeline.dandi_cli_commit)
    assert validation["Container"] == {
        "Type": "docker",
        "Tag": pipeline.container_version,
        "URI": f"docker://{pipeline.container_image}@{DIGEST}",
    }

    assert capsule_status(capsule.path) == "stalled"


def test_reruns_replace_the_capsule_in_place(git_repository, assets_jsonld, dandiset_yaml):
    first = _prepare(git_repository, assets_jsonld, dandiset_yaml).capsule

    # A failed run (logs, no results) is retried in the same capsule.
    first.logs_directory.mkdir()
    (first.logs_directory / "run.json").write_text("{}")
    assert capsule_status(first.path) == "failed"
    retry = _prepare(git_repository, assets_jsonld, dandiset_yaml).capsule
    assert retry.path == first.path
    assert not retry.logs_directory.exists() and capsule_status(retry.path) == "stalled"

    # A successful capsule is not run again for the same inputs, on any day, unless forced.
    _complete(retry)
    assert capsule_status(retry.path) == "successful"
    skipped = _prepare(git_repository, assets_jsonld, dandiset_yaml, now=NOW + datetime.timedelta(days=3))
    assert skipped.capsule is None and retry.name in skipped.reason
    forced = _prepare(git_repository, assets_jsonld, dandiset_yaml, force=True).capsule
    assert forced.path == first.path and not forced.derivatives_directory.exists()

    # Changed content is validated again, in the same capsule, and its provenance replaced.
    _complete(forced)
    before = read_provenance(forced.path)["source_digest"]
    changed = _prepare(git_repository, assets_jsonld, dandiset_yaml + b"description: changed\n").capsule
    assert changed.path == first.path and capsule_status(changed.path) == "stalled"
    assert read_provenance(changed.path)["source_digest"] != before
    assert list_capsules(git_repository) == [first.path]


def test_capsules_of_two_images_share_the_dataset_description(git_repository, assets_jsonld, dandiset_yaml):
    pipeline = load_pipeline()
    newer = pipeline.__class__(
        **{**pipeline.__dict__, "container_version": "d0.83.0-s0.14.0-n0.7.2-p4.2.0-h6.2.0-b3.0.2"}
    )
    first = _prepare(git_repository, assets_jsonld, dandiset_yaml).capsule
    second = _prepare(git_repository, assets_jsonld, dandiset_yaml, pipeline=newer).capsule
    assert first.path != second.path
    assert list_capsules(git_repository) == sorted([first.path, second.path])

    description = json.loads((git_repository / "dataset_description.json").read_text())
    assert set(description[PROVENANCE_KEY]) == {first.name, second.name}
    tags = [entry["Container"]["Tag"] for entry in description["GeneratedBy"][1:]]
    assert tags == sorted([pipeline.container_version, newer.container_version])


def test_nothing_to_stream(git_repository, dandiset_yaml):
    result = _prepare(git_repository, b"[]", dandiset_yaml)
    assert result.capsule is None and "no assets" in result.reason


def test_capsule_status_levels(tmp_path):
    capsule = tmp_path / "pipeline-dandi+validate_tag-x"
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
