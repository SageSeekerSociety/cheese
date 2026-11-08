#!/usr/bin/env bash
# $1 = code dir (…/backend), $2 = port. Same env as the stack's backend; background jobs off so the two extra servers only answer requests.
source /tmp/fb108-demo/backend.env
cd "$1" && exec /home/cheese/.cheese/home/de808b13-ffd2-4b8a-9d1d-fba7babe389f/d5db4854-d273-4d60-a402-ce0d101f148d/.cheese/tasks/143df8b8-5d7e-4a04-aac4-0fb45de93d45/backend/.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port "$2"
