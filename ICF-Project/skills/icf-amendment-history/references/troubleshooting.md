# Troubleshooting

## `unsupported template`

Cause:
- the template does not contain the expected cover/revision anchors

Action:
- switch back to the standard template
- if a new company template is required, add a new contract instead of weakening the current one

## `integrity.is_valid == false`

Cause:
- non-whitelisted OOXML parts changed
- revision table/cover structure drifted

Action:
- run `scripts/validate.py` against the generated output
- inspect `unexpected_parts`
- stop delivery until the validator passes

## Missing output document

Cause:
- generation raised before final copy

Action:
- check the CLI stderr
- inspect the sidecar report path if it exists
- re-run with a writable output directory
