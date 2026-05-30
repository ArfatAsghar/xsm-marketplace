"""routes/chat.py — inbox, chat, 2FA code, and notification API."""
import time
from flask import (Blueprint, render_template, request,
                   redirect, url_for, session, flash, jsonify)

from utils.auth  import login_required
from utils.db    import safe_db_reference, get_user_public, is_valid_id
from utils.notifications import push_notification

chat_bp = Blueprint("chat", __name__)


# ── Notification API ──────────────────────────────────────────────────────────

@chat_bp.route("/api/notifications")
def api_notifications():
    if "user" not in session:
        return jsonify({"error": "unauthorized"}), 401
    uid  = session["user"]["uid"]
    raw  = safe_db_reference(f"notifications/{uid}").get() or {}
    notifs = [
        {"id": nid, **n}
        for nid, n in raw.items()
    ]
    notifs.sort(key=lambda x: x.get("created_at",""), reverse=True)
    unread = sum(1 for n in notifs if not n.get("read", False))
    return jsonify({"notifications": notifs[:20], "unread": unread})


@chat_bp.route("/api/notifications/mark_read", methods=["POST"])
def api_mark_notifications_read():
    if "user" not in session:
        return jsonify({"error": "unauthorized"}), 401
    uid = session["user"]["uid"]
    nid = request.json.get("id") if request.is_json else request.form.get("id")
    ref = safe_db_reference(f"notifications/{uid}")
    if nid:
        ref.child(nid).update({"read": True})
    else:
        for key in (ref.get() or {}):
            ref.child(key).update({"read": True})
    return jsonify({"ok": True})


# ── Inbox ─────────────────────────────────────────────────────────────────────

@chat_bp.route("/inbox")
@login_required
def inbox():
    current_uid = session["user"]["uid"]
    all_chats   = safe_db_reference("chats").get() or {}
    all_users   = safe_db_reference("users").get() or {}
    user_chats  = {}

    for chat_id, chat_data in all_chats.items():
        if not isinstance(chat_data, dict): continue
        if current_uid not in chat_data.get("participants",{}): continue
        parts = chat_id.split("_")
        if len(parts) != 2: continue

        buyer, seller = parts
        other_uid   = seller if buyer == current_uid else buyer
        other_user  = all_users.get(other_uid, {})
        username    = other_user.get("username","Unknown")    if isinstance(other_user, dict) else "Unknown"
        profile_pic = other_user.get("profile_pic","/static/default_user.png") if isinstance(other_user, dict) else "/static/default_user.png"

        msgs   = safe_db_reference(f"chats/{chat_id}/messages").get() or {}
        unread = sum(1 for m in (msgs.values() if isinstance(msgs, dict) else [])
                     if isinstance(m, dict) and m.get("sender") != current_uid and not m.get("read",False))

        user_chats[chat_id] = {
            "buyer": buyer, "seller": seller,
            "other_username": username, "other_user_pic": profile_pic,
            "last_message": chat_data.get("last_message",""),
            "last_time":    chat_data.get("last_time",""),
            "last_sender":  chat_data.get("last_sender",""),
            "unread_count": unread,
        }

    return render_template("inbox.html", chats=user_chats)


# ── Chat ──────────────────────────────────────────────────────────────────────

@chat_bp.route("/chat/<buyer_uid>/<seller_uid>")
@login_required
def chat(buyer_uid, seller_uid):
    current_uid = session["user"]["uid"]
    if current_uid not in [buyer_uid, seller_uid]:
        flash("Access denied.", "danger")
        return redirect(url_for("chat.inbox"))

    chat_id   = "_".join(sorted([buyer_uid, seller_uid]))
    
    # Mark incoming messages from the other user as read
    db_ref = safe_db_reference(f"chats/{chat_id}/messages")
    messages_to_update = db_ref.get() or {}
    if isinstance(messages_to_update, dict):
        for msg_key, msg_data in messages_to_update.items():
            if isinstance(msg_data, dict) and msg_data.get("sender") != current_uid and not msg_data.get("read", False):
                db_ref.child(msg_key).update({"read": True})
    elif isinstance(messages_to_update, list):
        for index, msg_data in enumerate(messages_to_update):
            if isinstance(msg_data, dict) and msg_data.get("sender") != current_uid and not msg_data.get("read", False):
                db_ref.child(str(index)).update({"read": True})

    raw       = safe_db_reference(f"chats/{chat_id}/messages").get() or {}
    messages  = raw if isinstance(raw, dict) else {str(i): m for i, m in enumerate(raw)}
    other_uid = seller_uid if current_uid == buyer_uid else buyer_uid
    other     = get_user_public(other_uid)

    return render_template("chat.html",
                           messages=messages, chat_id=chat_id,
                           buyer_uid=buyer_uid, seller_uid=seller_uid,
                           other_user_id=other_uid,
                           other_user_name=other.get("username","Unknown"),
                           other_user_pic=other.get("profile_pic","/static/default_user.png"))


@chat_bp.route("/chat_messages/<chat_id>")
@login_required
def chat_messages_json(chat_id):
    uid   = session["user"]["uid"]
    parts = chat_id.split("_")
    if len(parts) != 2 or uid not in parts:
        return jsonify({"error": "forbidden"}), 403

    # Mark incoming messages from the other user as read on active polling
    db_ref = safe_db_reference(f"chats/{chat_id}/messages")
    messages_to_update = db_ref.get() or {}
    if isinstance(messages_to_update, dict):
        for msg_key, msg_data in messages_to_update.items():
            if isinstance(msg_data, dict) and msg_data.get("sender") != uid and not msg_data.get("read", False):
                db_ref.child(msg_key).update({"read": True})
    elif isinstance(messages_to_update, list):
        for index, msg_data in enumerate(messages_to_update):
            if isinstance(msg_data, dict) and msg_data.get("sender") != uid and not msg_data.get("read", False):
                db_ref.child(str(index)).update({"read": True})

    raw  = safe_db_reference(f"chats/{chat_id}/messages").get() or {}
    msgs = raw if isinstance(raw, dict) else {str(i): m for i, m in enumerate(raw)}
    return jsonify({"messages": msgs})


@chat_bp.route("/send_message", methods=["POST"])
@login_required
def send_message():
    chat_id      = request.form["chat_id"]
    sender_uid   = request.form["sender_uid"]
    receiver_uid = request.form["receiver_uid"]
    message_text = request.form["message"].strip()

    if sender_uid != session["user"]["uid"]:
        flash("Unauthorized.", "danger")
        return redirect(url_for("chat.inbox"))
    if not message_text:
        flash("Message cannot be empty.", "warning")
        return redirect(url_for("chat.chat", buyer_uid=sender_uid, seller_uid=receiver_uid))

    ts = int(time.time() * 1000)
    safe_db_reference(f"chats/{chat_id}/messages").push({
        "sender": sender_uid, "message": message_text,
        "timestamp": ts, "read": False,
    })
    meta = safe_db_reference(f"chats/{chat_id}").get() or {}
    parts = meta.get("participants", {})
    parts[sender_uid] = parts[receiver_uid] = True
    safe_db_reference(f"chats/{chat_id}").update({
        "last_message": message_text, "last_time": ts,
        "last_sender": sender_uid, "participants": parts,
    })
    push_notification(receiver_uid,
        f"New message from {session['user'].get('username','Someone')}.",
        "info", f"/chat/{sender_uid}/{receiver_uid}")

    return redirect(url_for("chat.chat", buyer_uid=sender_uid, seller_uid=receiver_uid))


# ── 2FA code ──────────────────────────────────────────────────────────────────

@chat_bp.route("/send_code/<listing_id>", methods=["POST"])
@login_required
def send_code(listing_id):
    seller_uid  = session["user"]["uid"]
    listing_ref = safe_db_reference("listings", listing_id)
    listing     = listing_ref.get()

    if not listing or listing.get("seller_uid") != seller_uid:
        flash("Not authorised.", "danger")
        return redirect(url_for("misc.dashboard"))

    code      = request.form.get("code","").strip()
    buyer_uid = listing.get("buyer_uid")
    if not code:
        flash("Code cannot be empty.", "warning")
        return redirect(url_for("chat.chat", buyer_uid=buyer_uid, seller_uid=seller_uid))
    if not buyer_uid:
        flash("No buyer found.", "danger")
        return redirect(url_for("misc.dashboard"))

    ts      = int(time.time() * 1000)
    chat_id = "_".join(sorted([buyer_uid, seller_uid]))
    safe_db_reference(f"chats/{chat_id}/messages").push({
        "sender": seller_uid, "message": code,
        "type": "2fa_code", "timestamp": ts, "read": False,
    })
    safe_db_reference(f"chats/{chat_id}").update({
        "last_message": "🔐 2FA Code sent", "last_time": ts,
        "last_sender": seller_uid,
        "participants": {buyer_uid: True, seller_uid: True},
    })
    flash("Code sent to buyer!", "success")
    return redirect(url_for("chat.chat", buyer_uid=buyer_uid, seller_uid=seller_uid))
