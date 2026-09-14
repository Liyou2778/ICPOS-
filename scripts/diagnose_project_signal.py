"""项目工期/成本语料「可学习性」诊断。

目的：在决定模型是否上线前，先回答"这批数据里到底有没有可学习的信号"。
输出：偏差分布、分组均值差异（工序/阶段/循环）、与特征的相关系数、噪声水平估计。
"""

from __future__ import annotations

import json

import pandas as pd

from backend.app.core.db import SessionLocal
from scripts.train_project_models import build_task_dataset, load_frames


def main() -> int:
    tasks, costs = load_frames()
    X, y, projects = build_task_dataset(tasks)
    print(f"已完工可标注任务：{len(X)} / 全部任务 {len(tasks)}；特征 {X.shape[1]} 维")

    d = y["delay_days"]
    print("\n[1] 偏差分布（天）")
    print(d.describe(percentiles=[0.1, 0.25, 0.5, 0.75, 0.9, 0.95]).round(3).to_string())
    print("分位数：", {q: round(float(d.quantile(q)), 2) for q in (0.5, 0.8, 0.9, 0.95)})
    print("类别分布：", y["delay_class"].value_counts().to_dict())

    raw = tasks.copy()
    raw["plan_end_d"] = pd.to_datetime(raw["plan_end"], errors="coerce")
    raw["actual_end_d"] = pd.to_datetime(raw["actual_end"], errors="coerce")
    raw = raw.dropna(subset=["plan_end_d", "actual_end_d"])
    raw["delay"] = (raw["actual_end_d"] - raw["plan_end_d"]).dt.days
    raw["plan_days"] = (raw["plan_end_d"] - pd.to_datetime(raw["plan_start"], errors="coerce")).dt.days

    print("\n[2] 分组均值（看是否存在系统性差异）")
    for col in ["process", "phase", "workload_unit", "status"]:
        g = raw.groupby(col)["delay"].agg(["count", "mean", "std"]).round(3)
        spread = float(g["mean"].max() - g["mean"].min())
        print(f"  {col}: 组均值极差 {spread:.3f} 天")
        print(g.to_string())

    print("\n[3] 与计划特征的相关系数（Pearson）")
    num = raw[["delay", "plan_days", "workload", "cycle"]].apply(pd.to_numeric, errors="coerce")
    print(num.corr()["delay"].round(4).to_string())

    print("\n[4] 组内/组间方差分解（工序）")
    overall = raw["delay"].var(ddof=1)
    within = raw.groupby("process")["delay"].var(ddof=1).mean()
    between = raw.groupby("process")["delay"].mean().var(ddof=1)
    eta2 = between / (between + within) if (between + within) else float("nan")
    print(f"  总方差 {overall:.3f} / 组内均值方差 {within:.3f} / 组间方差 {between:.3f} / eta² {eta2:.4f}")

    print("\n[5] 项目级偏差（项目间差异是否显著）")
    g = raw.groupby(raw["project_id"])["delay"].agg(["count", "mean", "std"]).round(3)
    print(g.sort_values("mean").to_string())
    print(
        f"  项目均值极差 {float(g['mean'].max() - g['mean'].min()):.3f} 天；"
        f"项目均值标准差 {float(g['mean'].std(ddof=1)):.3f}"
    )

    costs_df = costs.copy()
    tot = costs_df.groupby("project_code")["amount"].sum()
    piv = costs_df.pivot_table(
        index="project_code", columns="cost_type", values="amount", aggfunc="sum"
    ).fillna(0)
    shares = piv.div(piv.sum(axis=1), axis=0)
    print("\n[6] 成本构成占比（项目级）")
    print((shares * 100).round(2).to_string())
    print("占比标准差（百分点）：", (shares.std(ddof=1) * 100).round(2).to_dict())

    m = json.loads(open("data/corpus/project_manifest.json", encoding="utf-8").read_text(encoding="utf-8"))
    print(
        "\n[7] 划分一致性：train/test 项目数",
        len(m["train_projects"]),
        len(m["test_projects"]),
        "交叉",
        m["overlap_projects"],
    )
    print("成本台账总额（万元）:", round(float(tot.sum()) / 1e4, 2))
    db = SessionLocal()
    try:
        src = pd.read_sql("SELECT data_type, COUNT(*) n FROM proj_task GROUP BY data_type", db.bind)
        print("任务 data_type:", src.to_dict("records"))
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
