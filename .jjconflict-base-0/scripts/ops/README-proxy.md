# etrip 出海代理 (mihomo)

香港 IP 被 Anthropic 地区封锁；生产若要开 Claude/Fable 官方通道，其流量需经此代理。

- 服务: `systemctl {status,restart} mihomo` (systemd, Restart=always)
- 代理口: `http://127.0.0.1:7897` (仅回环)
- 配置: `/etc/mihomo/config.yaml` (US×3 + JP×3, url-test 自动选优, 探针 api.anthropic.com)
- 验证: `curl -x http://127.0.0.1:7897 https://api.anthropic.com/v1/models -H 'x-api-key: x'` → 401 (通)

## 让 Claude profile 走它
生产 .env 里给 Anthropic 通道加 `HTTPS_PROXY=http://127.0.0.1:7897`
(仅 claude/fable profile 需要; 智谱通道直连, 不要全局设代理)。
节点来自个人订阅, 仅本机自用。
