from __future__ import annotations

import unittest
from dataclasses import replace
from pathlib import PurePosixPath

from rc2ui.analysis.multilingual import (
    DefaultLanguageUnavailable,
    fuse_dialog_languages,
)
from rc2ui.domain.dialog import Control, ControlKey, Dialog, DialogKey
from rc2ui.domain.geometry import RectDlu
from rc2ui.domain.resource_id import ResourceId


def _dialog(
    language: int,
    width: int,
    height: int,
    controls: tuple[tuple[int, int, str, RectDlu], ...],
    *,
    class_names: tuple[str, ...] | None = None,
) -> Dialog:
    key = DialogKey(
        PurePosixPath("main.rc"),
        ResourceId.from_ordinal(100, "IDD_SAMPLE"),
        language,
    )
    occurrences: dict[int, int] = {}
    result = []
    for order, (control_id, style, text, rect) in enumerate(controls):
        occurrence = occurrences.get(control_id, 0) + 1
        occurrences[control_id] = occurrence
        result.append(
            Control(
                ControlKey(
                    key,
                    ResourceId.from_ordinal(control_id),
                    occurrence,
                ),
                class_names[order] if class_names else "Static",
                text,
                rect,
                style,
                0,
                order,
            )
        )
    return Dialog(
        key,
        "Русский" if language == 1049 else "English",
        RectDlu(0, 0, width, height),
        0,
        0,
        tuple(result),
    )


class MultilingualDialogTests(unittest.TestCase):
    def test_coordinates_beat_reversed_order_for_repeated_static_ids(self) -> None:
        english = _dialog(
            1033,
            200,
            100,
            (
                (-1, 0, "First", RectDlu(10, 10, 10, 8)),
                (-1, 0, "Second", RectDlu(14, 10, 10, 8)),
            ),
        )
        russian = _dialog(
            1049,
            200,
            100,
            (
                (-1, 0, "Второй", RectDlu(14, 10, 10, 8)),
                (-1, 0, "Первый", RectDlu(10, 10, 10, 8)),
            ),
        )

        result = fuse_dialog_languages((english, russian), 1033)

        self.assertEqual(result.variants[0].for_order(0).text, "Первый")
        self.assertEqual(result.variants[0].for_order(1).text, "Второй")

    def test_global_matching_handles_reordered_static_controls(self) -> None:
        russian = _dialog(
            1049,
            180,
            80,
            (
                (-1, 0, "Имя:", RectDlu(7, 10, 45, 8)),
                (-1, 0, "Пароль:", RectDlu(7, 40, 45, 8)),
            ),
        )
        english = _dialog(
            1033,
            240,
            100,
            (
                (-1, 0, "Password:", RectDlu(9, 50, 60, 10)),
                (-1, 0, "Name:", RectDlu(9, 12, 60, 10)),
            ),
        )

        result = fuse_dialog_languages((english, russian), 1049)

        self.assertEqual(result.default_language, 1049)
        self.assertEqual(result.dialog.caption, "Русский")
        self.assertEqual(result.dialog.rect, RectDlu(0, 0, 180, 80))
        self.assertEqual(result.layout_dialog.rect, RectDlu(0, 0, 180, 80))
        self.assertEqual(result.variants[0].for_order(0).text, "Name:")
        self.assertEqual(result.variants[0].for_order(1).text, "Password:")
        self.assertEqual(result.diagnostics, ())

    def test_reports_only_unmatched_structure_not_localized_geometry(self) -> None:
        russian = _dialog(
            1049,
            180,
            80,
            ((1001, 0, "Имя", RectDlu(7, 10, 45, 8)),),
        )
        english = _dialog(
            1033,
            210,
            90,
            (
                (1001, 0, "Name", RectDlu(8, 11, 55, 9)),
                (1002, 0, "Extra", RectDlu(8, 35, 55, 9)),
            ),
        )

        result = fuse_dialog_languages((russian, english), 1049)

        self.assertEqual(
            [item.code for item in result.diagnostics],
            ["language.structure-mismatch"],
        )
        self.assertIn("variant-only 1", result.diagnostics[0].message)

    def test_single_distant_variant_does_not_drag_default_geometry(self) -> None:
        default = _dialog(
            1033,
            200,
            100,
            ((1001, 0, "Name", RectDlu(10, 10, 60, 10)),),
        )
        russian = _dialog(
            1049,
            200,
            100,
            ((1001, 0, "Имя", RectDlu(80, 60, 110, 20)),),
        )

        result = fuse_dialog_languages((default, russian), 1033)

        self.assertEqual(
            result.layout_dialog.controls[0].rect,
            default.controls[0].rect,
        )

    def test_single_variant_cannot_move_a_default_control_to_another_row(
        self,
    ) -> None:
        default = _dialog(
            1033,
            200,
            100,
            (
                (1001, 0, "Third row", RectDlu(10, 30, 80, 10)),
                (1002, 0, "Fourth row", RectDlu(10, 48, 80, 10)),
            ),
        )
        russian = _dialog(
            1049,
            200,
            100,
            (
                (1001, 0, "Третья строка", RectDlu(10, 48, 80, 10)),
                (1002, 0, "Четвёртая строка", RectDlu(10, 48, 80, 10)),
            ),
        )
        result = fuse_dialog_languages((default, russian), 1033)

        self.assertEqual(
            result.layout_dialog.controls[0].rect,
            default.controls[0].rect,
        )
        self.assertEqual(
            result.layout_dialog.controls[1].rect,
            default.controls[1].rect,
        )
        self.assertIn(
            "language.topology-correction-rejected",
            tuple(item.code for item in result.diagnostics),
        )

    def test_two_languages_can_make_large_topology_safe_correction(self) -> None:
        default = _dialog(
            1033,
            200,
            100,
            (
                (1001, 0, "First row", RectDlu(10, 10, 80, 10)),
                (1002, 0, "Second row", RectDlu(10, 40, 80, 10)),
            ),
        )
        russian = _dialog(
            1049,
            200,
            100,
            (
                (1001, 0, "Первая строка", RectDlu(30, 30, 80, 10)),
                (1002, 0, "Вторая строка", RectDlu(30, 60, 80, 10)),
            ),
        )

        result = fuse_dialog_languages((default, russian), 1033)

        self.assertEqual(
            tuple(control.rect for control in result.layout_dialog.controls),
            (
                RectDlu(20, 20, 80, 10),
                RectDlu(20, 50, 80, 10),
            ),
        )
        self.assertNotIn(
            "language.topology-correction-rejected",
            tuple(item.code for item in result.diagnostics),
        )

    def test_translation_geometry_cannot_shrink_default_controls(self) -> None:
        default = _dialog(
            1033,
            200,
            100,
            (
                (1001, 0, "Long first label", RectDlu(10, 10, 90, 10)),
                (1002, 0, "Long second label", RectDlu(10, 40, 90, 10)),
            ),
        )
        russian = _dialog(
            1049,
            200,
            100,
            (
                (1001, 0, "A", RectDlu(10, 10, 30, 8)),
                (1002, 0, "B", RectDlu(10, 40, 30, 8)),
            ),
        )

        result = fuse_dialog_languages((default, russian), 1033)

        self.assertEqual(
            tuple(control.rect for control in result.layout_dialog.controls),
            tuple(control.rect for control in default.controls),
        )

    def test_unique_id_with_incompatible_class_is_not_layout_evidence(self) -> None:
        default = _dialog(
            1033,
            200,
            100,
            ((1001, 0, "Name", RectDlu(10, 10, 60, 10)),),
            class_names=("Edit",),
        )
        russian = _dialog(
            1049,
            200,
            100,
            ((1001, 0, "Имя", RectDlu(10, 10, 60, 10)),),
            class_names=("Button",),
        )

        result = fuse_dialog_languages((default, russian), 1033)

        self.assertIsNone(result.variants[0].for_order(0))
        self.assertEqual(result.geometry_languages, (1033,))
        self.assertEqual(result.diagnostics[0].code, "language.structure-mismatch")

    def test_multiple_variants_require_requested_default_language(self) -> None:
        russian = _dialog(
            1049,
            180,
            80,
            ((1001, 0, "Имя", RectDlu(7, 10, 45, 8)),),
        )
        german = _dialog(
            1031,
            180,
            80,
            ((1001, 0, "Name", RectDlu(7, 10, 45, 8)),),
        )

        with self.assertRaisesRegex(
            DefaultLanguageUnavailable,
            "default LANGID 1033 is unavailable",
        ):
            fuse_dialog_languages((russian, german), 1033)

    def test_single_variant_is_allowed_when_default_is_unavailable(self) -> None:
        russian = _dialog(
            1049,
            180,
            80,
            ((1001, 0, "Имя", RectDlu(7, 10, 45, 8)),),
        )

        result = fuse_dialog_languages((russian,), 1033)

        self.assertEqual(result.default_language, 1049)
        self.assertEqual(result.diagnostics, ())

    def test_languages_vote_for_grouping_alignment_and_overlap(self) -> None:
        def variant(language: int, shift: int) -> Dialog:
            return _dialog(
                language,
                200,
                100,
                (
                    (1100, 7, "Group", RectDlu(5, 5, 185, 85)),
                    (1001, 0, "Left", RectDlu(15, 25 + shift, 50, 10)),
                    (1002, 0, "Right", RectDlu(75, 25, 80, 14)),
                    (1003, 0, "Layer A", RectDlu(15, 55, 90, 14)),
                    (1004, 0, "Layer B", RectDlu(17, 56, 88, 14)),
                ),
                class_names=("Button", "Static", "Edit", "Edit", "ComboBox"),
            )

        result = fuse_dialog_languages(
            (variant(1033, 4), variant(1049, 0), variant(1031, 0)),
            1033,
        )

        hints = result.layout_hints
        self.assertTrue(
            any(item.order == 1 and item.parent_order == 0 for item in hints.parents)
        )
        self.assertIn(frozenset((1, 2)), hints.same_row_pairs)
        self.assertIn(frozenset((3, 4)), hints.alternative_pairs)
        alternative = next(
            item for item in hints.alternatives if item.orders == (3, 4)
        )
        self.assertEqual(alternative.supporting_languages, (1033, 1031, 1049))

    def test_group_box_is_not_peer_row_evidence_for_fields(self) -> None:
        def variant(language: int) -> Dialog:
            return _dialog(
                language,
                326,
                313,
                (
                    (1100, 7, "G", RectDlu(4, 58, 183, 25)),
                    (1001, 0, "A", RectDlu(254, 51, 69, 14)),
                    (1002, 0, "B", RectDlu(254, 67, 69, 14)),
                ),
                class_names=("Button", "Edit", "Edit"),
            )

        result = fuse_dialog_languages(
            (variant(1033), variant(1049)),
            1033,
        )

        self.assertNotIn(
            frozenset((0, 1)),
            result.layout_hints.same_row_pairs,
        )
        self.assertNotIn(
            frozenset((0, 2)),
            result.layout_hints.same_row_pairs,
        )

    def test_languages_vote_for_shared_right_edge_columns(self) -> None:
        def variant(language: int, shift: int) -> Dialog:
            return _dialog(
                language,
                200,
                100,
                (
                    (1001, 0, "Short", RectDlu(145 + shift, 10, 40, 14)),
                    (1002, 0, "Wide", RectDlu(125 + shift, 55, 60, 14)),
                ),
                class_names=("Button", "Button"),
            )

        result = fuse_dialog_languages(
            (variant(1033, 0), variant(1049, 2)),
            1033,
        )

        self.assertIn(
            frozenset((0, 1)),
            result.layout_hints.same_column_pairs,
        )

    def test_majority_can_reject_default_language_overlap(self) -> None:
        default = _dialog(
            1033,
            200,
            100,
            (
                (1001, 0, "A", RectDlu(15, 20, 90, 14)),
                (1002, 0, "B", RectDlu(17, 21, 88, 14)),
            ),
            class_names=("Edit", "ComboBox"),
        )
        russian = _dialog(
            1049,
            200,
            100,
            (
                (1001, 0, "A", RectDlu(15, 20, 90, 14)),
                (1002, 0, "B", RectDlu(15, 50, 90, 14)),
            ),
            class_names=("Edit", "ComboBox"),
        )
        german = replace(russian, key=replace(russian.key, language=1031))

        result = fuse_dialog_languages((default, russian, german), 1033)

        self.assertNotIn(
            frozenset((0, 1)),
            result.layout_hints.alternative_pairs,
        )
        self.assertIn(
            frozenset((0, 1)),
            result.layout_hints.rejected_alternative_pairs,
        )

    def test_second_language_repairs_small_group_boundary_error(self) -> None:
        default = _dialog(
            1033,
            200,
            100,
            (
                (1100, 7, "Group", RectDlu(10, 10, 100, 70)),
                (1001, 0, "Child", RectDlu(108, 30, 8, 10)),
            ),
            class_names=("Button", "Edit"),
        )
        russian = _dialog(
            1049,
            200,
            100,
            (
                (1100, 7, "Group", RectDlu(10, 10, 100, 70)),
                (1001, 0, "Child", RectDlu(103, 30, 8, 10)),
            ),
            class_names=("Button", "Edit"),
        )

        result = fuse_dialog_languages((default, russian), 1033)

        hint = next(item for item in result.layout_hints.parents if item.order == 1)
        self.assertEqual(hint.parent_order, 0)
        self.assertGreaterEqual(hint.confidence, 2 / 3)

    def test_dropdown_list_height_is_not_group_membership_geometry(self) -> None:
        def variant(language: int) -> Dialog:
            return _dialog(
                language,
                180,
                80,
                (
                    (1001, 3, "", RectDlu(12, 17, 148, 100)),
                    (1100, 7, "Mode", RectDlu(5, 3, 168, 42)),
                ),
                class_names=("ComboBox", "Button"),
            )

        result = fuse_dialog_languages(
            (variant(1033), variant(1049)),
            1033,
        )

        hint = next(item for item in result.layout_hints.parents if item.order == 0)
        self.assertEqual(hint.parent_order, 1)
        self.assertEqual(result.default_dialog.controls[0].rect.height, 100)
        self.assertEqual(result.layout_dialog.controls[0].rect.height, 14)

    def test_other_languages_can_reject_accidental_default_grouping(self) -> None:
        default = _dialog(
            1033,
            200,
            100,
            (
                (1100, 7, "Group", RectDlu(10, 10, 100, 70)),
                (1001, 0, "Outside", RectDlu(90, 30, 30, 10)),
            ),
            class_names=("Button", "Edit"),
        )
        outside = _dialog(
            1049,
            200,
            100,
            (
                (1100, 7, "Group", RectDlu(10, 10, 60, 70)),
                (1001, 0, "Outside", RectDlu(90, 30, 30, 10)),
            ),
            class_names=("Button", "Edit"),
        )
        german = replace(outside, key=replace(outside.key, language=1031))

        result = fuse_dialog_languages((default, outside, german), 1033)

        hint = next(item for item in result.layout_hints.parents if item.order == 1)
        self.assertIsNone(hint.parent_order)
        self.assertGreaterEqual(hint.confidence, 2 / 3)


if __name__ == "__main__":
    unittest.main()
