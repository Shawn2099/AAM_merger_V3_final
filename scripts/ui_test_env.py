"""Build (and optionally serve) a deterministic demo environment for UI testing.

Every state an operator can meet is created by the REAL pipeline code
(`ingest_file`, `extract_document`, `group_document`, `reconcile_po_set`); only the
VLM call is replaced by a canned reply, so there is no network access and no cost.
The result is a throwaway tree (config, DB, stored PDFs, real merged packets) plus
`manifest.json` that tells a tester what every scenario is, so tests assert against
known data instead of guessing.

    uv run python scripts/ui_test_env.py --root data/ui_test            # build only
    uv run python scripts/ui_test_env.py --root data/ui_test --serve    # build + serve

The root directory is wiped on every build, so its name must contain "ui_test".
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

#: The one secret a harness run must never be able to reach.
PROVIDER_KEY_ENV_VAR = "OPENROUTER_API_KEY"


def prepare_provider_free_environment() -> None:
    """Make this process fail closed on the provider key (H-01).

    Popping the variable is not enough: `load_config()` reads `.env` files
    itself, after import, so a `.env` beside the config (or the developer's own
    at the repository root) put a live key back and a "no network" harness made
    real, paid VLM calls.

    `dotenv.load_dotenv(override=False)` leaves a variable that is already
    present alone, and an empty string is present. Setting `""` therefore wins
    over every `.env` the loader tries, and `_call_vlm` raises instead of
    calling the provider.
    """
    os.environ[PROVIDER_KEY_ENV_VAR] = ""


def provider_free_env() -> dict[str, str]:
    """The environment for a harness child process: same, with the key blanked."""
    env = os.environ.copy()
    env[PROVIDER_KEY_ENV_VAR] = ""
    return env


# Scenario table. `mutable` scenarios are meant to be changed by a test (force merge,
# archive, reclassify, ...): each such test owns ONE scenario so tests stay independent.
# Lines are (line_no, qty, description). A scenario's `docs` list is processed in order
# (PO first, because only a PO can create a set).
L = lambda no, qty, desc="Widget": {  # noqa: E731
    "line_item_no": no,
    "description": desc,
    "quantity": qty,
    "dn_no": None,
}

HOSTILE_NAME = '<img src=x onerror="window.__xss=1"><b>bold</b>.pdf'

SCENARIOS: dict[str, dict] = {
    "merged_basic": {
        "po": "7000001",
        "docs": [
            ("PO", "PO-7000001.pdf", None, [L("1", "10", "Hex bolt"), L("2", "5", "Nut")]),
            ("DN", "DN-7000001.pdf", "7000001", [L("1", "10", "Hex bolt"), L("2", "5", "Nut")]),
            ("SI", "INV-7000001.pdf", "7000001", [L("1", "10", "Hex bolt"), L("2", "5", "Nut")]),
        ],
    },
    "merged_mapped_x10": {
        "po": "7000002",
        "docs": [
            ("PO", "PO-7000002.pdf", None, [L("10", "4", "Valve"), L("20", "6", "Gasket")]),
            ("DN", "DN-7000002.pdf", "7000002", [L("1", "4", "Valve"), L("2", "6", "Gasket")]),
            ("SI", "INV-7000002.pdf", "7000002", [L("1", "4", "Valve"), L("2", "6", "Gasket")]),
        ],
    },
    "pending_no_dn": {
        "po": "7000003",
        "docs": [
            ("PO", "PO-7000003.pdf", None, [L("1", "8", "Pipe")]),
            ("SI", "INV-7000003.pdf", "7000003", [L("1", "8", "Pipe")]),
        ],
    },
    "pending_partial_delivery": {
        "po": "7000004",
        "docs": [
            ("PO", "PO-7000004.pdf", None, [L("1", "10", "Cable"), L("2", "5", "Lug")]),
            ("DN", "DN-7000004.pdf", "7000004", [L("2", "5", "Lug")]),
            ("SI", "INV-7000004.pdf", "7000004", [L("2", "5", "Lug")]),
        ],
    },
    "pending_vendor_line_not_on_po": {
        "po": "7000005",
        "docs": [
            ("PO", "PO-7000005.pdf", None, [L("1", "100", "Bin")]),
            ("SI", "INV-7000005.pdf", "7000005", [L("1", "50", "Bin"), L("2", "50", "Bin")]),
        ],
    },
    "pending_two_po_documents": {
        "po": "7000006",
        "docs": [
            ("PO", "PO-7000006-p1.pdf", None, [L("1", "3", "Tool")]),
            ("PO", "PO-7000006-p2.pdf", None, [L("2", "4", "Tool B")]),
            ("DN", "DN-7000006.pdf", "7000006", [L("1", "3", "Tool"), L("2", "4", "Tool B")]),
            ("SI", "INV-7000006.pdf", "7000006", [L("1", "3", "Tool"), L("2", "4", "Tool B")]),
        ],
    },
    "needs_review_quantity": {
        "po": "7000007",
        "docs": [
            ("PO", "PO-7000007.pdf", None, [L("1", "10", "Hose")]),
            ("DN", "DN-7000007.pdf", "7000007", [L("1", "8", "Hose")]),
            ("SI", "INV-7000007.pdf", "7000007", [L("1", "10", "Hose")]),
        ],
    },
    "force_merge_target": {
        "po": "7000008",
        "mutable": True,
        "docs": [
            ("PO", "PO-7000008.pdf", None, [L("1", "10", "Clamp")]),
            ("DN", "DN-7000008.pdf", "7000008", [L("1", "7", "Clamp")]),
            ("SI", "INV-7000008.pdf", "7000008", [L("1", "10", "Clamp")]),
        ],
    },
    "force_merge_validation_target": {
        "po": "7000015",
        "mutable": True,
        "docs": [
            ("PO", "PO-7000015.pdf", None, [L("1", "10", "Rope")]),
            ("DN", "DN-7000015.pdf", "7000015", [L("1", "6", "Rope")]),
            ("SI", "INV-7000015.pdf", "7000015", [L("1", "10", "Rope")]),
        ],
    },
    "archive_modal_target": {
        "po": "7000016",
        "mutable": True,
        "docs": [
            ("PO", "PO-7000016.pdf", None, [L("1", "5", "Chain")]),
            ("DN", "DN-7000016.pdf", "7000016", [L("6", "5", "Chain")]),
            ("SI", "INV-7000016.pdf", "7000016", [L("1", "5", "Chain")]),
        ],
    },
    "awaiting_customs": {
        "po": "7000009",
        "customs_toggle": True,
        "docs": [
            ("PO", "PO-7000009.pdf", None, [L("1", "2", "Pump")]),
            ("DN", "DN-7000009.pdf", "7000009", [L("1", "2", "Pump")]),
            ("SI", "INV-7000009.pdf", "7000009", [L("1", "2", "Pump")]),
        ],
    },
    "quarantined_po_no_lines": {
        "po": "7000010",
        "docs": [
            ("PO", "PO-7000010.pdf", None, []),
            ("DN", "DN-7000010.pdf", "7000010", [L("1", "1", "Seal")]),
            ("SI", "INV-7000010.pdf", "7000010", [L("1", "1", "Seal")]),
        ],
    },
    "quarantined_unmatched_vendor_line": {
        "po": "7000011",
        "docs": [
            ("PO", "PO-7000011.pdf", None, [L("1", "5", "Filter")]),
            ("DN", "DN-7000011.pdf", "7000011", [L("9", "5", "Filter")]),
            ("SI", "INV-7000011.pdf", "7000011", [L("1", "5", "Filter")]),
        ],
    },
    # S-16's own target. It must not reuse another test's set: a re-extract
    # resets the set's verdict, and a session-scoped harness shares the data
    # (S-04 inspects quarantined_unmatched_vendor_line).
    "reextract_target": {
        "po": "7000017",
        "mutable": True,
        "docs": [
            ("PO", "PO-7000017.pdf", None, [L("1", "5", "Bracket")]),
            ("DN", "DN-7000017.pdf", "7000017", [L("9", "5", "Bracket")]),
            ("SI", "INV-7000017.pdf", "7000017", [L("1", "5", "Bracket")]),
        ],
    },
    "archive_target": {
        "po": "7000012",
        "mutable": True,
        "docs": [
            ("PO", "PO-7000012.pdf", None, [L("1", "5", "Belt")]),
            ("DN", "DN-7000012.pdf", "7000012", [L("7", "5", "Belt")]),
            ("SI", "INV-7000012.pdf", "7000012", [L("1", "5", "Belt")]),
        ],
    },
    "customs_toggle_target": {
        "po": "7000013",
        "mutable": True,
        "docs": [
            ("PO", "PO-7000013.pdf", None, [L("1", "5", "Fan")]),
            ("SI", "INV-7000013.pdf", "7000013", [L("1", "5", "Fan")]),
        ],
    },
    "upload_customs_target": {
        "po": "7000014",
        "mutable": True,
        "docs": [
            ("PO", "PO-7000014.pdf", None, [L("1", "5", "Lamp")]),
            ("SI", "INV-7000014.pdf", "7000014", [L("1", "5", "Lamp")]),
        ],
    },
    # S-21's own target: customs ON, a CUSTOMS and a SHIPPING file attached, no DN
    # yet, so the set stays `pending` and the Remove buttons are offered.
    "customs_remove_target": {
        "po": "7000018",
        "mutable": True,
        "customs_toggle": True,
        "supporting": [("CUSTOMS", "CUSTOMS-7000018.pdf"), ("SHIPPING", "AWB-7000018.pdf")],
        "docs": [
            ("PO", "PO-7000018.pdf", None, [L("1", "5", "Rivet")]),
            ("SI", "INV-7000018.pdf", "7000018", [L("1", "5", "Rivet")]),
        ],
    },
    # Settle window (D5): reconciled, but the newest file is younger than the
    # settle time, so the engine holds it. Only these sets are reconciled with
    # SETTLE_MINUTES set (see build()).
    "settling": {
        "po": "7000019",
        "settle_minutes": 240,
        "docs": [
            ("PO", "PO-7000019.pdf", None, [L("1", "4", "Spring")]),
            ("DN", "DN-7000019.pdf", "7000019", [L("1", "4", "Spring")]),
            ("SI", "INV-7000019.pdf", "7000019", [L("1", "4", "Spring")]),
        ],
    },
    "settling_merge_now_target": {
        "po": "7000020",
        "mutable": True,
        "settle_minutes": 240,
        "docs": [
            ("PO", "PO-7000020.pdf", None, [L("1", "6", "Washer")]),
            ("DN", "DN-7000020.pdf", "7000020", [L("1", "6", "Washer")]),
            ("SI", "INV-7000020.pdf", "7000020", [L("1", "6", "Washer")]),
        ],
    },
    # D9: the "-b" DN prints no PO number. Its DN number (DN-<po>, same as the
    # first DN) places it by number only, so the set holds. It carries a
    # DIFFERENT line number, or after Confirm the duplicate-DN guard would
    # quarantine the set instead of merging it.
    "placed_by_number_only": {
        "po": "7000021",
        "docs": [
            ("PO", "PO-7000021.pdf", None, [L("1", "5", "Gear"), L("2", "3", "Shaft")]),
            ("DN", "DN-7000021-a.pdf", "7000021", [L("1", "5", "Gear")]),
            ("DN", "DN-7000021-b.pdf", None, [L("2", "3", "Shaft")]),
            ("SI", "INV-7000021.pdf", "7000021", [L("1", "5", "Gear"), L("2", "3", "Shaft")]),
        ],
    },
    "confirm_placement_target": {
        "po": "7000022",
        "mutable": True,
        "docs": [
            ("PO", "PO-7000022.pdf", None, [L("1", "5", "Cog"), L("2", "3", "Axle")]),
            ("DN", "DN-7000022-a.pdf", "7000022", [L("1", "5", "Cog")]),
            ("DN", "DN-7000022-b.pdf", None, [L("2", "3", "Axle")]),
            ("SI", "INV-7000022.pdf", "7000022", [L("1", "5", "Cog"), L("2", "3", "Axle")]),
        ],
    },
    # S-13's own target (pending_no_dn is changed by U-04, so it cannot be used).
    "rematch_target": {
        "po": "7000023",
        "mutable": True,
        "docs": [
            ("PO", "PO-7000023.pdf", None, [L("1", "7", "Hinge")]),
            ("SI", "INV-7000023.pdf", "7000023", [L("1", "7", "Hinge")]),
        ],
    },
    "merge_now_mismatch_target": {
        "po": "7000024",
        "mutable": True,
        "docs": [
            ("PO", "PO-7000024.pdf", None, [L("1", "10", "Valve seat")]),
            ("DN", "DN-7000024.pdf", "7000024", [L("1", "8", "Valve seat")]),
            ("SI", "INV-7000024.pdf", "7000024", [L("1", "10", "Valve seat")]),
        ],
    },
    # S-14b's own target (S-14 owns merge_now_mismatch_target).
    "merge_now_reason_target": {
        "po": "7000029",
        "mutable": True,
        "docs": [
            ("PO", "PO-7000029.pdf", None, [L("1", "10", "Seal ring")]),
            ("DN", "DN-7000029.pdf", "7000029", [L("1", "8", "Seal ring")]),
            ("SI", "INV-7000029.pdf", "7000029", [L("1", "10", "Seal ring")]),
        ],
    },
    # A-02's own target: a force merge with a justification, then the audit page.
    "audit_force_merge_target": {
        "po": "7000025",
        "mutable": True,
        "docs": [
            ("PO", "PO-7000025.pdf", None, [L("1", "10", "Flange")]),
            ("DN", "DN-7000025.pdf", "7000025", [L("1", "9", "Flange")]),
            ("SI", "INV-7000025.pdf", "7000025", [L("1", "10", "Flange")]),
        ],
    },
    # A-03's own target: archiving it must leave a `quarantine_delete` audit row.
    "audit_archive_target": {
        "po": "7000026",
        "mutable": True,
        "docs": [
            ("PO", "PO-7000026.pdf", None, [L("1", "5", "Gland")]),
            ("DN", "DN-7000026.pdf", "7000026", [L("9", "5", "Gland")]),
            ("SI", "INV-7000026.pdf", "7000026", [L("1", "5", "Gland")]),
        ],
    },
    # Quarantined, then archived during build(): S-20 checks it is read-only.
    "archived_readonly": {
        "po": "7000027",
        "archive_in_build": True,
        "docs": [
            ("PO", "PO-7000027.pdf", None, [L("1", "5", "Bush")]),
            ("DN", "DN-7000027.pdf", "7000027", [L("8", "5", "Bush")]),
            ("SI", "INV-7000027.pdf", "7000027", [L("1", "5", "Bush")]),
        ],
    },
    # X-02: the second PO document's stored filename is replaced by HTML after
    # the build loop (before reconcile, so the reason names it too).
    "hostile_name_in_set": {
        "po": "7000028",
        "hostile_doc_index": 1,
        "docs": [
            ("PO", "PO-7000028-p1.pdf", None, [L("1", "2", "Pin")]),
            ("PO", "PO-7000028-p2.pdf", None, [L("1", "2", "Pin")]),
            ("DN", "DN-7000028.pdf", "7000028", [L("1", "2", "Pin")]),
            ("SI", "INV-7000028.pdf", "7000028", [L("1", "2", "Pin")]),
        ],
    },
    # Batch 1 audit: a mutable mismatched set for the remove-set browser test.
    "remove_set_target": {
        "po": "7000030",
        "mutable": True,
        "docs": [
            ("PO", "PO-7000030.pdf", None, [L("1", "10", "Bearing")]),
            ("DN", "DN-7000030.pdf", "7000030", [L("1", "6", "Bearing")]),
            ("SI", "INV-7000030.pdf", "7000030", [L("1", "10", "Bearing")]),
        ],
    },
    # Batch 1 audit: customs ON with both files attached, no DN yet. Turning the
    # requirement OFF must be refused until the files are deleted.
    "customs_toggle_refusal_target": {
        "po": "7000031",
        "mutable": True,
        "customs_toggle": True,
        "supporting": [("CUSTOMS", "CUSTOMS-7000031.pdf"), ("SHIPPING", "AWB-7000031.pdf")],
        "docs": [
            ("PO", "PO-7000031.pdf", None, [L("1", "4", "Gasket")]),
            ("SI", "INV-7000031.pdf", "7000031", [L("1", "4", "Gasket")]),
        ],
    },
    # Batch 1 audit: a merged packet that really contains customs pages.
    "merged_with_customs": {
        "po": "7000032",
        "customs_toggle": True,
        "supporting": [("CUSTOMS", "CUSTOMS-7000032.pdf"), ("SHIPPING", "AWB-7000032.pdf")],
        "docs": [
            ("PO", "PO-7000032.pdf", None, [L("1", "5", "Bolt")]),
            ("DN", "DN-7000032.pdf", "7000032", [L("1", "5", "Bolt")]),
            ("SI", "INV-7000032.pdf", "7000032", [L("1", "5", "Bolt")]),
        ],
    },
    # Batch 1 audit: customs OFF, but the packet was written with the files
    # attached, so the page must say "Yes" and show the "switched off" note.
    "merged_customs_off_with_docs": {
        "po": "7000033",
        "supporting": [("CUSTOMS", "CUSTOMS-7000033.pdf"), ("SHIPPING", "AWB-7000033.pdf")],
        "docs": [
            ("PO", "PO-7000033.pdf", None, [L("1", "6", "Nut")]),
            ("DN", "DN-7000033.pdf", "7000033", [L("1", "6", "Nut")]),
            ("SI", "INV-7000033.pdf", "7000033", [L("1", "6", "Nut")]),
        ],
    },
    # Batch 1 audit: pending (no DN), so Re-Match reports a reason, not a merge.
    "rematch_toast_target": {
        "po": "7000034",
        "mutable": True,
        "docs": [
            ("PO", "PO-7000034.pdf", None, [L("1", "7", "Hinge")]),
            ("SI", "INV-7000034.pdf", "7000034", [L("1", "7", "Hinge")]),
        ],
    },
    # Batch 1 audit: two toggles in a row must leave two audit rows.
    "customs_toggle_audit_target": {
        "po": "7000035",
        "mutable": True,
        "docs": [
            ("PO", "PO-7000035.pdf", None, [L("1", "5", "Fan")]),
            ("SI", "INV-7000035.pdf", "7000035", [L("1", "5", "Fan")]),
        ],
    },
    # Batch 1 audit: a mismatched set used to produce a force-merged dashboard label.
    "force_merge_label_target": {
        "po": "7000036",
        "mutable": True,
        "docs": [
            ("PO", "PO-7000036.pdf", None, [L("1", "10", "Clamp")]),
            ("DN", "DN-7000036.pdf", "7000036", [L("1", "9", "Clamp")]),
            ("SI", "INV-7000036.pdf", "7000036", [L("1", "10", "Clamp")]),
        ],
    },
    # Batch 1 audit: a PO line with no number, linked by description.
    "description_match_target": {
        "po": "7000037",
        "docs": [
            (
                "PO",
                "PO-7000037.pdf",
                None,
                [{"line_item_no": None, "description": "Widget", "quantity": 5, "dn_no": None}],
            ),
            ("DN", "DN-7000037.pdf", "7000037", [L("1", "5", "Widget")]),
            ("SI", "INV-7000037.pdf", "7000037", [L("1", "5", "Widget")]),
        ],
    },
}

# Documents that stay in the holding area (the Unclassified page). `po_ref` None means the
# document prints no PO number. kind: DN/SI/UNKNOWN. `state`: read | not_read | failed.
HELD: dict[str, dict] = {
    "held_dn_no_po": {
        "kind": "DN",
        "file": "DN-NOPO.pdf",
        "po_ref": None,
        "state": "read",
        "lines": [L("1", "3", "Orphan item")],
    },
    "held_ambiguous_po": {
        "kind": "DN",
        "file": "DN-AMBIG.pdf",
        "po_ref": "7000001",
        "state": "read",
        "ambiguous": True,
        "lines": [L("1", "1", "Ambiguous item")],
    },
    "held_not_read": {
        "kind": "UNKNOWN",
        "file": "NOT-READ.pdf",
        "po_ref": None,
        "state": "not_read",
        "lines": [],
    },
    "held_failed": {
        "kind": "UNKNOWN",
        "file": "FAILED-READ.pdf",
        "po_ref": None,
        "state": "failed",
        "lines": [],
    },
    # Its stored filename is replaced by HTML after the build: a page must escape it.
    "held_hostile_name": {
        "kind": "DN",
        "file": "hostile.pdf",
        "po_ref": None,
        "state": "read",
        "hostile": True,
        "lines": [L("1", "1", "Hostile item")],
    },
    "held_reclassify_target": {
        "kind": "DN",
        "file": "DN-RECLASSIFY.pdf",
        "po_ref": None,
        "state": "read",
        "mutable": True,
        "lines": [L("1", "5", "Fan")],
    },
    # Same situation, but no PO number is ever typed: the route answers with a
    # message row AND HX-Refresh, so the message must survive the reload.
    "held_reclassify_no_po": {
        "kind": "DN",
        "file": "DN-HOLD.pdf",
        "po_ref": None,
        "state": "read",
        "mutable": True,
        "lines": [L("1", "2", "Held item")],
    },
    # U-10's own document: reclassified as a DN with a fresh PO number (7000090).
    "held_count_target": {
        "kind": "DN",
        "file": "DN-COUNT.pdf",
        "po_ref": None,
        "state": "read",
        "mutable": True,
        "lines": [L("1", "2", "Count item")],
    },
    # Batch 1 audit: a valid held document, for the set-aside report test.
    "held_set_aside_target": {
        "kind": "DN",
        "file": "DN-SET-ASIDE.pdf",
        "po_ref": None,
        "state": "read",
        "mutable": True,
        "lines": [L("1", "3", "Orphan item")],
    },
    # Batch 1 audit: a failed held document, for the quarantine-page redirect test.
    "held_set_aside_failed_target": {
        "kind": "UNKNOWN",
        "file": "FAILED-SET-ASIDE.pdf",
        "po_ref": None,
        "state": "failed",
        "mutable": True,
        "lines": [],
    },
    # Batch 1 audit: reclassify this one and check the audit row.
    "held_reclassify_audit_target": {
        "kind": "DN",
        "file": "DN-RECLASSIFY-AUDIT.pdf",
        "po_ref": None,
        "state": "read",
        "mutable": True,
        "lines": [L("1", "4", "Audit item")],
    },
}


def _pdf(path: Path, width: int, pages: int = 1) -> Path:
    from pypdf import PdfWriter

    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(width=width, height=200)
    path.parent.mkdir(parents=True, exist_ok=True)
    w.write(str(path))
    return path


def _config(root: Path) -> Path:
    import yaml

    data = yaml.safe_load((REPO / "config.example.yaml").read_text(encoding="utf-8"))
    data["paths"] = {
        "input_folder": str(root / "input"),
        "output_folder": str(root / "output"),
        "quarantine_folder": str(root / "quarantine"),
        "stored_documents_folder": str(root / "stored"),
        "database_path": str(root / "aam_merger.db"),
        "log_folder": str(root / "logs"),
        "combined_folder": str(root / "combined"),
        "archive_folder": str(root / "archive"),
    }
    data["manual_merge"]["temp_folder"] = str(root / "manual_merge_tmp")
    data["schedule"]["poll_enabled"] = False  # UI tests must be deterministic
    data["server"]["allowed_hosts"] = ["*"]
    # The fail-closed path (no provider key) retries a document before giving
    # up. Real seconds of sleeping buy a UI test nothing: the code path and the
    # attempt count are identical, only the wall clock is shorter (S-16).
    data["extraction"]["retry_backoff_seconds"] = [0, 0, 0]
    path = root / "config.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def build(root: Path) -> dict:
    """Wipe and rebuild `root`; return the manifest."""
    if "ui_test" not in root.name:
        raise SystemExit(f"refusing to wipe {root}: its name must contain 'ui_test'")
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    # Fail closed on anything that could reach a provider. The empty `.env`
    # beside the config is defence in depth: even a child that forgets to
    # blank the environment cannot pick a key up from this folder.
    prepare_provider_free_environment()
    (root / ".env").write_text(f"{PROVIDER_KEY_ENV_VAR}=\n", encoding="utf-8")
    config_path = _config(root)
    os.environ["AAM_CONFIG_PATH"] = str(config_path)

    from sqlalchemy.orm import Session

    import app.services.extraction as extraction
    from app.core.config import load_config
    from app.core.database import get_engine
    from app.models import (
        AuditAction,
        AuditLog,
        DocType,
        Document,
        ExtractionStatus,
        POSet,
    )
    from app.models.base import Base
    from app.services.extraction import extract_document
    from app.services.grouping import (
        attach_unattached_to_open_sets,
        group_document,
        resolve_unattached_documents,
    )
    from app.services.ingestion import ingest_file
    from app.services.reconciliation import reconcile_po_set

    cfg = load_config(str(config_path))
    for name in ("input_folder", "output_folder", "quarantine_folder", "stored_documents_folder"):
        Path(getattr(cfg.paths, name)).mkdir(parents=True, exist_ok=True)
    eng = get_engine(cfg)
    Base.metadata.create_all(eng)

    payloads: dict[str, dict] = {}

    def fake_call_vlm(stored_path, dtype, cfg_):  # the only thing replaced: no network
        return payloads[Path(stored_path).name]

    extraction._call_vlm = fake_call_vlm  # type: ignore[assignment]

    width = [200]

    def make_doc(kind, filename, doc_no, po_ref, lines, ambiguous=False):
        width[0] += 3  # distinct bytes -> distinct SHA
        path = _pdf(Path(cfg.paths.input_folder) / filename, width[0])
        doc = ingest_file(path, cfg)
        payloads[Path(doc.stored_path).name] = {
            "document_type": kind,
            "document_number": doc_no,
            "po_no_raw": po_ref,
            "po_reference": po_ref,
            "po_reference_ambiguous": ambiguous,
            "vendor_name": "ACME Supplies",
            "components": [],
            "line_items": lines,
        }
        return doc.id

    scenario_docs: dict[str, list[int]] = {}
    for name, sc in SCENARIOS.items():
        ids = []
        for kind, filename, po_ref, lines in sc["docs"]:
            number = sc["po"] if kind == "PO" else f"{kind}-{sc['po']}"
            doc_id = make_doc(kind, filename, number, sc["po"] if kind == "PO" else po_ref, lines)
            extract_document(doc_id, cfg)
            with Session(eng) as s:
                d = s.get(Document, doc_id)
                ps_id, _problem = group_document(d, cfg)
                if ps_id is not None and d.po_set_id is None:
                    d.po_set_id = ps_id
                    s.commit()
            ids.append(doc_id)
        scenario_docs[name] = ids

    for name, sc in SCENARIOS.items():
        idx = sc.get("hostile_doc_index")
        if idx is not None:
            with Session(eng) as s:
                s.get(Document, scenario_docs[name][idx]).original_filename = HOSTILE_NAME
                s.commit()

    held_ids: dict[str, int] = {}
    for name, h in HELD.items():
        doc_id = make_doc(
            h["kind"],
            h["file"],
            f"{h['kind']}-{name}",
            h["po_ref"],
            h["lines"],
            ambiguous=bool(h.get("ambiguous")),
        )
        if h["state"] == "read":
            extract_document(doc_id, cfg)
        elif h["state"] == "failed":
            with Session(eng) as s:
                d = s.get(Document, doc_id)
                d.extraction_status = ExtractionStatus.failed
                d.extraction_attempt_count = cfg.extraction.max_retries
                s.commit()
        if h.get("hostile"):
            with Session(eng) as s:
                s.get(Document, doc_id).original_filename = HOSTILE_NAME
                s.commit()
        held_ids[name] = doc_id

    # A file ingest refuses for good (R-03) is listed on the Unclassified page.
    junk = Path(cfg.paths.input_folder) / "fake-not-a-pdf.pdf"
    junk.write_bytes(b"<html><body>404 not found</body></html>")
    with contextlib.suppress(ValueError):  # refused on purpose; leaves a failed row
        ingest_file(junk, cfg)

    resolve_unattached_documents(cfg)
    attach_unattached_to_open_sets(cfg)

    with Session(eng) as s:
        for sc in SCENARIOS.values():
            if sc.get("customs_toggle"):
                ps = s.query(POSet).filter(POSet.po_no_normalized == sc["po"]).one()
                ps.has_customs_toggle = True
        s.commit()

    # Supporting (CUSTOMS / SHIPPING) files attached the way the upload form does:
    # stored as-is, never read by the model.
    for sc in SCENARIOS.values():
        for dtype, filename in sc.get("supporting", []):
            width[0] += 3
            path = _pdf(Path(cfg.paths.input_folder) / filename, width[0])
            doc = ingest_file(path, cfg)
            with Session(eng) as s:
                d = s.get(Document, doc.id)
                ps = s.query(POSet).filter(POSet.po_no_normalized == sc["po"]).one()
                d.doc_type = DocType(dtype)
                d.extraction_status = ExtractionStatus.valid
                d.po_set_id = ps.id
                ps.customs_doc_count = (ps.customs_doc_count or 0) + 1
                s.commit()

    from app.services.reconciliation import SETTLE_MINUTES

    settle_by_po = {
        sc["po"]: sc["settle_minutes"] for sc in SCENARIOS.values() if sc.get("settle_minutes")
    }
    with Session(eng) as s:
        ids = [(r[0], r[1]) for r in s.query(POSet.id, POSet.po_no_normalized).order_by(POSet.id)]
    for ps_id, po_no in ids:
        minutes = settle_by_po.get(po_no)
        if minutes:
            # Only a poller-started sync sets this in production (D5).
            token = SETTLE_MINUTES.set(minutes)
            try:
                reconcile_po_set(ps_id, cfg)
            finally:
                SETTLE_MINUTES.reset(token)
        else:
            reconcile_po_set(ps_id, cfg)

    from app.services.quarantine import delete_quarantined

    for sc in SCENARIOS.values():
        if sc.get("archive_in_build"):
            with Session(eng) as s:
                ps = s.query(POSet).filter(POSet.po_no_normalized == sc["po"]).one()
                ps_id, status = ps.id, ps.status.value
            if status != "quarantined":
                raise SystemExit(f"{sc['po']} must be quarantined before archiving, got {status}")
            delete_quarantined(ps_id, cfg, justification=None, source="ui_test_env")

    # Operator history: a couple of audit rows so the Audit page has content.
    with Session(eng) as s:
        s.add(
            AuditLog(
                po_set_id=None,
                action=AuditAction.manual_status_change,
                detail=json.dumps({"note": "seeded sample row"}),
                justification="Seeded row for the UI tests (not a real action)",
                source="ui_test_env",
            )
        )
        s.commit()

    manifest: dict = {"root": str(root), "config": str(config_path), "scenarios": {}, "held": {}}
    with Session(eng) as s:
        for name, sc in SCENARIOS.items():
            ps = s.query(POSet).filter(POSet.po_no_normalized == sc["po"]).one()
            docs = s.query(Document).filter(Document.po_set_id == ps.id).order_by(Document.id).all()
            manifest["scenarios"][name] = {
                "id": ps.id,
                "po_no": sc["po"],
                "status": ps.status.value,
                "reason": ps.reconcile_reason,
                "mutable": bool(sc.get("mutable")),
                "archived": ps.archived_at is not None,
                "settle_minutes": sc.get("settle_minutes", 0),
                "line_mapping_rule": ps.line_mapping_rule,
                "merged_output_path": ps.merged_output_path,
                "docs": [
                    {
                        "id": d.id,
                        "type": d.doc_type.value,
                        "file": d.original_filename,
                        "lines": len(d.line_items),
                        "po_no_source": d.po_no_source,
                    }
                    for d in docs
                ],
            }
        for name, doc_id in held_ids.items():
            d = s.get(Document, doc_id)
            manifest["held"][name] = {
                "id": d.id,
                "file": d.original_filename,
                "type": d.doc_type.value,
                "extraction_status": d.extraction_status.value,
                "po_set_id": d.po_set_id,
                "mutable": bool(HELD[name].get("mutable")),
            }
        rejected = (
            s.query(Document).filter(Document.original_filename == "fake-not-a-pdf.pdf").first()
        )
        if rejected is not None:
            manifest["held"]["rejected_on_arrival"] = {
                "id": rejected.id,
                "file": rejected.original_filename,
                "type": rejected.doc_type.value,
                "extraction_status": rejected.extraction_status.value,
                "po_set_id": rejected.po_set_id,
                "mutable": False,
            }
    from collections import Counter

    manifest["counts"] = dict(Counter(sc["status"] for sc in manifest["scenarios"].values()))
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def free_port() -> int:
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    return port


def serve(root: Path, port: int | None = None) -> tuple[subprocess.Popen, str]:
    """Start uvicorn on the built environment (no provider key in its environment)."""
    import httpx

    port = port or free_port()
    env = provider_free_env()
    env["AAM_CONFIG_PATH"] = str(root / "config.yaml")
    env["PYTHONPATH"] = str(REPO / "src")
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=str(REPO),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    base = f"http://127.0.0.1:{port}"
    for _ in range(150):
        try:
            if httpx.get(base + "/health", timeout=0.5).status_code == 200:
                return proc, base
        except Exception:
            time.sleep(0.1)
    out = proc.communicate(timeout=5)[0]
    raise RuntimeError(f"server did not start:\n{out}")


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--root", default=str(REPO / "data" / "ui_test"))
    ap.add_argument("--serve", action="store_true", help="also start the server and wait")
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()
    root = Path(args.root).resolve()
    manifest = build(root)
    print(f"built {root}")
    print(f"statuses: {manifest['counts']}")
    print(f"manifest: {root / 'manifest.json'}")
    if args.serve:
        proc, base = serve(root, args.port)
        print(f"serving {base}  (Ctrl+C to stop)")
        try:
            proc.wait()
        except KeyboardInterrupt:
            proc.terminate()


if __name__ == "__main__":
    main()
