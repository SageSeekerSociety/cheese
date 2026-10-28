# Figma 稿

用户丢来一个 figma.com 的链接（或一段设计说明）让你照着做页面时读这一份。

## token 从哪来

稿子多半是私有的，读它要一个 token。你手上没有，也不该去找：由用户在**项目设置 → 环境变量**
里加一条 `FIGMA_TOKEN`（Figma 自己在 Settings → Security → Personal access tokens 生成）。
读不到 token 时**如实说**「要先把 FIGMA_TOKEN 加进项目环境变量、重开会话」，不要拿别的 key 顶替、
不要绕道去网页搜这份稿子。

## 三个子命令

脚本在 `scripts/figma.py`，只用标准库，直接 `python3` 跑：

```bash
SKILL="<这个技能的目录>"   # 加载 showcase 技能时给出的那个目录

# 结构：各页面下的顶层画板，名字、id、尺寸
python3 "$SKILL/scripts/figma.py" file "https://www.figma.com/design/<key>/<名字>"

# 细节：一棵子树，类型、尺寸、填充色、文字与字号
python3 "$SKILL/scripts/figma.py" node "https://www.figma.com/design/<key>/<名字>?node-id=12-34"

# 把一块画板导成图（png 或 svg），落到本地
python3 "$SKILL/scripts/figma.py" export "https://www.figma.com/design/<key>/…?node-id=12-34" \
    --format png --out 稿.png
```

网址里的 `node-id=12-34` 脚本会自己认出来（归一成 API 要的 `12:34`）；一次要看好几块就分别读。
`export` 出来的图**用 Read 打开看一眼**再动手——配色、间距、层次，看图比读属性快。

## 照着做，不是搬过来

稿子是**需求**，不是成品：

- 要读出来的是那些决定——几个十六进制配色、字号阶梯、间距与栅格、信息层次。把它们落进本技能的
  模板或自己写的设计方案里（`references/design-system.md`），页面照样本就中文可读、窄屏可看。
- 导出的图是位图，**不要**当页面里的图片贴进去；里面的图形用 HTML/CSS/SVG 重画。
- 稿子里读不到的（真实数据、真实链接、交互）向用户要，不要编。
- 稿子只有一屏时，别把它当成「整页就这么长」：页面该有的头、尾、空态自己想清楚。
