from __future__ import annotations

import argparse
from pathlib import Path
from typing import Iterable

from src.icf_parser.service import generate_amendment_history_safe


def generate_amendment_history(source_path: Path, template_path: Path, output_path: Path) -> Path:
    result = generate_amendment_history_safe(source_path, template_path, output_path)
    return result.output_path


def generate_batch_amendment_histories(
    source_paths: Iterable[Path],
    template_path: Path,
    output_dir: Path,
) -> list[Path]:
    template_file = Path(template_path)
    destination_dir = Path(output_dir)
    destination_dir.mkdir(parents=True, exist_ok=True)

    results: list[Path] = []
    for source_path in source_paths:
        source_file = Path(source_path)
        output_path = destination_dir / f"{source_file.stem}-amendment-history.docx"
        results.append(
            generate_amendment_history(
                source_path=source_file,
                template_path=template_file,
                output_path=output_path,
            )
        )
    return results


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate ICF amendment history document from a tracked DOCX.")
    parser.add_argument("source_path", type=Path, help="Path to the tracked ICF .docx file.")
    parser.add_argument("template_path", type=Path, help="Path to the standard amendment history template.")
    parser.add_argument("output_path", type=Path, help="Path to the generated amendment history .docx file.")
    return parser


def main() -> int:
    parser = build_argument_parser()
    args = parser.parse_args()
    output_path = generate_amendment_history(
        source_path=args.source_path,
        template_path=args.template_path,
        output_path=args.output_path,
    )
    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
