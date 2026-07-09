"""
train_model.py
Bootstraps synthetic YouTube channel data, trains a scikit-learn RandomForestClassifier,
and saves the model + metadata. No external heavy dependencies (no xgboost, no shap, no matplotlib).

Run:
    python train_model.py
"""

import os
import json
import math
import random
# Zero-dependency imports at module level

from feature_engineering import extract_features, FEATURE_NAMES

# ── output directory ─────────────────────────────────────────────────────────
MODEL_DIR = os.path.join(os.path.dirname(__file__), "model")
os.makedirs(MODEL_DIR, exist_ok=True)


# ─────────────────────── synthetic data generators ──────────────────────────

def _make_organic_channel(rng: random.Random) -> dict:
    subs = rng.randint(500, 2_000_000)
    age  = rng.randint(90, 3000)
    n    = 30
    mean_views = subs * rng.uniform(0.05, 0.5)

    videos = []
    for _ in range(n):
        v = max(10, int(rng.lognormvariate(math.log(max(1, mean_views)), 0.5)))
        l = max(0, int(v * rng.uniform(0.02, 0.10)))
        c = max(0, int(v * rng.uniform(0.001, 0.015)))
        videos.append({"views": v, "likes": l, "comments": c, "comments_disabled": False})

    comments = [
        rng.choice(["great video!", "love this", "very helpful!", "amazing content",
                    "keep it up", "so informative", "just subscribed", "wow!"])
        for _ in range(rng.randint(20, 50))
    ]
    comments += [
        "".join(rng.choices("abcdefghijklmnopqrstuvwxyz ", k=rng.randint(10, 60)))
        for _ in range(rng.randint(10, 30))
    ]

    return {
        "channel": {
            "subscriber_count": subs,
            "total_views": subs * rng.randint(30, 200),
            "video_count": rng.randint(20, 400),
            "published_at": f"20{20 - age // 365:02d}-01-01T00:00:00Z",
            "hidden_subscriber_count": False,
        },
        "videos": videos,
        "comments": comments,
    }


def _make_bot_channel(rng: random.Random, bot_type: str = None) -> dict:
    subs = rng.randint(1000, 800_000)
    age  = rng.randint(7, 500)
    n    = 30
    bot_type = bot_type or rng.choice(
        ["dead_subs", "fake_engagement", "flat_views", "spam_comments"]
    )

    spam_comments = [
        "sub to my channel!", "free money in bio", "click here to earn",
        "check my profile", "earn $500/day at home", "visit my site now",
        "sub4sub anyone?", "bit.ly/freecash", "follow back please!"
    ]
    organic_short = ["nice", "ok", "good", "ok video", "cool"]

    if bot_type == "dead_subs":
        mean_views = subs * rng.uniform(0.0001, 0.002)
        videos = []
        for _ in range(n):
            v = max(1, int(rng.uniform(mean_views * 0.5, mean_views * 1.5)))
            l = max(0, int(v * rng.uniform(0.005, 0.02)))
            c = rng.randint(0, 3)
            videos.append({"views": v, "likes": l, "comments": c, "comments_disabled": False})
        comments = [rng.choice(organic_short) for _ in range(rng.randint(0, 8))]

    elif bot_type == "fake_engagement":
        mean_views = subs * rng.uniform(0.5, 5.0)
        videos = []
        for _ in range(n):
            v = max(1, int(rng.uniform(mean_views * 0.9, mean_views * 1.1)))
            l = max(0, int(v * rng.uniform(0.00005, 0.001)))  # near-zero likes
            c = max(0, int(v * rng.uniform(0.000005, 0.0002)))
            videos.append({"views": v, "likes": l, "comments": c, "comments_disabled": False})
        comments = [rng.choice(spam_comments) if rng.random() > 0.3 else rng.choice(organic_short)
                    for _ in range(rng.randint(5, 30))]

    elif bot_type == "flat_views":
        base = int(subs * rng.uniform(0.05, 0.2))
        videos = []
        for _ in range(n):
            v = max(1, int(rng.normalvariate(base, base * 0.003)))  # near-zero variance
            l = int(v * 0.04)
            c = int(v * 0.002)
            videos.append({"views": v, "likes": l, "comments": c, "comments_disabled": False})
        comments = [rng.choice(organic_short) for _ in range(rng.randint(5, 20))]

    else:  # spam_comments
        mean_views = subs * rng.uniform(0.02, 0.3)
        videos = []
        for _ in range(n):
            v = max(10, int(rng.lognormvariate(math.log(max(1, mean_views)), 0.4)))
            l = max(0, int(v * rng.uniform(0.01, 0.05)))
            c = max(0, int(v * rng.uniform(0.002, 0.02)))
            videos.append({"views": v, "likes": l, "comments": c, "comments_disabled": False})
        comments = [rng.choice(spam_comments) for _ in range(rng.randint(25, 50))]

    return {
        "channel": {
            "subscriber_count": subs,
            "total_views": subs * rng.randint(1, 30),
            "video_count": rng.randint(5, 200),
            "published_at": f"20{24 - age // 365:02d}-01-01T00:00:00Z",
            "hidden_subscriber_count": rng.random() > 0.5,
        },
        "videos": videos,
        "comments": comments,
    }


def generate_dataset(n_organic=1500, n_bot=1500, seed=42):
    rng = random.Random(seed)
    records = []
    labels  = []

    for _ in range(n_organic):
        data = _make_organic_channel(rng)
        feats = extract_features(data)
        records.append([feats[f] for f in FEATURE_NAMES])
        labels.append(0)

    for _ in range(n_bot):
        data = _make_bot_channel(rng)
        feats = extract_features(data)
        records.append([feats[f] for f in FEATURE_NAMES])
        labels.append(1)

    try:
        import numpy as np
        return np.array(records, dtype=np.float32), np.array(labels, dtype=np.int32)
    except ImportError:
        return records, labels


# ─────────────────────────── training ────────────────────────────────────────

def train(n_organic=1500, n_bot=1500):
    print("[TRAIN] Generating synthetic dataset...")
    X, y = generate_dataset(n_organic, n_bot)

    from sklearn.ensemble import RandomForestClassifier
    print("[TRAIN] Fitting RandomForestClassifier...")
    model = RandomForestClassifier(
        n_estimators=150,
        max_depth=8,
        random_state=42,
        n_jobs=-1
    )
    model.fit(X, y)

    import joblib
    model_path = os.path.join(MODEL_DIR, "xgb_model.joblib")
    joblib.dump(model, model_path)
    print(f"[TRAIN] Model saved -> {model_path}")

    # ── Calculate feature importances and profile means ─────────────────────
    importances = model.feature_importances_
    
    try:
        import numpy as np
        organic_idx = (y == 0)
        bot_idx = (y == 1)
        organic_means = X[organic_idx].mean(axis=0)
        bot_means = X[bot_idx].mean(axis=0)
    except ImportError:
        # Fallback if numpy is not present
        organic_rows = [X[i] for i, label in enumerate(y) if label == 0]
        bot_rows = [X[i] for i, label in enumerate(y) if label == 1]
        organic_means = [sum(col) / len(col) for col in zip(*organic_rows)]
        bot_means = [sum(col) / len(col) for col in zip(*bot_rows)]

    importance_list = sorted(
        zip(FEATURE_NAMES, importances.tolist()),
        key=lambda x: x[1], reverse=True
    )

    # ── save metadata ────────────────────────────────────────────────────────
    meta = {
        "n_features": len(FEATURE_NAMES),
        "feature_names": FEATURE_NAMES,
        "n_train_organic": n_organic,
        "n_train_bot": n_bot,
        "top_features": [f for f, _ in importance_list[:5]],
        "feature_importances": {f: round(v, 6) for f, v in importance_list},
        "organic_means": {f: float(organic_means[i]) for i, f in enumerate(FEATURE_NAMES)},
        "bot_means": {f: float(bot_means[i]) for i, f in enumerate(FEATURE_NAMES)},
    }
    meta_path = os.path.join(MODEL_DIR, "model_meta.json")
    with open(meta_path, "w") as fp:
        json.dump(meta, fp, indent=2)
    print(f"[TRAIN] Meta saved -> {meta_path}")

    print("\n[OK] Training complete!")
    return model, importance_list


if __name__ == "__main__":
    train()
