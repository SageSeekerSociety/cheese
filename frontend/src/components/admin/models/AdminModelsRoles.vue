<script setup lang="ts">
import type { PlatformRole } from '@/api/admin/gateway'
import type { ModelRow } from '@/lib/adminModels'

import { useI18n } from 'vue-i18n'

import BaseLoadError from '@/components/base/BaseLoadError.vue'
import { roleLabelKey } from '@/lib/platformRoles'

// 平台模型角色那一段：平台自己挑模型的那几件事（默认模型、语音输入、话题命名……）各跑在
// 哪个网关模型上，每个一个下拉。改了立即生效，不用重新部署；没人指过的角色读部署的设置项，
// 下拉的第一项就是回到它。
//
// 下拉里只列这个角色能用的模型：要提供给项目的角色（默认模型、分身默认）只列上架的；
// 别的只列在用、有价的（语音输入跑在一个不上架的模型上）。判据与服务端相同，服务端说了算。
const props = defineProps<{
  roles: PlatformRole[] | null
  models: ModelRow[]
  loading: boolean
  error: string | null
  /** 正在写的那个角色。 */
  busy: string | null
}>()

const emit = defineEmits<{ retry: []; set: [role: string, model: string | null] }>()

const { t } = useI18n()

/** 回到部署设置项的那一项。下拉的值不能是 null，所以给它一个不会是模型名的哨兵。 */
const FALLBACK = '\u0000fallback'

function items(role: PlatformRole) {
  const fits = props.models.filter((m) => (role.offered_only ? m.offered : !m.blocked && m.priced))
  const names = fits.map((m) => ({ value: m.name, title: m.label === m.name ? m.name : `${m.label} (${m.name})` }))
  const fallbackTitle = role.fallback
    ? t('models.roles.fallback', { model: role.fallback })
    : t('models.roles.fallbackNone')
  return [{ value: FALLBACK, title: fallbackTitle }, ...names]
}

function current(role: PlatformRole): string {
  return role.chosen && role.model ? role.model : FALLBACK
}

function pick(role: PlatformRole, value: string) {
  if (value === current(role)) return
  emit('set', role.role, value === FALLBACK ? null : value)
}
</script>

<template>
  <div class="amr">
    <BaseLoadError
      v-if="props.error"
      :title="t('models.roles.loadFailed')"
      :error="props.error"
      :retry-label="t('models.page.retry')"
      @retry="emit('retry')"
    />
    <p class="amr__hint t-meta-read">{{ t('models.roles.hint') }}</p>
    <div v-for="role in props.roles ?? []" :key="role.role" class="amr__row">
      <div class="amr__label">
        <span class="t-body">{{ t(roleLabelKey(role.role)) }}</span>
        <span class="amr__now t-meta-read">{{
          role.model ? t('models.roles.now', { model: role.model }) : t('models.roles.unset')
        }}</span>
      </div>
      <v-select
        autocomplete="off"
        :model-value="current(role)"
        :items="items(role)"
        item-title="title"
        item-value="value"
        variant="outlined"
        density="compact"
        :label="t(roleLabelKey(role.role))"
        :disabled="props.loading || props.busy !== null"
        :loading="props.busy === role.role"
        hide-details
        @update:model-value="pick(role, $event)"
      />
    </div>
  </div>
</template>

<style scoped>
.amr {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.amr__hint {
  margin: 0;
  color: var(--muted);
}

.amr__row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1.4fr);
  align-items: center;
  gap: 12px;
}

.amr__label {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.amr__now {
  overflow: hidden;
  color: var(--muted);
  text-overflow: ellipsis;
  white-space: nowrap;
}

@container admin (max-width: 719.98px) {
  .amr__row {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
