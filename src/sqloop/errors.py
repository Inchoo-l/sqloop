"""sqloop で使う例外。"""


class Loop2SqlError(Exception):
    """sqloop 全体の基底例外。"""


class UnsupportedPattern(Loop2SqlError):
    """変換器が扱えない書き方に出会ったときに投げる。

    次の変換器（プラグイン）に処理を回すための合図にもなる。
    """

    def __init__(self, reason: str, lineno: int | None = None):
        super().__init__(reason)
        self.reason = reason
        self.lineno = lineno


class SourceParseError(Loop2SqlError):
    """入力コード自体が構文エラーで解析できないとき。"""


class UnknownLanguage(Loop2SqlError):
    """対応していない言語が指定されたとき。"""
