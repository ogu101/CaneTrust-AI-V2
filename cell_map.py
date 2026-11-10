"""
Row anchors for MiLA_Annotation_Template_v0.xlsx, sheet "Annotation".
Built once by inspecting the actual file — not regenerated per document.
If the legal team revises the template, only this file needs to change.

Columns are fixed across every row: A=Item label, B=Answer, C=Where
(citation), D=Status, E=Judgment call / note.
"""

HEADER = {
    "document_name": "B4",
    "annotator": "B5",
    "date_completed": "B6",
    "hours": "B7",
}

# Fixed single-row fields: dotted path into DocumentAnnotation -> row number.
FIXED_ROWS = {
    "bequest_language.location": 13,
    "bequest_language.verbatim_text": 14,
    "bequest_language.plain_english_summary": 15,
    "bequest_language.trustee_executor_names": 16,
    "bequest_language.trustee_executor_contact": 17,
    "bequest_language.um_receives": 18,
    "bequest_language.trigger_event": 19,
    "income_beneficiaries.count": 21,
    "income_beneficiaries.names_and_relationship": 22,
    "income_beneficiaries.timing_implication": 23,
    "restrictions.primary_or_contingent": 25,
    "restrictions.conditions_precedent": 26,
    "restrictions.other_restrictions": 27,
}

# Variable-length sections: (first_placeholder_row, last_placeholder_row,
# next_fixed_row_after_section). The template pre-allocates a guessed number
# of rows; the renderer fills as many as it needs and inserts more if the
# model found more than that, using next_fixed_row_after_section as the
# insertion point.
VARIABLE_SECTIONS = {
    "defined_terms": dict(first_row=29, placeholder_count=12, next_section_row=41),
    "um_mentions": dict(first_row=42, placeholder_count=12, next_section_row=54),
    "found_but_uncategorized": dict(first_row=55, placeholder_count=6, next_section_row=61),
    "judgment_calls": dict(first_row=62, placeholder_count=10, next_section_row=72),
    "questions_for_client": dict(first_row=73, placeholder_count=7, next_section_row=80),
}

STATUS_TALLY_FORMULA_CELLS = ["B9", "C9", "D9", "E9", "F9"]
STATUS_TALLY_RANGE_TEMPLATE = "D12:D200"  # range referenced inside those formulas
