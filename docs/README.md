# DANDI Validations: setting up and development

The overview is in the [README](../README.md).

## Setting up

1. Add the `_GITHUB_API_KEY` repository secret, a [fine-grained personal access token](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens#creating-a-fine-grained-personal-access-token) with:
  - Resource owner as the `dandi-validations` organization.
  - Access to all its repositories.
  - Read and write permissions for:
    - Administration (to create repositories).
    - Contents (to push the template).
    - Workflows (to push workflow files).
    - Actions (to dispatch workflows).
2. Run **Build and publish container**, then make the `dandi-validation` package public (Package settings → Change visibility), so that every repository's runner can pull it anonymously.
3. [Optional] Manually run **Make repositories** (e.g., `dandiset_ids: 000003`), then **Dispatch validations**.

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
dandi-validations collect --output records --tags                     # one table per capsule tag
```

To run one job locally end to end (needs Docker and Apptainer, and `pip install ".[test]"`):

```bash
bash scripts/e2e_local.sh 000006
```

## Development

Code style, tests, commits and the changelog follow [`AGENTS.md`](../AGENTS.md).
The repository template (`src/dandi_validations/repository_template/`) and each capsule's `submit.sh` (`src/dandi_validations/templates/`) are [Jinja2](https://jinja.palletsprojects.com) templates.
Rendering fails if a variable has no value, so wrap GitHub Actions expressions (`${{ ... }}`) in `{% raw %}...{% endraw %}`.
A pull request that changes `src/` or `pyproject.toml` must bump the version in `pyproject.toml`, which the **Version Check** workflow enforces.
Each capsule's `dataset_description.json` records that version, with the commit, as the version of this code that formed it.
The version also ends the name of each capsule's branch and directory (`-vX.Y.Z`), so a new version validates every Dandiset again in new capsules.
Its [pre-commit](https://pre-commit.com) hooks run black, ruff, numpydoc validation, mypy, codespell, shellcheck, md-lines and the workflow schema checks:

```bash
pip install pre-commit
pre-commit install
pre-commit run --all-files
```
