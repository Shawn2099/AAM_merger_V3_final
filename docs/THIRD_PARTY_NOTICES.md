# Third-party notices — PDF rendering

This project ships a proprietary internal tool. The PDF renderer was chosen so the
runtime contains no AGPL component. The components below are the ones used to turn
a stored PDF into the PNG pages sent to the VLM.

| Component | Version | Licence | Use |
|---|---|---|---|
| `pypdfium2` | 5.14.0 | **BSD-3-Clause OR Apache-2.0** | Python wrapper around PDFium |
| PDFium (bundled in the wheel as `pdfium.dll`) | 156.0.8076.0 | **BSD-3-Clause** | page rasteriser (Chromium's PDF engine) |
| `Pillow` | >= 13.0.0 | **HPND** (MIT-like) | `PdfBitmap.to_pil()` and PNG encoding |
| `pypdf` | >= 5.9.0 | **BSD-3-Clause** | merge/split and the page-box check |

`PyMuPDF` / MuPDF is **not** a runtime, test, or development dependency. It was
removed on 2026-11-03 because it is AGPL-3.0 or Artifex commercial; see
[`PDF_RENDERER_EVALUATION_2026-11-03.md`](PDF_RENDERER_EVALUATION_2026-11-03.md).

## Where the full licence texts are

The wheel carries the complete texts and the PDFium build licences. With the normal
virtual environment they are here:

```
.venv/Lib/site-packages/pypdfium2-5.14.0.dist-info/licenses/LICENSES/
    Apache-2.0.txt
    BSD-3-Clause.txt
    CC-BY-4.0.txt
.venv/Lib/site-packages/pypdfium2-5.14.0.dist-info/licenses/data/windows_x64/BUILD_LICENSES/
```

Any installer, Nuitka build, or other redistribution that does not ship
`site-packages` must copy those files (or the equivalent `pypdfium2` notices) into
the distribution. `pypdf` and `Pillow` ship their notices in their own
`*.dist-info/licenses/` directories.

## What this file is not

It is not legal advice. It records the components and where their notices live so
the handover package can include them; the owner still signs off the distribution.
