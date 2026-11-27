"""コマンドラインの入口: sqloop FILE [--lang python] [--json] [--converter NAME]"""

from __future__ import annotations

import argparse
import json
import sys

from . import __version__
from .core import convert_source
from .errors import Loop2SqlError
from .registry import detect_language, get_converters

# 終了コード
EXIT_OK = 0           # すべて変換できた
EXIT_PARTIAL = 1      # 変換できない箇所があった
EXIT_ERROR = 2        # ファイルが読めない・構文エラーなど


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="sqloop",
        description="ループで書いたデータ処理コードを SQL の SELECT 文に変換します。",
    )
    p.add_argument("file", help="変換するソースファイル（- で標準入力）")
    p.add_argument("--lang", help="言語（省略時は拡張子から判定）")
    p.add_argument("--json", action="store_true", help="結果を JSON で出力する")
    p.add_argument(
        "--converter", action="append", default=[], metavar="NAME",
        help="ルール変換で扱えなかった箇所に使うプラグイン（複数指定可）",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p


def _read(path: str) -> str:
    if path == "-":
        return sys.stdin.read()
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        raise Loop2SqlError(f"ファイルが見つかりません: {path}")
    except UnicodeDecodeError:
        raise Loop2SqlError(f"UTF-8 として読めません: {path}")
    except OSError as e:
        raise Loop2SqlError(f"ファイルを読めません: {path}（{e.strerror}）")


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        lang = args.lang or (detect_language(args.file) if args.file != "-" else "python")
        source = _read(args.file)
        plugins = get_converters(args.converter)
        results = convert_source(source, lang=lang, plugins=plugins)
    except Loop2SqlError as e:
        print(f"エラー: {e}", file=sys.stderr)
        return EXIT_ERROR

    if args.json:
        out = [
            {
                "line": r.snippet.lineno,
                "source": r.snippet.source,
                "sql": r.sql,
                "converter": r.converter,
                "needs_review": r.needs_review,
                "warnings": r.warnings,
                "reason": r.reason,
            }
            for r in results
        ]
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        if not results:
            print("変換候補が見つかりませんでした。")
        for r in results:
            head = f"--- {r.snippet.lineno}行目"
            if r.ok:
                mark = "（要確認）" if r.needs_review else ""
                print(f"{head} [{r.converter}]{mark} ---")
                print(r.snippet.source)
                print("↓")
                print(r.sql)
                for w in r.warnings:
                    print(f"  ⚠ {w}")
            else:
                print(f"{head} 変換できません ---")
                print(r.snippet.source)
                print(f"  理由: {r.reason}")
            print()

    return EXIT_OK if all(r.ok for r in results) else EXIT_PARTIAL


if __name__ == "__main__":
    sys.exit(main())
