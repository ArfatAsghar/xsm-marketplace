"""utils/notifications.py — in-app notifications and email helpers."""
import yagmail
from datetime import datetime as dt
from utils.db import safe_db_reference, is_valid_id
from utils.config import GMAIL_USER, GMAIL_APP_PASSWORD


# ── In-app notifications ──────────────────────────────────────────────────────

def push_notification(uid: str, message: str,
                      notif_type: str = "info", link: str = "/dashboard"):
    if not is_valid_id(uid):
        return
    try:
        safe_db_reference(f"notifications/{uid}").push({
            "message":    message,
            "type":       notif_type,
            "link":       link,
            "read":       False,
            "created_at": dt.utcnow().isoformat(),
        })
    except Exception as e:
        print(f"[NOTIFICATION ERROR] {e}")


# ── Audit log ─────────────────────────────────────────────────────────────────

def write_audit(action: str, admin_uid: str, admin_name: str,
                target_id: str = "", detail: str = ""):
    try:
        safe_db_reference("audit_log").push({
            "action":     action,
            "admin_uid":  admin_uid,
            "admin_name": admin_name,
            "target_id":  target_id,
            "detail":     detail,
            "timestamp":  dt.utcnow().isoformat(),
        })
    except Exception as e:
        print(f"[AUDIT LOG ERROR] {e}")


# ── Email helpers ─────────────────────────────────────────────────────────────

def send_email(to: str, subject: str, body: str):
    try:
        yagmail.SMTP({GMAIL_USER: "XSM Marketplace"}, GMAIL_APP_PASSWORD).send(
            to=to, subject=subject, contents=body
        )
    except Exception as e:
        print(f"[EMAIL ERROR] {e}")


def notify_seller_new_purchase(seller_email, seller_name,
                                listing_username, platform, amount):
    send_email(
        to=seller_email,
        subject="🛒 Your listing was purchased — XSM Market",
        body=(
            f"Hi {seller_name},\n\n"
            f'Your {platform} account "@{listing_username}" was purchased for {amount} credits.\n'
            f"Credits are held in escrow until the buyer confirms receipt.\n\n"
            f"👉 https://xsmmarket.com/dashboard\n\n— XSM Market"
        ),
    )


def notify_buyer_credentials_delivered(buyer_email, buyer_name,
                                        listing_username, platform):
    send_email(
        to=buyer_email,
        subject="🔑 Your account credentials are ready — XSM Market",
        body=(
            f"Hi {buyer_name},\n\n"
            f'Credentials for the {platform} account "@{listing_username}" have been delivered.\n\n'
            f"👉 View and confirm: https://xsmmarket.com/dashboard\n\n— XSM Market"
        ),
    )


def notify_seller_confirmed(seller_email, seller_name, listing_username, amount):
    send_email(
        to=seller_email,
        subject="✅ Payment released — XSM Market",
        body=(
            f'Hi {seller_name},\n\nBuyer confirmed "@{listing_username}".\n'
            f"{amount} credits released to your wallet.\n\n"
            f"👉 https://xsmmarket.com/dashboard\n\n— XSM Market"
        ),
    )


def notify_dispute_raised(buyer_email, seller_email, admin_email,
                           buyer_name, seller_name, listing_username, reason):
    msg = (
        f'Dispute raised on "@{listing_username}".\n\n'
        f"Buyer: {buyer_name}\nSeller: {seller_name}\n"
        f"Reason: {reason or 'No reason provided'}\n\n"
        f"Credits remain frozen in escrow.\n"
        f"👉 https://xsmmarket.com/dashboard\n\n— XSM Market"
    )
    send_email(to=buyer_email,  subject="⚠️ Dispute raised — XSM Market", body=msg)
    send_email(to=seller_email, subject="⚠️ Dispute on your listing — XSM Market", body=msg)
    send_email(to=admin_email,  subject=f"🚨 Admin: Dispute on @{listing_username}", body=msg)
