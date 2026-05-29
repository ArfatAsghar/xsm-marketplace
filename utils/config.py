"""utils/config.py — centralised config values used across the app."""
import os

FIREBASE_API_KEY   = os.getenv("FIREBASE_API_KEY", "")
GMAIL_USER         = os.getenv("GMAIL_USER", "")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "")
PLATFORM_FEE_PCT   = float(os.getenv("PLATFORM_FEE_PCT", "8"))

ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif"}

BOOST_COST  = {"24h": 1,  "48h": 2,  "10d": 5}
BOOST_HOURS = {"24h": 24, "48h": 48, "10d": 240}

REPORT_REASONS = [
    "Fake / Fraudulent listing",
    "Wrong platform or stats",
    "Seller is unresponsive",
    "Duplicate listing",
    "Inappropriate content",
    "Scam / already sold",
    "Other",
]
AUTO_FLAG_THRESHOLD = 3
