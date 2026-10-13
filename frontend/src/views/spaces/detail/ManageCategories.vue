<template>
  <ManageCategoriesView
    v-model:open="dialogOpen"
    :categories="categories"
    :default-category-id="currentSpace?.defaultCategoryId ?? null"
    :loading="loadingCategories"
    :saving="saving"
    :failed="failed"
    :error-reason="errorReason"
    :forbidden="forbidden"
    @retry="load"
    @submit="submitCategory"
    @delete="deleteCategory"
    @archive="archiveCategory"
    @unarchive="unarchiveCategory"
    @set-default="setAsDefault"
  />
</template>

<script setup lang="ts">
// 分类管理这一页的容器：读分类、增删改、读失败记下来交出去。画面在
// `ManageCategoriesView.vue`（场景规则见 docs/manual/dev/scenes.md）。
import type { CategoryFormValues } from './ManageCategoriesView.vue'

import { computed, onMounted, ref } from 'vue'
import { storeToRefs } from 'pinia'

import { useSpaceData } from '@/composables/useSpaceData'

import ManageCategoriesView from './ManageCategoriesView.vue'

import { isForbidden, loadFailureReason } from '@/lib/loadFailure'
import { useSpaceStore } from '@/stores/space'

const spaceStore = useSpaceStore()
const spaceData = useSpaceData()
const { currentSpace, categories, loadingCategories } = storeToRefs(spaceStore)

const dialogOpen = ref(false)
const saving = ref(false)

// 读失败和「一个分类都没有」要分得开：`fetchCategories` 从前只弹一条 toast，页面
// 接着画「暂无分类」，两件事长得一模一样。这里把读失败的那个错留住，只要读取失败、
// 且手上一条分类都没有，就用错误态替换整个列表区（有东西可显示时不动它）。
const readError = ref<unknown>(null)
const failed = computed(() => readError.value !== null && categories.value.length === 0)
const errorReason = computed(() => loadFailureReason(readError.value))
const forbidden = computed(() => isForbidden(readError.value))

const load = async () => {
  readError.value = null
  readError.value = await spaceData.fetchCategories(true)
}

const submitCategory = async (payload: { id: number | null; values: CategoryFormValues }) => {
  saving.value = true
  try {
    if (payload.id !== null) {
      await spaceData.updateCategory(payload.id, {
        name: payload.values.name,
        description: payload.values.description,
        displayOrder: payload.values.displayOrder,
      })
    } else {
      await spaceData.createCategory(payload.values.name, payload.values.description, payload.values.displayOrder)
    }
    dialogOpen.value = false
  } catch (error) {
    console.error('提交表单失败:', error)
  } finally {
    saving.value = false
  }
}

const deleteCategory = async (categoryId: number) => {
  try {
    await spaceData.deleteCategory(categoryId)
  } catch (error) {
    console.error('删除分类失败:', error)
  }
}

const archiveCategory = async (categoryId: number) => {
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

onMounted(() => {
  void load()
})
</script>

<style scoped src="@/styles/settings-card.css"></style>
