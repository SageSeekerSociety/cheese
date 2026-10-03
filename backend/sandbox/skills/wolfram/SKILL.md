---
name: wolfram
description: 用户问一个可以算出来的问题（数值、单位换算、距离、积分、回归、微分方程、优化）；要给一个数据集的结论或图；要查一条精选数据（国家、化学元素、行星、基因、公司）；要把口语化的科研需求写成一条准确的 Wolfram Language 表达式。说明工具在哪、为什么答案必须由计算得出而不是由记忆写出、图在一次性调用时怎么取。
---

# Wolfram 计算

## 为什么要有这个技能

**答案要由计算得出**：能算的题一律算一遍，不要凭记忆写。这个技能把 Wolfram 官方 MCP 接到这台机器上——查函数名、查语法、执行表达式、判真假、查精选数据。

**算不出来就说算不出来**。不要用网页搜到的数、也不要用记忆里的数冒充计算结果。

## 工具在哪

脚本跟着平台的原生技能一起发到这台机器上，文件是 `scripts/wolfram_mcp.py`。每次新开一条
shell 都要先定位它：

```bash
SKILL=skills/wolfram                                          # 项目 checkout 里的位置，先试这个
[ -d "$SKILL" ] || SKILL="$CLAUDE_CONFIG_DIR/skills/wolfram"   # 不在 checkout 就在会话的技能目录里
uv run --quiet --script "$SKILL/scripts/wolfram_mcp.py" health # 验 Wolfram 通道通不通
```

`health` 里的 `wolfram.available` 为 `false` 时**如实说做不到**，不要绕道去搜网页。

## 怎么用

**表达式由你自己写**：把用户的话翻成一条 WL 表达式，交给 `wolfram_execute` 跑。只调一层：

```bash
S="$CLAUDE_CONFIG_DIR/skills/wolfram/scripts/wolfram_mcp.py"   # 定位一次，后面复用
uv run --quiet --script "$S" call wolfram_execute '{"code":"{N[Sqrt[129],20], Plot[Sqrt[x],{x,0,129}]}"}'
```

第一个位置参数是工具名，第二个是入参 JSON。`call` 不带工具名会把全部可用工具列出来。
一次调用返回文本；有图时另外打印图片文件路径。

| 你要做的 | 工具 | 入参 |
|---|---|---|
| Wolfram 通道通不通 | `wolfram_health` | `{}` |
| 先判断该不该写 WL | `wolfram_probe` | `{"query":"用户原话"}` |
| 不确定某函数怎么写 | `wolfram_context` | `{"query":"GeoDistance"}` |
| 查一组函数名是不是真的 | `wolfram_check_symbols` | `{"names":["Plot","FakeFn"]}` |
| 只扫安全不执行 | `wolfram_safety_scan` | `{"code":"..."}` |
| 执行一条 WL 表达式 | `wolfram_execute` | `{"code":"..."}` |
| 看有哪几种表示（**只作参考，不进答案**） | `wolfram_alpha` | `{"query":"..."}` |

**写不出一次就对**：拿不准函数名先 `wolfram_context` 查官方文档，别猜；`wolfram_execute`
返回 `$Failed` 或报错就按原话改表达式再跑一次。

**一次把有用的东西都取回**：主结果、`N[...]` 数值近似、真正有用的图，用一个 `{...}` 一起要，
不要为同一件事分好几次调用。

**先算再讲**。外部的数（距离、气温、元素性质、行星数据）用 `Entity`、`Quantity`、
`GeoDistance`、`WeatherData` 这些精选数据函数算，不要用你记得的那个数。

## 图

图是结论的一部分。`call` 会把图片存成文件并打印路径：

```
[[1]] 图片文件：/…/wolfram/images/3f9a1c22.png
```

**用 Read 打开那个文件再看**，然后照图里真实出现的内容讲（坐标轴、量级、趋势），不要把
路径当结果输出。图里没出现的不要编。用户要多张图就一次多画几张（`GraphicsGrid` 或多条
绘图对象放进同一个 `{...}`），别拿同一张图充数。

## 不要做的事

- 不要用 `Import`、`ReadList`、`Run`、`OpenWrite`、`URLRead` 这类符号——文件、进程、网络
  一律被安全闸门拒掉。要外部数据就用 `Entity`、`Quantity`、`WeatherData` 这些精选数据函数。
- 不要把上一条命令的输出格式当成接口来解析。`call` 打的是给人看的结果，要结构化数据就
  在你的 WL 表达式里让它返回 `{...}` 或 `Association`，Wolfram 会按它的方式序列化。
- 不要在用户没问的时候解释这套流程。
