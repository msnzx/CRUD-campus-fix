import type { Tables } from './database.types'

/**
 * Reversible browser-only building inference. Building GPS centroids are
 * intentionally stored on the existing buildings table instead of attaching a
 * person's live coordinates to a ticket.
 */
export const AUTO_BUILDING_MAX_DISTANCE_METERS = 120

export type NearbyBuilding = {
  building: Tables<'buildings'>
  distanceMeters: number
}

function distanceInMeters(
  first: [number, number],
  second: [number, number],
): number {
  const radius = 6_371_000
  const toRadians = (degrees: number) => (degrees * Math.PI) / 180
  const latitudeDelta = toRadians(second[0] - first[0])
  const longitudeDelta = toRadians(second[1] - first[1])
  const a =
    Math.sin(latitudeDelta / 2) ** 2 +
    Math.cos(toRadians(first[0])) * Math.cos(toRadians(second[0])) * Math.sin(longitudeDelta / 2) ** 2
  return radius * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a))
}

export function findNearestBuilding(
  buildings: Tables<'buildings'>[],
  position: [number, number],
): NearbyBuilding | null {
  let closest: NearbyBuilding | null = null
  for (const building of buildings) {
    if (building.latitude == null || building.longitude == null) continue
    const distanceMeters = distanceInMeters(position, [Number(building.latitude), Number(building.longitude)])
    if (!closest || distanceMeters < closest.distanceMeters) {
      closest = { building, distanceMeters }
    }
  }
  return closest
}
