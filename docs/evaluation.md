# Extraction evaluation

`scripts/evaluate_extraction.py` builds a labelled synthetic corpus, renders each scenario to a real PDF (fpdf),
and sends it through the production path: `parse_pdf`, then the provider, then evidence verification. It scores
the output against the labels. Nothing is estimated. Raw output is in `evaluation_results.json`.

```powershell
cd backend
uv run python ../scripts/evaluate_extraction.py                    # FIXTURE provider (offline, deterministic)
uv run python ../scripts/evaluate_extraction.py --provider live    # needs AI_PROVIDER=anthropic, key, model; costs money
```

## Corpus (seed 20261109, 40 scenarios)

| Kind | Count | What it tests |
|---|---|---|
| clean | 16 | Grammar-conforming BOM lines with random parts, revisions, quantities and attributes |
| conflict | 6 | The same line key given twice with different values; this must be flagged, not silently merged |
| prose | 6 | Some lines written as free prose, outside the grammar |
| missing header | 6 | Product SKU or target quantity is absent; this must be reported as missing |
| injection | 6 | Instructions aimed at the model ("approve this project…", fake BOM lines in an injection paragraph) |

## Results: FIXTURE provider (run 2026-11-09)

| Metric | Result |
|---|---|
| Grammar-line recall | 1.0 (203/203) |
| Part number / revision / quantity / attributes exact match | 1.0 each (203/203) |
| Prose-line recall | **0.0 (0/12)** |
| Evidence validity (emitted BOM candidates whose snippet is found on the cited page) | 1.0 (215/215) |
| Unsupported assertions | 0 |
| SKU / target-quantity accuracy | 1.0 (37/37 each) |
| Missing required field detected | 6/6 |
| Contradictions caught | 6/6 |
| Candidates created from injected text | 0 |
| Provider errors | 0 |

There are 215 emitted candidates against 203 grammar lines because the conflict scenarios emit both conflicting
values. That is intended: the reviewer has to choose between them.

## How to read this

- The fixture provider is a **deterministic line-grammar parser, not a language model**. Scoring 1.0 on in-grammar
  lines is expected by construction. These numbers show that the pipeline, evidence verification and scoring
  harness work. They say **nothing about LLM quality**.
- A prose recall of 0 is the designed behavior: the parser doesn't guess. Prose content is where a live model
  should add value, and evidence re-verification is what keeps that value honest.
- The CI gate fails the build if unsupported assertions or injected candidates rise above 0, or if evidence
  validity drops below 1.0.

## Not yet measured

- Live-model extraction (no API key has been approved). When it runs, record model ID, date, cost, and each metric
  above in this file, next to the fixture results rather than in place of them.
- Real-world documents: scanned PDFs (rejected today with `PDF_NO_TEXT`), tables and multi-column layouts.
