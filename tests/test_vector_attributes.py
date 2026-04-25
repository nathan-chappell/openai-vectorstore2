from __future__ import annotations

from typing import Any, cast

from backend.app.services.sources import (
    TAG_SLOT_COUNT,
    VECTOR_ATTRIBUTES_VERSION,
    build_filter_groups,
    build_vector_attributes,
)


def test_build_vector_attributes_records_version_and_bounded_tag_slots() -> None:
    attributes = build_vector_attributes(
        library_id="library_1",
        source_id="source_1",
        chunk_id="chunk_1",
        source_kind="text",
        content_kind="semantic_chunk",
        title="A" * 300,
        tag_slugs=[f"tag-{index}" for index in range(1, TAG_SLOT_COUNT + 3)],
    )

    assert attributes["attributes_version"] == float(VECTOR_ATTRIBUTES_VERSION)
    assert attributes["title"] == "A" * 256
    assert attributes["tag_1"] == "tag-1"
    assert attributes[f"tag_{TAG_SLOT_COUNT}"] == f"tag-{TAG_SLOT_COUNT}"
    assert f"tag_{TAG_SLOT_COUNT + 1}" not in attributes


def test_build_filter_groups_combines_source_kind_and_any_tags() -> None:
    filters = cast(
        dict[str, Any],
        build_filter_groups(
            source_ids=["source_1", "source_2"],
            source_kinds=["text"],
            tag_slugs=["alpha", "bravo"],
            tag_match_mode="any",
        ),
    )

    assert filters["type"] == "and"
    source_filter, kind_filter, tag_filter = filters["filters"]
    assert source_filter == {
        "type": "or",
        "filters": [
            {"type": "eq", "key": "source_id", "value": "source_1"},
            {"type": "eq", "key": "source_id", "value": "source_2"},
        ],
    }
    assert kind_filter == {"type": "eq", "key": "source_kind", "value": "text"}
    assert tag_filter["type"] == "or"
    assert len(tag_filter["filters"]) == 2


def test_build_filter_groups_requires_all_tag_slots_when_requested() -> None:
    filters = cast(
        dict[str, Any],
        build_filter_groups(
            source_ids=[],
            source_kinds=[],
            tag_slugs=["alpha", "bravo"],
            tag_match_mode="all",
        ),
    )

    assert filters["type"] == "and"
    assert len(filters["filters"]) == 2
