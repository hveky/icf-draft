---
name: icf-amendment-history
description: Generate amendment-history DOCX files from tracked ICF/知情同意书 Word documents using the repository's deterministic Python core, the standard company template, and paired acceptance files as the offline gold standard for release validation.
---

# ICF Amendment History

Use this skill as the agent-facing shell for the repository's deterministic core. Do not re-derive business rules in prompt text. Do not free-edit `.docx` packages outside the controlled scripts.

## Workflow

1. Confirm the required inputs:
   - tracked source `.docx`
   - standard template `.docx`
   - output `.docx`
   - run mode: `draft` or `release`
   - paired acceptance `.docx` for every `release` run
2. Run exactly one of these scripts:
   - [`scripts/generate.py`](scripts/generate.py) for generation
   - [`scripts/validate.py`](scripts/validate.py) for post-hoc validation
   - [`scripts/compare.py`](scripts/compare.py) for strict delivery diff output
3. Treat the Python core in `src/icf_parser/` as the single source of truth for extraction, delivery-row construction, OOXML template writing, integrity validation, and delivery validation.
4. After `generate.py`, inspect:
   - `*.report.json`
   - `*.acceptance_diff.json` when an acceptance file exists
   - `output/progress/*.md`
5. Only treat `release_passed` as formal delivery.
6. Treat every `draft` run as provisional, even if an acceptance file was supplied for diagnostics.

## Guardrails

- Treat `src/icf_parser/` as the source of truth for extraction, classification, template writing, integrity validation, and delivery validation.
- Only the standard template is supported in this MVP.
- The final output must be produced from the template and must pass the template integrity check.
- `release` mode requires an acceptance file. If the user does not have one, switch to `draft`.
- Acceptance files are the offline gold standard; alignment scores are only diagnostic.
- Acceptance files must not be replayed as generated rows. Release output must come from source-derived `DeliveryRow` data.
- `delivery_status == release_passed` is the only acceptable formal-delivery state.
- If strict delivery validation fails, stop and read `blocking_issues`, `field_diffs`, `order_diffs`, `style_diffs`, `missing_rows`, and `unexpected_rows`.
- Never claim “完成交付” based only on `.docx` generation success or on fuzzy alignment scores.
- For deeper OOXML inspection or unusual Word behavior, follow the local `docx` skill workflow instead of improvising ad hoc XML edits.

## References

- [`references/mvp-workflow.md`](references/mvp-workflow.md): MVP operator flow and expected outputs
- [`references/acceptance-loop.md`](references/acceptance-loop.md): gold-standard validation loop
- [`references/capa-playbook.md`](references/capa-playbook.md): how to classify gaps and promote fixes
- [`references/output-contract.md`](references/output-contract.md): required `.docx`, `.json`, and `.md` artifacts per round
- [`references/template-contract.md`](references/template-contract.md): template anchors and whitelist rules
- [`references/troubleshooting.md`](references/troubleshooting.md): failure modes and next actions
