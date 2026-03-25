import time
import logging
import os
from config import KALSHI_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, BANKROLL, TRADE_INTERVAL_SECONDS, PAPER_TRADING
from kalshi_api import KalshiAPI
from trader import Trader
from notifier import Notifier
from logger import Logger

def setup_logging():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    logger = Logger()
    return logger

def main():
    logger = setup_logging()
    logger.info("Starting Kalshi Advanced Trading Bot with Phase 3 features")

    # Initialize to None to prevent UnboundLocalError in exception handlers
    trader = None
    notifier = None

    # Log trading mode clearly
    demo_mode = os.environ.get('KALSHI_DEMO_MODE', 'true').lower() == 'true'
    paper_mode = PAPER_TRADING
    if demo_mode:
        logger.warning("⚠️  KALSHI_DEMO_MODE=true — connected to DEMO exchange (no real orders)")
    elif paper_mode:
        logger.warning("⚠️  PAPER_TRADING=true — paper mode (simulated P&L, no real trades)")
    else:
        logger.info("🚀 LIVE TRADING MODE — real orders will be placed")

    try:
        api = KalshiAPI(KALSHI_API_KEY)
        notifier = Notifier(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)
        trader = Trader(api, notifier, logger, BANKROLL)

        # Sync actual account balance from API (balance is returned in cents, converted to dollars)
        trader.sync_bankroll_from_api()

        # Start market data streaming (Phase 3 feature)
        trader.market_data_streamer.start_streaming()
        logger.info("Market data streaming started")

        # Initial blocking fetch — populate markets before first trading cycle
        # Without this, the daemon's first fetch takes ~65s, leaving markets_data empty
        logger.info("Initial market fetch (blocking, ~5s)...")
        trader.market_data_streamer._update_market_data()
        logger.info(f"Initial fetch done — {len(trader.market_data_streamer.markets_data)} markets loaded")

        # Phase 1: Sync open positions from API into PositionTracker
        try:
            api_resp = api.get_positions()
            if api_resp and isinstance(api_resp, dict):
                # Kalshi returns {"market_positions": [...]} at TOP level
                positions_list = api_resp.get('market_positions', api_resp.get('event_positions', []))
                if isinstance(positions_list, dict):
                    positions_list = list(positions_list.values())
                count = trader.position_tracker.sync_from_api(positions_list)
                logger.info(f"PositionTracker synced: restored {count} positions from API")
        except Exception as e:
            logger.warning(f"Could not sync positions from API: {e}")

        while True:
            logger.info("Running trading strategy with real-time market data")
            trader.run_trading_strategy()
            time.sleep(TRADE_INTERVAL_SECONDS)

    except KeyboardInterrupt:
        logger.info("Bot shutdown requested by user")
        if trader:
            trader.market_data_streamer.stop_streaming()
    except Exception as e:
        logger.error(f"An error occurred: {e}")
        if notifier:
            notifier.send_error_notification(str(e))
        if trader:
            trader.market_data_streamer.stop_streaming()
        raise
    finally:
        if trader:
            trader.market_data_streamer.stop_streaming()
        logger.info("Market data streaming stopped")

if __name__ == "__main__":
    main()
