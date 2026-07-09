"""
analyzer.py
Loads the trained Random Forest model and runs end-to-end inference:
  fetch → features → predict → proxy SHAP explain → structured report
"""

import os
import json
import math
import numpy as np
import joblib

from feature_engineering import extract_features, FEATURE_NAMES
from youtube_fetcher import fetch_all_data

MODEL_DIR  = os.path.join(os.path.dirname(__file__), "model")
MODEL_PATH = os.path.join(MODEL_DIR, "xgb_model.joblib")
META_PATH  = os.path.join(MODEL_DIR, "model_meta.json")


# ─────────────────── risk tier helpers ──────────────────────────────────────

def _risk_tier(prob_bot: float) -> dict:
    if prob_bot < 0.25:
        return {"label": "GENUINE",       "color": "#22c55e", "icon": "✅", "score": round((1 - prob_bot) * 100)}
    elif prob_bot < 0.50:
        return {"label": "MOSTLY REAL",   "color": "#84cc16", "icon": "🟡", "score": round((1 - prob_bot) * 100)}
    elif prob_bot < 0.70:
        return {"label": "SUSPICIOUS",    "color": "#f59e0b", "icon": "⚠️",  "score": round((1 - prob_bot) * 100)}
    elif prob_bot < 0.85:
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
    Full pipeline: fetch → features → predict → explain.

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
    # ── load model ───────────────────────────────────────────────────────────
    if not os.path.exists(MODEL_PATH) or not os.path.exists(META_PATH):
        return {"error": "Model files not found. Please run train_model.py first."}

    model = joblib.load(MODEL_PATH)
    with open(META_PATH, "r") as fp:
        meta_info = json.load(fp)

    # ── fetch data ───────────────────────────────────────────────────────────
    data = fetch_all_data(channel_input, api_key=api_key)
    if not data:
        return {"error": f"Could not fetch data for: {channel_input}"}

    # ── extract features ─────────────────────────────────────────────────────
    raw_feats = extract_features(data)
    X = np.array([[raw_feats[f] for f in FEATURE_NAMES]], dtype=np.float32)

    # ── predict ──────────────────────────────────────────────────────────────
    prob_bot = float(model.predict_proba(X)[0][1])
    verdict  = _risk_tier(prob_bot)
    verdict["prob_bot"] = round(prob_bot, 4)

    # ── Dynamic Proxy SHAP / Feature Contribution ───────────────────────────
    # We estimate local feature impact using feature importances combined with
    # deviation from the training distributions.
    feature_importances = meta_info.get("feature_importances", {})
    organic_means = meta_info.get("organic_means", {})
    bot_means = meta_info.get("bot_means", {})

    shap_vals = []
    for i, fname in enumerate(FEATURE_NAMES):
        val = raw_feats[fname]
        importance = feature_importances.get(fname, 0.04)
        o_mean = organic_means.get(fname, 0.0)
        b_mean = bot_means.get(fname, 0.0)
        
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
        contrib = np.clip(raw_contrib, -2.5, 2.5) * importance * 2.0
        shap_vals.append(float(contrib))

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
