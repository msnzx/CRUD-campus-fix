import { useEffect, useState } from 'react'
import { supabase } from './supabase'
import type { Tables } from './database.types'

export interface Lookups {
  statuses: Tables<'statuses'>[]
  priorities: Tables<'priorities'>[]
  departments: Tables<'departments'>[]
  issueTypes: Tables<'issue_types'>[]
  buildings: Tables<'buildings'>[]
  floors: Tables<'floors'>[]
  areaTypes: Tables<'area_types'>[]
}

const EMPTY: Lookups = {
  statuses: [],
  priorities: [],
  departments: [],
  issueTypes: [],
  buildings: [],
  floors: [],
  areaTypes: [],
}

// Lookup tables are small, static, and read on nearly every screen.
// Fetch once per session and share.
let cache: Lookups | null = null
let inflight: Promise<Lookups> | null = null

async function fetchLookups(): Promise<Lookups> {
  const [statuses, priorities, departments, issueTypes, buildings, floors, areaTypes] =
    await Promise.all([
      supabase.from('statuses').select('*'),
      supabase.from('priorities').select('*').order('level'),
      supabase.from('departments').select('*').order('name'),
      supabase.from('issue_types').select('*').order('name'),
      supabase.from('buildings').select('*').order('name'),
      supabase.from('floors').select('*'),
      supabase.from('area_types').select('*').order('name'),
    ])

  return {
    statuses: statuses.data ?? [],
    priorities: priorities.data ?? [],
    departments: (departments.data ?? []).filter((d) => d.active),
    issueTypes: issueTypes.data ?? [],
    buildings: buildings.data ?? [],
    floors: floors.data ?? [],
    areaTypes: areaTypes.data ?? [],
  }
}

export function useLookups(): { lookups: Lookups; ready: boolean } {
  const [lookups, setLookups] = useState<Lookups>(cache ?? EMPTY)
  const [ready, setReady] = useState(cache !== null)

  useEffect(() => {
    if (cache) return
    let active = true
    inflight ??= fetchLookups()
    inflight.then((result) => {
      cache = result
      if (!active) return
      setLookups(result)
      setReady(true)
    })
    return () => {
      active = false
    }
  }, [])

  return { lookups, ready }
}

export function statusIdByName(lookups: Lookups, name: string): number | undefined {
  return lookups.statuses.find((s) => s.name === name)?.status_id
}

export function statusNameById(lookups: Lookups, id: number | null): string | null {
  if (id == null) return null
  return lookups.statuses.find((s) => s.status_id === id)?.name ?? null
}

export function nameById<T extends Record<string, unknown>>(
  rows: T[],
  idKey: keyof T,
  id: number | null | undefined,
  nameKey: keyof T = 'name' as keyof T,
): string | null {
  if (id == null) return null
  const row = rows.find((r) => r[idKey] === id)
  return row ? (row[nameKey] as string) : null
}
