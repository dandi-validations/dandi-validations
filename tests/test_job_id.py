import datetime

from dandi_validations._job_id import (
    JOB_ID_RE,
    capsules_for_hash,
    compute_job_hash,
    format_job_id,
    next_available_job_id,
    parse_job_hash,
)

FIELDS = dict(
    dandiset_id="000006",
    dandiset_version="draft",
    pipeline="dandi+validate",
    version="v0.1.0",
    params="c9c0b6f",
    source_digest="0" * 64,
)


def test_hash_is_stable_and_sensitive_to_every_field():
    job_hash = compute_job_hash(**FIELDS)
    assert len(job_hash) == 6 and job_hash == compute_job_hash(**FIELDS)
    for key in FIELDS:
        assert compute_job_hash(**{**FIELDS, key: FIELDS[key] + "x"}) != job_hash


def test_format_and_parse():
    date = datetime.date(2026, 11, 5)
    assert format_job_id(job_hash="abcdef", date=date) == "job-261105abcdef"
    assert format_job_id(job_hash="abcdef", date=date, index=3) == "job-261105abcdef-3"
    assert parse_job_hash("job-261105abcdef-3") == "abcdef"
    assert parse_job_hash("job-2610097bbb7e") == "7bbb7e"  # A DANDI Compute capsule name
    assert parse_job_hash("not-a-job") is None
    assert JOB_ID_RE.fullmatch("job-261105abcdef-1") is None


def test_next_available_job_id_appends_a_counter_on_the_same_day():
    date = datetime.date(2026, 11, 5)
    taken = {"job-261105abcdef", "job-261105abcdef-2"}
    assert next_available_job_id(job_hash="abcdef", taken_job_ids=taken, date=date) == "job-261105abcdef-3"
    assert next_available_job_id(job_hash="abcdef", taken_job_ids=taken, date=datetime.date(2026, 11, 6)) == (
        "job-261106abcdef"
    )


def test_capsules_for_hash():
    names = ["job-261105abcdef-2", "job-261104abcdef", "job-261105123456", "job-261105abcdef"]
    assert capsules_for_hash(names, "abcdef") == ["job-261104abcdef", "job-261105abcdef", "job-261105abcdef-2"]
