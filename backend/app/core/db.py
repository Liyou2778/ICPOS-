"""数据库引擎与会话（SQLite/SQLAlchemy 2.x，MVP 单库）。"""

from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from backend.app.core.config import settings


class Base(DeclarativeBase):
    pass


engine = create_engine(
    settings.db_path,
    connect_args={"check_same_thread": False},
    echo=False,
)


@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record):  # noqa: ANN001
    try:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()
    except Exception:  # noqa: BLE001
        pass


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    """FastAPI 依赖：请求级数据库会话。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _literal(value: object) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


def migrate_schema() -> list[str]:
    """轻量平滑迁移：为既有表补齐新增列（SQLite 不支持复杂 DDL 迁移，故按列增补）。

    返回本次执行的迁移语句列表（便于日志与测试断言）。现有行取列默认值（无默认则 NULL，
    业务代码读取时做兜底）。新表由 create_all 负责创建。
    """
    from sqlalchemy import inspect as sa_inspect
    from sqlalchemy import text as sa_text

    applied: list[str] = []
    inspector = sa_inspect(engine)
    existing_tables = set(inspector.get_table_names())
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue
            have = {c["name"] for c in inspector.get_columns(table.name)}
            for col in table.columns:
                if col.name in have:
                    continue
                ddl_type = col.type.compile(engine.dialect)
                stmt = f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {ddl_type}'
                default = getattr(col, "default", None)
                if default is not None and getattr(default, "is_scalar", False):
                    try:
                        stmt += f" DEFAULT {_literal(default.arg)}"
                    except Exception:  # noqa: BLE001  复杂默认值（如函数）不写 DDL
                        pass
                conn.execute(sa_text(stmt))
                applied.append(stmt)
    return applied


def init_db() -> None:
    """建表 + 平滑迁移 + 演示账号种子。"""
    import backend.app.models as models  # noqa: F401  确保模型注册

    Base.metadata.create_all(bind=engine)
    migrate_schema()
