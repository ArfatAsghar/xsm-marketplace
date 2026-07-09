"""
feature_engineering.py
Converts raw YouTube channel data dict into exactly 24 numeric features
for the XGBoost bot-detection model.
"""

import math
import re
from collections import Counter


# ─────────────────────────── helpers ────────────────────────────────────────

def safe_div(a, b, default=0.0):
    try:
        return a / b if b else default
    except Exception:
        return default


def _comment_diversity(comments):
    """Unique-comment ratio: 1.0 = all unique, 0.0 = all identical."""
    if not comments:
        return 1.0
    total = len(comments)
    unique = len(set(c.strip().lower() for c in comments))
    return safe_div(unique, total, 1.0)


def _spam_comment_ratio(comments):
    """Fraction of comments that look spammy."""
    if not comments:
        return 0.0
    spam_patterns = re.compile(
        r"(sub.?back|check my|click here|earn \$|free (cash|money|gift)|visit my|"
        r"bit\.ly|tinyurl|goo\.gl|follow me|sub4sub|subscribe back|link in bio|"
        r"check my channel|watch my)",
        re.IGNORECASE
    )
    spam_count = sum(1 for c in comments if spam_patterns.search(c))
    return safe_div(spam_count, len(comments))


def _avg_comment_length(comments):
    """Average word count per comment (short generic comments → bots)."""
    if not comments:
        return 0.0
    return safe_div(sum(len(c.split()) for c in comments), len(comments))


def _channel_age_days(published_at: str) -> int:
    """Days since channel was created. Returns 365 if unparseable."""
    try:
        from datetime import datetime, timezone
        dt = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        return max(1, (now - dt).days)
    except Exception:
        return 365


def _view_variance_ratio(video_views):
    """
    Coefficient of variation of video views.
    Very low CV = suspiciously uniform views (bulk-purchased packages).
    """
    if len(video_views) < 2:
        return 0.0
    mean = safe_div(sum(video_views), len(video_views))
    if mean == 0:
        return 0.0
    variance = safe_div(sum((v - mean) ** 2 for v in video_views), len(video_views))
    std = math.sqrt(variance)
    return safe_div(std, mean)


# ──────────────────────── main extractor ────────────────────────────────────

def extract_features(data: dict) -> dict:
    """
    data = {
        "channel": { subscriber_count, total_views, video_count,
                     published_at, hidden_subscriber_count, ... },
        "videos":  [ {views, likes, comments, comments_disabled}, ... ],
        "comments": [ "text", ... ]
    }

    Returns a dict of exactly 24 named features.
    """
    ch = data.get("channel", {})
    videos = data.get("videos", [])
    comments = data.get("comments", [])

    subs       = max(1, ch.get("subscriber_count", 1))
    total_views= ch.get("total_views", 0)
    video_count= max(1, ch.get("video_count", 1))
    age_days   = _channel_age_days(ch.get("published_at", ""))

    # ── per-video arrays ────────────────────────────────────────────────────
    views_list    = [v.get("views", 0)    for v in videos]
    likes_list    = [v.get("likes", 0)    for v in videos]
    comments_list = [v.get("comments", 0) for v in videos]
    disabled_list = [v.get("comments_disabled", False) for v in videos]

    total_vid_views    = sum(views_list)    or 1
    total_vid_likes    = sum(likes_list)
    total_vid_comments = sum(comments_list)
    n_vids             = len(videos)        or 1

    avg_views    = safe_div(total_vid_views, n_vids)
    avg_likes    = safe_div(total_vid_likes, n_vids)
    avg_comments = safe_div(total_vid_comments, n_vids)

    # ── Feature 1: subscriber-to-view ratio ─────────────────────────────────
    f01_sub_view_ratio = safe_div(subs, max(total_views, 1))

    # ── Feature 2: views per subscriber (recent 30 vids avg) ────────────────
    f02_views_per_sub = safe_div(avg_views, subs)

    # ── Feature 3: like rate (likes / views) ────────────────────────────────
    f03_like_rate = safe_div(total_vid_likes, total_vid_views)

    # ── Feature 4: comment rate (comments / views) ───────────────────────────
    f04_comment_rate = safe_div(total_vid_comments, total_vid_views)

    # ── Feature 5: comment-to-like ratio ────────────────────────────────────
    f05_comment_like_ratio = safe_div(total_vid_comments, max(total_vid_likes, 1))

    # ── Feature 6: coefficient of variation of views ─────────────────────────
    f06_view_cv = _view_variance_ratio(views_list)

    # ── Feature 7: videos per day since creation ─────────────────────────────
    f07_upload_rate = safe_div(video_count, age_days)

    # ── Feature 8: channel age in days (log-scaled) ──────────────────────────
    f08_log_age = math.log1p(age_days)

    # ── Feature 9: subscriber count (log-scaled) ─────────────────────────────
    f09_log_subs = math.log1p(subs)

    # ── Feature 10: total views (log-scaled) ─────────────────────────────────
    f10_log_total_views = math.log1p(total_views)

    # ── Feature 11: hidden subscriber flag ───────────────────────────────────
    f11_hidden_subs = 1.0 if ch.get("hidden_subscriber_count", False) else 0.0

    # ── Feature 12: comments-disabled fraction ───────────────────────────────
    f12_disabled_comments = safe_div(sum(disabled_list), n_vids)

    # ── Feature 13: comment diversity (unique ratio) ──────────────────────────
    f13_comment_diversity = _comment_diversity(comments)

    # ── Feature 14: spam comment ratio ───────────────────────────────────────
    f14_spam_ratio = _spam_comment_ratio(comments)

    # ── Feature 15: average comment length (words) ───────────────────────────
    f15_avg_comment_len = _avg_comment_length(comments)

    # ── Feature 16: number of comments collected ──────────────────────────────
    f16_comments_collected = float(len(comments))

    # ── Feature 17: top-video-to-avg-views ratio (viral spike detector) ──────
    top_views = max(views_list) if views_list else 0
    f17_top_avg_ratio = safe_div(top_views, avg_views + 1)

    # ── Feature 18: bottom-video-to-avg-views ratio ───────────────────────────
    bot_views = min(views_list) if views_list else 0
    f18_bot_avg_ratio = safe_div(bot_views, avg_views + 1)

    # ── Feature 19: view-like correlation proxy ───────────────────────────────
    # If likes increase proportionally with views → organic; if flat → bought
    if len(views_list) >= 2 and len(likes_list) >= 2:
        zipped = list(zip(views_list, likes_list))
        mean_v = safe_div(sum(v for v, _ in zipped), len(zipped))
        mean_l = safe_div(sum(l for _, l in zipped), len(zipped))
        cov = safe_div(sum((v - mean_v) * (l - mean_l) for v, l in zipped), len(zipped))
        std_v = math.sqrt(safe_div(sum((v - mean_v) ** 2 for v, _ in zipped), len(zipped)))
        std_l = math.sqrt(safe_div(sum((l - mean_l) ** 2 for _, l in zipped), len(zipped)))
        f19_view_like_corr = safe_div(cov, (std_v * std_l) + 1e-9)
    else:
        f19_view_like_corr = 0.0

    # ── Feature 20: average views per video (log-scaled) ─────────────────────
    f20_log_avg_views = math.log1p(avg_views)

    # ── Feature 21: average likes per video (log-scaled) ─────────────────────
    f21_log_avg_likes = math.log1p(avg_likes)

    # ── Feature 22: average comments per video (log-scaled) ───────────────────
    f22_log_avg_comments = math.log1p(avg_comments)

    # ── Feature 23: views-per-sub anomaly score ───────────────────────────────
    # Very high or very low vs. expected range [0.1, 10] → suspicious
    vps = safe_div(avg_views, subs)
    if vps < 0.005 or vps > 50:
        f23_vps_anomaly = 1.0
    elif vps < 0.05 or vps > 20:
        f23_vps_anomaly = 0.5
    else:
        f23_vps_anomaly = 0.0

    # ── Feature 24: overall engagement score (likes+comments / views) ─────────
    f24_engagement_score = safe_div(
        total_vid_likes + total_vid_comments, total_vid_views + 1
    )

    return {
        "sub_view_ratio":        f01_sub_view_ratio,
        "views_per_sub":         f02_views_per_sub,
        "like_rate":             f03_like_rate,
        "comment_rate":          f04_comment_rate,
        "comment_like_ratio":    f05_comment_like_ratio,
        "view_cv":               f06_view_cv,
        "upload_rate":           f07_upload_rate,
        "log_age":               f08_log_age,
        "log_subs":              f09_log_subs,
        "log_total_views":       f10_log_total_views,
        "hidden_subs":           f11_hidden_subs,
        "disabled_comments":     f12_disabled_comments,
        "comment_diversity":     f13_comment_diversity,
        "spam_ratio":            f14_spam_ratio,
        "avg_comment_len":       f15_avg_comment_len,
        "comments_collected":    f16_comments_collected,
        "top_avg_ratio":         f17_top_avg_ratio,
        "bot_avg_ratio":         f18_bot_avg_ratio,
        "view_like_corr":        f19_view_like_corr,
        "log_avg_views":         f20_log_avg_views,
        "log_avg_likes":         f21_log_avg_likes,
        "log_avg_comments":      f22_log_avg_comments,
        "vps_anomaly":           f23_vps_anomaly,
        "engagement_score":      f24_engagement_score,
    }


FEATURE_NAMES = list(extract_features({
    "channel": {
        "subscriber_count": 1000, "total_views": 100000,
        "video_count": 50, "published_at": "2020-01-01T00:00:00Z",
        "hidden_subscriber_count": False
    },
    "videos": [{"views": 1000, "likes": 50, "comments": 5, "comments_disabled": False}],
    "comments": ["nice video"]
}).keys())
