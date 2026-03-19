import sys
sys.path.insert(0, 'src')
from logger import TradeLogger
import sqlite3
import os
import json

db_path = 'data/test_full.db'
if os.path.exists(db_path):
    os.remove(db_path)

logger = TradeLogger(db_path=db_path)

# Log a complete trade with all fields
trade = {
    'market_id': 'KXHI-25MAR18-0.75-IS_RAISING',
    'market_title': 'Will inflation rise above 3.5%?',
    'strategy': 'news_sentiment',
    'action': 'buy',
    'quantity': 50,
    'entry_price': 0.48,
    'exit_price': 0.52,
    'pnl': 2.00,
    'confidence': 0.72,
    'position_size_pct': 0.05,
    'stop_loss_pct': 0.05,
    'take_profit_pct': 0.10,
    'exit_reason': 'take_profit',
    'metadata': json.dumps({'signalType': 'news_spike', 'newsVolume': 15}),
    'closed_at': '2025-03-18T16:00:00'
}
logger.log_trade(trade)

# Verify row exists and all fields match
conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row
row = conn.execute('SELECT * FROM trades').fetchone()
conn.close()

assert row is not None
assert row['market_id'] == trade['market_id']
assert row['strategy'] == trade['strategy']
assert row['action'] == trade['action']
assert row['quantity'] == trade['quantity']
assert row['entry_price'] == trade['entry_price']
assert row['market_title'] == trade['market_title']
assert row['exit_price'] == trade['exit_price']
assert row['pnl'] == trade['pnl']
assert row['confidence'] == trade['confidence']
assert row['position_size_pct'] == trade['position_size_pct']
assert row['exit_reason'] == trade['exit_reason']
assert row['closed_at'] == trade['closed_at']
assert row['metadata'] == trade['metadata']

print("✓ TradeLogger full schema test passed")
os.remove(db_path)
