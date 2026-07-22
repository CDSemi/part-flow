# Slice 1 — Minimum Domain and Logical Data Model

> **Scope:** Phase 4 vertical slice only — *transfer a tracked production quantity into an Area queue through the Scan Station workflow*.
> **Status:** Analysis and design. No migrations, no application code.
> **Basis:** `docs/PROJECT_PROFILE.md` (v5, canonical), `docs/IMPLEMENTATION_ROADMAP.md`, `docs/GUI_DESIGN.md` §4, `CLAUDE.md`.

---

## 0. Slice Definition and Explicit Scope Decisions

The slice implements exactly the Phase 4 workflow: resolve station context → scan a PN barcode → validate → record one immutable Movement → derive current state → refresh the Scan Station.

Three scope decisions shape the minimum model. Each is justified in §12 where it touches a documentation gap:

- **D1 — Whole-flow transfers only.** A scan moves the *entire* quantity of one Quantity Flow from its current Area into the station's Area queue. Partial-quantity transfer requires `SPLIT`, which is deferred. Quantity entry in the UI is therefore a *confirmation* step in this slice, not a free split amount.
- **D2 — Ambiguous scans are rejected, not resolved.** The roadmap's Phase 4 completion criterion is "unknown and ambiguous scans are rejected." The GUI ambiguity dialog (§4.6) arrives with later slices. A scan proceeds only when it resolves to exactly one valid source flow.
- **D3 — Quantity enters the system via seeded `RECEIVED` events, not an operator workflow.** PO intake is slice 7. To make the slice testable end to end while preserving quantity conservation ("every quantity originates from a Movement"), development/test fixtures create flows together with `RECEIVED` Movements. There is no UI for this in the slice.

---

## 1. Minimum Domain Entities

Five entities. Nothing else is required to execute, validate, record, and display the workflow.

| # | Entity | Kind |
|---|--------|------|
| 1 | `PartNumber` | Master data (mutable display, stable identity) |
| 2 | `Area` | Master data (mutable display, stable identity) |
| 3 | `ScanStation` | Configuration (binds a physical station to one Area) |
| 4 | `QuantityFlow` | Tracking identity + derived-position projection |
| 5 | `PartMovement` | Immutable event record — the core of the system |

---

## 2. Entity Responsibilities

**PartNumber** — the reusable PN master record and the *only* barcode-resolvable entity in this slice. Owns: unique PN string (arbitrary format), unique PartFlow barcode value, display name, active flag. Does **not** own quantity, location, PO context, or routes.

**Area** — a stable physical location identity. Owns: name, display color, active flag. In this slice it plays two roles: the *destination* (the station's bound Area) and the *source* (where the flow currently sits). Display properties may change without affecting history; only the row `id` is referenced by Movements.

**ScanStation** — resolves "which Area is this scan for?" on the server. Owns: station code, Area binding, active flag. Exists so the Area context of a write is server-owned configuration, never a client claim. (The profile's §14 binds stations to Areas and puts `station_id` on Movement but defines no entity — see §12.6.)

**QuantityFlow** — the traceable identity of a portion of PN quantity. Owns: PN reference, its physical quantity (immutable within this slice — it can only change via future `SPLIT`/`MERGED`/`QUANTITY_ADJUSTED` events), lifecycle status, and the *derived* current Area. It is the unit that moves, the lock target for concurrency, and the anchor that lets split/merge/routing attach later without touching Movement history.

**PartMovement** — one immutable event per recorded production action. Owns: what moved (flow + PN + quantity), the movement type, from/to Areas, which station recorded it, when, and the idempotency key. It is the audit record and the sole source from which current state is derived. Append-only; never updated, never deleted.

---

## 3. Relationships

```
PartNumber 1 ──── * QuantityFlow          (a PN may have many flows, in many Areas)
PartNumber 1 ──── * PartMovement          (denormalized; must agree with the flow's PN)
Area       1 ──── * ScanStation           (a station is bound to exactly one Area)
Area       1 ──── * QuantityFlow          (via derived current_area_id)
Area       1 ──── * PartMovement (from)   (nullable — NULL for RECEIVED)
Area       1 ──── * PartMovement (to)     (required)
QuantityFlow 1 ── * PartMovement          (a flow's movements form one linear chain)
ScanStation 1 ─── * PartMovement          (nullable — NULL for seeded RECEIVED)
```

Consistency between the denormalized `part_movements.part_number_id` and the flow's PN is enforced structurally: a composite foreign key `(quantity_flow_id, part_number_id)` referencing `quantity_flows (id, part_number_id)` (backed by a unique index on that pair). A Movement can never cite a different PN than its flow.

---

## 4. Business Invariants and Quantity-Integrity Rules

1. **PN identity is stable and unique.** `part_number` and `barcode_value` are unique, treated as arbitrary strings, never reused, never derived from display names.
2. **All quantities are positive integers.** Flow quantity > 0; Movement quantity > 0. Zero and negative quantities are structurally impossible.
3. **A flow occupies exactly one Area at a time.** Multi-Area distribution of a PN is represented by *multiple flows*, never by one flow in two places.
4. **Whole-flow movement (slice rule, D1).** `TRANSFERRED.quantity` must equal the flow's quantity at commit time. Consequence: within this slice, a flow's quantity never changes after creation, so conservation is trivial to verify.
5. **Source validity.** A `TRANSFERRED` Movement's `from_area_id` must equal the flow's current Area *at commit time*, validated under a row lock. This is the slice-specific form of Fundamental Invariant 7 ("no Movement may consume more than is available in its source position").
6. **No self-transfer.** `from_area_id ≠ to_area_id`. A PN whose only flow is already in the station's Area has no valid source → the scan is rejected ("already in this Area").
7. **Conservation.** For each PN: Σ(active flow quantities) = Σ(`RECEIVED` quantities). Until adjustment/stocking types exist, no other terms enter the equation. This is a runnable reconciliation query, usable in tests and integrity checks.
8. **Linear chain per flow.** Movement *n+1*'s `from_area_id` equals Movement *n*'s `to_area_id`; the first Movement of a flow is its `RECEIVED`. Guaranteed by rule 5 plus the lock; verifiable by replay.
9. **Unknown or ambiguous scans never write.** Exactly one candidate source flow is required; zero or many → explicit rejection, nothing recorded (D2).
10. **Inactive entities never accept writes.** Inactive PN, Area, or ScanStation → rejection.
11. **Atomicity.** The Movement insert and the derived-state update commit together or not at all (§9).

---

## 5. Immutability

| Record | Mutability |
|---|---|
| `part_movements` | **Immutable. Insert-only.** No code path updates or deletes rows. Enforced in PostgreSQL: revoke `UPDATE`/`DELETE` from the application role, plus a `BEFORE UPDATE OR DELETE` trigger that raises — per the project rule "enforce via PostgreSQL constraints whenever practical." Corrections (future) append `REVERSED` events; they never touch existing rows. |
| `quantity_flows` | Identity fields (`id`, `part_number_id`, `quantity` in this slice, `created_at`) immutable by convention; `current_area_id` / `status` / `updated_at` are mutable *derived* state (§6). |
| `part_numbers`, `areas` | Mutable master data; identity (`id`, `part_number`, `barcode_value`) stable. Display fields freely editable. |
| `scan_stations` | Mutable configuration. |

---

## 6. Derived State and Its Derivation

Only one piece of derived state is authoritative for the workflow, plus two read models computed at query time.

**`quantity_flows.current_area_id` (maintained projection).**
Rule: *the `to_area_id` of the flow's Movement with the highest `id`*. (`part_movements.id` is a monotonic `bigserial`; insertion order under the flow lock is the event order — `occurred_at` is informational, not ordering.)
Maintenance: updated inside the same transaction that inserts the Movement, while holding the flow's row lock.
Rebuildability: a replay procedure folds all Movements per flow ordered by `id` and asserts (or restores) the stored value. This satisfies "current state must be reconstructable from Movement history" without paying fold-on-read cost in the hot scan path.

**Area inventory ("In this Area now").** Query: active flows grouped by PN for `current_area_id = station.area_id`, summing quantity. In this slice *all* Area quantity is queue quantity (no Machine assignment exists yet), so the queued/assigned split from GUI §4.8 renders with the assigned group empty.

**Recent scans panel.** Query: today's `part_movements` for the station, newest first.

---

## 7. Required Barcode Resolution Data

Resolution is deterministic, server-side, and needs only this data:

1. **Format classification** (pure parsing, no data): the `PF:<TYPE>:<stable-id>` prefix from profile §9. In this slice only `PF:PN:` is *actionable*. Recognized-but-unsupported types (`PF:AREA:`, `PF:MACHINE:`, `PF:WORKER:`, `PF:ACTION:`) are rejected with a *distinct* message ("not usable at this station yet") — different from unknown input. Anything without a valid `PF:` structure is unknown; raw ERP PN text is never auto-accepted as a barcode.
2. **PN lookup:** exact match of the full scanned value against `part_numbers.barcode_value` (unique index). The value is stored and compared verbatim (scanner terminator/whitespace stripped at the input edge only). `is_active` must be true.
3. **Station context:** the station identifier presented by the client resolves via `scan_stations` (unique `code`) to the bound, active Area. The client never supplies an Area id directly.
4. **Manual entry fallback:** an explicitly separate input path that matches `part_numbers.part_number` exactly. It shares everything after resolution with the scan path; it is never applied implicitly to scan input.
5. **Source-flow resolution (post-barcode):** candidate set = active flows of the PN whose `current_area_id ≠ station.area_id`. Exactly one candidate → proceed; zero → reject ("no quantity available to transfer" / "already in this Area"); more than one → reject as ambiguous (D2).

---

## 8. Required Database Constraints and Indexes

**`part_numbers`**
- PK `id`; `UNIQUE (part_number)`; `UNIQUE (barcode_value)`; `NOT NULL` on both; `is_active NOT NULL DEFAULT true`.

**`areas`**
- PK `id`; `name NOT NULL`; `is_active NOT NULL DEFAULT true`.

**`scan_stations`**
- PK `id`; `UNIQUE (code)`; `area_id NOT NULL` FK → `areas`; `is_active NOT NULL DEFAULT true`.

**`quantity_flows`**
- PK `id`; `part_number_id NOT NULL` FK → `part_numbers`; `quantity int NOT NULL CHECK (quantity > 0)`; `status NOT NULL DEFAULT 'ACTIVE'` (single value in this slice; column exists so "live flow" filtering is explicit from day one); `current_area_id NOT NULL` FK → `areas`.
- `UNIQUE (id, part_number_id)` — target for the composite FK from `part_movements` (§3).
- Index `(part_number_id) WHERE status = 'ACTIVE'` — source-flow resolution per scan.
- Index `(current_area_id)` — Area inventory panel.

**`part_movements`**
- PK `id BIGSERIAL` (event order).
- `quantity_flow_id NOT NULL`, `part_number_id NOT NULL`, composite FK `(quantity_flow_id, part_number_id)` → `quantity_flows (id, part_number_id)`.
- `movement_type NOT NULL CHECK (movement_type IN ('RECEIVED','TRANSFERRED'))` — the only two types the slice can produce; the enum widens additively later.
- `quantity int NOT NULL CHECK (quantity > 0)`.
- `from_area_id` FK → `areas` (nullable), `to_area_id NOT NULL` FK → `areas`.
- Shape check: `CHECK ((movement_type = 'RECEIVED' AND from_area_id IS NULL) OR (movement_type = 'TRANSFERRED' AND from_area_id IS NOT NULL AND from_area_id <> to_area_id))`.
- `scan_station_id` FK → `scan_stations` (nullable — NULL for seeded `RECEIVED`).
- `client_event_id NOT NULL`, `UNIQUE (client_event_id)` — idempotency (§10); seed fixtures generate their own keys so the column stays NOT NULL.
- `occurred_at timestamptz NOT NULL DEFAULT now()` (server-assigned; see §12.7).
- Index `(quantity_flow_id, id)` — replay and last-position derivation.
- Index `(scan_station_id, id DESC)` — recent-scans panel.
- Immutability guard: revoke `UPDATE`/`DELETE`; raise-on-write trigger (§5).

Cross-row invariants that PostgreSQL cannot express declaratively (`current_area_id` agrees with the last Movement; `TRANSFERRED.quantity` equals flow quantity; chain linearity) are enforced by the transaction protocol in §9 and verified by the replay/reconciliation checks in §4.7 and §6.

---

## 9. Transaction Boundary for One Scan

One scan submission = exactly one database transaction (default `READ COMMITTED` is sufficient given the row lock):

1. **Begin.**
2. **Idempotency check:** look up `client_event_id`. If present, return the original outcome unchanged (no new write) and mark the response as a replay. (Equivalently: attempt the insert at step 8 and convert the unique-violation into a replay response — either placement is acceptable; the check must be inside the transaction.)
3. **Resolve station:** `scan_stations.code` → active station → active bound Area. Failure → rollback, reject.
4. **Resolve barcode** per §7 (classification, PN lookup, active check). Failure → rollback, reject.
5. **Resolve source flow:** candidate query per §7.5. Zero or >1 candidates → rollback, reject with the specific reason.
6. **Lock:** `SELECT … FOR UPDATE` on the candidate flow row. This serializes all concurrent writes touching the flow.
7. **Re-validate under lock:** flow still `ACTIVE`; `current_area_id` still ≠ station Area (a concurrent scan may have moved it — then reject); confirmed quantity equals flow quantity (D1).
8. **Insert `part_movements`:** `TRANSFERRED`, flow's full quantity, `from = flow.current_area_id`, `to = station.area_id`, station id, `client_event_id`.
9. **Update projection:** `quantity_flows.current_area_id = station.area_id`.
10. **Commit.**

Any failure at any step rolls back the whole transaction — a Movement is never recorded without its projection update, and vice versa. UI refresh (inventory, recent scans) happens after commit via normal reads; no external side effects live inside the transaction.

---

## 10. Duplicate-Scan and Retry Handling

Three layers, each catching a different failure:

1. **Transport retry (same intent, network doubt).** The client generates one `client_event_id` (UUID) per *scan submission* and reuses it on every retry of that submission (timeout, connection reset, unknown outcome). The unique constraint guarantees at-most-once recording; the replay path in §9.2 makes the retry return the original success. An interrupted request is therefore always safe to retry blindly.
2. **Semantic duplicate (operator scans the same PN again).** A new scan gets a new `client_event_id`, so idempotency does not mask it — correctly, because it is a new intent. It is caught by resolution: after the first transfer, the flow's `current_area_id` equals the station Area, the candidate set is empty, and the scan is rejected with the explicit "already in this Area" error (invariant 6). Nothing is recorded.
3. **Input debounce (scanner double-fire).** The UI ignores identical raw input arriving within a short window while a submission is in flight, and clears/refocuses the input after each outcome (GUI §4.9). This is UX hygiene only — layers 1–2 already guarantee correctness without it.

Concurrent scans of the *same PN from two stations* resolve to the same flow; the row lock serializes them, the second re-validation fails, and the second station receives a clean rejection rather than a partial write.

---

## 11. Explicitly Deferred Entities and Capabilities

Deferred, with the additive path that makes each safe to postpone:

| Deferred | Extension path (no core redesign) |
|---|---|
| `Department` | New table + nullable `department_id` on `areas`. |
| `PurchaseOrder`, `PoDemand`, `PoAllocation`, priority/Hot, temporary POs | New tables; PO Demand and Allocation are already separate from Movement by design — zero impact on `part_movements`. |
| `Operation` | New table + nullable `operation_id` on `part_movements` (queue receipt records no Operation; see §12.3). |
| `Machine`, machine sessions, `areas.machine_assignment_mode` | New table, new columns, new movement types `ASSIGNED_TO_MACHINE` / `RELEASED_FROM_MACHINE`, nullable machine columns on `part_movements`. |
| `Worker`, worker sessions, `ScanSession` | New tables + nullable `worker_id` / `scan_session_id` on `part_movements`. Session state stays out of slice 1 entirely (no session-worthy context exists in a queue-only transfer). |
| Routes (`RouteTemplate`, `RouteStep`, `AssignedRoute`), deviations | New tables + nullable `route_step_id`; route assignment attaches to `QuantityFlow`, which already exists. |
| Split / merge, partial-quantity transfer | `parent_flow_id` column, `SPLIT`/`MERGED` types, quantity entry becomes a real split amount. Flow identity and conservation rules already accommodate this. |
| Undo / corrections | `REVERSED` type + `reverses_movement_id` column; append-only model already assumes it. |
| Stockroom completion, `STOCKED`, allocation UI | New movement type + allocation tables; `is_terminal` column on `areas`. |
| Additional movement types (`QUANTITY_ADJUSTED`, `ROUTE_ADJUSTED`, `ROUTE_DEVIATION_CONFIRMED`) | Widen the type check. |
| Area / Machine / Worker / Action barcodes | `barcode_value` columns on their tables; the `PF:` classifier already reserves the prefixes and rejects them cleanly today. |
| Roles and authorization | Later slice per roadmap; no schema coupling to Movement. |
| Offline queue, `server_received_at`, ERP fields (`erp_id`), reporting, analytics | Explicitly out per roadmap "Deferred"; `client_event_id` is deliberately already in place so offline replay could reuse it. |
| PN display extras (`description`, `image_url`, `current_revision`) | Nullable columns, additive. |

---

## 12. Conflicts and Ambiguities Found in Current Documentation

1. **"Job / Part" vocabulary is stale.** `IMPLEMENTATION_ROADMAP.md` Phase 3 lists "Job" and "Part" as domain scope, and `CLAUDE.md` §3 says PartFlow "must track Jobs, Parts…". PROJECT_PROFILE v5 has *no Job entity*: external Job Numbers are strings on `PoDemand`, and the tracked identity is `PartNumber`. Per the canonical-source order, the profile wins; this design contains no Job entity. The roadmap and CLAUDE.md wording should be updated to the v5 vocabulary.
2. **No quantity-introduction workflow exists before slice 7.** Phase 4 transfers quantity that nothing in the approved scope has introduced (PO intake is a later slice). Assumption D3 (seeded `RECEIVED` fixtures) fills the gap; the roadmap should state this explicitly.
3. **"Area queue" semantics vs. profile §11.** The queue concept applies to Areas that require Machine selection; Areas *without* Machines take direct processing ownership and record the configured Operation. Slice 1 has no Machine and no Operation entity, so it models every receipt as a queue receipt with no Operation recorded. This is correct for a multi-machine pilot Area (e.g. Lathe/Mill) but means: **if the first deployed station is an Area without Machines, the Operation entity and §11 ownership behavior are needed earlier than the roadmap sequence implies.** The pilot Area choice should be confirmed.
4. **Ambiguity: dialog vs. rejection.** GUI §4.6 specifies a full ambiguity dialog; roadmap Phase 4 only requires ambiguous scans to be *rejected*. Not a contradiction, but the slice boundary (D2: reject with reason; dialog later) should be recorded so the UI slice doesn't silently expand scope.
5. **Quantity entry vs. whole-flow transfer.** GUI §4.7's numeric keypad implies operators may move a partial amount, which requires `SPLIT` — not in this slice. Under D1 the modal can only confirm the full amount (or be skipped when configuration allows). This slice restriction should be acknowledged in the GUI doc or the slice notes, otherwise the mockup overpromises.
6. **ScanStation is referenced but never defined.** Profile §14 binds each Scan Station to one Area and §8.11 puts `station_id` on PartMovement, yet §8 defines no Station entity. This design introduces a minimal `scan_stations` config entity; the profile should adopt it (or explicitly delegate station binding elsewhere).
7. **`occurred_at` vs. `server_received_at`.** Both fields exist in the profile's PartMovement to support offline capture — which is deferred. This slice keeps a single server-assigned `occurred_at`; `server_received_at` is added only when offline sync arrives. Meanwhile `device_event_id` is realized now as `client_event_id`, because retry safety is needed immediately even without offline support.
8. **Minor:** roadmap Phase 4 step 2 says "Scan a part or production quantity" — under the profile's barcode model the only scannable production identity is the PN barcode; quantity is never scanned. Cosmetic, but worth aligning.
