from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Float, Integer, String, Text
from sqlalchemy.dialects.mysql import JSON as MySQLJSON
from sqlalchemy.types import JSON

from libs.dbcore import Base


class ZARecording(Base):
    __tablename__ = "za_recordings"

    id: str = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    session_id: str = Column(String(36), nullable=False, unique=True)
    format: str = Column(String(20), nullable=False, default="frames_v1")
    frames = Column(JSON, nullable=False, default=list)
    duration_seconds: float = Column(Float, nullable=False, default=0.0)
    size_bytes: int = Column(Integer, nullable=False, default=0)
    storage_location: str = Column(String(20), nullable=False, default="local")  # local|s3|elasticsearch (v1.1)
    storage_key: str = Column(Text, nullable=False, default="")  # S3 key / ES doc id when tiered (v1.1)
    tiered_at = Column(DateTime(timezone=True), nullable=True)  # v1.1
    created_at: datetime = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
