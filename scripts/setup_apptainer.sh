#!/bin/bash
# Install Apptainer on an Ubuntu GitHub Actions runner (from the Apptainer PPA).
set -euo pipefail

if ! command -v apptainer > /dev/null; then
    sudo add-apt-repository -y ppa:apptainer/ppa
    sudo apt-get update -q
    sudo apt-get install -y -q apptainer
fi
# Ubuntu 24.04 restricts unprivileged user namespaces, which unprivileged Apptainer uses.
sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=0 || true
apptainer --version
