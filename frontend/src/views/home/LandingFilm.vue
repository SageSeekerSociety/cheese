<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'

import film from '@/assets/landing/zhongzhi-film.mp4'
import poster from '@/assets/landing/zhongzhi-film-poster.jpg'
import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'

// The film plays silently in a loop while it is on screen, like the brand scene
// above it (design-system §9.9): still under reduced motion, paused off screen
// and in a background tab. Its soundtrack is cut to the picture, so "with sound"
// starts it over from the top instead of unmuting it mid-way.
const video = ref<HTMLVideoElement>()
const withSound = ref(false)
const inView = ref(false)
const reducedMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false
let observer: IntersectionObserver | null = null

function sync() {
  const el = video.value
  if (!el) return
  const play = inView.value && !document.hidden && (withSound.value || !reducedMotion)
  if (play) el.play()?.catch(() => {})
  else el.pause()
}

function playWithSound() {
  const el = video.value
  if (!el) return
  withSound.value = true
  el.loop = false
  el.muted = false
  el.currentTime = 0
  sync()
}

onMounted(() => {
  observer = new IntersectionObserver(
    ([entry]) => {
      inView.value = entry.isIntersecting
      sync()
    },
    { threshold: 0.4 }
  )
  if (video.value) observer.observe(video.value)
  document.addEventListener('visibilitychange', sync)
})

onBeforeUnmount(() => {
  observer?.disconnect()
  document.removeEventListener('visibilitychange', sync)
})
</script>

<template>
  <section class="film" :aria-label="t('publicSite.filmLabel')">
    <div class="film-frame">
      <video
        ref="video"
        class="film-video"
        :src="film"
        :poster="poster"
        :controls="withSound"
        muted
        loop
        playsinline
        preload="metadata"
      />
      <div v-if="!withSound" class="film-play">
        <BaseButton kind="primary" size="lg" prepend-icon="mdi-volume-high" @click="playWithSound">
          {{ t('publicSite.filmPlay') }} · {{ t('publicSite.filmLength') }}
        </BaseButton>
      </div>
    </div>
  </section>
</template>

<style scoped>
.film {
  padding: 64px var(--gutter);
}

.film-frame {
  position: relative;
  max-width: 1280px;
  margin: 0 auto;
  overflow: hidden;
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
  aspect-ratio: 16 / 9;
}

.film-video {
  display: block;
  width: 100%;
  height: 100%;
  object-fit: cover;
}

/* The film's ground is a fixed warm white in both themes; a solid button is
   the one control that reads on it whichever theme the page is in. */
.film-play {
  position: absolute;
  left: 24px;
  bottom: 24px;
}

@media (max-width: 900px) {
  .film {
    padding: 32px var(--gutter);
  }

  .film-play {
    left: 12px;
    bottom: 12px;
  }
}
</style>
