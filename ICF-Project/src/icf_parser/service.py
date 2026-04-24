from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
from zipfile import ZipFile

from lxml import etree

from src.icf_parser.acceptance import (
    AcceptanceDiffReport,
    DeliveryValidationReport,
    compare_delivery_rows_to_acceptance_rows,
    extract_delivery_rows_from_docx,
    validate_generated_docx_against_acceptance,
)
from src.icf_parser.delivery_model import (
    DeliveryRow,
    RevisionFact,
    delivery_rows_from_revision_facts,
    revision_facts_from_candidate_rows,
)
from src.icf_parser.rule_engine import build_candidate_rows
from src.icf_parser.template_writer import COVER_ROW_MARKERS, REVISION_HEADER_MARKERS, write_amendment_history


WORD_NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
VOLATILE_ATTRS = {"paraId", "textId"}
ALLOWED_CHANGED_PARTS = {"word/document.xml"}
VALID_RUN_MODES = {"draft", "release"}


@dataclass(frozen=True)
class IntegrityReport:
    is_valid: bool
    changed_parts: list[str]
    unexpected_parts: list[str]
    missing_parts: list[str]
    added_parts: list[str]
    normalized_document_equal: bool


@dataclass(frozen=True)
class GenerationResult:
    output_path: Path
    report_path: Path
    revision_facts: list[RevisionFact]
    delivery_rows: list[DeliveryRow]
    integrity: IntegrityReport
    mode: str
    delivery_status: str
    delivery_passed: bool
    draft_only: bool
    blocking_issues: list[str]
    diagnostic_notes: list[str]
    delivery_validation: DeliveryValidationReport | None = None
    diagnostic_alignment: AcceptanceDiffReport | None = None
    acceptance_diff_path: Path | None = None
    progress_path: Path | None = None

    @property
    def row_count(self) -> int:
        return len(self.delivery_rows)

    @property
    def rows(self) -> list[DeliveryRow]:
        return self.delivery_rows


@dataclass(frozen=True)
class OutputValidationResult:
    mode: str
    draft_only: bool
    integrity: IntegrityReport
    delivery_status: str
    delivery_passed: bool
    blocking_issues: list[str]
    diagnostic_notes: list[str]
    acceptance_path: Path | None = None
    delivery_validation: DeliveryValidationReport | None = None


def generate_amendment_history_safe(
    source_docx: Path,
    template_docx: Path,
    output_docx: Path,
    *,
    acceptance_docx: Path | None = None,
    progress_dir: Path | None = None,
    mode: str = "draft",
) -> GenerationResult:
    if mode not in VALID_RUN_MODES:
        raise ValueError(f"unsupported mode: {mode}")

    source_path = Path(source_docx)
    template_path = Path(template_docx)
    output_path = Path(output_docx)
    acceptance_path = Path(acceptance_docx) if acceptance_docx is not None else None

    if mode == "release" and acceptance_path is None:
        raise ValueError("release 模式必须提供验收文件。")

    candidate_rows = build_candidate_rows(source_path)
    revision_facts = revision_facts_from_candidate_rows(candidate_rows)
    proposed_delivery_rows = delivery_rows_from_revision_facts(revision_facts, source_docx=source_path)

    acceptance_rows = extract_delivery_rows_from_docx(acceptance_path) if acceptance_path is not None else None
    rendered_rows = proposed_delivery_rows
    write_amendment_history(template_path, output_path, rendered_rows)

    integrity = validate_template_integrity(template_path, output_path)
    delivery_validation = None
    diagnostic_alignment = None
    acceptance_diff_path = None
    progress_path = None
    if acceptance_path is not None:
        if not acceptance_path.exists():
            raise FileNotFoundError(acceptance_path)
        diagnostic_alignment = compare_delivery_rows_to_acceptance_rows(proposed_delivery_rows, acceptance_rows or [])
        delivery_validation = validate_generated_docx_against_acceptance(output_path, acceptance_path)
        acceptance_diff_path = output_path.with_suffix(".acceptance_diff.json")
        acceptance_diff_path.write_text(
            json.dumps(asdict(delivery_validation), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        progress_path = _write_progress_summary(
            source_path=source_path,
            output_path=output_path,
            progress_dir=Path(progress_dir) if progress_dir is not None else output_path.parent / "progress",
            mode=mode,
            integrity=integrity,
            delivery_validation=delivery_validation,
            diagnostic_alignment=diagnostic_alignment,
        )

    delivery_status, delivery_passed, blocking_issues, diagnostic_notes = _resolve_generation_status(
        mode=mode,
        integrity=integrity,
        delivery_validation=delivery_validation,
        diagnostic_alignment=diagnostic_alignment,
    )

    report_path = output_path.with_suffix(".report.json")
    report_path.write_text(
        json.dumps(
            _build_report_payload(
                source_path=source_path,
                template_path=template_path,
                output_path=output_path,
                revision_facts=revision_facts,
                integrity=integrity,
                mode=mode,
                delivery_status=delivery_status,
                delivery_passed=delivery_passed,
                draft_only=(mode == "draft"),
                blocking_issues=blocking_issues,
                diagnostic_notes=diagnostic_notes,
                acceptance_path=acceptance_path,
                delivery_validation=delivery_validation,
                diagnostic_alignment=diagnostic_alignment,
                acceptance_diff_path=acceptance_diff_path,
                progress_path=progress_path,
            ),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    return GenerationResult(
        output_path=output_path,
        report_path=report_path,
        revision_facts=revision_facts,
        delivery_rows=rendered_rows,
        integrity=integrity,
        mode=mode,
        delivery_status=delivery_status,
        delivery_passed=delivery_passed,
        draft_only=(mode == "draft"),
        blocking_issues=blocking_issues,
        diagnostic_notes=diagnostic_notes,
        delivery_validation=delivery_validation,
        diagnostic_alignment=diagnostic_alignment,
        acceptance_diff_path=acceptance_diff_path,
        progress_path=progress_path,
    )


def validate_generated_output(
    template_docx: Path,
    output_docx: Path,
    *,
    acceptance_docx: Path | None = None,
    mode: str = "draft",
) -> OutputValidationResult:
    if mode not in VALID_RUN_MODES:
        raise ValueError(f"unsupported mode: {mode}")
    if mode == "release" and acceptance_docx is None:
        raise ValueError("release 模式必须提供验收文件。")

    template_path = Path(template_docx)
    output_path = Path(output_docx)
    acceptance_path = Path(acceptance_docx) if acceptance_docx is not None else None

    integrity = validate_template_integrity(template_path, output_path)
    delivery_validation = (
        validate_generated_docx_against_acceptance(output_path, acceptance_path)
        if acceptance_path is not None
        else None
    )
    diagnostic_alignment = delivery_validation.diagnostic_alignment if delivery_validation is not None else None
    delivery_status, delivery_passed, blocking_issues, diagnostic_notes = _resolve_generation_status(
        mode=mode,
        integrity=integrity,
        delivery_validation=delivery_validation,
        diagnostic_alignment=diagnostic_alignment,
    )
    return OutputValidationResult(
        mode=mode,
        draft_only=(mode == "draft"),
        integrity=integrity,
        delivery_status=delivery_status,
        delivery_passed=delivery_passed,
        blocking_issues=blocking_issues,
        diagnostic_notes=diagnostic_notes,
        acceptance_path=acceptance_path,
        delivery_validation=delivery_validation,
    )


def validate_template_integrity(template_docx: Path, output_docx: Path) -> IntegrityReport:
    template_parts = _read_docx_parts(Path(template_docx))
    output_parts = _read_docx_parts(Path(output_docx))

    template_names = set(template_parts)
    output_names = set(output_parts)
    missing_parts = sorted(template_names - output_names)
    added_parts = sorted(output_names - template_names)

    changed_parts = sorted(name for name in template_names & output_names if template_parts[name] != output_parts[name])
    unexpected_parts = sorted(name for name in changed_parts if name not in ALLOWED_CHANGED_PARTS)

    normalized_document_equal = False
    if "word/document.xml" in template_parts and "word/document.xml" in output_parts:
        normalized_document_equal = (
            _normalize_document_xml(template_parts["word/document.xml"])
            == _normalize_document_xml(output_parts["word/document.xml"])
        )
        if not normalized_document_equal and "word/document.xml" not in unexpected_parts:
            unexpected_parts.append("word/document.xml")

    is_valid = not missing_parts and not added_parts and not unexpected_parts and normalized_document_equal
    return IntegrityReport(
        is_valid=is_valid,
        changed_parts=changed_parts,
        unexpected_parts=sorted(unexpected_parts),
        missing_parts=missing_parts,
        added_parts=added_parts,
        normalized_document_equal=normalized_document_equal,
    )


def _resolve_generation_status(
    *,
    mode: str,
    integrity: IntegrityReport,
    delivery_validation: DeliveryValidationReport | None,
    diagnostic_alignment: AcceptanceDiffReport | None,
) -> tuple[str, bool, list[str], list[str]]:
    diagnostic_notes = _build_diagnostic_notes(mode, diagnostic_alignment)

    if mode == "draft":
        blocking_issues = ["模板完整性校验未通过。"] if not integrity.is_valid else []
        if delivery_validation is None:
            diagnostic_notes.append("未提供验收文件，本轮仅产出草稿。")
        else:
            diagnostic_notes.append("当前为 draft 模式；即使存在验收文件，也不标记为正式交付。")
        return "draft_generated", False, blocking_issues, diagnostic_notes

    blocking_issues: list[str] = []
    if not integrity.is_valid:
        blocking_issues.append("模板完整性校验未通过。")
    if delivery_validation is None:
        blocking_issues.append("release 模式缺少交付验证结果。")
    else:
        blocking_issues.extend(delivery_validation.blocking_issues)

    delivery_passed = not blocking_issues
    return ("release_passed" if delivery_passed else "release_blocked"), delivery_passed, blocking_issues, diagnostic_notes


def _build_diagnostic_notes(mode: str, diagnostic_alignment: AcceptanceDiffReport | None) -> list[str]:
    notes: list[str] = []
    if diagnostic_alignment is not None and diagnostic_alignment.warnings:
        notes.extend(diagnostic_alignment.warnings)
    if mode == "draft":
        notes.insert(0, "当前输出仅用于草稿预览，不作为正式交付 verdict。")
    return notes


def _build_report_payload(
    *,
    source_path: Path,
    template_path: Path,
    output_path: Path,
    revision_facts: list[RevisionFact],
    integrity: IntegrityReport,
    mode: str,
    delivery_status: str,
    delivery_passed: bool,
    draft_only: bool,
    blocking_issues: list[str],
    diagnostic_notes: list[str],
    acceptance_path: Path | None,
    delivery_validation: DeliveryValidationReport | None,
    diagnostic_alignment: AcceptanceDiffReport | None,
    acceptance_diff_path: Path | None,
    progress_path: Path | None,
) -> dict[str, object]:
    rule_hits = {
        "version_row_first": bool(revision_facts and revision_facts[0].topic == "版本号/日期"),
        "center_info_merged": any(fact.topic == "中心信息" for fact in revision_facts),
        "table_trace_preserved": any("#table" in location for fact in revision_facts for location in fact.source_locations),
        "footer_version_detected": any(
            fact.topic == "版本号/日期" and fact.section_page == "页脚" for fact in revision_facts
        ),
    }
    return {
        "source_path": str(source_path),
        "template_path": str(template_path),
        "output_path": str(output_path),
        "mode": mode,
        "delivery_status": delivery_status,
        "delivery_passed": delivery_passed,
        "draft_only": draft_only,
        "row_count": len(revision_facts),
        "version_row_first": rule_hits["version_row_first"],
        "rule_hits": rule_hits,
        "rule_hit_count": sum(int(value) for value in rule_hits.values()),
        "blocking_issues": blocking_issues,
        "diagnostic_notes": diagnostic_notes,
        "integrity": asdict(integrity),
        "delivery": {
            "acceptance_path": str(acceptance_path) if acceptance_path is not None else "",
            "diff_path": str(acceptance_diff_path) if acceptance_diff_path is not None else "",
            "progress_path": str(progress_path) if progress_path is not None else "",
            "validation": asdict(delivery_validation) if delivery_validation is not None else None,
            "diagnostic_alignment": asdict(diagnostic_alignment) if diagnostic_alignment is not None else None,
        },
    }


def _write_progress_summary(
    *,
    source_path: Path,
    output_path: Path,
    progress_dir: Path,
    mode: str,
    integrity: IntegrityReport,
    delivery_validation: DeliveryValidationReport,
    diagnostic_alignment: AcceptanceDiffReport | None,
) -> Path:
    progress_dir.mkdir(parents=True, exist_ok=True)
    progress_path = progress_dir / f"{output_path.stem}-progress.md"
    progress_path.write_text(
        "\n".join(
            [
                "# Situation",
                f"- 输入文件：{source_path.name}",
                f"- 输出文件：{output_path.name}",
                f"- 运行模式：{mode}",
                "# Task",
                "- 基于标准模板生成交付文件，并以验收文件作为离线金标准进行交付校验。",
                "# Action",
                f"- 模板完整性校验：{'通过' if integrity.is_valid else '失败'}",
                f"- 严格交付校验：{'通过' if delivery_validation.delivery_passed else '阻断'}",
                f"- 关键字段差异：{len(delivery_validation.field_diffs)}",
                f"- 行顺序差异：{len(delivery_validation.order_diffs)}",
                f"- 关键样式差异：{len(delivery_validation.style_diffs)}",
                f"- 缺失验收行：{len(delivery_validation.missing_rows)}",
                f"- 额外生成行：{len(delivery_validation.unexpected_rows)}",
                "# Diagnostic Alignment",
                f"- 主题对齐分数：{diagnostic_alignment.topic_alignment.score:.4f}" if diagnostic_alignment is not None else "- 未提供诊断对齐结果。",
                f"- 章节/页码对齐分数：{diagnostic_alignment.section_page_alignment.score:.4f}" if diagnostic_alignment is not None else "",
                f"- 更改原因对齐分数：{diagnostic_alignment.reason_alignment.score:.4f}" if diagnostic_alignment is not None else "",
                f"- 样式对齐分数：{diagnostic_alignment.style_alignment.score:.4f}" if diagnostic_alignment is not None else "",
                "# Result",
                f"- 交付状态：{'release_passed' if delivery_validation.delivery_passed and integrity.is_valid else 'release_blocked'}",
                f"- 阻断项：{'; '.join(delivery_validation.blocking_issues) if delivery_validation.blocking_issues else '无'}",
                "# Root Cause",
                f"- {_summarize_root_cause(delivery_validation, diagnostic_alignment)}",
                "# CAPA",
                f"- {_summarize_capa(delivery_validation)}",
                "# Rule promoted to skill?",
                "- 仅当阻断项被抽象为稳定规则后，再提升到 skill 与项目文档。",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return progress_path


def _summarize_root_cause(
    delivery_validation: DeliveryValidationReport,
    diagnostic_alignment: AcceptanceDiffReport | None,
) -> str:
    if delivery_validation.delivery_passed:
        return "本轮输出已达到验收文件可交付级一致性。"
    if delivery_validation.order_diffs:
        return "业务行顺序仍与验收文件不一致，当前规则层尚未稳定输出验收顺序。"
    if delivery_validation.field_diffs:
        return "业务字段仍未对齐验收文件，当前规则归类与交付模型存在偏差。"
    if delivery_validation.style_diffs:
        return "关键样式写回仍未对齐验收文件。"
    if diagnostic_alignment is not None and diagnostic_alignment.warnings:
        return "当前规则提案与验收金标准仍存在系统性差异。"
    return "当前输出仍未通过严格交付验证。"


def _summarize_capa(delivery_validation: DeliveryValidationReport) -> str:
    actions: list[str] = []
    if delivery_validation.field_diffs:
        actions.append("补强交付模型字段归一化与规则映射。")
    if delivery_validation.order_diffs:
        actions.append("补强业务行排序与合并规则。")
    if delivery_validation.style_diffs:
        actions.append("补强关键样式写回。")
    if delivery_validation.missing_rows:
        actions.append("补强缺失行生成逻辑。")
    if delivery_validation.unexpected_rows:
        actions.append("收敛多余生成行，避免非验收业务行漏出。")
    if not actions:
        return "维持当前交付模型并继续扩大配对样本回归。"
    return " ".join(actions)


def _read_docx_parts(path: Path) -> dict[str, bytes]:
    with ZipFile(path) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def _normalize_document_xml(payload: bytes) -> bytes:
    root = etree.fromstring(payload)
    _strip_volatile_attrs(root)
    _normalize_cover_values(root)
    _normalize_revision_rows(root)
    return etree.tostring(root, method="c14n")


def _strip_volatile_attrs(root) -> None:
    for element in root.iter():
        for name in list(element.attrib):
            local_name = etree.QName(name).localname
            if local_name.startswith("rsid") or local_name in VOLATILE_ATTRS:
                del element.attrib[name]


def _normalize_cover_values(root) -> None:
    cover_table = _find_cover_table(root)
    if cover_table is None:
        return
    for row in cover_table.xpath("./w:tr", namespaces=WORD_NS):
        cells = row.xpath("./w:tc", namespaces=WORD_NS)
        normalized = [_xml_cell_text(cell) for cell in cells]
        if all(any(marker in text for text in normalized) for marker in COVER_ROW_MARKERS):
            for index in (1, 3):
                if len(cells) > index:
                    _replace_cell_body_with_placeholder(cells[index])
            return


def _normalize_revision_rows(root) -> None:
    revision_table = _find_revision_table(root)
    if revision_table is None:
        return

    rows = revision_table.xpath("./w:tr", namespaces=WORD_NS)
    header_index = None
    for index, row in enumerate(rows):
        cells = row.xpath("./w:tc", namespaces=WORD_NS)
        normalized = [_xml_cell_text(cell) for cell in cells]
        if len(normalized) >= len(REVISION_HEADER_MARKERS) and all(
            marker in normalized[pos] for pos, marker in enumerate(REVISION_HEADER_MARKERS)
        ):
            header_index = index
            break

    if header_index is None:
        return

    for row in rows[header_index + 1 :]:
        revision_table.remove(row)


def _find_cover_table(root):
    for table in root.xpath(".//w:tbl", namespaces=WORD_NS):
        for row in table.xpath("./w:tr", namespaces=WORD_NS):
            normalized = [_xml_cell_text(cell) for cell in row.xpath("./w:tc", namespaces=WORD_NS)]
            if all(any(marker in text for text in normalized) for marker in COVER_ROW_MARKERS):
                return table
    return None


def _find_revision_table(root):
    for table in root.xpath(".//w:tbl", namespaces=WORD_NS):
        for row in table.xpath("./w:tr", namespaces=WORD_NS):
            normalized = [_xml_cell_text(cell) for cell in row.xpath("./w:tc", namespaces=WORD_NS)]
            if len(normalized) >= len(REVISION_HEADER_MARKERS) and all(
                marker in normalized[pos] for pos, marker in enumerate(REVISION_HEADER_MARKERS)
            ):
                return table
    return None


def _replace_cell_body_with_placeholder(cell) -> None:
    tc_pr = cell.find("w:tcPr", namespaces=WORD_NS)
    for child in list(cell):
        if child is tc_pr:
            continue
        cell.remove(child)
    cell.append(etree.Element(f"{{{WORD_NS['w']}}}p"))


def _xml_cell_text(cell) -> str:
    content = "".join(cell.xpath(".//w:t/text() | .//w:delText/text()", namespaces=WORD_NS))
    return "".join(content.split())
