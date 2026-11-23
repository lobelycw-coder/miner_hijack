"""Snapshot the SQLite file to S3-compatible storage on each cycle, restore on boot.

Free Render disks are ephemeral; a redeploy wipes /data. This module uploads a
gzipped VACUUM INTO snapshot every cycle under a rolling key and pulls the newest
one back on startup before db.py opens the file. Disabled entirely when S3_* is unset.
"""
import asyncio, gzip, os, tempfile, time
from . import db

ENDPOINT = os.environ.get("S3_ENDPOINT", "").strip()
BUCKET = os.environ.get("S3_BUCKET", "").strip()
KEY = os.environ.get("S3_ACCESS_KEY", "").strip()
SECRET = os.environ.get("S3_SECRET_KEY", "").strip()
REGION = os.environ.get("S3_REGION", "auto").strip() or "auto"
PREFIX = os.environ.get("S3_PREFIX", "backups").strip() or "backups"
DB_PATH = os.path.join(os.environ.get("DATA_DIR", "./data"), "scroof.db")

def enabled() -> bool:
    return bool(ENDPOINT and BUCKET and KEY and SECRET)

def _client():
    import boto3
    return boto3.client(
        "s3", endpoint_url=ENDPOINT, region_name=REGION,
        aws_access_key_id=KEY, aws_secret_access_key=SECRET,
    )

def _newest_key(c) -> str | None:
    paginator = c.get_paginator("list_objects_v2")
    best = None
    for page in paginator.paginate(Bucket=BUCKET, Prefix=PREFIX + "/"):
        for obj in page.get("Contents", []):
            if best is None or obj["LastModified"] > best["LastModified"]:
                best = obj
    return best["Key"] if best else None

def _snapshot() -> str:
    tmp = tempfile.mkdtemp(prefix="scroof-")
    raw = os.path.join(tmp, "snap.db")
    db.run_void_into(raw)
    gz = raw + ".gz"
    with open(raw, "rb") as f, gzip.open(gz, "wb", compresslevel=6) as g:
        g.write(f.read())
    os.unlink(raw)
    return gz

def backup_once() -> str | None:
    if not enabled():
        return None
    try:
        c = _client()
        gz = _snapshot()
        key = f"{PREFIX}/{int(time.time())}.db.gz"
        c.upload_file(gz, BUCKET, key)
        os.unlink(gz)
        # keep the last 48 hourly snapshots
        keys = []
        paginator = c.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=BUCKET, Prefix=PREFIX + "/"):
            for obj in page.get("Contents", []):
                keys.append((obj["LastModified"], obj["Key"]))
        keys.sort()
        for _, k in keys[:-48]:
            c.delete_object(Bucket=BUCKET, Key=k)
        return key
    except Exception as e:
        db.log(f"backup fail {type(e).__name__}: {e}", lvl="warn")
        return None

def restore_if_missing() -> bool:
    """Pull newest snapshot when no local DB exists. Call BEFORE db module import."""
    if not enabled():
        return False
    if os.path.exists(DB_PATH):
        return False
    try:
        c = _client()
        key = _newest_key(c)
        if not key:
            return False
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        gz = DB_PATH + ".gz"
        c.download_file(BUCKET, key, gz)
        with gzip.open(gz, "rb") as g, open(DB_PATH, "wb") as f:
            f.write(g.read())
        os.unlink(gz)
        return True
    except Exception:
        return False

async def loop(interval: int = 120):
    while True:
        await asyncio.sleep(interval)
        await asyncio.to_thread(backup_once)