# sqloop

ループで書いたデータ処理コードを、同じ結果になる SQL の `SELECT` 文に変換するコマンドラインツールです。

```python
adults = [u["name"] for u in users if u["age"] >= 20]
```
↓
```sql
SELECT name
FROM users
WHERE age >= 20;
```

## インストール

```bash
pip install git+https://github.com/<ユーザー名>/sqloop.git
```

## 使い方

```bash
sqloop sample.py            # 変換結果を表示
sqloop sample.py --json     # JSON で出力
cat sample.py | sqloop -    # 標準入力から読む
```

終了コード: `0` = すべて変換できた / `1` = 変換できない箇所があった / `2` = エラー（ファイルがない、構文エラーなど）

Python から呼び出すこともできます。

```python
from sqloop import convert_source
for r in convert_source(code):
    print(r.sql if r.ok else r.reason)
```

## 対応している書き方（v0.1・Python のみ）

| Python | SQL |
|---|---|
| `[r for r in t if 条件]` | `SELECT * ... WHERE` |
| `[r["a"] for r in t]` / `[(r.a, r.b) for r in t]` | `SELECT a` / `SELECT a, b` |
| `sum / max / min(r["x"] for r in t)` | `SUM / MAX / MIN` |
| `len([... for r in t if ...])` | `COUNT(*)` |
| `sorted(t, key=lambda r: r["x"], reverse=True)[:n]` | `ORDER BY x DESC LIMIT n` |
| `for r in t: if 条件: out.append(式)` | `SELECT 式 ... WHERE` |
| `for r in t: total += r["x"]` / `cnt += 1` | `SUM(x)` / `COUNT(*)` |
| `for r in t: d[k] = d.get(k, 0) + v` | `GROUP BY` |

条件式: `== != < <= > >=`、`and / or / not`、`is None / is not None`、`in [定数...]`、`+ - * / %`

変換できない書き方は無理に変換せず、行番号と理由を表示します。

また、Python と SQL で動きが違う部分（`/` による割り算、`NOT IN` と NULL など）には警告を出します。

## プラグイン（AI 変換器などの追加）

ルール変換で扱えなかった箇所だけを、プラグインの変換器に回せます。

```python
from sqloop.base import Converter, ConversionResult
from sqloop.errors import UnsupportedPattern

class MyAIConverter(Converter):
    name = "my-ai"
    needs_review = True          # 結果に「要確認」の印を付ける

    def convert(self, snippet, frontend):
        sql = ...                # snippet.source を AI に渡すなど
        if sql is None:
            raise UnsupportedPattern("変換できませんでした")
        return ConversionResult(snippet=snippet, sql=sql, converter=self.name)
```

別パッケージの `pyproject.toml` に登録します。

```toml
[project.entry-points."sqloop.converters"]
my-ai = "my_package:MyAIConverter"
```

```bash
sqloop sample.py --converter my-ai
```

新しい言語は `sqloop.base.Frontend` を継承したクラスを作り、`sqloop.frontends` に登録します。

## 開発

```bash
pip install -e ".[dev]"
pytest
```

テストでは、変換した SQL を SQLite で実際に実行し、元の Python コードと結果が一致するかを確かめています。

## ライセンス

MIT
