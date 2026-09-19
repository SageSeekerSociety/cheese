<script setup lang="ts">
// 一份 .docx 的修订，逐条处理。
//
// 改别人的文档要留修订，所以一份芝士改过的 .docx 里带着 `<w:ins>` / `<w:del>`。
// 旁边那一页已经把它们画出来了——LibreOffice 会渲染修订（实测：插入和删除的文字都
// 出现在 PDF 里）。所以这份清单不是为了让人看见改动，是为了让人**处理**改动：逐条
// 接受或拒绝，不用先装一个 Word。
//
// 每一条都带作者，而且一条都不过滤。用户传来的文档里本来就可能有别人未接受的修订，
// 在自己的文档里接受同事的一处改动是件平常事；不平常的是不知不觉地接受了它。
//
// 预览和改动两格用的是同一个组件：一处修订算一条这件事只能有一个答案，两份实现走散
// 的表现是读者点了第 2 条、生效的是第 3 条。
import type { DocumentRevision } from '../../../cx_types'

import { computed, ref, watch } from 'vue'

import { decideDocumentRevisions, documentRevisions } from '../../../api'
import { isLibraryPath } from '../../../lib/library'

const props = withDefaults(
  defineProps<{
    topicId: string | null
    path: string | null
    /** 这个文件现在是哪一版，用来判断要不要重读清单。 */
    version?: string | null
    /** 从哪个库读：某个任务的工作树，还是房间自己的文件（null）。 */
    task?: string | null
  }>(),
  { version: null, task: null }
)

// 处理完一条，文件就变了：宿主要重画那一页，而它是按文件版本缓存的——版本这时还没
// 变（是这里改的，不是芝士改的），所以要明说一句。
const emit = defineEmits<{ (e: 'decided'): void }>()

// 资料库里的那一份是用户给进来的原件，只读——修订照样列出来（它们是这份文档的一部
// 分，读者有权看见），但处理不了：接受一处修订会改写所有房间都在引用的那一份。
const readOnly = computed(() => isLibraryPath(props.path ?? ''))

const revisions = ref<DocumentRevision[]>([])
const error = ref('')
const deciding = ref(0)
let listedKey = ''
// 读这份清单时文件是哪一版：处理时带回去，芝士在这中间重新交付过就不会被盖掉。
let listedVersion = ''

async function load() {
  const tid = props.topicId
  const path = props.path
  if (!tid || !path) {
    revisions.value = []
    listedKey = ''
    return
  }
  const key = `${tid}:${props.task ?? ''}:${path}:${props.version ?? ''}`
  if (key === listedKey) return
  listedKey = key
  error.value = ''
  try {
    const read = await documentRevisions(tid, path, props.task)
    revisions.value = read.revisions
    listedVersion = read.version
  } catch (e) {
    // 读不到修订不该把文档也弄没：文档本身还好好地显示着。
    revisions.value = []
    listedVersion = ''
    error.value = e instanceof Error ? e.message : '未能读取修订'
  }
}

async function decide(decision: { accept?: number[]; reject?: number[] }) {
  const tid = props.topicId
  const path = props.path
  if (!tid || !path) return
  deciding.value += 1
  error.value = ''
  try {
    const done = await decideDocumentRevisions(tid, path, listedVersion, decision, props.task)
    revisions.value = done.revisions
    listedVersion = done.version
    // 文件改了，重新数的序号也变了：清单和那一页都要刷新，别让读者对着旧清单点第二下。
    listedKey = ''
    emit('decided')
    await load()
  } catch (e) {
    const said = e instanceof Error ? e.message : '未能处理这处修订'
    // 写不进去多半是文件已经变了：先把清单换成现在这份，再说刚才那下没生效。
    listedKey = ''
    await load()
    error.value = said
  } finally {
    deciding.value -= 1
  }
}

function reads(row: DocumentRevision): string {
  if (row.kind === 'replace') return `把「${row.removed}」改成「${row.added}」`
  if (row.kind === 'insert') return `加了「${row.added}」`
  return `删了「${row.removed}」`
}

watch([() => props.topicId, () => props.path, () => props.version, () => props.task], () => void load(), {
  immediate: true,
})

defineExpose({ reload: load })
</script>

<template>
  <!-- 一根柱子，两种内容：清单，或者一句「没读出来」。读不出清单时文档照旧显示——
       丢掉的是清单，而那份文档仍然是这个文件现在的样子。 -->
  <aside v-if="error || revisions.length" class="revs" data-testid="revisions">
    <v-alert v-if="error" type="warning" density="compact" class="mb-2">{{ error }}</v-alert>

    <div v-if="revisions.length" class="revs__bar">
      <span class="revs__count t-eyebrow">修订 {{ revisions.length }} 处</span>
      <v-spacer />
      <v-btn
        v-if="!readOnly"
        size="x-small"
        variant="text"
        class="c-muted"
        :disabled="deciding > 0"
        @click="decide({ accept: revisions.map((r) => r.number) })"
      >
        全部接受
      </v-btn>
      <v-btn
        v-if="!readOnly"
        size="x-small"
        variant="text"
        class="c-muted"
        :disabled="deciding > 0"
        @click="decide({ reject: revisions.map((r) => r.number) })"
      >
        全部拒绝
      </v-btn>
    </div>

    <p v-if="readOnly && revisions.length" class="revs__note t-meta">
      资料库里的原件不改。要改这份文档，让芝士基于它做一份新的
    </p>

    <ul v-if="revisions.length" class="revs__list">
      <li v-for="row in revisions" :key="row.number" class="revs__item">
        <div class="revs__what">{{ reads(row) }}</div>
        <div class="revs__who t-meta">第 {{ row.paragraph }} 段 · {{ row.author || '未署名' }}</div>
        <div v-if="!readOnly" class="revs__acts">
          <v-btn size="x-small" variant="text" :disabled="deciding > 0" @click="decide({ accept: [row.number] })">
            接受
          </v-btn>
          <v-btn
            size="x-small"
            variant="text"
            class="c-muted"
            :disabled="deciding > 0"
            @click="decide({ reject: [row.number] })"
          >
            拒绝
          </v-btn>
        </div>
      </li>
    </ul>
  </aside>
</template>

<style scoped>
.revs {
  flex: none;
  width: 236px;
  min-height: 0;
  overflow-y: auto;
  padding: 8px 12px 12px;
  border-left: 1px solid var(--line);
  background: var(--surface);
}
.revs__bar {
  display: flex;
  align-items: center;
  gap: 4px;
  margin-bottom: 8px;
}
.revs__count {
  color: var(--muted);
}
.revs__note {
  margin: 0 0 8px;
  color: var(--faint);
}
.revs__list {
  list-style: none;
  padding: 0;
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.revs__item {
  padding: 8px;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
}
.revs__what {
  font-size: 13px;
  color: var(--text);
  word-break: break-word;
}
.revs__who {
  margin-top: 2px;
  color: var(--faint);
}
.revs__acts {
  display: flex;
  gap: 4px;
  margin-top: 4px;
}

/* 窄屏上它落到页面下方（宿主把 .doc__body 改成竖排），所以左边那条界线要换成
   上边那条。 */
@media (max-width: 720px) {
  .revs {
    width: auto;
    max-height: 38%;
    border-left: none;
    border-top: 1px solid var(--line);
  }
}
</style>
