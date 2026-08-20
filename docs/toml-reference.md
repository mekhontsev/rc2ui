# TOML configuration reference

rc2ui uses one versioned UTF-8 TOML file. It contains input groups, batch
options, and three optional customization sections:

| Section | Purpose |
| --- | --- |
| `naming` | RC ID to Qt `objectName` |
| `controls` | Win32 class/ID to Qt widget |
| `semantics` | Multi-control composition policy |

The sections do not share responsibilities. A naming rule cannot change a
widget class and a control rule cannot rename an object. Reviewed exact source
sets use `controls.compounds`; inferred geometric candidates use `semantics`.
Both require explicit configuration before replacing several controls with one
Qt widget.

## TOML and regex conventions

The configuration is UTF-8 TOML. Python regular expressions are matched
with `fullmatch`, not substring search. Write `.*` explicitly when a prefix or
suffix may vary.

TOML literal strings are convenient for regex because backslashes are not
interpreted:

```toml
source_regex = 'resources/settings\.rc'
```

In a TOML basic string the same backslash must be escaped:

```toml
source_regex = "resources/settings\\.rc"
```

The same distinction matters for Windows paths. Prefer literal strings or
escaped backslashes:

```toml
project_root = 'C:\work\product'
# Equivalent:
# project_root = "C:\\work\\product"
```

Resource ID selectors are tested against every recovered symbolic alias, a
named resource value, and the numeric form `#ordinal`. For example, a dialog
compiled as ordinal 100 with alias `IDD_SETTINGS` can match either
`IDD_SETTINGS` or `#100`.

Source selectors use `/` separators. Naming-rule source paths are normalized
relative to `project_root`; control and semantic rules receive the same source
identity stored on the dialog.

Configuration paths are resolved against `project_root`; `project_root` itself
is resolved against the directory containing `rc2ui.toml`.

Rule order is never a precedence mechanism. Use `priority` and scopes. If two
matching rules have equal effective precedence, conversion reports an error
instead of selecting the first block.

## Recommended workflow

1. Convert once without customization sections.
2. Review `rc2ui-report.json`, generated `.ui` files, and
   `rc2ui-name-suggestions.toml`.
3. Merge reviewed suggestion rules into the `naming` section.
4. Add control rules only for project classes, runtime placeholders, or
   standard Qt classes that cannot be inferred from Win32 styles.
5. Add exact control compounds for reviewed project-specific source sets, and
   semantic rules for inferred behavior such as edit/up-down replacement.
6. Keep all rules in the same project configuration.
7. Treat unused-rule warnings as stale configuration unless the rule is meant
   for an input group not present in the current run.

## Project configuration

The top-level `version` is required and currently must be `1`. At least one
`[[input_groups]]` block is required.

### Complete example

```toml
version = 1
project_root = ".."
output = "generated-ui"
include_paths = ["include", "third_party/include"]
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
preview = "qt-previews"
preview_font_scale = 1.0
font_scales = [2.0]
resize_scales = [0.75, 1.0, 1.5]

[defines]
WIN32 = 1
ENTERPRISE = "0x1"

[[input_groups]]
rc = ["resources/main.rc", "resources/admin.rc"]
resources = [
  "build/application.exe",
  "build/application.fr-FR.mui",
]

[[input_groups]]
rc = ["plugins/editor.rc"]
resources = ["build/editor.dll"]

[[naming.rules]]
name = "dialogs"
kind = "dialog"
id_regex = 'IDD_(?P<name>[A-Z0-9_]+)'
name_template = "${name}_DIALOG"
```

Run it with:

```sh
rc2ui convert --manifest config/rc2ui.toml
```

Manifest mode cannot be combined with positional inputs, `--include`,
`--define`, or `--rc-encoding`. Put the complete repeatable configuration in
the manifest. Command-line overrides remain available for language, strict
mode, layout mode, UI comments, Qt checks, previews, and preview font scale.

### Manifest fields

| Field | Type | Default | Meaning |
| --- | --- | --- | --- |
| `version` | integer | required | Unified configuration schema; currently `1` |
| `project_root` | path string | `.` | Base for project-relative paths; itself relative to the manifest |
| `output` | path string | required | Generated output directory |
| `include_paths` | array of path strings | `[]` | Additional RC/header include roots |
| `default_language` | integer or integer string | `1033` | Authoritative Win32 LANGID for shared `.ui` content |
| `rc_encoding` | codec name | `cp1251` | Fallback for RC text that is neither BOM-marked nor valid UTF-8 |
| `strict` | boolean | `false` | Make warnings affect the exit status |
| `layout` | table | defaults below | Layout inference, spacing, and simplified-mode policy |
| `ui_comments` | boolean | `true` | Include translation comments in generated `.ui` strings |
| `validation` | table | defaults below | Qt checks, previews, and scale matrix |
| `defines` | TOML table | `{}` | Integer preprocessor definitions |
| `input_groups` | array of tables | required | Independent resource namespaces |
| `naming` | table | unset | Object-name rules |
| `controls` | table | unset | Widget profiles and control rules |
| `semantics` | table | unset | Compound-control rules |

`output`, include paths, preview paths, and every path in an input group are
resolved against `project_root`. Absolute paths stay absolute.

Values under `[defines]` may be TOML integers or strings accepted by Python's
base-zero integer parser, such as `"0x400"`. Boolean values are not integers.

`rc_encoding` affects source indexing only. Dialog-template strings come from
compiled resources as UTF-16. Use an explicit encoding when the source project
uses a non-UTF code page.

`layout.mode = "faithful"` emits the full coordinate-track reconstruction and
is the default. It is intended for maximum geometric fidelity. Set
`layout.mode = "simplified"` to post-process that same reconstruction into
smaller `QFormLayout`, `QHBoxLayout`, `QVBoxLayout`, and compact `QGridLayout`
regions where doing so preserves topology. Substantial vertical separators may
form coarse left/separator/right grids with shared row bands; perpendicular
horizontal lines create nested top/bottom regions. Source margins and positive
gaps remain proportional layout tracks. Ambiguous, crossing, or structurally
worse candidates fall back independently to another safe candidate or a
cleaned faithful grid. The direct command-line equivalent is
`convert --layout-mode simplified`; it changes the default mode for a one-off
run, while a matching per-dialog override may still select its own mode.

Per-form report fields `layout_mode_requested`, `layout_mode_used`,
`layout_policy`, `editability_score`, `simplified_regions`,
`faithful_fallback_regions`, and `layout_transformations` explain the result.
`layout_policy` is the fully resolved value after per-dialog overrides. A
transformation such as
`grid-to-form-grid:2` means that two regions used that rewrite. The score is a
relative structural metric for comparing the two outputs of the same form; it
is not a fidelity percentage.

### Layout policy

```toml
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
```

| Field | Values | Default | Effect |
| --- | --- | --- | --- |
| `layout.mode` | `faithful`, `simplified` | `faithful` | Select the emitted layout strategy |
| `layout.alignment_tolerance_dlu` | non-negative integer | `3` | Merge source guides whose coordinates differ by at most this many DLU |
| `layout.text_width_safety_factor` | finite number >= 1 | `1.1` | Reserve cross-toolkit width for text and determine deterministic multiline button breaks |
| `layout.max_designer_width_factor` | finite number >= 1 | `1.5` | Cap text-driven enlargement of the serialized Designer canvas relative to the source width |
| `layout.gap_growth` | `proportional`, `minimum`, `outer-minimum` | `proportional` | Decide which empty tracks receive surplus space |
| `layout.runtime_alternatives` | `auto`, `source-order`, `off` | `auto` | Control geometric collapse of probable runtime layers |
| `layout.simplified.profile` | `conservative`, `balanced`, `aggressive` | `balanced` | Set how much structural improvement a topology-safe rewrite must provide |
| `layout.simplified.max_serialized_tracks` | integer >= 2 | `5` | Bound stretch vectors created by separator-panel slicing and coarse shared rows |

`gap_growth` never removes the source minimum distance. `proportional` is the
reference behavior: controls and gaps share surplus space in their source DLU
ratio. `minimum` keeps every wholly empty gap at its minimum while occupied
tracks grow. `outer-minimum` applies that treatment only to empty leading and
trailing margins, leaving internal gaps proportional.

`runtime_alternatives = "auto"` accepts strict geometry even when source order
has no supporting pattern. `source-order` additionally requires nearby
z-order or a repeated layer offset; it is useful when authored overlaps are
common but runtime layers follow source order. `off` retains every overlapping
control as an independent layout item. None of these settings invents
visibility logic.

The `conservative` simplified profile requires both lower serialization cost
and lower Designer friction. `balanced` uses the established cost-based
selection. `aggressive` also accepts an equal-cost candidate when it reduces
Designer friction. All profiles retain the same absolute topology, grouping,
font-growth, and extent guards; the profile cannot authorize a geometrically
unsafe rewrite.

Use per-dialog policy only for source families whose intent differs from the
project default:

```toml
[[layout.overrides]]
name = "dense-reports"
dialog_regex = 'IDD_REPORT_.*'
priority = 20
mode = "simplified"
alignment_tolerance_dlu = 2
max_designer_width_factor = 2.0
gap_growth = "minimum"
runtime_alternatives = "source-order"

[layout.overrides.simplified]
profile = "conservative"
max_serialized_tracks = 7

[[layout.overrides]]
name = "one-reference-form"
dialog = "IDD_REFERENCE"
priority = 100
mode = "faithful"
```

Each override requires exactly one of `dialog` or `dialog_regex`; regular
expressions use full-match semantics. Selectors are matched against the source
dialog ID, compiled symbolic aliases, a named resource ID, and `#ordinal`.
Higher priority wins, then an exact selector beats a regexp. Multiple winners
at equal precedence are an error rather than depending on TOML order. An
override inherits every omitted field from `[layout]`.

`ui_comments = false` removes both `comment` and `extracomment` attributes from
generated `.ui` strings. The direct equivalent is `convert --no-ui-comments`;
`convert --ui-comments` can re-enable them when overriding a manifest. A
comment-free UI cannot carry Qt's per-message disambiguation key, so generated
TS catalogs omit that key too while retaining source provenance in
`extracomment`. Identical source strings in the same form then share a
translation; incompatible duplicates are diagnosed as translation conflicts.

### Validation policy

```toml
[validation]
qt = "auto"
preview = "qt-previews"
preview_font_scale = 1.0
font_scales = [1.5, 2.0]
resize_scales = [0.75, 1.0, 1.5]
```

`validation.qt = "auto"` validates when PyQt6 or PySide6 is already installed
and is otherwise silent. `required` makes an unavailable binding or validation
error a failure. `off` disables runtime validation. Setting
`validation.preview` implicitly requires validation because previews need a
real Qt runtime.

`validation.preview_font_scale` must be positive and finite. It scales the
`QApplication` font before any form is loaded and then scales explicit widget
fonts from the `.ui`, because those overrides do not inherit the application
font. Qt size hints, runtime checks, and PNG previews therefore all observe the
selected scale. The scale is not written back into generated `.ui` files; the
application remains free to change its font at runtime. For example,
`preview_font_scale = 1.5` previews at 150% of each form's normal font size.
The direct command equivalents are `convert --qt-font-scale 1.5` and
`qt-check --font-scale 1.5`.

`font_scales` and `resize_scales` are non-empty arrays of unique positive
finite numbers. Each `font_scales` value other than one is applied as an
in-place dynamic font change after loading; each result is stored in
`font_tests`, with the largest factor also exposed as `font_test` for report
consumers. `resize_scales` determines the smaller, baseline, and larger runtime
sizes. A baseline factor of `1.0` is added internally if omitted.

The top-level `layout_mode`, `qt_check`, `qt_preview`, and `qt_font_scale`
fields remain accepted as concise command-oriented aliases. Do not specify an
alias together with its nested equivalent in the same manifest.

### Input groups

Every group requires both non-empty arrays:

```toml
[[input_groups]]
rc = ["resources/module.rc"]
resources = ["build/module.res", "build/module.fr-FR.mui"]
dialogs = ["IDD_SETTINGS", "#1042"]
dialog_regex = ["IDD_REPORT_.*"]
```

| Input-group field | Type | Meaning |
| --- | --- | --- |
| `rc` | non-empty path array | RC sources and their reachable declarations/headers |
| `resources` | non-empty path array | Standalone RES or PE containers holding the compiled dialogs |
| `dialogs` | non-empty string array, optional | Exact dialog resource-ID allowlist |
| `dialog_regex` | non-empty string array, optional | Regular-expression dialog resource-ID allowlist |

Use one group when the files share a resource-ID namespace and may contain
language variants of the same logical dialogs. Use separate groups when two
modules reuse the same numeric dialog IDs for unrelated forms.

The cardinalities are independent: one RC may correspond to several compiled
containers, and several RC files may describe one compiled module.

When `dialogs` or `dialog_regex` is present, only a dialog matching at least
one selector is converted. Exact selectors are compared with every recovered
symbolic alias, a compiled named resource ID, and the numeric form `#ordinal`.
Patterns are case-sensitive Python regular expressions evaluated with
`re.fullmatch` against the same candidates. Omitting both fields converts every
dialog in the group. Selection applies to a logical dialog, so all its language
variants remain available for multilingual layout analysis and translation.

## Naming section

The optional `naming` table controls Qt object names only. Rules use the
`[[naming.rules]]` array. A valid symbolic dialog ID is preserved verbatim as
both the form `<class>` and root widget `name`; a dialog naming rule supplies
the fallback when the resource has no identifier usable by Qt `uic`.

| Top-level field | Type | Meaning |
| --- | --- | --- |
| `rules` | array of tables | Naming rules written as `[[naming.rules]]` |

Validate the complete configuration without conversion:

```sh
rc2ui validate-config config/rc2ui.toml
```

### Exact names

Use `[naming.rules.names]` for a large reviewed ID-to-name table:

```toml
[[naming.rules]]
name = "settings-control-names"
kind = "control"
source_regex = 'resources/settings\.rc'
dialog_regex = "IDD_SETTINGS"
priority = 100

[naming.rules.names]
IDC_HOST = "hostEdit"
IDC_PORT = "portEdit"
IDC_PROTOCOL = "protocolComboBox"
IDC_CONNECT = "connectButton"
"#1001" = "valueEdit"
```

One table can contain hundreds or thousands of entries. All entries inherit the
rule's kind, scopes, occurrence, priority, confidence, and derivation metadata.

An exact value may also be an inline table:

```toml
[naming.rules.names]
IDC_HOST = { name = "hostEdit", confidence = 0.98, derived_from = "reviewed generated suggestion" }
IDC_PORT = "portEdit"
```

The inline `confidence` and `derived_from` override rule-level defaults for that
entry. They are review metadata and do not affect matching or precedence.
Explicit naming-rule matches are treated as authoritative when names are
resolved.

Exact values use the same template renderer. `${0}` may refer to the exact
matched ID, although a fixed reviewed name is usually clearer.

Do not combine `[naming.rules.names]` with `id_regex` or `name_template` in the
same rule.

### Regex names

Use `id_regex` and `name_template` for an ID family:

```toml
[[naming.rules]]
name = "field-editors"
kind = "control"
id_regex = 'IDC_(?P<field>[A-Z0-9_]+)_EDIT'
name_template = "${field}_EDIT"
priority = 10
```

Template references are:

- `${0}`: the complete ID match;
- `${1}`, `${2}`, and so on: numeric capture groups;
- `${name}`: a named capture group.

Every referenced group must exist in `id_regex`. The rendered result is passed
through rc2ui's identifier normalizer and must become a valid Qt object name.
An already valid lower-camel-case value remains stable; underscore-style values
are normalized.

### Repeated IDs

`occurrence` is one-based and distinguishes repeated source IDs such as
`IDC_STATIC`:

```toml
[[naming.rules]]
name = "login-user-label"
kind = "control"
dialog_regex = "IDD_LOGIN"
id_regex = "IDC_STATIC"
occurrence = 1
name_template = "userNameLabel"
priority = 200

[[naming.rules]]
name = "login-password-label"
kind = "control"
dialog_regex = "IDD_LOGIN"
id_regex = "IDC_STATIC"
occurrence = 2
name_template = "passwordLabel"
priority = 200
```

Use separate rules for different occurrences because a TOML table cannot
contain the same exact key twice.

### Naming-rule fields

| Field | Required | Meaning |
| --- | --- | --- |
| `name` | yes | Unique configuration rule name |
| `kind` | yes | `dialog` or `control` |
| `source_regex` | no | Project-relative source path scope |
| `dialog_regex` | no | Enclosing dialog ID scope |
| `id_regex` | regex form | Dialog/control ID selector |
| `name_template` | regex form | Output template with capture references |
| `names` | exact form | Non-empty exact-ID table |
| `occurrence` | no | Positive one-based repeated-ID index |
| `priority` | no | Integer, default `0` |
| `confidence` | no | Review metadata from `0` through `1` |
| `derived_from` | no | Non-empty review metadata string |

Rule names may use letters, digits, `.`, `_`, and `-`, and must start with a
letter or digit.

### Naming precedence

Matching rules maximize:

```text
(priority, exact-entry, specificity)
```

`specificity` is the number of supplied `source_regex`, `dialog_regex`, and
`occurrence` constraints. An exact `[naming.rules.names]` entry therefore beats
a regex family at the same priority even if the regex rule has more scopes.
Raise the regex rule's priority if it must intentionally override an exact
entry.

If several matching rules have the same maximum tuple, the object receives a
`naming.map-rule-error`; order in the file does not break the tie. Duplicate
matchers and duplicate rule names are rejected while loading the section.

Object names must also be unique inside a form. An automatically inferred
duplicate receives a deterministic suffix and a warning. A duplicate produced
by an explicit naming rule is an error, even though a suffix is retained in
the diagnostic model for continued batch reporting.

Dialog names also determine output filenames: the `.ui` basename is exactly
the root widget `name`. Automatic collisions across source subsystems apply the
same stable resource-ID suffix to both. A collision between explicit dialog
names is `output.collision` and the later form is skipped.

Every loaded rule or exact entry that does not name a converted object produces
`naming-map.unused-rule`. This is a warning, so it fails only under `--strict`.

### Generated suggestions

Every run writes `rc2ui-name-suggestions.toml`. It is a mergeable TOML snippet
containing `[[naming.rules]]` entries grouped by source and dialog. Repeated IDs
use explicit occurrence rules. Confidence and derivation fields explain how
each suggestion was obtained.

A practical review loop is:

```powershell
Get-Content generated-ui\rc2ui-name-suggestions.toml | Add-Content config\rc2ui.toml
rc2ui validate-config config\rc2ui.toml
rc2ui convert --manifest config\rc2ui.toml
```

Review the snippet before merging; the command above is only an example of the
final append operation.

## Controls section

The optional `controls` table changes Qt classes, roles, size policies,
constant properties, optional button-group membership, and reviewed exact
many-to-one project compositions.

| Top-level field | Type | Meaning |
| --- | --- | --- |
| `widgets` | array of tables | Reusable profiles written as `[[controls.widgets]]` |
| `rules` | array of tables | Family selectors written as `[[controls.rules]]` |
| `bindings` | array of tables | Exact groups written as `[[controls.bindings]]` |
| `compounds` | array of tables | Exact many-to-one sets written as `[[controls.compounds]]` |

The schema has four sections:

```text
[[controls.widgets]]   reusable output profiles
[[controls.rules]]     class/regex/style families
[[controls.bindings]]  compact exact class-and-ID entries
[[controls.compounds]] exact control sets replaced by one profile
```

### Widget profiles

A standard Qt profile needs no promoted-widget metadata:

```toml
[[controls.widgets]]
name = "date-editor"
qt_class = "QDateEdit"
role = "input"
expands_horizontally = true
text_property = "displayFormat"
```

A project class normally declares a header and base class:

```toml
[[controls.widgets]]
name = "project-grid"
qt_class = "Company::GridWidget"
role = "input"
header = "company/gridwidget.h"
extends = "QWidget"
container = false
expands_horizontally = true
expands_vertically = true
text_property = "windowTitle"
warning = "Review the project grid data model after setupUi"

[controls.widgets.properties]
enabled = true
pageSize = 100
opacity = 0.75
modeName = "compact"
displayMode = { enum = "Company::GridWidget::Compact" }
storageKey = { cstring = "settings/results" }
```

`header` creates a Designer `<customwidgets>` entry. `extends` defaults to
`QWidget`, but `extends` and `container` may only be present when `header` is
present. Set `container = true` only when Designer should treat the project
widget as a container.

The project class must be available to `uic`, provide a compatible constructor,
derive from the declared base, and expose every configured Qt property.

### Widget-profile fields

| Field | Type | Default | Meaning |
| --- | --- | --- | --- |
| `name` | string | required | Unique profile name used by rules |
| `qt_class` | C++ class string | required | Result class, including qualified names such as `Company::Editor` |
| `role` | enum string | `input` | Semantic role used by naming and layout |
| `header` | string | unset | Header for a Designer promoted widget |
| `extends` | C++ class string | `QWidget` | Promoted widget base; requires `header` |
| `container` | boolean | `false` | Designer container flag; requires `header` |
| `expands_horizontally` | boolean | `false` | Horizontal expanding policy |
| `expands_vertically` | boolean | `false` | Vertical expanding policy |
| `text_property` | Qt identifier | unset | Property receiving source control text |
| `properties` | TOML table | `{}` | Constant Qt properties |
| `warning` | string | unset | Mapping warning attached to converted controls |

When a matched profile has `warning`, layout generation emits it as a
`mapping.fallback` diagnostic. Use this for a deliberate placeholder that still
requires project review.

Valid roles are:

```text
label, input, action, display, group, container, decoration, unknown
```

Choose the role by behavior, not appearance. For example, a read-only value is
usually `display`, a clickable project button is `action`, and a frame is
`decoration`. Roles influence label association and compound/layout heuristics.

### Property types and translation

Control-section property values support:

| TOML value | Qt model/XML meaning |
| --- | --- |
| string | non-translatable Qt string |
| boolean | `<bool>` |
| integer | `<number>` |
| finite float | `<double>` |
| `{ enum = "..." }` | `<enum>` |
| `{ cstring = "..." }` | `<cstring>` |

Constant strings are marked `notr`. Text copied through `text_property` is a
translatable source string. If both define the same property, source text wins
for controls that contain text.

Property names, `text_property`, `button_group`, and runtime property names must
be valid Qt identifiers. Enum and C-string inline tables must contain exactly
one supported key.

### Regex rules

Use a rule to convert a whole family:

```toml
[[controls.rules]]
name = "registered-result-grids"
widget = "project-grid"
source_regex = 'resources/.*\.rc'
dialog_regex = 'IDD_.*RESULTS'
win_class = "MyCompanyGrid"
id_regex = 'IDC_(PRIMARY|SECONDARY)_GRID'
priority = 50
```

Every rule requires exactly one class selector:

```toml
# Choose exactly one:
win_class = "MyCompanyGrid"             # case-insensitive exact match
# win_class_regex = 'Company(Grid|Tree)' # case-sensitive Python regex
```

The optional selectors are independent. Omitting an ID, source, or dialog scope
means any value in that dimension.

Use `occurrence` for repeated IDs. It is a positive, one-based source
occurrence.

### Style matching

Style selection uses the compiled Win32 style:

```text
(control.style & style_mask) == style_value
```

For example:

```toml
[[controls.rules]]
name = "project-owner-draw-buttons"
widget = "project-button"
win_class = "Button"
style_mask = 0x0000000f
style_value = 0x0000000b
priority = 20
```

`style_value` may not set a bit outside `style_mask`. Both fields default to
zero, which means no style restriction. Hexadecimal TOML integers are the most
readable form. The parser also accepts base-zero integer strings.

### Exact class-and-ID bindings

Use bindings when reviewed controls do not form one regex family:

```toml
[[controls.bindings]]
name = "settings-project-controls"
source_regex = 'resources/settings\.rc'
dialog_regex = "IDD_SETTINGS"
priority = 200

controls = [
  { win_class = "Edit", id = "IDC_PROJECT_PATH", widget = "path-editor", runtime_configured = ["text"] },
  { win_class = "Button", id = "IDC_COLOR", widget = "color-button" },
  { win_class = "MyCompanyGrid", id = "IDC_RESULTS", widget = "project-grid" },
]
```

The binding block supplies shared source/dialog scope and priority. Every inline
entry requires `win_class`, exact `id`, and `widget`. Entries may additionally
set `occurrence`, `style_mask`, `style_value`, `button_group`, and
`runtime_configured`.

There is intentionally no per-entry priority or regex class in a binding. Split
entries into another binding group when they need a different scope or
priority. Exact IDs still match any recovered alias and `#ordinal`.

One binding may contain hundreds of inline entries. Keep each inline table on
one TOML line, or use regular `[[controls.rules]]` when a selector needs a
multiline description.

### Exact many-to-one compounds

Use a compound when two or more exact RC controls are one application concept
and must become one standard or promoted Qt widget:

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
dialog_regex = "IDD_EXAMPLE_OPTIONS"
priority = 100
primary = { win_class = "LegacyChoice", id = "IDC_CHOICE_PRIMARY" }
members = [
  { win_class = "LegacyChoice", id = "IDC_CHOICE_SECONDARY" },
]
runtime_configured = ["selection"]

[[naming.rules]]
name = "choice-control-names"
kind = "control"

[naming.rules.names]
IDC_CHOICE_PRIMARY = "choiceSelector"
```

The compound references `choice-selector`; custom class, header, base class,
role, properties, and expansion behavior therefore remain defined once in the
widget profile. The primary is structural: its ID supplies the existing naming
pipeline and its source control carries the result. Every member rectangle,
including the primary, contributes to the emitted widget's union rectangle.
Secondary controls remain in the JSON report with `emitted = false`.

Every selector requires exact `win_class` and exact `id`. Class matching is
case-insensitive. IDs are case-sensitive and match any recovered symbolic
alias, named resource value, or `#ordinal`. Optional `occurrence` disambiguates
a repeated class-and-ID pair. The `members` array must be non-empty, selectors
must be distinct, and source order has no selection role.

A compound may also set `source_regex`, `dialog_regex`, integer `priority`, and
a `runtime_configured` property-name array. Regular expressions use
`re.fullmatch`. Exact membership is checked in the default language and
recorded as evidence across aligned language variants. A missing member leaves
the controls unchanged and produces `control-map.unused-compound`.

Rules resolving the same complete set maximize `(priority, specificity)`,
where specificity counts source and dialog scope. Equal leaders are
`control-compound.ambiguous-rule`. Repeated-ID ambiguity is
`control-compound.ambiguous-member`. Overlapping many-to-one replacements and
members already claimed by an explicit one-to-one rule are errors; TOML order
never grants ownership.

Compound blocks and member selectors accept exactly these fields:

| Location | Fields |
| --- | --- |
| `[[controls.compounds]]` | required `name`, `widget`, `primary`, non-empty `members`; optional `source_regex`, `dialog_regex`, `priority`, `runtime_configured` |
| `primary` or one `members` entry | required `win_class`, `id`; optional `occurrence` |

### Runtime-configured properties

Use `runtime_configured` to record properties supplied by application code:

```toml
runtime_configured = ["text", "currentIndex", "enabled"]
```

This is an array of unique Qt property names; omit it when there is no runtime
contract. It records metadata in `rc2ui-report.json`; it does not set values,
generate application code, or suppress unrelated validation.

### Runtime placeholders as radio buttons

Define one reusable profile and assign a shared `button_group`:

```toml
[[controls.widgets]]
name = "radio-button"
qt_class = "QRadioButton"
role = "input"
expands_horizontally = true
text_property = "text"

[[controls.rules]]
name = "runtime-mode-options"
widget = "radio-button"
dialog_regex = "IDD_SETTINGS"
win_class = "ProjectPlaceholder"
id_regex = 'IDC_MODE_.*'
button_group = "modeButtonGroup"
runtime_configured = ["checked"]
priority = 100
```

The emitter adds a `buttonGroup` attribute to every mapped button and a real
top-level `QButtonGroup` declaration. A project-specific result class may use
the same feature when it derives from `QAbstractButton`.

The group name is a Qt object name and must not collide with a widget, layout,
spacer, or other non-group object. Reusing the name on all intended buttons is
what places them in the same group. UI validation reports invalid collisions.

### Control-rule fields

| Field | Required | Meaning |
| --- | --- | --- |
| `name` | yes | Unique rule name |
| `widget` | yes | Existing widget-profile name |
| `win_class` / `win_class_regex` | exactly one | Source Win32 class selector |
| `source_regex` | no | Source path scope |
| `dialog_regex` | no | Dialog ID scope |
| `id_regex` | no | Control ID selector |
| `occurrence` | no | Positive one-based repeated-ID index |
| `style_mask` / `style_value` | no | Compiled style-bit condition |
| `priority` | no | Integer, default `0` |
| `button_group` | no | Result `QButtonGroup` object name |
| `runtime_configured` | no | Unique property-name array |

Rule, binding, and profile names may use letters, digits, `.`, `_`, and `-`,
and must start with a letter or digit.

Binding blocks and entries accept exactly these fields:

| Location | Fields |
| --- | --- |
| `[[controls.bindings]]` | required `name`, required non-empty `controls`; optional `source_regex`, `dialog_regex`, `priority` |
| one `controls` entry | required `win_class`, `id`, `widget`; optional `occurrence`, `style_mask`, `style_value`, `button_group`, `runtime_configured` |

### Control-rule precedence

All matching rules maximize:

```text
(
  priority,
  is_exact_binding,
  specificity,
  style_mask.bit_count(),
)
```

Specificity counts source scope, dialog scope, exact class, ID regex, exact-ID
identity, and occurrence. An exact class is intentionally stronger than a class
regex. An exact binding beats a regex rule at the same priority. A rule with
more checked style bits wins only after the earlier tuple components tie.

An equal top rank is `control-map.ambiguous`. Duplicate matchers are rejected
when the section is loaded. Every unmatched rule and every unmatched expanded
binding entry produces `control-map.unused-rule`.

An explicit control-rule match is authoritative for that control. Compound
detectors exclude it so a reviewed custom mapping cannot later be absorbed into
an edit/up-down, browse, slider/value, or list/action composition.

### Compiled class semantics

Do not select rules by the RC directive spelling. A compiled resource does not
retain whether a source line used `CONTROL`, `PUSHBUTTON`, `AUTORADIOBUTTON`, or
another shorthand. Select the compiled class, styles, and ID.

Standard `Button` radio styles already map to `QRadioButton`, and other standard
classes use the built-in mapper. Add a control rule only when project
semantics differ from that built-in result.

## Semantics section

The optional `semantics` table controls detected multi-control compositions and
consists of `[[semantics.rules]]` blocks.

| Top-level field | Type | Meaning |
| --- | --- | --- |
| `rules` | array of tables | Semantic policy blocks written as `[[semantics.rules]]` |

Detected kinds are:

```text
edit-updown
edit-browse
slider-value
list-actions
```

Available actions are:

| Action | Effect |
| --- | --- |
| `suggest` | Record the candidate without changing output |
| `keep` | Explicitly veto automatic conversion |
| `bundle` | Record an association while retaining all widgets in the shared grid |
| `replace` | Emit the primary result and consume secondary widgets |

Without a matching rule, each detector uses its conservative default. An
edit/up-down pair defaults to `suggest`. A strong browse pair defaults to
`bundle`; high-confidence slider/value and list/action candidates may also
bundle. No detector performs a many-to-one replacement without an explicit
rule.

`UDS_AUTOBUDDY` is an explicit WinAPI binding rather than a source-order hint:
the immediately preceding z-order control is the buddy. If it is an edit,
rc2ui detects the pair even when the up-down coordinates do not overlap it.
With `UDS_ALIGNLEFT` or `UDS_ALIGNRIGHT`, retained controls are placed using
their effective runtime geometry before layout inference. This correction does
not require a replacement rule.

### Select a candidate

```toml
[[semantics.rules]]
name = "keep-special-editor"
kind = "edit-updown"
action = "keep"
source_regex = 'resources/settings\.rc'
dialog_id = "IDD_ADVANCED_SETTINGS"
primary_id = "IDC_SPECIAL_VALUE"
member_id = "IDC_SPECIAL_SPIN"
label_regex = "(?i)special value"
priority = 1000
```

Selectors use `fullmatch`:

- `source_regex` matches the source path;
- `dialog_id` matches any symbolic/name/numeric dialog selector;
- `primary_id` matches any ID selector of the candidate's primary control;
- `member_id` must match at least one ID selector for every secondary member;
- `label_regex` matches any associated label text collected across aligned
  language variants.

`member_id` is therefore important for `list-actions`: every secondary action
button must satisfy the pattern, not merely one button.

Omit any selector to accept every value on that axis. A rule with only `kind`
and `action` is a project-wide policy for that compound kind.

### Replace an edit/up-down pair

Many-to-one replacement currently supports `QSpinBox` and `QDoubleSpinBox`:

```toml
[[semantics.rules]]
name = "floating-parameters"
kind = "edit-updown"
action = "replace"
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
value = 1.0
```

For an integer result:

```toml
[[semantics.rules]]
name = "item-counts"
kind = "edit-updown"
action = "replace"
primary_id = "IDC_.*_COUNT"
member_id = "IDC_.*_SPIN"
result = "QSpinBox"

[semantics.rules.properties]
minimum = 0
maximum = 100000
singleStep = 1
```

`result` is required for `replace` and is invalid for other actions.
`[semantics.rules.properties]` is also valid only for `replace`. Property names
must be Qt identifiers; values may be strings, booleans, integers, or finite
floats.

The numeric properties `minimum`, `maximum`, `singleStep`, and `value` must be
numeric. They must be integers for `QSpinBox`. `decimals` is an integer and is
valid only for `QDoubleSpinBox`.

### Runtime-configured replacement

If application code sets the numeric contract after `setupUi`, record that
instead of inventing a range:

```toml
[[semantics.rules]]
name = "runtime-range-count"
kind = "edit-updown"
action = "replace"
primary_id = "IDC_RUNTIME_COUNT"
member_id = "IDC_RUNTIME_COUNT_SPIN"
result = "QSpinBox"
runtime_configured = true
```

`runtime_configured` is a boolean in the semantics section, unlike the
property-name array in the controls section. It is valid only for `replace`.

Without `runtime_configured = true`, a spin-box replacement that lacks either
`minimum` or `maximum` emits `semantic.range-unspecified`. It is a warning, not
a load error, but it fails conversion under `--strict`.

### Semantic-rule fields

| Field | Required | Meaning |
| --- | --- | --- |
| `name` | yes | Unique rule name |
| `kind` | yes | Detected compound kind |
| `action` | yes | `suggest`, `keep`, `bundle`, or `replace` |
| `source_regex` | no | Source path selector |
| `dialog_id` | no | Dialog ID selector |
| `primary_id` | no | Primary control ID selector |
| `member_id` | no | Selector that every secondary control must satisfy |
| `label_regex` | no | Associated label selector across languages |
| `result` | replace only | `QSpinBox` or `QDoubleSpinBox` |
| `properties` | replace only | Result Qt properties |
| `runtime_configured` | replace only | Boolean runtime-contract marker |
| `priority` | no | Integer, default `0` |

### Semantic precedence and conflicts

Matching semantic rules maximize:

```text
(priority, specificity)
```

Specificity is the number of supplied source, dialog, primary-ID, member-ID,
and label selectors. Equal leaders produce `semantic-map.ambiguous` and leave
that candidate unchanged.

Several logical bundles may overlap because no widget is consumed. Replacement
candidates do consume controls. Explicit overlapping replacements are a
`semantic.compound-conflict`; the conflicting candidate is kept unchanged.
Otherwise conflict resolution prefers an explicit rule, then rule priority,
candidate confidence, source order, and kind as deterministic tie-breaks.

Every rule that matches no detected candidate produces
`semantic-map.unused-rule`. A rule may be unused because the geometry did not
form a candidate, because a project control rule excluded a member from
detection, or because its selectors are stale.

### Replacement provenance

The result uses the primary control's ID and object name. The output rectangle
is normally the union of all members, and an associated label buddy is
redirected to the result. For an aligned `UDS_AUTOBUDDY` pair, WinAPI treats the
edit rectangle as the combined runtime footprint, so replacement uses that
rectangle and ignores the up-down's placeholder position. Secondary source
controls remain in `rc2ui-report.json` with `emitted = false`.

The report stores kind, action, all source IDs and object names, confidence,
evidence, geometry policy, eligible and supporting LANGIDs, matched rule,
result class, and any conflict. This gives application code enough information
to replace references to a secondary up-down control deliberately.

## Putting the sections together

A practical project layout is:

```text
project/
  config/
    rc2ui.toml
  resources/
    main.rc
  build/
    application.exe
```

`config/rc2ui.toml`:

```toml
version = 1
project_root = ".."
output = "generated-ui"
default_language = 1033

[validation]
qt = "auto"

[[input_groups]]
rc = ["resources/main.rc"]
resources = ["build/application.exe"]

[[naming.rules]]
name = "dialog-names"
kind = "dialog"
id_regex = 'IDD_(?P<name>[A-Z0-9_]+)'
name_template = "${name}_DIALOG"

[[controls.widgets]]
name = "project-editor"
qt_class = "Company::EditorWidget"
role = "input"
header = "company/editorwidget.h"
extends = "QWidget"

[[controls.rules]]
name = "project-editors"
widget = "project-editor"
win_class = "CompanyEditor"

[[semantics.rules]]
name = "keep-special-values"
kind = "edit-updown"
action = "keep"
primary_id = "IDC_SPECIAL_VALUE"
```

Run from any directory:

```sh
rc2ui convert --manifest /path/to/project/config/rc2ui.toml
```

The sections retain a stable processing order:

1. top-level configuration and input groups choose source and compiled facts;
2. one-to-one control rules choose Qt classes and roles;
3. exact control compounds and semantic rules may combine eligible controls;
4. naming rules assign public object names, including compound primaries;
5. layout inference uses geometry and multilingual evidence;
6. reports retain every matched rule and destructive transformation.

`rc2ui validate-config config/rc2ui.toml` validates the complete file. Invalid
top-level data or any invalid customization section prevents conversion, so a
run never silently drops part of the project policy.

## Troubleshooting

### A regex rule never matches

- Remember that matching uses `fullmatch`.
- Add `.*` around a substring pattern when appropriate.
- Use `/` in source paths.
- Inspect the symbolic and numeric IDs in `rc2ui-report.json`.
- Quote `#ordinal` keys in TOML exact-name tables.
- Check `occurrence` for repeated IDs.

### A rule is reported unused

Unused warnings are per complete batch. Confirm that the relevant input group
was included and that a semantic candidate was actually detected. For exact
control bindings, each inline entry is tracked separately.

### Two rules are ambiguous

Do not reorder them. Increase the intended winner's `priority` or give it a
more precise source, dialog, ID, occurrence, or style scope. Deterministic
ambiguity errors are intentional.

### A custom widget fails Qt validation

Check `header`, `extends`, constructor compatibility, and property names. The
runtime checker substitutes promoted widgets with their base classes, so it can
validate the surrounding layout but not project-specific behavior.

### A radio group is missing

Confirm every intended rule or binding uses the identical valid
`button_group`, and that the result class derives from `QAbstractButton`. Check
the control-rule provenance and unused-rule warnings in the report.

### An edit/up-down pair was not replaced

A semantic rule applies only after the detector finds a candidate. Inspect the
compound evidence in the report. Ordinary pairs require compatible geometry;
`UDS_AUTOBUDDY` pairs require the edit to immediately precede the up-down in
z-order. Also confirm that neither member was explicitly consumed by a control
rule, because explicit project mappings intentionally bypass compound
detection.

### A generated range is unsafe

Set `minimum` and `maximum` explicitly in the semantic replacement, or declare
`runtime_configured = true` and set the range after `setupUi`. Never rely on the
Qt spin-box default range for application data.

## Complete example

The complete ready-to-edit configuration is
[examples/rc2ui.toml](../examples/rc2ui.toml).
