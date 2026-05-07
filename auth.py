from functools import wraps
from flask import session, redirect, url_for, request, jsonify


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            if request.is_json or request.path.startswith("/api/"):
                return jsonify({"error": "Unauthorized"}), 401
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


def api_key_required(f):
    """For public /api/v1/* endpoints used by external apps."""
    @wraps(f)
    def decorated(*args, **kwargs):
        import db
        key = request.headers.get("X-API-Key") or request.args.get("api_key")
        if not key:
            return jsonify({"error": "Missing API key. Send X-API-Key header."}), 401
        api_key_obj = db.validate_api_key(key)
        if not api_key_obj:
            return jsonify({"error": "Invalid or revoked API key."}), 403
        request.api_key_obj = api_key_obj
        return f(*args, **kwargs)
    return decorated
