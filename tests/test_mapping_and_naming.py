from __future__ import annotations

import tempfile
import tomllib
import unittest
from dataclasses import replace
from pathlib import Path, PurePosixPath

from rc2ui.adapters.res.dialog_template import parse_dialog
from rc2ui.adapters.res.reader import parse_res
from rc2ui.domain.geometry import RectDlu
from rc2ui.domain.resource_id import ResourceId
from rc2ui.mapping.controls import ControlMapper
from rc2ui.naming.map import NamingMap
from rc2ui.naming.resolver import NameResolver, NameSource, semantic_base
from tests.resource_fixtures import res_record, standard_dialog_payload


class _Symbols:
    _values = {
        100: ("IDD_LOGIN",),
        -1: ("IDC_STATIC",),
        1001: ("IDC_EDIT1",),
    }

    def symbols_for(self, value: int) -> tuple[str, ...]:
        return self._values.get(value, ())


def sample_dialog():
    [entry] = parse_res(res_record(5, 100, standard_dialog_payload()))
    return parse_dialog(
        entry,
        source=PurePosixPath("resources/main.rc"),
        symbols=_Symbols(),
    )


class MappingAndNamingTests(unittest.TestCase):
    def test_associated_label_names_generic_edit_and_sets_shared_base(self) -> None:
        dialog = sample_dialog()
        mapper = ControlMapper()
        mapped = tuple(mapper.map(control) for control in dialog.controls)

        result = NameResolver().resolve(dialog, mapped)

        self.assertEqual(result.dialog.object_name, "loginDialog")
        self.assertEqual(result.controls[0].object_name, "userNameLabel")
        self.assertEqual(result.controls[1].object_name, "userNameEdit")
        self.assertEqual(result.controls[1].source, NameSource.LABEL)
        self.assertGreater(result.label_associations[0].confidence, 0.8)

    def test_label_buddy_prefers_coordinates_over_resource_order(self) -> None:
        original = sample_dialog()
        label = replace(
            original.controls[0],
            text="&Value:",
            rect=RectDlu(7, 10, 30, 8),
            order=0,
        )
        farther_but_adjacent = replace(
            original.controls[1],
            key=replace(
                original.controls[1].key,
                resource_id=ResourceId.from_ordinal(1002),
            ),
            rect=RectDlu(50, 8, 20, 14),
            order=1,
        )
        nearer_but_later = replace(
            original.controls[1],
            key=replace(
                original.controls[1].key,
                resource_id=ResourceId.from_ordinal(1003),
            ),
            rect=RectDlu(42, 8, 20, 14),
            order=2,
        )
        dialog = replace(
            original,
            controls=(label, farther_but_adjacent, nearer_but_later),
        )
        mapper = ControlMapper()
        mapped = tuple(mapper.map(control) for control in dialog.controls)

        result = NameResolver().resolve(dialog, mapped)

        [association] = result.label_associations
        self.assertEqual(association.label_order, 0)
        self.assertEqual(association.target_order, 2)
        self.assertEqual(result.controls[2].object_name, "valueEdit")

    def test_explicit_table_has_priority_over_semantic_name(self) -> None:
        dialog = sample_dialog()
        mapper = ControlMapper()
        mapped = tuple(mapper.map(control) for control in dialog.controls)
        with tempfile.TemporaryDirectory() as directory_name:
            path = Path(directory_name, "names.toml")
            path.write_text(
                "[[rules]]\n"
                'name = "login-edit"\n'
                'kind = "control"\n'
                "source_regex = 'resources/main\\.rc'\n"
                'dialog_regex = "IDD_LOGIN"\n'
                'id_regex = "IDC_EDIT1"\n'
                'name_template = "LOGIN_EDIT"\n',
                encoding="utf-8",
            )
            naming_map = NamingMap.from_table(
                tomllib.loads(path.read_text(encoding="utf-8")),
                path=path,
            )

        result = NameResolver(naming_map).resolve(dialog, mapped)

        self.assertEqual(result.controls[1].object_name, "loginEdit")
        self.assertEqual(result.controls[1].source, NameSource.EXPLICIT)
        self.assertIn("matched regex", result.controls[1].evidence[1])

    def test_transliterates_cyrillic_label_deterministically(self) -> None:
        self.assertEqual(semantic_base("&Имя пользователя:"), "imyaPolzovatelya")

    def test_maps_standard_classes(self) -> None:
        dialog = sample_dialog()
        mapper = ControlMapper()

        mapped = tuple(mapper.map(control) for control in dialog.controls)

        self.assertEqual(mapped[0].qt_class, "QLabel")
        self.assertEqual(mapped[1].qt_class, "QLineEdit")
        self.assertTrue(mapped[1].expands_horizontally)

    def test_vertical_trackbar_preserves_its_tick_width(self) -> None:
        dialog = sample_dialog()
        control = replace(
            dialog.controls[1],
            class_name="msctls_trackbar32",
            style=0x0002,
            rect=RectDlu(8, 20, 30, 50),
        )

        mapped = ControlMapper().map(control)

        self.assertEqual(mapped.qt_class, "QSlider")
        self.assertTrue(mapped.expands_horizontally)
        self.assertTrue(mapped.expands_vertically)

    def test_regular_button_text_cannot_shift_coordinate_tracks(self) -> None:
        dialog = sample_dialog()
        control = replace(
            dialog.controls[1],
            class_name="Button",
            text="A localized caption wider than its neighbours",
            rect=RectDlu(8, 20, 60, 14),
        )

        mapped = ControlMapper().map(control)

        policy = next(
            property_.value
            for property_ in mapped.properties
            if property_.name == "sizePolicy"
        )
        self.assertEqual(policy.horizontal, "Ignored")
        self.assertTrue(mapped.expands_horizontally)

    def test_tall_checkbox_fills_its_multiline_source_rectangle(self) -> None:
        dialog = sample_dialog()
        control = replace(
            dialog.controls[1],
            class_name="Button",
            text="Preserve items during processing operation",
            style=3 | 0x00002000,
            rect=RectDlu(8, 20, 99, 30),
        )

        mapped = ControlMapper().map(control)

        policy = next(
            property_.value
            for property_ in mapped.properties
            if property_.name == "sizePolicy"
        )
        self.assertEqual(policy.horizontal, "Ignored")
        self.assertEqual(policy.vertical, "Preferred")
        self.assertTrue(mapped.expands_horizontally)
        self.assertTrue(mapped.expands_vertically)
        self.assertTrue(mapped.multiline_text)
        text = next(
            property_.value.value
            for property_ in mapped.properties
            if property_.name == "text"
        )
        self.assertEqual(
            text,
            "Preserve items\nduring processing\noperation",
        )

    def test_short_multiline_checkbox_keeps_a_single_line(self) -> None:
        dialog = sample_dialog()
        control = replace(
            dialog.controls[1],
            class_name="Button",
            text="Use open schedule",
            style=3 | 0x00002000,
            rect=RectDlu(8, 20, 74, 11),
        )

        mapped = ControlMapper().map(control)

        policy = next(
            property_.value
            for property_ in mapped.properties
            if property_.name == "sizePolicy"
        )
        text = next(
            property_.value.value
            for property_ in mapped.properties
            if property_.name == "text"
        )
        self.assertEqual(text, "Use open schedule")
        self.assertEqual(policy.vertical, "Fixed")
        self.assertFalse(mapped.expands_vertically)
        self.assertTrue(mapped.multiline_text)

    def test_radio_and_push_buttons_use_the_same_multiline_adapter(self) -> None:
        dialog = sample_dialog()
        for style, expected_class, expected_text in (
            (
                9 | 0x00002000,
                "QRadioButton",
                "Preserve items\nduring processing\noperation",
            ),
            (
                0 | 0x00002000,
                "QPushButton",
                "Preserve items during\nprocessing operation",
            ),
        ):
            with self.subTest(expected_class=expected_class):
                control = replace(
                    dialog.controls[1],
                    class_name="Button",
                    text="Preserve items during processing operation",
                    style=style,
                    rect=RectDlu(8, 20, 99, 30),
                )

                mapped = ControlMapper().map(control)
                text = next(
                    property_.value.value
                    for property_ in mapped.properties
                    if property_.name == "text"
                )
                policy = next(
                    property_.value
                    for property_ in mapped.properties
                    if property_.name == "sizePolicy"
                )

                self.assertEqual(mapped.qt_class, expected_class)
                self.assertEqual(text, expected_text)
                self.assertEqual(policy.vertical, "Preferred")
                self.assertTrue(mapped.expands_vertically)
                self.assertTrue(mapped.multiline_text)

    def test_combo_and_group_hints_cannot_distort_coordinate_tracks(self) -> None:
        dialog = sample_dialog()
        combo = replace(
            dialog.controls[1],
            class_name="ComboBox",
            rect=RectDlu(8, 20, 30, 80),
            style=1,
        )
        group = replace(
            dialog.controls[1],
            class_name="Button",
            text="A group title much wider than its rectangle",
            rect=RectDlu(8, 20, 30, 40),
            style=7,
        )

        mapped_combo = ControlMapper().map(combo)
        mapped_group = ControlMapper().map(group)

        combo_policy = next(
            property_.value
            for property_ in mapped_combo.properties
            if property_.name == "sizePolicy"
        )
        group_policy = next(
            property_.value
            for property_ in mapped_group.properties
            if property_.name == "sizePolicy"
        )
        self.assertEqual(combo_policy.horizontal, "Ignored")
        self.assertEqual(combo_policy.vertical, "Ignored")
        self.assertEqual(group_policy.horizontal, "Ignored")
        self.assertEqual(group_policy.vertical, "Preferred")

    def test_single_line_label_text_sets_a_horizontal_minimum(self) -> None:
        dialog = sample_dialog()
        control = replace(
            dialog.controls[0],
            text="A translated label whose text is much wider than its RC slot",
        )

        mapped = ControlMapper().map(control)

        policy = next(
            property_.value
            for property_ in mapped.properties
            if property_.name == "sizePolicy"
        )
        self.assertEqual(policy.horizontal, "Minimum")
        self.assertEqual(policy.vertical, "Preferred")

    def test_multiline_label_keeps_its_rc_wrapping_width(self) -> None:
        dialog = sample_dialog()
        control = replace(
            dialog.controls[0],
            text="A translated label whose text wraps onto another line",
            rect=RectDlu(8, 8, 60, 24),
        )

        mapped = ControlMapper().map(control)

        policy = next(
            property_.value
            for property_ in mapped.properties
            if property_.name == "sizePolicy"
        )
        self.assertEqual(policy.horizontal, "Ignored")
        self.assertEqual(policy.vertical, "Preferred")
        self.assertTrue(
            any(
                property_.name == "wordWrap" and property_.value is True
                for property_ in mapped.properties
            )
        )

    def test_syslink_fills_its_explicit_rc_rectangle(self) -> None:
        dialog = sample_dialog()
        control = replace(
            dialog.controls[0],
            class_name="SysLink",
            text='Learn about <A>compatibility</A>.',
        )

        mapped = ControlMapper().map(control)

        self.assertEqual(mapped.qt_class, "QLabel")
        self.assertTrue(mapped.expands_horizontally)
        policy = next(
            property_.value
            for property_ in mapped.properties
            if property_.name == "sizePolicy"
        )
        self.assertEqual(policy.horizontal, "Ignored")

    def test_generated_control_names_have_one_info_diagnostic(self) -> None:
        original = sample_dialog()
        controls = tuple(
            replace(
                control,
                key=replace(
                    control.key,
                    resource_id=ResourceId.from_ordinal(
                        control.key.resource_id.ordinal
                    ),
                ),
                text="",
            )
            for control in original.controls
        )
        dialog = replace(original, controls=controls)
        mapper = ControlMapper()
        mapped = tuple(mapper.map(control) for control in dialog.controls)

        result = NameResolver().resolve(dialog, mapped)

        generated = [
            diagnostic
            for diagnostic in result.diagnostics
            if diagnostic.code == "naming.generated-controls"
        ]
        self.assertEqual(len(generated), 1)
        self.assertEqual(generated[0].severity, "info")
        self.assertIn("2 controls", generated[0].message)
        self.assertFalse(
            any(
                "control uses generated name" in diagnostic.message
                for diagnostic in result.diagnostics
            )
        )


if __name__ == "__main__":
    unittest.main()
