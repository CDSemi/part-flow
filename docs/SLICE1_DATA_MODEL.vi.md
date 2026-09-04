# Slice 1 — Data model nhập Work Order thủ công và release sản xuất

> **Bản gốc chuẩn:** [`SLICE1_DATA_MODEL.md`](SLICE1_DATA_MODEL.md).
> Baseline upstream: commit `4fd635f2020189fa279adc6988268c36c39d595b`.
> File EN là source of truth.
>
> **Trạng thái:** Đã triển khai. Đây là contract chuẩn của Phase 4, không phải
> proposal: schema có migration `0004_phase4_audit` và
> `0005_phase4_release_index`, command ở Application layer, Acceptance Criteria
> §19 được test. Tài liệu này vẫn subordinate với `PROJECT_PROFILE.md` và
> `GUI_DESIGN.md`; khác biệt với implementation là defect cần sửa, không phải
> giấy phép tự phát minh behavior.
>
> **Phạm vi:** Roadmap Phase 4 — nhập Work Order/Demand thủ công rồi explicit
> release production quantity vào starting Area được cấu hình.

---

## 1. Mục tiêu và non-goal

**Mục tiêu:** vertical slice business đầu tiên end to end:

1. Create/find `WorkOrder`.
2. Create/find `PartNumber` cùng PartFlow barcode unique.
3. Create/update `WorkOrderDemand` cho Work Order.
4. Save business demand **không** tạo production quantity.
5. Cung cấp explicit release command riêng: tạo `QuantityFlow` với route mode
   (`FLOATING` mặc định, không AssignedRoute; hoặc `PLANNED` có snapshot), append
   immutable `RECEIVED` Movement và đặt current position, tất cả transactional và
   idempotent.

**Ngoài slice:** transfer tại Scan Station, Machine assignment, Worker/Machine
session, SPLIT/MERGED, Undo/correction, Stockroom/Allocation, file import,
authentication/role, ERP và mọi offline behavior. Xem §18.

---

## 2. Entity bắt buộc và trách nhiệm

| # | Entity | Trách nhiệm trong slice |
|---|---|---|
| 1 | `Department` | Owner tổ chức của Area, context cấu hình; initial Machine Shop |
| 2 | `Area` | Stable physical location; cung cấp configured starting Area và là destination của `RECEIVED` |
| 3 | `Operation` | Công việc của Area; resolve/confirm khi release và ghi trên Movement |
| 4 | `PartNumber` | Optional current metadata cho canonical PN; production row tự giữ PN và không phụ thuộc master |
| 5 | `WorkOrder` | Business shell: nullable opaque external number, received date, nullable due date, status |
| 6 | `WorkOrderDemand` | Requested quantity + Request Type/due/priority/Job/requester/reason/notes; chỉ business demand |
| 7 | `RouteTemplate` | Reusable route được chọn khi Planned release |
| 8 | `RouteStep` | Ordered Area/Operation/duration/instruction của template |
| 9 | `AssignedRoute` | Independent snapshot cho một `PLANNED` Flow; Floating không có |
| 10 | `QuantityFlow` | Traceable physical quantity tạo bởi release, route mode/snapshot và current-position projection |
| 11 | `PartMovement` | Immutable production event; slice này chỉ tạo `RECEIVED`, là production source of truth |
| 12 | Current-position projection | Rebuildable field `current_area_id`; `current_machine_id` đến Phase 6 |
| 13 | Audit event | Generic append-only history cho master/business demand; infrastructure, không domain aggregate |

`scan_stations` là stable app/infrastructure config, không core aggregate. Release
từ Management không có Station nên slice này không tạo `scan_stations` hoặc
`station_id`; table đến Phase 3.5 và Movement column đến Phase 5.

---

## 3. Relationship

```text
Department      1 ──── *    Area
Area            1 ──── *    Operation
WorkOrder       1 ──── *    WorkOrderDemand
RouteTemplate   1 ──── *    RouteStep
QuantityFlow    1 ──── 0..1 AssignedRoute     (PLANNED only)
AssignedRoute   1 ──── *    AssignedRouteStep
QuantityFlow    1 ──── *    PartMovement
Area            1 ──── *    PartMovement (to)
Operation       1 ──── *    PartMovement
```

PN được carry **by value**, không surrogate FK:

```text
WorkOrderDemand.part_number
QuantityFlow.part_number
PartMovement.part_number
```

Production row không FK tới `part_numbers`; master có thể hard-delete mà không
đụng history. PN agreement giữa Movement và Flow dùng composite FK
`(quantity_flow_id, part_number)` → `quantity_flows (id, part_number)`.

Movement không có `work_order_demand_id`. Release có thể ghi Demand context trong
metadata để audit display, nhưng Movement vẫn là PN + Flow + quantity. Demand
không sở hữu Movement; Allocation slice sau vẫn tách cả hai.

---

## 4. Business invariant

1. Canonical PN string UPPERCASE/no-whitespace là identity; production row tự giữ.
2. Save/edit WorkOrder/Demand không create/change/destroy production quantity.
3. Quantity chỉ vào hệ thống qua explicit release + `RECEIVED`; mỗi Flow bắt đầu
   bằng `RECEIVED`; Planned có đúng một snapshot, Floating không có.
4. Mọi quantity là positive integer.
5. `RECEIVED.quantity` bằng flow quantity.
6. Một Flow ở đúng một current Area; multi-Area dùng nhiều Flow.
7. Release không merge/implicit-add; active PN cần explicit confirmation.
8. Movement append-only; state reconstructable.
9. Flow + initial projection + optional snapshot + Movement atomic.
10. Cùng `device_event_id` và normalized request trả original result; khác request
    là idempotency conflict, zero write.
11. Inactive Area/Operation/RouteTemplate không nhận release. PartNumber master
    không có active lifecycle và không phải precondition.

---

## 5. Validation WorkOrder và WorkOrderDemand

- `work_order_number` là nullable opaque string. Blank được explicit-confirm rồi
  lưu `NULL`, UI hiển thị `—`, có thể audited-edit sau; multiple NULL được phép,
  non-null unique bằng partial index. Không temporary number; giữ nguyên entered
  string. Existing non-null number mở existing Work Order, không duplicate.
- `received_date` required, default current date; `work_orders.due_date` nullable
  và chỉ làm default cho demand-line due date.
- Demand cần canonical PN, `request_type IN ('NEW','MODIFY')`,
  `requested_quantity > 0`; line due date nullable. Manual default NEW; Scan
  Station intake default MODIFY; Repair không phải Request Type.
- Canonical demand order: Hot `priority_rank`, rồi `due_date ASC NULLS LAST`, với
  undated dùng parent `received_date ASC`, cuối cùng stable creation/id tie-break.
  Slice 1 chưa consume order; index đến phase tương ứng.
- Một canonical PN xuất hiện tối đa một lần trong current lines của một Work Order.
  Không unique index; Application layer lock parent WorkOrder rồi re-read PN set để
  serialize concurrent add. Loser nhận duplicate-demand error và zero write. Lock
  order: Demand id tăng dần → WorkOrder, tương thích removal/release.
- `job_numbers` là list opaque string metadata; `priority_rank` nullable.
- Admin/Manager edit có audit và không chạm Flow/Movement. Sau release chỉ
  `requested_quantity`, `due_date`, `job_numbers` sửa được; quantity không thấp hơn
  `max(released, allocated)`. `request_type`, `requester`, `reason`, `notes` bị
  từ chối; saved PN không sửa; released line không remove.

---

## 6. Normalize/create PartNumber và barcode

- Trim đầu/cuối, reject empty/internal whitespace, uppercase. `abc-123`,
  `AbC-123`, `" ABC-123 "` đều thành `ABC-123`; `"ABC 123"` và tab/newline bên
  trong invalid.
- Create master on first valid use, không catalog preload. Master keyed canonical
  PN nhưng optional; production table không FK, nên có thể hard-delete/recreate.
- Barcode derive `PF:PN:<part-number>`; không stored key riêng và không encode WO,
  quantity, route hay location.

---

## 7. Tách Demand save khỏi production release

- **Save demand** chỉ write `work_orders`/`work_order_demands`; không Flow,
  Movement hay projection change.
- **Release to production** (§8) là command riêng, không trigger ngầm bởi save,
  import hay edit.

Boundary chuẩn: WorkOrder/Demand là business demand, không định nghĩa current
position; release mới explicit-introduce physical quantity.

---

## 8. Explicit release command

Input: PN, release quantity, Route Mode, optional template cho Planned, confirmed
starting Area/Operation, optional Demand context và `device_event_id`.

Trong một transaction:

1. Normalize/validate PN; active, non-terminal starting Area; valid Operation;
   active RouteTemplate khi Planned; positive quantity không vượt Demand remaining.
2. Nếu PN có active Flow, request phải mang explicit confirmation sau khi UI show
   distribution; nếu không reject. Không auto-create/merge.
3. Snapshot route chỉ với `PLANNED`.
4. Create Flow với `route_mode`, snapshot id hoặc NULL, và
   `current_area_id = starting Area`.
5. Append `RECEIVED`; Planned reference first assigned step, Floating NULL; ghi
   resolved Operation; metadata có fingerprint, actor/Demand context informational.
6. Commit và trả flow id, mode, optional snapshot id, Area, Operation, quantity,
   Movement id.

Không append generic audit row; `RECEIVED` chính là immutable production audit.

### 8a. Partial và repeated release của một Demand

Demand 50 có thể release 20, 12, 18. Mỗi part có riêng event id, Flow, Movement và
từ part thứ hai phải confirm active quantity; không merge.

- `released_quantity` derive bằng tổng `RECEIVED.quantity` có
  `metadata.context.work_order_demand_id`; không counter/column/migration.
- `remaining = requested − released` là hard server cap. Demand row lock
  `FOR UPDATE` serialize concurrent release, không thể jointly over-release.
- Released line edit chỉ quantity/due/Jobs; quantity floor là max(released,
  allocated). Một invalid `line_edits` làm cả save transaction zero write. Edit và
  release dùng cùng row lock và recompute released quantity, nên bất kể arrival
  order vẫn không thể `released > requested`. Removal vẫn refused.
- Read model expose released/remaining. Work Order chỉ `RELEASED` khi mọi line
  remaining = 0; partly released vẫn `OPEN`.

---

## 9. Tạo QuantityFlow

Slice dùng `id`, canonical `part_number`, positive `quantity`, initial
`status='ACTIVE'`, route mode, nullable snapshot id, timestamps/closed_at. Lineage
không thuộc slice; Phase 8 dùng append-only `quantity_flow_lineage` edge table để
biểu diễn 1→N và N→1 thay vì `parent_flow_id` đơn.

Projection `current_area_id NOT NULL` được set ngay khi INSERT; `updated_at` đi
cùng. `current_machine_id` đến Phase 6. Quantity Flow không mutate trong slice;
conservation là Σ active flow = Σ RECEIVED. Phase 9 addition cũng tạo Flow mới,
không sửa quantity flow cũ.

---

## 10. Tạo AssignedRoute snapshot (chỉ `PLANNED`)

- Copy template steps vào `assigned_routes` + `assigned_route_steps`: sequence,
  Area, Operation, expected duration, instruction. Floating không snapshot.
- `source_route_template_id` informational; snapshot độc lập với template edit.
- Flow sở hữu snapshot qua `quantity_flows.assigned_route_id`; snapshot không có
  reverse `quantity_flow_id`. `UNIQUE (assigned_route_id)` và CHECK route mode.
- First snapshot step phải match confirmed starting Area/Operation; mismatch là
  validation error, không silent adjust.

---

## 11. `RECEIVED` PartMovement

Shape:

- `movement_type='RECEIVED'`;
- `from_area_id NULL`, `to_area_id` = starting Area;
- `operation_id NOT NULL`, quantity = Flow quantity, PN agreement qua composite FK;
- `assigned_route_step_id` trỏ **snapshot step**, không template row; Planned dùng
  first step của chính Flow snapshot, Floating NULL. Cross-table invariant được
  transaction protocol + reconciliation/test enforce;
- later canonical columns `movement_reason`, `reason`, `reverses_movement_id`,
  `station_id`, `worker_id`, `scan_session_id` chưa tạo trong slice;
- timestamps; unique `device_event_id`; metadata fingerprint/context;
- immutable: app role không UPDATE/DELETE và raise-on-write trigger. Retention
  maintenance sau này dùng privileged Admin path riêng.

---

## 12. Resolve starting Area và Operation

Starting Area đến từ Department/Route config, UI confirm, không guess. Một active
Operation có thể auto-resolve; nhiều Operation phải explicit confirm. Operation
schema tồn tại trước Movement đầu tiên và mọi valid `RECEIVED` ghi Operation.

---

## 13. Transaction boundary

```text
BEGIN
  idempotency check: device_event_id + request fingerprint
  validate PN / Area / Operation / RouteTemplate / quantity
  check active-quantity confirmation
  INSERT assigned_routes + assigned_route_steps  -- PLANNED only
  INSERT quantity_flows                          -- đầy đủ projection
  INSERT part_movements (RECEIVED)               -- snapshot step nếu Planned
COMMIT
```

Flow được insert đầy đủ với `current_area_id`; không có invalid intermediate row.
Failure rollback toàn bộ. Release không ghi generic audit event và không có external
side effect trong transaction. Demand save là transaction trước, riêng biệt.

---

## 14. Idempotency và retry

- Client tạo một UUID `device_event_id` cho mỗi release intent và reuse khi retry.
- Server hash normalized request gồm tối thiểu PN, quantity, route mode/template,
  Area, Operation, optional Demand context; lưu fingerprint trong Movement metadata.
  Không cần idempotency table riêng.
- Cùng id + cùng fingerprint → trả original committed result, không write.
- Cùng id + khác fingerprint → explicit conflict, không write; đây là client defect.
- New intent dùng id mới và vẫn chịu active-quantity confirmation.
- Idempotency ở slice chỉ cho release vì đây là command introduce quantity. Demand
  save không cần key; file import tương lai có contract riêng.
- Online synchronous: server có thể đặt `occurred_at = server_received_at`; không
  offline queue. `device_event_id` vẫn compatible với thiết kế offline được duyệt
  sau này.

---

## 15. Derived current-position projection

`quantity_flows.current_area_id` là maintained projection để đọc inventory/board
nhanh. Release set trong cùng transaction; Movement sau cập nhật dưới lock.
Movement history vẫn source of truth: projection = destination của latest effective
Movement và replay phải rebuild/assert được. Correctness-critical decision không
tin projection nếu chưa lock Flow trong transaction.

---

## 16. Audit persistence model

Audit trong slice:

- WorkOrder/Demand create/edit;
- PN master creation;
- release do `RECEIVED` Movement audit, gồm actor/context informational.

Hai mechanism tách trách nhiệm:

1. `PartMovement` là production audit/source of truth và replay projection; không
   duplicate generic audit cho production action.
2. `audit_events` append-only chỉ cho master/business demand: WorkOrder,
   WorkOrderDemand, PartNumber. Không replay để build state và không phải generic
   event-sourcing framework.

`audit_events`: BIGSERIAL id, `CREATED|UPDATED`, entity type/key, nullable actor,
timestamp, `before_data`/`after_data`, metadata. Entity id polymorphic không FK;
integrity do audit row và change commit cùng transaction. PartNumber entity id là
canonical PN. Phase 14 có thể migrate actor tới User.

Mọi audited write phải có audit row cùng transaction. Audit immutable qua revoke +
trigger; creation có before NULL, update append row mới, không rewrite row cũ.

---

## 17. Database constraint và index

**`departments`** — PK `id`, unique name, active flag, timestamps.

**`areas`** — PK, Department FK, required name, unique barcode nếu có, active,
timestamps. `is_terminal` và `worker_identification_mode` đến phase dùng; không có
`machine_assignment_mode` vì behavior derive từ Machines.

**`operations`** — PK, Area FK, code, `UNIQUE (area_id, code)`, name/active/time.

**`part_numbers`** — PK natural `part_number text`, CHECK:

```text
part_number = upper(part_number)
AND part_number !~ '\s'
AND part_number <> ''
```

Không surrogate id, active flag, stored barcode hoặc lowercase index; production
không FK. Barcode derive và master delete/recreate được.

**`work_orders`** — internal PK; nullable number với partial unique non-null;
required received date; nullable due; status/time. `completed_at` không thuộc slice,
đến Phase 10 và có partial `(completed_at,id)` index cho completed history.

**`work_order_demands`** — WorkOrder FK; canonical PN by value/no master FK;
Request Type CHECK; positive requested; non-negative allocated default 0; nullable
due/priority; Job Numbers `text[]` default empty; optional context fields/time;
indexes WorkOrder và PN. Không Job aggregate/GIN index trong slice.

**`route_templates`** — name, optional description, nullable `archived_at`;
không version column. Used template archive, never-used delete.

**`route_steps`** — Template FK, sequence unique per template, Area, optional
Operation/duration/instruction. `preferred_machine_id` đến phase dùng Machine.

**`assigned_routes`** — PK, optional source template, snapshot time; không reverse
Flow reference.

**`assigned_route_steps`** — AssignedRoute FK, unique sequence, Area, optional
Operation/duration/instruction.

**`quantity_flows`** — PK, canonical PN/no master FK, positive quantity, active
status, route-mode CHECK, nullable AssignedRoute FK unique, exact-mode CHECK:

```text
(route_mode = 'PLANNED') = (assigned_route_id IS NOT NULL)
```

`current_area_id NOT NULL`, composite unique `(id, part_number)`, timestamps,
partial active-PN index và Area index. Không lineage column.

**`part_movements`** — BIGSERIAL event order; Flow id + canonical PN composite FK;
`RECEIVED` type/shape; positive quantity; source null/destination required;
required Operation; optional assigned snapshot step; timestamps; unique event id;
metadata; `(quantity_flow_id,id)` index. Partial expression index cho released
quantity:

```text
((metadata['context'] ->> 'work_order_demand_id')::int)
WHERE movement_type = 'RECEIVED'
```

JSONB subscript expression phải khớp Application exactly; index không tạo column,
FK hay stored counter. UPDATE/DELETE bị guard.

**`audit_events`** — BIGSERIAL, constrained event/entity types, polymorphic id,
actor/time/before/after/metadata, `(entity_type,entity_id,id)` index, append-only.

Slice migration không FK tới table không tạo; deferred Station/Machine columns đến
phase sau. Cross-row invariant (projection/latest Movement, first RECEIVED,
route-step ownership/mode, audit same transaction, one PN per WO) do transaction
protocol, reconciliation và concurrency test enforce.

---

## 18. Capability được defer rõ ràng

| Deferred | Phase/trạng thái | Additive path |
|---|---|---|
| `scan_stations`, `machines`, terminal/config fields | Phase 3.5 — implemented | configuration migrations; workflow dùng ở Phase 5–7 |
| Transfer/`TRANSFERRED` | Phase 5 — implemented, `0006` | add station id, widen Movement type/shape, fingerprint + deviation metadata |
| one-shot Machine assignment, assign/release | Phase 6 — implemented, `0007` | current/source/destination Machine; `command_sequence`; unique `(device_event_id,sequence)` |
| `AREA_COMPLETED`/READY | Phase 6/7 — implemented | clear Machine giữ Area; implicit completion + transfer cùng event id; direct completion cho Machine NULL |
| Direct Area processing | Phase 7 — implemented, `0008` | derived PROCESSING, không stored mode/column |
| SPLIT/MERGED partial | Phase 8 — implemented, `0009` | types, flow lifecycle, append-only lineage edge table |
| Undo/Repair/Scrap/Adjustment | Phase 9 — implemented, `0010` | reason/reversal columns, types/status; complete-command reversal; addition tạo Flow mới |
| Stockroom/Allocation | Phase 10 — implemented, `0011` | STOCKED/closed flow, completed projection, append-only allocation/reversal; không FK Movement/Flow |
| Monitoring read models | Phase 11 | Movement-derived query |
| Priority/Hot UI | Phase 12 | priority column đã có |
| Full Administration | Phase 13 | master tables đã có từ Phase 3.5 |
| Authentication/role | Phase 14 | actor may migrate; không couple Movement |
| File Work Order import | Phase 15 | reuse validation idempotently |
| Worker/ScanSession persistence | Phase 6+ / pending | Worker/session/Area mode columns khi triển khai |
| ERP/offline sync | Deferred, chưa duyệt | isolated boundary; event id compatible |

---

## 19. Acceptance Criteria

1. Save WorkOrder/Demand không tạo Flow, Movement hoặc projection change.
2. First-use PN normalize đúng, equivalent case/surrounding space resolve một
   record; internal whitespace reject zero write; derive barcode; append PN CREATED
   audit.
3. Release tạo đúng một Flow + `RECEIVED` và chỉ Planned tạo đúng một snapshot;
   atomic, không generic audit; không observable partial state.
4. Flow INSERT set `current_area_id` confirmed, NOT NULL, không post-update.
5. Mọi RECEIVED có Operation; Planned trỏ first AssignedRouteStep của snapshot,
   Floating NULL.
6. Invalid PN/inactive Area/Operation/Planned template/quantity ≤0: zero write.
7. Existing active PN thiếu explicit flag: reject; có flag tạo separate Flow,
   không merge.
8. Same event id + same fingerprint: replay original, no new row.
9. Same id + different fingerprint: explicit conflict, zero write.
10. Template edit không đổi snapshot/Movement context đã tồn tại.
11. Replay rebuild đúng `current_area_id` cho mọi Flow.
12. App role không update/delete Movement/audit row.
13. Mỗi WorkOrder/Demand create/edit có audit row cùng transaction; prior row giữ.
14. Generic audit chỉ chứa WorkOrder, Demand, PartNumber; không production release.
15. Conservation: Σ active flow quantity per PN = Σ RECEIVED per PN trong slice.
16. Release vào terminal Area luôn reject zero write.
17. Partial releases tạo riêng Flow/RECEIVED tới khi remaining zero; exceed/release
    after zero reject; WO chỉ RELEASED khi mọi line remaining zero.
18. Released line restricted edit đúng field/floor; invalid edit zero write; raise
    quantity restore remaining/OPEN; history/header unaffected.
19. Slice migration không FK table chưa tạo hay deferred unused column, gồm Machine,
    Worker, Session, Station và các Phase 5+ fields.

---

## 20. Bất định còn lại

Chỉ hai open decision ở Project Profile §32 liên quan nhưng không block slice:

- controlled return từ stock có thể widen Movement type sau; hiện STOCKED không
  Undo, Allocation adjustment là correction path;
- offline sync chưa duyệt; slice online synchronous, `device_event_id` giữ đường
  mở tương thích.

Due date đã chốt nullable ở WorkOrder và Demand, missing date là valid data và
undated xếp sau dated; không cần policy toggle/migration.
