from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

from backend.app.services.sources import (
    TAG_SLOT_COUNT,
    VECTOR_ATTRIBUTES_VERSION,
    bounded_tag_ids,
    build_filter_groups,
    build_vector_attributes,
)


def test_build_vector_attributes_records_version_and_bounded_tag_slots() -> None:
    attributes = build_vector_attributes(
        source_id="source_1",
        chunk_id="chunk_1",
        source_kind="text",
        virtual_path="/" + "A" * 300,
        virtual_name="A" * 300,
        source_created_at=datetime(2026, 4, 25, 12, 30, tzinfo=UTC),
        tag_slugs=[f"tag-{index}" for index in range(1, TAG_SLOT_COUNT + 3)],
    )

    assert attributes["attributes_version"] == float(VECTOR_ATTRIBUTES_VERSION)
    assert attributes["virtual_path"] == ("/" + "A" * 300)[:256]
    assert attributes["virtual_name"] == "A" * 256
    assert attributes["created_at"] == datetime(2026, 4, 25, 12, 30, tzinfo=UTC).timestamp()
    assert attributes["tags"] == "tag-1,tag-2,tag-3,tag-4,tag-5,tag-6,tag-7,tag-8,tag-9,tag-10"
    assert attributes["tag_1"] == "tag-1"
    assert attributes[f"tag_{TAG_SLOT_COUNT}"] == f"tag-{TAG_SLOT_COUNT}"
    assert f"tag_{TAG_SLOT_COUNT + 1}" not in attributes
    assert len(attributes) == 8 + TAG_SLOT_COUNT


def test_build_filter_groups_combines_source_kind_and_any_tags() -> None:
    filters = cast(
        dict[str, Any],
        build_filter_groups(
            source_ids=["source_1", "source_2"],
            source_kinds=["text"],
            tag_slugs=["alpha", "bravo"],
            tag_match_mode="any",
            created_after=datetime(2026, 1, 1, tzinfo=UTC),
            created_before=datetime(2026, 12, 31, 23, 59, tzinfo=UTC),
        ),
    )

    assert filters["type"] == "and"
    source_filter, kind_filter, created_after_filter, created_before_filter, tag_filter = filters["filters"]
    assert source_filter == {
        "type": "or",
        "filters": [
            {"type": "eq", "key": "source_id", "value": "source_1"},
            {"type": "eq", "key": "source_id", "value": "source_2"},
        ],
    }
    assert kind_filter == {"type": "eq", "key": "source_kind", "value": "text"}
    assert created_after_filter == {"type": "gte", "key": "created_at", "value": datetime(2026, 1, 1, tzinfo=UTC).timestamp()}
    assert created_before_filter == {
        "type": "lte",
        "key": "created_at",
        "value": datetime(2026, 12, 31, 23, 59, tzinfo=UTC).timestamp(),
    }
    assert tag_filter["type"] == "or"
    assert len(tag_filter["filters"]) == 2


def test_build_filter_groups_can_skip_created_at_prefilters_for_legacy_attributes() -> None:
    filters = cast(
        dict[str, Any],
        build_filter_groups(
            source_ids=[],
            source_kinds=["text"],
            tag_slugs=[],
            tag_match_mode="all",
            created_after=datetime(2026, 1, 1, tzinfo=UTC),
            created_before=datetime(2026, 12, 31, tzinfo=UTC),
            include_created_at_filters=False,
        ),
    )

    assert filters == {"type": "eq", "key": "source_kind", "value": "text"}


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


def test_bounded_tag_ids_rejects_more_tags_than_vector_attributes_can_filter() -> None:
    try:
        bounded_tag_ids([f"tag_{index}" for index in range(TAG_SLOT_COUNT + 1)])
    except ValueError as exc:
        assert f"at most {TAG_SLOT_COUNT}" in str(exc)
    else:
        raise AssertionError("Expected too many tags to be rejected.")
