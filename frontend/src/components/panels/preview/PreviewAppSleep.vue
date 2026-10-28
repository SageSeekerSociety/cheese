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

import { computed } from 'vue'

import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'

const props = defineProps<{
  /** 睡的是哪一层：整台电脑（通道断了），还是电脑在、应用没答。 */
  kind: 'machine' | 'app'
  /** 最近一次成功加载时留的快照；从没留过就是 null。 */
  snapshotHtml: string | null
  /** 快照是多久以前留的，已经说成一句话（「4 分钟前」）。 */
  snapshotAge: string
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
  if (!props.snapshotHtml) return t('work.room.preview.sleep.noSnapshot')
  return t('work.room.preview.sleep.snapshotBody', { age: props.snapshotAge })
})
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
      <div class="app-sleep__body">{{ body }}</div>
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
      <template v-else>
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
  /* 和原型一样靠上：卡的上沿在页面往下约 100px 处（页面矮时按 18% 收），不是垂直居中——
   * 居中会盖住页面正中的那几行。 */
  top: min(120px, 18%);
  left: 50%;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  width: min(340px, calc(100% - 32px));
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
