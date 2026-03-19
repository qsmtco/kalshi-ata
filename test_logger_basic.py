import sys
sys.path.insert(0, 'src')
from logger import TradeLogger
import sqlite3
import os

# Ensure clean test DB
if os.path.exists('data/test.db'):
    os.remove('data/test.db')

logger = TradeLogger(db_path='data/test.db')
logger.log_trade({
    'market_id': 'TEST-KXHI-001',
    'strategy': 'news_sentiment',
    'action': 'buy',
    'quantity': 50,
    'entry_price': 0.48
})

# Verify
conn = sqlite3.connect('data/test.db')
row = conn.execute('SELECT * FROM trades').fetchone()
conn.close()

assert row is not None, "No trade logged"
assert row[2] == 'TEST-KXHI-001', f"market_id mismatch: {row[2]}"
assert row[3] == 'news_sentiment', f"strategy mismatch: {row[3]}"
assert row[4] == 'buy', f"action mismatch: {row[4]}"
assert row[5] == 50, f"quantity mismatch: {row[5]}"
assert row[6] == 0.48, f"entry_price mismatch: {row[6]}"

print("✓ TradeLogger basic test passed")
os.remove('data/test.db')
