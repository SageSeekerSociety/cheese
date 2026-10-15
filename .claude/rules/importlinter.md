---
paths:
  - "backend/.importlinter"
---

# Import-linter: edit the single configuration in its own format

Keep all nine contracts in `backend/.importlinter`. Import-linter 2.15 reads
one configuration, with no include/extends mechanism; `backend/scripts/check_boundaries.py`
requires all nine contract IDs. Splitting the file would require an additional
loader and would stop `uv run lint-imports` reading the same contracts directly.

## A layer is one physical line

Under `layers =`, write each layer on exactly one physical line. Separate
siblings with `:`; do not wrap a long layer for readability. Import-linter's
BaseMultipleValueField.parse (the dependency's domain/fields.py, lines 140–151)
parses physical lines separately, and the layers contract turns each into a
Layer. A line break creates another layer and changes which imports are legal.

## Resolve both additions without changing the layers

- When two branches append different package tokens to the same layer, keep
  both tokens on that same line. Order within the layer is immaterial; order
  between layers is not. Do not duplicate a package in another layer.
- When both branches insert explanatory paragraphs, keep both explanations
  with the contract they describe. Do not discard one merely to shorten a hunk.

Validate the resolved configuration from `backend/`:

```bash
uv run python scripts/check_boundaries.py --self-test
uv run lint-imports
```

## Why this is not split or generated

The frozen investigation found 10/1338 merges (0.75%) conflicting in this file;
6/10 included a layers-line conflict. Most conflicts share the same
domain-layers contract, so separating contracts would not separate those edits. Removing
comments eliminated only one of those ten conflicts. A generator would either
leave the generated layer line conflicting or require every consumer to build
its configuration first. Keep the reasons beside the contracts and resolve the
small number of conflicts explicitly.
