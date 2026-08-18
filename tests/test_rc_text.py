from __future__ import annotations

import unittest

from rc2ui.adapters.rc.text import strip_rc_comments


class RcTextTests(unittest.TestCase):
    def test_comment_markers_inside_strings_are_preserved(self) -> None:
        text = (
            'LTEXT "https://example.test/a/*b*/", 1, 0, 0, 10, 10 // note\n'
            "/* first\nsecond */\n"
            'CAPTION "kept // text"\n'
        )

        stripped = strip_rc_comments(text)

        self.assertIn('"https://example.test/a/*b*/"', stripped)
        self.assertIn('CAPTION "kept // text"', stripped)
        self.assertNotIn("note", stripped)
        self.assertEqual(stripped.count("\n"), text.count("\n"))

    def test_escaped_and_doubled_quotes_do_not_end_a_string(self) -> None:
        text = 'LTEXT "one \\" // two "" // three", 1 // comment\n'

        stripped = strip_rc_comments(text)

        self.assertIn("// two", stripped)
        self.assertIn("// three", stripped)
        self.assertNotIn("comment", stripped)


if __name__ == "__main__":
    unittest.main()
