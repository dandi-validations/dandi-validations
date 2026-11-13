"""
Behavior of capsules, without git.

What a capsule contains is shown, file by file, by the expected output (see tests/README.md and
test_expected_output.py).
"""

import dataclasses
import datetime
import pathlib

import pytest

from dandi_validations import (
    Capsule,
    Pipeline,
    PrepareResult,
    capsule_status,
    list_capsules,
    load_pipeline,
    prepare_capsule,
    read_dataset_description,
    read_provenance,
)

NOW = datetime.datetime(2026, 11, 5, 20, 31, 5, tzinfo=datetime.UTC)
CLAIM_MARKER = "code/submitted_date-2026+11+05_time-00+00+00"


def _prepare(  # noqa: PLR0913
    *,
    root: pathlib.Path,
    assets_jsonld: bytes,
    dandiset_yaml_url: str,
    now: datetime.datetime = NOW,
    force: bool = False,
    pipeline: Pipeline | None = None,
) -> PrepareResult:
    return prepare_capsule(
        repository_root=root,
        dandiset_id="000123",
        assets_jsonld=assets_jsonld,
        dandiset_yaml_url=dandiset_yaml_url,
        now=now,
        force=force,
        pipeline=pipeline,
    )


def _formed(
    *,
    root: pathlib.Path,
    assets_jsonld: bytes,
    dandiset_yaml_url: str,
    force: bool = False,
    pipeline: Pipeline | None = None,
) -> Capsule:
    capsule = _prepare(
        root=root,
        assets_jsonld=assets_jsonld,
        dandiset_yaml_url=dandiset_yaml_url,
        force=force,
        pipeline=pipeline,
    ).capsule
    assert capsule is not None
    return capsule


def _complete(capsule: Capsule, /) -> None:
    capsule.logs_directory.mkdir(exist_ok=True)
    (capsule.logs_directory / "run.json").write_text("{}")
    capsule.derivatives_directory.mkdir()
    (capsule.derivatives_directory / "validation.jsonl").write_text("")


@pytest.mark.ai_generated
def test_capsule_has_its_own_study_dataset_description(
    git_repository: pathlib.Path,
    assets_jsonld: bytes,
    dandiset_yaml_url: str,
) -> None:
    capsule = _formed(root=git_repository, assets_jsonld=assets_jsonld, dandiset_yaml_url=dandiset_yaml_url)
    description = read_dataset_description(capsule.path)
    assert description["DatasetType"] == "study"
    assert read_provenance(capsule.path) == description["DandiValidations"]
    # The repository's own dataset_description.json is left to the template.
    assert not (git_repository / "dataset_description.json").exists()


@pytest.mark.ai_generated
def test_capsule_keeps_the_manifest_it_validates_as_sourcedata(
    git_repository: pathlib.Path,
    assets_jsonld: bytes,
    dandiset_yaml_url: str,
) -> None:
    capsule = _formed(root=git_repository, assets_jsonld=assets_jsonld, dandiset_yaml_url=dandiset_yaml_url)
    assert capsule.manifest_path == capsule.path / "sourcedata" / "assets.jsonld"
    assert capsule.manifest_path.read_bytes() == assets_jsonld
    # Only the manifest, from which the run mints the dataset and downloads its dandiset.yaml.
    assert list(capsule.sourcedata_directory.iterdir()) == [capsule.manifest_path]
    assert sorted(path.name for path in capsule.code_directory.iterdir() if not path.name.startswith("submitted_")) == [
        "name-default.json",
        "run.sh",
        "submit.sh",
    ]
    # A manifest that was not downloaded from S3 has no URL.
    assert "' '" + dandiset_yaml_url + "'" in capsule.submit_script_path.read_text()
    assert read_provenance(capsule.path)["sourcedata"] == {
        "path": "sourcedata",
        "manifest": "sourcedata/assets.jsonld",
        "manifest_url": None,
        "dandiset_yaml": "sourcedata/000123/dandiset.yaml",
        "dandiset_yaml_url": dandiset_yaml_url,
        "dataset": "sourcedata/000123",
    }


@pytest.mark.ai_generated
def test_reruns_replace_the_capsule_in_place(
    git_repository: pathlib.Path,
    assets_jsonld: bytes,
    dandiset_yaml_url: str,
) -> None:
    first = _formed(root=git_repository, assets_jsonld=assets_jsonld, dandiset_yaml_url=dandiset_yaml_url)

    # A failed run (logs, no results) is retried in the same capsule.
    first.logs_directory.mkdir()
    (first.logs_directory / "run.json").write_text("{}")
    assert capsule_status(first.path) == "failed"
    retry = _formed(root=git_repository, assets_jsonld=assets_jsonld, dandiset_yaml_url=dandiset_yaml_url)
    assert retry.path == first.path
    assert not retry.logs_directory.exists()
    assert capsule_status(retry.path) == "stalled"

    # A successful capsule is not run again for the same inputs, on any day, unless forced.
    _complete(retry)
    assert capsule_status(retry.path) == "successful"
    skipped = _prepare(
        root=git_repository,
        assets_jsonld=assets_jsonld,
        dandiset_yaml_url=dandiset_yaml_url,
        now=NOW + datetime.timedelta(days=3),
    )
    assert skipped.capsule is None
    assert retry.name in skipped.reason
    forced = _formed(root=git_repository, assets_jsonld=assets_jsonld, dandiset_yaml_url=dandiset_yaml_url, force=True)
    assert forced.path == first.path
    assert not forced.derivatives_directory.exists()

    # Changed content is validated again, in the same capsule, and its provenance replaced.
    _complete(forced)
    before = read_provenance(forced.path)["source_digest"]
    changed_assets = assets_jsonld.replace(b"b" * 64, b"d" * 64)
    changed = _formed(root=git_repository, assets_jsonld=changed_assets, dandiset_yaml_url=dandiset_yaml_url)
    assert changed.path == first.path
    assert capsule_status(changed.path) == "stalled"
    assert read_provenance(changed.path)["source_digest"] != before
    assert list_capsules(git_repository) == [first.path]


@pytest.mark.ai_generated
def test_each_image_has_its_own_capsule(
    git_repository: pathlib.Path,
    assets_jsonld: bytes,
    dandiset_yaml_url: str,
) -> None:
    pipeline = load_pipeline()
    newer_code = "d0.83.0-s0.14.0-n0.7.2-p4.2.0-h6.2.0-b3.0.2"
    newer = dataclasses.replace(pipeline, container_version=newer_code)
    first = _formed(root=git_repository, assets_jsonld=assets_jsonld, dandiset_yaml_url=dandiset_yaml_url)
    second = _formed(
        root=git_repository,
        assets_jsonld=assets_jsonld,
        dandiset_yaml_url=dandiset_yaml_url,
        pipeline=newer,
    )
    assert first.path != second.path
    assert list_capsules(git_repository) == sorted([first.path, second.path])
    assert read_provenance(first.path)["version"] == pipeline.container_version
    assert read_provenance(second.path)["version"] == newer_code
    assert read_dataset_description(second.path)["GeneratedBy"][1]["Container"]["Tag"] == newer_code


@pytest.mark.ai_generated
def test_each_codebase_version_has_its_own_capsule(
    git_repository: pathlib.Path,
    assets_jsonld: bytes,
    dandiset_yaml_url: str,
) -> None:
    pipeline = load_pipeline()
    newer = dataclasses.replace(pipeline, codebase_version="99.0.0")
    first = _formed(root=git_repository, assets_jsonld=assets_jsonld, dandiset_yaml_url=dandiset_yaml_url)
    second = _formed(
        root=git_repository,
        assets_jsonld=assets_jsonld,
        dandiset_yaml_url=dandiset_yaml_url,
        pipeline=newer,
    )
    assert first.path != second.path
    assert second.tag == f"{pipeline.container_version}-v99.0.0"
    assert second.path.name == f"pipeline-dandi+validate_tag-{pipeline.container_version}-v99.0.0"
    assert read_provenance(second.path)["branch"] == second.tag
    assert read_provenance(second.path)["codebase"] == "v99.0.0"


@pytest.mark.ai_generated
def test_nothing_to_stream(git_repository: pathlib.Path, dandiset_yaml_url: str) -> None:
    result = _prepare(root=git_repository, assets_jsonld=b"[]", dandiset_yaml_url=dandiset_yaml_url)
    assert result.capsule is None
    assert "no assets" in result.reason


@pytest.mark.ai_generated
@pytest.mark.parametrize(
    ("contents", "expected_status"),
    [
        ([], "unknown"),
        (["code/"], "pending"),
        ([CLAIM_MARKER], "stalled"),
        # An empty logs directory is not a log.
        ([CLAIM_MARKER, "logs/"], "stalled"),
        ([CLAIM_MARKER, "logs/submit.log"], "failed"),
        ([CLAIM_MARKER, "logs/submit.log", "derivatives/validation.jsonl"], "successful"),
    ],
)
def test_capsule_status_levels(tmp_path: pathlib.Path, contents: list[str], expected_status: str) -> None:
    capsule = tmp_path / "pipeline-dandi+validate_tag-x"
    for relative in contents:
        path = capsule / relative
        if relative.endswith("/"):
            path.mkdir(parents=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("x")
    assert capsule_status(capsule) == expected_status
