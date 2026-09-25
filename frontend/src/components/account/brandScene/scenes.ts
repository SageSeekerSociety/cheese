import type { ShaderMountUniforms, ShaderSizingParams } from '@paper-design/shaders'

import colorLogoUrl from '@/assets/brand-scene/logo-color.png?url'
import heatLogoUrl from '@/assets/brand-scene/logo-heat.png?url'
import metalLogoUrl from '@/assets/brand-scene/logo-metal.png?url'

// The brand scenes on the sign-in pages (design-system §9.9), built on Paper
// Shaders. One is drawn at random each time the page is opened.
//
// The logo textures for liquid metal and heat are the library's own
// `toProcessedLiquidMetal` / `toProcessedHeatmap` output, generated once and
// committed, so a phone does not redo that work on every visit. The heatmap
// processor paints onto white, which is why it was fed a dark logo.
//
// Sizes are set so the six scenes with a centered subject carry about the same
// weight: the logo scenes are the reference, and the filled moon, the ring and
// the glowing heat logo are drawn smaller because their mass reads larger.

type Paper = typeof import('@paper-design/shaders')

export type SceneId = 'metal' | 'dither' | 'grain' | 'glass' | 'heat' | 'water' | 'halo' | 'neuro'

export const SCENE_IDS: readonly SceneId[] = ['metal', 'dither', 'grain', 'glass', 'heat', 'water', 'halo', 'neuro']

/** What a scene is painted against. */
export interface SceneGround {
  dark: boolean
  /** The pane's own background, so the scene and the pane meet without a seam. */
  back: string
  /** The short band above the form on a phone, not the tall side pane. */
  narrow: boolean
}

export interface Scene {
  fragment: string
  speed: number
  uniforms: ShaderMountUniforms
  mipmaps?: string[]
  /** Uniforms for a pointer at (x, y), each in -1..1 across the pane; t is seconds since mount. */
  pointer: (x: number, y: number, t: number) => ShaderMountUniforms
  /** The shader holds still and `pointer` animates it instead, every frame. */
  drift?: boolean
}

function sizing(P: Paper, o: ShaderSizingParams = {}): ShaderMountUniforms {
  const s = { ...P.defaultObjectSizing, ...o }
  return {
    u_fit: P.ShaderFitOptions[s.fit],
    u_scale: s.scale,
    u_rotation: s.rotation,
    u_offsetX: s.offsetX,
    u_offsetY: s.offsetY,
    u_originX: s.originX,
    u_originY: s.originY,
    u_worldWidth: s.worldWidth,
    u_worldHeight: s.worldHeight,
  }
}

const IMAGE_SIDE = 1024

// ShaderMount takes image elements, not URLs. Small images are drawn at 1024 on
// their long side, as the library's React wrapper does.
function loadImage(src: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const img = new Image()
    img.onload = () => {
      const { naturalWidth: w, naturalHeight: h } = img
      if (w > 0 && w < IMAGE_SIDE && h < IMAGE_SIDE) {
        const aspect = w / h
        img.width = Math.round(aspect > 1 ? IMAGE_SIDE * aspect : IMAGE_SIDE)
        img.height = Math.round(aspect > 1 ? IMAGE_SIDE : IMAGE_SIDE / aspect)
      }
      resolve(img)
    }
    img.onerror = () => reject(new Error(`could not load ${src}`))
    img.src = src
  })
}

// The noise texture comes back as an image still loading; wait for it, since a
// texture uploaded before its pixels arrive is rejected by WebGL.
function noise(P: Paper): Promise<HTMLImageElement> {
  const img = P.getShaderNoiseTexture()
  if (!img) return Promise.reject(new Error('no noise texture'))
  if (img.complete && img.naturalWidth > 0) return Promise.resolve(img)
  return new Promise((resolve, reject) => {
    img.onload = () => resolve(img)
    img.onerror = () => reject(new Error('could not load the noise texture'))
  })
}

const colors = (P: Paper, list: string[]) => list.map((c) => P.getShaderColorFromString(c))

export const SCENES: Record<SceneId, (P: Paper, g: SceneGround) => Promise<Scene>> = {
  // The logo as slow liquid metal; the pointer turns the light.
  metal: async (P, g) => ({
    fragment: P.liquidMetalFragmentShader,
    speed: 0.4,
    mipmaps: ['u_image'],
    uniforms: {
      u_colorBack: P.getShaderColorFromString(g.back),
      u_colorTint: P.getShaderColorFromString(g.dark ? '#FFB547' : '#FFD08A'),
      u_image: await loadImage(metalLogoUrl),
      u_contour: 0.3,
      u_distortion: 0.05,
      u_softness: 0.35,
      u_repetition: 1.5,
      u_shiftRed: 0.06,
      u_shiftBlue: 0.06,
      u_angle: 70,
      u_isImage: true,
      u_shape: P.LiquidMetalShapes.none,
      ...sizing(P, { scale: 0.62 }),
    },
    pointer: (x, y) => ({ u_angle: 70 + x * 30, u_distortion: 0.05 + Math.abs(y) * 0.03 }),
  }),

  // A dithered moon turning slowly; the pointer shifts it for parallax.
  dither: async (P, g) => ({
    fragment: P.ditheringFragmentShader,
    speed: 0.4,
    uniforms: {
      u_colorBack: P.getShaderColorFromString(g.back),
      u_colorFront: P.getShaderColorFromString(g.dark ? '#F9B233' : '#F57F17'),
      u_shape: P.DitheringShapes.sphere,
      u_type: P.DitheringTypes['4x4'],
      u_pxSize: 2,
      ...sizing(P, { scale: 0.5, fit: 'none' }),
    },
    pointer: (x, y) => ({ u_offsetX: x * 0.04, u_offsetY: y * 0.04 }),
  }),

  // A grainy warm gradient, no figure.
  grain: async (P, g) => ({
    fragment: P.grainGradientFragmentShader,
    speed: 0.5,
    uniforms: {
      u_colorBack: P.getShaderColorFromString(g.back),
      u_colors: colors(P, g.dark ? ['#c4730b', '#F9B233', '#E85D2C'] : ['#F9B233', '#F57F17', '#FFD58A']),
      u_colorsCount: 3,
      u_softness: 0.7,
      u_intensity: 0.15,
      u_noise: 0.5,
      u_shape: P.GrainGradientShapes.wave,
      u_noiseTexture: await noise(P),
      // The wave is drawn for a tall pane; in the short band it is scaled down
      // and tilted so a crest still crosses it.
      ...sizing(P, g.narrow ? { fit: 'none', scale: 0.45, rotation: -8 } : { fit: 'none' }),
    },
    pointer: (x) => ({ u_rotation: x * 12 }),
  }),

  // The color logo behind fluted glass that keeps sliding; the pointer adds to it.
  glass: async (P, g) => ({
    fragment: P.flutedGlassFragmentShader,
    speed: 0,
    drift: true,
    mipmaps: ['u_image'],
    uniforms: {
      u_image: await loadImage(colorLogoUrl),
      u_colorBack: P.getShaderColorFromString(g.back),
      u_colorShadow: P.getShaderColorFromString(g.dark ? '#000000' : '#7A4A12'),
      u_colorHighlight: P.getShaderColorFromString(g.dark ? '#FFE9C2' : '#FFFFFF'),
      u_shadows: 0.25,
      u_size: 0.45,
      u_angle: 0,
      u_distortion: 0.5,
      u_shift: 0,
      u_blur: 0,
      u_edges: 0.25,
      u_stretch: 0,
      u_distortionShape: P.GlassDistortionShapes.prism,
      u_highlights: 0.12,
      u_shape: P.GlassGridShapes.lines,
      u_marginLeft: 0,
      u_marginRight: 0,
      u_marginTop: 0,
      u_marginBottom: 0,
      u_grainMixer: 0.05,
      u_grainOverlay: 0.05,
      ...sizing(P, { fit: 'contain', scale: 0.62 }),
    },
    pointer: (x, y, t) => ({
      u_shift: Math.sin(t * 0.45) * 0.7 + x * 0.5,
      u_distortion: 0.55 + Math.sin(t * 0.23) * 0.2 + y * 0.15,
      u_angle: Math.sin(t * 0.11) * 8,
    }),
  }),

  // The logo through a heat camera: glowing bands flowing inside the shape.
  heat: async (P, g) => ({
    fragment: P.heatmapFragmentShader,
    speed: 0.5,
    mipmaps: ['u_image'],
    uniforms: {
      u_image: await loadImage(heatLogoUrl),
      u_contour: 0.5,
      u_angle: 0,
      u_noise: 0.1,
      u_innerGlow: 0.5,
      u_outerGlow: 0.5,
      u_colorBack: P.getShaderColorFromString(g.back),
      // In light mode the ramp stops short of white, which glared on the pale pane.
      u_colors: colors(
        P,
        g.dark
          ? ['#3a1206', '#7a2a08', '#c2500f', '#E85D2C', '#F57F17', '#F9B233', '#FFE9A8']
          : ['#F6E6CF', '#F2CB8C', '#EFAA55', '#E68A33', '#D26C22', '#AE531B', '#8A4016']
      ),
      u_colorsCount: 7,
      ...sizing(P, { scale: 0.54 }),
    },
    pointer: (x) => ({ u_angle: x * 60 }),
  }),

  // The color logo under water, caustics drifting over it.
  water: async (P, g) => ({
    fragment: P.waterFragmentShader,
    speed: 0.6,
    mipmaps: ['u_image'],
    uniforms: {
      u_image: await loadImage(colorLogoUrl),
      u_colorBack: P.getShaderColorFromString(g.back),
      u_colorHighlight: P.getShaderColorFromString(g.dark ? '#FFE9C2' : '#FFFFFF'),
      u_highlights: 0.08,
      u_layering: 0.5,
      u_waves: 0.3,
      u_edges: 0.8,
      u_caustic: 0.12,
      u_size: 1,
      ...sizing(P, { scale: 0.62 }),
    },
    pointer: (x, y) => {
      const k = Math.min(1, Math.hypot(x, y))
      return { u_waves: 0.3 + k * 0.25, u_caustic: 0.12 + k * 0.1 }
    },
  }),

  // A smoky ring, like a halo round the moon.
  halo: async (P, g) => ({
    fragment: P.smokeRingFragmentShader,
    speed: 0.4,
    uniforms: {
      u_colorBack: P.getShaderColorFromString(g.back),
      u_colors: colors(P, g.dark ? ['#C86A12', '#F9B233', '#FFE9C2'] : ['#E85D2C', '#F57F17', '#F9B233']),
      u_colorsCount: 3,
      u_noiseScale: 3,
      u_thickness: 0.42,
      u_radius: 0.3,
      u_innerShape: 0.7,
      u_noiseIterations: 8,
      u_noiseTexture: await noise(P),
      ...sizing(P, { fit: 'none', scale: 0.61 }),
    },
    pointer: (x, y) => ({ u_offsetX: x * 0.05, u_offsetY: y * 0.05 }),
  }),

  // Slowly growing, interlacing lines of light.
  neuro: async (P, g) => ({
    fragment: P.neuroNoiseFragmentShader,
    speed: 0.45,
    uniforms: {
      u_colorFront: P.getShaderColorFromString(g.dark ? '#FFE9C2' : '#C2570A'),
      u_colorMid: P.getShaderColorFromString(g.dark ? '#F57F17' : '#F9B233'),
      u_colorBack: P.getShaderColorFromString(g.back),
      u_brightness: 0.05,
      u_contrast: 0.3,
      ...sizing(P, { fit: 'none', scale: 1 }),
    },
    pointer: (x, y) => ({ u_rotation: x * 10, u_offsetY: y * 0.04 }),
  }),
}

const LAST_KEY = 'cheese.brandScene.last'
let chosen: SceneId | null = null

/**
 * The scene for this page load: drawn at random, never the one this browser
 * showed last time, then kept while the person moves between the account
 * pages. Opening or reloading the page draws again.
 */
export function sceneForThisPage(random: () => number = Math.random): SceneId {
  if (chosen) return chosen
  let last: string | null = null
  try {
    last = localStorage.getItem(LAST_KEY)
  } catch {
    // storage unavailable — any scene will do
  }
  const pool = SCENE_IDS.filter((id) => id !== last)
  chosen = pool[Math.floor(random() * pool.length)]
  try {
    localStorage.setItem(LAST_KEY, chosen)
  } catch {
    // as above
  }
  return chosen
}
