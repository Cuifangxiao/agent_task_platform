from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass(frozen=True)
class RetrievedChunk:
    content: str
    score: float
    metadata: dict = field(default_factory=dict)


class BaseRetriever(ABC):
    """检索抽象:agent 通过它拿到与 query 相关的知识片段。"""

    @abstractmethod
    def retrieve(self, query: str, top_k: int = 5) -> list[RetrievedChunk]:
        raise NotImplementedError
