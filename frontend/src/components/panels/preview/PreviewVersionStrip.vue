<script setup lang="ts">
// 文档条下面那一条：这一版和读者上次看的那一版比改了几处、正在比哪两版、正在看哪一版旧的。
//
// 三种只会出现一种，按读者手上在做的那件事排：在看旧版 > 在比两版 > 新版提示。三种都在
// 正文上方同一个位置，读者不用找「我现在看的是什么」。
//
//   - 提示（琥珀底）：「本版共修改 N 处（对比第 M 版）· 查看改动 · 收起」。产品配色里没有蓝
//     （提示用的 info 色是中性灰），琥珀底是这一套里「新的、值得看一眼」的那个颜色。
//   - 对比（绿底）：「版本对比：第 A 版 → 第 B 版」，加上图例、上一处 / 下一处、退出对比。
//   - 旧版（灰底）：「正在看第 N 版，不是当前版」，可以拿它和当前版比，或回到当前版。
import type { VersionCompare } from '@/composables/panelPreview/versions'

import { computed } from 'vue'

import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'

const props = defineProps<{
  hint?: { base: number; count: number; unit: 'place' | 'cell' } | null
  compare?: VersionCompare | null
  /** 正在看的旧版是第几版；没在看就是 null。 */
  oldSeq?: number | null
  oldError?: string
  canCompare?: boolean
  /** 对比态里现在停在第几处（0 起）。 */
  changeIndex?: number
  compact?: boolean
}>()

const emit = defineEmits<{
  'view-changes': []
  dismiss: []
  'exit-compare': []
  'compare-current': []
  back: []
  prev: []
  next: []
}>()

const mode = computed(() => {
  if (props.oldSeq != null) return 'old'
  if (props.compare) return 'compare'
  if (props.hint) return 'hint'
  return null
})

/** 对比里一共几处可以跳（表格的改动画在格子上，不跳）。 */
const total = computed(() => {
  const changes = props.compare?.changes
  return changes?.kind === 'text' ? changes.count : 0
})

/** 对比态那一句状态：在比、比不了、比出来没区别，或者表格里新加 / 删掉了哪些表。 */
const compareNote = computed(() => {
  const compare = props.compare
  if (!compare) return ''
  if (compare.loading) return t('work.room.versions.comparing')
  if (compare.error) return t('work.room.versions.compareFailedWith', { error: compare.error })
  const changes = compare.changes
  if (changes && !changes.count) return t('work.room.versions.noChanges')
  if (changes?.kind === 'sheet') {
    const parts: string[] = []
    if (changes.diff.addedSheets.length)
      parts.push(
        t('work.room.versions.sheetsAdded', { names: changes.diff.addedSheets.join(t('work.room.versions.listJoin')) })
      )
    if (changes.diff.removedSheets.length)
      parts.push(
        t('work.room.versions.sheetsRemoved', {
          names: changes.diff.removedSheets.join(t('work.room.versions.listJoin')),
        })
      )
    return parts.join(' · ')
  }
  return ''
})

const legend = computed(() =>
  props.compare?.changes?.kind === 'sheet'
    ? t('work.room.versions.compareLegendCells')
    : t('work.room.versions.compareLegend')
)
</script>

<template>
  <div v-if="mode === 'old'" class="vs vs--old" data-testid="version-strip" data-mode="old">
    <v-icon size="16" class="vs__icon">mdi-history</v-icon>
    <span class="vs__text">{{ t('work.room.versions.oldBar', { seq: oldSeq }) }}</span>
    <span v-if="oldError" class="vs__note">{{ oldError }}</span>
    <span class="vs__fill" />
    <button v-if="canCompare" type="button" class="vs__link" @click="emit('compare-current')">
      {{ t('work.room.versions.compareWithCurrent') }}
    </button>
    <button type="button" class="vs__link" data-testid="version-back" @click="emit('back')">
      {{ t('work.room.versions.backToCurrent') }}
    </button>
  </div>

  <div v-else-if="mode === 'compare' && compare" class="vs vs--cmp" data-testid="version-strip" data-mode="compare">
    <v-icon size="16" class="vs__icon">mdi-compare-horizontal</v-icon>
    <span class="vs__text">
      {{ t('work.room.versions.compareBar', { base: compare.base, target: compare.target }) }}
      <template v-if="!compact && !compareNote">· {{ legend }}</template>
    </span>
    <span v-if="compareNote" class="vs__note">{{ compareNote }}</span>
    <span class="vs__fill" />
    <template v-if="total > 0">
      <BaseButton
        kind="ghost"
        size="sm"
        icon="mdi-chevron-up"
        density="compact"
        :title="t('work.room.versions.prevChange')"
        :aria-label="t('work.room.versions.prevChange')"
        :disabled="(changeIndex ?? 0) <= 0"
        data-testid="version-prev"
        @click="emit('prev')"
      />
      <span class="vs__count">{{ t('work.room.versions.changeOf', { index: (changeIndex ?? 0) + 1, total }) }}</span>
      <BaseButton
        kind="ghost"
        size="sm"
        icon="mdi-chevron-down"
        density="compact"
        :title="t('work.room.versions.nextChange')"
        :aria-label="t('work.room.versions.nextChange')"
        :disabled="(changeIndex ?? 0) >= total - 1"
        data-testid="version-next"
        @click="emit('next')"
      />
    </template>
    <button type="button" class="vs__link" data-testid="version-exit" @click="emit('exit-compare')">
      {{ t('work.room.versions.exitCompare') }}
    </button>
  </div>

  <div v-else-if="mode === 'hint' && hint" class="vs vs--hint" data-testid="version-strip" data-mode="hint">
    <v-icon size="16" class="vs__icon">mdi-creation</v-icon>
    <span class="vs__text">{{
      t(hint.unit === 'cell' ? 'work.room.versions.hintCells' : 'work.room.versions.hint', {
        count: hint.count,
        base: hint.base,
      })
    }}</span>
    <span class="vs__fill" />
    <button type="button" class="vs__link" data-testid="version-view-changes" @click="emit('view-changes')">
      {{ t('work.room.versions.viewChanges') }}
    </button>
    <button type="button" class="vs__link" data-testid="version-dismiss" @click="emit('dismiss')">
      {{ t('work.room.versions.collapse') }}
    </button>
  </div>
</template>

<style scoped>
.vs {
  flex: none;
  display: flex;
  align-items: center;
  gap: 6px;
  min-height: 34px;
  padding: 4px 8px 4px 12px;
  border-bottom: 1px solid var(--line);
  font-size: 13px;
  line-height: var(--lh-13);
}
.vs--hint {
  background: var(--accent-wash);
  color: var(--accent-ink);
}
.vs--cmp {
  background: var(--ok-wash);
  color: var(--ok-ink);
}
.vs--old {
  background: var(--fill);
  color: var(--text);
}
.vs__icon {
  flex: none;
  color: inherit;
}
/* 说明那一句可以省略号，两头的链接不让位：窄的时候也得点得到「退出对比」。 */
.vs__text {
  flex: 0 1 auto;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.vs__note {
  flex: 0 1 auto;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  opacity: 0.8;
}
.vs__fill {
  flex: 1 1 0;
}
.vs__count {
  flex: none;
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}
.vs__link {
  flex: none;
  padding: 2px 4px;
  border-radius: var(--radius-sm);
  color: inherit;
  font-size: 13px;
  font-weight: 500;
  text-decoration: underline;
  text-underline-offset: 2px;
  white-space: nowrap;
}
.vs__link:hover {
  background: color-mix(in srgb, currentcolor 10%, transparent);
}
</style>
