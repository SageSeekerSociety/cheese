<script setup lang="ts">
// Discord/Slack-style project rail: the far-left vertical strip where each
// project is one small icon. Clicking switches the whole app's project context
// (the caller keeps the current page kind). This is the primary project
// switcher — the old app-bar dropdown is gone.
//
// Design language: light + neutral (NOT a dark Discord clone). Icons are
// squircles on --fill; the ONE amber is the active project (wash bg + accent
// ring + left pill). Tooltip carries the full name.
import { computed } from 'vue'
import type { Project } from '../types'

const props = defineProps<{
  projects: Project[]
  currentProjectId: string | null
  // Which org-level surface is active ('spaces' | 'market' | null) — the rail
  // bottom cluster highlights it.
  orgSurface?: string | null
}>()

const emit = defineEmits<{
  (e: 'pick', id: string): void
  (e: 'home'): void
  (e: 'create'): void
  (e: 'org', surface: 'spaces' | 'market'): void
}>()

// One glyph per project: the first *grapheme* of the name, so an emoji-leading
// name ("🚀 火箭") shows the emoji and a CJK name ("知是") shows one character.
// Pure display slicing (Rule 4 fine: structural, not semantic extraction).
const seg =
  typeof Intl !== 'undefined' && 'Segmenter' in Intl
    ? new Intl.Segmenter('zh', { granularity: 'grapheme' })
    : null

function glyph(name: string): string {
  const trimmed = name.trim()
  if (!trimmed) return '·'
  if (seg) {
    const first = seg.segment(trimmed)[Symbol.iterator]().next()
    return first.done ? '·' : first.value.segment
  }
  return trimmed.slice(0, 1)
}

const items = computed(() =>
  props.projects.map((p) => ({
    id: p.id,
    name: p.name,
    glyph: glyph(p.name),
    active: p.id === props.currentProjectId,
  })),
)

</script>

<template>
  <nav class="project-rail" aria-label="项目切换">
    <!-- 知是 home: back to the workspace root -->
    <v-tooltip location="right" text="工作台">
      <template #activator="{ props: tip }">
        <button class="rail-home" v-bind="tip" @click="emit('home')">
          <slot name="home-icon" />
        </button>
      </template>
    </v-tooltip>

    <div class="rail-divider" />

    <div class="rail-scroll">
      <v-tooltip
        v-for="item in items"
        :key="item.id"
        location="right"
        :text="item.name"
      >
        <template #activator="{ props: tip }">
          <button
            class="rail-item"
            :class="{ active: item.active }"
            v-bind="tip"
            @click="emit('pick', item.id)"
          >
            <span class="rail-pill" aria-hidden="true" />
            <span class="rail-glyph">{{ item.glyph }}</span>
          </button>
        </template>
      </v-tooltip>
    </div>

    <!-- 组织面（项目之上的一层）: 机构看板 / 市场 — Discord 的 explore 位。 -->
    <div class="rail-divider" />
    <v-tooltip location="right" text="机构看板">
      <template #activator="{ props: tip }">
        <button
          class="rail-item rail-org"
          :class="{ active: orgSurface === 'spaces' }"
          v-bind="tip"
          @click="emit('org', 'spaces')"
        >
          <span class="rail-pill" aria-hidden="true" />
          <v-icon size="18">mdi-domain</v-icon>
        </button>
      </template>
    </v-tooltip>
    <v-tooltip location="right" text="市场">
      <template #activator="{ props: tip }">
        <button
          class="rail-item rail-org"
          :class="{ active: orgSurface === 'market' }"
          v-bind="tip"
          @click="emit('org', 'market')"
        >
          <span class="rail-pill" aria-hidden="true" />
          <v-icon size="18">mdi-storefront-outline</v-icon>
        </button>
      </template>
    </v-tooltip>

    <v-tooltip location="right" text="新建项目">
      <template #activator="{ props: tip }">
        <button class="rail-item rail-add" v-bind="tip" @click="emit('create')">
          <v-icon size="18">mdi-plus</v-icon>
        </button>
      </template>
    </v-tooltip>
  </nav>
</template>

<style scoped>
.project-rail {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  height: 100%;
  padding: 10px 0 12px;
  background: var(--surface);
  border-right: 1px solid var(--line);
  overflow: hidden;
}

.rail-home {
  display: grid;
  place-items: center;
  width: 40px;
  height: 40px;
  border: 0;
  border-radius: 14px;
  background: transparent;
  cursor: pointer;
  transition: background 120ms ease;
}
.rail-home:hover {
  background: var(--fill);
}

.rail-divider {
  width: 24px;
  height: 1px;
  background: var(--line-2);
  margin: 2px 0 4px;
}

.rail-scroll {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  overflow-x: hidden;
  /* room for the left pill without horizontal scroll */
  width: 100%;
  padding: 2px 0;
  scrollbar-width: none;
}
.rail-scroll::-webkit-scrollbar {
  display: none;
}

.rail-item {
  position: relative;
  display: grid;
  place-items: center;
  width: 40px;
  height: 40px;
  flex: 0 0 auto;
  border: 0;
  border-radius: 14px;
  background: var(--fill);
  color: var(--ink);
  cursor: pointer;
  transition:
    border-radius 120ms ease,
    background 120ms ease,
    box-shadow 120ms ease;
}
.rail-item:hover {
  border-radius: 10px;
  background: var(--fill-2);
}
.rail-item.active {
  border-radius: 10px;
  background: var(--accent-wash);
  color: var(--accent-ink);
  box-shadow: inset 0 0 0 1.5px var(--accent);
}

.rail-glyph {
  font-size: 15px;
  font-weight: 600;
  line-height: 1;
  font-family: var(--font-sans);
}

/* Discord-signature left pill: grows in on hover, full on active. */
.rail-pill {
  position: absolute;
  left: -12px;
  top: 50%;
  translate: 0 -50%;
  width: 3px;
  height: 0;
  border-radius: 2px;
  background: var(--accent);
  transition: height 130ms ease;
}
.rail-item:hover .rail-pill {
  height: 10px;
}
.rail-item.active .rail-pill {
  height: 22px;
}

.rail-add {
  background: transparent;
  color: var(--muted);
  box-shadow: inset 0 0 0 1px var(--line-2);
}
.rail-add:hover {
  color: var(--accent-ink);
  background: var(--accent-wash);
  box-shadow: inset 0 0 0 1px var(--accent);
}

/* 组织面图标: quieter than project squircles (icon on transparent). */
.rail-org {
  background: transparent;
  color: var(--muted);
}
.rail-org:hover {
  background: var(--fill);
  color: var(--ink);
}
.rail-org.active {
  background: var(--accent-wash);
  color: var(--accent-ink);
  box-shadow: none;
}
</style>
