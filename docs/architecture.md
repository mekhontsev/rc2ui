# rc2ui architecture

This document defines the architecture and invariants used to convert Win32
dialog resources into scalable Qt 6 Designer forms.

The problem is broader than translating RC syntax to XML. A Win32 dialog
template stores absolute dialog-unit coordinates, while a useful Qt form must
express rows, columns, containment, alignment, spacing, stretch, and font
behavior. rc2ui therefore reconstructs layout intent from binary facts,
geometry, source metadata, and agreement between language variants.

## Goals

- process hundreds of source and compiled-resource files in one batch;
- extract every selected `RT_DIALOG`, not assume one form per file;
- generate one `.ui` for every logical dialog;
- use a requested default LANGID as the authoritative form and emit `.ts`
  catalogs for other LANGIDs;
- use additional language variants as layout evidence without letting them
  replace the default form's content;
- preserve clear horizontal and vertical alignment, containment, ordering, and
  whitespace through resize;
- tolerate small human coordinate errors without erasing real one-DLU gaps;
- support standard, common, and project-specific controls;
- provide deterministic, reviewable output with provenance;
- continue after a failure that is local to one form or input group.

## Non-goals

An RC resource describes the initial dialog template, not the complete runtime
UI. It cannot prove behavior implemented only in application code, including:

- controls created, moved, hidden, or resized by the dialog procedure;
- owner-draw rendering and custom message protocols;
- tab-page membership controlled at runtime;
- property sheets assembled from code;
- the condition selecting one of several overlapping controls;
- application-specific numeric range, validation, or formatting rules.

When the missing information changes meaning, rc2ui preserves a safe
layout-managed representation, emits evidence and diagnostics, and allows an
explicit project rule. It does not infer hidden business logic. See
[limitations.md](limitations.md).

## Input model

The application accepts logical input groups:

```text
InputGroup
  rc:        0..N source files
  resources: 1..N standalone RES or PE files
  selection: optional exact and regular-expression dialog-ID allowlist
```

One group defines one resource namespace. Several compiled containers may hold
different dialogs or different LANGID variants of the same dialog. Independent
modules that reuse numeric IDs for unrelated resources belong in separate
groups.

Selection is evaluated against the compiled resource identity and every
symbolic alias recovered from the group's RC sources. It happens before
language fusion and dialog parsing; selecting one logical resource retains all
of its LANGID variants. An absent selection accepts the complete group.

### Authority split

Compiled resources are authoritative for facts that survived the resource
compiler:

- resource type, ordinal or name, and LANGID;
- `DLGTEMPLATE` or `DLGTEMPLATEEX` structure;
- control class, style, extended style, and source order;
- DLU rectangles;
- dialog and control strings;
- dialog font, help IDs, and creation data.

RC files and reachable headers recover source-only context:

- symbolic aliases for numeric IDs;
- actual `DIALOG` and `DIALOGEX` declarations;
- the `LANGUAGE` active at each declaration;
- declaration ownership when headers are shared;
- project include roots and explicitly supplied preprocessor definitions.

The group is expected to be coherent: its compiled resources should have been
built from the supplied RC sources with compatible includes and defines.

### Standalone RES

The RES adapter walks aligned records, decodes ordinal or UTF-16 type and name
identifiers, selects `RT_DIALOG`, and passes dialog-template payloads to the
shared parser. It validates boundaries before decoding payload structures.

### PE32 and PE32+

The PE adapter parses DOS and NT headers, optional-header variants, section
tables, and the resource directory. Resource data RVAs are resolved through
section mappings with declared-range checks. Only `RT_DIALOG` leaves and their
LANGID levels are decoded.

The code path is architecture-neutral for x86, x64, and ARM64. Modules are
treated as byte containers and are never loaded or executed. Missing resource
directories are valid empty inputs; malformed or out-of-range trees are local
read errors.

Input format is identified by content signature, not filename extension. This
allows PE modules with project-specific extensions while rejecting a mislabeled
or malformed container accurately.

### Dialog templates

Standard and extended dialog formats produce one domain model:

```text
Dialog
  key: DialogKey(source, resource_id, language)
  rect: RectDlu
  caption, style, extended_style, font, help_id
  controls: tuple[Control, ...]

Control
  key: ControlKey(dialog, resource_id, occurrence)
  class_name, text
  style, extended_style
  rect: RectDlu
  order, help_id, creation_data
```

`ResourceId` retains compiled identity and every recovered symbolic alias.
Repeated identifiers, especially `IDC_STATIC`, are distinguished by
`occurrence` without claiming that occurrence is a semantic identity across
languages.

### Source declarations and preprocessing

The source indexer follows reachable quoted RC, RC2, DLG, and header includes.
It records actual dialog declarations and the language active at the declaration
site. A shared `#define IDD_X` is symbol evidence, not proof that every including
RC owns `IDD_X`.

Resource scripts frequently depend on build-system macros that are unavailable
during conversion. If an unresolved identifier or unevaluable macro affects a
condition in `.rc`, `.rc2`, or `.dlg`, the entire condition is treated as true.
An unresolved `#ifdef` in those files is treated the same way. This policy is
limited to resource scripts so externally gated dialog and language declarations
remain discoverable.

Headers keep conventional semantics: an unknown identifier evaluates as zero,
and an unsupported condition does not become active. Explicit `--define`
values always retain exact preprocessor meaning. Assumptions are aggregated per
source file as `symbols.condition-assumed-true` rather than flooding the log.

Known platform headers may be absent on the conversion machine and are excluded
from missing-project-include warnings. Missing project headers remain visible.

### Group resolution

`InputGroupLoader` prepares inputs without performing layout work:

1. load a separate symbol table for every top-level RC;
2. index reachable dialog declarations and their languages;
3. read each compiled container once;
4. group dialogs by compiled ordinal or name, then LANGID;
5. deduplicate byte-identical variants;
6. reject conflicting payloads for the same ID and LANGID;
7. choose the source declaration corresponding to the requested default
   language;
8. use unique `IDD_*` symbol evidence only as a fallback;
9. partition ambiguous sets by complete, uniquely recognized language sections
   when direct numeric linkage was hidden by header conditions.

File order never resolves ambiguous ownership. The user must separate unrelated
resource namespaces into different input groups.

## Application pipeline

```text
CLI or unified project configuration
  -> InputGroupLoader
  -> RES/PE adapters
  -> dialog-template parser
  -> RC declaration and symbol association
  -> logical dialog and LANGID grouping
  -> multilingual alignment and topology selection
  -> Win32-to-Qt control mapping
  -> compound-control analysis and explicit semantic policy
  -> object-name resolution
  -> container, separator, anchor, and coordinate-grid inference
  -> runtime-alternative representation
  -> topology guard
  -> localized Qt model
  -> .ui, .ts, report, and suggestion emitters
  -> optional isolated Qt runtime validation
```

Each transition creates a typed model. Binary parsers do not import Qt layout
logic, name resolution does not mutate geometry, and the XML emitter makes no
inference decisions.

## Layers and dependencies

```text
domain
  geometry, resource IDs, dialogs, diagnostics

adapters
  RES/PE readers, dialog templates, RC text, declarations, symbols
     |
analysis
  geometry, labels, multilingual matching, topology validation
     |
mapping / naming / semantics
  control classification, project rules, names, compound decisions
     |
layout
  containers, regions, anchors, tracks, spans, alternatives, font scaling,
  optional topology-preserving simplification
     |
qt / translations
  Qt-neutral model and deterministic XML/catalog emitters
     |
application
  input groups, batch policy, outputs, reports

qtcheck and corpus
  optional external validation/orchestration around the application pipeline
```

The Qt model is a serialization model and does not require a Qt Python binding.
PyQt6 or PySide6 appears only in the optional `qtcheck` subsystem.

## Multilingual dialog fusion

Compiled variants of one resource ID are not immediately reduced to one
language. `analysis.multilingual` creates `MultilingualDialog`:

1. select the requested default variant (`1033` unless configured);
2. match unique non-static IDs directly when class/type compatibility permits;
3. build a complete cost matrix for remaining controls from class, type style,
   normalized center, and size;
4. solve global minimum-cost assignment with dummy slots so unmatched controls
   remain unmatched;
5. collect confidence-weighted votes for group parent, shared row, shared
   column, meaningful overlap, and runtime alternatives;
6. project matched rectangles into the default dialog coordinate system;
7. form median position proposals for temporary layout geometry;
8. send proposals to an independent topology selector;
9. keep the default `Dialog` unchanged as the authority for controls, text,
   styles, font, z-order, and form size.

Resource order is a tiny deterministic tie-break only. It cannot outweigh
incompatible geometry. This is essential when translators or resource editors
reorder repeated static controls.

### Topology-preserving geometry selection

There is no global maximum correction in DLU. A coordinated correction may be
large, including with only two variants, when it preserves:

- clear horizontal and vertical partial order;
- distant shared left, right, top, bottom, and center anchors;
- immediate same-row neighbour gaps and clear left/right gap affinity;
- source group-box containment;
- dialog bounds;
- control extents from the selected default language.

The topology selector evaluates proposals locally. A proposal that moves one
control into an adjacent row or changes its parent is reverted without losing
safe corrections for unrelated controls. Rejections are reported as
`language.topology-correction-rejected`.

The algorithm deliberately separates source geometry from layout geometry:
raw default rectangles remain available for validation, while selected
rectangles are the only geometry used to build the layout.

Variant-only and default-only controls generate structure-mismatch evidence.
Class or type-style disagreement generates control-mismatch evidence. An
unmatched variant never deletes a default control or contributes geometry to
it.

## Object naming

Naming has three independent sources:

1. explicit naming rules from the project configuration;
2. semantic evidence such as an associated label;
3. a stable symbolic or technical fallback.

Naming does not modify layout rectangles or Qt classes.

### Semantic names

`analysis.labels` associates labels and controls by geometry, class role, shared
row evidence, and absence of a stronger candidate. Resource order is only a
last tie-break. An associated label provides a shared semantic base for the
label and input, and the emitter creates a Qt buddy relationship.

Text is normalized by removing mnemonic markers, trailing punctuation, and
non-identifier characters. Unicode text is handled deterministically. Public
names remain stable across translations because automatic naming uses only the
authoritative default variant.

### Naming section

The unified TOML schema has one top-level version. Naming rules live in
`[[naming.rules]]` and may define:

- `kind = "dialog"` or `"control"`;
- `source_regex` and `dialog_regex` scopes;
- `id_regex` and `name_template`;
- `occurrence` for repeated IDs;
- `priority`;
- a compact `[naming.rules.names]` table for exact ID-to-name mappings.

Regex uses Python `re.fullmatch`. Resource matching considers symbolic aliases,
named values, and `#ordinal`. Source paths are project-relative POSIX paths.
Templates support full, numeric, and named captures, then pass through the
common Qt identifier normalizer.

Precedence is `(priority, exact, specificity)`. Exact entries beat regex rules
at equal priority; source, dialog, and occurrence constraints then determine
specificity. Equal leaders are errors. Duplicate matchers, rule names, invalid
regex, unknown fields, and missing template captures fail during loading.

Rule provenance is retained in naming evidence. Generated
`rc2ui-name-suggestions.toml` emits mergeable `[[naming.rules]]` entries for
review before they are added to the project configuration.

### Dialog-name collisions

An automatically derived dialog name may collide across source subsystems. The
output allocator appends a stable suffix derived from the resource ID. A
collision caused by explicit naming entries is an error because silently
changing a reviewed public name would violate the configuration.

## Win32 control mapping

The built-in mapper is based on compiled facts:

```text
Win32 class + style bits -> Qt class + properties + semantic role + size policy
```

Representative mappings include:

```text
Button / push styles        -> QPushButton
Button / check styles       -> QCheckBox
Button / radio styles       -> QRadioButton
Button / group-box style    -> QGroupBox
Edit / single line          -> QLineEdit
Edit / multiline            -> QTextEdit
ListBox                     -> QListWidget
ComboBox                    -> QComboBox
SysTreeView32               -> QTreeWidget
SysListView32 / report      -> QTableWidget
msctls_progress32           -> QProgressBar
msctls_trackbar32           -> QSlider
SysDateTimePick32           -> QDateTimeEdit
SysMonthCal32               -> QCalendarWidget
```

Text layout is a shared mapping concern rather than a checkbox-specific
exception. Source controls with native wrapping use the corresponding Qt
property. Win32 `BUTTON` controls with `BS_MULTILINE` need an adapter because
`QAbstractButton` has no `wordWrap` property: the mapper inserts stable word
breaks using the source width, Qt-class decoration width, mnemonic-aware glyph
estimates, and the source multiline height. Default and translated controls
pass through the same mapper independently, so each language receives its own
line breaks. The coordinate grid and font rulers then scale both the occupied
height and the surrounding relations after `FontChange`.

Semantic roles (`label`, `input`, `action`, `group`, `container`, and
`decoration`) are consumed by label association, compound analysis, and layout
inference. Unknown registered classes and owner-draw cases become
layout-managed `QWidget` placeholders with the original class in diagnostics.

The original RC directive name is not available in a compiled resource.
`CONTROL`, `PUSHBUTTON`, `AUTORADIOBUTTON`, and other shorthand forms reduce to
class and style representation. Rules therefore use the stable compiled facts
rather than source spelling.

## Project controls section

Project mappings use the `controls` table with four concepts:

- `[[controls.widgets]]`: reusable output widget profiles;
- `[[controls.rules]]`: regex, context, and style selectors;
- `[[controls.bindings]]`: compact exact Win32 class-and-ID conversions;
- `[[controls.compounds]]`: exact source-control sets replaced by one profile.

### Widget profiles

A profile defines:

- `name` and `qt_class`;
- semantic `role`;
- horizontal and vertical expansion behavior;
- `text_property` receiving the source string;
- constant Qt properties;
- optional `header`, `extends`, and `container` promoted-widget metadata;
- an optional mapping warning.

Scalar TOML properties become natural Qt XML types. Structured
`{ enum = "..." }` and `{ cstring = "..." }` values preserve Qt-specific
property encodings. Source text replaces a constant property of the same name
and remains translatable.

When `header` is present, the emitter creates a Designer `<customwidgets>`
entry. The project class is responsible for a compatible constructor, the
declared inheritance relationship, and configured Qt properties.

### Rules

A `[[controls.rules]]` entry has a unique `name`, references one widget profile,
and requires exactly one of `win_class` or `win_class_regex`. Optional
selectors are:

- `source_regex`;
- `dialog_regex`;
- `id_regex`;
- `occurrence`;
- `style_mask` and `style_value`;
- `priority`.

Rules may also define `button_group` and a list of `runtime_configured`
properties. All regular expressions use `re.fullmatch`; resource IDs are tested
through their aliases and numeric representation.

### Exact bindings

A `[[controls.bindings]]` block gives shared source/dialog scope and priority
to a list of inline entries:

```toml
[[controls.bindings]]
name = "reviewed-settings-controls"
dialog_regex = "IDD_SETTINGS"
priority = 200

controls = [
  { win_class = "Edit", id = "IDC_PATH", widget = "path-editor" },
  { win_class = "ProjectPlaceholder", id = "IDC_MODE_FAST", widget = "radio-button", button_group = "modeButtonGroup" },
]
```

Each entry expands to an internal exact rule but retains the binding name and
entry location for provenance and unused-rule diagnostics.

### Exact control compounds

A `[[controls.compounds]]` entry references the same reusable widget profile
but owns two or more exact source selectors. One selector is explicitly
`primary`; its ID continues through the normal naming resolver. The remaining
`members` are consumed only after the complete set resolves unambiguously.

```text
profile metadata
       |
exact primary + exact members -> control-set candidate -> replace
       |                                      |
       +-> primary naming rule                +-> union rectangle
```

Each selector requires compiled Win32 class and resource ID. Class comparison
is case-insensitive, while resource IDs use exact recovered aliases or numeric
identity. An optional occurrence resolves repeated IDs. Coordinates are not
selectors: they determine the union rectangle and subsequent grid placement,
so arbitrary RC statement order cannot prevent an explicitly reviewed set from
matching.

The detector verifies aligned members in every available language and records
supporting LANGIDs as evidence. The default-language set is authoritative
because it is also the emitted form. Missing, repeated, or duplicate members
never trigger a partial replacement.

Runtime selection maximizes compound priority and source/dialog specificity.
Equal leaders and overlaps are diagnosed rather than decided by TOML order. A
one-to-one control rule claiming any member is also an explicit ownership
conflict, so the source controls remain unchanged.

Selection maximizes:

```text
(priority, exact-binding, selector-specificity, checked-style-bits)
```

Equal leaders are errors rather than file-order choices. Duplicate matchers,
unknown widget profiles, invalid property types, invalid identifiers, and
unknown fields fail at load time.

### Runtime-configured controls and button groups

`runtime_configured = ["property", ...]` records a contract: application code
must set those properties after `setupUi`. The controls section does not invent
a placeholder value. The contract and matched rule are written to each control
artifact in the JSON report.

Mapped buttons may share `button_group = "groupObjectName"`. The Qt model stores
membership on each widget, and the emitter writes both the widget
`buttonGroup` attribute and a top-level `<buttongroups>` declaration. This is a
real `QButtonGroup`, not only a naming convention. A mapped project subclass
must derive from `QAbstractButton` when it participates in a button group.

An explicit one-to-one control-rule match is authoritative for that source
control and excludes it from heuristic compound detection. If an exact control
compound also names that member, the engine reports an ownership conflict
instead of silently preferring either rule.

Control mapping and object naming remain separate: selecting another Qt class
never changes `objectName` implicitly.

## Compound-control semantics

A single application concept may be represented by several resource controls.
This is distinct from class mapping: an `Edit` and `msctls_updown32` are valid
individual controls, but a project may want one `QSpinBox` or
`QDoubleSpinBox`.

The subsystem is split into:

```text
CompoundDetector    -> candidates + confidence + evidence
CompoundPolicy      -> suggest | keep | bundle | replace
CompoundTransformer -> visual model + source provenance
```

Initial detectors are:

```text
edit-updown   Edit + msctls_updown32
edit-browse   Edit + adjacent browse/action button
slider-value  trackbar + numeric edit or static value
list-actions  list/tree/table + action-button column
```

Class and style decide eligible participants. Pairing normally remains
geometric: cross-axis overlap, gap, enclosing group, orientation, and absence
of a stronger candidate. `UDS_AUTOBUDDY` is the deliberate exception because
WinAPI defines the preceding z-order control as the buddy. An immediately
preceding edit therefore creates an edit/up-down candidate even when the
up-down rectangle is only a placeholder. Matching geometry or runtime binding
across LANGIDs adds evidence; label and action text are evaluated across
aligned variants.

Policy actions have different structural costs:

- `suggest` records a candidate without changing output;
- `keep` explicitly vetoes conversion;
- `bundle` records a logical relationship but leaves both widgets in the shared
  coordinate grid;
- `replace` keeps the primary widget and consumes secondary controls.

Bundles deliberately avoid an extra wrapper. This preserves distant anchors
between a member and controls outside the pair. Many-to-one replacement of a
heuristic candidate is allowed only through an explicit `[[semantics.rules]]`
entry. An exact project-defined set instead uses `[[controls.compounds]]`; it
needs no geometric inference because every source member is named explicitly.

On replacement, the result rectangle is normally the union of member
rectangles. An auto-buddy up-down aligned left or right is different: WinAPI
positions it inside the buddy's original footprint and shrinks the buddy, so
that original edit rectangle becomes the replacement footprint. When the pair
is retained separately, the same runtime geometry is materialized before grid
inference so both widgets share their top and height. The primary ID determines
the object name, and associated label buddies redirect to the result. Secondary
controls remain in the report with `emitted = false`. The compound artifact
stores source IDs, source names, confidence, evidence, geometry policy,
supporting languages, matched rule, result class, and runtime contract.

`QSpinBox` and `QDoubleSpinBox` have a dangerous default range of `0..99`.
Replacement rules must set `minimum` and `maximum` or declare
`runtime_configured = true`. Floating-point properties use a dedicated Qt model
type and serialize as Designer `<double>` values.

## Layout reconstruction

Each visual container receives one canonical `QGridLayout`. Source DLU edges
become coordinate tracks, while intervals between edges become elastic gap
tracks. The grid stores both source topology and resize behavior.

### Evidence priority

The same priority applies to standard controls, project widgets, labels,
groups, separators, and runtime alternatives:

1. visible coordinates define containment, partial order, spans, and regions;
2. stable edge and center anchors correct small manual offsets;
3. multilingual agreement strengthens grouping, alignment, and layer evidence;
4. source order is z-order evidence for geometry-proven overlap and a final
   tie-break for otherwise identical rectangles.

Text semantics may name or associate controls, but cannot move grid tracks.
Class hints cannot override an incompatible source rectangle.

### Hard invariants

Layout construction and post-validation enforce:

- a clearly lower control cannot move into an earlier row;
- a clearly right-hand control cannot move to the left;
- a control cannot cross a separator region;
- a child cannot leave or enter a source-proven group without stronger
  evidence;
- source gaps cannot collapse and must participate in form growth;
- an accepted shared anchor cannot split during resize;
- independent runtime layers are not incorrectly treated as simultaneously
  visible peers.

The pre-emission topology guard rejects a form if normalization or track mapping
would collapse a clear source order. Decorative frames, independent separator
regions, and mutually exclusive layers are excluded from comparisons where
simultaneous order has no meaning.

### Coordinate normalization

Controls created by hand often differ by one or two DLU even when intended to
share an edge or center. The anchor analyzer clusters compatible coordinates,
but a candidate anchor is accepted only if it does not bridge adjacent source
rows, cross a region boundary, or contradict stronger edge evidence.

Global anchor clusters are proposals rather than commands. A second local pass
builds short same-row neighbour chains from non-overlapping controls. It rejects
only the conflicting horizontal snaps when they change a small source gap too
far or reverse clear affinity: for example, a unit caption that was closer to
the field on its right cannot become attached to the field on its left.
Coherent translation of the entire chain remains valid, so this constraint does
not freeze responsive layout geometry.

Row inference uses coherent components rather than unconstrained transitive
overlap. A tall control cannot join two ordinary rows merely because it overlaps
both. Group frames are not peer-row evidence. Vertical separators partition row
inference, and horizontal separators partition column inference.

Fixed-height peers in one Qt grid share a vertical cell when their centre is
within one DLU of the strongest competing edge anchor. This absorbs small RC
rounding differences between labels, edits, and combo boxes and keeps their
centres aligned as native widget heights change with the font. Cross-container
rows cannot share a cell, so sibling group layouts retain their strongest exact
edge guide instead.

After proven normalization, every remaining edge is retained exactly. A
one-DLU interval therefore stays a real grid track instead of disappearing into
a tolerance.

### Container hierarchy

Group-box containment is determined by geometry, not declaration order. A child
must fit inside the usable frame with a limited tolerance. A control that only
intersects the frame remains in the parent container. When several groups can
contain a child, the smallest compatible enclosing group wins, strengthened by
multilingual parent evidence.

The group widget receives its own nested layout. Its title and frame remain Qt
properties rather than occupying artificial source geometry.

Before those independent nested grids are populated, children of sibling group
boxes participate in a cross-container row pass. Only coherent rows containing
members from at least two sibling groups are normalized. This preserves a
global field guide across group boundaries without allowing one group's
internal rows to restructure another group.

Ordinary controls outside the declared client rectangle extend effective
layout bounds and generate a diagnostic instead of being clipped. A decorative
separator is different: native child-window painting clips its overlong axis to
the parent client, so rc2ui clips that axis too and reports
`layout.separator-clipped-to-client` without enlarging the form.

### Separators and panes

A thin or etched static line is classified as horizontal or vertical from
style and aspect ratio. It stays in the common coordinate grid with a full span
on its long axis. The line partitions alignment and row/column evidence so
controls on opposite sides cannot pull each other into one form layout.

Perpendicular separators form nested regions. A short decorative line that does
not divide the visual area remains an ordinary grid decoration and does not
create a false pane.

In simplified output, a substantial vertical separator can become the middle
column of a coarse three-column panel grid. Left and right content is grouped
into no more than five common vertical regions, preserving cross-pane
top/bottom relations when fonts grow without serializing every faithful gap as
a grid row. Consecutive overlap bands are grouped at strong empty cuts. The
vertical separator stays one widget spanning those rows. A spanning horizontal
separator is applied first, producing nested top/bottom regions and allowing a
footer button row to remain a normal horizontal layout. Ordinary controls that
genuinely cross a candidate boundary reject the split; one- or two-DLU authored
overshoot is tolerated. The rewrite is also rejected when another topology-safe
candidate has lower Designer friction.

### Tracks, anchors, and spans

The grid builder gathers left, center, and right anchors on the horizontal axis
and top, center, and bottom anchors on the vertical axis. Anchors are global
within a visual region, so distant rows retain a shared left or right boundary
even when intermediate controls do not participate.

Every widget receives a row, column, row span, column span, and optional Qt
alignment. Tall and wide controls retain their source extents across several
tracks. Mixed-height controls in one visual row can align by top, center, or
bottom without forcing equal heights.

Track minimums are derived from source DLU intervals. Stretch weights are
proportional to interval sizes, including empty regions. Expanding controls and
gaps therefore share additional space while fixed controls retain qualitative
placement.

The source dialog rectangle becomes a safe minimum rather than a fixed window
size. The form can grow, while compression below known source geometry is
prevented.

Qt Designer initially displays the root geometry serialized in the `.ui`,
before runtime size negotiation is visible to the user. A deterministic,
shared DLU text-capacity estimate therefore enlarges the serialized width for
single-line labels, group titles, check boxes, radio buttons, and buttons that
approach their RC capacity. The estimate accounts for class-specific
decoration and includes a 10% reserve for Win32/Qt metric differences. Growth
is proportional so grid relations remain unchanged, capped at 150%, and
disabled for tiny host-owned templates. The effective enlarged DLU width is
also encoded by the zero-height font ruler, keeping the text reserve
font-relative after a runtime `FontChange`.

### Layout strategies

`LayoutBuilder` always produces the faithful model first. This is the sole
RC-to-layout inference path and remains the default output. The optional
`layout.simplify` stage is a pure Qt-model transformation after naming,
multilingual fusion, compound handling, containment, anchors, and coordinate
tracks have already been resolved. It neither reparses RC data nor invents a
second geometry model.

The simplifier walks nested widgets and layouts from the inside out. For each
faithful `QGridLayout`, it proposes deterministic candidates in increasing
generality:

1. separator-defined panels where a substantial vertical boundary exists;
2. label/editor form rows;
3. one-dimensional box layouts;
4. a compact coordinate matrix;
5. editable vertical bands for a complex container;
6. a cleaned faithful grid.

A form row with a positive source gap uses a three-column grid rather than a
`QFormLayout`, because the latter's inter-column spacing is style-controlled
and fixed. Box-layout candidates include explicit gap spacers. Candidates are
wrapped in three proportional margin zones derived from the faithful tracks.

Before accepting a candidate, a topology guard compares every pair of semantic
items on both axes. It rejects changes to strict order, overlap, or required
equal starts and ends. Candidate structural cost must also be lower than the
faithful region. Rejection is local: the current container keeps a cleaned
faithful grid while already accepted descendants and unrelated containers are
unchanged.

Separator-panel candidates have an additional editability guard. They are not
selected merely because a long line exists: a lower-friction valid form,
matrix, band, or cleaned-grid candidate wins. This keeps simplified output from
becoming structurally worse on sparse or unusually shaped panes.

Inside a selected separator panel, a terminal region first tries a short row
layout and then a recursive guillotine decomposition. A cut is legal only where
source rectangles leave the axis empty, allowing one DLU of authored overlap
fuzz. Candidate cuts prefer children that can be expressed directly as a short
box layout or a compact guide matrix with at most five serialized tracks. This
preference keeps shared row/column guides inside one matrix instead of turning
aligned rows into independent boxes.
Small hand-authored edge offsets are snapped only within the same panel, while
edges participating in a touching control pair remain locked. Initially hidden
widgets receive a same-cell extent spacer when moved to a box layout, retaining
their source slot until runtime code shows them.

For a complex root, vertical-overlap components may become rows of a root
`QVBoxLayout`. Ordinary rows use box or compact semantic layouts. A fine
coordinate grid survives only within a genuinely complex local band, so it no
longer prevents dropping a new widget between the dialog's major rows in
Designer. Root and row gaps are explicit Minimum spacers. Their layout stretch
factors remain proportional to the source DLU intervals; the same is true for
one-dimensional box layouts. Consequently a genuinely long row or band stack
may retain a long stretch vector. It is structural data, not redundant
serialization: removing it freezes gaps during resize, while arbitrary nested
grouping changes Qt's size-hint negotiation. Separator-defined panes are the
exception: their stronger boundaries and source-empty cuts support the bounded
coarse-region decomposition described above. Compact form and matrix grids are
preferred when their topology is unambiguous. Irregular pane shapes and
irreducible overlap retain a compact or faithful local grid rather than merging
tracks merely to meet a numeric limit.

Compact-grid acceptance includes an extent test in addition to pairwise
topology. Center guides collected from unrelated rows can create tiny tracks
inside a wide or tall control; if the resulting weighted span is less than half
of either source extent, that compact candidate is rejected and a band layout
is considered. This is a form-independent test over source geometry and track
weights.

The extent fallback is conservative around height-for-width text and shared
alignment constraints. A dialog containing a wrapped label retains the local
compact candidate, since a nested box rewrite can change the top-level height
negotiation during `FontChange`. A region also retains the compact candidate
when three or more controls share a near guide, or two same-class controls have
left, right, or vertical-center guides within three DLU. These guards preserve
distant alignment and tolerate ordinary hand-authored coordinate jitter.

The simplified model keeps `QLayout::SetMinimumSize`, removes full-span floor
spacers and the height ruler, and retains a zero-height root width ruler. The
ruler has no mouse-hit area but ensures that the dialog minimum width grows
with `FontChange`; natural widget hints grow it vertically. Explicit
proportional tracks preserve source whitespace during resize. Optional Qt
validation loads the final XML, resizes it, changes its font in place, and
checks the same observable topology used for faithful output, including newly
introduced horizontal or vertical text clipping.

A final typed spacer-compaction pass distinguishes explicit source gaps,
extent markers, hidden-control extents, font floors, and trailing faithful
tracks. It does not treat a zero-sized `ExtentMarker` as a no-op: in a
three-zone grid that item makes otherwise empty proportional tracks participate
in Qt's surplus-space distribution. With proportional growth, only a wrapper
whose outer weights are literally zero is removed. Fixed-margin policies allow
the equivalent margins to move onto the inner layout; the fully fixed policy
also permits an exact repeated-gap sequence to become layout spacing. The
conservative profile bypasses compaction. This keeps the default post-pass
equivalence-gated rather than using visual-size heuristics.

The simplifier returns transformation counts, local-fallback counts, and a
normalized structural editability score. These values are serialized with each
form together with spacer transformation counts and a semantic breakdown of
the remaining spacers, so large batches can be audited without inspecting
Designer files one by one. Runtime Qt metrics never choose a candidate,
preserving deterministic output across machines.

### Runtime alternatives and z-order

Exact or near-exact overlap can represent controls selected by application
state. Geometry is primary evidence. Repeated order offsets, compatible classes,
and matching overlap across language variants strengthen the classification.
A single distant order coincidence cannot create a layer.

Proven alternatives are placed in one grid cell through a wrapper model that
retains meaningful internal subrectangles. Every control retains its source
identity and alternative-state membership. The converter does not choose the
visible state.

Compiled child controls without `WS_VISIBLE` are emitted with `visible=false`.
They remain present and named in the `.ui`, allowing application code to show
them later without exposing a runtime-only state initially.

Partial overlap that lacks layer structure remains a warning. Resource order is
reported as z-order evidence but does not rearrange non-overlapping controls or
container ownership.

### Dynamic font scaling

DLU geometry is font-relative. Pixel-only grid minimums would preserve the
initial screenshot but fail after an application-wide font change.

The faithful reference mode restores the relationship without application
runtime code:

- nested layouts use `QLayout::SetMinimumSize`;
- the root grid includes source-size floor spacers;
- zero-thickness non-translatable label rulers cover the full source width and
  height;
- ruler text is chosen so Qt font metrics scale its `sizeHint` with
  `FontChange`;
- their size is distributed through the same coordinate tracks as controls and
  gaps.

The simplified mode preserves the horizontal ruler at zero height and relies
on semantic layout size hints vertically, avoiding a full-form overlay in
Designer while retaining the font-change invariant.

The runtime validator changes the already loaded form's font rather than
reloading the UI. It then verifies text width and height, order, anchors, and
containment again. `faithful` remains the reference mode; simplification is
accepted only as a post-processing convenience and does not redefine the
conversion's geometry evidence.

## Qt model and emission

The Qt-neutral model contains:

```text
QtWidget
  class_name, object_name
  properties
  optional custom_widget
  optional button_group
  optional layout
  explicit non-layout children

QtLayout
  class_name, object_name
  properties, stretch, row_stretch
  coordinate minimum widths/heights
  QtLayoutItem[]

QtLayoutItem
  widget | layout | spacer
  row, column, row_span, column_span, alignment
```

Property values have explicit model types for strings, translatable strings,
enums, C strings, fonts, rectangles, sizes, size policies, numbers, and
booleans. This avoids emitter heuristics based on Python value spelling.

The XML emitter is deterministic. It serializes the model, collects promoted
widget declarations, resources, empty connections, and all used button groups.
It does not alter names, geometry, or layout decisions. The application
preserves the authoritative symbolic RC dialog ID as both the form `<class>`
and root widget `name`; `class="QDialog"` remains the Qt widget type. The same
form class is passed to translation emission as its Qt Linguist context.
When `ui_comments` is disabled, serialization alone removes string comment
attributes; layout and text models remain unchanged. Catalog emission removes
the corresponding disambiguation key so runtime translation lookup stays
consistent, while retaining translator-only source notes.

Keyboard traversal remains source-driven even though visual layout inference
is geometry-driven. The compiled dialog-template creation order is filtered by
`WS_TABSTOP` and serialized explicitly as Qt `<tabstops>`. Consequently,
faithful and simplified forms have the same keyboard order. A many-to-one
compound replacement occupies the first tab-stop position of any consumed
source member and is emitted only once.

`validation.ui_xml` parses every generated document before writing it and checks
the root structure, tab-stop references, and global uniqueness of widget,
layout, spacer, and button group names.

## Localization emission

The default-language Qt model is annotated with stable message context and
comments. For each aligned variant, the translation layer remaps source controls
through the same control map and verifies that the resulting Qt class remains
compatible with the canonical form.

Only translatable `QtString` values enter catalogs. Generated fallback window
titles and constant project properties can be marked non-translatable. Missing
strings, structural mismatches, and incompatible classes are reported without
substituting text from a random language.

One TS catalog is emitted per non-default locale. Conflicts are diagnostics;
identical messages are deduplicated deterministically.

## Optional Qt runtime validation

`qtcheck` is intentionally outside conversion inference. It validates emitted
forms but does not modify them or create a feedback loop.

### Binding discovery and isolation

PyQt6 and PySide6 are optional. The binding discovery layer imports neither
during ordinary conversion. Validation launches an isolated subprocess using
the offscreen Qt platform so a crash, plugin failure, or malformed custom widget
does not terminate the batch process.

PyQt6 forms are compiled through `uic.compileUi` and loaded through `loadUi`.
PySide6 forms are loaded through `QUiLoader` for compile/load and runtime passes.
Promoted widgets are recursively substituted with their declared base classes
in a temporary validation copy.

### Runtime checks

Layouts are activated at a smaller size, source size, and larger size. The
worker records root-relative and local geometry for nested widgets and checks:

- root layout and expected object creation;
- label buddy targets;
- zero-size controls and bounds;
- minimum-size-hint and horizontal or vertical text clipping;
- serialized Designer geometry smaller than the activated layout hint;
- unexpected overlap;
- expansion on each declared expanding axis;
- source-relative spatial drift and collapse;
- clear horizontal and vertical partial order;
- generator-selected two-or-more-control anchor groups;
- source group parent;
- separator orientation and side membership;
- local same-row neighbour-gap affinity;
- source-gap preservation and growth.

After baseline checks, the worker increases the form font by a twofold factor
through a real `FontChange`, activates layouts again, and repeats clipping and
qualitative-order checks.

Controls from different layers of one explicit runtime-alternative group are
not compared as simultaneously visible. Independent groups and ordinary
controls remain comparable. This exception comes from generator provenance and
does not hide ordinary overlaps.

Text-clipping diagnostics include actual and required content sizes. Moderate
platform-dependent drift and an undersized serialized Designer canvas are
warnings. Radical displacement, size collapse, order inversion, lost anchor,
lost parent, reversed local gap affinity, collapsed elastic gap, or separator
crossing is an error.

### Reports and previews

`rc2ui-qt-report.json` records checked sizes, runtime geometries, size hints,
policies, font metrics, text advance, Qt and binding versions, platform plugin,
style, DPI, and device-pixel ratio. It explicitly states whether source geometry
was available and how many source controls were matched.

Diagnostics are aggregated by code in console output but remain complete per
form in JSON. Optional PNG previews and an HTML gallery use the same isolated
runtime and never change source `.ui` files. Validation without previews uses
Qt's offscreen platform by default. Preview workers prefer an available native
desktop platform for its real font stack while marking their widgets with
`WA_DontShowOnScreen`, so batch capture does not display hundreds of windows.

## External corpus subsystem

`rc2ui.corpus` is an orchestration layer around the main application. It never
introduces a second direct RC-to-layout path: a real external resource compiler
must first create a compiled resource.

### Discovery

Discovery expands directories containing immediate Git repositories, scans
RC/RC2/DLG include graphs, and classifies files as runnable roots, language
fragments, dialog fragments, non-dialog sources, or unreadable inputs. Results
are deterministic JSON evidence, not guesses hidden in runner behavior.

### Compilation

The compiler adapter supports MSVC-style `rc.exe` and `llvm-rc` invocation plus
GNU `windres`. Source decoding and compiler code page are separate options.
Include paths and preprocessor definitions remain explicit build inputs.

Every case gets an isolated compile tree. A case-insensitive asset/include
overlay resolves Windows filename casing without modifying the original
checkout, follows reachable local quoted includes, and reconstructs include
roots from project-relative suffixes when unambiguous. SDK angle-bracket headers
stay the external compiler's responsibility.

### Source-family extraction

For heterogeneous projects, the extractor materializes each declared structural
dialog family as a small UTF-8 RC case. Matching language variants remain in one
case, exact duplicates are removed, and IDs are materialized from the source
symbol environment. Every extracted case is still compiled before conversion.

### Execution and resumption

Cases run in isolated directories containing compiler logs, converter logs,
compiled resources, generated artifacts, and checkpoints. The runner continues
after compiler, converter, or timeout failures and distinguishes
`compile-failed`, `convert-failed`, and `no-forms`.

Output directories must be empty or new. Fingerprints support `--resume` and
selective `--retry-failed`; `--max-new-cases` enables bounded batches. A lock
prevents concurrent writers. Qt validation can be sharded and resumed
independently from conversion.

Machine-readable and Markdown reports aggregate issues and select small source
candidates for manual minimization without copying third-party files into the
permanent test suite.

## Diagnostics and batch resilience

Expected problems use structured values:

```text
Diagnostic
  code
  severity: info | warning | error
  message
  location
```

Representative codes include:

```text
resource.read-error
resource.no-dialogs
resource.conflicting-variant
input.ambiguous-dialog-owner
input.dialog-owner-not-found
dialog.parse-error
symbols.unresolved-expression
symbols.condition-assumed-true
naming.low-confidence
naming.generated-controls
naming.map-rule-error
naming.duplicate
naming-map.unused-rule
mapping.fallback
control-map.ambiguous
control-map.unused-rule
semantic-map.ambiguous
semantic-map.unused-rule
semantic.compound-conflict
semantic.range-unspecified
layout.overlap
layout.runtime-alternatives
layout-policy.ambiguous
layout.topology-changed
layout.simplified
language.default-unavailable
language.structure-mismatch
language.control-mismatch
language.topology-correction-rejected
translation.incomplete
translation.conflict
translation.unknown-language
output.name-disambiguated
output.collision
ui.invalid
qt.unavailable
qt.compile-error
qt.load-error
qt.runtime-error
qt.clipped-text
qt.unexpected-overlap
qt.source-gap-affinity-changed
qt.font-height-clipped
qt.font-width-clipped
qt.font-order-changed
```

A form-level error skips that form while unrelated dialogs and groups continue.
Without `--strict`, errors determine command failure. With `--strict`, warnings
also produce a nonzero exit status. Strict mode changes exit policy, not
conversion decisions.

`rc2ui-report.json` retains the original dialog ID, root object name, source
Win32 class, styles, raw and selected geometry, chosen Qt class, control object
name, confidence, evidence, anchors, group and alternative states, compound
provenance, control-map rule, button group, runtime-configured properties, and
emission state. It also records the requested/effective layout mode,
editability score, simplified/fallback region counts, and deterministic layout
and spacer transformation summaries, including counts by spacer role.

## Determinism

Identical inputs and configuration must produce identical:

- selected language variants;
- form and control order;
- object names;
- layout structure and Qt XML bytes;
- TS bytes and locale filenames;
- diagnostics, reports, and naming suggestions.

Algorithms sort resources and candidates explicitly, avoid hash-iteration
ordering, never use TOML rule order to resolve ambiguity, and omit timestamps
from generated conversion artifacts. Temporary names and output-collision
suffixes derive from stable source identities.

Qt runtime metrics are intentionally excluded from inference because they
depend on Qt version, platform style, installed fonts, DPI, and device scale.

## Configuration boundaries

The user-facing syntax and complete field reference are documented in
[toml-reference.md](toml-reference.md). This section defines only the layer
boundaries.

One versioned TOML file configures application concerns and all project rules:

```toml
version = 1
project_root = ".."
output = "generated-ui"
include_paths = ["include"]
default_language = 1033
rc_encoding = "cp1252"
strict = false
ui_comments = true

[layout]
mode = "faithful"
alignment_tolerance_dlu = 3
text_width_safety_factor = 1.1
max_designer_width_factor = 1.5
gap_growth = "proportional"
runtime_alternatives = "auto"

[layout.simplified]
profile = "balanced"
max_serialized_tracks = 5

[validation]
qt = "auto"
preview_font_scale = 1.0
font_scales = [2.0]
resize_scales = [0.75, 1.0, 1.5]
# preview = "qt-previews"

[defines]
ENTERPRISE = 1

[[input_groups]]
rc = ["resources/main.rc", "resources/admin.rc"]
resources = ["build/application.exe", "build/application.fr-FR.mui"]

[[naming.rules]]
name = "dialogs"
kind = "dialog"
id_regex = 'IDD_(?P<name>[A-Z0-9_]+)'
name_template = "${name}_DIALOG"
```

The manifest adapter validates these tables into immutable `LayoutPolicySet`,
`LayoutPolicy`, `SimplifiedPolicy`, and `ValidationPolicy` values. Batch
orchestration resolves a layout policy from all known identities of each
dialog before mapping or inference. Core layout, mapping, simplification, and
Qt-check modules receive typed values; they do not inspect raw TOML. Exact and
regexp overrides use explicit precedence and reject equal winners, keeping
configuration order out of deterministic conversion.

Naming, control, and semantic responsibilities remain independent typed
sections inside that file. This prevents a class substitution from renaming an
object, and prevents a naming rule by itself from authorizing a many-to-one
transformation. Exact many-to-one ownership is explicit in a control compound;
inferred ownership is explicit in semantic policy.

## Test invariants

The test suite covers these architectural properties:

- synthetic and real-compiler standard and extended dialog templates;
- standalone RES, PE32, PE32+, x86, x64, and ARM64 resources;
- signature-based input detection;
- repeated static IDs and reordered controls;
- multi-file and multi-language ownership;
- missing and conflicting default variants;
- conservative resource-script preprocessing;
- coordinate-driven grouping independent of source order;
- distant left, right, center, top, and bottom anchors;
- mixed-height rows and multi-track spans;
- group-box containment and crossing rejection;
- separator-created panes and nested regions;
- coarse shared rows across simplified separator panels, including overlong
  native separators, paired horizontal boundaries, and footer regions;
- five-entry metadata bounds inside separator regions, compact cross-row
  matrices, touching edit/up-down pairs, and initially hidden layout slots;
- one-DLU gaps and proportional resize growth;
- runtime alternatives and z-order evidence;
- topology-preserving multilingual correction;
- pre-emission rejection of collapsed rows or reversed order;
- dynamic font changes without horizontal or vertical clipping or order
  changes;
- simplified-layout degradation corpus covering long rows, regular matrices,
  repeated form rows, and tall-pane compact fallbacks at multiple runtime
  sizes;
- source-geometry post-validation at multiple runtime sizes;
- exact and regex naming-rule precedence and ambiguity;
- project widget promotion, typed properties, exact class-and-ID bindings and
  exact many-to-one project compounds, runtime contracts, and unused-rule
  diagnostics;
- native and project-mapped radio buttons with real `QButtonGroup` output;
- exclusion of explicit project mappings from compound heuristics;
- explicit edit/up-down and project-control-set replacement with
  secondary-control provenance;
- deterministic repeated conversion;
- continued batch processing after local errors.

## Extension points

New behavior belongs in the layer that owns the decision:

- a new compiled resource container or resource type: `adapters`;
- another Win32 class/style mapping: `mapping.controls`;
- a project class/ID conversion: the TOML `controls` section;
- another source of object names: `naming` with confidence and evidence;
- another compound composition: detector, policy, and transformer in
  `semantics`;
- another geometry invariant or layout heuristic: `analysis` or `layout`;
- another output format: an emitter over the Qt-neutral model;
- another batch policy: `application`, without changing parsers;
- another external verification strategy: `qtcheck` or `corpus`, without
  feeding platform-specific measurements into conversion.

Potential independent extensions include icon and bitmap extraction with `.qrc`
generation, explicit per-dialog layout hints for irreducible ambiguity, and
project-wide statistics for unknown classes and styles.

The governing principle remains unchanged: absolute coordinates are evidence of
the original layout intent, not the format of the resulting interface.
