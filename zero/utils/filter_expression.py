"""
Filter expression parser for advanced filtering.

Syntax:
    <column> <operator> <value> [<logic> <column> <operator> <value> ...]

Operators:
    -eq      Equal to
    -ne      Not equal to
    -gt      Greater than
    -lt      Less than
    -ge      Greater than or equal to
    -le      Less than or equal to
    -contain Contains substring
    -notcontain Does not contain substring
    -match   Regex match
    -startswith Starts with
    -endswith Ends with

Logic operators:
    &&       AND
    ||       OR

Logic operators are applied strictly left to right with no precedence and no
short-circuiting, so `a || b && c` means `(a || b) && c`, not `a || (b && c)`.
Parentheses are not supported.

Examples:
    imagename -contain "csrss.exe" && pid -eq 443
    username -eq "admin" || username -eq "root"
    memory -gt 1000 && memory -lt 5000
"""

import re
from typing import List, Dict, Any, Optional, Tuple, Sequence
from dataclasses import dataclass
from enum import Enum


class Operator(Enum):
    """Filter operators."""
    EQ = "eq"
    NE = "ne"
    GT = "gt"
    LT = "lt"
    GE = "ge"
    LE = "le"
    CONTAIN = "contain"
    NOT_CONTAIN = "notcontain"
    MATCH = "match"
    STARTS_WITH = "startswith"
    ENDS_WITH = "endswith"


class LogicOperator(Enum):
    """Logical operators."""
    AND = "&&"
    OR = "||"


@dataclass
class FilterCondition:
    """Represents a single filter condition."""
    column: str
    operator: Operator
    value: Any
    value_type: str = "string"  # "string", "number", "regex"


@dataclass
class FilterExpression:
    """Represents a complete filter expression."""
    conditions: List[FilterCondition]
    logic_operators: List[LogicOperator]


class FilterParser:
    """Parser for filter expressions."""

    # Token patterns (compiled once at class definition time)
    _COMPILED_PATTERNS = {
        'LOGIC': re.compile(r'&&|\|\|'),
        'OPERATOR': re.compile(r'-(?:eq|ne|gt|lt|ge|le|contain|notcontain|match|startswith|endswith)'),
        'QUOTED_STRING': re.compile(r'"(?:\\.|[^"\\])*"'),
        'IDENTIFIER': re.compile(r'[a-zA-Z_][a-zA-Z0-9_]*'),
        'BAREWORD': re.compile(r'[^\s&|]+'),
        'WHITESPACE': re.compile(r'\s+'),
    }

    def __init__(self, available_columns: Sequence[str] = ()):
        self.available_columns = list(available_columns)
        self.patterns = self._COMPILED_PATTERNS

    @staticmethod
    def _unescape_quoted_string(token: str) -> str:
        """Decode the contents of a double-quoted token."""
        inner = token[1:-1]
        return re.sub(r'\\(.)', r'\1', inner)

    @staticmethod
    def _coerce_unquoted_value(token: str) -> Tuple[Any, str]:
        """Interpret a bare token as a number when it is purely numeric."""
        if re.fullmatch(r'-?\d+(?:\.\d+)?', token):
            if '.' in token:
                return float(token), "number"
            return int(token), "number"
        return token, "string"

    def tokenize(self, expression: str) -> List[Tuple[str, str]]:
        """Tokenize the filter expression."""
        tokens = []
        pos = 0
        
        while pos < len(expression):
            match = None
            
            for token_type, pattern in self.patterns.items():
                m = pattern.match(expression, pos)
                if m:
                    if token_type != 'WHITESPACE':  # Skip whitespace
                        tokens.append((token_type, m.group()))
                    pos = m.end()
                    match = True
                    break
            
            if not match:
                raise ValueError(f"Unexpected character '{expression[pos]}' at position {pos}")
        
        return tokens

    def parse(self, expression: str) -> Optional[FilterExpression]:
        """Parse a filter expression string."""
        if not expression or not expression.strip():
            return None

        # Check whether this looks like advanced syntax. We intentionally
        # treat unknown `-xxx` operators as advanced attempts so typos surface
        # as parser errors instead of silently falling back to simple filtering.
        has_advanced_hint = bool(re.search(r'\s-[a-zA-Z_]+\b', expression))
        if not has_advanced_hint and '&&' not in expression and '||' not in expression:
            # Simple text filter - return None to use legacy filtering
            return None

        try:
            tokens = self.tokenize(expression)
            if not tokens:
                return None

            conditions = []
            logic_operators = []
            i = 0

            while i < len(tokens):
                # Expect column name: identifier or quoted string.
                if tokens[i][0] not in {'IDENTIFIER', 'QUOTED_STRING'}:
                    raise ValueError(f"Expected column name at position {i}, got {tokens[i][1]}")

                if tokens[i][0] == 'QUOTED_STRING':
                    column = self._unescape_quoted_string(tokens[i][1])
                else:
                    column = tokens[i][1]
                
                # Validate column name if available columns are provided
                if self.available_columns:
                    column_lower = column.lower()
                    matched_column = None
                    for col in self.available_columns:
                        if col.lower() == column_lower:
                            matched_column = col
                            break
                    if not matched_column:
                        raise ValueError(f"Unknown column: {column}")
                    column = matched_column
                
                i += 1

                # Expect operator
                if i >= len(tokens) or tokens[i][0] != 'OPERATOR':
                    raise ValueError(f"Expected operator after column '{column}'")
                
                op_str = tokens[i][1][1:]  # Remove '-' prefix
                operator = Operator(op_str)
                i += 1

                # Expect value
                if i >= len(tokens):
                    raise ValueError(f"Expected value after operator '{operator.value}'")

                value_token = tokens[i]
                if value_token[0] == 'QUOTED_STRING':
                    value = self._unescape_quoted_string(value_token[1])
                    value_type = "string"
                elif value_token[0] in {'IDENTIFIER', 'BAREWORD'}:
                    # Unquoted strings may include dates, hex values, paths, etc.
                    value, value_type = self._coerce_unquoted_value(value_token[1])
                else:
                    raise ValueError(f"Invalid value: {value_token[1]}")

                conditions.append(FilterCondition(column, operator, value, value_type))
                i += 1

                # Check for logic operator
                if i < len(tokens) and tokens[i][0] == 'LOGIC':
                    logic_op = LogicOperator(tokens[i][1])
                    logic_operators.append(logic_op)
                    i += 1

            if len(logic_operators) != len(conditions) - 1:
                raise ValueError("Expected condition after logical operator")

            return FilterExpression(conditions, logic_operators)

        except Exception as e:
            raise ValueError(f"Failed to parse filter expression: {str(e)}")


class _CompiledCondition:
    """A condition with everything row-independent resolved up front.

    Evaluating a filter over a large result set is the hot path: without this,
    every row re-did the column lookup, the ``str(...).lower()`` of the condition
    value, and the regex cache probe.
    """

    __slots__ = ("col_idx", "operator", "is_number", "number_value", "text_value", "regex")

    def __init__(self, condition: FilterCondition, column_indexes: Dict[str, int]):
        self.col_idx: Optional[int] = column_indexes.get(condition.column.lower())
        self.operator = condition.operator
        self.is_number = condition.value_type == "number"
        self.number_value = condition.value if self.is_number else None
        # Doubles as the lowered comparison value and the substring needle: in the
        # string case the original code derived both from str(value).lower().
        self.text_value = str(condition.value).lower()
        self.regex: Optional["re.Pattern"] = None
        if condition.operator == Operator.MATCH:
            try:
                self.regex = re.compile(str(condition.value), re.IGNORECASE)
            except re.error:
                self.regex = None


class FilterEvaluator:
    """Evaluates filter expressions against data rows."""

    def __init__(self, columns: List[str]):
        self.columns = columns
        self._column_indexes = {col.lower(): i for i, col in enumerate(columns)}
        # Memo of the most recent compilation. The expression is kept alive by
        # this reference, so identity comparison cannot be fooled by id() reuse.
        self._last_expression: Optional[FilterExpression] = None
        self._last_compiled: List["_CompiledCondition"] = []

    def _get_column_index(self, column: str) -> Optional[int]:
        """Get column index by name (case-insensitive)."""
        return self._column_indexes.get(column.lower())

    def compile(self, expression: FilterExpression) -> List["_CompiledCondition"]:
        """Resolve an expression's conditions once, memoized on the last one."""
        if expression is self._last_expression:
            return self._last_compiled
        compiled = [
            _CompiledCondition(cond, self._column_indexes)
            for cond in expression.conditions
        ]
        self._last_expression = expression
        self._last_compiled = compiled
        return compiled

    @staticmethod
    def _evaluate_compiled(row: tuple, cond: _CompiledCondition) -> bool:
        """Evaluate a pre-compiled condition against one row."""
        idx = cond.col_idx
        if idx is None or idx >= len(row):
            return False

        cell = row[idx]
        cell_str = str(cell).lower() if cell is not None else ""
        op = cond.operator

        if cond.is_number:
            try:
                cell_num = float(cell)
            except (ValueError, TypeError):
                # Non-numeric cell never satisfies a numeric condition.
                return False
            expected = cond.number_value
            if op == Operator.EQ:
                return cell_num == expected
            if op == Operator.NE:
                return cell_num != expected
            if op == Operator.GT:
                return cell_num > expected
            if op == Operator.LT:
                return cell_num < expected
            if op == Operator.GE:
                return cell_num >= expected
            if op == Operator.LE:
                return cell_num <= expected
        else:
            expected_text = cond.text_value
            if op == Operator.EQ:
                return cell_str == expected_text
            if op == Operator.NE:
                return cell_str != expected_text
            if op == Operator.GT:
                return cell_str > expected_text
            if op == Operator.LT:
                return cell_str < expected_text
            if op == Operator.GE:
                return cell_str >= expected_text
            if op == Operator.LE:
                return cell_str <= expected_text

        # Text operators apply to the cell's string form regardless of value type.
        needle = cond.text_value
        if op == Operator.CONTAIN:
            return needle in cell_str
        if op == Operator.NOT_CONTAIN:
            return needle not in cell_str
        if op == Operator.MATCH:
            return bool(cond.regex.search(cell_str)) if cond.regex else False
        if op == Operator.STARTS_WITH:
            return cell_str.startswith(needle)
        if op == Operator.ENDS_WITH:
            return cell_str.endswith(needle)

        return False

    def _evaluate_condition(self, row: tuple, condition: FilterCondition) -> bool:
        """Evaluate a single, uncompiled condition (convenience / compatibility)."""
        return self._evaluate_compiled(
            row, _CompiledCondition(condition, self._column_indexes)
        )

    def evaluate(self, row: tuple, expression: FilterExpression) -> bool:
        """Evaluate a complete filter expression against a row.

        Logic operators are applied left to right with no precedence, so
        ``a || b && c`` means ``(a || b) && c``.
        """
        return self.evaluate_compiled(
            row, self.compile(expression), expression.logic_operators
        )

    @classmethod
    def evaluate_compiled(
        cls,
        row: tuple,
        conditions: List[_CompiledCondition],
        logic_operators: List[LogicOperator],
    ) -> bool:
        if not conditions:
            return True

        result = cls._evaluate_compiled(row, conditions[0])

        for i, logic_op in enumerate(logic_operators):
            if i + 1 >= len(conditions):
                break

            next_result = cls._evaluate_compiled(row, conditions[i + 1])

            if logic_op == LogicOperator.AND:
                result = result and next_result
            elif logic_op == LogicOperator.OR:
                result = result or next_result

        return result


class AdvancedFilter:
    """High-level interface for advanced filtering."""

    def __init__(self, columns: List[str]):
        self.columns = columns
        self.parser = FilterParser(columns)
        self.evaluator = FilterEvaluator(columns)
        self._expression: Optional[FilterExpression] = None
        self._error: Optional[str] = None

    def set_expression(self, expression_str: str) -> bool:
        """Set and parse a filter expression.

        Returns True only when an advanced expression was parsed successfully.
        Returns False for empty/simple text (caller should use substring match)
        or on parse errors.
        """
        try:
            self._expression = self.parser.parse(expression_str)
            self._error = None
            # parse() returns None for simple substring filters (no operators).
            return self._expression is not None
        except ValueError as e:
            self._expression = None
            self._error = str(e)
            return False

    def get_error(self) -> Optional[str]:
        """Get the last parsing error."""
        return self._error

    def has_expression(self) -> bool:
        """Check if a valid expression is set."""
        return self._expression is not None

    def clear(self):
        """Clear the current expression."""
        self._expression = None
        self._error = None

    def filter_rows(self, rows: List[tuple]) -> List[tuple]:
        """Filter rows using the current expression."""
        if not self._expression:
            return rows

        # Compile once, then only per-row comparisons remain in the loop.
        conditions = self.evaluator.compile(self._expression)
        logic_operators = self._expression.logic_operators
        evaluate = self.evaluator.evaluate_compiled
        return [row for row in rows if evaluate(row, conditions, logic_operators)]

    def get_help(self) -> str:
        """Get help text for filter syntax."""
        return """
Advanced Filter Syntax:
  <column> <operator> <value> [<logic> <column> <operator> <value> ...]

Operators:
  -eq          Equal to
  -ne          Not equal to
  -gt          Greater than
  -lt          Less than
  -ge          Greater than or equal to
  -le          Less than or equal to
  -contain     Contains substring
  -notcontain  Does not contain substring
  -match       Regex match
  -startswith  Starts with
  -endswith    Ends with

Logic operators:
  &&           AND
  ||           OR

Examples:
  imagename -contain "csrss.exe" && pid -eq 443
  username -eq "admin" || username -eq "root"
  memory -gt 1000 && memory -lt 5000
  name -startswith "test" && status -ne "deleted"

Note: Simple text (without operators) will use basic substring matching.
"""
