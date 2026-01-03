from typing import Optional
import logging
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy import Column, Integer, String, Boolean, Float, Text, or_, select, inspect

DB_FILE = 'escrow_bot.db'
engine = create_async_engine(f"sqlite+aiosqlite:///{DB_FILE}")
AsyncSessionLocal = sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
Base = declarative_base()


class User(Base):
    __tablename__ = 'users'
    username = Column(String, primary_key=True)
    user_id = Column(Integer, nullable=False)
    wallet = Column(String, nullable=True)
    state = Column(String, nullable=True)


class Deal(Base):
    __tablename__ = 'deals'
    deal_id = Column(Integer, primary_key=True, autoincrement=True)
    seller_id = Column(Integer)
    buyer_id = Column(Integer)
    crypto_amount = Column(Float)
    fiat_amount = Column(String)
    payment_details = Column(Text)
    deposited = Column(Boolean, default=False)
    fiat_confirmed = Column(Boolean, default=False)
    buyer_wallet = Column(String)
    closed = Column(Boolean, default=False)


# Создаём таблицы асинхронно, если они не существуют
async def create_tables():
    try:
        async with engine.begin() as conn:
            # Проверяем существование таблиц перед созданием
            # Обернуть синхронную операцию в run_sync чтобы избежать MissingGreenlet ошибки
            def get_existing_tables(conn):
                inspector = inspect(engine.sync_engine)
                return inspector.get_table_names()
            
            existing_tables = await conn.run_sync(get_existing_tables)
            
            # Создаем только те таблицы, которых еще нет
            tables_to_create = [
                table for table in Base.metadata.tables.keys()
                if table not in existing_tables
            ]
            
            if tables_to_create:
                logging.info(f"Создаются таблицы: {tables_to_create}")
                await conn.run_sync(Base.metadata.create_all)
            else:
                logging.info("Все таблицы уже существуют")
    except Exception as e:
        logging.error(f"Ошибка при создании таблиц: {e}")
        raise

# Все функции теперь async

async def set_user_wallet(username: str, wallet: str) -> None:
    async with AsyncSessionLocal() as session:
        async with session.begin():
            u = await session.get(User, username)
            if u:
                u.wallet = wallet
                await session.commit()

async def upsert_user(username: str, user_id: int) -> None:
    async with AsyncSessionLocal() as session:
        user = await session.get(User, username)
        if user:
            user.user_id = user_id
        else:
            session.add(User(username=username, user_id=user_id))
        await session.commit()

async def find_user_by_username(username: str):
    async with AsyncSessionLocal() as session:
        u = await session.get(User, username)
        if not u:
            return None
        return {"username": u.username, "user_id": u.user_id, "wallet": u.wallet, "state": u.state}

async def create_deal(seller_id: int, buyer_id: int = None, crypto_amount: float = None, fiat_amount: str = None, payment_details: str = None) -> int:
    async with AsyncSessionLocal() as session:
        deal = Deal(seller_id=seller_id, buyer_id=buyer_id, crypto_amount=crypto_amount, fiat_amount=fiat_amount, payment_details=payment_details)
        session.add(deal)
        await session.commit()
        await session.refresh(deal)
        return deal.deal_id

async def get_deal_by_id(deal_id: int):
    async with AsyncSessionLocal() as session:
        d = await session.get(Deal, deal_id)
        if not d:
            return None
        return {
            "deal_id": d.deal_id,
            "seller_id": d.seller_id,
            "buyer_id": d.buyer_id,
            "crypto_amount": d.crypto_amount,
            "fiat_amount": d.fiat_amount,
            "payment_details": d.payment_details,
            "deposited": d.deposited,
            "fiat_confirmed": d.fiat_confirmed,
            "buyer_wallet": d.buyer_wallet,
        }

async def get_deals_for_user(user_id: int):
    async with AsyncSessionLocal() as session:
        stmt = select(Deal).where(or_(Deal.seller_id == user_id, Deal.buyer_id == user_id))
        rows = await session.execute(stmt)
        rows = rows.scalars().all()
        result = []
        for d in rows:
            result.append({
                "deal_id": d.deal_id,
                "seller_id": d.seller_id,
                "buyer_id": d.buyer_id,
                "crypto_amount": d.crypto_amount,
                "fiat_amount": d.fiat_amount,
                "payment_details": d.payment_details,
                "deposited": d.deposited,
                "fiat_confirmed": d.fiat_confirmed,
                "buyer_wallet": d.buyer_wallet,
                "closed": d.closed
            })
        return result

async def update_deal(deal_id: int, **kwargs):
    async with AsyncSessionLocal() as session:
        async with session.begin():
            d = await session.get(Deal, deal_id)
            if d:
                for name, value in kwargs.items():
                    if name in ['seller_id', 'buyer_id', 'crypto_amount', 'fiat_amount', 'payment_details', 'deposited', 'fiat_confirmed', 'buyer_wallet']:
                        setattr(d, name, value)
                await session.commit()

async def update_deal_buyer_wallet(deal_id: int, wallet: str) -> None:
    async with AsyncSessionLocal() as session:
        async with session.begin():
            d = await session.get(Deal, deal_id)
            if d:
                d.buyer_wallet = wallet
                await session.commit()

async def set_deal_deposited(deal_id: int, value: bool = True) -> None:
    async with AsyncSessionLocal() as session:
        async with session.begin():
            d = await session.get(Deal, deal_id)
            if d:
                d.deposited = value
                await session.commit()

async def close_deal(deal_id: int) -> None:
    async with AsyncSessionLocal() as session:
        async with session.begin():
            d = await session.get(Deal, deal_id)
            if d:
                d.closed = True
                await session.commit()

async def delete_deal(deal_id: int) -> None:
    async with AsyncSessionLocal() as session:
        async with session.begin():
            d = await session.get(Deal, deal_id)
            if d:
                await session.delete(d)
                await session.commit()

async def upsert_user_state(username: str, state: str) -> None:
    async with AsyncSessionLocal() as session:
        async with session.begin():
            u = await session.get(User, username)
            if u:
                u.state = state
                await session.commit()

async def get_user_state(username: str) -> Optional[str]:
    async with AsyncSessionLocal() as session:
        u = await session.get(User, username)
        if u:
            return u.state
        return None

async def update_user(username: str, **kwargs) -> None:
    async with AsyncSessionLocal() as session:
        async with session.begin():
            u = await session.get(User, username)
            if u:
                for name, value in kwargs.items():
                    if name in ['user_id', 'wallet', 'state']:
                        setattr(u, name, value)

async def get_deal_id_by_buyer_id(buyer_id: int):
    async with AsyncSessionLocal() as session:
        stmt = select(Deal.deal_id).where(Deal.buyer_id == buyer_id).order_by(Deal.deal_id.desc()).limit(1)
        result = await session.execute(stmt)
        deal_id = result.scalar_one_or_none()
        return deal_id
    
async def get_user_wallet_by_user_id(user_id: int) -> Optional[str]:
    async with AsyncSessionLocal() as session:
        stmt = select(User).where(User.user_id == user_id)
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()
        return user.wallet if user else None
