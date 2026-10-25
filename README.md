# dsh-skill-pdf-math

A [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) skill for reading
mathematics out of PDFs and formula screenshots **without guessing**.

## Why

Vision models misread formulas in predictable ways: a `\mathcal{L}` becomes `\angle`,
a summation subscript `j=1` becomes `\cdot`, a tall bracket turns into `[[[`.
Most of that is unnecessary, because a LaTeX-produced PDF stores formulas as
**positioned text**, not images — glyphs, baselines, sizes and font names are all there.

So the skill teaches one rule:

> read the text layer first, let formula OCR supply the structure, and cross-check the two.

## What it adds

- `pdf_formula.py` — turns a PDF region into readable (approximate) LaTeX by restoring
  sub/superscripts from baseline offsets, plus a small TeX `cmex10` operator table.
- `--probe` — decides whether a page's formulas are extractable text or images.
- `--crosscheck` — compares the OCR result against the text layer's letters **and their fonts**
  (`cmsy10`'s `L` *is* `\mathcal{L}`), so a wrong glyph is named instead of merely suspected.
- `--verify` — re-recognises with a different crop and resolution; disagreement is reported.

Measured on four numbered equations from a real paper:

| render | mean similarity | exact |
|---|---|---|
| 400 dpi | 0.941 | 2/4 |
| 600 dpi | 0.977 | 3/4 |

`--check` requires the optional `rapid_latex_ocr` runtime; without it the text-layer
channel still works and the skill says so instead of failing.

## Install

```
dsh plugin --profile <name> add dsh-skill-pdf-math
```

or install it from the DSH plugin marketplace. The skill registers itself as
`pdf-math` and is model-invocable.

## Notes

- Registration is wrapped in a `try/catch`: a broken asset can disable the skill,
  never the host application.
- Paths are injected at run time, so the published package contains no machine-specific paths.

## License

MIT
