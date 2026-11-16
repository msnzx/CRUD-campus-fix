# CaldwellFix redesign change tracker

This file tracks changes made only in `CaldwellFix-redesign`. It is a review list,
not a deployment plan: nothing in this project should be pushed to the main
GitHub repository unless Samuel approves it first.

## Ready to consider for the main project

- Refreshed visual system using Caldwell red (`#c8102e`), clearer cards, spacing,
  and responsive layouts.
- Redesigned public cover page with Caldwell branding, log in, sign up, contact,
  accessibility settings, and quit controls.
- Reversible campus-photo cover slideshow: five supplied campus images crossfade
  automatically every seven seconds with non-interactive progress indicators.
  A consistent charcoal/maroon overlay and translucent content panel prevent
  photo colors from reducing text and button contrast. The slideshow pauses
  for the app’s Reduce motion setting or the operating-system reduced-motion
  preference; high-contrast mode hides the photos entirely.
- Reversible signed-in campus-photo background: authenticated pages now reuse
  the same five-photo, seven-second rotation behind the app with a strong
  light/dark overlay so cards, forms, and navigation remain readable. It is
  isolated in `AppPhotoBackdrop.tsx`, one line in `App.tsx`, and the labeled
  `app-photo-backdrop` CSS block; high-contrast mode hides it.
- Login-page logo returns users to the cover page.
- Theme support: light/dark modes, remembered preference, improved dark-mode
  contrast, and a persistent bottom-left sun/moon control on every public and
  signed-in screen. It is fixed to the viewport, so it remains visible while
  scrolling and stays clear of the cover page's bottom-right Quit control.
- Animated theme change: circular reveal where supported and a sun/moon sweep;
  reduced-motion preferences disable the effect.
- Student dashboard presentation improvements.
- Improved the My Tickets **Report an issue** call-to-action contrast with a
  darker Caldwell-maroon button and solid white text.
- Three-step ticket submission flow that prevents advancing until required
  earlier-step information is complete.
- Guided ticket-location fields now reveal in a deliberate order: Building,
  Floor, Room number, then Area type. Later fields are blurred and disabled
  until the preceding selection is made. Numeric floors constrain room entries
  to that floor's range (for example, Floor 2 accepts 200–299). Choosing a
  different building clears dependent location choices. The GPS confirmation
  can now be dismissed with “Keep,” while “Choose another” reveals the
  clickable campus-map picker.
- GPS distance is used only internally to decide whether a nearby calibrated
  building can be selected. Neither students nor admins are shown measured or
  approximate distances in the interface.
- The student location step now starts with only the live OpenStreetMap view.
  The labeled clickable campus-map image is revealed only after the student
  chooses “Choose another,” and is hidden again when they keep an automatic
  location, keeping the primary automatic-location path simple.
- Reversible Werner Hall Floor 2 room picker: when the user selects **Werner
  Hall** and **Floor 2**, the supplied floor-plan image replaces OpenStreetMap
  and shows room-selection pins placed beside rather than over room labels.
  Pin selection fills the Room number field. This is isolated in the
  `WernerHallSecondFloorPlan` component, the `WERNER_HALL_FLOOR_2_*` constants,
  the `werner-floor-plan` CSS block, and `public/werner-hall-floor-2.png`.
- Werner Hall Floor 2 facility pins: Men’s Room, Ladies’ Room, Storage Room,
  and Elevator pins set Room Number to `N/A` and show their specific Area Type.
  Regular room pins continue to fill only Room Number and never alter Area Type.
  Facility labels are also saved as the location’s exact description while
  remaining compatible with the database’s existing generic area categories.
- Reversible Raymond Hall Floor 2 room picker: when the user selects **Raymond
  Hall** and **Floor 2**, the supplied Raymond Hall / Albertus Magnus Science
  Building floor plan replaces OpenStreetMap. Room pins fill the Room Number,
  including valid suffixes such as `201A`, `201B`, and `204A`; special facility
  pins fill `N/A` plus their Area Type. This is isolated in the
  `RaymondHallSecondFloorPlan` component, Raymond constants, and
  `public/raymond-hall-floor-2.png`.
- Raymond Hall Floor 2 pin-placement refinement: pins were moved into adjacent
  blank plan space so the printed room and facility labels remain fully visible
  while the controls stay easy to select.
- Raymond Hall Floor 2 follow-up: separated the Men’s Room and Science Lab 209
  pins so they do not visually compete or create an ambiguous tap target.
- Raymond Hall Floor 2 boundary correction: placed the Ladies’ Room, Office
  204, and Office 204A pins within their actual plan boundaries rather than
  adjacent Storage 206A or Classroom 206.
- Werner Hall Floor 2 pin-placement refinement: moved room and facility pins
  into clear corners or margins of their spaces so the printed room numbers
  and plan labels remain visible.
- Verified floor-plan area autofill: pins explicitly labeled **Classroom**,
  **Office**, or **Lab** now fill Area Type automatically. Pins whose printed
  plan label does not establish an area category leave Area Type blank, while
  facility pins retain their specific Area Type behavior.
- The Room Number field now offers **No room number (N/A)**. This is a valid
  location choice for hallways and other unnumbered places, and it unlocks
  Area Type so students can select the appropriate kind of space.
- Student-friendly location selection: campus-map building picker, live-location
  permission button, and no description word limit.
- Reversible nearest-building location workflow: with permission, the browser
  compares a student's live coordinates to calibrated building GPS centroids,
  fills the nearest building only within 120 m, and shows a confirmation/change
  choice. Coordinates are never attached to tickets. System Admins can
  deliberately calibrate each building from the map page using “Set GPS here.”
  The report form fetches fresh building GPS data at location-request time, so
  a newly calibrated building works without waiting for the session cache.
- GPS calibration permission migration: grants only `latitude` and `longitude`
  updates on `buildings` to authenticated users; the existing System Admin RLS
  rule still controls who may perform the update. It does not alter any data.
- Admin/staff campus map viewer: centered, framed, and capped near the source
  image's native size so it stays legible instead of becoming blurred by heavy
  enlargement.
- Reversible admin/staff map enhancements: building-name search, highlighted
  search matches, controlled zoom with scroll-to-pan, and pin pop-ups showing
  building name plus open/total/emergency ticket counts. These only change the
  viewer presentation; stored map pins and ticket data are untouched.
- Staff queue, campus map, analytics, and people-management visual improvements.
- Ticket-detail clarity: a dedicated ticket header now separates the title,
  status badges, routed department, and staff assignment; dark mode uses
  higher-contrast routing and title text.

## Keep as redesign-only until we decide otherwise

- Reversible dark-sky background preview, based on Samuel's supplied image.
  It appears only in dark mode behind opaque app surfaces and is disabled in
  high-contrast mode. Remove the labeled CSS block and
  `frontend/public/caldwell-night-sky.png` to revert it.
- Small branded footer preview across public and signed-in pages. It is a
  normal bottom-of-page footer that scrolls with content (not a floating
  control), and is isolated in `PublicFooter.tsx` plus a clearly labeled CSS
  block for easy removal.
- Extra decorative motion: background orbs, animated cover logo, and entrance
  animations.
- The exact "Campus Pulse" redesign styling and oversized hero treatment.
- Leaflet/OpenStreetMap live map addition. It is useful, but adds package weight
  and needs a product decision before being added to the main project.
- The cover page's quit button behavior (`about:blank`), which is mostly for the
  prototype and may not be appropriate for a real web app.

## Requires backend/configuration before it can be used in the main project

- System-admin **Add user** sends a Supabase invitation email. Requires the
  FastAPI backend, `SUPABASE_SERVICE_ROLE_KEY`, and a correct `APP_URL`.
- System-admin **Reset password** sends a secure Supabase reset email. An admin
  never views or chooses another person's password.
- User removal is a safe **Remove access / Reactivate** action, not permanent
  deletion, so ticket history and audit data stay intact.
- For this redesign's local preview, the backend `APP_URL` should be
  `http://localhost:5174`; hosted deployments must use their real HTTPS URL.

## Transfer checklist

Before moving an item to the main project:

1. Confirm that the team wants the feature and its visual behavior.
2. Copy only the necessary files/changes, rather than replacing the main
   project wholesale.
3. Run `npm run typecheck` and `npm run build` in the main project's `frontend`.
4. For server features, configure backend secrets outside Git and test using a
   non-production account.
5. Commit the approved feature on its own branch with a clear message.

## Verification status

- Redesign frontend: `npm run typecheck` and `npm run build` passed after the
  current set of changes.
- Redesign backend: Python syntax check passed for the admin endpoints.
