"""
analyzer.py
Rule-based weighted scoring engine for YouTube channel bot detection.
Replaces the miscalibrated Logistic Regression with threshold-based feature scoring
that matches real-world RF results.
"""

import os
import math

try:
    from yt_bot_detector.feature_engineering import extract_features, FEATURE_NAMES
    from yt_bot_detector.youtube_fetcher import fetch_all_data
except ImportError:
    from feature_engineering import extract_features, FEATURE_NAMES
    from youtube_fetcher import fetch_all_data


# ─── Organic channel baseline means ──────────────────────────────────────────
ORGANIC_MEANS = {
    "sub_view_ratio":     0.011,
    "views_per_sub":      0.313,
    "like_rate":          0.060,
    "comment_rate":       0.008,
    "comment_like_ratio": 0.133,
    "view_cv":            0.512,
    "upload_rate":        0.060,
    "log_age":            8.203,
    "log_subs":           13.51,
    "log_total_views":    18.13,
    "hidden_subs":        0.00,
    "disabled_comments":  0.00,
    "comment_diversity":  0.514,
    "spam_ratio":         0.00,
    "avg_comment_len":    2.06,
    "comments_collected": 54.7,
    "top_avg_ratio":      2.54,
    "bot_avg_ratio":      0.325,
    "view_like_corr":     0.759,
    "log_avg_views":      12.21,
    "log_avg_likes":      9.39,
    "log_avg_comments":   7.37,
    "vps_anomaly":        0.00,
    "engagement_score":   0.068,
}

BOT_MEANS = {
    "sub_view_ratio":     0.130,
    "views_per_sub":      0.737,
    "like_rate":          0.020,
    "comment_rate":       0.008,
    "comment_like_ratio": 1.523,
    "view_cv":            0.187,
    "upload_rate":        0.102,
    "log_age":            6.921,
    "log_subs":           12.59,
    "log_total_views":    15.09,
    "hidden_subs":        0.488,
    "disabled_comments":  0.00,
    "comment_diversity":  0.503,
    "spam_ratio":         0.319,
    "avg_comment_len":    1.992,
    "comments_collected": 17.6,
    "top_avg_ratio":      1.416,
    "bot_avg_ratio":      0.709,
    "view_like_corr":     0.586,
    "log_avg_views":      9.90,
    "log_avg_likes":      5.34,
    "log_avg_comments":   3.83,
    "vps_anomaly":        0.276,
    "engagement_score":   0.028,
}

# Feature weights — higher = more important for bot detection
WEIGHTS = {
    "like_rate":          0.210,
    "log_age":            0.197,
    "engagement_score":   0.135,
    "sub_view_ratio":     0.112,
    "log_total_views":    0.081,
    "view_cv":            0.055,
    "top_avg_ratio":      0.036,
    "comments_collected": 0.035,
    "bot_avg_ratio":      0.032,
    "log_avg_comments":   0.022,
    "comment_like_ratio": 0.021,
    "comment_rate":       0.018,
    "log_avg_likes":      0.014,
    "spam_ratio":         0.013,
    "avg_comment_len":    0.007,
    "comment_diversity":  0.007,
    "hidden_subs":        0.002,
    "log_avg_views":      0.002,
    "log_subs":           0.001,
    "views_per_sub":      0.001,
    "vps_anomaly":        0.001,
    "upload_rate":        0.000,
    "disabled_comments":  0.000,
    "view_like_corr":     0.000,
}

# Direction: True = higher value → more bot-like
# False = higher value → more organic
BOT_DIRECTION = {
    "sub_view_ratio":     True,   # more subs per view = dead subs
    "views_per_sub":      True,   # very high = view purchase
    "like_rate":          False,  # low = fake views
    "comment_rate":       False,  # low = fake views
    "comment_like_ratio": True,   # high imbalance = bots
    "view_cv":            False,  # low CV = uniform package views
    "upload_rate":        True,   # very high = automation
    "log_age":            False,  # young = suspicious
    "log_subs":           True,   # alone not useful, context matters
    "log_total_views":    False,  # many views vs age/subs = suspicious when low
    "hidden_subs":        True,   # hiding = suspicious
    "disabled_comments":  True,   # hiding = suspicious
    "comment_diversity":  False,  # low = repeated bot comments
    "spam_ratio":         True,   # high = bots
    "avg_comment_len":    False,  # short = bots
    "comments_collected": False,  # few = fewer visible = hiding
    "top_avg_ratio":      False,  # huge outlier spike = artificial
    "bot_avg_ratio":      True,   # high floor-to-avg = uniform packages
    "view_like_corr":     False,  # low correlation = decoupled engagement
    "log_avg_views":      False,  # context metric
    "log_avg_likes":      False,  # context metric
    "log_avg_comments":   False,  # context metric
    "vps_anomaly":        True,   # extreme = suspicious
    "engagement_score":   False,  # low = fake views/subs
}


def _score_feature(name: str, val: float) -> float:
    """
    Returns a score in [-1.0, +1.0] for one feature.
    Positive = pushes toward bot, negative = pushes toward organic.
    Uses sigmoid-like mapping relative to organic/bot means.
    """
    o = ORGANIC_MEANS.get(name, 0.0)
    b = BOT_MEANS.get(name, 0.0)
    span = abs(b - o)
    if span < 1e-9:
        return 0.0

    bot_dir = BOT_DIRECTION.get(name, True)
    if bot_dir:
        # Higher value = more bot
        raw = (val - o) / span
    else:
        # Lower value = more bot (invert)
        raw = (o - val) / span

    # Clamp and smooth
    return max(-2.0, min(2.0, raw)) * 0.5  # [-1, +1]


def _compute_bot_probability(raw_feats: dict) -> float:
    """Weighted sum of per-feature bot scores → sigmoid → probability."""
    total_weight = sum(WEIGHTS.values()) or 1.0
    weighted_score = 0.0
    for name, weight in WEIGHTS.items():
        val = raw_feats.get(name, 0.0)
        s = _score_feature(name, val)
        weighted_score += s * weight

    # Normalize to z-score range and pass through sigmoid
    z = weighted_score / total_weight * 8.0  # scale factor calibrated on real channels
    try:
        prob_bot = 1.0 / (1.0 + math.exp(-z))
    except OverflowError:
        prob_bot = 0.0 if z < 0 else 1.0

    return round(prob_bot, 4)


# ─── risk tier ──────────────────────────────────────────────────────────────

def _risk_tier(prob_bot: float) -> dict:
    if prob_bot < 0.20:
        return {"label": "GENUINE",      "color": "#22c55e", "icon": "✅", "score": round((1 - prob_bot) * 100)}
    elif prob_bot < 0.40:
        return {"label": "MOSTLY REAL",  "color": "#84cc16", "icon": "🟡", "score": round((1 - prob_bot) * 100)}
    elif prob_bot < 0.60:
        return {"label": "SUSPICIOUS",   "color": "#f59e0b", "icon": "⚠️",  "score": round((1 - prob_bot) * 100)}
    elif prob_bot < 0.78:
        return {"label": "LIKELY FAKE",  "color": "#f97316", "icon": "🚨", "score": round((1 - prob_bot) * 100)}
    else:
        return {"label": "BOT / FRAUD",  "color": "#ef4444", "icon": "🤖", "score": round((1 - prob_bot) * 100)}


def _human_feature(name: str, value: float, shap_val: float) -> dict:
    labels = {
        "sub_view_ratio":     ("Subscriber-to-View Ratio",    "High value = subs rarely watch → dead subscribers"),
        "views_per_sub":      ("Views per Subscriber",         "Too low = sub-bot likely; too high = view-purchase"),
        "like_rate":          ("Like Rate",                    "Abnormally low like rate signals fake views"),
        "comment_rate":       ("Comment Rate",                 "Suppressed comments relative to views = red flag"),
        "comment_like_ratio": ("Comment-to-Like Ratio",        "Imbalance signals bot engagement"),
        "view_cv":            ("View Variance (CV)",           "Near-zero CV = uniform view packages purchased"),
        "upload_rate":        ("Upload Frequency",             "Unusually high rate can indicate automation"),
        "log_age":            ("Channel Age",                  "Very new channels with huge subs = suspicious"),
        "log_subs":           ("Subscriber Count",             "Large sub count used in context with other signals"),
        "log_total_views":    ("Total Lifetime Views",         "Compared to subs and age"),
        "hidden_subs":        ("Hidden Subscriber Count",      "Sellers often hide subs to obscure manipulation"),
        "disabled_comments":  ("Comments Disabled Fraction",   "Blocking comments hides spam/bots"),
        "comment_diversity":  ("Comment Diversity",            "Low diversity = repeated bot comments"),
        "spam_ratio":         ("Spam Comment Ratio",           "High fraction of spam links / sub-for-sub requests"),
        "avg_comment_len":    ("Avg Comment Length",           "Very short generic comments indicate bots"),
        "comments_collected": ("Comments Collected",           "Fewer comments available = possible disabling"),
        "top_avg_ratio":      ("Top-to-Average Views",         "Huge outlier video may indicate one viral spike"),
        "bot_avg_ratio":      ("Bottom-to-Average Views",      "Very low floor relative to average = suspicious"),
        "view_like_corr":     ("View-Like Correlation",        "Low correlation signals decoupled engagement (bots)"),
        "log_avg_views":      ("Avg Views per Video",          "Per-video average reach"),
        "log_avg_likes":      ("Avg Likes per Video",          "Per-video average likes"),
        "log_avg_comments":   ("Avg Comments per Video",       "Per-video average comments"),
        "vps_anomaly":        ("Views-per-Sub Anomaly",        "Extreme values flag paid engagement"),
        "engagement_score":   ("Overall Engagement Score",     "Combined likes+comments vs views"),
    }
    label, desc = labels.get(name, (name, ""))
    direction = "🔴 Red Flag" if shap_val > 0 else "🟢 Clean Signal"
    return {
        "name": name,
        "label": label,
        "description": desc,
        "value": round(value, 6),
        "shap": round(shap_val, 6),
        "direction": direction,
    }


# ─── main public API ─────────────────────────────────────────────────────────

def analyze(channel_input: str, api_key: str = None) -> dict:
    """
    Full pipeline: fetch → features → score → explain.
    Returns structured report dict.
    """
    data = fetch_all_data(channel_input, api_key=api_key)
    if not data:
        return {"error": f"Could not fetch data for: {channel_input}"}

    raw_feats = extract_features(data)

    # ── compute bot probability via weighted rule scoring ────────────────────
    prob_bot = _compute_bot_probability(raw_feats)
    verdict  = _risk_tier(prob_bot)
    verdict["prob_bot"] = prob_bot

    # ── per-feature SHAP-proxy scores ────────────────────────────────────────
    feature_cards = []
    for name in FEATURE_NAMES:
        val = raw_feats[name]
        # shap = weighted contribution (+ve = bot direction)
        s = _score_feature(name, val)
        w = WEIGHTS.get(name, 0.0)
        shap_val = s * w  # signed contribution
        feature_cards.append(_human_feature(name, val, shap_val))

    # Sort by absolute contribution impact
    feature_cards.sort(key=lambda c: abs(c["shap"]), reverse=True)

    top_red   = [c for c in feature_cards if c["shap"] > 0][:5]
    top_clean = [c for c in feature_cards if c["shap"] < 0][:5]

    ch = data.get("channel", {})
    channel_info = {
        "title":            ch.get("title", "Unknown"),
        "handle":           ch.get("handle", ""),
        "subscriber_count": ch.get("subscriber_count", 0),
        "total_views":      ch.get("total_views", 0),
        "video_count":      ch.get("video_count", 0),
        "thumbnail":        ch.get("thumbnail", ""),
        "country":          ch.get("country", ""),
    }

    return {
        "channel":           channel_info,
        "verdict":           verdict,
        "features":          feature_cards,
        "top_red_flags":     top_red,
        "top_clean_signals": top_clean,
        "raw_features":      raw_feats,
        "meta": {
            "n_videos_analyzed":   len(data.get("videos", [])),
            "n_comments_analyzed": len(data.get("comments", [])),
        },
        "error": None,
    }


if __name__ == "__main__":
    import sys
    target = sys.argv[1] if len(sys.argv) > 1 else "@MrBeast"
    result = analyze(target)
    if result.get("error"):
        print("ERROR:", result["error"])
    else:
        ch = result["channel"]
        v  = result["verdict"]
        title = ch['title']
        subs  = ch['subscriber_count']
        icon  = v['icon']
        label = v['label']
        score = v['score']
        prob  = v['prob_bot'] * 100
        print(f"\nChannel : {title}")
        print(f"Subs    : {subs:,}")
        print(f"Verdict : {icon} {label}  (Authenticity: {score}%)")
        print(f"Bot Prob: {prob:.1f}%")
        print("\nTop Bot Signals:")
        for c in result["top_red_flags"]:
            lbl = c['label']
            val = c['value']
            shap = c['shap']
            print(f"  • {lbl}: {val:.4f}  (score +{shap:.4f})")
        print("\nTop Clean Signals:")
        for c in result["top_clean_signals"]:
            lbl = c['label']
            val = c['value']
            shap = c['shap']
            print(f"  • {lbl}: {val:.4f}  (score {shap:.4f})")
