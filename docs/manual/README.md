# 用户说明书

**这个目录里的每一页都是写给平台的使用者看的**，不是写给改这个仓库的人看的。`docs/` 下别的文档都是后者，两拨读者混在一页里会两边都不好用。

站点用 **VitePress** 构建（`.vitepress/config.mts`），发布到 `okcheese.com/docs`。

```bash
task docs:dev      # 本地边写边看，改一个字就刷新
task docs:build    # 构建 + 产出 llms.txt 和每页的 .md
```

**加一页 = 新建一个 `.md`。** 侧边栏是扫这个目录扫出来的，不用改配置；`order` 决定它排在第几个。

## 它是干什么用的，决定了它长什么样

说明书有两个读者，而**第二个是芝士**。用户在房间里问「我怎么连自己的仓库」，芝士要能答出来，并给出这一节的网址。这一条约束推出了下面所有规矩——离开它，这些规矩看起来都像洁癖。

## 规矩一：每个标题自己声明 slug

```markdown
## 第二步：连上你已有的仓库 {#connect-repo}
```

网址由 slug 生成，**不由标题文字生成**。这样标题怎么改都行，`/docs/quickstart#connect-repo` 一直有效。

如果让站点从中文标题自动生成锚点，那么有人把「连接仓库」改成「连上你已有的仓库」的那一刻，所有发出去过的链接同时失效——不会有任何构建报错，唯一的症状是读者落在页顶，不知道那一节去哪了。这正是最该防的那种失效：**它坏掉的样子和没坏一模一样。**

slug 用小写字母、数字和连字符，全页唯一。`check-manual-anchors.py` 会拦住不合规的。

## 规矩二：每一节的第一句话就是这一节的摘要

站点会发布一份 `llms.txt`——Anthropic、OpenAI、Stripe、Vercel、Linear 都用这个约定把文档喂给模型（2026-09-09 逐个实测确认）。它是一份目录，每条一行：标题、`.md` 地址、以及这一节开头的第一句话。模型就是靠这一句判断"这一节能不能回答眼前这个问题"。

所以第一句话要能独立说明这一节讲什么，不要用「首先我们来看看……」开头，也不要拿列表开场——一条列表项是关于某一行的事实，被当成整节的摘要就是错的。守卫会因为一节没有开头句而失败。

## 规矩三：给模型的那一份是构建出来的，仓库里不留副本

```bash
task docs:build                                   # VitePress + scripts/emit_llms.py
python3 .claude/scripts/check-manual-anchors.py   # CI 跑的检查
```

构建产出三样东西：VitePress 出的 HTML（导航、搜索、暗色、移动端都归它）、每页一个 `.md` 双胞胎（`/docs/quickstart.md` 就是 `/docs/quickstart` 的原文），以及站点根上的 `llms.txt`。后两样由 `scripts/emit_llms.py` 生成。

每份 `.md` 的开头都指回 `llms.txt`，这是抄 `code.claude.com` 的做法：模型不管从哪一页进来，都该知道完整目录在哪。

**为什么不在仓库里存一份索引**：存了就会落后。清单一旦落后于文档，模型不会说"我这儿没有"，它会答一个最接近的东西，配一个已经打不开的网址——而且没有任何人会收到告警。构建时现生成，它就不可能和文档不一致。

守卫管的是构建之前的事：所有标题都有显式且合法的 slug、锚点不重复、每一节都有摘要句、文档内部链到的锚点真实存在。它在 pre-commit 和 CI 里。

## 规矩四：给了链接，也要把答案说完

写进芝士的指令里的原则，也是写这些页面时该守的：**答案要能独立看懂，网址是"想看更多"，不是"自己去看"。** 用一个链接把人打发走是最讨人厌的客服模式，而模型特别容易滑进去。

## 写什么、不写什么

**写**：用户在界面上做得到的事，以及做这件事时需要建立的心智模型（采纳为什么等于合并、房间和活是什么关系）。

**不写**：`cheese` 命令行、沙箱、工作区路径、数据库、任何用户看不到也用不了的东西。那些是芝士干活的方式，写进说明书只会让读者去找一个不存在的入口。

**尤其不写**：还在 issue 里争的设计。说明书描述的是现在真实的样子，写一个还没落地的形态，读者照着做会发现界面上没有那个按钮。

## 每一页的 frontmatter

```yaml
---
title: 快速开始      # 侧边栏和 llms.txt 里显示的名字
slug: quickstart    # 地址：/docs/quickstart
order: 1            # 侧边栏里排第几
---
```

`index.md` 是首页（VitePress 的 home 布局），`README.md` 是你正在读的这份规矩——两个都不算说明书的一页，不进侧边栏也不进 `llms.txt`。

## 站内链接怎么写

写成 `/concepts#task` 这种**不带 `/docs` 前缀**的形式。VitePress 自己会补上前缀（`base: '/docs/'`），源码里再写一遍会变成 `/docs/docs/…`，它的死链检查会当场拦下。给模型的那份 `.md` 里，这些链接会被改写成完整地址，因为它是被单独抓走的、身边没有前缀可依。

## 发布到 okcheese.com/docs（还没做）

前端就是 Vite 打出来的静态文件塞进 nginx，所以文档只要落进 `dist/docs/` 就能用。要动的是三处：

1. 构建时把 `docs/manual/.vitepress/dist` 放进 `frontend/public/docs/`——`public/` 是 Vite 原样搬运的目录，不用改 Dockerfile。
2. `frontend/nginx.conf` 的 `try_files` 加一段 `$uri.html`。VitePress 开了 `cleanUrls`，`/docs/quickstart` 对应的文件是 `quickstart.html`；不补这一段，请求会掉进 SPA 的兜底 `index.html`，读者看到的是应用而不是文档。
3. 同一个文件里给 `.md` 声明 `text/markdown`。nginx 默认不认这个后缀，会当二进制让浏览器下载；而且 Claude Code 的抓取有一条"content-type 是 text/markdown 就直接读原文"的快路，类型写对才吃得到。

**别忘了第 1 步会静默失败**：镜像照样构建成功，只是 `/docs` 变成 404。`frontend/scripts/check-static-assets.sh` 是现成的地方，加一条"`docs/index.html` 必须存在"，就能把它变成一次响亮的失败。

## 页面

| 页面 | 给谁看 |
|---|---|
| [`quickstart.md`](quickstart.md) | 第一次用的人。一条路走到底：建项目 → 开房间 → 说一句话 → 采纳合并。 |
| [`concepts.md`](concepts.md) | 走过一遍之后想把词理清楚的人。项目 / 房间 / 芝士 / 活 / 看板 / 实况文档 / 卡 / 算力。 |
| [`working-with-cheese.md`](working-with-cheese.md) | 已经会用、但交出去的活总不太对的人。这是这个产品真正的门槛。 |

## 还没做

- **发布**：见上面那一节，三处改动都写清楚了，等拍板。
- **Ask AI**：那五家的文档站都有一个站内问答框。后端本来就有 OpenAI 兼容的 LLM 通道（`OPENAI_BASE_URL` 可指向任意中转），文档量小到整份能进上下文，不需要检索。**缺的只是 `OPENAI_API_KEY`**，配置里是空的。
