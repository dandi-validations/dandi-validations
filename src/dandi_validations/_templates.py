"""Fill ``{{ name }}`` placeholders, leaving GitHub Actions expressions (``${{ ... }}``) alone."""

import re

_PLACEHOLDER_RE = re.compile(r"(?<!\$)\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


def template_placeholders(template: str, /) -> set[str]:
    """
    List the names of the placeholders in a template.

    Parameters
    ----------
    template : str
        The text of the template.

    Returns
    -------
    set[str]
        The name of every ``{{ name }}`` placeholder.

    """
    return set(_PLACEHOLDER_RE.findall(template))


def render_template(*, template: str, **values: object) -> str:
    """
    Substitute every ``{{ name }}`` placeholder of a template.

    Every placeholder must be given a value and every value must be used. That way a template and
    the code that fills it cannot drift apart silently.

    Parameters
    ----------
    template : str
        The text of the template.
    **values : object
        The value of each placeholder, by name.

    Returns
    -------
    str
        The rendered text.

    Raises
    ------
    KeyError
        If a placeholder has no value, or a value has no placeholder.

    """
    names = template_placeholders(template)
    missing = names - values.keys()
    if missing:
        message = f"No value given for template placeholders: {sorted(missing)}."
        raise KeyError(message)
    unused = values.keys() - names
    if unused:
        message = f"Values given for placeholders the template does not have: {sorted(unused)}."
        raise KeyError(message)
    return _PLACEHOLDER_RE.sub(lambda match: str(values[match.group(1)]), template)
