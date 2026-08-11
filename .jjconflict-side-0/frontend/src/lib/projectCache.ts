import type { Project } from '@/cx_types'

const CACHE_PREFIX = 'cheesex.projects.v1:'
const MAX_AGE_MS = 24 * 60 * 60 * 1000

interface CachedProjects {
  savedAt: number
  projects: Project[]
}

function key(handle: string): string | null {
  const normalized = handle.trim()
  return normalized ? `${CACHE_PREFIX}${encodeURIComponent(normalized)}` : null
}

function validProject(value: unknown): value is Project {
  if (typeof value !== 'object' || value === null) return false
  const candidate = value as { id?: unknown; name?: unknown }
  return typeof candidate.id === 'string' && typeof candidate.name === 'string'
}

export function loadCachedProjects(handle: string, now: number = Date.now()): Project[] {
  const storageKey = key(handle)
  if (!storageKey || typeof sessionStorage === 'undefined') return []
  try {
    const raw = sessionStorage.getItem(storageKey)
    if (!raw) return []
    const cached = JSON.parse(raw) as Partial<CachedProjects>
    if (
      typeof cached.savedAt !== 'number' ||
      now - cached.savedAt > MAX_AGE_MS ||
      !Array.isArray(cached.projects) ||
      !cached.projects.every(validProject)
    ) {
      sessionStorage.removeItem(storageKey)
      return []
    }
    return cached.projects
  } catch {
    sessionStorage.removeItem(storageKey)
    return []
  }
}

export function saveCachedProjects(handle: string, projects: Project[], now: number = Date.now()): void {
  const storageKey = key(handle)
  if (!storageKey || typeof sessionStorage === 'undefined') return
  try {
    const cached: CachedProjects = { savedAt: now, projects }
    sessionStorage.setItem(storageKey, JSON.stringify(cached))
  } catch {
    // Storage can be disabled or full. The in-memory list still remains valid.
  }
}
