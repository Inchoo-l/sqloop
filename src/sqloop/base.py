"""フロントエンド（言語ごと）と変換器（ルール / AI など）の共通インターフェース。

新しい言語やAI変換器を足すときは、ここのクラスを継承して
pyproject.toml の entry points に登録するだけでよい。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from .ir import Select


@dataclass
class Snippet:
    """変換候補になったコードの断片。"""

    lang: str          # 言語名（"python" など）
    source: str        # 元のコード片
    lineno: int        # 開始行
    node: Any = None   # フロントエンド固有の構文木ノード（ルール変換で使う）


@dataclass
class ConversionResult:
    """1 つの断片の変換結果。"""

    snippet: Snippet
    sql: str | None = None
    converter: str | None = None          # どの変換器が変換したか
    warnings: list[str] = field(default_factory=list)
    reason: str | None = None              # 変換できなかった理由
    needs_review: bool = False             # AI など、人の確認が必要な結果か

    @property
    def ok(self) -> bool:
        return self.sql is not None


class Frontend(ABC):
    """言語ごとの解析器。"""

    name: str = ""
    extensions: tuple[str, ...] = ()

    @abstractmethod
    def find_snippets(self, source: str) -> list[Snippet]:
        """コード全体から、変換候補になる断片を探す。"""

    @abstractmethod
    def to_ir(self, snippet: Snippet) -> tuple[Select, list[str]]:
        """ルールで IR に変換する。(Select, 警告一覧) を返す。

        扱えない書き方なら UnsupportedPattern を投げる。
        """


class Converter(ABC):
    """変換器。ルール変換のあとに順番に試される。"""

    name: str = ""
    needs_review: bool = False

    @abstractmethod
    def convert(self, snippet: Snippet, frontend: Frontend) -> ConversionResult:
        """断片を SQL に変換する。扱えなければ UnsupportedPattern を投げる。"""


class RuleConverter(Converter):
    """組み込みのルール変換器。フロントエンドの to_ir を呼ぶだけ。"""

    name = "rule"

    def convert(self, snippet: Snippet, frontend: Frontend) -> ConversionResult:
        select, warnings = frontend.to_ir(snippet)
        return ConversionResult(
            snippet=snippet,
            sql=select.to_sql(),
            converter=self.name,
            warnings=warnings,
        )
