<script setup lang="ts">
// 修改记录右边那一栏：选中的一版和它上一版比，改了哪几段。
//
// 比的是正文原文（一段一行），不是渲染出来的样子：加粗、链接这种标记的改动也是改动，
// 渲染之后反而看不出来。加的段绿底，删的段红底带删除线，改了的段在行内标出增删的字。
import type { VersionDiff } from '../../../lib/versionDiff'

import { t } from '@/i18n'

defineProps<{ diff: VersionDiff }>()

/** 空的一段也要占一行高，看得出那里有一个空段被加了或删了。 */
const shown = (text: string) => text || '\u00a0'
</script>

<template>
  <div class="vdiff">
    <p class="vdiff__summary t-meta">
      {{ t('work.room.doc.diffSummary', { added: diff.added, removed: diff.removed, changed: diff.changed }) }}
    </p>
    <p v-if="!diff.rows.length" class="vdiff__none t-body">{{ t('work.room.doc.diffNone') }}</p>
    <div class="vdiff__rows">
      <template v-for="(row, i) in diff.rows" :key="i">
        <p v-if="row.kind === 'skip'" class="vdiff__skip">{{ t('work.room.doc.diffSkipped', { count: row.count }) }}</p>
        <p v-else-if="row.kind === 'same'" class="vdiff__row">{{ shown(row.text) }}</p>
        <p v-else-if="row.kind === 'add'" class="vdiff__row vdiff__row--add">
          <span class="visually-hidden">{{ t('work.room.doc.diffAdded') }}</span
          >{{ shown(row.text) }}
        </p>
        <p v-else-if="row.kind === 'del'" class="vdiff__row vdiff__row--del">
          <span class="visually-hidden">{{ t('work.room.doc.diffRemoved') }}</span
          >{{ shown(row.text) }}
        </p>
        <p v-else class="vdiff__row vdiff__row--changed">
          <template v-for="(seg, j) in row.segments" :key="j">
            <ins v-if="seg.kind === 'add'" class="vdiff__ins">{{ seg.text }}</ins>
            <del v-else-if="seg.kind === 'del'" class="vdiff__del">{{ seg.text }}</del>
            <span v-else>{{ seg.text }}</span>
          </template>
        </p>
      </template>
    </div>
  </div>
</template>

<style scoped>
.vdiff__summary {
  margin: 0 0 12px;
  color: var(--muted);
}
.vdiff__none {
  margin: 0;
  color: var(--muted);
}
.vdiff__rows {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.vdiff__row,
.vdiff__skip {
  margin: 0;
  padding: 2px 8px;
  border-radius: var(--radius-sm);
  font-size: 14px;
  line-height: var(--lh-14);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.vdiff__row {
  color: var(--text);
}
.vdiff__row--add {
  background: var(--ok-wash);
}
.vdiff__row--del {
  background: var(--danger-wash);
  color: var(--muted);
  text-decoration: line-through;
}
.vdiff__ins {
  background: var(--ok-wash);
  text-decoration: none;
}
.vdiff__del {
  background: var(--danger-wash);
  color: var(--muted);
}
.vdiff__skip {
  color: var(--faint);
  font-size: 12px;
  line-height: var(--lh-12);
}
</style>
