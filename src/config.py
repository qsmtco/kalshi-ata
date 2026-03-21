import os

def _get_env(name, default):
    return os.environ.get(name, default)

KALSHI_API_KEY = _get_env("KALSHI_API_KEY", "your_kalshi_api_key")
KALSHI_PRIVATE_KEY_PATH = _get_env("KALSHI_PRIVATE_KEY_PATH", "./kalshi_private_key.pem")
KALSHI_API_BASE_URL = _get_env("KALSHI_API_BASE_URL", "https://api.elections.kalshi.com/trade-api/v2")
TELEGRAM_BOT_TOKEN = _get_env("TELEGRAM_BOT_TOKEN", "your_telegram_bot_token")
TELEGRAM_CHAT_ID = _get_env("TELEGRAM_CHAT_ID", "your_chat_id")

# News API Configuration
NEWS_API_KEY = _get_env("NEWS_API_KEY", "your_news_api_key")
NEWS_API_BASE_URL = "https://newsapi.org/v2"

BANKROLL = 1000
RISK_FACTOR = 1.0
VOLATILITY_PENALTY = True
MIN_DATA_POINTS = 10
TRADE_INTERVAL_SECONDS = 300

# Logging configuration
LOG_FILE_PATH = "trading_bot.log"
LOG_LEVEL = "INFO"

# Error handling settings
MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 5

# Notification settings
ENABLE_NOTIFICATIONS = True
NOTIFICATION_THRESHOLD = 0.05  # Notify if profit/loss exceeds this percentage

# Advanced Strategy Parameters
NEWS_SENTIMENT_THRESHOLD = 0.6  # Threshold for positive sentiment to trigger a trade
STAT_ARBITRAGE_THRESHOLD = 0.05 # Price deviation for statistical arbitrage
VOLATILITY_THRESHOLD = 0.1     # Volatility threshold for trading

# Risk Management Parameters
MAX_POSITION_SIZE_PERCENTAGE = 0.10 # Max percentage of bankroll to commit to a single trade
STOP_LOSS_PERCENTAGE = 0.05         # Percentage loss at which to close a position

# =========================================================================
# PHASE 3: PAPER TRADING MODE
# =========================================================================
# Default to True for safety - must explicitly enable live trading
PAPER_TRADING = os.environ.get('PAPER_TRADING', 'true').lower() == 'true'

# =========================================================================
# PHASE 5: MARKET MAKING
# =========================================================================
MARKET_MAKING_ENABLED = os.environ.get('MARKET_MAKING_ENABLED', 'false').lower() == 'true'
MARKET_MAKING_MAX_POSITION_PCT = float(os.environ.get('MARKET_MAKING_MAX_PCT', '0.40'))
MARKET_MAKING_MIN_VOLUME = int(os.environ.get('MARKET_MAKING_MIN_VOLUME', '1000'))
MARKET_MAKING_MIN_SPREAD = float(os.environ.get('MARKET_MAKING_MIN_SPREAD', '0.02'))

# =========================================================================
# KYLE'S LAMBDA — Order Flow Impact Signal
# =========================================================================
# Ref: Kyle, A.S. (1985). "Continuous Auctions and Insider Trading." Econometrica.
KYLE_LAMBDA_ENABLED       = os.environ.get('KYLE_LAMBDA_ENABLED', 'true').lower() == 'true'
KYLE_MIN_TRADES           = int(os.environ.get('KYLE_MIN_TRADES', '30'))
# R² > KYLES_R2_THRESHOLD → significant informed flow
KYLE_R2_THRESHOLD         = float(os.environ.get('KYLE_R2_THRESHOLD', '0.05'))
# |λ| > KYLE_LAMBDA_THRESHOLD → high price impact per contract
KYLE_LAMBDA_THRESHOLD     = float(os.environ.get('KYLE_LAMBDA_THRESHOLD', '0.001'))
# Position size multiplier when Kyle signal fires (0.0–1.0)
KYLE_POSITION_SCALE_HIGH   = float(os.environ.get('KYLE_POSITION_SCALE_HIGH', '0.25'))
KYLE_POSITION_SCALE_MODERATE = float(os.environ.get('KYLE_POSITION_SCALE_MODERATE', '0.50'))
# How often to refresh λ estimates (seconds)
KYLE_REFRESH_INTERVAL_SEC = int(os.environ.get('KYLE_REFRESH_INTERVAL_SEC', '900'))  # 15 min

# =========================================================================
# HAWKES PROCESS — Order Flow Clustering Signal
# =========================================================================
# Ref: Hawkes (1971). "Spectra of Some Self-Exciting and Mutually Exciting."
HAWKES_ENABLED             = os.environ.get('HAWKES_ENABLED', 'true').lower() == 'true'
HAWKES_MIN_TRADES         = int(os.environ.get('HAWKES_MIN_TRADES', '20'))
# BR > HAWKES_BR_THRESHOLD → high self-excitation (trades cluster)
HAWKES_BR_THRESHOLD       = float(os.environ.get('HAWKES_BR_THRESHOLD', '0.70'))
# Skip trade if BR above this
HAWKES_SKIP_THRESHOLD     = float(os.environ.get('HAWKES_SKIP_THRESHOLD', '0.80'))
# How often to refresh BR estimates (seconds)
HAWKES_REFRESH_INTERVAL_SEC = int(os.environ.get('HAWKES_REFRESH_INTERVAL_SEC', '900'))  # 15 min

# =========================================================================
# VPIN — Volume-Synchronized Probability of Informed Trading
# =========================================================================
# Ref: Easley, López de Prado & O'Hara (2012).
#      "Flow Toxicity and Liquidity in a High-Frequency World."
VPIN_ENABLED             = os.environ.get('VPIN_ENABLED', 'true').lower() == 'true'
VPIN_M_BUCKETS           = int(os.environ.get('VPIN_M_BUCKETS', '50'))
VPIN_MIN_BUCKETS         = int(os.environ.get('VPIN_MIN_BUCKETS', '10'))
VPIN_HIGH_THRESHOLD       = float(os.environ.get('VPIN_HIGH_THRESHOLD', '0.50'))
VPIN_EXTREME_THRESHOLD    = float(os.environ.get('VPIN_EXTREME_THRESHOLD', '0.70'))
VPIN_SKIP_THRESHOLD       = float(os.environ.get('VPIN_SKIP_THRESHOLD', '0.80'))
VPIN_REFRESH_INTERVAL_SEC = int(os.environ.get('VPIN_REFRESH_INTERVAL_SEC', '900'))  # 15 min

# =========================================================================
# AVELLANEDA-STOIKOV — Market Making Quote Generation
# =========================================================================
# Ref: Avellaneda & Stoikov (2008). "High Frequency Trading in a LOB."
AVELLANEDA_ENABLED        = os.environ.get('AVELLANEDA_ENABLED', 'true').lower() == 'true'
AVELLANEDA_GAMMA          = float(os.environ.get('AVELLANEDA_GAMMA', '0.10'))   # risk aversion
AVELLANEDA_KAPPA          = float(os.environ.get('AVELLANEDA_KAPPA', '1.0'))    # order book liquidity
AVELLANEDA_SPREAD_PCT     = float(os.environ.get('AVELLANEDA_SPREAD_PCT', '0.02'))  # base spread (2%)
AVELLANEDA_MODE           = os.environ.get('AVELLANEDA_MODE', 'inventory')    # "inventory" or "symmetric"
AVELLANEDA_MAX_TTE_HOURS   = float(os.environ.get('AVELLANEDA_MAX_TTE_HOURS', '48'))  # max hours to expiry

# =========================================================================
# ORDER BOOK ANALYZER — L2 Depth + Spread Decomposition
# =========================================================================
ORDERBOOK_ENABLED        = os.environ.get('ORDERBOOK_ENABLED', 'true').lower() == 'true'
ORDERBOOK_DEPTH_LEVELS  = int(os.environ.get('ORDERBOOK_DEPTH_LEVELS', '10'))
ORDERBOOK_OFI_WINDOW    = int(os.environ.get('ORDERBOOK_OFI_WINDOW', '20'))
# Spread alert thresholds
ORDERBOOK_SPREAD_WARN_PCT = float(os.environ.get('ORDERBOOK_SPREAD_WARN_PCT', '80.0'))  # warn if spread > 80% of price

# =========================================================================
# ALMGREN-CHRISS — Optimal Execution Scheduler
# =========================================================================
# Ref: Almgren & Chriss (2001). "Optimal Execution of Portfolio Transactions."
AC_ENABLED            = os.environ.get('AC_ENABLED', 'true').lower() == 'true'
AC_MIN_QTY            = int(os.environ.get('AC_MIN_QTY', '100'))    # minimum qty to use A-C
AC_HORIZON_HOURS      = float(os.environ.get('AC_HORIZON_HOURS', '4.0'))  # hours to complete large order
AC_N_TRADES           = int(os.environ.get('AC_N_TRADES', '10'))    # number of discrete trades
AC_GAMMA             = float(os.environ.get('AC_GAMMA', '0.001'))   # temporary impact coefficient
AC_RISK_AVERSION     = float(os.environ.get('AC_RISK_AVERSION', '0.1'))  # risk aversion λ


