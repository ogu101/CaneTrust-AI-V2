"""
Two requests, never one held open while a person decides:

  POST /api/triage    PDFs in  -> pages + proposed documents out (JSON)
  POST /api/annotate  confirmed documents + pages in -> .xlsx (or .zip) out

Nothing is stored on the server between the two; the browser holds the state.
"""

import sys
from pathlib import Path

from fastapi import Body, FastAPI, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import annotate  # noqa: E402
import triage    # noqa: E402

app = FastAPI()


@app.get("/")
def home():
    """Serve the UI from the function too, so the home page works even if
    Vercel doesn't map public/index.html to "/"."""
    return FileResponse(ROOT / "public" / "index.html")


@app.get("/api/health")
def health():
    """Open this URL to check the function starts and the key is set."""
    import os
    return {"ok": True, "api_key_set": bool(os.getenv("ANTHROPIC_API_KEY")),
            "template_found": (ROOT / "templates" / "MiLA_Annotation_Template_v0.xlsx").exists()}


def _error(e: Exception) -> JSONResponse:
    code = 400 if isinstance(e, ValueError) else 500
    return JSONResponse({"detail": f"{type(e).__name__}: {e}"}, status_code=code)


@app.post("/api/triage")
async def triage_route(files: list[UploadFile]):
    try:
        data = [(f.filename or f"file{i + 1}.pdf", await f.read()) for i, f in enumerate(files)]
        return triage.run(data)
    except Exception as e:  # surface the real reason in the UI
        return _error(e)


@app.post("/api/annotate")
def annotate_route(payload: dict = Body(...)):
    try:
        name, content, media = annotate.run(payload)
        return Response(content=content, media_type=media,
                        headers={"Content-Disposition": f'attachment; filename="{name}"'})
    except Exception as e:
        return _error(e)
