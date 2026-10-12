"""The agent loop (slide 16): ask the LLM, run the action it chooses, feed the result back, repeat.

    1. The agent asks the LLM what to do      -> llm.create(system, tools, messages)
    2. The harness provides what it needs     -> system prompt + tool schemas + transcript
    3. The LLM chooses an action              -> tool_use blocks
    4. The harness executes it                -> Toolbox.execute (with permission checks)
    5. The result is observed and captured    -> tool_result blocks + activity-feed events
    6. Results go back to the LLM             -> appended to messages; loop until no tool call
"""
import json
from pathlib import Path

from . import config
from .tools import StoppedByUser, Toolbox, ToolError, tool_schemas, summarize_args

ELIDED = "[older tool output removed by the harness to keep the context small; re-run the tool if you need it again]"


class Agent:
    def __init__(self, name: str, system: str, toolbox: Toolbox, llm, emit, transcript_path: Path = None, should_stop=None):
        self.name = name
        self.system = system
        self.toolbox = toolbox
        self.tools = tool_schemas(toolbox.policy)
        self.llm = llm
        self.emit = emit                    # callback(event_dict) -> activity feed
        self.messages = []
        self.transcript_path = transcript_path
        self.should_stop = should_stop or (lambda: False)
        self.last_input_tokens = 0
        self.usage = {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}

    # ---------- context management ----------
    def _compact(self):
        """When the context grows large, drop the bodies of older tool results (keep the most recent)."""
        if self.last_input_tokens < config.COMPACT_AFTER_INPUT_TOKENS:
            return
        results = [(mi, bi) for mi, m in enumerate(self.messages) if m["role"] == "user" and isinstance(m["content"], list)
                   for bi, b in enumerate(m["content"]) if b.get("type") == "tool_result"]
        dropped = 0
        for mi, bi in results[:-config.KEEP_RECENT_TOOL_RESULTS]:
            block = self.messages[mi]["content"][bi]
            if block.get("content") != ELIDED:
                block["content"] = ELIDED
                dropped += 1
        if dropped:
            self.emit({"type": "context", "agent": self.name,
                       "text": f"context at {self.last_input_tokens:,} tokens: elided {dropped} older tool outputs"})

    def _save(self):
        if self.transcript_path:
            self.transcript_path.parent.mkdir(parents=True, exist_ok=True)
            self.transcript_path.write_text(json.dumps({"agent": self.name, "system": self.system, "messages": self.messages,
                                                        "usage": self.usage}, indent=1, ensure_ascii=False), encoding="utf-8")

    # ---------- the loop ----------
    def run(self, user_text: str, max_iterations: int = None) -> str:
        """Run one turn: deliver user_text, loop over tool calls until the model stops calling tools.
        Returns the model's final text for the turn."""
        self.messages.append({"role": "user", "content": user_text})
        final_text = ""
        for _ in range(max_iterations or config.MAX_TOOL_ITERATIONS):
            if self.should_stop():
                self._save()
                raise StoppedByUser(f"{self.name} stopped by the user")
            self._compact()
            response = self.llm.create(self.system, self.tools, self.messages)
            for k in self.usage:
                self.usage[k] += response.usage.get(k, 0)
            self.last_input_tokens = sum(response.usage.get(k, 0) for k in
                                         ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
            self.emit({"type": "usage", "agent": self.name, "usage": response.usage})

            content = response.content
            if response.stop_reason == "max_tokens" and content and content[-1]["type"] == "tool_use":
                # The output limit cut a tool call in half; it cannot be executed.
                content = [b for b in content if b["type"] == "text"] or [{"type": "text", "text": "(output truncated)"}]
                self.messages.append({"role": "assistant", "content": content})
                self.messages.append({"role": "user", "content": "Harness: your last response hit the output-token limit and the tool call "
                                      "was cut off. Write long files in smaller parts: write_file with mode 'w' for the first part, "
                                      "then mode 'a' for each following part."})
                self.emit({"type": "warning", "agent": self.name, "text": "response hit the output limit; asked the agent to write in parts"})
                self._save()
                continue

            self.messages.append({"role": "assistant", "content": content})
            texts = [b["text"] for b in content if b["type"] == "text" and b["text"].strip()]
            if texts:
                final_text = "\n".join(texts)
                self.emit({"type": "agent_text", "agent": self.name, "text": final_text})

            calls = [b for b in content if b["type"] == "tool_use"]
            if not calls:
                self._save()
                return final_text

            results = []
            for call in calls:
                self.emit({"type": "tool_call", "agent": self.name, "tool": call["name"],
                           "text": summarize_args(call["name"], call["input"])})
                try:
                    output = self.toolbox.execute(call["name"], call["input"] or {})
                    is_error = False
                except StoppedByUser:
                    self._save()
                    raise
                except ToolError as exc:
                    output, is_error = f"ERROR: {exc}", True
                except TypeError as exc:
                    output, is_error = f"ERROR: bad arguments for {call['name']}: {exc}", True
                except Exception as exc:  # never let one bad tool call kill the run
                    output, is_error = f"ERROR: {type(exc).__name__}: {exc}", True
                self.emit({"type": "tool_result", "agent": self.name, "tool": call["name"], "error": is_error,
                           "text": output[:600]})
                results.append({"type": "tool_result", "tool_use_id": call["id"], "content": output, "is_error": is_error})
            self.messages.append({"role": "user", "content": results})
            self._save()

        self.emit({"type": "warning", "agent": self.name, "text": "stopped: tool-iteration limit reached"})
        self._save()
        raise RuntimeError(f"{self.name} did not finish within {max_iterations or config.MAX_TOOL_ITERATIONS} tool iterations")
