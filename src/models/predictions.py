"""
News & Prediction Data Models
For Sentinel AI news feed and predictive analytics.
"""

from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional


class NewsArticle(BaseModel):
    """News article with Gemini-scored performance impact."""
    
    article_id: str = Field(..., description="Unique article identifier")
    title: str
    description: Optional[str] = None
    content: Optional[str] = None
    url: str
    image_url: Optional[str] = None
    
    # Source & timing
    source_name: str
    published_at: datetime
    fetched_at: datetime = Field(default_factory=datetime.utcnow)
    
    # Gemini AI Analysis
    teams_mentioned: list[str] = Field(default_factory=list, description="Teams found in article")
    drivers_mentioned: list[str] = Field(default_factory=list, description="Drivers found")
    
    # Performance impact score: -1.0 (very negative) to +1.0 (very positive)
    performance_impact: float = Field(
        0.0,
        ge=-1.0,
        le=1.0,
        description="Team performance impact (-1.0 to +1.0)"
    )
    
    # Sentiment breakdown
    sentiment_summary: str = Field("neutral", description="positive/neutral/negative")
    confidence_score: float = Field(0.5, ge=0, le=1, description="Gemini confidence")
    
    class Config:
        json_schema_extra = {
            "example": {
                "article_id": "news_001",
                "title": "McLaren upgrades MGU-K harvesting for Silverstone",
                "source_name": "F1-Technical",
                "published_at": "2026-03-15T10:30:00Z",
                "teams_mentioned": ["McLaren"],
                "performance_impact": 0.65,
                "sentiment_summary": "positive",
                "confidence_score": 0.92
            }
        }


class PredictionCaliber(BaseModel):
    """AI Predictor output - probabilistic race outcomes."""
    
    prediction_id: str = Field(..., description="Unique prediction ID")
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    
    # Replicate/Session info
    driver_number: int
    driver_name: str
    session_key: int
    session_type: str = Field("race", description="practice/qualifying/race")
    
    # Core predictions
    win_probability: float = Field(0.5, ge=0, le=1, description="Probability of winning")
    podium_probability: float = Field(0.5, ge=0, le=1, description="Top 3 finish")
    overtake_probability: float = Field(0.5, ge=0, le=1, description="Overtake likelihood")
    
    # Tactical predictions
    pit_window_optimal_lap: Optional[int] = None
    tire_life_forecast_laps: Optional[int] = None
    safety_car_risk: float = Field(0.5, ge=0, le=1, description="SC deployment likelihood")
    
    # Model metadata
    model_version: str = Field("v0.0.1", description="Model version used")
    confidence: float = Field(0.5, ge=0, le=1, description="Overall prediction confidence")
    
    class Config:
        json_schema_extra = {
            "example": {
                "prediction_id": "pred_001",
                "driver_number": 44,
                "driver_name": "Lewis Hamilton",
                "session_key": 8765,
                "win_probability": 0.75,
                "podium_probability": 0.92,
                "overtake_probability": 0.68,
                "pit_window_optimal_lap": 23,
                "tire_life_forecast_laps": 18,
                "safety_car_risk": 0.15,
                "confidence": 0.88
            }
        }


class PredictionComparison(BaseModel):
    """Head-to-head prediction comparison (e.g., Ham vs Ver)."""
    
    prediction_id: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    
    driver1_number: int
    driver1_name: str
    driver1_overtake_probability: float = Field(0.5, ge=0, le=1)
    
    driver2_number: int
    driver2_name: str
    driver2_overtake_probability: float = Field(0.5, ge=0, le=1)
    
    # Context
    current_gap_ms: Optional[float] = None
    track_name: Optional[str] = None
    sector: Optional[int] = None  # 1, 2, or 3
    
    class Config:
        json_schema_extra = {
            "example": {
                "prediction_id": "comp_001",
                "driver1_number": 44,
                "driver1_name": "Hamilton",
                "driver1_overtake_probability": 0.68,
                "driver2_number": 1,
                "driver2_name": "Verstappen",
                "driver2_overtake_probability": 0.32,
                "current_gap_ms": 523.5,
                "track_name": "Silverstone",
                "sector": 2
            }
        }


class RaceStandings(BaseModel):
    """Championship standings snapshot."""
    
    season_year: int
    rounds_completed: int
    
    drivers: list[dict] = Field(
        default_factory=list,
        description="List of {position, driver_number, points, wins, podiums}"
    )
    
    constructors: list[dict] = Field(
        default_factory=list,
        description="List of {position, team, points, wins}"
    )
    
    fetched_at: datetime = Field(default_factory=datetime.utcnow)
