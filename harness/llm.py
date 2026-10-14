"""The LLM: the only place the harness talks to the Claude API.

Messages are kept as plain Anthropic-format dicts so a transcript can be saved to disk
and resumed. The LLM is swappable: tests pass a scripted fake with the same interface.
"""
from dataclasses import dataclass, field

from . import config


@dataclass
class LLMResponse:
    content: list            # list of block dicts: {"type": "text"|"tool_use", ...}
    stop_reason: str
    usage: dict = field(default_factory=dict)


def with_cache_breakpoint(messages: list) -> list:
    """Return a copy of messages whose last content block carries cache_control."""
    if not messages:
        return messages
    out = list(messages[:-1])
    last = dict(messages[-1])
    content = last["content"]
    if isinstance(content, str):
        content = [{"type": "text", "text": content}]
    content = [dict(b) for b in content]
    content[-1]["cache_control"] = {"type": "ephemeral"}
    last["content"] = content
    out.append(last)
    return out


class AnthropicLLM:
    def __init__(self, api_key: str = None, model: str = None, max_tokens: int = None):
        import anthropic  # imported lazily so tests run without the package configured
        key = api_key or config.ANTHROPIC_API_KEY
        if not key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set on the server")
        headers = {"anthropic-workspace-id": config.ANTHROPIC_WORKSPACE_ID} if config.ANTHROPIC_WORKSPACE_ID else None
        self.client = anthropic.Anthropic(api_key=key, max_retries=4, default_headers=headers)
        self.model = model or config.MODEL
        self.max_tokens = max_tokens or config.MAX_TOKENS
        self._bad_request = anthropic.BadRequestError

    def create(self, system: str, tools: list, messages: list, max_tokens: int = None) -> LLMResponse:
        # Prompt caching: the system prompt (CLAUDE.md + agent definition + skill) and the tool
        # list are identical on every call of an agent, so they are cached after the first call.
        cached_tools = [dict(t) for t in tools]
        if cached_tools:
            cached_tools[-1]["cache_control"] = {"type": "ephemeral"}
        system_blocks = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
        # Incremental conversation caching: mark the end of the transcript so the next call reads the
        # whole history from the cache instead of paying full price for it again on every step.
        messages = with_cache_breakpoint(messages)
        # Streaming is required by the API for long generations (e.g. writing rules.json).
        limit = max_tokens or self.max_tokens
        try:
            message = self._call(limit, system_blocks, cached_tools, messages)
        except self._bad_request as exc:
            # A larger per-agent output limit than this model allows: fall back to the default and remember it.
            if "max_tokens" not in str(exc) or limit <= config.MAX_TOKENS:
                raise
            self.max_tokens = config.MAX_TOKENS
            message = self._call(self.max_tokens, system_blocks, cached_tools, messages)
        return self._normalise(message)

    def _call(self, limit, system_blocks, tools, messages):
        with self.client.messages.stream(model=self.model, max_tokens=limit, system=system_blocks,
                                         tools=tools, messages=messages) as stream:
            return stream.get_final_message()

    @staticmethod
    def _normalise(message) -> LLMResponse:
        blocks = []
        for block in message.content:
            if block.type == "text" and block.text.strip():  # the API rejects empty text blocks on replay
                blocks.append({"type": "text", "text": block.text})
            elif block.type == "tool_use":
                blocks.append({"type": "tool_use", "id": block.id, "name": block.name, "input": block.input})
        if not blocks:
            blocks = [{"type": "text", "text": "(no output)"}]
        u = message.usage
        usage = {"input_tokens": u.input_tokens, "output_tokens": u.output_tokens,
                 "cache_read_input_tokens": getattr(u, "cache_read_input_tokens", 0) or 0,
                 "cache_creation_input_tokens": getattr(u, "cache_creation_input_tokens", 0) or 0}
        return LLMResponse(content=blocks, stop_reason=message.stop_reason, usage=usage)
