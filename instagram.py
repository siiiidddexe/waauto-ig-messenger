"""
Instagram Messaging API client.
Docs: https://developers.facebook.com/docs/messenger-platform/instagram
"""
import json
import logging
import requests
import config

logger = logging.getLogger(__name__)

GRAPH_API_BASE = "https://graph.facebook.com/v21.0"

def _token():
    return config.INSTAGRAM_ACCESS_TOKEN

def _ig_id():
    return config.INSTAGRAM_USER_ID


def _headers():
    return {"Content-Type": "application/json"}


def _raise_with_detail(resp: requests.Response):
    """Raise with the full Instagram error message instead of just the HTTP status."""
    try:
        err = resp.json()
        msg = err.get("error", {}).get("message") or err.get("error") or resp.text
    except Exception:
        msg = resp.text
    raise requests.HTTPError(f"Instagram API {resp.status_code}: {msg}", response=resp)


def send_text_message(recipient_id: str, text: str) -> dict:
    """Send a plain text DM to an Instagram user."""
    url = f"{GRAPH_API_BASE}/{_ig_id()}/messages"
    payload = {
        "recipient": {"id": recipient_id},
        "message": {"text": text},
        "access_token": _token(),
    }
    logger.info("Sending text to %s via %s", recipient_id, _ig_id())
    resp = requests.post(url, json=payload, headers=_headers(), timeout=15)
    if not resp.ok:
        _raise_with_detail(resp)
    return resp.json()


def send_media_message(recipient_id: str, media_url: str, media_type: str = "image") -> dict:
    """Send a media attachment DM."""
    url = f"{GRAPH_API_BASE}/{_ig_id()}/messages"
    payload = {
        "recipient": {"id": recipient_id},
        "message": {
            "attachment": {
                "type": media_type,
                "payload": {"url": media_url, "is_reusable": True},
            }
        },
        "access_token": _token(),
    }
    resp = requests.post(url, json=payload, headers=_headers(), timeout=15)
    if not resp.ok:
        _raise_with_detail(resp)
    return resp.json()


def get_user_profile(user_id: str) -> dict:
    """Fetch basic profile info for an Instagram user (requires permission)."""
    url = f"{GRAPH_API_BASE}/{user_id}"
    params = {
        "fields": "name,profile_pic",
        "access_token": _token(),
    }
    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        logger.warning("Could not fetch profile for %s: %s", user_id, exc)
        return {}


def parse_webhook_payload(payload: dict) -> list[dict]:
    """
    Parse an Instagram webhook POST body and return a list of normalised message dicts:
    {
        'msg_id': str,
        'sender_id': str,
        'text': str | None,
        'media_url': str | None,
        'media_type': str | None,
        'timestamp': str,   # ISO-8601
        'raw': str,          # full JSON of the entry
    }
    """
    results = []
    entries = payload.get("entry", [])
    for entry in entries:
        for messaging in entry.get("messaging", []):
            sender_id = messaging.get("sender", {}).get("id")
            if not sender_id or sender_id == _ig_id():
                continue  # skip own messages echoed back

            msg = messaging.get("message", {})
            msg_id = msg.get("mid", f"unknown-{sender_id}")
            ts_ms = messaging.get("timestamp", 0)

            from datetime import datetime, timezone
            ts = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).isoformat()

            text = msg.get("text")
            media_url = None
            media_type = None

            attachments = msg.get("attachments", [])
            if attachments:
                att = attachments[0]
                media_type = att.get("type")
                media_url = att.get("payload", {}).get("url")

            results.append(
                {
                    "msg_id": msg_id,
                    "sender_id": sender_id,
                    "text": text,
                    "media_url": media_url,
                    "media_type": media_type,
                    "timestamp": ts,
                    "raw": json.dumps(messaging),
                }
            )
    return results
