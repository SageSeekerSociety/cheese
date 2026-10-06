<script setup lang="ts">
// 任务的产出 (#1085 结论四)：AI 队友在这件任务里摆出来的东西，新的在前。
//
// 它们属于这件任务：看完拿走，事情就结束了。想把一份留下来以后还用，按「保存到资料
// 库」——按了才算，平台不猜、不自动留。留下来的那一份按原名进资料库，别处也引用得到。
//
// 它不因此上产物清单：清单上的一项是**要交出去的**东西，而留着以后用的是资料。真交付
// 的那一下由 AI 队友在提交审阅时声明，和这个按钮无关。
//
// 它在任务概览里、实况文档下面，跟着整列一起滚，没有自己的折叠和滚动条。
import type { MenuAction } from '@/components/common/menuAction'
import type { DocumentTemplate, RoomOutput } from '@/types/roomOutput'

import { computed, ref } from 'vue'

import { useRowMenu } from '@/composables/useRowMenu'
import { useTaskOutputs } from '@/composables/useTaskOutputs'

import BaseButton from '@/components/base/BaseButton.vue'
import AdaptiveMenu from '@/components/common/AdaptiveMenu.vue'
import { t } from '@/i18n'
import { relTime } from '@/lib/relTime'

const props = defineProps<{ topicId: string | null }>()
const emit = defineEmits<{
  /** 点开这一份：开成面板里的一个页签。 */
  (e: 'open', path: string): void
}>()

const io = useTaskOutputs(() => props.topicId)
const outputs = io.outputs
const templates = io.templates
const saving = ref('')
const error = ref('')
const saved = ref<Record<string, string>>({})

/** 跑着的应用没有文件可存：它是一个进程，不是一份东西。 */
const files = computed(() => outputs.value.filter((o) => o.kind === 'file'))

// 右键一个文件：打开它、存进资料库（已经存过的不再给），弹在鼠标那一点上。
const rowMenu = useRowMenu<string>()
function outputActions(output: RoomOutput): MenuAction[] {
  const actions: MenuAction[] = [
    {
      key: 'open',
      label: t('tasks.preview.roomOutputs.openFile', { path: name(output.path) }),
      icon: 'mdi-file-eye-outline',
      onSelect: () => emit('open', output.path),
    },
  ]
  if (!saved.value[output.path])
    actions.push({
      key: 'save',
      label: t('tasks.preview.roomOutputs.saveToLibrary'),
      icon: 'mdi-folder-arrow-down-outline',
      loading: saving.value === output.path,
      onSelect: () => void save(output),
    })
  return actions
}

async function save(output: RoomOutput) {
  saving.value = output.path
  error.value = ''
  try {
    const name = await io.save(output.path)
    if (name) saved.value = { ...saved.value, [output.path]: t('tasks.preview.roomOutputs.savedToLibrary', { name }) }
  } catch (e) {
    error.value = e instanceof Error ? e.message : t('tasks.preview.roomOutputs.saveFailed')
  } finally {
    saving.value = ''
  }
}

// 从标准模板新建：建出来的是这件任务里的一份文件，建好就打开，进编辑器接着写。
const picking = ref<DocumentTemplate | null>(null)
const newPath = ref('')
const creating = ref(false)
const choosing = ref(false)

function toggleTemplates() {
  choosing.value = !choosing.value
  picking.value = null
  if (choosing.value) void io.loadTemplates()
}

function pick(template: DocumentTemplate) {
  picking.value = template
  newPath.value = `${t('work.room.outputs.defaultFolder')}/${template.name}.${template.suffix}`
}

async function create() {
  const template = picking.value
  if (!template) return
  creating.value = true
  error.value = ''
  try {
    const made = await io.create(template.id, newPath.value.trim())
    picking.value = null
    choosing.value = false
    if (made) emit('open', made)
  } catch (e) {
    error.value = e instanceof Error ? e.message : t('tasks.preview.roomOutputs.createFailed')
  } finally {
    creating.value = false
  }
}

function name(path: string): string {
  return path.split('/').pop() || path
}

defineExpose({ reload: io.load })
</script>

<template>
  <section v-if="topicId" class="outs" data-testid="room-outputs">
    <div class="outs__head">
      <h3 class="outs__title">
        {{ t('work.task.outputs') }}
        <span v-if="files.length" class="outs__count t-meta">· {{ files.length }}</span>
      </h3>
      <BaseButton
        kind="ghost"
        size="sm"
        prepend-icon="mdi-file-plus-outline"
        data-testid="new-from-template"
        @click="toggleTemplates"
      >
        {{ t('tasks.preview.roomOutputs.newFromTemplate') }}
      </BaseButton>
    </div>
    <ul v-if="choosing && !picking" class="outs__templates">
      <li v-if="!templates.length" class="t-meta c-faint">
        {{ t('tasks.preview.roomOutputs.readingTemplates') }}
      </li>
      <li v-for="tpl in templates" :key="tpl.id">
        <button type="button" class="outs__template" @click="pick(tpl)">
          <span class="t-body">{{ t('work.room.outputs.templateName', { name: tpl.name, suffix: tpl.suffix }) }}</span>
          <span class="t-meta c-faint">{{ tpl.about }}</span>
        </button>
      </li>
    </ul>
    <div v-if="picking" class="outs__new">
      <v-text-field
        v-model="newPath"
        density="compact"
        hide-details
        autocomplete="off"
        :label="t('tasks.preview.roomOutputs.newFromTemplateLabel', { name: picking.name })"
      />
      <BaseButton kind="primary" size="sm" :loading="creating" @click="create">
        {{ t('tasks.preview.roomOutputs.createAndOpen') }}
      </BaseButton>
      <BaseButton kind="ghost" size="sm" @click="toggleTemplates">
        {{ t('tasks.preview.roomOutputs.cancel') }}
      </BaseButton>
    </div>
    <p v-if="error" role="alert" class="outs__error t-meta">{{ error }}</p>
    <p v-if="!files.length" class="t-meta c-faint">{{ t('work.task.noOutputs') }}</p>
    <ul v-else class="outs__list">
      <li v-for="output in files" :key="output.path" class="outs-row" @contextmenu="rowMenu.open(output.path, $event)">
        <AdaptiveMenu v-bind="rowMenu.bind(output.path)" :actions="outputActions(output)" :title="name(output.path)">
          <template #activator />
        </AdaptiveMenu>
        <button
          type="button"
          class="outs-row__name t-body"
          :title="t('tasks.preview.roomOutputs.openFile', { path: output.path })"
          @click="emit('open', output.path)"
        >
          {{ name(output.path) }}
        </button>
        <span class="outs-row__when t-meta c-faint">{{ relTime(output.shown_at) }}</span>
        <span v-if="saved[output.path]" class="t-meta c-faint">{{ saved[output.path] }}</span>
        <BaseButton v-else kind="ghost" size="sm" :loading="saving === output.path" @click="save(output)">
          {{ t('tasks.preview.roomOutputs.saveToLibrary') }}
        </BaseButton>
      </li>
    </ul>
  </section>
</template>

<style scoped>
.outs {
  flex: none;
  padding: 16px;
  border-top: 1px solid var(--line);
}
.outs__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.outs__title {
  margin: 0;
  font-size: 13px;
  line-height: var(--lh-13);
  font-weight: 600;
  color: var(--ink);
}
.outs__count {
  margin-left: 4px;
  color: var(--faint);
  font-variant-numeric: tabular-nums;
}
.outs__templates {
  list-style: none;
  padding: 0;
  margin: 0 0 6px;
}
.outs__template {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  width: 100%;
  padding: 4px 6px;
  border: 0;
  border-radius: var(--radius-sm);
  background: transparent;
  text-align: left;
  cursor: pointer;
}
.outs__template:hover {
  background: var(--canvas);
}
.outs__new {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 6px;
}
/* 错误是给人读的一行字，所以用墨色那一档，不是记号色。 */
.outs__error {
  margin: 0 0 6px;
  color: var(--danger-ink);
}
.outs__list {
  list-style: none;
  padding: 0;
  margin: 6px 0 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.outs-row {
  display: flex;
  align-items: center;
  gap: 8px;
}
.outs-row__name {
  flex: 1 1 auto;
  min-width: 0;
  padding: 0;
  border: 0;
  background: transparent;
  text-align: left;
  cursor: pointer;
  color: var(--text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.outs-row__name:hover {
  color: var(--ink);
  text-decoration: underline;
}
.outs-row__when {
  flex: 0 0 auto;
  font-variant-numeric: tabular-nums;
}
</style>
