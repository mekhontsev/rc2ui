from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from statistics import median

from rc2ui.analysis.multilingual import MultilingualLayoutHints
from rc2ui.analysis.topology import (
    TopologyItem,
    select_topology_preserving_rects,
)
from rc2ui.analysis.visual_geometry import control_visual_rect
from rc2ui.domain.diagnostics import Diagnostic, Severity
from rc2ui.domain.dialog import Dialog
from rc2ui.domain.geometry import RectDlu
from rc2ui.layout.alternatives import (
    VisualNode,
    collapse_runtime_alternatives,
    intersection_area,
)
from rc2ui.layout.anchors import (
    AnchorKind,
    Axis,
    AxisAnchorAnalysis,
    AxisAnchorGroup,
    analyze_axis_anchors,
    anchor_coordinate2,
)
from rc2ui.layout.compounds import apply_compound_layout
from rc2ui.layout.cross_container import align_peer_group_rows
from rc2ui.layout.font_scaling import make_font_responsive
from rc2ui.layout.grid import build_coordinate_tracks
from rc2ui.layout.initial_size import initial_form_size
from rc2ui.layout.row_anchors import coherent_vertical_anchor_groups
from rc2ui.mapping.model import (
    ControlRole,
    MappedControl,
    SeparatorOrientation,
)
from rc2ui.naming.resolver import NamingResult
from rc2ui.qt.model import (
    QtCString,
    QtFont,
    QtLayout,
    QtLayoutItem,
    QtProperty,
    QtRect,
    QtSize,
    QtSpacer,
    QtWidget,
)
from rc2ui.semantics.model import SemanticPlan


_MAX_FIXED_CONTAINER_PADDING_DLU = 12


@dataclass(frozen=True, slots=True)
class LayoutBuildResult:
    root_widget: QtWidget
    diagnostics: tuple[Diagnostic, ...]
    layout_bounds: RectDlu
    resolved_rects: tuple[tuple[int, RectDlu], ...]
    selected_anchors: tuple[
        tuple[
            int,
            tuple[str, int] | None,
            tuple[str, int] | None,
        ],
        ...,
    ]
    alternative_states: tuple[
        tuple[int, tuple[tuple[int, int], ...]],
        ...,
    ]

    def rect_for(self, order: int) -> RectDlu:
        for candidate_order, rect in self.resolved_rects:
            if candidate_order == order:
                return rect
        raise KeyError(order)

    def anchors_for(
        self,
        order: int,
    ) -> tuple[tuple[str, int] | None, tuple[str, int] | None]:
        for candidate_order, horizontal, vertical in self.selected_anchors:
            if candidate_order == order:
                return horizontal, vertical
        raise KeyError(order)

    def alternative_states_for(
        self,
        order: int,
    ) -> tuple[tuple[int, int], ...]:
        for candidate_order, states in self.alternative_states:
            if candidate_order == order:
                return states
        return ()


class _ObjectNames:
    def __init__(self) -> None:
        self._counts: dict[str, int] = {}
        self._reserved: set[str] = set()

    def next(self, stem: str) -> str:
        count = self._counts.get(stem, 0) + 1
        candidate = stem if count == 1 else f"{stem}_{count}"
        while candidate in self._reserved:
            count += 1
            candidate = f"{stem}_{count}"
        self._counts[stem] = count
        self._reserved.add(candidate)
        return candidate

    def reserve(self, name: str) -> None:
        self._reserved.add(name)


class LayoutBuilder:
    def __init__(self, *, coordinate_tolerance: int = 3) -> None:
        self.coordinate_tolerance = coordinate_tolerance

    def build(
        self,
        dialog: Dialog,
        mapped_controls: tuple[MappedControl, ...],
        naming: NamingResult,
        layout_hints: MultilingualLayoutHints | None = None,
        semantic_plan: SemanticPlan | None = None,
    ) -> LayoutBuildResult:
        self._names = _ObjectNames()
        self._resolved_rects: dict[int, RectDlu] = {}
        self._selected_anchors: dict[
            int,
            tuple[tuple[str, int] | None, tuple[str, int] | None],
        ] = {}
        self._alternative_states: dict[int, list[tuple[int, int]]] = {}
        self._layout_hints = layout_hints or MultilingualLayoutHints()
        self._semantic_plan = semantic_plan or SemanticPlan()
        self._runtime_geometry_orders = (
            self._semantic_plan.runtime_geometry_secondary_orders
        )
        diagnostics: list[Diagnostic] = []
        buddy_targets = {
            association.label_order: naming.for_order(
                self._semantic_plan.primary_for(association.target_order)
            ).object_name
            for association in naming.label_associations
        }
        nodes: dict[int, VisualNode] = {}
        for mapped in mapped_controls:
            decision = naming.for_order(mapped.control.order)
            self._names.reserve(decision.object_name)
            properties = mapped.properties
            if target := buddy_targets.get(mapped.control.order):
                properties += (QtProperty("buddy", QtCString(target)),)
            nodes[mapped.control.order] = VisualNode(
                order=mapped.control.order,
                orders=(mapped.control.order,),
                rect=control_visual_rect(mapped.control),
                mapped=mapped,
                widget=QtWidget(
                    class_name=mapped.qt_class,
                    object_name=decision.object_name,
                    properties=properties,
                    custom_widget=mapped.custom_widget,
                    button_group=mapped.button_group,
                ),
                children=[],
            )
            self._resolved_rects[mapped.control.order] = control_visual_rect(
                mapped.control
            )
            self._selected_anchors[mapped.control.order] = (None, None)
            if mapped.warning:
                diagnostics.append(
                    Diagnostic(
                        code="mapping.fallback",
                        severity=Severity.WARNING,
                        message=mapped.warning,
                        location=(
                            f"{dialog.key.source}:"
                            f"{dialog.key.resource_id.display_name}:"
                            f"{mapped.control.key.resource_id.display_name}"
                        ),
                    )
                )

        nodes = apply_compound_layout(
            nodes,
            self._semantic_plan,
            resolved_rects=self._resolved_rects,
        )
        parked_nodes = tuple(
            node
            for node in nodes.values()
            if _is_runtime_parked(dialog, node.rect)
        )
        for node in parked_nodes:
            nodes.pop(node.order)
            diagnostics.append(
                Diagnostic(
                    code="layout.offscreen-control-parked",
                    severity=Severity.INFO,
                    message=(
                        f"control {node.widget.object_name!r} is far outside the "
                        "dialog client and is retained as a hidden, unmanaged "
                        "widget for runtime repositioning"
                    ),
                    location=(
                        f"{dialog.key.source}:"
                        f"{dialog.key.resource_id.display_name}"
                    ),
                )
            )

        roots = self._build_group_hierarchy(nodes)
        cross_container_anchors = align_peer_group_rows(
            roots,
            tolerance=self.coordinate_tolerance,
            same_row_pairs=self._usable_hint_pairs(
                self._layout_hints.same_row_pairs
            ),
        )
        for anchor in cross_container_anchors:
            horizontal, _ = self._selected_anchors[anchor.order]
            self._selected_anchors[anchor.order] = (
                horizontal,
                (anchor.kind.value, anchor.coordinate2),
            )
        for node in nodes.values():
            for order in node.orders:
                if len(node.orders) == 1:
                    self._resolved_rects[order] = node.rect
        roots, alternative_detections = collapse_runtime_alternatives(
            roots,
            tolerance=self.coordinate_tolerance,
            next_name=self._names.next,
            forced_pairs=self._usable_hint_pairs(
                self._layout_hints.alternative_pairs
            ),
            rejected_pairs=self._usable_hint_pairs(
                self._layout_hints.rejected_alternative_pairs
            ),
        )
        for group, detection in enumerate(alternative_detections):
            for layer, orders in enumerate(detection.layers):
                for order in orders:
                    self._alternative_states.setdefault(order, []).append(
                        (group, layer)
                    )
        alternative_pairs = {
            frozenset((left, right))
            for detection in alternative_detections
            for index, left in enumerate(detection.orders)
            for right in detection.orders[index + 1 :]
        }
        diagnostics.extend(
            Diagnostic(
                code="layout.runtime-alternatives",
                severity=Severity.INFO,
                message=(
                    "probable runtime alternatives share one layout slot: "
                    + ", ".join(repr(name) for name in detection.object_names)
                    + f"; geometry match {detection.geometry_match:.0%}"
                    + f"; z-order span {detection.z_order_span}"
                    + (
                        "; order evidence "
                        + ", ".join(detection.order_evidence)
                        if detection.order_evidence
                        else "; strict geometry (order-independent)"
                    )
                    + (
                        "; probable topmost control "
                        f"{detection.topmost_object_name!r}"
                    )
                ),
                location=(
                    f"{dialog.key.source}:{dialog.key.resource_id.display_name}"
                ),
            )
            for detection in alternative_detections
        )
        diagnostics.extend(
            self._geometry_diagnostics(
                dialog,
                tuple(nodes.values()),
                alternative_pairs,
            )
        )
        if dialog.menu is not None:
            diagnostics.append(
                Diagnostic(
                    code="dialog.menu-not-emitted",
                    severity=Severity.WARNING,
                    message="dialog menu resources are not represented by QDialog .ui forms",
                    location=f"{dialog.key.source}:{dialog.key.resource_id.display_name}",
                )
            )
        self._populate_group_layouts(roots)
        client_bounds = _effective_client_bounds(dialog, tuple(nodes.values()))
        if client_bounds != RectDlu(0, 0, dialog.rect.width, dialog.rect.height):
            diagnostics.append(
                Diagnostic(
                    code="layout.client-bounds-extended",
                    severity=Severity.WARNING,
                    message=(
                        "controls extend beyond the declared dialog client; "
                        f"layout bounds expanded to {client_bounds.x},"
                        f"{client_bounds.y} {client_bounds.width}x"
                        f"{client_bounds.height} DLU"
                    ),
                    location=(
                        f"{dialog.key.source}:"
                        f"{dialog.key.resource_id.display_name}"
                    ),
                )
            )
        root_layout = self._infer_container_layout(
            roots,
            client_bounds,
        )
        diagnostics.extend(
            _layout_topology_diagnostics(
                dialog,
                mapped_controls,
                naming,
                self._resolved_rects,
                suppressed_orders=self._semantic_plan.consumed_orders,
            )
        )
        # Tiny child-dialog templates are common in common-dialog extensions.
        # An arbitrary pixel floor changes their normalized geometry by a
        # factor of two or three, so derive the initial/minimum size solely
        # from the Win32 DLU client rectangle.
        initial_size = initial_form_size(
            client_bounds,
            mapped_controls,
        )
        root_layout = make_font_responsive(
            root_layout,
            baseline_width=initial_size.width,
            baseline_height=initial_size.height,
            source_width_dlu=initial_size.width_dlu,
            source_height_dlu=client_bounds.height,
            width_spacer_name=self._names.next("fontMinimumWidthSpacer"),
            height_spacer_name=self._names.next("fontMinimumHeightSpacer"),
            width_ruler_name=self._names.next("rc2uiFontWidthRuler"),
            height_ruler_name=self._names.next("rc2uiFontHeightRuler"),
        )
        root_properties: list[QtProperty] = [
            QtProperty(
                "geometry",
                QtRect(
                    0,
                    0,
                    initial_size.width,
                    initial_size.height,
                ),
            ),
            QtProperty(
                "minimumSize",
                QtSize(initial_size.width, initial_size.height),
            ),
            QtProperty("windowTitle", dialog.caption or naming.dialog.object_name),
        ]
        if dialog.font:
            root_properties.append(
                QtProperty(
                    "font",
                    QtFont(
                        family=dialog.font.typeface,
                        point_size=dialog.font.point_size,
                        weight=dialog.font.weight,
                        italic=dialog.font.italic,
                    ),
                )
            )
        root_widget = QtWidget(
            class_name="QDialog",
            object_name=naming.dialog.object_name,
            properties=tuple(root_properties),
            layout=root_layout,
            children=tuple(_parked_widget(node) for node in parked_nodes),
        )
        return LayoutBuildResult(
            root_widget,
            tuple(diagnostics),
            client_bounds,
            tuple(sorted(self._resolved_rects.items())),
            tuple(
                (order, horizontal, vertical)
                for order, (horizontal, vertical) in sorted(
                    self._selected_anchors.items()
                )
            ),
            tuple(
                (order, tuple(states))
                for order, states in sorted(self._alternative_states.items())
            ),
        )

    def _geometry_diagnostics(
        self,
        dialog: Dialog,
        nodes: tuple[VisualNode, ...],
        alternative_pairs: set[frozenset[int]],
    ) -> tuple[Diagnostic, ...]:
        diagnostics: list[Diagnostic] = []
        for node in nodes:
            if node.rect.width == 0 or node.rect.height == 0:
                diagnostics.append(
                    Diagnostic(
                        code="layout.zero-size",
                        severity=Severity.WARNING,
                        message=f"control {node.widget.object_name!r} has zero size",
                        location=f"{dialog.key.source}:{dialog.key.resource_id.display_name}",
                    )
                )
        ordinary = [
            node
            for node in nodes
            if node.mapped.role not in {ControlRole.GROUP, ControlRole.DECORATION}
        ]
        for index, left in enumerate(ordinary):
            for right in ordinary[index + 1 :]:
                if frozenset((left.order, right.order)) in alternative_pairs:
                    continue
                overlap = intersection_area(left.rect, right.rect)
                if overlap == 0:
                    continue
                smaller = min(
                    left.rect.width * left.rect.height,
                    right.rect.width * right.rect.height,
                )
                if smaller and overlap / smaller >= 0.2:
                    diagnostics.append(
                        Diagnostic(
                            code="layout.overlap",
                            severity=Severity.WARNING,
                            message=(
                                f"controls {left.widget.object_name!r} and "
                                f"{right.widget.object_name!r} overlap significantly"
                            ),
                            location=(
                                f"{dialog.key.source}:"
                                f"{dialog.key.resource_id.display_name}"
                            ),
                        )
                    )
        return tuple(diagnostics)

    def _build_group_hierarchy(
        self, nodes: dict[int, VisualNode]
    ) -> list[VisualNode]:
        groups = [node for node in nodes.values() if node.mapped.role is ControlRole.GROUP]
        parent: dict[int, int | None] = {}
        for node in nodes.values():
            containing = [
                group
                for group in groups
                if group.order != node.order
                and _contains_rect(
                    group.rect,
                    node.rect,
                    tolerance=self.coordinate_tolerance,
                )
                and group.rect.width * group.rect.height
                > node.rect.width * node.rect.height
            ]
            containing.sort(
                key=lambda item: (
                    item.rect.width * item.rect.height,
                    item.rect.top,
                    item.rect.left,
                    item.rect.bottom,
                    item.rect.right,
                    item.order,
                )
            )
            parent[node.order] = containing[0].order if containing else None
        for hint in self._layout_hints.parents:
            if hint.order not in nodes:
                continue
            if hint.order in self._runtime_geometry_orders:
                continue
            if hint.parent_order is None or hint.parent_order in nodes:
                parent[hint.order] = hint.parent_order
        roots: list[VisualNode] = []
        for node in sorted(nodes.values(), key=_visual_position_key):
            parent_order = parent[node.order]
            if parent_order is None:
                roots.append(node)
            else:
                nodes[parent_order].children.append(node)
        return roots

    def _usable_hint_pairs(
        self,
        pairs: frozenset[frozenset[int]],
    ) -> frozenset[frozenset[int]]:
        return frozenset(
            pair
            for pair in pairs
            if pair.isdisjoint(self._runtime_geometry_orders)
        )

    def _populate_group_layouts(
        self,
        nodes: list[VisualNode],
    ) -> None:
        for node in nodes:
            if node.children:
                self._populate_group_layouts(node.children)
                inner = RectDlu(
                    node.rect.x + 4,
                    node.rect.y + 8,
                    max(0, node.rect.width - 8),
                    max(0, node.rect.height - 8),
                )
                layout = self._infer_container_layout(
                    node.children,
                    inner,
                    fixed_outer_gaps=True,
                )
                node.widget = replace(node.widget, layout=layout)

    def _infer_container_layout(
        self,
        nodes: list[VisualNode],
        bounds: RectDlu,
        *,
        fixed_outer_gaps: bool = False,
    ) -> QtLayout:
        if not nodes:
            return QtLayout("QVBoxLayout", self._names.next("verticalLayout"), ())
        return self._coordinate_grid_layout(
            nodes,
            bounds,
            fixed_outer_gaps=fixed_outer_gaps,
        )

    def _coordinate_grid_layout(
        self,
        nodes: list[VisualNode],
        bounds: RectDlu,
        *,
        fixed_outer_gaps: bool,
    ) -> QtLayout:
        """Preserve RC geometry as shared, proportionally scalable tracks.

        Every source rectangle edge becomes a grid boundary. Anchor inference
        first snaps the small hand-authored offsets that have strong alignment
        evidence; all remaining edges stay exact, including intentional tiny
        gaps. Unlike a packed box or form layout, the empty tracks remain real
        layout space and therefore participate in resizing.
        """

        # Separators describe regions and boundaries, not peer widget
        # alignment.  Letting a long HLine participate in horizontal anchor
        # voting (or a VLine in vertical voting) can make its incidental edge
        # outvote an otherwise obvious column/centre shared by controls.
        anchor_nodes = [
            node
            for node in nodes
            if node.mapped.separator_orientation is None
        ]
        horizontal_anchors = analyze_axis_anchors(
            anchor_nodes,
            axis=Axis.HORIZONTAL,
            tolerance=self.coordinate_tolerance,
            hinted_pairs=self._usable_hint_pairs(
                self._layout_hints.same_column_pairs
            ),
        )
        vertical_groups = {
            node.order: None for node in nodes
        }
        # A group box is a container boundary, not another field in a visual
        # row.  In particular, its tall rectangle can overlap several rows in
        # a neighbouring pane and would otherwise connect all of them through
        # the transitive row graph.
        row_anchor_nodes = [
            node
            for node in anchor_nodes
            if node.mapped.role is not ControlRole.GROUP
        ]
        vertical_groups.update(coherent_vertical_anchor_groups(
            row_anchor_nodes,
            same_row_pairs=self._usable_hint_pairs(
                self._layout_hints.same_row_pairs
            ),
            tolerance=self.coordinate_tolerance,
            vertical_separators=[
                node
                for node in nodes
                if node.mapped.separator_orientation
                is SeparatorOrientation.VERTICAL
            ],
        ))
        horizontal_groups = {
            node.order: _node_anchor_group(
                node,
                anchor_nodes,
                horizontal_anchors,
            )
            for node in nodes
        }
        for node in nodes:
            selected = (
                _anchor_reference(horizontal_groups[node.order]),
                _anchor_reference(vertical_groups[node.order]),
            )
            for order in node.orders:
                previous = self._selected_anchors[order]
                if len(node.orders) == 1:
                    self._selected_anchors[order] = (
                        selected[0] or previous[0],
                        selected[1] or previous[1],
                    )
                    continue
                member_rect = self._resolved_rects[order]
                member_selected = (
                    _member_anchor_reference(
                        selected[0],
                        member_rect,
                        axis=Axis.HORIZONTAL,
                        tolerance=self.coordinate_tolerance,
                    ),
                    _member_anchor_reference(
                        selected[1],
                        member_rect,
                        axis=Axis.VERTICAL,
                        tolerance=self.coordinate_tolerance,
                    ),
                )
                self._selected_anchors[order] = (
                    member_selected[0] or previous[0],
                    member_selected[1] or previous[1],
                )
        forced_vertical_kinds = _group_child_vertical_anchors(nodes)
        recorded_vertical_kinds = {
            node.order: _recorded_vertical_anchor_kind(
                node,
                self._selected_anchors,
            )
            for node in nodes
        }
        anchored_rects = {
            node.order: _anchored_rect(
                node,
                horizontal_groups[node.order],
                vertical_groups[node.order],
            )
            for node in nodes
        }
        horizontal_rects, rejected_horizontal_anchors = (
            _gap_preserving_horizontal_rects(
                nodes,
                anchored_rects,
                tolerance=self.coordinate_tolerance,
            )
        )
        for node in nodes:
            if node.order not in rejected_horizontal_anchors:
                continue
            for order in node.orders:
                _horizontal, vertical = self._selected_anchors[order]
                self._selected_anchors[order] = (None, vertical)
        geometry_nodes = []
        for node in nodes:
            anchored = anchored_rects[node.order]
            horizontal = horizontal_rects[node.order]
            geometry_nodes.append(
                replace(
                    node,
                    rect=RectDlu(
                        horizontal.x,
                        anchored.y,
                        horizontal.width,
                        anchored.height,
                    ),
                )
            )
        for node in geometry_nodes:
            # A runtime-alternative wrapper represents several controls whose
            # slightly different source rectangles are meaningful evidence.
            # Do not replace every member's report geometry with the union.
            for order in node.orders if len(node.orders) == 1 else ():
                self._resolved_rects[order] = node.rect
        horizontal_tracks = build_coordinate_tracks(
            geometry_nodes,
            bounds=bounds,
            axis=Axis.HORIZONTAL,
        )
        vertical_tracks = build_coordinate_tracks(
            geometry_nodes,
            bounds=bounds,
            axis=Axis.VERTICAL,
        )
        column_stretch = _track_stretch(
            horizontal_tracks.stretch,
            nodes=geometry_nodes,
            bounds=bounds,
            axis=Axis.HORIZONTAL,
            fixed_outer_gaps=fixed_outer_gaps,
        )
        row_stretch = _track_stretch(
            vertical_tracks.stretch,
            nodes=geometry_nodes,
            bounds=bounds,
            axis=Axis.VERTICAL,
            fixed_outer_gaps=fixed_outer_gaps,
        )

        occupied: set[tuple[int, int]] = set()
        items: list[QtLayoutItem] = []
        # RC declaration order is a z-order hint. Geometry, never declaration
        # order, determines the grid position, but overlapping controls must be
        # emitted in their original order so runtime layers remain meaningful.
        for node in _layout_emission_order(geometry_nodes):
            vertical_group = vertical_groups[node.order]
            vertical_kind = (
                vertical_group.kind
                if vertical_group is not None
                else AnchorKind.CENTER
            )
            if vertical_group is None:
                vertical_kind = _container_edge_anchor_kind(
                    node,
                    bounds=bounds,
                    axis=Axis.VERTICAL,
                    tolerance=self.coordinate_tolerance,
                    fallback=vertical_kind,
                    prefer_nearest=(fixed_outer_gaps and len(nodes) == 1),
                )
            first_column, column_span = horizontal_tracks.span(
                node.rect.left,
                node.rect.right,
            )
            first_row, row_span = vertical_tracks.span(
                *(
                    _vertical_cell_bounds(
                        geometry_nodes,
                        vertical_group,
                    )
                    if vertical_group is not None
                    and _can_share_vertical_cell(
                        geometry_nodes,
                        vertical_group,
                    )
                    else (node.rect.top, node.rect.bottom)
                ),
            )
            occupied.update(
                (grid_row, grid_column)
                for grid_row in range(first_row, first_row + row_span)
                for grid_column in range(
                    first_column,
                    first_column + column_span,
                )
            )
            items.append(
                QtLayoutItem(
                    widget=node.widget,
                    row=first_row,
                    column=first_column,
                    row_span=row_span,
                    column_span=column_span,
                    alignment=_item_alignment(
                        node,
                        horizontal=(
                            _node_anchor_kind(
                                node,
                                nodes,
                                horizontal_anchors,
                            )
                        ),
                        vertical=(
                            forced_vertical_kinds.get(
                                node.order,
                                recorded_vertical_kinds[node.order]
                                or vertical_kind,
                            )
                        ),
                    ),
                )
            )
        _append_trailing_track_spacers(
            items,
            occupied,
            row_count=len(vertical_tracks.stretch),
            column_count=len(horizontal_tracks.stretch),
            next_name=self._names.next,
        )
        return QtLayout(
            "QGridLayout",
            self._names.next("geometryGridLayout"),
            tuple(items),
            properties=(
                QtProperty("horizontalSpacing", 0),
                QtProperty("verticalSpacing", 0),
                # The coordinate grid already contains all source outer gaps.
                # Designer defaults would add a second platform-dependent
                # margin and visibly shift controls in small dialogs.
                QtProperty("leftMargin", 0),
                QtProperty("topMargin", 0),
                QtProperty("rightMargin", 0),
                QtProperty("bottomMargin", 0),
            ),
            stretch=column_stretch,
            row_stretch=row_stretch,
            # Stretch factors alone govern only surplus space.  Without these
            # baselines Qt first sizes columns from widget sizeHint() values,
            # which can destroy the source proportions before resizing starts.
            # One DLU per minimum unit is sufficient: all remaining pixels are
            # then distributed by the same DLU weights.
            minimum_widths=horizontal_tracks.stretch,
            minimum_heights=vertical_tracks.stretch,
        )


def _item_alignment(
    node: VisualNode,
    *,
    horizontal: AnchorKind | None,
    vertical: AnchorKind | None,
) -> str | None:
    flags: list[str] = []
    if horizontal is not None and not node.mapped.expands_horizontally:
        flags.append(
            {
                AnchorKind.START: "Qt::AlignLeft",
                AnchorKind.CENTER: "Qt::AlignHCenter",
                AnchorKind.END: "Qt::AlignRight",
            }[horizontal]
        )
    if vertical is not None and not node.mapped.expands_vertically:
        flags.append(
            {
                AnchorKind.START: "Qt::AlignTop",
                AnchorKind.CENTER: "Qt::AlignVCenter",
                AnchorKind.END: "Qt::AlignBottom",
            }[vertical]
        )
    return "|".join(flags) if flags else None


def _node_anchor_kind(
    node: VisualNode,
    nodes: list[VisualNode],
    analysis: AxisAnchorAnalysis,
) -> AnchorKind:
    """Choose the strongest source anchor that relates this node to a peer."""

    group = _node_anchor_group(node, nodes, analysis)
    if group is not None:
        return group.kind
    return (
        AnchorKind.START
        if analysis.axis is Axis.HORIZONTAL
        else AnchorKind.CENTER
    )


def _group_child_vertical_anchors(
    nodes: list[VisualNode],
) -> dict[int, AnchorKind]:
    """Keep a group box edge aligned with children positioned on that edge."""

    result: dict[int, AnchorKind] = {}
    for group in nodes:
        if group.mapped.role is not ControlRole.GROUP or not group.children:
            continue
        if any(
            abs(child.rect.bottom - group.rect.bottom) <= 3
            for child in group.children
        ):
            result[group.order] = AnchorKind.END
        elif any(
            abs(child.rect.top - group.rect.top) <= 3
            for child in group.children
        ):
            result[group.order] = AnchorKind.START
    return result


def _layout_emission_order(nodes: list[VisualNode]) -> list[VisualNode]:
    """Use visual order generally and RC z-order only for actual overlaps."""

    visual = sorted(nodes, key=_visual_position_key)
    priority = {node.order: index for index, node in enumerate(visual)}
    by_order = {node.order: node for node in nodes}
    successors = {node.order: set() for node in nodes}
    incoming = {node.order: 0 for node in nodes}
    for index, left in enumerate(nodes):
        for right in nodes[index + 1 :]:
            if intersection_area(left.rect, right.rect) <= 0:
                continue
            before, after = sorted((left.order, right.order))
            if after not in successors[before]:
                successors[before].add(after)
                incoming[after] += 1

    ready = sorted(
        (order for order, count in incoming.items() if count == 0),
        key=lambda order: priority[order],
    )
    result: list[VisualNode] = []
    while ready:
        order = ready.pop(0)
        result.append(by_order[order])
        for successor in sorted(
            successors[order],
            key=lambda candidate: priority[candidate],
        ):
            incoming[successor] -= 1
            if incoming[successor] == 0:
                ready.append(successor)
                ready.sort(key=lambda candidate: priority[candidate])
    return result


def _track_stretch(
    stretch: tuple[int, ...],
    *,
    nodes: list[VisualNode],
    bounds: RectDlu,
    axis: Axis,
    fixed_outer_gaps: bool,
) -> tuple[int, ...]:
    """Keep container padding fixed while all ordinary whitespace is elastic."""

    if not fixed_outer_gaps or not stretch:
        return stretch
    result = list(stretch)
    if axis is Axis.HORIZONTAL:
        content_start = min(node.rect.left for node in nodes)
        content_end = max(node.rect.right for node in nodes)
        bounds_start, bounds_end = bounds.left, bounds.right
    else:
        content_start = min(node.rect.top for node in nodes)
        content_end = max(node.rect.bottom for node in nodes)
        bounds_start, bounds_end = bounds.top, bounds.bottom
    start_gap = content_start - bounds_start
    end_gap = bounds_end - content_end
    if 0 < start_gap <= _MAX_FIXED_CONTAINER_PADDING_DLU:
        result[0] = 0
    if 0 < end_gap <= _MAX_FIXED_CONTAINER_PADDING_DLU:
        result[-1] = 0
    return tuple(result)


def _recorded_vertical_anchor_kind(
    node: VisualNode,
    selected: dict[
        int,
        tuple[tuple[str, int] | None, tuple[str, int] | None],
    ],
) -> AnchorKind | None:
    """Return a cross-container guide retained before nested inference.

    A nested layout cannot rediscover a relation to a control owned by a peer
    group box.  ``align_peer_group_rows`` records that relation by source
    order; use it for the actual Qt item alignment as well as report metadata.
    """

    references = {
        reference
        for order in node.orders
        if (reference := selected[order][1]) is not None
    }
    if len(references) != 1:
        return None
    kind, _ = references.pop()
    return AnchorKind(kind)


def _container_edge_anchor_kind(
    node: VisualNode,
    *,
    bounds: RectDlu,
    axis: Axis,
    tolerance: int,
    fallback: AnchorKind,
    prefer_nearest: bool = False,
) -> AnchorKind:
    if axis is Axis.HORIZONTAL:
        start_gap = node.rect.left - bounds.left
        end_gap = bounds.right - node.rect.right
    else:
        start_gap = node.rect.top - bounds.top
        end_gap = bounds.bottom - node.rect.bottom
    if start_gap <= tolerance and start_gap <= end_gap:
        return AnchorKind.START
    if end_gap <= tolerance:
        return AnchorKind.END
    if prefer_nearest and start_gap + tolerance < end_gap:
        return AnchorKind.START
    if prefer_nearest and end_gap + tolerance < start_gap:
        return AnchorKind.END
    return fallback


def _node_anchor_group(
    node: VisualNode,
    nodes: list[VisualNode],
    analysis: AxisAnchorAnalysis,
) -> AxisAnchorGroup | None:
    """Return the most strongly evidenced alignment group for one node."""

    by_order = {candidate.order: candidate for candidate in nodes}
    priority = (
        {
            AnchorKind.START: 0,
            AnchorKind.END: 1,
            AnchorKind.CENTER: 2,
        }
        if analysis.axis is Axis.HORIZONTAL
        else {
            AnchorKind.CENTER: 0,
            AnchorKind.START: 1,
            AnchorKind.END: 2,
        }
    )
    candidates: list[tuple[int, int, int, int, int, AxisAnchorGroup]] = []
    for group in analysis.groups:
        if node.order not in group.node_orders or len(group.node_orders) < 2:
            continue
        members = [
            by_order[order]
            for order in group.node_orders
            if order in by_order
        ]
        values = [
            anchor_coordinate2(member.rect, analysis.axis, group.kind)
            for member in members
        ]
        candidates.append(
            (
                -len(members),
                abs(
                    anchor_coordinate2(node.rect, analysis.axis, group.kind)
                    - round(median(values))
                ),
                max(values) - min(values),
                priority[group.kind],
                group.coordinate2,
                group,
            )
        )
    if not candidates:
        return None
    return min(candidates)[-1]


def _vertical_cell_bounds(
    nodes: list[VisualNode],
    group: AxisAnchorGroup,
) -> tuple[int, int]:
    """Give vertically centered peers one shared scalable layout slot."""

    members = [
        candidate
        for candidate in nodes
        if candidate.order in group.node_orders
    ]
    return (
        min(member.rect.top for member in members),
        max(member.rect.bottom for member in members),
    )


def _can_share_vertical_cell(
    nodes: list[VisualNode],
    group: AxisAnchorGroup,
) -> bool:
    """Share a row slot only when Qt will keep every member at its hint size.

    A label and an edit can safely share one cell and align within it.  A
    vertical slider or separator is vertically expanding; putting two such
    controls in the union of their source rectangles makes both fill that
    union and destroys their original height and ordering relations.
    """

    members = [
        candidate
        for candidate in nodes
        if candidate.order in group.node_orders
    ]
    return (
        group.kind is AnchorKind.CENTER
        and len(members) >= 2
        and all(not member.mapped.expands_vertically for member in members)
    )


def _anchored_rect(
    node: VisualNode,
    horizontal_group: AxisAnchorGroup | None,
    vertical_group: AxisAnchorGroup | None,
) -> RectDlu:
    """Snap evidenced human coordinate errors to canonical anchors."""

    x = _aligned_start(node.rect.x, node.rect.width, horizontal_group)
    y = _aligned_start(node.rect.y, node.rect.height, vertical_group)
    return RectDlu(x, y, node.rect.width, node.rect.height)


def _gap_preserving_horizontal_rects(
    nodes: list[VisualNode],
    anchored_rects: dict[int, RectDlu],
    *,
    tolerance: int,
) -> tuple[dict[int, RectDlu], frozenset[int]]:
    """Reject global anchor snaps that corrupt a local horizontal chain."""

    participants = tuple(
        node
        for node in nodes
        if node.mapped.role
        not in {ControlRole.GROUP, ControlRole.DECORATION}
    )
    if len(participants) < 2:
        return dict(anchored_rects), frozenset()
    selection = select_topology_preserving_rects(
        tuple(
            TopologyItem(order=node.order, rect=node.rect)
            for node in participants
        ),
        {
            node.order: RectDlu(
                anchored_rects[node.order].x,
                node.rect.y,
                node.rect.width,
                node.rect.height,
            )
            for node in participants
        },
        order_axes=("horizontal",),
        preserve_alignments=False,
        preserve_containment=False,
        reject_unanchored=False,
        order_requires_orthogonal_overlap=True,
        preserve_neighbour_gaps=True,
        neighbour_gap_tolerance=tolerance,
    )
    result = dict(anchored_rects)
    for order, rect in selection.rects:
        result[order] = rect
    return result, frozenset(item.order for item in selection.rejections)


def _anchor_reference(
    group: AxisAnchorGroup | None,
) -> tuple[str, int] | None:
    if group is None:
        return None
    return group.kind.value, group.coordinate2


def _member_anchor_reference(
    reference: tuple[str, int] | None,
    rect: RectDlu,
    *,
    axis: Axis,
    tolerance: int,
) -> tuple[str, int] | None:
    """Apply a wrapper anchor only to members that touch that anchor.

    Runtime-alternative wrappers use the union of their member rectangles in
    the outer grid.  A member offset inside that union must not inherit the
    union's left/right/top/bottom relation to unrelated controls.
    """

    if reference is None:
        return None
    raw_kind, coordinate2 = reference
    kind = AnchorKind(raw_kind)
    if axis is Axis.HORIZONTAL:
        start = rect.left
        size = rect.width
    else:
        start = rect.top
        size = rect.height
    member_coordinate2 = {
        AnchorKind.START: start * 2,
        AnchorKind.CENTER: start * 2 + size,
        AnchorKind.END: (start + size) * 2,
    }[kind]
    return (
        reference
        if abs(member_coordinate2 - coordinate2) <= tolerance * 2
        else None
    )


def _aligned_start(
    start: int,
    size: int,
    group: AxisAnchorGroup | None,
) -> int:
    if group is None:
        return start
    if group.kind is AnchorKind.START:
        return round(group.coordinate2 / 2)
    if group.kind is AnchorKind.END:
        return round((group.coordinate2 - size * 2) / 2)
    return round((group.coordinate2 - size) / 2)


def _append_trailing_track_spacers(
    items: list[QtLayoutItem],
    occupied: set[tuple[int, int]],
    *,
    row_count: int,
    column_count: int,
    next_name: Callable[[str], str],
) -> None:
    last_column = column_count - 1
    if not any(column == last_column for _, column in occupied):
        row = next(
            candidate
            for candidate in range(row_count)
            if (candidate, last_column) not in occupied
        )
        items.append(
            QtLayoutItem(
                spacer=QtSpacer(
                    next_name("trailingHorizontalSpacer"),
                    "horizontal",
                    size_hint=0,
                ),
                row=row,
                column=last_column,
            )
        )
        occupied.add((row, last_column))

    last_row = row_count - 1
    if not any(row == last_row for row, _ in occupied):
        column = next(
            candidate
            for candidate in range(column_count)
            if (last_row, candidate) not in occupied
        )
        items.append(
            QtLayoutItem(
                spacer=QtSpacer(
                    next_name("trailingVerticalSpacer"),
                    "vertical",
                    size_hint=0,
                ),
                row=last_row,
                column=column,
            )
        )
        occupied.add((last_row, column))


def _effective_client_bounds(
    dialog: Dialog,
    nodes: tuple[VisualNode, ...],
) -> RectDlu:
    left = min((0, *(node.rect.left for node in nodes)))
    top = min((0, *(node.rect.top for node in nodes)))
    right = max((dialog.rect.width, *(node.rect.right for node in nodes)))
    bottom = max((dialog.rect.height, *(node.rect.bottom for node in nodes)))
    return RectDlu(left, top, right - left, bottom - top)


def _layout_topology_diagnostics(
    dialog: Dialog,
    mapped_controls: tuple[MappedControl, ...],
    naming: NamingResult,
    resolved_rects: dict[int, RectDlu],
    *,
    suppressed_orders: frozenset[int] = frozenset(),
) -> tuple[Diagnostic, ...]:
    """Reject layout inference that changes clear source relationships."""

    participants = tuple(
        mapped
        for mapped in mapped_controls
        if mapped.role not in {ControlRole.GROUP, ControlRole.DECORATION}
        and mapped.control.order not in suppressed_orders
    )
    participant_orders = {
        mapped.control.order for mapped in participants
    }
    selection = select_topology_preserving_rects(
        tuple(
            TopologyItem(
                order=mapped.control.order,
                rect=control_visual_rect(mapped.control),
            )
            for mapped in participants
        ),
        {
            order: rect
            for order, rect in resolved_rects.items()
            if order in participant_orders
        },
        bounds=RectDlu(0, 0, dialog.rect.width, dialog.rect.height),
        order_axes=("vertical",),
        preserve_alignments=False,
        preserve_containment=False,
        reject_unanchored=False,
        order_requires_orthogonal_overlap=True,
    )
    if not selection.rejections:
        return ()
    details = []
    for rejection in selection.rejections[:6]:
        name = naming.for_order(rejection.order).object_name
        peer_names = [
            naming.for_order(order).object_name
            for order in rejection.peers
        ]
        detail = f"{name!r}: {', '.join(rejection.reasons)}"
        if peer_names:
            detail += " with " + ", ".join(repr(item) for item in peer_names)
        details.append(detail)
    return (
        Diagnostic(
            code="layout.topology-changed",
            severity=Severity.ERROR,
            message=(
                "layout inference would change clear RC vertical order: "
                + "; ".join(details)
            ),
            location=f"{dialog.key.source}:{dialog.key.resource_id.display_name}",
        ),
    )


def _is_runtime_parked(dialog: Dialog, rect: RectDlu) -> bool:
    """Recognize controls deliberately parked far outside a Win32 dialog."""

    horizontal_limit = max(256, dialog.rect.width * 4)
    vertical_limit = max(256, dialog.rect.height * 4)
    return (
        rect.left > horizontal_limit
        or rect.top > vertical_limit
        or rect.right < -horizontal_limit
        or rect.bottom < -vertical_limit
    )


def _parked_widget(node: VisualNode) -> QtWidget:
    width = max(1, round(node.rect.width * 1.75))
    height = max(1, round(node.rect.height * 1.875))
    return replace(
        node.widget,
        properties=node.widget.properties
        + (
            QtProperty("geometry", QtRect(0, 0, width, height)),
            QtProperty("visible", False),
        ),
    )


def _contains_rect(
    container: RectDlu,
    child: RectDlu,
    *,
    tolerance: int,
) -> bool:
    """Require semantic group children to fit inside the frame.

    Centre-only containment incorrectly turns controls that merely cross a
    group box into children. A small tolerance still accommodates hand-edited
    frame coordinates and the title/border convention of Win32 group boxes.
    """

    return (
        child.left >= container.left - tolerance
        and child.top >= container.top - tolerance
        and child.right <= container.right + tolerance
        and child.bottom <= container.bottom + tolerance
    )


def _visual_position_key(node: VisualNode) -> tuple[float, int, int, int, int]:
    """Sort visually; retain RC order only for identical geometry."""

    return (
        node.rect.top,
        node.rect.left,
        node.rect.bottom,
        node.rect.right,
        node.order,
    )
