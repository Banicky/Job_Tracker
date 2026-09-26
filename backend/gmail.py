"""Minimal Gmail REST client: token refresh, message search, and body parsing."""

import base64
import os
import random
import threading
import time
from datetime import datetime, timezone
from html.parser import HTMLParser

import httpx

from crypto import decrypt, encrypt
from db import SessionLocal
from models import User

API = "https://gmail.googleapis.com/gmail/v1/users/me"
TOKEN_URL = "https://oauth2.googleapis.com/token"
MAX_BODY_CHARS = 20_000
# Gmail allows 250 quota units/sec per user; messages.get costs 5, so the hard
# ceiling is 50 req/s. Stay well under it.
MAX_REQUESTS_PER_SECOND = 20
MAX_ATTEMPTS = 6
# Gmail reports some rate limiting as 403 rather than 429.
RATE_LIMIT_REASONS = {"rateLimitExceeded", "userRateLimitExceeded"}


class GmailAuthError(Exception):
    """The stored refresh token is missing or revoked; the user must sign in again."""


class GmailError(Exception):
    pass


def _error_reason(resp: httpx.Response) -> str | None:
    try:
        errors = resp.json()["error"].get("errors") or []
        return errors[0].get("reason") if errors else resp.json()["error"].get("status")
    except (ValueError, KeyError, AttributeError):
        return None


def _error_message(resp: httpx.Response) -> str:
    try:
        return resp.json()["error"]["message"]
    except (ValueError, KeyError, TypeError):
        return resp.text[:200]


class GmailClient:
    """Thread-safe: message fetches run in a worker pool and share one access token."""

    def __init__(self, user_id: int):
        self.user_id = user_id
        self._lock = threading.Lock()
        self._rate_lock = threading.Lock()
        self._next_request_at = 0.0
        self._http = httpx.Client(timeout=30)
        with SessionLocal() as db:
            user = db.get(User, user_id)
            self._access_token = decrypt(user.access_token)
            self._refresh_token = decrypt(user.refresh_token)
            self._expires_at = user.token_expires_at or 0

    def close(self):
        self._http.close()

    def _token(self) -> str:
        with self._lock:
            if time.time() > self._expires_at - 120:
                self._refresh()
            return self._access_token

    def _refresh(self):
        if not self._refresh_token:
            raise GmailAuthError("No refresh token stored")
        resp = self._http.post(TOKEN_URL, data={
            "client_id": os.environ["GOOGLE_CLIENT_ID"],
            "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
            "refresh_token": self._refresh_token,
            "grant_type": "refresh_token",
        })
        if resp.status_code in (400, 401):
            raise GmailAuthError(resp.text)
        resp.raise_for_status()
        data = resp.json()
        self._access_token = data["access_token"]
        self._expires_at = int(time.time()) + data["expires_in"]
        with SessionLocal() as db:
            user = db.get(User, self.user_id)
            user.access_token = encrypt(self._access_token)
            user.token_expires_at = self._expires_at
            db.commit()

    def _throttle(self):
        """Spaces requests across all worker threads to stay under Gmail's rate limit."""
        with self._rate_lock:
            now = time.monotonic()
            wait = self._next_request_at - now
            self._next_request_at = max(now, self._next_request_at) + 1 / MAX_REQUESTS_PER_SECOND
        if wait > 0:
            time.sleep(wait)

    def _get(self, path: str, params: dict | None = None) -> dict:
        for attempt in range(MAX_ATTEMPTS):
            self._throttle()
            resp = self._http.get(
                f"{API}{path}", params=params,
                headers={"Authorization": f"Bearer {self._token()}"},
            )
            if resp.is_success:
                return resp.json()
            if resp.status_code == 401 and attempt == 0:
                with self._lock:
                    self._expires_at = 0  # force refresh on next _token()
                continue
            rate_limited = resp.status_code == 429 or (
                resp.status_code == 403 and _error_reason(resp) in RATE_LIMIT_REASONS
            )
            if rate_limited or resp.status_code >= 500:
                time.sleep(min(2 ** attempt, 32) + random.random())
                continue
            break
        raise GmailError(f"Gmail API {resp.status_code}: {_error_message(resp)}")

    def list_message_ids(self, query: str) -> list[str]:
        ids, page_token = [], None
        while True:
            params = {"q": query, "maxResults": 500}
            if page_token:
                params["pageToken"] = page_token
            data = self._get("/messages", params)
            ids.extend(m["id"] for m in data.get("messages", []))
            page_token = data.get("nextPageToken")
            if not page_token:
                return ids

    def get_message(self, message_id: str) -> dict:
        return parse_message(self._get(f"/messages/{message_id}", {"format": "full"}))


def parse_message(msg: dict) -> dict:
    payload = msg.get("payload", {})
    headers = {h["name"].lower(): h["value"] for h in payload.get("headers", [])}
    received_at = datetime.fromtimestamp(int(msg["internalDate"]) / 1000, timezone.utc)
    return {
        "gmail_message_id": msg["id"],
        "thread_id": msg["threadId"],
        "received_at": received_at.replace(tzinfo=None),
        "sender": headers.get("from"),
        "subject": headers.get("subject"),
        "snippet": msg.get("snippet"),
        "body_text": _extract_body(payload)[:MAX_BODY_CHARS],
    }


def _extract_body(payload: dict) -> str:
    plain, html = [], []

    def walk(part):
        mime = part.get("mimeType", "")
        data = part.get("body", {}).get("data")
        if data and mime == "text/plain":
            plain.append(_b64(data))
        elif data and mime == "text/html":
            html.append(_b64(data))
        for sub in part.get("parts", []):
            walk(sub)

    walk(payload)
    if plain:
        return "\n".join(plain).strip()
    return "\n".join(_html_to_text(h) for h in html).strip()


def _b64(data: str) -> str:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", errors="replace")


class _TextExtractor(HTMLParser):
    SKIP = {"script", "style", "head"}
    BLOCK = {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "table"}

    def __init__(self):
        super().__init__()
        self.parts, self._skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip += 1
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def _html_to_text(html: str) -> str:
    p = _TextExtractor()
    p.feed(html)
    lines = (" ".join(line.split()) for line in "".join(p.parts).splitlines())
    return "\n".join(line for line in lines if line)
