"""Plugin metadata for the Integration UI.

``discover_plugins`` answers "what can this service use"; this answers "what
should an admin SEE, and may they turn it off". They are deliberately separate:
discovery is a hot-path startup concern, this is a human-facing description.

The axis catalog is the load-bearing part. ``togglable`` is not cosmetic — an
axis marked False has no off switch anywhere in the API, because disabling it
would not be a configuration change but an outage:

  * ``kms`` unwraps the vault DEK. Disabling the key provider makes every stored
    credential undecryptable — the vault is still there, and nothing can read it.
  * ``credentials`` defines what a stored secret *is*. Disabling a kind orphans
    every credential of that kind mid-session.

So those two are reported as built-in and always on. The axes that genuinely are
a deployment choice (which IdP you trust, which executors may run, which trigger
sources are accepted, who brokers sessions) are the ones an admin can switch.
"""

from __future__ import annotations

import inspect


# axis -> (label, togglable, why-not if not togglable)
AXES: dict[str, tuple[str, bool, str]] = {
    "idp": ("Identity providers", True, ""),
    "executors": ("Automation executors", True, ""),
    "triggers": ("Trigger sources", True, ""),
    "brokers": ("Session brokers", True, ""),
    "command_filters": ("Command-filter matchers", True, ""),
    "credentials": ("Credential kinds", False,
                    "Disabling a credential kind would orphan every stored credential of that kind."),
    "kms": ("Key providers", False,
            "The key provider unwraps the vault key — disabling it makes every stored credential unreadable."),
}


def _summary(obj: object) -> str:
    """First non-empty line of the class docstring, for the plugin list."""
    doc = inspect.getdoc(type(obj)) or ""
    for line in doc.splitlines():
        if line.strip():
            return line.strip()
    return ""


def describe_plugins(axis: str, registry: dict, service: str) -> list[dict]:
    """Describe one axis' discovered plugins for the Integration page.

    ``registry`` is exactly what :func:`discover_plugins` returned, so this adds
    no second source of truth: a plugin that is not loaded cannot be described,
    and one that is loaded is always described.
    """
    label, togglable, locked_reason = AXES.get(axis, (axis, True, ""))
    out = []
    for name, instance in sorted(registry.items()):
        out.append({
            "axis": axis,
            "axis_label": label,
            "name": name,
            "service": service,
            "module": type(instance).__module__,
            "class": type(instance).__name__,
            "summary": _summary(instance),
            "togglable": togglable,
            "locked_reason": locked_reason,
        })
    return out
