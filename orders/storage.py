"""Supabase Storage helpers. Files live in a PRIVATE bucket/folder (Supabase Storage
in production, a private local directory when Supabase is not configured);
order_items.file_url stores the object path, and operators get short-lived signed URLs.

Supabase Storage REST API:
  upload:     POST {SUPABASE_URL}/storage/v1/object/{bucket}/{path}
  signed url: POST {SUPABASE_URL}/storage/v1/object/sign/{bucket}/{path}  body {"expiresIn": seconds}
  delete:     DELETE {SUPABASE_URL}/storage/v1/object/{bucket}  body {"prefixes": [path, ...]}
Headers: Authorization: Bearer {SUPABASE_SERVICE_KEY}
"""
import json
import logging
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from django.conf import settings

logger = logging.getLogger(__name__)


class StorageError(Exception):
    pass


class LocalBackend:
    def __init__(self, root: Path):
        self.root = Path(root)

    def _resolve(self, object_path: str) -> Path:
        p = (self.root / object_path).resolve()
        root = self.root.resolve()
        if p != root and root not in p.parents:
            raise StorageError(f"Path escapes storage root: {object_path}")
        return p

    def upload(self, fileobj, object_path: str) -> None:
        p = self._resolve(object_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        if hasattr(fileobj, "seek"):
            fileobj.seek(0)
        with open(p, "wb") as out:
            if hasattr(fileobj, "chunks"):
                for chunk in fileobj.chunks():
                    out.write(chunk)
            else:
                out.write(fileobj.read())

    def open(self, object_path: str):
        p = self._resolve(object_path)
        if not p.is_file():
            raise StorageError(f"File not found: {object_path}")
        return open(p, "rb")

    def delete(self, object_paths: list[str]) -> None:
        for path in object_paths:
            p = self._resolve(path)
            p.unlink(missing_ok=True)

    def signed_url(self, object_path: str, download_name: str | None = None) -> None:
        return None


class SupabaseBackend:
    def __init__(self, url: str, service_key: str, bucket: str, ttl: int):
        self.url = url.rstrip("/")
        self.key = service_key
        self.bucket = bucket
        self.ttl = ttl

    def _request(
        self,
        method: str,
        url: str,
        data: bytes | None = None,
        content_type: str | None = None,
        extra_headers: dict | None = None,
    ) -> bytes:
        headers = {"Authorization": f"Bearer {self.key}", "apikey": self.key}
        if content_type:
            headers["Content-Type"] = content_type
        if extra_headers:
            headers.update(extra_headers)
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            raise StorageError(f"Supabase {method} failed: HTTP {e.code}") from e
        except urllib.error.URLError as e:
            raise StorageError(f"Supabase {method} failed: {e.reason}") from e

    def _object_url(self, object_path: str) -> str:
        return f"{self.url}/storage/v1/object/{self.bucket}/{urllib.parse.quote(object_path)}"

    def upload(self, fileobj, object_path: str) -> None:
        if hasattr(fileobj, "seek"):
            fileobj.seek(0)
        if hasattr(fileobj, "chunks"):
            data = b"".join(fileobj.chunks())
        else:
            data = fileobj.read()
        self._request(
            "POST",
            self._object_url(object_path),
            data=data,
            content_type="application/pdf",
            extra_headers={"x-upsert": "false"},
        )

    def open(self, object_path: str):
        raise StorageError("Supabase files are served via signed URLs")

    def delete(self, object_paths: list[str]) -> None:
        if not object_paths:
            return
        self._request(
            "DELETE",
            f"{self.url}/storage/v1/object/{self.bucket}",
            data=json.dumps({"prefixes": list(object_paths)}).encode(),
            content_type="application/json",
        )

    def signed_url(self, object_path: str, download_name: str | None = None) -> str:
        body = self._request(
            "POST",
            f"{self.url}/storage/v1/object/sign/{self.bucket}/{urllib.parse.quote(object_path)}",
            data=json.dumps({"expiresIn": self.ttl}).encode(),
            content_type="application/json",
        )
        try:
            signed = json.loads(body)["signedURL"]  # relative, e.g. "/object/sign/print-files/uploads/1/x.pdf?token=abc"
        except (ValueError, KeyError, TypeError) as e:
            raise StorageError("Supabase sign response was invalid") from e
        url = f"{self.url}/storage/v1{signed}"
        if download_name:
            url += f"&download={urllib.parse.quote(download_name)}"
        return url


def get_backend():
    if settings.SUPABASE_URL and settings.SUPABASE_SERVICE_KEY:
        return SupabaseBackend(
            settings.SUPABASE_URL,
            settings.SUPABASE_SERVICE_KEY,
            settings.SUPABASE_BUCKET,
            settings.SIGNED_URL_TTL_SECONDS,
        )
    return LocalBackend(Path(settings.PRIVATE_MEDIA_ROOT))


def build_object_path(user_id: int) -> str:
    return f"uploads/{user_id}/{uuid.uuid4().hex}.pdf"


def upload_file(fileobj, object_path: str) -> str:
    get_backend().upload(fileobj, object_path)
    return object_path


def delete_files(object_paths: list[str]) -> None:
    try:
        get_backend().delete(list(object_paths))
    except (StorageError, OSError):
        logger.exception("Could not delete files %s", object_paths)
