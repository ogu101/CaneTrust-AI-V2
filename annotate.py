"""
Annotation: confirmed document list -> one annotation workbook per chain.

The browser sends back the page texts it received from triage together with
the confirmed documents. Page labels are recomputed here because a reviewer
may have moved boundaries; chains are rebuilt from the confirmed documents;
each chain gets one call to the existing analyze_document(), in parallel.
Every reviewer correction is written into a "Triage" sheet in each workbook
as the audit record.
"""

import io
import re
import tempfile
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.styles import Font

import config
from llm import InstrumentLayer, analyze_document
from render import render_to_workbook

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
TRACKED = ("start", "end", "type", "title", "date", "treatment", "chain", "superseded")


def page_labels(doc: dict, pages: list[dict]) -> list[tuple[str, dict]]:
    out = []
    for i, p in enumerate(pages[doc["start"] - 1: doc["end"]], start=1):
        label = f"Page {p['printed']}" if p.get("printed") else f"Unnumbered page {i} of this document"
        out.append((label, p))
    return out


def doc_text(doc: dict, pages: list[dict]) -> str:
    parts = []
    for label, p in page_labels(doc, pages):
        body = p["text"].strip() or "(No text layer on this page. It can't be read until OCR is added.)"
        parts.append(f"[{label}]\n{body}")
    return "\n\n".join(parts)


def printed_range(doc: dict, pages: list[dict]) -> str:
    labels = [lab for lab, _ in page_labels(doc, pages)]
    return labels[0] if len(labels) == 1 else f"{labels[0]} to {labels[-1]}"


def build_layers(docs: list[dict], pages: list[dict]) -> dict[str, list[InstrumentLayer]]:
    annotate = [d for d in docs if d["treatment"] == "annotate"]
    context = sorted((d for d in docs if d["treatment"] == "context"), key=lambda d: d["start"])
    chains: dict[str, list[dict]] = {}
    for d in annotate:
        chains.setdefault(d.get("chain") or d["title"], []).append(d)

    layers: dict[str, list[InstrumentLayer]] = {}
    for name, members in chains.items():
        members.sort(key=lambda d: (d.get("date") or "9999-99-99", d["start"]))
        chain_layers = []
        for i, d in enumerate(members):
            if d.get("superseded"):
                status = "SUPERSEDED (marked by reviewer)"
            elif i == 0:
                status = "EARLIEST INSTRUMENT IN THIS CHAIN"
            else:
                status = "LATER INSTRUMENT (may amend, restate, or revoke the above)"
            chain_layers.append(InstrumentLayer(
                label=f"{d['title']}, {d.get('date') or 'undated'}, {printed_range(d, pages)}",
                status=status,
                text=doc_text(d, pages),
            ))
        for d in context:
            chain_layers.append(InstrumentLayer(
                label=f"{d['title']}, {d.get('date') or 'undated'}, {printed_range(d, pages)}",
                status="CONTEXT ONLY (not a dispositive instrument)",
                text=doc_text(d, pages),
            ))
        layers[name] = chain_layers
    return layers


def corrections(proposed: list[dict], confirmed: list[dict]) -> list[str]:
    before = {d["id"]: d for d in proposed}
    after_ids = {d["id"] for d in confirmed}
    out = []
    for d in confirmed:
        p = before.get(d["id"])
        if p is None:
            out.append(f"{d['title']} (P{d['start']}-P{d['end']}): created by reviewer (split).")
            continue
        for f in TRACKED:
            if p.get(f) != d.get(f):
                out.append(f"{d['title']}: {f} changed from {p.get(f)!r} to {d.get(f)!r}.")
    for i, p in before.items():
        if i not in after_ids:
            out.append(f"{p['title']} (P{p['start']}-P{p['end']}): merged into another document by reviewer.")
    return out


def add_triage_sheet(path: Path, files: list[str], docs: list[dict], pages: list[dict], changes: list[str]) -> None:
    wb = load_workbook(path)
    ws = wb.create_sheet("Triage")
    bold = Font(bold=True)
    ws.append(["Confirmed documents (file order)"])
    ws["A1"].font = bold
    header = ["File", "Pages in file", "Printed pages", "Type", "Title", "Date", "Treatment", "Chain", "Superseded", "Triage reason"]
    ws.append(header)
    for c in ws[2]:
        c.font = bold
    for d in sorted(docs, key=lambda d: d["start"]):
        first, last = pages[d["start"] - 1], pages[d["end"] - 1]
        ws.append([
            files[first["file_index"]] if first["file_index"] < len(files) else "",
            f"{first['page_in_file']}-{last['page_in_file']}",
            printed_range(d, pages), d["type"], d["title"], d.get("date") or "",
            d["treatment"], d.get("chain") or "", "yes" if d.get("superseded") else "",
            d.get("reason") or "",
        ])
    ws.append([])
    ws.append(["Reviewer corrections to the triage proposal"])
    ws.cell(row=ws.max_row, column=1).font = bold
    for line in changes or ["None."]:
        ws.append([line])
    for col, width in zip("ABCDEFGHIJ", (28, 12, 26, 18, 44, 12, 11, 30, 11, 60)):
        ws.column_dimensions[col].width = width
    wb.save(path)


def _safe(name: str) -> str:
    return re.sub(r"[^\w\- .]+", "", name).strip()[:80] or "annotation"


def run(payload: dict) -> tuple[str, bytes, str]:
    pages = payload["pages"]
    docs = payload["documents"]
    files = payload.get("files", [])
    n = len(pages)
    for d in docs:
        if not (1 <= d["start"] <= d["end"] <= n):
            raise ValueError(f"'{d['title']}' has an invalid page range.")

    layers = build_layers(docs, pages)
    if not layers:
        raise ValueError("No document is set to Annotate, so there is nothing to annotate.")

    changes = corrections(payload.get("proposed", []), docs)
    key = config.api_key()

    def one(name: str):
        annotation = analyze_document(
            name, layers[name],
            api_key=key, model=config.CLAUDE_MODEL, effort=config.CLAUDE_EFFORT,
            max_tokens=config.MAX_TOKENS_PER_REQUEST, rules_path=config.EXTRACTION_RULES_PATH,
        )
        out = Path(tempfile.mkdtemp()) / f"{_safe(name)}_annotation.xlsx"
        render_to_workbook(annotation, str(config.ANNOTATION_TEMPLATE_FILE), str(out))
        add_triage_sheet(out, files, docs, pages, changes)
        return out

    with ThreadPoolExecutor(max_workers=min(4, len(layers))) as pool:
        outputs = list(pool.map(one, layers))

    if len(outputs) == 1:
        return outputs[0].name, outputs[0].read_bytes(), XLSX

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for path in outputs:
            z.write(path, path.name)
    base = _safe(Path(files[0]).stem) if files else "packet"
    return f"{base}_annotations.zip", buf.getvalue(), "application/zip"
