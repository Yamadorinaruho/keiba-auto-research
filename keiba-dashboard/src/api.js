const API = import.meta.env.DEV
  ? 'http://localhost:8001'
  : (import.meta.env.VITE_API_URL ?? '')

export async function fetchAPI(path, params = {}) {
  const qs = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== '') qs.set(k, v)
  }
  const url = qs.toString() ? `${API}${path}?${qs}` : `${API}${path}`
  const res = await fetch(url)
  if (!res.ok) throw new Error(`API error: ${res.status}`)
  return res.json()
}

export function filtersToParams(filters) {
  const p = {}
  if (filters.years?.length) p.year = filters.years.join(',')
  if (filters.venues?.length) p.venue = filters.venues.join(',')
  if (filters.surfaces?.length) p.surface = filters.surfaces.join(',')
  if (filters.classes?.length) p['class'] = filters.classes.join(',')
  if (filters.conditions?.length) p.condition = filters.conditions.join(',')
  if (filters.genders?.length) p.gender = filters.genders.join(',')
  return p
}
