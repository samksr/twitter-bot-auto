#!/data/data/com.termux/files/usr/bin/bash
echo -e "\033[0;32m🚀 Starting V50 Zenith Ultimate...\033[0m"
while true; do
    PYTHONUNBUFFERED=1 python -u bot.py 2>&1 | tee -a "data/logs/bot.log"
    EXIT_CODE=$?
    if [ $EXIT_CODE -eq 0 ]; then exit 0; fi
    echo -e "\033[1;33m⚠️ Crash detected. Restarting Zenith in 5s...\033[0m"
    sleep 5
done
