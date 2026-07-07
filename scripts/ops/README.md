# CheeseX 生产运维手册 (etrip)

服务器: 阿里云香港 `ssh etrip` (root)，公网 IP 8.217.1.152。
入口: **http://8.217.1.152:8080**（80/443 被 Cheese 1.0 老栈 `cheese_prod_*` 占用，绝对不动）。

部署形态:

| 组件 | 位置/方式 |
|---|---|
| 代码 | `/opt/cheesex/`（rsync 自本地工作树，无 .git） |
| 后端 | systemd `cheesex.service` → uv run uvicorn，127.0.0.1:8099 |
| 前端 | 本地构建的 `frontend/dist`，Caddy file_server |
| Caddy | apt 安装，`/etc/caddy/Caddyfile`，监听 :8080 |
| PG 17 | docker `cheesex-pg`，127.0.0.1:5433，数据卷 `/opt/cheesex-data/pg` |
| PG 密码 | `/opt/cheesex-data/pg.pass`（.env 里的 DATABASE_URL 引用同一密码） |
| 后端配置 | `/opt/cheesex/backend/.env`（chmod 600） |
| 沙箱镜像 | `cheesex-agent-sandbox:latest`（backend/sandbox/Dockerfile，服务器本机构建） |
| 宿主依赖 | uv (`/root/.local/bin`)、jj (`/usr/local/bin/jj`)、git identity |
| 备份 | cron 每日 03:30 → `/opt/cheesex/ops/pg-backup.sh` |

## 1. 重启

```bash
systemctl restart cheesex          # 后端
systemctl reload caddy             # Caddy (改配置后)
docker restart cheesex-pg          # 数据库（一般不需要）
# 验证:
curl -s http://127.0.0.1:8080/api/projects | head -c 100
```

## 2. 看日志

```bash
journalctl -u cheesex -f                 # 后端实时日志
journalctl -u cheesex --since "-1 hour"  # 最近一小时
journalctl -u caddy -f                   # Caddy
docker logs cheesex-pg --tail 50         # PG
tail /opt/cheesex-data/backups/backup.log  # 备份日志
ls /opt/cheesex/logs/                    # 沙箱 shim 调试日志 (sbx-debug.log)
```

## 3. 备份 / 恢复

备份: cron 每日 03:30 跑 `/opt/cheesex/ops/pg-backup.sh`，产物
`/opt/cheesex-data/backups/cheesex-YYYYmmdd-HHMMSS.sql.gz`，保留 14 天。
手动备份: 直接执行 `/opt/cheesex/ops/pg-backup.sh`。

恢复（危险操作，先确认！）:

```bash
systemctl stop cheesex
gunzip -c /opt/cheesex-data/backups/cheesex-XXXX.sql.gz | \
  docker exec -i cheesex-pg psql -U cheesex -d cheesex
systemctl start cheesex
```

（如需彻底重建库: 先 `dropdb`/`createdb` 再灌入。）

## 4. 更新部署

本地 (MacBook, 仓库 tmp/cheesex) 执行:

```bash
# 1) 前端本地构建
cd frontend && npm run build && cd ..
# 2) rsync 代码 + dist（注意 exclude 列表，服务器端 ops/ 不在源里所以要排除）
rsync -az --delete --exclude='.git' --exclude='.workspaces' --exclude='.viking' \
  --exclude='node_modules' --exclude='dist' --exclude='tmp_*' --exclude='logs' \
  --exclude='.env' --exclude='.porkbun-state.json' --exclude='.venv' \
  --exclude='ops' --exclude='uploads' --exclude='preview.db' \
  ./ etrip:/opt/cheesex/
rsync -az --delete frontend/dist/ etrip:/opt/cheesex/frontend/dist/
# 3) 服务器端: 依赖 + 迁移 + 重启
ssh etrip 'export PATH="$HOME/.local/bin:$PATH"; cd /opt/cheesex/backend \
  && uv sync --frozen && uv run alembic upgrade head \
  && systemctl restart cheesex && sleep 3 \
  && curl -sf http://127.0.0.1:8080/api/projects >/dev/null && echo DEPLOY-OK'
# 4) 沙箱镜像有改动时:
ssh etrip 'cd /opt/cheesex/backend/sandbox && docker build -t cheesex-agent-sandbox:latest .'
```

## 已知事项

- **域名**: okcheese.com 到手后，80/443 需要一个前置反代统一路由两个栈
  （老栈 cheese_prod_frontend 现占 80）——单独规划，别直接改 Caddyfile 抢 80。
- **安全组**: 8080 入方向须在阿里云控制台放行（服务器侧 ufw inactive、iptables 全开）。
- **apt 源**: 原内网源 mirrors.cloud.aliyuncs.com 失效，已切 mirrors.aliyun.com
  （原文件备份 /etc/apt/sources.list.bak-cheesex）。
- **老栈** (只读侦察结论): `/opt/cheese-deploy/{deploy.sh,docker-compose.prod.yml}`，
  镜像 ghcr.io/sageseekersociety/cheese-backend-py/{backend,frontend}:main，
  restart=unless-stopped。另有 7 周前退出的 cheese_py_* / staging_* 容器（未清理）。
- 本手册与 ops 脚本双份维护: 服务器 `/opt/cheesex/ops/` ↔ 仓库 `scripts/ops/`。
