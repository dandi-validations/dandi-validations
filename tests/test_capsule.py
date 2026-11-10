"""
Behavior of capsules, without git. What a capsule contains is shown, file by file, by the golden
example (see tests/README.md and test_golden_example.py).
"""

import datetime

from dandi_validations._capsule import (
    capsule_status,
    list_capsules,
    prepare_capsule,
    read_dataset_description,
    read_provenance,
)
from dandi_validations._pipeline import load_pipeline

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


def _complete(capsule):
    capsule.logs_directory.mkdir(exist_ok=True)
    (capsule.logs_directory / "run.json").write_text("{}")
    capsule.derivatives_directory.mkdir()
    (capsule.derivatives_directory / "summary.json").write_text("{}")


def test_capsule_has_its_own_study_dataset_description(git_repository, assets_jsonld, dandiset_yaml):
    capsule = _prepare(git_repository, assets_jsonld, dandiset_yaml).capsule
    description = read_dataset_description(capsule.path)
    assert description["DatasetType"] == "study"
    assert read_provenance(capsule.path) == description["DandiValidations"]
    # The repository's own dataset_description.json is left to the template.
    assert not (git_repository / "dataset_description.json").exists()


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


def test_each_image_has_its_own_capsule(git_repository, assets_jsonld, dandiset_yaml):
    pipeline = load_pipeline()
    newer_code = "d0.83.0-s0.14.0-n0.7.2-p4.2.0-h6.2.0-b3.0.2"
    newer = pipeline.__class__(**{**pipeline.__dict__, "container_version": newer_code})
    first = _prepare(git_repository, assets_jsonld, dandiset_yaml).capsule
    second = _prepare(git_repository, assets_jsonld, dandiset_yaml, pipeline=newer).capsule
    assert first.path != second.path
    assert list_capsules(git_repository) == sorted([first.path, second.path])
    assert read_provenance(first.path)["version"] == pipeline.container_version
    assert read_provenance(second.path)["version"] == newer_code
    assert read_dataset_description(second.path)["GeneratedBy"][1]["Container"]["Tag"] == newer_code


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
