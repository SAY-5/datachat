"""Thin Store wrapper over SQLAlchemy 2."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from . import models


def _user_question_for(s: Session, asst: models.Message) -> str | None:
    """Walk back from an assistant message to the prior user message
    in the same session. Useful in the gallery view: 'this chart
    answered: <question>'."""
    stmt = (
        select(models.Message)
        .where(models.Message.session_id == asst.session_id)
        .where(models.Message.created_at < asst.created_at)
        .where(models.Message.role == "user")
        .order_by(models.Message.created_at.desc())
        .limit(1)
    )
    prev = s.execute(stmt).scalar_one_or_none()
    return prev.content if prev is not None else None


@dataclass
class Store:
    """Simple repo-style wrapper. The store is held by the FastAPI
    app for the lifetime of the process; sessions are short-lived."""

    dsn: str

    def __post_init__(self) -> None:
        connect_args: dict[str, object] = {}
        if self.dsn.startswith("sqlite"):
            connect_args["check_same_thread"] = False
        self.engine = create_engine(self.dsn, connect_args=connect_args, future=True)
        self._mk = sessionmaker(self.engine, expire_on_commit=False, class_=Session)
        models.Base.metadata.create_all(self.engine)

    @contextmanager
    def session(self) -> Iterator[Session]:
        s = self._mk()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    # -- session lifecycle ------------------------------------------------

    def create_session(self, dataset: str | None, title: str | None) -> models.Session:
        with self.session() as s:
            sess = models.Session(dataset=dataset, title=title)
            s.add(sess)
            s.flush()
            s.refresh(sess)
            s.expunge(sess)
            return sess

    def list_sessions(self, limit: int = 50) -> list[models.Session]:
        with self.session() as s:
            stmt = select(models.Session).order_by(
                models.Session.created_at.desc()
            ).limit(limit)
            rows = list(s.execute(stmt).scalars().all())
            for r in rows:
                s.expunge(r)
            return rows

    def get_session(self, sid: str) -> models.Session | None:
        with self.session() as s:
            obj = s.get(models.Session, sid)
            if obj is None:
                return None
            # Eagerly access messages so they're loaded before expunge.
            _ = list(obj.messages)
            for m in obj.messages:
                s.expunge(m)
            s.expunge(obj)
            return obj

    # -- messages ---------------------------------------------------------

    def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        *,
        code: str | None = None,
        figure_json: str | None = None,
        elapsed_ms: int = 0,
        status: str = "ok",
    ) -> models.Message:
        with self.session() as s:
            m = models.Message(
                session_id=session_id, role=role, content=content,
                code=code, figure_json=figure_json,
                elapsed_ms=elapsed_ms, status=status,
            )
            s.add(m)
            s.flush()
            s.refresh(m)
            s.expunge(m)
            return m

    # -- branching --------------------------------------------------------

    def fork_session(
        self,
        source_id: str,
        up_to_message_id: str,
        *,
        title: str | None = None,
    ) -> models.Session:
        """Create a new Session that copies messages of `source_id` up
        to and including `up_to_message_id`. The new session inherits
        the dataset and stores `forked_from_session_id` +
        `forked_at_message_id` so the UI can render the lineage.

        Used for 'try a different question from here' — the user picks
        any past message, forks at it, and asks the assistant something
        else without disturbing the original thread.

        Raises LookupError if source is missing or anchor doesn't
        belong to source."""
        with self.session() as s:
            src = s.get(models.Session, source_id)
            if src is None:
                raise LookupError(f"source session {source_id!r} not found")
            anchor = s.get(models.Message, up_to_message_id)
            if anchor is None or anchor.session_id != source_id:
                raise LookupError(
                    f"anchor {up_to_message_id!r} not in session {source_id!r}"
                )
            stmt = (
                select(models.Message)
                .where(models.Message.session_id == source_id)
                .where(models.Message.created_at <= anchor.created_at)
                .order_by(models.Message.created_at)
            )
            originals = list(s.execute(stmt).scalars().all())
            new = models.Session(
                dataset=src.dataset,
                title=title or (src.title or "branch"),
                forked_from_session_id=src.id,
                forked_at_message_id=anchor.id,
            )
            s.add(new)
            s.flush()
            for m in originals:
                s.add(models.Message(
                    session_id=new.id,
                    role=m.role,
                    content=m.content,
                    code=m.code,
                    figure_json=m.figure_json,
                    tokens_in=m.tokens_in,
                    tokens_out=m.tokens_out,
                    elapsed_ms=m.elapsed_ms,
                    status=m.status,
                ))
            s.flush()
            s.refresh(new)
            _ = list(new.messages)
            for m in new.messages:
                s.expunge(m)
            s.expunge(new)
            return new

    # -- pinned charts ----------------------------------------------------

    def pin_chart(
        self,
        session_id: str,
        message_id: str,
        *,
        title: str | None = None,
        note: str | None = None,
    ) -> models.PinnedChart:
        """Pin an assistant message's figure to the gallery. Does not
        copy the figure — the gallery view joins back to messages.
        Idempotent on (message_id) — pinning twice updates title/note."""
        with self.session() as s:
            existing = s.execute(
                select(models.PinnedChart).where(models.PinnedChart.message_id == message_id)
            ).scalar_one_or_none()
            if existing is not None:
                if title is not None:
                    existing.title = title
                if note is not None:
                    existing.note = note
                s.add(existing)
                s.flush()
                s.refresh(existing)
                s.expunge(existing)
                return existing
            msg = s.get(models.Message, message_id)
            if msg is None or msg.session_id != session_id:
                raise LookupError(f"message {message_id!r} not in session {session_id!r}")
            if not msg.figure_json:
                raise LookupError(f"message {message_id!r} has no figure to pin")
            pin = models.PinnedChart(
                session_id=session_id,
                message_id=message_id,
                title=title,
                note=note,
            )
            s.add(pin)
            s.flush()
            s.refresh(pin)
            s.expunge(pin)
            return pin

    def unpin_chart(self, pin_id: str) -> bool:
        with self.session() as s:
            row = s.get(models.PinnedChart, pin_id)
            if row is None:
                return False
            s.delete(row)
            return True

    def list_pinned(self, session_id: str | None = None,
                    limit: int = 100) -> list[dict]:
        """Return pins joined with their message's figure_json + dataset.
        Returned as plain dicts so the API can JSON them straight back."""
        with self.session() as s:
            stmt = select(
                models.PinnedChart, models.Message, models.Session,
            ).join(
                models.Message, models.PinnedChart.message_id == models.Message.id,
            ).join(
                models.Session, models.PinnedChart.session_id == models.Session.id,
            ).order_by(models.PinnedChart.created_at.desc()).limit(limit)
            if session_id is not None:
                stmt = stmt.where(models.PinnedChart.session_id == session_id)
            out: list[dict] = []
            for pin, msg, sess in s.execute(stmt).all():
                out.append({
                    "id": pin.id,
                    "session_id": pin.session_id,
                    "message_id": pin.message_id,
                    "title": pin.title,
                    "note": pin.note,
                    "created_at": pin.created_at.isoformat(),
                    "session_title": sess.title,
                    "dataset": sess.dataset,
                    "figure_json": msg.figure_json,
                    "code": msg.code,
                    "user_question": _user_question_for(s, msg),
                })
            return out

    # -- runs -------------------------------------------------------------

    def add_run(
        self,
        *,
        message_id: str,
        exit_code: int,
        elapsed_ms: int,
        peak_mem_bytes: int,
        stdout: str,
        stderr: str,
        error_class: str | None,
    ) -> None:
        with self.session() as s:
            s.add(models.Run(
                message_id=message_id, exit_code=exit_code,
                elapsed_ms=elapsed_ms, peak_mem_bytes=peak_mem_bytes,
                stdout_truncated=stdout, stderr_truncated=stderr,
                error_class=error_class,
            ))
