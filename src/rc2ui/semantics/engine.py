from __future__ import annotations

from dataclasses import replace

from rc2ui.analysis.multilingual import MultilingualDialog
from rc2ui.domain.diagnostics import Diagnostic, Severity
from rc2ui.mapping.model import MappedControl
from rc2ui.mapping.overrides import ControlMap
from rc2ui.semantics.config import SemanticMap, SemanticRule
from rc2ui.semantics.detect import detect_compounds, resource_selectors
from rc2ui.semantics.exact_sets import analyze_exact_control_sets
from rc2ui.semantics.model import (
    CompoundAction,
    CompoundCandidate,
    CompoundDecision,
    SemanticPlan,
)


class SemanticEngine:
    def __init__(
        self,
        rules: SemanticMap | None = None,
        control_map: ControlMap | None = None,
    ) -> None:
        self.rules = rules or SemanticMap(())
        self.control_map = control_map or ControlMap((), ())

    def analyze(
        self,
        multilingual: MultilingualDialog,
        mapped_controls: tuple[MappedControl, ...],
    ) -> SemanticPlan:
        candidates = detect_compounds(multilingual, mapped_controls)
        exact_sets = analyze_exact_control_sets(
            multilingual,
            mapped_controls,
            self.control_map.compounds,
        )
        diagnostics: list[Diagnostic] = list(exact_sets.diagnostics)
        decisions: list[CompoundDecision] = list(exact_sets.decisions)
        used_rules: set[int] = set()
        source = multilingual.dialog.key.source.as_posix()
        dialog_ids = resource_selectors(multilingual.dialog.key.resource_id)
        location = (
            f"{source}:{multilingual.dialog.key.resource_id.display_name}"
        )

        for candidate in candidates:
            matches = [
                rule
                for rule in self.rules.rules
                if rule.matches(
                    candidate,
                    source=source,
                    dialog_ids=dialog_ids,
                )
            ]
            selected = _select_rule(matches)
            if selected is None and matches:
                leaders = _leading_rules(matches)
                diagnostics.append(
                    Diagnostic(
                        code="semantic-map.ambiguous",
                        severity=Severity.ERROR,
                        message=(
                            f"compound {candidate.kind.value} for "
                            f"{candidate.primary_ids[0]!r} matches equally "
                            "specific rules: "
                            + ", ".join(repr(rule.name) for rule in leaders)
                        ),
                        location=location,
                    )
                )
                decisions.append(
                    CompoundDecision(
                        candidate=candidate,
                        action=CompoundAction.KEEP,
                        conflict="ambiguous semantic rules",
                    )
                )
                continue
            if selected is None:
                decisions.append(
                    CompoundDecision(
                        candidate=candidate,
                        action=candidate.default_action,
                    )
                )
                continue
            used_rules.add(selected.index)
            decision = CompoundDecision(
                candidate=candidate,
                action=selected.action,
                rule_name=selected.name,
                rule_index=selected.index,
                rule_priority=selected.priority,
                result_class=selected.result_class,
                properties=selected.properties,
                runtime_configured=selected.runtime_configured,
            )
            decisions.append(decision)
            if (
                decision.action is CompoundAction.REPLACE
                and not decision.runtime_configured
                and decision.result_class in {"QSpinBox", "QDoubleSpinBox"}
            ):
                property_names = {name for name, _ in decision.properties}
                if not {"minimum", "maximum"} <= property_names:
                    diagnostics.append(
                        Diagnostic(
                            code="semantic.range-unspecified",
                            severity=Severity.WARNING,
                            message=(
                                f"rule {selected.name!r} replaces controls with "
                                f"{decision.result_class} without both minimum "
                                "and maximum; set them or use "
                                "runtime_configured = true"
                            ),
                            location=location,
                        )
                    )

        decisions = _resolve_active_conflicts(
            decisions,
            diagnostics,
            location=location,
        )
        return SemanticPlan(
            decisions=tuple(decisions),
            diagnostics=tuple(diagnostics),
            used_rule_indices=tuple(sorted(used_rules)),
            used_control_rule_keys=exact_sets.used_rule_keys,
        )


def _select_rule(rules: list[SemanticRule]) -> SemanticRule | None:
    leaders = _leading_rules(rules)
    return leaders[0] if len(leaders) == 1 else None


def _leading_rules(rules: list[SemanticRule]) -> list[SemanticRule]:
    if not rules:
        return []
    best_key = max((rule.priority, rule.specificity) for rule in rules)
    return [
        rule
        for rule in rules
        if (rule.priority, rule.specificity) == best_key
    ]


def _resolve_active_conflicts(
    decisions: list[CompoundDecision],
    diagnostics: list[Diagnostic],
    *,
    location: str,
) -> list[CompoundDecision]:
    ranked = sorted(
        enumerate(decisions),
        key=lambda item: (
            not item[1].explicit,
            -item[1].rule_priority,
            -item[1].candidate.confidence,
            item[1].candidate.primary_order,
            item[1].candidate.kind.value,
        ),
    )
    occupied: dict[int, CompoundDecision] = {}
    result = list(decisions)
    for index, decision in ranked:
        # Logical bundles deliberately stay in the shared coordinate grid, so
        # they may overlap other associations. Only many-to-one replacements
        # consume controls and therefore conflict.
        if decision.action is not CompoundAction.REPLACE:
            continue
        conflicts = {
            occupied[order]
            for order in decision.candidate.orders
            if order in occupied
        }
        if not conflicts:
            occupied.update(
                (order, decision) for order in decision.candidate.orders
            )
            continue
        explicit_conflict = decision.explicit and any(
            other.explicit for other in conflicts
        )
        other = min(
            conflicts,
            key=lambda item: (
                item.candidate.primary_order,
                item.candidate.kind.value,
            ),
        )
        if explicit_conflict:
            diagnostics.append(
                Diagnostic(
                    code="semantic.compound-conflict",
                    severity=Severity.ERROR,
                    message=(
                        f"compound {decision.candidate.kind.value} overlaps "
                        f"explicit {other.candidate.kind.value}; leaving the "
                        "former unchanged"
                    ),
                    location=location,
                )
            )
        result[index] = replace(
            decision,
            action=CompoundAction.KEEP,
            conflict=f"overlaps {other.candidate.kind.value}",
        )
    return result
