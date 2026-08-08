"""T17 drift guard: metrics-service must read only the v_metrics_* views, never
raw za_ tables. This keeps the view layer as the schema contract — a raw-table
query added later fails here in CI instead of silently coupling to another
service's schema."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
METRICS_MAIN = ROOT / "services/metrics-service/app/main.py"


def test_metrics_reads_only_from_views():
    src = METRICS_MAIN.read_text()
    targets = re.findall(r"(?:FROM|JOIN)\s+([A-Za-z_][A-Za-z0-9_]*)", src)
    raw_tables = sorted({t for t in targets if t.startswith("za_")})
    assert raw_tables == [], (
        "metrics-service must query v_metrics_* views, not raw tables. "
        f"Found raw table references: {raw_tables}"
    )


def test_metrics_actually_uses_views():
    # Sanity: the views are in fact referenced (guards against the regex silently
    # matching nothing if the query style changes).
    src = METRICS_MAIN.read_text()
    assert "v_metrics_" in src
