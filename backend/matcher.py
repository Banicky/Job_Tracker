"""Groups classified job emails into applications and derives each one's status.

Matching order for each unassigned email (oldest first):
  1. Same Gmail thread as an already-assigned email.
  2. Same normalized company, then same (or containing) normalized role.
  3. Same company, email has no role: the company's most recent open application.
Emails that don't match can start a new application if their type implies one.
"""

import logging
import re
from datetime import timedelta

from sqlalchemy import delete, select, update

from db import SessionLocal
from models import Application, Email, Event

log = logging.getLogger(__name__)

# Types that belong on an application's timeline.
APPLICATION_TYPES = {"application_confirmation", "rejection", "interview_request", "assessment", "offer", "other"}
# Types that imply an application exists even without a confirmation email.
CREATES_APPLICATION = APPLICATION_TYPES - {"other"}
# Pipeline order; status is the furthest stage reached (or rejected).
STAGE_BY_TYPE = {"application_confirmation": "applied", "assessment": "assessment",
                 "interview_request": "interviewing", "offer": "offer"}
STAGE_ORDER = ["applied", "assessment", "interviewing", "offer"]
# A new confirmation this long after a rejection is treated as a re-application.
REAPPLY_AFTER = timedelta(days=30)

_COMPANY_SUFFIXES = re.compile(r"\b(inc|llc|ltd|limited|corp|corporation|co|company|gmbh|plc|the)\b")
_JOB_ID = re.compile(r"\b[a-z]*\d[\w-]{3,}\b")  # R12345, JR-00981, 2026-1234


def normalize_company(name: str | None) -> str | None:
    if not name:
        return None
    s = _COMPANY_SUFFIXES.sub(" ", name.lower().replace("&", " and "))
    s = re.sub(r"[^a-z0-9]+", "", s)
    return s or None


def normalize_role(role: str | None) -> str | None:
    if not role:
        return None
    s = re.sub(r"[(\[].*?[)\]]", " ", role.lower())  # drop "(Remote)", "[R123]"
    s = _JOB_ID.sub(" ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s).strip()
    return s or None


def _roles_match(a: str | None, b: str | None) -> bool:
    return bool(a and b and (a == b or a in b or b in a))


def match_pending(user_id: int) -> int:
    """Assigns every classified, unassigned job email. Returns how many were assigned."""
    with SessionLocal() as db:
        emails = db.scalars(
            select(Email).where(
                Email.user_id == user_id, Email.application_id.is_(None),
                Email.processed_at.is_not(None), Email.type.in_(APPLICATION_TYPES),
            ).order_by(Email.received_at)
        ).all()
        if not emails:
            return 0

        apps = db.scalars(select(Application).where(Application.user_id == user_id)).all()
        by_company: dict[str, list[Application]] = {}
        for app in apps:
            by_company.setdefault(normalize_company(app.company), []).append(app)
        thread_app = dict(db.execute(
            select(Email.thread_id, Email.application_id)
            .where(Email.user_id == user_id, Email.application_id.is_not(None))
        ).all())
        apps_by_id = {a.id: a for a in apps}

        assigned = 0
        for email in emails:
            data = email.extracted_json or {}
            company_key = normalize_company(data.get("company"))
            role_key = normalize_role(data.get("role"))

            app = apps_by_id.get(thread_app.get(email.thread_id))
            if app is None and company_key:
                app = _find_match(by_company.get(company_key, []), email, role_key)
            if app is None:
                if not company_key or email.type not in CREATES_APPLICATION:
                    continue
                app = Application(user_id=user_id, company=data["company"], source=None)
                db.add(app)
                db.flush()
                apps_by_id[app.id] = app
                by_company.setdefault(company_key, []).append(app)

            _fill_details(app, data)
            email.application_id = app.id
            app.events.append(Event(email_id=email.id, type=email.type, occurred_at=email.received_at))
            # Derive after every email so later matches see current status.
            if not app.user_edited:
                derive_status(app)
            thread_app.setdefault(email.thread_id, app.id)
            assigned += 1

        db.commit()
        return assigned


def _find_match(candidates: list[Application], email: Email, role_key: str | None) -> Application | None:
    if not candidates:
        return None
    if role_key:
        same_role = [a for a in candidates if _roles_match(normalize_role(a.role), role_key)]
        if same_role:
            app = max(same_role, key=lambda a: a.last_activity_at or a.created_at)
            if (email.type == "application_confirmation" and app.status == "rejected"
                    and app.rejected_at and email.received_at - app.rejected_at > REAPPLY_AFTER):
                return None  # re-application
            return app
        roleless = [a for a in candidates if not a.role]
        return roleless[0] if len(roleless) == 1 else None
    # No role on this email: attach to the most recent open application at the company.
    open_apps = [a for a in candidates if a.status not in ("rejected", "offer")]
    pool = open_apps or candidates
    return max(pool, key=lambda a: a.last_activity_at or a.created_at)


def _fill_details(app: Application, data: dict):
    """Fills blanks only, so the first good value wins and user edits are kept."""
    if not app.role and data.get("role"):
        app.role = data["role"]
    if not app.level and data.get("level") not in (None, "unknown"):
        app.level = data["level"]
    if not app.ats and data.get("ats"):
        app.ats = data["ats"]


def _furthest_stage(events) -> str:
    return max((STAGE_BY_TYPE[e.type] for e in events if e.type in STAGE_BY_TYPE),
               key=STAGE_ORDER.index, default="applied")


def derive_status(app: Application):
    events = sorted(app.events, key=lambda e: e.occurred_at)
    if not events:
        return
    confirmations = [e.occurred_at for e in events if e.type == "application_confirmation"]
    app.applied_at = min(confirmations) if confirmations else None
    app.last_activity_at = events[-1].occurred_at

    last_rejection = next((e for e in reversed(events) if e.type == "rejection"), None)
    # A later assessment/interview/offer means the process continued after that
    # rejection (e.g. rejected for one role round, invited to another).
    progressed_after = last_rejection is not None and any(
        e.occurred_at > last_rejection.occurred_at and e.type in STAGE_BY_TYPE
        and e.type != "application_confirmation"
        for e in events
    )
    if last_rejection and not progressed_after:
        app.status = "rejected"
        app.rejected_at = last_rejection.occurred_at
        app.rejection_stage = _furthest_stage(e for e in events if e.occurred_at <= last_rejection.occurred_at)
    else:
        app.status = _furthest_stage(events)
        app.rejected_at = app.rejection_stage = None


def reset_matches(user_id: int, db):
    """Deletes auto-built applications (keeping hand-edited ones) so matching can rerun."""
    auto_ids = select(Application.id).where(Application.user_id == user_id, Application.user_edited.is_(False))
    db.execute(update(Email).where(Email.application_id.in_(auto_ids)).values(application_id=None))
    db.execute(delete(Event).where(Event.application_id.in_(auto_ids)))
    db.execute(delete(Application).where(Application.id.in_(auto_ids)))
