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

    <p v-else class="settings-empty">{{ t('spaces.domainGroups.noGroups') }}</p>

    <!-- 创建/编辑对话框 -->
    <v-dialog v-model="dialogOpen" max-width="520">
      <v-card>
        <v-card-title>
          {{ editingGroup ? t('spaces.domainGroups.editGroup') : t('spaces.domainGroups.createGroup') }}
        </v-card-title>
        <v-card-text>
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
        </v-card-text>
        <v-card-actions>
          <v-spacer></v-spacer>
          <BaseButton kind="ghost" @click="dialogOpen = false">{{
            t('spaces.detail.manageCategories.cancel')
          }}</BaseButton>
          <BaseButton kind="primary" :loading="isSubmitting" @click="submitForm">
            {{ t('spaces.detail.manageCategories.confirm') }}
          </BaseButton>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </div>
</template>

<script setup lang="ts">
import type { DomainGroup } from '@/types'

import { computed, onMounted, reactive, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute } from 'vue-router'
import { toTypedSchema } from '@vee-validate/zod'
import { storeToRefs } from 'pinia'
import { useForm } from 'vee-validate'
import { z } from 'zod'

import { vuetifyConfig } from '@/utils/form'

import { useSpaceData } from '@/composables/useSpaceData'

import BaseButton from '@/components/base/BaseButton.vue'
import SettingsToolbar from '@/components/spaces/SettingsToolbar.vue'
import { SpacesApi } from '@/network/api/spaces'
import { useDialog } from '@/plugins/dialog'
import { useSpaceStore } from '@/stores/space'

const { t } = useI18n()
const route = useRoute()
const { confirm } = useDialog()

const spaceId = Number(route.params.spaceId)

const spaceStore = useSpaceStore()
const spaceData = useSpaceData()
const { domainGroups } = storeToRefs(spaceStore)

const loading = ref(false)
const dialogOpen = ref(false)
const editingGroup = ref<DomainGroup | null>(null)
const formRef = ref()

const domainSchema = z
  .string()
  .min(1, { message: t('spaces.domainGroups.invalidDomain') })
  .refine((v) => !v.includes('@') && !v.includes(' ') && v.includes('.'), {
    message: t('spaces.domainGroups.invalidDomain'),
  })

const { handleSubmit, defineField, isSubmitting, resetForm } = useForm({
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

async function fetchDomainGroups() {
  loading.value = true
  try {
    await spaceData.fetchDomainGroups(spaceId)
  } catch {
    // handled silently
  } finally {
    loading.value = false
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

const submitForm = handleSubmit(async (values) => {
  try {
    const payload = {
      name: values.name,
      description: values.description || null,
      domains: values.domains.filter((d) => d.trim() !== ''),
    }

    if (editingGroup.value) {
      await SpacesApi.updateDomainGroup(spaceId, editingGroup.value.id, payload)
    } else {
      await SpacesApi.createDomainGroup(spaceId, payload)
    }
    dialogOpen.value = false
    await fetchDomainGroups()
  } catch {
    // handled by API layer
  }
})

async function deleteGroup(group: DomainGroup) {
  const confirmed = await confirm(t('spaces.domainGroups.confirmDelete', { name: group.name })).wait()
  if (!confirmed) return

  try {
    await SpacesApi.deleteDomainGroup(spaceId, group.id)
    await fetchDomainGroups()
  } catch {
    // handled by API layer
  }
}

onMounted(() => {
  fetchDomainGroups()
})
</script>

<style scoped src="@/styles/settings-card.css"></style>
