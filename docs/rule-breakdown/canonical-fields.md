# Canonical Fields Reference

Human-readable reference for the string fields that receive comparison-time
canonicalization in the rules engine. Machine-readable form:
[`samantha_server/data/canonical-fields.json`](../../samantha_server/data/canonical-fields.json).

## Policy

**Comparison-time only.** `Order` field values are stored exactly as the
upstream message sent them. Canonicalization is applied only when a primitive
evaluator (`Equals`, `InEnum`, `Contains`) compares a value against a rule
literal.

**Rules applied at comparison time (in order):**

1. Trim leading and trailing whitespace.
2. Case-fold (`str.casefold()` — handles multi-character folds like `ß → ss`).
3. Pick-list check — if the casefolded value appears in the field's pick list,
   the match proceeds normally.
4. Unknown pass-through — if the casefolded value is **not** in the pick list,
   the engine forwards the casefolded value anyway (no hard error) and records
   a `CanonicalizationTrace` on the `EngineDecision` with `was_unknown=True`.
   This surfaces unknown values in receipts without crashing the engine on new
   specimen types.

**Fields in scope:** `specimen_type`, `anatomic_site`, `fixative`,
`ordered_tests`, `priority`.

**Fields out of scope:** `patient_name`, `patient_sex` — these are only
checked with `is_null`; no pick list applies.

**Pick-list-only model.** Only trim + casefold + pick-list membership check.
Synonym maps (e.g., `lumpectomy with margins → lumpectomy`) were considered
in the original spec but intentionally omitted for POC scope — see the
[design comment](https://github.com/ChrisHenryOC/samantha_server/issues/35#issuecomment-4407628775).

---

## Pick Lists

### `specimen_type`

| Canonical value | Notes |
|---|---|
| `biopsy` | Generic biopsy |
| `core_needle_biopsy` | Core needle variant |
| `excision` | Excisional biopsy |
| `lumpectomy` | Surgical excision with margin assessment |
| `resection` | Larger surgical resection |
| `mastectomy` | Full or partial mastectomy specimen |
| `fna` | Fine-needle aspiration (cytology) |
| `fine_needle_aspiration` | Alternate spelling for `fna` |
| `cytology` | Generic cytology specimen |
| `cytospin` | Cytospin preparation |
| `cell_block` | Cell block from cytology |
| `bone_marrow_aspirate` | Bone marrow aspirate |
| `body_fluid` | Body fluid cytology |
| `touch_prep` | Touch preparation |
| `direct_smear` | Direct smear preparation |
| `thinprep` | ThinPrep liquid cytology |
| `surepath` | SurePath liquid cytology |
| `brushing` | Brush cytology |
| `washing` | Washing cytology |
| `swab` | Swab specimen |

### `anatomic_site`

| Canonical value | Notes |
|---|---|
| `breast` | Unspecified breast |
| `left breast` | Left breast |
| `right breast` | Right breast |
| `axillary lymph node` | Axillary lymph node |
| `chest wall` | Chest wall |
| `lung` | Lung (out-of-scope for breast workflow; triggers rejection via ACC-003) |
| `skin overlying breast` | Skin overlying breast |

### `fixative`

| Canonical value | Notes |
|---|---|
| `formalin` | 10% neutral buffered formalin (standard) |
| `alcohol` | Alcohol-based fixative (incompatible with HER2 per ACC-005) |
| `fresh` | Fresh/unfixed specimen |

### `priority`

| Canonical value | Notes |
|---|---|
| `routine` | Standard processing priority |
| `stat` | Urgent / STAT priority |
| `rush` | Rush priority |

### `ordered_tests`

Each element of the `ordered_tests` tuple is canonicalized independently.

| Canonical value | Notes |
|---|---|
| `her2` | HER2 immunohistochemistry |
| `breast ihc panel` | Full breast IHC panel (implies HER2 per ACC-005/ACC-006/ACC-009) |
| `er` | Estrogen receptor |
| `pr` | Progesterone receptor |
| `ki-67` | Ki-67 proliferation marker |
| `h&e` | Hematoxylin and eosin stain |
| `pd-l1` | PD-L1 immunohistochemistry |

---

## `was_unknown=True` receipt field

When `_canonicalize(field, value)` returns `was_unknown=True`, the engine
appends a `CanonicalizationTrace` to `EngineDecision.decision_traces`:

```python
CanonicalizationTrace(
    kind="canonicalization_warning",
    field="specimen_type",       # which field warned
    raw_value="LCMI",            # literal as the upstream sent it
    canonical_value_attempted="lcmi",  # post trim+casefold; not in pick list
)
```

This trace is deduplicated by `(field, raw_value)` within a single
`EngineDecision`. Clinical traceability: reviewers can see the raw
input alongside the warning without re-fetching the source message.
