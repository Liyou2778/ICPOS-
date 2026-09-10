"""pytest 共享夹具：临时 SQLite + 知识库装载 + 演示数据就绪检测。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from backend.app.core.db import Base, SessionLocal  # noqa: E402
import backend.app.models  # noqa: E402,F401  确保模型注册


@pytest.fixture(scope="session")
def engine(tmp_path_factory):
    dbfile = tmp_path_factory.mktemp("db") / "test.db"
    e = create_engine(f"sqlite:///{dbfile.as_posix()}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(e)
    return e


@pytest.fixture
def db(engine):
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    s = Session()
    yield s
    s.rollback()
    s.close()


@pytest.fixture(scope="session")
def kb_csv() -> Path:
    return REPO / "data" / "knowledge" / "equipment_models.csv"


@pytest.fixture(scope="session")
def fault_csv() -> Path:
    return REPO / "data" / "knowledge" / "maintenance_knowledge.csv"


def _demo_ready() -> bool:
    """演示库（dev DB + 向量库 + 模型产物）是否就绪。"""
    try:
        s = SessionLocal()
        from backend.app.models import Device, KnowledgeEntry

        dev_ok = s.query(Device).count() > 0
        kb_ok = s.query(KnowledgeEntry).count() > 0
        s.close()
        from backend.app.services.predictive import models_ready

        vec_ok = kb_ok
        return dev_ok and kb_ok and vec_ok and models_ready()
    except Exception:  # noqa: BLE001
        return False


demo_ready = _demo_ready()

requires_demo = pytest.mark.skipif(
    not demo_ready,
    reason="需先执行演示初始化：scripts.build_kb -> data.simulator.gen -> "
    "scripts.load_demo -> scripts.train_models",
)
