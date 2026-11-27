"""言語に依存しない中間表現（IR）と SQL 出力。

各言語のフロントエンドはコードをこの Select に変換し、
SQL の組み立てはここに一本化する。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Select:
    """1 つの SELECT 文を表す。式はすでに SQL 文字列に変換済みのものを持つ。"""

    table: str
    columns: list[str] = field(default_factory=lambda: ["*"])
    where: str | None = None
    group_by: list[str] = field(default_factory=list)
    # (式, 降順なら True)
    order_by: list[tuple[str, bool]] = field(default_factory=list)
    limit: int | None = None

    def to_sql(self) -> str:
        """SQL 文字列を組み立てる。"""
        if not self.table:
            raise ValueError("テーブル名が空です")
        if not self.columns:
            raise ValueError("SELECT する列がありません")

        lines = [f"SELECT {', '.join(self.columns)}", f"FROM {self.table}"]
        if self.where:
            lines.append(f"WHERE {self.where}")
        if self.group_by:
            lines.append(f"GROUP BY {', '.join(self.group_by)}")
        if self.order_by:
            parts = [f"{expr} DESC" if desc else expr for expr, desc in self.order_by]
            lines.append(f"ORDER BY {', '.join(parts)}")
        if self.limit is not None:
            if self.limit < 0:
                raise ValueError("LIMIT に負の値は使えません")
            lines.append(f"LIMIT {self.limit}")
        return "\n".join(lines) + ";"
