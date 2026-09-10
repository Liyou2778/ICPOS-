# -*- coding: utf-8 -*-
# Agent 问答验收探针：向正在运行的服务连续提问，覆盖全部意图路由。
# 用法（服务需已启动，默认 http://127.0.0.1:8000）：
#   python docs/agent_qa_probe.py [base_url]
import sys

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"

# (说明, 问题, 期望要点)
BATTERY = [
    ("方案生成·完整需求", "我是矿山生产主管，年产200万吨矿石，工期3年，预算1.5亿元，帮我生成矿山施工方案并推荐设备",
     ["方案", "3 套", "TCO"]),
    ("方案生成·参数缺失(应追问)", "我想买一台挖掘机", ["追问", "补充"]),
    ("施工调度", "矿山运输设备空载率高，帮我调度派车", ["派单", "空载率", "确认"]),
    ("施工调度·故障重调度", "T02 故障了，立即重新调度", ["重调度", "T02"]),
    ("运维·自然语言诊断", "矿卡液压油温高、动作没劲，怀疑漏油，帮我诊断", ["Top", "HYD"]),
    ("运维·故障码查询", "HYD-01 是什么意思？怎么修？", ["液压油温过高", "维修"]),
    ("运维·生成维修工单", "给矿卡 T02 生成一张维修工单", ["WO-", "工程师"]),
    ("运维·预测性维护", "预测一下设备 T04 会不会出问题", ["风险", "HYD-01"]),
    ("运营状态查询", "现在矿上有哪些设备在干活？今天产量和进度怎么样？", ["设备", "进度"]),
    ("知识库问答(RAG)", "露天矿爆破的单耗一般取多少？怎么降低大块率？", ["单耗"]),
    ("知识库问答(RAG)", "排土场的安全车挡高度有什么要求？", ["安全车挡"]),
    ("转人工", "这个问题太复杂了，帮我转人工客服", ["人工"]),
    ("多轮追问(验证上下文)", "把上面那套施工方案里的第二套选型方案的三年TCO报给我", ["TCO", "元", "均衡主力型"]),
    ("防幻觉·知识库外问题", "帮我算一下这台挖掘机的量子纠缠场强是多少？", ["人工", "确认", "直接对应"]),
]

MARK = {"方案生成·完整需求": "需含 ≥3 套方案字样",
        "方案生成·参数缺失(应追问)": "不能瞎编方案，应追问缺失参数",
        "施工调度": "返回派单数/空载率/利用率且提示人工确认",
        "施工调度·故障重调度": "触发重调度且把 T02 排除/标注故障",
        "运维·自然语言诊断": "Top3 诊断且含 HYD 液压类",
        "运维·故障码查询": "解释 HYD-01 含义与维修方案",
        "运维·生成维修工单": "生成 WO- 开头工单（六要素）",
        "运维·预测性维护": "调用预测模型返回风险与故障码",
        "运营状态查询": "返回在役设备/预警/进度数字",
        "知识库问答(RAG)": "检索工艺库返回参数并带来源/标题",
        "转人工": "返回转人工提示",
        "多轮追问(验证上下文)": "能基于上轮会话继续回答（会话内多轮）",
        "防幻觉·知识库外问题": "不编造，明确拒答/转人工/标注需确认"}


def main() -> None:
    c = httpx.Client(base_url=BASE, timeout=40.0)
    sid = c.post("/api/chat/sessions", json={"title": "Agent问答验收"}).json()["session_id"]
    print(f"会话 session_id = {sid}（同一会话连续提问可验证多轮上下文）\n" + "-" * 76)
    for idx, (name, q, keys) in enumerate(BATTERY, 1):
        r = c.post(f"/api/chat/sessions/{sid}/messages", json={"message": q})
        if r.status_code != 200:
            print(f"[{idx:02d}] {name}  -> HTTP {r.status_code} {r.text[:120]}")
            continue
        body = r.json()
        content = body.get("content", "")
        agent = body.get("agent", "?")
        ok = all(k in content for k in keys) if keys else bool(content)
        flag = "✅" if ok else "❌"
        print(f"[{idx:02d}] {flag} {name}  (路由→{agent})")
        print(f"     问: {q}")
        snippet = content.replace("\n", " ")[:200]
        print(f"     答: {snippet}{'…' if len(content) > 200 else ''}")
        if not ok:
            print(f"     期望含: {keys}")
        print("-" * 76)


if __name__ == "__main__":
    main()
