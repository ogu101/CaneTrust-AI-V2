"""
Annotation: confirmed document list -> one workbook with one annotation tab
per chain, named after the document it annotates, plus a Triage tab.

The browser sends back the page texts it received from triage together with
the confirmed documents. Page labels are recomputed here because a reviewer
may have moved boundaries; chains are rebuilt from the confirmed documents;
each chain gets one call to the existing analyze_document(), in parallel.
Every reviewer correction is written into the "Triage" tab as the audit record.
"""

import io
import re
from concurrent.futures import ThreadPoolExecutor
from openpyxl import load_workbook
from openpyxl.styles import Font

import config
from llm import InstrumentLayer, analyze_document
from render import fill_sheet

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


def add_triage_sheet(wb, files: list[str], docs: list[dict], pages: list[dict], changes: list[str]) -> None:
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


def _safe(name: str) -> str:
    return re.sub(r"[^\w\- .]+", "", name).strip()[:80] or "annotation"


def _tab_name(name: str, taken: set[str]) -> str:
    """Excel tab names: at most 31 characters, none of []:*?/\\, unique
    (case-insensitive), and not 'Instructions' or 'Triage'."""
    base = re.sub(r"[\[\]:*?/\\]", "", name).strip().strip("'")[:31].rstrip() or "Annotation"
    tab, k = base, 2
    while tab.lower() in taken:
        suffix = f" ({k})"
        tab, k = base[:31 - len(suffix)].rstrip() + suffix, k + 1
    taken.add(tab.lower())
    return tab


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
        return analyze_document(
            name, layers[name],
            api_key=key, model=config.CLAUDE_MODEL, effort=config.CLAUDE_EFFORT,
            max_tokens=config.MAX_TOKENS_PER_REQUEST, rules_path=config.EXTRACTION_RULES_PATH,
        )

    names = list(layers)
    with ThreadPoolExecutor(max_workers=min(4, len(names))) as pool:
        annotations = list(pool.map(one, names))

    # One workbook: Instructions, one tab per chain, then Triage.
    wb = load_workbook(config.ANNOTATION_TEMPLATE_FILE)
    template = wb["Annotation"]
    taken = {"instructions", "triage", "annotation"}
    for name, annotation in zip(names, annotations):
        ws = wb.copy_worksheet(template)
        ws.title = _tab_name(name, taken)
        ws.freeze_panes = template.freeze_panes
        ws.sheet_view.showGridLines = template.sheet_view.showGridLines
        ws.print_title_rows = template.print_title_rows
        fill_sheet(ws, annotation)
    wb.remove(template)
    add_triage_sheet(wb, files, docs, pages, changes)
    wb.active = 1  # open on the first annotation tab

    buf = io.BytesIO()
    wb.save(buf)
    base = _safe(names[0]) if len(names) == 1 else _safe(files[0].rsplit(".", 1)[0] if files else "packet")
    return f"{base}_annotation.xlsx", buf.getvalue(), XLSX
