import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { CircleMarker, MapContainer, TileLayer, Tooltip, useMap } from 'react-leaflet'
import type { LatLngExpression } from 'leaflet'
import { supabase } from '../lib/supabase'
import { uploadAttachment, validateImage } from '../lib/attachments'
import { useAuth } from '../auth/AuthProvider'
import { useLookups, statusIdByName } from '../lib/useLookups'
import { ErrorNote, Loading, Modal, Spinner } from '../components/ui'
import {
  CAMPUS_SAFETY_PHONE,
  EMERGENCY_PHONE,
  detectEmergency,
} from '../lib/emergency'
import {
  ACCEPTED_IMAGE_TYPES,
  MAX_ATTACHMENT_BYTES,
  STATUS,
  UNSPECIFIED_FLOOR,
} from '../lib/domain'
import { submissionText } from '../lib/submission'
import { formatBytes } from '../lib/format'
import { AUTO_BUILDING_MAX_DISTANCE_METERS, findNearestBuilding } from '../lib/buildingGeolocation'
import type { Tables } from '../lib/database.types'

const MAP_IMAGE = '/campus-map.png'
const WERNER_HALL_FLOOR_2_IMAGE = '/werner-hall-floor-2.png'
const RAYMOND_HALL_FLOOR_2_IMAGE = '/raymond-hall-floor-2.png'
const CAMPUS_CENTER: LatLngExpression = [40.8339079, -74.2713297]

type FloorPlanRoomPin = {
  room: string
  left: string
  top: string
  areaType?: 'Classroom' | 'Office' | 'Lab'
}

// Reversible Werner Hall floor-plan enhancement. Pin coordinates are kept
// beside each printed room label so the source evacuation-plan labels remain
// readable. Remove this component and its conditional use below to return to
// the standard live OpenStreetMap view for every floor.
const WERNER_HALL_FLOOR_2_ROOMS: readonly FloorPlanRoomPin[] = [
  { room: '211', areaType: 'Classroom', left: '8%', top: '38%' },
  { room: '215', areaType: 'Classroom', left: '7%', top: '55%' },
  { room: '219', left: '18%', top: '59%' },
  { room: '220', left: '18%', top: '67%' },
  { room: '221', left: '18%', top: '74%' },
  { room: '222', left: '22%', top: '79%' },
  { room: '223', left: '31%', top: '79%' },
  { room: '224', left: '41%', top: '79%' },
  { room: '226', left: '38%', top: '72%' },
  { room: '209', left: '37%', top: '27%' },
  { room: '208', left: '34%', top: '36%' },
  { room: '207', areaType: 'Lab', left: '36%', top: '45%' },
  { room: '206', areaType: 'Classroom', left: '53%', top: '45%' },
  { room: '205', left: '64%', top: '44%' },
  { room: '203', areaType: 'Classroom', left: '87%', top: '45%' },
  { room: '201', areaType: 'Classroom', left: '87%', top: '65%' },
]

// Facility pins use an exact label for the interface and ticket details. The
// fallback keeps the saved location compatible with the existing database
// area-type records.
const WERNER_HALL_FLOOR_2_FACILITIES = [
  { value: 'facility:mens-room', label: "Men's Room", fallbackAreaType: 'Restroom', left: '47%', top: '78%' },
  { value: 'facility:ladies-room', label: "Ladies' Room", fallbackAreaType: 'Restroom', left: '55%', top: '78%' },
  { value: 'facility:storage-room-west', label: 'Storage Room', fallbackAreaType: 'Common Area', left: '25%', top: '39%' },
  { value: 'facility:storage-room-east', label: 'Storage Room', fallbackAreaType: 'Common Area', left: '58%', top: '68%' },
  { value: 'facility:elevator-north', label: 'Elevator', fallbackAreaType: 'Elevator', left: '24%', top: '32%' },
  { value: 'facility:elevator-south', label: 'Elevator', fallbackAreaType: 'Elevator', left: '45%', top: '67%' },
] as const

// Reversible Raymond Hall floor-2 enhancement. Pins sit beside the printed
// labels in the supplied evacuation plan, preserving the original room text.
const RAYMOND_HALL_FLOOR_2_ROOMS: readonly FloorPlanRoomPin[] = [
  { room: '201', areaType: 'Lab', left: '84%', top: '61%' },
  { room: '201A', areaType: 'Office', left: '93%', top: '35%' },
  { room: '201B', areaType: 'Office', left: '93%', top: '20%' },
  { room: '202', areaType: 'Lab', left: '87%', top: '25%' },
  { room: '203', areaType: 'Office', left: '51%', top: '61%' },
  { room: '204', areaType: 'Office', left: '66%', top: '42%' },
  { room: '204A', areaType: 'Office', left: '66%', top: '20%' },
  { room: '205', areaType: 'Office', left: '62%', top: '56%' },
  { room: '206', areaType: 'Classroom', left: '53%', top: '25%' },
  { room: '207', areaType: 'Classroom', left: '45%', top: '62%' },
  { room: '208', left: '41%', top: '25%' },
  { room: '209', areaType: 'Lab', left: '19%', top: '63%' },
]

const RAYMOND_HALL_FLOOR_2_FACILITIES = [
  { value: 'facility:raymond-ladies-room', label: "Ladies' Room", fallbackAreaType: 'Restroom', left: '3%', top: '43%' },
  { value: 'facility:raymond-mens-room', label: "Men's Room", fallbackAreaType: 'Restroom', left: '5%', top: '57%' },
  { value: 'facility:raymond-storage-206a', label: 'Storage Room', fallbackAreaType: 'Common Area', left: '14%', top: '24%' },
  { value: 'facility:raymond-chem-storage', label: 'Chem Storage', fallbackAreaType: 'Common Area', left: '91%', top: '44%' },
  { value: 'facility:raymond-test-prep', label: 'Test Prep', fallbackAreaType: 'Common Area', left: '59%', top: '71%' },
  { value: 'facility:raymond-stair-2-upper', label: 'Stairwell', fallbackAreaType: 'Stairwell', left: '70%', top: '70%' },
  { value: 'facility:raymond-stair-2-lower', label: 'Stairwell', fallbackAreaType: 'Stairwell', left: '58%', top: '84%' },
  { value: 'facility:raymond-pantry', label: 'Pantry', fallbackAreaType: 'Kitchen / Dining', left: '72%', top: '84%' },
] as const

const FLOOR_PLAN_AUTOMATIC_AREAS = [
  { value: 'auto:lab', label: 'Lab', fallbackAreaType: 'Common Area' },
] as const

function areaTypeOverrideFor(value: string) {
  return [...WERNER_HALL_FLOOR_2_FACILITIES, ...RAYMOND_HALL_FLOOR_2_FACILITIES, ...FLOOR_PLAN_AUTOMATIC_AREAS]
    .find((facility) => facility.value === value)
}

function WernerHallSecondFloorPlan({
  roomNumber,
  areaTypeId,
  onSelectRoom,
  onSelectFacility,
}: {
  roomNumber: string
  areaTypeId: string
  onSelectRoom: (room: string, areaType?: string) => void
  onSelectFacility: (value: string) => void
}) {
  return (
    <section className="werner-floor-plan" aria-labelledby="werner-floor-plan-heading">
      <div className="werner-floor-plan-heading">
        <div><strong id="werner-floor-plan-heading">Werner Hall · Floor 2</strong><span>Select a room pin to fill the room-number field.</span></div>
      </div>
      <div className="werner-floor-plan-canvas">
        <img src={WERNER_HALL_FLOOR_2_IMAGE} alt="Werner Hall second-floor plan" />
        {WERNER_HALL_FLOOR_2_ROOMS.map(({ room, areaType, left, top }) => (
          <button
            key={room}
            type="button"
            className={`werner-room-pin ${roomNumber === room ? 'selected' : ''}`}
            style={{ left, top }}
            onClick={() => onSelectRoom(room, areaType)}
            aria-label={`Select room ${room}`}
            aria-pressed={roomNumber === room}
          >
            <span aria-hidden="true">+</span>
          </button>
        ))}
        {WERNER_HALL_FLOOR_2_FACILITIES.map(({ value, label, left, top }, index) => (
          <button
            key={`${value}-${index}`}
            type="button"
            className={`werner-room-pin werner-facility-pin ${areaTypeId === value ? 'selected' : ''}`}
            style={{ left, top }}
            onClick={() => onSelectFacility(value)}
            aria-label={`Select ${label}`}
            aria-pressed={areaTypeId === value}
          >
            <span aria-hidden="true">◆</span>
          </button>
        ))}
      </div>
    </section>
  )
}

function RaymondHallSecondFloorPlan({
  roomNumber,
  areaTypeId,
  onSelectRoom,
  onSelectFacility,
}: {
  roomNumber: string
  areaTypeId: string
  onSelectRoom: (room: string, areaType?: string) => void
  onSelectFacility: (value: string) => void
}) {
  return (
    <section className="werner-floor-plan" aria-labelledby="raymond-floor-plan-heading">
      <div className="werner-floor-plan-heading">
        <div><strong id="raymond-floor-plan-heading">Raymond Hall · Floor 2</strong><span>Select a room or facility pin to fill the location fields.</span></div>
      </div>
      <div className="werner-floor-plan-canvas">
        <img src={RAYMOND_HALL_FLOOR_2_IMAGE} alt="Raymond Hall second-floor plan" />
        {RAYMOND_HALL_FLOOR_2_ROOMS.map(({ room, areaType, left, top }) => (
          <button
            key={room}
            type="button"
            className={`werner-room-pin ${roomNumber === room ? 'selected' : ''}`}
            style={{ left, top }}
            onClick={() => onSelectRoom(room, areaType)}
            aria-label={`Select room ${room}`}
            aria-pressed={roomNumber === room}
          >
            <span aria-hidden="true">+</span>
          </button>
        ))}
        {RAYMOND_HALL_FLOOR_2_FACILITIES.map(({ value, label, left, top }) => (
          <button
            key={value}
            type="button"
            className={`werner-room-pin werner-facility-pin ${areaTypeId === value ? 'selected' : ''}`}
            style={{ left, top }}
            onClick={() => onSelectFacility(value)}
            aria-label={`Select ${label}`}
            aria-pressed={areaTypeId === value}
          >
            <span aria-hidden="true">◆</span>
          </button>
        ))}
      </div>
    </section>
  )
}

function FollowLocation({ position }: { position: [number, number] | null }) {
  const map = useMap()
  useEffect(() => {
    if (position) map.flyTo(position, 18, { duration: 0.7 })
  }, [map, position])
  return null
}

function StudentMapPicker({
  buildings,
  buildingId,
  floorNumber,
  roomNumber,
  areaTypeId,
  onSelect,
  onChooseAnother,
  onSelectRoom,
  onSelectFacility,
}: {
  buildings: Tables<'buildings'>[]
  buildingId: string
  floorNumber: string
  roomNumber: string
  areaTypeId: string
  onSelect: (id: string) => void
  onChooseAnother: () => void
  onSelectRoom: (room: string) => void
  onSelectFacility: (value: string) => void
}) {
  const [hovered, setHovered] = useState<number | null>(null)
  const [locationMessage, setLocationMessage] = useState('')
  const [userLocation, setUserLocation] = useState<[number, number] | null>(null)
  const [detectedBuilding, setDetectedBuilding] = useState<{ name: string; id: string } | null>(null)
  const [showCampusMap, setShowCampusMap] = useState(false)
  const placedBuildings = buildings.filter((building) => building.map_x != null && building.map_y != null)
  const selectedBuilding = buildings.find((building) => String(building.building_id) === buildingId)
  const useWernerSecondFloorPlan = selectedBuilding?.name === 'Werner Hall' && floorNumber === '2'
  const useRaymondSecondFloorPlan = selectedBuilding?.name === 'Raymond Hall' && floorNumber === '2'

  function requestLocation() {
    setDetectedBuilding(null)
    if (!navigator.geolocation) {
      setLocationMessage('Location services are not available in this browser. Choose a building from the list instead.')
      return
    }
    setLocationMessage('Checking your location…')
    navigator.geolocation.getCurrentPosition(
      async (position) => {
        const coordinates: [number, number] = [position.coords.latitude, position.coords.longitude]
        setUserLocation(coordinates)
        // Building GPS points can be calibrated moments before a student
        // opens this form. Fetch fresh rows here instead of relying only on
        // the session lookup cache, so detection works immediately.
        const { data: freshBuildings, error: buildingsError } = await supabase
          .from('buildings')
          .select('*')
        const candidates = buildingsError ? buildings : (freshBuildings ?? buildings)
        const nearest = findNearestBuilding(candidates, coordinates)
        if (!nearest) {
          setDetectedBuilding(null)
          setLocationMessage('Location found, but no calibrated campus building is available yet. Choose your building from the list below.')
          return
        }
        if (nearest.distanceMeters > AUTO_BUILDING_MAX_DISTANCE_METERS) {
          setDetectedBuilding(null)
          setLocationMessage('Location found, but no nearby calibrated building was identified. Please choose your building manually.')
          return
        }
        const id = String(nearest.building.building_id)
        onSelect(id)
        setDetectedBuilding({ name: nearest.building.name, id })
        setLocationMessage('')
      },
      () => setLocationMessage('We could not access your location. You can still choose a building manually.'),
      { enableHighAccuracy: true, timeout: 10_000, maximumAge: 60_000 },
    )
  }

  return (
    <section className="student-map-picker" aria-labelledby="student-map-heading">
      <div className="student-map-heading">
        <div><p className="eyebrow">Location helper</p><h3 id="student-map-heading">Find your building</h3><p>Share your location to identify the nearest calibrated building.</p></div>
        <button type="button" className="sm location-button" onClick={requestLocation}>◎ Use my location</button>
      </div>
      {locationMessage && <p className="location-message" role="status">{locationMessage}</p>}
      {detectedBuilding && (
        <div className="location-suggestion" role="status">
          <div><strong>Building filled automatically: {detectedBuilding.name}</strong><span>Check the building name, or choose another one.</span></div>
          <div className="actions"><button type="button" className="sm" onClick={() => { setDetectedBuilding(null); setShowCampusMap(false) }}>Keep {detectedBuilding.name}</button><button type="button" className="sm" onClick={() => { onChooseAnother(); setDetectedBuilding(null); setShowCampusMap(true) }}>Choose another</button></div>
        </div>
      )}
      {useWernerSecondFloorPlan ? (
        <WernerHallSecondFloorPlan
          roomNumber={roomNumber}
          areaTypeId={areaTypeId}
          onSelectRoom={onSelectRoom}
          onSelectFacility={onSelectFacility}
        />
      ) : useRaymondSecondFloorPlan ? (
        <RaymondHallSecondFloorPlan
          roomNumber={roomNumber}
          areaTypeId={areaTypeId}
          onSelectRoom={onSelectRoom}
          onSelectFacility={onSelectFacility}
        />
      ) : (
        <div className="live-location-panel">
          <div><strong>Live campus map</strong><span>Your blue dot appears here after you share your location.</span></div>
          <MapContainer center={CAMPUS_CENTER} zoom={16} scrollWheelZoom={false} className="live-location-map" aria-label="Interactive map of Caldwell University">
            <TileLayer
              attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
              url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
            />
            <CircleMarker center={CAMPUS_CENTER} radius={8} pathOptions={{ color: '#8f0a25', fillColor: '#c8102e', fillOpacity: 0.92, weight: 3 }}>
              <Tooltip direction="top">Caldwell University</Tooltip>
            </CircleMarker>
            {userLocation && <CircleMarker center={userLocation} radius={10} pathOptions={{ color: '#ffffff', fillColor: '#1677ff', fillOpacity: 1, weight: 4 }}><Tooltip direction="top" permanent>You are here</Tooltip></CircleMarker>}
            <FollowLocation position={userLocation} />
          </MapContainer>
        </div>
      )}
      {showCampusMap && <>
        <div className="student-map-canvas">
          <img className="student-map-image" src={MAP_IMAGE} alt="Caldwell University campus map" />
          {placedBuildings.map((building) => {
            const selected = String(building.building_id) === buildingId
            return (
              <button
                key={building.building_id}
                type="button"
                className={`student-map-pin ${selected ? 'selected' : ''}`}
                style={{ left: `${building.map_x}%`, top: `${building.map_y}%` }}
                aria-label={`Select ${building.name}`}
                aria-pressed={selected}
                onMouseEnter={() => setHovered(building.building_id)}
                onMouseLeave={() => setHovered(null)}
                onFocus={() => setHovered(building.building_id)}
                onBlur={() => setHovered(null)}
                onClick={() => { onSelect(String(building.building_id)); setDetectedBuilding(null); setLocationMessage(''); setShowCampusMap(false) }}
              >
                <span aria-hidden="true">●</span>
                {(hovered === building.building_id || selected) && <span className="student-map-tooltip">{building.name}</span>}
              </button>
            )
          })}
        </div>
        <p className="student-map-note">Select a pin for a different building. For privacy, your current location is only used in this browser and is not saved with your report.</p>
      </>}
    </section>
  )
}
import { appendTranscript } from '../lib/voice'
import VoiceInputButton from '../components/VoiceInputButton'

export default function SubmitTicketPage() {
  const nav = useNavigate()
  const { profile } = useAuth()
  const { lookups, ready } = useLookups()

  const [title, setTitle] = useState('')
  const [step, setStep] = useState<1 | 2 | 3>(1)
  const [description, setDescription] = useState('')
  const [issueTypeId, setIssueTypeId] = useState<string>('')
  const [buildingId, setBuildingId] = useState<string>('')
  const [floorId, setFloorId] = useState<string>('')
  const [areaTypeId, setAreaTypeId] = useState<string>('')
  const [roomNumber, setRoomNumber] = useState('')
  const [file, setFile] = useState<File | null>(null)

  const [error, setError] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)
  const [emergencyAck, setEmergencyAck] = useState(false)
  const [showEmergency, setShowEmergency] = useState(false)

  // "Unspecified" first, then ground floor, then numbered floors in order.
  const floorsForBuilding = useMemo(() => {
    const rows = lookups.floors.filter((f) => String(f.building_id) === buildingId)
    const rank = (n: string) =>
      n === UNSPECIFIED_FLOOR ? -2 : n === 'G' ? -1 : Number(n)
    return [...rows].sort((a, b) => rank(a.floor_number) - rank(b.floor_number))
  }, [lookups.floors, buildingId])

  const emergency = useMemo(
    () => detectEmergency(title, description),
    [title, description],
  )

  const selectedFloor = useMemo(
    () => floorsForBuilding.find((floor) => String(floor.floor_id) === floorId),
    [floorId, floorsForBuilding],
  )
  const selectedFloorNumber = selectedFloor?.floor_number ?? ''
  const numericFloor = /^\d+$/.test(selectedFloorNumber) ? Number(selectedFloorNumber) : null
  const roomRange = numericFloor === null ? null : {
    min: numericFloor * 100,
    max: numericFloor * 100 + 99,
  }
  const selectedAreaOverride = areaTypeOverrideFor(areaTypeId)
  const roomNumericPart = Number(roomNumber.match(/^\d+/)?.[0])
  const roomHasValidSuffix = /^\d+[A-Z]?$/.test(roomNumber)
  const roomIsInRange = roomNumber === 'N/A'
    ? true
    : (!roomNumber || !roomRange || (
    roomHasValidSuffix && roomNumericPart >= roomRange.min && roomNumericPart <= roomRange.max
    ))
  const roomReady = Boolean(floorId && roomNumber && roomIsInRange)

  const issueComplete = title.trim().length > 0 && description.trim().length > 0
  const locationComplete = Boolean(buildingId || areaTypeId || roomNumber.trim()) && roomIsInRange

  function chooseBuilding(id: string) {
    setBuildingId(id)
    setFloorId('')
    setRoomNumber('')
    setAreaTypeId('')
  }

  function chooseDifferentBuilding() {
    chooseBuilding('')
  }

  function chooseWernerFacility(value: string) {
    setRoomNumber('N/A')
    setAreaTypeId(value)
  }

  function markRoomNotApplicable() {
    setRoomNumber('N/A')
    // A manual N/A is for an unnumbered area such as a hallway, so it should
    // not retain a previous floor-plan facility label.
    if (areaTypeOverrideFor(areaTypeId)) setAreaTypeId('')
  }

  function chooseFloorPlanRoom(room: string, automaticAreaType?: string) {
    setRoomNumber(room)
    if (!automaticAreaType) {
      setAreaTypeId('')
      return
    }
    if (automaticAreaType === 'Lab') {
      setAreaTypeId('auto:lab')
      return
    }
    const area = lookups.areaTypes.find((item) => item.name === automaticAreaType)
    setAreaTypeId(area ? String(area.area_type_id) : '')
  }

  function moveTo(next: 2 | 3) {
    setError(null)
    if (next === 2 && !issueComplete) {
      setError(new Error('Add a title and a short description before continuing.'))
      return
    }
    if (next === 3 && !locationComplete) {
      setError(new Error(roomNumber && !roomIsInRange
        ? `Enter a room number between ${roomRange?.min} and ${roomRange?.max} for the selected floor.`
        : 'Add a building, area type, or nearest landmark before continuing.'))
      return
    }
    setStep(next)
  }

  function onPickFile(e: React.ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0] ?? null
    setError(null)
    if (!f) return setFile(null)
    const problem = validateImage(f)
    if (problem) {
      setError(new Error(problem))
      e.target.value = ''
      return setFile(null)
    }
    setFile(f)
  }

  async function reallySubmit() {
    if (!profile) return
    setBusy(true)
    setError(null)

    try {
      // 1. Optional location. A ticket without a precise location is still
      //    valid; an outdoor issue has no floor at all.
      //
      //    A building with no floor chosen must still record the building.
      //    Since a location reaches its building only through a floor, fall
      //    back to that building's "Unspecified" floor rather than dropping
      //    the building on the floor, which is what used to happen.
      let locationId: number | null = null
      let locationFailed = false

      let effectiveFloorId: number | null = floorId ? Number(floorId) : null
      const selectedFacility = areaTypeOverrideFor(areaTypeId)
      const resolvedAreaTypeId = selectedFacility
        ? lookups.areaTypes.find((area) => area.name === selectedFacility.fallbackAreaType)?.area_type_id ?? null
        : areaTypeId ? Number(areaTypeId) : null
      if (effectiveFloorId === null && buildingId) {
        effectiveFloorId =
          lookups.floors.find(
            (f) =>
              String(f.building_id) === buildingId &&
              f.floor_number === UNSPECIFIED_FLOOR,
          )?.floor_id ?? null
      }

      if (effectiveFloorId !== null || roomNumber.trim() || areaTypeId) {
        const { data: loc, error: locErr } = await supabase
          .from('locations')
          .insert({
            floor_id: effectiveFloorId,
            area_type_id: resolvedAreaTypeId,
            room_number: roomNumber.trim() || null,
            exact_description: selectedFacility?.label ?? null,
          })
          .select('location_id')
          .single()

        if (locErr) {
          // A location failure must not cost the user their report, but it
          // must not be invisible either. Swallowing this is what left every
          // ticket unplaceable on the map.
          locationFailed = true
          console.error('Could not save ticket location:', locErr)
        } else if (loc) {
          locationId = loc.location_id
        }
      }

      const newStatus = statusIdByName(lookups, STATUS.NEW)
      if (!newStatus) throw new Error('Status table is not seeded. Contact an administrator.')

      // 2. Create the ticket. THIS IS THE COMMIT POINT. Everything after
      //    this may fail without costing the user their submission.
      const { data: ticket, error: ticketErr } = await supabase
        .from('tickets')
        .insert({
          reported_by: profile.user_id,
          ...submissionText(title, description),
          issue_type_id: issueTypeId ? Number(issueTypeId) : null,
          location_id: locationId,
          status_id: newStatus,
          is_emergency: emergency.isEmergency,
        })
        .select('ticket_id')
        .single()

      if (ticketErr) throw ticketErr
      const ticketId = ticket.ticket_id

      // 3. Attachment. Best effort, but a failure is reported on the ticket
      //    page rather than silently dropped.
      let attachmentFailed = false
      if (file) {
        const upErr = await uploadAttachment(ticketId, file, profile.user_id)
        if (upErr) {
          attachmentFailed = true
          console.error('Could not upload photo:', upErr)
        }
      }

      nav(`/tickets/${ticketId}`, { state: { locationFailed, attachmentFailed } })
    } catch (err) {
      setError(err)
      setBusy(false)
    }
  }

  function onSubmit(e: React.FormEvent) {
    e.preventDefault()
    // Enter should follow the same guarded route as the visible buttons.
    if (step === 1) {
      moveTo(2)
      return
    }
    if (step === 2) {
      moveTo(3)
      return
    }
    if (!issueComplete) {
      setStep(1)
      setError(new Error('Add a title and a short description before submitting.'))
      return
    }
    // V-004: every ticket says where. Outdoor spots have no building, so an
    // area type or a landmark is enough on its own.
    if (!locationComplete) {
      setStep(2)
      setError(
        new Error(
          'Tell us where the problem is: choose a building, or for outdoor issues pick an area type or name the nearest landmark.',
        ),
      )
      return
    }
    if (emergency.isEmergency && !emergencyAck) {
      setShowEmergency(true)
      return
    }
    void reallySubmit()
  }

  if (!ready) return <Loading />

  return (
    <div className="content narrow submit-page">
      <section className="form-hero">
        <div>
          <p className="eyebrow">New report</p>
          <h1>Let’s get it fixed.</h1>
          <p>Give us the details and we’ll route your report to the right team.</p>
        </div>
        <ol className="form-steps" aria-label="Submission steps"><li className={step === 1 ? 'active' : 'complete'}>1 <span>Issue</span></li><li className={step === 2 ? 'active' : step > 2 ? 'complete' : ''}>2 <span>Location</span></li><li className={step === 3 ? 'active' : ''}>3 <span>Review</span></li></ol>
      </section>

      <ErrorNote error={error} />

      {emergency.isEmergency && (
        <div className="alert error" role="alert">
          <strong>This may be an emergency.</strong> If anyone is in danger, call{' '}
          <strong>{EMERGENCY_PHONE}</strong> or Campus Safety at{' '}
          <strong>{CAMPUS_SAFETY_PHONE}</strong> now. Do not wait for a ticket.
        </div>
      )}

      <form onSubmit={onSubmit} className="card ticket-form">
        {step === 1 && <>
        <div className="form-section-heading"><span className="section-number">01</span><div><h2>Tell us what happened</h2><p>The more detail you include, the faster we can help.</p></div></div>
        <div className="field">
          <label htmlFor="title">What's the problem?</label>
          <input
            id="title"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            required
            maxLength={120}
            placeholder="Radiator won't turn off"
          />
        </div>

        <div className="field">
          <label htmlFor="desc">Describe it</label>
          <textarea
            id="desc"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            required
            placeholder="The radiator in my room has been stuck on since Friday. The room is about 85 degrees and the valve won't turn."
          />
          <div className="hint">
            Include what you see, when it started, and anything you already tried.
          </div>
          <VoiceInputButton
            disabled={busy}
            onTranscript={(spoken) => setDescription((current) => appendTranscript(current, spoken))}
          />
        </div>

        <div className="field">
          <label htmlFor="type">Category</label>
          <select
            id="type"
            value={issueTypeId}
            onChange={(e) => setIssueTypeId(e.target.value)}
          >
            <option value="">Not sure — let CampusFix decide</option>
            {lookups.issueTypes.map((it) => (
              <option key={it.issue_type_id} value={it.issue_type_id}>
                {it.name}
              </option>
            ))}
          </select>
          <div className="hint">
            Choose a category if you know it, or leave it blank for CampusFix to classify.
            Unclear reports may need staff review.
          </div>
        </div>
        <div className="wizard-actions wizard-next"><span>Step 1 of 3</span><button className="primary" type="button" onClick={() => moveTo(2)}>Continue to location <span aria-hidden="true">→</span></button></div>
        </>}

        {step === 2 && <>
        <div className="form-section-heading"><span className="section-number">02</span><div><h2>Where is it?</h2><p>A precise location helps the right team find the problem.</p></div></div>

        <div className="row">
          <div className="field">
            <label htmlFor="building">Building</label>
            <select
              id="building"
              value={buildingId}
              onChange={(e) => {
                chooseBuilding(e.target.value)
              }}
            >
              <option value="">Select…</option>
              {lookups.buildings.map((b) => (
                <option key={b.building_id} value={b.building_id}>
                  {b.name}
                </option>
              ))}
            </select>
          </div>

          <div className={`field guided-field ${!buildingId ? 'guided-field-locked' : ''}`}>
            <label htmlFor="floor">Floor</label>
            <select
              id="floor"
              value={floorId}
              onChange={(e) => {
                setFloorId(e.target.value)
                setRoomNumber('')
                setAreaTypeId('')
              }}
              disabled={!buildingId}
            >
              <option value="">Select…</option>
              {floorsForBuilding.map((f) => (
                <option key={f.floor_id} value={f.floor_id}>
                  {f.floor_number === UNSPECIFIED_FLOOR
                    ? "Not sure / anywhere in the building"
                    : f.floor_number === 'G'
                      ? 'Ground floor'
                      : `Floor ${f.floor_number}`}
                </option>
              ))}
            </select>
          </div>
        </div>

        <StudentMapPicker
          buildings={lookups.buildings}
          buildingId={buildingId}
          floorNumber={selectedFloorNumber}
          roomNumber={roomNumber}
          areaTypeId={areaTypeId}
          onSelect={chooseBuilding}
          onChooseAnother={chooseDifferentBuilding}
          onSelectRoom={chooseFloorPlanRoom}
          onSelectFacility={chooseWernerFacility}
        />

        <div className="row guided-location-row">
          <div className={`field guided-field ${!floorId ? 'guided-field-locked' : ''}`}>
            <label htmlFor="room">Room number</label>
            <input
              id="room"
              value={roomNumber}
              onChange={(e) => {
                setRoomNumber(e.target.value.toUpperCase().replace(/[^0-9A-Z]/g, ''))
                if (areaTypeOverrideFor(areaTypeId)) setAreaTypeId('')
              }}
              placeholder={roomRange ? `${roomRange.min}` : 'Choose a floor first'}
              inputMode="text"
              min={roomRange?.min}
              max={roomRange?.max}
              disabled={!floorId}
              aria-describedby="room-range"
            />
            <button type="button" className="sm na-room-button" onClick={markRoomNotApplicable} disabled={!floorId}>
              No room number (N/A)
            </button>
            <div className="hint" id="room-range">
              {!floorId
                ? 'Choose a floor first.'
                : roomNumber === 'N/A'
                  ? 'No room number selected. Choose an area type next.'
                : roomRange
                  ? `For Floor ${numericFloor}, use a room number from ${roomRange.min}–${roomRange.max}; room suffixes such as 204A are accepted.`
                  : 'This floor does not use a numbered-room range.'}
              {floorId && !roomIsInRange && ` ${roomNumber} is outside the allowed range.`}
            </div>
          </div>

          <div className={`field guided-field ${!roomReady ? 'guided-field-locked' : ''}`}>
            <label htmlFor="area">Area type</label>
            <select
              id="area"
              value={areaTypeId}
              onChange={(e) => setAreaTypeId(e.target.value)}
              disabled={!roomReady}
            >
              {selectedAreaOverride && <option value={selectedAreaOverride.value}>{selectedAreaOverride.label}</option>}
              <option value="">Select…</option>
              {lookups.areaTypes.map((a) => (
                <option key={a.area_type_id} value={a.area_type_id}>
                  {a.name}
                </option>
              ))}
            </select>
          </div>
        </div>

        <div className="field">
          <label htmlFor="photo">Photo (optional)</label>
          <input
            id="photo"
            type="file"
            accept={ACCEPTED_IMAGE_TYPES.join(',')}
            onChange={onPickFile}
          />
          <div className="hint">
            JPEG, PNG, WebP, GIF, or HEIC. Up to {formatBytes(MAX_ATTACHMENT_BYTES)}.
            {file && ` Selected: ${file.name} (${formatBytes(file.size)})`}
          </div>
        </div>

        <div className="wizard-actions"><button type="button" onClick={() => { setError(null); setStep(1) }}>← Back</button><button className="primary" type="button" onClick={() => moveTo(3)}>Review report <span aria-hidden="true">→</span></button></div>
        </>}

        {step === 3 && <>
          <div className="form-section-heading"><span className="section-number">03</span><div><h2>Review your report</h2><p>Check your details, then send it to the right team.</p></div></div>
          <div className="report-review">
            <div><span>Issue</span><strong>{title}</strong><p>{description}</p></div>
            <div><span>Category</span><strong>{lookups.issueTypes.find((item) => String(item.issue_type_id) === issueTypeId)?.name ?? 'CampusFix will route it'}</strong></div>
            <div><span>Location</span><strong>{lookups.buildings.find((item) => String(item.building_id) === buildingId)?.name ?? roomNumber ?? lookups.areaTypes.find((item) => String(item.area_type_id) === areaTypeId)?.name ?? 'Location provided'}</strong>{roomNumber && buildingId && <p>{selectedAreaOverride ? `Area type: ${selectedAreaOverride.label}` : `Room: ${roomNumber}`}</p>}</div>
            <div><span>Photo</span><strong>{file ? file.name : 'No photo attached'}</strong></div>
          </div>
          <div className="wizard-actions"><button type="button" onClick={() => { setError(null); setStep(2) }}>← Edit location</button><button className="primary" type="submit" disabled={busy}>{busy ? <Spinner /> : 'Submit ticket'}</button></div>
        </>}
      </form>

      {showEmergency && (
        <Modal title="This looks like an emergency" onClose={() => setShowEmergency(false)}>
          <div className="alert error">
            We detected {emergency.reasons.join(', ')} in your report.
          </div>
          <p>
            <strong>A ticket is not an emergency response.</strong> Nobody is watching this
            queue right now.
          </p>
          <p>
            If anyone is in danger, call <strong>{EMERGENCY_PHONE}</strong>. For campus
            incidents, call Campus Safety at <strong>{CAMPUS_SAFETY_PHONE}</strong>.
          </p>
          <p className="small muted">
            You can still file this for the record. It will be flagged as an emergency and
            sent to Campus Safety at the highest priority.
          </p>
          <div className="actions">
            <button className="primary" onClick={() => setShowEmergency(false)} type="button">
              Go back
            </button>
            <button
              type="button"
              onClick={() => {
                setEmergencyAck(true)
                setShowEmergency(false)
                void reallySubmit()
              }}
            >
              I've called — file the report
            </button>
          </div>
        </Modal>
      )}
    </div>
  )
}
