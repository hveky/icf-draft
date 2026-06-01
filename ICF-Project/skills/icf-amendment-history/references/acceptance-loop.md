# Acceptance Loop

Use this loop for every `release` round that aims to reach a formally deliverable result.

## Sequence

1. Generate from the tracked source and the standard template.
2. Ensure an acceptance file is provided.
3. Validate template integrity.
4. Run strict delivery validation against the paired acceptance file.
5. Read:
   - `blocking_issues`
   - `field_diffs`
   - `order_diffs`
   - `style_diffs`
   - `missing_rows`
   - `unexpected_rows`
   - `diagnostic_alignment`
6. Decide whether the gap is:
   - extraction
   - rule logic
   - template rendering
   - normalization policy
7. Promote only stable, reusable fixes into `src/icf_parser/`, project docs, and this skill's references.
8. Re-run the same source document before moving to the next sample.

## Delivery Gate

- Training set is the convergence stage.
- Test set is delivery only after the training set has reached `release_passed`.
- A run is not considered ready when it only produces a `.docx`; it must also produce integrity and delivery artifacts.
- If an acceptance file exists, a run is not ready when `delivery_status != release_passed`.
- If no acceptance file exists, the output is `draft` only and cannot be claimed as formal delivery.
