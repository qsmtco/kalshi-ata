#!/bin/bash
# Railway environment variables setup
echo "Setting up Railway environment variables..."
railway variables set TELEGRAM_BOT_TOKEN="8262379311:AAHJAM3rAj37nu9vO_bLgGAnh9hbQcsIiy8"
railway variables set TELEGRAM_CHAT_ID="-1003361575892"
railway variables set KALSHI_API_BASE_URL="https://api.elections.kalshi.com/trade-api/v2"
railway variables set BOT_INTERFACE_URL="http://localhost:3050"
railway variables set INTERFACE_PORT="3050"
echo "Environment variables set successfully!"
