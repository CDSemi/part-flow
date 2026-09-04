# Roadmap triển khai PartFlow

> **Bản gốc chuẩn:** [`IMPLEMENTATION_ROADMAP.md`](IMPLEMENTATION_ROADMAP.md).
> Baseline upstream: commit `40fbcb591c3ca1b5952d240e3bcf50ac918e7286`.
>
> **Quyền chuẩn:** File tiếng Anh là canonical source cho thứ tự triển khai,
> ranh giới phase, dependency và các giới hạn tạm thời. Hành vi domain và phạm
> vi sản phẩm do [`PROJECT_PROFILE.md`](PROJECT_PROFILE.md) định nghĩa; UI
> đích đã duyệt do [`GUI_DESIGN.md`](GUI_DESIGN.md) định nghĩa.

## Trạng thái hiện tại

> **Coverage audit (2026-09-03):** đối chiếu `PROJECT_PROFILE.md`, `GUI_DESIGN.md`,
> roadmap này và code production thật đã phát hiện một production workflow bị
> defer mà không phase sau nào sở hữu (`Receive Quantity` ở Scan Station), cùng
> các bàn giao chưa trọn ở monitoring, Worker sessions, authorized corrections và
> Administration policy settings. Phase 10.5 được chèn vào như một corrective
> prerequisite có tên, không đánh số lại các phase sau. Mọi target behavior còn
> lại phải được gán cho đúng một owner phase, hoặc nằm ở `Deferred` / một
> canonical open decision.

- Đặc tả project chuẩn là `PROJECT_PROFILE.md` v21. V21 cho phép sửa có giới hạn
  WorkOrderDemand đã release: `requested_quantity` không thấp hơn quantity đã
  release hoặc allocate; `due_date` và Job Numbers vẫn sửa được; Part Number,
  Request Type, requester, reason, notes cố định và vẫn không được xóa line.
  Tăng quantity của line đã release hết sẽ tạo lại remaining quantity và đưa
  Work Order về `OPEN`. V20 đã cho phép release một demand thành nhiều phần,
  mỗi phần là release rõ ràng, QuantityFlow và `RECEIVED` riêng, không merge;
  remaining quantity là hard limit và terminal Area không thể là starting Area.
  V19 đặt final confirmation gate riêng cho DONE, QUEUE và Undo: ở Area dùng
  scanned Worker Session, scan Worker badge là bước cuối sau confirmation
  summary; ở fixed-Worker Area dùng câu hỏi xác nhận cuối. V18 chốt Worker là
  audit identity theo Scan Station, tách khỏi User; profile gồm stable id, name,
  badge barcode hiện có của hãng, avatar và active status, không có employee
  number. Badge được exact-match như barcode không có prefix `PF:`; format
  `PF:WORKER:` cũ bị bỏ. Scanned session dùng sliding inactivity timeout có
  default Administration và override theo Area, chỉ refresh bởi production
  interaction hợp lệ, cho phép switch Worker ngay, và badge modal chỉ block
  Scan Station trong khi giữ draft dialog. Undo ghi Worker active lúc xác nhận
  theo mode của Area. Theme được lưu theo User/Scan Station theo thứ tự User →
  Station → Dark mặc định. Priority hot-add resolve xác định: một demand hợp lệ
  thì add thẳng, nhiều demand thì bắt buộc chọn; Undo/Redo trong session không
  giới hạn. Production Board là Department-wide và có rotation setting theo
  Department. V17 xác định canonical PN string uppercase, không whitespace là
  stable identity; PartNumber master chỉ là metadata hiện tại có thể hard-delete;
  WorkOrderDemand, QuantityFlow, PartMovement và Allocation tự giữ PN, không có
  `part_number_id`. Retention Movement phải export lossless → verify → purge,
  không được đảo thứ tự. Machine reactivation là đưa **cùng physical machine**
  từ `RETIRED` về `ACTIVE` trên cùng record, thêm lifecycle event `RETIRED` /
  `REACTIVATED`, chỉ đổi Area về phía trước nếu máy đã di chuyển khi retired,
  và block identity reissue/name collision. Active Machine name unique trong
  một Area nhưng được reuse theo thời gian khi replacement. Operational state
  của Machine được derive: Maintenance override, nếu không có assigned quantity
  thì Idle, có thì Running; `state_changed_at` và elapsed time cũng derive.
  Retire thay cho hard delete; replacement là retire record cũ + tạo record mới.
  RouteTemplate được trình bày là Planned Routes, archive nếu từng dùng, delete
  chỉ khi chưa dùng, không version framework. Machines, Planned Routes và Part
  Numbers là production master data có permission trong Management, không nằm
  trong Administration. Area Completion hiển thị DONE, ghi `AREA_COMPLETED`,
  derive `READY_TO_TRANSFER`, theo quantity và khác `RELEASED_FROM_MACHINE` /
  `STOCKED`; transfer có thể implicit complete source trong cùng atomic command
  và Undo đảo cả command. Request Type chỉ `NEW`/`MODIFY`; Repair là movement
  intent `TRANSFERRED · movement_reason REPAIR`; Floating Route mặc định,
  AssignedRoute tùy chọn; Work Order Number ngoài có thể NULL và hiển thị `—`,
  không tạo temporary number; PN barcode chứa PN và master create-on-first-use;
  `SCRAPPED` cùng `QUANTITY_ADJUSTED · INCREASE` đều auditable; có đúng hai Area
  ownership mode, Machine assignment one-shot không Machine session; Scan
  Station route theo station; due date nullable; demand ordering chuẩn; chỉ báo
  scan thành công sau server confirmation; đổi Hot order phải xác nhận.
- UI đích là `GUI_DESIGN.md`; visual reference mới nhất là
  `docs/mockups/partflow-gui-mockup-v18.html`.
- **Phase 1** đã có: React + TypeScript frontend, FastAPI backend, PostgreSQL,
  Alembic baseline, Docker Compose, `/api/health`, formatter/linter/typecheck/
  test và CI.
- **Phase 2** đã triển khai design system và application shell: semantic token,
  Dark/Light, URL routing, Management nhớ subview trong session, mười view đã
  duyệt dưới dạng mock chỉ load khi `import.meta.env.DEV`; production build hiện
  explicit not-connected cho view chưa có backend và `npm run build` kiểm tra
  mock sentinel. Có loading/empty/error/disconnected/long-data state và preview
  dev `?state=`. Connectivity thật dùng `/api/health`: browser online/offline,
  poll khoảng 1 giây với timeout ngắn hơn interval, không probe chồng, recheck
  khi focus/visibility, passive probe không đổi sang connecting, OFFLINE banner
  persistent, disable write và refocus Scan Station sau reconnect; không
  WebSocket/SSE và không offline write queue. Các vòng GUI v9–v18 hoàn thiện
  presentation: Work Orders modal/manual Add Part, nullable WO/due date, save
  omission và unsaved-change guard; Production Board clock, Hot `🔥#n`, due
  urgency, scrap, dynamic pagination/kiosk; Scan Station theo station với
  keyboard wedge, PN-centric one-shot wizard, Machine-first/PN-first, direct
  processing, partial quantity, merge, correction, structured confirmation và
  shared Area/Machine monitoring; Area Board và Scan Station dùng chung layout;
  Tracking, Priority confirmation/Undo/Redo; Machines lifecycle/maintenance/
  retire/reactivate; Planned Routes archive/delete/duplicate/reorder; Part
  Numbers management preview; responsive/touch behavior, theme, copy audit,
  production/mock boundary và accessibility/reduced-motion refinements. Mọi
  Phase 2 save chỉ đổi local mock state; Phase 2 không có domain implementation,
  backend business API hay persisted production write.
- **Phase 3** đã triển khai minimum canonical Domain/Data foundation: PN
  normalization độc lập framework; enum `RequestType`, `RouteMode`,
  `MovementType`, `QuantityFlowStatus`; SQLAlchemy mapping và migration
  `0002_phase3_domain` tạo Department/Area/Operation, optional PartNumber master
  có natural PN key không FK từ production row, WorkOrder/WorkOrderDemand,
  route template/snapshot, QuantityFlow và append-only PartMovement với named
  constraint/index. `route_mode` mặc định `FLOATING`; `assigned_route_id` chỉ có
  khi `PLANNED`; `current_area_id` là NOT NULL projection; PN consistency dùng
  composite FK `(quantity_flow_id, part_number)`. Database trigger bảo vệ
  Movement immutability và model/migration parity được test.
- **Phase 3.5** đã hoàn tất minimum environment setup: persistence và API/UI thật
  cho Departments, Areas, Operations, Scan Stations, Barcode configuration và
  Machines. Area có terminal flag; Area barcode derive; Operation thuộc Area;
  station có stable ID/bound Area/active; Machine được auto-assign immutable
  Asset Tag và `PF:MACHINE:` barcode, có edit metadata, maintenance, retire,
  reactivate same physical machine và append-only `machine_lifecycle_events`
  trong cùng transaction. Đây không tạo production Movement, không generic
  `audit_events`, không Worker/User, không full Administration.
- **Phase 4** đã triển khai end to end Work Order intake và production release:
  Work Order/demand save transaction riêng, PartNumber create-or-reuse và label,
  `FLOATING` hoặc snapshot `PLANNED`, explicit partial/repeated release theo
  remaining cap, mỗi release tạo QuantityFlow + immutable `RECEIVED` atomically,
  idempotent theo `device_event_id`, restricted edit của released demand,
  demand removal rule, audit event cho master/business data, projection replay
  và conservation. UI Work Orders dùng API thật.
- **Phase 5** đã triển khai transfer vào Area queue: station context, PN resolve,
  explicit candidate selection, route/Operation validation và deviation reason,
  full/partial từ Phase 8, immutable `TRANSFERRED`, projection cùng transaction,
  replay/conflict/race handling và Area inventory. Production Scan Station chỉ
  báo success sau server confirmation, refresh inventory và restore focus.
- **Phase 6** đã triển khai Machine-Area processing: migration
  `0007_phase6_machine_assignment` thêm Machine projection/reference,
  `command_sequence` và các type `ASSIGNED_TO_MACHINE`,
  `RELEASED_FROM_MACHINE`, `AREA_COMPLETED`. State QUEUED/ON_MACHINE/
  READY_TO_TRANSFER derive từ latest Movement. Assign/QUEUE/DONE là command
  idempotent có row lock; ON_MACHINE transfer ghi `AREA_COMPLETED` +
  `TRANSFERRED` atomically; Machine operational state và assigned total derive;
  retire bị block khi còn assigned quantity. Read model và Scan Station thật hỗ
  trợ Machine barcode one-shot, PN-first action, inventory split và final
  confirmation; không Machine session.
- **Phase 7** đã triển khai direct processing cho Area không có active Machine.
  Area mode derive từ Machine, không có config mode. Arrival vào Area này là
  PROCESSING với Machine NULL và Operation rõ; DONE không Machine ghi một
  `AREA_COMPLETED`; transfer từ PROCESSING implicit complete + transfer trong
  một command; finished transfer chỉ `TRANSFERRED`. Migration chỉ widen shape
  constraint; history/replay, race, invalid/stale zero-write và frontend thật
  đều được test.
- **Phase 8** đã triển khai SPLIT/MERGED và lineage: migration
  `0009_phase8_split_merge` thêm type, lifecycle closure và append-only
  `quantity_flow_lineage` edge table để biểu diễn 1→N và N→1. Mọi command nhận
  partial quantity bằng SPLIT atomically trong chính command; remainder giữ
  state; Planned child có snapshot riêng. Merge chỉ các active flow cùng PN và
  giống hoàn toàn production context, luôn explicit. Server trả `combine_groups`;
  frontend cung cấp quantity/remainder preview và `Combine quantities`.
- **Phase 9** đã triển khai Undo và correction. Migration
  `0010_phase9_undo_corrections` thêm `SCRAPPED`, `QUANTITY_ADJUSTED`, `REVERSED`,
  `movement_reason`, mandatory `reason`, unique `reverses_movement_id` và status
  mới. Undo đảo **toàn bộ application command** bằng compensating REVERSED theo
  thứ tự ngược, không sửa original; mọi derivation bỏ reversed pair. Eligibility
  được kiểm tra dưới lock, chỉ station ghi original, không double Undo/Undo của
  Undo hoặc restore vào invalid context. Repair là transfer intent đến Area đã
  từng đi qua; Scrap đóng flow/part với reason; addition tạo FLOATING flow mới
  bằng `QUANTITY_ADJUSTED · INCREASE`, không đổi requested quantity. Frontend
  thật có Add more, Return for repair, PF:SCRAP counting và server-authoritative
  Undo preview/final confirmation.
- **Phase 10**: đã triển khai end to end — backend (persistence, command
  `STOCKED`, allocation suggestion/confirmation/reversal, completion derive, read
  model completed history và API) và frontend (workflow `Receive into Stockroom`
  của Stockroom station với allocation dialog theo GUI_DESIGN §10 trên Scan
  Station shell, trang Completed Work Orders thật trên
  `GET /api/work-orders/completed`). Migration `0011_phase10_stock_allocation` thêm
  `STOCKED`, closed status, `work_orders.completed_at` và append-only
  `work_order_allocations`. Stocking dùng cùng arrival protocol như transfer,
  chỉ destination terminal, partial qua SPLIT, implicit completion nếu cần,
  idempotent và không Undo. Available stock derive từ effective STOCKED trừ
  active allocation. Suggestion theo Hot rank → due date → received date → id;
  confirmation giữ hai invariant dưới per-PN advisory lock và row lock: mỗi line
  không quá shortage, tổng không quá available stock. Override được ghi;
  completion derive khi mọi demand fully allocated; reversal có reason, chỉ một
  lần và reopen Work Order. Confirmation mang `allocation_quantity` tường minh
  mà các line phải cộng đúng bằng, từ chối khi stale so với available stock.
  Completed history read-only có search, Done range (preset đặt tên
  `LAST_30_DAYS` / `LAST_90_DAYS` / `THIS_YEAR` / `LAST_YEAR` resolve server-side
  theo ngày hiện tại của site, hoặc Custom với `done_from` / `done_to` tường
  minh) và due outcome xét trên lịch nhà máy (`SITE_TIMEZONE`, một rule
  server-side cho cả filter lẫn ngày hiển thị), sort server-side và keyset
  paging giữ nguyên effective Done range đã resolve ở page đầu, cursor chỉ tồn
  tại khi còn row tiếp theo. Đã audit trước khi đóng.
  Authorization adjustment chờ Phase 14, Worker chờ Phase 13, read model
  monitoring chờ Phase 11.
- **Phase 10.5**: đã triển khai nhưng **chưa đóng** — Application command
  `app/application/intake.py` kèm read model, `POST /api/scan-stations/{id}/receipts`
  và PN resolution mở rộng, cùng wizard `Receive Quantity` ba view thật trong Scan
  Station: một canonical PN không còn active Work Order Demand và không có active
  quantity nay được RECEIVED tại production station — kể cả PN gặp lần đầu — với
  internal Work Order blank-number được tạo hoặc reuse, WorkOrderDemand,
  QuantityFlow, AssignedRoute snapshot cho `PLANNED` và immutable Movement
  `RECEIVED` commit trong một transaction. Không cần migration: shape check của
  `RECEIVED` đã cho phép Scan Station identity và reason. Phase vẫn mở vì
  PROJECT_PROFILE §32 quyết định 3 — “join an existing Quantity Flow” nghĩa là gì
  — chưa chốt, trong khi §14 yêu cầu điều đó trước khi receive một PN đã có active
  quantity.
- **Phase 11**: đã triển khai **Production Board** (Area Board và Tracking là
  phần còn mở của phase). Backend `app/application/production_board.py` trên
  `GET /api/production-board` derive board toàn Department từ projection vị trí
  hiện tại và Movement history: mọi PN có active quantity trong Area của
  Department (hoặc stocked quantity kèm demand còn mở), phân bổ theo Area /
  Machine / External activity với holding state derive (`MACHINE` / `QUEUE` /
  `PROCESSING` / `DONE` / `STOCKED`), timestamp vào vị trí cố định (`occurred_at`
  của effective position-bearing Movement, lấy cũ nhất trong nhóm), stocked và
  scrapped từ effective `STOCKED` / `SCRAPPED`, demand context theo canonical
  demand ordering (PROJECT_PROFILE §18 — demand đầu tiên quyết định Hot rank, due
  date và received date của row; Work Order đã complete không bao giờ cấp
  metadata cho row), tổng Department, theo đúng canonical demand ordering đó
  (stocked không phải một tầng sắp xếp riêng). Frontend thật
  (`src/api/production-board.ts`, `views/production-board/ProductionBoardView.tsx`,
  `board-feed.ts`): Department do server resolve (một Department active duy nhất,
  hoặc `?department=<id>` trên URL của màn hình), auto-refresh định kỳ với một
  request in flight, giữ rows hoàn chỉnh cuối cùng kèm trạng thái `Feed stale —
  reconnecting` khi refresh hoặc kết nối lỗi, refresh ngay khi kết nối trở lại,
  các state loading / error có Retry / empty dưới header luôn hiển thị, và giữ
  nguyên presentation GUI_DESIGN §5 (kiosk, pagination + rotation, auto scale,
  điều hướng tay, dwell / countdown / Total Days derive từ UI clock chung). Mục
  Phase 11 bên dưới ghi chi tiết trạng thái và ranh giới.

## Nguyên tắc triển khai

- Xây theo vertical slice; hoàn tất một workflow end to end trước khi mở rộng.
- Không transfer quantity trước khi có workflow thật để đưa quantity vào hệ
  thống: Work Order Intake và production release phải đi trước transfer.
- Giữ Presentation → Application → Domain → Infrastructure.
- Chỉ dùng mock đến khi backend slice tương ứng tồn tại; mock không lọt vào
  production.
- Mọi ambiguity bắt buộc explicit confirmation trước write; input unknown/invalid
  bị từ chối và zero write.
- Block production write khi disconnected; offline sync bị defer và chưa duyệt.
- Không triển khai ERP integration trong MVP.
- Mọi behavior được mô tả là `deliberately absent`, `temporary`, `placeholder`,
  `later` hoặc bị defer cách khác phải nêu đúng một phase sau sở hữu nó, hoặc
  được liệt kê tường minh ở `Deferred` / một canonical open decision. Một phase
  không bao giờ được đóng khi còn target behavior vô chủ.
- Trước khi đóng bất kỳ phase nào, đối chiếu `PROJECT_PROFILE.md` +
  `GUI_DESIGN.md` + code production hiện tại với roadmap này. Chỉ đóng phase khi
  mọi target behavior trong scope đã triển khai và mọi phần cố ý bỏ qua đều có
  owner sau tường minh.

## Phase 1 — Repository Foundation

Phạm vi:

- React + TypeScript frontend
- FastAPI backend
- PostgreSQL
- Alembic
- Docker Compose
- health endpoint
- connectivity frontend/backend/database
- nền formatter, linter, type check và test

Tiêu chí hoàn tất:

- Development environment start bằng command đã document.
- Frontend gọi backend health; backend kết nối PostgreSQL.
- Formatting, lint, typecheck và initial test chạy thành công.

## Phase 2 — Frontend Design System and Application Shell

Phạm vi:

- shared token;
- context Dark/Light;
- routing/navigation;
- mock view đã duyệt, gồm Work Orders trong shell với New Work Order modal,
  optional WO Number/due date, manual-first Add Part, edit OPEN line, native
  calendar, mock validation và unsaved-change protection;
- phát hiện kết nối nhanh bằng browser event và poll `/api/health` khoảng 1 giây,
  recheck focus/visibility; WebSocket/SSE vẫn out of scope;
- mock data chỉ ở development sau production build boundary thật;
- loading, empty, error, connectivity-loss và long-data state.

Phase 2 chỉ là frontend presentation và mock behavior ở development.

Không thuộc phase:

- production business rule trong mock component;
- domain implementation/database migration;
- backend business API;
- persisted production write;
- ERP integration.

## Phase 3 — Minimum Canonical Domain and Data Foundation

Chỉ tạo foundation cần cho manual Work Order Intake và release:

- Department
- Area
- Operation
- PartNumber master là optional current metadata theo canonical PN uppercase,
  không whitespace; create-on-first-use, production table tự giữ PN và không FK
  đến master
- WorkOrder với external `work_order_number` nullable, unique khi non-null
- WorkOrderDemand với `request_type IN ('NEW','MODIFY')`
- RouteTemplate
- RouteStep
- AssignedRoute tùy chọn, chỉ `PLANNED` flow có
- QuantityFlow có `route_mode` (`FLOATING` mặc định / `PLANNED`) và nullable
  `assigned_route_id`; phải hỗ trợ Floating Route ngay từ đầu
- PartMovement, gồm concept column `movement_reason` cho Repair sau này
- current-position projection được derive

Rule:

- Khi field được thêm phải giữ canonical name: `station_id`, `occurred_at`,
  `server_received_at`, `device_event_id`, `movement_reason`.
- Không tạo tên cạnh tranh như `client_event_id`.
- Movement history immutable; quantity integrity được enforce; invariant có test.
- Floating Route trace derive từ Movement, không có mutable route-history table
  thứ hai.

## Phase 3.5 — Minimum Environment Setup

Đây là prerequisite nhỏ nhất để vận hành production slice đầu tiên, tách khỏi
full Administration Phase 13. Nó nằm sau Domain foundation và trước mọi workflow
cần environment đã cấu hình. Phase 4 cần Department/Area/Operation; Phase 5 cần
active Scan Station bound với Area.

Phạm vi cấu hình đúng các mục sau:

- Departments;
- Areas: identity, color/display, terminal flag;
- Operations theo Area;
- Scan Stations: Station ID, bound Area, active;
- Machines theo Area, lifecycle và maintenance, quản lý trong **Management →
  Machines**, không duplicate ở Administration;
- append-only `machine_lifecycle_events` cho `RETIRED`/`REACTIVATED`, commit
  atomically với lifecycle change, ghi type, Machine identity, time, reason,
  before/after và previous/current Area khi máy di chuyển lúc retired. Đây không
  phải generic audit; Machine không vào Phase 4 `audit_events`. Actor chỉ là
  nullable reference-free value cho đến Phase 14; Worker không liên quan action
  Management này;
- active/inactive flag và đúng barcode ownership: Area có
  `PF:AREA:<stable-id>`, Machine có `PF:MACHINE:<asset-tag>` derive từ immutable
  Asset Tag, Department/Operation không barcode, Scan Station dùng Station ID và
  Area binding, không có `PF:STATION`; Barcode configuration quản lý Asset Tag
  prefix + zero-padded sequence.

Không thuộc prerequisite: production workflow/write; Work Order/release;
QuantityFlow/Movement; transfer/Machine assignment; generic `audit_events`;
Planned Routes/Part Numbers management thật; Worker, User/role, authorization,
correction permission, retention policy, general setting và full Administration.

## Phase 4 — Manual Work Order Intake and Production Release

Vertical slice nghiệp vụ đầu tiên, UI là Work Orders. Phải:

- create/find WorkOrder;
- lưu blank external WO Number thành `NULL`, hiển thị `—`, không persist
  placeholder/temporary number và cho phép audited edit sau;
- chấp nhận WorkOrder và WorkOrderDemand due date NULL;
- create/find PartNumber master theo canonical PN và create-on-first-use;
- view/print tối thiểu barcode `PF:PN:<canonical-part-number>` từ demand-line PN;
  full Management → Part Numbers vẫn thuộc Phase 13;
- create/update WorkOrderDemand;
- tách save demand khỏi production quantity;
- explicit production release;
- tạo QuantityFlow với `FLOATING` mặc định hoặc snapshot độc lập khi `PLANNED`;
- append `RECEIVED`, establish current position, transaction/idempotency;
- không auto-merge active quantity;
- enforce demand removal: chỉ xóa khi chưa release; đã release thì từ chối và
  không cascade bất kỳ production/master/history record nào.

Manual entry đi trước file import. Seed `RECEIVED` chỉ phục vụ dev/test, không
phải product intake workflow.

## Phase 5 — Scan Station Transfer to an Area Queue

Ví dụ pilot: `Material -> Lathe queue`, với Lathe được cấu hình Queue rồi chọn
Machine bằng scan.

Slice gồm:

- stable Scan Station bound Lathe;
- PN barcode và source QuantityFlow resolution;
- Operation/route/quantity validation;
- explicit ambiguity confirmation;
- immutable `TRANSFERRED`;
- transactional current-position update;
- idempotent retry;
- restore keyboard focus;
- recent scans và Area inventory refresh.

Candidate phải theo current position, route, Operation, station context và
deviation hợp lệ; không bao giờ lấy mọi active flow ngoài target Area.

Giới hạn tạm whole-flow đã được Phase 8 gỡ; trước đó partial bị từ chối zero
write. Trước Area Completion, Phase 5 chỉ ghi `TRANSFERRED`; từ Phase 6/7,
transfer active processing quantity ghi `AREA_COMPLETED` + `TRANSFERRED` trong
một atomic command.

Phase này đã hoàn tất end to end: migration `0006_phase5_transfer`, endpoint
`/api/scan-stations/{station_id}/…`, Area inventory và production Scan Station
thật. Route deviation về Area hoặc Operation cần explicit reason, ghi trên
`TRANSFERRED`; route edit/deviation event riêng thuộc phase sau.

## Phase 6 — One-Shot Machine Assignment and Area Completion

- Resolve Machine barcode chỉ như one-shot shortcut; **không có bất kỳ Machine
  session nào**.
- Validate Machine/Area.
- Entry point Machine-first: scan Machine mở assignment dialog đã preselect;
  PN-first: action Assign trên queued quantity.
- `ASSIGNED_TO_MACHINE`.
- `RELEASED_FROM_MACHINE` cho action `QUEUE`, trả quantity chưa xong/đang pause
  về queue, không phải hoàn thành.
- `AREA_COMPLETED` cho Machine Area: DONE trên quantity đang assigned, derive
  `READY_TO_TRANSFER`, clear `current_machine_id` nhưng vẫn giữ Area; transfer
  ON_MACHINE ghi `AREA_COMPLETED` + `TRANSFERRED` trong một transaction. DONE
  theo quantity, không phải PN status và không phải `STOCKED`.
- Reject inactive Machine.
- Một Machine có thể giữ nhiều PN; một Machine hoạt động giống nhiều Machine,
  không auto-assign theo số Machine.

Trạng thái triển khai: hoàn tất persistence, Application command, read model và
frontend thật. Migration `0007_phase6_machine_assignment` thêm nullable/indexed
`quantity_flows.current_machine_id`, `part_movements.source_machine_id` /
`destination_machine_id` và `command_sequence`. Một `device_event_id` từ đây
định danh một **application command** có thể append nhiều Movement;
`UNIQUE (device_event_id, command_sequence)` thay unique cũ và row trước đó có
sequence 1. Enum/shape check thêm `ASSIGNED_TO_MACHINE`,
`RELEASED_FROM_MACHINE`, `AREA_COMPLETED`; các type trong Area giữ cùng Area,
station và đúng source/destination Machine, còn `RECEIVED`/`TRANSFERRED` không
reference Machine.

Holding state derive từ effective latest Movement, không chỉ từ Machine NULL:
assignment → ON_MACHINE, completion → READY_TO_TRANSFER, còn lại → QUEUED trong
Machine Area. Projection replay dựng lại Area, Machine và state. Ba endpoint
`machine-assignments`, `machine-releases`, `area-completions` trả 201 khi mới,
200 khi idempotent replay, 409 khi reuse id khác intent. Assign chỉ từ QUEUED,
lock/re-read Machine và reject retired, maintenance, other-Area; QUEUE chỉ từ
ON_MACHINE và không complete; DONE chỉ từ ON_MACHINE, giữ Area, clear Machine,
cho phép khi maintenance override đang bật. Transfer ON_MACHINE append completion
sequence 1 rồi transfer sequence 2 trong một command; queued/finished chỉ
transfer. Lock order là flow → station → Machine → target Area → Operation; mọi
refusal zero write. Whole-flow limitation ban đầu đã được Phase 8 gỡ.

Machine operational state derive Maintenance > assigned active quantity Running
> Idle; `state_changed_at` chỉ đổi khi state thật đổi. Retirement lock Machine
trước và reject nếu còn active assigned quantity. Machine-scan resolve không lưu
sticky state/session: trả Machine preselected và queued flows; command vẫn
revalidate sau scan. Mỗi flow có `available_actions`; Area inventory split
`queued`, active Machine card chỉ chứa ON_MACHINE và `finished`; `/api/machines`
reconcile assigned total. Frontend hiển thị đúng split, one-shot dialog,
quantity → summary → final question, implicit completion và shared unknown-
outcome retry. Direct processing, SPLIT/MERGED, Worker badge, Undo, Repair,
Scrap, Stockroom chưa thuộc trạng thái Phase 6 ban đầu.

## Phase 7 — Direct Area Processing (Areas Without Machines)

- Area không có active Machine nhận direct processing ownership, không queue.
- Operation được ghi, Machine NULL.
- Nhiều Operation bắt buộc explicit choice.
- Direct `AREA_COMPLETED`: cùng DONE workflow nhưng không Machine; transfer từ
  quantity đang direct-processing implicit ghi `AREA_COMPLETED` + `TRANSFERRED`
  atomically.
- Có đúng hai Area mode: không Machine → direct processing; có Machine →
  `QUEUE_AND_ASSIGN`. Không per-Area mode setting và không auto-assign cho Area
  chỉ có một Machine.

Phase đã hoàn tất. Migration `0008_phase7_direct_processing` chỉ widen branch
shape của `AREA_COMPLETED`: có source Machine trong Machine Area hoặc NULL trong
direct processing, không destination Machine; không thêm table/column/type và
downgrade từ chối nếu đã có completion không Machine. Mode derive bởi
`area_has_machines`; latest arrival Movement trở thành QUEUED hoặc PROCESSING
tùy mode, nên thêm Machine đầu tiên hoặc retire Machine cuối cùng thay đổi derived
mode mà không sửa history.

Direct DONE dùng cùng endpoint `area-completions` nhưng omit `machine_id`, chỉ
accept PROCESSING, ghi đúng một immutable `AREA_COMPLETED`, giữ Operation/Area và
derive READY_TO_TRANSFER. Machine command reject direct-processing quantity.
Transfer PROCESSING ghi completion + transfer dưới cùng `device_event_id`;
READY_TO_TRANSFER chỉ transfer. Idempotency, transaction, row lock và replay giữ
contract Phase 5–6. Read model trả `has_machines`, action DONE/TRANSFER và
inventory `processing`/`finished`; frontend không render queue/Machine card,
hiển thị Operation, summary/final question và chỉ success sau server response.
Worker badge, Undo, Repair, Scrap và Stockroom vẫn chưa thuộc phase này.

## Phase 8 — Quantity SPLIT and MERGED Workflows

Phase đã triển khai end to end:

- migration `0009_phase8_split_merge` thêm `SPLIT` và `MERGED` với shape chung:
  một Area tại Station, không Machine;
- QuantityFlow lifecycle `ACTIVE`/`SPLIT`/`MERGED` và `closed_at` được constrain;
- append-only `quantity_flow_lineage` lưu edge parent → child, relation và
  command `device_event_id`: một edge cho mỗi child của SPLIT và một edge cho
  mỗi source bị consume của MERGED; vì N→1 nên không dùng một `parent_flow_id`;
- lineage event không phải position. State/Machine/Operation của child/result
  derive bằng cách đi theo lineage đến latest position-bearing Movement;
- mọi in-Area/transfer command nhận quantity nhỏ hơn source: trong cùng command
  append source SPLIT, selected child SPLIT, remainder SPLIT rồi action row;
  selected child nhận action, remainder giữ source context; full quantity không
  split. Planned source copy snapshot riêng cho từng child tại route position;
- merge endpoint chỉ merge named active flows của cùng PN trong station Area nếu
  state, Machine, Operation và route context giống hệt. Mọi khác biệt zero write;
  không auto-merge;
- PN resolution trả `combine_groups`; frontend chỉ offer `Combine quantities`
  theo group server cho, có source selection, result preview, `Confirm combine`;
- mọi wizard chấp nhận 1..MAX, hiển thị selected/remainder trước và sau, chỉ gửi
  selected quantity; one-shot write/idempotency giữ nguyên.

## Phase 9 — Undo, Corrections, and Auditable Quantity Events

Phase đã triển khai end to end. Migration `0010_phase9_undo_corrections` thêm
Movement type/shape `SCRAPPED`, `QUANTITY_ADJUSTED`, `REVERSED`; thêm
`movement_reason` chỉ nhận `REPAIR` trên `TRANSFERRED`; `reason` bắt buộc cho
Scrap/adjustment/Repair; `reverses_movement_id` chỉ có trên REVERSED và unique;
status thêm `SCRAPPED`/`REVERSED`.

**Undo:** `undo-preview` là read model; `POST .../undos` đảo **toàn bộ original
command**, kể cả multi-Movement transfer, SPLIT-prefixed partial và merge, bằng
compensating `REVERSED` theo thứ tự ngược. Original không đổi; derivation bỏ
original/reversal pair, nên Area, Machine, route position, holding state, flow
lifecycle và Machine total trở về chính xác trạng thái trước command. Chỉ undo
most-recent effective eligible operation tại station đã ghi; không Management
event, double reversal, Undo của Undo, restore vào retired Machine/deactivated
Area. Eligibility được recheck dưới lock; database unique chặn race; refusal zero
write. Authorization/reason policy chờ Phase 13/14.

**Repair:** là cùng transfer command với explicit intent
`movement_reason = REPAIR` và mandatory reason. Destination phải từng xuất hiện
trong effective history; partial dùng Phase 8 SPLIT; Planned deviation vẫn cần
confirmation/reason riêng; fingerprint phân biệt Repair với plain transfer.

**Scrap:** mỗi confirmation ghi đúng một `SCRAPPED` với reason. Full hoặc split-
off part đóng; ON_MACHINE ghi/release Machine đúng dưới lock, partial remainder
vẫn ở Machine. `scrapped_quantity` là net của reversed scrap.

**Quantity addition:** tạo **new FLOATING QuantityFlow** có first Movement
`QUANTITY_ADJUSTED` với `direction INCREASE`, station và reason. Không edit
quantity của flow cũ, không đổi requested demand và không đoán demand context.
Chỉ dùng khi PN đã có active quantity trong station Area; Area mode quyết định
queue hay direct processing.

Mọi Phase 5–8 contract vẫn giữ: một transaction, whole-command idempotency,
fingerprint conflict, lock order, race winner tại commit, zero write khi từ
chối, projection replay. `available_actions` thêm SCRAP. Frontend thật có Add
more quantity không MAX/default, Return for repair chỉ khi `repair_available`,
Scrap dialog đếm PF:SCRAP cục bộ rồi gửi một write, và Undo ở Last Scanned PN.
Undo target do server preview quyết định, bỏ qua command mới hơn nhưng ineligible,
hiện structured summary rồi warning final question; lost response retry cùng id,
success refresh inventory và focus. Test bao phủ mọi command, conservation,
Machine/route restore, consecutive undo, race, Repair/Scrap/addition, offline và
focus.

## Phase 10 — Stockroom and WorkOrderAllocation

- Movement `STOCKED`.
- Available stocked quantity.
- Suggested allocation theo canonical ordering:
  1. WorkOrderDemand priority do Manager đặt, cao nhất trước;
  2. cùng priority: demand có due date sớm trước; undated sau mọi dated demand,
     rồi parent WorkOrder `received_date` cũ trước;
  3. bằng nhau dùng stable deterministic tie-breaker, chỉ là implementation
     detail.
- WorkOrderAllocation tách khỏi PartMovement.
- Khả năng adjustment allocation (reversal có audit) — authorization theo role
  do Phase 14 enforce, không sớm hơn.

Trạng thái: hoàn tất — backend (persistence, command `STOCKED`, allocation
command/read model, completion derive, completed history) và frontend (workflow
receiving của Stockroom station với allocation dialog theo GUI_DESIGN §10, trang
Completed Work Orders §11.5); đã audit trước khi đóng theo mục này, PROJECT_PROFILE
§8.12/§18, GUI_DESIGN §10/§11.5 và các contract command/lineage/correction của
Phase 5–9. Migration `0011_phase10_stock_allocation` thêm STOCKED shape
giống transfer giữa hai Area khác nhau tại Station, không Machine; terminal là
Application rule dưới Area lock. Flow status STOCKED là closed; thêm
`work_orders.completed_at` với partial keyset index; tạo append-only
`work_order_allocations` chứa canonical PN, demand id, positive quantity, source
STOCKROOM/MANAGEMENT, override flag, reason, unique reversal reference, optional
station, actor reference, allocated time, idempotency pair/fingerprint và trigger
chặn update/delete. Downgrade từ chối làm mất history.

Stocking endpoint dùng cùng arrival engine với transfer nhưng target station
phải bound terminal Area; source/destination/route/Operation được validate dưới
lock, Planned route cuối ở Stockroom là on-route, terminal khác là deviation cần
xác nhận. Partial dùng in-command SPLIT; ON_MACHINE/PROCESSING implicit complete;
Repair vào stock bị cấm. Stocked flow đóng, rời mọi active read model, không nhận
command sau và không Undo vì allocation có thể đã dựa vào nó. Reconciliation là
`introduced = active + stocked + scrapped`.

Stocked quantity derive từ effective STOCKED; active allocation là row không bị
reversal; available = stocked − active. `allocated_quantity` và `completed_at`
là maintained projection có thể rebuild. Suggestion list mọi outstanding line,
propose `min(shortage, remaining available)` và báo surplus. Confirmation lock
theo per-PN advisory namespace, rồi demand và WorkOrder theo id; recheck
idempotency, PN agreement, line không vượt shortage, total không vượt stock;
khác suggestion được ghi override. Fully allocated mọi line đặt `completed_at`,
đưa Work Order khỏi active list vào read-only history; exact number lookup vẫn
tìm thấy để không duplicate. Patch/remove/release completed Work Order bị từ
chối. Allocation reversal append row có mandatory reason, chỉ một lần, hoàn
stock và reopen Work Order. Completed history search WO/PN/Job Number; Done
range là preset đặt tên `done_range` (`LAST_30_DAYS`, `LAST_90_DAYS`,
`THIS_YEAR`, `LAST_YEAR` — server resolve theo ngày hiện tại của site trong
`SITE_TIMEZONE`, không bao giờ theo clock browser) hoặc Custom với
`done_from` / `done_to` inclusive tường minh, không bao giờ cả hai; filter due
outcome xét trên cùng lịch nhà máy; sort server-side; keyset page theo opaque
cursor gắn với sort đã phát hành và mang luôn các ngày mà preset đã resolve ở
page đầu — mọi continuation của một query đã load giữ cùng effective range dù
site midnight hay New Year đi qua giữa hai page, còn first page mới resolve
preset theo ngày site mới — và cursor chỉ được phát khi thực sự còn row tiếp
theo (history kết thúc đúng ranh giới trang không có `Show more` thừa). Không
command allocation nào sửa Movement history. Authorization chờ Phase 14; Worker chờ Phase 13; return
stock to production vẫn là open decision.

## Phase 10.5 — Scan Station Receive Quantity Gap Closure (corrective prerequisite có tên — không đánh số lại phase sau)

Phase corrective này đóng một workflow gap phát hiện sau Phase 10: Phase 5 cố ý
defer `Receive Quantity` ở Scan Station và Phase 9 vẫn phân biệt nó với `Add more
quantity`, nhưng không phase sau nào sở hữu phần triển khai. Phải hoàn tất trước
khi phần Phase 11 còn lại được coi là xong và trước khi Phase 12 bắt đầu.

Scope:

- triển khai workflow **`Receive Quantity`** thật của GUI_DESIGN §4.7 /
  PROJECT_PROFILE §14 khi một canonical PN không có active Work Order Demand, kể
  cả PN gặp lần đầu;
- manual entry và scan `PF:PN:<part-number>` resolve theo cùng canonical PN rule:
  trim whitespace bao ngoài, từ chối whitespace bên trong, uppercase, tạo
  PartNumber master khi dùng hợp lệ lần đầu;
- giữ workflow ba bước: settings → quantity → confirmation, default editable
  `Request Type = MODIFY` và `Route Mode = FLOATING`, optional due date, chọn
  Planned Route chỉ khi `PLANNED`, context Area/Operation của station,
  reason/notes và canonical Work Order behavior; `Confirm receipt` là write point
  duy nhất;
- create/reuse internal WorkOrder / WorkOrderDemand đúng như PROJECT_PROFILE §14
  định nghĩa. Nhiều blank-number MODIFY Work Order plausible thì bắt buộc explicit
  selection — không bao giờ đoán first match;
- receipt đã confirm tạo production quantity transactional: business-demand
  records, QuantityFlow, AssignedRoute snapshot độc lập chỉ cho `PLANNED`,
  immutable Movement `RECEIVED` và current-position projection commit trọn vẹn
  hoặc không gì cả;
- `RECEIVED` do scan ghi Scan Station identity và Operation đã resolve. Station
  phải còn active và còn bound vào Area active, non-terminal đã confirm lúc
  command commit; context station / Area / Operation cũ bị từ chối, zero write;
- dùng command/idempotency model sẵn có: một `device_event_id` cho một intent đã
  freeze, deterministic fingerprint, replay cùng intent, reuse xung đột bị từ
  chối, retry khi mất response dùng cùng id, success chỉ hiện sau khi server xác
  nhận;
- refresh station context/inventory và trả focus scan sau success; input invalid,
  ambiguous, cancelled, offline, stale hay bị từ chối đều zero write;
- giữ **`Add more quantity`** tách biệt: nó vẫn là correction
  `QUANTITY_ADJUSTED · INCREASE` của Phase 9 bên cạnh quantity đã active trong
  Area của station, không bao giờ thay cho `Receive Quantity` ban đầu;
- giữ production build boundary: workflow thật dùng server state, không import
  hay phụ thuộc mock Scan Station chỉ có trong development;
- ghi `received_date` từ SCAN, không phải từ confirmation (PROJECT_PROFILE §14):
  resolution phát ra instant, wizard mang nó đi, server validate và derive date
  theo lịch site;
- serialize entry condition với mọi command có thể làm PN có active demand hoặc
  active quantity, để receipt không bao giờ commit bên cạnh một trong số đó;
- chỉ receive một PN ĐÃ có active quantity sau explicit join-or-separate
  confirmation mà PROJECT_PROFILE §14 yêu cầu. **Bullet này đang bị chặn**:
  không canonical document nào định nghĩa ngữ nghĩa của việc join
  (PROJECT_PROFILE §32 quyết định 3), nên workflow từ chối trường hợp đó và
  phase vẫn mở tới khi quyết định được chốt.

Không thuộc phase corrective này:

- Worker identity / Worker Sessions / badge-confirmation gate — Phase 13;
- authentication / role enforcement — Phase 14;
- quản lý Planned Route template hay PartNumber metadata — Phase 13;
- allocation behavior của Stockroom — đã thuộc Phase 10.

Trạng thái triển khai (**một phần — phase CHƯA đóng**: mọi scope bullet ở trên đã
triển khai, nhưng canonical decision mà bullet cuối phụ thuộc vẫn mở, nên
`Receive Quantity` chỉ phủ trường hợp PN không có active quantity — xem *Bị chặn
bởi canonical decision* bên dưới. Backend: command `Receive Quantity` kèm read
model và API; frontend: wizard ba view của GUI_DESIGN §4.7 mục 1 trong Scan
Station thật; đã validate bằng full backend gate — `ruff format --check`,
`ruff check`, `mypy app tests`, 658 test —, `alembic upgrade head` +
`alembic check` không drift, và full frontend gate `npm run check` — format,
lint, typecheck, 719 test, build, production-boundary sentinel): **không
migration** — shape check theo type của Movement đã cho phép `RECEIVED` mang
`station_id` và `reason`, nên receipt do scan không cần đổi schema và Alembic
head vẫn là `0011_phase10_stock_allocation`. **Entry condition**
(`app/application/intake.py`, expose qua `app/application/scan_station.py`): một
Work Order Demand là ACTIVE khi còn business shortage —
`requested_quantity > allocated_quantity` (PROJECT_PROFILE §14, đã làm rõ ở đó
trong phase này: released quantity không quyết định, và `WorkOrder.completed_at`
là trạng thái tổng của cả Work Order chứ không phải của một line). Resolution
báo `has_active_demand` theo rule đó cùng `intake_available` (không active
demand, không ACTIVE QuantityFlow nào của PN ở bất kỳ đâu, và Area của station
có thể bắt đầu production — terminal Area thì không bao giờ), `part_number_known`
(copy Step 1 phân biệt PN đã biết với PN mới), `internal_work_orders` và
`scanned_at`; read model xét entry condition còn command xét lại một cách
authoritative lúc ghi. **Command** (`intake.receive_quantity`,
`POST /api/scan-stations/{station_id}/receipts`) là MỘT transaction theo thứ tự
đã thiết lập — input shape → deterministic fingerprint → idempotency fast path →
station context → MỘT advisory lock cấp PN dùng chung
(`part_numbers.acquire_part_number_lock`) → idempotency re-check → row lock Scan
Station kèm re-check active/binding authoritative → precondition no-active-quantity
và no-active-demand → resolve internal Work Order dưới lock demand → WorkOrder →
re-read Area đã lock (active, non-terminal) → lock Operation
(`transfers.resolve_arrival_operation` — một Operation active tự resolve, nhiều
thì phải chọn tường minh) → các write → COMMIT (hoặc replay của bên thắng race).
Nó tạo PartNumber master khi dùng hợp lệ lần đầu, internal `WorkOrder`
(`work_order_number = NULL`, `received_date` là ngày trên lịch site **của lần
scan**), `WorkOrderDemand` (due date optional và reason của operator trên line
mới), `AssignedRoute` snapshot độc lập chỉ cho `PLANNED` — step đầu phải bắt đầu
ở Area của station —, `QuantityFlow` với `current_area_id` do chính INSERT đặt,
và Movement `RECEIVED` mang Scan Station identity, Operation đã resolve, reason
của receipt và cùng `context.work_order_demand_id` bất biến mà mọi release ghi,
nên released quantity của demand và mọi read model vẫn derive từ một nguồn.
**Reuse internal Work Order** (PROJECT_PROFILE §14 “không bao giờ đoán”): ứng
viên là Work Order không có external number, chưa completed và đã có `MODIFY`
demand line cho cùng PN; đúng một thì reuse, nhiều thì từ chối bằng 409 mang
`selection_required` kèm danh sách để station cho chọn tường minh, không có thì
tạo internal Work Order mới, và receipt `NEW` không bao giờ reuse. Reuse NÂNG
line sẵn có thêm received quantity, đóng dấu `updated_at` đúng như một Work Order
edit thường, kèm audit row `UPDATED` (SLICE1_DATA_MODEL §5 — một canonical PN tối
đa một lần trên một Work Order — vẫn nguyên; restricted edit của PROJECT_PROFILE
§13 cho phép nâng line đã release và sửa due date, không gì khác của line cũ bị
ghi lại). Mọi từ chối đều không ghi gì: PN, quantity, Request Type, Route Mode
hay Planned Route invalid, Route không bắt đầu ở đây, station đã deactivate hoặc
rebind, Area đã deactivate hoặc terminal, Operation lạ hoặc ambiguous, active
demand / active quantity xuất hiện từ lúc mở wizard, selection Work Order cũ,
scan timestamp naive / ở tương lai / quá cũ so với intake scan window, và reuse
`device_event_id` sai fingerprint hoặc khác command.

**Undo boundary:** receipt cố ý KHÔNG reversible từ station
(`undo._ineligibility` từ chối command `INTAKE` tường minh, như Phase 10 đã làm
với `STOCK`): reversal cấp Movement khôi phục production state và không bao giờ
ghi lại business demand mà receipt đã tạo hoặc nâng, nên nửa-reversal là bất khả
thi by construction.

**Serialization cấp PN.** Entry condition của receipt là state cấp PN, nên nó
được bảo vệ bằng MỘT lock cấp PN dùng chung cho mọi command có thể đẩy một PN
qua các ngưỡng đó — không active quantity → có active quantity (production
release, chính receipt, và Undo mở lại flow mà command của nó đã đóng) và không
active demand → có active demand (Work Order save thêm hoặc nâng demand line,
allocation reversal, và chính receipt). `part_numbers.acquire_part_number_lock`
thay cho hai namespace release và allocation cũ, vốn chỉ bảo vệ chính command đó
với chính nó; `acquire_part_number_locks` lấy nhiều PN theo thứ tự canonical tăng
dần để các Work Order save chồng lấn xếp hàng thay vì deadlock. Mọi bên đều lấy
advisory lock TRƯỚC mọi row lock và re-read state dưới lock, nên thứ tự toàn cục
vẫn là PN advisory → row (demand tăng dần → Work Order → flow → station → Machine
→ Area → Operation), không có chu trình. Các command chỉ đẩy PN theo chiều nới
lỏng — xóa demand line, allocation confirmation, đóng flow — cố ý không lấy lock.

**Received date.** `received_date` theo SCAN, không theo confirmation
(PROJECT_PROFILE §14): PN resolution phát ra `scanned_at`, wizard mang nó qua mọi
bước, và `Confirm receipt` gửi lại. Server validate (có time zone, không ở tương
lai, không cũ hơn `intake.MAX_SCAN_AGE` — mười hai giờ) và derive calendar date
theo `SITE_TIMEZONE` qua đúng một helper lịch site dùng chung
(`work_orders.site_date_of`), nên receipt chuẩn bị lúc 23:50 và confirm lúc 00:10
vẫn thuộc ngày đã scan. Instant này nằm trong idempotency fingerprint: retry cùng
intent replay receipt gốc, còn một lần scan mới là intent khác và cần
`device_event_id` riêng.

Test: `tests/test_intake_api.py` (entry condition với active demand, active
quantity và terminal Area giữ workflow lại; các record của receipt, arrival
queued so với direct processing, due date, snapshot `PLANNED` và các từ chối của
nó; reuse bằng cách nâng line kèm audit row, từ chối khi nhiều ứng viên kèm danh
sách và selection tường minh sau đó, selection cũ, `NEW` không bao giờ reuse,
Work Order completed không bao giờ là ứng viên; revalidation lúc ghi và ma trận
input invalid, mỗi trường hợp zero write; replay, reuse sai và reuse khác
command; từ chối Undo; projection replay; received date qua site midnight cùng
validation và intent của scan timestamp; và bộ concurrency — hai receipt đồng
thời của một PN, và receipt chạy đua với từng writer có thể trao cho PN active
demand hoặc active quantity).

**Frontend** (`src/api/scan-station.ts` — `receiveQuantity`,
`workOrderSelectionRequired`, resolution mở rộng kèm `scannedAt` —,
`src/views/scan-station/scan-station-intake-dialog.tsx`, nối trong
`ScanStationView.tsx`): PN resolve với `intake_available` mở `Receive Quantity`
thay cho placeholder trung thực trước đây — ba view đã duyệt trong MỘT dialog
lifecycle (settings → quantity → confirmation), default editable
`Request Type = MODIFY` và `Route Mode = FLOATING`, field Planned Route chỉ cho
`PLANNED` và chỉ liệt kê Route bắt đầu tại Area này, due date optional với label
`.field-optional` dùng chung, chọn Operation tường minh khi Area cấu hình nhiều,
field reason/notes, và dòng Work Order behavior nêu cái sẽ tạo hoặc reuse với
selection tường minh ngay trong settings view khi có nhiều ứng viên (`Next` bị
chặn tới khi chọn). Quantity view không MAX và không default, dùng keypad và
recap chip chung (`TypeChip · RouteModeChip · Operation`), và `Confirm receipt`
trên summary có cấu trúc là write point duy nhất, gửi qua one-shot write model
chung: success chỉ đọc sau câu trả lời của server, từ chối tường minh giữ wizard
mở kèm lý do của server và không ghi gì, từ chối `selection_required` quay lại
settings view với ứng viên của server dưới `device_event_id` MỚI (request bị từ
chối không ghi gì, nên intent đã sửa là intent khác — `useOneShotWrite` có thêm
`resetIntent` đúng cho việc này), và mất response thì freeze intent sau CÙNG
`device_event_id` với `Retry the same receipt`. Sau khi server xác nhận, station
context và Area inventory load lại từ server, barcode input lấy lại focus, và
receipt vào session log NHƯNG không trở thành Undo target. Test frontend:
`scan-station-intake.test.tsx` (default settings và cả hai copy PN, Planned Route
lọc theo Area, write point kèm reload và refocus, due date / reason / Operation,
request `PLANNED`, arrival direct-processing, reuse và selection tường minh,
`NEW` không reuse, từ chối, từ chối selection-required với id mới, retry khi mất
response dùng cùng id và cùng scan timestamp, scan timestamp đi nguyên vẹn tới
write point, block offline, Cancel không ghi gì, và receipt không được đề nghị
làm Undo target), cùng suite transfer Phase 5 đã cập nhật, nơi placeholder “no
intake at the station” cũ nay là wizard.

Cố ý chưa có: Worker identity, Worker Sessions và badge gate (Phase 13),
authorization (Phase 14), quản lý Planned Route và PartNumber master (Phase 13).

**Bị chặn bởi canonical decision — vì sao phase chưa đóng.** PROJECT_PROFILE §14
yêu cầu một PN đã có active quantity chỉ được receive sau explicit confirmation
rằng quantity mới **join existing Quantity Flow hay tạo Flow riêng**. GUI_DESIGN
§4.7 không đặc tả bước đó trong wizard `Receive Quantity` (mục 1 chỉ mở cho PN
không có active demand, và ba view của nó không thu thập lựa chọn join-or-separate
nào), và không tài liệu nào định nghĩa join nghĩa là gì — được join active flow
nào, quantity được join thừa hưởng gì (route mode, Assigned Route, current
position, processing state), Movement nào ghi lại. Khoảng trống đó nay được ghi
thành PROJECT_PROFILE §32 quyết định 3. Trước khi chốt, implementation từ chối
receipt mà PN có active quantity thay vì tự bịa ngữ nghĩa, và phase này vẫn
**chưa hoàn tất**: đóng nó sẽ đánh dấu một canonical requirement chưa triển khai
là xong. Mọi phần còn lại trong scope đã triển khai và đã validate.

## Phase 11 — Read Models and Monitoring Views

- Production Board;
- Area Board, gồm Manager Summary trong All Areas overview, không có view Manager
  Summary riêng;
- Tracking;
- projection derive từ Movement;
- stale-feed và long-data state;
- breakdown quantity theo từng PN của **Assigned now** trong Management →
  Machines, thay presentation chỉ-tổng tạm thời của Phase 6;
- expected-duration monitoring: thay stand-in `>= 3 days` cố định của long-dwell
  trên Production Board bằng hành vi expected-duration advisory canonical theo
  PROJECT_PROFILE §17 / GUI_DESIGN. Trước khi triển khai, canonical docs phải
  định nghĩa nguồn duration nào áp dụng khi vừa có duration của Assigned Route
  step vừa có default của Operation; implementation không được bịa hay đoán quy
  tắc ưu tiên.

Phase 11 vẫn chỉ là read-model / monitoring. Nó không được hút vào production
write của Scan Station, Priority write, master-data management, Worker session
hay authentication.

Trạng thái triển khai (một phần — Production Board hoàn tất end to end; phần
Phase 11 còn mở là Area Board, Tracking, breakdown theo PN của Machines và
expected-duration monitoring): **Backend**
(`app/application/production_board.py`, `app/api/production_board.py` —
`GET /api/production-board?department_id=`): read model read-only toàn Department,
không có per-Area mode (PROJECT_PROFILE §21, GUI_DESIGN §5), derive hoàn toàn từ
projection vị trí hiện tại và Movement history bất biến, không bao giờ từ counter
lưu sẵn. Active quantity là mọi QuantityFlow ACTIVE có current Area thuộc
Department; holding state theo effective latest position-bearing Movement và mode
của Area (`projections.processing_state_of` — cùng một derivation với mọi read
model Scan Station; `projections.effective_latest_movements` nay giải quyết
trường hợp thường bằng một query gộp, chỉ walk lineage cho flow sinh từ lineage
event mà chưa di chuyển), Machine là destination Machine của Movement đó với
`ON_MACHINE` và Machine hoàn thành (source) chỉ là context phụ cho quantity đã
DONE, timestamp vào vị trí (`since`) là `occurred_at` của Movement đó — split
child kế thừa thời điểm vào của parent qua lineage, command bị Undo khôi phục
thời điểm của state được khôi phục. Quantity gộp theo (Area, state, Machine,
External activity) lấy thời điểm CŨ NHẤT của nhóm; Operation external
(`Operation.is_external`) đặt tên activity. Stocked = Σ effective `STOCKED` vào
terminal Area của Department (theo Area, không có thời gian vào); scrapped = Σ
effective `SCRAPPED` trong Area của Department (trừ scrap đã reverse). Demand
context = CHỈ demand còn mở của PN (Work Order chưa complete), theo
`allocations.canonical_demand_order`; demand đầu tiên quyết định Hot rank, due
date, received date của row. Work Order đã complete là lịch sử, không bao giờ
cấp Hot rank, ngày hay metadata Work Order / Job Number cho row kể cả khi
quantity nó release còn trong sản xuất: row như vậy — cũng như quantity addition
Phase 9 hay merge giữa các demand — không có demand context, giữ due date null
và lấy received date từ ngày tạo active flow cũ nhất theo lịch site
(`SITE_TIMEZONE`). Chọn row: PN có active quantity luôn là row; PN không có
active quantity chỉ là row khi có stocked quantity trong Department VÀ demand
còn mở, rời board khi mọi Work Order của nó complete. Board order
(`board_row_sort_key`): đúng canonical demand ordering của demand quyết định và
không gì khác — Hot rank trước, dated theo due date sớm nhất, undated sau mọi
dated theo received date của Work Order, demand id là tie-breaker; stocked không
phải một tầng sắp xếp (row stocked hoàn toàn sắp theo open demand của nó như
mọi row khác), row không có demand context là row không rank, không due date,
sắp theo received date fallback. Department: `department_id` tường minh phải tồn tại (404); bỏ trống thì
resolve Department active duy nhất (không có → 404, nhiều → 409 nêu tên — màn
hình không bao giờ bị trỏ nhầm Department âm thầm). Response chỉ mang timestamp
và ngày nguồn cố định — dwell, countdown và `Total Days` derive lúc render từ UI
clock chung (GUI_DESIGN §3.12); không so thời gian tại vị trí với expected
duration của Route Step. Test: `tests/test_production_board_api.py` (phạm vi và
resolve Department, mọi state kèm Machine và External activity, gộp lấy thời
điểm cũ nhất, timestamp kế thừa qua lineage và khôi phục qua Undo, stocked /
scrapped từ history, tổng footer khớp rows, row stocked-only còn khi demand mở
và rời khi complete, Work Order complete không bao giờ cấp context cho row,
quantity không có demand context vẫn giữ row với ngày fallback, canonical board
order không có tầng stocked, demand đầu tiên quyết định ngày của row). **Frontend** (`src/api/production-board.ts`,
`views/production-board/ProductionBoardView.tsx`, `board-feed.ts`,
`board-logic.ts`; view thật trong `src/app/real-views.ts` có trong mọi build,
mock dataset cũ đã xoá): board đọc `GET /api/production-board` —
`?department=<id>` trên URL board (địa chỉ cấu hình của màn hình treo tường,
địa chỉ presentation chứ không phải route) thành `department_id`, nếu không
server resolve Department active duy nhất và từ chối tường minh được hiển thị —
tự refresh mỗi `BOARD_REFRESH_MS` (15 s, `board-logic`) với một request in
flight (request kế tiếp chỉ arm sau khi có trả lời); refresh lỗi giữ board hoàn
chỉnh cuối và đánh dấu feed stale — trạng thái `● Live` chuyển tone warning kèm
ghi chú `Feed stale — reconnecting`, giống hệt khi connectivity chung không
khỏe — polling tiếp tục để board tự hồi phục, kết nối trở lại sau khi mất thì
refresh ngay, còn lần load ĐẦU lỗi là error state có Retry; header board (dòng
Department từ trả lời server, tiêu đề với live status, đồng hồ) render ở mọi
state trong khi vùng bảng hiển thị loading, error, `No active production in this
Department.` hoặc rows, và footer (chỉ số trang, rotation, điều hướng, tổng của
server — active PNs, pcs in production, pcs stocked, pcs scrapped —, công tắc
Kiosk và Auto scale, legend sắp xếp) render khi đã có board hoàn chỉnh.
Presentation duyệt ở Phase 2 giữ nguyên: location rows (chấm Area màu từ
server, chip Machine với `on machine`, `queue`, `processing`, chip External
activity, `done` với Machine hoàn thành trong tooltip, `stocked`), dòng total
với `n scrapped`, cột Job Numbers nêu mọi demand (`<job numbers> · WO <number
hoặc —> [· MODIFY] · <n> pcs` hoặc `· allocated a/n`), dwell derive theo vị trí
(`long` khi ≥ 3 ngày), countdown và `Total Days` từ UI clock chung, ngọn lửa Hot
và tint row, kiosk, pagination theo chiều cao với rotation tỉ lệ, auto scale và
mọi điều hướng tay; rows render đúng thứ tự server trả — `sortBoardRows` phía
client đã bỏ, một quy tắc sắp xếp duy nhất thuộc read model (fixture long-data
development sắp bằng `compareDemandOrder` chung một lần lúc load). Chỉ development: `?state=loading|empty|error|long` render state xác định
mà không request (fixture long-data inline sau ranh giới DEV — không import
`src/mocks/`; mock dataset Phase 2 `src/mocks/production-board.ts` đã bỏ). Test
frontend: suite Production Board hiện có chạy trên trả lời giả của
`GET /api/production-board` (đúng wire shape backend) cộng hành vi feed (request
có và không `department_id`, cột Job Numbers, loading dưới header, load đầu lỗi
có Retry, refresh định kỳ, feed stale khi refresh lỗi vẫn giữ rows và hồi phục
ở trả lời tốt kế tiếp, mất kết nối hiện feed stale và refresh ngay khi trở lại,
Department trống, state preview không request) và `production-boundary.test.ts`
(board trong registry view thật, có trong production module graph, không
import `src/mocks/`). Cố ý chưa có: read model và view Area Board, Tracking
(phần còn lại của phase — vẫn là mock view chỉ development), dòng tên / revision
PN master (Part Numbers management, Phase 13 — dòng phụ chỉ render khi có tên),
thời gian rotation theo Department và Due Soon policy từ Administration (Phase
13 — dùng default đặt tên trong `board-logic` và `views/dates`), quản lý Hot rank
(Phase 12 — board chỉ đọc `priority_rank`), và highlight thời gian tại vị trí
theo expected duration (PROJECT_PROFILE §17 — cờ `long` ≥ 3 ngày thay thế).

## Phase 12 — Priority Management

- ranking Hot WorkOrderDemand;
- add/search/scan; add ở bottom áp dụng trực tiếp;
- reorder bằng drag-and-drop, Move Up/Move Down;
- xác nhận trước remove và trước mọi thay đổi order existing entry, kể cả Undo/
  Redo;
- apply có audit sau explicit confirmation;
- Undo/Redo.

## Phase 13 — Full Administration and Production Identity Configuration

Hoàn thiện Administration ngoài minimum setup Phase 3.5 và làm thật các surface
master-data / configuration production còn lại.

- Workers: stable id, name, employee badge barcode exact-match không
  `PF:WORKER:`, avatar, active; Worker vẫn tách khỏi User;
- Worker identification theo Area: lưu và quản lý các mode canonical `disabled`,
  `fixed Worker` và `scanned Worker Session`, gồm cả fixed Worker được cấu hình;
- tích hợp runtime của Worker Session: resolve/switch badge, session sliding
  inactivity theo phạm vi Scan Station, modal chặn khi hết hạn, giữ nguyên draft
  của production dialog đang mở, và chỉ refresh timeout bởi production
  interaction hợp lệ;
- production audit identity: thêm/dùng reference Worker/ScanSession canonical mà
  Movement của Scan Station cần và Worker attribution mà station allocation cần;
  record lịch sử vẫn hợp lệ với identity null và không bao giờ được backfill bằng
  cách đoán;
- RouteTemplate management thật trong **Management → Planned Routes**, không
  duplicate trong Administration;
- optional PartNumber master management thật trong **Management → Part Numbers**:
  sửa metadata, quản lý image, toàn bộ surface barcode label, và hard deletion
  chỉ record metadata; deletion không bao giờ cascade vào WorkOrderDemand,
  QuantityFlow, PartMovement hay allocation;
- khi Part Numbers management thành thật, đổi đích của PN control trên demand
  line sang shared `Edit Part Number` dialog; barcode label vẫn mở được từ trong
  đó và control dùng affordance edit đã duyệt;
- Users và role/authorization management: cấu hình tạo ở đây, enforcement là
  Phase 14;
- Worker Session policy: một sliding inactivity timeout default cùng override
  theo Area, cùng ba badge-confirmation option độc lập cho `DONE`, `QUEUE` và
  `UNDO` (default ON) quyết định hình thức của final confirmation gate luôn tồn
  tại;
- Undo reason policy: cấu hình kích hoạt `reason when configured` của
  PROJECT_PROFILE §16; khi bật thì backend Undo command, không chỉ UI, bắt buộc
  reason;
- correction permission: cấu hình ở đây, enforce ở Phase 14;
- Department display setting cho Production Board rotation (giây trên mỗi row
  hiển thị và thời gian dừng tối thiểu của một page);
- Due Soon policy setting: thay default tạm ở frontend bằng policy cấu hình được
  mà presentation urgency của due date dùng, theo GUI_DESIGN §3.12 chứ không tạo
  model policy thứ hai;
- theme persistence theo User và Scan Station với thứ tự User → Station → Dark
  mặc định;
- scan behavior, retention/archive policy setting (execute Phase 16) và general
  setting.

Phase 13 không được để lại preview Worker-session policy chỉ-development hay
production identity giả trong module graph production.

## Phase 14 — Authentication, Role Enforcement, and Authorized Management Corrections

- authenticate application User; badge Worker vẫn là audit identity của Scan
  Station và không bao giờ thành login credential;
- enforce authorization phía server trên mọi write của Management,
  Administration, correction, allocation adjustment và master data — visibility ở
  frontend không bao giờ là security boundary;
- áp correction permission đã cấu hình ở Phase 13 vào Undo/correction command và
  các production action đặc quyền khác;
- biến khả năng **Management allocation / allocation reversal** của Phase 10
  thành workflow Management thật có authorization: allocate stocked quantity để
  lại cho sau, xem allocation history, và append reversal có audit kèm reason bắt
  buộc; không bao giờ sửa/xóa allocation history;
- triển khai đường correction allocation được cho phép tường minh theo
  PROJECT_PROFILE §8.12 / §18 khi một correction có thể vượt giới hạn remaining
  demand thông thường; đó là intent đặc quyền, có audit, riêng biệt, không bao
  giờ là nới lỏng allocation Stockroom thường ngày;
- triển khai workflow **AssignedRoute adjustment** còn thiếu cho một QuantityFlow
  `PLANNED` đã chọn (PROJECT_PROFILE §8.10 / §17): chỉ user có quyền, reason
  tường minh, giữ route state cũ trong audit history, chỉ flow đã chọn, không
  đụng Route Template và immutable actual Movement history;
- gắn identity User đã authenticate vào audit record Management/Admin và các
  action lifecycle của Machine nơi canonical actor linkage áp dụng; Movement
  production của Scan Station tiếp tục dùng Worker identity từ Phase 13;
- năng lực correction rộng hơn của Manager/Admin dùng command có kiểu và có
  audit; Phase 14 không tạo đường edit/delete chung nào cho PartMovement history.

Một route deviation đã ghi trên Movement `TRANSFERRED` thật không bị nhân đôi chỉ
để tạo thêm một deviation event. `ROUTE_ADJUSTED` audit một thay đổi AssignedRoute
có thẩm quyền về sau; actual Movement history vẫn là nguồn chuẩn.

## Phase 15 — File-Based Work Order Import

- import idempotent;
- validate từng row;
- báo partial failure rõ;
- không phụ thuộc ERP.

## Phase 16 — Deployment, Production Hardening, and Admin Maintenance

Yêu cầu vận hành và hướng theo nền tảng nằm trong
[`DEPLOYMENT.md`](DEPLOYMENT.md). Hướng chạy repo hiện tại như staging nội bộ
có giới hạn không làm phase này trở thành complete.

- backup;
- migration;
- HTTPS/internal access;
- observability;
- rollback;
- reconciliation check;
- pilot deployment;
- hardening database role production, gồm cả các hạn chế UPDATE/DELETE dự kiến
  của application role trên production/audit history append-only;
- **administrative Movement-history archival/purge maintenance** theo
  PROJECT_PROFILE §28: retention theo thời gian cấu hình, select-by-cutoff →
  lossless archive export → verify → purge đúng các row đã archive và đã verify,
  qua một maintenance path riêng có privilege. Giữ trọn chain reversal/reference
  như `reverses_movement_id` và atomic-command group để không retained Movement
  nào reference row bị purge. Có policy/size threshold/manual trigger, scope
  preview, mandatory reason và full audit. Runtime bình thường vẫn append-only;
  không xây full retention engine trước phase này.

Hard deletion của PartNumber master **không** phải maintenance operation của
Phase 16. Nó thuộc Phase 13 Management → Part Numbers và chỉ xóa metadata.

## Deferred

- ERP synchronization;
- offline scan synchronization, không thuộc MVP; disconnected vẫn block write;
- WebSocket/SSE push connectivity; cơ chế đã duyệt là event + health polling;
- advanced analytics;
- speculative automation;
- ERP/MES feature rộng ngoài PartFlow.
