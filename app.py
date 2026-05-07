import os
import json
import logging
import uuid
from datetime import datetime, timezone
from flask import Flask, request, jsonify, render_template, abort
from dotenv import load_dotenv

load_dotenv()

import db
import instagram as ig

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
db.init_db()

WEBHOOK_VERIFY_TOKEN = os.environ.get("WEBHOOK_VERIFY_TOKEN", "my_secure_verify_token")

# ---------------------------------------------------------------------------
# Web UI
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


# ---------------------------------------------------------------------------
# REST API — conversations & messages
# ---------------------------------------------------------------------------

@app.route("/api/conversations")
def api_conversations():
    return jsonify(db.get_conversations())


@app.route("/api/conversations/<conv_id>/messages")
def api_messages(conv_id):
    msgs = db.get_messages(conv_id)
    return jsonify(msgs)


@app.route("/api/send", methods=["POST"])
def api_send():
    body = request.get_json(force=True)
    recipient_id = body.get("recipient_id")
    text = body.get("text", "").strip()
    media_url = body.get("media_url")
    media_type = body.get("media_type", "image")

    if not recipient_id:
        return jsonify({"error": "recipient_id required"}), 400

    try:
        if media_url:
            result = ig.send_media_message(recipient_id, media_url, media_type)
        elif text:
            result = ig.send_text_message(recipient_id, text)
        else:
            return jsonify({"error": "text or media_url required"}), 400
    except Exception as exc:
        logger.error("Failed to send message: %s", exc)
        return jsonify({"error": str(exc)}), 502

    msg_id = result.get("message_id") or str(uuid.uuid4())
    ts = datetime.now(timezone.utc).isoformat()
    db.save_message(
        msg_id=msg_id,
        conversation_id=recipient_id,
        direction="outbound",
        text=text or None,
        media_url=media_url,
        media_type=media_type if media_url else None,
        timestamp=ts,
        raw=json.dumps(result),
    )
    return jsonify({"ok": True, "message_id": msg_id})


# ---------------------------------------------------------------------------
# Webhook — Instagram will POST events here
# ---------------------------------------------------------------------------

@app.route("/webhook", methods=["GET"])
def webhook_verify():
    """Instagram webhook verification handshake."""
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")

    if mode == "subscribe" and token == WEBHOOK_VERIFY_TOKEN:
        logger.info("Webhook verified successfully.")
        return challenge, 200
    else:
        logger.warning("Webhook verification failed. token=%s", token)
        abort(403)


@app.route("/webhook", methods=["POST"])
def webhook_receive():
    """Receive incoming Instagram messages."""
    payload = request.get_json(force=True, silent=True) or {}
    logger.info("Webhook payload: %s", json.dumps(payload)[:500])

    if payload.get("object") not in ("instagram", "page"):
        return "ok", 200

    messages = ig.parse_webhook_payload(payload)
    for msg in messages:
        sender_id = msg["sender_id"]

        # Optionally enrich with profile info (best-effort)
        profile = ig.get_user_profile(sender_id)
        username = profile.get("name")
        profile_pic = profile.get("profile_pic")
        db.upsert_conversation(sender_id, username=username, profile_pic=profile_pic)

        db.save_message(
            msg_id=msg["msg_id"],
            conversation_id=sender_id,
            direction="inbound",
            text=msg["text"],
            media_url=msg["media_url"],
            media_type=msg["media_type"],
            timestamp=msg["timestamp"],
            raw=msg["raw"],
        )
        logger.info("Saved inbound message %s from %s", msg["msg_id"], sender_id)

        # ---------------------------------------------------------------
        # AI HOOK — plug your AI response logic here
        # Example:
        #   ai_reply = ai_module.generate_reply(msg["text"])
        #   ig.send_text_message(sender_id, ai_reply)
        # ---------------------------------------------------------------

    return "ok", 200


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.route("/health")
def health():
    return jsonify({"status": "ok", "ts": datetime.now(timezone.utc).isoformat()})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=os.environ.get("FLASK_DEBUG", "0") == "1")
