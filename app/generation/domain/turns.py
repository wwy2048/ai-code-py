from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TurnRequest:
    """一次 Turn 执行请求；它不是入站事件，只进入执行器端口。"""

    app_id: int
    user_id: int
    code_gen_type: str
    message: str
    turn_id: str
