<template>
  <v-dialog v-model="open" :max-width="DIALOG_WIDTH.md" scrollable data-testid="learning-export">
    <v-card rounded="lg">
      <v-card-title class="lc-dialog__title t-dialog-title">
        {{ t('spaces.analytics.learning.class.exportDialog.title') }}
      </v-card-title>
      <v-card-text class="lc-dialog__body">
        <p class="an-note">{{ t('spaces.analytics.learning.class.exportDialog.hint') }}</p>

        <template v-if="point">
          <h4 class="lc-dialog__h">{{ t('spaces.analytics.learning.class.exportDialog.scope') }}</h4>
          <v-radio-group v-model="scope" density="compact" hide-details>
            <v-radio value="all" :label="t('spaces.analytics.learning.class.exportDialog.scopeAll')" />
            <v-radio
              value="one"
              :label="t('spaces.analytics.learning.class.exportDialog.scopeOne', { name: point.name })"
            />
          </v-radio-group>
        </template>

        <h4 class="lc-dialog__h">{{ t('spaces.analytics.learning.class.exportDialog.content') }}</h4>
        <v-checkbox
          v-for="section in SECTIONS"
          :key="section"
          v-model="sections"
          :value="section"
          :label="t(`spaces.analytics.learning.class.exportDialog.sections.${section}`)"
          density="compact"
          hide-details
        />

        <h4 class="lc-dialog__h">{{ t('spaces.analytics.learning.class.exportDialog.format') }}</h4>
        <v-radio-group v-model="format" inline density="compact" hide-details>
          <v-radio
            v-for="item in FORMATS"
            :key="item"
            :value="item"
            :label="t(`spaces.analytics.learning.class.exportDialog.formats.${item}`)"
          />
        </v-radio-group>
      </v-card-text>
      <v-card-actions class="px-4 pb-3">
        <v-spacer />
        <BaseButton kind="ghost" @click="open = false">{{
          t('spaces.analytics.learning.class.exportDialog.cancel')
        }}</BaseButton>
        <BaseButton kind="primary" :loading="exporting" :disabled="!sections.length" @click="submit">
          {{ t('spaces.analytics.learning.class.exportDialog.submit') }}
        </BaseButton>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<script setup lang="ts">
// 导出报告：范围（全部或一个知识点）、内容（四项，匿名摘录默认不勾）、格式（PDF / Excel / Word）。
// 只把选择交上去；取文件、存盘在容器那边。
import type { LearningExportFormat, LearningExportRequest, LearningExportSection } from '@/types/apiSpaces'

import { ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import BaseButton from '@/components/base/BaseButton.vue'
import { DIALOG_WIDTH } from '@/components/base/dialogSize'

const open = defineModel<boolean>('open', { required: true })
const props = withDefaults(
  defineProps<{
    /** 从知识点详情打开时那个知识点：多出「只导出它」这一项，并默认选它。 */
    point?: { id: number; name: string } | null
    exporting?: boolean
  }>(),
  { point: null, exporting: false }
)
const emit = defineEmits<{ export: [request: LearningExportRequest] }>()
const { t } = useI18n()

const SECTIONS: LearningExportSection[] = ['tasks', 'points', 'advice', 'excerpts']
const FORMATS: LearningExportFormat[] = ['pdf', 'xlsx', 'docx']
/** 匿名摘录默认不勾：它是原话，哪怕只标代号，也要老师自己决定带不带出去。 */
const DEFAULT_SECTIONS: LearningExportSection[] = ['tasks', 'points', 'advice']

const sections = ref<LearningExportSection[]>([...DEFAULT_SECTIONS])
const format = ref<LearningExportFormat>('pdf')
const scope = ref<'all' | 'one'>('all')

watch(
  open,
  (value) => {
    if (!value) return
    sections.value = [...DEFAULT_SECTIONS]
    format.value = 'pdf'
    scope.value = props.point ? 'one' : 'all'
  },
  { immediate: true }
)

const submit = () => {
  emit('export', {
    format: format.value,
    sections: SECTIONS.filter((section) => sections.value.includes(section)),
    knowledgePointId: scope.value === 'one' && props.point ? props.point.id : null,
  })
}
</script>

<style scoped src="../analytics.css"></style>

<style scoped>
.lc-dialog__title {
  padding: 20px 24px 8px;
}

.lc-dialog__body {
  padding: 0 24px 8px;
}

.lc-dialog__h {
  margin: 16px 0 4px;
  color: var(--ink);
  font-size: 14px;
  font-weight: 600;
  line-height: var(--lh-14);
}
</style>
