"""Fixture tests for optional SOP / context-document upload.

The load-bearing assertions are the honesty ones (§1.1): a file we could not read
must never be indistinguishable from a file we read, documents must reach the
modelling prompt only when a model can actually use them, and a draft must record
which documents informed it.
"""

import io
import json

import pytest

from extraction.documents import (
    MAX_CHARS_PER_DOC, ContextDocument, classify, corpus_for_prompt, extract,
)


def _docx_bytes(paragraphs, table_rows=None) -> bytes:
    import docx

    d = docx.Document()
    for p in paragraphs:
        d.add_paragraph(p)
    if table_rows:
        t = d.add_table(rows=len(table_rows), cols=len(table_rows[0]))
        for r, row in enumerate(table_rows):
            for c, value in enumerate(row):
                t.cell(r, c).text = value
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def _png_bytes() -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (40, 20), (200, 200, 200)).save(buf, format="PNG")
    return buf.getvalue()


class VisionLLM:
    """Stands in for a configured multimodal model."""

    def __init__(self, reply="Screenshot shows: Invoice Blocked -> Price Variance 2%"):
        self.reply = reply
        self.images = 0

    def complete(self, system, user):            # pragma: no cover - not used here
        return "{}"

    def complete_with_image(self, system, user, image, media_type="image/png"):
        self.images += 1
        return self.reply


# ---- classification + plain formats ---------------------------------------------------


@pytest.mark.parametrize("name,kind", [
    ("sop.txt", "text"), ("SOP.DOCX", "word"), ("manual.pdf", "pdf"),
    ("codes.xlsx", "spreadsheet"), ("screen.PNG", "image"), ("archive.zip", "unknown"),
])
def test_classify(name, kind):
    assert classify(name) == kind


def test_plain_text_is_read():
    doc = extract("d1", "sop.txt", b"AP blocks invoices above a 2% price variance.")
    assert doc.status == "read"
    assert "2% price variance" in doc.text
    assert doc.chars > 0 and not doc.truncated


def test_word_document_includes_table_cells():
    raw = _docx_bytes(["Purchase-to-Pay SOP"], [["Code", "Meaning"], ["S1", "Blocked for price"]])
    doc = extract("d2", "sop.docx", raw)
    assert doc.status == "read"
    assert "Purchase-to-Pay SOP" in doc.text
    assert "Blocked for price" in doc.text      # the real content lives in tables


def test_spreadsheet_is_flattened_to_text():
    from openpyxl import Workbook

    wb = Workbook()
    wb.active.append(["Status", "Meaning"])
    wb.active.append(["S1", "Blocked"])
    buf = io.BytesIO(); wb.save(buf)
    doc = extract("d3", "codes.xlsx", buf.getvalue())
    assert doc.status == "read" and "Blocked" in doc.text


# ---- the honesty cases ----------------------------------------------------------------


def test_unsupported_type_is_stored_with_a_reason_not_dropped():
    doc = extract("d4", "diagram.zip", b"PK\x03\x04junk")
    assert doc.status == "unreadable"
    assert "not supported" in doc.note
    assert doc.filename == "diagram.zip"        # still visible to the user


def test_empty_file_says_so():
    assert extract("d5", "empty.txt", b"").status == "unreadable"


def test_image_without_a_model_is_needs_ai_not_silently_ignored():
    doc = extract("d6", "screen.png", _png_bytes(), llm=None)
    assert doc.status == "needs_ai"
    assert "AI model" in doc.note
    assert doc.text == ""


def test_image_with_a_vision_model_is_transcribed():
    llm = VisionLLM()
    doc = extract("d7", "screen.png", _png_bytes(), llm=llm)
    assert doc.status == "read"
    assert llm.images == 1
    assert "Invoice Blocked" in doc.text
    assert "Read by the AI model" in doc.note


def test_vision_failure_is_reported_not_swallowed():
    class Broken(VisionLLM):
        def complete_with_image(self, *a, **k):
            raise RuntimeError("model refused the image")

    doc = extract("d8", "screen.png", _png_bytes(), llm=Broken())
    assert doc.status == "unreadable" and "model refused the image" in doc.note


def test_long_document_is_capped_and_says_so():
    doc = extract("d9", "manual.txt", ("word " * 40_000).encode())
    assert doc.status == "read"
    assert doc.truncated and doc.chars <= MAX_CHARS_PER_DOC
    assert "characters are used" in doc.note


# ---- corpus assembly -------------------------------------------------------------------


def test_corpus_skips_unreadable_and_names_each_file():
    docs = [
        ContextDocument("a", "sop.docx", "word", "read", text="Block above 2%"),
        ContextDocument("b", "scan.png", "image", "needs_ai", note="no model"),
        ContextDocument("c", "policy.txt", "text", "read", text="Three-way match required"),
    ]
    corpus = corpus_for_prompt(docs)
    assert "sop.docx" in corpus and "policy.txt" in corpus
    assert "scan.png" not in corpus              # not read -> must not reach the prompt
    assert "Three-way match required" in corpus


def test_corpus_is_empty_when_nothing_is_readable():
    docs = [ContextDocument("b", "scan.png", "image", "needs_ai")]
    assert corpus_for_prompt(docs) == ""


def test_corpus_respects_the_total_cap():
    docs = [ContextDocument(str(i), f"f{i}.txt", "text", "read", text="x" * 12_000)
            for i in range(10)]
    assert len(corpus_for_prompt(docs)) <= 40_000


# ---- how documents reach the modelling agent -------------------------------------------

from pathlib import Path                                      # noqa: E402

from engine.db import EventLogStore                           # noqa: E402
from modelling.agent import ModellingAgent                    # noqa: E402

SAP_DIR = Path(__file__).parent.parent / "fixtures" / "sap"


@pytest.fixture()
def staged_store():
    with EventLogStore() as store:
        for csv in SAP_DIR.glob("*.csv"):
            store.stage_csv(csv.stem, csv)
        yield store


class EchoLLM:
    """Returns the draft it was given, so refinement succeeds and we can inspect
    exactly what was put in front of the model."""

    def __init__(self):
        self.system = ""
        self.payload = {}

    def complete(self, system, user):
        self.system = system
        self.payload = json.loads(user)
        return json.dumps(self.payload["draft_spec"])


def _sop() -> ContextDocument:
    return ContextDocument("s1", "AP SOP v3.docx", "word", "read",
                           text="Invoices are blocked when the price differs by more than 2%.")


def test_documents_reach_the_prompt_and_are_recorded_on_the_spec(staged_store):
    llm = EchoLLM()
    spec = ModellingAgent(staged_store, llm, [_sop()]).draft()

    assert "AP SOP v3.docx" in llm.payload["context_documents"]
    assert "price differs by more than 2%" in llm.payload["context_documents"]
    # the model is told how to treat a document, including not to pick a side
    assert "open_question" in llm.system and "not proof of it" in llm.system
    # audit trail on the spec itself
    assert spec.documents_considered == ["AP SOP v3.docx"]
    assert "AP SOP v3.docx" in spec.notes


def test_unreadable_documents_never_reach_the_prompt(staged_store):
    llm = EchoLLM()
    unread = ContextDocument("s2", "scan.png", "image", "needs_ai", note="no model")
    spec = ModellingAgent(staged_store, llm, [_sop(), unread]).draft()

    assert "scan.png" not in llm.payload.get("context_documents", "")
    assert spec.documents_considered == ["AP SOP v3.docx"]


def test_without_documents_the_prompt_is_unchanged(staged_store):
    """The feature is optional: with no documents the agent behaves exactly as before."""
    llm = EchoLLM()
    spec = ModellingAgent(staged_store, llm, []).draft()

    assert "context_documents" not in llm.payload
    assert "DOCUMENT" not in llm.system          # no document clause appended
    assert spec.documents_considered == []


def test_documents_without_a_model_are_flagged_as_unused(staged_store):
    """An upload that cannot inform the mapping must not look like one that did."""
    spec = ModellingAgent(staged_store, None, [_sop()]).draft()
    assert spec.documents_considered == []
    assert "not read" in spec.notes and "no AI model is configured" in spec.notes


# ---- HTTP surface ------------------------------------------------------------------------


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROCESSIQ_DATA", str(tmp_path))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from fastapi.testclient import TestClient

    from api.main import app

    return TestClient(app)


@pytest.fixture()
def auth(client):
    r = client.post("/api/workspaces", json={"name": "Docs Co"})
    return {"X-API-Key": r.json()["api_key"]}


def test_upload_list_and_delete(client, auth):
    assert client.get("/api/documents", headers=auth).json()["documents"] == []

    up = client.post("/api/documents", headers=auth,
                     files={"file": ("sop.txt", b"Three-way match is mandatory.", "text/plain")},
                     data={"uploaded_by": "Venkat"})
    assert up.status_code == 200
    body = up.json()
    assert body["status"] == "read" and body["filename"] == "sop.txt"
    assert body["uploaded_by"] == "Venkat"
    assert "text" not in body                     # the body is for the agent, not the list view

    listed = client.get("/api/documents", headers=auth).json()["documents"]
    assert len(listed) == 1

    assert client.delete(f"/api/documents/{body['id']}", headers=auth).status_code == 200
    assert client.get("/api/documents", headers=auth).json()["documents"] == []
    assert client.delete(f"/api/documents/{body['id']}", headers=auth).status_code == 404


def test_unreadable_upload_is_kept_with_its_reason(client, auth):
    up = client.post("/api/documents", headers=auth,
                     files={"file": ("notes.zip", b"PK\x03\x04", "application/zip")})
    assert up.status_code == 200                  # stored, not rejected
    assert up.json()["status"] == "unreadable"
    assert "not supported" in up.json()["note"]
    assert len(client.get("/api/documents", headers=auth).json()["documents"]) == 1


def test_documents_are_per_workspace(client, auth):
    client.post("/api/documents", headers=auth,
                files={"file": ("sop.txt", b"mine", "text/plain")})
    other = client.post("/api/workspaces", json={"name": "Other Co"}).json()
    other_auth = {"X-API-Key": other["api_key"]}
    assert client.get("/api/documents", headers=other_auth).json()["documents"] == []


def test_documents_require_auth(client):
    assert client.get("/api/documents").status_code == 401
