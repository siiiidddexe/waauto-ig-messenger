# Instagram Messenger — WA-AUTO

A Python/Flask app to **send and receive Instagram DMs programmatically** via the Meta Graph API, with a clean web UI. Built to be Dokploy-compatible and ready for AI integration.

---

## Features

- 📥 Receive Instagram DMs via Meta webhook
- 📤 Send text/media replies from the web UI or via REST API
- 💬 Conversation history stored in SQLite (persistent Docker volume)
- 🤖 AI hook ready — drop in your LLM response logic in `app.py`
- 🐳 Docker / Dokploy compatible

---

## Quick Start (local)

```bash
# 1. Clone and enter dir
git clone <your-repo>
cd instagram-messenger

# 2. Create venv and install
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt

# 3. Configure
cp .env.example .env
# Edit .env with your credentials

# 4. Run
python app.py
# Open http://localhost:5000
```

---

## Docker / Dokploy Deploy

```bash
docker compose up -d
```

Or in Dokploy:
1. Point to this Git repo
2. Set build type to **Dockerfile**
3. Add environment variables from `.env.example`
4. Expose port **5000**
5. Mount a persistent volume at `/data`

---

## Webhook Setup

1. Deploy to a public URL (e.g. `https://your-app.domain.com`)
2. In the Meta developer dashboard → Instagram API → Configure webhooks:
   - **Callback URL**: `https://your-app.domain.com/webhook`
   - **Verify Token**: value of `WEBHOOK_VERIFY_TOKEN` in your `.env`
3. Subscribe to the **messages** field
4. Set App Mode to **Live**

---

## REST API

| Method | Path | Description |
|--------|------|-------------|
| `GET`  | `/` | Web UI |
| `GET`  | `/api/conversations` | List all conversations |
| `GET`  | `/api/conversations/:id/messages` | Messages in a conversation |
| `POST` | `/api/send` | Send a message |
| `GET`  | `/webhook` | Meta webhook verification |
| `POST` | `/webhook` | Receive incoming messages |
| `GET`  | `/health` | Health check |

**Send message payload:**
```json
{
  "recipient_id": "INSTAGRAM_USER_ID",
  "text": "Hello!",
  "media_url": "https://example.com/img.jpg",  // optional
  "media_type": "image"                          // optional
}
```

---

## AI Integration

In `app.py`, find the comment block:

```python
# AI HOOK — plug your AI response logic here
# Example:
#   ai_reply = ai_module.generate_reply(msg["text"])
#   ig.send_text_message(sender_id, ai_reply)
```

Replace it with your AI call (OpenAI, Gemini, local LLM, etc.).

---

## Credentials

Store all secrets in `.env` (never commit this file).
