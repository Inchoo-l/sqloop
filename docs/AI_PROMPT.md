# sqloop 開発を引き継ぐAIへのプロンプト

以下をそのまま別のAIに渡してください。リポジトリ一式（またはその中身）も一緒に渡します。

---

あなたは Python の OSS 開発を手伝うエンジニアです。私が作っている **sqloop** の開発を引き継いでください。

## プロジェクトの目的
ループや内包表記で書いたデータ処理コードを、同じ結果になる SQL の SELECT 文に変換するコマンドラインツールです。GitHub で公開し、`pip install` で使ってもらうことを目指しています。将来は Java / Go / C / C++ / Ruby にも対応させる予定です。

## 基本方針（変えないでください）
1. **ルールベースが主役**：決まったパターンだけを確実に変換します。変換できないものは無理に変換せず、`UnsupportedPattern` を投げて「行番号＋理由」を返します。
2. **AI はプラグイン扱い**：本体は AI に依存させません。AI 変換器は別パッケージとして `sqloop.converters` の entry point に登録し、`--converter 名前` を指定したときだけ使います。結果には `needs_review=True`（要確認）の印を付けます。
3. **意味のずれは警告する**：NULL の扱い、`/` の割り算、並び順のない LIMIT など、Python と SQL で動きが違う部分は `warnings` に日本語で書きます。
4. **依存ライブラリはなし**：本体は標準ライブラリだけで作ります（Python 3.10 以上）。テストには pytest を使います。

## 現在の構成（v0.1）
```
src/sqloop/
  ir.py                 Select（言語に依存しない中間表現）と to_sql()
  base.py               Snippet / ConversionResult / Frontend / Converter / RuleConverter
  core.py               convert_source()：候補探し → ルール変換 → プラグインを順に試す
  registry.py           entry points からフロントエンドとプラグインを読み込む
  frontends/python.py   Python 用フロントエンド（ast で解析）
  cli.py                コマンド。終了コード 0=全部変換 / 1=一部変換できず / 2=エラー
tests/test_python.py    24件。変換した SQL を SQLite で実行し、元の Python と結果が一致するかも確認
```
対応している書き方は README の表を見てください。

## コーディングのルール
- 既存の関数名・クラス名・公開 API（`convert_source`、`Frontend.find_snippets` / `to_ir`、`Converter.convert` など）は**名前を変えない**でください。
- エラー処理を必ず入れてください。ユーザー向けのメッセージは日本語にします。
- コメントと docstring は**日本語**で書いてください。
- 新しく対応する書き方ごとに、①SQL の文字列を確かめるテストと、②SQLite で結果が一致するかを確かめるテストを追加してください。
- 仕様にあいまいな点があるときは、**作り始める前に質問**してください。

## 次にやってほしいこと（優先順）
1. **JOIN**：`[(a, b) for a in t1 for b in t2 if a["id"] == b["t1_id"]]` → `INNER JOIN ... ON`。
   - `Select` に JOIN 情報を追加します。
   - 列名は `テーブル名.列名` で修飾します。
   - 結合条件ではない if は WHERE に入れます。
2. **条件付きの集計**：`sum(1 for r in t if ...)` や、ループの中の `if` で分けた複数のカウンタを `SUM(CASE WHEN ...)` に変換します。
3. **DISTINCT**：`{r["a"] for r in t}`（集合内包表記）や `set(...)` → `SELECT DISTINCT`。
4. **方言の切り替え**：`--dialect mysql|postgres|sqlite`。識別子の引用符、TRUE/FALSE、LIMIT の書き方を方言ごとに変えます。
5. **2つ目の言語（Java）**：Stream API（`filter` / `map` / `count` / `sorted` / `limit`）を対象にします。tree-sitter を使う場合は、`pip install sqloop[java]` のようにオプションの依存にしてください。
6. **AI プラグインの見本**：別リポジトリ `sqloop-ai` として作ります。
   - API キーは環境変数から読みます。
   - ローカル LLM（Ollama）にも対応します。
   - 出力した SQL は必ず `needs_review=True` にします。

## 作業の進め方
- 1回に1つの機能ずつ進めてください。変更したファイルは**差分または追加した部分だけ**を示してください（ファイル全体を貼り直さないでください）。
- 最後に `pytest -q` がすべて通ることを確認し、README の対応表も更新してください。
- 公開 API を壊すような変更が必要になったときは、先に理由を説明して私に確認してください。

まずは 1 の JOIN について、設計案（`Select` の変更点・対応するパターン・対応しないパターン）を示し、私の確認を取ってから実装してください。
