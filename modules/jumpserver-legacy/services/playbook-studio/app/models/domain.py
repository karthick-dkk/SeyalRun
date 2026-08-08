"""SQLAlchemy ORM models for playbook-studio."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..database import Base


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


class Playbook(Base):
    __tablename__ = "playbooks"
    __table_args__ = {"schema": "playbook_studio"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String(50), default="system")
    tags: Mapped[list] = mapped_column(JSONB, default=list)
    tasks: Mapped[list] = mapped_column(JSONB, default=list)
    variables: Mapped[list] = mapped_column(JSONB, default=list)
    yaml_cache: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_template: Mapped[bool] = mapped_column(Boolean, default=False)
    source_template_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_by: Mapped[str] = mapped_column(String(100), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    jobs: Mapped[list[Job]] = relationship("Job", back_populates="playbook", lazy="select")


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = {"schema": "playbook_studio"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    playbook_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("playbook_studio.playbooks.id", ondelete="SET NULL"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(String(20), default="pending")
    triggered_by: Mapped[str] = mapped_column(String(100), default="")
    ab_job_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    inventory_selector: Mapped[str] = mapped_column(String(500), default="all")
    extra_vars: Mapped[dict] = mapped_column(JSONB, default=dict)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_lines: Mapped[list] = mapped_column(JSONB, default=list)
    yaml_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    playbook: Mapped[Playbook | None] = relationship("Playbook", back_populates="jobs")


class AlertRule(Base):
    __tablename__ = "alert_rules"
    __table_args__ = {"schema": "playbook_studio"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    conditions: Mapped[dict] = mapped_column(JSONB, default=dict)
    channels: Mapped[list] = mapped_column(JSONB, default=list)
    created_by: Mapped[str] = mapped_column(String(100), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    history: Mapped[list[AlertHistory]] = relationship(
        "AlertHistory", back_populates="rule", lazy="select"
    )


class AlertHistory(Base):
    __tablename__ = "alert_history"
    __table_args__ = {"schema": "playbook_studio"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    rule_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("playbook_studio.alert_rules.id", ondelete="SET NULL"),
        nullable=True,
    )
    rule_name: Mapped[str] = mapped_column(String(200), default="")
    event_type: Mapped[str] = mapped_column(String(50), default="")
    event_payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    channels_tried: Mapped[list] = mapped_column(JSONB, default=list)
    delivery_status: Mapped[str] = mapped_column(String(20), default="pending")
    delivered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)

    rule: Mapped[AlertRule | None] = relationship("AlertRule", back_populates="history")


class SSHSession(Base):
    __tablename__ = "ssh_sessions"
    __table_args__ = {"schema": "playbook_studio"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    user: Mapped[str] = mapped_column(String(100), nullable=False)
    asset_id: Mapped[str] = mapped_column(String(100), nullable=True)
    asset_name: Mapped[str] = mapped_column(String(200), default="")
    asset_address: Mapped[str] = mapped_column(String(255), nullable=False)
    ssh_username: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), default="active"
    )  # active | idle | closed | error
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), server_default=func.now()
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    recording: Mapped[list] = mapped_column(JSONB, default=list)  # [{t: float, d: str}]
    command_count: Mapped[int] = mapped_column(Integer, default=0)
    exit_reason: Mapped[str] = mapped_column(String(200), default="")


class NotificationChannel(Base):
    __tablename__ = "notification_channels"
    __table_args__ = {"schema": "playbook_studio"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    channel_type: Mapped[str] = mapped_column(String(20), nullable=False)
    config: Mapped[dict] = mapped_column(JSONB, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
