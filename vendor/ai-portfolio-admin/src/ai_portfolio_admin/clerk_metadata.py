from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast

from .contracts import UserRole


@dataclass(frozen=True, slots=True)
class ClerkMetadataKeys:
    active: str = "active"
    role: str = "role"
    credit_floor_usd: str = "credit_floor_usd"
    default_credit_floor_usd: float = -1.0


DEFAULT_CLERK_METADATA_KEYS = ClerkMetadataKeys()


def public_metadata(raw_metadata: object | None) -> Mapping[str, object]:
    if isinstance(raw_metadata, Mapping):
        typed_metadata = cast(Mapping[object, object], raw_metadata)
        return {key: value for key, value in typed_metadata.items() if isinstance(key, str)}
    return {}


def resolve_role(
    metadata: Mapping[str, object] | None,
    keys: ClerkMetadataKeys = DEFAULT_CLERK_METADATA_KEYS,
) -> UserRole:
    if metadata is None:
        return "user"
    return "admin" if metadata.get(keys.role) == "admin" else "user"


def resolve_active(
    metadata: Mapping[str, object] | None,
    keys: ClerkMetadataKeys = DEFAULT_CLERK_METADATA_KEYS,
) -> bool:
    return bool(metadata is not None and metadata.get(keys.active) is True)


def coerce_credit_floor_usd(raw_value: object) -> float | None:
    if isinstance(raw_value, bool):
        return None
    if isinstance(raw_value, (int, float)):
        value = float(raw_value)
    elif isinstance(raw_value, str):
        normalized_value = raw_value.strip()
        if not normalized_value:
            return None
        try:
            value = float(normalized_value)
        except ValueError:
            return None
    else:
        return None
    if not math.isfinite(value):
        return None
    return round(value, 8)


def has_explicit_credit_floor_usd(
    metadata: Mapping[str, object] | None,
    keys: ClerkMetadataKeys = DEFAULT_CLERK_METADATA_KEYS,
) -> bool:
    return metadata is not None and coerce_credit_floor_usd(metadata.get(keys.credit_floor_usd)) is not None


def resolve_credit_floor_usd(
    metadata: Mapping[str, object] | None,
    keys: ClerkMetadataKeys = DEFAULT_CLERK_METADATA_KEYS,
) -> float:
    if metadata is None:
        return keys.default_credit_floor_usd
    resolved = coerce_credit_floor_usd(metadata.get(keys.credit_floor_usd))
    return resolved if resolved is not None else keys.default_credit_floor_usd


def metadata_with_active_state(
    metadata: Mapping[str, object] | None,
    *,
    active: bool,
    keys: ClerkMetadataKeys = DEFAULT_CLERK_METADATA_KEYS,
) -> dict[str, object]:
    updated = dict(metadata or {})
    updated[keys.active] = active
    if active and not has_explicit_credit_floor_usd(updated, keys):
        updated[keys.credit_floor_usd] = keys.default_credit_floor_usd
    return updated
