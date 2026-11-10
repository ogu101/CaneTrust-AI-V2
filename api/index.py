"""
Two requests, never one held open while a person decides:

  POST /api/triage    PDFs in  -> pages + proposed documents out (JSON)
  POST /api/annotate  confirmed documents + pages in -> .xlsx (or .zip) out

Nothing is stored on the server between the two; the browser holds the state.
"""

import sys
from pathlib import Path

from fastapi import Body, FastAPI, UploadFile
from fastapi.responses import JSONResponse, Response

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import annotate  # noqa: E402
import triage    # noqa: E402

app = FastAPI()


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
