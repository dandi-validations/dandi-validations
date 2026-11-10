import os
import subprocess
import sys

import pytest


@pytest.mark.ai_generated
@pytest.mark.parametrize("command", ["ensure-repos", "dispatch"])
def test_classic_tokens_are_refused(command: str) -> None:
    environment = {**os.environ, "_GITHUB_API_KEY": "ghp_" + "0" * 36}
    result = subprocess.run(
        [sys.executable, "-m", "dandi_validations", command, "--dandisets", "000006"],
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )
    assert result.returncode == 1
    assert "must be a fine-grained personal access token" in result.stderr
