"""客户域：客户 / 组织 / 合同（表4-3 客户域）。"""

from __future__ import annotations

from datetime import datetime, UTC

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.core.db import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class Customer(Base):
    """客户（制造商/施工企业/租赁运营商）。"""

    __tablename__ = "cust_customer"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    industry: Mapped[str] = mapped_column(String(64), default="mining")
    contact: Mapped[str] = mapped_column(String(64), default="")
    service_level: Mapped[str] = mapped_column(String(16), default="standard")
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Contract(Base):
    """合同（设备清单、服务等级）。"""

    __tablename__ = "cust_contract"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("cust_customer.id"))
    name: Mapped[str] = mapped_column(String(128), default="")
    amount_cny: Mapped[float] = mapped_column(Float, default=0)
    sign_date: Mapped[str] = mapped_column(String(16), default="")
    status: Mapped[str] = mapped_column(String(16), default="active")
