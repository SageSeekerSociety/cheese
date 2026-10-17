<script setup lang="ts">
// 项目总览最上面的「现状」：AI 队友读项目的目标、近两周做完和在做的事、频道里人说的
// 话，写下的几句。什么时候重写由后端定（`project_situation.service`），这一块只画。
import CheeseAvatar from '@/components/CheeseAvatar.vue'
import MarkdownView from '@/components/common/MarkdownView.vue'
import { t } from '@/i18n'
import { relTime } from '@/lib/relTime'

defineProps<{
  /** 上一次写下的 Markdown；还没写成过是空串。 */
  content: string
  writtenAt: string | null
  /** 后台正在重写。 */
  writing: boolean
  agentName: string
  agentHandle: string | null
}>()
</script>

<template>
  <section class="situation" data-testid="overview-situation" :aria-busy="writing">
    <div class="situation__head">
      <CheeseAvatar :size="20" :name="agentName" :handle="agentHandle" />
      <h2 class="situation__title">{{ t('work.overview.situation') }}</h2>
      <span class="situation__when t-meta c-muted">
        <template v-if="writing">{{ t('work.overview.situationWriting', { agent: agentName }) }}</template>
        <template v-else-if="writtenAt">
          {{ t('work.overview.situationWritten', { agent: agentName, when: relTime(writtenAt) }) }}
        </template>
      </span>
    </div>
    <div v-if="content" class="situation__body" data-user-content>
      <MarkdownView class="md-content" :source="content" />
    </div>
  </section>
</template>

<style scoped>
.situation {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 20px 24px 18px;
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
}
.situation__head {
  display: flex;
  align-items: center;
  gap: 8px;
}
.situation__title {
  flex: 1 1 auto;
  margin: 0;
  font-size: 15px;
  line-height: var(--lh-15);
  font-weight: 600;
  color: var(--ink);
}
.situation__when {
  flex: none;
}
.situation__body {
  color: var(--text);
}
</style>
