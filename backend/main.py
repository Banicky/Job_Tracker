from dotenv import load_dotenv

load_dotenv()  # must run before modules that read env at import (crypto)

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from urllib.parse import quote

from authlib.integrations.starlette_client import OAuth, OAuthError
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, selectinload
from starlette.middleware.sessions import SessionMiddleware

import sync
from crypto import encrypt
from db import Base, engine, get_db, SessionLocal
from matcher import reset_matches
from models import Application, Email, User, utcnow

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
POLL_INTERVAL_SECONDS = int(os.environ.get("POLL_INTERVAL_SECONDS", 300))


async def poll_gmail():
    while True:
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
        with SessionLocal() as db:
            user_ids = db.scalars(select(User.id).where(User.backfill_completed_at.is_not(None))).all()
        for user_id in user_ids:
            sync.start_sync(user_id, "incremental")


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(engine)
    poller = asyncio.create_task(poll_gmail())
    yield
    poller.cancel()


app = FastAPI(lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=os.environ["SESSION_SECRET"])

oauth = OAuth()
oauth.register(
    name="google",
    client_id=os.environ["GOOGLE_CLIENT_ID"],
    client_secret=os.environ["GOOGLE_CLIENT_SECRET"],
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": f"openid email profile {GMAIL_SCOPE}"},
)


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    user_id = request.session.get("user_id")
    user = db.get(User, user_id) if user_id else None
    if not user:
        raise HTTPException(401)
    return user


def login_error(message: str) -> RedirectResponse:
    return RedirectResponse(f"/login?error={quote(message)}")


@app.get("/api/auth/google")
async def login(request: Request):
    redirect_url = request.url_for("auth_callback")
    # offline + consent so Google always returns a refresh token.
    return await oauth.google.authorize_redirect(
        request, redirect_url, access_type="offline", prompt="consent"
    )


@app.get("/api/auth/google/callback")
async def auth_callback(request: Request, db: Session = Depends(get_db)):
    try:
        token = await oauth.google.authorize_access_token(request)
    except OAuthError as e:
        return login_error(e.description or e.error)
    if GMAIL_SCOPE not in token.get("scope", "").split():
        return login_error("Gmail read access is required. Please allow it on the Google consent screen.")

    info = token["userinfo"]
    user = db.scalar(select(User).where(User.google_sub == info["sub"]))
    if not user:
        user = User(google_sub=info["sub"])
        db.add(user)
    user.email, user.name, user.picture = info["email"], info.get("name"), info.get("picture")
    user.access_token = encrypt(token["access_token"])
    user.token_expires_at = token.get("expires_at")
    if token.get("refresh_token"):
        user.refresh_token = encrypt(token["refresh_token"])
    db.commit()

    request.session.clear()
    request.session["user_id"] = user.id
    return RedirectResponse("/")


@app.get("/api/me")
def me(user: User = Depends(current_user)):
    return {"email": user.email, "name": user.name, "picture": user.picture}


@app.post("/api/logout")
async def logout(request: Request):
    request.session.clear()
    return {"ok": True}


@app.post("/api/sync/{kind}")
def start_sync(kind: str, user: User = Depends(current_user)):
    if kind not in ("backfill", "incremental", "classify"):
        raise HTTPException(404)
    if not sync.start_sync(user.id, kind):
        raise HTTPException(409, "A sync is already running")
    return {"ok": True}


@app.post("/api/classify/reset")
def reclassify_all(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Marks every email unprocessed and reclassifies, e.g. after changing the prompt."""
    if sync.state.running:
        raise HTTPException(409, "A sync is already running")
    reset_matches(user.id, db)
    db.execute(update(Email).where(Email.user_id == user.id).values(processed_at=None))
    db.commit()
    sync.start_sync(user.id, "classify")
    return {"ok": True}


@app.post("/api/applications/rebuild")
def rebuild_applications(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Regroups classified emails into applications. No Claude calls, so it's free."""
    if sync.state.running:
        raise HTTPException(409, "A sync is already running")
    reset_matches(user.id, db)
    db.commit()
    sync.start_sync(user.id, "match")
    return {"ok": True}


GHOSTED_AFTER_DAYS = 30
RESPONSE_TYPES = {"rejection", "interview_request", "assessment", "offer"}


@app.get("/api/applications")
def list_applications(user: User = Depends(current_user), db: Session = Depends(get_db)):
    apps = db.scalars(
        select(Application).where(Application.user_id == user.id)
        .options(selectinload(Application.events))
    ).all()
    email_ids = [ev.email_id for a in apps for ev in a.events if ev.email_id]
    emails = {e.id: e for e in db.scalars(select(Email).where(Email.id.in_(email_ids)))}
    now = utcnow()

    result = []
    for a in apps:
        first_response = next((ev.occurred_at for ev in a.events if ev.type in RESPONSE_TYPES), None)
        start = a.applied_at or (a.events[0].occurred_at if a.events else None)
        ghosted = (first_response is None and a.status == "applied" and start is not None
                   and (now - start).days >= GHOSTED_AFTER_DAYS)
        result.append({
            "id": a.id, "company": a.company, "role": a.role, "level": a.level, "ats": a.ats,
            "source": a.source, "status": a.status, "applied_at": a.applied_at,
            "rejected_at": a.rejected_at, "rejection_stage": a.rejection_stage,
            "last_activity_at": a.last_activity_at, "user_edited": a.user_edited,
            "responded": first_response is not None, "first_response_at": first_response,
            "days_to_response": (first_response - a.applied_at).days if first_response and a.applied_at else None,
            "ghosted": ghosted,
            "events": [
                {"type": ev.type, "occurred_at": ev.occurred_at,
                 "subject": emails[ev.email_id].subject if ev.email_id in emails else None,
                 "gmail_message_id": emails[ev.email_id].gmail_message_id if ev.email_id in emails else None}
                for ev in a.events
            ],
        })
    result.sort(key=lambda r: r["last_activity_at"] or r["applied_at"] or now, reverse=True)
    return result


@app.get("/api/sync/status")
def sync_status(user: User = Depends(current_user), db: Session = Depends(get_db)):
    counts = dict(db.execute(
        select(Email.processed_at.is_not(None), func.count())
        .where(Email.user_id == user.id).group_by(Email.processed_at.is_not(None))
    ).all())
    return {
        **sync.state.as_dict(),
        "unclassified_count": counts.get(False, 0),
        "last_synced_at": user.last_synced_at,
        "backfill_completed_at": user.backfill_completed_at,
        "email_count": sum(counts.values()),
    }


@app.get("/api/emails")
def list_emails(limit: int = 100, offset: int = 0, job_only: bool = False,
                user: User = Depends(current_user), db: Session = Depends(get_db)):
    query = select(Email).where(Email.user_id == user.id)
    if job_only:
        query = query.where(Email.type.is_not(None), Email.type != "not_job_related")
    rows = db.scalars(query.order_by(Email.received_at.desc()).limit(min(limit, 500)).offset(offset))
    return [
        {"id": e.id, "gmail_message_id": e.gmail_message_id, "received_at": e.received_at,
         "sender": e.sender, "subject": e.subject, "snippet": e.snippet, "type": e.type,
         "confidence": e.confidence, **{k: (e.extracted_json or {}).get(k) for k in ("company", "role", "level", "ats")}}
        for e in rows
    ]
