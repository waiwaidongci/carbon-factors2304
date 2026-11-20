from sqlalchemy import Boolean, Column, Float, Integer, JSON, Numeric, String, Text, UniqueConstraint
from src.config.database import Base


class Department(Base):
    __tablename__ = 'departments'
    id = Column(Integer, primary_key=True)
    name = Column(String(100), unique=True, nullable=False)
    retrofit = Column(Boolean, default=False, nullable=False)
    created = Column(Float)


class DepartmentOutput(Base):
    __tablename__ = 'department_outputs'
    __table_args__ = (UniqueConstraint('department', 'period', name='uq_department_output_period'),)
    id = Column(Integer, primary_key=True)
    department = Column(String(100), nullable=False)
    period = Column(String(40), nullable=False)
    output = Column(Numeric(24, 6), nullable=False)
    created = Column(Float)


class Evaluation(Base):
    __tablename__ = 'carbon_evaluations'
    id = Column(Integer, primary_key=True)
    name = Column(String(120), nullable=False)
    earlier_run_id = Column(Integer, nullable=False)
    later_run_id = Column(Integer, nullable=False)
    gap_limit = Column(Numeric(24, 6), nullable=False)
    status = Column(String(20), default='queued')
    basis = Column(JSON)
    checksum = Column(String(64))
    request_key = Column(String(160), unique=True, nullable=False)
    payload_hash = Column(String(64))
    result = Column(JSON)
    error = Column(Text)
    created = Column(Float)
    published = Column(Boolean, default=False, nullable=False)
    published_at = Column(Float)
