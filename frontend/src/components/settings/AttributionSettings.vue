<script setup lang="ts">
// 提交署名：这个项目的 PR 和提交算谁的。拆自 `views/ProjectSettingsView.vue`（#2143）。
//
// 两种：任务负责人（默认），或者 AI 队友。选中哪种、存没存上由页面管，这一件只画、
// 只把选中的那一种报上去。署名给任务负责人时，下面多一行看的人自己的 GitHub 账号：
// 他负责的任务用它创建 PR。账号在账号安全里管，这里只说状态、给一个就地关联的入口。
import type { CommitCreditMode } from '@/cx_types'
import type { OwnGithub } from '@/lib/githubAccount'
import type { NavTarget } from '@/lib/navTarget'

import BaseButton from '@/components/base/BaseButton.vue'
import NavLink from '@/components/common/NavLink.vue'
import { t } from '@/i18n'

defineOptions({ name: 'AttributionSettings' })

defineProps<{
  /** 当前那一种；还没读到时为 null。 */
  mode: CommitCreditMode | null
  /** 项目里给 AI 队友起的名字。 */
  agentName: string
  /** 正在保存（下拉禁用并转圈）。 */
  saving: boolean
  /** 上一次保存失败那句话。 */
  error: string | null
  /** 看的人自己的 GitHub 账号。 */
  github: OwnGithub
  /** 账号安全那一页：GitHub 账号在那里管理。 */
  manageTo: NavTarget
}>()

const emit = defineEmits<{
  save: [mode: CommitCreditMode]
  /** 去 GitHub 关联（或重新授权）看的人自己的账号。 */
  link: []
}>()

const items = [
  { title: t('work.projectSettings.attribution.owner'), value: 'owner' },
  { title: t('work.projectSettings.attribution.agent'), value: 'agent' },
]
</script>

<template>
  <section class="page-section" data-testid="forge-attribution">
    <div class="page-section-head">
      <v-icon size="14" class="c-faint">mdi-account-edit-outline</v-icon>
      <span class="page-section-title">{{ t('work.projectSettings.attribution.title') }}</span>
    </div>
    <div class="page-section-body">
      <v-alert v-if="error" type="error" density="compact" class="mb-3">{{ error }}</v-alert>
      <v-select
        autocomplete="off"
        :model-value="mode"
        :items="items"
        :label="t('work.projectSettings.attribution.label')"
        :loading="saving"
        :disabled="saving"
        hide-details
        @update:model-value="emit('save', $event)"
      />
      <p v-if="mode" class="t-body c-muted mt-2">
        {{
          mode === 'owner'
            ? t('work.projectSettings.attribution.ownerHint', { agent: agentName })
            : t('work.projectSettings.attribution.agentHint', { agent: agentName })
        }}
      </p>
      <p class="t-body c-faint mt-2 settings-hint">
        {{ t('work.projectSettings.attribution.hint') }}
      </p>
      <div v-if="mode === 'owner' && github.state !== 'unknown'" class="own-github" data-testid="own-github">
        <div class="own-github__row">
          <span class="t-body c-muted">{{ t('work.projectSettings.attribution.yourGithub') }}</span>
          <span v-if="github.state === 'none'" class="t-body c-muted own-github__value">
            {{ t('work.projectSettings.attribution.unlinked') }}
          </span>
          <span v-else class="t-body own-github__value own-github__login">@{{ github.login }}</span>
          <BaseButton v-if="github.state === 'none'" kind="secondary" size="sm" @click="emit('link')">
            {{ t('work.projectSettings.attribution.link') }}
          </BaseButton>
          <BaseButton v-else-if="github.state === 'expired'" kind="secondary" size="sm" @click="emit('link')">
            {{ t('work.projectSettings.attribution.relink') }}
          </BaseButton>
          <NavLink v-else :to="manageTo" class="t-body own-github__manage">
            {{ t('work.projectSettings.attribution.manage') }}
          </NavLink>
        </div>
        <span v-if="github.state === 'none'" class="t-body c-muted settings-hint">
          {{ t('work.projectSettings.attribution.linkHint') }}
        </span>
        <span v-else-if="github.state === 'expired'" class="t-body settings-hint own-github__expired">
          {{ t('work.projectSettings.attribution.expired', { agent: agentName }) }}
        </span>
      </div>
    </div>
  </section>
</template>

<style scoped src="./settings-section.css"></style>
<style scoped>
.own-github {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin-top: 8px;
  padding-top: 12px;
  border-top: 1px solid var(--line);
}
.own-github__row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
}
.own-github__value {
  flex: 1;
  min-width: 0;
  overflow-wrap: anywhere;
}
.own-github__login {
  color: var(--ink);
  font-weight: 600;
}
.own-github__manage {
  color: var(--text);
  text-decoration: underline;
  text-underline-offset: 2px;
}
.own-github__expired {
  color: var(--danger-ink);
}
</style>
