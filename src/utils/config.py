"""
F1 Predictor 2026 - Configuration Module
Loads and validates all environment variables with type safety and defaults.
ML-ready configuration for telemetry normalization and data pipelines.
"""

import os
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv

# Load environment variables from .env file
env_path = Path(__file__).parent.parent.parent / '.env'
load_dotenv(dotenv_path=env_path)


class Config:
    """Central configuration class with validation."""

    # ============ API KEYS & AUTHENTICATION ============
    GEMINI_API_KEY: str = os.getenv('GEMINI_API_KEY', '')
    NEWS_API_KEY: str = os.getenv('NEWS_API_KEY', '')
    OPENF1_ACCESS_TOKEN: str = os.getenv('OPENF1_ACCESS_TOKEN', '')

    # ============ BASE URLs ============
    OPENF1_BASE_URL: str = os.getenv('OPENF1_BASE_URL', 'https://api.openf1.org/v1')
    JOLPICA_BASE_URL: str = os.getenv('JOLPICA_BASE_URL', 'http://api.jolpi.ca/ergast/f1')
    JOLPICA_2026_BASE_URL: str = os.getenv('JOLPICA_2026_BASE_URL', 'http://api.jolpi.ca/ergast/f1/2026')
    OPEN_METEO_BASE_URL: str = os.getenv('OPEN_METEO_BASE_URL', 'https://api.open-meteo.com/v1/forecast')

    # ============ APPLICATION SETTINGS ============
    DEBUG: bool = os.getenv('DEBUG', 'False').lower() == 'true'
    LOG_LEVEL: str = os.getenv('LOG_LEVEL', 'INFO')
    ENVIRONMENT: str = os.getenv('ENVIRONMENT', 'development')

    # ============ DATA PATHS ============
    DATA_RAW_PATH: str = os.getenv('DATA_RAW_PATH', './data/raw')
    DATA_PROCESSED_PATH: str = os.getenv('DATA_PROCESSED_PATH', './data/processed')

    # ============ ML/NORMALIZATION SETTINGS ============
    # All telemetry is normalized to 0-1 range for ML training
    SPEED_MAX_KMH: float = 360.0  # 2026 regulations
    RPM_MAX: float = 15500.0
    THROTTLE_SCALING: float = 100.0  # 0-100% to 0.0-1.0
    BRAKE_SCALING: float = 100.0  # 0-100% to 0.0-1.0

    # ============ REAL-TIME DATA SETTINGS ============
    OPENF1_POLL_INTERVAL_MS: int = 200  # Poll every 200ms
    TELEMETRY_BUFFER_SIZE: int = 1000  # Keep last 1000 telemetry points

    # ============ NEWS & SENTIMENT SETTINGS ============
    GEMINI_MODEL: str = 'gemini-1.5-flash'
    NEWS_API_LANGUAGE: str = 'en'
    NEWS_CACHE_DURATION_HOURS: int = 6

    # ============ CORS & SECURITY ============
    FRONTEND_URL: str = os.getenv('FRONTEND_URL', 'http://localhost:3000')
    ALLOWED_ORIGINS: list = ['http://localhost:3000', 'http://localhost:8000']

    @staticmethod
    def validate() -> None:
        """Validate all required environment variables."""
        errors = []

        if not Config.GEMINI_API_KEY:
            errors.append("GEMINI_API_KEY not found in environment")
        if not Config.NEWS_API_KEY:
            errors.append("NEWS_API_KEY not found in environment")

        if errors:
            error_message = "\n".join(errors)
            raise ValueError(f"Configuration validation failed:\n{error_message}")

        print("✅ Configuration validated successfully")


# Validate on import
Config.validate()

# Export for easy access
GEMINI_API_KEY = Config.GEMINI_API_KEY
NEWS_API_KEY = Config.NEWS_API_KEY
OPENF1_ACCESS_TOKEN = Config.OPENF1_ACCESS_TOKEN
OPENF1_BASE_URL = Config.OPENF1_BASE_URL
JOLPICA_BASE_URL = Config.JOLPICA_BASE_URL
JOLPICA_2026_BASE_URL = Config.JOLPICA_2026_BASE_URL
OPEN_METEO_BASE_URL = Config.OPEN_METEO_BASE_URL
