#!/bin/bash
# Starts the branch's vite (if not up) and the isolated preview proxy in front of it.
cd "/home/cheese/.cheese/home/de808b13-ffd2-4b8a-9d1d-fba7babe389f/3e9577a4-0166-45ae-a953-53e2ef44804e/.cheese/tasks/7d017c3d-605f-42ac-bd99-bf188b12917a/frontend"
ss -ltn | grep -q ':3217 ' || (nohup pnpm exec vite --port 3217 --strictPort --host 127.0.0.1 > /tmp/bm_vite.log 2>&1 &)
cd /tmp/bmpreview
: "${PW:?set PW to the shared dev account password}"
exec node server.js
