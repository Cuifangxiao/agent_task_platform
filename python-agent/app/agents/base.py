from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class AgentContext:
    """一次 agent 调用的上下文:谁在问、在哪个会话里问。"""

    user_id: int
    session_id: str = ""
    metadata: dict = field(default_factory=dict)


class BaseAgent(ABC):
    """所有 agent 的基类。

    一个 agent = 模型客户端 + 可选的记忆 / 检索 / 工具。
    """

    @abstractmethod
    def run(self, message: str, ctx: AgentContext) -> str:
        """处理一条用户消息,返回回复文本。"""
        raise NotImplementedError
