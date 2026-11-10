# PartFlow GUI Design v18

> **Language:** English is the source of truth. [Tiếng Việt](./GUI_DESIGN.vi.md).

> **Status:** Current. This document is the approved target user interface (UI) of PartFlow.

## About this document

This document describes how every PartFlow screen looks and behaves: layouts, states, the exact on-screen wording,
keyboard and barcode-scanner behaviour, design tokens, screen-width breakpoints and accessibility rules. It is written
for anyone who designs, builds, reviews or tests the PartFlow frontend, and for anyone who needs to know what a screen
is supposed to show.

It is one of several canonical documents, each with its own job:

- [PROJECT_PROFILE.md](./PROJECT_PROFILE.md) owns the domain: terminology, business rules, invariants, workflows and
  product scope. This document does not redefine those rules; where a screen depends on one, it points to the
  PROJECT_PROFILE section (written "PROJECT_PROFILE §n").
- This document (GUI_DESIGN) owns the approved UI.
- [IMPLEMENTATION_ROADMAP.md](./IMPLEMENTATION_ROADMAP.md) owns the implementation order and the current
  implementation state, including any temporary limitation of a screen.
- Slice documents (for example [SLICE1_DATA_MODEL.md](./SLICE1_DATA_MODEL.md)) are subordinate implementation detail.
- [DEPLOYMENT.md](./DEPLOYMENT.md) owns deployment.

An interactive mockup accompanies this document: `docs/mockups/partflow-gui-mockup-v18.html`. Older mockups are
archived under `docs/archive/`. The mockup is a visual reference; where it and this text differ, this text wins. The
mockup follows the PROJECT_PROFILE Part Number identity model: the canonical Part Number is uppercase, trimmed and has
no internal whitespace, and the Part Number master record is optional metadata that can be hard-deleted.

Source code cites this document by section number (for example `GUI_DESIGN §4.7`), so the numbered headings keep their
numbers. The version history is the change table at the end of the document.

**Abbreviations used throughout.** PN means Part Number. WO means Work Order. Other abbreviations are spelled out where
they first appear.

---

# 1. Scope

This document covers the application views listed in PROJECT_PROFILE §21, organized as **ten GUI views**:

1. Scan Station (production).
2. Production Board (monitoring on a large display).
3. Area Board (monitoring: the **All Areas** overview plus a detail view per Area; the overview carries the content
   that PROJECT_PROFILE §21 calls *Manager Summary*).
4. Machines (management of the Machine lifecycle and maintenance).
5. Tracking (management, organized around the Part Number; the label operators see is **PN Tracking**).
6. Work Orders (management: manual Work Order entry, file import and production release; PROJECT_PROFILE §21
   *Work Orders*).
7. Planned Routes (management of reusable route definitions).
8. Part Numbers (management of the PartNumber master metadata and PN barcode labels; PROJECT_PROFILE §21
   *Part Numbers*).
9. Priority Management (ranking of Hot Work Order Demand).
10. Administration (configuration).

## 1.1 Navigation structure

The top navigation has four entries. The management views are grouped as **sub views of one Management view**:

| Top level        | Contains                                                                                       |
|------------------|------------------------------------------------------------------------------------------------|
| Scan Station     | — (single view)                                                                                |
| Production Board | — (single view)                                                                                |
| Management       | Area Board · Work Orders · PN Tracking · Priority · Planned Routes · Part Numbers · Machines   |
| Administration   | — (single view, with its own sidebar navigation as described in §9)                            |

Selecting Management opens its **last-used sub view** when the user may open it. Otherwise it opens the first sub
view in the bar that the user may open (Area Board on first open for a user who may open it; Area Board itself opens on
its All Areas overview). Management also reveals a secondary sub-view bar beneath the top navigation. In the sub-view
bar **Part Numbers sits next to last and Machines last, directly after it**.

The sub-view bar sits inside the scrolling content area and sticks to its top as a frosted-glass bar on its own
translucent panel tone: scrolled view content passes beneath it and shows through faintly. The bar condenses while the
content scrolls down and is restored on the first upward scroll or near the top. The persistent OFFLINE banner (§3
rule 6) stays outside the scrolling area, above it.

All navigation chrome follows the global theme mode (§2.1). The grouping is navigation only: each sub view keeps its
own specification (§6–§8, §11–§14).

Machines (`/management/machines`), Planned Routes (`/management/planned-routes`) and Part Numbers
(`/management/part-numbers`) are **production master data** sub views. They are operational management functions,
opened according to permissions by authorized production roles (Production Manager, Process Engineer, Maintenance
Manager, or another authorized specialist; PROJECT_PROFILE §20). They are deliberately grouped under Management rather
than Administration.

### Access to Management

Management requires a signed-in user (§1.2).

- **Signed out.** Entering Management while signed out opens the sign-in dialog over a sign-in panel.
- **Expired sign-in.** An expired sign-in keeps the open screen and its work for the same user; if a different user
  signs in, the screen starts fresh. Meanwhile the live views (Area Board, PN Tracking) stop refreshing. Closing the
  sign-in dialog keeps it closed. The live views refresh again once the same user signs in.
- **Which sub views open.** Each sub view opens for users who hold **View all current and historical production data**
  or a permission that one of its actions uses:

  | Sub view       | Opens with View all current and historical production data, or also with …                     |
  |----------------|--------------------------------------------------------------------------------------------------|
  | Area Board     | (only the former)                                                                                |
  | Work Orders    | Create and edit Work Orders, Edit Work Order Demand, or Edit Work Order Allocation                |
  | PN Tracking    | Edit Work Order Allocation or Assign Routes                                                      |
  | Priority       | Set Work Order Demand priority or Reorder Hot items                                              |
  | Planned Routes | Manage Planned Routes                                                                            |
  | Part Numbers   | Manage Part Numbers                                                                              |
  | Machines       | Manage Machines                                                                                  |

- **The sub-view bar** lists only the sub views the signed-in user may open. A sub view the user cannot open (for
  example, reached through a link) shows which permissions open it.
- **Controls** for changes the user may not make are hidden, not disabled. When the user may change nothing in the
  view, a `View only` note names the permission. Values that are already loaded stay visible as text.
- The Part Number details dialog opens read-only for a user who may not manage Part Numbers; the barcode label stays
  reachable (§11.2, §14.2).
- Priority's Undo and Redo appear for users who may change the Hot list.
- Scan Stations and the Production Board never ask for sign-in.
- The server checks every Management read and write itself; what the screen hides is presentation only.

## 1.2 Signed-in User

Application Users sign in with a login name and a password. Administration and Management require sign-in. Scan
Stations and the Production Board never ask for sign-in; a Scan Station is used from an enrolled device instead
(§4.13).

- **Account chip.** The top navigation ends with an account chip. When nobody is signed in it shows `Sign in` (and
  `Set up PartFlow` while PartFlow has no Administrator yet). Otherwise it shows the avatar and name, with a menu
  holding `Change password…` and `Sign out`. The chip is absent wherever the top navigation is hidden: Scan Station
  production mode and the Production Board kiosk.
- **Sign-in is a modal dialog over the current view.** The route and any open work stay as they are. An expired
  sign-in reopens the sign-in dialog over the current view. The exception is a theme save that finds the sign-in
  ended (§2.1): the dialog then opens at the next refused request.
- **Replacing an administrator-set password.** While a password set by an administrator must be replaced
  (Administration → Settings → User sign-in, §9), the `Choose a new password` dialog cannot be dismissed except by
  signing out.
- **Set up PartFlow.** The first-run dialog is available only while no Administrator exists. It asks for the setup
  token printed in the server log and creates the first Administrator.
- **Who is never asked.** Scan Stations and the Production Board never ask for sign-in. Workers are identified by
  badge, never by User sign-in (§4.12).
- **Hidden, not disabled.** Controls a User may not use are hidden, not disabled.
- **Connectivity.** All sign-in actions follow the offline write block (§3 rule 6).

---

# 2. Design System

## 2.1 One token set, two switchable themes

The application has a single global **theme mode, Dark or Light, that the user can switch**. **Every view follows the
selected mode**; no view has a fixed theme.

- **The toggle** lives in the top navigation bar and applies to the whole application instantly, including navigation
  chrome, dialogs and toasts. In Scan Station **production mode**, where the top navigation is hidden, a compact
  **borderless** variant of the same shared control renders in the station header's actions group **directly below
  the connectivity chip**, in a compact vertical, right-aligned stack (§4.1). It uses the same global theme state
  (`aria-pressed`, visible focus state). The selected theme is preserved when switching between the standard and the
  production routes. The Production Board **Kiosk mode** header (§5.1) reuses the same compact borderless control.
- **Dark is the default.** PartFlow is shop-floor first: a dark theme reduces glare in the shop, is readable from a
  distance and tolerates low-quality displays. Desk users who prefer a light theme for dense data work switch once.
- **Semantic tokens only.** All component styling uses semantic tokens (background, panel, border, text, muted,
  status-text, …); the two theme definitions supply the values. Adding or restyling a component never hard-codes a
  theme-specific color.
- **Status colors** (§2.2) keep per-theme *text* variants so contrast holds in both modes (for example, success text is
  bright on dark and deepened on light). Status backgrounds and tints are shared. Area identity colors are identical in
  both themes.

### Theme persistence

The theme choice persists **per User and per Scan Station** and is resolved in a fixed order of precedence:

1. the signed-in User's preference;
2. the Scan Station's preference;
3. the Dark default.

A Worker Session never affects the theme. Worker badges are shop-floor audit identity, not application accounts
(§4.12), so badge sign-in, badge sign-out and session expiration leave the theme unchanged.

**Scan Station preference.** On `/scan-station/<id>` and its production route, the station's saved preference applies
when the station loads (no saved preference means Dark). The toggle there saves the preference for that station while
the station is loaded and connected. The change applies to the current browser session only, and nothing is queued,
when the station is offline, when the station cannot be loaded, or when the save is not confirmed; in the last case a
warning notice says so and explains how to save again. While nobody is signed in, other routes keep the choice for the
browser session.

**Which preference the toggle saves.**

- While a User is signed in, that User's saved preference applies on every route, and the toggle saves the User's
  preference on every route, including Scan Station routes and the Kiosk, never the Scan Station's.
- The Scan Station's preference is saved by its toggle only while that browser is known to have nobody signed in.
  While the sign-in state is not yet known, the change applies to the current browser session only.
- Signing out, or a sign-in that has ended, returns the screen to the Scan Station preference on station routes and to
  Dark elsewhere. A theme save never opens the Sign-in dialog.
- When the browser is offline, while a password change is required, or when the save is not confirmed, the change
  applies to the current browser session only and nothing is queued. An unconfirmed save or an ended sign-in shows a
  warning that says what to do: on a Scan Station, its floating warning notice; elsewhere, a toast.
- No Administration screen edits a User's stored theme preference; only the toggle sets it.
- Changing the theme is never audited.

Both themes share the same color tokens, spacing scale and typography, so the product feels like one system in either
mode.

## 2.2 Color tokens

Status colors are semantic, never decorative:

| Status         | Color     | Meaning                                                   |
|----------------|-----------|-----------------------------------------------------------|
| **Success**    | `#31d287` | Recorded Movement, confirmed action                        |
| **Warning**    | `#ffb224` | Needs attention, pending context, route deviation, due soon |
| **Error**      | `#ff6166` | Rejected scan, integrity violation, overdue               |
| **Info / accent** | `#4f8cff` | Selection, focus, primary action                       |

**Area colors.** Every Area has a stable identity color, used consistently in **all** views (chips, dots, distribution
bars, column headers). Colors are Area display properties and can be edited in Administration without affecting
history (PROJECT_PROFILE §7 Area).

Initial palette: Material `#8b93a8`, Cut `#f5b83d`, Lathe `#3da5ff`, Mill `#9b6ef3`, Manual `#e06fae`, Deburr
`#2fbf9b`, External `#ff8a4c`, Stockroom `#2fca7c`.

## 2.3 Typography

- **UI text** uses the system font stack (`system-ui, Segoe UI, Roboto…`): no web-font dependency, and it works
  offline.
- **Identifiers** (PN, Work Order Number, temporary Work Order, external Job Number, Quantity Flow id, barcode values,
  quantities, timestamps) use a monospace font. Identifiers must be visually distinct from prose because operators read
  them against paper travelers and folder labels.
- **Shop-floor minimum sizes:** body 16 px, PN ≥ 19 px, quantities ≥ 18 px bold. The Production Board is sized for
  reading at 3–5 m (PN 22 px or more, key figures 18 px or more).
- **The PN never wraps.** It is always rendered on a single line. Where the container cannot grow with the identifier
  (Area Board cards and overview lists), an over-long PN is **truncated with an ellipsis** and the full PN is shown in
  a tooltip on hover; layout-critical figures (quantities) must never move because of PN length. Views sized around
  the PN (Production Board, Tracking) keep sizing the column to fit it.
- **Realistic identifier and description shapes** (PROJECT_PROFILE §8.1):
  - PNs are opaque strings, commonly multi-segment, hyphenated and numeric, of varying length (from short `214-406`
    through long `0455-20-0118-03` and longer). PNs always display in their canonical uppercase form, which is exactly
    the stored identity. The UI preserves the exact string, never derives meaning from its segments, and search and
    filtering match the hyphenated value and its punctuation literally.
  - Work Order Numbers and external Job Numbers are likewise opaque strings, commonly numeric-looking (Work Order
    Number `007482`, Job Number `18427`). They are never parsed, padded or reformatted. The two are separate
    identifiers and are never combined.
  - Revision is a separate optional informational field (`A`, `B`, `E`, or blank). It is never appended to the PN, and
    a blank Revision renders cleanly without placeholder noise.
  - Part names and descriptions are free text, often uppercase with commas, slashes, fractions, dimensions and
    manufacturing abbreviations, and **may wrap naturally to two or three lines**. Description wrapping must never shift
    quantity, status, date or action columns: cap the description's width instead of widening the identifier column.

## 2.4 Touch and scanner ergonomics

- **Touch targets.** Touch-target sizing is a Scan Station and shop-floor requirement, not a global application rule.
  Interactive controls that an operator actually operates by touch on the Scan Station (and on other shop-floor touch
  surfaces) have a minimum 48×48 px touch target (`--touch-min`). Primary Scan Station actions are at least 56 px tall
  (`--touch-primary`). Desktop-oriented management and administration views keep normal desktop control sizing.
- **Scan or tap.** Every production action is reachable by a scan or a single tap. A mouse is never required on
  shop-floor views.
- **Keyboard wedge support.** The scan input is a plain text input terminated by Enter: no custom driver and no
  scan-mode selection (PROJECT_PROFILE §10 Barcode Model). A keyboard-wedge scanner is a barcode scanner that types
  the decoded value as keystrokes.

## 2.5 Small screens — vertical-scroll-first

Every view supports phone-width viewports (about 390 px and wider). The rules are:

- **Vertical scrolling is the only browsing gesture.** Every view must be fully browsable by vertical scrolling alone;
  the document never overflows horizontally on any supported viewport width.
- **Navigation adapts.** On phone widths:
  - the top application navigation collapses behind an explicit menu button (`aria-expanded`); the navigation links
    open as a vertical panel under the bar and close by navigating, by Escape, or by a tap outside;
  - the development sample-data tag is hidden;
  - the Dark/Light control reduces to its icon; the mode word stays visually hidden, never removed, so the accessible
    name is unchanged;
  - the connectivity status chip stays visible at every width.

  The Management sub-view navigation is ONE swipeable row at every width and never wraps onto a second row. Wherever
  the links do not fit, the row pans horizontally inside its own region with no visible scrollbar, and the active sub
  view is brought into view. On wider viewports the top navigation renders inline and wraps when needed.
- **Wide desktop tables shed columns first, then compact.** When the viewport cannot hold a table's desktop column
  layout, its lowest-priority columns hide first (the Machines Asset column, then Assigned now). When even the
  remaining columns cannot fit, the table compacts:
  - PN Tracking, the Tracking detail overlay's WO Demand table, the Work Orders list, Planned Routes, the
    Administration Areas table and the retired Machines table collapse to a stacked presentation: the header row is
    hidden, each row becomes one vertical block, and cells whose meaning is not self-evident carry their column label
    as a small inline caption;
  - the active Machines list instead keeps each Machine on one line that wraps only when it genuinely cannot hold the
    cells.

  Column sorting is a wide-layout convenience and is unavailable while a table is collapsed; whole-row activation and
  in-row controls keep working unchanged. Collapse breakpoints derive from each table's real content width, not from
  one shared device breakpoint.
- **Toolbars keep their primary action on the search row.** On Machines, `+ New Machine` never drops below the search
  field: the field shrinks into the remaining width, and the button label wraps onto two lines when needed.
- **The Scan Station scan row and header adapt without losing scan priority.** The **⌨ Enter PN manually** button
  compresses to roughly half its natural width (its label wrapping onto two lines) before the barcode input gives up
  width. The header's Operations row keeps one line and sheds presentation in tiers measured against the available
  space (label dropped, trailing chips behind one `…` chip, then hidden) instead of wrapping (§4.3, §4.4).
- **The Area Board becomes a paged per-Area detail view at phone and tablet widths.** The tab strip (`All Areas` plus
  one tab per Area) is hidden entirely, and a **`Summary` slide toggle (default off)** replaces `Wrap columns`.
  - Summary **off** pages through the **per-Area details**: one shared detail layout (§6.3) per page in a swipeable
    snap carousel, with no visible scrollbar, each page slightly narrower than the viewport so the neighbors peek in,
    Area-colored page dots above the pages (indicator and direct jump targets), and floating `‹` / `›` edge buttons
    pinned to the middle of the viewport. Paging never wraps.
  - Summary **on** stacks the All Areas overview (§6.2) as a single full-width vertical column list; a card header
    there jumps straight to that Area's detail page.

  The tabbed presentation with the `Wrap columns` toggle remains the desktop and wide presentation.
- **Deliberate horizontal panning is a navigation tool, never an accident of layout.** Exactly three sanctioned
  horizontal regions exist:
  1. the All Areas board on desktop and tablet;
  2. the phone-width Management sub-navigation row (a swipeable strip with no visible scrollbar);
  3. the phone and tablet Area Board paged detail carousel with Summary off (snap paging, no visible scrollbar).

  Content surfaces (tables, cards, dialogs) never require horizontal panning on a phone.
- **The Production Board adapts by scaling, not by re-layout.** It is a Department wall or kiosk display sized for
  reading at 3–5 m, with measured content-driven columns (§2.3, §5); it is not a phone working surface, and its layout
  is never compressed at the cost of the distance-readability rules. On small screens the automatic display scaling
  (§5) shrinks the whole board uniformly so the full table width fits, and a horizontal swipe changes pages. With Auto
  scale off, the board may scroll within its own region on very narrow viewports.
- **Existing narrow-viewport behaviors** (near-full-screen workflow dialogs in §11, demand-line cards, the Tracking
  overlay spanning the viewport in §7) remain and complement these rules.
- **Shop-floor rules are not weakened.** The PN single-line rule (§2.3) and the Scan Station's scanner-first model
  (§2.4) still apply. Phone support is a browsing and monitoring convenience and never weakens shop-floor rules.

## Forms and dialogs

These presentation rules apply to the dialog forms of every view:

- **Qualifiers in field labels.** A parenthesized qualifier inside a field label renders through a shared marker that
  is visually distinct from the label text: `.field-optional` (quiet, faint) for `(optional)`; `.field-required`
  (semantic error tone) for `(required)` (Scan Station reason and scrap-reason labels, the Machine Reactivate reason);
  and `.field-note` (neutral, one step quieter than the label) for neutral qualifiers (`PN (queued)` in Assign to
  Machine, the typed-confirmation `(Asset Tag)` hint).
- **Qualifier placement.** When the label sits **above** its control, the qualifier stays inline on the label's own
  line: a stacked flex-column label wraps its text (including the qualifier span) in ONE inline element, so the
  qualifier can never land on a row of its own between the text and the control. When label and control sit **side by
  side**, the qualifier may sit inline or wrap naturally under the label text, whichever fits the column.
- **Label spacing.** Every label-above-control dialog form uses one consistent 8 px gap between label and control.
- **Decision dialog spacing.** The compact decision dialogs (`ConfirmDialog`, `UnsavedChoiceDialog`,
  `TypedConfirmDialog`) share the `msgdlg` spacing variant of the dialog shell: larger padding and clear breathing room
  between the eyebrow title, the message body and the action row. Workflow dialogs keep the tighter base spacing.
- **Select chevron.** Every native select draws one consistent theme-muted dropdown chevron: a per-theme
  `--select-arrow` token consumed by a shared `select` rule with `appearance: none`. Select-surface rules set
  `background-color`, never the `background` shorthand that would erase the arrow.
- **Discard tone.** In the shared `UnsavedChoiceDialog`, the **Discard** action takes the danger tone in every caller
  (Work Orders, Machines, Planned Routes, Part Numbers): it always throws the entered edits away, so it never reads as
  the neutral option beside Cancel.

---

# 3. Global Interaction Rules

These rules apply to every view. They express the core principles of PROJECT_PROFILE in UI terms.

1. **Focus discipline.** On the Scan Station, the barcode input regains focus automatically after every completed
   operation, dialog close, session change and view activation. Nothing may steal focus permanently. **When a scan
   opens a dialog, focus moves into the dialog and stays there:** Enter, Escape and Tab belong to the dialog while it is
   open, and no pending delayed refocus may ever pull focus back out of an open modal dialog. Focus returns to the main
   barcode input only after the dialog closes or the workflow completes. Keyboard-wedge capture still applies: with no
   dialog open, a scan is captured completely even when the main input is unfocused (§4.4).

2. **Ambiguity requires confirmation.** Whenever a scan resolves to more than one valid production context, the UI
   presents an explicit choice list (§4.6). The UI never guesses and never defaults silently; nothing is recorded until
   the choice is made (PROJECT_PROFILE §10 Barcode Model).

3. **Feedback is tri-state and instant.** Every scan produces exactly one of three results: Success (green), Warning
   (amber: recorded but needs awareness, or an action is pending), or Error (red: nothing recorded). Feedback shows
   *what happened* and *why*, in one line each. On the Scan Station this feedback renders as a **floating
   notification** (§4.4) that reserves no layout space: only the most recent notice shows; it carries an explicit
   close button; success and neutral notices dismiss themselves after about 4 s and warnings and errors after about
   8 s; a replacing notice restarts the timer. The persistent OFFLINE / DISCONNECTED banner (rule 6) is **not** a
   notification: it stays until connectivity is restored.

4. **Quantity integrity is visible.** Quantity entry displays the available source quantity. An attempt to move more
   than is available is rejected with an explicit error: the UI explains the limit rather than clamping silently
   (PROJECT_PROFILE §11 Quantity Model).

5. **History is append-only in the UI too.** Undo appears as a new `REVERSED` entry in the scan list, and the original
   entry stays visible. Tracking's Movement history has no edit or delete affordances (PROJECT_PROFILE §16).

6. **Connectivity loss is an explicit write-blocked state, detected fast.**
   - **Detection** combines browser `online`/`offline` events with polling; the UI uses no WebSocket or server-sent
     events (SSE) channel. An `offline` event marks the application unavailable immediately; an `online` event triggers
     an immediate health check; `GET /api/health` is polled about every second, with a request timeout shorter than
     the probe interval and no overlapping probes; connectivity is rechecked when the tab gains focus or its visibility
     changes. Passive probes never flip the UI to a "connecting" state, so there is no flicker.
   - **The OFFLINE banner.** Losing the connection shows the persistent application OFFLINE banner, and production
     write submission is disabled while disconnected. **The banner copy is exactly
     `⚠ OFFLINE — Connection to the PartFlow server has been lost. Production actions are disabled`: one compact
     statement with no additional explanatory sentences** (no explanations about a scan queue, read-only data or
     recovery; the write-blocked behavior itself is the explanation on every surface).
   - **Banner layout.** The banner renders as **two explicit regions**, the same division as the Scan Station Undo
     block (§4.5). The message region fills the remaining space on the left; its text wraps naturally on narrow screens,
     with no horizontal overflow. **`Retry connection` is an independent action rail at the banner's complete right
     edge**: the whole region is the button element itself (never a small text button after the message), at full
     banner height with no gap or leftover background beside it, and its text centered vertically. There is **no
     separator element and no `|`**: a **short vertical rule inset from the banner's top and bottom edges**, drawn by
     the rail itself and purely decorative, divides the regions; the rail's click and hover area still reaches flush to
     the banner's edge and right up to that rule. The rail carries the shared `.zone-action` presentation: it is
     **transparent at rest**, so it reads as part of the banner's own surface and only its text carries the error color;
     a light error surface appears only on hover and while pressed (active), and keyboard focus (focus-visible) draws an
     error-toned outline inside the region. These states apply to the whole region.
   - **While disconnected.** Already loaded read-only information stays visible where practical. No production write is
     queued locally, no pending-Movement indicator is shown, and the UI never claims that scans will synchronize later.
   - **On reconnection**, input readiness and focus are restored: the Scan Station input is re-enabled and refocused.
   - **A scan is successful only after the server confirms the write.** Connectivity status is an early warning, never
     permission to record a Movement optimistically. If connectivity disappears between the last heartbeat and a write
     request, the request fails as "nothing recorded"; the UI must never display a false recorded result
     (PROJECT_PROFILE §15).
   - This rule must be preserved unchanged in the production phases. Offline scan synchronization remains deferred and
     unapproved (PROJECT_PROFILE §30, §32.4).

7. **PartFlow vocabulary everywhere.** UI labels use the canonical names: PN, WO / Work Order, Work Order Demand,
   Request Type (`NEW` / `MODIFY`), Route Mode (`FLOATING` / `PLANNED`), Quantity Flow, Route, the Movement types
   (`RECEIVED`, `TRANSFERRED`, `ASSIGNED_TO_MACHINE`, `RELEASED_FROM_MACHINE`, `AREA_COMPLETED`, `SPLIT`, `MERGED`,
   `STOCKED`, `QUANTITY_ADJUSTED`, `SCRAPPED`, `REVERSED`, …), the user-facing DONE status with its derived
   `READY_TO_TRANSFER` holding state (PROJECT_PROFILE §7 Area Completion), the Repair movement intent
   (`movement_reason = REPAIR`, never a Request Type), Work Order Allocation, and Hot (PROJECT_PROFILE §7, §8.11). A
   blank external Work Order Number renders as `—` everywhere; this is display-only and never persisted.

8. **One-shot actions, no armed state.** There is no persistent Machine Session, no armed Action barcode, and no
   pending PN intent that survives a dialog. Every Scan Station production action is one dialog: open, complete or
   cancel, context cleared, input refocused. Cancel always means no write.

9. **Missing due dates are valid data.** Work Order and demand-line due dates may be absent (PROJECT_PROFILE §8.2,
   §8.3). A missing due date renders as `No due date` where a note line exists and as `—` as the compact or table date
   value, consistently, and never as an error or warning state. Wherever demand is ordered by due date, the canonical
   demand ordering of PROJECT_PROFILE §18 applies: dated demand first, earliest first; undated demand after all dated
   demand, ordered by the parent Work Order's received date, with a stable deterministic tie-breaker.

10. **Professional operator copy, exhaustively.** Every rendered string is classified as exactly one of the following:
    - **Operator- or user-facing.** It explains what to do, what is required, what happens after confirmation, why an
      action is unavailable or rejected, and how to recover. It never states implementation detail.
    - **Audit- or history-facing.** Canonical recorded data: Movement types, workers, timestamps, Areas, Machines,
      quantities and reversal relationships. Raw enum names are acceptable only here, that is, in the Movement history,
      type badges and the confirmation summary's `Recorded event(s)` row.
    - **Development-only.** Demo barcodes and sample-data boundaries. This copy is concise, clearly isolated behind the
      shared `DevNotice` (dev-marked) presentation, excluded from production builds, never dominates normal workflows,
      and is never repeated per toast, dialog or footer: at most one concise development notice per view.

    The shared `DevNotice` is a fixed two-part structure: the compact `.dev-notice-tag` plus **one**
    `.dev-notice-content` element that wraps all children as a single inline text flow. Long copy wraps only at natural
    word boundaries and is never distributed across separate flex items. The notice fills its parent block's available
    width (`width: 100%`, `box-sizing: border-box`, `min-width: 0`, no `max-width` cap), and each `<code>` barcode value
    inside it is unbreakable (`white-space: nowrap`) while the sentence still wraps between values.

    Normal copy never exposes wording such as `(mock)`, `nothing persisted`, `local mock data`, `priority_rank`,
    `movement_reason`, `derived from Movement history`, or architecture explanations. Legitimate operational
    distinctions are expressed in user language instead (for example `Requested quantity`,
    `Current recorded location`, `Complete activity history`). Cancel buttons throughout the workflows read exactly
    `Cancel (Esc)`.

    An automated rendered-copy guard (`src/rendered-copy.test.ts`) scans the renderable frontend sources (comments
    stripped) and the current mockup for banned developer-facing phrases, with an explicit allowlist mechanism for
    approved audit surfaces. Canonical Movement names themselves are never banned.

11. **No false scrollbars.** The application shell derives the available content area from normal flex layout: the
    root is sized to the dynamic viewport (`100dvh`, with a `100vh` fallback), navigation rows keep their natural
    height, and the content area scrolls internally only when a view genuinely exceeds it. A view whose content fits
    shows **no** vertical scrollbar. Content that genuinely exceeds the viewport (including the development
    `?state=long` previews) scrolls normally: real overflow is never hidden, and dialogs, long tables and validation
    content are never clipped. No view duplicates a fixed navigation-height calculation (`100vh − nav`), and the
    navigation stays visible while content scrolls.

12. **Derived time values share one UI clock.**
    - **Fixed versus derived.** Original timestamps (Movement times, state changes, due dates, Area-entry times) are
      fixed data. Only the **derived** presentation values (elapsed durations, due countdowns, days in production,
      live clocks) change with wall-clock time.
    - **One shared clock.** Every view derives those values at render from the fixed source timestamp plus the one
      shared clock (`src/components/ui-clock.ts`, `useUiClock('minute' | 'second')`: one shared subscription store per
      precision, never per-component timers that drift apart). Minute-precision values re-render once per minute and
      the live clock once per second, never faster than their precision needs. After a movement or state change, the
      affected component re-renders immediately and re-derives from the current tick. The formatters clamp a timestamp
      newer than the tick to `<1m` instead of going negative. The same timestamp therefore yields the same displayed
      value in every view at the same moment: Scan Station, Area Board, Production Board, Tracking and Machines can
      never disagree.
    - **Shared helpers** live in `src/views/dates.ts`:
      - `formatDuration` / `formatElapsedSince` / `elapsedMinutesSince`: one compact duration language (`<1m`, `18m`,
        `1h 24m`, `2d 03h`);
      - `daysBetweenIso`;
      - `dueCountdown`: one countdown language everywhere (`N days left`, `due today`, `overdue N days`,
        `No due date`), with `soon` inside a **Due Soon window proportional to the lead time**;
      - `daysInProductionNote` (`N d`).
    - **The Due Soon window** is the lead-time percentage of the received → due lead time (exact whole-number
      arithmetic), clamped into [`minDays`, `maxDays`] and derived by `dueSoonWindowDays` from the `DueSoonPolicy` the
      caller supplies. The policy is the server's persisted **Due Soon warning** configuration (Administration →
      Settings, §9; initial values Minimum 2 days, 15 %, Maximum 7 days), loaded as part of each view's ready state.
      Business logic never hard-codes the numbers, there is no frontend default, and an unknown or invalid lead time
      falls back to the policy's minimum window. The Scan Station alone reads the policy beside its Area inventory;
      when the policy cannot be loaded, the station keeps every production action and withholds only the `soon`
      judgement behind an explicit notice. A display policy never gates production.
    - A formatted duration or countdown is never stored.
    - The development mocks author **relative** offsets that are resolved once at module load
      (`src/mocks/mock-time.ts`: `minutesAgoIso`, `isoDateIn`), so the sample data always shows the intended mix of
      states regardless of when the development build opens. From then on the values are fixed data, like real backend
      timestamps.

13. **A changed release is an explicit write-blocked state.** A release is one deployed version of PartFlow.
    - **Detection.** Every request carries the release of the page that is loaded. The server refuses a change that
      comes from a page of another release (nothing is recorded) and reports its own release in the health answer of
      rule 6.
    - **The update notice.** When the release differs (in a health answer or in such a refusal), the persistent
      **update notice** shows in the OFFLINE banner's place, with rule 6's two-region layout: the exact copy
      `⚠ UPDATED — PartFlow was updated on the server. Reload this page to continue. Production actions are disabled`,
      and the action rail `Reload page`. Production write submission is disabled exactly as while disconnected, reads
      continue, and the connectivity chip reads `UPDATED`. While the connection is lost, the OFFLINE banner shows
      instead. The notice takes no focus.
    - **Reloading.** **The page never reloads itself while a dialog is open.** On the Scan Station and Production Board
      routes (unattended stations and wall displays), the page reloads itself once the notice has shown for 60 s with
      no dialog open, at most once per 10 minutes per browser tab, and only when the server already serves the new
      page. Elsewhere only `Reload page` reloads, after the existing unsaved-change guard.
    - **Dialogs that cannot be dismissed.** The Worker sign-in / `Worker session expired` modal, the enrollment dialog
      (§4.12, §4.13) and the `Choose a new password` dialog (§1.2) cannot be dismissed, so while the page is outdated
      each shows its own `Reload page` button; the password dialog also shows
      `PartFlow was updated — reload the page to continue.` While the Worker sign-in modal or the enrollment dialog is
      open, the station does not reload itself.
    - **Monitoring.** Monitoring feeds keep refreshing, so the `● Live` status of §5 stays healthy, and the notice states
      the reason.
    - **Unknown outcomes.** A dialog whose earlier submission had no answer keeps its unknown-outcome state. The
      outdated page cannot resolve it, so after reloading the operator checks the Area (or Work Order) before repeating
      the action.
    - **Scan Station copy while outdated (exact):**
      - the scan inputs read `PartFlow was updated — reload to continue scanning`;
      - the blocked-scan notice reads `PartFlow was updated — scanning is paused` /
        `Reload this page before continuing. No scans or production updates are recorded until then.`;
      - a write that was not sent reads
        `PartFlow was updated — the {what} was not sent and nothing was recorded. Reload the page to continue.`;
      - a dialog's blocked line reads `PartFlow was updated — the {what} cannot be recorded until this page is reloaded.`;
      - the enrollment reason reads `PartFlow was updated — reload this page to enroll the device.`
    - **Other disabled reasons.** Disabled reasons that name the connection elsewhere (Work Orders import and its
      dialog, §11.7; sign-in; Edit assigned Route) read `PartFlow was updated — reload the page to continue.` while
      outdated. Every other disabled reason is unchanged.
    - **A view that cannot load.** A view whose code cannot load after a release offers `Reload page`
      (`This view could not be loaded — PartFlow may have been updated.` /
      `Reload the page to continue. The rest of the application is still available.`).
    - The business rules are in PROJECT_PROFILE; this rule only states what the screen shows.

---

# 4. Scan Station

The Scan Station is one screen per station, with no navigation during normal production. It is organized around the
Part Number and offers one-shot actions only.

## 4.1 Station routing and selection

There are three routes:

- **`/scan-station` — the Station Selector.** It never redirects to a station automatically. Each active Scan Station
  renders as one card with enough information to distinguish it: Station ID, Department, Area (with its color), the
  supported Operations as **individual light informational chips** (the same shared chip presentation as the station
  header, never comma-joined text), and whether the Area has Machines.
  - The card's **main selection surface is one full-card button** that opens the station in **standard mode**
    (→ `/scan-station/:stationId`).
  - A separate sibling button labeled exactly **`Production mode`** opens `/scan-station/:stationId/production`.
  - There is no standalone `Open` button and there are **no nested interactive controls**: the full-card button
    contains only presentation content. Both actions carry accessible names with the Station ID
    (`Open LATHE-ST-01`, `Open LATHE-ST-01 in production mode`).
- **`/scan-station/:stationId` — one station in standard mode** (for example `/scan-station/LATHE-ST-01`). It keeps the
  normal top application navigation (for use by Managers and Administrators). An unknown or inactive Station ID shows a
  clear error and never silently falls back to another station.
- **`/scan-station/:stationId/production` — the same station in production mode.** The top application navigation (and
  with it the Production Board, Management and Administration links) is hidden, so operators cannot casually leave the
  configured station.
  - The mode is explicit in the router model (`mode: 'standard' | 'production'`) and in the pathname, never a query
    parameter, hidden click or secret gesture.
  - It is a presentation choice only, **never an authorization or security boundary** (the browser itself stays outside
    PartFlow's control).
  - Everything else is preserved: the persistent OFFLINE banner and update notice (§3 rule 13), the Worker Session, all
    scan, DONE, QUEUE, Undo, dialog and notification behavior, and full-viewport height with no leftover top-navigation
    offset.
  - Connectivity status stays visible through the connectivity chip inside the station header, with the **compact
    borderless Dark/Light mode control** (the same shared theme component and global state as the top-navigation
    toggle, §2.1) **directly below it**, in one compact vertical, right-aligned header-actions stack. The narrow stack
    never crowds the Worker Session and never forces the Area statistics to wrap poorly at intermediate widths.

**Head group height contract.** The Worker Session pill keeps its **natural height in both modes**; it is never
stretched. `.ss-headgroup` uses `align-items: flex-start`. The **frameless** actions column (`.ss-headactions`) gets
`align-self: stretch` with `justify-content: space-between`, and compact padding on the ONLINE chip and the theme toggle
keeps the actions' combined natural height below the pill's, so the pill is always the height driver. The `ONLINE` chip
(which keeps its own chip frame; there is no group card and no separator) aligns its top edge with the pill's top
border, and the theme control aligns its bottom edge with the pill's bottom border. The header action group can never
determine the Worker Session height.

Both modes require the browser to be an enrolled device of that Scan Station (§4.13). Standard mode on a Manager's or
Administrator's own computer is enrolled like any other device.

**Footer.** Both modes share **one coherent non-interactive footer**: the faint Station ID, a subtle non-interactive
mode label (**`Standard mode`** or **`Production mode`**), and the concise shortcut hint
**`Ctrl+Shift+K: switch mode`**.

- The Station ID is never a button in either mode. The top navigation's **Scan Station** entry already returns to the
  Station Selector, and the footer never duplicates that behavior.
- The **Ctrl+Shift+K** shortcut toggles between the same station's standard and production routes. It works from the
  main barcode input (a scanner target that the wedge capture leaves alone under modifier chords) but is inert in every
  other text field, select or active dialog. The dedicated route and the explicit selector actions stay the primary
  entry.

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
│ Station LATHE-ST-01 · Standard mode · Ctrl+Shift+K: switch mode (faint)      │
└──────────────────────────────────────────────────────────────────────────────┘
                                        [floating scan notification — no
                                         reserved layout space, bottom edge]
```

Cards use the same subtle semantic shadow token as the Management cards (`box-shadow: var(--shadow)`); there is no
separate strong shadow or decorative glow system.

## 4.3 Header

**Grid.** The header is an explicit grid, with no spacer-based wrapping.

- The left identity group (Department, Area name with the Area color, supported Operations) and the **Worker** pill
  (absent in Disabled Areas, see below) always share the main row.
- The **Area statistics** sit between them while horizontal space allows. They drop to a full-width second row, whose
  cells divide the width evenly, only when the single-row layout genuinely cannot fit. This is decided by a real fit
  measurement of the full natural widths of the three header cells, never by a hard-coded breakpoint. The identity
  group keeps the Department, the Area name and the full Operations row (label plus every chip) each on one line, so
  the Operations row never sheds anything before the statistics drop.
- The Worker session never wraps onto a row of its own. The pill is **content-sized** (no fixed or minimum width), and
  its Worker name and sub lines never wrap.
- There is **no Machine session pill, no Machine status strip and no persistent active-Machine state**.

**The Worker pill follows the Area's Worker ID mode.** There is no shift-end concept anywhere. The pill is one two-line
structure: the **borderless tinted avatar spans both text lines** (its circle is as tall as the name line and the sub
line together), with the Worker name on the first line and the mode or countdown sub line beneath it.

| Worker ID mode   | Pill content |
|------------------|--------------|
| Scanned session  | The active Worker's avatar and name (`[avatar] H. Nguyen`) with the live session countdown sub line `Session: 10m 23s`. Minutes and seconds tick down every second in the sub line's plain (non-bold) weight, with the time value in the **success tone**, derived from the shared UI clock (§3 rule 12). Near expiration (≤ 2 minutes) the value and line step into a **warning tone**; this is a color change only, never a weight step. With no active session the pill reads `No Worker` over `Session · scan badge`. |
| Fixed Worker     | The configured Worker's avatar and name with the static sub line `Fixed Worker`; no countdown. |
| Disabled         | **No Worker pill renders at all.** Worker identity does not exist in such an Area, the header simply ends after the Area statistics, and the mode itself stays visible in Administration → Areas (§9). |

Session behavior, sign-in and expiration are specified in §4.12.

**Operations chips.** Each supported Operation renders as an individual light informational chip: subtle panel
background, subtle border, rounded corners, readable in both themes, no action color and no button-like hover. The
station header keeps the Operations on **one line at every width**, shedding presentation in measured tiers (below)
instead of wrapping; the Station Selector cards (§4.1) keep wrapping their chips. The Area name stays visually more
prominent. Operation names are never parsed or modified. The chips are labels, not controls.

**Narrow header: tiered Operations presentation.** When the identity cell genuinely cannot hold the full single-line
`Operations: <chips>` row (a real fit measurement against a hidden ghost copy of the row at its natural width, never a
hard-coded breakpoint), the row sheds presentation in this order:

1. the `Operations:` label is dropped and the chips keep the line alone;
2. trailing chips are replaced by one `…` chip whose tooltip names the hidden Operations;
3. when not even the first chip fits beside the ellipsis, the row hides entirely.

The header fit probe described above measures the identity cell at its full natural width (label plus every chip), so
the Area totals always drop to their second row before the Operations row sheds anything. Returning space restores the
full presentation.

**Area totals with semantic tones.** The header is the **single summary surface** for Area totals; the
`In this Area now` card carries no statistics block (§4.10).

- Area with Machines: Total PNs · Total pcs · Queued · On machines · Done · Hot, reconciling as
  `Total pcs = Queued + On machines + Done`.
- Area without Machines: Total PNs · Total pcs · Processing · Done · Hot, reconciling as `Total pcs = Processing + Done`.

Number tones follow the shared statistic tone mapping used by the Area presentation. **The PN totals (Total PNs / PNs)
carry the primary text neutral (the main text color) on every view, while Total pcs keeps the secondary muted neutral,
the same color as the Machine-card totals line text.** The status statistics keep their semantic tones: Queued warning,
On machines / Processing information, Done success, Hot error. All tones are semantic tokens, never theme-specific
hard-coded colors. The reconciling quantity values always render as numbers; Hot renders `—` when zero.

## 4.4 Scan barcode card

The Scan Barcode card sits immediately under the header and spans the full available width. The main input supports
**PN barcodes**, **Worker barcodes**, and **Machine barcodes only as a one-shot shortcut** (§4.6). Action barcodes do
not exist: `PF:SCRAP` in the main input is rejected, because it counts only inside the Scrap workflow (§4.9). Unknown
barcodes and raw PN text are rejected with nothing recorded.

There is **no dedicated ENTER button**: the scanner submits with Enter, and the input's placeholder says so:
`Scan Part Number, Worker, or Machine barcode · Press Enter`. The barcode input remains the primary and largest
control.

**Keyboard-wedge capture: the main input never loses a scan.**

- When no dialog is open, a scanned barcode reaches the main input even while the input is not focused: the first
  scanned character is captured (never lost) and focus then follows the scan. A barcode terminated by Enter is
  submitted exactly once.
- The capture never intercepts typing when focus is inside another text input, a textarea, a select, a
  contenteditable element or an active dialog workflow. It never interferes with Ctrl/Alt/Meta shortcuts, normal button
  activation or accessibility navigation, and it never merely forces focus after every click.
- After processing, cancellation, dialog close, Worker change, reconnection or a completed operation, focus is restored
  to the main barcode input as appropriate.
- Unknown or ambiguous input never updates tracking data.

**Touch-primary devices.** On a touch-primary device the main barcode input renders `inputMode="none"`, so tapping it
never raises the native soft keyboard: the input is driven by the scanner or a physical keyboard and is never an
ordinary text field. Detection uses the same shared capability-detection module as the quantity input
(`src/components/touch-device.ts`, §4.8). Physical keyboards, wedge scanners, the focus behavior and the manual-entry
dialog input (a normal keyboard field) are unaffected.

**Manual PN entry** stays a visible secondary action (**⌨ Enter PN manually**) placed **in the scan row itself**, at the
input's right edge: visibly secondary to scanning but large enough for shop-floor use.

- On narrow screens the button compresses **before** the barcode input gives up width. It shrinks to roughly half its
  natural width, wrapping its label onto two lines but never below its widest word, while the input keeps the remaining
  row width. This is continuous flex behavior with no hard-coded breakpoint, and the button never drops below the
  input.
- It accepts **any PN value that is non-empty after trimming and free of internal whitespace** (PROJECT_PROFILE §7,
  §8.1; the PN is created on first valid use). Surrounding whitespace is trimmed, lowercase input is accepted and
  canonicalized to the uppercase PN, and a value with internal whitespace is rejected with an inline explanation and
  never silently cleaned up. The value resolves the canonical PN exactly like a scan.
- The fallback explanation remains only as a small hint line below the scan row; there is no separate button row.

**Fixed card content order.** The card's DOM order is the visual, keyboard and screen-reader order and is never
reordered by CSS:

1. the main barcode input row with **⌨ Enter PN manually**;
2. the manual-entry fallback explanation directly beneath that row;
3. the shared `DevNotice` demo barcodes;
4. the `LAST SCANNED PN` section (§4.5).

**Demo barcodes hint.** The development-only "Demo barcodes (development build only) — …" content renders through the
shared `DevNotice` component, the same development block as the Management views (§3 rule 10), not through a bespoke
hint block. `.dev-notice code` styles the barcode values: each value is unbreakable while the sentence wraps between
them, in the notice's single full-width inline text flow (§3 rule 10).

**Floating scan notification.** Scan feedback does not reserve layout space in the card. It renders as one floating
notification near the viewport's bottom edge:

- only the most recent notice shows, and an explicit `×` close button dismisses it;
- success and neutral notices dismiss themselves after about 4 s, warnings and errors after about 8 s; a replacing
  notice restarts the timer, and timers are cleaned up on replacement and unmount;
- notices use the semantic success, warning and error styling and alert or status live-region semantics, stay inside
  the viewport on narrow screens, and never cover the main barcode input or dialog actions.

The persistent OFFLINE / DISCONNECTED application banner is not a notification and remains until connectivity is
restored. `Last scanned PN` stays fixed inside the Scan Barcode card; only the feedback surface floats.

## 4.5 Last scanned PN and Undo

The Last Scanned PN surface lives inside the Scan Barcode card, with the **Undo action anchored to its right edge**.
There is no separate Undo card and **no Recent Scans list**.

**Presentation.**

- The compact uppercase section label sits **outside** the block: left-aligned, directly above it, never inside the
  bordered surface.
- The block itself is **two explicit regions**, the same division as a Station Selector card:
  - the **information region** (the PN, never wrapped arbitrarily, with the movement summary beside it) fills the
    remaining space on the left;
  - the **`⟲ UNDO` action region** IS the block's complete right edge. The whole region is the button element itself
    (never a small text button inside a larger container), flush with the right edge and at full block height, with no
    gap, spacer or leftover background beside it (the block clips its children). Its icon and text are centered both
    horizontally and vertically.
- There is **no separator element and no `|` character**. A **short vertical rule inset from the block's top and
  bottom edges**, drawn by the action region itself and purely decorative, divides it from the information region; the
  region's click and hover area still reaches flush to the block's border and right up to that rule. The region carries
  the shared `.zone-action` presentation (§3 rule 6) across the whole region: transparent at rest, so only the icon and
  text carry the error color, a light error surface on hover and while pressed (active), and the shared error-toned
  focus-visible outline.
- When narrow, the PN keeps the first row and the summary moves to its own full-width second row inside the
  information region, while the action region keeps covering the full block height at the right edge. There is never
  horizontal overflow.
- The block carries no bottom margin: the parent card's padding is the only outer spacing below it.
- A disabled Undo makes the whole action region non-clickable while keeping its layout and divider rule (no size
  change). Icon, text and divider rule dim together clearly, and no hover surface appears; the information region never
  dims.

**Behavior.**

- Worker scans never replace the Last Scanned PN, and cancelled dialogs never replace it. Only completed PN-related
  actions become the Last Scanned PN.
- Undo targets the most recent eligible completed PN operation and always shows a summary confirmation first: PN,
  original action, quantity, source and destination, Machine if applicable, Worker, timestamp, and the effect of the
  reversal. It uses the same structured confirmation-summary layout as the production wizards (§4.6). Cancel performs
  no write.
- Undo creates compensating (reversal) history; the original action is never deleted. After a confirmed Undo, the Last
  Scanned PN advances to the next eligible previous PN operation. With nothing eligible, Undo is disabled.
- **Worker identity for Undo** follows the Area's Worker ID mode (§4.12): Disabled records no Worker; Fixed Worker
  records the configured Worker; Scanned session with a valid session records the **Worker active at the moment the
  Undo is confirmed** on the `REVERSED` (audit) record. The confirmation shows the original action's Worker as context
  and the reversing Worker separately.
- **Final gate for Undo.** After `Confirm reversal`, Undo carries the shared **final-confirmation gate** (§4.6):
  - in a Scanned-session Area with the Administration `UNDO` option ON (the default), a **badge-scan gate** completes
    the reversal; any active Worker badge confirms and identifies the confirming Worker;
  - in every other case (a Fixed-Worker or Disabled Area, or the option OFF), a **warning-toned final question**
    (`Reverse this action?`) restates the key facts before anything is reversed.

  The final gate itself is never skipped. In Scanned session mode with no active session or an expired session, the
  station's badge modal (§4.12) already blocks the surface: a valid badge scan is required before Undo can open or
  confirm.
- **Undo reason.** While Administration → Correction permissions → **Undo reason** is On, the Undo summary shows a
  required `Reason` field (label `Reason (required)`, hint `This reason will be included in the reversal history.`)
  below the summary and before the final gate. `Confirm reversal` stays disabled until the field holds text, the final
  gate restates the reason with the key facts, and the reason is recorded on the reversal and shown in Tracking. A
  reversal that the server refuses for a missing reason records nothing: the summary shows the field with the server's
  explanation and keeps every selection. With the option Off (the default), no reason field is shown.
- Production Undo operates on the complete application command (PROJECT_PROFILE §16), not on one arbitrary Movement
  row.
- **Undo not granted.** When the role applied at Scan Stations does not grant Undo, the `⟲ UNDO` action region is not
  shown. The Last Scanned PN block then has only its information region, spanning the block's full width, with no
  divider rule and no empty action zone. The narrow-screen two-row rule still applies.

## 4.6 One-shot workflows — temporary wizards

There is no persistent Machine Session and no persistent PN intent after a dialog completes or is cancelled. Every
production action is **one temporary wizard inside one dialog lifecycle**:

1. open;
2. select or enter information;
3. review a dedicated confirmation view;
4. confirm or cancel;
5. all temporary state is cleared;
6. focus is restored to the main barcode input.

The steps are views rendered inside the same modal dialog. They are never nested dialogs, and the overlay never closes
and reopens between steps.

### Wizard rules (all workflows)

- **No production action is recorded before the final confirmation step.** Selection and entry steps never write.
- **The structured confirmation summary.** The final view is a two-column term/value layout that shows only the
  applicable fields: Action, PN, Quantity, Request Type, Route Mode, Planned Route, Work Order behavior, Source,
  Destination, Machine, Area/Operation, Due date, Reason/notes, Worker, Scan Station, and the resulting quantity state
  or effect. It is never one long interpolated sentence and never a note inside a dashed box.
  - **Row emphasis.** A small optional row-emphasis mechanism makes primary operational values (PN, Quantity, Action,
    Source, Destination, Machine, Area, Operation, and Reason when it is operationally important) read stronger.
    Selected entities may render as subtle non-interactive chips. Secondary audit and context rows (Worker, Scan
    Station, recorded event names, parenthetical notes) stay present but visually quiet.
  - **Verification layout.** The summary's label column is content-sized (`fit-content(230px)`), so values sit close
    to their labels instead of drifting across a fixed-width gap. Entity chips inside confirmation summaries **flatten
    to plain verification text** that inherits the row's size and weight: a confirmation is read-and-verify, never a
    set of badge-like elements. The PN keeps its monospace distinction; step recaps and selection steps keep the chip
    look.
  - Nothing is hidden, labels never look like buttons, contrast holds in both themes, long values wrap safely, and
    color is never the only distinction.
  - **Semantic value tones.** Summary values may carry a controlled semantic tone: success and recorded results use the
    success tone, deviations the warning tone, and destructive quantities the error tone, while Worker, Scan Station and
    timestamps stay muted. Area values (Source, Destination, Area, `Area queue`) carry their Area color dot beside the
    flattened plain verification text. The tones are additive, never the only distinction, and never one color per
    field.
  - The same presentation applies to every confirmation dialog, including the Undo summary.
- **Confirmation buttons.** The confirmation view has **Back**, **Cancel (Esc)** and a **final Confirm whose label names
  the operation** (`Confirm receipt`, `Confirm assignment`, `Confirm transfer`, `Confirm addition`, `Confirm repair`,
  `Confirm scrap`, `Confirm return to queue`). Enter on the confirmation view activates the final Confirm while the
  operation remains valid.
- **Final-confirmation gate for sensitive actions.** `DONE` (Complete Area processing), `QUEUE` (Return unfinished
  quantity to queue) and Undo (§4.5) add ONE more step after their confirmation summary, ALWAYS, in every Area. Each
  action has its own Administration → Worker sessions option (default ON; §9, PROJECT_PROFILE §19), which decides only
  whether that final step must be a Worker badge scan where badges exist.
  - **Badge-scan gate.** In a **Scanned-session** Area with the option ON, the confirming action opens a badge-scan gate
    in the shared attention presentation: the **blue information tone for DONE**, and the **warning tone for QUEUE
    return and Undo** (the global tone family, §12.4). The key facts of the pending action stay visible with the **PN
    emphasized in bold** (`Are you sure Lathe 3 has finished 2 of 5 pcs of **{PN}**?` /
    `Are you sure you want to return 4 of 10 pcs of **{PN}** running on Lathe 2 back to the {Area} queue?` / the
    reversal facts). **Any ACTIVE Worker badge confirms.** The badge identifies the confirming Worker, who is recorded
    on the action and signed in exactly as by a normal badge scan. Unknown or inactive badges are rejected in place.
    Cancel or Escape returns to the summary with nothing recorded.
  - **Final confirmation question.** In every other case (a **Fixed-Worker or Disabled** Area, or a Scanned-session
    Area with the option OFF), the final step is a final confirmation question instead, in the shared Machines
    last-question presentation (§12.4). It shows the same key facts in the same tones as the badge gates: the
    **information tone** for DONE (`Confirm finished quantity?` / `Yes — finished`), and the **warning attention
    variant** for QUEUE (`Return unfinished quantity?` / `Yes — return to queue`) and for Undo
    (`Reverse this action?` / `Yes — reverse it`). Cancel returns to the summary.
  - The final gate itself is never optional; no configuration removes the step. Nothing is ever recorded before the
    gate completes.
- **Back preserves all previously entered values.** It appears only when a meaningful previous view exists, never as a
  second Cancel.
- **Back to the parent dialog.** A meaningful previous view includes the dialog or step that the current dialog was
  opened from within the same workflow:
  - every dialog opened from the PN action dialog (Machine assignment, receive/transfer, source selection, DONE, add
    quantity, repair, scrap) offers Back to that action dialog;
  - a transfer opened from the source selection returns to that selection with all sources intact;
  - a dialog resolved from manual PN entry returns to manual entry with the entered PN preserved for correction;
  - when one dialog is reachable through several entry paths, Back returns to the exact step the user actually came
    from (the stored previous step of the current workflow, with context such as PN, source and selections intact).

  Back never records or changes tracking data. Workflows entered directly from the Scan Station surface (a scan, a
  monitoring row action, Undo) have no previous dialog step and show **no** Back; Cancel stays the only exit there.
- **Escape always cancels the entire workflow** from any step and records nothing. Cancelled workflows never update the
  Last Scanned PN.
- **Selection views** (source selection, "Select an action") stay selection views; they are not additional confirmation
  steps.
- **Dialog information hierarchy.** Every dialog keeps four visually distinct layers:
  1. a neutral, concise **description** (purpose and operational consequence, with no walls of text);
  2. a compact secondary **step recap** of previous selections (important selected entities such as Machine, Area,
     Operation or source may render as subtle non-interactive chips);
  3. **guidance** directly above the related input (§4.8);
  4. **validation** that reads stronger than any instruction, with actionable wording and with entered data preserved.
- **Focus follows the steps.** The first useful control of each step receives focus: the quantity input is focused with
  its value selected on quantity steps, and the barcode-selection input on assignment Step 1. Back restores focus to the
  relevant prior control, and closing or completing the wizard restores focus to the main barcode input. Wizard state
  is local dialog state: there is no Context, and no hidden Machine or PN state survives the wizard. **The Receive
  Quantity settings step is the deliberate exception:** it opens with NO field focused; the dialog root holds focus,
  so an immediate Enter advances to the quantity step instead of being swallowed by a focused select. Every field
  stays reachable by Tab or tap.

### Machine-first

Scanning a Machine barcode acts only as a shortcut: it opens the Machine assignment wizard, dialog title
**`Assign to Machine`**, with three views in one modal dialog and the Machine preselected. The one-shot behavior is
explained in natural operator language ("This assignment applies once and closes after confirmation."); implementation
wording such as "persistent Machine Session" or "armed" never renders.

**Step 1 selects the Machine and a queued PN.**

- The Machine is **always** selected by explicit buttons or cards, never a dropdown.
- The queued PN is selected by explicit buttons or cards by default. It collapses to a compact accessible dropdown
  **only while the dialog's natural height with the full PN button layout cannot fit the dialog height, which is
  capped by the viewport**. The fit is measured from an invisible probe of the full PN button layout at the cell's
  width, so the decision never depends on the currently applied mode; the dialog's `max-height` cap already carries the
  required margin to the viewport edges. The measurement re-runs on viewport resize and on queued-PN changes, and the
  selection returns to buttons as soon as the viewport fits them again. Long PNs truncate without moving the queued
  quantity.
- A light full-width hairline separator, fading toward both ends on the subtle border token, quietly divides the
  Machine and queued-PN selection groups inside the dialog grid.
- A dedicated barcode input (`Scan Machine or queued PN barcode… (ENTER)`) accepts a Machine barcode of the station's
  Area or a queued PN barcode. Every invalid scan (an unknown, other-Area or inactive Machine; a PN without queued
  quantity; Worker, scrap or unsupported barcodes) shows an inline error and changes no selection. Scanning the second
  value completes the pair but never advances or records automatically.
- **Enter on the selection page.** With focus in the Step 1 barcode input, one Enter has exactly one meaning. A filled
  input processes the barcode as a selection scan. An **empty** input advances to the quantity page when a Machine
  **and** a PN are validly selected (whether they were selected by scan, button or dropdown) and does nothing
  otherwise. The event handling consumes the keypress, so one Enter can never both scan and advance.
- **Selection errors use the shared guidance-error style.** Invalid-selection feedback renders as the same inline `✕`
  error guidance as quantity validation (§4.8); this also applies to the Scrap dialog's rejected-barcode note (§4.9).
  The unsupported-barcode copy is operator-facing:
  `Scan a Machine in this Area or a Part Number currently waiting in the Area queue. Your current selections were kept.`

**Step 2** enters the quantity (MAX = queued quantity, which is the default).

**Step 3** is the confirmation summary (`ASSIGNED_TO_MACHINE`, PN, quantity, Area queue → Machine, quantity remaining
queued after assignment, Worker, Station) → `Confirm assignment` → context cleared.

Inactive (maintenance) Machines and Machines of other Areas are rejected with nothing recorded. No Machine context
stays armed after completion or cancellation.

### PN-first

Scanning a PN opens the applicable one-shot wizard (§4.7). Entering Machine assignment from the PN action dialog
preselects the PN, and Step 1's Back returns to that action dialog, the same parent-dialog Back that every child of the
action dialog offers (wizard rules above). Worker scans switch the Worker Session (visible in the header); Worker
identity never determines business correctness.

### Monitoring row actions

In an Area **with Machines**, the `In this Area now` card shows **no row actions**; assignment stays available through
PN scan, Machine scan and the action dialog. Machine-card PN rows carry **two distinct actions**: compact, with the icon
above the label (the icon is never the only source of meaning), each with a clear accessible name, and never merged.

- **`DONE`** (accessible name `Complete Area processing`, success tone) completes processing and moves the quantity to
  the finished state. It opens the manual DONE wizard: a quantity step whose MAX defaults to the quantity at the current
  source position, then a dedicated confirmation view with Action `Complete Area processing`, PN, Quantity, Area,
  Machine when applicable, Result `Finished — ready to move`, Worker, Scan Station and the `AREA_COMPLETED` recorded
  event, then `Confirm completion`. On confirmation the quantity leaves the Machine card and appears under
  `Finished — ready to move` in the Area summary; the current Machine is cleared and the current Area stays the physical
  location. Other quantity of the same PN is untouched.
- **`QUEUE`** (accessible name `Return to Area queue`, neutral tone) returns unfinished or paused quantity to the Area
  queue (`RELEASED_FROM_MACHINE`, quantity MAX-defaulted, then a dedicated confirmation view). QUEUE never marks
  quantity DONE.

**Direct-processing exception.** In an Area **without Machines**, the actively processing quantity has no Machine card
to act from. Exactly the `In this Area now` rows whose portion is actively processing (`In processing`, or
`External processing` at an outside vendor) therefore carry the **single `DONE` row action**.

- It uses the same shared button presentation as the Machine-card `DONE` (accessible name `Complete Area processing`,
  success tone, icon above the `DONE` label) in the shared separated action rail (§4.10). The PN row itself never
  becomes clickable, and the shop-floor touch target is preserved.
- Clicking `DONE` records nothing: it opens the **same manual DONE wizard**, which renders no Machine field. The quantity
  step's MAX defaults to the actively processing quantity of the selected PN portion (a smaller quantity completes only
  part of it). The confirmation view (Action `Complete Area processing`, PN, Quantity, Area, Result
  `Finished — ready to move`, Worker, Scan Station, Recorded event `AREA_COMPLETED`, primary `Confirm completion`) stays
  the only write point.
- On confirmation exactly one immutable `AREA_COMPLETED` Movement is recorded. The selected quantity moves from
  `In processing` to `Finished — ready to move`, the current Area stays the physical location, and no Machine is
  assigned or cleared. A partial DONE leaves the remainder actively processing, other QuantityFlows of the same PN are
  untouched, and Last Scanned PN and Undo follow the manual DONE behavior.
- There is **no `QUEUE`**: a direct-processing Area has no Machine queue to return quantity to.
- Visibility is decided by **explicit semantic data** (a Machine-less, non-terminal Area plus an actively processing
  portion state), never by CSS selectors or Area names. Finished (`READY_TO_TRANSFER`) portions, quantity no longer in
  the Area, and Areas with Machines never show the row action.
- While the application is offline or write actions are blocked, `DONE` is disabled in place: the action rail keeps its
  layout, and no workflow that could lead to a falsely recorded state can open.
- The PN action dialog's `Complete Area processing` choice (§4.7 item 3) remains the scan-driven path to the same
  wizard.

Management → Area Board renders the same shared card **without any row actions** and remains completely read-only.

## 4.7 PN scan resolution

What a PN scan opens depends on where the PN's quantity is.

### Item 1 — The PN has no active Work Order Demand

This includes a PN seen for the first time. The **three-step Receive Quantity wizard** applies. It opens directly on
the scan when the PN has no active quantity anywhere. When the PN does have active quantity, the wizard is an explicit
choice in the dialogs of items 2 and 3, and it then uses the separate-quantity confirmation of Step 3.

The wizard's dialog title is **`Receive Quantity`** (`intake` survives only as the internal workflow name in
identifiers and comments; PROJECT_PROFILE §14). It is equivalent to the Work Orders "Add Part" workflow, with the
editable defaults `Request Type = MODIFY` and `Route Mode = FLOATING`.

**Step 1 — Settings.**

- The PN is shown prominently.
- The description line carries the **PN identity only**:
  - known PN: `This Part Number has no active production demand. Review the details below before receiving quantity.`;
  - new PN: `New Part Number. Verify it carefully; it will be registered when you confirm the receipt.`
- Directly after it comes the ℹ information guidance `No changes are recorded until you review and confirm the final step.`
  The header carries **no sentences that explain the defaults**: the defaults and dates are visible directly in the
  fields.
- Then the settings fields: review and edit Request Type and Route Mode (Planned Route only when `PLANNED`); an optional
  due date (owned by the WorkOrderDemand; its label marks `(optional)` with the shared quiet `.field-optional` element,
  the reusable optional-label style, never CSS pseudo-content); the starting Area/Operation; reason/notes; and the
  internal Work Order reuse behavior. There is no quantity on this step.
- `received_date` defaults to the scan timestamp: the instant the scan resolution issued when the wizard opened,
  carried unchanged through every step and sent with the confirmation, so a receipt confirmed after midnight still
  records the day it was scanned. The station never reads its own clock at the write point; the server derives the date
  on the site calendar.

**Step 2 — Quantity.** The step shows the PN and a compact two-line recap of Step 1.

- Line 1 shows the shared `TypeChip` (`NEW`/`MODIFY`; the same chip as Management → Tracking, mandatory **everywhere**
  NEW/MODIFY renders), the shared **`RouteModeChip`** (mode and route information in **one** chip separated by an em
  dash: `FLOATING — actual trace` / `PLANNED — <route name>`, colored by mode, shared with Tracking §7.2) and the
  Operation chip. **Standalone `·` separators** stand between the chips (`TypeChip · RouteModeChip · Operation chip`);
  the separators are never part of chip content. The shared Request Type and Route Mode chips are aligned to **one
  visual height**: the DEFAULT `RouteModeChip` metrics match the other chips and pills such as `TypeChip`, and only
  Tracking's Details-panel flow header keeps its compact variant (§7.2). The semantic chip colors are unchanged.
- Line 2 is the Work Order line. The labels `WO` and `Due:` keep normal weight, while the WO Number value and the date
  value render bold in a slightly stronger text tone (`.ss-recap .rval`): **text emphasis only, never a badge or an
  action color**. `Notes: …` follows when present.
- Then the ℹ information guidance `Enter the physical quantity received. No default quantity is assumed.` directly above
  the input, then the keypad.

**Step 3 — Confirmation.**

- The structured summary shows: Action `Receive Quantity`, PN, quantity, the same Request Type and Route Mode chips
  (the chip carries the route name, so there is **no separate Planned Route row**), Work Order behavior, the due date
  formatted as `Aug 12, 2026`, starting Area/Operation, destination state, reason/notes, `Existing quantity` when there
  is any, Worker and Scan Station.
- **`Confirm receipt`** is the only write point. The recorded description reads `received into <Area>…`.
- **Separate-quantity confirmation** (PROJECT_PROFILE §14). Where the PN **already has active quantity**:
  - the confirmation view names that distribution (`<Area> × <n> pcs`, joined by `·`; the internal flow id never
    renders);
  - a warning-toned guidance states that the receipt **does not join it** and that `Combine quantities` is where
    quantities are brought together later;
  - the view carries ONE explicit acknowledgement checkbox:
    `Record this as a separate quantity — I reviewed the quantity already in production.`
  - `Confirm receipt` stays **disabled** until it is checked, and Enter on the confirmation view records nothing while
    it is unchecked: the decision is never a keystroke.
  - The distribution comes from the scan resolution, and the server judges the same rule when it writes. Quantity that
    appeared only after the wizard opened comes back as an explicit refusal that recorded nothing; the view then shows
    the server's own distribution, clears the acknowledgement, and the operator answers with the same retry under the
    same `device_event_id`.
- **Work Order behavior.** The summary names the internal blank-number MODIFY Work Order that will be created or reused
  (`—`). When several are plausible, an explicit selection appears **inside the settings view** (a step view in the
  same modal, never a nested dialog, §4.6), and `Next` stays blocked until the operator has chosen one. It is never a
  guess.

**Copy is operator-facing.** The receive, unknown-PN and manual-entry wording explains what the operator must check
and what confirmation does. Internal record-creation or database wording never renders: the create-on-first-use,
case-insensitivity and `received_date` engineering rules stay behind the copy, not in it.

### Item 2 — The PN is not currently in the Area

The source is resolved explicitly.

- **Choosing the intent.** When more than one intent applies here (receiving a transfer, the Repair return, and
  `Receive new quantity` where the **server** reports the `Receive Quantity` entry condition), the `Select an action`
  dialog asks for the intent FIRST and nothing is inferred. Quantity waiting in another Area never makes the transfer
  the only possible intent, and the `Receive new quantity` choice states that the receipt is recorded separately from
  the quantity elsewhere.
- **One valid source** → quantity view (MAX = available source quantity, which is the default) → dedicated confirmation
  view → `Confirm transfer`.
- **The quantity view's step recap** shows the transfer as source → destination Area chips plus the **Work Order
  context line**, never the raw `workOrder` display string. `WO` stays a plain label; the WO Number value carries the
  shared `.rval` text emphasis (`—` is emphasized for an internal blank number); the Operation renders as the shared
  entity chip; and a `NEW`/`MODIFY` segment renders as the shared Request Type chip. The display string is taken apart
  in **one dedicated helper** (`parseWorkOrderLabel` / `WorkOrderRecapLine`), never by repeated ad-hoc parsing in JSX.
- **Several source Areas or QuantityFlows** → an explicit source-selection dialog first. Quantities from several sources
  are never combined silently.
- Back on the confirmation returns to the quantity view with the source selection preserved.
- **Implicit completion at the source.** When the transferred quantity is still actively processing at the source
  (`ON_MACHINE` or direct processing), the transfer implicitly completes that processing. The confirmation identifies
  the completion (`Source processing: Completed at {Area} by this transfer`), and its
  `Recorded events` row states `AREA_COMPLETED, then TRANSFERRED (one command)` (§4.11). Quantity already `READY_TO_TRANSFER` (or still
  queued) at the source transfers with `TRANSFERRED` alone and never duplicates `AREA_COMPLETED`.
- Undo reverses the complete command (§4.5).
- A partial quantity completes and transfers only the selected quantity; the remainder keeps its source state.

### Item 3 — The PN already has quantity in the Area

An action dialog shows only the currently valid choices. It is a selection view, not a confirmation step. The
`Complete Area processing` (DONE) choices sit directly after `Assign queued quantity…` and before
`Receive more quantity…`.

- **Assign queued quantity to a Machine** (only when queued quantity exists): the assignment wizard (§4.6) with the PN
  preselected.
- **Complete Area processing on {Machine}** (one choice per Machine the PN is actively assigned to): the same one-shot
  DONE wizard that the Machine card's `DONE` row action opens, with the Machine preselected and MAX defaulting to the
  quantity assigned on that Machine. Back returns to this action dialog.
- **Complete Area processing** (direct-processing Areas only, when actively processing quantity exists): the manual DONE
  wizard of §4.6 without a Machine field. The same wizard is reachable directly from the `DONE` row action on the
  actively processing `In this Area now` rows (§4.6, direct-processing exception).
- **Combine quantities.** This is the operator-facing term; the UI never says "merge Quantity Flows".
  - **When offered:** only when the PN has two or more separate quantities in this Area that the **server** reports as
    combinable, that is, in one identical production context (same PN, same Area, same processing state, same Machine
    when Machine-bound, same Operation and route context). The station never judges compatibility itself and never
    combines anything automatically.
  - **Choices:** one choice per group the server reports, subtitled by the group's quantities, state and Operation
    (`6 pcs + 4 pcs · In processing · Milling`).
  - **The Combine quantities dialog** is a selection view that lists every portion of the group as
    `{qty} pcs · {state} · {Operation}` (`On {Machine}` names the Machine for Machine-bound portions; internal
    identifiers never render). Every portion is preselected, and the operator may narrow the selection to **at least
    two** (the primary action stays disabled below two). A live result preview shows the outcome
    (`10 pcs · In processing · Milling`).
  - **Next** opens the dedicated confirmation summary (Action `Combine quantities`, PN, Area, `Selected quantities`
    `6 pcs + 4 pcs`, `Resulting quantity` `6 pcs + 4 pcs → 10 pcs`, State, Machine when Machine-bound, Operation,
    Worker, Scan Station, Recorded event `MERGED`), whose primary **`Confirm combine`** is the only write point.
  - Whole portions only: a part of a portion is never combined here (a SPLIT followed by a combine stays a backend
    composition, not an operator workflow).
  - **After success.** Success reads only after the server has confirmed the `MERGED` command. The consumed portions
    leave the Area inventory, the resulting quantity appears in the same Area and state, the totals are unchanged, the
    Last Action records `MERGED · 6 pcs + 4 pcs → 10 pcs in {Area}`, focus returns to the barcode input, and the
    lineage stays visible in Tracking.
  - **Refusal or lost response.** A server refusal (the portions' context changed meanwhile) and a lost response follow
    the shared rejection and unknown-outcome model of §4.11 exactly (`Retry combine`, `Retry the same combine` under
    the same `device_event_id`, `Leave — check the Area`).
  - Back returns to the action dialog.
- **Receive more quantity from another Area** (only when transferable quantity exists elsewhere; with an explicit source
  selection when there are several sources).
- **Receive new quantity** (only where the **server** reports the `Receive Quantity` entry condition: no active Work
  Order Demand of the PN and an Area that can start production): the wizard of item 1. Its choice sits directly
  after the `Add more quantity` choice in the dialog. Its subtitle states that the quantity arrives with its own Work Order and is recorded
  **separately** from the pcs already here. Nothing is merged, and the wizard uses the explicit separate-quantity
  confirmation. It never substitutes for `Add more quantity`, and `Add more quantity` never substitutes for it: the
  correction records found quantity beside existing production quantity, while the receipt introduces quantity with its
  own business demand.
- **Add more quantity.** The operator guidance reads: "Add physical quantity found at this Area that was not transferred
  from another Area. A reason is required."
  - It is allowed for operators, needs no Manager approval, and requires a reason and a quantity, with **no MAX button
    and no default value**.
  - A dedicated confirmation view follows, whose `Recorded event` row states `QUANTITY_ADJUSTED · INCREASE`. It is never
    hidden as an ordinary transfer and never changes the WO Demand requested quantity. `Confirm addition` is the only
    write point.
  - Added quantity enters the Area queue (Areas with Machines) or direct processing (Areas without Machines).
- **Return quantity for repair** (this is the label wording, never "Create REPAIR demand"; offered only when quantity
  elsewhere may deliberately return to this previously visited Area): choose the Repair intent → select the source
  Area/QuantityFlow, the repair quantity and the required reason → a dedicated confirmation view that explicitly
  identifies `movement_reason = REPAIR` → `Confirm repair`. Repair is never converted into a Request Type and is never
  selected automatically merely because the Area appears earlier in history; a normal transfer to a previously visited
  Area stays possible. A partial quantity requires a QuantityFlow SPLIT.
- **Scrap damaged quantity** (§4.9).

## 4.8 Quantity entry

Quantity entry is a wizard step with an oversized keypad **and a real focusable numeric text input**. Entering the step
focuses the numeric entry and selects its value, so a physical keyboard works without clicking first. The input uses
`inputMode="numeric"` and has no native number spinner. The virtual keypad buttons are `type="button"` and
non-focusable (`tabIndex={-1}`, focus kept on the input), so clicking one never leaves focus where Space or Enter would
re-activate it.

**Touch-primary devices.** On a touch-primary device the shared quantity input suppresses the native soft numeric
keyboard with `inputMode="none"` (never a permanently `readOnly` field), while the custom NumPad, selection and caret
behavior, physical-keyboard input, and the MAX / CLEAR / Backspace / Enter / Escape behavior all keep working.

- Touch-primary is detected through **browser capability signals only** (coarse pointer, no hover, real touch points),
  never by user-agent sniffing or a screen-width guess.
- Because mobile browsers honor `inputMode="none"` inconsistently, a **`window.visualViewport` fallback** runs while a
  quantity dialog is open: a meaningful viewport-height reduction consistent with a soft keyboard collapses the
  dialog's on-screen NumPad and tightens the layout, so the input, guidance, validation and dialog actions stay
  visible. The NumPad returns when the viewport does.
- The detection is isolated in one shared, generalized module (`src/components/touch-device.ts`) that also serves the
  Scan Station's main barcode input (§4.4). It is fully reversible and never touches the shared cursor-aware quantity
  editing semantics.

**Fixed visual order.** Every quantity view uses one order: **PN → selected context or instruction →
guidance/validation message → quantity input → numpad → navigation buttons**.

**Guidance kinds.** The guidance sits directly above the input as semantic text, never inside a framed or dashed
summary box and never in a heavy card. There are exactly four visually distinct kinds:

| Kind        | Marker | Used for | Example |
|-------------|--------|----------|---------|
| Information | `ℹ`    | Instructions and information | `Enter the physical quantity received. No default quantity is assumed.` |
| Warning     | `⚠`    | Important constraints | — |
| Action      | `›`    | Required actions | — |
| Error       | `✕`    | Validation errors | `Quantity cannot exceed the 6 pcs currently available at the source.` |

There is no marker-less neutral kind. Every guidance carries a small marker plus a thin accent edge in its semantic
tone, so color is never the only distinction, and validation reads strongest. The Receive Quantity and Add more
quantity instructions, the Repair MAX and default statement, and the Scrap status line are ℹ information guidance. The
validation message updates next to the input, entered values are preserved, and **Next stays disabled while the
quantity is invalid**.

**Cursor-aware editing.** Every shared Quantity NumPad, physical keyboard and on-screen keypad alike, applies ONE shared
value-editing model:

- a digit **replaces the selected range at its exact position** (the selected default `4` becomes `2`, never `42`);
- a digit **inserts at a collapsed caret** (`23` with the caret in the middle becomes `243`, never `234`);
- a digit **appends to the end only when the input is not focused** or no usable caret exists;
- Backspace removes the selection or the digit before the caret (the last digit in the unfocused fallback).

After an on-screen keypad edit, focus stays on (or returns to) the quantity input with the caret directly after the
inserted digit or replaced range; the caret jumps to the end only in the append fallback. Digits-only sanitization, the
maximum digit count, and paste and input-method-editor (IME) sanitization all still apply.

Physical keys are handled centrally:

```text
0–9       replace the selection / insert at the caret / append (unfocused fallback)
Backspace remove the selection / the digit before the caret (last digit unfocused)
Delete    clear
Enter     advance to the confirmation view (never directly to a write)
Escape    cancel the whole workflow
Space     ignored
Other     ignored
```

Enter on the quantity step always means "advance to the confirmation view", regardless of which virtual keypad button
was clicked previously. Enter on the confirmation view performs the final Confirm. Escape always cancels the entire
wizard.

**MAX and defaults.** Transfer and assignment show **MAX** and default to it; a smaller valid quantity may be entered.
Add More Quantity has no MAX and no default. Next is blocked for 0 and for quantities above the available source. All
server-side quantity validation requirements remain (the mockup validates on the presentation side only).

**Partial quantity.** Every production wizard on existing quantity (Transfer, Assign to Machine, QUEUE, Machine DONE
and the direct-processing DONE) accepts any whole quantity from **1 to MAX**.

- The guidance names the available quantity as MAX and says what a smaller quantity does
  (`the rest stays queued / on {Machine} / in processing`).
- `0` and anything above MAX are refused in the view with an explicit message and never submitted.
- A quantity below MAX renders on the confirmation summary as `{n} pcs of {MAX}` with a **Remaining** row
  (`Remaining at source`, with where it stays; `Remaining queued after assignment`; `Remaining on {Machine}`;
  `Remaining in processing`) and in the final question (`The remaining {m} pcs stay …`).
- The station submits only the chosen quantity. The **server** splits the Quantity Flow inside the same command (the
  response names the consumed source and the remainder); the client never splits anything, and the full quantity keeps
  the whole-flow workflow with no split at all.
- After the server has confirmed, the success notice names the remainder (`SPLIT · {m} pcs remain …`), the Last Action
  reads `SPLIT + {event} · … · qty {n} of {MAX}`, and the re-read inventory shows both resulting quantities in their
  states.

## 4.9 Scrap workflow

The canonical term is `SCRAPPED`; the UI wording is "Scrap damaged quantity".

**Instruction copy is for operators only.** The workflow instruction reads
`Scan PF:SCRAP once for each damaged piece, then enter one reason for the total. Nothing is recorded until confirmation.`
The `SCRAPPED` event name never appears in instruction copy; it renders only in the confirmation summary's
`Recorded event` row and on the audit and history surfaces (§3 rule 10).

One dedicated, context-sensitive barcode, `PF:SCRAP`, drives the count:

- it is accepted **only** inside the Scrap workflow; the main scan input rejects it;
- each scan increments the pending scrap counter by one, and counting changes no production state;
- the pending count can be corrected (−1, `Remove one`) or reset (`Reset`) before confirmation;
- a common scrap reason is mandatory;
- the available, pending scrap and remaining quantities stay visible while counting;
- the **dedicated confirmation view** shows PN, Area, Machine if applicable, original available quantity, scrap
  quantity, remaining active quantity, Worker, Station and reason as a structured summary;
- Cancel on any step discards the pending count with no write. `Confirm scrap` on the confirmation view is the only
  write point and creates **one** auditable `SCRAPPED` operation for the total.

Scrapped quantity is displayed wherever the PN appears operationally (Scan Station, Production Board, Area Board,
Tracking).

## 4.10 Area and Machine monitoring layout

This shared layout is also used by the Area Board detail (§6.3):

```text
[ In this Area now ] [ Machine cards grid                    ]
[ fixed left col   ] [ Machine 1 ][ Machine 2 ][ Machine 3   ]
[ grows vertically ] [ Machine 4 ][ Machine 5 ]              ]
```

**Columns.**

- `In this Area now` is the left column. Machine cards occupy only the right-side grid, wrap to additional rows
  **within** that grid, and never wrap underneath the left card (`align-items: start`).
- The left card may grow vertically with its PN list but never grows without bounds (its column is capped). It is wide
  enough to display a normal full PN such as `2027-60-8114-00` with reasonable spacing between the PN on the left and
  the context and quantity information anchored on the right. Extremely long PNs truncate with an ellipsis and a
  tooltip.
- The single-column fallback is **container-aware**, not one arbitrary viewport breakpoint: when the containing view
  can no longer fit at least one usable Machine card beside the summary, `In this Area now` fills the full row width and
  the Machine cards move below it at their normal width.
- Areas without Machines render only `In this Area now`, spanning the full width, with no blank Machine region.

**The `In this Area now` card.**

- It uses the Area color along its top edge and presents its compact Area description (for example
  `Turning cell · Lathe 1–4`) inside the card header block exactly like the Management → Area Board → All Areas column
  headers, never as a separate framed or strongly divided block.
- It carries **no statistics block**: the Scan Station header is the single totals surface (§4.3). The Area Board keeps
  the statistics row on its summary card through the shared component's `showStats` option.
- It uses the same PN presentation language as Management → Area Board → Area, with these quantity groups: `On Machines`
  and `Area queue — awaiting Machine` when the Area has Machines; a direct `In processing` group otherwise (`Stocked` for
  the terminal Stockroom); and a `Finished — ready to move` group for `READY_TO_TRANSFER` quantity.
- DONE rows use a clear success or ready status (`done` chip; status line `Finished — ready to move`, or
  `Finished at Lathe 3 — ready to move` when a Machine completed the work, as completion context). They show their
  quantity, never imply that the quantity is still assigned to that Machine, and remain located in the current Area
  until transferred. After DONE is confirmed, the quantity leaves the Machine card and appears in this group; Machine
  cards show only actively assigned quantity.

**PN row layout.** Each PN row is an explicit grid: production information on the left, and the optional action in its
own separated right-side cell.

```text
┌────────────────────────────────────────────────────────────┬──────────┐
│ Hot + PN                         Machine/Queue · quantity  │          │
│ WO Number · Job Numbers                         days left  │  ACTION  │
│ in-Area status                              time in Area   │          │
│ n scrapped                                                 │          │
└────────────────────────────────────────────────────────────┴──────────┘
```

| Line | Left | Right |
|------|------|-------|
| 1 | Hot indicator and PN | Machine, queue or done context, and quantity (formatted with `pcs`) |
| 2 | Work Order Number (`—` when blank) and external Job Number (may truncate with an ellipsis; the complete value stays available through a `title` tooltip) | The derived due countdown (§3 rule 12: `N days left`, `due today`, `overdue N days`, `No due date`) |
| 3 (when meaningful) | In-Area status (`Awaiting Machine`, `On Machine`, `In processing`, `External processing`, `Finished … — ready to move`) | Time in Area |
| 4 (only when scrapped quantity exists) | `{n} scrapped` | — |

Scrapped quantity appears **only** as this readable text; there is no compact scrap indicator, and scrap is never
displayed twice. Rows inside an Area summary show their Machine, queue or completion context chip. Rows inside a
specific Machine card pass the explicit `showContext={false}` presentation option, so the Machine already identified by
the card header is never repeated per row (never hidden through fragile CSS selectors).

**Action rail.**

- When a row has actions, they sit in a dedicated right-side action cell, separated by spacing and a subtle vertical
  border, vertically centered and with a predictable minimum width, never immediately beside the PN, context or
  quantity. Machine-card rows stack `DONE` above `QUEUE` inside that cell (§4.6).
- At very narrow widths the actions may stack below the content but stay visually separated.
- PN rows are never clickable as a whole.
- Action visibility: in an Area with Machines, `In this Area now` shows no row actions and Machine-card rows show
  `DONE` and `QUEUE`. In a direct-processing Area (no Machines), the actively processing `In this Area now` rows show the
  single `DONE` (§4.6 direct-processing exception; never `QUEUE`, and never on finished rows). The Area Board passes no
  actions at all (read-only).

**Machine-card operational state with age.**

- Every Machine monitoring card's header status pill carries the derived operational state **with the elapsed time in
  that state** (`running · 1h 24m`). It is derived at render from the Machine's shared `stateChangedAt` timestamp plus
  the shared minute-precision UI clock (§3 rule 12; PROJECT_PROFILE §8.6). `formatStateAge` delegates to the shared
  duration language (`18m`, `1h 24m`, `2d 03h`, `<1m`); a formatted duration is never stored. Every surface therefore
  shows the same age, and the age **keeps ticking while the view stays open** (Machines table, monitoring cards).
- The operational-state tones are shared by every surface: **Running success, Idle warning, Maintenance error**,
  always rendered beside the written state word, never color-only.
- A maintenance card's empty text carries the maintenance context: `Under maintenance · Unavailable for production`,
  followed by the note and `expected back YYYY-MM-DD` when present.
- The card's totals line `N pcs assigned · M PN(s)` carries two semantic value classes, `machine-total-pcs` and
  `machine-total-pns`. **Both share the secondary muted neutral of the surrounding line text and of the header
  `Total pcs` total. The PN count deliberately stays on this secondary neutral even though the header `Total PNs`
  statistic carries the primary text neutral** (§4.3). These are semantic classes, never positional `<b>` or
  nth-child selectors.

**Derived Time in Area and due countdown.**

- A row stores only fixed timestamps: the Area-entry timestamp (`enteredAreaAt`, null where elapsed time does not
  apply) and the due date (`due`, ISO or null). The `Time in Area` text, its sort order and the due countdown are all
  derived at render from them plus the shared minute-precision UI clock (§3 rule 12), in the one shared duration and
  countdown language.
- A card where a countdown does not apply (the Stockroom `allocated 50/50`) shows its verbatim status through the
  optional `dueText` instead.
- Stored formatted strings (`timeInArea`, `timeInAreaMinutes`, `dueDays`, `dueClass`) do not exist in the row model.
- **Expected duration** (PROJECT_PROFILE §17). The row also carries the fixed `expectedBy` instant that the shared
  inventory states per flow: the entry time plus the position's effective expected duration (the current Assigned Route
  Step's snapshot value, otherwise the Operation default), absent when neither applies. Its `Time in Area` turns the
  warning tone (`tia.long`, with the written `Exceeds the expected duration` tooltip) once the shared clock passes it; a
  row without one is never flagged. This is advisory only: the Scan Station's row actions are exactly the same either
  way.

**Shared components.** The presentation is built from shared components (`AreaPnRow`, `AreaSummaryCard`,
`MachineMonitoringCard`, `DueStatus`, `QuantityStatus`: domain-named, no vague helpers), so the Scan Station and the Area
Board never drift apart. Changing the shared row affects both surfaces, and both must be verified.

## 4.11 States

| State           | Behavior |
|-----------------|----------|
| Loading         | Skeleton panels; scan input disabled with "Connecting…" placeholder |
| Empty inventory | "No production in this Area" placeholder row |
| Disconnected    | Persistent OFFLINE banner (§3 rule 6: exact one-statement copy with the quiet `Retry connection` action); production write submission disabled; scan inputs read `Disconnected — scanning disabled`; loaded read-only data stays visible |
| Updated         | Persistent update notice (§3 rule 13: exact copy with `Reload page`); production write submission disabled; scan input `PartFlow was updated — reload to continue scanning`; reloads itself after 60 s when no dialog is open (§3 rule 13) |
| Reconnected     | Banner clears; scan input re-enabled and refocused, ready for the next scan |
| Error           | Red floating notification (about 8 s, closable); input cleared and refocused, ready for the next scan |

### How the station works with the server

The Station Selector and the Scan Station are production UI backed by the `/api` surface. The rules below apply to every
station workflow.

**Loading and freshness.**

- The Station Selector lists the active Scan Stations with their Department, Area and active Operations from the
  configuration.
- A station loads its bound Area context, the Area's Machines and the live Area inventory (`In this Area now`); the
  header totals reconcile from that inventory. The Area inventory is the server's state split: `Area queue — awaiting
  Machine`, the Machine cards (every active Machine of the Area, holding ONLY its `ON_MACHINE` quantity, with several PNs
  possible on one card and the derived Running / Idle / Maintenance state with its age), and `Finished — ready to move`
  (Area summary only).
- If the Area inventory or the Machines fail to load, the station shows an error with Retry, never an empty Area. No
  totals render before the first inventory response.
- PN barcodes and manual entries resolve **on the server**. An invalid or unknown barcode is refused with nothing
  recorded.
- **Configuration freshness.** When a scan resolves to a different Area than the one rendered (the station was rebound
  after the page loaded), the pending workflow is discarded, the context and inventory reload, and the operator rescans,
  so a confirmation always names the destination just resolved.
- The station re-reads its context in the background on every resolved scan and whenever a Machine-card `DONE` /
  `QUEUE` or the direct-processing `DONE` opens its wizard. A failed background re-read keeps the station, the open
  dialog and the Worker pill as last read; it never shows the error page.
- **The server judges the Area mode.** Whether the Area has Machines is the server's judgement (`has_machines` on the
  station context and the Area inventory; it follows from the Area's active Machines, PROJECT_PROFILE §12), and every
  flow's holding state comes from the read models (`PROCESSING` for quantity that an Area without Machines processes
  directly). The station never infers direct processing from an Area name or a CSS class. It follows the **freshest**
  server read: the rendered inventory (re-read after every action, carrying the flow states derived under the same
  mode) decides the presentation and the row actions, and the PN action dialog offers its choices from the resolved
  flows' states. A first Machine added, or the last one retired, after the station loaded is reflected by the next read
  and is never overridden by the context loaded earlier.
- **Recorded Operation.** Every flow carries the Operation **recorded** on it (code, name, external flag, activation).
  Existing quantity keeps that Operation even after it was deactivated and the station no longer offers it for new
  arrivals. In an Area without Machines, a row reads `External processing` when the flow's recorded Operation is an
  external one, and `In processing` otherwise.

**Writes: success, rejection and unknown outcome.**

- **Success only after the server.** Every command reports success **only after the server has confirmed the write**.
  The station then reloads the station context, the Area inventory and its Machine cards from the server, records the
  Last Action, clears the dialog context (no Machine or PN context survives completion or cancellation) and restores
  focus to the barcode input.
- **Rejection.** A server rejection (for example: the quantity moved or changed state meanwhile, a Machine was retired
  or put under maintenance after the scan, the station was rebound meanwhile, an Operation was deactivated, or a
  Planned Route deviation is not yet confirmed) stays in the dialog with the server's reason, and nothing is recorded.
  The station context and the Area inventory are re-read from the server at once, and the dialog then offers only
  `Retry …` or Cancel. Back is withdrawn, because it would return to a selection or source snapshot that the server has
  already refused.
- **Unknown outcome.** A command whose response is lost (a network or transport failure after submission) is presented
  as an **unknown outcome**, never as "nothing was changed". The dialog freezes the confirmed intent (for example
  source, Operation, reason) and offers only the exact same retry (`Retry the same …`), which keeps its
  `device_event_id` so that a committed command replays instead of recording twice, or leaving to re-read the Area
  (`Leave — check the Area`).
- **One-shot write model.** Every wizard submits under the shared one-shot write model (`useOneShotWrite`), with the
  same `device_event_id` on every retry.
- **Partial quantity.** The server's split (`source_quantity_flow_id`, `remainder_quantity_flow_id` and
  `remainder_quantity` on the command responses) is reported in the notice and the Last Action, and the Area inventory
  re-read from the server carries both resulting quantities (§4.8).
- No optimistic state is ever shown.

**Transfer details.**

- The explicit source selection always presents several valid sources; they are never selected automatically or
  combined.
- The destination Operation is resolved from the station Area's configuration: a single active Operation resolves
  itself, and several require an explicit choice. At a destination with several active Operations the operator always
  chooses explicitly. The Planned Route's expected Operation is marked as guidance only, and choosing another one is
  confirmed as an `OPERATION` route deviation with its reason. A Planned Route deviation is confirmed in the same
  summary with a mandatory reason.
- The transfer confirmation summary shows Source → Destination, PN, quantity, Operation, Work Order context, Route,
  Scan Station and the recorded event.
- A `transfer_blocked_reason` (a terminal Area) refuses every Receive/Transfer action, even when the PN already has
  quantity in the Area.
- A transfer of `ON_MACHINE` quantity announces in its recap and summary that processing at the source is completed by
  the transfer (`AREA_COMPLETED, then TRANSFERRED (one command)`), and reports both recorded events after the server has
  confirmed the whole command.
- A transfer whose source quantity is directly processing announces the same in its recap
  (`still in processing at {Area}: transferring it completes that processing first`) and summary
  (`Source processing: Completed at {Area} by this transfer`;
  `Recorded events: AREA_COMPLETED, then TRANSFERRED (one command)`). Quantity already `READY_TO_TRANSFER` (or queued)
  shows `Recorded event: TRANSFERRED` and never a second completion.
- A transfer INTO an Area without Machines names its destination as `{Area} — direct processing`, and the quantity
  arrives as `In processing`.

**Machine assignment, DONE and QUEUE details.**

- Scanning a `PF:MACHINE:` barcode on the main input resolves the Machine **on the server**. A retired, other-Area or
  maintenance Machine, or an unknown Asset Tag, gives an actionable error notice with nothing resolved.
- `Assign to Machine` lists the Area's queued flows as explicit choices; several separate quantities of one PN are
  several choices. Its summary shows Action, PN, Quantity, Source `Area queue`, Destination Machine, Scan Station and
  `ASSIGNED_TO_MACHINE`.
- A queued PN's action dialog offers assignment per queued quantity (the same wizard with the PN preselected), and
  `Complete Area processing on {Machine}` per Machine-assigned quantity.
- The Machine-card `DONE` and `QUEUE` wizards show the Result `Finished — ready to move` or `Queued — awaiting Machine`,
  with the recorded event `AREA_COMPLETED` or `RELEASED_FROM_MACHINE`.
- The direct-processing `Complete Area processing` choice is offered once per directly processing Quantity Flow of the
  PN. Its quantity view's recap names the recorded Operation, and its confirmation summary also shows the **Operation**
  (the recorded one). The confirmed completion is submitted to `POST /api/scan-stations/{id}/area-completions`
  **without** `machine_id`; the server records exactly one `AREA_COMPLETED` with no Machine. Afterwards the quantity
  moves from `In processing` to `Finished — ready to move` and loses its `DONE`, and the Last Action records
  `AREA_COMPLETED · {Area} processing → Finished — ready to move`.

**Combine quantities details.** `Combine quantities` is offered for exactly the groups that the PN resolution reports
(`combine_groups`, the server's judgement by the merge command's own rule). It opens `scan-station-combine-dialog.tsx`
(a selection whose portions use `aria-pressed`, a live result preview, the confirmation summary and `Confirm combine`),
submits `POST /api/scan-stations/{id}/merges` with the selected flows only, reports success after the server has
confirmed the `MERGED` command, re-reads the Area and restores focus.

**Correction workflow details.**

- `Add more quantity` asks for an explicit Operation choice when several are active.
- `Return quantity for repair` is offered for exactly the transfer candidates that the **server** marks as
  repair-eligible (the station's Area was previously visited); the station never judges the history itself, and a
  normal transfer to the same Area stays a separate, unchanged choice. A PN with **no** quantity in the Area whose
  sources include a repair-eligible one opens the same explicit intent choice: normal Transfer, keeping every valid
  source, or Repair, narrowing the source selection to the repair-eligible ones. Repair is therefore reachable there
  too and is never inferred. Repair uses the explicit source selection when several sources are eligible, then the same
  transfer wizard carrying the explicit Repair intent: quantity 1 to MAX, with a partial return split by the server
  inside the same command; the mandatory repair reason, separate from any route-deviation reason; and a confirmation
  that identifies the Repair intent on its `Recorded event` row (`TRANSFERRED · REPAIR intent`), with `Confirm repair`
  as the only write point.
- The PN action dialog offers one `Scrap damaged quantity` choice per in-Area quantity.
- The PN resolution's `scrapped_quantity` renders on the action dialog and on the no-quantity dialog.

**Undo details.**

- The station keeps this session's completed commands as a stack and walks it newest-first through the server's undo
  preview, which is the authority on eligibility. The walk uses reads only and skips a newer command that the server
  judges ineligible until an eligible one is found.
- With nothing eligible, `⟲ UNDO` is disabled and a notice names the reason. This verdict is revalidated on every later
  server sync and never cached for good. Undo is also disabled with nothing to reverse or while writes are blocked.
- The Last Scanned PN block shows the target derived by the server, never the raw newest session entry, which may
  already be ineligible.
- The first eligible preview renders the structured summary confirmation: original action, quantity, source and
  destination, Machine (its identity is never dropped: a Machine of another Area falls back to the explicit
  `Machine #<id>` label), timestamp and the exact effect of the reversal. The Undo summary shows the original `Worker`
  and `Reversed by` as the server previews them.
- The confirmed reversal is submitted under the Undo's own `device_event_id`. After the server has confirmed, the
  original history stays visible, and the Last Scanned PN advances to the next eligible previous operation as the walk
  re-resolves it from the server. With nothing eligible left, the block reports no reversible action and Undo is
  disabled at once. The Area is re-read and focus returns to the barcode input.
- Stocked quantity is never undone: the Undo stack skips it (§10).

**Receive Quantity details.**

- The **server** judges where the wizard applies (`intake_available`: the PN has no active Work Order Demand and the
  station's Area can start production), and the station never guesses it.
- The Planned Route field appears only for `PLANNED` and lists only Routes that start at this Area. The settings step
  asks for an explicit Operation choice when the Area configures several.
- The received date follows the scan: the resolution issues the instant, the wizard carries it unchanged and sends it
  back, and the server derives the calendar date on the site calendar.
- A `selection_required` refusal returns to the settings view under a NEW `device_event_id`: a refused request wrote
  nothing, so the corrected intent is a different one. A lost response is frozen behind the SAME `device_event_id` with
  `Retry the same receipt`, which a committed receipt replays however long the retry takes.
- The receipt creates a SEPARATE quantity and changes nothing about the existing one.
- After the server has confirmed, the station context and the Area inventory reload and the barcode input regains
  focus. The receipt joins the session log **without** becoming the Undo target: a receipt also creates or raises
  business demand, which a Movement-level reversal never rewrites.
- A receipt needs an enrolled station device and the Receive quantity into an Area permission of the role applied at
  Scan Stations (§4.13).

**Worker details.**

- Every production action records the Area's Worker, and every production confirmation summary shows it as the `Worker`
  row before `Scan Station` (omitted in Disabled Areas).
- In Disabled and Fixed Worker Areas a badge scan is answered with `Worker badge scans are not used in this Area` and
  changes nothing. In Scanned-session Areas it signs in, switches or refreshes the Worker Session (§4.12). Either way it
  never changes the Last Scanned PN.
- For the badge-confirmation gate of `DONE`, `QUEUE` return and Undo (§4.6, §4.12), the server decides the gate form. A
  gate badge signs that Worker in and is recorded on the action, and a refused gate keeps the draft, selections and
  quantity.

**Development preview.** In a development build, the approved presentation of the station workflows is also available
as a **development-only mock preview** (`?preview=mock` on a Scan Station route, remembered for the browser session).
It never enters a production build.

## 4.12 Worker identification and sessions

**A Worker is not a User.** A **Worker** exists only within the Scan Station scope: it is the audit identity of the
person operating the station (PROJECT_PROFILE §7 Worker, §8.13, §19). A **User** is an application account for
Management, Administration and every screen outside the Scan Station. A Worker badge never logs into PartFlow, and a
Worker Session is never an authentication session. The two are managed on separate Administration screens (Workers and
Users, §9) and are never merged into one entity.

**Badge barcode.** The Worker badge barcode is the barcode already printed on the company's existing employee badge; no
dedicated `PF:WORKER:` barcode exists or is required. A scanned value that does not start with `PF:` is **matched
exactly after canonicalization (trim, uppercase)** against the badge barcodes of **active** Workers, so letter case
never matters. An unknown or ambiguous value is rejected with nothing recorded and is never guessed (PROJECT_PROFILE
§10). Workers have no employee number; a Worker record carries a stable internal id, name, badge barcode, avatar and
active status.

**Worker ID modes.** Worker identification is configurable per Area (§9 Areas table):

| Mode            | Effect |
|-----------------|--------|
| Disabled        | No Worker is recorded; the station header renders no Worker pill. |
| Fixed Worker    | The Area's configured Worker is recorded automatically. |
| Scanned session | Badge scans open and switch Worker Sessions. |

The header pill presentation per mode is specified in §4.3. In Disabled and Fixed Worker Areas a scanned badge performs
no sign-in: a matching badge is answered with an explanatory notice and no state change.

**Scanned session: sliding inactivity timeout.** A Worker Session ends after a configurable period of inactivity, never
at a shift boundary.

- The timeout is configured in **Administration → Worker sessions** (§9): one default policy value with optional
  per-Area overrides. There is no shift-schedule management, and the timeout is never inferred from the number of
  Workers in an Area.
- Every **valid production interaction** at the Scan Station refreshes the timeout: a successfully resolved PN or
  Machine scan, a confirmed production command, a valid badge scan. Invalid, unknown or rejected scans never refresh it.
- Scanning a different active Worker's badge switches the active Worker **immediately** and starts that Worker's
  session; there is no sign-out step.

**Badge modal: sign-in and expiration.** In a Scanned-session Area, the Scan Station requires an active Worker Session.

- Opening the station without an active session shows a **blocking modal** asking for a badge scan; the station surface
  behind it is inert.
- When the session times out, **only the Scan Station is blocked**: the same modal appears with exactly
  `Worker session expired` and `Scan your badge to continue.` (never `unlock` wording). Every other application view is
  unaffected; the block exists only on the Scan Station route.
- If a production dialog is open when the session expires, the dialog's draft and selections are **preserved**
  underneath, but production confirmation stays blocked until a valid badge scan. The modal renders above the dialog.
- After a valid badge scan the modal closes, the interrupted workflow continues where it was, and focus is restored
  according to the station focus rules (§4.4): into the open dialog if one exists, otherwise to the main barcode input.
- While the page is outdated (§3 rule 13), the modal shows `Reload page` and its badge input is disabled with the
  outdated scan copy. The station does not reload itself while the modal is open.
- Development builds show the shared `DevNotice` demo badges inside the modal (§3 rule 10). Each badge value is
  clickable and simulates a badge scan through the same badge-matching path, exactly like the scan card's demo barcodes
  (§4.4).

**Badge confirmation of sensitive actions.** Separately from the session sign-in modal, the Administration → Worker
sessions options (§9) can require a **badge-scan gate** as the final step of `DONE`, `QUEUE` return and Undo in a
Scanned-session Area (§4.6). The gate restates the key facts of the pending action. Any active Worker badge confirms:
the badge identifies the confirming Worker, who is recorded on the action and signed in exactly as by a normal badge
scan (switching the session when it is a different Worker, and refreshing the timeout). Unlike the sign-in modal, the
gate can be cancelled: Cancel or Escape returns to the confirmation summary with nothing recorded.

## 4.13 Device enrollment

A Scan Station works only from a browser that an administrator has enrolled for that station (PROJECT_PROFILE §15,
§20). Enrollment authenticates the terminal, never a person: Worker identification stays as described in §4.12, and a
Worker badge never authorizes anything.

- **Enrollment screen.** A station that cannot load because it has no enrolled device (the browser holds no device, the
  device was revoked or replaced, or it is enrolled for another station) shows an enrollment panel in place of the
  station. The panel names the Station ID and asks for the enrollment code that an administrator issued for this station
  (Administration → Scan Stations, §9). The code is ten characters shown as `XXXXX-XXXXX`; it is accepted in any letter
  case and with or without the hyphen or spaces. An unknown or inactive Station ID still shows the explicit
  unavailable-station error (§4.1) instead of the panel.
- **During work.** When a request is refused because the device was revoked or replaced while the station is open, the
  same enrollment appears as a blocking dialog, like the Worker badge modal (§4.12). Open drafts, selections and the
  Worker Session are preserved underneath, and nothing further is recorded until the device is enrolled again. If the
  outcome of the last action is unknown, the station keeps it and asks the operator to confirm the same intent again
  after enrolling; it never claims that nothing was recorded.
- **Connectivity.** Enrolling is a write: it is blocked while disconnected (§3 rule 6), and nothing is queued. In
  production mode the panel keeps a `Station Selector` button; the mode itself never changes who is enrolled.
- **A rebound station.** A station whose Area was changed in Administration while it was open reloads its context
  without asking for enrollment: the enrollment belongs to the Station ID.
- **Actions the station role does not grant.** The station hides the actions that the role applied at Scan Stations
  (§9) does not grant: the PN-action choices, the Machine card and row actions, the allocation steppers and `⟲ UNDO`
  (§4.5). A scan that would open only such an action shows a red notification and opens nothing. A refusal of an action
  the role does not grant (on a stale screen) is an ordinary red rejection with nothing recorded. A badge scan refused
  because the badge-scan permission is missing is shown inside the Worker badge modal (§4.12), which stays open.

---

# 5. Production Board

The Production Board is read-only and full-screen, for large shared displays. It has no interactive elements except an
optional settings gesture that only an administrator can use. **The board is Department-wide only: there is no
per-Area filtered mode.** Per-Area monitoring is the Area Board's responsibility (§6: All Areas overview and per-Area
detail), and the two views never duplicate each other.

### Header and identity

- **Header clock.** The header shows the live local date **and time**, driven by the shared **second-precision UI
  clock** (§3 rule 12: the clock component subscribes to `useUiClock('second')`; it is never a static mock timestamp
  and never a component-owned timer). The clock reads as **one coherent block**: the time is the strongest element,
  with the date directly beneath it as a clearly associated secondary line. It is plain text on the header surface (no
  border, no background), so it never resembles an editable control, and it is sized for large shared displays in both
  themes.
- **Board title with live status.** Auto-refresh keeps the board current, and the board title carries the live status.
  The header identity group is styled like the Scan Station header identity: a `Machine Shop` **Department line**
  (small uppercase, `.ss-head .dept` styling) above the **`Production`** board title in the Scan Station Area-title
  typography, with the **`● Live` operational status directly after the title**, slightly separated, smaller than the
  title, and **in the CURRENT connection tone**.
  - While the shared connectivity state is healthy, the status text reads in the success tone, and its rounded-square
    dot (in the style of the Area indicator) pulses with the one shared `@keyframes pf-heartbeat` animation
    (`styles/global.css`). That animation sits behind **every** healthy connected-status dot in the application, namely
    the application and Scan Station connectivity chip (`ONLINE`) and this status dot, so "alive" looks the same
    everywhere.
  - Connecting and offline states never pulse: the dot and the status text turn the warning tone, and a small explicit
    `Feed stale — reconnecting` note appears beside them, so status is never color-only. `prefers-reduced-motion`
    disables the pulse.
  - The labels keep distinct meanings: `ONLINE` is application and Scan Station connectivity; `● Live` is the board's
    operational status.
  - Both the standard and the Kiosk header (§5.1) show the **same** identity group and live status through one shared
    component and the shared connectivity state, never with a second `ONLINE` chip beside it.

### Columns

The columns (PROJECT_PROFILE §21) are **No. · Part Number · Areas & Quantities · Time · Due Date · Total Days · Job
Numbers**. Job Numbers is deliberately last.

**Content-driven column sizing.** Column widths come from semantic `colgroup` classes, never from a fragile set of
arbitrary fixed pixel widths.

- No. stays compact. Part Number, Due Date and Total Days never wrap.
- **Due Date and Total Days keep minimum widths derived from their complete headings plus cell padding**: the headings
  never wrap, and Total Days is never visually glued to Job Numbers.
- **Job Numbers receives a larger bounded share**, so representative Job Number rows do not wrap unnecessarily, while
  Areas & Quantities · Time gets enough width for its structured data without absorbing excessive unused space.
- Narrower widths degrade by wrapping Job Numbers, never by clipping.
- After every column has enough width for its content, the remaining table width is distributed across Part Number,
  Areas & Quantities · Time, Due Date and a larger bounded Job Numbers share. No. and Total Days stay compact, with Due
  Date and Total Days never narrower than their own headings (which never wrap).

**Part Number** renders on a single line with **intrinsic sizing**. A PN shorter than the standard 15 characters still
reserves a real `15ch` monospace content minimum (in the PN's own font metrics, not an arbitrary fixed pixel width); a
15-character PN such as `2027-60-8114-00` displays fully; and a longer PN expands the column to display fully. PNs are
**never truncated** on this board. No Hot-chip space is reserved inside the PN width. The name and revision appear as a
secondary line that wraps beneath the PN without widening the column.

**Hot presentation.** The flame renders in the **No. column** (`1 🔥`, `2 🔥`) with an accessible label such as
`Row 1, Hot priority`. The Part Number cell contains **only** the PN and description: never `🔥#n`, and never a second
flame elsewhere in the row. The footer legend describes the No.-column flame. The other views (Area Board, Tracking,
Priority) keep the shared `🔥#n <PN>` presentation (`HotPn` in `src/components/indicators.tsx`).

### Areas & Quantities · Time

This column uses a **stable grid** with **explicit presentation data**. Each location row carries the Area identity or
label, an optional separate Machine name, an optional External activity, the quantity, the state and the time. It is
never one composite display string (`External — Plating`, `Lathe 2 machine`) that would have to be parsed back apart.

**Track alignment.** Every row follows **`Location | Quantity | State/activity | Time`**. The location, quantity,
state and time positions, including the `total` label and the total quantity, line up at the same horizontal positions
across **all visible PN rows**, and the quantity track never shifts with the length of an individual row's Area or
Machine label.

- **Measured shared content-driven tracks.** Because every PN row lays its location fields out in its own grid,
  cross-row alignment comes from a **shared measured track width**. Each board commit measures the widest real value of
  each field across **all** rows in the hidden measurement copy (which always lays out from pure content, so measuring
  and applying never feed back into each other) and publishes the four widths as custom properties that every visible
  row consumes. Small `ch` fallbacks apply only for the moment before the first measurement, and the cell padding is
  tightened to 12 px and 8 px.
- Every row therefore reserves exactly the width that the widest actual content needs: the four positions line up
  across all PN rows **and** across page rotation, no leftover air pushes the fields apart, and no typography shrinks.
- An exceptionally long Area or Machine name **widens the shared track** instead of truncating (never an ellipsis), so
  nothing overlaps or clips for Machine, queue, direct processing, External activity, DONE, Stockroom, long durations or
  long Machine names.
- A trailing spacer track keeps the leftover cell width in one place, so the time stays clearly separated and aligned
  **near its location data** instead of drifting to the far cell edge, and the location details read as one group.

**Location rows.**

- Actively Machine-assigned quantity renders as `[Area color dot] [Lathe 2 chip]  4  on machine  1h 05m`. The Machine
  name sits in a compact rounded chip (label look: subtle chip or panel background, subtle border, rounded corners, both
  themes, no action color). The **full Machine name is always visible, never an ellipsis**, and the full user-facing
  state text is **`on machine`** (no abbreviation exists anywhere).
- Queued quantity keeps a `queue` label; direct processing shows `processing`.
- **External locations show only `External`** as the Area text, with the specific activity (`plating`, `vendor`,
  `painting`, …) as a **light informational chip in the state position** replacing the generic `processing` label.
  DONE External quantity shows `done` as its current state, with the activity as secondary tooltip context only.
- DONE (`READY_TO_TRANSFER`) quantity is visually distinct from active Machine processing: it shows the current Area
  with a concise `done` state, never `on machine`, and never the Machine as the current executor. A Machine that
  completed the work appears only as secondary completion context (tooltip).

**State tones color the quantity values.** The semantic tone, consistent with the Scan Station statistics, lives on the
row's quantity value: the `queue` quantity is warning; the `on machine`, `processing` and External-activity quantities
are information; the `done` quantity is success. Only semantic tokens are used. The state words themselves read as the
row's quietest dim text, and color is never the only distinction: the written state text remains beside every toned
quantity.

**Per-location dwell time is derived.** Each location row stores only the fixed entry timestamp `since` (null where
elapsed time does not apply, for example the Stockroom). The displayed time derives at render from it plus the shared
minute-precision UI clock (§3 rule 12).

- The time turns amber (`long`, with the written `Exceeds the expected duration` tooltip) once the position has
  exceeded its **effective expected duration** (PROJECT_PROFILE §17: the current Assigned Route Step's snapshot value,
  otherwise the Operation default).
- The server states the fixed instant `expected_by` at which the location's earliest-due portion exceeds its own
  expected duration. Each portion is judged against its own expected duration, never an average, so a newer portion
  never hides an overdue one. The shared clock judges the instant, and a location without an expected duration is never
  flagged. There is no fixed dwell threshold.
- The highlight is advisory and blocks nothing.

**Total row.** **One continuous dashed separator** (a dedicated element spanning the complete location grid, never
per-cell border fragments) sits between the location rows and the **total row**. The total row starts at the Location
column (`total`), aligns its quantity with the quantity column, follows with `pcs` (the Stockroom appends `stocked`),
and reserves the right-hand time position.

**Quantity tones.** The per-location quantities carry their row's semantic state tone (above; the muted tone remains
where no state tone applies, for example the Stockroom), and the total row's quantity uses the primary text color (no
status tone), so the reconciling total still leads visually. Location labels and `pcs` stay muted, the state words use
the dim tone, and the dwell time reads as secondary text.

### Due Date, Total Days and scrap

- **Due Date** carries a highlighted secondary line with the derived due countdown (§3 rule 12: `N days left`,
  `due today`, `overdue N days`). Amber means due soon within the Due Soon window proportional to the lead time (§3
  rule 12), red means overdue, and neutral means comfortable or stocked; `✓ stocked` still derives from the stocked
  total. There is no separate Days Left column.
- **Total Days** likewise derives from the Work Order received date (`N d`). Neither value is stored.
- A missing due date renders as `—` (§3 rule 9). A blank external Work Order Number in the Job Numbers metadata renders
  as `—` too.
- **Scrap visibility.** A PN with scrapped quantity shows **`n scrapped` as clear error-toned plain text on the total
  line itself**, with appropriate weight, **no filled background, no border and no pill radius**, anchored in the
  right-hand time position of the total row. There is no `⊘` symbol, no extra table row and no overlap with `pcs`, and
  nothing renders at zero scrap. The footer carries the Department scrap total.

### Ordering, urgency and motion

- **Priority model.** The Hot Work Order Demand rank comes from Priority Management. Hot rows sort first in rank order,
  with a row tint that gets redder the hotter the rank. **Every Hot rank is tinted**, in the three tiers of the shared
  Hot presentation (rank 1 red, rank 2 orange, every rank from 3 down the same weakest amber): a lower rank is quieter,
  never untreated. Non-Hot rows follow in the canonical demand ordering (PROJECT_PROFILE §18): dated rows earliest
  first, then undated rows after all dated rows, ordered by Work Order received date.
- **Due-date urgency.** Only the urgency text, that is, the derived due countdown (`N days left` / `overdue N days`, §3
  rule 12), blinks; the threshold comes from the Due Soon policy (§3 rule 12, §9). The PN and the date itself stay
  steady. The footer legend states this. `prefers-reduced-motion` disables the blink animation while keeping the
  warning color and weight. **Blinking is reserved for due-date urgency.** The Hot flame carries its own distinct,
  subtle pulse (below); no other Production Board content may blink or pulse.
- **Hot flame pulse.** The `🔥` icon in the No. column carries a subtle repeating opacity and scale pulse: gentle and
  readable, never fast or distracting, and clearly distinct from the due-date blink. Only the flame animates, never the
  row, the row number or the PN, and the flame renders nowhere else. Under `prefers-reduced-motion: reduce` the pulse
  is disabled while the flame and the existing Hot styling remain.

### Pagination and rotation

**Dynamic pagination.** Pages are calculated from the **actual available board height and the actual rendered row
heights** (through a hidden measurement table).

- They are recalculated on viewport or container resize, on data changes, on theme or font-metric changes
  (ResizeObserver plus window resize), **and on the shared minute tick**: derived dwell and countdown text can change
  measured widths and heights, so the measurement and pagination recalculation re-runs with each re-derivation (§3
  rule 12).
- At least one row is always shown per page, and the active page clamps when the page structure changes.
- In document object model (DOM) environments without layout (tests), a fallback of 10 rows per page applies.

**Automatic rotation.** Pages rotate automatically **only when more than one page exists**, with a **per-page dwell
time proportional to the rows displayed on the current page**. The timing uses the Department's **seconds per
displayed row** and **minimum page dwell** (initially 3 s and 6 s). These are Department display settings (§9),
configured per Department, delivered with the board feed and applied at the next refresh; they are never hard-coded UI
constants. The rotation-indicator tooltip states the Department's values. The page indicator is accurate, and a single
page never claims rotation.

**Manual navigation.** The footer also offers manual navigation:

- A Previous and a Next button. Previous is disabled on the first page and Next on the last: manual navigation never
  wraps, although automatic rotation may continue wrapping.
- **Clickable page dots** with clear accessible labels and `aria-current` on the active page.
- **`ArrowLeft` / `ArrowRight`** navigate pages at window level regardless of focus (the board has no normal text-entry
  workflow). Nothing happens when the requested page does not exist; there is no wrap; the keys are ignored under
  Ctrl/Alt/Meta and inert while an application modal dialog is active; and a valid page change consumes the key (no
  horizontal scrolling).
- **Touch swipe.** A horizontal swipe anywhere on the board navigates pages exactly like the buttons and arrow keys:
  swipe left for the next page and right for the previous one, clamped with no wrap. Short gestures, predominantly
  vertical gestures (scrolling) and multi-touch gestures (pinch zoom) never change pages.
- Every manual change (button, dot, arrow key or swipe) **restarts the auto-rotation timer**. The listeners are cleaned
  up on unmount.

**Page-change transition.** Every page change (buttons, dots, arrow keys, swipe and the automatic rotation alike)
replays a brief **direction-aware entry transition** on the visible table: a one-shot ease-out fade of about 0.28 s
while the table slides in from the direction of travel. Moving toward higher pages slides forward, and the rotation's
wrap from the last page back to the first also stays forward, so the cycle keeps one direction. The transition is
deliberately subtle for a hands-off wall display, and it is disabled under `prefers-reduced-motion` (pages then change
instantly).

**Rotation indicator.** While more than one page exists, a subtle indicator shows when the next automatic rotation will
occur: a thin progress track integrated with the footer's pagination controls plus a small seconds-remaining label,
visually subordinate to the production rows.

- It reads the **same timing deadline and the same per-page duration that drive the actual page rotation** (one timing
  source, never a second unsynchronized timer), so every manual navigation resets the rotation and the indicator
  together, and the track always reflects the current page's proportional dwell.
- The indicator owns its own visual tick, so the complete board never re-renders per animation frame.
- `prefers-reduced-motion` hides the moving track while the seconds label remains.
- The indicator is hidden entirely when only one page exists.

### Automatic display scaling

The footer carries an `Auto scale` On/Off slide switch (`role="switch"`, accessible name `Automatic display scaling`,
the same slide-control family as the Kiosk toggle; default **On**).

- While On, the board applies ONE uniform scale factor: CSS zoom on the board root, computed from the measured intrinsic
  max-content table width (`autoFitScale` in `board-logic`), so the full table width exactly fills the display. It
  **grows** on large wall displays to close the whitespace between columns, and it **shrinks on small screens (phones
  and tablets) so the complete unwrapped table fits the width without horizontal scrolling**.
- Every column keeps its full content in both directions: content that fits at the baseline can never start wrapping at
  the fitted scale.
- The height-aware pagination divides its viewport budget by the same factor and re-derives the rows per page for the
  scaled typography.
- The scale-down floor (`FIT_SCALE_MIN`) is a deliberate near-zero guard against degenerate measurements only, never a
  legibility clamp: small screens shrink as far as their width requires.
- Off returns the baseline sizes (narrow viewports then wrap or scroll as described in §2.5).
- The setting is presentation only and is never persisted.

### Footer

The footer is a normal flex child **anchored to the bottom of the board viewport** through the board's flex layout
(`margin-top: auto`). It is never `position: fixed` and never covers table content; its real height is always included
in the dynamic pagination measurement; and empty or one-page states still place it at the bottom without creating a
scrollbar. Its content is organized into readable groups instead of one undifferentiated sentence:

- **A controls row**: page indicator, rotation indicator, manual navigation, aggregate summary, and the `Kiosk` and
  `Auto scale` switches (§5.1).
- **A legend row** that carries only the user-facing **sorting rule**:
  `Order: Hot rank first → earliest due date → no due date by oldest received date.` All three levels are stated;
  deterministic tie-breakers and implementation field names are never exposed.
- **Column-header tooltips** hold the conventions. `No.` explains the 🔥 Hot flame. `Due Date` explains the blinking
  days count **and how the Due Soon warning window is derived**: the policy percentage, its minimum–maximum day clamp,
  and a few lead-time examples, with every number derived from the shared Due Soon policy (§3 rule 12), never
  duplicated as literals. Each tooltip is a small key/description panel, hidden until hover, so the sticky header height
  and the pagination measurement stay unchanged. No other column header carries such a tooltip.
- **The aggregate summary** is a restrained inline presentation, not a row of pills. The numeric values are emphasized
  through weight and monospace typography with subtle semantic tones (active PNs neutral, pcs in production
  information, pcs stocked success, pcs scrapped error); the labels stay quieter; groups are separated by soft `·`
  separators; and the whole summary remains clearly subordinate to the production table.

### How the board works with the server

The Production Board is production UI on `GET /api/production-board`.

- **Server read model.** The rows arrive in the canonical board order from the **server's** read model. The
  distribution per Area, Machine and External activity with the derived state, the fixed entry timestamp of each
  position, the stocked and scrapped quantities, the open Work Order and Job Number context, and the Hot rank are all
  derived on the server from the current-position projection and the Movement history.
  - A completed Work Order never supplies a row's context.
  - The rows follow exactly the canonical demand ordering; stocked quantity is not a sorting tier.
  - Merged quantity is read across every lineage branch, so its position is dated from the oldest entry of the whole
    merge, and a completing Machine appears only where the branches agree.
  - Every displayed time value (the per-location dwell and its `long` flag, judged against the server's fixed
    `expected_by` instant; the due countdown; `Total Days`; the clock) still derives at render from the shared UI clock
    (§3 rule 12).
- **Department.** The Department line names the Department the server resolved: the single active Department, or the
  one addressed by `?department=<id>` in the display's URL (a presentation address for a wall display, never a route).
  An ambiguous configuration is refused explicitly.
- **Auto-refresh** polls the feed periodically, with one request in flight at a time, and immediately again when
  connectivity returns after a loss.
  - A failed refresh keeps the last complete rows on screen, and the `● Live` status turns the warning tone with
    `Feed stale — reconnecting`, exactly as while the shared connectivity state is unhealthy.
  - A failed first load is the error state with Retry.
  - Because `● Live` is the board's own operational status, it reads healthy ONLY while a complete board is on screen.
    A first load that is still running or has failed carries the same warning tone and note, never a live-looking feed
    over an empty board. Both render under the always-present board header (Department, title with its status, clock),
    and the footer is shown once a complete board exists.
- **Job Numbers** names every demand of the row (`<job numbers> · WO <number or —> [· MODIFY] · <n> pcs`, or
  `· allocated a/n` once allocation has started).
- **Name and revision.** A row's PN name and revision line (`{name} · rev {revision}`) comes from the saved Part Number
  details and is absent when none exist.
- **Display settings.** The feed's Department carries its rotation timing, and the board reads the Due Soon policy with
  every refresh. A failed policy read behaves exactly like a failed board read.
- **Outdated page.** While the page is outdated (§3 rule 13) and the feed refreshes, `● Live` reads healthy and the
  update notice states the reason. `Feed stale — reconnecting` is kept for connecting, offline and failed refreshes.
- **Development previews.** The development-only `?state=` previews (loading, empty, error, long) render deterministic
  states without a request.

## 5.1 Kiosk mode

Kiosk mode is an explicit presentation mode for wall displays, addressable as its own route:

- `/production-board`: standard mode, keeping the normal top application navigation;
- `/production-board/kiosk`: Kiosk mode.

The mode is explicit in the router model (`mode: 'standard' | 'kiosk'`) and in the pathname, **never a component-only
boolean or a query parameter**. **Ctrl+Shift+K** toggles between the two routes, mirroring the Scan Station mode
shortcut. It is inert in unrelated text-entry controls and while a modal dialog is active; it preserves the current page
and board behavior where practical (the board component stays mounted across the toggle); and it is presentation-only,
never an authorization boundary.

In Kiosk mode:

- The normal top application navigation is hidden (by the application shell, with the same mechanism as Scan Station
  production mode). The persistent OFFLINE banner and the update notice are preserved, and the board reloads itself as
  §3 rule 13 describes.
- The board renders **one header shared with standard mode**: the **same Scan Station-style identity group as the
  standard header** (the Department line above the `Production` title with its `● Live` connection-toned status, §5).
  The kiosk header has **no application brand mark** (the board identity carries the header) and never renders the
  `ONLINE` connectivity chip: one status per header, and two presentations of one board. After the identity group comes
  a spacer, then the shared **borderless compact Dark/Light control** (§2.1, kiosk only), rendered **inside the clock's
  time row and vertically centered on the current-time text**, never centered on the taller time-and-date block. The
  header is one row that wraps cleanly and stays readable at narrower widths; there is no vertical actions stack.
- **The kiosk toggle lives in the footer controls row**: one control after the aggregate summary, sized like the
  pagination controls so the footer height is unchanged (it is still a normal measured layout child). It is a `Kiosk`
  **On/Off slide switch** in the shared footer-switch family (`role="switch"`, accessible name `Kiosk mode`,
  `aria-checked` mirroring the active route, the written `On` / `Off` state beside the track), in the same slide-control
  language as the `Auto scale` switch and the Machines Maintenance switch. Switching it On navigates to
  `/production-board/kiosk` (tooltip `Enter kiosk mode (Ctrl+Shift+K)`); switching it Off returns to
  `/production-board` (tooltip `Exit kiosk mode (Ctrl+Shift+K)`). It is the same control in the same position in both
  modes. The `Ctrl+Shift+K` shortcut remains, and no legend line repeats it: the shortcut lives in the control's
  tooltip.

Standard mode keeps the normal application navigation and shows the **same board header** (the shared identity group,
that is, the Department line and the `Production` title with its `● Live` connection-toned status, §5, and the clock;
the theme and connectivity chrome are supplied by the navigation there), plus the footer `Kiosk` switch (Off in
standard mode).

---

# 6. Area Board

The Area Board is a monitoring sub view of Management and follows the global theme mode (§2.1). It is one view with two
modes behind a single tab strip: the **All Areas overview** (the PROJECT_PROFILE §21 *Manager Summary* content) and the
**per-Area detail** (the PROJECT_PROFILE §21 *Area Board* content). The name "Manager Summary" is not used in the UI.

## 6.1 Tab strip and toolbar

- The tab strip starts with **All Areas** (the default on first open), followed by one tab per Area with its color dot
  and item count. Exactly one tab is active.
- Both modes share one toolbar:
  - a text search (PN, WO, Job Number) that matches **every** open demand of a PN, so searching the Work Order or Job
    Number of a demand summarized as `+N more` keeps that PN visible;
  - a sort selector (Due date, Priority, Time in Area, Quantity);
  - a scope summary (PN count and total pieces for the active Area, or across all Areas in the overview);
  - the **feed status** of the live read: `● Live` in the success tone with the shared connected heartbeat, or the
    warning-toned `● Feed stale — reconnecting` (§5: the same statement and the same meaning as the Production Board's,
    never color-only, and a status of the BOARD rather than of the connection).
- The search field's minimum width equals the Sort select's minimum (about 150 px), so the toolbar compresses evenly
  before it wraps.

## 6.2 All Areas overview

The overview shows one column per Area. By default the layout scrolls horizontally when all Areas do not fit. A
**Wrap columns** slide toggle in the toolbar (wide viewports, overview mode only) switches to a wrapping layout: columns
flow onto additional rows within the page width, and nothing scrolls horizontally. The choice is presentation state
within the view: it survives switching to a detail tab and back, and it defaults to horizontal scrolling on each open.

**Narrow presentation.** Below the phone/tablet breakpoint the view drops the tab strip entirely and shows a
**`Summary` slide toggle (default off)** in place of `Wrap columns`.

- With Summary **off**, the board pages through the **per-Area details**: one §6.3 detail layout per page in a swipeable
  carousel. It uses native horizontal scrolling with mandatory snap (each swipe lands centered on one Area's detail),
  no visible scrollbar, and pages slightly narrower than the viewport so the neighboring pages peek in at the edges,
  which is the built-in cue that more pages exist.
  - **Area-colored page dots** above the pages mirror the stable Area identity colors (§2.2) and double as direct jump
    targets (`Go to {Area}`, `aria-current` on the active page, a generous hit area around each dot).
  - **Floating `‹` / `›` half-pill edge buttons**, pinned to the middle of the viewport on the frosted panel surface,
    page backward and forward. They are always visible while a tall page scrolls vertically and are disabled at the
    ends: paging never wraps.
  - The active page derives from the scroll position, so swipe, momentum, snap, dots and buttons can never disagree, and
    the toolbar's PN/pcs meta line follows the current page's Area.
- With Summary **on**, the All Areas overview stacks as the full-width vertical column list (the §2.5 wrap layout) with
  the all-Areas meta line. Clicking a card header jumps straight to that Area's detail page (Summary switches off).
- Search and sort apply unchanged in both modes. Wide viewports never render the pager chrome or the Summary toggle.

**Each Area column shows:**

- a **clickable header** (Area color, name, description, supported Operation chips) with a "detail ›" affordance;
  clicking it opens that Area's detail mode, which is equivalent to selecting its tab;
- a statistics row with only meaningful values, with no zero-value noise: Areas with Machines show **total pcs · queued
  · on machines**, plus **done** when finished quantity exists; Areas without Machines show **total pcs · processing**,
  plus **done** when finished quantity exists; the terminal Stockroom column shows **stocked pcs · PNs**;
- the PN list for the Area: **one row per Part Number**, its separate quantities aggregated into that row's portion
  chips, built from the **shared PN-row shell and subcomponents** (§4.10: `AreaOverviewRow` reusing the shared row grid,
  `HotPn`, `QuantityStatus`, `DueStatus`). A row shows the PN (🔥 and the rank when Hot), quantity, WO and Job Number (a
  blank Work Order Number as `—`, truncating with a `title` tooltip), the derived due countdown (color-ramped, §3 rule
  12; `N days left` in the one shared countdown language, with the Stockroom's verbatim `dueText` such as
  `allocated 50/50` where a countdown does not apply), the card's portions aggregated as compact context chips
  (`Lathe 3 × 3`, `queue × 2`, `processing × n`, success-toned `done × 1`), the derived time in Area, and `{n} scrapped`
  where present. There is no independent JSX or CSS row implementation that could drift from the shared presentation;
- an explicit empty state for Areas without production.

**Search and sort in the overview.** Search filters the PN lists, and the sort selector orders PNs within each column.
Because the overview row IS the Part Number, sorting happens **after** the aggregation and compares the row's own
values: `Quantity` orders by the PN's total in that Area (a PN holding `6 + 6` outranks one holding `10`), and
`Time in Area` by the oldest of its portions. `Priority` and `Due date` are unaffected, because the Hot rank and the due
date are already PN-level. The per-Area detail keeps sorting the separate quantities, because there each row is
separately actionable.

## 6.3 Per-Area detail — shared Area/Machine monitoring layout

The detail mode uses the **same structural layout as the Scan Station** (§4.10):

```text
[ In this Area now ] [ Machine cards grid                    ]
[ fixed left col   ] [ Machine 1 ][ Machine 2 ][ Machine 3   ]
[ grows vertically ] [ Machine 4 ][ Machine 5 ]              ]
```

- **Area summary card (left).** It has the Area color along its top edge and shows the Area name, description and
  Operations; then the statistics row (the Area Board keeps `showStats` on, including the Done statistic in the success
  tone); then the grouped compact PN list. The groups are **"On Machines"** and **"Area queue — awaiting Machine"** only
  when the Area has Machines, a direct **"In processing"** group otherwise (the terminal Stockroom shows **"Stocked"**),
  and **"Finished — ready to move"** for `READY_TO_TRANSFER` quantity. Finished quantity belongs to the Area summary,
  never to a Machine card, and is never displayed as Stocked or as manufacturing-complete. Each entry uses the shared
  grid PN row presentation (§4.10): Hot indicator and PN (`🔥#n` before the PN, §5), machine or queue context with the
  quantity (`pcs`), WO (or `—`) and Job Number with a tooltip for truncated values, due status, in-Area status, time in
  Area, and `{n} scrapped` text when scrapped quantity exists. Grouping never duplicates or loses quantity.
- **Machine monitoring cards (right-side grid only).** There is one card per Machine: name, status (running / idle /
  maintenance) with the state age in the status pill (`running · 1h 24m`, §4.10), total quantity assigned, PN count,
  and the assigned PN list in the same shared row presentation. Idle Machines show a clear empty state. Maintenance
  Machines stay visually distinct (dashed error-tone border), with the maintenance note and expected return date in the
  empty text (§4.10). Cards wrap to additional rows within the right grid and never underneath the left card; a
  deliberate single-column fallback applies at narrow breakpoints.
- Areas without Machines render **only the full-width summary card**, with no placeholder Machine cards and no blank
  Machine region.
- **The Area Board remains read-only.** The shared components render without any Scan Station action rail (no `QUEUE`
  buttons, no action cells), and there is no visual drift between the two views (shared `AreaPnRow`,
  `AreaSummaryCard`, `MachineMonitoringCard`, `DueStatus`, `QuantityStatus`).
- Search and the sort selector (§6.1) still narrow and order the PN lists. **Sort: Time in Area** orders by the elapsed
  minutes derived from the fixed Area-entry timestamp (`enteredAreaAt` plus the shared UI clock, §3 rule 12; there is no
  stored `timeInAreaMinutes` field), longest first.
- An over-long PN is truncated with an ellipsis, and hovering shows the full PN as a tooltip (§2.3).
- Empty state: "No production in {Area}".

### How the Area Board works with the server

The Area Board is production UI on `GET /api/area-board`.

- **One read, two presentations.** ONE read of the Department returns every ACTIVE Area with the **same Area monitoring
  model that the Scan Station reads** (`app/api/area_inventory.py`): the Area mode, every ACTIVE Quantity Flow with its
  holding state derived by the server, the Machine cards holding only actively assigned quantity, the queued, directly
  processing and finished groups and their totals, the PN's OPEN demand context, and the scrapped quantity per PN in
  that Area (the `{n} scrapped` line of the shared row, which the station therefore shows too). It also returns the
  Area's active Operations and, for the terminal Stockroom (whose stocked quantity is manufacturing-complete and holds
  no active flow), its stocked lines with the PN's active allocation and the PN's OPEN demand context: the Stockroom
  row's Hot rank, Work Order Number and Job Numbers follow the same rule as every other monitoring row and as the
  Production Board's stocked-only row, and it shows `WO — · —` only once no Work Order of the PN is open.
- The All Areas overview and the per-Area detail are **two presentations of that one answer**, so switching tabs
  re-reads nothing and the two modes can never disagree. Both render through the shared components (`AreaSummaryCard`,
  `MachineMonitoringCard`, `AreaPnRow`, `AreaOverviewRow`) against the same client mapping that the Scan Station uses,
  which is what keeps the two views free of drift.

**The overview is PN-centric; the detail is quantity-centric.** This is deliberate, because they answer different
questions.

- One Part Number in one Area is exactly ONE overview row and counts once in every PN figure (the tab count, the
  toolbar meta line, `Total PNs` / `PNs`), however many separate quantities it holds there. Those quantities are
  aggregated into that row's portion chips (`Lathe 3 × 3`, `queue × 2`, `processing × 6`, `done × 1`), which always
  account for the whole row quantity: a piece is never lost and never counted twice, and a mixed presence (internal
  processing beside an external Operation) keeps its direct remainder as its own chip.
- The per-Area detail keeps ONE row per separate quantity, because each is scanned and acted on separately at the Scan
  Station (§4.10).

**What a row is worked FOR is the PN's OPEN demand, never its origin.**

- The Hot rank, the due countdown, the Work Order Number and the Job Numbers of every monitoring row come from the PN's
  OPEN Work Order Demands in the canonical demand order (PROJECT_PROFILE §18); the FIRST one defines them.
- Where a PN has several open demands, the row names that defining demand and states `· +N more`, with every demand
  spelled out in its tooltip. They are never merged into one ambiguous value and never silently reduced to the first.
- A completed Work Order is history and supplies nothing, even while quantity that it released is still in the Area:
  such a row reads `WO — · —` with `No due date` rather than borrowing a context it no longer has.
- The demand that a quantity ORIGINATED from stays on the quantity as provenance, and provenance is **workflow and audit
  context only**. It names the lot in the Scan Station's action dialogs, confirmations and Last Action recaps, where the
  operator is about to act on exactly that quantity. It never supplies a monitoring row on either surface: the station's
  `In this Area now` and the Area Board's rows are ONE presentation, so a quantity can never read as worked FOR one
  thing at the station and another on the board, and the two answers are never mixed in one line.
- Each row also carries the fixed monitoring values of ITS quantity: the Area-entry timestamp (read across every lineage
  branch, so merged quantity is dated from the oldest of them) and the Machine that COMPLETED finished quantity. The
  latter is reported from the quantity itself, so a Machine retired after finishing the work still names the completion
  although its card is gone.
- `Time in Area` and the due countdown still derive at render from the shared UI clock (§3 rule 12). A Stockroom row
  states `allocated a/n` instead of a countdown and has no entry time at all.

**Department, refresh and states.**

- The Department is the single active one, or the one addressed by `?department=<id>`. An ambiguous configuration is
  refused explicitly.
- Auto-refresh polls the feed on the Production Board's own schedule (one request in flight, and immediately again when
  connectivity returns after a loss). A failed refresh keeps the last complete board, with the toolbar status turning
  `Feed stale — reconnecting`. A failed first load is the error state with Retry. A Department without active Areas is
  an explicit empty state.
- `?state=loading|empty|error|long` renders deterministic development previews without a request.
- Search (reaching every open demand of a PN, not only the one its row names), the four sort orders (applied to the
  overview AFTER the PN aggregation, so `Quantity` compares the PN's total in the Area; `Priority` orders every Hot rank
  before every unranked row, whatever the rank number) and the layout choices (Wrap columns, the narrow Summary toggle
  and paging) stay presentation state of the view. Unlike the Production Board, there is no canonical Area Board order
  for the server to own.

**Expected duration.** A row's `Time in Area` turns the warning tone (`tia.long`, with the written
`Exceeds the expected duration` tooltip) once the quantity has exceeded the effective expected duration of its position
(PROJECT_PROFILE §17: the current Assigned Route Step's snapshot value, otherwise the Operation default; nothing is
flagged without either). The shared inventory states the fixed `expected_by` instant per flow, and the shared clock
judges it. The PN-centric overview row warns from the EARLIEST-due portion of the PN's presence, each portion judged
against its own expected duration and never an average, so a newer portion never hides an overdue one. The same shared
row warns identically at the Scan Station (§4.10). The highlight is advisory, and every action stays exactly as it is.

---

# 7. Tracking

Tracking is the primary management interface. It is **organized around the Part Number** (PROJECT_PROFILE §21): a
filterable PN results table plus a **modeless floating detail overlay** for the selected PN. The title operators see and
the Management navigation label are **PN Tracking**; the view keeps its internal `Tracking` name, the
`/management/tracking` route and the §7 section identity.

**Selection toggles on the whole row.** The selected PN may be **none**. Clicking an unselected PN row selects it and
opens the details; clicking the currently selected row again unselects it and closes them. The **whole result row is
the selection click target**: one interactive button on the PN cell is the single focusable control per row and
carries the accessible name and the visible focus treatment, while a click anywhere on the row and the button's
activation share one toggle handler. There are no nested interactive controls and no divergence between keyboard and
pointer.

**Modeless floating detail overlay.** The detail panel is a modeless floating overlay anchored to the lower right of the
viewport (`position: fixed`), with its own internal scrolling, a professional elevation, **no backdrop and no focus
trap**, layered below navigation and application dialogs.

- Opening or closing the panel **never resizes or reflows the results table**: the table wrapper is single-column in
  every selection state, so the list stays steady for comparison while details are open. This is deliberately not a
  blocking modal.
- The panel header carries an **accessible close (✕) button** (accessible name, touch target of at least 48 px).
  Escape closes the panel (a dialog stacked above it would own Escape through its own focus scope), as does clicking
  the selected row again; both return focus to the originating row.
- A click anywhere outside every result row and outside the panel itself also closes it (clicking a different row
  instead switches the selection, through that row's own handler). This outside-click case does not move focus.
- At 900 px or narrower, the overlay spans the viewport width with a bounded height.
- The details remain read-only, with all the tracking content described in §7.2.

## 7.1 Filters and list

**Filters.** A search across PN, WO and Job Number, and selects for Area, Operation, Machine, Request Type (`NEW` /
`MODIFY`), priority (Hot only), status (Active, Stocked, Open, Completed) and due window.

**List columns.**

- PN (with its name). Hot entries use the standard `🔥#n` presentation before the PN (§5). A PN whose master metadata
  record was deleted keeps rendering its canonical PN normally; only the name and metadata from the master record show
  as absent (`—`).
- Active WO Demand (WO · quantity · Request Type chip; a blank Work Order Number shows as `—`).
- Current distribution (Area color dots).
- Active quantity, stocked quantity, cumulative scrapped quantity.
- Next due date (`—` when the demand has no due date, §3 rule 9).
- Status pill, derived as follows:

  | Status      | Shown when |
  |-------------|------------|
  | `Active`    | Quantity is in production. |
  | `Stocked`   | Only while UNALLOCATED stocked quantity (effective `STOCKED` minus the active allocation) waits for open demand. |
  | `Open`      | Open demand has no quantity in production and no unallocated stock (stock already allocated to earlier work included). |
  | `Completed` | No open Work Order Demand remains. |

**How the list works with the server.** The list is production UI on `GET /api/tracking`.

- The search (debounced, over the PN, the Work Order Number and the Job Number of any of the PN's demands) and every
  select are evaluated on the server against the derived row. The due window is judged on the site calendar against the
  PN's next due date.
- The rows come in the canonical demand order.
- The list is bounded: `Showing n of m PNs`, with `Show more` widening the page once to the server's bound and then
  asking to narrow the search.
- The title row carries the live view's feed status (`● Live` / `Feed stale — reconnecting`, §5; the list's own status,
  not the connection's), and the list refreshes itself on the shared monitoring cadence.

## 7.2 Detail panel sections

1. **PN master.** PN image (the ONE shared default PN image placeholder when no custom image exists, the same default
   that Management → Part Numbers uses, §14; never a second default), PN, name, current revision (informational),
   barcode value (`PF:PN:…`, derived from the canonical PN), and enterprise resource planning (ERP) id. The master
   record is optional current metadata (PROJECT_PROFILE §8.1): when no master record exists for the PN, the section
   shows the canonical PN and its derived barcode with the metadata fields absent (`—`), and the history and
   current-state sections below are unaffected. The name, revision, image and ERP id come from the saved Part Number
   details and render `—` (the default image) when absent.
2. **Active WO Demand.** A table of WO · Request Type · requested · allocated · remaining shortage · due · priority,
   with an allocation progress bar, labeled "business demand — separate from Movement". A line allocated beyond its
   demand by an authorized correction shows `(+n beyond demand)`. The summary and the progress bar count each line only
   up to its requested quantity and name the beyond-demand quantity separately
   (`Allocated 18 / 20 requested · +2 beyond demand`), so a short line is never shown as covered by another line's
   excess.
3. **Current quantity by Area.** Horizontal bars per Area or Machine in Area colors, with queue rows visually distinct,
   labeled "derived from Movement history".
4. **Quantity Flows & Routes.** One block per Quantity Flow.
   - **Header chip.** The block header carries the shared **`RouteModeChip`**, the one route-mode presentation
     everywhere, shared with the Scan Station Receive Quantity recap and confirmation: mode and route information in one
     chip separated by an em dash and colored by mode; here `FLOATING — actual trace` / `PLANNED — snapshot`,
     deliberately in the **compact variant** (the default chip height elsewhere matches the other chips such as
     `TypeChip`).
   - **`PLANNED` flow.** It shows its AssignedRoute snapshot chips (done → current → queued → future), with deviations
     marked with who, when and why, and the audit-preservation note.
   - **`FLOATING` flow.** It shows the **actual route trace derived from Movement history**: repeated Areas preserved,
     split flows shown independently, and Repair transfers marked explicitly, for example `A → B → C → D → B ⟲ REPAIR`.
   - **Steps and arrows** render as **separate sibling flex items** (step, arrow, step, …): each arrow is centered
     between adjacent steps, arrows never overlap step cards, and wrapping stays readable.
   - The finished rack never appears as a route step: `AREA_COMPLETED` is completion inside the existing source Area,
     and only `TRANSFERRED` extends the trace. The section explicitly avoids implying that the whole PN is at one Route
     Step.
   - **Adjusted routes.** A `PLANNED` flow whose own route was adjusted (PROJECT_PROFILE §8.10) shows
     `(snapshot, adjusted)` in its Planned note and one `Route adjusted …` note per adjustment: time, user, reason and
     "the steps after step n were replaced", with the audit-preservation note. Every adjustment is shown, oldest first
     after the deviation notes, with nothing truncated. The notes belong to the flow whose route was adjusted: a split
     child or merge result copies the route as it was (it shows `(snapshot)`), and its position line names its source
     flow, whose block carries the notes.
   - **Expected duration.** A Quantity Flow's position line adds the explicit advisory note
     `· exceeds expected duration` (warning tone, written out, never color alone) once the position has exceeded its
     effective expected duration (PROJECT_PROFILE §17: the server's fixed `expected_by` instant judged by the shared
     clock; nothing without one).
5. **Movement history (immutable).** Reverse-chronological: timestamp, Movement type (color-coded, canonical names,
   `SCRAPPED` and `AREA_COMPLETED` included; `AREA_COMPLETED` uses the success tone; Repair transfers carry an explicit
   REPAIR badge), and the full description (Areas, quantity, Quantity Flow, Machines, Worker, station, reasons).
   - Movement rows name the recorded Worker as `W: <name>`, and route-deviation notes add ` by <name>`; rows without a
     recorded Worker omit it.
   - An undone original stays visible with an explicit `REVERSED` badge, beside the `REVERSED` row that undid it.
   - On phone widths, a Movement history row wraps its description onto its own line.
   - The current-state sections distinguish active Machine assignment, direct Area processing, Area completion (ready
     to transfer, with operator wording such as `Completed processing at Lathe — ready to transfer` and the completing
     Machine as context only), transfer, and Stockroom completion. Quantity that is ready to transfer is never implied
     to be `STOCKED`.
   - The history is read-only; no edit affordances exist.
6. **Scrap history.** The PN's `SCRAPPED` events and cumulative scrapped quantity, with the reconciliation note
   (introduced = active + stocked + scrapped; scrap never reduces the requested quantity).
7. **Stocked & Allocation history.** Stocked quantity and Work Order Allocation entries, with the PROJECT_PROFILE §18
   ordering note, and an empty state when nothing is stocked. A Management entry names the user who recorded it; a
   beyond-demand correction is marked `beyond demand`.
8. **Corrections.** Actions for authorized users only, as explicit buttons: Quantity adjustment, Edit assigned Route,
   Adjust WO Allocation, Change priority, View audit trail.
   - Every correction (an allocation beyond demand, a reversal) requires a reason and produces new history. A routine
     allocation of stocked quantity that was left for later, offered from the same `Adjust WO Allocation` dialog
     (§11.6), takes an optional note.
   - `Change priority` and `View audit trail` are not correction flows and take no reason. `Change priority` is a link
     into Management → Priority, which hosts the Hot list actions (§8; the Hot list change is made and audited there:
     removals and reorders are confirmed, and an addition needs an explicit choice of the Work Order Demand). It is shown
     to users who may set or reorder priority. `View audit trail` opens the PN's read-only audit trail (§7.4) and is
     shown to every user who may open Tracking.
   - Being a navigation and a read, both stay available while disconnected (§3 rule 6 blocks writes, and Priority blocks
     its own writes). The openers of the correction flows stay disabled while disconnected.

**Edit assigned Route dialog.** `Edit assigned Route…` (permission: Assign and edit Routes) opens
`Edit assigned Route — {PN}`.

1. **Choosing the flow.** When several active `PLANNED` flows of the PN exist, the user first chooses the Quantity Flow
   explicitly (none is preselected; other flows keep their routes). A flow with a single choice is preselected, and a
   PN without an active Planned flow gets a plain explanation.
2. **Locked steps.** The steps the quantity has reached, or that its history records, are **locked** (`Done` /
   `Current` / `Recorded`; `Recorded` is the step of an undone arrival, which stays). An accurate hint names the step
   that the next on-route arrival is checked against or, when no step follows, says that no step is expected and that
   the next arrival needs a route-deviation confirmation.
3. **Editing the future steps.** Only the future steps are edited, with the Planned Routes step **rows** (the §13.2
   step-row rules; numbering continues after the locked steps; the future tail may be emptied), the §13.2
   `● Unsaved changes` marker and the `Discard unsaved route changes?` guard. A reason is mandatory.
4. **Review.** `Review adjustment` leads to a review step (`Adjust the assigned route?`) showing the steps before and
   after, each step as `{Area} · {Operation} · Est. {time}` plus its preferred Machine, with the steps whose only change
   is their instructions named.
5. **Outcomes.** A route that changed since it was opened is refused with `Reload route` (the edits are discarded). An
   unknown outcome offers `Retry`, which applies the adjustment only once.

Only this flow's own AssignedRoute changes, never the Planned Route, the other flows or the Movement history, and the
previous route stays in the audit history.

## 7.3 States

- **Loading** shows skeletons per section.
- **Empty filter result** shows "No PNs match — clear filters".
- **Corrections visibility.** The Corrections section is hidden entirely, never disabled, for a user who holds none of
  its buttons' permissions, and a button the user may not use is absent; buttons are never rendered as inert. The
  section shows its buttons in this order: `Edit assigned Route…` (Assign and edit Routes), `Adjust WO Allocation…`
  (Edit Work Order Allocation, §11.6), `Change priority` (Set Demand Priority or Reorder Hot Items; a link into
  Priority) and `View audit trail` (any permission that opens Tracking). Because `View audit trail` follows the
  Tracking read set, every user who may open Tracking sees the section with at least that button. A correction button
  that is not implemented is absent, never inert.
- **Tag and status line.** The tag `authorized actions — recorded with your name` shows only while a recording
  correction (`Edit assigned Route…`, `Adjust WO Allocation…`) is offered. One status line under the buttons reports the
  outcome of the last correction (a route adjustment or an allocation change).
- **Connectivity.** The two correction-flow openers are disabled while not connected; `Change priority` and
  `View audit trail` stay enabled.

**How the detail works with the server.** The detail is production UI on `GET /api/tracking/detail`.

- Each selected PN is its own polled read, with the loading skeleton and an error with Retry inside the panel. A failed
  refresh keeps the last complete detail, with a `Feed stale — reconnecting` note under the PN.
- **Movement history** is reverse-chronological by timestamp (`occurred_at`, with the Movement id breaking ties) and
  paged on that same order (`Showing n of m Movements`; `Show older Movements` appends the next page of the immutable
  history without a gap or a duplicate).
- **Scrap history** (§7.2 item 6) lists the PN's `SCRAPPED` events as rows of that same history (timestamp, quantity,
  Area, reason; an undone event is kept with its `REVERSED` badge) under the net cumulative figure, with
  `Show older scrap events`.
- **Quantity Flows** page in one stable order: newest flow first, on the immutable flow id. A flow's active or closed
  status is presentation and never its position. One bounded page loads at a time (`Show older Quantity Flows`); the
  current quantities stay complete in the `Current quantity by Area` section regardless of flow paging.
- **Allocation history** pages likewise (`Show older allocation entries`). Nothing is truncated out of reach.
- An appended page of any section is dropped and read again to the same depth when a refresh moves its boundary or
  changes what its rows show (a flow closed or reopened, a scrap undone, a route adjusted), never on a refresh that
  changes nothing relevant.

## 7.4 Audit trail

`View audit trail` (§7.2 item 8; for users who may open Tracking) opens a read-only dialog titled `Audit trail — {PN}`,
stacked above the detail panel. The dialog owns Escape and returns focus to the button on close.

**What it lists, newest first:**

- the PN's Part Number details (created, edited, image added, replaced or removed, deleted);
- the Work Orders and Work Order Demand lines that request the PN (created, edited, a Work Order completed by a demand
  change). A line deleted since then keeps its recorded history and is marked `(since deleted)`. A Work Order's header
  changes and completion appear for every PN it requests;
- every Hot rank change of those lines, with what caused it. A line that only shifted because another entry was added,
  removed or moved says so, never that it was moved or removed itself;
- its Management allocation entries, every allocation reversal (Stockroom reversals included) and authorized
  beyond-demand corrections, marked as such;
- the route adjustments of its Quantity Flows (the replaced and the new steps, each with its Area, Operation, estimated
  time, preferred Machine and instructions).

**Each entry shows** the time; what changed and on which Work Order, demand line or Quantity Flow; the user who
recorded it (avatar and name, or nothing when no user was recorded, as for Scan Station records and history from before
sign-in existed); the reason when one was given; and the values before → after in user language (no field
identifiers).

**What it does not list.** Production Movements, Undo included, stay in the Movement history (§7.2 item 5), and routine
Stockroom allocations stay in the allocation history (§7.2 item 7). Configuration changes that are not about this PN
are not listed. Nothing in the trail is editable or deletable.

**Paging.** It pages like the other Tracking histories (`Showing n of m entries`, `Show older entries`, keyset on time,
nothing truncated out of reach) and is a snapshot read taken when it opens: paging never repeats an entry and never
skips one recorded before the trail was opened. A change recorded while it is open may appear on a later page or only
after reopening, and the last page then says
`reopen the audit trail to include changes recorded since it was opened`.

**Connectivity and states.** It is a read and stays available while disconnected (a read error shows with Retry).
States: loading; error with Retry; empty `No recorded changes for {PN} yet.`

---

# 8. Priority Management

Priority Management manages the Department's Hot Work Order Demand list (PROJECT_PROFILE §21 Priority Management).
Priority belongs to Work Order Demand, so several Work Orders for the same PN may hold different ranks.

### List

The list shows each Hot entry with its rank in the standard Hot presentation (`🔥#n` immediately before the PN, §5), WO
and Job Number, Request Type chip, demand figures (requested / allocated / shortage), current distribution, and the
color-ramped due date (`—` when absent, §3 rule 9).

### Add

The "+ Add to Hot list" button opens a dialog with a single search field that accepts free text (PN, WO, Job Number)
*and* PN barcode scans.

- **Barcode resolution.** With **no** eligible Work Order Demand for the scanned PN, nothing is added. With exactly
  **one**, it is added directly. With **more than one**, the list filters to that PN, and an **explicit selection** of
  the specific Work Order Demand is required: the application never picks the first match on an ambiguous barcode.
- New entries always join at the **bottom** of the list. Adding a new entry at the bottom acts directly, without an
  order-change confirmation.

### Arrival from Tracking

Tracking's `Change priority` (for users who may set or reorder priority) opens this view for one PN. Arrival itself
changes nothing.

- When the PN has Hot entries, they are highlighted and a status line names their ranks
  (`{PN} is on the Hot list at #2 and #5.`).
- Otherwise, for users who may add, the Add dialog opens listing all and only the Work Order Demand of that PN that can
  join the Hot list (an exact PN match, never a text search; the search field stays empty for a different search).
  Nothing is added until one is clicked, even when only one is listed.
- For users who may only reorder, a status line says that the PN is not on the Hot list and that the account can
  reorder but not add.

### Remove

Each entry has a remove (✕) affordance. Clicking it opens a **confirmation dialog identifying the PN and the Work Order
Demand**; Cancel changes nothing. On Confirm the entry is removed, the remaining ranks close the gap, the change is
applied and audited, and Undo can restore it. The confirmation guards the removal; it does not reintroduce a separate
save-or-cancel workflow.

**Automatic removal.** An entry whose line becomes fully allocated (including by its Work Order completing) leaves the
list together with the allocation or demand edit that caused it: no confirmation, the ranks close the gap, and the
change is audited. The list shows its last read. A confirmation made against an older list is refused as stale and the
current list is shown, and an Undo or Redo step whose entry has left the list is dropped with a notice.

### Reorder requires confirmation, presented as two snapshots

Every operation that changes the order of existing Hot entries (drag-and-drop with a visible grip, Move Up, Move Down,
Undo and Redo) opens a **confirmation dialog before applying**. The visible list is **never renumbered before
confirmation**.

**Summary.** The dialog leads with a primary summary of the item the user moved
(`Move 2027-60-8114-00 · WO 007010 from #4 to #2`), plus a computed secondary line such as
`2 other demands will shift down.`. The name of the implementation action stays secondary detail only. Beneath the
moved-PN summary, a **compact impact and action block** states the shift impact as a readable sentence
(`1 other demand will shift up.`) and separates the `Action` label from its value. The action value (`Move Down`, …) is
emphasized through weight and semantic text treatment only: no decorative pill, and never louder than the moved-PN
summary.

**Two snapshots.** A **`Current Position` → `New Position` two-snapshot layout** follows, restricted to exactly the
affected rank range (never the full Hot list), with **one centered downward transition arrow** between the sections,
visually distinct from the per-row rank arrows.

- Each entry line separates rank, direction, PN, and WO/Job metadata, with the rank first.
- In **Current Position**, a per-row **↑/↓ arrow beside the current rank** shows where the entry will move (↑ toward
  rank #1, ↓ away from it). Rows stay in current rank order.
- **New Position** shows the order after applying, without per-row arrows. Instead every row displays its complete rank
  transition **`#<current-rank> → #<new-rank>`**: the old rank and the arrow render quietly and the new rank strongest,
  and rows stay sorted by new rank.
- An entry that exists on only one side of the change (Undo or Redo restoring a removed entry or taking back an added
  one) spells the missing side out explicitly with a clear **`Not listed`** placeholder: `Not listed → #n` for a
  restored or newly listed entry, `#n → Not listed` for a removed one. It is never a silent omission.
- The directly moved entry is highlighted, and indirectly shifted entries render with less emphasis.

**Shared, content-sized position track.** One wrapper grid encloses `Current Position`, the transition arrow and
`New Position`, and the section boxes, lists and rows all join its tracks through a subgrid chain. The rank/transition
track therefore sizes from the widest real value of **either** side (for example `Not listed → #3`), and the PN column
sits at the same horizontal offset in both sections: aligned across the sections, spaced by actual content, and never
overlapping. There is no fixed-width label or rank track, so no large gap opens between the position value and the PN,
and the rank, PN and WO/Job metadata never blur together.

**Snapshot divider.** Each section carries **one vertical rule between the shared position track and the PN column**: a
single row-spanning grid item attached to the shared track's edge, never per-content-row border fragments. The rows are
explicitly pinned to their grid lines, so auto-placement cannot dodge the divider's cells. The divider exists only in
environments that support subgrid; it is hidden in the fallback without subgrid and in the stacked layout at 560 px or
narrower.

**Narrow screens.** The narrow-screen fallback stacks the rank/transition above the PN and metadata identically in both
sections, keeping the rank–PN–WO/Job relationship readable.

**Typography.** The **PN** uses the primary text color, stronger weight, monospace and clear spacing. **Work Order and
Job Number** render as one light informational chip (`WO 007001 · Job 18112`: subtle surface and border, rounded,
secondary tone, no button behavior, full metadata as a tooltip), built from the explicit `workOrderNumber` and
`jobNumber` fields, never parsed out of a display string.

**Buttons.** `Apply ranking` and `Cancel (Esc)`. Cancel leaves the list order and both Undo and Redo histories
unchanged. Undo and Redo confirmations use the user-facing titles `Restore previous ranking` and `Reapply ranking` and
present the same snapshot layout.

### Undo and Redo instead of save-or-cancel

Every confirmed change is applied and audited, and it can be stepped back and forward with Undo and Redo buttons; each
step is itself an order change and shows the same confirmation. The buttons are disabled when the corresponding history
is empty. **The depth is unlimited within the current application session:** no numeric limit exists. The Undo/Redo
history belongs to the signed-in user: it ends with the application session, when the user signs out, or when another
user signs in (an expired sign-in renewed by the same user keeps it).

### Footer note

A footer note restates the rules in user language: Hot demand is always worked first, in rank order, and ordering
follows the canonical demand ordering: ① Hot rank; ② dated demand earliest first, with undated demand after all dated
demand, ordered by Work Order received date (PROJECT_PROFILE §18). Implementation identifiers (`priority_rank`,
tie-breakers) never render. The note also states that an entry leaves the list on its own once its line is fully
allocated.

---

# 9. Administration

Administration is isolated from production and focused on **system administration**. Its sidebar navigation is grouped
as follows:

| Group                | Sections |
|----------------------|----------|
| **Organization**     | Departments, Areas, Operations, Workers |
| **Production setup** | Scan Stations, Barcode configuration, Scan behavior |
| **Access**           | Users, Roles & permissions |
| **Policies**         | Worker sessions, Machine assignment, Correction permissions, History archival & purge, Department display settings, Settings |

Each section is a standard table-plus-editor pattern. The Areas table is the reference example: Area (color and name),
Operations, Machine assignment (Direct processing — no Machines / Queue → assign (one-shot); the mode follows from
whether the Area has any Machines, one Machine behaving like several, never from a per-count configuration), Machines,
Worker ID mode (Disabled / Fixed Worker / Scanned session), Terminal flag, Active status.

Operations are managed as described in PROJECT_PROFILE §8.5: each Operation belongs to an Area, and the Areas table
lists the Operations an Area supports.

Editing an Area's display properties shows an inline note that identity and barcode are stable and history is
unaffected. Destructive operations (deactivating an Area with active quantities) are blocked with an explanation, not
confirmed through.

### Policies

- **Worker sessions** owns the Scanned-session **sliding inactivity timeout** (§4.12): one default value with optional
  per-Area overrides, in whole minutes from 1 to 720 (default 15), with no shift schedules. It also owns the
  **badge-confirmation options for sensitive actions**: three independent On/Off options (default On), each saved when
  it is changed, that decide whether the ALWAYS-present final confirmation gate of `DONE`, `QUEUE` return and Undo
  (§4.6) must be a Worker badge scan in Scanned-session Areas. With an option Off (and always in Fixed-Worker and
  Disabled Areas), the gate is a final confirmation question instead. The gate itself is never optional.
- **Machine assignment** is a read-only policy statement of the two Area ownership modes, not a Machine registry: a
  two-row statement (no Machines → `Direct processing (no Machines)`; one or more Machines →
  `Queue → assign (one-shot)`, never automatic) with no entry action, pointing to Management → Machines and the Areas
  table.
- **Correction permissions** holds the **Undo reason** switch: one global On/Off option (default Off) that makes a
  reason mandatory for every Undo (§4.5; PROJECT_PROFILE §16 "require a reason when configured"), enforced by the
  server and saved when it is changed. Who may undo or correct is a role × correction-permission table below the Undo
  reason switch. The section states which correction permissions are checked so far: Edit Work Order Allocation is
  checked for Management allocation and allocation reversal, and Undo recent eligible scans applies at the Scan
  Stations through the role marked Applied at Scan Stations; the other correction permissions are not checked yet.
- **History archival & purge** (Admin-only) is described under its own heading below.
- **Department display settings** are configured **per Department, never globally**: a table of Departments with an
  editor for the Production Board rotation timing (seconds per displayed row 1–60, minimum page dwell 1–300 s; defaults
  3 and 6).
- **Settings** hosts two panels:
  - the **Due Soon warning** panel owns the configuration behind every derived due countdown (§3 rule 12): **Minimum
    warning days** (0–365), **Lead-time warning percentage** (1–100) and **Maximum warning days** (0–365, never below
    the minimum), with the initial values 2 days, 15 % and 7 days. It is one global policy;
  - the **User sign-in** panel, for application Users only and distinct from **Worker sessions**: the user sign-in
    expiry in days or never (initially 30 days), the number of failed sign-ins before a lock (10) and the lock duration
    (15 minutes), and whether users must replace an administrator-set password at their next sign-in (On). It is read
    by a signed-in user (anyone else sees a prompt to sign in) and editable only by users whose role may configure
    system settings.

  The Settings section states that other application settings are not available yet.

### No Machines, no Route Templates and no PartNumbers sections

Machines, Planned Routes (Route Templates) and PartNumber master metadata are production master data owned by
authorized production roles. They are managed in **Management → Machines** (§12), **Management → Planned Routes** (§13)
and **Management → Part Numbers** (§14), and Administration keeps **no duplicate screens** for them (PROJECT_PROFILE
§20, §21). PartNumber master maintenance (metadata upkeep and hard deletion, following PROJECT_PROFILE §8.1 and §28)
lives in Management → Part Numbers.

### Barcode configuration — Machine Asset Tag format

The Barcode configuration section (Production setup) hosts the **Machine Asset Tag format** panel: a deliberately
simple prefix plus a zero-padded numeric sequence (PROJECT_PROFILE §8.6; for example `CD-` + 4 digits → `CD-0001`),
never a template engine.

- The panel explains that every Machine receives its Asset Tag automatically at creation in Management → Machines; that
  tags are unique, never reused (retired Machines keep theirs) and never change; and that the Machine barcode is the
  Asset Tag in the `PF:MACHINE:` namespace.
- It offers a **Prefix** text input (spaces and `:` rejected with an inline error) and a **Number length (digits)**
  input (1–8; the digit count is a minimum width, and longer sequences never truncate).
- It shows a live preview of the resulting tags (`CD-0001, CD-0002, …`), the **Next Asset Tag** against the current
  Machines, and the resulting **Scanned barcode**.
- A closing note states that a format change applies only to Machines created afterwards: existing Asset Tags are never
  renamed or regenerated.
- It is a settings form, not an entry table, so the section shows no `+ New entry` action.
- The panel is persisted configuration backed by the `/api/barcode-configuration` surface. It reads and saves the live
  format; the **Next Asset Tag** preview follows the server's persisted never-reuse counter (a format change never
  resets the sequence); and Machine creation in Management → Machines allocates from this same persisted format.
- Invalid input is rejected as entered: a prefix containing whitespace or `:`, or a digit count outside the whole
  numbers 1–8, shows an inline error and disables Save. Nothing is trimmed, clamped or substituted to force validity.

### Persisted configuration and sections not available yet

The **Minimum Environment Setup** sections of IMPLEMENTATION_ROADMAP (Departments, Areas, Operations, Scan Stations and
Barcode configuration) are persisted configuration UI backed by the `/api` surface. The tables and editors read and
write the live environment with explicit loading, error and retry states; server-rejected writes are shown in place
(nothing is silently confirmed through); and the offline write block gates every write action. The Areas table and
editor configure the Worker ID mode: Disabled, Fixed Worker with its Fixed Worker, or Scanned session.

Workers, Users, Roles & permissions, Correction permissions, Worker sessions, the Department display settings, the
Settings section's Due Soon warning and User sign-in panels, the History archival & purge retention period and the
Machine-assignment statement are persisted configuration as well. Users and Roles & permissions state that users sign
in with a login name and password and which permissions are checked so far.

Everything else presents itself honestly. A section with no defined content states that it is not available yet, with
its entry action disabled; it is never made to appear functional.

- **Scan behavior** states that it is not available yet and that its settings have not been defined, with its entry
  action disabled.
- The Settings section states that other application settings are not available yet.
- History archival & purge states that archival and purge runs are not available yet (see below).

Scan behavior and the remaining general settings have no defined content (IMPLEMENTATION_ROADMAP lists them as
`Deferred`). None of these statements promises a phase. Machines, Planned Routes and Part Numbers stay managed in Management by
authorized production roles.

### Access to Administration

- **Sign-in.** Every Administration section needs a signed-in user. Entering Administration while signed out opens the
  sign-in dialog, and a sign-in panel replaces the sections until then (Cancel leaves it).
- **Expired sign-in.** An expired sign-in reopens the sign-in dialog over the open section and keeps its work for the
  same user, while a different user signing in starts the sections fresh. A theme save that finds the sign-in ended
  opens no dialog (§2.1): the dialog then opens at the section's next refused request.
- **Password replacement.** While a password that an administrator set must still be replaced, the sections wait behind
  the `Choose a new password` dialog (the same user's open work stays).
- **Viewing and changing.** Any signed-in user may view the sections. Each section's changes need its own permission
  (Departments, Areas, Operations, Workers, Scan Stations, Barcode configuration, Worker sessions, Correction
  permissions, Settings, and Roles & permissions / Users each name theirs). Without it the section is view-only: its
  controls are hidden and a `View only` line names the permission. The server checks these permissions.
- Worker badges are shown only to users who may manage Workers.
- **Correction permissions guard.** Changing correction permissions, the permission to manage them, or users whose role
  holds them needs the permission to manage correction permissions. The correction permissions in Policies →
  Correction permissions are edited with that permission.
- **Last-holder rule.** PartFlow refuses a change that would leave no active user with a password who may manage users
  and roles, or who may manage correction permissions.
- Permissions that grant nothing yet are marked as such in Roles & permissions.
- **Applied at Scan Stations.** The role marked **Applied at Scan Stations** (initially Operator) decides what every
  enrolled Scan Station may do.
- Where this document says "Admin-only" (History archival & purge), it means the permission to configure system
  settings, initially granted to the Administrator role (PROJECT_PROFILE §20).

### Scan Station devices

Production setup → **Scan Stations** gives each station a `Devices…` action that opens its enrolled devices and
unexpired enrollment codes, listing name, state (`Enrolled`, or `Code issued` with its expiry) and last seen (the
device's most recent request, including refused ones). A revoked device or an expired code is no longer listed once the
list is loaded again.

- `Enroll device…` asks for a device name and shows a one-time enrollment code (valid 15 minutes, single use, shown
  once) to enter on the station device (§4.13).
- `Re-enroll…` issues a code that replaces an enrolled device when the code is used, so the station never goes dark in
  between.
- `Revoke…` stops a device at once; a pending code is cancelled the same way.
- Enrolling needs the permission to manage Scan Stations and, while the role applied at Scan Stations holds a
  correction permission, the permission to manage correction permissions. Revoking needs only the first.
- Without the permissions, the matching controls are hidden and a note names what is missing.

### Workers and Users

The **Workers** screen (Organization) manages Worker profiles: name, badge barcode (the company's existing employee
badge, §4.12), avatar and active status. Workers have a stable internal id and no employee number. The **Users** screen
(Access) is separate and manages application accounts and their avatars. Workers and Users are never merged into one
entity (§4.12, PROJECT_PROFILE §8.13).

- **Workers editor.** It saves the badge in capital letters and shows a `Saved as:` preview when that differs from what
  was typed or scanned. It uploads an avatar (PNG, JPEG or WebP; a large image is resized before upload) or removes it,
  shows initials when there is no avatar, and deactivates a Worker without any delete.
- **Users editor.** It saves the name, a login name in small letters (with a `Saved as:` preview), exactly one role and
  an optional avatar, and deactivates a user without any delete. The section states that users sign in with a login
  name and password, that `Set password…` gives a user a password, and which permissions are checked so far. For user
  administrators the Users table also has a `Sign-in` column showing each user's sign-in state, and `Set password…` sets
  a temporary password, ends the user's sign-ins and clears a lock.
- **Roles & permissions** lists named roles (initially Administrator, Manager and Operator, with exactly the
  capabilities PROJECT_PROFILE §20 lists as initial grants) and edits each role's name and its permissions, grouped as
  Administration, Production master data, Work Orders, priority and reports, and Scan Station. The four correction
  permissions (Undo recent eligible scans, Perform quantity corrections, Edit Work Order Allocation, Perform authorized
  historical corrections) are edited only in Policies → **Correction permissions**, as a role × permission table below
  the Undo reason switch.

### History archival & purge

This is Admin-only maintenance of the Movement-history retention (PROJECT_PROFILE §28 Administrative Archival and
Purge).

- **Target design.** A configurable retention policy (for example N years in the primary database), a size threshold,
  or a manual request triggers a lossless archive export → verification → purge of exactly the archived rows, never
  purge first. It runs only through the privileged Admin maintenance path (normal runtime can never UPDATE or DELETE
  Movement rows) and keeps related Movements together (for example a `REVERSED` correction and its original), so no
  retained row references a purged one. It has a scope and impact preview, a required reason, and a full audit of who
  ran what and when. Retention settings live here, never in production workflow logic.
- **Retention period.** The section stores the **retention period** as audited configuration that no production
  workflow reads. The form offers **No retention period** (the initial state) or **`Keep a set period of history`** with
  a whole number of months from 12 to 1200, and a neutral line restates the period in years and months (for example
  `Retention period: 10 years.`). Saving asks for no confirmation, because it archives or deletes nothing.
- **Not available yet.** Archival and purge runs (archive export, verification, purge, the scope and impact preview,
  the reason), the data-size threshold and manual requests are not available yet (IMPLEMENTATION_ROADMAP). Until they
  are, the section states that they are not available yet and that all Movement history stays in the database.

---

# 10. Completion / Receiving UI (Stockroom Scan Station)

The Stockroom station reuses the Scan Station shell with one additional step. After the `STOCKED` Movement, an
**allocation dialog** shows the suggested split across outstanding Work Order Demand in the exact canonical demand
ordering of PROJECT_PROFILE §18: ① highest Hot rank; ② dated demand earliest first, with undated demand after all dated
demand, ordered by Work Order received date.

- Each row shows the Work Order, requested quantity, previously allocated quantity, remaining shortage and the proposed
  quantity, adjustable with +/− steppers and a clamped quantity input. Operators may review and adjust the suggestion
  before confirmation (PROJECT_PROFILE §18).
- The allocated total against the stocked quantity stays visible. `Confirm allocation` is enabled only when the
  allocated total equals the stocked quantity.
- Routine receiving requires no Manager involvement. Administrators and Managers may adjust the allocation later, with
  every change audited (§11.6).

### Receiving flow at the Stockroom station

The Stockroom receiving is production UI on the `/api` surface, on the same Scan Station shell.

1. **Scan.** At a station bound to a terminal Area, a PN scan opens `Receive into Stockroom` (never the ordinary
   transfer) for exactly the sources the **server** marks as stockable (`stock_available` on the PN resolution): one
   source directly, several through the explicit source selection. A terminal station with nothing to receive explains
   it with the stocked and not-yet-allocated quantities, and nothing is recorded.
2. **Receive.** The receiving wizard keeps the transfer wizard's quantity 1 to MAX, with a partial quantity split by the
   server inside the same command, the route-deviation confirmation and the one-shot write model (a lost response is
   retried under the same `device_event_id`). `Confirm stocking` is the only write point, and `STOCKED` is named on the
   `Recorded event(s)` row.
3. **Allocate.** After the server has confirmed the write, the allocation dialog shows the server's suggestion for
   exactly the stocked quantity: every outstanding line in the canonical demand ordering, with Work Order, requested,
   previously allocated, remaining shortage and the proposed quantity. The confirmation sends the stocked quantity as
   the allocation quantity.
   - A server refusal keeps the dialog open with its reason and a refreshed suggestion.
   - A lost response freezes the lines for a retry of the same intent.
   - Success is reported only after the server's answer, naming the Work Orders that were completed. The inventory then
     refreshes and the barcode input is refocused.
4. **Allocate later.** `Leave in stock — allocate later` records nothing.

Stocked quantity is never undone: the Undo stack skips it (§4.5).

---

# 11. Work Orders

Work Orders is a Management sub view (it follows the global theme mode, §2.1) for manual Work Order entry, file import
(§11.7) and explicit production release (PROJECT_PROFILE §13; §21 *Work Orders*). It handles business demand only; it
is not enterprise resource planning (ERP)-style customer, pricing, invoicing, shipping, purchasing or accounting
functionality.

**Routes.** The view lives on two routes. The **active WO list** on `/management/work-orders` is the entry screen. The
read-only **Completed Work Orders page** (§11.5) lives on `/management/work-orders/completed`: a real route that can be
deep-linked (not a toggle or a dialog), because the completed history is unbounded and must survive refresh, deep links
and browser back/forward. Both routes belong to the Work Orders sub view: the Management sub-view bar keeps
**Work Orders** active on either, and the last-used sub-view restoration (§1.1) always re-enters through the active
list, never deep inside the history.

**Dialogs, not routes.** Selecting a Work Order opens **Work Order Details as a modal dialog over the list**: the list
stays mounted and visible behind the overlay, and the URL never changes. **New Work Order** is likewise a **modal dialog
over the WO list**. There is no separate detail panel or view and no per-Work-Order detail route.

**Editable dates use native calendar controls** (`<input type="date">`) in every Work Orders form: the New Work Order
received date and WO due date, the OPEN Work Order due date, and every demand-line due date. Editable values are ISO
`YYYY-MM-DD` internally; read-only presentation formats them as `Jul 24, 2026`. No date-picker dependency is added: the
native accessible control works in both theme modes (`color-scheme` follows the theme) and stays keyboard-accessible.

**How Work Orders works with the server.** The Work Orders view is persisted Management UI backed by the `/api` surface
(work orders, part numbers, production release, and the read-only route-template listing that a `PLANNED` release
selects from).

- The active WO list, New Work Order, Work Order Details, demand save and removal, and the explicit production release
  of §11.4 read and write live backend state, with explicit loading, error and retry presentation, server-rejected
  writes surfaced in place, and the offline write block gating every write action.
- A demand line's released state is derived by the server. A line whose quantity has been released arrives **Released
  with its restricted edit** from the first load (§11.2; PROJECT_PROFILE §13: Qty, due date and Job Numbers stay
  editable, the PN and Request Type are fixed, and removal is refused). The backend enforces the same restriction, so
  the rule is never UI-only.
- A Work Order presents as **Released** only once every line is fully released. A demand may be released in **parts**:
  a partly released line shows what is actually released (`Released 20/50`), keeps its **Release to production…**
  action for the remainder, and keeps the Work Order **Open** until nothing is left to release.
- The **Completed Work Orders page (§11.5)** is production UI on `GET /api/work-orders/completed`. Completion is derived
  from allocation (the Stockroom receiving of §10), and a completed Work Order leaves the active list the moment the
  completing allocation is recorded.

**Copy discipline.** All normal Work Orders copy is concise operator and manager wording (what to do, what will happen,
why an action is unavailable, how to recover), never implementation notes, architecture explanations or repeated
specifications. One explanation lives in one place: the same rule is never restated across the introduction, card
header, field hint, footer note, toast and confirmation. Legitimate audit-facing Movement and event names stay only on
the approved audit surfaces (§3 rule 10).

**Responsive layout.**

- The wide editable demand-line tables are the desktop presentation only. At tablet and mobile widths every demand line
  renders as a labeled card-style block (label over value, with the PN and row actions spanning the card) instead of
  forcing a horizontally scrolled desktop table.
- The two large workflow dialogs (New Work Order, Work Order Details) take a near-full-screen treatment on narrow
  viewports, with the heading and primary actions easy to reach (sticky dialog actions on a solid surface, never
  covering content once scrolled to the end).
- The inner line-list scroll region is removed at these widths, so nested scrolling exists only where it clearly helps
  (the desktop line list with its sticky header).
- All fields, validation messages, long PNs, WO Numbers and Job Numbers stay readable, and the existing touch-target
  standards hold.
- An optional top-right close ✕ may be added later, but it must route through the existing dirty-close
  (discard-confirmation) logic rather than bypassing it.
- Child dialogs keep stacking, so only the topmost dialog owns Escape, backdrop behavior and focus trapping.

## 11.1 WO list

- **Rows.** One row per Work Order: WO Number, received date, **WO due date** (color-ramped like all due dates; `—` when
  absent, §3 rule 9), demand-line count with a PN preview, and status (**Open** / **Released**). A completed Work Order
  never appears in the active list (§11.5), so `Complete` is not an active-list status.
- **Search.** The search is over the WO Number. An existing WO Number is always opened, never duplicated. A miss offers
  "＋ New Work Order" **and points at the completed history**:
  `No active Work Order matches "…" — search Completed Work Orders, check the number, or create it with ＋ New Work Order`,
  so nobody re-creates a Work Order that has already run to completion. The search is a **server** query over the Work
  Order Number: the entry travels after a short typing pause, the match is made in the database, and the list is never
  downloaded whole to be filtered in the browser. It is scoped to the number; PNs and Job Numbers are searched on the
  Completed page (§11.5), not here.
- **The active list is bounded by the server.** A Work Order leaves the active list only through completion derived from
  allocation (§11.5: every line fully allocated, whether the last allocation or a demand change made it so), so the list
  grows with intake and shrinks with completion alone. One read returns at most the server's page (100 Work Orders,
  newest first), and a full page says so in a quiet line above the list:
  `Showing the first 100 Work Orders — refine the search to narrow it.` A search still finds a Work Order beyond that
  page, because the match is made before the bound. The New Work Order duplicate check resolves the exact number over
  the whole history and is never bounded away. While a search is in flight, the same quiet line reads
  `Searching Work Orders…`, and the rows already on screen stay put rather than collapsing into a loading state.
- **Full-width list with one toolbar row.** The WO list spans the full view width. The search field, the quiet
  secondary **`Completed Work Orders ›`** action (the entry to §11.5), the secondary **`Import from file…`** action (the
  entry to §11.7; hidden, not disabled, for users without either Create and edit Work Orders or Edit Work Order
  Demand) and the **＋ New Work Order** button share one toolbar row, right-aligned to the list, in the same toolbar
  layout as Machines (§12.1). The primary action stays ＋ New Work Order; the history entry is deliberately quieter.
- **＋ New Work Order opens the modal dialog of §11.3 over the list.** The list stays mounted and visible behind the
  overlay, and the URL remains `/management/work-orders`.
- **Internal Work Orders.** Internal Work Orders without an external number (for example from a Scan Station MODIFY
  intake, PROJECT_PROFILE §14) appear like any other Work Order. The blank number renders as `—` (display-only, never
  persisted), the row is clearly labeled "internal Work Order — no external number yet", and the Work Order is found
  through its PN preview. No temporary Work Order Number is ever generated.
- **Completed Work Orders** (every Work Order Demand fully allocated) leave the active list the moment the completing
  allocation, or the demand save or line removal that leaves every remaining line fully allocated, is recorded. They
  remain permanently available on the **Completed Work Orders page** (§11.5; PROJECT_PROFILE §8.2). The list's page note
  links there.
- **Demand lines are shown only after a Work Order is selected.** Selecting a row opens the **Work Order Details** dialog
  (§11.2). **Whole-row activation:** the entire row is the click target; a single row button (accessible name
  `Open Work Order {no}`) is the keyboard and screen-reader entry. Enter and Space activate it, and closing the dialog
  returns focus to the row. There is no visible focus ring on the row button (unlike Tracking, §7).

## 11.2 Work Order Details — modal dialog with demand lines

- **The dialog.** Selecting a Work Order opens a modal dialog titled **`Work Order Details`** over the list, built on the
  shared accessible dialog primitive (`role="dialog"`, `aria-modal`, focus trap, initial focus, Escape and backdrop close
  requests, responsive sizing with internal scrolling). On close, focus returns to the Work Order row that opened it.
- **Primary actions** are **`Save demand`** and **`Cancel (Esc)`**. Closing through Cancel, Escape, the backdrop or any
  other close request never silently discards edits: a dirty draft asks for explicit discard confirmation first, and a
  clean dialog closes directly.
- **Child dialogs** (Add Part, removal and release confirmations) stack correctly: only the topmost active dialog
  handles Escape, backdrop and focus trapping, and a child's close request never closes Work Order Details.
- **Dialog header.** WO Number, received date, WO due date (**editable with a calendar control while the Work Order is
  Open**), line count and status. Inside the dialogs (this one and §11.3) the heading renders as the shared uppercase
  eyebrow above the strong WO identity, with the meta line beneath it separated by a hairline divider, and the card and
  note width caps are lifted. This is presentation only: native calendar date inputs, the card-style demand-line layout
  at narrow widths, the dirty-close discard confirmation, the released-line edit restriction, focus on the first invalid
  control after a failed save, and correctly stacked sibling dialogs all stay as specified.
- **WorkOrderDemand rows.** An editable table with one row per PN demand: PN (lookup or create: any PN text that is
  non-empty after trimming and free of internal whitespace is accepted and created on first use, canonicalized to the
  uppercase PN; internal whitespace is rejected, never silently removed), Request Type (`NEW` default / `MODIFY`),
  requested quantity, due date (calendar control), priority when applicable, external Job Numbers, and requester /
  reason / notes. Long WO line lists scroll with a sticky header and a line count.
- **The PN control on a demand line.** On every line carrying a canonical PN, **the PN itself is the control**.
  - Activating it opens the shared **`Edit Part Number`** dialog (§14.2): metadata editing, with the printable barcode
    label reachable from inside it through `Barcode label…` (§14.3), the ONE PN label dialog in the application. A PN
    without saved details opens the same dialog as **`New Part Number`**, with the PN fixed.
  - A small **pencil (edit) icon** after the PN marks it as activatable, and the control replaces the plain PN text in
    place. For a user who may not manage Part Numbers, the PN stays the control without the edit glyph and opens the
    same dialog read-only (`Part Number details`, §14.2), so the barcode label stays reachable.
  - The control costs **no extra row height**. An unsaved draft line whose PN has no master record yet carries the
    quiet `new PN` marker beside it on the same line; the marker follows the existence the dialog last observed.
  - The barcode derives entirely from the canonical PN, so a saved line and an unsaved draft line print alike. It is
    never rendered as row prose; it is always the derived `PF:PN:<part-number>`.
  - Opening the dialog is presentation only: it never touches the draft, the dirty state or release behavior (the
    dialog's own writes concern only the Part Number details, never the demand), and it stays available on a released
    line.
  - The Add Part flow's own `Barcode label…` link stays a label link.
- **Adding demand lines (manual first).** An **OPEN** Work Order shows a prominent **＋ Add Part manually** action;
  closed or completed Work Orders remain read-only. Add Part manually opens the multi-step Add Part dialog of §11.3
  (PN → quantity → due date → optional metadata). Barcode scanning remains available as a **secondary, optional
  method**: it accepts only valid PN barcodes and rejects unknown barcodes. A new line joins the Work Order as a visibly
  marked **unsaved draft**: Request Type defaults to `NEW`, and the due date defaults to the WO due date when one exists.
  Scanning or entering a PN already on the Work Order focuses the existing line instead of adding a duplicate; on a
  released line the announcement names the fields that are still editable there.
- **Released demand lines stay editable within limits** (PROJECT_PROFILE §13 *Restricted edit after release*).
  - A line with released production quantity renders **Qty**, **due date** and **Job Numbers** as normal editable
    fields, on an Open and on a Released Work Order alike, so a Released Work Order still offers **`Save demand`**. Its
    **PN** and **Request Type** render as plain read-only values.
  - The Qty field carries no standing note. It speaks only when the entry is actually wrong: a value below the committed
    quantity turns the field into its normal red per-field error **as it is typed** (`≥ 10 pcs released`), clears again
    the moment the entry is corrected, and never travels to the server.
  - Raising the Qty returns remaining quantity to the line, so the Work Order derives back to **Open** and the line
    offers **Release to production…** again.
  - The OPEN-only scope still applies: a Released Work Order shows no ＋ Add Part manually, no scan row, no editable WO
    due date and no row actions except the allocation actions of users who may adjust allocation (§11.6), and a released
    line can never be removed.
- **Allocation of a saved line.** Each saved line shows `Allocated a/b` (with `+n beyond demand` when an authorized
  correction allocated beyond the demand). For users who may adjust allocation (Edit Work Order Allocation), it also
  shows `Allocate from stock…` and `Reverse…` on Open, Released and Completed Work Orders alike. Both open the shared
  dialog of §11.6 and are disabled while the demand draft has unsaved changes. Otherwise the restricted Qty edit applies
  as described: an unchanged Qty is never an error (a line allocated beyond its demand keeps its other fields editable),
  and a changed Qty is never below the released or allocated quantity.
- **Removing demand lines** follows the canonical Work Order Demand removal rule (PROJECT_PROFILE §13):
  - an unsaved draft line is removed immediately;
  - a saved line with no released production quantity is removed only after an explicit confirmation dialog;
  - once any quantity has been released to production, the line's remove action is disabled with the explanation
    "Cannot remove: production quantity has already been released."; later adjustments go through correction and
    production workflows, never deletion;
  - a saved line on the Hot list shows a warning naming its Hot rank (`🔥#n`) and is removed only after the PN is typed
    to confirm (the shared typed-confirmation dialog); the removal also takes it off the Hot list, and the remaining Hot
    ranks close the gap. This applies to a line with no released quantity and no allocation, current or reversed: a
    line with allocation history is never removable, so it gets the plain confirmation and the server's refusal
    instead;
  - removal never deletes the PartNumber master, Quantity Flows, Movements, release history, or other Work Order Demand
    for the same PN.
- **Due-date default.** Each line's due date defaults to the **WO due date** (when one exists) and may be edited per
  line. A blank due date is valid and displays cleanly (§3 rule 9). Changing the WO due date updates only lines still
  holding the previously inherited default; a line whose due date was changed manually keeps its value, and a line
  explicitly set to **"No due date"** counts as edited by the user and never inherits a later WO due date. For a user
  who may change the Work Order header but not its demand lines (Create and edit Work Orders without Edit Work Order
  Demand), changing the WO due date changes only the WO due date: every line keeps its due date, and the dialog says so
  beside the field (`Line due dates stay unchanged — you may not edit demand lines.`).
- **PN lookup and create.** The PN field searches existing PartNumbers. A new PN can be created inline, which shows a
  **barcode preview** (`PF:PN:…`; the barcode is derived from the canonical PN itself) and creates the PN master
  metadata record. **"This PN does not exist yet" is only ever said after the server has answered for exactly what is
  in the field:** while the entry is still settling or its lookup is in flight, the step says `Searching Part Numbers…`
  and offers no creation, so an existing PN (a one-character PN especially, which resolves through the exact lookup
  rather than the contains-search) is never momentarily presented as new. Once the lookup for exactly the current entry
  has answered that the PN does not exist, the step offers `＋ Create new PN "…"`. The PN master has no active or
  inactive state: a canonical PN is always usable, with or without a master record.
- **Validation states.** Per-field errors (missing PN, quantity not a positive integer, duplicate PN). A row with errors
  cannot be saved and is **never silently filtered out**. A **missing due date is not an error and never blocks
  saving** (PROJECT_PROFILE §8.3); absent dates are summarized in the save confirmation instead (§11.3). After a failed
  save the first invalid control receives focus and all entered values are preserved.
- **Unsaved changes** are visibly marked ("● Unsaved changes") and guarded. Leaving the Work Order (through the back
  action, top-level navigation, Management sub-navigation, browser back/forward, or reload or tab close,
  `beforeunload`) requires explicit confirmation before the draft is discarded. The state means what it says: it is the
  actual difference between the draft and the loaded Work Order, not "something was typed once". Undoing an edit
  (retyping the committed quantity, removing a draft line again, clearing the external-number entry) returns the dialog
  to no unsaved changes, so the marker disappears, closing asks nothing, saved-line removal becomes available again,
  and **Release to production…** stops asking to settle the draft first (§11.4), all without a save or a reload.

## 11.3 New Work Order — manual-first modal dialog and Add Part flow

- **Presentation.** New Work Order opens as a **modal dialog over the WO list**. The dialog has `role="dialog"`,
  `aria-modal="true"`, an accessible name from its visible heading, initial focus inside the dialog, keyboard focus
  trapping, Escape and backdrop-click close requests, focus restoration to the **＋ New Work Order** button on close,
  and responsive sizing with internal scrolling for long line lists, and it follows both theme modes. The URL never
  changes.
- **Nothing is silently discarded.** Escape, a backdrop click, Cancel or any other close request on a dialog with
  entered data first asks for explicit confirmation ("Discard this New Work Order?").
- **Header form.** WO Number (**optional**), received date (calendar control, defaults to today, always available) and
  **WO due date** (calendar control, **optional**), which is the default due date for demand lines when set.
  - The dialog explains the consequences plainly: a blank WO Number saves an internal Work Order with a **null** number
    that displays as `—` and may receive the real external number later through an audited edit (PROJECT_PROFILE §7;
    no temporary number is generated); due dates can be added later; and the Work Order can be saved without a due
    date.
  - Entering a WO Number that already exists opens the existing Work Order instead of duplicating it. **Duplicate
    handling applies only when a WO Number was entered.** If lines were already entered, opening the existing Work
    Order is confirmed explicitly first.
  - The uniqueness check spans the **whole history, including completed Work Orders**: entering the number of a
    completed Work Order announces the completion and opens its read-only details (§11.5). A completed number is never
    silently reusable just because the Work Order has left the active list.
- **Manual Part addition is the primary workflow.** A prominent **＋ Add Part manually** action opens **one accessible
  multi-step dialog** (no stacked nested modals):
  1. **PN**: search and select an existing PartNumber, or explicitly create a new one (with a barcode preview, §11.2).
     The search matches the PN and the saved Name and Description, and each result shows the saved name (the derived
     barcode when none is saved);
  2. **quantity**: a positive whole number, with the same keypad and physical-keyboard interaction as the Scan Station
     quantity dialog (§4.8). The shared `QuantityKeypad` owns its stylesheet (`components/QuantityKeypad.css`, imported
     by the component), so the step renders correctly wherever it is mounted, never depending on Scan Station CSS or
     route navigation order;
  3. **due date**: defaults to the WO due date when one exists; an explicit **"No due date"** choice is valid;
  4. **optional metadata**: Job Numbers, Notes, Request Type.

  Backward navigation preserves entered values. Completing the dialog creates an editable **draft row**; nothing is
  persisted until Save demand.
- **Barcode scanning remains available as a secondary, optional method.** A PN barcode carries the PN itself
  (`PF:PN:<part-number>`): the entire non-empty suffix is the PN, a PN outside the catalog is created on first use, and
  non-PN barcodes are rejected with an error, with nothing added. Scanning a PN that is already on the Work Order
  focuses the existing line (to edit its quantity) instead of adding a duplicate. The Work Order entry workflow is
  **not scanner-first**: it is desk work. The Scan Station shop-floor workflow (§4) remains scanner-first.
- **Due dates.** Changing the WO due date updates only lines still inheriting the previous default; lines whose due
  date was edited, including an explicit "No due date", keep their value (§11.2). A user without Edit Work Order Demand
  changes only the WO due date, and every line keeps its due date (§11.2). Request Type and due date stay editable per
  line before saving.
- **Save demand with omission confirmation.** Save validates the header and all rows (§11.2 validation states:
  field-level errors, first invalid control focused, values preserved, incomplete rows never dropped, missing due dates
  never blocking). When the external WO Number, the WO due date or any line due dates are absent, a **confirmation
  dialog summarizes the omissions** before anything is saved: the Work Order is saved as an internal Work Order without
  an external number (displays `—`), the Work Order remains unscheduled, and N undated lines get the lowest due-date
  priority with later received-date ordering (PROJECT_PROFILE §18). Save happens only after explicit confirmation;
  Cancel returns to editing with all values preserved. On save the dialog closes, and the new Work Order appears in the
  list as **Open**. The save persists the Work Order and its demand lines through the `/api` surface: business demand
  only, never production quantity (§11.4).

> **Data-model note:** the WO due date is the nullable `due_date` attribute on WorkOrder (PROJECT_PROFILE §8.2). It is
> an entry default only; WorkOrderDemand keeps its own nullable `due_date` as the operative business value.

## 11.4 Demand save vs. production release

- **Save demand** persists the WorkOrder and WorkOrderDemand rows only. Saving never creates production quantity, and
  the UI states this explicitly ("business demand — separate from production").
- Each saved demand row carries an explicit **Release to production…** action, offered while the line still has
  remaining quantity. A demand may be released in several parts (20 of 50, then 12, then 18), and the action closes
  only once the remainder reaches 0.
- **A release always runs on the SAVED demand, and unsaved changes are settled explicitly first.** The release
  quantities come from the committed Work Order, never from the draft on screen, and a committed release reloads the
  details, so releasing with edits in flight would both mislead about the quantity and discard the draft.
  - The action therefore stays available while the draft is dirty (its tooltip says
    `Releasing asks to save or discard demand changes first.`). When used, it opens the shared three-way
    **`Unsaved changes`** decision (§12.4, the same dialog that Machines and Planned Routes use):
    **`Save demand, then release…`** / **`Discard changes, then release…`** / **`Cancel (Esc)`**.
  - Save runs the ordinary Save demand path, including its validation and its omission confirmation, and opens the
    release on exactly what was committed. A failed save opens no release and keeps the draft.
  - Discard restores the draft from the saved Work Order and releases that.
  - Save is unavailable while the draft has validation errors, with the reason stated; Discard remains the way forward.
  - Nothing is ever saved or released implicitly.
  - Saved-line **removal** keeps its stricter rule: it stays disabled until the draft is saved or discarded (§11.2).
- **Releasing opens a confirmation flow that:**
  1. confirms the release quantity, defaulted to the **remaining** quantity and never more than it, with the demand's
     requested, already released and remaining figures stated (the backend enforces the same cap). The quantity field
     carries the cap where the number is typed: `available N pcs` sits inside the field, right-aligned in the quiet
     note tone, followed by a **`MAX`** shortcut that fills the available quantity (disabled when nothing remains);
  2. confirms the Route Mode: `FLOATING` (default, no Route required) or `PLANNED` with a selected Route (snapshot
     noted);
  3. confirms the configured starting Area and Operation. A **terminal** Area is never offered and never accepted
     (finished quantity ends there; production does not enter there). When the chosen Area has exactly **one** active
     Operation there is nothing to choose: that Operation is preselected (derived, so changing the Area re-derives it,
     and an explicit pick always wins). An Area with several keeps the choice open, and an Area with none states that it
     cannot start a release;
  4. **warns when the PN already has active quantity**, showing the existing distribution and requiring explicit
     confirmation of intent, never creating or merging quantity automatically;
  5. shows a **release summary before commit** (PN, quantity, Route, starting Area/Operation, Work Order Demand);
  6. on Confirm, reports the result: the created Quantity Flow id, the assigned Route, the starting Area, the quantity,
     and the appended `RECEIVED` Movement.
- Saving demand never triggers a release automatically; release is always a separate explicit action.

## 11.5 Completed Work Orders page

The page is the read-only history of every completed Work Order (completion means every Work Order Demand is fully
allocated, PROJECT_PROFILE §8.2).

- **Route and entry.** The page lives on its own route, `/management/work-orders/completed`, inside the Work Orders sub
  view. The entry is the quiet secondary **`Completed Work Orders ›`** toolbar action on the active WO list (§11.1), plus
  the link in the list's page note. The page leads with a **`‹ Work Orders`** back action that returns to the active
  list.
- **Unbounded by design.** Completed Work Orders are retained **permanently**, so the page is designed for an unbounded
  list from the first day: search, filtering, ordering and paging are a server-side contract over the whole history, and
  the page never assumes that the history fits in one response.
- **The done date is the page's central value.** It is the Work Order's `completed_at` (PROJECT_PROFILE §8.2): the
  timestamp of the event that left the last open Work Order Demand fully allocated (the allocation, or a demand save
  lowering its Qty to the allocated quantity, or the removal of the last line not fully allocated). It is shown as its
  calendar **done date** in the site's time zone: one server-side rule, `SITE_TIMEZONE`, for the displayed date, the Done
  range and the due outcome alike, never the browser's local date. A later audited allocation correction can make the
  Work Order incomplete again (PROJECT_PROFILE §8.2): it then leaves this page and reappears in the active list. Nothing
  is ever deleted, and the allocation audit trail keeps the full story.
- **Own toolbar, independent of the active list.**
  - A search field over **WO Number, PN and Job Number** (history lookups usually start from a PN or a Job Number, not
    only the WO Number); the search is debounced.
  - A **Done range** select with the presets `Last 30 days` / **`Last 90 days` (the default)** / `This year` /
    `Last year` / `All time` / `Custom…`. The presets travel to the server by name and are anchored there to the site's
    current date on the `SITE_TIMEZONE` calendar, so a browser near midnight or across a year boundary in another time
    zone never shifts the window. Custom shows two native date inputs (§11 date conventions) and sends those explicit,
    inclusive site-calendar dates.
  - A **Due outcome** select `All` / `On time` / `Late` / `No due date`, for quick delivery-performance queries.
  - The default 90-day window keeps the initial query and the visible list honest against years of history. It is
    presentation state only: never persisted and never silently widened. Search and filters combine (AND).
- **Result summary line.** Under the toolbar, a line restates what is actually shown: the count, loaded and total, and
  the effective range (for example `Showing 50 of 1,248 completed Work Orders · last 90 days`), so a bounded view is
  never mistaken for "everything".
- **Table.** The columns are **WO Number | Done | Received | Due | Demand lines**. There is no Status column (every row
  is Complete) and no due-date urgency ramp (lateness under §3 rule 9 never applies to completed work). The **Due** cell
  carries the date plus a quiet derived outcome line: `✓ On time`, `✕ N days late` (done date against due date, the
  server's verdict on the site calendar), or `—` when the Work Order had no due date. The ✓/✕ markers keep the signal
  from relying on color alone (the §12.3/§12.4 idiom). Internal Work Orders render exactly as on the active list (`—`
  plus the internal label), and demand lines keep the count plus PN preview presentation.
- **Sorting.** The date and identity columns (WO Number, Done, Received, Due) carry the shared sortable headers (§12.1:
  `aria-sort` on the header cell, ↑/↓ arrow, faint ↕ while inactive, stable order). The initial sort is **Done
  descending** (most recently completed first), and the unsorted state of the cycle returns to that default, because an
  unbounded history has no meaningful "registry order". On the Done header itself, whose descending order *is* the
  default, the cycle is therefore ascending ↔ descending, so every click changes the order and both directions of every
  sortable column stay reachable. Sorting is a **server** contract like everything else on the page: the chosen column
  and direction travel with the query, rows without a value (an internal Work Order's number, a missing due date) sort
  last in either direction, and the Work Order id is the stable tie-breaker. The page never re-sorts the rows it has
  loaded.
- **Paging.** The page loads the first 50 matching rows of the current order. A **`Show more`** action appends the next
  50, as a keyset continuation on the server's opaque cursor, bound to the sort it was issued for (never offset paging
  over an unbounded table). The server issues a cursor only when a further row actually exists, so a history ending
  exactly on a page boundary never offers an empty `Show more`. A continuation of a preset Done range keeps the range
  that its first page resolved: the site's midnight passing between two pages never re-anchors a loaded query. Changing
  the search, filters or sort resets the paging, and the summary line keeps the loaded and total counts visible.
- **Row activation** follows the shared whole-row pattern (§11.1: row button `Open Work Order {no}`, Enter/Space).
  Activating a row opens the same **Work Order Details** dialog (§11.2), read-only exactly as for every non-Open Work
  Order (its demand stays read-only; users who may adjust allocation keep the allocation actions of §11.2, and a
  reversal can reopen the Work Order, §11.6), with **`Done <date>`** added to the dialog's meta line and the allocated
  quantity shown per demand line. Closing returns focus to the row, and the URL never changes for the dialog.
- **Empty states distinguish "none ever" from "none in this range".** With no completed Work Orders at all, a plain
  empty state shows. With filters active, the empty state reads
  `No completed Work Orders match in this range — widen the Done range or clear filters.` When a **search text** finds
  nothing inside a bounded range, the empty state additionally offers a one-click **`Search all history`** action (it
  clears the Done range and keeps the search): a Work Order must never be presumed missing just because it completed
  outside the default window.
- **History only.** The page offers no New Work Order, no demand editing and no release actions, only the allocation
  actions of users who may adjust allocation (§11.6). Its toolbar state is fully independent of the active list's
  search. Its presentation (table, states, responsive behavior) matches the WO list (§11.1).
- **Server.** The page is production UI on `GET /api/work-orders/completed`. The search, the Done range (a named preset
  that the server anchors to the site's current date, or the Custom range's inclusive done dates), the due-outcome
  filter and the sort column and direction are server-side query parameters, and the done date and due outcome of every
  row are the server's verdict on the site calendar. A Work Order completes only when the server derives it (every
  demand line fully allocated from stocked quantity); it then leaves the active list and is never duplicated by a New
  Work Order lookup of its number (the lookup opens the completed details).

## 11.6 Allocation adjustment dialog

The shared **`Adjust WO Allocation`** dialog is the one Management surface for allocating stocked quantity that was
left for later, for the authorized beyond-demand correction, and for reversing an allocation (PROJECT_PROFILE §8.12,
§18). It is offered only to users holding Edit Work Order Allocation and is hidden from everyone else, never rendered
disabled or inert.

- **Entry points.** Tracking → Corrections → `Adjust WO Allocation…` (§7.2 item 8; the open Work Orders of the selected
  PN), and Work Order Details (§11.2; one saved line, on Open, Released and Completed Work Orders, including on the
  Completed Work Orders page, §11.5, where a reversal may reopen the Work Order). Tracking lists open demand only; a
  completed Work Order is adjusted from its Work Order Details.
- **Steps.**
  - `Overview`: the demand lines in scope with allocated and requested figures, `+n pcs beyond demand` where it applies,
    and the active allocations, each marked `beyond demand` when it was a correction.
  - `Allocate from stock`: one demand line, never above its remaining demand or the available stocked quantity, with an
    optional note.
  - `Allocate beyond demand`.
  - `Reverse allocation`.
- **Beyond-demand correction.** An explicit second step with a warning that the quantity exceeds the demand, the
  quantity, and a mandatory reason. It is recorded as a distinct correction with the signed-in user's name and is
  reversible like any allocation. A routine allocation never exceeds the remaining demand.
- **Reversal.** It needs a mandatory reason. It appends new history and never edits or deletes the allocation. It can
  make a completed Work Order incomplete again: the Work Order reopens and its done date is cleared, and a later
  completion records a new done date. A correction or reversal that leaves the Work Order complete never moves its done
  date.
- **Actor.** Each entry shows the user who recorded it, with an avatar and name.
- **Unknown outcome and sign-in.** One intent keeps one request identity through every resubmit. The inputs and the
  step navigation lock while a submission is in flight, and stay locked when the server did not answer, so the next
  submit can only repeat the same intent. An explicit refusal of that resubmit proves that nothing was recorded and
  unlocks the inputs. A sign-in interruption (the session ended) is an answered request in which nothing was recorded,
  so it keeps the intent and its request identity for the resubmit after signing in again; it neither locks nor unlocks
  the inputs. Closing while the outcome is unknown reloads the host and says so.
- **Offline.** While the connection is lost, the submit actions are blocked, and nothing is queued.

This dialog is referenced from §7.2 item 8, §7.3, §10, §11.2 and §11.5.

## 11.7 Import Work Orders from a file

The **`Import Work Orders`** dialog creates Work Orders from a prepared CSV or Excel file, or changes the Open and
Released Work Orders that the file lists (PROJECT_PROFILE §13 *File import*). It saves business demand only; nothing is
released to production.

**Who sees it.** The dialog is shown to holders of Create and edit Work Orders (`MANAGE_WORK_ORDERS`) or Edit Work
Order Demand (`EDIT_WORK_ORDER_DEMAND`); everyone else does not see **`Import from file…`** (hidden, not disabled, §1.1
*Access to Management*). Importing needs the permission or permissions the file's content requires: Create and edit
Work Orders for the Work Orders it creates, and Edit Work Order Demand for the existing ones it changes.

- **Placement and flow.** **`Import from file…`** sits in the §11.1 toolbar row between `Completed Work Orders ›` and
  `＋ New Work Order`. The dialog (extra wide) walks through these steps:
  1. choose a `.csv` or `.xlsx` file;
  2. **Check file**;
  3. read the per-Work-Order report;
  4. **Import** (labelled as described under *Changes to existing Work Orders*);
  5. read the result report.

  Check file is required before Import. The import re-validates the same file, and the server refuses a file that is not
  the one that was checked (the dialog then offers `Check file again`). Picking another file clears the report. Two
  template links (`CSV`, `Excel`) download a header-only template.
- **Help (in one place).** Required columns: Work Order Number, Part Number, Requested Quantity. Optional: Job Number
  and Due Date (`YYYY-MM-DD`). One row per Part Number. Format the Work Order Number, Part Number and Job Number columns
  as Text so leading zeros stay. Columns A–BL are read. For Excel files the first worksheet is read and must be visible,
  formulas are read as the value last saved in Excel (a formula that was never calculated reads as empty), and every row
  is imported, hidden or filtered rows too. Limits: 1 MB, 2,000 rows, 500 lines per Work Order, 200 characters per text
  cell.
- **Report.**
  - A summary line `Will create {a} · Will change {u} · Already in PartFlow {b} · Not imported {c}` (after the import:
    `Created {a} · Changed {u} · …`), `Worksheet read: {name}` for Excel files, the rows read, the empty rows ignored and
    the ignored columns.
  - A table `WO Number | Rows | Lines | Result` lists REFUSED Work Orders first. Each row discloses its lines
    (`Row · PN · Qty · Due · Job`; dates as `Jul 24, 2026`, an absent date or Job Number as `—`) and, when refused,
    every error as `Row {r} · {column} — {message}`.
  - Result labels:
    - `Will be created` (with the count of new Part Numbers) and `Created`;
    - `Will change — {k} changes` / `Changed — {k} changes` (see *Changes to existing Work Orders*);
    - `Already in PartFlow — nothing to change`: an Open or Released Work Order that the file leaves as saved;
    - `Already in PartFlow — not changed by this import`: a completed Work Order, with
      `Differs from this file — this Work Order is completed and is never changed.` when it differs. The sentence
      "For completed Work Orders, due dates and Job Numbers are not compared." is shown once under the table, and only
      when such a row is shown. The same label marks a Work Order Number that someone created between the check and
      the Import, which the import never compared, with
      `Differs from this file — check the file again to see the changes.` when it differs;
    - `Not imported — fix the rows listed`.
  - Status never relies on color alone.
- **Omission statement.** Every preview that will create Work Orders states
  `Imported Work Orders get no Work Order due date — they stay unscheduled.` Every preview states
  `{n} lines have no due date and sort after dated demand.` when lines that the import writes carry no due date (an
  update-only preview too; a blank Due Date on an existing line keeps the saved date and is not counted). **Import**
  (with the typed confirmation when the file changes Work Orders) is the confirmation of these omissions (as in §11.3).
- **Blocking.** Rows without a usable Work Order Number are listed under `Rows without a usable Work Order Number` and
  disable Import. When there is nothing to create or change, the dialog shows `Nothing to create or change.`
- **Offline and in flight.**
  - While the connection is lost, Check file and Import are disabled (`Reconnect to check or import the file.`), and
    nothing is queued.
  - During the import the dialog cannot be closed or cancelled and shows `Importing… Keep this page open.`, and leaving
    the page asks for confirmation.
  - A lost outcome shows
    `The import may be partly saved. Check the file again: Work Orders already in PartFlow are never duplicated, and changes already saved are not listed again.`
    and is never retried automatically.
  - While the page is outdated (§3 rule 13), Check file and Import are disabled with
    `PartFlow was updated — reload the page to continue.` A running import is never interrupted by a reload (Work Orders
    reloads only manually, after the existing leave guard).
- **Focus and narrow viewports.** Initial focus is on the file control; a new report moves focus to its heading; errors
  are announced as alerts; and closing restores focus to **`Import from file…`**. On a narrow viewport the table
  becomes labeled card blocks with a sticky footer.

### Changes to existing Work Orders

- **What the file can change.** For a Work Order Number already in PartFlow, the file changes quantities, sets due
  dates, adds Job Numbers, and adds lines to Open Work Orders. Empty cells and lines not in the file keep their saved
  values, and an import never removes a line (the Help states this in one sentence).
- **Show changes.** A `Will change` / `Changed` row discloses **`Show changes`**, with one line per change:
  - an edited line: `Row {r} · {PN} · Qty {a} → {b} · Due {a|—} → {b} · Job Numbers {a|—} → {b}` (only the changed
    parts, plus ` · leaves the Hot list` when a fully allocated Hot line is lowered);
  - an added line: `Row {r} · Add {PN} · Qty {q} · Due … · Job … · new Part Number`;
  - then `Completes the Work Order — every line becomes fully allocated.` when the changes complete it;
  - and `Kept, not in this file: {PNs}` for saved lines that the file does not list (the same kept line shows on an
    `Already in PartFlow — nothing to change` row).
- **Refused changes.** A line added to a Released Work Order, or a quantity below the released or allocated quantity,
  makes that Work Order `Not imported`, with the row error.
- **Typed confirmation.** **Import** with at least one change opens a stacked typed confirmation (the §11.2 pattern):
  - title `Change {m} existing Work Order(s)?`;
  - body `These Work Orders are already in PartFlow. Import applies every change below; each Work Order is saved on its own.`
    (plus `It also creates {a} new Work Order(s).`);
  - a scroll region listing **every** change per Work Order under `WO {number} · {Open|Released}`;
  - the typed value `CHANGE {m}` (labeled `Work Orders to change`) and the button
    `Import and change {m} Work Order(s)`;
  - Cancel or Esc sends nothing.
- **What the confirmation binds.** The typed confirmation binds the changes shown, not every later edit of the Work
  Order. When the changes shown no longer match at Import, every Work Order to change is `Not imported`, and a Work
  Order that changes after the Import re-checked it is `Not imported`, each with the reason. A change by someone else
  that leaves the shown changes as they were does not stop them. When a Work Order was not changed, a line above the
  table says
  `Some Work Orders were not changed because they changed after the check. Check the file again to see and confirm the current changes.`,
  and **Check file again** keeps the file and requires a new typed confirmation. Work Orders to create still import.
- **Button labels.** `Import {n} Work Order(s)` for creates only, `Change {m} Work Order(s)…` for changes only, and
  `Create {n} Work Order(s), change {m} Work Order(s)…` for both.
- **Missing permissions.** When the signed-in User lacks a permission the file needs, Import is disabled with one quiet
  line per missing permission key
  (`Creating Work Orders needs the "Create and edit Work Orders" permission.` /
  `Changing existing Work Orders needs the "Edit Work Order Demand" permission.`). Check file stays available to a holder
  of either key.
- **Permission needed after the check.** When Import is refused because the file now needs a permission that the check
  did not ask for (for example, a number created since the check now makes it a change), nothing is written, the report
  is dropped, the alert reads
  `Nothing was imported: this file now needs a permission your account does not have. Check the file again to see what it needs.`,
  and **Check file again** keeps the file.

---

# 12. Machines (Management)

Machines is a Management sub view on `/management/machines` (it follows the global theme mode, §2.1). It is the single
place for operational Machine monitoring and authorized Machine configuration: lifecycle, maintenance and asset
identification (PROJECT_PROFILE §7 Machine, §8.6, §20).

- **Access is permission-based.** A Production Manager, Process Engineer, Maintenance Manager or another authorized
  specialist manages Machines without being an Administrator, and Administration keeps no duplicate Machine screen (§9).
- **The scope stays deliberately focused.** PartFlow is not a computerized maintenance management system (CMMS): no
  spare parts, no maintenance schedules, no service contracts, no cost accounting.

## 12.1 Active Machines table

The table has one row per active Machine, with a search field (Machine, Area, asset tag, model, manufacturer) and a
**+ New Machine** action. The columns are **Machine | State | Assigned now | Asset | Maintenance**; there is no separate
Actions column.

**Sortable column headers.** Every column header is a toggle that cycles **ascending → descending → unsorted**
(unsorted means the registry order). The header carries a direction arrow (`↑`/`↓`; a faint `↕` while unsorted marks
the column as sortable), and the active sort renders emphasized in the info tone (bold and info text color). The owning
header cell (`th`) carries `aria-sort`. The sort values are:

| Column       | Sorts by |
|--------------|----------|
| Machine      | Display name |
| State        | Derived state, ordered Running → Idle → Maintenance |
| Assigned now | Total assigned quantity |
| Asset        | Asset Tag |
| Maintenance  | Override on before off |

The sort is stable: equal values keep name order, then the registry order.

**Columns.**

- **Machine.** The display name with the Area beneath it (Area color dot and name).
- **State.** The derived operational state with its elapsed time (`Running · 1h 24m`):
  1. an explicit **Maintenance** override wins;
  2. otherwise, assigned active quantity means **Running**;
  3. otherwise, the state is **Idle**.

  Users never choose Running or Idle by hand. The age derives at render from the shared `stateChangedAt` timestamp plus
  the shared minute-precision UI clock (§3 rule 12; compact formats `18m`, `1h 24m`, `2d 03h`, `<1m`; never a stored
  formatted duration), so this view, the Scan Station and the Area Board always show the same age, and the age keeps
  ticking while the view stays open (§4.10). Under maintenance, a quiet secondary line shows the note and
  `Expected back YYYY-MM-DD` when present. The column is content-sized to its widest `<state> · <age>` line, which never
  wraps; the maintenance note wraps beneath it.
- **Assigned now.** The PN portions currently assigned to the Machine with their quantities (`pcs`), one line per Part
  Number (`<PN> · <n> pcs`), or `—` while nothing is assigned. The separator and the `pcs` unit stay faint. The quantity
  carries the information tone while the Machine is Running and the error tone while it is under Maintenance: an
  additive signal that the State column repeats, never the only distinction.
- **Asset.** The asset tag, manufacturer and model as quiet metadata. The column is content-sized: **both** the
  `Asset <tag>` line and the manufacturer-and-model line never wrap, so the wider of the two defines the column's minimum
  width. Manufacturer and model render in two distinct neutral tones (muted and faint), so they read as separate values
  without becoming statuses.
- **Maintenance.** An **On/Off switch** per row (`role="switch"`, `aria-checked` mirroring the real maintenance state,
  accessible name `Maintenance — {name}`). Toggling never changes the state directly; it only **opens the existing
  dialogs**: Off → On opens Start maintenance (§12.2), and On → Off opens the Clear maintenance confirmation. The switch
  reflects reality only after the dialog confirms.

**Whole-row activation.** Clicking anywhere on a row opens the Edit Machine dialog (§12.3). The name cell's row button
(accessible name `Edit {name}`) is the keyboard and screen-reader entry, with Enter/Space activation; there is no
visible focus ring on the row button (unlike Tracking, §7). The Maintenance cell is the row's one interactive island: it
stops propagation, so operating the switch never opens the dialog.

## 12.2 Maintenance

Maintenance is an explicit override and nothing more.

- **Starting maintenance** is allowed while quantity remains assigned. The confirmation is worded in the future tense
  (`will switch to Maintenance`) and states plainly that the assigned quantity stays exactly where it is
  (`… pcs stay assigned to it — nothing is moved or released by this action.`). Starting maintenance never moves,
  releases, completes or transfers quantity and never rewrites history.
- The Start maintenance dialog collects an optional reason or note and an optional expected return date.
- **While a Machine is under maintenance**, the Edit Machine dialog (§12.3) carries an editable **Maintenance** section:
  the reason or note and the expected return date, prefilled and saved with the normal `Save changes`. It updates the
  context in place without touching the maintenance start time or the state itself. The section states that Maintenance
  is switched off from the Machines list.
- **Clearing maintenance** returns the Machine to **Running** when quantity is still assigned, and otherwise to **Idle**.
  The confirmation names the resulting state in the future tense (`will return to Running` / `will return to Idle`).

## 12.3 New Machine / Edit Machine dialog

- **Identity header first.** The dialog opens with a read-only **identity header** on a quiet panel directly under the
  dialog title: `Asset Tag` (emphasized) and `Barcode` as calm monospace values with small uppercase labels.
  - There is **no barcode input, no Asset Tag input and no explanatory sentence under the values**. The Asset Tag is
    assigned automatically when the Machine is created, as the next value in the format configured in Administration →
    Barcode configuration (PROJECT_PROFILE §8.6), and the barcode derives from it (`PF:MACHINE:<asset-tag>`, §10).
  - On an **existing** Machine the header also carries the **fixed Area** as a full-width row (a color line in the label
    row, the Area color dot and name, and the quiet help text
    `The Area cannot be changed while the Machine is active. If the physical Machine moves to another Area, retire it first and update the Area when it is reactivated.`)
    and the **`Barcode label…`** action that opens the printable label dialog (below).
- **Barcode label dialog (existing Machines).** `Barcode label…` opens `Machine barcode label`, with the description
  `Scan this label to identify {Machine}.`. It shows the display name, the Asset Tag and a real **Code 128** rendering of
  the scanned value (`PF:MACHINE:<asset-tag>`; the dependency-free encoder `src/views/code128.ts`, subset B with
  structural unit-test guards) on a **white label card with black ink in both themes**, plus the value text. On the
  label the **display name is the primary information**, big and bold, while the Asset Tag recedes to small secondary
  metadata (the barcode itself carries it for scanning). `Print Label` prints exactly the label (print styles hide
  everything else on the page); `Cancel (Esc)` returns to the Edit dialog.
- **Display name.** It is required and must not collide with an active Machine of the same Area: display names stay
  unique among the active Machines of one Area, and a duplicate is a field error with nothing saved.
  - The name column carries **live per-field feedback exactly like the Reactivate dialog** (§12.4): a collision shows
    `✕ “<name>” already exists in <Area>.` under the input; a missing name (after a save attempt) shows its error there
    too; and an entered name that differs from the saved one shows the success-toned
    `✓ “<name>” is available in <Area>.` confirmation. There is no catch-all error block at the bottom.
  - The form lays out on a **three-column grid**. On a **new** Machine the first row is Display name · Area select ·
    Installed date. On an existing Machine the Display name spans two columns and Installed date closes the row (the Area
    lives in the identity header). The Installed date column is exactly one metadata column wide, and every form control
    (text inputs, selects and the native date input, whose internal editor would otherwise render taller) shares one
    explicit control height.
- **Area (new Machine).** A native select, with the selected Area's color previewed as a **slim horizontal line filling
  the label row** (the same label-row preview as the Reactivate dialog's Area select), never a custom control just for a
  color.
- **Optional asset metadata.** **Manufacturer, model and serial number on one three-column row** (the installed date
  lives in the first form row, above), and **Notes as a full-width field** beneath it. Metadata is optional: production
  tracking never depends on it. The required **Asset Tag identifies the physical asset** even when display names are
  reused, and it is never one of these editable fields. Every text field carries a realistic `e.g. …` example
  placeholder (the native date input shows the browser's own format hint instead).
- **Adding is staged (new Machine).** The form's primary action is `Continue`: it validates and leads to a
  **`Confirm new Machine` summary** that states that nothing has been added yet and recaps the Machine, Area, Asset Tag,
  barcode and whichever optional metadata was entered. `Back` returns to the form with its input intact.
  - All Machines confirmation summaries (add, retire, reactivate) share the **Scan Station confirmation-summary idiom**
    (§4.6): a two-column definition list on a quiet panel with a content-sized label column. The Machine name is the
    primary row (bigger, bolder); context rows (barcode, metadata, `Returns as`) recede as secondary; semantic tones stay
    additive (success for the confirmed physical identity, warning for an Area move or a recorded unsaved-edits decision,
    and the `Returns as` value `Idle` keeps the warning text tone it carries on every monitoring surface); and the
    barcode renders as quiet monospace verification text in the muted tone, never a badge.
  - Rendered barcode values (`PF:…`) share this ONE muted monospace reading tone on every surface (the confirmation
    summaries, the identity headers of §12.3 and §12.4, the retirement consequences list, and the Administration
    barcode-configuration preview of §9) through one shared style: a barcode is identification data, never emphasis.
  - The summary's `Add Machine` action asks one final explicit question (`Add this Machine?`: a Machine record cannot be
    deleted once added; it can only be retired later) in the **attention confirmation variant** (§12.4 Retire flow;
    warning tone, because it is permanent but constructive). Only its confirmation really adds the Machine.
- **Unsaved edits** are visibly marked (`● Unsaved changes` **beside the dialog title**, sharing the heading row), and
  the cancel action is exactly `Cancel (Esc)` (§3 rule 10). **Closing with unsaved input asks first**, so nothing is saved
  or lost silently:
  - on an existing Machine, the shared `UnsavedChoiceDialog` offers `Save changes` (saves and closes; disabled with a
    stated reason while the form is invalid) / `Discard changes` / `Cancel (Esc)`;
  - on a new Machine, a `Discard new Machine?` confirmation offers `Keep editing` / `Discard input` (nothing has been
    added yet, so there is nothing to save).
- **Initial focus.** The **New Machine dialog opens with initial focus on the Display name** field, the one required
  entry. Edit Machine focuses no field (focus on the dialog root, because the whole record is equally editable).
- **Lifecycle timeline (Machine History).** The dialog shows the Machine's append-only lifecycle events under the
  heading `Machine History`.
  - Each event shows its label (`Retired` or `Reactivated`; the recorded audit values stay `RETIRED` / `REACTIVATED`),
    date, actor (the user who recorded it, shown as avatar and name; events recorded before sign-in existed show what
    was recorded), reason, and the previous → current Area when the Machine moved. With no events the timeline shows
    `No Machine history recorded.`
  - It uses the ONE shared vertical-timeline presentation: tone-ringed markers on a hairline rail, the error tone for a
    retirement and the success tone for a reactivation. The event label always renders, so color is never the only
    distinction. The timeline is shared by Edit Machine, the Reactivate dialog and the Retired Machine Details dialog
    (§12.4).
  - Edit Machine uses the timeline's **compact variant**: the same rail and markers with each event on ONE line (event
    label, date, actor, reason, Area move inline), so the audit stays present without claiming form height. The
    Reactivate and details dialogs keep the full stacked presentation.
  - The timeline is read-only and never editable.
- **Danger Zone (existing Machines only).** The dialog ends with a visually separated Danger Zone containing the
  `Retire…` action; retirement is not a list-row action (§12.4). The zone states the current truth **in place**:
  - while retirement is possible, the consequences line
    (`Retiring <name> removes it from every assignment choice and stops its barcode from accepting assignment scans. History is preserved — the Machine is never deleted.`)
    renders in a soft error reading tone;
  - while quantity is still assigned, the message becomes the plain blocked explanation in the neutral muted tone
    (`<name> cannot be retired while <qty> pcs are still assigned. Complete or transfer that quantity through the normal production workflow first.`),
    and **`Retire…` is disabled**.

  The zone's error styling is the same in both states.
- **Replacement guidance.** The New Machine dialog carries the replacement guidance: retire the old Machine record and
  create a new one. The new Machine gets its own new Asset Tag and barcode and may reuse the familiar display name;
  history always keeps the Machine that really did the work.

## 12.4 Retirement, reactivation and replacement

### Retirement is blocked while active quantity is assigned

The Danger Zone communicates this **in place** (§12.3: the blocked message and a disabled `Retire…`). The
`Cannot retire Machine` dialog (with `Close` as the only action) remains as the fallback for a stale view or a race, and
the real gate stays in the production workflow, never in the presentation layer only.

### Retire flow

Retirement starts from the Edit dialog's Danger Zone (§12.3) and uses a two-step confirmation.

1. **Unsaved edits decision.** Starting Retire with unsaved edits first requires an explicit three-way decision through
   the shared `UnsavedChoiceDialog`: `Save changes` / `Discard changes` / `Cancel (Esc)` (the save choice is disabled,
   with a stated reason, while the form is invalid). Edits are never saved silently. The choice is a **recorded decision,
   not an immediate action**: nothing is saved or discarded until the retirement completes, so cancelling any later step
   returns to the Edit dialog with the edits still in the form.
2. **Typed confirmation.** The shared `TypedConfirmDialog` (`Retire Machine`) follows.
   - It is **danger-toned across the whole dialog** (error border, faint error surface wash, error-toned eyebrow title;
     the `Confirm retirement` summary below shares the same treatment), so the destructive context never reads as
     routine.
   - It opens with **initial focus on the typed-confirmation input**, because the gate is the dialog's task.
   - It requires **typing the Machine's Asset Tag** (always present, never the reusable display name), matched after
     trimming and case-insensitively. Its confirming action stays disabled until the typed value matches.
   - The typed field follows an explicit consequences list: the Machine disappears from assignment choices; its barcode
     (`PF:MACHINE:<asset-tag>`) no longer accepts assignment scans; all history is preserved and nothing is deleted; the
     record moves to Retired Machines, and only the same physical machine can later be reactivated.
   - Its confirming action is `Continue`.
3. **Confirm retirement summary.** A final **`Confirm retirement` summary** states that nothing has changed yet and
   recaps the Machine (as it will be retired, with the edits when the recorded decision is Save), Area, Asset Tag,
   derived barcode and the recorded edits decision, with `Cancel (Esc)` / `Retire Machine`.
4. **Final question.** The summary's `Retire Machine` action asks one last explicit question (`Retire this Machine?`:
   the Machine will be retired; this retirement is permanently recorded in Machine history, and if the same Machine is
   reactivated later the retirement remains recorded) in the **attention confirmation variant**. Only its confirmation
   retires the Machine: it applies the recorded edits decision, appends a `RETIRED` lifecycle audit event (§12.3) and
   sets the retirement date.

A Machine ever referenced by history is **never hard-deleted**. The operator wording is `Retired`, `Retire Machine` and
`Retired on …`.

**The attention confirmation variant.** It is one shared family: a centered icon badge with a tone halo, an emphasized
sentence-style title, a tone-washed surface, and the description itself in a soft wash of the same tone, with the
Machine name and the permanence statement in the full text tone.

- The danger variant is light red, and the warning variant (used for the warning final questions) is light amber.
- The information variant (`tone-info`) is blue and is used for informational final gates (§4.6). It belongs to the same
  global attention family as danger and warning (defined in `styles/global.css`, available to every view, never a style
  local to the Scan Station), and its icon badge shows `i`.
- The copy of every final question leads with what happens and states explicitly that the change is permanently
  recorded in Machine history, so the variant interrupts reflexive confirmation instead of reading as a routine notice.

### Retired Machines table

- **Columns:** **Machine | Retired | Asset | Notes**: name and Area, `Retired on YYYY-MM-DD`, asset metadata, notes. It is
  historical display and reporting with **no action column**.
- **Sorting.** All four columns carry the same sortable headers as the active table (§12.1: Machine = name, Retired =
  retirement date, Asset = Asset Tag, Notes = notes text), sorting independently of the active table's sort state.
- **Whole-row activation opens the read-only Retired Machine Details dialog**, in the same pattern as the active rows:
  the name-cell button (accessible name `Machine details — {name}`) is the keyboard and screen-reader entry. The details
  dialog carries the only `Reactivate` entry.
- **Page note.** The explanatory text beneath the table reads
  `Retired Machines remain in history and cannot receive new work or assignments. Reactivate only the same physical Machine. Replacements may reuse the display name but require a new Machine record and Asset Tag.`
  It renders through the **shared PageNote presentation** (info-weak surface, info border, info text tone, a small
  `NOTE` tag leading one inline content flow, full parent width): the ONE page-level note used by every view, including
  Machines, Work Orders (the list note and the demand-line removal note in the details dialog), Priority, and the
  Planned Routes Archived Routes explanation (§13.1).
- Retired Machines never appear in assignment choices and accept no new scans.

### Retired Machine Details dialog

A read-only presentation of one retired record:

- an identity row (display name, Area dot and name, and the `Retired on …` badge stepped up to the **error text tone on
  the error-weak surface**, matching the retirement timeline marker, so the record's key status cannot be missed);
- the shared identity panel (Asset Tag and derived `PF:MACHINE:` barcode, §12.3);
- summary rows for the asset metadata (manufacturer, model, serial number, installed date, retirement date; absent values
  as `—`);
- the notes;
- the shared **lifecycle timeline** (§12.3).

Nothing is editable. The actions are `Close (Esc)` and `Reactivate`, which opens the staged Reactivate workflow (below).
Entered this way, the Reactivate dialog's leave-the-workflow action is **`‹ Back`** (Escape included), which returns to
the details dialog instead of closing everything.

### Reactivate Machine dialog

This is the return to service of PROJECT_PROFILE §8.6.

- **Identity header.** The dialog leads with the **read-only identity header** (§12.3: Asset Tag and derived barcode,
  untouched by reactivation), extended by a third **`Machine`** identity column on the same row (manufacturer and model
  in their two neutral tones, `S/N <serial>`; `—` when none).
- **Hard blockers.** If the Asset Tag (which is also the barcode) or the serial number has meanwhile been reissued to
  another active Machine, an explicit alert renders and Confirm is disabled.
- **Display name and Return Area.** The display name is editable in the dialog and **shares one form row with the Area
  select** (`Return Area`: active non-terminal Areas, with the shared label-row color-line preview of §12.3). Each column
  carries its own feedback directly under its input.
  - Under Display name: a name collision with an active Machine of the target Area shows the inline error
    `✕ “<name>” already exists in <Area>.`, scoped to the name column, and renaming resolves it (display names must be
    unique among the **active** Machines of one Area; reuse across time stays allowed). A valid non-empty name shows the
    success-toned availability confirmation (`✓ “<name>” is available in <Area>.`); the ✓/✕ markers keep the signal
    from relying on color alone.
  - Under Return Area sits the quiet note
    `Change only if the physical Machine moved while retired. Previous production history keeps the original Area.`
- **Validation at the field.** **Every validation error renders at its failing field** (a missing name in the name
  column, a missing reason under the Reason field, the unchecked acknowledgement inside the Important panel), never as
  one catch-all block at the bottom and never doubled. Each clears as soon as its field is fixed.
- **Initial focus** is on Display name.
- **Reason and acknowledgement.** A reason is required, as is the explicit confirmation checkbox
  `This is the same physical machine returning to service — not a replacement.` (checkbox and text clearly separated
  and vertically centered against each other). The checkbox sits on a calm **warning `Important (required)` panel**
  (warning-weak surface and warning border, with the `⚠ Important` marker in the warning text tone): a
  be-sure-before-continuing cue, deliberately NOT error or danger toned, and the sentence itself keeps the normal text
  tone. Only continuing without the check raises the normal red validation error.
- **Confirm reactivation summary.** The form's primary action is `Continue`: it validates and leads to a final
  **`Confirm reactivation` summary** that states that nothing has changed yet and recaps the Machine name, Area (old →
  new when moved), Asset Tag, derived barcode, the read-only `Physical identity — Same physical machine confirmed`
  acknowledgement, reason and the `Idle` return. Its note reads
  `The same Machine returns to Active with its existing Asset Tag and barcode. Previous production history is preserved.`,
  followed by `The Area change applies from reactivation onward.` when the Area is changing. The actions are `Back`
  (returning to the form with its state intact) / `Reactivate Machine`.
- **Final question.** The summary's `Reactivate Machine` action asks one last explicit question
  (`Reactivate this Machine?`: the Machine will return to service; this reactivation is permanently recorded in Machine
  history, and the previous retirement remains part of that history) in the **attention confirmation variant** (warning
  tone here, because it is permanent but constructive). Only its confirmation reactivates: it clears the retirement date,
  clears any maintenance override, resets the state-change timestamp, and appends a `REACTIVATED` lifecycle audit event.
  The Machine returns as **Idle** (Running stays derived from assigned quantity, §12.1), and the audit records who, when,
  the reason, and the previous and current Area when moved.

### Replacement of a physical Machine

Retire the old record and create a **new** record with its own stable identity and its own new, automatically assigned
Asset Tag (and barcode). The new Machine may reuse the operator-facing display name (the familiar floor position, for
example `Lathe 1`). The old record is never renamed or mutated, and old and new stay distinguishable through the Asset
Tag and the internal identity. No MachineSlot or WorkCenter abstraction is introduced.

## 12.5 States and implementation boundary

Loading, error and empty states follow the standard view-state presentation (`No Machines configured yet.` / a no-match
message), with a real loading, error and Retry cycle against the API.

The view is persisted Management UI backed by `/api/machines`.

- Listing, creation with the Asset Tag assigned by the server, the one Save-changes edit transaction, the maintenance
  override, and retirement and reactivation (each committing atomically with its append-only lifecycle event) all read
  and write the live Machine registry.
- At creation, the previewed tag travels as an optimistic precondition: a stale preview is rejected with nothing
  consumed.
- The server reports each Machine's **assigned ACTIVE quantity**, derived from the production projection. The state
  derives **Running** from it (§12.1); the Assigned now column lists the server's per-PN portions from the same
  projection that the total sums, so the breakdown and the total can never disagree; and the assigned-quantity
  retirement blocker is enforced by the server and reflected in Edit Machine.
- The state derivation is shared, framework-independent presentation logic (`src/views/machine-state.ts`), reused by
  the Scan Station and Area Board monitoring cards.

---

# 13. Planned Routes (Management)

Planned Routes is a Management sub view on `/management/planned-routes` (it follows the global theme mode, §2.1). It
holds the reusable route definitions (internal domain name `RouteTemplate`, PROJECT_PROFILE §8.8) for authorized
production roles, with access based on permissions like Machines (§12; PROJECT_PROFILE §20). The user-facing name
**Planned Routes** keeps the definitions clearly apart from the **Floating actual route traces** shown in Tracking
(§7.2). Planned Routes guide production; they never replace or rewrite the actual immutable Movement history.

## 13.1 Route template list

A searchable list (route name, Operation) with a **+ New Planned Route** action, **split into two tables like
Machines**:

- an **Active** table with the columns **Planned Route | Steps | Status | Used by** (no Actions column);
- a separate **Archived Routes** section with the columns **Planned Route | Steps | Archived | Used by | Duplicate**.

**Columns.**

- **Planned Route.** The name, with the optional description beneath it.
- **Steps.** The ordered step sequence as **Area-colored chips**. Each chip carries a tinted surface (`color-mix` of the
  Area color into the panel), an Area-colored border, the Area color dot, and the Area name in the normal text color;
  the arrows between chips are separate sibling elements. Without `color-mix` support the chip falls back to the
  neutral chip surface; the dot and border still carry the Area identity, so color-mix is never load-bearing.
- **Status.** `Active` with its updated date (active table). The archived table shows the archived date, rendered
  visually quiet.
- **Used by.** `N Quantity Flows…`, which opens the usage dialog, or `Never used`.

**Whole-row activation.** Clicking anywhere on an active-table row opens the Edit dialog (§13.2). The name cell's row
button (accessible name `Edit {name}`) is the keyboard and screen-reader entry, with no visible focus ring on the row
button (unlike Tracking, §7). The usage cell stops propagation, so opening the usage dialog never opens the editor.
`Duplicate`, `Archive…` and `Delete…` live **inside the Edit dialog** (§13.2, §13.3), not on list rows. **Archived rows
are not clickable and offer only their per-row `Duplicate` button.** For a user who may not manage Planned Routes, the
active rows are not activatable, `+ New Planned Route` and the archived rows' `Duplicate` are absent, and `Used by`
stays.

**Archived Routes note.** Beneath the Archived Routes table, an informational note explains the archived routes. It
uses the one shared page-note component (the PageNote presentation, §12.4).

**Usage dialog.** It lists the Quantity Flows released with the template (flow id, PN, released date) and states that
each keeps its own route snapshot from release time.

## 13.2 Create / Edit dialog

- **Fields.** Route name (required), an optional description, and the **ordered steps** under a labelled column header
  row (Area / Operation / Est. time / Preferred Machine). Each step row is presented like a Priority item: drag grip,
  step index, the step fields, ↑/↓/✕ controls, and a labelled Instructions line beneath.
- **Reordering.** Steps reorder with drag-and-drop, like the Priority list, plus explicit up and down controls: **Move
  Up / Move Down remain the keyboard and touch path, so reordering is never drag-only**. Remove buttons stay per step,
  and an **+ Add step** action appends a step. At least one step is required.
- **Operation is a select scoped to the chosen Area's Operations.** A stored Operation that the Area no longer offers
  renders as an explicit `{operation} (unavailable)` value: kept visible and **never silently cleared**.
- **Preferred Machine is a select over the chosen Area's ACTIVE Machines, referenced by stable Machine id** (matching
  `preferred_machine_id`; display names are reusable and cannot identify a preference), with `— no preferred Machine` as
  the default. A referenced Machine that is retired or missing renders as an explicit `(unavailable)` value
  (`{name} — retired (unavailable)` / unknown id): kept visible and never silently cleared.
- **Changing a step's Area resets and revalidates its Operation and Preferred Machine.** The Operation is kept only when
  the new Area still offers it, and the Preferred Machine only when it belongs to the new Area.
- **Sizing.** The step comboboxes size to their content (`max-content`, capped) and go full-width at the phone
  breakpoint. The duration and instruction fields, which have only a bottom border, keep their labels and a visible
  focus state.
- **Unsaved edits** are visibly marked (`● Unsaved changes`), the cancel action is exactly `Cancel (Esc)`, and closing a
  dirty dialog asks `Discard unsaved route changes?` before discarding.
- **Shared step rows.** The step rows, their validation and their numbering are one component shared with Tracking's
  `Edit assigned Route…` (§7.2 item 8), which also uses this section's `● Unsaved changes` marker and
  `Discard unsaved route changes?` guard.
- **Editing a used template shows an explicit note:** changes apply to **future assignments only**. The Quantity Flows
  already released with this route keep their Assigned Route snapshots unchanged, and an Assigned Route in production is
  changed separately in its own audited workflow with a reason (Tracking → `Edit assigned Route…`, §7.2 Corrections),
  never here.
- **In-dialog actions.** `Duplicate`, plus `Archive…` (ever-used) or a destructive-toned `Delete…` (never-used only),
  live in the dialog. Starting Duplicate with unsaved edits requires an explicit choice (`Save, then duplicate` /
  `Duplicate the saved route` / `Cancel (Esc)`, through the shared `UnsavedChoiceDialog`). `Duplicate` copies the
  template (including an archived one, from its table) into a new, never-used, active `{name} (variant)` draft and opens
  it for editing.

## 13.3 Archive versus delete

- **Delete exists only for never-referenced templates.** The confirmation states that the route has never been used by a
  released Quantity Flow, so it can be removed completely. It stays a plain destructive confirmation dialog.
- **An ever-used template is archived instead**, guarded by a typed confirmation.
  1. Starting Archive with unsaved edits first requires an explicit `Save changes, then archive` / `Discard changes` /
     `Cancel (Esc)` decision (shared `UnsavedChoiceDialog`).
  2. Then the shared `TypedConfirmDialog` (`Archive Planned Route`) requires **typing the exact route name** (matched
     after trimming and case-insensitively) after the four-part consequences warning: the route is no longer offered for
     future assignments; the Quantity Flows released with it keep their Assigned Route snapshots; the actual Movement
     history is unchanged; and the route stays visible for historical context.
- Archived templates are never offered for new route assignments.
- There is **no separate template-versioning system**: existing Assigned Route snapshots preserve the historical route
  definitions (PROJECT_PROFILE §8.8, §8.10).

## 13.4 States and implementation boundary

Loading, error and empty states follow the standard view-state presentation (`No Planned Routes defined yet.` / a
no-match message).

The view is production UI on the `/api/route-templates` surface: create, edit (the full step set), Duplicate (a server
create of `{name} (variant)`), Archive (ever-used) and Delete (never-used), and the usage dialog (the newest 200
released Quantity Flows, with the total). Writes are blocked while disconnected. Est. time is entered and shown in the
shared duration tokens (`45m`, `4h 00m`, `2d 03h`). A stored Operation or Machine that is no longer offered renders
`(unavailable)` and must be replaced before saving (§13.2). The development-only `?state=` previews are available.

---

# 14. Part Numbers (Management)

Part Numbers is a Management sub view on `/management/part-numbers` (it follows the global theme mode, §2.1). It is the
single place for **PartNumber master metadata** (PROJECT_PROFILE §8.1, §21 *Part Numbers*): production master data,
managed according to permissions by authorized production roles, like Machines and Planned Routes (§12, §13;
PROJECT_PROFILE §20).

- The canonical PN string itself stays the stable production identity. Records here are **optional current metadata
  only** (image, name/description, informational revision, ERP mapping) and never gate production use: a canonical PN is
  always usable, with or without a master record.
- In the Management sub-view bar the view sits **next to last, directly before Machines** (§1.1).
- The view follows the Machines presentation family (§12): the same page header, toolbar (search and primary action),
  table, dialog and Danger Zone idioms.

**Copy discipline.** All normal user-facing copy on this view speaks plain production language ("Part Number details",
"saved details", "production history"), never domain or implementation vocabulary (`canonical`, `identity`,
`metadata record`, `master record`, `Movement history`), which stays in this specification and in the code. The page
description is exactly `Manage optional Part Number details, images, ERP IDs, and barcode labels.`

## 14.1 Part Number list

A searchable list (PN, name/description, revision, ERP id) with a **+ New Part Number** action, sharing the Machines
toolbar layout (§12.1). The columns are **Image | Part Number | Name / Description | Revision | ERP ID | Barcode**.

- **Image.** The record's uploaded PN image, or the **ONE shared default PN image placeholder**, exactly the placeholder
  that the Tracking Part Details panel uses (§7.2). No second default image exists anywhere.
- **Part Number.** The canonical **UPPERCASE** PN in the standard monospace presentation.
- **Name / Description.** The master name or description; absent values render `—` (§3 rule 9).
- **Revision.** The informational current revision, `—` when absent.
- **ERP ID.** The ERP mapping id, `—` when absent.
- **Barcode.** The derived value `PF:PN:<part-number>` in the ONE shared muted monospace barcode reading tone (§12.3): a
  derived identification value, never an independently editable field, and never a badge.
- **Whole-row activation.** Clicking anywhere on a row opens the Edit Part Number dialog (§14.2). The PN cell's row
  button (accessible name `Edit {pn}`) is the keyboard and screen-reader entry, in the same whole-row pattern as
  Machines, Planned Routes and Tracking rows, but with no visible focus ring on the row button (unlike Tracking, §7).

There is **no page-level note beneath the table**: the deletion semantics are explained where the action lives, in the
Edit dialog's delete section and its confirmation (§14.2), and are never repeated at page level.

## 14.2 New / Edit Part Number dialog

**Read-only for users who may not manage Part Numbers.** Such a user opens the same dialog read-only, from the Part
Numbers list and from the PN control of Work Order demand lines (§11.2). It is titled `Part Number details`, with the
values as text (`—` when absent), the image without its controls, `Barcode label…` kept, no `Save`, no
`Delete Part Number Details` section, and `Close (Esc)`.

- **Edit leads with the read-only identity header** (the §12.3 idiom): `Part Number` (emphasized) and the derived
  `Barcode` (`PF:PN:<part-number>`) as calm values on a quiet panel. There is no PN input and no barcode input on an
  existing record (the PN is the identity and is never edited; the barcode always derives from it). The header also
  holds the **`Barcode label…`** entry, which opens the printable label dialog (§14.3).
- **New Part Number** collects the PN as its one required field. Input is canonicalized on entry exactly like every
  other PN entry path (§4.4, §11.2: trimmed; internal whitespace rejected with an inline explanation and never silently
  removed; lowercase canonicalized to uppercase). The field feedback copy is:
  - internal whitespace → `Part Number cannot contain spaces or other whitespace.`;
  - an existing record → `Part Number "{PN}" already has saved details.` (one master record per canonical PN);
  - a valid entry → the success-toned live line `✓ Will be saved as {PN} · Barcode {barcode}`.

  Creating a record here is optional metadata entry ahead of time; creation on first use at intake (§4.7, §11.2) still
  applies.
- **Metadata fields.** Name / Description, Revision (informational) and ERP ID, all optional, laid out on the Machines
  form grid with `e.g. …` placeholders.
- **Image.** The current image (or the shared default placeholder) with `Upload image…` / `Change image…` and, when a
  custom image exists, `Remove image`; removing returns to the shared default placeholder. The quiet help line reads
  `If no image is uploaded, the default Part Number image is shown.` The image is master metadata only.
- **Unsaved edits** are visibly marked (`● Unsaved changes` beside the dialog title), the cancel action is exactly
  `Cancel (Esc)` (§3 rule 10), and closing with unsaved input asks first: the shared `UnsavedChoiceDialog` on an
  existing record (`Save changes` / `Discard changes` / `Cancel (Esc)`, body `You have unsaved changes to {PN}.`), and a
  `Discard new Part Number?` confirmation on a new one (body `Your entered information will not be saved.`). The New
  dialog opens with initial focus on the PN field.
- **Delete section (existing records).** The dialog ends with the user-facing **`Delete Part Number Details`** section in
  the §12.3 Danger Zone presentation: the description
  `This removes the saved image, description, revision, and ERP ID for {PN}. Production tracking and Work Order history are not affected.`
  and the `Delete details…` action.
  - Deletion is a **plain destructive confirmation in the attention confirmation variant** (§12.4; danger tone: centered
    icon badge, emphasized danger-toned title, the description in the soft danger wash with the PN in the full text tone,
    never the whole message in red, and the danger-toned confirming action).
  - It is the **strongest warning of the flow** (the Edit dialog's delete section above keeps its lighter danger-zone
    treatment) while staying ONE plain confirmation: **no typed confirmation and no additional step**, because the
    deletion is recoverable by re-creating the record.
  - It is titled `Delete Part Number details?`, with the confirming action `Delete details` and the body
    `This permanently removes the saved image, description, revision, and ERP ID for {PN}. The Part Number and its production history remain available.`
  - Only the master metadata record is deleted (PROJECT_PROFILE §8.1). Work Order demand, production quantities,
    Movement history and allocations are never touched or cascaded into. Every screen keeps displaying the canonical PN
    with the master-derived fields absent (`—`, §7.1), and a record can be created again later for the same PN.
  - There is **no PN archive, no soft delete and no active/inactive lifecycle** (PROJECT_PROFILE §8.1).

## 14.3 Part Number barcode label

`Barcode label…` opens **`Part Number barcode label`**, with the description
`Scan this label to identify Part Number {PN}.` It shows a real **Code 128** rendering of the scanned value
`PF:PN:<part-number>` (the shared dependency-free encoder `src/views/code128.ts`, §12.3) on the **white label card with
black ink in both themes**, with the **PN text beneath the barcode** as the label's primary text and the full scanned
value as the quiet monospace verification line. `Print Label` prints exactly the label (print styles hide everything
else); `Cancel (Esc)` returns to the dialog.

The dialog is deliberately simple: no barcode configuration exists here (the `PF:` namespace scheme stays in
Administration → Barcode configuration, §9). This dialog is the **ONE PN label surface** in the application:
Management → Part Numbers (§14.2) and the demand-line `Edit Part Number` dialog (§11.2) reach it through
`Barcode label…`, and the Add Part flow (§11.2, §11.3) opens it directly. All of them render the bars through the one
shared Code 128 component; no second PN label card and no second encoder exist anywhere.

## 14.4 States and implementation boundary

Loading, error and empty states follow the standard view-state presentation (`No Part Number details have been added yet.`
/ the no-match message `No saved Part Number details match "{search}".`).

The view is production UI on the `/api/part-numbers` surface: a bounded search on the server
(`Showing N of T Part Numbers`, `Show more`, at most 200 rows), create, edit, image upload and removal, and hard delete,
with writes blocked while disconnected. The development-only `?state=` previews are available. The bounded-list line is
a list-state indicator (the PN Tracking pattern of §7.1), not the page-level note that §14.1 excludes.

---

# 15. Version history

The version history of this document is the change table at the end of the document (Change history).

---

# 16. Out of Scope for Phase 2 UI

The approved UI deliberately leaves out the following: a localization framework (the UI ships in English using the
PROJECT_PROFILE vocabulary), charts and analytics dashboards, and administrative command barcodes.

---

# 17. Open Questions

There are no open questions.

---

# Change history

| Date | Commit | Change |
|---|---|---|
| 2026-11-09 | uncommitted | Aligned with the code: the Undo and Retry action zones are transparent at rest with an inset divider rule, and an `ON_MACHINE` transfer announces `AREA_COMPLETED, then TRANSFERRED (one command)`. |
| 2026-11-09 | uncommitted | Rewritten as current content for new readers; requirements that only the old version notes (former §15.1–§15.17) held were moved into the body; no rule changed. |
| 2026-10-06 to 2026-10-08 | 702ab1c … 7b24d10 | Added User sign-in with permission-aware Management and Administration, Scan Station device enrollment, allocation adjustment, assigned-route editing, the PN audit trail, the per-User theme, Work Order file import and the release-update notice. |
| 2026-10-04 to 2026-10-06 | 9f466fa … 69b8ec1 | Specified Workers, Worker ID modes, badge sessions and gates, the Undo reason, Part Numbers, Planned Routes, display and Due Soon settings, retention, Users and roles; fully allocated Hot entries leave the list automatically. |
| 2026-08-25 to 2026-09-13 | d7bcd06 … f016d41 | Put the Scan Station workflows, Production Board, Area Board, PN Tracking and Completed Work Orders on server data, with feed status, paging, Receive Quantity beside existing quantity and expected-duration warnings. |
| 2026-08-13 to 2026-08-24 | c4509f9 … 1fe73e9 | Added the Part Numbers page, phone-width layouts, final confirmation steps for DONE, QUEUE and Undo, Worker badge sessions, one shared PN label, restricted edits of released demand and a server-bounded Work Order list. |
| 2026-08-12 | fa4b51d, c4f18a6 | GUI Design v18: the canonical uppercase PN without internal whitespace became the identity and the Part Number record optional and deletable; preserved casing, PN archiving and the inactive PN state were removed. |
| 2026-08-05 to 2026-08-12 | 5a18ce6 … cb8c457 | GUI Design v17: DONE row action for Areas without Machines, Machines dialogs with automatic Asset Tags and staged confirmations, PN Tracking name, and completed Work Orders on their own page. |
| 2026-08-05 | b59ef51 … 53481a8 | GUI Design v16: one shared clock for elapsed times and countdowns, focus kept inside dialogs a scan opens, intake renamed Receive Quantity, and a shorter offline banner. |
| 2026-08-05 | 01a7904 | GUI Design v15: Machine reactivation, typed and three-way confirmation dialogs, row-click editing in Machines and Planned Routes, rotation timed by rows per page, and four guidance kinds. |
| 2026-08-04 | 74e0386, cca711b | GUI Design v14: Machines and Planned Routes views, Machine state age on monitoring cards, a floating Tracking detail panel, and content-measured shared columns on the board and in Priority. |
| 2026-08-04 | 1a043cc | GUI Design v13: Production Board kiosk mode and rotation indicator, full-card Station Selector cards, one shared Scan Station footer, touch-aware quantity input and responsive Work Order dialogs. |
| 2026-08-03 | 1c6e73e | GUI Design v12: cursor-aware quantity editing, no false scrollbars, and refined dialog hierarchy, confirmation emphasis, board clock, columns, footer and Priority rank transitions. |
| 2026-08-03 | 4271796 | GUI Design v11: Scan Station production mode, manual board paging, the Hot flame in the No. column, the two-snapshot Priority confirmation and an automated guard on rendered copy. |
| 2026-07-31 | 8dd337b | GUI Design v10: Area-level DONE completion with its finished group, restructured Scan Station header totals, and Work Order Details as a modal dialog. |
| 2026-07-30 | 79ada7d | GUI Design v9: every Scan Station action became a multi-step wizard with its own confirmation view, with floating scan notifications and the grid PN row layout. |
| 2026-07-28 | fbb38cb | GUI Design v8: Scan Station rebuilt around one-shot PN actions without Machine sessions, with Floating routes, blank Work Order Numbers, Repair intent, scrap and quantity additions. |
| 2026-07-26 | 74edbdd | GUI Design v7: fast connectivity detection, optional due dates, manual-first Work Order entry, a live board clock with measured paging, and confirmation before Priority reordering. |
| 2026-07-24 | 766ed7a, d5d5a1c, 027567e | GUI Design v6: Purchase Orders renamed Work Orders, with New Work Order as a modal dialog with line adding, removal rules, native date inputs and unsaved-change guards. |
| 2026-07-22 | f47da8e | GUI Design v5: global Dark/Light theme toggle and ellipsis truncation of long PNs on Area Board cards. |
| 2026-07-22 | bf2183d | GUI Design v4: Manager Summary merged into the Area Board as the All Areas overview, and PO Intake restructured into Purchase Orders. |
| 2026-07-22 | 8f642e4, 2a53463 | GUI Design v3: Area Board, Manager Summary, Tracking, PO Intake and Priority grouped as sub views of one Management view. |
| 2026-07-22 | b45a81f, 7bd5180 | GUI Design v2: Machine sessions replaced the per-Part Machine scan, Hot demand ranked per Department with Undo and Redo, and Manager Summary and Priority Management specified. |
| 2026-07-20 | c2aeda5 | GUI Design v1: initial GUI design. |
