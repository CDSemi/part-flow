# PartFlow GUI Design v1

> **Status:** Draft for review — companion to [PROJECT_PROFILE.md](./PROJECT_PROFILE.md).
> This document specifies the user interface only. Business rules, terminology and workflows are defined in PROJECT_PROFILE and are not redefined here.
> An interactive mockup accompanies this document: `mockups/partflow-gui-mockup.html`.

---

# 1. Scope

Covers the five application views from PROJECT_PROFILE §16:

1. Scan Station (production)
2. Production Board (monitoring, large display)
3. Area Board (monitoring, per-Area)
4. Tracking (management)
5. Administration (configuration)

---

# 2. Design System

## 2.1 Two visual contexts, one token set

| Context    | Views                                      | Theme | Rationale                                                                           |
|------------|--------------------------------------------|-------|-------------------------------------------------------------------------------------|
| Shop floor | Scan Station, Production Board, Area Board | Dark  | Reduces glare in the shop, readable from distance, tolerant of low-quality displays |
| Management | Tracking, Administration                   | Light | Dense data work at a desk, matches office tooling expectations                      |

Both contexts share the same color tokens, spacing scale, and typography so the product feels like one system.

## 2.2 Color tokens

Status colors (semantic, never decorative):

- **Success** `#2fca7c` — recorded Movement, confirmed action
- **Warning** `#f5b83d` — needs attention, offline queue, route deviation, due soon
- **Error** `#ff5d5d` — rejected scan, integrity violation, overdue
- **Info / accent** `#3da5ff` — selection, focus, primary action

Area colors: every Area has a stable identity color used consistently in **all** views (chips, dots, distribution bars). Colors are Area display properties and editable in Administration without affecting history (PROJECT_PROFILE §5 Area).

Initial palette: Material `#8b93a8`, Cut `#f5b83d`, Lathe `#3da5ff`, Mill `#9b6ef3`, Deburr `#2fbf9b`, External `#ff8a4c`, Stockroom `#2fca7c`, Manual `#e06fae`.

## 2.3 Typography

- UI text: system font stack (`system-ui, Segoe UI, Roboto…`) — no webfont dependency, works offline.
- Identifiers (Part Number, PO, Request Number, external Job Number, quantities, timestamps): monospace. Identifiers must be visually distinct from prose because operators read them against paper travelers and folder labels.
- Shop-floor minimum sizes: body 16 px, Part Numbers ≥ 19 px, quantities ≥ 18 px bold. Production Board is sized for reading at 3–5 m (Part 22 px+, totals 26 px+).

## 2.4 Touch and scanner ergonomics

- Minimum touch target 48×48 px; primary Scan Station actions ≥ 56 px tall.
- All production actions reachable by scan or single tap. Mouse never required on shop-floor views.
- Keyboard wedge support: the scan input is a plain text input terminated by Enter — no custom driver, no scan-mode selection (PROJECT_PROFILE §7).

---

# 3. Global Interaction Rules

These apply to every view; they implement the profile's core principles in UI terms.

1. **Focus discipline.** On the Scan Station, the barcode input regains focus automatically after every completed operation, dialog close, machine/worker change, and view activation. Nothing may steal focus permanently.
2. **Ambiguity requires confirmation.** Whenever a scan resolves to more than one valid production context, the UI presents an explicit choice list (see §4.5). The UI never guesses and never defaults silently.
3. **Feedback is tri-state and instant.** Every scan produces exactly one of: Success (green), Warning (amber, action recorded or queued but needs awareness), Error (red, nothing recorded). Feedback shows *what happened* and *why* in one line each.
4. **Quantity integrity is visible.** Quantity entry displays the available upstream quantity. Attempts to move more than available are rejected with an explicit error — the UI explains the limit rather than clamping silently.
5. **History is append-only in the UI too.** Undo appears as a new compensating entry in the scan list; the original entry stays visible (struck context, never removed). Tracking's Movement history has no edit/delete affordances.
6. **Offline is a first-class state.** Going offline shows a persistent banner with the queued-event count; queued scans render with an amber "pending" dot and flip to green after sync. The workflow itself does not change (PROJECT_PROFILE §14).

---

# 4. Scan Station

Fixed to one Area per station. Single screen, no navigation during normal production.

## 4.1 Layout

```
┌────────────────────────────────────────────────────────────────────────┐
│ Dept / AREA (color) / Operation · Station   [Machine][Worker][●Online] │
│ (offline banner when disconnected)                                     │
├────────────────────────────────┬───────────────────────────────────────┤
│ Machine strip (scan to select) │  UNDO LAST SCAN                       │
│ Scan input (large, focused)    │  Recent scans (today)                 │
│ Feedback zone                  │  In this Area now (inventory)         │
└────────────────────────────────┴───────────────────────────────────────┘
```

## 4.2 Header

Department, Area name with Area color, Operation, Station ID. Pills for active Machine and active Worker (both show `—` when not applicable per Area configuration). Connectivity indicator with explicit ONLINE/OFFLINE text — color alone is not sufficient.

## 4.3 Machine selection — scan-only, one scan per Part

Applies only when the Area has Machine tracking enabled.

- Machine selection happens **exclusively by scanning a Machine barcode**. There is no tap/click selection; the on-screen machine strip is a read-only status display (idle / running / maintenance) that also highlights the currently armed Machine.
- A Machine scan is **not** required before a Part scan — the application classifies every barcode automatically, so either order works:
  - **Part → Machine:** after quantity entry the Movement is held in an *awaiting-Machine* state, shown as a prominent pulsing banner ("Scan Machine barcode to record {Part} × {qty}") and a `REQUIRED` Machine pill. Scanning the Machine records the Movement.
  - **Machine → Part:** the scanned Machine is *armed* for the next Part only (pill shows "{Machine} (next Part)"). The following Part scan records immediately and consumes the armed Machine.
- **Every Part scan requires its own Machine scan.** The Machine is never sticky across Parts — after each recorded Movement the selection resets to "— scan required".
- Scanning another Part while a previous Part is still awaiting its Machine is **rejected with an error** ("Machine barcode required — scan the Machine first, then the next Part"). Nothing is recorded.

> ⚠ **PROJECT_PROFILE impact:** this supersedes §5 Machine / §13 Machine Selection ("the selected Machine remains active across subsequent scans until changed or cleared"). PROJECT_PROFILE should be updated to the per-Part machine-scan model.

## 4.4 Scan input and resolution

One input accepts every barcode type; the system classifies the scan (Part / Machine / Worker / Request Type / Area). Resolution outcomes:

| Scan resolves to                     | UI behavior                                                                         |
|--------------------------------------|-------------------------------------------------------------------------------------|
| Machine, Part awaiting               | Record the held Movement on this Machine, success feedback                          |
| Machine, nothing awaiting            | Arm the Machine for the next Part (one-shot)                                        |
| Worker                               | Start Worker Session, update Worker pill                                            |
| Part, previous Part awaiting Machine | Error — Machine barcode required first; nothing recorded                            |
| Part, single context                 | Quantity entry (if required) → record (armed Machine) or hold awaiting Machine      |
| Part, multiple contexts              | Ambiguity dialog (§4.5)                                                             |
| Part, no active context              | Offer to create an internal Production Request (Production / Rework / Modification) |
| Unknown Part / unrecognized          | Error feedback, nothing recorded                                                    |
| Request Type                         | Combine with pending/next Part scan; order-independent (Part→Rework ≡ Rework→Part)  |

## 4.5 Ambiguity dialog

Full-screen modal listing every valid context as a large tappable row: continue running Production Request(s), release queued Production Request(s), continue queued Rework/Modification Requests, create a new internal Production Request. Each row shows Request Number, status, quantity, due date, and Request Type. Esc / Cancel abandons with nothing recorded. Choices are filtered by current production context where possible (PROJECT_PROFILE §7 Barcode Resolution).

## 4.6 Quantity entry

Modal with oversized numeric keypad (62 px keys) for touch; physical keyboard digits also accepted. Shows Part, target Area/Machine, and available upstream quantity. Confirm is blocked for 0; quantities above available are rejected with an integrity error after confirm (server-validated in the real app).

## 4.7 Right column

- **Undo Last Scan** — one prominent button; only undoes scans made at this Area (PROJECT_PROFILE §10 Undo). Creates a compensating entry; disabled state when nothing is undoable.
- **Recent scans** — today's scans, newest first: Part × qty, movement description, time, status dot (green recorded / amber queued offline / red undo).
- **In this Area now** — live Area inventory: Part, Machine, quantity.

## 4.8 States

| State           | Behavior                                                            |
|-----------------|---------------------------------------------------------------------|
| Loading         | Skeleton panels; scan input disabled with "Connecting…" placeholder |
| Empty inventory | "No production in this Area" placeholder row                        |
| Offline         | Amber banner + queued count; scans continue, marked pending         |
| Sync return     | Success feedback with uploaded count; pending dots flip to green    |
| Error           | Red feedback zone; input cleared and refocused, ready for next scan |

---

# 5. Production Board

Read-only, full-screen, for large shared displays. No interactive elements except an (optional, admin-gated) settings gesture.

- Columns: Part (+ name), Current location (per-Area/Machine distribution with color dots and quantities), total Qty, Requests/PO **with external Job Numbers**, Due (date + days remaining), Time in area (per-location).
- **Priority model:** priority is an **order number across Parts** (P1, P2, … — not HIGH/MED/LOW), so there is no Priority column. Instead:
  - Priority Parts always sort **first**, in priority order (P1 at top).
  - Non-priority Parts follow, sorted by due date (most urgent first).
  - Priority Parts are marked with 🔥 + a `P#` chip and a row tint that gets **redder the hotter the priority** (P1 strongest).
- **Due-soon blink:** when a Part approaches its due date (threshold configurable), its Part Number text blinks. Blink is reserved for due-date urgency only — nothing else on the board may blink.
- **Time in area is per-location:** when a Part's quantity is distributed across multiple Areas/Machines, the Time-in-area column shows one sub-line per location, aligned with the Current-location rows (e.g. `Cut · 3h 40m / Lathe 1 · 2h 05m / Lathe 3 · 1h 25m`). A line turns amber past the Route Step's expected duration.
- Long lists rotate pages automatically (page indicator + interval in footer). Footer carries the legend (🔥 P# = priority order, blinking = due soon) and aggregate stats.
- Auto-refresh; a "Live" indicator plus last-updated time when the feed is stale.

---

# 6. Area Board

Focused per-Area view, interactive (wall tablet or desktop).

- Area tab strip with color dots and item counts; one active Area at a time.
- Toolbar: text search (Part / PO / Request Number / external Job Number) and sort selector (Due date, Priority, Time in area, Quantity).
- Card grid, one card per Part-in-Area:
  - Part Number (🔥 + `P#` chip for priority Parts), PO/Request + Operation (+ Request Type when not Production), and **external Job Number** (`Ext. Job: ERP-88112`, or `— (internal)` for internal Production Requests).
  - Quantity presented as a **right-aligned block** with an explicit label ("IN THIS AREA" / big number / "pcs"), visually separated by a divider so the number can't be misread as anything else.
  - Per-Machine chips, due text with the same color ramp as the Production Board.
- Empty state: "No production in {Area}".

---

# 7. Tracking

Primary management interface. Master–detail layout: filterable Production Request list (left) + Production Request detail panel (right).

## 7.1 Filters and list

Search across Part Number, PO, Request Number, external Job Number, and requester; selects for Area, Request Type, Status, Due window (PROJECT_PROFILE §16 Tracking). List columns: Request, **Ext. Job No.** (— for internal Production Requests), Part, Type, Requested qty, Completed x/y, Due, Status pill (Running / Queued / Completed).

## 7.2 Detail panel sections

1. **Production Request information** — type, requested qty, due, priority (order number, e.g. "🔥 P1 — first in queue", blank for non-priority), external Job No.(s), requester, completion progress bar.
2. **Quantity distribution (live)** — horizontal bars per Area/Machine in Area colors; quantities always sum to active total.
3. **Assigned route** — step chips: done (green) → current (blue) → future (neutral); deviations marked (orange, ⚠) with who/when/reason caption. Original route retained in audit view.
4. **Movement history (immutable)** — reverse-chronological: timestamp, type (Receive/Transfer/Complete/Undo/Adjustment color-coded), full description (areas, qty, machines, worker, station). Read-only; no edit affordances exist.
5. **Corrections** — authorized-only actions as explicit buttons: Quantity adjustment, Edit assigned route, Reallocate completion, Change priority. Every correction flow requires a reason and produces new history.

## 7.3 States

Loading skeletons per section; empty filter result ("No Production Requests match — clear filters"); permission-restricted users see the Corrections section hidden entirely rather than disabled.

---

# 8. Administration

Isolated from production. Sidebar navigation grouped as:

- **Organization:** Departments, Areas, Machines, Workers
- **Production setup:** Route Templates, Barcode configuration, Allocation policy
- **Access:** Users, Roles & permissions
- **System:** Worker policies, Offline behavior, Settings

**Operation is a property of the Area**, configured inside the Area editor — Operations are not managed as separate objects and have no dedicated administration page.

> ⚠ **PROJECT_PROFILE impact:** §15 Administrator and §16 Administration list "Operations" as a separately configured item; PROJECT_PROFILE should be updated to reflect Operation as an Area attribute.

Each section is a standard table + editor pattern. The Areas table is the reference example: Area (color + name), Operation, Machine tracking on/off, Machines, Worker ID mode (Disabled / Default worker / Scan required), Active status.

Editing an Area's display properties shows an inline note that identity is stable and history is unaffected. Destructive operations (deactivating an Area with active quantities) are blocked with an explanation, not confirmed through.

---

# 9. Completion / Receiving UI (Stockroom Scan Station)

The Stockroom station reuses the Scan Station shell with one additional step: after quantity entry, an **allocation dialog** shows the suggested split across outstanding Production Requests (per configured policy — Earliest Due Date / Priority / FIFO / Manual). The operator can adjust each Request amount with +/− steppers or keypad; Confirm is enabled only when allocated total equals completed quantity. No Manager involvement in the normal path (PROJECT_PROFILE §9).

---

# 10. Out of Scope for v1 UI

Deferred intentionally: dark/light user toggle (theme is fixed per view class), localization framework (UI ships in English using PROJECT_PROFILE vocabulary), charts/analytics dashboards, mobile-phone layouts (tablet-first), administrative command barcodes.

---

# 11. Open Questions

1. Should the Production Board offer a per-Area filtered mode, or is that fully covered by Area Board?
2. Worker Session expiry duration and its visual countdown — policy value lives in Administration; default TBD.
3. Whether Undo requires Worker identity when Worker scanning is enabled for the Area.
4. Awaiting-Machine timeout: how long may a Part+quantity wait for its Machine scan before auto-cancelling (nothing recorded)?
5. Non-priority sort: confirmed as most-urgent due date first — confirm the tie-breaker (time in area vs. Part Number).

---

# 12. Pending PROJECT_PROFILE Updates

Decisions made during GUI design that supersede the current profile text; PROJECT_PROFILE should be amended:

1. **Machine selection (§5 Machine, §13 Machine Selection):** Machine is selected only by scanning the Machine barcode, one scan required per Part; the selection never persists across Parts. Order Part→Machine or Machine→Part is both valid; a second Part scan while one is awaiting its Machine is rejected.
2. **Operations administration (§15, §16):** Operation is an attribute of an Area configured in the Area editor, not a separately managed object.
3. **Priority (§5 Production Request, §16 Production Board):** priority is an ordering number across Parts (P1, P2, …), not a level (High/Med/Low). Priority Parts sort first; non-priority Parts sort by due date.
