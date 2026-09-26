"""Pulls candidate job emails from Gmail into the emails table.

Backfill searches the last 6 months; incremental syncs search from the last sync
time (with overlap). Already-stored message IDs are skipped, so reruns are safe.
"""

import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

import anthropic

from classify import classify_pending
from db import SessionLocal
from gmail import GmailAuthError, GmailClient
from matcher import match_pending
from models import Email, User, utcnow

log = logging.getLogger(__name__)

BACKFILL_DAYS = 182  # ~6 months
INCREMENTAL_OVERLAP = timedelta(hours=1)
FETCH_WORKERS = 5

KEYWORDS = [
    '"thank you for applying"', '"thanks for applying"', '"your application"',
    '"application received"', '"application for"', '"applying to"', '"applied to"',
    '"thank you for your interest"', '"next steps"', '"not moving forward"',
    '"move forward"', '"other candidates"', "unfortunately", "interview",
    "assessment", '"coding challenge"', '"take-home"', "offer",
]
ATS_DOMAINS = [
    "greenhouse.io", "greenhouse-mail.io", "lever.co", "myworkday.com", "workday.com",
    "ashbyhq.com", "smartrecruiters.com", "icims.com", "jobvite.com", "workablemail.com",
    "bamboohr.com", "taleo.net", "successfactors.com", "rippling.com", "breezy.hr",
    "recruitee.com", "jazzhr.com", "applytojob.com",
]
# Promotions is excluded to cut marketing noise ("offer", "interview tips", ...).
# Drop that clause if real application emails turn out to land there.
EXCLUDE = "-from:me -category:promotions"


def build_query(since: datetime) -> str:
    keywords = " OR ".join(KEYWORDS)
    senders = " OR ".join(ATS_DOMAINS)
    return f"after:{int(since.timestamp())} ({keywords} OR from:({senders})) {EXCLUDE}"


class SyncState:
    def __init__(self):
        self.lock = threading.Lock()
        self.running = False
        self.kind: str | None = None  # backfill | incremental | classify | match
        self.phase: str | None = None  # fetching | classifying | matching
        self.total = 0
        self.fetched = 0
        self.classify_total = 0
        self.classified = 0
        self.classify_failed = 0
        self.error: str | None = None
        self.started_at: datetime | None = None
        self.finished_at: datetime | None = None

    def as_dict(self):
        return {k: getattr(self, k) for k in
                ("running", "kind", "phase", "total", "fetched", "classify_total", "classified",
                 "classify_failed", "error", "started_at", "finished_at")}


state = SyncState()


def start_sync(user_id: int, kind: str) -> bool:
    """Starts a sync in a background thread. Returns False if one is already running."""
    with state.lock:
        if state.running:
            return False
        state.running, state.kind = True, kind
        state.phase = None
        state.total = state.fetched = 0
        state.classify_total = state.classified = state.classify_failed = 0
        state.error, state.started_at, state.finished_at = None, utcnow(), None
    threading.Thread(target=_run, args=(user_id, kind), daemon=True).start()
    return True


def _run(user_id: int, kind: str):
    try:
        if kind in ("backfill", "incremental"):
            state.phase = "fetching"
            _sync(user_id, kind)
        if kind != "match":
            state.phase = "classifying"
            _classify(user_id)
        state.phase = "matching"
        match_pending(user_id)
    except GmailAuthError:
        log.exception("Gmail auth failed")
        state.error = "Gmail access expired or was revoked. Sign out and sign in again."
    except anthropic.AuthenticationError:
        log.exception("Anthropic auth failed")
        state.error = "Anthropic API key is missing or invalid. Set ANTHROPIC_API_KEY in backend/.env."
    except Exception as e:
        log.exception("Sync failed")
        state.error = str(e)
    finally:
        state.running, state.finished_at = False, utcnow()


def _sync(user_id: int, kind: str):
    started = utcnow()
    with SessionLocal() as db:
        user = db.get(User, user_id)
        if kind == "incremental" and user.last_synced_at:
            since = user.last_synced_at - INCREMENTAL_OVERLAP
        else:
            since = started - timedelta(days=BACKFILL_DAYS)
        existing = set(db.scalars(select(Email.gmail_message_id).where(Email.user_id == user_id)))

    # Stored datetimes are naive UTC; tag them so .timestamp() isn't read as local time.
    since_utc = since.replace(tzinfo=timezone.utc)
    client = GmailClient(user_id)
    try:
        ids = [i for i in client.list_message_ids(build_query(since_utc)) if i not in existing]
        state.total = len(ids)
        log.info("%s sync: %d new messages", kind, len(ids))

        failed = 0
        with SessionLocal() as db, ThreadPoolExecutor(FETCH_WORKERS) as pool:
            futures = {pool.submit(client.get_message, i): i for i in ids}
            try:
                for n, fut in enumerate(as_completed(futures), 1):
                    try:
                        db.add(Email(user_id=user_id, **fut.result()))
                    except GmailAuthError:
                        raise
                    except Exception as e:
                        failed += 1
                        log.warning("Failed to fetch message %s: %s", futures[fut], e)
                    state.fetched = n
                    if n % 50 == 0:
                        db.commit()
            except BaseException:
                pool.shutdown(cancel_futures=True)
                db.commit()  # keep what was fetched; reruns skip stored IDs
                raise

            # Only advance the sync markers when nothing was skipped; otherwise the
            # skipped messages could fall outside the next incremental window.
            if not failed:
                user = db.get(User, user_id)
                user.last_synced_at = started
                if kind == "backfill":
                    user.backfill_completed_at = utcnow()
            db.commit()
    finally:
        client.close()
    if failed:
        state.error = f"{failed} email(s) failed to download. Run the sync again to retry them."


def _classify(user_id: int):
    def progress(done: int, total: int):
        state.classified, state.classify_total = done, total

    _, failed = classify_pending(user_id, progress)
    state.classify_failed = failed
    if failed:
        msg = f"{failed} email(s) could not be classified; they'll be retried on the next sync."
        state.error = f"{state.error} {msg}" if state.error else msg
