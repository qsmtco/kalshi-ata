import sqlite3
import os
from typing import Any, Dict, List

DEFAULTS = {
    'kelly_fraction': 0.5,
    'max_position_size_pct': 0.1,
    'stop_loss_pct': 0.05,
    'take_profit_pct': 0.1,
    'news_sentiment_threshold': 0.6,
    'stat_arbitrage_threshold': 0.05,
    'volatility_threshold': 0.1,
    'trade_interval_seconds': 60,
    # Strategy enables
    'news_sentiment_enabled': False,  # Disabled: NewsAPI free tier (100 req/day) gets exhausted quickly
    'statistical_arbitrage_enabled': False,
    'volatility_based_enabled': False,
    # Notifications
    'telegram_notifications': True,
    'market_data_update_interval': 60,
}

GUARDRAILS = {
    'kellyFraction': (0.1, 0.8),
    'maxPositionSizePct': (0.01, 0.25),
    'stopLossPct': (0.01, 0.20),
    'takeProfitPct': (0.02, 0.50),
    'newsSentimentThreshold': (0.3, 0.9),
    'statArbitrageThreshold': (0.01, 0.20),
    'volatilityThreshold': (0.05, 0.30),
    'tradeIntervalSeconds': (30, 3600)
}

class SettingsManager:
    def __init__(self, db_path='data/kalshi.db'):
        # Convert relative path to absolute based on script location
        if not os.path.isabs(db_path):
            script_dir = os.path.dirname(os.path.abspath(__file__))
            project_root = os.path.dirname(script_dir)  # src/ -> project root
            db_path = os.path.join(project_root, db_path)
        self.db = sqlite3.connect(db_path)
        self.create_tables()
        self.ensure_defaults()
        # Listener support
        self._change_listeners = []
    
    def add_change_listener(self, callback):
        """Register a callback to be called when settings change."""
        if callback not in self._change_listeners:
            self._change_listeners.append(callback)
    
    def _notify_listeners(self, changes):
        """Notify all registered listeners of settings changes."""
        for callback in self._change_listeners:
            try:
                callback(changes)
            except Exception:
                pass
    
    @property
    def settings(self):
        """Return all settings as an object with attributes."""
        class Settings:
            pass
        s = Settings()
        for k, v in self.get_settings().items():
            setattr(s, k, v)
        return s

    def create_tables(self):
        self.db.execute('''
            CREATE TABLE IF NOT EXISTS current_settings (
                parameter TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        self.db.execute('''
            CREATE TABLE IF NOT EXISTS settings_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                changed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                parameter TEXT NOT NULL,
                old_value TEXT,
                new_value TEXT NOT NULL,
                source TEXT NOT NULL,
                reason TEXT
            )
        ''')
        self.db.commit()

    def ensure_defaults(self):
        for param, default in DEFAULTS.items():
            self.db.execute(
                'INSERT OR IGNORE INTO current_settings (parameter, value) VALUES (?, ?)',
                (param, str(default))
            )
        self.db.commit()

    def _parse_value(self, key: str, raw: str) -> Any:
        """Parse a stored string value back to its Python type."""
        default = DEFAULTS.get(key)
        if default is None:
            return raw
        # Explicitly handle booleans — bool('False') is True (non-empty string!)
        if isinstance(default, bool):
            return raw == 'True'
        if isinstance(default, float):
            return float(raw)
        if isinstance(default, int):
            return int(raw)
        return raw

    def get(self, key: str) -> Any:
        cur = self.db.execute('SELECT value FROM current_settings WHERE parameter = ?', (key,))
        row = cur.fetchone()
        if row:
            return self._parse_value(key, row[0])
        return DEFAULTS.get(key)

    def update(self, key: str, value: Any, source: str, reason: str = ''):
        if key not in DEFAULTS:
            raise ValueError(f"Unknown setting: {key}")
        # Validate guardrails
        if key in GUARDRAILS:
            min_v, max_v = GUARDRAILS[key]
            if not (min_v <= value <= max_v):
                raise ValueError(f"{key}={value} outside guardrail [{min_v}, {max_v}]")
        old = self.get(key)
        # Update current_settings
        self.db.execute(
            'INSERT OR REPLACE INTO current_settings (parameter, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)',
            (key, str(value))
        )
        # Log history
        self.db.execute(
            'INSERT INTO settings_history (parameter, old_value, new_value, source, reason) VALUES (?, ?, ?, ?, ?)',
            (key, str(old), str(value), source, reason)
        )
        self.db.commit()

    def get_history(self, parameter: str = None, limit: int = 100) -> List[Dict]:
        if parameter:
            cur = self.db.execute(
                'SELECT * FROM settings_history WHERE parameter = ? ORDER BY changed_at DESC LIMIT ?',
                (parameter, limit)
            )
        else:
            cur = self.db.execute(
                'SELECT * FROM settings_history ORDER BY changed_at DESC LIMIT ?',
                (limit,)
            )
        rows = cur.fetchall()
        cols = [desc[0] for desc in cur.description]
        return [dict(zip(cols, row)) for row in rows]

    def get_settings(self) -> Dict[str, Any]:
        """Return all current settings as a dictionary."""
        cur = self.db.execute('SELECT parameter, value FROM current_settings')
        rows = cur.fetchall()
        result = {}
        for param, val in rows:
            if param in DEFAULTS:
                result[param] = self._parse_value(param, val)
            else:
                result[param] = val
        return result

    def update_settings(self, updates: Dict[str, Any]):
        """Batch update multiple settings, validating each against guardrails."""
        updated = []
        for key, value in updates.items():
            self.update(key, value, source='manual', reason='api_update')
            updated.append(key)
        return {"success": True, "updated": updated}
