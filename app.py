
import json
import os
import secrets
from pathlib import Path
from flask import Flask
from dotenv import load_dotenv
import firebase_admin
from firebase_admin import credentials

BASE_DIR = Path(__file__).resolve().parent
IS_VERCEL = bool(os.getenv("VERCEL") or os.getenv("VERCEL_ENV"))

load_dotenv(dotenv_path=BASE_DIR / ".env", override=True)

FIREBASE_CRED_PATH = os.getenv("FIREBASE_CRED_PATH", "cred.json")
FIREBASE_DB_URL    = os.getenv("FIREBASE_DB_URL")
FIREBASE_API_KEY   = os.getenv("FIREBASE_API_KEY")
GMAIL_USER         = os.getenv("GMAIL_USER")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")

if not FIREBASE_DB_URL:
    raise RuntimeError("Missing FIREBASE_DB_URL. Set it in your environment or .env file.")


def _load_firebase_credentials():
    firebase_cred_json = os.getenv("FIREBASE_CRED_JSON", "").strip()
    if firebase_cred_json:
        try:
            return credentials.Certificate(json.loads(firebase_cred_json))
        except json.JSONDecodeError as exc:
            raise RuntimeError("FIREBASE_CRED_JSON must contain valid JSON.") from exc

    cred_path = Path(FIREBASE_CRED_PATH)
    if not cred_path.is_absolute():
        cred_path = BASE_DIR / cred_path

    if cred_path.is_file():
        return credentials.Certificate(str(cred_path))

    raise RuntimeError(
        "Missing Firebase credentials. Set FIREBASE_CRED_JSON or provide cred.json locally."
    )


def _upload_dir(relative_name: str) -> str:
    root = Path("/tmp/xsm_market") / relative_name if IS_VERCEL else BASE_DIR / "static" / relative_name
    root.mkdir(parents=True, exist_ok=True)
    return str(root)


def create_app() -> Flask:
    app = Flask(
        __name__,
        template_folder=str(BASE_DIR / "templates"),
        static_folder=str(BASE_DIR / "static"),
    )
    import datetime
    app.secret_key = os.getenv("SECRET_KEY") or "xsm_market_fallback_secure_key_1293810238"
    app.config["PERMANENT_SESSION_LIFETIME"] = datetime.timedelta(days=7)

    # Import from extensions.py — avoids circular imports
    from extensions import cache, limiter
    cache.init_app(app, config={
        "CACHE_TYPE":            "SimpleCache",
        "CACHE_DEFAULT_TIMEOUT": 60,
    })
    limiter.init_app(app)

    if not firebase_admin._apps:
        cred = _load_firebase_credentials()
        firebase_admin.initialize_app(cred, {"databaseURL": FIREBASE_DB_URL})

    payment_slips = _upload_dir("payment_slips")
    profile_pics = _upload_dir("profile_pics")
    listing_media = _upload_dir("listing_media")
    kyc_docs = _upload_dir("kyc_docs")

    app.config.update(
        PAYMENT_SLIPS   = payment_slips,
        PROFILE_UPLOADS = profile_pics,
        LISTING_UPLOADS = listing_media,
        UPLOAD_FOLDER   = profile_pics,
        KYC_UPLOADS     = kyc_docs,
    )

    from routes.auth     import auth_bp
    from routes.listings import listings_bp
    from routes.wallet   import wallet_bp
    from routes.admin    import admin_bp
    from routes.chat     import chat_bp
    from routes.reports  import reports_bp
    from routes.misc     import misc_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(listings_bp)
    app.register_blueprint(wallet_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(chat_bp)
    app.register_blueprint(reports_bp)
    app.register_blueprint(misc_bp)

    from utils.filters import register_filters
    from utils.context import inject_globals
    register_filters(app)
    app.context_processor(inject_globals)

    return app


app = create_app()

# ── Google Search Console ownership verification ──────────────────────────────
from flask import Response as _Response

@app.route("/google52150855c235b99a.html")
def gsc_verify_old():
    return _Response(
        "google-site-verification: google52150855c235b99a.html",
        mimetype="text/html"
    )

@app.route("/google3fa371a423b58e26.html")
def gsc_verify_new():
    return _Response(
        "google-site-verification: google3fa371a423b58e26.html",
        mimetype="text/html"
    )
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app.run(debug=True)
