"""Private Supabase Storage helpers for local and OneDrive migration."""
from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO
import json
from pathlib import PurePosixPath
import zipfile
from urllib.parse import quote

import httpx


MAX_UPLOAD_BYTES = 50 * 1024 * 1024
MAX_ARCHIVE_BYTES = 250 * 1024 * 1024
MIGRATION_MARKER = "_migration/local_upload.json"
ALLOWED_EXTENSIONS = {".pdf", ".docx", ".xlsx", ".pptx", ".csv", ".tsv", ".txt", ".png", ".jpg", ".jpeg", ".html", ".json"}


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
        # New sb_secret_* keys are API keys, not JWTs. Keep compatibility with
        # legacy service_role JWTs while sending new keys only as `apikey`.
        self.headers = {"apikey": self.key}
        if self.key.startswith("eyJ"):
            self.headers["Authorization"] = "Bearer " + self.key

    def _request(self, method, path, *, content=None, json_data=None, headers=None, allowed=(200, 201, 204)):
        try:
            response = httpx.request(method, self.base_url + path,
                headers={**self.headers, **(headers or {})}, content=content,
                json=json_data, timeout=90)
        except httpx.HTTPError:
            raise SupabaseStorageError("Supabase Storage could not be reached.") from None
        if response.status_code not in allowed:
            explanations = {
                400: "Supabase rejected the bucket or file request.",
                401: "Supabase rejected the server key. Confirm the current Secret key is saved under [supabase_storage].",
                403: "Supabase denied the storage operation. Confirm the server Secret key and bucket permissions.",
                404: "The Supabase bucket or object was not found. Confirm the bucket name and project URL.",
                409: "A file with this path already exists. Enable replacement or remove the duplicate from the upload.",
                413: "Supabase rejected the upload because it exceeds the bucket or project size limit.",
            }
            detail = explanations.get(response.status_code, "Check the Supabase Storage settings and retry.")
            server_detail = ""
            try:
                body = response.json()
                code = str(body.get("code") or body.get("error") or "").strip()
                message = str(body.get("message") or "").strip()
                # Never include response headers, request URLs, or credentials.
                if self.key:
                    message = message.replace(self.key, "[redacted]")
                parts = [part for part in (code, message[:240]) if part]
                if parts:
                    server_detail = " Supabase detail: " + " — ".join(parts)
            except (ValueError, AttributeError):
                pass
            raise SupabaseStorageError(f"{detail}{server_detail} (HTTP {response.status_code})")
        return response

    def ensure_private_bucket(self):
        path = "/storage/v1/bucket/" + quote(self.bucket, safe="")
        try:
            response = self._request("GET", path, allowed=(200,))
        except SupabaseStorageError as exc:
            if "HTTP 404" not in str(exc):
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
        if mime_type == "application/octet-stream":
            import mimetypes
            mime_type = mimetypes.guess_type(path)[0] or mime_type
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

    @staticmethod
    def _safe_archive_path(name):
        normalized = str(name or "").replace("\\", "/")
        path = PurePosixPath(normalized)
        if path.is_absolute() or not normalized or any(part in ("", ".", "..") for part in path.parts):
            raise SupabaseStorageError("The ZIP contains an unsafe file path.")
        if path.suffix.lower() not in ALLOWED_EXTENSIONS:
            raise SupabaseStorageError(f"The ZIP contains an unsupported file type: {path.name}.")
        return str(path)

    def upload_local_files(self, files, *, root_name="DARE-Arbo", replace_existing=False, progress=None):
        """Upload local Streamlit file objects or ZIP files and verify each object."""
        self.ensure_private_bucket()
        entries = []
        staged = {}

        def add(path, content):
            path = self._safe_archive_path(path)
            if len(content) > MAX_UPLOAD_BYTES:
                raise SupabaseStorageError(f"{PurePosixPath(path).name} exceeds the 50 MB per-file limit.")
            if path in staged:
                raise SupabaseStorageError(f"The upload contains the same path more than once: {path}.")
            staged[path] = content

        total_archive_bytes = 0
        for uploaded in files:
            filename = str(getattr(uploaded, "name", "") or "").replace("\\", "/").split("/")[-1]
            payload = uploaded.getvalue() if hasattr(uploaded, "getvalue") else bytes(uploaded)
            if filename.lower().endswith(".zip"):
                if len(payload) > MAX_ARCHIVE_BYTES:
                    raise SupabaseStorageError("A ZIP upload must be no larger than 250 MB.")
                try:
                    archive = zipfile.ZipFile(BytesIO(payload))
                except (zipfile.BadZipFile, OSError):
                    raise SupabaseStorageError(f"{filename} is not a valid ZIP archive.") from None
                with archive:
                    for member in archive.infolist():
                        if member.is_dir():
                            continue
                        # Reject Unix symlinks, encrypted members, and oversized archives before extraction.
                        if (member.external_attr >> 16) & 0o170000 == 0o120000 or member.flag_bits & 1:
                            raise SupabaseStorageError("The ZIP contains a link or encrypted entry; upload regular files only.")
                        total_archive_bytes += member.file_size
                        if total_archive_bytes > MAX_ARCHIVE_BYTES:
                            raise SupabaseStorageError("The uncompressed ZIP contents exceed the 250 MB total limit.")
                        relative = self._safe_archive_path(member.filename)
                        add(f"{root_name}/{relative}", archive.read(member))
            else:
                if not filename:
                    raise SupabaseStorageError("An uploaded file has no filename.")
                add(f"{root_name}/{filename}", payload)

        if not staged:
            raise SupabaseStorageError("Choose one or more files or a ZIP archive with supported project files.")

        for path, content in staged.items():
            digest = sha256(content).hexdigest()
            try:
                self.upload(path, content, upsert=replace_existing)
                copied = self.download(path)
            except SupabaseStorageError:
                raise
            if sha256(copied).hexdigest() != digest:
                raise SupabaseStorageError(f"Checksum verification failed for {PurePosixPath(path).name}.")
            entries.append({"path": path, "size": len(content), "sha256": digest})
            if progress:
                progress(len(entries), PurePosixPath(path).name)

        manifest = {
            "source": "local file upload",
            "copied_at": datetime.now(timezone.utc).isoformat(),
            "file_count": len(entries),
            "files": entries,
        }
        self.upload(MIGRATION_MARKER, json.dumps(manifest, indent=2).encode("utf-8"),
                    "application/json", upsert=True)
        return manifest
