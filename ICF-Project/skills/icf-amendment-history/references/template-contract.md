# Template Contract

The MVP supports the repository's standard template only.

## Required Anchors

- Cover table row containing:
  - `原版本号/日期`
  - `修订后版本号/日期`
- Revision table header row containing, in order:
  - `主题`
  - `修订章节/页码`
  - `原文`
  - `修订后内容`
  - `更改原因`

## Allowed Mutation Scope

- Cover table old/new version value cells
- Revision table data rows under the header
- `word/document.xml` only
- Text runs and nested `w:tbl` blocks inside those data cells

## Protected Scope

- Any non-`word/document.xml` package part
- Revision table header row
- Non-whitelisted cover cells
- All other template structure and formatting

The writer edits `word/document.xml` directly. It must not save the template through a high-level Word library that rewrites unrelated package parts or table structure.
