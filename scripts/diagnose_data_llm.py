"""诊断脚本：训练数据格式体检 + LLM 实际调用审计 + DeepSeek 直连验证。"""

from __future__ import annotations

import json
import os

DATA_PATHS = [
    r"C:\Users\Liyou\.dsh\attachments\v1\files\64\6467ba6f500c0312f3ec5b7e0cb7d609b6f47b33c670579ca97ecabfbef06e77\train(1).jsonl",
    r"C:\Users\Liyou\.dsh\attachments\v1\files\78\78ca260f6b1cdfcf53fed1ad9632080f969d7c7081a4a2d168f31c4a160b6694\test(1).jsonl",
]


def inspect_data() -> None:
    for p in DATA_PATHS:
        print("=" * 78)
        print("FILE:", p)
        if not os.path.exists(p):
            print("  缺失")
            continue
        print(f"  size: {os.path.getsize(p) / 1e6:.1f} MB")
        n = 0
        keys: object = None
        samples: list[str] = []
        bad = 0
        with open(p, encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f):
                line = line.strip()
                if not line:
                    continue
                n += 1
                if i < 2:
                    try:
                        obj = json.loads(line)
                        keys = list(obj.keys()) if isinstance(obj, dict) else type(obj).__name__
                        samples.append(json.dumps(obj, ensure_ascii=False)[:700])
                    except Exception as exc:  # noqa: BLE001
                        samples.append(f"PARSE_ERR: {exc}")
                elif n % 20000 == 0:
                    try:
                        json.loads(line)
                    except Exception:  # noqa: BLE001
                        bad += 1
        print(f"  lines: {n}, top-level keys: {keys}, 抽样解析错误: {bad}")
        for s in samples:
            print("  sample:", s)


def audit_llm_calls() -> None:
    print("=" * 78)
    try:
        from backend.app.core.db import SessionLocal
        from backend.app.models import LLMCallLog

        db = SessionLocal()
        total = db.query(LLMCallLog).count()
        rows = db.query(LLMCallLog).order_by(LLMCallLog.id.desc()).limit(8).all()
        print(f"LLM 调用日志总数: {total}")
        for r in rows:
            print(
                f"  #{r.id} provider={r.provider} model={r.model} scene={r.scene} ok={r.ok} "
                f"in={r.prompt_tokens} out={r.completion_tokens} cost={r.cost_cny:.6f} at={r.created_at}"
            )
        db.close()
    except Exception as exc:  # noqa: BLE001
        print("  读取日志失败:", exc)


def probe_deepseek() -> None:
    print("=" * 78)
    try:
        import httpx

        from backend.app.core.config import settings

        key = settings.deepseek_api_key
        print(
            f"DeepSeek Key 已配置: {bool(key)} (长度 {len(key)})  base={settings.llm_base_url} model={settings.llm_model}"
        )
        if not key:
            return
        r = httpx.post(
            settings.llm_base_url.rstrip("/") + "/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={
                "model": settings.llm_model,
                "messages": [{"role": "user", "content": "只回复四个字：连接成功"}],
                "max_tokens": 32,
            },
            timeout=40,
        )
        print("HTTP:", r.status_code)
        print("BODY:", r.text[:500])
    except Exception as exc:  # noqa: BLE001
        print("  直连失败:", type(exc).__name__, exc)


if __name__ == "__main__":
    inspect_data()
    audit_llm_calls()
    probe_deepseek()
    print("=" * 78)
    print("诊断结束")
