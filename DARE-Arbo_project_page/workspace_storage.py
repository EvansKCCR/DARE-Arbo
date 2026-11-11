"""Supabase-backed private workspace for documents and the study workbook."""
from hashlib import sha256
from pathlib import PurePosixPath
import mimetypes

from access import require_full_access
from supabase_storage import MAX_UPLOAD_BYTES, SupabaseStorage

PROJECT_FOLDER = "DARE-Arbo"
WORKBOOK_PATH = PROJECT_FOLDER + "/DARE-Arbo_Project_Gantt_Updated_Sep2026-May2027.xlsx"
SOURCE_ID = WORKBOOK_PATH
WORKBOOK_FOLDER_ID = PROJECT_FOLDER
FOLDER_ID = PROJECT_FOLDER
WORKBOOK_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
MAX_DOCUMENT_BYTES = 25 * 1024 * 1024
MAX_BYTES = MAX_DOCUMENT_BYTES
ALLOWED = {".pdf", ".docx", ".xlsx", ".pptx", ".csv", ".tsv", ".txt", ".png", ".jpg", ".jpeg", ".html"}


class SupabaseWorkspaceService:
    """Small storage adapter for existing workbook-editing logic."""

    def __init__(self, config):
        self.storage = SupabaseStorage(config)

    @staticmethod
    def _parent(path):
        return str(PurePosixPath(path).parent)

    def get_item(self, item_id):
        path = str(item_id).replace("\\", "/").strip("/")
        content = self.storage.download(path)
        name = PurePosixPath(path).name
        mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
        return {
            "id": path,
            "path": path,
            "name": name,
            "size": len(content),
            "eTag": sha256(content).hexdigest(),
            "parentReference": {"id": self._parent(path)},
            "file": {"mimeType": mime},
        }

    def download(self, item_id):
        return self.storage.download(str(item_id))

    def update_item(self, item_id, content, etag, mime_type):
        path = str(item_id)
        current = self.storage.download(path)
        if not etag or sha256(current).hexdigest() != etag:
            error = RuntimeError("The workbook changed. Reload before saving again.")
            error.resp = type("ResponseStatus", (), {"status": 412})()
            raise error
        self.storage.upload(path, content, mime_type, upsert=True)
        return self.get_item(path)

    def list_children(self, folder_id):
        prefix = str(folder_id).replace("\\", "/").strip("/")
        items = self.storage.list_objects(prefix=prefix)
        result = []
        for item in items:
            name = str(item.get("name") or "").strip("/")
            if not name:
                continue
            # Storage's folder-list API returns children relative to its prefix.
            # Accommodate providers that return the full object path as well.
            if name.startswith(prefix + "/"):
                relative = name[len(prefix) + 1:]
            else:
                relative = name
            if "/" in relative:
                first, remainder = relative.split("/", 1)
                if remainder:
                    result.append({"id": prefix + "/" + first, "name": first, "folder": {}})
                    continue
            path = prefix + "/" + relative
            metadata = item.get("metadata") or {}
            is_file = item.get("id") is not None or bool(metadata)
            entry = {
                "id": path,
                "name": relative,
                "size": int(metadata.get("size") or item.get("size") or 0),
                "lastModifiedDateTime": item.get("updated_at") or item.get("lastModifiedDateTime") or "",
                "parentReference": {"id": prefix},
            }
            if is_file:
                entry["file"] = {"mimeType": metadata.get("mimetype") or mimetypes.guess_type(relative)[0] or "application/octet-stream"}
            else:
                entry["folder"] = {}
            result.append(entry)
        # Folder placeholders can be repeated when list results include nested paths.
        unique = {}
        for item in result:
            unique[(item["name"].casefold(), bool(item.get("folder")))] = item
        return list(unique.values())

    def upload(self, parent_id, name, content, mime_type):
        safe_name = PurePosixPath(str(name).replace("\\", "/")).name.strip()
        if not safe_name or PurePosixPath(safe_name).suffix.lower() not in ALLOWED:
            raise ValueError("Choose a supported document filename.")
        if not content or len(content) > MAX_DOCUMENT_BYTES:
            raise ValueError("Uploads must contain data and be no larger than 25 MB.")
        path = str(parent_id).strip("/") + "/" + safe_name
        self.storage.upload(path, content, mime_type, upsert=False)
        return {"id": path, "name": safe_name}


class SupabaseDocumentStore:
    def __init__(self, service, identity, allowed_emails):
        self.service, self.identity, self.allowed_emails = service, identity, allowed_emails

    def authorize(self):
        require_full_access(self.identity(), self.allowed_emails())

    def list_files(self):
        self.authorize()
        result = []
        for item in self.service.list_children(PROJECT_FOLDER):
            if item.get("folder"):
                continue
            result.append({
                "id": item["id"], "name": item["name"],
                "mimeType": item.get("file", {}).get("mimeType", "application/octet-stream"),
                "size": item.get("size", 0), "modifiedTime": item.get("lastModifiedDateTime", ""),
                "parents": [PROJECT_FOLDER],
            })
        return result

    def download(self, file_id):
        self.authorize()
        path = str(file_id).replace("\\", "/").strip("/")
        if self._parent(path) != PROJECT_FOLDER:
            raise PermissionError("This file is outside the project folder.")
        metadata = self.service.get_item(path)
        if metadata["size"] > MAX_DOCUMENT_BYTES:
            raise ValueError("This file exceeds the 25 MB download limit.")
        return metadata["name"], metadata["file"]["mimeType"], self.service.download(path)

    def upload(self, name, content):
        self.authorize()
        import mimetypes
        return self.service.upload(PROJECT_FOLDER, name, content,
                                   mimetypes.guess_type(name)[0] or "application/octet-stream")

    @staticmethod
    def _parent(path):
        return str(PurePosixPath(path).parent)
