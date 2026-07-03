from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class Message:
    role: str  # system / user / assistant
    content: str


class BaseLLMClient(ABC):
    """统一的模型客户端接口,上层(agents)只依赖这个抽象。"""

    @abstractmethod
    def chat(self, messages: list[Message]) -> str:
        raise NotImplementedError
