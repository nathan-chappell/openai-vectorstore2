from __future__ import annotations

from typing import Annotated, Literal, TypeAlias

from pydantic import BaseModel, Field, model_validator

from backend.app.schemas.records import LibrarySourceSummary, TaskSummary


class ReportCitation(BaseModel):
    label: str = Field(min_length=1, max_length=80)
    source_id: str | None = Field(default=None, max_length=128)
    url: str | None = Field(default=None, max_length=2048)
    note: str | None = Field(default=None, max_length=512)


class ReportParagraphBlock(BaseModel):
    kind: Literal["paragraph"] = "paragraph"
    text: str = Field(min_length=1)
    citations: list[ReportCitation] = Field(default_factory=list, max_length=16)


class ReportListBlock(BaseModel):
    kind: Literal["list"] = "list"
    ordered: bool = False
    items: list[str] = Field(min_length=1)


class ReportTableBlock(BaseModel):
    kind: Literal["table"] = "table"
    headers: list[str] = Field(min_length=1)
    rows: list[list[str]] = Field(min_length=1)
    caption: str | None = Field(default=None, max_length=512)

    @model_validator(mode="after")
    def validate_row_widths(self) -> "ReportTableBlock":
        expected_width = len(self.headers)
        if any(len(row) != expected_width for row in self.rows):
            raise ValueError("Report table rows must match the header width.")
        return self


class ReportMathBlock(BaseModel):
    kind: Literal["math"] = "math"
    expression: str = Field(min_length=1)
    display: bool = True
    label: str | None = Field(default=None, max_length=120)


class ReportFigureBlock(BaseModel):
    kind: Literal["figure"] = "figure"
    alt_text: str = Field(min_length=1, max_length=512)
    uri: str = Field(min_length=1, max_length=2048)
    caption: str | None = Field(default=None, max_length=512)


ReportBlock: TypeAlias = Annotated[
    ReportParagraphBlock | ReportListBlock | ReportTableBlock | ReportMathBlock | ReportFigureBlock,
    Field(discriminator="kind"),
]


class ReportSection(BaseModel):
    title: str = Field(min_length=1, max_length=240)
    blocks: list[ReportBlock] = Field(default_factory=list)
    subsections: list["ReportSection"] = Field(default_factory=list)


class ReportDocument(BaseModel):
    title: str = Field(min_length=1, max_length=240)
    subtitle: str | None = Field(default=None, max_length=512)
    abstract: str | None = Field(default=None, max_length=4096)
    sections: list[ReportSection] = Field(default_factory=list)
    citations: list[ReportCitation] = Field(default_factory=list)


class ReportMarkdownSaveRequest(BaseModel):
    document: ReportDocument
    filename: str | None = Field(default=None, min_length=1, max_length=255)
    folder_id: str | None = None
    tag_ids: list[str] = Field(default_factory=list, max_length=8)
    user_guidance: str | None = Field(default=None, max_length=2048)


class ReportMarkdownSaveResponse(BaseModel):
    markdown: str
    source: LibrarySourceSummary
    task: TaskSummary | None = None


ReportSection.model_rebuild()
