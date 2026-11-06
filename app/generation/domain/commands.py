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



@dataclass(frozen=True, slots=True)
class EnqueueFollowupCommand:
    """运行中提交一条 Followup，等待当前 Turn 结束后执行。"""

    app_id: int
    user_id: int
    code_gen_type: str
    message: str
    command_id: str
    clear_paused: bool = False


@dataclass(frozen=True, slots=True)
class SteerQueuedCommand:
    """把一条尚未消费的排队项提升到当前 Turn 的 Steering 收件箱。"""

    app_id: int
    command_id: str
    item_id: str


@dataclass(frozen=True, slots=True)
class DeleteQueuedCommand:
    """删除一条尚未消费的排队项。"""

    app_id: int
    command_id: str
    item_id: str


@dataclass(frozen=True, slots=True)
class ResumeFollowupsCommand:
    """恢复全部暂停的 Followup，并按原顺序继续执行。"""

    app_id: int
    command_id: str


@dataclass(frozen=True, slots=True)
class ClearPausedFollowupsCommand:
    """清空全部暂停的 Followup。"""

    app_id: int
    command_id: str


type QueueCommand = (
    EnqueueFollowupCommand
    | SteerQueuedCommand
    | DeleteQueuedCommand
    | ResumeFollowupsCommand
    | ClearPausedFollowupsCommand
)
