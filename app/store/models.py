"""SQLAlchemy models. Used unchanged on SQLite (default) and Postgres."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import Column, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, relationship
from sqlalchemy.types import DateTime


def _now() -> datetime:
    return datetime.now(UTC)


def _new_id() -> str:
    return uuid.uuid4().hex


class Base(DeclarativeBase):
    pass


class Session(Base):
    __tablename__ = "sessions"
    id = Column(String(32), primary_key=True, default=_new_id)
    created_at = Column(DateTime(timezone=True), default=_now, nullable=False)
    dataset = Column(String(64), nullable=True)
    title = Column(String(256), nullable=True)
    # Branching: forked sessions point at the session they were copied
    # from, plus the message at which the fork happened. NULL on the
    # original session.
    forked_from_session_id = Column(String(32), ForeignKey("sessions.id"), nullable=True)
    forked_at_message_id   = Column(String(32), nullable=True)
    messages = relationship(
        "Message", back_populates="session",
        order_by="Message.created_at", cascade="all,delete-orphan",
    )


class Message(Base):
    __tablename__ = "messages"
    id = Column(String(32), primary_key=True, default=_new_id)
    session_id = Column(String(32), ForeignKey("sessions.id", ondelete="CASCADE"),
                        nullable=False)
    role = Column(String(16), nullable=False)
    content = Column(Text, nullable=False)
    code = Column(Text, nullable=True)
    figure_json = Column(Text, nullable=True)
    tokens_in = Column(Integer, default=0, nullable=False)
    tokens_out = Column(Integer, default=0, nullable=False)
    elapsed_ms = Column(Integer, default=0, nullable=False)
    status = Column(String(16), default="ok", nullable=False)
    created_at = Column(DateTime(timezone=True), default=_now, nullable=False)
    session = relationship("Session", back_populates="messages")


class Run(Base):
    __tablename__ = "runs"
    id = Column(String(32), primary_key=True, default=_new_id)
    message_id = Column(String(32), ForeignKey("messages.id", ondelete="CASCADE"),
                        nullable=False)
    exit_code = Column(Integer, default=0, nullable=False)
    elapsed_ms = Column(Integer, default=0, nullable=False)
    peak_mem_bytes = Column(Integer, default=0, nullable=False)
    stdout_truncated = Column(Text, nullable=True)
    stderr_truncated = Column(Text, nullable=True)
    error_class = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_now, nullable=False)


Index("ix_messages_session_created", Message.session_id, Message.created_at)
Index("ix_sessions_created_desc", Session.created_at.desc())
