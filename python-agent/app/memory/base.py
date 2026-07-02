from abc import ABC, abstractmethod

from app.llm.base import Message


class BaseMemory(ABC):
    @abstractmethod
    def load(self, session_id: str, limit: int = 20) -> list[Message]:
        raise NotImplementedError

    @abstractmethod
    def append(self, session_id: str, message: Message) -> None:
        raise NotImplementedError
