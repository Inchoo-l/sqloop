"""フロントエンドと変換器プラグインの読み込み。"""

from __future__ import annotations

import sys
from importlib.metadata import entry_points

from .base import Converter, Frontend
from .errors import Loop2SqlError, UnknownLanguage
from .frontends.python import PythonFrontend

# インストールしていない状態（ソースから直接実行）でも動くように組み込み分を持っておく
_BUILTIN_FRONTENDS: dict[str, type[Frontend]] = {"python": PythonFrontend}


def _load_group(group: str) -> dict[str, type]:
    """entry points から指定グループのクラスを読み込む。壊れたプラグインは警告して飛ばす。"""
    loaded: dict[str, type] = {}
    for ep in entry_points(group=group):
        try:
            loaded[ep.name] = ep.load()
        except Exception as e:  # プラグイン側の不具合で本体を落とさない
            print(f"警告: プラグイン {ep.name} を読み込めませんでした: {e}", file=sys.stderr)
    return loaded


def frontends() -> dict[str, type[Frontend]]:
    found = dict(_BUILTIN_FRONTENDS)
    found.update(_load_group("sqloop.frontends"))
    return found


def get_frontend(lang: str) -> Frontend:
    table = frontends()
    if lang not in table:
        raise UnknownLanguage(f"未対応の言語です: {lang}（対応: {', '.join(sorted(table))}）")
    return table[lang]()


def detect_language(path: str) -> str:
    """拡張子から言語を推定する。"""
    for name, cls in frontends().items():
        if any(path.endswith(ext) for ext in cls.extensions):
            return name
    raise UnknownLanguage(f"拡張子から言語を判定できません: {path}（--lang で指定してください）")


def get_converters(names: list[str]) -> list[Converter]:
    """名前で指定されたプラグイン変換器を作る。"""
    table = _load_group("sqloop.converters")
    result: list[Converter] = []
    for name in names:
        if name not in table:
            available = ", ".join(sorted(table)) or "なし"
            raise Loop2SqlError(f"変換器プラグインが見つかりません: {name}（インストール済み: {available}）")
        result.append(table[name]())
    return result
