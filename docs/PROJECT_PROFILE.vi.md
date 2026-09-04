# Hồ sơ dự án PartFlow v22

> **Bản gốc chuẩn:** [`PROJECT_PROFILE.md`](PROJECT_PROFILE.md).
> Baseline upstream: commit `4fd635f2020189fa279adc6988268c36c39d595b`.
> File tiếng Anh là nguồn chuẩn cho hành vi domain và định hướng sản phẩm; nếu
> hai bản khác nhau, phải sửa bản EN trước rồi đồng bộ lại bản VI.
>
> **Trạng thái:** Living Document
> **Thẩm quyền:** Đặc tả chuẩn về domain behavior và product direction của PartFlow.

---

# 1. Tổng quan dự án

## Mục đích

PartFlow là hệ thống theo dõi nội bộ trên xưởng, dùng để theo dõi Part Number
(PN) và số lượng vật lý khi chúng di chuyển qua các Area sản xuất.

Hệ thống phải luôn trả lời được:

- PN nào đang được xử lý;
- Area nào đang giữ quantity của PN đó và bao nhiêu chiếc ở mỗi Area;
- Operation nào đang thực hiện và Machine nào đang xử lý, nếu có;
- Work Order nào yêu cầu PN, số lượng từng Work Order và phần việc còn lại;
- route dự kiến của từng active quantity, các Area/Machine đã đi qua;
- quantity yêu cầu đã hoàn tất và được stock chưa;
- stocked quantity đã allocate cho Work Order nào.

PartFlow cố ý là công cụ production tracking gọn nhẹ. Nó không phải ERP, MES,
inventory management, production scheduler, accounting hay machine-control.
ERP integration có thể bổ sung sau, nhưng PartFlow phải hoạt động độc lập khi
không kết nối ERP.

Triển khai đầu tiên nhắm tới Department `Machine Shop`; kiến trúc phải mở rộng
được sang Department khác mà không thay core tracking model.

---

# 2. Mục tiêu thiết kế

Thiết kế phải phản ánh vận hành xưởng thực tế, ưu tiên theo thứ tự:

1. Độ chính xác theo dõi.
2. Đơn giản khi vận hành.
3. Workflow barcode nhanh.
4. Movement history đầy đủ, bất biến.
5. Ít thao tác nhất cho operator.
6. Quantity integrity đáng tin cậy.
7. Production visibility rõ ràng.
8. Dễ bảo trì lâu dài.

Khi có đánh đổi, usability trên xưởng được ưu tiên miễn là tracking accuracy và
data integrity vẫn được giữ.

---

# 3. Nguyên tắc thiết kế

## Scanner First

- Barcode là tương tác production chính; keyboard-wedge scanner không cần driver.
- Workflow bình thường không cần chuột; touch tối ưu cho tablet/fixed workstation.
- Scan input tự lấy lại focus sau mỗi action; luôn có manual-entry fallback.

## Part Number-Centric

PN là identity được theo dõi chính. Không dùng `Work Order + PN`, physical batch
hay từng piece làm tracked object chính:

- PN nhận diện part design dùng lại;
- physical quantity biểu diễn production state;
- Work Order Demand biểu diễn business demand;
- Part Movement biểu diễn production activity;
- Work Order Allocation chỉ nối stocked quantity với từng Demand sau completion.

## Quantity-Based Tracking

PartFlow theo dõi quantity, không serialize từng piece. Một PN có thể đồng thời
ở nhiều Area, nhiều Machine, vừa queued vừa processing và theo các route khác
nhau. Không bao giờ giả định một PN chỉ có một location hay một processing path.

## Production-Oriented

Operator không bị ép làm bước hành chính không tương ứng với công việc vật lý.
Production data phải mô tả thực tế.

## Production Reflects Reality

Route, plan và expected duration chỉ hướng dẫn. Movement history thật là chuẩn.
Khi lệch route, hệ thống cảnh báo, yêu cầu xác nhận nếu mơ hồ, ghi lại deviation
được phép, nhưng không từ chối hoạt động hợp lệ chỉ vì khác kế hoạch. Thực tế ưu
tiên hơn plan cũ.

## History Is Immutable

Mọi movement có ý nghĩa đều được ghi. Correction tạo compensating/reversal/
adjustment event; workflow bình thường không sửa hoặc xóa Movement lịch sử.

## ERP Independent

PartFlow chạy được không cần ERP. Dữ liệu ERP có thể nhập tay, import file hoặc
sync sau qua boundary riêng; business rule không phụ thuộc API/response ERP.

## Practical Before Perfect

Hệ thống phải xử lý exception hợp lệ an toàn. Khi không đủ chắc chắn về intent,
phải hỏi xác nhận thay vì đoán. Input unknown/ambiguous không được update data.

---

# 4. Bối cảnh vận hành

Mỗi thiết kế sản xuất dùng lại có một PN duy nhất và một folder bản vẽ vật lý:

- folder thuộc PN, không thuộc riêng Work Order;
- dùng lại cho mọi Work Order/production run của PN;
- có một PN barcode duy nhất;
- có thể chứa bản vẽ revision hiện tại.

Không dán nhãn từng part vì raw material, machining có thể phá nhãn, thao tác
không thực tế và nhân lực không hỗ trợ piece tracking. Vì vậy PartFlow theo dõi
quantity gắn với PN.

## Shared Drawing Folder

Folder chủ yếu phục vụ setup/reference. Sau setup, quantity có thể di chuyển độc
lập, folder có thể được Area khác dùng và tách khỏi một hoặc mọi active quantity.
Vị trí folder không phải production position. Không được suy ra rằng folder và
parts luôn đi cùng nhau, scan folder là di chuyển toàn bộ quantity, hay chỉ một
Area được làm PN tại một thời điểm. Barcode chỉ nhận diện PN; app resolve quantity
và context.

---

# 5. Quy tắc theo dõi cốt lõi

PN string là stable production identity. PN:

- unique trong ERP;
- case-insensitive, lưu UPPERCASE, không whitespace; ngoài ra là opaque string;
- có reusable folder và một PartFlow barcode;
- có thể được nhiều active Work Order yêu cầu và có nhiều Job Number ngoài;
- có quantity ở nhiều Area/Machine và nhiều route đồng thời;
- phải trace được vĩnh viễn sau lần ghi đầu: Demand, Flow, Movement, Allocation và
  history tự giữ canonical PN, không phụ thuộc PartNumber master.

Không theo dõi serial piece, physical batch như first-class identity hoặc
`Work Order + PN` làm movement identity. Movement ở mức PN + quantity; Demand và
Allocation tách riêng; đổi Allocation không viết lại Movement.

---

# 6. Invariant bắt buộc

1. PN đã ghi không được mất khả năng truy vết.
2. Movement history bất biến.
3. Current state được derive từ Movement history khi thực tế cho phép.
4. Một PN có thể có quantity ở nhiều Area và nhiều Machine.
5. Quantity không được vô tình tạo, hủy, nhân đôi, mất hoặc âm.
6. Movement không consume quá available source quantity.
7. Scan unknown/invalid bị từ chối; ambiguity không write trước khi xác nhận.
8. Work Order Demand tách khỏi shop-floor Movement.
9. Work Order Allocation tách khỏi shop-floor Movement và có thể đổi không sửa
   Movement.
10. Route hướng dẫn nhưng không thay Movement history.
11. Correction giữ event gốc; scan update atomic.
12. MVP hoạt động không cần ERP.

---

# 7. Từ vựng chuẩn

Các tên sau phải dùng nhất quán trong docs, code, API, database và UI.

## Part Number

Identifier ERP dùng lại cho part design; viết tắt `PN`. PN string là stable domain
identity, không có surrogate `part_number_id` trong production data.

Canonical form:

- identity không phân biệt hoa/thường; trim đầu/cuối rồi lưu/so sánh/hiển thị
  UPPERCASE (`abc-123` → `ABC-123`);
- sau trim phải không rỗng và không có bất kỳ internal whitespace; input sai bị
  reject, không tự xóa whitespace bên trong;
- ngoài hai rule này, PN là opaque string, không parse segment hay áp format.

`PartNumber` master chỉ là optional current metadata. Production/historical truth
không phụ thuộc record đó.

## Work Order

Container sản xuất có nguồn ngoài, chứa một hoặc nhiều PN/quantity yêu cầu. Có thể
viết `WO` trong label ngắn; code dùng đầy đủ `WorkOrder`, `WorkOrderDemand`,
`WorkOrderAllocation`.

Work Order Number là opaque string, không format/pad/normalize sau khi nhập và có
thể `NULL`. Khi user để trống và xác nhận, lưu `work_order_number = NULL` trên
internal Work Order, hiển thị `—` (chỉ presentation, không persist), có thể thêm
số thật sau bằng audited edit. Không tạo temporary WO number. Nhiều Work Order
có thể cùng null; uniqueness chỉ áp dụng số khác null. Internal database key
không bao giờ là user-facing identifier.

Work Order Number và external Job Number là hai identifier tách biệt. Job Number
chỉ dùng display/search/sort/report, không làm identity/workflow key.

## Work Order Demand

Quantity của một PN được một Work Order yêu cầu. Nó chứa requested/allocated/
remaining, due date, priority, Request Type, Job Numbers và context kinh doanh;
không xác định current location.

## Request Type

Giá trị ban đầu:

- `NEW` — production được yêu cầu từ bên ngoài;
- `MODIFY` — đưa physical quantity vào để modification, có thể không WO number,
  route hoặc active Demand; hệ thống vẫn tạo minimum internal records.

`REPAIR` không phải Request Type. Repair là explicit transfer intent
(`movement_reason = REPAIR`) cho quantity đã trong production quay lại Area đã
đi qua, không tạo quantity hay Demand mới.

## Department

Đơn vị tổ chức lớn, ví dụ Machine Shop, Purchasing, Assembly, Production,
Stockroom, Quality Control. Release đầu nhắm Machine Shop.

## Area

Địa điểm vật lý nhận, queue, xử lý, transfer, outsource hoặc stock quantity. Area
có stable identity; name/description/color/icon có thể đổi không làm đổi history.

## Operation

Công việc thực hiện trong Area, ví dụ Cutting, Turning, Milling, Deburring,
Plating, Painting, Testing, Receiving. Một Area có một hoặc nhiều Operation;
Operation mô tả công việc, Area mô tả nơi vật lý.

## Machine

Resource vật lý tùy chọn trong đúng một Area. Mỗi Machine có Asset Tag bắt buộc,
tự cấp và bất biến; barcode derive `PF:MACHINE:<asset-tag>`. Machine là current
executor/location khi quantity được assign.

Machine từng được history tham chiếu không hard-delete mà retire. Retired Machine
không nhận scan/assignment nhưng vẫn dùng cho history/report. Có thể reactivate
chính **cùng physical machine trên cùng record**, giữ identity/barcode/asset/history,
ghi lifecycle event `RETIRED`/`REACTIVATED`. Physical machine khác luôn là record
mới. Display name unique giữa active Machines của một Area nhưng có thể reuse theo
thời gian; replacement = retire cũ + create mới với Asset Tag mới.

## Quantity Flow (khái niệm nội bộ)

Phần quantity có thể truy vết khi move/split/merge/queue/assign. Không phải batch
được dán nhãn. Nó bảo toàn quantity distribution, route assignment, lineage và
Area/Machine progression. Các flow cùng PN có thể theo route khác.

## Route và Route Mode

Mỗi Flow có:

- `FLOATING` (mặc định): không sequence bắt buộc; trace derive từ Movement,
  giữ repeated Area và các flow sau split đi độc lập; không AssignedRoute.
- `PLANNED`: có AssignedRoute snapshot copy từ RouteTemplate làm guidance.

Planned Route có Area, Operation, expected duration, instruction, preferred
Machine. Movement thật luôn authoritative; không tạo mutable route history thứ hai.

## Part Movement

Event bất biến ghi việc move, assign, complete, correct, split hoặc merge quantity.

## Area Completion (DONE)

Completion của **selected physical quantity** ở current Area:

- UI action/status: `DONE`;
- Movement type: `AREA_COMPLETED`;
- derived holding state: `READY_TO_TRANSFER`.

DONE nghĩa là xử lý tại Area đã xong và quantity chờ transfer trên finished rack.
Machine được clear, Area vẫn là location. Nó không có nghĩa PN/WO/manufacturing
complete, stocked, QC-approved hay allocated. Chỉ `STOCKED` ở terminal Area mới
biểu diễn manufacturing completion. DONE luôn quantity-scoped; không có
`PartNumber.status = DONE`.

## Current Position

Current Area và optional Machine được derive cho một QuantityFlow.

## Work Order Allocation

Gán stocked PN quantity cho Demand cụ thể; độc lập Part Movement.

## Priority

Rank do Manager đặt trên WorkOrderDemand; tiêu chí cao nhất khi order work và
allocation.

## Hot Part

Nhãn UI/business cho Demand cần expedite; không thay `priority_rank`.

## Worker

Operator thực hiện/xác nhận activity, chỉ dùng accountability/reporting, không
quyết định business correctness. Worker là audit identity tại Scan Station; User
là application account cho Management/Administration. Badge không login và
Worker Session không phải authentication session.

---

# 8. Domain model

## 8.1 PartNumber

Optional current metadata cho canonical PN:

```text
PN string         = stable production identity
PartNumber master = optional/current metadata của PN
```

Thuộc tính minh họa: `part_number`, `name`, `description`, `image_url`,
`current_revision`, `erp_id`, `created_at`, `updated_at`.

Rules:

- natural key là canonical uppercase PN; một master record mỗi canonical PN;
- create-on-first-valid-use, không cần catalog preload hay ERP call trong MVP;
- Demand/Flow/Movement/Allocation tự giữ PN, lookup master chỉ enrich metadata;
- giữ nguyên opaque PN, kể cả chuỗi nhiều đoạn như `0455-20-0118-03`;
- name/description là free text, có thể dài; revision chỉ informational;
- barcode là `PF:PN:<part-number>` và dùng lại cho mọi Work Order;
- authorized role quản lý tại Management → Part Numbers và có thể hard-delete
  **chỉ metadata**; không cascade, history vẫn hiển thị PN; có thể tạo lại sau.

## 8.2 WorkOrder

Thuộc tính minh họa: `id`, `work_order_number`, `received_date`, `due_date`,
`status`, `completed_at`, `erp_id`, timestamps.

- Number là opaque nullable string; null hiển thị `—`, multiple null được phép,
  non-null unique và có thể audited-edit từ null.
- `received_date` bắt buộc, mặc định current date; `due_date` có thể null.
- Chứa ít nhất một Demand.
- Complete khi mọi Demand fully allocated; status derive từ allocations.
- `completed_at` là timestamp allocation hoàn tất line cuối, phục vụ server-side
  sort/filter/keyset pagination; không nhập tay.
- Reversal allocation có thể reopen Work Order và clear `completed_at`; history
  không bị xóa.
- Completed Work Order rời active view nhưng ở read-only unbounded history;
  uniqueness của WO Number bao trùm cả history.

## 8.3 WorkOrderDemand

Thuộc tính minh họa: `id`, `work_order_id`, `part_number`, `request_type`,
`requested_quantity`, `allocated_quantity`, `due_date`, `priority_rank`,
`job_numbers`, `requester`, `reason`, `notes`, timestamps.

- Một PN có nhiều active Demand; quantity là physical pieces.
- `due_date` nullable và không block save; undated demand xếp sau dated demand.
- PN không sở hữu due date.
- Admin/Manager được edit. Sau release chỉ còn restricted edit: requested quantity
  không thấp hơn released/allocated, due date và Job Numbers; không remove.
- Priority thuộc Demand; Demand không sở hữu Movement/location.
- Allocation có thể audited-adjust bởi Admin/Manager.

## 8.4 Area

Thuộc tính: `id`, `department_id`, `barcode_value`, display fields, `is_terminal`,
`is_active`, `worker_identification_mode`, timestamps.

Identity/barcode stable; display name đổi được. Area có 0..N Machine và 1..N
Operation. Stockroom thường terminal. Area không Machine xử lý trực tiếp; có ít
nhất một Machine luôn `QUEUE_AND_ASSIGN`; không có per-Area assignment mode và
không auto-assign khi chỉ một Machine.

## 8.5 Operation

Thuộc tính: `id`, `area_id`, `code`, `name`, `description`,
`default_expected_duration`, `is_external`, `is_active`. Không cần barcode nếu
resolve rõ từ Area; nhiều Operation thì phải resolve/confirm.

## 8.6 Machine

Thuộc tính: `id`, `area_id`, `name`, immutable auto-assigned `asset_tag`, derived
`barcode_value`, optional asset metadata, maintenance override, derived
`state_changed_at`, `retired_on`.

Asset Tag:

- tự cấp, không nhập/sửa tay; format đơn giản prefix + zero-padded sequence, ví dụ
  `CD-0001`, cấu hình tại Administration → Barcode configuration;
- unique, không reuse, không đổi; format mới chỉ áp dụng machine tạo sau;
- là human-readable physical identity, khác database `id`, đồng thời là barcode.

Lifecycle/behavior:

- Active Machine thuộc một Area cố định; chuyển capacity sang Area khác là retire
  + new record. Ngoại lệ: reactivate cùng máy đã di chuyển khi retired, chỉ có tác
  dụng về sau.
- Active display name unique trong Area; reuse theo thời gian được phép.
- State derive: Maintenance override > Running nếu có assigned active quantity >
  Idle. UI derive elapsed time từ `state_changed_at`; không lưu formatted duration.
- Maintenance không move/release/complete quantity; note/expected-return có thể sửa
  mà không đổi start time. Clear maintenance trả về Running/Idle theo quantity.
- Retire bị block khi còn assigned quantity; retired record không nhận scan/assign.
- Reactivate RETIRED→ACTIVE chỉ cùng physical machine/same record, reason bắt buộc,
  có thể chọn Area mới về sau, return Idle. Block nếu identity/serial bị reissue,
  target Area inactive hoặc name collision.
- Retire/reactivate ghi append-only lifecycle event gồm actor, time, reason,
  before/after và Area change.
- Replacement luôn record và Asset Tag mới; không mutate record cũ.

## 8.7 QuantityFlow

Thuộc tính: `id`, `part_number`, `quantity`, `status`, `route_mode`, optional
`assigned_route_id`, lineage/parent, timestamps.

Là cấu trúc nội bộ; split/merge phải giữ full history và conservation. Floating
không có AssignedRoute; current position derive từ Movement; không phơi complexity
không cần thiết cho operator.

## 8.8 RouteTemplate

Reusable route definition, tên UI **Planned Routes**. Có `id`, `name`,
`description`, `archived_at` và ordered RouteStep.

Template edit chỉ ảnh hưởng assignment tương lai; active flow giữ snapshot. Template
chưa từng dùng có thể delete; đã dùng phải archive, vẫn xuất hiện trong historical
context và không được chọn mới. Không có template-versioning system riêng.

## 8.9 RouteStep

Thuộc tính: `id`, `route_template_id`, `sequence`, `area_id`, `operation_id`,
`expected_duration`, `instructions`, `preferred_machine_id`. Duration chỉ advisory,
dùng overdue/bottleneck/queue/processing/estimate và không block production.

## 8.10 AssignedRoute

Snapshot optional chỉ cho `PLANNED` Flow. Copy từ template rồi độc lập; template
edit không đổi active work. Authorized user có thể audited-edit selected Flow;
flows cùng PN có route khác; deviation được ghi, Movement luôn authoritative.

## 8.11 PartMovement

Thuộc tính minh họa: ids, canonical PN, flow, quantity, `movement_type`, source/
destination Area/Machine, Operation/RouteStep, Worker/Station/ScanSession,
timestamps, `movement_reason`, `reason`, `reverses_movement_id`,
`device_event_id`, metadata.

Type ban đầu:

- `RECEIVED`, `TRANSFERRED`, `ASSIGNED_TO_MACHINE`, `RELEASED_FROM_MACHINE`;
- `AREA_COMPLETED`, `SPLIT`, `MERGED`, `STOCKED`;
- `QUANTITY_ADJUSTED`, `SCRAPPED`, `ROUTE_ADJUSTED`;
- `ROUTE_DEVIATION_CONFIRMED`, `REVERSED`.

`movement_reason = REPAIR` là explicit transfer intent, reason text bắt buộc.
`QUANTITY_ADJUSTED` direction `INCREASE` thêm physical quantity có audit nhưng
không đổi requested quantity. `SCRAPPED` loại quantity hỏng khỏi active production.

`AREA_COMPLETED` đưa selected quantity sang READY_TO_TRANSFER, clear Machine nhưng
giữ Area; khác `RELEASED_FROM_MACHINE` (unfinished → queue) và `STOCKED` (terminal
manufacturing completion). Transfer quantity còn processing ghi atomically
`AREA_COMPLETED` rồi `TRANSFERRED`; quantity đã READY chỉ ghi `TRANSFERRED`. Undo
đảo toàn command.

Movement tự giữ PN, không gắn Allocation, không overwrite và phải đủ để rebuild
current state.

## 8.12 WorkOrderAllocation

Thuộc tính: `id`, canonical `part_number`, `work_order_demand_id`, `quantity`,
`allocated_at`, actor, reason, override flag, `reverses_allocation_id`.

Chỉ allocate từ stock; không sửa Movement; mọi adjustment audit được. Active
allocation không vượt available stocked quantity và không vượt Demand remaining
trừ correction được authorize rõ ràng.

## 8.13 Worker

Scan-Station audit identity, không phải account. Fields: stable `id`, `name`,
unique existing-company `badge_barcode`, `avatar`, `is_active`; không có employee
number. Mode theo Area: disabled, fixed Worker, hoặc scanned Worker Session với
sliding inactivity timeout. Worker không điều khiển business rule.

---

# 9. Khái niệm application

## ScanSession

Temporary Application state có thể giữ Area, active Worker và expiration. Không
có persistent Machine Session hay persistent PN intent: mọi action là one-shot
dialog; cancel/complete xóa temporary context. Chỉ Worker Session được giữ.
ScanSession không bao giờ là source of truth.

---

# 10. Mô hình barcode

Barcode PartFlow xác định entity type một cách deterministic:

```text
PF:PN:<part-number>
PF:MACHINE:<asset-tag>
PF:AREA:<stable-id>
PF:SCRAP
```

`PF:` phân biệt barcode của PartFlow với vendor/factory barcode. Worker badge là
ngoại lệ: dùng chính barcode hiện có trên thẻ nhân viên, exact-match với một active
Worker; zero/multiple match bị reject. Format `PF:WORKER:` cũ không còn tồn tại.

Machine barcode chứa immutable Asset Tag. PN barcode chứa canonical PN. Parser:

1. Trim terminator/surrounding whitespace, không xóa ký tự bên trong.
2. Scan PN phải có exact prefix `PF:PN:`.
3. Suffix phải không rỗng và không internal whitespace.
4. Canonicalize UPPERCASE.
5. Không parse segment/format.
6. Không yêu cầu PN preload; create master on first valid use, không gọi ERP.

Không có Action barcode. Modify/Repair/Scrap/addition được chọn qua one-shot
dialog. `PF:SCRAP` chỉ hợp lệ bên trong Scrap workflow, mỗi scan tăng counter;
scan ở main input bị reject.

Barcode unique, không phụ thuộc display name; raw ERP PN không tự được coi là
PartFlow barcode; inactive entity không nhận update. PN barcode **không chứa**
WO, Demand, quantity, Job Number, Route, Area hoặc Machine; app resolve context.

## Barcode Resolution

Operator không chọn scan mode. Một valid context có thể đi tiếp; nhiều context
thì app phải hiển thị relevant choices, không đoán. Ambiguity gồm nhiều source
Flow/Area, nhiều Operation, normal transfer so với explicit Repair, nhiều blank
MODIFY Work Order, hoặc unexpected Planned destination. Không write trước khi
resolve.

---

# 11. Mô hình quantity

Quantity luôn là số physical parts. Một PN có thể phân bố:

```text
PN ABC

Material        5
Cut             4
Lathe 1         3
Lathe 2         2
Mill            6
```

Conservation phải reconcile:

```text
introduced quantity = active quantity + stocked quantity + scrapped quantity
```

Introduced gồm production release, Modify intake và explicit increase. Scrap
không giảm requested Demand.

## Quantity Splitting

```text
Material: 10
```

thành:

```text
Cut: 4
Lathe: 6
```

Split tạo Flow riêng, giữ PN/history/conservation và không đổi Demand.

## Quantity Merging

```text
Lathe 1: 2
Lathe 2: 4
```

thành:

```text
Mill: 6
```

Merge giữ lineage để biết quantity đã đến thế nào.

## Quantity Integrity

Không được vô tình create/destroy/duplicate/lose quantity, tạo quantity âm, move/
stock vượt source, hoặc allocate quá stock. Correction chủ ý phải là explicit
auditable event, không rewrite history.

## Scrap

Quantity hỏng ghi `SCRAPPED` qua “Scrap damaged quantity”:

- vào từ PN action dialog;
- scan `PF:SCRAP` để tăng pending count, chưa write khi đang đếm;
- có thể sửa/reset count, reason chung bắt buộc;
- final summary gồm PN, Area/Machine, available/scrap/remaining, Worker, Station,
  reason; Cancel zero write;
- Confirm tạo đúng một auditable `SCRAPPED` operation cho total.

Scrap quantity và history phải hiện ở các operational/Tracking surface.

---

# 12. Quy tắc ownership khi xử lý

## Area không có Machine

Area zero Machine không có queue: nhận quantity là trực tiếp processing, ghi
Operation và Machine null. UI không hiển thị placeholder Machine/queue statistics.
Nhiều Operation thì phải resolve/confirm.

## Area có Machine — `QUEUE_AND_ASSIGN`

Area có một hay nhiều Machine luôn cùng behavior:

- arrival vào Area queue, không auto-assign;
- one-shot `Assign to Machine` bằng scan Machine shortcut hoặc PN-first;
- một Machine giữ nhiều PN;
- queued row có `ASSIGN`, assigned row có `QUEUE` ghi
  `RELEASED_FROM_MACHINE` để trả unfinished quantity về queue.

Machine là executor đến khi DONE/implicit completion, transfer, release to queue,
reversal hoặc explicit reassignment.

## Trạng thái xử lý của Area

Area có Machine:

```text
QUEUED
  -> ASSIGNED_TO_MACHINE
ON_MACHINE
  -> AREA_COMPLETED
READY_TO_TRANSFER
  -> TRANSFERRED
next Area queue hoặc direct processing
```

Area không Machine:

```text
PROCESSING
  -> AREA_COMPLETED
READY_TO_TRANSFER
  -> TRANSFERRED
next Area queue hoặc direct processing
```

`DONE` hoàn tất selected quantity; `QUEUE` trả unfinished quantity — không gộp.
Workflow chọn PN/Area/Machine/quantity, preview, zero write trước final confirmation,
ghi `AREA_COMPLETED`, giữ Area và clear Machine. Partial DONE split khi cần; không
đánh dấu mọi quantity của PN. Finished quantity nằm trong Area summary, Machine
card chỉ chứa actively assigned quantity; finished không phải stocked.

---

# 13. Nhập Work Order

Nguồn có thể là manual, file import hoặc ERP sync tương lai. Với Work Order mới:

1. Create/locate Work Order.
2. Create/update Demand cho từng PN.
3. Locate reusable folder; create PN metadata nếu mới.
4. Lưu business Demand; không tạo production quantity.
5. Khi explicit release, confirm quantity, Route Mode (Floating default), starting
   Area/Operation.
6. Create QuantityFlow; snapshot AssignedRoute chỉ khi Planned.
7. Append `RECEIVED` và update projection atomically.

Terminal Area không bao giờ là starting Area.

**Partial/repeated release:** Demand 50 có thể release 20, 12, 18; mỗi release tạo
Flow riêng và không auto-merge. Released quantity là tổng release Movement;
remaining là hard cap. Vượt cap hoặc remaining zero thì zero write.

**Restricted edit sau release:**

- requested quantity không thấp hơn `max(released, allocated)`; tăng quantity làm
  xuất hiện remaining và đưa WO về Open; giảm đúng committed quantity là hợp lệ;
- due date và Job Numbers sửa được;
- PN không sửa; Request Type, requester, reason, notes cố định sau release;
- removal bị từ chối; edit không rewrite Flow/Movement/release history;
- enforcement ở write layer và serialize với concurrent release.

Nếu PN đã có active quantity, hiển thị distribution và hỏi explicit intent; không
tự tạo quantity hay merge. Demand không định nghĩa current position.

## Mapping từ source system

- Source WO Number → `WorkOrder.work_order_number`.
- Source Job Number → informational metadata trên relevant Demand.
- Source component PN → canonical PN/PartNumber.
- Source component row → candidate Demand.
- Source quantity → candidate requested quantity chỉ khi là production demand mà
  PartFlow quản lý.
- Source Revision → informational revision riêng của PN.

Parent/build-for assembly ở header vẫn là context trừ khi workflow được duyệt tạo
Demand/Flow riêng. Không import toàn BOM. Các field Type/Ref Designator/Attribute/
Issued/Shelf/Cost/Open-Closed không tự có PartFlow meaning; source Type không phải
Request Type, Issued không phải Movement, Shelf không phải Area, Cost out of scope.
Chỉ có một Work Order aggregate.

## Xóa Work Order Demand

- Draft chưa save: remove ngay.
- Saved line chưa release: remove sau explicit confirmation.
- Đã release bất kỳ quantity: không delete; correction dùng workflow khác.

Removal không được delete PartNumber master, Flow, Movement, release history hay
Demand khác cùng PN.

---

# 14. Modify Intake và Repair

## Modify Intake

`MODIFY` đưa physical quantity vào cho modification và vẫn tạo minimum records:
blank-number internal WorkOrder, một Demand, Flow và immutable Movement. UI gọi
workflow là **Receive Quantity**, final action **Confirm receipt**; từ “intake”
chỉ dùng nội bộ.

Ở đây một Work Order Demand là **active** theo business shortage còn lại:
`requested_quantity > allocated_quantity`. Released quantity không quyết định
điều đó — line release hết nhưng chưa allocate vẫn là active demand — và
`completed_at` là trạng thái tổng của cả Work Order (§8.2), không bao giờ là
trạng thái của một line: line đã allocate hết là inactive kể cả khi Work Order
còn open vì các line khác. Khi PN còn active demand, quantity của nó thuộc về
explicit production release từ Management (§13) và Scan Station không bao giờ
receive nó.

1. Scan/enter PN.
2. Confirm/change Request Type; `MODIFY` là default, không forced.
3. Confirm/change Route Mode; `FLOATING` default.
4. Confirm quantity, optional due date, reason/notes.
5. Confirm Area/Operation; `received_date` mặc định là scan timestamp: chính
   instant lúc scan PN mở workflow, giữ nguyên qua mọi bước của wizard và đọc
   thành calendar date theo lịch của site (`SITE_TIMEZONE`, §8.2). SCAN quyết
   định ngày của receipt, không phải confirmation — receipt chuẩn bị trước nửa
   đêm và confirm sau nửa đêm vẫn thuộc ngày đã scan — và scan instant là một
   phần của confirmed intent nên retry ghi đúng ngày đó.
6. Khi PN đã có active quantity, explicit confirm rằng quantity nhận được được
   ghi thành một Quantity Flow **riêng** (xem bên dưới). Confirmation được đưa
   ra dựa trên existing active distribution của PN, và không có nó thì không
   ghi gì cả.

Transaction create/reuse PartNumber, applicable blank MODIFY Work Order, Demand,
Flow, initial Movement/current position; quantity vào queue hoặc direct processing.

Reuse Work Order không được đoán: cùng PN có đúng một blank-number MODIFY Work
Order clearly applicable — không external number, chưa completed, và đã có sẵn
`MODIFY` demand line cho PN đó — thì reuse; nhiều record plausible thì bắt buộc
explicit selection, không bao giờ lấy first match.

Reuse giữ nguyên rule một canonical PN tối đa một line trên một Work Order:
line sẵn có của PN được **nâng** thêm received quantity — restricted edit của
§13, trong đó nâng requested quantity của line đã release luôn hợp lệ — thay vì
Work Order có thêm line thứ hai cho cùng PN, và receipt release đúng phần tăng
đó. Chỉ sửa những gì restricted edit cho phép: requested quantity và, khi
operator nhập, due date. Request Type, requester, reason và notes của line có
sẵn không bao giờ bị station receipt ghi đè — reason của receipt đi theo
immutable Movement của nó. Receipt `NEW` không bao giờ reuse (reuse chỉ định
nghĩa cho blank-number MODIFY Work Order) và luôn tạo internal Work Order riêng.

Receipt đã confirm không được undo tại Scan Station: reversal của §16 khôi phục
production state và không bao giờ ghi lại business demand mà receipt đã tạo hoặc
nâng, nên receipt sai được sửa qua production correction workflow chứ không bị
đảo một nửa.

**Quantity nhận được không bao giờ được join vào quantity đang có (v22 — đã
chốt; giải quyết open decision 3 cũ của §32):** một receipt luôn tạo QuantityFlow
RIÊNG của nó. Nếu PN đã có active quantity, operator phải **explicit confirm**
rằng quantity vừa nhận được ghi thành một Quantity Flow **riêng**; hệ thống
không bao giờ suy ra intent từ PN identity, và không ghi gì trước confirmation
đó. Quantity đang có không bao giờ bị receipt merge vào, mutate, thừa hưởng hay
ghi đè: quantity, route mode, Assigned Route, current position và processing
state của nó giữ nguyên, và không Movement nào được ghi lên nó. Confirmation
được đưa ra dựa trên existing active distribution của PN — quantity đang ở đâu
và bao nhiêu — và rule được xét lại authoritative tại write time, nên receipt mà
PN có active quantity xuất hiện giữa scan và confirmation bị từ chối, không ghi
gì, cho tới khi operator confirm dựa trên distribution hiện tại. Đây đúng là
explicit confirmation mà production release §13 vẫn dùng, vốn cũng luôn tạo flow
riêng và không bao giờ merge.

Các Quantity Flow về sau hoá ra thuộc về nhau được gom lại bằng workflow
**Combine quantities** đã có (`MERGED`, §11; GUI_DESIGN §4.7) — nơi duy nhất
quantity từng được merge, và chỉ cho các portion có production context giống hệt
nhau. Receipt không bao giờ là nơi đó. Quantity tìm thấy bên cạnh quantity đang
active trong Area vẫn là correction `QUANTITY_ADJUSTED · INCREASE` của §11, và
nó không join gì cả.

## Repair

```text
A → B → C → D → B
```

Repair trả một phần/toàn quantity hiện có về Area đã đi qua:

- không create quantity/Demand và không phải Request Type;
- partial Repair split Flow, full Repair move whole Flow;
- ghi `TRANSFERRED` + `movement_reason = REPAIR`, không aggregate riêng;
- quay lại Area cũ không tự suy ra Repair; user phải chọn “Return quantity for
  repair”, vì normal route cũng có thể lặp Area;
- thu source Flow/Area, destination, quantity, mandatory reason, PN, Worker,
  Station/time và final summary;
- actual Movement mở rộng Floating trace.

---

# 15. Core scan workflow

1. Station bound vào một Area.
2. Scan PN/Worker; Machine barcode là one-shot shortcut.
3. Detect type, validate entity/context.
4. Resolve PN/source Flow/Operation/Machine; nhiều source phải chọn đúng một.
5. Hỏi quantity khi cần (transfer/assignment default MAX; addition không default).
6. Hỏi confirmation cho ambiguity/deviation; mọi action có summary.
7. Record immutable Movement và derive state.
8. Refresh view; refocus barcode input; clear dialog context.

Transfer từ processing implicit-complete bằng atomic `AREA_COMPLETED` +
`TRANSFERRED`. Repair vẫn explicit. Action one-shot:

- Machine-first: scan Machine, chọn/scan PN, quantity MAX default, review/confirm;
- PN-first: mở intake, source-explicit transfer, hoặc valid-action dialog
  (assign, receive/add, direct DONE, Repair, Scrap); Machine row DONE riêng;
- Cancel luôn zero write và clear pending context.

Unknown/inactive/invalid relation/impossible quantity/unauthorized correction/
unconfirmed deviation bị reject zero write. Ambiguity thì đưa lựa chọn, không chỉ
reject và không guess. Retry cùng event id trả original idempotent result. Success
chỉ sau server-confirmed write; heartbeat không cho phép optimistic success.

## Persistence của Scan Station

Station identity và Area binding là stable application/infrastructure config;
bare route hiển thị selector, không auto-pick; unknown/inactive ID là explicit
error. Có thể dùng bảng `scan_stations`; config không phải core aggregate.
Movement ghi `station_id`; production state vẫn từ Movement, không từ Session.

---

# 16. Undo và correction

Undo không delete original Movement. Nó tạo compensating/reversal Movement,
reference original, restore derived state, ghi Worker/time và reason khi cấu hình.
Undo target recent eligible **completed PN operation**, luôn preview PN/action/
quantity/source/destination/Machine/Worker/time/effect. Sau Undo, last-scanned
context lùi tới eligible action trước; hết action thì disabled.

Worker của reversal theo Area mode. Final gate luôn tồn tại:

- scanned-session Area + UNDO badge-confirmation enabled (default): scan một active
  badge sau summary để confirm và xác định Worker;
- fixed/disabled hoặc option off: final warning question restate key facts.

Không record trước gate. Undo đảo toàn **application command**, ví dụ cả
`AREA_COMPLETED` + `TRANSFERRED`, không đảo một row tùy ý. Operator chỉ Undo recent
eligible action theo quyền; Manager/Admin có broader correction; tất cả auditable.

---

# 17. Production routing

Mỗi Flow là `FLOATING` default hoặc `PLANNED`.

## Floating Route (mặc định)

Không AssignedRoute. Trace derive từ immutable Movement, giữ repeated Area,
Repair, split flow độc lập; không duplicate PartMovement bằng mutable trace khác.

## Planned Route

Expected path để guidance/planning, không override Movement thật.

## Route Template

Tên UI Planned Route, sequence dùng lại. Template đã dùng archive, chưa dùng có
thể delete.

```text
Material
→ Cut
→ Lathe
→ Deburr
→ External
→ Stockroom
```

Route có thể lặp Area:

```text
Material
→ Mill
→ External
→ Mill
→ Stockroom
```

## Assigned Route

Snapshot khi `PLANNED` release. Flows cùng PN có thể khác route; split có thể
inherit/modify; template change không đổi snapshot; authorized edit chỉ selected
Flow và có audit; Movement luôn source of truth.

## Route Deviation

Chỉ áp dụng Planned. Khi tới unexpected Area: warn, confirm nếu cần, record actual
Movement + deviation, giữ previous route, update assigned route nếu authorized và
ghi actor/time/reason. Không ép reality theo plan lỗi thời.

## Expected Duration

Advisory cho days-left/total, overdue, queue/processing/bottleneck/estimate; không
block production.

---

# 18. Stockroom và completion allocation

Stockroom là terminal Area thông thường. Scan vào đó ghi `STOCKED`, quantity trở
thành manufacturing-complete và available cho Allocation; Movement và Allocation
vẫn tách.

## Thứ tự allocation

1. Highest Demand priority/Hot rank.
2. Cùng priority: dated demand trước, due sớm trước; undated sau mọi dated, rồi
   parent Work Order `received_date` cũ trước.
3. Tie dùng deterministic creation/id order.

Thứ tự này áp dụng ở suggestion, work ordering và demand-sorted display.

## Xác nhận nhận hàng

1. Scan PN.
2. Confirm completed quantity.
3. Review suggested Allocation.
4. Confirm.

Suggestion hiển thị WO, requested, previously allocated, remaining và proposed.
Routine receiving không cần Manager approval; operator được adjust trước confirm;
Admin/Manager được audited-adjust sau. Active allocation không vượt available
stock; portion được allocate phải reconcile.

## Hoàn tất Work Order

Complete khi mọi Demand fully allocated; rời active view nhưng nằm vĩnh viễn trong
History; Movement không đổi. Work sau này là Demand/internal demand mới, không
reopen historical Movement. Allocation reversal có thể đưa derived Work Order về
open như §8.2 mà vẫn giữ history.

---

# 19. Worker Session

Mode theo Area: none, fixed Worker, hoặc barcode session. Badge scan activate/
switch Worker ngay, không thay last-scanned-PN; session kết thúc khi switch,
sign-out hoặc expire.

Scanned session dùng **sliding inactivity timeout**, không shift boundary:

- Administration có default và per-Area override;
- valid resolved scan/confirmed production command/valid badge refresh timeout;
  invalid scan không refresh;
- expire chỉ block Scan Station bằng badge modal; view khác không ảnh hưởng;
- station mở không session cũng block trước production;
- dialog draft giữ nguyên khi expire, nhưng confirm bị block cho tới badge hợp lệ.

Badge confirmation cho DONE, QUEUE, Undo có option riêng, default enabled. Mọi
sensitive action luôn có final gate:

- scanned-session + option enabled: active Worker badge là bước cuối sau summary,
  vừa confirm/identify actor vừa switch/refresh session;
- fixed/disabled hoặc option off: final confirmation question, tone phù hợp;
- unknown/inactive badge zero write; cancel về summary; không write trước gate.

Worker chỉ là accountability metadata. Không có Machine Session. Active Worker
luôn visible ở Scan Station.

---

# 20. Role và permission

RBAC áp dụng. Machine, Planned Route và PartNumber metadata là production master
data trong Management, do authorized specialist quản lý; không bắt buộc Admin.

## Administrator

Quản lý Department/Area/Operation, Worker, User/role/permission, Station, barcode,
scan behavior, Worker session policy, correction permission, settings; có quyền
quản lý Machine/Route/PartNumber qua Management; edit Demand/Allocation, historical
correction và archival/purge maintenance.

## Manager

Xem current/history; create/edit Work Order và Demand; set/reorder priority; assign/
edit route; quantity correction; Allocation edit; resolve exception; export/report.

## Operator

Theo permission: scan PN/Machine/Worker, receive Area, assign Machine, confirm
quantity, complete vào Stockroom, review/adjust suggested allocation và Undo recent
eligible scan. Không trực tiếp rewrite history.

---

# 21. Các view ứng dụng

## Scan Station

Fixed interface bound một Area. Bare route là Station Selector; unknown/inactive
station explicit error; footer Station ID/mode/shortcut không interactive.

Phải có scanner-first one-screen flow, full-width focused input, auto-refocus,
immediate feedback, Department/Area/Operation, ONLINE state, Worker theo mode,
last scanned PN + right-edge Undo, quantity chỉ khi cần và shared Area/Machine
monitoring. Statistics:

- Area có Machine: Total PNs, Total pcs, Queued, On machines, Done, Hot; Total =
  Queued + On machines + Done.
- Area không Machine: Total PNs, Total pcs, Processing, Done, Hot; Total =
  Processing + Done.

Machine card chỉ actively assigned, có derived state/elapsed và maintenance info;
Area summary giữ queued/direct/finished. Row Machine có `DONE` và `QUEUE`. Không
có persistent Machine Session/armed context/Recent Scans.

## Production Board

Read-only Department-wide wall display, không có per-Area mode. Phải distance-
readable, auto paginate/rotate, dynamic rows, canonical priority/due sort, overdue
tone, Area color, distributed quantity/time, scrap, explicit Machine/finished
presentation, `—` cho null WO và single-line PN.

Rotation dwell theo rows trên page, default 3s/row, min 6s; config per Department.

| No. | Part Number | Areas and Quantities · Time | Due Date | Total Days | Job Numbers |
|---|---|---|---|---|---|

Days Left nằm trong Due Date. Ví dụ:

```text
Cut (3 · 3h 40m), Lathe 1 (4 · 2h 05m), Lathe 2 (2 · 1h 10m), Mill (6 · 45m)
```

## Area Board

Một Management view gồm All Areas overview và per-Area detail.

### All Areas overview

Nhóm theo Area, hiển thị name/description, Operations, total, queue, Machine
assignment, PN list, priority/due, search/sort. Desktop có thể horizontal-scroll.

### Per-Area detail

Dùng cùng read-only Area/Machine layout với Scan Station: Area summary trái, Machine
grid phải; Area không Machine summary full-width. Hiển thị PN/total/queue/Operation/
Machine/finished, active WO/Job/due/priority, time in Area, scrap, search/sort.
Finished tách queued/on-Machine/direct/stocked và không nằm trong Machine card.

## Machines

Production master-data view cho lifecycle, maintenance và asset identity:

- active table: name/Area, derived state + elapsed, assigned PN portions, Asset Tag
  metadata, inline Maintenance switch; click row mở Edit;
- edit maintenance note/expected return không đổi state/start time;
- retired table read-only; details có Reactivate, retired không nhận assignment;
- create/edit dialog: required name, Area (new only), read-only auto Asset Tag/
  barcode, optional asset metadata, lifecycle history;
- in barcode label gồm display name, Asset Tag, `PF:MACHINE:<asset-tag>`;
- create có summary/final warning rằng chỉ retire, không delete;
- Danger Zone `Retire…` yêu cầu type Asset Tag, block nếu còn assigned quantity;
- Reactivate same physical machine có reason, optional new Area, collision checks,
  final permanent-lifecycle confirmation;
- replacement = retire old + create new identity/tag; name có thể reuse.

Không biến thành CMMS: không spare parts, schedule, contract hay cost accounting.

## Tracking

Management interface chính, filter PN/WO/Job/Area/Operation/Machine/Request Type/
priority/status/due. PN detail có metadata/barcode/image/revision, Demand và
requested/allocated/shortage theo WO, current distribution/Machine/finished,
Flows/route mode, Planned/Floating traces, Movement/scrap/time/stock/Allocation/
correction history. PN đã xóa master vẫn hiển thị canonical identity/history,
chỉ thiếu metadata; null WO hiển thị `—`.

Planned route phân biệt completed/active/queued/future/deviation. Floating trace
derive từ history, giữ repeated Area, split flow và Repair:

```text
A → B → C → D → B
                ⟲ REPAIR
```

Không ngụ ý cả PN ở một step.

## Work Orders

Management view cho manual intake. List full width, toolbar search + New, row mở
details. Hỗ trợ create/locate WO, Demand lines, PN create-on-use/barcode, nullable
WO/due, default NEW, quantity/Job/requester/reason/notes; Save chỉ business demand;
`Release to production` là action riêng xác nhận quantity/route/starting context
và kết quả Flow/Movement.

Completed Work Orders nằm ở read-only history riêng, `completed_at` newest-first,
bounded default range, server-side search/filter/paging; không create/edit/release.
Active search miss dẫn tới history; nhập number đã completed mở details thay vì
duplicate. Existing active PN phải show distribution và ask intent. Không mở rộng
sang customer/pricing/invoice/shipping/purchasing/accounting.

## Planned Routes

Search active/archive template, hiển thị Area chips/status/updated/usage. Row mở
dialog; used template archive, never-used delete; archived chỉ duplicate. Dialog
quản lý ordered steps với Area-scoped Operation, stable-id preferred Machine,
duration/instruction và drag + Up/Down. Archive dùng typed name confirmation;
edit used template chỉ ảnh hưởng future assignment. Không template-versioning.

## Part Numbers

Quản lý optional metadata, không gate production. List có image/default placeholder,
canonical PN, description/revision/ERP id và derived barcode. Create canonicalize
và unique; edit metadata/image; print simple barcode label. Hard delete chỉ xóa
metadata, không cascade/hide history, không archive/soft-delete; có thể create lại.

## Priority Management

Hot list theo Demand, không chỉ PN:

1. Sort explicit rank.
2. Add bằng search/select hoặc PN scan: 0 eligible → none; 1 → add; nhiều → chọn.
3. Mỗi Demand cùng PN rank riêng; new entry mặc định cuối.
4. Drag/reorder/remove có confirmation rõ PN/Demand/rank/action; cancel không đổi.
5. Chỉ renumber sau confirm; mọi change ghi audit.
6. Undo/Redo thay save/cancel, cũng phải confirmation, unlimited trong session.
7. Stored rank là highest work/allocation priority.

## Administration

Chỉ system administration: Department, Area, Operation, Station, Worker profile,
User/role/permission, barcode/Asset Tag format, scan behavior, Worker sessions,
Department display settings, retention/archive/purge, app settings. Machines,
Routes, Part Numbers chỉ ở Management, không duplicate. Admin workflow tách khỏi
normal scan.

---

# 22. Phạm vi Department

Ban đầu Machine Shop; tương lai có Purchasing, Assembly, Production, Outsourcing,
Stockroom, QC. Core không hard-code Machine Shop. Department config có Area,
Operation, Machine, Route, starting/terminal Area, scan/Worker/display settings.

---

# 23. Boundary ERP

ERP sở hữu planning/source master; PartFlow sở hữu production tracking. PartFlow
chạy độc lập; ERP ID tách internal ID; import idempotent; ERP model không leak vào
domain; không giả định format PN/WO; ERP change không erase Movement; Modify/Repair
vẫn hợp lệ dù ERP không model.

---

# 24. Kiến trúc ứng dụng

Stack ưu tiên: React hoặc Next.js, FastAPI, PostgreSQL.

```text
Presentation
    ↓
Application
    ↓
Domain
    ↓
Infrastructure
```

Controller/route mỏng; scan orchestration ở Application; business rule ở Domain/
Application; Domain không phụ thuộc framework; Infrastructure không điều khiển
business flow; scan write dùng transaction; database constraint giữ identity/
quantity; Movement immutable; hạn chế global state; ERP isolated.

---

# 25. Yêu cầu transaction

Mọi production write atomic. Một scan có thể parse barcode, validate entity/
permission, resolve Worker/dialog/source Flow, validate quantity/Area/Operation/
Machine/Route, record Movement, rebuild/update state/projection rồi commit. Bất kỳ
bước bắt buộc fail thì rollback toàn bộ; không partial Movement.

---

# 26. Logging

Log phải trả lời what happened, PN/quantity/Flow, relevant Demand, Area/Operation/
Machine/Worker/Station, failure reason và reversal/correction. Tránh duplicate/noise;
không đưa raw internal exception cho operator.

---

# 27. Reporting

Hỗ trợ active WO/Demand/PN, quantity theo Area/Machine, queue, overdue/Hot, planned
vs actual route, Movement/time, stock, Allocation, completed WO, deviation và
correction. Report phải phân biệt business demand, production quantity state,
Movement history và completion Allocation.

---

# 28. Audit và data integrity

Audit đầy đủ cho WO/Demand, priority, route, split/merge, transfer/assignment,
Stockroom, Allocation/correction, quantity adjustment, Repair, Scrap, Undo, Worker
Session, config và archive/purge. Production runtime append-only.

Database constraint khi thực tế: unique PN/non-null WO/barcode; valid Machine-Area;
non-negative quantity; allocation ≤ stock; idempotent `device_event_id`.

## Administrative archival và purge

Admin có explicit maintenance authority ở mọi environment, tách khỏi app runtime.

**PartNumber master deletion:** authorized user hard-delete metadata; không delete/
mutate production records; history vẫn có canonical PN; PN có thể create-on-use
và metadata record có thể tạo lại; không cần tombstone/archive.

**Movement retention:** append-only trong active runtime không bắt buộc mọi row ở
primary PostgreSQL mãi mãi. Configurable retention chọn old history rồi:

1. export lossless;
2. verify export thành công;
3. chỉ sau đó purge đúng rows đã archive.

Mandatory:

- app role không có UPDATE/DELETE Movement; privileged Admin maintenance path riêng;
- không purge trước verified export;
- archive giữ PN, quantity/type, Area/Operation/Machine/Worker/time, relationships,
  corrections và audit fields;
- relationship chain/atomic command phải archive/purge cùng nhau; retained row không
  được reference purged row;
- preview scope/impact, Admin authorization, reason và audit result;
- không purge data cần rebuild active state; projection vẫn reconcile;
- không truncate whole table; retention setting ở Administration;
- file format/transport là quyết định implementation phase.

Production identity traceable không đồng nghĩa mọi raw Movement row phải ở primary
database vĩnh viễn; archive vẫn là permanent record.

---

# 29. Phạm vi ban đầu

Machine Shop, PN metadata/folder barcode, manual/file Work Order, Demand, Modify,
Repair, Scrap, Area/Operation/Machine, scan, distribution/split/merge, routes,
Stockroom/Allocation, optional Worker, Scan Station, Work Orders, Production/Area
Board, Tracking, Machine/Planned Route management, immutable history, Undo và RBAC.
ERP sync có thể bổ sung không thay core model.

---

# 30. Phạm vi tương lai

Chưa thuộc initial scope nếu không duyệt riêng: serial piece tracking, full ERP
sync, machine automation/CNC telemetry, scheduling, inventory valuation, cost/
payroll, IoT, predictive analytics, full quality system, generic workflow engine,
offline scan synchronization. Feature mới phải mở rộng PN/quantity/Movement/Route/
Allocation model, không thay thế.

---

# 31. Non-goal rõ ràng

PartFlow không trở thành full ERP/MES, accounting, serial tracker, CNC controller,
telemetry platform, inventory valuation hoặc automatic ERP replacement nếu chưa
có explicit project decision.

---

# 32. Quyết định còn mở

1. Stocked quantity có được quay lại active production qua controlled reversal?
2. Offline scan synchronization có được thêm ở release sau?

Scrap đã chốt là first-class Movement; không Machine session; Worker expiration đã
chốt là configurable sliding inactivity timeout. Câu hỏi “join an existing Quantity
Flow” của `MODIFY` intake đã chốt ở v22: receipt không bao giờ join quantity đang
có — nó tạo Quantity Flow riêng sau một explicit confirmation, và `Combine
quantities` vẫn là merge duy nhất (§14). Implementation không được đưa giả định
làm hai open decision còn lại khó thay đổi.

---

# 33. Nguyên tắc dẫn đường

- Track PN identity và physical quantity, không individual piece.
- Tách Demand, Movement và Allocation.
- Floating mặc định; Planned snapshot chỉ khi cần guidance.
- Derive current state và Floating trace từ immutable Movement.
- Giữ quantity integrity và complete auditability.
- Scanner-first, tối thiểu interaction, confirm ambiguity.
- Production reality thắng outdated plan.
- ERP-independent.
- Chọn thiết kế đơn giản nhất vẫn mô tả xưởng chính xác.
