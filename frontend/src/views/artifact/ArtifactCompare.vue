<script setup lang="ts">
// 两版之间改了什么。
//
// Word、表格、幻灯片按读者的单位比（段落、单元格、页），和「改动」里用的是同一个
// 组件。代码和别的文本给一张改动文件清单，点开哪一个看哪一个。比不出逐字差异的
// （二进制、太大）说清楚为什么，直接把两版并排摆出来——那时候能看的只有这个。
import type { ArtifactComparison, ArtifactVersion } from '@/api'
import type { OfficeComparison } from '@/types/reviewComment'

import { computed, ref, watch } from 'vue'

import ArtifactChanges from './ArtifactChanges.vue'
import { noteText } from './notes'

import { compareArtifactVersions } from '@/api'
import ArtifactVersionPreview from '@/components/ArtifactVersionPreview.vue'
import SegmentedControl from '@/components/common/SegmentedControl.vue'
import OfficeCompare from '@/components/review/OfficeCompare.vue'
import { t } from '@/i18n'

const props = defineProps<{
  projectId: string
  artifactId: string
  versions: ArtifactVersion[]
  before: string
  after: string
}>()

const emit = defineEmits<{ 'update:before': [cardId: string] }>()

const comparison = ref<ArtifactComparison | null>(null)
const loading = ref(false)
const failed = ref('')
const mode = ref<'changes' | 'side'>('changes')
let generation = 0

const beforeVersion = computed(() => props.versions.find((v) => v.card_id === props.before))
const afterVersion = computed(() => props.versions.find((v) => v.card_id === props.after))
const others = computed(() =>
  [...props.versions]
    .reverse()
    .filter((v) => v.card_id !== props.after)
    .map((v) => ({
      value: v.card_id,
      title: `${t('tasks.artifactComparison.version', { number: v.number })}${v.subject ? ` · ${v.subject}` : ''}`,
    }))
)

watch(
  () => [props.projectId, props.artifactId, props.before, props.after],
  async () => {
    const current = ++generation
    comparison.value = null
    failed.value = ''
    if (!props.before || !props.after || props.before === props.after) return
    loading.value = true
    try {
      const result = await compareArtifactVersions(props.projectId, props.artifactId, props.before, props.after)
      if (current !== generation) return
      comparison.value = result
      // 比不出逐字差异时，能看的只有两版并排。
      mode.value = sideOnly.value ? 'side' : 'changes'
    } catch (e) {
      if (current === generation)
        failed.value = e instanceof Error ? e.message : t('tasks.artifactComparison.loadError')
    } finally {
      if (current === generation) loading.value = false
    }
  },
  { immediate: true }
)

/** 一份 Word、表格或幻灯片：按段落、单元格、页比出来的那一份。 */
const office = computed(() => {
  const c = comparison.value
  if (c?.kind !== 'file') return null
  return (c.files[0] as { office?: OfficeComparison | null } | undefined)?.office ?? null
})

/** 交出去的是文件、却一行差异都给不出（二进制、太大）。 */
const sideOnly = computed(
  () =>
    comparison.value?.kind === 'unavailable' ||
    (comparison.value?.kind === 'file' && !office.value && comparison.value.files.every((file) => file.diff === null))
)

const fileNote = computed(() => noteText(comparison.value?.files[0]?.note))

const modes = computed(() => [
  { value: 'changes' as const, label: t('tasks.artifact.changes') },
  { value: 'side' as const, label: t('tasks.artifactComparison.showPreviews') },
])
</script>

<template>
  <div class="compare">
    <!-- Deliberately not an AppPage: this is a section inside ProjectArtifactView,
         which already owns the page frame (AppPage width="full"). It is the
         two-pane comparison view that page swaps its column to, so a page frame
         here would nest a second header and re-centre the column. -->
    <div class="compare__bar">
      <label class="compare__pick t-meta">
        {{ t('tasks.artifact.compareTo') }}
        <select
          :value="before"
          :aria-label="t('tasks.artifact.compareTo')"
          @change="emit('update:before', ($event.target as HTMLSelectElement).value)"
        >
          <option v-for="option in others" :key="option.value" :value="option.value">{{ option.title }}</option>
        </select>
      </label>
      <SegmentedControl
        v-if="comparison?.kind === 'file' && !sideOnly"
        v-model="mode"
        :options="modes"
        :label="t('tasks.artifact.view')"
      />
    </div>

    <p v-if="loading" class="t-body c-muted" role="status">{{ t('tasks.artifactComparison.loading') }}</p>
    <p v-else-if="failed" class="t-body c-danger" role="alert">{{ failed }}</p>

    <template v-else-if="comparison">
      <!-- 地址：只比记下来的那个地址，网页当时长什么样没有留。 -->
      <template v-if="comparison.kind === 'link'">
        <p class="t-body" role="status">
          {{
            comparison.identical ? t('tasks.artifactComparison.sameLink') : t('tasks.artifactComparison.changedLink')
          }}
        </p>
        <p class="t-meta c-faint">{{ t('tasks.artifactComparison.link') }}</p>
        <ul class="compare__links t-body">
          <li v-if="beforeVersion?.url">
            {{ t('tasks.artifactComparison.version', { number: beforeVersion.number }) }}
            <a :href="beforeVersion.url" target="_blank" rel="noopener noreferrer">{{ beforeVersion.url }}</a>
          </li>
          <li v-if="afterVersion?.url">
            {{ t('tasks.artifactComparison.version', { number: afterVersion.number }) }}
            <a :href="afterVersion.url" target="_blank" rel="noopener noreferrer">{{ afterVersion.url }}</a>
          </li>
        </ul>
      </template>

      <p v-else-if="comparison.identical" class="t-body" role="status">
        {{ t('tasks.artifactComparison.identical') }}
      </p>

      <template v-else-if="mode === 'side' || sideOnly">
        <p v-if="comparison.kind === 'unavailable'" class="t-meta c-faint">
          {{ t('tasks.artifactComparison.unavailable') }}
        </p>
        <p v-else-if="fileNote && !office" class="t-meta c-faint">{{ fileNote }}</p>
        <div v-if="beforeVersion && afterVersion" class="compare__side">
          <ArtifactVersionPreview :project-id="projectId" :artifact-id="artifactId" :version="beforeVersion" />
          <ArtifactVersionPreview :project-id="projectId" :artifact-id="artifactId" :version="afterVersion" />
        </div>
      </template>

      <OfficeCompare v-else-if="office" :comparison="office" />

      <template v-else>
        <p v-if="comparison.note" class="t-meta c-faint">{{ noteText(comparison.note) }}</p>
        <ArtifactChanges :files="comparison.files" />
      </template>
    </template>
  </div>
</template>

<style scoped lang="scss">
@use '../../styles/breakpoints.scss' as bp;

.compare {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.compare__bar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
}
.compare__pick {
  display: flex;
  white-space: nowrap;
  flex: 1 1 280px;
  align-items: center;
  gap: 8px;
  min-width: 0;
  color: var(--muted);
}
.compare__pick select {
  flex: 1 1 auto;
  min-width: 0;
  max-width: 420px;
  height: 36px;
  padding: 0 10px;
  border: 1px solid var(--line-2);
  border-radius: var(--radius-md);
  background: var(--surface);
  color: var(--text);
}
.compare__links {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin: 0;
  padding: 0;
  list-style: none;
  overflow-wrap: anywhere;
}
.compare__side {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
}
// 断点收进共享 token（`styles/breakpoints.scss`）：700 → 768（`$bp-phone`）。
@include bp.below(bp.$bp-phone) {
  .compare__side {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
