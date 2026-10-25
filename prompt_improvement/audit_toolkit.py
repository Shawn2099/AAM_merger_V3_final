#!/usr/bin/env python3
"""
AAM Merger V3 — Audit & Prompt Improvement Toolkit
Location: prompt_improvement/audit_toolkit.py

A unified, reusable CLI automation tool for inspecting vendor documents, rendering PDF pages
to high-resolution PNGs for visual verification, auto-detecting and scaffolding new batch audit
reports in standardized markdown format, retrieving past batch findings and vendor rules,
and maintaining the master vendor intelligence matrix.

Usage:
  # Run directly from workspace root OR from within prompt_improvement/!

  # 1. Check system audit status (total audited, remaining uninspected, PO set stats):
  python audit_toolkit.py status

  # 2. Auto-generate the next batch when new samples arrive (e.g. Batch 41):
  python audit_toolkit.py new-batch

  # 3. Retrieve audit findings, vendor intelligence, or document records:
  python audit_toolkit.py retrieve --batch 25
  python audit_toolkit.py retrieve --doc 163
  python audit_toolkit.py retrieve --vendor "Valaris"
  python audit_toolkit.py retrieve --po 50116501

  # 4. Inspect specific documents or batches in detail:
  python audit_toolkit.py inspect --docs 194,195,196
  python audit_toolkit.py inspect --batch 35
  python audit_toolkit.py inspect --uninspected --limit 5

  # 5. Render PDF pages to high-resolution PNGs (144 DPI / zoom=2.0):
  python audit_toolkit.py render --docs 285,286

  # 6. Scaffold a custom batch report:
  python audit_toolkit.py scaffold-report --batch 41 --docs 3,4,5,6,7

  # 7. Append a completed batch report to the master AUDIT_FINDINGS_REPORT.md:
  python audit_toolkit.py append-report --file batch_41_findings.md

  # 8. Query or list vendors in the master extraction matrix:
  python audit_toolkit.py matrix --query "BHD"
  python audit_toolkit.py matrix --list
"""

import argparse
import glob
import io
import json
import os
import re
import sqlite3
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Dynamic Workspace & Path Discovery
# ---------------------------------------------------------------------------
def find_workspace_root() -> Path:
    """Dynamically resolves project root whether invoked from root, prompt_improvement, or elsewhere."""
    current = Path(__file__).resolve().parent
    if (current / "data" / "aam_merger.db").exists():
        return current
    if (current.parent / "data" / "aam_merger.db").exists():
        return current.parent
    for parent in current.parents:
        if (parent / "data" / "aam_merger.db").exists():
            return parent
    return current.parent


WORKSPACE_ROOT = find_workspace_root()
DB_PATH = WORKSPACE_ROOT / "data" / "aam_merger.db"
RENDER_DIR = WORKSPACE_ROOT / "scratch_dump" / "render_all"
PROMPT_IMPROVEMENT_DIR = WORKSPACE_ROOT / "prompt_improvement"
MATRIX_PATH = PROMPT_IMPROVEMENT_DIR / "vendor_extraction_matrix.md"
AUDIT_REPORT_PATH = WORKSPACE_ROOT / "AUDIT_FINDINGS_REPORT.md"
ORDERED_AUDIT_DOCS_PATH = WORKSPACE_ROOT / "ordered_audit_docs.json"

# Ensure UTF-8 output on Windows consoles
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# Attempt importing PyMuPDF with fallback to temp directory
try:
    import pymupdf
except ImportError:
    temp_pdflibs = os.path.join(os.environ.get("TEMP", ""), "opencode", "pdflibs")
    if os.path.exists(temp_pdflibs) and temp_pdflibs not in sys.path:
        sys.path.insert(0, temp_pdflibs)
    try:
        import pymupdf
    except ImportError:
        pymupdf = None


# ---------------------------------------------------------------------------
# Database Utilities
# ---------------------------------------------------------------------------
def get_db_connection() -> sqlite3.Connection:
    if not DB_PATH.exists():
        print(f"Error: Database file not found at {DB_PATH.resolve()}")
        sys.exit(1)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def fetch_doc_details(conn: sqlite3.Connection, doc_id: int) -> dict:
    """Retrieves document record, line items, parent PO set, raw JSON, and rendered images."""
    cur = conn.cursor()
    cur.execute("SELECT * FROM documents WHERE id = ?", (doc_id,))
    row = cur.fetchone()
    if not row:
        return None
    doc_dict = dict(row)

    cur.execute("SELECT * FROM line_items WHERE document_id = ? ORDER BY id", (doc_id,))
    line_items = [dict(r) for r in cur.fetchall()]

    po_set = None
    if doc_dict.get("po_set_id"):
        cur.execute("SELECT * FROM po_sets WHERE id = ?", (doc_dict["po_set_id"],))
        set_row = cur.fetchone()
        if set_row:
            po_set = dict(set_row)

    # Resolve full stored path relative to workspace root if necessary
    stored_path = doc_dict.get("stored_path")
    if stored_path and not Path(stored_path).is_absolute():
        full_stored_path = str(WORKSPACE_ROOT / stored_path)
    else:
        full_stored_path = stored_path

    rendered_images = get_rendered_images_for_doc(doc_id, full_stored_path)

    raw_json = {}
    if doc_dict.get("raw_extraction_json"):
        try:
            raw_json = json.loads(doc_dict["raw_extraction_json"])
        except Exception:
            raw_json = {}

    return {
        "doc_id": doc_id,
        "filename": doc_dict["original_filename"],
        "stored_path": doc_dict["stored_path"],
        "full_stored_path": full_stored_path,
        "doc_type": doc_dict["doc_type"],
        "po_no_raw": doc_dict["po_no_raw"],
        "po_no_normalized": doc_dict["po_no_normalized"],
        "dn_no": doc_dict["dn_no"],
        "si_no": doc_dict["si_no"],
        "invoice_no": doc_dict["invoice_no"],
        "extraction_status": doc_dict["extraction_status"],
        "po_set_id": doc_dict["po_set_id"],
        "po_set": po_set,
        "line_items": line_items,
        "raw_json": raw_json,
        "rendered_images": rendered_images,
    }


# ---------------------------------------------------------------------------
# Rendering Utilities
# ---------------------------------------------------------------------------
def render_pdf_to_images(stored_path: str, doc_id: int, zoom: float = 2.0) -> list[Path]:
    """Renders all pages of a PDF to high-resolution PNG images (144 DPI at zoom=2.0)."""
    if pymupdf is None:
        print("Warning: PyMuPDF is not available. Automatic PDF rendering disabled.")
        return []

    RENDER_DIR.mkdir(parents=True, exist_ok=True)
    pdf_file = Path(stored_path)
    if not pdf_file.exists():
        # Try relative to workspace root
        pdf_file = WORKSPACE_ROOT / stored_path
    if not pdf_file.exists():
        print(f"Warning: PDF file not found at {pdf_file.resolve()}")
        return []

    rendered = []
    try:
        pdf_doc = pymupdf.open(str(pdf_file))
        for p in range(pdf_doc.page_count):
            pix = pdf_doc[p].get_pixmap(matrix=pymupdf.Matrix(zoom, zoom))
            out_path = RENDER_DIR / f"doc{doc_id:03d}_p{p + 1}.png"
            pix.save(str(out_path))
            rendered.append(out_path)
        pdf_doc.close()
    except Exception as e:
        print(f"Error rendering doc {doc_id} ({stored_path}): {e}")

    return rendered


def get_rendered_images_for_doc(doc_id: int, stored_path: str = None) -> list[str]:
    """Finds existing rendered images or renders them on the fly if missing."""
    rendered = sorted([str(p) for p in RENDER_DIR.glob(f"doc{doc_id:03d}_p*.png")])
    if not rendered and stored_path:
        new_renders = render_pdf_to_images(stored_path, doc_id)
        rendered = [str(p) for p in new_renders]
    return rendered


# ---------------------------------------------------------------------------
# Batch & Document Tracking
# ---------------------------------------------------------------------------
def get_existing_batch_files() -> list[tuple[int, Path]]:
    """Returns sorted list of (batch_number, file_path) for all batch findings in prompt_improvement."""
    batches = []
    for f in PROMPT_IMPROVEMENT_DIR.glob("batch_*_findings.md"):
        m = re.search(r"batch_(\d+)_findings\.md", f.name)
        if m:
            batches.append((int(m.group(1)), f))
    return sorted(batches, key=lambda x: x[0])


def get_all_audited_doc_ids() -> set[int]:
    """Scans all existing batch reports in prompt_improvement to extract already audited doc IDs."""
    audited = set()
    for _, fpath in get_existing_batch_files():
        try:
            content = fpath.read_text(encoding="utf-8")
            # Look for patterns like `Doc 123`: or ### Doc 123:
            matches = re.findall(r"(?:###\s*Doc\s*|`Doc\s*)(\d+)", content)
            for m in matches:
                audited.add(int(m))
        except Exception:
            pass

    # Also check ordered_audit_docs.json if available
    if ORDERED_AUDIT_DOCS_PATH.exists():
        try:
            with open(ORDERED_AUDIT_DOCS_PATH, "r", encoding="utf-8") as f:
                ordered_docs = json.load(f)
                for d in ordered_docs:
                    audited.add(d["doc_id"])
        except Exception:
            pass

    return audited


def get_uninspected_doc_ids(conn: sqlite3.Connection, limit: int = None) -> list[int]:
    """Finds documents in SQLite database that have not been included in any batch audit."""
    audited = get_all_audited_doc_ids()
    cur = conn.cursor()
    cur.execute("SELECT id FROM documents ORDER BY id")
    all_db_ids = [r[0] for r in cur.fetchall()]
    uninspected = [did for did in all_db_ids if did not in audited]
    if limit:
        uninspected = uninspected[:limit]
    return uninspected


# ---------------------------------------------------------------------------
# Command Handlers
# ---------------------------------------------------------------------------
def handle_status(args):
    """Displays comprehensive audit statistics and remaining sample queues."""
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) FROM documents")
    total_docs = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM po_sets")
    total_sets = cur.fetchone()[0]

    cur.execute("SELECT status, COUNT(*) FROM po_sets GROUP BY status")
    set_status_counts = dict(cur.fetchall())

    existing_batches = get_existing_batch_files()
    audited_ids = get_all_audited_doc_ids()
    uninspected = get_uninspected_doc_ids(conn)

    print("=====================================================================")
    print("      AAM MERGER V3 — PROMPT IMPROVEMENT & AUDIT HUB STATUS          ")
    print("=====================================================================")
    print(f"• Workspace Root:            {WORKSPACE_ROOT.resolve()}")
    print(f"• Database Location:         {DB_PATH.resolve()}")
    print(f"• Completed Batch Reports:   {len(existing_batches)} batches (Batch 01 to Batch {existing_batches[-1][0]:02d})" if existing_batches else "0 batches")
    print(f"• Total Audited Documents:   {len(audited_ids)} document(s)")
    print(f"• Total Database Documents:  {total_docs} document(s)")
    print(f"• Uninspected Documents:     {len(uninspected)} document(s) remaining in DB")
    if uninspected:
        preview = ", ".join(str(x) for x in uninspected[:10])
        print(f"  Next in queue:             [{preview}{'...' if len(uninspected) > 10 else ''}]")

    print("\n• PO Set Distribution:")
    for status, count in sorted(set_status_counts.items()):
        print(f"  - {status:<15}: {count} set(s)")

    print("\n• PyMuPDF Status:            " + ("Available (High-Res 144 DPI active)" if pymupdf else "Unavailable (Install pymupdf for auto-render)"))
    print("=====================================================================")


def handle_new_batch(args):
    """Automatically detects next batch number, renders images, and scaffolds findings markdown."""
    conn = get_db_connection()
    existing_batches = get_existing_batch_files()
    next_batch_num = (existing_batches[-1][0] + 1) if existing_batches else 1

    doc_ids = []
    if args.docs:
        doc_ids = [int(x.strip()) for x in args.docs.split(",") if x.strip()]
    else:
        doc_ids = get_uninspected_doc_ids(conn, limit=args.limit or 5)

    if not doc_ids:
        print("No uninspected documents found in database.")
        print("All current database documents have already been audited into batches!")
        print("To audit specific documents, pass: --docs <ID1,ID2,...>")
        return

    print(f"Creating Batch {next_batch_num:02d} for {len(doc_ids)} document(s): {doc_ids}...")

    # Render images
    print("Rendering high-resolution page images (144 DPI)...")
    for did in doc_ids:
        cur = conn.cursor()
        cur.execute("SELECT stored_path FROM documents WHERE id = ?", (did,))
        row = cur.fetchone()
        if row and row["stored_path"]:
            stored = row["stored_path"]
            if not Path(stored).is_absolute():
                stored = str(WORKSPACE_ROOT / stored)
            render_pdf_to_images(stored, did)

    # Scaffold report
    scaffold_args = argparse.Namespace(batch=next_batch_num, docs=",".join(str(x) for x in doc_ids))
    handle_scaffold_report(scaffold_args)

    report_path = PROMPT_IMPROVEMENT_DIR / f"batch_{next_batch_num:02d}_findings.md"
    print("\n" + "=" * 68)
    print(f"SUCCESS: Batch {next_batch_num:02d} created!")
    print(f"• Scaffold File:    {report_path.resolve()}")
    print("• Next Steps:")
    print("  1. Visually inspect the rendered images in scratch_dump/render_all/ for docs:")
    print(f"     {doc_ids}")
    print(f"  2. Complete your notes in batch_{next_batch_num:02d}_findings.md")
    print(f"  3. Append report to master: python audit_toolkit.py append-report --file {report_path.name}")
    print("=" * 68)


def handle_retrieve(args):
    """Retrieves batch reports, document details, vendor rules, or PO numbers in standardized format."""
    conn = get_db_connection()

    # 1. Retrieve by Batch
    if args.batch:
        batch_file = PROMPT_IMPROVEMENT_DIR / f"batch_{args.batch:02d}_findings.md"
        if not batch_file.exists():
            # Check workspace root fallback
            batch_file = WORKSPACE_ROOT / f"batch_{args.batch:02d}_findings.md"
        if not batch_file.exists():
            print(f"Error: Batch report {args.batch} not found.")
            return
        content = batch_file.read_text(encoding="utf-8")
        if args.format == "json":
            print(json.dumps({"batch": args.batch, "file": str(batch_file), "content": content}, indent=2))
        else:
            print(content)
        return

    # 2. Retrieve by Document ID
    if args.doc:
        doc_id = args.doc
        info = fetch_doc_details(conn, doc_id)
        if not info:
            print(f"Error: Document {doc_id} not found in database.")
            return

        if args.format == "json":
            # Convert non-serializable elements
            clean_info = dict(info)
            clean_info["rendered_images"] = [str(x) for x in clean_info["rendered_images"]]
            print(json.dumps(clean_info, indent=2, default=str))
            return

        print(f"### Doc {info['doc_id']}: `{info['filename']}`")
        print(f"- **Visual Document Type**: `{info['doc_type']}`")
        print(f"- **DB Document Type**: `{info['doc_type']}`")
        print("- **Extracted Metadata**:")
        print(f"  - PO Raw: `{info['po_no_raw']}` | Normalized: `{info['po_no_normalized']}`")
        if info.get("dn_no"):
            print(f"  - Delivery Note: `{info['dn_no']}`")
        if info.get("si_no"):
            print(f"  - Sales Invoice: `{info['si_no']}`")
        if info.get("invoice_no"):
            print(f"  - Invoice No: `{info['invoice_no']}`")

        status = info["po_set"]["status"] if info["po_set"] else "None"
        reason = info["po_set"]["reconcile_reason"] if info["po_set"] and info["po_set"].get("reconcile_reason") else ""
        print(f"- **Parent PO Set**: {info['po_set_id']} (Status: `{status}`) {f'[{reason}]' if reason else ''}")

        print("- **DB Extracted Items**:")
        for li in info["line_items"]:
            qty = (li["quantity"] / 1000.0) if li["quantity"] is not None else "None"
            price = (li["unit_price"] / 1000.0) if li["unit_price"] is not None else "None"
            print(f"  - Line `{li['line_item_no']}`: `{li['description']}` | Qty `{qty}` | Unit Price `{price}`")

        print("- **Rendered Visual Images**:")
        for img in info["rendered_images"]:
            print(f"  - `{img}`")

        # Also search if this doc is described in any batch findings markdown
        print("\n- **Audit Findings Record Reference**:")
        found_in_batch = False
        for bnum, fpath in get_existing_batch_files():
            content = fpath.read_text(encoding="utf-8")
            if f"Doc {doc_id}" in content or f"Doc {doc_id}:" in content:
                print(f"  - Audited in Batch {bnum:02d} ({fpath.name})")
                found_in_batch = True
        if not found_in_batch:
            print("  - Not yet logged in any finalized batch findings report.")
        return

    # 3. Retrieve by Vendor Name or Keyword
    if args.vendor or args.query:
        query = (args.vendor or args.query).strip().lower()
        print(f"=== RETRIEVING ENTRIES MATCHING: '{query}' ===")

        # A. Search Matrix
        if MATRIX_PATH.exists():
            with open(MATRIX_PATH, "r", encoding="utf-8") as f:
                matrix_lines = f.readlines()
            matrix_matches = [line.strip() for line in matrix_lines if "|" in line and query in line.lower() and not line.startswith("| Company")]
            if matrix_matches:
                print(f"\n[Master Vendor Matrix Matches: {len(matrix_matches)}]")
                for m in matrix_matches:
                    print(f"• {m}")

        # B. Search Batch Reports
        batch_matches = []
        for bnum, fpath in get_existing_batch_files():
            content = fpath.read_text(encoding="utf-8")
            if query in content.lower():
                batch_matches.append((bnum, fpath.name))
        if batch_matches:
            print(f"\n[Batch Findings Mentions: {len(batch_matches)} batches]")
            for bnum, bname in batch_matches:
                print(f"• Batch {bnum:02d}: {bname}")

        # C. Search Database Documents
        cur = conn.cursor()
        cur.execute("SELECT id, original_filename, doc_type, po_no_normalized FROM documents WHERE LOWER(original_filename) LIKE ? OR LOWER(raw_extraction_json) LIKE ?", (f"%{query}%", f"%{query}%"))
        db_rows = cur.fetchall()
        if db_rows:
            print(f"\n[Database Document Matches: {len(db_rows)} documents]")
            for r in db_rows[:15]:
                print(f"• Doc {r[0]}: {r[1]} ({r[2]}) | PO: {r[3]}")
            if len(db_rows) > 15:
                print(f"  ... and {len(db_rows) - 15} more documents.")
        return

    # 4. Retrieve by PO Number
    if args.po:
        po_query = args.po.strip()
        print(f"=== RETRIEVING PO SET & DOCUMENTS FOR PO: '{po_query}' ===")
        cur = conn.cursor()
        cur.execute("SELECT * FROM po_sets WHERE po_no_normalized = ? OR po_no_normalized LIKE ?", (po_query, f"%{po_query}%"))
        sets = cur.fetchall()
        for s in sets:
            set_dict = dict(s)
            print(f"\n• PO Set {set_dict['id']}: PO `{set_dict['po_no_normalized']}` | Status: `{set_dict['status']}`")
            if set_dict.get("reconcile_reason"):
                print(f"  Reconcile Reason: {set_dict['reconcile_reason']}")

            cur.execute("SELECT id, original_filename, doc_type, dn_no, si_no FROM documents WHERE po_set_id = ?", (set_dict["id"],))
            docs = cur.fetchall()
            print(f"  Associated Documents ({len(docs)}):")
            for d in docs:
                print(f"    - Doc {d[0]}: {d[1]} ({d[2]}) | DN: {d[3]} | SI: {d[4]}")
        return

    print("Please specify a retrieval target: --batch, --doc, --vendor, --query, or --po.")


def handle_inspect(args):
    conn = get_db_connection()
    doc_ids = []

    if args.docs:
        doc_ids = [int(x.strip()) for x in args.docs.split(",") if x.strip()]
    elif args.batch:
        batch_num = args.batch
        if ORDERED_AUDIT_DOCS_PATH.exists():
            with open(ORDERED_AUDIT_DOCS_PATH, "r", encoding="utf-8") as f:
                ordered_docs = json.load(f)
            start_idx = (batch_num - 1) * 5
            end_idx = start_idx + 5
            doc_ids = [d["doc_id"] for d in ordered_docs[start_idx:end_idx]]
        else:
            start_id = (batch_num - 1) * 5 + 1
            doc_ids = list(range(start_id, start_id + 5))
    elif args.uninspected:
        doc_ids = get_uninspected_doc_ids(conn, limit=args.limit or 5)

    if not doc_ids:
        print("No documents selected for inspection.")
        return

    print(f"=== INSPECTING {len(doc_ids)} DOCUMENT(S) ===")
    for did in doc_ids:
        info = fetch_doc_details(conn, did)
        if not info:
            print(f"\nDoc {did}: Not found in database.")
            continue

        print(f"\nDoc {info['doc_id']}: {info['filename']} ({info['doc_type']})")
        print(f"  PO Raw: {info['po_no_raw']} | Norm: {info['po_no_normalized']} | DN: {info['dn_no']} | SI: {info['si_no']}")
        status = info["po_set"]["status"] if info["po_set"] else "None"
        reason = info["po_set"]["reconcile_reason"] if info["po_set"] and info["po_set"].get("reconcile_reason") else ""
        print(f"  PO Set: {info['po_set_id']} (Status: {status}) {f'[{reason}]' if reason else ''}")
        print(f"  Line items count: {len(info['line_items'])}")
        for li in info["line_items"]:
            qty = (li["quantity"] / 1000.0) if li["quantity"] is not None else "None"
            price = (li["unit_price"] / 1000.0) if li["unit_price"] is not None else "None"
            print(f"    Line {li['line_item_no']}: {li['description']} | Qty: {qty} | Price: {price}")
        print(f"  Rendered images: {info['rendered_images']}")


def handle_render(args):
    conn = get_db_connection()
    doc_ids = [int(x.strip()) for x in args.docs.split(",") if x.strip()]
    print(f"Rendering {len(doc_ids)} document(s) to 144 DPI PNGs...")
    for did in doc_ids:
        cur = conn.cursor()
        cur.execute("SELECT stored_path FROM documents WHERE id = ?", (did,))
        row = cur.fetchone()
        if not row:
            print(f"Doc {did}: Not found in database.")
            continue
        stored = row["stored_path"]
        if not Path(stored).is_absolute():
            stored = str(WORKSPACE_ROOT / stored)
        imgs = render_pdf_to_images(stored, did)
        print(f"Doc {did}: Rendered {len(imgs)} page(s) -> {[str(p) for p in imgs]}")


def handle_scaffold_report(args):
    """Scaffolds a batch report in the exact standardized markdown format."""
    conn = get_db_connection()
    doc_ids = []
    if args.docs:
        doc_ids = [int(x.strip()) for x in args.docs.split(",") if x.strip()]
    elif args.batch:
        start_id = (args.batch - 1) * 5 + 1
        doc_ids = list(range(start_id, start_id + 5))

    items = []
    for did in doc_ids:
        info = fetch_doc_details(conn, did)
        if info:
            items.append(info)

    if not items:
        print("No documents found to scaffold.")
        return

    batch_num = args.batch if args.batch else 99
    report_file = PROMPT_IMPROVEMENT_DIR / f"batch_{batch_num:02d}_findings.md"

    lines = []
    lines.append(f"# Audit Findings — Batch {batch_num:02d} (Docs {items[0]['doc_id']} - {items[-1]['doc_id']})\n")
    lines.append("## 1. Batch Overview\n- **Documents Audited**:")
    for it in items:
        status = it["po_set"]["status"] if it["po_set"] else "None"
        lines.append(f"  - `Doc {it['doc_id']}`: `{it['filename']}` ({it['doc_type']}) — PO Set {it['po_set_id']} (`{status}`)")

    vendors = {it["raw_json"].get("vendor_name", "Unknown Vendor") for it in items if it.get("raw_json")}
    clean_vendors = [v for v in vendors if v and v != "Unknown Vendor"]
    vendor_display = ", ".join(sorted(clean_vendors)) if clean_vendors else "[Inspect and record vendor names]"
    lines.append(f"- **Primary Companies/Vendors**:\n  - {vendor_display}\n")
    lines.append("---\n")
    lines.append("## 2. Document-by-Document Visual vs Database Audit\n")

    for it in items:
        lines.append(f"### Doc {it['doc_id']}: `{it['filename']}`")
        lines.append(f"- **Visual Document Type**: `{it['doc_type']}`")
        lines.append(f"- **DB Document Type**: `{it['doc_type']}`")
        lines.append("- **Visual Metadata**:")
        lines.append(f"  - Document Number: `{it['invoice_no'] or it['dn_no'] or it['po_no_raw'] or '[Visual Doc No]'}`")
        lines.append(f"  - PO Reference: `{it['po_no_raw'] or '[Visual PO No]'}`")
        lines.append("- **Visual Line Items**:")
        lines.append("  - [Verify visible line numbers, descriptions, quantities, and prices on rendered image]")
        lines.append("- **DB Extracted Items**:")
        lines.append(f"  - `po_no_normalized`: `{it['po_no_normalized']}`")
        if it.get("si_no"):
            lines.append(f"  - `si_no`: `{it['si_no']}`")
        if it.get("dn_no"):
            lines.append(f"  - `dn_no`: `{it['dn_no']}`")
        for li in it["line_items"]:
            qty = (li["quantity"] / 1000.0) if li["quantity"] is not None else "None"
            price = (li["unit_price"] / 1000.0) if li["unit_price"] is not None else "None"
            lines.append(f"  - Line `{li['line_item_no']}`: `{li['description']}` | Qty `{qty}` | Unit Price `{price}`")
        lines.append(f"- **Rendered Images**: `{it['rendered_images']}`")
        lines.append("")

    lines.append("---\n")
    lines.append("## 3. Brand & Document Diagnostics\n")
    lines.append("| Attribute | Visual Feature on Document | Prompt Engineering Implication |")
    lines.append("|---|---|---|")
    lines.append("| **Header Placement** | [Describe ROI location & labels on page 1] | [Boundary or label rule] |")
    lines.append("| **Line Numbering** | [Describe column header and format (e.g. 1, 10, 1-1)] | [Step-10 / serial / release rule] |")
    lines.append("| **Financial Fields** | [Currency and decimal markings (e.g. SAR, BHD 3-dec, comma dec)] | [Decimal & sanitizer rule] |\n")

    lines.append("---\n")
    lines.append("## 4. Prompt Engineering & Pipeline Recommendations\n")
    lines.append("1. **Header Extraction Rules**: [State specific rules or label aliases for this vendor]\n")
    lines.append("2. **Line Item Reconciliation**: [State any line key normalization required]\n")

    report_content = "\n".join(lines)
    with open(report_file, "w", encoding="utf-8") as f:
        f.write(report_content)
    print(f"Scaffolded report created at {report_file.resolve()}")


def handle_append_report(args):
    file_path = Path(args.file)
    if not file_path.is_absolute():
        if (PROMPT_IMPROVEMENT_DIR / file_path).exists():
            file_path = PROMPT_IMPROVEMENT_DIR / file_path
        elif (WORKSPACE_ROOT / file_path).exists():
            file_path = WORKSPACE_ROOT / file_path

    if not file_path.exists():
        print(f"Error: File not found at {file_path.resolve()}")
        sys.exit(1)

    content = file_path.read_text(encoding="utf-8").strip()

    with open(AUDIT_REPORT_PATH, "a", encoding="utf-8") as f:
        f.write("\n\n" + content + "\n")
    print(f"Successfully appended {file_path.name} to {AUDIT_REPORT_PATH.name}.")


def handle_matrix(args):
    if not MATRIX_PATH.exists():
        print(f"Error: Matrix file not found at {MATRIX_PATH.resolve()}")
        sys.exit(1)

    with open(MATRIX_PATH, "r", encoding="utf-8") as f:
        lines = f.readlines()

    if args.list:
        print("=== REGISTERED VENDORS IN EXTRACTION MATRIX ===")
        for line in lines:
            if line.startswith("| **"):
                parts = [p.strip() for p in line.split("|")]
                if len(parts) >= 3:
                    vendor = parts[1].replace("**", "")
                    doc_type = parts[2].replace("**", "")
                    print(f"• {vendor:<45} | Doc Type: {doc_type}")
        return

    if args.query:
        query = args.query.lower()
        matches = []
        for line in lines:
            if "|" in line and query in line.lower() and not line.startswith("| Company"):
                matches.append(line.strip())

        if matches:
            print(f"Found {len(matches)} matching entry/entries in {MATRIX_PATH.name}:")
            for m in matches:
                print(f"\n{m}")
        else:
            print(f"No entries matching '{args.query}' found in {MATRIX_PATH.name}.")


# ---------------------------------------------------------------------------
# CLI Argument Parsing
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="AAM Merger V3 — Audit & Prompt Improvement Toolkit")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # status
    subparsers.add_parser("status", help="Display audit overview, database totals, and uninspected queue")

    # new-batch
    new_batch_parser = subparsers.add_parser("new-batch", help="Auto-detect next batch, render PNGs, and scaffold report")
    new_batch_parser.add_argument("--docs", type=str, help="Specific comma-separated document IDs (optional)")
    new_batch_parser.add_argument("--limit", type=int, default=5, help="Number of documents to include (default: 5)")

    # retrieve
    retrieve_parser = subparsers.add_parser("retrieve", help="Retrieve batch findings, documents, or vendor rules")
    retrieve_parser.add_argument("--batch", type=int, help="Batch number (e.g. 25)")
    retrieve_parser.add_argument("--doc", type=int, help="Document ID (e.g. 163)")
    retrieve_parser.add_argument("--vendor", type=str, help="Vendor name or client name")
    retrieve_parser.add_argument("--query", type=str, help="Search query across batches and matrix")
    retrieve_parser.add_argument("--po", type=str, help="PO number to retrieve documents and PO set")
    retrieve_parser.add_argument("--format", choices=["text", "md", "json"], default="text", help="Output format")

    # inspect
    inspect_parser = subparsers.add_parser("inspect", help="Inspect document records, line items, and images")
    inspect_parser.add_argument("--batch", type=int, help="Batch number (1..N, 5 docs per batch)")
    inspect_parser.add_argument("--docs", type=str, help="Comma-separated document IDs (e.g. 194,195,196)")
    inspect_parser.add_argument("--uninspected", action="store_true", help="Inspect uninspected document queue")
    inspect_parser.add_argument("--limit", type=int, default=5, help="Limit number of uninspected documents")

    # render
    render_parser = subparsers.add_parser("render", help="Render PDF pages to PNG images")
    render_parser.add_argument("--docs", type=str, required=True, help="Comma-separated document IDs")

    # scaffold-report
    scaffold_parser = subparsers.add_parser("scaffold-report", help="Generate a standardized batch markdown template")
    scaffold_parser.add_argument("--batch", type=int, required=True, help="Batch number")
    scaffold_parser.add_argument("--docs", type=str, help="Comma-separated document IDs")

    # append-report
    append_parser = subparsers.add_parser("append-report", help="Append completed markdown findings to master report")
    append_parser.add_argument("--file", type=str, required=True, help="Path or filename of markdown batch report")

    # matrix
    matrix_parser = subparsers.add_parser("matrix", help="Query or list vendors in the master extraction matrix")
    matrix_parser.add_argument("--query", type=str, help="Search term (vendor name, doc type, or client)")
    matrix_parser.add_argument("--list", action="store_true", help="List all registered vendors in matrix")

    args = parser.parse_args()

    if args.command == "status":
        handle_status(args)
    elif args.command == "new-batch":
        handle_new_batch(args)
    elif args.command == "retrieve":
        handle_retrieve(args)
    elif args.command == "inspect":
        handle_inspect(args)
    elif args.command == "render":
        handle_render(args)
    elif args.command == "scaffold-report":
        handle_scaffold_report(args)
    elif args.command == "append-report":
        handle_append_report(args)
    elif args.command == "matrix":
        handle_matrix(args)


if __name__ == "__main__":
    main()
