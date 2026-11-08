# pypdfium2 port — verification record (2026-11-03)

**Branch:** `feat/pdfium-rasterizer` (from `main` `e716903`)
**Commit under test:** `da841b9` plus the documentation in this commit
**Host:** Windows 11 Home 10.0.26300, x64, Python 3.11.17 (uv)
**Production target:** **Windows 11 x64** (validated 2026-11-04). Windows Server
2016 is legacy and is no longer the target.

This record closes the renderer acceptance gates from
[`PDF_RENDERER_EVALUATION_2026-11-03.md`](PDF_RENDERER_EVALUATION_2026-11-03.md) §12.1.
All numbers below were measured on this host, not inferred. The separate
deployment gates (NSSM service session, reboot, live sync and restore) are in
[`WIN11_DEPLOYMENT_VALIDATION_2026-11-04.md`](WIN11_DEPLOYMENT_VALIDATION_2026-11-04.md).

---

## 1. Runtime-only installation (what `uv sync --no-dev` gives production)

A clean venv was built from `uv export --no-dev` (411 pinned lines, no dev
dependencies) and Python 3.11:

| Check | Result |
|---|---|
| Python | 3.11.17 |
| `pypdfium2` | 5.14.0 |
| PDFium | 156.0.8076.0 |
| Pillow | 13.0.0 |
| `PyMuPDF` present | **no** |
| Render `SIV-RHO-26-233.pdf` @150 DPI | **1275×1651, mode RGB** |
| `import app.main` | **OK** |

`pdfium.dll` import table (measured with `pefile` on the installed wheel):

```
advapi32.dll, gdi32.dll, kernel32.dll, user32.dll
```

No `VCRUNTIME140`, no `MSVCP140`. PE optional-header OS version is **10.0**, so
Windows 11 x64 (10.0.26300 here) satisfies the declared minimum. Windows Server
2016 is no longer a target and its clean-image gate is retired.

### Live HTTP smoke (the real service, not TestClient)

Started with the runtime-only venv and a temporary config (paths, DB and logs under
`%TEMP%\aam_win11`, port 8791, poller off):

| Request | Result |
|---|---|
| `GET /health` | 200 `{"status":"ok","version":"0.1.0"}` |
| `GET /manual/merger` | 200, 10,207 bytes |
| `POST /manual/merge` (two real PDFs) | 200, `application/pdf`, 448,129 bytes |
| Returned packet, pypdf | 2 pages, boxes `(0,0,612,792)` each |
| Returned packet, PDFium | 2 pages, both render 612×792 RGB |

This exercises the manual merger end to end and proves its pypdf output is readable
by the new renderer.

---

## 2. Renderer acceptance (real corpus)

All **695** `data/real_samples` PDFs, production `_rasterize_pdf`:

| Gate | Result |
|---|---|
| Opened + rendered | **693/695** |
| Failures | the 2 known corrupt files (`SIV-DTS-25-584-2/3`); the same two fail in PyMuPDF |
| Page-count parity with pypdf | **0 mismatches** on all 693 valid files |
| Page-box repair | fires on **exactly 1 page in 695** (`SIV-RHO-26-234B`) |
| 234B at 150 DPI | **1240×1753** (unrepaired PDFium would give 1275×1651 and clip) |
| 234B vs PyMuPDF | MAD **6.31**, ink **10.87%** vs **10.07%**; both extrema (0,255) |
| Encrypted user password | `PdfRasterError: … password-protected` (fail closed) |
| Owner-only encrypted | opens and renders |
| Corrupt / truncated / empty | `PdfRasterError`, no crash |
| Thread stress | 4 threads, deterministic lock check `max_inside == 1`, 0 errors |
| Memory | ~45–47 MB RSS steady over 500 renders, no growth |
| 13-page text | 0.83 s (64 ms/page) |
| 19-page scan | 2.51 s (132 ms/page) |
| 8-page rotated scan | 1.03 s (128 ms/page) |

---

## 3. VLM extraction on real vendor documents

Production `_call_vlm` (real PDFium rasteriser, real OpenRouter model
`openai/gpt-6-luna`, 150 DPI, effort high), scored against the human-recorded
`prompt_improvement/eval/gold.json`.

### 3.1 Five-document spot check (printed ground truth)

| Document | Type | Identifier | PO | Lines / quantities |
|---|---|---|---|---|
| `SIV-RHO-26-233.pdf` | SI | `SIV-RHO-26-233` | `4500362256` | 4 lines, 12/1/1/5, per-line DN `GDN-RHO-26-92` |
| `SIV-RAK-25-2967-Noble_Part1.pdf` | SI | `SIV-RAK-25-2967` | `300145905` | 1 line, 4 |
| `PO_2112_15676_0_US.pdf` | PO | `15676 , 0` → normalises `15676` | null | first two lines 10/5 |
| `DN2182.pdf` (scan) | DN | `GDN-RHO-26-2182` | `LPO26/2360` | 1 line, 400 |
| `D7519-PO-233130-055-01.pdf` | PO | `D7519-PO-233130-055-01` | null | — |

All five were correct. Two apparent mismatches were checked against the page:
`15676 , 0` is the verbatim print (`normalize_po_no` → `15676`), and the scan
prints `LPO26/2360` (the old PyMuPDF baseline returned `LPO2612360`, dropping the
slash; gold says `LPO26/2360`).

### 3.2 20-document gold run

Documents: `1, 2, 18, 19, 20, 21, 22, 23, 81, 82, 83, 84, 85, 86, 113, 194, 195,
258, 268, 272` — 6 PO, 8 DN, 6 SI, including both ambiguous DN cases and the
repaired 234B.

| Renderer / prompt | Pass | Notes |
|---|---:|---|
| pypdfium2 + current prompt | **17/20** | total 136.8 s |
| PyMuPDF + cached older prompt (`c826512ea909`) | **16/20** | not a clean A/B: the current prompt hash is `5dabfcadca38` |

The new run fixes the baseline's failures on `DN2182` (slash), `SIV-DTS-25-452-pages-2`
and `SIV-DTS-25-517-pages-1`. The three first-run misses were re-run three times:

| Doc | Miss | Repeats |
|---|---|---|
| `SIV-ARS-25-7970-pages-1` | `50117194` read as `S0117194` (5→S) | pass, fail, fail |
| `SIV-ARS-25-8332-pages-2` | `GDN-ARS-25-9606` read as `9686` (0→8) | fail, pass, pass |
| `SIV-RHO-26-1669A` | missed handwritten `Po: 83000 52050` beside printed `P.O.: 260988` | pass, fail, fail |

Each document passes on at least one repeat. The 150 DPI renders were inspected:
`50117194`, `9606` and the handwritten number are all clearly legible. These are
**model sampling errors**, not renderer losses. The ambiguous-DN case matches the
old baseline's behaviour and is a pre-existing prompt/model risk.

The repaired 234B document (`id 272`) **passes**, including its PO and lines — the
end-to-end proof that the MediaBox repair feeds the model a full page.

> Recommendation (not part of this port): identifier reads are stochastic at the
> model's default sampling. If a misread PO key is unacceptable, set a deterministic
> temperature/seed in `_call_vlm` or add a second identifier read; do not treat a
> single extracted key as authoritative. This is a model/prompt decision.

---

## 4. Licence audit (runtime, 2026-11-03)

`uv export --no-dev` + a clean venv, 127 installed packages, audited with
`pip-licenses` and the package metadata:

| Licence family | Packages | Position |
|---|---|---|
| MIT / MIT-CMU / BSD-2 / BSD-3 / Apache-2.0 / ISC / PSF | 122 | permissive; notices retained |
| MPL-2.0 | `certifi`, `orjson`, `pathspec`, `tqdm` | file-level copyleft; used unmodified, notices retained |
| Artistic **or** GPL (dual) | `text-unidecode` 1.3 (Prefect → `python-slugify`) | **Artistic License elected**; no GPL obligation on this code |
| AGPL | **none** | PyMuPDF removed from runtime, dev group, lockfile and tools |

The PDF stack is `pypdfium2` (BSD-3/Apache-2.0), PDFium (BSD-3), Pillow (MIT-CMU)
and pypdf (BSD-3). Full texts and build licences ship in the wheel under
`pypdfium2-5.14.0.dist-info/licenses/`; see
[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).

---

## 5. Test suite

- `ruff check src scripts tests`: **pass**.
- Fast suite (`-m "not slow"`, no UI): **pass**, coverage **90.59%** against the
  **85%** CI floor.
- Full non-UI suite: green through **97%**, then a pre-existing Prefect task-engine
  wait in `test_untested_engine_paths.py` (it passes all 14 tests in a fresh
  process: 32 s here, 44 s on a clean `origin/main` baseline). Every file after that
  point passes in a fresh process, including the 20-set soak and the
  original-pages tests.

---

## 6. Remaining deployment gate — Windows 11

The renderer gates are closed on Windows 11. The three NSSM services are now
installed and running against the current code; the reboot persistence check and
one live sync/restore remain. That gate is tracked in
[`WIN11_DEPLOYMENT_VALIDATION_2026-11-04.md`](WIN11_DEPLOYMENT_VALIDATION_2026-11-04.md).
The renderer smoke command on the target is:

```powershell
.\.venv\Scripts\python.exe -c "import pypdfium2; print(pypdfium2.PDFIUM_INFO)"
.\.venv\Scripts\python.exe -c "import sys; sys.path.insert(0,'src'); from app.services.extraction import _rasterize_pdf; print(len(_rasterize_pdf(open(r'data\real_samples\SIV-RHO-26-233.pdf','rb').read(),150,30)))"
```

---

## 7. Sign-off

**Owner decision:** approved for handover as the internal CA tool, 2026-11-03.

Basis: no AGPL runtime component; the PDF renderer is BSD-3/Apache-2.0 with its
notices included; MPL-2.0 components are unmodified; `text-unidecode` is used under
the Artistic License. External distribution or resale requires a fresh review of
these notices.

| | |
|---|---|
| Name | ______________________________ |
| Signature | ______________________________ |
| Date | 2026-11-03 |
