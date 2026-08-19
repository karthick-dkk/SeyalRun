"""Every Model.attribute referenced in service code must exist on that model.

Found the hard way. The credential history cap ordered by
`ZACredentialHistory.created_at`; the column is `rotated_at`. Python resolves a
class attribute at call time, so this is not a syntax error and not an import
error — it raises AttributeError deep inside the request, the transaction rolls
back, and rotation returns 500 having archived nothing. The feature the cap was
written to provide had therefore never worked once.

It survived review because the tests covering it were source-shape checks that
asserted SECRET_HISTORY_KEEP existed and that delete(ZACredentialHistory) was
called — both true, neither executed. A test that greps for the presence of code
cannot tell you the code runs. This one resolves the names.

Static, so it needs no database: parse each service's models.py for its declared
columns and relationships, then parse the service's own modules for
`SomeModel.attr` and check membership. That covers the whole class of typo, not
just the one instance.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SERVICES = ROOT / "services"

# SQLAlchemy adds these to every mapped class; they are not declared columns.
_SA_BUILTINS = {
    "metadata", "registry", "__table__", "__tablename__", "__mapper__", "query",
    "c", "columns",
}
# Attributes that exist on a Column/InstrumentedAttribute rather than the model.
_COLUMN_OPS = {
    "desc", "asc", "in_", "notin_", "is_", "isnot", "like", "ilike", "label",
    "any", "has", "contains", "between", "startswith", "endswith", "distinct",
    "nulls_first", "nulls_last", "op", "cast", "concat", "collate", "self_group",
}


def _models_for(service: Path) -> dict[str, set[str]]:
    """model class name -> declared attribute names, from that service's models.py"""
    models_py = service / "app/models.py"
    if not models_py.exists():
        return {}
    tree = ast.parse(models_py.read_text())
    out: dict[str, set[str]] = {}
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        attrs: set[str] = set()
        for stmt in node.body:
            # `name: Mapped[...] = mapped_column(...)` and plain assignments
            if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                attrs.add(stmt.target.id)
            elif isinstance(stmt, ast.Assign):
                attrs |= {t.id for t in stmt.targets if isinstance(t, ast.Name)}
            # @property / regular methods are legitimate attribute access too
            elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                attrs.add(stmt.name)
        if attrs:
            out[node.name] = attrs | _SA_BUILTINS
    return out


def _service_dirs() -> list[Path]:
    return sorted(d for d in SERVICES.iterdir() if (d / "app/models.py").exists())


def test_at_least_one_service_has_models():
    """Guard against the parametrisation below being empty and passing vacuously."""
    services = _service_dirs()
    assert services, "no services with models.py found"
    inv = SERVICES / "inventory-service"
    assert "ZACredentialHistory" in _models_for(inv), "inventory-service models did not parse"


@pytest.mark.parametrize("service", _service_dirs(), ids=lambda p: p.name)
def test_every_model_attribute_reference_resolves(service: Path):
    models = _models_for(service)
    if not models:
        pytest.skip(f"{service.name} declares no models")

    bad: list[str] = []
    for py in (service / "app").rglob("*.py"):
        try:
            tree = ast.parse(py.read_text())
        except SyntaxError:                      # not our problem here
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute) or not isinstance(node.value, ast.Name):
                continue
            model, attr = node.value.id, node.attr
            if model not in models or attr in _COLUMN_OPS or attr.startswith("__"):
                continue
            if attr not in models[model]:
                rel = py.relative_to(SERVICES)
                bad.append(f"{rel}:{node.lineno}: {model}.{attr} is not a column on {model}")

    assert not bad, (
        "these resolve at call time and raise AttributeError inside the request, "
        "rolling the transaction back — a 500 with no other symptom:\n  "
        + "\n  ".join(bad)
    )
