"""Durable simulation, reading outbox, and controller command journal."""

from datetime import datetime

from sqlalchemy import JSON, Boolean, CheckConstraint, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from packages.db.base import Base


class ThermalSimulation(Base):
    __tablename__ = "thermal_simulations"
    __table_args__ = (
        CheckConstraint("NOT (heater_on AND cooler_on)", name="ck_thermal_exclusive_actuators"),
        CheckConstraint("mode IN ('heating','cooling')", name="ck_thermal_mode"),
    )
    policy: Mapped[str] = mapped_column(String(16), default="scenario", server_default="scenario")
    target_c: Mapped[float] = mapped_column(Float, default=22, server_default="22")
    settings_revision: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    phase: Mapped[str] = mapped_column(String(32), default="idle", server_default="idle")
    settled_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    observation_requested: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    mode: Mapped[str] = mapped_column(String(16), default="heating", server_default="heating")
    cooler_on: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    predecessor_simulation_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    stop_request_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    stop_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    stop_command_sequence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stop_recovery_only: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    simulation_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    building_id: Mapped[str] = mapped_column(String(128), default="thermal-demo")
    zone_id: Mapped[str] = mapped_column(String(128), default="room-1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="active")
    resume_generation: Mapped[int] = mapped_column(Integer, default=0)
    multiplier: Mapped[float] = mapped_column(Float, default=1.0)
    temperature_c: Mapped[float] = mapped_column(Float, default=19.0)
    heater_on: Mapped[bool] = mapped_column(Boolean, default=False)
    step: Mapped[int] = mapped_column(Integer, default=0)
    state_version: Mapped[int] = mapped_column(Integer, default=0)
    simulated_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    reading_sequence: Mapped[int] = mapped_column(Integer, default=0)
    command_sequence: Mapped[int] = mapped_column(Integer, default=0)
    last_processed_reading: Mapped[int] = mapped_column(Integer, default=0)
    controller_started: Mapped[bool] = mapped_column(Boolean, default=False)
    controller_stopped: Mapped[bool] = mapped_column(Boolean, default=False)


class ThermalReading(Base):
    settings_revision: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    cause: Mapped[str] = mapped_column(String(16), default="periodic", server_default="periodic")
    __tablename__ = "thermal_readings"
    cooler_on: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    simulation_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    sequence: Mapped[int] = mapped_column(Integer, primary_key=True)
    step: Mapped[int] = mapped_column(Integer)
    state_version: Mapped[int] = mapped_column(Integer)
    simulated_seconds: Mapped[float] = mapped_column(Float)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    temperature_c: Mapped[float] = mapped_column(Float)
    heater_on: Mapped[bool] = mapped_column(Boolean)
    final: Mapped[bool] = mapped_column(Boolean, default=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    processed_event_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    publication_status: Mapped[str] = mapped_column(String(32), default="pending")
    body: Mapped[str] = mapped_column(Text)
    transport_receipt: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class ThermalCommand(Base):
    settings_revision: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    __tablename__ = "thermal_commands"
    __table_args__ = (
        CheckConstraint(
            "command_type IN ('heating.start','heating.stop','cooling.start','cooling.stop')",
            name="ck_thermal_command_type",
        ),
        CheckConstraint(
            "(command_type != 'heating.start' OR heater_on = true) AND "
            "(command_type != 'heating.stop' OR heater_on = false)",
            name="ck_thermal_heating_projection",
        ),
    )
    cooler_on: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    origin: Mapped[str] = mapped_column(String(16), default="automatic", server_default="automatic")
    stop_request_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    final_reading_sequence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    command_type: Mapped[str] = mapped_column(String(32))
    legacy_wire_allowed: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    simulation_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    sequence: Mapped[int] = mapped_column(Integer, primary_key=True)
    decision_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    applied_state_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reading_sequence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    heater_on: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    transport_message_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="pending")


class ThermalSetting(Base):
    __tablename__ = "thermal_settings"
    operation_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    simulation_id: Mapped[str] = mapped_column(String(36), index=True)
    expected_revision: Mapped[int] = mapped_column(Integer)
    revision: Mapped[int] = mapped_column(Integer)
    mode: Mapped[str] = mapped_column(String(16))
    target_c: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    origin: Mapped[str] = mapped_column(String(16), default="user")
