# Changelog

## Upcoming

### 🚀 Enhancement

- Added a repository per Dandiset whose own GitHub Actions runner validates it with streaming `dandi validate` in the `dandi-validation` image, and records each run as a job capsule on a branch named by the image's validator version code ([#1](https://github.com/dandi-validations/dandi-validations/pull/1))
- Added the version of dandi-validations, as `-vX.Y.Z`, to the end of each capsule's tag, which names its branch and its `pipeline-dandi+validate_tag-...` directory. Each version of the code now forms its own capsules ([#9](https://github.com/dandi-validations/dandi-validations/pull/9))
- Marked each new capsule's `derivatives/validation_summary.txt` as generated, so GitHub collapses it in diffs. `validation.jsonl`, which holds the results themselves, is not marked ([#8](https://github.com/dandi-validations/dandi-validations/pull/8))

### 🐛 Bug Fix

- Fixed reruns and queued runs of the Validate workflow failing to push `main` when the commit they checked out predates the capsule's submodule. A job now rebases its clone onto the remote's `main` before forming its capsule ([#6](https://github.com/dandi-validations/dandi-validations/pull/6))
