# 起步骨架：Python 与 TypeScript

两份骨架只给到「跑起来、挂上工具、能被 inspector 看见」。SDK 的完整 API 以上游为准，拿不准就取它：

- Python：`https://raw.githubusercontent.com/modelcontextprotocol/python-sdk/main/README.md`
- TypeScript：`https://raw.githubusercontent.com/modelcontextprotocol/typescript-sdk/main/README.md`
- 协议本体：`https://modelcontextprotocol.io/sitemap.xml`（每页加 `.md` 后缀拿 markdown）

两边都按 **Streamable HTTP** 起服务：芝士只接远程 `http`/`sse`，local stdio 进不了项目声明。

## Python（`MCPServer`，官方 `mcp` 包）

```bash
uv add mcp            # 或 pip install mcp —— 现在默认拿到的就是 v2
```

```python
from typing import Annotated

from mcp.server import MCPServer     # v2：FastMCP 已改名 MCPServer
from pydantic import Field

mcp = MCPServer("jira_mcp")          # 服务名，别写版本号


@mcp.tool(annotations={"readOnlyHint": True})
async def jira_list_issues(
    project: Annotated[str, Field(description="项目 key，如 ENG")],
    limit: Annotated[int, Field(ge=1, le=100, description="一页几条")] = 20,
    offset: Annotated[int, Field(ge=0)] = 0,
) -> dict:
    """列出一个项目里的 issue，按更新时间倒序。要查 issue 列表时用它。"""
    rows, total = await client.list_issues(project, limit=limit, offset=offset)
    return {
        "items": rows,
        "count": len(rows),
        "total_count": total,
        "has_more": offset + len(rows) < total,
        "next_offset": offset + len(rows),
    }


if __name__ == "__main__":
    # 监听地址从构造函数搬到了 run()：v2 起 MCPServer("x", port=9000) 是 TypeError。
    mcp.run(transport="streamable-http", host="0.0.0.0", port=8000)  # 缺省挂 /mcp
```

函数名 = 工具名，docstring = 触发说明（写「什么时候用」，不写「它是什么」）。v2 起监听地址不再是构造函数的 `settings.host` / `.port`，而是 `run(transport="streamable-http", host=…, port=…)` 的入参；上线前设成平台能到的那一个。

## TypeScript（`@modelcontextprotocol/sdk`）

TypeScript SDK 的 v2 拆成了若干包（`@modelcontextprotocol/server`、`/client`、`/node` …），单包 `@modelcontextprotocol/sdk` 是仍在维护的 v1，下面这段按它写；上 v2 就按上游 README 换 import 路径。

```bash
npm i @modelcontextprotocol/sdk zod
```

```ts
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import { createServer } from "node:http";
import { z } from "zod";

const server = new McpServer({ name: "jira-mcp-server", version: "0.1.0" });

server.registerTool(
  "jira_list_issues",
  {
    title: "列 issue",
    description: "列出一个项目里的 issue，按更新时间倒序。要查 issue 列表时用它。",
    inputSchema: {
      project: z.string().describe("项目 key，如 ENG"),
      limit: z.number().int().min(1).max(100).default(20),
      offset: z.number().int().min(0).default(0),
    },
    annotations: { readOnlyHint: true },
  },
  async ({ project, limit, offset }) => ({
    content: [
      { type: "text", text: JSON.stringify(await listIssues(project, limit, offset)) },
    ],
  })
);

// 无状态：每个请求一份 transport，不存会话，横向扩展最省事
const transport = new StreamableHTTPServerTransport({ sessionIdGenerator: undefined });
await server.connect(transport);
createServer((req, res) => void transport.handleRequest(req, res)).listen(8000);
```

## 自己先看一眼

- `npx @modelcontextprotocol/inspector` 起一个网页客户端，连上你的 URL，点着试每个工具、看原始返回。
- `python3 scripts/smoke.py <url>` 是不用浏览器的那条：握手 + `tools/list`，加 `--call` 真调一次。接进项目之前必须绿。

## 服务端要自己扛的事

- **输入校验**：路径不许穿越、URL 和外键要校验、长度和范围要卡。schema 校验（Pydantic / Zod）是底线，不是全部。
- **密钥**：只从环境变量读，进代码即错；启动时校验，缺了就说清缺哪一个。
- **日志走 stderr**：stdout 是协议通道，写别的会把消息撕开。
- **只读工具不许改东西**：`readOnlyHint: true` 是承诺，不是装饰——标错了，客户端会放心地重试和并行。
