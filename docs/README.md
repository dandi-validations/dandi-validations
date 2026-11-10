# DANDI Validations: setting up and development

The overview is in the [README](../README.md).

## Setting up

1. Add the `_GITHUB_API_KEY` organization or repository secret, used only by **Make repositories** and **Dispatch validations**: a token that can create repositories in `dandi-validations`, push workflow files to them, and dispatch their workflows (a classic PAT with `repo` and `workflow`, or a fine-grained token with Administration, Contents, Workflows and Actions write access on the organization's repositories). Every other workflow, including each Dandiset repository's Validate workflow, uses the built-in `GITHUB_TOKEN`.
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
dandi-validations collect --output records
```

To run one job locally end to end (needs Docker and Apptainer, and `pip install ".[test]"`):

```bash
bash scripts/e2e_local.sh 000006
```

## Development

Code style, tests, commits and the changelog follow [`AGENTS.md`](../AGENTS.md), which adopts the conventions of [CodyCBakerPhD/historia](https://github.com/CodyCBakerPhD/historia).
Its [pre-commit](https://pre-commit.com) hooks run black, ruff, numpydoc validation, mypy, codespell, shellcheck, [md-lines](https://github.com/CodyCBakerPhD/md-lines-pre-commit) and the workflow schema checks:

```bash
pip install pre-commit
pre-commit install
pre-commit run --all-files
```
