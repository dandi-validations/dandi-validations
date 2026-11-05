# syntax=docker/dockerfile:1

# Runtime for streaming `dandi validate` over DataLad datasets built from the DANDI Archive's
# S3 manifests. It installs exactly what the recipe at the top of
# https://github.com/dandi/dandi-cli/pull/1956 installs (git-annex, con-duct and
# `dandi[datalad]` from that PR), with dandi-cli pinned to a commit rather than a branch.
#
# The image is built and published to the GitHub Container Registry by
# .github/workflows/build_container.yml, which reads the pins from
# src/dandi_validations/pipeline.json. Jobs run it through Apptainer, via
# `datalad containers-run`.

FROM python:3.13-slim-trixie

ARG DANDI_CLI_REPOSITORY=https://github.com/dandi/dandi-cli
ARG DANDI_CLI_COMMIT
ARG CONTAINER_VERSION=unknown

LABEL org.opencontainers.image.source="https://github.com/dandi-validations/dandi-validations"
LABEL org.opencontainers.image.description="Streaming dandi validate (dandi/dandi-cli#1956) over DataLad datasets of DANDI Archive Dandisets."
LABEL org.opencontainers.image.licenses="Apache-2.0"
LABEL org.opencontainers.image.version="${CONTAINER_VERSION}"
LABEL org.dandi-validations.dandi-cli.repository="${DANDI_CLI_REPOSITORY}"
LABEL org.dandi-validations.dandi-cli.commit="${DANDI_CLI_COMMIT}"

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        git \
    && rm -rf /var/lib/apt/lists/*

ENV VIRTUAL_ENV=/opt/venv \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv "${VIRTUAL_ENV}"
ENV PATH="${VIRTUAL_ENV}/bin:${PATH}"

# The install line from the top of dandi/dandi-cli#1956, plus DataLad itself, which builds the
# dataset from the S3 manifest (`datalad addurls`) inside the container as well.
RUN test -n "${DANDI_CLI_COMMIT}" \
    && pip install \
        git-annex \
        con-duct \
        datalad \
        "dandi[datalad] @ git+${DANDI_CLI_REPOSITORY}@${DANDI_CLI_COMMIT}"

# Recorded so that a job can report what it ran with, without network access.
ENV DANDI_VALIDATE_CONTAINER_VERSION="${CONTAINER_VERSION}" \
    DANDI_VALIDATE_DANDI_CLI_REPOSITORY="${DANDI_CLI_REPOSITORY}" \
    DANDI_VALIDATE_DANDI_CLI_COMMIT="${DANDI_CLI_COMMIT}" \
    DANDI_NO_ET=1

COPY dandi-validate-versions /usr/local/bin/dandi-validate-versions
RUN chmod 0755 /usr/local/bin/dandi-validate-versions \
    && mkdir -p /opt/dandi-validate \
    && dandi-validate-versions > /opt/dandi-validate/versions.json \
    && cat /opt/dandi-validate/versions.json \
    && git annex version | head -n 1 \
    && dandi --version

CMD ["dandi-validate-versions"]
