from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

from openai_vectorstore2_backend.app.services.sources import VECTOR_ATTRIBUTES_VERSION, bounded_tag_ids, build_filter_groups, build_vector_attributes


def test_build_vector_attributes_records_version_and_single_tag() -> None:
    attributes = build_vector_attributes(
        source_id="source_1",
        source_kind="text",
        virtual_path="/" + "A" * 300,
        virtual_name="A" * 300,
        source_created_at=datetime(2026, 4, 25, 12, 30, tzinfo=UTC),
        tag_slugs=["alpha"],
    )

    assert attributes["attributes_version"] == float(VECTOR_ATTRIBUTES_VERSION)
    assert attributes["index_kind"] == "source_file"
    assert attributes["virtual_path"] == ("/" + "A" * 300)[:256]
    assert attributes["virtual_name"] == "A" * 256
    assert attributes["created_at"] == datetime(2026, 4, 25, 12, 30, tzinfo=UTC).timestamp()
    assert attributes["tag"] == "alpha"
    assert "tags" not in attributes
    assert "tag_1" not in attributes
    assert len(attributes) == 8


def test_build_filter_groups_combines_source_kind_and_tag() -> None:
    filters = cast(
        dict[str, Any],
        build_filter_groups(
            source_ids=["source_1", "source_2"],
            source_kinds=["text"],
            tag_slugs=["alpha"],
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
    assert created_after_filter == {
        "type": "gte",
        "key": "created_at",
        "value": datetime(2026, 1, 1, tzinfo=UTC).timestamp(),
    }
    assert created_before_filter == {
        "type": "lte",
        "key": "created_at",
        "value": datetime(2026, 12, 31, 23, 59, tzinfo=UTC).timestamp(),
    }
    assert tag_filter == {"type": "eq", "key": "tag", "value": "alpha"}


def test_build_filter_groups_can_filter_by_virtual_paths() -> None:
    filters = cast(
        dict[str, Any],
        build_filter_groups(
            source_ids=[],
            source_kinds=[],
            virtual_paths=["Research/alpha.txt", "/Research/bravo.txt"],
            tag_slugs=[],
            tag_match_mode="all",
        ),
    )

    assert filters == {
        "type": "or",
        "filters": [
            {"type": "eq", "key": "virtual_path", "value": "/Research/alpha.txt"},
            {"type": "eq", "key": "virtual_path", "value": "/Research/bravo.txt"},
        ],
    }


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


def test_build_filter_groups_uses_the_single_selected_tag() -> None:
    filters = cast(
        dict[str, Any],
        build_filter_groups(
            source_ids=[],
            source_kinds=[],
            tag_slugs=["alpha", "bravo"],
            tag_match_mode="all",
        ),
    )

    assert filters == {"type": "eq", "key": "tag", "value": "alpha"}


def test_bounded_tag_ids_rejects_more_than_one_tag() -> None:
    try:
        bounded_tag_ids(["alpha", "bravo"])
    except ValueError as exc:
        assert "at most one tag" in str(exc)
    else:
        raise AssertionError("Expected too many tags to be rejected.")
