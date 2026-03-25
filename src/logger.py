import sqlite3
import logging
import os
from threading import Lock

class Logger:
    def __init__(self, db_path='data/kalshi.db'):
        # Convert relative path to absolute based on script location
        # This ensures db_path works regardless of cwd when Python is spawned
        if not os.path.isabs(db_path):
            script_dir = os.path.dirname(os.path.abspath(__file__))
            project_root = os.path.dirname(script_dir)  # src/ -> project root
            db_path = os.path.join(project_root, db_path)
        self.db_path = db_path
        self.lock = Lock()
        # Set up standard Python logging
        self.logger = logging.getLogger('KalshiBot')
        self.logger.setLevel(logging.INFO)
        if not self.logger.handlers:
            handler = logging.StreamHandler()
            handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
            self.logger.addHandler(handler)
        self.init_database()
    
    # Standard logging methods
    def info(self, msg):
        self.logger.info(msg)
    
    def warning(self, msg):
        self.logger.warning(msg)
    
    def error(self, msg):
        self.logger.error(msg)
    
    def debug(self, msg):
        self.logger.debug(msg)

    def init_database(self):
        """Create full trades table per DATABASE_SCHEMA.sql"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    market_id TEXT NOT NULL,
                    market_title TEXT,
                    strategy TEXT NOT NULL CHECK(strategy IN (
                        'news_sentiment',
                        'statistical_arbitrage',
                        'volatility_based'
                    )),
                    action TEXT NOT NULL CHECK(action IN ('buy', 'sell')),
                    quantity INTEGER NOT NULL CHECK(quantity > 0),
                    entry_price REAL NOT NULL CHECK(entry_price >= 0),
                    exit_price REAL CHECK(exit_price >= 0),
                    pnl REAL,
                    confidence REAL CHECK(confidence >= 0 AND confidence <= 1),
                    position_size_pct REAL CHECK(position_size_pct > 0 AND position_size_pct <= 1),
                    stop_loss_pct REAL CHECK(stop_loss_pct > 0 AND stop_loss_pct <= 1),
                    take_profit_pct REAL CHECK(take_profit_pct > 0 AND take_profit_pct <= 1),
                    exit_reason TEXT CHECK(exit_reason IN (
                        'stop_loss', 'take_profit', 'manual', 'end_of_day', 'circuit_breaker'
                    )),
                    metadata TEXT,
                    closed_at DATETIME,
                    is_paper INTEGER DEFAULT 0,
                    UNIQUE(strategy, market_id, created_at)
                )
            ''')
            conn.commit()

    def log_trade(self, trade_data):
        with self.lock:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute('''
                    INSERT INTO trades (
                        market_id, strategy, action, quantity, entry_price,
                        market_title, exit_price, pnl, confidence,
                        position_size_pct, stop_loss_pct, take_profit_pct,
                        exit_reason, metadata, closed_at, is_paper
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    trade_data['market_id'],
                    trade_data['strategy'],
                    trade_data['action'],
                    trade_data['quantity'],
                    trade_data['entry_price'],
                    trade_data.get('market_title'),
                    trade_data.get('exit_price'),
                    trade_data.get('pnl'),
                    trade_data.get('confidence'),
                    trade_data.get('position_size_pct'),
                    trade_data.get('stop_loss_pct'),
                    trade_data.get('take_profit_pct'),
                    trade_data.get('exit_reason'),
                    trade_data.get('metadata'),
                    trade_data.get('closed_at'),
                    1 if trade_data.get('is_paper', False) else 0
                ))
                conn.commit()
