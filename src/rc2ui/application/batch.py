from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import asdict, replace
from pathlib import Path, PurePosixPath

from rc2ui.adapters.res.dialog_template import DialogTemplateError, parse_dialog
from rc2ui.analysis.multilingual import (
    DefaultLanguageUnavailable,
    MultilingualDialog,
    fuse_dialog_languages,
)
from rc2ui.application.input_groups import InputGroupLoader
from rc2ui.application.models import (
    BatchResult,
    CompoundArtifact,
    ControlArtifact,
    ConversionRequest,
    FormArtifact,
    LanguageAlignmentArtifact,
    LayoutEvidenceArtifact,
    LayoutPolicyArtifact,
    RelationEvidenceArtifact,
)
from rc2ui.application.output_names import (
    OutputNameAllocator,
    OutputNameCollision,
)
from rc2ui.domain.diagnostics import Diagnostic, Severity
from rc2ui.domain.dialog import Dialog
from rc2ui.domain.geometry import RectDlu
from rc2ui.domain.resource_id import ResourceId
from rc2ui.layout.gap_growth import apply_gap_growth
from rc2ui.layout.infer import LayoutBuilder
from rc2ui.layout.mode import LayoutMode
from rc2ui.layout.policy import LayoutPolicy
from rc2ui.layout.simplify import editability_score, simplify_form
from rc2ui.mapping.controls import ControlMapper
from rc2ui.mapping.overrides import ControlMap
from rc2ui.naming.map import NamingKind, NamingMap
from rc2ui.naming.resolver import NameResolver, NameSource, NamingResult
from rc2ui.naming.suggestions import (
    NamingSuggestion,
    emit_naming_suggestions,
)
from rc2ui.qt.emitter import emit_ui
from rc2ui.qtcheck.model import (
    ControlGeometryReference,
    FormGeometryReference,
    QtCheckMode,
)
from rc2ui.qtcheck.runner import run_qt_checks
from rc2ui.semantics.config import SemanticMap
from rc2ui.semantics.engine import SemanticEngine
from rc2ui.semantics.model import SemanticPlan
from rc2ui.semantics.transform import apply_semantic_mapping
from rc2ui.translations.catalog import write_translation_catalogs
from rc2ui.translations.form import prepare_localized_form
from rc2ui.translations.model import TranslationMessage
from rc2ui.validation.ui_xml import UiValidationError, validate_ui_xml


class BatchConverter:
    def convert(self, request: ConversionRequest) -> BatchResult:
        diagnostics: list[Diagnostic] = []
        forms: list[FormArtifact] = []
        suggestions: list[NamingSuggestion] = []
        translation_messages: list[TranslationMessage] = []
        used_naming_rules: set[tuple[int, str | None]] = set()
        used_control_rules: set[str] = set()
        used_semantic_rules: set[int] = set()
        root = request.project_root.resolve()
        output_dir = _resolve_from(root, request.output_dir)
        output_names = OutputNameAllocator(output_dir)
        report_path = output_dir / "rc2ui-report.json"
        suggestions_path = output_dir / "rc2ui-name-suggestions.toml"

        naming_map = request.rules.naming
        control_map = request.rules.controls
        semantic_map = request.rules.semantics

        group_loader = InputGroupLoader(
            project_root=root,
            include_paths=tuple(
                _resolve_from(root, path) for path in request.include_paths
            ),
            predefined=dict(request.defines),
            rc_encoding=request.rc_encoding,
            default_language=request.default_language,
        )
        for input_group in request.input_groups:
            group_result = group_loader.load(input_group)
            diagnostics.extend(group_result.diagnostics)
            for dialog_input in group_result.dialogs:
                resource_id = dialog_input.variants[0].entry.resource_id
                resource_languages = tuple(
                    variant.entry.language
                    for variant in dialog_input.variants
                )
                if (
                    len(resource_languages) > 1
                    and request.default_language not in resource_languages
                ):
                    diagnostics.append(
                        Diagnostic(
                            code="language.default-unavailable",
                            severity=Severity.ERROR,
                            message=str(
                                DefaultLanguageUnavailable(
                                    request.default_language,
                                    resource_languages,
                                )
                            ),
                            location=(
                                f"{dialog_input.source}:"
                                f"{resource_id.display_name}"
                            ),
                        )
                    )
                    continue
                parsed: list[Dialog] = []
                for variant in dialog_input.variants:
                    try:
                        parsed.append(
                            parse_dialog(
                                variant.entry,
                                source=dialog_input.source,
                                symbols=dialog_input.symbols,
                            )
                        )
                    except DialogTemplateError as error:
                        diagnostics.append(
                            Diagnostic(
                                code="dialog.parse-error",
                                severity=Severity.ERROR,
                                message=str(error),
                                location=(
                                    f"{variant.container}@"
                                    f"{variant.entry.file_offset}"
                                ),
                            )
                        )
                if not parsed:
                    continue
                if len(resource_languages) > 1 and not any(
                    dialog.key.language == request.default_language
                    for dialog in parsed
                ):
                    # The requested resource existed but failed parsing. Its
                    # parse diagnostic is already present; never substitute a
                    # successfully parsed translation as the shared form.
                    continue
                try:
                    multilingual = fuse_dialog_languages(
                        tuple(parsed),
                        request.default_language,
                    )
                except DefaultLanguageUnavailable as error:
                    diagnostics.append(
                        Diagnostic(
                            code="language.default-unavailable",
                            severity=Severity.ERROR,
                            message=str(error),
                            location=(
                                f"{dialog_input.source}:"
                                f"{parsed[0].key.resource_id.display_name}"
                            ),
                        )
                    )
                    continue
                diagnostics.extend(multilingual.diagnostics)

                try:
                    layout_policy = request.layout_policies.resolve(
                        _layout_policy_candidates(
                            dialog_input.dialog_id,
                            multilingual.dialog.key.resource_id,
                        ),
                        mode=request.layout_mode,
                    )
                except ValueError as error:
                    diagnostics.append(
                        Diagnostic(
                            code="layout-policy.ambiguous",
                            severity=Severity.ERROR,
                            message=str(error),
                            location=(
                                f"{dialog_input.source}:"
                                f"{resource_id.display_name}"
                            ),
                        )
                    )
                    continue

                artifact = self._convert_dialog(
                    multilingual,
                    dialog_input.dialog_id,
                    layout_policy,
                    request.ui_comments,
                    naming_map,
                    diagnostics,
                    output_names,
                    output_dir,
                    suggestions,
                    translation_messages,
                    used_naming_rules,
                    control_map,
                    used_control_rules,
                    semantic_map,
                    used_semantic_rules,
                )
                if artifact:
                    forms.append(artifact)

        for rule in naming_map.rules:
            if rule.key not in used_naming_rules:
                diagnostics.append(
                    Diagnostic(
                        code="naming-map.unused-rule",
                        severity=Severity.WARNING,
                        message=(
                            f"naming rule {rule.display_name!r} "
                            "did not name any converted resource"
                        ),
                        location=(
                            _config_location(
                                request.config_path,
                                "naming",
                                rule.location_suffix,
                            )
                        ),
                    )
                )

        for rule in control_map.rules:
            if rule.key not in used_control_rules:
                diagnostics.append(
                    Diagnostic(
                        code="control-map.unused-rule",
                        severity=Severity.WARNING,
                        message=(
                            f"control rule {rule.display_name!r} "
                            "did not map any converted control"
                        ),
                        location=_config_location(
                            request.config_path,
                            "controls",
                            rule.location_suffix,
                        ),
                    )
                )

        for rule in control_map.compounds:
            if rule.key not in used_control_rules:
                diagnostics.append(
                    Diagnostic(
                        code="control-map.unused-compound",
                        severity=Severity.WARNING,
                        message=(
                            f"control compound {rule.name!r} did not match a "
                            "complete exact control set"
                        ),
                        location=_config_location(
                            request.config_path,
                            "controls",
                            rule.location_suffix,
                        ),
                    )
                )

        for rule in semantic_map.rules:
            if rule.index not in used_semantic_rules:
                diagnostics.append(
                    Diagnostic(
                        code="semantic-map.unused-rule",
                        severity=Severity.WARNING,
                        message=(
                            f"semantic rule {rule.name!r} did not match any "
                            "detected compound"
                        ),
                        location=_config_location(
                            request.config_path,
                            "semantics",
                            f"rules#{rule.index}",
                        ),
                    )
                )

        if any(
            form.layout_mode_requested == LayoutMode.SIMPLIFIED.value
            for form in forms
        ):
            simplified_form_count = sum(
                form.layout_mode_used == LayoutMode.SIMPLIFIED.value
                for form in forms
            )
            simplified_region_count = sum(
                form.simplified_regions for form in forms
            )
            fallback_region_count = sum(
                form.faithful_fallback_regions for form in forms
            )
            average_editability = sum(
                form.editability_score for form in forms
            ) / len(forms)
            diagnostics.append(
                Diagnostic(
                    code="layout.simplified",
                    severity=Severity.INFO,
                    message=(
                        f"simplified {simplified_region_count} layout "
                        f"region(s) in {simplified_form_count}/{len(forms)} "
                        f"form(s); kept {fallback_region_count} faithful "
                        "fallback region(s); average editability score "
                        f"{average_editability:.3f}"
                    ),
                )
            )

        catalog_run = write_translation_catalogs(
            tuple(translation_messages),
            output_dir,
            include_disambiguation=request.ui_comments,
        )
        diagnostics.extend(catalog_run.diagnostics)

        qt_report_path = None
        qt_preview_index = None
        if forms and (
            request.qt_check is not QtCheckMode.OFF
            or request.qt_preview_dir is not None
        ):
            preview_dir = (
                _resolve_from(root, request.qt_preview_dir)
                if request.qt_preview_dir is not None
                else None
            )
            qt_run = run_qt_checks(
                tuple(form.output for form in forms),
                report_path=output_dir / "rc2ui-qt-report.json",
                required=(
                    request.qt_check is QtCheckMode.REQUIRED
                    or preview_dir is not None
                ),
                preview_dir=preview_dir,
                ui_root=output_dir,
                font_scale=request.qt_font_scale,
                font_factors=request.validation.font_scales,
                size_factors=request.validation.resize_scales,
                geometry_references={
                    form.output: FormGeometryReference(
                        rect_dlu=form.default_rect_dlu,
                        layout_rect_dlu=form.layout_rect_dlu,
                        controls=tuple(
                            ControlGeometryReference(
                                object_name=control.object_name,
                                rect_dlu=control.rect_dlu,
                                layout_rect_dlu=control.layout_rect_dlu,
                                separator_orientation=(
                                    control.separator_orientation
                                ),
                                qt_class=control.qt_class,
                                horizontal_anchor=control.horizontal_anchor,
                                vertical_anchor=control.vertical_anchor,
                                alternative_states=control.alternative_states,
                            )
                            for control in form.controls
                            if control.emitted
                        ),
                    )
                    for form in forms
                },
            )
            diagnostics.extend(qt_run.diagnostics)
            qt_report_path = qt_run.report_path
            qt_preview_index = qt_run.preview_index

        result = BatchResult(
            forms=tuple(forms),
            diagnostics=tuple(diagnostics),
            report_path=report_path,
            suggestions_path=suggestions_path,
            translation_paths=catalog_run.paths,
            qt_report_path=qt_report_path,
            qt_preview_index=qt_preview_index,
        )
        self._write_metadata(result, request, suggestions)
        return result

    def _convert_dialog(
        self,
        multilingual: MultilingualDialog,
        source_dialog_id: str,
        layout_policy: LayoutPolicy,
        ui_comments: bool,
        naming_map: NamingMap,
        diagnostics: list[Diagnostic],
        output_names: OutputNameAllocator,
        output_dir: Path,
        suggestions: list[NamingSuggestion],
        translation_messages: list[TranslationMessage],
        used_naming_rules: set[tuple[int, str | None]],
        control_map: ControlMap | None,
        used_control_rules: set[str],
        semantic_map: SemanticMap,
        used_semantic_rules: set[int],
    ) -> FormArtifact | None:
        dialog = multilingual.dialog
        mapper = ControlMapper(
            control_map,
            text_width_safety_factor=(
                layout_policy.text_width_safety_factor
            ),
        )
        try:
            mapped = tuple(mapper.map(control) for control in dialog.controls)
        except ControlMapError as error:
            diagnostics.append(
                Diagnostic(
                    code="control-map.ambiguous",
                    severity=Severity.ERROR,
                    message=str(error),
                    location=(
                        f"{dialog.key.source}:"
                        f"{dialog.key.resource_id.display_name}"
                    ),
                )
            )
            return None
        used_control_rules.update(
            mapped_control.mapping_rule_key
            for mapped_control in mapped
            if mapped_control.mapping_rule_key is not None
        )
        semantic_plan = SemanticEngine(semantic_map, control_map).analyze(
            multilingual,
            mapped,
        )
        diagnostics.extend(semantic_plan.diagnostics)
        used_semantic_rules.update(semantic_plan.used_rule_indices)
        used_control_rules.update(semantic_plan.used_control_rule_keys)
        naming_mapped = apply_semantic_mapping(
            mapped,
            semantic_plan,
            for_naming=True,
        )
        naming = NameResolver(naming_map).resolve(dialog, naming_mapped)
        if naming.dialog.rule_key is not None:
            used_naming_rules.add(naming.dialog.rule_key)
        used_naming_rules.update(
            decision.rule_key
            for decision in naming.controls
            if decision.rule_key is not None
        )
        diagnostics.extend(naming.diagnostics)
        dialog_object_name = _qt_dialog_identifier(
            source_dialog_id,
            fallback=naming.dialog.object_name,
        )
        try:
            allocation = output_names.allocate(
                source=dialog.key.source,
                resource_id=dialog.key.resource_id,
                requested_name=dialog_object_name,
                explicit=(
                    dialog_object_name != source_dialog_id
                    and naming.dialog.source is NameSource.EXPLICIT
                ),
            )
        except OutputNameCollision as error:
            diagnostics.append(
                Diagnostic(
                    code="output.collision",
                    severity=Severity.ERROR,
                    message=str(error),
                    location=(
                        f"{dialog.key.source}:"
                        f"{dialog.key.resource_id.display_name}"
                    ),
                )
            )
            return None
        if allocation.was_disambiguated:
            assert allocation.conflicting_owner is not None
            original_name = dialog_object_name
            dialog_object_name = allocation.object_name
            naming = replace(
                naming,
                dialog=replace(
                    naming.dialog,
                    object_name=allocation.object_name,
                    evidence=naming.dialog.evidence
                    + (
                        "disambiguated automatic output name using resource ID",
                    ),
                ),
            )
            diagnostics.append(
                Diagnostic(
                    code="output.name-disambiguated",
                    severity=Severity.INFO,
                    message=(
                        f"automatic dialog name {original_name!r} is already used "
                        f"by {allocation.conflicting_owner.display_name}; using "
                        f"{allocation.object_name!r}"
                    ),
                    location=(
                        f"{dialog.key.source}:"
                        f"{dialog.key.resource_id.display_name}"
                    ),
                )
            )
        # Geometry-dependent policies (fixed button width, toolbar minimums,
        # multiline height) must use the consensus rectangle that the layout
        # will actually emit. Text and styles remain those of the default
        # language because layout_dialog only replaces control rectangles.
        try:
            layout_mapped = tuple(
                mapper.map(control)
                for control in multilingual.layout_dialog.controls
            )
        except ControlMapError as error:
            diagnostics.append(
                Diagnostic(
                    code="control-map.ambiguous",
                    severity=Severity.ERROR,
                    message=str(error),
                    location=(
                        f"{dialog.key.source}:"
                        f"{dialog.key.resource_id.display_name}"
                    ),
                )
            )
            output_names.release(allocation)
            return None
        layout_mapped = apply_semantic_mapping(
            layout_mapped,
            semantic_plan,
        )
        layout = LayoutBuilder(
            coordinate_tolerance=layout_policy.alignment_tolerance_dlu,
            text_width_safety_factor=(
                layout_policy.text_width_safety_factor
            ),
            max_designer_width_factor=(
                layout_policy.max_designer_width_factor
            ),
            runtime_alternatives=layout_policy.runtime_alternatives,
        ).build(
            multilingual.layout_dialog,
            layout_mapped,
            naming,
            multilingual.layout_hints,
            semantic_plan,
        )
        diagnostics.extend(layout.diagnostics)
        if any(
            diagnostic.severity is Severity.ERROR
            for diagnostic in layout.diagnostics
        ):
            output_names.release(allocation)
            return None
        layout_mode_used = LayoutMode.FAITHFUL
        simplified_regions = 0
        faithful_fallback_regions = 0
        layout_transformations: tuple[str, ...] = ()
        if layout_policy.mode is LayoutMode.SIMPLIFIED:
            simplified = simplify_form(
                layout.root_widget,
                layout_policy.simplified,
                alignment_tolerance_dlu=(
                    layout_policy.alignment_tolerance_dlu
                ),
            )
            layout = replace(layout, root_widget=simplified.root_widget)
            simplified_regions = simplified.simplified_regions
            faithful_fallback_regions = simplified.faithful_fallback_regions
            layout_transformations = simplified.transformations
            if simplified_regions:
                layout_mode_used = LayoutMode.SIMPLIFIED
        layout = replace(
            layout,
            root_widget=apply_gap_growth(
                layout.root_widget,
                layout_policy.gap_growth,
            ),
        )
        editability = editability_score(layout.root_widget)
        relative_output = PurePosixPath(
            allocation.output.relative_to(output_dir).as_posix()
        )
        root_widget = replace(
            layout.root_widget,
            object_name=dialog_object_name,
        )
        localized_form = prepare_localized_form(
            root_widget,
            multilingual,
            mapped,
            naming,
            form_class=dialog_object_name,
            control_map=control_map,
            ui_path=relative_output,
            text_width_safety_factor=(
                layout_policy.text_width_safety_factor
            ),
        )
        diagnostics.extend(localized_form.diagnostics)
        if any(
            diagnostic.severity is Severity.ERROR
            for diagnostic in localized_form.diagnostics
        ):
            output_names.release(allocation)
            return None
        text = emit_ui(
            localized_form.root_widget,
            form_class=dialog_object_name,
            include_comments=ui_comments,
            tab_order=layout.tab_order,
        )
        try:
            validate_ui_xml(text)
        except UiValidationError as error:
            output_names.release(allocation)
            diagnostics.append(
                Diagnostic(
                    code="ui.invalid",
                    severity=Severity.ERROR,
                    message=str(error),
                    location=f"{dialog.key.source}:{dialog.key.resource_id.display_name}",
                )
            )
            return None

        output = allocation.output
        try:
            output.parent.mkdir(parents=True, exist_ok=True)
            temporary = output.with_name(output.name + ".tmp")
            temporary.write_text(text, encoding="utf-8", newline="\n")
            temporary.replace(output)
        except OSError as error:
            output_names.release(allocation)
            diagnostics.append(
                Diagnostic(
                    code="output.write-error",
                    severity=Severity.ERROR,
                    message=str(error),
                    location=str(output),
                )
            )
            return None

        translation_messages.extend(localized_form.messages)
        controls = tuple(
            ControlArtifact(
                rc_id=_selector(control.key.resource_id, dialog=False),
                occurrence=control.key.occurrence,
                win_class=control.class_name,
                object_name=naming.for_order(control.order).object_name,
                qt_class=naming_mapped[control.order].qt_class,
                style=f"0x{control.style:08x}",
                extended_style=f"0x{control.extended_style:08x}",
                rect_dlu=(
                    control.rect.x,
                    control.rect.y,
                    control.rect.width,
                    control.rect.height,
                ),
                layout_rect_dlu=_rect_tuple(
                    layout.rect_for(control.order)
                ),
                name_source=naming.for_order(control.order).source.value,
                confidence=naming.for_order(control.order).confidence,
                evidence=naming.for_order(control.order).evidence,
                separator_orientation=(
                    mapped[control.order].separator_orientation.value
                    if mapped[control.order].separator_orientation is not None
                    else None
                ),
                horizontal_anchor=layout.anchors_for(control.order)[0],
                vertical_anchor=layout.anchors_for(control.order)[1],
                alternative_states=layout.alternative_states_for(control.order),
                emitted=control.order not in semantic_plan.consumed_orders,
                compound_kind=_compound_kind_for_order(
                    semantic_plan,
                    control.order,
                ),
                mapping_rule=naming_mapped[control.order].mapping_rule,
                button_group=naming_mapped[control.order].button_group,
                runtime_configured=(
                    naming_mapped[control.order].runtime_configured
                ),
            )
            for control in dialog.controls
        )
        self._collect_suggestions(dialog, naming, controls, suggestions)
        return FormArtifact(
            source=dialog.key.source.as_posix(),
            rc_id=source_dialog_id,
            language=dialog.key.language or 0,
            object_name=dialog_object_name,
            output=output,
            controls=controls,
            available_languages=multilingual.available_languages,
            default_rect_dlu=(
                multilingual.default_dialog.rect.x,
                multilingual.default_dialog.rect.y,
                multilingual.default_dialog.rect.width,
                multilingual.default_dialog.rect.height,
            ),
            layout_rect_dlu=(
                layout.layout_bounds.x,
                layout.layout_bounds.y,
                layout.layout_bounds.width,
                layout.layout_bounds.height,
            ),
            geometry_languages=multilingual.geometry_languages,
            translation_languages=localized_form.translation_languages,
            language_alignments=tuple(
                LanguageAlignmentArtifact(
                    language=variant.language,
                    matched_controls=variant.matched_controls,
                    default_controls=len(multilingual.default_dialog.controls),
                    variant_controls=len(variant.dialog.controls),
                    confidence=round(variant.match_confidence, 4),
                )
                for variant in multilingual.variants
            ),
            layout_evidence=LayoutEvidenceArtifact(
                group_memberships=sum(
                    hint.parent_order is not None
                    for hint in multilingual.layout_hints.parents
                ),
                rejected_group_memberships=sum(
                    hint.parent_order is None
                    for hint in multilingual.layout_hints.parents
                ),
                row_alignments=len(multilingual.layout_hints.same_rows),
                column_alignments=len(multilingual.layout_hints.same_columns),
                overlaps=len(multilingual.layout_hints.overlaps),
                runtime_alternatives=len(
                    multilingual.layout_hints.alternatives
                ),
                rejected_runtime_alternatives=len(
                    multilingual.layout_hints.rejected_alternatives
                ),
            ),
            layout_relations=_relation_artifacts(multilingual, naming),
            compounds=_compound_artifacts(
                semantic_plan,
                dialog,
                naming,
            ),
            layout_mode_requested=layout_policy.mode.value,
            layout_mode_used=layout_mode_used.value,
            layout_policy=LayoutPolicyArtifact(
                alignment_tolerance_dlu=(
                    layout_policy.alignment_tolerance_dlu
                ),
                text_width_safety_factor=(
                    layout_policy.text_width_safety_factor
                ),
                max_designer_width_factor=(
                    layout_policy.max_designer_width_factor
                ),
                gap_growth=layout_policy.gap_growth.value,
                runtime_alternatives=(
                    layout_policy.runtime_alternatives.value
                ),
                simplified_profile=layout_policy.simplified.profile.value,
                max_serialized_tracks=(
                    layout_policy.simplified.max_serialized_tracks
                ),
            ),
            editability_score=editability,
            simplified_regions=simplified_regions,
            faithful_fallback_regions=faithful_fallback_regions,
            layout_transformations=layout_transformations,
        )

    def _collect_suggestions(
        self,
        dialog: Dialog,
        naming: NamingResult,
        controls: tuple[ControlArtifact, ...],
        suggestions: list[NamingSuggestion],
    ) -> None:
        dialog_selector = _selector(dialog.key.resource_id, dialog=True)
        if naming.dialog.source is not NameSource.EXPLICIT:
            suggestions.append(
                NamingSuggestion(
                    kind=NamingKind.DIALOG,
                    source_regex=re.escape(dialog.key.source.as_posix()),
                    dialog_regex=re.escape(dialog_selector),
                    source_id=dialog_selector,
                    occurrence=None,
                    object_name=naming.dialog.object_name,
                    confidence=naming.dialog.confidence,
                    derived_from=naming.dialog.source.value,
                )
            )
        repeated_ids = Counter(item.rc_id for item in controls)
        for artifact, decision in zip(controls, naming.controls):
            if decision.source is NameSource.EXPLICIT:
                continue
            suggestions.append(
                NamingSuggestion(
                    kind=NamingKind.CONTROL,
                    source_regex=re.escape(dialog.key.source.as_posix()),
                    dialog_regex=re.escape(dialog_selector),
                    source_id=artifact.rc_id,
                    occurrence=(
                        artifact.occurrence
                        if repeated_ids[artifact.rc_id] > 1
                        else None
                    ),
                    object_name=artifact.object_name,
                    confidence=artifact.confidence,
                    derived_from=decision.source.value,
                )
            )

    def _write_metadata(
        self,
        result: BatchResult,
        request: ConversionRequest,
        suggestions: list[NamingSuggestion],
    ) -> None:
        result.report_path.parent.mkdir(parents=True, exist_ok=True)
        report = {
            "summary": {
                "forms": len(result.forms),
                "errors": result.error_count,
                "warnings": result.warning_count,
                "translation_catalogs": len(result.translation_paths),
            },
            "configuration": (
                str(request.config_path) if request.config_path else None
            ),
            "layout_mode": request.layout_mode.value,
            "layout": {
                "default": asdict(request.layout_policies.default),
                "overrides": [
                    asdict(override)
                    for override in request.layout_policies.overrides
                ],
            },
            "ui_comments": request.ui_comments,
            "input_groups": [
                {
                    "rc": [str(path) for path in group.rc_files],
                    "resources": [str(path) for path in group.resource_files],
                    "dialogs": list(group.dialog_selection.exact),
                    "dialog_regex": list(group.dialog_selection.regex),
                }
                for group in request.input_groups
            ],
            "forms": [
                {
                    **asdict(form),
                    "output": str(form.output),
                }
                for form in result.forms
            ],
            "translations": [str(path) for path in result.translation_paths],
            "qt_check": {
                "font_scale": request.qt_font_scale,
                "font_scales": list(request.validation.font_scales),
                "resize_scales": list(request.validation.resize_scales),
                "report": (
                    str(result.qt_report_path) if result.qt_report_path else None
                ),
                "preview_index": (
                    str(result.qt_preview_index)
                    if result.qt_preview_index
                    else None
                ),
            },
            "diagnostics": [asdict(item) for item in result.diagnostics],
        }
        report_text = json.dumps(
            report,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ) + "\n"
        temporary_report = result.report_path.with_name(result.report_path.name + ".tmp")
        temporary_report.write_text(report_text, encoding="utf-8", newline="\n")
        temporary_report.replace(result.report_path)

        temporary_suggestions = result.suggestions_path.with_name(
            result.suggestions_path.name + ".tmp"
        )
        temporary_suggestions.write_text(
            emit_naming_suggestions(tuple(suggestions)),
            encoding="utf-8",
            newline="\n",
        )
        temporary_suggestions.replace(result.suggestions_path)


def _resolve_from(root: Path, path: Path) -> Path:
    return path if path.is_absolute() else root / path


def _layout_policy_candidates(
    source_dialog_id: str,
    resource_id: ResourceId,
) -> tuple[str, ...]:
    values = [source_dialog_id, resource_id.display_name]
    values.extend(resource_id.symbols)
    if resource_id.ordinal is not None:
        values.append(f"#{resource_id.ordinal}")
    if resource_id.name is not None:
        values.append(resource_id.name)
    return tuple(dict.fromkeys(values))


def _config_location(
    config_path: Path | None,
    section: str,
    suffix: str,
) -> str:
    prefix = f"{config_path}:" if config_path is not None else ""
    return f"{prefix}{section}.{suffix}"


def _rect_tuple(rect: RectDlu) -> tuple[int, int, int, int]:
    return rect.x, rect.y, rect.width, rect.height


def _compound_kind_for_order(
    plan: SemanticPlan,
    order: int,
) -> str | None:
    matching = [
        decision
        for decision in plan.decisions
        if order in decision.candidate.orders
    ]
    if not matching:
        return None
    decision = next((item for item in matching if item.active), matching[0])
    return decision.candidate.kind.value


def _compound_artifacts(
    plan: SemanticPlan,
    dialog: Dialog,
    naming: NamingResult,
) -> tuple[CompoundArtifact, ...]:
    by_order = {control.order: control for control in dialog.controls}
    return tuple(
        CompoundArtifact(
            kind=decision.candidate.kind.value,
            action=decision.action.value,
            primary_order=decision.candidate.primary_order,
            orders=decision.candidate.orders,
            source_ids=tuple(
                _selector(by_order[order].key.resource_id, dialog=False)
                for order in decision.candidate.orders
            ),
            object_names=tuple(
                naming.for_order(order).object_name
                for order in decision.candidate.orders
            ),
            confidence=round(decision.candidate.confidence, 4),
            evidence=decision.candidate.evidence,
            supporting_languages=decision.candidate.supporting_languages,
            eligible_languages=decision.candidate.eligible_languages,
            geometry=decision.candidate.geometry.value,
            rule_name=decision.rule_name,
            result_class=decision.result_class,
            conflict=decision.conflict,
        )
        for decision in plan.decisions
    )


def _relation_artifacts(
    multilingual: MultilingualDialog,
    naming: NamingResult,
) -> tuple[RelationEvidenceArtifact, ...]:
    result: list[RelationEvidenceArtifact] = []
    for hint in multilingual.layout_hints.parents:
        evidence_parent = (
            hint.parent_order
            if hint.parent_order is not None
            else hint.tested_parent_order
        )
        orders = (
            (hint.order, evidence_parent)
            if evidence_parent is not None
            else (hint.order,)
        )
        result.append(
            RelationEvidenceArtifact(
                relation=(
                    "group-membership"
                    if hint.parent_order is not None
                    else "group-membership-rejected"
                ),
                orders=orders,
                object_names=tuple(
                    naming.for_order(order).object_name for order in orders
                ),
                confidence=round(hint.confidence, 4),
                supporting_languages=hint.supporting_languages,
                eligible_languages=hint.eligible_languages,
            )
        )
    pair_relations = (
        ("same-row", multilingual.layout_hints.same_rows),
        ("same-column", multilingual.layout_hints.same_columns),
        ("significant-overlap", multilingual.layout_hints.overlaps),
        ("runtime-alternative", multilingual.layout_hints.alternatives),
        (
            "runtime-alternative-rejected",
            multilingual.layout_hints.rejected_alternatives,
        ),
    )
    for relation, hints in pair_relations:
        for hint in hints:
            result.append(
                RelationEvidenceArtifact(
                    relation=relation,
                    orders=hint.orders,
                    object_names=tuple(
                        naming.for_order(order).object_name
                        for order in hint.orders
                    ),
                    confidence=round(hint.confidence, 4),
                    supporting_languages=hint.supporting_languages,
                    eligible_languages=hint.eligible_languages,
                )
            )
    return tuple(
        sorted(
            result,
            key=lambda item: (item.relation, item.orders),
        )
    )


def _selector(resource_id: ResourceId, *, dialog: bool) -> str:
    preferred_prefix = "IDD_" if dialog else "IDC_"
    for symbol in resource_id.symbols:
        if symbol.startswith(preferred_prefix):
            return symbol
    if resource_id.symbols:
        return resource_id.symbols[0]
    if resource_id.ordinal is not None:
        return f"#{resource_id.ordinal}"
    assert resource_id.name is not None
    return resource_id.name


def _qt_dialog_identifier(dialog_id: str, *, fallback: str) -> str:
    """Use an RC identifier verbatim when it is valid in generated Qt code."""

    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", dialog_id):
        return dialog_id
    return fallback
