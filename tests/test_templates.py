import subprocess

import pytest

from dandi_validations._pipeline import TEMPLATES_DIRECTORY
from dandi_validations._repositories import render_repository_template
from dandi_validations._templates import placeholders, render


def test_render_substitutes_and_leaves_github_expressions():
    template = "id={{ dandiset_id }} ref=${{ inputs.ref }} again={{dandiset_id}}"
    assert placeholders(template) == {"dandiset_id"}
    assert render(template, dandiset_id="000006") == "id=000006 ref=${{ inputs.ref }} again=000006"


def test_render_requires_exact_values():
    with pytest.raises(KeyError, match="No value"):
        render("{{ a }} {{ b }}", a=1)
    with pytest.raises(KeyError, match="does not have"):
        render("{{ a }}", a=1, b=2)


def test_repository_template_renders_completely():
    files = render_repository_template(dandiset_id="000123", codebase_ref="main")
    assert set(files) == {
        ".github/workflows/validate.yml",
        ".gitattributes",
        "README.md",
        "dataset_description.json",
    }
    for path, content in files.items():
        text = content.decode()
        assert not placeholders(text), path
    workflow = files[".github/workflows/validate.yml"].decode()
    assert "uses: dandi-validations/dandi-validations/.github/actions/setup@main" in workflow
    assert "--dandiset-id 000123" in workflow
    assert "${{ inputs.dandiset_version || 'draft' }}" in workflow


def test_submit_template_is_valid_bash(tmp_path):
    placeholder_names = placeholders((TEMPLATES_DIRECTORY / "submit.sh").read_text())
    script = render((TEMPLATES_DIRECTORY / "submit.sh").read_text(), **{name: "x" for name in placeholder_names})
    path = tmp_path / "submit.sh"
    path.write_text(script)
    subprocess.run(["bash", "-n", str(path)], check=True)
