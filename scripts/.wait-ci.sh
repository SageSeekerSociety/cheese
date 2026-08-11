#!/usr/bin/env bash
# 等 cheesex/50766c66 上出现 github-actions 的 check-suite（PR 冲突消除后才可能出现）
for i in $(seq 1 40); do
  T=$(/work/backend/sandbox/cheese gh-token 2>/dev/null)
  OUT=$(GH_TOKEN="$T" gh api "repos/SageSeekerSociety/cheese/commits/cheesex%2F50766c66/check-suites" \
        --jq '.check_suites[] | "\(.app.slug) \(.head_sha[0:8]) \(.status)/\(.conclusion)"' 2>/dev/null)
  echo "[$i] $(echo "$OUT" | tr '\n' ' ')"
  if echo "$OUT" | grep -q "^github-actions"; then
    echo "RESULT: github-actions suite 已出现 —— PR 冲突已消除，CI 触发了"
    exit 0
  fi
  sleep 30
done
echo "RESULT: 20 分钟内仍无 github-actions suite —— PR 很可能仍与 GitHub main 冲突"
exit 1
