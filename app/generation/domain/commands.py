from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class StartTurnCommand:
    """启动一个新 Turn 的入站命令。"""

    app_id: int
    user_id: int
    code_gen_type: str
    message: str
    turn_id: str


@dataclass(frozen=True, slots=True)
class SteerTurnCommand:
    """把用户输入插入当前 Turn 的模型步边界。"""

    app_id: int
    user_id: int
    code_gen_type: str
    turn_id: str
    command_id: str
    text: str


@dataclass(frozen=True, slots=True)
class InterruptTurnCommand:
    """停止当前 Turn 的控制命令。"""

    app_id: int
    user_id: int
    code_gen_type: str
    turn_id: str
    command_id: str


@dataclass(frozen=True, slots=True)
class RedirectTurnCommand:
    """停止当前模型步并携带新意图继续当前 Turn。"""

    app_id: int
    user_id: int
    code_gen_type: str
    turn_id: str
    command_id: str
    text: str


type ControlCommand = SteerTurnCommand | InterruptTurnCommand | RedirectTurnCommand
type InboundCommand = StartTurnCommand | ControlCommand
