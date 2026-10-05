# 飞书 / lark-cli 配置（团队通用）

团队都在**同一个飞书 org**（租户域名 `acnxgqu0961c.feishu.cn`）。所有飞书自动化——云文档、云空间、知识库 Wiki、多维表格——都经 `lark-cli` 完成。

## 飞书知识库

产品方向、服务对象、合作关系、公司信息和团队在产品上的决定都记在团队飞书的知识库“知是平台”里，仓库里只有代码和开发文档。知识库首页是“知是知识库”，它下面的两篇愿景《愿景：让好奇心拥有改变世界的力量》和《众智成事：我们为什么做 AI+教育》是产品定位和官网文案的出处；现行材料在“工作资料”和“新人入门”下；“过往方向与旧版本”下是已被替代的方向和旧版本，不作为现行依据。

```bash
lark-cli wiki +space-list --profile cheese                                    # 找到「知是平台」的 space_id
lark-cli wiki +node-list  --profile cheese --space-id <space_id>              # 首页
lark-cli wiki +node-list  --profile cheese --space-id <space_id> --parent-node-token <首页 node_token>
lark-cli docs +fetch      --profile cheese --doc <页面链接>
```

`lark-cli docs +search` 按标题找页面，中文查询用完整标题更容易命中。

## 首次接入

```bash
task lark:setup
```

脚本先安装 `lark-cli`（npm 包 `@larksuite/cli`），再注册共用的 `cheese` app，最后用你自己的飞书账号在浏览器里登录。注册时要粘贴 app secret，它不在 git 里，向团队成员私下要这个 secret。已经能通过 `--profile cheese` 访问飞书的机器，脚本检查后直接退出。飞书 skill 随仓库提供，不需要另外安装。

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

## Updating project skills

Lark skills live in `.agents/skills/`, with directory links in `.claude/skills/`
for Claude Code. Codex reads `.agents/skills/` directly. Commit any local skill
changes, then run `bash scripts/update-lark-skills.sh` to refresh the complete
official collection and `skills-lock.json` in this checkout. Review and commit
the resulting diff. Each Git worktree uses the skills in its own checkout.

Keep these skills project-scoped. The script omits `--global`; `lark-cli update`
also installs skills and can recreate global entries. Upgrade the CLI separately,
following the installed wrapper's guidance on machines with managed credentials.
