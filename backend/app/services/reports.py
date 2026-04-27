from __future__ import annotations

from backend.app.schemas.reports import (
    ReportBlock,
    ReportCitation,
    ReportDocument,
    ReportListBlock,
    ReportMathBlock,
    ReportParagraphBlock,
    ReportSection,
    ReportTableBlock,
)


def render_report_markdown(report: ReportDocument) -> str:
    lines: list[str] = [f"# {report.title.strip()}"]
    if report.subtitle:
        lines.extend(["", report.subtitle.strip()])
    if report.abstract:
        lines.extend(["", "## Abstract", "", report.abstract.strip()])
    for section in report.sections:
        lines.extend(["", *_render_section(section, level=2)])
    if report.citations:
        lines.extend(["", "## References", ""])
        for citation in report.citations:
            lines.append(f"- {_render_citation(citation)}")
    return "\n".join(lines).strip() + "\n"


def _render_section(section: ReportSection, *, level: int) -> list[str]:
    heading_level = min(max(level, 1), 6)
    lines: list[str] = [f"{'#' * heading_level} {section.title.strip()}"]
    for block in section.blocks:
        lines.extend(["", *_render_block(block)])
    for subsection in section.subsections:
        lines.extend(["", *_render_section(subsection, level=heading_level + 1)])
    return lines


def _render_block(block: ReportBlock) -> list[str]:
    if isinstance(block, ReportParagraphBlock):
        text = block.text.strip()
        if block.citations:
            text = f"{text} {' '.join(_render_citation_marker(citation) for citation in block.citations)}"
        return [text]
    if isinstance(block, ReportListBlock):
        if block.ordered:
            return [f"{index}. {item.strip()}" for index, item in enumerate(block.items, start=1)]
        return [f"- {item.strip()}" for item in block.items]
    if isinstance(block, ReportTableBlock):
        return _render_table(block)
    if isinstance(block, ReportMathBlock):
        expression = block.expression.strip()
        if block.display:
            lines = ["$$", expression, "$$"]
        else:
            lines = [f"${expression}$"]
        if block.label:
            lines.append(f"<!-- math-label: {block.label.strip()} -->")
        return lines
    lines = [f"![{_escape_brackets(block.alt_text.strip())}]({block.uri.strip()})"]
    if block.caption:
        lines.append(f"*{block.caption.strip()}*")
    return lines


def _render_table(block: ReportTableBlock) -> list[str]:
    header = "| " + " | ".join(_escape_table_cell(value) for value in block.headers) + " |"
    divider = "| " + " | ".join("---" for _value in block.headers) + " |"
    rows = ["| " + " | ".join(_escape_table_cell(value) for value in row) + " |" for row in block.rows]
    if block.caption:
        return [f"*{block.caption.strip()}*", "", header, divider, *rows]
    return [header, divider, *rows]


def _render_citation_marker(citation: ReportCitation) -> str:
    target = citation.url or (f"chatkit-link://source?source_id={citation.source_id}" if citation.source_id else "")
    return f"[{citation.label}]({target})" if target else f"[{citation.label}]"


def _render_citation(citation: ReportCitation) -> str:
    marker = _render_citation_marker(citation)
    return f"{marker}: {citation.note.strip()}" if citation.note else marker


def _escape_table_cell(value: str) -> str:
    return value.strip().replace("\\", "\\\\").replace("|", "\\|").replace("\n", "<br>")


def _escape_brackets(value: str) -> str:
    return value.replace("[", "\\[").replace("]", "\\]")
