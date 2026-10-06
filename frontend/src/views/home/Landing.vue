<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import LandingFilm from './LandingFilm.vue'
import LandingRoom from './LandingRoom.vue'
import LandingShell from './LandingShell.vue'
import LandingUseCases from './LandingUseCases.vue'

import BrandScene from '@/components/account/brandScene/BrandScene.vue'
import BaseButton from '@/components/base/BaseButton.vue'
import i18n, { t } from '@/i18n'

// The manifesto lights up clause by clause once it is on screen, beside the film
// that shows it happening, and goes dim again once it has left, to replay.
const manifesto = computed(() => [t('publicSite.manifesto1'), t('publicSite.manifesto2'), t('publicSite.manifesto3')])
const manifestoEl = ref<HTMLElement>()
const manifestoLit = ref(false)

const steps = computed(() => [
  { title: t('publicSite.memoryTitle'), body: t('publicSite.memoryBody') },
  { title: t('publicSite.agentsTitle'), body: t('publicSite.agentsBody') },
  { title: t('publicSite.previewTitle'), body: t('publicSite.previewBody') },
  { title: t('publicSite.reviewTitle'), body: t('publicSite.reviewBody') },
])

const resources = computed(() => [
  { icon: 'mdi-creation-outline', title: t('publicSite.modelsTitle'), body: t('publicSite.modelsBody') },
  { icon: 'mdi-cloud-outline', title: t('publicSite.cloudTitle'), body: t('publicSite.cloudBody') },
  { icon: 'mdi-laptop', title: t('publicSite.deviceTitle'), body: t('publicSite.deviceBody') },
])

// The visitor's own system first; the files are served by this site (lib/desktop.ts).

// The line under the slogan keeps its one sentence. 项目 stays put, and the word
// in front of it is selected and typed over, one kind of project after another
// (research, course, company, competition, startup, open source) and back to 真, so every
// frame reads as the whole sentence and each kind reads as a kind of project.
// The kinds are the ones the team's vision and product brief name. It stays on
// the original under reduced motion, and waits out a background tab.
const kinds = computed(() => [
  t('publicSite.heroKind1'),
  t('publicSite.heroKind2'),
  t('publicSite.heroKind3'),
  t('publicSite.heroKind4'),
  t('publicSite.heroKind5'),
  t('publicSite.heroKind6'),
])
const HOME_HOLD_MS = 2800
const HOLD_MS = 1500
const SELECT_MS = 520
const TYPE_MS = 130
const SETTLE_MS = 240
// null shows the home word, so a language switch at rest follows the locale.
const typed = ref<string | null>(null)
const isKind = ref(false)
const selected = ref(false)
const typing = ref(false)
const modBox = ref<HTMLElement>()
const sizer = ref<HTMLElement>()
const sizerText = ref('')
const reducedMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false
let typingTimer: ReturnType<typeof setTimeout> | undefined
// Each run of the cycle holds a number; a newer run, or leaving the page, retires it.
let run = 0

class Retired extends Error {}

// Sizes the box to `text` before it is typed, so 项目 glides aside once and the
// letters fill a gap that is already open instead of drawing over it.
async function openFor(text: string, my: number, caret: boolean) {
  sizerText.value = text
  await nextTick()
  if (my !== run) throw new Retired()
  if (!modBox.value || !sizer.value) return
  const extra = caret ? parseFloat(getComputedStyle(modBox.value).fontSize) * 0.12 : 0
  modBox.value.style.width = `${sizer.value.getBoundingClientRect().width + extra}px`
}

async function cycle(my: number) {
  const wait = (ms: number) =>
    new Promise<void>((resolve, reject) => {
      typingTimer = setTimeout(() => (my === run ? resolve() : reject(new Retired())), ms)
    })
  const retype = async (word: string | null) => {
    const target = word ?? t('publicSite.heroHome')
    await openFor(typed.value ?? t('publicSite.heroHome'), my, false)
    selected.value = true
    await wait(SELECT_MS)
    selected.value = false
    isKind.value = word !== null
    typing.value = true
    typed.value = ''
    await openFor(target, my, true)
    for (let i = 1; i <= target.length; i++) {
      typed.value = target.slice(0, i)
      await wait(TYPE_MS)
    }
    await wait(SETTLE_MS)
    typing.value = false
    typed.value = word
    await openFor(target, my, false)
  }
  for (;;) {
    await wait(HOME_HOLD_MS)
    while (document.hidden) await wait(HOLD_MS)
    for (const kind of kinds.value) {
      await retype(kind)
      await wait(HOLD_MS)
    }
    await retype(null)
  }
}

// Back to the original sentence and a fresh cycle, e.g. after a language switch.
function restartTyping() {
  run += 1
  clearTimeout(typingTimer)
  typed.value = null
  isKind.value = false
  selected.value = false
  typing.value = false
  if (modBox.value) modBox.value.style.width = ''
  if (reducedMotion) return
  cycle(run).catch((error) => {
    if (!(error instanceof Retired)) throw error
  })
}

watch(() => i18n.global.locale.value, restartTyping)

// Which step of the story is in the middle of the screen drives the room.
const step = ref(0)
const stepEls = ref<HTMLElement[]>([])
let observer: IntersectionObserver | null = null
let manifestoObserver: IntersectionObserver | null = null

onMounted(() => {
  observer = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) {
        if (entry.isIntersecting) step.value = Number((entry.target as HTMLElement).dataset.step)
      }
    },
    { rootMargin: '-45% 0px -45% 0px' }
  )
  for (const el of stepEls.value) observer.observe(el)
  manifestoObserver = new IntersectionObserver(
    ([entry]) => {
      if (entry.intersectionRatio >= 0.6) manifestoLit.value = true
      else if (!entry.isIntersecting) manifestoLit.value = false
    },
    { threshold: [0, 0.6] }
  )
  if (manifestoEl.value) manifestoObserver.observe(manifestoEl.value)
  restartTyping()
})

onBeforeUnmount(() => {
  observer?.disconnect()
  manifestoObserver?.disconnect()
  run += 1
  clearTimeout(typingTimer)
})
</script>

<template>
  <LandingShell v-slot="{ entryHref, entryLabel }" page="home">
    <section class="hero">
      <div class="hero-scene">
        <BrandScene scene="neuro" />
      </div>
      <div class="hero-copy">
        <h1 class="hero-title">{{ t('publicSite.slogan') }}</h1>
        <p class="hero-position">
          <span class="visually-hidden">{{ t('publicSite.positioning') }}</span>
          <span ref="sizer" class="hero-mod-sizer" :class="{ 'hero-kind': isKind }" aria-hidden="true">{{
            sizerText
          }}</span>
          <span aria-hidden="true"
            >{{ t('publicSite.heroBefore')
            }}<span ref="modBox" class="hero-mod"
              ><span class="hero-mod-text"
                ><span :class="{ 'hero-kind': isKind, 'hero-selected': selected }">{{
                  typed ?? t('publicSite.heroHome')
                }}</span
                ><span v-if="typing" class="hero-caret" /></span></span
            >{{ t('publicSite.heroAfter') }}</span
          >
        </p>
        <div class="hero-actions">
          <BaseButton :to="entryHref" kind="primary" size="lg" append-icon="mdi-arrow-top-right">
            {{ entryLabel }}
          </BaseButton>
          <router-link class="text-link" to="/solutions">
            {{ t('publicSite.solutionsLink') }}
            <v-icon icon="mdi-arrow-right" size="16" />
          </router-link>
        </div>
      </div>
    </section>

    <section class="manifesto" :aria-label="t('publicSite.manifestoLabel')">
      <p ref="manifestoEl" class="manifesto-text" :class="{ 'manifesto-lit': manifestoLit }">
        <span v-for="(clause, i) in manifesto" :key="i" class="manifesto-clause">{{ clause }}</span>
      </p>
      <!-- The film is in Chinese, with no subtitles yet. -->
      <LandingFilm v-if="i18n.global.locale.value === 'zh-CN'" />
    </section>

    <section id="story" class="story">
      <div class="story-steps">
        <div
          v-for="(item, i) in steps"
          :key="i"
          ref="stepEls"
          class="story-step"
          :class="{ 'story-step-active': step === i }"
          :data-step="i"
        >
          <p class="story-index">{{ String(i + 1).padStart(2, '0') }}</p>
          <h2 class="story-title">{{ item.title }}</h2>
          <p class="story-body">{{ item.body }}</p>
        </div>
      </div>
      <div class="story-stage">
        <div class="story-room">
          <LandingRoom :step="step" />
        </div>
      </div>
    </section>

    <LandingUseCases />

    <section class="resources">
      <div v-for="item in resources" :key="item.title" class="resources-item">
        <v-icon class="resources-icon" :icon="item.icon" size="24" />
        <h3>{{ item.title }}</h3>
        <p>{{ item.body }}</p>
      </div>
      <p class="resources-terms">{{ t('publicSite.resourceTerms') }}</p>
    </section>

    <section class="cta">
      <div class="cta-scene">
        <BrandScene scene="neuro" />
      </div>
      <div class="cta-copy">
        <h2 class="cta-title">{{ t('publicSite.ctaTitle') }}</h2>
        <p class="cta-body">{{ t('publicSite.ctaBody') }}</p>
        <div class="hero-actions">
          <BaseButton :to="entryHref" kind="primary" size="lg" append-icon="mdi-arrow-top-right">
            {{ entryLabel }}
          </BaseButton>
          <router-link class="text-link" to="/solutions">
            {{ t('publicSite.solutionsLink') }}
            <v-icon icon="mdi-arrow-right" size="16" />
          </router-link>
        </div>
      </div>
    </section>
  </LandingShell>
</template>

<style scoped src="./landing.css"></style>
