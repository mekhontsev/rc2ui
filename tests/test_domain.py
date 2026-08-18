from __future__ import annotations

import unittest

from rc2ui.domain.geometry import RectDlu
from rc2ui.domain.resource_id import ResourceId


class RectDluTests(unittest.TestCase):
    def test_calculates_edges_and_centers(self) -> None:
        rect = RectDlu(x=10, y=20, width=30, height=12)

        self.assertEqual(rect.right, 40)
        self.assertEqual(rect.bottom, 32)
        self.assertEqual(rect.center_x, 25)
        self.assertEqual(rect.center_y, 26)


class ResourceIdTests(unittest.TestCase):
    def test_prefers_source_symbol_for_display(self) -> None:
        resource_id = ResourceId.from_ordinal(1, "IDOK")

        self.assertEqual(resource_id.display_name, "IDOK")

    def test_requires_one_compiled_identity(self) -> None:
        with self.assertRaises(ValueError):
            ResourceId()


if __name__ == "__main__":
    unittest.main()
