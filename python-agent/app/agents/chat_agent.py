"""最基础的聊天 agent:system prompt + 历史消息 + 当前消息 -> LLM。"""

from app.agents.base import AgentContext, BaseAgent
from app.llm.base import BaseLLMClient, Message


class ChatAgent(BaseAgent):
    def __init__(self, llm: BaseLLMClient, system_prompt: str = "") -> None:
        self._llm = llm
        self._system_prompt = system_prompt

    def run(self, message: str, ctx: AgentContext) -> str:
        messages: list[Message] = []
        if self._system_prompt:
            messages.append(Message(role="system", content=self._system_prompt))
        # TODO: 接入 app/memory,把 ctx.session_id 对应的历史消息插到这里
        messages.append(Message(role="user", content=message))
        return self._llm.chat(messages)
