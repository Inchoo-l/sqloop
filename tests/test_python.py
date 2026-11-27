"""Python フロントエンドのテスト。

変換結果の文字列だけでなく、SQLite で実際に実行して
元の Python コードと結果が一致するかも確かめる。
"""

import sqlite3

import pytest

from sqloop import convert_source
from sqloop.base import Converter, ConversionResult
from sqloop.cli import main
from sqloop.errors import SourceParseError, UnsupportedPattern

USERS = [
    {"name": "Aki", "age": 25, "city": "Tokyo", "score": 80},
    {"name": "Ben", "age": 17, "city": "Osaka", "score": 65},
    {"name": "Cai", "age": 32, "city": "Tokyo", "score": 92},
    {"name": "Dan", "age": 20, "city": "Nagoya", "score": None},
]


def sql_of(code: str) -> str:
    results = convert_source(code)
    assert len(results) == 1, results
    assert results[0].ok, results[0].reason
    return results[0].sql


def run_sql(sql: str):
    """USERS を SQLite に入れて SQL を実行する。"""
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE users (name TEXT, age INT, city TEXT, score INT)")
    con.executemany("INSERT INTO users VALUES (:name, :age, :city, :score)", USERS)
    rows = con.execute(sql).fetchall()
    con.close()
    return rows


def run_py(code: str, var: str):
    ns = {"users": USERS}
    exec(code, ns)
    return ns[var]


# ---------- 文字列としての変換 ----------

def test_filter_comprehension():
    assert sql_of('adults = [u for u in users if u["age"] >= 20]') == \
        "SELECT *\nFROM users\nWHERE age >= 20;"


def test_attribute_access_and_bool_ops():
    sql = sql_of('x = [u.name for u in users if u.age >= 20 and (u.city == "Tokyo" or u.city == "Osaka")]')
    assert "WHERE (age >= 20 AND (city = 'Tokyo' OR city = 'Osaka'))" in sql


def test_string_escape():
    assert "'O''Brien'" in sql_of("""x = [u for u in users if u["name"] == "O'Brien"]""")


def test_is_none_and_in():
    sql = sql_of('x = [u["name"] for u in users if u["score"] is not None and u["city"] in ["Tokyo", "Osaka"]]')
    assert "score IS NOT NULL" in sql and "city IN ('Tokyo', 'Osaka')" in sql


def test_sorted_limit():
    sql = sql_of('top = sorted(users, key=lambda u: u["age"], reverse=True)[:2]')
    assert sql == "SELECT *\nFROM users\nORDER BY age DESC\nLIMIT 2;"


def test_loop_groupby():
    code = '''
d = {}
for u in users:
    d[u["city"]] = d.get(u["city"], 0) + 1
'''
    assert sql_of(code) == "SELECT city, COUNT(*)\nFROM users\nGROUP BY city;"


# ---------- SQLite で結果を比べる ----------

@pytest.mark.parametrize("code,var", [
    ('r = [u["name"] for u in users if u["age"] >= 20]', "r"),
    ('r = [(u["name"], u["age"]) for u in users if u["city"] == "Tokyo"]', "r"),
])
def test_rows_match(code, var):
    py = [x if isinstance(x, tuple) else (x,) for x in run_py(code, var)]
    assert sorted(py) == sorted(run_sql(sql_of(code)))


@pytest.mark.parametrize("code,var", [
    ('r = sum(u["age"] for u in users if u["city"] == "Tokyo")', "r"),
    ('r = len([u for u in users if u["age"] < 30])', "r"),
    ('r = max(u["age"] for u in users)', "r"),
    ('r = 0\nfor u in users:\n    if u["age"] >= 20:\n        r += u["age"]', "r"),
    ('r = 0\nfor u in users:\n    r = r + 1', "r"),
])
def test_scalar_match(code, var):
    results = [r for r in convert_source(code) if r.ok]
    assert len(results) == 1
    assert run_py(code, var) == run_sql(results[0].sql)[0][0]


def test_groupby_match():
    code = 'd = {}\nfor u in users:\n    d[u["city"]] = d.get(u["city"], 0) + u["age"]'
    assert sorted(run_py(code, "d").items()) == sorted(run_sql(sql_of(code)))


# ---------- 変換できない場合・エラー ----------

def test_join_is_unsupported():
    r = convert_source("x = [(a, b) for a in users for b in orders]")[0]
    assert not r.ok and "JOIN" in r.reason


def test_loop_with_else_is_unsupported():
    code = 'for u in users:\n    if u["age"] > 1:\n        out.append(u)\n    else:\n        pass'
    r = convert_source(code)[0]
    assert not r.ok and "else" in r.reason


def test_eq_none_is_rejected():
    r = convert_source('x = [u for u in users if u["score"] == None]')[0]
    assert not r.ok and "is None" in r.reason


def test_division_warning():
    r = convert_source('x = [u["score"] / u["age"] for u in users]')[0]
    assert r.ok and any("割り算" in w for w in r.warnings)


def test_syntax_error():
    with pytest.raises(SourceParseError):
        convert_source("x = [")


def test_non_data_loop_ignored():
    assert convert_source("for i in range(10):\n    print(i)") == []


# ---------- プラグインの仕組み ----------

class FakeAI(Converter):
    """ルールで変換できないときだけ呼ばれることを確かめるダミー。"""
    name = "fake-ai"
    needs_review = True

    def convert(self, snippet, frontend):
        if "orders" not in snippet.source:
            raise UnsupportedPattern("対象外")
        return ConversionResult(snippet=snippet, sql="SELECT 1;", converter=self.name)


def test_plugin_fallback():
    code = "x = [u for u in users]\ny = [(a, b) for a in users for b in orders]"
    r1, r2 = convert_source(code, plugins=[FakeAI()])
    assert r1.converter == "rule" and not r1.needs_review
    assert r2.converter == "fake-ai" and r2.needs_review


# ---------- CLI ----------

def test_cli(tmp_path, capsys):
    f = tmp_path / "a.py"
    f.write_text('x = [u for u in users if u["age"] > 1]', encoding="utf-8")
    assert main([str(f)]) == 0
    assert "WHERE age > 1" in capsys.readouterr().out


def test_cli_missing_file(capsys):
    assert main(["no_such_file.py"]) == 2
    assert "見つかりません" in capsys.readouterr().err


def test_cli_unknown_plugin(tmp_path, capsys):
    f = tmp_path / "a.py"
    f.write_text("x = 1", encoding="utf-8")
    assert main([str(f), "--converter", "nothing"]) == 2
