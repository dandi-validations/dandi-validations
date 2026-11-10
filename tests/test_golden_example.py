"""
The golden example, end to end up to running the container: a job on Dandiset 000006 (its frozen
manifest) forms and claims its capsule, and the repository then holds exactly
tests/expected_output/submitted, branch by branch.
"""

import pathlib

from dandi_validations._pipeline import load_pipeline
from dandi_validations._run import run_job

from .golden import (
    EXAMPLE_DANDISET_ID,
    EXAMPLE_MANIFEST_DIRECTORY,
    EXPECTED_OUTPUT_DIRECTORY,
    NOW,
    assert_matches_expected,
    labels,
    make_world,
    materialize,
)


def test_submitted_repository_matches_the_golden_example(tmp_path: pathlib.Path, monkeypatch) -> None:
    world = make_world(tmp_path, monkeypatch)
    exit_code = run_job(
        path=world.runner,
        dandiset_id=EXAMPLE_DANDISET_ID,
        push_to="origin",
        manifest_directory=EXAMPLE_MANIFEST_DIRECTORY,
        execute=False,
        now=NOW,
    )
    assert exit_code == 0

    tag = load_pipeline().container_version
    names = labels(world)
    actual = tmp_path / "submitted"
    for branch in ("main", tag):
        materialize(world.repository, branch, actual / branch, names)
    assert_matches_expected(actual, EXPECTED_OUTPUT_DIRECTORY / "submitted")
