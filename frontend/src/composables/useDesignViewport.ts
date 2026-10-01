import { computed, type Ref, ref } from 'vue'

export type DesignDevice = 'desktop' | 'tablet' | 'mobile'
export type DesignSize = { width: number; height: number }
export const DESIGN_VIEWPORTS: Record<DesignDevice, DesignSize> = {
  desktop: { width: 1440, height: 900 },
  tablet: { width: 768, height: 1024 },
  mobile: { width: 390, height: 844 },
}

/** CSS viewport dimensions and visual scale are separate: zoom never changes media queries. */
export function useDesignViewport(available: Ref<DesignSize>) {
  const device = ref<DesignDevice>('desktop')
  const zoom = ref(1)
  const fitted = ref(true)
  const viewport = computed(() => DESIGN_VIEWPORTS[device.value])
  const scale = computed(() =>
    fitted.value
      ? Math.max(
          0.1,
          Math.min(1, available.value.width / viewport.value.width, available.value.height / viewport.value.height)
        )
      : zoom.value
  )
  const displayed = computed(() => ({
    width: viewport.value.width * scale.value,
    height: viewport.value.height * scale.value,
  }))
  function setZoom(value: number) {
    if (!Number.isFinite(value)) return
    zoom.value = Math.max(0.1, Math.min(3, value))
    fitted.value = false
  }
  function selectDevice(value: DesignDevice) {
    device.value = value
  }
  function fit() {
    fitted.value = true
  }
  return { device, zoom, fitted, viewport, scale, displayed, setZoom, selectDevice, fit }
}
