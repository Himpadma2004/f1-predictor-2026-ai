"""
OpenF1 Live Telemetry Client
Handles real-time websocket streaming at 3.7Hz from OpenF1 API.
Normalizes data for ML training (0-1 range).
"""

import asyncio
import aiohttp
import logging
from datetime import datetime
from typing import Optional, Callable, Any
from src.utils.config import Config
from src.models.telemetry import TelemetryPoint, DriverPosition

logger = logging.getLogger(__name__)


class OpenF1Client:
    """Client for OpenF1 real-time telemetry."""
    
    def __init__(self):
        self.base_url = Config.OPENF1_BASE_URL
        self.session: Optional[aiohttp.ClientSession] = None
        self.telemetry_buffer: list[TelemetryPoint] = []
        self.callbacks: list[Callable[[TelemetryPoint], Any]] = []
        
    async def start(self) -> None:
        """Initialize HTTP session."""
        self.session = aiohttp.ClientSession()
        logger.info(f"OpenF1 client initialized with base URL: {self.base_url}")
        
    async def stop(self) -> None:
        """Close HTTP session."""
        if self.session:
            await self.session.close()
            
    def register_callback(self, callback: Callable[[TelemetryPoint], Any]) -> None:
        """Register callback for new telemetry points."""
        self.callbacks.append(callback)
        
    async def poll_live_drivers(self, session_key: int) -> list[dict]:
        """Poll current driver positions for a session."""
        try:
            url = f"{self.base_url}/drivers?session_key={session_key}"
            async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data if isinstance(data, list) else []
                else:
                    logger.warning(f"OpenF1 drivers poll returned {resp.status}")
                    return []
        except Exception as e:
            logger.error(f"Failed to poll OpenF1 drivers: {e}")
            return []
            
    async def poll_telemetry(self, session_key: int, driver_number: int) -> Optional[TelemetryPoint]:
        """Poll telemetry for a specific driver in a session."""
        try:
            # OpenF1 provides telemetry as time-series data
            url = f"{self.base_url}/telemetry?session_key={session_key}&driver_number={driver_number}"
            async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data and isinstance(data, list) and len(data) > 0:
                        # Get latest telemetry point
                        latest = data[-1]
                        point = self._normalize_telemetry(latest, session_key, driver_number)
                        
                        # Add to buffer
                        self.telemetry_buffer.append(point)
                        if len(self.telemetry_buffer) > Config.TELEMETRY_BUFFER_SIZE:
                            self.telemetry_buffer.pop(0)
                            
                        # Trigger callbacks
                        for callback in self.callbacks:
                            try:
                                await callback(point) if asyncio.iscoroutinefunction(callback) else callback(point)
                            except Exception as e:
                                logger.error(f"Callback error: {e}")
                                
                        return point
            return None
        except Exception as e:
            logger.error(f"Failed to poll OpenF1 telemetry: {e}")
            return None
            
    def _normalize_telemetry(self, raw_data: dict, session_key: int, driver_number: int) -> TelemetryPoint:
        """
        Normalize OpenF1 raw telemetry to ML-safe Pydantic model.
        All numeric values scaled to 0-1 range.
        """
        # Raw values
        speed_kmh = raw_data.get('speed', 0)
        rpm = raw_data.get('rpm', 0)
        throttle_pct = raw_data.get('throttle', 0)
        brake_pct = raw_data.get('brake', 0)
        
        # Normalize to 0-1
        speed_norm = min(float(speed_kmh) / Config.SPEED_MAX_KMH, 1.0) if speed_kmh else 0.0
        rpm_norm = min(float(rpm) / Config.RPM_MAX, 1.0) if rpm else 0.0
        throttle_norm = float(throttle_pct) / Config.THROTTLE_SCALING if throttle_pct else 0.0
        brake_norm = float(brake_pct) / Config.BRAKE_SCALING if brake_pct else 0.0
        
        # Position (0-1 normalized for track coordinates)
        x_raw = raw_data.get('x', 0)
        y_raw = raw_data.get('y', 0)
        # Assuming OpenF1 provides absolute coordinates; normalize to track dimensions
        x_norm = float(x_raw) / 4000.0 if x_raw else 0.0  # Typical track width ~4000m
        y_norm = float(y_raw) / 5500.0 if y_raw else 0.0  # Typical track length ~5500m
        x_norm = min(max(x_norm, 0.0), 1.0)
        y_norm = min(max(y_norm, 0.0), 1.0)
        
        return TelemetryPoint(
            timestamp=datetime.utcnow(),
            driver_number=driver_number,
            session_key=session_key,
            x=x_norm,
            y=y_norm,
            speed=speed_norm,
            speed_raw_kmh=float(speed_kmh) if speed_kmh else None,
            rpm=rpm_norm,
            rpm_raw=int(rpm) if rpm else None,
            gear=int(raw_data.get('gear', 0)),
            throttle=throttle_norm,
            brake=brake_norm,
            drs_active=raw_data.get('drs_active'),
            tire_compound=raw_data.get('tire_compound'),
            fuel_remaining_kg=raw_data.get('fuel_remaining_kg')
        )
        
    async def stream_telemetry(
        self, 
        session_key: int, 
        driver_numbers: list[int],
        poll_interval_ms: int = None
    ) -> None:
        """
        Continuously stream telemetry for multiple drivers.
        Default: 200ms polling (3.7Hz as specified).
        """
        poll_interval = (poll_interval_ms or Config.OPENF1_POLL_INTERVAL_MS) / 1000.0
        
        logger.info(f"Starting telemetry stream for drivers {driver_numbers} at {poll_interval*1000}ms intervals")
        
        try:
            while True:
                for driver_number in driver_numbers:
                    await self.poll_telemetry(session_key, driver_number)
                await asyncio.sleep(poll_interval)
        except asyncio.CancelledError:
            logger.info("Telemetry stream cancelled")
            raise
        except Exception as e:
            logger.error(f"Telemetry stream error: {e}")
            await asyncio.sleep(poll_interval)
            
    async def get_session_drivers(self, session_key: int) -> list[DriverPosition]:
        """Get current positions of all drivers in session."""
        drivers_data = await self.poll_live_drivers(session_key)
        
        positions = []
        for driver in drivers_data:
            try:
                position = DriverPosition(
                    driver_number=driver.get('driver_number'),
                    driver_name=driver.get('driver_name', 'Unknown'),
                    team=driver.get('team_name', 'Unknown'),
                    position=driver.get('position', 0),
                    x=min(max(float(driver.get('x', 0)) / 4000.0, 0.0), 1.0),
                    y=min(max(float(driver.get('y', 0)) / 5500.0, 0.0), 1.0),
                    gap_to_leader_ms=driver.get('gap_to_leader'),
                    interval_ms=driver.get('interval'),
                    pit_stop_count=driver.get('pit_stops', 0),
                    tire_compound=driver.get('tire_compound')
                )
                positions.append(position)
            except Exception as e:
                logger.warning(f"Failed to parse driver position: {e}")
                
        return positions
