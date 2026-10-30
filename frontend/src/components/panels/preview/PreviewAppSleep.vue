<script setup lang="ts">
// 应用睡着了时盖在网页那一格上的一层：最近一次成功加载的静态快照（压暗），中间一张说明
// 卡——「运行环境已休眠」和「唤醒并重启应用」；点了之后卡变成分步进度，一步步打勾。
//
// 快照放进一个不跑脚本、不同源、不出网的框里：`sandbox=""` 挡脚本，不出网靠存下来的
// 那份字节里已经被平台盖上的一条 `default-src 'none'` CSP（`seal_snapshot`，入库那一步
// 盖的——写快照的人自己带不带都不算数）。它是一份冻住的 DOM，只求看上去一样，零沙箱开
// 销。压暗那一层不收鼠标，所以宿主盖在上面的圈选遮罩照样能在快照上框一块发批注；圈选开
// 着时说明卡让开，免得挡住要框的那一处。
//
// `no-referrer` 是第二道（2026-10-27 实测 Chromium 156 下这道用不上：`srcdoc` 的框发起
// 的请求不带 Referer）。留着的理由是它只花一个属性：快照里只要有谁点得动一下（`sandbox=""`
// 挡不住框自己发起导航，点得到链结就会走），那一跳到外站时面板地址不该跟着过去。
import type { AppWakeStep } from '@/composables/panelPreview/appWake'

import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'

const props = defineProps<{
  /** 睡的是哪一层：整台电脑（通道断了），还是电脑在、应用没答。 */
  kind: 'machine' | 'app'
  /** 最近一次成功加载时留的快照；从没留过就是 null。 */
  snapshotHtml: string | null
  /** 快照是多久以前留的，已经说成一句话（「4 分钟前」）。 */
  snapshotAge: string
  /** 这一件拍过、但那一页太大留不下（超过快照上限）。和「从没留过」不是一回事：
   *  后者听着像没人打开过，而这里的真相是页面太大、存不下来。 */
  tooLarge?: boolean
  /** 快照停在哪一页（标题或路径）。一份应用只留一张快照，几个读者同开时后写的盖掉先写
   *  的，所以得说清是哪一页，否则另一个读者会以为自己那一页被冻住了。空串就不说。 */
  snapshotWhere?: string
  /** 正在唤醒时的分步进度；没在唤醒是 null。 */
  steps: AppWakeStep[] | null
  /** 唤醒没成时那一句。 */
  wakeError: string
  /** 底下什么都没有（没快照、屏幕上也没留旧页面）：垫一层浅底。 */
  bare: boolean
  /** 圈选开着：卡让开。 */
  picking: boolean
  agentName: string
}>()

const emit = defineEmits<{ wake: [] }>()

const title = computed(
  () =>
    t(props.kind === 'machine' ? 'work.room.preview.sleep.machineTitle' : 'work.room.preview.sleep.appTitle') +
    (props.snapshotHtml ? t('work.room.preview.sleep.snapshotTag') : '')
)
const body = computed(() => {
  if (props.snapshotHtml) return t('work.room.preview.sleep.snapshotBody', { age: props.snapshotAge })
  // 拍过、但太大留不下：说清是哪一种，别让它读成「还没人打开过」。
  if (props.tooLarge) return t('work.room.preview.sleep.tooLarge')
  return t('work.room.preview.sleep.noSnapshot')
})
/** 停在那一页：有就单独一行放在正文下面（一串路径混在正文里读不出来）。 */
const where = computed(() => (props.snapshotHtml ? props.snapshotWhere ?? '' : ''))

/** 说明那一块是不是放不下、正在滚。CSS 判不出「内容比框高」，所以量一次；量完加一道
 *  底部淡出，让人看得出下面还有。画面一变（换步骤、换窗口高度）就重量。 */
const scroll = ref<HTMLElement | null>(null)
const overflowing = ref(false)
function measureOverflow() {
  const el = scroll.value
  overflowing.value = !!el && el.scrollHeight > el.clientHeight + 1
}
onMounted(() => {
  measureOverflow()
  window.addEventListener('resize', measureOverflow)
})
onBeforeUnmount(() => window.removeEventListener('resize', measureOverflow))
watch([() => props.steps, where, () => props.snapshotHtml], () => void nextTick(measureOverflow))
</script>

<template>
  <div class="app-sleep" :class="{ 'app-sleep--bare': bare }" data-testid="preview-app-sleep" :data-kind="kind">
    <iframe
      v-if="snapshotHtml"
      class="app-sleep__snapshot"
      data-testid="preview-snapshot"
      sandbox=""
      referrerpolicy="no-referrer"
      tabindex="-1"
      :title="t('work.room.preview.sleep.snapshotTitle')"
      :srcdoc="snapshotHtml"
    />
    <div v-show="!picking" class="app-sleep__card" role="status" aria-live="polite">
      <v-icon size="30" class="app-sleep__icon" aria-hidden="true">mdi-sleep</v-icon>
      <b class="app-sleep__title">{{ title }}</b>
      <!-- 卡里能滚的是这一段说明和进度；标题和底下那颗按钮不跟着滚。画面矮的时候
           （半屏右栏、笔记本拉高对话栏、手机横屏）说明会把按钮挤到看不见的地方——
           卡叫人唤醒而按钮按不到就没意义了。 -->
      <div ref="scroll" class="app-sleep__scroll" :class="{ 'app-sleep__scroll--overflowing': overflowing }">
        <div class="app-sleep__body">{{ body }}</div>
        <div v-if="where" class="app-sleep__where">{{ t('work.room.preview.sleep.snapshotWhere', { where }) }}</div>
        <!-- 四步进度也在能滚的这一块里：它和底下那颗按钮是二选一（唤醒中就没有按钮），
             几行说明加四步在矮画面里会被挤掉，让它跟着滚比截断强。 -->
        <div v-if="steps" class="app-sleep__steps" data-testid="preview-wake-steps">
          <div
            v-for="step in steps"
            :key="step.key"
            class="app-sleep__step"
            :class="`app-sleep__step--${step.state}`"
            :data-state="step.state"
          >
            <v-icon size="16" aria-hidden="true" :class="{ 'app-sleep__spin': step.state === 'now' }">{{
              step.state === 'done' ? 'mdi-check-circle' : step.state === 'now' ? 'mdi-loading' : 'mdi-circle-outline'
            }}</v-icon>
            <span>{{ t(step.label, { agent: agentName }) }}</span>
          </div>
        </div>
      </div>
      <template v-if="!steps">
        <div v-if="wakeError" class="app-sleep__error" role="alert">{{ wakeError }}</div>
        <BaseButton kind="primary" prepend-icon="mdi-power" data-testid="preview-wake" @click="emit('wake')">
          {{
            t(kind === 'machine' ? 'work.room.preview.sleep.wake' : 'work.room.preview.sleep.restart', {
              agent: agentName,
            })
          }}
        </BaseButton>
        <div class="app-sleep__hint">{{ t('work.room.preview.sleep.hint') }}</div>
      </template>
    </div>
  </div>
</template>

<style scoped>
.app-sleep {
  position: absolute;
  inset: 0;
  z-index: 1;
  overflow: hidden;
  /* 盖层自己不收鼠标：底下留着的旧页面已经压暗点不动，宿主的圈选遮罩在更上面。 */
  pointer-events: none;
}
/* 没有快照、也没有留在屏幕上的旧页面时，卡片底下垫一层浅底，不是一片空白。 */
.app-sleep--bare {
  background: var(--fill);
}
.app-sleep__snapshot {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  border: none;
  /* stylelint-disable-next-line color-no-hex -- 快照是一份网页，按白纸画，同 .preview-frame */
  background: #fff;
  filter: grayscale(1);
  opacity: 0.55;
  pointer-events: none;
}
.app-sleep__card {
  /* 缩小的档（平板/手机在窄面板里）里，这一层跟页面一起缩：卡得按**画出来的**尺寸摆，
     不然手机上点「唤醒」的是一颗缩到一半的按钮，上面几行字也发糊。
     做法只有一个反向缩放：外层把整层缩了 s，卡自己缩回 1/s，屏幕上就是原尺寸——宽度和
     位置照原型那几个数写，别再乘一遍（乘了会双算，卡宽变成 1/s 倍，右边被裁掉）。
     `--preview-scale` 没给（没缩）时反向缩放等于 1，和原型一模一样。 */
  --inv: calc(1 / var(--preview-scale, 1));
  position: absolute;
  /* 和原型一样靠上：卡的上沿在页面往下约 120px 处，不是垂直居中——居中会盖住页面正中
   * 的那几行。画面矮的时候这个留白先让出去（按高度的 6% 收），把高度留给卡：留白比说明
   * 文字更该让位，不然矮画面里第一行说明就被压在标题下面。 */
  top: min(120px, 6%);
  left: 50%;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  width: min(340px, calc(100% - 32px));
  /* 画面矮的时候（半屏的右栏、笔记本上拉高的对话栏、手机横屏）卡会比看得见的那块还高。
     卡自己按可用高度封顶，但**滚的是中间那层说明**（见 `.app-sleep__scroll`）：标题和
     那颗「唤醒」按钮都留在外面，再挤也够得着。卡本身不滚，否则按钮会跟着说明一起滚走。 */
  max-height: calc(100% - min(120px, 6%) - 8px);
  padding: 18px;
  text-align: center;
  color: var(--text);
  background: var(--raised);
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-2);
  transform: translateX(-50%) scale(var(--inv));
  transform-origin: top center;
  pointer-events: auto;
}
.app-sleep__icon {
  color: var(--warn);
}
.app-sleep__title {
  font-size: 15px;
}
.app-sleep__scroll {
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-height: 0;
  overflow-y: auto;
}
/* 放不下、正在滚的时候，底部淡出一小道：读起来像「下面还有」，而不是字被切了。用 `mask`
   走颜色本身，不铺一层假白——卡底下的快照是压暗的，白色会露馅。是不是放不下由
   `overflowing` 量出来（CSS 自己判不出来），放得下就一点痕迹都不留。
   mask 里那一段要的是**不透明**，不是某个具体颜色：照仓库其他几处写 `var(--ink)`
   （见 `landing.css`、`FileBytesPreview.vue`），不写字面色，写死一个值就跟不上深色主题。 */
.app-sleep__scroll--overflowing {
  mask-image: linear-gradient(to bottom, var(--ink) calc(100% - 14px), transparent);
}
.app-sleep__where {
  max-width: 100%;
  font: 12px/1.5 var(--font-mono);
  color: var(--faint);
  overflow-wrap: anywhere;
}
.app-sleep__body,
.app-sleep__hint {
  font-size: 14px;
  line-height: var(--lh-14);
  color: var(--muted);
}
.app-sleep__hint {
  font-size: 12px;
  line-height: var(--lh-12);
}
.app-sleep__error {
  font-size: 13px;
  color: var(--danger-ink);
}
.app-sleep__steps {
  display: flex;
  flex-direction: column;
  align-self: stretch;
  gap: 4px;
  text-align: left;
  font-size: 13px;
}
.app-sleep__step {
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--faint);
}
.app-sleep__step--done {
  color: var(--ok-ink);
}
.app-sleep__step--now {
  font-weight: 600;
  color: var(--ink);
}
/* 正在做的那一步转起来。不用字体自带的那个旋转类：它不是图标，写成类名会被图标
 * 子集的守卫当成一个字体里没有的图标，直接判红。 */
.app-sleep__spin {
  animation: app-sleep-spin 1.6s linear infinite;
}
@keyframes app-sleep-spin {
  from {
    transform: rotate(0deg);
  }
  to {
    transform: rotate(360deg);
  }
}
/* 关掉动效时不能留着转：全局那条把时长压到 0.001ms，转圈会变成高频闪烁。停掉之后
 * 这一行照样在说「正在做」——图标是 mdi-loading，整行是加粗的。 */
@media (prefers-reduced-motion: reduce) {
  .app-sleep__spin {
    animation: none;
  }
}
</style>
