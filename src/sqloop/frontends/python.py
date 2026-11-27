"""Python 用フロントエンド。標準ライブラリの ast で解析する。

対応パターン（MVP）:
  - [r for r in t if 条件]               → SELECT * ... WHERE
  - [r["a"] for r in t]                   → SELECT a
  - sum / max / min (r["x"] for r in t)   → SUM / MAX / MIN
  - len([... for r in t if ...])          → COUNT(*)
  - sorted(..., key=lambda r: r["x"], reverse=True)[:n] → ORDER BY ... LIMIT
  - for r in t: if 条件: out.append(式)    → SELECT 式 ... WHERE
  - for r in t: total += r["x"] / cnt += 1 → SUM / COUNT(*)
  - for r in t: d[k] = d.get(k, 0) + v    → GROUP BY
"""

from __future__ import annotations

import ast
import re

from ..base import Frontend, Snippet
from ..errors import SourceParseError, UnsupportedPattern
from ..ir import Select

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

_CMP_OPS = {
    ast.Eq: "=", ast.NotEq: "<>", ast.Lt: "<", ast.LtE: "<=", ast.Gt: ">", ast.GtE: ">=",
}
_BIN_OPS = {ast.Add: "+", ast.Sub: "-", ast.Mult: "*", ast.Div: "/", ast.Mod: "%"}
_AGG_FUNCS = {"sum": "SUM", "max": "MAX", "min": "MIN"}


def _ident(name: str) -> str:
    """識別子をそのまま、使えない文字を含むなら二重引用符で囲む。"""
    if _IDENT.match(name):
        return name
    return '"' + name.replace('"', '""') + '"'


class _Expr:
    """Python の式を SQL の式文字列に変換する。row_var は行を表す変数名。"""

    def __init__(self, row_var: str):
        self.row_var = row_var
        self.warnings: list[str] = []

    def render(self, node: ast.AST) -> str:
        # 列の参照: r["col"] / r.col
        if isinstance(node, ast.Subscript) and self._is_row(node.value):
            key = node.slice
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                return _ident(key.value)
            raise UnsupportedPattern("列名は文字列の定数で指定してください（例: r[\"age\"]）", node.lineno)
        if isinstance(node, ast.Attribute) and self._is_row(node.value):
            return _ident(node.attr)

        if isinstance(node, ast.Constant):
            return self._literal(node)

        if isinstance(node, ast.Compare):
            return self._compare(node)

        if isinstance(node, ast.BoolOp):
            op = " AND " if isinstance(node.op, ast.And) else " OR "
            return "(" + op.join(self.render(v) for v in node.values) + ")"

        if isinstance(node, ast.UnaryOp):
            if isinstance(node.op, ast.Not):
                return f"NOT ({self.render(node.operand)})"
            if isinstance(node.op, ast.USub):
                return f"-{self.render(node.operand)}"

        if isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS:
            if isinstance(node.op, ast.Div):
                self.warnings.append(
                    f"{node.lineno}行目: Python の / は小数の割り算ですが、DBによっては整数同士だと切り捨てになります"
                )
            return f"({self.render(node.left)} {_BIN_OPS[type(node.op)]} {self.render(node.right)})"

        if isinstance(node, ast.Name) and node.id == self.row_var:
            raise UnsupportedPattern("行全体をこの位置では使えません", getattr(node, "lineno", None))

        raise UnsupportedPattern(
            f"未対応の式です: {ast.unparse(node)}", getattr(node, "lineno", None)
        )

    def _is_row(self, node: ast.AST) -> bool:
        return isinstance(node, ast.Name) and node.id == self.row_var

    def _literal(self, node: ast.Constant) -> str:
        v = node.value
        if v is None:
            return "NULL"
        if isinstance(v, bool):  # bool は int より先に判定する
            return "TRUE" if v else "FALSE"
        if isinstance(v, (int, float)):
            return repr(v)
        if isinstance(v, str):
            return "'" + v.replace("'", "''") + "'"  # シングルクォートをエスケープ
        raise UnsupportedPattern(f"未対応の定数です: {v!r}", node.lineno)

    def _compare(self, node: ast.Compare) -> str:
        parts: list[str] = []
        left = node.left
        # a < b < c のような連続比較は AND に分解する
        for op, right in zip(node.ops, node.comparators):
            parts.append(self._compare_one(left, op, right))
            left = right
        return parts[0] if len(parts) == 1 else "(" + " AND ".join(parts) + ")"

    def _compare_one(self, left: ast.AST, op: ast.cmpop, right: ast.AST) -> str:
        if isinstance(op, (ast.Is, ast.IsNot)):
            if isinstance(right, ast.Constant) and right.value is None:
                not_ = " NOT" if isinstance(op, ast.IsNot) else ""
                return f"{self.render(left)} IS{not_} NULL"
            raise UnsupportedPattern("is は None との比較だけに対応しています", left.lineno)
        if isinstance(op, (ast.In, ast.NotIn)):
            if isinstance(right, (ast.List, ast.Tuple, ast.Set)) and right.elts:
                items = ", ".join(self.render(e) for e in right.elts)
                not_ = " NOT" if isinstance(op, ast.NotIn) else ""
                if not_:
                    self.warnings.append(
                        f"{left.lineno}行目: NOT IN は、列の値が NULL の行は結果に含まれません"
                    )
                return f"{self.render(left)}{not_} IN ({items})"
            raise UnsupportedPattern("in は定数のリストとの比較だけに対応しています", left.lineno)
        if type(op) in _CMP_OPS:
            # == None は SQL では常に不明になるので IS NULL を勧める
            if isinstance(right, ast.Constant) and right.value is None:
                raise UnsupportedPattern("None との比較は is None を使ってください", left.lineno)
            return f"{self.render(left)} {_CMP_OPS[type(op)]} {self.render(right)}"
        raise UnsupportedPattern(f"未対応の比較演算子です: {type(op).__name__}", left.lineno)


class PythonFrontend(Frontend):
    name = "python"
    extensions = (".py",)

    # ---------- 候補探し ----------

    def find_snippets(self, source: str) -> list[Snippet]:
        try:
            tree = ast.parse(source)
        except SyntaxError as e:
            raise SourceParseError(f"Python の構文エラー（{e.lineno}行目）: {e.msg}") from e

        snippets: list[Snippet] = []
        for stmt in self._walk_stmts(tree.body):
            if self._is_candidate(stmt):
                text = ast.get_source_segment(source, stmt) or ast.unparse(stmt)
                snippets.append(Snippet(lang=self.name, source=text, lineno=stmt.lineno, node=stmt))
        return snippets

    def _walk_stmts(self, body: list[ast.stmt]):
        """文を順にたどる。候補になった for 文の中にはもぐらない。"""
        for stmt in body:
            yield stmt
            if isinstance(stmt, ast.For) and self._is_candidate(stmt):
                continue
            for attr in ("body", "orelse", "finalbody"):
                yield from self._walk_stmts(getattr(stmt, attr, []) or [])
            for handler in getattr(stmt, "handlers", []) or []:
                yield from self._walk_stmts(handler.body)

    def _is_candidate(self, stmt: ast.stmt) -> bool:
        # for 行 in テーブル名:
        if isinstance(stmt, ast.For):
            return isinstance(stmt.iter, ast.Name) and isinstance(stmt.target, ast.Name)
        # 代入の右辺に内包表記か sorted がある
        if isinstance(stmt, (ast.Assign, ast.AnnAssign)) and stmt.value is not None:
            for n in ast.walk(stmt.value):
                if isinstance(n, (ast.ListComp, ast.GeneratorExp)):
                    return True
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "sorted":
                    return True
        return False

    # ---------- ルール変換 ----------

    def to_ir(self, snippet: Snippet) -> tuple[Select, list[str]]:
        node = snippet.node
        if isinstance(node, ast.For):
            return self._from_for(node)
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            return self._from_value(node.value)
        raise UnsupportedPattern("未対応の文です", snippet.lineno)

    def _from_value(self, v: ast.AST) -> tuple[Select, list[str]]:
        # 内包表記
        if isinstance(v, (ast.ListComp, ast.GeneratorExp)):
            return self._from_comp(v)

        # sorted(...)[:n]
        if isinstance(v, ast.Subscript) and isinstance(v.slice, ast.Slice):
            sl = v.slice
            if sl.lower is None and sl.step is None and isinstance(sl.upper, ast.Constant) \
                    and isinstance(sl.upper.value, int) and not isinstance(sl.upper.value, bool):
                sel, warns = self._from_value(v.value)
                if not sel.order_by:
                    warns.append(f"{v.lineno}行目: 並べ替えなしの LIMIT は、DBによって返る行が変わります")
                sel.limit = sl.upper.value
                return sel, warns
            raise UnsupportedPattern("スライスは [:n]（n は整数の定数）だけに対応しています", v.lineno)

        if isinstance(v, ast.Call) and isinstance(v.func, ast.Name):
            fname = v.func.id
            # sum / max / min (ジェネレータ)
            if fname in _AGG_FUNCS and len(v.args) == 1 and not v.keywords:
                sel, warns = self._from_comp(self._need_comp(v.args[0], fname))
                if sel.columns == ["*"]:
                    raise UnsupportedPattern(f"{fname}() には列を指定してください", v.lineno)
                sel.columns = [f"{_AGG_FUNCS[fname]}({sel.columns[0]})"]
                return sel, warns
            # len([...])
            if fname == "len" and len(v.args) == 1 and not v.keywords:
                sel, warns = self._from_comp(self._need_comp(v.args[0], "len"))
                sel.columns = ["COUNT(*)"]
                return sel, warns
            # sorted(テーブル or 内包表記, key=lambda r: ..., reverse=...)
            if fname == "sorted" and len(v.args) == 1:
                return self._from_sorted(v)

        raise UnsupportedPattern(f"未対応の書き方です: {ast.unparse(v)}", getattr(v, "lineno", None))

    def _need_comp(self, node: ast.AST, fname: str) -> ast.AST:
        if isinstance(node, (ast.ListComp, ast.GeneratorExp)):
            return node
        raise UnsupportedPattern(f"{fname}() の中は内包表記にしてください", getattr(node, "lineno", None))

    def _from_comp(self, comp: ast.ListComp | ast.GeneratorExp) -> tuple[Select, list[str]]:
        if len(comp.generators) != 1:
            raise UnsupportedPattern("for が2つ以上ある内包表記（JOIN 相当）はまだ未対応です", comp.lineno)
        gen = comp.generators[0]
        if not isinstance(gen.target, ast.Name) or not isinstance(gen.iter, ast.Name):
            raise UnsupportedPattern("for 変数 in テーブル名 の形にしてください", comp.lineno)
        if gen.is_async:
            raise UnsupportedPattern("async for は未対応です", comp.lineno)

        ex = _Expr(gen.target.id)
        sel = Select(table=_ident(gen.iter.id))
        sel.columns = self._columns(comp.elt, ex)
        if gen.ifs:
            conds = [ex.render(c) for c in gen.ifs]
            sel.where = conds[0] if len(conds) == 1 else " AND ".join(conds)
        return sel, ex.warnings

    def _columns(self, elt: ast.AST, ex: _Expr) -> list[str]:
        if isinstance(elt, ast.Name) and elt.id == ex.row_var:
            return ["*"]
        if isinstance(elt, ast.Tuple):
            return [ex.render(e) for e in elt.elts]
        return [ex.render(elt)]

    def _from_sorted(self, call: ast.Call) -> tuple[Select, list[str]]:
        src = call.args[0]
        if isinstance(src, ast.Name):
            sel, warns = Select(table=_ident(src.id)), []
        else:
            sel, warns = self._from_comp(self._need_comp(src, "sorted"))

        key_node, reverse = None, False
        for kw in call.keywords:
            if kw.arg == "key":
                key_node = kw.value
            elif kw.arg == "reverse":
                if not (isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, bool)):
                    raise UnsupportedPattern("reverse は True / False の定数にしてください", call.lineno)
                reverse = kw.value.value
            else:
                raise UnsupportedPattern(f"sorted の引数 {kw.arg} は未対応です", call.lineno)

        if not isinstance(key_node, ast.Lambda) or len(key_node.args.args) != 1:
            raise UnsupportedPattern("sorted には key=lambda r: r[\"列\"] を指定してください", call.lineno)
        kex = _Expr(key_node.args.args[0].arg)
        body = key_node.body
        keys = body.elts if isinstance(body, ast.Tuple) else [body]
        sel.order_by = [(kex.render(k), reverse) for k in keys]
        return sel, warns + kex.warnings

    def _from_for(self, loop: ast.For) -> tuple[Select, list[str]]:
        if loop.orelse:
            raise UnsupportedPattern("for ... else は未対応です", loop.lineno)
        ex = _Expr(loop.target.id)
        sel = Select(table=_ident(loop.iter.id))

        # for の中身: [if 条件:] 文1つ
        body = loop.body
        conds: list[str] = []
        while len(body) == 1 and isinstance(body[0], ast.If):
            if body[0].orelse:
                raise UnsupportedPattern("if ... else を含むループは未対応です", body[0].lineno)
            conds.append(ex.render(body[0].test))
            body = body[0].body
        if len(body) != 1:
            raise UnsupportedPattern("ループの中の処理は1文だけに対応しています", loop.lineno)
        if conds:
            sel.where = conds[0] if len(conds) == 1 else " AND ".join(conds)

        stmt = body[0]

        # out.append(式)
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
            c = stmt.value
            if isinstance(c.func, ast.Attribute) and c.func.attr == "append" and len(c.args) == 1:
                sel.columns = self._columns(c.args[0], ex)
                return sel, ex.warnings

        # total += 式 / count += 1
        if isinstance(stmt, ast.AugAssign) and isinstance(stmt.op, ast.Add):
            if isinstance(stmt.target, ast.Name):
                sel.columns = [self._sum_or_count(stmt.value, ex)]
                return sel, ex.warnings
            # d[キー] += 値 （defaultdict 前提）
            if isinstance(stmt.target, ast.Subscript):
                key = ex.render(stmt.target.slice)
                sel.columns = [key, self._sum_or_count(stmt.value, ex)]
                sel.group_by = [key]
                ex.warnings.append(f"{stmt.lineno}行目: d[キー] += 値 は defaultdict(int) を使っている前提で変換しました")
                return sel, ex.warnings

        # total = total + 式
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
            tgt, val = stmt.targets[0], stmt.value
            if isinstance(tgt, ast.Name) and isinstance(val, ast.BinOp) and isinstance(val.op, ast.Add) \
                    and isinstance(val.left, ast.Name) and val.left.id == tgt.id:
                sel.columns = [self._sum_or_count(val.right, ex)]
                return sel, ex.warnings
            # d[k] = d.get(k, 0) + 値
            if isinstance(tgt, ast.Subscript) and isinstance(tgt.value, ast.Name):
                grouped = self._dict_get_add(tgt, val, ex)
                if grouped:
                    key, agg = grouped
                    sel.columns, sel.group_by = [key, agg], [key]
                    return sel, ex.warnings

        raise UnsupportedPattern(f"未対応のループ処理です: {ast.unparse(stmt)}", stmt.lineno)

    def _sum_or_count(self, value: ast.AST, ex: _Expr) -> str:
        if isinstance(value, ast.Constant) and value.value == 1 and not isinstance(value.value, bool):
            return "COUNT(*)"
        return f"SUM({ex.render(value)})"

    def _dict_get_add(self, tgt: ast.Subscript, val: ast.AST, ex: _Expr):
        """d[k] = d.get(k, 0) + 値 の形なら (キー式, 集計式) を返す。"""
        dname = tgt.value.id
        if not (isinstance(val, ast.BinOp) and isinstance(val.op, ast.Add)):
            return None
        call = val.left
        if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                and call.func.attr == "get" and isinstance(call.func.value, ast.Name)
                and call.func.value.id == dname and len(call.args) == 2):
            return None
        if ast.dump(call.args[0]) != ast.dump(tgt.slice):
            raise UnsupportedPattern("d[k] と d.get(k, 0) のキーが一致していません", tgt.lineno)
        if not (isinstance(call.args[1], ast.Constant) and call.args[1].value == 0):
            raise UnsupportedPattern("d.get(k, 0) の初期値は 0 だけに対応しています", tgt.lineno)
        key = ex.render(tgt.slice)
        return key, self._sum_or_count(val.right, ex)
