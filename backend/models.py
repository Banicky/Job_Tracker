from datetime import datetime, timezone

from sqlalchemy import JSON, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db import Base


# All datetimes are stored as naive UTC; SQLite drops tzinfo anyway.
def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    google_sub: Mapped[str] = mapped_column(String, unique=True)
    email: Mapped[str]
    name: Mapped[str | None]
    picture: Mapped[str | None]

    # Fernet-encrypted; see crypto.py
    access_token: Mapped[str | None]
    refresh_token: Mapped[str | None]
    token_expires_at: Mapped[int | None]  # unix seconds

    last_synced_at: Mapped[datetime | None]
    backfill_completed_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class Application(Base):
    __tablename__ = "applications"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    company: Mapped[str]
    role: Mapped[str | None]
    level: Mapped[str | None]  # intern, junior, mid, senior, staff, ...
    ats: Mapped[str | None]  # greenhouse, lever, workday, ...
    source: Mapped[str | None]  # manual: linkedin, company_site, referral, recruiter

    # applied | assessment | interviewing | offer | rejected
    status: Mapped[str] = mapped_column(default="applied")
    applied_at: Mapped[datetime | None]
    last_activity_at: Mapped[datetime | None]
    rejected_at: Mapped[datetime | None]
    rejection_stage: Mapped[str | None]  # status the application was in when rejected

    # Set when the user edits by hand; automatic updates must not overwrite.
    user_edited: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    events: Mapped[list["Event"]] = relationship(
        back_populates="application", order_by="Event.occurred_at", cascade="all, delete-orphan"
    )


class Email(Base):
    __tablename__ = "emails"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    gmail_message_id: Mapped[str] = mapped_column(String, unique=True)
    thread_id: Mapped[str] = mapped_column(String, index=True)
    application_id: Mapped[int | None] = mapped_column(
        ForeignKey("applications.id", ondelete="SET NULL"), index=True
    )

    received_at: Mapped[datetime] = mapped_column(index=True)
    sender: Mapped[str | None]
    subject: Mapped[str | None]
    snippet: Mapped[str | None]
    body_text: Mapped[str | None] = mapped_column(Text)

    # Filled by the classifier. processed_at is None until classified.
    # type: application_confirmation | rejection | interview_request | assessment
    #       | offer | recruiter_outreach | other | not_job_related
    type: Mapped[str | None]
    extracted_json: Mapped[dict | None] = mapped_column(JSON)
    confidence: Mapped[float | None]
    processed_at: Mapped[datetime | None] = mapped_column(index=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"), index=True
    )
    email_id: Mapped[int | None] = mapped_column(ForeignKey("emails.id", ondelete="SET NULL"))
    # Same vocabulary as Email.type, plus "manual" for hand-entered events.
    type: Mapped[str]
    occurred_at: Mapped[datetime]

    application: Mapped[Application] = relationship(back_populates="events")
