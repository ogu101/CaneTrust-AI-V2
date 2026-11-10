"""
Single-call extraction: one analyze_documents tool call per document.

Prompt layout (why this order):
  system[0]  = extraction_rules.md, cache_control="ephemeral"  -> identical on
               every call in the batch, so it's cached once and reused across
               the whole corpus instead of being paid for on every document.
  tools[0]   = the DocumentAnnotation JSON schema, cache_control="ephemeral"
               -> also identical every call; field-level instructions live
               here (see schema.py) so they travel with the field they
               govern instead of padding the system prompt.
  user       = the one thing that's actually different per call: the
               instrument chain + the document text. Not cached, not
               compressed — a 100-page trust needs to be there in full for
               citations to be checkable.
"""

import json
from dataclasses import dataclass
from pathlib import Path

import anthropic
from schema import DocumentAnnotation


@dataclass
class InstrumentLayer:
    label: str          # e.g. "Fifth Amendment (Jul 14, 2017)"
    status: str         # e.g. "OPERATIVE", "REVOKED by Fifth Amendment", "IN FORCE"
    text: str


def render_instrument_chain(layers: list[InstrumentLayer]) -> str:
    """Explicit chronological labeling is what actually solves the layered-
    instruments problem — the model shouldn't have to infer precedence from
    document order alone."""
    parts = []
    for layer in layers:
        parts.append(f"--- {layer.label} [{layer.status}] ---\n{layer.text}")
    return "\n\n".join(parts)


def build_request(
    document_name: str,
    layers: list[InstrumentLayer],
    *,
    model: str,
    effort: str,
    max_tokens: int,
    rules_path: Path,
) -> dict:
    system_text = rules_path.read_text()
    tool_schema = DocumentAnnotation.model_json_schema()

    user_text = (
        f"Document: {document_name}\n\n"
        f"Instrument chain, chronological. Later items may amend, restate, or "
        f"revoke earlier ones — read all of them before answering.\n\n"
        f"{render_instrument_chain(layers)}"
    )

    return dict(
        model=model,
        max_tokens=max_tokens,
        # Adaptive thinking (not manual "enabled"/budget_tokens thinking) is
        # required here specifically because it's the mode that still
        # supports forced tool_choice below — manual extended thinking does not.
        thinking={"type": "adaptive"},
        output_config={"effort": effort},
        system=[
            {
                "type": "text",
                "text": system_text,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        tools=[
            {
                "name": "analyze_documents",
                "description": (
                    "Record the complete structured extraction for this document. "
                    "Call this exactly once, after reading the full instrument chain."
                ),
                "input_schema": tool_schema,
                "strict": True,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        tool_choice={"type": "tool", "name": "analyze_documents"},
        messages=[{"role": "user", "content": user_text}],
    )


def analyze_document(
    document_name: str,
    layers: list[InstrumentLayer],
    *,
    api_key: str,
    model: str,
    effort: str,
    max_tokens: int,
    rules_path: Path,
) -> DocumentAnnotation:
    client = anthropic.Anthropic(api_key=api_key)
    request = build_request(
        document_name, layers,
        model=model, effort=effort, max_tokens=max_tokens, rules_path=rules_path,
    )
    response = client.messages.create(**request)

    tool_use_blocks = [b for b in response.content if b.type == "tool_use"]
    if not tool_use_blocks:
        raise RuntimeError(f"No tool_use block returned for {document_name!r}: {response.content}")

    raw = tool_use_blocks[0].input

    debug_path = Path("/tmp") / "last_raw_output.json"
    debug_path.parent.mkdir(parents=True, exist_ok=True)
    debug_path.write_text(json.dumps(raw, indent=2))
    print(f"[debug] wrote raw tool output to {debug_path}")
    
    annotation = DocumentAnnotation.model_validate(raw)

    usage = response.usage
    print(
        f"[{document_name}] input={usage.input_tokens} "
        f"cache_read={getattr(usage, 'cache_read_input_tokens', 0)} "
        f"cache_write={getattr(usage, 'cache_creation_input_tokens', 0)} "
        f"output={usage.output_tokens}"
    )

    return annotation

