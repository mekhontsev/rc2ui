# Limits of automatic conversion

An RC dialog describes its initial template, not the complete runtime UI.
`rc2ui` therefore reports rather than guesses when the missing information can
change meaning.

The following require review or later project-specific extensions:

- owner-draw controls and arbitrary registered window classes;
- controls created, moved, hidden or resized in application code;
- tab page membership implemented by the dialog procedure;
- images, icons and other referenced binary resources;
- menus attached to dialog resources;
- property sheets assembled at runtime;
- runtime show/hide logic for overlapping alternative controls;
- project-specific handling of `WM_SIZE` and anchoring libraries;
- strings loaded or constructed by application code outside dialog templates.

PE input means an ordinary, unpacked PE32/PE32+ image whose resource directory
is present in the file. Packers, encrypted/virtualized resources, resources
downloaded or constructed at runtime, 16-bit NE executables, COFF object files
and non-Windows executable formats are not decoded. The module is parsed as
bytes and never loaded or executed.

Unknown controls are emitted as layout-managed `QWidget` placeholders, with the
original class recorded in diagnostics. Supported controls continue to be
generated, so one unsupported item does not discard a complete dialog.

Simplified mode rewrites only regions whose pairwise topology can be proven
from the faithful layout model. Irregular, intentionally layered, or ambiguous
regions remain coordinate grids and may still be more cumbersome to edit in
Qt Designer. This local fallback is intentional; simplified mode never trades
known ordering, overlap, or shared-edge constraints for a smaller layout tree.

Near-identical overlapping controls are preserved in one shared grid cell and
reported as probable runtime alternatives. The converter cannot reconstruct the
condition that chooses which control is visible; application code must supply
that behavior.

Language variants can correct small coordinate mistakes and vote on grouping,
alignment and overlap relations, but they do not prove runtime ownership or
visibility. Structural differences and incomplete control matching remain in
the report for review. Generated `.ts` catalogs include dialog-template text;
menus, string tables and text assigned by application code are outside them.

Absolute child coordinates are intentionally not copied into `.ui`. They are
used as evidence for rows, columns, groups, gaps and stretch policies. This is
what allows the generated form to follow Qt font metrics, DPI and window size.
Coordinate-track minimums preserve the initial DLU proportions before Qt
distributes resize surplus. If controls lie outside the declared client rectangle,
the generated form expands its effective bounds and reports that decision rather
than clipping them.

Generated root grids also contain zero-thickness, non-translatable font rulers.
They restore the font-relative meaning of DLU after a dynamic Qt `FontChange`,
so tracks and gaps can grow with glyph metrics rather than remaining fixed in
pixels. Empty dialogs have no coordinate grid and therefore retain only their
fixed source-size floor.

The optional PyQt6/PySide6 runtime check substitutes promoted widgets with
their base Qt classes. It validates the surrounding layout but cannot reproduce a custom
widget's real `sizeHint`, painting, child objects or behavior. Its metrics and
PNG previews depend on the installed Qt version, platform style, fonts and DPI;
they are evidence for review, not deterministic conversion inputs. On an
available desktop, preview capture uses Qt's native platform and font stack;
headless systems fall back to the offscreen platform.
