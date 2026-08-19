from __future__ import annotations

from rc2ui.domain.dialog import Dialog
from rc2ui.naming.resolver import NamingResult
from rc2ui.semantics.model import SemanticPlan


WS_TABSTOP = 0x00010000


def source_tab_order(
    dialog: Dialog,
    naming: NamingResult,
    semantic_plan: SemanticPlan | None = None,
) -> tuple[str, ...]:
    """Return emitted widget names in Win32 dialog-template tab order.

    Win32 walks controls with ``WS_TABSTOP`` in creation order, which is the
    declaration order stored by the compiled dialog template.  A many-to-one
    semantic replacement inherits the first tab-stop position of any source
    member and appears only once.
    """

    plan = semantic_plan or SemanticPlan()
    result: list[str] = []
    seen: set[str] = set()
    for control in sorted(dialog.controls, key=lambda item: item.order):
        if not control.style & WS_TABSTOP:
            continue
        emitted_order = plan.primary_for(control.order)
        object_name = naming.for_order(emitted_order).object_name
        if object_name in seen:
            continue
        seen.add(object_name)
        result.append(object_name)
    return tuple(result)
