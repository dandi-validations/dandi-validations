"""
The job ID that names a job capsule directory.

This follows the convention of DANDI Compute (dandi-compute/dandi-compute-core): a capsule
directory is named ``job-{YYMMDD}{hash}``, where ``hash`` is the first six characters of the MD5
checksum of the fields that identify the job. The date makes the name readable at a glance and
separates re-attempts of the same job across days, while the hash keeps capsules for different
inputs apart within a single day. A ``-2``, ``-3`` counter is appended when one logical job forms
a second capsule on the same day.

For a validation the identifying fields are the Dandiset, its version, the pipeline and its
container version, the parameter set, and the digest of the source (the registered assets and
``dandiset.yaml``). The codebase version is deliberately left out: a job is the same logical
job no matter which release of this package formed it.
"""

import datetime
import hashlib
import re
from collections.abc import Collection, Iterable

JOB_ID_RE = re.compile(r"job-(?P<job_date>\d{6})(?P<job_hash>[0-9a-f]{6})(?:-(?P<job_index>[2-9]|\d{2,}))?")


def compute_job_hash(
    *,
    dandiset_id: str,
    dandiset_version: str,
    pipeline: str,
    version: str,
    params: str,
    source_digest: str,
) -> str:
    payload = "|".join([dandiset_id, dandiset_version, pipeline, version, params, source_digest])
    return hashlib.md5(payload.encode("utf-8")).hexdigest()[:6]


def format_job_id(*, job_hash: str, date: datetime.date | None = None, index: int = 1) -> str:
    date = date if date is not None else datetime.datetime.now(tz=datetime.timezone.utc).date()
    counter = "" if index <= 1 else f"-{index}"
    return f"job-{date:%y%m%d}{job_hash}{counter}"


def parse_job_hash(job_id: str, /) -> str | None:
    match = JOB_ID_RE.fullmatch(job_id)
    return match.group("job_hash") if match is not None else None


def capsules_for_hash(capsule_names: Iterable[str], job_hash: str) -> list[str]:
    """The capsules of one logical job, oldest first."""
    return sorted(name for name in capsule_names if parse_job_hash(name) == job_hash)


def next_available_job_id(*, job_hash: str, taken_job_ids: Collection[str], date: datetime.date | None = None) -> str:
    index = 1
    job_id = format_job_id(job_hash=job_hash, date=date, index=index)
    while job_id in taken_job_ids:
        index += 1
        job_id = format_job_id(job_hash=job_hash, date=date, index=index)
    return job_id
