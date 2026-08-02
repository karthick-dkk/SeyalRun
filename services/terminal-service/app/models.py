from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def _uid() -> str:
    return str(uuid.uuid4())


class ZASSHSession(Base):
    __tablename__ = "za_ssh_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uid)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    username: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    host_id: Mapped[str] = mapped_column(String(36), nullable=False)
    host_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    credential_id: Mapped[str] = mapped_column(String(36), nullable=False)
    gateway_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    client_ip: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # PCI DSS Phase A — set when this session was created via the admin/superadmin
    # JIT elevation fallback (no ZAAuthorization grant existed for this host) rather
    # than an ordinary PAM-authorized connect. reviewed_at/reviewed_by let an admin
    # sign off after the fact — this IS the break-glass review trail, not a separate
    # subsystem.
    elevation_used: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reviewed_by: Mapped[str | None] = mapped_column(String(36), nullable=True)

    commands: Mapped[list["ZASessionCommand"]] = relationship(
        "ZASessionCommand", back_populates="session", lazy="select"
    )

    __table_args__ = (
        Index("ix_za_ssh_sessions_user_id", "user_id"),
        Index("ix_za_ssh_sessions_host_id", "host_id"),
    )


class ZASessionCommand(Base):
    __tablename__ = "za_session_commands"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uid)
    session_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("za_ssh_sessions.id", ondelete="CASCADE"), nullable=False
    )
    command_text: Mapped[str] = mapped_column(Text, nullable=False)
    matched_filter_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    action: Mapped[str] = mapped_column(String(20), nullable=False, default="logged")
    executed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    session: Mapped["ZASSHSession"] = relationship("ZASSHSession", back_populates="commands")

    __table_args__ = (Index("ix_za_session_commands_session_id", "session_id"),)
