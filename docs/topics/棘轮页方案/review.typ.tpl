#set page(paper: "a4", margin: (x: 18mm, y: 16mm), numbering: "1 / 1", number-align: center)
#set text(font: ("Noto Sans SC"), size: 9.5pt, lang: "zh", fill: rgb("#36383C"))
#set par(leading: 0.72em, justify: false)
#show raw: set text(font: "DejaVu Sans Mono", size: 8pt)
#show heading.where(level: 1): it => block(above: 16pt, below: 8pt)[#text(size: 14pt, weight: "bold", fill: rgb("#191A1C"))[#it.body]]
#show heading.where(level: 2): it => block(above: 12pt, below: 6pt)[#text(size: 11pt, weight: "bold", fill: rgb("#191A1C"))[#it.body]]

#let tag(body, fill: rgb("#EEEFF1"), ink: rgb("#36383C")) = box(fill: fill, inset: (x: 4pt, y: 1.5pt), radius: 6pt)[#text(size: 7.5pt, fill: ink)[#body]]
#let real = tag(fill: rgb("#E8F6EE"), ink: rgb("#12703A"))[真实值]
#let miss = tag(fill: white, ink: rgb("#5A5E66"))[未采集]
#let recal = tag(fill: rgb("#FAF0DC"), ink: rgb("#8F5406"))[口径修正中]
#let rule = tag(fill: rgb("#EAF0FB"), ink: rgb("#2F5AA8"))[规则变更]
#let del = tag[删除功能]
#let up = tag(fill: rgb("#FDECEC"), ink: rgb("#B91C1C"))[新增豁免]
#let ok = tag(fill: rgb("#E8F6EE"), ink: rgb("#12703A"))[通过]
#let th(x) = text(size: 8pt, fill: rgb("#747A82"))[#x]
#let tbl(cols, ..cells) = table(columns: cols, stroke: (x, y) => (top: 0.5pt + rgb("#ECEDEF")), inset: (x: 5pt, y: 4.5pt), fill: (x, y) => if y == 0 { rgb("#F4F5F7") }, ..cells)

#align(left)[
  #text(size: 18pt, weight: "bold", fill: rgb("#191A1C"))[后台「棘轮」页：维度盘点与页面方案]
  #v(-4pt)
  #text(fill: rgb("#5A5E66"))[第一份评审稿 · 2026-09-30 · 芝士Opus · 数据取自 `main@dc069a4`（05:22–05:23 UTC 实跑）与 CI 的 50 次历史]
]

#block(fill: rgb("#FDF1E2"), inset: 10pt, radius: 8pt, width: 100%)[
  *结论*
  - 仓库里有 13 道真实检查，分属四个方面：能独立运行的前端场景、前后端依赖边界、文件规模、类型与样式。页面按这四个方面分区（结构一）。
  - 数据推荐从 CI 取：扩展现有「Architecture metrics」工作流，每次合入 main 时把各检查的实际数、冻结数和规则版本存成一份记录，后端读来存库。
  - 页面只读检查脚本的输出，不另写评分规则。冻结数和实际数分两列显示。场景一区等 \#2182 的判定修复合入、回归通过后再采集，现在显示「待核算」。
  - 需要你定三件事，见第 5 节。
]

= 1　用户在这一页上能做什么

打开「后台管理 → 棘轮」，一屏看到四个方面各自的状态：还冻结着多少债、比起点少了多少、哪道检查一直没动。点任一道检查展开详情：规则原文、基线文件路径、已失效但还没清掉的豁免，以及每一步变化对应的提交和 PR。

页面顶部固定写明数据来自哪个提交、什么时候采集。每个数都带一个标签：#real、#tag(fill: white, ink: rgb("#5A5E66"))[样例]、#miss、#recal。变化按来源分成还债、#rule、#del、#up 四类。规则变过的检查，只和规则变更之后的那次比，不把口径变化算成进步或退步。

页面只看不改，不会拦任何 PR。拦 PR 的仍是各道检查自己。

= 2　真实维度盘点

下表每一行是一道现有检查。「实际」是本次实跑检查器得到的数，「冻结」是基线文件里登记的豁免数。两列不一致时，说明有豁免已经失效、还没清掉。

#tbl((auto, 1.6fr, 0.55fr, 0.55fr, 1.3fr, 1.2fr),
  th[方面], th[检查（命令 · 基线）], th[实际], th[冻结], th[当前能拿到什么], th[历史],
  [① 场景], [场景棘轮：新页面必须能独立运行（`scene-ratchet.py`，\#2182）], [—], [—], [未合入 main，判定在修 #recal], [无],
  [① 场景], [组件 A/B/C/D 分级（`arch-metrics.py`）], [—], [—], [旧口径 A 180/392，不作当前值 #recal], [CI 50 次，口径待重算],
  [② 边界], [组件不取数、不读路由（`lint:boundary`）], [116], [116], [实际与冻结 #real], [CI 50 次（冻结数）],
  [② 边界], [C1 api→domain→core 分层（import-linter）], [26], [26], [#real], [CI 50 次（冻结数）],
  [② 边界], [C2 路由不碰别域 models], [*54*], [*56*], [2 条豁免已失效（\#2153 删了接口） #real], [CI 只存冻结数],
  [② 边界], [C3 同级领域无环], [173], [173], [#real], [CI 50 次（冻结数）],
  [② 边界], [领域不直连别域 repository（pytest 账本）], [154], [154], [通过即相等], [#miss],
  [② 边界], [Claude Code 适配器跨界账本（pytest）], [6], [6], [通过即相等], [#miss],
  [② 边界], [`is_private` 读点登记（pytest）], [20], [20], [通过即相等], [#miss],
  [③ 规模], [单文件行数上限（`check-file-sizes.py`）], [29 个 / 超 28,034 行], [无冻结表], [PR 上与合并基点比；总数由看板算 #real], [CI 50 次],
  [④ 样式], [vue-tsc 类型错误（`typecheck`）], [0], [0], [#real], [#miss],
  [④ 样式], [设计令牌（`lint:style`）], [37], [37], [#real], [#miss],
  [④ 样式], [Vuetify 固定色（`check-repo-rules.sh`）], [#miss], [7], [脚本只报通过/失败，不输出计数], [#miss],
)

#text(size: 8.5pt, fill: rgb("#5A5E66"))[看板里另有几项参考指标不是棘轮：后端函数内导入 726 条、30/90 天热点文件、同一天被多次改动的文件-天。它们没有「通过/违规」，只能看方向。]

== 现有两处读法要改

- 看板 `arch-metrics.py` 的「前端边界 116」和「后端契约 255」读的是基线文件里的豁免行，不跑检查器。豁免修掉后要等有人跑 `--update` 才会降，所以它比实际数高：C2 现在冻结 56、实际 54。棘轮页必须拿检查器本身的输出。
- CI 记录里没有规则版本。\#2163 把前端边界从 83 算成 127，记录里看不出这是口径变化。新的采集要写入每道检查所用判定脚本和配置的内容指纹，指纹一变就开一个新的比较起点。

== 历史里已经出现的四类变化（真实记录）

#tbl((auto, auto, 1fr, 1.2fr),
  th[类别], th[提交], th[PR], th[对数字的影响],
  [还债], [`2d33703fc`], [\#2137 路由缝], [前端边界 91 → 84；12 个组件不再需要路由],
  [#rule], [`14565ff91`], [\#2163 开始数相对路径导入], [前端边界 83 → 127。不是退步],
  [#del], [`4dcb876b4`], [\#2132 删除 ChatGPT 订阅导入], [C3 178 → 172],
  [#del], [`90f83ddb2`], [\#2154 删除课程与讨论], [组件 442 → 392；超标文件 32 → 29；前端边界 120 → 116],
  [#up], [`49d769b2d`], [\#2180 拆 review/services.py], [C3 172 → 173],
)

同一口径下的走势（每点一次 main 运行，2026-09-29 15:24 至 09-30 05:01 UTC）：

#grid(columns: (1fr, 1fr, 1fr), gutter: 10pt,
  [前端边界（冻结） 91 → 116 \ %%TREND_fe_boundary%% \ #text(size: 7.5pt, fill: rgb("#2F5AA8"))[虚线 = \#2163 规则变更；之后 127 → 116]],
  [C3 同级领域无环 178 → 173 \ %%TREND_c3%% \ #text(size: 7.5pt, fill: rgb("#747A82"))[−6 删除，+1 新增豁免]],
  [C1 分层 26 → 26 \ %%TREND_c1%% \ #text(size: 7.5pt, fill: rgb("#747A82"))[50 次运行无变化]],
  [超标文件 34 → 29 \ %%TREND_over%%],
  [超出上限行数 34,753 → 28,034 \ %%TREND_excess%%],
  [`agent/chat.py` 6,637 → 5,487 \ %%TREND_chat%%],
)

= 3　数据从哪来、多久刷新

#tbl((auto, 1.4fr, 1fr, 1fr),
  th[做法], th[怎么做], th[好处], th[问题],
  [*A 读 CI 记录（推荐）*], [扩展 `arch-metrics.yml`：同一 job 里再跑各检查器，每个检查器加 `--json` 输出实际数、冻结数、失效豁免、规则指纹，合成一份记录上传。后端用平台的 GitHub App 读最新记录并逐次存库。], [和 CI 同源，不复制判断；每次合入 main 和每周一自动刷新；存库后不受 CI 保留 90 天的限制], [这个 job 要装前后端依赖，从约 1 分钟变成几分钟；要改几个检查脚本的输出参数],
  [B 后端自己跑], [后端定时在服务器上跑检查器。], [不依赖 CI], [部署镜像里没有前端源码、node 依赖和 git 历史，跑不起来],
  [C 结果提交进仓库], [CI 把结果写成 JSON 提交回 main。], [看得见改动], [每次合入多一个提交，又触发 CI，和合并队列冲突],
)

已核实：平台 App 能列出并下载 `arch-metrics` 记录，现有 50 份、保留 90 天。页面显示「采集提交 · 采集时间」；最近一次记录晚于部署提交时，页面照实写两个 SHA，不假装同步。

#pagebreak()
= 4　页面结构：三种做法

#let wf(body) = block(stroke: 0.6pt + rgb("#E2E3E6"), radius: 6pt, inset: 6pt, width: 100%, fill: rgb("#F7F8FA"))[#set text(size: 7.5pt); #body]
#let bar(w, t, c: rgb("#FFFFFF")) = box(width: w, height: 11pt, fill: c, stroke: 0.5pt + rgb("#E2E3E6"), radius: 2pt, inset: (x: 3pt, y: 2pt))[#t]

#grid(columns: (1fr, 1fr, 1fr), gutter: 10pt,
  [*一、按方面分区（推荐）* \
   #wf[#bar(100%, [四个方面一览：状态 · 还冻结的债 · 起点→现在], c: rgb("#FDF1E2")) \ #v(2pt)
     #bar(100%, [① 场景　待核算]) \ #bar(32%, [KPI]) #bar(32%, [KPI]) #bar(32%, [KPI]) \ #bar(100%, [检查表 · 点开看详情]) \ #v(2pt)
     #bar(100%, [② 依赖边界]) \ #bar(100%, [检查表 + 走势]) \ #v(2pt) #bar(100%, [③ 文件规模]) \ #bar(100%, [④ 类型与样式])]
   先回答「哪一块在变好、哪一块没动」，再往下钻到文件和 PR。区的划分跟着检查走，新加一道检查只是在某区多一行。],
  [*二、一张总表* \
   #wf[#bar(100%, [筛选：方面 · 结果 · 有变化]) \ #v(2pt)
     #for i in range(7) [#bar(100%, [检查 · 实际 · 冻结 · 变化 · 走势]) \ ]]
   13 行全在一张表里，扫得快、好排序。但没有每个方面的汇总，「哪里卡住」要自己从表里读出来。],
  [*三、变化时间线优先* \
   #wf[#bar(100%, [最近的变化], c: rgb("#FDF1E2")) \
     #for i in range(5) [#bar(100%, [PR · 哪个数动了 · 哪一类]) \ ] #v(2pt)
     #bar(100%, [现状汇总（折叠）])]
   最直接回答「最近谁还了什么债」。可现在只有 1 天的历史，首屏会很空；而且没变化的检查在时间线里看不见，「卡住」反而被藏起来。],
)

推荐一，并把三的时间线放进每道检查的展开详情里（预览页已这样做）。历史攒够几周后，可以在页面顶部加一个「最近变化」标签页。

预览页（交互版在房间右侧，本页是按它重排的静态示意）首屏：

#block(stroke: 0.6pt + rgb("#E2E3E6"), radius: 8pt, inset: 10pt, width: 100%)[
  #set text(size: 8pt)
  #text(size: 12pt, weight: "bold", fill: rgb("#191A1C"))[棘轮] #h(6pt) #text(fill: rgb("#5A5E66"))[架构还债进度：每道检查现在是多少、冻结了多少、比起点好了还是差了]\
  #text(fill: rgb("#5A5E66"))[检查结果 `main@dc069a4`（\#2189） · 采集 2026-09-30 05:22–05:23 UTC · 历史 50 次 main 运行]
  #v(4pt)
  #tbl((1.2fr, auto, 1.4fr, 1.3fr, 1.1fr),
    th[方面], th[结果], th[还冻结着的债], th[同口径起点 → 现在], th[判断],
    [能独立运行的前端场景], [#recal], [待核算], [暂不比较], [等 \#2182 修复],
    [前后端依赖边界], [7 #ok], [前端 116 · 后端 253（冻结 255）], [前端 127→116 · C3 178→173], [在降；C1、C2 未动],
    [文件规模], [#ok], [29 个文件，超 28,034 行], [34→29 · 34,753→28,034], [在降；3 个靠删除],
    [类型与样式], [3 #ok], [令牌 37 · 固定色 7（冻结）], [#miss], [无历史不判断],
  )
]

= 5　需要你定的事

+ *页面结构*：按方面分区（推荐）、一张总表，还是时间线优先？
+ *数据来源*：同意扩展 `arch-metrics.yml`，在合入 main 时实跑全部检查器（这个 job 会慢几分钟，不影响 PR 的必需检查）？
+ *参考指标*：热点、函数内导入这些没有「通过/违规」的看板指标，放进本页第五区（只显示方向），还是留在文档站的「架构指标」页？

#pagebreak()
= 6　定了之后怎么做

三个小 PR，依次合入，每个都报 head、CI、截图和未验证部分：

+ *数据*：几道检查加 `--json`，工作流合成一份记录。只加输出参数，不改任何判定。`frontend_grade.py` 由重构话题维护，这里只读。
+ *后端*：后台管理接口读最新记录并存库，按提交去重。
+ *前端*：新页面 `views/admin/AdminRatchetPage.vue`。共享文件只按布局话题的约定各加一行：路由一条、后台导航一项。页头、指标卡和表格用现成的后台组件。

方案定之前不合入任何界面改动。

= 7　没做和没核实的

- 场景一区没有数字：等 \#2182 的判定修复。修复会改变组件分级，届时分级也换新起点。
- 固定色、三个 pytest 账本和 vue-tsc 没有历史：CI 看板从来没存过，上线后才开始积累。
- 预览页没在真浏览器里截图：工作机起不了浏览器，所以 PDF 里的首屏是重排示意，交互版请在房间右侧看。
- 「还债 / 删除功能」的分类是我按 PR 标题人工标的。上线版只自动识别规则变更（靠规则指纹），其余变化直接列出 PR，由人判断。
