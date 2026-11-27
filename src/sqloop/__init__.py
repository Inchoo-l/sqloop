"""sqloop: ループで書いたデータ処理コードを SQL に変換する。"""

from .core import convert_source
from .errors import Loop2SqlError, UnsupportedPattern
from .ir import Select

__all__ = ["convert_source", "Select", "Loop2SqlError", "UnsupportedPattern"]
__version__ = "0.1.0"
