from __future__ import annotations

import ast
import operator
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Collection, Mapping

from rc2ui.adapters.rc.text import read_rc_text, strip_rc_comments
from rc2ui.domain.diagnostics import Diagnostic, Severity


_DIRECTIVE = re.compile(r"^\s*#\s*(\w+)\b(.*)$")
_IDENTIFIER = re.compile(r"^[A-Za-z_]\w*$")
_MACRO_DEFINITION = re.compile(
    r"^([A-Za-z_]\w*)(\([^)]*\))?(?:\s+(.*))?$"
)
_INCLUDE = re.compile(r'^\s*([<"])([^>"]+)[>"]')
_INTEGER_SUFFIX = re.compile(r"\b(0[xX][0-9A-Fa-f]+|\d+)(?:[uUlL]+)\b")
_C_OCTAL = re.compile(r"\b0([0-7]+)\b")
_DEFINED_CALL = re.compile(r"\bdefined\s*\(\s*([A-Za-z_]\w*)\s*\)")
_DEFINED_NAME = re.compile(r"\bdefined\s+([A-Za-z_]\w*)")
_RESOURCE_SCRIPT_SUFFIXES = frozenset({".rc", ".rc2", ".dlg"})

# These platform/framework headers define Win32 itself, not project resource
# identities. Their values have already been compiled into the .res payload.
# Parsing them is both unnecessary and noisy, especially when the SDK is not
# installed on the machine doing the conversion.
_IGNORED_PLATFORM_HEADERS = frozenset(
    {
        "afxres.h",
        "afxwin.h",
        "atlres.h",
        "commctrl.h",
        "commdlg.h",
        "dlgs.h",
        "prsht.h",
        "richedit.h",
        "sdkddkver.h",
        "shellapi.h",
        "shlobj.h",
        "strsafe.h",
        "winbase.h",
        "windows.h",
        "windowsx.h",
        "wingdi.h",
        "winnt.h",
        "winres.h",
        "winresrc.h",
        "winuser.h",
    }
)


@dataclass(frozen=True, slots=True)
class SymbolDefinition:
    name: str
    value: int
    source: Path
    line: int


@dataclass(frozen=True, slots=True)
class SymbolTable:
    definitions: tuple[SymbolDefinition, ...]
    _by_name: dict[str, int] = field(init=False, repr=False, compare=False)
    _by_value: dict[int, tuple[str, ...]] = field(
        init=False, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        by_name: dict[str, int] = {}
        by_value: dict[int, list[str]] = {}
        for definition in self.definitions:
            by_name[definition.name] = definition.value
        for definition in self.definitions:
            if by_name.get(definition.name) != definition.value:
                continue
            names = by_value.setdefault(definition.value, [])
            if definition.name not in names:
                names.append(definition.name)
        object.__setattr__(self, "_by_name", by_name)
        object.__setattr__(
            self,
            "_by_value",
            {value: tuple(names) for value, names in by_value.items()},
        )

    def value_of(self, name: str) -> int | None:
        return self._by_name.get(name)

    def symbols_for(self, value: int) -> tuple[str, ...]:
        return self._by_value.get(value, ())


@dataclass(frozen=True, slots=True)
class PreprocessedLine:
    source: Path
    line: int
    text: str


@dataclass(frozen=True, slots=True)
class SymbolLoadResult:
    table: SymbolTable
    diagnostics: tuple[Diagnostic, ...]
    active_lines: tuple[PreprocessedLine, ...] = ()


@dataclass(frozen=True, slots=True)
class _PendingDefinition:
    name: str
    expression: str
    source: Path
    line: int


@dataclass(slots=True)
class _Conditional:
    parent_active: bool
    branch_taken: bool
    active: bool


class SymbolLoader:
    """A deliberately small resource-header preprocessor.

    It follows includes and evaluates integer object-like macros without trying
    to parse the RC language itself. Unsupported expressions are reported and
    can still be matched as numeric `#123` values in the naming map.
    """

    def __init__(
        self,
        *,
        include_paths: tuple[Path, ...] = (),
        predefined: Mapping[str, int] | None = None,
        source_encoding: str = "cp1251",
    ) -> None:
        self.include_paths = tuple(path.resolve() for path in include_paths)
        self.values = dict(predefined or {})
        self._defined_names = set(self.values)
        self._value_unavailable_names: set[str] = set()
        self.source_encoding = source_encoding
        self._definitions: list[SymbolDefinition] = []
        self._pending: list[_PendingDefinition] = []
        self._diagnostics: list[Diagnostic] = []
        self._active_lines: list[PreprocessedLine] = []
        self._visited: set[Path] = set()
        self._assumed_conditions: dict[Path, list[tuple[int, str]]] = {}

    def load(self, source: Path) -> SymbolLoadResult:
        self._visit(source.resolve())
        self._resolve_pending()
        self._report_assumed_conditions()
        return SymbolLoadResult(
            table=SymbolTable(tuple(self._definitions)),
            diagnostics=tuple(self._diagnostics),
            active_lines=tuple(self._active_lines),
        )

    def _visit(self, path: Path) -> None:
        if path in self._visited:
            return
        self._visited.add(path)
        try:
            text = read_rc_text(path, fallback_encoding=self.source_encoding)
        except (OSError, UnicodeError) as error:
            self._io_diagnostic(path, error)
            return

        logical_lines = _logical_lines(strip_rc_comments(text))
        conditions: list[_Conditional] = []
        for line_number, line in logical_lines:
            match = _DIRECTIVE.match(line)
            if not match:
                if all(condition.active for condition in conditions):
                    self._active_lines.append(
                        PreprocessedLine(path, line_number, line)
                    )
                continue
            directive = match.group(1).lower()
            argument = match.group(2).strip()
            active = all(condition.active for condition in conditions)

            if directive in {"if", "ifdef", "ifndef"}:
                parent_active = active
                condition = False
                if parent_active:
                    if directive == "ifdef":
                        condition = self._ifdef_value(
                            path,
                            line_number,
                            argument,
                        )
                    elif directive == "ifndef":
                        condition = argument not in self._defined_names
                    else:
                        condition = self._condition_value(
                            path, line_number, argument
                        )
                conditions.append(
                    _Conditional(
                        parent_active=parent_active,
                        branch_taken=condition,
                        active=parent_active and condition,
                    )
                )
                continue

            if directive in {"elif", "else"}:
                if not conditions:
                    self._warning(path, line_number, f"unmatched #{directive}")
                    continue
                current = conditions[-1]
                if directive == "else":
                    take = current.parent_active and not current.branch_taken
                else:
                    take = (
                        current.parent_active
                        and not current.branch_taken
                        and self._condition_value(path, line_number, argument)
                    )
                current.active = take
                current.branch_taken = current.branch_taken or take
                continue

            if directive == "endif":
                if conditions:
                    conditions.pop()
                else:
                    self._warning(path, line_number, "unmatched #endif")
                continue

            if not active:
                continue
            if directive == "include":
                self._include(path, line_number, argument)
            elif directive == "define":
                self._define(path, line_number, argument)
            elif directive == "undef":
                self._undefine(argument)

        if conditions:
            self._warning(path, logical_lines[-1][0] if logical_lines else 1, "missing #endif")

    def _include(self, source: Path, line: int, argument: str) -> None:
        match = _INCLUDE.match(argument)
        if not match:
            return
        delimiter, include_name = match.groups()
        normalized_name = include_name.replace("\\", "/").rsplit("/", 1)[-1]
        if normalized_name.casefold() in _IGNORED_PLATFORM_HEADERS:
            return
        candidates = []
        if delimiter == '"':
            candidates.append(source.parent / include_name)
        candidates.extend(base / include_name for base in self.include_paths)
        for candidate in candidates:
            if candidate.is_file():
                self._visit(candidate.resolve())
                return
        if delimiter == '"':
            self._warning(source, line, f"include not found: {include_name}")

    def _condition_value(self, source: Path, line: int, expression: str) -> bool:
        prepared = _prepare_condition(expression, self._defined_names)
        expression_names = _expression_names(prepared)
        unresolved = expression_names - self.values.keys()
        if unresolved:
            if source.suffix.casefold() in _RESOURCE_SCRIPT_SUFFIXES:
                return self._unresolved_condition(source, line, expression)
        try:
            return bool(
                _evaluate(
                    prepared,
                    self.values,
                    unknown_as_zero=True,
                )
            )
        except (SyntaxError, ValueError, ZeroDivisionError):
            return self._unresolved_condition(source, line, expression)

    def _ifdef_value(self, source: Path, line: int, name: str) -> bool:
        if name in self._defined_names:
            return True
        if source.suffix.casefold() not in _RESOURCE_SCRIPT_SUFFIXES:
            return False
        self._assume_condition_true(source, line, f"defined({name})")
        return True

    def _unresolved_condition(
        self,
        source: Path,
        line: int,
        expression: str,
    ) -> bool:
        if source.suffix.casefold() in _RESOURCE_SCRIPT_SUFFIXES:
            self._assume_condition_true(source, line, expression)
            return True
        self._warning(
            source,
            line,
            f"cannot evaluate preprocessor condition: {expression}",
            code="symbols.unresolved-condition",
        )
        return False

    def _define(self, source: Path, line: int, argument: str) -> None:
        match = _MACRO_DEFINITION.fullmatch(argument)
        if match is None:
            return
        name, parameters, expression = match.groups()
        self._defined_names.add(name)
        self.values.pop(name, None)
        self._value_unavailable_names.discard(name)
        self._pending = [
            definition for definition in self._pending if definition.name != name
        ]
        if parameters is not None or expression is None:
            self._value_unavailable_names.add(name)
            return
        expression = expression.strip()
        try:
            value = _evaluate(_prepare_expression(expression), self.values)
        except (SyntaxError, ValueError, ZeroDivisionError):
            self._value_unavailable_names.add(name)
            self._pending.append(_PendingDefinition(name, expression, source, line))
            return
        self._record(name, value, source, line)

    def _undefine(self, argument: str) -> None:
        name = argument.split(None, 1)[0] if argument else ""
        if not _IDENTIFIER.fullmatch(name):
            return
        self.values.pop(name, None)
        self._defined_names.discard(name)
        self._value_unavailable_names.discard(name)
        self._pending = [
            definition for definition in self._pending if definition.name != name
        ]

    def _resolve_pending(self) -> None:
        pending = self._pending
        self._pending = []
        while pending:
            unresolved: list[_PendingDefinition] = []
            progress = False
            for definition in pending:
                try:
                    value = _evaluate(
                        _prepare_expression(definition.expression),
                        self.values,
                    )
                except (SyntaxError, ValueError, ZeroDivisionError):
                    unresolved.append(definition)
                    continue
                self._record(
                    definition.name,
                    value,
                    definition.source,
                    definition.line,
                )
                progress = True
            if not progress:
                for definition in unresolved:
                    self._warning(
                        definition.source,
                        definition.line,
                        f"cannot evaluate macro {definition.name}: "
                        f"{definition.expression}",
                        code="symbols.unresolved-expression",
                    )
                break
            pending = unresolved

    def _record(self, name: str, value: int, source: Path, line: int) -> None:
        self._defined_names.add(name)
        self._value_unavailable_names.discard(name)
        self.values[name] = value
        self._definitions.append(SymbolDefinition(name, value, source, line))

    def _assume_condition_true(
        self,
        source: Path,
        line: int,
        expression: str,
    ) -> None:
        self._assumed_conditions.setdefault(source, []).append(
            (line, expression)
        )

    def _report_assumed_conditions(self) -> None:
        for source, conditions in self._assumed_conditions.items():
            examples = "; ".join(
                f"line {line}: {expression}"
                for line, expression in conditions[:3]
            )
            if len(conditions) > 3:
                examples += f"; and {len(conditions) - 3} more"
            self._diagnostics.append(
                Diagnostic(
                    code="symbols.condition-assumed-true",
                    severity=Severity.WARNING,
                    message=(
                        f"{len(conditions)} preprocessor condition(s) could "
                        f"not be evaluated and were assumed true: {examples}"
                    ),
                    location=str(source),
                )
            )

    def _io_diagnostic(self, path: Path, error: OSError | UnicodeError) -> None:
        self._diagnostics.append(
            Diagnostic(
                code="symbols.read-error",
                severity=Severity.ERROR,
                message=str(error),
                location=str(path),
            )
        )

    def _warning(
        self,
        path: Path,
        line: int,
        message: str,
        *,
        code: str = "symbols.preprocessor",
    ) -> None:
        self._diagnostics.append(
            Diagnostic(
                code=code,
                severity=Severity.WARNING,
                message=message,
                location=f"{path}:{line}",
            )
        )


def _logical_lines(text: str) -> list[tuple[int, str]]:
    result: list[tuple[int, str]] = []
    buffer = ""
    start = 1
    for number, line in enumerate(text.splitlines(), 1):
        if not buffer:
            start = number
        if line.rstrip().endswith("\\"):
            buffer += line.rstrip()[:-1] + " "
            continue
        result.append((start, buffer + line))
        buffer = ""
    if buffer:
        result.append((start, buffer))
    return result


def _prepare_expression(expression: str) -> str:
    expression = _INTEGER_SUFFIX.sub(r"\1", expression)
    expression = _C_OCTAL.sub(r"0o\1", expression)
    return expression


def _prepare_condition(
    expression: str,
    defined_names: Collection[str],
) -> str:
    def defined(match: re.Match[str]) -> str:
        return "1" if match.group(1) in defined_names else "0"

    expression = _DEFINED_CALL.sub(defined, expression)
    expression = _DEFINED_NAME.sub(defined, expression)
    expression = expression.replace("&&", " and ").replace("||", " or ")
    expression = re.sub(r"!(?!=)", " not ", expression)
    return _prepare_expression(expression).strip()


def _expression_names(expression: str) -> set[str]:
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError:
        return set()
    return {
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name)
        and node.id not in {"and", "or", "not"}
    }


_BINARY_OPERATORS: dict[type[ast.operator], Callable[[int, int], int]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: lambda left, right: int(left / right),
    ast.FloorDiv: lambda left, right: int(left / right),
    ast.Mod: operator.mod,
    ast.LShift: operator.lshift,
    ast.RShift: operator.rshift,
    ast.BitOr: operator.or_,
    ast.BitAnd: operator.and_,
    ast.BitXor: operator.xor,
}
_UNARY_OPERATORS: dict[type[ast.unaryop], Callable[[int], int]] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
    ast.Invert: operator.invert,
    ast.Not: lambda value: int(not value),
}
_COMPARE_OPERATORS: dict[type[ast.cmpop], Callable[[int, int], bool]] = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
}


def _evaluate(
    expression: str,
    values: Mapping[str, int],
    *,
    unknown_as_zero: bool = False,
) -> int:
    tree = ast.parse(expression, mode="eval")

    def visit(node: ast.AST) -> int:
        if isinstance(node, ast.Expression):
            return visit(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, bool)):
            return int(node.value)
        if isinstance(node, ast.Name):
            if node.id in values:
                return values[node.id]
            if unknown_as_zero:
                return 0
            raise ValueError(f"unknown symbol {node.id}")
        if isinstance(node, ast.BinOp) and type(node.op) in _BINARY_OPERATORS:
            return _BINARY_OPERATORS[type(node.op)](visit(node.left), visit(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPERATORS:
            return _UNARY_OPERATORS[type(node.op)](visit(node.operand))
        if isinstance(node, ast.BoolOp):
            values_ = [bool(visit(value)) for value in node.values]
            if isinstance(node.op, ast.And):
                return int(all(values_))
            if isinstance(node.op, ast.Or):
                return int(any(values_))
        if isinstance(node, ast.Compare) and len(node.ops) == 1:
            operation = _COMPARE_OPERATORS.get(type(node.ops[0]))
            if operation is not None:
                return int(operation(visit(node.left), visit(node.comparators[0])))
        raise ValueError(f"unsupported expression node: {type(node).__name__}")

    return visit(tree)
