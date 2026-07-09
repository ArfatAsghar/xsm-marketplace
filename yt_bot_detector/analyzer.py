"""
analyzer.py
Loads the trained Logistic Regression model coefficients and runs end-to-end inference in pure Python:
  fetch → features → predict (pure Python LR) → proxy SHAP explain → structured report
"""

import os
import json
import math

try:
    from yt_bot_detector.feature_engineering import extract_features, FEATURE_NAMES
    from yt_bot_detector.youtube_fetcher import fetch_all_data
except ImportError:
    from feature_engineering import extract_features, FEATURE_NAMES
    from youtube_fetcher import fetch_all_data


# ─────────────────── model parameters (pure Python LR) ───────────────────────

LR_INTERCEPT = -0.30  # calibrated neutral baseline — prevents universal "genuine" bias

LR_COEFFS = [
    0.4466392993927002,      # sub_view_ratio
    -0.11691398918628693,    # views_per_sub
    -1.3507740497589111,     # like_rate
    0.25191864371299744,     # comment_rate
    0.14245055615901947,     # comment_like_ratio
    -0.88691645860672,       # view_cv
    0.19768337905406952,     # upload_rate
    -1.7512792348861694,     # log_age
    0.3746248781681061,      # log_subs
    -0.7428250312805176,     # log_total_views
    0.5148366093635559,      # hidden_subs
    0.0,                     # disabled_comments
    -0.6981310248374939,     # comment_diversity
    1.563989281654358,       # spam_ratio
    0.376443088054657,       # avg_comment_len
    -1.2328894138336182,     # comments_collected
    -0.6217555403709412,     # top_avg_ratio
    0.9887635707855225,      # bot_avg_ratio
    0.17161493003368378,     # view_like_corr
    -0.29656532406806946,    # log_avg_views
    -0.5339978337287903,     # log_avg_likes
    -0.42677703499794006,    # log_avg_comments
    0.46654435992240906,     # vps_anomaly
    -0.4643995463848114      # engagement_score
]

LR_MEANS = [
    0.07480409695475827,
    0.5317286869192485,
    0.039890186486612945,
    0.00888830922782472,
    0.8313421988738701,
    0.35057122118983536,
    0.08026808506826637,
    7.558793152689934,
    13.045020999312401,
    16.585217672348023,
    0.2425,
    0.0,
    0.5088407717347145,
    0.16677380147203802,
    2.0479703992903233,
    36.45,
    1.984228210389614,
    0.5145074110850691,
    0.6663504264086841,
    11.095814038962125,
    7.395304716225714,
    5.634255601204932,
    0.132125,
    0.04873964231822174
]

LR_STDS = [
    0.15179123429396574,
    0.958453329139991,
    0.023158442196770143,
    0.04154564996907414,
    5.053219769847347,
    0.20942615256660932,
    0.052944129364632206,
    0.6728212187423283,
    1.1028152795214627,
    1.9654213296664786,
    0.4285950886326094,
    1.0,
    0.19330744010155348,
    0.2975686097949741,
    0.699353624068591,
    22.148589119851305,
    0.7700230666771531,
    0.2636544025983312,
    0.27327500367755536,
    2.5804671767419145,
    2.8373503377625484,
    2.443820039844998,
    0.33407407019252083,
    0.04543026805469832
]


# ── feature importances & profiling means for local explanations ───────────────────

FEATURE_IMPORTANCES = {
    "like_rate": 0.209224,
    "log_age": 0.196904,
    "engagement_score": 0.135021,
    "sub_view_ratio": 0.111784,
    "log_total_views": 0.080596,
    "view_cv": 0.054673,
    "top_avg_ratio": 0.036346,
    "comments_collected": 0.034578,
    "bot_avg_ratio": 0.031693,
    "log_avg_comments": 0.022331,
    "comment_like_ratio": 0.021329,
    "comment_rate": 0.017879,
    "log_avg_likes": 0.014497,
    "spam_ratio": 0.012733,
    "avg_comment_len": 0.006933,
    "comment_diversity": 0.006833,
    "hidden_subs": 0.002316,
    "log_avg_views": 0.002314,
    "log_subs": 0.000749,
    "views_per_sub": 0.000648,
    "vps_anomaly": 0.000488,
    "upload_rate": 0.000131,
    "disabled_comments": 0.0,
    "view_like_corr": 0.0
}

ORGANIC_MEANS = {
    "sub_view_ratio": 0.011410539038479328,
    "views_per_sub": 0.3133045732975006,
    "like_rate": 0.05993172153830528,
    "comment_rate": 0.007946250028908253,
    "comment_like_ratio": 0.13341358304023743,
    "view_cv": 0.5123059749603271,
    "upload_rate": 0.059611499309539795,
    "log_age": 8.203341484069824,
    "log_subs": 13.516475677490234,
    "log_total_views": 18.12601661682129,
    "hidden_subs": 0.0,
    "disabled_comments": 0.0,
    "comment_diversity": 0.5145220756530762,
    "spam_ratio": 0.0,
    "avg_comment_len": 2.06109881401062,
    "comments_collected": 54.71466827392578,
    "top_avg_ratio": 2.546874523162842,
    "bot_avg_ratio": 0.32552582025527954,
    "view_like_corr": 0.7586963772773743,
    "log_avg_views": 12.207462310791016,
    "log_avg_likes": 9.390250205993652,
    "log_avg_comments": 7.369561195373535,
    "vps_anomaly": 0.0,
    "engagement_score": 0.06787791103124619
}

BOT_MEANS = {
    "sub_view_ratio": 0.12973082065582275,
    "views_per_sub": 0.7370650768280029,
    "like_rate": 0.019969580695033073,
    "comment_rate": 0.007956593297421932,
    "comment_like_ratio": 1.5234614610671997,
    "view_cv": 0.18681852519512177,
    "upload_rate": 0.10239092260599136,
    "log_age": 6.921595096588135,
    "log_subs": 12.593055725097656,
    "log_total_views": 15.08756160736084,
    "hidden_subs": 0.4880000054836273,
    "disabled_comments": 0.0,
    "comment_diversity": 0.502870500087738,
    "spam_ratio": 0.3188885450363159,
    "avg_comment_len": 1.9921575784683228,
    "comments_collected": 17.6113338470459,
    "top_avg_ratio": 1.4157581329345703,
    "bot_avg_ratio": 0.7094672322273254,
    "view_like_corr": 0.5860517024993896,
    "log_avg_views": 9.902387619018555,
    "log_avg_likes": 5.342679023742676,
    "log_avg_comments": 3.832756280899048,
    "vps_anomaly": 0.2763333320617676,
    "engagement_score": 0.02790861576795578
}


# ─────────────────── risk tier helpers ──────────────────────────────────────

def _risk_tier(prob_bot: float) -> dict:
    if prob_bot < 0.18:
        return {"label": "GENUINE",       "color": "#22c55e", "icon": "✅", "score": round((1 - prob_bot) * 100)}
    elif prob_bot < 0.38:
        return {"label": "MOSTLY REAL",   "color": "#84cc16", "icon": "🟡", "score": round((1 - prob_bot) * 100)}
    elif prob_bot < 0.58:
        return {"label": "SUSPICIOUS",    "color": "#f59e0b", "icon": "⚠️",  "score": round((1 - prob_bot) * 100)}
    elif prob_bot < 0.78:
        return {"label": "LIKELY FAKE",   "color": "#f97316", "icon": "🚨", "score": round((1 - prob_bot) * 100)}
    else:
        return {"label": "BOT / FRAUD",   "color": "#ef4444", "icon": "🤖", "score": round((1 - prob_bot) * 100)}


def _human_feature(name: str, value: float, shap_val: float) -> dict:
    """Returns a human-readable signal card for one feature."""
    labels = {
        "sub_view_ratio":     ("Subscriber-to-View Ratio",     "High value = subs rarely watch → dead subscribers"),
        "views_per_sub":      ("Views per Subscriber",          "Too low = sub-bot likely; too high = view-purchase"),
        "like_rate":          ("Like Rate",                     "Abnormally low like rate signals fake views"),
        "comment_rate":       ("Comment Rate",                  "Suppressed comments relative to views = red flag"),
        "comment_like_ratio": ("Comment-to-Like Ratio",         "Imbalance signals bot engagement"),
        "view_cv":            ("View Variance (CV)",            "Near-zero CV = uniform view packages purchased"),
        "upload_rate":        ("Upload Frequency",              "Unusually high rate can indicate automation"),
        "log_age":            ("Channel Age",                   "Very new channels with huge subs = suspicious"),
        "log_subs":           ("Subscriber Count",              "Large sub count used in context with other signals"),
        "log_total_views":    ("Total Lifetime Views",          "Compared to subs and age"),
        "hidden_subs":        ("Hidden Subscriber Count",       "Sellers often hide subs to obscure manipulation"),
        "disabled_comments":  ("Comments Disabled Fraction",    "Blocking comments hides spam/bots"),
        "comment_diversity":  ("Comment Diversity",             "Low diversity = repeated bot comments"),
        "spam_ratio":         ("Spam Comment Ratio",            "High fraction of spam links / sub-for-sub requests"),
        "avg_comment_len":    ("Avg Comment Length",            "Very short generic comments indicate bots"),
        "comments_collected": ("Comments Collected",            "Fewer comments available = possible disabling"),
        "top_avg_ratio":      ("Top-to-Average Views",          "Huge outlier video may indicate one viral spike"),
        "bot_avg_ratio":      ("Bottom-to-Average Views",       "Very low floor relative to average = suspicious"),
        "view_like_corr":     ("View-Like Correlation",         "Low correlation signals decoupled engagement (bots)"),
        "log_avg_views":      ("Avg Views per Video",           "Per-video average reach"),
        "log_avg_likes":      ("Avg Likes per Video",           "Per-video average likes"),
        "log_avg_comments":   ("Avg Comments per Video",        "Per-video average comments"),
        "vps_anomaly":        ("Views-per-Sub Anomaly",         "Extreme values flag paid engagement"),
        "engagement_score":   ("Overall Engagement Score",      "Combined likes+comments vs views"),
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


# ──────────────────────── main public API ───────────────────────────────────

def analyze(channel_input: str, api_key: str = None) -> dict:
    """
    Full pipeline: fetch → features → predict (pure Python LR) → explain.

    Returns:
      {
        "channel":  { title, handle, subscriber_count, total_views, video_count, thumbnail },
        "verdict":  { label, color, icon, score, prob_bot },
        "features": [ { name, label, description, value, shap, direction }, ... ],
        "top_red_flags": [ ... ],          # top 5 bot-signal features
        "top_clean_signals": [ ... ],      # top 5 clean-signal features
        "raw_features": { name: value },
        "meta": { n_videos_analyzed, n_comments_analyzed },
        "error": None | "string"
      }
    """
    # ── fetch data ───────────────────────────────────────────────────────────
    data = fetch_all_data(channel_input, api_key=api_key)
    if not data:
        return {"error": f"Could not fetch data for: {channel_input}"}

    # ── extract features ─────────────────────────────────────────────────────
    raw_feats = extract_features(data)

    # ── predict using pure Python Logistic Regression ────────────────────────
    z = LR_INTERCEPT
    for i, fname in enumerate(FEATURE_NAMES):
        val = raw_feats[fname]
        mean = LR_MEANS[i]
        std = LR_STDS[i] if LR_STDS[i] > 1e-9 else 1.0
        scaled = (val - mean) / std
        z += scaled * LR_COEFFS[i]

    try:
        prob_bot = 1.0 / (1.0 + math.exp(-z))
    except OverflowError:
        prob_bot = 0.0 if z < 0 else 1.0

    verdict  = _risk_tier(prob_bot)
    verdict["prob_bot"] = round(prob_bot, 4)

    # ── Dynamic Proxy SHAP / Feature Contribution ───────────────────────────
    shap_vals = []
    for i, fname in enumerate(FEATURE_NAMES):
        val = raw_feats[fname]
        importance = FEATURE_IMPORTANCES.get(fname, 0.04)
        o_mean = ORGANIC_MEANS.get(fname, 0.0)
        b_mean = BOT_MEANS.get(fname, 0.0)
        
        # Calculate how much this value aligns with organic vs. bot distributions
        diff_organic = abs(val - o_mean)
        diff_bot = abs(val - b_mean)
        
        # Add normalization factor
        norm = max(abs(o_mean - b_mean), 1e-5)
        
        # Positive local contribution pushes toward bot, negative pushes toward organic
        if b_mean > o_mean:
            raw_contrib = (val - o_mean) / norm
        else:
            raw_contrib = (o_mean - val) / norm
            
        # Bound contribution and scale with feature importance
        contrib = max(-2.5, min(2.5, raw_contrib)) * importance * 2.0
        shap_vals.append(contrib)

    feature_cards = []
    for i, fname in enumerate(FEATURE_NAMES):
        card = _human_feature(fname, raw_feats[fname], shap_vals[i])
        feature_cards.append(card)

    # Sort by absolute SHAP impact
    feature_cards.sort(key=lambda c: abs(c["shap"]), reverse=True)

    top_red    = [c for c in feature_cards if c["shap"] > 0][:5]
    top_clean  = [c for c in feature_cards if c["shap"] < 0][:5]

    # ── channel summary ──────────────────────────────────────────────────────
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
        "channel":          channel_info,
        "verdict":          verdict,
        "features":         feature_cards,
        "top_red_flags":    top_red,
        "top_clean_signals":top_clean,
        "raw_features":     raw_feats,
        "meta": {
            "n_videos_analyzed":   len(data.get("videos", [])),
            "n_comments_analyzed": len(data.get("comments", [])),
        },
        "error": None,
    }


if __name__ == "__main__":
    # Quick CLI test
    import sys
    target = sys.argv[1] if len(sys.argv) > 1 else "@MrBeast"
    result = analyze(target)
    if result.get("error"):
        print("ERROR:", result["error"])
    else:
        ch = result["channel"]
        v  = result["verdict"]
        print(f"\n{'='*50}")
        print(f"Channel : {ch['title']} ({ch['handle']})")
        print(f"Subs    : {ch['subscriber_count']:,}")
        print(f"Verdict : {v['icon']} {v['label']}  (Authenticity: {v['score']}%)")
        print(f"Bot Prob: {v['prob_bot']*100:.1f}%")
        print(f"\nTop Red Flags:")
        for c in result["top_red_flags"]:
            print(f"  • {c['label']}: {c['value']:.4f}  (SHAP +{c['shap']:.4f})")
        print(f"\nTop Clean Signals:")
        for c in result["top_clean_signals"]:
            print(f"  • {c['label']}: {c['value']:.4f}  (SHAP {c['shap']:.4f})")
        print('='*50)
