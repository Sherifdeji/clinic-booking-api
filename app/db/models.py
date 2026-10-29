from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Slot(Base):
    __tablename__ = "slots"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    provider: Mapped[str] = mapped_column(String(200), nullable=False)
    start_time: Mapped[datetime] = mapped_column(
        "start", DateTime(timezone=True), nullable=False
    )
    end_time: Mapped[datetime] = mapped_column(
        "end", DateTime(timezone=True), nullable=False
    )

    bookings: Mapped[list["Booking"]] = relationship(back_populates="slot")


class Booking(Base):
    __tablename__ = "bookings"
    __table_args__ = (
        Index("ix_bookings_slot_status", "slot_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    slot_id: Mapped[str] = mapped_column(
        ForeignKey("slots.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    slot: Mapped[Slot] = relationship(back_populates="bookings")
