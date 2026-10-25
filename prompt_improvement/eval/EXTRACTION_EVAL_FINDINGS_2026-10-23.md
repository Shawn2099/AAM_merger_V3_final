# Extraction evaluation: findings and decisions (2026-10-23)

Scope: how the VLM (`openai/gpt-6-luna` via OpenRouter) should receive documents and what the
prompt should say, measured on image-verified gold sets. Rule that drove every decision (user,
2026-10-23): **what gets merged must be perfect; holding or skipping a doubtful document is fine.**
A wrong value is far worse than a null.

All numbers below come from `prompt_improvement/eval/results/` and are reproducible with the
commands in section 8. Samples are small (37 tuning docs, 29 held-out docs, 3 repeats each), so
differences of one or two runs are noise.

---

## 1. Method

| Piece | What it is |
|---|---|
| Gold set (tuning) | `gold.json`, 37 documents transcribed from rendered page images, then audited by Opus (26 docs re-read blind). Uncertain fields are moved to `unscored_reads` and not scored. |
| Gold set (held-out) | `gold_heldout.json`, 29 different documents including vendors absent from tuning (Borr, GARMCO, Bahrain Airport Services, IMI, first Noble DN/SI). Audited by Opus (all 29 checked). The prompt agents never opened it while tuning. |
| Harness | `run_extraction.py`, `run_variants.py`, `variants.py`, `score.py`, `usage_report.py`. Runs the app's real instructor + OpenRouter path with a pluggable prompt/transport. DB is opened read-only. |
| Scoring | Per field `correct / WRONG / null`. A wrong non-null value is counted separately from a null. A line the model invents fails the document. Gold-ambiguous documents (two PO numbers) are correct only if the model sets `po_reference_ambiguous`. Precision = correct / (correct + wrong). |
| Ground truth rule | Page images only. The PDF text layer and DB-extracted values are never truth (verified unreliable for this corpus). |

## 2. Corrections to earlier claims

- **X3 (doc 23, `50137194` vs `50117194`) was not a misread.** At 14x zoom the delivery note prints
  `50137194`; its sibling invoice (doc 22) prints `50117194`. The vendor's own documents disagree, so
  the quarantine on set 8 was correct. Doc 23 is excluded from PO scoring.
- **Gemini "40-batch" notes were treated as unverified hints.** Not adopted: renaming line numbers in
  the prompt (`1-1`->`1`, `10`->`1`), "prefer the 8-10 digit PO", "top 35% header" (the Drilling Tools
  header is a narrow block at the left edge, not a band), and unmeasured accuracy claims.
- The earlier "Valaris quantity and price swapped" finding (rev. 1 of the PDF/DB comparison) was an
  artefact of the text layer and stays retracted.

## 3. How the PDF reaches the model (biggest finding)

The app sent the PDF as a native base64 `file` part. Token counts were identical with and without
an explicit `native` plugin, so **the default path is native**. OpenRouter's OCR engine
(`mistral-ocr`) is worse and costs about 10x.

Live prompt, tuning set, 37 docs x 3 repeats:

| Transport | Docs passing | Precision | Wrong fields | Null header fields |
|---|---|---|---|---|
| Native PDF (current app) | 82.0% | 96.9% | 14 | docs 86 and 121 null in every repeat |
| Native, explicit plugin | 83.8% | 97.8% | 12 | same |
| Mistral OCR plugin | 78.0% (partial) | 92.1% | 10 wrong line quantities | 40 of 111 calls failed (HTTP 402 key limit) |
| **Pages as images, 150 DPI** | **90.1%** | **98.0%** | 11 | **none** |
| Images, 200 DPI | 88.3% | 98.1% | 13 | none (adds new misreads, 10k tokens) |
| 150 DPI + `reasoning.effort=high` | **91.9%** | **98.7%** | **9** | none |
| 150 DPI + effort low / medium / seed | 90.1 / 89.2 / 90.1% | no gain over images alone | | |

Held-out check (live prompt, 29 docs): native PDF **73.6%** pass, 18 of 78 runs lost the document
number and PO, and all three two-PO documents were guessed instead of held. Images + effort high:
95.4%. So the transport result generalises.

Decision: **150 DPI page images + `reasoning.effort=high`** (about $0.0006 per document).
This replaces the old "no rasterization" rule.

Other facts: the model accepts `reasoning` / `reasoning_effort` (`low`, `medium`, `high`,
`minimal`, `none`, `xhigh` all accepted), `seed`, and `verbosity`. It already uses some reasoning by
default. Cost per document varies about 5x between identical runs (provider routing), so treat the
cost column as accounting only.

## 4. Prompt findings

Failures of the live prompt:

- It says "strip non-alnum, upper" for the PO number. The model then squashes `22613 , 0` into
  `226130` (doc 113, wrong 3 of 3 runs; doc 114 on held-out, wrong 3 of 3) and turns `LPO26/2360`
  into `LPO2612360`. `normalize_po_no` already strips revisions, but only if the comma survives.
- Two PO numbers on one document were flagged ambiguous in only 1 of 6 runs (docs 258, 268).
- The old STEP 5 line "emit first po_reference" contradicts the ambiguity rule.

Variants (all on 150 DPI + effort high):

| | P0 live | P1 | P2 | P3 | P4 vendor hints | P5 |
|---|---|---|---|---|---|---|
| Tuning: docs passing | 94.6% | 96.4% | 97.3% | 98.2% | 100% | 97.3% |
| Tuning: wrong PO refs | 5 | 2 | 1 | 0 | 0 | 3 |
| Tuning: wrong lines | 1 | 2 | 2 | 2 | 0 | 0 |
| Held-out: docs passing | 95.4% | | | | 100% | **100%** |
| Held-out: wrong values | 4 | | | | 0 | **0** |

- P1: copy PO/document numbers exactly as printed, whole-page header scan, explicit ambiguity rule.
- P2: P1 plus a label-alias list mined from the tuning pages (Del. Note No, Buyers Order No, DPO
  Number, ...).
- P3: P2 plus synthetic few-shot examples.
- P4: P2 plus per-vendor layout hints (overfit probe).
- P5: P2 plus one generic rule that release markers such as `Line\Rel No: N\1` are not the line
  number. P1 to P3 made the model copy `1\1`, `4/1` into `line_item_no` (spurious lines).
- P6 (P5 + new synthetic two-PO shapes, STEP 5 rewritten): 98.9% on held-out with one false
  ambiguity flag. Rejected. Its tuning-set run is invalid (calls failed on the key limit).

Results:

- "Copy exactly" is the most solid gain (docs 113 and 114 fixed in every variant).
- **P4 (vendor hints) equals P5 on documents it never saw (both 100%), so per-vendor hints add
  nothing measurable.** Decision: keep the prompt generic. If vendor hints are ever wanted, apply
  them per document after identifying the issuer, never as a block in every prompt.
- **P5 is the adopted prompt.** 0 wrong values across 87 held-out runs and 108 tuning runs except
  3 PO misreads on the tuning set (one run of doc 22, and the noisy two-PO docs).
- The matrix rules to strip revisions or prefer the 10-digit SAP number were not used; they
  contradict copy-exactly and the ambiguity rule.

## 5. Matching change decided (PO key)

The prompt now returns PO numbers verbatim, so code owns normalisation. Decision (user, approved):

- **Group key = the digits of the PO number**, after the existing revision stripping.
- **Letters are a safety check.** One side with letters and the other without (`60213576 OPG` vs
  `60213576`, `LPO26/2360` vs `262360`, `P213899` vs `213899`) is the same PO. Both sides with
  different letters (`TOGA-PO-25-26-163` vs `ARM-PO-25-26-163`) must not group; the document stays
  unattached with a visible reason.
- Accepted failures ("let it fail"): `J255-300115876` (digits `255300115876`) does not match a plain
  `300115876`. A PO with no digits never groups.
- Checked against the 62 distinct PO values in both gold sets: the only digit collision is the
  intended one (doc 262 `60213576 OPG` with its own SI/DN `60213576`).

## 6. Verification ideas researched (not yet built)

Papers (abstracts only were read; results are on public datasets, not these documents):

- Beyond Logprobs / ExtractConf (arXiv 2606.24420): token log-probs, stated confidence and repeated
  sampling alone turn "always confident"; two differently structured calls combined reach ROC AUC
  0.93, 99.1% accuracy at 80% coverage.
- Perception, Layout, Validation (arXiv 2609.20110): combine agreement, layout and rule checks;
  set the accept threshold on held-out data against a target error.
- ConfBench (arXiv 2608.01792): model calibration varies a lot; measure per model.
- Entropy heat-mapping (arXiv 2505.00746): most OCR errors sit in high-entropy tokens (test whether
  Luna returns log-probs).
- Do VLMs Read or Rewrite? (arXiv 2607.21617): models rewrite short tokens, which matches the
  `LPO26/2360` -> `LPO2612360` behaviour.
- LAVA (arXiv 2608.16763): model extracts, symbolic/arithmetic stage verifies.

Candidate checks, in order of value (all hold the set, never guess):

1. A second, differently built read of the identity fields (header-only on a zoomed crop) that must
   agree. Replaying existing runs can estimate its catch rate without new API calls.
2. Cross-check the PO number against the PO database; a near miss is shown to the operator as a hint
   and never attached.
3. A model-returned "cleaned" value compared with `normalize_po_no(verbatim)`; the model's value is
   never used as the key. Needs an A/B on both gold sets; catches rewrites but not a consistent
   misread.
4. Independent OCR as verifier only (value must appear on the page). Needs measurement for false
   holds.
5. Quantity x price = total (needs a line-total field in the schema; ask first).

Not worth it now: image clean-up (deskew/denoise: indirect evidence, can blur digits or erase
decimal points), Mistral OCR plugin, a second model.

## 7. Open items

- **Doc 262** prints the PO as `60213576 OPG`; its SI and DN print `60213576`. Covered by the digit
  key rule above.
- The OpenRouter key hit its $1.50 total limit mid-evaluation; the limit has since been removed
  (usage $1.70). Check the key's limit before the real run: file requests also need headroom.
- Docs 19, 23, 4, 7, 40 are partly unscored (illegible digits). Doc 121 line numbers are unscored
  (handwritten overwrite).
- Still unmeasured: the verification checks in section 6, log-probability availability, and the
  effect of the whole change on a full sync of the corpus.
- Keep the eval repeatable: add new vendors to the gold sets as they appear, re-audit before
  trusting any new prompt, and reject any variant with more WRONG values than the current one.

## 8. Reproduce

```
E=prompt_improvement/eval
uv run --with pymupdf python $E/run_variants.py --label X --variant img150+eff=high \
    --prompts $E/candidates/P5.py --repeats 3                 # tuning set (gold.json)
uv run --with pymupdf python $E/run_variants.py --label H_X --variant img150+eff=high \
    --prompts $E/candidates/P5.py --docs <held-out ids> --repeats 3
uv run --no-sync python $E/score.py P0 X                      # tuning
uv run --no-sync python $E/score.py H_P0 H_X --gold $E/gold_heldout.json
```

Variant grammar (see `variants.py`): transport `default | native | ocr | img<N>` (images at N DPI),
plus optional `+eff=<low|medium|high>`, `+reff=<x>`, `+seed=<n>`; e.g. `img150+eff=high`. Files: `gold_notes.md` and `gold_heldout_notes.md` record
every reading dispute and the Opus audit changes.
