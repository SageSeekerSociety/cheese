<template>
  <v-sheet flat rounded="lg">
    <v-toolbar :title="t('spaces.course.settings.title')" color="transparent" density="compact"></v-toolbar>

    <!-- 这一页最容易误会的是「关掉一个模块」到底关掉了什么，所以先把它说清。 -->
    <v-alert type="info" variant="tonal" density="comfortable" class="mx-4 mb-4">
      {{ t('spaces.course.settings.help') }}
    </v-alert>

    <!-- 模块开关 -->
    <v-card flat class="mx-4 mb-6">
      <v-card-title class="text-subtitle-1">{{ t('spaces.course.settings.modules') }}</v-card-title>
      <v-card-subtitle>{{ t('spaces.course.settings.modulesHint') }}</v-card-subtitle>
      <v-card-text>
        <div v-for="mod in wiredModules" :key="mod.key" class="d-flex align-start ga-4 py-2">
          <v-switch
            :model-value="moduleState[mod.key]"
            color="primary"
            hide-details
            density="compact"
            @update:model-value="(value) => (moduleState[mod.key] = value === true)"
          ></v-switch>
          <div>
            <div class="text-body-1">{{ t(mod.label) }}</div>
            <div class="text-caption text-medium-emphasis">{{ t(mod.effect) }}</div>
          </div>
        </div>

        <!-- 拨了没反应的开关比没有开关更糟：还没接上界面的模块只在这里交代一句。 -->
        <p class="text-caption text-medium-emphasis mt-4 mb-0">
          {{ t('spaces.course.settings.notWired', { modules: unwiredNames }) }}
        </p>
      </v-card-text>
      <v-card-actions>
        <v-spacer></v-spacer>
        <v-btn color="primary" variant="flat" :loading="savingModules" @click="saveModules">
          {{ t('spaces.course.settings.save') }}
        </v-btn>
      </v-card-actions>
    </v-card>

    <!-- 课程参数 -->
    <v-card flat class="mx-4 mb-6">
      <v-card-title class="text-subtitle-1">{{ t('spaces.course.settings.teaching') }}</v-card-title>
      <v-card-subtitle>{{ t('spaces.course.settings.teachingHint') }}</v-card-subtitle>
      <v-card-text>
        <v-textarea
          v-model="teaching.systemPrompt"
          rows="4"
          auto-grow
          autocomplete="off"
          :label="t('spaces.course.settings.systemPrompt')"
          :hint="t('spaces.course.settings.systemPromptHint')"
          persistent-hint
          class="mb-6"
        ></v-textarea>

        <v-text-field
          v-model="weekInput"
          type="number"
          min="0"
          autocomplete="off"
          :label="t('spaces.course.settings.currentWeek')"
          :hint="t('spaces.course.settings.currentWeekHint')"
          persistent-hint
          class="mb-6"
        ></v-text-field>

        <v-text-field
          v-model="topicsInput"
          autocomplete="off"
          :label="t('spaces.course.settings.allowedTopics')"
          :hint="t('spaces.course.settings.allowedTopicsHint')"
          persistent-hint
          class="mb-6"
        ></v-text-field>

        <v-text-field
          v-model="avoidInput"
          autocomplete="off"
          :label="t('spaces.course.settings.avoidInCode')"
          :hint="t('spaces.course.settings.avoidInCodeHint')"
          persistent-hint
          class="mb-6"
        ></v-text-field>

        <v-text-field
          v-model="materialIdsInput"
          autocomplete="off"
          :label="t('spaces.course.settings.materialIds')"
          :hint="t('spaces.course.settings.materialIdsHint')"
          persistent-hint
          class="mb-6"
        ></v-text-field>

        <v-text-field
          v-model="knowledgeIdsInput"
          autocomplete="off"
          :label="t('spaces.course.settings.knowledgeIds')"
          :hint="t('spaces.course.settings.knowledgeIdsHint')"
          persistent-hint
        ></v-text-field>
      </v-card-text>
      <v-card-actions>
        <v-spacer></v-spacer>
        <v-btn color="primary" variant="flat" :loading="savingTeaching" @click="saveTeaching">
          {{ t('spaces.course.settings.save') }}
        </v-btn>
      </v-card-actions>
    </v-card>
  </v-sheet>
</template>

<script lang="ts" setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute } from 'vue-router'
import { toast } from 'vuetify-sonner'
import { storeToRefs } from 'pinia'

import { COURSE_MODULES, moduleOn } from '@/lib/courseNav'
import { useSpaceStore } from '@/stores/space'

const { t } = useI18n()
const route = useRoute()
const spaceStore = useSpaceStore()
const { currentSpace, categories } = storeToRefs(spaceStore)

const spaceId = Number(route.params.spaceId)

const savingModules = ref(false)
const savingTeaching = ref(false)

/** 只有界面在听的模块才画成开关（拨了没反应的开关比没有开关更糟）。 */
const wiredModules = computed(() => COURSE_MODULES.filter((mod) => mod.wired))
const unwiredNames = computed(() =>
  COURSE_MODULES.filter((mod) => !mod.wired)
    .map((mod) => t(mod.label))
    .join(' / ')
)

/**
 * 开关的本地副本。装的是**整张表**（缺省开着的格子也有一条），因为提交时要整份
 * 替换；服务端那边缺省同样是「开着」，所以装全表与只装例外等价。
 */
const moduleState = ref<Record<string, boolean>>({})

function loadModules() {
  const declared = currentSpace.value?.courseModules ?? {}
  moduleState.value = Object.fromEntries(COURSE_MODULES.map((mod) => [mod.key, moduleOn(declared, mod.key)]))
}

watch(() => currentSpace.value?.courseModules, loadModules, { immediate: true })

const weekInput = ref('')
const topicsInput = ref('')
const avoidInput = ref('')
const materialIdsInput = ref('')
const knowledgeIdsInput = ref('')
const teaching = ref<{ systemPrompt: string }>({ systemPrompt: '' })

/** 课程参数挂在**默认分组**上（一门课一份教学安排，二十道题共享这一份）。 */
const courseCategoryId = computed(() => {
  const declared = currentSpace.value?.defaultCategoryId
  if (declared && categories.value.some((cat) => cat.id === declared && !cat.archivedAt)) {
    return declared
  }
  const active = [...categories.value].filter((cat) => !cat.archivedAt).sort((a, b) => a.displayOrder - b.displayOrder)
  return active[0]?.id ?? null
})

function loadTeaching() {
  const category = categories.value.find((cat) => cat.id === courseCategoryId.value)
  const config = category?.teaching ?? {}
  teaching.value = { systemPrompt: config.systemPrompt ?? '' }
  weekInput.value = config.currentWeek === null || config.currentWeek === undefined ? '' : String(config.currentWeek)
  topicsInput.value = (config.allowedTopics ?? []).join(', ')
  avoidInput.value = (config.avoidInCode ?? []).join(', ')
  materialIdsInput.value = (config.materialIds ?? []).join(', ')
  knowledgeIdsInput.value = (config.knowledgeIds ?? []).join(', ')
}

watch(courseCategoryId, loadTeaching, { immediate: true })

/** 逗号分隔 → 字符串数组（空项丢掉，老师随手多打一个逗号不该变成一条空知识点）。 */
function splitList(value: string): string[] {
  return value
    .split(',')
    .map((item) => item.trim())
    .filter((item) => item.length > 0)
}

function splitIds(value: string): number[] {
  return splitList(value)
    .map((item) => Number(item))
    .filter((id) => Number.isInteger(id) && id > 0)
}

async function saveModules() {
  if (savingModules.value) return
  savingModules.value = true
  try {
    // 整份替换，但**只替换这一屏真的画了的那几格**：没接上界面的模块原样带过去，
    // 别让保存这一屏顺手把别人写过的值抹成缺省。
    const payload: Record<string, boolean> = { ...(currentSpace.value?.courseModules ?? {}) }
    for (const mod of wiredModules.value) {
      payload[mod.key] = moduleState.value[mod.key]
    }
    await spaceStore.updateSpace(spaceId, { courseModules: payload }, false)
    toast.success(t('spaces.course.settings.saved'))
  } catch {
    toast.error(t('spaces.course.settings.saveFailed'))
  } finally {
    savingModules.value = false
  }
}

async function saveTeaching() {
  if (savingTeaching.value) return
  if (!courseCategoryId.value) {
    toast.error(t('spaces.course.settings.noCategory'))
    return
  }
  savingTeaching.value = true
  try {
    const week = weekInput.value.trim()
    await spaceStore.updateCategory(courseCategoryId.value, {
      // 整键替换：省略 teaching 才是「别动它」，所以这里把整份教学配置发上去。
      teaching: {
        systemPrompt: teaching.value.systemPrompt.trim() || null,
        currentWeek: week === '' ? null : Number(week),
        allowedTopics: splitList(topicsInput.value),
        avoidInCode: splitList(avoidInput.value),
        materialIds: splitIds(materialIdsInput.value),
        knowledgeIds: splitIds(knowledgeIdsInput.value),
      },
    })
  } catch {
    toast.error(t('spaces.course.settings.saveFailed'))
  } finally {
    savingTeaching.value = false
  }
}

onMounted(async () => {
  if (!currentSpace.value || currentSpace.value.id !== spaceId) {
    await spaceStore.fetchSpace(spaceId)
  }
  await spaceStore.fetchCategories()
})
</script>
