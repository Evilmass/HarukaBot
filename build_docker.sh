#!/bin/sh
set -eu

git pull
docker compose up --build --force-recreate --detach
