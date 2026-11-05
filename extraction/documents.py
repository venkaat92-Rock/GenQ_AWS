"""L1 — Context documents: SOPs, work instructions, screenshots, anything the
customer already has written down about how the process is supposed to run.

Why this exists. The modelling agent reads a SCHEMA, which says what the system
records but never why. An SOP says "AP blocks the invoice when the price differs by
more than 2%" — that sentence explains a status code no amount of staring at RBKP
will reveal. Klarity's Discover phase ingests documents for exactly this reason; this
is the event-log equivalent, and it is strictly OPTIONAL: every path here degrades to
"no documents" and the agent drafts from the schema alone, as it does today.

Design rules:

* A document never fails silently. Each one carries a `status` and, when something
  could not be read, a `note` in plain language saying why and what to do. A SOP the
  agent could not read must not look like a SOP the agent read and ignored.
* Extraction is uniform text. Images and image-only PDFs are transcribed by a vision
  model at upload time, so everything downstream handles one shape.
* Text is capped per document and in total. An agent prompt stuffed with a 200-page
  manual is slower, dearer, and worse — and silently truncating it would be the kind
  of invisible lossiness §1.1 exists to prevent, so truncation is recorded on the
  document and shown to the user.
"""

from __future__ import annotations

import io
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

# Per-document and whole-corpus caps on what reaches the modelling prompt.
MAX_CHARS_PER_DOC = 12_000
MAX_CHARS_TOTAL = 40_000

TEXT_EXT = {".txt", ".md", ".markdown", ".rst", ".log"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff", ".tif"}
_IMAGE_MEDIA = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
                ".tiff": "image/tiff", ".tif": "image/tiff"}

VISION_PROMPT = (
    "This image was uploaded as reference material for a process-mining project — it "
    "may be a screenshot of a system, a scanned work instruction, a flowchart, or a "
    "photo of a whiteboard. Transcribe everything legible: headings, field labels, "
    "status values, step names, and the order of any flow. If it shows a process, "
    "describe the sequence. Do not speculate about anything you cannot actually see."
)


@dataclass
class ContextDocument:
    id: str
    filename: str
    kind: str                       # text | word | pdf | spreadsheet | image | unknown
    status: str                     # read | needs_ai | unreadable
    note: str = ""                  # plain-language reason when not fully read
    text: str = ""
    chars: int = 0
    truncated: bool = False
    bytes: int = 0
    uploaded_by: str = ""
    uploaded_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))

    def to_dict(self) -> dict:
        return asdict(self)

    def summary(self) -> dict:
        """What the UI and the audit trail need — without the full body."""
        d = self.to_dict()
        d.pop("text", None)
        return d


def classify(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    if ext in TEXT_EXT:
        return "text"
    if ext == ".docx":
        return "word"
    if ext == ".pdf":
        return "pdf"
    if ext in (".xlsx", ".xlsm", ".csv"):
        return "spreadsheet"
    if ext in IMAGE_EXT:
        return "image"
    return "unknown"


_ELLIPSIS = " …"


def _clip(text: str, limit: int) -> str:
    """Cut to AT MOST `limit` characters, ellipsis included. The caps are a promise
    about prompt size, so the marker has to fit inside the budget, not sit beyond it."""
    if len(text) <= limit:
        return text
    cut = text[:max(0, limit - len(_ELLIPSIS))]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut + _ELLIPSIS


def _truncate(text: str) -> tuple[str, bool]:
    text = text.strip()
    if len(text) <= MAX_CHARS_PER_DOC:
        return text, False
    return _clip(text, MAX_CHARS_PER_DOC), True


# ---- per-format readers --------------------------------------------------------------


def _read_word(raw: bytes) -> str:
    import docx  # python-docx, already a dependency

    doc = docx.Document(io.BytesIO(raw))
    parts = [p.text for p in doc.paragraphs if p.text.strip()]
    for table in doc.tables:                     # SOPs put the real content in tables
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if cells:
                parts.append(" | ".join(cells))
    return "\n".join(parts)


def _read_pdf(raw: bytes) -> str:
    import pdfplumber

    pages = []
    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                pages.append(text)
    return "\n".join(pages)


def _read_spreadsheet(filename: str, raw: bytes) -> str:
    if filename.lower().endswith(".csv"):
        return raw.decode("utf-8", errors="replace")
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    lines: list[str] = []
    for ws in wb.worksheets:
        lines.append(f"# sheet: {ws.title}")
        for row in ws.iter_rows(values_only=True):
            cells = [str(c) for c in row if c is not None and str(c).strip()]
            if cells:
                lines.append(" | ".join(cells))
    return "\n".join(lines)


def _image_payload(raw: bytes, filename: str, max_dim: int = 1400) -> tuple[bytes, str]:
    """Downscale so a phone photo does not blow up the request."""
    try:
        from PIL import Image

        im = Image.open(io.BytesIO(raw))
        if max(im.size) > max_dim:
            r = max_dim / float(max(im.size))
            im = im.resize((max(1, int(im.width * r)), max(1, int(im.height * r))))
        if im.mode not in ("RGB", "L"):
            im = im.convert("RGB")
        buf = io.BytesIO()
        im.save(buf, format="PNG")
        return buf.getvalue(), "image/png"
    except Exception:
        return raw, _IMAGE_MEDIA.get(Path(filename).suffix.lower(), "image/png")


# ---- the one entry point -------------------------------------------------------------


def extract(doc_id: str, filename: str, raw: bytes, llm=None, uploaded_by: str = "") -> ContextDocument:
    """Turn an uploaded file into text the modelling agent can read.

    `llm` is optional and only used for images / image-only PDFs. Without it those
    are stored with status `needs_ai` rather than being dropped, so the user can see
    that a file they uploaded is not actually informing the mapping.
    """
    kind = classify(filename)
    base = dict(id=doc_id, filename=filename, kind=kind, bytes=len(raw), uploaded_by=uploaded_by)

    def fail(note: str, status: str = "unreadable") -> ContextDocument:
        return ContextDocument(status=status, note=note, **base)

    if not raw:
        return fail("The file is empty.")

    try:
        if kind == "text":
            text = raw.decode("utf-8", errors="replace")
        elif kind == "word":
            text = _read_word(raw)
        elif kind == "spreadsheet":
            text = _read_spreadsheet(filename, raw)
        elif kind == "pdf":
            text = _read_pdf(raw)
            if not text.strip():
                # A scan: there is no text layer, so it is really an image.
                return _vision(base, raw, filename, llm,
                               "This PDF has no text layer — it is a scan.")
        elif kind == "image":
            return _vision(base, raw, filename, llm, "")
        else:
            return fail(
                f"'{Path(filename).suffix or 'this file type'}' is not supported. "
                "Word, PDF, plain text, spreadsheets and images are."
            )
    except ImportError as exc:
        return fail(f"The reader for this format is not installed on the server ({exc}).")
    except Exception as exc:
        msg = str(exc).lower()
        if "encrypt" in msg or "password" in msg:
            return fail("The file is password-protected. Remove the password and re-upload.")
        return fail(f"Could not read the file: {exc}")

    if not text.strip():
        return fail("No readable text was found in the file.")

    body, truncated = _truncate(text)
    return ContextDocument(status="read", text=body, chars=len(body), truncated=truncated,
                           note=(f"Only the first {MAX_CHARS_PER_DOC:,} characters are used."
                                 if truncated else ""), **base)


def _vision(base: dict, raw: bytes, filename: str, llm, prefix: str) -> ContextDocument:
    if llm is None or not hasattr(llm, "complete_with_image"):
        return ContextDocument(
            status="needs_ai",
            note=(prefix + " An AI model with vision is needed to read it — configure one "
                  "on the Data load page, then re-upload.").strip(),
            **base,
        )
    try:
        payload, media = _image_payload(raw, filename)
        text = llm.complete_with_image(VISION_PROMPT, f"File name: {filename}", payload, media)
    except Exception as exc:
        return ContextDocument(status="unreadable",
                               note=f"The AI model could not read this image: {exc}", **base)
    if not (text or "").strip():
        return ContextDocument(status="unreadable",
                               note="The AI model found nothing legible in this image.", **base)
    body, truncated = _truncate(text)
    return ContextDocument(status="read", text=body, chars=len(body), truncated=truncated,
                           note=(prefix + " Read by the AI model from the image.").strip(), **base)


def corpus_for_prompt(documents: list[ContextDocument]) -> str:
    """Join the readable documents into one block for the modelling prompt, under the
    total cap. Returns '' when there is nothing usable — the caller then behaves
    exactly as it did before documents existed."""
    separator = "\n\n"
    chunks, used = [], 0
    for d in documents:
        if d.status != "read" or not d.text.strip():
            continue
        header = f"--- DOCUMENT: {d.filename} ---\n"
        joint = len(separator) if chunks else 0
        room = MAX_CHARS_TOTAL - used - joint - len(header)
        if room <= 200:          # too little left to be worth a partial document
            break
        body = _clip(d.text, room)
        chunks.append(header + body)
        used += joint + len(header) + len(body)
    return separator.join(chunks)
