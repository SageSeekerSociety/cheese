<template>
  <SettingsToolbar :title="t('spaces.settings.sections.categories')">
    <BaseButton kind="primary" prepend-icon="mdi-plus" @click="openCreateDialog">
      {{ t('spaces.detail.manageCategories.addCategory') }}
    </BaseButton>
  </SettingsToolbar>
  <div class="settings-card">
    <div v-if="loadingCategories" class="pa-4 text-center">
      <v-progress-circular indeterminate color="primary"></v-progress-circular>
    </div>

    <v-list v-else-if="categories.length > 0" class="settings-list" bg-color="transparent">
      <v-list-item
        v-for="category in categories"
        :key="category.id"
        :title="
          category.name +
          (currentSpace?.defaultCategoryId === category.id ? ' ' + t('spaces.detail.manageCategories.isDefault') : '')
        "
        :subtitle="category.description || undefined"
        @contextmenu="rowMenu.open(category.id, $event)"
      >
        <template #prepend>
          <v-icon size="18" class="c-faint">{{
            category.archivedAt ? 'mdi-archive-outline' : 'mdi-shape-outline'
          }}</v-icon>
        </template>
        <template #append>
          <v-tooltip v-if="!category.archivedAt && currentSpace?.defaultCategoryId !== category.id" location="top">
            <template #activator="{ props }">
              <BaseButton
                v-bind="props"
                kind="ghost"
                icon="mdi-star-outline"
                size="sm"
                :aria-label="t('spaces.detail.manageCategories.setAsDefault')"
                @click="setAsDefault(category.id)"
              />
            </template>
            {{ t('spaces.detail.manageCategories.setAsDefault') }}
          </v-tooltip>

          <BaseButton
            v-if="!category.archivedAt"
            kind="ghost"
            icon="mdi-pencil-outline"
            size="sm"
            :aria-label="t('spaces.detail.manageCategories.updateCategory')"
            @click="openEditDialog(category)"
          />

          <AdaptiveMenu v-bind="rowMenu.bind(category.id)" :actions="categoryActions(category)" :title="category.name">
            <template #activator="{ props }">
              <BaseButton
                v-bind="props"
                kind="ghost"
                icon="mdi-dots-horizontal"
                size="sm"
                :aria-label="t('navigation.shell.more')"
              />
            </template>
          </AdaptiveMenu>
        </template>
      </v-list-item>
    </v-list>

    <p v-else class="settings-empty">{{ t('spaces.detail.manageCategories.noCategories') }}</p>

    <!-- Create/edit category form: dialog on desktop, full page on phones (AdaptiveDialog). -->
    <AdaptiveDialog
      v-model="dialogOpen"
      :title="
        editingCategory
          ? t('spaces.detail.manageCategories.updateCategory')
          : t('spaces.detail.manageCategories.createCategory')
      "
      :primary-label="t('spaces.detail.manageCategories.confirm')"
      :primary-loading="isSubmitting"
      @primary="submitForm"
    >
      <v-form @submit.prevent="submitForm">
        <v-text-field
          v-model="formData.name"
          autocomplete="off"
          :label="t('spaces.detail.manageCategories.name')"
          required
          v-bind="nameProps"
        ></v-text-field>

        <v-textarea
          v-model="formData.description"
          autocomplete="off"
          :label="t('spaces.detail.manageCategories.description')"
          v-bind="descriptionProps"
          rows="3"
          auto-grow
        ></v-textarea>

        <v-text-field
          v-model.number="formData.displayOrder"
          :label="t('spaces.detail.manageCategories.displayOrder')"
          type="number"
          min="0"
          :hint="t('spaces.detail.manageCategories.displayOrderHint')"
          v-bind="displayOrderProps"
        ></v-text-field>
      </v-form>
    </AdaptiveDialog>
  </div>
</template>

<script setup lang="ts">
import type { MenuAction } from '@/components/common/menuAction'

import { onMounted, reactive, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { toTypedSchema } from '@vee-validate/zod'
import { storeToRefs } from 'pinia'
import { useForm } from 'vee-validate'
import { z } from 'zod'

import { vuetifyConfig } from '@/utils/form'

import { useRowMenu } from '@/composables/useRowMenu'
import { useSpaceData } from '@/composables/useSpaceData'

import BaseButton from '@/components/base/BaseButton.vue'
import AdaptiveDialog from '@/components/common/AdaptiveDialog.vue'
import AdaptiveMenu from '@/components/common/AdaptiveMenu.vue'
import SettingsToolbar from '@/components/spaces/SettingsToolbar.vue'
import { useDialog } from '@/plugins/dialog'
import { useSpaceStore } from '@/stores/space'
import { SpaceCategory } from '@/types'

const spaceStore = useSpaceStore()
const spaceData = useSpaceData()
const { currentSpace, categories, loadingCategories } = storeToRefs(spaceStore)
const { t } = useI18n()
const { confirm } = useDialog()
const rowMenu = useRowMenu<number>()

// 表单相关
const dialogOpen = ref(false)
const editingCategory = ref<SpaceCategory | null>(null)

/** 一个分类那一行的 ⋯：归档了的只剩「恢复」和「删除」。 */
function categoryActions(category: SpaceCategory): MenuAction[] {
  return [
    ...(category.archivedAt
      ? [
          {
            key: 'unarchive',
            label: t('spaces.detail.manageCategories.unarchiveCategory'),
            icon: 'mdi-archive-arrow-up-outline',
            onSelect: () => void unarchiveCategory(category.id),
          },
        ]
      : [
          {
            key: 'archive',
            label: t('spaces.detail.manageCategories.archiveCategory'),
            icon: 'mdi-archive-arrow-down-outline',
            onSelect: () => void archiveCategory(category.id),
          },
        ]),
    {
      key: 'delete',
      label: t('spaces.detail.manageCategories.deleteCategory'),
      icon: 'mdi-delete-outline',
      danger: true,
      onSelect: () => void deleteCategory(category.id),
    },
  ]
}

// 表单校验
const { handleSubmit, defineField, isSubmitting, resetForm } = useForm({
  validationSchema: toTypedSchema(
    z.object({
      name: z
        .string()
        .min(1, { message: 'spaces.detail.manageCategories.categoryNameRequired' })
        .max(32, { message: 'spaces.detail.manageCategories.categoryNameMaxLength' }),
      description: z
        .string()
        .max(255, { message: 'spaces.detail.manageCategories.descriptionMaxLength' })
        .nullable()
        .optional(),
      displayOrder: z.number().int().nonnegative().optional(),
    })
  ),
})

const [name, nameProps] = defineField('name', vuetifyConfig)
const [description, descriptionProps] = defineField('description', vuetifyConfig)
const [displayOrder, displayOrderProps] = defineField('displayOrder', vuetifyConfig)

const formData = reactive({
  name,
  description,
  displayOrder: 0,
})

// 生命周期钩子
onMounted(async () => {
  await spaceData.fetchCategories(true)
})

// 方法
const openCreateDialog = () => {
  resetForm({
    values: {
      name: '',
      description: '',
      displayOrder: 0,
    },
  })
  editingCategory.value = null
  dialogOpen.value = true
}

const openEditDialog = (category: SpaceCategory) => {
  resetForm({
    values: {
      name: category.name,
      description: category.description,
      displayOrder: category.displayOrder,
    },
  })
  editingCategory.value = category
  dialogOpen.value = true
}

const submitForm = handleSubmit(async (values) => {
  try {
    if (editingCategory.value) {
      await spaceData.updateCategory(editingCategory.value.id, {
        name: values.name,
        description: values.description,
        displayOrder: values.displayOrder,
      })
    } else {
      await spaceData.createCategory(values.name, values.description, values.displayOrder)
    }
    dialogOpen.value = false
  } catch (error) {
    console.error('提交表单失败:', error)
  }
})

const deleteCategory = async (categoryId: number) => {
  const confirmed = await confirm(t('spaces.detail.manageCategories.confirmDelete')).wait()
  if (!confirmed) return

  try {
    await spaceData.deleteCategory(categoryId)
  } catch (error) {
    console.error('删除分类失败:', error)
  }
}

const archiveCategory = async (categoryId: number) => {
  const confirmed = await confirm(t('spaces.detail.manageCategories.confirmArchive')).wait()
  if (!confirmed) return

  try {
    await spaceData.archiveCategory(categoryId)
  } catch (error) {
    console.error('归档分类失败:', error)
  }
}

const unarchiveCategory = async (categoryId: number) => {
  try {
    await spaceData.unarchiveCategory(categoryId)
  } catch (error) {
    console.error('恢复分类失败:', error)
  }
}

const setAsDefault = async (categoryId: number) => {
  try {
    await spaceData.setDefaultCategory(categoryId)
  } catch (error) {
    console.error('设置默认分类失败:', error)
  }
}
</script>

<style scoped src="@/styles/settings-card.css"></style>
