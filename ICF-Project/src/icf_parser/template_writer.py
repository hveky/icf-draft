from __future__ import annotations

from copy import deepcopy
from difflib import SequenceMatcher
from pathlib import Path
from zipfile import ZipFile

from lxml import etree

from src.icf_parser.delivery_model import ContentBlock
from src.icf_parser.rule_engine import CandidateRow


WORD_NS_URI = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
WORD_NS = {"w": WORD_NS_URI}
XML_NS_URI = "http://www.w3.org/XML/1998/namespace"
GREEN_HEX = "70AD47"
COVER_ROW_MARKERS = ("原版本号/日期", "修订后版本号/日期")
REVISION_HEADER_MARKERS = ("主题", "修订章节/页码", "原文", "修订后内容", "更改原因")


class TemplateContractError(ValueError):
    pass


def write_amendment_history(template_path: Path, output_path: Path, rows: list[CandidateRow]) -> Path:
    template_file = Path(template_path)
    destination = Path(output_path)
    if not template_file.exists():
        raise FileNotFoundError(template_file)

    with ZipFile(template_file) as archive:
        document_xml = archive.read("word/document.xml")

    root = etree.fromstring(document_xml)
    cover_row = _locate_cover_contract(root)
    revision_table, template_row = _locate_revision_contract(root)

    _fill_cover_row(cover_row, rows)
    _fill_revision_table(revision_table, template_row, rows)

    payload = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
    destination.parent.mkdir(parents=True, exist_ok=True)
    _write_docx_with_document_xml(template_file, destination, payload)
    return destination


def _locate_cover_contract(root):
    for table in root.xpath(".//w:tbl", namespaces=WORD_NS):
        for row in table.xpath("./w:tr", namespaces=WORD_NS):
            normalized = [_normalize_text(_xml_cell_text(cell)) for cell in _direct_cells(row)]
            if all(any(marker in text for text in normalized) for marker in COVER_ROW_MARKERS):
                return row
    raise TemplateContractError("unsupported template: cover version row not found")


def _locate_revision_contract(root):
    for table in root.xpath(".//w:tbl", namespaces=WORD_NS):
        rows = table.xpath("./w:tr", namespaces=WORD_NS)
        for index, row in enumerate(rows):
            normalized = [_normalize_text(_xml_cell_text(cell)) for cell in _direct_cells(row)]
            if len(normalized) < len(REVISION_HEADER_MARKERS):
                continue
            if all(marker in normalized[pos] for pos, marker in enumerate(REVISION_HEADER_MARKERS)):
                if len(rows) <= index + 1:
                    raise TemplateContractError("unsupported template: missing revision template row")
                return table, rows[index + 1]
    raise TemplateContractError("unsupported template: revision table not found")


def _fill_cover_row(table_row, rows: list[CandidateRow]) -> None:
    version_row = next((row for row in rows if row.topic == "版本号/日期"), None)
    if version_row is None:
        return
    cells = _direct_cells(table_row)
    if len(cells) < 4:
        raise TemplateContractError("unsupported template: cover row shape changed")
    _set_plain_text(cells[1], version_row.old_text)
    _set_plain_text(cells[3], version_row.new_text)


def _fill_revision_table(table, template_row, rows: list[CandidateRow]) -> None:
    template_source = deepcopy(template_row)
    table_rows = list(table.xpath("./w:tr", namespaces=WORD_NS))
    template_row_position = next(index for index, row in enumerate(table_rows) if row is template_row)
    for row in table_rows[template_row_position:]:
        table.remove(row)

    rows_to_write = rows or []
    if not rows_to_write:
        blank_row = deepcopy(template_source)
        table.append(blank_row)
        _clear_revision_row(blank_row)
        return

    for row in rows_to_write:
        target_row = deepcopy(template_source)
        table.append(target_row)
        _write_row(target_row, row)


def _write_row(table_row, row: CandidateRow) -> None:
    cells = _direct_cells(table_row)
    if len(cells) < 5:
        raise TemplateContractError("unsupported template: revision row shape changed")

    use_explicit_delivery_style = getattr(row, "origin", "draft") != "draft"
    for cell in cells:
        _clear_cell_shading(cell)
    _set_plain_text(cells[0], row.topic)
    _set_plain_text(cells[1], row.section_page)

    old_blocks = _content_blocks(row, "old")
    new_blocks = _content_blocks(row, "new")
    if old_blocks or new_blocks:
        _set_content_blocks(cells[2], old_blocks or (ContentBlock(kind="text", text=row.old_text),))
        _set_content_blocks(cells[3], new_blocks or (ContentBlock(kind="text", text=row.new_text),))
    else:
        _set_diff_text(
            cells[2],
            cells[3],
            row.old_text,
            row.new_text,
            force_old_strike=getattr(row, "old_has_strike", None) if use_explicit_delivery_style else None,
            force_new_highlight=getattr(row, "new_has_highlight", None) if use_explicit_delivery_style else None,
        )
    _set_plain_text(cells[4], row.reason)


def _clear_revision_row(table_row) -> None:
    for cell in _direct_cells(table_row):
        _clear_cell_shading(cell)
        _set_plain_text(cell, "")


def _set_plain_text(cell, text: str) -> None:
    _replace_cell_content(cell, [ContentBlock(kind="text", text=text)])


def _set_content_blocks(cell, blocks: tuple[ContentBlock, ...]) -> None:
    _replace_cell_content(cell, blocks)


def _set_diff_text(
    old_cell,
    new_cell,
    old_text: str,
    new_text: str,
    *,
    force_old_strike: bool | None = None,
    force_new_highlight: bool | None = None,
) -> None:
    if force_old_strike is not None or force_new_highlight is not None:
        _replace_cell_content(old_cell, [ContentBlock(kind="text", text=old_text, strike=bool(force_old_strike))])
        _replace_cell_content(
            new_cell,
            [
                ContentBlock(
                    kind="text",
                    text=new_text,
                    bold=bool(force_new_highlight),
                    color=GREEN_HEX if force_new_highlight else None,
                )
            ],
        )
        return

    old_segments, new_segments = _build_diff_segments(old_text, new_text)
    _replace_cell_content(
        old_cell,
        tuple(ContentBlock(kind="text", text=text, strike=is_changed) for text, is_changed in old_segments),
    )
    _replace_cell_content(
        new_cell,
        tuple(
            ContentBlock(kind="text", text=text, bold=is_changed, color=GREEN_HEX if is_changed else None)
            for text, is_changed in new_segments
        ),
    )


def _replace_cell_content(cell, blocks) -> None:
    tc_pr = cell.find("w:tcPr", namespaces=WORD_NS)
    paragraph_properties = _first_paragraph_properties(cell)
    for child in list(cell):
        if child is tc_pr:
            continue
        cell.remove(child)

    appended = False
    active_paragraph = None
    for block in blocks:
        if block.kind == "text":
            if not block.text:
                continue
            if active_paragraph is None:
                active_paragraph = _new_paragraph(paragraph_properties)
                cell.append(active_paragraph)
            _append_text_run(
                active_paragraph,
                block.text,
                strike=block.strike,
                bold=block.bold,
                color=block.color,
            )
            appended = True
            continue
        if block.kind == "table_xml":
            active_paragraph = None
            table = _parse_table_block(block)
            cell.append(table)
            appended = True
            continue
        raise TemplateContractError(f"unsupported content block kind: {block.kind}")

    if not appended or etree.QName(cell[-1]).localname == "tbl":
        cell.append(_new_paragraph(paragraph_properties))


def _parse_table_block(block: ContentBlock):
    try:
        table = etree.fromstring(block.xml.encode("utf-8") if isinstance(block.xml, str) else block.xml)
    except (TypeError, etree.XMLSyntaxError) as exc:
        raise TemplateContractError("invalid table_xml content block") from exc
    if etree.QName(table).localname != "tbl":
        raise TemplateContractError("table_xml content block must contain a w:tbl element")
    return table


def _new_paragraph(paragraph_properties=None):
    paragraph = etree.Element(_qn("p"))
    if paragraph_properties is not None:
        paragraph.append(deepcopy(paragraph_properties))
    return paragraph


def _append_text_run(paragraph, text: str, *, strike: bool = False, bold: bool = False, color: str | None = None) -> None:
    chunks = text.split("\n")
    for index, chunk in enumerate(chunks):
        if index:
            break_run = etree.SubElement(paragraph, _qn("r"))
            etree.SubElement(break_run, _qn("br"))
        if not chunk:
            continue
        run = etree.SubElement(paragraph, _qn("r"))
        run_properties = _run_properties(strike=strike, bold=bold, color=color)
        if run_properties is not None:
            run.append(run_properties)
        text_element = etree.SubElement(run, _qn("t"))
        if chunk[:1].isspace() or chunk[-1:].isspace():
            text_element.set(f"{{{XML_NS_URI}}}space", "preserve")
        text_element.text = chunk


def _run_properties(*, strike: bool, bold: bool, color: str | None):
    if not strike and not bold and not color:
        return None
    run_properties = etree.Element(_qn("rPr"))
    if bold:
        etree.SubElement(run_properties, _qn("b"))
        etree.SubElement(run_properties, _qn("bCs"))
    if strike:
        etree.SubElement(run_properties, _qn("strike"))
    if color:
        color_element = etree.SubElement(run_properties, _qn("color"))
        color_element.set(_qn("val"), color)
    return run_properties


def _build_diff_segments(old_text: str, new_text: str) -> tuple[list[tuple[str, bool]], list[tuple[str, bool]]]:
    matcher = SequenceMatcher(a=old_text, b=new_text)
    old_segments: list[tuple[str, bool]] = []
    new_segments: list[tuple[str, bool]] = []

    for tag, old_start, old_end, new_start, new_end in matcher.get_opcodes():
        if tag == "equal":
            old_segments.append((old_text[old_start:old_end], False))
            new_segments.append((new_text[new_start:new_end], False))
            continue
        if tag in {"replace", "delete"}:
            old_segments.append((old_text[old_start:old_end], True))
        if tag in {"replace", "insert"}:
            new_segments.append((new_text[new_start:new_end], True))

    return _collapse_segments(old_segments), _collapse_segments(new_segments)


def _collapse_segments(segments: list[tuple[str, bool]]) -> list[tuple[str, bool]]:
    collapsed: list[tuple[str, bool]] = []
    for text, is_changed in segments:
        if not text:
            continue
        if collapsed and collapsed[-1][1] == is_changed:
            previous_text, previous_changed = collapsed[-1]
            collapsed[-1] = (previous_text + text, previous_changed)
            continue
        collapsed.append((text, is_changed))
    return collapsed


def _content_blocks(row: CandidateRow, field_name: str) -> tuple[ContentBlock, ...]:
    blocks = getattr(row, f"{field_name}_content_blocks", ())
    return tuple(blocks or ())


def _clear_cell_shading(cell) -> None:
    tc_pr = cell.find("w:tcPr", namespaces=WORD_NS)
    if tc_pr is None:
        return
    for child in list(tc_pr):
        if etree.QName(child).localname == "shd":
            tc_pr.remove(child)


def _first_paragraph_properties(cell):
    paragraph = cell.find("w:p", namespaces=WORD_NS)
    if paragraph is None:
        return None
    paragraph_properties = paragraph.find("w:pPr", namespaces=WORD_NS)
    return deepcopy(paragraph_properties) if paragraph_properties is not None else None


def _direct_cells(row):
    return row.xpath("./w:tc", namespaces=WORD_NS)


def _xml_cell_text(cell) -> str:
    content = "".join(cell.xpath(".//w:t/text() | .//w:delText/text()", namespaces=WORD_NS))
    return content


def _write_docx_with_document_xml(template_path: Path, output_path: Path, document_xml: bytes) -> None:
    with ZipFile(template_path) as template_archive, ZipFile(output_path, "w") as output_archive:
        for item in template_archive.infolist():
            payload = document_xml if item.filename == "word/document.xml" else template_archive.read(item.filename)
            output_archive.writestr(item, payload)


def _normalize_text(text: str) -> str:
    return "".join(text.split())


def _qn(local_name: str) -> str:
    return f"{{{WORD_NS_URI}}}{local_name}"
