"""utils/context.py — Flask context processor (runs on every request)."""
from flask import session, request
from utils.db import safe_db_reference


def inject_globals():
    count  = 0
    credit = session.get("user", {}).get("credit", 0)
    endpoint = request.endpoint or ""
    seo_robots = "index,follow"

    if endpoint.startswith(("admin.", "auth.", "wallet.", "chat.", "reports.")) or endpoint in {
        "misc.dashboard",
        "misc.add_listing",
        "misc.edit_listing",
        "misc.settings_profile",
        "misc.withdraw",
        "misc.kyc_submit",
    }:
        seo_robots = "noindex,nofollow"

    if "user" in session:
        uid = session["user"]["uid"]
        try:
            user_data = safe_db_reference("users", uid).get() or {}
            credit    = int(user_data.get("credit", 0))
            theme     = user_data.get("theme", "cyberpunk")
            session["user"]["credit"] = credit
            session["user"]["theme"]  = theme
            session.modified = True
        except Exception:
            pass
        try:
            raw   = safe_db_reference(f"notifications/{uid}").get() or {}
            count = sum(
                1 for n in raw.values()
                if isinstance(n, dict) and not n.get("read", False)
            )
        except Exception:
            pass

    return {
        "unread_notif_count": count,
        "current_credit": credit,
        "seo_robots": seo_robots,
    }
