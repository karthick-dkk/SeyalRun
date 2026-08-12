"""PCI DSS Phase A — admin/superadmin JIT elevation (Azure-PIM/sudo-timeout style).

X-Elevated-Until is set by api-gateway from the caller's own server-side Redis
state (identity-service/app/sessions.py::elevate, read live in
api-gateway/app/security.py) — never client-controlled. A present, unexpired
value is trustworthy proof of a re-proved-MFA elevation window. Shared by every
downstream service that offers the elevation fallback (terminal-service's
SSH-connect gate, inventory-service's credential-reveal gate) so the two never
drift out of sync on what counts as "active".
"""

from __future__ import annotations

import time


def elevation_active(elevated_until: str) -> bool:
    if not elevated_until or not elevated_until.isdigit():
        return False
    return int(elevated_until) > int(time.time())
