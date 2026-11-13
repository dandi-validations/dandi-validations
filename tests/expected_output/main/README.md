# DANDI Validations: Dandiset 000006

Records of [`dandi validate`](https://github.com/dandi/dandi-cli) runs over [Dandiset 000006](https://dandiarchive.org/dandiset/000006), made by [DANDI Validations](https://github.com/dandi-validations/dandi-validations).

Each run happens on this repository's own GitHub Actions runner (the **Validate** workflow):

1. A DataLad dataset of the Dandiset is built directly from the archive's manifest from S3: every file gets its SHA-256 git-annex key and its S3 URL, and nothing is downloaded.[^sourcedata]
2. `dandi validate --missing-file-content=stream` validates it, streaming only the parts of each file the validators need.
3. The validation runs inside the `dandi-validation` image from the GitHub Container Registry (`ghcr.io/dandi-validations/dandi-validation:{version code}`), through Apptainer, with `datalad containers-run`, whose commit records the command, its inputs and its outputs.
   [duct](https://github.com/con/duct) records its resource usage.

## Layout

The repository is a DataLad dataset laid out as a standalone [BIDS derivative dataset (type 2)](https://bids-specification.readthedocs.io/en/stable/common-principles.html#storage-of-derived-datasets) of the Dandiset, with one [BIDS Study](https://bids-specification.readthedocs.io/en/stable/common-principles.html#study-dataset) capsule per `dandi-validation` image and version of DANDI Validations.
Each capsule is a DataLad dataset on its own branch, named by its `{tag}`.
That is the image's tag (its validator version code), followed by `-v` and the version of DANDI Validations that formed it.
`main` holds a submodule for each of those branches:

```
https://github.com/dandi-validations/000006/   (main)
├── dataset_description.json  ← the derivative dataset that collects the capsules
└── pipeline-dandi+validate_tag-{tag}/  ← submodule: branch {tag} of this repository
    ├── dataset_description.json  ← a study dataset: the provenance of its latest run
    ├── code/  ← the exact code and inputs: submit.sh, run.sh, parameters
    │            and assets.tsv (the files that were validated)
    ├── logs/  ← all runtime records for success or failure: duct's usage and
    │            output, the image's versions, the GitHub Actions run
    ├── derivatives/  ← present only when the validation completed: validation.jsonl, all the
    │                   results, and validation_summary.txt, their counts (dandi validate --summary)
    ├── envs/dandi-validation/image  ← the image's registration (annexed, on GHCR and in a release of https://github.com/dandi-validations/dandi-validations)
    └── sourcedata/  ← the Dandiset as validated: dandiset.yaml and the manifest
                       (.dandi/assets.jsonld), as downloaded from S3, and every file validated
```

A capsule holds the latest validation of the Dandiset with its image and version of DANDI Validations.
It is not run again while the Dandiset's files and metadata are unchanged.
When they change, the next run replaces its contents in a new commit on its branch, whose history keeps every earlier run and its `datalad containers-run` record.
To look at a capsule's results:

```bash
git clone https://github.com/dandi-validations/000006
git -C 000006 submodule update --init  # the capsules, without the content of their sourcedata/
dandi validate --load 000006/pipeline-dandi+validate_tag-{tag}/derivatives/validation.jsonl --summary
```

To rerun a capsule's latest validation, with DataLad, git-annex and Apptainer:

```bash
datalad clone https://github.com/dandi-validations/000006
cd 000006
datalad get -n pipeline-dandi+validate_tag-{tag}
cd pipeline-dandi+validate_tag-{tag}
datalad rerun "$(git log --format=%H --grep='DATALAD RUNCMD' -1)"  # downloads the image first
```

---

This repository's shared files are managed by [dandi-validations/dandi-validations](https://github.com/dandi-validations/dandi-validations).

[^sourcedata]: Each capsule's `sourcedata/` holds that dataset's files, registered the same way, beside the manifest itself.
The manifest is annexed with the URL of the S3 version it was downloaded from.
Any of them downloads with `datalad get`.
