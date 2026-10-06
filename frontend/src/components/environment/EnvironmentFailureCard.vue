<script setup lang="ts">
// 环境页最上面那张：这个频道最近一次准备失败。哪一步、有几段对话在等、日志末尾，
// 以及「让芝士看看」和「重试」。芝士看过之后，原因、改法和改动的那几行接在下面，
// 采用了才会保存并重试；不采用什么都不变。
import type { EnvironmentDiagnosis, EnvironmentFailure } from '@/types/environment'

import { computed } from 'vue'

import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'
import { type DiffLine, lineDiff } from '@/lib/lineDiff'
import { relTime } from '@/lib/relTime'

const props = defineProps<{
  failure: EnvironmentFailure
  roomTitle: string
  canEdit: boolean
  /** 项目给 AI 队友起的名字。 */
  agentName: string
  diagnosis: EnvironmentDiagnosis | null
  diagnosing: boolean
  retrying: boolean
}>()

const emit = defineEmits<{
  (e: 'diagnose'): void
  (e: 'retry'): void
  (e: 'adopt'): void
  (e: 'dismiss'): void
}>()

const stage = computed(() =>
  props.failure.stage === 'startup'
    ? t('work.projectSettings.environment.stage.startup')
    : t('work.projectSettings.environment.stage.setup')
)

/** 改法里动了的那一段脚本，逐行标出加的、删的。 */
const changes = computed(() => {
  const d = props.diagnosis
  if (!d) return []
  const out: { label: string; lines: DiffLine[] }[] = []
  if (d.setup_script !== null && d.setup_script !== d.ran.setup_script)
    out.push({
      label: t('work.projectSettings.environment.setupLabel'),
      lines: lineDiff(d.ran.setup_script, d.setup_script),
    })
  if (d.startup_script !== null && d.startup_script !== d.ran.startup_script)
    out.push({
      label: t('work.projectSettings.environment.startupLabel'),
      lines: lineDiff(d.ran.startup_script, d.startup_script),
    })
  return out
})
</script>

<template>
  <section class="env-failure" data-testid="environment-failure">
    <div class="env-failure__head">
      <v-icon size="18" class="env-failure__mark">mdi-alert-circle-outline</v-icon>
      <div class="env-failure__what">
        <div class="t-body env-failure__title">
          {{ t('work.projectSettings.environment.failure.title', { when: relTime(failure.at) }) }}
          · <span data-user-content>#{{ roomTitle }}</span>
        </div>
        <div class="t-meta c-muted">
          {{ stage }}
          <template v-if="failure.exit_code !== null">
            · {{ t('work.projectSettings.environment.failure.exitCode', { code: failure.exit_code }) }}</template
          >
          <template v-if="failure.waiting">
            · {{ t('work.projectSettings.environment.failure.waiting', { count: failure.waiting }) }}</template
          >
        </div>
      </div>
      <div v-if="canEdit" class="env-failure__actions">
        <BaseButton
          kind="secondary"
          prepend-icon="mdi-creation-outline"
          :loading="diagnosing"
          :disabled="retrying"
          data-testid="environment-diagnose"
          @click="emit('diagnose')"
        >
          {{ t('work.projectSettings.environment.failure.diagnose', { name: agentName }) }}
        </BaseButton>
        <BaseButton kind="primary" :loading="retrying" :disabled="diagnosing" @click="emit('retry')">
          {{ t('work.projectSettings.environment.failure.retry') }}
        </BaseButton>
      </div>
    </div>
    <pre v-if="failure.log" class="env-failure__log">{{ failure.log }}</pre>

    <div v-if="diagnosis" class="env-failure__answer" data-testid="environment-diagnosis">
      <p class="t-body">
        <strong>{{ t('work.projectSettings.environment.failure.reason') }}</strong
        >{{ diagnosis.reason }}
      </p>
      <p v-if="diagnosis.change" class="t-body">
        <strong>{{ t('work.projectSettings.environment.failure.change') }}</strong
        >{{ diagnosis.change }}
      </p>
      <div v-for="script in changes" :key="script.label" class="env-failure__diff">
        <div class="t-meta c-muted">{{ script.label }}</div>
        <pre class="env-failure__code"><span
          v-for="(line, i) in script.lines"
          :key="i"
          :class="`env-diff env-diff--${line.kind}`"
        >{{ line.kind === 'added' ? '+ ' : line.kind === 'removed' ? '- ' : '  ' }}{{ line.text }}
</span></pre>
      </div>
      <p class="t-meta c-faint">
        {{
          diagnosis.sure
            ? t('work.projectSettings.environment.failure.readOnly')
            : t('work.projectSettings.environment.failure.unsure')
        }}
      </p>
      <div class="env-failure__actions">
        <BaseButton kind="ghost" @click="emit('dismiss')">{{
          t('work.projectSettings.environment.failure.dismiss')
        }}</BaseButton>
        <BaseButton
          v-if="changes.length"
          kind="primary"
          :loading="retrying"
          data-testid="environment-adopt"
          @click="emit('adopt')"
        >
          {{ t('work.projectSettings.environment.failure.adopt') }}
        </BaseButton>
      </div>
    </div>
  </section>
</template>

<style scoped>
.env-failure {
  display: flex;
  flex-direction: column;
  gap: 12px;
  margin-bottom: 16px;
  padding: 16px;
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
}
.env-failure__head {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-start;
  gap: 8px 12px;
}
.env-failure__mark {
  margin-top: 2px;
  color: var(--warn);
}
.env-failure__what {
  flex: 1 1 240px;
  min-width: 0;
}
.env-failure__title {
  font-weight: 600;
  color: var(--ink);
}
.env-failure__actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.env-failure__log,
.env-failure__code {
  max-height: 240px;
  margin: 0;
  padding: 12px;
  overflow: auto;
  border-radius: var(--radius-md);
  background: var(--fill);
  font-family: var(--font-mono);
  font-size: 12px;
  line-height: var(--lh-12);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.env-failure__answer {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding-top: 12px;
  border-top: 1px solid var(--line);
}
.env-failure__answer p {
  margin: 0;
}
.env-diff {
  display: block;
}
.env-diff--added {
  background: var(--ok-wash);
  color: var(--ok-ink);
}
.env-diff--removed {
  background: var(--danger-wash);
  color: var(--danger-ink);
}
</style>
