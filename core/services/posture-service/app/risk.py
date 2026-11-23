"""Severity normalisation, the risk score and feed staleness. Stdlib only.

Risk score (0-100), the formula the Findings page sorts by:

    base   = CVSS base score (0-10) if any source gave one, else the severity's
             representative score: critical 9.5, high 7.5, medium 5.0,
             low 2.5, unknown 5.0 (unknown is NOT treated as harmless).
    p      = EPSS percentile (0-1) from the feed snapshot, else 0.5 — a CVE
             EPSS has not scored yet (often a brand-new one) is neither
             promoted nor buried.
    score  = round(100 * (0.6 * base / 10 + 0.4 * p))
    KEV    : a CVE in CISA's Known Exploited Vulnerabilities catalog scores
             at least 90 — "exploited in the wild" outranks any estimate.
    result is clamped to 0..100.

So severity carries 60 % and likelihood of exploitation 40 %: a CVSS 9.8 nobody
exploits (p 0.05) scores 61, a CVSS 7.5 at the 99th EPSS percentile scores 85,
and either one in KEV scores >= 90.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime

SEVERITIES = ("critical", "high", "medium", "low", "unknown")
_RANK = {s: i for i, s in enumerate(reversed(SEVERITIES))}  # unknown 0 .. critical 4
SEVERITY_BASE = {"critical": 9.5, "high": 7.5, "medium": 5.0, "low": 2.5, "unknown": 5.0}
EPSS_NEUTRAL = 0.5
KEV_FLOOR = 90

_ALIASES = {
    "critical": "critical", "crit": "critical", "urgent": "critical",
    "high": "high", "important": "high", "error": "high", "severe": "high",
    "medium": "medium", "moderate": "medium", "warning": "medium", "med": "medium",
    "low": "low", "minor": "low", "negligible": "low", "unimportant": "low", "note": "low",
    "info": "low", "informational": "low",
}


def severity_from_cvss(cvss: float | None) -> str:
    """CVSS v3 qualitative bands."""
    if cvss is None:
        return "unknown"
    if cvss >= 9.0:
        return "critical"
    if cvss >= 7.0:
        return "high"
    if cvss >= 4.0:
        return "medium"
    if cvss > 0.0:
        return "low"
    return "unknown"


def normalise_severity(raw: str | None, cvss: float | None = None) -> str:
    """Vendor wording (Red Hat 'Important', Debian 'unimportant', SARIF 'error')
    -> critical|high|medium|low|unknown. Falls back to the CVSS band."""
    key = (raw or "").strip().lower()
    sev = _ALIASES.get(key)
    if sev is None and key:
        for word, mapped in _ALIASES.items():
            if key.startswith(word):
                sev = mapped
                break
    if sev is None:
        sev = severity_from_cvss(cvss)
    return sev


def worse(a: str, b: str) -> str:
    """The more severe of two normalised severities (unknown loses to anything)."""
    return a if _RANK.get(a, 0) >= _RANK.get(b, 0) else b


def clean_cvss(value) -> float | None:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(v) or not (0.0 <= v <= 10.0):
        return None
    return round(v, 1)


def risk_score(cvss: float | None, severity: str, epss_percentile: float | None, kev: bool) -> int:
    base = cvss if cvss is not None else SEVERITY_BASE.get(severity, SEVERITY_BASE["unknown"])
    p = epss_percentile if epss_percentile is not None else EPSS_NEUTRAL
    p = min(1.0, max(0.0, p))
    score = round(100 * (0.6 * base / 10.0 + 0.4 * p))
    if kev:
        score = max(score, KEV_FLOOR)
    return int(min(100, max(0, score)))


def feed_age_days(generated_at: datetime | None, now: datetime | None = None) -> int | None:
    if generated_at is None:
        return None
    if generated_at.tzinfo is None:
        generated_at = generated_at.replace(tzinfo=UTC)
    return max(0, ((now or datetime.now(UTC)) - generated_at).days)


def feed_stale(age_days: int | None, stale_after_days: int) -> bool:
    """No active feed at all counts as stale: there is nothing current to match
    or score against, and the page should say so."""
    return age_days is None or age_days > stale_after_days
