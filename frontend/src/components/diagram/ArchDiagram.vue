<script setup lang="ts">
/**
 * ArchDiagram.vue — 架构图 / 数据流图。
 *
 * 泳道（lane）是一层，层内节点横排，层与层之间用带箭头的曲线连。
 * 连线方向由节点实际的相对位置决定（见 useMeasuredBoxes.connect）：
 * 跨层走竖边、同层走横边，跟人看着图的直觉一致。
 *
 * 跟 ErDiagram 共用度量和几何那层，只是节点长得不一样。
 */
import type { DiagramArch, DiagramEdge } from '@/lib/diagramSpec'

import { computed, ref } from 'vue'

import { useMeasuredBoxes } from './useMeasuredBoxes'

import { arrowPath, bezierPoint, connect, curvePath } from '@/lib/diagramGeometry'

const props = defineProps<{
  diagram: DiagramArch
}>()

interface Wire {
  key: string
  line: string
  head: string
  from: string
  to: string
  label: { x: number; y: number; text: string } | null
}

const { host, boxes, size, setNodeRef } = useMeasuredBoxes(() => [props.diagram])

const active = ref<string | null>(null)

const known = computed(() => {
  const ids = new Set<string>()
  for (const lane of props.diagram.lanes) for (const node of lane.nodes) ids.add(node.id)
  return ids
})

const wires = computed<Wire[]>(() => {
  const box = boxes.value
  const out: Wire[] = []
  props.diagram.edges.forEach((edge: DiagramEdge, index) => {
    const a = box[edge.from]
    const b = box[edge.to]
    if (!a || !b) return
    const [p0, p1, p2, p3] = connect(a, b)
    const mid = bezierPoint(p0, p1, p2, p3, 0.5)
    out.push({
      key: `${edge.from}-${edge.to}-${index}`,
      line: curvePath(p0, p1, p2, p3),
      head: arrowPath(p2, p3),
      from: edge.from,
      to: edge.to,
      label: edge.label ? { x: mid.x, y: mid.y - 6, text: edge.label } : null,
    })
  })
  return out
})

function wireState(wire: Wire): string {
  if (!active.value) return ''
  return wire.from === active.value || wire.to === active.value ? 'is-on' : 'is-off'
}

function connected(id: string): boolean {
  return props.diagram.edges.some(
    (edge) => (edge.from === active.value && edge.to === id) || (edge.to === active.value && edge.from === id)
  )
}

function nodeState(id: string): string {
  if (!active.value || active.value === id) return ''
  return connected(id) ? 'is-linked' : 'is-dim'
}

/** 图上找不到的节点引用会让连线凭空消失，这里是给评审用的自查口子。 */
const dangling = computed(() => {
  const bad: string[] = []
  for (const edge of props.diagram.edges) {
    if (!known.value.has(edge.from)) bad.push(`${edge.from} → ${edge.to}`)
    if (!known.value.has(edge.to)) bad.push(`${edge.from} → ${edge.to}`)
  }
  return bad
})

const summary = computed(() => props.diagram.edges.map((edge) => `${edge.from} 到 ${edge.to}`).join('；'))
</script>

<template>
  <figure class="arch">
    <figcaption v-if="diagram.title || diagram.subtitle" class="arch__caption">
      <h3 v-if="diagram.title" class="arch__title">{{ diagram.title }}</h3>
      <p v-if="diagram.subtitle" class="arch__sub">{{ diagram.subtitle }}</p>
    </figcaption>

    <div ref="host" class="arch__canvas" role="img" :aria-label="summary">
      <svg class="arch__wires" :width="size.w" :height="size.h" :viewBox="`0 0 ${size.w} ${size.h}`">
        <g v-for="wire in wires" :key="wire.key" class="arch-wire" :class="wireState(wire)">
          <path class="arch-wire__hit" :d="wire.line" />
          <path class="arch-wire__line" :d="wire.line" />
          <path class="arch-wire__head" :d="wire.head" />
          <text v-if="wire.label" class="arch-wire__label" :x="wire.label.x" :y="wire.label.y">
            {{ wire.label.text }}
          </text>
        </g>
      </svg>

      <div class="arch__lanes">
        <section v-for="lane in diagram.lanes" :key="lane.id" class="arch__lane">
          <h4 class="arch__lane-title">{{ lane.title }}</h4>
          <div class="arch__lane-nodes">
            <article
              v-for="node in lane.nodes"
              :key="node.id"
              :ref="(el) => setNodeRef(node.id, el)"
              class="arch-node"
              :class="nodeState(node.id)"
              tabindex="0"
              @mouseenter="active = node.id"
              @mouseleave="active = null"
              @focusin="active = node.id"
              @focusout="active = null"
            >
              <span class="arch-node__rail" :class="`is-${node.kind ?? 'plain'}`" />
              <span class="arch-node__body">
                <span class="arch-node__label">{{ node.label }}</span>
                <span v-if="node.sub" class="arch-node__sub">{{ node.sub }}</span>
              </span>
            </article>
          </div>
        </section>
      </div>
    </div>

    <p v-if="dangling.length" class="arch__broken">图里有连线指向不存在的节点：{{ dangling.join('、') }}</p>

    <p class="arch__legend">
      <span class="arch__legend-item"><span class="arch-rail is-harness" /> 提交端</span>
      <span class="arch__legend-item"><span class="arch-rail is-tool" /> 上报契约</span>
      <span class="arch__legend-item"><span class="arch-rail is-service" /> 服务端</span>
      <span class="arch__legend-item"><span class="arch-rail is-store" /> 存储</span>
      <span class="arch__legend-item"><span class="arch-rail is-queue" /> 投递</span>
      <span class="arch__legend-item"><span class="arch-rail is-ui" /> 界面</span>
      <span class="arch__legend-item"><span class="arch-rail is-note" /> 还没接上的口子</span>
      <span class="arch__legend-item">悬停任意节点，高亮它的上下游</span>
    </p>
  </figure>
</template>

<style scoped>
.arch {
  margin: 0;
}

.arch__caption {
  margin-bottom: 16px;
}

.arch__title {
  margin-bottom: 4px;
  font-size: 15px;
  font-weight: 600;
  color: var(--text);
}

.arch__sub {
  font-size: 13px;
  line-height: 1.6;
  color: var(--muted);
}

.arch__canvas {
  position: relative;
  overflow-x: auto;
  padding: 4px;
}

.arch__wires {
  position: absolute;
  top: 0;
  left: 0;
  overflow: visible;
  pointer-events: none;
}

.arch-wire__hit {
  fill: none;
  stroke: transparent;
  stroke-width: 14;
  pointer-events: stroke;
}

.arch-wire__line {
  fill: none;
  stroke: var(--line-2);
  stroke-width: 1.5;
  transition: stroke 0.2s ease;
}

.arch-wire__head {
  fill: var(--line-2);
  transition: fill 0.2s ease;
}

.arch-wire__label {
  font-size: 11px;
  text-anchor: middle;
  fill: var(--faint);
}

.arch-wire.is-off {
  opacity: 0.16;
}

.arch-wire.is-on .arch-wire__line {
  stroke: var(--v-theme-primary);
}

.arch-wire.is-on .arch-wire__head {
  fill: var(--v-theme-primary);
}

.arch-wire.is-on .arch-wire__label {
  fill: var(--v-theme-primary);
}

/* 跟 ER 图同一个理由：不换行，溢出就整块横向滚动。每一层必须留在同一行上，
   层内一折行，同一条数据流的线就会横穿到别的层去。滚动放在画布上而不是每层
   各自滚 —— 否则各层会滚到不同位置，竖着对不齐。 */
.arch__lanes {
  display: flex;
  flex-direction: column;
  gap: 28px;
  width: max-content;
}

.arch__lane {
  display: flex;
  align-items: flex-start;
  gap: 20px;
}

.arch__lane-title {
  width: 76px;
  flex: 0 0 auto;
  padding-top: 11px;
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.06em;
  line-height: 1.5;
  color: var(--faint);
}

.arch__lane-nodes {
  display: flex;
  align-items: flex-start;
  gap: 18px;
  flex: 0 0 auto;
  flex-wrap: nowrap;
}

/* 定宽 150px × 六个节点 + 五个 18px 间距 + 96px 的层名栏 = 1086，塞得进 1100
   的宽容器。字进不去的就让它断词换行，别把盒子撑开 —— 盒子一宽，整层就要滚。 */
.arch-node {
  display: flex;
  align-items: stretch;
  gap: 9px;
  width: 150px;
  flex: 0 0 auto;
  padding: 9px 12px 9px 0;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  transition:
    border-color 0.2s ease,
    opacity 0.2s ease;
}

.arch-node:hover,
.arch-node:focus-visible {
  border-color: var(--v-theme-primary);
  outline: none;
}

.arch-node.is-linked {
  border-color: var(--line-2);
}

.arch-node.is-dim {
  opacity: 0.35;
}

.arch-node__rail {
  width: 3px;
  flex: 0 0 auto;
  background: var(--line-2);
  border-radius: var(--radius-sm);
}

/* 只有上报契约那一层用琥珀 —— 一屏一个主色的规矩，图里也照办：琥珀色标的是
   「所有入口最后都收敛到这里」。其余各层用中性色阶区分，不另开颜色。 */
.arch-node__rail.is-harness {
  background: var(--text);
}

.arch-node__rail.is-tool {
  background: var(--v-theme-primary);
}

.arch-node__rail.is-service {
  background: var(--ok);
}

.arch-node__rail.is-store {
  background: var(--faint);
}

.arch-node__rail.is-queue {
  background: var(--muted);
}

.arch-node__rail.is-ui {
  background: var(--line-2);
}

.arch-node__rail.is-note {
  background: var(--fill-2);
}

.arch-node__body {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.arch-node__label {
  font-size: 13px;
  font-weight: 600;
  line-height: 1.4;
  color: var(--text);
  overflow-wrap: anywhere;
}

.arch-node__sub {
  font-size: 11px;
  line-height: 1.5;
  color: var(--muted);
  overflow-wrap: anywhere;
}

.arch__broken {
  padding: 8px 10px;
  margin-top: 14px;
  font-size: 12px;
  color: var(--ok-ink);
  background: var(--ok-wash);
  border-radius: var(--radius-sm);
}

.arch__legend {
  display: flex;
  align-items: center;
  gap: 18px;
  flex-wrap: wrap;
  padding-top: 16px;
  margin-top: 20px;
  font-size: 11px;
  color: var(--faint);
  border-top: 1px solid var(--line);
}

.arch__legend-item {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.arch-rail {
  width: 3px;
  height: 12px;
  background: var(--line-2);
  border-radius: var(--radius-sm);
}

.arch-rail.is-harness {
  background: var(--text);
}

.arch-rail.is-tool {
  background: var(--v-theme-primary);
}

.arch-rail.is-service {
  background: var(--ok);
}

.arch-rail.is-store {
  background: var(--faint);
}

.arch-rail.is-queue {
  background: var(--muted);
}

.arch-rail.is-ui {
  background: var(--line-2);
}

.arch-rail.is-note {
  background: var(--fill-2);
}
</style>
