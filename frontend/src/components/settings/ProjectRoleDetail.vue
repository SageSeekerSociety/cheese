<script setup lang="ts">
// 一个角色自己那一页的权限（#4795）：按组排，每项一个开关。所有人都看得到，只有能修改
// 角色的人能拨。锁定的两项（管人、改角色）拨不动：管理员永远有，别的角色永远没有。
//
// 它只画：拨了哪一项往上报，存不存、存没存上由页面管。
import type { ProjectRoleRow } from '@/api'
import type { PermissionGroup } from '@/lib/projectPermissions'

import { computed } from 'vue'

import { t } from '@/i18n'
import { isLocked, PERMISSION_GROUPS, PERMISSIONS } from '@/lib/projectPermissions'

const props = defineProps<{
  role: ProjectRoleRow
  /** 能不能拨：能修改角色的人才能。 */
  editable: boolean
  /** 项目给 AI 队友起的名字。 */
  agentName: string
  /** 读或存失败时那一句。 */
  error?: string | null
}>()

const emit = defineEmits<{ toggle: [permission: string, granted: boolean] }>()

const groups = computed(() =>
  PERMISSION_GROUPS.map((group: PermissionGroup) => ({
    group,
    items: PERMISSIONS.filter((p) => p.group === group),
  }))
)

function label(permission: string): string {
  return t(`work.projectSettings.roles.perm.${permission}`, { agent: props.agentName })
}

function has(permission: string): boolean {
  return props.role.permissions.includes(permission)
}
</script>

<template>
  <section class="role-detail">
    <v-alert v-if="error" type="error" density="comfortable" class="mb-4">{{ error }}</v-alert>
    <div class="role-detail__box">
      <template v-for="g in groups" :key="g.group">
        <div class="role-detail__group">{{ t(`work.projectSettings.roles.group.${g.group}`) }}</div>
        <div
          v-for="p in g.items"
          :key="p.key"
          class="role-detail__row"
          :class="{ 'role-detail__row--locked': isLocked(p.key) }"
        >
          <span class="role-detail__name">{{ label(p.key) }}</span>
          <v-icon
            v-if="isLocked(p.key)"
            size="16"
            class="role-detail__lock"
            :title="t('work.projectSettings.roles.locked')"
            >mdi-lock-outline</v-icon
          >
          <v-switch
            density="compact"
            color="primary"
            hide-details
            :model-value="has(p.key)"
            :disabled="!editable || isLocked(p.key)"
            :aria-label="label(p.key)"
            @update:model-value="emit('toggle', p.key, Boolean($event))"
          />
        </div>
      </template>
    </div>
  </section>
</template>

<style scoped>
.role-detail__box {
  display: flex;
  flex-direction: column;
  padding-bottom: 8px;
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
}

.role-detail__group {
  padding: 14px 20px 4px;
  font-size: 13px;
  font-weight: 600;
  line-height: var(--lh-13);
  color: var(--muted);
}

.role-detail__row {
  display: flex;
  gap: 8px;
  align-items: center;
  min-height: 44px;
  padding: 0 20px;
  color: var(--text);
}

.role-detail__row--locked {
  color: var(--muted);
}

.role-detail__name {
  flex: 1 1 auto;
  min-width: 0;
}

.role-detail__lock {
  color: var(--faint);
}

.role-detail__row :deep(.v-switch) {
  flex: 0 0 auto;
}
</style>
