"""handler 依赖组装(手写 DI 容器)。

新增 handler 时: 在 AgentHandlers 加字段, 在 build_agent_handlers 里 new 出依赖并注入。
"""

from dataclasses import dataclass

from core.deck import DeckService
from core.provider.service import ProviderService
from handler.ppt_handler import PptHandler
from handler.provider_handler import ProviderHandler


@dataclass(frozen=True)
class AgentHandlers:
    provider_handler: ProviderHandler
    ppt_handler: PptHandler


def build_agent_handlers() -> AgentHandlers:
    return AgentHandlers(
        provider_handler=ProviderHandler(ProviderService()),
        ppt_handler=PptHandler(DeckService()),
    )
