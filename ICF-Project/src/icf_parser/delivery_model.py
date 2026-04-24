from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from zipfile import ZipFile

from lxml import etree

from src.icf_parser.rule_engine import CandidateRow


WORD_NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}


@dataclass(frozen=True)
class ContentBlock:
    kind: str
    text: str = ""
    xml: str = ""
    strike: bool = False
    bold: bool = False
    color: str | None = None


@dataclass(frozen=True)
class RevisionFact:
    topic: str
    section_page: str
    old_text: str
    new_text: str
    reason: str
    source_locations: tuple[str, ...]


@dataclass(frozen=True)
class DeliveryRow:
    topic: str
    section_page: str
    old_text: str
    new_text: str
    reason: str
    old_has_strike: bool = False
    new_has_highlight: bool = False
    old_content_blocks: tuple[ContentBlock, ...] = ()
    new_content_blocks: tuple[ContentBlock, ...] = ()
    source_locations: tuple[str, ...] = ()
    origin: str = "draft"


def revision_facts_from_candidate_rows(rows: list[CandidateRow]) -> list[RevisionFact]:
    return [
        RevisionFact(
            topic=row.topic,
            section_page=row.section_page,
            old_text=row.old_text,
            new_text=row.new_text,
            reason=row.reason,
            source_locations=tuple(row.source_locations),
        )
        for row in rows
    ]


def delivery_rows_from_revision_facts(
    revision_facts: list[RevisionFact],
    *,
    source_docx: Path | None = None,
) -> list[DeliveryRow]:
    return [_delivery_row_from_revision_fact(fact, source_docx=source_docx) for fact in revision_facts]


def _delivery_row_from_revision_fact(fact: RevisionFact, *, source_docx: Path | None) -> DeliveryRow:
    old_content_blocks: tuple[ContentBlock, ...] = ()
    new_content_blocks: tuple[ContentBlock, ...] = ()
    if source_docx is not None:
        old_content_blocks, new_content_blocks = _table_content_blocks_from_source(fact, Path(source_docx))

    return DeliveryRow(
        topic=fact.topic,
        section_page=fact.section_page,
        old_text=fact.old_text,
        new_text=fact.new_text,
        reason=fact.reason,
        old_content_blocks=old_content_blocks,
        new_content_blocks=new_content_blocks,
        source_locations=fact.source_locations,
        origin="draft",
    )


def _table_content_blocks_from_source(
    fact: RevisionFact,
    source_docx: Path,
) -> tuple[tuple[ContentBlock, ...], tuple[ContentBlock, ...]]:
    table_numbers = _table_numbers_from_locations(fact.source_locations)
    if not table_numbers:
        return (), ()

    old_blocks: list[ContentBlock] = []
    new_blocks: list[ContentBlock] = []
    variants = _load_table_variants(source_docx, table_numbers)
    for old_xml, new_xml in variants:
        old_blocks.append(ContentBlock(kind="table_xml", xml=old_xml))
        new_blocks.append(ContentBlock(kind="table_xml", xml=new_xml))
    return tuple(old_blocks), tuple(new_blocks)


def _table_numbers_from_locations(locations: tuple[str, ...]) -> tuple[int, ...]:
    numbers: list[int] = []
    for location in locations:
        for match in re.finditer(r"#table(\d+)", location):
            table_number = int(match.group(1))
            if table_number not in numbers:
                numbers.append(table_number)
    return tuple(numbers)


def _load_table_variants(source_docx: Path, table_numbers: tuple[int, ...]) -> list[tuple[str, str]]:
    if not source_docx.exists():
        return []
    with ZipFile(source_docx) as archive:
        root = etree.fromstring(archive.read("word/document.xml"))
    tables = root.xpath(".//w:tbl", namespaces=WORD_NS)

    variants: list[tuple[str, str]] = []
    for table_number in table_numbers:
        if table_number < 1 or table_number > len(tables):
            continue
        table = tables[table_number - 1]
        variants.append(
            (
                _serialize_table_variant(table, mode="old"),
                _serialize_table_variant(table, mode="new"),
            )
        )
    return variants


def _serialize_table_variant(table, *, mode: str) -> str:
    clone = etree.fromstring(etree.tostring(table))
    if mode == "old":
        _remove_change_wrappers(clone, remove_tag="ins", keep_tag="del")
    else:
        _remove_change_wrappers(clone, remove_tag="del", keep_tag="ins")
    return etree.tostring(clone, encoding="unicode")


def _remove_change_wrappers(root, *, remove_tag: str, keep_tag: str) -> None:
    for node in list(root.xpath(f".//w:{remove_tag}", namespaces=WORD_NS)):
        parent = node.getparent()
        if parent is not None:
            parent.remove(node)

    for node in list(root.xpath(f".//w:{keep_tag}", namespaces=WORD_NS)):
        if keep_tag == "del":
            for deleted_text in node.xpath(".//w:delText", namespaces=WORD_NS):
                deleted_text.tag = f"{{{WORD_NS['w']}}}t"
        parent = node.getparent()
        if parent is None:
            continue
        index = parent.index(node)
        for child in list(node):
            node.remove(child)
            parent.insert(index, child)
            index += 1
        parent.remove(node)
