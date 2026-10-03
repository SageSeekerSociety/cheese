<script setup lang="ts">
// 这个房间里的东西 (#1085 结论四)。
//
// 一个房间常有好几样值得看的东西 —— 一份改好的 .docx、一张图、一个跑起来的应用
// —— 而上面那块预览只显示最后摆出来的那一样。这几行是全部，新的在前。
//
// 它们属于这个房间：用户看完拿走，事情就结束了。想把一份留下来以后还用，按一下
// 「保存到资料库」——按了才算，平台不猜、不自动留。留下来的那一份按原名进资料库，
// 别的房间也引用得到，和用户自己上传的那些并排。
//
// 它不因此上产物清单：清单上的一项是**要交出去的**东西，而留着以后用的是资料。真
// 交付的那一下由 芝士 在递卡时声明，那条路和这个按钮无关。
//
// 只有一样东西时这一块照样出现：上面那块预览只是在看它，而这个动作只在这里有。
//
// 列表长了要收得住：一个跑久了的房间能摆出三十几样东西，全都摊在这里会把上面那条
// 应用条顶出屏幕（早先更糟——它会把那一格压成 0 高，条和 iframe 溢出来叠在标题行
// 上）。所以小标题那一行是个折叠开关，默认短列表全摊开、长列表只露最近几行。
import type { DocumentTemplate, RoomOutput } from '@/api'

import { computed, ref, useId, watch } from 'vue'

import { listDocumentTemplates, listRoomOutputs, newFromTemplate, saveRoomOutputToLibrary } from '@/api'
import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'
import { relTime } from '@/lib/relTime'

const props = defineProps<{ topicId: string | null }>()
const emit = defineEmits<{
  /** 点开这一份：上面那块只看得到最后一样，前面几样从这里开成自己的页签。 */
  (e: 'open', path: string): void
}>()

const outputs = ref<RoomOutput[]>([])
const saving = ref('')
const error = ref('')
const saved = ref<Record<string, string>>({})

/** 跑着的应用没有文件可存：它是一个进程，不是一份东西。 */
const files = computed(() => outputs.value.filter((o) => o.kind === 'file'))

// ---- 折叠 ----
// 收起来时露几行。长列表留最近这几行做引子（末尾给「展开全部 N 项」），短列表收
// 起来就是收起来 —— 一共三五样还留五行，等于按了没反应。
const PREVIEW_COUNT = 5
// 按话题存，键里存的是人自己按的那一下：键不在 = 还没按过，按列表长短取默认。
// 默认跟着长度走，所以默认态不能只靠「键不在」表达——这个键要写 0 和 1 两个值。
const EXPANDED_PREFIX = 'cheesex.roomOutputsExpanded.v1:'

function storageKey(topicId: string | null): string | null {
  const id = (topicId ?? '').trim()
  return id ? `${EXPANDED_PREFIX}${encodeURIComponent(id)}` : null
}

/** null = 没存过（人没按过），调用方按列表长短决定默认。 */
function loadExpanded(topicId: string | null): boolean | null {
  const key = storageKey(topicId)
  if (!key || typeof localStorage === 'undefined') return null
  try {
    const raw = localStorage.getItem(key)
    return raw === '1' ? true : raw === '0' ? false : null
  } catch {
    return null
  }
}

function saveExpanded(topicId: string | null, expanded: boolean): void {
  const key = storageKey(topicId)
  if (!key || typeof localStorage === 'undefined') return
  try {
    localStorage.setItem(key, expanded ? '1' : '0')
  } catch {
    // 隐私模式/配额满：这一下仍然在内存里生效，只是刷新后回到默认。
  }
}

const expanded = ref(true)

/** 露在外面的那几行。 */
const rows = computed(() => {
  if (expanded.value) return files.value
  return files.value.length > PREVIEW_COUNT ? files.value.slice(0, PREVIEW_COUNT) : []
})
const hiddenCount = computed(() => files.value.length - rows.value.length)

const rowsId = `room-outputs-${useId()}`

function toggleExpanded() {
  expanded.value = !expanded.value
  saveExpanded(props.topicId, expanded.value)
}

/** 人按过的那一下优先；没按过看长短——短列表摊开，长列表收在最近几行。 */
function applyDefault() {
  expanded.value = loadExpanded(props.topicId) ?? files.value.length <= PREVIEW_COUNT
}

async function load() {
  const topicId = props.topicId
  if (!topicId) return
  try {
    const listed = await listRoomOutputs(topicId)
    outputs.value = listed.data
  } catch {
    // 读不到这一块就不显示它：这一格的主体是上面那块预览。
    outputs.value = []
  }
  // 默认态跟着列表长短走，所以得等列表回来再定。
  applyDefault()
}

// 换一个话题就是换一份列表，折叠态也跟着换一份。
watch(() => props.topicId, applyDefault)

async function save(output: RoomOutput) {
  const topicId = props.topicId
  if (!topicId) return
  saving.value = output.path
  error.value = ''
  try {
    const done = await saveRoomOutputToLibrary(topicId, output.path)
    // 说出它在资料库里叫什么：撞名时那边会加 `(2)`，而人下次找的是那个名字。
    saved.value = {
      ...saved.value,
      [output.path]: t('tasks.preview.roomOutputs.savedToLibrary', { name: done.name }),
    }
  } catch (e) {
    error.value = e instanceof Error ? e.message : t('tasks.preview.roomOutputs.saveFailed')
  } finally {
    saving.value = ''
  }
}

// 从标准模板新建：建出来的是房间里的一份文件，建好就打开，进编辑器接着写。
const templates = ref<DocumentTemplate[]>([])
const picking = ref<DocumentTemplate | null>(null)
const newPath = ref('')
const creating = ref(false)
const choosing = ref(false)

function toggleTemplates() {
  choosing.value = !choosing.value
  picking.value = null
  if (choosing.value) void loadTemplates()
}

async function loadTemplates() {
  const topicId = props.topicId
  if (!topicId || templates.value.length) return
  try {
    templates.value = (await listDocumentTemplates(topicId)).data
  } catch {
    templates.value = []
  }
}

function pick(template: DocumentTemplate) {
  picking.value = template
  newPath.value = `${t('work.room.outputs.defaultFolder')}/${template.name}.${template.suffix}`
}

async function create() {
  const topicId = props.topicId
  const template = picking.value
  if (!topicId || !template) return
  creating.value = true
  error.value = ''
  try {
    const made = await newFromTemplate(topicId, template.id, newPath.value.trim())
    picking.value = null
    choosing.value = false
    await load()
    emit('open', made.path)
  } catch (e) {
    error.value = e instanceof Error ? e.message : t('tasks.preview.roomOutputs.createFailed')
  } finally {
    creating.value = false
  }
}

function name(path: string): string {
  return path.split('/').pop() || path
}

void load()

defineExpose({ reload: load })
</script>

<template>
  <section v-if="topicId" class="outs" data-testid="room-outputs">
    <div class="outs__head">
      <!-- 小标题这一行就是折叠开关：长列表收在最近几行，点它摊开／收起整个列表。
           aria-expanded 说的是「全摊开了没有」——收起来时那几行只是引子。 -->
      <button
        type="button"
        class="outs__toggle"
        data-testid="room-outputs-toggle"
        :title="expanded ? t('tasks.preview.roomOutputs.collapse') : t('tasks.preview.roomOutputs.expand')"
        :aria-expanded="expanded"
        :aria-controls="rowsId"
        @click="toggleExpanded"
      >
        <v-icon size="16" class="outs__chevron">
          {{ expanded ? 'mdi-chevron-down' : 'mdi-chevron-right' }}
        </v-icon>
        <span class="outs__title t-eyebrow c-muted">{{ t('tasks.preview.roomOutputs.title') }}</span>
        <!-- 有几样东西是这一块唯一该说清的事，收起时更得说。 -->
        <span v-if="files.length" class="outs__count t-meta">· {{ files.length }}</span>
      </button>
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
    <ul :id="rowsId" class="outs__list">
      <li v-for="output in rows" :key="output.path" class="outs-row">
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
      <!-- 收起来的那些去哪儿了：说清一共有多少样，按钮就在这一行的末尾。 -->
      <li v-if="hiddenCount > 0" class="outs__more">
        <BaseButton kind="ghost" size="sm" data-testid="room-outputs-expand-all" @click="toggleExpanded">
          {{ t('tasks.preview.roomOutputs.expandAll', { count: files.length }) }}
        </BaseButton>
      </li>
    </ul>
  </section>
</template>

<style scoped>
.outs {
  padding: 8px 12px 12px;
  border-top: 1px solid var(--line);
}
.outs__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 6px;
}
.outs__title {
  margin: 0;
}
/* 折叠开关就是小标题那一行本身：读起来是标题，点起来是开关。 */
.outs__toggle {
  display: flex;
  align-items: center;
  gap: 2px;
  padding: 0;
  text-align: left;
  cursor: pointer;
  background: transparent;
  border: 0;
}
.outs__toggle:hover .outs__title {
  color: var(--ink);
}
.outs__chevron {
  flex: none;
  color: var(--faint);
}
.outs__count {
  margin-left: 4px;
  color: var(--faint);
  font-variant-numeric: tabular-nums;
}
/* 「展开全部 N 项」跟着列表末尾，不另起一块。 */
.outs__more {
  display: flex;
  justify-content: flex-start;
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
  margin: 0;
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
