from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import Column, BigInteger, Boolean, DateTime, func, String, Integer
from config import app_settings
import aiosqlite

DATABASE_URL = app_settings.DATABASE_URL

engine = create_async_engine(DATABASE_URL, echo=False)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)

class Base(DeclarativeBase): pass

class ReminderSetting(Base):
    __tablename__ = "reminder_settings"
    chat_id     = Column(BigInteger, primary_key=True)
    enabled     = Column(Boolean,  nullable=False, default=False)
    asked       = Column(Boolean,  nullable=False, default=False)
    last_msg_id = Column(BigInteger, nullable=True)
    created_at  = Column(DateTime(timezone=True), server_default=func.now())
    updated_at  = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

class AdminSetting(Base):
    __tablename__ = "admin_settings"
    admin_id        = Column(BigInteger, primary_key=True)
    selected_server = Column(String, nullable=True)

class Broadcast(Base):
    __tablename__ = "broadcast"
    id = Column(Integer, primary_key=True, autoincrement=True)
    text = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class BroadcastErrorLog(Base):
    __tablename__ = "broadcast_error_log"
    id = Column(Integer, primary_key=True, autoincrement=True)
    bc_id = Column(Integer, nullable=False)
    chat_id = Column(BigInteger, nullable=False)
    reason = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

async def init_models():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

async def get_selected(admin_id: int) -> str | None:
    async with SessionLocal() as s:
        row = await s.get(AdminSetting, admin_id)
        return row.selected_server if row else None

async def set_selected(admin_id: int, sid: str):
    async with SessionLocal() as s:
        row = await s.get(AdminSetting, admin_id) or AdminSetting(admin_id=admin_id)
        row.selected_server = sid
        s.add(row)
        await s.commit() 