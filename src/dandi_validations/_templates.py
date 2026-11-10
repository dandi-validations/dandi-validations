"""
Render the Jinja2 templates of the Dandiset repositories and the job capsules.

GitHub Actions expressions (``${{ ... }}``) in a template are wrapped in ``{% raw %}``, so that
they are kept as they are.
"""

import jinja2
import jinja2.meta

# The templates are Markdown, JSON, YAML and shell, not HTML, so nothing is escaped.
_ENVIRONMENT = jinja2.Environment(
    autoescape=False,  # noqa: S701
    keep_trailing_newline=True,
    undefined=jinja2.StrictUndefined,
)


def template_placeholders(template: str, /) -> set[str]:
    """
    List the names of the variables in a template.

    Parameters
    ----------
    template : str
        The text of the template.

    Returns
    -------
    set[str]
        The name of every variable the template reads, such as ``dandiset_id`` in
        ``{{ dandiset_id }}``.

    """
    return jinja2.meta.find_undeclared_variables(_ENVIRONMENT.parse(template))


def render_template(*, template: str, **values: object) -> str:
    """
    Render a template.

    Every variable must be given a value and every value must be used. That way a template and the
    code that fills it cannot drift apart silently.

    Parameters
    ----------
    template : str
        The text of the template.
    **values : object
        The value of each variable, by name.

    Returns
    -------
    str
        The rendered text.

    Raises
    ------
    KeyError
        If a variable has no value, or a value has no variable.

    """
    names = template_placeholders(template)
    missing = names - values.keys()
    if missing:
        message = f"No value given for template variables: {sorted(missing)}."
        raise KeyError(message)
    unused = values.keys() - names
    if unused:
        message = f"Values given for variables the template does not have: {sorted(unused)}."
        raise KeyError(message)
    return _ENVIRONMENT.from_string(template).render(**values)
