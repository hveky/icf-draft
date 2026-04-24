from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.icf_parser.acceptance import validate_generated_docx_against_acceptance


def _resolve_path(path: Path) -> Path:
    return path if path.is_absolute() else (Path.cwd() / path).resolve()


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Compare a generated amendment-history .docx against an acceptance .docx.")
    parser.add_argument("--generated", required=True, type=Path, help="Generated amendment-history .docx path.")
    parser.add_argument("--acceptance", required=True, type=Path, help="Acceptance .docx path.")
    parser.add_argument("--output", required=True, type=Path, help="Output diff report .json path.")
    return parser


def main() -> int:
    parser = build_argument_parser()
    args = parser.parse_args()
    generated_path = _resolve_path(args.generated)
    acceptance_path = _resolve_path(args.acceptance)
    output_path = _resolve_path(args.output)

    report = validate_generated_docx_against_acceptance(generated_path, acceptance_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(asdict(report), ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "output_path": str(output_path),
                "delivery_passed": report.delivery_passed,
                "blocking_issue_count": len(report.blocking_issues),
            },
            ensure_ascii=False,
        )
    )
    return 0 if report.delivery_passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
