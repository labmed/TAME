# Key-Based JOIN Checks

2026-09-11. Optional ACTION fields extend the existing `JOIN` operation, without
changing the TAME container version or adding an NHANES-specific command.

```toml
[ACTIONS.JOIN_MEASUREMENTS]
TYPE = "JOIN"
RIGHT = "/absolute/path/measurements.tame"
ON = ["SEQN"]
HOW = "left"
VALIDATE = "one_to_one"
MISSING_KEYS = "ERROR"
REQUIRE_RIGHT_MATCH = true

[ACTION_PIPELINES]
COMBINE = ["JOIN_MEASUREMENTS"]
```

Run with `tametools preprocess base.tame COMBINE --output joined.tame`.
A relative `RIGHT` is resolved against the working directory, not the input file's
directory. The tutorial generates absolute paths; prepare again after moving a run.

| Field | Default | Contract |
|---|---|---|
| `VALIDATE` | omitted | pandas cardinality validation: `one_to_one`, `one_to_many`, `many_to_one`, `many_to_many`; uniqueness applies to the combined key |
| `MISSING_KEYS` | `ALLOW` | `ERROR` rejects absent, explicit NULL, empty and whitespace-only keys on either side |
| `REQUIRE_RIGHT_MATCH` | `false` | `true` rejects right-hand input rows whose complete key is absent from the left |

Legacy defaults retain unchecked cardinality and pandas missing-key behavior.
For participant-level NHANES components, set **all three** fields as shown above.
`many_to_many` permits row expansion and is not appropriate for this tutorial.
Keys are compared without case folding, whitespace trimming or numeric coercion.
Existing reader normalization still applies, including CSV empty-cell handling.
`LEFT_ON` and `RIGHT_ON` can select differently named composite keys.

The operation table adds `validate`, `right_rows`, `left_unmatched_rows` and
`right_unmatched_rows`. Counts describe input rows, including duplicates, not unique
people or multiplied output rows. Detailed pipeline logs retain the executed
configuration and content hashes.

## Scope

`JOIN` adds columns by keys; `merge` stacks rows; `integrate` applies explicit
measurement mappings and unit harmonization. These operations are not interchangeable.

JOIN keeps left metadata and right header tags when names do not collide. It does
**not** reconcile two `COLUMN`, `SURVEY`, `ANALYSIS_PLAN` or source contracts, infer
component eligibility, or decide whether cycles are poolable. The NHANES tutorial
rejects overlapping non-key names and restores a reviewed column catalog after
checking every selected raw cell. Arbitrary right-hand units and plans are not
automatically imported. Source checks and per-cell verification are tutorial steps,
not guarantees of the generic JOIN. Inspect the effective logged configuration.

See [five-component NHANES tutorial](NHANES_DOWNLOAD.md).
