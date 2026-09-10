"""设备域：设备型号 / 设备实例 / 备件（表4-3 设备域）。"""

from __future__ import annotations

from datetime import datetime, UTC

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.core.db import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class EquipmentModel(Base):
    """设备型号（设备参数库主表，结构化数据，不参与文本分块——指导书 6.2）。"""

    __tablename__ = "dev_equipment_model"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True)  # 如 XE490
    brand: Mapped[str] = mapped_column(String(32), default="徐工")  # 公开资料采集
    series: Mapped[str] = mapped_column(String(64), default="")
    model_name: Mapped[str] = mapped_column(String(128), default="")
    category: Mapped[str] = mapped_column(String(32), index=True)  # excavator|truck|loader|dozer|...
    category_cn: Mapped[str] = mapped_column(String(16), default="挖掘机")  # 中文类别
    scene: Mapped[str] = mapped_column(String(64), default="mining")  # mining|earthwork|agriculture
    price_cny: Mapped[float] = mapped_column(Float, default=0)  # 购置价（元）
    rated_load_t: Mapped[float] = mapped_column(Float, default=0)  # 矿卡额定载重 t
    bucket_m3: Mapped[float] = mapped_column(Float, default=0)  # 铲斗容量 m3
    power_kw: Mapped[float] = mapped_column(Float, default=0)
    fuel_lh: Mapped[float] = mapped_column(Float, default=0)  # 小时油耗 L/h
    productivity: Mapped[float] = mapped_column(Float, default=0)  # 额定生产率（m3/h 或 t/h）
    maintain_yearly_cny: Mapped[float] = mapped_column(Float, default=0)  # 年均维保成本
    lifespan_years: Mapped[float] = mapped_column(Float, default=10)
    residual_ratio_3y: Mapped[float] = mapped_column(Float, default=0.55)  # 3 年残值率
    spec: Mapped[dict] = mapped_column(JSON, default=dict)  # 扩展参数（作业循环时间、胎型等）
    data_note: Mapped[str] = mapped_column(String(128), default="")  # 示例数据标注
    source: Mapped[str] = mapped_column(String(128), default="徐工官网/产品手册（公开）")


class Device(Base):
    """设备实例（矿山施工现场）。"""

    __tablename__ = "dev_device"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True)  # D001
    model_id: Mapped[int] = mapped_column(ForeignKey("dev_equipment_model.id"))
    name: Mapped[str] = mapped_column(String(64), default="")
    owner_customer_id: Mapped[int | None] = mapped_column(ForeignKey("cust_customer.id"), nullable=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("proj_project.id"), nullable=True)
    work_state: Mapped[str] = mapped_column(
        String(24), default="idle"
    )  # working|idle|loading|hauling|fault|maintenance
    lat: Mapped[float] = mapped_column(Float, default=0.0)
    lng: Mapped[float] = mapped_column(Float, default=0.0)
    cur_load_t: Mapped[float] = mapped_column(Float, default=0.0)
    odo_hours: Mapped[float] = mapped_column(Float, default=0.0)
    purchase_date: Mapped[str] = mapped_column(String(16), default="")
    status_note: Mapped[str] = mapped_column(String(128), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class SparePart(Base):
    """备件（维修工单备件清单）。"""

    __tablename__ = "dev_spare_part"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sku: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    fit_codes: Mapped[str] = mapped_column(String(512), default="")  # 适配故障码，逗号分隔
    price_cny: Mapped[float] = mapped_column(Float, default=0)
    stock: Mapped[int] = mapped_column(Integer, default=0)
