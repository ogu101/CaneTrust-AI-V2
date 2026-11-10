"""
Structured output schema for the analyze_documents tool call.

Every field description below is scoped instruction for exactly that field —
pulled from ParalegalInstructions.docx and attached where it's used, instead
of living as a numbered list in the system prompt the model has to remember
across a 100-page document.
"""

from typing import Literal, Optional
from pydantic import BaseModel, Field

Status = Literal["Found", "Not in this document", "Could not determine", "Ambiguous"]


class StrictModel(BaseModel):
    """Base class enforcing additionalProperties: false at every nesting
    level, required for Anthropic's strict tool use (constrained decoding
    that guarantees all required fields are actually present)."""
    model_config = {"extra": "forbid"}

STATUS_DESCRIPTION = (
    "Found: the document answers this — put the answer in `answer`, its location "
    "in `citation`. "
    "Not in this document: the document genuinely does not address this — a real "
    "finding, not a gap. "
    "Could not determine: it may be here but you could not establish it (unreadable "
    "scan, a missing referenced instrument, unclear drafting) — say why in `note`. "
    "Ambiguous: more than one defensible reading remains. Put both readings in "
    "`answer`, cite both locations in `citation`, and in `note` state exactly what "
    "conflicts, which provisions are involved, whether one is later in date, whether "
    "it expressly revokes the earlier one, and why it isn't resolved. Never choose "
    "one reading silently."
)


class Finding(StrictModel):
    status: Status = Field(description=STATUS_DESCRIPTION)
    answer: Optional[str] = Field(
        default=None, description="The finding itself. Quote verbatim where the "
        "field calls for exact language; otherwise state plainly. Leave null if "
        "status is not 'Found' or 'Ambiguous'."
    )
    citation: Optional[str] = Field(
        default=None, description="Article / section / page, e.g. 'Article 11 / "
        "§1 / Page 2'. Cite every location involved if status is 'Ambiguous'."
    )
    note: Optional[str] = Field(
        default=None, description="Judgment call, conflict explanation, or reason "
        "a determination could not be made. Required if status is 'Could not "
        "determine' or 'Ambiguous'."
    )


class BequestLanguage(StrictModel):
    location: Finding = Field(description=
        "Article/section/page where the bequest language appears — the language "
        "that disposes of property to a beneficiary. Common forms: 'the Trustor "
        "bequeaths', 'our Trustee shall divide the balance as follows', 'I give/"
        "devise', 'I bequeath', 'my Trustee shall distribute', 'the Trustee shall "
        "hold in trust for'. If the document uses none of these but still disposes "
        "of property to UM, use that language anyway and say so in `note`.")
    verbatim_text: Finding = Field(description=
        "The complete bequest language, quoted exactly as written. No paraphrase.")
    plain_english_summary: Finding = Field(description=
        "In plain English: what does the grantor intend the University to receive? "
        "State whether the gift is a specific dollar amount, a percentage, specific "
        "property, income from the trust, part or all of the residue, or the "
        "remainder after other distributions.")
    trustee_executor_names: Finding = Field(description=
        "Currently serving trustee(s); successor trustees in order of service; "
        "executor or personal representative if identified.")
    trustee_executor_contact: Finding = Field(description=
        "Address, phone, and/or email for each person or institution named above, "
        "if the document provides it. If only the drafting attorney's contact "
        "info appears, say so — that is not the trustee's contact info.")
    um_receives: Finding = Field(description=
        "Everything the University is designated to receive, combined. If a "
        "percentage, state whether it's of the gross estate, net estate, residue, "
        "or another defined fund. Note the exact name/school/department/fund used.")
    trigger_event: Finding = Field(description=
        "What event causes UM's interest to become payable: must the grantor die "
        "first, must another beneficiary die first, are there multiple stages, "
        "does the trustee have discretion to delay, must debts/taxes/other gifts "
        "be paid first.")


class IncomeBeneficiaries(StrictModel):
    count: Finding = Field(description=
        "How many income or lifetime beneficiaries are named. Use status "
        "'Not in this document' if there are none.")
    names_and_relationship: Finding = Field(description=
        "Full name of each income beneficiary, and their stated relationship to "
        "the grantor if given.")
    timing_implication: Finding = Field(description=
        "What each beneficiary's interest implies for when the University "
        "receives its gift — does the interest last for life, a fixed period, "
        "or until another event?")


class RestrictionsContingencies(StrictModel):
    primary_or_contingent: Finding = Field(description=
        "Is the University a primary beneficiary or only a contingent one?")
    conditions_precedent: Finding = Field(description=
        "What must happen before UM becomes entitled to receive the gift? "
        "Could another beneficiary receive the property instead? Is the gift "
        "subject to a survival requirement?")
    other_restrictions: Finding = Field(description=
        "Other restrictions/limitations: conditions on UM's name, tax-exempt "
        "status, merger, or continued existence; restriction to a specific "
        "purpose/school/program/fund; an alternate charitable beneficiary if the "
        "purpose becomes impossible; trustee discretion to redirect/reduce/"
        "postpone/eliminate the gift; any conflict between the original "
        "instrument and later amendments.")


class DefinedTerm(StrictModel):
    term: str = Field(description="The defined term itself, e.g. 'Trust Estate', "
        "'Residuary Trust', 'Net Income', 'Descendants', 'Charitable Beneficiary'.")
    finding: Finding = Field(description=
        "Where it's defined and its exact definition as stated. In `note`, say "
        "whether the definition changes the amount, timing, priority, or "
        "conditions of UM's gift, and whether it's defined in another document "
        "that must be obtained.")


class UMMention(StrictModel):
    exact_text: str = Field(description=
        "The exact name used for the University in this occurrence, plus enough "
        "surrounding text to show context (the sentence or clause it appears in). "
        "Include named schools, colleges, departments, or funds.")
    finding: Finding = Field(description=
        "Section reference for this occurrence. Note in `note` if this mention "
        "conflicts with, or may not refer to, the same institution as other "
        "mentions.")


class UncategorizedFinding(StrictModel):
    text: str = Field(description=
        "Material fact that doesn't fit categories 1-5 but that a reviewing "
        "attorney should see — e.g. a revocation of a prior amendment, a "
        "postponement-of-distribution mechanism, a power of appointment, a "
        "no-contest clause.")
    citation: Optional[str] = Field(default=None)
    note: Optional[str] = Field(default=None, description=
        "Why this matters / what it does.")


class DocumentAnnotation(StrictModel):
    document_name: str
    bequest_language: BequestLanguage
    income_beneficiaries: IncomeBeneficiaries
    restrictions: RestrictionsContingencies
    defined_terms: list[DefinedTerm] = Field(default_factory=list, description=
        "One entry per formally defined term that affects UM's interest. Only "
        "emit terms you actually found — do not pad with empty entries.")
    um_mentions: list[UMMention] = Field(default_factory=list, description=
        "One entry per occurrence of the University's name or a variant, "
        "including named schools/units. Only emit occurrences you actually found.")
    found_but_uncategorized: list[UncategorizedFinding] = Field(default_factory=list)
    judgment_calls: list[str] = Field(default_factory=list, description=
        "Every point where you had to decide rather than read — e.g. deciding "
        "which of two amendments controls, or that two differently named funds "
        "refer to the same gift.")
    questions_for_client: list[str] = Field(default_factory=list, description=
        "Questions only the Office of Estate and Gift Planning can answer — "
        "phrase each as a direct, answerable question, per the examples in the "
        "conflict-handling procedure.")