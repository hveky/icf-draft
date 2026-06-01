from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[3]
ACCEPTANCE_ROOTS = (
    REPO_ROOT / "训练集" / "训练文件验收文件",
    REPO_ROOT / "测试集" / "验收文件",
)
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.icf_parser.service import generate_amendment_history_safe


def _resolve_path(path: Path) -> Path:
    return path if path.is_absolute() else (Path.cwd() / path).resolve()


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate an ICF amendment-history document via the skill MVP.")
    parser.add_argument("--source", required=True, type=Path, help="Tracked ICF .docx path.")
    parser.add_argument(
        "--template",
        type=Path,
        default=REPO_ROOT / "TG-ICF模板" / "标准模板.docx",
        help="Standard template .docx path.",
    )
    parser.add_argument("--acceptance", type=Path, help="Optional acceptance .docx path.", default=None)
    parser.add_argument("--output", required=True, type=Path, help="Generated amendment-history .docx path.")
    parser.add_argument(
        "--mode",
        choices=("draft", "release"),
        default="draft",
        help="Run in draft mode or release mode. Release mode requires --acceptance.",
    )
    return parser


def _guess_acceptance_path(source_path: Path) -> Path | None:
    stem = source_path.stem
    candidates = (
        f"{stem}-验收.docx",
        f"{stem}验收标准文档.docx",
        f"{stem}-验收标准文档.docx",
    )
    for root in ACCEPTANCE_ROOTS:
        for candidate in candidates:
            path = root / candidate
            if path.exists():
                return path
    return None


def main() -> int:
    parser = build_argument_parser()
    args = parser.parse_args()
    source_path = _resolve_path(args.source)
    template_path = _resolve_path(args.template)
    output_path = _resolve_path(args.output)
    acceptance_path = _resolve_path(args.acceptance) if args.acceptance is not None else None
    if args.mode == "draft" and acceptance_path is None:
        acceptance_path = _guess_acceptance_path(source_path)

    result = generate_amendment_history_safe(
        source_docx=source_path,
        template_docx=template_path,
        output_docx=output_path,
        acceptance_docx=acceptance_path,
        progress_dir=REPO_ROOT / "output" / "progress" if acceptance_path is not None else None,
        mode=args.mode,
    )

    payload = {
        "mode": args.mode,
        "output_path": str(result.output_path),
        "report_path": str(result.report_path),
        "row_count": result.row_count,
        "delivery_status": result.delivery_status,
        "delivery_passed": result.delivery_passed,
        "draft_only": result.draft_only,
        "integrity": {
            "is_valid": result.integrity.is_valid,
            "unexpected_parts": result.integrity.unexpected_parts,
        },
        "acceptance_path": str(acceptance_path) if acceptance_path is not None else "",
        "acceptance_diff_path": str(result.acceptance_diff_path) if result.acceptance_diff_path is not None else "",
        "progress_path": str(result.progress_path) if result.progress_path is not None else "",
        "blocking_issues": result.blocking_issues,
        "diagnostic_notes": result.diagnostic_notes,
    }
    print(json.dumps(payload, ensure_ascii=False))
    return 0 if (result.integrity.is_valid and (args.mode == "draft" or result.delivery_passed)) else 2


if __name__ == "__main__":
    raise SystemExit(main())
