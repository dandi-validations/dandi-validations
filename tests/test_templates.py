import pathlib
import re
import subprocess

import pytest

from dandi_validations import TEMPLATES_DIRECTORY, render_repository_template, render_template, template_placeholders


@pytest.mark.ai_generated
def test_render_substitutes_and_keeps_raw_github_expressions() -> None:
    template = "id={{ dandiset_id }} ref={% raw %}${{ inputs.ref }}{% endraw %} again={{dandiset_id}}\n"
    assert template_placeholders(template) == {"dandiset_id"}
    rendered = render_template(template=template, dandiset_id="000006")
    assert rendered == "id=000006 ref=${{ inputs.ref }} again=000006\n"


@pytest.mark.ai_generated
@pytest.mark.parametrize(
    ("template", "values", "message"),
    [
        ("{{ a }} {{ b }}", {"a": 1}, "No value"),
        ("{{ a }}", {"a": 1, "b": 2}, "does not have"),
        # A GitHub Actions expression outside {% raw %} is read as a variable.
        ("ref=${{ inputs.ref }}", {}, "No value"),
    ],
)
def test_render_requires_exact_values(template: str, values: dict, message: str) -> None:
    with pytest.raises(KeyError, match=message):
        render_template(template=template, **values)


@pytest.mark.ai_generated
def test_repository_template_renders_completely() -> None:
    files = render_repository_template(dandiset_id="000123", codebase_ref="main")
    assert set(files) == {
        ".github/workflows/validate.yml",
        ".gitattributes",
        "README.md",
        "dataset_description.json",
    }
    unrendered = {path for path, content in files.items() if re.search(r"(?<!\$)\{\{|\{%", content.decode())}
    assert unrendered == set()
    workflow = files[".github/workflows/validate.yml"].decode()
    assert "uses: dandi-validations/dandi-validations/.github/actions/setup@main" in workflow
    assert "--dandiset 000123" in workflow
    assert "${{ inputs.dandiset_version || 'draft' }}" in workflow


@pytest.mark.ai_generated
def test_submit_template_is_valid_bash(tmp_path: pathlib.Path) -> None:
    template = (TEMPLATES_DIRECTORY / "submit.sh").read_text()
    script = render_template(template=template, **dict.fromkeys(template_placeholders(template), "x"))
    path = tmp_path / "submit.sh"
    path.write_text(script)
    subprocess.run(["bash", "-n", str(path)], check=True)
