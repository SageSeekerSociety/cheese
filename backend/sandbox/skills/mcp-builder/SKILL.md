---
name: mcp-builder
description: 要写一个 MCP server、把某个外部 API 接给 AI 用时先加载；只调用现成的 MCP 服务不用。
---

把一件外部能力接成模型能稳稳用的工具：写一个 MCP server，再在项目里声明它。

## 什么时候做

用户说「把我们的 XX 系统接给芝士」「让 AI 能查/改 XX」时：

- 有一个装得上的现成 MCP 服务 → 直接在项目里**声明**它，不要重写一份。
- 内部 API、没有官方 server、或官方 server 缺了你真正要的那几个操作 → 写一个。
- 只是这一轮的活，用完就完 → 不用接 MCP，写个脚本调它就行。

## 四步

**先定工具，再写代码**——返工都在这一步省下来。

1. **研究**：把这个 API 真正要完成的那几件活列出来，一件活对应一个工具。宁可覆盖全，别只留一条捷径：模型能组合基础操作，但补不上你没给的能力。每件活的入参、返回、报错一起定清楚。
2. **实现**：读 `references/sdk.md`，照你的语言选 Python 或 TypeScript。每个工具都要输入校验、能指路的报错、分页。
3. **自检**：`python3 scripts/smoke.py <url>` 对跑起来的 server 走一遍握手和 `tools/list`；加 `--call '{"工具":"名","参数":{...}}'` 真调一次。**跑通之前不要接进项目**。
4. **评测**：按 `references/evaluation.md` 写 10 道题，自己拿这台 server 解一遍。答不上来，是工具没设计好，不是题太难。

## 工具怎么起名

一个会话里会同时挂着好几个 server，所以名字带服务前缀：`{服务}_{动作}_{对象}`，snake_case，动作在前——`jira_create_issue`、`linear_list_teams`。别用 `send_message` 这种放之四海皆准的名字。

名字和说明是模型唯一的线索，说明写偏了，模型就会该用的时候不用、不该用的时候乱用：

- **说明是触发器**，和技能一样：写「什么情况下用它」，不只写「它是什么」。
- **返回要小**：列表默认 20–50 条，带 `has_more` / `next_offset` / `total_count`，不要把整张表倒出来。
- **报错要指路**：说清哪一项不对、下一步怎么改。工具失败放进结果的 `isError`，别掀成协议级错误。
- 给注解：只读的 `readOnlyHint: true`，会改东西的 `destructiveHint: true`，重复调用无副作用的 `idempotentHint: true`。注解只是提示，不是安全保证——真正的闸在服务端。

## 接进芝士（这一段和别处不一样）

server 写完还差一步：项目要**声明**它，会话里才见得到。声明写在**默认分支**的 `.mcp.json`：

```json
{
  "mcpServers": {
    "jira": { "type": "http", "url": "https://mcp.example.com/jira" }
  }
}
```

芝士这边的规矩都是平台侧既定的，照做即可：

- **只读默认分支的 `.mcp.json`**，不读工作区那份。房间里的 agent 能改自己的 checkout，从那儿取 URL 等于让它把某个名字指向任意主机、把项目的 token 送过去。改完**提交进默认分支**才生效。
- **平台只接 `http`（Streamable HTTP）和 `sse`**；带 `command` 的 stdio 项是执行机那一层的事，项目声明接不上。所以 server 要部署在**平台能连到的地方**——agent 的机器不能替你转发。
- **`${VAR}` 会展开**，`${VAR:-默认}` 给默认值。值在项目设置的 **MCP 密钥**里填；token 只写变量名，绝不写进 `.mcp.json`（它进的是仓库）。
- **没写 `headers` 时，平台按 OAuth 2.1 自己去授权**：发现、动态注册、回调都在平台侧，你不用实现。写了 `headers` 就按 headers 发。
- **一个名字只指一个 host**。同名时以项目的 `.mcp.json` 为准，agent 类型文件里的让位。
- 名字 `native` 是平台留给房间自己的文件操作的，不要占用。

**「配好了」= 会话里真的调得通**，不是 `.mcp.json` 提交成功。在项目设置里连上它、看到握手通过，再让人用。

## 参考

- `references/sdk.md` — Python（FastMCP）与 TypeScript SDK 的起步模板、工具签名与注解写法。
- `references/evaluation.md` — 最后那道评测：10 道题的规矩、XML 格式、怎么算通过。
