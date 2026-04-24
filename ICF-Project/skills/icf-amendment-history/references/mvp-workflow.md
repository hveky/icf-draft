# MVP Workflow

## Inputs

- `--source`: tracked ICF `.docx`
- `--template`: standard amendment-history template `.docx`
- `--output`: destination `.docx`

## Command

```bash
python skills/icf-amendment-history/scripts/generate.py \
  --source "训练集/训练文件/training-1.docx" \
  --template "TG-ICF模板/标准模板.docx" \
  --output "output/R26-skill-mvp.docx"
```

## Outputs

- Main document: requested `--output`
- Sidecar report: same filename with `.report.json`

## Success Criteria

- `.docx` exists
- `integrity.is_valid == true`
- `version_row_first == true`
- report contains `rule_hits` and `warnings`
