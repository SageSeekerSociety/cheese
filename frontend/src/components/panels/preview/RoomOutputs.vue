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
import type { RoomOutput } from '@/api'

import { computed, ref, watch } from 'vue'

import { listRoomOutputs, saveRoomOutputToLibrary } from '@/api'
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

async function load() {
  const topicId = props.topicId
  if (!topicId) return
  try {
    const listed = await listRoomOutputs(topicId)
    if (props.topicId !== topicId) return
    outputs.value = listed.data
  } catch {
    // 读不到这一块就不显示它：这一格的主体是上面那块预览。
    if (props.topicId === topicId) outputs.value = []
  }
}

async function save(output: RoomOutput) {
  const topicId = props.topicId
  if (!topicId) return
  saving.value = output.path
  error.value = ''
  try {
    const done = await saveRoomOutputToLibrary(topicId, output.path)
    // 说出它在资料库里叫什么：撞名时那边会加 `(2)`，而人下次找的是那个名字。
    saved.value = { ...saved.value, [output.path]: `已存进资料库：${done.name}` }
  } catch (e) {
    error.value = e instanceof Error ? e.message : '未能存进资料库'
  } finally {
    saving.value = ''
  }
}

function name(path: string): string {
  return path.split('/').pop() || path
}

watch(
  () => props.topicId,
  () => {
    outputs.value = []
    saved.value = {}
    error.value = ''
    void load()
  },
  { immediate: true }
)

defineExpose({ reload: load })
</script>

<template>
  <section v-if="files.length" class="outs" data-testid="room-outputs">
    <h3 class="outs__title t-eyebrow c-muted">这个房间里的东西</h3>
    <p v-if="error" role="alert" class="outs__error t-meta">{{ error }}</p>
    <ul class="outs__list">
      <li v-for="output in files" :key="output.path" class="outs-row">
        <button
          type="button"
          class="outs-row__name t-body"
          :title="`打开 ${output.path}`"
          @click="emit('open', output.path)"
        >
          {{ name(output.path) }}
        </button>
        <span class="outs-row__when t-meta c-faint">{{ relTime(output.shown_at) }}</span>
        <span v-if="saved[output.path]" class="t-meta c-faint">{{ saved[output.path] }}</span>
        <v-btn
          v-else
          size="small"
          variant="text"
          color="on-surface-variant"
          :loading="saving === output.path"
          @click="save(output)"
        >
          保存到资料库
        </v-btn>
      </li>
    </ul>
  </section>
</template>

<style scoped>
.outs {
  padding: 8px 12px 12px;
  border-top: 1px solid var(--line);
}
.outs__title {
  margin: 0 0 6px;
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
