"""
Gemini AI News Analyzer
Parses headlines for team mentions and assigns performance impact scores.
Uses Gemini 1.5 Flash for real-time sentiment analysis.
"""

import asyncio
import aiohttp
import logging
import json
from datetime import datetime
from typing import Optional
from src.utils.config import Config
from src.models.predictions import NewsArticle

logger = logging.getLogger(__name__)

try:
    import google.generativeai as genai
    GEMINI_AVAILABLE = True
except ImportError:
    GEMINI_AVAILABLE = False
    logger.warning("Gemini API not available")


class GeminiNewsAnalyzer:
    """Analyzes news articles for team performance impact using Gemini."""
    
    SYSTEM_PROMPT = """You are an expert Formula 1 analyst specializing in 2026 regulations.
Analyze the following news article and extract:

1. Teams mentioned (exact team names or abbreviations)
2. Drivers mentioned (exact driver names)
3. Performance Impact: A numerical score from -1.0 (very negative for team/driver) to +1.0 (very positive)
4. Sentiment: 'positive', 'neutral', or 'negative'
5. Confidence: Your confidence in the analysis (0-1)

Consider 2026 regulation changes:
- 768kg weight reduction (performance sensitive)
- 50/50 ICE/MGU-K power split (energy management critical)
- Active Aerodynamics (X-Mode dragy, Z-Mode downforce)
- Manual electrical boost overtaking system

Respond as JSON: {
    "teams": ["Team1", "Team2"],
    "drivers": ["Driver1", "Driver2"],
    "performance_impact": 0.65,
    "sentiment": "positive",
    "confidence": 0.92,
    "reasoning": "Brief explanation"
}"""
    
    def __init__(self):
        self.api_key = Config.GEMINI_API_KEY
        if GEMINI_AVAILABLE and self.api_key:
            genai.configure(api_key=self.api_key)
            self.model = genai.GenerativeModel(Config.GEMINI_MODEL)
        else:
            self.model = None
            logger.warning("Gemini News Analyzer initialized without API connection")
            
    async def analyze_article(self, article: dict) -> Optional[NewsArticle]:
        """
        Analyze a single article (from NewsAPI).
        
        Article dict should have:
        - title, description, content, url, image_url
        - source: {name}
        - publishedAt
        """
        try:
            if not self.model:
                return self._create_dummy_article(article)
                
            # Prepare article text
            article_text = f"""
Title: {article.get('title', '')}
Description: {article.get('description', '')}
Content: {article.get('content', '')}
"""
            
            # Call Gemini API for analysis
            response = await asyncio.to_thread(
                self.model.generate_content,
                f"{self.SYSTEM_PROMPT}\n\nArticle:\n{article_text}"
            )
            
            if not response or not response.text:
                logger.warning(f"No response from Gemini for article: {article.get('title')}")
                return self._create_dummy_article(article)
                
            # Parse JSON response
            analysis = self._parse_gemini_response(response.text)
            
            # Create NewsArticle
            news = NewsArticle(
                article_id=f"article_{hash(article.get('url', ''))}",
                title=article.get('title', ''),
                description=article.get('description'),
                content=article.get('content'),
                url=article.get('url', ''),
                image_url=article.get('urlToImage'),
                source_name=article.get('source', {}).get('name', 'Unknown'),
                published_at=self._parse_date(article.get('publishedAt')),
                teams_mentioned=analysis.get('teams', []),
                drivers_mentioned=analysis.get('drivers', []),
                performance_impact=float(analysis.get('performance_impact', 0.0)),
                sentiment_summary=analysis.get('sentiment', 'neutral'),
                confidence_score=float(analysis.get('confidence', 0.5))
            )
            
            logger.info(f"Analyzed article: {article.get('title')[:50]}... Impact: {news.performance_impact}")
            return news
            
        except Exception as e:
            logger.error(f"Error analyzing article: {e}")
            return None
            
    async def analyze_articles_batch(self, articles: list[dict]) -> list[NewsArticle]:
        """Analyze multiple articles concurrently."""
        tasks = [self.analyze_article(article) for article in articles]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return [r for r in results if isinstance(r, NewsArticle)]
        
    def _parse_gemini_response(self, response_text: str) -> dict:
        """Parse Gemini's JSON response."""
        try:
            # Try to extract JSON from response
            if '```json' in response_text:
                json_str = response_text.split('```json')[1].split('```')[0]
            elif '{' in response_text:
                # Find JSON object
                start = response_text.find('{')
                end = response_text.rfind('}') + 1
                json_str = response_text[start:end]
            else:
                json_str = response_text
                
            return json.loads(json_str)
        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse Gemini JSON: {e}")
            # Return default analysis
            return {
                'teams': [],
                'drivers': [],
                'performance_impact': 0.0,
                'sentiment': 'neutral',
                'confidence': 0.0
            }
            
    def _create_dummy_article(self, article: dict) -> NewsArticle:
        """Create NewsArticle with dummy Gemini analysis (fallback)."""
        return NewsArticle(
            article_id=f"article_{hash(article.get('url', ''))}",
            title=article.get('title', ''),
            description=article.get('description'),
            content=article.get('content'),
            url=article.get('url', ''),
            image_url=article.get('urlToImage'),
            source_name=article.get('source', {}).get('name', 'Unknown'),
            published_at=self._parse_date(article.get('publishedAt')),
            teams_mentioned=[],
            drivers_mentioned=[],
            performance_impact=0.0,
            sentiment_summary='neutral',
            confidence_score=0.0
        )
        
    def _parse_date(self, date_str: Optional[str]) -> datetime:
        """Parse ISO date string."""
        if not date_str:
            return datetime.utcnow()
        try:
            return datetime.fromisoformat(date_str.replace('Z', '+00:00'))
        except:
            return datetime.utcnow()


class NewsAPIClient:
    """Client for fetching headlines from NewsAPI."""
    
    def __init__(self):
        self.api_key = Config.NEWS_API_KEY
        self.base_url = "https://newsapi.org/v2"
        self.session: Optional[aiohttp.ClientSession] = None
        
    async def start(self) -> None:
        """Initialize HTTP session."""
        self.session = aiohttp.ClientSession()
        
    async def stop(self) -> None:
        """Close HTTP session."""
        if self.session:
            await self.session.close()
            
    async def get_f1_headlines(self, query: str = "Formula 1", limit: int = 10) -> list[dict]:
        """Fetch F1-related headlines from NewsAPI."""
        try:
            url = f"{self.base_url}/everything"
            params = {
                'q': query,
                'sortBy': 'publishedAt',
                'language': Config.NEWS_API_LANGUAGE,
                'apiKey': self.api_key,
                'pageSize': limit
            }
            
            async with self.session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get('articles', [])
                else:
                    logger.warning(f"NewsAPI returned {resp.status}")
                    return []
        except Exception as e:
            logger.error(f"Failed to fetch news: {e}")
            return []
            
    async def get_team_news(self, team_name: str, limit: int = 5) -> list[dict]:
        """Get news for a specific F1 team."""
        query = f"{team_name} Formula 1 OR {team_name} F1"
        return await self.get_f1_headlines(query, limit)
        
    async def get_driver_news(self, driver_name: str, limit: int = 5) -> list[dict]:
        """Get news for a specific driver."""
        query = f"{driver_name} Formula 1 OR {driver_name} F1"
        return await self.get_f1_headlines(query, limit)
