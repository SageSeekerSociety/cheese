<script setup lang="ts">
// 「新增角色」（#4795）：起一个名字，权限从现有的某个角色复制；项目还没有导师角色时，
// 也可以从导师模板开始。从模板开始可以不起名，它就叫模板的名字。被拒不关窗，理由留在弹窗里。
import type { ProjectRoleRow } from '@/api'

import { computed, ref, watch } from 'vue'

import BaseField from '@/components/base/BaseField.vue'
import AdaptiveDialog from '@/components/common/AdaptiveDialog.vue'
import { t } from '@/i18n'
import { roleName } from '@/lib/projectPermissions'

/** 从导师模板开始那一项的值。 */
const MENTOR_TEMPLATE = 'template:mentor'

const props = defineProps<{
  roles: ProjectRoleRow[]
  saving: boolean
  error: string | null
}>()
const emit = defineEmits<{ create: [name: string | null, from: string] }>()
const open = defineModel<boolean>({ required: true })

const name = ref('')
const from = ref('member')
watch(open, (v) => {
  if (v) {
    name.value = ''
    from.value = 'member'
  }
})

const hasMentor = computed(() => props.roles.some((r) => r.template === 'mentor'))
const items = computed(() => [
  ...props.roles.map((r) => ({ value: r.key, title: roleName(r), template: false, divider: false })),
  ...(hasMentor.value
    ? []
    : [{ value: MENTOR_TEMPLATE, title: t('work.projectSettings.roles.role.mentor'), template: true, divider: true }]),
])
const fromTemplate = computed(() => from.value === MENTOR_TEMPLATE)
const ready = computed(() => fromTemplate.value || name.value.trim() !== '')

function submit() {
  if (!ready.value) return
  emit('create', name.value.trim() || null, from.value)
}
</script>

<template>
  <AdaptiveDialog
    v-model="open"
    :title="t('work.projectSettings.roles.create')"
    :primary-label="t('work.projectSettings.roles.createSubmit')"
    :primary-loading="saving"
    :primary-disabled="!ready"
    :cancel-label="t('work.members.cancel')"
    :max-width="440"
    @primary="submit"
  >
    <div class="role-form">
      <BaseField :label="t('work.projectSettings.roles.nameLabel')" :error="error ?? undefined">
        <v-text-field
          v-model="name"
          autocomplete="off"
          variant="outlined"
          density="comfortable"
          hide-details
          maxlength="40"
          :placeholder="fromTemplate ? t('work.projectSettings.roles.role.mentor') : undefined"
          :aria-label="t('work.projectSettings.roles.nameLabel')"
          @keydown.enter="submit"
        />
      </BaseField>
      <BaseField :label="t('work.projectSettings.roles.copyFrom')">
        <v-select
          v-model="from"
          autocomplete="off"
          :items="items"
          item-title="title"
          item-value="value"
          variant="outlined"
          density="comfortable"
          hide-details
          :aria-label="t('work.projectSettings.roles.copyFrom')"
        >
          <template #item="{ props: itemProps, item }">
            <v-divider v-if="item.raw.divider" class="my-1" />
            <v-list-item v-bind="itemProps">
              <template v-if="item.raw.template" #append>
                <span class="chip-neutral">{{ t('work.projectSettings.roles.template') }}</span>
              </template>
            </v-list-item>
          </template>
        </v-select>
      </BaseField>
    </div>
  </AdaptiveDialog>
</template>

<style scoped>
.role-form {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
</style>
