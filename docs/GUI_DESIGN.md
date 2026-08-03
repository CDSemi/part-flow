# PartFlow GUI Design v11

> **Status:** Current — companion to [PROJECT_PROFILE.md](./PROJECT_PROFILE.md) (v10).
> This document specifies the user interface only. Business rules, terminology and workflows are defined in PROJECT_PROFILE and are not redefined here.
> An interactive mockup accompanies this document: `mockups/partflow-gui-mockup-v11.html`.
> Supersedes GUI Design v10 (mockup: `archive/partflow-gui-mockup-v10.html`); the differences are listed in §12.1. Differences from v9, v8, v7, v6, v5, v4, v3, v2 and v1 remain listed in §12.2–§12.10.

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
3. **Feedback is tri-state and instant.** Every scan produces exactly one of: Success (green), Warning (amber — recorded but needs awareness, or an action is pending), Error (red — nothing recorded). Feedback shows *what happened* and *why* in one line each. On the Scan Station this feedback renders as a **floating notification** (§4.4) that reserves no layout space: only the most recent notice shows, it carries an explicit close button, success/neutral notices auto-dismiss after ~4 s and warnings/errors after ~8 s (a replacing notice restarts the timer). The persistent OFFLINE / DISCONNECTED banner (rule 6) is **not** a notification — it stays until connectivity is restored.
4. **Quantity integrity is visible.** Quantity entry displays the available source quantity. Attempts to move more than available are rejected with an explicit error — the UI explains the limit rather than clamping silently (PROJECT_PROFILE §11 Quantity Model).
5. **History is append-only in the UI too.** Undo appears as a new `REVERSED` entry in the scan list; the original entry stays visible. Tracking's Movement history has no edit/delete affordances (PROJECT_PROFILE §16).
6. **Connectivity loss is an explicit write-blocked state, detected fast.** Detection combines browser `online`/`offline` events with polling (no WebSocket/SSE in Phase 2): `offline` marks the application unavailable immediately; `online` triggers an immediate health check; `GET /api/health` is polled about every second with a request timeout below the probe interval and no overlapping probes; connectivity is rechecked on tab focus/visibility change. Passive probes never flip the UI to a "connecting" state — no flicker. Losing the connection shows a persistent OFFLINE / DISCONNECTED banner with an actionable message; production write submission is disabled while disconnected. Already loaded read-only information stays visible where practical. No production write is queued locally, no pending Movement indicator is shown, and the UI never claims that scans will synchronize later. On reconnection, input readiness and focus are restored — the Scan Station input is re-enabled and refocused. **A scan is successful only after the server confirms the write:** connectivity status is an early warning, never permission to record a Movement optimistically. If connectivity disappears between the last heartbeat and a write request, the request fails as "nothing recorded"; the UI must never display a false recorded result (PROJECT_PROFILE §15). This rule must be preserved unchanged in the production phases. (Offline scan synchronization remains deferred and unapproved — PROJECT_PROFILE §30, §32.4.)
7. **PartFlow vocabulary everywhere.** UI labels use the canonical names: PN, WO / Work Order, Work Order Demand, Request Type (`NEW` / `MODIFY`), Route Mode (`FLOATING` / `PLANNED`), Quantity Flow, Route, Movement types (`RECEIVED`, `TRANSFERRED`, `ASSIGNED_TO_MACHINE`, `RELEASED_FROM_MACHINE`, `AREA_COMPLETED`, `SPLIT`, `MERGED`, `STOCKED`, `QUANTITY_ADJUSTED`, `SCRAPPED`, `REVERSED`, …), the user-facing DONE status with its derived `READY_TO_TRANSFER` holding state (PROJECT_PROFILE §7 Area Completion), the Repair movement intent (`movement_reason = REPAIR` — never a Request Type), Work Order Allocation, Hot (PROJECT_PROFILE §7, §8.11). A blank external Work Order Number renders as `—` everywhere (display-only — never persisted).
8. **One-shot actions, no armed state.** There is no persistent Machine Session, no armed Action barcode, and no pending PN intent that survives a dialog: every Scan Station production action is one dialog — open, complete or cancel, context cleared, input refocused. Cancel always means no write.
9. **Missing due dates are valid data.** Work Order and demand-line due dates may be absent (PROJECT_PROFILE §8.2, §8.3). A missing due date renders as `No due date` where a note line exists and as `—` as the compact/table date value — consistently, and never as an error or warning state. Wherever demand is due-date ordered, the canonical demand ordering applies (PROJECT_PROFILE §18): dated demand first, earliest first; undated demand after all dated demand, ordered by the parent Work Order's received date, with a stable deterministic tie-breaker.

10. **Professional operator copy — exhaustive.** Every rendered string is classified as exactly one of: **operator/user-facing** (explains what to do, what is required, what happens after confirmation, why an action is unavailable or rejected, and how to recover — never implementation detail), **audit/history-facing** (canonical recorded data: Movement types, workers, timestamps, Areas, Machines, quantities, reversal relationships — raw enum names are acceptable only here, i.e. the Movement history, type badges, and the confirmation summary's `Recorded event(s)` row), or **development-only** (demo barcodes, sample-data boundaries — concise, clearly isolated behind the shared `DevNotice`/dev-marked presentation, excluded from production builds, never dominating normal workflows, and never repeated per toast/dialog/footer: at most one concise development notice per view). Normal copy never exposes wording such as `(mock)`, `nothing persisted`, `local mock data`, `priority_rank`, `movement_reason`, `derived from Movement history`, or architecture explanations; legitimate operational distinctions are expressed in user language instead (e.g. `Requested quantity`, `Current recorded location`, `Complete activity history`). Cancel buttons throughout the workflows are exactly `Cancel (Esc)`. An automated rendered-copy guard (`src/rendered-copy.test.ts`) scans the renderable frontend sources (comments stripped) and the current mockup for banned developer-facing phrases, with an explicit allowlist mechanism for approved audit surfaces — canonical Movement names themselves are never banned.

---

# 4. Scan Station

One screen per station, no navigation during normal production. PN-centric, one-shot actions only.

## 4.1 Station routing and selection

Three routes:

- `/scan-station` — the **Station Selector**. It never auto-redirects to a station. Each active Scan Station renders as one card with enough information to distinguish it: Station ID, Department, Area (with color), the supported Operations as **individual light informational chips** (the same shared chip presentation as the station header — never comma-joined text), and whether the Area has Machines. Each card offers **two clearly differentiated actions** with accessible names carrying the Station ID: **`Open`** (→ `/scan-station/:stationId`) and **`Open production mode`** (→ `/scan-station/:stationId/production`); the whole card is never one ambiguous click target.
- `/scan-station/:stationId` — one station in **standard mode** (e.g. `/scan-station/LATHE-ST-01`), keeping the normal top application navigation (Manager/Admin use). An unknown or inactive Station ID shows a clear error and never silently falls back to another station.
- `/scan-station/:stationId/production` — the same station in **production mode**: the top application navigation (and with it the Production Board / Management / Administration links) is hidden so operators cannot casually leave the configured station. The mode is explicit in the router model (`mode: 'standard' | 'production'`) and in the pathname — never a query parameter, hidden click, or secret gesture. It is a presentation choice only, **never an authorization or security boundary** (the browser itself stays outside PartFlow's control). Everything else is preserved: the persistent OFFLINE banner, the Worker Session, all scan/DONE/QUEUE/Undo/dialog/notification behavior, and full-viewport height with no leftover top-nav offset. Connectivity status stays visible through the connectivity chip inside the station header, and a small non-interactive **`Production mode`** label renders beside the footer Station ID.

The footer keeps the faint Station ID caption. In standard mode it is **subtly clickable** and opens the Station Selector for switching stations — an intentionally unobtrusive affordance, not a promoted operator workflow — and documents the convenience shortcut **Ctrl+Shift+K**, which toggles between the same station's standard and production routes (usable from the main barcode input — a scanner target the wedge capture leaves alone under modifier chords — but inert in every other text field, select, or active dialog; the dedicated route and the explicit selector actions stay the primary entry). In production mode the Station ID stays visible as plain text but is **not** a button — no casual route away from the configured station.

## 4.2 Layout

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ Dept / AREA (color) / Operations   [Area statistics][Worker session][●Online] │
│ (disconnected banner when connectivity is lost — writes blocked)              │
├──────────────────────────────────────────────────────────────────────────────┤
│ Scan barcode card (full width)                                               │
│   Scan input (large, focused) …………………………… [⌨ Enter PN manually]              │
│   Last scanned PN ……………………………………………………… [⟲ UNDO]                            │
├────────────────────┬─────────────────────────────────────────────────────────┤
│ In this Area now   │ Machine cards grid                                      │
│ (fixed left col,   │ [Machine 1][Machine 2][Machine 3]                       │
│  grows vertically) │ [Machine 4][Machine 5]                                  │
├────────────────────┴─────────────────────────────────────────────────────────┤
│ Station LATHE-ST-01                        (faint, clickable station switch) │
└──────────────────────────────────────────────────────────────────────────────┘
                                        [floating scan notification — no
                                         reserved layout space, bottom edge]
```

Cards use the same subtle semantic shadow token as the Management cards (`box-shadow: var(--shadow)`) — no separate strong shadow or decorative glow system.

## 4.3 Header

The header is an explicit grid — no spacer-based wrapping. The left identity group (Department, Area name with Area color, supported Operations) and the **Worker session** pill always share the main row; the **Area statistics** sit between them while horizontal space allows and drop to a full-width second row whose cells divide the width evenly when it does not (container query). The Worker session never wraps onto a row of its own. There is **no Machine session pill, no Machine status strip, and no persistent active-Machine state**.

**Operations chips.** Each supported Operation renders as an individual light informational chip (subtle panel background, subtle border, rounded corners, readable in both themes, no action color, no button-like hover). Chips wrap cleanly for multi-Operation Areas; the Area name stays visually more prominent; Operation names are never parsed or modified. The chips are labels, not controls.

**Area totals with semantic tones.** The header is the **single summary surface** for Area totals (the `In this Area now` card carries no statistics block, §4.10):

- Area with Machines: Total PNs · Total pcs · Queued · On machines · Done · Hot, reconciling as `Total pcs = Queued + On machines + Done`.
- Area without Machines: Total PNs · Total pcs · Processing · Done · Hot, reconciling as `Total pcs = Processing + Done`.

Number tones follow the shared statistic tone mapping used by the Area presentation: plain totals default text, Queued warning, On machines / Processing information, Done success, Hot error. The reconciling quantity values always render as numbers; Hot renders `—` when zero.

## 4.4 Scan barcode card

The Scan Barcode card sits immediately under the header and spans the full available width. The main input supports **PN barcodes**, **Worker barcodes**, and **Machine barcodes only as a one-shot shortcut** (§4.6). Action barcodes do not exist; `PF:SCRAP` in the main input is rejected — it counts only inside the Scrap workflow (§4.9). Unknown barcodes and raw PN text are rejected with nothing recorded.

There is **no dedicated ENTER button**: the scanner submits with Enter, and the input's placeholder says so — `Scan PN / Worker / Machine barcode… (ENTER)`. The barcode input remains the primary and largest control.

**Keyboard-wedge capture — the main input never loses a scan.** When no dialog is open, a scanned barcode reaches the main input even while the input is not focused: the first scanned character is captured (never lost) and focus then follows the scan; a barcode terminated by Enter is submitted exactly once. The capture never intercepts typing when focus is inside another text input, a textarea, a select, a contenteditable element, or an active dialog workflow; it never interferes with Ctrl/Alt/Meta shortcuts, normal button activation, or accessibility navigation; and it never merely forces focus after every click. After processing, cancellation, dialog close, Worker change, reconnect, or a completed operation, focus is restored to the main barcode input as appropriate. Unknown or ambiguous input never updates tracking data.

Manual PN entry stays a visible secondary action (**⌨ Enter PN manually**) placed **in the scan row itself**, at the input's right edge — visibly secondary to scanning but large enough for shop-floor use. It accepts **any non-empty PN value** (PROJECT_PROFILE §8.1 — created on first valid use, case-insensitive identity, entered casing preserved) and resolves it exactly like a scan. The fallback explanation remains only as a small hint line below the scan row — no separate button row.

**Floating scan notification.** Scan feedback does not reserve layout space in the card. It renders as one floating notification near the viewport's bottom edge: only the most recent notice shows; an explicit `×` close button dismisses it; success/neutral notices auto-dismiss after ~4 s, warnings and errors after ~8 s; a replacing notice restarts the timer, and timers are cleaned up on replacement and unmount. Notices use the semantic success/warning/error styling and alert/status live-region semantics, stay inside the viewport on narrow screens, and never cover the main barcode input or dialog actions. The persistent OFFLINE / DISCONNECTED application banner is not a notification and remains until connectivity is restored. `Last scanned PN` stays fixed inside the Scan Barcode card — only the feedback surface floats.

## 4.5 Last scanned PN and Undo

The Last Scanned PN surface lives inside the Scan Barcode card, with the **Undo action anchored to its right edge**. There is no separate Undo card and **no Recent Scans list**.

- Worker scans never replace the Last Scanned PN; cancelled dialogs never replace it. Only completed PN-related actions become the Last Scanned PN.
- Undo targets the most recent eligible completed PN operation and always shows a summary confirmation first: PN, original action, quantity, source and destination, Machine if applicable, Worker, timestamp, and the effect of the reversal — presented with the same structured confirmation-summary layout as the production wizards (§4.6). Cancel performs no write.
- Undo creates compensating/reversal history — the original action is never deleted. After a confirmed Undo, the Last Scanned PN advances to the next eligible previous PN operation; with nothing eligible, Undo disables.
- Future production Undo operates on the complete application command (PROJECT_PROFILE §16), not on one arbitrary Movement row.

## 4.6 One-shot workflows — temporary wizards

There is no persistent Machine Session and no persistent PN intent after a dialog completes or is cancelled. Every production action is **one temporary wizard inside one dialog lifecycle**: open → select or enter information → review a dedicated confirmation view → confirm or cancel → all temporary state cleared → focus restored to the main barcode input. The steps are views rendered inside the same modal — never nested dialogs, and the overlay never closes and reopens between steps.

Wizard rules (all workflows):

- **No production action is recorded before the final confirmation step.** Selection and entry steps never write.
- The final view is a **structured confirmation summary** (a two-column term/value layout showing only the applicable fields: Action, PN, Quantity, Request Type, Route Mode, Planned Route, Work Order behavior, Source, Destination, Machine, Area/Operation, Due date, Reason/notes, Worker, Scan Station, resulting quantity state/effect) — never one long interpolated sentence and never a note inside a dashed box.
- The confirmation view has **Back**, **Cancel (Esc)** and a **final Confirm whose label names the operation** (`Confirm intake`, `Confirm assignment`, `Confirm transfer`, `Confirm addition`, `Confirm repair`, `Confirm scrap`, `Confirm queue return`). Enter on the confirmation view activates the final Confirm while the operation remains valid.
- **Back preserves all previously entered values** and appears only when a meaningful previous view exists — never as a second Cancel.
- **Escape always cancels the entire workflow** from any step and records nothing; cancelled workflows never update the Last Scanned PN.
- Selection views (source selection, “Choose the action for this PN”) stay selection views — they are not additional confirmation steps.
- **Focus follows the steps.** The first useful control of each step receives focus (the quantity input is focused with its value selected on quantity steps; the barcode-selection input on assignment Step 1); Back restores focus to the relevant prior control; closing or completing the wizard restores focus to the main barcode input. Wizard state is local dialog state — no Context, and no hidden Machine or PN state survives the wizard.

**Machine-first.** Scanning a Machine barcode acts only as a shortcut: it opens the one-shot Machine assignment wizard (three views in one modal) with the Machine preselected. Step 1 selects the Machine and a queued PN — by explicit buttons/cards when the Area has 6 or fewer Machines (or 6 or fewer queued PNs), by a compact accessible dropdown above that count (long PNs truncate without moving the queued quantity), and by a dedicated barcode input (`Scan Machine or queued PN barcode… (ENTER)`) that accepts a Machine barcode of the station's Area or a queued PN barcode; every invalid scan (unknown, other-Area or inactive Machine; PN without queued quantity; Worker/scrap/unsupported barcodes) shows an inline error and changes no selection. Scanning the second value completes the pair but never advances or records automatically. Step 2 enters the quantity (MAX = queued quantity, the default). Step 3 is the confirmation summary (`ASSIGNED_TO_MACHINE`, PN, quantity, Area queue → Machine, remaining queued after assignment, Worker, Station) → `Confirm assignment` → context cleared. Inactive (maintenance) Machines and Machines of other Areas are rejected with nothing recorded. No Machine context stays armed after completion or cancellation.

**PN-first.** Scanning a PN opens the applicable one-shot wizard (§4.7); entering Machine assignment from the PN action dialog preselects the PN, and Step 1's Back returns to that action dialog. Worker scans switch the Worker Session (visible in the header); Worker identity never determines business correctness.

**Monitoring row actions.** The `In this Area now` card shows **no row actions**; assignment stays available through PN scan, Machine scan and the action dialog. Machine-card PN rows carry **two distinct actions**, compact, icon-above-label (the icon is never the only source of meaning), each with a clear accessible name, and never merged:

- `DONE` (accessible name `Complete Area processing`, success tone) — complete processing and move the quantity to the finished state: the manual DONE wizard (quantity with MAX defaulting to the quantity at the current source position → dedicated confirmation view with Action `Complete Area processing`, PN, Quantity, Area, Machine when applicable, Result `Finished — ready to move`, Worker, Scan Station and the `AREA_COMPLETED` recorded event → `Confirm DONE`). On confirmation the quantity leaves the Machine card, appears under `Finished — ready to move` in the Area summary, the current Machine is cleared and the current Area stays the physical location. Other quantity of the same PN is untouched.
- `QUEUE` (accessible name `Return to Area queue`, neutral tone) — return unfinished or paused quantity to the Area queue (`RELEASED_FROM_MACHINE`, quantity MAX-defaulted, then a dedicated confirmation view). QUEUE never marks quantity DONE.

A direct-processing Area (no Machines) completes quantity through the PN action dialog's `Complete processing — DONE` choice — the same wizard without a Machine field. The Area Board remains completely read-only.

## 4.7 PN scan resolution

1. **PN has no active Work Order Demand** (including a PN seen for the first time): the **three-step intake wizard** opens — equivalent to the Work Orders “Add Part” workflow — with editable defaults `Request Type = MODIFY` and `Route Mode = FLOATING`. **Step 1 — Intake settings:** PN shown prominently; review/edit Request Type, Route Mode (Planned Route only when `PLANNED`), optional due date (owned by the WorkOrderDemand), starting Area/Operation, reason/notes, and the internal Work Order reuse behavior; no quantity on this step; `received_date` defaults to the scan timestamp. **Step 2 — Quantity:** the PN, a concise recap of Step 1 (e.g. `MODIFY · FLOATING`, Area — Operation, `Due: —`, `Internal WO —`, notes), the guidance `Enter the physical quantity received. No default quantity is assumed.` directly above the input, then keypad. **Step 3 — Confirmation:** the structured summary (action, PN, quantity, Request Type, Route Mode, Planned Route when applicable, Work Order behavior, due date, starting Area/Operation, destination state, reason/notes, Worker, Scan Station) with `Confirm intake` as the only write point. The Work Order behavior names the internal blank-number MODIFY Work Order that will be created or reused (`—`; an explicit selection dialog appears when several are plausible — never a guess).
2. **PN is not currently in the Area**: the source is resolved explicitly. One valid source → quantity view (MAX = available source quantity, the default) → dedicated confirmation view → `Confirm transfer`. Multiple source Areas/QuantityFlows → an explicit source-selection dialog first; quantities from multiple sources are never combined silently. Back on the confirmation returns to the quantity view with the source selection preserved. When the transferred quantity is still actively processing at the source (`ON_MACHINE` or direct processing), the transfer implicitly completes that processing: the confirmation identifies the completion (`Source processing: Completed by this transfer …`) and its `Recorded events` row states `AREA_COMPLETED, then TRANSFERRED — one atomic operation`; quantity already `READY_TO_TRANSFER` (or still queued) at the source transfers with `TRANSFERRED` alone and never duplicates `AREA_COMPLETED`. Undo reverses the complete command (§4.5). Partial quantity completes and transfers only the selected quantity; the remainder keeps its source state.
3. **PN already has quantity in the Area**: an action dialog shows only the currently valid choices (a selection view — not a confirmation step):
   - **Assign queued quantity to a Machine** (only when queued quantity exists) — the assignment wizard (§4.6) with the PN preselected.
   - **Receive more quantity from another Area** (only when transferable quantity exists elsewhere; explicit source selection when several).
   - **Complete processing — DONE** (direct-processing Areas only, when actively processing quantity exists) — the manual DONE wizard of §4.6 without a Machine field.
   - **Add more quantity** — operator guidance reads: “Add physical quantity that was not received from another Area. Enter a reason so the adjustment can be reviewed later.” Operator-allowed, no Manager approval, mandatory reason, mandatory quantity with **no MAX button and no default value**, then a dedicated confirmation view whose `Recorded event` row states `QUANTITY_ADJUSTED · INCREASE` (never hidden as an ordinary transfer, never changing the WO Demand requested quantity); `Confirm addition` is the only write point. Added quantity enters the Area queue (Areas with Machines) or direct processing (Areas without Machines).
   - **Send quantity here for repair** (label wording — never “Create REPAIR demand”; only when quantity elsewhere may deliberately return to this previously visited Area): choose the Repair intent → select source Area/QuantityFlow, repair quantity and required reason → a dedicated confirmation view that explicitly identifies `movement_reason = REPAIR` → `Confirm repair`. Repair is never converted into a Request Type, and never auto-selected merely because the Area appears earlier in history — a normal transfer to a previously visited Area stays possible. Partial quantity requires a QuantityFlow SPLIT.
   - **Scrap damaged quantity** (§4.9).

## 4.8 Quantity entry

Quantity entry is a wizard step with an oversized keypad **and a real focusable numeric text input**: entering the step focuses the numeric entry and selects its value, so a physical keyboard works without clicking first. `inputMode="numeric"`, no native number-spinner. Virtual keypad buttons are `type="button"` and non-focusable (`tabIndex={-1}`, focus kept on the input), so clicking one never leaves focus where Space or Enter would re-activate it.

Every quantity view uses one fixed visual order: **PN → selected context or instruction → guidance/validation message → quantity input → numpad → navigation buttons**. The guidance sits directly above the input as plain semantic text (never inside a framed or dashed summary box): muted text for neutral instructions (`Enter the physical quantity received. No default quantity is assumed.`), info/warning text for important limits or selected context (`Available at Manual: 4 pcs. MAX is selected by default.`, `Assigning to Lathe 2. Queued quantity: 8 pcs.`), error text for invalid input (`Quantity cannot exceed the 6 pcs currently available at the source.`). The validation message updates next to the input, and **Next stays disabled while the quantity is invalid**.

Physical keys are handled centrally:

```text
0–9       append digit
Backspace remove last digit
Delete    clear
Enter     advance to the confirmation view (never directly to a write)
Escape    cancel the whole workflow
Space     ignored
Other     ignored
```

Enter on the quantity step always means “advance to the confirmation view” regardless of which virtual keypad button was clicked previously; Enter on the confirmation view performs the final Confirm; Escape always cancels the entire wizard. Transfer and assignment show **MAX** and default to it; a smaller valid quantity may be entered. Add More Quantity has no MAX and no default. Next is blocked for 0 and for quantities above the available source; all server-side quantity validation requirements remain (the mock validates presentation-side only).

## 4.9 Scrap workflow

Canonical term `SCRAPPED`; UI wording “Scrap damaged quantity”. One dedicated, context-sensitive barcode `PF:SCRAP`:

- accepted **only** inside the Scrap workflow — the main scan input rejects it;
- each scan increments the pending scrap counter by one; counting changes no production state;
- the pending count can be corrected (−1) or reset before confirmation;
- a common scrap reason is mandatory;
- available, pending scrap and remaining quantities stay visible while counting;
- the **dedicated confirmation view** shows PN, Area, Machine if applicable, original available quantity, scrap quantity, remaining active quantity, Worker, Station, and reason as a structured summary;
- Cancel on any step discards the pending count with no write; `Confirm scrap` on the confirmation view is the only write point and creates **one** auditable `SCRAPPED` operation for the total.

Scrapped quantity is displayed wherever the PN appears operationally (Scan Station, Production Board, Area Board, Tracking).

## 4.10 Area and Machine monitoring layout

The shared layout (also used by the Area Board detail, §6.3):

```text
[ In this Area now ] [ Machine cards grid                    ]
[ fixed left col   ] [ Machine 1 ][ Machine 2 ][ Machine 3   ]
[ grows vertically ] [ Machine 4 ][ Machine 5 ]              ]
```

- `In this Area now` is the left column; Machine cards occupy only the right-side grid, wrap to additional rows **within** that grid, and never wrap underneath the left card (`align-items: start`). The left card may grow vertically with its PN list but never grows without bounds (its column is capped), and it is wide enough to display a normal full PN such as `2027-60-8114-00` with reasonable spacing between the left PN and the right-anchored context/quantity information; extremely long PNs truncate with an ellipsis and a tooltip. The single-column fallback is **container-aware**, not one arbitrary viewport breakpoint: when the containing view can no longer fit at least one usable Machine card beside the summary, `In this Area now` fills the full row width and the Machine cards move below it at their normal width.
- Areas without Machines render only `In this Area now`, spanning the full width — no blank Machine region.
- The `In this Area now` card uses the Area color along its top edge, presents its compact Area description (e.g. `Turning cell · Lathe 1–4`) inside the card header block exactly like the Management → Area Board → All Areas column headers — never as a separate framed or strongly divided block — and carries **no statistics block** (the Scan Station header is the single totals surface, §4.3; the Area Board keeps the statistics row on its summary card through the shared component's `showStats` option). It uses the same PN presentation language as Management → Area Board → Area with the quantity groups `On Machines` and `Area queue — awaiting Machine` when the Area has Machines, a direct `In processing` group otherwise (`Stocked` for the terminal Stockroom), and a `Finished — ready to move` group for `READY_TO_TRANSFER` quantity. DONE rows use a clear success/ready status (`done` chip; status line `Finished — ready to move`, or `Finished at Lathe 3 — ready to move` when a Machine completed the work as completion context), show their quantity, never imply the quantity is still assigned to that Machine, and remain located in the current Area until transferred. After DONE is confirmed the quantity leaves the Machine card and appears in this group; Machine cards show only actively assigned quantity.
- **PN row layout.** Each PN row is an explicit grid: production information on the left, the optional action in its own separated right-side cell:

  ```text
  ┌────────────────────────────────────────────────────────────┬──────────┐
  │ Hot + PN                         Machine/Queue · quantity  │          │
  │ WO Number · Job Numbers                    days remaining  │  ACTION  │
  │ in-Area status                              time in Area   │          │
  │ n scrapped                                                 │          │
  └────────────────────────────────────────────────────────────┴──────────┘
  ```

  Line 1: Hot indicator and PN left; Machine/queue/done context and quantity (formatted with `pcs`) right. Line 2: Work Order Number (`—` when blank) and external Job Number left (may truncate with an ellipsis; the complete value stays available through a `title` tooltip); due/days-remaining status right. Line 3, when meaningful: in-Area status left (`Awaiting Machine`, `On Machine`, `In processing`, `External processing`, `Finished … — ready to move`); time in Area right. Line 4, only when scrapped quantity exists: `{n} scrapped`. Scrapped quantity appears **only** as this readable text — the compact `⊘n` indicator no longer exists and scrap is never displayed twice. Rows inside an Area summary show their Machine/queue/completion context chip; rows inside a specific Machine card pass the explicit `showContext={false}` presentation option so the Machine already identified by the card header is never repeated per row (never hidden through fragile CSS selectors).
- **Action rail.** When a row has actions, they sit in a dedicated right-side action cell — separated by spacing and a subtle vertical border, vertically centered, with a predictable minimum width — never immediately beside the PN, context or quantity. Machine-card rows stack `DONE` above `QUEUE` inside that cell (§4.6). At very narrow widths the actions may stack below the content but stay visually separated. PN rows are never clickable as a whole. Action visibility: `In this Area now` shows no row actions; Machine-card rows show `DONE` and `QUEUE`; the Area Board passes no actions at all (read-only).
- The presentation is built from shared components (`AreaPnRow`, `AreaSummaryCard`, `MachineMonitoringCard`, `DueStatus`, `QuantityStatus` — domain-named, no vague helpers) so Scan Station and Area Board never drift apart; changing the shared row affects both surfaces and both must be verified.

## 4.11 States

| State           | Behavior                                                            |
|-----------------|---------------------------------------------------------------------|
| Loading         | Skeleton panels; scan input disabled with "Connecting…" placeholder |
| Empty inventory | "No production in this Area" placeholder row                        |
| Disconnected    | Persistent OFFLINE banner with actionable message; production write submission disabled; loaded read-only data stays visible |
| Reconnected     | Banner clears; scan input re-enabled and refocused, ready for the next scan |
| Error           | Red floating notification (~8 s, closable); input cleared and refocused, ready for next scan |

---

# 5. Production Board

Read-only, full-screen, for large shared displays. No interactive elements except an (optional, admin-gated) settings gesture.

- **Header clock (v7):** the header shows the live local date **and time**, driven by a self-updating clock component — never a static mock timestamp.
- Columns (PROJECT_PROFILE §21): **No. · Part Number · Areas & Quantities · Time · Due Date · Total Days · Job Numbers** — Job Numbers is deliberately last (v7).
- **Content-driven column sizing (v7):** column widths come from semantic `colgroup` classes, not fixed percentage widths. No., Part Number, Due Date and Total Days shrink to their content and never wrap (Due Date keeps a minimum width so its secondary line always fits); Job Numbers absorbs the remaining width and may wrap.
- **Part Number** renders on a single line with **intrinsic sizing**: a PN shorter than the standard 15 characters still reserves a real `15ch` monospace content minimum (in the PN's own font metrics — not an arbitrary fixed pixel width), a 15-character PN such as `2027-60-8114-00` displays fully, and a longer PN expands the column to display fully — PNs are **never truncated** on this board. No Hot-chip space is reserved inside the PN width. Name and revision appear as a secondary line that wraps beneath the PN without widening the column. After every column has enough width for its content, remaining table width is distributed primarily among Part Number, Areas & Quantities · Time, and Due Date (after its minimum); Job Numbers receives a bounded share; No. and Total Days stay compact, with Due Date and Total Days never narrower than their own headings (which never wrap).
- **Hot presentation (v11):** the flame renders in the **No. column** (`1 🔥`, `2 🔥`) with an accessible label such as `Row 1, Hot priority`; the Part Number cell contains **only** the PN and description — never `🔥#n`, and never a second flame elsewhere in the row. Hot sorting, priority semantics and the Hot row tint are unchanged, and the footer legend describes the No.-column flame. The other views (Area Board, Tracking, Priority) keep the shared `🔥#n <PN>` presentation (`HotPn` in `src/components/indicators.tsx`).
- **Areas & Quantities · Time** uses a **stable grid** (v7) with **explicit presentation data** (v10, extended v11): each location row carries the Area identity/label, an optional separate Machine name, an optional External activity, quantity, state, and time — never one composite display string (`External — Plating`, `Lathe 2 machine`) that would have to be parsed back apart. Every row follows **`Location | Quantity | State/activity | Time`**: Location left-aligned; Quantity immediately after Location with a fixed gap; State/activity next; Time anchored to the right edge in monospace. Location, quantity and state columns size to their widest content — a long Area or Machine name **expands the column minimum** instead of truncating — so nothing overlaps or clips for Machine, queue, direct processing, External activity, DONE, Stockroom, long durations, or long Machine names; the column's minimum width follows from the actual content (longest Area label, full Machine chip, quantity, state chip, longest time, total row, scrap chip), never a fixed width tuned to short sample data. Actively Machine-assigned quantity renders as `[Area color dot] [Lathe 2 chip]  4  on mch.  1h 05m`: the Machine name in a compact rounded chip (label look — subtle chip/panel background, subtle border, rounded corners, both themes, no action color), the **full Machine name always visible — never an ellipsis**, the exact concise state `on mch.` (never the standalone word `machine`). Queued quantity keeps a `queue` label; direct processing shows `processing`. **External locations show only `External`** as the Area text, with the specific activity (`plating`, `vendor`, `painting`, …) as a **light informational chip in the state position** replacing the generic `processing` label; DONE External quantity shows `done` as its current state with the activity as secondary tooltip context only. DONE/`READY_TO_TRANSFER` quantity is visually distinct from active Machine processing: it shows the current Area with a concise `done` state, never `on mch.`, never the Machine as current executor — a Machine that completed the work appears only as secondary completion context (tooltip). A time turns amber when it exceeds the expected duration of the active Route Step. **One continuous dashed separator** — a dedicated element spanning the complete location grid, never per-cell border fragments — sits between the location rows and the **total row**, which starts at the Location column (`total`), aligns its quantity with the quantity column, follows with `pcs` (Stockroom appends `stocked`), and reserves the right-hand time position.
- **Due Date** carries a highlighted secondary line with days left (amber = due soon, red = overdue, neutral = comfortable / stocked). There is no separate Days Left column. A missing due date renders as `—` (§3.9); a blank external Work Order Number in the Job Numbers metadata renders as `—` too.
- **Scrap visibility (v11):** a PN with scrapped quantity shows a quiet **`n scrapped` chip on the total line itself** — a light pill on a subtle error surface with a subtle border, in a small state-label font, anchored in the right-hand time position of the total row. No `⊘` symbol, no extra table row, no overlap with `pcs`, and no chip at zero scrap; the footer carries the Department scrap total.
- **Priority model:** Hot Work Order Demand rank from Priority Management. Hot rows sort first in rank order with a row tint that gets redder the hotter the rank; non-Hot rows follow in the canonical demand ordering (PROJECT_PROFILE §18) — dated rows earliest-first, undated rows after all dated rows ordered by Work Order received date.
- **Due-date urgency (v7):** only the urgency text — days remaining / days overdue — blinks (threshold configurable); the PN and the date itself stay steady. The footer legend states this. `prefers-reduced-motion` disables the blink animation while keeping the warning color and weight. Blink remains reserved for due-date urgency only — nothing else on the board may blink.
- **Dynamic pagination (v7) with manual controls (v11):** pages are calculated from the **actual available board height and actual rendered row heights** (via a hidden measurement table), recalculated on viewport/container resize, data changes, and theme/font-metric changes (ResizeObserver plus window resize). At least one row is always shown per page; the active page clamps when the page structure changes. Pages rotate automatically every 12 s **only when more than one page exists**; the page indicator is accurate and a single page never claims rotation. (In DOM environments without layout — tests — a fallback of 10 rows per page applies.) The footer additionally offers **manual navigation**: a Previous and a Next button (Previous disables on the first page, Next on the last — manual navigation never wraps; automatic rotation may continue wrapping) and **clickable page dots** with clear accessible labels and `aria-current` on the active page. **`ArrowLeft` / `ArrowRight`** navigate pages at window level regardless of focus (the board has no normal text-entry workflow): no action when the requested page does not exist, no wrap, ignored under Ctrl/Alt/Meta, inert while an application modal dialog is active, and a valid page change consumes the key (no horizontal scrolling). Every manual change — button, dot, or arrow key — **restarts the auto-rotation timer**; the listener is cleaned up on unmount.
- Footer carries the legend and aggregate stats (active PNs, pcs in production, pcs stocked).
- Auto-refresh; a **"Live" indicator with a board-owned heartbeat**: the connected dot pulses via the Production Board's own `pb-live-pulse` animation (never a Scan Station CSS dependency), the stale/reconnecting state shows a solid warning dot with explicit text, and `prefers-reduced-motion` renders a solid semantic dot without animation.

---

# 6. Area Board

Monitoring sub view of Management (follows the global theme mode, §2.1). One view, two modes behind a single tab strip: the **All Areas overview** (the §21 *Manager Summary* content) and the **per-Area detail** (the §21 *Area Board* content). The "Manager Summary" name is retired.

## 6.1 Tab strip and toolbar

- The tab strip starts with **All Areas** — the default on first open — followed by one tab per Area with its color dot and item count. Exactly one tab is active.
- Shared toolbar for both modes: text search (PN / WO / Job Number), sort selector (Due date, Priority, Time in Area, Quantity), and a scope summary (PN count and total pieces for the active Area, or across all Areas in the overview).

## 6.2 All Areas overview

One column per Area; the layout scrolls horizontally when all Areas do not fit. Each Area column shows:

- a **clickable header** (Area color, name, description, supported Operation chips) with a "detail ›" affordance — clicking it opens that Area's detail mode, equivalent to selecting its tab;
- a stat row with only meaningful values: Areas with Machines show **total pcs · queued · on machines** plus **done** when finished quantity exists; Areas without Machines show **total pcs · processing** plus **done** when finished quantity exists (no zero-value noise); the terminal Stockroom column shows **stocked pcs · PNs**;
- the PN list for the Area, built from the **shared PN-row shell and subcomponents** (§4.10 — `AreaOverviewRow` reusing the shared row grid, `HotPn`, `QuantityStatus`, `DueStatus`): PN (🔥 + rank when Hot), quantity, WO + Job Number (a blank Work Order Number as `—`, truncating with a `title` tooltip), due date (color-ramped), the card's portions aggregated as compact context chips (`Lathe 3 × 3`, `queue × 2`, `processing × n`, success-toned `done × 1`), time in Area, and `{n} scrapped` where present — no independent JSX/CSS row implementation that could drift from the shared presentation;
- an explicit empty state for Areas without production.

Search filters the PN lists; the sort selector orders PNs within each column.

## 6.3 Per-Area detail — shared Area/Machine monitoring layout

The detail mode uses the **same structural layout as the Scan Station** (§4.10):

```text
[ In this Area now ] [ Machine cards grid                    ]
[ fixed left col   ] [ Machine 1 ][ Machine 2 ][ Machine 3   ]
[ grows vertically ] [ Machine 4 ][ Machine 5 ]              ]
```

- **Area summary card (left):** Area color top edge, Area name/description/Operations; the statistics row (the Area Board keeps `showStats` on — including the Done statistic with the success tone); then the grouped compact PN list — **"On Machines"** and **"Area queue — awaiting Machine"** only when the Area has Machines, a direct **"In processing"** group otherwise (the terminal Stockroom shows **"Stocked"**), and **"Finished — ready to move"** for `READY_TO_TRANSFER` quantity. Finished quantity belongs to the Area summary, never to a Machine card, and is never displayed as Stocked or manufacturing-complete. Each entry uses the shared grid PN row presentation (§4.10): Hot indicator + PN (`🔥#n` before the PN, §5), machine/queue context with the quantity (`pcs`), WO (or `—`) + Job Number with a tooltip for truncated values, due status, in-Area status, time in Area, and `{n} scrapped` text when scrapped quantity exists. Grouping never duplicates or loses quantity.
- **Machine monitoring cards (right-side grid only):** one card per Machine — name, status (running / idle / maintenance), total quantity assigned, PN count, assigned PN list in the same shared row presentation. Idle machines show a clear empty state; maintenance machines stay visually distinct (dashed warning border). Cards wrap to additional rows within the right grid and never underneath the left card; a deliberate single-column fallback applies at narrow breakpoints. In Phase 2 the Machines come from the mock model `MOCK_AREA_MACHINES` (Cut → Saw 1; Lathe → Lathe 1 idle, Lathe 2 running, Lathe 3 running, Lathe 4 maintenance; Mill → Mill 1, Mill 2).
- Areas without Machines render **only the full-width summary card** — no placeholder Machine cards and no blank Machine region.
- **Area Board remains read-only:** the shared components render without any Scan Station action rail (no `QUEUE` buttons, no action cells), and there is no visual drift between the two views (shared `AreaPnRow`, `AreaSummaryCard`, `MachineMonitoringCard`, `DueStatus`, `QuantityStatus`).
- Search and the sort selector (§6.1) still narrow and order the PN lists. **Sort: Time in Area** orders by the sortable duration field (`timeInAreaMinutes`), longest first.
- An over-long PN is truncated with an ellipsis; hovering shows the full PN as a tooltip (§2.3).
- Empty state: "No production in {Area}".

---

# 7. Tracking

Primary management interface, **PN-centric** per PROJECT_PROFILE §21. Master–detail layout: filterable PN list (left) + PN detail panel (right).

## 7.1 Filters and list

Search across PN, WO and Job Number; selects for Area, Operation, Machine, Request Type (`NEW` / `MODIFY`), priority (Hot only), status, and due window. List columns: PN (+ name; Hot entries use the standard `🔥#n` presentation before the PN, §5; archived PNs carry an explicit `(archived)` marker while keeping the original PN text), active WO Demand (WO · qty · Request Type chip — a blank Work Order Number as `—`), current distribution (Area color dots), active quantity, stocked quantity, cumulative scrapped quantity, next due date (`—` when the demand has no due date, §3.9), status pill (Active / Stocked / Completed).

## 7.2 Detail panel sections

1. **PN master** — image placeholder, PN, name, current revision (informational), barcode value (`PF:PN:…`), ERP id.
2. **Active WO Demand** — table of WO · Request Type · requested · allocated · remaining shortage · due · priority, with an allocation progress bar. Labeled "business demand — separate from Movement".
3. **Current quantity by Area** — horizontal bars per Area/Machine in Area colors, queue rows visually distinct; labeled "derived from Movement history".
4. **Quantity Flows & Routes** — one block per Quantity Flow with its **route mode badge**: a `PLANNED` flow shows its AssignedRoute snapshot chips (done → current → queued → future; deviations marked with who/when/reason and the audit-preservation note); a `FLOATING` flow shows the **actual route trace derived from Movement history** — repeated Areas preserved, split flows shown independently, and Repair transfers marked explicitly, e.g. `A → B → C → D → B ⟲ REPAIR`. Route steps and arrows render as **separate sibling flex items** (step, arrow, step, …): each arrow is centered between adjacent steps, arrows never overlap step cards, and wrapping stays readable. The finished rack never appears as a route step — `AREA_COMPLETED` is completion inside the existing source Area; only `TRANSFERRED` extends the trace. The section explicitly avoids implying the whole PN is at one Route Step.
5. **Movement history (immutable)** — reverse-chronological: timestamp, Movement type (color-coded, canonical names, `SCRAPPED` and `AREA_COMPLETED` included — `AREA_COMPLETED` uses the success tone; Repair transfers carry an explicit REPAIR badge), full description (areas, quantity, Quantity Flow, machines, worker, station, reasons). The current-state sections distinguish active Machine assignment, direct Area processing, Area completion (ready to transfer — operator wording such as `Completed processing at Lathe — ready to transfer`, with the completing Machine as context only), transfer, and Stockroom completion; ready-to-transfer quantity is never implied to be `STOCKED`. Read-only; no edit affordances exist.
6. **Scrap history** — the PN's `SCRAPPED` events and cumulative scrapped quantity, with the reconciliation note (introduced = active + stocked + scrapped; scrap never reduces requested quantity).
7. **Stocked & Allocation history** — stocked quantity and Work Order Allocation entries with the §18 ordering note; empty state when nothing is stocked.
8. **Corrections** — authorized-only actions as explicit buttons: Quantity adjustment, Edit assigned Route, Adjust WO Allocation, Change priority, View audit trail. Every correction flow requires a reason and produces new history.

## 7.3 States

Loading skeletons per section; empty filter result ("No PNs match — clear filters"); permission-restricted users see the Corrections section hidden entirely rather than disabled.

---

# 8. Priority Management

Manages the Department's Hot Work Order Demand list (PROJECT_PROFILE §21 Priority Management). Priority belongs to Work Order Demand; multiple Work Orders for the same PN may hold different ranks.

- The list shows each Hot entry with its rank in the standard Hot presentation (`🔥#n` immediately before the PN, §5), WO + Job Number, Request Type chip, demand figures (requested / allocated / shortage), current distribution, and color-ramped due date (`—` when absent, §3.9).
- **Add:** the "+ Add to Hot list" button opens a dialog with a single search field that accepts free text (PN / WO / Job Number) *and* PN barcode scans — scanning with the dialog open adds the matching Work Order Demand directly. If a PN has multiple active Work Order Demand records, each is listed and added separately. New entries always join at the **bottom** of the list.
- **Remove:** each entry has a remove (✕) affordance. Clicking it opens a **confirmation dialog identifying the PN and Work Order Demand**; Cancel changes nothing. On Confirm the entry is removed, remaining ranks close the gap, the change is applied and audited, and Undo can restore it. Confirmation guards the removal; it does not reintroduce a separate save-or-cancel workflow.
- **Reorder requires confirmation (v7), presented as two snapshots (v11):** every operation that changes the order of existing Hot entries — drag-and-drop (visible grip), Move Up, Move Down, Undo, and Redo — opens a **confirmation dialog before applying**. The dialog leads with a primary summary of the item the user moved (`Move 2027-60-8114-00 · WO 007010 from #4 to #2` plus a computed secondary line such as `2 other demands will shift down.`; the implementation action name stays secondary detail only), followed by a **`Current Position` → `New Position` two-snapshot layout** restricted to exactly the affected rank range (never the full Hot list), with **one centered downward transition arrow** between the sections — visually distinct from the per-row rank arrows. Each entry line separates rank, direction, PN, and WO/Job metadata: the rank renders first; in **Current Position** a per-row **↑/↓ arrow beside the current rank** shows where the entry will move (↑ toward rank #1, ↓ away from it), with rows kept in current rank order; **New Position** shows the order after applying, without per-row arrows. The **PN** uses primary text color, stronger weight, monospace and clear spacing; **Work Order and Job Number** render as one light informational chip (`WO 007001 · Job 18112` — subtle surface/border, rounded, secondary tone, no button behavior, full metadata as tooltip) built from the explicit `workOrderNumber`/`jobNumber` fields, never parsed out of a display string. The directly moved entry is highlighted, indirectly shifted entries render with less emphasis, and an entry that exists on only one side of the change (Undo/Redo restoring a removed entry or taking back an added one) shows a clear **`Not listed`** placeholder on the side where it does not exist — never a silent omission. Buttons: `Apply ranking` and `Cancel (Esc)`. Cancel leaves the list order and both Undo/Redo histories unchanged; the visible list is **never renumbered before confirmation**. Undo and Redo confirmations use the user-facing titles `Restore previous ranking` and `Reapply ranking` and present the same snapshot layout. Adding a new entry at the bottom keeps its direct behavior (no order-change confirmation).
- **Undo / Redo instead of save-or-cancel:** every confirmed change is applied, audited, and can be stepped back and forward with Undo/Redo buttons — each step is itself an order change and shows the same confirmation. The buttons disable when the corresponding history is empty.
- A footer note restates the rules in user language: Hot demand is always worked first, in rank order, and ordering follows the canonical demand ordering — ① Hot rank ② dated demand earliest-first, with undated demand after all dated demand ordered by Work Order received date (PROJECT_PROFILE §18). Implementation identifiers (`priority_rank`, tie-breakers) never render.

---

# 9. Administration

Isolated from production. Sidebar navigation grouped as:

- **Organization:** Departments, Areas, Operations, Machines, Workers, PartNumbers (archive/soft-delete maintenance)
- **Production setup:** Route Templates, Barcode configuration, Scan behavior
- **Access:** Users, Roles & permissions
- **Policies:** Worker sessions, Machine assignment, Correction permissions, History archival & purge, Settings

Operations are managed per PROJECT_PROFILE §8.5 — each Operation belongs to an Area, and the Areas table lists the Operations an Area supports.

Each section is a standard table + editor pattern. The Areas table is the reference example: Area (color + name), Operations, Machine assignment (Direct processing — no Machines / Queue → assign (one-shot); the mode follows from the Area's Machines, never from a per-count configuration), Machines, Worker ID mode (Disabled / Fixed Worker / Scanned session), Terminal flag, Active status.

**PartNumbers maintenance:** the administrative “delete” archives (soft-deletes) a junk/test PN by default — it disappears from active lookup and intake while history keeps the original PN text with an `(archived)` marker; a physical purge is a separate, explicitly named maintenance operation. **History archival & purge:** Admin-only maintenance workflows (retention policy, size threshold, or manual request) with a scope/impact preview, a required reason, and full audit of who ran what and when; archive is the default, purge separate and more explicit; retention settings live here, never in production workflow logic (PROJECT_PROFILE §28 Administrative Archival and Purge). Phase 2 shows these sections as specification placeholders.

Editing an Area's display properties shows an inline note that identity and barcode are stable and history is unaffected. Destructive operations (deactivating an Area with active quantities) are blocked with an explanation, not confirmed through.

---

# 10. Completion / Receiving UI (Stockroom Scan Station)

The Stockroom station reuses the Scan Station shell with one additional step: after the `STOCKED` Movement, an **allocation dialog** shows the suggested split across outstanding Work Order Demand in the exact §18 canonical demand ordering — ① highest Hot rank ② dated demand earliest-first, undated demand after all dated demand ordered by Work Order received date. Each row shows the Work Order, requested quantity, previously allocated quantity, remaining shortage and the proposed quantity, adjustable with +/− steppers — Operators may review and adjust the suggestion before confirmation (PROJECT_PROFILE §18). Confirm is enabled only when the allocated total equals the stocked quantity. Routine receiving requires no Manager involvement; Admin and Manager may adjust the allocation later, with every change audited.

---

# 11. Work Orders

Management sub view (follows the global theme mode, §2.1) implementing manual Work Order entry and explicit production release (PROJECT_PROFILE §13; §21 *Work Orders* — the view was called *PO Intake*, then *Purchase Orders*, before the v6 vocabulary migration, §12.6). It handles business demand only — it is not ERP-style customer, pricing, invoicing, shipping, purchasing, or accounting functionality.

The view lives on one route (`/management/work-orders`): the **WO list** is the entry screen, and selecting a Work Order opens **Work Order Details as a modal dialog over the list** (v10) — the list stays mounted and visible behind the overlay and the URL never changes. **New Work Order** is likewise a **modal dialog over the WO list** (v6). There is no separate detail panel/view and no detail route.

**Editable dates use native calendar controls** (`<input type="date">`) in every Work Orders form: New Work Order received date and WO due date, the OPEN Work Order due date, and every demand-line due date. Editable values are ISO `YYYY-MM-DD` internally; read-only presentation formats them as `Jul 24, 2026`. No date-picker dependency is added — the native accessible control works in both theme modes (`color-scheme` follows the theme) and stays keyboard-accessible.

In Phase 2 every Work Orders interaction changes **development-only local mock state** and says so explicitly; nothing is persisted to the backend.

## 11.1 WO list

- One row per Work Order: WO Number, received date, **WO due date** (color-ramped like all due dates; `—` when absent, §3.9), demand-line count with a PN preview, and status (**Open** / **Released** / **Complete**).
- Search over WO Number. An existing WO Number is always opened, never duplicated; a miss offers "＋ New Work Order".
- **＋ New Work Order opens the modal dialog of §11.3 over the list.** The list stays mounted and visible behind the overlay, and the URL remains `/management/work-orders`.
- Internal Work Orders without an external number (e.g. from a Scan Station MODIFY intake, PROJECT_PROFILE §14) appear like any other Work Order: the blank number renders as `—` (display-only — never persisted), the row is clearly labeled "internal Work Order — no external number yet", and the Work Order is found through its PN preview. No temporary Work Order Number is ever generated.
- Completed Work Orders (every Work Order Demand fully allocated) move out of the active list but remain permanently available in history (PROJECT_PROFILE §8.2).
- **Demand lines are shown only after a Work Order is selected** — selecting a row opens the **Work Order Details** dialog (§11.2).

## 11.2 Work Order Details — modal dialog with demand lines

- Selecting a Work Order opens a modal dialog titled **`Work Order Details`** over the list, built on the shared accessible dialog primitive (`role="dialog"`, `aria-modal`, focus trap, initial focus, Escape/backdrop close requests, responsive sizing with internal scrolling). On close, focus returns to the Work Order row that opened it.
- Primary actions: **`Save demand`** and **`Cancel (Esc)`**. Closing through Cancel, Escape, the backdrop, or any other close request never silently discards edits: a dirty draft asks for explicit discard confirmation first; a clean dialog closes directly.
- Child dialogs (Add Part, removal/release confirmations) stack correctly: only the topmost active dialog handles Escape, backdrop, and focus trapping — a child's close request never closes Work Order Details.
- Dialog header: WO Number, received date, WO due date (**editable with a calendar control while the Work Order is Open**), line count, status.
- **WorkOrderDemand rows:** an editable table, one row per PN demand: PN (lookup or create — any non-empty PN text is accepted and created on first use, entered casing preserved, identity case-insensitive), Request Type (`NEW` default / `MODIFY`), requested quantity, due date (calendar control), priority when applicable, external Job Numbers, requester / reason / notes. Long WO line lists scroll with a sticky header and a line count.
- **Adding demand lines (v7 — manual-first):** an **OPEN** Work Order shows a prominent **＋ Add Part manually** action; CLOSED/completed Work Orders remain read-only. Add Part manually opens the multi-step Add Part dialog of §11.3 (PN → quantity → due date → optional metadata). Barcode scanning remains available as a **secondary, optional method**: it accepts only valid PN barcodes and rejects unknown barcodes. A new line joins the Work Order as a visibly marked **unsaved draft** — Request Type defaults to `NEW`, the due date defaults to the WO due date when one exists. Scanning or entering a PN already on the Work Order focuses the existing line instead of adding a duplicate; a released line is announced as read-only instead.
- **Removing demand lines (v6)** follows the canonical Work Order Demand removal rule (PROJECT_PROFILE §13):
  - an unsaved draft line is removed immediately;
  - a saved line with no released production quantity is removed only after an explicit confirmation dialog;
  - once any quantity has been released to production, the line's remove action is disabled with the explanation "Cannot remove: production quantity has already been released." — later adjustments go through correction/production workflows, never deletion;
  - removal never deletes the PartNumber master, Quantity Flows, Movements, release history, or other Work Order Demand for the same PN.
- **Due-date default (v7):** each line's due date defaults to the **WO due date** (when one exists) and may be edited per line. A blank due date is valid and displays cleanly (§3.9). Changing the WO due date updates only lines still holding the previously inherited default; a line whose due date was manually changed keeps its value, and a line explicitly set to **"No due date"** counts as user-edited — it never inherits a later WO due date.
- **PN lookup / create:** the PN field searches existing PartNumbers; a new PN can be created inline, which shows a **barcode preview** (`PF:PN:…`) and creates the unique PN barcode with the PN master. An **inactive PN** is flagged and cannot be released without reactivation.
- **Validation states (v7):** per-field errors (missing PN, quantity not a positive integer, duplicate PN); a row with errors cannot be saved and is **never silently filtered out**. A **missing due date is not an error and never blocks saving** (PROJECT_PROFILE §8.3) — absent dates are summarized in the save confirmation instead (§11.3). After a failed save the first invalid control receives focus and all entered values are preserved.
- **Unsaved changes** are visibly marked ("● Unsaved changes") and guarded: leaving the Work Order — via the back action, top-level navigation, Management sub-navigation, browser back/forward, or reload/tab close (`beforeunload`) — requires explicit confirmation before the draft is discarded.

## 11.3 New Work Order — manual-first modal dialog and Add Part flow

- **Presentation (v6):** New Work Order opens as a **modal dialog over the WO list**. The dialog has `role="dialog"`, `aria-modal="true"`, an accessible name from its visible heading, initial focus inside the dialog, keyboard focus trapping, Escape and backdrop-click close requests, focus restoration to the **＋ New Work Order** button on close, responsive sizing with internal scrolling for long line lists, and follows both theme modes. The URL never changes.
- **Nothing is silently discarded:** Escape, backdrop click, Cancel, or any other close request on a dialog with entered data first asks for explicit confirmation ("Discard this New Work Order?").
- **Header form (v8):** WO Number (**optional**), received date (calendar control, defaults to today, always available), and **WO due date** (calendar control, **optional**) — the default due date for demand lines when set. The dialog explains the consequences plainly: a blank WO Number saves an internal Work Order with a **null** number that displays as `—` and may receive the real external number later through an audited edit (PROJECT_PROFILE §7 — no temporary number is generated); due dates can be added later; the Work Order can be saved without a due date. Entering a WO Number that already exists opens the existing Work Order instead of duplicating it — **duplicate handling applies only when a WO Number was entered**; if lines were already entered, opening the existing Work Order is confirmed explicitly first.
- **Manual Part addition is the primary workflow (v7):** a prominent **＋ Add Part manually** action opens **one accessible multi-step dialog** (no stacked nested modals):
  1. **PN** — search and select an existing PartNumber, or explicitly create a new one (with barcode preview, §11.2);
  2. **quantity** — positive whole number, with the same keypad + physical-keyboard interaction as the Scan Station quantity dialog (§4.8). The shared `QuantityKeypad` owns its stylesheet (`components/QuantityKeypad.css`, imported by the component), so the step renders correctly wherever it is mounted — never depending on Scan Station CSS or route navigation order;
  3. **due date** — defaults to the WO due date when one exists; an explicit **"No due date"** choice is valid;
  4. **optional metadata** — Job Numbers, Notes, Request Type.
  Backward navigation preserves entered values. Completing the dialog creates an editable **draft row**; nothing is persisted until Save demand.
- **Barcode scanning remains available as a secondary, optional method:** a PN barcode carries the PN itself (`PF:PN:<part-number>`); the entire non-empty suffix is the PN, a PN outside the catalog is created on first use, and non-PN barcodes are rejected with an error — nothing is added. Scanning a PN that is already on the Work Order focuses the existing line (edit its quantity) instead of adding a duplicate. The Work Order entry workflow is **no longer scanner-first** — it is desk work; the Scan Station shop-floor workflow (§4) remains scanner-first and is unchanged.
- Changing the WO due date updates only lines still inheriting the previous default; lines whose due date was edited — including an explicit "No due date" — keep their value (§11.2). Request Type and due date stay editable per line before saving.
- **Save demand with omission confirmation (v8):** Save validates the header and all rows (§11.2 validation states — field-level errors, first invalid control focused, values preserved, incomplete rows never dropped; missing due dates never block saving). When the external WO Number, the WO due date, or any line due dates are absent, a **confirmation dialog summarizes the omissions** before anything is saved: the Work Order is saved as an internal Work Order without an external number (displays `—`), the Work Order remains unscheduled, and N undated lines get the lowest due-date priority with later received-date ordering (PROJECT_PROFILE §18). Save happens only after explicit confirmation; Cancel returns to editing with all values preserved. On save the dialog closes and the new Work Order appears in the list as **Open**. In Phase 2 the save changes local mock state only and reports that nothing was persisted to the backend.

> **Data-model note:** the WO due date is the nullable `due_date` attribute on WorkOrder (PROJECT_PROFILE v8 §8.2). It is an entry default only — WorkOrderDemand keeps its own nullable `due_date` as the operative business value.

## 11.4 Demand save vs. production release

- **Save demand** persists the WorkOrder and WorkOrderDemand rows only. Saving never creates production quantity — the UI states this explicitly ("business demand — separate from production").
- Each saved demand row carries an explicit **Release to production…** action. Releasing opens a confirmation flow that:
  1. confirms the release quantity;
  2. confirms the Route Mode — `FLOATING` (default, no Route required) or `PLANNED` with a selected Route (snapshot noted);
  3. confirms the configured starting Area and Operation;
  4. **warns when the PN already has active quantity**, showing the existing distribution and requiring explicit confirmation of intent — never auto-creating or auto-merging quantity;
  5. shows a **release summary before commit** (PN, quantity, Route, starting Area/Operation, Work Order Demand);
  6. on Confirm, reports the result: created Quantity Flow id, assigned Route, starting Area, quantity, and the appended `RECEIVED` Movement.
- Saving demand never triggers a release automatically; release is always a separate explicit action.

---

# 12. Changes from previous versions

> Historical entries in §12.5–§12.10 intentionally keep the vocabulary of the versions they describe (REWORK, temporary `TMP-…` Work Order Numbers, Machine sessions, Purchase Order / PO / PO Demand / PO Intake). Since v6 the canonical term is **Work Order** (§12.5 item 9); since v8 REWORK, temporary Work Order Numbers, Machine sessions, Action barcodes, and the Recent Scans list no longer exist — the older names appear below only as history.

## 12.1 Changes from GUI Design v10

The v10→v11 changes are Phase 2 presentation, navigation-chrome and copy refinements. No domain behavior, movement semantics, quantity rules, backend APIs or database design changed; everything remains implemented against development-only mock state.

1. **Scan Station production mode** (§4.1): the new route `/scan-station/:stationId/production` loads a station with the top application navigation hidden (no Production Board / Management / Administration links) so operators cannot casually leave the configured station; the mode is explicit in the router model (`mode: 'standard' | 'production'`) and the pathname — never a query parameter or hidden gesture — and is presentation only, never a security boundary. The OFFLINE banner, Worker Session, connectivity status (chip moves into the station header), all scan workflows and full-viewport height are preserved; the footer Station ID becomes plain text with a small non-interactive `Production mode` label; standard mode is unchanged and documents the `Ctrl+Shift+K` toggle (usable from the main barcode input, inert in other fields and dialogs).
2. **Station Selector cards** (§4.1): supported Operations render as individual light informational chips through the shared chip presentation (never comma-joined text), and every card offers the two clearly differentiated actions `Open` and `Open production mode` with accessible names carrying the Station ID.
3. **Production Board heartbeat** (§5): the Live dot pulses via the board-owned `pb-live-pulse` animation (no Scan Station CSS dependency), with a distinct solid-warning stale state and a `prefers-reduced-motion` fallback.
4. **Hot flame in the No. column** (§5): Hot rows render `1 🔥` in the No. column (accessible label `Row 1, Hot priority`); the PN cell carries only the PN and description — `🔥#n` no longer renders before the board PN (other views keep the shared `HotPn` presentation); the footer legend was updated.
5. **Intrinsic PN width** (§5): the fixed 310px minimum was replaced by a real `15ch` content minimum on the PN itself; longer PNs expand the column and are never truncated; horizontal cell padding was reduced and the flexible width distribution across PN / Areas / Due Date retained with a bounded Job Numbers share.
6. **Location row alignment** (§5): every Areas & Quantities row follows `Location | Quantity | State/activity | Time` with the time anchored to the right edge; location/quantity/state size to their widest content — full Machine names always visible (no ellipsis), long names expand the column minimum; External locations show only `External` with the activity (`plating`, `vendor`, …) as a light chip in the state position; the `MockLocationRow` model gained the explicit `activity` field (composite labels such as `External — Plating` no longer exist).
7. **Total row, separator and scrap chip** (§5): one continuous dashed separator element spans the complete location grid (no per-cell border fragments); the total row starts at the Location column with its quantity aligned to the quantity column; scrap renders as a `n scrapped` error-toned chip on the total line, anchored in the right-hand time position — no `⊘` symbol and no extra row.
8. **Manual board pagination** (§5): Previous/Next buttons and clickable page dots join the automatic rotation (manual navigation never wraps and restarts the rotation timer); `ArrowLeft`/`ArrowRight` navigate at window level with modifier chords ignored and modal dialogs blocking the shortcut.
9. **Priority two-snapshot confirmation** (§8): the reorder confirmation replaced the Current/New comparison table with `Current Position` → transition arrow → `New Position` snapshots restricted to the affected rank range; rank first, per-row ↑/↓ arrows only on the current side, PN visually separated from a `WO … · Job …` metadata chip built from explicit fields, and `Not listed` placeholders for entries that exist on only one side (Undo/Redo of add/remove); `MockHotEntry` gained the explicit `workOrderNumber` and `jobNumber` fields.
10. **Exhaustive professional copy + rendered-copy guard** (§3.10): all rendered strings were audited against the operator / audit-history / development-only classification; developer wording (`(mock)`, `nothing persisted`, `priority_rank`, `movement_reason`, `derived from Movement history`, `separate from Movement`, …) was rewritten in user language or moved behind the single per-view `DevNotice`; `src/rendered-copy.test.ts` now guards the renderable sources and the current mockup against regressions while keeping canonical Movement names legitimate in audit surfaces.

## 12.2 Changes from GUI Design v9

The v9→v10 changes introduce the Area-level processing completion state — user-facing **DONE**, canonical immutable Movement `AREA_COMPLETED`, derived holding state `READY_TO_TRANSFER` (canonical in PROJECT_PROFILE v10 §7 *Area Completion*, §8.11, §12) — plus Phase 2 presentation refinements across the views. Everything is implemented in the Phase 2 frontend against development-only mock state; no backend persistence and no database migrations exist yet.

1. **Area completion — DONE** (§4.6, §4.10; PROJECT_PROFILE §7/§8.11/§12): Machine-card PN rows carry the two distinct actions `DONE` (complete Area processing → `AREA_COMPLETED` → `READY_TO_TRANSFER`, success tone, accessible name `Complete Area processing`) and `QUEUE` (return unfinished quantity → `RELEASED_FROM_MACHINE`, accessible name `Return to Area queue`), icon-above-label in the shared action rail; direct-processing Areas complete through the PN action dialog's `Complete processing — DONE` choice. The manual DONE wizard (quantity with MAX = source-position quantity → dedicated confirmation with Action `Complete Area processing`, Result `Finished — ready to move`, `Confirm DONE`) records one immutable `AREA_COMPLETED`; the quantity leaves the Machine card, joins the Area summary's new `Finished — ready to move` group, the Machine clears, and the Area stays the location. DONE is quantity-scoped — never a PN status, never `STOCKED`.
2. **Implicit source completion during transfer** (§4.7): a transfer whose quantity is still actively processing at the source appends `AREA_COMPLETED`, then `TRANSFERRED` as one atomic application command (confirmation rows `Source processing` and `Recorded events … one atomic operation`); a `READY_TO_TRANSFER` source transfers with `TRANSFERRED` alone; whole-command Undo reverses both together; no fake route visit is created for the finished rack.
3. **Keyboard-wedge capture** (§4.4): with no dialog open, scans reach the main barcode input without DOM focus — first character preserved, Enter submits exactly once; typing in other fields/dialogs, modifier shortcuts, and button activation are never intercepted.
4. **Scan Station header restructure** (§4.3): explicit grid — identity group and Worker Session always share the main row, totals drop to a full-width evenly divided second row when space runs out (container query); Operations render as light informational chips; the header totals add **Done**, reconcile (`Total pcs = Queued + On machines + Done`, or `Processing + Done`), and use the shared semantic tones (Queued warning, On machines/Processing info, Done success, Hot error).
5. **`In this Area now` card slimmed** (§4.10): the statistics block is removed (the header is the single totals surface; the Area Board keeps its stats through `showStats`); the Area description renders compactly in the card header block; groups renamed/extended to `On Machines` / `Area queue — awaiting Machine` / `In processing` / `Finished — ready to move`; Machine-card rows stop repeating the Machine name via the explicit `showContext` presentation option; the single-column fallback became container-aware.
6. **Production Board location model** (§5): explicit per-location presentation data (Area label, separate optional Machine, quantity, state, time — never one parsed display string); active Machine quantity renders as an Area dot + compact Machine chip + quantity + the exact state `on mch.` + time; DONE quantity shows the Area with a `done` state (completing Machine as tooltip context only); scrap moved to its own separated line under the total row; the PN column gained an enforced minimum width for the standard 15-character PN; remaining width is distributed across PN / Areas / Due Date instead of Job Numbers.
7. **Area Board unification** (§6): the All Areas overview reuses the shared PN-row shell and subcomponents with aggregated portion chips (`Lathe 3 × 3`, `queue × 2`, `done × 1`) and a Done overview stat; the detail summary distinguishes queued / on-Machine / processing / finished; finished quantity never renders inside a Machine card and never as Stocked.
8. **Tracking** (§7): route steps and arrows are separate sibling flex items (arrows never overlap steps); `AREA_COMPLETED` appears color-coded in the immutable Movement history; ready-to-transfer state renders with operator wording (`Completed processing at Lathe — ready to transfer`), distinct from `STOCKED`, with no extra route step for the finished rack.
9. **Work Order Details modal** (§11.2): the WO detail panel became the `Work Order Details` modal dialog over the always-mounted list (URL unchanged, `Save demand` / `Cancel (Esc)`, dirty-close discard confirmation, focus restored to the opening row, child dialogs stack without breaking Escape/backdrop/focus).
10. **Quantity keypad CSS ownership** (§11.3): `QuantityKeypad` imports its own `QuantityKeypad.css` (moved verbatim out of scan-station.css), so Work Orders renders the keypad correctly without visiting the Scan Station first.
11. **Priority reorder confirmation** (§8): redesigned around a primary moved-item summary, a current-versus-proposed rank comparison with moved/shifted emphasis and ↑/↓ indicators, `Apply ranking` / `Cancel (Esc)`, and user-facing Undo/Redo titles (`Restore previous ranking` / `Reapply ranking`).
12. **Professional operator copy** (§3.10): operator guidance stopped exposing raw enum combinations (canonical types remain as audit data and in the confirmation summaries' `Recorded event(s)` row); Cancel buttons standardized to exactly `Cancel (Esc)`; repetitive mock/persistence phrasing reduced to concise development-only notices; demo barcode hints marked development-only and guarded out of production builds.

## 12.3 Changes from GUI Design v8

All v8→v9 changes are Scan Station interaction and presentation refinements, implemented in the Phase 2 frontend against development-only mock state. No domain behavior, movement semantics, quantity rules, backend APIs or database design changed; the one-shot rule is preserved — every multi-step workflow remains one temporary dialog with no armed state.

1. **Dedicated confirmation views for every production action** (§4.6): each one-shot action (intake, Machine assignment, transfer, add quantity, repair, scrap, queue return) is a temporary wizard inside one modal — data entry views followed by a separate structured confirmation summary (two-column term/value layout, operation-naming Confirm buttons such as `Confirm intake` / `Confirm assignment`); the inline dashed `Summary:` note inside entry views is removed; nothing is recorded before the final confirmation; Back preserves entered values; Escape cancels the whole workflow; the Undo confirmation shares the same summary presentation.
2. **Three-step intake wizard** (§4.7): Step 1 settings (Request Type, Route Mode, Planned Route, due date, starting Area/Operation, notes, Work Order reuse behavior — no quantity), Step 2 quantity with a recap of the settings and the no-default guidance, Step 3 structured confirmation with `Confirm intake` as the only write point.
3. **Three-step Machine assignment wizard** (§4.6): Step 1 selects Machine and queued PN — explicit buttons up to 6 options, a compact accessible dropdown above 6 (no new dependency), plus a dedicated Step-1 barcode input accepting this Area's Machine barcodes and queued PN barcodes with inline errors for every invalid scan; Machine-first, PN-first (Back returns to the action dialog) and manual selection entry points preserved; Step 2 quantity (MAX = queued default); Step 3 confirmation with remaining-queued-after-assignment; `Confirm assignment` is the only write point and nothing stays armed afterwards.
4. **Quantity-step guidance and Enter semantics** (§4.8): fixed step order PN → context → guidance/validation → input → numpad → buttons; semantic guidance text directly above the input (neutral/info/warn/error — no framed box); validation updates near the input with Next disabled while invalid; Enter on a quantity step advances to the confirmation view (never directly to a write); Enter on the confirmation view performs the final Confirm.
5. **Scan Barcode card without an ENTER button** (§4.4): the scanner submits with Enter and the placeholder says so (`Scan PN / Worker / Machine barcode… (ENTER)`); **⌨ Enter PN manually** moves into the scan row as the secondary action (the separate manual-entry row is removed; the fallback explanation stays as a small hint).
6. **Floating scan notification** (§3.3, §4.4): the permanently reserved feedback block is removed; feedback is one closable floating notification (success/neutral ~4 s, warning/error ~8 s, timer reset on replacement, semantic styling, alert/status live-region semantics, viewport-safe on narrow screens); the persistent OFFLINE banner is unchanged and is not a notification; `Last scanned PN` stays fixed in the card.
7. **PN row grid layout** (§4.10): four content lines (Hot+PN | context · quantity `pcs`; WO·Job — truncating with a `title` tooltip | due status; in-Area status | time in Area; `{n} scrapped`), with the action in a dedicated, visually separated right-side action rail (stacking below only at very narrow widths); the compact `⊘n` scrap indicator is removed — scrapped quantity renders only as `{n} scrapped` text; rows are never clickable as a whole.
8. **Row-action visibility** (§4.6, §4.10): `In this Area now` shows no `ASSIGN`/`QUEUE` row buttons; Machine cards show only `QUEUE`; Machine assignment stays available through PN scan, Machine scan and the action dialog; the Area Board remains completely read-only. The underlying one-shot assignment capability is unchanged.

## 12.4 Changes from GUI Design v7

All v7→v8 changes are implemented in the Phase 2 frontend against development-only mock state; the corresponding domain rules are canonical in PROJECT_PROFILE v9.

1. **PN-centric one-shot Scan Station, no Machine Session** (§4): the Machine session pill, Machine status strip, persistent active-Machine state, armed Action barcodes (`PF:ACTION:…`) and the Recent Scans list are removed. Scanning a Machine barcode is only a one-shot assignment shortcut; scanning a PN opens the applicable one-shot dialog (intake / source-explicit transfer / action dialog with only currently valid choices); completing or cancelling a dialog clears every temporary context. The header carries Area statistics instead of Machine-session state; the Scan Barcode card spans the full width directly under the header; the Last Scanned PN surface carries the Undo action at its right edge with a summary confirmation before every reversal.
2. **Scan Station routing** (§4.1): `/scan-station` becomes a Station Selector (never auto-redirecting); `/scan-station/:stationId` loads one station; unknown/inactive Station IDs show an explicit error; the faint footer Station ID is subtly clickable for switching.
3. **Request Types reduce to `NEW` / `MODIFY`; Repair becomes a movement intent** (PROJECT_PROFILE §7, §14): REWORK is removed everywhere; MODIFY intake creates/reuses an internal blank-number Work Order (`work_order_number = NULL`, displayed `—`); Repair is an explicit `TRANSFERRED · movement_reason REPAIR` chosen by the user (“Send quantity here for repair”), never inferred from route history and never new demand.
4. **Floating Routes by default** (PROJECT_PROFILE §7, §17): every Quantity Flow carries `route_mode` (`FLOATING` default / `PLANNED`); AssignedRoute snapshots exist only for Planned flows; Tracking shows the route-mode badge and derives Floating actual traces from Movement history with repeated Areas and explicit `⟲ REPAIR` markers (§7.2).
5. **Temporary Work Order Numbers removed** (§11; PROJECT_PROFILE §7): a blank WO Number saves `NULL`, renders `—` (never persisted as a placeholder), non-null numbers stay unique, and the real number can be added later through an audited edit.
6. **PN barcodes carry the PN; create-on-first-use** (PROJECT_PROFILE §8.1, §10): `PF:PN:<part-number>` with the entire non-empty suffix as the PN — no opaque stable-id mapping, no format validation, no preloaded catalog requirement; PN identity is case-insensitive with the first-entered casing preserved; manual PN entry accepts any non-empty value.
7. **Scrap workflow and scrap visibility** (§4.9, §5, §6, §7): canonical `SCRAPPED` Movement; context-sensitive `PF:SCRAP` counting barcode (rejected in the main input); pending count with correction/reset; mandatory common reason; one auditable operation per confirmation; scrapped quantity visible on Scan Station, Production Board (`⊘ n scrapped` on the total line), Area Board summaries and Tracking (list column + scrap history section).
8. **Auditable quantity addition** (§4.7): “Add more quantity” is operator-allowed with a mandatory reason, no MAX and no default, summarized and confirmed, represented as `QUANTITY_ADJUSTED · direction INCREASE` — never an ordinary transfer, never changing requested quantity.
9. **Two Area modes only** (PROJECT_PROFILE §12): no Machines → direct Area processing (`Machine = NULL`, no queue, no placeholder cards, no zero-value statistics); one or more Machines → `QUEUE_AND_ASSIGN` with one-shot assignment (`ASSIGN` on queued rows, `QUEUE` on Machine rows); single-Machine auto-assignment is removed.
10. **Shared Area/Machine monitoring layout** (§4.10, §6.3): Scan Station and Area Board detail render the same `[ In this Area now | Machine cards grid ]` structure from shared components (`AreaPnRow`, `AreaSummaryCard`, `MachineMonitoringCard`, `DueStatus`, `QuantityStatus`); Area Board stays read-only; Machine cards wrap only within the right-side grid; no-Machine Areas render the full-width summary card; cards use the shared `var(--shadow)` token.
11. **Quantity keypad keyboard fix** (§4.8): a real focusable numeric input opens focused (`inputMode="numeric"`); keys are handled centrally (0–9, Backspace, Delete, Enter=Confirm, Escape=Cancel, Space ignored); keypad buttons are `type="button"`, non-focusable, and never re-activate on Space/Enter; MAX exists (and is the default) only for transfer/assignment.
12. **Administrative archival/purge specified** (§9; PROJECT_PROFILE §28): PN archive (soft-delete) with `(archived)` markers and preserved historical text; Admin-only history archival & purge maintenance with preview, reason, and audit; archive preferred over purge; retention settings in Administration. Phase 2 carries these as specification placeholders only.

## 12.5 Changes from GUI Design v6

All v6→v7 changes are implemented in the Phase 2 frontend against development-only mock state; the corresponding domain rules are canonical in PROJECT_PROFILE v8.

1. **Fast connectivity detection with a strict write-confirmation rule** (§3.6): browser `online`/`offline` events plus ~1 s polling of `GET /api/health` (request timeout below the probe interval, no overlapping probes), recheck on tab focus/visibility, no "connecting" flicker from passive probes, and Scan Station input re-enable and refocus on recovery. A scan is successful only after the server confirms the write — connectivity status is an early warning, never permission for optimistic recording; a write that cannot reach the server fails as "nothing recorded". WebSocket/SSE remain out of scope for Phase 2.
2. **Missing due dates are valid data** (§3.9, §11; PROJECT_PROFILE v8 §8.2/§8.3): WorkOrder and demand-line due dates may be absent, rendered as `No due date` / `—`; "missing due date" is removed from the validation error list; ordering everywhere follows the canonical demand ordering (PROJECT_PROFILE §18 — dated earliest-first, undated after all dated demand by Work Order received date, stable tie-breaker).
3. **Work Orders becomes manual-first with optional identifiers** (§11): WO Number and WO due date are both optional; a blank WO Number generates a temporary internal `TMP-YYYYMMDD-HHMMSS` number on confirmed save; **＋ Add Part manually** is the primary workflow through one accessible multi-step Add Part dialog (PN → quantity → due date with explicit "No due date" → optional metadata, backward navigation preserving values); barcode scanning is demoted to a secondary optional method (the Scan Station remains scanner-first); Save demand shows an omission-summary confirmation before saving. The modal-over-list architecture and URL behavior of v6 are unchanged; no new route is added.
4. **Scan Station refinements** (§4): the Station ID leaves the header and becomes a faint bottom-edge diagnostic caption ("Station LATHE-ST-01"); manual PN entry becomes a visible secondary action **⌨ Enter PN manually** with an explanatory caption; recent scans carry the Movement type as an explicit field (`movementType: MovementType` in the mock model) rendered as its own badge plus a recorded/reversed status — never embedded at the start of description strings; the quantity dialog gains full physical-keyboard support (digits, Backspace, Delete/Clear, Enter, Escape).
5. **Production Board hardening** (§5): live local date-and-time header clock (self-updating component, no static mock clock); standard Hot Part presentation `🔥#1 <PN>` via one shared component (`HotPn`, `src/components/indicators.tsx`) used on Production Board, Area Board, Tracking and Priority; only the due-date urgency text blinks (PN and date stay steady; `prefers-reduced-motion` honored; footer legend updated); stable Areas & Quantities grid with vertically aligned quantities; column order moves Job Numbers last (No. · Part Number · Areas & Quantities · Time · Due Date · Total Days · Job Numbers) with content-driven `colgroup` sizing; **dynamic pagination** measured from actual board and row heights replaces the former fixed rows-per-page assumption (12 s rotation only when more than one page exists; 10-row fallback in layout-less DOM environments).
6. **Area Board per-Area detail redesigned** (§6.3): the "one card per PN" grid is replaced by an Area summary card (name, description, Operations, stats, grouped compact PN list — Assigned to Machines / Area queue / Stocked for terminal Stockroom) followed by one monitoring card per Machine (running / idle / maintenance with distinct empty and maintenance presentations, from the `MOCK_AREA_MACHINES` mock model); Areas without Machines render only the summary card; "Sort: Time in Area" works via the sortable `timeInAreaMinutes` duration field, longest first.
7. **Priority reordering requires confirmation** (§8; PROJECT_PROFILE v8 §21): drag-and-drop, Move Up, Move Down, Undo and Redo confirm before applying — showing affected PN + Work Order Demand, previous rank, proposed new rank and action type; Cancel leaves the list and both histories unchanged and the visible list is never renumbered early; adding at the bottom stays direct; the existing remove confirmation is unchanged.

## 12.6 Changes from GUI Design v5

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

## 12.7 Changes from GUI Design v4

1. **Global Dark/Light theme mode** (§2.1): a user-facing toggle in the top navigation switches the entire application between Dark and Light; **every view follows the selected mode**, replacing the fixed per-view themes of v2–v4. Dark remains the default (shop-floor first). All component styling was moved to semantic tokens with per-theme values; status *text* colors have per-theme variants for contrast, while status tints and Area identity colors are shared. Theme persistence (per user / per station) is an open decision (§14). This removes "dark/light user toggle" from the deferred list (§13).
2. **Area Board card layout hardening** (§6.3): the quantity block is anchored to the card's right edge independent of PN length; an over-long PN truncates with an ellipsis and shows the full identifier in a hover tooltip. The same truncation applies to the All Areas overview PN lists. §2.3's single-line PN rule was amended accordingly.

## 12.8 Changes from GUI Design v3

1. **Manager Summary merged into Area Board.** The Area-column overview becomes the **All Areas** overview — the first tab of the Area Board tab strip and its default mode (§6.2). The "Manager Summary" name is retired; overview column headers open the per-Area detail. Management sub views reduce to Area Board · Tracking · Purchase Orders · Priority. No §21 content is dropped — only its placement changed.
2. **Area Board returns to the dark theme** (as in v2), including the All Areas overview: it is a monitoring surface rather than desk paperwork, and the light v3 variant proved hard to read (§2.1). The Management sub-view bar follows the active sub view's theme. Dark now covers Scan Station, Production Board and Area Board; Tracking, Purchase Orders, Priority and Administration stay light.
3. **PO Intake renamed Purchase Orders** and restructured as PO list → PO detail → New PO (§11): the view opens with the list of POs, demand lines appear only after selecting a PO, and a dedicated **scanner-first New PO** flow adds one demand line per PN barcode scan (Request Type defaults to `NEW`, quantity typed immediately after each scan).
4. **PO-level due date** introduced as the default for each demand line's due date, editable per line (§11.2/§11.3). Requires PurchaseOrder.`due_date` — pending PROJECT_PROFILE §8.2 and §21 alignment (§1).
5. **Section renumbering:** former §7 Manager Summary removed; later sections shift up by one (Tracking §7 … Open Questions §14).

## 12.9 Changes from GUI Design v2

1. **Navigation regrouped.** Area Board, Manager Summary, Tracking, PO Intake and Priority Management become **sub views of a single Management view** (§1.1). Top-level navigation is reduced to Scan Station · Production Board · Management · Administration. Management remembers its last-used sub view.
2. **"Shop floor" navigation group label removed.** In v2 it was a nav group heading only (never a view); with only two shop-floor views left at top level the label adds nothing.
3. **Area Board and Manager Summary move to the light Management context.** §2.1's context table now assigns dark exclusively to Scan Station and Production Board. Both views keep their layout and behavior; only the theme changes so the entire Management view is visually consistent.
4. **Realigned to PROJECT_PROFILE v6** (was v5): allocation and Hot work ordering use two business criteria only — ① priority rank ② earliest due date — with the deterministic tie-breaker demoted to an implementation detail; Operators may review and adjust the suggested PO Allocation before confirmation (no longer role-gated); PROJECT_PROFILE section references follow the v6 renumbering (Barcode Model §10, Quantity Model §11, …, Application Views §21, Remaining Open Decisions §32).

## 12.10 Changes from GUI Design v1

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
4. Worker session expiration values and their visual countdown — policy values live in Administration; defaults TBD (PROJECT_PROFILE §32.2). (Machine sessions no longer exist — v8.)
5. Should the Production Board offer a per-Area filtered mode, or is that fully covered by the Area Board (All Areas overview / per-Area detail)?
6. Whether Undo on the Scan Station requires Worker identity when Worker scanning is enabled for the Area.
