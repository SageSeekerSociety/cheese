<script setup lang="ts">
// 新建 / 修改一条规则的那张表单。项目总览页和房间右侧那一格用同一张。
//
// **新建是两步里的一步**：只问「在哪个频道执行」「管哪几个频道」和一段大白话。名字、
// 工作内容、存储位置、执行条件都由 AI 从这段话里起草，之后摊在「待确认」那一条里，
// 那时才是这些字段第一次出现在界面上。
//
// **修改是同一套字段的另一半**：起草好的每一项都能改，包括名字、工作内容、存储位置、
// 执行条件和执行范围。所以这张表有两个形状，按 `routine` 给没给分开。
//
// 只吃 props：不知道接口在哪儿、也不认识路由。存下去由调用方办（`@save`），出错也由
// 调用方把话递回来（`error`）—— 那句话要写在这张表里，人改了字段就地再存一次。
import type { Routine, RoutineInput } from '@/lib/routine'

import { computed, reactive, ref, watch } from 'vue'

import AdaptiveDialog from '@/components/common/AdaptiveDialog.vue'
import { t } from '@/i18n'

/** 一个能执行规则的频道：够画那一排勾，也够画「在「X」这个频道执行」那一行。 */
interface Room {
  id: string
  title: string
}

const props = withDefaults(
  defineProps<{
    /** 关着的时候组件还在（对话框有自己的退场动画），所以「开没开」是一个 prop。 */
    modelValue: boolean
    /** 正在改的那一条；`null` 是新建。 */
    routine?: Routine | null
    /** 可选频道。空数组 = 不画这一格（房间面板里频道是定死的：规则就长在这个频道里）。 */
    rooms?: Room[]
    /** 新建时落在哪个频道。`rooms` 空着时就是它。 */
    defaultRoom?: string
    /** 那个频道叫什么。房间面板里只知道 id，名字由外壳传进来——那一行要说人话。 */
    defaultRoomName?: string
    /** 从频道里点「新建」进来：落在当前频道，也不再问执行范围。 */
    inRoom?: boolean
    saving?: boolean
    error?: string
  }>(),
  {
    routine: null,
    rooms: () => [],
    defaultRoom: '',
    defaultRoomName: '',
    inRoom: false,
    saving: false,
    error: '',
  }
)

const emit = defineEmits<{
  (e: 'update:modelValue', open: boolean): void
  (e: 'save', payload: { room: string; body: RoutineInput }): void
}>()

const form = reactive({
  room: '',
  /** 新建时那段大白话；它就是起草的输入，也是 `instructions` 的初值。 */
  brief: '',
  /** 修改时才有的那几项。 */
  title: '',
  instructions: '',
  output_dir: '',
  trigger: 'schedule' as RoutineInput['trigger'],
  scope_topic_ids: [] as string[],
})

const editing = ref<Routine | null>(null)
const heading = ref('')
/** 修改：AI 保存后重新算执行条件，所以那一格是只读的一句话。 */
const triggerText = ref('')

const rooms = computed<Room[]>(() => props.rooms)
/** 新建时能从这几个里勾；修改时是「保存之后哪些频道归它管」。 */
const selectable = computed(() => (props.routine ? rooms.value.filter((r) => r.id !== form.room) : rooms.value))
const allSelected = computed(
  () => selectable.value.length > 0 && form.scope_topic_ids.length === selectable.value.length
)

// 打开的那一下把表单铺成这一条的样子。关上的这一下不动它：收起动画还在画刚才那张，
// 半路清空会闪一下。
watch(
  () => props.modelValue,
  (open) => {
    if (!open) return
    const r = props.routine
    editing.value = r
    heading.value = r ? t('routines.editTitle', { title: r.title }) : t('routines.newTitle')
    triggerText.value = r?.trigger_text ?? ''
    Object.assign(form, {
      room: r?.topic_id || props.defaultRoom || props.rooms[0]?.id || '',
      brief: '',
      title: r?.title ?? '',
      instructions: r?.instructions ?? '',
      output_dir: r?.output_dir ?? '',
      trigger: r?.trigger ?? 'schedule',
      scope_topic_ids: [...(r?.scope_topic_ids ?? [])].filter((id) => id !== r?.topic_id),
    })
  },
  { immediate: true }
)

/** 全选 / 取消全选：这一排勾一次都勾上，再一次都去掉。 */
function toggleAll() {
  form.scope_topic_ids = allSelected.value ? [] : selectable.value.map((r) => r.id)
}

function submit() {
  if (editing.value) {
    emit('save', {
      room: form.room,
      body: {
        title: form.title,
        instructions: form.instructions,
        output_dir: form.output_dir,
        trigger: form.trigger,
        spec: (editing.value.spec ?? {}) as Record<string, unknown>,
        timezone: editing.value.timezone,
        scope_topic_ids: form.scope_topic_ids,
      },
    })
    return
  }
  // 新建：只发那一段大白话，别的一项都没有。起草是后端的事（见 `routine/service.py`
  // 的 `create`：人写的直接是规则本身，AI 写的先落草稿再让人确认）。
  emit('save', {
    room: form.room,
    body: {
      title: form.brief.slice(0, 200),
      instructions: form.brief,
      output_dir: '',
      trigger: 'schedule',
      spec: {},
      timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || 'Asia/Shanghai',
      scope_topic_ids: form.scope_topic_ids,
    },
  })
}
</script>

<template>
  <AdaptiveDialog
    :model-value="modelValue"
    :title="heading"
    :primary-label="editing ? t('routines.save') : t('routines.submit')"
    :primary-loading="saving"
    :max-width="560"
    @update:model-value="emit('update:modelValue', $event)"
    @primary="submit"
  >
    <template v-if="modelValue">
      <!-- 新建：没选频道才问一句；从频道里进来就一行说明落在哪。 -->
      <v-select
        v-if="!editing && rooms.length > 1"
        v-model="form.room"
        autocomplete="off"
        :items="rooms"
        item-title="title"
        item-value="id"
        :label="t('routines.form.room')"
        class="mb-3"
      />
      <p v-else-if="!editing" class="t-body c-muted mb-3">
        {{ t('routines.form.here', { room: rooms[0]?.title || props.defaultRoomName || props.defaultRoom }) }}
      </p>

      <!-- 修改：起草好之后的那几项，每一项都能改。 -->
      <template v-if="editing">
        <v-select
          v-if="rooms.length > 1"
          v-model="form.room"
          autocomplete="off"
          :items="rooms"
          item-title="title"
          item-value="id"
          :label="t('routines.form.room')"
          class="mb-3"
        />
        <v-text-field
          v-model="form.title"
          autocomplete="off"
          :label="t('routines.form.name')"
          :placeholder="t('routines.form.namePlaceholder')"
        />
        <v-textarea
          v-model="form.instructions"
          autocomplete="off"
          :label="t('routines.form.instructions')"
          rows="3"
          auto-grow
          :placeholder="t('routines.form.instructionsPlaceholder')"
        />
        <v-text-field
          v-model="form.output_dir"
          autocomplete="off"
          :label="t('routines.form.outputDir')"
          :placeholder="t('routines.form.outputDirPlaceholder')"
        />
        <!-- 执行条件是一句话，不是可选项：改完 AI 重新算具体安排，所以这里只给读。 -->
        <v-text-field
          :model-value="triggerText"
          autocomplete="off"
          readonly
          :label="t('routines.form.trigger')"
        />
      </template>

      <!-- 执行范围：哪几个频道归它管。新建时从项目页进来才勾（从频道进来就一个频道，
           再给人勾一遍是多余的）；修改时总能勾。 -->
      <fieldset v-if="selectable.length" class="routine-form__range">
        <div class="routine-form__range-head">
          <legend class="routine-form__legend">{{ t('routines.form.range') }}</legend>
          <button type="button" class="routine-form__all" @click="toggleAll">
            {{ t('routines.form.selectAll') }}
          </button>
        </div>
        <label v-for="r in selectable" :key="r.id" class="routine-form__room">
          <input v-model="form.scope_topic_ids" type="checkbox" :value="r.id" />
          <span>{{ r.title }}</span>
        </label>
        <p v-if="!selectable.length" class="t-meta c-faint">{{ t('routines.form.rangeEmpty') }}</p>
      </fieldset>

      <v-textarea
        v-if="!editing"
        v-model="form.brief"
        autocomplete="off"
        :label="t('routines.form.open')"
        rows="3"
        auto-grow
        :placeholder="t('routines.form.openPlaceholder')"
      />
      <p class="t-meta c-faint">{{ t('routines.form.machineNote') }}</p>
      <p v-if="error" role="alert" class="t-body c-danger mt-2">{{ error }}</p>
    </template>
  </AdaptiveDialog>
</template>

<style scoped>
.routine-form__range {
  margin: 0 0 12px;
  padding: 0;
  border: 0;
}
.routine-form__range-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
}
.routine-form__legend {
  padding: 0;
  color: var(--muted);
  font-size: 13px;
}
.routine-form__all {
  padding: 0;
  border: 0;
  background: none;
  font: inherit;
  color: var(--muted);
  text-decoration: underline;
  text-decoration-color: var(--line);
  text-underline-offset: 3px;
  cursor: pointer;
}
.routine-form__room {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
}
</style>
