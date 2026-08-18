from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from rc2ui.domain.diagnostics import Diagnostic

if TYPE_CHECKING:
    from rc2ui.mapping.overrides import WidgetProfile


class CompoundKind(StrEnum):
    CONTROL_SET = "control-set"
    EDIT_UPDOWN = "edit-updown"
    EDIT_BROWSE = "edit-browse"
    SLIDER_VALUE = "slider-value"
    LIST_ACTIONS = "list-actions"


class CompoundAction(StrEnum):
    """Policy outcome for one detected compound.

    ``suggest`` records evidence without changing the form. ``keep`` is an
    explicit veto. ``bundle`` records a logical association while keeping all
    widgets in the shared layout; ``replace`` consumes every secondary widget
    and emits one Qt widget.
    """

    SUGGEST = "suggest"
    KEEP = "keep"
    BUNDLE = "bundle"
    REPLACE = "replace"


class CompoundGeometry(StrEnum):
    """How a compound's runtime footprint relates to source rectangles."""

    UNION = "union"
    AUTOBUDDY_LEFT = "autobuddy-left"
    AUTOBUDDY_RIGHT = "autobuddy-right"


SemanticValue = str | bool | int | float


@dataclass(frozen=True, slots=True)
class CompoundCandidate:
    kind: CompoundKind
    primary_order: int
    orders: tuple[int, ...]
    primary_ids: tuple[str, ...]
    member_ids: tuple[tuple[str, ...], ...]
    label_texts: tuple[str, ...]
    confidence: float
    evidence: tuple[str, ...]
    supporting_languages: tuple[int, ...]
    eligible_languages: tuple[int, ...]
    default_action: CompoundAction
    geometry: CompoundGeometry = CompoundGeometry.UNION

    def __post_init__(self) -> None:
        if not self.orders or self.primary_order not in self.orders:
            raise ValueError("compound primary must be one of its orders")
        if len(set(self.orders)) != len(self.orders):
            raise ValueError("compound orders must be unique")
        if len(self.member_ids) != len(self.orders) - 1:
            raise ValueError("compound member IDs must describe secondary orders")
        if not 0 <= self.confidence <= 1:
            raise ValueError("compound confidence must be between zero and one")

    @property
    def secondary_orders(self) -> tuple[int, ...]:
        return tuple(order for order in self.orders if order != self.primary_order)


@dataclass(frozen=True, slots=True)
class CompoundDecision:
    candidate: CompoundCandidate
    action: CompoundAction
    rule_name: str | None = None
    rule_index: int | None = None
    rule_priority: int = 0
    result_class: str | None = None
    properties: tuple[tuple[str, SemanticValue], ...] = ()
    runtime_configured: bool = False
    result_widget: WidgetProfile | None = None
    result_rule_key: str | None = None
    result_runtime_configured: tuple[str, ...] = ()
    conflict: str | None = None

    @property
    def active(self) -> bool:
        return self.action in {CompoundAction.BUNDLE, CompoundAction.REPLACE}

    @property
    def explicit(self) -> bool:
        return self.rule_name is not None


@dataclass(frozen=True, slots=True)
class SemanticPlan:
    decisions: tuple[CompoundDecision, ...] = ()
    diagnostics: tuple[Diagnostic, ...] = ()
    used_rule_indices: tuple[int, ...] = ()
    used_control_rule_keys: tuple[str, ...] = ()

    @property
    def active_decisions(self) -> tuple[CompoundDecision, ...]:
        return tuple(decision for decision in self.decisions if decision.active)

    @property
    def consumed_orders(self) -> frozenset[int]:
        return frozenset(
            order
            for decision in self.active_decisions
            if decision.action is CompoundAction.REPLACE
            for order in decision.candidate.secondary_orders
        )

    @property
    def runtime_geometry_secondary_orders(self) -> frozenset[int]:
        return frozenset(
            order
            for decision in self.decisions
            if decision.candidate.geometry is not CompoundGeometry.UNION
            for order in decision.candidate.secondary_orders
        )

    def decision_for_order(self, order: int) -> CompoundDecision | None:
        return next(
            (
                decision
                for decision in self.active_decisions
                if order in decision.candidate.orders
            ),
            None,
        )

    def primary_for(self, order: int) -> int:
        decision = next(
            (
                item
                for item in self.decisions
                if item.action is CompoundAction.REPLACE
                and order in item.candidate.orders
            ),
            None,
        )
        return (
            decision.candidate.primary_order
            if decision is not None
            else order
        )
