# DANDI Validations: Dandiset {{ dandiset_id }}

Records of [`dandi validate`](https://github.com/dandi/dandi-cli) runs over
[Dandiset {{ dandiset_id }}]({{ dandiset_url }}), made by [DANDI Validations]({{ codebase_url }}).

Each run happens on this repository's own GitHub Actions runner (the **Validate** workflow):

1. A DataLad dataset of the Dandiset is built from the archive's manifest on S3: every file gets
   its SHA-256 git-annex key and its S3 URL, and nothing is downloaded.
2. `dandi validate --missing-file-content=stream` validates it, streaming only the parts of each
   file the validators read ([dandi/dandi-cli#1956](https://github.com/dandi/dandi-cli/pull/1956)).
3. The validation runs inside the `dandi-validate` container from the GitHub Container Registry,
   through Apptainer, with `datalad containers-run`, whose commit records the command, its inputs
   and its outputs. [duct](https://github.com/con/duct) records its resource usage.

## Layout

The repository is a DataLad dataset laid out as a
[BIDS study](https://bids-specification.readthedocs.io/en/stable/common-principles.html#study-dataset),
with the nesting of DANDI Compute's job capsules:

```
{{ dandiset_id }}/
└── derivatives/
    └── dandisets-{first 3 digits of the Dandiset ID}/
        └── dandiset-{{ dandiset_id }}/
            └── pipeline-{{ pipeline }}/
                ├── jobs.tsv  ← one row per job capsule (described by jobs.json)
                └── job-{YYMMDD}{hash}/
                    ├── code/  ← the exact code and inputs: submit.sh, run.sh, parameters,
                    │            dandiset.yaml and assets.tsv (the files that were validated)
                    ├── logs/  ← all runtime records for success or failure: duct's usage and
                    │            output, container versions, the GitHub Actions run
                    ├── derivatives/  ← validation.jsonl (all results) and summary.json;
                    │                   present only when the validation completed
                    └── dataset_description.json  ← provenance
```

A job is identified by the Dandiset version, the container version, the parameters and the
digest of the assets it validated: an unchanged Dandiset is not validated twice by the same
pipeline. To look at the results of a job:

```bash
dandi validate --load {{ pipeline_path }}/job-.../derivatives/validation.jsonl --summary
```

This repository's shared files are managed by [{{ organization }}/dandi-validations]({{ codebase_url }}).
