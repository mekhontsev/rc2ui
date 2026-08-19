# rc2ui

`rc2ui` converts Win32 dialog resources into scalable Qt 6 Designer forms.
Compiled resources are the authoritative source: standalone `.res` files and
PE32/PE32+ modules (`.exe`, `.dll`, `.cpl`, `.ocx`, and `.mui`) are supported.
By default, every logical `RT_DIALOG` becomes a separate `.ui` file; manifest
input groups can restrict conversion to selected dialog IDs.

The matching `.rc` files and headers restore symbolic `IDD_*` and `IDC_*`
names, declaration ownership, and source context. They must describe the same
resources and build configuration as the compiled inputs.

## Features

- Microsoft `.res`, PE32/PE32+, `DLGTEMPLATE`, and `DLGTEMPLATEEX` readers;
- x86, x64, and ARM64 PE resource extraction without loading or executing the
  module;
- any number of dialogs and LANGID variants in each input group;
- one shared `.ui` using a selected default language plus populated Qt
  Linguist `.ts` catalogs for the other variants;
- multilingual evidence for grouping, alignment, geometry correction, and
  runtime-alternative detection;
- coordinate-driven `QGridLayout` generation with rows, columns, spans,
  proportional gaps, and long-range anchors;
- preservation of clear horizontal and vertical ordering during resize;
- dynamic font scaling through layout minimums and font-relative grid rulers;
- geometry-first `GROUPBOX` containment, independent of RC declaration order;
- horizontal and vertical separator regions;
- standard Win32 controls, common controls, and configurable project widgets;
- semantic recognition of edit/up-down, browse-field, slider/value, and
  list/action compositions;
- exact and regular-expression object-name rules;
- exact class-and-ID bindings and regular-expression rules for project control
  conversion;
- exact many-to-one project control sets using reusable promoted-widget
  profiles;
- real Qt `QButtonGroup` output for explicitly mapped button families;
- optional PyQt6 or PySide6 compile, load, resize, font, and geometry checks;
- deterministic output, structured diagnostics, evidence reports, and naming
  suggestions;
- batch isolation: one invalid dialog does not stop unrelated forms.

## Installation

For an editable development installation:

```sh
python -m pip install -e .
python -m unittest discover -s tests -v
```

### Windows virtual environment

Open PowerShell in the cloned repository:

```powershell
cd C:\path\to\rc2ui
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
```

Use `python` instead of `py` if the launcher is unavailable. Activate the
environment in every new PowerShell window:

```powershell
cd C:\path\to\rc2ui
.\.venv\Scripts\Activate.ps1
```

If PowerShell blocks the activation script, allow it for the current process:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\.venv\Scripts\Activate.ps1
```

Activation is optional; the environment's interpreter can run the module
directly:

```powershell
.\.venv\Scripts\python.exe -m rc2ui --help
```

## Conversion

A direct batch command may contain several RC sources and several compiled
resource containers:

```sh
rc2ui convert \
  --project-root /project \
  --output generated-ui \
  --include include \
  --define ENTERPRISE=1 \
  --default-language 1033 \
  resources/main.rc \
  resources/admin.rc \
  build/application.exe \
  build/application.fr-FR.mui
```

On Windows, a minimal command for files in `C:\temp` is:

```powershell
rc2ui convert --project-root "C:\temp" --output "C:\temp\generated-ui" --default-language 1033 "C:\temp\main.rc" "C:\temp\main.res"
```

Positional `.rc` paths are source files. `.res` paths and all other extensions
are treated as compiled-resource containers, then identified by their content
signature. PE modules are parsed as data and are never loaded through WinAPI.

All positional inputs form one resource namespace. Put modules that reuse the
same numeric IDs for unrelated dialogs into separate manifest input groups.

### Manifest

Use one versioned TOML file for repeatable conversions and project rules:

```sh
rc2ui convert --manifest config/rc2ui.toml
```

```toml
version = 1
project_root = ".."
output = "generated-ui"
include_paths = ["include", "third_party/include"]
default_language = 1033
rc_encoding = "cp1252"
strict = false
layout_mode = "faithful"
ui_comments = true
qt_check = "auto"
qt_font_scale = 1.0
# qt_preview = "qt-previews"

[defines]
ENTERPRISE = 1

[[input_groups]]
rc = ["resources/main.rc", "resources/admin.rc"]
resources = [
  "build/application.exe",
  "build/application.fr-FR.mui",
]
dialogs = ["IDD_SETTINGS", "#1042"]
dialog_regex = ["IDD_REPORT_.*"]

[[input_groups]]
rc = ["plugins/editor.rc"]
resources = ["build/editor.dll"]

[[naming.rules]]
name = "settings-dialog"
kind = "dialog"
id_regex = "IDD_SETTINGS"
name_template = "settingsDialog"
```

Manifest paths are resolved relative to `project_root`; `project_root` itself
is resolved relative to the manifest. See
[examples/rc2ui.toml](examples/rc2ui.toml).

The complete field-by-field guide for the configuration and all three rule
sections is
[docs/toml-reference.md](docs/toml-reference.md).

Each `[[input_groups]]` entry may contain any number of RC and compiled files.
Optional `dialogs` and `dialog_regex` arrays restrict conversion to matching
dialog resource IDs. The two arrays are combined with OR; without either one,
all dialogs in the group are converted. Exact selectors accept recovered
symbolic IDs, named resource IDs, and numeric forms such as `#1042`. Regular
expressions use case-sensitive Python `re.fullmatch` semantics.

### RC ownership and preprocessing

For every compiled dialog, rc2ui finds the actual `DIALOG` or `DIALOGEX`
declaration rather than treating every shared `resource.h` definition as
ownership evidence. Declarations are associated with the preceding `LANGUAGE`
statement. The RC source matching `--default-language` becomes the source
owner; other sources remain language variants.

If an expression in `.rc`, `.rc2`, or `.dlg` depends on an unresolved macro,
the complete condition is conservatively treated as true. The same applies to
an unresolved `#ifdef` in a resource script. This prevents externally gated
`DIALOG` and `LANGUAGE` declarations from disappearing during indexing. Header
files retain ordinary preprocessor semantics. Assumptions are aggregated as
`symbols.condition-assumed-true`; exact values can be provided with repeated
`--define NAME=VALUE` options.

RC files without a BOM are attempted as UTF-8 and then decoded using
`--rc-encoding`. Compiled dialog strings are already UTF-16, while generated
`.ui` and `.ts` files are always UTF-8.

## Language variants and translations

`--default-language` (also `--language`) selects the authoritative LANGID for
the shared `.ui`. The default is `1033`. Its control set, strings, styles, font,
and dialog size remain authoritative. Every other LANGID generates a populated
Qt Linguist catalog:

```text
generated-ui/
  main/IDD_LOGIN.ui
  translations/
    rc2ui_fr_FR.ts
```

Each message contains a stable disambiguation comment derived from the source,
dialog ID, object name, and property. Compile a catalog with Qt's `lrelease`:

```powershell
lrelease "C:\temp\generated-ui\translations\rc2ui_fr_FR.ts"
```

Set `ui_comments = false` in the manifest, or pass `--no-ui-comments`, to omit
the `comment` and `extracomment` attributes from every generated `.ui` string.
The translation catalogs then omit the matching disambiguation key as well, so
compiled translations continue to match the form; source notes remain in the
catalog as `extracomment`. Identical source strings in one form consequently
share one translation, and conflicting translations are reported. Use
`--ui-comments` to override a comment-free manifest for one run.

For a symbolic RC dialog ID, both the `.ui` form `<class>` and the root widget
`name` preserve the exact ID from the authoritative `DIALOG` declaration:

```xml
<class>IDD_LOGIN</class>
<widget class="QDialog" name="IDD_LOGIN">
```

The `class="QDialog"` attribute remains the actual Qt widget type. Numeric or
otherwise non-identifier resource names use the normal generated dialog name so
that Qt `uic` can still compile the form.

Unique control IDs align directly across languages. Repeated IDs such as
`IDC_STATIC` are matched globally by Win32 class, normalized geometry, size,
and type style. Resource order is only a deterministic tie-break. Language
variants then cast confidence-weighted votes for group membership, shared rows
and columns, significant overlap, and runtime alternatives.

Matched rectangles are projected into the default dialog coordinate system.
Median positions form temporary layout geometry, while default widths and
heights are retained. A topology guard accepts even large coordinated
corrections when they preserve clear ordering, long-range anchors, group
containment, and dialog bounds. Conflicting corrections are rejected locally
as `language.topology-correction-rejected`.

If several variants exist but the requested default LANGID is absent, that
form is skipped with `language.default-unavailable`. A dialog with only one
available LANGID may use that sole variant.

The generated TS format follows the
[Qt Linguist TS specification](https://doc.qt.io/qt-6/linguist-ts-file-format.html).

## Layout reconstruction

RC coordinates are evidence, not output geometry. Every visual container gets
a coordinate grid whose tracks are inferred from source edges and alignments.
Children have no absolute positions in the resulting `.ui`.

The central invariants are:

- a control clearly below or to the right of another remains so after
  conversion and resize;
- shared left, right, top, bottom, and center anchors remain shared, including
  across distant rows or columns;
- a short same-row chain keeps both its local neighbour gaps and which side a
  middle caption is visibly closer to;
- source gaps remain present and participate in resize;
- small manual coordinate errors may be normalized only when stronger
  topology evidence supports the correction;
- resource declaration order cannot override visible geometry.

### Faithful and simplified layouts

`faithful` is the default and reference layout mode. It preserves the full
coordinate-track model, including small gaps, distant alignment guides, spans,
separators, and layered controls. Its primary invariant is that a form remains
usable after an application-wide dynamic font change. Resize behavior is
secondary, and convenient manual editing is not allowed to weaken the font
invariant or the reference geometry.

`simplified` keeps the faithful analysis as its source of truth, then replaces
only unambiguous layout regions with smaller Designer-oriented structures:

- repeated label/editor rows become a `QFormLayout` when the columns touch, or
  a compact three-column `QGridLayout` when the source has a scalable gap;
- single rows and columns become `QHBoxLayout` and `QVBoxLayout`;
- regular matrices become compact logical grids;
- complex dialogs are split into editable vertical bands, with a fine grid
  retained only inside a band whose genuine local overlap requires it;
- group boxes and other nested containers are simplified independently.

Vertical bands use explicit gap spacers instead of serializing one root
`layoutStretch` value for every source row and gap. A one-DLU overlap at a row
boundary is treated as coordinate noise, preventing an otherwise ordinary pair
of rows from becoming a large local coordinate grid. Matrices and staggered
rows also prefer a `QVBoxLayout` of `QHBoxLayout` rows. Row proportions are
stored on the participating widgets' size policies. A simple row may retain a
stretch vector of at most five entries so its gaps participate in resize;
longer rows become a tree of proportional box layouts whose individual vectors
are still bounded to five entries. Empty source cuts also split pane-shaped
forms recursively, for example into `HBox(left pane, gap, VBox(right rows))`.
Only an irreducible overlap region may retain a faithful local grid: rc2ui does
not merge tracks when doing so would collapse rows or reverse source order.

Every candidate must preserve pairwise left/right and above/below order,
overlap, and source-proven shared boundaries. Outer margins and meaningful gaps
remain explicit, proportionally stretched regions. If a candidate changes
those invariants, only that container keeps a cleaned faithful grid; unrelated
containers may still be simplified.

Select the mode in the manifest:

```toml
layout_mode = "simplified"
```

or for a one-off run:

```powershell
rc2ui convert --layout-mode simplified --project-root "C:\temp" --output "C:\temp\generated-ui" "C:\temp\main.rc" "C:\temp\main.res"
```

The conversion report records the requested and effective mode, an editability
score, simplified and fallback region counts, and the transformations used for
each form. Runtime Qt validation applies to both modes.

`GROUPBOX` ownership is inferred from complete geometric containment with a
small tolerance. A control that merely crosses the frame is not moved inside.
The group may appear anywhere in source order. Dropdown combo-box height is
normalized to its closed selection field for grouping and row inference,
because the RC height includes the hidden popup list.

Children of neighbouring group boxes are compared before their independent
nested layouts are built. A source-proven shared row is normalized globally,
so an edit and combo box in separate groups retain the same top, center, or
bottom guide instead of drifting with unrelated nested grid calculations.

`STATIC` controls with `SS_ETCHEDVERT`, `SS_ETCHEDHORZ`, or equivalent narrow
frame geometry become expanding `QFrame::VLine` or `QFrame::HLine` widgets.
Separators partition row and column evidence into independent regions so
controls stay on the same side during resize.

Grid stretch factors are proportional to source DLU intervals. Minimum track
sizes preserve the initial proportions before Qt distributes surplus space.
Wide and tall controls use `colspan` and `rowspan`; mixed-height peers retain
top, center, or bottom alignment as supported by the source.

For ordinary standalone dialogs, the serialized Designer canvas may grow when
the single-line text of a label, group title, check box, radio button, or
button approaches the capacity of its RC rectangle. The common font-relative
estimate includes the control's non-text decoration and a small cross-toolkit
metrics reserve. It is capped at 150% of the source width, preserves grid
proportions, and does not resize tiny host-owned child templates. The enlarged
DLU width also feeds the invisible font ruler, so the reserve grows when the
application changes its font dynamically.

Win32 `BUTTON` controls carrying `BS_MULTILINE` retain their multiline intent.
`QLabel` uses native word wrapping; Qt button classes have no equivalent
`wordWrap` property, so rc2ui inserts deterministic word breaks from the
source DLU width. Every language variant is wrapped independently, and the
source DLU height plus the font ruler keeps those lines usable after a dynamic
font change. A short, single-line-height button is left on one line even when
the permissive Win32 flag is present.

Nearly identical overlapping controls may represent runtime alternatives.
Repeated z-order offsets, geometry, class compatibility, and multilingual
agreement identify those layers. Alternatives share a layout cell without
inventing runtime visibility logic. Partial accidental overlap remains a
warning.

### Dynamic font changes

A fixed pixel minimum cannot preserve DLU semantics when the application
changes its font at runtime. Generated layouts use `QLayout::SetMinimumSize`,
full-size floor spacers, and zero-thickness font-relative `QLabel` rulers in the
root coordinate grid. Their `sizeHint` changes on Qt `FontChange`, allowing the
grid, text cells, and gaps to grow without generated Python runtime code.

The full coordinate rulers are part of `faithful` mode. `simplified` relies on
the natural, font-dependent height hints of semantic Qt layouts and retains one
zero-height width ruler at the root. The ruler cannot cover controls on the
Designer canvas, but makes the dialog minimum width follow `FontChange` on Qt
styles that do not propagate a new top-level horizontal size hint by
themselves. `QLayout::SetMinimumSize` remains active in both modes.

## Optional Qt 6 validation

PyQt6 and PySide6 are optional local tools and are not package dependencies.
rc2ui never installs them automatically. If both are available, PyQt6 is
preferred.

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install PyQt6
# or
python -m pip install PySide6
```

Conversion defaults to `--qt-check auto`: generated forms are checked when a
supported binding is present, otherwise conversion continues silently.

```powershell
# Require Qt validation.
rc2ui convert --qt-check required --project-root "C:\temp" --output "C:\temp\generated-ui" "C:\temp\main.rc" "C:\temp\main.res"

# Disable Qt validation.
rc2ui convert --qt-check off --project-root "C:\temp" --output "C:\temp\generated-ui" "C:\temp\main.rc" "C:\temp\main.res"

# Check an existing form or directory.
rc2ui qt-check "C:\temp\generated-ui"
```

The checker runs Qt in an isolated offscreen process. It compiles and loads
forms, activates layouts at smaller, source, and larger sizes, changes the form
font dynamically, and checks:

- root layouts, expected widgets, and label buddies;
- zero-size widgets, clipping, bounds, and unexpected overlap;
- a serialized Designer canvas smaller than Qt's layout hint;
- expansion behavior and source-gap participation;
- source-relative horizontal and vertical ordering;
- generator-selected anchors and group parents;
- local same-row gap affinity, so a caption cannot attach to the wrong field;
- separator sides and runtime-alternative exclusions;
- text width, text height, and ordering after a twofold font increase;
- normalized spatial drift and size collapse against report geometry.

Results are written to `rc2ui-qt-report.json`. Metrics include runtime geometry,
`sizeHint`, `minimumSizeHint`, size policies, font metrics, Qt version, platform
style, DPI, and device-pixel ratio. These platform-dependent measurements are
diagnostic evidence and never feed back into deterministic layout inference.

Promoted widgets are substituted with their declared base Qt classes in the
temporary validation copy. This validates the surrounding layout but cannot
reproduce a project widget's real size hints or behavior.

### PNG previews

Generate a preview gallery during conversion:

```powershell
rc2ui convert --qt-preview "C:\temp\qt-previews" --project-root "C:\temp" --output "C:\temp\generated-ui" "C:\temp\main.rc" "C:\temp\main.res"
```

Or render existing forms:

```powershell
rc2ui qt-check "C:\temp\generated-ui" --preview "C:\temp\qt-previews"
```

The result contains PNG files and `qt-previews/index.html`. Requesting previews
makes a supported Qt binding mandatory. Every successfully loaded form is
rendered before the optional runtime checks, so a later validation failure does
not suppress its PNG. Preview failures are reported per form as
`qt.preview-error`. The Qt JSON report includes a ready-to-read `summary` with
prepared, compiled, and loaded form counts plus requested, attempted, saved,
and failed preview counts. Its `failure_diagnostics` list groups the leading
blockers and includes representative messages. The same preview totals and
leading blocker are printed by the command. Source `.ui` files are not
modified. Preview capture uses the native desktop Qt platform when available
so the platform font engine paints text normally; validation without previews
continues to use the offscreen platform. Preview widgets are never shown on
screen during the batch.

Preview paths mirror the `.ui` paths relative to the checked output root. Thus
`dialogs/SETTINGS.ui` normally becomes `dialogs/SETTINGS.png`, preserving the
form name while allowing identical basenames in different directories. A
numeric suffix is used only for a case-insensitive path collision.

Set `qt_font_scale` in the manifest to preview and validate with scaled fonts
without baking the scale into generated `.ui` files:

```toml
qt_font_scale = 1.5
```

The scale is applied both to the platform application font and to explicit
widget fonts loaded from `.ui`, which otherwise override the application font.
The default is `1.0`. The direct equivalents are
`convert --qt-font-scale 1.5` and `qt-check --font-scale 1.5`.

## Naming rules

The optional `naming` section supplies project-wide reviewed exact names and
regular-expression families. Without it rc2ui derives object names from
labels, text, symbolic IDs, or stable fallbacks and writes mergeable rules to
`rc2ui-name-suggestions.toml`.

Large exact-name sets use a compact table:

```toml
[[naming.rules]]
name = "settings-controls"
kind = "control"
source_regex = 'resources/settings\.rc'
dialog_regex = "IDD_SETTINGS"
priority = 100

[naming.rules.names]
IDC_HOST = "hostEdit"
IDC_PORT = "portEdit"
IDC_PROTOCOL = "protocolComboBox"
IDC_CONNECT = "connectButton"
```

One `[naming.rules.names]` block may contain hundreds of exact replacements. Quote
numeric keys, for example `"#1001" = "hostEdit"`.

Regex rules cover ID families and repeated occurrences:

```toml
[[naming.rules]]
name = "editors-from-symbols"
kind = "control"
id_regex = 'IDC_(?P<name>[A-Z0-9_]+)_EDIT'
name_template = "${name}_EDIT"
priority = 10

[[naming.rules]]
name = "second-login-static"
kind = "control"
dialog_regex = "IDD_LOGIN"
id_regex = "IDC_STATIC"
occurrence = 2
name_template = "passwordLabel"
priority = 100
```

`source_regex`, `dialog_regex`, and `id_regex` use Python `re.fullmatch`.
Resource IDs are tested through symbolic aliases, named values, and `#ordinal`.
Templates support `${name}`, `${1}`, and `${0}` captures and normalize the
result to a valid lower-camel-case Qt object name.

Precedence is `(priority, exact, specificity)`: an exact
`[naming.rules.names]` entry
beats a regex rule at equal priority, then additional source, dialog, and
occurrence constraints win. Equal leaders are errors; file order never resolves
ambiguity. Unknown fields, invalid captures, duplicate names, and duplicate
matchers are rejected at load time.

Validate and use the unified configuration:

```sh
rc2ui validate-config config/rc2ui.toml
rc2ui convert --manifest config/rc2ui.toml
```

The matched rule, pattern, and captures are retained in the JSON report.
Generated suggestions additionally record confidence and derivation metadata.
See [examples/rc2ui.toml](examples/rc2ui.toml).

## Project control rules

The optional `controls` section converts Win32 classes and IDs to standard or
project-specific Qt widgets. It deliberately separates reusable output widget
profiles from selectors.

```toml
[[controls.widgets]]
name = "project-grid"
qt_class = "Company::GridWidget"
role = "input"
header = "company/gridwidget.h"
extends = "QWidget"
expands_horizontally = true
expands_vertically = true
text_property = "windowTitle"

[controls.widgets.properties]
displayMode = { enum = "Company::GridWidget::Compact" }

[[controls.rules]]
name = "registered-project-grids"
widget = "project-grid"
win_class = "MyCompanyGrid"
id_regex = 'IDC_RESULTS_.*'
dialog_regex = 'IDD_.*'
priority = 50
```

Use `[[controls.bindings]]` for a large reviewed list of exact
`Win32 class + ID`
conversions:

```toml
[[controls.bindings]]
name = "settings-project-controls"
dialog_regex = "IDD_SETTINGS"
priority = 100

controls = [
  { win_class = "Edit", id = "IDC_PATH", widget = "project-editor" },
  { win_class = "Button", id = "IDC_COLOR", widget = "color-button" },
]
```

Use `[[controls.compounds]]` when several exact source controls represent one
project widget. The result reuses a normal widget profile, occupies the union
of all source rectangles, and inherits the primary control's object name:

```toml
[[controls.widgets]]
name = "choice-selector"
qt_class = "Example::ChoiceSelector"
role = "input"
header = "example/choiceselector.h"
extends = "QWidget"
expands_horizontally = true

[[controls.compounds]]
name = "paired-choice-selector"
widget = "choice-selector"
primary = { win_class = "LegacyChoice", id = "IDC_CHOICE_PRIMARY" }
members = [
  { win_class = "LegacyChoice", id = "IDC_CHOICE_SECONDARY" },
]

[[naming.rules]]
name = "choice-control-names"
kind = "control"

[naming.rules.names]
IDC_CHOICE_PRIMARY = "choiceSelector"
```

Every member selector requires an exact compiled class and exact resource ID;
matching is case-insensitive for the Win32 class and case-sensitive for the ID.
An ambiguous repeated ID must add `occurrence`. Missing members leave the
source controls unchanged and report the compound rule as unused. Equal rules,
overlapping replacements, and a member claimed by a one-to-one control rule are
reported as errors rather than being resolved by TOML order.

Rules support `source_regex`, `dialog_regex`, exactly one of `win_class` or
`win_class_regex`, `id_regex`, `occurrence`, `style_mask`, `style_value`,
`priority`, `button_group`, and `runtime_configured`. Regex selectors use
`re.fullmatch`. Precedence is priority, exact binding, selector specificity,
then the number of checked style bits. An unresolved tie is an error.

Runtime placeholders can become a family of radio buttons with a real
`QButtonGroup`:

```toml
[[controls.widgets]]
name = "radio-button"
qt_class = "QRadioButton"
role = "input"
text_property = "text"

[[controls.rules]]
name = "runtime-mode-options"
widget = "radio-button"
win_class = "ProjectPlaceholder"
id_regex = 'IDC_MODE_.*'
button_group = "modeButtonGroup"
runtime_configured = ["checked"]
priority = 100
```

`runtime_configured` records properties the application must set after
`setupUi`; it does not invent values. Rule provenance, button-group membership,
and the runtime contract are included in `rc2ui-report.json`. Unused rules and
binding entries produce `control-map.unused-rule` diagnostics.

An explicit control-rule match takes precedence over compound-control
heuristics. A mapped placeholder therefore cannot be absorbed by a later
edit/up-down or browse-field transformation.

When a profile has `header` and `extends`, the emitter creates a Designer
`<customwidgets>` declaration. The class must be available to `uic`, have a
compatible constructor, and expose the configured Qt properties. Control rules
do not change `objectName`; naming remains the naming section's responsibility.

Compiled resources do not retain whether a source line used `CONTROL`,
`PUSHBUTTON`, or another RC shorthand. The stable selectors are the compiled
Win32 class, ID, styles, dialog, source, and occurrence. A standard `Button`
with `BS_RADIOBUTTON` or `BS_AUTORADIOBUTTON` already maps to `QRadioButton`
without a project rule.

See [examples/rc2ui.toml](examples/rc2ui.toml) for complete inline examples.

## Compound controls and semantic rules

rc2ui detects common multi-control compositions by class, style bits, IDs,
geometry, labels, and language agreement:

- `edit-updown` — an edit and `msctls_updown32`;
- `edit-browse` — an edit and adjacent browse button;
- `slider-value` — a trackbar and numeric value display;
- `list-actions` — a list, tree, or table with an action-button column.

Candidates, confidence, evidence, and supporting LANGIDs are always reported.
Safe visual relationships may use `bundle`, which preserves both widgets in
the common coordinate grid. `edit-updown` defaults to `suggest` because a dialog
template does not contain the application's numeric range, step, precision, or
validation rules.

`UDS_AUTOBUDDY` is stronger than a geometric hint: WinAPI binds an up-down to
the immediately preceding control in z-order. When that control is an edit,
rc2ui recognizes the pair even if the up-down has placeholder coordinates.
`UDS_ALIGNLEFT` and `UDS_ALIGNRIGHT` then supply its effective runtime position,
so separately retained widgets stay vertically attached. If a semantic rule
replaces the pair, the result occupies the edit's original runtime footprint
rather than the union with the placeholder rectangle.

Explicit replacement of these inferred candidates uses the `semantics`
section:

```toml
[[semantics.rules]]
name = "floating-parameters"
kind = "edit-updown"
action = "replace"
source_regex = ".*"
dialog_id = "IDD_.*PARAMETERS"
primary_id = "IDC_.*_(VALUE|FACTOR)"
member_id = "IDC_.*_SPIN"
label_regex = "(?i).*(factor|scale).*"
result = "QDoubleSpinBox"
priority = 100

[semantics.rules.properties]
minimum = -1000000.0
maximum = 1000000.0
decimals = 3
singleStep = 0.1
```

Actions are `suggest`, `keep`, `bundle`, and `replace`. Higher priority wins,
then more specific selectors; equal leaders are errors. Label matching uses
associated labels across all aligned language variants.

`QSpinBox` and `QDoubleSpinBox` replacement rules must provide `minimum` and
`maximum`, unless the application configures the result at runtime and the rule
sets `runtime_configured = true`. A replacement inherits the primary control's
name and redirects its label buddy. Consumed secondary controls remain in the
report with `emitted = false`.

See [examples/rc2ui.toml](examples/rc2ui.toml) for complete inline examples.

## Corpus runner

The `corpus` command validates rc2ui against source trees that do not already
provide compiled `.res` files. It does not bypass the normal converter: each
root RC is compiled by an external resource compiler, then the resulting
`.rc + .res` pair enters the standard pipeline.

Discover cases first:

```powershell
rc2ui corpus discover "C:\corpus\repos" --report "C:\corpus\discovery.json"
```

Discovery follows `.rc`, `.rc2`, and `.dlg` includes and classifies root files,
language fragments, dialog fragments, non-dialog resources, and unreadable
files. A directory containing multiple immediate Git repositories is expanded
into separate project roots.

Run a complete or filtered corpus:

```powershell
rc2ui corpus run "C:\corpus\repos" `
  --output "C:\corpus\run-001" `
  --jobs 4 `
  --timeout 60 `
  --match "notepad|settings" `
  --limit 100
```

For large heterogeneous trees, extract every declared structural dialog family
into an isolated source case, then run conversion in process:

```powershell
rc2ui corpus extract "C:\corpus\repos" --output "C:\corpus\source-cases"
rc2ui corpus run "C:\corpus\source-cases" `
  --output "C:\corpus\source-run" `
  --jobs 4 --converter-mode in-process --qt-check off
rc2ui corpus qt-check "C:\corpus\source-run" `
  --output "C:\corpus\source-qt" --batch-size 20
```

The runner searches for `rc.exe`, then `llvm-rc`, then `windres`. Select a
compiler with `--compiler PATH`. Pass project includes and macros with repeated
`--include` and `--define`; compiler code pages remain independent from
rc2ui's source-decoding fallback.

Every case gets isolated compiler output, converter output, a generated tree,
and a case-sensitive include/asset overlay. The source checkout is never
modified. Results are summarized in `corpus-report.json` and
`corpus-report.md`. Runs support `--resume`, `--retry-failed`, bounded
`--max-new-cases`, and output locking. Rebuild Markdown without rerunning cases:

```powershell
rc2ui corpus report "C:\corpus\run-001\corpus-report.json"
```

## Output and exit policy

A typical output tree is:

```text
generated-ui/
  resources/main/
    IDD_LOGIN.ui
    IDD_SETTINGS.ui
  translations/
    rc2ui_fr_FR.ts
  rc2ui-report.json
  rc2ui-qt-report.json
  rc2ui-name-suggestions.toml
```

Automatic dialog-name collisions receive a stable resource-ID suffix. A
collision caused by explicit naming rules is an error. Generated control names
are summarized once per dialog instead of flooding the console; full details
remain in the report and suggestion snippet.

The `.ui` basename always equals the root widget `name`. For example,
`<widget class="QDialog" name="settingsDialog">` is written as
`settingsDialog.ui`; any automatic collision suffix is applied to both names.

Without `--strict`, errors produce a nonzero exit code and warnings do not.
With `--strict`, warnings also fail the command. Failure remains local to the
affected form or input group whenever possible.

For every TOML field and practical configuration recipes, see
[docs/toml-reference.md](docs/toml-reference.md). For implementation details,
see [docs/architecture.md](docs/architecture.md).
For unavoidable limits, see [docs/limitations.md](docs/limitations.md).

## License

Copyright (c) 2026 Dmitry Mekhontsev.

Licensed under the [MIT License](LICENSE).
