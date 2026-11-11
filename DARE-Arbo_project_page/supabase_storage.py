"""Private Supabase Storage and one-time OneDrive migration helpers."""
from datetime import datetime, timezone
from hashlib import sha256
import json
from urllib.parse import quote

import httpx


MAX_UPLOAD_BYTES = 50 * 1024 * 1024
MIGRATION_MARKER = "_migration/onedrive_copy.json"


class SupabaseStorageError(RuntimeError):
    """Sanitized error for private storage operations."""


def configured(config):
    return bool(config.get("url") and config.get("secret_key") and config.get("bucket"))


class SupabaseStorage:
    def __init__(self, config):
        if not configured(config):
            raise SupabaseStorageError("Supabase private storage is not configured.")
        self.base_url = str(config["url"]).rstrip("/")
        self.key = str(config["secret_key"])
        self.bucket = str(config["bucket"])
        self.headers = {"apikey": self.key, "Authorization": "Bearer " + self.key}

    def _request(self, method, path, *, content=None, json_data=None, headers=None, allowed=(200, 201, 204)):
        try:
            response = httpx.request(method, self.base_url + path,
                headers={**self.headers, **(headers or {})}, content=content,
                json=json_data, timeout=90)
        except httpx.HTTPError:
            raise SupabaseStorageError("Supabase Storage could not be reached.") from None
        if response.status_code not in allowed:
            raise SupabaseStorageError(f"Supabase Storage request failed ({response.status_code}).")
        return response

    def ensure_private_bucket(self):
        path = "/storage/v1/bucket/" + quote(self.bucket, safe="")
        try:
            response = self._request("GET", path, allowed=(200,))
        except SupabaseStorageError as exc:
            if "(404)" not in str(exc):
                raise
            created = self._request("POST", "/storage/v1/bucket", json_data={
                "id": self.bucket, "name": self.bucket, "public": False,
                "file_size_limit": MAX_UPLOAD_BYTES,
            }, allowed=(200, 201, 409))
            if created.status_code == 409:
                response = self._request("GET", path, allowed=(200,))
            else:
                response = created
        try:
            metadata = response.json()
        except ValueError:
            metadata = {}
        if metadata.get("public") is True:
            raise SupabaseStorageError("The configured Supabase bucket is public. Use a private bucket before migrating files.")
        return metadata

    def _object_path(self, path):
        path = str(path).replace("\\", "/").strip("/")
        if not path or any(part in ("", ".", "..") for part in path.split("/")):
            raise ValueError("Invalid project storage path.")
        return "/storage/v1/object/" + quote(self.bucket, safe="") + "/" + quote(path, safe="/")

    def upload(self, path, content, mime_type="application/octet-stream", upsert=True):
        if len(content) > MAX_UPLOAD_BYTES:
            raise ValueError("Files must be no larger than 50 MB.")
        response = self._request("POST", self._object_path(path), content=content,
            headers={"Content-Type": mime_type, "x-upsert": "true" if upsert else "false"},
            allowed=(200, 201))
        return response.json() if response.content else {}

    def download(self, path):
        return self._request("GET", self._object_path(path), allowed=(200,)).content

    def list_objects(self, prefix="", limit=1000):
        response = self._request("POST", "/storage/v1/object/list/" + quote(self.bucket, safe=""),
            json_data={"prefix": str(prefix).strip("/"), "limit": min(max(int(limit), 1), 1000),
                       "offset": 0, "sortBy": {"column": "name", "order": "asc"}}, allowed=(200,))
        return response.json()

    def copy_onedrive_tree(self, source, root_folder_id, root_name="DARE-Arbo", progress=None):
        """Copy a full OneDrive folder tree and verify every copied object's SHA-256."""
        self.ensure_private_bucket()
        entries = []

        def scan(folder_id, relative):
            for item in source.list_children(folder_id):
                name = str(item.get("name") or "").replace("\\", "/").strip("/")
                if not name or "/" in name or name in (".", ".."):
                    raise SupabaseStorageError("A OneDrive item has an unsupported filename; no migration marker was written.")
                path = relative + "/" + name
                if item.get("folder"):
                    scan(item["id"], path)
                elif item.get("file"):
                    size = int(item.get("size") or 0)
                    if size > MAX_UPLOAD_BYTES:
                        raise SupabaseStorageError(f"{name} exceeds the 50 MB Supabase Storage upload limit.")
                    content = source.download(item["id"])
                    digest = sha256(content).hexdigest()
                    if size and len(content) != size:
                        raise SupabaseStorageError(f"The source size for {name} changed during migration.")
                    mime = item.get("file", {}).get("mimeType") or "application/octet-stream"
                    self.upload(path, content, mime, upsert=True)
                    copied = self.download(path)
                    if sha256(copied).hexdigest() != digest:
                        raise SupabaseStorageError(f"Checksum verification failed for {name}.")
                    entries.append({"path": path, "size": len(content), "sha256": digest})
                    if progress:
                        progress(len(entries), name)

        scan(root_folder_id, root_name)
        if not entries:
            raise SupabaseStorageError("No files were found in the OneDrive project folder.")
        manifest = {
            "source": "OneDrive project folder",
            "copied_at": datetime.now(timezone.utc).isoformat(),
            "file_count": len(entries),
            "files": entries,
        }
        self.upload(MIGRATION_MARKER, json.dumps(manifest, indent=2).encode("utf-8"),
                    "application/json", upsert=True)
        return manifest
