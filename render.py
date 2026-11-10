"""
Renders a DocumentAnnotation into the "Annotation" sheet layout of
MiLA_Annotation_Template_v0.xlsx.

Plain, deterministic Python, no LLM. `fill_sheet()` fills one worksheet, so
several annotations can be tabs in one workbook; `render_to_workbook()` keeps
the original one-document-per-file behaviour for local runs.

Variable-length sections (defined terms, UM mentions, ...) grow by inserting
rows. Every later section is shifted by the rows inserted above it, and row
heights and the Status dropdown are moved to match.
"""

import copy
import datetime as dt
import re
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.worksheet.datavalidation import DataValidation

from cell_map import HEADER, FIXED_ROWS, VARIABLE_SECTIONS
from schema import DocumentAnnotation, Finding

ANNOTATOR = "MiLA Assistant (LLM draft — attorney review required)"

# The template's dropdown and status tally use these exact labels.
STATUS_LABEL = {"Ambiguous": "Ambiguous — see note"}
STATUS_CHOICES = '"Found,Not in this document,Could not determine,Ambiguous — see note"'

# Sections 4 and 5 carry a Status per row; later sections are free text.
SECTIONS_WITH_STATUS = {"defined_terms", "um_mentions"}

SECTION_ORDER = ["defined_terms", "um_mentions", "found_but_uncategorized",
                 "judgment_calls", "questions_for_client"]


def _get_by_path(annotation: DocumentAnnotation, path: str):
    obj = annotation
    for part in path.split("."):
        obj = getattr(obj, part)
    return obj


def _write_finding(ws, row: int, finding) -> None:
    """finding may be a Finding, an UncategorizedFinding, or a plain string
    (judgment_calls / questions_for_client are free text with no status)."""
    if isinstance(finding, str):
        ws[f"B{row}"] = finding
        return
    ws[f"B{row}"] = getattr(finding, "answer", None) or getattr(finding, "text", None)
    ws[f"C{row}"] = getattr(finding, "citation", None)
    if getattr(finding, "status", None):
        ws[f"D{row}"] = STATUS_LABEL.get(finding.status, finding.status)
    ws[f"E{row}"] = getattr(finding, "note", None)


def _copy_row_style(ws, src_row: int, dst_row: int, max_col: int = 5) -> None:
    for col in range(1, max_col + 1):
        src, dst = ws.cell(row=src_row, column=col), ws.cell(row=dst_row, column=col)
        dst.font = copy.copy(src.font)
        dst.fill = copy.copy(src.fill)
        dst.border = copy.copy(src.border)
        dst.alignment = copy.copy(src.alignment)
        dst.number_format = src.number_format


def _insert_rows(ws, at: int, amount: int, style_row: int) -> None:
    """Insert rows and move row heights with them (openpyxl moves cells but
    not heights)."""
    heights = {r: d.height for r, d in ws.row_dimensions.items() if r >= at and d.height}
    ws.insert_rows(at, amount=amount)
    for r in list(heights):
        ws.row_dimensions[r].height = None
    for r, h in heights.items():
        ws.row_dimensions[r + amount].height = h
    for i in range(amount):
        _copy_row_style(ws, style_row, at + i)
        ws.row_dimensions[at + i].height = ws.row_dimensions[style_row].height


def _next_label(label, n: int):
    """'Term 12' -> 'Term 13', '6.' -> '7.'"""
    m = re.fullmatch(r"(.*?)(\d+)(\.?)\s*", str(label or ""))
    return f"{m.group(1)}{n}{m.group(3)}" if m else label


def _fill_section(ws, key: str, first_row: int, items: list, labeler=None) -> tuple[int, int]:
    """Fill one variable section starting at first_row. Returns (rows used,
    rows inserted)."""
    count = VARIABLE_SECTIONS[key]["placeholder_count"]
    extra = max(0, len(items) - count)
    if extra:
        last = first_row + count - 1
        label = ws[f"A{last}"].value
        _insert_rows(ws, last + 1, extra, style_row=last)
        for i in range(extra):
            ws[f"A{last + 1 + i}"] = _next_label(label, count + 1 + i)

    for i in range(count + extra):
        row = first_row + i
        if i < len(items):
            payload = items[i]
            if labeler:
                label, payload = labeler(items[i])
                if label is not None:
                    ws[f"A{row}"] = label
            _write_finding(ws, row, payload)
        elif key in SECTIONS_WITH_STATUS and ws[f"D{row}"].value is None:
            ws[f"D{row}"] = "Not in this document"
    return count + extra, extra


def fill_sheet(ws, annotation: DocumentAnnotation, annotator: str = ANNOTATOR) -> None:
    ws[HEADER["document_name"]] = annotation.document_name
    ws[HEADER["annotator"]] = annotator
    ws[HEADER["date_completed"]] = dt.date.today().isoformat()

    for path, row in FIXED_ROWS.items():
        _write_finding(ws, row, _get_by_path(annotation, path))

    mentions = [Finding(status=m.finding.status, answer=m.exact_text,
                        citation=m.finding.citation, note=m.finding.note)
                for m in annotation.um_mentions]
    content = {
        "defined_terms": (annotation.defined_terms, lambda t: (t.term, t.finding)),
        "um_mentions": (mentions, None),
        "found_but_uncategorized": (annotation.found_but_uncategorized, None),
        "judgment_calls": (annotation.judgment_calls, None),
        "questions_for_client": (annotation.questions_for_client, None),
    }

    offset = 0
    status_ranges = ["D13:D19", "D21:D23", "D25:D27"]
    for key in SECTION_ORDER:
        items, labeler = content[key]
        first = VARIABLE_SECTIONS[key]["first_row"] + offset
        used, inserted = _fill_section(ws, key, first, items, labeler)
        if key in SECTIONS_WITH_STATUS:
            status_ranges.append(f"D{first}:D{first + used - 1}")
        offset += inserted

    # Rebuild the Status dropdown over the rows as they now stand.
    ws.data_validations.dataValidation = []
    dv = DataValidation(type="list", formula1=STATUS_CHOICES, allow_blank=True)
    for rng in status_ranges:
        dv.add(rng)
    ws.add_data_validation(dv)


def render_to_workbook(annotation: DocumentAnnotation, template_path: str, out_path: str,
                       annotator: str = ANNOTATOR) -> None:
    wb = load_workbook(template_path)
    fill_sheet(wb["Annotation"], annotation, annotator)
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    wb.save(out_path)
