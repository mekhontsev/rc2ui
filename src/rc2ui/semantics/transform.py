from __future__ import annotations

from dataclasses import replace

from rc2ui.mapping.model import ControlRole, MappedControl
from rc2ui.qt.model import QtProperty, QtSizePolicy, QtString
from rc2ui.semantics.model import (
    CompoundAction,
    CompoundDecision,
    SemanticPlan,
    SemanticValue,
)


def apply_semantic_mapping(
    mapped_controls: tuple[MappedControl, ...],
    plan: SemanticPlan,
    *,
    for_naming: bool = False,
) -> tuple[MappedControl, ...]:
    """Apply result widget classes while retaining source control identity."""

    by_primary = {
        decision.candidate.primary_order: decision
        for decision in plan.active_decisions
        if decision.action is CompoundAction.REPLACE
    }
    consumed = plan.consumed_orders if for_naming else frozenset()
    result: list[MappedControl] = []
    for mapped in mapped_controls:
        order = mapped.control.order
        if decision := by_primary.get(order):
            assert decision.result_class is not None
            if decision.result_widget is not None:
                result.append(_profile_replacement(mapped, decision))
            else:
                result.append(
                    replace(
                        mapped,
                        qt_class=decision.result_class,
                        role=ControlRole.INPUT,
                        properties=_replacement_properties(
                            mapped,
                            decision.properties,
                            result_class=decision.result_class,
                        ),
                        expands_horizontally=True,
                        expands_vertically=False,
                        warning=None,
                        custom_widget=None,
                        separator_orientation=None,
                        button_group=None,
                        mapping_rule=None,
                        mapping_rule_key=None,
                        runtime_configured=(),
                    )
                )
        elif order in consumed:
            # Consumed controls still need names and provenance, but must not
            # compete with their replacement for a nearby QLabel buddy.
            result.append(
                replace(
                    mapped,
                    role=ControlRole.DECORATION,
                    properties=(),
                    warning=None,
                )
            )
        else:
            result.append(mapped)
    return tuple(result)


def _profile_replacement(
    source: MappedControl,
    decision: CompoundDecision,
) -> MappedControl:
    profile = decision.result_widget
    assert profile is not None
    properties = profile.properties
    if profile.text_property is not None and source.control.text is not None:
        properties = tuple(
            item for item in properties if item.name != profile.text_property
        ) + (
            QtProperty(profile.text_property, QtString(source.control.text)),
        )
    configured_names = {item.name for item in properties}
    properties += tuple(
        item
        for item in source.properties
        if item.name == "enabled" and item.name not in configured_names
    )
    return replace(
        source,
        qt_class=profile.qt_class,
        role=profile.role,
        properties=properties,
        expands_horizontally=profile.expands_horizontally,
        expands_vertically=profile.expands_vertically,
        warning=profile.warning,
        custom_widget=profile.custom_widget,
        separator_orientation=None,
        button_group=None,
        mapping_rule=decision.rule_name,
        mapping_rule_key=decision.result_rule_key,
        runtime_configured=decision.result_runtime_configured,
    )


def _replacement_properties(
    source: MappedControl,
    configured: tuple[tuple[str, SemanticValue], ...],
    *,
    result_class: str,
) -> tuple[QtProperty, ...]:
    properties: list[QtProperty] = [
        QtProperty("sizePolicy", QtSizePolicy("Ignored", "Fixed"))
    ]
    for property_ in source.properties:
        if property_.name in {"enabled", "readOnly"}:
            properties.append(property_)
    for name, value in configured:
        if (
            result_class == "QDoubleSpinBox"
            and name in {"minimum", "maximum", "singleStep", "value"}
            and isinstance(value, int)
            and not isinstance(value, bool)
        ):
            value = float(value)
        converted = (
            QtString(value, translatable=False)
            if isinstance(value, str)
            else value
        )
        properties = [item for item in properties if item.name != name]
        properties.append(QtProperty(name, converted))
    return tuple(properties)
