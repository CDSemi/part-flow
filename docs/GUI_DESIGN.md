# PartFlow GUI Design v3

> **Status:** Current — companion to [PROJECT_PROFILE.md](./PROJECT_PROFILE.md) (v6).
> This document specifies the user interface only. Business rules, terminology and workflows are defined in PROJECT_PROFILE and are not redefined here.
> An interactive mockup accompanies this document: `mockups/partflow-gui-mockup-v3.html`.
> Supersedes GUI Design v2 (mockup: `archive/partflow-gui-mockup-v2.html`); the differences are listed in §13.1. Differences from v1 remain listed in §13.2.

---

# 1. Scope

Covers the eight application views from PROJECT_PROFILE §21:

1. Scan Station (production)
2. Production Board (monitoring, large display)
3. Area Board (monitoring, per-Area)
4. Manager Summary (management overview, grouped by Area)
5. Tracking (management, PN-centric)
6. PO Intake (management, manual PO entry and production release)
7. Priority Management (Hot PO Demand ranking)
8. Administration (configuration)

## 1.1 Navigation structure

Top-level navigation exposes four entries; the management views are grouped as **sub views of one Management view**:

| Top level        | Contains                                                          |
|------------------|-------------------------------------------------------------------|
| Scan Station     | — (single view)                                                   |
| Production Board | — (single view)                                                   |
| Management       | Area Board · Manager Summary · Tracking · PO Intake · Priority    |
| Administration   | — (single view, own sidebar navigation per §10)                   |

Selecting Management opens its **last-used sub view** (Area Board on first open) and reveals a secondary sub-view bar beneath the top navigation. The grouping is navigation only — each sub view keeps its own specification (§6–§9, §12). The eight views of PROJECT_PROFILE §21 are unchanged.

---

# 2. Design System

## 2.1 Two visual contexts, one token set

| Context    | Views                                                                                             | Theme | Rationale                                                                           |
|------------|---------------------------------------------------------------------------------------------------|-------|-------------------------------------------------------------------------------------|
| Shop floor | Scan Station, Production Board                                                                    | Dark  | Reduces glare in the shop, readable from distance, tolerant of low-quality displays |
| Management | Management sub views (Area Board, Manager Summary, Tracking, PO Intake, Priority), Administration | Light | Dense data work at a desk, matches office tooling expectations; one consistent theme across the whole Management view |

Both contexts share the same color tokens, spacing scale, and typography so the product feels like one system.

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
- Identifiers (PN, PO, temporary PO, external Job Number, Quantity Flow id, barcode values, quantities, timestamps): monospace. Identifiers must be visually distinct from prose because operators read them against paper travelers and folder labels.
- Shop-floor minimum sizes: body 16 px, PN ≥ 19 px, quantities ≥ 18 px bold. Production Board is sized for reading at 3–5 m (PN 22 px+, key figures 18 px+).
- The PN is always rendered on a single line; columns and cards size themselves to fit it rather than wrapping the identifier.

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
6. **Connectivity loss is an explicit write-blocked state.** Losing the connection shows a persistent OFFLINE / DISCONNECTED banner with an actionable message; production write submission is disabled while disconnected. Already loaded read-only information stays visible where practical. No production write is queued locally, no pending Movement indicator is shown, and the UI never claims that scans will synchronize later. On reconnection, input readiness and focus are restored. (Offline scan synchronization remains deferred and unapproved — PROJECT_PROFILE §30, §32.4.)
7. **PartFlow vocabulary everywhere.** UI labels use the canonical names: PN, PO, PO Demand, Request Type (`NEW` / `REWORK` / `MODIFY`), Quantity Flow, Route, Movement types (`RECEIVED`, `TRANSFERRED`, `ASSIGNED_TO_MACHINE`, `SPLIT`, `MERGED`, `STOCKED`, `REVERSED`, …), PO Allocation, Hot (PROJECT_PROFILE §7).

---

# 4. Scan Station

Fixed to one Area per station. Single screen, no navigation during normal production.

## 4.1 Layout

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ Dept / AREA (color) / Operations · Station  [Machine session][Worker][●Online]│
│ (disconnected banner when connectivity is lost — writes blocked)              │
├──────────────────────────────────────┬────────────────────────────────────────┤
│ Machine status strip (scan to select)│  UNDO LAST SCAN                        │
│ Scan input (large, focused)          │  Recent scans (today)                  │
│ Pending scan context banner          │  In this Area now:                     │
│ Last scanned PN                      │    – assigned to Machines              │
│ Feedback zone                        │    – Area queue (awaiting Machine)     │
└──────────────────────────────────────┴────────────────────────────────────────┘
```

## 4.2 Header

Department, Area name with Area color, supported Operations, Station ID. Session pills for the active **Machine session** and **Worker session** (both show `—` when not applicable per Area configuration), each with a caption describing its lifetime. Connectivity indicator with explicit ONLINE/OFFLINE text — color alone is not sufficient.

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

Manual PN entry remains available as an explicit fallback link under the scan input. It opens a **separate manual-entry flow** that accepts the exact PartNumber text and resolves it through the same server-side validation as a scan. It never pre-fills or reuses the barcode input, and raw PN text is never silently interpreted as a barcode (PROJECT_PROFILE §10 Barcode Model).

## 4.5 Pending scan context and last scanned PN

Two persistent context surfaces required by §21:

- **Pending scan context** — an amber pulsing banner whenever the station holds an incomplete intent (e.g. quantity received into the queue awaiting its Machine scan, or an armed Request Type). It names the PN, quantity and the scan that completes the action.
- **Last scanned PN** — a fixed strip showing the most recent PN with a one-line description of what happened.

## 4.6 Ambiguity dialog

Full-screen modal listing every valid context as a large tappable row, filtered to the current station where possible. Typical contexts: assign queued quantity to a Machine, receive additional quantity from an upstream Area, create `REWORK` demand, create `MODIFY` demand. Each row shows the relevant quantities, POs and due dates; REWORK/MODIFY rows explain the temporary-PO fallback (`TMP-YYYYMMDD-HHMM-REWORK`) used when no active PO applies (PROJECT_PROFILE §14). Choosing REWORK or MODIFY does **not** record anything immediately — it opens a prefilled intake/confirmation flow (Request Type, quantity, link to an applicable PO or temporary internal PO, Route and starting Area); `RECEIVED` is appended only after that confirmation. Esc / Cancel abandons with nothing recorded.

## 4.7 Quantity entry

Modal with oversized numeric keypad (62 px keys) for touch; physical keyboard digits also accepted. Shows PN, source → target context, and available source quantity. Confirm is blocked for 0; quantities above the available source are rejected with an integrity error (server-validated in the real application).

## 4.8 Right column

- **Undo Last Scan** — one prominent button; only undoes recent eligible scans made at this Area. Creates a `REVERSED` compensating Movement referencing the original (PROJECT_PROFILE §16); disabled when nothing is undoable.
- **Recent scans** — today's scans, newest first: PN, Movement type and description, time, status dot (green recorded / red reversed).
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

- Columns (PROJECT_PROFILE §21): **No. · Part Number · Areas & Quantities · Time · Job Numbers · Due Date · Total Days**.
- **Part Number** renders on a single line — the column is sized so the PN, Hot flame and rank chip never wrap. Name and revision appear as a secondary line.
- **Areas & Quantities · Time:** one row per location showing Area color dot, Area/Machine name, quantity, a `machine` / `queue` tag where relevant, and the **time in that location** right-aligned in monospace. A time turns amber when it exceeds the expected duration of the active Route Step. A dashed separator shows the PN total underneath.
- **Due Date** carries a highlighted secondary line with days left (amber = due soon, red = overdue, neutral = comfortable / stocked). There is no separate Days Left column.
- **Priority model:** Hot PO Demand rank (🔥 + `#n` chip) from Priority Management. Hot rows sort first in rank order with a row tint that gets redder the hotter the rank; non-Hot rows follow sorted by due date.
- **Due-soon blink:** a PN approaching or past its due date blinks (threshold configurable). Blink is reserved for due-date urgency only — nothing else on the board may blink.
- Long lists rotate pages automatically (page indicator + interval in footer). Footer carries the legend and aggregate stats (active PNs, pcs in production, pcs stocked).
- Auto-refresh; a "Live" indicator plus last-updated time when the feed is stale.

---

# 6. Area Board

Focused per-Area view, interactive. Management sub view (light theme, §1.1); typically used at a desk or on a tablet.

- Area tab strip with color dots and item counts; one active Area at a time.
- Toolbar: text search (PN / PO / Job Number) and sort selector (Due date, Priority, Time in Area, Quantity); a summary of PN count and total pieces for the Area.
- Card grid, one card per PN-in-Area:
  - PN (🔥 + `#n` chip for Hot demand), PO + Operation (+ Request Type when not `NEW`), and Job Number (`— (internal)` for temporary internal POs).
  - Quantity presented as a **right-aligned block** with an explicit label ("IN THIS AREA" / big number / "pcs"), visually separated by a divider so the number cannot be misread.
  - Per-Machine chips (dashed style for queued quantity), due text with the same color ramp as the Production Board, and time in Area.
- Empty state: "No production in {Area}".

---

# 7. Manager Summary

Operational overview grouped by Area (PROJECT_PROFILE §21). Management sub view (light theme, §1.1). One column per Area; the layout scrolls horizontally when all Areas do not fit.

Each Area column shows:

- header with Area color, name, description and supported Operation chips;
- a three-value stat row: **total pcs · queued · on machines**;
- the PN list for the Area: PN (🔥 + rank when Hot), quantity, PO + Job Number, due date (color-ramped), Machine distribution or time in Area;
- an explicit empty state for Areas without production.

A toolbar provides search (PN / PO / Job Number) and PN sorting (priority, due date, quantity).

---

# 8. Tracking

Primary management interface, **PN-centric** per PROJECT_PROFILE §21. Master–detail layout: filterable PN list (left) + PN detail panel (right).

## 8.1 Filters and list

Search across PN, PO and Job Number; selects for Area, Operation, Machine, Request Type, priority (Hot only), status, and due window. List columns: PN (+ name, Hot chip), active PO Demand (PO · qty · Request Type chip), current distribution (Area color dots), active quantity, stocked quantity, next due date, status pill (Active / Stocked / Completed).

## 8.2 Detail panel sections

1. **PN master** — image placeholder, PN, name, current revision (informational), barcode value (`PF:PN:…`), ERP id.
2. **Active PO Demand** — table of PO · Request Type · requested · allocated · remaining shortage · due · priority, with an allocation progress bar. Labeled "business demand — separate from Movement".
3. **Current quantity by Area** — horizontal bars per Area/Machine in Area colors, queue rows visually distinct; labeled "derived from Movement history".
4. **Quantity Flows & assigned Routes** — one block per Quantity Flow: flow id, quantity, current position, split/merge lineage, and its own route chips: done (green) → current (blue) → queued (amber) → future (neutral); deviations marked (orange, ⚠) with who/when/reason caption and a note that the previous route is preserved in audit. The section explicitly avoids implying the whole PN is at one Route Step.
5. **Movement history (immutable)** — reverse-chronological: timestamp, Movement type (color-coded, canonical names), full description (areas, quantity, Quantity Flow, machines, worker, station). Read-only; no edit affordances exist.
6. **Stocked & Allocation history** — stocked quantity and PO Allocation entries with the §18 ordering note; empty state when nothing is stocked.
7. **Corrections** — authorized-only actions as explicit buttons: Quantity adjustment, Edit assigned Route, Adjust PO Allocation, Change priority, View audit trail. Every correction flow requires a reason and produces new history.

## 8.3 States

Loading skeletons per section; empty filter result ("No PNs match — clear filters"); permission-restricted users see the Corrections section hidden entirely rather than disabled.

---

# 9. Priority Management

Manages the Department's Hot PO Demand list (PROJECT_PROFILE §21 Priority Management). Priority belongs to PO Demand; multiple POs for the same PN may hold different ranks.

- The list shows each Hot entry with rank badge, PN, PO + Job Number, Request Type chip, demand figures (requested / allocated / shortage), current distribution, and color-ramped due date.
- **Add:** the "+ Add to Hot list" button opens a dialog with a single search field that accepts free text (PN / PO / Job Number) *and* PN barcode scans — scanning with the dialog open adds the matching PO Demand directly. If a PN has multiple active PO Demand records, each is listed and added separately. New entries always join at the **bottom** of the list.
- **Remove:** each entry has a remove (✕) affordance. Clicking it opens a **confirmation dialog identifying the PN and PO Demand**; Cancel changes nothing. On Confirm the entry is removed, remaining ranks close the gap, the change applies immediately and is audited, and Undo can restore it. Confirmation guards the removal; it does not reintroduce a separate save-or-cancel workflow.
- **Reorder:** drag-and-drop with a visible grip; rank badges renumber live.
- **Undo / Redo instead of save-or-cancel:** every change (reorder, add, remove) applies immediately, is audited, and can be stepped back and forward with Undo/Redo buttons. The buttons disable when the corresponding history is empty.
- A footer note restates the rules: Hot is a label on top of `priority_rank`, and ordering criteria are ① Hot rank ② earliest due date; a stable deterministic tie-breaker is an implementation detail, not a business rule (PROJECT_PROFILE §18).

---

# 10. Administration

Isolated from production. Sidebar navigation grouped as:

- **Organization:** Departments, Areas, Operations, Machines, Workers
- **Production setup:** Route Templates, Barcode configuration, Scan behavior
- **Access:** Users, Roles & permissions
- **Policies:** Worker sessions, Machine assignment, Correction permissions, Settings

Operations are managed per PROJECT_PROFILE §8.5 — each Operation belongs to an Area, and the Areas table lists the Operations an Area supports.

Each section is a standard table + editor pattern. The Areas table is the reference example: Area (color + name), Operations, Machine assignment mode (None / Auto-assign single Machine / Queue → select by scan), Machines, Worker ID mode (Disabled / Fixed Worker / Scanned session), Terminal flag, Active status.

Editing an Area's display properties shows an inline note that identity and barcode are stable and history is unaffected. Destructive operations (deactivating an Area with active quantities) are blocked with an explanation, not confirmed through.

---

# 11. Completion / Receiving UI (Stockroom Scan Station)

The Stockroom station reuses the Scan Station shell with one additional step: after the `STOCKED` Movement, an **allocation dialog** shows the suggested split across outstanding PO Demand in the exact §18 order — ① highest Hot rank ② earliest due date. Each row shows the PO, requested quantity, previously allocated quantity, remaining shortage and the proposed quantity, adjustable with +/− steppers — Operators may review and adjust the suggestion before confirmation (PROJECT_PROFILE §18). Confirm is enabled only when the allocated total equals the stocked quantity. Routine receiving requires no Manager involvement; Admin and Manager may adjust the allocation later, with every change audited.

---

# 12. PO Intake

Management view (light theme) implementing manual PO entry and explicit production release (PROJECT_PROFILE §13, §21 PO Intake). It handles business demand only — it is not ERP-style customer, pricing, invoicing, shipping, purchasing, or accounting functionality.

## 12.1 Layout and PO handling

- **PO search / create:** one search field over PO Number; an exact miss offers "Create PO". Creating captures PO Number and received date. Attempting to create a PO Number that already exists surfaces the existing PO instead of duplicating it (duplicate PO handling).
- **PO header:** PO Number, received date, status, and demand-line count.
- **PoDemand rows:** an editable table, one row per PN demand: PN (lookup or create), Request Type (`NEW` default / `REWORK` / `MODIFY`), requested quantity, due date, priority when applicable, external Job Numbers, requester / reason / notes. Long PO line lists scroll with a sticky header and a line count.
- **PN lookup / create:** the PN field searches existing PartNumbers; a new PN can be created inline, which shows a **barcode preview** (`PF:PN:…`) and creates the unique PN barcode with the PN master. An **inactive PN** is flagged and cannot be released without reactivation.
- **Validation states:** per-field errors (missing PN, quantity ≤ 0, missing due date where required); a row with errors cannot be saved. **Unsaved changes** are visibly marked and guarded against navigation loss.

## 12.2 Demand save vs. production release

- **Save demand** persists the PurchaseOrder and PoDemand rows only. Saving never creates production quantity — the UI states this explicitly ("business demand — separate from production").
- Each saved demand row carries an explicit **Release to production…** action. Releasing opens a confirmation flow that:
  1. confirms the release quantity;
  2. confirms or assigns the Route (snapshot noted);
  3. confirms the configured starting Area and Operation;
  4. **warns when the PN already has active quantity**, showing the existing distribution and requiring explicit confirmation of intent — never auto-creating or auto-merging quantity;
  5. shows a **release summary before commit** (PN, quantity, Route, starting Area/Operation, PO Demand);
  6. on Confirm, reports the result: created Quantity Flow id, assigned Route, starting Area, quantity, and the appended `RECEIVED` Movement.
- Saving demand never triggers a release automatically; release is always a separate explicit action.

---

# 13. Changes from previous versions

## 13.1 Changes from GUI Design v2

1. **Navigation regrouped.** Area Board, Manager Summary, Tracking, PO Intake and Priority Management become **sub views of a single Management view** (§1.1). Top-level navigation is reduced to Scan Station · Production Board · Management · Administration. Management remembers its last-used sub view.
2. **"Shop floor" navigation group label removed.** In v2 it was a nav group heading only (never a view); with only two shop-floor views left at top level the label adds nothing.
3. **Area Board and Manager Summary move to the light Management context.** §2.1's context table now assigns dark exclusively to Scan Station and Production Board. Both views keep their layout and behavior; only the theme changes so the entire Management view is visually consistent.
4. **Realigned to PROJECT_PROFILE v6** (was v5): allocation and Hot work ordering use two business criteria only — ① priority rank ② earliest due date — with the deterministic tie-breaker demoted to an implementation detail; Operators may review and adjust the suggested PO Allocation before confirmation (no longer role-gated); PROJECT_PROFILE section references follow the v6 renumbering (Barcode Model §10, Quantity Model §11, …, Application Views §21, Remaining Open Decisions §32).

## 13.2 Changes from GUI Design v1

Decisions in v1 that were superseded in v2, all aligned to PROJECT_PROFILE v5:

1. **Machine selection:** v1 proposed a per-Part Machine scan with no persistent selection. v2 follows §19 — the scanned Machine starts a **session** that persists until changed, cleared, or expired. Scan order remains flexible.
2. **Terminology:** "Production Request" and "Request Number" are replaced by the canonical PO Demand / Request Type / Quantity Flow / temporary PO vocabulary.
3. **Priority model:** v1 used per-Part `P#` ordering. v2 ranks **Hot PO Demand** per Department; the same PN may appear with different ranks through different POs.
4. **Priority workflow:** save-or-cancel is replaced by immediate, audited application with Undo/Redo; the Hot list gains add-by-search/scan and per-entry removal.
5. **Production Board columns:** now No. · PN · Areas & Quantities · Time · Job Numbers · Due Date (with embedded Days Left) · Total Days; per-location time replaces the separate "Time in area" column, and PN never wraps.
6. **Operations administration:** v1 folded Operations into the Area editor with no admin page. v2 restores Operations as configurable objects under Organization, per §8.5 and §21.
7. **New views:** Manager Summary and Priority Management are specified; Tracking becomes PN-centric with Quantity Flow / per-flow route visualization.

---

# 14. Out of Scope for v3 UI

Deferred intentionally: dark/light user toggle (theme is fixed per view class), localization framework (UI ships in English using PROJECT_PROFILE vocabulary), charts/analytics dashboards, mobile-phone layouts (tablet-first), administrative command barcodes.

---

# 15. Open Questions

1. Undo/Redo depth and retention for Priority Management (per session? until sign-out?).
2. Hot-add barcode behavior when the scanned PN has multiple active PO Demand records — auto-open the filtered list, or require explicit selection?
3. Worker and Machine session expiration values and their visual countdown — policy values live in Administration; defaults TBD (PROJECT_PROFILE §32.3).
4. Should the Production Board offer a per-Area filtered mode, or is that fully covered by Area Board / Manager Summary?
5. Whether Undo on the Scan Station requires Worker identity when Worker scanning is enabled for the Area.
