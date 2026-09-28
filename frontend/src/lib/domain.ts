// Status and role names come from seeded lookup tables. These constants exist
// so a typo becomes a compile error rather than a silently empty query.
// They are names, never ids: ids are generated and differ per environment.

export const STATUS = {
  NEW: 'NEW',
  AI_PROCESSING: 'AI_PROCESSING',
  NEEDS_REVIEW: 'NEEDS_REVIEW',
  ASSIGNED: 'ASSIGNED',
  IN_PROGRESS: 'IN_PROGRESS',
  WAITING_FOR_USER: 'WAITING_FOR_USER',
  RESOLVED: 'RESOLVED',
  CLOSED: 'CLOSED',
  REOPENED: 'REOPENED',
} as const

export type StatusName = (typeof STATUS)[keyof typeof STATUS]

export const ROLE = {
  STUDENT: 'STUDENT',
  DEPARTMENT_STAFF: 'DEPARTMENT_STAFF',
  DEPARTMENT_ADMIN: 'DEPARTMENT_ADMIN',
  SYSTEM_ADMIN: 'SYSTEM_ADMIN',
} as const

export type RoleName = (typeof ROLE)[keyof typeof ROLE]

export const STAFF_ROLES: RoleName[] = [
  ROLE.DEPARTMENT_STAFF,
  ROLE.DEPARTMENT_ADMIN,
  ROLE.SYSTEM_ADMIN,
]

export const ADMIN_ROLES: RoleName[] = [ROLE.DEPARTMENT_ADMIN, ROLE.SYSTEM_ADMIN]

// Legal status transitions, enforced in the UI. The database and the service
// layer enforce this independently; this exists so staff aren't offered a
// button that will fail.
export const ALLOWED_TRANSITIONS: Record<string, StatusName[]> = {
  [STATUS.NEW]: [STATUS.NEEDS_REVIEW, STATUS.ASSIGNED],
  [STATUS.AI_PROCESSING]: [STATUS.NEEDS_REVIEW, STATUS.ASSIGNED],
  [STATUS.NEEDS_REVIEW]: [STATUS.ASSIGNED],
  [STATUS.ASSIGNED]: [STATUS.IN_PROGRESS, STATUS.NEEDS_REVIEW],
  [STATUS.IN_PROGRESS]: [STATUS.WAITING_FOR_USER, STATUS.RESOLVED],
  [STATUS.WAITING_FOR_USER]: [STATUS.IN_PROGRESS, STATUS.RESOLVED],
  [STATUS.RESOLVED]: [STATUS.CLOSED, STATUS.REOPENED],
  [STATUS.REOPENED]: [STATUS.IN_PROGRESS],
  [STATUS.CLOSED]: [],
}

// Statuses a requester may still withdraw from — must match the guard inside
// the withdraw_own_ticket() RPC.
export const WITHDRAWABLE: string[] = [
  STATUS.NEW,
  STATUS.AI_PROCESSING,
  STATUS.NEEDS_REVIEW,
]

export const REOPEN_WINDOW_DAYS = 7

export const OPEN_STATUSES: string[] = [
  STATUS.NEW,
  STATUS.AI_PROCESSING,
  STATUS.NEEDS_REVIEW,
  STATUS.ASSIGNED,
  STATUS.IN_PROGRESS,
  STATUS.WAITING_FOR_USER,
  STATUS.REOPENED,
]

export const MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
export const ACCEPTED_IMAGE_TYPES = [
  'image/jpeg',
  'image/png',
  'image/webp',
  'image/gif',
  'image/heic',
]

export const MIN_DESCRIPTION_LENGTH = 20

// A building-level location whose floor is unknown. The hierarchy is
// buildings -> floors -> locations, so a location reaches its building only
// through a floor; without this sentinel, "somewhere in Werner Hall" cannot
// be stored and the building is silently lost.
export const UNSPECIFIED_FLOOR = 'Unspecified'
