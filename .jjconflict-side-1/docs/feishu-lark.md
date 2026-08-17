# 飞书 / lark-cli 配置（团队通用）

团队都在**同一个飞书 org**（租户域名 `acnxgqu0961c.feishu.cn`）。所有飞书自动化——云文档、云空间、知识库 Wiki、多维表格——都经 `lark-cli` 完成。

## 铁律：飞书操作一律走 `cheese` profile

```bash
lark-cli <子命令> ... --profile cheese
```

`cheese` = org 内的项目 app `cli_a97ca79454785bd5`，已申请 drive + docs 等权限。**全员共用这一个 app id**，各自授权出自己的 user token：

```bash
lark-cli auth login  --profile cheese    # 首次 / token 过期后授权
lark-cli auth status --profile cheese    # 查 token 是否有效
```

> - 默认选中的 profile **不一定**是 cheese；每条命令都显式带 `--profile cheese`，别赌默认值。
> - 本机若还有别的 profile（个人账号），团队飞书操作一律忽略它们。
> - app id 不是密钥（相当于 client_id），可入 git；app secret 与 token 不入 git。

## 托管凭据机器（token 由外部喂入）

有些机器**不走** `auth login`，token 由外部守护进程定时刷新并注入（如 Bitwarden relay + wrapper）。在这类机器上 wrapper 会自述：`lark-cli auth status` 做**真实健康检查**（逐 profile 发活探测）并解释架构；`auth login` / `lark-cli update` 被拦截并给出指引（re-login 会分叉 refresh 链，update 会覆盖 wrapper）。**相信 CLI 的输出**——`ok:false` 的 error 里写明了原因和该去哪修；它不代表飞书被 block。

## 排障表

| 现象 | 真实原因 | 处理 |
|------|---------|------|
| `no_token` / `403` / `no authority` | 用错 profile | 加 `--profile cheese` 重试 |
| auth 命令报 `unsupported` / `credentials are provided externally` | 托管凭据机器，设计如此 | 跑 `lark-cli auth status` 看活探测结果，或直接发数据调用 |
| `strict mode is "user"` | 本机策略禁用 bot 身份 | 用 user 身份（默认）即可，勿切 strict-mode |
| `invalid access token` 偶发一次 | 旧 token 被刷新失效（缓存窗口） | 原样重试一次即可（wrapper 通常已自动重试） |
| token 过期且 `auth login` 可用 | 普通机器 user token 到期 | `lark-cli auth login --profile cheese` |
