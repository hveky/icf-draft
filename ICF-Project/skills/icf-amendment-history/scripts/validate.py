from __future__ import annotations

import argparse
from dataclasses import asdict, is_dataclass
import json
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.icf_parser.service import validate_generated_output


def _resolve_path(path: Path) -> Path:
    return path if path.is_absolute() else (Path.cwd() / path).resolve()


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate template integrity for a generated amendment-history DOCX.")
    parser.add_argument("--template", required=True, type=Path, help="Standard template .docx path.")
    parser.add_argument("--output", required=True, type=Path, help="Generated amendment-history .docx path.")
    parser.add_argument("--acceptance", type=Path, default=None, help="Acceptance .docx path for release validation.")
    parser.add_argument(
        "--mode",
        choices=("draft", "release"),
        default="draft",
        help="Validate in draft mode or release mode. Release mode requires --acceptance.",
    )
    return parser


def main() -> int:
    parser = build_argument_parser()
    args = parser.parse_args()
    template_path = _resolve_path(args.template)
    output_path = _resolve_path(args.output)
    acceptance_path = _resolve_path(args.acceptance) if args.acceptance is not None else None

    result = validate_generated_output(
        template_docx=template_path,
        output_docx=output_path,
        acceptance_docx=acceptance_path,
        mode=args.mode,
    )
    delivery_validation = asdict(result.delivery_validation) if result.delivery_validation is not None else None
    print(
        json.dumps(
            {
                "mode": args.mode,
                "draft_only": result.draft_only,
                "delivery_status": result.delivery_status,
                "delivery_passed": result.delivery_passed,
                "blocking_issues": result.blocking_issues,
                "diagnostic_notes": result.diagnostic_notes,
                "integrity": {
                    "is_valid": result.integrity.is_valid,
                    "changed_parts": result.integrity.changed_parts,
                    "unexpected_parts": result.integrity.unexpected_parts,
                    "missing_parts": result.integrity.missing_parts,
                    "added_parts": result.integrity.added_parts,
                },
                "delivery_validation": delivery_validation,
            },
            ensure_ascii=False,
            default=lambda value: asdict(value) if is_dataclass(value) else value,
        )
    )
    return 0 if (result.integrity.is_valid and (args.mode == "draft" or result.delivery_passed)) else 2


if __name__ == "__main__":
    raise SystemExit(main())
