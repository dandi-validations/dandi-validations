# DANDI Validations: setting up and development

The overview is in the [README](../README.md).

## Setting up

1. Add the `_GITHUB_API_KEY` repository secret, a [fine-grained personal access token](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens#creating-a-fine-grained-personal-access-token).
   Its resource owner is the `dandi-validations` organization, with access to all its repositories, so that it covers the repositories it creates.
   It needs these repository permissions, all read and write: Administration (to create repositories), Contents (to push the template), Workflows (to push workflow files) and Actions (to dispatch workflows).
2. Run **Build and publish container**, then make the `dandi-validation` package public (Package settings → Change visibility), so that every repository's runner can pull it anonymously.
3. Run **Make repositories** (try `dandiset_ids: 000006` first), then **Dispatch validations**.

## Command line

From the root of this repository:

```bash
pip install .
dandi-validations list-dandisets --source s3 --limit 5
dandi-validations version-code --lock containers/dandi-validation/pixi.lock
dandi-validations prepare --dandiset 000006 --path /path/to/dataset   # form a capsule only
dandi-validations run --dandiset 000006 --push origin                 # what the Validate workflow runs
dandi-validations ensure-repos --dandisets 000006 --ref main
dandi-validations dispatch --dandisets 000006
dandi-validations collect --output records --tags                     # one table per image tag
```

To run one job locally end to end (needs Docker and Apptainer, and `pip install ".[test]"`):

```bash
bash scripts/e2e_local.sh 000006
```

## Development

Code style, tests, commits and the changelog follow [`AGENTS.md`](../AGENTS.md), which adopts the conventions of [CodyCBakerPhD/historia](https://github.com/CodyCBakerPhD/historia).
A pull request that changes `src/` or `pyproject.toml` must bump the version in `pyproject.toml`, which the **Version Check** workflow enforces.
Each capsule's `dataset_description.json` records that version, with the commit, as the version of this code that formed it.
Its [pre-commit](https://pre-commit.com) hooks run black, ruff, numpydoc validation, mypy, codespell, shellcheck, [md-lines](https://github.com/CodyCBakerPhD/md-lines-pre-commit) and the workflow schema checks:

```bash
pip install pre-commit
pre-commit install
pre-commit run --all-files
```
