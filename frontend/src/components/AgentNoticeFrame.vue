<script setup lang="ts">
// 时间线上「谁做了一件事」那一行的外框（改了几个文件、记了一条决策、检查没过、
// 工作电脑就绪）。
//
// 记号回答「这是谁的事」。某个 AI 队友的事（它那一轮的正在处理、失败、重试、平台
// 替它处理好的那几行）就以它的身份出现，和它发的消息一模一样：同样大的头像，名字
// 后面跟时间，读起来是它自己说的。平台自己的事（不属于任何一位队友的那一轮）是头像
// 列里一个小一号的中性记号。
//
// 事件本身装在一个圆角底色块里，和消息下面的支线卡同一种形状：块的左沿在消息正文那
// 条竖线上，字在块里留出内边距。平台的那一行没有名字可跟，时间就在块里的最右端，和
// 状态字（「等人处理」）在同一行。轻重只改块的底色（`tone`：warn / danger，不给是
// 中性的灰），不改形状。
import type { FaceState } from '../lib/agentFace'

import { computed } from 'vue'

import CheeseAvatar from './CheeseAvatar.vue'

import { t } from '@/i18n'

const props = defineProps<{
  name: string | null
  handle?: string | null
  time: string
  cont?: boolean
  /** 这一行的头像是这位队友最近出现的那个，它正在干活（或刚干完）：头像的表情。 */
  face?: FaceState | null
  /** 头像在动时，悬停看到的那一句（现场顶上那一行）。有它，头像点下去去现场。 */
  faceLabel?: string | null
  /** 头像在动时，读屏读到的那一句：只有状态，不带在走的秒数，免得每秒再念一遍。 */
  faceStatus?: string | null
  /** 不署名那一行头像列里的记号：默认是平台的；一条改文档的记录换成文档的。 */
  mark?: string
  /** 这件事有多重：块的底色。不给是中性的。 */
  tone?: 'warn' | 'danger' | null
}>()

const liveLabel = computed(() =>
  props.faceLabel ? t('work.agentAvatar.live', { name: props.name, status: props.faceLabel }) : null
)
const liveName = computed(() =>
  props.faceStatus ? t('work.agentAvatar.live', { name: props.name, status: props.faceStatus }) : undefined
)
</script>

<template>
  <div v-if="name" class="notice-row notice-row--agent agent-status" :class="{ 'notice-row--cont': cont }">
    <!-- 续行和消息的续话一样：头像列空着，时间在悬停时出现在那里。 -->
    <span v-if="cont" class="notice-row__gutter">
      <span class="notice-row__gutter-time">{{ time }}</span>
    </span>
    <!-- 在动的头像和消息里的一样：点下去去现场（ChatPanel 认 data-site）。 -->
    <button
      v-else-if="liveLabel"
      type="button"
      class="notice-row__avatar notice-row__live im-person"
      data-site=""
      :aria-label="liveName"
    >
      <!-- 这一句带着每秒在走的秒数：不用原生 title（一换字就收起再弹，悬停时每秒
           闪一下），用原地换字的气泡。 -->
      <v-tooltip activator="parent" location="top" :text="liveLabel" />
      <CheeseAvatar :size="28" :name="name" :handle="handle" :state="face ?? null" />
    </button>
    <span v-else class="notice-row__avatar" :title="name" :aria-label="name" role="img">
      <CheeseAvatar :size="28" :name="name" :handle="handle" :state="face ?? null" />
    </span>
    <div class="notice-row__body">
      <div v-if="!cont" class="notice-row__meta">
        <span class="notice-row__name" data-user-content>{{ name }}</span>
        <span class="notice-row__meta-time">{{ time }}</span>
      </div>
      <div class="notice-row__box" :class="tone && `notice-row__box--${tone}`">
        <slot />
      </div>
    </div>
  </div>
  <div v-else class="notice-row">
    <span class="notice-row__mark" :title="mark ? undefined : t('work.room.notice.platform')">
      <span class="notice-row__platform" aria-hidden="true">
        <v-icon size="12">{{ mark ?? 'mdi-server' }}</v-icon>
      </span>
    </span>
    <div class="notice-row__body">
      <div class="notice-row__box notice-row__box--platform" :class="tone && `notice-row__box--${tone}`">
        <div class="notice-row__content">
          <slot />
        </div>
        <span class="notice-row__time">{{ time }}</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* 两种行和消息行（room-row.css 的 .im-row）同一套几何：头像列 16px 起、28px 宽，
   正文从 54px 起。平台那一行的 20px 记号在这一列里居中。 */
.notice-row {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 2px 16px;
  margin-top: 8px;
  /* 平台自己的那一行也是时间线里的行，和消息行（room-row.css 的 .im-row）一样：
     离屏的不渲染，但留在 DOM 里 —— Ctrl+F、读屏、选中都还找得到它，这是「不虚拟化」。
     `auto 28px` 是「一行事件」的估计高度，只在这一行**从未渲染过**时用来占位；`auto`
     让渲染过的行记住真实高度，于是常态滚动和向上翻页都不因估计值漂。向上翻页补偿、
     scrollIntoView 这类「必须按真实高度量」的地方，由 lib/contentVisibility 挂
     .cv-measure 临时关掉它。底色块是这一行内部的绘制，跳过这一行时整行一起跳过。 */
  content-visibility: auto;
  contain-intrinsic-size: auto 28px;
}
/* 测量帧（向上翻页补偿、scrollIntoView）：这一窗里的行按真实高度铺开。选择器带
   .cv-measure 前缀，胜过上面没带的那条。见 lib/contentVisibility。 */
.cv-measure .notice-row {
  content-visibility: visible;
}
/* 连着几件事贴在一起，和同一个人连着说的几句话一样。 */
.notice-row + .notice-row {
  margin-top: 2px;
}
.notice-row--agent {
  padding: 4px 16px;
}
.notice-row--agent.notice-row--cont {
  margin-top: 0;
}
.notice-row__gutter {
  flex: 0 0 28px;
}
.notice-row__gutter-time {
  display: block;
  margin-left: -12px;
  width: 40px;
  text-align: right;
  font-family: var(--font-mono);
  font-size: 12px;
  line-height: var(--lh-13);
  color: var(--faint);
  opacity: 0;
  transition: opacity var(--dur-quick) var(--ease-standard);
}
@media (hover: hover) {
  .notice-row:hover .notice-row__gutter-time {
    opacity: 1;
  }
}
.notice-row__avatar,
.notice-row__mark {
  display: inline-flex;
  flex: none;
}
/* 记号在 28px 的头像列里居中，和块里第一行字（块的 1px 边 + 4px 内边距之下那 20px）
   同高。 */
.notice-row__mark {
  justify-content: center;
  width: 28px;
  padding-top: 5px;
}
.notice-row__live {
  padding: 0;
  border: 0;
  background: none;
  cursor: pointer;
}
.notice-row__platform {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  border-radius: var(--radius-sm);
  background: var(--fill-2);
  color: var(--muted);
}
.notice-row__body {
  flex: 1;
  min-width: 0;
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
}
/* 事件装在这一块里：和消息下面的支线卡（ChatTimeline 的 .msg-work + ThreadLine）
   同一种形状——左沿在正文竖线上，1px 的边加 12px 内边距，圆角，中性的灰底。 */
.notice-row__box {
  min-width: 0;
  padding: 4px 12px;
  border: 1px solid transparent;
  border-radius: var(--radius-md);
  background: var(--fill);
}
.notice-row__box--warn {
  background: var(--warn-wash);
}
.notice-row__box--danger {
  background: var(--danger-wash);
}
/* 平台那一行：块里左边是事件，右端是时间，和第一行的状态字同一行。 */
.notice-row__box--platform {
  display: flex;
  align-items: flex-start;
  gap: 12px;
}
.notice-row__content {
  flex: 1;
  min-width: 0;
}
/* 名字和时间：和消息行的 .im-meta / .im-name / .im-time 同一档。 */
.notice-row__meta {
  display: flex;
  align-items: baseline;
  gap: 8px;
  margin-bottom: 2px;
}
.notice-row__name {
  font-size: 13px;
  line-height: var(--lh-13);
  font-weight: 600;
  color: var(--ink);
}
.notice-row__meta-time {
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--faint);
}
.notice-row__time {
  flex: none;
  color: var(--faint);
  font-family: var(--font-mono);
  font-size: 12px;
  line-height: var(--lh-12);
  padding-top: 1px; /* 18px 的行盒放进块里第一行的 20px 里，上下各 1px */
}
</style>
