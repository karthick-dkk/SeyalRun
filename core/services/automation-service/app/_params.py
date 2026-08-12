"""Caller-param allowlisting for job runs (T5 / closes the T11 injection path).

Job runs can be triggered from three caller-controlled places: the UI
(`/job-templates/{id}/run`), a Zabbix webhook (`/internal/job-runs`, the
*untrusted* path — the event payload is attacker-influenceable), and reruns.
A valid HMAC authenticates the *sender* of a webhook, not the *safety of its
content*, so caller-supplied params must never be able to choose the code that
runs on a host.

Two rules enforced here:

  1. Reserved keys (script_content, playbook_path) are TEMPLATE-ONLY. A caller
     may never supply them — they are always taken from the job template. This
     is the rule that closes the command-injection path: previously
     ``tmpl.script_content or params.get("script_content", "")`` let a caller
     inject the bash to run when a template had no script of its own.

  2. Every other caller key must be in the template's allowlist
     (``allowed_param_keys`` ∪ ``default_params`` keys ∪ declared survey
     fields). Unknown keys are rejected, not silently dropped, so a misconfigured
     trigger fails loudly instead of running with unexpected input.

Internal control keys (``_``-prefixed, e.g. ``_credential_id``) are set by the
trusted backend, never accepted from a caller here.
"""

from __future__ import annotations

# Template-controlled keys a caller may never set. These select the actual
# code/playbook executed on target hosts.
RESERVED_PARAM_KEYS = frozenset({"script_content", "playbook_path"})


class ParamNotAllowedError(ValueError):
    """Raised when caller params contain reserved or non-allowlisted keys."""


def _survey_field_names(survey_schema: object) -> set[str]:
    """Best-effort extraction of declared survey field names.

    Survey schemas are free-form JSON; we look for the common shapes
    (a list under ``fields`` or ``spec``, each item naming a ``name`` or
    ``variable``). Anything we cannot parse simply contributes no keys.
    """
    names: set[str] = set()
    if not isinstance(survey_schema, dict):
        return names
    for container in ("fields", "spec", "questions"):
        items = survey_schema.get(container)
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict):
                    key = item.get("name") or item.get("variable")
                    if isinstance(key, str) and key:
                        names.add(key)
    return names


def allowed_param_keys_for(tmpl) -> set[str]:
    """The set of param keys a caller may supply for this template."""
    allowed: set[str] = set(tmpl.allowed_param_keys or [])
    allowed |= set((tmpl.default_params or {}).keys())
    allowed |= _survey_field_names(tmpl.survey_schema)
    allowed -= RESERVED_PARAM_KEYS  # reserved keys are never caller-allowable
    return allowed


def filter_caller_params(tmpl, supplied: dict | None) -> dict:
    """Return caller params restricted to the template allowlist.

    Raises :class:`ParamNotAllowedError` if any reserved key, internal control
    key, or non-allowlisted key is present.
    """
    supplied = supplied or {}

    reserved = sorted(k for k in supplied if k in RESERVED_PARAM_KEYS)
    if reserved:
        raise ParamNotAllowedError(
            "params may not set template-controlled key(s): " + ", ".join(reserved)
        )

    control = sorted(k for k in supplied if k.startswith("_"))
    if control:
        raise ParamNotAllowedError(
            "params may not set internal control key(s): " + ", ".join(control)
        )

    allowed = allowed_param_keys_for(tmpl)
    unknown = sorted(k for k in supplied if k not in allowed)
    if unknown:
        raise ParamNotAllowedError(
            "params not in template allowlist: " + ", ".join(unknown)
        )

    return dict(supplied)


def template_code_params(tmpl) -> dict:
    """The template-controlled execution params (always template-sourced)."""
    code: dict = {}
    if tmpl.script_content:
        code["script_content"] = tmpl.script_content
    if tmpl.playbook_path:
        code["playbook_path"] = tmpl.playbook_path
    return code
