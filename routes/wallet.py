"""routes/wallet.py — wallet, deposit payments, withdrawals."""
import uuid, os
from datetime import datetime as dt
from flask import (Blueprint, render_template, request,
                   redirect, url_for, session, flash)
from werkzeug.utils import secure_filename

from utils.auth  import login_required, allowed_file
from utils.db    import (safe_db_reference, invalidate_users_cache)
from utils.notifications import push_notification, write_audit, send_email
from utils.config import GMAIL_USER, GMAIL_APP_PASSWORD, PLATFORM_FEE_PCT

wallet_bp = Blueprint("wallet", __name__)


# ── Wallet overview ───────────────────────────────────────────────────────────

@wallet_bp.route("/wallet")
@login_required
def wallet():
    user_id = session["user"]["uid"]

    user_data   = safe_db_reference("users", user_id).get() or {}
    credit      = int(user_data.get("credit", 0))
    kyc_ok      = bool(user_data.get("kyc_verified", False))
    session["user"]["credit"] = credit

    all_payments    = safe_db_reference("payments").get() or {}
    all_withdrawals = safe_db_reference("withdrawals").get() or {}
    purchases       = safe_db_reference("purchases", user_id).get() or {}
    all_listings    = safe_db_reference("listings").get() or {}

    payments    = {pid: p for pid, p in all_payments.items()    if p.get("user_id") == user_id}
    withdrawals = {wid: w for wid, w in all_withdrawals.items() if w.get("user_id") == user_id}
    sales = {
        lid: l for lid, l in all_listings.items()
        if l.get("seller_uid") == user_id
        and l.get("status") in ("completed","pending_delivery","pending_confirmation")
    }

    total_earned    = sum(int(l.get("escrow",{}).get("seller_gets",
                              l.get("escrow",{}).get("amount",0)))
                         for l in sales.values() if l.get("status") == "completed")
    total_spent     = sum(int(p.get("price",0)) for p in purchases.values())
    total_deposited = sum(int(p.get("approved_amount",0))
                         for p in payments.values() if p.get("status") == "approved")
    total_withdrawn = sum(int(w.get("amount",0))
                         for w in withdrawals.values() if w.get("status") == "approved")

    return render_template("wallet.html",
                           credit=credit, payments=payments, withdrawals=withdrawals,
                           purchases=purchases, sales=sales,
                           total_earned=total_earned, total_spent=total_spent,
                           total_deposited=total_deposited, total_withdrawn=total_withdrawn,
                           kyc_ok=kyc_ok, platform_fee_pct=PLATFORM_FEE_PCT)


# ── Add payment (deposit request) ─────────────────────────────────────────────

@wallet_bp.route("/wallet/add_payment", methods=["GET", "POST"])
@login_required
def add_payment():
    from flask import current_app
    user = session["user"]

    if request.method == "POST":
        slip          = request.files.get("slip")
        slip_url      = None
        if slip and slip.filename and allowed_file(slip.filename):
            slip_filename = f"{uuid.uuid4().hex}_{secure_filename(slip.filename)}"
            try:
                from utils.storage import upload_to_supabase
                uploaded_url = upload_to_supabase(slip, "payment_slips", slip_filename)
                if uploaded_url:
                    slip_url = uploaded_url
            except Exception as upload_err:
                print(f"[PAYMENT SLIP UPLOAD ERROR] {upload_err}")

        safe_db_reference("payments").push({
            "user_id":    user.get("uid"),
            "name":       request.form.get("name","").strip(),
            "email":      request.form.get("email","").strip(),
            "cnic":       request.form.get("cnic","").strip(),
            "method":     request.form.get("method","").strip(),
            "amount":     request.form.get("amount","").strip(),
            "account_no": request.form.get("account_no","").strip(),
            "slip_url":   slip_url,
            "status":     "pending",
            "created_at": dt.utcnow().isoformat(),
        })
        flash("Payment request submitted!", "success")
        return redirect(url_for("misc.dashboard"))

    return render_template("add_payment.html", user=user)


# ── Withdraw ──────────────────────────────────────────────────────────────────

@wallet_bp.route("/wallet/withdraw", methods=["GET", "POST"])
@login_required
def withdraw():
    user    = session["user"]
    user_id = user["uid"]

    user_data = safe_db_reference("users", user_id).get() or {}
    credit    = int(user_data.get("credit", 0))
    kyc_ok    = bool(user_data.get("kyc_verified", False))

    if not kyc_ok:
        flash("KYC verification required before withdrawing.", "warning")
        return redirect(url_for("wallet.wallet"))

    if request.method == "POST":
        amount_str   = request.form.get("amount","").strip()
        method       = request.form.get("method","").strip()
        account_no   = request.form.get("account_no","").strip()
        account_name = request.form.get("account_name","").strip()

        if not amount_str.isdigit() or int(amount_str) <= 0:
            flash("Enter a valid amount.", "danger")
            return render_template("withdraw.html", credit=credit, user=user)

        amount = int(amount_str)
        if amount < 10:
            flash("Minimum withdrawal is $10.", "danger")
            return render_template("withdraw.html", credit=credit, user=user)
        if amount > credit:
            flash(f"Insufficient balance. You have ${credit}.", "danger")
            return render_template("withdraw.html", credit=credit, user=user)
        if not all([method, account_no, account_name]):
            flash("All fields are required.", "danger")
            return render_template("withdraw.html", credit=credit, user=user)

        safe_db_reference("users", user_id).update({"credit": credit - amount})
        session["user"]["credit"] = credit - amount
        invalidate_users_cache()

        safe_db_reference("withdrawals").push({
            "user_id": user_id, "username": user.get("username"),
            "email": user.get("email"), "amount": amount,
            "method": method, "account_no": account_no,
            "account_name": account_name, "status": "pending",
            "created_at": dt.utcnow().isoformat(),
        })
        send_email(
            to=user.get("email",""),
            subject="💸 Withdrawal Received — XSM Market",
            body=(f"Hi {user.get('username')},\n\n"
                  f"Your withdrawal of ${amount} via {method} has been received.\n"
                  f"Processing within 24–48 hours.\n\n— XSM Market"),
        )
        flash(f"Withdrawal of ${amount} submitted!", "success")
        return redirect(url_for("misc.dashboard"))

    return render_template("withdraw.html", credit=credit, user=user)
