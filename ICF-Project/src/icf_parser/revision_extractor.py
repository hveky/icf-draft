from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from zipfile import ZipFile

from lxml import etree


WORD_NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}


@dataclass(frozen=True)
class RevisionScan:
    has_tracked_revisions: bool
    insertions: int
    deletions: int


@dataclass(frozen=True)
class RevisionSegment:
    location: str
    old_text: str
    new_text: str


def scan_tracked_revisions(path: Path) -> RevisionScan:
    package_path = Path(path)
    if not package_path.exists():
        raise FileNotFoundError(package_path)

    insertions = 0
    deletions = 0

    with ZipFile(package_path) as archive:
        for part_name in _iter_word_xml_parts(archive):
            root = etree.fromstring(archive.read(part_name))
            insertions += len(root.xpath(".//w:ins", namespaces=WORD_NS))
            deletions += len(root.xpath(".//w:del", namespaces=WORD_NS))

    return RevisionScan(
        has_tracked_revisions=bool(insertions or deletions),
        insertions=insertions,
        deletions=deletions,
    )


def extract_version_change(path: Path) -> RevisionSegment | None:
    structured = _extract_structured_version_change(path)
    segmented = _extract_segment_version_change(path)
    return _select_preferred_version_change(structured, segmented)


def _extract_structured_version_change(path: Path) -> RevisionSegment | None:
    package_path = Path(path)
    candidates: list[RevisionSegment] = []
    with ZipFile(package_path) as archive:
        version_parts = sorted(
            name
            for name in archive.namelist()
            if name.endswith(".xml") and (name.startswith("word/footer") or name.startswith("word/header"))
        )
        for part_name in version_parts:
            root = etree.fromstring(archive.read(part_name))
            old_lines: list[str] = []
            new_lines: list[str] = []
            has_revision = False

            for paragraph in root.xpath(".//w:p", namespaces=WORD_NS):
                old_text = _normalize_text(_paragraph_revision_text(paragraph, mode="old"))
                new_text = _normalize_text(_paragraph_revision_text(paragraph, mode="new"))
                if not old_text and not new_text:
                    continue
                if old_text != new_text:
                    has_revision = True
                else:
                    continue
                if not _is_version_candidate_line(old_text, new_text):
                    continue
                if old_text:
                    old_lines.append(_strip_footer_page_tail(old_text))
                if new_text:
                    new_lines.append(_strip_footer_page_tail(new_text))

            if not has_revision or not old_lines or not new_lines:
                continue

            old_version = _normalize_version_block("\n".join(old_lines))
            new_version = _normalize_version_block("\n".join(new_lines))
            if _looks_like_version_block(old_version) and _looks_like_version_block(new_version):
                candidates.append(
                    RevisionSegment(
                        location=f"{part_name}#version",
                        old_text=old_version,
                        new_text=new_version,
                    )
                )
    if not candidates:
        return None
    candidates.sort(key=lambda item: (_version_candidate_score(item), len(item.new_text)), reverse=True)
    return candidates[0]


def _extract_segment_version_change(path: Path) -> RevisionSegment | None:
    candidates: list[RevisionSegment] = []
    for segment in extract_revision_segments(path):
        old_text = _normalize_version_candidate_text(segment.old_text)
        new_text = _normalize_version_candidate_text(segment.new_text)
        if not _is_version_candidate_line(old_text, new_text):
            continue
        if not _looks_like_version_block(old_text) or not _looks_like_version_block(new_text):
            continue
        candidates.append(
            RevisionSegment(
                location=segment.location,
                old_text=old_text,
                new_text=new_text,
            )
        )

    if not candidates:
        return None
    candidates.sort(key=lambda item: (_version_candidate_score(item), len(item.new_text)), reverse=True)
    return candidates[0]


def _select_preferred_version_change(
    structured: RevisionSegment | None,
    segmented: RevisionSegment | None,
) -> RevisionSegment | None:
    if structured is not None and structured.location.startswith("word/header"):
        return structured
    if structured is not None and segmented is not None:
        if structured.new_text.count("\n") > segmented.new_text.count("\n"):
            return structured
    if segmented is not None and segmented.location.startswith("word/footer"):
        return segmented
    if structured is not None:
        return structured
    return segmented


def extract_revision_segments(path: Path) -> list[RevisionSegment]:
    package_path = Path(path)
    if not package_path.exists():
        raise FileNotFoundError(package_path)

    segments: list[RevisionSegment] = []

    with ZipFile(package_path) as archive:
        for part_name in _iter_word_xml_parts(archive):
            root = etree.fromstring(archive.read(part_name))
            for index, paragraph in enumerate(root.xpath(".//w:p[w:ins or w:del]", namespaces=WORD_NS), start=1):
                old_text = _normalize_text(_paragraph_revision_text(paragraph, mode="old"))
                new_text = _normalize_text(_paragraph_revision_text(paragraph, mode="new"))
                if (not old_text and not new_text) or old_text == new_text:
                    continue
                segments.append(
                    RevisionSegment(
                        location=_build_paragraph_location(part_name, paragraph, index),
                        old_text=old_text,
                        new_text=new_text,
                    )
                )

    return segments


def _iter_word_xml_parts(archive: ZipFile) -> list[str]:
    return sorted(
        name
        for name in archive.namelist()
        if name.startswith("word/") and name.endswith(".xml") and any(
            marker in name for marker in ("document", "header", "footer")
        )
    )


def _paragraph_revision_text(paragraph, mode: str) -> str:
    parts: list[str] = []
    for child in paragraph.iterchildren():
        local_name = etree.QName(child).localname
        if local_name == "r":
            parts.append(_collect_run_text(child, deleted=False))
            continue
        if local_name == "ins" and mode == "new":
            parts.append(_collect_nested_text(child, deleted=False))
            continue
        if local_name == "del" and mode == "old":
            parts.append(_collect_nested_text(child, deleted=True))
            continue
    return "".join(parts)


def _collect_nested_text(node, deleted: bool) -> str:
    texts: list[str] = []
    for item in node.xpath(".//w:r", namespaces=WORD_NS):
        texts.append(_collect_run_text(item, deleted=deleted))
    return "".join(texts)


def _collect_run_text(run, deleted: bool) -> str:
    if deleted:
        text_nodes = run.xpath(".//w:delText/text()", namespaces=WORD_NS)
    else:
        text_nodes = run.xpath(".//w:t/text()", namespaces=WORD_NS)
    return "".join(text_nodes)


def _normalize_text(text: str) -> str:
    collapsed = " ".join(text.replace("\u00a0", " ").split())
    return collapsed.strip()


def _build_paragraph_location(part_name: str, paragraph, index: int) -> str:
    table_ancestors = paragraph.xpath("ancestor::w:tbl", namespaces=WORD_NS)
    if not table_ancestors:
        return f"{part_name}#p{index}"
    path_parts: list[str] = []
    for table in reversed(table_ancestors):
        row = paragraph.xpath("ancestor::w:tr[ancestor::w:tbl[1] = $table][1]", namespaces=WORD_NS, table=table)
        cell = paragraph.xpath("ancestor::w:tc[ancestor::w:tbl[1] = $table][1]", namespaces=WORD_NS, table=table)
        row_index = _index_within_parent(table.xpath("./w:tr", namespaces=WORD_NS), row[0]) if row else None
        cell_index = _index_within_parent(row[0].xpath("./w:tc", namespaces=WORD_NS), cell[0]) if row and cell else None
        table_index = _global_table_index(table)
        part = f"table{table_index}"
        if row_index is not None:
            part += f"/r{row_index}"
        if cell_index is not None:
            part += f"/c{cell_index}"
        path_parts.append(part)
    return f"{part_name}#{'/'.join(path_parts)}/p{index}"


def _index_within_parent(nodes, target) -> int | None:
    for idx, node in enumerate(nodes, start=1):
        if node is target:
            return idx
    return None


def _global_table_index(table) -> int:
    root = table.getroottree().getroot()
    all_tables = root.xpath(".//w:tbl", namespaces=WORD_NS)
    for idx, candidate in enumerate(all_tables, start=1):
        if candidate is table:
            return idx
    return 1


def _looks_like_version_block(text: str) -> bool:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return False
    return all(_looks_like_version_line(line) for line in lines)


def _looks_like_version_line(line: str) -> bool:
    if "版" in line and "年" in line and "月" in line and "日" in line:
        return True
    return bool(re.search(r"V\d+\.\d+/\d{4}-\d{2}-\d{2}", line))


def _normalize_version_candidate_text(text: str) -> str:
    cleaned = _strip_footer_page_tail(text)
    lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
    if len(lines) != 1:
        return "\n".join(lines)

    line = lines[0]
    tail = line.split("：", 1)[1].strip() if "：" in line and "知情同意书" in line else line
    match = re.search(r"([\u4e00-\u9fffA-Za-z].*?知情同意书[_\s]*V\d+\.\d+\s*/\s*\d{4}年\d{1,2}月\d{1,2}日)", tail)
    if match:
        return match.group(1).strip()
    return line


def _normalize_version_block(text: str) -> str:
    lines = [_strip_footer_page_tail(line) for line in text.splitlines() if line.strip()]
    if len(lines) == 1:
        return _normalize_version_candidate_text(lines[0])
    return "\n".join(lines)


def _strip_footer_page_tail(text: str) -> str:
    cleaned = text
    for pattern in (
        r"第\d+页\s*[，,]?\s*共\d+页?$",
        r"第\d+\s*/\s*共?\d+\s*页$",
        r"第?\d+\s*/?\s*共?\d+\s*页$",
        r"页$",
    ):
        cleaned = re.sub(pattern, "", cleaned).strip()
    return cleaned


def _is_version_candidate_line(old_text: str, new_text: str) -> bool:
    combined = f"{old_text} {new_text}"
    if "方案编号" in combined or "标题" in combined:
        return False
    if "年" not in combined or "月" not in combined or "日" not in combined:
        return False
    return (
        _looks_like_version_line(old_text)
        or _looks_like_version_line(new_text)
        or any(token in combined for token in ("版本", "日期", "专用版", "基于", "KDN-", "ICF"))
    )


def _version_candidate_score(segment: RevisionSegment) -> tuple[int, int]:
    text = f"{segment.old_text}\n{segment.new_text}"
    return (
        int("专用版" in text) + int("基于" in text) + int("KDN-" in text or "ICF" in text),
        text.count("\n"),
    )
