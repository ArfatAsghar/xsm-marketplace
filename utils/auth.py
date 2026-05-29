"""utils/auth.py — decorators, password rules, file validation."""
from functools import wraps
from flask import session, flash, redirect, url_for
from utils.config import ALLOWED_EXTENSIONS


def require_auth() -> bool:
    return "user" in session


def login_required(f):
    """Redirect to login if not authenticated."""
    @wraps(f)
    def wrapped(*args, **kwargs):
        if not require_auth():
            flash("Please log in first.", "warning")
            return redirect(url_for("auth.login"))
        return f(*args, **kwargs)
    return wrapped


def admin_required(f):
    """Redirect to login if not an admin."""
    @wraps(f)
    def wrapped(*args, **kwargs):
        if "user" not in session or session["user"].get("role") != "admin":
            flash("Access denied. Admins only.", "danger")
            return redirect(url_for("auth.login"))
        return f(*args, **kwargs)
    return wrapped


def is_admin() -> bool:
    return session.get("user", {}).get("role") == "admin"


def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def check_password_strength(password: str) -> tuple[bool, str]:
    """Returns (ok, message). Rules: ≥8 chars, digit, uppercase, special char."""
    if len(password) < 8:
        return False, "Password must be at least 8 characters."
    if not any(c.isdigit() for c in password):
        return False, "Password must contain at least one number."
    if not any(c.isupper() for c in password):
        return False, "Password must contain at least one uppercase letter."
    if not any(c in "!@#$%^&*()_+-=[]{}|;':\",./<>?" for c in password):
        return False, "Password must contain at least one special character."
    return True, "OK"
