# Tests

## The golden example

The integration tests all run on one example, so that what a job should produce can be read here,
file by file: **Dandiset 000006**.

**Input:** [`example_dandiset/000006/`](example_dandiset/000006) holds the archive's S3 manifest of
the Dandiset's draft (`assets.jsonld`, pretty-printed for reading, and `dandiset.yaml`), frozen so
that the expectations don't drift with the archive. Jobs read it instead of S3
(`dandi-validations run --manifest-directory ...`).

**Expected output:** [`expected_output/`](expected_output) shows what the Dandiset's repository,
`https://github.com/dandi-validations/000006`, holds:

- [`submitted/`](expected_output/submitted): right after a job has formed and claimed its capsule,
  before the container runs. It has one directory per branch:
  - [`main/`](expected_output/submitted/main): the derivative dataset, and the capsule's submodule.
  - [`d0.82.0-s0.14.0-n0.7.2-p4.2.0-h6.2.0-b3.0.2/`](expected_output/submitted/d0.82.0-s0.14.0-n0.7.2-p4.2.0-h6.2.0-b3.0.2):
    the capsule's branch, named by the image's tag. It is a study dataset holding the capsule's
    code and inputs, and the link to `sourcedata/`.
  - [`test_golden_example.py`](test_golden_example.py) checks it. It runs offline against local
    stand-ins for github.com.
- [`validated/`](expected_output/validated): the capsule's results after the real validation:
  `summary.json` and every result in `validation.jsonl`.
  - The **EndToEnd** job checks it with [`scripts/e2e_local.sh`](../scripts/e2e_local.sh): it builds
    the image and runs the job for real, streaming the Dandiset's files from S3.

Files are shown as GitHub shows them:
- submodules as a file holding `Subproject commit <commit>`;
- symbolic links as a file holding `-> <target>`.

Values that differ from run to run appear as placeholders:

| Placeholder | Stands for |
| --- | --- |
| `<datalad dataset id>` | the random ID `datalad create` gives each dataset |
| `<head of branch d0.82.0-...>` | the capsule branch's latest commit, which main's submodule pins |
| `<head of dandisets/000006 draft>` | the commit of DANDI's DataLad mirror that `sourcedata/` pins |
| `<capsule>` | where the capsule was checked out on the runner, in result paths |

The rest is fixed in [`golden.py`](golden.py):
- the clock: 2026-11-05T20:31:05Z;
- the GitHub Actions run: run 1, attempt 1, of `dandi-validations/000006`;
- the image digest (`sha256:000...`) and the dandi-validations commit (`000...`).

The sandbox's stand-in mirror has one `draft` commit holding the example's `dandiset.yaml`.

To regenerate the expected output after an intended change, run the following and review the
diff before committing it:

```bash
DANDI_VALIDATIONS_REGENERATE_GOLDEN=1 python -m pytest tests/test_golden_example.py
DANDI_VALIDATIONS_REGENERATE_GOLDEN=1 bash scripts/e2e_local.sh 000006
```

[`test_branches.py`](test_branches.py) checks, on the same example, how capsule branches and
`main`'s submodules behave across jobs: other runners, reruns, deleted branches, the template sync
and the collect step.

## Unit tests

The other test files check single functions on small synthetic inputs (see
[`conftest.py`](conftest.py)): version codes, manifests, templates, the summary, the capsule table,
and capsule behavior without git.
