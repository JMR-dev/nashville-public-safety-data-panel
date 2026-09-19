"""Static depth and cost limits for GraphQL documents, checked before execution.

Depth counts nesting from zero at the operation's top-level fields. Every field costs one, and a
field's children cost as many times as the list it returns may hold: the page size argument for
paginated fields and a fixed estimate for other lists. A size given by a variable uses the value
sent with the request, or the largest allowed size when none was sent.

Validation rules also run on invalid documents, so unknown types and fragments and fragment
cycles are skipped here and reported by GraphQL's standard rules.
"""

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any, cast

from graphql import (
    FieldNode,
    FragmentDefinitionNode,
    FragmentSpreadNode,
    GraphQLError,
    GraphQLObjectType,
    GraphQLSchema,
    InlineFragmentNode,
    IntValueNode,
    NamedTypeNode,
    OperationDefinitionNode,
    SelectionSetNode,
    ValidationRule,
    VariableNode,
)
from strawberry.extensions import SchemaExtension

UNSIZED_LIST = 100


@dataclass(frozen=True)
class Size:
    argument: str
    default: int
    worst: int


@dataclass(frozen=True)
class Limits:
    max_depth: int
    max_cost: int
    sizes: Mapping[tuple[str, str], Size]
    """(type, field) pairs whose children are sized by an argument such as ``first``."""
    sized_children: frozenset[tuple[str, str]]
    """Lists inside sized fields; their length is already counted by the parent's argument."""


class _Walk:
    def __init__(
        self,
        schema: GraphQLSchema,
        fragments: Mapping[str, FragmentDefinitionNode],
        limits: Limits,
        variables: Mapping[str, Any],
    ) -> None:
        self.schema = schema
        self.fragments = fragments
        self.limits = limits
        self.variables = variables
        self.deepest = 0

    def cost(
        self,
        selection_set: SelectionSetNode,
        parent: GraphQLObjectType,
        level: int,
        spread: frozenset[str],
    ) -> int:
        total = 0
        for selection in selection_set.selections:
            if isinstance(selection, FieldNode):
                total += self.field(selection, parent, level, spread)
            elif isinstance(selection, InlineFragmentNode):
                # graphql-core annotates this as required, but "... { field }" parses to None.
                condition = cast(NamedTypeNode | None, selection.type_condition)
                target = parent if condition is None else self.object(condition.name.value)
                if target is not None:
                    total += self.cost(selection.selection_set, target, level, spread)
            else:
                name = cast(FragmentSpreadNode, selection).name.value
                fragment = self.fragments.get(name)
                if fragment is None or name in spread:
                    continue
                spread = spread | {name}
                target = self.object(fragment.type_condition.name.value)
                if target is not None:
                    total += self.cost(fragment.selection_set, target, level, spread)
        return total

    def object(self, name: str) -> GraphQLObjectType | None:
        found = self.schema.get_type(name)
        return found if isinstance(found, GraphQLObjectType) else None

    def field(
        self, node: FieldNode, parent: GraphQLObjectType, level: int, spread: frozenset[str]
    ) -> int:
        name = node.name.value
        field = parent.fields.get(name)
        if name.startswith("__") or field is None:
            return 0
        self.deepest = max(self.deepest, level)
        # Printed types such as "[Call!]!" name the element type and show list wrapping.
        printed = str(field.type)
        child = self.object(printed.strip("[]!"))
        if node.selection_set is None or child is None:
            return 1
        multiplier = self.multiplier(parent.name, node, printed.startswith("["))
        return 1 + multiplier * self.cost(node.selection_set, child, level + 1, spread)

    def size(self, value: object, worst: int) -> int:
        if isinstance(value, IntValueNode):
            return max(int(value.value), 0)
        if isinstance(value, VariableNode):
            supplied = self.variables.get(value.name.value)
            if type(supplied) is int:
                return max(supplied, 0)
        return worst

    def multiplier(self, parent: str, node: FieldNode, is_list: bool) -> int:
        key = (parent, node.name.value)
        size = self.limits.sizes.get(key)
        if size is not None:
            for argument in node.arguments:
                if argument.name.value == size.argument:
                    return self.size(argument.value, size.worst)
            return size.default
        if is_list and key not in self.limits.sized_children:
            return UNSIZED_LIST
        return 1


def limits_rule(limits: Limits, variables: Mapping[str, Any]) -> type[ValidationRule]:
    class LimitsRule(ValidationRule):
        def enter_operation_definition(self, node: OperationDefinitionNode, *_args: object) -> None:
            root = self.context.schema.get_root_type(node.operation)
            if root is None:
                return
            fragments = {
                definition.name.value: definition
                for definition in self.context.document.definitions
                if isinstance(definition, FragmentDefinitionNode)
            }
            walk = _Walk(self.context.schema, fragments, limits, variables)
            cost = walk.cost(node.selection_set, root, 0, frozenset())
            if walk.deepest > limits.max_depth:
                message = f"Query depth {walk.deepest} exceeds the maximum of {limits.max_depth}"
                self.report_error(GraphQLError(message, node))
            if cost > limits.max_cost:
                message = f"Query cost {cost} exceeds the maximum of {limits.max_cost}"
                self.report_error(GraphQLError(message, node))

    return LimitsRule


class DocumentLimits(SchemaExtension):
    """Adds the limits rule to each operation's validation, with that request's variables."""

    def __init__(self, limits: Limits) -> None:
        self.limits = limits

    def on_operation(self) -> Iterator[None]:
        context = self.execution_context
        rule = limits_rule(self.limits, context.variables or {})
        context.validation_rules = (*context.validation_rules, rule)
        yield
