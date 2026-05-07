import logging
from datetime import timezone as _tz
from apscheduler.schedulers.background import BackgroundScheduler

import db
import instagram as ig

logger = logging.getLogger(__name__)
scheduler = BackgroundScheduler(timezone=_tz.utc)


def process_queue():
    """Send all pending messages whose scheduled_at has passed."""
    pending = db.get_pending_queue_messages()
    if not pending:
        return
    logger.info("Queue worker: processing %d message(s)", len(pending))
    for msg in pending:
        try:
            if msg.get("media_url"):
                ig.send_media_message(
                    msg["recipient_id"],
                    msg["media_url"],
                    msg.get("media_type", "image"),
                )
            elif msg.get("message"):
                ig.send_text_message(msg["recipient_id"], msg["message"])
            else:
                db.update_queue_status(msg["id"], "failed", "No content")
                continue

            db.update_queue_status(msg["id"], "sent")
            logger.info("Queue: sent message id=%s to %s", msg["id"], msg["recipient_id"])

        except Exception as exc:
            logger.error("Queue: failed message id=%s — %s", msg["id"], exc)
            db.update_queue_status(msg["id"], "failed", str(exc))


def start():
    if not scheduler.running:
        scheduler.add_job(process_queue, "interval", seconds=30, id="queue_worker")
        scheduler.start()
        logger.info("Queue worker started (30s interval)")


def stop():
    if scheduler.running:
        scheduler.shutdown(wait=False)
