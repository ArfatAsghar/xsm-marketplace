"""routes/misc.py — home, dashboard, seller profile, ratings, KYC, contact, static pages."""
import uuid, os, statistics
from datetime import datetime as dt
from flask import (Blueprint, Response, render_template, request,
                   redirect, url_for, session, flash, abort)
from xml.sax.saxutils import escape
from werkzeug.utils import secure_filename

from extensions import limiter
from utils.auth  import login_required, allowed_file
from utils.db    import (safe_db_reference, get_all_listings, get_user_public,
                          is_valid_id, is_listing_boosted, resolve_media_url,
                          compute_avg_rating_from_reviews, star_breakdown,
                          invalidate_listings_cache, invalidate_users_cache)
from utils.notifications import push_notification, send_email
from utils.config import GMAIL_USER, GMAIL_APP_PASSWORD

misc_bp = Blueprint("misc", __name__)

DASHBOARD_PER_PAGE = 12
GOOGLE_VERIFICATION_HTML = "google-site-verification: google52150855c235b99a.html"


def _public_pages() -> list[str]:
    pages = [
        url_for("misc.home", _external=True),
        url_for("listings.marketplace", _external=True),
        url_for("misc.contact", _external=True),
        url_for("misc.terms", _external=True),
        url_for("misc.privacy_policy", _external=True),
    ]

    try:
        listings = safe_db_reference("listings").get() or {}
        for listing_id, listing in listings.items():
            if isinstance(listing, dict) and listing.get("status", "available") != "deleted":
                pages.append(url_for("listings.listing_detail", listing_id=listing_id, _external=True))

        seller_ids = {
            listing.get("seller_uid")
            for listing in listings.values()
            if isinstance(listing, dict) and listing.get("seller_uid")
        }
        for seller_id in seller_ids:
            pages.append(url_for("misc.seller_profile", seller_id=seller_id, _external=True))
    except Exception:
        pass

    return sorted(set(pages))


@misc_bp.route("/robots.txt")
def robots_txt():
    sitemap_url = url_for("misc.sitemap_xml", _external=True)
    body = f"""User-agent: *
Allow: /
Disallow: /admin/
Disallow: /dashboard
Disallow: /login
Disallow: /signup
Disallow: /forgot
Disallow: /new_password
Disallow: /verify_reset
Disallow: /wallet
Disallow: /chat/
Disallow: /settings_profile
Disallow: /add_listing
Disallow: /edit_listing
Disallow: /withdraw
Disallow: /report_listing
Sitemap: {sitemap_url}
"""
    return Response(body, mimetype="text/plain")


@misc_bp.route("/sitemap.xml")
def sitemap_xml():
    pages = _public_pages()
    items = "\n".join(
        f"  <url><loc>{escape(page)}</loc></url>" for page in pages
    )
    body = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
{items}
</urlset>
"""
    return Response(body, mimetype="application/xml")


@misc_bp.route("/google52150855c235b99a.html")
def google_site_verification_file():
    return Response(GOOGLE_VERIFICATION_HTML, mimetype="text/html")


# ── Home ──────────────────────────────────────────────────────────────────────

@misc_bp.route("/")
def home():
    return render_template("index.html")


# ── Dashboard ─────────────────────────────────────────────────────────────────

@misc_bp.route("/dashboard")
@login_required
def dashboard():
    user    = session["user"]
    user_id = user["uid"]
    page    = request.args.get("page", 1, type=int)

    user_data = safe_db_reference("users", user_id).get() or {}
    credit    = int(user_data.get("credit", 0))

    all_listings = get_all_listings()
    listings     = {lid: l for lid, l in all_listings.items()
                    if l.get("seller_uid") == user_id}

    for lid, item in listings.items():
        item["price"]      = int(float(item.get("price", 0) or 0))
        item["followers"]  = int(item.get("followers", 0) or 0)
        item["thumbnail"]  = resolve_media_url(item.get("thumbnail"))
        item["view_count"] = int(item.get("view_count", 0) or 0)
        item["is_boosted"] = is_listing_boosted(item)
        if item.get("buyer_uid"):
            try:
                bd = safe_db_reference("users", item["buyer_uid"]).get() or {}
                item["buyer_username"] = bd.get("username", "Unknown")
            except Exception:
                item["buyer_username"] = "Unknown"

    # Purchases made BY this user as buyer
    try:
        purchases = safe_db_reference("purchases", user_id).get() or {}
        for listing_id, _ in purchases.items():
            if listing_id in listings: continue
            try:
                pdata = safe_db_reference("listings", listing_id).get() or {}
                if pdata:
                    pdata["_is_purchase"] = True
                    pdata["thumbnail"]    = resolve_media_url(pdata.get("thumbnail"))
                    pdata["price"]        = int(float(pdata.get("price", 0) or 0))
                    pdata["followers"]    = int(pdata.get("followers", 0) or 0)
                    listings[listing_id]  = pdata
            except Exception:
                pass
    except Exception:
        pass

    all_items   = list(listings.items())
    total       = len(all_items)
    total_pages = max(1, (total + DASHBOARD_PER_PAGE - 1) // DASHBOARD_PER_PAGE)
    page        = max(1, min(page, total_pages))
    page_listings = dict(all_items[(page-1)*DASHBOARD_PER_PAGE : page*DASHBOARD_PER_PAGE])

    session["user"]["credit"] = credit
    return render_template("dashboard.html",
                           listings=page_listings, credit=credit, user=user,
                           page=page, total_pages=total_pages, total=total)


# ── Seller profile & ratings ──────────────────────────────────────────────────

@misc_bp.route("/seller/<seller_id>")
def seller_profile(seller_id):
    if not is_valid_id(seller_id): abort(404)
    seller = safe_db_reference("users", seller_id).get()
    if not seller: abort(404)
    seller["uid"] = seller_id

    all_listings    = safe_db_reference("listings").get() or {}
    seller_listings = {k: v for k, v in all_listings.items()
                       if v.get("seller_uid") == seller_id}

    total_views = sum(int(l.get("view_count", 0)) for l in seller_listings.values())
    sales_count = sum(1 for l in seller_listings.values() if l.get("status") == "completed")

    try:
        ratings_node = safe_db_reference("ratings", seller_id).get() or {}
        stars = [int(v.get("stars",0)) for v in ratings_node.values()
                 if str(v.get("stars","")).isdigit()]
        avg_rating = round(statistics.mean(stars), 1) if stars else compute_avg_rating_from_reviews(seller_id)
    except Exception:
        avg_rating = 0.0

    full, half, empty = star_breakdown(avg_rating)
    reviews = safe_db_reference("reviews", seller_id).get() or {}

    return render_template("seller_profile.html",
                           seller=seller, username=seller.get("username","Unknown"),
                           listings=seller_listings, reviews=reviews,
                           avg_rating=avg_rating, stars_full=full,
                           stars_half=half, stars_empty=empty,
                           total_views=total_views, sales_count=sales_count)


@misc_bp.route("/seller/<seller_id>/review", methods=["POST"])
@login_required
def add_review(seller_id):
    rating      = int(request.form.get("rating", 0))
    review_text = request.form.get("review","").strip()
    reviewer    = session["user"]

    if not 1 <= rating <= 5:
        flash("Invalid rating.", "danger")
        return redirect(url_for("misc.seller_profile", seller_id=seller_id))

    try:
        reviews_ref = safe_db_reference("reviews", seller_id)
        reviews_ref.push({
            "reviewer_uid":  reviewer["uid"],
            "reviewer_name": reviewer.get("username","Anonymous"),
            "rating":        rating,
            "review":        review_text,
            "timestamp":     dt.now().isoformat(),
        })
        all_r   = reviews_ref.get() or {}
        ratings = [int(r.get("rating",0)) for r in all_r.values()
                   if str(r.get("rating","")).isdigit()]
        avg = round(statistics.mean(ratings), 1) if ratings else 0
        safe_db_reference("users", seller_id).update({"average_rating": avg})
        invalidate_users_cache()
        push_notification(seller_id,
            f"{reviewer.get('username','Someone')} left you a {rating}-star review.",
            "info", f"/seller/{seller_id}")
    except Exception:
        pass

    flash("Review submitted!", "success")
    return redirect(url_for("misc.seller_profile", seller_id=seller_id))


@misc_bp.route("/rate_seller/<seller_id>", methods=["POST"])
@login_required
def rate_seller(seller_id):
    buyer_uid = session["user"]["uid"]
    purchases = safe_db_reference("purchases", buyer_uid).get() or {}
    if not any(p.get("seller_id") == seller_id for p in purchases.values()):
        flash("You can only rate sellers you purchased from.", "danger")
        return redirect(url_for("misc.seller_profile", seller_id=seller_id))

    try:
        rating = int(request.form.get("rating", 0))
    except ValueError:
        rating = 0

    if not 1 <= rating <= 5:
        flash("Invalid rating.", "danger")
        return redirect(url_for("misc.seller_profile", seller_id=seller_id))

    review      = request.form.get("review","").strip()
    ratings_ref = safe_db_reference("ratings", seller_id)
    ratings_ref.child(buyer_uid).set({
        "stars": rating, "review": review,
        "timestamp": dt.utcnow().isoformat(),
    })
    all_r  = ratings_ref.get() or {}
    stars  = [int(r.get("stars",0)) for r in all_r.values()
              if str(r.get("stars","")).isdigit()]
    avg    = round(sum(stars) / len(stars), 1) if stars else 0
    safe_db_reference("users", seller_id).update({"average_rating": avg})
    invalidate_users_cache()
    flash("Thanks for your feedback!", "success")
    return redirect(url_for("misc.seller_profile", seller_id=seller_id))


# ── KYC submission ────────────────────────────────────────────────────────────

@misc_bp.route("/kyc", methods=["GET", "POST"])
@login_required
def kyc_submit():
    user_id  = session["user"]["uid"]
    user_ref = safe_db_reference("users", user_id)

    existing = safe_db_reference("kyc_applications") \
                   .order_by_child("user_id").equal_to(user_id).get() or {}
    kyc_app  = next(iter(existing.values()), {})

    kyc_status           = kyc_app.get("status")
    kyc_submitted_at     = kyc_app.get("submitted_at","")
    kyc_rejection_reason = kyc_app.get("rejection_reason","")

    if kyc_status == "approved":
        user_ref.update({"kyc_verified": True})

    if request.method == "POST":
        full_name = request.form.get("full_name","").strip()
        cnic      = request.form.get("cnic","").strip()
        phone     = request.form.get("phone","").strip()
        address   = request.form.get("address","").strip()

        if not all([full_name, cnic, phone, address]):
            flash("All fields are required.", "danger")
            return render_template("kyc_submit.html",
                                   kyc_status=kyc_status,
                                   kyc_submitted_at=kyc_submitted_at,
                                   kyc_rejection_reason=kyc_rejection_reason)

        def _save_doc(field):
            f = request.files.get(field)
            if f and f.filename and allowed_file(f.filename):
                fname = f"{uuid.uuid4().hex}_{secure_filename(f.filename)}"
                try:
                    from utils.storage import upload_to_supabase
                    uploaded_url = upload_to_supabase(f, "kyc_docs", fname)
                    if uploaded_url:
                        return uploaded_url
                except Exception as upload_err:
                    print(f"[KYC DOC UPLOAD ERROR] {upload_err}")
            return None

        cnic_front_url = _save_doc("cnic_front")
        cnic_back_url  = _save_doc("cnic_back")
        selfie_url     = _save_doc("selfie")

        if not all([cnic_front_url, cnic_back_url, selfie_url]):
            flash("Please upload all three documents.", "danger")
            return render_template("kyc_submit.html",
                                   kyc_status=kyc_status,
                                   kyc_submitted_at=kyc_submitted_at,
                                   kyc_rejection_reason=kyc_rejection_reason)

        safe_db_reference("kyc_applications").push({
            "user_id":        user_id,
            "username":       session["user"].get("username",""),
            "email":          session["user"].get("email",""),
            "full_name":      full_name,
            "cnic":           cnic,
            "phone":          phone,
            "address":        address,
            "cnic_front_url": cnic_front_url,
            "cnic_back_url":  cnic_back_url,
            "selfie_url":     selfie_url,
            "status":         "pending",
            "submitted_at":   dt.utcnow().isoformat(),
        })
        send_email(
            to=GMAIL_USER,
            subject=f"🪪 New KYC — {session['user'].get('username')}",
            body=(f"User: {session['user'].get('username')} ({session['user'].get('email')})\n"
                  f"CNIC: {cnic}\nPhone: {phone}\nAddress: {address}\n\n"
                  f"Review: https://xsmmarket.com/admin/kyc"),
        )
        flash("KYC submitted! We'll review within 24–48 hours.", "success")
        return redirect(url_for("misc.kyc_submit"))

    return render_template("kyc_submit.html",
                           kyc_status=kyc_status,
                           kyc_submitted_at=kyc_submitted_at,
                           kyc_rejection_reason=kyc_rejection_reason)


# ── Public profile ────────────────────────────────────────────────────────────

@misc_bp.route("/profile/<username>")
def profile(username):
    users   = safe_db_reference("users").get() or {}
    user_id = next((uid for uid, d in users.items()
                    if d.get("username") == username), None)
    if not user_id:
        flash("User not found!", "error")
        return redirect(url_for("listings.marketplace"))
    all_listings  = safe_db_reference("listings").get() or {}
    user_listings = {lid: l for lid, l in all_listings.items()
                     if l.get("seller_uid") == user_id}
    return render_template("profile.html", username=username, listings=user_listings)


# ── Contact ───────────────────────────────────────────────────────────────────

@misc_bp.route("/contact", methods=["GET", "POST"])
@limiter.limit("5 per hour")
def contact():
    if request.method == "POST":
        import yagmail
        name    = request.form.get("name","").strip()
        email   = request.form.get("email","").strip()
        message = request.form.get("message","").strip()
        try:
            yagmail.SMTP(GMAIL_USER, GMAIL_APP_PASSWORD).send(
                to=GMAIL_USER,
                subject=f"Contact: {name}",
                contents=f"From: {name} ({email})\n\n{message}",
            )
            flash("Message sent!", "success")
        except Exception as e:
            flash(f"Error: {e}", "danger")
        return redirect(url_for("misc.contact"))
    return render_template("contact.html")


# ── Static pages ──────────────────────────────────────────────────────────────

@misc_bp.route("/privacy-policy")
def privacy_policy():
    return render_template("privacy-policy.html")


@misc_bp.route("/terms")
def terms():
    return render_template("terms.html")
