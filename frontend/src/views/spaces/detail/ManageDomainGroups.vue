<template>
  <v-sheet flat rounded="lg">
    <v-toolbar :title="t('spaces.domainGroups.title')" color="transparent" density="compact">
      <template #append>
        <v-btn color="primary" prepend-icon="mdi-plus" @click="openCreateDialog">
          {{ t('spaces.domainGroups.createGroup') }}
        </v-btn>
      </template>
    </v-toolbar>

    <div v-if="loading" class="pa-4 text-center">
      <v-progress-circular indeterminate color="primary"></v-progress-circular>
    </div>

    <v-list v-else-if="domainGroups.length > 0" rounded="lg">
      <v-list-item
        v-for="group in domainGroups"
        :key="group.id"
        :title="group.name"
        :subtitle="group.description || undefined"
      >
        <template #prepend>
          <v-avatar color="primary-lighten-5" size="42" class="me-3">
            <v-icon color="primary">mdi-web</v-icon>
          </v-avatar>
        </template>
        <template #append>
          <v-btn icon="mdi-pencil" variant="text" @click="openEditDialog(group)"></v-btn>
          <v-btn icon="mdi-delete" variant="text" color="error" @click="deleteGroup(group)"></v-btn>
        </template>
      </v-list-item>
    </v-list>

    <v-sheet v-else class="pa-4 text-center">
      <p class="text-medium-emphasis">{{ t('spaces.domainGroups.noGroups') }}</p>
    </v-sheet>

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
                <v-btn
                  icon="mdi-close"
                  variant="text"
                  size="small"
                  :disabled="domainList.length <= 1"
                  @click="removeDomain(index)"
                ></v-btn>
              </v-col>
            </v-row>
            <p v-if="domainAllProps['error-messages']?.length" class="text-error text-caption mt-1">
              {{ domainAllProps['error-messages'][0] }}
            </p>
            <v-btn variant="text" color="primary" prepend-icon="mdi-plus" size="small" @click="addDomain">
              {{ t('spaces.domainGroups.addDomain') }}
            </v-btn>
          </v-form>
        </v-card-text>
        <v-card-actions>
          <v-spacer></v-spacer>
          <v-btn variant="text" @click="dialogOpen = false">{{ t('spaces.detail.manageCategories.cancel') }}</v-btn>
          <v-btn color="primary" :loading="isSubmitting" @click="submitForm">
            {{ t('spaces.detail.manageCategories.confirm') }}
          </v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </v-sheet>
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

import { SpacesApi } from '@/network/api/spaces'
import { useDialog } from '@/plugins/dialog'
import { useSpaceStore } from '@/stores/space'

const { t } = useI18n()
const route = useRoute()
const { confirm } = useDialog()

const spaceId = Number(route.params.spaceId)

const spaceStore = useSpaceStore()
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
    await spaceStore.fetchDomainGroups(spaceId)
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

<style scoped>
.v-list-item {
  border-bottom: 1px solid rgba(var(--v-border-color), 0.1);
}
.v-list-item:last-child {
  border-bottom: none;
}
</style>
