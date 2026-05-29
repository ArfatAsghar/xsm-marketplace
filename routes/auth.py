"""routes/auth.py — signup, login, logout, password reset, email verification."""
import random
import string
import time
import requests
import yagmail
from datetime import datetime as dt
from flask import (Blueprint, render_template, request,
                   redirect, url_for, session, flash)
from firebase_admin import auth as admin_auth
from werkzeug.utils import secure_filename

from extensions import limiter
from utils.auth import check_password_strength, allowed_file
from utils.db   import safe_db_reference, invalidate_users_cache
from utils.config import FIREBASE_API_KEY, GMAIL_USER, GMAIL_APP_PASSWORD

auth_bp = Blueprint("auth", __name__)


# ── Firebase REST helpers ─────────────────────────────────────────────────────

def _sign_in(email: str, password: str) -> dict:
    url = (
        "https://identitytoolkit.googleapis.com/v1/"
        f"accounts:signInWithPassword?key={FIREBASE_API_KEY}"
    )
    return requests.post(
        url,
        json={"email": email, "password": password, "returnSecureToken": True},
        timeout=5,
    ).json()


def get_id_token(email: str, password: str) -> str | None:
    return _sign_in(email, password).get("idToken")


def send_verification_email(id_token: str):
    url = (
        "https://identitytoolkit.googleapis.com/v1/"
        f"accounts:sendOobCode?key={FIREBASE_API_KEY}"
    )
    try:
        requests.post(
            url,
            json={"requestType": "VERIFY_EMAIL", "idToken": id_token},
            timeout=5,
        )
    except Exception as e:
        print(f"[EMAIL VERIFY] {e}")


# ── Signup ────────────────────────────────────────────────────────────────────

@auth_bp.route("/signup", methods=["GET", "POST"])
@limiter.limit("10 per hour")
def signup():
    if request.method == "POST":
        username    = request.form.get("username",    "").strip()
        email       = request.form.get("email",       "").strip().lower()
        password    = request.form.get("password",    "")
        dob         = request.form.get("dob",         "")
        address     = request.form.get("address",     "").strip()
        phone       = request.form.get("phone",       "").strip()
        country_code = request.form.get("country_code", "+92").strip()
        profile_pic = request.files.get("profile_pic")

        if not all([username, email, password, dob, address, phone]):
            flash("⚠️ All fields are required.", "danger")
            return render_template("signup.html")

        strong, msg = check_password_strength(password)
        if not strong:
            flash(f"⚠️ {msg}", "danger")
            return render_template("signup.html")

        # International phone: 6–15 digits
        digits_only = "".join(c for c in phone if c.isdigit())
        if not (6 <= len(digits_only) <= 15):
            flash("⚠️ Enter a valid phone number (6–15 digits).", "danger")
            return render_template("signup.html")

        # Build E.164 number for Firebase Auth
        dial = country_code.replace("-CA", "")  # normalise +1-CA → +1
        full_phone_e164 = f"{dial}{digits_only}"

        pic_url = "/static/default_user.png"
        if profile_pic and profile_pic.filename:
            if allowed_file(profile_pic.filename):
                ext = profile_pic.filename.rsplit(".", 1)[1].lower()
                ts  = dt.now().strftime("%Y%m%d_%H%M%S")
                pic_filename = secure_filename(f"{username}_{ts}.{ext}")
                try:
                    from utils.storage import upload_to_supabase
                    uploaded_url = upload_to_supabase(profile_pic, "profile_pics", pic_filename)
                    if uploaded_url:
                        pic_url = uploaded_url
                    else:
                        flash("⚠️ Failed to upload profile picture to Supabase. Using default.", "warning")
                except Exception as upload_err:
                    print(f"[SIGNUP PROFILE PIC ERROR] {upload_err}")
            else:
                flash("⚠️ Invalid file type for profile picture. Using default.", "warning")

        try:
            user_record = admin_auth.create_user(
                email=email,
                password=password,
                display_name=username,
                phone_number=full_phone_e164,
            )
            safe_db_reference("users", user_record.uid).set({
                "username":       username,
                "email":          email,
                "role":           "user",
                "profile_pic":    pic_url,
                "dob":            dob,
                "address":        address,
                "phone":          digits_only,
                "country_code":   dial,
                "average_rating": 0,
                "credit":         0,
                "email_verified": False,
                "verified":       False,
                "kyc_verified":   False,
                "created_at":     dt.now().isoformat(),
            })

            id_token = get_id_token(email, password)
            if id_token:
                send_verification_email(id_token)

            flash(
                "✅ Account created! Check your email and verify before logging in.",
                "success",
            )
            return redirect(url_for("auth.login"))
        except Exception as e:
            flash(f"❌ Signup error: {e}", "danger")

    return render_template("signup.html")


# ── Login ─────────────────────────────────────────────────────────────────────

@auth_bp.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per minute")
def login():
    if request.method == "POST":
        email    = request.form.get("email",    "").strip().lower()
        password = request.form.get("password", "")

        if not email or not password:
            flash("Email and password are required.", "danger")
            return render_template("login.html")

        result = _sign_in(email, password)

        if "idToken" not in result:
            flash(result.get("error", {}).get("message", "Login failed"), "danger")
            return render_template("login.html")

        uid      = result["localId"]
        id_token = result["idToken"]

        try:
            lookup = requests.post(
                f"https://identitytoolkit.googleapis.com/v1/"
                f"accounts:lookup?key={FIREBASE_API_KEY}",
                json={"idToken": id_token},
                timeout=5,
            ).json()
            email_verified = lookup.get("users", [{}])[0].get("emailVerified", False)
        except Exception:
            email_verified = False

        if not email_verified:
            session["unverified_token"] = id_token
            session["unverified_email"] = email
            flash(
                "📧 Please verify your email before logging in. "
                "Check your inbox or "
                "<a href='/resend_verification' class='underline font-semibold'>resend the link</a>.",
                "warning",
            )
            return render_template("login.html")

        try:
            profile = safe_db_reference("users", uid).get() or {}
            pic     = profile.get("profile_pic") or "/static/default_user.png"
            if pic and not pic.startswith("/static/") and not pic.startswith("http"):
                pic = f"/static/profile_pics/{pic}"
            session.permanent = True
            session["user"] = {
                "uid":         uid,
                "email":       email,
                "username":    profile.get("username", "User"),
                "role":        profile.get("role", "user"),
                "profile_pic": pic,
                "credit":      int(profile.get("credit", 0)),
            }
            safe_db_reference("users", uid).update({"email_verified": True})
            session.pop("unverified_token", None)
            session.pop("unverified_email", None)
            flash("Logged in successfully.", "success")
            if session["user"]["role"] == "admin":
                return redirect(url_for("admin.admin_users"))
            return redirect(url_for("misc.dashboard"))
        except Exception:
            flash("Error accessing user data.", "danger")
            return render_template("login.html")

    return render_template("login.html")


# ── Logout ────────────────────────────────────────────────────────────────────

@auth_bp.route("/logout")
def logout():
    session.clear()
    flash("Logged out.", "success")
    return redirect(url_for("auth.login"))


# ── Email verification ────────────────────────────────────────────────────────

@auth_bp.route("/resend_verification")
def resend_verification():
    id_token = session.get("unverified_token")
    email    = session.get("unverified_email", "your email")
    if not id_token:
        flash("No pending verification. Please log in again.", "warning")
        return redirect(url_for("auth.login"))
    send_verification_email(id_token)
    flash(f"📧 Verification email resent to {email}.", "success")
    return redirect(url_for("auth.login"))


# ── Password reset ────────────────────────────────────────────────────────────

@auth_bp.route("/forgot", methods=["GET", "POST"])
@limiter.limit("5 per hour")
def forgot():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        if not email:
            flash("Email required.", "danger")
            return render_template("forgot.html")
        try:
            user_record = admin_auth.get_user_by_email(email)
            otp = "".join(random.choices(string.digits, k=6))
            yagmail.SMTP(GMAIL_USER, GMAIL_APP_PASSWORD).send(
                email, "XSM Password Reset Code", f"Your reset code is: {otp}"
            )
            session.update({
                "reset_email": email,
                "reset_uid":   user_record.uid,
                "reset_otp":   otp,
                "reset_time":  time.time(),
            })
            flash("Reset code sent. Valid for 1 minute.", "success")
            return redirect(url_for("auth.verify_reset"))
        except Exception as e:
            flash(f"Error: {e}", "danger")
    return render_template("forgot.html")


@auth_bp.route("/verify_reset", methods=["GET", "POST"])
def verify_reset():
    if request.method == "POST":
        code       = request.form.get("code", "").strip()
        saved_code = session.get("reset_otp")
        saved_time = session.get("reset_time")
        if not saved_code or not saved_time:
            flash("No reset request found.", "danger")
            return redirect(url_for("auth.forgot"))
        if time.time() - saved_time > 60:
            flash("Code expired. Request a new one.", "danger")
            return redirect(url_for("auth.forgot"))
        if code == saved_code:
            return redirect(url_for("auth.new_password"))
        flash("Invalid code.", "danger")
    return render_template("verify_reset.html")


@auth_bp.route("/resend_otp")
@limiter.limit("5 per hour")
def resend_otp():
    email = session.get("reset_email")
    uid   = session.get("reset_uid")
    if not email or not uid:
        flash("No reset request found.", "danger")
        return redirect(url_for("auth.forgot"))
    try:
        otp = "".join(random.choices(string.digits, k=6))
        yagmail.SMTP(GMAIL_USER, GMAIL_APP_PASSWORD).send(
            email, "XSM Password Reset Code", f"Your new code is: {otp}"
        )
        session["reset_otp"]  = otp
        session["reset_time"] = time.time()
        flash("New code sent.", "success")
        return redirect(url_for("auth.verify_reset"))
    except Exception as e:
        flash(f"Error: {e}", "danger")
        return redirect(url_for("auth.forgot"))


@auth_bp.route("/new_password", methods=["GET", "POST"])
def new_password():
    if request.method == "POST":
        new_pass = request.form.get("password", "")
        strong, msg = check_password_strength(new_pass)
        if not strong:
            flash(f"⚠️ {msg}", "danger")
            return render_template("new_password.html")
        try:
            admin_auth.update_user(session.get("reset_uid"), password=new_pass)
            for k in ("reset_uid", "reset_email", "reset_otp"):
                session.pop(k, None)
            flash("Password reset! Please log in.", "success")
            return redirect(url_for("auth.login"))
        except Exception as e:
            flash(f"Error: {e}", "danger")
    return render_template("new_password.html")


# ── Profile settings ──────────────────────────────────────────────────────────

@auth_bp.route("/settings/profile", methods=["GET", "POST"])
def settings_profile():
    if "user" not in session:
        return redirect(url_for("auth.login"))

    import os, time as _time
    from flask import current_app

    uid      = session["user"]["uid"]
    user_ref = safe_db_reference("users", uid)

    if request.method == "POST":
        updates = {}
        pic = request.files.get("profile_pic")
        if pic and pic.filename:
            if allowed_file(pic.filename):
                ext      = pic.filename.rsplit(".", 1)[1].lower()
                filename = f"{uid}_{int(_time.time())}.{ext}"
                try:
                    from utils.storage import upload_to_supabase
                    uploaded_url = upload_to_supabase(pic, "profile_pics", filename)
                    if uploaded_url:
                        updates["profile_pic"] = uploaded_url
                        session["user"]["profile_pic"] = uploaded_url
                        session.modified = True
                    else:
                        flash("⚠️ Failed to upload image to Supabase Storage.", "danger")
                        return redirect(url_for("auth.settings_profile"))
                except Exception as upload_err:
                    print(f"[SETTINGS PROFILE PIC ERROR] {upload_err}")
                    flash("⚠️ Failed to upload image to storage.", "danger")
                    return redirect(url_for("auth.settings_profile"))
            else:
                flash("Invalid file type.", "danger")
                return redirect(url_for("auth.settings_profile"))

        for field in ("phone", "address", "bio"):
            val = request.form.get(field, "").strip()
            if val:
                updates[field] = val

        if updates:
            user_ref.update(updates)
            invalidate_users_cache()
            flash("Profile updated!", "success")
        else:
            flash("No changes detected.", "info")

        return redirect(url_for("auth.settings_profile"))

    return render_template("settings_profile.html", user=user_ref.get() or {})
