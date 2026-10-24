<script setup lang="ts">
// 「角色和权限」那一栏的角色清单（#4795）：一行一个角色，写着有几个人，点开是它自己那一页。
// 管理员、成员排在最上面，项目自己的角色在一道缝下面，按建的先后。
//
// 它只画：点了哪一行往上报，去哪一页由页面管。
import type { ProjectRoleRow } from '@/api'

import { computed } from 'vue'

import { t } from '@/i18n'
import { roleName } from '@/lib/projectPermissions'

const props = defineProps<{ roles: ProjectRoleRow[] }>()

const emit = defineEmits<{ open: [key: string] }>()

const builtin = computed(() => props.roles.filter((r) => r.builtin))
const own = computed(() => props.roles.filter((r) => !r.builtin))
</script>

<template>
  <nav class="role-list" :aria-label="t('work.projectSettings.sections.roles')">
    <button v-for="r in builtin" :key="r.key" type="button" class="role-list__row" @click="emit('open', r.key)">
      <span class="role-list__name">{{ roleName(r) }}</span>
      <span class="role-list__count">{{ t('work.projectSettings.roles.holders', { n: r.holders }) }}</span>
      <v-icon size="18" class="role-list__chevron">mdi-chevron-right</v-icon>
    </button>
    <template v-if="own.length">
      <div class="role-list__gap" />
      <button v-for="r in own" :key="r.key" type="button" class="role-list__row" @click="emit('open', r.key)">
        <span class="role-list__name">{{ roleName(r) }}</span>
        <span class="role-list__count">{{ t('work.projectSettings.roles.holders', { n: r.holders }) }}</span>
        <v-icon size="18" class="role-list__chevron">mdi-chevron-right</v-icon>
      </button>
    </template>
  </nav>
</template>

<style scoped>
.role-list {
  display: flex;
  flex-direction: column;
  overflow: hidden;
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
}

.role-list__row {
  display: flex;
  gap: 12px;
  align-items: center;
  min-height: 48px;
  padding: 0 16px 0 20px;
  font: inherit;
  color: var(--ink);
  text-align: left;
  cursor: pointer;
  background: none;
  border: 0;
  transition: background-color var(--dur-quick) var(--ease-standard);
}

.role-list__row + .role-list__row {
  border-top: 1px solid var(--line);
}

.role-list__row:hover {
  background: var(--fill);
}

.role-list__name {
  flex: 1 1 auto;
  min-width: 0;
  overflow-wrap: anywhere;
}

.role-list__count {
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--muted);
}

.role-list__chevron {
  color: var(--faint);
}

.role-list__gap {
  height: 8px;
  background: var(--canvas);
  border-top: 1px solid var(--line);
  border-bottom: 1px solid var(--line);
}
</style>
