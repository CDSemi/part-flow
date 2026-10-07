# PartFlow

> **Ngôn ngữ:** Đây là bản tiếng Việt của [`README.md`](./README.md).
> File tiếng Anh là nguồn chuẩn (source of truth). Bản dịch này dùng baseline
> upstream commit `f96bf09` (Production Board — merged quantity theo mọi nhánh lineage).
>
> Xem [mục lục tài liệu tiếng Việt](./docs/README.vi.md) và
> [hướng dẫn triển khai](./docs/DEPLOYMENT.vi.md).

PartFlow là hệ thống nội bộ theo dõi quá trình sản xuất, dùng barcode để ghi
nhận việc di chuyển số lượng chi tiết qua nhà máy.

## Trạng thái hiện tại

Repository hiện có nền tảng từ Phase 1 đến Phase 10.5 triển khai end to end và
Production Board + Area Board + PN Tracking của Phase 11 và Priority Management của Phase 12 và Workers registry của Phase 13 (đang triển khai):

- **Phase 1:** React + TypeScript, FastAPI, PostgreSQL, Alembic, Docker Compose,
  health check, formatter/linter/typecheck/test và CI.
- **Phase 2:** design system Dark/Light, application shell, routing, trạng thái
  loading/empty/error/disconnected/long-data và mock UI chỉ dành cho development.
- **Phase 3:** domain/data model chuẩn gồm Department, Area, Operation,
  PartNumber metadata, WorkOrder/Demand, route snapshot, QuantityFlow và bảng
  PartMovement append-only.
- **Phase 3.5:** cấu hình môi trường thật cho Department, Area, Operation, Scan
  Station, barcode và Machine; quản lý Machine có Asset Tag tự cấp, maintenance,
  retire/reactivate cùng lịch sử lifecycle append-only.
- **Phase 4:** nhập Work Order thủ công, lưu Demand và release sản xuất là hai
  hành động riêng; release từng phần/lặp lại tạo QuantityFlow cùng `RECEIVED`
  Movement trong một transaction idempotent.
- **Phase 5:** Scan Station resolve PN và transfer toàn bộ hoặc một phần quantity
  sang Area của station, ghi `TRANSFERRED` và cập nhật projection atomically.
- **Phase 6:** assign vào Machine, trả về QUEUE, DONE tại Machine, và implicit
  completion khi transfer quantity đang ở Machine.
- **Phase 7:** direct processing cho Area không có Machine; DONE không cần
  `machine_id`, hoặc implicit complete khi transfer.
- **Phase 8:** SPLIT/MERGED, bảo toàn quantity và lineage N→1/1→N; mọi action
  hỗ trợ partial quantity trong cùng command.
- **Phase 9:** Undo theo toàn command bằng Movement `REVERSED`, Repair,
  `SCRAPPED` và `QUANTITY_ADJUSTED · INCREASE`, đều có audit và reason.
- **Phase 10:** Stockroom/Allocation end to end: backend có `STOCKED`, gợi ý
  allocation theo thứ tự chuẩn, xác nhận allocation với `allocation_quantity`
  tường minh (từ chối khi stale so với available stock), reversal, completion
  Work Order được derive và lịch sử completed chỉ đọc (search, Done range —
  preset theo tên neo vào ngày hiện tại của site hoặc ngày tường minh — due
  outcome và done date theo lịch nhà máy `SITE_TIMEZONE`, sort server-side,
  keyset paging chỉ phát cursor khi còn row tiếp); frontend có workflow
  `Receive into Stockroom` cùng allocation
  dialog trên Scan Station shell và trang Completed Work Orders thật.
- **Phase 10.5 (Scan Station Receive Quantity):** wizard `Receive Quantity` ba
  view ĐƯA VÀO một canonical Part Number không còn active Work Order Demand (kể
  cả Part Number gặp lần đầu) trong một transaction: internal Work Order không có
  external number được tạo hoặc reuse, Work Order Demand của nó, Quantity Flow,
  route snapshot `PLANNED` và Movement `RECEIVED` bất biến mang Scan Station và
  Operation đã resolve (`POST /api/scan-stations/{id}/receipts`). `received_date`
  lấy từ chính lần scan mà resolution đóng dấu, không phải lúc confirm, và toàn
  bộ entry condition được serialize bằng lock cấp Part Number dùng chung cho mọi
  command có thể tạo demand hoặc quantity. Receipt bên cạnh active quantity của
  Part Number tạo Quantity Flow RIÊNG và chỉ sau explicit confirmation của
  operator — không bao giờ merge gì, và `Combine quantities` vẫn là merge duy
  nhất (PROJECT_PROFILE v22 §14, chốt open decision 3 cũ của §32).
- **Phase 11 (Production Board + Area Board + PN Tracking):** read model board toàn Department derive từ
  projection vị trí hiện tại và Movement history (`GET /api/production-board`:
  phân bổ theo Area / Machine / External activity kèm timestamp vào vị trí,
  stocked và scrapped, Work Order / Job Number context, Hot rank, theo canonical
  board order; `department_id` chọn Department, bỏ trống chỉ khi có một
  Department active) và view board thật trên đó: tự refresh định kỳ, giữ rows
  hoàn chỉnh cuối cùng kèm `Feed stale — reconnecting` mỗi khi chưa có board
  hoàn chỉnh trên màn hình (refresh lỗi, kết nối không khỏe, load đầu đang chạy
  hoặc đã lỗi), giữ nguyên presentation đã duyệt (kiosk, pagination + rotation — timing theo Department từ Phase 13 —, auto
  scale, điều hướng tay). **Area Board** cũng đã thật: một read của Department
  (`GET /api/area-board`) trả về mọi Area active mang CHÍNH model monitoring Area
  mà Scan Station đọc — nên All Areas overview và per-Area detail là hai
  presentation của cùng một trả lời và không thể lệch khỏi station — cộng
  Operation của Area và stocked line kèm allocation và demand context mở của PN
  cho terminal Stockroom (scrapped theo PN trong Area thuộc chính model dùng
  chung, nên row của station cũng mang dòng `{n} scrapped`). All Areas overview theo PN (một row mỗi Part Number trong
  một Area, các quantity riêng biệt gộp vào chip portion) còn per-Area detail
  giữ một row mỗi quantity thao tác được; Hot rank, due date và Job Numbers của
  row monitoring lấy từ OPEN Work Order Demand của PN trên cả hai bề mặt, kể cả
  Scan Station — không lấy Work Order đã complete mà quantity tình cờ bắt nguồn
  (cái đó ở lại trên quantity làm provenance, chỉ dành cho dialog thao tác,
  recap của station và audit) — và
  quantity finished giữ Machine hoàn thành kể cả sau khi Machine đó retired.
  **PN Tracking** cũng đã thật: list Management theo PN đọc `GET /api/tracking`
  (mọi PN có lịch sử production hoặc open demand, search và filter server-side,
  theo canonical demand order, có bound và phân trang) qua cùng polling /
  stale-feed như các board, còn overlay detail modeless đọc
  `GET /api/tracking/detail` — open demand với released / allocated / shortage,
  current quantity theo Area / Machine qua cùng derivation các board dùng, stock
  và allocation history, reconciliation `introduced = active + stocked +
  scrapped`, mọi Quantity Flow với lineage và PLANNED snapshot (done / current /
  future, deviation đã confirm) hoặc FLOATING actual trace (repeated Area,
  Repair, prefix kế thừa từ split), Scrap history (chính các event `SCRAPPED`,
  event đã undo được đánh dấu), và Movement history bất biến phân trang theo
  thứ tự thời gian ngược `(occurred_at DESC, id DESC)`
  (`GET /api/tracking/movements`) với original đã reverse vẫn hiển thị cạnh row
  `REVERSED` — Quantity Flow (mới nhất trước theo flow id bất biến, status không
  bao giờ là vị trí phân trang) và allocation entry cũng
  phân trang, không gì bị cắt ngoài tầm với; status derive chỉ tính stock khi còn chưa allocate (`Stocked`),
  open demand không có gì trong production và không còn stock available là
  `Open`. Migration `0012_phase11_tracking_index` thêm một composite index
  `(part_number, occurred_at, id)` trên `part_movements` cho read này. Phase 11
  cũng liệt kê breakdown theo PN của quantity đang gán cho từng Machine trong
  Management → Machines (`assigned_lines` trên `/api/machines`), đã audit ngày
  2026-09-13 (IMPLEMENTATION_ROADMAP Phase 11), và khép lại bằng expected-duration
  monitoring theo PROJECT_PROFILE §17 (thời điểm cố định `expected_by` trên mọi
  monitoring position — giá trị snapshot của Assigned Route Step hiện tại, nếu
  không thì Operation default — do UI clock chung đánh giá; chỉ advisory).
- **Phase 12 (Priority Management):** Hot list của Work Order Demand — `GET /api/hot-list` (các entry theo rank, mỗi entry có quantity hiện tại của PN theo Area / Machine), `GET /api/hot-list/candidates` (`?search=` theo PN / Work Order Number / Job Number, hoặc `?barcode=PF:PN:…`; chỉ demand eligible để vào list) và `POST /api/hot-list/changes` (command idempotent, có audit, thêm / xóa / di chuyển / undo / redo một entry đối chiếu order mà manager đã xác nhận — cùng với automatic removal entry có line trở thành allocate đủ, là các writer duy nhất của `priority_rank`; Department-gated: 404 khi không có Department active, 409 khi có nhiều — chỉ replay của một thay đổi đã commit vẫn trả lời, với `entries: null`) — và view Management → Priority thật trên đó (confirmation trước mọi thay đổi order, Undo / Redo trong session, drag-and-drop và Move Up / Move Down, add bằng search hoặc scan `PF:PN:`). Management → Work Orders chỉ remove một Hot demand line sau typed confirmation (server từ chối bằng 409 khi thiếu cờ confirmation). Migration `0013_phase12_priority` có pre-check từ chối nếu rank hiện có không dense, rồi thêm CHECK rank dương, UNIQUE `priority_rank` và audit expression index cho idempotency lookup của command.
- **Phase 13 (Workers registry và audit cấu hình môi trường — đang triển khai, slice 1, 2, 2b, 2c, 3, 4, 5, 6, 7, 8, 9, 10, 11 và 12):** `GET` / `POST /api/workers`, `PATCH /api/workers/{id}` và `PUT` / `DELETE` / `GET /api/workers/{id}/avatar` (avatar lưu trong PostgreSQL, PNG / JPEG / WebP tối đa 2 MiB), migration `0014_phase13_workers` (bảng `workers` với badge UNIQUE và CHECK dạng chuẩn hóa — trim, uppercase — cùng audit event `DELETED` và entity `Worker`; downgrade từ chối; `0015_phase13_badge_check` tạo lại CHECK đó dưới collation `"C"` để không bao giờ phụ thuộc bảng case của libc trong OS; `0016_phase13_environment_audit` mở rộng CHECK entity của audit với các entity môi trường `Department`, `Area`, `Operation`, `ScanStation` và `MachineAssetTagConfig`; downgrade từ chối; `0017_phase13_machine_audit` thêm `Machine` vào CHECK entity của audit; downgrade từ chối; `0018_phase13_pn_check_collation` tạo lại các CHECK canonical PN dưới `"C"`; downgrade từ chối) và section Administration → Workers thật; mọi write Worker được audit, và (slice 2) mọi write cấu hình môi trường (Department, Area, Operation, Scan Station, định dạng Asset Tag) cũng được audit, cùng audit cấu hình Machine, hai bản sửa lost-race và PN CHECK độc lập collation (slice 2b), cùng các parent-activity lock giúp tuần tự hóa write cấu hình của child với việc deactivate parent đồng thời (slice 2c), cùng Worker ID mode theo Area (Disabled / Fixed Worker) và Worker được ghi trên Movement production và station allocation (slice 3; `0019_phase13_worker_identity`; downgrade từ chối), cùng Worker Session được quét với sliding inactivity timeout (`GET` / `PUT /api/policies/worker-sessions`, override theo Area nằm trên Area, đăng nhập bằng badge qua `POST /api/scan-stations/{id}/badge-scans`) và section Administration → Worker sessions thật (slice 4; `0020_phase13_worker_sessions`; downgrade từ chối; Scanned session chưa chọn được trong Administration → Areas ở slice 4), cùng badge-confirmation gate của `DONE`, `QUEUE` return và Undo (ba option trên cùng policy, `confirming_badge` trên ba command, Worker của badge được command đăng nhập) với Scanned session chọn được trong Administration → Areas (slice 5; `0021_phase13_badge_confirmation`; downgrade từ chối), cùng Undo reason policy (`GET` / `PUT /api/policies/correction-permissions`, `reason` tùy chọn trên Undo command và bắt buộc khi công tắc bật, công tắc Administration → Correction permissions thật và field Reason bắt buộc trong summary Undo của Scan Station) (slice 6; `0022_phase13_undo_reason_policy`; downgrade từ chối), cùng quản lý Part Numbers (view Management → Part Numbers thật và dialog `Edit Part Number` dùng chung trên `/api/part-numbers`: tạo chỉ-tạo-mới, sửa từng phần, ảnh, tìm kiếm `/page` có giới hạn và hard delete của master record; control bút chì `Edit Part Number` trên demand line của Work Order; name đã lưu trên kết quả Add Part, row Production Board và PN Tracking) (slice 7; `0023_phase13_part_number_master`; downgrade từ chối), cùng quản lý Planned Routes (view Management → Planned Routes thật trên `/api/route-templates`: `GET /api/route-templates/management` (danh sách kèm usage), `POST /api/route-templates` (tạo), `PUT /api/route-templates/{id}` (thay toàn bộ step, mỗi step có preferred Machine tùy chọn mang tính tham khảo), `POST …/{id}/archive` (route đã từng dùng), `DELETE …/{id}` (route chưa từng dùng) và `GET …/{id}/usage`; `GET /api/route-templates` vẫn chỉ trả template active; preferred Machine được copy vào snapshot Assigned Route) (slice 8; `0024_phase13_planned_routes`: `preferred_machine_id` trên `route_steps` và `assigned_route_steps`, index `ix_assigned_routes_source_route_template_id` và entity audit `RouteTemplate`; downgrade từ chối), cùng Department display settings và Due Soon policy (rotation timing của Production Board theo Department qua `PATCH` Department, `GET`/`PUT /api/policies/due-soon`, các section Administration → Department display settings và Administration → Settings thật; mọi due countdown dùng policy của server) (slice 9; `0025_phase13_display_settings`: `board_seconds_per_row` và `board_min_page_seconds` trên `departments`, ba cột Due Soon trên `application_policy`; downgrade từ chối), cùng theme persistence của station tier (`PUT /api/scan-stations/{id}/theme-preference`, không audit; `GET /api/scan-stations/{id}/context` báo theme đã lưu; toggle theme trên route Scan Station lưu nó cho station đó khi đang kết nối) (slice 10; `0026_phase13_station_theme`: `scan_stations.theme_preference`; downgrade từ chối), cùng retention period của Movement history (`GET`/`PUT /api/policies/data-retention`, có audit; section Administration → History archival & purge thật lưu period từ 12–1200 tháng nguyên hoặc không có và nêu rằng run archival và purge chưa khả dụng — không gì đọc period, và không gì bị archive hay purge; Administration → Machine assignment là statement chỉ-đọc về hai mode của Area, còn Scan behavior nêu rằng nó chưa khả dụng) (slice 11; `0027_phase13_retention_period`: `application_policy.retention_period_months`; downgrade từ chối), cùng cấu hình Users, role và permission (`/api/roles` và `/api/users`, có audit; các section Administration → Users và Roles & permissions thật, và bảng role × correction-permission trong Administration → Correction permissions; preference theme được lưu của User chưa có writer — users chưa đăng nhập được cho đến Phase 14 slice 1, và trước Phase 14 không gì đọc user, role hay permission để cho phép hoặc từ chối một hành động) (slice 12; `0028_phase13_users_roles`: `roles`, `role_permissions` và `users` cùng ba role được seed và các entity audit `User` / `Role`; downgrade từ chối).
- **Phase 14 (đang mở — slice 1, sign-in cho application User, và slice 2, Administration enforcement):** tài khoản cục bộ bằng login name và mật khẩu, server session và quy tắc CSRF header (`/api/session`, `/api/session/password`), first-run setup Administrator đầu tiên với setup token dùng một lần được in ra server log (`/api/setup`), `PUT /api/users/{id}/password`, sign-in settings (`GET` / `PUT /api/policies/sign-in`), lệnh recovery `python -m app.cli reset-password`, và account chip, các dialog sign-in, `Set password…` của Users và Settings → User sign-in ở frontend (`0029_phase14_sign_in`: cột sign-in policy trên `application_policy`, `user_credentials`, `user_sessions` và `actor_user_id` trên `audit_events`, `machine_lifecycle_events`, `work_order_allocations`; downgrade từ chối); từ slice 2, mọi đọc Administration cần User đã sign-in và mọi write Administration cần permission của nó (các section Administration là view-only khi thiếu), cùng permission-management guard và quy tắc last-holder, route registry `app/api/route_access.py`, lệnh recovery `python -m app.cli restore-correction-permission-management`, và `actor_user_id` trên các audit row mà các write này append; từ **slice 3**, **Management enforcement**: mọi write Management cần permission của nó (Work Order save được xét theo nội dung và Hot list change theo việc nó đổi thành viên của danh sách hay chỉ đổi thứ tự), mọi lượt đọc Management cần View all current and historical production data hoặc một permission mà action của view dùng, `POST /api/allocations` chỉ còn là receiving confirmation của station Stockroom (bắt buộc `station_id`) còn Management allocate về sau trên `POST /api/allocations/management` và reverse trên `POST /api/allocations/{id}/reversals` (cả hai cần Edit Work Order Allocation), `actor_user_id` được ghi trên các row Management, request retire và reactivate Machine không còn nhận `actor`, Management command bị replay bởi User khác bị từ chối (409), route registry phân loại mọi route, và các màn hình Management yêu cầu sign-in và ẩn những thay đổi user không được làm; từ **slice 4**, **thiết bị Scan Station** (`0030_phase14_station_devices`: `scan_station_devices`, `application_policy.scan_station_role_id` và audit entity `ScanStationDevice`; downgrade từ chối, còn upgrade bị từ chối khi không role nào tên Operator — đổi tên role, upgrade, rồi đổi lại): mọi route Scan Station yêu cầu header `X-PartFlow-Station-Device` mang thiết bị mà administrator đã enroll cho station đó (enrollment code dùng một lần được đổi lấy device token; Administration → Scan Stations → `Devices…`), mỗi command của station cần permission của role áp dụng tại Scan Station (ban đầu là Operator), Scan Station ẩn các action mà role đó không cấp và hiện màn hình enroll trên thiết bị chưa enroll (xem [Enroll thiết bị Scan Station](#enroll-thiết-bị-scan-station)); và, trong **Phase 16** đang mở, **command reconciliation** chỉ đọc `python -m app.cli reconcile` (slice 1; xem [Reconciliation (chỉ đọc)](#reconciliation-chỉ-đọc)).

Các phase tiếp theo, gồm enforce authorization (Phase 14 mới có sign-in) và production deployment,
chưa hoàn tất. Vì vậy Compose hiện tại là môi trường phát triển; xem
[`docs/DEPLOYMENT.vi.md`](./docs/DEPLOYMENT.vi.md) trước khi đưa dữ liệu thật vào.

## Thành phần chính

- `frontend/` — Vite + React + TypeScript. Các view có backend (Administration
  Phase 3.5, Machines, Work Orders và Completed Work Orders, Scan Station gồm
  cả `Receive Quantity`, Production Board, Area Board, PN Tracking, Priority, Part Numbers, Planned Routes, Administration → Workers) đã kết nối API thật; mọi view đã duyệt đều là view thật, không còn view mock nào được nối.
- `backend/` — FastAPI. Application service sở hữu business rule và transaction;
  domain vocabulary độc lập framework; SQLAlchemy mapping khớp schema chuẩn.
- PostgreSQL 16 + Alembic — Movement, lifecycle event, audit event và allocation
  history là append-only, được bảo vệ bằng constraint/trigger.
- Docker Compose — stack phát triển gồm database, backend và frontend có health
  check.

Lưu Demand không tạo production quantity. Chỉ explicit release mới tạo
QuantityFlow và `RECEIVED`. Scan Station chỉ báo thành công sau khi server xác
nhận write; client không tự ghi optimistic. Một command có thể gồm SPLIT và
action, hoặc implicit `AREA_COMPLETED` + `TRANSFERRED`, nhưng vẫn idempotent theo
`device_event_id`. Undo không sửa/xóa lịch sử gốc mà ghi các Movement bù trừ.

Đặc tả chuẩn nằm ở:

- [`docs/PROJECT_PROFILE.md`](./docs/PROJECT_PROFILE.md) — domain và workflow.
- [`docs/GUI_DESIGN.md`](./docs/GUI_DESIGN.md) — UI đích đã duyệt.
- [`docs/IMPLEMENTATION_ROADMAP.md`](./docs/IMPLEMENTATION_ROADMAP.md) — phase và
  dependency.
- [`docs/SLICE1_DATA_MODEL.md`](./docs/SLICE1_DATA_MODEL.md) — vertical slice đầu.

## Frontend Phase 2

### Route

| URL | View |
|---|---|
| `/scan-station` | Station Selector; `/` redirect về đây và không tự chọn station |
| `/scan-station/:stationId` | Một Scan Station ở standard mode; station không tồn tại/inactive hiển thị lỗi rõ ràng |
| `/scan-station/:stationId/production` | Cùng station ở production mode, ẩn top navigation; đây chỉ là presentation, không phải security boundary |
| `/production-board` | Production Board chỉ đọc, dành cho màn hình lớn |
| `/production-board/kiosk` | Production Board kiosk, tự có wall-display header |
| `/management/area-board` | Management → Area Board |
| `/management/machines` | Management → Machines |
| `/management/tracking` | Management → PN Tracking (list theo PN + overlay detail modeless) |
| `/management/work-orders` | Management → Work Orders |
| `/management/work-orders/completed` | Completed Work Orders, chỉ đọc |
| `/management/planned-routes` | Planned Routes |
| `/management/part-numbers` | Part Numbers |
| `/management/priority` | Hot Work Order Demand ranking |
| `/administration` | Administration |

`/management` mở subview dùng gần nhất trong session, mặc định Area Board. Router
dùng History API nội bộ; browser back/forward hoạt động và route lạ có trang
not-found của ứng dụng.

### Cấu trúc frontend

- `src/styles/` — semantic token và shared primitive; component không hard-code
  màu theo theme.
- `src/app/` — router, theme provider (Dark default; preference theo Scan Station trên route station, chỉ session ở nơi khác), connectivity provider, registry view thật
  (`real-views.ts`), preview state chỉ dành cho development.
- `src/api/` — typed client và mapping `snake_case` ↔ `camelCase`; production-safe,
  không import `src/mocks/`.
- `src/mocks/` — dataset mẫu chỉ dành cho development; mọi view đã duyệt đều là view thật (Planned Routes, view mock cuối cùng, trở thành thật ở Phase 13 slice 8; dataset mock của nó và registry view chỉ-development cũ đã bị gỡ), nên nơi duy nhất còn đọc từ đây là preview Scan Station chỉ-development (`ScanStationMockView.tsx`, sau `import.meta.env.DEV`), và production build loại trừ hoàn toàn các dataset này.
- `src/views/<view>/` — mỗi GUI view một thư mục.
- `src/components/` — component dùng chung, gồm Area/Machine monitoring.

Production build quét sentinel và test module graph để bảo đảm mock không lọt vào
bundle.

### Xem trước trạng thái UI (chỉ development)

Thêm `?state=…` vào URL:

- `?state=loading` — skeleton.
- `?state=empty` — không có dữ liệu.
- `?state=error` — lỗi.
- `?state=long` — fixture dữ liệu dài ở view có hỗ trợ.

Ví dụ: `http://localhost:5173/management/tracking?state=long`.

Disconnected là trạng thái thật: dừng backend để shell hiển thị OFFLINE banner,
vô hiệu hóa production write và cho phép `Retry connection`. Không có local
write queue hay offline synchronization.

## Yêu cầu

- Docker có Docker Compose v2 (`docker compose`).
- Nếu chạy trực tiếp ngoài Docker (tùy chọn): Node.js 24+, Python 3.12+ và
  [uv](https://docs.astral.sh/uv/).

## Thiết lập môi trường

1. Sao chép file mẫu:

   ```bash
   cp .env.example .env
   ```

2. Chỉnh `.env` nếu cần. Giá trị mẫu chỉ dành cho development; `.env` thật đã
   được git-ignore và không được chứa secret dùng chung/production.
   `SITE_TIMEZONE` (tên múi giờ IANA, mặc định `UTC`) là lịch của nhà máy:
   backend đổi completion timestamp thành done date theo múi giờ này cho Done
   range, due outcome và ngày hiển thị của completed history — đặt theo múi
   giờ của site (ví dụ `America/Los_Angeles`) để "on time"/"late" theo ngày
   của nhà máy, không theo browser.

Lockfile `backend/uv.lock` và `frontend/package-lock.json` đã commit. Docker build
dùng `uv sync --frozen` và `npm ci`, nên checkout sạch không cần bootstrap thêm.

## Khởi động toàn bộ stack

```bash
docker compose up --build
```

- Frontend: <http://localhost:5173>
- Backend API: <http://localhost:8000>
- Health: <http://localhost:8000/api/health> hoặc qua frontend proxy tại
  <http://localhost:5173/api/health>

Dừng bằng `Ctrl+C`, sau đó:

```bash
docker compose down
```

`docker compose down -v` còn xóa volume PostgreSQL và toàn bộ dữ liệu trong đó;
không dùng trừ khi chủ động reset development database.

Sau khi đổi dependency backend/frontend, rebuild và tạo mới anonymous dependency
volume để `.venv`/`node_modules` cũ không che image mới:

```bash
docker compose up --build -V
```

## Migration database (Alembic)

Áp dụng toàn bộ migration:

```bash
docker compose exec backend uv run alembic upgrade head
```

### Tạo, reset và kiểm tra development database

PostgreSQL image chỉ đọc `POSTGRES_USER`, `POSTGRES_PASSWORD` và `POSTGRES_DB`
trong lần đầu khởi tạo volume. Đổi `.env` sau đó không đổi role trong volume cũ.
Luôn dùng user thật trong `.env`; nếu credential đã đổi thì reset volume
development có chủ đích:

```bash
# kết nối bằng user trong .env
docker compose exec db psql -U <POSTGRES_USER> -d partflow

# tạo/cập nhật schema development
docker compose up -d db backend
docker compose exec backend uv run alembic upgrade head

# kiểm tra schema
docker compose exec db psql -U <POSTGRES_USER> -d partflow -c "\dt"
docker compose exec db psql -U <POSTGRES_USER> -d partflow -c "\d part_movements"

# RESET TOÀN BỘ — phá hủy volume postgres_data và mọi development data
docker compose down -v
docker compose up -d db backend
docker compose exec backend uv run alembic upgrade head
```

Lưu ý:

- Chỉ downgrade/reset migration chưa từng share trên disposable database. Migration
  đã commit/share không sửa tại chỗ; tạo revision mới.
- Ký tự `$` trong `POSTGRES_PASSWORD` có thể bị Compose interpolate; dùng `$$`
  nếu Compose cảnh báo biến chưa được đặt.

## First-run setup và account recovery

Database mới chưa có Administrator. Khi chưa có, backend ghi một setup token dùng
một lần vào log lúc khởi động, và nút **Set up PartFlow** ở top navigation mở
dialog first-run:

```bash
docker compose logs backend | grep "Setup token"
```

Mở PartFlow, chọn **Set up PartFlow**, dán token và tạo Administrator đầu tiên.
Lần tạo đầu tiên đóng setup vĩnh viễn và token bị hủy; restart backend chỉ thông
báo token mới khi chưa có Administrator. Token là bảo vệ duy nhất của first-run
setup, nên hãy hoàn tất trước khi service truy cập được bởi người khác và hạn
chế quyền đọc log cho đến lúc đó. Mỗi process backend thông báo token riêng của nó.

**Nâng cấp lên Phase 14 slice 2.** Trước và sau khi deploy nó, chạy lệnh sau trong database shell (`docker compose exec db psql -U <POSTGRES_USER> -d partflow`) để đếm các active user có mật khẩu mà role giữ từng permission-management key:

```sql
SELECT rp.permission, count(*) AS holders
FROM users u
JOIN user_credentials c ON c.user_id = u.id
JOIN role_permissions rp ON rp.role_id = u.role_id
WHERE u.is_active
  AND rp.permission IN ('MANAGE_USERS_AND_ROLES', 'MANAGE_CORRECTION_PERMISSIONS')
GROUP BY rp.permission;
```

Thiếu row nghĩa là không có holder. Cả hai số đếm nên ít nhất là 1, hoặc `MANAGE_USERS_AND_ROLES` vắng mặt (khi đó first-run setup đang mở). Nếu `MANAGE_CORRECTION_PERMISSIONS` không có holder trong khi `MANAGE_USERS_AND_ROLES` có, sẽ không ai cấp lại nó được khi slice 2 chạy: trước khi deploy, cấp nó trong Administration; sau khi deploy, backend ghi cảnh báo lúc khởi động và bạn khôi phục từ host:

```bash
docker compose exec backend uv run python -m app.cli restore-correction-permission-management --role-name <role>
```

Lệnh chỉ chạy khi không có active user có mật khẩu nào được quản lý correction permission, và role được nêu phải có một thành viên như vậy. Nó cấp permission cho role đó, append một audit row và không đổi gì khác. Mỗi trường hợp bị từ chối (đã có holder, không có role đó, role không có active user có mật khẩu) không ghi gì và thoát với exit code 1.

Nếu Administrator duy nhất quên mật khẩu, reset từ host:

```bash
docker compose exec backend uv run python -m app.cli reset-password --login-name <name>
```

Mật khẩu mới được gõ ở prompt, không bao giờ trên command line. Lệnh chỉ chạy với
user đã có mật khẩu và chỉ khi đã có Administrator; nó không bao giờ tạo
Administrator. Nó đặt mật khẩu tạm, kết thúc các sign-in của user và xóa khóa.
Nếu kết nối database bị lỗi đúng lúc đang lưu việc reset, lệnh báo kết quả không
xác định (exit code 2); hãy chạy lại lệnh, vì nó đặt lại mật khẩu trong mọi trường hợp.

Backend đọc `SESSION_COOKIE_SECURE` (mặc định `false`, vì development stack chạy
trên HTTP thuần). Đặt `true` trong environment của backend khi PartFlow được phục
vụ qua HTTPS để session cookie chỉ gửi trên kết nối an toàn; `compose.yaml` cho
development không truyền biến này.

## Enroll thiết bị Scan Station

Từ Phase 14 slice 4, Scan Station chỉ hoạt động từ browser mà administrator đã enroll cho station đó; station development được enroll theo cùng cách. Sign-in bằng user được quản lý Scan Stations, mở Administration → Scan Stations → `Devices…` của station và chọn `Enroll device…`. Nhập code dùng một lần (`XXXXX-XXXXX`, hiệu lực 15 phút, dùng một lần, chỉ hiện một lần) trên station trong thời gian đó: browser của station đổi nó lấy device token, lưu trong browser storage và gửi trong `X-PartFlow-Station-Device` ở mọi request. Browser bị xóa storage phải được enroll lại; `Re-enroll…` thay một thiết bị khi code mới được dùng, và `Revoke…` dừng ngay một thiết bị bị mất. Khi role áp dụng tại Scan Station giữ một correction permission (ban đầu là Undo recent eligible scans), enroll còn cần permission quản lý correction permission. Một station đã enroll được làm gì theo các permission của role được đánh dấu **Applied at Scan Stations** trong Roles & permissions (ban đầu là Operator). Device token và enrollment code là bearer credential: hãy phục vụ PartFlow qua HTTPS và giữ header này ngoài log của reverse proxy ([hướng dẫn triển khai](./docs/DEPLOYMENT.vi.md)).

## Reconciliation (chỉ đọc)

`python -m app.cli reconcile` kiểm tra trạng thái production đang lưu khớp với
Movement history, allocation và các canonical identity rule. Nó chạy trong một
database snapshot chỉ đọc, in một JSON report ra stdout và không bao giờ đổi dữ
liệu hay repair finding.

```bash
f=reconcile.json
docker compose exec -T backend uv run python -m app.cli reconcile > "$f"; rc=$?
python3 -c 'import json,sys; r=json.load(open(sys.argv[1])); assert r["exit_code"]==int(sys.argv[2]); print(r["result"], r["exit_code"])' "$f" "$rc" \
  || echo "could not run: no complete report (exit $rc)"
docker compose exec -T backend uv run python -m app.cli reconcile --check j     # identity check only
```

Option: `--check ID` (lặp lại được, `a` đến `j`), `--statement-timeout SECONDS`
(1-3600, mặc định 300) và `--max-findings N` (1-10000, mặc định 100). Exit code:
`0` sạch, `1` mismatch, `2` không chạy được. Exit status chỉ có giá trị khi report
là một JSON document hoàn chỉnh có `exit_code` bằng nó; report rỗng hoặc không
parse được nghĩa là không chạy được. Check (g) và (h) báo `not_applicable` cho
đến khi có Movement-history archival và database-role hardening. Không chạy
migration khi nó đang chạy. Các check, dạng chạy trên staging và quy tắc vận hành
nằm ở [`docs/deployment/OPERATIONS_RUNBOOK.md`](./docs/deployment/OPERATIONS_RUNBOOK.md)
§7.

## IntelliJ IDEA / PyCharm

Thiết lập tùy chọn nhưng hữu ích cho Database Tools và SQL inspection:

1. Tạo PostgreSQL data source: host `localhost`, port `5432`, database
   `partflow`, credential từ `.env`; introspect cả `public` và `pg_catalog`.
2. Đặt Project SQL Dialect là **PostgreSQL**.
3. Map project hoặc `backend/` tới `partflow.public` trong SQL Resolution Scopes.
4. Refresh data source sau mỗi migration/reset.

IDE có thể báo false-positive với trigger/function do migration tạo ở fragment
khác, hoặc pytest class không có `__init__`. Quality gate chuẩn vẫn là command
trong container bên dưới.

## Quality gate

Linux container là môi trường chuẩn. Khởi động stack bằng `docker compose up -d`
trước khi chạy.

### Backend

```bash
docker compose exec backend uv run ruff format --check .
docker compose exec backend uv run ruff check .
docker compose exec backend uv run mypy app tests
docker compose exec backend uv run pytest
docker compose exec backend uv run alembic upgrade head
```

Test backend gồm:

- behavior test cho `/api/health`;
- unit test của route registry `app/api/route_access.py` (`tests/test_route_access.py`): test fail với mọi route registry chưa phân loại, nên route mới phải được thêm vào đó; test gọi route cần sign-in hoặc permission qua `client_as` / `admin_of` của `tests/auth_harness.py`, không qua client ẩn danh; lệnh gọi route Scan Station đi qua `station_device_client` (thiết bị đã enroll cho station được nhắm tới), và route station mới phải khai báo `RequireStationDevice` và được phân loại trong `route_access.py`;
- unit test normalization PN và badge Worker (`tests/test_worker_badge_normalization.py`);
- unit test hash mật khẩu scrypt và password policy (`tests/test_password_hashing.py`, `tests/test_password_policy.py`);
- integration test dùng PostgreSQL thật cho migration/schema, environment API,
  Machine lifecycle, Work Order intake/release, transfer, Machine/direct Area
  processing, split/merge lineage, correction/Undo, Stockroom/allocation và
  Workers API (`tests/test_workers_api.py`) và audit cấu hình môi trường
  (`tests/test_environment_audit_api.py`), race parent-activity của cấu hình môi trường (`tests/test_environment_parent_activity_api.py`) và audit cấu hình Machine (`tests/test_machine_audit_api.py`) và Worker identity (`tests/test_worker_identity_api.py`) và Worker Session (`tests/test_worker_sessions_api.py`) và badge-confirmation gate (`tests/test_badge_confirmation_api.py`) và sign-in (`tests/test_authentication_api.py`), first-run setup (`tests/test_first_run_setup_api.py`), route authorization (`tests/test_route_authorization_api.py`), Management authorization (`tests/test_management_authorization_api.py`), quản lý permission (`tests/test_permission_management_api.py`), thiết bị Scan Station (`tests/test_station_devices_api.py`) và lệnh `app.cli` (`tests/test_cli.py`, `tests/test_reconciliation.py` — integration test trên database clone từ template) và Undo reason policy (`tests/test_undo_reason_policy_api.py`) và quản lý Part Numbers (`tests/test_part_number_management_api.py`) và quản lý Planned Routes (`tests/test_route_template_management_api.py`) và Department display settings cùng Due Soon policy (`tests/test_display_settings_api.py`) và theme preference của Scan Station (`tests/test_station_theme_api.py`) và retention period của Movement history (`tests/test_retention_policy_api.py`) và cấu hình Users, role và permission (`tests/test_users_roles_api.py`). Module schema của mỗi phase dừng ở
  revision biên của chính nó (đến `0013_phase12_priority` cho Phase 12);
  `tests/test_phase13_schema.py` giữ phần coverage ở head (`0014_phase13_workers`,
  `0015_phase13_badge_check`, `0016_phase13_environment_audit`, `0017_phase13_machine_audit`, `0018_phase13_pn_check_collation`, `0019_phase13_worker_identity`, `0020_phase13_worker_sessions`, `0021_phase13_badge_confirmation`, `0022_phase13_undo_reason_policy`, `0023_phase13_part_number_master`, `0024_phase13_planned_routes`, `0025_phase13_display_settings`, `0026_phase13_station_theme`, `0027_phase13_retention_period` và `0028_phase13_users_roles`, còn `tests/test_phase14_schema.py` giữ các revision Phase 14 `0029_phase14_sign_in` và `0030_phase14_station_devices` (head duy nhất): constraint của bảng `workers`, vocabulary audit
  mở rộng gồm các entity audit môi trường, các downgrade từ chối và
  models↔migration parity).

Integration test tạo database tạm `partflow_test_*`; role cấu hình phải có quyền
tạo database. Test kiểm tra atomicity, constraint, append-only, idempotent replay,
conflicting reuse, lock/race, projection replay và conservation ở từng phase.

### Frontend

```bash
docker compose exec frontend npm run format
docker compose exec frontend npm run format:check
docker compose exec frontend npm run lint
docker compose exec frontend npm run typecheck
docker compose exec frontend npm run test
docker compose exec frontend npm run build
```

### Chạy trực tiếp trên host (tùy chọn)

Có thể bỏ prefix `docker compose exec …` và chạy từ `backend/` hoặc `frontend/`,
nhưng đây chỉ là best effort. `DATABASE_URL` phải resolve được; integration test
cần service `db` đang chạy và port 5432 publish ra host. Nếu kết quả host khác
container, kết quả Linux container là chuẩn.

## Ghi chú Docker development

Format frontend trong container:

```bash
docker compose exec frontend npm run format
```

Sau đó kiểm tra:

```bash
docker compose exec frontend npm run format:check
```

Quality gate frontend đầy đủ:

```bash
docker compose exec frontend sh -lc "npm run format:check && npm run lint && npm run typecheck && npm run test && npm run build"
```

Quality gate backend đầy đủ:

```bash
docker compose exec backend sh -lc "uv run ruff format --check . && uv run ruff check . && uv run mypy app tests && uv run pytest"
```

## Continuous integration

`.github/workflows/ci.yml` chạy cùng quality gate trên mỗi push vào `main` và
pull request: backend format/lint/mypy/migration/pytest với PostgreSQL 16;
frontend format/lint/typecheck/test/production build; job Docker riêng kiểm tra
`docker compose build`.

## Cấu trúc repository

```text
frontend/          Vite + React + TypeScript
  src/styles/      semantic token và shared primitive
  src/app/         router, theme, connectivity, real view registry
  src/api/         typed API client production-safe
  src/mocks/       fixture chỉ development, không vào production build
  src/views/       một thư mục cho mỗi view
  src/components/  presentation component dùng chung
backend/
  app/api/         HTTP route
  app/application/ application service và transaction
  app/core/        cấu hình
  app/domain/      domain vocabulary độc lập framework
  app/infrastructure/ database engine, connectivity và SQLAlchemy mapping
  tests/           pytest suite
  alembic/         migration environment và revisions
compose.yaml       development stack: db, backend, frontend
docs/              tài liệu chuẩn của project
```
