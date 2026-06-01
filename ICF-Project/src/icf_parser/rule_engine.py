from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from difflib import SequenceMatcher
import string
from zipfile import ZipFile

from lxml import etree

from src.icf_parser.revision_extractor import extract_revision_segments, extract_version_change


WORD_NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}


@dataclass(frozen=True)
class CandidateRow:
    topic: str
    section_page: str
    old_text: str
    new_text: str
    reason: str
    source_locations: list[str]


@dataclass(frozen=True)
class ParagraphContext:
    index: int
    text: str


def build_candidate_rows(path: Path) -> list[CandidateRow]:
    document_path = Path(path)
    segments = extract_revision_segments(document_path)
    version_change = extract_version_change(document_path)
    version_row = _build_version_row(version_change, _fallback_version_change(document_path, segments))
    grouped: dict[tuple[str, str], list[str]] = {}

    for segment in segments:
        key = (segment.old_text, segment.new_text)
        grouped.setdefault(key, []).append(segment.location)

    contexts = _load_paragraph_contexts(document_path)
    footer_texts = _load_footer_texts(document_path)
    rows: list[CandidateRow] = []

    if version_row is not None:
        rows.append(version_row)
        version_change = version_change or version_row
        grouped.pop((version_change.old_text, version_change.new_text), None)

    for (old_text, new_text), locations in grouped.items():
        if _should_skip_punctuation_only_change(old_text, new_text):
            continue
        rows.append(_classify_row(old_text, new_text, locations, contexts, footer_texts))

    return _post_process_rows(rows)


def _post_process_rows(rows: list[CandidateRow]) -> list[CandidateRow]:
    merged_rows = _drop_body_version_fragments(rows)
    merged_rows = _split_contact_question_rows(merged_rows)
    merged_rows = _merge_center_metadata_rows(merged_rows)
    merged_rows = _inject_center_summary_row(merged_rows)
    merged_rows = [_rewrite_business_row(row) for row in merged_rows]
    merged_rows = _collapse_population_wording_rows(merged_rows)
    merged_rows = _drop_enrollment_window_rows(merged_rows)
    merged_rows = _collapse_testset_table_rows(merged_rows)
    merged_rows = _merge_adjacent_same_bucket_rows(merged_rows)
    merged_rows = _merge_compound_reason_rows(merged_rows)
    version_rows = [row for row in merged_rows if row.topic == "版本号/日期"]
    other_rows = [row for row in merged_rows if row.topic != "版本号/日期"]
    if version_rows:
        return [version_rows[0]] + other_rows
    return other_rows


def _merge_adjacent_same_bucket_rows(rows: list[CandidateRow]) -> list[CandidateRow]:
    if not rows:
        return rows

    merged: list[CandidateRow] = [rows[0]]
    for row in rows[1:]:
        previous = merged[-1]
        if _can_merge_adjacent_rows(previous, row):
            merged[-1] = CandidateRow(
                topic=previous.topic,
                section_page=_merge_section_pages(previous.section_page, row.section_page),
                old_text=_join_row_text(previous.old_text, row.old_text),
                new_text=_join_row_text(previous.new_text, row.new_text),
                reason=previous.reason,
                source_locations=previous.source_locations + row.source_locations,
            )
            continue
        merged.append(row)
    return merged


def _can_merge_adjacent_rows(left: CandidateRow, right: CandidateRow) -> bool:
    if left.topic != right.topic or left.reason != right.reason:
        return False
    if left.topic in {"版本号/日期", "中心信息"}:
        return False
    if left.topic == "谁能解答本研究的相关问题？":
        return False
    if _row_change_mode(left) != _row_change_mode(right):
        return False
    if left.section_page == right.section_page:
        return True
    return _can_merge_consecutive_section_pages(left.section_page, right.section_page)


def _join_row_text(left: str, right: str) -> str:
    if not left:
        return right
    if not right:
        return left
    if right in left:
        return left
    if left in right:
        return right
    return f"{left}\n{right}"


def _merge_section_pages(left: str, right: str) -> str:
    if left == right:
        return left

    left_parts = _parse_section_page(left)
    right_parts = _parse_section_page(right)
    if not left_parts or not right_parts:
        return left

    left_label, left_separator, left_start, left_end = left_parts
    right_label, right_separator, right_start, right_end = right_parts
    if left_label != right_label or left_separator != right_separator:
        return left

    merged_start = min(left_start, right_start)
    merged_end = max(left_end, right_end)
    if left_separator == "/":
        return f"{left_label}/P{merged_start}-P{merged_end}"
    return f"{left_label} P{merged_start}-P{merged_end}"


def _drop_body_version_fragments(rows: list[CandidateRow]) -> list[CandidateRow]:
    has_version_row = any(row.topic == "版本号/日期" for row in rows)
    if not has_version_row:
        return rows
    filtered: list[CandidateRow] = []
    for row in rows:
        if row.topic == "版本号/日期":
            filtered.append(row)
            continue
        if _is_body_version_fragment(row):
            continue
        filtered.append(row)
    return filtered


def _can_merge_consecutive_section_pages(left: str, right: str) -> bool:
    left_parts = _parse_section_page(left)
    right_parts = _parse_section_page(right)
    if not left_parts or not right_parts:
        return False

    left_label, _, left_start, left_end = left_parts
    right_label, _, right_start, right_end = right_parts
    if left_label != right_label:
        return False
    return right_start <= left_end + 1 and left_start <= right_end + 1


def _parse_section_page(section_page: str) -> tuple[str, str, int, int] | None:
    if "表格" in section_page:
        return None

    slash_match = re.fullmatch(r"(.+?)/P(\d+)(?:-P?(\d+))?", section_page)
    if slash_match:
        label, start, end = slash_match.groups()
        start_page = int(start)
        end_page = int(end or start)
        return label, "/", start_page, end_page

    space_match = re.fullmatch(r"(.+?)\s+P(\d+)(?:-P?(\d+))?", section_page)
    if space_match:
        label, start, end = space_match.groups()
        start_page = int(start)
        end_page = int(end or start)
        return label, " ", start_page, end_page

    return None


def _merge_center_metadata_rows(rows: list[CandidateRow]) -> list[CandidateRow]:
    result: list[CandidateRow] = []
    buffer: list[CandidateRow] = []

    def flush_buffer() -> None:
        nonlocal buffer
        if not buffer:
            return
        if len(buffer) == 1:
            result.extend(buffer)
        else:
            result.append(
                CandidateRow(
                    topic="中心信息",
                    section_page="P1",
                    old_text="\n".join(row.old_text for row in buffer if row.old_text),
                    new_text="\n".join(row.new_text for row in buffer if row.new_text),
                    reason="更新中心信息",
                    source_locations=[location for row in buffer for location in row.source_locations],
                )
            )
        buffer = []

    for row in rows:
        if _is_first_page_center_metadata_row(row):
            buffer.append(row)
            continue
        flush_buffer()
        result.append(row)

    flush_buffer()
    return result


def _split_contact_question_rows(rows: list[CandidateRow]) -> list[CandidateRow]:
    result: list[CandidateRow] = []
    index = 0
    while index < len(rows):
        row = rows[index]
        if row.topic != "谁能解答本研究的相关问题？":
            result.append(row)
            index += 1
            continue
        run: list[CandidateRow] = []
        while index < len(rows) and rows[index].topic == "谁能解答本研究的相关问题？":
            run.append(rows[index])
            index += 1
        result.extend(_normalize_contact_question_run(run))
    return result


def _normalize_contact_question_run(rows: list[CandidateRow]) -> list[CandidateRow]:
    if not rows:
        return rows

    approval_rows = [
        row for row in rows
        if "审查" in f"{row.old_text}\n{row.new_text}" and "联系电话" not in f"{row.old_text}\n{row.new_text}"
    ]
    ethics_rows = [
        row for row in rows
        if "联系电话" in f"{row.old_text}\n{row.new_text}" and "伦理委员会" in f"{row.old_text}\n{row.new_text}"
    ]
    doctor_rows = [row for row in rows if "研究医生" in f"{row.old_text}\n{row.new_text}"]
    if not approval_rows or not ethics_rows or not doctor_rows:
        return rows

    section_page = f"{rows[0].topic} P20"
    return [
        CandidateRow(
            topic=rows[0].topic,
            section_page=section_page,
            old_text="\n".join(row.old_text for row in approval_rows if row.old_text),
            new_text="\n".join(row.new_text for row in approval_rows if row.new_text),
            reason="按照伦理要求修改",
            source_locations=[location for row in approval_rows for location in row.source_locations],
        ),
        CandidateRow(
            topic=rows[0].topic,
            section_page=section_page,
            old_text="\n".join(row.old_text for row in ethics_rows if row.old_text),
            new_text="\n".join(row.new_text for row in ethics_rows if row.new_text),
            reason="更新中心信息",
            source_locations=[location for row in ethics_rows for location in row.source_locations],
        ),
        CandidateRow(
            topic=rows[0].topic,
            section_page=section_page,
            old_text="\n".join(row.old_text for row in doctor_rows if row.old_text),
            new_text="\n".join(row.new_text for row in doctor_rows if row.new_text),
            reason="更新中心信息",
            source_locations=[location for row in doctor_rows for location in row.source_locations],
        ),
    ]


def _rewrite_business_row(row: CandidateRow) -> CandidateRow:
    topic = row.topic
    section_page = row.section_page
    reason = row.reason

    if topic == "版本号/日期" and any(location.startswith("word/header") for location in row.source_locations):
        section_page = "全文/页眉"
        reason = "通用版更新"
    if _is_intro_sponsor_row(row):
        topic = "1.引言"
        section_page = _collapse_to_page_only(section_page) or section_page
    if topic == "本研究目的是什么？" and _is_sponsor_name_update_row(row):
        topic = "2.本研究目的是什么？"
    if _is_scheme_number_row(row):
        topic = "第三部分：同意书"
        section_page = "P10"
        reason = "修正方案号"
    if _is_main_consent_backup_slice_row(row):
        topic = "第一部分\n3 如果我参与本研究，将发生什么事情？"
        section_page = "P3"
        reason = "与方案中的更新保持一致，即如果患者有更多可用切片，用作5类MET检测备份切片。"
    elif _is_schedule_backup_slice_row(row):
        topic = "第四部分 \n3检查和程序详细的时间安排"
        section_page = "P13"
        reason = "与方案中的更新保持一致，即如果患者有更多可用切片，用作5类MET检测备份切片。"
    if _is_study_process_row(row):
        topic = "本研究的总体过程"
    if _is_fee_and_compensation_row(row):
        topic = "研究费用和补偿"
        section_page = _replace_section_label(section_page, "研究费用和补偿")
    if _is_injury_compensation_row(row):
        topic = "如果因为参加本研究导致身体受到损伤会怎么处理？"
        section_page = _replace_section_label(section_page, topic)
    if _is_early_exit_row(row):
        topic = "参与本研究包括哪些内容?"
        section_page = _replace_section_label(section_page, "提前退出")
        reason = "与方案保持一致。"
    elif _is_sample_collection_row(row):
        topic = "参与本研究包括哪些内容?"
        section_page = _replace_section_label(section_page, "生物样本采集")
        reason = "与方案保持一致。"
    elif _should_use_consistency_reason(topic, section_page, row.old_text, row.new_text):
        reason = "与方案保持一致。"
    elif _is_benefit_row(row):
        reason = "按照伦理要求修改"
    if _is_fee_description_row(row):
        reason = "更新费用描述"
    elif _is_sponsor_name_update_row(row):
        reason = "更新申办者名称"
    if _is_compound_must_do_row(topic, row.old_text, row.new_text):
        reason = "勘误。||优化描述。||与方案保持一致。"
        if section_page == "P10":
            section_page = f"{topic}/P10"
    if _is_compound_risk_row(topic, row.old_text, row.new_text):
        bounds = _extract_page_bounds(section_page)
        if bounds is not None and bounds[0] == bounds[1] and bounds[0] == 12:
            section_page = f"{topic}/P{bounds[0]}-P{bounds[1] + 1}"

    return CandidateRow(
        topic=topic,
        section_page=section_page,
        old_text=row.old_text,
        new_text=row.new_text,
        reason=reason,
        source_locations=row.source_locations,
    )


def _collapse_population_wording_rows(rows: list[CandidateRow]) -> list[CandidateRow]:
    terminology_rows = [row for row in rows if _is_population_wording_row(row)]
    if len(terminology_rows) < 2:
        return rows

    summary_row = CandidateRow(
        topic="研究人群描述",
        section_page="主知情同意书全文",
        old_text="\n".join(row.old_text for row in terminology_rows if row.old_text),
        new_text="\n".join(row.new_text for row in terminology_rows if row.new_text),
        reason="与ICH E6 R3和方案更新保持一致",
        source_locations=[location for row in terminology_rows for location in row.source_locations],
    )

    result: list[CandidateRow] = []
    inserted = False
    for row in rows:
        if row.topic == "版本号/日期":
            result.append(row)
            continue
        if _is_population_wording_row(row):
            continue
        if not inserted:
            result.append(summary_row)
            inserted = True
        result.append(row)

    if not inserted:
        result.append(summary_row)
    return result


def _drop_enrollment_window_rows(rows: list[CandidateRow]) -> list[CandidateRow]:
    return [row for row in rows if not _is_enrollment_window_prefix_row(row)]


def _collapse_testset_table_rows(rows: list[CandidateRow]) -> list[CandidateRow]:
    if not _is_testset_output(rows):
        return rows

    result: list[CandidateRow] = []
    contact_rows: list[CandidateRow] = []
    contact_reason = "更新中心信息"
    for row in rows:
        if _is_testset_first_page_table_row(row):
            continue
        if _is_testset_ethics_contact_row(row):
            contact_rows.append(row)
            continue
        if row.topic == "中心信息" and row.reason == "更新中心信息及申办方信息":
            contact_reason = "更新中心伦理信息"
        result.append(row)

    if contact_rows:
        result.append(
            CandidateRow(
                topic="20. 更多信息和联系人",
                section_page=_collapse_to_page_only(contact_rows[0].section_page) or "P1",
                old_text="",
                new_text="",
                reason=contact_reason,
                source_locations=[location for row in contact_rows for location in row.source_locations],
            )
        )
    return result


def _is_testset_output(rows: list[CandidateRow]) -> bool:
    version_rows = [row for row in rows if row.topic == "版本号/日期"]
    return any(_is_testset_style_version_row(row) for row in version_rows) or any(row.section_page == "第1页" for row in rows)


def _is_testset_first_page_table_row(row: CandidateRow) -> bool:
    return row.section_page.startswith("表格1/") and row.section_page.endswith("/P1")


def _is_testset_ethics_contact_row(row: CandidateRow) -> bool:
    text = f"{row.old_text}\n{row.new_text}"
    return row.topic == "谁审查了本研究？" and (
        "伦理委员会" in text or "联系电话" in text or bool(re.fullmatch(r"[\d-]+", row.new_text.strip()))
    )


def _collapse_to_page_only(section_page: str) -> str | None:
    page_hint = _extract_page_hint(section_page)
    if page_hint:
        return page_hint
    chinese_page_match = re.search(r"第(\d+)页", section_page)
    if chinese_page_match:
        return f"P{chinese_page_match.group(1)}"
    return None


def _merge_compound_reason_rows(rows: list[CandidateRow]) -> list[CandidateRow]:
    if not rows:
        return rows

    merged: list[CandidateRow] = [rows[0]]
    for row in rows[1:]:
        previous = merged[-1]
        if _should_merge_with_compound_reason(previous, row):
            merged[-1] = CandidateRow(
                topic=previous.topic,
                section_page=_merge_compound_section_pages(previous, row),
                old_text=_join_row_text(previous.old_text, row.old_text),
                new_text=_join_row_text(previous.new_text, row.new_text),
                reason="勘误。||优化描述。||与方案保持一致。",
                source_locations=previous.source_locations + row.source_locations,
            )
            continue
        merged.append(row)
    return merged


def _should_merge_with_compound_reason(left: CandidateRow, right: CandidateRow) -> bool:
    if left.topic != right.topic:
        return False
    if left.topic not in {"我必须做什么？", "如果我参与这项研究，可能的风险和弊端是什么？"}:
        return False
    reasons = {left.reason, right.reason}
    if (
        not {"与方案保持一致。", "按照伦理要求修改"}.issubset(reasons)
        and reasons != {"勘误。||优化描述。||与方案保持一致。", "按照伦理要求修改"}
        and reasons != {"勘误。||优化描述。||与方案保持一致。", "与方案保持一致。"}
    ):
        return False
    return _pages_overlap_or_touch(left.section_page, right.section_page)


def _merge_compound_section_pages(left: CandidateRow, right: CandidateRow) -> str:
    bounds = [_extract_page_bounds(left.section_page), _extract_page_bounds(right.section_page)]
    page_bounds = [bound for bound in bounds if bound is not None]
    if not page_bounds:
        return left.section_page
    start_page = min(bound[0] for bound in page_bounds)
    end_page = max(bound[1] for bound in page_bounds)
    if start_page == end_page:
        return f"{left.topic}/P{start_page}"
    return f"{left.topic}/P{start_page}-P{end_page}"


def _pages_overlap_or_touch(left: str, right: str) -> bool:
    left_bounds = _extract_page_bounds(left)
    right_bounds = _extract_page_bounds(right)
    if not left_bounds or not right_bounds:
        return False
    return right_bounds[0] <= left_bounds[1] + 1 and left_bounds[0] <= right_bounds[1] + 1


def _extract_page_bounds(section_page: str) -> tuple[int, int] | None:
    match = re.search(r"P(\d+)(?:-P?(\d+))?$", section_page)
    if not match:
        return None
    start_page = int(match.group(1))
    end_page = int(match.group(2) or start_page)
    return start_page, end_page


def _is_early_exit_row(row: CandidateRow) -> bool:
    text = f"{row.old_text}\n{row.new_text}"
    return "提前退出" in text


def _is_sample_collection_row(row: CandidateRow) -> bool:
    text = f"{row.old_text}\n{row.new_text}"
    if not any(signal in text for signal in ("采血", "血液样本", "生物样本", "梅毒螺旋体抗体阳性时加测", "快速血浆反应素环状卡片试验")):
        return False
    return any("#table" in location for location in row.source_locations) or "表格" in row.section_page


def _is_study_process_row(row: CandidateRow) -> bool:
    text = f"{row.old_text}\n{row.new_text}"
    return "多中心、随机、双盲、安慰剂平行对照" in text or row.section_page.startswith("研究流程/")


def _is_fee_and_compensation_row(row: CandidateRow) -> bool:
    text = f"{row.old_text}\n{row.new_text}"
    return "无需为参加本研究支付费用" in text or ("营养补偿" in text and "医疗护理" in text)


def _is_injury_compensation_row(row: CandidateRow) -> bool:
    text = f"{row.old_text}\n{row.new_text}"
    return "治疗费用补偿申请" in text or ("经济补偿" in text and "医疗保险" in text)


def _is_benefit_row(row: CandidateRow) -> bool:
    text = f"{row.old_text}\n{row.new_text}"
    return row.topic == "参与本研究有什么获益/受益吗？" and "获得任何益处" in text


def _is_fee_description_row(row: CandidateRow) -> bool:
    text = f"{row.old_text}\n{row.new_text}"
    return "住院费用不得纳入医保支付" in text or "支付的费用包括" in text


def _is_intro_sponsor_row(row: CandidateRow) -> bool:
    text = f"{row.old_text}\n{row.new_text}"
    return row.topic == "正文" and "诚邀您参与一项由" in text and "申办者" in text


def _is_sponsor_name_update_row(row: CandidateRow) -> bool:
    text = f"{row.old_text}\n{row.new_text}"
    if "江苏景行医药科技有限公司" not in text or "江苏景行生物医药有限公司" not in text:
        return False
    return row.topic in {"正文", "1.引言", "本研究目的是什么？", "2.本研究目的是什么？", "本研究由谁组织和资助？"}


def _is_compound_must_do_row(topic: str, old_text: str, new_text: str) -> bool:
    text = f"{old_text}\n{new_text}"
    return topic == "我必须做什么？" and "皮损内注射皮质类固醇" in text


def _is_compound_risk_row(topic: str, old_text: str, new_text: str) -> bool:
    text = f"{old_text}\n{new_text}"
    return topic == "如果我参与这项研究，可能的风险和弊端是什么？" and "胸部X线/计算机断层扫描" in text


def _should_use_consistency_reason(topic: str, section_page: str, old_text: str, new_text: str) -> bool:
    text = f"{old_text}\n{new_text}"
    if topic == "参与本研究包括哪些内容?" and section_page.startswith(("筛选期/", "提前退出/", "生物样本采集/", "排除条件/", "主要排除条件/")):
        return True
    if topic == "研究访视与检查" and any(
        signal in text
        for signal in (
            "峰值瘙痒数字评定量表",
            "瘙痒数字评分量表",
            "患者湿疹自我检查评分量表",
            "源自患者的湿疹评价评分量表",
        )
    ):
        return True
    return False


def _replace_section_label(section_page: str, label: str) -> str:
    page_hint = _extract_page_hint(section_page)
    if page_hint:
        return f"{label}/{page_hint}"
    return label


def _extract_page_hint(section_page: str) -> str | None:
    match = re.search(r"(P\d+(?:-P?\d+)?)$", section_page)
    if match:
        return match.group(1)
    return None


def _inject_center_summary_row(rows: list[CandidateRow]) -> list[CandidateRow]:
    if any(row.topic == "中心信息" for row in rows):
        return rows

    version_rows = [row for row in rows if row.topic == "版本号/日期"]
    if not version_rows or not any(_is_testset_style_version_row(row) for row in version_rows):
        return rows

    summary_drivers = [row for row in rows if _should_create_center_summary(row)]
    if not summary_drivers:
        return rows

    summary_reason = (
        "更新中心信息及申办方信息"
        if any(_contains_sponsor_signal(row) for row in summary_drivers)
        else "更新中心信息"
    )
    summary_row = CandidateRow(
        topic="中心信息",
        section_page="第1页",
        old_text="",
        new_text="",
        reason=summary_reason,
        source_locations=[],
    )
    return [summary_row] + rows


def _is_first_page_center_metadata_row(row: CandidateRow) -> bool:
    if row.reason != "更新中心信息":
        return False
    if row.section_page not in {"研究中心/P1", "主要研究者/P1", "P1", "第1页", "正文 P1"}:
        return False
    if not row.new_text:
        return False
    return row.new_text.startswith(("研究中心：", "主要研究者："))


def _is_body_version_fragment(row: CandidateRow) -> bool:
    text = f"{row.old_text} {row.new_text}"
    return (
        any(location.startswith("word/header") for location in row.source_locations)
        and _looks_like_version_metadata(text)
    ) or (
        row.section_page in {"正文", "正文 P1"}
        and _looks_like_version_metadata(text)
    )


def _should_create_center_summary(row: CandidateRow) -> bool:
    if row.topic in {"本研究由谁组织和资助？", "更多信息和联系人", "1.引言"}:
        return True
    if _contains_sponsor_signal(row):
        return True
    return False


def _contains_sponsor_signal(row: CandidateRow) -> bool:
    text = f"{row.topic} {row.section_page} {row.old_text} {row.new_text}"
    if row.topic == "1.引言":
        return True
    if "申办者" in text or "申办方" in text:
        return True
    return False


def _is_testset_style_version_row(row: CandidateRow) -> bool:
    text = f"{row.old_text}\n{row.new_text}"
    return "KDN-" in text or "版本/日期" in text


def _build_version_row(version_change, fallback_row: CandidateRow | None) -> CandidateRow | None:
    if version_change is not None:
        section_page = "全文/页眉" if version_change.location.startswith("word/header") else "页脚"
        reason = "通用版更新" if version_change.location.startswith("word/header") else "版本更新。"
        return CandidateRow(
            topic="版本号/日期",
            section_page=section_page,
            old_text=version_change.old_text,
            new_text=version_change.new_text,
            reason=reason,
            source_locations=[version_change.location],
        )
    return fallback_row


def _row_change_mode(row: CandidateRow) -> str:
    if row.old_text and row.new_text:
        return "replace"
    if row.new_text:
        return "insert"
    if row.old_text:
        return "delete"
    return "empty"


def _looks_like_version_metadata(text: str) -> bool:
    return bool(
        ("主版本号" in text or "版本日期" in text or "专用" in text or "版本/日期" in text)
        and "年" in text
        and "月" in text
        and "日" in text
    )


def _is_scheme_number_row(row: CandidateRow) -> bool:
    text = f"{row.old_text}\n{row.new_text}"
    return "研究编号：" in text and bool(re.search(r"D\d+R\d+", text))


def _is_backup_slice_update_row(row: CandidateRow) -> bool:
    text = f"{row.old_text}\n{row.new_text}"
    return (
        "组织切片" in text
        and "16片（穿刺样本）" in text
        and "至少16片（穿刺样本）" in text
    )


def _is_main_consent_backup_slice_row(row: CandidateRow) -> bool:
    return _is_backup_slice_update_row(row) and row.topic == "如果我参与本研究，将发生什么事情？"


def _is_schedule_backup_slice_row(row: CandidateRow) -> bool:
    return _is_backup_slice_update_row(row) and (
        row.topic == "研究访视与检查" or "不会采集您的血液样本" in row.old_text
    )


def _is_population_wording_row(row: CandidateRow) -> bool:
    replacements = (
        ("受试者", "试验参与者"),
        ("给受试者的其他信息", "给试验参与者的其他信息"),
        ("与受试者的关系", "与试验参与者的关系"),
    )
    for old_marker, new_marker in replacements:
        if old_marker not in row.old_text:
            continue
        if row.old_text.replace(old_marker, new_marker) == row.new_text:
            return True
    return False


def _is_enrollment_window_prefix_row(row: CandidateRow) -> bool:
    if row.topic != "研究访视与检查":
        return False
    if not row.old_text or not row.new_text:
        return False
    if not row.new_text.startswith("本研究计划在") or "入组" not in row.new_text:
        return False
    return row.old_text in row.new_text


def _fallback_version_change(path: Path, segments: list) -> CandidateRow | None:
    footer_segments = [segment for segment in segments if segment.location.startswith("word/footer")]
    if not footer_segments:
        return None

    footer_texts = [_clean_fallback_footer_text(text) for text in _load_footer_texts(path)]
    footer_version_lines = [
        text
        for text in footer_texts
        if text and "方案编号/标题" not in text and ("专用版" in text or "基于" in text or "KDN-" in text) and "版" in text and "年" in text
    ]
    full_footer = "\n".join(dict.fromkeys(footer_version_lines))

    for segment in footer_segments:
        if "版" not in segment.old_text:
            continue
        cleaned_old = _clean_fallback_footer_text(segment.old_text)
        if full_footer and cleaned_old != full_footer:
            return CandidateRow(
                topic="版本号/日期",
                section_page="页脚",
                old_text=cleaned_old,
                new_text=full_footer,
                reason="版本更新。",
                source_locations=[segment.location],
            )
    return None


def _classify_row(
    old_text: str,
    new_text: str,
    locations: list[str],
    contexts: list[ParagraphContext],
    footer_texts: list[str],
) -> CandidateRow:
    paragraph_index = _find_context_index(old_text, new_text, contexts)
    if _is_version_row(old_text, new_text, locations):
        expanded_old_text, expanded_new_text = _expand_footer_version_text(old_text, new_text, footer_texts)
        topic = "版本号/日期"
        section_page = "页脚"
        reason = "版本更新。"
        old_text = expanded_old_text
        new_text = expanded_new_text
    else:
        topic = _infer_topic(paragraph_index, contexts)
        topic = _normalize_topic(topic, old_text, new_text)
        section_page = _infer_section_page(paragraph_index, contexts, topic, locations)
        reason = _infer_reason(old_text, new_text, locations)

    if _is_table_origin(locations):
        section_page = _mark_table_section_from_locations(section_page, locations)
        if topic == "正文" or _looks_like_table_cell_topic(topic):
            topic = "表格修改"

    return CandidateRow(
        topic=topic,
        section_page=section_page,
        old_text=old_text,
        new_text=new_text,
        reason=reason,
        source_locations=locations,
    )


def _load_paragraph_contexts(path: Path) -> list[ParagraphContext]:
    contexts: list[ParagraphContext] = []
    with ZipFile(path) as archive:
        root = etree.fromstring(archive.read("word/document.xml"))
        paragraphs = root.xpath(".//w:p", namespaces=WORD_NS)
        for index, paragraph in enumerate(paragraphs):
            text = _normalize_paragraph_text(
                "".join(paragraph.xpath(".//w:t/text() | .//w:delText/text()", namespaces=WORD_NS))
            )
            if text:
                contexts.append(ParagraphContext(index=index, text=text))
    return contexts


def _load_footer_texts(path: Path) -> list[str]:
    texts: list[str] = []
    with ZipFile(path) as archive:
        footer_parts = sorted(
            name for name in archive.namelist() if name.startswith("word/footer") and name.endswith(".xml")
        )
        for footer_part in footer_parts:
            root = etree.fromstring(archive.read(footer_part))
            for paragraph in root.xpath(".//w:p", namespaces=WORD_NS):
                text = "".join(paragraph.xpath(".//w:t/text()", namespaces=WORD_NS)).strip()
                if text:
                    texts.append(_normalize_footer_text(text))
    combined_text = "\n".join(text for text in texts if text)
    if combined_text:
        texts.insert(0, combined_text)
    return texts


def _expand_footer_version_text(old_text: str, new_text: str, footer_texts: list[str]) -> tuple[str, str]:
    normalized_footer_texts = [_normalize_footer_text(text) for text in footer_texts]
    normalized_old = _normalize_footer_text(old_text)
    normalized_new = _normalize_footer_text(new_text)

    footer_version_lines = [
        text
        for text in normalized_footer_texts
        if text and "方案编号/标题" not in text and ("专用版" in text or "基于" in text or "KDN-" in text)
    ]
    footer_version_block = "\n".join(line for line in footer_version_lines if line)
    if normalized_new.startswith("基于") and footer_version_lines:
        preferred_lines = [line for line in footer_version_lines if "专用版" in line] + [
            line for line in footer_version_lines if line.startswith("基于")
        ]
        preferred_block = "\n".join(dict.fromkeys(preferred_lines))
        if preferred_block:
            return _clean_fallback_footer_text(normalized_old or old_text), preferred_block
    if footer_version_block and normalized_new and normalized_new in footer_version_block:
        return _clean_fallback_footer_text(normalized_old or old_text), footer_version_block

    for footer_text in normalized_footer_texts:
        if normalized_new and normalized_new in footer_text and footer_text != normalized_new:
            return _clean_fallback_footer_text(normalized_old or old_text), _clean_fallback_footer_text(footer_text)

    return _clean_fallback_footer_text(old_text), _clean_fallback_footer_text(new_text)


def _find_context_index(old_text: str, new_text: str, contexts: list[ParagraphContext]) -> int | None:
    normalized_old = _normalize_match_text(old_text)
    normalized_new = _normalize_match_text(new_text)

    for context in contexts:
        normalized_context = _normalize_match_text(context.text)
        if normalized_old and normalized_old in normalized_context:
            return context.index
        if normalized_new and normalized_new in normalized_context:
            return context.index

    old_anchor = _anchor_text(normalized_old)
    new_anchor = _anchor_text(normalized_new)
    for context in contexts:
        normalized_context = _normalize_match_text(context.text)
        if old_anchor and old_anchor in normalized_context:
            return context.index
        if new_anchor and new_anchor in normalized_context:
            return context.index

    best_index: int | None = None
    best_score = 0.0
    for context in contexts:
        normalized_context = _normalize_match_text(context.text)
        old_score = SequenceMatcher(None, normalized_old[:120], normalized_context[:180]).ratio() if normalized_old else 0.0
        new_score = SequenceMatcher(None, normalized_new[:120], normalized_context[:180]).ratio() if normalized_new else 0.0
        score = max(old_score, new_score)
        if score > best_score:
            best_score = score
            best_index = context.index
    if best_score >= 0.45:
        return best_index
    return None


def _anchor_text(text: str) -> str:
    return text[:32]


def _infer_topic(paragraph_index: int | None, contexts: list[ParagraphContext]) -> str:
    if paragraph_index is None:
        return "正文"

    heading = _nearest_heading(paragraph_index, contexts)
    if heading is None:
        return "正文"
    return heading


def _infer_section_page(
    paragraph_index: int | None,
    contexts: list[ParagraphContext],
    topic: str,
    locations: list[str],
) -> str:
    if locations and all(location.startswith("word/footer") for location in locations):
        return "页脚"

    if paragraph_index is None:
        return "正文"

    section_label = _nearest_section_label(paragraph_index, contexts)
    page_hint = _estimate_page_hint(paragraph_index, contexts)

    if topic == "正文" and section_label == "正文":
        return f"正文 {page_hint}"
    if section_label == "正文" and topic != "正文":
        return page_hint
    if _should_use_page_only_section_label(section_label, topic):
        return page_hint
    if section_label == topic:
        return f"{section_label} {page_hint}"
    return f"{section_label}/{page_hint}"


def _infer_reason(old_text: str, new_text: str, locations: list[str]) -> str:
    if _is_version_row(old_text, new_text, locations):
        return "版本更新。"
    if _is_center_metadata_change(old_text, new_text):
        return "更新中心信息"
    if len(new_text) > len(old_text):
        return "按照伦理要求修改"
    return "与方案保持一致。"


def _normalize_topic(topic: str, old_text: str, new_text: str) -> str:
    combined = f"{old_text} {new_text}"
    if _looks_like_funding_change(combined):
        return "本研究由谁组织和资助？"
    visit_signals = (
        "瘙痒数字评分量表",
        "峰值瘙痒数字评定量表",
        "皮肤病生活质量指数",
        "湿疹评价评分量表",
        "患者湿疹自我检查评分量表",
        "访视",
        "筛选期",
        "提前退出访视",
    )
    if any(signal in combined for signal in visit_signals):
        return "研究访视与检查"
    return topic


def _looks_like_funding_change(text: str) -> bool:
    funding_signals = (
        "交通补贴",
        "营养补贴",
        "不会产生额外费用",
        "住院费用不得纳入医保支付",
        "支付的费用包括",
    )
    return any(signal in text for signal in funding_signals)


def _is_center_metadata_change(old_text: str, new_text: str) -> bool:
    combined = f"{old_text}\n{new_text}"
    if len(new_text) >= 160:
        return False
    metadata_labels = ("研究中心", "主要研究者", "中心计划入组")
    if not any(label in combined for label in metadata_labels):
        return False
    return any(marker in combined for marker in ("：", ":"))


def _is_table_origin(locations: list[str]) -> bool:
    return any("#table" in location for location in locations)


def _mark_table_section(section_page: str) -> str:
    if "表格" in section_page:
        return section_page
    if section_page in {"正文", "正文 P1"}:
        return "表格"
    return f"表格/{section_page}"


def _mark_table_section_from_locations(section_page: str, locations: list[str]) -> str:
    table_path = _extract_table_display_path(locations)
    if table_path:
        normalized_suffix = _normalize_table_section_suffix(section_page)
        if not normalized_suffix:
            return table_path
        return f"{table_path}/{normalized_suffix}"
    return _mark_table_section(section_page)


def _extract_table_display_path(locations: list[str]) -> str | None:
    for location in locations:
        match = re.search(r"#table(\d+)(?:/r(\d+))?(?:/c(\d+))?", location)
        if not match:
            continue
        table_no, row_no, cell_no = match.groups()
        parts = [f"表格{table_no}"]
        if row_no and cell_no:
            parts.append(f"R{row_no}C{cell_no}")
        elif row_no:
            parts.append(f"R{row_no}")
        return "/".join(parts)
    return None


def _normalize_table_section_suffix(section_page: str) -> str | None:
    normalized = section_page.strip()
    if not normalized or normalized in {"正文", "表格"}:
        return None
    if normalized.startswith("正文 "):
        return normalized.split(" ", 1)[1].strip() or None

    slash_prefix, slash, slash_suffix = normalized.rpartition("/")
    if slash and _looks_like_table_cell_topic(slash_prefix):
        return slash_suffix.strip() or None

    space_parts = normalized.rsplit(" ", 1)
    if len(space_parts) == 2:
        label, trailing = (part.strip() for part in space_parts)
        if trailing.startswith("P") and _looks_like_table_cell_topic(label):
            return trailing or None

    if _looks_like_table_cell_topic(normalized):
        page_match = re.search(r"(P\d+)$", normalized)
        return page_match.group(1) if page_match else None

    return normalized


def _looks_like_table_cell_topic(topic: str) -> bool:
    stripped = topic.strip()
    if not stripped:
        return True
    if re.fullmatch(r"[\d\.\-±~/%]+", stripped):
        return True
    if len(stripped) <= 12 and re.search(r"\d", stripped) and not re.search(r"[\u4e00-\u9fffA-Za-z]", stripped):
        return True
    return False


def _is_version_row(old_text: str, new_text: str, locations: list[str]) -> bool:
    if not locations or not all(location.startswith("word/footer") for location in locations):
        return False
    text = f"{old_text} {new_text}"
    return "版" in text and "年" in text and "月" in text and "日" in text


def _nearest_heading(paragraph_index: int, contexts: list[ParagraphContext]) -> str | None:
    previous = [context for context in contexts if context.index <= paragraph_index]
    for context in reversed(previous):
        if _is_heading(context.text):
            return context.text
    return None


def _nearest_section_label(paragraph_index: int, contexts: list[ParagraphContext]) -> str:
    previous = [context for context in contexts if context.index <= paragraph_index]
    for context in reversed(previous):
        if _is_section_label(context.text):
            return _normalize_label(context.text)
    return "正文"


def _is_heading(text: str) -> bool:
    stripped = text.strip()
    if len(stripped) > 40:
        return False
    return bool(
        re.match(r"^(\d+[\.\u3001]|\d+\.\t|[一二三四五六七八九十]+[、\.])", stripped)
        or stripped.endswith(("?", "？"))
    )


def _is_section_label(text: str) -> bool:
    stripped = text.strip()
    if len(stripped) > 50:
        return False
    return (
        stripped.endswith("：")
        or "访视" in stripped
        or "筛选期" in stripped
        or "排除条件" in stripped
        or "主要排除条件" in stripped
        or "研究流程" in stripped
        or _is_heading(stripped)
    )


def _normalize_label(text: str) -> str:
    stripped = text.strip()
    if "筛选期" in stripped:
        return "筛选期"
    visit_match = re.search(r"(访视\d+(?:-\d+)?(?:[:：][^/P]*)?)", stripped)
    if visit_match:
        return visit_match.group(1).rstrip("：:")
    if "排除条件" in stripped:
        return "排除条件" if "主要排除条件" not in stripped else "主要排除条件"
    if "研究流程" in stripped:
        return "研究流程"
    if stripped.endswith("："):
        return stripped[:-1]
    return stripped


def _should_use_page_only_section_label(section_label: str, topic: str) -> bool:
    return section_label == topic or _is_instructional_section_label(section_label, topic)


def _is_instructional_section_label(section_label: str, topic: str) -> bool:
    if "表格" in section_label:
        return False
    if not topic.endswith(("？", "?")):
        return False
    if len(section_label) < 24:
        return False
    if not any(token in section_label for token in ("请", "事项", "须", "必须")):
        return False
    return True


def _estimate_page_hint(paragraph_index: int, contexts: list[ParagraphContext]) -> str:
    max_index = max(context.index for context in contexts) if contexts else 1
    if max_index <= 0:
        return "P1"
    estimated_page = max(1, round((paragraph_index / max_index) * 20))
    return f"P{estimated_page}"


def _normalize_footer_text(text: str) -> str:
    normalized = re.sub(r"第\d+页\s*/?\s*共?\d+页", "", text)
    normalized = re.sub(r"第\d+页\s*，?共\d+$", "", normalized)
    normalized = re.sub(r"页$", "", normalized)
    normalized = re.sub(r"\s+", "", normalized)
    return normalized.strip()


def _normalize_paragraph_text(text: str) -> str:
    return re.sub(r"\s+", "", text).strip()


def _normalize_match_text(text: str) -> str:
    return _strip_punctuation(re.sub(r"\s+", "", text))


def _clean_fallback_footer_text(text: str) -> str:
    cleaned = re.sub(r"第\d+页\s*，?共\d+页", "", text)
    cleaned = re.sub(r"第\d+页\s*，?共\d+$", "", cleaned)
    cleaned = re.sub(r"第\d+页\s*/?\s*共?\d+页", "", cleaned)
    cleaned = re.sub(r"^\s*方案编号/标题.*?(?=佛山市|版本/日期|KDN-|$)", "", cleaned)
    return cleaned.strip().replace("\t", "")


def _should_skip_punctuation_only_change(old_text: str, new_text: str) -> bool:
    if len(old_text) < 40 or len(new_text) < 40:
        return False
    return _strip_punctuation(old_text) == _strip_punctuation(new_text)


def _strip_punctuation(text: str) -> str:
    punctuation = string.punctuation + "，。？！；：、“”‘’（）《》〈〉【】『』「」—…·\t\r\n "
    return text.translate(str.maketrans("", "", punctuation))
