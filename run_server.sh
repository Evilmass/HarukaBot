
# !/bin/sh
pkill python
sleep 1
nohup python bot.py >> run.log 2>&1 &
tail -f run.log