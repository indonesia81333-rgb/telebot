import os

API_ID = int(os.environ.get("API_ID", "0"))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

# ---- IDrive e2 (S3 compatible) ----
# Endpoint looks like: https://xxxx.va.idrivee2-xx.com  (IDrive e2 dashboard -> Buckets/Endpoint)
E2_ENDPOINT = os.environ.get("E2_ENDPOINT", "").strip().rstrip("/")
E2_ACCESS_KEY = os.environ.get("E2_ACCESS_KEY", "").strip()
E2_SECRET_KEY = os.environ.get("E2_SECRET_KEY", "").strip()
E2_BUCKET = os.environ.get("E2_BUCKET", "").strip()
E2_REGION = os.environ.get("E2_REGION", "us-east-1").strip()
# Optional folder (key prefix) inside the bucket, e.g. "telegram/"
E2_PREFIX = os.environ.get("E2_PREFIX", "").strip().lstrip("/")
# Link lifetime in days (S3 presigned links allow at most 7 days)
LINK_EXPIRY_DAYS = max(1, min(int(os.environ.get("LINK_EXPIRY_DAYS", "7")), 7))

# Comma separated Telegram user IDs allowed to use the bot. Empty = everyone.
ALLOWED_USERS = {
    int(x) for x in os.environ.get("ALLOWED_USERS", "").split(",") if x.strip()
}

PORT = int(os.environ.get("PORT", "10000"))  # Render provides PORT
MAX_FILE_SIZE = int(os.environ.get("MAX_FILE_SIZE_GB", "4")) * 1024**3
MAX_PARALLEL = int(os.environ.get("MAX_PARALLEL", "2"))
DOWNLOAD_DIR = os.environ.get("DOWNLOAD_DIR", "downloads")


def e2_configured():
    return all([E2_ENDPOINT, E2_ACCESS_KEY, E2_SECRET_KEY, E2_BUCKET])
