"""routes/admin.py — all /admin/* routes."""
import uuid, os, statistics, datetime
from datetime import datetime as dt
from flask import (Blueprint, render_template, request,
                   redirect, url_for, session, flash)
from firebase_admin import db

from utils.auth  import admin_required, allowed_file
from utils.db    import (safe_db_reference, get_all_listings, get_all_users,
                          invalidate_listings_cache, invalidate_users_cache,
                          calculate_fee, record_platform_revenue,
                          compute_avg_rating_from_reviews, star_breakdown,
                          resolve_media_url, is_valid_id, is_listing_boosted)
from utils.notifications import push_notification, write_audit, send_email
from utils.config import GMAIL_USER, PLATFORM_FEE_PCT

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


# ── Dashboard ─────────────────────────────────────────────────────────────────

@admin_bp.route("")
@admin_required
def admin_dashboard():
    users       = safe_db_reference("users").get()       or {}
    listings    = safe_db_reference("listings").get()    or {}
    payments    = safe_db_reference("payments").get()    or {}
    withdrawals = safe_db_reference("withdrawals").get() or {}
    revenue     = safe_db_reference("revenue").get()     or {}

    return render_template("admin_dashboard.html",
        users=users, listings=listings, payments=payments, withdrawals=withdrawals,
        dispute_count=sum(1 for l in listings.values() if l.get("status") == "disputed"),
        pending_confirmation_count=sum(1 for l in listings.values() if l.get("status") == "pending_confirmation"),
        resolved_disputes={lid: l for lid, l in listings.items()
                           if l.get("status") in ("completed","refunded") and l.get("dispute")},
        approved_count=sum(1 for p in payments.values() if p.get("status") == "approved"),
        rejected_count=sum(1 for p in payments.values() if p.get("status") == "rejected"),
        pending_count =sum(1 for p in payments.values() if p.get("status") == "pending"),
        platform_revenue=int(revenue.get("total", 0)),
    )


# ── Users ─────────────────────────────────────────────────────────────────────

@admin_bp.route("/users")
@admin_required
def admin_users():
    return render_template("admin_users.html", users=safe_db_reference("users").get() or {})


@admin_bp.route("/users/<uid>")
@admin_required
def admin_user_profile(uid):
    if not is_valid_id(uid):
        flash("Invalid user ID.", "danger")
        return redirect(url_for("admin.admin_users"))
    user_data = safe_db_reference("users", uid).get()
    if not user_data:
        flash("User not found.", "danger")
        return redirect(url_for("admin.admin_users"))
    all_listings  = safe_db_reference("listings").get() or {}
    user_listings = {lid: l for lid, l in all_listings.items() if l.get("seller_uid") == uid}
    return render_template("admin_user_profile.html", user=user_data, listings=user_listings, uid=uid)


@admin_bp.route("/verify_seller/<uid>", methods=["POST"])
@admin_required
def admin_verify_seller(uid):
    if not is_valid_id(uid): return redirect(url_for("admin.admin_users"))
    user_ref  = safe_db_reference("users", uid)
    user_data = user_ref.get() or {}
    new_state = not bool(user_data.get("verified", False))
    user_ref.update({"verified": new_state})
    invalidate_users_cache()
    write_audit("toggle_seller_badge", session["user"]["uid"], session["user"]["username"],
                uid, f"Badge set to {new_state}")
    word = "granted" if new_state else "revoked"
    push_notification(uid, f"Seller badge {word}.", "success" if new_state else "warning")
    flash(f"Seller badge {word}.", "success")
    return redirect(url_for("admin.admin_user_profile", uid=uid))


@admin_bp.route("/kyc_verify/<uid>", methods=["POST"])
@admin_required
def admin_kyc_verify(uid):
    if not is_valid_id(uid): return redirect(url_for("admin.admin_users"))
    user_ref  = safe_db_reference("users", uid)
    user_data = user_ref.get() or {}
    new_state = not bool(user_data.get("kyc_verified", False))
    user_ref.update({"kyc_verified": new_state})
    invalidate_users_cache()
    write_audit("toggle_kyc", session["user"]["uid"], session["user"]["username"],
                uid, f"KYC set to {new_state}")
    if new_state:
        push_notification(uid, "KYC verified — you can now withdraw.", "success", "/wallet")
    flash(f"KYC {'granted' if new_state else 'revoked'}.", "success")
    return redirect(url_for("admin.admin_user_profile", uid=uid))


@admin_bp.route("/delete_user/<uid>")
@admin_required
def admin_delete_user(uid):
    if not is_valid_id(uid): return redirect(url_for("admin.admin_users"))
    user_data = safe_db_reference("users", uid).get() or {}
    safe_db_reference("users", uid).delete()
    for lid, item in (safe_db_reference("listings").get() or {}).items():
        if item.get("seller_uid") == uid:
            safe_db_reference("listings", lid).delete()
    write_audit("delete_user", session["user"]["uid"], session["user"]["username"],
                uid, f"Deleted {user_data.get('username', uid)}")
    invalidate_listings_cache(); invalidate_users_cache()
    flash("User and listings deleted.", "success")
    return redirect(url_for("admin.admin_users"))


@admin_bp.route("/delete_listing/<lid>")
@admin_required
def admin_delete_listing(lid):
    if not is_valid_id(lid):
        return redirect(request.referrer or url_for("admin.admin_users"))
    safe_db_reference("listings", lid).delete()
    invalidate_listings_cache()
    write_audit("delete_listing", session["user"]["uid"], session["user"]["username"], lid)
    flash("Listing deleted.", "success")
    return redirect(request.referrer or url_for("admin.admin_users"))


# ── Payments ──────────────────────────────────────────────────────────────────

@admin_bp.route("/payments")
@admin_required
def admin_payments():
    payments = safe_db_reference("payments").get() or {}
    return render_template("admin_payments.html", payments=payments, user=session["user"])


@admin_bp.route("/approve_payment/<payment_id>", methods=["POST"])
@admin_required
def approve_payment(payment_id):
    amt_str = request.form.get("approved_amount","").strip()
    if not amt_str.isdigit():
        flash("Invalid amount.", "danger")
        return redirect(url_for("admin.admin_payments"))

    approved = int(amt_str)
    payment_ref = safe_db_reference("payments", payment_id)
    payment     = payment_ref.get() or {}
    user_id     = payment.get("user_id")
    if not user_id:
        flash("Payment not found.", "danger")
        return redirect(url_for("admin.admin_payments"))

    payment_ref.update({"status": "approved", "approved_amount": approved,
                         "approved_at": dt.utcnow().isoformat(),
                         "approved_by": session["user"]["username"]})
    user_ref  = safe_db_reference("users", user_id)
    user_data = user_ref.get() or {}
    user_ref.update({"credit": int(user_data.get("credit",0)) + approved})
    invalidate_users_cache()
    write_audit("approve_payment", session["user"]["uid"], session["user"]["username"],
                payment_id, f"Approved {approved} credits")
    push_notification(user_id, f"Deposit of {approved} credits approved!", "success", "/wallet")
    flash(f"Payment approved. {approved} credits added.", "success")
    return redirect(url_for("admin.admin_payments"))


@admin_bp.route("/reject_payment/<payment_id>", methods=["POST"])
@admin_required
def reject_payment(payment_id):
    payment_ref = safe_db_reference("payments", payment_id)
    payment     = payment_ref.get() or {}
    if not payment:
        flash("Payment not found.", "danger")
    else:
        payment_ref.update({"status": "rejected", "rejected_at": dt.utcnow().isoformat(),
                             "rejected_by": session["user"]["username"]})
        write_audit("reject_payment", session["user"]["uid"], session["user"]["username"], payment_id)
        push_notification(payment.get("user_id",""),
            "Deposit request rejected. Contact support if this is an error.",
            "danger", "/wallet")
        flash("Payment rejected.", "info")
    return redirect(url_for("admin.admin_payments"))


# ── Withdrawals ───────────────────────────────────────────────────────────────

@admin_bp.route("/withdrawals")
@admin_required
def admin_withdrawals():
    withdrawals = safe_db_reference("withdrawals").get() or {}
    sorted_w = dict(sorted(withdrawals.items(),
                            key=lambda x: x[1].get("created_at",""), reverse=True))
    return render_template("admin_withdrawals.html", withdrawals=sorted_w)


@admin_bp.route("/approve_withdrawal/<wid>", methods=["POST"])
@admin_required
def approve_withdrawal(wid):
    ref = safe_db_reference("withdrawals", wid)
    w   = ref.get() or {}
    if not w: flash("Not found.", "danger"); return redirect(url_for("admin.admin_withdrawals"))
    if w.get("status") != "pending": flash("Already processed.", "warning"); return redirect(url_for("admin.admin_withdrawals"))

    ref.update({"status": "approved", "approved_at": dt.utcnow().isoformat(),
                "approved_by": session["user"]["username"]})
    write_audit("approve_withdrawal", session["user"]["uid"], session["user"]["username"],
                wid, f"Approved ${w.get('amount')} for {w.get('username')}")
    push_notification(w.get("user_id",""), f"Withdrawal of ${w.get('amount')} approved.", "success", "/wallet")
    send_email(to=w.get("email",""), subject="✅ Withdrawal Approved — XSM Market",
               body=f"Hi {w.get('username')},\n\nYour ${w.get('amount')} withdrawal was approved.\n\n— XSM Market")
    flash(f"Withdrawal of ${w.get('amount')} approved.", "success")
    return redirect(url_for("admin.admin_withdrawals"))


@admin_bp.route("/reject_withdrawal/<wid>", methods=["POST"])
@admin_required
def reject_withdrawal(wid):
    ref = safe_db_reference("withdrawals", wid)
    w   = ref.get() or {}
    if not w: flash("Not found.", "danger"); return redirect(url_for("admin.admin_withdrawals"))
    if w.get("status") != "pending": flash("Already processed.", "warning"); return redirect(url_for("admin.admin_withdrawals"))

    user_id = w.get("user_id")
    amount  = int(w.get("amount",0))
    if user_id and amount:
        ud = safe_db_reference("users", user_id).get() or {}
        safe_db_reference("users", user_id).update({"credit": int(ud.get("credit",0)) + amount})
        invalidate_users_cache()
    ref.update({"status": "rejected", "rejected_at": dt.utcnow().isoformat(),
                "rejected_by": session["user"]["username"]})
    write_audit("reject_withdrawal", session["user"]["uid"], session["user"]["username"],
                wid, f"Rejected ${amount}")
    push_notification(user_id, f"Withdrawal of ${amount} rejected — refunded.", "danger", "/wallet")
    send_email(to=w.get("email",""), subject="❌ Withdrawal Rejected — XSM Market",
               body=f"Hi {w.get('username')},\n\nYour ${amount} withdrawal was rejected and refunded.\n\n— XSM Market")
    flash(f"Withdrawal rejected. ${amount} refunded.", "info")
    return redirect(url_for("admin.admin_withdrawals"))


# ── Disputes ──────────────────────────────────────────────────────────────────

@admin_bp.route("/disputes")
@admin_required
def admin_disputes():
    all_listings = safe_db_reference("listings").get() or {}
    disputes = {lid: l for lid, l in all_listings.items() if l.get("status") == "disputed"}
    return render_template("admin_disputes.html", disputes=disputes)


@admin_bp.route("/auto_resolve", methods=["POST"])
@admin_required
def admin_auto_resolve():
    resolved = 0
    for lid, listing in (safe_db_reference("listings").get() or {}).items():
        if listing.get("status") != "pending_confirmation": continue
        delivered_at = listing.get("delivery",{}).get("delivered_at")
        if not delivered_at: continue
        try:
            elapsed = (dt.utcnow() - dt.fromisoformat(delivered_at)).total_seconds() / 3600
        except Exception: continue
        if elapsed < 72: continue

        amount = int(listing.get("escrow",{}).get("amount",0))
        seller_uid = listing.get("seller_uid")
        buyer_uid  = listing.get("buyer_uid")
        if not seller_uid or not amount: continue

        try:
            fee, seller_gets = calculate_fee(amount)
            sd = safe_db_reference("users", seller_uid).get() or {}
            db.reference("/").update({
                f"/users/{seller_uid}/credit":                 int(sd.get("credit",0)) + seller_gets,
                f"/listings/{lid}/status":                     "completed",
                f"/listings/{lid}/escrow/state":               "auto_released",
                f"/listings/{lid}/escrow/released_at":         dt.utcnow().isoformat(),
                f"/listings/{lid}/escrow/fee":                 fee,
                f"/listings/{lid}/escrow/seller_gets":         seller_gets,
                f"/purchases/{buyer_uid}/{lid}/status":        "completed",
            })
            record_platform_revenue(fee, lid)
            resolved += 1
        except Exception: continue

    write_audit("auto_resolve", session["user"]["uid"], session["user"]["username"],
                detail=f"Auto-resolved {resolved} listings")
    flash(f"Auto-resolve complete. {resolved} listing(s) released.", "success")
    return redirect(url_for("admin.admin_disputes"))


@admin_bp.route("/resolve_dispute/<listing_id>", methods=["POST"])
@admin_required
def resolve_dispute(listing_id):
    decision    = request.form.get("decision")
    listing_ref = safe_db_reference("listings", listing_id)
    listing     = listing_ref.get()

    if not listing or listing.get("status") != "disputed":
        flash("Not in disputed state.", "danger")
        return redirect(url_for("admin.admin_disputes"))

    amount     = int(listing.get("escrow",{}).get("amount",0))
    buyer_uid  = listing.get("buyer_uid")
    seller_uid = listing.get("seller_uid")

    if decision == "refund":
        bd = safe_db_reference("users", buyer_uid).get() or {}
        updates = {
            f"/users/{buyer_uid}/credit":                   int(bd.get("credit",0)) + amount,
            f"/listings/{listing_id}/status":               "refunded",
            f"/listings/{listing_id}/escrow/state":         "refunded",
            f"/listings/{listing_id}/escrow/resolved_at":   dt.utcnow().isoformat(),
            f"/listings/{listing_id}/dispute/resolved":     True,
            f"/listings/{listing_id}/dispute/resolved_by":  session["user"]["username"],
            f"/purchases/{buyer_uid}/{listing_id}/status":  "refunded",
        }
        msg = f"Refunded {amount} credits to buyer."
        push_notification(buyer_uid,  "Dispute resolved — refund issued.", "success", "/wallet")
        push_notification(seller_uid, "Dispute resolved in buyer's favour.", "warning", "/dashboard")

    elif decision == "release":
        fee, seller_gets = calculate_fee(amount)
        sd = safe_db_reference("users", seller_uid).get() or {}
        updates = {
            f"/users/{seller_uid}/credit":                  int(sd.get("credit",0)) + seller_gets,
            f"/listings/{listing_id}/status":               "completed",
            f"/listings/{listing_id}/escrow/state":         "released",
            f"/listings/{listing_id}/escrow/resolved_at":   dt.utcnow().isoformat(),
            f"/listings/{listing_id}/escrow/fee":           fee,
            f"/listings/{listing_id}/escrow/seller_gets":   seller_gets,
            f"/listings/{listing_id}/dispute/resolved":     True,
            f"/listings/{listing_id}/dispute/resolved_by":  session["user"]["username"],
            f"/purchases/{buyer_uid}/{listing_id}/status":  "completed",
        }
        msg = f"Released {seller_gets} credits to seller (fee: {fee})."
        push_notification(seller_uid, f"Dispute resolved — {seller_gets} credits added.", "success", "/wallet")
        push_notification(buyer_uid,  "Dispute resolved in seller's favour.", "warning", "/dashboard")
    else:
        flash("Invalid decision.", "danger")
        return redirect(url_for("admin.admin_disputes"))

    db.reference("/").update(updates)
    if decision == "release": record_platform_revenue(fee, listing_id)
    invalidate_listings_cache(); invalidate_users_cache()
    write_audit(f"resolve_dispute_{decision}", session["user"]["uid"],
                session["user"]["username"], listing_id, msg)
    flash(msg, "success")
    return redirect(url_for("admin.admin_disputes"))


# ── KYC ───────────────────────────────────────────────────────────────────────

@admin_bp.route("/kyc")
@admin_required
def admin_kyc_list():
    status_filter = request.args.get("status","")
    all_apps = safe_db_reference("kyc_applications").get() or {}
    apps = {k: v for k, v in all_apps.items()
            if not status_filter or v.get("status") == status_filter}
    apps = dict(sorted(apps.items(), key=lambda x: x[1].get("submitted_at",""), reverse=True))
    pending = sum(1 for v in all_apps.values() if v.get("status") == "pending")
    return render_template("admin_kyc.html", applications=apps,
                           status_filter=status_filter, pending_count=pending)


@admin_bp.route("/kyc/approve/<kid>", methods=["POST"])
@admin_required
def admin_kyc_approve(kid):
    kyc_ref = safe_db_reference("kyc_applications", kid)
    kyc     = kyc_ref.get() or {}
    if not kyc: flash("Not found.", "danger"); return redirect(url_for("admin.admin_kyc_list"))
    user_id = kyc.get("user_id")
    kyc_ref.update({"status": "approved", "approved_at": dt.utcnow().isoformat(),
                    "approved_by": session["user"]["username"]})
    safe_db_reference("users", user_id).update({"kyc_verified": True})
    invalidate_users_cache()
    write_audit("kyc_approve", session["user"]["uid"], session["user"]["username"],
                user_id, f"KYC approved for {kyc.get('username')}")
    push_notification(user_id, "✅ KYC approved — you can now withdraw.", "success", "/wallet/withdraw")
    send_email(to=kyc.get("email",""), subject="✅ KYC Approved — XSM Market",
               body=f"Hi {kyc.get('username')},\n\nKYC approved. You can now withdraw.\n\n— XSM Market")
    flash(f"KYC approved for {kyc.get('username')}.", "success")
    return redirect(url_for("admin.admin_kyc_list"))


@admin_bp.route("/kyc/reject/<kid>", methods=["POST"])
@admin_required
def admin_kyc_reject(kid):
    reason  = request.form.get("reason","").strip()
    kyc_ref = safe_db_reference("kyc_applications", kid)
    kyc     = kyc_ref.get() or {}
    if not kyc: flash("Not found.", "danger"); return redirect(url_for("admin.admin_kyc_list"))
    user_id = kyc.get("user_id")
    kyc_ref.update({"status": "rejected", "rejected_at": dt.utcnow().isoformat(),
                    "rejected_by": session["user"]["username"], "rejection_reason": reason})
    write_audit("kyc_reject", session["user"]["uid"], session["user"]["username"],
                user_id, f"KYC rejected: {reason}")
    push_notification(user_id, f"❌ KYC rejected: {reason}. Please resubmit.", "danger", "/kyc")
    send_email(to=kyc.get("email",""), subject="❌ KYC Rejected — XSM Market",
               body=f"Hi {kyc.get('username')},\n\nKYC rejected.\nReason: {reason}\n\nResubmit: https://xsmmarket.com/kyc\n\n— XSM Market")
    flash(f"KYC rejected for {kyc.get('username')}.", "info")
    return redirect(url_for("admin.admin_kyc_list"))


# ── Audit log & Revenue ────────────────────────────────────────────────────────

@admin_bp.route("/audit")
@admin_required
def admin_audit_log():
    raw  = safe_db_reference("audit_log").get() or {}
    logs = sorted(raw.values(), key=lambda x: x.get("timestamp",""), reverse=True)
    return render_template("admin_audit.html", logs=logs)


@admin_bp.route("/revenue")
@admin_required
def admin_revenue():
    revenue = safe_db_reference("revenue").get() or {}
    txns    = revenue.pop("transactions", {}) or {}
    txn_list = sorted(txns.values(), key=lambda x: x.get("timestamp",""), reverse=True)
    return render_template("admin_revenue.html", revenue=revenue, transactions=txn_list)
