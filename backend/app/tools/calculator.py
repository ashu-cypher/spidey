import ast
import re

from app.tools.base import BaseTool, ToolError

_ALLOWED_NODES = (
    ast.Expression,
    ast.BinOp,
    ast.UnaryOp,
    ast.Constant,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.Pow,
    ast.Mod,
    ast.UAdd,
    ast.USub,
)

_CANDIDATE_RE = re.compile(r"[\d\(][\d\s\.\+\-\*\/\%\^\(\)]*")

_WORD_OPS = [
    ("divided by", "/"),
    ("multiply", "*"),
    ("plus", "+"),
    ("minus", "-"),
    ("times", "*"),
]

_COMMAND_WORDS = [
    "spidey",
    "please",
    "compute",
    "calculate",
    "what's",
    "what is",
    "?",
]


class CalculatorTool(BaseTool):
    name = "calculator"
    description = "Safely evaluates basic arithmetic expressions."
    input_schema = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    }
    output_schema = {"expression": "str", "result": "int|float"}

    def _normalize(self, text: str) -> str:
        s = text.lower()
        # Unicode operator signs users actually type (× ÷) -> ASCII.
        s = s.replace("×", "*").replace("÷", "/")
        for word, op in _WORD_OPS:
            s = re.sub(rf"\b{re.escape(word)}\b", op, s)
        for word in _COMMAND_WORDS:
            s = re.sub(rf"\b{re.escape(word)}\b", " ", s)
        s = s.replace("divided", "/")
        return s

    def _extract(self, s: str) -> str:
        best = ""
        for m in _CANDIDATE_RE.finditer(s):
            cand = m.group(0).strip()
            if not re.search(r"\d", cand):
                continue
            if not re.search(r"[\+\-\*\/\%\^]", cand):
                continue
            if len(cand) > len(best):
                best = cand
        if not best:
            raise ToolError("No arithmetic expression found.")
        expr = best.replace("^", "**").rstrip(".")
        if expr.count("(") != expr.count(")"):
            raise ToolError(
                "That doesn't look like a calculation I can safely run."
            )
        return expr

    def _validate(self, tree: ast.Expression) -> None:
        for node in ast.walk(tree):
            if not isinstance(node, _ALLOWED_NODES):
                raise ToolError(
                    "That doesn't look like a calculation I can safely run."
                )
            if isinstance(node, ast.Constant) and not isinstance(
                node.value, (int, float)
            ):
                raise ToolError(
                    "That doesn't look like a calculation I can safely run."
                )

    async def _run(self, **kwargs) -> dict:
        text = kwargs.get("text", "")
        expr = self._extract(self._normalize(text))
        try:
            tree = ast.parse(expr, mode="eval")
        except SyntaxError:
            raise ToolError(
                "That doesn't look like a calculation I can safely run."
            )
        self._validate(tree)
        try:
            value = eval(compile(tree, "<calc>", "eval"), {"__builtins__": {}}, {})
        except Exception:
            raise ToolError("That doesn't look like a calculation I can safely run.")
        if isinstance(value, float):
            value = round(value, 10)
        return {"expression": expr, "result": value}
