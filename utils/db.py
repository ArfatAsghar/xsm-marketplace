"""utils/db.py — Firebase helpers, cached reads, and data utilities."""
import statistics
from datetime import datetime as dt
from firebase_admin import db
from extensions import cache


# ── Basic helpers ─────────────────────────────────────────────────────────────

def is_valid_id(value) -> bool:
    return bool(value and isinstance(value, str) and value.strip())


def safe_db_reference(path: str, child_id: str = None):
    if child_id and not is_valid_id(child_id):
        raise ValueError(f"Invalid ID: {child_id}")
    ref = db.reference(path)
    return ref.child(child_id) if child_id else ref


# ── Cache helpers ─────────────────────────────────────────────────────────────

@cache.cached(timeout=60, key_prefix="all_listings")
def get_all_listings() -> dict:
    return safe_db_reference("listings").get() or {}


@cache.cached(timeout=120, key_prefix="all_users")
def get_all_users() -> dict:
    return safe_db_reference("users").get() or {}


@cache.cached(timeout=30, key_prefix="all_payments")
def get_all_payments() -> dict:
    return safe_db_reference("payments").get() or {}


def invalidate_listings_cache():
    cache.delete("all_listings")


def invalidate_users_cache():
    cache.delete("all_users")


# ── User helpers ──────────────────────────────────────────────────────────────

def get_user_public(uid: str) -> dict:
    _default = {
        "username": "Unknown",
        "profile_pic": "/static/default_user.png",
        "average_rating": 0,
        "verified": False,
    }
    if not is_valid_id(uid):
        return _default
    try:
        data = get_all_users().get(uid) or safe_db_reference("users", uid).get() or {}
        return {
            "username":       data.get("username", "Unknown"),
            "profile_pic":    data.get("profile_pic", "/static/default_user.png"),
            "average_rating": round(float(data.get("average_rating", 0) or 0), 1),
            "verified":       bool(data.get("verified", False)),
        }
    except Exception:
        return _default


def get_admin_uid() -> str:
    """Return the UID of the first admin user found."""
    try:
        for uid, u in (safe_db_reference("users").get() or {}).items():
            if isinstance(u, dict) and u.get("role") == "admin":
                return uid
    except Exception:
        pass
    return ""


# ── Ratings helpers ───────────────────────────────────────────────────────────

def compute_avg_rating_from_reviews(seller_id: str) -> float:
    if not is_valid_id(seller_id):
        return 0.0
    try:
        reviews = safe_db_reference("reviews", seller_id).get() or {}
        ratings = [
            int(r.get("rating", 0))
            for r in reviews.values()
            if str(r.get("rating", "")).isdigit()
        ]
        return round(statistics.mean(ratings), 1) if ratings else 0.0
    except (ValueError, TypeError):
        return 0.0


def star_breakdown(avg: float) -> tuple[int, int, int]:
    try:
        full  = int(avg)
        half  = 1 if (avg - full) >= 0.5 and full < 5 else 0
        empty = 5 - full - half
        return full, half, empty
    except (ValueError, TypeError):
        return 0, 0, 5


# ── Media helpers ─────────────────────────────────────────────────────────────

def resolve_media_url(raw_url, default="/static/default_thumbnail.png") -> str:
    if not raw_url:
        return default
    raw_url = raw_url.strip()
    if raw_url.startswith("/static/") or raw_url.startswith("http"):
        return raw_url
    return f"/static/listing_media/{raw_url}"


# ── Listing boost ─────────────────────────────────────────────────────────────

def is_listing_boosted(listing: dict) -> bool:
    if not listing.get("boosted"):
        return False
    boosted_until = listing.get("boosted_until")
    if not boosted_until:
        return False
    try:
        return dt.utcnow() < dt.fromisoformat(boosted_until)
    except Exception:
        return False


# ── Platform fee ──────────────────────────────────────────────────────────────

from utils.config import PLATFORM_FEE_PCT  # noqa: E402 (avoid circular at top)


def calculate_fee(amount: int) -> tuple[int, int]:
    fee = int(round(amount * PLATFORM_FEE_PCT / 100))
    return fee, amount - fee


def record_platform_revenue(fee: int, listing_id: str):
    try:
        rev_ref  = db.reference("revenue")
        rev_data = rev_ref.get() or {}
        rev_ref.update({
            "total":      int(rev_data.get("total", 0)) + fee,
            "updated_at": dt.utcnow().isoformat(),
        })
        db.reference("revenue/transactions").push({
            "listing_id": listing_id,
            "fee":        fee,
            "timestamp":  dt.utcnow().isoformat(),
        })
    except Exception as e:
        print(f"[REVENUE ERROR] {e}")
