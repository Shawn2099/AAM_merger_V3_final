# PDF renderer evaluation — 2026-11-03 (UTC)

**Status:** discovery/benchmark complete; **implemented on `feat/pdfium-rasterizer`** (2026-11-03), branched from `main` `e716903`.
**Branch:** `feat/pdfium-rasterizer` (from latest `main`).
**Scope:** replace PyMuPDF 1.28.2 (AGPL/commercial) as the PDF→PNG rasteriser used by the VLM extraction path, with a permissively licensed, maintainable renderer for a proprietary internal Windows tool.
**Primary question:** is PDF Oxide 0.3.78 mature/safe enough for handover?
**Short answer:** **No.** PDF Oxide 0.3.78 is promising but too new and currently loses real vendor documents. **Recommend pypdfium2 5.14.0 as primary**, with a small, tested malformed-MediaBox repair and a process-wide PDFium lock. PyMuPDF remains the technical fallback only if the owner buys an Artifex commercial licence.

> **Implementation note (2026-11-03).** The port is in `src/app/services/extraction.py`
> (`_rasterize_pdf`, `_PDFIUM_GLOBAL_LOCK`, `_repair_page_box`), with tests in
> `tests/test_extraction_raster.py` and an integration test in
> `tests/test_manual_merge_matrix.py`. Two details in §7.2/§7.5 below were corrected
> during implementation: the original repair rule false-fires on inherited MediaBoxes
> and on a deliberate CropBox smaller than the MediaBox, so the shipped rule confirms
> the malformed array with pypdf first; and the branch this landed on (`main` `e716903`)
> has no `vlm.rasterize_concurrency` / raster semaphore, so only the global lock is
> present. The acceptance scan after the port: 693/695 real files render (the same two
> corrupt files fail), 0 page-count mismatches, 234B renders 1240×1753, and the repair
> fires only on 234B across all 695 files.

---

## 0. Evidence labels used in this document

- **M (measured):** executed on the evaluation host on 2026-11-03; commands and output paths are in §14.
- **I (inferred):** from upstream source/docs/issues, package metadata, or code inspection; not executed here.
- **N/T (not tested):** WS2016, clean service session, 2-core timing, commercial SDKs, pdf.js rendering.

Evaluation host: Windows 11 Home 10.0.26300 x64, Intel i7-7700 (4 cores/8 threads), 24 GB RAM, Python 3.11.9/3.11.17. **Production is Windows Server 2016 x64, 2 cores, 128 GB.** Per-page timings are roughly transferable because all three Python renderers are single-threaded on the page, but **WS2016/2-core timing is still a gate, not a measured result**.

Corpus: 28 deliberately diverse real vendor PDFs (65 pages, first 3 pages each) plus a full scan of all **695** PDFs in `C:\Users\malliath\Desktop\aam_merger\data\real_samples`. The 28-doc set intentionally over-samples the SIV-RHO family and the known malformed file, so its failure rate is **not** representative; the 695-file scan is the better prevalence estimate. Files were read-only; no data was modified.

---

## 1. Decision summary

| Rank | Option | Verdict |
|---|---|---|
| **1** | **pypdfium2 5.14.0** (PDFium 156.0.8076.0) | **Primary.** BSD-3/Apache-2.0, 693/695 real PDFs render, page-count parity, RGB output, low memory, no VC++ redist, fast. Cost: **all PDFium calls must be serialized process-wide**; one malformed MediaBox needs an in-memory repair. |
| 2 | **PyMuPDF 1.28.2 (pin) / 2.1 (latest)** | Technically best compatibility; **AGPL-3.0 or Artifex commercial**. Not a permissive replacement. Use only with a signed commercial licence, or accept AGPL. |
| 3 | **Apache PDFBox 3.0.8** | Apache-2.0, real renderer, but Java/JVM runtime and slower; viable only if a JVM is acceptable. **N/T.** |
| 4 | **Commercial SDK** (Aspose.PDF 26.10, IronPDF 2026.11, Apryse/PDFTron) | Paid escape hatch; solves licence and quality, adds procurement/vendor lock-in. **N/T.** |
| 5 | **PDF Oxide 0.3.78** | **Do not ship.** MIT/Apache licence and nice API, but 30/695 real files fail, password-protected files render blank without error, custom renderer/font substitution, no fixed release. Revisit only at 0.3.79+ after gates. |
| — | pdf.js+Node, Poppler, Ghostscript, ImageMagick, LibreOffice, borb | Reject for this stack: extra runtime and/or GPL/AGPL licence. |
| — | pypdf, pikepdf, pdfplumber, pdfminer.six, pdftext, docling, marker | **Not rasterisers.** Extraction/repair/OCR only; do not list as renderers. |

**Why pypdfium2 wins:** it is the only permissive, mature, native-Windows renderer that produced a page image for every valid file in the real corpus (693/695; the 2 failures are corrupt and fail in every renderer), matches PyMuPDF page counts exactly, uses ~62 MB peak for a 19-page scan, and does not require the Visual C++ redistributable. Its one hard constraint — PDFium is globally not thread-safe — is compatible with a 2-core server that renders ~10 sets/day, provided access is serialized with a **process-wide lock**, not the current 2-permit semaphore.

---

## 2. Current code surface (what a replacement must touch)

| File | What it does | Change needed |
|---|---|---|
| `src/app/services/extraction.py:283-328` `_rasterize_pdf` | Opens bytes with `pymupdf.open(stream=..., filetype="pdf")`; rejects `needs_pass`; caps pages (`vlm.max_pages`, default 30); caps page pixels (`_MAX_PAGE_PIXELS=40_000_000`) and total PNG bytes (`_MAX_IMAGE_BYTES=40 MiB`); renders every page at `vlm.raster_dpi` (default 150); returns PNG bytes in page order. | Replace the PyMuPDF open/render block; keep the caps and error contract. Add PDFium global lock + MediaBox repair + RGB conversion. |
| `src/app/services/extraction.py:241-249` `_raster_gate` | `threading.BoundedSemaphore(vlm.rasterize_concurrency)`, default 2; intended to stop 3 VLM calls becoming 3 CPU renders. | **Must become/also use a single process-wide lock for PDFium.** A 2-permit semaphore is unsafe: two threads may enter PDFium simultaneously. |
| `src/app/services/extraction.py:331-399` `_call_vlm` | Rasterises before building the client; sends one PNG data URL per page in page order. | No change beyond `_rasterize_pdf`; preserves fail-before-API property. |
| `src/app/core/config.py:73-76` | `vlm.rasterize_concurrency` default 2, 1..4; comment says PyMuPDF CPU-bound. | Update comment; with PDFium set 1 (or keep 2 but lock inside). |
| `config.example.yaml` `vlm:` block | `raster_dpi: 150`, `max_pages: 30`, `rasterize_concurrency: 2`. | Update the explanatory comment, not necessarily the value; gate on 2-core measurement. |
| `pyproject.toml:21` | `pymupdf==1.28.2` runtime dependency. | Replace with `pypdfium2==5.14.0`; decide whether PyMuPDF stays dev-only as an oracle. |
| `tests/test_extraction_raster.py` | Imports `pymupdf` at line 14; `_images()` constructs `pymupdf.Pixmap`; fixtures/encryption use PyMuPDF; asserts 150 DPI, page cap, oversized page, encrypted, corrupt/truncated/empty. | Rewrite image inspection with Pillow; generate encrypted fixtures with pypdf/pikepdf or keep a dev-only PyMuPDF fixture builder. Add 234B and thread-lock tests. |
| `scripts/setup_test_rig.ps1:93-104,186-195` | Warns about VC++ 2015-2022 x64; fails if `import pymupdf` fails. | Check `import pypdfium2` instead; **remove the VC++ requirement for PDFium** (PDFium DLL imports only ADVAPI32/GDI32/USER32/KERNEL32 — measured). Keep a generic VC++ check only if another dependency needs it. |
| `tests/test_windows_scripts.py:208` | Asserts `-c "import pymupdf"` and `vc_redist.x64.exe`. | Update to `pypdfium2`; remove/adjust VC++ assertion. |
| `docs/DELIVERY.md:119-127` | Already records the PyMuPDF AGPL/commercial decision. | Update to the chosen renderer and licence notice. |
| `README.md`, `docs/CORE_LOGIC.md`, `AGENTS.md` | Mention PyMuPDF as the renderer. | Update names/comments. |
| `prompt_improvement/audit_toolkit.py`, `tests/test_ws6_d2_original_pages.py` | Dev/audit scripts import `fitz`/`pymupdf`; not runtime. | Optional: rewrite with pypdfium2; if left, keep PyMuPDF dev-only and document it. |

**Branch state already decided/attempted (do not re-litigate):**
- Authentication (named users, Argon2id, opaque sessions, CSRF, roles) and parallel extraction (`prefect.max_concurrent_extraction_tasks=3`, `vlm.rasterize_concurrency=2`) are in `feat/handover-hardening`.
- One installer, 13:00 DB backup, 15:00 development snapshot, LAN/NSSM decisions are in.
- Previous round: pypdfium2 5.14.0 identified as the strong permissive candidate; two risks: **PDFium global thread-unsafety** and **one malformed 8-number MediaBox file crops under PDFium while PyMuPDF renders it fully**. Both confirmed here; the MediaBox risk now has a tested fix.
- PyMuPDF 2.1 works but is still AGPL/commercial; **2.1 is now the latest release (2026-11-02), not 1.28.2.** The project pin remains 1.28.2.
- This evaluation changed **only this document**. No dependency, code, test, installer, or data file was modified.

---

## 3. Candidate metadata (verified 2026-11-03)

| Property | pypdfium2 5.14.0 | PDF Oxide 0.3.78 | PyMuPDF 1.28.2 / 2.1 |
|---|---|---|---|
| Released | 2026-10-04 | 2026-09-08 | 1.28.2 earlier; 2.1 on 2026-11-02 |
| Licence | **BSD-3-Clause + Apache-2.0** (PDFium BSD-3) | **MIT OR Apache-2.0** | **AGPL-3.0 or Artifex commercial** |
| Python | 3.6+; wheel `py3-none-win_amd64` | >=3.9; wheel `cp38-abi3-win_amd64`; 3.11 works | 1.28.2 `cp310-abi3`; 2.1 `cp311-abi3`; 3.11 works |
| Wheel size (win_amd64) | **3.8 MB** | 11.1 MB | 18.9 MB (1.28.2); **60.1 MB (2.1)** |
| External runtime | **None**; `pdfium.dll` imports ADVAPI32/GDI32/USER32/KERNEL32 only | No Python deps; `.pyd` imports **VCRUNTIME140.dll** | 1.28.2 no Python deps; 2.1 pulls `onnxruntime`, `pymupdf-layout`, `pymupdf4llm`, numpy, etc.; `.pyd` needs MSVCP140/VCRUNTIME140 |
| Renderer | **PDFium 156.0.8076.0** (Chromium) | **Custom Rust rasteriser using `tiny-skia`**; not PDFium | **MuPDF** |
| Render API | `page.render(scale=..., ...)` → `PdfBitmap`; `.to_pil()` | `PdfDocument.render_page(index, dpi=..., format="png")`; `render_pixmap`; **no `Page.render` in 0.3.78** | `page.get_pixmap(dpi=...)` |
| Output | RGB PIL image; PNG via Pillow | **RGBA PNG** (alpha 255 by default); PNG/JPEG only | RGB pixmap; PNG |
| Thread safety | **Process-global unsafe; hard crash if concurrent** | Shared doc raises `RuntimeError: Already borrowed`; separate docs safe; no scaling | Shared/separate docs survived 4 threads here; no speedup; AGPL |
| Maturity | PDFium is Chromium's renderer; pypdfium2 131 releases, widely used | Repo created 2025-11-05; 1,156 stars; 271 open issues/PRs; 83 releases; **Development Status 4 - Beta**; latest 0.3.78, no 0.3.79 wheel | MuPDF is mature; PyMuPDF 140 releases |
| Repository activity | Stable wrapper around PDFium | Very active: 3,000+ issues; last push 2026-10-31; fixes after 0.3.78 are **not in the installable wheel** | Mature |

**PDF Oxide custom renderer evidence (I, source):** `src/rendering/page_renderer.rs` header: “Page renderer using tiny-skia”; Cargo features `rendering` enable `tiny-skia`, `fast_image_resize`, `jpeg2000`. `pdfium-render` is an optional dependency for a table/ML feature, not the page-raster path. So PDF Oxide does **not** inherit PDFium's rendering compatibility.

**PDF Oxide repository numbers (M, GitHub API):** 1,156 stars, 147 forks, 271 open issues/PRs, created 2025-11-05, last push 2026-10-31, default branch `main`, Apache-2.0 repo licence with MIT/Apache package metadata.

---

## 4. Benchmark methodology

### 4.1 28-document benchmark

- Manifest: 28 PDFs from `data/real_samples` (25) + `data/output` merged sets (3), listed in `C:\Users\malliath\AppData\Local\Temp\pdfeval\manifest.json`.
- Each document: first `min(3, page_count)` pages at **150 DPI**, PNG.
- Per page: render success, dimensions, mode, PNG bytes, wall ms; per doc: open success, page count, open ms; process peak RSS.
- Libraries: PyMuPDF 1.28.2, pypdfium2 5.14.0, PDF Oxide 0.3.78, each in a separate process, sequential (no cross-library CPU contention).
- Pixel diff: Pillow/numpy; RGB; MAD = mean absolute difference across channels; `% >32` = fraction of pixels whose max channel delta > 32; blurred diff = MAD after Gaussian blur radius 2; 64-bit dHash Hamming distance. Dimensions differing by ≤1 px were cropped to the common area; a true dimension mismatch is reported.

### 4.2 Full document runs

- 19-page scan: `20260901150338593.pdf` (all 19 pages, 150 DPI).
- 13-page text: `PO_2112_15676_0_US.pdf` (all 13 pages).
- 8-page rotated scan: `20260630184127064 (2 DNs).pdf` (all 8 pages, `/Rotate 270`).

### 4.3 Full-corpus scan

- All **695** `data/real_samples/*.pdf`: open, page count, render page 0 at 72 DPI (failure detection and page-count parity), not full-page timing.
- PDFium scan applied the MediaBox repair rule (see §7.2); PDF Oxide scan had no repair.

### 4.4 Edge fixtures

Under `C:\Users\malliath\AppData\Local\Temp\pdfeval\fixtures`:
- `enc_user.pdf` — AES-256, user password `secret123`, owner password `owner123`, permissions 0.
- `enc_owner_only.pdf` — empty user password, owner password `owner123`.
- `corrupt.pdf` — `%PDF-1.7` + 20 KB deterministic random bytes.
- `empty_zero.pdf` — 0 bytes.
- `empty_no_pages.pdf` — valid PDF with no pages.
- `truncated_20.pdf`, `truncated_50.pdf` — first 20%/50% of a real PDF.
- `blank_page.pdf` — one blank 595×842 page.
- `rot_crop_form.pdf` — media 600×800, crop [50,50,550,750], `/Rotate 90`, highlight annotation, text form widget.
- `234B_normalized.pdf`, `234B_fixed2/3.pdf` — pikepdf experiments, not used for production.

---

## 5. Results — 28-doc benchmark (M)

### 5.1 Summary

| Metric | PyMuPDF 1.28.2 | pypdfium2 5.14.0 | PDF Oxide 0.3.78 |
|---|---:|---:|---:|
| Documents opened | 28/28 | 28/28 | 28/28 |
| Pages attempted | 65 | 65 | 65 |
| Pages rendered | **65/65** | **65/65** | **59/65 (6 fail)** |
| Total render time (successful pages) | 6.132 s | **5.625 s** | 5.274 s (59 pages only) |
| Mean ms/page | 94.3 | **86.5** | 89.4 |
| Median ms/page | 78.2 | **71.2** | 75.8 |
| p95 ms/page | 180.0 | **149.0** | 155.3 |
| Max ms/page | 275.5 | **205.4** | **937.6** (outlier) |
| Process peak RSS | 306.5 MB | **83.3 MB** | 190.1 MB |
| Mean PNG size | 395 KB | 450 KB | **854 KB** |
| Total PNG (65/59 pages) | 25.1 MB | 28.6 MB | **49.2 MB** |
| PNG mode | RGB | RGB | **RGBA** (alpha 255) |
| Dimensions vs PyMuPDF | reference | 1 px on 26/65 (ceil rounding) | **exact on 59/59** |

**Consolidated scorecard (same 28-doc run; pixel diff vs PyMuPDF):**

| Candidate | Version | Licence | win_amd64 / Py3.11 | Render? | 65-page performance | Peak RSS | Pixel diff (MAD mean / max) | Real-corpus failures |
|---|---|---|---|---|---|---|---|---|
| **pypdfium2** | 5.14.0 | BSD-3 + Apache-2.0 | yes | **yes** | 5.625 s / 86.5 ms mean | **83.3 MB** | 3.62 / 13.17 | 2/695 corrupt only (0 unexpected) |
| **PDF Oxide** | 0.3.78 | MIT OR Apache-2.0 | yes (VCRUNTIME140) | **yes** | 5.274 s / 89.4 ms mean (59 pages) | 190.1 MB | 3.58 / 15.33 | **30/695 page-tree + 2 corrupt** |
| **PyMuPDF** | 1.28.2 (pin) / 2.1 | AGPL-3.0 or commercial | yes (MSVCP/VCRUNTIME) | **yes** | 6.132 s / 94.3 ms mean | 306.5 MB | reference | 2/695 corrupt only |

**Caveats:** PDF Oxide’s total/mean excludes the 6 failed pages, so its per-page mean is not directly comparable to the 65-page means. On the pages it did render, it was slightly faster than PyMuPDF and slightly slower than PDFium at 150 DPI PNG. This differs from upstream issue #2702 (2.6× slower than PyMuPDF at 72 DPI raw) because this corpus is image-heavy and includes PNG encoding; PDF Oxide is **faster on scans, slower on dense text/vector pages**, with a 937 ms outlier.

### 5.2 Per-page outliers

- PDF Oxide max: `SIV-RAK-25-2967-Noble_Part3.pdf` page 2 = **937.6 ms**; same page PyMuPDF 81.2 ms, PDFium 80.3 ms. This is an ~11.5× outlier on a page with different geometry (1241×1754) and heavier vector/image content. It is consistent with upstream #2702 (“single-threaded software raster, slower than PyMuPDF and pdfium”; worst cases ~12×).
- PDF Oxide is fastest on the 19-page scan (2.03 s vs 3.00/3.40 s), because its image path is competitive; but it writes 27 MB of PNG for that document vs 15.8 MB (PDFium) / 13.7 MB (PyMuPDF).

### 5.3 Full-document runs (M)

| Document | Renderer | Pages | Total | Mean ms/page | Max ms/page | PNG total | Peak RSS |
|---|---|---:|---:|---:|---:|---:|---:|
| 19-page scan | PyMuPDF | 19 | 3.402 s | 171.4 | 226.5 | 13.70 MB | 255.5 MB |
| 19-page scan | pypdfium2 | 19 | **2.998 s** | 151.6 | 272.8 | 15.78 MB | **61.7 MB** |
| 19-page scan | PDF Oxide | 19 | **2.033 s** | **101.4** | 147.5 | **26.99 MB** | 78.6 MB |
| 13-page text | PyMuPDF | 13 | **1.022 s** | **69.0** | 101.3 | **4.05 MB** | 63.9 MB |
| 13-page text | pypdfium2 | 13 | 1.058 s | 73.8 | 110.7 | 5.45 MB | **50.8 MB** |
| 13-page text | PDF Oxide | 13 | 1.138 s | 79.6 | 103.3 | **13.81 MB** | 54.6 MB |
| 8-page rotated scan | PyMuPDF | 8 | 1.249 s | 139.2 | 233.6 | 5.77 MB | 114.6 MB |
| 8-page rotated scan | pypdfium2 | 8 | 1.236 s | 142.2 | 174.8 | 6.27 MB | **58.3 MB** |
| 8-page rotated scan | PDF Oxide | 8 | **1.140 s** | **129.8** | 189.1 | **10.75 MB** | 67.0 MB |

**Interpretation:** all three are in the same throughput class on this host (~70–170 ms/page). PDFium has the lowest memory and RGB output; PDF Oxide is lower than PyMuPDF on these full-document runs but higher than PDFium, and writes 2–3.4× larger PNGs; PyMuPDF has the highest peak on the 19-page scan and the AGPL problem.

### 5.4 Pixel difference (M)

| Pair | Comparable pages | Dimension mismatches | MAD mean | MAD max | % >32 mean | % >32 max | Blurred MAD mean | dHash mean |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| PyMuPDF vs pypdfium2 | 64 | 31 (all 1 px) | 3.62 | 13.17 | 3.29% | 11.68% | 1.36 | 1.5 |
| PDF Oxide vs PyMuPDF | 59 | **0** | 3.58 | **15.33** | 3.40% | 12.73% | 1.42 | 1.5 |
| PDF Oxide vs pypdfium2 | 59 | 26 (all 1 px) | 4.45 | 14.41 | 4.43% | 12.64% | 1.59 | 1.9 |

**Important:** these differences are **not** a ranking by themselves. The worst pages were visually reviewed as side-by-side images and difference heatmaps:
- `RAKENERGY-001-DOHA.PDF` p0/p1: PDF Oxide’s text edges show red/blue fringing (anti-aliasing/hinting and a sub-pixel vertical shift), but **no missing words or table cells**. Ink coverage was 2.3 percentage points lower on p1 (13.17% vs 10.86%), consistent with thinner glyph rendering, not omitted content.
- `D7519-PO-233130-055-01.pdf` p0 and `D7264-PO-186000-013-01.pdf` p1: same edge fringing; all text present.
- `P106420232.pdf` p0: same; no missing content.
- **Synthetic `rot_crop_form.pdf`:** PDF Oxide rendered the Base-14 Helvetica text in a **serif face**, while PyMuPDF/PDFium rendered sans-serif. This is a real font-fallback fidelity defect (see §7.5), not just anti-aliasing.

Ink-coverage comparison across the 59 PDF Oxide pages: mean delta ≈ 0; largest loss −2.31 pp (RAKENERGY p1), largest gain +0.32 pp. Visual review found no dropped text in those 59 pages. The synthetic font case and the encrypted-blank case still violate “no silent content loss” in the general sense.

---

## 6. PDF Oxide deep dive

### 6.1 What it is

- `pdf-oxide==0.3.78`, import name `pdf_oxide`, MIT OR Apache-2.0, Rust/PyO3, wheel `cp38-abi3-win_amd64`, 11.1 MB, no required Python dependencies (optional `[ocr]` adds `onnxruntime>=1.20.1`).
- Install: `uv run --python 3.11 --with "pdf-oxide==0.3.78" --with pillow python ...` **works**; package name is `pdf-oxide`, import is `pdf_oxide`.
- It is **not PDFium-backed for rendering**. The page renderer is a custom Rust rasteriser using `tiny-skia` (`src/rendering/page_renderer.rs`), with its own text/path/image/shading code. It does not inherit Chromium/PDFium compatibility or security testing.
- The README’s “100% pass rate on 3,830 real-world PDFs” is a **text-extraction** claim, not a page-rasterisation claim. Upstream issue #1468 explicitly says a total render failure was invisible to their render gate.

### 6.2 API in 0.3.78 (M, introspected wheel)

```python
from pdf_oxide import PdfDocument

doc = PdfDocument("in.pdf")                 # or PdfDocument.from_bytes(data, password=None)
len(doc)                                    # page count; doc.page_count() also exists
doc.render_page(0, dpi=150, format="png")   # -> bytes; default format "png"; default DPI is 72, so pass dpi=150
doc.render_pixmap(0, dpi=150)               # -> RenderedPixmap: .data (raw premultiplied RGBA), .width, .height
doc.render_page_fit(0, width, height, ...)  # -> bytes
doc.authenticate("password")                # -> bool
doc.permissions()                           # -> dict; exists even before authentication
# with PdfDocument(...) as doc: ...          # context manager works
```

- **`Page.render()` does not exist in 0.3.78**, despite the README example `png_bytes = page.render(dpi=150, format="png")`. Actual page object (`doc.page(i)` / `doc[i]`) has `width`, `height`, annotations, text helpers, but no `render`.
- **`PdfDocument.close()` does not exist**; use the context manager. Minor but a docs/API mismatch.
- `render_page` signature:
  `render_page(page, dpi=None, format=None, background=None, transparent=False, render_annotations=None, jpeg_quality=None, excluded_layers=None)`.
- `format` accepts `"png"` and `"jpeg"` only; `"raw"` raises `ValueError: format must be 'png' or 'jpeg'`.
- Output is **RGBA PNG**; default alpha is 255 everywhere (`transparent=True` produces alpha 0..255). The project requires RGB PNG, so a conversion is mandatory if this renderer is used.
- `render_pixmap` returns raw **premultiplied RGBA8888**, top-left origin; if alpha is 255, dropping the alpha channel is safe.
- Errors: open failures raise `OSError` (`Invalid cross-reference table`, `Invalid PDF header...`); render failures raise `RuntimeError` (`Failed to render page: ...`). There is no project-specific exception class in the Python API.
- `PdfDocument(path, password=None)` is allowed; `authenticate()` requires a **str** (`authenticate(None)` raises `TypeError`, not a clean “not encrypted” answer).
- Logging: renderer emits warnings to stderr, e.g. `Failed to parse font 'FXF1' ...`, `Dictionary used where Stream expected, treating as empty stream`, `empty paths and horizontal/vertical lines cannot be filled`. `pdf_oxide.disable_logging()` / `set_log_level()` exist; warnings are a signal of substitution, not harmless noise.

### 6.3 Rotation, CropBox, annotations, forms (M)

Using `fixtures/rot_crop_form.pdf` (media 600×800, crop [50,50,550,750], `/Rotate 90`, highlight annotation, text widget):

| Renderer | Rendered size | MAD vs PyMuPDF | Annotation | Form value |
|---|---:|---:|---|---|
| PyMuPDF | 1459×1042 | — | yes | yes |
| pypdfium2 | 1459×1042 | 0.86 | yes | yes (with `init_forms`; 0.93 without) |
| PDF Oxide default | 1459×1042 | 2.05 | yes | yes |
| PDF Oxide `render_annotations=False` | 1459×1042 | 3.45 | **no** | **no** (widget is an annotation) |

- PDF Oxide reports correct `page_crop_box`, `page_media_box`, `page_rotation`; its renderer honors both crop and rotation.
- **Trap:** `page.width`/`page.height` return the **unrotated** media box. For the 270°-rotated scan, `page.width=842`, `page.height=595`, while the rendered PNG is 1241×1755. A page-size guard must use `page_media_box` + `page_rotation` (or `render_pixmap` dimensions), not `page.width*page.height` alone.
- Default `render_annotations` is effectively true; `False` hides both highlights and form widgets. That is acceptable for this app because it renders with defaults; document it if a config switch is ever added.

### 6.4 Real-corpus failures (M) — the decisive finding

Full scan of all 695 `real_samples` PDFs (page 0 at 72 DPI, failure detection):

| Result | Count | Notes |
|---|---:|---|
| Opened | 693/695 | 2 open failures: `SIV-DTS-25-584-2.pdf`, `SIV-DTS-25-584-3.pdf` (`Invalid cross-reference table`); PyMuPDF also fails these. |
| Rendered page 0 | **663/695 (95.4%)** | **30 failures** all `RuntimeError: Failed to render page: Invalid PDF: Pages node missing Kids`. |
| Page-count mismatches (among opened) | **0** | Page counts match PyMuPDF exactly. |
| Failure families | **10 distinct vendor families, 30 files** | SIV-RHO-26-1669, 1896, 1910, 1914, 233, 234, 685, 687, 688, 854; each with base/A/B files. |

The error text is misleading: the files do **not** lack `/Kids`. They have a **valid indirect array** `… /Type /Pages /Count 1 /Kids 4 0 R`, where object 4 is the array. PDFium and PyMuPDF resolve it; PDF Oxide 0.3.78 does not. Upstream issue **#1468** (“/Kids given as an indirect reference is not resolved: 78 documents fail to render at all, and a total render failure is invisible to the render gate”) describes exactly this class. It was closed on **2026-09-22**, after the 0.3.78 wheel was built (2026-09-08); the fix is on `main`, but **no 0.3.79 wheel exists on PyPI** as of 2026-11-03.

In the 28-doc benchmark, 6/28 failed for the same reason: `DN2182.pdf`, `PO2360.pdf`, `SIV-RHO-26-1669.pdf`, `SIV-RHO-26-234.pdf`, `SIV-RHO-26-234B.pdf`, `SIV-RHO-26-685.pdf`. The 28-doc set over-samples this family, so **6/28 = 21% is not the population rate**; 30/695 = **4.3% of files** (10 vendor families) is the better estimate.

**Workaround tested (M):** a `pikepdf` rewrite that makes `/Kids` a direct array lets PDF Oxide render 234B; with `/MediaBox`/`/CropBox` also normalized to 595×841 it renders 1240×1753, MAD 4.29 vs PyMuPDF. But this means rewriting/normalizing every PDF before rendering, adds a dependency, and does not fix the other maturity problems. It is not recommended as the production path when PDFium reads the original directly.

### 6.5 Encryption (M) — silent blank pages

`enc_user.pdf` is AES-256 with user password `secret123`, owner password `owner123`, permissions 0. PyMuPDF reports `needs_pass=1`; PDFium raises `Incorrect password error` without the password.

PDF Oxide 0.3.78:

```python
d = PdfDocument("fixtures/enc_user.pdf")   # no password
len(d)                                      # 2
b = d.render_page(0, dpi=72, format="png") # returns a 12,591-byte PNG
# image is pure white: ink 0.0, extrema 255..255
# stderr: "Decryption failed for object 4 0: Invalid PDF: Not authenticated"
#         "Decryption failed for object 10 0: Invalid PDF: Not authenticated"
d.authenticate("")                          # False
```

- It does **not raise** on a user-password-protected document. It opens it, reports the correct page count, and renders **blank white pages**. If the app simply renders and sends, the VLM receives blank pages; this is exactly the “no silent content loss” failure mode.
- With `PdfDocument(path, password="wrong")` it raises `RuntimeError: Authentication failed: wrong password`; with the correct password it renders content (342,538 bytes, mean ink 12.155). So password handling works **only if the caller supplies one**.
- There is **no `is_encrypted` / `needs_pass` property** in the Python API (upstream #2511: “missing is_encrypted”). `permissions()` exists and returns a dict even before authentication, so it is not a reliable “is locked” test.
- A possible mitigation is to call `doc.authenticate("")` immediately and treat `False` as `PdfRasterError`. Measured: unencrypted blank page → `True`; user-encrypted → `False`; owner-only (empty user password) → `True`. This would fail closed for the common case, but it is undocumented semantics and does not cover certificate/unsupported encryption. It is a workaround, not a reason to adopt 0.3.78.
- Owner-only encrypted files open and render normally (because the user password is empty), which is correct.

### 6.6 Corrupt / truncated / empty (M)

| Fixture | PDF Oxide | PyMuPDF | pypdfium2 |
|---|---|---|---|
| `corrupt.pdf` | `OSError: Invalid cross-reference table` | `FileDataError` | `PdfiumError: Data format error` |
| `empty_zero.pdf` | `OSError: Invalid PDF header...` | `EmptyFileError` | `PdfiumError: Data format error` |
| `empty_no_pages.pdf` | opens, `len=0`; render → `RuntimeError: Page index 0 not found` | opens, 0 pages | open fails (`PDFium: Success`, odd message) |
| `truncated_20/50.pdf` | `OSError: Invalid cross-reference table` | opens, 0 pages | `PdfiumError: Data format error` |
| `blank_page.pdf` | renders 1240×1755 RGBA | renders RGB | renders RGB |

All three fail closed at the app layer for corrupt/truncated/empty zero-byte files. The app’s `count < 1` check covers PDF Oxide/PyMuPDF no-page PDFs. The encryption case is the exception: PDF Oxide **does not** fail closed without an explicit `authenticate("")` check.

### 6.7 Thread safety (M)

Probe: 24 renders over an 8-page document, `ThreadPoolExecutor`, 1/2/4 threads; “shared” = one `PdfDocument`; “separate” = one document per task.

| Renderer | Mode | 1 thread | 2 threads | 4 threads | Errors |
|---|---|---:|---:|---:|---|
| PDF Oxide | shared | 2.407 s (24/24) | 2.236 s (23/24) | 2.029 s (21/24) | 1 × `RuntimeError: Already borrowed` at 2; 3 at 4 |
| PDF Oxide | separate | 2.538 s (24/24) | 2.521 s (24/24) | 2.669 s (24/24) | none, but **no speed-up** |
| pypdfium2 | shared, **no lock** | 1.432 s (24/24) | 0.743 s (23/24) | **process exit −1073740940 (0xC0000374 heap corruption)** | 1 `Failed to load page` at 2; hard crash at 4 |
| pypdfium2 | separate, **no lock** | 1.557 s (24/24) | 0.979 s (14/24) | 0.675 s (14/24) | 10 × `Data format error` at 2/4 |
| pypdfium2 | **global lock** | 1.682 s (24/24) | 1.419 s (24/24) | 1.416 s (24/24) | none |
| PyMuPDF | shared | 3.058 s (24/24) | 3.076 s (24/24) | 3.077 s (24/24) | none; no speed-up |
| PyMuPDF | separate | 3.524 s (24/24) | 3.641 s (24/24) | 3.534 s (24/24) | none; no speed-up |

**Conclusions:**
- **PDFium must be serialized process-wide.** The failure is not limited to sharing one document: opening separate documents concurrently also produced `Data format error`, and 4 shared threads **crashed the whole process**. A `BoundedSemaphore(2)` is not sufficient. A single `threading.Lock` around open/render/close is required; measured 24/24 at all thread counts with the lock.
- **PDF Oxide does not hard-crash** on shared documents, but `render_page` is `&mut self` in PyO3 terms and raises `Already borrowed` under concurrency. One document per task is safe, but there is no throughput gain; it behaves like a serialized renderer. If adopted, serialize/one-doc-per-worker.
- **PyMuPDF** survived both modes here, but did not scale; this does not change the licence verdict.

### 6.8 Output size and the app’s caps (M)

PDF Oxide’s RGBA PNG is 2–3.4× larger than PyMuPDF/PDFium:

- 28-doc mean: 854 KB/page vs 395 KB (PyMuPDF) / 450 KB (PDFium).
- 13-page text: **13.81 MB total** vs 4.05 MB / 5.45 MB.
- 19-page scan: **26.99 MB total** vs 13.70 MB / 15.78 MB.
- 30-page extrapolation on the text document: ~32 MB raw PNG; the app’s `_MAX_IMAGE_BYTES` is **40 MiB**, and base64 inflates by ~33% for the provider request. A dense 30-page document can hit the app cap earlier than with PyMuPDF. This is a behavior change, not just an efficiency issue.

Converting PDF Oxide RGBA→RGB with Pillow (`Image.open(...).convert("RGB")`; `save(..., optimize=True)`) reduced 5 sample pages to **26–31%** of the RGBA size (e.g. 609 KB → 187 KB), but added **~136 ms/page** on those pages. Using `render_pixmap` + drop alpha + re-encode was **~103–112 ms/page**. So the RGB requirement erases much of PDF Oxide’s raw speed advantage on text documents.

---

## 7. pypdfium2 deep dive

### 7.1 Strengths (M)

- **Licence:** BSD-3-Clause (pypdfium2) + BSD-3-Clause (PDFium) + Apache-2.0 components; suitable for a proprietary internal tool with notices.
- **Platform:** `py3-none-win_amd64` wheel, 3.8 MB, Python 3.11 works. `pdfium.dll` imports only `ADVAPI32.dll`, `GDI32.dll`, `USER32.dll`, `KERNEL32.dll` — **no VCRUNTIME/MSVCP**, unlike PyMuPDF and PDF Oxide (measured with `pefile`).
- **Coverage:** 693/695 real PDFs open and render page 0 (the 2 failures are corrupt and fail in PyMuPDF too); **0 page-count mismatches** on 693; 65/65 pages in the 28-doc benchmark; exact page order by index.
- **Quality:** PDFium is Chromium’s renderer; pixel diff vs PyMuPDF is in the same range as PDF Oxide vs PyMuPDF (MAD 3.62 mean, max 13.17). Visual review of the worst diffs (RAKENERGY p1, D7519 p0, D7264 p1) found no missing text; ink coverage delta mean +0.0031, max loss −0.0016 (no content loss).
- **Memory:** process peak 83.3 MB in the 28-doc run; 61.7 MB on the 19-page scan; 50.8 MB on the 13-page text. Much lower than PyMuPDF’s 255.5 MB on the scan.
- **Speed:** 5.625 s / 65 pages (86.5 ms mean, 149 ms p95); 2.998 s for 19 scan pages; 1.058 s for 13 text pages. Competitive with PyMuPDF and PDF Oxide; no 937 ms outlier observed.
- **Output:** RGB PIL image directly (`bitmap.to_pil()`), mean 450 KB/page; no RGBA conversion needed. 19-page scan 15.78 MB, 13-page text 5.45 MB — comfortably under the 40 MiB cap.
- **Edge cases:** user-password encrypted → `PdfiumError: Incorrect password error` (fail closed); corrupt/truncated/empty zero-byte → `Data format error`; owner-only encrypted opens (correct); no-page PDF fails open with an odd `PDFium: Success` message but is caught by the app. No hard crashes on these fixtures.
- **Rotation/CropBox/annotations/forms:** feature doc 90° + crop renders 1459×1042 exactly; highlight annotation and form widget render; `init_forms()` is available and `may_draw_forms=True` is the default. Forms render even without `init_forms` in this fixture (MAD 0.93 vs 0.86 with), but call `init_forms()` if form values matter.
- **Page dimensions:** `get_size()` returns the rotated size; `get_mediabox()`/`get_cropbox()` return unrotated boxes. This is the right model for the app’s page-size guard.

### 7.2 The known malformed 8-number MediaBox — fixed (M)

`SIV-RHO-26-234B.pdf` has an 8-number `/MediaBox`/`/CropBox` (`[0 0 595 841 0 0 595 841]`). Default PDFium behavior:

- `page.get_size()` = **(612, 792)** (Letter), while `get_mediabox()`/`get_cropbox()` = **(0,0,595,841)**.
- Render = **1275×1651**, while PyMuPDF = **1240×1753** (595×841 pt at 150 DPI). This is the known crop: PDFium falls back to Letter and clips the A4 page.

**Fix (tested):**

```python
page = doc[index]
size = page.get_size()                 # rotated
crop = page.get_cropbox()
media = page.get_mediabox()
ss = sorted(round(v, 1) for v in size)
cc = sorted(round(abs(v), 1) for v in (crop[2], crop[3]))
mm = sorted(round(abs(v), 1) for v in (media[2], media[3]))
if ss != cc and ss != mm:              # 234B is the only real_samples hit
    page.set_mediabox(*media)
```

After the fix: render **1240×1753**, MAD **6.31** vs PyMuPDF (normal anti-aliasing), ink 0.1176 vs 0.1101 (no crop). Across all 693 valid `real_samples`, this rule fires **only** on `SIV-RHO-26-234B.pdf` (M). It is an in-memory page-box override; the stored PDF is never modified. This removes the only known content-loss case from the prior round.

> **Correction (2026-11-03, found during the port).** The comparison above is **not safe as
> written** and is *not* what shipped. It uses the boxes' upper-right coordinates rather than
> their dimensions, and it treats "PDFium's size matches neither box" as proof of a malformed
> array. Two legal page shapes also match that condition and were measured on synthetic fixtures:
>
> - a `/MediaBox` inherited from the `/Pages` node: `get_mediabox()` returns PDFium's ANSI-A
>   fallback, not the inherited box, so a 300×400 page would be "repaired" to 612×792;
> - a deliberate `/CropBox` smaller than the `/MediaBox` (a trimmed scan): the rendered size is
>   the crop, which matches neither the media box nor PDFium's fallback.
>
> The shipped rule keeps the cheap check as a pre-filter, then **confirms the array itself with
> pypdf**: repair only when `page.get("/MediaBox")` or `page.get("/CropBox")` is a non-four-number
> array. It compares `(width, height)` (rotation-safe), and repairs both boxes from their first
> four numbers. Re-measured after the port: the repair fires on exactly one page in all 695 real
> files (`SIV-RHO-26-234B.pdf`), and the synthetic inherited/cropped/rotated cases render at
> their true sizes. Pinned by `test_inherited_mediabox_is_not_rewritten_to_letter`,
> `test_cropbox_smaller_than_mediabox_is_honoured` and `test_rotated_page_keeps_its_rotation_and_is_not_repaired`.

### 7.3 One-pixel dimension variance (M)

PDFium uses ceiling for the raster size; PyMuPDF rounds. Example: 612×792 pt at 150 DPI → PDFium 1275×1651, PyMuPDF 1275×1650. In the 28-doc set, 26/65 pages differed by exactly 1 px (height or width); no true size differences after the 234B fix. A `scale = (dpi - 1e-6)/72` trick fixes the integer cases but causes other 1 px differences; there is no single epsilon that matches PyMuPDF on every fractional page size. **Treat ±1 px as acceptable** and assert `abs(w-w_ref) <= 1` in tests; the vision model is insensitive to this. If exact parity is required, crop/resize after render, but that is unnecessary work.

### 7.4 Thread model required for the app (M)

- Use a module-level `threading.Lock` (not the existing semaphore) around the **entire** PDFium lifecycle: `PdfDocument(...)`, `len(doc)`, each `page.render(...)`, `bitmap.to_pil()`, and `doc.close()`. The bitmap/image conversion can be outside the lock only if the PDFium object is no longer touched; simplest is inside.
- Keep `vlm.rasterize_concurrency` at 1 for clarity, or leave 2 but the inner lock serializes. Do **not** rely on the 2-permit semaphore.
- If throughput ever needs more than one core, use a `ProcessPoolExecutor` with one PDFium process per worker (not threads), or a dedicated renderer service; do not share PDFium across threads.
- Measured with the lock: 24/24 renders at 1, 2, and 4 threads; 1.68 s → 1.42 s (cache warm, not true concurrency). On a 2-core server, 30 pages × ~86 ms ≈ 2.6 s serialized, which is fine for ~10 sets/day.

### 7.5 pypdfium2 exact integration sketch (not applied)

```python
import threading
import pypdfium2 as pdfium
from PIL import Image

_PDFIUM_LOCK = threading.Lock()

def _rasterize_pdf(pdf_bytes: bytes, dpi: int, max_pages: int, name: str = "pdf") -> list[bytes]:
    with _PDFIUM_LOCK:
        try:
            doc = pdfium.PdfDocument(pdf_bytes)   # bytes accepted directly
        except Exception as e:
            raise PdfRasterError(f"{name}: cannot open PDF ({type(e).__name__}: {e})") from e
        try:
            count = len(doc)
            if count < 1:
                raise PdfRasterError(f"{name}: PDF has no pages")
            if count > max_pages:
                raise PdfTooLargeError(...)
            images = []
            total = 0
            for index in range(count):
                page = doc[index]
                size = page.get_size()             # rotated, points
                # 234B repair: only when get_size disagrees with both boxes
                crop = page.get_cropbox(); media = page.get_mediabox()
                ss = sorted(round(v, 1) for v in size)
                cc = sorted(round(abs(v), 1) for v in (crop[2], crop[3]))
                mm = sorted(round(abs(v), 1) for v in (media[2], media[3]))
                if ss != cc and ss != mm:
                    page.set_mediabox(*media)
                    size = page.get_size()
                if size[0] * size[1] * (dpi / 72) ** 2 > _MAX_PAGE_PIXELS:
                    raise PdfTooLargeError(...)
                bitmap = page.render(
                    scale=dpi / 72,
                    fill_color=(255, 255, 255, 255),
                    may_draw_forms=True,
                )
                image = bitmap.to_pil()             # RGB
                buf = io.BytesIO(); image.save(buf, format="PNG")
                png = buf.getvalue()
                total += len(png)
                if total > _MAX_IMAGE_BYTES:
                    raise PdfTooLargeError(...)
                images.append(png)
            return images
        except PdfRasterError:
            raise
        except Exception as e:
            raise PdfRasterError(f"{name}: cannot render page ({type(e).__name__}: {e})") from e
        finally:
            doc.close()
```

Notes:
- `PdfDocument(bytes)` and `PdfDocument(io.BytesIO(...))` both work (M); no temp file needed.
- `bitmap.to_pil()` returns RGB; no RGBA conversion.
- `page.render` may draw annotations by default; forms use `may_draw_forms=True`.
- `doc.init_forms()` if form values are required; call it before rendering, inside the lock.
- `fill_color=(255,255,255,255)` gives a white background; otherwise transparent areas may be black/undefined.
- Keep `_MAX_PAGE_PIXELS` and `_MAX_IMAGE_BYTES`; do the page-size check **after** the 234B repair and before `render`.

---

## 8. PyMuPDF assessment

- **1.28.2** is the current pin; 18.9 MB win_amd64 wheel; no Python deps; `pymupdf.open(stream=..., filetype="pdf")`; `needs_pass`; `page.get_pixmap(dpi=150)`; RGB PNG. It rendered 65/65 pages and 234B fully; 693/695 real PDFs open (2 corrupt, same as PDFium). Peak RSS 306.5 MB in the 28-doc run and 255.5 MB on the 19-page scan — highest of the three.
- **2.1** is the latest release (2026-11-02), still **AGPL-3.0 or Artifex commercial**, Python >=3.11, 60.1 MB wheel, and pulls `onnxruntime`, `pymupdf-layout`, `pymupdf4llm`, numpy, networkx, psutil, tabulate. It may improve quality/features, but it does **not** solve the licence and makes the runtime heavier on a 2-core WS2016 host. Do not “upgrade to 2.1” as a licence fix.
- **Licence:** the app is proprietary and served over the LAN; AGPL §13 network-use is the relevant clause. `docs/DELIVERY.md:119-127` already states the three options (buy commercial, replace rasteriser, or accept AGPL + source offer). This evaluation recommends option (b) via pypdfium2.
- **Use as fallback only** if the owner signs an Artifex commercial licence. Its compatibility and quality are the best; the licence is the blocker.
- If PyMuPDF remains in the repo as a **dev/test oracle**, keep it in the dev dependency group only, not in the runtime install; `uv sync --no-dev` must not install it. Legal should confirm that a dev-only AGPL oracle in the source tree is acceptable; otherwise purge it and use PDFium as the oracle for tests.

---

## 9. Full library landscape

Legend: **Renderer?** = can produce page images directly. **Fit** = Python 3.11 + win_amd64 + no heavy external runtime + permissive licence for a proprietary internal tool. **M** measured; **I** inferred; **N/T** not tested.

### 9.1 Python-native and direct candidates

| Library | Version (2026-11-03) | Licence | Windows/Py3.11 | Renderer? | Fit / verdict |
|---|---|---|---|---|---|
| **pypdfium2** | **5.14.0** (PDFium 156.0.8076.0) | BSD-3 + Apache-2.0 | Yes, `py3-none-win_amd64`, no VC++ | **Yes** | **Best permissive fit; primary.** Global lock required; one box repair. |
| **PDF Oxide** | **0.3.78** | MIT OR Apache-2.0 | Yes, `cp38-abi3-win_amd64`; needs VCRUNTIME140 | **Yes** (custom tiny-skia) | **Not yet.** 30/695 fail; encrypted blank; font substitution; beta. |
| **PyMuPDF** | **1.28.2** (pin) / **2.1** (latest) | **AGPL-3.0 or commercial** | Yes; VC++ runtime; 2.1 heavier | **Yes** (MuPDF) | Technically excellent; **licence blocker**. Commercial fallback only. |
| **borb** | 3.0.9 | **AGPL-3.0-or-later** | Pure Python | Creation/manipulation, not a general page rasteriser | Reject (licence + not a renderer). |
| **pypdf** | 6.21.4 | BSD-3 | Pure Python | **No** | Parser/merger only; support/test fixtures. |
| **pikepdf** | 10.17.0 | MPL-2.0 | Yes, wheels | **No** (QPDF) | Repair/normalization support only; optional. |
| **pdfplumber** | 0.11.10 | MIT | Pure Python | **No** (`to_image` delegates to external) | Extraction only; not a renderer. |
| **pdfminer.six** | 20260107 | MIT | Pure Python | **No** | Extraction only. |
| **pdftext** | 0.7.1 | Apache-2.0 | Pure Python | **No** (pins pypdfium2 5.10.1 for text) | Extraction only; avoid dependency conflict. |
| **docling** | 2.145.0 | MIT | Yes, but heavy models | **No** (document conversion/OCR pipeline) | Not a rasteriser; heavy. |
| **marker-pdf** | 2.1.0 | Apache-2.0 | Yes, but torch/surya | **No** (OCR/markdown pipeline) | Not a rasteriser; heavy. |

### 9.2 External runtimes / other languages

| Option | Version | Licence | Windows/Py3.11 fit | Renderer? | Verdict |
|---|---|---|---|---|---|
| **pdf.js + Node** | pdfjs-dist **6.5.438** | Apache-2.0 | Node **>=22.13** + `@napi-rs/canvas` | **Yes** (Firefox renderer) | High quality, but adds Node + native canvas; reject for this Python/NSSM stack. **N/T.** |
| **Poppler / pdf2image** | Poppler **26.10** (2026; not pinned); pdf2image **1.17.0** | **GPL-2.0** (wrapper MIT) | External `pdftoppm`/`pdftocairo`; Windows builds unofficial | **Yes** | Excellent quality, but GPL + external binary; reject for proprietary distribution unless legal accepts separate-process use. **N/T.** |
| **Ghostscript** | 10.x (vendor current) | **AGPL-3.0 or commercial** | Windows `gswin64c.exe`; external | **Yes** (`png16m`) | Same AGPL issue as PyMuPDF + external runtime; reject. **N/T.** |
| **ImageMagick / Wand** | ImageMagick **7.1.2-35**; Wand **0.7.2** | ImageMagick License (Apache-compatible) | External ImageMagick; PDF delegate usually Ghostscript; policy may block PDF | **Only via Ghostscript** | Reject: not standalone, Ghostscript licence, policy/sandbox. **N/T.** |
| **LibreOffice** | **26.8.x** (2026; not pinned) | MPL-2.0 | Huge install; headless | Not a reliable page rasteriser | Reject: heavy, conversion semantics, not per-page PNG API. **N/T.** |
| **Apache PDFBox** | **3.0.8** | Apache-2.0 | Java/JVM; no Python native | **Yes** (`PDFRenderer` → `BufferedImage`) | Permissive but adds JVM and is slower; viable fallback if JVM acceptable. **N/T.** |
| **pdfium-render (Rust)** | **0.9.5** | MIT OR Apache-2.0 | Rust crate; needs PDFium binary; no Python | **Yes** (PDFium) | Same PDFium thread/box issues; no advantage over pypdfium2 for Python. **N/T.** |
| **MuPDF / mupdf-rs** | vendor current | **AGPL-3.0 or commercial** | C/Rust bindings; Windows | **Yes** (MuPDF) | Same licence as PyMuPDF; no. **N/T.** |
| **unipdf (Go)** | **v5.2.0** | Commercial | Go; separate service | Yes (commercial) | Not Python; paid; no. **N/T.** |
| **Aspose.PDF for Python via .NET** | **26.10.0** | Commercial | Python 3.9–3.13; .NET runtime | **Yes** | Paid fallback; mature; procurement/proprietary. **N/T.** |
| **IronPDF for Python** | **2026.11.0.1** | Commercial | Python; .NET/Java runtime | **Yes** | Paid fallback; same. **N/T.** |
| **Apryse / PDFTron** | vendor current | Commercial | C++/Java/.NET/Python bindings | **Yes** | Paid fallback; enterprise; same. **N/T.** |
| **Adobe PDF Library SDK** | vendor current | Commercial | C++/Java; Windows | **Yes** | Paid fallback; same. **N/T.** |

*Versions for external runtimes (Poppler, Ghostscript, LibreOffice, ImageMagick) are vendor-current estimates from their release lines; none was installed or benchmarked on this host. Python/package versions in §9.1 are from PyPI/GitHub metadata on 2026-11-03.*

### 9.3 Explicitly not renderers (do not list as candidates)

`pypdf`, `pikepdf`, `pdfplumber`, `pdfminer.six`, `pdftext`, `docling`, `marker-pdf`, `ocrmypdf`, `img2pdf`, `borb` (creation/manipulation), `lopdf`, `pdf-extract`, `pdfcpu` — they parse, extract, repair, OCR, or create PDFs; they do not provide a general “render arbitrary page to RGB PNG at 150 DPI” API. `pdfplumber.to_image()` and `pypdfium2`-backed text extractors are not independent renderers.

**Why not Poppler/Ghostscript if quality is good?** Both are technically strong, but GPL/AGPL is incompatible with shipping a proprietary internal tool without a legal decision; both add an external binary/toolchain to a 2-core WS2016 NSSM deployment. The owner’s requirement is a permissive library, so they are out unless the licence position changes.

**Why not pdf.js?** It is the Firefox renderer and high quality, but it requires Node 22.13+ and a native canvas package, plus an async worker model. That is a second runtime and deployment surface for a Python app that already has a working permissive native option.

**Why not Apache PDFBox?** Apache-2.0 and capable, but a JVM is a heavy external runtime for a 2-core server; pypdfium2 is native, smaller, and faster.

---

## 10. What breaks if we choose PDF Oxide 0.3.78

1. **30 real vendor files fail to render** (`Pages node missing Kids`, indirect `/Kids`), representing 10 SIV-RHO document families. In the 28-doc set, 6/28 fail. The app would mark those documents failed and they would not merge. This is operational loss, not silent, but unacceptable for a CA handover.
2. **Password-protected files render blank pages without error.** A user-password PDF opens, reports the right page count, and returns pure-white PNGs; only stderr says `Not authenticated`. Without an explicit `authenticate("")` guard, the VLM sees blank pages and the app may mark a meaningless extraction. This violates “no silent content loss”.
3. **The documented API is wrong for 0.3.78.** README says `page.render(...)`; the wheel has no `Page.render`. Code copied from docs fails with `AttributeError`. Actual API is `PdfDocument.render_page(index, ...)`.
4. **RGBA output and 2–3.4× larger PNGs.** The app requires RGB; conversion adds ~100–170 ms/page, and 30-page text documents can approach/exceed the 40 MiB `_MAX_IMAGE_BYTES` cap that PyMuPDF/PDFium stay under.
5. **Non-embedded font substitution.** 115/208 real docs with fonts use non-embedded Type1/Type3 fonts. A synthetic Base-14 Helvetica page rendered in a **serif** face under PDF Oxide; family/metrics are not guaranteed. Layout shifts or clipped text are possible, especially on WS2016 without desktop fonts.
6. **Custom renderer, not PDFium.** `tiny-skia` rasteriser; release notes for 0.3.75/0.3.76/0.3.78 are still fixing rotation, CMYK, CCITT, Type3, Form XObject, tiling patterns, etc. It does not inherit Chromium’s rendering compatibility.
7. **Thread model is awkward.** Shared document → `RuntimeError: Already borrowed`; separate documents are safe but do not scale. `rasterize_concurrency=2` gives no CPU parallelism and may add contention.
8. **No fixed release.** Critical fixes (#1468 page-tree, #1590 wrong-page numbering, #2740 page-tree fast path, #2324 repairs) are on `main` after 0.3.78 but **not in a PyPI wheel**. Installing from git needs Rust/maturin on WS2016, against the stack constraints.
9. **Maturity/bus factor.** Repo created 2025-11-05, 0.3.x beta, 271 open issues/PRs, 1,156 stars, effectively a single-maintainer project; high AI-assisted issue/PR volume. For a handover to a CA, that is a maintenance risk.
10. **VC++ runtime still required.** `pdf_oxide.pyd` imports `VCRUNTIME140.dll`; a clean WS2016 may need the same VC++ redist that PyMuPDF needs. pypdfium2 does not.
11. **Minor API friction.** No `close()`; page `width/height` are unrotated; stderr warnings; no `is_encrypted`; errors are generic `OSError`/`RuntimeError`.

**What does work:** licence, install, page-count parity (693/695 opened, 0 page-count mismatches), rotation, CropBox, annotations, forms, low memory, no external PDFium, fast scans. It is a credible future candidate **once 0.3.79+ is released and the gates in §12 pass**, not now.

---

## 11. Ranked recommendation

### Primary — pypdfium2 5.14.0

Adopt it with the following conditions:
1. Process-wide `threading.Lock` around all PDFium calls; set `vlm.rasterize_concurrency` to 1 (or keep 2 but lock inside).
2. Apply the `set_mediabox` repair when `get_size()` disagrees with both CropBox and MediaBox; measured only 234B in 693 files.
3. Render RGB with white fill; keep page/pixel/byte caps; map `PdfiumError` to `PdfRasterError`.
4. Validate on WS2016 clean image, service session 0, 2 cores, real vendor corpus, and VLM A/B.
5. Include PDFium/pypdfium2 BSD/Apache notices in the distribution.

This gives a permissive, native, low-memory, RGB renderer with 693/695 real-file coverage and no known content-loss case after the 234B fix.

### Fallback — PyMuPDF 1.28.2 or 2.1 **only with an Artifex commercial licence**

If the owner will not accept the PDFium global lock, or if a rendering regression appears on a vendor file that PDFium cannot handle, PyMuPDF is the technical fallback. It is not a permissive option. Do not ship it under AGPL without a legal decision and source-offer plan.

### Paid fallback — Apache PDFBox 3.0.8 or a commercial SDK

If the owner rejects both PDFium’s thread model and the AGPL/commercial PyMuPDF choice, evaluate PDFBox (Apache-2.0, JVM) or Aspose/IronPDF/Apryse (paid, native/Windows). These are outside the current “no heavy external runtime / no licence fee” constraints.

### Do not adopt — PDF Oxide 0.3.78

Revisit only when **all** of these are true:
- A 0.3.79+ wheel is on PyPI with #1468/#1590/#2740 fixes.
- A fresh 695-file scan renders 693/695 and the 2 corrupt files fail cleanly.
- User-password PDFs fail closed (or the app’s `authenticate("")` guard is proven on all encryption types).
- RGB conversion/output-size and font-fallback gates pass.
- Thread model and WS2016 VC++ runtime are validated.
- The owner accepts a beta, fast-moving, single-maintainer dependency.

---

## 12. Acceptance gates

### 12.1 Gates for pypdfium2 (must pass before merge)

| Gate | Threshold | How |
|---|---|---|
| Real vendor coverage | 693/695 `real_samples` page 0 render; 2 corrupt fail with `PdfRasterError`; 0 unexpected failures | `full_scan`-style script; inspect errors |
| Page count parity | Exact vs PyMuPDF on all valid files | compare `len(doc)` |
| Page order | First/last page of a multi-page doc visually correct; VLM split indices unchanged | render all pages; compare to baseline |
| Page size | Within ±1 px of PyMuPDF; 234B exactly 1240×1753 after repair | image dimensions |
| RGB PNG | Mode RGB; white background; no alpha | Pillow inspect |
| 234B | Full A4 content, MAD ≤ 8 vs PyMuPDF, no crop | `set_mediabox` test |
| Encrypted | User password → `PdfRasterError`; owner-only opens; no blank render | fixtures |
| Corrupt/truncated/empty | Clear `PdfRasterError`, no crash | fixtures |
| Thread stress | 4 threads × 100 renders, one process, 0 crashes/0 PDFium errors | global lock probe |
| Memory | Peak < 512 MB per worker for a 30-page doc; PNG total < 40 MiB | psutil |
| WS2016 | Clean image, no VC++ redist, service session 0, import + render | deployment test |
| 2-core latency | 30-page doc raster < 30 s; p95 page < 300 ms | stopwatch on WS2016 |
| VLM A/B | 20 real docs: extracted fields equal or better than PyMuPDF baseline; no new failures | OpenRouter run |

### 12.2 Gates if PDF Oxide is reconsidered

All of §12.1 plus:
- 0.3.79+ wheel; 693/695 render; 0 unexpected failures.
- User-password PDF must **raise**, not blank; test `authenticate("")` and a real user-password file.
- RGB output and 30-page total under 40 MiB after conversion; measure conversion cost.
- Non-embedded font corpus (115 docs) visual review; no wrong-family/clipped text.
- Shared-document thread test must not raise `Already borrowed`; or app must use one doc per task + serialization, with measured throughput.
- WS2016 VCRUNTIME140 present; clean-image test.
- Pin exact version and record upstream commit; no git installs on WS2016.

---

## 13. Exact next implementation steps (pypdfium2)

1. **Branch** `feat/pdfium-rasterizer` from `dev`; keep `main` deployable.
2. **Write failing tests first** (TDD):
   - `test_rasterize_pdf_returns_rgb_png_in_page_order` using pypdf blank pages of distinct widths; assert `Image.mode == "RGB"` and order.
   - `test_rasterize_pdf_150_dpi_dimensions` (2-inch page → 300 px).
   - `test_rasterize_pdf_rejects_encrypted_user_password` (fixture).
   - `test_rasterize_pdf_rejects_corrupt_truncated_empty`.
   - `test_rasterize_pdf_repairs_8_number_mediabox` with a copy of 234B; assert 1240×1753 and content.
   - `test_pdfium_calls_are_serialized` (monkeypatch/instrument lock or run 4 threads × 20 pages; no errors).
   - `test_page_and_byte_caps_still_apply`.
3. **Implement** `_rasterize_pdf` as in §7.5; add `_PDFIUM_LOCK`, `_repair_page_box`, `_render_page_png`; keep `PdfRasterError`/`PdfTooLargeError` messages and fail-before-API behavior.
4. **Dependencies:** `pyproject.toml` runtime `pypdfium2==5.14.0` **and `pillow`** (required by `PdfBitmap.to_pil()`; currently Pillow is dev-only); remove `pymupdf==1.28.2`. If keeping PyMuPDF as an oracle, put it in `[dependency-groups] dev` only and document that `uv sync --no-dev` must not install it.
5. **Installer/tests:** change `setup_test_rig.ps1` to `import pypdfium2`; remove the VC++ check or make it generic; update `tests/test_windows_scripts.py`; update `docs/DELIVERY.md`, `README.md`, `docs/CORE_LOGIC.md`, config comments.
6. **Run:** full unit suite; 28-doc benchmark; 695-file scan; thread stress; 234B; encrypted/corrupt/truncated; memory; 2-core/WS2016.
7. **A/B:** run 20 real documents through the VLM with both renderers; compare extracted PO/DN/SI numbers, line items, quantities; no new failures.
8. **Rollback:** keep the old `pymupdf` code path behind a temporary config flag only if the wheel is not installed; do not ship both AGPL and permissive runtime deps. A one-release `renderer: pypdfium2|pymupdf` switch is acceptable only for A/B, default pypdfium2, and must be removed before handover.
9. **Docs:** add a short “PDF renderer” section to `docs/DELIVERY.md` with licence notices and the 234B repair rationale; update `AGENTS.md` stack table.

**Pillow check:** pypdfium2’s `PdfBitmap.to_pil()` requires Pillow. The app currently has Pillow only in the dev group. Before implementation, either add `pillow>=...` to runtime dependencies or use `bitmap.buffer`/`PdfBitmap` raw conversion without Pillow. Do not assume dev-only Pillow is available in production. Measured: a clean venv with only `pypdfium2==5.14.0` raises `ModuleNotFoundError: No module named 'PIL'` on `to_pil()`.

---

## 14. Commands, scripts, and temp paths

Temp root: `C:\Users\malliath\AppData\Local\Temp\pdfeval` (throwaway; not in the repo).

Environment:

```powershell
cd C:\Users\malliath\AppData\Local\Temp\pdfeval
uv venv --python 3.11 .venv
uv pip install --python .venv\Scripts\python.exe `
  "pdf-oxide==0.3.78" "pypdfium2==5.14.0" "pymupdf==1.28.2" `
  "pillow" "numpy" "psutil" "pypdf" "pikepdf" "pefile"
```

Isolation check (as requested):

```powershell
uv run --python 3.11 --with "pdf-oxide==0.3.78" --with pillow python -c `
  "import pdf_oxide; print(pdf_oxide.VERSION)"
```

Scripts (all under `C:\Users\malliath\AppData\Local\Temp\pdfeval`):

| Script | Purpose | Output |
|---|---|---|
| `make_manifest.py` / `manifest.json` | 28-doc corpus | manifest |
| `bench_renderers.py` | per-library first 3 pages, 150 DPI, timings/RSS/PNG | `out2/{lib}/results.json`, PNGs |
| `analyze.py` | aggregate stats + pixel diffs | `diffs.json` |
| `thread_probe.py` | 1/2/4 threads, shared/separate docs | stdout JSON |
| `full_scan.py` | all 695 real_samples, open/page count/render page 0 | `full_scan.json` |
| `full_doc_bench.py` | all pages of 19/13/8-page docs | stdout JSON |
| `fixture_probe.py` | encrypted/corrupt/empty/truncated/blank matrix | stdout |
| `feature_probe.py` | rotation/CropBox/annotation/form | `feat_*.png` |
| `pdfium_scan.py` | 695-file PDFium render page 0 + repair | `pdfium_full_scan.json` |
| `pdfium_lock.py` | global-lock thread test | stdout |
| `box_rule.py` | 695-file MediaBox repair candidate scan | stdout |
| `font_scan.py` | non-embedded font prevalence | stdout |
| `enc_probe.py`, `enc_construct.py`, `enc_blank.py` | PDF Oxide encryption behavior | stdout |
| `montage.py`, `diffmap.py`, `align.py` | visual side-by-side/diff/alignment | `cmp_*.png`, `diff_*.png` |

No project files were modified; `git status` on `aam_merger-handover` must remain clean except for this document.

---

## 15. Open questions for the owner

1. **Licence:** Is an Artifex commercial licence for PyMuPDF available? If yes, PyMuPDF 2.1 is the lowest technical risk. If no, pypdfium2 is the recommended permissive path. This is a commercial/legal decision, not a code default.
2. **Global PDFium lock:** Is serialized rasterisation acceptable on the 2-core server for ~10 sets/day? Expected yes (30 pages ≈ 2.6 s), but confirm against the production schedule and 409-lock concurrency.
3. **234B repair:** Is an in-memory `set_mediabox` override acceptable, given the stored PDF is not modified? It is the only real file affected in 693 valid samples.
4. **1 px variance:** Is ±1 px acceptable in tests and VLM input? If not, define whether to crop/resize (extra work) or keep PyMuPDF.
5. **PDF Oxide retry:** Do you want a 90-day trigger to re-evaluate when 0.3.79+ ships, or close the PDF Oxide option now? The licence/API are attractive; the maturity is not.
6. **WS2016 validation:** Is a clean WS2016 image available (no VC++ redist, no Python packages) for the acceptance run? PDFium should import without VC++; PDF Oxide will likely need VCRUNTIME140.
7. **Password-protected vendor PDFs:** Do they occur? If yes, the app must fail them clearly; pypdfium2 does; PDF Oxide 0.3.78 does not without a guard.
8. **VLM A/B budget:** Approve ~20–30 real documents (one paid extraction each) to compare renderers on extraction fields, not just pixels.
9. **PyMuPDF as dev oracle:** May it remain in the dev dependency group for pixel-diff tests, or must the repo contain no AGPL code at all?
10. **Licence notices:** Confirm the distribution can include PDFium/pypdfium2 BSD/Apache notices (and optionally PDF Oxide MIT/Apache if trialled).

---

## 16. Progress / next-session checklist

- [x] Verify PyPI/GitHub metadata, wheels, licence, release dates, repo activity, PDF Oxide renderer type.
- [x] Install PDF Oxide 0.3.78 in isolation; confirm `render_page` exists, `page.render` does not.
- [x] Benchmark 28 real PDFs at 150 DPI for three renderers; timings, RSS, PNG sizes, dimensions.
- [x] Full 695-file `real_samples` scan for PDF Oxide and PDFium; page-count parity.
- [x] Pixel diffs vs PyMuPDF and PDFium; visual review of worst pages.
- [x] Thread tests 1/2/4 threads, shared/separate docs; PDFium crash reproduced; lock verified.
- [x] Encrypted/corrupt/empty/truncated/blank fixtures; PDF Oxide blank-page encryption bug reproduced.
- [x] Rotation/CropBox/annotation/form feature test.
- [x] 234B malformed MediaBox; PDFium `set_mediabox` fix verified; only one real hit.
- [x] Non-embedded font prevalence scan (115/208 docs).
- [x] Full library landscape table.
- [ ] WS2016 clean-image import/render (PDFium and, if retried, PDF Oxide).
- [ ] 2-core timing and memory on WS2016.
- [ ] VLM A/B on 20–30 real documents.
- [ ] Implement pypdfium2 branch + tests.
- [ ] Update installer/docs/tests and remove PyMuPDF runtime dependency.
- [ ] Legal sign-off on PDFium notices / PyMuPDF commercial (if fallback).
- [ ] Re-evaluate PDF Oxide 0.3.79+ only if owner wants.

---

## 17. References

- PyPI JSON: `https://pypi.org/pypi/pdf-oxide/json`, `.../pypdfium2/json`, `.../pymupdf/json`, `.../pdf-oxide/0.3.78/json`.
- PDF Oxide repository: `https://github.com/yfedoseev/pdf_oxide`; source `src/rendering/page_renderer.rs` (tiny-skia).
- PDF Oxide issues: #1468 (indirect `/Kids`; 78 docs; render gate blind spot), #1590 (wrong page numbering on malformed trees; 0.3.78 affected), #2702 (render 2.6× slower than PyMuPDF, 3.5× PDFium), #1589 (shared-document threads ~1.9×), #2511 (Python API lacks `is_encrypted`), #1246 (oversized image resampling/memory), #2987 (Standard-14 width bug).
- pypdfium2: `https://pypi.org/project/pypdfium2/`; PDFium 156.0.8076.0 (`pypdfium2.PDFIUM_INFO`).
- PyMuPDF: `https://pypi.org/project/pymupdf/`; 2.1 released 2026-11-02; AGPL/commercial.
- pdf.js: `https://registry.npmjs.org/pdfjs-dist/latest` (6.5.438, Apache-2.0, Node >=22.13, optional `@napi-rs/canvas`).
- Apache PDFBox 3.0.8: `https://github.com/apache/pdfbox/tags`.
- ImageMagick 7.1.2-35: `https://github.com/ImageMagick/ImageMagick/releases`.
- Aspose.PDF 26.10.0 / IronPDF 2026.11.0.1: PyPI metadata.
- `pdfium-render` 0.9.5: crates.io; `unipdf` v5.2.0: GitHub releases.

---

*End of evaluation. The recommendation is pypdfium2 5.14.0; PDF Oxide 0.3.78 is not handover-ready. The implementation landed on `feat/pdfium-rasterizer` (2026-11-03); the measured results in this document are the pre-port benchmark, and the post-port acceptance scan is recorded in the implementation note at the top.*
