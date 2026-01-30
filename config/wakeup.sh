#!/bin/bash
# Run eBay monitor on wake
sleep 10  # Wait for network to reconnect
cd /Users/samwhite/Documents/ebay-search
/Users/samwhite/Documents/ebay-search/venv/bin/python ebay_card_monitor.py >> monitor.log 2>> monitor_error.log &
