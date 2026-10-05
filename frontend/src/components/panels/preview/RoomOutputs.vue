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
// 它挂在总览的最底下、实况文档下面，默认收起，只露小标题那一行（带件数）。它原先
// 在预览那一格的底部，和预览抢高度；预览那一格现在只放预览。小标题那一行是折叠开
// 关，摊开后列表有高度上限、自己滚，不把上面的文档挤没。
import type { MenuAction } from '@/components/common/menuAction'
import type { DocumentTemplate, RoomOutput } from '@/cx_types'

import { computed, ref, useId, watch } from 'vue'

import { useRowMenu } from '@/composables/useRowMenu'

import BaseButton from '@/components/base/BaseButton.vue'
import AdaptiveMenu from '@/components/common/AdaptiveMenu.vue'
import { t } from '@/i18n'
import { relTime } from '@/lib/relTime'

// **只吃 props**：取数在 `components/work/PanelOverviewHost.vue`（它渲染整个总览）。
// 这里留下的只有「这一格长什么样」：折叠态、正在存哪一份、存完了叫什么、出错了说什么。
// `components/panels/**` 下每个 SFC 都是「场景」，场景不取数，也不自己定义怎么取。
const props = withDefaults(
  defineProps<{
    topicId: string | null
    /** 这个房间摆出来的东西，新的在前。读不到就是空的。 */
    outputs?: RoomOutput[]
    /** 标准模板。第一次点「从模板新建」时由上面那一层去取，取到的再传回来。 */
    templates?: DocumentTemplate[]
    /** 去取一次模板列表。 */
    loadTemplates?: () => void
    /** 把一份存进资料库，返回它在资料库里叫什么（撞名时那边会加 `(2)`）。 */
    saveToLibrary?: (path: string) => Promise<string>
    /** 从模板建一份。建完的列表刷新和打开由上面那一层做——这里只管按钮的忙碌和报错。 */
    createFromTemplate?: (templateId: string, path: string) => Promise<void>
  }>(),
  {
    topicId: null,
    outputs: () => [],
    templates: () => [],
    loadTemplates: undefined,
    saveToLibrary: undefined,
    createFromTemplate: undefined,
  }
)

const emit = defineEmits<{
  /** 点开这一份：上面那块只看得到最后一样，前面几样从这里开成自己的页签。 */
  (e: 'open', path: string): void
}>()

const saving = ref('')
const error = ref('')
const saved = ref<Record<string, string>>({})

/** 跑着的应用没有文件可存：它是一个进程，不是一份东西。 */
const files = computed(() => props.outputs.filter((o) => o.kind === 'file'))

// ---- 折叠 ----
// 按话题存人自己按的那一下；键不在 = 没按过 = 收起。v1 是它还在预览格里、默认按
// 列表长短摊开时存的，搬进总览后默认变了，旧值不再沿用。
const EXPANDED_PREFIX = 'cheesex.roomOutputsExpanded.v2:'

function storageKey(topicId: string | null): string | null {
  const id = (topicId ?? '').trim()
  return id ? `${EXPANDED_PREFIX}${encodeURIComponent(id)}` : null
}

/** null = 没存过（人没按过），调用方取默认的收起。 */
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

const expanded = ref(false)

const rowsId = `room-outputs-${useId()}`

function toggleExpanded() {
  expanded.value = !expanded.value
  saveExpanded(props.topicId, expanded.value)
}

/** 人按过的那一下优先；没按过就收起。 */
function applyDefault() {
  expanded.value = loadExpanded(props.topicId) ?? false
}

// 换一个话题就是换一份列表，折叠态也跟着换一份。列表本身由上面那一层跟着换。
applyDefault()
watch(() => props.topicId, applyDefault)

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
  const toLibrary = props.saveToLibrary
  if (!toLibrary) return
  saving.value = output.path
  error.value = ''
  try {
    // 说出它在资料库里叫什么：撞名时那边会加 `(2)`，而人下次找的是那个名字。
    const name = await toLibrary(output.path)
    saved.value = {
      ...saved.value,
      [output.path]: t('tasks.preview.roomOutputs.savedToLibrary', { name }),
    }
  } catch (e) {
    error.value = e instanceof Error ? e.message : t('tasks.preview.roomOutputs.saveFailed')
  } finally {
    saving.value = ''
  }
}

// 从标准模板新建：建出来的是房间里的一份文件，建好就打开，进编辑器接着写。
const picking = ref<DocumentTemplate | null>(null)
const newPath = ref('')
const creating = ref(false)
const choosing = ref(false)

function toggleTemplates() {
  choosing.value = !choosing.value
  picking.value = null
  if (choosing.value) props.loadTemplates?.()
}

function pick(template: DocumentTemplate) {
  picking.value = template
  newPath.value = `${t('work.room.outputs.defaultFolder')}/${template.name}.${template.suffix}`
}

async function create() {
  const template = picking.value
  const make = props.createFromTemplate
  if (!template || !make) return
  creating.value = true
  error.value = ''
  try {
    await make(template.id, newPath.value.trim())
    picking.value = null
    choosing.value = false
  } catch (e) {
    error.value = e instanceof Error ? e.message : t('tasks.preview.roomOutputs.createFailed')
  } finally {
    creating.value = false
  }
}

function name(path: string): string {
  return path.split('/').pop() || path
}
</script>

<template>
  <section v-if="topicId" class="outs" data-testid="room-outputs">
    <div class="outs__head">
      <!-- 小标题这一行就是折叠开关：点它摊开／收起整个列表。 -->
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
    <ul v-show="expanded" :id="rowsId" class="outs__list">
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
  padding: 8px 12px;
  border-top: 1px solid var(--line);
}
.outs__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
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
  /* 摊开了也只占总览约四成高，再多就自己滚：上面的文档才是这一格的主体。 */
  max-height: 40vh;
  overflow-y: auto;
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
