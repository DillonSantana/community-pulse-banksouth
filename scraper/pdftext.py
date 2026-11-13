"""Download agenda PDFs and extract text so agenda line items can be tagged."""
from __future__ import annotations

import io
import logging

from pypdf import PdfReader

from .http import Client

log = logging.getLogger(__name__)

MAX_BYTES = 25 * 1024 * 1024
MAX_PAGES = 30          # agendas are short; packets can be hundreds of pages
MAX_CHARS = 60_000


def extract(client: Client, url: str) -> str:
    resp = client.get(url, stream=True)
    ctype = resp.headers.get("content-type", "").lower()
    buf = io.BytesIO()
    for chunk in resp.iter_content(64 * 1024):
        buf.write(chunk)
        if buf.tell() > MAX_BYTES:
            resp.close()
            raise ValueError(f"PDF larger than {MAX_BYTES // 1_048_576} MB")
    data = buf.getvalue()
    if not data.startswith(b"%PDF"):
        raise ValueError(f"not a PDF (content-type {ctype or 'unknown'})")
    reader = PdfReader(io.BytesIO(data))
    parts, total = [], 0
    for page in reader.pages[:MAX_PAGES]:
        try:
            txt = page.extract_text() or ""
        except Exception as e:     # malformed page; keep going
            log.debug("page extract failed for %s: %s", url, e)
            continue
        parts.append(txt)
        total += len(txt)
        if total >= MAX_CHARS:
            break
    return "\n".join(parts)[:MAX_CHARS]
