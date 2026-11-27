"""変換の流れ: 候補を探す → ルール変換 → だめならプラグインを順に試す。"""

from __future__ import annotations

from .base import ConversionResult, Converter, RuleConverter
from .errors import UnsupportedPattern
from .registry import get_frontend


def convert_source(
    source: str,
    lang: str = "python",
    plugins: list[Converter] | None = None,
) -> list[ConversionResult]:
    """コード全体を変換し、候補ごとの結果を返す。"""
    frontend = get_frontend(lang)
    converters: list[Converter] = [RuleConverter(), *(plugins or [])]

    results: list[ConversionResult] = []
    for snippet in frontend.find_snippets(source):
        reasons: list[str] = []
        result: ConversionResult | None = None
        for conv in converters:
            try:
                result = conv.convert(snippet, frontend)
                result.needs_review = result.needs_review or conv.needs_review
                break
            except UnsupportedPattern as e:
                reasons.append(f"[{conv.name}] {e.reason}")
            except Exception as e:  # プラグインの予期しない失敗は理由として残して次へ
                reasons.append(f"[{conv.name}] 予期しないエラー: {e}")
        if result is None:
            result = ConversionResult(snippet=snippet, reason=" / ".join(reasons))
        results.append(result)
    return results
