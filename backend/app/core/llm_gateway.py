"""大模型容灾网关（指导书 6.1）：统一入口、主备自动切换、超时熔断、成本/配额记录。

双模式：
  * 真实模式：配置了 DEEPSEEK_API_KEY / DASHSCOPE_API_KEY 时自动启用；
    主通道连续失败/超时 -> 切换备用（dashscope），均失败 -> 降级 demo 兜底。
  * 离线演示模式：无任何密钥时使用确定性模板 Provider（LLMProvider="demo"），
    系统仍可完整演示（数字直读数据库，仅文本组织走模板）。

全部大模型调用必须经由本网关，禁止业务代码直连供应商接口。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, UTC
from typing import Any
from collections.abc import AsyncIterator

import httpx

from backend.app.core.config import settings
from backend.app.models import DailyCost, LLMCallLog
from backend.app.core.db import SessionLocal

logger = logging.getLogger("icops.llm")

# 单价（元 / 百万 token），用于成本估算与预算告警（以官方牌价为准，可调整）
_PRICE = {
    "deepseek-chat": {"input": 2.0, "output": 8.0},
    "qwen-plus": {"input": 0.8, "output": 2.0},
    "demo": {"input": 0.0, "output": 0.0},
}


class LLMBudgetExceeded(Exception):
    pass


@dataclass
class LLMResult:
    text: str
    provider: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_cny: float = 0.0
    latency_ms: int = 0
    degraded: bool = False


@dataclass
class _Candidate:
    name: str
    base_url: str
    api_key: str
    model: str


class _OpenAIChannel:
    """OpenAI 兼容通道。"""

    def __init__(self, cand: _Candidate) -> None:
        self.cand = cand
        self.client = httpx.AsyncClient(
            base_url=cand.base_url.rstrip("/"),
            timeout=httpx.Timeout(settings.llm_timeout_stream_total, connect=10.0),
            headers={
                "Authorization": f"Bearer {cand.api_key}",  # 关键：OpenAI 兼容通道必须带 Bearer 密钥
                "Content-Type": "application/json",
            },
        )

    async def close(self) -> None:
        await self.client.aclose()

    async def chat(
        self, messages: list[dict], json_mode: bool = False, temperature: float = 0.3
    ) -> tuple[str, int, int]:
        body: dict[str, Any] = {
            "model": self.cand.model,
            "messages": messages,
            "temperature": temperature,
            "stream": False,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        r = await self.client.post("/chat/completions", json=body)
        r.raise_for_status()
        data = r.json()
        choice = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})
        return choice, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)

    async def stream_chat(self, messages: list[dict], temperature: float = 0.3) -> AsyncIterator[str]:
        body = {
            "model": self.cand.model,
            "messages": messages,
            "temperature": temperature,
            "stream": True,
        }
        # 首字节超时约束（指导书 6.1：流式首字节 <=3s）
        async with self.client.stream(
            "POST",
            "/chat/completions",
            json=body,
            timeout=httpx.Timeout(
                settings.llm_timeout_stream_first_byte, read=settings.llm_timeout_stream_total
            ),
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                try:
                    delta = json.loads(payload)["choices"][0]["delta"].get("content", "")
                except (KeyError, IndexError, json.JSONDecodeError):
                    continue
                if delta:
                    yield delta


class DemoProvider:
    """离线演示 Provider：确定性模板组织语言，零成本、可离线、可复现。

    按指导书“数字不出模型”策略：业务数字一律由数据库直读后以“数据事实”
    形式注入 prompt，演示模式只负责把这些事实组织成通顺的中文答复。
    """

    name = "demo"
    model = "demo-template"

    def _build_reply(self, messages: list[dict]) -> str:
        # 取最后一条 user 消息中由各智能体注入的“数据事实”行
        facts: list[str] = []
        for msg in reversed(messages):
            if msg.get("role") != "user":
                continue
            for line in str(msg.get("content", "")).splitlines():
                line = line.strip()
                if line.startswith("数据事实:") or line.startswith("[事实]"):
                    facts.append(line.split(":", 1)[1].strip() if ":" in line else line)
        lines: list[str] = []
        if facts:
            lines.append("根据系统检索与核算，为您汇总如下：")
            for f in facts[:14]:
                lines.append(f"· {f}")
            lines.append("以上内容中所有数值均来自结构化数据库与知识库直读，可逐项溯源。")
        else:
            lines.append("已收到您的需求。建议进一步说明工程量、工期与预算，以便生成量化方案。")
        lines.append("（AI 生成初稿，需人工确认）")
        return "\n".join(lines)

    async def chat(
        self, messages: list[dict], json_mode: bool = False, temperature: float = 0.3
    ) -> tuple[str, int, int]:
        text = self._build_reply(messages)
        return text, len(text), 0

    async def stream_chat(self, messages: list[dict], temperature: float = 0.3) -> AsyncIterator[str]:
        text, _, _ = await self.chat(messages, temperature=temperature)
        # 按小片段流式吐出，模拟 SSE 增量输出
        for i in range(0, len(text), 6):
            yield text[i : i + 6]
            await asyncio.sleep(0.002)


class LLMGateway:
    """容灾网关。"""

    def __init__(self) -> None:
        self._channels: dict[str, _OpenAIChannel] = {}
        self.demo = DemoProvider()
        self._demo_fallback_used: set[str] = set()
        # 最近一次调用的实际提供方（供 /api/health、SSE 与前端显式展示是否真的用了 DeepSeek）
        self.last_meta: dict = {}

    # ---------- 候选通道 ----------
    def _real_candidates(self) -> list[_Candidate]:
        out: list[_Candidate] = []
        if settings.has_deepseek:
            out.append(
                _Candidate("deepseek", settings.llm_base_url, settings.deepseek_api_key, settings.llm_model)
            )
        if settings.has_dashscope:
            out.append(
                _Candidate(
                    "dashscope",
                    settings.llm_backup_base_url,
                    settings.dashscope_api_key,
                    settings.llm_backup_model,
                )
            )
        return out

    @property
    def mode(self) -> str:
        """health 端点使用：返回 deepseek / dashscope / demo。"""
        if settings.has_deepseek:
            return "deepseek"
        if settings.has_dashscope:
            return "dashscope"
        return "demo"

    async def _channel(self, cand: _Candidate) -> _OpenAIChannel:
        if cand.name not in self._channels:
            self._channels[cand.name] = _OpenAIChannel(cand)
        return self._channels[cand.name]

    async def aclose(self) -> None:
        for ch in self._channels.values():
            await ch.close()
        self._channels.clear()

    # ---------- 预算检查 ----------
    def _check_budget(self, scene: str) -> None:
        if settings.is_demo_env:
            return
        day = datetime.now(UTC).strftime("%Y-%m-%d")
        db = SessionLocal()
        try:
            row = db.query(DailyCost).filter(DailyCost.day == day).first()
            if row and row.cost_cny > settings.llm_daily_budget_cny:
                raise LLMBudgetExceeded(
                    f"今日大模型费用 {row.cost_cny:.2f} 元已超日预算 {settings.llm_daily_budget_cny} 元，已熔断（指导书 6.1）"
                )
        finally:
            db.close()

    def _log_call(
        self,
        *,
        provider: str,
        model: str,
        scene: str,
        prompt_tokens: int,
        completion_tokens: int,
        cost_cny: float,
        latency_ms: int,
        ok: bool,
    ) -> None:
        db = SessionLocal()
        try:
            db.add(
                LLMCallLog(
                    provider=provider,
                    model=model,
                    scene=scene,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    cost_cny=cost_cny,
                    latency_ms=latency_ms,
                    ok=ok,
                )
            )
            if cost_cny > 0:
                day = datetime.now(UTC).strftime("%Y-%m-%d")
                row = db.query(DailyCost).filter(DailyCost.day == day).first()
                if row is None:
                    db.add(DailyCost(day=day, cost_cny=cost_cny))
                else:
                    row.cost_cny += cost_cny
            db.commit()
        except Exception:  # noqa: BLE001  日志失败不影响主流程
            db.rollback()
        finally:
            db.close()

    def _estimate_cost(self, provider: str, prompt: int, completion: int) -> float:
        p = _PRICE.get(provider, _PRICE["qwen-plus"]) if provider != "demo" else _PRICE["demo"]
        return (prompt * p["input"] + completion * p["output"]) / 1_000_000

    # ---------- 主入口 ----------
    async def complete(
        self,
        messages: list[dict],
        *,
        scene: str = "general",
        json_mode: bool = False,
        temperature: float = 0.3,
    ) -> LLMResult:
        self._check_budget(scene)
        start = time.perf_counter()
        candidates = self._real_candidates()
        errors: list[str] = []
        for cand in candidates:
            try:
                ch = await self._channel(cand)
                text, pt, ct = await ch.chat(messages, json_mode=json_mode, temperature=temperature)
                latency = int((time.perf_counter() - start) * 1000)
                cost = self._estimate_cost(cand.name, pt, ct)
                self._log_call(
                    provider=cand.name,
                    model=cand.model,
                    scene=scene,
                    prompt_tokens=pt,
                    completion_tokens=ct,
                    cost_cny=cost,
                    latency_ms=latency,
                    ok=True,
                )
                self.last_meta = {
                    "provider": cand.name,
                    "model": cand.model,
                    "degraded": False,
                    "scene": scene,
                    "latency_ms": latency,
                }
                return LLMResult(
                    text=text,
                    provider=cand.name,
                    model=cand.model,
                    prompt_tokens=pt,
                    completion_tokens=ct,
                    cost_cny=cost,
                    latency_ms=latency,
                )
            except Exception as exc:  # noqa: BLE001  失败-> 尝试下一个候选（熔断切换）
                errors.append(f"{cand.name}: {exc}")
                logger.warning("LLM 通道 %s 调用失败，准备切换：%s", cand.name, exc)
        # 全部真实通道失败 -> demo 兜底（保证演示不中断；事件记录在案）
        text, pt, ct = await self.demo.chat(messages, json_mode=json_mode, temperature=temperature)
        latency = int((time.perf_counter() - start) * 1000)
        self._log_call(
            provider="demo",
            model=self.demo.model,
            scene=scene,
            prompt_tokens=pt,
            completion_tokens=ct,
            cost_cny=0.0,
            latency_ms=latency,
            ok=True,
        )
        logger.warning("LLM 全部真实通道失败，降级 demo 兜底。原因：%s", "; ".join(errors))
        self.last_meta = {
            "provider": "demo",
            "model": self.demo.model,
            "degraded": bool(errors),
            "scene": scene,
            "latency_ms": latency,
            "errors": errors[-2:],
        }
        return LLMResult(
            text=text, provider="demo", model=self.demo.model, degraded=bool(errors), latency_ms=latency
        )

    async def stream(
        self, messages: list[dict], *, scene: str = "general", temperature: float = 0.3
    ) -> AsyncIterator[LLMResult]:
        """流式：逐通道尝试，成功即切换为文本增量迭代器。"""
        self._check_budget(scene)
        start = time.perf_counter()
        candidates = self._real_candidates()
        errors: list[str] = []
        for cand in candidates:
            try:
                ch = await self._channel(cand)
                chunks: list[str] = []

                async def _gen(ch=ch, chunks=chunks):  # 闭包显式绑定循环变量
                    async for d in ch.stream_chat(messages, temperature=temperature):
                        chunks.append(d)
                        yield d

                first = True
                async for d in _gen():
                    if first:
                        first = False
                    yield LLMResult(text=d, provider=cand.name, model=cand.model, latency_ms=0)
                latency = int((time.perf_counter() - start) * 1000)
                full = "".join(chunks)
                pt, ct = len("".join(str(m.get("content", "")) for m in messages)), len(full)
                cost = self._estimate_cost(cand.name, pt, ct)
                self._log_call(
                    provider=cand.name,
                    model=cand.model,
                    scene=scene,
                    prompt_tokens=pt,
                    completion_tokens=ct,
                    cost_cny=cost,
                    latency_ms=latency,
                    ok=True,
                )
                self.last_meta = {
                    "provider": cand.name,
                    "model": cand.model,
                    "degraded": False,
                    "scene": scene,
                    "latency_ms": latency,
                }
                return
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{cand.name}: {exc}")
                logger.warning("LLM 流式通道 %s 失败，准备切换：%s", cand.name, exc)
        # demo 兜底
        chunks: list[str] = []
        async for d in self.demo.stream_chat(messages, temperature=temperature):
            chunks.append(d)
            yield LLMResult(text=d, provider="demo", model=self.demo.model, degraded=True)
        latency = int((time.perf_counter() - start) * 1000)
        self.last_meta = {
            "provider": "demo",
            "model": self.demo.model,
            "degraded": bool(errors),
            "scene": scene,
            "latency_ms": latency,
            "errors": errors[-2:],
        }
        self._log_call(
            provider="demo",
            model=self.demo.model,
            scene=scene,
            prompt_tokens=0,
            completion_tokens=len("".join(chunks)),
            cost_cny=0.0,
            latency_ms=latency,
            ok=True,
        )

    async def probe(self) -> dict:
        """探测真实通道可用性（供 /api/llm/probe 使用，会真实调用一次模型）。"""
        mode = self.mode
        if mode == "demo":
            return {"ok": False, "mode": "demo", "reason": "未配置任何真实大模型密钥（离线演示模式）"}
        result = await self.complete(
            [{"role": "user", "content": "只回复四个字：连接成功"}], scene="probe", temperature=0.0
        )
        return {
            "ok": result.provider != "demo",
            "mode": self.mode,
            "provider": result.provider,
            "model": result.model,
            "degraded": result.degraded,
            "latency_ms": result.latency_ms,
            "reply": result.text[:60],
            "last_meta": self.last_meta,
        }


gateway = LLMGateway()
