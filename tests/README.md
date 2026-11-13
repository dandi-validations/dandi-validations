# Tests

## The expected output

The integration tests all run on one example, so that what a job should produce can be read here, file by file: **Dandiset 000006**.

**Input:** [`example_dandiset/000006/`](example_dandiset/000006) holds the archive's S3 manifest of the Dandiset's draft (`assets.jsonld`, pretty-printed for reading, and `dandiset.yaml`).
It is frozen so that the expectations don't drift with the archive.
Jobs read it instead of S3 (`dandi-validations run --manifest ...`).

**Expected output:** [`expected_output/`](expected_output) shows what the Dandiset's repository, `https://github.com/dandi-validations/000006`, holds after one validation.
It has one directory per branch:

- [`main/`](expected_output/main) is the derivative dataset, with the capsule's submodule.
- [`d0.82.0-s0.14.0-n0.7.2-p4.2.0-h6.2.0-b3.0.2-v0.4.0/`](expected_output/d0.82.0-s0.14.0-n0.7.2-p4.2.0-h6.2.0-b3.0.2-v0.4.0) is the capsule's branch, named by the image's tag and the version of dandi-validations. It is a study dataset with these side by side:
  - `code/` holds the capsule's code and inputs.
  - `logs/` holds duct's records (`duct_info.json`, `duct_usage.jsonl`, `duct_stdout`, `duct_stderr`), the image's versions (`container_versions.json`), the run's record (`run.json`) and the output of `submit.sh` (`submit.log`).
  - `derivatives/` holds every result in `validation.jsonl`, and their counts by severity, validator and standard in `validation_summary.txt` (`dandi validate --summary`).
    `dandi validate` writes the results in whatever order its workers finish, so they are compared sorted by file, then issue.
  - `sourcedata/` holds the example's `dandiset.yaml` and manifest (`.dandi/assets.jsonld`), and a link to the annexed key of each of the 53 files validated.
  - `envs/` registers the image.

[`test_expected_output.py`](test_expected_output.py) checks it with two tests.
Both run offline against local stand-ins for github.com.

- `test_claimed_capsule_matches_expected_output` forms and claims the capsule without running it.
  The repository must then hold everything the expected output holds besides `logs/`, `derivatives/` and `envs/`. The run itself registers the image in `.datalad/config`, so that file is only checked to be there.
- `test_validated_capsule_matches_expected_output` runs the job for real, streaming the Dandiset's files from S3, and compares the whole repository. It needs Apptainer and the image, so it is marked `container` and skipped without them. The **EndToEnd** job runs it through [`scripts/e2e_local.sh`](../scripts/e2e_local.sh), which builds the image first.

Files are shown as GitHub shows them:
- submodules as a file holding `Subproject commit <commit>`
- symbolic links as a file holding `-> <target>`
- JSON logs indented.

Values that differ from run to run appear as placeholders:

| Placeholder | Stands for |
| --- | --- |
| `<datalad dataset id>` | the random ID `datalad create` gives each dataset |
| `<head of branch d0.82.0-...>` | the capsule branch's latest commit, which main's submodule pins |
| `<annex key of the image>` | where git-annex keeps the image, which depends on the image's build |
| `<capsule>` | where the capsule was checked out on the runner, in result paths |
| `<tmp>`, `<image cache>`, `<python>` | the test's directory, the image's cache and the host's Python |
| `<varies>` | measurements and host details in `duct_info.json` and `run.json` |

Logs that hold progress output and samples taken during the run (`duct_stdout`, `duct_stderr`, `duct_usage.jsonl` and `submit.log`) show one run on GitHub Actions.
Only their presence is checked.

The rest is fixed in [`test_expected_output.py`](test_expected_output.py):
- the clock, 2026-11-05T20:31:05Z
- the GitHub Actions run, run 1, attempt 1, of `dandi-validations/000006`
- the image digest (`sha256:000...`) and the dandi-validations commit (`000...`).

The keys in `sourcedata/` are the frozen example's, so they are shown as they are.
The example's manifest is a copy that no S3 version serves, so no URL is recorded for it.

To regenerate the expected output after an intended change, run the following and review the diff before committing it.
A version bump renames the capsule's branch, so first rename its directory here and `main/`'s submodule file with `git mv`.
Without the image, the first command updates everything but the run's outputs.

```bash
DANDI_VALIDATIONS_REGENERATE_EXPECTED_OUTPUT=1 python -m pytest tests/test_expected_output.py
DANDI_VALIDATIONS_REGENERATE_EXPECTED_OUTPUT=1 bash scripts/e2e_local.sh 000006
```

[`test_branches.py`](test_branches.py) checks, on the same example, how capsule branches and `main`'s submodules behave across jobs.
It covers other runners, reruns, deleted branches, the template sync, the collect step, and downloading a capsule's image, manifest and files from where they are published.
It also checks that `sourcedata/` holds the same keys and URLs as the dataset `run.sh` builds with `datalad addurls`, and that capsules record them once between them.

## Unit tests

The other test files check single functions on small synthetic inputs (see [`conftest.py`](conftest.py)).
They cover version codes, manifests, templates, the command line, the capsule table, and capsule behavior without git.
