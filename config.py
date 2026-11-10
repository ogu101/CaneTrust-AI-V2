"""All settings in one place. Paths are anchored to this file so they resolve
the same way locally and on Vercel."""

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")
CLAUDE_EFFORT = os.getenv("CLAUDE_EFFORT", "high")  # annotation only

MAX_TOKENS_PER_REQUEST = 16000  # annotation call
TRIAGE_MAX_TOKENS = 16000       # triage call (one list of documents)

ANNOTATION_TEMPLATE_FILE = ROOT / "templates" / "MiLA_Annotation_Template_v0.xlsx"
EXTRACTION_RULES_PATH = ROOT / "extraction_rules.md"


def api_key() -> str:
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. Add it under Vercel > Project > "
            "Settings > Environment Variables, then redeploy."
        )
    return key
