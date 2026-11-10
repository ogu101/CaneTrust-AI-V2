# CaneTrust AI: packet triage + annotation

Upload one packet PDF or separate trust, will and amendment PDFs. The app
finds the documents in them, shows them on a confirm screen, then fills the
MiLA annotation template for each instrument chain.

## Flow

1. **Triage** (`POST /api/triage`). The browser extracts each page's text with
   pdf.js and sends only the text, so the PDFs never leave the browser. Code
   computes the
   printed page number, footer document numbers, form numbers, dates and
   University mentions. One Claude call over short page previews returns the
   document list. Code checks it (every page covered once, nothing crossing
   files, valid dates), retries once with the problems stated, and if it still
   fails, repairs the list and flags those rows.
2. **Confirm** (browser). Rows needing a decision are amber. Change type,
   title, date, treatment (annotate / context / ignore), chain, superseded;
   split or merge page ranges. A single high-confidence document with no flags
   skips this screen.
3. **Annotate** (`POST /api/annotate`). Page labels are recomputed, chains
   rebuilt (trust + amendments, will + codicils, ordered by date), and each
   chain runs through the original `analyze_document()` in parallel. Context
   documents are attached to every chain. The result is one `.xlsx`: the
   Instructions tab, one annotation tab per chain named after its document,
   and a **Triage** tab with the confirmed documents and every reviewer
   correction.

Nothing is stored on the server; the browser holds the pages between steps.

## Files

| File | Role |
|---|---|
| `api/index.py` | FastAPI app, the two routes |
| `triage.py` | Page facts, triage call, checks, repair, chains, flags |
| `annotate.py` | Page labels, chain layers, parallel annotation, Triage sheet |
| `llm.py`, `schema.py`, `render.py`, `cell_map.py` | Unchanged from CaneTrust-AI |
| `extraction_rules.md` | Unchanged, plus a section on page markers and context documents |
| `config.py` | Model, effort, token limits, paths |
| `public/index.html` | Upload, confirm and result screens |

## Deploy

1. Push this folder to a new GitHub repo.
2. In Vercel: **Add New > Project**, import the repo, keep the defaults.
3. Add `ANTHROPIC_API_KEY` under **Settings > Environment Variables**, then redeploy.

Local: `pip install -r requirements.txt`, put the key in `.env`, then
`uvicorn api.index:app --reload` and open `public/index.html` via any static
server pointed at the same origin (or just test on a Vercel preview).

## Limits to know

- **Request size:** Vercel caps a request at 4.5 MB. Only text is sent, so a
  packet of several hundred pages fits; the page warns before sending if not.
- **Time:** functions are set to 300 s (`vercel.json`). If a long chain times out on Pro, raise `maxDuration` to 800.
- **Scans:** pages without a text layer are flagged; OCR isn't in yet.
- **Very long packets:** triage is a single call. Past roughly 500 pages it would need overlapping windows, which aren't built.
- **Strict tool use + forced tool choice:** triage uses them without thinking; annotation keeps the original settings.
