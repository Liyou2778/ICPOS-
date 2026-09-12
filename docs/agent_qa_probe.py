# -*- coding: utf-8 -*-
# Agent 问答验收探针：向正在运行的服务连续提问，覆盖全部意图路由。
# 断言以「结构化字段（agent / need_more / transfer）+ 关键要点」为准，
# 兼容两种模式：离线演示（确定性模板）与真实大模型（自然行文，可能改写措辞）。
# 用法（服务需已启动，默认 http://127.0.0.1:8000）：
#   python docs/agent_qa_probe.py [base_url]

import sys

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"

# name, 问题, 期望 agent, 期望 need_more, 期望 transfer(None=不校验), 关键要点(命中任一即可), 是否新建会话
BATTERY = [
    ("方案生成·完整需求", "我是矿山生产主管，年产200万吨矿石，工期3年，预算1.5亿元，帮我生成矿山施工方案并推荐设备",
     "solution", False, False, ["方案", "选型"], False),
    ("方案生成·参数缺失(阻塞追问)", "帮我做个矿山施工方案，年产200万吨",
     "requirement", True, False, ["预算", "补录", "缺"], True),  # 独立会话，验证"无历史槽位时阻塞"
    ("施工调度", "矿山运输设备空载率高，帮我调度派车",
     "dispatch", False, False, ["派单", "空载"], False),
    ("施工调度·故障重调度", "T02 故障了，立即重新调度",
     "dispatch", False, False, ["T02", "重调度", "故障"], False),
    ("运维·自然语言诊断", "矿卡液压油温高、动作没劲，怀疑漏油，帮我诊断",
     "maintenance", False, False, ["HYD", "液压", "诊断"], False),
    ("运维·故障码查询", "HYD-01 是什么意思？怎么修？",
     "maintenance", False, False, ["HYD-01", "液压油温过高", "维修"], False),
    ("运维·生成维修工单", "给矿卡 T02 生成一张维修工单",
     "maintenance", False, False, ["WO-", "工单"], False),
    ("运维·预测性维护", "预测一下设备 T04 会不会出问题",
     "maintenance", False, False, ["T04", "风险", "HYD"], False),
    ("运营状态查询", "现在矿上有哪些设备在干活？今天产量和进度怎么样？",
     "status", False, False, ["设备", "进度"], False),
    ("知识库问答(RAG)·爆破单耗", "露天矿爆破的单耗一般取多少？怎么降低大块率？",
     "kb_qa", False, False, ["单耗", "爆破"], False),
    ("知识库问答(RAG)·安全车挡", "排土场的安全车挡高度有什么要求？",
     "kb_qa", False, False, ["安全车挡", "排土"], False),
    ("转人工", "这个问题太复杂了，帮我转人工客服",
     "human", False, True, ["人工"], False),
    ("多轮追问(验证上下文)", "把上面那套施工方案里的第二套选型方案的三年TCO报给我",
     None, False, False, ["TCO", "元", "方案"], False),  # agent 允许 solution / solution_followup
    ("防幻觉·知识库外问题", "帮我算一下这台挖掘机的量子纠缠场强是多少？",
     "kb_qa", False, None, ["人工", "核实", "参考", "不编造", "对应"], False),  # 拒答或最接近参考+转人工均可
]


def main() -> int:
    c = httpx.Client(base_url=BASE, timeout=90)
    main_sid = c.post("/api/chat/sessions", json={"title": "Agent问答验收"}).json()["session_id"]
    sid = main_sid
    print(f"主会话 session_id = {main_sid}（多轮/上下文类用例都在此会话；标注项使用独立会话后切回）\n" + "-" * 78)
    passed = 0
    for idx, (name, q, agent, need_more, transfer, keys, fresh) in enumerate(BATTERY, 1):
        if fresh:
            sid = c.post("/api/chat/sessions", json={"title": f"Agent问答验收-{name}"}).json()["session_id"]
        else:
            sid = main_sid
        r = c.post(f"/api/chat/sessions/{sid}/messages", json={"message": q})
        if r.status_code != 200:
            print(f"[{idx:02d}] ❌ {name} -> HTTP {r.status_code} {r.text[:120]}")
            continue
        body = r.json()
        got_agent = body.get("agent", "?")
        got_need = bool(body.get("need_more"))
        got_transfer = bool(body.get("transfer"))
        content = body.get("content", "")
        agent_ok = (agent is None and got_agent in ("solution", "solution_followup")) or got_agent == agent
        need_ok = got_need == need_more
        transfer_ok = transfer is None or got_transfer == transfer or (transfer and got_transfer)
        key_ok = (not keys) or any(k in content for k in keys)
        ok = agent_ok and need_ok and transfer_ok and key_ok and len(content) > 0
        passed += int(ok)
        print(f"[{idx:02d}] {'✅' if ok else '❌'} {name}  (路由→{got_agent}, need_more={got_need}, transfer={got_transfer})")
        print(f"     问: {q}")
        print(f"     答: {content.replace(chr(10), ' ')[:150]}…")
        if not ok:
            print(f"     判定: agent_ok={agent_ok} need_ok={need_ok} transfer_ok={transfer_ok} key_ok={key_ok}")
        print("-" * 78)
    print(f"结果：{passed}/{len(BATTERY)} 项通过")
    return 0 if passed == len(BATTERY) else 1


if __name__ == "__main__":
    raise SystemExit(main())
