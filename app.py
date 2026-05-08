import json
import logging
import uuid
from datetime import datetime, timezone

from flask import (
    Flask, request, jsonify, render_template,
    redirect, url_for, session, abort, flash
)
from werkzeug.security import generate_password_hash, check_password_hash

import config
import db
import instagram as ig
import ai_agent as ai
import wakeword_processor as wp
import queue_worker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = config.SECRET_KEY

WEBHOOK_VERIFY_TOKEN = config.WEBHOOK_VERIFY_TOKEN
ADMIN_EMAIL    = config.ADMIN_EMAIL
ADMIN_PASSWORD = config.ADMIN_PASSWORD

# ── Bootstrap ────────────────────────────────────────────────────────────────

try:
    db.init_db()
    db.seed_admin(ADMIN_EMAIL, generate_password_hash(ADMIN_PASSWORD))
    db.seed_ai_agent()
except Exception as _e:
    logging.getLogger(__name__).error("DB init error: %s", _e)

try:
    queue_worker.start()
except Exception as _e:
    logging.getLogger(__name__).error("Queue worker failed to start: %s", _e)

# ── Auth helpers ─────────────────────────────────────────────────────────────

from auth import login_required, api_key_required

# ── Auth routes ──────────────────────────────────────────────────────────────

@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("logged_in"):
        return redirect(url_for("dashboard"))
    error = None
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = db.get_user_by_email(email)
        if user and check_password_hash(user["password_hash"], password):
            session["logged_in"] = True
            session["email"] = user["email"]
            return redirect(url_for("dashboard"))
        error = "Invalid email or password."
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ── Dashboard pages ───────────────────────────────────────────────────────────

@app.route("/")
@login_required
def dashboard():
    stats = db.get_stats()
    recent = db.get_conversations()[:10]
    return render_template("dashboard.html", stats=stats, recent=recent)


@app.route("/chat")
@login_required
def chat():
    conversations = db.get_conversations()
    return render_template("chat.html", conversations=conversations)


@app.route("/queue")
@login_required
def queue_page():
    items = db.get_queue_messages(limit=100)
    return render_template("queue.html", items=items)


@app.route("/wakewords")
@login_required
def wakewords_page():
    wakewords = db.get_wakewords()
    return render_template("wakewords.html", wakewords=wakewords)


@app.route("/ai-agent")
@login_required
def ai_agent_page():
    agent = db.get_ai_agent()
    return render_template("ai_agent.html", agent=agent)


@app.route("/api-keys")
@login_required
def api_keys_page():
    keys = db.get_api_keys()
    new_key = session.pop("new_api_key", None)
    return render_template("api_keys.html", keys=keys, new_key=new_key)


# ── Internal API — conversations & messages ───────────────────────────────────

@app.route("/api/conversations")
@login_required
def api_conversations():
    return jsonify(db.get_conversations())


@app.route("/api/conversations/<conv_id>/messages")
@login_required
def api_messages(conv_id):
    return jsonify(db.get_messages(conv_id))


@app.route("/api/stats")
@login_required
def api_stats():
    return jsonify(db.get_stats())


@app.route("/api/known-users")
@login_required
def api_known_users():
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify([])
    return jsonify(db.search_known_users(q))


# ── Internal API — send ───────────────────────────────────────────────────────

@app.route("/api/send", methods=["POST"])
@login_required
def api_send():
    body = request.get_json(force=True)
    recipient_id = body.get("recipient_id", "").strip()
    text = body.get("text", "").strip()
    media_url = body.get("media_url", "").strip()
    media_type = body.get("media_type", "image")

    if not recipient_id:
        return jsonify({"error": "recipient_id required"}), 400

    # Resolve username → IG ID if needed (look up known_users)
    if not recipient_id.isdigit():
        known = db.get_known_user_by_username(recipient_id)
        if known:
            recipient_id = known["ig_id"]
        else:
            return jsonify({
                "error": f"Username '{recipient_id}' not found in known users. "
                         "They must message your account first, or enter their IG user ID directly."
            }), 404

    try:
        if media_url:
            result = ig.send_media_message(recipient_id, media_url, media_type)
        elif text:
            result = ig.send_text_message(recipient_id, text)
        else:
            return jsonify({"error": "text or media_url required"}), 400
    except Exception as exc:
        logger.error("Send failed: %s", exc)
        return jsonify({"error": str(exc)}), 502

    msg_id = result.get("message_id") or str(uuid.uuid4())
    db.save_message(
        msg_id=msg_id,
        conversation_id=recipient_id,
        direction="outbound",
        text=text or None,
        media_url=media_url or None,
        media_type=media_type if media_url else None,
        timestamp=datetime.now(timezone.utc).isoformat(),
        raw=json.dumps(result),
    )
    return jsonify({"ok": True, "message_id": msg_id})


# ── Internal API — queue ──────────────────────────────────────────────────────

@app.route("/api/queue", methods=["GET"])
@login_required
def api_queue_list():
    status = request.args.get("status")
    return jsonify(db.get_queue_messages(limit=100, status=status))


@app.route("/api/queue", methods=["POST"])
@login_required
def api_queue_add():
    body = request.get_json(force=True)
    recipient_id = body.get("recipient_id", "").strip()
    text = body.get("text", "").strip()
    scheduled_at = body.get("scheduled_at")
    recipient_name = body.get("recipient_name", "")

    if not recipient_id or not text:
        return jsonify({"error": "recipient_id and text required"}), 400

    if not recipient_id.isdigit():
        known = db.get_known_user_by_username(recipient_id)
        if known:
            recipient_name = recipient_name or known.get("username", "")
            recipient_id = known["ig_id"]
        else:
            return jsonify({"error": f"Username '{recipient_id}' not in known users."}), 404

    qid = db.queue_message(
        recipient_id=recipient_id,
        message=text,
        scheduled_at=scheduled_at,
        recipient_name=recipient_name,
        source="ui",
    )
    return jsonify({"ok": True, "queue_id": qid})


@app.route("/api/queue/<int:qid>", methods=["DELETE"])
@login_required
def api_queue_cancel(qid):
    db.cancel_queue_message(qid)
    return jsonify({"ok": True})


# ── Internal API — wakewords ──────────────────────────────────────────────────

@app.route("/api/wakewords", methods=["GET"])
@login_required
def api_wakewords_list():
    return jsonify(db.get_wakewords())


@app.route("/api/wakewords", methods=["POST"])
@login_required
def api_wakewords_create():
    body = request.get_json(force=True)
    phrase = body.get("phrase", "").strip()
    match_type = body.get("match_type", "contains")
    reply_text = body.get("reply_text", "").strip()
    if not phrase or not reply_text:
        return jsonify({"error": "phrase and reply_text required"}), 400
    wid = db.create_wakeword(phrase, match_type, reply_text)
    return jsonify({"ok": True, "id": wid})


@app.route("/api/wakewords/<int:wid>", methods=["PUT"])
@login_required
def api_wakewords_update(wid):
    body = request.get_json(force=True)
    db.update_wakeword(wid, **body)
    return jsonify({"ok": True})


@app.route("/api/wakewords/<int:wid>", methods=["DELETE"])
@login_required
def api_wakewords_delete(wid):
    db.delete_wakeword(wid)
    return jsonify({"ok": True})


# ── Internal API — AI agent ───────────────────────────────────────────────────

@app.route("/api/ai-agent", methods=["GET"])
@login_required
def api_ai_agent_get():
    agent = db.get_ai_agent()
    if agent:
        agent.pop("gemini_api_key", None)  # don't expose key via API
    return jsonify(agent or {})


@app.route("/api/ai-agent", methods=["POST"])
@login_required
def api_ai_agent_save():
    body = request.get_json(force=True)
    allowed = {"name", "enabled", "wakeword", "gemini_api_key", "gemini_model", "system_prompt"}
    kwargs = {k: v for k, v in body.items() if k in allowed}
    db.update_ai_agent(**kwargs)
    return jsonify({"ok": True})


@app.route("/api/ai-agent/test", methods=["POST"])
@login_required
def api_ai_agent_test():
    body = request.get_json(force=True)
    test_msg = body.get("message", "Hello")
    agent = db.get_ai_agent()
    if not agent:
        return jsonify({"error": "Agent not configured"}), 400
    reply = ai.get_ai_reply(test_msg, agent)
    if reply is None:
        return jsonify({"error": "Gemini call failed. Check API key and model."}), 502
    return jsonify({"reply": reply})


# ── Internal API — API keys ───────────────────────────────────────────────────

@app.route("/api/api-keys", methods=["GET"])
@login_required
def api_api_keys_list():
    return jsonify(db.get_api_keys())


@app.route("/api/api-keys", methods=["POST"])
@login_required
def api_api_keys_create():
    body = request.get_json(force=True)
    name = body.get("name", "").strip()
    description = body.get("description", "").strip()
    if not name:
        return jsonify({"error": "name required"}), 400
    raw_key = db.generate_api_key(name, description)
    return jsonify({"ok": True, "key": raw_key})


@app.route("/api/api-keys/<int:kid>", methods=["DELETE"])
@login_required
def api_api_keys_revoke(kid):
    db.revoke_api_key(kid)
    return jsonify({"ok": True})


# ── Public API — for external apps ───────────────────────────────────────────

@app.route("/api/v1/send", methods=["POST"])
@api_key_required
def public_send():
    """
    Send or schedule a message from an external app.
    Body: { "recipient": "username_or_igsid", "message": "text",
            "scheduled_at": "ISO-8601 optional" }
    """
    body = request.get_json(force=True) or {}
    recipient = body.get("recipient", "").strip()
    message = body.get("message", "").strip()
    media_url = body.get("media_url", "").strip()
    scheduled_at = body.get("scheduled_at")

    if not recipient or (not message and not media_url):
        return jsonify({"error": "recipient and (message or media_url) required"}), 400

    recipient_name = recipient
    if not recipient.isdigit():
        known = db.get_known_user_by_username(recipient)
        if not known:
            return jsonify({
                "error": f"User '{recipient}' not found. They must message your account first, "
                         "or provide their numeric IG user ID."
            }), 404
        recipient_name = known.get("username", recipient)
        recipient = known["ig_id"]

    api_key_id = request.api_key_obj.get("id")

    if scheduled_at:
        qid = db.queue_message(
            recipient_id=recipient,
            message=message or None,
            media_url=media_url or None,
            scheduled_at=scheduled_at,
            recipient_name=recipient_name,
            source="api",
            api_key_id=api_key_id,
        )
        return jsonify({"ok": True, "queued": True, "queue_id": qid})

    # Instant send
    try:
        if media_url:
            result = ig.send_media_message(recipient, media_url)
        else:
            result = ig.send_text_message(recipient, message)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 502

    msg_id = result.get("message_id") or str(uuid.uuid4())
    db.save_message(
        msg_id=msg_id,
        conversation_id=recipient,
        direction="outbound",
        text=message or None,
        media_url=media_url or None,
        timestamp=datetime.now(timezone.utc).isoformat(),
        raw=json.dumps(result),
    )
    return jsonify({"ok": True, "message_id": msg_id})


@app.route("/api/v1/queue", methods=["POST"])
@api_key_required
def public_queue():
    """Queue a message for scheduled delivery."""
    body = request.get_json(force=True) or {}
    recipient = body.get("recipient", "").strip()
    message = body.get("message", "").strip()
    scheduled_at = body.get("scheduled_at")

    if not recipient or not message:
        return jsonify({"error": "recipient and message required"}), 400

    if not recipient.isdigit():
        known = db.get_known_user_by_username(recipient)
        if not known:
            return jsonify({"error": f"User '{recipient}' not found."}), 404
        recipient = known["ig_id"]

    qid = db.queue_message(
        recipient_id=recipient,
        message=message,
        scheduled_at=scheduled_at,
        source="api",
        api_key_id=request.api_key_obj.get("id"),
    )
    return jsonify({"ok": True, "queue_id": qid})


# ── Webhook ───────────────────────────────────────────────────────────────────

@app.route("/webhook", methods=["GET", "POST"], strict_slashes=False)
def webhook():
    # ── GET: Meta verification handshake ──
    if request.method == "GET":
        mode      = request.args.get("hub.mode", "")
        token     = request.args.get("hub.verify_token", "")
        challenge = request.args.get("hub.challenge", "")
        logger.info("Webhook GET — mode=%r token_ok=%s challenge=%r", mode, token == WEBHOOK_VERIFY_TOKEN, challenge)
        if mode == "subscribe" and token == WEBHOOK_VERIFY_TOKEN:
            logger.info("Webhook verified OK")
            return challenge   # plain string → Flask returns text/html 200, exactly what Meta needs
        logger.warning("Webhook verify FAILED — received token=%r", token)
        return "Forbidden", 403

    # ── POST: incoming messages ──
    payload = request.get_json(force=True, silent=True) or {}
    if payload.get("object") not in ("instagram", "page"):
        return "ok", 200

    messages = ig.parse_webhook_payload(payload)

    for msg in messages:
        sender_id = msg["sender_id"]

        profile = ig.get_user_profile(sender_id)
        username = profile.get("name")
        profile_pic = profile.get("profile_pic")

        db.upsert_conversation(sender_id, username=username, profile_pic=profile_pic)
        db.upsert_known_user(sender_id, username=username, profile_pic=profile_pic)

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
        logger.info("Inbound message %s from %s", msg["msg_id"], sender_id)

        text = msg.get("text") or ""

        # 1. Check wakewords first
        wakewords = db.get_wakewords(enabled_only=True)
        matched_ww = wp.find_match(text, wakewords)
        if matched_ww:
            db.increment_wakeword_trigger(matched_ww["id"])
            try:
                ig.send_text_message(sender_id, matched_ww["reply_text"])
                reply_id = str(uuid.uuid4())
                db.save_message(
                    msg_id=reply_id,
                    conversation_id=sender_id,
                    direction="outbound",
                    text=matched_ww["reply_text"],
                    timestamp=datetime.now(timezone.utc).isoformat(),
                )
                logger.info("Wakeword reply sent to %s", sender_id)
            except Exception as exc:
                logger.error("Wakeword reply failed: %s", exc)
            continue  # don't also run AI agent for same message

        # 2. Check AI agent
        agent = db.get_ai_agent()
        if agent and ai.should_trigger(text, agent):
            reply = ai.get_ai_reply(text, agent)
            if reply:
                try:
                    ig.send_text_message(sender_id, reply)
                    reply_id = str(uuid.uuid4())
                    db.save_message(
                        msg_id=reply_id,
                        conversation_id=sender_id,
                        direction="outbound",
                        text=reply,
                        timestamp=datetime.now(timezone.utc).isoformat(),
                    )
                    logger.info("AI agent reply sent to %s", sender_id)
                except Exception as exc:
                    logger.error("AI agent reply failed: %s", exc)

    return "ok", 200


# ── Webhook test ─────────────────────────────────────────────────────────────

@app.route("/webhook-test")
def webhook_test():
    return jsonify({
        "status": "reachable",
        "webhook_url": "https://instapy.logiclaunch.in/webhook",
        "verify_token": WEBHOOK_VERIFY_TOKEN,
        "manual_test": f"https://instapy.logiclaunch.in/webhook?hub.mode=subscribe&hub.verify_token={WEBHOOK_VERIFY_TOKEN}&hub.challenge=test123",
    })


# ── Health ────────────────────────────────────────────────────────────────────

@app.route("/health")
def health():
    return jsonify({
        "status": "ok",
        "ts": datetime.now(timezone.utc).isoformat(),
        "queue_worker": queue_worker.scheduler.running,
    })


# ── Meta / Instagram required endpoints ──────────────────────────────────────

@app.route("/auth/callback")
def auth_callback():
    """
    OAuth redirect URI for Instagram Business Login.
    Meta redirects here after the user completes the login flow.
    In a multi-tenant app you'd exchange the 'code' for an access token here.
    For this single-owner dashboard we just confirm and redirect to home.
    """
    code = request.args.get("code")
    error = request.args.get("error")
    if error:
        logger.warning("OAuth error: %s — %s", error, request.args.get("error_description"))
        return render_template("login.html", error=f"Instagram OAuth error: {error}"), 400
    if code:
        logger.info("OAuth code received (single-owner setup — no exchange needed): %s…", code[:8])
    return redirect(url_for("dashboard"))


@app.route("/deauthorize", methods=["GET", "POST"])
def deauthorize():
    """
    Deauthorize callback URL.
    Meta calls this (POST) when a user removes your app.
    Must return HTTP 200.
    """
    payload = request.get_json(force=True, silent=True) or {}
    logger.info("Deauthorize callback received: %s", payload)
    return jsonify({"ok": True}), 200


@app.route("/data-deletion", methods=["GET", "POST"])
def data_deletion():
    """
    Data Deletion Request URL (GDPR).
    Meta calls this when a user requests their data be deleted.
    Must return a JSON body with a url and confirmation_code.
    """
    payload = request.get_json(force=True, silent=True) or {}
    logger.info("Data deletion request: %s", payload)
    # Return the required Meta format
    return jsonify({
        "url": "https://instapy.logiclaunch.in/data-deletion",
        "confirmation_code": "instapy_data_deletion_confirmed",
    }), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=os.environ.get("FLASK_DEBUG", "0") == "1")
