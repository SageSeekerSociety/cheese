<script setup lang="ts">
// 现场里的一步：动词成列，参数占满剩下的宽度，时间悬停才出现。参数太长时截断而
// 不是折行 —— 点这一行摊开全文。挂了的那一步，错误摘要另起一行缩进到参数那一列，
// 它自己也能点开。
//
// 这一只只画，取数一滴不进：摊开的那一步打印了什么由 `load` 递进来（`usePanelSite`
// 那一包里的 `loadStepOutput`）。哪一步摊开了记在外面 —— 读的是 `block.id`，所以
// 一次安静的刷新不会把读的人刚点开的那一行又收回去。
import type { Block, ScriptCall } from '../../cx_types'

import { argDisplay, eventArg, eventFailed, eventVerb, isProseArg, isRunRecord } from '../../lib/siteLog'
import { isPlatformEvent, mcpServerOf, toolLabel } from '../../lib/toolLabels'

import SiteStepOutput from './SiteStepOutput.vue'

import { clockTime } from '@/lib/dateText'

const props = defineProps<{
  block: Block
  /** 这一步摊开了没有（按 block id 记在外面）。 */
  expanded: boolean
  /** 名册 handle → 名字：平台写的一句话里点到人时照它换成名字。 */
  memberNames: Record<string, string>
  /** 这段对话的 id：摊开一步之后按它去取那一段输出。 */
  topicId: string | null
  /** 取这一步打印了什么（取数那一层知道在哪间房里找）。 */
  load: (blockId: string) => Promise<string>
}>()

defineEmits<{ (e: 'toggle', id: string): void }>()

function fmtTime(iso: string): string {
  // Local 24-hour HH:mm, not raw UTC slice, and not the browser's default locale.
  return clockTime(new Date(iso).getTime())
}

// 摊开这一行之后显示的那一份：参数原文，一个字都没剪。没有第二份时摊开的仍是
// 这一行本身 —— 面板窄到把它省略掉时，展开是唯一能看全的办法。
function eventDetail(b: Block): string {
  return b.meta?.detail || eventArg(b)
}
function eventError(b: Block): string {
  return b.meta?.error ?? ''
}
// 平台写的一句里点到的人是 `<@handle>`：照对话栏换成名字，认不出的留 handle。
function named(text: string): string {
  return text.replace(/<@([^>\s]+)>/g, (_, handle: string) => props.memberNames?.[handle] ?? handle)
}
// 没有参数的那种行（平台提示、后端报错），整句就压在动词上。它会折到三行
// （见样式里的 `.site-act--solo`），全文挂到 title 上，鼠标停一下看全。
function soloVerb(b: Block): string {
  return eventArg(b) ? '' : eventVerb(b)
}
// 一段脚本里调过的工具（pi 的 codemode）：脚本这一行下面一样一行，按调的先后。
function scriptCalls(b: Block): ScriptCall[] {
  return Array.isArray(b.meta?.calls) ? (b.meta.calls as ScriptCall[]) : []
}
// 圆点分级: solid = platform action, faint = plain work (structured fields
// only — never guessed from the content text).
function eventPlatform(b: Block): boolean {
  return isPlatformEvent(b.meta, b.refs)
}
</script>

<template>
  <!-- One step per line: the repeated part is the verb, the informative part is
       the argument, so the verb becomes a fixed column and the argument takes
       the text colour. A failed step keeps the same row and only changes its
       dot. -->
  <div
    class="site-act"
    :class="{
      'site-act--platform': eventPlatform(block),
      'site-act--failed': eventFailed(block),
      'site-act--solo': !eventArg(block),
      'site-act--record': isRunRecord(block),
      'site-act--warn': isRunRecord(block) && block.meta?.severity === 'warn',
    }"
  >
    <i class="site-act__dot" :class="{ 'site-act__dot--platform': eventPlatform(block) }" />
    <span class="site-act__verb" :title="named(soloVerb(block)) || undefined">{{ named(eventVerb(block)) }}</span>
    <button
      v-if="eventArg(block)"
      type="button"
      class="site-act__argtext"
      :class="{
        'site-act__argtext--full': expanded,
        'site-act__argtext--prose': isProseArg(block),
      }"
      data-testid="site-act-arg"
      :aria-expanded="expanded"
      :title="eventArg(block)"
      @click="$emit('toggle', block.id)"
    >
      {{ expanded ? eventDetail(block) : argDisplay(block) }}
    </button>
    <span v-else class="site-act__argtext"></span>
    <span class="site-act__time">{{ fmtTime(block.created_at) }}</span>
    <!-- A failed step: the error summary on its own line, indented to the
         argument column. When a session never started, the original text it
         printed at launch lives here, and the row it belongs to has no
         argument to click. -->
    <button
      v-if="eventFailed(block) && eventError(block)"
      type="button"
      class="site-act__error"
      :class="{ 'site-act__error--full': expanded }"
      data-testid="site-act-error"
      :title="eventError(block)"
      @click="$emit('toggle', block.id)"
    >
      {{ eventError(block) }}
    </button>
    <!-- A script's own calls, one line each, indented under it. -->
    <ul v-if="scriptCalls(block).length" class="site-act__calls" data-testid="site-act-calls">
      <li
        v-for="(call, i) in scriptCalls(block)"
        :key="i"
        class="site-act__call"
        :class="{ 'site-act__call--failed': call.failed }"
      >
        <span v-if="mcpServerOf(call.tool)" class="site-act__server">{{ mcpServerOf(call.tool) }}</span>
        <span class="site-act__verb">{{ toolLabel(call.tool) }}</span>
        <span class="site-act__call-arg" :title="call.arg">{{ call.arg }}</span>
      </li>
    </ul>
    <!-- What this step printed: collapsed, fetched on the first open. -->
    <SiteStepOutput
      v-if="topicId && expanded && block.meta?.output_bytes"
      :block-id="block.id"
      :bytes="block.meta.output_bytes"
      :load="load"
    />
  </div>
</template>

<style scoped>
/* 脚本调过的那几样：缩进到参数那一列，左边一道细线把它们挂在脚本这一行下面。 */
.site-act__calls {
  flex: 1 0 100%;
  margin: 2px 0 2px calc(4em + 14px);
  padding: 0 0 0 10px;
  border-left: 1px solid var(--line);
  list-style: none;
}
.site-act__call {
  display: flex;
  align-items: baseline;
  gap: 8px;
  min-width: 0;
}
.site-act__server {
  flex: 0 0 auto;
  color: var(--muted);
}
.site-act__call-arg {
  min-width: 0;
  overflow: hidden;
  color: var(--text);
  text-overflow: ellipsis;
  white-space: nowrap;
}
.site-act__call--failed .site-act__call-arg {
  color: var(--danger-ink);
}
/* 一步一行。重复的是动词，有信息的是参数，所以动词退成固定宽度的次要列，参数
   拿正文色 —— 扫下来看见的是文件名和命令在变，不是二十遍「执行命令」。 */
.site-act {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  /* 只隔开同一行里的几列。错误摘要折到第二行时，行与行之间不另加空隙：它紧贴着
     出错的那一步。 */
  column-gap: 8px;
  padding: 1px 6px;
  border-radius: var(--radius-sm);
  font-family: var(--font-mono);
  font-size: 13px;
  line-height: 1.55;
  transition: background-color var(--dur-quick) var(--ease-standard);
  /* 离屏的一步不渲染，但留在 DOM 里（Ctrl+F、读屏还找得到）。22px 是「一步一行」的
     估计高度，只在这一行从未渲染过时用；`auto` 记住渲染过的真实高度。
     为什么不给 .site-msg 也加：useSiteClamp 每次更新都要读每一条 .site-msg__body 的
     scrollHeight（决定要不要夹），读离屏的就是强制把它铺开，加了也省不下来。
     测量帧的关掉见 lib/contentVisibility 与下面的 .cv-measure。 */
  content-visibility: auto;
  contain-intrinsic-size: auto 22px;
}
/* 测量帧（向上翻页补偿）：按真实高度铺开。见 lib/contentVisibility。 */
.cv-measure .site-act {
  content-visibility: visible;
}
.site-act:hover {
  background: var(--fill);
}
.site-act:hover .site-act__time {
  opacity: 1;
}
/* 平台动作是这一轮的产出（交出一份成果、写文档、递验收卡），不该和 ls 长得
   一样——靠墨色和字重拉开，不靠琥珀：这一栏里没有主操作。 */
/* 挂了的一步：圆点换成危险色。动词和参数照旧 —— 这一行说的还是它做了什么，
   变的只是它有没有做成。 */
.site-act--failed .site-act__dot {
  background: var(--danger);
}
/* 错误摘要缩进到参数那一列，和上面那一行对齐。这个缩进是圆点 + 间隙 + 动词列
   + 间隙 —— 写成 calc 而不是量出来的一个数，改了上面这一行不用回来改它。
   一条启动失败的原文可能很长：折到三行，整段能说多少说多少，鼠标停一下看全文，
   点开这一行摊开那一份。截成一行的话，读的人只拿到开头那半句，而这一行恰恰是
   出问题时唯一有人真去读的字。 */
.site-act__error {
  display: -webkit-box;
  flex: 0 0 100%;
  order: 2;
  min-width: 0;
  margin: 2px 0 0;
  padding: 0 0 0 calc(5px + 8px + 4em + 8px);
  border: 0;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 3;
  line-clamp: 3;
  overflow: hidden;
  overflow-wrap: anywhere;
  background: none;
  font: inherit;
  text-align: left;
  cursor: pointer;
  color: var(--danger-ink);
  /* 13px 而不是 12：这一行是挂了的那一步上唯一有人真去读的字。 */
  font-size: 13px;
  line-height: 1.5;
}
.site-act__error:focus-visible {
  outline: 1px solid var(--accent);
  outline-offset: 2px;
  border-radius: var(--radius-sm);
}
.site-act__error--full {
  display: block;
  -webkit-line-clamp: none;
  line-clamp: none;
  white-space: pre-wrap;
  word-break: break-word;
}
/* 圆点分级：淡 = 普通的读、搜、跑；实 = 平台动作（cheese 工具、写文档）。 */
.site-act__dot {
  flex: 0 0 auto;
  width: 5px;
  height: 5px;
  border-radius: var(--radius-pill);
  background: var(--faint);
  /* 圆点没有文字基线，按行高把它压到第一行的中线上。 */
  transform: translateY(-3px);
}
.site-act__dot--platform {
  background: var(--ink);
}
/* 运行记录：平台运行中记下的事，不是芝士做的一步。同一套行，空心圆点、正文退一档；
   要留意的（重试、等机器）圆点和正文换成提醒色。 */
.site-act--record .site-act__dot {
  background: none;
  box-shadow: inset 0 0 0 1px var(--faint);
}
.site-act--record .site-act__argtext {
  font-family: var(--font-sans);
  color: var(--muted);
}
.site-act--warn .site-act__dot {
  box-shadow: inset 0 0 0 1px var(--warn);
}
.site-act--warn .site-act__argtext {
  color: var(--warn-ink);
}
/* 4em = 四个汉字，绝大多数动词正好这么宽，参数因此对齐成一列。更长的那几个
   （平台动作）自己把这一行的参数推开，而它们本来就该显眼。 */
.site-act__verb {
  flex: 0 0 auto;
  min-width: 4em;
  font-family: var(--font-sans);
  font-size: 12px;
  color: var(--muted);
  white-space: nowrap;
}
.site-act--platform .site-act__verb {
  color: var(--ink);
  font-weight: 600;
}
/* 这一步只有动词、没有参数：平台提示和后端报错都是这样 —— 内容本来就是一句话，
   落库时没有「动词\n参数」那道换行（见 lib/siteLog.ts 的 eventArg）。定宽不折行
   的动词列装不下整句，而按内容宽量出来的尺寸比这一行还长，于是它先整段掉到圆点
   下面另起一行（圆点孤零零占一行），再横着冲出面板。这时动词就是这一行的正文：
   从 0 起算占满剩下的宽度，折到三行，整句挂在 title 上。 */
.site-act--solo .site-act__verb {
  display: -webkit-box;
  flex: 1 1 0;
  min-width: 0;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 3;
  line-clamp: 3;
  overflow: hidden;
  overflow-wrap: anywhere;
  white-space: normal;
}
/* 折起来的动词是个 `overflow: hidden` 的盒子，它的基线算在底边而不是第一行，
   圆点和时间会跟着掉到三行的底下去。这里把它们钉回第一行顶上：时间是同字号的
   一行字，顶对顶就和动词第一行对齐；圆点不跟基线走了，基类那 -3px 的上移也要
   撤掉，改按行高居中：(18.6 - 5) / 2 ≈ 7，18.6 = 12px 的字 × 1.55 的行高。 */
.site-act--solo .site-act__dot,
.site-act--solo .site-act__time {
  align-self: flex-start;
}
.site-act--solo .site-act__dot {
  margin-top: 7px;
  transform: none;
}
/* 同一行里那个空的参数位不再和动词分宽度。 */
.site-act--solo .site-act__argtext {
  display: none;
}
/* 截断而不是折行：一条几百字符的命令折下来能占掉半屏，而这一列的用处是扫。
   点开这一行换成参数原文，整条摊开，不再截第二次。
   是个 button 而不是带 click 的 span：摊开是一个真的操作，键盘要够得着它。 */
/* 起始宽度是 0，不是内容宽：这一行是可折行的 flex，按内容宽起算的话，一条长参数
   量出来比剩下的空间宽，就整段掉到动词下面一行去，而不是在这一行里截断。 */
.site-act__argtext {
  flex: 1 1 0;
  min-width: 0;
  padding: 0;
  border: 0;
  background: none;
  font: inherit;
  text-align: left;
  color: var(--text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  cursor: pointer;
}
.site-act__argtext:focus-visible {
  outline: 1px solid var(--accent);
  outline-offset: 2px;
  border-radius: var(--radius-sm);
}
/* 摊开时参数换到动词下面另起一行，动词和时间留在第一行——整行被参数原文顶掉的
   话，读的人就看不见「这一步是什么、什么时候做的」了。缩进和错误行同一个算法：
   圆点 + 间隙 + 动词列 + 间隙。order 把它排到最后，time 用 margin-left 顶到最右。 */
.site-act__argtext--full {
  flex: 0 0 100%;
  order: 1;
  padding-left: calc(5px + 8px + 4em + 8px);
  white-space: pre-wrap;
  word-break: break-word;
}
.site-act__argtext--full ~ .site-act__time {
  margin-left: auto;
  /* 摊开时时间不再等悬停：参数原文拉到下面去了，这一行只剩动词和它，是「什么时候
     做的」唯一的落点。 */
  opacity: 1;
}
.site-act--platform .site-act__argtext {
  color: var(--ink);
}
.site-act__argtext--prose {
  font-family: var(--font-sans);
}
/* 时间悬停才出现：二十个同样的 20:28 占着最右边的强位置，却不说明任何事，这一
   轮的时间写在组头上。位置照留，不然一行会在鼠标划过时改变宽度。 */
.site-act__time {
  flex: 0 0 auto;
  color: var(--faint);
  font-size: 12px;
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
  opacity: 0;
  transition: opacity var(--dur-quick) var(--ease-standard);
}
</style>
