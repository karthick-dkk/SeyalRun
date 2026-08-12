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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
