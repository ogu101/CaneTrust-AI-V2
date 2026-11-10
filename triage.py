"""
Triage: uploaded PDFs -> page records -> proposed list of documents.

Code computes every checkable fact about each page (printed page number,
document-system footer numbers, form numbers, dates, University mentions).
One model call over short previews decides where documents start and end and
what each one is. Code then checks the answer (every page covered exactly
once, no document crossing files, valid dates), retries once with the
problems stated, and if it still fails, repairs the list and flags the
affected rows instead of guessing silently.
"""

import re
from datetime import date
from io import BytesIO
from typing import Literal, Optional

import anthropic
from pydantic import Field
from pypdf import PdfReader

import config
from schema import StrictModel

# ---------------------------------------------------------------------------
# Document types and the default-treatment table (applied by code)
# ---------------------------------------------------------------------------

DocType = Literal[
    "trust", "restatement", "amendment", "will", "codicil",
    "notice_of_administration", "court_filing", "letter", "other",
]
Treatment = Literal["annotate", "context", "ignore"]

DEFAULT_TREATMENT = {
    "trust": "annotate", "restatement": "annotate", "amendment": "annotate",
    "will": "annotate", "codicil": "annotate",
    "notice_of_administration": "context", "letter": "context",
    "court_filing": "ignore", "other": "ignore",
}
FAMILY = {"trust": "trust", "restatement": "trust", "amendment": "trust",
          "will": "will", "codicil": "will"}
BASE_TYPE = {"trust": "trust", "will": "will"}


# ---------------------------------------------------------------------------
# Model output schema (strict tool use)
# ---------------------------------------------------------------------------

class TriageDoc(StrictModel):
    start_page: int = Field(description="First page, using the global page numbers shown as P<n>.")
    end_page: int = Field(description="Last page, global numbering, inclusive.")
    type: DocType
    title: str = Field(description="Title as the document names itself, e.g. 'Fifth Amendment to the Herrold Living Trust'.")
    date: Optional[str] = Field(description="Execution or filing date as YYYY-MM-DD, or null if none is shown.")
    amends: Optional[str] = Field(description="For an amendment, restatement or codicil: the instrument it modifies, as named in the text. Otherwise null.")
    refers_to: list[str] = Field(description="Other instruments this document mentions (e.g. 'Second Amendment dated 3/1/2009'). Used to spot missing documents.")
    contains_um_gift: bool = Field(description="True if this document itself appears to give, or change a gift, to the University of Miami.")
    treatment: Treatment = Field(description="Proposed treatment; use the default for the type unless there is a specific reason not to.")
    reason: str = Field(description="One sentence: why this treatment.")
    confidence: Literal["high", "medium", "low"] = Field(description="Confidence in this document's boundaries and type.")
    boundary_evidence: str = Field(description="What marked the start/end, e.g. 'new title page', 'footer ID changes', 'page numbering restarts'.")


class TriageResult(StrictModel):
    documents: list[TriageDoc]
    mixed_matters_suspected: bool = Field(description="True if the packet appears to contain papers of more than one decedent or grantor.")
    mixed_matters_explanation: Optional[str] = Field(description="If suspected, whose papers appear and where. Otherwise null.")
    missing_instruments: list[str] = Field(description="Instruments referred to by documents in the packet but not present in it.")


TRIAGE_SYSTEM = """You split an estate-planning packet into its separate documents for the University of Miami Office of Estate and Gift Planning. A person reviews your list before anything else happens, so flag doubt honestly through `confidence`.

You see one block per page: its global number P<n>, its file and page within the file, facts computed by code (printed page number, document-system numbers from the footer, form numbers, dates, how often 'University of Miami' appears, whether the page has a text layer), and its opening and closing lines.

Hard rules:
- Every page belongs to exactly one document. Ranges are contiguous and listed in page order.
- A document never crosses from one file into another. A single file may hold several documents only when it is itself a packet.

Boundary signals: a new title page or heading, a change in the document-system footer number or form number, page numbering restarting at 1, a signature or notary block followed by a new heading.

Types and default treatments:
- trust, restatement, amendment, will, codicil: annotate
- notice_of_administration, letter: context (used for contact details and background)
- court_filing, other: ignore
Propose a different treatment only for a specific reason, and say it in `reason`. For example, a letter that itself makes a gift to the University should be annotated, and an exact duplicate copy of a document can be ignored.

Call record_documents exactly once."""


# ---------------------------------------------------------------------------
# Page records (no model)
# ---------------------------------------------------------------------------

MONTHS = r"(?:January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sept?|Oct|Nov|Dec)\.?"
DATE_RE = re.compile(rf"\b{MONTHS}\s+\d{{1,2}}(?:st|nd|rd|th)?,?\s+\d{{4}}\b|\b\d{{1,2}}/\d{{1,2}}/\d{{2,4}}\b", re.I)
FORM_RE = re.compile(r"\b(?:Bar\s+)?Form\s+(?:No\.?|Number)\s*[A-Z0-9][\w.\-]*", re.I)
DOCNUM_RE = re.compile(r"(?<![\d/.\-])(\d{7,9})(?:[.v]\d{1,2})?(?![\d/])")
PAGE_ALONE_RE = re.compile(r"[-–\s]*(?:page\s+)?(\d{1,3})(?:\s+of\s+\d{1,3})?[-–\s]*", re.I)
PAGE_IN_LINE_RE = re.compile(r"\bpage\s+(\d{1,3})(?:\s+of\s+\d{1,3})?\b", re.I)
UM_RE = re.compile(r"university\s+of\s+miami", re.I)


def _printed_number(head: list[str], foot: list[str]) -> Optional[str]:
    for line in foot[::-1] + head:
        m = PAGE_ALONE_RE.fullmatch(line)
        if m:
            return m.group(1)
    for line in foot[::-1] + head:
        m = PAGE_IN_LINE_RE.search(line)
        if m:
            return m.group(1)
    return None


def extract_pages(files: list[tuple[str, bytes]]) -> list[dict]:
    pages = []
    for fi, (name, data) in enumerate(files):
        reader = PdfReader(BytesIO(data))
        for pi, page in enumerate(reader.pages, start=1):
            text = page.extract_text() or ""
            lines = [l.strip() for l in text.splitlines() if l.strip()]
            head, foot = lines[:3], lines[-4:]
            pages.append({
                "n": len(pages) + 1,
                "file_index": fi,
                "file": name,
                "page_in_file": pi,
                "text": text,
                "has_text": len(text.strip()) > 20,
                "printed": _printed_number(head, foot),
                "doc_numbers": sorted(set(DOCNUM_RE.findall(" ".join(foot)))),
                "form_numbers": sorted(set(m.group(0) for m in FORM_RE.finditer(text))),
                "dates": list(dict.fromkeys(m.group(0) for m in DATE_RE.finditer(text)))[:4],
                "um_count": len(UM_RE.findall(text)),
            })
    return pages


def _preview(p: dict) -> str:
    flat = " ".join(p["text"].split())
    opening = flat[:400]
    closing = flat[-200:] if len(flat) > 600 else ""
    facts = [
        f"printed page: {p['printed'] or 'none'}",
        f"footer doc numbers: {', '.join(p['doc_numbers']) or 'none'}",
        f"form numbers: {', '.join(p['form_numbers']) or 'none'}",
        f"dates: {'; '.join(p['dates']) or 'none'}",
        f"UM mentions: {p['um_count']}",
    ]
    if not p["has_text"]:
        facts.append("NO TEXT LAYER (scan, unreadable until OCR)")
    block = f"P{p['n']} | file {p['file_index'] + 1} '{p['file']}' page {p['page_in_file']}\n{' | '.join(facts)}\nOPENS: {opening}"
    if closing:
        block += f"\nCLOSES: {closing}"
    return block


# ---------------------------------------------------------------------------
# Model call
# ---------------------------------------------------------------------------

def _call_model(pages: list[dict], problems: list[str] | None) -> TriageResult:
    files = sorted({(p["file_index"], p["file"]) for p in pages})
    user = (
        f"{len(files)} file(s), {len(pages)} pages in total:\n"
        + "\n".join(f"- file {i + 1}: '{name}'" for i, name in files)
        + "\n\n" + "\n\n".join(_preview(p) for p in pages)
    )
    if problems:
        user += ("\n\nA previous attempt at this list had these problems. "
                 "Produce a corrected list:\n- " + "\n- ".join(problems))

    client = anthropic.Anthropic(api_key=config.api_key())
    response = client.messages.create(
        model=config.CLAUDE_MODEL,
        max_tokens=config.TRIAGE_MAX_TOKENS,
        system=TRIAGE_SYSTEM,
        tools=[{
            "name": "record_documents",
            "description": "Record the list of documents found in the packet.",
            "input_schema": TriageResult.model_json_schema(),
            "strict": True,
        }],
        tool_choice={"type": "tool", "name": "record_documents"},
        messages=[{"role": "user", "content": user}],
    )
    blocks = [b for b in response.content if b.type == "tool_use"]
    if not blocks:
        raise RuntimeError("Triage returned no document list.")
    return TriageResult.model_validate(blocks[0].input)


# ---------------------------------------------------------------------------
# Checks done by code
# ---------------------------------------------------------------------------

def _valid_date(s: Optional[str]) -> bool:
    if s is None:
        return True
    try:
        date.fromisoformat(s)
        return True
    except ValueError:
        return False


def _ranges(nums: list[int]) -> str:
    out, start = [], None
    for i, n in enumerate(nums):
        if start is None:
            start = n
        if i == len(nums) - 1 or nums[i + 1] != n + 1:
            out.append(f"P{start}" if start == n else f"P{start}-P{n}")
            start = None
    return ", ".join(out)


def check(docs: list[TriageDoc], pages: list[dict]) -> list[str]:
    problems, n = [], len(pages)
    cover = [0] * (n + 1)
    for d in docs:
        s, e = d.start_page, d.end_page
        if s < 1 or e > n or s > e:
            problems.append(f"'{d.title}' has an invalid range P{s}-P{e} (the packet has {n} pages).")
            continue
        if pages[s - 1]["file_index"] != pages[e - 1]["file_index"]:
            problems.append(f"'{d.title}' (P{s}-P{e}) crosses from one file into another.")
        for p in range(s, e + 1):
            cover[p] += 1
        if not _valid_date(d.date):
            problems.append(f"'{d.title}' has an invalid date '{d.date}'; use YYYY-MM-DD or null.")
    gaps = [p for p in range(1, n + 1) if cover[p] == 0]
    overlaps = [p for p in range(1, n + 1) if cover[p] > 1]
    if gaps:
        problems.append(f"These pages belong to no document: {_ranges(gaps)}.")
    if overlaps:
        problems.append(f"These pages belong to more than one document: {_ranges(overlaps)}.")
    return problems


def repair(docs: list[TriageDoc], pages: list[dict]) -> list[dict]:
    """Last resort after a failed retry: give every page one owner, split at
    file boundaries, and flag every row that had to change."""
    n = len(pages)
    owner: list[Optional[int]] = [None] * (n + 1)
    for i, d in enumerate(sorted(range(len(docs)), key=lambda k: docs[k].start_page)):
        dd = docs[d]
        for p in range(max(1, dd.start_page), min(n, dd.end_page) + 1):
            if owner[p] is None:
                owner[p] = d

    out, p = [], 1
    while p <= n:
        o, fi, start = owner[p], pages[p - 1]["file_index"], p
        while p + 1 <= n and owner[p + 1] == o and pages[p]["file_index"] == fi:
            p += 1
        if o is None:
            d = {"start_page": start, "end_page": p, "type": "other",
                 "title": "Unassigned pages", "date": None, "amends": None,
                 "refers_to": [], "contains_um_gift": False, "treatment": "ignore",
                 "reason": "Triage did not place these pages in any document.",
                 "confidence": "low", "boundary_evidence": "",
                 "_attention": ["Triage left these pages unassigned. Merge them into the right document or leave them ignored."]}
        else:
            src = docs[o]
            d = src.model_dump()
            d["start_page"], d["end_page"] = start, p
            d["_attention"] = []
            if (start, p) != (src.start_page, src.end_page):
                d["_attention"].append("Boundaries were adjusted automatically after triage failed its checks.")
            if not _valid_date(src.date):
                d["date"] = None
                d["_attention"].append(f"Triage gave an invalid date ('{src.date}'); please enter it.")
        out.append(d)
        p += 1
    return out


# ---------------------------------------------------------------------------
# Default treatments, chains, attention flags (code)
# ---------------------------------------------------------------------------

def assign_chains(docs: list[dict]) -> None:
    """Group annotate-treated instruments into chains: a trust with its
    amendments and restatements, a will with its codicils."""
    for d in docs:
        d.setdefault("chain", None)
    for family, base_type in (("trust", "trust"), ("will", "will")):
        members = [d for d in docs if FAMILY.get(d["type"]) == family and d["treatment"] == "annotate"]
        if not members:
            continue
        bases = [d for d in members if d["type"] == base_type]
        names: list[str] = []
        for b in bases:
            name = b["title"] or base_type.title()
            k = 2
            while name in names:
                name = f"{b['title']} ({k})"
                k += 1
            names.append(name)
            b["chain"] = name
        for d in members:
            if d in bases:
                continue
            if not names:
                d["chain"] = "Trust" if family == "trust" else "Will"
            elif len(names) == 1:
                d["chain"] = names[0]
            else:
                a = (d.get("amends") or "").lower()
                hits = [n for n in names if a and (a in n.lower() or n.lower() in a)]
                d["chain"] = hits[0] if hits else names[0]
                if not hits:
                    d["attention"].append("Several instruments of this kind are in the packet; check which one this amends.")


def finalize(raw_docs: list[dict], pages: list[dict]) -> list[dict]:
    docs = []
    for i, d in enumerate(raw_docs, start=1):
        rng = pages[d["start_page"] - 1: d["end_page"]]
        default = DEFAULT_TREATMENT[d["type"]]
        attention = list(d.pop("_attention", []))
        um = sum(p["um_count"] for p in rng)
        blank = sum(1 for p in rng if not p["has_text"])
        if d["confidence"] != "high":
            attention.append(f"Triage confidence is {d['confidence']}.")
        if d["treatment"] != default:
            attention.append(f"Treatment differs from the default for a {d['type'].replace('_', ' ')} ({default}).")
        if d["contains_um_gift"] and d["treatment"] != "annotate":
            attention.append("Appears to contain a gift to the University but is not set to annotate.")
        if um and d["treatment"] == "ignore":
            attention.append(f"Mentions the University {um} time(s) but is set to ignore.")
        if blank:
            attention.append(f"{blank} page(s) have no text layer and can't be read until OCR is added.")
        docs.append({
            "id": f"d{i}",
            "start": d["start_page"],
            "end": d["end_page"],
            "file_index": rng[0]["file_index"],
            "type": d["type"],
            "title": d["title"],
            "date": d["date"],
            "amends": d["amends"],
            "refers_to": d["refers_to"],
            "um_gift": d["contains_um_gift"],
            "um_count": um,
            "treatment": d["treatment"],
            "default_treatment": default,
            "reason": d["reason"],
            "confidence": d["confidence"],
            "evidence": d["boundary_evidence"],
            "superseded": False,
            "attention": attention,
        })
    assign_chains(docs)
    return docs


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run(files: list[tuple[str, bytes]]) -> dict:
    pages = extract_pages(files)
    if not pages:
        raise ValueError("No pages found in the uploaded files.")

    result = _call_model(pages, None)
    problems = check(result.documents, pages)
    attempts = 1
    if problems:
        result = _call_model(pages, problems)
        problems = check(result.documents, pages)
        attempts = 2

    if problems:
        raw = repair(result.documents, pages)
    else:
        raw = [d.model_dump() | {"_attention": []} for d in result.documents]
        raw.sort(key=lambda d: d["start_page"])

    return {
        "files": [name for name, _ in files],
        "pages": [{k: p[k] for k in ("n", "file_index", "page_in_file", "text", "printed", "has_text")} for p in pages],
        "documents": finalize(raw, pages),
        "mixed_matters": {"suspected": result.mixed_matters_suspected,
                          "explanation": result.mixed_matters_explanation},
        "missing_instruments": result.missing_instruments,
        "triage_problems": problems,
        "attempts": attempts,
    }
