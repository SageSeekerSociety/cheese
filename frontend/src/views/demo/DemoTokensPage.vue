<script setup lang="ts">
// 设计 token 页：每条 token 一个名字、两个色块（浅色 / 深色）、一句「用在哪」。
//
// 值**不是抄的**：全从 `src/style.css` 的 `:root` 与 `:root[data-theme='dark']` 两条
// 选择器里现读（`demoTokens.ts`），读不到就说读不到，不编一个默认值。token 一动这一页
// 立刻跟着动——这一页存在的理由就是「改之前先在这里看一眼会怎么样」。
//
// 深色那一列是**算出来的**，不是把主题切过去：一条 token 的值可以是另一条（深色块的
// `--code-bg: var(--fill-2)`），`var()` 原样刷上去得到的是当前主题的颜色，不是深色档的，
// 引用要就地解开。
import type { TokenPreview } from './demoTokens'

import {
  conditionalValue,
  documentTokenScopes,
  groupColors,
  numericValue,
  previewOf,
  resolveIn,
  tokenNames,
} from './demoTokens'

/** 一条 token 的两档值。读一次就存着：整页要问上百次，每次重算一遍没有意义。 */
const scopes = documentTokenScopes()
const previews = new Map<string, TokenPreview>()
for (const name of tokenNames(scopes)) {
  const preview = previewOf(name, scopes)
  if (preview) previews.set(name, preview)
}

/** 这一条的值。样式表里读不到就给 null——下面每一处都照 null 写「读不到」。 */
function preview(name: string): TokenPreview | null {
  return previews.get(name) ?? null
}

function valueOf(name: string): string {
  return preview(name)?.light ?? ''
}

/** 一条 token 在浅色档下算出来的值。字体那两条会引用 `--font-sans`，也要解开。 */
function stackOf(name: string): string {
  return resolveIn(valueOf(name), scopes.light)
}

/** 一族的名字，按声明顺序。 */
function family(prefix: string): string[] {
  return tokenNames(scopes, prefix)
}

/**
 * 这条 token 在窄视口下另有的那个值，没有就空着。
 *
 * 上面那一列是**定义处**那一版（`demoTokens.ts` 只认 `:root` 自己那一条）；产品另有
 * 一条媒体查询把两条页宽在窄屏改成别的值。这一节画的是一张表，所以那个值补在下面
 * 一行，不把上面那一列改掉——改了，看这页的人就以为桌面上的页宽是那个数。
 */
function narrowOf(name: string): string {
  const value = conditionalValue(name, scopes)
  return value ? `窄视口下 ${value}` : ''
}

/* ------------------------------------------------------------------ 用在哪 */

/**
 * 每条 token 用在哪。这是**人写的**，值才是现读的：CSS 的注释读不出来，而「用在哪」
 * 正是这一页要回答的那半件事。写的是 `style.css` 自己注释里那句话。
 */
const ROLE: Record<string, string> = {
  '--ink': '标题',
  '--text': '正文',
  '--muted': '次要信息',
  '--faint': '元信息：时间、计数、id',
  '--line': '细线',
  '--line-2': '分隔线；也是选中行的底',
  '--fill': '悬停、内嵌',
  '--fill-2': '内嵌的第二级',
  '--canvas': '页面底',
  '--surface': '卡片、面板',
  '--raised': '浮层：菜单、对话框、抽屉',
  '--accent': '主色，只给关键动作',
  '--accent-press': '主色按下',
  '--accent-ink': '主色文字',
  '--accent-wash': '主色淡底',
  '--ok': '通过、未读的点',
  '--ok-ink': '通过的文字',
  '--ok-wash': '通过的淡底',
  '--warn': '等待的点',
  '--warn-ink': '等待的文字',
  '--warn-wash': '等待的淡底',
  '--danger': '出错的点',
  '--danger-ink': '出错的文字',
  '--danger-wash': '出错的淡底',
  '--signal-yellow': '侧栏红黄绿灯的黄',
  '--code-bg': '反色代码块的底',
  '--code-ink': '反色代码块的字',
  '--code-comment': '注释',
  '--code-keyword': '关键字',
  '--code-string': '字符串',
  '--code-number': '数字',
  '--code-type': '类型',
  '--code-function': '函数',
  '--code-deletion': '删掉的行',
  '--agent-ink': '队友头像上的字',
  '--agent-tone-0': '队友色 0',
  '--agent-tone-1': '队友色 1',
  '--agent-tone-2': '队友色 2',
  '--agent-tone-3': '队友色 3',
  '--agent-tone-4': '队友色 4',
  '--usage-collab': '额度条 · 协作',
  '--usage-ask': '额度条 · 问答',
  '--usage-write': '额度条 · 写作',
  '--usage-compute': '额度条 · 算力',
  '--chart-1': '图表序列 1 · 蓝',
  '--chart-2': '图表序列 2 · 青',
  '--chart-3': '图表序列 3 · 陶',
  '--chart-4': '图表序列 4 · 紫',
  '--chart-5': '图表序列 5 · 橄榄',
  '--chart-6': '图表序列 6 · 玫瑰',
  '--chart-7': '图表序列 7 · 天蓝',
  '--chart-8': '图表序列 8 · 赭',
  '--focus-ring': '键盘焦点的圈',
  '--overlay': '对话框的遮罩',
  '--selection-bg': '选中文字的底',
  '--scrollbar-thumb': '滚动条滑块',
  '--scrollbar-thumb-hover': '滑块 · 悬停',
  '--scrollbar-track': '滚动条槽',
  '--inverse-surface': '浮在页面上的反色块 · 底',
  '--inverse-ink': '反色块 · 字',
  '--inverse-fill': '反色块 · 悬停',
  '--logo-primary': '品牌光晕 · 主',
  '--logo-secondary': '品牌光晕 · 次',
  '--category-material': '知识库 · 文件',
  '--category-text': '知识库 · 文本',
  '--category-link': '知识库 · 链接',
  '--category-code': '知识库 · 代码',
}

/** 颜色分节在 `demoTokens.ts` 里：那一份可测（spec 拿真样式表跑，断言没有一条落进「其余」）。 */
const colorSections = groupColors(scopes)

/* ------------------------------------------------------------ 圆角与字号 */

const RADIUS_ROLE: Record<string, string> = {
  '--radius-sm': 'chips、标签、小徽标',
  '--radius-md': '按钮、输入、列表行',
  '--radius-lg': '卡片、面板、对话框',
  '--radius-pill': '胶囊、圆头像',
}

const radii = family('--radius-')

const SHADOW_ROLE: Record<string, string> = {
  '--shadow-1': '菜单、下拉、浮起的提示',
  '--shadow-2': '对话框、抽屉',
}

const shadows = family('--shadow-')

const widths = family('--page-w')
const WIDTH_ROLE: Record<string, string> = {
  '--page-w': '表单和卡片列表（默认）',
  '--page-w-wide': '多列看板',
  '--page-w-admin': '后台工作台；断点挂在内容列上，不看视口',
  '--page-w-read': '连续正文；920 一行会排到 60 个字以上，容易跳行',
}

/**
 * 字号阶梯。字号本身不是 token（写出来就是那五个），配死的行高才是——下面那几行的
 * `lh` 从样式表现读，改一处这一页就跟着变。
 */
const SIZES: ReadonlyArray<{ size: number; lh: string; role: string }> = [
  { size: 12, lh: '--lh-12', role: '元信息：时间、计数、id' },
  { size: 13, lh: '--lh-13', role: '密排正文；13px 是可读性下限' },
  { size: 14, lh: '--lh-14', role: '正文' },
  { size: 15, lh: '--lh-15', role: '小节、卡片标题' },
  { size: 18, lh: '--lh-18', role: '弹层标题' },
  { size: 23, lh: '--lh-23', role: '页标题' },
]

/** 为长文单开的两档，不是阶梯上的新一级：一行排到三行以上时才换过来。 */
const LONG_FORM: ReadonlyArray<{ size: number; lh: string; role: string }> = [
  { size: 14, lh: '--lh-14-loose', role: '.t-body-readable' },
  { size: 15, lh: '--lh-15-reading', role: '.t-reading' },
]

/* ------------------------------------------------------------------ 动效 */

const DURATION_ROLE: Record<string, string> = {
  '--dur-press': '按下那一刻的回执',
  '--dur-quick': '悬停变色；离开、收起',
  '--dur-base': '到达、展开；指示条与重排',
  '--dur-slow': '抽屉、侧栏；卡片展开成详情',
}

const EASE_ROLE: Record<string, string> = {
  '--ease-out': '到达',
  '--ease-in': '离开',
  '--ease-standard': '原地变化',
}

const durations = family('--dur-')
const eases = family('--ease-')

const EASE_POINTS = /cubic-bezier\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*\)/

/**
 * 把一条曲线画出来：横轴是时间、竖轴是走完了多少。
 *
 * 画的是产品声明的那四个数，不是照着一张三等份的图描的——所以曲线改了这一页跟着改。
 * 认不出这个写法（不是 cubic-bezier）就不画，只留值本身。
 */
function curvePath(value: string): string {
  const hit = EASE_POINTS.exec(value)
  if (!hit) return ''
  const [x1, y1, x2, y2] = hit.slice(1, 5).map(Number)
  if ([x1, y1, x2, y2].some((n) => !Number.isFinite(n))) return ''
  const at = (n: number): number => Math.round(n * 40)
  return `M 0 40 C ${at(x1)} ${40 - at(y1)} ${at(x2)} ${40 - at(y2)} 40 0`
}

/* ------------------------------------------------------------------ 层级 */

const Z_ROLE_TITLE = '一个梯子：读它在哪一带，再挑名字；组件里写一个裸数字就是等着踩到隔壁那一层。'

/** 层级按**数值**排，不按声明顺序：这一节要回答的是「谁在上面」。 */
const zLadder = family('--z-')
  .map((name) => ({ name, value: valueOf(name), z: numericValue(valueOf(name)) }))
  .filter((row) => row.z !== null)
  .sort((a, b) => (a.z ?? 0) - (b.z ?? 0))

const fonts = family('--font-')
const FONT_ROLE: Record<string, string> = {
  '--font-sans': '界面正文；只用系统字体，不下载字体文件',
  '--font-display': '品牌字，用在页标题上',
  '--font-mono': '代码、路径、id',
}
</script>

<template>
  <div class="tokens">
    <h1 class="tokens-name t-page-title">设计 token</h1>
    <p class="tokens-lede">
      这一页列的是产品真在用的 token（值取自 <code>frontend/src/style.css</code> 当前版本，下面每一条都是现读的），
      每条给浅色和深色两个色块——两个色块读的是同一个变量名，各按自己那一档解出来的。组件站自己只用这些名字，
      一个色值都不写死；所以 token 一动，这一页立刻变。
    </p>
    <p v-if="!previews.size" class="tokens-empty">
      这个环境里读不到样式表，所以下面报不出任何一条的值。不是「一条都没有」。
    </p>

    <section v-for="section in colorSections" :key="section.name" class="tokens-section">
      <h2 class="tokens-h2">{{ section.name }}</h2>
      <p class="tokens-note">{{ section.note }}</p>
      <ul class="swatches">
        <li v-for="name in section.names" :key="name" class="swatch">
          <!-- The swatch is painted with the **resolved value** (the declared colour literal
               itself): this page draws that literal. Painting var(--name) instead gives the
               current theme's value, which makes the light and dark swatches identical. -->
          <span class="swatch-paint" :style="{ background: preview(name)?.light }" />
          <span class="swatch-paint" :style="{ background: preview(name)?.dark }" />
          <span class="swatch-text">
            <code class="swatch-name">{{ name }}</code>
            <span class="swatch-role">{{ ROLE[name] ?? '' }}</span>
            <span class="swatch-value">{{ preview(name)?.light }}</span>
          </span>
        </li>
      </ul>
    </section>

    <section class="tokens-section">
      <h2 class="tokens-h2">圆角与投影</h2>
      <p class="tokens-note">
        圆角四档，主题无关——深色下角不会换个形状，所以只在 :root 写一次。投影是两档，只给浮层： 卡片靠一条 --line
        边框立住，加阴影就把「这是一块」说了两遍。
      </p>
      <ul class="radii">
        <li v-for="name in radii" :key="name" class="radius">
          <span class="radius-box" :style="{ borderRadius: `var(${name})` }" />
          <code class="radius-name">{{ name }}</code>
          <span class="radius-value">{{ valueOf(name) }}</span>
          <span class="radius-role">{{ RADIUS_ROLE[name] ?? '' }}</span>
        </li>
        <li v-for="name in shadows" :key="name" class="radius">
          <span class="radius-box radius-box-shadow" :style="{ boxShadow: `var(${name})` }" />
          <code class="radius-name">{{ name }}</code>
          <span class="radius-role">{{ SHADOW_ROLE[name] ?? '' }}</span>
        </li>
      </ul>
    </section>

    <section class="tokens-section">
      <h2 class="tokens-h2">字号与行高</h2>
      <p class="tokens-note">一行盒配一个字号，所以竖直的节奏是字号自己的属性，到调用处不再决定一次。</p>
      <table class="tokens-table">
        <thead>
          <tr>
            <th scope="col">样子</th>
            <th scope="col">字号</th>
            <th scope="col">行高</th>
            <th scope="col">用在哪</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in [...SIZES, ...LONG_FORM]" :key="`${row.size}-${row.lh}`">
            <td class="tokens-sample" :style="{ fontSize: `${row.size}px` }">组件站</td>
            <td>
              <code>{{ row.size }}px</code>
            </td>
            <td>
              <code>{{ row.lh }}</code>
              <span v-if="valueOf(row.lh)" class="tokens-sub">{{ valueOf(row.lh) }}</span>
            </td>
            <td>{{ row.role }}</td>
          </tr>
        </tbody>
      </table>
    </section>

    <section class="tokens-section">
      <h2 class="tokens-h2">动效</h2>
      <p class="tokens-note">四条时长、三条曲线。到达减速、离开加速，离开比到达快一档。动效台上能一条条看。</p>
      <table class="tokens-table">
        <thead>
          <tr>
            <th scope="col">token</th>
            <th scope="col">值</th>
            <th scope="col">用在哪</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="name in durations" :key="name">
            <td>
              <code>{{ name }}</code>
            </td>
            <td>
              <code>{{ valueOf(name) }}</code>
            </td>
            <td>{{ DURATION_ROLE[name] ?? '' }}</td>
          </tr>
          <tr v-for="name in eases" :key="name">
            <td>
              <code>{{ name }}</code>
            </td>
            <td>
              <svg v-if="curvePath(valueOf(name))" class="ease" viewBox="0 0 40 40" aria-hidden="true">
                <path :d="curvePath(valueOf(name))" fill="none" stroke="currentColor" stroke-width="1.5" />
              </svg>
              <code class="ease-value">{{ valueOf(name) }}</code>
            </td>
            <td>{{ EASE_ROLE[name] ?? '' }}</td>
          </tr>
        </tbody>
      </table>
    </section>

    <section class="tokens-section">
      <h2 class="tokens-h2">层级</h2>
      <p class="tokens-note">{{ Z_ROLE_TITLE }}</p>
      <ul class="ladder">
        <li v-for="row in zLadder" :key="row.name" class="ladder-row">
          <code class="ladder-name">{{ row.name }}</code>
          <span class="ladder-value">{{ row.value }}</span>
        </li>
      </ul>
    </section>

    <section class="tokens-section">
      <h2 class="tokens-h2">字体与页宽</h2>
      <p class="tokens-note">字体只用系统装好的那些，一个字体文件都不下载；页宽是内容栏自己的属性。</p>
      <ul class="defs">
        <li v-for="name in fonts" :key="name" class="def">
          <code class="def-name">{{ name }}</code>
          <span class="def-role">{{ FONT_ROLE[name] ?? '' }}</span>
          <p class="def-value" :style="{ fontFamily: `var(${name})` }">组件站 Component Catalog 123</p>
          <span class="def-stack">{{ stackOf(name) }}</span>
        </li>
        <li v-for="name in widths" :key="name" class="def">
          <code class="def-name">{{ name }}</code>
          <span class="def-role">{{ WIDTH_ROLE[name] ?? '' }}</span>
          <span class="def-value">{{ valueOf(name) }}</span>
          <!-- The product's own breakpoints override these widths in a `@media` block, so
               the definition-site value above is not the whole story. This line is the
               other half: a note under the table, not a second token. -->
          <span v-if="narrowOf(name)" class="def-stack">{{ narrowOf(name) }}</span>
        </li>
      </ul>
    </section>
  </div>
</template>

<style scoped>
.tokens {
  display: flex;
  flex-direction: column;
  gap: 28px;
  padding: 16px 24px 48px;
}

.tokens-name {
  margin: 0 0 -20px;
}

.tokens-lede {
  max-width: 74ch;
  margin: 0;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}

.tokens-lede code,
.swatch-name,
.radius-name,
.tokens-table code,
.ladder-name,
.def-name {
  font-family: var(--font-mono);
  font-size: 12px;
}

.tokens-lede code {
  color: var(--faint);
}

.tokens-empty {
  margin: 0;
  font-size: 13px;
  color: var(--warn-ink);
}

.tokens-section {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.tokens-h2 {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
  color: var(--ink);
}

.tokens-note {
  max-width: 74ch;
  margin: 0 0 8px;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}

/* ---- 色块 ---- */
.swatches {
  display: grid;
  gap: 10px;

  /* 两个色块 + 一行字，最窄 250px：窄屏上折成一列。 */
  grid-template-columns: repeat(auto-fill, minmax(min(250px, 100%), 1fr));
  padding: 0;
  margin: 0;
  list-style: none;
}

.swatch {
  display: flex;
  gap: 10px;
  align-items: center;
  padding: 8px;
  background: var(--surface);
  border: 1px solid var(--line-2);
  border-radius: var(--radius-md);
}

.swatch-paint {
  flex: none;
  width: 34px;
  height: 34px;
  border: 1px solid var(--line-2);
  border-radius: var(--radius-sm);
}

.swatch-text {
  display: flex;
  min-width: 0;
  flex-direction: column;
}

.swatch-name {
  color: var(--ink);
  overflow-wrap: anywhere;
}

.swatch-role {
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--muted);
}

.swatch-value {
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--faint);
  overflow-wrap: anywhere;
}

/* ---- 圆角 ---- */
.radii {
  display: flex;
  flex-wrap: wrap;
  gap: 16px;
  padding: 0;
  margin: 0;
  list-style: none;
}

.radius {
  display: flex;
  width: 120px;
  flex-direction: column;
  gap: 2px;
}

.radius-box {
  height: 46px;
  margin-bottom: 6px;
  background: var(--fill);
  border: 1px solid var(--line-2);
}

/* 投影那块要垫一个不透明的面：阴影画在盒子背后，底下是画布就看不出它有边。 */
.radius-box-shadow {
  background: var(--surface);
  border-color: var(--line);
}

.radius-name {
  color: var(--ink);
}

.radius-value,
.radius-role {
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--faint);
}

/* ---- 表 ---- */
.tokens-table {
  width: 100%;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--text);
  text-align: left;
  border-collapse: collapse;
}

.tokens-table th {
  padding: 6px 12px 6px 0;
  font-size: 12px;
  font-weight: 600;
  color: var(--faint);
  border-bottom: 1px solid var(--line);
}

.tokens-table td {
  padding: 6px 12px 6px 0;
  vertical-align: top;
  border-bottom: 1px solid var(--line);
}

.tokens-table code {
  color: var(--ink);
}

.tokens-sub {
  margin-left: 6px;
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--faint);
}

.tokens-sample {
  font-weight: 600;
  color: var(--ink);
}

/* 曲线：横轴时间、竖轴走完了多少，四个数就是产品声明的那四个。 */
.ease {
  width: 34px;
  height: 34px;
  margin-right: 8px;
  color: var(--accent);
  vertical-align: middle;
}

.ease-value {
  vertical-align: middle;
}

/* ---- 层级 ---- */
.ladder {
  display: grid;
  gap: 2px;
  grid-template-columns: repeat(auto-fill, minmax(min(200px, 100%), 1fr));
  padding: 0;
  margin: 0;
  list-style: none;
}

.ladder-row {
  display: flex;
  gap: 8px;
  align-items: baseline;
  justify-content: space-between;
  padding: 3px 8px;
  background: var(--fill-2);
  border-radius: var(--radius-sm);
}

.ladder-value {
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--faint);
}

/* ---- 字体与页宽 ---- */
.defs {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 0;
  margin: 0;
  list-style: none;
}

.def {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.def-name {
  color: var(--ink);
}

.def-role {
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--faint);
}

.def-value {
  max-width: 74ch;
  margin: 2px 0 0;
  font-size: 15px;
  line-height: var(--lh-15);
  color: var(--text);
  overflow-wrap: anywhere;
}

.def-stack {
  max-width: 74ch;
  font-family: var(--font-mono);
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--faint);
  overflow-wrap: anywhere;
}

@media (width <= 900px) {
  .tokens {
    padding: 16px 16px 40px;
  }

  /* 四列的表在手机上放不下：表头留一行、每行折成两列（字号 + 行高），说明另起一行。 */
  .tokens-table thead {
    display: none;
  }

  .tokens-table tr {
    display: grid;
    gap: 0 12px;
    grid-template-columns: auto 1fr;
    padding: 8px 0;
  }

  .tokens-table td {
    padding: 0;
    border: 0;
  }

  .tokens-table td:last-child {
    grid-column: 1 / -1;
    color: var(--muted);
  }
}
</style>
