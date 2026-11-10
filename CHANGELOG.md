# Changelog

## Upcoming

### 🚀 Enhancement

- Added a repository per Dandiset whose own GitHub Actions runner validates it with streaming `dandi validate` in the `dandi-validation` image, and records each run as a job capsule on a branch named by the image's validator version code ([#1](https://github.com/dandi-validations/dandi-validations/pull/1))
- Made a capsule's image downloadable with `datalad get`, so `datalad rerun` works in any clone. Each SIF is also published as a release of this repository, and jobs record its URL in the repository's `git-annex` branch ([#7](https://github.com/dandi-validations/dandi-validations/pull/7))

### 🐛 Bug Fix

- Fixed reruns and queued runs of the Validate workflow failing to push `main` when the commit they checked out predates the capsule's submodule. A job now rebases its clone onto the remote's `main` before forming its capsule ([#6](https://github.com/dandi-validations/dandi-validations/pull/6))
