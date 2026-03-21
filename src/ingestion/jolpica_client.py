"""
Jolpica Historical F1 Data Client
Fetches race history, results, and lap data.
Caches to Parquet for lightning-fast UI loading.
"""

import asyncio
import aiohttp
import logging
import json
from pathlib import Path
from datetime import datetime
from typing import Optional
from src.utils.config import Config

logger = logging.getLogger(__name__)

try:
    import pandas as pd
    import pyarrow.parquet as pq
    PARQUET_AVAILABLE = True
except ImportError:
    PARQUET_AVAILABLE = False
    logger.warning("Pandas/Parquet not available - caching disabled")


class JolpicaClient:
    """Client for Jolpica Ergast F1 API - historical race data."""
    
    def __init__(self):
        self.base_url = Config.JOLPICA_BASE_URL
        self.session: Optional[aiohttp.ClientSession] = None
        self.cache_dir = Path(Config.DATA_PROCESSED_PATH)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        
    async def start(self) -> None:
        """Initialize HTTP session."""
        self.session = aiohttp.ClientSession()
        logger.info(f"Jolpica client initialized with base URL: {self.base_url}")
        
    async def stop(self) -> None:
        """Close HTTP session."""
        if self.session:
            await self.session.close()
            
    async def _get_json(self, endpoint: str) -> Optional[dict]:
        """Generic GET request handler with JSON parsing."""
        try:
            url = f"{self.base_url}/{endpoint}.json"
            async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    return await resp.json()
                else:
                    logger.warning(f"Jolpica {endpoint} returned {resp.status}")
                    return None
        except Exception as e:
            logger.error(f"Failed to fetch {endpoint}: {e}")
            return None
            
    async def get_current_season(self, year: int = 2026) -> Optional[dict]:
        """Get current season standings."""
        cache_file = self.cache_dir / f"season_{year}.parquet"
        
        # Try cache first
        if cache_file.exists() and PARQUET_AVAILABLE:
            try:
                df = pd.read_parquet(cache_file)
                return df.to_dict('records')
            except Exception as e:
                logger.warning(f"Cache read error: {e}")
                
        # Fetch from API
        data = await self._get_json(f"{year}")
        
        # Cache if available
        if data and PARQUET_AVAILABLE:
            try:
                df = pd.json_normalize(data.get('SeasonsTable', {}).get('Seasons', []))
                df.to_parquet(cache_file)
                logger.info(f"Cached season data to {cache_file}")
            except Exception as e:
                logger.warning(f"Failed to cache season data: {e}")
                
        return data
        
    async def get_races(self, year: int = 2026) -> list[dict]:
        """Get all races for a season."""
        cache_file = self.cache_dir / f"races_{year}.parquet"
        
        if cache_file.exists() and PARQUET_AVAILABLE:
            try:
                df = pd.read_parquet(cache_file)
                return df.to_dict('records')
            except Exception as e:
                logger.warning(f"Cache read error: {e}")
                
        data = await self._get_json(f"{year}/races")
        
        if data and PARQUET_AVAILABLE:
            try:
                races = data.get('RaceTable', {}).get('Races', [])
                df = pd.json_normalize(races)
                df.to_parquet(cache_file)
                logger.info(f"Cached {len(races)} races to {cache_file}")
            except Exception as e:
                logger.warning(f"Failed to cache races: {e}")
                
        races = data.get('RaceTable', {}).get('Races', []) if data else []
        return races
        
    async def get_race_results(self, year: int, round_num: int) -> list[dict]:
        """Get race results for a specific race."""
        cache_file = self.cache_dir / f"results_{year}_{round_num}.parquet"
        
        if cache_file.exists() and PARQUET_AVAILABLE:
            try:
                df = pd.read_parquet(cache_file)
                return df.to_dict('records')
            except Exception as e:
                logger.warning(f"Cache read error: {e}")
                
        data = await self._get_json(f"{year}/{round_num}/results")
        
        if data and PARQUET_AVAILABLE:
            try:
                results = data.get('RaceTable', {}).get('Races', [{}])[0].get('Results', [])
                df = pd.json_normalize(results)
                df.to_parquet(cache_file)
                logger.info(f"Cached {len(results)} results to {cache_file}")
            except Exception as e:
                logger.warning(f"Failed to cache results: {e}")
                
        results = data.get('RaceTable', {}).get('Races', [{}])[0].get('Results', []) if data else []
        return results
        
    async def get_qualifying_results(self, year: int, round_num: int) -> list[dict]:
        """Get qualifying results for a specific race."""
        cache_file = self.cache_dir / f"qualifying_{year}_{round_num}.parquet"
        
        if cache_file.exists() and PARQUET_AVAILABLE:
            try:
                df = pd.read_parquet(cache_file)
                return df.to_dict('records')
            except Exception as e:
                logger.warning(f"Cache read error: {e}")
                
        data = await self._get_json(f"{year}/{round_num}/qualifying")
        
        if data and PARQUET_AVAILABLE:
            try:
                results = data.get('RaceTable', {}).get('Races', [{}])[0].get('QualifyingResults', [])
                df = pd.json_normalize(results)
                df.to_parquet(cache_file)
                logger.info(f"Cached {len(results)} qualifying results to {cache_file}")
            except Exception as e:
                logger.warning(f"Failed to cache qualifying: {e}")
                
        results = data.get('RaceTable', {}).get('Races', [{}])[0].get('QualifyingResults', []) if data else []
        return results
        
    async def get_standings(self, year: int = 2026) -> dict:
        """Get current championship standings."""
        data = await self._get_json(f"{year}/standings")
        
        if data:
            standings_table = data.get('StandingsTable', {}).get('StandingsList', [{}])[0]
            return {
                'drivers': standings_table.get('DriverStandings', []),
                'constructors': standings_table.get('ConstructorStandings', []) 
                    if 'ConstructorStandings' in standings_table else []
            }
        return {'drivers': [], 'constructors': []}
        
    async def get_drivers(self) -> list[dict]:
        """Get all drivers ever in F1."""
        cache_file = self.cache_dir / "drivers.parquet"
        
        if cache_file.exists() and PARQUET_AVAILABLE:
            try:
                df = pd.read_parquet(cache_file)
                return df.to_dict('records')
            except Exception as e:
                logger.warning(f"Cache read error: {e}")
                
        data = await self._get_json("drivers")
        
        if data and PARQUET_AVAILABLE:
            try:
                drivers = data.get('DriverTable', {}).get('Drivers', [])
                df = pd.json_normalize(drivers)
                df.to_parquet(cache_file)
                logger.info(f"Cached {len(drivers)} drivers to {cache_file}")
            except Exception as e:
                logger.warning(f"Failed to cache drivers: {e}")
                
        drivers = data.get('DriverTable', {}).get('Drivers', []) if data else []
        return drivers
        
    async def get_constructors(self) -> list[dict]:
        """Get all F1 teams/constructors."""
        cache_file = self.cache_dir / "constructors.parquet"
        
        if cache_file.exists() and PARQUET_AVAILABLE:
            try:
                df = pd.read_parquet(cache_file)
                return df.to_dict('records')
            except Exception as e:
                logger.warning(f"Cache read error: {e}")
                
        data = await self._get_json("constructors")
        
        if data and PARQUET_AVAILABLE:
            try:
                constructors = data.get('ConstructorTable', {}).get('Constructors', [])
                df = pd.json_normalize(constructors)
                df.to_parquet(cache_file)
                logger.info(f"Cached {len(constructors)} constructors to {cache_file}")
            except Exception as e:
                logger.warning(f"Failed to cache constructors: {e}")
                
        constructors = data.get('ConstructorTable', {}).get('Constructors', []) if data else []
        return constructors
        
    def clear_cache(self) -> None:
        """Clear all cached Parquet files."""
        try:
            for parquet_file in self.cache_dir.glob("*.parquet"):
                parquet_file.unlink()
            logger.info("Cache cleared")
        except Exception as e:
            logger.error(f"Failed to clear cache: {e}")
