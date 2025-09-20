from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.models.base import Base

_settings = get_settings()
_database_url = _settings.database_url
_url = make_url(_database_url)

if _url.drivername.startswith("sqlite") and _url.database:
    db_path = Path(_url.database).expanduser()
    if db_path.parent:
        db_path.parent.mkdir(parents=True, exist_ok=True)
    # Rebuild URL so SQLAlchemy can handle relative paths nicely
    _database_url = f"sqlite:///{db_path}"

_engine = create_engine(
    _database_url,
    echo=False,
    connect_args={"check_same_thread": False} if _url.drivername.startswith("sqlite") else {},
)
SessionLocal = sessionmaker(bind=_engine, autocommit=False, autoflush=False, expire_on_commit=False)


def init_db() -> None:
    Base.metadata.create_all(bind=_engine)


@contextmanager
def db_session() -> Generator[Session, None, None]:
    session: Session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Generator[Session, None, None]:
    with db_session() as session:
        yield session
