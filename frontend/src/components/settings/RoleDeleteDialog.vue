<script setup lang="ts">
// 删除项目自己的一个角色（#4795）。还有人在用它时，先选他们改为什么角色，选好才能删；
// 没人用就直接删。被拒不关窗，理由留在弹窗里。
import type { ProjectRoleRow } from '@/api'

import { computed, ref, watch } from 'vue'

import BaseField from '@/components/base/BaseField.vue'
import AdaptiveDialog from '@/components/common/AdaptiveDialog.vue'
import { t } from '@/i18n'
import { roleName } from '@/lib/projectPermissions'

const props = defineProps<{
  role: ProjectRoleRow
  roles: ProjectRoleRow[]
  /** 我能不能把人设为管理员：只有所有者能。 */
  makesAdmins: boolean
  saving: boolean
  error: string | null
}>()
const emit = defineEmits<{ delete: [moveTo: string | undefined] }>()
const open = defineModel<boolean>({ required: true })

const moveTo = ref('member')
watch(open, (v) => {
  if (v) moveTo.value = 'member'
})

const targets = computed(() =>
  props.roles
    .filter((r) => r.key !== props.role.key && (r.builtin !== 'admin' || props.makesAdmins))
    .map((r) => ({ value: r.key, title: roleName(r) }))
)
</script>

<template>
  <AdaptiveDialog
    v-model="open"
    :title="t('work.projectSettings.roles.deleteTitle', { name: roleName(role) })"
    :primary-label="t('work.projectSettings.roles.deleteSubmit')"
    :primary-loading="saving"
    primary-danger
    :cancel-label="t('work.members.cancel')"
    :max-width="440"
    @primary="emit('delete', role.holders ? moveTo : undefined)"
  >
    <BaseField
      v-if="role.holders"
      :label="t('work.projectSettings.roles.moveTo', { n: role.holders })"
      :error="error ?? undefined"
    >
      <v-select
        v-model="moveTo"
        autocomplete="off"
        :items="targets"
        item-title="title"
        item-value="value"
        variant="outlined"
        density="comfortable"
        hide-details
        :aria-label="t('work.projectSettings.roles.moveTo', { n: role.holders })"
      />
    </BaseField>
    <v-alert v-else-if="error" type="error" density="comfortable">{{ error }}</v-alert>
  </AdaptiveDialog>
</template>
