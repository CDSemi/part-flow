# Slice 1 — Data model nhập Work Order thủ công và release sản xuất

> **Bản gốc chuẩn:** [`SLICE1_DATA_MODEL.md`](SLICE1_DATA_MODEL.md).
> Baseline upstream: commit `f96bf09` (không có thay đổi domain sau `f10d8bd`).
> **Trạng thái đồng bộ:** các thay đổi Phase 13 của bản EN đã được dịch theo từng slice đến bản
> đóng Phase 13 (sau commit `dbd42ee`) và các thay đổi Phase 14 slice 1 (sign-in), slice 2 (Administration enforcement) và slice 3 (Management enforcement) và slice 4 (thiết bị Scan Station) và slice 5 (Management allocation và correction beyond-demand) và slice 6 (AssignedRoute adjustment) đã được dịch theo đúng các đoạn thay đổi, nhưng chưa review diff đầy đủ so với baseline `f96bf09`
> theo TRANSLATION_POLICY §4, nên baseline chưa được nâng; nếu hai bản khác nhau, bản EN đúng.
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
| 9 | `AssignedRoute` | Independent snapshot cho một `PLANNED` Flow; Floating không có; past step bất biến — chỉ future step chưa được tham chiếu mới đổi, qua command `ROUTE_ADJUSTED` có audit (Phase 14 slice 6) |
| 10 | `QuantityFlow` | Traceable physical quantity tạo bởi release, route mode/snapshot và current-position projection |
| 11 | `PartMovement` | Immutable production event; slice này chỉ tạo `RECEIVED`, là production source of truth |
| 12 | Current-position projection | Rebuildable field `current_area_id`; `current_machine_id` đến Phase 6 |
| 13 | Audit event | Generic append-only history cho master/business demand/cấu hình; infrastructure, không domain aggregate |

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
- `job_numbers` là list opaque string metadata; `priority_rank` nullable và là nơi lưu priority duy nhất: demand là **Hot** đúng khi nó khác NULL, rank 1 là cao nhất (không flag, không table riêng). Từ Phase 12 các demand có rank mang đúng các rank `1..N`, **unique và dense** (do §17 enforce), và `priority_rank` có đúng hai writer (IMPLEMENTATION_ROADMAP Phase 12): Hot-list command (`POST /api/hot-list/changes`) và automatic / line-deletion removal của `app/application/hot_ranks.py` — Work Order create và edit vẫn từ chối field này như input. Invariant **H2** (quyết định 2026-10-04): tại mọi trạng thái đã commit không có demand có rank nào inactive — Hot entry mà demand trở thành inactive (Work Order completed, hoặc line allocate đủ) bị remove tự động, trong cùng transaction, bởi allocation confirmation hoặc Work Order save hạ quantity đã gây ra nó. Eligibility để vào list là rule của Application, xét trên row đã lock và đọc lại: demand tồn tại, chưa có rank, Work Order của nó chưa completed, và nó **active** (PROJECT_PROFILE §14: `requested_quantity > allocated_quantity`). Mỗi demand line được chọn và xếp rank riêng. **Hot demand line chỉ được remove với cờ confirmation tường minh** (quyết định 2026-10-04): không có cờ, khi mọi rule removal khác đều thỏa, removal là 409 không ghi gì và nêu rank hiện tại của line; có cờ, entry rời Hot list (renumber dense, có audit) và line bị xóa trong một transaction, dưới Hot advisory lock và các row lock, nên serialize với Hot command. Command, và automatic removal bên trong allocation làm completed, cũng có thể đổi rank, kèm audit row, của demand thuộc Work Order **completed** (§16).
- Admin/Manager edit có audit và không chạm Flow/Movement. Sau release chỉ
  `requested_quantity`, `due_date`, `job_numbers` sửa được; quantity không thấp hơn
  `max(released, allocated)`. `request_type`, `requester`, `reason`, `notes` bị
  từ chối; saved PN không sửa; released line không remove. Một edit mang quantity không đổi
  không bị xét (line được allocate vượt demand bằng correction được cấp quyền vẫn sửa được
  các field khác).

---

## 6. Normalize/create PartNumber và barcode

- Trim đầu/cuối, reject empty/internal whitespace, uppercase. `abc-123`,
  `AbC-123`, `" ABC-123 "` đều thành `ABC-123`; `"ABC 123"` và tab/newline bên
  trong invalid.
- Create master on first valid use, không catalog preload. Master keyed canonical
  PN nhưng optional; production table không FK, nên có thể hard-delete/recreate. Từ Phase 13 slice 7, `POST /api/part-numbers` chỉ tạo mới và lấy PN advisory lock (mọi nơi tạo master đều tuần tự hóa theo PN), `GET ?search=` cũng khớp name đã lưu, và Management → Part Numbers sửa, quản lý ảnh và hard-delete master; create-on-first-use không đổi.
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
   resolved Operation; metadata có fingerprint, User đã sign-in đã release dưới dạng `context.actor_user_id` (Phase 14 slice 3; do server suy ra, không bao giờ từ request; release cũ không có) và Demand context optional.
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
  allocated), chỉ xét khi quantity bị đổi. Một invalid `line_edits` làm cả save transaction zero write. Edit và
  release dùng cùng row lock và recompute released quantity, nên bất kể arrival
  order vẫn không thể `released > requested`. Removal vẫn refused, và line đang trên Hot list chỉ được remove với confirmation của §5.
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
  Area, Operation, expected duration, instruction và — từ Phase 13 slice 8 —
  `preferred_machine_id` (cũng nằm trên bản copy split/merge). Merge
  compatibility ("snapshot bằng nhau về cấu trúc") bao gồm `preferred_machine_id`,
  nên các flow có snapshot chỉ khác nhau ở preferred Machine không bao giờ được
  merge. Floating không snapshot.
- `source_route_template_id` informational; snapshot độc lập với template edit.
- Flow sở hữu snapshot qua `quantity_flows.assigned_route_id`; snapshot không có
  reverse `quantity_flow_id`. `UNIQUE (assigned_route_id)` và CHECK route mode. Deviation được ghi trên arrival Movement (Phase 5); route editing là adjustment của Phase 14 (§16).
- First snapshot step phải match confirmed starting Area/Operation; mismatch là
  validation error, không silent adjust.

---

## 11. `RECEIVED` PartMovement

Shape:

- `movement_type='RECEIVED'`;
- `from_area_id NULL`, `to_area_id` = starting Area;
- `operation_id NOT NULL`, quantity = Flow quantity, PN agreement qua composite FK;
- `assigned_route_step_id` trỏ một snapshot step (step được tham chiếu là past step và không bao giờ đổi — trigger cấm UPDATE ở §17, foreign key từ chối xóa nó), không template row; Planned dùng
  first step của chính Flow snapshot, Floating NULL. Cross-table invariant được
  transaction protocol + reconciliation/test enforce;
- later canonical columns `movement_reason`, `reason`, `reverses_movement_id`,
  `station_id`, `worker_id`, `scan_session_id` chưa tạo trong slice (`worker_id` đến cùng `0019_phase13_worker_identity`; `scan_session_id` đến cùng `0020_phase13_worker_sessions` (FK tới `worker_sessions`, `ScanSession` của PROFILE));
- timestamps; unique `device_event_id`; metadata fingerprint/context (gồm User release `context.actor_user_id` từ Phase 14 slice 3);
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
- **User khác (Phase 14 slice 3).** Một Management command (release, Management allocation và reversal của nó, Hot list change) bị replay bởi User khác với User đã ghi nó bị từ chối bằng 409 `recorded_by_another_user` và không ghi gì; record tạo trước khi có sign-in, không có User được ghi, được tính là do User khác ghi. Cùng User replay từ session hay browser khác thì replay bình thường. Fingerprint được kiểm tra trước, và identity không bao giờ là một phần của fingerprint.
- **Assigned Route adjustment (Phase 14 slice 6).** Audit row của adjustment chính là idempotency record của nó, được làm race-free bởi index UNIQUE partial trên `device_event_id` của nó (§17) — không phải advisory lock như Hot list; fingerprint gồm flow, các future step id đã đọc, các step mới và reason, không bao giờ gồm danh tính actor; replay trả kết quả đã ghi chỉ từ audit row, và replay bởi User khác với cùng danh tính bị từ chối như quy tắc trên.
- **Correction beyond-demand (Phase 14 slice 5).** Correction là command có kiểu dùng chung namespace `device_event_id` của allocation; fingerprint của nó mang giá trị `command` riêng và không bao giờ mang identity người thực hiện, và việc User khác replay cùng identity bị từ chối như quy tắc trên.
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
- PN master creation (từ Phase 13 slice 7 còn sửa, đổi ảnh và hard delete master);
- release do `RECEIVED` Movement audit, gồm User release (`context.actor_user_id`, từ Phase 14 slice 3) và Demand context trong metadata.

Hai mechanism tách trách nhiệm:

1. `PartMovement` là production audit/source of truth và replay projection; không
   duplicate generic audit cho production action.
2. `audit_events` append-only chỉ cho master, business demand và thay đổi cấu hình: WorkOrder,
   WorkOrderDemand và PartNumber (Phase 4), Worker, các entity cấu hình môi trường và cấu hình Machine (Phase 13, bên dưới), cùng (Phase 14) correction `ROUTE_ADJUSTED` của future step của một AssignedRoute — correction route-guidance duy nhất được audit ở đây; nó không di chuyển quantity nên không bao giờ là Movement. Không replay để build state và không phải generic
   event-sourcing framework.

`audit_events`: BIGSERIAL id, `CREATED|UPDATED`, entity type/key, nullable actor,
timestamp, `before_data`/`after_data`, metadata. Entity id polymorphic không FK;
integrity do audit row và change commit cùng transaction. PartNumber entity id là
canonical PN. `actor_reference` là cột text cũ, nullable, được giữ cho lịch sử và không bao giờ backfill; enforce permission đã được triển khai cho Administration (Phase 14 slice 2) và Management (slice 3); cột này không còn được writer đã chuyển đổi nào ghi, nên là NULL trên mọi row Management và Administration mới, còn trên row cũ là identifier actor development/system được cấu hình tường minh hoặc NULL. Không bảng user nào được tạo ở slice này. `actor_user_id` — FK nullable → `users (id)` (`fk_audit_events_actor_user_id_users`, thêm bởi `0029_phase14_sign_in`) — là User đã sign in, **do server suy ra chỉ từ session principal và không bao giờ từ request body**; Phase 14 slice 1 ghi nó cho các write mật khẩu và các write user sign-in policy, và từ Phase 14 slice 2 mọi write cấu hình Administration cũng ghi nó (Departments, Areas, Operations, Scan Stations, định dạng Asset Tag, Workers, các section policy, Roles và Users); và từ Phase 14 slice 3 mọi write Management cũng ghi nó (Machines kể cả lifecycle event, Planned Routes, Part Numbers, Work Orders và demand, Hot list, Management allocation và reversal, cùng các row completion và Hot-removal của Work Order do các write đó gây ra); row do Scan Station command ghi (receipt, station allocation và Hot removal của chúng, tạo Part Number và Work Order ở intake) giữ `actor_user_id` NULL. Row cũ giữ `actor_user_id` NULL. `machine_lifecycle_events` và `work_order_allocations` có thêm FK `actor_user_id` nullable tương tự (`fk_machine_lifecycle_events_actor_user_id_users`, `fk_work_order_allocations_actor_user_id_users`); các cột text cũ của chúng (`machine_lifecycle_events.actor`, trước đây lấy từ request body retire/reactivate Machine, field mà Phase 14 slice 3 đã bỏ) được giữ cho lịch sử, không còn được ghi và không bao giờ backfill. Management command lấy khóa `FOR KEY SHARE` trên row `users` của User thực hiện qua các FK này, tại lúc INSERT; khóa chỉ xung đột với việc đổi login name của chính User đó. Trong lúc đổi tên như vậy, Management command chờ **trong khi giữ advisory lock của nó** (PN lock của nó và, với allocation, Hot list change hoặc sửa quantity / xóa line đã xác nhận của Work Order, Hot list lock), nên các command xếp hàng trên các lock đó (mọi station allocation, receipt và reversal của PN đó, Hot list change, Work Order save lấy Hot lock) chờ đến khi việc đổi tên commit; thời gian chờ bị chặn bởi transaction đổi tên ngắn và không có cycle. Scan Station command không bao giờ đọc hay khóa `users`, `roles`, `role_permissions`, `user_credentials` hay `user_sessions`. Ánh xạ event của PN master từ Phase 13 slice 7: tạo → `CREATED`, sửa → `UPDATED`, đổi ảnh → `UPDATED` (digest), hard delete → `DELETED` (`before_data` = snapshot cộng digest ảnh); snapshot `PartNumber` là `{part_number, name, current_revision, erp_id}`.

Mọi audited write phải có audit row cùng transaction. Audit immutable qua revoke +
trigger; creation có before NULL, update append row mới, không rewrite row cũ.

**Thay đổi priority của Hot list (Phase 12).** Thay đổi priority được audit bằng row `UPDATED` trên `WorkOrderDemand`, một row cho mỗi demand đổi `priority_rank` (gồm cả demand được đánh số lại để lấp chỗ hở), ghi cùng transaction với rank: `before_data = {"priority_rank": old}`, `after_data = {"priority_rank": new}` (NULL nghĩa là ngoài list), và `metadata.hot_list_change` giữ `device_event_id`, `action`, request `fingerprint`, `sequence` của row trong command và một **identity snapshot** lấy lúc chạy command (`work_order_demand_id`, `part_number`, `work_order_id`, `work_order_number`). Snapshot cho phép replay của command dựng lại `changes` chỉ từ audit row, kể cả khi demand đã bị xóa. Audit row cũng là idempotency record của command: khác cơ chế dựa trên UNIQUE của §14, lookup được làm race-free bằng Hot advisory lock. Thay đổi rank thực hiện ngoài command ghi cùng loại row: `metadata.hot_list_change` khi đó mang `action` `AUTO_REMOVE` (một allocation hoặc một Work Order save hạ quantity làm demand inactive) hoặc `LINE_DELETE` (việc xóa Hot line đã confirm), `sequence`, identity snapshot và một block `cause` (`trigger`, `reference`, và list `removed` kèm từng `reason`), và **không** có `device_event_id` hay `fingerprint` ở level đó, nên idempotency lookup của command không bao giờ thấy chúng. Không thêm audit vocabulary. Ghi rank trên demand của Work Order completed là ngoại lệ được chấp nhận và có tài liệu so với việc Work Order đó read-only (IMPLEMENTATION_ROADMAP Phase 12, OD1): ghi priority không phải sửa Work Order. Có hai writer làm việc đó — automatic removal bên trong allocation làm completed, và REMOVE / MOVE của manager trên entry tồn đọng từ trước thay đổi qua command.

**Cấu hình Worker và vocabulary được mở rộng (Phase 13, `0014_phase13_workers`).** Vocabulary `event_type` mở rộng thêm `DELETED` (hard delete record master/configuration; writer đầu tiên là hard delete Part Number của Phase 13 slice 7) và vocabulary `entity_type` thêm `Worker` (cấu hình audit identity của Scan Station, không bao giờ là hoạt động production). Mỗi write Worker có hiệu lực append đúng một row trong cùng transaction với write; `entity_id` là internal id của Worker dạng text, `actor_reference` NULL và `metadata` NULL. Row profile snapshot `{name, badge_barcode, is_active}` (`before_data` NULL với `CREATED`); row avatar snapshot `{"avatar": null | {content_type, byte_size, sha256}}` — digest, không bao giờ là byte ảnh. Row Worker được lock trước, nên trong từng facet (profile, avatar) `before_data` của một row là `after_data` của row liền trước; hai facet đan xen và không nối chuỗi với nhau. Write bị từ chối và no-op không append gì.

**Cấu hình môi trường (Phase 13, `0016_phase13_environment_audit`).** Các write môi trường Phase 3.5 được audit với năm giá trị `entity_type` bổ sung: `Department`, `Area`, `Operation`, `ScanStation` và `MachineAssetTagConfig` (định dạng Asset Tag). `entity_id` là internal id dạng text cho Department, Area và Operation, Station ID cho `ScanStation`, và `'1'` cho singleton `MachineAssetTagConfig`. Mỗi create hoặc update có hiệu lực append đúng một row `CREATED` hoặc `UPDATED` trong cùng transaction (một PATCH nhiều field là một row; no-op, write bị từ chối hoặc race thua không append gì). Snapshot là danh sách field tường minh: Department `{name, is_active, board_seconds_per_row, board_min_page_seconds}` (hai key cuối từ Phase 13 slice 9); Area `{department_id, name, barcode_value, description, color, icon_url, is_terminal, is_active, worker_identification_mode, fixed_worker_id}` (hai key cuối từ Phase 13 slice 3); Operation `{area_id, code, name, description, default_expected_duration_seconds, is_external, is_active}` (thời lượng tính bằng giây dạng JSON number, `null` khi chưa đặt); ScanStation `{area_id, is_active}`; MachineAssetTagConfig `{prefix, digits}` — `next_sequence` (bộ đếm never-reuse của việc tạo Machine) không phải cấu hình và không bao giờ được audit, và cột slice sau thêm chỉ được audit khi slice đó thêm nó vào snapshot. Mỗi update khóa row của mình trước, theo mode mà UPDATE của chính nó dùng, và snapshot dưới lock, nên các row liên tiếp của một entity nối chuỗi: mỗi `before_data` bằng `after_data` liền trước trên mọi key có ở cả hai. Không backfill: row audit đầu tiên của cấu hình có trước revision là `UPDATED` có `before_data` giữ trạng thái tìm thấy. `actor_reference` và `metadata` NULL cho đến Phase 14. Theme preference của Scan Station (`scan_stations.theme_preference`, `0026_phase13_station_theme`) là display preference, không phải cấu hình: không bao giờ được audit (OD-13) và không vào snapshot `ScanStation`.

**Cấu hình Machine (Phase 13, `0017_phase13_machine_audit`).** Các write cấu hình Machine được audit với giá trị `entity_type` bổ sung `Machine`; `entity_id` là id nội bộ của Machine dạng text. Mỗi create, sửa metadata hoặc maintenance context, maintenance start hoặc clear có hiệu lực append đúng một row `CREATED` hoặc `UPDATED` trong cùng transaction. Snapshot là danh sách key tường minh `{area_id, name, asset_tag, description, manufacturer, model, serial_number, installed_on, notes, maintenance_since, maintenance_note, maintenance_expected_return}` (ngày dạng text ISO-8601; `maintenance_since` là instant UTC ISO-8601, để text không bao giờ phụ thuộc time zone của connection). Bị loại: `id` (chính là `entity_id`), `retired_on` (do `machine_lifecycle_events` sở hữu, nên không có chuyển trạng thái nào bị ghi hai lần), `state_changed_at` (tuổi trạng thái runtime do production command dịch chuyển), `created_at` và `updated_at`. Retirement và reactivation vẫn chỉ được ghi trong `machine_lifecycle_events`: bản thuần không append row audit, còn bản Save draft mà retirement áp dụng, hoặc việc đổi tên, đổi Area hay clear maintenance của reactivation, append một row `UPDATED` có `metadata` là `{"machine_lifecycle_event_id": <id>}`. Mọi write Machine khóa row Machine trước (`FOR NO KEY UPDATE`; retirement giữ `FOR UPDATE`; việc tạo không khóa gì trên `machines`), nên các row liên tiếp của một Machine nối chuỗi như với các entity môi trường. Không backfill và `actor_reference` NULL cho đến Phase 14.

**Application policy (Phase 13, `0020_phase13_worker_sessions`).** Các write policy toàn cục được audit với giá trị `entity_type` bổ sung `ApplicationPolicy`; `entity_id` là section Administration, `worker-sessions` cho Worker session timeout, `correction-permissions` cho Undo reason policy `due-soon` cho panel Due Soon warning của Settings và `data-retention` cho History archival & purge và `sign-in` cho panel Settings → User sign-in (Phase 14 slice 1). Mỗi `PUT /api/policies/worker-sessions` có hiệu lực append đúng một row `UPDATED` trong cùng transaction (PUT không đổi gì không append gì) với snapshot `{worker_session_timeout_minutes, badge_confirm_done, badge_confirm_queue, badge_confirm_undo}` (ba option do `0021_phase13_badge_confirmation` thêm vào); mỗi `PUT /api/policies/correction-permissions` có hiệu lực cũng append đúng một row `UPDATED` theo cách đó, với snapshot `{undo_reason_required}` (`0022_phase13_undo_reason_policy`); mỗi `PUT /api/policies/due-soon` có hiệu lực cũng append đúng một row `UPDATED` theo cách đó, với snapshot `{due_soon_min_days, due_soon_lead_time_percent, due_soon_max_days}` (`0025_phase13_display_settings`); mỗi `PUT /api/policies/data-retention` có hiệu lực (đặt hoặc xóa period) cũng append đúng một row `UPDATED` theo cách đó, với snapshot `{retention_period_months}` (`0027_phase13_retention_period`). Mỗi `PUT /api/policies/sign-in` có hiệu lực append đúng một row `UPDATED` theo cùng cách, với snapshot `{user_session_expires, user_session_days, sign_in_lockout_attempts, sign_in_lockout_minutes, require_password_change}` (`0029_phase14_sign_in`) và `actor_user_id` là User đã sign in. Các write mật khẩu append row `UPDATED` cho entity type `User` với snapshot `{password_set, password_temporary, password_changed_at}` (`password_changed_at` là text ISO-8601; không bao giờ có hash, salt, token hay mật khẩu) và khóa metadata `password_change` (`SET_BY_ADMINISTRATOR`, `CHANGED_BY_USER` hoặc `RECOVERY_CLI`; lệnh recovery còn ghi `source: cli`); Administrator đầu tiên do first-run setup tạo append row `User` `CREATED` và row `UPDATED` của mật khẩu với `source: first-run-setup` và `actor_user_id` NULL. Sign-in, sign-out, lần thử sai, khóa và việc kết thúc session được log, không bao giờ audit. Snapshot Area cũng mang `worker_session_timeout_minutes` (override theo Area, `null` khi chưa đặt). Row Worker Session và việc đóng chúng — kể cả các gate sign-in — là production audit identity (PROFILE §9; `worker_sessions`, `scan_session_id`), không bao giờ là row `audit_events`.

**Planned Routes (Phase 13, `0024_phase13_planned_routes`).** Các write cấu hình Planned Routes được audit với giá trị `entity_type` bổ sung `RouteTemplate`; `entity_id` là id nội bộ của template dưới dạng text. Mỗi write hiệu lực append đúng một row trong cùng transaction: `CREATED` (tạo), `UPDATED` (edit hoặc archive) và `DELETED` (xóa template chưa từng dùng). Snapshot là danh sách key tường minh `{name, description, archived_at, steps}` với `archived_at` là text ISO-8601 UTC và mỗi step `{sequence, area_id, operation_id, expected_duration_seconds, preferred_machine_id, instructions}` (duration tính bằng giây, số JSON); write no-op hoặc bị từ chối không append gì. Snapshot Assigned Route và release không bao giờ được audit như `RouteTemplate`; chỉ adjustment của một snapshot được audit, như `AssignedRoute` (bên dưới).

**Users và roles (Phase 13, `0028_phase13_users_roles`).** Cấu hình Users và roles được audit với các giá trị `entity_type` bổ sung `User` và `Role`; `entity_id` là id nội bộ dạng text và `actor_reference` vẫn NULL còn `actor_user_id` mang User đã sign-in (Phase 14 slice 2). Mỗi write hiệu lực append đúng một row trong cùng transaction: tạo role là `CREATED` với `before_data` NULL và `after_data` `{name, permissions}` (các permission key sắp theo giá trị); đổi tên role hoặc một delta grant / revoke là một row `UPDATED` với cùng snapshot trước và sau; tạo user là `CREATED` với profile snapshot `{login_name, display_name, role_id, is_active}`; sửa profile user là một row `UPDATED` với snapshot đó trước và sau; đổi avatar của user là row `UPDATED` với `{"avatar": null | digest}` — một digest, không bao giờ là byte ảnh, như với Worker. Mọi write role và user trước hết lấy advisory lock `partflow:user-administration` rồi đọc lại permission của user thực hiện; permission-management guard và số holder là lượt đọc thường dưới lock đó (Phase 14 slice 2). Row role hoặc user được lock tiếp theo, nên các row liên tiếp của một entity tạo thành chuỗi. Write cấu hình có audit lấy `FOR KEY SHARE` trên row `users` của user thực hiện qua foreign key `actor_user_id`: nó chỉ chờ sau việc đổi login name của user đó và không tạo chu trình với các lock ở trên. Command production không bao giờ đọc hay lock `users`, `roles` hoặc `role_permissions`. Ba role được seed không có row `CREATED` (`before_data` của lần sửa đầu là seed). Preference theme được lưu của User (`users.theme_preference`) không có writer ở Phase 13 nên không có row audit nào cho nó. No-op hay write bị từ chối không append gì.

**Thiết bị Scan Station (Phase 14 slice 4, `0030_phase14_station_devices`).** Enroll thiết bị station được audit với giá trị `entity_type` bổ sung `ScanStationDevice`; `entity_id` là id nội bộ của thiết bị dạng text. Phát hành enrollment code append row `CREATED` mang `actor_user_id` của User phát hành; việc kích hoạt của chính station append row `UPDATED` không có User (`actor_user_id` NULL, `metadata.source = "station-activation"`), và kích hoạt một thiết bị thay thế thiết bị khác append thêm một row `UPDATED` cho thiết bị bị thay (`metadata.replaced_by_device_id`); revoke append row `UPDATED` mang User revoke. Code, token hay digest không bao giờ được audit, và `last_seen_at` là metadata liên hệ của thiết bị, không audit. Command của Scan Station đọc các grant của role áp dụng tại Scan Station bằng plain read và không bao giờ lock `users`, `roles` hay `role_permissions`.

**Assigned Route adjustment (Phase 14 slice 6, `0032_phase14_route_adjusted`).** Adjustment được cấp quyền của future step của một flow `PLANNED` được audit với event type `ROUTE_ADJUSTED` và giá trị `entity_type` bổ sung `AssignedRoute`; `entity_id` là `assigned_routes.id` dạng text. Mỗi adjustment đã commit append đúng một row trong cùng transaction; refusal hoặc idempotent replay không append gì. `before_data` và `after_data` là `{steps: [{id, sequence, area_id, operation_id, expected_duration_seconds, preferred_machine_id, instructions}]}` cho cả route, gồm cả past step, kèm step id (Movement và metadata deviation gọi step bằng id nên audit phải resolve được; các step mà adjustment xóa chỉ còn tồn tại ở đây); `metadata.route_adjustment` là `{device_event_id, fingerprint, quantity_flow_id, part_number, reason, kept_through_sequence}`; `actor_user_id` mang User đã sign-in. Thứ tự lock là Part Number advisory lock, rồi row `quantity_flows` `FOR UPDATE`, rồi Machine, Area và Operation `FOR KEY SHARE` theo thứ tự tăng, rồi DELETE và INSERT step cùng INSERT audit. Không ghi cột `quantity_flows`, Movement, Route Template hay snapshot khác.

**Allocation row là audit record của chính nó (Phase 14 slice 5).** Correction beyond-demand ghi một row `work_order_allocations` append-only (`exceeds_demand`, reason bắt buộc, `actor_user_id`, và các con số trước / requested trong command metadata) và không ghi row `audit_events`; các lần gỡ khỏi Hot list mà nó kích hoạt vẫn được audit như trước.

---

## 17. Database constraint và index

**`departments`** — PK `id`, unique name, active flag, timestamps. `board_seconds_per_row integer NOT NULL DEFAULT 3` với `ck_departments_board_seconds_per_row_range` (1–60) và `board_min_page_seconds integer NOT NULL DEFAULT 6` với `ck_departments_board_min_page_seconds_range` (1–300) (`0025_phase13_display_settings`) là Production Board rotation timing tính bằng số giây nguyên, cấu hình theo Department.

**`areas`** — PK, Department FK, required name, unique barcode nếu có, active,
timestamps. `is_terminal` và `worker_identification_mode` đến phase dùng; không có
`machine_assignment_mode` vì behavior derive từ Machines. `0019_phase13_worker_identity` thêm `worker_identification_mode text NOT NULL DEFAULT 'DISABLED'` (`ck_areas_worker_identification_mode`: `DISABLED`, `FIXED` hoặc `SCANNED`) và `fixed_worker_id` (FK nullable `fk_areas_fixed_worker_id_workers`; `ck_areas_fixed_worker_shape`: Fixed Worker tồn tại đúng khi mode là `FIXED`), cùng các FK nullable `part_movements.worker_id` và `work_order_allocations.allocated_by_worker_id`, mỗi cái có CHECK rằng Worker đòi hỏi `station_id` (`ck_part_movements_worker_requires_station`, `ck_work_order_allocations_worker_requires_station`); không index, không backfill, downgrade từ chối. `0020_phase13_worker_sessions` thêm override nullable theo Area `worker_session_timeout_minutes integer` (`ck_areas_worker_session_timeout_range`: NULL hoặc 1–720).

**`operations`** — PK, Area FK, code, `UNIQUE (area_id, code)`, name/active/time.

**`part_numbers`** — PK natural `part_number text`, CHECK:

```text
part_number = upper(part_number COLLATE "C")
AND part_number COLLATE "C" !~ '[[:space:]]'
AND part_number <> ''
```

(Cả hai vế chỉ ASCII và độc lập với libc của OS kể từ `0018_phase13_pn_check_collation`; việc uppercase Unicode đầy đủ và từ chối whitespace do domain normalization sở hữu, §6.)

Không surrogate id, active flag, stored barcode hoặc lowercase index; production
không FK. Barcode derive và master delete/recreate được. Từ `0023_phase13_part_number_master` bảng còn có các chi tiết nullable `name`, `current_revision` và `erp_id` (free text, không unique, không index) và ảnh nằm trên row — `image` (`bytea`), `image_type`, `image_updated_at` — với `ck_part_numbers_image_shape` (cả ba hoặc không cột nào), `ck_part_numbers_image_type` (`image/png`, `image/jpeg`, `image/webp`) và `ck_part_numbers_image_size` (từ 1 byte đến 2 MiB); vẫn không có FK từ bảng production.

**`work_orders`** — internal PK; nullable number với partial unique non-null;
required received date; nullable due; status/time. `completed_at` không thuộc slice,
đến Phase 10 và có partial `(completed_at,id)` index cho completed history.

**`work_order_demands`** — WorkOrder FK; canonical PN by value/no master FK;
Request Type CHECK; positive requested; non-negative allocated default 0; nullable
due; `priority_rank` nullable với `CHECK (priority_rank IS NULL OR priority_rank >= 1)` (`ck_work_order_demands_priority_rank_positive`) và `UNIQUE (priority_rank)` không deferrable (`uq_work_order_demands_priority_rank` — NULL vẫn phân biệt; Hot command và automatic / line-deletion removal đều ghi bằng hai flush, xóa rồi gán, nên không cái nào tạo trùng tạm thời; Phase 12, migration `0013_phase12_priority`, có pre-check từ chối để database không đổi khi các rank hiện có không đúng là `1..N`); Job Numbers `text[]` default empty; optional context fields/time;
indexes WorkOrder và PN. Không Job aggregate/GIN index trong slice.

**`route_templates`** — name, optional description, nullable `archived_at`;
không version column. Used template archive, never-used delete.

**`route_steps`** — Template FK, sequence unique per template, Area, optional
Operation/duration/instruction. `preferred_machine_id` (PROJECT_PROFILE §8.9) không tạo trong slice này; `0024_phase13_planned_routes` (Phase 13 slice 8) tạo nó dưới dạng FK nullable `fk_route_steps_preferred_machine_id_machines` tới `machines.id` (NO ACTION; Machine được retire, không bao giờ xóa). Việc khớp với Area của step là Application rule chỉ ở thời điểm save, nên preference đã lưu có thể cũ đi và khi đó hiển thị `(unavailable)`.

**`assigned_routes`** — PK, optional source template (có index `ix_assigned_routes_source_route_template_id` từ `0024_phase13_planned_routes`, phục vụ usage list của Planned Routes, kiểm tra ever-used và kiểm tra FK khi xóa template), snapshot time; không reverse
Flow reference.

**`assigned_route_steps`** — AssignedRoute FK, unique sequence, Area, optional
Operation/duration/instruction. `0032_phase14_route_adjusted` làm past step bất biến ngay trong database: trigger mức statement `trg_assigned_route_steps_forbid_update` (function `partflow_assigned_route_steps_forbid_update`) từ chối mọi UPDATE, và step mà một Movement tham chiếu không bao giờ bị xóa (foreign key `part_movements.assigned_route_step_id` là `NO ACTION`); DELETE vẫn được phép cho các future step chưa được tham chiếu mà adjustment thay.
`0024_phase13_planned_routes` thêm `preferred_machine_id` nullable — integer thường **không có foreign key**: giá trị được copy từ cột template đã qua kiểm tra FK (hoặc từ snapshot khác), Machine không bao giờ bị xóa, và một FK sẽ khiến release, receipt, split và merge khóa row Machine sau khi đã khóa Area hoặc Machine, ngược thứ tự production.

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
FK hay stored counter. UPDATE/DELETE bị guard. `0032_phase14_route_adjusted` thêm `ix_part_movements_assigned_route_step_id` trên `(assigned_route_step_id)`: nó phục vụ kiểm tra của foreign key khi một adjustment xóa future step chưa được tham chiếu và ranh giới "step này có được tham chiếu không" của việc sửa.

**`audit_events`** — BIGSERIAL, constrained event/entity types (`0014_phase13_workers` mở rộng `event_type` thành `('CREATED','UPDATED','DELETED')` và thêm `'Worker'` vào `entity_type`; `0016_phase13_environment_audit` thêm `'Department'`, `'Area'`, `'Operation'`, `'ScanStation'`, `'MachineAssetTagConfig'`; `0017_phase13_machine_audit` thêm `'Machine'`; `0020_phase13_worker_sessions` thêm `'ApplicationPolicy'`; `0024_phase13_planned_routes` thêm `'RouteTemplate'`; `0028_phase13_users_roles` thêm `'User'` và `'Role'`; `0032_phase14_route_adjusted` thêm event type `'ROUTE_ADJUSTED'` và entity type `'AssignedRoute'`), polymorphic id,
actor/time/before/after/metadata, `(entity_type,entity_id,id)` index, append-only; Phase 12 thêm partial expression index `ix_audit_events_hot_list_device_event_id` trên `(metadata['hot_list_change'] ->> 'device_event_id') WHERE entity_type = 'WorkOrderDemand'` cho idempotency lookup của Hot command (dạng lưu là operator `->>` tường minh trên JSONB subscript, và Application lookup phát ra cùng expression); Phase 14 slice 6 thêm index expression UNIQUE partial `uq_audit_events_route_adjustment_device_event_id` trên `(metadata['route_adjustment'] ->> 'device_event_id') WHERE entity_type = 'AssignedRoute'` (dạng JSONB subscript, tạo bằng raw SQL để expression lưu đúng là expression Application phát ra), giúp idempotency của adjustment race-free.

**`application_policy`** (`0020_phase13_worker_sessions`) — singleton có kiểu duy nhất cho policy toàn cục: PK `id` với `ck_application_policy_singleton` (`id = 1`), được migration seed nên row luôn tồn tại; `worker_session_timeout_minutes integer NOT NULL DEFAULT 15` với `ck_application_policy_worker_session_timeout_range` (1–720); `created_at`, `updated_at`. `badge_confirm_done`, `badge_confirm_queue` và `badge_confirm_undo` (`0021_phase13_badge_confirmation`) là `boolean NOT NULL DEFAULT true`, mỗi cái cho một sensitive action, quyết định form của final gate của nó ở Area Scanned-session. `undo_reason_required` (`0022_phase13_undo_reason_policy`) là `boolean NOT NULL DEFAULT false`: khi true, Undo command từ chối reversal không có reason. `due_soon_min_days` (default 2, `ck_application_policy_due_soon_min_days_range`, 0–365), `due_soon_lead_time_percent` (default 15, `ck_application_policy_due_soon_lead_time_percent_range`, 1–100) và `due_soon_max_days` (default 7, `ck_application_policy_due_soon_max_days_range`, 0–365), cùng `ck_application_policy_due_soon_window_order` (`due_soon_min_days <= due_soon_max_days`) (`0025_phase13_display_settings`), là `integer NOT NULL` và giữ policy Due Soon warning toàn cục duy nhất. `retention_period_months integer NULL` với `ck_application_policy_retention_period_range` (NULL hoặc 12–1200) (`0027_phase13_retention_period`) giữ retention period của Movement history theo số tháng nguyên, NULL nghĩa là không có retention period; đây là policy column duy nhất không có server default (PROJECT_PROFILE §28 cấm số retention hard-code), được lưu cho archival maintenance của Phase 16 và không production path nào đọc. Policy sau này được thêm thành column có kiểu (kèm server default khi có default chuẩn); không có key/value store.

**`worker_sessions`** (`0020_phase13_worker_sessions`) — `ScanSession` của PROFILE: PK `id bigint` identity (API không bao giờ lộ); `station_id` FK → `scan_stations (station_id)`; `area_id` FK → `areas (id)` (Area của station lúc đăng nhập); `worker_id` FK → `workers (id)`; `started_at`, `expires_at timestamptz NOT NULL`; `ended_at timestamptz` và `end_reason text` nullable. CHECK: `end_reason IN ('SWITCHED','EXPIRED','AREA_MODE_CHANGED','STATION_CHANGED','WORKER_DEACTIVATED')`; `(ended_at IS NULL) = (end_reason IS NULL)`; `expires_at > started_at`; `ended_at` NULL hoặc trong `[started_at, expires_at]`; `end_reason = 'EXPIRED'` chỉ với `ended_at = expires_at`. `UNIQUE (id, worker_id, station_id)` là đích của FK ghép. Index: partial UNIQUE `uq_worker_sessions_open_station` trên `(station_id) WHERE ended_at IS NULL` (tối đa một session mở mỗi station) và `ix_worker_sessions_open_worker` trên `(worker_id) WHERE ended_at IS NULL`. Trigger mutation guard cấm DELETE và TRUNCATE và chỉ cho UPDATE đổi `expires_at`, `ended_at` và `end_reason` của row đang mở. FK giữ `NO ACTION` (station, Area và Worker không bao giờ bị xóa).

**`part_movements.scan_session_id`** (`0020_phase13_worker_sessions`) — `bigint` nullable (lịch sử giữ NULL, không backfill); `ck_part_movements_session_requires_worker` (`scan_session_id IS NULL OR worker_id IS NOT NULL`) và FK ghép `fk_part_movements_scan_session_worker_sessions` `(scan_session_id, worker_id, station_id)` → `worker_sessions (id, worker_id, station_id)`, nên một Movement không thể nêu session của Worker hay station khác. Không index (không reader nào lọc theo nó). Upgrade validate CHECK và FK bằng một lần quét `part_movements`; downgrade từ chối khi còn bất kỳ session, override Area hoặc timeout khác default.

**`roles`** (`0028_phase13_users_roles`) — role có tên, sửa được: PK `id` (identity); `name text NOT NULL` với UNIQUE `uq_roles_name` thường (phân biệt hoa/thường, trên tên đã trim); `created_at`, `updated_at`. Không có active flag và không delete: role được đổi tên, không bao giờ bị xóa. Migration seed `Administrator`, `Manager` và `Operator` với đúng các grant PROJECT_PROFILE §20 liệt kê (17, 10 và 10 permission key); chúng là row bình thường và không có gì được gắn với tên của chúng.

**`role_permissions`** (`0028_phase13_users_roles`) — một row cho mỗi grant: PK `pk_role_permissions` `(role_id, permission)`; `role_id` FK `fk_role_permissions_role_id_roles` → `roles (id)`; `permission text NOT NULL` với `ck_role_permissions_permission_known`, chấp nhận đúng vocabulary 35 key (một key cho mỗi capability của PROJECT_PROFILE §20: `Permission` trong `app/domain/enums.py`, lặp lại dưới dạng literal trong migration và được schema test ghim bằng nhau). Capability về sau nới CHECK; key không bao giờ bị đổi tên.

**`users`** (`0028_phase13_users_roles`) — application account, không bao giờ là Worker: PK `id` (identity); `login_name text NOT NULL` với UNIQUE `uq_users_login_name` (trên cả active và inactive) và `ck_users_login_name_canonical` (`login_name COLLATE "C" ~ '^[a-z0-9._@+-]{1,128}$'`, nên giá trị lưu là dạng trimmed, lowercase ASCII mà Application tạo ra); `display_name text NOT NULL`; `role_id integer NOT NULL` FK `fk_users_role_id_roles` → `roles (id)` (đúng một role); avatar tùy chọn `avatar_image`, `avatar_image_type`, `avatar_image_updated_at` dưới `ck_users_avatar_image_shape`, `ck_users_avatar_image_type` và `ck_users_avatar_image_size` (quy tắc avatar của `workers`: có đủ hoặc không có gì, PNG / JPEG / WebP, 1 byte đến 2 MiB); `theme_preference text` nullable với `ck_users_theme_preference` (`DARK` hoặc `LIGHT`; NULL = không có preference), chỉ được lưu — không có writer hay reader ở Phase 13; `is_active boolean NOT NULL DEFAULT true`; `created_at`, `updated_at`. Không có column credential và không có foreign key giữa `users` và `workers` theo cả hai hướng. Downgrade của `0028_phase13_users_roles` từ chối khi còn bất kỳ row `users` hay row audit `User` / `Role` nào.

**Sign-in (Phase 14 slice 1, `0029_phase14_sign_in`).** `application_policy` có thêm `user_session_expires boolean NOT NULL DEFAULT true`, `user_session_days integer NOT NULL DEFAULT 30`, `sign_in_lockout_attempts integer NOT NULL DEFAULT 10`, `sign_in_lockout_minutes integer NOT NULL DEFAULT 15` và `require_password_change boolean NOT NULL DEFAULT true` cùng `ck_application_policy_user_session_days_range` (1–365), `ck_application_policy_sign_in_lockout_attempts_range` (3–100) và `ck_application_policy_sign_in_lockout_minutes_range` (1–1440). **`user_credentials`** — PK `pk_user_credentials` `user_id` (FK `fk_user_credentials_user_id_users` → `users (id)`: mỗi User một credential); `password_hash text NOT NULL` (scrypt, `ck_user_credentials_password_hash_format`: bắt đầu bằng `scrypt$`); `password_is_temporary boolean NOT NULL`; `password_changed_at timestamptz NOT NULL`; `failed_attempts integer NOT NULL DEFAULT 0` (`ck_user_credentials_failed_attempts_non_negative`); `locked_until timestamptz` nullable; `created_at`. Credential là bảng riêng, không bao giờ là column của `users`, nên response serialize một user không thể mang nó. **`user_sessions`** — PK `pk_user_sessions` `id bigint` identity; `user_id` FK `fk_user_sessions_user_id_users`; `token_digest bytea NOT NULL` (digest SHA-256 của token ngẫu nhiên; token không bao giờ được lưu) với UNIQUE `uq_user_sessions_token_digest` và `ck_user_sessions_token_digest_length` (32 byte); `created_at`; `ended_at` / `end_reason` nullable cùng nhau (`ck_user_sessions_end_shape`) với `ck_user_sessions_end_reason` (`SIGNED_OUT`, `REPLACED`, `PASSWORD_CHANGED`, `PASSWORD_RESET`, `USER_DEACTIVATED`); row đã kết thúc được giữ; `ix_user_sessions_user_id_open` index các session đang mở của một User. `audit_events`, `machine_lifecycle_events` và `work_order_allocations` mỗi bảng có thêm `actor_user_id` nullable với FK tên `fk_<table>_actor_user_id_users` (§16). Downgrade của `0029_phase14_sign_in` từ chối khi còn bất kỳ credential, session, giá trị `actor_user_id` hay row audit policy `sign-in` nào, hoặc khi bất kỳ column sign-in policy nào khác default.

**Thiết bị Scan Station (Phase 14 slice 4, `0030_phase14_station_devices`).** **`scan_station_devices`** — PK `pk_scan_station_devices` `id` (identity); `station_id text NOT NULL` FK `fk_scan_station_devices_station_id_scan_stations` → `scan_stations (station_id)`; `label text NOT NULL` (đã trim, 1–80 ký tự, một rule của Application); `enrollment_code_digest bytea` nullable với UNIQUE `uq_scan_station_devices_enrollment_code_digest` và `ck_scan_station_devices_enrollment_code_digest_length` (`octet_length = 32`); `enrollment_expires_at timestamptz NOT NULL`; `token_digest bytea` nullable với UNIQUE `uq_scan_station_devices_token_digest` và `ck_scan_station_devices_token_digest_length` (`octet_length = 32`); `replaces_device_id integer` nullable FK `fk_scan_station_devices_replaces_device_id_scan_station_devices` → `scan_station_devices (id)`; `issued_at timestamptz NOT NULL DEFAULT now()`; `activated_at`, `last_seen_at`, `revoked_at` timestamptz nullable; `revoked_reason text` nullable. Chỉ lưu digest SHA-256. Row đang chờ giữ digest của code và kích hoạt xóa nó đồng thời đặt digest của token, nên hai thứ không bao giờ cùng tồn tại (`ck_scan_station_devices_code_or_token`) và code đã dùng không thể khớp lại; `ck_scan_station_devices_activation_shape` (có token digest khi và chỉ khi có `activated_at`), `ck_scan_station_devices_revocation_shape` (`revoked_at` khi và chỉ khi có `revoked_reason`), `ck_scan_station_devices_revoked_reason` (`REVOKED`, `REPLACED`), `ck_scan_station_devices_replaced_shape` (`REPLACED` chỉ cho thiết bị đã kích hoạt), `ck_scan_station_devices_last_seen_shape` và `ck_scan_station_devices_no_self_replace`. Application không bao giờ xóa row và không có index phụ. `application_policy` có thêm `scan_station_role_id integer NOT NULL` với `fk_application_policy_scan_station_role_id_roles` → `roles (id)`: role áp dụng tại Scan Station, migration đặt một lần thành role Operator seed (nơi duy nhất resolve một role theo tên; upgrade bị từ chối khi không role nào tên Operator) và không API nào ghi. `ck_audit_events_entity_type` được mở rộng với `ScanStationDevice`. Downgrade của `0030_phase14_station_devices` từ chối khi còn bất kỳ row `scan_station_devices` hay row audit `ScanStationDevice` nào; nếu không thì nó khôi phục CHECK entity-type trước đó và bỏ pointer cùng bảng.

**Correction beyond-demand (Phase 14 slice 5, `0031_phase14_beyond_demand`).** `work_order_allocations` thêm `exceeds_demand boolean NOT NULL DEFAULT false` (mọi row lịch sử giữ `false`) và `ck_work_order_allocations_exceeds_demand_shape` (`NOT exceeds_demand OR (source = 'MANAGEMENT' AND allocation_reason IS NOT NULL AND reverses_allocation_id IS NULL AND station_id IS NULL AND allocated_by_worker_id IS NULL AND actor_user_id IS NOT NULL)`: correction là row Management có reason, không bao giờ là reversal, không bao giờ gán cho Scan Station hay Worker, và luôn được ghi kèm User đã sign-in). Downgrade từ chối khi còn bất kỳ row correction nào; nếu không thì nó bỏ CHECK và cột.

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
| Undo/Repair/Scrap/Adjustment | Phase 9 — implemented, `0010` | reason/reversal columns, types/status; complete-command reversal; addition tạo Flow mới; Undo reason policy (`application_policy.undo_reason_required`, Phase 13 `0022_phase13_undo_reason_policy`) bắt buộc `reason` trên row `REVERSED` khi đang bật — do Undo command enforce, không bao giờ bằng CHECK (phụ thuộc cấu hình) |
| Stockroom/Allocation | Phase 10 — implemented, `0011` | STOCKED/closed flow, completed projection, append-only allocation/reversal; không FK Movement/Flow; Phase 14 slice 5 thêm `exceeds_demand` (`0031_phase14_beyond_demand`) |
| Monitoring read models | Phase 11 | Movement-derived query |
| Priority/Hot UI | Phase 12 — implemented, `0013_phase12_priority` | không thêm column: `priority_rank` hiện có nhận CHECK dương và UNIQUE (dense `1..N`, §5/§17) sau pre-check từ chối, cùng audit expression index (§17); writer là Hot command và automatic / line-deletion removal (`hot_ranks`), cả hai audit qua `audit_events` (§16) |
| Full Administration | Phase 13 | master tables đã có từ Phase 3.5; Department display settings và Due Soon policy đã triển khai (`0025_phase13_display_settings`); setting retention period của Movement history đã triển khai (`0027_phase13_retention_period`) — việc thực thi vẫn ở Phase 16 |
| Quản lý metadata PartNumber, ảnh, hard delete | Phase 13 — implemented (`0023_phase13_part_number_master`) | column nullable trên `part_numbers`; không chạm bảng production nào |
| Theme preference của Scan Station (station tier) | Phase 13 — implemented (`0026_phase13_station_theme`) | một column nullable `scan_stations.theme_preference` có CHECK (`DARK` / `LIGHT`); không audit |
| Quản lý Planned Routes | Phase 13 — implemented (`0024_phase13_planned_routes`) | hai column nullable `preferred_machine_id` (FK trên `route_steps`, không FK trên `assigned_route_steps`), một index, vocabulary audit `RouteTemplate`; không backfill |
| AssignedRoute adjustment (`ROUTE_ADJUSTED`) | Phase 14 — implemented (`0032_phase14_route_adjusted`) | mở rộng vocabulary audit, index idempotency UNIQUE partial, index `part_movements.assigned_route_step_id` và trigger cấm UPDATE trên `assigned_route_steps`; không có Movement type |
| Authentication/role | Cấu hình (users, role, permission) **implemented** ở Phase 13 (`0028_phase13_users_roles`); sign-in, session và cột actor **implemented** ở Phase 14 slice 1 (`0029_phase14_sign_in`); enforce permission trên đọc và write Administration **implemented** ở Phase 14 slice 2 và trên đọc và write Management **implemented** ở Phase 14 slice 3; enroll thiết bị Scan Station và permission của station **implemented** ở Phase 14 slice 4 (`0030_phase14_station_devices`) | `actor_user_id` nằm cạnh cột text cũ `actor_reference`, không bao giờ backfill (§16); không couple Movement |
| File Work Order import | Phase 15 | reuse validation idempotently |
| Worker/ScanSession persistence | Phase 13 — Workers registry **implemented** (`0014_phase13_workers`); `worker_id`, `allocated_by_worker_id`, `areas.worker_identification_mode` và `areas.fixed_worker_id` **implemented** (`0019_phase13_worker_identity`); `scan_session_id`, `worker_sessions`, `application_policy` và override theo Area **implemented** (`0020_phase13_worker_sessions`); badge-confirmation option **implemented** (`0021_phase13_badge_confirmation`) | bảng `workers` (badge UNIQUE trên dạng chuẩn hóa, avatar trên row) và vocabulary audit mở rộng (§16) đã có; `0019_phase13_worker_identity` thêm `worker_id`, `allocated_by_worker_id`, `areas.worker_identification_mode` và `areas.fixed_worker_id` (§11, §17); `0020_phase13_worker_sessions` thêm `scan_session_id`, `worker_sessions`, `application_policy` và `areas.worker_session_timeout_minutes` (§11, §17); `0021_phase13_badge_confirmation` thêm ba column option của `application_policy` (§17) |
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
