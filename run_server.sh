#!/bin/sh
set -eu

# Dependencies, including nonebot-plugin-sticker-saver, are installed at build
# time. Keeping Python in the foreground lets Docker forward stop signals.
exec python bot.py
