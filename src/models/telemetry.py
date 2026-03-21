"""
Telemetry Data Models
All values are normalized for ML (0-1 range) where applicable.
"""

from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional


class TelemetryPoint(BaseModel):
    """Single telemetry data point from OpenF1."""
    
    timestamp: datetime = Field(..., description="High-precision timestamp for ML sequencing")
    driver_number: int = Field(..., description="Driver number")
    session_key: int = Field(..., description="Session identifier")
    
    # Location (normalized)
    x: float = Field(0.0, ge=0, le=1, description="Track X position (normalized 0-1)")
    y: float = Field(0.0, ge=0, le=1, description="Track Y position (normalized 0-1)")
    
    # Speed (normalized to 0-1, max 360 km/h)
    speed: float = Field(0.0, ge=0, le=1, description="Speed normalized (0-360kph -> 0-1)")
    speed_raw_kmh: Optional[float] = Field(None, description="Raw speed in km/h for display")
    
    # RPM (normalized)
    rpm: float = Field(0.0, ge=0, le=1, description="RPM normalized (0-15500 -> 0-1)")
    rpm_raw: Optional[int] = Field(None, description="Raw RPM value")
    
    # Gear
    gear: int = Field(0, ge=0, le=8, description="Current gear (0-8)")
    
    # Pedal inputs (normalized 0-1)
    throttle: float = Field(0.0, ge=0, le=1, description="Throttle input (0-100% -> 0-1)")
    brake: float = Field(0.0, ge=0, le=1, description="Brake input (0-100% -> 0-1)")
    
    # Advanced 2026 data
    drs_active: Optional[bool] = Field(None, description="DRS/Active Aero state")
    tire_compound: Optional[str] = Field(None, description="Soft/Medium/Hard")
    fuel_remaining_kg: Optional[float] = Field(None, description="Fuel load")
    
    class Config:
        json_schema_extra = {
            "example": {
                "timestamp": "2026-03-14T15:30:45.123Z",
                "driver_number": 44,
                "session_key": 8765,
                "x": 0.45,
                "y": 0.67,
                "speed": 0.92,
                "speed_raw_kmh": 331.2,
                "rpm": 0.85,
                "rpm_raw": 13155,
                "gear": 6,
                "throttle": 1.0,
                "brake": 0.0,
                "drs_active": True,
                "tire_compound": "Soft",
                "fuel_remaining_kg": 42.5
            }
        }


class LapData(BaseModel):
    """Complete lap telemetry data."""
    
    driver_number: int
    lap_number: int
    lap_start_time: datetime
    lap_duration_ms: float = Field(..., description="Lap time in milliseconds")
    telemetry_points: list[TelemetryPoint] = Field(default_factory=list)
    
    # Sector times
    sector_1_ms: Optional[float] = Field(None)
    sector_2_ms: Optional[float] = Field(None)
    sector_3_ms: Optional[float] = Field(None)
    
    # Deltas
    delta_to_best_ms: Optional[float] = Field(None)
    delta_to_pole_ms: Optional[float] = Field(None)

    class Config:
        json_schema_extra = {
            "example": {
                "driver_number": 44,
                "lap_number": 15,
                "lap_start_time": "2026-03-14T15:30:00Z",
                "lap_duration_ms": 85432.5,
                "sector_1_ms": 28123.0,
                "sector_2_ms": 29456.0,
                "sector_3_ms": 27853.5
            }
        }


class DriverPosition(BaseModel):
    """Current driver position on track."""
    
    driver_number: int
    driver_name: str
    team: str
    position: int
    
    # Current location
    x: float = Field(0.0, ge=0, le=1)
    y: float = Field(0.0, ge=0, le=1)
    
    # Gap to leader
    gap_to_leader_ms: Optional[float] = Field(None)
    interval_ms: Optional[float] = Field(None)  # Gap to car ahead
    
    # Last lap info
    last_lap_time_ms: Optional[float] = Field(None)
    sector_1_ms: Optional[float] = Field(None)
    sector_2_ms: Optional[float] = Field(None)
    sector_3_ms: Optional[float] = Field(None)
    
    # Status
    pit_stop_count: int = 0
    tire_compound: Optional[str] = Field(None)
    drs_available: bool = False


class ModelInput(BaseModel):
    """Input tensor for ML models - current telemetry state."""
    
    timestamp: datetime
    # Flattened telemetry array for LSTM/RNN ingestion
    features: list[float] = Field(..., description="Normalized telemetry features (0-1)")
    feature_names: list[str] = Field(default_factory=list)
    driver_number: int
    lap_number: int
    session_key: int
    
    class Config:
        json_schema_extra = {
            "example": {
                "timestamp": "2026-03-14T15:30:45.123Z",
                "features": [0.92, 0.85, 0.5, 1.0, 0.0, 0.45, 0.67],
                "feature_names": ["speed", "rpm", "throttle", "brake", "gear", "x", "y"],
                "driver_number": 44,
                "lap_number": 15,
                "session_key": 8765
            }
        }
