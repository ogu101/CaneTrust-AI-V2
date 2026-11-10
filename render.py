"""
Renders a DocumentAnnotation into a copy of MiLA_Annotation_Template_v0.xlsx.

This is plain, deterministic Python. It runs once per document, costs no
tokens, and is the only place that knows about cell coordinates — if the
template changes, only cell_map.py and this file need to change, not the
prompt or the schema's meaning.
"""

import copy
import datetime as dt
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter

from cell_map import (
    HEADER,
    FIXED_ROWS,
    VARIABLE_SECTIONS,
    STATUS_TALLY_FORMULA_CELLS,
    STATUS_TALLY_RANGE_TEMPLATE,
)
from schema import DocumentAnnotation, Finding


def _get_by_path(annotation: DocumentAnnotation, path: str) -> Finding:
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

    answer = getattr(finding, "answer", None) or getattr(finding, "text", None)
    ws[f"B{row}"] = answer
    ws[f"C{row}"] = getattr(finding, "citation", None)
    if hasattr(finding, "status"):
        ws[f"D{row}"] = finding.status
    ws[f"E{row}"] = getattr(finding, "note", None)


def _copy_row_style(ws, src_row: int, dst_row: int, max_col: int = 5) -> None:
    for col in range(1, max_col + 1):
        src = ws.cell(row=src_row, column=col)
        dst = ws.cell(row=dst_row, column=col)
        dst.font = copy.copy(src.font)
        dst.fill = copy.copy(src.fill)
        dst.border = copy.copy(src.border)
        dst.alignment = copy.copy(src.alignment)
        dst.number_format = src.number_format


def _fill_variable_section(ws, section_key: str, items: list, item_labeler=None) -> int:
    """Writes `items` into the section's placeholder rows, inserting extra
    rows if there are more items than placeholders. Returns the number of
    rows the section grew by, so later sections can be offset."""
    cfg = VARIABLE_SECTIONS[section_key]
    first_row = cfg["first_row"]
    placeholder_count = cfg["placeholder_count"]
    growth = 0

    if len(items) > placeholder_count:
        extra = len(items) - placeholder_count
        insert_at = first_row + placeholder_count  # just after the last placeholder
        ws.insert_rows(insert_at, amount=extra)
        for i in range(extra):
            _copy_row_style(ws, first_row + placeholder_count - 1, insert_at + i)
        growth = extra

    total_rows = placeholder_count + growth
    for i in range(total_rows):
        row = first_row + i
        if i < len(items):
            if item_labeler:
                label, payload = item_labeler(items[i])
                if label is not None:
                    ws[f"A{row}"] = label
            else:
                payload = items[i]
            _write_finding(ws, row, payload)
        else:
            # Unused placeholder row: mark not-found rather than leaving it
            # ambiguous-looking, matching how annotators treated unused rows.
            if hasattr(ws[f"D{row}"], "value") and ws[f"D{row}"].value is None:
                ws[f"D{row}"] = "Not in this document"
    return growth


def render_to_workbook(
    annotation: DocumentAnnotation,
    template_path: str,
    out_path: str,
    annotator: str = "MiLA Assistant (LLM draft — attorney review required)",
) -> None:
    wb = load_workbook(template_path)
    ws = wb["Annotation"]

    ws[HEADER["document_name"]] = annotation.document_name
    ws[HEADER["annotator"]] = annotator
    ws[HEADER["date_completed"]] = dt.date.today().isoformat()

    for path, row in FIXED_ROWS.items():
        _write_finding(ws, row, _get_by_path(annotation, path))

    row_growth = 0

    def term_labeler(dt_):
        return dt_.term, dt_.finding

    def mention_labeler(m):
        return None, m  # keep "Mention N" label, UMMention has no separate label

    um_mentions_as_findings = [
        Finding(status=m.finding.status, answer=m.exact_text,
                citation=m.finding.citation, note=m.finding.note)
        for m in annotation.um_mentions
    ]

    row_growth += _fill_variable_section(ws, "defined_terms", annotation.defined_terms, term_labeler)
    row_growth += _fill_variable_section(ws, "um_mentions", um_mentions_as_findings)
    row_growth += _fill_variable_section(ws, "found_but_uncategorized", annotation.found_but_uncategorized)
    row_growth += _fill_variable_section(ws, "judgment_calls", annotation.judgment_calls)
    row_growth += _fill_variable_section(ws, "questions_for_client", annotation.questions_for_client)

    if row_growth:
        # The status tally's COUNTIF/COUNTBLANK ranges (D12:D200) already cover
        # up to row 200, well past any realistic growth from a handful of
        # extra defined terms or UM mentions — no formula edit needed. If a
        # document ever pushes past row 200, widen STATUS_TALLY_RANGE_TEMPLATE
        # in cell_map.py and rewrite the formulas here.
        pass

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    wb.save(out_path)
