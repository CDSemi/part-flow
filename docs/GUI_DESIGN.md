# PartFlow GUI Design v7

> **Status:** Current — companion to [PROJECT_PROFILE.md](./PROJECT_PROFILE.md) (v8).
> This document specifies the user interface only. Business rules, terminology and workflows are defined in PROJECT_PROFILE and are not redefined here.
> An interactive mockup accompanies this document: `mockups/partflow-gui-mockup-v7.html`.
> Supersedes GUI Design v6 (mockup: `archive/partflow-gui-mockup-v6.html`); the differences are listed in §12.1. Differences from v5, v4, v3, v2 and v1 remain listed in §12.2–§12.6.

---

# 1. Scope

Covers the application-view content of PROJECT_PROFILE §21 in **seven GUI views**:

1. Scan Station (production)
2. Production Board (monitoring, large display)
3. Area Board (monitoring — **All Areas** overview + per-Area detail; the overview carries the §21 *Manager Summary* content)
4. Tracking (management, PN-centric)
5. Work Orders (management, manual Work Order entry and production release — §21 *Work Orders*)
6. Priority Management (Hot Work Order Demand ranking)
7. Administration (configuration)

> **Pending PROJECT_PROFILE alignment (v4).** PROJECT_PROFILE v8 still lists Area Board and Manager Summary as separate views in §21. GUI v4 merges Manager Summary into Area Board as its All Areas overview (no content dropped). (Two earlier deviations are now resolved: PROJECT_PROFILE v8 §8.2 defines the nullable Work-Order-level `due_date` used as the entry default for demand-line due dates, and the former "PO Intake" view name was resolved by the v7 vocabulary migration — PROJECT_PROFILE §21 names the view **Work Orders**.) PROJECT_PROFILE §21 still needs the view-merge update; until then this document notes the deviation where it occurs.

## 1.1 Navigation structure

Top-level navigation exposes four entries; the management views are grouped as **sub views of one Management view**:

| Top level        | Contains                                                |
|------------------|----------------------------------------------------------|
| Scan Station     | — (single view)                                         |
| Production Board | — (single view)                                         |
| Management       | Area Board · Tracking · Work Orders · Priority          |
| Administration   | — (single view, own sidebar navigation per §9)          |

Selecting Management opens its **last-used sub view** (Area Board on first open, which itself opens on its All Areas overview) and reveals a secondary sub-view bar beneath the top navigation. All navigation chrome follows the global theme mode (§2.1). The grouping is navigation only — each sub view keeps its own specification (§6–§8, §11).

---

# 2. Design System

## 2.1 One token set, two switchable themes

The application has a single global **theme mode — Dark or Light — switchable by the user**; **every view follows the selected mode** (v5; supersedes the fixed per-view themes of v2–v4).

- The toggle lives in the top navigation bar and applies to the whole application instantly, including navigation chrome, dialogs and toasts. No view has a fixed theme anymore.
- **Dark is the default**: PartFlow is shop-floor first — dark reduces glare in the shop, is readable from distance, and is tolerant of low-quality displays. Desk users who prefer light for dense data work switch once.
- All component styling uses **semantic tokens only** (background, panel, border, text, muted, status-text, …); the two theme definitions supply the values. Adding or restyling a component never hard-codes a theme-specific color.
- Status colors (§2.2) keep per-theme *text* variants so contrast holds in both modes (e.g. success text is bright on dark, deepened on light); status backgrounds/tints are shared. Area identity colors are identical in both themes.
- In the mockup the choice is session-only; how the real application persists it (per user, per station, or both) is an open decision (§14).

Both themes share the same color tokens, spacing scale, and typography so the product feels like one system in either mode.

## 2.2 Color tokens

Status colors (semantic, never decorative):

- **Success** `#31d287` — recorded Movement, confirmed action
- **Warning** `#ffb224` — needs attention, pending context, route deviation, due soon
- **Error** `#ff6166` — rejected scan, integrity violation, overdue
- **Info / accent** `#4f8cff` — selection, focus, primary action

Area colors: every Area has a stable identity color used consistently in **all** views (chips, dots, distribution bars, column headers). Colors are Area display properties and editable in Administration without affecting history (PROJECT_PROFILE §7 Area).

Initial palette: Material `#8b93a8`, Cut `#f5b83d`, Lathe `#3da5ff`, Mill `#9b6ef3`, Manual `#e06fae`, Deburr `#2fbf9b`, External `#ff8a4c`, Stockroom `#2fca7c`.

## 2.3 Typography

- UI text: system font stack (`system-ui, Segoe UI, Roboto…`) — no webfont dependency, works offline.
- Identifiers (PN, Work Order Number, temporary Work Order, external Job Number, Quantity Flow id, barcode values, quantities, timestamps): monospace. Identifiers must be visually distinct from prose because operators read them against paper travelers and folder labels.
- Shop-floor minimum sizes: body 16 px, PN ≥ 19 px, quantities ≥ 18 px bold. Production Board is sized for reading at 3–5 m (PN 22 px+, key figures 18 px+).
- The PN is always rendered on a single line and never wraps. Where the container cannot grow with the identifier (Area Board cards and overview lists), an over-long PN is **truncated with an ellipsis** and the full PN is shown in a tooltip on hover; layout-critical figures (quantities) must never move because of PN length. Views sized around the PN (Production Board, Tracking) keep sizing the column to fit it.
- **Realistic identifier and description shapes** (v6; PROJECT_PROFILE §8.1). PNs are opaque, commonly multi-segment hyphenated numeric strings of varying length (short `214-406` through long `0455-20-0118-03` and longer); the UI preserves the exact string, never derives meaning from segments, and search/filtering matches the hyphenated value and its punctuation literally. Work Order Numbers and external Job Numbers are likewise opaque strings — commonly numeric-looking (Work Order Number `007482`, Job Number `18427`) and never parsed, padded, or reformatted; the two are separate identifiers and are never combined. Revision is a separate optional informational field (`A`, `B`, `E`, or blank): it is never appended to the PN, and a blank Revision renders cleanly without placeholder noise. Part names/descriptions are free text — often uppercase with commas, slashes, fractions, dimensions and manufacturing abbreviations — and **may wrap naturally to two or three lines**; description wrapping must never shift quantity, status, date, or action columns (cap the description's width instead of widening the identifier column).

## 2.4 Touch and scanner ergonomics

- Minimum touch target 48×48 px; primary Scan Station actions ≥ 56 px tall.
- All production actions reachable by scan or single tap. Mouse never required on shop-floor views.
- Keyboard wedge support: the scan input is a plain text input terminated by Enter — no custom driver, no scan-mode selection (PROJECT_PROFILE §10 Barcode Model).

---

# 3. Global Interaction Rules

These apply to every view; they implement the profile's core principles in UI terms.

1. **Focus discipline.** On the Scan Station, the barcode input regains focus automatically after every completed operation, dialog close, session change, and view activation. Nothing may steal focus permanently.
2. **Ambiguity requires confirmation.** Whenever a scan resolves to more than one valid production context, the UI presents an explicit choice list (§4.6). The UI never guesses and never defaults silently; nothing is recorded until the choice is made (PROJECT_PROFILE §10 Barcode Model).
3. **Feedback is tri-state and instant.** Every scan produces exactly one of: Success (green), Warning (amber — recorded but needs awareness, or an action is pending), Error (red — nothing recorded). Feedback shows *what happened* and *why* in one line each.
4. **Quantity integrity is visible.** Quantity entry displays the available source quantity. Attempts to move more than available are rejected with an explicit error — the UI explains the limit rather than clamping silently (PROJECT_PROFILE §11 Quantity Model).
5. **History is append-only in the UI too.** Undo appears as a new `REVERSED` entry in the scan list; the original entry stays visible. Tracking's Movement history has no edit/delete affordances (PROJECT_PROFILE §16).
6. **Connectivity loss is an explicit write-blocked state, detected fast.** Detection combines browser `online`/`offline` events with polling (no WebSocket/SSE in Phase 2): `offline` marks the application unavailable immediately; `online` triggers an immediate health check; `GET /api/health` is polled about every second with a request timeout below the probe interval and no overlapping probes; connectivity is rechecked on tab focus/visibility change. Passive probes never flip the UI to a "connecting" state — no flicker. Losing the connection shows a persistent OFFLINE / DISCONNECTED banner with an actionable message; production write submission is disabled while disconnected. Already loaded read-only information stays visible where practical. No production write is queued locally, no pending Movement indicator is shown, and the UI never claims that scans will synchronize later. On reconnection, input readiness and focus are restored — the Scan Station input is re-enabled and refocused. **A scan is successful only after the server confirms the write:** connectivity status is an early warning, never permission to record a Movement optimistically. If connectivity disappears between the last heartbeat and a write request, the request fails as "nothing recorded"; the UI must never display a false recorded result (PROJECT_PROFILE §15). This rule must be preserved unchanged in the production phases. (Offline scan synchronization remains deferred and unapproved — PROJECT_PROFILE §30, §32.4.)
7. **PartFlow vocabulary everywhere.** UI labels use the canonical names: PN, WO / Work Order, Work Order Demand, Request Type (`NEW` / `REWORK` / `MODIFY`), Quantity Flow, Route, Movement types (`RECEIVED`, `TRANSFERRED`, `ASSIGNED_TO_MACHINE`, `SPLIT`, `MERGED`, `STOCKED`, `REVERSED`, …), Work Order Allocation, Hot (PROJECT_PROFILE §7).
8. **Missing due dates are valid data.** Work Order and demand-line due dates may be absent (PROJECT_PROFILE §8.2, §8.3). A missing due date renders as `No due date` where a note line exists and as `—` as the compact/table date value — consistently, and never as an error or warning state. Wherever demand is due-date ordered, the canonical demand ordering applies (PROJECT_PROFILE §18): dated demand first, earliest first; undated demand after all dated demand, ordered by the parent Work Order's received date, with a stable deterministic tie-breaker.

---

# 4. Scan Station

Fixed to one Area per station. Single screen, no navigation during normal production.

## 4.1 Layout

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ Dept / AREA (color) / Operations   [Machine session][Worker session][●Online] │
│ (disconnected banner when connectivity is lost — writes blocked)              │
├──────────────────────────────────────┬────────────────────────────────────────┤
│ Machine status strip (scan to select)│  UNDO LAST SCAN                        │
│ Scan input (large, focused)          │  Recent scans (today)                  │
│ Pending scan context banner          │  In this Area now:                     │
│ Last scanned PN                      │    – assigned to Machines              │
│ Feedback zone                        │    – Area queue (awaiting Machine)     │
├──────────────────────────────────────┴────────────────────────────────────────┤
│ Station LATHE-ST-01                        (faint diagnostic caption, bottom) │
└──────────────────────────────────────────────────────────────────────────────┘
```

## 4.2 Header

Department, Area name with Area color, and supported Operations stay prominent. Session pills for the active **Machine session** and **Worker session** (both show `—` when not applicable per Area configuration), each with a caption describing its lifetime. Connectivity indicator with explicit ONLINE/OFFLINE text — color alone is not sufficient.

The **Station ID is not part of the header** (v7). It renders as a faint diagnostic caption at the bottom edge of the view (e.g. `Station LATHE-ST-01`) — present for troubleshooting, visually recessive so it never competes with production information.

## 4.3 Machine sessions — scan-only, sticky per §19

Applies only when the Area requires Machine selection (`machine_assignment_mode`).

- Machine selection happens **exclusively by scanning a Machine barcode**. The on-screen machine strip is a read-only status display (idle / running / maintenance) that highlights the Machine of the active session.
- The selected Machine becomes a **session**: it remains active for subsequent PN scans until it is changed, cleared, or expires (PROJECT_PROFILE §19). The active Machine is always visible in the header pill.
- Scan order is flexible (PROJECT_PROFILE §12, §15):
  - **PN → Machine:** the received quantity enters the Area queue and the UI shows a pending-context banner ("scan a Machine barcode to assign"); scanning the Machine records `ASSIGNED_TO_MACHINE`.
  - **Machine → PN:** with an active Machine session, a PN scan assigns directly to that Machine.
- Areas configured for direct single-Machine assignment auto-select the Machine; Areas without Machines skip Machine handling entirely. Behavior comes from Area configuration, never from machine count (PROJECT_PROFILE §12).
- Inactive Machines (e.g. maintenance) are rejected with an error and never accept production updates.

## 4.4 Scan input and resolution

One input accepts every barcode type; the system classifies the scan deterministically from the `PF:` prefix (PROJECT_PROFILE §10 Barcode Model). Resolution outcomes:

| Scan resolves to                       | UI behavior                                                                     |
|----------------------------------------|---------------------------------------------------------------------------------|
| Machine, pending PN context            | Record `ASSIGNED_TO_MACHINE` for the pending quantity, success feedback         |
| Machine, no pending context            | Start/replace the Machine session                                               |
| Worker                                 | Start Worker Session, update Worker pill; previous Worker signs out             |
| PN, single context                     | Quantity entry (only if required) → record; assign if a Machine session is active, otherwise receive into Area queue |
| PN, multiple contexts                  | Ambiguity dialog (§4.6)                                                          |
| Action barcode (`PF:ACTION:REWORK/MODIFY`) | Armed for the next PN scan; order-independent (PN → Action ≡ Action → PN)    |
| Unknown PN / unrecognized value        | Error feedback, nothing recorded; raw ERP text is never auto-accepted           |
| Inactive entity                        | Error feedback, nothing recorded                                                |

Manual PN entry is a **visible secondary action** under the scan input — a button labeled **⌨ Enter PN manually** with a caption explaining that it is the fallback when the scanner is unavailable. It opens a **separate manual-entry flow** that accepts the exact PartNumber text and resolves it through the same server-side validation as a scan. It never pre-fills or reuses the barcode input, and raw PN text is never silently interpreted as barcode input (PROJECT_PROFILE §10 Barcode Model).

## 4.5 Pending scan context and last scanned PN

Two persistent context surfaces required by §21:

- **Pending scan context** — an amber pulsing banner whenever the station holds an incomplete intent (e.g. quantity received into the queue awaiting its Machine scan, or an armed Request Type). It names the PN, quantity and the scan that completes the action.
- **Last scanned PN** — a fixed strip showing the most recent PN with a one-line description of what happened.

## 4.6 Ambiguity dialog

Full-screen modal listing every valid context as a large tappable row, filtered to the current station where possible. Typical contexts: assign queued quantity to a Machine, receive additional quantity from an upstream Area, create `REWORK` demand, create `MODIFY` demand. Each row shows the relevant quantities, Work Orders and due dates; REWORK/MODIFY rows explain the temporary-Work-Order fallback (`TMP-YYYYMMDD-HHMM-REWORK`) used when no active Work Order applies (PROJECT_PROFILE §14). Choosing REWORK or MODIFY does **not** record anything immediately — it opens a prefilled intake/confirmation flow (Request Type, quantity, link to an applicable Work Order or temporary internal Work Order, Route and starting Area); `RECEIVED` is appended only after that confirmation. Esc / Cancel abandons with nothing recorded.

## 4.7 Quantity entry

Modal with oversized numeric keypad (62 px keys) for touch **and full physical-keyboard support** (v7): digits append, Backspace removes one digit, Delete/Clear clears the value, Enter confirms when the quantity is valid, Escape cancels. Shows PN, source → target context, and available source quantity. Confirm is blocked for 0; quantities above the available source are rejected with an integrity error (server-validated in the real application) — keyboard input does not bypass any validation.

## 4.8 Right column

- **Undo Last Scan** — one prominent button; only undoes recent eligible scans made at this Area. Creates a `REVERSED` compensating Movement referencing the original (PROJECT_PROFILE §16); disabled when nothing is undoable.
- **Recent scans** — today's scans, newest first: PN, Movement type, description, time, and a recorded/reversed status. The **Movement type is an explicit field** (mock model field `movementType: MovementType`) rendered as its own badge next to the recorded/reversed status, separate from the PN and the description; movement types are never embedded at the start of description strings.
- **In this Area now** — live Area inventory split into two labeled groups per §21: quantity **assigned to Machines** (with Machine name) and quantity in the **Area queue** awaiting Machine assignment, with the Area total beneath.

## 4.9 States

| State           | Behavior                                                            |
|-----------------|---------------------------------------------------------------------|
| Loading         | Skeleton panels; scan input disabled with "Connecting…" placeholder |
| Empty inventory | "No production in this Area" placeholder row                        |
| Disconnected    | Persistent OFFLINE banner with actionable message; production write submission disabled; loaded read-only data stays visible |
| Reconnected     | Banner clears; scan input re-enabled and refocused, ready for the next scan |
| Error           | Red feedback zone; input cleared and refocused, ready for next scan |

---

# 5. Production Board

Read-only, full-screen, for large shared displays. No interactive elements except an (optional, admin-gated) settings gesture.

- **Header clock (v7):** the header shows the live local date **and time**, driven by a self-updating clock component — never a static mock timestamp.
- Columns (PROJECT_PROFILE §21): **No. · Part Number · Areas & Quantities · Time · Due Date · Total Days · Job Numbers** — Job Numbers is deliberately last (v7).
- **Content-driven column sizing (v7):** column widths come from semantic `colgroup` classes, not fixed percentage widths. No., Part Number, Due Date and Total Days shrink to their content and never wrap (Due Date keeps a minimum width so its secondary line always fits); Job Numbers absorbs the remaining width and may wrap.
- **Part Number** renders on a single line — the column is sized so the Hot indicator and the PN never wrap. Name and revision appear as a secondary line.
- **Standard Hot Part presentation (v7):** a Hot entry renders as `🔥#1 2027-60-8114-00` — flame and rank immediately **before** the PN, never as a separated rank chip after it. This is one shared component (`HotPn` in `src/components/indicators.tsx`) used identically on the Production Board, the Area Board (All Areas and per-Area detail), Tracking, and every Priority-related PN presentation.
- **Areas & Quantities · Time** uses a **stable grid** (v7): Area/Machine label, quantity, machine/queue tag, and time occupy consistent columns so all quantities align vertically — including the `total … pcs` line — and a long Area or Machine name never pushes the quantity out of alignment. Each row shows the Area color dot, Area/Machine name, quantity, a `machine` / `queue` tag where relevant, and the **time in that location** right-aligned in monospace. A time turns amber when it exceeds the expected duration of the active Route Step. A dashed separator shows the PN total underneath.
- **Due Date** carries a highlighted secondary line with days left (amber = due soon, red = overdue, neutral = comfortable / stocked). There is no separate Days Left column. A missing due date renders as `—` (§3.8).
- **Priority model:** Hot Work Order Demand rank from Priority Management. Hot rows sort first in rank order with a row tint that gets redder the hotter the rank; non-Hot rows follow in the canonical demand ordering (PROJECT_PROFILE §18) — dated rows earliest-first, undated rows after all dated rows ordered by Work Order received date.
- **Due-date urgency (v7):** only the urgency text — days remaining / days overdue — blinks (threshold configurable); the PN and the date itself stay steady. The footer legend states this. `prefers-reduced-motion` disables the blink animation while keeping the warning color and weight. Blink remains reserved for due-date urgency only — nothing else on the board may blink.
- **Dynamic pagination (v7):** pages are calculated from the **actual available board height and actual rendered row heights** (via a hidden measurement table), recalculated on viewport/container resize, data changes, and theme/font-metric changes (ResizeObserver plus window resize). At least one row is always shown per page; the active page clamps when the page structure changes. Pages rotate automatically every 12 s **only when more than one page exists**; the page indicator is accurate and a single page never claims rotation. (In DOM environments without layout — tests — a fallback of 10 rows per page applies.)
- Footer carries the legend and aggregate stats (active PNs, pcs in production, pcs stocked).
- Auto-refresh; a "Live" indicator plus last-updated time when the feed is stale.

---

# 6. Area Board

Monitoring sub view of Management (follows the global theme mode, §2.1). One view, two modes behind a single tab strip: the **All Areas overview** (the §21 *Manager Summary* content) and the **per-Area detail** (the §21 *Area Board* content). The "Manager Summary" name is retired.

## 6.1 Tab strip and toolbar

- The tab strip starts with **All Areas** — the default on first open — followed by one tab per Area with its color dot and item count. Exactly one tab is active.
- Shared toolbar for both modes: text search (PN / WO / Job Number), sort selector (Due date, Priority, Time in Area, Quantity), and a scope summary (PN count and total pieces for the active Area, or across all Areas in the overview).

## 6.2 All Areas overview

One column per Area; the layout scrolls horizontally when all Areas do not fit. Each Area column shows:

- a **clickable header** (Area color, name, description, supported Operation chips) with a "detail ›" affordance — clicking it opens that Area's detail mode, equivalent to selecting its tab;
- a three-value stat row: **total pcs · queued · on machines** (the terminal Stockroom column shows **stocked pcs** instead);
- the PN list for the Area: PN (🔥 + rank when Hot), quantity, WO + Job Number, due date (color-ramped), Machine distribution or time in Area;
- an explicit empty state for Areas without production.

Search filters the PN lists; the sort selector orders PNs within each column.

## 6.3 Per-Area detail

The detail mode is a monitoring layout of **one Area summary card followed by one monitoring card per Machine of the Area** (v7; supersedes the v2–v6 "one card per PN" grid):

- **Area summary card (first):** Area name, description and supported Operations; a stat row — PN count, total pcs, queued pcs, pcs on machines, Hot count; then a **compact PN list in the All Areas visual language**, grouped **"Assigned to Machines"** first, then **"Area queue — awaiting Machine"** (the terminal Stockroom shows **"Stocked"** instead). Each entry shows the Hot indicator + PN (`🔥#n` before the PN, §5), quantity, machine or queue context, WO + Job Number, due status, and time in Area. Grouping never duplicates or loses quantity.
- **Machine monitoring cards:** one card per Machine of the Area — machine name, status (running / idle / maintenance), total quantity assigned, PN count, and the assigned PN list with quantity, WO, Job Number, due status and time in Area. Idle machines show a clear empty state; maintenance machines are visually distinct (dashed warning border). In Phase 2 the Machines come from the mock model `MOCK_AREA_MACHINES` (Cut → Saw 1; Lathe → Lathe 1 idle, Lathe 2 running, Lathe 3 running, Lathe 4 maintenance; Mill → Mill 1, Mill 2).
- Areas without Machines render **only the summary card** — no placeholder Machine cards.
- Search and the sort selector (§6.1) still narrow and order the PN lists. **Sort: Time in Area** orders by a sortable duration field (`timeInAreaMinutes` in the mock model), longest first.
- An over-long PN is truncated with an ellipsis; hovering shows the full PN as a tooltip (§2.3).
- Empty state: "No production in {Area}".

---

# 7. Tracking

Primary management interface, **PN-centric** per PROJECT_PROFILE §21. Master–detail layout: filterable PN list (left) + PN detail panel (right).

## 7.1 Filters and list

Search across PN, WO and Job Number; selects for Area, Operation, Machine, Request Type, priority (Hot only), status, and due window. List columns: PN (+ name; Hot entries use the standard `🔥#n` presentation before the PN, §5), active WO Demand (WO · qty · Request Type chip), current distribution (Area color dots), active quantity, stocked quantity, next due date (`—` when the demand has no due date, §3.8), status pill (Active / Stocked / Completed).

## 7.2 Detail panel sections

1. **PN master** — image placeholder, PN, name, current revision (informational), barcode value (`PF:PN:…`), ERP id.
2. **Active WO Demand** — table of WO · Request Type · requested · allocated · remaining shortage · due · priority, with an allocation progress bar. Labeled "business demand — separate from Movement".
3. **Current quantity by Area** — horizontal bars per Area/Machine in Area colors, queue rows visually distinct; labeled "derived from Movement history".
4. **Quantity Flows & assigned Routes** — one block per Quantity Flow: flow id, quantity, current position, split/merge lineage, and its own route chips: done (green) → current (blue) → queued (amber) → future (neutral); deviations marked (orange, ⚠) with who/when/reason caption and a note that the previous route is preserved in audit. The section explicitly avoids implying the whole PN is at one Route Step.
5. **Movement history (immutable)** — reverse-chronological: timestamp, Movement type (color-coded, canonical names), full description (areas, quantity, Quantity Flow, machines, worker, station). Read-only; no edit affordances exist.
6. **Stocked & Allocation history** — stocked quantity and Work Order Allocation entries with the §18 ordering note; empty state when nothing is stocked.
7. **Corrections** — authorized-only actions as explicit buttons: Quantity adjustment, Edit assigned Route, Adjust WO Allocation, Change priority, View audit trail. Every correction flow requires a reason and produces new history.

## 7.3 States

Loading skeletons per section; empty filter result ("No PNs match — clear filters"); permission-restricted users see the Corrections section hidden entirely rather than disabled.

---

# 8. Priority Management

Manages the Department's Hot Work Order Demand list (PROJECT_PROFILE §21 Priority Management). Priority belongs to Work Order Demand; multiple Work Orders for the same PN may hold different ranks.

- The list shows each Hot entry with its rank in the standard Hot presentation (`🔥#n` immediately before the PN, §5), WO + Job Number, Request Type chip, demand figures (requested / allocated / shortage), current distribution, and color-ramped due date (`—` when absent, §3.8).
- **Add:** the "+ Add to Hot list" button opens a dialog with a single search field that accepts free text (PN / WO / Job Number) *and* PN barcode scans — scanning with the dialog open adds the matching Work Order Demand directly. If a PN has multiple active Work Order Demand records, each is listed and added separately. New entries always join at the **bottom** of the list.
- **Remove:** each entry has a remove (✕) affordance. Clicking it opens a **confirmation dialog identifying the PN and Work Order Demand**; Cancel changes nothing. On Confirm the entry is removed, remaining ranks close the gap, the change is applied and audited, and Undo can restore it. Confirmation guards the removal; it does not reintroduce a separate save-or-cancel workflow.
- **Reorder requires confirmation (v7):** every operation that changes the order of existing Hot entries — drag-and-drop (visible grip), Move Up, Move Down, Undo, and Redo — opens a **confirmation dialog before applying**, showing the affected PN + Work Order Demand, the previous rank, the proposed new rank, and the action type. Cancel leaves the list and both Undo/Redo histories unchanged; the visible list is **never renumbered before confirmation**. Adding a new entry at the bottom keeps its direct behavior (no order-change confirmation).
- **Undo / Redo instead of save-or-cancel:** every confirmed change is applied, audited, and can be stepped back and forward with Undo/Redo buttons — each step is itself an order change and shows the same confirmation. The buttons disable when the corresponding history is empty.
- A footer note restates the rules: Hot is a label on top of `priority_rank`, and ordering follows the canonical demand ordering — ① Hot rank ② dated demand earliest-first, with undated demand after all dated demand ordered by Work Order received date; a stable deterministic tie-breaker is an implementation detail, not a business rule (PROJECT_PROFILE §18).

---

# 9. Administration

Isolated from production. Sidebar navigation grouped as:

- **Organization:** Departments, Areas, Operations, Machines, Workers
- **Production setup:** Route Templates, Barcode configuration, Scan behavior
- **Access:** Users, Roles & permissions
- **Policies:** Worker sessions, Machine assignment, Correction permissions, Settings

Operations are managed per PROJECT_PROFILE §8.5 — each Operation belongs to an Area, and the Areas table lists the Operations an Area supports.

Each section is a standard table + editor pattern. The Areas table is the reference example: Area (color + name), Operations, Machine assignment mode (None / Auto-assign single Machine / Queue → select by scan), Machines, Worker ID mode (Disabled / Fixed Worker / Scanned session), Terminal flag, Active status.

Editing an Area's display properties shows an inline note that identity and barcode are stable and history is unaffected. Destructive operations (deactivating an Area with active quantities) are blocked with an explanation, not confirmed through.

---

# 10. Completion / Receiving UI (Stockroom Scan Station)

The Stockroom station reuses the Scan Station shell with one additional step: after the `STOCKED` Movement, an **allocation dialog** shows the suggested split across outstanding Work Order Demand in the exact §18 canonical demand ordering — ① highest Hot rank ② dated demand earliest-first, undated demand after all dated demand ordered by Work Order received date. Each row shows the Work Order, requested quantity, previously allocated quantity, remaining shortage and the proposed quantity, adjustable with +/− steppers — Operators may review and adjust the suggestion before confirmation (PROJECT_PROFILE §18). Confirm is enabled only when the allocated total equals the stocked quantity. Routine receiving requires no Manager involvement; Admin and Manager may adjust the allocation later, with every change audited.

---

# 11. Work Orders

Management sub view (follows the global theme mode, §2.1) implementing manual Work Order entry and explicit production release (PROJECT_PROFILE §13; §21 *Work Orders* — the view was called *PO Intake*, then *Purchase Orders*, before the v6 vocabulary migration, §12.2). It handles business demand only — it is not ERP-style customer, pricing, invoicing, shipping, purchasing, or accounting functionality.

The view has two panels on one route (`/management/work-orders`): the **WO list** (master, the entry screen) and the **WO detail** (demand lines of one selected Work Order). **New Work Order** is a **modal dialog over the WO list** (v6) — it never replaces the list and never changes the URL.

**Editable dates use native calendar controls** (`<input type="date">`) in every Work Orders form: New Work Order received date and WO due date, the OPEN Work Order due date, and every demand-line due date. Editable values are ISO `YYYY-MM-DD` internally; read-only presentation formats them as `Jul 24, 2026`. No date-picker dependency is added — the native accessible control works in both theme modes (`color-scheme` follows the theme) and stays keyboard-accessible.

In Phase 2 every Work Orders interaction changes **development-only local mock state** and says so explicitly; nothing is persisted to the backend.

## 11.1 WO list

- One row per Work Order: WO Number, received date, **WO due date** (color-ramped like all due dates; `—` when absent, §3.8), demand-line count with a PN preview, and status (**Open** / **Released** / **Complete**).
- Search over WO Number. An existing WO Number is always opened, never duplicated; a miss offers "＋ New Work Order".
- **＋ New Work Order opens the modal dialog of §11.3 over the list.** The list stays mounted and visible behind the overlay, and the URL remains `/management/work-orders`.
- Temporary internal Work Orders — both scan-intake `TMP-…-REWORK/MODIFY` and generated `TMP-YYYYMMDD-HHMMSS` numbers (PROJECT_PROFILE §7) — appear like any other Work Order, clearly labeled "temporary internal Work Order", and are searchable like any other WO Number.
- Completed Work Orders (every Work Order Demand fully allocated) move out of the active list but remain permanently available in history (PROJECT_PROFILE §8.2).
- **Demand lines are shown only after a Work Order is selected** — selecting a row opens the WO detail panel.

## 11.2 WO detail — demand lines

- Header: "‹ All Work Orders" back action, WO Number, received date, WO due date (**editable with a calendar control while the Work Order is Open**), line count, status.
- **WorkOrderDemand rows:** an editable table, one row per PN demand: PN (lookup or create), Request Type (`NEW` default / `REWORK` / `MODIFY`), requested quantity, due date (calendar control), priority when applicable, external Job Numbers, requester / reason / notes. Long WO line lists scroll with a sticky header and a line count.
- **Adding demand lines (v7 — manual-first):** an **OPEN** Work Order shows a prominent **＋ Add Part manually** action; CLOSED/completed Work Orders remain read-only. Add Part manually opens the multi-step Add Part dialog of §11.3 (PN → quantity → due date → optional metadata). Barcode scanning remains available as a **secondary, optional method**: it accepts only valid PN barcodes and rejects unknown barcodes. A new line joins the Work Order as a visibly marked **unsaved draft** — Request Type defaults to `NEW`, the due date defaults to the WO due date when one exists. Scanning or entering a PN already on the Work Order focuses the existing line instead of adding a duplicate; a released line is announced as read-only instead.
- **Removing demand lines (v6)** follows the canonical Work Order Demand removal rule (PROJECT_PROFILE §13):
  - an unsaved draft line is removed immediately;
  - a saved line with no released production quantity is removed only after an explicit confirmation dialog;
  - once any quantity has been released to production, the line's remove action is disabled with the explanation "Cannot remove: production quantity has already been released." — later adjustments go through correction/production workflows, never deletion;
  - removal never deletes the PartNumber master, Quantity Flows, Movements, release history, or other Work Order Demand for the same PN.
- **Due-date default (v7):** each line's due date defaults to the **WO due date** (when one exists) and may be edited per line. A blank due date is valid and displays cleanly (§3.8). Changing the WO due date updates only lines still holding the previously inherited default; a line whose due date was manually changed keeps its value, and a line explicitly set to **"No due date"** counts as user-edited — it never inherits a later WO due date.
- **PN lookup / create:** the PN field searches existing PartNumbers; a new PN can be created inline, which shows a **barcode preview** (`PF:PN:…`) and creates the unique PN barcode with the PN master. An **inactive PN** is flagged and cannot be released without reactivation.
- **Validation states (v7):** per-field errors (missing PN, quantity not a positive integer, duplicate PN); a row with errors cannot be saved and is **never silently filtered out**. A **missing due date is not an error and never blocks saving** (PROJECT_PROFILE §8.3) — absent dates are summarized in the save confirmation instead (§11.3). After a failed save the first invalid control receives focus and all entered values are preserved.
- **Unsaved changes** are visibly marked ("● Unsaved changes") and guarded: leaving the Work Order — via the back action, top-level navigation, Management sub-navigation, browser back/forward, or reload/tab close (`beforeunload`) — requires explicit confirmation before the draft is discarded.

## 11.3 New Work Order — manual-first modal dialog and Add Part flow

- **Presentation (v6):** New Work Order opens as a **modal dialog over the WO list**. The dialog has `role="dialog"`, `aria-modal="true"`, an accessible name from its visible heading, initial focus inside the dialog, keyboard focus trapping, Escape and backdrop-click close requests, focus restoration to the **＋ New Work Order** button on close, responsive sizing with internal scrolling for long line lists, and follows both theme modes. The URL never changes.
- **Nothing is silently discarded:** Escape, backdrop click, Cancel, or any other close request on a dialog with entered data first asks for explicit confirmation ("Discard this New Work Order?").
- **Header form (v7):** WO Number (**optional**), received date (calendar control, defaults to today, always available), and **WO due date** (calendar control, **optional**) — the default due date for demand lines when set. The dialog explains the consequences plainly: a blank WO Number generates a unique temporary internal Work Order Number on confirmed save (`TMP-YYYYMMDD-HHMMSS`, deterministic `-2`, `-3`, … suffix on collision — PROJECT_PROFILE §7); due dates can be added later; the Work Order can be saved without a due date. Entering a WO Number that already exists opens the existing Work Order instead of duplicating it — **duplicate handling applies only when a WO Number was entered**; if lines were already entered, opening the existing Work Order is confirmed explicitly first.
- **Manual Part addition is the primary workflow (v7):** a prominent **＋ Add Part manually** action opens **one accessible multi-step dialog** (no stacked nested modals):
  1. **PN** — search and select an existing PartNumber, or explicitly create a new one (with barcode preview, §11.2);
  2. **quantity** — positive whole number, with the same keypad + physical-keyboard interaction as the Scan Station quantity dialog (§4.7);
  3. **due date** — defaults to the WO due date when one exists; an explicit **"No due date"** choice is valid;
  4. **optional metadata** — Job Numbers, Notes, Request Type.
  Backward navigation preserves entered values. Completing the dialog creates an editable **draft row**; nothing is persisted until Save demand.
- **Barcode scanning remains available as a secondary, optional method:** the scan input accepts only valid PN barcodes (`PF:PN:…`); unknown or non-PN barcodes are rejected with an error — nothing is added. Scanning a PN that is already on the Work Order focuses the existing line (edit its quantity) instead of adding a duplicate. The Work Order entry workflow is **no longer scanner-first** — it is desk work; the Scan Station shop-floor workflow (§4) remains scanner-first and is unchanged.
- Changing the WO due date updates only lines still inheriting the previous default; lines whose due date was edited — including an explicit "No due date" — keep their value (§11.2). Request Type and due date stay editable per line before saving.
- **Save demand with omission confirmation (v7):** Save validates the header and all rows (§11.2 validation states — field-level errors, first invalid control focused, values preserved, incomplete rows never dropped; missing due dates never block saving). When the external WO Number, the WO due date, or any line due dates are absent, a **confirmation dialog summarizes the omissions** before anything is saved: a temporary internal number will be generated, the Work Order remains unscheduled, and N undated lines get the lowest due-date priority with later received-date ordering (PROJECT_PROFILE §18). Save happens only after explicit confirmation; Cancel returns to editing with all values preserved. On save the dialog closes and the new Work Order appears in the list as **Open**. In Phase 2 the save changes local mock state only and reports that nothing was persisted to the backend.

> **Data-model note:** the WO due date is the nullable `due_date` attribute on WorkOrder (PROJECT_PROFILE v8 §8.2). It is an entry default only — WorkOrderDemand keeps its own nullable `due_date` as the operative business value.

## 11.4 Demand save vs. production release

- **Save demand** persists the WorkOrder and WorkOrderDemand rows only. Saving never creates production quantity — the UI states this explicitly ("business demand — separate from production").
- Each saved demand row carries an explicit **Release to production…** action. Releasing opens a confirmation flow that:
  1. confirms the release quantity;
  2. confirms or assigns the Route (snapshot noted);
  3. confirms the configured starting Area and Operation;
  4. **warns when the PN already has active quantity**, showing the existing distribution and requiring explicit confirmation of intent — never auto-creating or auto-merging quantity;
  5. shows a **release summary before commit** (PN, quantity, Route, starting Area/Operation, Work Order Demand);
  6. on Confirm, reports the result: created Quantity Flow id, assigned Route, starting Area, quantity, and the appended `RECEIVED` Movement.
- Saving demand never triggers a release automatically; release is always a separate explicit action.

---

# 12. Changes from previous versions

> Historical entries in §12.3–§12.6 intentionally keep the vocabulary of the versions they describe (Purchase Order / PO / PO Demand / PO Intake). Since v6 the canonical term is **Work Order** (§12.2 item 9); those older names appear below only as history.

## 12.1 Changes from GUI Design v6

All v6→v7 changes are implemented in the Phase 2 frontend against development-only mock state; the corresponding domain rules are canonical in PROJECT_PROFILE v8.

1. **Fast connectivity detection with a strict write-confirmation rule** (§3.6): browser `online`/`offline` events plus ~1 s polling of `GET /api/health` (request timeout below the probe interval, no overlapping probes), recheck on tab focus/visibility, no "connecting" flicker from passive probes, and Scan Station input re-enable and refocus on recovery. A scan is successful only after the server confirms the write — connectivity status is an early warning, never permission for optimistic recording; a write that cannot reach the server fails as "nothing recorded". WebSocket/SSE remain out of scope for Phase 2.
2. **Missing due dates are valid data** (§3.8, §11; PROJECT_PROFILE v8 §8.2/§8.3): WorkOrder and demand-line due dates may be absent, rendered as `No due date` / `—`; "missing due date" is removed from the validation error list; ordering everywhere follows the canonical demand ordering (PROJECT_PROFILE §18 — dated earliest-first, undated after all dated demand by Work Order received date, stable tie-breaker).
3. **Work Orders becomes manual-first with optional identifiers** (§11): WO Number and WO due date are both optional; a blank WO Number generates a temporary internal `TMP-YYYYMMDD-HHMMSS` number on confirmed save; **＋ Add Part manually** is the primary workflow through one accessible multi-step Add Part dialog (PN → quantity → due date with explicit "No due date" → optional metadata, backward navigation preserving values); barcode scanning is demoted to a secondary optional method (the Scan Station remains scanner-first); Save demand shows an omission-summary confirmation before saving. The modal-over-list architecture and URL behavior of v6 are unchanged; no new route is added.
4. **Scan Station refinements** (§4): the Station ID leaves the header and becomes a faint bottom-edge diagnostic caption ("Station LATHE-ST-01"); manual PN entry becomes a visible secondary action **⌨ Enter PN manually** with an explanatory caption; recent scans carry the Movement type as an explicit field (`movementType: MovementType` in the mock model) rendered as its own badge plus a recorded/reversed status — never embedded at the start of description strings; the quantity dialog gains full physical-keyboard support (digits, Backspace, Delete/Clear, Enter, Escape).
5. **Production Board hardening** (§5): live local date-and-time header clock (self-updating component, no static mock clock); standard Hot Part presentation `🔥#1 <PN>` via one shared component (`HotPn`, `src/components/indicators.tsx`) used on Production Board, Area Board, Tracking and Priority; only the due-date urgency text blinks (PN and date stay steady; `prefers-reduced-motion` honored; footer legend updated); stable Areas & Quantities grid with vertically aligned quantities; column order moves Job Numbers last (No. · Part Number · Areas & Quantities · Time · Due Date · Total Days · Job Numbers) with content-driven `colgroup` sizing; **dynamic pagination** measured from actual board and row heights replaces the former fixed rows-per-page assumption (12 s rotation only when more than one page exists; 10-row fallback in layout-less DOM environments).
6. **Area Board per-Area detail redesigned** (§6.3): the "one card per PN" grid is replaced by an Area summary card (name, description, Operations, stats, grouped compact PN list — Assigned to Machines / Area queue / Stocked for terminal Stockroom) followed by one monitoring card per Machine (running / idle / maintenance with distinct empty and maintenance presentations, from the `MOCK_AREA_MACHINES` mock model); Areas without Machines render only the summary card; "Sort: Time in Area" works via the sortable `timeInAreaMinutes` duration field, longest first.
7. **Priority reordering requires confirmation** (§8; PROJECT_PROFILE v8 §21): drag-and-drop, Move Up, Move Down, Undo and Redo confirm before applying — showing affected PN + Work Order Demand, previous rank, proposed new rank and action type; Cancel leaves the list and both histories unchanged and the visible list is never renumbered early; adding at the bottom stays direct; the existing remove confirmation is unchanged.

## 12.2 Changes from GUI Design v5

All v5→v6 changes concern the Work Orders view (§11) and the canonical vocabulary; Phase 2 implements them against development-only local mock state.

1. **New Work Order becomes a modal dialog over the WO list** (§11.3): ＋ New Work Order no longer replaces the Work Orders content with a full-page panel. The list stays mounted behind the overlay and the URL remains `/management/work-orders`. The dialog specifies full accessibility behavior (accessible name from its visible heading, `aria-modal`, focus trap, initial focus, Escape/backdrop close requests, focus restoration to ＋ New Work Order, responsive sizing with internal scrolling) and never silently discards entered data — a dirty dialog asks "Discard this New Work Order?" before closing.
2. **OPEN Work Order detail supports adding demand lines** (§11.2): a clear ＋ Add Part action (OPEN Work Orders only) reuses the scanner-first entry — scan an existing PN barcode, search or manually enter a PN, or create a PN inline. New lines are visibly marked unsaved drafts; duplicate PNs focus the existing line instead of duplicating it.
3. **Demand-line removal follows the canonical Work Order Demand removal rule** (§11.2; PROJECT_PROFILE §13): unsaved drafts remove immediately, saved unreleased lines require explicit confirmation, and released lines cannot be removed — the action is disabled with the explanation "Cannot remove: production quantity has already been released."
4. **Editable dates use native calendar controls** (§11 intro): `<input type="date">` for the New Work Order received/due dates, the OPEN Work Order due date, and every demand-line due date. ISO `YYYY-MM-DD` internally, formatted display read-only, both themes via `color-scheme`, no date-picker dependency. The inherited-default rule is unchanged: only lines still holding the WO due date follow later changes.
5. **Validation is corrected** (§11.2/§11.3): a manual row without a PN makes the form invalid instead of being silently filtered out at save; field-level errors cover missing PN, non-positive quantity, missing due date, and duplicate PNs; after a failed save the first invalid control receives focus and entered values are preserved.
6. **Unsaved changes are guarded everywhere** (§11.2): the OPEN Work Order detail visibly tracks its dirty state, and navigation away — back action, top-level navigation, Management sub-navigation, browser back/forward, reload/tab close — requires explicit confirmation.
7. **Production mock boundary made real** (implementation alignment): mock views and datasets load only in development builds; production builds show an explicit "not connected to a production data source yet" state per route, verified by a mock-sentinel check in the build.
8. **Realistic data shapes** (§2.3): mock datasets and the mockup use representative synthetic identifiers — multi-segment hyphenated numeric PNs, manufacturing-style descriptions, numeric-looking opaque Work Order Numbers (`007482`), numeric-looking external Job Numbers (`18427`), and optional Revision values (populated and blank) — instead of demo-style `PF-…` / `PO-…` / `ERP-…` identifiers; §2.3 documents the identifier/description shapes, search, and wrapping constraints.
9. **Canonical vocabulary migration: Purchase Order → Work Order** (PROJECT_PROFILE v7 §7). The business container previously called Purchase Order is a Work Order on the actual shop floor. The view is renamed **Work Orders**, its route is `/management/work-orders` (no legacy `/management/purchase-orders` alias — the application is not deployed yet), UI labels use Work Order / WO, and code names use the full `WorkOrder` / `WorkOrderDemand` / `WorkOrderAllocation` forms. Work Order Number and external Job Number remain separate identifiers; Job Number keeps its informational display/search/sort/report role.

## 12.3 Changes from GUI Design v4

1. **Global Dark/Light theme mode** (§2.1): a user-facing toggle in the top navigation switches the entire application between Dark and Light; **every view follows the selected mode**, replacing the fixed per-view themes of v2–v4. Dark remains the default (shop-floor first). All component styling was moved to semantic tokens with per-theme values; status *text* colors have per-theme variants for contrast, while status tints and Area identity colors are shared. Theme persistence (per user / per station) is an open decision (§14). This removes "dark/light user toggle" from the deferred list (§13).
2. **Area Board card layout hardening** (§6.3): the quantity block is anchored to the card's right edge independent of PN length; an over-long PN truncates with an ellipsis and shows the full identifier in a hover tooltip. The same truncation applies to the All Areas overview PN lists. §2.3's single-line PN rule was amended accordingly.

## 12.4 Changes from GUI Design v3

1. **Manager Summary merged into Area Board.** The Area-column overview becomes the **All Areas** overview — the first tab of the Area Board tab strip and its default mode (§6.2). The "Manager Summary" name is retired; overview column headers open the per-Area detail. Management sub views reduce to Area Board · Tracking · Purchase Orders · Priority. No §21 content is dropped — only its placement changed.
2. **Area Board returns to the dark theme** (as in v2), including the All Areas overview: it is a monitoring surface rather than desk paperwork, and the light v3 variant proved hard to read (§2.1). The Management sub-view bar follows the active sub view's theme. Dark now covers Scan Station, Production Board and Area Board; Tracking, Purchase Orders, Priority and Administration stay light.
3. **PO Intake renamed Purchase Orders** and restructured as PO list → PO detail → New PO (§11): the view opens with the list of POs, demand lines appear only after selecting a PO, and a dedicated **scanner-first New PO** flow adds one demand line per PN barcode scan (Request Type defaults to `NEW`, quantity typed immediately after each scan).
4. **PO-level due date** introduced as the default for each demand line's due date, editable per line (§11.2/§11.3). Requires PurchaseOrder.`due_date` — pending PROJECT_PROFILE §8.2 and §21 alignment (§1).
5. **Section renumbering:** former §7 Manager Summary removed; later sections shift up by one (Tracking §7 … Open Questions §14).

## 12.5 Changes from GUI Design v2

1. **Navigation regrouped.** Area Board, Manager Summary, Tracking, PO Intake and Priority Management become **sub views of a single Management view** (§1.1). Top-level navigation is reduced to Scan Station · Production Board · Management · Administration. Management remembers its last-used sub view.
2. **"Shop floor" navigation group label removed.** In v2 it was a nav group heading only (never a view); with only two shop-floor views left at top level the label adds nothing.
3. **Area Board and Manager Summary move to the light Management context.** §2.1's context table now assigns dark exclusively to Scan Station and Production Board. Both views keep their layout and behavior; only the theme changes so the entire Management view is visually consistent.
4. **Realigned to PROJECT_PROFILE v6** (was v5): allocation and Hot work ordering use two business criteria only — ① priority rank ② earliest due date — with the deterministic tie-breaker demoted to an implementation detail; Operators may review and adjust the suggested PO Allocation before confirmation (no longer role-gated); PROJECT_PROFILE section references follow the v6 renumbering (Barcode Model §10, Quantity Model §11, …, Application Views §21, Remaining Open Decisions §32).

## 12.6 Changes from GUI Design v1

Decisions in v1 that were superseded in v2, all aligned to PROJECT_PROFILE v5:

1. **Machine selection:** v1 proposed a per-Part Machine scan with no persistent selection. v2 follows §19 — the scanned Machine starts a **session** that persists until changed, cleared, or expired. Scan order remains flexible.
2. **Terminology:** "Production Request" and "Request Number" are replaced by the canonical PO Demand / Request Type / Quantity Flow / temporary PO vocabulary.
3. **Priority model:** v1 used per-Part `P#` ordering. v2 ranks **Hot PO Demand** per Department; the same PN may appear with different ranks through different POs.
4. **Priority workflow:** save-or-cancel is replaced by immediate, audited application with Undo/Redo; the Hot list gains add-by-search/scan and per-entry removal.
5. **Production Board columns:** now No. · PN · Areas & Quantities · Time · Job Numbers · Due Date (with embedded Days Left) · Total Days; per-location time replaces the separate "Time in area" column, and PN never wraps.
6. **Operations administration:** v1 folded Operations into the Area editor with no admin page. v2 restores Operations as configurable objects under Organization, per §8.5 and §21.
7. **New views:** Manager Summary and Priority Management are specified; Tracking becomes PN-centric with Quantity Flow / per-flow route visualization.

---

# 13. Out of Scope for v5 UI

Deferred intentionally: localization framework (UI ships in English using PROJECT_PROFILE vocabulary), charts/analytics dashboards, mobile-phone layouts (tablet-first), administrative command barcodes.

---

# 14. Open Questions

1. Undo/Redo depth and retention for Priority Management (per session? until sign-out?).
2. Theme-mode persistence: is the Dark/Light choice stored per user, per station, or both — and which wins on a shared terminal? (§2.1; the mockup keeps it session-only.)
3. Hot-add barcode behavior when the scanned PN has multiple active Work Order Demand records — auto-open the filtered list, or require explicit selection?
4. Worker and Machine session expiration values and their visual countdown — policy values live in Administration; defaults TBD (PROJECT_PROFILE §32.3).
5. Should the Production Board offer a per-Area filtered mode, or is that fully covered by the Area Board (All Areas overview / per-Area detail)?
6. Whether Undo on the Scan Station requires Worker identity when Worker scanning is enabled for the Area.
