#!/bin/bash
cd /home/tikmiks/bot

if ! pgrep -f "main.py" > /dev/null
then
    echo "[$(date)] Bot down, restarting..." >> /home/tikmiks/bot/cron.log
    nohup /home/tikmiks/bot/.venv/bin/python /home/tikmiks/bot/main.py > /home/tikmiks/bot/bot.log 2>&1 < /dev/null &
    disown
fi
