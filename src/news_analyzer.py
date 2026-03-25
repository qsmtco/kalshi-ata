#!/usr/bin/env python3
"""News sentiment analysis module for Kalshi trading bot."""

import requests
import logging
import json
import os
from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta
from textblob import TextBlob
import re
from config import NEWS_API_KEY, NEWS_API_BASE_URL

logger = logging.getLogger(__name__)

# Daily request budget for NewsAPI (free tier = 100/day, we use 50 to be safe)
NEWS_API_DAILY_BUDGET = 50

class NewsSentimentAnalyzer:
    """Analyzes news sentiment for trading signals."""

    def __init__(self):
        self.api_key = NEWS_API_KEY
        self.base_url = NEWS_API_BASE_URL
        self.session = requests.Session()
        # In-memory news cache: keyed by query, value is (articles, fetch_time)
        # Cache TTL of 10 minutes to avoid burning through NewsAPI's 100 req/day limit.
        self._news_cache: Dict[str, tuple] = {}
        self._cache_ttl_seconds = 600
        # Track consecutive 429s to implement backoff
        self._consecutive_429s = 0
        # H4: Daily budget tracking
        self._usage_file = os.path.join(os.path.dirname(__file__), '..', 'data', 'news_api_usage.json')
        self._daily_budget = NEWS_API_DAILY_BUDGET

    def _load_usage(self) -> Dict[str, Any]:
        """Load usage data from file."""
        try:
            if os.path.exists(self._usage_file):
                with open(self._usage_file, 'r') as f:
                    return json.load(f)
        except Exception as e:
            logger.debug(f"Could not load news API usage file: {e}")
        return {'date': '', 'count': 0}

    def _save_usage(self, data: Dict[str, Any]) -> None:
        """Save usage data to file."""
        try:
            os.makedirs(os.path.dirname(self._usage_file), exist_ok=True)
            with open(self._usage_file, 'w') as f:
                json.dump(data, f)
        except Exception as e:
            logger.warning(f"Could not save news API usage file: {e}")

    def _check_and_increment_usage(self) -> bool:
        """Check if budget available and increment counter. Returns True if allowed."""
        today = datetime.now().strftime('%Y-%m-%d')
        data = self._load_usage()
        
        # Reset counter if new day
        if data.get('date') != today:
            data = {'date': today, 'count': 0}
        
        # Check budget
        if data['count'] >= self._daily_budget:
            logger.warning(f"NewsAPI daily budget exhausted ({data['count']}/{self._daily_budget})")
            return False
        
        # Increment and save
        data['count'] += 1
        self._save_usage(data)
        logger.debug(f"NewsAPI usage: {data['count']}/{self._daily_budget}")
        return True

        # Default keywords for Kalshi markets - expanded to cover gaming, sports, and politics
        # These are used when market-specific keywords aren't provided
        self.keywords = [
            # Gaming/Esports (primary market type)
            'esports', 'gaming', 'Valorant', 'CS2', 'CS:GO', 'League of Legends',
            'Dota 2', 'Overwatch', 'Call of Duty', 'PUBG', 'Fortnite',
            'JDG', 'JD Gaming', 'Bilibili Gaming', 'T1', 'G2 Esports', 'Fnatic',
            'basketball', 'NBA', 'football', 'NFL', 'soccer', 'Premier League',
            'Champions League', 'La Liga', 'Serie A', 'Bundesliga', 'Feyenoord', 'Ajax',
            # Political/Economic (secondary)
            'election', 'president', 'senate', 'congress', 'federal reserve',
            'economy', 'inflation', 'unemployment', 'GDP', 'policy',
            'Supreme Court', 'court', 'ruling', 'decision', 'vote'
        ]

    def fetch_news(self, query: str = None, days_back: int = 1) -> List[Dict[str, Any]]:
        """
        Fetch news articles from NewsAPI, with in-memory caching and 429 backoff.

        Caching: results are cached for _cache_ttl_seconds (default 10 min) to avoid
        burning through NewsAPI's 100 req/day free-tier limit.

        Backoff: on 429 (rate limited), we skip fetching entirely for
        _cache_ttl_seconds and reset the counter on success.

        Args:
            query: Search query (if None, uses default keywords)
            days_back: Number of days to look back

        Returns:
            List of news articles with metadata
        """
        if not self.api_key or self.api_key == "your_news_api_key":
            logger.warning("NewsAPI key not configured, skipping news fetch")
            return []

        # Build cache key from query + days_back
        if not query:
            query = ' OR '.join(f'"{kw}"' for kw in self.keywords[:5])
        cache_key = f"{query}:{days_back}"

        # Return cached result if fresh
        if cache_key in self._news_cache:
            articles, fetch_time = self._news_cache[cache_key]
            age = (datetime.now() - fetch_time).total_seconds()
            if age < self._cache_ttl_seconds:
                logger.info(f"News cache hit ({age:.0f}s old), returning {len(articles)} cached articles")
                return articles

        # Skip fetch if we're in backoff from 429
        if self._consecutive_429s > 0:
            logger.warning(f"NewsAPI 429 backoff active (count={self._consecutive_429s}), skipping fetch")
            # Return stale cache if available
            if cache_key in self._news_cache:
                articles, _ = self._news_cache[cache_key]
                logger.info(f"Returning {len(articles)} stale cached articles during backoff")
                return articles
            return []

        # H4: Check daily budget before making request
        if not self._check_and_increment_usage():
            # Return stale cache if available, else empty
            if cache_key in self._news_cache:
                articles, _ = self._news_cache[cache_key]
                logger.info(f"Budget exhausted — returning {len(articles)} cached articles")
                return articles
            return []

        try:
            # Calculate date range
            from_date = (datetime.now() - timedelta(days=days_back)).strftime('%Y-%m-%d')

            params = {
                'q': query,
                'from': from_date,
                'sortBy': 'relevancy',
                'language': 'en',
                'apiKey': self.api_key
            }

            response = self.session.get(f"{self.base_url}/everything", params=params, timeout=10)
            response.raise_for_status()

            data = response.json()
            articles = data.get('articles', [])
            self._news_cache[cache_key] = (articles, datetime.now())
            self._consecutive_429s = 0  # reset on success
            logger.info(f"Fetched {len(articles)} news articles")
            return articles

        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code == 429:
                self._consecutive_429s += 1
                backoff_min = self._cache_ttl_seconds // 60
                logger.warning(
                    f"NewsAPI 429 rate limit hit (consecutive #{self._consecutive_429s}), "
                    f"backing off for {backoff_min} min"
                )
            else:
                logger.error(f"Error fetching news: {e}")
            return []

        except Exception as e:
            logger.error(f"Error fetching news: {e}")
            return []

    def analyze_sentiment(self, text: str) -> Dict[str, float]:
        """
        Analyze sentiment of text using TextBlob.

        Args:
            text: Text to analyze

        Returns:
            Dictionary with polarity and subjectivity scores
        """
        try:
            blob = TextBlob(text)
            return {
                'polarity': blob.sentiment.polarity,  # -1 to 1 (negative to positive)
                'subjectivity': blob.sentiment.subjectivity  # 0 to 1 (objective to subjective)
            }
        except Exception as e:
            logger.error(f"Error analyzing sentiment: {e}")
            return {'polarity': 0.0, 'subjectivity': 0.5}

    def preprocess_text(self, text: str) -> str:
        """
        Preprocess text for better sentiment analysis.

        Args:
            text: Raw text

        Returns:
            Cleaned text
        """
        if not text:
            return ""

        # Remove URLs
        text = re.sub(r'http\S+', '', text)

        # Remove special characters but keep basic punctuation
        text = re.sub(r'[^\w\s.,!?-]', '', text)

        # Normalize whitespace
        text = ' '.join(text.split())

        return text.strip()

    def analyze_news_sentiment(self, articles: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Analyze sentiment across multiple news articles.

        Args:
            articles: List of news articles

        Returns:
            Aggregated sentiment analysis
        """
        if not articles:
            return {
                'overall_sentiment': 0.0,
                'confidence': 0.0,
                'article_count': 0,
                'positive_articles': 0,
                'negative_articles': 0,
                'neutral_articles': 0
            }

        sentiments = []
        positive_count = 0
        negative_count = 0
        neutral_count = 0

        for article in articles:
            title = article.get('title', '')
            description = article.get('description', '')
            content = article.get('content', '')

            # Combine title and description for analysis
            text = f"{title} {description}".strip()
            clean_text = self.preprocess_text(text)

            if clean_text:
                sentiment = self.analyze_sentiment(clean_text)
                sentiments.append(sentiment)

                # Classify sentiment
                polarity = sentiment['polarity']
                if polarity > 0.1:
                    positive_count += 1
                elif polarity < -0.1:
                    negative_count += 1
                else:
                    neutral_count += 1

        if not sentiments:
            return {
                'overall_sentiment': 0.0,
                'confidence': 0.0,
                'article_count': 0,
                'positive_articles': 0,
                'negative_articles': 0,
                'neutral_articles': 0
            }

        # Calculate aggregate metrics
        avg_polarity = sum(s['polarity'] for s in sentiments) / len(sentiments)
        avg_subjectivity = sum(s['subjectivity'] for s in sentiments) / len(sentiments)

        # Confidence based on agreement and article count
        polarity_variance = sum((s['polarity'] - avg_polarity) ** 2 for s in sentiments) / len(sentiments)
        agreement_factor = 1 / (1 + polarity_variance)  # Higher agreement = higher confidence
        volume_factor = min(len(sentiments) / 10, 1.0)  # More articles = higher confidence
        confidence = agreement_factor * volume_factor

        return {
            'overall_sentiment': round(avg_polarity, 3),
            'avg_subjectivity': round(avg_subjectivity, 3),
            'confidence': round(confidence, 3),
            'article_count': len(sentiments),
            'positive_articles': positive_count,
            'negative_articles': negative_count,
            'neutral_articles': neutral_count,
            'sentiment_distribution': {
                'positive': positive_count,
                'negative': negative_count,
                'neutral': neutral_count
            }
        }

    def extract_keywords_from_markets(self, markets: List[Dict[str, Any]]) -> List[str]:
        """
        Extract relevant keywords from market titles for news searching.
        
        Parses market titles like "JD Gaming vs Bilibili Gaming Winner?"
        into search-friendly keywords like ["JD Gaming", "Bilibili Gaming", "gaming"].
        
        Args:
            markets: List of market dicts with 'title' field
            
        Returns:
            List of extracted keywords for news search
        """
        extracted = []
        seen = set()
        
        for market in markets[:20]:  # Limit to first 20 markets
            title = market.get('title', '')
            if not title:
                continue
                
            # Remove common market title suffixes
            # e.g., "Will JD Gaming win vs Bilibili Gaming?" -> "JD Gaming win vs Bilibili Gaming"
            title_clean = re.sub(r'^(Will|Shall|Does|Is)\s+', '', title, flags=re.IGNORECASE)
            title_clean = re.sub(r'\s*(\?|Winner|Loser|lose|win|vs\.?|versus)\s*$', '', title_clean, flags=re.IGNORECASE)
            title_clean = re.sub(r'\s*(Winner|Loser)$', '', title_clean, flags=re.IGNORECASE)

            # Remove Kalshi "yes"/"no" prefix — e.g. "yes Draymond Green: 4+" -> "Draymond Green: 4+"
            title_clean = re.sub(r'^(yes|no)\s+', '', title_clean, flags=re.IGNORECASE)

            # Remove player prop suffixes — e.g. "Draymond Green: 4+" -> "Draymond Green"
            title_clean = re.sub(r'\s*:\s*[\d.+]+\s*$', '', title_clean)
            
            # Split on "vs", "at", "-" to get team/player names
            parts = re.split(r'\s+(?:vs\.?|versus|at|vs)\s+', title_clean, flags=re.IGNORECASE)
            
            for part in parts:
                # Clean each part
                part = part.strip()
                # Remove parenthetical content
                part = re.sub(r'\s*\([^)]*\)', '', part)
                part = part.strip()
                
                if len(part) >= 3 and part.lower() not in seen:
                    seen.add(part.lower())
                    extracted.append(part)
            
            # Also add broader category keywords based on title content
            title_lower = title.lower()
            if 'basketball' in title_lower or 'nba' in title_lower:
                self._add_if_new(extracted, seen, 'NBA')
                self._add_if_new(extracted, seen, 'basketball')
            if 'football' in title_lower or 'nfl' in title_lower:
                self._add_if_new(extracted, seen, 'NFL')
                self._add_if_new(extracted, seen, 'football')
            if 'soccer' in title_lower or 'premier league' in title_lower:
                self._add_if_new(extracted, seen, 'soccer')
                self._add_if_new(extracted, seen, 'Premier League')
            if 'cs2' in title_lower or 'cs:go' in title_lower or 'valorant' in title_lower:
                self._add_if_new(extracted, seen, 'esports')
                self._add_if_new(extracted, seen, 'Valorant')
                self._add_if_new(extracted, seen, 'CS2')
            if 'league of legends' in title_lower:
                self._add_if_new(extracted, seen, 'League of Legends')
                self._add_if_new(extracted, seen, 'LoL')
        
        logger.info(f"Extracted {len(extracted)} keywords from market titles: {extracted[:5]}...")
        return extracted

    def _add_if_new(self, keywords: List[str], seen: set, word: str) -> None:
        """Helper to add keyword if not already present."""
        if word.lower() not in seen:
            seen.add(word.lower())
            keywords.append(word)

    def get_market_relevant_news(self, market_keywords: List[str] = None, markets: List[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Get news specifically relevant to Kalshi markets.

        Args:
            market_keywords: Market-specific keywords (optional)
            markets: Market list to extract keywords from (optional, used if keywords empty)

        Returns:
            News sentiment analysis for market relevance
        """
        # Skip entirely if no API key configured — avoids unnecessary NewsAPI calls
        if not self.api_key or self.api_key == "your_news_api_key":
            logger.info("NewsAPI not configured, returning empty sentiment")
            return {'headlines': [], 'sentiment_score': 0.0, 'confidence': 0.0}

        # Determine keywords to use: provided > extracted from markets > defaults
        # NOTE: dynamic extraction from market titles is disabled — titles contain
        # embedded yes/no markers that produce invalid search queries.
        # Using curated default keywords instead, which cover esports/sports/politics well.
        if not market_keywords:
            market_keywords = self.keywords

        # Build search query from keywords (limit to 5 to avoid overly broad search)
        query_terms = market_keywords[:5]
        query = ' OR '.join(f'"{kw}"' for kw in query_terms)

        logger.info(f"Fetching news with query: {query[:80]}...")
        articles = self.fetch_news(query=query, days_back=2)

        sentiment_analysis = self.analyze_news_sentiment(articles)

        # Add timestamp and source info
        sentiment_analysis.update({
            'timestamp': datetime.now().isoformat(),
            'source': 'NewsAPI',
            'query_used': query_terms
        })

        logger.info(f"Market news sentiment: {sentiment_analysis['overall_sentiment']:.3f} "
                   f"(confidence: {sentiment_analysis['confidence']:.3f})")

        return sentiment_analysis

    def should_trade_based_on_sentiment(self, sentiment_analysis: Dict[str, Any],
                                       threshold: float = 0.6) -> Dict[str, Any]:
        """
        Determine if sentiment warrants a trading decision.

        Args:
            sentiment_analysis: Result from analyze_news_sentiment
            threshold: Minimum confidence and sentiment threshold

        Returns:
            Trading decision with reasoning
        """
        sentiment = sentiment_analysis.get('overall_sentiment', 0)
        confidence = sentiment_analysis.get('confidence', 0)

        decision = {
            'should_trade': False,
            'direction': None,  # 'long' or 'short'
            'confidence': confidence,
            'sentiment_score': sentiment,
            'reason': ''
        }

        if confidence < 0.3:  # Minimum confidence threshold
            decision['reason'] = f"Low confidence ({confidence:.2f}) - insufficient data"
            return decision

        if sentiment > threshold and confidence > 0.5:
            decision['should_trade'] = True
            decision['direction'] = 'long'
            decision['reason'] = f"Strong positive sentiment ({sentiment:.2f}) with high confidence ({confidence:.2f})"
        elif sentiment < -threshold and confidence > 0.5:
            decision['should_trade'] = True
            decision['direction'] = 'short'
            decision['reason'] = f"Strong negative sentiment ({sentiment:.2f}) with high confidence ({confidence:.2f})"
        else:
            decision['reason'] = f"Sentiment ({sentiment:.2f}) below threshold or insufficient confidence"

        return decision
