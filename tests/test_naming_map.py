from __future__ import annotations

import tomllib
import unittest
from pathlib import Path, PurePosixPath

from rc2ui.domain.resource_id import ResourceId
from rc2ui.naming.map import NamingKind, NamingMap, NamingMapError


class NamingMapTests(unittest.TestCase):
    def load(self, body: str) -> NamingMap:
        return NamingMap.from_table(
            tomllib.loads(body),
            path=Path("rc2ui.toml"),
        )

    def resolve(
        self,
        naming_map: NamingMap,
        *,
        source: str = "resources/account.rc",
        dialog: ResourceId | None = None,
        kind: NamingKind = NamingKind.CONTROL,
        resource_id: ResourceId | None = None,
        occurrence: int = 1,
    ):
        return naming_map.resolve(
            source=PurePosixPath(source),
            dialog=dialog or ResourceId.from_ordinal(100, "IDD_LOGIN"),
            kind=kind,
            resource_id=resource_id or ResourceId.from_ordinal(1, "IDOK"),
            occurrence=occurrence,
        )

    def test_named_capture_and_template_become_lower_camel_case(self) -> None:
        naming_map = self.load(
            "[[rules]]\n"
            'name = "edit-fields"\n'
            'kind = "control"\n'
            "id_regex = 'IDC_(?P<name>[A-Z0-9_]+)_EDIT'\n"
            'name_template = "${name}_EDIT"\n'
        )

        match = self.resolve(
            naming_map,
            resource_id=ResourceId.from_ordinal(1001, "IDC_USER_NAME_EDIT"),
        )

        self.assertIsNotNone(match)
        self.assertEqual(match.object_name, "userNameEdit")
        self.assertEqual(match.captures, (("name", "USER_NAME"),))

    def test_numeric_capture_is_supported(self) -> None:
        naming_map = self.load(
            "[[rules]]\n"
            'name = "buttons"\n'
            'kind = "control"\n'
            "id_regex = 'IDC_([A-Z0-9_]+)_BUTTON'\n"
            'name_template = "${1}_BUTTON"\n'
        )

        match = self.resolve(
            naming_map,
            resource_id=ResourceId.from_ordinal(7, "IDC_SAVE_AS_BUTTON"),
        )

        self.assertIsNotNone(match)
        self.assertEqual(match.object_name, "saveAsButton")

    def test_dialog_rule_uses_same_resource_for_scope_and_template(self) -> None:
        naming_map = self.load(
            "[[rules]]\n"
            'name = "dialogs"\n'
            'kind = "dialog"\n'
            "dialog_regex = 'IDD_.*'\n"
            "id_regex = 'IDD_(?P<name>[A-Z0-9_]+)'\n"
            'name_template = "${name}_DIALOG"\n'
        )

        match = self.resolve(
            naming_map,
            kind=NamingKind.DIALOG,
            resource_id=ResourceId.from_ordinal(100, "IDD_LOGIN"),
        )

        self.assertIsNotNone(match)
        self.assertEqual(match.object_name, "loginDialog")

    def test_more_specific_rule_wins_independently_of_file_order(self) -> None:
        naming_map = self.load(
            "[[rules]]\n"
            'name = "general-ok"\n'
            'kind = "control"\n'
            'id_regex = "IDOK"\n'
            'name_template = "OK_BUTTON"\n'
            "[[rules]]\n"
            'name = "account-sign-in"\n'
            'kind = "control"\n'
            "source_regex = 'resources/account\\.rc'\n"
            'dialog_regex = "IDD_LOGIN"\n'
            'id_regex = "IDOK"\n'
            'name_template = "SIGN_IN_BUTTON"\n'
        )

        match = self.resolve(naming_map)

        self.assertIsNotNone(match)
        self.assertEqual(match.object_name, "signInButton")
        self.assertEqual(match.rule.name, "account-sign-in")

    def test_priority_precedes_specificity(self) -> None:
        naming_map = self.load(
            "[[rules]]\n"
            'name = "high-priority"\n'
            'kind = "control"\n'
            'id_regex = "IDOK"\n'
            'name_template = "PRIMARY_OK_BUTTON"\n'
            "priority = 10\n"
            "[[rules]]\n"
            'name = "scoped"\n'
            'kind = "control"\n'
            'dialog_regex = "IDD_LOGIN"\n'
            'id_regex = "IDOK"\n'
            'name_template = "SCOPED_OK_BUTTON"\n'
        )

        match = self.resolve(naming_map)

        self.assertIsNotNone(match)
        self.assertEqual(match.object_name, "primaryOkButton")

    def test_equal_precedence_is_ambiguous(self) -> None:
        naming_map = self.load(
            "[[rules]]\n"
            'name = "source-rule"\n'
            'kind = "control"\n'
            "source_regex = 'resources/account\\.rc'\n"
            'id_regex = "IDOK"\n'
            'name_template = "SOURCE_BUTTON"\n'
            "[[rules]]\n"
            'name = "dialog-rule"\n'
            'kind = "control"\n'
            'dialog_regex = "IDD_LOGIN"\n'
            'id_regex = "IDOK"\n'
            'name_template = "DIALOG_BUTTON"\n'
        )

        with self.assertRaisesRegex(NamingMapError, "ambiguous naming rules"):
            self.resolve(naming_map)

    def test_compact_names_table_handles_many_exact_mappings(self) -> None:
        naming_map = self.load(
            "[[rules]]\n"
            'name = "login-controls"\n'
            'kind = "control"\n'
            'dialog_regex = "IDD_LOGIN"\n'
            "[rules.names]\n"
            'IDC_USER = "userEdit"\n'
            'IDC_PASSWORD = { name = "passwordEdit", confidence = 0.9, '
            'derived_from = "reviewed" }\n'
        )

        user = self.resolve(
            naming_map,
            resource_id=ResourceId.from_ordinal(1001, "IDC_USER"),
        )
        password = self.resolve(
            naming_map,
            resource_id=ResourceId.from_ordinal(1002, "IDC_PASSWORD"),
        )

        self.assertEqual(len(naming_map.rules), 2)
        self.assertEqual(user.object_name, "userEdit")
        self.assertEqual(password.object_name, "passwordEdit")
        self.assertEqual(password.rule.confidence, 0.9)
        self.assertEqual(password.rule.exact_id, "IDC_PASSWORD")

    def test_compact_names_table_scales_to_one_hundred_mappings(self) -> None:
        entries = "".join(
            f'IDC_FIELD_{index} = "field{index}Edit"\n'
            for index in range(1, 101)
        )
        naming_map = self.load(
            "[[rules]]\n"
            'name = "bulk-controls"\n'
            'kind = "control"\n'
            'dialog_regex = "IDD_LOGIN"\n'
            "[rules.names]\n"
            + entries
        )

        match = self.resolve(
            naming_map,
            resource_id=ResourceId.from_ordinal(1100, "IDC_FIELD_100"),
        )

        self.assertEqual(len(naming_map.rules), 100)
        self.assertEqual(match.object_name, "field100Edit")

    def test_exact_entry_precedes_regex_at_equal_priority(self) -> None:
        naming_map = self.load(
            "[[rules]]\n"
            'name = "generic-buttons"\n'
            'kind = "control"\n'
            "id_regex = 'ID(?P<name>[A-Z]+)'\n"
            'name_template = "${name}_BUTTON"\n'
            "[[rules]]\n"
            'name = "reviewed-buttons"\n'
            'kind = "control"\n'
            "[rules.names]\n"
            'IDOK = "acceptButton"\n'
        )

        match = self.resolve(naming_map)

        self.assertEqual(match.object_name, "acceptButton")
        self.assertEqual(match.rule.name, "reviewed-buttons")

    def test_occurrence_distinguishes_repeated_static_controls(self) -> None:
        naming_map = self.load(
            "[[rules]]\n"
            'name = "second-static"\n'
            'kind = "control"\n'
            'dialog_regex = "IDD_LOGIN"\n'
            'id_regex = "IDC_STATIC"\n'
            "occurrence = 2\n"
            'name_template = "PASSWORD_LABEL"\n'
        )

        match = self.resolve(
            naming_map,
            resource_id=ResourceId.from_ordinal(-1, "IDC_STATIC"),
            occurrence=2,
        )

        self.assertIsNotNone(match)
        self.assertEqual(match.object_name, "passwordLabel")

    def test_numeric_ordinal_uses_hash_candidate(self) -> None:
        naming_map = self.load(
            "[[rules]]\n"
            'name = "numeric-controls"\n'
            'kind = "control"\n'
            "source_regex = 'legacy\\.rc'\n"
            "dialog_regex = '#100'\n"
            "id_regex = '#(?P<id>\\d+)'\n"
            'name_template = "SERVER_${id}_EDIT"\n'
        )

        match = self.resolve(
            naming_map,
            source="legacy.rc",
            dialog=ResourceId.from_ordinal(100),
            resource_id=ResourceId.from_ordinal(1001),
        )

        self.assertIsNotNone(match)
        self.assertEqual(match.object_name, "server1001Edit")

    def test_regex_uses_full_match(self) -> None:
        naming_map = self.load(
            "[[rules]]\n"
            'name = "ok"\nkind = "control"\n'
            'id_regex = "IDOK"\nname_template = "OK_BUTTON"\n'
        )

        match = self.resolve(
            naming_map,
            resource_id=ResourceId.from_ordinal(2, "IDOK_EXTRA"),
        )

        self.assertIsNone(match)

    def test_catch_all_prefers_contextual_id_symbol(self) -> None:
        naming_map = self.load(
            "[[rules]]\n"
            'name = "all-controls"\nkind = "control"\n'
            "id_regex = '.*'\nname_template = '${0}_WIDGET'\n"
        )

        match = self.resolve(
            naming_map,
            resource_id=ResourceId.from_ordinal(
                1001,
                "GENERIC_ALIAS",
                "IDC_USER_NAME",
            ),
        )

        self.assertEqual(match.matched_id, "IDC_USER_NAME")
        self.assertEqual(match.object_name, "idcUserNameWidget")

    def test_rejects_invalid_regex(self) -> None:
        with self.assertRaisesRegex(NamingMapError, "invalid id_regex"):
            self.load(
                "[[rules]]\nname = 'bad'\nkind = 'control'\n"
                "id_regex = '(broken'\nname_template = 'name'\n"
            )

    def test_rejects_missing_template_group(self) -> None:
        with self.assertRaisesRegex(NamingMapError, "missing group 'name'"):
            self.load(
                "[[rules]]\nname = 'bad'\nkind = 'control'\n"
                "id_regex = 'IDC_(.*)'\n"
                "name_template = '${name}_EDIT'\n"
            )

    def test_rejects_duplicate_matchers(self) -> None:
        with self.assertRaisesRegex(NamingMapError, "duplicate naming matchers"):
            self.load(
                "[[rules]]\nname = 'one'\nkind = 'control'\n"
                "id_regex = 'IDOK'\nname_template = 'okButton'\n"
                "[[rules]]\nname = 'two'\nkind = 'control'\n"
                "id_regex = 'IDOK'\nname_template = 'otherButton'\n"
            )

    def test_rejects_unknown_field(self) -> None:
        with self.assertRaisesRegex(NamingMapError, "unexpected field"):
            self.load(
                "[[rules]]\nname = 'bad'\nkind = 'control'\n"
                "id_regex = 'IDOK'\nname_template = 'okButton'\n"
                "pirority = 10\n"
            )

    def test_rejects_section_level_version(self) -> None:
        with self.assertRaisesRegex(NamingMapError, "unexpected field"):
            self.load("version = 1\nrules = []\n")


if __name__ == "__main__":
    unittest.main()
