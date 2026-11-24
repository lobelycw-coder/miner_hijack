"""Snapshot SQLite to S3-compatible storage. Disabled unless S3_* is fully set.

db.py registers the snapshot callable, so this module never imports db and the
circular import is gone. Every entry point is wrapped: a backup failure can
never take the site down.
"""
import asyncio, gzip, os, tempfile, time

ENDPOINT = os.environ.get("S3_ENDPOINT", "").strip()
BUCKET = os.environ.get("S3_BUCKET", "").strip()
KEY = os.environ.get("S3_ACCESS_KEY", "").strip()
SECRET = os.environ.get("S3_SECRET_KEY", "").strip()
REGION = os.environ.get("S3_REGION", "auto").strip() or "auto"
PREFIX = os.environ.get("S3_PREFIX", "backups").strip() or "backups"
DB_PATH = os.path.join(os.environ.get("DATA_DIR", "./data"), "scroof.db")

_snapshot = None
_log = None

def enabled() -> bool:
    return bool(ENDPOINT and BUCKET and KEY and SECRET)

def configure(snapshot_fn, log_fn):
    global _snapshot, _log
    _snapshot, _log = snapshot_fn, log_fn

def _client():
    import boto3
    return boto3.client("s3", endpoint_url=ENDPOINT, region_name=REGION,
                        aws_access_key_id=KEY, aws_secret_access_key=SECRET)

def _keys(c):
    out = []
    pag = c.get_paginator("list_objects_v2")
    for page in pag.paginate(Bucket=BUCKET, Prefix=PREFIX + "/"):
        for obj in page.get("Contents", []):
            out.append((obj["LastModified"], obj["Key"]))
    out.sort()
    return out

def restore_if_missing() -> bool:
    if not enabled() or os.path.exists(DB_PATH):
        return False
    try:
        c = _client()
        keys = _keys(c)
        if not keys:
            return False
        os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
        gz = DB_PATH + ".gz"
        c.download_file(BUCKET, keys[-1][1], gz)
        with gzip.open(gz, "rb") as g, open(DB_PATH, "wb") as f:
            f.write(g.read())
        os.unlink(gz)
        return True
    except Exception:
        return False

def backup_once():
    if not enabled() or _snapshot is None:
        return None
    tmp = None
    try:
        tmp = tempfile.mkdtemp(prefix="scroof-")
        raw = os.path.join(tmp, "snap.db")
        _snapshot(raw)
        gz = raw + ".gz"
        with open(raw, "rb") as f, gzip.open(gz, "wb", compresslevel=6) as g:
            g.write(f.read())
        c = _client()
        key = f"{PREFIX}/{int(time.time())}.db.gz"
        c.upload_file(gz, BUCKET, key)
        for _, k in _keys(c)[:-48]:
            c.delete_object(Bucket=BUCKET, Key=k)
        return key
    except Exception as e:
        if _log:
            try:
                _log(f"backup fail {type(e).__name__}", lvl="warn")
            except Exception:
                pass
        return None
    finally:
        if tmp and os.path.isdir(tmp):
            for n in os.listdir(tmp):
                try:
                    os.unlink(os.path.join(tmp, n))
                except OSError:
                    pass
            try:
                os.rmdir(tmp)
            except OSError:
                pass

async def loop(interval: int = 180):
    while True:
        await asyncio.sleep(interval)
        await asyncio.to_thread(backup_once)