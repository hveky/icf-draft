# Output Contract

Every formal run should leave behind reproducible artifacts.

## Required Artifacts

- Generated amendment-history `.docx`
- `*.report.json`
- `*.acceptance_diff.json` for every `release` run
- A progress note under `output/progress/`

## Status Fields

Every report must expose:

- `mode`
- `delivery_status`
- `delivery_passed`
- `draft_only`
- `blocking_issues`
- `diagnostic_notes`

## Naming

- Round naming follows `R{round}-{node}`.
- Training examples should use names like:
  - `R29-training1-release`
  - `R29-training2-release`
  - `R29-training3-release`
- Test examples should use names like:
  - `R29-test-ICF-1-release`
  - `R29-test-ICF-2-release`

## Minimum Delivery Report Fields

- integrity verdict
- delivery verdict
- field diffs
- order diffs
- style diffs
- missing rows
- unexpected rows
- diagnostic alignment
