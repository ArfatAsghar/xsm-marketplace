"""routes/listings.py — marketplace, listing CRUD, boost, escrow/delivery."""
import uuid, os, datetime
from datetime import datetime as dt
from flask import (Blueprint, render_template, request,
                   redirect, url_for, session, flash, abort)
from firebase_admin import db
from werkzeug.utils import secure_filename

from utils.auth  import login_required, allowed_file
from utils.db    import (safe_db_reference, get_all_listings, get_user_public,
                          is_valid_id, is_listing_boosted, compute_avg_rating_from_reviews,
                          star_breakdown, resolve_media_url, calculate_fee,
                          record_platform_revenue, invalidate_listings_cache,
                          invalidate_users_cache)
from utils.notifications import push_notification, notify_seller_new_purchase, \
    notify_buyer_credentials_delivered, notify_seller_confirmed, notify_dispute_raised
from utils.config import BOOST_COST, BOOST_HOURS, GMAIL_USER

listings_bp = Blueprint("listings", __name__)

PER_PAGE = 9


# ── Marketplace ───────────────────────────────────────────────────────────────

@listings_bp.route("/marketplace")
def marketplace():
    selected_platform = request.args.get("platform", "")
    search_query      = request.args.get("search",   "").lower()
    topic             = request.args.get("topic",    "")
    monetized         = request.args.get("monetized","")
    page              = request.args.get("page", 1, type=int)
    subs_min   = request.args.get("subs_min",   type=int)
    subs_max   = request.args.get("subs_max",   type=int)
    price_min  = request.args.get("price_min",  type=int)
    price_max  = request.args.get("price_max",  type=int)
    income_min = request.args.get("income_min", type=int)
    income_max = request.args.get("income_max", type=int)

    boosted, regular = {}, {}

    for lid, item in get_all_listings().items():
        if item.get("status", "available") != "available":
            continue

        seller_uid  = item.get("seller_uid", "")
        seller_info = get_user_public(seller_uid)
        item.update({
            "seller_username": item.get("seller_username", seller_info["username"]),
            "seller_pic":      seller_info["profile_pic"],
            "seller_uid":      seller_uid,
            "seller_verified": seller_info["verified"],
            "thumbnail":       resolve_media_url(item.get("thumbnail")),
            "is_boosted":      is_listing_boosted(item),
        })
        avg = seller_info["average_rating"] or compute_avg_rating_from_reviews(seller_uid)
        item["avg_rating"] = round(avg, 1)
        f, h, e = star_breakdown(item["avg_rating"])
        item.update(stars_full=f, stars_half=h, stars_empty=e)

        # Filters
        if search_query:
            hay = " ".join([item.get(k,"") for k in
                            ("username","platform","description","topic","seller_username")]).lower()
            if search_query not in hay: continue
        if selected_platform and item.get("platform","").lower() != selected_platform.lower(): continue
        if topic and item.get("topic","").lower() != topic.lower(): continue
        if monetized and str(item.get("monetized","")).lower() not in ("1","true","yes","on"): continue
        subs = int(item.get("followers",0) or 0)
        if subs_min is not None and subs < subs_min: continue
        if subs_max is not None and subs > subs_max: continue
        price = int(float(item.get("price",0) or 0))
        if price_min is not None and price < price_min: continue
        if price_max is not None and price > price_max: continue
        income = int(item.get("income",0) or 0)
        if income_min is not None and income < income_min: continue
        if income_max is not None and income > income_max: continue

        (boosted if item["is_boosted"] else regular)[lid] = item

    filtered    = {**boosted, **regular}
    all_items   = list(filtered.items())
    total       = len(all_items)
    total_pages = max(1, (total + PER_PAGE - 1) // PER_PAGE)
    page        = max(1, min(page, total_pages))
    page_items  = dict(all_items[(page-1)*PER_PAGE : page*PER_PAGE])

    return render_template("marketplace.html",
                           listings=page_items, selected_platform=selected_platform,
                           search_query=search_query, topic=topic,
                           page=page, total_pages=total_pages, total=total)


# ── Add listing ───────────────────────────────────────────────────────────────

@listings_bp.route("/add_listing", methods=["GET", "POST"])
@login_required
def add_listing():
    from flask import current_app
    if request.method == "POST":
        try:
            def _save(f):
                if f and f.filename and allowed_file(f.filename):
                    fname = f"{uuid.uuid4().hex}_{secure_filename(f.filename)}"
                    try:
                        from utils.storage import upload_to_supabase
                        uploaded_url = upload_to_supabase(f, "listing_media", fname)
                        if uploaded_url:
                            return uploaded_url
                    except Exception as upload_err:
                        print(f"[LISTING MEDIA UPLOAD ERROR] {upload_err}")
                return None

            gallery_urls = [
                u for f in request.files.getlist("gallery")
                if (u := _save(f))
            ]

            page_link = request.form.get("page_link","").strip()
            if not page_link:
                flash("Account / Page Link is required.", "danger")
                return render_template("add_listing.html")

            ref = safe_db_reference("listings").push({
                "platform":    request.form.get("platform","").strip(),
                "username":    request.form.get("username","").strip(),
                "page_link":   page_link,

                "followers":   request.form.get("followers","").strip(),
                "price":       float(request.form.get("price",0) or 0),
                "monetized":   request.form.get("monetized","").strip(),
                "topic":       request.form.get("topic","").strip(),
                "income":      request.form.get("income","0").strip(),
                "description": request.form.get("description","").strip(),
                "thumbnail":   _save(request.files.get("thumbnail")),
                "gallery":     gallery_urls,
                "status":      "available",
                "seller_uid":  session["user"]["uid"],
                "view_count":  0,
                "created_at":  str(dt.now()),
            })
            invalidate_listings_cache()
            flash("Listing added successfully!", "success")
            return redirect(url_for("listings.listing_detail", listing_id=ref.key))
        except Exception as e:
            flash(f"Error adding listing: {e}", "danger")
    return render_template("add_listing.html")


# ── Listing detail ────────────────────────────────────────────────────────────

@listings_bp.route("/listing/<listing_id>")
def listing_detail(listing_id):
    if not is_valid_id(listing_id):
        flash("Listing not found.", "danger")
        return redirect(url_for("listings.marketplace"))

    listing = safe_db_reference("listings", listing_id).get()
    if not listing:
        flash("Listing not found.", "danger")
        return redirect(url_for("listings.marketplace"))

    current_uid = session.get("user", {}).get("uid")
    if current_uid and current_uid != listing.get("seller_uid"):
        _increment_views(listing_id)

    seller_uid = listing.get("seller_uid","")
    seller     = get_user_public(seller_uid)
    avg        = seller.get("average_rating") or compute_avg_rating_from_reviews(seller_uid)
    full, half, empty = star_breakdown(avg)

    listing["thumbnail"]  = resolve_media_url(listing.get("thumbnail"))
    listing["gallery"]    = [resolve_media_url(i) for i in listing.get("gallery",[]) if i]
    listing["is_boosted"] = is_listing_boosted(listing)

    return render_template("listing_detail.html",
                           listing=listing, listing_id=listing_id,
                           seller_name=listing.get("seller_username", seller.get("username","Unknown")),
                           seller_pic=seller.get("profile_pic","/static/default_user.png"),
                           seller_verified=seller.get("verified",False),
                           avg_rating=avg, stars_full=full, stars_half=half, stars_empty=empty,
                           seller_id=seller_uid, escrow=listing.get("escrow",{}))


def _increment_views(listing_id):
    try:
        ref   = safe_db_reference("listings", listing_id)
        data  = ref.get() or {}
        ref.update({"view_count": int(data.get("view_count",0)) + 1})
        invalidate_listings_cache()
    except Exception as e:
        print(f"[VIEW COUNT] {e}")


# ── Edit / Delete listing ─────────────────────────────────────────────────────

@listings_bp.route("/edit_listing/<listing_id>", methods=["GET", "POST"])
@login_required
def edit_listing(listing_id):
    user_id     = session["user"]["uid"]
    listing_ref = safe_db_reference("listings", listing_id)
    listing     = listing_ref.get()

    if not listing or listing.get("seller_uid") != user_id:
        flash("Not authorised.", "danger")
        return redirect(url_for("misc.dashboard"))

    if request.method == "POST":
        update = {k: request.form.get(k) for k in
                  ("platform","username","followers","price","description","topic","income", "page_link")}
        thumb = request.form.get("thumbnail","").strip()
        if thumb:
            update["thumbnail"] = thumb
        listing_ref.update(update)
        invalidate_listings_cache()
        flash("Listing updated.", "success")
        return redirect(url_for("misc.dashboard"))

    return render_template("edit_listing.html", listing=listing, listing_id=listing_id)


@listings_bp.route("/delete_listing/<listing_id>")
@login_required
def delete_listing(listing_id):
    user_id     = session["user"]["uid"]
    listing_ref = safe_db_reference("listings", listing_id)
    listing     = listing_ref.get()

    if not listing or listing.get("seller_uid") != user_id:
        flash("Not authorised.", "danger")
        return redirect(url_for("misc.dashboard"))

    listing_ref.delete()
    invalidate_listings_cache()
    flash("Listing deleted.", "success")
    return redirect(url_for("misc.dashboard"))


# ── Boost listing ─────────────────────────────────────────────────────────────

@listings_bp.route("/boost/<listing_id>", methods=["GET", "POST"])
@login_required
def boost_listing(listing_id):
    seller_uid  = session["user"]["uid"]
    listing_ref = safe_db_reference("listings", listing_id)
    listing     = listing_ref.get()

    if not listing or listing.get("seller_uid") != seller_uid:
        flash("Not authorised.", "danger")
        return redirect(url_for("misc.dashboard"))

    if request.method == "POST":
        duration = request.form.get("duration","24h")
        if duration not in BOOST_COST:
            flash("Invalid boost tier.", "danger")
            return redirect(url_for("listings.boost_listing", listing_id=listing_id))

        cost      = BOOST_COST[duration]
        user_ref  = safe_db_reference("users", seller_uid)
        user_data = user_ref.get() or {}
        credit    = int(user_data.get("credit",0))

        if credit < cost:
            flash(f"Insufficient credits. Boost costs {cost} credit(s).", "danger")
            return redirect(url_for("misc.dashboard"))

        until = (dt.utcnow() + datetime.timedelta(hours=BOOST_HOURS[duration])).isoformat()
        user_ref.update({"credit": credit - cost})
        listing_ref.update({"boosted": True, "boosted_until": until, "boost_tier": duration})
        invalidate_listings_cache()
        invalidate_users_cache()
        session["user"]["credit"] = credit - cost
        record_platform_revenue(cost, listing_id)

        label = {"24h":"24 hours","48h":"48 hours","10d":"10 days"}[duration]
        push_notification(seller_uid,
            f"Your listing @{listing.get('username','')} is boosted for {label}!",
            "success", "/dashboard")
        flash(f"Listing boosted for {label}! (${cost} deducted)", "success")
        return redirect(url_for("misc.dashboard"))

    return render_template("boost_listing.html", listing=listing,
                           listing_id=listing_id, boost_cost=BOOST_COST)


# ── Buy (escrow) ──────────────────────────────────────────────────────────────

@listings_bp.route("/buy/<listing_id>", methods=["POST"])
@login_required
def buy(listing_id):
    buyer_uid   = session["user"]["uid"]
    listing_ref = safe_db_reference("listings", listing_id)
    listing     = listing_ref.get()

    if not listing:
        flash("Listing not found.", "danger")
        return redirect(url_for("listings.marketplace"))
    if listing.get("status") != "available":
        flash("Listing is no longer available.", "danger")
        return redirect(url_for("listings.marketplace"))

    seller_uid = listing.get("seller_uid")
    if not seller_uid or seller_uid == buyer_uid:
        flash("Invalid seller.", "danger")
        return redirect(url_for("listings.marketplace"))

    price        = float(listing.get("price",0) or 0)
    buyer_data   = safe_db_reference("users", buyer_uid).get() or {}
    buyer_credit = float(buyer_data.get("credit",0))

    if buyer_credit < price:
        flash("Insufficient credit.", "danger")
        return redirect(url_for("listings.listing_detail", listing_id=listing_id))

    escrow_id = uuid.uuid4().hex
    db.reference("/").update({
        f"/users/{buyer_uid}/credit":           int(buyer_credit - price),
        f"/listings/{listing_id}/status":       "pending_delivery",
        f"/listings/{listing_id}/buyer_uid":    buyer_uid,
        f"/listings/{listing_id}/escrow": {
            "escrow_id": escrow_id, "amount": int(price),
            "created_at": dt.utcnow().isoformat(), "state": "holding",
        },
        f"/purchases/{buyer_uid}/{listing_id}": {
            "listing_id": listing_id, "seller_id": seller_uid,
            "price": int(price), "timestamp": dt.utcnow().isoformat(),
            "status": "pending_delivery",
        },
    })
    invalidate_listings_cache(); invalidate_users_cache()
    session["user"]["credit"] = int(buyer_credit - price)

    push_notification(seller_uid,
        f"@{listing.get('username','')} was purchased! Deliver credentials.",
        "success", f"/deliver/{listing_id}")
    push_notification(buyer_uid, "Purchase initiated. Waiting for credentials.", "info", "/dashboard")

    try:
        sd = safe_db_reference("users", seller_uid).get() or {}
        if sd.get("email"):
            notify_seller_new_purchase(sd["email"], sd.get("username","Seller"),
                listing.get("username",""), listing.get("platform",""), int(price))
    except Exception:
        pass

    flash("Purchase initiated! Credits held in escrow.", "success")
    return redirect(url_for("misc.dashboard"))


# ── Deliver credentials ───────────────────────────────────────────────────────

@listings_bp.route("/deliver/<listing_id>", methods=["GET", "POST"])
@login_required
def deliver_credentials(listing_id):
    seller_uid  = session["user"]["uid"]
    listing_ref = safe_db_reference("listings", listing_id)
    listing     = listing_ref.get()

    if not listing or listing.get("seller_uid") != seller_uid:
        flash("Not authorised.", "danger")
        return redirect(url_for("misc.dashboard"))
    if listing.get("status") != "pending_delivery":
        flash("Not awaiting delivery.", "warning")
        return redirect(url_for("misc.dashboard"))

    if request.method == "POST":
        creds = request.form.get("credentials","").strip()
        if not creds:
            flash("Please enter credentials.", "danger")
            return render_template("deliver_credentials.html", listing=listing, listing_id=listing_id)

        twofa_type   = request.form.get("twofa_type","none").strip()
        twofa_secret = request.form.get("twofa_secret","").strip()
        listing_ref.update({
            "status": "pending_confirmation",
            "delivery": {
                "credentials":  creds,
                "notes":        request.form.get("notes","").strip(),
                "twofa_type":   twofa_type,
                "twofa_secret": twofa_secret if twofa_type == "authenticator" else "",
                "delivered_at": dt.utcnow().isoformat(),
                "delivered_by": seller_uid,
            },
        })
        invalidate_listings_cache()
        buyer_uid = listing.get("buyer_uid")
        push_notification(buyer_uid,
            f"Credentials delivered for @{listing.get('username','')}. Confirm receipt.",
            "success", "/dashboard")
        try:
            bd = safe_db_reference("users", buyer_uid).get() or {}
            if bd.get("email"):
                notify_buyer_credentials_delivered(
                    bd["email"], bd.get("username","Buyer"),
                    listing.get("username",""), listing.get("platform",""))
        except Exception:
            pass
        flash("Credentials delivered!", "success")
        return redirect(url_for("misc.dashboard"))

    return render_template("deliver_credentials.html", listing=listing, listing_id=listing_id)


# ── View credentials (buyer) ──────────────────────────────────────────────────

@listings_bp.route("/view_credentials/<listing_id>")
@login_required
def view_credentials(listing_id):
    buyer_uid = session["user"]["uid"]
    listing   = safe_db_reference("listings", listing_id).get()
    if not listing: abort(404)
    if listing.get("buyer_uid") != buyer_uid:
        flash("Not authorised.", "danger")
        return redirect(url_for("misc.dashboard"))
    if listing.get("status") not in ("pending_confirmation","completed"):
        flash("Credentials not available yet.", "warning")
        return redirect(url_for("misc.dashboard"))

    delivery = listing.get("delivery",{})
    return render_template("view_credentials.html",
                           listing=listing, listing_id=listing_id,
                           credentials=delivery.get("credentials",""),
                           twofa_secret=delivery.get("twofa_secret",""),
                           twofa_type=delivery.get("twofa_type","none"),
                           notes=delivery.get("notes",""))


# ── Confirm delivery ──────────────────────────────────────────────────────────

@listings_bp.route("/confirm_delivery/<listing_id>", methods=["POST"])
@login_required
def confirm_delivery(listing_id):
    buyer_uid   = session["user"]["uid"]
    listing_ref = safe_db_reference("listings", listing_id)
    listing     = listing_ref.get()

    if not listing or listing.get("buyer_uid") != buyer_uid:
        flash("Not authorised.", "danger")
        return redirect(url_for("misc.dashboard"))
    if listing.get("status") != "pending_confirmation":
        flash("Nothing to confirm.", "warning")
        return redirect(url_for("misc.dashboard"))

    amount     = int(listing.get("escrow",{}).get("amount",0))
    seller_uid = listing.get("seller_uid")
    fee, seller_gets = calculate_fee(amount)
    sd = safe_db_reference("users", seller_uid).get() or {}

    db.reference("/").update({
        f"/users/{seller_uid}/credit":                 int(sd.get("credit",0)) + seller_gets,
        f"/listings/{listing_id}/status":              "completed",
        f"/listings/{listing_id}/escrow/state":        "released",
        f"/listings/{listing_id}/escrow/released_at":  dt.utcnow().isoformat(),
        f"/listings/{listing_id}/escrow/fee":          fee,
        f"/listings/{listing_id}/escrow/seller_gets":  seller_gets,
        f"/purchases/{buyer_uid}/{listing_id}/status": "completed",
    })
    record_platform_revenue(fee, listing_id)
    invalidate_listings_cache(); invalidate_users_cache()
    push_notification(seller_uid,
        f"Payment released! {seller_gets} credits added (fee: {fee}).", "success", "/wallet")
    flash(f"Confirmed! {seller_gets} credits released to seller.", "success")

    try:
        sd2 = safe_db_reference("users", seller_uid).get() or {}
        if sd2.get("email"):
            notify_seller_confirmed(sd2["email"], sd2.get("username","Seller"),
                listing.get("username",""), seller_gets)
    except Exception:
        pass

    return redirect(url_for("misc.dashboard"))


# ── Dispute ───────────────────────────────────────────────────────────────────

@listings_bp.route("/dispute/<listing_id>", methods=["POST"])
@login_required
def dispute_delivery(listing_id):
    buyer_uid   = session["user"]["uid"]
    listing_ref = safe_db_reference("listings", listing_id)
    listing     = listing_ref.get()

    if not listing or listing.get("buyer_uid") != buyer_uid:
        flash("Not authorised.", "danger")
        return redirect(url_for("misc.dashboard"))
    if listing.get("status") != "pending_confirmation":
        flash("Can only dispute after delivery.", "warning")
        return redirect(url_for("misc.dashboard"))

    delivered_at = listing.get("delivery",{}).get("delivered_at")
    if delivered_at:
        try:
            elapsed = (dt.utcnow() - dt.fromisoformat(delivered_at)).total_seconds() / 3600
            if elapsed > 72:
                flash("Dispute window closed (72h).", "warning")
                return redirect(url_for("misc.dashboard"))
        except Exception:
            pass

    reason = request.form.get("reason","").strip()
    listing_ref.update({
        "status": "disputed",
        "dispute": {"reason": reason, "raised_by": buyer_uid,
                    "raised_at": dt.utcnow().isoformat(), "resolved": False},
        "escrow/state": "disputed",
    })
    invalidate_listings_cache()
    seller_uid = listing.get("seller_uid")
    push_notification(seller_uid,
        f"Dispute raised on @{listing.get('username','')}. Admin will review.",
        "warning", "/dashboard")
    flash("Dispute raised. Admin will review shortly.", "warning")

    try:
        bd = safe_db_reference("users", buyer_uid).get() or {}
        sd = safe_db_reference("users", seller_uid).get() or {}
        notify_dispute_raised(
            buyer_email=bd.get("email",""), seller_email=sd.get("email",""),
            admin_email=GMAIL_USER,
            buyer_name=bd.get("username","Buyer"), seller_name=sd.get("username","Seller"),
            listing_username=listing.get("username",""), reason=reason)
    except Exception:
        pass

    return redirect(url_for("misc.dashboard"))


@listings_bp.route("/listing/<listing_id>/run_legitimacy", methods=["POST"])
@login_required
def run_legitimacy(listing_id):
    buyer_uid   = session["user"]["uid"]
    listing_ref = safe_db_reference("listings", listing_id)
    listing     = listing_ref.get()

    if not listing:
        flash("Listing not found.", "danger")
        return redirect(url_for("listings.marketplace"))

    if listing.get("platform") != "YouTube":
        flash("Legitimacy test is only available for YouTube listings.", "danger")
        return redirect(url_for("listings.listing_detail", listing_id=listing_id))

    page_link = listing.get("page_link", "").strip()
    if not page_link:
        flash("This listing does not have a channel link to run the legitimacy test.", "danger")
        return redirect(url_for("listings.listing_detail", listing_id=listing_id))

    # Fee is 2 credits
    user_ref  = safe_db_reference("users", buyer_uid)
    user_data = user_ref.get() or {}
    credit    = int(user_data.get("credit", 0))

    if credit < 2:
        flash("Insufficient credits. Running a legitimacy test costs 2 credits.", "danger")
        return redirect(url_for("listings.listing_detail", listing_id=listing_id))

    try:
        from yt_bot_detector.analyzer import analyze
        report = analyze(page_link)
        if report.get("error"):
            flash(f"Error running legitimacy test: {report['error']}", "danger")
            return redirect(url_for("listings.listing_detail", listing_id=listing_id))

        # Deduct fee from user
        new_credit = credit - 2
        user_ref.update({"credit": new_credit})
        session["user"]["credit"] = new_credit
        record_platform_revenue(2, listing_id)

        # Update listing with legitimacy report
        listing_ref.update({
            "legitimacy_report": {
                "verdict": report["verdict"],
                "top_red_flags": report["top_red_flags"],
                "top_clean_signals": report["top_clean_signals"],
                "features": report["features"],
                "meta": report["meta"],
                "channel": report["channel"],
                "run_at": datetime.datetime.now().isoformat(),
                "run_by_username": session["user"]["username"]
            }
        })
        invalidate_listings_cache()
        flash("Legitimacy test completed successfully! 2 credits deducted.", "success")
    except Exception as e:
        flash(f"An unexpected error occurred during analysis: {e}", "danger")

    return redirect(url_for("listings.listing_detail", listing_id=listing_id))

