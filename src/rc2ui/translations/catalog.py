from __future__ import annotations

import locale
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, replace
from pathlib import Path

from rc2ui.domain.diagnostics import Diagnostic, Severity
from rc2ui.translations.model import TranslationMessage


@dataclass(frozen=True, slots=True)
class CatalogWriteResult:
    paths: tuple[Path, ...]
    diagnostics: tuple[Diagnostic, ...]


def write_translation_catalogs(
    messages: tuple[TranslationMessage, ...],
    output_dir: Path,
    *,
    include_disambiguation: bool = True,
) -> CatalogWriteResult:
    if not include_disambiguation:
        messages = tuple(replace(message, comment="") for message in messages)
    diagnostics: list[Diagnostic] = []
    paths: list[Path] = []
    by_language: dict[int, list[TranslationMessage]] = {}
    for message in messages:
        by_language.setdefault(message.language, []).append(message)
    for language, language_messages in sorted(by_language.items()):
        merged = _merge_messages(language_messages, diagnostics)
        locale_name = windows_language_name(language)
        token = locale_name or f"langid_{language:04X}"
        safe_token = re.sub(r"[^A-Za-z0-9_.-]+", "_", token)
        path = output_dir / "translations" / f"rc2ui_{safe_token}.ts"
        if locale_name is None:
            diagnostics.append(
                Diagnostic(
                    code="translation.unknown-language",
                    severity=Severity.WARNING,
                    message=(
                        f"Windows LANGID {language} has no known locale name; "
                        f"TS file uses {safe_token!r} in its filename"
                    ),
                    location=str(path),
                )
            )
        text = emit_ts(merged, language=language)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(path.name + ".tmp")
            temporary.write_text(text, encoding="utf-8", newline="\n")
            temporary.replace(path)
        except OSError as error:
            diagnostics.append(
                Diagnostic(
                    code="translation.write-error",
                    severity=Severity.ERROR,
                    message=str(error),
                    location=str(path),
                )
            )
            continue
        paths.append(path)
    return CatalogWriteResult(tuple(paths), tuple(diagnostics))


def emit_ts(
    messages: tuple[TranslationMessage, ...],
    *,
    language: int,
) -> str:
    attributes = {"version": "2.1"}
    if target_name := windows_language_name(language):
        attributes["language"] = target_name
    source_languages = {message.source_language for message in messages}
    if len(source_languages) == 1:
        source_name = windows_language_name(next(iter(source_languages)))
        if source_name:
            attributes["sourcelanguage"] = source_name
    root = ET.Element("TS", attributes)
    contexts: dict[str, list[TranslationMessage]] = {}
    for message in messages:
        contexts.setdefault(message.context, []).append(message)
    for context_name, context_messages in sorted(contexts.items()):
        context = ET.SubElement(root, "context")
        _text_child(context, "name", context_name)
        for message in sorted(
            context_messages,
            key=lambda item: (item.source, item.comment, item.location),
        ):
            element = ET.SubElement(context, "message")
            ET.SubElement(
                element,
                "location",
                {"filename": message.location},
            )
            _text_child(element, "source", message.source)
            if message.comment:
                _text_child(element, "comment", message.comment)
            _text_child(element, "extracomment", message.extra_comment)
            _text_child(element, "translation", message.translation)
    ET.indent(root, space=" ")
    body = ET.tostring(root, encoding="unicode", short_empty_elements=True)
    return '<?xml version="1.0" encoding="utf-8"?>\n' + body + "\n"


def windows_language_name(language: int) -> str | None:
    return locale.windows_locale.get(language)


def _merge_messages(
    messages: list[TranslationMessage],
    diagnostics: list[Diagnostic],
) -> tuple[TranslationMessage, ...]:
    merged: dict[tuple[str, str, str], TranslationMessage] = {}
    for message in messages:
        key = message.context, message.source, message.comment
        previous = merged.get(key)
        if previous is None:
            merged[key] = message
            continue
        if previous.translation != message.translation:
            diagnostics.append(
                Diagnostic(
                    code="translation.conflict",
                    severity=Severity.ERROR,
                    message=(
                        f"translation key {message.comment!r} has conflicting "
                        f"LANGID {message.language} texts"
                    ),
                    location=message.location,
                )
            )
    # Comments include form/object/property identity, so identical keys refer
    # to the same UI property. The first stable location is sufficient.
    return tuple(message for _, message in sorted(merged.items()))


def _text_child(parent: ET.Element, tag: str, value: str) -> None:
    child = ET.SubElement(parent, tag)
    child.text = value
