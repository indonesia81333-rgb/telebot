"""IDrive e2 (S3 compatible) storage helper. boto3 is blocking, so every call
runs in a worker thread; progress callbacks therefore only store numbers."""
import asyncio
import mimetypes
import os
import re
import uuid

import boto3
from boto3.s3.transfer import TransferConfig
from botocore.config import Config

import config

# 64 MB parts with parallel threads = fast, high quality large-file transfers.
TRANSFER = TransferConfig(
    multipart_threshold=64 * 1024**2,
    multipart_chunksize=64 * 1024**2,
    max_concurrency=8,
    use_threads=True,
)


class E2:
    def __init__(self):
        self._client = None

    @property
    def client(self):
        if not config.e2_configured():
            raise RuntimeError(
                "IDrive e2 is not configured. Set E2_ENDPOINT, E2_ACCESS_KEY, "
                "E2_SECRET_KEY and E2_BUCKET."
            )
        if self._client is None:
            self._client = boto3.client(
                "s3",
                endpoint_url=config.E2_ENDPOINT,
                aws_access_key_id=config.E2_ACCESS_KEY,
                aws_secret_access_key=config.E2_SECRET_KEY,
                region_name=config.E2_REGION,
                config=Config(
                    signature_version="s3v4",
                    s3={"addressing_style": "path"},
                    retries={"max_attempts": 5, "mode": "standard"},
                    max_pool_connections=32,
                ),
            )
        return self._client

    @staticmethod
    def clean_name(name):
        return re.sub(r"[^\w.\- ()\[\]]+", "_", name).strip() or "file"

    def _make_key(self, name, prefix):
        prefix = (prefix if prefix is not None else config.E2_PREFIX).strip("/")
        base = f"{uuid.uuid4().hex[:8]}/{self.clean_name(name)}"
        return f"{prefix}/{base}" if prefix else base

    def link(self, key, download=False):
        params = {"Bucket": config.E2_BUCKET, "Key": key}
        if download:
            fname = os.path.basename(key)
            params["ResponseContentDisposition"] = f'attachment; filename="{fname}"'
        return self.client.generate_presigned_url(
            "get_object", Params=params, ExpiresIn=config.LINK_EXPIRY_DAYS * 86400
        )

    def _upload_sync(self, path, key, callback):
        extra = {}
        ctype = mimetypes.guess_type(path)[0]
        if ctype:
            extra["ContentType"] = ctype
        self.client.upload_file(
            path, config.E2_BUCKET, key, ExtraArgs=extra or None,
            Config=TRANSFER, Callback=callback,
        )

    async def upload(self, path, progress=None, prefix=None):
        """Upload a local file. progress(bytes_just_sent) is called from worker threads."""
        key = self._make_key(os.path.basename(path), prefix)
        await asyncio.to_thread(self._upload_sync, path, key, progress)
        return {
            "key": key,
            "name": os.path.basename(path),
            "share_link": self.link(key),
            "download_link": self.link(key, download=True),
        }

    async def download(self, key, dest_path, progress=None):
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        await asyncio.to_thread(
            self.client.download_file, config.E2_BUCKET, key, dest_path,
            Config=TRANSFER, Callback=progress,
        )
        return dest_path

    async def size(self, key):
        r = await asyncio.to_thread(self.client.head_object, Bucket=config.E2_BUCKET, Key=key)
        return r["ContentLength"]

    async def test(self):
        """Returns a list of step dicts: head bucket, write, presign, delete."""
        steps = []
        c = self.client
        try:
            await asyncio.to_thread(c.head_bucket, Bucket=config.E2_BUCKET)
            steps.append({"step": f"Open bucket '{config.E2_BUCKET}'", "ok": True, "detail": "OK"})
        except Exception as e:
            steps.append({"step": f"Open bucket '{config.E2_BUCKET}'", "ok": False, "detail": _err(e)})
            return steps
        key = self._make_key("connection-test.txt", "")
        try:
            await asyncio.to_thread(
                c.put_object, Bucket=config.E2_BUCKET, Key=key, Body=b"IDrive e2 connection test"
            )
            steps.append({"step": "Write test file", "ok": True, "detail": key})
        except Exception as e:
            steps.append({"step": "Write test file", "ok": False, "detail": _err(e)})
            return steps
        try:
            self.link(key)
            steps.append({"step": "Create share link", "ok": True, "detail": f"valid {config.LINK_EXPIRY_DAYS} day(s)"})
        except Exception as e:
            steps.append({"step": "Create share link", "ok": False, "detail": _err(e)})
        try:
            await asyncio.to_thread(c.delete_object, Bucket=config.E2_BUCKET, Key=key)
            steps.append({"step": "Delete test file", "ok": True, "detail": "cleaned up"})
        except Exception as e:
            steps.append({"step": "Delete test file", "ok": False, "detail": _err(e)})
        return steps


def _err(e):
    resp = getattr(e, "response", None)
    if resp:
        err = resp.get("Error", {})
        return f"{err.get('Code')}: {err.get('Message')}"
    return repr(e)


def friendly_error(e):
    text = _err(e)
    if "InvalidAccessKeyId" in text or "SignatureDoesNotMatch" in text:
        return f"IDrive e2 rejected the keys ({text}). Check E2_ACCESS_KEY and E2_SECRET_KEY."
    if "NoSuchBucket" in text:
        return f"Bucket not found ({text}). Check E2_BUCKET and E2_ENDPOINT."
    if "AccessDenied" in text or "403" in text:
        return f"Access denied ({text}). The key needs read/write permission on the bucket."
    return text
