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

# Check schema
conn = sqlite3.connect(db_path)
cursor = conn.execute("PRAGMA table_info(trades)")
cols = [row[1] for row in cursor.fetchall()]
conn.close()
print("Columns in trades:", cols)

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

# Re-open and check row
conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row
row = conn.execute('SELECT * FROM trades').fetchone()
conn.close()

if row:
    print("Row keys:", list(row.keys()))
    print("Row data:", dict(row))
else:
    print("No row found")

os.remove(db_path)
