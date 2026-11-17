<template>
  <v-dialog v-model="open" :max-width="DIALOG_WIDTH.md" scrollable data-testid="learning-excerpts">
    <v-card rounded="lg">
      <v-card-title class="lc-dialog__title t-dialog-title">
        {{ t('spaces.analytics.learning.class.excerpt.title') }}
        <span v-if="data?.title" class="lc-muted"> · {{ data.title }}</span>
      </v-card-title>
      <v-card-text class="lc-dialog__body">
        <p class="an-note">{{ t('spaces.analytics.learning.class.excerpt.hint') }}</p>
        <v-progress-linear v-if="loading" indeterminate color="primary" class="lc-gap" />
        <p v-else-if="failed" class="an-note lc-gap">{{ t('spaces.analytics.learning.class.excerpt.failed') }}</p>
        <p v-else-if="data && !data.analysed" class="an-note lc-gap">
          {{ t('spaces.analytics.learning.class.notAnalysed') }}
        </p>
        <p v-else-if="data && !data.excerpts.length" class="an-note lc-gap">
          {{ t('spaces.analytics.learning.class.excerpt.empty') }}
        </p>
        <div v-for="(excerpt, index) in data?.excerpts ?? []" :key="index" class="lc-item lc-excerpt">
          <div class="lc-row">
            <span class="lc-muted"
              >{{ t('spaces.analytics.learning.class.memberCode', { code: excerpt.code }) }} ·
              {{ excerpt.taskName }}</span
            >
            <BaseButton v-if="excerpt.source" kind="ghost" size="sm" @click="emit('openSource', excerpt.source)">
              {{ t('spaces.analytics.learning.class.excerpt.source') }}
            </BaseButton>
          </div>
          <div
            v-for="(message, at) in excerpt.messages"
            :key="at"
            class="lc-msg"
            :class="{ 'lc-msg--agent': message.role === 'agent' }"
          >
            <div class="lc-msg__who">
              {{
                message.role === 'agent'
                  ? t('spaces.analytics.learning.class.excerpt.agent')
                  : t('spaces.analytics.learning.class.memberCode', { code: message.code })
              }}
              <span v-if="message.at"> · {{ formatLearningTime(message.at) }}</span>
            </div>
            <div class="lc-msg__text">{{ message.text }}</div>
          </div>
        </div>
      </v-card-text>
      <v-card-actions class="px-4 pb-3">
        <v-spacer />
        <BaseButton kind="primary" @click="open = false">{{
          t('spaces.analytics.learning.class.excerpt.close')
        }}</BaseButton>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<script setup lang="ts">
// 匿名对话摘录：一簇问题或一种误解，至多三段、三个不同的人。只标代号，姓名学号服务端
// 已经遮掉。「查看原文」只在服务端给了坐标（看的人读得了那个项目）时才有。
import type { LearningClassExcerpts, LearningExcerpt } from '@/types/apiSpaces'

import { useI18n } from 'vue-i18n'

import { formatLearningTime } from '../helpers'

import BaseButton from '@/components/base/BaseButton.vue'
import { DIALOG_WIDTH } from '@/components/base/dialogSize'

const open = defineModel<boolean>('open', { required: true })
withDefaults(defineProps<{ data: LearningClassExcerpts | null; loading?: boolean; failed?: boolean }>(), {
  loading: false,
  failed: false,
})
const emit = defineEmits<{ openSource: [source: NonNullable<LearningExcerpt['source']>] }>()
const { t } = useI18n()
</script>

<style scoped src="../analytics.css"></style>
<style scoped src="./learningClass.css"></style>

<style scoped>
.lc-dialog__title {
  padding: 20px 24px 8px;
}

.lc-dialog__body {
  padding: 0 24px 8px;
}

.lc-excerpt {
  margin-top: 12px;
}

.lc-msg {
  margin-top: 8px;
  padding: 8px 12px;
  border-radius: var(--radius-md);
  background: var(--fill);
}

.lc-msg--agent {
  background: transparent;
  border: 1px solid var(--line);
}

.lc-msg__who {
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
}

.lc-msg__text {
  margin-top: 4px;
  color: var(--text);
  font-size: 14px;
  line-height: var(--lh-14);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
</style>
