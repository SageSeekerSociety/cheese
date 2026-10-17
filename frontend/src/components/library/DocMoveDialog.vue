<script setup lang="ts">
// 「移动到…」（#3774）：把一份文档挪到自己名下、一个团队，或团队里的一个项目。
//
// 能去的地方摆成一列：「我的文档」在最上，每个团队一行，它的项目缩进挂在下面；点一行
// 就选中它。现在所在的那一行写「当前位置」、选不了。多的时候在上面按名字筛。挪了之后
// 谁能读跟着变，所以底下一句说清楚，人看过了再点「移动」。
// 只吃 props、只往上发：能去哪儿由页面给，挪不挪是页面的事。
import { computed, ref, watch } from 'vue'

import AdaptiveDialog from '@/components/common/AdaptiveDialog.vue'
import UserAvatar from '@/components/common/UserAvatar.vue'
import { t } from '@/i18n'

/** 一个能去的团队：自己名下（personal）或一个团队，连同它的项目。 */
export interface MovePlace {
  teamId: number
  name: string
  personal: boolean
  projects: { id: string; name: string }[]
}

type Spot = { teamId: number; projectId: string | null }

const props = defineProps<{
  title: string
  places: MovePlace[]
  /** 它现在在哪：团队和项目（不属于项目是 null）。 */
  current: Spot
  saving: boolean
  error: string
}>()
const open = defineModel<boolean>({ required: true })
const emit = defineEmits<{ (e: 'move', to: Spot): void }>()

const picked = ref<Spot | null>(null)
const query = ref('')
watch(
  open,
  (v) => {
    if (!v) return
    picked.value = null
    query.value = ''
  },
  { immediate: true }
)

const same = (a: Spot | null, b: Spot) => !!a && a.teamId === b.teamId && a.projectId === b.projectId

interface Row {
  key: string
  spot: Spot
  name: string
  kind: 'mine' | 'team' | 'project'
  teamName: string
}

/** 摆出来的那一列：自己名下在最上，团队一行、它的项目缩进跟在后面。按名字筛时，项目
 *  对上了，它的团队那一行也留着，看得出它在哪个团队里。 */
const rows = computed<Row[]>(() => {
  const q = query.value.trim().toLowerCase()
  const hit = (name: string) => !q || name.toLowerCase().includes(q)
  const ordered = [...props.places.filter((p) => p.personal), ...props.places.filter((p) => !p.personal)]
  const out: Row[] = []
  for (const place of ordered) {
    const name = place.personal ? t('work.room.doc.moveDialog.mine') : place.name
    const projects = place.projects.filter((p) => hit(p.name))
    if (!hit(name) && !projects.length) continue
    out.push({
      key: `t${place.teamId}`,
      spot: { teamId: place.teamId, projectId: null },
      name,
      kind: place.personal ? 'mine' : 'team',
      teamName: place.name,
    })
    for (const project of hit(name) ? place.projects : projects)
      out.push({
        key: `p${project.id}`,
        spot: { teamId: place.teamId, projectId: project.id },
        name: project.name,
        kind: 'project',
        teamName: place.name,
      })
  }
  return out
})

const pickedRow = computed(() => rows.value.find((r) => same(picked.value, r.spot)) ?? null)

/** 挪过去以后谁能读。 */
const readers = computed(() => {
  const row = pickedRow.value
  if (!row) return ''
  if (row.kind === 'mine') return t('work.room.doc.moveDialog.readersPersonal')
  if (row.kind === 'team') return t('work.room.doc.moveDialog.readersTeam', { name: row.name })
  return t('work.room.doc.moveDialog.readersProject', { name: row.name })
})
</script>

<template>
  <AdaptiveDialog
    v-model="open"
    :title="t('work.room.doc.moveDialog.title', { name: title })"
    :primary-label="t('work.room.doc.moveDialog.move')"
    :primary-loading="saving"
    :primary-disabled="!pickedRow"
    :cancel-label="t('work.mcp.form.cancel')"
    :max-width="480"
    @primary="picked && emit('move', picked)"
  >
    <div class="doc-move">
      <v-text-field
        v-model="query"
        :placeholder="t('work.room.doc.moveDialog.search')"
        prepend-inner-icon="mdi-magnify"
        variant="outlined"
        density="compact"
        autocomplete="off"
        hide-details
      />
      <ul class="doc-move__list" role="listbox" :aria-label="t('work.room.doc.moveDialog.place')">
        <li v-for="row in rows" :key="row.key">
          <button
            type="button"
            role="option"
            class="doc-move__row"
            :class="{
              'doc-move__row--project': row.kind === 'project',
              'is-picked': same(picked, row.spot),
            }"
            :aria-selected="same(picked, row.spot)"
            :disabled="same(row.spot, current)"
            @click="picked = row.spot"
          >
            <UserAvatar
              v-if="row.kind !== 'project'"
              size="22"
              :name="row.teamName"
              :seed="row.teamName"
              :kind="row.kind === 'mine' ? 'person' : 'org'"
            />
            <v-icon v-else size="18" class="doc-move__icon">mdi-folder-outline</v-icon>
            <span class="doc-move__name t-body">{{ row.name }}</span>
            <span v-if="same(row.spot, current)" class="doc-move__here t-meta-read">
              {{ t('work.room.doc.moveDialog.here') }}
            </span>
            <v-icon v-else-if="same(picked, row.spot)" size="18" class="doc-move__check">mdi-check</v-icon>
          </button>
        </li>
        <li v-if="!rows.length" class="doc-move__empty t-body c-muted">{{ t('work.room.doc.moveDialog.none') }}</li>
      </ul>
      <p class="doc-move__readers t-body" :class="pickedRow ? 'c-text' : 'c-muted'" data-testid="doc-move-readers">
        {{ readers || t('work.room.doc.moveDialog.pick') }}
      </p>
      <v-alert v-if="error" type="error" variant="tonal" density="comfortable">{{ error }}</v-alert>
    </div>
  </AdaptiveDialog>
</template>

<style scoped>
.doc-move {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding-top: 8px;
}

.doc-move__list {
  list-style: none;
  margin: 0;
  padding: 4px;
  max-height: 320px;
  overflow-y: auto;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
}

.doc-move__row {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  min-height: 40px;
  padding: 6px 10px;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--text);
  text-align: left;
  cursor: pointer;
  transition: background-color var(--dur-quick) var(--ease-out);
}

.doc-move__row--project {
  padding-left: 40px;
}

.doc-move__row:hover:not(:disabled),
.doc-move__row:focus-visible {
  background: var(--fill);
}

.doc-move__row.is-picked {
  background: var(--fill);
  color: var(--ink);
}

.doc-move__row:disabled {
  cursor: default;
}

.doc-move__icon {
  color: var(--muted);
}

.doc-move__name {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.doc-move__here {
  flex: none;
  color: var(--muted);
}

.doc-move__check {
  flex: none;
  color: var(--ink);
}

.doc-move__empty {
  padding: 12px 10px;
}

.doc-move__readers {
  margin: 0;
}
</style>
