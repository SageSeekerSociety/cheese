<template>
  <SettingsToolbar>
    <BaseButton kind="primary" prepend-icon="mdi-plus" @click="openCreateDialog">
      {{ t('spaces.domainGroups.createGroup') }}
    </BaseButton>
  </SettingsToolbar>
  <div class="settings-card">
    <div v-if="loading" class="pa-4 text-center">
      <v-progress-circular indeterminate color="primary"></v-progress-circular>
    </div>

    <v-list v-else-if="domainGroups.length > 0" class="settings-list" bg-color="transparent">
      <v-list-item
        v-for="group in domainGroups"
        :key="group.id"
        :title="group.name"
        :subtitle="group.description || undefined"
      >
        <template #prepend>
          <v-icon size="18" class="c-faint">mdi-web</v-icon>
        </template>
        <template #append>
          <BaseButton
            kind="ghost"
            icon="mdi-pencil-outline"
            size="sm"
            :aria-label="t('spaces.domainGroups.editGroup')"
            @click="openEditDialog(group)"
          />
          <BaseButton
            kind="ghost"
            icon="mdi-delete-outline"
            size="sm"
            :aria-label="t('spaces.domainGroups.deleteGroup')"
            @click="deleteGroup(group)"
          />
        </template>
      </v-list-item>
    </v-list>

    <BaseEmptyState v-else size="inline" class="settings-empty" :title="t('spaces.domainGroups.noGroups')" />

    <!-- Create / edit dialog -->
    <AdaptiveDialog
      v-model="dialogOpen"
      :title="editingGroup ? t('spaces.domainGroups.editGroup') : t('spaces.domainGroups.createGroup')"
      :primary-label="t('spaces.detail.manageCategories.confirm')"
      :cancel-label="t('spaces.detail.manageCategories.cancel')"
      :primary-loading="saving"
      @primary="submitForm"
    >
      <v-form ref="formRef" @submit.prevent="submitForm">
        <v-text-field
          v-model="formData.name"
          autocomplete="off"
          :label="t('spaces.domainGroups.groupName')"
          required
          v-bind="nameProps"
        ></v-text-field>

        <v-textarea
          v-model="formData.description"
          autocomplete="off"
          :label="t('spaces.domainGroups.groupDescription')"
          rows="2"
          auto-grow
        ></v-textarea>

        <div class="text-subtitle-2 mb-2">{{ t('spaces.domainGroups.domains') }}</div>
        <v-row v-for="(_, index) in domainList" :key="index" align="center" class="mb-1">
          <v-col cols="10">
            <v-text-field
              autocomplete="off"
              :model-value="domainList[index]"
              :placeholder="t('spaces.domainGroups.domainPlaceholder')"
              density="compact"
              hide-details="auto"
              @update:model-value="(val: string) => updateDomain(index, val)"
            ></v-text-field>
          </v-col>
          <v-col cols="2">
            <BaseButton
              kind="ghost"
              icon="mdi-close"
              size="sm"
              :aria-label="t('spaces.materials.remove')"
              :disabled="domainList.length <= 1"
              @click="removeDomain(index)"
            />
          </v-col>
        </v-row>
        <p v-if="domainAllProps['error-messages']?.length" class="text-error text-caption mt-1">
          {{ domainAllProps['error-messages'][0] }}
        </p>
        <BaseButton kind="secondary" prepend-icon="mdi-plus" size="sm" @click="addDomain">
          {{ t('spaces.domainGroups.addDomain') }}
        </BaseButton>
      </v-form>
    </AdaptiveDialog>
  </div>
</template>

<script setup lang="ts">
// 域名组管理这一屏的画面：一列域名组、新建/编辑那个表单。读列表、真增删改、确认框之外
// 的取数都归容器 `ManageDomainGroups.vue`（场景规则见 docs/manual/dev/scenes.md）。
import type { DomainGroup } from '@/types'

import { computed, reactive, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { toTypedSchema } from '@vee-validate/zod'
import { useForm } from 'vee-validate'
import { z } from 'zod'

import { vuetifyConfig } from '@/utils/form'

import BaseButton from '@/components/base/BaseButton.vue'
import BaseEmptyState from '@/components/base/BaseEmptyState.vue'
import AdaptiveDialog from '@/components/common/AdaptiveDialog.vue'
import SettingsToolbar from '@/components/spaces/SettingsToolbar.vue'
import { useDialog } from '@/plugins/dialog'

export interface DomainGroupValues {
  name: string
  description?: string | null
  domains: string[]
}

defineProps<{
  domainGroups: DomainGroup[]
  loading: boolean
  saving: boolean
}>()

const emit = defineEmits<{
  submit: [payload: { id: number | null; values: DomainGroupValues }]
  delete: [id: number]
}>()

const dialogOpen = defineModel<boolean>('open', { required: true })

const { t } = useI18n()
const { confirm } = useDialog()

const editingGroup = ref<DomainGroup | null>(null)
const formRef = ref()

const domainSchema = z
  .string()
  .min(1, { message: t('spaces.domainGroups.invalidDomain') })
  .refine((v) => !v.includes('@') && !v.includes(' ') && v.includes('.'), {
    message: t('spaces.domainGroups.invalidDomain'),
  })

const { handleSubmit, defineField, resetForm } = useForm({
  validationSchema: toTypedSchema(
    z.object({
      name: z.string().min(1, { message: t('spaces.domainGroups.nameRequired') }),
      description: z.string().nullable().optional(),
      domains: z.array(domainSchema).min(1, { message: t('spaces.domainGroups.invalidDomain') }),
    })
  ),
})

const [name, nameProps] = defineField('name', vuetifyConfig)
const [domains, domainAllProps] = defineField('domains', vuetifyConfig)

// defineField types every field as possibly-undefined (a form can be rendered
// before resetForm supplies initialValues). Every write below replaces the whole
// array, so reads are what need narrowing — do it once here instead of at each
// of the six use sites, and keep writes going through `domains` so vee-validate
// still sees them.
const domainList = computed(() => domains.value ?? [])

const formData = reactive({
  name,
  description: '' as string | null,
})

function updateDomain(index: number, value: string) {
  domains.value = domainList.value.map((d, i) => (i === index ? value : d))
}

function addDomain() {
  domains.value = [...domainList.value, '']
}

function removeDomain(index: number) {
  if (domainList.value.length > 1) {
    domains.value = domainList.value.filter((_, i) => i !== index)
  }
}

function openCreateDialog() {
  resetForm({
    values: {
      name: '',
      description: '',
      domains: [''],
    },
  })
  editingGroup.value = null
  dialogOpen.value = true
}

function openEditDialog(group: DomainGroup) {
  resetForm({
    values: {
      name: group.name,
      description: group.description,
      domains: group.domains.length > 0 ? [...group.domains] : [''],
    },
  })
  editingGroup.value = group
  dialogOpen.value = true
}

const submitForm = handleSubmit((values) => {
  emit('submit', {
    id: editingGroup.value?.id ?? null,
    values: values as DomainGroupValues,
  })
})

async function deleteGroup(group: DomainGroup) {
  const confirmed = await confirm(t('spaces.domainGroups.confirmDelete', { name: group.name }), {
    confirmLabel: t('spaces.domainGroups.deleteGroup'),
    danger: true,
  }).wait()
  if (!confirmed) return

  emit('delete', group.id)
}
</script>

<style scoped src="@/styles/settings-card.css"></style>
