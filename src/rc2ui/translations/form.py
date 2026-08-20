from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import PurePosixPath

from rc2ui.analysis.multilingual import MultilingualDialog
from rc2ui.domain.diagnostics import Diagnostic, Severity
from rc2ui.domain.resource_id import ResourceId
from rc2ui.mapping.controls import ControlMapper
from rc2ui.mapping.model import MappedControl
from rc2ui.mapping.overrides import ControlMap, ControlMapError
from rc2ui.naming.resolver import NamingResult
from rc2ui.qt.model import (
    QtLayout,
    QtLayoutItem,
    QtProperty,
    QtString,
    QtWidget,
)
from rc2ui.translations.model import TranslationMessage


@dataclass(frozen=True, slots=True)
class PreparedLocalizedForm:
    root_widget: QtWidget
    messages: tuple[TranslationMessage, ...]
    diagnostics: tuple[Diagnostic, ...]
    translation_languages: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class _UiString:
    object_name: str
    property_name: str
    source: str
    comment: str
    extra_comment: str


def prepare_localized_form(
    root_widget: QtWidget,
    multilingual: MultilingualDialog,
    mapped_controls: tuple[MappedControl, ...],
    naming: NamingResult,
    *,
    form_class: str,
    control_map: ControlMap | None,
    ui_path: PurePosixPath,
    text_width_safety_factor: float = 1.1,
) -> PreparedLocalizedForm:
    identity = _dialog_identity(multilingual.dialog.key.resource_id)
    prefix = f"{multilingual.dialog.key.source.as_posix()}:{identity}"
    root_title_is_source_text = multilingual.default_dialog.caption is not None
    nontranslatable = (
        set()
        if root_title_is_source_text
        else {(root_widget.object_name, "windowTitle")}
    )
    strings: list[_UiString] = []
    annotated = _annotate_widget(
        root_widget,
        prefix,
        strings,
        nontranslatable,
    )
    context = form_class
    mapper = ControlMapper(
        control_map,
        text_width_safety_factor=text_width_safety_factor,
    )
    messages: list[TranslationMessage] = []
    diagnostics: list[Diagnostic] = []

    default_values = {
        (item.object_name, item.property_name): item for item in strings
    }
    control_object_names = {
        control.order: naming.for_order(control.order).object_name
        for control in multilingual.dialog.controls
    }
    canonical_classes = {
        control.control.order: control.qt_class for control in mapped_controls
    }

    for variant in multilingual.variants:
        target_values: dict[tuple[str, str], str] = {}
        if root_title_is_source_text and variant.dialog.caption is not None:
            target_values[(root_widget.object_name, "windowTitle")] = (
                variant.dialog.caption
            )
        incompatible = 0
        for order, object_name in control_object_names.items():
            target_control = variant.for_order(order)
            if target_control is None:
                continue
            try:
                target_mapped = mapper.map(target_control)
            except ControlMapError as error:
                incompatible += 1
                diagnostics.append(
                    Diagnostic(
                        code="translation.control-map-ambiguous",
                        severity=Severity.ERROR,
                        message=f"LANGID {variant.language}: {error}",
                        location=(
                            f"{multilingual.dialog.key.source}:"
                            f"{multilingual.dialog.key.resource_id.display_name}"
                        ),
                    )
                )
                continue
            if target_mapped.qt_class != canonical_classes[order]:
                incompatible += 1
                continue
            for property_ in target_mapped.properties:
                value = _translatable_text(property_.value)
                if value is not None:
                    target_values[(object_name, property_.name)] = value

        missing: list[_UiString] = []
        for key, source in sorted(
            default_values.items(),
            key=lambda item: (item[0][0], item[0][1]),
        ):
            if not source.source:
                continue
            target = target_values.get(key)
            if target is None:
                # A generated fallback title has no source string in Win32.
                if key == (root_widget.object_name, "windowTitle") and not (
                    root_title_is_source_text
                ):
                    continue
                missing.append(source)
                continue
            messages.append(
                TranslationMessage(
                    language=variant.language,
                    source_language=multilingual.default_language,
                    context=context,
                    source=source.source,
                    translation=target,
                    comment=source.comment,
                    extra_comment=source.extra_comment,
                    location=ui_path.as_posix(),
                )
            )
        if missing or incompatible:
            diagnostics.append(
                Diagnostic(
                    code="translation.incomplete",
                    severity=Severity.WARNING,
                    message=(
                        f"LANGID {variant.language}: {len(missing)} UI strings "
                        f"have no aligned translation; {incompatible} controls "
                        "map to a different Qt class"
                    ),
                    location=(
                        f"{multilingual.dialog.key.source}:"
                        f"{multilingual.dialog.key.resource_id.display_name}"
                    ),
                )
            )

    languages = tuple(sorted({message.language for message in messages}))
    return PreparedLocalizedForm(
        root_widget=annotated,
        messages=tuple(messages),
        diagnostics=tuple(diagnostics),
        translation_languages=languages,
    )


def _annotate_widget(
    widget: QtWidget,
    prefix: str,
    strings: list[_UiString],
    nontranslatable: set[tuple[str, str]],
) -> QtWidget:
    properties = tuple(
        _annotate_property(
            widget.object_name,
            property_,
            prefix,
            strings,
            nontranslatable,
        )
        for property_ in widget.properties
    )
    layout = (
        _annotate_layout(widget.layout, prefix, strings, nontranslatable)
        if widget.layout is not None
        else None
    )
    return replace(widget, properties=properties, layout=layout)


def _annotate_layout(
    layout: QtLayout,
    prefix: str,
    strings: list[_UiString],
    nontranslatable: set[tuple[str, str]],
) -> QtLayout:
    items = []
    for item in layout.items:
        if item.widget is not None:
            items.append(
                replace(
                    item,
                    widget=_annotate_widget(
                        item.widget,
                        prefix,
                        strings,
                        nontranslatable,
                    ),
                )
            )
        elif item.layout is not None:
            items.append(
                replace(
                    item,
                    layout=_annotate_layout(
                        item.layout,
                        prefix,
                        strings,
                        nontranslatable,
                    ),
                )
            )
        else:
            items.append(item)
    return replace(layout, items=tuple(items))


def _annotate_property(
    object_name: str,
    property_: QtProperty,
    prefix: str,
    strings: list[_UiString],
    nontranslatable: set[tuple[str, str]],
) -> QtProperty:
    if isinstance(property_.value, QtString):
        if not property_.value.translatable:
            return property_
        value = property_.value.value
    elif isinstance(property_.value, str):
        value = property_.value
    else:
        return property_
    if not value or (object_name, property_.name) in nontranslatable:
        return replace(
            property_,
            value=QtString(value, translatable=False),
        )
    comment = f"rc2ui:{prefix}:{object_name}.{property_.name}"
    extra_comment = f"Win32 {prefix}; {object_name}.{property_.name}"
    strings.append(
        _UiString(
            object_name,
            property_.name,
            value,
            comment,
            extra_comment,
        )
    )
    return replace(
        property_,
        value=QtString(
            value,
            comment=comment,
            extra_comment=extra_comment,
        ),
    )


def _translatable_text(value: object) -> str | None:
    if isinstance(value, QtString):
        return value.value if value.translatable else None
    return value if isinstance(value, str) else None


def _dialog_identity(resource_id: ResourceId) -> str:
    if resource_id.ordinal is not None:
        return f"#{resource_id.ordinal}"
    assert resource_id.name is not None
    return resource_id.name
