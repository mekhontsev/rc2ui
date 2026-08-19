from __future__ import annotations

from pathlib import Path

from rc2ui.qtcheck.protocol import diagnostic


_HEADROOM_TOLERANCE_PX = 2
# Grid tracks and widget contents round independently.  At a 2x font an exact
# shared RC edge can differ by three physical pixels without changing the
# visual order; larger crossings are qualitative movement and remain errors.
_ORDER_TOLERANCE_PX = 4


def analyze_font_change(
    baseline: dict[str, object],
    scaled: dict[str, object],
    *,
    path: Path,
    expected_overlaps: set[tuple[str, str]],
) -> list[dict[str, str]]:
    """Check dynamic FontChange without depending on a Qt binding."""

    before = _widgets(baseline)
    after = _widgets(scaled)
    diagnostics: list[dict[str, str]] = []
    common_names = tuple(sorted(before.keys() & after.keys()))
    for name in common_names:
        before_item = before[name]
        after_item = after[name]
        if not _visible(before_item) or not _visible(after_item):
            continue
        before_headroom = _vertical_headroom(before_item)
        after_headroom = _vertical_headroom(after_item)
        if (
            before_headroom is not None
            and after_headroom is not None
            and before_headroom >= -_HEADROOM_TOLERANCE_PX
            and after_headroom < -_HEADROOM_TOLERANCE_PX
        ):
            diagnostics.append(
                diagnostic(
                    "qt.font-height-clipped",
                    "error",
                    (
                        f"widget {name!r} no longer fits its font after "
                        f"dynamic FontChange ({-after_headroom}px short)"
                    ),
                    path,
                )
            )
        before_width_headroom = _horizontal_headroom(before_item)
        after_width_headroom = _horizontal_headroom(after_item)
        if (
            before_width_headroom is not None
            and after_width_headroom is not None
            and before_width_headroom >= -_HEADROOM_TOLERANCE_PX
            and after_width_headroom < -_HEADROOM_TOLERANCE_PX
        ):
            diagnostics.append(
                diagnostic(
                    "qt.font-width-clipped",
                    "error",
                    (
                        f"widget {name!r} no longer fits its text after "
                        f"dynamic FontChange ({-after_width_headroom}px short)"
                    ),
                    path,
                )
            )

    for left_index, left_name in enumerate(common_names):
        for right_name in common_names[left_index + 1 :]:
            names = tuple(sorted((left_name, right_name)))
            if names in expected_overlaps:
                continue
            left_before = before[left_name]
            right_before = before[right_name]
            left_after = after[left_name]
            right_after = after[right_name]
            if not all(
                _visible(item)
                for item in (
                    left_before,
                    right_before,
                    left_after,
                    right_after,
                )
            ):
                continue
            if not _same_parent(
                left_before,
                right_before,
                left_after,
                right_after,
            ):
                continue
            relation = _broken_order(
                _rect(left_before),
                _rect(right_before),
                _rect(left_after),
                _rect(right_after),
            )
            if relation is None:
                continue
            diagnostics.append(
                diagnostic(
                    "qt.font-order-changed",
                    "error",
                    (
                        f"dynamic FontChange breaks {relation} order between "
                        f"{left_name!r} and {right_name!r}"
                    ),
                    path,
                )
            )
    return diagnostics


def _vertical_headroom(item: dict[str, object]) -> int | None:
    if item.get("font_height_sensitive") is False:
        return None
    geometry = _rect(item)
    minimum = item.get("minimum_size_hint")
    candidates: list[int] = []
    if (
        isinstance(minimum, list)
        and len(minimum) == 2
        and all(isinstance(value, int) for value in minimum)
        and minimum[1] > 0
    ):
        candidates.append(geometry[3] - minimum[1])
    contents_height = item.get("contents_height")
    text_height = item.get("text_required_height")
    if (
        isinstance(contents_height, int)
        and isinstance(text_height, int)
        and text_height > 0
    ):
        candidates.append(contents_height - text_height)
    return min(candidates) if candidates else None


def _horizontal_headroom(item: dict[str, object]) -> int | None:
    if item.get("font_width_sensitive") is False:
        return None
    contents_width = item.get("contents_width")
    text_width = item.get("text_required_width")
    if (
        isinstance(contents_width, int)
        and isinstance(text_width, int)
        and text_width > 0
    ):
        return contents_width - text_width
    return None


def _broken_order(
    left_before: tuple[int, int, int, int],
    right_before: tuple[int, int, int, int],
    left_after: tuple[int, int, int, int],
    right_after: tuple[int, int, int, int],
) -> str | None:
    left_before_right = left_before[0] + left_before[2]
    right_before_right = right_before[0] + right_before[2]
    left_after_right = left_after[0] + left_after[2]
    right_after_right = right_after[0] + right_after[2]
    left_before_bottom = left_before[1] + left_before[3]
    right_before_bottom = right_before[1] + right_before[3]
    left_after_bottom = left_after[1] + left_after[3]
    right_after_bottom = right_after[1] + right_after[3]
    horizontal_projection_overlaps = (
        min(left_before_right, right_before_right)
        > max(left_before[0], right_before[0])
    )
    vertical_projection_overlaps = (
        min(left_before_bottom, right_before_bottom)
        > max(left_before[1], right_before[1])
    )
    if (
        vertical_projection_overlaps
        and left_before_right <= right_before[0]
        and left_after_right > right_after[0] + _ORDER_TOLERANCE_PX
    ):
        return "left-to-right"
    if (
        vertical_projection_overlaps
        and right_before_right <= left_before[0]
        and right_after_right > left_after[0] + _ORDER_TOLERANCE_PX
    ):
        return "right-to-left"
    if (
        horizontal_projection_overlaps
        and left_before_bottom <= right_before[1]
        and left_after_bottom > right_after[1] + _ORDER_TOLERANCE_PX
    ):
        return "top-to-bottom"
    if (
        horizontal_projection_overlaps
        and right_before_bottom <= left_before[1]
        and right_after_bottom > left_after[1] + _ORDER_TOLERANCE_PX
    ):
        return "bottom-to-top"
    return None


def _widgets(snapshot: dict[str, object]) -> dict[str, dict[str, object]]:
    raw = snapshot.get("widgets")
    if not isinstance(raw, dict):
        return {}
    return {
        name: item
        for name, item in raw.items()
        if isinstance(name, str) and isinstance(item, dict)
    }


def _rect(item: dict[str, object]) -> tuple[int, int, int, int]:
    raw = item.get("geometry")
    if (
        isinstance(raw, list)
        and len(raw) == 4
        and all(isinstance(value, int) for value in raw)
    ):
        return raw[0], raw[1], raw[2], raw[3]
    return 0, 0, 0, 0


def _visible(item: dict[str, object]) -> bool:
    return bool(item.get("visible", True))


def _same_parent(*items: dict[str, object]) -> bool:
    return len({item.get("parent_name") for item in items}) == 1
