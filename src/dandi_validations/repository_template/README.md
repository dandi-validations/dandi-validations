# DANDI Validations: Dandiset {{ dandiset_id }}

Records of [`dandi validate`](https://github.com/dandi/dandi-cli) runs over [Dandiset {{ dandiset_id }}]({{ dandiset_url }}), made by [DANDI Validations]({{ codebase_url }}).

Each run happens on this repository's own GitHub Actions runner (the **Validate** workflow):

1. A DataLad dataset of the Dandiset is built directly from the archive's manifest from S3: every file gets its SHA-256 git-annex key and its S3 URL, and nothing is downloaded.[^sourcedata]
2. `dandi validate --missing-file-content=stream` validates it, streaming only the parts of each file the validators need.
3. The validation runs inside the `dandi-validation` image from the GitHub Container Registry (`{{ image }}:{tag}`), through Apptainer, with `datalad containers-run`, whose commit records the command, its inputs and its outputs.
   [duct](https://github.com/con/duct) records its resource usage.

## Layout

The repository is a DataLad dataset laid out as a standalone [BIDS derivative dataset (type 2)](https://bids-specification.readthedocs.io/en/stable/common-principles.html#storage-of-derived-datasets) of the Dandiset, with one [BIDS Study](https://bids-specification.readthedocs.io/en/stable/common-principles.html#study-dataset) capsule per `dandi-validation` image.
Each capsule is a DataLad dataset on its own branch, named by the image's tag (its validator version code), and `main` holds a submodule for each of those branches:

```
https://github.com/{{ organization }}/{{ dandiset_id }}/   (main)
├── dataset_description.json  ← the derivative dataset that collects the capsules
└── {{ capsule_pattern }}/  ← submodule: branch {tag} of this repository
    ├── dataset_description.json  ← a study dataset: the provenance of its latest run
    ├── code/  ← the exact code and inputs: submit.sh, run.sh, parameters,
    │            dandiset.yaml and assets.tsv (the files that were validated)
    ├── logs/  ← all runtime records for success or failure: duct's usage and
    │            output, the image's versions, the GitHub Actions run
    ├── derivatives/  ← validation.jsonl, all the results, present only when the validation
    │                   completed (`dandi validate --summary` prints their counts in logs/)
    ├── envs/dandi-validation/image  ← the image's registration (annexed, on GHCR)
    └── sourcedata/  ← subdataset: DANDI's DataLad mirror of the Dandiset
                       (https://github.com/dandisets/{{ dandiset_id }}) at its draft head when the run was formed
```

A capsule holds the latest validation of the Dandiset with its image.
It is not run again while the Dandiset's files and metadata are unchanged.
When they change, the next run replaces its contents in a new commit on its branch, whose history keeps every earlier run and its `datalad containers-run` record.
To look at a capsule's results:

```bash
git clone https://github.com/{{ organization }}/{{ dandiset_id }}
git -C {{ dandiset_id }} submodule update --init  # the capsules, not their sourcedata/
dandi validate --load {{ dandiset_id }}/{{ capsule_pattern }}/derivatives/validation.jsonl --summary
```

---

This repository's shared files are managed by [{{ organization }}/dandi-validations]({{ codebase_url }}).

[^sourcedata]: `sourcedata/` is linked for provenance and self-containment only: the validation reads the Dandiset through a dataset built from the archive's S3 manifest, and nothing is fetched from the mirror.
