# Roadmap triển khai PartFlow

> **Bản gốc chuẩn:** [`IMPLEMENTATION_ROADMAP.md`](IMPLEMENTATION_ROADMAP.md).
> Baseline upstream: commit `f96bf09` (Production Board — merged quantity theo mọi nhánh lineage).
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

- Đặc tả project chuẩn là `PROJECT_PROFILE.md` v22. V22 chốt: receipt của Scan
  Station không bao giờ join quantity đang có — nó tạo Quantity Flow riêng sau
  một explicit confirmation, và `Combine quantities` vẫn là merge duy nhất (§14,
  giải quyết open decision 3 cũ của §32). V21 cho phép sửa có giới hạn
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
  number. Badge được khớp chính xác sau khi chuẩn hóa (trim, uppercase) như barcode không có prefix `PF:`; format
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
  `audit_events` (Phase 13 slice 2 về sau audit các write Department, Area, Operation, Scan Station và định dạng Asset Tag; slice 2b audit các write cấu hình Machine), không Worker/User, không full Administration.
- **Phase 4** đã triển khai end to end Work Order intake và production release:
  Work Order/demand save transaction riêng, PartNumber create-or-reuse (chỉ tạo mới từ Phase 13 slice 7) và label (PN control trên demand line được đổi đích sang `Edit Part Number` ở Phase 13 slice 7),
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
  Undo preview/final confirmation. Reason khi được cấu hình do Phase 13 slice 6
  (Undo reason policy) bổ sung; authorization chờ Phase 14.
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
- **Phase 10.5**: đã triển khai end to end — Application command
  `app/application/intake.py` kèm read model, `POST /api/scan-stations/{id}/receipts`
  và PN resolution mở rộng, cùng wizard `Receive Quantity` ba view thật trong Scan
  Station: một canonical PN không còn active Work Order Demand nay được RECEIVED
  tại production station — kể cả PN gặp lần đầu — với internal Work Order
  blank-number được tạo hoặc reuse, WorkOrderDemand, QuantityFlow, AssignedRoute
  snapshot cho `PLANNED` và immutable Movement `RECEIVED` commit trong một
  transaction. Receipt KHÔNG BAO GIỜ join quantity đang có: bên cạnh active
  quantity của PN nó tạo Quantity Flow RIÊNG, và chỉ sau explicit confirmation
  của operator (PROJECT_PROFILE v22 §14, chốt open decision 3 cũ của §32). Không
  cần migration: shape check của `RECEIVED` đã cho phép Scan Station identity và
  reason.
- **Phase 11**: đã triển khai **Production Board**, **Area Board**, **PN
  Tracking** và breakdown **Assigned now** theo PN của Management → Machines, và
  đã audit (2026-09-13) đối chiếu PROJECT_PROFILE §21, GUI_DESIGN §5–§7 và
  roadmap này, và hoàn tất bằng expected-duration monitoring theo PROJECT_PROFILE
  §17 "Expected duration hiệu lực của một position" (chốt 2026-09-14 — snapshot
  của Assigned Route Step hiện tại thắng Operation default sống, elapsed là chính
  `since` của position, chỉ advisory; xem mục Phase 11). Backend `app/application/production_board.py` trên
  `GET /api/production-board` derive board toàn Department từ projection vị trí
  hiện tại và Movement history: mọi PN có active quantity trong Area của
  Department (hoặc stocked quantity kèm demand còn mở), phân bổ theo Area /
  Machine / External activity với holding state derive (`MACHINE` / `QUEUE` /
  `PROCESSING` / `DONE` / `STOCKED`), timestamp vào vị trí cố định (`occurred_at`
  của effective position-bearing Movement, lấy cũ nhất trong nhóm; quantity đã
  merge đọc qua mọi nhánh lineage nên dated theo entry cũ nhất của cả khối merge
  và chỉ nêu Machine khi các nhánh đồng nhất), stocked và
  scrapped từ effective `STOCKED` / `SCRAPPED`, demand context theo canonical
  demand ordering (PROJECT_PROFILE §18 — demand đầu tiên quyết định Hot rank, due
  date và received date của row; Work Order đã complete không bao giờ cấp
  metadata cho row), tổng Department, theo đúng canonical demand ordering đó
  (stocked không phải một tầng sắp xếp riêng). Frontend thật
  (`src/api/production-board.ts`, `views/production-board/ProductionBoardView.tsx`,
  `board-feed.ts`): Department do server resolve (một Department active duy nhất,
  hoặc `?department=<id>` trên URL của màn hình), auto-refresh định kỳ với một
  request in flight, giữ rows hoàn chỉnh cuối cùng kèm trạng thái `Feed stale —
  reconnecting` mỗi khi chưa có board hoàn chỉnh trên màn hình — refresh lỗi,
  kết nối không khỏe, và cả load đầu đang chạy hoặc đã lỗi — refresh ngay khi
  kết nối trở lại,
  các state loading / error có Retry / empty dưới header luôn hiển thị, và giữ
  nguyên presentation GUI_DESIGN §5 (kiosk, pagination + rotation, auto scale,
  điều hướng tay, dwell / countdown / Total Days derive từ UI clock chung).
  **Area Board** (`app/application/area_board.py` trên `GET /api/area-board`,
  `views/area-board/AreaBoardView.tsx`) là view monitoring thứ hai trên cùng nền
  tảng và cố ý KHÔNG phải một representation thứ hai của Area: một read trả về
  các Area active của Department mang CHÍNH model monitoring Scan Station đang
  đọc (`scan_station.area_inventory` qua schema response dùng chung
  `app/api/area_inventory.py`), cộng Operation active của Area và stocked line
  của terminal Stockroom kèm allocation và demand context MỞ của PN (PN đã stock
  mà vẫn đang được làm cho demand mở thì nêu demand đó, như row stocked-only của
  Production Board) — scrapped theo PN trong Area thuộc về chính model inventory
  dùng chung, nên row `In this Area now` của Scan Station mang cùng dòng `{n}
  scrapped` với board; All Areas overview và per-Area detail là hai presentation của cùng một trả lời, render
  qua component chung và mapping client chung (`api/area-inventory.ts`,
  `views/area-presentation.ts`), với đúng hành vi polling / stale-feed của board
  (`views/monitoring-feed.ts`). Model chung tách bạch HAI câu hỏi về cùng một
  quantity: PROVENANCE (demand mà quantity được release cho — dialog thao tác
  và recap của Scan Station nêu, audit giữ, không bao giờ là row monitoring) và
  MONITORING context (`allocations.open_demand_context` — PN đang
  được làm CHO cái gì, các OPEN demand theo canonical order, nguồn của mọi Hot
  rank / due date / Job Numbers trên row monitoring). Model còn mang timestamp
  vào Area và Machine ĐÃ HOÀN THÀNH quantity finished — chính Machine chứ không
  chỉ id, nên Machine retired vẫn nêu được nơi hoàn thành — cả hai derive qua
  `projections.effective_positions`. Overview theo PN (một row mỗi Part Number
  trong một Area, gộp các quantity), detail theo từng quantity thao tác được.
  **PN Tracking** (`app/application/tracking.py` trên `GET /api/tracking`,
  `GET /api/tracking/detail` và `GET /api/tracking/movements`;
  `src/api/tracking.ts`, `views/tracking/TrackingView.tsx`) là read model thứ ba
  trên cùng nền và là giao diện quản lý chính (PROJECT_PROFILE §21, GUI_DESIGN
  §7): list theo PN — mọi PN có lịch sử production hoặc open demand, search theo
  PN / WO Number / Job Number và lọc theo Area, Operation, Machine, Request Type,
  Hot only, status derive và due window hoàn toàn server-side, theo canonical
  demand order với offset paging — mang open demand context, distribution hiện
  tại, active / stocked / scrapped quantity, next due date và status derive của
  mỗi PN; và detail read-only — master tùy chọn kèm barcode derive, open demand
  với released / allocated / shortage, current quantity theo Area / Machine qua
  CÙNG derivation branch-aware của các board, stocked quantity kèm active
  allocation, reconciliation §11, mọi Quantity Flow với lineage, PLANNED snapshot
  (done / current / future, deviation đã confirm, off-route) hoặc FLOATING actual
  trace derive từ Movement history (repeated Area, Repair, prefix kế thừa từ
  split qua toàn bộ single-parent ancestry), Scrap history (chính các event
  SCRAPPED, event đã undo đánh dấu), allocation history, và Movement history
  bất biến — tất cả phân trang: history theo thời gian ngược `(occurred_at DESC,
  id DESC)` trên keyset server resolve, các Quantity Flow theo một thứ tự bất biến có
  bound (mới nhất trước theo flow id) và allocation entry nối tiếp dưới page đầu trên
  cursor được validate, các page đã nối được đọc lại mỗi khi refresh làm đổi
  nội dung chúng hiển thị — original đã reverse vẫn hiển thị. View thật thay mock Phase 2
  (overlay detail modeless giữ nguyên) với polling / stale-feed chung, các state
  loading / error / empty và paging long-data có bound. Mục Phase 11 bên dưới
  ghi chi tiết trạng thái và ranh giới.
- **Phase 12**: đã triển khai end to end (backend và frontend) **Priority Management** và phase đã
  **ĐÓNG** (2026-10-04): product owner đã quyết OD1, OD3, OD5 và OD7 ngày 2026-10-04 (xem mục Phase 12),
  phần follow-up họ yêu cầu đã được triển khai và audit, và không còn quyết định owner nào đang mở.
  Persistence:
  migration `0013_phase12_priority` trước hết chạy **pre-check từ chối** — các giá trị
  `work_order_demands.priority_rank` khác NULL hiện có phải đúng là `1..N`, không rank nào < 1, trùng
  hoặc hở; nếu không migration raise, nêu từng demand id vi phạm cùng rank của nó, không rewrite gì và
  để database ở revision mà lần upgrade bắt đầu (`0012_phase11_tracking_index`, hoặc một revision cũ
  hơn khi có nhiều revision đang chờ, vì `alembic/env.py` chạy mọi revision đang chờ trong một
  transaction) — rồi thêm
  `ck_work_order_demands_priority_rank_positive`, UNIQUE không deferrable
  `uq_work_order_demands_priority_rank` (NULL vẫn phân biệt) và partial expression index
  `ix_audit_events_hot_list_device_event_id` trên `metadata['hot_list_change'] ->> 'device_event_id'`
  (`entity_type = 'WorkOrderDemand'`) phục vụ idempotency lookup. Hot list là tập Work Order Demand có
  rank (`priority_rank IS NOT NULL`; không flag, không table); invariant H1 luôn đúng — các rank đúng là
  `1..N`, unique và dense — và `priority_rank` có đúng hai writer — Hot list command và automatic /
  line-deletion removal của `app/application/hot_ranks.py` (OD1 / OD3 bên dưới); Work Order
  create/PATCH vẫn từ chối nó như input. Application (`app/application/hot_list.py`, rule thuần trong
  `app/domain/hot_list.py`): `POST /api/hot-list/changes` áp dụng một thay đổi single-entry (`ADD` ở
  bottom — áp dụng trực tiếp, `REMOVE`, `MOVE_UP` / `MOVE_DOWN`, `DRAG`, `UNDO` / `REDO`) đối chiếu
  `expected_order` mà manager đã xác nhận, và đánh số lại `1..N` trong một transaction dưới Hot advisory
  lock, rồi `FOR UPDATE` chỉ các row thay đổi cộng row được chèn theo id tăng dần (bản thân command không
  lấy lock Work Order hay Part Number; thứ tự lock toàn cục giữ wait graph không có chu trình được ghi
  ở OD1 bên dưới); rank được ghi bằng hai flush (xóa rồi gán) nên UNIQUE không bao giờ thấy trùng tạm
  thời — automatic và line-deletion removal ghi theo cùng cách; `expected_order` cũ là 409 `hot_list_changed` kèm entries hiện tại
  và không ghi gì; demand được chèn phải **eligible** — tồn tại, chưa có rank, Work Order của nó chưa
  completed và nó **active** theo nghĩa PROJECT_PROFILE §14 (`requested_quantity > allocated_quantity`)
  — được xét trên row đã lock và đọc lại; command idempotent theo `device_event_id` (fingerprint của
  action, expected order và new order: cùng id và fingerprint thì replay với 200 và `created: false`
  — kể cả sau khi cấu hình Department đã đổi, với `entries: null` khi không còn đúng một Department
  active — fingerprint khác là 409; audit row chính là idempotency record, tra trước và tra lại sau advisory
  lock). Audit (PROJECT_PROFILE §28): một row `audit_events` `UPDATED` `WorkOrderDemand` cho mỗi demand
  đổi rank, `before_data` / `after_data` giữ rank và `metadata.hot_list_change` giữ `device_event_id`,
  action, fingerprint, sequence cùng **identity snapshot** (demand id, Part Number, Work Order id và
  number), nên replay dựng lại `changes` chỉ từ audit row kể cả khi demand đã bị xóa; `actor_reference`
  vẫn NULL tới Phase 14. Read và API (`app/api/hot_list.py`): `GET /api/hot-list` (entries theo rank —
  entry inactive chỉ có thể tồn tại như entry tồn đọng từ trước thay đổi, xem OD1 bên dưới — mỗi entry có Work Order, request type, Job Number, requested / allocated /
  shortage / released quantity, due date, cờ `active` và quantity hiện tại của PN theo Area / Machine /
  state), `GET /api/hot-list/candidates?search=` (match không phân biệt hoa thường trên PN, Work Order
  Number hoặc Job Number, giới hạn 50 kèm cờ `truncated`) hoặc `?barcode=PF:PN:…` (toàn bộ demand
  eligible của PN đó, không giới hạn; copy từ chối riêng của view Priority), cả hai kèm
  `already_listed_count` (ký tự NUL trong một trong hai là 422 trước mọi query, cũng như demand id
  nằm ngoài miền `integer` của PostgreSQL trong command), và command. **Department gate**: mọi read và command resolve Department qua
  `production_board.resolve_department` — không có Department active là 404, nhiều Department là 409 với
  wording của Hot list, không ghi gì; cố ý không có tham số `department_id` (OD2 bên dưới). **Remove
  Hot line (OD3, quyết định 2026-10-04)**: `DELETE /api/work-orders/{id}/demands/{id}` trên line có rank
  mà mọi rule removal khác đều thỏa là 409 với `confirmation_required: true` và `hot_list_entry` (PN và
  rank hiện tại), không ghi gì, trừ khi request mang `?confirm_hot_removal=true` — khi đó line rời Hot
  list và bị xóa trong một transaction; Management → Work Orders bật ✕ của line đó và yêu cầu typed
  confirmation PN nêu rõ rank của nó. **Automatic removal (OD1, quyết định 2026-10-04)**: một allocation
  confirmation hoặc một Work Order save hạ quantity làm demand có rank trở thành inactive thì đưa nó
  khỏi Hot list trong cùng transaction (chi tiết ở OD1 trong mục Phase 12). Frontend: view Management → Priority thật
  (`src/api/hot-list.ts`, `views/priority/PriorityView.tsx`, `hot-history.ts`) thay mock Phase 2
  (`src/mocks/priority.ts` đã xóa; view ship từ `src/app/real-views.ts`) — list chỉ render từ response
  của server, confirmation đổi order hiển thị snapshot trước / sau, Remove có confirmation riêng,
  drag-and-drop và Move Up / Move Down, Add dialog một ô (search phía server, hoặc scan `PF:PN:` được
  resolve khi Enter), entry **inactive** (Work Order completed, hoặc line đã allocate đủ) chỉ có thể tồn tại như entry
  tồn đọng từ trước thay đổi (OD1 bên dưới) — view vẫn gắn cờ nó và nó vẫn remove / move được —, Undo / Redo **session** không giới hạn (PROJECT_PROFILE §21 mục 9)
  là stack phía client của các intent single-entry được rebase lên list hiện tại, mọi write bị chặn khi
  disconnected, đang chạy, outcome chưa rõ hoặc khi một lần reload đã yêu cầu chưa trả lời, và outcome
  chưa rõ cho phép retry tường minh đúng payload và key cũ, hoặc Reload list (bị vô hiệu hóa khi
  disconnected, nên retry cùng key không bao giờ bị mất vì một lần đọc không thể thành công). Board,
  Tracking và Scan Station đọc `priority_rank` không đổi. Test: ở baseline trước follow-up, backend gate hoàn tất với 777 test pass
  (`test_phase12_schema.py`, `test_hot_list_rules.py`, `test_hot_list_api.py` gồm bốn test lock /
  concurrency) và frontend gate với 796 pass; follow-up thêm các test removal, delete-confirmation,
  lock-order và race, và sau các sửa lỗi của audit follow-up, backend gate hoàn tất với 812 test pass và frontend gate với 808. Phase 12 đóng trên audit đó
  (2026-10-04), audit gồm cả audit đóng phase đối chiếu PROJECT_PROFILE §21 Priority Management mục 1–11
  và GUI_DESIGN §8 / §11.2.
- **Phase 13** (Full Administration and Production Identity Configuration): **ĐANG TRIỂN KHAI** — slice 1, 2, 2b, 2c, 3, 4, 5, 6, 7 và 8 đã triển khai: **Workers registry** (slice 1, end to end, backend và frontend, tính đến 2026-10-04), **audit cấu hình cho các write môi trường Phase 3.5** (slice 2, chỉ backend, tính đến 2026-10-05) **audit cấu hình Machine, hai bản sửa lost-race và PN CHECK độc lập collation** (slice 2b, chỉ backend, tính đến 2026-10-05) **parent-activity lock** của các write môi trường và Machine (slice 2c, chỉ backend, tính đến 2026-10-05) **Worker ID mode theo Area cùng Worker identity trên production record** (slice 3, backend và frontend, tính đến 2026-10-05) và **Worker Session, timeout policy cùng runtime đăng nhập của Scan Station** (slice 4, backend và frontend, tính đến 2026-10-05) và **badge-confirmation gate cho `DONE`, `QUEUE` và Undo cùng việc bật Scanned session** (slice 5, backend và frontend, tính đến 2026-10-05) và **Undo reason policy** (slice 6, backend và frontend, tính đến 2026-10-05) và **quản lý Part Numbers cùng việc đổi đích Edit Part Number** (slice 7, backend và frontend, tính đến 2026-10-05) và **quản lý Planned Routes** (slice 8, backend và frontend, tính đến 2026-10-06); mọi bullet Phase 13 khác vẫn đang chờ (xem mục Phase 13). Persistence: migration `0014_phase13_workers` (down revision `0013_phase12_priority`) tạo bảng `workers` — `id` identity, `name` bắt buộc và **không unique**, `badge_barcode` lưu ở dạng chuẩn hóa (`uq_workers_badge_barcode` UNIQUE trên **mọi** Worker, gồm cả inactive, và `ck_workers_badge_barcode_canonical`: không rỗng, đã trim, UPPERCASE, tối đa 128 ký tự, nằm ngoài namespace `PF:` — nên UNIQUE thường đã không phân biệt hoa/thường), avatar tùy chọn nằm ngay trên row (`avatar_image` `bytea`, `avatar_image_type`, `avatar_image_updated_at`, cùng các CHECK giữ ba column hoặc có đủ hoặc không có, type `image/png` / `image/jpeg` / `image/webp` và kích thước từ 1 byte đến 2 MiB), `is_active` và timestamp cấu hình — đồng thời mở rộng các CHECK của `audit_events` với event type `DELETED` và entity type `Worker` (chưa có writer nào của `DELETED`). Downgrade TỪ CHỐI và không bao giờ xóa dữ liệu: row audit `Worker` hoặc `DELETED` làm CHECK hẹp được tạo lại thất bại, và bảng `workers` không rỗng bị từ chối tường minh (chỉ dành cho database development và test dùng một lần). Migration `0015_phase13_badge_check` tạo lại `ck_workers_badge_barcode_canonical` với các mệnh đề trim và uppercase chạy dưới collation `"C"`: bảng case của glibc trong collation database không khớp với Python `str.upper()` ở một số code point (`ɤ`), nên một badge mà domain rule đã chấp nhận có thể vi phạm CHECK của 0014 và thành lỗi 500; giờ CHECK chỉ kiểm tra ASCII và không bao giờ phụ thuộc libc của OS, còn uppercase Unicode đầy đủ vẫn do domain rule sở hữu (không rewrite dữ liệu; downgrade khôi phục CHECK của 0014 và từ chối khi còn badge mà chỉ 0015 chấp nhận). `models.py` khai báo cùng các object đó, phần metadata parity ở head đã chuyển sang `tests/test_phase13_schema.py` còn `tests/test_phase12_schema.py` được pin ở `0013_phase12_priority`. Domain và Application: `app/domain/worker_badge.py` (`normalize_badge_barcode` — trim, UPPERCASE, từ chối giá trị rỗng, dài hơn 128 ký tự và `PF:`), `app/application/images.py` (một validation ảnh dùng chung: body rỗng bị từ chối 422, quá 2 MiB bị 413, và type khai báo không phải PNG, JPEG hay WebP hoặc không bằng type mà chính các byte mang theo bị 415; snapshot audit chỉ giữ digest — type, size, SHA-256 — không bao giờ giữ byte) và `app/application/workers.py` (list, create, update name / badge / active, avatar set / remove / get, cùng thao tác đọc thuần `resolve_badge`, được gọi bởi `POST /api/scan-stations/{id}/badge-scans`, mà từ slice 4 đăng nhập, chuyển hoặc làm mới Worker Session trong Area Scanned-session; từ slice 5 các command `DONE`, `QUEUE` và Undo cũng resolve confirming badge của final gate). API: `GET /api/workers` (gồm cả Worker inactive), `POST /api/workers`, `PATCH /api/workers/{id}` và `PUT` / `DELETE` / `GET /api/workers/{id}/avatar`; request body từ chối field lạ, mọi response mang badge chuẩn hóa đã lưu và `avatar_updated_at` (cache version của avatar, `null` = không có avatar) và không bao giờ mang byte ảnh, và không có route `DELETE` Worker. Avatar đi theo raw request body với `Content-Type` của chính ảnh (không multipart, không thêm dependency): `Content-Length` khai báo vượt 2 MiB bị từ chối trước khi đọc và body streaming bị từ chối ngay khi vượt giới hạn; `GET …/avatar` trả `ETag` mạnh suy từ `avatar_image_updated_at`, `Cache-Control: private, no-cache` và 304 khi `If-None-Match` khớp. Badge trùng là 409 nêu tên người đang giữ (UNIQUE vẫn là nguồn quyết định khi race). Mọi write có hiệu lực — create, đổi profile, avatar set / replace / remove — append đúng một row `audit_events` (entity `Worker`, `actor_reference` NULL cho đến Phase 14) trong cùng transaction, dưới row lock của Worker với Worker đã có, nên `before_data` của mỗi row audit là bản committed liền trước trong cùng facet (profile `{name, badge_barcode, is_active}` hoặc digest avatar); write bị từ chối và no-op không append gì. Frontend: Administration → Workers là section thật (bảng có avatar, name, badge barcode và status; dialog `New Worker` / `Edit Worker`; badge được lưu bằng chữ in hoa kèm preview `Saved as:` trực tiếp; upload và xóa avatar; công tắc Active khi sửa; không có delete; Cancel, Escape và backdrop bị bỏ qua khi đang lưu, nên editor chỉ đóng và danh sách chỉ reload sau khi các write đã xong), cùng component dùng chung `WorkerAvatar` (hiện initials khi không có avatar), helper dùng chung `components/image-upload.ts` (sniff type thật, resize cạnh dài nhất xuống 1024 px, từ chối kết quả trên 2 MiB) và `apiUpload` trong `api/client.ts` (đường raw-body duy nhất); write bị chặn khi disconnected. Validation (môi trường Docker Compose, working tree slice 1 trên HEAD `9f466fa`): backend `ruff format --check`, `ruff check` và `mypy app tests` sạch và `pytest` 912 test pass; frontend `format:check`, `lint` và `typecheck` sạch, Vitest 835 test pass trong 43 file, và `npm run build` cùng kiểm tra production-boundary pass. Không có gì khác thay đổi: không đổi hành vi Scan Station, Area, Movement hay production command, và preview Worker sessions chỉ-development vẫn còn (slice 4 đã gỡ). **Slice 2 — audit cấu hình cho các write môi trường Phase 3.5.** Migration `0016_phase13_environment_audit` (down revision `0015_phase13_badge_check`) chỉ mở rộng `ck_audit_events_entity_type` với `Department`, `Area`, `Operation`, `ScanStation` và `MachineAssetTagConfig` (định dạng Asset Tag); CHECK của event, index và bảng không đổi, không backfill, và downgrade của nó TỪ CHỐI, không bao giờ xóa lịch sử (một row audit môi trường làm CHECK hẹp hơn được tạo lại bị fail). `AuditEntityType` và CHECK trong `models.py` phản chiếu năm giá trị này, và một schema test xác nhận CHECK trong database nêu đúng các giá trị của enum. Mỗi create hoặc update có hiệu lực trong `app/application/environment.py` (Department, Area, Operation, Scan Station và định dạng Asset Tag: 9 service, 10 đường write) append đúng một row `audit_events` `CREATED` hoặc `UPDATED` (`actor_reference` và `metadata` NULL cho đến Phase 14) trong cùng transaction, kèm snapshot before/after tường minh của các field cấu hình (SLICE1_DATA_MODEL §16; thời lượng Operation tính bằng giây; `next_sequence` không bao giờ được audit). `entity_id` là id nội bộ dạng text, trừ `ScanStation` (Station ID) và `MachineAssetTagConfig` (`"1"`, singleton). Mỗi update khóa row mình sửa trước, theo đúng mode mà UPDATE của chính nó đã dùng (`FOR NO KEY UPDATE`; `FOR UPDATE` cho Area PATCH yêu cầu deactivate), nên `before_data` của mỗi row là predecessor đã commit của entity; write bị từ chối, race thua ở flush hoặc COMMIT và no-op (giá trị giống hệt) không append gì, và một PATCH nhiều field chỉ là một row. Machine nằm ngoài `audit_events` ở slice 2 (slice 2b thêm chúng). Không đổi hình dạng API, status, message, ánh xạ conflict hay frontend. Validation (môi trường Docker Compose, slice 2 cùng các bản sửa audit, working tree trên HEAD `0984d43`): backend `ruff format --check`, `ruff check` và `mypy app tests` sạch và `pytest` 960 test pass (module mới `tests/test_environment_audit_api.py` có 27 hàm test, 36 case được collect); frontend `format:check`, `lint` và `typecheck` sạch, Vitest 836 test pass trong 43 file, và `npm run build` cùng kiểm tra production-boundary pass. **Slice 2b — audit cấu hình Machine, hai bản sửa lost-race và PN CHECK độc lập collation.** Migration `0017_phase13_machine_audit` (down revision `0016_phase13_environment_audit`) chỉ mở rộng `ck_audit_events_entity_type` với `Machine`; CHECK event, index và bảng không đổi, không backfill, và downgrade của nó TỪ CHỐI (một row audit `Machine` làm CHECK hẹp hơn được tạo lại bị fail). Migration `0018_phase13_pn_check_collation` tạo lại bốn CHECK canonical PN (`part_numbers`, `work_order_demands`, `quantity_flows`, `work_order_allocations`) với vế uppercase và whitespace dưới collation `"C"`, vì bảng case của libc bất đồng với `str.upper()` của Python ở 27 code point (`ɤ`) và một PN mà domain rule chấp nhận từng fail CHECK thành lỗi 500 không được dịch; CHECK nay từ chối chữ thường ASCII, whitespace ASCII và chuỗi rỗng độc lập với OS, còn việc uppercase Unicode đầy đủ và từ chối whitespace Unicode vẫn do `normalize_part_number` sở hữu (không rewrite dữ liệu; downgrade từ chối khi còn giá trị chỉ `0018` chấp nhận). Audit Machine: mọi write cấu hình Machine có hiệu lực — tạo, sửa metadata và maintenance context, maintenance start và clear, bản Save draft mà retirement áp dụng, và việc đổi tên, đổi Area hoặc clear maintenance của một reactivation — append đúng một row `audit_events` `CREATED` hoặc `UPDATED` (entity `Machine`, `entity_id` là id nội bộ dạng text, `actor_reference` NULL) trong cùng transaction, kèm snapshot tường minh gồm `area_id`, `name`, `asset_tag`, `description`, `manufacturer`, `model`, `serial_number`, `installed_on`, `notes`, `maintenance_since` (UTC ISO-8601), `maintenance_note` và `maintenance_expected_return`; `retired_on` (do lifecycle event sở hữu), `state_changed_at` (tuổi trạng thái runtime do production command dịch chuyển), `created_at` và `updated_at` bị loại. Retirement hoặc reactivation thuần, không có chênh lệch cấu hình, không append row audit; khi có chênh lệch, row của nó có `metadata.machine_lifecycle_event_id` trỏ tới lifecycle event. Mọi write Machine khóa row của mình trước (`FOR NO KEY UPDATE`; retirement giữ `FOR UPDATE`; việc tạo không khóa gì trên `machines`), nên mỗi `before_data` là predecessor đã commit; production command không bao giờ ghi row audit; write bị từ chối, race thua và no-op không append gì. Kết quả lost-race (không đổi hình dạng API; race của Department và Machine dùng lại message hiện có, còn race lưu Asset Tag đầu tiên thêm message mới S2b-OD7): đổi tên Department thua `uq_departments_name` nay là 409 (`update_department` đọc trước khi gán), lần lưu định dạng Asset Tag đầu tiên đồng thời thua `pk_machine_asset_tag_config` nay là 409, và các lost-race của write admin Machine trả 409 với message hiện có. Không đổi frontend. Validation (môi trường Docker Compose, working tree slice 2b trên HEAD `1c1be2a`; `alembic heads` trả `0018_phase13_pn_check_collation`): backend `ruff format --check`, `ruff check` và `mypy app tests` sạch và `pytest` 1001 test pass (module mới `tests/test_machine_audit_api.py` có 16 hàm test, 19 case được collect); frontend `format:check`, `lint` và `typecheck` sạch, Vitest 836 test pass trong 43 file, và `npm run build` cùng kiểm tra production-boundary pass. **Slice 2c — race parent-activity.** Một write cấu hình của child giờ được tuần tự hóa với việc deactivate đồng thời parent của nó. Bảy đường child và parent của chúng là: tạo Area và kích hoạt Area (parent Department), cùng tạo Operation, tạo Scan Station, đổi Area của Scan Station, tạo Machine và reactivate Machine, tại chỗ hoặc sang Area khác (parent Area). Mỗi đường khóa dòng parent `FOR SHARE`, đọc lại nó dưới khóa **trước** khi đánh giá cờ active, và giữ khóa đến COMMIT (`require_active_area` và các lần đọc Department trong `app/application/environment.py`; `app/application/machines.py` thừa hưởng khóa và không đổi code). `FOR SHARE` xung đột với mọi UPDATE của dòng parent, nên mọi đường deactivate đều tuần tự hóa với child dù nó lấy khóa nào, và không bao giờ xung đột với kiểm tra foreign key (`FOR KEY SHARE`) hay với write child khác dưới cùng parent. Hai thứ tự có thể xảy ra mỗi thứ có một kết quả tuần tự duy nhất: child thua deactivate parent sẽ chờ rồi nhận 409 sẵn có mà không ghi gì (không có dòng child, dòng audit, lifecycle event hay bộ đếm Asset Tag bị tiêu thụ); child commit trước thì deactivate Department trả 409 sẵn có "This Department still has active Areas", còn deactivate Area vẫn tiếp tục (200), cùng kết quả như hai write thực hiện lần lượt. Reactivate Machine tại chỗ giờ cũng chờ một lệnh production đang giữ Area của nó. Không đổi schema, migration, hình dạng API, status, message hay frontend; `alembic heads` vẫn là `0018_phase13_pn_check_collation`. Validation (môi trường Docker Compose, working tree slice 2c trên HEAD `811b268`; `alembic heads` trả `0018_phase13_pn_check_collation`): backend `ruff format --check`, `ruff check` và `mypy app tests` sạch và `pytest` 1045 test pass (module mới `tests/test_environment_parent_activity_api.py` có 7 hàm test, 28 case được collect; trên các service chưa sửa 20 trong 28 case fail và 8 pass, 8 case đó là các guard rằng khóa không bao giờ chờ `FOR SHARE` hay `FOR KEY SHARE` và guard hồi quy của việc đổi Area Scan Station; `tests/test_environment_api.py`, `tests/test_environment_audit_api.py`, `tests/test_machines_api.py` và `tests/test_machine_audit_api.py` pass không đổi, 123 test pass); frontend, không bị slice này thay đổi, chạy lại: `format:check`, `lint` và `typecheck` sạch, Vitest 836 test pass trong 43 file, và `npm run build` cùng kiểm tra production-boundary pass. **Slice 3 — Worker ID mode theo Area và Worker identity trên production record.** Migration `0019_phase13_worker_identity` (down revision `0018_phase13_pn_check_collation`; `alembic heads` trả về nó) thêm `areas.worker_identification_mode` (`DISABLED` / `FIXED` / `SCANNED`, NOT NULL, default `DISABLED`, `ck_areas_worker_identification_mode`) và `areas.fixed_worker_id` (FK tới `workers`, `ck_areas_fixed_worker_shape`: Fixed Worker tồn tại đúng khi mode là `FIXED`), cùng các FK nullable `part_movements.worker_id` và `work_order_allocations.allocated_by_worker_id`, mỗi cái có CHECK rằng Worker chỉ được ghi cùng một Scan Station (`ck_part_movements_worker_requires_station`, `ck_work_order_allocations_worker_requires_station`). Không có index, không backfill (lịch sử giữ identity NULL, không bao giờ đoán) và không đổi trigger; các Area hiện có thành `DISABLED`; lần quét bảng duy nhất là hai lần validate CHECK trên `part_movements` và `work_order_allocations` (mọi row NULL). Downgrade TỪ CHỐI và không bao giờ xóa dữ liệu: nó fail khi còn Movement hoặc allocation mang Worker hoặc còn Area không phải `DISABLED`. Application: tạo và sửa Area (`app/application/environment.py`) đặt mode và Fixed Worker — `SCANNED` bị từ chối (422) cho đến khi có các badge gate (được chấp nhận từ slice 5), rời `FIXED` thì xóa Worker, Worker inactive hoặc không tồn tại bị từ chối, row Worker bị khóa `FOR SHARE`, và một lần sửa Area không liên quan không bao giờ đánh giá lại Fixed Worker của nó — và hai key này nhập vào snapshot audit của Area; `workers.update_worker` từ chối (409, nêu tên các Area) deactivate một Worker đang là Fixed Worker của bất kỳ Area nào. `app/application/station_identity.py` mới (`resolve_station_identity`, `stamp_movements`, `worker_identification`, `badge_scan`) là nơi duy nhất áp dụng mode: mọi Scan Station command — 14 entry point (receipt, transfer, Repair, stocking, Machine assignment và release, Area completion có hoặc không có Machine, merge, scrap, quantity addition, Undo, station allocation và reversal của nó) — resolve identity sau các lock và check sẵn có và sau idempotency fast path, khóa Fixed Worker `FOR KEY SHARE` ở cuối cùng (để việc deactivate Worker được tuần tự hóa với mọi command đang ghi Worker đó), từ chối Fixed Worker inactive hoặc Area `SCANNED` dựng bằng fixture với 409 và không ghi gì, và đóng dấu cùng một Worker lên mọi row của command; các row Management (allocation và reversal không có station, `RECEIVED` của Phase 4 release) và toàn bộ lịch sử cũ giữ NULL. Request và fingerprint không mang field identity, nên command đã commit replay với identity nó đã ghi, bất kể mode của Area sau đó đổi thành gì. Thứ tự khóa Worker không có chu trình với các parent lock của slice 2c (tạo Area: Department `FOR SHARE`, rồi Worker `FOR SHARE`; cập nhật Area: row Area, rồi Worker, rồi Department khi activation), đóng follow-up S2c-F1. API: `worker_identification_mode` và `fixed_worker_id` của Area; `worker_identification` (`mode` và `fixed_worker`) trong station context; `POST /api/scan-stations/{id}/badge-scans`, một thao tác đọc không ghi gì, trả `NOT_USED_IN_AREA` hoặc `UNKNOWN` kèm mode của Area (409 cho Area `SCANNED` cho đến slice session); `worker` của Undo preview (Worker đã ghi của command gốc) và `reversed_by` do server tính; `worker` của Movement trong Tracking và `worker` của route-deviation. Frontend: Administration → Areas hiện cột Worker ID mode và sửa mode cùng Fixed Worker (`Scanned session` hiển thị nhưng không chọn được (chọn được từ slice 5); tên Worker không unique nên nhãn select là `{name} · {badge}`); header Scan Station render Worker pill thật (avatar, tên, `Fixed Worker`) ở Area Fixed và không có pill ở Area Disabled; mọi confirmation summary production hiện row `Worker` trước `Scan Station` (bỏ ở Area Disabled); station context được đọc lại ngầm ở mỗi lần scan PN hoặc Machine được resolve và mỗi khi `DONE` / `QUEUE` của Machine card hoặc `DONE` của direct processing mở wizard (một lần đọc lại ngầm thất bại giữ nguyên station, dialog đang mở và pill như lần đọc cuối); ô scan mang placeholder GUI §4.4 (`Scan Part Number, Worker, or Machine barcode · Press Enter`, `Checking barcode…` khi đang kiểm tra badge); badge scan đi qua `badge-scans` và được trả lời bằng `Worker badge scans are not used in this Area` mà không đổi Last Scanned PN; summary Undo hiện `Worker` gốc và `Reversed by` từ preview của server; PN Tracking hiện `W: <name>` trong mô tả Movement và ` by <name>` ở ghi chú route-deviation. Validation (môi trường Docker Compose, working tree slice 3 trên HEAD `0bacffc`): backend `ruff format --check`, `ruff check` và `mypy app tests` sạch và `pytest` 1109 test pass (`tests/test_worker_identity_api.py` mới có 20 hàm test, 51 case được collect; `tests/test_phase13_schema.py`, `tests/test_environment_api.py`, `tests/test_environment_audit_api.py` và `tests/test_workers_api.py` được mở rộng); frontend `format:check`, `lint` và `typecheck` sạch, Vitest 866 test pass trong 44 file (`scan-station-worker.test.tsx` mới có 14), và `npm run build` cùng kiểm tra production-boundary pass. Không có gì khác đổi: sau slice 3 chưa có Worker Session, `scan_session_id`, badge modal hay badge gate, và preview Worker sessions chỉ-development vẫn còn (slice 4 bên dưới thêm Worker Session, `scan_session_id` và modal đăng nhập và gỡ preview, nhưng không thêm badge gate — các badge gate do slice 5 giao). **Slice 4 — Worker Session, timeout policy và runtime đăng nhập của Scan Station.** Migration `0020_phase13_worker_sessions` (down revision `0019_phase13_worker_identity`) tạo singleton `application_policy` (`id = 1`, được migration seed; `worker_session_timeout_minutes` mặc định 15, `ck_application_policy_worker_session_timeout_range` 1–720), thêm override theo Area nullable `areas.worker_session_timeout_minutes` (`ck_areas_worker_session_timeout_range`, 1–720) và tạo `worker_sessions` (`ScanSession` của PROFILE: `id` bigint identity, `station_id`, `area_id` lúc đăng nhập, `worker_id`, `started_at`, `expires_at`, `ended_at`, `end_reason`; vocabulary `end_reason` đóng `SWITCHED` / `EXPIRED` / `AREA_MODE_CHANGED` / `STATION_CHANGED` / `WORKER_DEACTIVATED`; CHECK: thời điểm kết thúc tồn tại đúng khi có lý do, expiry sau start, thời điểm kết thúc nằm trong cửa sổ session, và session `EXPIRED` kết thúc đúng tại expiry; partial UNIQUE index cho phép tối đa một session mở mỗi station; partial index open-by-Worker; và trigger cấm DELETE và TRUNCATE, chỉ cho `expires_at`, `ended_at` và `end_reason` của một session đang mở thay đổi). `part_movements.scan_session_id` nullable (lịch sử giữ NULL, không backfill) cùng `ck_part_movements_session_requires_worker` và FK ghép `(scan_session_id, worker_id, station_id)` tới Worker và station của session, nên một Movement không thể nêu session của Worker hay station khác; CHECK entity của `audit_events` thêm `ApplicationPolicy`. Upgrade validate CHECK và FK mới bằng một lần quét `part_movements`. Downgrade TỪ CHỐI và không bao giờ xóa dữ liệu: nó fail khi còn bất kỳ row `worker_sessions`, override Area nào hoặc timeout khác 15, và một row audit `ApplicationPolicy` làm CHECK entity hẹp hơn được tạo lại fail (chỉ dành cho database development và test dùng một lần). Application: `app/application/worker_sessions.py` mới sở hữu runtime — đăng nhập badge (`SIGNED_IN`), chuyển (`SWITCHED`, session trước kết thúc) hoặc làm mới (`REFRESHED`, cùng badge quét lại) dưới khóa station, lấy station `FOR UPDATE`, Area của nó `FOR SHARE` và Worker `FOR KEY SHARE`; sliding inactivity timeout (override của Area của station, nếu không thì default của policy) được đẩy tới từ session clock (`clock_timestamp()` đọc sau mọi khóa) bởi mỗi command Scanned-session hợp lệ, mỗi lần scan PN hoặc Machine resolve thành công (kể cả lần trả `NO_TRANSFERABLE_QUANTITY`) và mỗi lần quét badge của cùng Worker; từ chối hay scan không hợp lệ không bao giờ làm mới, và replay một command đã commit cũng không; session hết hạn bị mọi reader bỏ qua và được đóng lười là `EXPIRED` tại expiry bởi lần đăng nhập hoặc lần đóng do cấu hình kế tiếp. `station_identity` ghi session và Worker của nó trên mọi row của command Scanned-session và làm mới session trong transaction của chính command, hoặc từ chối bằng 409 `worker_session_required` mà không ghi gì (sau idempotency fast path, nên command đã commit vẫn replay). Đổi mode Area rời Scanned session (`AREA_MODE_CHANGED`), rebind hoặc deactivate Scan Station (`STATION_CHANGED`) và deactivate Worker (`WORKER_DEACTIVATED`) đóng các session mở bị ảnh hưởng trong cùng transaction; các closer khóa row theo thứ tự `id`. `app/application/policies.py` mới phục vụ `GET` / `PUT /api/policies/worker-sessions` (số phút nguyên chặt 1–720, nếu không thì 422, audit là `ApplicationPolicy` với `entity_id` `worker-sessions`; PUT không đổi gì thì không ghi gì); override theo Area là field của Area, ghi qua `POST` / `PATCH /api/areas` và audit là `Area`. API: `POST /api/scan-stations/{id}/badge-scans` nay trả `SIGNED_IN` / `SWITCHED` (kèm `previous_worker`) / `REFRESHED` trong Area Scanned-session, kèm `worker_session` (Worker, `started_at`, `expires_at` và `server_now` mà câu trả lời được đánh giá; session id không rời server), và `UNKNOWN` mà không ghi hay làm mới gì; `worker_identification` của station context mang `session` hợp lệ, và response resolve PN và Machine mang `worker_session`; response 409 `worker_session_required` mang cờ `worker_session_required`. Frontend: Administration → Worker sessions là thật — giá trị mặc định và override theo Area là cấu hình được lưu (số phút nguyên 1–720), và ba tùy chọn badge-confirmation nói rõ rằng chưa có (thật từ slice 5); preview chỉ-development `WorkerSessionsPreview.tsx` bị xóa. Scan Station hiện pill session với đếm ngược trực tiếp đã hiệu chỉnh theo clock của server, bật modal chặn `Worker sign-in required` / `Worker session expired` phía trên mọi dialog đang mở với draft được giữ, gửi lại nguyên request sau khi đăng nhập, đọc lại preview Undo khi Worker của session đang sống đổi lúc dialog Undo đang mở, và chỉ ở build development mới liệt kê badge của các Worker active thật làm demo badge qua module lazy được bảo vệ bởi `DEV` (kiểm tra production-boundary thêm sentinel `Demo badges`). Scanned session vẫn chưa chọn được trong Administration → Areas (chọn được từ slice 5). Validation (môi trường Docker Compose, working tree slice 4 trên HEAD `077000c`): backend `ruff format --check`, `ruff check` và `mypy app tests` sạch và `pytest` 1196 test pass (`tests/test_worker_sessions_api.py` mới có 36 hàm test, 66 case được collect; `tests/test_phase13_schema.py`, `tests/test_environment_audit_api.py`, `tests/test_scan_station_transfer_api.py`, `tests/test_worker_identity_api.py` và `tests/test_workers_api.py` được mở rộng); frontend `format:check`, `lint` và `typecheck` sạch, Vitest 890 test pass trong 45 file (`scan-station-session.test.tsx` mới có 18), và `npm run build` cùng kiểm tra production-boundary (11 mock sentinel) pass. Không có gì khác đổi: chưa có badge-confirmation gate và `SCANNED` vẫn bị Area service từ chối (cả hai do slice 5 giao). **Slice 5 — badge-confirmation gate cho `DONE`, `QUEUE` và Undo, và bật Scanned session.** Migration `0021_phase13_badge_confirmation` (down revision `0020_phase13_worker_sessions`) thêm `badge_confirm_done`, `badge_confirm_queue` và `badge_confirm_undo` vào singleton `application_policy` (boolean `NOT NULL`, server default true, nên row đã seed nhận true); downgrade TỪ CHỐI — không bao giờ xóa dữ liệu — khi có option đang tắt hoặc có row audit `ApplicationPolicy` ghi một option. Application: `app/application/station_identity.py` sở hữu quy tắc gate duy nhất `final_gate` — final gate của một sensitive action là `BADGE` đúng khi Area ở Scanned session và option của action đó bật, ngược lại là `QUESTION` — và station context báo form theo từng action là `worker_identification.final_gates` (client không tự suy ra). `DONE` (cả hai biến thể trên `/area-completions` — có `machine_id` ở Machine card, không có cho direct processing), `QUEUE` (`/machine-releases`) và Undo (`/undos`) nhận `confirming_badge`; `/machine-assignments` từ chối nó. Badge được kiểm tra hình dạng trước idempotency fast path và được đánh giá sau fast path, dưới các lock của command: badge ở nơi gate là câu hỏi bị từ chối bằng 409 `badge_confirmation_not_expected`, thiếu badge ở nơi gate là badge bị từ chối bằng 409 `badge_confirmation_required` (trước mọi xử lý session, nên ưu tiên hơn việc thiếu session), và badge không phải của Worker active bị từ chối bằng 422 `badge_not_recognized`, mỗi trường hợp không ghi gì; Worker active của badge được chấp nhận do chính command đăng nhập — open, switch hoặc refresh qua `worker_sessions.sign_in_locked`, lõi sign-in duy nhất dùng chung với `badge-scans`, dưới `FOR SHARE` trên Area của station — và được ghi, cùng session của nó, trên mọi row trong transaction của command. Badge không tham gia fingerprint của request, và command đã commit được replay bất kể badge, mode hay option lúc retry (replay không bao giờ sign in hay refresh). `GET` / `PUT /api/policies/worker-sessions` mang ba option; PUT là partial merge dưới row lock (mỗi field tùy chọn, field bỏ qua giữ giá trị đã lưu; `null`, body rỗng hoặc field thừa là 422) và thêm một row audit `UPDATED` với snapshot bốn key cho mỗi thay đổi có hiệu lực; `POST` / `PATCH /api/areas` chấp nhận `SCANNED`. Frontend: Administration → Worker sessions hiện ba công tắc thật (mỗi lần click chỉ lưu option của nó, nên một lần đọc cũ không bao giờ ghi đè thay đổi của administrator khác); Administration → Areas cung cấp `Scanned session`; Scan Station mở badge gate (`scan-station-badge-gate.tsx`) cho `DONE`, `QUEUE` return và Undo khi server báo `BADGE`, giữ draft, selection và quantity ở mọi lần từ chối, đổi form gate theo đúng typed refusal chỉ định, và yêu cầu quét mới sau một lần badge bị từ chối; ở build development, gate liệt kê badge của các Worker active thật làm demo badge qua slot được bảo vệ duy nhất `scan-station-dev-badges-slot.tsx`. Validation (môi trường Docker Compose, working tree slice 5 trên HEAD `777c9c1`): backend `ruff format --check`, `ruff check` và `mypy app tests` sạch và `pytest` 1266 test pass (`tests/test_badge_confirmation_api.py` mới có 28 hàm test, 60 case được collect; `tests/test_phase13_schema.py`, `tests/test_environment_api.py`, `tests/test_environment_audit_api.py`, `tests/test_worker_identity_api.py` và `tests/test_worker_sessions_api.py` được mở rộng) và `alembic heads` báo `0021_phase13_badge_confirmation` là head duy nhất; frontend `format:check`, `lint` và `typecheck` sạch, Vitest 911 test pass trong 46 file (`scan-station-final-gate.test.tsx` mới có 18), và `npm run build` cùng kiểm tra production-boundary (11 mock sentinel) pass. Không có gì khác đổi: không có gate trên action nào khác và preview mock Scan Station chỉ-development vẫn còn. **Slice 6 — Undo reason policy.** Migration `0022_phase13_undo_reason_policy` (down revision `0021_phase13_badge_confirmation`) thêm `undo_reason_required` vào singleton `application_policy` (boolean `NOT NULL`, server default false, nên row đã seed nhận false và không có gì đổi lúc upgrade); downgrade TỪ CHỐI — không bao giờ xóa dữ liệu — khi option đang bật hoặc có row audit `ApplicationPolicy` của section `correction-permissions` (row `REVERSED` mang reason không bao giờ chặn nó: cột `reason` và CHECK của nó có trước revision này). Application: `GET` / `PUT /api/policies/correction-permissions` mang đúng một field (body `PUT` đúng `{undo_reason_required: boolean}`; field thiếu, không phải boolean hoặc field thừa là 422), write khóa singleton trước và thêm một row audit `UPDATED` (`ApplicationPolicy`, `entity_id` `correction-permissions`) cho mỗi thay đổi có hiệu lực — write no-op không ghi gì. Undo command (`POST /api/scan-stations/{id}/undos`) nhận `reason` tùy chọn (đã trim; trống là vắng; giá trị chứa U+0000 là 422), được ghi trên mọi row `REVERSED` và chỉ nằm trong request fingerprint khi có, nên mọi Undo đã commit trước slice này vẫn replay được. Khi policy bật, Undo không có reason bị từ chối bằng 409 `undo_reason_required` và không ghi gì: nó được xét sau idempotency fast path, sau re-check dưới lock và sau mọi state refusal, và trước bước resolve badge / session identity, nên Undo đã commit luôn replay bất kể policy lúc retry; policy được đọc chỉ theo cột (`policies.is_undo_reason_required`), không bao giờ qua entity singleton đã cache. Response của Undo mang `reason` đã ghi (cả khi replay) và read model `undo-preview` mang `reason_required` (một lần đọc; command xét lại). Frontend: Administration → Correction permissions thật cho một control — công tắc `Require a reason for every Undo` (default Off, lưu khi click, giá trị đã lưu được đọc lại) — và nói rõ role-based correction permission chưa cấu hình được; summary Undo của Scan Station hiện field `Reason` bắt buộc (hint `This reason will be included in the reversal history.`) bên dưới summary và trước final gate khi preview báo hoặc typed refusal của server chỉ định, giữ Confirm bị disable đến khi có chữ, giữ selection, draft và `device_event_id` khi gặp typed refusal (field khi đó hiện lời giải thích của server và lần Confirm kế tiếp mở lại gate), nhắc lại reason trong final gate, và không hiện field khi policy tắt. Reason hiển thị trong Tracking nhờ phần hiển thị reason `REVERSED` sẵn có. Validation (môi trường Docker Compose, working tree slice 6 trên HEAD `650c1a2`): backend `ruff format --check`, `ruff check` và `mypy app tests` sạch và `pytest` 1306 test pass (`tests/test_undo_reason_policy_api.py` mới có 24 hàm test, 32 case được collect; `tests/test_phase13_schema.py` được mở rộng) và `alembic heads` báo `0022_phase13_undo_reason_policy` là head duy nhất; frontend `format:check`, `lint` và `typecheck` sạch, Vitest 926 test pass trong 47 file (`scan-station-undo-reason.test.tsx` mới có 12), và `npm run build` cùng kiểm tra production-boundary (11 mock sentinel) pass. Không có gì khác đổi: chưa có scope theo role hay theo Area, chưa có reason category và chưa có role-based correction permission. **Slice 7 — Quản lý Part Numbers và đổi đích Edit Part Number.** Migration `0023_phase13_part_number_master` (down revision `0022_phase13_undo_reason_policy`) thêm vào `part_numbers` các chi tiết tùy chọn `name` (Name / Description trên GUI), `current_revision` và `erp_id` — free text nullable, được trim và giá trị trống lưu NULL, **không unique**, không giới hạn độ dài, không index — cùng ảnh PN nằm trên row (`image` `bytea`, `image_type`, `image_updated_at`, tức cache version), được bảo vệ bởi `ck_part_numbers_image_shape` (cả ba cột hoặc không cột nào), `ck_part_numbers_image_type` (`image/png`, `image/jpeg`, `image/webp`) và `ck_part_numbers_image_size` (từ 1 byte đến 2 MiB), cùng quy tắc với avatar Worker; không có foreign key (không bảng production nào tham chiếu `part_numbers`), không backfill và không đổi CHECK của `audit_events` (`DELETED`, `UPDATED` và entity `PartNumber` đã có). Downgrade TỪ CHỐI — không bao giờ xóa dữ liệu — khi còn row nào mang name, revision, ERP id hoặc ảnh. Application (`app/application/part_numbers.py`, `app/api/part_numbers.py`): `POST /api/part-numbers` nay **chỉ tạo mới** (201 kèm `name`, `current_revision` và `erp_id` tùy chọn; 409 khi chi tiết đã lưu đã tồn tại; field lạ và giá trị không phải text là 422, không ghi gì), còn create-on-first-use ở bước lưu Work Order và nhận hàng ở Scan Station giữ nguyên; `PATCH /api/part-numbers?number=` là cập nhật từng phần (key bị bỏ qua giữ nguyên, `null` xóa, `{}` là no-op); `DELETE /api/part-numbers?number=` là hard delete (204); `PUT` / `DELETE` / `GET /api/part-numbers/image?number=` upload (raw body, đường của slice 1), gỡ và phục vụ ảnh với `ETag` và `Cache-Control: private, no-cache` (query `v=` chỉ để phá cache); `GET /api/part-numbers/page` là danh sách quản lý có giới hạn — `search` trên PN, name, revision và ERP id, `offset`, `limit` từ 1 đến 200 (mặc định 100), `total` và `has_more`; `GET ?search=` của Phase 4 giữ nguyên shape và giới hạn 50 row, nay khớp PN **hoặc** name đã lưu. PN không bao giờ nằm trong path segment, và các ETag helper chuyển từ Workers API sang `app/api/uploads.py`, hành vi không đổi. Locking: create lấy **PN advisory lock** trước khi kiểm tra tồn tại, nên tuần tự hóa với create-on-first-use (create từ Management không bao giờ biến một lần nhận hàng hay lưu Work Order của production thành 409); sửa, ảnh và xóa chỉ khóa **đúng row `part_numbers` của chúng**, không bao giờ lấy PN advisory lock hay row production, nên production không bao giờ chờ chúng. Mỗi write có hiệu lực ghi đúng một row audit `PartNumber` trong cùng transaction (`CREATED`, `UPDATED` cho thay đổi chi tiết hoặc ảnh, và **writer đầu tiên của `DELETED`**) với snapshot `{part_number, name, current_revision, erp_id}` — ảnh là `{image: digest}`, không bao giờ là byte ảnh — còn write no-op hoặc bị từ chối không ghi gì; create-on-first-use nay ghi cùng snapshot bốn key. Hard delete chỉ xóa row metadata và không bao giờ đọc hay ghi demand, Quantity Flow, Movement, allocation hay lịch sử. Read model: mỗi row Production Board mang `master` (`{name, current_revision}` hoặc null), mỗi row Tracking mang `name`, và `master` của Tracking detail mang các chi tiết đã lưu cùng `updated_at` và `image_updated_at`. Frontend: Management → Part Numbers là **view thật** (ở slice 7 registry mock chỉ-development chỉ còn Planned Routes; slice 8 đã nối nó) với tìm kiếm phía server có giới hạn (`Showing N of T Part Numbers`, `Show more`, tối đa 200 row), dialog `Edit Part Number` dùng chung (`components/EditPartNumberDialog.tsx`: tạo dưới tên `New Part Number`, sửa, ảnh staged được lưu bằng write thứ hai, `Barcode label…`, hard delete sau xác nhận `Delete Part Number details?`, write bị chặn khi mất kết nối); PN control trên demand line của Work Orders trong Work Order Details và New Work Order là control bút chì `Edit Part Number` (`PnEditButton`, thay `PnLabelButton`; PN chưa có chi tiết đã lưu mở `New Part Number` với PN cố định, và marker `new PN` theo sự tồn tại mà dialog quan sát được) trong khi `Barcode label…` của Add Part vẫn là link label; kết quả Add Part hiện name đã lưu (barcode khi chưa lưu); `PnImage` quay về ảnh mặc định khi ảnh tải lỗi; row Production Board hiện `{name} · rev {revision}`; Tracking hiện name ở danh sách và name, revision, ảnh, ERP id ở detail (giá trị vắng render `—`). Validation (môi trường Docker Compose, working tree slice 7 trên HEAD `25e6243`): backend `ruff format --check`, `ruff check` và `mypy` sạch và `pytest` 1351 test pass (`tests/test_part_number_management_api.py` mới có 19 hàm test, 31 case được collect; `tests/test_part_numbers_api.py` collect 13 case và `tests/test_phase13_schema.py` được mở rộng) và `alembic heads` báo `0023_phase13_part_number_master` là head duy nhất; frontend `format:check`, `lint` và `typecheck` sạch, Vitest 948 test pass trong 49 file (`part-numbers.test.tsx` có 23, `PnImage.test.tsx` mới có 2 và `api/part-numbers.test.ts` có 3), và `npm run build` cùng kiểm tra production-boundary (11 mock sentinel) pass. Không có gì khác đổi: chưa có ERP lookup, chưa có archive hay soft delete của PN, và Scan Station cùng Area Board không hiện name hay revision của PN. **Slice 8 — quản lý Planned Routes.** Migration `0024_phase13_planned_routes` (down revision `0023_phase13_part_number_master`, head duy nhất) thêm `route_steps.preferred_machine_id` (attribute step mang tính tham khảo theo PROJECT_PROFILE §8.9: FK nullable tới `machines`, `fk_route_steps_preferred_machine_id_machines`), `assigned_route_steps.preferred_machine_id` (bản copy vào snapshot: integer nullable thường **không có foreign key**, để lệnh INSERT snapshot trong release, receipt, split và merge không bao giờ khóa row Machine sau khi đã khóa Area hoặc Machine), index `ix_assigned_routes_source_route_template_id` trên `assigned_routes`, và `RouteTemplate` trong CHECK `ck_audit_events_entity_type`; không backfill nên step cũ không có Operation giữ nguyên. Downgrade TỪ CHỐI — không bao giờ xóa dữ liệu — khi còn preferred Machine nào được lưu hoặc còn row audit `RouteTemplate`. Application (`app/application/route_templates.py`, `app/api/route_templates.py`): `GET /api/route-templates` giữ contract Phase 4 (chỉ template active, dùng cho release selection); `GET /api/route-templates/management` liệt kê mọi template (active trước, rồi archived) kèm `ever_used` và `usage_count`; `POST /api/route-templates` tạo (201) và `PUT /api/route-templates/{id}` thay name, description và toàn bộ step set có thứ tự (server gán `sequence = 1..N` theo thứ tự request; PUT giống hệt là no-op); `POST /api/route-templates/{id}/archive` archive template đã từng dùng (idempotent; 409 nếu chưa từng dùng); `DELETE /api/route-templates/{id}` hard delete template chưa từng dùng (204; 409 nếu đã từng dùng; lặp lại là 404); `GET /api/route-templates/{id}/usage` trả total và 200 Quantity Flow mới nhất đã release với template (`quantity_flow_id`, `part_number`, `released_on`). Quy tắc save: name bắt buộc (không unique), ít nhất một step, mỗi step có Operation (OD-11), estimated time rỗng hoặc lớn hơn 0, Area active, Operation active và preferred Machine chưa retire đều phải thuộc Area của step, và step 1 không bao giờ nằm ở Area terminal (PROJECT_PROFILE §13 — route như vậy không thể release); mọi step được validate ở mọi lần save có hiệu lực, nên tham chiếu cũ đã lưu bị từ chối thay vì bị giữ hay xóa âm thầm. Template từng được một Assigned Route dùng làm nguồn thì archive, không bao giờ delete; không có unarchive và không có versioning. Edit không bao giờ đổi Assigned Route: release, receipt và bản copy split/merge snapshot các step và nay copy cả `preferred_machine_id`, còn merge compatibility (`lineage.RouteContext`) so sánh nó cùng các field snapshot khác, nên các flow có snapshot chỉ khác nhau ở preferred Machine không bao giờ được merge. Locking: release và receipt khóa template `FOR SHARE` trước khi khóa Area bắt đầu, rồi khóa mọi Area của snapshot trong một lượt tăng dần theo id (Area bắt đầu `FOR UPDATE` đúng vị trí của nó, các Area khác `FOR KEY SHARE`) và các Operation của snapshot `FOR KEY SHARE`, tăng dần, trước khi INSERT snapshot, nên hai lần gán có route đi qua các Area theo thứ tự ngược nhau sẽ tuần tự hóa thay vì deadlock; writer của template khóa template (`FOR NO KEY UPDATE`, `FOR UPDATE` khi delete) rồi khóa các Machine, Area, Operation được tham chiếu `FOR KEY SHARE`, mỗi loại tăng dần theo id, trước khi insert bất kỳ step nào — đúng thứ tự production. Mỗi write có hiệu lực append đúng một row audit `RouteTemplate` trong transaction của nó (`CREATED`, `UPDATED` cho edit hoặc archive, `DELETED`; snapshot gồm template và các step với duration tính bằng giây); write no-op hoặc bị từ chối không ghi gì, và snapshot Assigned Route không bao giờ được audit ở đây. Frontend: Management → Planned Routes là **view thật** (`views/planned-routes/PlannedRoutesView.tsx` trên các hàm management mới của `api/route-templates.ts`): danh sách với bảng Active và Archived, tìm kiếm theo name, description và Operation, usage dialog (`Showing the N most recent.` khi total vượt danh sách), route editor (create, full edit, Operation và preferred Machine giới hạn theo Area của step, Operation, Machine hay Area đã lưu mà không còn được cung cấp hiển thị `(unavailable)` và phải thay trước khi Save, step cũ không có Operation hiển thị `—` và chặn Save, ngôn ngữ Est. time của `route-duration.ts` — `45m`, `4h 00m`, `2d 03h` — nhập và hiển thị không mất thông tin, client validation phản chiếu server), `Duplicate` (server create `{name} (variant)` rồi mở để sửa; mở `New Planned Route` điền sẵn khi bản copy bị từ chối), xác nhận gõ tên `Archive Planned Route` và xác nhận `Delete Planned Route`, mọi write bị chặn khi mất kết nối và có thông báo kết quả-không-rõ tường minh thay vì tự retry. Registry đã đầy đủ: `src/app/real-views.ts` liệt kê cả mười view đã duyệt và `App.tsx` chỉ render view thật, nên không còn view mock chỉ-development nào được nối và `src/mocks/planned-routes.ts`, các type `MockRouteTemplate` / `MockRouteStep`, `src/app/dev-views.ts` và `src/app/UnconnectedView.tsx` (cùng style `.unconnected`) khi đó không còn được tham chiếu đã được các bản sửa audit slice 8 gỡ bỏ; các preview `?state=` chỉ-development của view vẫn còn sau `import.meta.env.DEV`. Validation (môi trường Docker Compose, working tree slice 8 trên HEAD `663a682`): backend `ruff format --check`, `ruff check` và `mypy app tests` (111 file nguồn) sạch và `pytest` 1392 test pass (`tests/test_route_template_management_api.py` mới có 26 hàm test, 27 case được collect; `tests/test_route_templates_api.py` và `tests/test_phase13_schema.py` được mở rộng) và `alembic heads` báo `0024_phase13_planned_routes` là head duy nhất; frontend `format:check`, `lint` và `typecheck` sạch, Vitest 995 test pass trong 50 file (`planned-routes.test.tsx` có 36 và `route-duration.test.ts` mới có 28), và `npm run build` cùng kiểm tra production-boundary (11 mock sentinel, 61 production asset) pass. Không có gì khác đổi: Scan Station không dùng preferred Machine, chưa Assigned Route nào sửa được (Phase 14), và Tracking cùng release dialog không hiện preferred Machine.

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
  phải generic audit; Machine nằm ngoài phạm vi `audit_events` của Phase 4 (Phase 13 về sau mở rộng `audit_events` với Worker, các entity môi trường — Department, Area, Operation, ScanStation và định dạng Asset Tag — và cấu hình Machine, để thay đổi cấu hình quản trị được audit, PROJECT_PROFILE §28); Phase 3.5 không tạo bảng `audit_events`. Phase 13 (slice 2b, quyết định của owner 2026-10-05) về sau thêm `Machine` làm entity của `audit_events` cho các write **cấu hình** Machine — tạo, sửa metadata và maintenance context, maintenance start/clear, và phần chênh lệch cấu hình mà một bản nháp retirement hoặc một lần reactivation mang theo — còn bản thân retirement và reactivation vẫn chỉ được ghi dưới dạng các lifecycle event này. Actor chỉ là
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
  full Management → Part Numbers vẫn thuộc Phase 13 (đã triển khai ở Phase 13 slice 7);
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
write. Authorization chờ Phase 14; reason khi được cấu hình do Phase 13 slice 6
(Undo reason policy) bổ sung — Phase 9 giao reversal không có reason input.

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
command allocation nào sửa Movement history. Authorization chờ Phase 14; Worker chờ Phase 13 (slice 3 của Phase 13 ghi `allocated_by_worker_id` cho một receiving confirmation ở station Stockroom); return
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
- chỉ receive một PN ĐÃ có active quantity sau explicit confirmation mà
  PROJECT_PROFILE §14 yêu cầu, và ghi nó thành QuantityFlow RIÊNG: receipt không
  bao giờ join, merge, mutate hay ghi đè quantity đang có, resolution mang theo
  existing distribution để operator confirm dựa trên đó, backend xét lại state đó
  authoritative lúc write (receipt mà PN có active quantity xuất hiện sau khi mở
  wizard bị từ chối, zero write, tới khi được confirm), và các quantity về sau
  thuộc về nhau được gom lại bằng workflow `Combine quantities` đã có.

Không thuộc phase corrective này:

- Worker identity / Worker Sessions / badge-confirmation gate — Phase 13;
- authentication / role enforcement — Phase 14;
- quản lý Planned Route template hay PartNumber metadata — Phase 13 (PartNumber metadata: Phase 13 slice 7; Planned Route: Phase 13 slice 8);
- allocation behavior của Stockroom — đã thuộc Phase 10.

Trạng thái triển khai (**hoàn tất**: mọi scope bullet ở trên đã triển khai và đã
validate. Backend: command `Receive Quantity` kèm read model và API; frontend:
wizard ba view của GUI_DESIGN §4.7 mục 1 trong Scan Station thật, vào được từ
scan và từ explicit choice `Receive new quantity` của §4.7 mục 2 và 3; đã validate
bằng full backend gate — `ruff format --check`, `ruff check`, `mypy app tests`,
661 test —, `alembic upgrade head` + `alembic check` không drift, và full frontend
gate `npm run check` — format, lint, typecheck, 723 test, build,
production-boundary sentinel): **không
migration** — shape check theo type của Movement đã cho phép `RECEIVED` mang
`station_id` và `reason`, nên receipt do scan không cần đổi schema và Alembic
head vẫn là `0011_phase10_stock_allocation`. **Entry condition**
(`app/application/intake.py`, expose qua `app/application/scan_station.py`): một
Work Order Demand là ACTIVE khi còn business shortage —
`requested_quantity > allocated_quantity` (PROJECT_PROFILE §14, đã làm rõ ở đó
trong phase này: released quantity không quyết định, và `WorkOrder.completed_at`
là trạng thái tổng của cả Work Order chứ không phải của một line). Resolution
báo `has_active_demand` theo rule đó cùng `intake_available` (không active
demand và Area của station có thể bắt đầu production — terminal Area thì không
bao giờ), `part_number_known` (copy Step 1 phân biệt PN đã biết với PN mới),
`internal_work_orders`, `active_quantity` (existing ACTIVE distribution của PN,
ở bất kỳ đâu) và `scanned_at`; read model xét entry condition còn command xét lại
một cách authoritative lúc ghi. Active quantity KHÔNG giữ workflow lại — receipt
tạo flow riêng bên cạnh nó, xem *Không bao giờ join* bên dưới. **Command** (`intake.receive_quantity`,
`POST /api/scan-stations/{station_id}/receipts`) là MỘT transaction theo thứ tự
đã thiết lập — input shape → deterministic fingerprint → idempotency fast path →
station context → MỘT advisory lock cấp PN dùng chung
(`part_numbers.acquire_part_number_lock`) → idempotency re-check → row lock Scan
Station kèm re-check active/binding authoritative → separate-quantity confirmation
xét trên distribution re-read dưới lock và precondition no-active-demand →
resolve internal Work Order dưới lock demand → WorkOrder →
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
demand xuất hiện từ lúc mở wizard, active quantity mà thiếu explicit
separate-quantity confirmation, selection Work Order cũ, scan timestamp naive /
ở tương lai / — với receipt chưa được ghi — quá cũ so với intake scan window, và
reuse `device_event_id` sai fingerprint hoặc khác command.

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
`device_event_id` riêng. **Normalization tách khỏi freshness**
(`_normalized_scanned_at` so với `_validate_scan_freshness`): shape check chạy
trước fingerprint vì instant là một phần của nó, còn đồng hồ chỉ xét command CHƯA
được commit. Scan window chi phối việc NHẬN quantity mới, không bao giờ chi phối
việc BÁO LẠI quantity đã nhận, nên một lost response retry sau mười hai giờ vẫn
replay receipt đã commit thay vì bị từ chối là quá cũ — và receipt chưa được ghi
thì vẫn bị từ chối đúng theo window đó.

Test: `tests/test_intake_api.py` (entry condition với active demand và terminal
Area giữ workflow lại, còn active quantity một mình thì workflow vẫn mở; các
record của receipt, arrival
queued so với direct processing, due date, snapshot `PLANNED` và các từ chối của
nó; reuse bằng cách nâng line kèm audit row, từ chối khi nhiều ứng viên kèm danh
sách và selection tường minh sau đó, selection cũ, `NEW` không bao giờ reuse,
Work Order completed không bao giờ là ứng viên; revalidation lúc ghi và ma trận
input invalid, mỗi trường hợp zero write; replay, reuse sai và reuse khác
command; từ chối Undo; projection replay; separate-quantity confirmation — bị từ
chối khi chưa confirm kèm distribution và zero write, confirm rồi thì tạo flow
thứ hai bên cạnh flow đầu còn nguyên vẹn, quantity chỉ xuất hiện sau resolution
bị từ chối lúc write rồi được confirm dưới CÙNG `device_event_id`; received date
qua site midnight cùng validation và intent của scan timestamp, và receipt đã
commit replay được sau khi scan window hết hạn; và bộ concurrency — hai receipt
đồng thời của một PN, và receipt chạy đua với từng writer có thể trao cho PN
active demand hoặc active quantity).

**Frontend** (`src/api/scan-station.ts` — `receiveQuantity`,
`workOrderSelectionRequired`, `receiptConfirmationRequired`, resolution mở rộng
kèm `scannedAt` và `activeQuantity` —,
`src/views/scan-station/scan-station-intake-dialog.tsx`, nối trong
`ScanStationView.tsx`): PN resolve với `intake_available` mở `Receive Quantity`
thay cho placeholder trung thực trước đây — mở thẳng từ scan khi PN không có
active quantity ở đâu cả, và qua explicit choice `Receive new quantity` của PN
action dialog (bên cạnh `Add more quantity`) hoặc của intent dialog cho PN có
quantity ở nơi khác (`PnIntentDialog`, mở rộng từ choice transfer-or-repair cũ:
không suy ra intent nào khi có nhiều intent áp dụng) — ba view đã duyệt trong MỘT dialog
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
`device_event_id` với `Retry the same receipt`. Khi PN đã có active quantity,
confirmation view nêu tên distribution đó (`<Area> × <n> pcs`), nói rằng receipt
không join nó, và chặn `Confirm receipt` — cùng phím Enter — sau MỘT explicit
acknowledgement; refusal `confirmation_required` của server (quantity chỉ xuất
hiện sau khi wizard mở, không ghi gì) hiển thị distribution của chính server ở đó
và được trả lời dưới CÙNG `device_event_id`, vì flag cố ý không nằm trong request
fingerprint. Sau khi server xác nhận, station context và Area inventory load lại
từ server, barcode input lấy lại focus, và receipt vào session log NHƯNG không
trở thành Undo target. Test frontend:
`scan-station-intake.test.tsx` (default settings và cả hai copy PN, Planned Route
lọc theo Area, write point kèm reload và refocus, due date / reason / Operation,
request `PLANNED`, arrival direct-processing, reuse và selection tường minh,
`NEW` không reuse, từ chối, từ chối selection-required với id mới, retry khi mất
response dùng cùng id và cùng scan timestamp, scan timestamp đi nguyên vẹn tới
write point, block offline, Cancel không ghi gì, và receipt không được đề nghị
làm Undo target; và các case active-quantity — choice `Receive new quantity` xuất
hiện bên cạnh transfer intent và bên cạnh `Add more quantity`, `Confirm receipt`
bị chặn tới khi có explicit acknowledgement với quantity đang có được nêu tên,
receipt đã confirm gửi flag và giữ nguyên flow cũ, và refusal
confirmation-required của server được trả lời dưới cùng `device_event_id`), cùng
suite transfer Phase 5 đã cập nhật, nơi placeholder “no intake at the station” cũ
nay là wizard.

Cố ý chưa có: Worker identity, Worker Sessions và badge gate (Phase 13),
authorization (Phase 14), quản lý Planned Route và PartNumber master (Phase 13) (PartNumber master: Phase 13 slice 7; Planned Routes: Phase 13 slice 8).

**Không bao giờ join — explicit separate-quantity confirmation.** Canonical
decision từng chặn phase này đã được chốt (PROJECT_PROFILE v22 §14, giải quyết
open decision 3 cũ của §32) và cố ý là phương án đơn giản nhất giữ được quantity
integrity: receipt **không bao giờ join** một Quantity Flow đang tồn tại. Nó luôn
tạo flow riêng, và khi PN đã có active quantity thì operator confirm điều đó
tường minh trước khi bất cứ gì được ghi. Quantity đang có không bao giờ bị merge
vào, mutate, thừa hưởng hay ghi đè — không Movement nào được ghi lên nó, và
quantity, route mode, Assigned Route, vị trí cùng processing state của nó giữ
nguyên — còn các Quantity Flow về sau hoá ra thuộc về nhau thì được gom lại bằng
workflow `Combine quantities` đã có (Phase 8, `MERGED`), vốn đã sở hữu
compatibility và lineage. Không có ngữ nghĩa mới nào được bịa ra cho trường hợp
join, nên không có semantics mới, không migration và không có merge path thứ hai.
Rule được enforce ở MỘT chỗ lúc write: `intake.receive_quantity` re-read
distribution dưới advisory lock cấp PN dùng chung và raise CHÍNH
`ActiveQuantityConfirmationRequiredError` mà production release raise (409,
`confirmation_required` kèm existing distribution, zero write), nên quantity xuất
hiện giữa scan và confirmation không bao giờ được receive một cách âm thầm. Flag
confirmation cố ý nằm ngoài request fingerprint: confirm là tiếp tục cùng một
submission dưới cùng `device_event_id`, nên nó replay chứ không ghi hai lần.

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
  PROJECT_PROFILE §17 "Expected duration hiệu lực của một position" (quyết định
  mà implementation đã chờ: snapshot của Assigned Route Step hiện tại thắng,
  Operation default là fallback sống, không có cả hai thì không phán xét).

Phase 11 vẫn chỉ là read-model / monitoring. Nó không được hút vào production
write của Scan Station, Priority write, master-data management, Worker session
hay authentication.

Trạng thái triển khai (đã audit 2026-09-13; expected-duration monitoring hoàn tất
2026-09-14 theo quyết định canonical ghi bên dưới — Production Board, Area Board,
PN Tracking, breakdown theo PN của Machines và expected-duration monitoring hoàn
tất end to end; Phase 11 không còn mục mở): **Backend**
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
thời điểm của state được khôi phục. **Quantity đã merge đọc qua MỌI nhánh
lineage** (`projections.effective_latest_movement_branches`, walk theo frontier
đặt cạnh walk một-Movement): merge result chưa di chuyển kế thừa từ TẤT CẢ
source thay vì parent id nhỏ nhất, vì hai giá trị mô tả cùng một state lại không
tương đương giữa các source — vị trí dated theo entry CŨ NHẤT trong các nhánh
(khối quantity đã merge chờ từ thời điểm sớm nhất), và Machine chỉ hiện khi mọi
nhánh nêu cùng một Machine, nên quantity finished merge từ các Machine hoàn
thành khác nhau (`merges.merge_context` so CURRENT Machine, đã bị `AREA_COMPLETED`
xoá nên merge được phép) hiển thị không Machine thay vì gán cho một source. Bản
thân state vẫn không mơ hồ: merge đã bắt buộc cùng Area, holding state, Machine
và Operation. Quantity gộp theo (Area, state, Machine,
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
điểm cũ nhất, timestamp kế thừa qua lineage và khôi phục qua Undo, quantity đã
merge dated theo entry cũ nhất của các nhánh và chỉ nêu Machine hoàn thành khi
các nhánh đồng nhất, stocked / scrapped từ history, tổng footer khớp rows, row stocked-only còn khi demand mở
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
refresh ngay, còn lần load ĐẦU lỗi là error state có Retry; `● Live` là trạng thái vận hành
của CHÍNH board nên chỉ xanh khi đã có board hoàn chỉnh trên màn hình — load đầu
đang chạy hoặc đã lỗi mang cùng tone warning và ghi chú, board không bao giờ
được trình bày như feed live trên dữ liệu server chưa trả. Header board (dòng
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
(`long` khi clock chung vượt `expected_by` của location; không có expected duration
thì không cờ — stand-in ≥ 3 ngày và `LONG_DWELL_MINUTES` đã bỏ), countdown và
`Total Days` từ UI clock chung, ngọn lửa Hot
và row tint Hot (MỌI Hot rank đều có tint, càng hot càng đỏ theo ba tier của Hot
presentation chung: rank 1 đỏ, rank 2 cam, từ rank 3 trở xuống dùng amber nền —
rank thấp thì nhạt hơn chứ không mất hẳn), kiosk, pagination theo chiều cao với rotation tỉ lệ, auto scale và
mọi điều hướng tay; rows render đúng thứ tự server trả — `sortBoardRows` phía
client đã bỏ, một quy tắc sắp xếp duy nhất thuộc read model (fixture long-data
development sắp bằng `compareDemandOrder` chung một lần lúc load). Chỉ development: `?state=loading|empty|error|long` render state xác định
mà không request (fixture long-data inline sau ranh giới DEV — không import
`src/mocks/`; mock dataset Phase 2 `src/mocks/production-board.ts` đã bỏ). Test
frontend: suite Production Board hiện có chạy trên trả lời giả của
`GET /api/production-board` (đúng wire shape backend) cộng hành vi feed (request
có và không `department_id`, cột Job Numbers, loading dưới header, load đầu lỗi
có Retry kèm status không-Live, load đầu đang chạy cũng không Live, refresh định
kỳ, feed stale khi refresh lỗi vẫn giữ rows và hồi phục ở trả lời tốt kế tiếp,
mất kết nối hiện feed stale và refresh ngay khi trở lại, row tint của mọi Hot
rank với hai tier mạnh hơn được giữ,
Department trống, state preview không request) và `production-boundary.test.ts`
(board trong registry view thật, có trong production module graph, không
import `src/mocks/`). Cố ý chưa có trong riêng slice Production Board: dòng tên / revision
PN master (Part Numbers management, Phase 13 — dòng phụ chỉ render khi có tên; đã triển khai ở Phase 13 slice 7),
thời gian rotation theo Department và Due Soon policy từ Administration (Phase
13 — dùng default đặt tên trong `board-logic` và `views/dates`) và quản lý Hot rank
(Phase 12 — board chỉ đọc `priority_rank`); highlight thời gian tại vị trí theo
expected duration đã hoàn tất (xem **Expected-duration monitoring** bên dưới).

**Area Board** (PROJECT_PROFILE §21, GUI_DESIGN §6 — nội dung Manager Summary
nằm trong All Areas overview, không có view Manager Summary riêng). Quyết định
chi phối slice này: per-Area detail **không phải representation thứ hai** của một
Area, nó chính là model monitoring Area mà Scan Station đã đọc, chỉ bỏ lớp bọc.
*Backend* (`app/application/area_board.py`, `app/api/area_board.py` —
`GET /api/area-board?department_id=`): một read trả về các Area ACTIVE của
Department (thứ tự theo tên; Area inactive không bao giờ giữ active quantity —
lệnh deactivate từ chối khi còn giữ — nên không giấu gì đang sản xuất) với nội
dung chính là `scan_station.area_inventory`, trả qua schema response DÙNG CHUNG
(`app/api/area_inventory.py`, tách ra từ endpoint Scan Station để hai bề mặt gửi
một contract), cộng Operation active của Area, và với terminal Area là các
stocked line kèm allocation active của PN
(`allocations.active_allocated_quantities`) và demand context MỞ của PN
(`allocations.open_demand_context` — cùng monitoring context mà một row
inventory mang, nên PN đã stock vẫn đang được làm CHO demand mở nêu Hot rank,
Work Order Number và Job Numbers đúng như row stocked-only của Production Board,
và chỉ đọc `WO — · —` khi không còn Work Order nào của nó mở) — stocked quantity
của terminal Area đã hoàn tất sản xuất, flow đã đóng, nên Area đó không có
ACTIVE inventory nào. Scrapped theo PN ghi trong Area (trừ scrap đã reverse —
`projections.effective_totals_by_area`) là một phần của model inventory DÙNG
CHUNG (`AreaInventory.scrapped`, có cả trên `GET /areas/{id}/inventory`), vì
dòng `{n} scrapped` thuộc về PN row dùng chung của GUI_DESIGN §4.10 và Scan
Station phải hiển thị đúng con số của board. Resolve Department dùng đúng quy tắc của Production Board
(`production_board.resolve_department`).

Model chung trả lời **HAI câu hỏi khác nhau về cùng một quantity, và không câu
nào được mượn câu trả lời của câu kia**. *Provenance* là `FlowInArea.work_order`
— demand mà quantity được RELEASE cho, chỉ identity và request type, giữ cho
dialog thao tác, confirmation và recap của Scan Station và cho audit, vẫn đúng
sau khi Work Order đó complete; đây là context workflow/audit và không bao giờ
là dòng của một row monitoring, trên cả hai bề mặt.
*Monitoring context* là `AreaInventory.demand_context` — mỗi PN đang được làm CHO
cái gì: các OPEN Work Order Demand (`allocations.open_demand_context`, một nơi
duy nhất giữ quy tắc, dùng chung với Production Board) theo canonical demand
order, demand đầu quyết định row còn các demand khác vẫn được nêu, không gộp
không bỏ. PN mà mọi Work Order đã complete thì đơn giản là vắng mặt trong
context đó: quantity vẫn hiện, chỉ không có context nó không còn nữa. Model còn
mang các giá trị monitoring theo từng quantity mà cả hai view hiển thị —
**timestamp vào vị trí** để derive thời gian chờ và **Machine ĐÃ HOÀN THÀNH**
quantity finished (chỉ `READY_TO_TRANSFER`), cái sau là chính Machine chứ không
chỉ id, nên Machine retired sau khi làm xong — không còn là card của Area — vẫn
nêu được nơi hoàn thành. Cả hai đến từ một derivation duy nhất,
`projections.effective_positions`: quantity đã merge dated theo nhánh lineage CŨ
NHẤT và chỉ nêu Machine khi mọi nhánh đồng nhất.

*Frontend*: `src/api/area-inventory.ts` là model client DUY NHẤT của contract đó
(tách khỏi `api/scan-station.ts`, file này re-export lại), `src/views/area-presentation.ts`
là mapping DUY NHẤT sang shape row chung — mọi row monitoring, của Scan Station
cũng như của board, lấy Hot rank, due date, Work Order Number và Job Numbers từ
OPEN demand context của PN, còn provenance của flow chỉ đi tới dialog thao tác
và recap của station (`flowOf`), nên hai bề mặt không có nhánh monitoring riêng
để drift — và `src/views/monitoring-feed.ts` là hành vi polling / stale / hồi phục
DUY NHẤT sau feed của cả hai board. `AreaBoardView` chuyển vào registry view thật
và render cả hai mode từ một read: **All Areas overview theo PN** (một row mỗi
Part Number trong một Area, các quantity riêng biệt gộp vào chip portion bằng
`aggregateByPartNumber`, PN đếm một lần trên tab count, meta toolbar và statistics
chung), còn **per-Area detail giữ một row cho mỗi quantity thao tác được**.
`splitAssignments` giữ quantity của row gộp luôn bảo toàn, kể cả phần dư direct
bên cạnh các portion có tên. Đã có: loading, error kèm Retry, Department không
có Area, trạng thái stale-feed, và `?state=long` (fixture DEV inline — không
import `src/mocks/`). Sort vẫn ở client vì Area Board có bốn thứ tự do người
dùng chọn; overview sort trên row ĐÃ GỘP (nên `Quantity` so tổng của PN trong
Area và `Time in Area` lấy portion cũ nhất, còn `Priority`/`Due date` giữ nguyên
semantics cấp PN), detail sort từng quantity riêng, và `Priority` xếp mọi Hot
rank trước mọi row không rank, không dùng giá trị sentinel. Search khớp toàn bộ
monitoring context của PN — `demandsSearchText` mang Work Order Number và Job
Numbers của MỌI open demand vào row, nên demand nằm trong `+N more` vẫn tìm
được chứ không chỉ nằm trong tooltip.

*Test*: `tests/test_area_board_api.py` (18) — phạm vi Department và các từ chối,
nội dung Area khẳng định **bằng đúng byte với `GET /api/areas/{id}/inventory`**,
Machine card, timestamp vào vị trí qua split và merge, Machine hoàn thành vẫn
còn sau khi Machine đó retired, monitoring context bám OPEN demand (origin đã
complete không bao giờ cấp context, nhiều demand nêu đủ theo canonical order),
scrap trừ reversal, stocked và allocated của Stockroom — cùng suite frontend
viết lại trên trả lời giả của `GET /api/area-board` (gộp theo PN với quantity
bảo toàn, dòng demand `+N more`, Machine hoàn thành đã retired, thứ tự Priority
với rank vượt mọi sentinel, `Quantity` so tổng PN đã gộp nên `6 + 6` đứng trước
`10` trong khi detail vẫn liệt kê từng quantity, và search bằng Work Order/Job
Number của một open demand THỨ CẤP vẫn giữ row PN đó) cùng regression của suite
Scan Station: row station và Hot count ở header nhận monitoring context từ open
demand trong khi action của row vẫn mang demand nguồn gốc vào dialog. Cố ý chưa
có: quản lý Hot rank (Phase 12) và tên PN master (không áp dụng — GUI_DESIGN §6 không định nghĩa vị trí; Phase 13 slice 7, OD-16); highlight thời gian
chờ theo expected duration đã hoàn tất (xem đoạn audit bên dưới).

**PN Tracking** (PROJECT_PROFILE §21 Tracking, GUI_DESIGN §7). Quyết định chi
phối slice này: Tracking không tự bịa derivation nào — mọi con số nó hiện đều là
con số Production Board, Area Board hoặc Scan Station đã derive, nên không hai
surface nào nói khác nhau về một quantity. *Backend*
(`app/application/tracking.py`, `app/api/tracking.py`): `GET /api/tracking` là
list theo PN — tập PN là mọi PN có lịch sử production (một QuantityFlow) hoặc
open Work Order Demand; `search` chạm PN và WO Number / Job Number của MỌI demand
của PN (WO đã complete vẫn tìm được theo số); các select judged server-side trên
row derive — `area_id` (active quantity trong Area, hoặc stocked quantity ở
terminal Area), `operation_id` (Operation ghi trên active quantity),
`machine_id` (quantity ĐANG TRÊN Machine đó), `request_type` và `hot_only` (một
open demand thuộc loại đó), `status`, và `due` (`OVERDUE` / `THIS_WEEK` /
`THIS_MONTH`, judged trên site calendar theo NEXT due date của PN — sớm nhất
trong các open demand; PN không có open demand có ngày chỉ khớp `ANY`); rows
theo canonical demand order của open demand đầu tiên (row không có demand xếp
cuối, theo PN) với `offset` / `limit` (mặc định 100, tối đa 200) và `total`
khớp. Mỗi row mang open demand context (`allocations.open_demand_context` — MỘT
monitoring demand context), distribution theo Area (active, và stocked ở
terminal Area), active quantity từ derivation vị trí dùng chung
(`production_board.flow_positions` / `group_locations`, tách ra từ board để cả
hai đọc một code path), stocked và scrapped quantity từ effective history, master
tùy chọn có tồn tại hay không, active allocation và **available stocked
quantity** (`effective STOCKED − active allocation`, từ MỘT query allocation gộp
— `allocations.active_allocated_quantities` — không bao giờ theo từng row), và
**status derive**: `ACTIVE` khi còn quantity trong production; nếu không,
`COMPLETED` khi không còn open demand (chỉ còn history — kể cả PN đã stock và
allocate đủ); nếu không, `STOCKED` chỉ khi stocked quantity CÒN AVAILABLE chờ
open demand; nếu không, `OPEN` — open demand không có quantity trong production
và không còn stock chưa allocate (chưa release, scrap hết, release đã undo, hoặc
mọi stocked piece đã allocate cho work trước: stock đã allocate cho Work Order
hoàn tất không bao giờ làm demand mới của cùng PN thành `STOCKED`).
`OPEN` là bổ sung do implementation vào ba pill GUI_DESIGN §7.1 nêu (Active /
Stocked / Completed): bộ giá trị đã duyệt không có giá trị trung thực cho trạng
thái đó, và báo nó bằng bất kỳ giá trị nào trong ba giá trị kia đều nói sai
production — GUI_DESIGN §7.1 ghi nhận nó. `GET /api/tracking/detail?part_number=` là detail read-only (PN đi
trong query parameter và được canonicalize theo MỘT quy tắc domain; không có
production, demand lẫn master → 404): master khi tồn tại (chỉ tồn tại — name,
revision, image và ERP id đến với Part Numbers management, Phase 13) và barcode
`PF:PN:` derive dù có hay không, các open demand với released
(`production_release.released_quantities`), allocated và remaining shortage,
current quantity theo Area / Machine (nhóm `BoardLocation` — entry CŨ NHẤT mỗi
nhóm, Machine chỉ khi mọi nhánh lineage đồng ý), stocked quantity theo terminal
Area kèm active allocation và phần available, các hạng mục reconciliation §11
(`introduced = active + stocked + scrapped`, introduced là effective `RECEIVED`
+ `QUANTITY_ADJUSTED`), các Quantity Flow theo MỘT thứ tự ổn định, bất biến — mới
nhất trước chỉ theo flow id (`id DESC`); status ACTIVE / closed của flow là
presentation của row, không bao giờ là vị trí phân trang, vì status là mutable
và vị trí đi theo status có thể bỏ sót hoặc lặp một flow đổi status giữa hai
lần đọc page — với `flows_limit`
(mặc định 50, tối đa 200) là hard bound của MỌI page kể cả page đầu, nên PN có
nhiều ACTIVE flow hơn limit vẫn trả một page có bound (current quantity vẫn đầy
đủ: `locations` mang toàn bộ active quantity), kèm total và keyset nối tiếp
`GET /api/tracking/flows?part_number=&before=` — `before` là flow cuối đã trả
và page tiếp ngay dưới nó (`id < before`) theo đúng thứ tự đó, nên mọi flow
được đọc đúng một lần dù status nào đổi giữa hai lần đọc page, và ACTIVE flow
trả ở bất kỳ page nào vẫn mang vị trí derive; `before`
không phải flow của PN — flow của PN khác dù ACTIVE hay closed, hoặc id không
tồn tại — là 404, không bao giờ đọc như `id < before` trần mà bỏ sót dữ liệu,
đúng như cursor Movement và allocation được validate — mỗi flow với
status và lifecycle, vị trí derive,
parent / child lineage HIỆU LỰC (`projections.effective_lineage_edges` — cạnh
của SPLIT/MERGED đã undo là vô hiệu), PLANNED snapshot với mỗi step judged
`DONE` / `CURRENT` / `FUTURE` từ last known step của flow và cờ `off_route`
phán xét từ arrival lập nên position hiện tại (`projections.route_positions` —
derivation route-position dùng chung mà expected duration cũng đọc; Movement đã
reverse không tính, flow closed không có current step, và deviation quay lại
Area của step trước vẫn off route; quantity off route cũng không có step
CURRENT — known step của nó đọc `DONE` như tiến độ đã đạt và route chờ step kế
tiếp), cùng mọi deviation đã confirm đọc lại từ Movement ghi nó, và **actual route trace** derive từ Movement history — các Area
quantity đã đến, theo thứ tự (`RECEIVED`, `TRANSFERRED`, `QUANTITY_ADJUSTED`,
`STOCKED`; không bao giờ `AREA_COMPLETED`, vốn là hoàn thành trong Area nguồn),
giữ repeated Area, Repair transfer được gắn cờ, split child kế thừa trace của
parent duy nhất tới điểm split (cờ `inherited`) đệ quy lên TOÀN BỘ single-parent
ancestry — lineage hiệu lực của mỗi ancestor được đọc từ history khi walk tới,
nên prefix không phụ thuộc detail page liệt kê flow nào — merge result bắt đầu
tại điểm merge còn các nguồn giữ trace riêng, arrival đã reverse bị loại;
allocation history (`(allocated_at DESC, id DESC)`, mặc định 100, reversal đứng
cạnh allocation nó thu hồi, nối tiếp qua
`GET /api/tracking/allocations?part_number=&before=` với keyset resolve từ
allocation đó); **Scrap history** — chính Movement history bất biến giới hạn
vào row `SCRAPPED` (`movement_type=SCRAPPED` trên read movements, mặc định 20,
phân trang cùng cách), mỗi event với timestamp, quantity, Area, reason, station
và, khi đã undo, row `REVERSED` đã undo nó, còn `scrapped_quantity` vẫn là tổng
net hiệu lực — không có nguồn scrap song song; và page đầu của **Movement
history bất biến** — mọi Movement của PN theo thứ tự thời gian ngược của GUI
`(occurred_at DESC, id DESC)`, `movements_limit` (mặc định 50, tối đa 200) với
keyset tiếp trên ĐÚNG thứ tự đó (`GET /api/tracking/movements?part_number=&before=`
nêu Movement cuối đã trả và server resolve `(occurred_at, id)` của nó — id có
timestamp bị lùi ngày không bao giờ gây hở khoảng hay trùng; `before` ngoài
history của PN là 404), phục vụ bởi migration `0012_phase11_tracking_index` —
một composite index `(part_number, occurred_at, id)` trên `part_movements`,
không column, table hay constraint —, mỗi row với
Area, Operation, Machine, station, reason, `movement_reason`, command sequence,
snapshot step đã fulfil, route deviation đã ghi, các cạnh lineage của SPLIT /
MERGED, demand khởi phát của `RECEIVED`, và — trên original đã undo — row
`REVERSED` đã undo nó, nên current state loại history đã reverse còn audit trail
không bao giờ mất. *Frontend* (`src/api/tracking.ts`,
`views/tracking/TrackingView.tsx`, `tracking-feed.ts`, `tracking-logic.ts`,
`tracking-preview.ts`; view thật trong `src/app/real-views.ts` có trong mọi
build, mock dataset Phase 2 `src/mocks/tracking.ts` đã bỏ): list đọc
`GET /api/tracking` qua monitoring feed chung (`views/monitoring-feed` — refresh
15 s, một request tại một thời điểm, refresh lỗi giữ page hoàn chỉnh cuối với
status `Feed stale — reconnecting` cạnh title, load đầu lỗi là error state có
Retry, refresh ngay khi kết nối trở lại) với search debounce và mọi select gửi
thành query parameter (lựa chọn Area / Operation / Machine đọc một lần từ
environment và Machines API — chỉ mục active); rows render các cột §7.1 đã
duyệt (Hot trước PN, mọi open demand với WO Number trống là `—`, chấm màu Area,
các con số, next due date, status pill — dòng tên từ master giữ `—` tới Phase
13); long data có bound — `Showing n of m PNs` với `Show more` mở rộng page một
lần tới bound của server, quá đó yêu cầu thu hẹp search; overlay detail modeless
(GUI v14 — whole-row selection, nút đóng accessible, Escape, click ngoài, trả
focus, không reflow table) giữ nguyên và nay key theo PN, mỗi lựa chọn là polled
detail read riêng với loading và error-with-Retry trong panel và ghi chú stale
khi refresh lỗi; panel render các section §7.2 — PN master (`PnImage` dùng chung,
barcode, metadata vắng là `—`, ghi chú rõ khi không có master record), Active WO
Demand (WO · Job · Type · requested · released · allocated · shortage · due ·
priority với thanh allocation progress), Current quantity by Area (thanh theo
Area / Machine với các row queue, direct processing, ready-to-transfer và
stocked phân biệt, wording `Completed processing at … — ready to transfer`),
Quantity Flows & Routes (một block mỗi flow với `RouteModeChip` compact, dòng vị
trí kèm lineage, chip PLANNED snapshot hoặc FLOATING trace là các item step /
arrow sibling riêng với `⟲ REPAIR`, actual path và ghi chú off-route của flow
PLANNED, mọi deviation đã confirm), Movement history (reverse-chronological với
badge type canonical, badge REPAIR, original đã undo vẫn hiển thị với badge
REVERSED, và `Show older Movements` nối page keyset kế),
Scrap history (con số tích lũy net kèm dòng reconciliation, mọi event SCRAPPED
là một row history — timestamp, quantity, Area, reason, badge REVERSED trên
event đã undo — và `Show older scrap events`), Stocked & Allocation history
(stocked / allocated / available và các allocation entry với `Show older
allocation entries`), và `Show older Quantity Flows` của section Quantity Flows
cho các flow ngoài page đầu có bound (mới nhất trước theo flow id bất biến) — MỘT hành vi nối
tiếp chung (`tracking-feed.useOlderPages`) sau bốn section, nhất quán với live
refresh: mỗi section có một **revision signature**
(`tracking-logic.detailRevisions` — số Movement cho history, số row scrap kèm
scrapped quantity net cho Scrap history, số flow kèm số Movement cho flows, số
row allocation cho allocations); khi refresh dời ranh giới của section hoặc đổi
revision của nó, các page đã nối bị bỏ và được đọc lại tới cùng độ sâu dưới
ranh giới mới — ACTIVE flow đóng sau khi older page đã load vẫn được liệt kê
với status mới, closed flow được Undo mở lại không bao giờ xuất hiện cùng lúc
ở page đầu và một older page cũ, scrap bị undo trên older Scrap page nhận dấu
REVERSED cùng lúc với con số tích lũy dù ranh giới scrap không dời — còn
refresh không đổi gì trong hai thứ đó giữ nguyên các page (không đọc lại sau
mỗi poll). Chỉ
development: `?state=loading|empty|error|long` render state xác định không
request (`tracking-preview.ts`, fixture DEV inline — không import `src/mocks/`).
*Test*: `tests/test_tracking_api.py` (22) — status derive mọi trường hợp và
filter mặc định `ACTIVE`, stock đã allocate hết cho work trước khiến demand mới
là `OPEN` còn stock chưa allocate khiến nó `STOCKED`, Scrap history với event
đã undo được đánh dấu và phân trang trên cùng history, trace ancestry giữ đủ
ngoài flow page (ancestor SPLIT ở giữa không được liệt kê) kèm nối tiếp closed
flow, `flows_limit` bound mọi page và các page đọc mọi flow đúng một lần mới
nhất trước với vị trí derive trên ACTIVE flow ở page nối tiếp, đổi status giữa
hai lần đọc page (một flow của page đầu, rồi chính cursor flow, được stock
trước khi gọi nối tiếp bằng cursor cũ) không lặp không bỏ sót flow nào, flow cursor của PN khác hoặc id không tồn tại bị từ chối, keyset
allocation history, thứ tự history `(occurred_at DESC, id DESC)`
với Movement bị lùi ngày phân trang không hở không trùng, search theo PN / WO
Number / Job Number với ký tự LIKE
là literal và WO đã complete vẫn tìm được, con số row với distribution, next due
date qua các demand, master đã xóa cứng, mọi select filter, due window trên site
calendar, canonical order với offset paging, 404 / canonicalize của detail, các
con số demand / vị trí / stock / allocation / reconciliation của detail với một
allocation reversal, FLOATING trace với repeated Area, Repair và prefix kế thừa
từ split, merge result nêu mọi nguồn, PLANNED snapshot state với deviation đã
confirm và closure stocked, history phân trang giữ original đã reverse cạnh các
row `REVERSED`, và audit context lineage / scrap / Machine —
`tests/test_phase11_schema.py` (ranh giới head: đúng history index, parity
models↔migration, downgrade sạch về 0011 — schema test Phase 10 nay pin ở 0011),
cùng suite frontend viết lại (36) trên trả lời giả `GET /api/tracking*` (row
Scrap history với marker REVERSED và page cũ hơn, nối tiếp closed flow và
allocation, các page đã nối được đọc lại tới cùng độ sâu khi refresh đóng một
ACTIVE flow dưới ranh giới, mở lại một closed flow hoặc undo một scrap trên
older Scrap page, và giữ nguyên khi refresh không đổi gì liên quan, read mặc
định và các cột
render, search debounce và query parameter của select, lựa chọn filter, loading
dưới header, load đầu lỗi có Retry, kết quả rỗng, feed stale khi refresh lỗi và
khi mất kết nối, `Show more` và bound, state preview không request, detail read
với các section, step / arrow sibling, Floating trace với Repair marker, quy
tắc finished-rack, ready-to-transfer không bao giờ là Stocked, badge history với
original đã reverse, nối page cũ hơn, history read-only, lỗi detail trong panel
có Retry, master vắng, `describeMovement` cho lineage / stocking / addition, và
tương tác overlay v14 giữ nguyên), `production-boundary.test.ts` (Tracking trong
registry view thật, có trong production module graph, không import
`src/mocks/`) và `rendered-copy.test.ts` (tên Movement canonical nay guard trong
`tracking-logic.ts`). Cố ý chưa có: section Corrections của GUI_DESIGN §7.2 mục
8 (luồng correction có authorization và authorization của chúng là Phase 14 —
section ẩn hoàn toàn, đúng presentation §7.3 cho user không có quyền, thay vì
render nút vô hiệu), name / revision / image / ERP id từ master (Phase 13 —
render `—`; đã triển khai ở Phase 13 slice 7, giá trị vắng vẫn render `—`), quản lý Hot rank (Phase 12), Worker identity trên row Movement
(Phase 13 — station là identity đã ghi; đã triển khai ở slice 3 của Phase 13: row Movement nêu Worker đã ghi); highlight thời gian tại mỗi Area theo
expected duration, từng là phần mở của Phase 11, đã hoàn tất (xem
**Expected-duration monitoring** bên dưới).

**Machines → breakdown Assigned now theo PN** (GUI_DESIGN §12.1; presentation
chỉ-tổng của Phase 6 là stand-in tạm). *Backend*: `machines.assigned_lines` gộp
assigned ACTIVE quantity của từng Machine theo canonical PN, thứ tự PN, từ CÙNG
tham chiếu projection (`quantity_flows.current_machine_id`, status `ACTIVE`) mà
tổng đang cộng, và mọi `MachineResponse` (`GET /api/machines`, `GET
/api/machines/{id}` và response của các lệnh) mang `assigned_lines`
(`[{part_number, quantity}]`) cạnh `assigned_quantity` — nay là tổng của các
line đó, nên breakdown và tổng không thể lệch nhau. *Frontend*: ô Assigned now
của bảng Machines liệt kê mỗi phần một dòng `<PN> · <n> pcs` theo presentation
đã duyệt (quantity mang tone trạng thái, separator và đơn vị mờ), `—` khi không
có gì assigned; `src/api/machines.ts` map `assignedLines`. *Tests*:
`tests/test_machine_processing_api.py` (breakdown của một và nhiều PN trên một
Machine, bằng nhau giữa list và single read, rỗng khi Idle) và suite Machines
(các phần liệt kê và tone của chúng).

**Audit Phase 11 (2026-09-13)** — đã audit implementation đối chiếu
PROJECT_PROFILE §21 và các domain rule, GUI_DESIGN §5 / §6 / §7 (cùng §4.10 và
các global rule) và mục này: dữ liệu backend thật trên mọi production path
(không import `src/mocks/` trong view thật, preview sau `import.meta.env.DEV`,
sentinel scan khi build); location / state / quantity hiện tại derive từ
Movement history và lineage; Undo / `REVERSED`, partial `SPLIT`, `MERGED`,
Repair, Scrap, `AREA_COMPLETED`, assign / release Machine và `STOCKED` không làm
read model sai hay double-count; board toàn Department theo canonical order;
nội dung Manager Summary chỉ nằm trong All Areas overview của Area Board; Area
detail và Scan Station dùng chung một representation; model PN-centric của
Tracking; history bất biến với correction trình bày như history; các state
stale-feed, loading, empty, error và long-data; và không phụ thuộc Phase 12+.
Phát hiện, đều đã sửa trong phạm vi Phase 11: (1) row Stockroom của Area Board
đọc `WO — · —` không có Hot rank với PN đã stock mà vẫn có demand MỞ, trong khi
row Production Board của cùng quantity nêu demand đó — stocked line nay mang
demand context mở của PN (ở trên); (2) row `In this Area now` của Scan Station
không có dòng `{n} scrapped` trong khi row Area Board của cùng quantity có —
scrapped theo PN chuyển vào model inventory dùng chung (ở trên); (3) All Areas
overview gắn nhãn chip của cột Stockroom là `processing × n` — overview row dùng
chung nhận `directLabel` (`stocked` với terminal Area); (4) `N PN(s)` trên
Machine card đếm row thay vì PN riêng biệt (hai quantity của một PN đọc `2 PNs`)
— nay đếm PN riêng biệt, đúng quy tắc của Area statistics; (5) monitoring feed
dùng chung chỉ refresh khi kết nối trở lại theo chuyển đổi trực tiếp
`unavailable → connected` và bỏ sót đường Retry của banner OFFLINE (`unavailable
→ connecting → connected`) — nay nhớ kết nối đã mất cho tới khi khỏe lại; (6)
PN Tracking hiển thị rows của query TRƯỚC như danh sách hiện tại (và `Live`)
trong lúc lần đọc đầu của filter mới đang chạy, và như danh sách "stale" khi
lần đọc đó lỗi — page nay mang query mà nó trả lời và danh sách đọc loading cho
tới khi query hiện tại được trả lời, và shared monitoring feed bắt đầu lại khi
load đổi, nên lần đọc ĐẦU của query hiện tại mà lỗi là error state với Retry
(đọc lại đúng query hiện tại) thay vì loading vô thời hạn — chỉ REFRESH lỗi của
một query đã được trả lời mới giữ rows như stale feed; (7) detail Tracking chỉ gửi
`movements_limit` và dựa vào việc default server trùng page size client cho các
trang flows / allocations / Scrap — nay gửi đủ mọi limit; (8) bước hiện tại của
trace FLOATING chọn theo index thay vì theo vị trí server derive; (9) hint của
empty state dưới filter status `Active` mặc định gợi ý rằng không có demand nào.
Sửa tài liệu: GUI_DESIGN §5.1 vẫn mô tả nút `Enter kiosk` / `Exit kiosk` của
v17 trong khi implementation và §5 mang slide switch `Kiosk` của v18. Đã thêm
regression test cho mọi phát hiện (context và nhãn chip của Stockroom trên Area
Board, PN count của Machine card, dòng scrap ở station, reconnection qua
`connecting`, đổi filter Tracking, ghi chú stale trong panel, ghi chú off-route
và deviation đã xác nhận của flow PLANNED, và thứ tự server cố ý không canonical
được render đúng như nhận). **Expected-duration monitoring (hoàn tất 2026-09-14):**
audit đã để mở mục này vì PROJECT_PROFILE §17 định nghĩa expected duration trên
Route Step (advisory) và §8.5 `default_expected_duration` của Operation mà không
nói nguồn nào áp dụng khi có cả hai, cũng không nói flow FLOATING (không có step)
được phán xét theo gì; implementation không đoán. Quyết định của owner nay là
canonical trong PROJECT_PROFILE §17 "Expected duration hiệu lực của một
position": flow PLANNED lấy `expected_duration` của Assigned Route Step HIỆN TẠI — hiện
tại theo derivation route-position dùng chung `projections.route_positions`:
known step là route progress (Movement hiệu lực mới nhất có tham chiếu step),
và quantity chỉ ON route khi arrival lập nên position hiện tại đã fulfill một
step; arrival deviation đã confirm không tham chiếu step nên để quantity OFF
route, không có step hiện tại, kể cả khi quay lại Area của step trước (Repair
return — không bao giờ dùng Area equality; audit cuối Phase 11 đã tìm ra và bỏ
rule đó), event trong Area giữ state của arrival, arrival đã undo coi như chưa
xảy ra, split child / merge result kế thừa state từ arrival của nguồn (on route
chỉ khi mọi branch on route) — nếu không thì `default_expected_duration` của
Operation đã ghi; flow FLOATING lấy Operation
default; không có → không phán xét. Giá trị snapshot luôn thắng; Operation
default là fallback sống (đổi có hiệu lực ngay, không mutate hay backfill
snapshot). Elapsed là thời gian của chính position — `now − entered_at` của
`EffectivePosition` branch-aware, chính `since` mọi view đã dùng — không timer,
timestamp hay persisted state mới; phán xét chỉ advisory (warning highlight,
không bao giờ block scan, transfer, `DONE`, Machine action hay command khác);
location gộp warning ngay khi BẤT KỲ portion nào đã vượt expected duration CỦA
CHÍNH NÓ (không average, portion mới không che portion overdue); và rule `>= 3
days` cố định bị bỏ, không có fallback thay thế. `off_route` / current-step presentation của PN Tracking đọc CÙNG derivation, nên
hai nơi không thể phán xét một quantity khác nhau. *Backend*: derivation dùng
chung nằm đúng nơi mọi monitoring read model đã đọc position —
`projections.effective_positions` giải `EffectivePosition.expected_duration`
(`route_positions`: known step của các flow trong một grouped query trên các
Movement hiệu lực có tham chiếu step, arrival hiệu lực mới nhất của chúng — của
chính flow, hoặc kế thừa qua lineage walk — cho state on/off route, rồi các
snapshot step và default sống của Operation) và
expose `expected_by = entered_at + expected_duration` (None khi không áp dụng);
`BoardLocation` của Production Board (dùng chung với `locations` gộp của
Tracking) mang `expected_by` SỚM NHẤT trong các portion, còn mỗi surface theo
flow — `FlowInArea` của shared Area inventory mà Scan Station và Area Board đọc,
`position` của flow trên Tracking — mang giá trị của chính nó. Các API thêm
thời điểm nullable `expected_by` vào location của `GET /api/production-board`,
flow của `GET /api/area-board` / `GET /api/areas/{id}/inventory` và position /
location của `GET /api/tracking/detail`; server không phán xét theo clock.
Test: snapshot step thắng Operation default và đổi default sống mà snapshot
không đổi, event trong Area giữ step, quantity FLOATING và off-route theo
Operation default, null khi không cấu hình, location gộp warning từ portion
overdue sớm nhất, và regression deviation-return — deviation đã confirm và
Repair return vào Area của step đầu vẫn off route theo Operation default, split
của partial assignment kế thừa, Undo khôi phục expectation theo từng arrival
(`test_production_board_api.py`); `expected_by` trên position và location gộp,
và cùng kịch bản deviation-return trên `off_route` / state các step — `DONE` /
`FUTURE` khi off route, `CURRENT` lại khi Undo khôi phục arrival on-route — qua
split và các Undo (`test_tracking_api.py`); `expected_by` theo flow kế thừa qua
split và null khi không có nguồn, mọi action vẫn có (`test_area_board_api.py`).
*Frontend*: một phán xét dùng chung `views/dates.exceedsExpectedDuration(expectedBy,
now)` — thời điểm cố định của server so với UI clock chung, false khi không có —
gắn cờ dwell của Production Board (`ltime.long`, bỏ `LONG_DWELL_MINUTES`),
`Time in Area` của shared PN row (`tia.long`, nên Scan Station và Area Board
detail warning như nhau; row overview theo PN gộp `expectedBy` sớm nhất trong
`aggregateByPartNumber`) và position của flow trên Tracking (ghi chú tường minh
`· exceeds expected duration`); mọi highlight có tooltip `Exceeds the expected
duration` hoặc chữ đi kèm, không bao giờ chỉ màu. Test: board chỉ cờ khi qua
`expected_by` và 5 ngày không có expected duration thì không cờ, cờ xuất hiện khi
clock vượt thời điểm; row detail và gộp overview của Area Board; ghi chú Tracking;
các trường hợp biên của helper.

## Phase 12 — Priority Management

- ranking Hot WorkOrderDemand;
- add/search/scan; add ở bottom áp dụng trực tiếp;
- reorder bằng drag-and-drop, Move Up/Move Down;
- xác nhận trước remove và trước mọi thay đổi order existing entry, kể cả Undo/
  Redo;
- apply có audit sau explicit confirmation;
- Undo/Redo;
- automatic removal entry có demand trở thành inactive; remove Hot demand line bằng typed confirmation
  (quyết định owner 2026-10-04).

Trạng thái triển khai (backend và frontend đã triển khai end to end; **Phase 12 ĐÃ ĐÓNG** ngày 2026-10-04 —
các quyết định owner OD1 / OD3 / OD5 / OD7 bên dưới đã được đưa ra, audit follow-up, gồm cả audit đóng
phase đối chiếu PROJECT_PROFILE §21 Priority Management mục 1–11 và GUI_DESIGN §8 / §11.2, phủ chúng, và
không còn quyết định owner nào đang mở): **Backend**. Persistence là migration
`0013_phase12_priority` (pre-check từ chối, CHECK rank dương, UNIQUE và audit expression index;
downgrade drop cả ba; `models.py` khai báo cùng các object và `compare_metadata` rỗng tại head).
Pre-check từ chối thay vì normalize có chủ đích: PROJECT_PROFILE §18 xếp tie theo ngày nghiệp vụ và §28
yêu cầu mọi thay đổi priority đều được audit, nên renumber âm thầm sẽ không đạt cái nào. Expression của
index được lưu ở dạng operator `->>` tường minh trên subscript `metadata['hot_list_change']` — cách
render `.astext` có thêm dấu ngoặc mà Alembic đọc thành expression khác — và Application lookup dùng
cùng expression (test `EXPLAIN` chứng minh index được dùng). Rule thuần nằm trong
`app/domain/hot_list.py` (`interpret_change` trả về **mọi** cách đọc của một thay đổi, vì hoán đổi hai
phần tử kề nhau là thay đổi duy nhất có hai cách đọc: "B lên" hoặc "A xuống"; action gửi lên phải khớp
một trong hai). `app/application/hot_list.py` resolve Department, dựng read model và chạy
`apply_hot_list_change`: input shape, cách đọc thay đổi và khớp action, fingerprint, idempotency lookup
trước và sau Hot advisory lock, precondition `expected_order`, row lock, eligibility trên row đã lock,
ghi hai flush, một audit row cho mỗi rank đổi, và entries của response dựng **trước** COMMIT để là list
như đã commit. Replay trả về `changes` gốc dựng lại từ audit row và một lần đọc mới của list hiện tại;
replay không bao giờ resolve Department để có `changes`, nên khi không còn đúng một Department active
nó vẫn trả 200 với `entries: null` và view đọc lại list (lần đọc đó nêu vấn đề Department).
Body 409 `hot_list_changed` là `{detail, hot_list_changed: true, entries}` và dùng chung wire shape
thành công của `entries`. `priority_rank` không có writer nào trong `app/` ngoài command này và
`hot_ranks.remove_from_hot_list` (đã kiểm bằng search). **Frontend**: view xem bullet Trạng thái hiện tại; history Undo / Redo nằm ở module scope
(giữ qua việc đổi sub-view Management và kết thúc khi reload trang, không giới hạn độ sâu), step không
còn áp dụng được lên list hiện tại (entry đã có trong list, hoặc không còn) bị bỏ kèm thông báo giải
thích thay vì chặn các step sau, 409 stale giữ cả hai stack, và Undo / Redo chèn lại entry mà server từ
chối vì không eligible (409) hoặc không tồn tại (404) thì bỏ step đó kèm message của server. Add dialog
không bao giờ thêm từ kết quả cũ hoặc debounce: candidate chỉ được thêm bằng lựa chọn tường minh, hoặc
bằng barcode có đúng một demand eligible; barcode có nhiều demand mở danh sách đã lọc để chọn, và barcode
không có demand nào hiện từ chối; mọi lần scan không thêm gì (không có, nhiều, bị từ chối hoặc bị chặn)
giữ ô nhập được chọn cho lần scan tiếp, danh sách đã lọc thay thế lỗi load candidate trước đó, và một
lần scan không liệt kê gì khi list mặc định chưa về thì load lại list đó thay vì để nó mãi ở trạng thái
loading. List refresh khi mở view và
sau mỗi command; không polling và không nhận push.

Quyết định owner (2026-10-04; chúng thay các default mà triển khai Phase 12 ban đầu đã lấy cho OD1, OD3,
OD5 và OD7): **OD1 — automatic removal entry inactive**: Hot entry có Work Order Demand trở thành inactive
(PROJECT_PROFILE §14 — Work Order completed, hoặc line allocate đủ, `requested_quantity <=
allocated_quantity`; completed kéo theo line allocate đủ nên test theo từng line phủ cả hai) rời Hot list
tự động trong **cùng transaction** với thay đổi làm nó inactive; các rank còn lại đóng khoảng trống (H1 vẫn
là `1..N`) và mọi thay đổi rank đều được audit. Không có confirmation dialog nào: hành động kích hoạt chính
là hành động đã được xác nhận. Invariant **H2**: tại mọi trạng thái đã commit không có demand có rank nào
inactive, và ADD của Hot command đã từ chối demand inactive. Write path (tìm bằng cách search `app/` mọi
writer của `allocated_quantity`, `completed_at`, `requested_quantity`, `priority_rank` và việc xóa demand):
`allocations.confirm_allocation` — một command cho Stockroom confirmation và Management allocation — remove
mọi command line có rank mà nó allocate đủ (reason `FULLY_ALLOCATED`, hoặc `WORK_ORDER_COMPLETED` khi chính
command đó làm Work Order của nó completed); `work_orders.update_work_order` remove mọi line đã sửa có rank
mà requested quantity bị chính save đó hạ xuống bằng allocated quantity (reason `FULLY_ALLOCATED`) — không
bao giờ remove line có rank mà save không đổi quantity, nên sửa due date hay Job Number không bao giờ remove
entry và không bao giờ ghi save là nguyên nhân;
`delete_work_order_demand` là OD3 bên dưới. **Reversal** của allocation mở lại Work Order không thêm lại gì,
tăng quantity cũng vậy; release, intake và các Scan Station command không bao giờ làm demand có rank trở
thành inactive. Khối dùng chung là `app/application/hot_ranks.py` (leaf module, để allocation và Work Order
save dùng không tạo import cycle; rule thuần `removal_shift_scope` và `close_gaps` trong
`app/domain/hot_list.py`). **Thứ tự lock** (một thứ tự toàn cục; thay claim Phase 12 rằng không writer nào
khác lấy Hot lock): PN advisory lock (tăng dần) → Hot advisory lock → một row Scan Station (`FOR KEY SHARE`
trong allocation và reversal — lấy trước pass demand để không holder demand row nào phải chờ station) →
demand row `FOR UPDATE` trong MỘT pass tăng dần (các line của chính command cộng mọi demand có rank mà rank
có thể dịch) → Work Order row tăng dần. Allocation confirmation lấy Hot lock vô điều kiện (lấy có điều kiện
sẽ dựa trên một pre-read không lock và có thể đảo thứ tự); Work Order save chỉ lấy khi nó sửa requested
quantity; việc xóa demand chỉ lấy với cờ OD3; reversal không bao giờ lấy. Hot lock và PN lock là advisory
key 64-bit, nên hash collision giữa chúng về lý thuyết có thể đảo thứ tự — PostgreSQL khi đó hủy một
transaction (`40P01`) mà không commit gì, xác suất khoảng N/2^64 — thay cho phát biểu cũ "collision chỉ
serialize". Allocation remove entry **trước** khi stage các allocation row, nên race mất `device_event_id` ở
COMMIT vẫn đi vào nhánh replay / 409 hiện có và rollback bỏ luôn các ghi Hot. **Audit** (PROJECT_PROFILE
§28): một row `audit_events` `UPDATED` `WorkOrderDemand` cho mỗi demand đổi rank — các entry bị remove và
mọi entry bị dịch — với `metadata.hot_list_change` mang `action` (`AUTO_REMOVE`, hoặc `LINE_DELETE` cho OD3;
bảy action của command không đổi), `sequence`, identity snapshot và block `cause` (`trigger` `ALLOCATION` /
`WORK_ORDER_SAVE` / `DEMAND_LINE_REMOVAL`, `reference` — `device_event_id`, `source` và `station_id` của
allocation, hoặc `work_order_id` — và `removed`, list các removal kèm `reason`); các row này **không** mang
`device_event_id` hay fingerprint ở level `hot_list_change`, nên idempotency lookup của command không bao
giờ thấy chúng; `actor_reference` vẫn NULL tới Phase 14. Response của allocation và reversal không đổi
(không có thông báo Stockroom); audit trail và view Priority là bản ghi. **Không migration**: column, CHECK,
UNIQUE và audit index hiện có là đủ, và migration rewrite sẽ tạo thay đổi rank không audit (tiền lệ
`0013`: từ chối, không bao giờ rewrite). Entry inactive tồn đọng chỉ có thể có trên database đã chạy các
commit Phase 12 chưa phát hành (`80f7925` … `b9785d2`, 2026-10-04) trước thay đổi này — database
development đã được kiểm chỉ đọc ở `0013_phase12_priority` và có 0 demand có rank; Hot command vẫn nhận
REMOVE / MOVE của entry đó (đường phục hồi có audit của manager) và view Priority vẫn gắn cờ nó và nói Undo
không thể thêm lại. Check chỉ đọc trước khi deploy cho mọi database đã chạy các commit đó phải trả về 0 row
(nếu không remove các entry được liệt kê trong Management → Priority; ghi trong `docs/DEPLOYMENT.md`):
`SELECT d.id, d.priority_rank FROM work_order_demands d JOIN work_orders w ON w.id = d.work_order_id WHERE
d.priority_rank IS NOT NULL AND (w.completed_at IS NOT NULL OR d.requested_quantity <=
d.allocated_quantity);`. Là ngoại lệ có tài liệu, ghi priority không phải sửa Work Order và bề mặt Work
Orders cùng trang Completed Work Orders vẫn read-only, nhưng `priority_rank` có thể đổi, kèm audit row
`UPDATED`, trên demand của Work Order **completed** qua **hai** writer: automatic removal bên trong
allocation làm completed, và REMOVE / MOVE (kèm renumber) của manager trên entry tồn đọng từ trước thay đổi
qua Hot command. Việc owner đang chờ trước đây về GUI_DESIGN §8 ("Undo can restore it" cho entry inactive)
được đóng bởi quyết định này: theo H2 entry inactive chỉ tồn tại như entry tồn đọng từ trước thay đổi, và
GUI_DESIGN §8 không bị sửa cho trường hợp chỉ-còn-ở-dữ-liệu-cũ đó. Thông báo step không áp dụng được của view
Priority nói entry có thể đã "removed elsewhere, or automatically once its line was fully allocated", footer
nói entry tự rời list, và Management → Work Orders hiện thông báo khi một save đã auto-remove entry. Giới
hạn đã biết (có từ trước, không đổi): Work Order save hạ line mở cuối cùng xuống bằng allocated quantity
không set `work_orders.completed_at` (completion chỉ do allocation set); Hot entry vẫn được auto-remove theo
rule từng line. **OD2 — phạm vi Department** (không đổi): một rank space bị gate về đúng một Department
active (404 / 409 nếu khác), vì demand không mang Department và rank là một cột cho mỗi demand, được
allocation dùng theo PN; rank space theo Department cần Department trên demand hoặc suy ra theo Area cộng
migration renumber, nên cái này **không thể đảo ngược nếu không sửa dữ liệu**. Đây là **quyết định owner**
được ghi nhận, không phải hoãn sang phase nào và không phải closure gate: deployment được hỗ trợ có một
Department active (PROJECT_PROFILE §22), và gate từ chối tường minh sự mơ hồ cho tới khi owner quyết khác.
**OD3 — xóa Hot line**: xóa một saved Work Order demand line đang trên Hot list được phép, chỉ sau khi UI
cảnh báo line đang trên Hot list (kèm rank) và yêu cầu **typed confirmation** (`TypedConfirmDialog` dùng
chung); backend từ chối Hot line khi thiếu cờ tường minh `confirm_hot_removal`, không ghi gì, và có cờ thì
remove entry (renumber dense, audit là `LINE_DELETE`) và xóa line trong MỘT transaction. Server hỏi Hot
confirmation **cuối cùng**, nên mọi rule khác (Work Order completed, allocated quantity, allocation history,
released quantity, last line) giữ nguyên từ chối riêng và thứ tự của nó, và UI chỉ đưa typed dialog ra trước
cho line không có allocation history (hiện tại hay đã reverse — `has_allocation_history` của Work Order read)
không phải saved line duy nhất, nên typed confirmation không bao giờ bị hỏi vô ích (nếu không thì chạy plain confirmation và typed
confirmation chỉ theo sau khi Hot rank là cổng cuối). PROJECT_PROFILE §13 và GUI_DESIGN §11.2 mang wording
đã sửa. **OD5 — priority tại Work Order intake**: hoãn (xem `Deferred`); Hot list vẫn là nơi duy nhất đặt
priority. **OD7 — báo cáo "Hot and priority demand" của PROJECT_PROFILE §27**: được thỏa bởi view Priority,
Hot rank của Production Board và filter Hot only của PN Tracking; không build gì thêm.

Cố ý chưa có, kèm quyết định sở hữu: role enforcement (chỉ Manager / Admin) → **Phase 14** (bullet
authorization cho mọi Management write; `actor_reference` là NULL tới lúc đó, như mọi surface có audit
khác); **OD5 — priority tại Work Order intake** (PROJECT_PROFILE §21 Work Orders mục 5, GUI_DESIGN §11.2
"priority when applicable") → `Deferred` (rank gõ tay lúc intake sẽ là writer thứ hai của dense list);
**OD7** → được thỏa như trên, không build gì; rank space theo Department (OD2) như trên; history Undo / Redo
phía server (history session chỉ ở client, theo PROJECT_PROFILE §21 mục 9); push hoặc polling của Hot list
(view đang mở biết automatic removal qua đường stale-409 và refresh khi activation); flag `is_hot`; hợp nhất
các cài đặt canonical-order. **Phase 12 đã đóng: không còn quyết định owner nào đang mở.**

## Phase 13 — Full Administration and Production Identity Configuration

Hoàn thiện Administration ngoài minimum setup Phase 3.5 và làm thật các surface
master-data / configuration production còn lại.

- Workers: stable id, name, employee badge barcode khớp chính xác sau khi chuẩn hóa
  (trim, uppercase), không `PF:WORKER:`, avatar, active; Worker vẫn tách khỏi User;
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

Trạng thái triển khai (**đang triển khai** — slice 1, 2, 2b, 2c, 3, 4, 5, 6, 7 và 8 đã triển khai — các bullet Workers, Worker identification per Area, Worker Session runtime integration, production audit identity, Worker Session policies, Undo reason policy, Route Templates, Part Numbers và đổi đích PN control trên demand line đã hoàn tất (quyền truy cập dựa trên permission của bullet Route Templates được enforce ở Phase 14), và audit cấu hình của các write môi trường đã có; mọi bullet còn lại ở trên đang chờ): **Đã triển khai — Workers (slice 1).** Migration `0014_phase13_workers`, các endpoint `/api/workers` cùng endpoint avatar, giao thức write có audit và section Administration → Workers thật được mô tả trong bullet Trạng thái hiện tại. Phần lưu ảnh xây ở đây (`app/application/images.py`, đường upload raw-body, `prepareImageUpload`) là đường duy nhất mà ảnh Part Number và avatar User dùng lại về sau trong phase này. **Quyết định owner (2026-10-04, OD-3 — quy tắc badge của Worker)**: badge được chuẩn hóa bằng cách trim khoảng trắng hai đầu rồi đổi sang UPPERCASE **cả khi lưu lẫn khi khớp scan**, nên việc khớp badge không phân biệt hoa/thường; giá trị rỗng hoặc bắt đầu bằng `PF:` (sau chuẩn hóa) bị từ chối; tối đa 128 ký tự; badge unique trên mọi Worker, **gồm cả Worker inactive**, nên kích hoạt lại một Worker không bao giờ tạo khớp mơ hồ (PROJECT_PROFILE §10). Các lớp bảo vệ là domain rule, UNIQUE và CHECK dạng chuẩn hóa đã nêu ở trên. **Quyết định owner (2026-10-04, OD-10 — lưu ảnh)**: ảnh lưu trong PostgreSQL, PNG / JPEG / WebP tối đa 2 MiB (avatar Worker bây giờ, ảnh Part Number về sau); avatar Worker lưu trên row `workers`, và trình duyệt resize ảnh lớn xuống cạnh dài nhất 1024 px trước khi upload. **Quyết định owner được ghi lại cho các slice chưa xây (2026-10-04)**: **OD-2** — sliding inactivity timeout của Worker Session mặc định **15 phút** và cấu hình được từ **1 đến 720 phút**, cho giá trị mặc định lẫn từng override theo Area; **OD-8** — Users và roles là **role có tên, chỉnh sửa được**: chỉ seed các grant mà PROJECT_PROFILE §20 nêu tường minh, enforcement vẫn ở Phase 14, và slice Users và roles thuộc Phase 13 (không defer). Slice 1 chưa triển khai hai quyết định này. **Quyết định lấy theo default**: không xóa Worker — Worker chỉ bị deactivate; audit cấu hình qua `audit_events` với actor NULL cho đến Phase 14; sửa Worker theo last-writer-wins như các section cấu hình Phase 3.5, mỗi write có hiệu lực đều được audit; name không unique; editor Worker hiện initials khi không có avatar. **Cố ý chưa có ở slice 1** (các slice Phase 13 sau): badge-confirmation gate (do slice 5 giao), Users và roles, ảnh và hard delete của Part Number, và ảnh cùng hard delete của Part Number (do slice 7 giao, writer đầu tiên của audit event `DELETED`). Từ slice 4 preview Worker sessions đã biến mất; Scan Station đọc Workers registry cho Fixed Worker, đăng nhập badge và (chỉ build development) demo badge. **Đã triển khai — audit cấu hình (slice 2).** Migration `0016_phase13_environment_audit` và giao thức write có audit của mọi write môi trường Phase 3.5 (Department, Area, Operation, Scan Station, định dạng Asset Tag) được mô tả trong bullet Trạng thái hiện tại; slice này đóng khoảng trống "administrative configuration changes" của PROJECT_PROFILE §28 cho các entity đó. **Quyết định đã lấy trong slice 2 (default của spec trong kế hoạch Phase 13)**: không backfill, vì cấu hình có trước revision không có bản ghi tạo lập trung thực, nên row audit đầu tiên của entity cũ là `UPDATED` có `before_data` là trạng thái tìm thấy; một row cho mỗi request có hiệu lực; mọi update khóa row của mình trước, theo lock mode của chính lần sửa đó; `next_sequence` (bộ đếm của việc tạo Machine) không phải cấu hình và không bao giờ được audit; ánh xạ conflict không đổi; snapshot là danh sách field tường minh, nên slice sau thêm cột cấu hình chỉ được audit khi thêm cột đó vào snapshot (theme của Station không bao giờ được audit). **Quyết định của owner (2026-10-05, S2-F6 và S2-F4/F5)**, do slice 2b thực hiện: mọi lần tạo hoặc sửa Machine (tên, đổi Area, metadata, maintenance context) được audit trong `audit_events` bằng cùng cơ chế với slice 2 (PROJECT_PROFILE §28 liệt kê thay đổi cấu hình quản trị không miễn trừ; câu chữ Phase 3.5 đã được sửa ở trên), và hai lỗi 500 do thua race có sẵn từ trước được sửa (`update_department` vừa đổi tên vừa deactivate thua `uq_departments_name` ở autoflush, và hai Asset Tag PUT đầu tiên đồng thời thua primary key). **Đã triển khai — slice 2b.** Các migration, giao thức audit Machine, hai ánh xạ 409 và thay đổi PN CHECK được mô tả trong bullet Trạng thái hiện tại. **Quyết định đã lấy trong slice 2b**: S2b-OD1 (default của spec) — retirement và reactivation chỉ có row audit cho phần chênh lệch cấu hình chúng mang theo, nối bằng `metadata.machine_lifecycle_event_id`, không bao giờ cho bản thân chuyển trạng thái lifecycle; S2b-OD2 (default của spec) — hai revision, `0017` và `0018`, mỗi revision có downgrade từ chối riêng; S2b-OD3 (default của spec) — vế whitespace của PN cũng chuyển sang `"C"`, nên backstop của database chỉ từ chối whitespace ASCII còn domain rule từ chối mọi whitespace Unicode trên mọi đường write; S2b-OD4 (default của spec) — `entity_id` của Machine là id nội bộ dạng text; S2b-OD5 (default của spec) — `maintenance_since` được audit dưới dạng text UTC ISO-8601; S2b-OD6 (hệ quả của S2-F6, owner thấy được; owner có thể veto) — write admin Machine khóa trước, làm bốn kết quả lost-race chuyển thành 409 với message hiện có: maintenance start đồng thời (trước đây 200 và ghi đè âm thầm), maintenance clear đồng thời, edit, maintenance start hoặc clear thua một retirement (trước đây record retired bị sửa), và reactivation đồng thời (trước đây có event `REACTIVATED` trùng); S2b-OD7 (default của spec) — 409 của Asset Tag PUT đầu tiên có nội dung "The Machine Asset Tag format was just saved by someone else. Refresh the page and open Barcode configuration again to see the saved format, then apply your change again."; S2b-OD8 (owner thấy được) — PN chứa 27 code point nhạy cảm với phiên bản Unicode được chấp nhận ở dạng canonical của Python 3.12 / UCD 15.0 (`pnɤ1` thành `PNɤ1`), phiên bản UCD được ghim ở `15.0.0` bằng `test_unicode_database_version_is_pinned`, và rủi ro được nêu tên là trình duyệt mới hơn pre-normalize các code point đó sang dạng Unicode 16, nên hai dạng có thể được lưu thành hai PN khác nhau (phương án bị loại: từ chối các code point bị ảnh hưởng sau `upper()`, trái PROJECT_PROFILE §7 và có thể từ chối mới những PN có thể đã được lưu; muốn từ chối phải sửa PROJECT_PROFILE trước). **Owner placement**: S2b-F2 được xếp vào slice 2c (OWNER_DECISIONS, mục S2b-OD8). Slice 2c cũng đóng các lần đọc parent-activity của slice 2 (S2-F1), cùng họ race. **Đã triển khai — slice 2c.** Các parent-activity lock và kết quả tuần tự của chúng được mô tả trong bullet Trạng thái hiện tại. **Quyết định đã đưa ra trong slice 2c**: S2c-OD1 (mặc định của spec) — child lấy `FOR SHARE` trên parent (phương án bị loại: child lấy `FOR KEY SHARE` và deactivate Department được nâng lên `FOR UPDATE`, vì khi đó tính đúng đắn phụ thuộc vào việc mọi đường deactivate hiện tại và tương lai đều lấy `FOR UPDATE`); S2c-OD2 (mặc định của spec) — tính an toàn khi retry của `POST /api/areas` vẫn chưa được xếp; S2c-OD3 (owner thấy được) — kết quả thay đổi: child thua một lần deactivate parent đồng thời giờ nhận 409 sẵn có thay vì commit, deactivate Department thua một child đang chạy nhận 409 sẵn có, và write child cùng edit parent giờ chờ nhau (reactivate Machine tại chỗ có thể chờ ngắn sau một lệnh production trong Area của nó), copy không đổi. **Follow-up S2c-F1** — thứ tự khóa của tạo và cập nhật Area ghi trong đặc tả slice 3 (khóa Worker, đọc Department) phải được viết lại cùng Department `FOR SHARE` trước khi slice 3 được xây; owner: đặc tả slice 3 (đã đóng: được viết lại trong đặc tả trước khi xây và triển khai như mô tả ở bullet Trạng thái hiện tại). **Còn chưa xếp**: `POST /api/areas` không an toàn khi retry (tên Area không unique và không có idempotency key; đã đánh giá trong slice 2c: bản sửa cần một idempotency key, một thay đổi API, hoặc một quy tắc tên Area canonical, nên nó vẫn chưa được xếp); kiểm tra prefix Asset Tag từ chối tập `\s` của Python, một superset chặt của `[[:space:]]` libc của PostgreSQL, nên hiện không prefix nào được chấp nhận mà fail CHECK của database; nó chỉ bị phơi ra trước một thay đổi glibc trong tương lai; **S2b-F1** — frontend submit một PN đã normalize bằng bảng Unicode của trình duyệt (`barcode.ts`) khi gõ PN và khi scan nhãn demand của Work Order (`processScan` trong `demand-lines.ts` chuẩn hóa lại nhãn `PF:PN:` được scan), nên với các code point mà trình duyệt và server bất đồng, một PN được gõ có thể thành hai PN được lưu và chính nhãn của một PN đã lưu có thể thêm demand line dưới một PN khác; đường scan của Scan Station submit giá trị scan thô nên không bị ảnh hưởng (hướng sửa đề xuất: submit input thô đã trim và hiển thị dạng canonical của server); và **S2b-F3** — danh tính canonical của PN và badge Worker theo UCD của Python backend (ghim ở `15.0.0`), nên trước mọi lần nâng cấp Python làm đổi nó, phải chạy một kiểm tra chỉ-đọc liệt kê mọi PN và badge đã lưu có dạng canonical sẽ đổi, rồi owner quyết định có canonicalize lại không. **Đã triển khai — Worker ID mode theo Area và Worker identity trên production record (slice 3).** Migration `0019_phase13_worker_identity`, mode và Fixed Worker của Area, station identity resolver, 14 command ghi identity, thao tác đọc `badge-scans` và frontend được mô tả ở bullet Trạng thái hiện tại. **Quyết định đã lấy ở slice 3 (default của spec trong kế hoạch Phase 13)**: S3-OD1 — resolver khóa Fixed Worker `FOR KEY SHARE`, ở cuối cùng, làm việc từ chối Worker inactive chính xác mà không đổi thứ tự khóa nào sẵn có; S3-OD2 — một `stamp_movements` trên toàn bộ danh sách command ngay trước khi stage, nên mọi row của một command mang cùng identity theo cấu trúc; S3-OD3 — `SCANNED` bị Area service từ chối (422) cho đến slice badge-gate (được chấp nhận từ slice 5), còn (cho đến slice 4) resolver và `badge-scans` trả 409 cho Area `SCANNED` dựng bằng fixture, nên Area scanned không bao giờ ghi identity NULL; S3-OD4 — `badge-scans` trả 200 kèm `outcome` (`NOT_USED_IN_AREA` / `UNKNOWN`) và `mode`, một thao tác đọc không ghi gì; S3-OD5 — Tracking hiện `W: <name>` trước station và bỏ khi không có; S3-OD7 — `Reversed by` của summary Undo do server tính từ mode hiện tại của Area của station, để rule identity nằm ngoài Presentation, trong khi Undo command vẫn resolve identity có thẩm quyền lúc confirmation; S3-OD8 — lịch sử hiện tên hiện tại của Worker (không snapshot), như Area, Operation và Machine; S3-OD9 — nhãn select Fixed Worker là `{name} · {badge}`; S3-OD10 — Fixed Worker chỉ được đánh giá lại khi tạo, khi đổi mode hoặc Worker, hoặc khi activate Area; S3-OD11 — downgrade `0019` từ chối khi còn identity hoặc cấu hình khác Disabled; S3-OD12 — row `Worker` của bảy confirmation summary không phải Undo lấy từ station context vừa đọc lại (không có server preview mới). **Giới hạn đã chấp nhận S3-L1**: pill và row `Worker` của summary hiện station context như lần đọc cuối (tải trang, mỗi lần scan PN hoặc Machine được resolve, mỗi lần mở `DONE` / `QUEUE` của Machine card hoặc `DONE` của direct processing, mỗi command hoặc lần từ chối, và một câu trả lời badge có mode khác); pill của một station đang rảnh hiện lần đọc cuối cho tới một trong các lần đó, và nếu Administration đổi Area khi một dialog đang mở — hoặc một lần đọc lại ngầm thất bại — summary có thể nêu cấu hình cũ, nhưng server ghi identity nó đánh giá lúc confirmation (Fixed Worker inactive bị từ chối) và lần resolve hoặc command kế tiếp làm mới station; không thêm polling. **Cố ý chưa có ở slice 3** (`scan_session_id`, Worker Session, timeout policy và badge modal chặn đã đến cùng slice 4): `SCANNED` chọn được và các badge-confirmation gate (slice badge-gate, Phase 13 — do slice 5 giao); Worker trong lịch sử Stocked & Allocation của Tracking — `allocated_by_worker_id` được ghi nhưng GUI_DESIGN §7.2 mục 7 không định nghĩa field Worker (S3-OD6; owner xếp). **Follow-up (owner xếp)**: S3-F1 — các thuộc tính Area minh họa của PROJECT_PROFILE §8.4 liệt kê `worker_identification_mode` nhưng không có tham chiếu Fixed Worker; S3-F2 — Worker trên row allocation chưa có surface hiển thị; S3-F3 — command trong Area đọc mode của Area không khóa, nên một thay đổi mode commit trong lúc command chạy được xếp sau command (đúng tuần tự; slice 4 đóng session khi đổi mode rời `SCANNED` và khóa Area như S4-OD12 nêu). **Đã triển khai — Worker Session và timeout policy (slice 4).** Migration `0020_phase13_worker_sessions`, `application_policy`, override theo Area, `worker_sessions`, `part_movements.scan_session_id`, các rule đăng nhập, chuyển, làm mới và đóng, `GET` / `PUT /api/policies/worker-sessions` và frontend được mô tả ở bullet Trạng thái hiện tại. **Quyết định đã lấy ở slice 4 (default của spec trong kế hoạch Phase 13 và OD-2)**: S4-OD1 — session id là bigint identity như mọi bảng khác (PLAN CD5 từng nói uuid) và không bao giờ rời server (S4-OD9); S4-OD2 — override theo Area được ghi trên Area qua `POST` / `PATCH /api/areas` và audit là `Area`, chỉnh từ section Worker sessions; S4-OD3 — đổi timeout áp dụng từ lần làm mới hoặc đăng nhập kế tiếp của mỗi session và không viết lại row session nào; S4-OD4 — một Worker có thể đăng nhập ở nhiều station (session theo station); S4-OD5 — deactivate Area không đóng session nào (Area inactive vốn từ chối production và session sẽ hết hạn); S4-OD6 — replay một command đã commit không bao giờ làm mới session; S4-OD7 — mọi resolve PN hoặc Machine 200 đều làm mới, kể cả `NO_TRANSFERABLE_QUANTITY`, còn từ chối thì không; S4-OD8 — cùng badge quét lại trả `REFRESHED` kèm thông báo `Worker signed in: {name}`; S4-OD10 — ghi session dùng `clock_timestamp()` sau các khóa của đường đi, nên `occurred_at` của Movement có thể trước `started_at` của session và `scan_session_id` là liên kết có thẩm quyền; S4-OD11 — trước slice badge-confirmation, section nói rõ ba tùy chọn chưa có và không đưa công tắc nào (slice 5 thay thế); S4-OD12 — command khóa Area của station `FOR KEY SHARE` ở mode Scanned-session (nhóm A đã giữ `FOR UPDATE`), nên một station allocation hoặc command nhóm B có thể nhận 409 khi đổi mode rời `SCANNED` commit trước và operator xác nhận lại; S4-OD14 — đọc context và preview Undo không bao giờ làm mới session, và client đọc lại preview Undo khi Worker của session đang sống đổi lúc dialog đang mở; S4-OD15 — demo badge là module chỉ-development liệt kê các Worker active thật; S4-OD16 — client áp dụng câu trả lời session theo thứ tự gửi và bỏ qua câu trả lời cũ hơn câu trả lời đã áp dụng cuối. **Cố ý chưa có ở slice 4**: chọn Scanned session trong Administration → Areas và các badge-confirmation gate (slice badge-gate, Phase 13 — do slice 5 giao); control sign-out (không có theo quyết định owner, PLAN OD-4 — S4-OD13); Machine session (không có theo thiết kế); Worker Session trong Tracking hoặc báo cáo. **Follow-up (owner xếp)**: S4-F1 — PROJECT_PROFILE §19 liệt kê việc Worker đăng xuất là một cách kết thúc session nhưng không có control GUI, nên vocabulary `end_reason` không có giá trị sign-out (thêm là thay đổi CHECK cộng thêm); S4-F2 — chưa có surface nào hiển thị lịch sử session hay `scan_session_id`; S4-F3 — retention Phase 16 chỉ được purge `worker_sessions` cùng với, và sau, các Movement tham chiếu chúng, qua đường đặc quyền; S4-F4 — session hết hạn nhưng chưa đóng giữ `ended_at IS NULL` đến lần đăng nhập hoặc lần đóng do cấu hình kế tiếp ở station của nó, reader coi `expires_at <= now` là đã kết thúc, và báo cáo phải join Movement với session qua `scan_session_id`, không bao giờ theo cửa sổ thời gian. **Đã triển khai — badge-confirmation gate và bật Scanned session (slice 5).** Migration `0021_phase13_badge_confirmation`, quy tắc `final_gate` cùng `final_gates` của station context, các kiểm tra `confirming_badge` của `DONE`, `QUEUE` và Undo với ba typed refusal, việc command đăng nhập theo badge, PUT policy dạng partial-merge, mode `SCANNED` được chấp nhận và frontend được mô tả trong bullet Current State. **Quyết định đã chọn ở slice 5 (default của spec trong kế hoạch Phase 13, OD-2 và OD-3)**: S5-OD1 — server tính form của gate và báo trong context (client chỉ đọc; PLAN từng để client suy ra từ mode và policy); S5-OD2 — `PUT /api/policies/worker-sessions` của slice 4 trở thành partial merge (mỗi field tùy chọn, vắng = giữ, `{}` / `null` = 422, một row audit với snapshot đầy đủ bốn key), nên một công tắc không bao giờ ghi đè thay đổi đồng thời của administrator khác lên field khác (đồng thời trên cùng field vẫn là last-writer-wins, mỗi write đều được audit); S5-OD3 — mỗi công tắc lưu ngay và chỉ gửi option của nó; S5-OD4 — đường badge khóa Area của station `FOR SHARE`, nên việc đổi mode khỏi `SCANNED` và một gate sign-in được tuần tự hóa; S5-OD5 — badge không rõ hoặc inactive là 422 với cờ typed `badge_not_recognized`, nên gate giữ mở với lỗi hiện tại chỗ; S5-OD6 — form của gate được chốt trước (`badge_confirmation_required` trước `worker_session_required`, `badge_confirmation_not_expected` trước mọi xử lý session); S5-OD7 — retry: kết quả không rõ thì gửi lại request đã đóng băng, kèm badge; sau một lần từ chối tường minh, form question gửi lại mà không hỏi lại và form badge yêu cầu quét mới, luôn dưới cùng `device_event_id`, và một typed refusal tự chỉ định form kế tiếp (`REQUIRED` mở lại gate dạng quét badge với lý do của server bên trong, `NOT_EXPECTED` quay về summary và lần confirm kế tiếp hỏi question); S5-OD8 — summary vẫn hiện Worker đang đăng nhập (giá trị của server) trước một badge gate, badge quyết định Worker được ghi, và không có notice sign-in thừa; S5-OD9 — replay bỏ qua badge; S5-OD10 — downgrade từ chối khi có option tắt hoặc row audit ghi một option; S5-OD11 — `confirming_badge` là chuỗi không rỗng, không có độ dài tối đa, và vấn đề ở dạng canonical là "not recognized"; S5-OD12 — `/machine-assignments` tiếp tục từ chối field này (danh sách gate chuẩn không gồm assignment); S5-OD13 — gate dùng lại demo badge chỉ-development qua một slot được bảo vệ dùng chung. **Cố ý chưa có ở slice 5**: gate trên mọi action khác (danh sách chuẩn là `DONE`, `QUEUE` và Undo); role enforcement về ai được confirm (Phase 14); control sign-out (không có theo quyết định owner, PLAN OD-4). **Follow-up (owner xếp)**: S5-F1 — sau slice 5 không còn workflow Scan Station đã duyệt nào chưa triển khai, nên `ScanStationMockView.tsx`, `?preview=mock` và các mock dataset của scan-station có thể được gỡ (hiện chỉ-development nên không chặn đóng phase); S5-F2 — PROJECT_PROFILE §21 Administration liệt kê Worker session policy với sliding inactivity timeout nhưng không có các badge-confirmation option mà §19 đặt trong cùng policy (không mâu thuẫn; owner sửa wording nếu muốn); S5-F3 — Tracking hiện Worker đã ghi nhưng không hiện việc có badge gate xác nhận action hay không, và không có yêu cầu chuẩn nào đòi hỏi. **Đã triển khai — Undo reason policy (slice 6).** Migration `0022_phase13_undo_reason_policy`, policy `GET` / `PUT /api/policies/correction-permissions`, `reason` tùy chọn của Undo command cùng refusal 409 `undo_reason_required`, field `reason_required` của preview và frontend được mô tả trong bullet Current State; slice này giao quy tắc "require a reason when configured" của PROJECT_PROFILE §16 mà Phase 9 để vắng. **Quyết định đã chọn ở slice 6 (default của spec trong kế hoạch Phase 13 và OD-6)**: S6-OD1 — một công tắc toàn cục, default Off, không scope theo role hay Area; S6-OD2 — policy là `/api/policies/correction-permissions`, audit với `entity_id` `correction-permissions`, và `PUT` của nó chỉ một field (bắt buộc, không phải partial merge); S6-OD3 — `reason` chỉ vào request fingerprint khi có, nên mọi Undo trước đó vẫn replay và reason khác dưới cùng một id là conflict id tường minh sẵn có; S6-OD4 — reason được trim và trống là vắng; S6-OD5 — summary Undo không hiện field khi policy tắt, dù API vẫn lưu reason tùy chọn; S6-OD6 — client biết policy từ `undo-preview.reason_required` và từ typed refusal, không từ station context; S6-OD7 và S6-OD15 — typed refusal giữ `device_event_id`, xóa trạng thái unknown-outcome (nó chứng minh không có gì được commit dưới id), cho sửa reason lại và mở gate ở lần Confirm kế tiếp, còn reason là read-only khi đang gửi, sau unknown outcome và sau generic refusal; S6-OD8 — state refusal, rồi `undo_reason_required`, rồi các refusal badge / session identity; S6-OD9 — không giới hạn độ dài; S6-OD10 — final gate nhắc lại reason; S6-OD11 — downgrade từ chối khi option bật hoặc section từng được audit; S6-OD12 — refusal là 409 với cờ typed; S6-OD13 — response của Undo mang `reason`; S6-OD14 — khi dialog đã hỏi reason (qua preview hoặc refusal) yêu cầu được chốt trong suốt vòng đời dialog; S6-OD16 — Enter trong field reason không làm gì. **Cố ý chưa có ở slice 6**: scope theo role hay Area của yêu cầu reason, reason category hoặc pick list, giới hạn độ dài, role-based correction permission (cấu hình ở slice Users và roles, enforce Phase 14), field reason khi policy tắt, và enforcement ở mức database (CHECK không thể phụ thuộc cấu hình). **Follow-up (owner xếp)**: S6-F1 — PROJECT_PROFILE §16 có thể nêu nơi cấu hình reason (owner sửa wording nếu muốn); S6-F2 — slice Users và roles thêm bảng role-permission vào Correction permissions và chỉ thay câu nói rõ hiện trạng; S6-F3 — reason category hoặc scope theo Area sẽ là cột hay bảng typed mới, không có dữ liệu đã lưu cần sửa; S6-F4 — các field reason free-text khác (Scrap, quantity addition, Repair, allocation reversal và correction) chung lỗ hổng U+0000 mà slice 6 chỉ chặn cho reason của Undo (`DataError` của psycopg lúc flush, 500, không ghi gì). **Đã triển khai — quản lý Part Numbers (slice 7).** Migration `0023_phase13_part_number_master`, `POST` chỉ-tạo-mới cùng các route `PATCH` / `DELETE` / image / `page` với locking và audit, các read model và frontend (view Management → Part Numbers thật, dialog `Edit Part Number` dùng chung, PN control trên demand line được đổi đích, name trong Add Part, chi tiết trên board và Tracking) được mô tả trong bullet Trạng thái hiện tại; slice này giao việc quản lý Part Number master theo PROJECT_PROFILE §8.1, §21 và §28 và gỡ mock Part Numbers khỏi module graph production. **Quyết định đã chốt ở slice 7 (default của spec trong kế hoạch Phase 13 và OD-10)**: S7-OD1 — danh sách quản lý là `GET /api/part-numbers/page` mới (search trên PN, name, revision và ERP id cùng `total` / `has_more`, limit tối đa 200), còn `GET ?search=` của Phase 4 giữ nguyên shape và giới hạn 50 row; S7-OD2 — `POST` nhận các chi tiết tùy chọn để một row `CREATED` có audit ghi một lần tạo, còn ảnh vẫn là `PUT` raw-body riêng; S7-OD3 — mọi row audit `PartNumber`, gồm cả create-on-first-use, mang snapshot đủ bốn key (chỉ thêm key null); S7-OD4 — cột `name`, `current_revision`, `erp_id`, `image`, `image_type`, `image_updated_at` (tên thuộc tính của PROJECT_PROFILE §8.1); S7-OD5 — không giới hạn text, có trim, trống là NULL, không unique; S7-OD6 — `PATCH` cùng giá trị hoặc `PUT` ảnh cùng ảnh là no-op, client coi `DELETE` 404 là đã mất, và `POST` 409 sau unknown outcome thì tải lại record; S7-OD7 — glyph bút chì là `✎` (U+270E, `aria-hidden`; tên accessible `Edit Part Number {PN}` mang ý nghĩa); S7-OD8 — dialog PN cố định chưa có chi tiết đã lưu mang tiêu đề `New Part Number` với identity header, `Barcode label…` và action `Add Part Number`; S7-OD9 — dòng board là `{name} · rev {revision}` và Tracking detail mở đầu bằng name dạng text thường (`name —` chỉ khi chưa lưu name) rồi `· revision … (informational) · barcode … · ERP id …`; S7-OD10 — downgrade từ chối khi còn chi tiết hoặc ảnh; S7-OD11 — tìm kiếm của Add Part khớp PN hoặc name và hiện `name ?? barcode`, header PN đã chọn và revision không đổi; S7-OD12 — create lấy PN advisory lock trước khi kiểm tra, nên mọi nơi tạo master vẫn tuần tự hóa theo PN. **Hành vi được chấp nhận**: một lần xóa Part Numbers commit trong lúc một lần lưu Work Order đang chạy có thể để demand mới không có chi tiết đã lưu (lần lưu đã đọc master trước khi xóa; production không bao giờ phụ thuộc master, và create-on-first-use kế tiếp hoặc một create từ Management khôi phục chi tiết); một create từ Management đua với create-on-first-use của production cho cùng PN thì tuần tự hóa trên PN advisory lock, production dùng lại master không có 409, và nếu production thắng thì create trả 409 và dialog chuyển sang Edit giữ các giá trị đã nhập. **Cố ý chưa có ở slice 7**: ERP lookup, sync hay validate ERP id (ERP giữ cô lập), name hay revision của PN trong PN resolution của Scan Station, Area Board (GUI_DESIGN §6 không định nghĩa vị trí) và header PN đã chọn của Add Part, revision trong kết quả Add Part, tìm Tracking theo name PN, archive hay soft delete của PN, và enforce permission trên các write master-data (Phase 14, `actor_reference` giữ NULL đến lúc đó). **Follow-up (owner xếp)**: S7-F1 — hậu tố ` · no Part Number master record — history unaffected` của Tracking dùng từ vựng domain mà copy discipline của GUI_DESIGN §14 tránh trên view Part Numbers (copy đề xuất ` · no saved Part Number details — history unaffected`); ở slice 7 view Planned Routes là view mock chỉ-development duy nhất còn được đăng ký. **Đã triển khai — quản lý Planned Routes (slice 8).** Migration `0024_phase13_planned_routes`, các route management cùng locking và audit, việc copy preferred Machine vào snapshot và view Management → Planned Routes thật được mô tả trong bullet Trạng thái hiện tại; slice này giao phần quản lý Planned Routes theo PROJECT_PROFILE §8.8–§8.10, §21 và §28 và kết thúc view mock chỉ-development cuối cùng, nên không còn view mock chỉ-development nào. **Quyết định đã đưa ra ở slice 8 (default của spec trong kế hoạch Phase 13 và OD-11)**: áp dụng OD-11 — Operation của step bắt buộc khi create và edit, step cũ không có Operation hiển thị `—` và phải hoàn tất ở lần save kế tiếp, còn `preferred_machine_id` được copy vào snapshot Assigned Route và chỉ mang tính tham khảo; S8-OD1 — danh sách management là `GET /api/route-templates/management` riêng, còn listing Phase 4 giữ contract chỉ-active để release selection không bao giờ thấy template archived; S8-OD2 và S8-OD3 — PUT đầy đủ thay step set, id của step không ổn định và server gán `sequence = 1..N`; S8-OD4 — estimated time rỗng hoặc lớn hơn 0; S8-OD5 — Area inactive trên một step bị từ chối khi save và hiển thị `{name} (unavailable)` trong editor (**default đã áp dụng, chờ owner xác nhận**: GUI_DESIGN §13.2 chỉ gọi tên trạng thái `(unavailable)` cho Operation và Machine nên chưa ghi vào GUI_DESIGN; phương án thay thế là cho phép Area inactive, một thay đổi quy tắc Application và UI không cần sửa dữ liệu); S8-OD6 — mọi step được validate ở mọi lần save; S8-OD7 — name không unique; S8-OD8 — thứ tự lock ở trên; S8-OD9 — Duplicate là server create `{name} (variant)` rồi Edit, và mở `New Planned Route` điền sẵn khi bị từ chối; S8-OD10 và S8-OD11 — archive chỉ cho route đã từng dùng, idempotent, không unarchive, và xác nhận gõ tên chỉ là guard ở UI; S8-OD12 — usage liệt kê các Quantity Flow có Movement `RECEIVED` mà snapshot nêu tên template (split child không phải release), 200 mới nhất kèm total; S8-OD13 — snapshot audit là toàn bộ template và các step; S8-OD14 — index `source_route_template_id`; S8-OD16 — token Est. time không mất thông tin; S8-OD17 — step mới mặc định là Area active không-terminal đầu tiên và Operation đầu tiên của nó, step thêm vào dùng Area của step trước; S8-OD18 — nhiều editor đồng thời: người ghi sau thắng, mỗi lần save có hiệu lực đều được audit; S8-OD19 — Area terminal ở step 1 bị từ chối (409) và bởi client validation, còn template cũ có step như vậy vẫn được liệt kê và bị từ chối khi release bằng copy hiện có cho đến khi được sửa; S8-OD20 — `preferred_machine_id` của snapshot không có foreign key. **Hành vi và giới hạn được chấp nhận**: L1 — template được lưu ngay trước khi Operation bị deactivate hoặc Machine bị retire giữ tham chiếu đã cũ, hiển thị `(unavailable)` và được thay ở lần save kế tiếp; L2 — một release hay receipt và một edit của cùng template, hoặc một lần save template và việc deactivate Area hay retire Machine, có thể chờ nhau trong thời gian ngắn; L3 — PN Tracking gọi Planned Route nguồn của flow bằng name hiện tại của template trong khi các step hiển thị là snapshot lúc release, nên template đổi tên sẽ gắn name mới cho các flow cũ; S8-OD15 — `POST /api/route-templates` không an toàn khi retry: sau kết quả không rõ, client reload và yêu cầu người dùng kiểm tra, và bản trùng là một template chưa từng dùng có thể xóa. **Cố ý chưa có ở slice 8**: sửa Assigned Route đang trong sản xuất, `ROUTE_ADJUSTED` và Tracking → `Edit assigned Route…` (Phase 14), template versioning và view lịch sử, unarchive template, việc Scan Station dùng preferred Machine, enforce permission trên write của template và `actor_reference` (Phase 14), và việc kiểm tra tham chiếu template khi deactivate Area, Operation hay Machine (tham chiếu chỉ mang tính tham khảo và hiển thị `(unavailable)`). **Follow-up (owner xếp)**: S8-F1 — Scan Station có thể gợi ý preferred Machine của snapshot khi assign (chưa dùng, OD-11); S8-F2 — name route unique cần quy tắc chuẩn trước; S8-F3 — snapshot name của template lúc release nếu PN Tracking nên hiện name lúc release (L3); S8-F4 (chu trình thứ tự Area có sẵn từ trước của release / receipt) đã được đóng bởi các bản sửa audit slice 8: release và receipt giờ khóa mọi Area của snapshot trong một lượt tăng dần, rồi các Operation, trước khi INSERT snapshot.

Phase 13 không được để lại preview Worker-session policy chỉ-development hay
production identity giả trong module graph production. Slice 8 đã nối view mock cuối cùng (Management → Planned Routes), nên không còn view mock chỉ-development nào được đăng ký.

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
- ERP/MES feature rộng ngoài PartFlow;
- nhập priority tại Work Order intake (PROJECT_PROFILE §21 Work Orders mục 5, GUI_DESIGN §11.2 "priority when applicable") — owner hoãn ngày 2026-10-04; Hot list (Management → Priority) vẫn là nơi duy nhất đặt priority; khi làm, là một entry point "add to Hot list" qua cùng command, append ở bottom (không gõ rank).
