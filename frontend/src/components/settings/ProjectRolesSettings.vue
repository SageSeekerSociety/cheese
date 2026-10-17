<script setup lang="ts">
// 项目设置里的「角色和权限」（#3830）：一张「角色 × 权限」表。每个人都看得到，好知道
// 一个项目里谁能做什么；只有管理员能勾选。锁定的格子（管人和改角色）不能动。
//
// 它只画：勾选的结果往上报，存不存、存没存上由页面管。
import type { PermissionGroup, ProjectRole } from '@/lib/projectPermissions'

import { computed } from 'vue'

import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'
import { isLocked, PERMISSION_GROUPS, PERMISSIONS, PROJECT_ROLES } from '@/lib/projectPermissions'

const props = defineProps<{
  /** 每个角色现在有的权限。 */
  grants: Record<ProjectRole, readonly string[]>
  /** 能不能改：只有管理员能。 */
  editable: boolean
  /** 项目给 AI 队友起的名字。 */
  agentName: string
  /** 和默认值一样时不给「恢复默认」。 */
  isDefault: boolean
  /** 读或存失败时那一句。 */
  error?: string | null
}>()

const emit = defineEmits<{
  toggle: [role: ProjectRole, permission: string, granted: boolean]
  reset: []
}>()

const rows = computed(() =>
  PERMISSION_GROUPS.map((group: PermissionGroup) => ({
    group,
    items: PERMISSIONS.filter((p) => p.group === group),
  }))
)

function has(role: ProjectRole, permission: string): boolean {
  return props.grants[role].includes(permission)
}

function hint(permission: string): string {
  return t(`work.projectSettings.roles.permHint.${permission}`, { agent: props.agentName })
}
</script>

<template>
  <section class="page-section roles">
    <v-alert v-if="error" type="error" density="comfortable" class="mb-4">{{ error }}</v-alert>
    <p class="settings-hint c-muted roles__intro">
      {{ t('work.projectSettings.roles.intro') }}
    </p>
    <table class="roles__table">
      <thead>
        <tr>
          <th class="roles__corner" scope="col" />
          <th v-for="role in PROJECT_ROLES" :key="role" class="roles__role" scope="col">
            <span class="roles__role-name">{{ t(`work.projectSettings.roles.role.${role}`) }}</span>
          </th>
        </tr>
      </thead>
      <tbody v-for="row in rows" :key="row.group">
        <tr>
          <th class="roles__group" colspan="4" scope="colgroup">
            {{ t(`work.projectSettings.roles.group.${row.group}`) }}
          </th>
        </tr>
        <tr v-for="p in row.items" :key="p.key" class="roles__row">
          <th class="roles__perm" scope="row">
            <span class="roles__perm-name">{{
              t(`work.projectSettings.roles.perm.${p.key}`, { agent: agentName })
            }}</span>
            <span class="roles__perm-hint">{{ hint(p.key) }}</span>
          </th>
          <td v-for="role in PROJECT_ROLES" :key="role" class="roles__cell">
            <v-icon
              v-if="isLocked(p.key)"
              size="18"
              class="roles__lock"
              :class="{ 'roles__lock--on': has(role, p.key) }"
              :title="t('work.projectSettings.roles.locked')"
              :aria-label="t('work.projectSettings.roles.locked')"
            >
              {{ has(role, p.key) ? 'mdi-check' : 'mdi-minus' }}
            </v-icon>
            <v-checkbox-btn
              v-else
              :model-value="has(role, p.key)"
              :disabled="!editable"
              density="compact"
              class="roles__check"
              :aria-label="`${t(`work.projectSettings.roles.role.${role}`)} ${t(`work.projectSettings.roles.perm.${p.key}`, { agent: agentName })}`"
              @update:model-value="emit('toggle', role, p.key, Boolean($event))"
            />
          </td>
        </tr>
      </tbody>
    </table>
    <div class="roles__foot">
      <span v-if="!editable" class="settings-hint c-muted">{{ t('work.projectSettings.roles.readOnly') }}</span>
      <BaseButton v-else-if="!isDefault" size="sm" kind="ghost" @click="emit('reset')">
        {{ t('work.projectSettings.roles.reset') }}
      </BaseButton>
    </div>
  </section>
</template>

<style scoped src="./settings-section.css"></style>
<style scoped>
.roles__intro {
  margin: 0 0 16px;
}

.roles__table {
  width: 100%;
  border-collapse: collapse;
  table-layout: fixed;
}

.roles__corner {
  width: 58%;
}

.roles__role {
  padding: 0 8px 12px;
  text-align: center;
  vertical-align: bottom;
  border-bottom: 1px solid var(--line);
}

.roles__role-name {
  display: block;
  font-size: 14px;
  font-weight: 600;
  line-height: var(--lh-14);
  color: var(--ink);
}

.roles__group {
  padding: 20px 0 6px;
  font-size: 13px;
  font-weight: 600;
  line-height: var(--lh-13);
  color: var(--muted);
  text-align: left;
}

.roles__row {
  border-top: 1px solid var(--line);
}

.roles__perm {
  padding: 10px 12px 10px 0;
  font-weight: 400;
  text-align: left;
}

.roles__perm-name {
  display: block;
  font-size: 14px;
  line-height: var(--lh-14);
  color: var(--text);
}

.roles__perm-hint {
  display: block;
  margin-top: 2px;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}

.roles__cell {
  text-align: center;
}

.roles__check {
  justify-content: center;
}

.roles__lock {
  color: var(--faint);
}

.roles__lock--on {
  color: var(--muted);
}

.roles__foot {
  display: flex;
  justify-content: flex-end;
  margin-top: 16px;
}
</style>
