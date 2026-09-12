"""验证真实大模型是否被实际使用：llm/status → llm/probe → 对话 SSE 的 provider 归属。"""

from __future__ import annotations

import json
import sys

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8004"


def main() -> int:
    c = httpx.Client(base_url=BASE, timeout=90)
    st = c.get("/api/llm/status").json()
    print(
        "[llm/status] mode =",
        st["mode"],
        "| deepseek_key =",
        st["has_deepseek_key"],
        "| dashscope_key =",
        st["has_dashscope_key"],
    )
    print("[llm/status] recent_calls:", json.dumps(st["recent_calls"][:3], ensure_ascii=False))

    pr = c.post("/api/llm/probe", timeout=90).json()
    print("[llm/probe ]", json.dumps(pr, ensure_ascii=False)[:300])

    sid = c.post("/api/chat/sessions", json={"title": "LLM验证"}).json()["session_id"]
    events: dict[str, str] = {}
    deltas: list[str] = []
    with c.stream(
        "POST",
        f"/api/chat/sessions/{sid}/messages/stream",
        json={"message": "用一句话说明：设备空载率下降 15% 对矿山意味着什么？"},
    ) as resp:
        for line in resp.iter_lines():
            if not line:
                continue
            if line.startswith("event:"):
                cur = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                payload = line.split(":", 1)[1].strip()
                if cur == "delta":
                    try:
                        deltas.append(json.loads(payload).get("text", ""))
                    except Exception:  # noqa: BLE001
                        pass
                elif cur == "done":
                    events["done"] = payload
    done = json.loads(events.get("done", "{}"))
    text = "".join(deltas)
    print(f"[chat SSE  ] done.provider = {done.get('provider')!r} degraded = {done.get('degraded')}")
    print(f"[chat SSE  ] 回复前 120 字：{text[:120]!r}")
    ok = done.get("provider") not in (None, "", "demo")
    print("=" * 60)
    print("结论：", "已实际使用真实大模型 ✅" if ok else "未使用真实大模型（仍为离线兜底）❌")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
