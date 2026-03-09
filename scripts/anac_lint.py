#!/usr/bin/env python3
"""Lint ANAC 0.1.2 manifests for semantic issues beyond JSON Schema."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from jsonschema import Draft202012Validator


ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_SCHEMA = ROOT_DIR / "schema" / "anac-core-0.1.2.schema.json"

CEL_MACRO_PATTERN = re.compile(r"\.\s*(all|exists|exists_one|filter|map)\s*\(\s*([A-Za-z_][A-Za-z0-9_]*)")
CEL_ROOT_PATTERN = re.compile(r"(?<![\w.])([A-Za-z_][A-Za-z0-9_]*)")
STEP_REF_PATTERN = re.compile(r"(?<![\w.])steps\.([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)")
INPUT_REF_PATTERN = re.compile(r"(?<![\w.])inputs\.([A-Za-z_][A-Za-z0-9_]*)")
BINDING_REF_PATTERN = re.compile(r"(?<![\w.])bindings\.([A-Za-z_][A-Za-z0-9_]*)")
INTERPOLATION_PATTERN = re.compile(r"\$\{([^{}]+)\}")
STRING_LITERAL_PATTERN = re.compile(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'')

CEL_BUILTINS = {
    "true",
    "false",
    "null",
    "in",
    "size",
    "has",
}

ALLOWED_SYMBOLS_BY_LOCATION = {
    "entity_constraint": {"context", "entity"},
    "action_predicate": {"context", "params", "bindings"},
    "workflow_predicate": {"context", "workflow", "steps", "inputs", "current", "watch"},
    "interpolation": {"context", "workflow", "steps", "inputs", "current", "watch", "bindings", "params"},
}


@dataclass
class Issue:
    severity: str
    path: str
    code: str
    message: str


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def format_path(parts: Iterable[Any]) -> str:
    items = [str(part) for part in parts]
    return " -> ".join(items) if items else "(root)"


def strip_string_literals(expr: str) -> str:
    return STRING_LITERAL_PATTERN.sub("", expr)


def extract_cel_roots(expr: str) -> tuple[set[str], set[str]]:
    sanitized = strip_string_literals(expr)
    lambda_vars = {match.group(2) for match in CEL_MACRO_PATTERN.finditer(sanitized)}
    roots = set()
    for match in CEL_ROOT_PATTERN.finditer(sanitized):
        token = match.group(1)
        if token in lambda_vars:
            continue
        roots.add(token)
    return roots, lambda_vars


def find_step_refs(expr: str) -> list[tuple[str, str]]:
    return STEP_REF_PATTERN.findall(strip_string_literals(expr))


def find_input_refs(expr: str) -> list[str]:
    return INPUT_REF_PATTERN.findall(strip_string_literals(expr))


def find_binding_refs(expr: str) -> list[str]:
    return BINDING_REF_PATTERN.findall(strip_string_literals(expr))


def iter_interpolations(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        for match in INTERPOLATION_PATTERN.finditer(value):
            yield match.group(1)
        return
    if isinstance(value, list):
        for item in value:
            yield from iter_interpolations(item)
        return
    if isinstance(value, dict):
        for item in value.values():
            yield from iter_interpolations(item)


class ManifestLinter:
    def __init__(self, manifest: dict[str, Any], path: Path) -> None:
        self.manifest = manifest
        self.path = path
        self.issues: list[Issue] = []
        static = manifest.get("static", {})
        self.entities = {entity["id"]: entity for entity in static.get("entities", []) if "id" in entity}
        self.actions = {action["id"]: action for action in static.get("actions", []) if "id" in action}
        self.workflows = {workflow["id"]: workflow for workflow in static.get("workflows", []) if "id" in workflow}

    def add_issue(self, severity: str, path: str, code: str, message: str) -> None:
        self.issues.append(Issue(severity=severity, path=path, code=code, message=message))

    def lint(self) -> list[Issue]:
        self.check_duplicates()
        self.check_entities()
        self.check_actions()
        self.check_tier_requirements()
        self.check_workflows()
        return self.issues

    def check_duplicates(self) -> None:
        for key in ("entities", "actions", "workflows"):
            seen: set[str] = set()
            for index, item in enumerate(self.manifest.get("static", {}).get(key, [])):
                item_id = item.get("id")
                if not item_id:
                    continue
                path = f"static -> {key} -> {index} -> id"
                if item_id in seen:
                    self.add_issue("error", path, "duplicate-id", f"Duplicate {key[:-1]} id {item_id!r}")
                seen.add(item_id)

    def check_entities(self) -> None:
        for index, entity in enumerate(self.manifest.get("static", {}).get("entities", [])):
            base_path = f"static -> entities -> {index}"
            for rel_index, relationship in enumerate(entity.get("relationships", [])):
                target = relationship.get("target")
                if target and target not in self.entities:
                    path = f"{base_path} -> relationships -> {rel_index} -> target"
                    self.add_issue("error", path, "unknown-entity", f"Relationship target {target!r} is not a known entity type")
            for trans_index, transition in enumerate(entity.get("lifecycle", {}).get("transitions", [])):
                action_id = transition.get("via_action")
                if action_id is not None and action_id not in self.actions:
                    path = f"{base_path} -> lifecycle -> transitions -> {trans_index} -> via_action"
                    self.add_issue("error", path, "unknown-action", f"Lifecycle action {action_id!r} is not a known action")
            for constraint_index, constraint in enumerate(entity.get("constraints", [])):
                path = f"{base_path} -> constraints -> {constraint_index} -> predicate"
                self.check_cel_expression(
                    expr=constraint.get("predicate", ""),
                    location="entity_constraint",
                    path=path,
                    step_ids=None,
                    available_inputs=None,
                    available_bindings=None,
                    emitted_outputs=None,
                )

    def check_actions(self) -> None:
        for index, action in enumerate(self.manifest.get("static", {}).get("actions", [])):
            base_path = f"static -> actions -> {index}"
            param_names = {param.get("name") for param in action.get("parameters", []) if param.get("name")}
            bindings = action.get("bindings", {})
            for binding_name, binding in bindings.items():
                entity_type = binding.get("entity_type")
                if entity_type not in self.entities:
                    self.add_issue(
                        "error",
                        f"{base_path} -> bindings -> {binding_name} -> entity_type",
                        "unknown-entity",
                        f"Binding entity type {entity_type!r} is not declared",
                    )
                if binding.get("from") == "param":
                    root_name = str(binding.get("path", "")).split(".", 1)[0]
                    if root_name and root_name not in param_names:
                        self.add_issue(
                            "error",
                            f"{base_path} -> bindings -> {binding_name} -> path",
                            "unknown-param",
                            f"Binding path root {root_name!r} is not a declared action parameter",
                        )
            for rule_group in ("preconditions", "postconditions"):
                for rule_index, rule in enumerate(action.get(rule_group, [])):
                    path = f"{base_path} -> {rule_group} -> {rule_index} -> predicate"
                    self.check_cel_expression(
                        expr=rule.get("predicate", ""),
                        location="action_predicate",
                        path=path,
                        step_ids=None,
                        available_inputs=None,
                        available_bindings=set(bindings.keys()),
                        emitted_outputs=None,
                    )
            for param_index, param in enumerate(action.get("parameters", [])):
                constraint = param.get("constraints")
                if isinstance(constraint, str):
                    path = f"{base_path} -> parameters -> {param_index} -> constraints"
                    self.check_cel_expression(
                        expr=constraint,
                        location="action_predicate",
                        path=path,
                        step_ids=None,
                        available_inputs=None,
                        available_bindings=set(bindings.keys()),
                        emitted_outputs=None,
                    )
            req = action.get("context_requirements", {}).get("required_selection")
            if isinstance(req, dict):
                entity_type = req.get("entity_type")
                if entity_type and entity_type not in self.entities:
                    self.add_issue(
                        "error",
                        f"{base_path} -> context_requirements -> required_selection -> entity_type",
                        "unknown-entity",
                        f"Selection requirement entity type {entity_type!r} is not declared",
                    )

    def check_tier_requirements(self) -> None:
        tier = self.manifest.get("application", {}).get("tier")
        workflows = self.manifest.get("static", {}).get("workflows", [])
        revision_tracked_count = sum(1 for entity in self.entities.values() if entity.get("revision_tracked"))

        if tier == "behavioral" and not workflows:
            self.add_issue("error", "application -> tier", "tier-requirement", "Behavioral tier requires at least one workflow")
        if tier == "behavioral" and revision_tracked_count == 0:
            self.add_issue(
                "warning",
                "application -> tier",
                "tier-requirement",
                "Behavioral tier has no revision-tracked entities; optimistic concurrency cannot be exercised",
            )

    def check_workflows(self) -> None:
        for workflow_index, workflow in enumerate(self.manifest.get("static", {}).get("workflows", [])):
            base_path = f"static -> workflows -> {workflow_index}"
            step_map = self.index_steps(workflow.get("steps", []), f"{base_path} -> steps")
            subflow_map = self.index_subflows(workflow.get("subflows", []), f"{base_path} -> subflows")
            entry_point = workflow.get("entry_point")
            if entry_point not in step_map:
                self.add_issue("error", f"{base_path} -> entry_point", "unknown-step", f"Workflow entry point {entry_point!r} is not a defined step")
            self.check_step_collection(
                workflow=workflow,
                base_path=base_path,
                step_map=step_map,
                subflow_map=subflow_map,
                is_subflow=False,
                declared_inputs=set(),
            )
            for subflow_index, subflow in enumerate(workflow.get("subflows", [])):
                subflow_path = f"{base_path} -> subflows -> {subflow_index}"
                sub_step_map = self.index_steps(subflow.get("steps", []), f"{subflow_path} -> steps")
                sub_entry = subflow.get("entry_point")
                if sub_entry not in sub_step_map:
                    self.add_issue("error", f"{subflow_path} -> entry_point", "unknown-step", f"Subflow entry point {sub_entry!r} is not a defined step")
                input_names = {
                    item.get("name")
                    for item in subflow.get("inputs", [])
                    if item.get("name")
                }
                self.check_step_collection(
                    workflow=subflow,
                    base_path=subflow_path,
                    step_map=sub_step_map,
                    subflow_map={},
                    is_subflow=True,
                    declared_inputs=input_names,
                )

    def index_steps(self, steps: list[dict[str, Any]], base_path: str) -> dict[str, dict[str, Any]]:
        step_map: dict[str, dict[str, Any]] = {}
        seen: set[str] = set()
        for index, step in enumerate(steps):
            step_id = step.get("id")
            path = f"{base_path} -> {index} -> id"
            if not step_id:
                continue
            if step_id in seen:
                self.add_issue("error", path, "duplicate-id", f"Duplicate step id {step_id!r}")
            seen.add(step_id)
            step_map[step_id] = step
        return step_map

    def index_subflows(self, subflows: list[dict[str, Any]], base_path: str) -> dict[str, dict[str, Any]]:
        subflow_map: dict[str, dict[str, Any]] = {}
        seen: set[str] = set()
        for index, subflow in enumerate(subflows):
            subflow_id = subflow.get("id")
            path = f"{base_path} -> {index} -> id"
            if not subflow_id:
                continue
            if subflow_id in seen:
                self.add_issue("error", path, "duplicate-id", f"Duplicate subflow id {subflow_id!r}")
            seen.add(subflow_id)
            subflow_map[subflow_id] = subflow
        return subflow_map

    def check_step_collection(
        self,
        workflow: dict[str, Any],
        base_path: str,
        step_map: dict[str, dict[str, Any]],
        subflow_map: dict[str, dict[str, Any]],
        is_subflow: bool,
        declared_inputs: set[str],
    ) -> None:
        emitted_outputs = {step_id: set(step.get("emits", [])) for step_id, step in step_map.items()}
        for step_index, step in enumerate(workflow.get("steps", [])):
            step_path = f"{base_path} -> steps -> {step_index}"
            step_id = step.get("id", "")
            kind = step.get("kind")

            self.check_transition_targets(step, step_path, step_map)
            self.check_action_references(step, step_path)
            self.check_runtime_refs(step, step_path)
            self.check_watch_binding(step, step_path, step_map, emitted_outputs, declared_inputs)
            self.check_expected_revisions(step, step_path)
            self.check_interpolations(step, step_path, step_map, emitted_outputs, declared_inputs)
            self.check_predicates(step, step_path, step_map, emitted_outputs, declared_inputs)

            if kind == "subflow":
                workflow_ref = step.get("workflow_ref")
                if workflow_ref not in subflow_map and workflow_ref not in self.workflows:
                    self.add_issue(
                        "error",
                        f"{step_path} -> workflow_ref",
                        "unknown-workflow",
                        f"Subflow target {workflow_ref!r} is not a declared subflow or workflow",
                    )

            if is_subflow and kind == "subflow":
                self.add_issue(
                    "warning",
                    f"{step_path} -> kind",
                    "nested-subflow",
                    f"Subflow step {step_id!r} invokes another workflow; verify the runtime supports nested subflows",
                )

    def check_transition_targets(self, step: dict[str, Any], step_path: str, step_map: dict[str, dict[str, Any]]) -> None:
        kind = step.get("kind")
        targets: list[tuple[str, Any]] = []
        if kind in {"observe", "mutate", "confirm", "wait", "subflow"}:
            on_block = step.get("on", {})
            if isinstance(on_block, dict):
                targets.extend((f"{step_path} -> on -> {name}", target) for name, target in on_block.items())
        elif kind == "decide":
            targets.append((f"{step_path} -> on_true", step.get("on_true")))
            targets.append((f"{step_path} -> on_false", step.get("on_false")))
        for path, target in targets:
            if target == "end":
                continue
            if target not in step_map:
                self.add_issue("error", path, "unknown-step", f"Transition target {target!r} is not a defined step")

    def check_action_references(self, step: dict[str, Any], step_path: str) -> None:
        kind = step.get("kind")
        action_id = step.get("action")
        if not action_id:
            return
        action = self.actions.get(action_id)
        if action is None:
            self.add_issue("error", f"{step_path} -> action", "unknown-action", f"Step action {action_id!r} is not declared")
            return
        writes = action.get("writes_types", [])
        if kind == "observe" and writes:
            self.add_issue(
                "error",
                f"{step_path} -> action",
                "observe-writes",
                f"Observe step references action {action_id!r}, but that action declares writes_types {writes!r}",
            )
        has_side_effects = bool(action.get("side_effects"))
        if kind == "mutate" and not writes and not has_side_effects:
            self.add_issue(
                "warning",
                f"{step_path} -> action",
                "mutate-readonly",
                f"Mutate step references action {action_id!r}, but that action declares no writes_types",
            )

    def check_runtime_refs(self, step: dict[str, Any], step_path: str) -> None:
        for field_name in ("reads_refs", "writes_refs"):
            for index, ref in enumerate(step.get(field_name, [])):
                if ref == "context_frame":
                    continue
                entity_type = str(ref).split(":", 1)[0]
                if entity_type not in self.entities:
                    path = f"{step_path} -> {field_name} -> {index}"
                    self.add_issue("error", path, "unknown-entity", f"Runtime ref {ref!r} does not use a declared entity type")

    def check_watch_binding(
        self,
        step: dict[str, Any],
        step_path: str,
        step_map: dict[str, dict[str, Any]],
        emitted_outputs: dict[str, set[str]],
        declared_inputs: set[str],
    ) -> None:
        if step.get("kind") != "wait":
            return
        binding = step.get("watch_binding", {})
        entity_type = binding.get("entity_type")
        if entity_type not in self.entities:
            self.add_issue(
                "error",
                f"{step_path} -> watch_binding -> entity_type",
                "unknown-entity",
                f"Watch binding entity type {entity_type!r} is not declared",
            )
        ref_from = binding.get("ref_from")
        path = binding.get("path", "")
        if ref_from == "step_output":
            self.check_step_output_path(path, f"{step_path} -> watch_binding -> path", step_map, emitted_outputs)
        elif ref_from == "input":
            if not path.startswith("inputs.") or path.split(".", 1)[1] not in declared_inputs:
                self.add_issue(
                    "error",
                    f"{step_path} -> watch_binding -> path",
                    "unknown-input",
                    f"Watch binding path {path!r} does not resolve to a declared input",
                )
        elif ref_from == "binding":
            if not path.startswith("bindings."):
                self.add_issue(
                    "error",
                    f"{step_path} -> watch_binding -> path",
                    "invalid-binding-path",
                    f"Watch binding path {path!r} must start with 'bindings.' when ref_from is 'binding'",
                )

    def check_expected_revisions(self, step: dict[str, Any], step_path: str) -> None:
        if step.get("kind") != "mutate":
            return
        action_id = step.get("action")
        action = self.actions.get(action_id, {})
        expected = step.get("expected_revisions", [])
        if expected and not action.get("accepts_expected_revision", False):
            self.add_issue(
                "error",
                f"{step_path} -> expected_revisions",
                "unexpected-expected-revision",
                f"Action {action_id!r} does not declare accepts_expected_revision, but the step supplies expected revisions",
            )
        for index, item in enumerate(expected):
            entity_type = item.get("entity_type")
            entity = self.entities.get(entity_type)
            if entity is None:
                self.add_issue(
                    "error",
                    f"{step_path} -> expected_revisions -> {index} -> entity_type",
                    "unknown-entity",
                    f"Expected revision entity type {entity_type!r} is not declared",
                )
                continue
            if not entity.get("revision_tracked", False):
                self.add_issue(
                    "error",
                    f"{step_path} -> expected_revisions -> {index}",
                    "untracked-revision",
                    f"Expected revision references entity type {entity_type!r}, which is not revision-tracked",
                )
        if action.get("accepts_expected_revision") and action.get("writes_types") and not expected:
            self.add_issue(
                "warning",
                f"{step_path} -> expected_revisions",
                "missing-expected-revision",
                f"Mutate step uses revision-capable action {action_id!r} without expected revisions",
            )

    def check_interpolations(
        self,
        step: dict[str, Any],
        step_path: str,
        step_map: dict[str, dict[str, Any]],
        emitted_outputs: dict[str, set[str]],
        declared_inputs: set[str],
    ) -> None:
        for expr in iter_interpolations(step):
            self.check_cel_expression(
                expr=expr,
                location="interpolation",
                path=f"{step_path} -> interpolation",
                step_ids=set(step_map.keys()),
                available_inputs=declared_inputs,
                available_bindings=set(step.get("bindings", {}).keys()),
                emitted_outputs=emitted_outputs,
            )

    def check_predicates(
        self,
        step: dict[str, Any],
        step_path: str,
        step_map: dict[str, dict[str, Any]],
        emitted_outputs: dict[str, set[str]],
        declared_inputs: set[str],
    ) -> None:
        for field_name in ("predicate", "foreach", "until"):
            expr = step.get(field_name)
            if isinstance(expr, str):
                self.check_cel_expression(
                    expr=expr,
                    location="workflow_predicate",
                    path=f"{step_path} -> {field_name}",
                    step_ids=set(step_map.keys()),
                    available_inputs=declared_inputs,
                    available_bindings=set(step.get("bindings", {}).keys()),
                    emitted_outputs=emitted_outputs,
                )

    def check_cel_expression(
        self,
        expr: str,
        location: str,
        path: str,
        step_ids: set[str] | None,
        available_inputs: set[str] | None,
        available_bindings: set[str] | None,
        emitted_outputs: dict[str, set[str]] | None,
    ) -> None:
        if not isinstance(expr, str) or not expr.strip():
            self.add_issue("error", path, "empty-expression", "CEL expression is empty")
            return

        if expr.count("(") != expr.count(")"):
            self.add_issue("warning", path, "unbalanced-parens", "Expression has unbalanced parentheses")

        roots, _ = extract_cel_roots(expr)
        allowed_roots = ALLOWED_SYMBOLS_BY_LOCATION[location] | CEL_BUILTINS
        invalid_roots = sorted(root for root in roots if root not in allowed_roots)
        for root in invalid_roots:
            self.add_issue(
                "error",
                path,
                "invalid-root-symbol",
                f"Expression uses root symbol {root!r}, which is not valid in {location}",
            )

        for binding_name in find_binding_refs(expr):
            if available_bindings is not None and binding_name not in available_bindings:
                self.add_issue(
                    "error",
                    path,
                    "unknown-binding",
                    f"Expression references binding {binding_name!r}, which is not declared in this context",
                )

        for input_name in find_input_refs(expr):
            if available_inputs is not None and input_name not in available_inputs:
                self.add_issue(
                    "error",
                    path,
                    "unknown-input",
                    f"Expression references input {input_name!r}, which is not declared in this subflow",
                )

        if step_ids is not None and emitted_outputs is not None:
            for step_id, output_name in find_step_refs(expr):
                if step_id not in step_ids:
                    self.add_issue(
                        "error",
                        path,
                        "unknown-step",
                        f"Expression references step {step_id!r}, which is not defined in this workflow",
                    )
                    continue
                if output_name not in emitted_outputs.get(step_id, set()):
                    self.add_issue(
                        "error",
                        path,
                        "unknown-step-output",
                        f"Expression references output {output_name!r} from step {step_id!r}, but that step does not emit it",
                    )

    def check_step_output_path(
        self,
        path_value: str,
        path: str,
        step_map: dict[str, dict[str, Any]],
        emitted_outputs: dict[str, set[str]],
    ) -> None:
        if not path_value.startswith("steps."):
            self.add_issue("error", path, "invalid-step-path", f"Expected a step output path, got {path_value!r}")
            return
        match = re.fullmatch(r"steps\.([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)", path_value)
        if not match:
            self.add_issue("error", path, "invalid-step-path", f"Step output path {path_value!r} is not in steps.<step_id>.<output> form")
            return
        step_id, output_name = match.groups()
        if step_id not in step_map:
            self.add_issue("error", path, "unknown-step", f"Step output path references unknown step {step_id!r}")
            return
        if output_name not in emitted_outputs.get(step_id, set()):
            self.add_issue(
                "error",
                path,
                "unknown-step-output",
                f"Step output path references {output_name!r}, but step {step_id!r} does not emit it",
            )


def collect_schema_errors(schema: dict[str, Any], manifest: dict[str, Any]) -> list[Issue]:
    validator = Draft202012Validator(schema)
    issues: list[Issue] = []
    for error in sorted(validator.iter_errors(manifest), key=lambda item: list(item.absolute_path)):
        issues.append(
            Issue(
                severity="error",
                path=format_path(error.absolute_path),
                code="schema",
                message=error.message,
            )
        )
    return issues


def print_issue(issue: Issue) -> None:
    print(f"[{issue.severity.upper()}] {issue.code} @ {issue.path}")
    print(f"  {issue.message}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifests", nargs="+", help="Path(s) to ANAC JSON manifests")
    parser.add_argument("--schema", default=str(DEFAULT_SCHEMA), help="Path to the ANAC JSON Schema")
    parser.add_argument("--strict", action="store_true", help="Treat warnings as failures")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    schema = load_json(Path(args.schema))
    overall_success = True

    for manifest_arg in args.manifests:
        manifest_path = Path(manifest_arg)
        manifest = load_json(manifest_path)
        print("=" * 72)
        print(f"Linting: {manifest_path}")
        print("=" * 72)

        issues = collect_schema_errors(schema, manifest)
        if not issues:
            issues.extend(ManifestLinter(manifest, manifest_path).lint())

        if not issues:
            print("PASS")
            print()
            continue

        has_error = any(issue.severity == "error" for issue in issues)
        has_warning = any(issue.severity == "warning" for issue in issues)
        for issue in issues:
            print_issue(issue)
        print()

        if has_error or (args.strict and has_warning):
            overall_success = False

    return 0 if overall_success else 1


if __name__ == "__main__":
    sys.exit(main())
