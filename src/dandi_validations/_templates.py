"""Minimal ``{{ name }}`` templating that leaves GitHub Actions expressions (``${{ ... }}``) alone."""

import re

_PLACEHOLDER_RE = re.compile(r"(?<!\$)\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


def placeholders(template: str) -> set[str]:
    return set(_PLACEHOLDER_RE.findall(template))


def render(template: str, **values: object) -> str:
    """
    Substitute every ``{{ name }}`` placeholder.

    Every placeholder must be given a value and every value must be used, so that a template and
    the code that fills it cannot drift apart silently.
    """
    names = placeholders(template)
    missing = names - values.keys()
    if missing:
        message = f"No value given for template placeholders: {sorted(missing)}."
        raise KeyError(message)
    unused = values.keys() - names
    if unused:
        message = f"Values given for placeholders the template does not have: {sorted(unused)}."
        raise KeyError(message)
    return _PLACEHOLDER_RE.sub(lambda match: str(values[match.group(1)]), template)
