from __future__ import annotations

import unittest

from rc2ui.analysis.topology import (
    TopologyItem,
    select_topology_preserving_rects,
)
from rc2ui.domain.geometry import RectDlu


class TopologySelectionTests(unittest.TestCase):
    def test_accepts_large_coordinated_shift_that_preserves_rows(self) -> None:
        items = (
            TopologyItem(0, RectDlu(10, 10, 60, 10)),
            TopologyItem(1, RectDlu(10, 40, 60, 10)),
        )
        proposals = {
            0: RectDlu(20, 24, 60, 10),
            1: RectDlu(20, 54, 60, 10),
        }

        result = select_topology_preserving_rects(
            items,
            proposals,
            bounds=RectDlu(0, 0, 200, 100),
        )

        self.assertEqual(dict(result.rects), proposals)
        self.assertEqual(result.rejections, ())

    def test_rejects_only_control_that_would_enter_next_row(self) -> None:
        items = (
            TopologyItem(0, RectDlu(10, 10, 60, 10)),
            TopologyItem(1, RectDlu(10, 30, 60, 10)),
            TopologyItem(2, RectDlu(10, 50, 60, 10)),
        )
        proposals = {
            0: RectDlu(10, 12, 60, 10),
            1: RectDlu(10, 45, 60, 10),
            2: RectDlu(10, 50, 60, 10),
        }

        result = select_topology_preserving_rects(
            items,
            proposals,
            bounds=RectDlu(0, 0, 200, 100),
        )

        self.assertEqual(result.rect_for(0), proposals[0])
        self.assertEqual(result.rect_for(1), items[1].rect)
        self.assertEqual(result.rect_for(2), proposals[2])
        self.assertEqual(tuple(item.order for item in result.rejections), (1,))
        self.assertIn("vertical-order", result.rejections[0].reasons)

    def test_preserves_distant_left_alignment(self) -> None:
        items = (
            TopologyItem(0, RectDlu(10, 10, 60, 10)),
            TopologyItem(1, RectDlu(10, 70, 90, 10)),
        )
        proposals = {
            0: RectDlu(30, 10, 60, 10),
            1: items[1].rect,
        }

        result = select_topology_preserving_rects(items, proposals)

        self.assertEqual(result.rect_for(0), items[0].rect)
        self.assertIn("left-alignment", result.rejections[0].reasons)

    def test_preserves_original_one_dlu_overlap_without_growing_it(self) -> None:
        items = (
            TopologyItem(0, RectDlu(10, 10, 60, 11)),
            TopologyItem(1, RectDlu(10, 20, 60, 10)),
        )
        proposals = {
            0: RectDlu(10, 12, 60, 11),
            1: items[1].rect,
        }

        result = select_topology_preserving_rects(items, proposals)

        self.assertEqual(result.rect_for(0), items[0].rect)
        self.assertEqual(result.rejections[0].reasons, ("vertical-order",))

    def test_preserves_group_containment(self) -> None:
        items = (
            TopologyItem(0, RectDlu(5, 5, 150, 80), is_container=True),
            TopologyItem(1, RectDlu(15, 20, 60, 10)),
        )
        proposals = {
            0: items[0].rect,
            1: RectDlu(160, 20, 60, 10),
        }

        result = select_topology_preserving_rects(items, proposals)

        self.assertEqual(result.rect_for(1), items[1].rect)
        self.assertEqual(result.rejections[0].reasons, ("containment",))

    def test_large_unanchored_correction_keeps_default(self) -> None:
        item = TopologyItem(0, RectDlu(10, 10, 60, 10))

        result = select_topology_preserving_rects(
            (item,),
            {0: RectDlu(70, 50, 100, 20)},
            bounds=RectDlu(0, 0, 200, 100),
        )

        self.assertEqual(result.rect_for(0), item.rect)
        self.assertEqual(result.rejections[0].reasons, ("unanchored",))

    def test_rejects_anchor_shift_that_reverses_local_gap_affinity(self) -> None:
        items = (
            TopologyItem(0, RectDlu(53, 87, 37, 12)),
            TopologyItem(1, RectDlu(95, 87, 21, 12)),
            TopologyItem(2, RectDlu(119, 87, 37, 12)),
        )
        proposals = {
            0: RectDlu(55, 87, 37, 12),
            1: RectDlu(94, 87, 21, 12),
            2: items[2].rect,
        }

        result = select_topology_preserving_rects(
            items,
            proposals,
            preserve_alignments=False,
            preserve_containment=False,
            reject_unanchored=False,
            preserve_neighbour_gaps=True,
            neighbour_gap_tolerance=3,
        )
        rects = dict(result.rects)
        left_gap = rects[1].left - rects[0].right
        right_gap = rects[2].left - rects[1].right

        self.assertGreater(left_gap, right_gap)
        self.assertIn(
            "horizontal-gap-affinity",
            {
                reason
                for rejection in result.rejections
                for reason in rejection.reasons
            },
        )

    def test_accepts_coherent_row_translation_with_unchanged_gaps(self) -> None:
        items = (
            TopologyItem(0, RectDlu(53, 87, 37, 12)),
            TopologyItem(1, RectDlu(95, 87, 21, 12)),
            TopologyItem(2, RectDlu(119, 87, 37, 12)),
        )
        proposals = {
            item.order: RectDlu(
                item.rect.x + 2,
                item.rect.y,
                item.rect.width,
                item.rect.height,
            )
            for item in items
        }

        result = select_topology_preserving_rects(
            items,
            proposals,
            preserve_alignments=False,
            preserve_containment=False,
            reject_unanchored=False,
            preserve_neighbour_gaps=True,
            neighbour_gap_tolerance=3,
        )

        self.assertEqual(dict(result.rects), proposals)
        self.assertEqual(result.rejections, ())


if __name__ == "__main__":
    unittest.main()
