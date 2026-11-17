<script setup lang="ts">
// 成员进这块板时那张告知卡：一次读完，按钮只有「我已阅读并知悉」。
//
// 它只画这一段说明和那一颗按钮 —— 要不要弹（`needsAcknowledge`）、点了之后记在哪，
// 全在调用方（`views/spaces/Detail.vue` 进板时那条路、以及设置页的预览）。所以这一件
// 是纯 props + emit（场景规则见 docs/manual/dev/scenes.md），组件预览站里也能单独看。
//
// 不能从别处关掉：没有 ✕、按 Esc 不关、点遮罩也不关（`persistent`）。要它只在
// 「读过并知悉」之后消失，是这件事本身的意思 —— 关掉还能再弹，就等于没告知。
import { useI18n } from 'vue-i18n'

import BaseButton from '@/components/base/BaseButton.vue'
import { DIALOG_WIDTH } from '@/components/base/dialogSize'

const { t } = useI18n()

const open = defineModel<boolean>('open', { required: true })

withDefaults(defineProps<{ acknowledging?: boolean }>(), { acknowledging: false })

const emit = defineEmits<{ acknowledge: [] }>()

/** 卡片正文的那几条：一句一条，顺序与设计稿一致（见 xueqing/demo.html 的 `#consent`）。 */
const POINTS = ['pointEvidence', 'pointTeacher', 'pointOtherRooms', 'pointLeaving', 'pointGroups'] as const
</script>

<template>
  <v-dialog v-model="open" :max-width="DIALOG_WIDTH.md" persistent scrollable data-testid="learning-notice">
    <v-card rounded="lg">
      <v-card-title class="learning-notice__title t-dialog-title">
        {{ t('spaces.learningAnalysis.notice.title') }}
      </v-card-title>

      <v-card-text class="learning-notice__body">
        <p class="learning-notice__lead">{{ t('spaces.learningAnalysis.notice.lead') }}</p>
        <ul class="learning-notice__list">
          <li v-for="key in POINTS" :key="key">{{ t(`spaces.learningAnalysis.notice.${key}`) }}</li>
        </ul>
      </v-card-text>

      <v-card-actions class="px-4 pb-3">
        <v-spacer />
        <BaseButton kind="primary" :loading="acknowledging" @click="emit('acknowledge')">
          {{ t('spaces.learningAnalysis.notice.ack') }}
        </BaseButton>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<style scoped>
.learning-notice__title {
  padding: 20px 24px 8px;
}

.learning-notice__body {
  padding: 0 24px 8px;
  font-size: 14px;
  line-height: var(--lh-14);
  color: var(--text);
}

.learning-notice__lead {
  margin: 0 0 8px;
}

.learning-notice__list {
  padding-left: 18px;
  margin: 0;
}

.learning-notice__list li {
  margin: 4px 0;
}
</style>
