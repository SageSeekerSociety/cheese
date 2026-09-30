#!/usr/bin/env python3
"""生成后台布局方案 B 的中文 PDF。图片以 base64 内联，产物自包含。"""
import base64
import pathlib
import subprocess
import sys

SHOTS = pathlib.Path('/var/tmp/shots')
OUT_HTML = pathlib.Path('/var/tmp/admin-layout-b.html')
OUT_PDF = pathlib.Path('/var/tmp/后台管理布局统一-方案B.pdf')

# (文件名, 图注)
IMAGES = [
    ('light-1440-queue.png', '浅色 1440 · 反馈队列（列表）'),
    ('light-1440-models.png', '浅色 1440 · 模型管理'),
    ('light-1440-spaces.png', '浅色 1440 · 空间申请'),
    ('dark-1440-models.png', '深色 1440 · 模型管理'),
    ('dark-1440-spaces.png', '深色 1440 · 空间申请'),
    ('narrow-390-queue.png', '390 窄屏 · 反馈队列'),
    ('narrow-390-models.png', '390 窄屏 · 模型管理'),
    ('long-content-spaces-390.png', '390 窄屏 · 长内容下的空间申请'),
    ('wide-1920-queue.png', '1920 宽屏 · 反馈队列（1440 是内容列上限）'),
    ('wide-1920-models.png', '1920 宽屏 · 模型管理'),
    ('state-loading-queue.png', '加载态 · 反馈队列'),
    ('state-error-queue.png', '出错态 · 反馈队列（「重试」按钮 #1859 就有，本轮不动）'),
    ('long-content-spaces.png', '长内容 · 空间申请（长标题与长简介）'),
]

# 本轮补的 390 窄屏七页 × 浅深两色。顺序按路由，浅色一排、深色一排。
PAGES_390 = [
    ('queue', '反馈队列'),
    ('dashboard', '看板'),
    ('feature-stats', '功能数据'),
    ('models', '模型管理'),
    ('spaces', '空间申请'),
    ('members', '成员管理'),
    ('integrations', '飞书应用'),
]
IMAGES_390 = [
    (f'narrow-390-{tone}-{key}.png', f'390 {"浅色" if tone == "light" else "深色"} · {label}')
    for tone in ('light', 'dark') for key, label in PAGES_390
]


SHOTS_390 = pathlib.Path('/var/tmp/shots-390')
SHOTS_COPY = pathlib.Path('/var/tmp/shots-copy')

# 出错态文案对照页的逐行图。编号跟产品里的真实调用点对齐：#1–#10 是十处读失败。
COPY_ROWS = [
    ('row-n5.png', '#5 模型管理 · 模型段（结构甲：原话当标题）'),
    ('row-n6.png', '#6 模型管理 · 额度段（结构甲）'),
    ('row-n7.png', '#7 模型管理 · 操作记录段（结构甲）'),
    ('row-n9.png', '#9 成员管理（结构甲）'),
    ('row-n1.png', '#1 反馈队列 · 列表视图（结构乙：原话挂 title，悬停才见）'),
    ('row-n2.png', '#2 反馈队列 · 表格视图（结构乙）'),
    ('row-n4.png', '#4 功能数据（结构丙：原话丢了）'),
    ('row-n8.png', '#8 空间申请（结构丙）'),
    ('row-n10.png', '#10 飞书应用（结构丙）'),
    ('row-n3.png', '#3 看板（结构丁：原话作说明行 —— 就是建议的形状）'),
]
COPY_WRITE = ('write-table.png', '五处写失败横幅 W1–W5：都没有按钮，本轮不新加入口')

def img_path(name: str) -> pathlib.Path:
    # 24 张沿用图在 /var/tmp/shots，390 浅深七页在 /var/tmp/shots-390，
    # 本轮出错态对照页的逐行图在 /var/tmp/shots-copy
    for d in (SHOTS, SHOTS_390, SHOTS_COPY):
        p = d / name
        if p.exists():
            return p
    sys.exit(f'缺图：{SHOTS}/{name} 与 {SHOTS_390}/{name} 都不存在')

def img_tag(name: str) -> str:
    b64 = base64.b64encode(img_path(name).read_bytes()).decode()
    return f'<img alt="{name}" src="data:image/png;base64,{b64}">'


def figures(names):
    out = []
    for n, cap in names:
        out.append(
            f'<figure>{img_tag(n)}<figcaption>{cap}</figcaption></figure>'
        )
    return '\n'.join(out)

def two_up(pairs):
    """浅深并排：同一行的两页放一起，比分开摞更看得出主题有没有串。"""
    half = len(pairs) // 2
    out = []
    for i in range(half):
        left, right = pairs[i], pairs[half + i]
        out.append(
            '<div class="two">'
            f'<figure>{img_tag(left[0])}<figcaption>{left[1]}</figcaption></figure>'
            f'<figure>{img_tag(right[0])}<figcaption>{right[1]}</figcaption></figure>'
            '</div>'
        )
    return '\n'.join(out)


CSS = """
@page { size: A4; margin: 16mm 14mm; }
* { box-sizing: border-box; }
body {
  font-family: "Noto Sans CJK SC", "Noto Sans CJK", sans-serif;
  color: #1a1a1a; line-height: 1.75; font-size: 10.5pt; margin: 0;
}
h1 { font-size: 20pt; margin: 0 0 6px; line-height: 1.35; }
h2 {
  font-size: 13.5pt; margin: 22px 0 8px; padding-bottom: 5px;
  border-bottom: 1px solid #d4d4d4; page-break-after: avoid;
}
h3 { font-size: 11.5pt; margin: 14px 0 5px; page-break-after: avoid; }
p { margin: 0 0 8px; }
ul, ol { margin: 0 0 8px; padding-left: 1.3em; }
li { margin: 0 0 3px; }
table {
  border-collapse: collapse; width: 100%; margin: 8px 0 12px;
  font-size: 9.5pt; page-break-inside: avoid;
}
th, td { border: 1px solid #cfcfcf; padding: 5px 7px; text-align: left; vertical-align: top; }
th { background: #f2f2f2; font-weight: 600; }
.lede {
  background: #f5f5f4; border: 1px solid #e0e0e0; border-radius: 8px;
  padding: 11px 14px; margin: 12px 0 16px;
}
.meta { color: #666; font-size: 9pt; margin: 0 0 14px; }
.note {
  border-left: 3px solid #b45309; background: #fffbeb;
  padding: 8px 12px; margin: 10px 0; font-size: 9.5pt;
}
figure { margin: 10px 0 14px; page-break-inside: avoid; text-align: center; }
figure img {
  max-width: 100%; height: auto; border: 1px solid #d8d8d8; border-radius: 6px;
}
figcaption { font-size: 8.5pt; color: #666; margin-top: 5px; }
code {
  font-family: ui-monospace, "DejaVu Sans Mono", monospace;
  background: #f0f0ee; padding: 0 3px; border-radius: 3px; font-size: 9pt;
}
.two { display: flex; gap: 12px; }
.two figure { flex: 1 1 0; min-width: 0; }
.pb { page-break-before: always; }
"""

HTML = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>后台管理布局统一：方案 B 实现与核验</title>
<style>{CSS}</style></head>
<body>

<h1>后台管理布局统一：方案 B 实现与核验</h1>
<p class="meta">2026-09-30 · 分支 <code>task/d0241114</code> · 审基线 head <code>0c8565785</code> · PR #2206</p>

<div class="lede">
<p><strong>结论</strong>：方案 B 已实现到可评审状态。八个后台页的页头、正文宽度、间距、筛选与操作区、表格卡片、加载/空/出错三态收成一套；<strong>功能和权限一字未改</strong>。</p>
<p>这一版修掉了一个窄屏真缺陷：390 下长空间申请卡的标题与说明被右侧整段裁掉（不是省略号）。修的是 <code>AdminSpacesPage</code> 窄屏列布局的横轴约束，行内动作与功能未动，见第 5.6 节的前后对照。</p>
<p>图片沿用情况：<strong>24 张里 23 张原样沿用</strong>（未空重拍）；<code>long-content-spaces-390.png</code> 因上述修复<strong>重拍</strong>了这一张；另<strong>新增 14 张</strong> 390 窄屏七页 × 浅深两色，补上此前只核过三页的空缺。本 PDF 里出现的每张图都标了它属于哪一类。</p>
<p>第 4 节给的是<strong>出错态文案的修订提案</strong>（十处读失败 + 五处写失败，按真实调用点唯一编号）。<strong>「重试」按钮不是本轮加的</strong>，#1859 就在 <code>main</code> 上；本轮动的是标题与说明行的措辞，以及服务端原话的去向。这些修订<strong>还没写进产品组件，等确认</strong>。</p>
</div>

<h2>1. 三个方案的对照</h2>
<table>
<tr><th></th><th>A：各自维持现状</th><th>B：三层骨架</th><th>C：全套新组件</th></tr>
<tr><td>页头</td><td>每页自己画</td><td>白色页头 + 筛选条同住一条白带</td><td>新建页头组件</td></tr>
<tr><td>正文宽度</td><td>各写各的</td><td>一套 1440 上限，窄屏流式收缩</td><td>同 B，另加栅格</td></tr>
<tr><td>背景</td><td>有白有灰</td><td>灰画布上摆白卡片</td><td>同 B</td></tr>
<tr><td>组件</td><td>不动</td><td>不换，只加共享骨架</td><td>大量替换</td></tr>
<tr><td>功能 / 权限</td><td>无影响</td><td>无影响</td><td>无影响，但改动面易带出问题</td></tr>
</table>
<p><strong>选 B，已获批准。</strong>八页的差别集中在骨架，不在组件。A 不解决问题；C 要换组件，改动面大到容易把功能一起带进来，与「功能和权限不变」冲突。</p>

<h2>2. 三层骨架</h2>
<ol>
<li><strong>白色页头带</strong> — 页头和它下面的筛选条 / 工具行同住一条白带。一条白带只画最下面那一道发丝线，页头自己那道不画。</li>
<li><strong>灰底画布</strong> — <code>--canvas</code>，滚动归这一页自己领。</li>
<li><strong>白卡片</strong> — 卡片用 <code>--line</code> 描边，不带阴影。</li>
</ol>
<p>共享骨架落在 <code>frontend/src/style.css</code>：<code>.admin-page</code>、<code>.admin-page__col</code>、<code>.admin-page__body</code>、<code>.admin-card</code>、<code>.admin-form-card</code>、<code>.page-container--admin</code>。各页把自己的壳类接上去，宽度和断点不再各写一份。</p>

<h2>3. 六处差异，收成一套</h2>
<table>
<tr><th>原来差在</th><th>收成</th></tr>
<tr><td>正文宽度</td><td>1440 内容列上限（宽表 / 看板），窄屏流式收缩；表单 720 上限</td></tr>
<tr><td>页头分隔线</td><td>页头自带一道 <code>--line</code>；与工具行同带时只留最下面那道</td></tr>
<tr><td>背景留白</td><td>灰画布 + 白卡片</td></tr>
<tr><td>筛选与按钮位置</td><td>筛选条在页头那条白带里，操作按钮在同一行右端</td></tr>
<tr><td>指标卡</td><td>一张卡一个指标，列数 6 / 3 / 2 整除，不留孤零零一张</td></tr>
<tr><td>加载 / 出错态</td><td>骨架屏 / 出错居中 + 「重试」</td></tr>
</table>

<h2 class="pb">4. 出错态文案：十处读失败 + 五处写失败（修订待确认）</h2>
<p><strong>先说这一版没改什么。</strong>「重试」按钮<strong>不是本轮加的</strong>：<code>AdminEmptyState</code> 的 <code>action</code> 槽在 #1859 就在 <code>main</code> 上，十处读失败本来都挂着它。本轮分支的 41 个改动文件里<strong>没有</strong> <code>AdminEmptyState</code>、i18n、错误文案的任何一处——全是布局、预览入口与取证脚本。</p>
<p><strong>这一版给出的是待确认的文案修订</strong>，还没写进产品组件。下面用真实组件把「现状」和「建议」并排渲染出来，等确认之后才改产品。</p>
<p><strong>编号口径</strong>：按<strong>每个真实调用点唯一编号</strong>，不按页数算。<strong>#1–#10 是十处读失败结构，W1–W5 是五处写失败横幅，合计 15 个调用点</strong>；它们落在 7 个后台页上（模型管理一页内有 3 段，反馈队列一页内有 2 个视图）。表、图、正文都按这套编号对齐，标题页数与错误实例数分开算，不混。</p>

<h3>4.1 十处读失败：原话去向分四种结构</h3>
<p>十处<strong>全都有「重试」按钮</strong>，按钮本轮不动。真正的差别在「服务端那句原话最后去了哪」，分四种结构，<strong>合计 3+4+2+1=10</strong>：</p>
<table>
<tr><th>结构</th><th>服务端原话的去向</th><th>编号</th><th>处数</th></tr>
<tr><td>甲</td><td><strong>当标题显示</strong> —— 原话常常很长，标题行被撑得不像标题</td><td>#5 #6 #7 #9</td><td>4</td></tr>
<tr><td>乙</td><td><strong>挂在外层 title 属性</strong> —— 只有悬停才看得到</td><td>#1 #2</td><td>2</td></tr>
<tr><td>丙</td><td><strong>丢了</strong> —— 页面只留下一句固定话，原因无处可查</td><td>#4 #8 #10</td><td>3</td></tr>
<tr><td>丁</td><td><strong>直接作说明行</strong>，可见</td><td>#3</td><td>1</td></tr>
</table>
<p><strong>建议的形状只有一种</strong>，取自 #3 看板——<strong>它现在就是这个形状</strong>，是别处照着改的样板：<strong>中性标题「某某加载失败」+ 服务端原话作说明行 + 一颗对应动作的按钮</strong>。原话一律保留，<strong>不用泛化提示吞掉原因</strong>。有「重试」的地方就放「重试」；写失败横幅没有按钮，就写真实的下一步，<strong>不新加入口</strong>。</p>

<h3>4.2 逐处变化清单（唯一编号）</h3>
<table>
<tr><th>编号</th><th>调用点</th><th>组件链</th><th>原话去向</th><th>按钮</th><th>变化</th></tr>
<tr><td>#1</td><td>反馈队列 · 列表视图</td><td>AdminQueueEmpty 壳 → AdminEmptyState</td><td>乙</td><td>有「重试」</td><td>说明行从「检查网络后重试。」换成服务端原话；悬停那句取消（已经看得见了）。标题、按钮不动。</td></tr>
<tr><td>#2</td><td>反馈队列 · 表格视图</td><td>同上，插槽挂在表格上</td><td>乙</td><td>有「重试」</td><td>同 #1。</td></tr>
<tr><td>#3</td><td>看板</td><td>AdminEmptyState（整块）</td><td>丁</td><td>有「重试」</td><td><strong>不动。</strong>已经是建议的形状，它是别处照着改的样板。</td></tr>
<tr><td>#4</td><td>功能数据</td><td>AdminEmptyState（整块）</td><td>丙</td><td>有「重试」</td><td>标题改中性；新增说明行显示原话。<strong>要改组件</strong>：failed 得从布尔换成字符串。</td></tr>
<tr><td>#5</td><td>模型管理 · 模型段</td><td>AdminModelsTable → AdminEmptyState</td><td>甲</td><td>有「重试」</td><td>标题换成中性「模型加载失败」，原话移到说明行。</td></tr>
<tr><td>#6</td><td>模型管理 · 额度段</td><td>AdminModelsBudgets → AdminEmptyState</td><td>甲</td><td>有「重试」</td><td>同 #5，标题换成「额度加载失败」。</td></tr>
<tr><td>#7</td><td>模型管理 · 操作记录段</td><td>AdminModelsAudit → AdminEmptyState</td><td>甲</td><td>有「重试」</td><td>同 #5，标题换成「最近操作加载失败」。</td></tr>
<tr><td>#8</td><td>空间申请</td><td>AdminEmptyState（紧凑版，在卡里）</td><td>丙</td><td>有「重试」</td><td>标题改中性；新增说明行显示原话。<strong>要改组件</strong>：loadError 得存原话而不是固定话。</td></tr>
<tr><td>#9</td><td>成员管理</td><td>AdminEmptyState（表格 #error 槽）</td><td>甲</td><td>有「重试」</td><td>标题换成中性「成员加载失败」，原话移到说明行。</td></tr>
<tr><td>#10</td><td>飞书应用</td><td>AdminEmptyState（整块）</td><td>丙</td><td>有「重试」</td><td>标题改中性并说清是哪一页；新增说明行显示原话。<strong>要改组件</strong>：布尔换成字符串。</td></tr>
</table>
<p class="note"><strong>要改组件的只有三处</strong>（#4 #8 #10），因为它们把服务端原话在页面状态里就换成了布尔或固定话，界面上无从还原。<strong>「仅改 i18n 不动组件」在这里不成立</strong>：<code>AdminEmptyState</code> 的 <code>desc</code> 是可选 prop，六处调用点原本没传 <code>:desc</code>，只加一条 i18n 键什么都不会渲染出来。</p>

<h3 class="pb">4.3 实际渲染的代表错误态（按四种结构分组）</h3>
<p>下面每张都是<strong>真实组件按 props 渲染的</strong>，不是画的示意图：左边按现状传 props，右边按建议传 props。四组分别对应 4.1 里的甲 / 乙 / 丙 / 丁。</p>
<h3>结构甲 · 原话当标题（#5 #6 #7 #9）</h3>
{figures(COPY_ROWS[0:4])}
<h3 class="pb">结构乙 · 原话挂 title，悬停才见（#1 #2）</h3>
{figures(COPY_ROWS[4:6])}
<h3>结构丙 · 原话丢了（#4 #8 #10）</h3>
{figures(COPY_ROWS[6:9])}
<h3 class="pb">结构丁 · 原话作说明行（#3，已是建议的形状）</h3>
{figures(COPY_ROWS[9:10])}

<h3>4.4 五处写失败横幅（W1–W5）</h3>
<p>写失败横幅<strong>没有按钮</strong>，本轮<strong>不新加入口</strong>。五处里有四处已经在显示服务端原话，<strong>不动</strong>；只有 <strong>W1</strong> 的措辞要改——它现在写「请刷新确认申请状态后重试」，可旁边没有按钮，容易看成按钮丢了。改成真实的下一步（刷新确认状态）。</p>
{figures([COPY_WRITE])}

<h2 class="pb">5. 截图</h2>
<p>每张图在<strong>截取前</strong>把「路由 + 状态样本 + 宽度 + 主题」写进 manifest，截完再从 DOM 读回实际渲染的页面与状态，一起记进去。判定只认这份 manifest，不认像素像不像。</p>
<p><strong>哪些沿用、哪些是新拍的</strong>：5.1 / 5.2 / 5.4 / 5.5 全部<strong>沿用</strong>上一版的 24 张，未空重拍。5.3 里 <code>narrow-390-queue.png</code>、<code>narrow-390-models.png</code> 沿用，<code>long-content-spaces-390.png</code> 是窄屏修复后<strong>重拍</strong>的唯一一张。5.6 全部是<strong>本轮新拍</strong>的 14 张。</p>

<h3>5.1 浅色 1440 · 代表页</h3>
{figures(IMAGES[0:3])}

<h3>5.2 深色模式</h3>
{figures(IMAGES[3:5])}

<h3>5.3 390 窄屏：必须流式收缩</h3>
<p>下面第三张 <code>long-content-spaces-390.png</code> 是本轮<strong>重拍</strong>的：修复前那张里，长空间申请卡的标题和说明从右侧被整段裁掉（不是可见省略号）。前两张沿用。</p>
{figures(IMAGES[5:8])}

<h3>5.4 1920 宽屏：1440 是内容列上限</h3>
<p>宽屏下内容列不再变宽，多出来的宽度留在两侧。下面是同一列在 1920 下的样子。</p>
{figures(IMAGES[8:10])}

<h3>5.5 三种状态：加载 / 出错 / 长内容</h3>
{figures(IMAGES[10:11])}
{figures(IMAGES[12:13])}

<h3 class="pb">5.6 窄屏缺陷的修复，与 390 七页浅深全覆盖</h3>
<p><strong>缺陷</strong>：390 下空间申请的长卡，标题与说明从右侧被<strong>整段裁掉</strong>，连省略号都看不到。</p>
<p><strong>固定源</strong>：<code>AdminSpacesPage</code> 的 <code>@media (max-width: 700px)</code> 把 <code>.asp__row</code> 从 <code>row</code> 改成 <code>column</code>，却留下了基线里的 <code>align-items: flex-start</code>。改成 column 之后 cross 轴变成水平，<code>flex-start</code> 就让 <code>.asp__main</code> 只拿 fit-content 宽；标题是 <code>nowrap</code>，把那个宽度顶成整句那么宽；<code>.asp__list</code> 的 <code>overflow: hidden</code> 再把伸出的部分硬裁掉，省略号画在可视区之外。</p>
<p><strong>改法</strong>：窄屏下 <code>.asp__row</code> 用 <code>align-items: stretch</code> 让正文跟着行宽走，<code>.asp__name</code> 在窄屏换成 <code>white-space: normal</code>（要的是读得全，不是省略号），<code>.asp__main</code> 加 <code>overflow-wrap: anywhere</code> 让<strong>无空格文本</strong>也能断行。<strong>行内动作与功能不动</strong>：通过 / 驳回仍在这一行自己的动作区，只是窄屏下排到正文下方右对齐。</p>
<table>
<tr><th>量法</th><th>修复后（390）</th><th>注回旧写法做对照</th></tr>
<tr><td>行宽 clientWidth → scrollWidth</td><td>356 → 356</td><td>356 → <strong>644 / 1066 / 1701</strong></td></tr>
<tr><td>正文块 <code>.asp__main</code> 宽（可用 324）</td><td>324</td><td>628 / 1050 / 1684.6</td></tr>
<tr><td>三组文本</td><td>原样 / 长中文 / 无空格 全通过</td><td>三种都溢出</td></tr>
</table>
<p>对照的三个数分别是「原样 / 长中文 / 无空格」，最坏的无空格那种在旧写法下超出行边界 1345px。对照是把旧写法注回同一个静态包量的，<strong>证明这条判据看得出问题，不是量不出问题所以全绿</strong>。700 宽与 1440 宽也各跑一遍：700 三组全过（666 → 666）；1440 下长标题按桌面既有样式走省略号，属<strong>设计如此</strong>，不算缺陷。</p>
<p><strong>390 七页 × 浅深</strong>：下面 14 张是本轮新拍的。判据是「文字叶子伸出某个 <code>overflow-x: hidden</code> 祖先」即静默裁切（就是上面那个缺陷的形态）；<code>overflow-x: auto/scroll</code> 的祖先算可滚动、有滚动条兜底，不算缺陷。<strong>14 / 14 通过，静默裁切 0 处，无横向滚动，网络层零请求。</strong></p>
{two_up(IMAGES_390)}
<div class="note">
<p><strong>窄屏下可以横向滚动的三处，不是缺陷</strong>：反馈队列详情面板（最多伸出 504px）、看板的 <code>ad__kinds</code> 指标条、模型详情抽屉。它们的祖先给的是 <code>auto</code>/<code>scroll</code>，滚动条本身是可用性兜底，文字没有被裁掉。</p>
<p>取证脚本可重跑：<code>docs/evidence/preview-layout/narrow-overflow.mjs</code>（缺陷量法 + 注回旧写法的对照）与 <code>docs/evidence/preview-layout/narrow-390-pages.mjs</code>（七页浅深扫描），输出在同目录 JSON。</p>
</div>

<h2 class="pb">6. 1440 与 720 是项目取舍，不是行业默认</h2>
<p><strong>1440 —— 宽表和看板的内容列上限。</strong>这几页以横向可比的表格和指标为主，再宽一行读到头要来回扫；再窄则长标题和指标并排放不下。<strong>窄屏必须流式收缩</strong>：1440 是上限，不是固定宽度。</p>
<p><strong>720 —— 表单上限。</strong>输入、校验、提交落在一个视线里，再宽一行读不完。</p>
<p><strong>正文阅读区可单独限宽</strong>，不跟这两条绑死。</p>
<div class="note">
<p>成熟产品里常见 1200 / 1280 / Fluid 等做法。<strong>这里把它们当结构参考，不取相同像素才算有依据</strong>——参考的是「有一个内容列上限 + 窄屏收缩」这个结构，不是某个具体数值。上面两个数字是本项目按自己的表格密度和中文行长定的，<strong>不称行业默认</strong>。</p>
</div>

<h2>7. 外部设计参考</h2>
<p>看的是结构，不是像素。</p>
<table>
<tr><th>来源</th><th>看它什么</th><th>适用性取舍</th></tr>
<tr>
  <td>IBM Carbon 的 2x Grid<br><code>carbondesignsystem.com/foundations/2x-grid/overview/</code></td>
  <td>内容列上限，以及流式容器与固定容器两套并存的做法</td>
  <td><strong>结构适用。</strong>它的列数与像素按它自己的产品密度定，不搬。</td>
</tr>
<tr>
  <td>Material 3 的 Layout<br><code>m3.material.io/foundations/layout/understanding-layout/overview</code></td>
  <td>按窗口尺寸分档（compact / medium / expanded…）决定布局换挡</td>
  <td><strong>「窄屏换挡」适用。</strong>本项目用 CSS 断点与容器查询落地，不引入「尺寸档」这套概念，避免多一层要维护的抽象。</td>
</tr>
<tr>
  <td>GitHub Primer 的 Page layout<br><code>primer.style/product/foundations/layout/</code></td>
  <td>后台类页面的页头 — 筛选 — 表格三层分工</td>
  <td><strong>分层与我们一致，可对照。</strong>宽度取值不搬。</td>
</tr>
</table>
<div class="note">
<p><strong>取舍一句话</strong>：三家都在做「内容列上限 + 窄屏收缩」，我们取这个结构；像素按自己的表格密度和中文行长定，所以第 6 节那两个数字写成<strong>项目取舍</strong>。</p>
<p><strong>如实说明</strong>：这次核验没有逐条抄录三家的像素表，上面写的是它们在结构上的做法。需要逐条像素对照的话，请指明要哪一家的哪一节，我再去取那一节的原文。</p>
</div>

<h2>8. 核验方式</h2>
<p>三件事分开核，不混着说：</p>
<ol>
<li><strong>逐页 manifest</strong> — 两份，各自记「路由 / 状态样本 / 宽度 / 主题」四项（截图前记，截完 DOM 读回实际渲染结果）。沿用集 24 张（<code>/var/tmp/shots/manifest.json</code>，其中 1 张标了 <code>reShot</code>）与本轮新拍集 14 张（<code>/var/tmp/shots-390/manifest.json</code>），<strong>38 / 38 对得上</strong>。</li>
<li><strong>像素客观复核</strong> — 尺寸、亮度、边缘密度。<strong>沿用集 24 / 24</strong>：尺寸与 manifest 一致；深色图平均亮度 26–33、浅色 247–250，主题没串；无空白图。加载态与出错态的边缘密度明显低于正常态（0.006–0.011 对 0.02–0.07），符合少内容的样子。</li>
<li><strong>窄屏静默裁切判据</strong> — 见 5.6。判据是「文字叶子伸出某个 <code>overflow-x: hidden</code> 祖先」，不是「元素自身 scrollWidth &gt; clientWidth」（后者会把设计好的省略号误判成缺陷）。<strong>14 / 14 通过，静默裁切 0 处</strong>；另把旧写法注回同一包做对照，判据立刻转红，证明它看得出问题。</li>
<li><strong>请求不出网</strong> — 预览是<strong>真组件 + 假数据</strong>，出口在 <code>proto-preview-transport.ts</code>，样例数据与路由在另两个 fixtures 文件。取证在 <code>docs/evidence/preview-fake-service/</code>，两份脚本可重跑，输出就是同目录的 JSON：
  <ul>
    <li><strong>读路径</strong>（<code>get-spaces.mjs</code>）：Playwright 网络层事件零请求，长标题与长简介都在 DOM 里，3 条申请人。</li>
    <li><strong>写路径</strong>（<code>review-click.mjs</code>）：点<strong>行内</strong>「通过」。四条同时成立才算 —— ① 点到的是行内按钮（<code>getByRole('button', {{name:'通过', exact:true}})</code>，排除页头「已通过」页签）；② 假服务收到那条 <code>POST /admin/spaces/41/review</code>，请求体与响应体都记下；③ 拿 POST 带的 id 查<strong>那条记录自己</strong>，<code>reviewStatus</code> PENDING→APPROVED；④ 网络层零请求。</li>
    <li><strong>为什么不看列表条数</strong>：条数会随筛选变。曾经一次取证里「已通过 1→2、待审核 3→1」看着像审核成功，其实是定位命中了页头「已通过」页签、切换了筛选。<strong>那条结论已撤回</strong>，脚本改成把模糊命中的候选逐个摊开（<code>fuzzyWho</code>），误判在数据里直接看得见。</li>
    <li><strong>出口认两种路径</strong>：<code>/api/...</code> 与无前缀的 <code>/admin/...</code>、<code>/feedback/...</code>。<code>network/api</code> 的 <code>baseURL</code> 取 <code>VITE_API_BASE_URL</code> 且无 fallback，而 Vite 不加载 <code>.env.sample</code>，干净 checkout 下它发的就是无前缀那种。所以取证<strong>构建时不给 env</strong>，跑的正是默认配置。</li>
  </ul>
</li>
</ol>

<h2>9. 还没核的</h2>
<ul>
<li><strong>700px 以下已补齐</strong>：此前只核过队列、模型、空间申请三页，现在 390 七页 × 浅深都拍了图并跑了 DOM 判据（第 5.6 节，14 / 14 通过）。</li>
<li><strong>1024 / 1280 这些中间宽度本轮不要求补</strong>，不作为本轮门禁。只核了 390 / 1440 / 1920 三档；中间宽度按 1440 上限 + 流式收缩的同一条规则推，没有逐档截图。</li>
<li>预览用的是样例数据，真实后端数据下的表现没测。</li>
<li>只有队列和飞书两页的卡片走 <code>.admin-card</code>；模型 / 空间 / 成员 / 看板 / 功能数据还在各自文件里写 <code>--surface</code> / <code>--line</code> / <code>--radius-lg</code> 三件套。收敛是下一步，不影响本轮的视觉一致性。</li>
</ul>

</body></html>
"""

OUT_HTML.write_text(HTML, encoding='utf-8')
print(f'html: {OUT_HTML} ({OUT_HTML.stat().st_size} bytes)')

cmd = [
    'chromium', '--headless', '--no-sandbox', '--disable-gpu',
    '--disable-dev-shm-usage', '--no-pdf-header-footer',
    f'--print-to-pdf={OUT_PDF}',
    OUT_HTML.as_uri(),
]
r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
print('chromium exit', r.returncode)
if r.returncode != 0:
    print(r.stderr[-2000:])
    sys.exit(1)
print(f'pdf: {OUT_PDF} ({OUT_PDF.stat().st_size} bytes)')
