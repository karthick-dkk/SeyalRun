from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import JSON

from libs.dbcore import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return str(uuid.uuid4())


class ZAZabbixTriggerBinding(Base):
    __tablename__ = "za_zabbix_trigger_bindings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    job_template_id: Mapped[str] = mapped_column(String(36), nullable=False)
    zabbix_triggerid: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # Cached at bind-time from the search picker's selected label, so the list
    # view and the edit modal can show a human-readable name without a live
    # Zabbix lookup (which would fail silently if the trigger was since deleted).
    zabbix_trigger_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    zabbix_host_group: Mapped[str | None] = mapped_column(String(200), nullable=True)
    severity_min: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    target_scope: Mapped[str] = mapped_column(String(20), nullable=False, default="affected_host")
    post_result_to_zabbix: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    extra_vars: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Author / last editor — sent with each dispatch so neither can approve a
    # production run their own binding produced (F-2). Null for older rows.
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # Append-only: every user who has ever changed it. Never shrunk, so a later
    # edit by someone else cannot free an earlier editor to approve.
    editor_ids: Mapped[list | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


def with_editor(editor_ids: list | None, user_id: str) -> list[str]:
    """editor_ids with user_id appended once — never removes anyone."""
    ids = list(editor_ids or [])
    if user_id and user_id not in ids:
        ids.append(user_id)
    return ids


def binding_accountable_ids(binding: "ZAZabbixTriggerBinding") -> list[str]:
    """Who configured this binding — barred from approving the runs it produces."""
    out: list[str] = []
    for u in [getattr(binding, "created_by", None), *(getattr(binding, "editor_ids", None) or [])]:
        if u and u not in out:
            out.append(u)
    return out
