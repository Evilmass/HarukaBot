#!/bin/sh
set -eu

# Dependencies are installed at build time. Keeping Python in the foreground
# lets Docker forward stop signals.
exec python bot.py
