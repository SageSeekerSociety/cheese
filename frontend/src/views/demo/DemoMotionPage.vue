<script setup lang="ts">
// 动效台：产品里每一种变化各摆一格，标明它用的 token。
//
// 这一页存在的理由是「改 token 之前先在这里看一眼会变成什么样」：产品里的动效是
// 100–320ms，正常速度下看不清它是怎么变的，所以上面那一档能慢放 4 倍、每一格能单独
// 重放。慢放走的是 `demoMotion.ts` 那一条——把产品自己的 `--dur-*` 乘 4，不是在这里
// 另写一套时长；这一页（和整站）的时长都从那几个变量来。
//
// 「全部重放」和每一格的「重放」都只是换一个 key，把那一格重新挂一遍：动效从初始态
// 再走一次。不做「播放/暂停」那种播放器，因为它会给人一个产品里并不存在的状态。
import type { MotionMode } from './demoMotion'

import { computed, ref } from 'vue'

import { useStudio } from './demoCatalogStudio'
import { MOTION_FACTOR, MOTION_MODES, prefersReducedMotion } from './demoMotion'
import { documentTokenScopes, numericValue, previewOf } from './demoTokens'
import { TRANSITIONS } from './demoTransitions'
import MotionSample from './MotionSample.vue'

const studio = useStudio()

/** 时长 token 的原值：页面上那句「乘了几倍、变成了多少」要说真的。 */
const scopes = documentTokenScopes()
const base = computed(() => previewOf('--dur-base', scopes))
const baseMs = computed(() => (base.value ? numericValue(base.value.light) : null))
const slowMs = computed(() => (baseMs.value === null ? null : baseMs.value * MOTION_FACTOR.slow))

/** 说的是不是真话：这个环境里读不到样式表就只说倍数，不编一个毫秒数。 */
const factorNote = computed(() => {
  if (studio.motion !== 'slow') return ''
  // 倍数从档位里取，不写死：`MOTION_FACTOR.slow` 改了，这句话跟着改。
  const times = `${MOTION_FACTOR.slow} 倍`
  if (baseMs.value === null || slowMs.value === null) return `动效已放慢 ${times}。`
  return `动效已放慢 ${times}：--dur-base 从 ${baseMs.value}ms 变成 ${slowMs.value}ms。`
})

/** 系统开着「减少动态效果」时，上面的开关默认就落在「关」——把这件事说出来。 */
const reduced = prefersReducedMotion()

/**
 * 这个环境下这一档还起不起作用。
 *
 * 慢放把产品的时长 token 乘几倍写回去，可产品自己在「减少动态效果」时有一条全局规则
 * （`style.css` 的 `@media (prefers-reduced-motion: reduce)`）用 `!important` 把每一段
 * 过渡压成了 0.001ms。那条规则赢，乘几倍都落不到屏幕上——档位摆在那里只会骗人，所以
 * 标成不可用，并在下面那句里说清为什么。
 */
function unavailable(id: MotionMode): boolean {
  return reduced && id === 'slow'
}

/** 时间线上一共几条。产品里有几条就该有几条：清单在 demoTransitions.ts。 */
const count = computed(() => TRANSITIONS.length)

/**
 * 每一格各记各的重放次数。`all` 那一下是给所有格各加一次——用同一个数当「第几次全局
 * 重放」，和每一格自己的次数加在一起，两边都不会互相吃掉。
 */
const all = ref(0)
const mine = ref<Record<string, number>>({})

function replay(id: string): void {
  mine.value = { ...mine.value, [id]: (mine.value[id] ?? 0) + 1 }
}

function replayAll(): void {
  all.value += 1
}

function replaysOf(id: string): number {
  return all.value + (mine.value[id] ?? 0)
}
</script>

<template>
  <div class="motion">
    <h1 class="motion-name t-page-title">动效台</h1>
    <p class="motion-lede">
      产品里每一次变化各摆一格，标明它用的 token。产品里的动效是 100–320ms，正常速度下看不清「它是怎么变的」——
      慢放把产品自己的时长 token 乘 4，不另写一套时长；改 token 之前先在这里看一眼会变成什么样。
    </p>

    <div class="motion-bar">
      <div class="motion-group" role="group" aria-label="动效档">
        <span class="motion-label">动效档</span>
        <button
          v-for="mode in MOTION_MODES"
          :key="mode.id"
          type="button"
          class="motion-btn"
          :aria-pressed="studio.motion === mode.id"
          :disabled="unavailable(mode.id)"
          :aria-describedby="unavailable(mode.id) ? 'motion-reduced-note' : undefined"
          @click="studio.motion = mode.id"
        >
          {{ mode.label }}
        </button>
      </div>
      <button type="button" class="motion-btn motion-all" @click="replayAll">全部重放</button>
      <p v-if="factorNote" class="motion-note" role="status">{{ factorNote }}</p>
    </div>

    <p v-if="reduced" id="motion-reduced-note" class="motion-reduced">
      系统开着「减少动态效果」，所以这一档默认落在「关」：过渡还在，只是不花时间，终态和正常档一模一样。
      慢放档在这个环境下不起作用——产品的全局规则把每一段过渡压成了 0.001ms，乘几倍都落不到屏幕上——所以标成不可用。
    </p>

    <p class="motion-count">{{ count }} 条时间线，全部引产品自己的时长与曲线 token。</p>

    <ul class="motion-grid">
      <li v-for="item in TRANSITIONS" :key="item.id" class="motion-cell">
        <!-- Replay replaces the whole cell (its key carries the count): a new key remounts
             it, so the transition runs from its initial state again. Not a "play" signal —
             the product has no such state, so do not invent one. -->
        <MotionSample :id="item.id" :key="`${item.id}-${replaysOf(item.id)}`" />

        <div class="motion-info">
          <p class="motion-cell-name">{{ item.name }}</p>
          <p class="motion-cell-about">{{ item.about }}</p>
          <p class="motion-cell-tokens">
            <code v-for="token in item.tokens" :key="token" class="motion-token">{{ token }}</code>
          </p>
          <button type="button" class="motion-btn motion-replay" @click="replay(item.id)">重放</button>
        </div>
      </li>
    </ul>
  </div>
</template>

<style scoped>
.motion {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 16px 24px 48px;
}

.motion-name {
  margin: 0;
}

.motion-lede {
  max-width: 74ch;
  margin: 0;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}

.motion-bar {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 16px;
  align-items: center;
}

.motion-group {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  align-items: center;
}

.motion-label {
  font-size: 12px;
  color: var(--faint);
}

.motion-btn {
  padding: 3px 10px;
  font: inherit;
  font-size: 12px;
  color: var(--muted);
  cursor: pointer;
  background: var(--surface);
  border: 1px solid var(--line-2);
  border-radius: var(--radius-pill);
}

/* 不可用的那一档不给「可以点」的样子：悬停不变色。 */
.motion-btn:not(:disabled):hover {
  color: var(--ink);
  border-color: var(--accent);
}

/* 这个环境下不起作用的那一档：看得见、点不动，理由写在下面那句里。 */
.motion-btn:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}

.motion-btn[aria-pressed='true'] {
  color: var(--accent-ink);
  background: var(--accent-wash);
  border-color: var(--accent);
}

.motion-all {
  margin-left: auto;
}

.motion-note {
  flex: 1 1 100%;
  margin: 0;
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--muted);
}

.motion-reduced {
  max-width: 74ch;
  margin: 0;
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--muted);
}

.motion-count {
  margin: 0;
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--faint);
}

.motion-grid {
  display: grid;
  gap: 14px;

  /* 窄屏（360 宽的手机减去左右留白）放不下 320px 一列：最窄退到整行宽。 */
  grid-template-columns: repeat(auto-fill, minmax(min(320px, 100%), 1fr));
  padding: 0;
  margin: 0;
  list-style: none;
}

.motion-cell {
  display: flex;
  overflow: hidden;
  background: var(--surface);
  border: 1px solid var(--line-2);
  border-radius: var(--radius-lg);
  flex-direction: column;
}

.motion-info {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 10px 12px 12px;
  border-top: 1px solid var(--line);
}

.motion-cell-name {
  margin: 0;
  font-size: 13px;
  font-weight: 600;
  color: var(--ink);
}

.motion-cell-about {
  margin: 0;
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--muted);
}

.motion-cell-tokens {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 8px;
  margin: 2px 0 0;
}

.motion-token {
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--faint);
}

.motion-replay {
  align-self: flex-start;
  margin-top: 4px;
}

@media (width <= 900px) {
  .motion {
    padding: 16px 16px 40px;
  }

  /* 窄屏上「全部重放」不再顶到最右：它和动效档本来就是一排里的两件事。 */
  .motion-all {
    margin-left: 0;
  }
}
</style>
