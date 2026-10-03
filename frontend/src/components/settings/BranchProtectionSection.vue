<script setup lang="ts">
import type { BranchProtectionLoadState } from '@/composables/useBranchProtection'
import type { BranchProtection, BranchProtectionPatch } from '@/cx_types'

import { computed } from 'vue'

import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'

// 分支保护 (#718) 的那一块模板：平台侧的合并规则，照 GitHub 分支保护那一页的顺序
// 排。拆自 `views/ProjectSettingsView.vue`（#2143）。
//
// 这一件不认识接口：取数、保存、两个草稿（新检查的名字与路径范围）和批准人数的
// 校验都在 `composables/useBranchProtection.ts`，它只把当前状态画出来、把人的动作
// 往上报。所以哪一条规则灰掉、哪一条保存中转圈，看 props 就够了。
//
// **灰显的两档在这里算**（`ghEnforced` / `bpBusy`）：它们是「这一格现在能不能动」，
// 是把 props 读成 disabled，不是判断，所以不必再往上要一个 prop。
defineOptions({ name: 'BranchProtectionSection' })

const props = defineProps<{
  /** 这一块自己的四态（GET 要问 GitHub，可能比这一页别的请求慢）。 */
  state: BranchProtectionLoadState
  loadError: string | null
  /** 读回来的规则；`state === 'loaded'` 时才有。 */
  bp: BranchProtection | null
  /** 正在保存的那一条的 key（'' = 没有）。所有控件在它非空时一起禁用。 */
  saving: string | null
  /** 上一次保存失败那句话。 */
  error: string | null
  /** 人工放行名单的可选项（成员，去掉 AI 队友）。 */
  memberItems: { title: string; value: string }[]
  /** 任务默认 reviewer 的可选项（多一个「未指定」）。 */
  reviewerItems: { title: string; value: string }[]
  /** 新检查的两个草稿：名字与路径范围。 */
  checkName: string
  checkPaths: string
  /** 批准人数的草稿字符串：非法输入要被拒、弹回原值。 */
  approvalsDraft: string
}>()

const emit = defineEmits<{
  retry: []
  /** 一条规则的一次改动：`key` 指哪一条（哪一颗 spinner 转）。 */
  save: [patch: BranchProtectionPatch, key: string]
  'add-check': []
  'remove-check': [index: number]
  'save-approvals': []
  'save-override': [handles: string[]]
  'update:checkName': [value: string]
  'update:checkPaths': [value: string]
  'update:approvalsDraft': [value: string]
  'clear-error': []
}>()

// GitHub 自己开了保护时，平台的同名规则灰掉（#718 拍板②：两处都能改就是两套配置）。
// 同名 = 出现在 GitHub 分支保护那一页的规则。自动合并和任务默认 reviewer 是平台自己
// 的概念，保持可编。status === 'unknown' 查不到 ≠ 已开启，不灰。
const ghEnforced = computed(() => props.bp?.github_protection.enforced ?? false)
const bpBusy = computed(() => props.saving !== null)
</script>

<template>
  <section class="page-section">
    <div class="page-section-head">
      <v-icon size="14" class="c-faint">mdi-shield-outline</v-icon>
      <span class="page-section-title">{{ t('work.projectSettings.merge.title') }}</span>
    </div>
    <div class="page-section-body">
      <!-- 加载中 -->
      <div v-if="state === 'loading'" class="d-flex align-center" style="gap: 8px">
        <v-progress-circular indeterminate size="16" width="2" color="primary" />
        <span class="t-body c-muted">{{ t('work.projectSettings.merge.loading') }}</span>
      </div>

      <!-- 加载失败 -->
      <div v-else-if="state === 'error'" class="d-flex align-center" style="gap: 8px">
        <v-icon size="18" color="error">mdi-alert-circle-outline</v-icon>
        <span class="t-body text-error">{{ loadError ?? t('work.projectSettings.merge.loadFailed') }}</span>
        <v-spacer />
        <BaseButton kind="secondary" size="sm" @click="emit('retry')">
          {{ t('work.projectSettings.merge.retry') }}
        </BaseButton>
      </div>

      <template v-else-if="bp">
        <!-- GitHub 已开保护: 顶行提示 + 同名规则灰掉；查不到状态只说明，不灰 -->
        <div v-if="bp.github_protection.enforced" class="bp-github-note">
          <v-icon size="16" class="c-muted">mdi-github</v-icon>
          <span>{{ t('work.projectSettings.merge.githubEnforced') }}</span>
        </div>
        <p v-else-if="bp.github_protection.status === 'unknown'" class="t-body c-faint settings-hint">
          {{ t('work.projectSettings.merge.githubUnknown') }}
        </p>

        <v-alert v-if="error" type="error" density="compact" closable @click:close="emit('clear-error')">
          {{ error }}
        </v-alert>

        <!-- 1. 合并前必须通过的检查 -->
        <div class="bp-row bp-row--stack">
          <div class="bp-main">
            <div class="bp-label">{{ t('work.projectSettings.merge.checks') }}</div>
            <div class="bp-hint c-faint">{{ t('work.projectSettings.merge.checksHint') }}</div>
          </div>
          <div v-if="bp.required_checks.length === 0" class="bp-hint c-muted">
            {{ t('work.projectSettings.merge.checksNone') }}
          </div>
          <div v-for="(c, i) in bp.required_checks" :key="`${c.name}-${i}`" class="bp-check">
            <span class="bp-check-name">{{ c.name }}</span>
            <span v-if="c.paths?.length" class="bp-check-paths c-muted">{{
              c.paths.join(t('work.projectSettings.merge.pathSeparator'))
            }}</span>
            <span v-else class="bp-check-paths c-faint">{{ t('work.projectSettings.merge.allFiles') }}</span>
            <v-spacer />
            <BaseButton
              icon="mdi-close"
              size="sm"
              :title="t('work.projectSettings.merge.removeCheck')"
              :disabled="ghEnforced || bpBusy"
              @click="emit('remove-check', i)"
            />
          </div>
          <div class="d-flex align-center" style="gap: 8px">
            <v-text-field
              :model-value="checkName"
              autocomplete="off"
              density="compact"
              variant="outlined"
              hide-details
              :placeholder="t('work.projectSettings.merge.checkName')"
              style="flex: 1"
              :disabled="ghEnforced || bpBusy"
              @update:model-value="emit('update:checkName', $event)"
              @keydown.enter="emit('add-check')"
            />
            <v-text-field
              :model-value="checkPaths"
              autocomplete="off"
              density="compact"
              variant="outlined"
              hide-details
              :placeholder="t('work.projectSettings.merge.checkPaths')"
              style="flex: 1"
              :disabled="ghEnforced || bpBusy"
              @update:model-value="emit('update:checkPaths', $event)"
              @keydown.enter="emit('add-check')"
            />
            <BaseButton
              kind="secondary"
              size="sm"
              :disabled="ghEnforced || !checkName.trim()"
              :loading="saving === 'required_checks'"
              @click="emit('add-check')"
            >
              {{ t('work.projectSettings.merge.add') }}
            </BaseButton>
          </div>
        </div>

        <!-- 2. strict -->
        <div class="bp-row">
          <div class="bp-main">
            <div class="bp-label">{{ t('work.projectSettings.merge.strict') }}</div>
            <div class="bp-hint c-faint">{{ t('work.projectSettings.merge.strictHint') }}</div>
          </div>
          <v-switch
            density="compact"
            color="primary"
            hide-details
            :model-value="bp.strict"
            :disabled="ghEnforced || bpBusy"
            :loading="saving === 'strict' ? 'primary' : false"
            @update:model-value="emit('save', { strict: !!$event }, 'strict')"
          />
        </div>

        <!-- 3. dismiss_stale -->
        <div class="bp-row">
          <div class="bp-main">
            <div class="bp-label">{{ t('work.projectSettings.merge.dismissStale') }}</div>
            <div class="bp-hint c-faint">{{ t('work.projectSettings.merge.dismissStaleHint') }}</div>
          </div>
          <v-switch
            density="compact"
            color="primary"
            hide-details
            :model-value="bp.dismiss_stale"
            :disabled="ghEnforced || bpBusy"
            :loading="saving === 'dismiss_stale' ? 'primary' : false"
            @update:model-value="emit('save', { dismiss_stale: !!$event }, 'dismiss_stale')"
          />
        </div>

        <!-- 4. auto_merge_allowed（平台自己的概念，不随 GitHub 灰掉） -->
        <div class="bp-row">
          <div class="bp-main">
            <div class="bp-label">{{ t('work.projectSettings.merge.autoMerge') }}</div>
            <div class="bp-hint c-faint">{{ t('work.projectSettings.merge.autoMergeHint') }}</div>
          </div>
          <v-switch
            density="compact"
            color="primary"
            hide-details
            :model-value="bp.auto_merge_allowed"
            :disabled="bpBusy"
            :loading="saving === 'auto_merge_allowed' ? 'primary' : false"
            @update:model-value="emit('save', { auto_merge_allowed: !!$event }, 'auto_merge_allowed')"
          />
        </div>

        <!-- 5. override_handles -->
        <div class="bp-row">
          <div class="bp-main">
            <div class="bp-label">{{ t('work.projectSettings.merge.override') }}</div>
            <div class="bp-hint c-faint">{{ t('work.projectSettings.merge.overrideHint') }}</div>
          </div>
          <v-select
            autocomplete="off"
            density="compact"
            variant="outlined"
            hide-details
            multiple
            chips
            closable-chips
            :placeholder="t('work.projectSettings.merge.overridePlaceholder')"
            style="max-width: 320px"
            :items="memberItems"
            :model-value="bp.override_handles ?? []"
            :disabled="ghEnforced || bpBusy"
            :loading="saving === 'override_handles'"
            @update:model-value="emit('save-override', $event)"
          />
        </div>

        <!-- 6. approvals_required -->
        <div class="bp-row">
          <div class="bp-main">
            <div class="bp-label">{{ t('work.projectSettings.merge.approvals') }}</div>
            <div class="bp-hint c-faint">{{ t('work.projectSettings.merge.approvalsHint') }}</div>
          </div>
          <v-text-field
            :model-value="approvalsDraft"
            type="number"
            min="1"
            density="compact"
            variant="outlined"
            hide-details
            style="max-width: 96px"
            :disabled="ghEnforced || bpBusy"
            :loading="saving === 'approvals_required'"
            @update:model-value="emit('update:approvalsDraft', $event)"
            @change="emit('save-approvals')"
            @keydown.enter="emit('save-approvals')"
          />
        </div>

        <!-- 7. merge_method（只读附注） -->
        <div class="bp-row">
          <div class="bp-main">
            <div class="bp-label">{{ t('work.projectSettings.merge.mergeMethod') }}</div>
            <div class="bp-hint c-faint">{{ t('work.projectSettings.merge.mergeMethodHint') }}</div>
          </div>
          <span class="bp-check-name">{{ bp.merge_method }}</span>
        </div>

        <!-- 8. default_reviewer（平台自己的概念，不随 GitHub 灰掉） -->
        <div class="bp-row">
          <div class="bp-main">
            <div class="bp-label">{{ t('work.projectSettings.merge.defaultReviewer') }}</div>
            <div class="bp-hint c-faint">{{ t('work.projectSettings.merge.defaultReviewerHint') }}</div>
          </div>
          <v-select
            autocomplete="off"
            density="compact"
            variant="outlined"
            hide-details
            style="max-width: 320px"
            :items="reviewerItems"
            :model-value="bp.default_reviewer"
            :disabled="bpBusy"
            :loading="saving === 'default_reviewer'"
            @update:model-value="emit('save', { default_reviewer: $event ?? '' }, 'default_reviewer')"
          />
        </div>
      </template>
    </div>
  </section>
</template>

<style scoped src="./settings-section.css"></style>

<style scoped>
/* 分支保护 (#718)。规则行：左边名称 + 说明，右边控件；GitHub 已执行时控件 disabled
   （Vuetify 自己降透明度），行本身不动 —— 灰掉不是藏起来。 */
.bp-github-note {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  background: var(--fill);
  color: var(--text);
  font-size: 13px;
}
.bp-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
  padding: 4px 0;
}
.bp-row--stack {
  flex-direction: column;
  align-items: stretch;
  gap: 8px;
}
/* 说明至少留 240 宽：控件和它挤不下一行时，控件换到说明下面，而不是把说明挤成
   一列。开关那种窄控件照旧排在右边。 */
.bp-main {
  flex: 1 1 240px;
  min-width: 0;
}
.bp-row--stack > .bp-main {
  flex: none;
}
/* 下拉框不缩到看不清选了什么：放不下就整个换到下一行。 */
.bp-row > .v-select {
  flex: 1 1 200px;
}
.bp-label {
  font-size: 13px;
  font-weight: 500;
  color: var(--text);
}
.bp-hint {
  font-size: 12px;
  margin-top: 1px;
}
.bp-check {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 10px;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
}
.bp-check-name {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12px;
  padding: 1px 6px;
  background: var(--fill);
  border-radius: var(--radius-sm);
  color: var(--text);
}
.bp-check-paths {
  font-size: 12px;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
