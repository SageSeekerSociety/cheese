<script setup lang="ts">
// 「参考资料」这一格（#944）：从这块板的资料库里勾，不再手填编号。
//
// 摆法照设计稿：勾中的课件做成 chip 摆在外面（点 chip 上的 ✕ 去掉），点这一栏才摊开
// 资料库清单。理由是人多半只扫一眼「这道题带了哪几份」，摊开的整份清单天天挡在那里；
// 展开之后还能顺手跳到「资料库」页传新文件。
//
// 三条规矩落在这里，两处表单（空间设置里的默认、发题页里的覆盖）共用同一份：
//
// - **「仅管理员」那一档不进清单。** 学生和他们的 AI 读不到的东西，不该出现在老师的
//   候选里。服务端按权限给清单（管理员两档都拿得到），过滤只在这一处做，免得两页各
//   写一遍慢慢走样。
// - **已经不在清单里的编号不悄悄丢掉。** 素材删了、或者事后被改成「仅管理员」，指导
//   里那个引用还在（读的时候后端本来就把它过滤掉了），这里把它单列出来让人自己去掉。
//   静默删掉等于替人改一份他没动过的配置 —— 保存的时候他会以为只是换了批课件。
// - **读不出清单时不判失效。** 一次网络失败不该让人删掉有效的引用。
import type { SpaceMaterial, SpaceMaterialsState } from '@/types'

import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import BaseButton from '@/components/base/BaseButton.vue'
import BaseEmptyState from '@/components/base/BaseEmptyState.vue'

const props = defineProps<{
  /** 现在勾着的编号。顺序照旧，新勾的接在后面 —— 勾一下不该把已有的顺序洗一遍。 */
  modelValue: number[]
  /** 这块板资料库的清单，原样递进来（含「仅管理员」档，过滤在这里做）。 */
  materials: SpaceMaterial[]
  /** 清单拿不拿得到。`error` 时不判失效，也不摆候选 —— 读不出来不等于东西没了。 */
  state?: SpaceMaterialsState
  /** 「上传到资料库」那条跳到哪儿（就是资料库那一页的地址）。不给就不摆这一条。 */
  libraryTo?: string
}>()

const emit = defineEmits<{ 'update:modelValue': [number[]] }>()

const { t } = useI18n()

/** 清单摊开没有。默认收起：多数题目一份课件都不带，摊着只是挡路。 */
const open = ref(false)

// 换一块板（或者重取一次）时收回去。`open` 挂在这个组件上，而取数那段时间只是
// 换了里面的分支、组件本身没重建，不收的话新清单会带着上一次的展开状态回来 ——
// 与「默认收起」这条规矩对不上。
watch(
  () => props.state,
  (next) => {
    if ((next ?? 'ready') !== 'ready') open.value = false
  }
)

/** 选得到的：只有「所有成员」那一档。 */
const selectable = computed(() => props.materials.filter((item) => item.visibility === 'members'))

/** 摆在外面的 chip：照 `modelValue` 的顺序取，勾的顺序就是人看到的顺序。 */
const chosen = computed(() =>
  props.modelValue
    .map((id) => selectable.value.find((item) => item.id === id))
    .filter((item): item is SpaceMaterial => item !== undefined)
)

/**
 * 挂着别人的编号：**选不到**的那些（删了、或者事后被改成「仅管理员」）。
 *
 * 判据拿的是 `selectable` 而不是整份 `materials` —— 拿后者的话，一份被改成「仅管理
 * 员」的素材两头都不在：既不在候选里，也不算失效，编号就留在配置里看不见、去不掉，
 * 保存时静默写回去。这正是这一段要防的。
 *
 * 不用在这里挡「清单没读出来」：这一段只在 `state` 是 `ready` 的那一支里摆（错误
 * 和读取中各摆各的话），所以能走到这里就是读到了。
 */
const dangling = computed(() => {
  const known = new Set(selectable.value.map((item) => item.id))
  return props.modelValue.filter((id) => !known.has(id))
})

function toggle(id: number, on: boolean) {
  if (on) {
    if (props.modelValue.includes(id)) return
    emit('update:modelValue', [...props.modelValue, id])
    return
  }
  emit(
    'update:modelValue',
    props.modelValue.filter((current) => current !== id)
  )
}
</script>

<template>
  <div class="material-picker">
    <div class="material-picker__label">{{ t('spaces.teaching.fields.materials') }}</div>
    <p class="material-picker__hint">{{ t('spaces.teaching.fields.materialsHint') }}</p>

    <p v-if="(state ?? 'ready') === 'loading'" class="material-picker__empty" data-testid="teaching-materials-loading">
      {{ t('spaces.teaching.fields.materialsLoading') }}
    </p>

    <p v-else-if="state === 'error'" class="material-picker__empty" data-testid="teaching-materials-error">
      {{ t('spaces.teaching.fields.materialsError') }}
    </p>

    <template v-else>
      <!-- 勾中的摆在这一条里；点它摊开清单。整条可以点，所以 chip 上的 ✕ 要 stop。 -->
      <div
        class="material-picker__field"
        data-testid="teaching-materials-toggle"
        role="button"
        tabindex="0"
        :aria-expanded="open"
        :aria-label="t(open ? 'spaces.teaching.fields.materialsCollapse' : 'spaces.teaching.fields.materialsExpand')"
        @click="open = !open"
        @keydown.enter.prevent="open = !open"
        @keydown.space.prevent="open = !open"
      >
        <v-chip
          v-for="item in chosen"
          :key="item.id"
          size="small"
          class="material-picker__chip"
          data-testid="teaching-material-chip"
          @click.stop
        >
          {{ item.name }}
          <button
            type="button"
            class="material-picker__chip-x"
            :data-testid="`teaching-material-chip-remove-${item.id}`"
            :aria-label="t('spaces.teaching.fields.materialsRemove', { name: item.name })"
            @click.stop="toggle(item.id, false)"
            @keydown.stop
          >
            ✕
          </button>
        </v-chip>
        <span v-if="chosen.length === 0" class="material-picker__placeholder">
          {{ t('spaces.teaching.fields.materialsNone') }}
        </span>
        <span class="material-picker__caret">
          <v-icon :icon="open ? 'mdi-chevron-up' : 'mdi-chevron-down'" size="18" />
        </span>
      </div>

      <div v-if="open" class="material-picker__menu">
        <div class="material-picker__subhead">{{ t('spaces.teaching.fields.materialsLibrary') }}</div>
        <BaseEmptyState
          v-if="selectable.length === 0"
          size="inline"
          class="material-picker__empty"
          data-testid="teaching-materials-empty"
          :title="t('spaces.teaching.fields.materialsEmpty')"
        />
        <div v-else class="material-picker__list" data-testid="teaching-materials">
          <v-checkbox
            v-for="item in selectable"
            :key="item.id"
            :model-value="modelValue.includes(item.id)"
            :label="item.name"
            density="compact"
            hide-details
            @update:model-value="(on: unknown) => toggle(item.id, on === true)"
          />
        </div>

        <template v-if="libraryTo">
          <v-divider class="material-picker__sep" />
          <BaseButton
            kind="ghost"
            size="sm"
            class="material-picker__upload"
            data-testid="teaching-materials-upload"
            :to="libraryTo"
          >
            {{ t('spaces.teaching.fields.materialsUpload') }}
          </BaseButton>
        </template>
      </div>

      <div v-if="dangling.length > 0" class="material-picker__dangling" data-testid="teaching-materials-dangling">
        <p class="material-picker__hint">
          {{ t('spaces.teaching.fields.materialsDangling', { ids: dangling.join(', ') }) }}
        </p>
        <BaseButton v-for="id in dangling" :key="id" kind="ghost" size="sm" @click="toggle(id, false)">
          {{ t('spaces.teaching.fields.materialsDanglingDrop', { id }) }}
        </BaseButton>
      </div>
    </template>
  </div>
</template>

<style scoped>
.material-picker {
  display: flex;
  flex-direction: column;
}

.material-picker__label {
  font-size: 0.875rem;
  font-weight: 600;
  color: var(--text);
}

.material-picker__hint {
  margin: 2px 0 0;
  font-size: 0.75rem;
  color: var(--muted);
}

.material-picker__field {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
  min-height: 40px;
  margin-top: 4px;
  padding: 4px 10px;
  border: 1px solid var(--line-2);
  border-radius: var(--radius-sm);
  background: var(--surface);
  cursor: pointer;
}

.material-picker__field:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 1px;
}

.material-picker__chip {
  max-width: 100%;
}

.material-picker__chip-x {
  margin-left: 6px;
  padding: 0;
  border: none;
  background: none;
  color: inherit;
  font-size: 0.75rem;
  line-height: 1;
  cursor: pointer;
}

.material-picker__placeholder {
  font-size: 0.8125rem;
  color: var(--muted);
}

.material-picker__caret {
  display: flex;
  margin-left: auto;
  color: var(--muted);
}

.material-picker__menu {
  margin-top: 4px;
  padding: 6px 10px 8px;
  border: 1px solid var(--line-2);
  border-radius: var(--radius-sm);
  background: var(--surface);
}

.material-picker__subhead {
  font-size: 0.75rem;
  font-weight: 600;
  color: var(--muted);
}

.material-picker__list {
  max-height: 220px;
  overflow-y: auto;
}

.material-picker__sep {
  margin: 6px 0;
}

.material-picker__upload {
  padding-left: 4px;
}

.material-picker__empty {
  margin: 8px 0 0;
  font-size: 0.8125rem;
  color: var(--muted);
}

.material-picker__dangling {
  margin-top: 8px;
  padding: 8px 12px;
  border-radius: var(--radius-md);
  background: rgba(var(--v-theme-warning), 0.08);
}
</style>
