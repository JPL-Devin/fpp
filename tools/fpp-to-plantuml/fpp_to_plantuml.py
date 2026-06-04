#!/usr/bin/env python3
"""Translate FPP state machine definitions into PlantUML state diagrams.

This tool consumes the ``fpp-ast.json`` file produced by ``fpp-to-json`` and
emits one PlantUML state diagram (``@startuml`` ... ``@enduml``) for every
state machine definition found in the model.

Usage:

    # Generate JSON from an FPP model (produces fpp-ast.json in the cwd)
    fpp-to-json my_model.fpp

    # Translate every state machine into PlantUML, writing to stdout
    fpp_to_plantuml.py fpp-ast.json

    # Or read the AST JSON from stdin
    fpp-to-json my_model.fpp && fpp_to_plantuml.py < fpp-ast.json

The resulting text can be rendered with PlantUML (https://plantuml.com), e.g.

    fpp_to_plantuml.py fpp-ast.json > model.puml
    plantuml model.puml          # produces model.png

Only the AST (``fpp-ast.json``) is required; the location map and analysis
files are not used. Target names in transitions (``enter S``) are resolved
using FPP's lexical scoping rules (innermost enclosing scope first).
"""

import argparse
import json
import sys
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# AST JSON helpers
#
# The JSON serialization follows a small set of conventions:
#   * AstNode[X]      -> {"AstNode": {"data": <X>, "id": <int>}}
#   * Annotated[X]    -> [<pre-annotations>, <X>, <post-annotations>]
#   * sealed trait    -> {"<CaseClassName>": {<fields>}}
#   * Option[X]       -> {"Some": <X>} or "None"
#   * Ident           -> a plain string
# ---------------------------------------------------------------------------


def node_data(ast_node: dict) -> dict:
    """Return the ``data`` payload of an ``AstNode`` wrapper."""
    return ast_node["AstNode"]["data"]


def node_id(ast_node: dict) -> int:
    """Return the unique ``id`` of an ``AstNode`` wrapper."""
    return ast_node["AstNode"]["id"]


def opt(value):
    """Unwrap an ``Option``; return ``None`` for ``"None"``."""
    if value == "None" or value is None:
        return None
    return value["Some"]


def member_parts(member: list) -> Tuple[List[str], str, dict]:
    """Split an annotated member ``[pre, node, post]`` into (annotations, kind, body)."""
    pre, node_wrap, _post = member
    kind = next(iter(node_wrap))
    return pre, kind, node_wrap[kind]


def ident_list(nodes: List[dict]) -> List[str]:
    """Extract the identifier strings from a list of ``AstNode[Ident]``."""
    return [node_data(n) for n in nodes]


def qual_ident_parts(ast_node: dict) -> List[str]:
    """Flatten a ``QualIdent`` AstNode into a list of name components."""
    data = node_data(ast_node)
    kind = next(iter(data))
    body = data[kind]
    if kind == "Unqualified":
        return [body["name"]]
    # Qualified: qualifier (a QualIdent) followed by a trailing name.
    return qual_ident_parts(body["qualifier"]) + [node_data(body["name"])]


# ---------------------------------------------------------------------------
# Model representation
# ---------------------------------------------------------------------------


class Scope:
    """A state machine or state, holding the states/choices defined within it."""

    def __init__(self, alias: str, parent: Optional["Scope"]):
        self.alias = alias
        self.parent = parent
        # Direct child states/choices, keyed by their unqualified name.
        self.children: Dict[str, "Scope"] = {}
        self.is_choice = False

    def resolve(self, components: List[str]) -> Optional["Scope"]:
        """Resolve a (possibly qualified) target name to a Scope.

        Unqualified names are searched in this scope and then outward through
        enclosing scopes, matching FPP's innermost-first lexical scoping.
        """
        head = components[0]
        scope: Optional[Scope] = self
        while scope is not None:
            if head in scope.children:
                target = scope.children[head]
                for name in components[1:]:
                    target = target.children.get(name)
                    if target is None:
                        return None
                return target
            scope = scope.parent
        return None


def sanitize(name: str) -> str:
    """Turn a qualified path component string into a safe PlantUML identifier."""
    return "".join(ch if ch.isalnum() else "_" for ch in name)


# ---------------------------------------------------------------------------
# PlantUML generation
# ---------------------------------------------------------------------------


class StateMachineWriter:
    """Render a single ``DefStateMachine`` AST node as a PlantUML diagram."""

    # State-machine members that describe structure; everything else
    # (types, constants, actions, guards, signals, ...) is ignored.
    STRUCTURAL = {
        "DefState",
        "DefChoice",
        "SpecInitialTransition",
        "SpecStateEntry",
        "SpecStateExit",
        "SpecStateTransition",
    }

    def __init__(self, include_annotations: bool = True):
        self.include_annotations = include_annotations
        self.lines: List[str] = []

    # -- scope construction (first pass) ------------------------------------

    def _build_scope(self, members: List[list], alias_prefix: str,
                     parent: Optional[Scope]) -> Scope:
        scope = Scope(alias_prefix, parent)
        for member in members:
            _pre, kind, body = member_parts(member)
            ast_node = body["node"]
            data = node_data(ast_node)
            if kind == "DefState":
                name = data["name"]
                child_alias = f"{alias_prefix}_{sanitize(name)}" if alias_prefix else sanitize(name)
                child = self._build_scope(data["members"], child_alias, scope)
                scope.children[name] = child
            elif kind == "DefChoice":
                name = data["name"]
                child_alias = f"{alias_prefix}_{sanitize(name)}" if alias_prefix else sanitize(name)
                child = Scope(child_alias, scope)
                child.is_choice = True
                scope.children[name] = child
        return scope

    # -- emission helpers ---------------------------------------------------

    def _emit(self, indent: int, text: str) -> None:
        self.lines.append("  " * indent + text)

    @staticmethod
    def _annotation_text(pre: List[str]) -> str:
        return " ".join(s.strip() for s in pre).strip()

    @staticmethod
    def _actions_suffix(actions: List[str]) -> str:
        return " / " + "; ".join(actions) if actions else ""

    def _transition_label(self, signal: Optional[str], guard: Optional[str],
                          actions: List[str]) -> str:
        parts = []
        if signal:
            parts.append(signal)
        if guard:
            parts.append(f"[{guard}]")
        label = " ".join(parts)
        return label + self._actions_suffix(actions)

    def _target_alias(self, scope: Scope, target_node: dict) -> str:
        components = qual_ident_parts(target_node)
        resolved = scope.resolve(components)
        if resolved is not None:
            return resolved.alias
        # Fall back to a best-effort alias if resolution fails.
        return sanitize(".".join(components))

    # -- emission (second pass) ---------------------------------------------

    def _emit_scope_inner(self, members: List[list], scope: Scope,
                          indent: int) -> None:
        """Emit everything that lives *inside* a scope's block."""
        for member in members:
            pre, kind, body = member_parts(member)
            if kind == "DefState":
                self._emit_state(member, scope, indent)
            elif kind == "DefChoice":
                self._emit_choice(pre, body, scope, indent)
        # The scope's own initial transition (scopes the [*] to this block).
        for member in members:
            _pre, kind, body = member_parts(member)
            if kind == "SpecInitialTransition":
                data = node_data(body["node"])
                expr = node_data(data["transition"])
                actions = ident_list(expr["actions"])
                target = self._target_alias(scope, expr["target"])
                suffix = self._actions_suffix(actions)
                if suffix:
                    self._emit(indent, f"[*] --> {target} :{suffix}")
                else:
                    self._emit(indent, f"[*] --> {target}")

    def _emit_choice(self, pre: List[str], body: dict, scope: Scope,
                     indent: int) -> None:
        data = node_data(body["node"])
        name = data["name"]
        child = scope.children[name]
        alias = child.alias
        if alias == name:
            self._emit(indent, f"state {name} <<choice>>")
        else:
            self._emit(indent, f'state "{name}" as {alias} <<choice>>')
        if self.include_annotations:
            annotation = self._annotation_text(pre)
            if annotation:
                self._emit(indent, f"{alias} : {annotation}")
        guard = node_data(data["guard"])
        if_expr = node_data(data["ifTransition"])
        else_expr = node_data(data["elseTransition"])
        if_target = self._target_alias(scope, if_expr["target"])
        else_target = self._target_alias(scope, else_expr["target"])
        if_label = f"[{guard}]" + self._actions_suffix(ident_list(if_expr["actions"]))
        else_label = "[else]" + self._actions_suffix(ident_list(else_expr["actions"]))
        self._emit(indent, f"{alias} --> {if_target} : {if_label}")
        self._emit(indent, f"{alias} --> {else_target} : {else_label}")

    def _emit_state(self, member: list, parent_scope: Scope, indent: int) -> None:
        pre, _kind, body = member_parts(member)
        data = node_data(body["node"])
        name = data["name"]
        scope = parent_scope.children[name]
        alias = scope.alias
        members = data["members"]
        composite = any(
            member_parts(m)[1] in ("DefState", "DefChoice") for m in members
        )

        header = f"state {name}" if alias == name else f'state "{name}" as {alias}'
        if composite:
            self._emit(indent, header + " {")
            self._emit_scope_inner(members, scope, indent + 1)
            self._emit(indent, "}")
        else:
            self._emit(indent, header)

        # Description lines and transitions for this state (valid anywhere).
        if self.include_annotations:
            annotation = self._annotation_text(pre)
            if annotation:
                self._emit(indent, f"{alias} : {annotation}")
        self._emit_state_specifiers(members, scope, alias, indent)

    def _emit_state_specifiers(self, members: List[list], scope: Scope,
                               alias: str, indent: int) -> None:
        for member in members:
            _pre, kind, body = member_parts(member)
            data = node_data(body["node"])
            if kind == "SpecStateEntry":
                actions = ident_list(data["actions"])
                self._emit(indent, f"{alias} : entry{self._actions_suffix(actions)}")
            elif kind == "SpecStateExit":
                actions = ident_list(data["actions"])
                self._emit(indent, f"{alias} : exit{self._actions_suffix(actions)}")
            elif kind == "SpecStateTransition":
                self._emit_transition(data, scope, alias, indent)

    def _emit_transition(self, data: dict, scope: Scope, alias: str,
                         indent: int) -> None:
        signal = node_data(data["signal"])
        guard_node = opt(data["guard"])
        guard = node_data(guard_node) if guard_node is not None else None
        transition_or_do = data["transitionOrDo"]
        kind = next(iter(transition_or_do))
        body = transition_or_do[kind]
        if kind == "Do":
            # Internal transition: no target, stays in the current state.
            actions = ident_list(body["actions"])
            label = self._transition_label(signal, guard, actions)
            self._emit(indent, f"{alias} : {label}")
        else:  # Transition: external, has a target.
            expr = node_data(body["transition"])
            actions = ident_list(expr["actions"])
            target = self._target_alias(scope, expr["target"])
            label = self._transition_label(signal, guard, actions)
            self._emit(indent, f"{alias} --> {target} : {label}")

    # -- entry point --------------------------------------------------------

    def render(self, sm_data: dict, annotation: str = "") -> str:
        name = sm_data["name"]
        members = opt(sm_data["members"]) or []
        machine_scope = self._build_scope(members, "", None)

        self.lines = []
        self._emit(0, "@startuml")
        self._emit(0, f"title {name}")
        if self.include_annotations and annotation:
            self._emit(0, f"note as N_{sanitize(name)}")
            self._emit(0, annotation)
            self._emit(0, "end note")
        self._emit_scope_inner(members, machine_scope, 0)
        self._emit(0, "@enduml")
        return "\n".join(self.lines) + "\n"


# ---------------------------------------------------------------------------
# AST traversal: find every state machine definition
# ---------------------------------------------------------------------------


def find_state_machines(members: List[list],
                        qualifier: List[str]) -> List[Tuple[List[str], List[str], dict]]:
    """Recursively collect ``(qualifier, annotations, sm_data)`` triples."""
    found: List[Tuple[List[str], List[str], dict]] = []
    for member in members:
        pre, kind, body = member_parts(member)
        data = node_data(body["node"])
        if kind == "DefStateMachine":
            found.append((qualifier, pre, data))
        elif kind == "DefModule":
            found.extend(
                find_state_machines(data["members"], qualifier + [data["name"]])
            )
    return found


def translate(ast_json: dict, include_annotations: bool = True) -> str:
    """Translate a parsed ``fpp-ast.json`` document into PlantUML text."""
    diagrams: List[str] = []
    for trans_unit in ast_json["ast"]:
        for qualifier, pre, sm_data in find_state_machines(trans_unit["members"], []):
            writer = StateMachineWriter(include_annotations=include_annotations)
            annotation = " ".join(s.strip() for s in pre).strip()
            diagrams.append(writer.render(sm_data, annotation))
    return "\n".join(diagrams)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Translate FPP state machine definitions (from fpp-to-json "
                    "output) into PlantUML state diagrams.",
    )
    parser.add_argument(
        "ast_json",
        nargs="?",
        default="-",
        help="Path to fpp-ast.json (default: read from stdin)",
    )
    parser.add_argument(
        "-o",
        "--output",
        help="Write PlantUML to this file (default: stdout)",
    )
    parser.add_argument(
        "--no-annotations",
        action="store_true",
        help="Do not emit FPP annotations as PlantUML notes/descriptions",
    )
    args = parser.parse_args(argv)

    if args.ast_json == "-":
        ast_json = json.load(sys.stdin)
    else:
        with open(args.ast_json, "r", encoding="utf-8") as handle:
            ast_json = json.load(handle)

    plantuml = translate(ast_json, include_annotations=not args.no_annotations)
    if not plantuml.strip():
        print("warning: no state machine definitions found in input",
              file=sys.stderr)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(plantuml)
    else:
        sys.stdout.write(plantuml)
    return 0


if __name__ == "__main__":
    sys.exit(main())
