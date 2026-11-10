# syntax=docker/dockerfile:1

# The dandi-validation image, following the convention of dandi/dandi-archive#2987:
#
# - Its environment is pixi.lock, resolved from pixi.toml. The lock pins the six validator
#   packages, Python, git, git-annex, DataLad, datalad-container and con-duct, with checksums.
# - Two stages: the official pixi image installs the locked environment, and a plain Ubuntu
#   receives only that environment, its lock and an activation script. Both are pinned by digest.
# - It is published as ghcr.io/dandi-validations/dandi-validation:<version code>, where the
#   version code is derived from the six validator versions it contains (see
#   .github/workflows/build_container.yml and src/dandi_validations/_version_code.py).
#
# The environment lives at the same path in both stages, since conda environments are not
# relocatable.

FROM ghcr.io/prefix-dev/pixi:0.84.1-noble@sha256:2d8821a1f3ee11a7fe8055f2c72d2b116d22f00c4caa73809857b5f216fcdebc AS build

# Fetching the PyPI packages pinned to git commits needs git. pixi installs the conda packages,
# the locked git among them, before the PyPI ones, so the environment's own git does it: no
# operating-system package (or mirror) is involved.
ENV PATH=/opt/dandi-validation/.pixi/envs/default/bin:${PATH}

WORKDIR /opt/dandi-validation
COPY pixi.toml pixi.lock ./
RUN pixi install --locked \
    && pixi shell-hook --shell bash > activate.sh


FROM ubuntu:24.04@sha256:fe81560990fae86dc4743202b2cb4d3df98d0bcf949173cecef15ce1127ef852

LABEL org.opencontainers.image.source="https://github.com/dandi-validations/dandi-validations"
LABEL org.opencontainers.image.description="dandi validate, streaming (dandi/dandi-cli#1956), with DataLad, datalad-container and con-duct; tagged by validator version code (dandi/dandi-archive#2987)."
LABEL org.opencontainers.image.licenses="Apache-2.0"

COPY --from=build /opt/dandi-validation/.pixi/envs/default /opt/dandi-validation/.pixi/envs/default
COPY --from=build /opt/dandi-validation/pixi.toml /opt/dandi-validation/pixi.lock /opt/dandi-validation/activate.sh /opt/dandi-validation/
COPY dandi-validation-versions /usr/local/bin/dandi-validation-versions

# What activate.sh sets, as image environment, so that `apptainer exec` (which sources nothing)
# finds the environment too.
ENV CONDA_PREFIX=/opt/dandi-validation/.pixi/envs/default \
    PATH=/opt/dandi-validation/.pixi/envs/default/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    DANDI_NO_ET=1

RUN chmod 0755 /usr/local/bin/dandi-validation-versions \
    && dandi-validation-versions > /opt/dandi-validation/versions.json \
    && cat /opt/dandi-validation/versions.json \
    && git annex version | head -n 1 \
    && datalad containers-list --help > /dev/null \
    && duct --version \
    && dandi --version

CMD ["dandi-validation-versions"]
