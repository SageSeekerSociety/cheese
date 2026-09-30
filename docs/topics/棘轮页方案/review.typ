#set page(paper: "a4", margin: (x: 18mm, y: 16mm), numbering: "1 / 1", number-align: center)
#set text(font: ("Noto Sans SC"), size: 9.5pt, lang: "zh", fill: rgb("#36383C"))
#set par(leading: 0.72em, justify: false)
#show raw: set text(font: "DejaVu Sans Mono", size: 8pt)
#show heading.where(level: 1): it => block(above: 16pt, below: 8pt)[#text(size: 14pt, weight: "bold", fill: rgb("#191A1C"))[#it.body]]
#show heading.where(level: 2): it => block(above: 12pt, below: 6pt)[#text(size: 11pt, weight: "bold", fill: rgb("#191A1C"))[#it.body]]

#let tag(body, fill: rgb("#EEEFF1"), ink: rgb("#36383C")) = box(fill: fill, inset: (x: 4pt, y: 1.5pt), radius: 6pt)[#text(size: 7.5pt, fill: ink)[#body]]
#let real = tag(fill: rgb("#E8F6EE"), ink: rgb("#12703A"))[真实值]
#let miss = tag(fill: white, ink: rgb("#5A5E66"))[未采集]
#let nocol = tag(fill: white, ink: rgb("#5A5E66"))[not_collected]
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
  #text(fill: rgb("#5A5E66"))[第三份评审稿（已并入 main 现行规则重采）· 2026-09-30 · 数字取自 `aaed8e4f7`（2026-09-30 10:03 UTC）的一次真实采集；走势取自 CI 的 55 次 main 记录]
]

#block(fill: rgb("#FDF1E2"), inset: 10pt, radius: 8pt, width: 100%)[
  *结论*
  - 仓库里有 13 道真实检查，分属四个方面：能独立运行的前端场景、前后端依赖边界、文件规模、类型与样式。页面按这四个方面分区（结构一）。
  - 采集已经跑通了：这几道检查各自的 `--json` 输出、一个合成快照的脚本、工作流在合入 main 后跑并存进 CI 产物。本页所有数字是它在 `aaed8e4f7` 上的一次真实运行，不是手抄的。
  - 每条记录带规则指纹。指纹变了页面就开一个新的比较起点，规则前后不画成一条线。
  - 场景一区的判定规则在 main 上换过（\#2209，HuanCheng65 首修；补强 \#2217 仍在 draft）。本稿按 main 现行规则重采，所以这一区标「口径修正中」、不给方向：128 → 126 是规则变化，不是重构成果。实际独立挂载证据另列，本轮实跑 37 个组件 / 125 格全部通过。
  - 四分区、main 上采集留历史、参考指标留在文档站——三条都已批准，第 5 节记成已定项。本稿只等界面本身被看。
]

= 1　用户在这一页上能做什么

打开「后台管理 → 棘轮」，一屏看到四个方面各自的状态：还冻结着多少债、比起点少了多少、哪道检查一直没动。点任一道检查展开详情：规则原文、基线文件路径、已失效但还没清掉的豁免，以及每一步变化对应的提交和 PR。

页面顶部固定写明数据来自哪个提交、什么时候采集。每个数带一个来源标签：#real、#tag(fill: white, ink: rgb("#5A5E66"))[样例]、#recal，以及快照自己的哨兵值 #nocol——某道检查这次没有跑出记录时，页面写「没跑到」，不写 0。0 的意思是「量过了、没有问题」，正是这套东西要避免的说法。

变化按来源分成还债、#rule、#del、#up 四类。规则变过的检查只和规则变更之后的那次比。页面只看不改，不拦任何 PR。

= 2　真实维度盘点

下表每一行是一道检查。「实际」是本次实跑检查器得到的数，「冻结」是基线文件里登记的豁免数。两列不一致时，说明有豁免已经失效、还没清掉。

#tbl((auto, 1.75fr, 0.6fr, 0.6fr, 1.5fr, 0.9fr),
  th[方面], th[检查（命令 · 基线）], th[实际], th[冻结], th[当前能拿到什么], th[历史],
  [① 场景], [场景棘轮：新页面必须能独立运行（`scene-ratchet.py`）], [126], [126], [0 条失效豁免；数的是「评分未达标」的场景 #real], [无],
  [① 场景], [组件 A/B/C/D 分级（`arch-metrics.py`）], [—], [—], [旧口径 A 180/392，不作当前值 #recal], [CI 里存了旧口径，待重算],
  [② 边界], [组件不取数、不读路由（`lint:boundary`）], [116], [116], [0 条失效豁免 #real], [55 次（冻结数）],
  [② 边界], [C1 分层 api→domain→core（import-linter）], [26], [26], [0 条失效豁免 #real], [55 次],
  [② 边界], [C2 路由不碰别域 models], [*54*], [*56*], [2 条豁免已失效（\#2153 删了接口） #real], [只存冻结数],
  [② 边界], [C3 同级领域无环], [174], [174], [0 条失效豁免 #real], [55 次],
  [② 边界], [领域不直连别域 repository（pytest 账本）], [156], [156], [通过即相等；数从测试模块自带的登记表读 #real], [#miss],
  [② 边界], [Claude Code 适配器跨界账本（pytest）], [6], [6], [通过即相等 #real], [#miss],
  [② 边界], [`is_private` 读点登记（pytest）], [20], [20], [数的是读点、不是文件 #real], [#miss],
  [③ 规模], [单文件行数上限（`check-file-sizes.py`）], [0], [无冻结表], [这道闸门只判改动文件，在 main 上通常是 0；树的全量数由看板给：26 个文件、超 23,677 行 #real], [55 次],
  [④ 样式], [vue-tsc 类型错误（`typecheck`）], [0], [0], [#real], [#miss],
  [④ 样式], [设计令牌（`lint:style`）], [37], [37], [#real], [#miss],
  [④ 样式], [Vuetify 固定色（`check-repo-rules.sh`）], [6], [7], [1 条失效豁免，上一版这一格是「未采集」；本轮脚本输出了计数 #real], [#miss],
)

看板里另有几项参考指标不是棘轮：后端函数内导入、30/90 天热点文件、同一天被多次改动的文件-天。它们没有「通过/违规」，只能看方向，本页暂时没有收。

== 现有两处读法要改

- 看板 `arch-metrics.py` 的「前端边界 116」和「后端契约」读的是基线文件里的豁免行，不跑检查器。豁免修掉后要等有人跑 `--update` 才会降，所以它比实际数高：C2 现在冻结 56、实际 54。棘轮页拿的是检查器自己的输出。
- CI 记录里没有规则版本。\#2163 把前端边界从 83 算成 127，记录里看不出这是口径变化。新的快照写入了每道检查所用判定脚本和配置的内容指纹（基线文件不算：多冻结一条豁免不是改规则，那正是要数的债），指纹一变就开一个新的比较起点。

== 历史里已经出现的四类变化（真实记录）

#tbl((auto, auto, 1fr, 1.2fr),
  th[类别], th[提交], th[PR], th[对数字的影响],
  [还债], [`2d33703fc`], [\#2137 路由缝], [前端边界 91 → 84；12 个组件不再需要路由],
  [#rule], [`14565ff91`], [\#2163 开始数相对路径导入], [前端边界 83 → 127。不是退步],
  [#del], [`4dcb876b4`], [\#2132 删除 ChatGPT 订阅导入], [C3 178 → 172],
  [#del], [`90f83ddb2`], [\#2154 删除课程与讨论], [组件 442 → 392；超标文件 32 → 29；前端边界 120 → 116],
  [#up], [`49d769b2d`], [\#2180 拆 review/services.py], [C3 172 → 173],
)

同一口径下的走势（每点一次 main 运行，2026-09-29 15:24 至 09-30 05:51 UTC）：

#grid(columns: (1fr, 1fr, 1fr), gutter: 10pt,
  [前端边界（冻结） 91 → 116 \ #box(width: 150pt, height: 36pt)[#place(line(start: (69.7pt, 0pt), end: (69.7pt, 36pt), stroke: (paint: rgb("#2F5AA8"), dash: "dashed", thickness: 0.6pt)))#place(line(start: (3.0pt, 27.5pt), end: (5.7pt, 27.5pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (5.7pt, 27.5pt), end: (8.3pt, 27.5pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (8.3pt, 27.5pt), end: (11.0pt, 32.3pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (11.0pt, 32.3pt), end: (13.7pt, 32.3pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (13.7pt, 32.3pt), end: (16.3pt, 32.3pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (16.3pt, 32.3pt), end: (19.0pt, 32.3pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (19.0pt, 32.3pt), end: (21.7pt, 32.3pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (21.7pt, 32.3pt), end: (24.3pt, 32.3pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (24.3pt, 32.3pt), end: (27.0pt, 32.3pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (27.0pt, 32.3pt), end: (29.7pt, 32.3pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (29.7pt, 32.3pt), end: (32.3pt, 32.3pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (32.3pt, 32.3pt), end: (35.0pt, 32.3pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (35.0pt, 32.3pt), end: (37.7pt, 32.3pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (37.7pt, 32.3pt), end: (40.3pt, 32.3pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (40.3pt, 32.3pt), end: (43.0pt, 32.3pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (43.0pt, 32.3pt), end: (45.7pt, 33.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (45.7pt, 33.0pt), end: (48.3pt, 33.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (48.3pt, 33.0pt), end: (51.0pt, 33.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (51.0pt, 33.0pt), end: (53.7pt, 33.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (53.7pt, 33.0pt), end: (56.3pt, 33.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (56.3pt, 33.0pt), end: (59.0pt, 33.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (59.0pt, 33.0pt), end: (61.7pt, 33.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (61.7pt, 33.0pt), end: (64.3pt, 33.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (64.3pt, 33.0pt), end: (67.0pt, 33.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (67.0pt, 33.0pt), end: (69.7pt, 3.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (69.7pt, 3.0pt), end: (72.3pt, 3.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (72.3pt, 3.0pt), end: (75.0pt, 3.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (75.0pt, 3.0pt), end: (77.7pt, 3.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (77.7pt, 3.0pt), end: (80.3pt, 3.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (80.3pt, 3.0pt), end: (83.0pt, 3.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (83.0pt, 3.0pt), end: (85.7pt, 3.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (85.7pt, 3.0pt), end: (88.3pt, 3.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (88.3pt, 3.0pt), end: (91.0pt, 3.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (91.0pt, 3.0pt), end: (93.7pt, 3.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (93.7pt, 3.0pt), end: (96.3pt, 3.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (96.3pt, 3.0pt), end: (99.0pt, 3.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (99.0pt, 3.0pt), end: (101.7pt, 3.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (101.7pt, 3.0pt), end: (104.3pt, 3.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (104.3pt, 3.0pt), end: (107.0pt, 5.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (107.0pt, 5.0pt), end: (109.7pt, 5.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (109.7pt, 5.0pt), end: (112.3pt, 5.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (112.3pt, 5.0pt), end: (115.0pt, 5.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (115.0pt, 5.0pt), end: (117.7pt, 5.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (117.7pt, 5.0pt), end: (120.3pt, 7.8pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (120.3pt, 7.8pt), end: (123.0pt, 7.8pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (123.0pt, 7.8pt), end: (125.7pt, 7.8pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (125.7pt, 7.8pt), end: (128.3pt, 10.5pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (128.3pt, 10.5pt), end: (131.0pt, 10.5pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (131.0pt, 10.5pt), end: (133.7pt, 10.5pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (133.7pt, 10.5pt), end: (136.3pt, 10.5pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (136.3pt, 10.5pt), end: (139.0pt, 10.5pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (139.0pt, 10.5pt), end: (141.7pt, 10.5pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (141.7pt, 10.5pt), end: (144.3pt, 10.5pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (144.3pt, 10.5pt), end: (147.0pt, 10.5pt), stroke: 1pt + rgb("#5A5E66")))] \ #text(size: 7.5pt, fill: rgb("#2F5AA8"))[虚线 = \#2163 规则变更；之后 127 → 116]],
  [C3 同级领域无环 178 → 174 \ #box(width: 150pt, height: 36pt)[#place(line(start: (3.0pt, 3.0pt), end: (5.7pt, 3.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (5.7pt, 3.0pt), end: (8.3pt, 3.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (8.3pt, 3.0pt), end: (11.0pt, 3.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (11.0pt, 3.0pt), end: (13.7pt, 3.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (13.7pt, 3.0pt), end: (16.3pt, 3.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (16.3pt, 3.0pt), end: (19.0pt, 3.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (19.0pt, 3.0pt), end: (21.7pt, 3.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (21.7pt, 3.0pt), end: (24.3pt, 3.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (24.3pt, 3.0pt), end: (27.0pt, 3.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (27.0pt, 3.0pt), end: (29.7pt, 3.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (29.7pt, 3.0pt), end: (32.3pt, 3.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (32.3pt, 3.0pt), end: (35.0pt, 3.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (35.0pt, 3.0pt), end: (37.7pt, 3.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (37.7pt, 3.0pt), end: (40.3pt, 3.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (40.3pt, 3.0pt), end: (43.0pt, 3.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (43.0pt, 3.0pt), end: (45.7pt, 33.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (45.7pt, 33.0pt), end: (48.3pt, 33.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (48.3pt, 33.0pt), end: (51.0pt, 33.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (51.0pt, 33.0pt), end: (53.7pt, 33.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (53.7pt, 33.0pt), end: (56.3pt, 33.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (56.3pt, 33.0pt), end: (59.0pt, 33.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (59.0pt, 33.0pt), end: (61.7pt, 33.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (61.7pt, 33.0pt), end: (64.3pt, 33.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (64.3pt, 33.0pt), end: (67.0pt, 33.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (67.0pt, 33.0pt), end: (69.7pt, 33.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (69.7pt, 33.0pt), end: (72.3pt, 33.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (72.3pt, 33.0pt), end: (75.0pt, 33.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (75.0pt, 33.0pt), end: (77.7pt, 33.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (77.7pt, 33.0pt), end: (80.3pt, 33.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (80.3pt, 33.0pt), end: (83.0pt, 33.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (83.0pt, 33.0pt), end: (85.7pt, 33.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (85.7pt, 33.0pt), end: (88.3pt, 33.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (88.3pt, 33.0pt), end: (91.0pt, 33.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (91.0pt, 33.0pt), end: (93.7pt, 33.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (93.7pt, 33.0pt), end: (96.3pt, 33.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (96.3pt, 33.0pt), end: (99.0pt, 33.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (99.0pt, 33.0pt), end: (101.7pt, 33.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (101.7pt, 33.0pt), end: (104.3pt, 33.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (104.3pt, 33.0pt), end: (107.0pt, 33.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (107.0pt, 33.0pt), end: (109.7pt, 33.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (109.7pt, 33.0pt), end: (112.3pt, 33.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (112.3pt, 33.0pt), end: (115.0pt, 33.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (115.0pt, 33.0pt), end: (117.7pt, 33.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (117.7pt, 33.0pt), end: (120.3pt, 33.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (120.3pt, 33.0pt), end: (123.0pt, 33.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (123.0pt, 33.0pt), end: (125.7pt, 28.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (125.7pt, 28.0pt), end: (128.3pt, 28.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (128.3pt, 28.0pt), end: (131.0pt, 28.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (131.0pt, 28.0pt), end: (133.7pt, 28.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (133.7pt, 28.0pt), end: (136.3pt, 28.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (136.3pt, 28.0pt), end: (139.0pt, 28.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (139.0pt, 28.0pt), end: (141.7pt, 28.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (141.7pt, 28.0pt), end: (144.3pt, 23.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (144.3pt, 23.0pt), end: (147.0pt, 23.0pt), stroke: 1pt + rgb("#5A5E66")))] \ #text(size: 7.5pt, fill: rgb("#747A82"))[−6 删除，+2 新增豁免]],
  [C1 分层 26 → 26 \ #box(width: 150pt, height: 36pt)[#place(line(start: (3.0pt, 18.0pt), end: (5.7pt, 18.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (5.7pt, 18.0pt), end: (8.3pt, 18.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (8.3pt, 18.0pt), end: (11.0pt, 18.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (11.0pt, 18.0pt), end: (13.7pt, 18.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (13.7pt, 18.0pt), end: (16.3pt, 18.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (16.3pt, 18.0pt), end: (19.0pt, 18.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (19.0pt, 18.0pt), end: (21.7pt, 18.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (21.7pt, 18.0pt), end: (24.3pt, 18.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (24.3pt, 18.0pt), end: (27.0pt, 18.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (27.0pt, 18.0pt), end: (29.7pt, 18.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (29.7pt, 18.0pt), end: (32.3pt, 18.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (32.3pt, 18.0pt), end: (35.0pt, 18.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (35.0pt, 18.0pt), end: (37.7pt, 18.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (37.7pt, 18.0pt), end: (40.3pt, 18.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (40.3pt, 18.0pt), end: (43.0pt, 18.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (43.0pt, 18.0pt), end: (45.7pt, 18.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (45.7pt, 18.0pt), end: (48.3pt, 18.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (48.3pt, 18.0pt), end: (51.0pt, 18.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (51.0pt, 18.0pt), end: (53.7pt, 18.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (53.7pt, 18.0pt), end: (56.3pt, 18.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (56.3pt, 18.0pt), end: (59.0pt, 18.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (59.0pt, 18.0pt), end: (61.7pt, 18.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (61.7pt, 18.0pt), end: (64.3pt, 18.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (64.3pt, 18.0pt), end: (67.0pt, 18.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (67.0pt, 18.0pt), end: (69.7pt, 18.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (69.7pt, 18.0pt), end: (72.3pt, 18.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (72.3pt, 18.0pt), end: (75.0pt, 18.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (75.0pt, 18.0pt), end: (77.7pt, 18.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (77.7pt, 18.0pt), end: (80.3pt, 18.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (80.3pt, 18.0pt), end: (83.0pt, 18.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (83.0pt, 18.0pt), end: (85.7pt, 18.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (85.7pt, 18.0pt), end: (88.3pt, 18.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (88.3pt, 18.0pt), end: (91.0pt, 18.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (91.0pt, 18.0pt), end: (93.7pt, 18.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (93.7pt, 18.0pt), end: (96.3pt, 18.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (96.3pt, 18.0pt), end: (99.0pt, 18.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (99.0pt, 18.0pt), end: (101.7pt, 18.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (101.7pt, 18.0pt), end: (104.3pt, 18.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (104.3pt, 18.0pt), end: (107.0pt, 18.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (107.0pt, 18.0pt), end: (109.7pt, 18.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (109.7pt, 18.0pt), end: (112.3pt, 18.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (112.3pt, 18.0pt), end: (115.0pt, 18.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (115.0pt, 18.0pt), end: (117.7pt, 18.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (117.7pt, 18.0pt), end: (120.3pt, 18.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (120.3pt, 18.0pt), end: (123.0pt, 18.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (123.0pt, 18.0pt), end: (125.7pt, 18.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (125.7pt, 18.0pt), end: (128.3pt, 18.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (128.3pt, 18.0pt), end: (131.0pt, 18.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (131.0pt, 18.0pt), end: (133.7pt, 18.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (133.7pt, 18.0pt), end: (136.3pt, 18.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (136.3pt, 18.0pt), end: (139.0pt, 18.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (139.0pt, 18.0pt), end: (141.7pt, 18.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (141.7pt, 18.0pt), end: (144.3pt, 18.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (144.3pt, 18.0pt), end: (147.0pt, 18.0pt), stroke: 1pt + rgb("#5A5E66")))] \ #text(size: 7.5pt, fill: rgb("#747A82"))[55 次运行无变化]],
  [超标文件 34 → 29 \ #box(width: 150pt, height: 36pt)[#place(line(start: (3.0pt, 3.0pt), end: (5.7pt, 3.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (5.7pt, 3.0pt), end: (8.3pt, 3.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (8.3pt, 3.0pt), end: (11.0pt, 3.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (11.0pt, 3.0pt), end: (13.7pt, 3.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (13.7pt, 3.0pt), end: (16.3pt, 3.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (16.3pt, 3.0pt), end: (19.0pt, 3.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (19.0pt, 3.0pt), end: (21.7pt, 3.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (21.7pt, 3.0pt), end: (24.3pt, 3.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (24.3pt, 3.0pt), end: (27.0pt, 3.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (27.0pt, 3.0pt), end: (29.7pt, 3.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (29.7pt, 3.0pt), end: (32.3pt, 3.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (32.3pt, 3.0pt), end: (35.0pt, 3.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (35.0pt, 3.0pt), end: (37.7pt, 3.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (37.7pt, 3.0pt), end: (40.3pt, 3.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (40.3pt, 3.0pt), end: (43.0pt, 3.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (43.0pt, 3.0pt), end: (45.7pt, 3.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (45.7pt, 3.0pt), end: (48.3pt, 3.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (48.3pt, 3.0pt), end: (51.0pt, 3.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (51.0pt, 3.0pt), end: (53.7pt, 3.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (53.7pt, 3.0pt), end: (56.3pt, 3.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (56.3pt, 3.0pt), end: (59.0pt, 3.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (59.0pt, 3.0pt), end: (61.7pt, 3.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (61.7pt, 3.0pt), end: (64.3pt, 3.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (64.3pt, 3.0pt), end: (67.0pt, 3.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (67.0pt, 3.0pt), end: (69.7pt, 3.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (69.7pt, 3.0pt), end: (72.3pt, 3.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (72.3pt, 3.0pt), end: (75.0pt, 3.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (75.0pt, 3.0pt), end: (77.7pt, 3.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (77.7pt, 3.0pt), end: (80.3pt, 3.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (80.3pt, 3.0pt), end: (83.0pt, 3.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (83.0pt, 3.0pt), end: (85.7pt, 9.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (85.7pt, 9.0pt), end: (88.3pt, 9.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (88.3pt, 9.0pt), end: (91.0pt, 9.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (91.0pt, 9.0pt), end: (93.7pt, 9.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (93.7pt, 9.0pt), end: (96.3pt, 9.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (96.3pt, 9.0pt), end: (99.0pt, 9.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (99.0pt, 9.0pt), end: (101.7pt, 9.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (101.7pt, 9.0pt), end: (104.3pt, 9.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (104.3pt, 9.0pt), end: (107.0pt, 15.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (107.0pt, 15.0pt), end: (109.7pt, 15.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (109.7pt, 15.0pt), end: (112.3pt, 15.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (112.3pt, 15.0pt), end: (115.0pt, 15.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (115.0pt, 15.0pt), end: (117.7pt, 15.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (117.7pt, 15.0pt), end: (120.3pt, 15.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (120.3pt, 15.0pt), end: (123.0pt, 15.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (123.0pt, 15.0pt), end: (125.7pt, 15.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (125.7pt, 15.0pt), end: (128.3pt, 33.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (128.3pt, 33.0pt), end: (131.0pt, 33.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (131.0pt, 33.0pt), end: (133.7pt, 33.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (133.7pt, 33.0pt), end: (136.3pt, 33.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (136.3pt, 33.0pt), end: (139.0pt, 33.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (139.0pt, 33.0pt), end: (141.7pt, 33.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (141.7pt, 33.0pt), end: (144.3pt, 33.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (144.3pt, 33.0pt), end: (147.0pt, 33.0pt), stroke: 1pt + rgb("#5A5E66")))]],
  [超出上限行数 34,753 → 27,673 \ #box(width: 150pt, height: 36pt)[#place(line(start: (3.0pt, 3.0pt), end: (5.7pt, 3.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (5.7pt, 3.0pt), end: (8.3pt, 3.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (8.3pt, 3.0pt), end: (11.0pt, 3.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (11.0pt, 3.0pt), end: (13.7pt, 3.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (13.7pt, 3.0pt), end: (16.3pt, 3.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (16.3pt, 3.0pt), end: (19.0pt, 3.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (19.0pt, 3.0pt), end: (21.7pt, 3.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (21.7pt, 3.0pt), end: (24.3pt, 3.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (24.3pt, 3.0pt), end: (27.0pt, 3.1pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (27.0pt, 3.1pt), end: (29.7pt, 3.1pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (29.7pt, 3.1pt), end: (32.3pt, 3.1pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (32.3pt, 3.1pt), end: (35.0pt, 3.4pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (35.0pt, 3.4pt), end: (37.7pt, 3.4pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (37.7pt, 3.4pt), end: (40.3pt, 3.4pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (40.3pt, 3.4pt), end: (43.0pt, 3.4pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (43.0pt, 3.4pt), end: (45.7pt, 4.3pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (45.7pt, 4.3pt), end: (48.3pt, 4.3pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (48.3pt, 4.3pt), end: (51.0pt, 4.3pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (51.0pt, 4.3pt), end: (53.7pt, 4.3pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (53.7pt, 4.3pt), end: (56.3pt, 4.7pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (56.3pt, 4.7pt), end: (59.0pt, 5.8pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (59.0pt, 5.8pt), end: (61.7pt, 5.8pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (61.7pt, 5.8pt), end: (64.3pt, 5.8pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (64.3pt, 5.8pt), end: (67.0pt, 5.8pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (67.0pt, 5.8pt), end: (69.7pt, 5.8pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (69.7pt, 5.8pt), end: (72.3pt, 5.8pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (72.3pt, 5.8pt), end: (75.0pt, 5.8pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (75.0pt, 5.8pt), end: (77.7pt, 6.6pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (77.7pt, 6.6pt), end: (80.3pt, 7.3pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (80.3pt, 7.3pt), end: (83.0pt, 7.3pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (83.0pt, 7.3pt), end: (85.7pt, 13.4pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (85.7pt, 13.4pt), end: (88.3pt, 13.5pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (88.3pt, 13.5pt), end: (91.0pt, 13.5pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (91.0pt, 13.5pt), end: (93.7pt, 14.4pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (93.7pt, 14.4pt), end: (96.3pt, 14.4pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (96.3pt, 14.4pt), end: (99.0pt, 15.3pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (99.0pt, 15.3pt), end: (101.7pt, 16.8pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (101.7pt, 16.8pt), end: (104.3pt, 16.8pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (104.3pt, 16.8pt), end: (107.0pt, 20.5pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (107.0pt, 20.5pt), end: (109.7pt, 22.7pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (109.7pt, 22.7pt), end: (112.3pt, 22.7pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (112.3pt, 22.7pt), end: (115.0pt, 22.7pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (115.0pt, 22.7pt), end: (117.7pt, 22.7pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (117.7pt, 22.7pt), end: (120.3pt, 22.7pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (120.3pt, 22.7pt), end: (123.0pt, 22.7pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (123.0pt, 22.7pt), end: (125.7pt, 23.3pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (125.7pt, 23.3pt), end: (128.3pt, 25.5pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (128.3pt, 25.5pt), end: (131.0pt, 29.9pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (131.0pt, 29.9pt), end: (133.7pt, 31.5pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (133.7pt, 31.5pt), end: (136.3pt, 32.1pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (136.3pt, 32.1pt), end: (139.0pt, 32.1pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (139.0pt, 32.1pt), end: (141.7pt, 32.1pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (141.7pt, 32.1pt), end: (144.3pt, 33.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (144.3pt, 33.0pt), end: (147.0pt, 33.0pt), stroke: 1pt + rgb("#5A5E66")))]],
  [`agent/chat.py` 6,637 → 5,487 \ #box(width: 150pt, height: 36pt)[#place(line(start: (3.0pt, 3.0pt), end: (5.7pt, 3.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (5.7pt, 3.0pt), end: (8.3pt, 3.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (8.3pt, 3.0pt), end: (11.0pt, 3.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (11.0pt, 3.0pt), end: (13.7pt, 3.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (13.7pt, 3.0pt), end: (16.3pt, 3.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (16.3pt, 3.0pt), end: (19.0pt, 3.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (19.0pt, 3.0pt), end: (21.7pt, 3.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (21.7pt, 3.0pt), end: (24.3pt, 3.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (24.3pt, 3.0pt), end: (27.0pt, 3.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (27.0pt, 3.0pt), end: (29.7pt, 3.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (29.7pt, 3.0pt), end: (32.3pt, 3.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (32.3pt, 3.0pt), end: (35.0pt, 3.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (35.0pt, 3.0pt), end: (37.7pt, 3.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (37.7pt, 3.0pt), end: (40.3pt, 3.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (40.3pt, 3.0pt), end: (43.0pt, 3.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (43.0pt, 3.0pt), end: (45.7pt, 3.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (45.7pt, 3.0pt), end: (48.3pt, 3.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (48.3pt, 3.0pt), end: (51.0pt, 3.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (51.0pt, 3.0pt), end: (53.7pt, 3.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (53.7pt, 3.0pt), end: (56.3pt, 3.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (56.3pt, 3.0pt), end: (59.0pt, 9.6pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (59.0pt, 9.6pt), end: (61.7pt, 9.6pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (61.7pt, 9.6pt), end: (64.3pt, 9.6pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (64.3pt, 9.6pt), end: (67.0pt, 9.6pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (67.0pt, 9.6pt), end: (69.7pt, 9.6pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (69.7pt, 9.6pt), end: (72.3pt, 9.6pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (72.3pt, 9.6pt), end: (75.0pt, 9.6pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (75.0pt, 9.6pt), end: (77.7pt, 9.6pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (77.7pt, 9.6pt), end: (80.3pt, 13.9pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (80.3pt, 13.9pt), end: (83.0pt, 13.9pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (83.0pt, 13.9pt), end: (85.7pt, 13.9pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (85.7pt, 13.9pt), end: (88.3pt, 13.9pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (88.3pt, 13.9pt), end: (91.0pt, 13.9pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (91.0pt, 13.9pt), end: (93.7pt, 13.9pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (93.7pt, 13.9pt), end: (96.3pt, 13.9pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (96.3pt, 13.9pt), end: (99.0pt, 19.8pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (99.0pt, 19.8pt), end: (101.7pt, 19.8pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (101.7pt, 19.8pt), end: (104.3pt, 19.8pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (104.3pt, 19.8pt), end: (107.0pt, 19.8pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (107.0pt, 19.8pt), end: (109.7pt, 33.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (109.7pt, 33.0pt), end: (112.3pt, 33.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (112.3pt, 33.0pt), end: (115.0pt, 33.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (115.0pt, 33.0pt), end: (117.7pt, 33.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (117.7pt, 33.0pt), end: (120.3pt, 33.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (120.3pt, 33.0pt), end: (123.0pt, 33.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (123.0pt, 33.0pt), end: (125.7pt, 33.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (125.7pt, 33.0pt), end: (128.3pt, 33.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (128.3pt, 33.0pt), end: (131.0pt, 33.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (131.0pt, 33.0pt), end: (133.7pt, 33.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (133.7pt, 33.0pt), end: (136.3pt, 33.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (136.3pt, 33.0pt), end: (139.0pt, 33.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (139.0pt, 33.0pt), end: (141.7pt, 33.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (141.7pt, 33.0pt), end: (144.3pt, 33.0pt), stroke: 1pt + rgb("#5A5E66")))#place(line(start: (144.3pt, 33.0pt), end: (147.0pt, 33.0pt), stroke: 1pt + rgb("#5A5E66")))]],
)

*走势的最后一点比快照旧。* CI 历史停在 `27a2619b1`（09-30 05:51 UTC），快照取自 `aaed8e4f7`（2026-09-30 10:03 UTC，含 main 的 \#2199 拆 AdminDashboardPage）。两者之间的差值不全是同一棵树上的变化，页面把这件事写在 ③ 的脚注里。

== 本轮按 main 现行规则重采

这一稿的数字来自把 main 合进来之后的一次采集。两道检查的数动了，性质不同：

#block(breakable: false)[
#tbl((1.35fr, 0.5fr, 0.5fr, 2.5fr),
  th[检查], th[上一稿], th[本稿], th[为什么动],
  [① 场景（`scene-ratchet.py`）], [128], [126], [规则变更 \#2209：一个页面把渲染交给同目录的 `<Page>View.vue` 时算容器，不再计入未达标。指纹变了，按新起点比 #rule],
  [② 领域不直连（`domain-import-guard`）], [157], [156], [main 的空间重构把 `member_participating_service` 搬走，它那 1 条豁免随之删掉。是还债，不是改规则 \#real],
)
]

= 本轮改掉的一处指纹口径

上一稿里 `domain-import-guard` 的指纹是跟着豁免表走的：`_EXEMPT` 登记表就写在判定它的那个测试模块里，删掉一条豁免，整份文件的哈希就变，页面会把「还掉一条债」读成「换了把尺子」，正好把要数的东西挡掉。

改法是给这类检查点名它自己的基线表（`_EXEMPT` / `_LEDGER` / `BASELINE`），取指纹时用 `ast` 把这条顶层语句整段切掉——语句到哪儿结束由语法回答，不靠数括号。和 `.importlinter` 里切掉 `ignore_imports` 是同一个道理。自测里补了三条：还掉一条不再动指纹、改断言仍然动、三道账本检查都必须点出自己的表。

同一件事的另一面：指纹是对整份判定文件取的哈希，所以给脚本本身加个 `--json` 参数也会让指纹变。本分支就是如此（vue-tsc、设计令牌两道脚本都加了 `--json` 并顺手排了 import），判定逻辑没动，数字也就没动。

规则变化前后不画成一条线：指纹一变页面就开一个新的比较起点，ready 数量的跳变不当重构成果。\#2217 合入后按同样办法再采一次、再分段。

= 3　数据从哪来、多久刷新

#tbl((auto, 1.4fr, 1fr, 1fr),
  th[做法], th[怎么做], th[好处], th[状态],
  [*A 读 CI 快照（推荐，已实现）*], [扩展 `arch-metrics.yml`：装前后端依赖后按各检查自己的命令跑一遍，每道检查输出一条 JSON（实际数、冻结数、失效豁免），采集脚本再补上区、检查名和规则指纹，合成一份 `ratchet-snapshot.json` 上传成 CI 产物（保留 90 天）。], [和 CI 同源，不复制判断；后端读最新一份存库后不受 90 天限制], [脚本与工作流已在分支 `task/4db75afb` 上（head `aaed8e4f7`）；后端存库与页面未做],
  [B 后端自己跑], [后端定时在服务器上跑检查器。], [不依赖 CI], [不可行：部署镜像里没有前端源码、node 依赖和 git 历史],
  [C 结果提交进仓库], [CI 把结果写成 JSON 提交回 main。], [看得见改动], [不可行：每次合入多一个提交，又触发 CI，和合并队列冲突],
)

已核实：平台 App 能列出并下载 `arch-metrics` 记录，现有 55 份、保留 90 天。页面显示「采集提交 · 采集时间」；最近一次记录晚于部署提交时，页面照实写两个 SHA，不假装同步。

== 这次采集的几个约定

- *没跑到就是不记，不记 0。* 命令不在、超时、没吐记录、吐了两行，都记 `not_collected`，快照里 `actual` 是 `null`。快照里不会出现「用 0 冒充没测到」。
- *退出码仍是判据。* 0 通过 / 1 违规 / 2 判不了；`--json` 只加输出、不改判定，默认模式的 stdout 逐字节不变。pytest 的账本检查退出码 2 及以上（被打断、用法错、没收集到用例）都算「判不了」，且跑之前先探一下 pytest 在不在——`python -m pytest` 缺 pytest 时也退 1，和「有测试失败」分不开。
- *账本检查的实际数取自它自己的登记表。* 三道 pytest 账本（领域直连、适配器跨界、`is_private` 读点）没有 `--json` 出口，采集脚本跑 pytest 拿退出码，再把测试模块 import 进来读它的登记表；测试失败时数量记 `null` 而不是 0。`is_private` 那道数的是读点：12 个文件、合计 20 处，不是 12。

#pagebreak()
= 4　页面结构：三种做法

#let wf(body) = block(stroke: 0.6pt + rgb("#E2E3E6"), radius: 6pt, inset: 6pt, width: 100%, fill: rgb("#F7F8FA"))[#set text(size: 7.5pt); #body]
#let bar(w, t, c: rgb("#FFFFFF")) = box(width: w, height: 11pt, fill: c, stroke: 0.5pt + rgb("#E2E3E6"), radius: 2pt, inset: (x: 3pt, y: 2pt))[#t]

#grid(columns: (1fr, 1fr, 1fr), gutter: 10pt,
  [*一、按方面分区（推荐）* \
   #wf[#bar(100%, [四个方面一览：状态 · 还冻结的债 · 起点→现在], c: rgb("#FDF1E2")) \ #v(2pt)
     #bar(100%, [① 场景　口径修正中 + 挂载证据另列]) \ #bar(32%, [KPI]) #bar(32%, [KPI]) #bar(32%, [KPI]) \ #bar(100%, [检查表 · 点开看详情]) \ #v(2pt)
     #bar(100%, [② 依赖边界]) \ #bar(100%, [检查表 + 走势]) \ #v(2pt) #bar(100%, [③ 文件规模]) \ #bar(100%, [④ 类型与样式]) \ #bar(100%, [⑤ 采集出的 10 条记录])]
   先回答「哪一块在变好、哪一块没动」，再往下钻到文件和 PR。区的划分跟着检查走，新加一道检查只是在某区多一行。],
  [*二、一张总表* \
   #wf[#bar(100%, [筛选：方面 · 结果 · 有变化]) \ #v(2pt)
     #for i in range(7) [#bar(100%, [检查 · 实际 · 冻结 · 变化 · 走势]) \ ]]
   13 行全在一张表里，扫得快、好排序。但没有每个方面的汇总，「哪里卡住」要自己从表里读出来。],
  [*三、变化时间线优先* \
   #wf[#bar(100%, [最近的变化], c: rgb("#FDF1E2")) \
     #for i in range(5) [#bar(100%, [PR · 哪个数动了 · 哪一类]) \ ] #v(2pt)
     #bar(100%, [现状汇总（折叠）])]
   最直接回答「最近谁还了什么债」。可现在只有两天的历史，首屏会很空；而且没变化的检查在时间线里看不见，「卡住」反而被藏起来。],
)

结构一已批准（时间线放进每道检查的展开详情里，预览页已这样做）。历史攒够几周后，可以在页面顶部加一个「最近变化」标签页。

预览页（交互版在房间右侧，本页是按它重排的静态示意）首屏，数字同 `aaed8e4f7` 的快照：

#block(stroke: 0.6pt + rgb("#E2E3E6"), radius: 8pt, inset: 10pt, width: 100%)[
  #set text(size: 8pt)
  #text(size: 12pt, weight: "bold", fill: rgb("#191A1C"))[棘轮] #h(6pt) #text(fill: rgb("#5A5E66"))[架构还债进度：每道检查现在是多少、冻结了多少、比起点好了还是差了]\
  #text(fill: rgb("#5A5E66"))[采集提交 `aaed8e4f7` · 提交 2026-09-30 09:58 · 采集 2026-09-30 10:03 UTC · 带规则指纹 10 / 10 道]\
  #v(4pt)
  #tbl((1.2fr, auto, 1.4fr, 1.5fr, 1.2fr),
    th[方面], th[结果], th[还冻结着的债], th[同口径起点 → 现在], th[判断],
    [能独立运行的前端场景], [#recal], [规则评分未达标 126 个], [规则在改，暂不比较], [等规则定稿],
    [前后端依赖边界], [7 #ok], [前端 116 处 · 后端契约 254 条（冻结 256）], [前端 127 → 116 · C3 178 → 174], [前端在降；C1、C2 没动],
    [文件规模], [#ok], [26 个文件，超 23,677 行], [34 → 26 · 34,753 → 23,677], [在降；拆文件和删功能都有],
    [类型与样式], [3 #ok], [令牌 37 · 固定色 6（冻结 7）], [#miss], [无历史不判断],
  )
]

= 5　已经定下来的

+ *页面结构*：按四个方面分区（结构一），时间线放进每道检查的展开详情。已批准。
+ *数据来源*：方案 A——合入 main 后实跑各检查、把快照存成 CI 产物、由后端读最新一份存库，历史因此在 main 上自然积累。已批准，采集这半边已在 PR \#2205 里。
+ *参考指标*：热点文件、函数内导入这些没有「通过/违规」的看板指标不进本页，留在文档站的「架构指标」页。已批准。
+ *人工标注*：还没上线的分类（还债 / 删除功能 / 新增豁免）由人工标、页面上挂「样例」标签；上线版只自动识别规则变更。
+ *接下来*：\#2205 在同一个 head 上 CI 全过、独立审完就按正常队列合入；随后做后端存库与真实后台页 `views/admin/AdminRatchetPage.vue`（路由与导航按布局 B 的约定各加一行）。新界面出 PDF 后由王长鑫给用户做可见变化的最终确认。

#pagebreak()
= 6　定了之后怎么做

三个 PR，依次合入，每个都报 head、CI、截图和未验证部分：

+ *采集*（已在 PR \#2205 里）：各检查的 `--json`、采集脚本、工作流里合入 main 后跑并上传快照。只加输出参数，不改任何判定；`frontend_grade.py` 与 `scene-ratchet.py` 的规则由重构话题维护，这里只读。
+ *后端*：后台管理接口读最新快照并存库，按提交去重。
+ *前端*：新页面 `views/admin/AdminRatchetPage.vue`。共享文件只按布局话题的约定各加一行：路由一条、后台导航一项。页头、指标卡和表格用现成的后台组件。

结构已批准，但界面这一份仍等这一稿 PDF 被看过再开 PR。

= 7　没做和没核实的

- 快照是本机在分支 `aaed8e4f7` 上跑的一次，*不是 CI 跑出来的那一份*：PR \#2205 已开，但合并后的采集工作流要在合入 main 之后才会在 CI 上跑。工作流这一步的 YAML 只在本机 lint 过。
- 走势的最后一点（09-30 05:51 的 `27a2619b1`）早于快照的提交，中间隔着 main 的 \#2199。29 → 26 的差有一部分出在那里，页面已写明。
- 场景一区没有方向：main 上 \#2209（HuanCheng65）已经换过判定规则，这一稿按新规则重采；补强 \#2217 仍在 draft、未合。规则一改指纹就变，本页自动换新起点，不把 ready 数量的跳变当成重构成果。
- 挂载证据是单独一件事：本轮在 `aaed8e4f7` 上跑 `vitest run src/views/demo/catalog.spec.ts`，37 个组件、125 格全部挂载通过（128 passed / 0 failed）。它是跑出来的，不是规则给的，页面单独列一列。
- 固定色、三个 pytest 账本和 vue-tsc 没有历史：CI 看板从来没存过，合入后才开始积累。上一版 PDF 里这三格是「未采集」，现在有当次数了，但还没有第二个点可比。
- 「还债 / 删除功能」的分类是我按 PR 标题人工标的，预览页上带 #tag(fill: white, ink: rgb("#5A5E66"))[样例] 标签。上线版只自动识别规则变更（靠指纹），其余变化直接列 PR，由人判断。
- 预览页在真浏览器里截图核对过（1440 / 1100 两个宽度，无 JS 报错、无横向溢出）；页面本身还没进后台，导航项是示意。
