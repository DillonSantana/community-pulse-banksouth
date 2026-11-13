"""Fetch, fingerprint, store and extract text from county documents.

The lead-finder contract wants every relevant file landed untouched: the sha256 of
the raw bytes, the original file kept on disk, and the full extracted text (not
cleaned, not split). Conditional requests (ETag / Last-Modified) keep daily runs
from re-downloading files the county hasn't changed.
"""
from __future__ import annotations

import hashlib
import io
import logging
import re
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from pypdf import PdfReader

from .http import Client

log = logging.getLogger(__name__)

MAX_BYTES = 80 * 1024 * 1024    # packets can be large; skip beyond this
MAX_PDF_PAGES = 600
SCANNED_CHARS_PER_PAGE = 40      # below this average, treat the PDF as image-only


@dataclass
class Doc:
    source_url: str
    content_hash: str
    file_type: str               # pdf | html | docx | txt | other
    retrieved_at: str
    file_ref: str                # path of the stored original, relative to the project
    raw_text: str
    has_text_layer: bool | None
    changed: bool                # new URL, or same URL with new bytes
    extra: dict = field(default_factory=dict)


def _file_type(url: str, ctype: str, head: bytes) -> str:
    if head.startswith(b"%PDF") or "pdf" in ctype:
        return "pdf"
    if head.startswith(b"PK") and ("word" in ctype or url.lower().split("?")[0].endswith(".docx")):
        return "docx"
    if "html" in ctype or head.lstrip()[:15].lower().startswith((b"<!doctype", b"<html")):
        return "html"
    if ctype.startswith("text/"):
        return "txt"
    return "other"


def _pdf_text(data: bytes) -> tuple[str, bool | None, dict]:
    reader = PdfReader(io.BytesIO(data))
    pages = reader.pages
    parts = []
    for page in pages[:MAX_PDF_PAGES]:
        try:
            parts.append(page.extract_text() or "")
        except Exception as e:   # one bad page shouldn't lose the document
            log.debug("page extract failed: %s", e)
            parts.append("")
    text = "\n".join(parts)
    n = max(len(parts), 1)
    has_layer = len(re.sub(r"\s", "", text)) / n >= SCANNED_CHARS_PER_PAGE
    extra = {"page_count": len(pages)}
    if len(pages) > MAX_PDF_PAGES:
        extra["text_truncated_after_page"] = MAX_PDF_PAGES
    return text, has_layer, extra


def _docx_text(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        xml = z.read("word/document.xml").decode("utf-8", "ignore")
    xml = re.sub(r"</w:p>", "\n", xml)
    return re.sub(r"<[^>]+>", "", xml)


def _html_text(data: bytes) -> str:
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(data, "html.parser")
    for tag in soup(["script", "style", "noscript", "nav", "header", "footer"]):
        tag.decompose()
    main = soup.find("article") or soup.find("main") or soup.body or soup
    return main.get_text("\n", strip=True)


def extract_text(file_type: str, data: bytes) -> tuple[str, bool | None, dict]:
    if file_type == "pdf":
        return _pdf_text(data)
    if file_type == "docx":
        return _docx_text(data), True, {}
    if file_type == "html":
        return _html_text(data), True, {}
    if file_type == "txt":
        return data.decode("utf-8", "ignore"), True, {}
    return "", None, {}


class DocumentFetcher:
    def __init__(self, client: Client, store, files_dir: Path, project_root: Path):
        self.client = client
        self.store = store
        self.files_dir = files_dir
        self.root = project_root
        files_dir.mkdir(parents=True, exist_ok=True)

    def fetch(self, url: str) -> Doc:
        prev = self.store.doc_state(url)       # None or dict of the last version we saw
        headers = {}
        if prev and prev.get("etag"):
            headers["If-None-Match"] = prev["etag"]
        if prev and prev.get("last_modified"):
            headers["If-Modified-Since"] = prev["last_modified"]

        resp = self.client.get(url, headers=headers, stream=True)
        now = datetime.now().astimezone().isoformat(timespec="seconds")

        if resp.status_code == 304 and prev:
            resp.close()
            return self._from_prev(prev, now)

        buf = io.BytesIO()
        for chunk in resp.iter_content(256 * 1024):
            buf.write(chunk)
            if buf.tell() > MAX_BYTES:
                resp.close()
                raise ValueError(f"file over {MAX_BYTES // 1_048_576} MB, skipped")
        data = buf.getvalue()
        digest = hashlib.sha256(data).hexdigest()
        etag, last_mod = resp.headers.get("ETag"), resp.headers.get("Last-Modified")

        if prev and prev["content_hash"] == digest:
            self.store.touch_doc(url, etag, last_mod, now)
            return self._from_prev(prev, now)

        ftype = _file_type(url, resp.headers.get("content-type", "").lower(), data[:64])
        text, has_layer, extra = extract_text(ftype, data)
        ext = {"pdf": ".pdf", "docx": ".docx", "html": ".html", "txt": ".txt"}.get(ftype, ".bin")
        path = self.files_dir / f"{digest}{ext}"
        if not path.exists():
            path.write_bytes(data)
        if etag:
            extra["etag"] = etag
        if last_mod:
            extra["last_modified"] = last_mod
        extra["content_type"] = resp.headers.get("content-type", "")
        if prev:
            extra["previous_content_hash"] = prev["content_hash"]

        doc = Doc(source_url=url, content_hash=digest, file_type=ftype, retrieved_at=now,
                  file_ref=str(path.relative_to(self.root)), raw_text=text,
                  has_text_layer=has_layer, changed=True, extra=extra)
        self.store.save_doc(doc, etag, last_mod)
        return doc

    def _from_prev(self, prev: dict, now: str) -> Doc:
        return Doc(source_url=prev["url"], content_hash=prev["content_hash"],
                   file_type=prev["file_type"], retrieved_at=now, file_ref=prev["file_ref"],
                   raw_text=prev["raw_text"], has_text_layer=prev["has_text_layer"],
                   changed=False, extra={})
