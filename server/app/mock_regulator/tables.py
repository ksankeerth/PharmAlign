"""The mock regulator's own tables. Nothing here refers to a case table."""
from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, Identity, Text
from sqlalchemy.dialects import postgresql as pg
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from ..tables import TS


class MockProposalRow(Base):
    __tablename__ = "mock_regulator_proposal"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    seq: Mapped[int] = mapped_column(BigInteger, Identity(), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    request_hash: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[dict] = mapped_column(pg.JSONB, nullable=False)
    state: Mapped[str] = mapped_column(Text, nullable=False)
    gov_ref: Mapped[Optional[str]] = mapped_column(Text)
    resulting_version: Mapped[Optional[str]] = mapped_column(Text)
    reason: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TS, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(TS, nullable=False)


class MockLicenceRow(Base):
    __tablename__ = "mock_regulator_licence"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    doc: Mapped[dict] = mapped_column(pg.JSONB, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(TS, nullable=False)


class MockEventRow(Base):
    __tablename__ = "mock_regulator_event"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    seq: Mapped[int] = mapped_column(BigInteger, Identity(), nullable=False)
    at: Mapped[datetime] = mapped_column(TS, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    object_id: Mapped[str] = mapped_column(Text, nullable=False)


class MockSettingRow(Base):
    __tablename__ = "mock_regulator_setting"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[dict] = mapped_column(pg.JSONB, nullable=False)
    set_at: Mapped[datetime] = mapped_column(TS, nullable=False)
