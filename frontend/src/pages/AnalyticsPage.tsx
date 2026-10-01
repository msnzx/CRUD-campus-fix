import { useEffect, useMemo, useState } from 'react'
import { supabase } from '../lib/supabase'
import { useLookups, nameById, statusNameById } from '../lib/useLookups'
import { Empty, ErrorNote, Loading } from '../components/ui'
import { OPEN_STATUSES, STATUS } from '../lib/domain'
import type { Tables } from '../lib/database.types'

/**
 * Every number here is computed from the same ticket rows the operational
 * screens use, under the same RLS. There is no separate metrics write path,
 * so a figure on this page cannot drift from the queue it describes.
 */

interface Bucket {
  label: string
  count: number
}

/**
 * Single-series magnitude comparison: one hue, sorted descending, value
 * labelled directly on every row. Colour carries no identity here — the row
 * label does — so there is no categorical palette and nothing to confuse a
 * colourblind reader. Bars are a recessive track with a rounded data end.
 */
function BarList({ title, data, empty }: { title: string; data: Bucket[]; empty: string }) {
  const max = Math.max(1, ...data.map((d) => d.count))
  return (
    <div className="card">
      <h3>{title}</h3>
      {data.length === 0 ? (
        <p className="muted small">{empty}</p>
      ) : (
        data.map((d) => (
          <div className="bar-row" key={d.label} title={`${d.label}: ${d.count}`}>
            <div className="bar-label" title={d.label}>{d.label}</div>
            <div className="bar-track">
              <div className="bar-fill" style={{ width: `${(d.count / max) * 100}%` }} />
            </div>
            <div className="bar-value">{d.count}</div>
          </div>
        ))
      )}
    </div>
  )
}

/**
 * Change over time: one column per week, oldest on the left, one hue. The
 * busiest week and the latest week are labelled; every column has a tooltip.
 */
function WeeklyColumns({ title, data }: { title: string; data: Bucket[] }) {
  const max = Math.max(1, ...data.map((d) => d.count))
  const peak = data.reduce((best, d, i) => (d.count > data[best].count ? i : best), 0)
  return (
    <div className="card">
      <h3>{title}</h3>
      <div className="cols" role="img" aria-label={`${title}: ${data.map((d) => `${d.label} ${d.count}`).join(', ')}`}>
        {data.map((d, i) => (
          <div className="col" key={d.label} title={`Week of ${d.label}: ${d.count} tickets`}>
            <div className="col-value">{i === peak || i === data.length - 1 ? d.count : ''}</div>
            <div className="col-bar" style={{ height: `${(d.count / max) * 100}%` }} />
          </div>
        ))}
      </div>
      <div className="cols-axis">
        <span>{data[0]?.label}</span>
        <span>{data[data.length - 1]?.label}</span>
      </div>
    </div>
  )
}

function weeklyVolume(tickets: Tables<'tickets'>[], weeks = 12): Bucket[] {
  const day = 86_400_000
  const now = new Date()
  // Weeks start on Monday, local time.
  const start = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  start.setDate(start.getDate() - ((start.getDay() + 6) % 7) - (weeks - 1) * 7)
  const buckets: Bucket[] = Array.from({ length: weeks }, (_, i) => ({
    label: new Date(start.getTime() + i * 7 * day).toLocaleDateString(undefined, {
      month: 'short',
      day: 'numeric',
    }),
    count: 0,
  }))
  for (const t of tickets) {
    const i = Math.floor((new Date(t.created_at).getTime() - start.getTime()) / (7 * day))
    if (i >= 0 && i < weeks) buckets[i].count++
  }
  return buckets
}

function Stat({ value, label }: { value: string | number; label: string }) {
  return (
    <div className="stat">
      <div className="value">{value}</div>
      <div className="label">{label}</div>
    </div>
  )
}

function tally(rows: string[]): Bucket[] {
  const counts = new Map<string, number>()
  for (const r of rows) counts.set(r, (counts.get(r) ?? 0) + 1)
  return [...counts.entries()]
    .map(([label, count]) => ({ label, count }))
    .sort((a, b) => b.count - a.count)
}

export default function AnalyticsPage() {
  const { lookups, ready } = useLookups()
  const [tickets, setTickets] = useState<Tables<'tickets'>[] | null>(null)
  const [buildingByTicket, setBuildingByTicket] = useState<Record<number, string>>({})
  const [placeByTicket, setPlaceByTicket] = useState<Record<number, string>>({})
  const [error, setError] = useState<unknown>(null)

  useEffect(() => {
    supabase
      .from('tickets')
      .select('*')
      .is('deleted_at', null)
      .limit(5000)
      .then(async ({ data, error }) => {
        if (error) {
          setError(error)
          return
        }
        setTickets(data)

        const locationIds = [...new Set(data.map((t) => t.location_id).filter(Boolean))] as number[]
        if (locationIds.length === 0) return
        const { data: locs } = await supabase
          .from('locations')
          .select('location_id, room_number, floors(buildings(name))')
          .in('location_id', locationIds)

        const byLocation = new Map<number, string>()
        const placeByLocation = new Map<number, string>()
        for (const l of locs ?? []) {
          const floor = l.floors as unknown as { buildings: { name: string } | null } | null
          const building = floor?.buildings?.name
          if (building) byLocation.set(l.location_id, building)
          // A "place" is building + room: the grain at which a repeat fault
          // points to something worth fixing properly.
          if (building && l.room_number) {
            placeByLocation.set(l.location_id, `${building} · Room ${l.room_number}`)
          }
        }
        const map: Record<number, string> = {}
        const places: Record<number, string> = {}
        for (const t of data) {
          if (t.location_id && byLocation.has(t.location_id)) {
            map[t.ticket_id] = byLocation.get(t.location_id)!
          }
          if (t.location_id && placeByLocation.has(t.location_id)) {
            places[t.ticket_id] = placeByLocation.get(t.location_id)!
          }
        }
        setBuildingByTicket(map)
        setPlaceByTicket(places)
      })
  }, [])

  const metrics = useMemo(() => {
    if (!tickets) return null

    const statusOf = (t: Tables<'tickets'>) => statusNameById(lookups, t.status_id)

    const open = tickets.filter((t) => {
      const s = statusOf(t)
      return s && OPEN_STATUSES.includes(s)
    })
    const resolved = tickets.filter((t) => t.resolved_at)

    const resolutionHours = resolved
      .map((t) => (new Date(t.resolved_at!).getTime() - new Date(t.created_at).getTime()) / 3_600_000)
      .filter((h) => h >= 0)

    const avgHours = resolutionHours.length
      ? resolutionHours.reduce((a, b) => a + b, 0) / resolutionHours.length
      : null

    // Aging: open tickets untouched for more than seven days.
    const aging = open.filter(
      (t) => (Date.now() - new Date(t.created_at).getTime()) / 86_400_000 > 7,
    )

    return {
      total: tickets.length,
      open: open.length,
      resolved: resolved.length,
      aging: aging.length,
      emergencies: tickets.filter((t) => t.is_emergency).length,
      needsReview: tickets.filter((t) => statusOf(t) === STATUS.NEEDS_REVIEW).length,
      avgHours,
      byDepartment: tally(
        tickets.map(
          (t) => nameById(lookups.departments, 'department_id', t.department_id) ?? 'Unrouted',
        ),
      ),
      byCategory: tally(
        tickets.map(
          (t) => nameById(lookups.issueTypes, 'issue_type_id', t.issue_type_id) ?? 'Uncategorised',
        ),
      ).slice(0, 10),
      byStatus: tally(tickets.map((t) => statusOf(t) ?? 'Unknown')),
      byBuilding: tally(
        tickets.map((t) => buildingByTicket[t.ticket_id] ?? 'No location given'),
      ),
      weekly: weeklyVolume(tickets),
      recurring: tally(
        tickets.map((t) => placeByTicket[t.ticket_id]).filter((p): p is string => !!p),
      )
        .filter((b) => b.count >= 2)
        .slice(0, 10),
    }
  }, [tickets, lookups, buildingByTicket, placeByTicket])

  if (error) return <div className="content"><ErrorNote error={error} /></div>
  if (!tickets || !ready || !metrics) return <Loading />

  if (metrics.total === 0) {
    return (
      <div className="content">
        <h1>Analytics</h1>
        <Empty
          title="No ticket data yet"
          hint="Metrics appear here once tickets exist. Submit one to see this populate."
        />
      </div>
    )
  }

  return (
    <div className="content">
      <h1>Analytics</h1>
      <p className="muted small">
        Computed live from ticket and history data, filtered by your access level.
      </p>

      <div className="stat-grid">
        <Stat value={metrics.total} label="Total tickets" />
        <Stat value={metrics.open} label="Open" />
        <Stat value={metrics.resolved} label="Resolved" />
        <Stat
          value={metrics.avgHours === null ? '—' : `${metrics.avgHours.toFixed(1)}h`}
          label="Avg. resolution time"
        />
        <Stat value={metrics.aging} label="Aging over 7 days" />
        <Stat value={metrics.needsReview} label="Awaiting review" />
        <Stat value={metrics.emergencies} label="Flagged emergency" />
      </div>

      <WeeklyColumns title="New tickets per week (last 12 weeks)" data={metrics.weekly} />
      <BarList title="Tickets by department" data={metrics.byDepartment} empty="No departments assigned yet." />
      <BarList title="Tickets by status" data={metrics.byStatus} empty="No statuses recorded." />
      <BarList title="Top categories" data={metrics.byCategory} empty="No categories assigned yet." />
      <BarList title="Tickets by building" data={metrics.byBuilding} empty="No locations recorded." />
      <BarList
        title="Recurring locations (2+ tickets in the same room)"
        data={metrics.recurring}
        empty="No room has had more than one ticket."
      />
    </div>
  )
}
