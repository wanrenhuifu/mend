"""LLM 层：一个抽象 + 一个 OpenAI 兼容实现 + 一个离线假模型。

为什么要假模型：agent 的核心是循环、工具调度和验证闭环，不是"能不能调到 API"。
有了 FakeLLM，整套流程能在没有密钥、没有网络的情况下跑通，也能被测试稳定覆盖。
"""

from __future__ import annotations

import itertools
import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Protocol

_counter = itertools.count(1)


class LLMError(RuntimeError):
    pass


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class Message:
    role: str  # system | user | assistant | tool
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str = ""  # role == "tool" 时指回被响应的调用

    def to_api(self) -> dict[str, Any]:
        msg: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.tool_calls:
            msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                    },
                }
                for tc in self.tool_calls
            ]
        if self.tool_call_id:
            msg["tool_call_id"] = self.tool_call_id
        return msg


class LLM(Protocol):
    """只要求一个方法：给对话和工具表，返回下一条 assistant 消息。"""

    usage: dict[str, int]

    def chat(self, messages: list[Message], tools: list[dict[str, Any]]) -> Message: ...


class OpenAICompatLLM:
    """任何 OpenAI 兼容端点都能接：DeepSeek / Qwen / GLM / Kimi / OpenAI / 本地 vLLM。"""

    def __init__(self, model: str, base_url: str, api_key: str, timeout: int = 120) -> None:
        if not api_key:
            raise LLMError("没有找到 API key：设置环境变量 MEND_API_KEY（或写进 .env），或者用 --fake 离线跑。")
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.usage = {"prompt_tokens": 0, "completion_tokens": 0}

    def chat(self, messages: list[Message], tools: list[dict[str, Any]]) -> Message:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [m.to_api() for m in messages],
            "temperature": 0,  # 修 bug 不需要创造力：同样的输入要能得到同样的行为
        }
        if tools:
            payload["tools"] = tools
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:500]
            raise LLMError(f"HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise LLMError(f"网络错误: {exc.reason}") from exc

        usage = data.get("usage") or {}
        for key in self.usage:
            self.usage[key] += int(usage.get(key) or 0)
        return self._parse(data)

    @staticmethod
    def _parse(data: dict[str, Any]) -> Message:
        choices = data.get("choices") or []
        if not choices:
            raise LLMError(f"模型没有返回 choices: {json.dumps(data, ensure_ascii=False)[:300]}")
        raw = choices[0].get("message") or {}
        calls: list[ToolCall] = []
        for item in raw.get("tool_calls") or []:
            fn = item.get("function") or {}
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {"_raw": fn.get("arguments")}  # 参数解析失败也不要炸掉整个循环
            if not isinstance(args, dict):
                args = {"_raw": args}
            calls.append(
                ToolCall(
                    id=item.get("id") or f"call_{next(_counter)}",
                    name=fn.get("name") or "",
                    arguments=args,
                )
            )
        return Message(role="assistant", content=raw.get("content") or "", tool_calls=calls)


class FakeLLM:
    """按剧本回放的假模型：脚本用尽后就返回一句收尾消息。"""

    def __init__(self, script: list[Message] | None = None, final: str = "（离线演示结束）") -> None:
        self.script = list(script or [])
        self.final = final
        self.usage = {"prompt_tokens": 0, "completion_tokens": 0}
        self.calls = 0

    def chat(self, messages: list[Message], tools: list[dict[str, Any]]) -> Message:
        self.calls += 1
        if self.script:
            return self.script.pop(0)
        return Message(role="assistant", content=self.final)


def call(name: str, **arguments: Any) -> Message:
    """写脚本用：call("read_file", path="a.py") -> 一条发起工具调用的 assistant 消息。"""
    return Message(
        role="assistant",
        tool_calls=[ToolCall(id=f"call_{next(_counter)}", name=name, arguments=arguments)],
    )


def say(text: str) -> Message:
    """写脚本用：say("改好了") -> 一条不再调用工具的收尾消息。"""
    return Message(role="assistant", content=text)


# `--fake` 的默认剧本：任何仓库都能跑，用来演示上下文构建、工具调度和轨迹。
DEMO_SCRIPT = [
    call("list_dir", path="."),
    call("search", pattern="def |class |function "),
]
