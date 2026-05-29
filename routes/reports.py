"""routes/reports.py — listing report / flag system."""
from datetime import datetime as dt
from flask import (Blueprint, render_template, request,
                   redirect, url_for, session, flash, abort)

from extensions import limiter
from utils.auth  import login_required, admin_required
from utils.db    import (safe_db_reference, is_valid_id, get_all_listings,
                          invalidate_listings_cache)
from utils.notifications import push_notification, write_audit, send_email
from utils.config import REPORT_REASONS, AUTO_FLAG_THRESHOLD, GMAIL_USER
from utils.db import get_admin_uid

reports_bp = Blueprint("reports", __name__)


# ── User: report a listing ────────────────────────────────────────────────────

@reports_bp.route("/report/<listing_id>", methods=["GET", "POST"])
@login_required
@limiter.limit("5 per hour")
def report_listing(listing_id):
    if not is_valid_id(listing_id): abort(404)
    listing = safe_db_reference("listings", listing_id).get()
    if not listing: abort(404)

    reporter_uid = session["user"]["uid"]
    if listing.get("seller_uid") == reporter_uid:
        flash("You cannot report your own listing.", "warning")
        return redirect(url_for("listings.listing_detail", listing_id=listing_id))

    existing = safe_db_reference(f"reports/{listing_id}").get() or {}
    if any(r.get("reporter_uid") == reporter_uid
           for r in existing.values() if isinstance(r, dict)):
        flash("You have already reported this listing.", "info")
        return redirect(url_for("listings.listing_detail", listing_id=listing_id))

    if request.method == "POST":
        reason  = request.form.get("reason","").strip()
        details = request.form.get("details","").strip()

        if not reason or reason not in REPORT_REASONS:
            flash("Please select a valid reason.", "danger")
            return render_template("report_listing.html",
                                   listing=listing, listing_id=listing_id, reasons=REPORT_REASONS)

        safe_db_reference(f"reports/{listing_id}").push({
            "reporter_uid":      reporter_uid,
            "reporter_username": session["user"].get("username",""),
            "reason":            reason,
            "details":           details,
            "status":            "pending",
            "created_at":        dt.utcnow().isoformat(),
        })

        all_reports  = safe_db_reference(f"reports/{listing_id}").get() or {}
        report_count = len(all_reports)
        listing_ref  = safe_db_reference("listings", listing_id)
        listing_ref.update({"report_count": report_count})

        if report_count >= AUTO_FLAG_THRESHOLD and not listing.get("flagged"):
            listing_ref.update({"flagged": True, "flagged_at": dt.utcnow().isoformat()})
            push_notification(get_admin_uid(),
                f"⚠️ @{listing.get('username', listing_id)} auto-flagged ({report_count} reports).",
                "warning", "/admin/reports")
            send_email(to=GMAIL_USER,
                subject=f"🚩 Auto-flagged: @{listing.get('username', listing_id)}",
                body=(f"Listing @{listing.get('username','')} has {report_count} reports.\n\n"
                      f"Review: https://xsmmarket.com/admin/reports"))

        invalidate_listings_cache()
        flash("Report submitted. Our team will review it.", "success")
        return redirect(url_for("listings.listing_detail", listing_id=listing_id))

    return render_template("report_listing.html",
                           listing=listing, listing_id=listing_id, reasons=REPORT_REASONS)


# ── Admin: view all reports ───────────────────────────────────────────────────

@reports_bp.route("/admin/reports")
@admin_required
def admin_reports():
    status_filter   = request.args.get("status","")
    all_listings    = safe_db_reference("listings").get() or {}
    all_reports_raw = safe_db_reference("reports").get() or {}

    report_groups = []
    for listing_id, reports_dict in all_reports_raw.items():
        if not isinstance(reports_dict, dict): continue
        reports_list = [{"id": rid, **rdata}
                        for rid, rdata in reports_dict.items()
                        if isinstance(rdata, dict)]
        if not reports_list: continue

        listing = all_listings.get(listing_id, {})
        if status_filter == "flagged":
            if not listing.get("flagged"): continue
        elif status_filter:
            reports_list = [r for r in reports_list if r.get("status") == status_filter]
            if not reports_list: continue

        reports_list.sort(key=lambda r: r.get("created_at",""), reverse=True)
        report_groups.append({
            "listing_id":    listing_id, "listing": listing,
            "reports":       reports_list, "report_count": len(reports_list),
            "flagged":       listing.get("flagged", False),
            "latest_reason": reports_list[0].get("reason","") if reports_list else "",
        })

    report_groups.sort(key=lambda g: (not g["flagged"], -g["report_count"]))

    total_flagged = sum(1 for l in all_listings.values() if l.get("flagged"))
    total_pending = sum(
        1 for reports in all_reports_raw.values() if isinstance(reports, dict)
        for r in reports.values()
        if isinstance(r, dict) and r.get("status") == "pending"
    )

    return render_template("admin_reports.html",
                           report_groups=report_groups, status_filter=status_filter,
                           total_flagged=total_flagged, total_pending=total_pending)


@reports_bp.route("/admin/reports/dismiss/<listing_id>/<report_id>", methods=["POST"])
@admin_required
def admin_dismiss_report(listing_id, report_id):
    safe_db_reference(f"reports/{listing_id}/{report_id}").update({
        "status": "dismissed", "reviewed_by": session["user"]["username"],
        "reviewed_at": dt.utcnow().isoformat(),
    })
    write_audit("dismiss_report", session["user"]["uid"], session["user"]["username"],
                listing_id, f"Dismissed {report_id}")
    flash("Report dismissed.", "info")
    return redirect(url_for("reports.admin_reports"))


@reports_bp.route("/admin/reports/reviewed/<listing_id>", methods=["POST"])
@admin_required
def admin_mark_reports_reviewed(listing_id):
    for rid in (safe_db_reference(f"reports/{listing_id}").get() or {}):
        safe_db_reference(f"reports/{listing_id}/{rid}").update({
            "status": "reviewed", "reviewed_by": session["user"]["username"],
            "reviewed_at": dt.utcnow().isoformat(),
        })
    safe_db_reference("listings", listing_id).update({
        "flagged": False, "flag_cleared_at": dt.utcnow().isoformat(),
        "flag_cleared_by": session["user"]["username"],
    })
    invalidate_listings_cache()
    write_audit("clear_listing_flag", session["user"]["uid"], session["user"]["username"],
                listing_id, "All reports reviewed, flag cleared")
    flash("Reports reviewed and listing unflagged.", "success")
    return redirect(url_for("reports.admin_reports"))


@reports_bp.route("/admin/reports/remove_listing/<listing_id>", methods=["POST"])
@admin_required
def admin_remove_reported_listing(listing_id):
    listing    = safe_db_reference("listings", listing_id).get() or {}
    seller_uid = listing.get("seller_uid","")
    safe_db_reference("listings", listing_id).delete()
    safe_db_reference(f"reports/{listing_id}").update({"_removed": True})
    invalidate_listings_cache()
    write_audit("remove_reported_listing", session["user"]["uid"], session["user"]["username"],
                listing_id, f"Removed @{listing.get('username', listing_id)}")
    if seller_uid:
        push_notification(seller_uid,
            f"Your listing @{listing.get('username','')} was removed for policy violations.",
            "danger", "/dashboard")
    flash("Listing removed.", "success")
    return redirect(url_for("reports.admin_reports"))
