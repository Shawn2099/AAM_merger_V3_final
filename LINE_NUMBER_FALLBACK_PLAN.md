# Line-number fallback (x10 / N-1, either direction) - IMPLEMENTATION PLAN

Status: reviewed by Opus (2026-10-24, revision 2, after the user's decisions). Nothing is built yet. A Sonnet agent can implement this without asking questions.

It starts only after the `unit_price` removal is committed. That change touches the same files and owns migration `d5e6f7a8b9c0`.

Sources:
- HANDOFF_PLAN.md rules and `docs/CORE_LOGIC.md`
- the code as of this review
- read-only experiments over `data/aam_merger.db` and `data/prev_run_20261024/aam_merger.db` (section 11)

## 1. Goal and non-goals

Some customer ERPs number PO lines differently from the vendor's DN/SI. Four rules map a vendor key to a PO key:

| Rule id | PO keys | Vendor (DN/SI) keys | Vendor key -> PO key |
|---|---|---|---|
| `x10` | 10, 20, 30 (also SAP `00010`) | 1, 2, 3 | `str(int(k) * 10)` |
| `div10` | 1, 2, 3 | 10, 20, 30 (also `00010`) | `str(int(k) // 10)` |
| `add_rel` | 1-1, 2-1 (also `01-01`) | 1, 2 | `k + "-1"` |
| `strip_rel` | 1, 2 (or 10, 20) | 1-1, 2-1 (or `00010-0001`) | `"N-1" -> "N"` |

**Goal:** treat these as the same line, only as a fallback, and change nothing else.

**Non-goals.** These keep today's behaviour (quarantine):
- shifted or renumbered lines (DTS 22843: PO 1,3,4 vs DN 1,2,3; offset +1)
- step-5, step-20 and step-100 numbering
- any suffix other than `-1`
- dotted keys (`1.1`, `1.0`, `1.1.1`), slashed (`1/1`, `00010/1`), underscored (`1_1`) and Unicode-dash (`1–1`, `1−1`) keys
- labelled keys (`Line 10`, `#10`, `Item 10`) and trailing-dot or float keys (`10.`, `10.0`)
- any set with a numberless row
- description-based mapping and vendor recognition
- composing two rules (for example PO `1` against vendor `10-1`)

PO lines that DN/SI do not mention are deliveries still to come. They never block a mapping.

The rule is unchanged: PO == sum(DN) == sum(SI) per line, as exact integers x1000, and one failing line fails the set.

## 2. Invariants (each is enforced by construction and pinned by a test)

| # | Invariant | How |
|---|---|---|
| I1 | Exact matching always runs first and is untouched. | A pool whose keys are all PO keys is never mapped. `group_by_line_no`, `normalize_line_no`, `compare_aggregates` and `po_item_key` are NOT modified. |
| I2 | PO keys are never rewritten. PO is the anchor. | `po_lines` go to `group_by_line_no` unchanged. Only copies of the DN/SI rows get a new `line_item_no`. |
| I3 | In any one comparison (PO vs DN, PO vs SI) only one side is transformed: the vendor side. | Each pool is resolved against the untouched PO keys. |
| I4 | **The pools are independent (user decision, 2026-10-24).** | DN and SI each resolve on their own. One may be exact while the other is mapped, and they may use different rules. |
| I5 | One rule per pool, all or nothing. | Every numbered key of every document in the pool must map to an existing PO key under the same rule. Otherwise the pool is not mapped. |
| I6 | A mixed pool is never mapped. | If a rule is chosen for a pool, no single document in that pool may already fit the PO keys as printed. A pool where DN-A is exact and DN-B needs x10 is not mapped, and today's quarantine follows. |
| I7 | Off by default at the function level. | `compare_po_set_lines(..., line_fallback=None)` is exact only. Only the two production callers pass the config. |

## 3. Verified code facts (2026-10-24)

| Item | Fact | Consequence |
|---|---|---|
| `matching.normalize_line_no` | `re.sub(r"(^\|[-_/])0+(\d+)", r"\1\2", s.strip())`, so `01`→`1`, `00010`→`10`, `01-01`→`1-1`, `00010-0001`→`10-1`, `00010/1`→`10/1`; `1-1`, `1.05`, `10.0`, `#10` and `1–1` stay as they are; `0` stays `0`. It is idempotent. | The rule regexes run on normalised keys. Zero padding and outer spaces are already handled (section 11.2). |
| `matching.group_by_line_no` | Returns `(po_totals, vendor_totals, orphans, failure_reason)`. A numbered vendor key not in the PO is an orphan unless the PO has numberless lines. Extra dict keys are ignored. | Numberless sets are excluded, so "unmatched" means a numbered vendor key that is not in the PO key set. |
| `reconciliation.compare_po_set_lines` | Calls `group_by_line_no` twice (DN, then SI) with the same `po_lines`. | The integration point is: resolve → remap the vendor copies → run the unchanged body. |
| Callers | `_reconcile_po_set_inner` (~line 702) and `dashboard.po_set_detail_view` (~line 427). Tests call it directly with no config. | Both production callers must pass the same `line_fallback`. |
| Row dicts | The engine (~lines 596-625) and the dashboard (`enriched`, ~lines 405-413) carry no `document_id`. | Add `document_id` to both. A missing key counts as one document. |
| Several DNs/SIs | A pool is the flat concatenation of all its documents' lines. | There is one rule per pool, and I6 checks each document. |
| Engine early return | A set that is already `quarantined` is not re-reconciled. | Today's quarantined x10/N-1 sets change only after Redo matching (`POST /po_sets/{id}/redo_match`) or a bulk re-run (section 15). |
| `AuditLog.action` | Non-native `Enum`, no CHECK constraint. The live DB stores it as `VARCHAR(20)`, which SQLite does not enforce. | `line_mapping_applied` (20 characters) needs no migration. |
| `d5e6f7a8b9c0_drop_line_item_unit_price.py` | Exists, with `down_revision="a9b0c1d2e3f4"`. `migrate_db.HEAD_REVISION = "d5e6f7a8b9c0"`. The live DB is at `a9b0c1d2e3f4`. | The new migration `e8f9a0b1c2d3` has `down_revision="d5e6f7a8b9c0"`. |
| `quarantine._write_quarantine_report` | Overlays `vendor_quantity` for any flag that has a `pool` (~lines 125-134). | The new flag types must be skipped there. |
| `_persist_reason` | Returns early for an already-merged set unless it carries naming or description flags (~lines 271-278). | Add `and not mapping`. |

## 4. Algorithm - `src/app/services/matching.py` (appended; only the module docstring changes elsewhere)

```python
from dataclasses import dataclass, field

LINE_MAPPING_RULES: tuple[str, ...] = ("x10", "div10", "add_rel", "strip_rel")

RULE_TEXT: dict[str, str] = {
    "x10": "PO lines in steps of 10, DN/SI lines in steps of 1",
    "div10": "DN/SI lines in steps of 10, PO lines in steps of 1",
    "add_rel": "PO lines carry a -1 suffix, DN/SI lines do not",
    "strip_rel": "DN/SI lines carry a -1 suffix, PO lines do not",
}

_PLAIN = re.compile(r"^[1-9]\d*$")        # 1, 2, 10 ... (never "0")
_REL1 = re.compile(r"^([1-9]\d*)-1$")    # 1-1, 12-1 ... (suffix -1 only, ASCII hyphen)


@dataclass(frozen=True)
class PoolMapping:
    rule: str | None = None                             # one of LINE_MAPPING_RULES, None = as printed
    map: dict[str, str] = field(default_factory=dict)   # normalised printed vendor key -> PO key


@dataclass(frozen=True)
class LineMapping:
    dn: PoolMapping = field(default_factory=PoolMapping)
    si: PoolMapping = field(default_factory=PoolMapping)
    rejections: tuple[tuple[str, str, str], ...] = ()   # (pool, reason_code, operator message)

    @property
    def label(self) -> str | None:
        """Short tag for the set: None, a rule id, or 'DN <rule|as printed>, SI <rule|as printed>'."""
        if self.dn.rule is None and self.si.rule is None:
            return None
        if self.dn.rule == self.si.rule:
            return self.dn.rule
        return f"DN {self.dn.rule or 'as printed'}, SI {self.si.rule or 'as printed'}"


def _eligible_rules(po_keys: set[str], vendor_keys: set[str]) -> list[str]:
    """Structural preconditions. For non-empty key sets at most one rule is eligible (pinned)."""
    po_plain = all(_PLAIN.match(k) for k in po_keys)
    v_plain = all(_PLAIN.match(k) for k in vendor_keys)
    out: list[str] = []
    if po_plain and v_plain and all(int(k) % 10 == 0 for k in po_keys):
        out.append("x10")
    if (po_plain and v_plain and all(int(k) % 10 == 0 for k in vendor_keys)
            and not all(int(k) % 10 == 0 for k in po_keys)):
        out.append("div10")
    if v_plain and all(_REL1.match(k) for k in po_keys):
        out.append("add_rel")
    if po_plain and all(_REL1.match(k) for k in vendor_keys):
        out.append("strip_rel")
    return out


def _apply_rule(rule: str, key: str) -> str:
    if rule == "x10":
        return str(int(key) * 10)
    if rule == "div10":
        return str(int(key) // 10)
    if rule == "add_rel":
        return f"{key}-1"
    m = _REL1.match(key)          # strip_rel; eligibility guarantees a match
    assert m is not None
    return m.group(1)


def _natural(key: str) -> tuple[int, str]:
    return (len(key), key)


def _resolve_pool(po_keys: set[str], rows: list[dict], rules: frozenset[str]
                  ) -> tuple[str, str | None, dict[str, str]]:
    """(kind, rule, map); kind in {"empty", "identity", "mapped", "none", "rejected"}."""
    vendor_keys = {normalize_line_no(r.get("line_item_no")) for r in rows}
    if not vendor_keys:
        return ("empty", None, {})
    if vendor_keys <= po_keys:
        return ("identity", None, {})                                           # I1
    fits = [r for r in _eligible_rules(po_keys, vendor_keys)
            if r in rules and all(_apply_rule(r, k) in po_keys for k in vendor_keys)]   # I5
    assert len(fits) <= 1
    if not fits:
        return ("none", None, {})
    rule = fits[0]
    by_doc: dict[int | None, set[str]] = {}
    for r in rows:
        by_doc.setdefault(r.get("document_id"), set()).add(normalize_line_no(r.get("line_item_no")))
    if any(keys <= po_keys for keys in by_doc.values()):                       # I6
        return ("rejected", rule, {})
    return ("mapped", rule, {k: _apply_rule(rule, k) for k in sorted(vendor_keys, key=_natural)})


def resolve_line_mapping(po_lines: list[dict], dn_lines: list[dict], si_lines: list[dict],
                         rules: frozenset[str]) -> LineMapping:
    """Per pool, decide whether the vendor keys need a line-number rule. Never touches PO keys."""
    if not rules:
        return LineMapping()
    if any(normalize_line_no(r.get("line_item_no")) == "" for r in (*po_lines, *dn_lines, *si_lines)):
        return LineMapping()                                     # numberless row anywhere: exact only
    po_keys = {normalize_line_no(r.get("line_item_no")) for r in po_lines}
    if not po_keys:
        return LineMapping()                                     # nothing to anchor on (dashboard preview)
    pools: dict[str, PoolMapping] = {}
    rejections: list[tuple[str, str, str]] = []
    for pool, rows in (("DN", dn_lines), ("SI", si_lines)):
        kind, rule, mapping = _resolve_pool(po_keys, rows, rules)
        pools[pool] = PoolMapping(rule, mapping) if kind == "mapped" else PoolMapping()
        if kind == "rejected":
            assert rule is not None
            rejections.append((pool, "document_fits_exact", (
                f"{pool} line numbers fit rule {rule} ({RULE_TEXT[rule]}), but a {pool} document "
                "already matches the PO line numbers as printed; line numbers were not mapped")))
    return LineMapping(dn=pools["DN"], si=pools["SI"], rejections=tuple(rejections))
```

Notes:
- `ty check src` must stay clean. Keep the type hints as written.
- Every rule is one-to-one.
- A pool that resolves to `none` or `rejected` stays as printed, so its orphans quarantine the set as they do today. The other pool may still be mapped. That only affects what the page shows, because the verdict is a quarantine either way.
- There is no "rules differ" hold any more (I4).

## 5. Integration - exact changes per file

| File / function | Change |
|---|---|
| `src/app/core/config.py` | **Add** `class LineNumberFallbackConfig(_StrictConfigModel)` with four fields: `step10: bool = True`, `step10_reverse: bool = True`, `release_suffix: bool = True`, `release_suffix_reverse: bool = True`. **Add** a method `rules(self) -> frozenset[str]` that maps each field to its rule: `step10`→`"x10"`, `step10_reverse`→`"div10"`, `release_suffix`→`"add_rel"`, `release_suffix_reverse`→`"strip_rel"`. **In `MatchingConfig`**, add `line_number_fallback: LineNumberFallbackConfig = Field(default_factory=LineNumberFallbackConfig)` and fix the "only matching knob" comment. |
| `config.example.yaml` | Add the block in section 7 under `matching:`. |
| `src/app/services/matching.py` | Add the section 4 code. In the module docstring, replace "and ERP step-numbering are deliberately not matching inputs" with: step-10 and `-1` numbering are mapped per pool, only when exact matching leaves vendor keys unmatched (`resolve_line_mapping`). |
| `reconciliation.compare_po_set_lines` | **Signature:** `compare_po_set_lines(po_lines, dn_lines, si_lines, desc_threshold: int = 85, *, line_fallback: LineNumberFallbackConfig \| None = None) -> dict`. **Steps:** (1) `rules = line_fallback.rules() if line_fallback is not None else frozenset()`. (2) `lm = resolve_line_mapping(po_lines, dn_lines, si_lines, rules)`. (3) If `lm.dn.rule`: `dn_lines = [{**r, "line_item_no": lm.dn.map[normalize_line_no(r.get("line_item_no"))]} for r in dn_lines]`. Do the same for SI with `lm.si`. (4) Run the existing body unchanged. (5) Before `flags.sort`, append the section 6.1 flags. (6) Add `"line_mapping": {"label": lm.label, "DN": {"rule": lm.dn.rule, "map": dict(lm.dn.map)}, "SI": {"rule": lm.si.rule, "map": dict(lm.si.map)}}` to the return value. Update the docstring. |
| `reconciliation._reconcile_po_set_inner` | (a) Add `"document_id": d.id` to every dict in `po_lines`, `dn_lines` and `si_lines`. (b) `lf = getattr(cfg.matching, "line_number_fallback", None)`, and use `lf = None` unless it is a `LineNumberFallbackConfig`. Call `compare_po_set_lines(po_lines, dn_lines, si_lines, thr, line_fallback=lf)`. (c) Right after `flags = comparison["flags"]`: `label = comparison["line_mapping"]["label"]`, then `if ps.line_mapping_rule != label: ps.line_mapping_rule = label`. Every later branch already commits. (d) In the post-merge block, when `merged_path is not None and label`, add the section 6.4 audit row and call `s.commit()`. Import `json`, plus `AuditAction` and `AuditLog` from `app.models`. |
| `reconciliation._persist_reason` | **Collect:** `mapping = [f for f in flags if f.get("type") == "line_mapping"]` and `rejected = [f for f in flags if f.get("type") == "line_mapping_rejected"]`. **Sentence:** `sentence = mapping_sentence(mapping[0]["label"]) if mapping else ""`, where `mapping_sentence(label) = f"Line numbers mapped by rule {label}." if label in LINE_MAPPING_RULES else f"Line numbers mapped ({label})."`. **Suffix:** `suffix = f" {rejected[0]['message']}." if rejected else (f" {sentence}" if sentence else "")`. **Merged branch:** if `sentence`, append it to `extras`. **reason_code and has_quantity_evidence branches:** `if suffix: note = note.rstrip(".") + "." + suffix`. **Early-return guard:** add `and not mapping`. |
| `api/routes/dashboard.py::po_set_detail_view` | (a) Add `"document_id": li.document_id` to the `enriched` dicts. (b) Pass `line_fallback=cfg.matching.line_number_fallback`. (c) Add flag-loop branches, shown for every status: `line_mapping` becomes `{"priority": 2, "badge": "badge-desc", "type": "Line Numbers Mapped", "message": f["message"]}`; `line_mapping_rejected` becomes `{"priority": 1, "badge": "badge-quarantined", "type": "Line Numbers Not Mapped", "message": f["message"]}`. |
| `dashboard.build_matrix_rows` | Build `mapped_from: dict[str, list[str]]`: for each `pool` in `("DN", "SI")` and each `vkey, pkey` in `(comparison.get("line_mapping") or {}).get(pool, {}).get("map", {}).items()`, do `mapped_from.setdefault(pkey, []).append(f"{pool} {vkey}")`. Then set row `"mapped_from": " · ".join(mapped_from.get(key, []))`. |
| `dashboard.py` table rows (~line 145) | Add `"line_mapping_rule": ps.line_mapping_rule`. |
| `templates/po_set_detail.html` | **Header:** change the description `elif` to `{% elif (po_set.description_match_count or 0) > 0 or po_set.line_mapping_rule %}`. Keep "Merged • review". Wrap the "description match ×N" badge in `{% if (po_set.description_match_count or 0) > 0 %}`. Add `{% if po_set.line_mapping_rule %}<span class="badge badge-desc" style="font-size:0.8rem;" title="Line numbers were paired by a numbering rule, not as printed.">lines mapped ({{ po_set.line_mapping_rule }})</span>{% endif %}`. **Matrix:** under `#{{ r.line_item_no }}` add `{% if r.mapped_from %}<span class="badge badge-desc" style="display:block; margin-top:4px; font-size:0.7rem;" title="Paired by a line-number rule, not as printed.">&larr; {{ r.mapped_from }}</span>{% endif %}`. |
| `templates/_dashboard_table.html` | Same header change with `ps.`; the badge is `lines mapped ({{ ps.line_mapping_rule }})` at `font-size:0.72rem; margin-left:4px;`. |
| `quarantine._write_quarantine_report` | Declare `notes: list[str] = []` before the flag loop. First statement inside the loop: `if f.get("type") in ("line_mapping", "line_mapping_rejected"): notes.append(str(f.get("message") or "")); continue`. After the table (or the else text), if `notes`, append `""`, `"Line numbers"`, `"-" * 60` and each note. |
| `quarantine.py` (~line 497, manifest dict) | Add `"line_mapping_rule": ps.line_mapping_rule`. |
| `models/models.py` | Add `AuditAction.line_mapping_applied = "line_mapping_applied"`. Add `POSet.line_mapping_rule: Mapped[str \| None] = mapped_column(Text, nullable=True)` with the comment: the `LineMapping.label` the last reconcile used; advisory; never decides a verdict. |

Do NOT touch any of these:
- `normalize_line_no`, `group_by_line_no`, `compare_aggregates`, `po_item_key`
- `tests/test_erp_line_formats.py`
- the verdict filter `failure_flags = [... ("identification", "quantity")]`

## 6. Operator-visible output (exact)

### 6.1 Flags appended by `compare_po_set_lines`

| When | Flag dict |
|---|---|
| `lm.dn.rule` is set | `{"priority": 2, "type": "line_mapping", "pool": "DN", "rule": lm.dn.rule, "label": lm.label, "map": dict(lm.dn.map), "reason": "line_number_mapped", "message": f"DN line numbers mapped to the PO by rule {rule} ({RULE_TEXT[rule]}): " + ", ".join(f"{v} -> {p}" for v, p in lm.dn.map.items())}` |
| `lm.si.rule` is set | The same dict with `"pool": "SI"` and `lm.si`. |
| Each `(pool, code, msg)` in `lm.rejections` | `{"priority": 1, "type": "line_mapping_rejected", "pool": pool, "reason": code, "message": msg}` |

Neither type carries `line_item_no` or `vendor_quantity`, and neither is in the verdict filter.

### 6.2 Persisted reason

| Verdict | Text |
|---|---|
| merged, both pools x10 | `Fully reconciled — packet merged. Line numbers mapped by rule x10.` |
| merged, DN exact and SI x10 | `Fully reconciled — packet merged. Line numbers mapped (DN as printed, SI x10).` |
| pending partial | `<explain("partial_fulfillment", flags)>. Line numbers mapped by rule x10.` |
| mismatched | `<explain(None, flags)>. Line numbers mapped by rule x10.` |
| quarantined because a pool was rejected (I6) | `A delivery or invoice line could not be matched to any PO line. DN line numbers fit rule x10 (PO lines in steps of 10, DN/SI lines in steps of 1), but a DN document already matches the PO line numbers as printed; line numbers were not mapped.` |
| quarantined because the other pool resolved to none (set 15) | `A delivery or invoice line could not be matched to any PO line. Line numbers mapped (DN as printed, SI x10).` |

### 6.3 `po_sets.line_mapping_rule`

It holds `LineMapping.label` (for example `x10`, or `DN as printed, SI x10`), or NULL. It is written on every pass that reaches the comparison.

### 6.4 Audit

```python
AuditLog(po_set_id=ps.id,
         action=AuditAction.line_mapping_applied,
         detail=json.dumps({"po_no": ps.po_no_normalized,
                            "DN": comparison["line_mapping"]["DN"],
                            "SI": comparison["line_mapping"]["SI"]},
                           sort_keys=True),
         source="system")
```

It is written once, when the set merges with a non-null label.

## 7. Config (`config.example.yaml`, under `matching:`)

```yaml
  # Line-number fallback, used ONLY when exact matching leaves DN or SI line
  # numbers unmatched. Per pool (DN and SI independently), all-or-nothing, one
  # rule per pool; PO numbers are never rewritten.
  line_number_fallback:
    step10: true                  # PO 10,20,30 vs DN/SI 1,2,3       (rule x10)
    step10_reverse: true          # PO 1,2,3 vs DN/SI 10,20,30       (rule div10)
    release_suffix: true          # PO 1-1,2-1 vs DN/SI 1,2          (rule add_rel)
    release_suffix_reverse: true  # PO 1,2 vs DN/SI 1-1,2-1          (rule strip_rel)
```

Unknown keys fail fast. With all four set to `false`, behaviour is identical to today.

## 8. Migration

New file `alembic/versions/e8f9a0b1c2d3_add_line_mapping_rule.py`:

```python
"""add po_sets.line_mapping_rule

Advisory: the line-number fallback label (x10/div10/add_rel/strip_rel, or
"DN <rule|as printed>, SI <rule|as printed>") the last reconcile used, NULL
when lines were compared as printed. Read by the UI; never decides a verdict.

Revision ID: e8f9a0b1c2d3
Revises: d5e6f7a8b9c0
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "e8f9a0b1c2d3"
down_revision: str | None = "d5e6f7a8b9c0"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    cols = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("po_sets")}
    if "line_mapping_rule" in cols:
        return  # create_all DB built from current models already has it
    op.add_column("po_sets", sa.Column("line_mapping_rule", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("po_sets") as batch_op:
        batch_op.drop_column("line_mapping_rule")
```

Changes to `scripts/migrate_db.py`:
- Append `("po_sets.line_mapping_rule", "e8f9a0b1c2d3"),` as the last `_MARKERS` entry.
- Set `HEAD_REVISION = "e8f9a0b1c2d3"`.
- Add the docstring line `po_sets.line_mapping_rule         -> e8f9a0b1c2d3`.
- Add `"line_mapping_rule"` to `verify()`'s `required_ps`.

First confirm that `d5e6f7a8b9c0_drop_line_item_unit_price.py` is committed with that id. If the id changed, use the new id. `tests/test_migrate_db.py` refers to `HEAD_REVISION` symbolically. Run migrations only on temp copies.

## 9. Tests

`A = LineNumberFallbackConfig()` (all on) and `R = A.rules()`. `L(*keys, qty=1000, doc=None)` builds rows `{"line_item_no": k, "description": "ITEM", "quantity": qty, "document_id": doc}`. Quantities are scaled x1000.

### 9.1 `tests/test_line_mapping.py` (pure resolver and compare)

| Test | PO / DN / SI keys | Expected (`lm = resolve_line_mapping(..., R)`) |
|---|---|---|
| `test_exact_match_needs_no_mapping` | 10,20 / 10,20 / 10,20 | `lm == LineMapping()` |
| `test_x10_maps_vendor_steps_of_one` | 10,20,30 / 1,2,3 / 1,2,3 | `lm.dn == lm.si == PoolMapping("x10", {"1":"10","2":"20","3":"30"})`; `lm.label == "x10"` |
| `test_div10_maps_vendor_steps_of_ten` | 1,2 / 10,20 / 10,20 | both `PoolMapping("div10", {"10":"1","20":"2"})` |
| `test_add_rel_maps_po_release_suffix` | 1-1,2-1 / 1,2 / 1,2 | both `PoolMapping("add_rel", {"1":"1-1","2":"2-1"})` |
| `test_strip_rel_maps_vendor_release_suffix` | 1,2 / 01-01,2-1 / 1-1,2-1 | `lm.dn == PoolMapping("strip_rel", {"1-1":"1","2-1":"2"})` |
| `test_partial_vendor_pool_still_maps` | 10,20,30 / 2,3 / 2,3 | x10, map `{"2":"20","3":"30"}` |
| `test_x10_with_ten_or_more_po_lines_maps_all_or_nothing` | 10..120 step 10 / 1..12 / 1..12 | x10; `lm.dn.map["10"] == "100"` |
| `test_dn_exact_si_mapped_is_allowed` | 10,20 / 10,20 / 1,2 | `lm.dn == PoolMapping()`; `lm.si.rule == "x10"`; `lm.label == "DN as printed, SI x10"`; `lm.rejections == ()` |
| `test_dn_and_si_may_use_different_rules` | 1,2 / 10,20 / 1-1,2-1 | `lm.dn.rule == "div10"`; `lm.si.rule == "strip_rel"`; `lm.label == "DN div10, SI strip_rel"` |
| `test_set15_shape_maps_si_but_not_dn` | real set 15 (section 9.3) | `lm.dn == PoolMapping()` (DN 11 and 19 have no target); `lm.si.rule == "x10"`; no rejections |
| `test_mixed_pool_is_not_mapped` | PO 10,20,30,100; DN `L("10", doc=1) + L("2","3", doc=2)`; SI 1,2,3,10 | `lm.dn == PoolMapping()`; `lm.rejections == (("DN","document_fits_exact","DN line numbers fit rule x10 (PO lines in steps of 10, DN/SI lines in steps of 1), but a DN document already matches the PO line numbers as printed; line numbers were not mapped"),)`; `lm.si.rule == "x10"` |
| `test_numberless_row_disables_mapping` | 10,20 / [1, None] / 1,2 | `LineMapping()` |
| `test_zero_key_never_maps` | 10,20 / 0,1 / 1,2 | `lm.dn == PoolMapping()`; `lm.si.rule == "x10"` |
| `test_no_po_keys_no_mapping` | (none) / 1 / 1 | `LineMapping()` |
| `test_rules_off_equals_exact` | the x10 case with `frozenset()` | `LineMapping()` |
| `test_single_rule_off_disables_only_that_rule` | the x10 case with `R - {"x10"}` | `LineMapping()`; the add_rel case still maps |
| `test_preconditions_are_mutually_exclusive` | parametrize PO in `[{"10","20"},{"1","2"},{"1","2","10"},{"1-1","2-1"},{"10"},{"1"}]` x vendor in `[{"1","2"},{"10","20"},{"1-1","2-1"},{"10-1"},{"1"}]` | `len(_eligible_rules(po, v)) <= 1` |
| `test_compare_x10_merges_without_failure_flags` | `compare_po_set_lines(PO 10:5000,20:3000; DN/SI 1:5000,2:3000, line_fallback=A)` | flag types `["line_mapping","line_mapping"]`; `line_mapping == {"label":"x10","DN":{"rule":"x10","map":{"1":"10","2":"20"}},"SI":{"rule":"x10","map":{"1":"10","2":"20"}}}`; DN message `"DN line numbers mapped to the PO by rule x10 (PO lines in steps of 10, DN/SI lines in steps of 1): 1 -> 10, 2 -> 20"` |
| `test_compare_po_keys_are_never_rewritten` | the same input, plus the dn-exact/si-mapped input | `set(po_totals) == set(dn_totals) == set(si_totals)` and equals the normalised PO keys; `po_lines` equals a `copy.deepcopy` taken before the call |
| `test_compare_mapped_quantity_mismatch_reports_po_line` | PO 10:32000; DN/SI 1:19000 | one quantity flag per pool: `line_item_no == "10"`, `po_quantity == 32000`, `vendor_quantity == 19000` |
| `test_compare_without_config_is_exact` | the x10 input, no `line_fallback` | flag types `["identification","identification"]`; label None |
| `test_compare_rejection_flag_is_advisory` | the mixed-pool input with `A` | exactly one `line_mapping_rejected` flag (priority 1, `pool == "DN"`), plus DN `identification` flags; SI has no failure flags |

### 9.2 `tests/test_line_mapping_variations.py` (new; table-driven ERP variation families)

A case is `(id, po_style, dn_style, si_style, delivery, expected)`. Bases come from section 9.3:
- `B3` is set 40: true lines 1..3, qty 4,4,6.
- `B1` is set 39: one line, qty 1.

Styles render a true line number `i`; delivery is either `full` or `partial` (drop true line 1). A helper `render(style, i)` must use exactly these formats:

| Style | `i=1` | Style | `i=1` | Style | `i=1` |
|---|---|---|---|---|---|
| `plain` | `1` | `pad2` | `01` | `spaces` | ` 1 ` |
| `step10` | `10` | `sap3` / `sap4` / `sap5` / `sap6` | `010` / `0010` / `00010` / `000010` | `spaces10` | `10 ` |
| `rel` | `1-1` | `rel_pad` | `01-01` | `sap5_sched` | `00010-0001` |
| `sap_sched_dash` | `10-1` | `sap_sched_slash` | `00010/1` | `rel_us` / `rel_slash` | `1_1` / `1/1` |
| `rel_endash` | `1–1` (U+2013) | `rel_minus` | `1−1` (U+2212) | `dot1` / `dot0` / `dot11` | `1.1` / `1.0` / `1.1.1` |
| `line_lbl` / `hash_lbl` / `item_lbl` | `Line 1` / `#1` / `Item 1` | `trail_dot` / `float10` | `1.` / `10.0` | `step5` / `step20` / `step100` / `offset1` | `5` / `20` / `100` / `2` |

**Expected outcome per case.** "merged", "pending" and "quarantined" are the verdicts of `compare_po_set_lines` + `group_by_line_no` with `A`, using the engine's verdict rules. Assert the DN and SI rules and the verdict. `wrong=0` means every vendor row lands on its true PO line.

| Family / id | PO | DN | SI | Delivery | Expected (DN rule, SI rule, verdict) | Pin |
|---|---|---|---|---|---|---|
| `exact_plain` | plain | plain | plain | full | None, None, merged | |
| `exact_zero_pad_po` | pad2 | plain | plain | full | None, None, merged (normalised) | |
| `exact_sap_both` | sap5 | step10 | sap4 | full | None, None, merged | |
| `exact_spaces` | spaces | plain | plain | full | None, None, merged | |
| `sap_po_vendor_serial` (x5: sap3..sap6, step10) | sapN | plain | plain | full | x10, x10, merged | |
| `sap_po_vendor_serial_partial` | sap5 | plain | plain | partial | x10, x10, pending | |
| `vendor_sap_po_serial` (reverse) | plain | sap5 | sap5 | full | div10, div10, merged | |
| `vendor_step10_spaces` (reverse) | plain | spaces10 | spaces10 | full | div10, div10, merged | |
| `po_release_suffix` | rel | plain | plain | full | add_rel, add_rel, merged | |
| `po_release_suffix_padded` | rel_pad | plain | plain | full | add_rel, add_rel, merged | |
| `vendor_release_suffix` (reverse) | plain | rel | rel | full | strip_rel, strip_rel, merged | |
| `sap_schedule_vs_sap_po` | step10 | sap5_sched | sap5_sched | full | strip_rel, strip_rel, merged | |
| `dn_exact_si_step10` (Q1) | plain | plain | step10 | full | None, div10, merged | |
| `dn_step10_si_suffix` (Q1) | plain | step10 | rel | full | div10, strip_rel, merged | |
| `held_sap_schedule_vs_plain_po` | plain | sap_sched_dash | sap_sched_dash | full | None, None, quarantined | `test_limitation_two_rules_never_compose` |
| `held_sap_schedule_slash` | step10 | sap_sched_slash | sap_sched_slash | full | None, None, quarantined | `test_limitation_slash_schedule_is_not_mapped` |
| `held_unicode_dash_po` (x2) | rel_endash / rel_minus | plain | plain | full | None, None, quarantined | `test_limitation_unicode_dash_is_not_normalised` |
| `held_other_separators` (x5) | rel_us / rel_slash / dot1 / dot0 / dot11 | plain | plain | full | None, None, quarantined | `test_limitation_other_separators_are_not_mapped` |
| `held_labels` (x5) | line_lbl / hash_lbl / item_lbl / trail_dot / float10 | plain | plain | full | None, None, quarantined | `test_limitation_labelled_keys_are_not_mapped` |
| `held_other_steps` (x3) | step5 / step20 / step100 | plain | plain | full | None, None, quarantined | `test_limitation_steps_other_than_10_are_not_mapped` |
| `held_offset_vendor` | plain | offset1 | offset1 | full | None, None, quarantined | `test_limitation_shifted_numbering_is_not_mapped` |
| `held_mixed_docs` (explicit keys, not a base) | 10,20,30,100 (qty 1) | doc A `10`, doc B `2`,`3` (qty 1) | 1,2,3,10 (qty 1) | full | DN None (rejected `document_fits_exact`), SI x10, quarantined | `test_limitation_mixed_pool_is_not_mapped` |
| `held_mixed_docs_plain_step10` (B3) | plain | doc A plain (true line 1), doc B step10 (true lines 2,3) | plain | full | None, None, quarantined | `test_limitation_mixed_pool_is_not_mapped` |
| `vendor_serial_renumbered_partial` | step10 | plain renumbered 1..m for true lines 2,3 | same | partial | x10, x10, mismatched (wrong pairing, never merged) | `test_known_limitation_vendor_serial_renumbering_pairs_wrongly_but_never_merges` |
| `single_line_step10` | step10 on B1 | plain, description differs | plain | full | x10, x10, merged | `test_known_limitation_single_line_step10_maps_on_quantity_alone` |
| `single_line_reverse` (x2) | plain on B1 | step10 / rel | same | full | div10 / strip_rel, merged | `test_known_limitation_reverse_rules_map_on_structure_alone` |

Implementation: one `@pytest.mark.parametrize` over the table, with ids from the first column, plus a separate test function named in the "Pin" column for each pinned row. A pinned row calls the same helper with that row's arguments.

### 9.3 `tests/test_line_mapping_real_shapes.py` (sanitised real sets; keys and quantities only, no names)

| Test | Source | PO | DN | SI | Expected |
|---|---|---|---|---|---|
| `test_real_set39_single_line_step10_merges` | cur S39 | 10:1 | 1:1 | 1:1 | x10/x10, merged |
| `test_real_set40_three_lines_step10_merges` | cur S40 | 10:4,20:4,30:6 | 1:4,2:4,3:6 | same | x10/x10, merged |
| `test_real_set42_step10_merges` | cur S42 | 10:12 | 1:12 | 1:12 | x10/x10, merged |
| `test_real_set41_partial_step10_is_pending` | cur S41 | 10:3,20:3,30:3 | 2:3,3:3 | same | x10/x10, pending |
| `test_real_set43_step10_quantity_disagreement_is_mismatched` | cur S43 | 10:32 | 1:19 | 1:19 | x10/x10, mismatched, flag line `10` |
| `test_real_set48_release_suffix_merges` | cur S48 | 1-1:20 | 1:20 | 1:20 | add_rel/add_rel, merged |
| `test_real_set50_release_suffix_merges` | cur S50 | 1-1:1 | 1:1 | 1:1 | add_rel/add_rel, merged |
| `test_real_set47_release_suffix_quantity_disagreement_is_mismatched` | cur S47 | 1-1:50 | 1:46 | 1:46 | add_rel/add_rel, mismatched |
| `test_real_ars_set8_exact_step10_on_all_three_is_unchanged` | cur S8 | 10:50,20:12 | 10:50,20:12 | same | None/None, merged |
| `test_real_set15_si_maps_dn_does_not_and_set_quarantines` | cur S15 | 10:1,20:2,30:2,40:1,50:1,60:1,70:2,80:1,90:1,100:1,120:1,130:1,140:1,150:2,160:1,170:1,180:1 | 1:1,2:2,3:2,4:1,5:1,6:1,7:2,8:1,9:1,10:1,11:1,12:1,13:1,14:1,15:2,16:1,17:1,18:1,19:1 | doc A 1:1,2:2,3:2,4:1,5:1,6:1,7:2,8:1,9:1,10:1,12:1; doc B 13:1,14:1,15:2,16:1,17:1,18:1 | DN None, SI x10, quarantined (DN identification flags for 11 and 19) |
| `test_limitation_real_dts22843_shifted_numbering_is_not_mapped` | cur S20 | 1:2,3:6,4:2 | 1:2,2:6,3:2 | same | None/None, quarantined |

### 9.4 `tests/test_business_scenarios_ui.py` (engine and pages; existing helpers)

| Test | Data | Expected |
|---|---|---|
| `test_step10_po_merges_by_line_mapping_and_pages_say_so` | `PO-MAP10`: PO [("10","BOLT",5),("20","NUT",3)], DN [("1","BOLT",5),("2","NUT",3)], SI the same with `si_no="INV-MAP10"` | **Verdict:** `merged`. **Stored on the set:** `line_mapping_rule == "x10"`; the reason contains `Line numbers mapped by rule x10.`. **Audit:** exactly one `line_mapping_applied` row, `json.loads(detail) == {"DN":{"map":{"1":"10","2":"20"},"rule":"x10"},"SI":{"map":{"1":"10","2":"20"},"rule":"x10"},"po_no":"PO-MAP10"}`. **Set page:** contains `Line Numbers Mapped`, `rule x10 (PO lines in steps of 10` and `DN 1 · SI 1`. **`/dashboard/table`:** contains `lines mapped (x10)`. |
| `test_dn_exact_si_step10_merges_with_label` | PO 10,20 (qty 1); DN 10,20; SI 1,2 | `merged`; `line_mapping_rule == "DN as printed, SI x10"`; the reason contains `Line numbers mapped (DN as printed, SI x10).` |
| `test_release_suffix_po_merges_by_line_mapping` | PO [("1-1","GREASE",2)], DN and SI [("1","GREASE",2)] | `merged`; `add_rel` |
| `test_partial_step10_delivery_is_pending_with_mapping` | PO 10/20/30 qty 3; DN and SI 2,3 qty 3 | `pending`/`partial_fulfillment`; the reason contains `Line numbers mapped by rule x10.`; the set is not on `/quarantine` |
| `test_step10_quantity_mismatch_is_mismatched_on_the_po_line` | PO [("10",..,32)], DN and SI [("1",..,19)] | `mismatched`; the page contains `Line #10: delivered 19 of 32` |
| `test_mixed_numbering_in_one_pool_quarantines_and_both_pages_say_why` | PO 10,20,30,100 qty 1; DN-A [("10",..,1)]; DN-B [("2",..,1),("3",..,1)]; SI 1,2,3,10 qty 1 | `quarantined`/`unmatched_vendor_line`; `already matches the PO line numbers as printed` appears in the reason and on `/quarantine`; the page contains `Line Numbers Not Mapped` |
| `test_line_number_fallback_switched_off_quarantines_as_before` | the first test's data with all four switches `False` | `quarantined`; `line_mapping_rule is None`; no audit row |

### 9.5 Existing tests: flip or keep (any other failure is a bug to report, not to "fix")

| Test | Change |
|---|---|
| `test_reconciliation.py::test_step_10_numbering_no_longer_maps_and_quarantines` | Rename it to `test_step_10_numbering_maps_by_fallback_and_merges`. Expect `merged`, `line_mapping_rule == "x10"`. Rewrite the docstring. |
| `test_real_samples.py` Bridon `"100060000080880"` | Expect `"merged"`. The comment should say `add_rel`. |
| `test_robustness_vlm_output.py` PO `8100004` | Expect `merged`. Docstring item D: "merged by rule x10". |
| `test_synthetic_spec.py:335` and `test_erp_line_formats.py` | No change. |
| `test_matching.py` docstring | Wording only. |

## 10. Docs (Haiku, after the code is green)

| Doc | Change |
|---|---|
| `docs/CORE_LOGIC.md` | Section 3 gets a "Line-number fallback" paragraph (the four rules, I1-I7). In section 7, replace the "step-10 is not mapped" bullet with the limitations pinned in section 9. Section 8 lists the new business tests. |
| `AAM_merger_V3_PRODUCT.md` | Sections 2 and 7 get the rule. Section 8 gets one entry per `test_limitation_*` / `test_known_limitation_*`. |
| `AAM_merger_V3_SPEC.md` | Add FR-8.1a. |
| `HANDOFF_PLAN.md` | Update the status. |

## 11. Experiments (read-only, 2026-10-24)

The scripts are in the session scratchpad (`exp.py`, `exp_b2.py`). They import the real `normalize_line_no` and use a prototype of section 4 that matches it line for line. The databases are opened with `mode=ro`.

### 11.1 Corpus replay (comparison-level; quarantined sets also need Redo matching)

| `data/aam_merger.db` outcome | Sets |
|---|---|
| merged x10 / add_rel | 39, 40, 42 / 48, 50 |
| pending partial | 41, 55 (x10); 49, 51 (add_rel) |
| mismatched | 43 (x10, 32 vs 19); 47 (add_rel, 50 vs 46) |
| still quarantined | 15 (SI maps x10, DN keys 11 and 19 have no target); 20, 21, 45 (no rule fits) |
| mapped but held first by `multiple_po_documents` | 17, 18, 34, 35 |

The previous-run DB shows the same pattern. Neither corpus contains a real `div10` or `strip_rel` case.

### 11.2 Variation experiment

The experiment covered 117 usable sets from both DBs, 34 styles, the full, partial and vendor-serial-renumbered delivery shapes, and 5 placements (PO varied, vendor varied, SI only, PO step-10 with the vendor varied, and mixed documents). That is **30,544 comparisons**.

| Result | Value |
|---|---|
| Merged with any wrong pairing | **0** |
| Wrong pairing through a mapping (all mismatched or pending, never merged) | Only where the PO uses step-20/step-5 numbering or the vendor offsets by one, on partial deliveries, and where the vendor renumbers a partial delivery as its own 1..m (the exact path pairs those wrongly too). A later full delivery re-resolves the pool from scratch, so a partial mis-pairing can never be carried into a merge. |

How each ERP style fares:

| ERP style | Handled by `normalize_line_no` alone | Needs a rule (planned) | Stays held |
|---|---|---|---|
| Zero padding `01`, `001`; SAP `010`/`0010`/`00010`/`000010` vs `10`; outer spaces | yes | | |
| SAP `00010` (or `10`) vs serial `1` | | x10 / div10 | |
| `1-1` (and `01-01`) vs `1` | | add_rel / strip_rel | |
| SAP schedule `00010-0001` vs PO `10` | `00010-0001`→`10-1` | strip_rel | |
| SAP schedule vs serial PO (`10-1` vs `1`), `00010/1` | | | yes (no composition, slash) |
| Oracle/Maximo `1.1`, `1.0`, `1.1.1`; `1/1`, `1_1` | | | yes |
| Unicode dashes `1–1`, `1−1` | | | yes (see 11.4) |
| `Line 10`, `#10`, `Item 10`, `10.`, `10.0` | | | yes (the prompt asks for the bare number) |
| Step 5/20/100, offset +1, vendor renumbering with gaps, mixed documents in a pool | | | yes |

### 11.3 False-merge harmlessness (cross-set pairs)

Every PO was paired with the DN and SI pools of every **other** real set (the same PO in both DBs counted once).

| Measure | Value |
|---|---|
| Unique real sets / ordered cross pairs on the true line identity | 75 / 5,032 |
| Cross pairs that are merge-eligible under exact matching with the same numbering on both sides (the existing "same number, same quantity" exposure, which PO-number grouping prevents in production) | 104 (103 single-line, 1 multi-line) |
| Styled cross pairs (PO and vendor each in plain, step10 or rel) | 45,288; exact-only merge-eligible 312; with fallback 728 |
| **Fallback merge-eligible pairs beyond the same-numbering baseline** | **0** |
| Raw printed keys (all sets with PO+DN+SI, including those quarantined today): pairs / exact / fallback / fallback-only | 5,700 / 76 / 110 / 34 |
| Of those 34 fallback-only pairs | 33 are single-line POs; 1 is three lines with uniform quantities (cur S41 PO 10/20/30 at qty 3 against prev S6 1/2/3 at qty 3). div10 accounts for 7 of them. All are the same-quantity exposure a plain-numbered PO already has. |
| Q1 shapes: own PO+DN with a foreign SI in plain/step10/rel (15,096); own PO with a foreign DN step10 and SI rel or vice versa (10,064) | 0 merge-eligible beyond baseline in both |
| Single-line exposure | 31 unique single-line sets, 28 with DN+SI; **95 ordered cross pairs share the quantity** (qty 2: 49, qty 1: 42, qty 20: 2, qty 4: 2) |

**Conclusion.** The fallback, including div10 and strip_rel, never creates a merge that exact matching would not also create if both sides used the same numbering. What it does is give step-10 and `-1` POs the same exposure that plain-numbered POs already have: the accepted single-line, same-quantity limitation (PRODUCT 8-H), which PO-number grouping guards in production. **The reverse rules are harmless by this evidence, so they are ON by default.**

### 11.4 Proposed separate item (NOT in this plan): Unicode dashes in `normalize_line_no`

Map U+2010-U+2015 and U+2212 to `-` before the zero strip. This changes the exact path for every set. It also flips the strict-xfail `en dash` pair in `test_erp_line_formats.py::CONVENTION_PAIRS`, which must move to the "works" list. That test is `test_unicode_dash_line_numbers_normalise_to_hyphen`, with inputs `1–1`, `1−1`, `01‒01` and expected output `1-1`.

The corpus has no Unicode-dash key, so this is cheap but unproven. Ship it separately, if the user wants it. Zero padding needs nothing; it is already handled.

## 12. Acceptance (in order; all must pass)

```bash
uv run ruff check . && uv run ruff format --check .
uv run ty check src
uv run pytest tests/test_line_mapping.py tests/test_line_mapping_variations.py tests/test_line_mapping_real_shapes.py -q
uv run pytest tests/test_business_scenarios_ui.py -q
uv run pytest tests/test_migrate_db.py tests/test_reconciliation.py tests/test_real_samples.py tests/test_robustness_vlm_output.py -q
uv run pytest -q
```

Then two checks:
- **Replay:** re-run the section 11 replay with the real `resolve_line_mapping` and `compare_po_set_lines` against `file:data/aam_merger.db?mode=ro` and confirm table 11.1. Never write to `data/*.db`.
- **Browser:** on a DB copy, Redo-match set 40, then open its page and confirm the mapping flag and the matrix markers.

## 13. Work breakdown

| Step | Tier |
|---|---|
| Sections 4-5 code and all of section 9 | Sonnet (one agent; tightly coupled) |
| Section 10 docs | Haiku |
| Commit review (reconciliation, matching, migration), with `detect_changes` | Opus (required) |

## 14. Decisions

| Q | Decision | Why |
|---|---|---|
| Q1 | **Independent pools (user).** DN and SI each resolve against the PO. Exact+mapped and two different rules are allowed. Within a pool, all documents and lines follow one rule; a mixed pool is not mapped. | The user decided this. Wrong-merge hunt for the new shapes: 0 merge-eligible results beyond baseline in 15,096 DN-exact + foreign-SI-mapped pairs and 10,064 two-rule pairs (11.3), and 0 in the SI-only variations (11.2). A wrong exact pairing in one pool can only merge if that pool covers every PO key as printed, which means it really is in PO numbering. A partial wrong pairing never merges, and I6 rejects it once the pool becomes mixed. **No new guard needed.** |
| Q2 | **Reverse rules ON by default**, each with its own switch. | The harmlessness experiment found 0 fallback merges beyond the same-numbering baseline. The residual exposure is the accepted single-line limitation (pinned). |
| Q3 | **Kept.** div10 needs PO keys that are all plain digits and not all multiples of 10. A "no vendor key is also a PO key" guard was rejected because it would block every legitimate PO with 10 or more lines. Q1 does not affect this: the guard is per pool. | The four rules' preconditions exclude each other (pinned). |
| Q4 | **Yes.** PO keys are never rewritten, and only the vendor side changes in each comparison. | Pinned by `test_compare_po_keys_are_never_rewritten`. |
| Q5 | **Kept: a mixed pool is never mapped (I6).** Q1 does not affect it: the guard was always per pool. The old "DN and SI must agree" hold is removed. | `test_mixed_pool_is_not_mapped` and the business test. |

## 15. Still needs the USER's decision

1. **Bulk re-run of currently quarantined sets** (39-43, 47-51 in `data/aam_merger.db`). The options are Redo matching per set, or a one-off script. A script would be a new item.
2. **"Merged • review" vs "Merged & Ready"** for a set merged under a mapping. The plan uses "Merged • review".
3. **Unicode-dash normalisation** (11.4). Ship it as a separate small item, or leave it?
