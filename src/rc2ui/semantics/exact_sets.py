from __future__ import annotations

from dataclasses import dataclass, replace

from rc2ui.analysis.multilingual import MultilingualDialog
from rc2ui.domain.diagnostics import Diagnostic, Severity
from rc2ui.domain.dialog import Control
from rc2ui.mapping.model import MappedControl
from rc2ui.mapping.overrides import ControlCompoundRule, ExactControlSelector
from rc2ui.semantics.detect import resource_selectors
from rc2ui.semantics.model import (
    CompoundAction,
    CompoundCandidate,
    CompoundDecision,
    CompoundKind,
)


@dataclass(frozen=True, slots=True)
class ExactSetAnalysis:
    decisions: tuple[CompoundDecision, ...] = ()
    diagnostics: tuple[Diagnostic, ...] = ()
    used_rule_keys: tuple[str, ...] = ()


def analyze_exact_control_sets(
    multilingual: MultilingualDialog,
    mapped_controls: tuple[MappedControl, ...],
    rules: tuple[ControlCompoundRule, ...],
) -> ExactSetAnalysis:
    """Resolve explicitly configured exact control sets in one dialog.

    Geometry is deliberately not a selector here. The project has named every
    source member exactly, so coordinates only determine the replacement's
    union rectangle later in the layout pipeline.
    """

    if not rules or not multilingual.default_dialog.controls:
        return ExactSetAnalysis()
    location = (
        f"{multilingual.dialog.key.source}:"
        f"{multilingual.dialog.key.resource_id.display_name}"
    )
    mapped_by_order = {item.control.order: item for item in mapped_controls}
    diagnostics: list[Diagnostic] = []
    matched: list[tuple[ControlCompoundRule, CompoundDecision]] = []
    used: set[str] = set()

    for rule in rules:
        if not rule.matches_dialog(multilingual.default_dialog.controls[0]):
            continue
        resolved: list[Control] = []
        failed = False
        for selector in rule.selectors:
            controls = tuple(
                control
                for control in multilingual.default_dialog.controls
                if selector.matches(control)
            )
            if not controls:
                failed = True
                break
            if len(controls) > 1:
                used.add(rule.key)
                diagnostics.append(
                    Diagnostic(
                        code="control-compound.ambiguous-member",
                        severity=Severity.ERROR,
                        message=(
                            f"compound {rule.name!r} selector "
                            f"{selector.win_class}:{selector.exact_id} matches "
                            f"{len(controls)} controls; add occurrence"
                        ),
                        location=location,
                    )
                )
                failed = True
                break
            resolved.append(controls[0])
        if failed:
            continue
        used.add(rule.key)
        orders = tuple(control.order for control in resolved)
        if len(set(orders)) != len(orders):
            diagnostics.append(
                Diagnostic(
                    code="control-compound.duplicate-member",
                    severity=Severity.ERROR,
                    message=(
                        f"compound {rule.name!r} resolves more than one "
                        "selector to the same control"
                    ),
                    location=location,
                )
            )
            continue

        candidate = _candidate(multilingual, rule, tuple(resolved))
        explicitly_mapped = tuple(
            item
            for order in orders
            if (item := mapped_by_order[order]).mapping_rule is not None
        )
        action = CompoundAction.REPLACE
        conflict = None
        if explicitly_mapped:
            action = CompoundAction.KEEP
            conflict = "member also has an explicit one-to-one control mapping"
            diagnostics.append(
                Diagnostic(
                    code="control-compound.mapping-conflict",
                    severity=Severity.ERROR,
                    message=(
                        f"compound {rule.name!r} contains controls already "
                        "claimed by explicit control rules: "
                        + ", ".join(
                            item.control.key.resource_id.display_name
                            for item in explicitly_mapped
                        )
                    ),
                    location=location,
                )
            )
        matched.append(
            (
                rule,
                CompoundDecision(
                    candidate=candidate,
                    action=action,
                    rule_name=rule.name,
                    rule_priority=rule.priority,
                    result_class=rule.widget.qt_class,
                    result_widget=rule.widget,
                    result_rule_key=rule.key,
                    result_runtime_configured=rule.runtime_configured,
                    conflict=conflict,
                ),
            )
        )

    decisions = _resolve_duplicate_matches(matched, diagnostics, location)
    return ExactSetAnalysis(
        decisions=decisions,
        diagnostics=tuple(diagnostics),
        used_rule_keys=tuple(sorted(used)),
    )


def _candidate(
    multilingual: MultilingualDialog,
    rule: ControlCompoundRule,
    controls: tuple[Control, ...],
) -> CompoundCandidate:
    orders = tuple(control.order for control in controls)
    eligible = [multilingual.default_language]
    supporting = [multilingual.default_language]
    for variant in multilingual.variants:
        eligible.append(variant.language)
        if all(
            (control := variant.for_order(order)) is not None
            and selector.matches(control)
            for order, selector in zip(orders, rule.selectors)
        ):
            supporting.append(variant.language)
    consensus = len(supporting) / len(eligible)
    evidence = [
        "configured exact class-and-ID control set: "
        + ", ".join(
            f"{selector.win_class}:{selector.exact_id}"
            for selector in rule.selectors
        )
    ]
    if len(eligible) > 1:
        evidence.append(
            "exact membership supported by LANGIDs "
            + ", ".join(str(language) for language in supporting)
            + " of "
            + ", ".join(str(language) for language in eligible)
        )
    return CompoundCandidate(
        kind=CompoundKind.CONTROL_SET,
        primary_order=controls[0].order,
        orders=orders,
        primary_ids=resource_selectors(controls[0].key.resource_id),
        member_ids=tuple(
            resource_selectors(control.key.resource_id)
            for control in controls[1:]
        ),
        label_texts=(),
        confidence=min(0.99, 0.9 + 0.09 * consensus),
        evidence=tuple(evidence),
        supporting_languages=tuple(sorted(set(supporting))),
        eligible_languages=tuple(sorted(set(eligible))),
        default_action=CompoundAction.REPLACE,
    )


def _resolve_duplicate_matches(
    matched: list[tuple[ControlCompoundRule, CompoundDecision]],
    diagnostics: list[Diagnostic],
    location: str,
) -> tuple[CompoundDecision, ...]:
    by_orders: dict[tuple[int, ...], list[tuple[ControlCompoundRule, CompoundDecision]]] = {}
    for item in matched:
        key = tuple(sorted(item[1].candidate.orders))
        by_orders.setdefault(key, []).append(item)

    result: list[CompoundDecision] = []
    for orders in sorted(by_orders):
        entries = by_orders[orders]
        best_rank = max(rule.rank for rule, _ in entries)
        leaders = [item for item in entries if item[0].rank == best_rank]
        if len(leaders) > 1:
            names = ", ".join(repr(rule.name) for rule, _ in leaders)
            diagnostics.append(
                Diagnostic(
                    code="control-compound.ambiguous-rule",
                    severity=Severity.ERROR,
                    message=(
                        "exact control set matches equally ranked compound "
                        f"rules: {names}; leaving its controls unchanged"
                    ),
                    location=location,
                )
            )
            for rule, decision in entries:
                result.append(
                    replace(
                        decision,
                        action=CompoundAction.KEEP,
                        conflict=(
                            "ambiguous exact compound rules"
                            if rule.rank == best_rank
                            else "shadowed by a higher-ranked compound rule"
                        ),
                    )
                )
            continue
        leader_rule, leader = leaders[0]
        result.append(leader)
        result.extend(
            replace(
                decision,
                action=CompoundAction.KEEP,
                conflict=f"shadowed by compound rule {leader_rule.name!r}",
            )
            for rule, decision in entries
            if rule is not leader_rule
        )
    return tuple(result)
