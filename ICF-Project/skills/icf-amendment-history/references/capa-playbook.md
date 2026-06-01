# CAPA Playbook

Use this file to classify differences after each acceptance comparison.

## Gap Categories

- `Extraction gap`: the source change was lost, truncated, or mislabeled before rule classification.
- `Rule gap`: the extracted fact exists, but topic, section/page, merge granularity, or reason does not match acceptance style.
- `Template gap`: the right row exists, but formatting or placement is wrong.
- `Comparator gap`: generated and acceptance rows are semantically aligned, but the comparison logic is too literal or forces low-confidence matches.
- `Acceptance noise`: the acceptance file contains likely manual inconsistency that should not drive hard-coded logic.

## CAPA Mapping

- For extraction gaps:
  - add or extend a failing extractor test
  - repair XML or structure parsing
  - common trigger: version metadata lives in `header*.xml` rather than `footer*.xml`
- For rule gaps:
  - add a training-sample regression test
  - generalize the rule without hard-coding sample-specific text
  - common trigger: acceptance wants one business summary row for repeated terminology swaps, but the generator emits many literal fragments
  - common trigger: adjacent rows share topic/page/reason, but a pure insertion must not be merged into a neighboring replacement row
- For template gaps:
  - add a rendering or integrity regression test
  - repair the template writer while keeping the whitelist intact
- For comparator gaps:
  - add a failing acceptance-comparison regression test first
  - normalize stable label variants such as `版本号/日期` vs `版本号及日期`
  - normalize plain `版本号` as a version-topic variant when the acceptance file uses the shorter title
  - parse both `P12`-style and `第12页`-style page labels before computing section/page similarity
  - allow acceptance-side version rows to appear later in the document as long as the generated output still promotes version metadata to the first row
  - treat first-page center metadata as equivalent even when acceptance renders it under `正文`
  - accept red-bold or plain-bold emphasis as a valid acceptance-side insertion marker
  - raise the matching threshold or leave rows unmatched if the best pair is still low-confidence
- For acceptance noise:
  - document the mismatch in the progress note
  - do not patch the code unless the pattern repeats across files

## Promotion Rule

Only promote a lesson into the skill or `开发规则.md` when it survives at least one additional sample or clearly applies across both training and test conventions.
