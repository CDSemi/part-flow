# Roadmap triển khai PartFlow

> **Bản gốc:** [`IMPLEMENTATION_ROADMAP.md`](IMPLEMENTATION_ROADMAP.md). Đồng bộ với bản tiếng Anh tại commit `a7949e3` cộng các thay đổi chưa commit ngày 2026-11-12. Bản tiếng Anh là nguồn chuẩn (source of truth); khi hai
> bản khác nhau, bản tiếng Anh thắng.

Tài liệu này cho biết PartFlow được xây theo thứ tự nào, mỗi phase gồm những gì, code hiện có những gì, và những gì
còn thiếu hoặc đang bị giới hạn tạm thời. Người đọc là lập trình viên hoặc người vận hành cần biết hệ thống làm được
gì lúc này và phần việc còn lại nằm ở đâu. Tài liệu đi cùng các tài liệu chuẩn (canonical) khác:
[`PROJECT_PROFILE.md`](./PROJECT_PROFILE.md) sở hữu quy tắc nghiệp vụ, bất biến (invariant), workflow và phạm vi sản
phẩm; [`GUI_DESIGN.md`](./GUI_DESIGN.md) sở hữu giao diện người dùng đã duyệt; roadmap này sở hữu thứ tự triển khai,
ranh giới phase, phụ thuộc, trạng thái triển khai hiện tại và các giới hạn tạm thời; các tài liệu slice, như
[`SLICE1_DATA_MODEL.md`](./SLICE1_DATA_MODEL.md), là chi tiết triển khai cấp dưới; và
[`DEPLOYMENT.md`](./DEPLOYMENT.md) sở hữu việc triển khai (deployment). Khác với các tài liệu chuẩn khác, roadmap này
phải khớp với code: khi code thay đổi, phase liên quan được viết lại ở đây.

## Trạng thái hiện tại

**PartFlow làm được gì hôm nay.** PartFlow theo dõi số lượng sản xuất vật lý theo Part Number (PN) trong toàn nhà
máy. Quản lý nhập Work Order bằng tay hoặc import từ file giá trị phân cách bằng dấu phẩy (CSV) hay Excel, rồi release
số lượng vào sản xuất. Người vận hành tại Scan Station nhận số lượng mới, chuyển số lượng giữa các Area, giao cho
Machine, đánh dấu xong gia công tại Area, tách và gộp số lượng, gửi số lượng đi sửa (repair), ghi nhận scrap và số
lượng tìm thấy thêm, và hoàn tác (undo) lỗi vừa thao tác. Stockroom nhận số lượng đã hoàn thành và phân bổ
(allocate) cho demand của Work Order, việc này làm Work Order hoàn tất. Production Board, Area Board và PN Tracking
cho thấy mỗi số lượng đang ở đâu, và Hot list (Priority) xếp hạng các demand khẩn. Administration cấu hình Department,
Area, Operation, Scan Station, Machine, Worker cùng phiên badge của họ, User, role và permission, và các chính sách
ứng dụng. User đăng nhập, mọi thao tác Management và Administration được kiểm tra theo permission, và chỉ thiết bị
đã enroll mới hoạt động được như Scan Station. Bộ production stack đầy đủ (image, file Compose, script release,
backup, diễn tập restore, database role, giám sát và đối soát (reconciliation)) đã sẵn sàng; nó chưa được cài lên
máy chủ của công ty.

**Các phase.**

| Phase | Trạng thái | Tóm tắt |
| --- | --- | --- |
| Phase 1 — Repository Foundation | Đã đóng (2026-07-23) | Frontend, backend, PostgreSQL, Alembic, Docker Compose, health endpoint, công cụ kiểm tra và tích hợp liên tục (CI). |
| Phase 2 — Frontend Design System and Application Shell | Đã đóng (2026-08-13) | Design token, theme Dark/Light, routing, phát hiện kết nối, các trạng thái view và ranh giới build production không có mock. |
| Phase 3 — Minimum Canonical Domain and Data Foundation | Đã đóng (2026-08-18) | Từ vựng domain, quy tắc PN chuẩn và mười hai bảng đầu tiên, với Movement chỉ-ghi-thêm (append-only). |
| Phase 3.5 — Minimum Environment Setup | Đã đóng (2026-08-19) | Department, Area, Operation, Scan Station, Machine và định dạng Asset Tag, cùng API và màn hình. |
| Phase 4 — Manual Work Order Intake and Production Release | Đã đóng (2026-08-24) | Work Order với các dòng demand, nhãn PN và lệnh release vào sản xuất tường minh, release được từng phần. |
| Phase 5 — Scan Station Transfer to an Area Queue | Đã đóng (2026-08-25) | Scan Station thật và việc chuyển số lượng giữa các Area, với xác nhận lệch route. |
| Phase 6 — One-Shot Machine Assignment and Area Completion | Đã đóng (2026-08-26) | Giao Machine, trả về `QUEUE` và hoàn tất Area bằng `DONE` ở Area có Machine. |
| Phase 7 — Direct Area Processing (Areas Without Machines) | Đã đóng (2026-08-28) | Gia công trực tiếp và `DONE` không Machine ở Area không có Machine. |
| Phase 8 — Quantity SPLIT and MERGED Workflows | Đã đóng (2026-08-31) | Số lượng một phần trên mọi lệnh Scan Station và thao tác `Combine quantities` tường minh. |
| Phase 9 — Undo, Corrections, and Auditable Quantity Events | Đã đóng (2026-09-01) | Undo theo cấp lệnh, Repair, Scrap và thêm số lượng. |
| Phase 10 — Stockroom and WorkOrderAllocation | Đã đóng (2026-09-01) | Nhận vào Stockroom, phân bổ cho demand, hoàn tất Work Order suy ra từ phân bổ và lịch sử đã hoàn tất. |
| Phase 10.5 — Scan Station Receive Quantity Gap Closure | Đã đóng (2026-09-04) | `Receive Quantity` tại Scan Station sản xuất cho PN không có demand đang hoạt động. |
| Phase 11 — Read Models and Monitoring Views | Đã đóng (2026-09-14) | Production Board, Area Board, PN Tracking, phân rã theo PN của Machine và giám sát thời lượng dự kiến. |
| Phase 12 — Priority Management | Đã đóng (2026-10-04) | Hot list có audit, tự gỡ các mục không còn hoạt động. |
| Phase 13 — Full Administration and Production Identity Configuration | Đã đóng (2026-10-07) | Worker, nhận diện Worker và phiên, quản lý master data, chính sách, User và role dưới dạng cấu hình. |
| Phase 14 — Authentication, Role Enforcement, and Authorized Management Corrections | Đã đóng (2026-10-08) | Đăng nhập, cưỡng chế permission, thiết bị Scan Station đã enroll và các workflow hiệu chỉnh có thẩm quyền. |
| Phase 15 — File-Based Work Order Import | Đã đóng (2026-10-08) | Tạo và cập nhật Work Order từ file CSV hoặc Excel. |
| Phase 16 — Deployment, Production Hardening, and Admin Maintenance | Đang làm | Production stack đã triển khai xong; bước tiếp theo là go-live trên NAS của công ty; lưu trữ (archival) lịch sử Movement đã lên kế hoạch. |

**Lược đồ cơ sở dữ liệu.** Head migration Alembic hiện tại là `0032_phase14_route_adjusted`
(`backend/alembic/versions/20261007_0032_phase14_route_adjusted.py`). Phase 15 và 16 không thêm migration nào.

**Quyết định của chủ sở hữu ngày 2026-11-09: go-live trực tiếp.** PartFlow được đưa vào sử dụng thật trên Synology
NAS của công ty ngay khi production stack được cài xong. Không có giai đoạn pilot và không có danh sách release gate;
lỗi được sửa ở các bản phát hành sau trong khi hệ thống đang được dùng. Chỉ những gì phần mềm cần để chạy là bắt
buộc; backup, giám sát và các kiểm tra trên máy chủ được thiết lập sau go-live, khi thuận tiện, và không chặn điều gì
(DEPLOYMENT §1 và §5).

**Các tài liệu khác ở đâu.**

- Quy tắc nghiệp vụ: [`PROJECT_PROFILE.md`](./PROJECT_PROFILE.md).
- Giao diện đã duyệt: [`GUI_DESIGN.md`](./GUI_DESIGN.md), với bản tham chiếu trực quan
  [`mockups/partflow-gui-mockup-v18.html`](./mockups/partflow-gui-mockup-v18.html).
- Slice mô hình dữ liệu đầu tiên: [`SLICE1_DATA_MODEL.md`](./SLICE1_DATA_MODEL.md).
- Triển khai: [`DEPLOYMENT.md`](./DEPLOYMENT.md), với các quy trình hằng ngày trong
  [`deployment/OPERATIONS_RUNBOOK.md`](./deployment/OPERATIONS_RUNBOOK.md) và hướng dẫn NAS trong
  [`deployment/SYNOLOGY_NAS.md`](./deployment/SYNOLOGY_NAS.md).

## Nguyên tắc triển khai

- Xây theo lát dọc (vertical slice); hoàn tất trọn vẹn một workflow trước khi mở rộng.
- Không bao giờ chuyển số lượng trước khi hệ thống có workflow đưa số lượng vào thật: Work Order Intake và release sản xuất đi trước mọi workflow chuyển thật.
- Giữ phân lớp Presentation → Application → Domain → Infrastructure.
- Chỉ dùng dữ liệu mock cho tới khi slice backend tương ứng tồn tại; hành vi mock không bao giờ lọt vào production.
- Mọi tình huống mơ hồ luôn cần xác nhận tường minh trước khi ghi; dữ liệu vào không rõ hoặc không hợp lệ bị từ chối và không ghi gì.
- Ghi dữ liệu sản xuất bị chặn khi mất kết nối; đồng bộ scan offline được hoãn và chưa được duyệt.
- Không triển khai tích hợp hệ thống hoạch định nguồn lực doanh nghiệp (ERP) trong sản phẩm khả dụng tối thiểu (MVP).
- Mọi hành vi được mô tả là `deliberately absent`, `temporary`, `placeholder`, `later`, hay được hoãn theo cách khác phải nêu đúng một phase sau sở hữu nó, hoặc được liệt kê tường minh trong `Deferred` / một quyết định mở chuẩn. Một phase không bao giờ được đóng khi còn hành vi mục tiêu chưa có chủ.
- Trước khi đóng bất kỳ phase nào, audit `PROJECT_PROFILE.md` + `GUI_DESIGN.md` + code production hiện tại đối chiếu với roadmap này. Chỉ đóng phase khi mọi hành vi mục tiêu trong phạm vi đã được triển khai và mọi chỗ cố ý bỏ qua đều có chủ sở hữu ở phase sau rõ ràng.

## Phase 1 — Repository Foundation

Phạm vi:

- frontend React + TypeScript
- backend FastAPI
- PostgreSQL
- Alembic
- Docker Compose
- health endpoint
- kết nối frontend/backend/database
- nền tảng formatter, linter, kiểm tra kiểu (type check) và test

Tiêu chí hoàn tất:

- Môi trường phát triển khởi động bằng các lệnh đã được tài liệu hóa.
- Frontend gọi health endpoint của backend; backend kết nối PostgreSQL.
- Định dạng, lint, kiểm tra kiểu và các test ban đầu chạy thành công.

**Đã có.** Frontend React + TypeScript, backend FastAPI, PostgreSQL, Alembic, stack Docker Compose cho phát triển
(`compose.yaml`), endpoint `/api/health`, công cụ formatter, linter, kiểm tra kiểu và test, và workflow CI.

**Giới hạn tạm thời.** Không có.

## Phase 2 — Frontend Design System and Application Shell

Phạm vi:

- token dùng chung
- ngữ cảnh dark và light
- routing/điều hướng ứng dụng
- các mock view đã duyệt, gồm view Work Orders trong application shell (modal New Work Order với WO Number/due date tùy chọn, flow Add Part ưu tiên nhập tay, sửa dòng của Work Order OPEN, ô nhập ngày dạng lịch, kiểm tra hợp lệ mock, bảo vệ thay đổi chưa lưu)
- phát hiện kết nối nhanh: sự kiện online/offline của trình duyệt cộng polling `/api/health` khoảng mỗi 1 giây, kiểm tra lại khi focus/hiển thị (WebSocket/SSE nằm ngoài phạm vi và được hoãn)
- dữ liệu mock chỉ cho phát triển, đặt sau một ranh giới build production thật (kiểm tra mock sentinel trong build)
- các trạng thái loading, empty, error, mất kết nối và dữ liệu dài

Phase 2 là phần trình bày frontend cộng hành vi mock chỉ dành cho phát triển.

Không phải mục tiêu:

- quy tắc nghiệp vụ production trong các component mock
- triển khai domain hay migration database
- API nghiệp vụ ở backend
- ghi dữ liệu sản xuất lưu bền (mọi thao tác lưu ở Phase 2 chỉ thay đổi trạng thái mock dành cho phát triển)
- tích hợp ERP

**Đã có.**

- Design token ngữ nghĩa với theme Dark và Light chuyển đổi được (mặc định Dark). Nơi lưu lựa chọn theme được mô tả ở
  Phase 13 (tầng Scan Station) và Phase 14 (tầng User).
- Routing và điều hướng theo URL; Management nhớ sub view dùng gần nhất trong phiên.
- Phát hiện kết nối nhanh (GUI_DESIGN §3.6): sự kiện online và offline của trình duyệt, polling health khoảng mỗi
  giây với timeout request nhỏ hơn chu kỳ polling và không có probe chồng nhau, kiểm tra lại khi tab lấy lại focus
  hoặc hiển thị lại, probe thụ động không bao giờ chuyển giao diện sang trạng thái "connecting", banner OFFLINE cố
  định với Retry, các nút ghi bị vô hiệu khi mất kết nối, và ô nhập của Scan Station được bật lại và lấy lại focus khi
  kết nối phục hồi. Không có push qua WebSocket hay server-sent events (SSE) và không có hàng đợi ghi offline.
- Các trạng thái loading, empty, error, mất kết nối và dữ liệu dài, với preview `?state=` chỉ dành cho phát triển.
- Ranh giới build production: mock view và dataset chỉ được nạp khi `import.meta.env.DEV` là true, `src/mocks/` không
  bao giờ vào bundle production, và `npm run build` thất bại khi tìm thấy mock sentinel trong asset production
  (`src/production-boundary.test.ts` cũng canh đồ thị module production). Cả mười view đã duyệt lúc đầu được dựng ở
  đây dưới dạng mock view và về sau trở thành view thật trên API server (Phase 3.5 đến 13); `src/app/real-views.ts`
  nay liệt kê cả mười, và không còn mock view chỉ-cho-phát-triển nào được đăng ký.
- Bộ canh nội dung hiển thị tự động (`src/rendered-copy.test.ts`) và việc chuyển từ vựng Purchase Order sang Work
  Order trên toàn bộ tài liệu, code, route và test.

**Giới hạn tạm thời.** Bản preview Scan Station mock chỉ dành cho phát triển (`ScanStationMockView.tsx`, mở qua
`?preview=mock` trong bản build phát triển) và các dataset mock của nó vẫn còn; chúng không bao giờ vào bản build
production. Việc gỡ bỏ chúng được liệt kê trong `Deferred`.

## Phase 3 — Minimum Canonical Domain and Data Foundation

Chỉ phần nền tảng mà Work Order Intake nhập tay và release sản xuất cần:

- Department
- Area
- Operation
- PartNumber master là metadata hiện tại tùy chọn, khóa theo chuỗi PN chuẩn (chuẩn hóa viết hoa, không khoảng trắng; tạo khi dùng lần đầu — không yêu cầu nạp sẵn danh mục; các bảng production giữ giá trị PN chuẩn riêng và không bao giờ tham chiếu master bằng FK)
- WorkOrder (`work_order_number` bên ngoài nullable; duy nhất chỉ với các số khác null)
- WorkOrderDemand (`request_type IN ('NEW','MODIFY')`)
- RouteTemplate
- RouteStep
- AssignedRoute (tùy chọn — chỉ flow `PLANNED` mới có)
- QuantityFlow với `route_mode` (`FLOATING` mặc định / `PLANNED`) và `assigned_route_id` nullable — **nền dữ liệu phải hỗ trợ Floating Route ngay từ đầu**
- PartMovement (gồm khái niệm cột có kiểu `movement_reason` cho các Movement Repair về sau)
- projection vị trí hiện tại được suy ra

Quy tắc:

- Giữ nguyên tên trường chuẩn của PartMovement khi các trường đó được đưa vào: `station_id`, `occurred_at`, `server_received_at`, `device_event_id`, `movement_reason`.
- Không đưa vào tên cạnh tranh như `client_event_id`.
- Lịch sử Movement là bất biến; tính toàn vẹn số lượng được cưỡng chế; các bất biến domain được test bao phủ.
- Vết của Floating Route được suy ra từ lịch sử Movement — không có bảng lịch sử route thứ hai có thể sửa.

**Đã có.**

- Từ vựng domain độc lập framework trong `backend/app/domain/`: quy tắc PN chuẩn (cắt khoảng trắng hai đầu, từ chối
  khoảng trắng bên trong, đổi sang CHỮ HOA; `InvalidPartNumberError`) và các enumeration, mỗi cái được mở rộng theo
  kiểu chỉ-thêm bởi phase đưa vào giá trị mới.
- Các mapping SQLAlchemy trong `backend/app/infrastructure/models.py` và migration `0002_phase3_domain`, tạo mười hai
  bảng với tên ràng buộc và index tường minh: `departments`, `areas`, `operations`, master tùy chọn `part_numbers`
  (chuỗi PN chuẩn làm khóa chính tự nhiên, không có id thay thế, và không có khóa ngoại nào từ bảng production, nên
  master luôn xóa cứng được — PROJECT_PROFILE §8.1 và §28), `work_orders` (số bên ngoài nullable, duy nhất trong các số
  khác null), `work_order_demands` (Job Number là mảng text thuần — metadata, không bao giờ là tổng hợp),
  `route_templates` và `route_steps` (không có cột version; snapshot giữ lịch sử), các bảng snapshot độc lập
  `assigned_routes` và `assigned_route_steps`, `quantity_flows` (route mode, `assigned_route_id` có mặt đúng khi
  `PLANNED`, cột vị trí hiện tại `current_area_id`, và đích tham chiếu ghép `(id, part_number)`), và bảng append-only
  `part_movements` (số lượng dương, khóa idempotency duy nhất, một khóa ngoại ghép để Movement không bao giờ mang PN của
  flow khác, và một trigger cấp câu lệnh từ chối UPDATE, DELETE và TRUNCATE ngay trong PostgreSQL).
- Trên production stack, database role của ứng dụng chỉ có SELECT và INSERT trên `part_movements` (database role của
  Phase 16); database phát triển và test giữ một owner role duy nhất, nơi chỉ riêng trigger ràng buộc mọi đường ghi
  không tắt nó.

**Giới hạn tạm thời.** Không có. Bảng `audit_events` dùng chung thuộc Phase 4, và các bảng môi trường thuộc Phase 3.5.

## Phase 3.5 — Minimum Environment Setup (named prerequisite — no later phases renumbered)

Năng lực quản trị nhỏ nhất cần để vận hành các slice sản xuất đầu tiên, được tách riêng có chủ đích khỏi phase
Administration đầy đủ về sau (Phase 13). Nó nằm sau nền tảng domain (Phase 3) và **trước khi bất kỳ workflow sản xuất
thật nào cần một môi trường đã cấu hình**: Phase 4 cần Department, Area và Operation đã cấu hình để release, và Phase 5
cần Scan Station đang hoạt động và gắn với Area.

Phạm vi — quản lý cấu hình cho đúng các mục:

- Department
- Area (định danh, thuộc tính màu/hiển thị, cờ terminal)
- Operation (theo Area)
- Scan Station (Station ID, Area được gắn, trạng thái hoạt động)
- Machine (theo Area, trạng thái vòng đời và bảo trì) — được cấu hình qua **Management → Machines** (GUI_DESIGN §12),
  dựa trên permission cho các vai trò sản xuất có thẩm quyền thay vì một panel Administration, nhưng vẫn là một phần của
  điều kiện tiên quyết cài đặt tối thiểu này: Machine phải được cấu hình trước khi chạy sản xuất thật
- Lịch sử sự kiện vòng đời Machine — bảng append-only chuyên dụng **`machine_lifecycle_events`** (tên chuẩn) lưu các
  sự kiện vòng đời `RETIRED` và `REACTIVATED` mà PROJECT_PROFILE §8.6 yêu cầu, được tạo ở phase này cùng với
  `machines`. Một lần retire hoặc reactivate và sự kiện vòng đời của nó commit nguyên tử — một transaction, không có
  thay đổi vòng đời nào thiếu sự kiện và không có sự kiện nào thiếu thay đổi — và các sự kiện đã ghi là bất biến/chỉ
  ghi thêm. Mỗi sự kiện ghi ngữ cảnh vòng đời chuẩn: loại sự kiện, định danh Machine, thời điểm xảy ra, lý do, trạng
  thái trước/sau, và Area trước và hiện tại khi Machine được chuyển trong lúc đã retire. Đây là lưu trữ vòng đời/lịch
  sử Machine, có chủ đích **không phải** một framework audit chung và **không phải** cơ chế `audit_events` của Slice 1
  (SLICE1_DATA_MODEL §16); Phase 3.5 không tạo bảng `audit_events`. Các thao tác ghi **cấu hình** Machine — tạo, sửa
  metadata và ngữ cảnh bảo trì, bắt đầu/xóa bảo trì, và phần thay đổi cấu hình mà bản nháp retire hay một lần
  reactivate mang theo — được audit trong `audit_events` (Phase 13), còn bản thân việc retire và reactivate chỉ được ghi
  dưới dạng các sự kiện vòng đời này. Retire/reactivate Machine là thao tác Management, nên tác nhân của một sự kiện
  vòng đời là User đã đăng nhập (Phase 14); Worker là định danh audit sản xuất trong phạm vi Scan Station, tách biệt với
  User, và không bao giờ gắn với sự kiện vòng đời Machine.
- các cờ active/inactive bắt buộc và quyền sở hữu barcode chính xác như PROJECT_PROFILE §10 định nghĩa — không bịa ra
  namespace barcode nào khác: Area mang `PF:AREA:<stable-id>`; Machine mang `PF:MACHINE:<asset-tag>`, luôn suy ra từ
  Asset Tag bất biến; Department không có barcode; Operation không có trường barcode; Scan Station được nhận diện bằng
  Station ID ổn định và Area được gắn — không có `PF:STATION` hay bất kỳ barcode trạm nào khác. Điều này gồm định dạng
  Asset Tag trong Administration → Barcode configuration (tiền tố + dãy số đệm số 0, PROJECT_PROFILE §8.6) mà việc tạo
  Machine cần để tự gán Asset Tag

Tường minh không thuộc điều kiện tiên quyết này: bất kỳ workflow sản xuất hay thao tác ghi sản xuất nào (Phase 4 đến
6); bảng `audit_events` dùng chung (Phase 4, SLICE1_DATA_MODEL §16); việc quản lý Planned Routes và Part Numbers thật
(Phase 13, qua Management → Planned Routes và Management → Part Numbers); và phần năng lực quản trị còn lại — Worker,
User và role, quản lý phân quyền, chính sách Worker session, correction permission và thiết lập thời hạn lưu giữ lịch
sử Movement (Phase 13, với cưỡng chế ở Phase 14 và thực thi archival và purge ở Phase 16). Các thiết lập chung và chính
sách hành vi scan được liệt kê trong `Deferred`.

**Đã có.**

- Lưu trữ (migration `0003_phase35_environment`): `areas` có thêm mô tả, màu, cờ terminal và barcode ổn định
  `PF:AREA:<id>` do server gán (một trigger chỉ-gán-một-lần canh giữ); `operations` có thêm mô tả, thời lượng dự kiến
  tùy chọn và cờ external; các bảng mới `scan_stations` (Station ID ổn định, an toàn cho URL, làm khóa tự nhiên, Area
  được gắn, cờ hoạt động, không có barcode trạm), `machines` (Asset Tag bất biến tự gán, được trigger canh giữ, tên hiển
  thị duy nhất trong các Machine đang hoạt động của một Area, override bảo trì tường minh, `state_changed_at`, ngày
  retire), bảng append-only `machine_lifecycle_events`, và định dạng Asset Tag dạng singleton với bộ đếm không bao giờ
  dùng lại.
- API: `/api/departments`, `/api/areas`, `/api/operations`, `/api/scan-stations` và
  `GET`/`PUT /api/barcode-configuration/machine-asset-tag-format` (chỉ tạo, đọc, cập nhật và ngưng hoạt động — không có
  endpoint DELETE; các trường do server sở hữu bị từ chối), và `/api/machines` (liệt kê, tạo với cấp Asset Tag phía
  server chỉ coi tag được xem trước là điều kiện lạc quan, một transaction Save-changes cho metadata và ngữ cảnh bảo
  trì, bắt đầu và xóa bảo trì, và retire cùng reactivate, mỗi việc commit nguyên tử với sự kiện vòng đời của nó). Các
  service Application sở hữu mọi quy tắc và transaction; lỗi có kiểu được ánh xạ tập trung sang response HTTP.
- Frontend: Administration → Departments, Areas, Operations, Scan Stations và Barcode configuration, cùng Management →
  Machines là view thật với trạng thái loading, error và retry, lỗi từ chối của server hiển thị tại chỗ, và mọi thao
  tác ghi bị chặn khi offline. Các view thật được phát hành từ `src/app/real-views.ts` với API client dùng chung trong
  `src/api/`.

**Giới hạn tạm thời.** Không có.

## Phase 4 — Manual Work Order Intake and Production Release

Lát dọc nghiệp vụ đầu tiên, hiển thị trong UI là view Work Orders (GUI_DESIGN §11). Nó phải:

- tạo/tìm WorkOrder,
- lưu một WO Number bên ngoài để trống đã được xác nhận thành `NULL` trên một Work Order nội bộ (hiển thị `—`, không bao giờ lưu một giá trị giữ chỗ; có thể thay về sau qua một lần sửa có audit — PROJECT_PROFILE §7 Work Order; không sinh số tạm),
- chấp nhận due date null của WorkOrder và WorkOrderDemand là dữ liệu hợp lệ (PROJECT_PROFILE §8.2/§8.3),
- tạo/tìm PartNumber master cho PN chuẩn (chuẩn hóa viết hoa, không khoảng trắng; tạo khi dùng lần đầu),
- cung cấp năng lực vận hành tối thiểu để xem/in nhãn barcode suy ra `PF:PN:<canonical-part-number>` (PROJECT_PROFILE §10) cho một PartNumber được tạo/tìm thấy trong lúc intake — việc scan sản xuất ở Phase 5 cần barcode PN vật lý. Đây là năng lực Part Numbers duy nhất của phase này: nó không biến màn hình Management → Part Numbers đầy đủ thành thật (Phase 13). Trong UI, lúc đầu nhãn được mở bằng cách kích hoạt chính PN của dòng demand (GUI_DESIGN §11.2); từ Phase 13 control đó mở hộp thoại dùng chung `Edit Part Number`, và từ đó vẫn mở được nhãn,
- tạo/cập nhật WorkOrderDemand,
- lưu demand tách biệt với số lượng sản xuất,
- release sản xuất một cách tường minh,
- tạo QuantityFlow với route mode của nó (`FLOATING` mặc định),
- snapshot một Route độc lập **chỉ** cho release `PLANNED` — release Floating không có AssignedRoute,
- ghi thêm `RECEIVED`,
- thiết lập vị trí hiện tại,
- giữ tính transaction và idempotent,
- không tự động gộp với số lượng đang hoạt động sẵn có,
- cưỡng chế quy tắc gỡ Work Order Demand (PROJECT_PROFILE §13) trong một transaction ở lớp Application/Domain: một dòng WorkOrderDemand chỉ được xóa khi chưa có số lượng sản xuất nào được release cho nó; khi đã release, việc xóa phải bị từ chối, và việc gỡ không bao giờ được lan sang PartNumber master, QuantityFlow, PartMovement, lịch sử release, hay các bản ghi WorkOrderDemand khác của cùng PN.

Nhập tay đi trước import file. Dữ liệu seed cho phát triển có thể hỗ trợ phát triển UI và test, nhưng các fixture `RECEIVED` được seed không phải là workflow intake của sản phẩm.

**Đã có.**

- **Bảng audit.** Migration `0004_phase4_audit` thêm bảng append-only dùng chung `audit_events` (SLICE1_DATA_MODEL
  §16 và §17) với một trigger raise-on-write. Mọi lần tạo hoặc sửa Work Order, dòng demand và PartNumber master đều
  commit cùng dòng audit của nó. Release sản xuất không ghi dòng audit: Movement `RECEIVED` bất biến chính là bản ghi
  audit sản xuất.
- **API Work Orders** (`/api/work-orders`): tạo và tìm (một số để trống đã xác nhận được lưu thành `NULL` trên Work
  Order nội bộ), liệt kê và tìm kiếm (danh sách đang hoạt động giới hạn 100 dòng; tra cứu chính xác `number=` thì không
  bao giờ bị giới hạn, nên một số trùng không bao giờ bị che sau giới hạn đó), một PATCH trong một transaction cho lần
  sửa header có audit cộng các sửa dòng chỉ-phần-khác và dòng mới (PN chuẩn, master được tạo khi dùng lần đầu), và
  DELETE một dòng demand theo quy tắc gỡ (dòng còn lại cuối cùng không bao giờ gỡ được). Tra cứu Part Number trên
  `/api/part-numbers` trả tối đa 50 master mỗi lần liệt kê kèm barcode `PF:PN:` suy ra; việc phân giải chính xác
  `number=` không bao giờ bị giới hạn mất.
- **Lệnh release** `POST /api/work-orders/{work_order_id}/demands/{demand_id}/release`. Lệnh kiểm tra PN chuẩn, Area
  bắt đầu (đang hoạt động **và không terminal**, cưỡng chế ở lớp Application), Operation, một Route Template đang hoạt
  động cho `PLANNED` (từ `GET /api/route-templates` chỉ liệt kê template đang hoạt động), và một số lượng dương trong
  phạm vi số lượng còn lại của demand. Khi PN đã có số lượng đang hoạt động, lệnh đòi cờ xác nhận tường minh và cho thấy
  phân bố hiện có. Lệnh snapshot AssignedRoute chỉ cho `PLANNED`, tạo QuantityFlow, ghi thêm Movement `RECEIVED` với
  ngữ cảnh demand làm metadata, và commit nguyên tử. Idempotency theo `device_event_id` lưu một fingerprint của
  request: cùng id và cùng fingerprint thì phát lại (replay), dùng lại không khớp là xung đột, và cả hai đều không tạo
  gì.
- **Release từng phần và sửa có giới hạn.** Số lượng đã release được suy ra từ metadata của `RECEIVED` (không có bộ đếm
  lưu trữ; một index biểu thức một phần từ migration `0005_phase4_release_index` giữ cho việc đọc nhanh). Một Work Order
  hiển thị `RELEASED` chỉ khi mọi dòng hiện tại đã release đủ. Một demand có thể release làm nhiều phần, mỗi phần là
  QuantityFlow riêng, với số lượng còn lại là giới hạn cứng. Một dòng đã có số lượng release chỉ giữ lại việc sửa có
  giới hạn của PROJECT_PROFILE §13, cưỡng chế ở backend: số lượng yêu cầu (không bao giờ thấp hơn số lớn hơn giữa số
  lượng đã release và đã phân bổ), due date và Job Number; thay đổi Request Type, người yêu cầu, lý do hay ghi chú bị từ
  chối bằng 409 và không ghi gì cả, và việc gỡ vẫn bị từ chối. Mỗi dòng được sửa bị khóa và đọc lại, nên một lần sửa và
  một lần release cùng dòng được tuần tự hóa; một lần lưu có thêm dòng sẽ lấy khóa dòng Work Order, nên mỗi PN chuẩn
  xuất hiện tối đa một lần trong một Work Order.
- **Frontend.** Management → Work Orders (GUI_DESIGN §11): liệt kê và tìm kiếm, New Work Order phân giải một số đã tồn
  tại thay vì tạo trùng, Work Order Details với lưu chỉ-phần-khác, quyết định dùng chung `Unsaved changes` trước một lần
  release, hộp thoại release (GUI_DESIGN §11.4) với việc dùng lại `device_event_id` an toàn khi thử lại và xác nhận số
  lượng đang hoạt động, các dòng đã release bị giới hạn, việc sửa số bên ngoài có audit trên Work Order nội bộ, và nhãn
  `PF:PN:` Code 128 in được.

**Giới hạn tạm thời.** Không có. Việc hoàn tất một Work Order được suy ra từ phân bổ (Phase 10).

## Phase 5 — Scan Station Transfer to an Area Queue

Ví dụ minh họa: `Material -> Lathe queue`, trong đó Lathe được cấu hình là `Queue -> select Machine by scan`.

Slice này phải gồm:

- cấu hình Scan Station ổn định gắn với Lathe,
- phân giải barcode PN,
- phân giải QuantityFlow nguồn,
- phân giải Operation,
- kiểm tra route,
- kiểm tra số lượng,
- xác nhận tường minh khi mơ hồ,
- Movement `TRANSFERRED` bất biến,
- cập nhật projection vị trí hiện tại trong transaction,
- xử lý thử lại idempotent,
- khôi phục focus bàn phím,
- làm mới các lần scan gần đây và tồn tại Area.

Việc phân giải ứng viên phải tôn trọng vị trí hiện tại, route, Operation, ngữ cảnh trạm và các lần lệch hợp lệ — không bao giờ là "mọi flow đang hoạt động ngoài Area đích".

**Đã có.**

- Lưu trữ (migration `0006_phase5_transfer`): loại Movement `TRANSFERRED`, cột nullable `part_movements.station_id`
  (NULL với release của Management), và một CHECK hình dạng theo từng loại.
- Read model: `GET /api/scan-stations/{station_id}/context` (trạm không tồn tại là 404, trạm hoặc Area ngưng hoạt động
  là 409 — không bao giờ lặng lẽ thay thế); `POST /api/scan-stations/{station_id}/scans/resolve` (một barcode `PF:PN:`
  hoặc nhập tay, chuẩn hóa theo đúng một quy tắc domain, phân giải thành số lượng của PN trong Area của trạm và các
  **ứng viên chuyển** tường minh ở nơi khác, mỗi ứng viên được đánh giá `FLOATING`, `ON_ROUTE` hoặc `DEVIATION`; nhiều
  ứng viên được trả về không xếp hạng và người vận hành phải chọn đúng một); và `GET /api/areas/{area_id}/inventory`.
- Lệnh chuyển `POST /api/scan-stations/{station_id}/transfers` đưa một QuantityFlow đã chọn vào Area của trạm. Nguồn
  và đích người vận hành đã xác nhận là điều kiện tiên quyết: dòng trạm bị khóa và phải vẫn đang hoạt động và gắn đúng
  Area đã xác nhận, nên màn hình không bao giờ xác nhận Area A trong khi backend ghi Area B, và Area đích được đánh giá
  (đang hoạt động, không terminal) trên lần đọc lại có khóa. Operation được phân giải từ bước route khớp, nếu không thì
  từ Operation đang hoạt động duy nhất, nếu không thì từ lựa chọn tường minh của người vận hành. Lệnh ghi thêm Movement
  `TRANSFERRED` bất biến và cập nhật `quantity_flows.current_area_id` trong cùng transaction; idempotency trả 201 khi
  mới, 200 khi phát lại (kiểm tra trước khi đọc bất kỳ trạng thái trạm, Area hay Operation nào) và 409 khi dùng lại
  không khớp. Thứ tự khóa: flow → trạm → Area đích → Operation.
- **Lệch route** (PROJECT_PROFILE §17) trên flow `PLANNED` — một Area khác, hoặc một Operation khác tại Area dự kiến —
  bị từ chối cho đến khi request mang `confirm_route_deviation` cùng `route_deviation_reason` bắt buộc; lần lệch đã xác
  nhận được ghi ngay trên Movement `TRANSFERRED`, không bước nào được ghi là khớp, và vị trí route biết gần nhất được
  giữ nguyên. Không có Movement lệch route riêng; việc sửa route là điều chỉnh AssignedRoute có thẩm quyền của Phase 14
  (`ROUTE_ADJUSTED`, một sự kiện audit, không bao giờ là Movement).
- Frontend: Station Selector và Scan Station là view thật (GUI_DESIGN §4). Wizard chuyển (GUI_DESIGN §4.7) đòi chọn
  nguồn tường minh khi có nhiều nguồn, hiển thị phần tóm tắt xác nhận, dùng lại một `device_event_id` cho mỗi ý định khi
  thử lại, chỉ báo thành công sau khi server xác nhận, rồi làm mới tồn tại, cập nhật Last Action và khôi phục focus bàn
  phím.

**Giới hạn tạm thời.** Hiện không có. Giới hạn chỉ-chuyển-nguyên-QuantityFlow mà phase này có lúc phát hành đã được
Phase 8 gỡ bỏ (số lượng một phần), và từ Phase 6 và 7, việc chuyển số lượng đang gia công ghi thêm `AREA_COMPLETED` +
`TRANSFERRED` như một lệnh nguyên tử (PROJECT_PROFILE §8.11).

## Phase 6 — One-Shot Machine Assignment and Area Completion

- phân giải barcode Machine (chỉ là lối tắt một lần — **không có Machine session dưới bất kỳ hình thức nào**)
- kiểm tra Machine/Area
- điểm vào Machine-first (scan Machine mở hộp thoại giao một lần với Machine được chọn sẵn) và PN-first (thao tác giao trên số lượng đang xếp hàng)
- `ASSIGNED_TO_MACHINE`
- `RELEASED_FROM_MACHINE` (thao tác trả về `QUEUE` — trả số lượng chưa xong hoặc tạm dừng về hàng đợi, không bao giờ là việc đã hoàn tất)
- **`AREA_COMPLETED` cho Area có Machine** (PROJECT_PROFILE §7 Area Completion, §8.11, §12): thao tác DONE thủ công trên số lượng đã giao Machine (`DONE` và `QUEUE` vẫn là hai thao tác riêng biệt), suy ra `READY_TO_TRANSFER`, xóa `current_machine_id`, giữ Area làm vị trí; việc chuyển từ số lượng `ON_MACHINE` ghi thêm `AREA_COMPLETED` + `TRANSFERRED` trong một transaction; DONE áp theo số lượng, không bao giờ là trạng thái của PN, không bao giờ là `STOCKED`
- từ chối Machine không hoạt động
- một Machine có thể giữ số lượng của nhiều PN; một Machine hành xử đúng như nhiều Machine (không bao giờ tự giao dựa trên số lượng Machine)

**Đã có.**

- Lưu trữ (migration `0007_phase6_machine_assignment`): `quantity_flows.current_machine_id`, các tham chiếu Machine của
  Movement `source_machine_id` và `destination_machine_id`, và `part_movements.command_sequence`. Một
  `device_event_id` nhận diện một **lệnh ứng dụng**, lệnh này có thể ghi thêm nhiều Movement
  (`UNIQUE (device_event_id, command_sequence)`), nên Undo có thể đảo ngược trọn một lệnh. Các loại Movement có thêm
  `ASSIGNED_TO_MACHINE`, `RELEASED_FROM_MACHINE` và `AREA_COMPLETED`; các loại trong-Area ở yên một Area tại một Scan
  Station và mang đúng tham chiếu Machine của chúng.
- Trạng thái giữ (holding state) được suy ra từ Movement mới nhất của flow (`ASSIGNED_TO_MACHINE` → `ON_MACHINE`,
  `AREA_COMPLETED` → `READY_TO_TRANSFER`, còn lại `QUEUED`); việc dựng lại projection tái tạo Area, Machine và trạng
  thái từ lịch sử.
- Các lệnh (`app/application/machine_processing.py`): `POST /api/scan-stations/{id}/machine-assignments` (giao số lượng
  đang xếp hàng; Machine bị khóa và phải đang hoạt động, thuộc Area của trạm và không đang bảo trì; nhiều PN có thể dùng
  chung một Machine; không bao giờ tự giao), `machine-releases` (trả `QUEUE` về hàng đợi) và `area-completions` (`DONE`:
  xóa Machine, giữ Area, suy ra `READY_TO_TRANSFER`; được phép khi đang override bảo trì). Mỗi lệnh trả 201 khi mới,
  200 khi phát lại và 409 khi dùng lại không khớp, kể cả giữa các loại lệnh. Việc chuyển số lượng `ON_MACHINE` ghi thêm
  `AREA_COMPLETED` rồi `TRANSFERRED` như một lệnh nguyên tử. Thứ tự khóa: flow → trạm → Machine → Area đích →
  Operation.
- Trạng thái vận hành của Machine vẫn là suy ra (override bảo trì, nếu không thì Running khi có số lượng được giao, nếu
  không thì Idle) và `state_changed_at` chỉ đổi khi có thay đổi thật; việc retire bị từ chối khi còn số lượng được giao.
- Read model: `POST /api/scan-stations/{id}/machine-scans/resolve` (ngữ cảnh Machine-first một lần; không lưu gì, và
  lệnh kiểm tra lại Machine dưới khóa của nó), `available_actions` và `requires_selection` cho từng flow, và tồn tại được
  chia thành `queued`, `machines` (mỗi Machine đang hoạt động một thẻ, chỉ với số lượng `ON_MACHINE` của nó) và
  `finished`.
- Frontend: Scan Station hiển thị phần chia này, biến barcode Machine thành `Assign to Machine` với Machine được chọn
  sẵn, cung cấp `Assign to Machine` và `Complete Area processing on {Machine}` theo kiểu PN-first, đưa hai thao tác
  riêng biệt `DONE` và `QUEUE` trên thẻ Machine qua bước số lượng, tóm tắt và xác nhận cuối, và thông báo việc hoàn tất
  ngầm khi chuyển. Management → Machines hiển thị trạng thái và tổng số được giao do server suy ra.

**Giới hạn tạm thời.** Không có. Định danh Worker và các badge gate (Phase 13), Undo, Repair và Scrap (Phase 9) và
Stockroom (Phase 10) đến ở các phase riêng của chúng.

## Phase 7 — Direct Area Processing (Areas Without Machines)

- Area không có Machine nhận quyền gia công trực tiếp (không có hàng đợi)
- ghi Operation và Machine là null
- xác nhận khi có nhiều Operation
- **`AREA_COMPLETED` cho gia công trực tiếp**: cùng workflow DONE thủ công nhưng không có trường Machine, và hoàn tất ngầm khi số lượng đang gia công trực tiếp được chuyển sang Area khác (một lệnh nguyên tử `AREA_COMPLETED` + `TRANSFERRED`)
- chỉ có đúng hai chế độ Area: không có Machine → gia công trực tiếp; có Machine → `QUEUE_AND_ASSIGN` (PROJECT_PROFILE §12) — không có cấu hình chế độ giao theo từng Area và không tự giao khi chỉ có một Machine

**Đã có.**

- Lưu trữ (migration `0008_phase7_direct_processing`): chỉ hình dạng của `AREA_COMPLETED` được nới, để một lần hoàn tất
  có thể không mang Machine nguồn (không bao giờ mang Machine đích). Không thêm cột, bảng hay cấu hình Area nào.
- **Chế độ Area được suy ra, không bao giờ cấu hình** (`app.application.machines.area_has_machines`): có ít nhất một
  Machine đang hoạt động, chưa retire nghĩa là `QUEUE_AND_ASSIGN`, nếu không là gia công trực tiếp. Thêm Machine đầu
  tiên hoặc retire Machine cuối cùng sẽ chuyển Area và số lượng của nó giữa hai chế độ mà không đụng tới lịch sử.
- Ở Area không có Machine, một `RECEIVED` hay `TRANSFERRED` đến nơi suy ra `PROCESSING`: không hàng đợi, Machine NULL,
  Operation được ghi trên lần đến (nhiều Operation đang hoạt động thì cần lựa chọn tường minh; không có gì được tự
  chọn).
- `DONE` không Machine (`app/application/direct_processing.py`, `POST /api/scan-stations/{id}/area-completions` không
  có `machine_id`) ghi thêm đúng một `AREA_COMPLETED` không Machine và suy ra `READY_TO_TRANSFER`; nó bị từ chối, không
  ghi gì, ở Area có Machine, cũng như `DONE`, `QUEUE` và việc giao của Area có Machine bị từ chối trên số lượng đang gia
  công trực tiếp. Việc chuyển số lượng `PROCESSING` ghi thêm `AREA_COMPLETED` + `TRANSFERRED` như một lệnh nguyên tử; số
  lượng đã xong được chuyển chỉ với `TRANSFERRED`. `DONE` trực tiếp và `DONE` có Machine là hai ý định khác nhau theo
  quy tắc idempotency.
- Read model: `has_machines` trên ngữ cảnh trạm và tồn tại, `processing_state` có `PROCESSING`, và phần chia tồn tại
  `processing` / `finished`. Mỗi flow mang Operation được ghi trên Movement mới nhất của nó, bất kể Operation đó hiện
  còn hoạt động hay không, nên số lượng hiện có giữ Operation đã ghi sau khi Operation bị ngưng hoạt động.
- Frontend: Scan Station lấy chế độ Area từ lần đọc server mới nhất, hiển thị số lượng đang gia công trực tiếp là
  `In processing` (`External processing` với Operation external) và `Finished — ready to move`, cung cấp thao tác dòng
  duy nhất `DONE` và `Complete Area processing`, dùng lại wizard Area Completion không có trường Machine, và thông báo
  việc hoàn tất ngầm khi chuyển (`Recorded events: AREA_COMPLETED, then TRANSFERRED (one command)`).

**Giới hạn tạm thời.** Không có.

## Phase 8 — Quantity SPLIT and MERGED Workflows

- chuyển một phần
- giữ nguyên lineage
- bảo toàn số lượng
- lịch sử Movement `SPLIT` và `MERGED`
- bàn phím số lượng hỗ trợ các số lượng một phần hợp lệ
- **SPLIT là điều kiện tiên quyết của Repair một phần** (Phase 9): trước khi có SPLIT, Repair một phần bị từ chối rõ ràng và không ghi gì — không được tuyên bố là đã hỗ trợ

**Đã có.**

- Lưu trữ (migration `0009_phase8_split_merge`): các loại Movement `SPLIT` và `MERGED` (một Area tại một Scan Station,
  không Machine — một sự kiện lineage không bao giờ khai lại một vị trí), vòng đời Quantity Flow (`status` `ACTIVE`,
  `SPLIT` hoặc `MERGED`, với `closed_at` được đặt đúng khi flow đã đóng), và bảng cạnh append-only
  `quantity_flow_lineage` (cha → con, quan hệ, `device_event_id` của lệnh). Mỗi con một cạnh ghi một lần tách (1 → N)
  và mỗi nguồn bị tiêu thụ một cạnh ghi một lần gộp (N → 1), vì vậy không có cột đơn `quantity_flows.parent_flow_id`
  (SLICE1_DATA_MODEL §9 và §18).
- Suy ra (`app/application/projections.py`): một flow con hay kết quả gộp chưa di chuyển kế thừa trạng thái giữ,
  Machine và Operation bằng cách lần theo lineage tới Movement mang vị trí gần nhất
  (`projections.effective_latest_movement`); các flow đã bị tiêu thụ chính là các cha trong đồ thị và không bao giờ là
  tồn tại đang hoạt động.
- Số lượng một phần trên mọi lệnh: Assign, `QUEUE`, `DONE` có Machine, `DONE` trực tiếp và Transfer nhận một số lượng
  nhỏ hơn số lượng của flow. Nguồn được tách nguyên tử ngay trong cùng lệnh (các dòng `SPLIT` của nguồn, con được chọn
  và con còn lại, rồi các dòng thao tác, tất cả dưới một `device_event_id`); nguồn `PLANNED` cho mỗi con một bản sao
  snapshot riêng tại vị trí route của nguồn; thao tác chỉ áp vào con được chọn, phần còn lại giữ trạng thái của nguồn,
  và toàn bộ số lượng thì không bao giờ bị tách. `WorkOrderDemand` không bao giờ bị đụng tới. Hai lệnh trên cùng một
  nguồn được tuần tự hóa trên khóa dòng của nguồn, và lệnh thua bị từ chối ("was split into separate quantities").
- Gộp (`app/application/merges.py`, `POST /api/scan-stations/{id}/merges`): một lệnh tường minh nêu ít nhất hai flow
  `ACTIVE` của một PN trong Area của trạm, chỉ được chấp nhận khi ngữ cảnh sản xuất của chúng giống hệt nhau (trạng thái
  giữ, Machine, Operation đã ghi, và ngữ cảnh route — tất cả `FLOATING`, hoặc tất cả `PLANNED` với snapshot giống nhau
  về cấu trúc ở cùng bước); mọi khác biệt bị từ chối, không ghi gì, và không bao giờ có gì được gộp tự động. Việc phân
  giải PN báo `combine_groups`, tính bằng cùng quy tắc mà lệnh cưỡng chế, nên frontend không giữ quy tắc tương thích
  riêng nào.
- Frontend (GUI_DESIGN §4.7 và §4.8): mọi wizard nhận từ 1 đến số lượng hiện có, hiển thị phần được chọn và phần còn
  lại trước và sau khi ghi và chỉ gửi số lượng được chọn; hộp thoại thao tác PN cung cấp `Combine quantities` cho đúng
  các nhóm mà server báo, với việc chọn nguồn, xem trước kết quả và phần tóm tắt `Confirm combine`.

**Giới hạn tạm thời.** Không có.

## Phase 9 — Undo, Corrections, and Auditable Quantity Events

- Movement `REVERSED`; Undo đảo ngược trọn lệnh ứng dụng (không bao giờ một dòng tùy ý của một thao tác nhiều Movement — ví dụ một lần chuyển có `AREA_COMPLETED` + `TRANSFERRED` ngầm được đảo ngược như một), với xác nhận bằng phần tóm tắt trước khi áp dụng
- Movement gốc được giữ nguyên
- phân quyền
- lý do khi được cấu hình
- khôi phục projection
- hiển thị audit
- **Movement Repair**: `TRANSFERRED · movement_reason REPAIR` với lý do bắt buộc; Repair nguyên flow từ phase này, Repair một phần chỉ dựa trên SPLIT (Phase 8)
- **Scrap**: sự kiện chuẩn `SCRAPPED` từ workflow đếm PF:SCRAP — một thao tác có audit cho mỗi lần xác nhận; đối soát `introduced = active + stocked + scrapped`
- **Thêm số lượng**: `QUANTITY_ADJUSTED · direction INCREASE` với lý do bắt buộc, không bao giờ thay đổi số lượng yêu cầu

**Đã có.**

- Lưu trữ (migration `0010_phase9_undo_corrections`): các loại Movement `SCRAPPED`, `QUANTITY_ADJUSTED` và `REVERSED`
  với hình dạng của chúng; `part_movements.movement_reason` (chỉ `REPAIR`, chỉ trên `TRANSFERRED`); `reason` dạng văn
  bản tự do (bắt buộc với Scrap, thêm số lượng và mọi Repair, cưỡng chế bằng CHECK); `reverses_movement_id` (đặt đúng
  trên `REVERSED`, UNIQUE, nên một Movement không bao giờ bị đảo ngược hai lần, kể cả khi có tranh chấp); và các trạng
  thái flow `SCRAPPED` và `REVERSED`.
- Suy ra: mọi phép suy ra loại trừ cặp đã đảo ngược (các dòng `REVERSED` và các bản gốc chúng tham chiếu), nên sau
  một lần Undo, vị trí, Machine, Operation, vị trí route và trạng thái giữ đúng như trước lệnh bị hoàn tác, kể cả qua
  lineage.
- Undo (`app/application/undo.py`; `GET /api/scan-stations/{id}/undo-preview/{device_event_id}` và
  `POST /api/scan-stations/{id}/undos`) đảo ngược trọn lệnh đã commit bằng cách ghi thêm một dòng `REVERSED` bù cho mỗi
  dòng gốc, theo thứ tự ngược, dưới `device_event_id` riêng của Undo. Chỉ hợp lệ là thao tác có hiệu lực gần nhất của
  mọi flow liên quan, được ghi tại trạm này, không do Management ghi, chưa bị đảo ngược và bản thân không phải là một
  Undo; khôi phục lên một Machine đã retire hoặc vào một Area đã ngưng hoạt động bị từ chối. Tính hợp lệ được đánh giá
  dưới các khóa dòng, không ghi gì khi từ chối. Preview phục vụ phần tóm tắt xác nhận kèm phán quyết hợp lệ. Release sản
  xuất, nhập Stockroom (Phase 10), receipt tại Scan Station (Phase 10.5) và một lệnh đã tạo flow mà route của nó sau đó
  được điều chỉnh (Phase 14) không bao giờ là đích của Undo. Yêu cầu lý do và phân quyền đến cùng Phase 13 và 14 (xem
  ở đó).
- Repair là ý định tường minh `repair` với lý do bắt buộc trên cùng lệnh chuyển
  (`TRANSFERRED · movement_reason REPAIR`): không tạo bản ghi Repair, Request Type hay demand nào; đích phải là một Area
  mà số lượng đã từng qua; Repair một phần đi cùng lần tách trong lệnh; lệch route vẫn được xác nhận riêng. Việc phân
  giải PN đánh dấu các ứng viên đủ điều kiện là `repair_available`.
- Scrap (`app/application/quantity_events.py`, `POST /api/scan-stations/{id}/scraps`) ghi thêm một `SCRAPPED` cho mỗi
  lần xác nhận với lý do bắt buộc và đóng flow hoặc phần được tách ra; việc đếm `PF:SCRAP` diễn ra ở client và không
  ghi gì. Tổng scrap là số ròng sau khi trừ các scrap đã bị đảo ngược.
- Thêm số lượng (`POST /api/scan-stations/{id}/quantity-additions`) đưa số lượng tìm thấy vào như một flow `FLOATING`
  mới, Movement đầu tiên của nó là `QUANTITY_ADJUSTED` với direction `INCREASE` và lý do bắt buộc, chỉ bên cạnh số lượng
  của PN đang hoạt động sẵn trong Area của trạm, không bao giờ đụng tới số lượng yêu cầu.
- Frontend: Scan Station cung cấp `Add more quantity`, `Return quantity for repair` (chỉ cho nguồn `repair_available`,
  kể cả qua một lựa chọn Transfer-hay-Repair tường minh), workflow Scrap (GUI_DESIGN §4.9; `PF:SCRAP` chỉ được đếm bên
  trong nó), và Undo từ khối Last Scanned PN, khối này duyệt các lệnh đã hoàn tất của phiên từ mới nhất về cũ qua
  preview của server để tìm thao tác hợp lệ gần nhất, hiển thị phần tóm tắt có cấu trúc và câu hỏi cuối mang tông cảnh
  báo, và chuyển sang thao tác hợp lệ kế tiếp sau một lần đảo ngược đã xác nhận.

**Giới hạn tạm thời.** Không có.

## Phase 10 — Stockroom and WorkOrderAllocation

- Movement `STOCKED`
- số lượng tồn kho khả dụng
- phân bổ gợi ý theo thứ tự demand chuẩn (PROJECT_PROFILE §18):
  1. ưu tiên Work Order Demand cao nhất do quản lý đặt
  2. trong cùng ưu tiên: demand có ngày thì sớm nhất trước; demand không có ngày xếp sau mọi demand có ngày, theo received_date của WorkOrder cha (cũ nhất trước)
- các giá trị bằng nhau được phân định bằng một tie-breaker ổn định, tất định (chi tiết triển khai, không phải quy tắc nghiệp vụ)
- WorkOrderAllocation được ghi tách biệt với PartMovement
- năng lực điều chỉnh phân bổ (lần đảo ngược có audit) — việc phân quyền theo role của nó được cưỡng chế ở Phase 14, không bao giờ sớm hơn

**Đã có.**

- Lưu trữ (migration `0011_phase10_stock_allocation`): loại Movement và trạng thái flow `STOCKED`,
  `work_orders.completed_at`, và bảng append-only `work_order_allocations` (các dòng phân bổ và đảo ngược, PN chuẩn theo
  giá trị, khóa ngoại tới demand, nguồn `STOCKROOM` hoặc `MANAGEMENT`, cờ manual override, `reverses_allocation_id`
  UNIQUE nên mỗi phân bổ chỉ một lần đảo ngược kể cả khi tranh chấp, và một trigger raise-on-write). Một phân bổ không
  bao giờ tham chiếu Movement hay QuantityFlow.
- Nhập kho (`POST /api/scan-stations/{id}/stockings`) là cơ chế đến nơi riêng của lệnh chuyển tại một trạm gắn với
  Area terminal, với cùng việc chọn nguồn, khóa, đánh giá route, tách trong lệnh và `AREA_COMPLETED` ngầm. Flow đóng lại
  ở trạng thái đã sản xuất xong (`status = STOCKED`), rời mọi read model và việc dựng lại projection, và không bao giờ
  được hoàn tác; điều chỉnh phân bổ là đường hiệu chỉnh. Việc chuyển vào Area terminal vẫn bị từ chối.
- Phân bổ (`app/application/allocations.py`): số lượng tồn kho khả dụng được suy ra (`STOCKED` có hiệu lực trừ các dòng
  phân bổ đang có hiệu lực), không bao giờ lưu; `work_order_demands.allocated_quantity` và `work_orders.completed_at` là
  projection dựng lại được từ các dòng phân bổ. `GET /api/allocations/suggestion` đề xuất theo thứ tự demand chuẩn (hạng
  Hot, dòng có ngày sớm nhất trước, dòng không ngày theo ngày nhận của Work Order, id demand làm tie-breaker) tới mức
  thiếu hụt còn lại của từng dòng và không bao giờ vượt số lượng khả dụng. Xác nhận của Stockroom
  (`POST /api/allocations`) nêu `allocation_quantity` tường minh mà các dòng phải cộng đúng bằng, và số lượng tồn kho khả
  dụng phải vẫn đủ cho nó dưới khóa. Một dòng không bao giờ vượt mức thiếu hụt của nó và phân bổ không bao giờ vượt số
  lượng khả dụng; cả hai được đánh giá dưới một advisory lock theo PN cộng khóa dòng demand và Work Order. Lần đảo ngược
  (`POST /api/allocations/{id}/reversals`) là điều chỉnh có audit: một lần cho mỗi phân bổ, lý do bắt buộc, trả số lượng
  về kho và mở lại Work Order đã hoàn tất.
- Hoàn tất: một Work Order mà mọi dòng demand đã được phân bổ đủ thì hoàn tất (bởi một lần phân bổ, hoặc bởi một lần lưu
  demand hay gỡ dòng khiến mọi dòng còn lại được phân bổ đủ), rời danh sách đang hoạt động và trở thành lịch sử chỉ đọc;
  sửa, gỡ dòng và release đều bị từ chối, và tra cứu chính xác `number=` vẫn tìm thấy nó, nên số của nó không bao giờ bị
  trùng.
- Lịch sử đã hoàn tất: `GET /api/work-orders/completed` với tìm kiếm, khoảng Done (các preset có tên được phân giải theo
  ngày hiện tại của nhà máy, hoặc ngày tường minh), kết quả so với hạn, sắp xếp phía server (mặc định Done giảm dần), phân
  trang keyset và `history_total` không phụ thuộc bộ lọc. Mọi phán đoán ngày dùng lịch của nhà máy (`SITE_TIMEZONE`, một
  múi giờ IANA, mặc định `UTC`).
- Frontend: một trạm gắn với Area terminal mở `Receive into Stockroom`; sau khi server xác nhận việc nhập kho, hộp thoại
  phân bổ (GUI_DESIGN §10) hiển thị gợi ý cho đúng số lượng vừa nhập và chỉ bật `Confirm allocation` khi tổng phân bổ
  bằng số đó; `Leave in stock — allocate later` không ghi gì. Trang Completed Work Orders (GUI_DESIGN §11.5) là view thật.

**Giới hạn tạm thời.** Không có.

**Việc còn mở.** Việc số lượng đã nhập kho có được trả lại sản xuất qua một lần đảo ngược có kiểm soát hay không là một
quyết định mở của chủ sở hữu (PROJECT_PROFILE §32); cho tới khi được quyết, số lượng đã nhập kho không bao giờ quay lại
sản xuất.

## Phase 10.5 — Scan Station Receive Quantity Gap Closure (named corrective prerequisite — no later phases renumbered)

Phase hiệu chỉnh này lấp một khoảng trống workflow được phát hiện sau Phase 10: Phase 5 cố ý hoãn `Receive Quantity` tại Scan Station, và Phase 9 vẫn phân biệt nó với `Add more quantity`, nhưng không phase nào sau đó sở hữu việc triển khai nó. Nó phải được hoàn tất trước khi phần việc còn lại của Phase 11 được coi là xong và trước khi Phase 12 bắt đầu.

Phạm vi:

- triển khai workflow **`Receive Quantity`** thật tại Scan Station theo GUI_DESIGN §4.7 / PROJECT_PROFILE §14 khi một PN chuẩn không có Work Order Demand đang hoạt động, kể cả PN thấy lần đầu;
- nhập PN bằng tay và scan `PF:PN:<part-number>` được phân giải qua cùng quy tắc PN chuẩn: cắt khoảng trắng hai đầu, từ chối khoảng trắng bên trong, chuẩn hóa thành chữ hoa, và tạo PartNumber master tùy chọn ở lần dùng hợp lệ đầu tiên;
- giữ workflow ba bước đã duyệt: thiết lập → số lượng → xác nhận, với các mặc định sửa được `Request Type = MODIFY` và `Route Mode = FLOATING`, due date tùy chọn, chọn Planned Route chỉ khi `PLANNED`, ngữ cảnh Area của trạm / Operation đã phân giải, lý do/ghi chú, và hành vi Work Order nội bộ chuẩn; `Confirm receipt` là điểm ghi duy nhất;
- tạo hoặc dùng lại WorkOrder / WorkOrderDemand nội bộ đúng như PROJECT_PROFILE §14 định nghĩa. Nhiều Work Order MODIFY không số có vẻ phù hợp thì cần chọn tường minh — không bao giờ đoán lấy cái khớp đầu tiên;
- một receipt đã xác nhận tạo số lượng sản xuất trong một transaction: các bản ghi demand nghiệp vụ cần thiết, QuantityFlow, một snapshot AssignedRoute độc lập chỉ cho `PLANNED`, Movement `RECEIVED` bất biến và projection vị trí hiện tại commit nguyên tử hoặc không gì cả;
- một `RECEIVED` do scan tạo ghi định danh Scan Station và Operation đã phân giải. Trạm phải vẫn đang hoạt động và gắn với Area đã xác nhận, đang hoạt động và không terminal khi lệnh commit; ngữ cảnh trạm / Area / Operation đã cũ bị từ chối, không ghi gì;
- dùng mô hình lệnh/idempotency một lần sẵn có: một `device_event_id` cho mỗi ý định đã đóng băng, fingerprint tất định, phát lại cùng ý định, từ chối dùng lại xung đột, thử lại khi mất response dưới cùng id, và chỉ hiển thị thành công sau khi server xác nhận;
- làm mới ngữ cảnh/tồn tại của trạm và khôi phục focus scan sau khi thành công; dữ liệu vào không hợp lệ, mơ hồ, bị hủy, offline, cũ hoặc bị từ chối đều không ghi gì;
- giữ **`Add more quantity`** tách riêng: nó vẫn là hiệu chỉnh `QUANTITY_ADJUSTED · INCREASE` của Phase 9 bên cạnh số lượng đang hoạt động sẵn trong Area của trạm và không bao giờ được thay thế cho `Receive Quantity` ban đầu;
- giữ ranh giới build production: workflow thật dùng trạng thái server và không bao giờ import hay phụ thuộc vào Scan Station mock chỉ dành cho phát triển;
- ghi `received_date` theo lần SCAN, không theo lúc xác nhận (PROJECT_PROFILE §14): việc phân giải cấp thời điểm, wizard mang nó theo, server kiểm tra nó và suy ra ngày theo lịch của nhà máy;
- tuần tự hóa điều kiện vào workflow với mọi lệnh có thể làm PN có demand đang hoạt động hoặc số lượng đang hoạt động, để một receipt không bao giờ commit song song với một lệnh như thế;
- chỉ nhận một PN ĐÃ có số lượng đang hoạt động sau xác nhận tường minh mà PROJECT_PROFILE §14 yêu cầu, và ghi nó thành một QuantityFlow RIÊNG: một receipt không bao giờ nhập vào, gộp, thay đổi hay viết lại số lượng hiện có, việc phân giải mang theo phân bố hiện có để người vận hành xác nhận dựa trên đó, backend đánh giá lại trạng thái đó một cách quyết định lúc ghi (một receipt mà PN của nó có thêm số lượng đang hoạt động sau khi wizard mở bị từ chối, không ghi gì, cho đến khi được xác nhận), và các số lượng thuộc về nhau được gộp lại sau đó bằng workflow `Combine quantities` sẵn có.

Không thuộc phase hiệu chỉnh này: định danh Worker, Worker Session và các badge gate (Phase 13); xác thực và cưỡng chế
role (Phase 14 — một receipt cần một thiết bị trạm đã enroll và permission Receive Quantity của role áp dụng tại Scan
Station); quản lý Planned Route và metadata PartNumber (Phase 13); phân bổ Stockroom (Phase 10).

**Đã có.**

- Không có migration: hình dạng `RECEIVED` vốn đã cho phép một Scan Station và một lý do.
- Điều kiện vào (`app/application/intake.py`): một Work Order Demand đang hoạt động khi
  `requested_quantity > allocated_quantity` (PROJECT_PROFILE §14). Việc phân giải PN báo `has_active_demand`,
  `intake_available` (không có demand đang hoạt động và Area của trạm có thể bắt đầu sản xuất — Area terminal thì không
  bao giờ), `part_number_known`, `internal_work_orders` và `active_quantity` hiện có của PN.
- Lệnh `POST /api/scan-stations/{station_id}/receipts` tạo trong một transaction PartNumber master ở lần dùng đầu, Work
  Order nội bộ (không số, ngày nhận lấy từ lần scan), dòng demand, snapshot `PLANNED` (bước đầu tiên phải bắt đầu ở
  Area của trạm), QuantityFlow và Movement `RECEIVED`. Việc dùng lại Work Order nội bộ không bao giờ đoán: ứng viên là
  các Work Order chưa hoàn tất, không số, đã có một dòng `MODIFY` cho PN; đúng một thì được dùng lại bằng cách tăng dòng
  đó (kèm dòng audit), nhiều thì bị từ chối với `selection_required` và danh sách ứng viên, không có thì tạo Work Order
  mới, và receipt `NEW` không bao giờ dùng lại.
- Không bao giờ nhập vào: khi PN đã có số lượng đang hoạt động, lệnh đọc lại phân bố dưới advisory lock của PN và từ
  chối (409, không ghi gì) cho đến khi người vận hành xác nhận; cờ xác nhận nằm ngoài fingerprint của request, nên xác
  nhận là tiếp tục cùng lần gửi dưới cùng `device_event_id`.
- Ngày nhận: server kiểm tra thời điểm scan (có múi giờ, không ở tương lai, không cũ hơn mười hai giờ —
  `intake.MAX_SCAN_AGE`) và suy ra ngày theo `SITE_TIMEZONE`. Cửa sổ thời gian chỉ chi phối việc nhận số lượng mới: một
  receipt đã commit vẫn được phát lại kể cả sau mười hai giờ.
- Một advisory lock PN dùng chung (`part_numbers.acquire_part_number_lock`) tuần tự hóa mọi lệnh có thể làm một PN có
  số lượng đang hoạt động (release, receipt, một Undo mở lại flow) hoặc có demand đang hoạt động (một lần lưu Work Order
  thêm hoặc tăng một dòng, một lần đảo ngược phân bổ, receipt); nhiều PN được khóa theo thứ tự tăng dần. Thứ tự khóa
  toàn cục: advisory PN → demand tăng dần → Work Order → flow → trạm → Machine → Area → Operation.
- Một receipt không đảo ngược được từ trạm: việc đảo ngược cấp Movement không bao giờ viết lại demand nghiệp vụ mà
  receipt đã tạo hay đã tăng.
- Frontend: `Receive Quantity` mở trực tiếp khi PN không có số lượng đang hoạt động ở đâu cả, và qua lựa chọn tường minh
  `Receive new quantity` trong trường hợp còn lại; khi có số lượng đang hoạt động, `Confirm receipt` chờ một lần ghi nhận
  tường minh.

**Giới hạn tạm thời.** Không có.

## Phase 11 — Read Models and Monitoring Views

- Production Board
- Area Board, gồm nội dung Manager Summary trong phần tổng quan All Areas của nó (không có view Manager Summary riêng)
- Tracking
- Management → Machines: phân rã số lượng **Assigned now** theo PN, thay cho cách trình bày tạm chỉ-có-tổng của Phase 6
- giám sát thời lượng dự kiến: hành vi thời lượng dự kiến mang tính tư vấn chuẩn của PROJECT_PROFILE §17 "Effective expected duration of a position" (giá trị snapshot của Assigned Route Step hiện tại thắng, giá trị mặc định của Operation là phương án dự phòng trực tiếp, không đánh giá gì khi không có cả hai), thay cho mốc tạm cố định `>= 3 days` để báo nằm lâu trước đây
- projection suy ra từ Movement
- trạng thái feed cũ và dữ liệu dài

Phase 11 chỉ là công việc read model / giám sát. Nó không được ôm các thao tác ghi sản xuất của Scan Station, ghi Priority, quản lý master data, Worker session hay xác thực.

**Đã có.** Mọi con số giám sát được suy ra từ projection vị trí hiện tại và lịch sử Movement bất biến qua các phép suy
ra dùng chung (`projections.effective_positions`), nên không có hai bề mặt nào bất đồng về một số lượng.

- **Production Board** (`app/application/production_board.py`, `GET /api/production-board`). Một read model trên toàn
  Department (PROJECT_PROFILE §21, GUI_DESIGN §5): mọi PN có số lượng đang hoạt động trong Department, hoặc có số lượng
  đã nhập kho và một demand đang mở, với phân bố theo Area, Machine và hoạt động external, trạng thái giữ được suy ra,
  thời điểm vào cố định của từng vị trí (cũ nhất của một nhóm; số lượng đã gộp được tính ngày từ lần vào cũ nhất của cả
  lần gộp và chỉ nêu Machine khi mọi nhánh thống nhất), số lượng đã nhập kho và đã scrap, và ngữ cảnh demand đang mở.
  Các dòng theo đúng thứ tự demand chuẩn của demand đang mở đầu tiên của chúng; một Work Order đã hoàn tất không bao giờ
  cung cấp ngữ cảnh cho dòng, và một dòng không có ngữ cảnh giữ due date null và lấy ngày nhận từ flow đang hoạt động cũ
  nhất của nó. Department là Department đang hoạt động duy nhất hoặc Department được nêu bằng `?department=<id>` trên
  địa chỉ của màn hình (không có thì 404, nhiều thì 409). View làm mới mỗi 15 giây với một request đang chạy và hiển thị
  `Feed stale — reconnecting` bất cứ khi nào không có board đầy đủ trên màn hình.
- **Area Board** (`app/application/area_board.py`, `GET /api/area-board`). Nó trả các Area đang hoạt động của
  Department với cùng mô hình giám sát Area mà Scan Station đọc (`scan_station.area_inventory`, schema dùng chung trong
  `app/api/area_inventory.py`), cộng các Operation và các dòng đã nhập kho của Stockroom. Mô hình tách nguồn gốc của một
  số lượng (demand mà nó được release cho, dùng trong các hộp thoại của Scan Station và trong audit) khỏi ngữ cảnh giám
  sát của nó (các demand đang mở của PN, nơi cho ra mọi hạng Hot, due date và Job Number của một dòng giám sát). Phần
  tổng quan All Areas lấy PN làm trung tâm; phần chi tiết theo Area hiển thị mỗi số lượng thao tác riêng được một dòng;
  việc sắp xếp làm ở client.
- **PN Tracking** (`app/application/tracking.py`; `GET /api/tracking` cùng các route chi tiết và phân trang). Danh sách
  lấy PN làm trung tâm với tìm kiếm và bộ lọc phía server theo thứ tự demand chuẩn, và phần chi tiết chỉ đọc: master,
  demand đang mở, số lượng hiện tại, tồn kho và phân bổ, đối soát `introduced = active + stocked + scrapped`, mọi
  Quantity Flow với lineage và snapshot `PLANNED` hoặc vết `FLOATING` của nó, và các lịch sử Scrap, phân bổ và Movement,
  mỗi cái được phân trang trên một keyset được kiểm tra theo một thứ tự cố định (migration
  `0012_phase11_tracking_index` phục vụ thứ tự Movement). Trạng thái suy ra `OPEN`, bên cạnh Active, Stocked và
  Completed, là phần bổ sung do triển khai đòi hỏi, được ghi trong GUI_DESIGN §7.1.
- **Machines → Assigned now** (GUI_DESIGN §12.1): `assigned_lines` theo PN bên cạnh `assigned_quantity`, là tổng của
  chúng.
- **Giám sát thời lượng dự kiến** (PROJECT_PROFILE §17): với flow `PLANNED` đang đúng route, thời lượng snapshot của
  Assigned Route Step hiện tại; nếu không, giá trị mặc định hiện hành của Operation đã ghi; không có cả hai thì không
  đánh giá gì. Việc đúng route được quyết định bởi lần đến đã thiết lập vị trí hiện tại, nên một lần đến có lệch đã xác
  nhận, kể cả một lần trả về Repair vào Area của một bước trước, làm số lượng ở trạng thái lệch route. Server chỉ gửi
  thời điểm `expected_by`; client so nó với đồng hồ dùng chung và hiển thị cảnh báo bằng chữ
  `Exceeds the expected duration`. Phán đoán này chỉ mang tính tư vấn và không bao giờ chặn một lệnh.

**Giới hạn tạm thời.** Không có. Các view giám sát dùng polling; không có push (xem `Deferred`).

## Phase 12 — Priority Management

- xếp hạng WorkOrderDemand Hot
- thêm/tìm/scan (thêm vào cuối được áp dụng trực tiếp)
- sắp xếp lại (kéo thả, Move Up / Move Down)
- xác nhận trước khi gỡ và trước mọi thay đổi thứ tự của các mục hiện có — kể cả Undo và Redo (PROJECT_PROFILE §21 Priority Management)
- áp dụng có audit sau khi xác nhận tường minh
- Undo/Redo
- tự gỡ một mục khi demand của nó không còn hoạt động; gỡ một dòng demand Hot có xác nhận bằng cách gõ (các quyết định của chủ sở hữu ngày 2026-10-04)

**Đã có.**

- Lưu trữ (migration `0013_phase12_priority`): một bước kiểm tra trước từ chối, thay vì đánh số lại, các hạng hiện có
  không đúng là `1..N`; sau đó là CHECK hạng dương, UNIQUE trên `work_order_demands.priority_rank` và một index biểu
  thức trên metadata audit của Hot list cho việc tra cứu idempotency. Hot list là tập các dòng demand có hạng
  (`priority_rank IS NOT NULL`; không có cờ và không có bảng). Các hạng của nó luôn đúng là `1..N`, duy nhất và liền
  nhau.
- Lệnh `POST /api/hot-list/changes` (`app/application/hot_list.py`, quy tắc trong `app/domain/hot_list.py`) áp một thay
  đổi (`ADD` vào cuối, `REMOVE`, `MOVE_UP`, `MOVE_DOWN`, `DRAG`, `UNDO`, `REDO`) dựa trên `expected_order` mà quản lý đã
  xác nhận, đánh số lại trong một transaction dưới advisory lock của Hot. Thứ tự cũ là 409 `hot_list_changed` kèm các
  mục hiện tại. Một demand được thêm phải đủ điều kiện: chưa có hạng, Work Order của nó chưa hoàn tất, và đang hoạt động
  (`requested_quantity > allocated_quantity`). Lệnh idempotent theo `device_event_id`; các dòng audit của nó là bản ghi
  idempotency. Mỗi thay đổi hạng ghi thêm một dòng audit `UPDATED` `WorkOrderDemand` với snapshot định danh.
- Đọc: `GET /api/hot-list` và `GET /api/hot-list/candidates` (tìm kiếm giới hạn 50 kèm cờ `truncated`, hoặc mọi demand
  đủ điều kiện của một barcode `PF:PN:` được scan). Mọi lần đọc và lệnh phân giải đúng một Department đang hoạt động.
- Tự gỡ: một lần phân bổ hoặc một lần lưu Work Order làm giảm số lượng khiến một demand có hạng không còn hoạt động sẽ
  gỡ nó khỏi Hot list trong cùng transaction, khép chỗ trống và audit mọi hạng bị dịch (`AUTO_REMOVE` với một khối
  nguyên nhân). Một lần đảo ngược hay tăng số lượng không thêm lại gì. Xóa một dòng demand có hạng cần
  `?confirm_hot_removal=true` (nếu không là 409 kèm mục đó, không ghi gì); Management → Work Orders đòi xác nhận bằng
  cách gõ PN có nêu hạng của nó (audit là `LINE_DELETE`). Khối xây dựng dùng chung là `app/application/hot_ranks.py`.
- Thứ tự khóa toàn cục: advisory lock PN (tăng dần) → advisory lock Hot → một dòng Scan Station → các dòng demand trong
  một lượt tăng dần → các dòng Work Order tăng dần.
- Frontend: Management → Priority (`views/priority/PriorityView.tsx`) chỉ hiển thị danh sách của server, xác nhận mọi
  thay đổi thứ tự với snapshot trước và sau, có kéo thả, Move Up và Move Down, hộp thoại Add một trường (tìm kiếm, hoặc
  scan `PF:PN:`; một barcode có nhiều demand đủ điều kiện mở danh sách để chọn), và Undo/Redo không giới hạn trong phiên
  (PROJECT_PROFILE §21 mục 9) như một ngăn xếp phía client được đặt lại trên danh sách hiện tại. Thao tác ghi bị chặn khi
  mất kết nối, đang chạy hoặc có kết quả không rõ; kết quả không rõ cho phép thử lại đúng request đó.
- Báo cáo "Hot and priority demand" của PROJECT_PROFILE §27 được đáp ứng bởi view Priority, hạng Hot trên Production
  Board và bộ lọc Hot only của PN Tracking.

**Giới hạn tạm thời.**

- Một không gian hạng cho đúng một Department đang hoạt động: mọi lần đọc và lệnh đều từ chối khi không có hoặc có nhiều
  Department đang hoạt động. Đây là một quyết định đã ghi nhận của chủ sở hữu, không phải một sự hoãn: demand không mang
  Department, và không gian hạng theo Department sẽ cần Department trên demand hoặc một phép suy ra theo Area cộng một
  migration đánh số lại, nên không đảo ngược được nếu không sửa dữ liệu. Triển khai được hỗ trợ có một Department đang
  hoạt động duy nhất (PROJECT_PROFILE §22).
- Một mục Hot có demand đã không còn hoạt động chỉ có thể tồn tại trên database đã chạy code Phase 12 sớm nhất trước khi
  có tự gỡ. Lệnh Hot vẫn chấp nhận `REMOVE` và `MOVE` cho mục như vậy, view Priority đánh dấu nó, và Undo không thể thêm
  nó lại. Kiểm tra đối soát (i) liệt kê các mục như vậy (DEPLOYMENT §5); gỡ chúng trong Management → Priority.
- Như một ngoại lệ đã ghi nhận, hạng của một demand trên Work Order đã hoàn tất vẫn có thể thay đổi (kèm dòng audit) qua
  việc tự gỡ bên trong lần phân bổ làm hoàn tất và qua việc quản lý gỡ hoặc di chuyển một mục còn sót như vậy.
- Khóa Hot và các khóa PN là advisory key 64 bit. Va chạm hash giữa chúng về lý thuyết có thể đảo thứ tự khóa;
  PostgreSQL khi đó hủy một transaction (`40P01`) mà không commit gì (xác suất khoảng N/2^64).

**Không xây (không có yêu cầu chuẩn).** Lịch sử Undo/Redo phía server (lịch sử phiên chỉ ở client theo PROJECT_PROFILE
§21 mục 9); push hay polling Hot list (một view đang mở biết về việc tự gỡ qua lần từ chối do thứ tự cũ và lần làm mới
khi kích hoạt); cờ `is_hot`; hợp nhất các cài đặt khác nhau của thứ tự demand chuẩn. Nhập priority khi tạo Work Order
được liệt kê trong `Deferred`.

## Phase 13 — Full Administration and Production Identity Configuration

Hoàn thiện view Administration vượt ra ngoài phần cài đặt môi trường tối thiểu của Phase 3.5 và biến các bề mặt master data/cấu hình sản xuất còn lại thành thật.

- Worker — hồ sơ với id ổn định, tên, barcode badge (badge nhân viên sẵn có của công ty, khớp chính xác sau khi chuẩn hóa — cắt khoảng trắng, viết hoa; không có barcode `PF:WORKER:`), avatar, và trạng thái hoạt động; Worker vẫn tách biệt với User
- Nhận diện Worker theo Area — lưu và quản lý các chế độ chuẩn `disabled`, `fixed Worker` và `scanned Worker Session`, gồm cả Worker cố định được cấu hình khi áp dụng
- Tích hợp runtime Worker Session — phân giải/chuyển badge, phiên trượt theo thời gian không hoạt động trong phạm vi Scan Station, modal chặn khi hết hạn, giữ bản nháp của hộp thoại sản xuất đang mở, và chỉ làm mới timeout trên các tương tác sản xuất hợp lệ
- định danh audit sản xuất — thêm/dùng các tham chiếu Worker/ScanSession chuẩn mà Movement của Scan Station cần và việc gán Worker mà phân bổ tại trạm cần; các bản ghi lịch sử hiện có vẫn hợp lệ với định danh null và không bao giờ được điền bù bằng phỏng đoán
- Route Template — quản lý thật qua **Management → Planned Routes** (GUI_DESIGN §13), dựa trên permission cho các vai trò sản xuất có thẩm quyền; Administration không giữ màn hình Route Templates trùng lặp
- Part Number — quản lý thật PartNumber master tùy chọn qua **Management → Part Numbers** (GUI_DESIGN §14), gồm sửa metadata, quản lý ảnh, toàn bộ bề mặt nhãn barcode, và xóa cứng chỉ bản ghi metadata; việc xóa không bao giờ lan sang WorkOrderDemand, QuantityFlow, PartMovement, Allocation hay lịch sử, và thuộc về đây chứ không phải Phase 16
- khi quản lý Part Numbers trở thành thật, nhắm lại control PN của dòng demand trong Work Orders sang hộp thoại dùng chung `Edit Part Number`; nhãn barcode vẫn mở được từ bên trong nó và control dùng kiểu thao tác sửa đã duyệt
- User và role / quản lý phân quyền — cấu hình được tạo ở đây; cưỡng chế là Phase 14
- Chính sách Worker Session — một timeout trượt theo thời gian không hoạt động mặc định với override theo Area, cộng ba tùy chọn xác nhận bằng badge độc lập cho `DONE`, `QUEUE` và `UNDO` (mặc định BẬT) để chọn hình thức của cổng xác nhận cuối luôn có mặt
- Chính sách lý do Undo — cấu hình kích hoạt `reason when configured` của PROJECT_PROFILE §16; khi bật, chính lệnh Undo ở backend, không chỉ UI, đòi lý do
- correction permission — cấu hình ở đây và cưỡng chế ở Phase 14
- Thiết lập hiển thị theo Department — thời gian xoay trang của Production Board theo từng Department (số giây cho mỗi dòng hiển thị và thời gian dừng tối thiểu mỗi trang)
- Thiết lập chính sách Due Soon — thay giá trị mặc định tạm ở frontend bằng chính sách cấu hình được dùng cho phần trình bày mức khẩn của due date, theo GUI_DESIGN §3.12 thay vì tạo một mô hình chính sách thứ hai
- lưu theme theo User và theo Scan Station với thứ tự ưu tiên User → Station → mặc định Dark — tầng Scan Station được giao ở đây và tùy chọn của User được lưu ở đây; nó được áp dụng và lưu bởi Phase 14
- thiết lập chính sách lưu giữ/archival — thời hạn lưu giữ lịch sử Movement (archival và purge, cùng ngưỡng kích thước dữ liệu với cơ chế kích hoạt của nó, được thực thi ở Phase 16)

Phase 13 không để lại trong đồ thị module production bản preview chính sách Worker session chỉ-cho-phát-triển hay định
danh sản xuất giả nào, và không còn mock view chỉ-cho-phát-triển nào được đăng ký.

**Đã có.** Migration `0014` đến `0028`; mọi mục Administration và sub view Management đều là thật.

- **Worker** (`app/application/workers.py`; `/api/workers` cùng các route avatar). Một Worker có id định danh, tên không
  duy nhất, một badge, avatar tùy chọn và cờ hoạt động; Worker được ngưng hoạt động, không bao giờ bị xóa. Quy tắc badge
  (quyết định của chủ sở hữu): badge được cắt khoảng trắng và đổi sang CHỮ HOA cả khi lưu lẫn khi khớp một lần scan, nên
  việc khớp không phân biệt hoa thường; giá trị rỗng hoặc bắt đầu bằng `PF:` bị từ chối; tối đa 128 ký tự; duy nhất
  trên mọi Worker, kể cả Worker ngưng hoạt động (PROJECT_PROFILE §10). CHECK ở database chỉ so ASCII dưới collation
  `"C"`; việc viết hoa Unicode đầy đủ do quy tắc domain sở hữu. Quy tắc ảnh (quyết định của chủ sở hữu): ảnh được lưu
  trong PostgreSQL, PNG, JPEG hoặc WebP tới 2 MiB, được kiểm tra ở một nơi (`app/application/images.py`), tải lên dưới
  dạng thân request thô, phục vụ kèm `ETag`, và được trình duyệt thu nhỏ về cạnh dài nhất 1024 px trước khi tải lên.
  Các trình sửa chỉ gửi các trường đã đổi.
- **Audit cấu hình.** Mọi thao tác ghi cấu hình có hiệu lực — Department, Area, Operation, Scan Station, định dạng Asset
  Tag, Machine, Worker, chính sách ứng dụng, Part Number, Planned Route, User và Role — ghi thêm đúng một dòng
  `audit_events` `CREATED`, `UPDATED` hoặc `DELETED` trong cùng transaction, sau khi khóa dòng nó sửa để `before_data` là
  bản đã commit liền trước. Một lần retire hoặc reactivate Machine chỉ có dòng audit cho phần thay đổi cấu hình mà nó
  mang theo, liên kết qua `metadata.machine_lifecycle_event_id`. Thao tác không đổi gì, bị từ chối và tranh chấp thua
  không ghi gì (tranh chấp thua trả 409, không bao giờ 500); không điền bù. Một thao tác ghi cấu hình con khóa Department
  hoặc Area cha `FOR SHARE` và đọc lại cờ hoạt động của cha dưới khóa, nên một lần ngưng hoạt động cha đồng thời có một
  kết quả tuần tự duy nhất. Bốn CHECK PN chuẩn so sánh dưới collation `"C"`, nên database từ chối chữ thường ASCII,
  khoảng trắng ASCII và chuỗi rỗng không phụ thuộc hệ điều hành, còn quy tắc Unicode đầy đủ vẫn nằm trong
  `normalize_part_number`.
- **Nhận diện Worker theo Area.** `areas.worker_identification_mode` (`DISABLED`, `FIXED`, `SCANNED`; mặc định
  `DISABLED`) với `areas.fixed_worker_id`, và các cột nullable `part_movements.worker_id` và
  `work_order_allocations.allocated_by_worker_id`. `app/application/station_identity.py` áp chế độ ở một nơi duy nhất:
  mọi lệnh Scan Station phân giải định danh sau các khóa của nó và sau đường tắt idempotency và đóng dấu cùng một Worker
  trên mọi dòng; các dòng Management và lịch sử trước đó giữ NULL, không bao giờ phỏng đoán. Một Fixed Worker không thể
  bị ngưng hoạt động. Lịch sử hiển thị tên hiện tại của Worker.
- **Worker Session** (`app/application/worker_sessions.py`). Singleton `application_policy` giữ timeout trượt theo thời
  gian không hoạt động mặc định (quyết định của chủ sở hữu: 15 phút, cấu hình được từ 1 đến 720 phút) với override theo
  Area. `worker_sessions` cho phép một phiên mở cho mỗi trạm; `part_movements.scan_session_id` nối mỗi Movement với phiên
  của nó qua một khóa ngoại ghép. Một lần scan badge sẽ đăng nhập, chuyển hoặc làm mới; mọi lệnh hợp lệ ở chế độ Scanned
  session và mọi lần phân giải PN hay Machine thành công đẩy timeout về sau, còn một lần từ chối, một lần scan không hợp
  lệ hay một lần phát lại thì không bao giờ. Đổi chế độ khỏi `SCANNED`, gắn lại hoặc ngưng hoạt động trạm, và ngưng hoạt
  động Worker sẽ đóng các phiên bị ảnh hưởng. Scan Station hiển thị pill phiên với bộ đếm ngược và modal chặn
  `Worker sign-in required` / `Worker session expired` nằm trên mọi hộp thoại đang mở, với bản nháp được giữ nguyên.
- **Cổng xác nhận bằng badge.** Ba tùy chọn chính sách (mặc định bật) cho `DONE`, `QUEUE` và Undo. Server quyết định cổng
  cuối (`final_gates` trong ngữ cảnh trạm): `BADGE` ở Area chế độ Scanned session có tùy chọn đang bật, nếu không là
  `QUESTION`. Các lệnh nhận `confirming_badge` và từ chối bằng 409 `badge_confirmation_not_expected`, 409
  `badge_confirmation_required` hoặc 422 `badge_not_recognized`, không ghi gì; một badge được chấp nhận đăng nhập Worker
  của nó và được ghi trên mọi dòng. Badge không tham gia fingerprint nào, nên một lệnh đã commit được phát lại bất kể
  badge hay chính sách lúc thử lại là gì. Mỗi tùy chọn được lưu riêng, nên các quản trị viên đồng thời không bao giờ ghi
  đè các tùy chọn khác của nhau.
- **Chính sách lý do Undo** (`/api/policies/correction-permissions`): một công tắc toàn cục, mặc định tắt. Khi bật, một
  Undo không có lý do là 409 `undo_reason_required`, không ghi gì; Scan Station hiển thị trường `Reason` bắt buộc và
  nhắc lại nó ở cổng cuối. Mọi lý do dạng văn bản tự do chứa U+0000 bị từ chối bằng 422.
- **Quản lý Part Number** (`app/application/part_numbers.py`): tên, revision hiện tại và ERP id tùy chọn (cắt khoảng
  trắng, rỗng thì lưu NULL, không duy nhất) và ảnh PN theo quy tắc ảnh; `POST` chỉ-tạo, `PATCH`, `DELETE` cứng (chỉ
  metadata — nó không bao giờ đọc hay ghi demand, flow, Movement, phân bổ hay lịch sử), các route ảnh và danh sách quản
  lý có giới hạn `GET /api/part-numbers/page`. PN không bao giờ đi trong một đoạn của path. Thao tác tạo lấy advisory
  lock PN, nên nó tuần tự với việc tạo khi dùng lần đầu; sửa, ảnh và xóa chỉ khóa dòng của chính nó, nên sản xuất không
  bao giờ phải chờ chúng. Hộp thoại dùng chung `Edit Part Number` cũng được mở bởi control PN của dòng demand trong Work
  Orders, và từ đó vẫn mở được nhãn barcode.
- **Quản lý Planned Route** (`app/application/route_templates.py`): tạo, thay thế toàn bộ, archive (chỉ template đã
  từng dùng, không có bỏ archive), xóa (chỉ template chưa từng dùng) và xem mức sử dụng; một Machine ưu tiên mang tính
  tư vấn cho mỗi bước, được sao vào snapshot mà không có khóa ngoại. Mỗi bước cần một Area đang hoạt động, một
  Operation, và một Machine ưu tiên chưa retire thuộc Area đó; bước 1 không bao giờ ở Area terminal; mọi bước được kiểm
  tra ở mọi lần lưu. Việc sửa không bao giờ thay đổi một Assigned Route. Release và receipt khóa mọi Area của snapshot
  trong một lượt tăng dần, rồi các Operation của nó, trước lệnh INSERT snapshot.
- **Thiết lập hiển thị theo Department và chính sách Due Soon.** Thời gian xoay trang theo Department (số giây mỗi dòng,
  mặc định 3, 1–60; thời gian dừng tối thiểu mỗi trang, mặc định 6, 1–300) và chính sách Due Soon
  (`/api/policies/due-soon`: số ngày tối thiểu 2, phần trăm lead-time 15, số ngày tối đa 7 theo mặc định, tối thiểu ≤ tối
  đa). Mọi bộ đếm ngược hạn dùng chính sách của server; khi không đọc được chính sách, Scan Station giữ mọi thao tác sản
  xuất và chỉ tạm bỏ tông `N days left` phía sau một thông báo tường minh.
- **Tầng theme Scan Station** (`PUT /api/scan-stations/{id}/theme-preference`): tùy chọn Dark/Light riêng của trạm,
  không audit.
- **Thời hạn lưu giữ** (`/api/policies/data-retention`): số tháng nguyên từ 12 đến 1200, hoặc không có (giá trị ban
  đầu). Nó chỉ là cấu hình được lưu: không có code nào ngoài mục chính sách đọc nó (một test quét mã nguồn tĩnh cưỡng
  chế điều này), và không có gì được lưu trữ, xóa, lên lịch hay xem trước. Administration → History archival & purge nói
  rõ các lần chạy chưa có; Machine assignment là một phát biểu chỉ đọc về hai chế độ Area; Scan behavior được hiển thị là
  chưa có.
- **User, role và permission dưới dạng cấu hình.** `roles`, `role_permissions` (đúng bộ từ vựng 35 key, mỗi key cho một
  năng lực của PROJECT_PROFILE §20, định nghĩa là `Permission` trong `app/domain/enums.py`; key không bao giờ bị đổi
  tên) và `users` (tên đăng nhập chuẩn viết thường, tên hiển thị, đúng một role, avatar tùy chọn, tùy chọn theme được
  lưu, cờ hoạt động; không có liên kết tới `workers`). Các role được seed giữ đúng các quyền ban đầu mà PROJECT_PROFILE
  §20 nêu (Administrator 17, Manager 10, Operator 10), không suy diễn gì thêm. Quyết định của chủ sở hữu: role có tên và
  sửa được, permission key là thẩm quyền duy nhất, không role nào là superuser ngầm định, và không hành vi nào gắn với tên
  role. Không có xóa role hay user và không có ngưng hoạt động role.

**Giới hạn tạm thời.**

- Pill ở header Scan Station và dòng `Worker` trong phần tóm tắt hiển thị ngữ cảnh trạm như lần đọc gần nhất (tải trang,
  mỗi lần scan được phân giải, mỗi thao tác dòng được mở, mỗi lệnh hay lần từ chối). Nếu Administration đổi Area trong
  khi một hộp thoại đang mở, phần tóm tắt có thể nêu cấu hình trước đó, nhưng server ghi định danh mà nó đánh giá lúc
  xác nhận, và lần phân giải hay lệnh kế tiếp làm mới trạm. Không thêm polling.
- Một phiên đã hết hạn vẫn mở trong bảng cho tới lần đăng nhập hay lần đóng do cấu hình kế tiếp tại trạm của nó; bên đọc
  coi `expires_at <= now` là đã kết thúc, và một báo cáo phải nối Movement với phiên qua `scan_session_id`, không bao giờ
  theo khoảng thời gian. Một Worker có thể đăng nhập ở nhiều trạm. Không có control đăng xuất Worker (quyết định của chủ
  sở hữu); một phiên kết thúc khi chuyển Worker, khi hết hạn hoặc khi có thay đổi cấu hình làm nó mất hiệu lực.
- Hành vi chủ sở hữu thấy được và có thể bác: các thao tác ghi quản trị Machine khóa trước, nên một lần bắt đầu hay xóa
  bảo trì đồng thời, một lần sửa thua một lần retire, và một lần reactivate đồng thời trả 409 với thông điệp hiện có.
- Một lần xóa Part Number commit trong lúc một lần lưu Work Order đang chạy có thể để demand mới không có chi tiết đã
  lưu; sản xuất không bao giờ phụ thuộc master, và lần tạo kế tiếp sẽ khôi phục chi tiết. Một lần tạo từ Management
  tranh chấp với việc tạo-khi-dùng-lần-đầu của sản xuất được tuần tự hóa trên advisory lock PN.
- Một Planned Route được lưu ngay trước khi một Operation bị ngưng hoạt động hay một Machine bị retire giữ tham chiếu đã
  cũ, hiển thị `(unavailable)` và được thay ở lần lưu kế tiếp; một lần release hay nhận hàng (receipt) và một lần sửa cùng template, hoặc một lần lưu template và một lần ngưng hoạt
  động Area hay retire Machine, có thể chờ nhau một chút; PN Tracking đặt nhãn Planned Route nguồn của một flow bằng tên hiện tại của template trong khi hiển thị
  các bước snapshot tại thời điểm release. Một template cũ có bước 1 ở Area terminal vẫn được liệt kê và bị từ chối khi
  release cho tới khi được sửa.
- `POST /api/route-templates` và `POST /api/areas` không an toàn khi thử lại: sau một kết quả không rõ, client tải lại và
  đề nghị người dùng kiểm tra; một route trùng là một template chưa từng dùng và có thể xóa. Trường hợp Area được liệt
  kê trong `Deferred`.
- Các PN chứa khoảng 27 code point nhạy với phiên bản Unicode được lưu theo dạng chuẩn của cơ sở dữ liệu Unicode của
  Python ở backend, được ghim ở phiên bản `15.0.0` bởi `test_unicode_database_version_is_pinned`; một trình duyệt mới
  hơn có thể chuẩn hóa trước các code point đó khác đi, nên hai dạng có thể được lưu thành hai PN khác nhau. Việc sửa
  các đường nhập và các kiểm tra khi nâng cấp thuộc Phase 16.
- Thông điệp từ chối khi downgrade migration xác nhận bằng badge nêu việc xác nhận bằng badge rộng hơn cần thiết; các
  migration đã commit không được sửa.

**Việc còn mở.**

- Dạng chuẩn khi nhập PN và các kiểm tra định danh khi nâng cấp nền tảng (PN, badge Worker và tiền tố Asset Tag) —
  Phase 16.
- Hậu tố ` · no Part Number master record — history unaffected` của Tracking có thể đổi thành
  ` · no saved Part Number details — history unaffected` trong một lượt rà soát câu chữ của chủ sở hữu (tùy chọn).
- Không xây, không có yêu cầu chuẩn: tra cứu hay kiểm tra ERP id qua ERP; tên hay revision của PN trong phân giải của
  Scan Station, trong Area Board (GUI_DESIGN §6 không định nghĩa vị trí đặt) và trong header PN đã chọn của Add Part;
  revision trong kết quả Add Part; tìm Tracking theo tên PN; archive hay xóa mềm PN; versioning, view lịch sử và bỏ
  archive cho Planned Route; kiểm tra tham chiếu template khi ngưng hoạt động Area, Operation hay retire Machine; các
  thiết lập hiển thị khác, chính sách Due Soon theo Department và chu kỳ làm mới board cấu hình được; xóa một tùy chọn
  theme đã lưu, việc quản trị viên sửa theme của trạm và một gợi ý theme cho lần vẽ đầu tiên (một Scan Station dùng
  Light sẽ hiện Dark cho đến khi context của nó tải xong); cổng trên các thao tác khác ngoài `DONE`, `QUEUE` và Undo; giới
  hạn độ dài hay cưỡng chế ở database cho lý do Undo; Machine session; nhiều role cho một User và liên kết giữa User và
  Worker.
- Các việc còn lại của Phase 13 (Worker trên dòng phân bổ, lịch sử phiên, gỡ Scan Station mock, dấu hiệu cổng badge
  trong Tracking, danh mục lý do Undo, gợi ý Machine ưu tiên, tên route duy nhất, tên template tại thời điểm release,
  Area ngưng hoạt động trên bước route) được liệt kê trong `Deferred`.

## Phase 14 — Authentication, Role Enforcement, and Authorized Management Corrections

- xác thực User của ứng dụng; badge của Worker vẫn là định danh audit tại Scan Station và không bao giờ trở thành thông tin đăng nhập
- cưỡng chế phân quyền phía server trên mọi thao tác ghi Management, Administration, hiệu chỉnh, điều chỉnh phân bổ và master data — việc hiển thị ở frontend không bao giờ là ranh giới bảo mật
- áp các correction permission đã cấu hình ở Phase 13 cho Undo/các lệnh hiệu chỉnh và các thao tác sản xuất đặc quyền khác
- biến năng lực **phân bổ / đảo ngược phân bổ của Management** từ Phase 10 thành một workflow Management có phân quyền thật: phân bổ số lượng tồn kho để lại cho sau, xem lịch sử phân bổ, và ghi thêm các lần đảo ngược có audit kèm lý do bắt buộc; không bao giờ sửa/xóa lịch sử phân bổ
- triển khai đường hiệu chỉnh phân bổ có thẩm quyền tường minh mà PROJECT_PROFILE §8.12 / §18 cho phép khi một lần hiệu chỉnh có thể vượt giới hạn demand còn lại thông thường; đây là một ý định riêng, đặc quyền, có audit và không bao giờ là sự nới lỏng phân bổ thường lệ của Stockroom
- triển khai workflow **điều chỉnh AssignedRoute** cho một QuantityFlow `PLANNED` được chọn (PROJECT_PROFILE §8.10 / §17): chỉ người dùng có thẩm quyền, lý do tường minh, trạng thái route trước được giữ trong lịch sử audit, chỉ flow được chọn, Route Template và lịch sử Movement thực tế bất biến không bị đụng tới, và không lặng lẽ viết lại route sau một lần lệch, dùng biểu diễn audit điều chỉnh route chuẩn (`ROUTE_ADJUSTED`)
- gắn định danh User đã xác thực vào các bản ghi audit Management/Admin và thao tác vòng đời Machine khi liên kết tác nhân chuẩn áp dụng; Movement sản xuất của Scan Station tiếp tục dùng định danh Worker từ Phase 13
- cung cấp một **audit trail theo PN**, chỉ đọc, trong Tracking (`View audit trail`; PROJECT_PROFILE §21 Tracking "correction history", §28): các thay đổi đã ghi đối với master data của PN, đối với các Work Order và dòng Work Order Demand yêu cầu nó (gồm thay đổi priority / Hot list và việc hoàn tất Work Order), các mục phân bổ Management của nó, mọi lần đảo ngược phân bổ (kể cả của Stockroom) và các hiệu chỉnh vượt demand có thẩm quyền, và các lần điều chỉnh route của các Quantity Flow của nó — mới nhất trước, kèm thời gian, người dùng và lý do; Movement sản xuất (kể cả Undo) và các phân bổ Stockroom thường lệ ở trong lịch sử bất biến riêng của chúng; không gì trong đó sửa được
- năng lực hiệu chỉnh rộng hơn của Manager/Admin dùng các lệnh tường minh có kiểu và có audit; Phase 14 không tạo đường sửa/xóa chung nào cho lịch sử PartMovement
- cung cấp mô hình thông tin đăng nhập cho User của ứng dụng; `users.login_name` là tên tài khoản mà việc đăng nhập dùng
- khởi tạo tài khoản Administrator đầu tiên và bảo vệ chống gỡ người giữ cuối cùng của `MANAGE_USERS_AND_ROLES` (và của `MANAGE_CORRECTION_PERMISSIONS`) khi bắt đầu cưỡng chế
- ánh xạ mọi thao tác được cưỡng chế vào các permission key của Phase 13; năng lực mới dùng key sẵn có hoặc thêm key mới bằng cách mở rộng bộ từ vựng — key không bao giờ bị đổi tên; thay đổi bốn correction permission (`UNDO_RECENT_SCANS`, `PERFORM_QUANTITY_CORRECTIONS`, `EDIT_WORK_ORDER_ALLOCATION`, `PERFORM_HISTORICAL_CORRECTIONS`) được phép bởi `MANAGE_CORRECTION_PERMISSIONS` và thay đổi mọi key khác, role và user được phép bởi `MANAGE_USERS_AND_ROLES`, cưỡng chế phía server
- định nghĩa cách các năng lực của Scan Station và permission Undo áp dụng, khi Scan Station do Worker vận hành, mà Worker không phải User và không giữ role nào (PROJECT_PROFILE §7, §16, §20)
- cưỡng chế permission key là thẩm quyền duy nhất, như PROJECT_PROFILE §16 và §20 nêu: không có superuser ngầm định và không hành vi nào gắn với tên role; một câu chuẩn nêu tên role là nêu các quyền ban đầu của role đó
- phân giải tầng User của theme (GUI_DESIGN §2.1) cho User đã xác thực từ tùy chọn đã lưu, trên tầng Scan Station, và quyết định nút chuyển lưu vào tầng nào khi có User đăng nhập

Một lần lệch route đã ghi trên Movement `TRANSFERRED` thực tế không bị nhân đôi chỉ để tạo ra thêm một sự kiện lệch. `ROUTE_ADJUSTED` audit một thay đổi AssignedRoute có thẩm quyền về sau; lịch sử Movement thực tế vẫn là căn cứ quyết định.

**Đã có.**

- **Đăng nhập** (migration `0029_phase14_sign_in`). Tài khoản cục bộ (quyết định của chủ sở hữu): `user_credentials` giữ
  hash scrypt, cờ mật khẩu tạm, bộ đếm lần sai và trạng thái khóa; `user_sessions` giữ digest của một token mờ, không bao
  giờ giữ chính token. Administration → Settings → User sign-in cấu hình thời hạn (mặc định bật, 30 ngày, hoặc không bao
  giờ; tính tuyệt đối từ lúc đăng nhập), việc khóa (10 lần đăng nhập sai thì khóa 15 phút) và yêu cầu mật khẩu mới ở lần
  đăng nhập đầu (mặc định bật); khoảng cấu hình được là 1 đến 365 ngày, 3 đến 100 lần đăng nhập sai và 1 đến 1440 phút.
  Mật khẩu có 6 đến 256 ký tự sau chuẩn hóa NFKC và không có quy tắc thành phần. API:
  `GET`/`POST`/`DELETE /api/session`, `PUT /api/session/password`, `PUT /api/users/{id}/password` (mật khẩu tạm; kết
  thúc các phiên đăng nhập của User và gỡ khóa) và `GET`/`PUT /api/policies/sign-in`. Mọi thao tác ghi cần header
  `X-PartFlow-CSRF: 1`; cookie phiên `partflow_session` là `HttpOnly`, `SameSite=Strict` và `Secure` khi đặt
  `SESSION_COOKIE_SECURE`. Các lần đăng nhập và thất bại được ghi log, không audit.
- **Administrator đầu tiên và khôi phục.** Khi chưa có Administrator nào (một User đang hoạt động có mật khẩu và role giữ
  `MANAGE_USERS_AND_ROLES`), `POST /api/setup/administrator` nhận một setup token dùng một lần mà server in ra log; việc
  tạo là nguyên tử và đóng việc thiết lập, và role của Administrator đầu tiên phải giữ cả `MANAGE_USERS_AND_ROLES` lẫn
  `MANAGE_CORRECTION_PERMISSIONS`. `python -m app.cli reset-password --login-name <name>` chỉ đặt lại một mật khẩu đã có
  khi đã có Administrator và không bao giờ tạo mới;
  `python -m app.cli restore-correction-permission-management --role-name <role>` sửa một database không còn ai giữ
  `MANAGE_CORRECTION_PERMISSIONS` (server ghi một cảnh báo lúc khởi động ở trạng thái đó).
- **Cưỡng chế.** Bảng đăng ký route `app/api/route_access.py` phân loại mọi route là `PUBLIC`, `STATION`, `SIGNED_IN`
  hoặc `PERMISSION`, và một test thất bại với bất kỳ route nào chưa được liệt kê. Các lần đọc công khai là Production
  Board, các danh sách của Station Selector, `GET /api/machines`, `GET /api/route-templates`, các lần đọc ảnh và
  `GET /api/policies/due-soon`. Các lần đọc Administration cần một User đã đăng nhập; các lần đọc Management cần
  `VIEW_PRODUCTION_DATA` hoặc một key có thao tác mà view đó chứa. Mọi thao tác ghi Administration, master data và
  Management cần permission key riêng của nó (ví dụ `MANAGE_AREAS`, `MANAGE_MACHINES`, `MANAGE_PART_NUMBER_MASTER`,
  `MANAGE_WORK_ORDERS`, `EDIT_WORK_ORDER_DEMAND`, `CONFIGURE_SYSTEM_SETTINGS`, `MANAGE_USERS_AND_ROLES`). Một lần lưu Work
  Order được đánh giá theo nội dung (số hay due date so với các dòng demand), và một thay đổi Hot list theo tác dụng của
  nó (thêm hay gỡ thành viên cần `SET_DEMAND_PRIORITY`, sắp xếp lại cần `REORDER_HOT_ITEMS`, kể cả Undo và Redo); việc tự
  gỡ Hot kế thừa key của lệnh kích hoạt. Giá trị badge chỉ trả về cho người giữ `MANAGE_WORKERS`. Một lần từ chối là 403
  `permission_denied` kèm `required_permissions` đã sắp xếp (với `any_permission: true` cho một lần đọc).
- **Bảo vệ quản lý permission.** Cấp hay thu hồi một correction permission hoặc `MANAGE_CORRECTION_PERMISSIONS`, và đổi
  role, trạng thái hoạt động hay mật khẩu của một user có role giữ một trong số đó, cũng cần `MANAGE_CORRECTION_PERMISSIONS`.
  Quy tắc người giữ cuối cùng từ chối một thay đổi khiến không còn user đang hoạt động có mật khẩu nào giữ
  `MANAGE_USERS_AND_ROLES`, hoặc không còn ai giữ `MANAGE_CORRECTION_PERMISSIONS` (409 `last_permission_holder`). Các thao
  tác ghi role và user tuần tự hóa trên advisory lock `partflow:user-administration`.
- **Tác nhân.** Mọi dòng audit, sự kiện vòng đời Machine và dòng phân bổ Management mang `actor_user_id`; một lần release
  ghi User trong ngữ cảnh Movement `RECEIVED` của nó; các cột tác nhân dạng text cũ không còn được ghi và không bao giờ
  được điền bù; không Movement nào của Scan Station mang User. Một lệnh Management được phát lại bởi một User khác với
  người đã ghi nó bị từ chối bằng 409 `recorded_by_another_user`.
- **Thiết bị Scan Station** (migration `0030_phase14_station_devices`; quyết định của chủ sở hữu). Chỉ một thiết bị đã
  enroll mới gọi được các route Scan Station, và mỗi lệnh của trạm cần permission của role áp dụng tại Scan Station
  (`application_policy.scan_station_role_id`, ban đầu là role Operator được seed). Quản trị viên cấp một mã enroll dùng
  một lần cho một trạm (hiệu lực 15 phút, dùng một lần); trình duyệt của trạm đổi nó lấy một device token gửi trong header
  `X-PartFlow-Station-Device`; một thiết bị có thể bị thu hồi. Chỉ digest của mã và token được lưu. Các lần từ chối: 401
  `station_device_required`, 403 `station_device_mismatch`, 409 `station_context_changed`, và 403
  `station_permission_denied` (đánh giá sau lần kiểm tra lại idempotency, nên một lệnh đã commit vẫn được phát lại). Việc
  enroll cần `MANAGE_SCAN_STATIONS` và, khi role của trạm giữ một permission được bảo vệ, cần thêm
  `MANAGE_CORRECTION_PERMISSIONS`; thu hồi chỉ cần `MANAGE_SCAN_STATIONS`. Một thiết bị không bao giờ nhận diện Worker, và
  một badge không bao giờ cấp quyền. Scan Station ẩn các thao tác mà role của trạm không cấp.
- **Phân bổ của Management và hiệu chỉnh vượt demand** (migration `0031_phase14_beyond_demand`). Management phân bổ tồn
  kho để lại cho sau (`POST /api/allocations/management`), đảo ngược (`POST /api/allocations/{id}/reversals`, chỉ
  Management) và ghi hiệu chỉnh có thẩm quyền (`POST /api/allocations/corrections`: một dòng, nhiều hơn demand còn lại
  của nó, không bao giờ vượt số lượng tồn kho khả dụng, lý do bắt buộc, ghi `exceeds_demand`, đảo ngược được). Tất cả cần
  Edit Work Order Allocation (`EDIT_WORK_ORDER_ALLOCATION`); Stockroom chỉ giữ việc xác nhận khi nhận hàng. Hộp thoại
  dùng chung `Adjust WO Allocation` được Work Order Details cung cấp trên mọi dòng đã lưu của Work Order Open, Released
  và Completed, và bởi mục Corrections của Tracking.
- **Điều chỉnh AssignedRoute** (migration `0032_phase14_route_adjusted`).
  `POST /api/quantity-flows/{quantity_flow_id}/route-adjustments` (`ASSIGN_ROUTES`) chỉ thay các bước tương lai của
  AssignedRoute riêng của một flow `ACTIVE` `PLANNED` — các bước sau bước cuối cùng mà bất kỳ Movement nào của flow tham
  chiếu, kể cả Movement đã hoàn tác — với lý do bắt buộc, một chốt chặn route cũ (409 `route_changed`) và từ chối phần
  đuôi không đổi. Nó ghi một dòng audit `ROUTE_ADJUSTED` trên `AssignedRoute` với đầy đủ danh sách bước trước và mới,
  không ghi Movement nào, và không bao giờ đụng tới Route Template hay flow khác. PostgreSQL cấm mọi UPDATE trên
  `assigned_route_steps` và việc xóa một bước đang được tham chiếu. Undo tại trạm từ chối một lệnh đã tạo flow mà route
  của nó được điều chỉnh sau đó. Tracking cung cấp `Edit assigned Route…`.
- **Audit trail theo PN** (`app/application/audit_trail.py`; `GET /api/tracking/audit-trail`): một bộ đọc chỉ đọc, mới
  nhất trước, trên các dòng audit của bản ghi master của PN, của các Work Order và dòng demand yêu cầu nó (dòng đã xóa
  được đánh dấu `(since deleted)`) và của các lần điều chỉnh route của các flow của nó, cộng các dòng phân bổ Management
  và mọi lần đảo ngược phân bổ. Mục Corrections của Tracking có thêm `Change priority` (một lần chuyển sang Management →
  Priority) và `View audit trail`.
- **Tầng theme User** (`PUT /api/session/theme-preference`; quyết định của chủ sở hữu). Thứ tự ưu tiên User → Scan
  Station → Dark trên mọi route. Khi có User đăng nhập, nút chuyển lưu tùy chọn của User trên mọi route; tùy chọn của trạm
  chỉ được lưu khi trình duyệt biết chưa có ai đăng nhập; đăng xuất trở về tùy chọn của trạm trên route trạm và về Dark ở
  nơi khác. Thay đổi theme không được audit. Worker Session không bao giờ ảnh hưởng theme.

**Giới hạn tạm thời.**

- Role Administrator được seed không giữ `VIEW_PRODUCTION_DATA`, `MANAGE_WORK_ORDERS` hay hai key priority (quyết định
  của chủ sở hữu: chỉ dùng key, giữ nguyên seed), nên cho đến khi một quản trị viên cấp chúng trong Roles & permissions,
  nó không mở được Area Board hay Priority, không tạo được Work Order và không release được.
- Việc thiết lập lần đầu chỉ được bảo vệ bởi setup token in trong log của backend.
- Device token và cookie phiên là thông tin xác thực kiểu bearer và phải đi qua HTTPS, do proxy của nền tảng cung cấp
  trên máy chủ production (Phase 16). Một lần thu hồi có hiệu lực từ request kế tiếp.
- Các dòng audit do trạm ghi hoặc ghi trước khi có đăng nhập không hiển thị người dùng, vì dòng đã lưu không phân biệt
  được một lần sửa tại trạm với một lần sửa Management cũ hơn.
- Các thao tác ghi Management đánh giá permission đọc được lúc bắt đầu request; chỉ thao tác ghi role, user và mật khẩu
  đọc lại chúng dưới khóa. Một lệnh Management đang chờ việc đổi tên đăng nhập của chính user thực hiện nó sẽ chờ trong
  khi giữ các advisory lock của nó: một lần đứng có giới hạn, không có chu trình.
- Không có key master data, hộp thoại Part Number mở ở chế độ chỉ đọc (nhãn vẫn mở được) và các dòng Planned Route và
  Machine không mở được, nên một người dùng chỉ đọc Planned Routes thấy các chip Area nhưng không thấy Operation, ước
  tính, Machine ưu tiên hay hướng dẫn của một bước.
- Việc đọc audit trail không có index riêng: việc tra cứu dòng đã xóa quét các dòng audit `WorkOrderDemand`. Thời gian đo
  được thấp hơn nhiều so với ngưỡng kích hoạt 100 ms; một index được thêm nếu một lần đọc trang vượt ngưỡng đó.
- Hai flow có route khác nhau, ví dụ hai flow anh em sau khi một flow được điều chỉnh, từ chối gộp cho tới khi cả hai
  mang cùng route.
- Các mặc định về theme mà chủ sở hữu thấy được và có thể bác: một User bị để đăng nhập trên trình duyệt của một máy
  trạm sẽ chi phối theme của nó cho tới khi đăng xuất (thực hiện từ chế độ standard, vì chip tài khoản bị ẩn trong chế độ
  production); khi trạng thái đăng nhập chưa biết, nút chuyển chỉ có hiệu lực trong phiên; nút chuyển của trạm cũng lưu
  ở chế độ standard khi chưa có ai đăng nhập (phương án khác là chỉ lưu ở chế độ production).

**Việc còn mở.**

- Permission key cho phép thực thi archival và purge lịch sử Movement — Phase 16.
- Thứ tự khóa khi sao chép snapshot AssignedRoute (một rủi ro có từ Phase 8) — Phase 16.
- Không lên kế hoạch, không có yêu cầu chuẩn: một dòng audit cho việc xóa dòng demand, cho việc hoàn tất do phân bổ hay
  việc mở lại do đảo ngược; đăng nhập user tại Scan Station; ghi thiết bị vào Movement hay phân bổ; một trình sửa để chọn
  role áp dụng tại Scan Station (hãy sửa quyền của role đó); bộ permission theo trạm hay theo thiết bị; cho thấy trình
  duyệt đã enroll cho những trạm nào; giới hạn tần suất thử mã enroll; một màn hình phân bổ Management nhiều dòng theo
  PN; một dòng `audit_events` cho các lệnh phân bổ (dòng phân bổ chính là bản ghi audit của nó); điều chỉnh phân bổ của Work Order đã hoàn tất từ Tracking (hãy dùng Work Order Details); sửa các bước route đã qua
  hay đang được tham chiếu, thay đổi Route Template hay flow khác qua một lần điều chỉnh, điều chỉnh flow Floating, đã
  đóng hay đã hoàn tất, cập nhật route tự động sau một lần lệch, dấu `adjusted` được kế thừa trên flow con hay kết quả
  gộp, và điều kiện bước-dự-kiến trên xác nhận lệch tại trạm; một bộ đọc audit toàn hệ thống cho các thực thể cấu hình,
  User, Role, thiết bị, Machine và Planned Route; Movement sản xuất và phân bổ Stockroom thường lệ trong trail; Worker
  của Stockroom trên dòng đảo ngược tại trạm; liên kết sâu vào Priority hay vào trail; audit việc đọc trail; tác nhân Hot
  list ngay trong view Priority; xóa tùy chọn theme đã lưu của User, hiển thị hay sửa nó trong Administration, và đồng bộ
  theme trực tiếp giữa các trình duyệt đang mở.
- Đăng nhập qua thư mục hoặc đăng nhập một lần (SSO), xác thực đa yếu tố, tự đặt lại mật khẩu, các lệnh
  `Quantity adjustment` và hiệu chỉnh lịch sử trong Tracking, và các permission key chưa route nào cần — được liệt kê
  trong `Deferred`. Việc trả số lượng đã nhập kho về sản xuất vẫn là một quyết định mở của chủ sở hữu (PROJECT_PROFILE
  §32).

## Phase 15 — File-Based Work Order Import

- import idempotent
- kiểm tra hợp lệ từng dòng
- báo cáo rõ ràng khi thất bại một phần
- không phụ thuộc ERP

Quyết định của chủ sở hữu:

- Định dạng file: CSV (UTF-8) **và** Excel (`.xlsx`), cùng tiêu đề cột cố định và cùng giới hạn cho cả hai, có template tải về; việc đọc `.xlsx` biện minh cho phụ thuộc `openpyxl`, và chỉ đọc giá trị đã lưu.
- Cho phép cập nhật Work Order **đang hoạt động** (Open / Released) từ file, nhưng chỉ sau khi UI liệt kê mọi thay đổi theo từng Work Order và người dùng gõ một xác nhận; việc cập nhật tuân theo các quy tắc sửa Work Order hiện có qua đúng một đường ghi sẵn có, và một Work Order đã hoàn tất không bao giờ bị thay đổi (được báo cáo, không bị đụng tới). Mọi dòng được import phải có Work Order Number.
- Đơn vị nguyên tử là một transaction cho mỗi Work Order: các Work Order hợp lệ được commit, các Work Order bị từ chối được liệt kê theo từng Work Order kèm lý do.
- `Check file` (xem trước) là bắt buộc trước `Import`; việc import kiểm tra lại đúng các byte đó, và xác nhận bằng cách gõ cho các cập nhật diễn ra ở bước Import.

**Đã có.** Không có migration; phụ thuộc mới `openpyxl` (runtime) và `types-openpyxl` (phát triển).

- Management → Work Orders có `Import from file…` (GUI_DESIGN §11.7) cho một file CSV UTF-8 hoặc worksheet hiển thị đầu
  tiên của một file `.xlsx` (chỉ giá trị đã lưu; công thức không bao giờ được tính; đọc các cột A đến BL).
- Route: `POST /api/work-orders/import/preview` (`Check file`: chạy thử không khóa, không ghi, phân loại từng Work Order
  là `WILL_CREATE`, `WILL_UPDATE`, `EXISTS` hoặc `REFUSED` kèm các giá trị đọc được theo từng dòng, và trả một
  `check_token` và, với các cập nhật, một `update_token`); `POST /api/work-orders/import` (kiểm tra lại đúng các byte đó
  — một file có hash khác token `X-PartFlow-Import-Check` bị từ chối — và báo `CREATED`, `UPDATED`, `EXISTS` hoặc
  `REFUSED` cho từng Work Order); và các template chỉ có tiêu đề `GET /api/work-orders/import/template.csv` và `.xlsx`.
- Cột: Work Order Number, Part Number và Requested Quantity là bắt buộc; Job Number và Due Date (`YYYY-MM-DD`) là tùy
  chọn. Một dòng không có Work Order Number chặn toàn bộ lần import; các cột định mức vật tư (BOM) của nguồn làm file bị
  từ chối. Giới hạn: 1 MiB, 2.000 dòng dữ liệu, 500 dòng cho mỗi Work Order, 200 ký tự cho mỗi ô văn bản. Một Work Order
  có bất kỳ dòng không hợp lệ nào bị từ chối toàn bộ trong khi các Work Order khác vẫn được import. Phát lại một file
  không tạo gì: Work Order Number là khóa idempotency.
- Work Order mới được tạo qua đường ghi sẵn có `create_work_order`, mỗi cái trong transaction riêng. Một Work Order được
  tạo không có ngày nhận (mọi Work Order được import) lấy ngày hôm nay theo lịch của nhà máy (`SITE_TIMEZONE`). Bộ kiểm
  tra số lượng dùng chung từ chối giá trị vượt phạm vi integer của PostgreSQL bằng 422 trên cả đường nhập tay.
- Cập nhật Work Order Open và Released, khớp theo PN chuẩn: một số lượng (không bao giờ thấp hơn mức sàn đã release hay
  đã phân bổ), một due date, một Job Number được nối thêm, và dòng mới trên Work Order Open. Các hệ quả (rời Hot list,
  hoàn tất Work Order, Part Number mới) được đánh dấu trước khi xác nhận. Một Work Order đang hoạt động không đổi là
  `EXISTS` ("Already in PartFlow — nothing to change"); một Work Order đã hoàn tất không bao giờ bị thay đổi; một thay đổi
  vi phạm quy tắc sửa (dòng mới trên Work Order Released, số lượng dưới mức sàn) là `REFUSED` kèm lý do. Ô trống và các
  dòng không có trong file giữ giá trị đã lưu, và một lần import không bao giờ gỡ dòng. Import có ít nhất một cập nhật mở
  một xác nhận bằng cách gõ (`CHANGE {m}`) liệt kê mọi thay đổi và gửi nó dưới dạng `X-PartFlow-Import-Confirm` (giá trị
  sai định dạng là 422). Khi danh sách thay đổi của chính lần commit khác với danh sách đã xác nhận, mọi cập nhật của file
  bị từ chối theo từng Work Order trong khi các lần tạo vẫn được import; một Work Order đã thay đổi sau khi Import kiểm
  tra lại nó bị từ chối bởi một state token; không có gì chưa được xác nhận được ghi. Mỗi cập nhật là một transaction qua
  đúng một đường ghi `update_work_order`, nên các quy tắc sửa thủ công, việc gỡ khỏi Hot list và phán đoán hoàn tất đều
  áp dụng.
- Audit: dòng `CREATED` của Work Order và các dòng `CREATED`/`UPDATED` của demand mang
  `metadata.intake = {"channel": "FILE_IMPORT"}` và User đã đăng nhập; một lần import không ghi dòng header Work Order nào
  cho các cập nhật.
- Permission được đánh giá theo nội dung: xem trước và template cần Create and edit Work Orders (`MANAGE_WORK_ORDERS`)
  hoặc Edit Work Order Demand (`EDIT_WORK_ORDER_DEMAND`); lần commit cần key thứ nhất cho các Work Order nó tạo và key thứ
  hai cho các Work Order nó thay đổi (một 403 nêu toàn bộ tập key).
- Tầng `web` cho phép thân request 4 MiB và timeout 180 giây trên route import (Phase 16); proxy của nền tảng phải cho
  phép ít nhất 5 MiB và 300 giây (DEPLOYMENT §5).

**Giới hạn tạm thời.**

- Các mặc định hướng tới chủ sở hữu được chọn khi chưa có quyết định và vẫn mở để quyết: một xác nhận đã cũ làm từ chối
  mọi cập nhật của file thay vì chỉ các Work Order đã đổi, và việc giảm số lượng được phép xuống tới mức sàn đã cam kết.

**Việc còn mở.** Các năng lực import file vượt quá phần trên, việc lưu giữ hay purge các dòng audit mà một lần import
ghi, và một giới hạn độ dài dùng chung cho Work Order Number, Part Number và Job Number trên đường nhập tay được liệt kê
trong `Deferred`, mỗi việc chờ một quyết định của chủ sở hữu.

## Phase 16 — Deployment, Production Hardening, and Admin Maintenance

Các yêu cầu vận hành và đường đi theo nền tảng được định nghĩa trong [`DEPLOYMENT.md`](./DEPLOYMENT.md). Hướng dẫn chạy repository hiện tại như một môi trường staging nội bộ có giới hạn không làm cho phase này hoàn tất.

- backup
- migration
- HTTPS/truy cập nội bộ
- khả năng quan sát (observability)
- rollback
- kiểm tra đối soát
- go-live trên NAS (quyết định của chủ sở hữu ngày 2026-11-09: dùng thật trực tiếp, không có giai đoạn pilot và không có release gate; DEPLOYMENT §1 và §5)
- gia cố database role cho production, gồm các hạn chế UPDATE/DELETE theo dự định đối với role của ứng dụng trên lịch sử sản xuất/audit chỉ-ghi-thêm
- kiểm tra định danh khi nâng cấp nền tảng: định danh PN chuẩn và badge Worker theo Cơ sở dữ liệu ký tự Unicode (UCD) của Python ở backend, được ghim ở `15.0.0`, và kiểm tra tiền tố Asset Tag từ chối tập khoảng trắng `\s` của Python, một tập cha thực sự của `[[:space:]]` trong libc của PostgreSQL; trước mọi lần nâng cấp Python làm đổi UCD, hoặc một thay đổi glibc, một kiểm tra chỉ đọc liệt kê mọi PN, badge và tiền tố Asset Tag đã lưu mà dạng chuẩn hay kết quả CHECK của nó sẽ thay đổi, và chủ sở hữu quyết định việc chuẩn hóa lại trước khi nâng cấp
- dạng chuẩn khi nhập PN: các đường nhập PN bằng tay và nhãn demand của Work Orders gửi đầu vào thô đã cắt khoảng trắng và hiển thị dạng chuẩn của server, để bảng Unicode của trình duyệt không bao giờ quyết định một PN được lưu (hiện nay chúng chỉ có thể khác server ở khoảng 27 code point nhạy với phiên bản Unicode)
- thứ tự khóa khi sao chép snapshot AssignedRoute: `lineage.copy_assigned_route` (bản sao snapshot của một flow con và của một kết quả gộp) chèn các bước được sao theo thứ tự bước mà không có lượt khóa tăng dần, nên mỗi INSERT lấy `FOR KEY SHARE` trên Area của nó qua khóa ngoại trong khi một lần chuyển số lượng một phần đã giữ Area đích `FOR UPDATE`; một lần release cắt chéo hoặc một lần chuyển một phần cắt chéo thứ hai khi đó có thể deadlock (PostgreSQL hủy một bên: không ghi gì, kết quả không rõ tại trạm, phát lại được). Hãy khóa các Area và Operation của bản sao trong một lượt tăng dần cùng với Area đích trước lệnh INSERT snapshot, như `route_templates.lock_assignment_areas` làm cho release và receipt, kèm một test đồng thời với route cắt chéo
- **bảo trì archival/purge lịch sử Movement có tính quản trị** (PROJECT_PROFILE §28): thời hạn lưu giữ cấu hình được, chọn theo mốc cắt → xuất archive không mất mát → kiểm tra → purge đúng các dòng đã được archive và kiểm tra qua một đường bảo trì đặc quyền riêng; giữ nguyên vẹn các chuỗi đảo ngược/tham chiếu và các nhóm lệnh nguyên tử để không dòng nào được giữ lại tham chiếu tới lịch sử đã purge; gồm các cơ chế kích hoạt theo chính sách / ngưỡng kích thước / thủ công, xem trước phạm vi, lý do bắt buộc và audit đầy đủ; runtime thông thường vẫn chỉ-ghi-thêm; không xây một engine lưu giữ/archival đầy đủ trước phase này; thời hạn lưu giữ đã được Phase 13 lưu (`application_policy.retention_period_months`, số tháng nguyên hoặc không có), và thước đo cùng đơn vị của ngưỡng kích thước dữ liệu được định nghĩa ở đây cùng với cơ chế kích hoạt của nó

Xóa cứng PartNumber master **không** phải là một thao tác bảo trì của Phase 16. Nó thuộc Management → Part Numbers của
Phase 13 và chỉ gỡ metadata.

**Đã có.** Không phần việc nào dưới đây thêm migration; head lược đồ vẫn là `0032_phase14_route_adjusted`.

### Lệnh đối soát

`python -m app.cli reconcile` kiểm tra trạng thái production đối chiếu với lịch sử Movement và không bao giờ sửa gì. Nó
lấy một snapshot REPEATABLE READ chỉ đọc sau các khóa bảng ACCESS SHARE (không khóa dòng hay advisory lock; timeout khóa
5 giây và timeout câu lệnh mặc định 300 giây) và chạy các kiểm tra sau:

- (a) đến (f) của OPERATIONS_RUNBOOK §7 (kiểm tra (f) không báo phần vượt của một hiệu chỉnh vượt demand có thẩm quyền,
  được ghi `exceeds_demand`), cộng các bất biến giữa nhiều dòng của SLICE1_DATA_MODEL §17 trừ bất biến về dòng audit, vốn
  do giao thức transaction cưỡng chế;
- (g) không Movement nào được giữ lại tham chiếu lịch sử đã purge — `not_applicable` cho tới khi có archival;
- (h) các chốt chặn chỉ-ghi-thêm và quyền của database role còn nguyên vẹn — `not_applicable` chỉ trên database không có
  các database role của PartFlow (phát triển, test, staging);
- (i) truy vấn Hot list của DEPLOYMENT §5 (các mục Hot list là demand đang hoạt động);
- (j) định danh chuẩn dưới interpreter và database đang chạy: mọi PN, badge Worker và tiền tố Asset Tag đã lưu mà dạng
  chuẩn hay kết quả CHECK của nó sẽ thay đổi, mọi CHECK dạng chuẩn được đánh giá lại dưới collation và ctype của database
  đang chạy, phiên bản collation, và các phép dò trùng lặp không phụ thuộc index trên các khóa định danh. Nửa glibc và
  image PostgreSQL chạy trên một lần restore lên server ứng viên (`restore-test.sh --db-image`), và
  `reconcile --check j` chạy trên một image ứng viên.

Báo cáo JSON đi ra standard output; mã thoát là 0 sạch, 1 không khớp, 2 không chạy được, và chỉ có giá trị khi đi cùng
một báo cáo đầy đủ có `exit_code` khớp. Mỗi phát hiện là một sự cố do chủ sở hữu quyết định (ví dụ các Work Order còn
chưa hoàn tất từ trước bản sửa hoàn tất ngày 2026-10-06, các mục Hot không còn hoạt động, lệch collation hay index).

### Image production và Compose stack

Mô tả đầy đủ nằm trong DEPLOYMENT §3.1. Tóm tắt:

- Stage `production` của image backend chạy dưới user không phải root `10001:10001` (Python 3.12, UCD `15.0.0`), không
  reload và không chạy migration khi khởi động. Thiết lập database lấy từ đúng một trong hai: `DATABASE_URL` (chấp nhận
  percent-encoding) hoặc các thiết lập `DATABASE_*` dạng file; một lỗi kiểm tra không bao giờ in lại giá trị đầu vào.
- Image `web` (`nginx:1.30.5-alpine`) phục vụ bản build frontend bất biến và làm proxy cho `/api` với giới hạn kích
  thước request (mặc định 1 MiB, 4 MiB trên các route tải lên và import, để JSON 413 của chính ứng dụng thắng), timeout
  180 giây trên route import, giới hạn tần suất theo địa chỉ client trên đăng nhập và các route mật khẩu, các response
  JSON 413, 429, 502 và 504, các header bảo mật gồm content security policy cơ bản, access log không có header thiết bị
  trạm hay query string, và địa chỉ client chỉ lấy từ một bước nhảy tin cậy. 413 hoặc 429 là từ chối chắc chắn; 502 hoặc
  504 là kết quả ghi không rõ.
- `compose.production.yaml` (project `partflow-production`) chạy `db`, `backend` và `web`, cộng các service chạy một
  lần `migrate`, `db-roles`, `backup-tools` và `status` dưới profile `ops`. `web` chỉ được publish trên `127.0.0.1`;
  image được gắn tag theo `PARTFLOW_RELEASE` và không bao giờ được pull hay build bởi `up` hoặc `run`; mật khẩu là secret
  file; cookie phiên luôn `Secure`. `.env.production.example` là bản kê cấu hình và secret, và CI build các image
  production và chạy các test tĩnh của artifact.

### Quy trình release

- Định danh release: image và bundle frontend mang release tag và commit đầy đủ; một lần build từ chối giá trị thiếu hay
  sai định dạng, và các tag đã có không bao giờ bị build lại.
- `GET /api/health/live` là liveness; `GET /api/health` là readiness và trả 503 khi lược đồ không khớp.
- Write gate (bật ở production, tắt ở phát triển và test) từ chối mọi thao tác ghi từ một trang của release khác (409
  `release_mismatch`) và mọi thao tác ghi khi lược đồ không khớp hoặc không xác nhận được database (503 `not_ready`), không
  ghi gì. `ACCEPT_SCHEMA_REVISION` chấp nhận một lược đồ cũ hơn đã được kiểm chứng tương thích trong lúc rollback.
  `last_seen_at` của một thiết bị trạm là thao tác ghi duy nhất được ghi nhận khi không khớp.
- `python -m app.cli migrate` nâng cấp trong một transaction, đòi một tham chiếu backup hoặc một lý do được ghi lại, áp
  quyền (grant) trong cùng transaction và từ chối khi còn một phiên backend đang kết nối; `python -m app.cli revision` báo
  cáo ở chế độ chỉ đọc.
- `deploy/production/release.sh` chỉ build tag đang được checkout, cần `--operator` và `--approver` (một người có thể ghi
  cùng tên cho cả hai), đóng băng thao tác ghi bằng cách dừng `backend`, lấy backup trước-release đã kiểm tra ngay trong
  lúc đóng băng, migrate, chuyển `backend` rồi `web`, chạy `smoke.sh` và ghi `record.json` (mã thoát 0 hoàn tất, 1 đã
  dừng, 2 không chạy được, 3 đang đóng băng, 4 cần xem xét). Nó không bao giờ được lên lịch và từ chối chạy khi có một
  project `partflow-staging` trên cùng Docker daemon.
- Frontend hiển thị thông báo `UPDATED` và chặn thao tác ghi trên một trang đã cũ. Khi không có hộp thoại nào mở, trang
  tự tải lại: trên các route Scan Station và Production Board sau 60 s, trên mọi route khác khi không ai dùng trang
  trong 2 phút và trên trang không còn gì chưa lưu (GUI_DESIGN §3 quy tắc 13).
- Rollback không đổi lược đồ thì chuyển release tag về bản cũ, nên image của release trước phải được giữ trên máy chủ.
  Rollback lược đồ thì restore backup trước-release (bên dưới). Không bao giờ có down-migration.

### Database role

- `python -m app.cli provision-roles` tạo hoặc sửa các role `partflow_app` và `partflow_maintenance` từ secret file,
  không có thuộc tính nâng quyền, không có membership và không sở hữu đối tượng nào, và không bao giờ ghi mật khẩu dạng
  rõ vào SQL, log hay báo cáo.
- `python -m app.cli apply-grants`, cũng chạy bên trong mọi lần `migrate`, suy ra mọi grant từ một bảng phân loại duy
  nhất (`backend/app/infrastructure/database_privileges.py`): chỉ SELECT và INSERT trên `part_movements`, `audit_events`,
  `machine_lifecycle_events`, `quantity_flow_lineage` và `work_order_allocations`; giữ UPDATE trên `worker_sessions`;
  DELETE nhưng không UPDATE trên `assigned_route_steps`; không TRUNCATE ở đâu cả; gỡ quyền PUBLIC và quyền mặc định. Một
  bảng chưa được phân loại làm lệnh từ chối. Grant không bao giờ được khôi phục từ dump; chúng được suy ra lại sau mọi
  lần migration và restore.
- `backend` production kết nối bằng `partflow_app` và không bao giờ giữ thông tin xác thực của owner; các trigger
  raise-on-write vẫn là lớp bảo vệ đầu tiên, và kiểm tra đối soát (h) xác minh cả hai lớp từ catalog.
- Các test backend cũng chạy dưới role của ứng dụng (`PARTFLOW_TEST_DATABASE_ROLE=app`, chế độ của CI); chế độ này là
  kiểm tra cục bộ bắt buộc trước khi commit một thay đổi đụng tới thao tác ghi database, grant hay phần thiết lập test.

### Backup, diễn tập restore và rollback bằng restore

- `deploy/production/backup.sh` công bố mỗi bản backup thành một thư mục dưới `PARTFLOW_BACKUP_DIR` (dump, mục lục,
  `manifest.json`, `SHA256SUMS`) bằng một lần đổi tên nguyên tử. `python -m app.cli backup-manifest`, `backup-verify` và
  `backup-rotate` chạy trong service `backup-tools` không có mạng. Việc xoay vòng giữ 14 bản hằng ngày và 8 bản hằng tuần
  đã kiểm tra cho tới khi chủ sở hữu đặt giá trị thật; các bản trước-release, thủ công và archive không bao giờ bị xoay
  vòng.
- `migrate` từ chối một backup trước-release đã cũ, của revision hay database khác, bị can thiệp, hoặc chưa chứng minh
  được là chứa mọi thao tác ghi.
- `deploy/production/restore-test.sh` restore một bản backup vào một Compose project cô lập, cấp các role tạm, áp grant,
  chạy một lần `reconcile` đầy đủ (kiểm tra (h) và (j) phải đạt) và các kiểm tra smoke, ghi bằng chứng có đo thời gian và
  chỉ gỡ project do chính nó tạo.
- Rollback bằng restore (OPERATIONS_RUNBOOK §6, đường 3) restore backup trước-release vào một database mới
  `<POSTGRES_DB>_r<stamp>` bên trong cùng instance PostgreSQL, giữ database đã migrate, và chỉ khởi động release trước
  sau khi sự chấp thuận của chủ sở hữu được ghi trong `rollback.json`. Một lần restore vào production không bao giờ ghi
  đè.
- Mã hóa và sao chép ra ngoài máy chủ do công cụ của nền tảng thực hiện (Hyper Backup trên NAS, hoặc restic trên một máy
  chủ riêng ảo (VPS)).

### Giám sát

- Backend production ghi mọi bản ghi log thành một dòng JSON; mọi response mang một `X-Request-ID` mà cả `web` và
  backend đều ghi log. Mỗi request ghi một bản ghi `app.access` với ngữ cảnh sản xuất của lệnh (PN, flow, demand, Area,
  Operation, Machine, Worker, Scan Station, `device_event_id`) và lý do của mọi lần từ chối, và không bao giờ ghi thân
  request, query string, cookie, token, badge, mật khẩu hay giá trị được scan; setup token của lần chạy đầu là ngoại lệ
  duy nhất đã được công bố.
- `python -m app.cli status` báo kích thước database, số kết nối, chờ khóa, khối lượng Movement, mức sẵn sàng của lược
  đồ, tuổi backup và database role đang kết nối.
- `deploy/production/check.sh` kiểm tra HTTPS, chứng chỉ, container, số lần khởi động lại, lỗi backend, đĩa, database,
  lược đồ và tuổi backup và thoát với mã khác 0 để tới được thông báo lỗi của bộ lập lịch trên máy chủ (ngưỡng: backup cũ
  hơn 26 giờ, đĩa còn trống dưới 15 %, chứng chỉ còn trong vòng 21 ngày trước khi hết hạn, bất kỳ mức tăng số lần khởi
  động lại nào, bất kỳ bản ghi lỗi backend nào; một lỗi lặp lại được thông báo mỗi 6 giờ).
  `deploy/production/scheduled-reconcile.sh` chạy `reconcile` hằng ngày.

### Bước tiếp theo: go-live trên NAS

PartFlow go-live trực tiếp trên Synology NAS của công ty. Việc cài đặt theo
[`deployment/SYNOLOGY_NAS.md`](./deployment/SYNOLOGY_NAS.md) §1 đến §10. Chỉ những gì phần mềm cần để chạy là bắt buộc
(DEPLOYMENT §5): HTTPS tại proxy của nền tảng (thân request ít nhất 5 MiB, timeout ít nhất 300 giây, chứng chỉ được mọi
máy trạm và Scan Station tin cậy), thiết lập lần đầu với một worker backend cho tới khi có Administrator đầu tiên, mỗi
thiết bị Scan Station được enroll, không có project `partflow-staging` trên Docker daemon production, và release từ một
git tag.

**Sau go-live, khi thuận tiện (không chặn điều gì).** Chưa việc nào trong số này được chạy trên máy chủ thật; các script
đã được chạy trên Docker Desktop và trong một container Linux.

- Proxy của nền tảng: nó đặt địa chỉ client ở cuối `X-Forwarded-For`, các thiết lập thân request và timeout của nó,
  peer loopback khớp với gateway `edge` (nếu không thì đặt `PARTFLOW_TRUSTED_PROXY`), việc lọc header thiết bị trạm khỏi
  log của chính nó, quy trình chứng chỉ và việc máy trạm tin cậy chứng chỉ, và quyết định có gửi HTTP Strict Transport
  Security (HSTS) hay không.
- Quyền của secret file, giới hạn tài nguyên và số worker đo được, và thời gian import trường hợp xấu nhất qua tên máy
  chủ thật.
- Một lần release và một lần rollback thực hiện trên NAS; các kiểm tra trên trình duyệt cần một Area, Operation đã
  cấu hình và một Scan Station đã enroll (tự tải lại trên route production của Scan Station, không tải lại khi một hộp
  thoại sản xuất hay modal đăng nhập Worker đang mở, `Reload page` trong modal, và feed `● Live` dưới thông báo cập
  nhật); và một trang đang mở trên route khác tự tải lại khi không ai dùng nó trong 2 phút.
- Phép dò quyền và `reconcile --check h` trên database production, và một ghi nhận rằng `backend` chỉ giữ
  `partflow_app_password`.
- Thư mục backup, tác vụ backup hằng ngày và bản sao mã hóa ngoài máy chủ, bản backup đầu tiên, một lần kiểm tra rằng
  bản sao ngoài máy chủ restore được, một lần chạy `restore-test.sh` có đo thời gian, hành vi đóng-an-toàn khi thiếu mount
  backup trên engine Linux, và một lần diễn tập rollback bằng restore nếu chủ sở hữu muốn.
- Lịch `check.sh` (mỗi 15 phút) và `scheduled-reconcile.sh` hằng ngày, một thông báo lỗi nhận được thật, khối lượng log
  đo được, và thời gian chạy reconcile trên database production.
- Nửa glibc và image PostgreSQL của kiểm tra (j) trên máy chủ thật, trước mọi lần nâng cấp nền tảng.
- Các kiểm tra tương tự trên một VPS, chỉ khi có dùng VPS.

### Đã lên kế hoạch: archival và purge lịch sử Movement

Archival và purge sẽ làm khi khối lượng dữ liệu cần tới; chưa phần nào được triển khai. Hiện nay thời hạn lưu giữ được
lưu nhưng không có gì đọc nó, toàn bộ lịch sử Movement ở lại trong database, Administration nói rõ các lần chạy archival
và purge chưa có, kiểm tra (g) là `not_applicable`, và `check.sh` báo `archival_proposal` là `SKIP`. Phần việc đã lên kế
hoạch có ba bước: (1) bản ghi lần chạy, phân quyền, màn hình xem trước, yêu cầu và phê duyệt, và việc xuất cùng kiểm tra
archive, không xóa gì; (2) purge đặc quyền với một bản ghi chuyển tiếp (carry-forward) cho các flow đã purge, để các số
lượng suy ra vẫn đúng; (3) các cơ chế kích hoạt theo chính sách và ngưỡng kích thước, chỉ đề xuất lần chạy. Các ràng buộc
đã được phần việc đã triển khai cố định: thư mục archive nằm ngoài `PARTFLOW_BACKUP_DIR` và không bao giờ bị công cụ nào
xoay vòng hay dọn bớt; điều kiện tiên quyết của purge là một backup được `backup-verify` kiểm tra mà dump của nó bắt đầu
sau bước kiểm tra của lần chạy; role bảo trì nhận DELETE chỉ trên `part_movements`, với trigger và function mới của nó
được thêm vào tập chốt chặn được ghim của kiểm tra (h) trong cùng commit với migration của chúng; các bảng lần chạy mới
nhận một lớp grant và một service `maintenance` được thêm; và một lần purge Movement chỉ xóa Movement, không bao giờ xóa
dòng `worker_sessions`.

Các quyết định của chủ sở hữu cần có trước khi bắt đầu phần việc này:

- phạm vi xóa (mặc định đề xuất: chỉ dòng Movement, với một bản ghi chuyển tiếp cho các flow đã purge làm nguồn bằng
  chứng thứ hai cho số lượng đã release, đã nhập kho và mức sử dụng route, điều này cần sửa đổi PROJECT_PROFILE §13 và
  §28);
- định dạng, nơi lưu, cách kiểm tra và độ bền của archive (gồm sao chép ra ngoài máy chủ với checksum được ghi lại trước
  mọi lần purge);
- permission key cho phép thực thi archival và purge (một key mới, hoặc một key có sẵn như `CONFIGURE_SYSTEM_SETTINGS`);
- quy tắc mốc cắt thủ công (không mới hơn thời hạn lưu giữ đã cấu hình, hoặc một mức sàn cố định 12 tháng khi chưa đặt
  thời hạn);
- mô hình thực thi (phê duyệt trong ứng dụng kèm lý do và bản xem trước, thực thi bởi một job bảo trì ngoài luồng, có thể
  kèm lần phê duyệt thứ hai của người vận hành máy chủ);
- ý nghĩa và đơn vị của ngưỡng kích thước dữ liệu (ví dụ số dòng Movement hiện có);
- một chính sách đã cấu hình có bao giờ được chạy mà không cần phê duyệt từng lần hay không (mặc định đề xuất: không);
- các dòng audit do import file ghi có bao giờ được lưu giữ có thời hạn hay purge hay không (mặc định đề xuất: hoãn).

### Giới hạn tạm thời

- Một superuser của database (owner role) tắt một trigger hoặc đặt `session_replication_role = replica` trong phiên của
  chính nó có thể thay đổi lịch sử rồi khôi phục chốt chặn; kiểm tra (h) chỉ thấy trạng thái lưu bền và không phát hiện
  được thay đổi đã qua như vậy (được chấp nhận; một chuỗi hash trên các dòng Movement không được chọn vì nó thêm chi phí
  ghi cho mọi lệnh sản xuất). Các đối tượng do owner tạo nằm ngoài (h) trừ khi chúng thay thế một chốt chặn. Database
  phát triển, test và staging của Deployment Admin giữ một owner role duy nhất, nơi chỉ riêng trigger ràng buộc mọi
  đường ghi không tắt nó.
- Việc đọc lại có xác thực từng màn hình sau một lần restore chưa được tự động hóa; reconcile phát lại lịch sử Movement
  đối chiếu với mọi projection, và chủ sở hữu có thể kiểm tra bằng tay (`restore-test.sh --keep`).
- Các thao tác ghi thực hiện bên ngoài ứng dụng nằm ngoài cam kết độ mới của backup.
- Backup chứa mọi bảng, kể cả hash thông tin đăng nhập, nên thư mục backup và mọi bản sao là bí mật.
- Cho tới khi tác vụ backup hằng ngày chạy, các bản backup duy nhất là những bản `release.sh` lấy trước mỗi lần
  release.

### Quyết định mở của chủ sở hữu

- Chấp nhận đích rollback-bằng-restore đã triển khai: một database mới, rỗng, bên trong cùng instance PostgreSQL thay vì
  "một instance sạch" (nếu bị bác, một volume mới thay bước đó của quy trình; không cần sửa dữ liệu).
- Số lượng bản backup lưu giữ thật (giá trị tạm: 14 hằng ngày và 8 hằng tuần) và, nếu muốn, một mục tiêu điểm khôi phục
  (RPO) và mục tiêu thời gian khôi phục (RTO) được duyệt; lịch hằng ngày cho RPO 24 giờ cho tới khi đó.
- Có gửi HSTS từ proxy của nền tảng hay không.
- Các mặc định được khuyến nghị đang có hiệu lực mà chủ sở hữu chưa xác nhận tường minh và có thể thay đổi: readiness trả
  503 khi lược đồ không khớp; write gate của backend; hai database role và giới hạn superuser được chấp nhận của kiểm
  tra (h); mã hóa và sao chép ra ngoài máy chủ bằng công cụ của nền tảng; các ngưỡng giám sát ở trên; và thông báo cập
  nhật của GUI_DESIGN §3 quy tắc 13.

### Việc còn mở

- Dạng chuẩn khi nhập PN (gạch đầu dòng phạm vi ở trên) — chưa triển khai.
- Thứ tự khóa khi sao chép snapshot AssignedRoute (gạch đầu dòng phạm vi ở trên) — chưa triển khai; hiện nay một
  deadlock cắt chéo hủy một bên mà không ghi gì và cho kết quả không rõ tại trạm, và một lần thử lại sẽ phát lại.
- Kiểm tra định danh khi nâng cấp nền tảng: phía Python đã được kiểm tra (j) triển khai; trước mọi lần nâng cấp Python
  hay glibc, chủ sở hữu xem danh sách của kiểm tra đó và quyết định việc chuẩn hóa lại.
- Deployment Admin (công cụ `pf` riêng dùng cho staging): các health check container và các probe frontend của nó phải
  chuyển sang `GET /api/health/live` trước khi staging chạy một commit có head lược đồ khác với database staging, vì
  `/api/health` là readiness; cách nó cấp database role và việc nó có dùng artifact backup hay không được quyết định ở
  bước profile PartFlow của nó (PF-A4.1), và với phương án nào thì `apply-grants` cũng chạy sau mọi lần restore và kiểm
  tra (h) phải đạt trước khi ứng dụng khởi động; một route được quản lý để chạy một lệnh ứng dụng trên staging (PF-A4).
- Archival và purge lịch sử Movement — đã lên kế hoạch ở trên.

## Deferred

Mỗi mục dưới đây chờ một quyết định của chủ sở hữu hoặc một yêu cầu chuẩn; chưa mục nào có phase sở hữu.

- đồng bộ ERP
- Các năng lực import file được chủ sở hữu hoãn ngày 2026-10-06: chọn worksheet; các định dạng khác (`.xls`, `.xlsm`,
  CSV phân cách bằng `;`); một màn hình ánh xạ cột và bí danh tiêu đề; các cột Revision, assembly cha, due date của Work
  Order, ngày nhận, Request Type, người yêu cầu, lý do và ghi chú; một view lịch sử hay lô import và tên file trong
  audit; và phát hiện các dòng worksheet bị ẩn hay bị lọc. Việc cập nhật Work Order hiện có từ file cố ý không cung cấp,
  mỗi việc cho tới khi có một quyết định của chủ sở hữu yêu cầu: gỡ các dòng demand không có trong file, xóa due date
  hay Job Number bằng một ô trống, thêm dòng vào Work Order Released bằng import, đổi một trường header của Work Order từ
  file, và digest xác nhận theo từng Work Order (vốn cần một cách truyền có trạng thái).
- Lưu giữ có thời hạn hay purge các dòng audit mà một lần import file ghi: PROJECT_PROFILE §28 giới hạn việc lưu giữ cho
  PartMovement và việc import không giữ bản ghi import nào; các quyết định archival của Phase 16 mang mục này với mặc định
  "hoãn".
- Một giới hạn độ dài dùng chung cho Work Order Number, Part Number và Job Number khi tạo và sửa Work Order bằng tay:
  import từ chối ô văn bản dài hơn 200 ký tự, trong khi đường nhập tay chấp nhận mọi độ dài, nên một giá trị vượt giới
  hạn mục index của database (khoảng 2,7 KB văn bản khó nén) thất bại thành một lỗi database không được xử lý (HTTP 500,
  rollback, không ghi gì) thay vì 422. Chủ sở hữu quyết định giới hạn dùng chung trước khi áp một quy tắc cho cả hai
  đường.
- đồng bộ scan offline (không thuộc MVP; thao tác ghi sản xuất vẫn bị chặn khi mất kết nối; một quyết định mở trong
  PROJECT_PROFILE §32)
- kết nối push qua WebSocket/SSE (phát hiện health theo sự kiện + polling là cơ chế đã duyệt)
- phân tích nâng cao
- tự động hóa mang tính suy đoán
- các tính năng ERP và hệ thống điều hành sản xuất (MES) diện rộng
- nhập priority khi tạo Work Order (PROJECT_PROFILE §21 Work Orders mục 5, GUI_DESIGN §11.2 "priority when
  applicable") — được chủ sở hữu hoãn ngày 2026-10-04; Hot list (Management → Priority) vẫn là nơi duy nhất đặt priority;
  khi được làm, sẽ là một điểm vào "thêm vào Hot list" qua cùng lệnh, nối vào cuối (không gõ hạng), vì một hạng gõ tay lúc
  tạo sẽ là bên ghi thứ hai của danh sách liền mạch
- chính sách hành vi scan và các thiết lập ứng dụng chung ngoài cảnh báo Due Soon và thiết lập đăng nhập User — chưa có
  thiết lập chuẩn nào được định nghĩa (PROJECT_PROFILE §20 và §21 và GUI_DESIGN §9 chỉ nêu tên các mục); Administration
  hiển thị chúng là chưa có cho tới khi chủ sở hữu định nghĩa nội dung (mặc định của chủ sở hữu, 2026-10-04)
- các lệnh `Quantity adjustment` và hiệu chỉnh lịch sử của Tracking (quyết định của chủ sở hữu ngày 2026-10-06)
- các permission key chưa route nào cần, cùng chủ sở hữu của chúng: `Manage scan behavior` (cùng mục Scan behavior, khi
  có một mục như vậy trong Administration); `Resolve exceptional production situations` (cho tới khi PROJECT_PROFILE
  định nghĩa một lệnh); `Export and print reports` (cho tới khi có một bề mặt báo cáo được định nghĩa);
  `Perform quantity corrections` và `Perform authorized historical corrections` (cho tới khi có các lệnh điều chỉnh số
  lượng và hiệu chỉnh lịch sử). Administration đánh dấu chúng là chưa cấp quyền gì.
- đăng nhập qua thư mục hoặc đăng nhập một lần (SSO), xác thực đa yếu tố và tự đặt lại mật khẩu cho User của ứng dụng
  (không có yêu cầu chuẩn; bảng thông tin đăng nhập riêng giữ cho chúng có thể thêm vào mà không phá gì)
- Các việc còn lại của Phase 13 không có yêu cầu chuẩn:
  - tính an toàn khi thử lại của `POST /api/areas`: tên Area không duy nhất và không có khóa idempotency, nên một bản
    sửa cần một khóa idempotency, một thay đổi API hoặc một quy tắc tên Area chuẩn;
  - một bề mặt hiển thị Worker được ghi trên dòng phân bổ (`allocated_by_worker_id`; GUI_DESIGN §7.2 không định nghĩa
    trường Worker ở đó);
  - một bề mặt cho lịch sử Worker Session và `scan_session_id`;
  - gỡ bản preview Scan Station mock chỉ dành cho phát triển (`ScanStationMockView.tsx`, `?preview=mock` và các dataset
    mock của nó; không bao giờ có trong bản build production);
  - cho thấy trong Tracking liệu một cổng badge đã xác nhận một thao tác hay không;
  - danh mục lý do Undo hoặc phạm vi theo Area;
  - gợi ý Machine ưu tiên của snapshot khi giao Machine;
  - tên Planned Route duy nhất (cần một quy tắc chuẩn trước);
  - snapshot tên Route Template tại thời điểm release, để PN Tracking hiển thị tên tại lúc release;
  - cho phép Area ngưng hoạt động trên các bước của Planned Route (một thay đổi quy tắc ở Application và UI, không cần
    sửa dữ liệu; hiện nay một Area ngưng hoạt động bị từ chối khi lưu và hiển thị `{name} (unavailable)` trong trình
    sửa).

## Lịch sử thay đổi

| Ngày | Commit | Thay đổi |
| --- | --- | --- |
| 2026-11-12 | uncommitted | Release flow của Phase 16: mọi trang đã cũ tự tải lại khi rảnh, không chỉ Scan Station và Production Board, và các kiểm tra trên trình duyệt sau một lần release trên NAS bao gồm việc này. |
| 2026-11-12 | `a7949e3` | Hạ độ dài tối thiểu của mật khẩu mới từ 12 xuống 6 ký tự (quyết định của chủ sở hữu). |
| 2026-11-10 | `e0e5056` | Bước go-live trỏ tới hướng dẫn cài đặt NAS đã viết lại (§1 đến §10). |
| 2026-11-09 | `398c617` | Viết lại thành trạng thái hiện tại cho người đọc mới; pilot được thay bằng go-live trực tiếp (quyết định của chủ sở hữu). |
| 2026-11-09 | `b405c6e` | Ghi nhận quyết định go-live trực tiếp của chủ sở hữu, không có pilot và không có release gate. |
| 2026-10-09 | `03bd90c`, `4271f61` | Giám sát của Phase 16: log có cấu trúc, request id, lệnh status, kiểm tra máy chủ và reconcile theo lịch. |
| 2026-10-08 | `a330383`, `92727f6` | Backup, diễn tập restore và rollback bằng restore của Phase 16. |
| 2026-10-08 | `1116b07` | Database role và grant của Phase 16. |
| 2026-10-08 | `b820e7a` | Quy trình release, định danh release, readiness và write gate của Phase 16. |
| 2026-10-08 | `d13e303`, `d7e86a9`, `25f63bf` | Image production và Compose stack của Phase 16. |
| 2026-10-07 đến 2026-10-08 | `e1dee40` đến `e13574f` | Import file Phase 15 cho Work Order mới và hiện có; đóng Phase 15. |
| 2026-10-07 | `3fb66df`, `4313863` | Lệnh đối soát của Phase 16; chấp nhận URL database có percent-encoding. |
| 2026-10-06 đến 2026-10-07 | `702ab1c` đến `eb7171c` | Phase 14: đăng nhập, cưỡng chế, thiết bị trạm, hiệu chỉnh, audit trail và theme User; đóng Phase 14. |
| 2026-10-04 đến 2026-10-06 | `c12728d` đến `7581844` | Các slice và việc đóng Phase 13. |
| 2026-10-04 | `80f7925`, `b9785d2`, `9f466fa` | Phase 12 Priority Management, đóng theo các quyết định của chủ sở hữu. |
| 2026-09-13 đến 2026-09-14 | `1ce5e19` đến `8b3b089` | Audit Phase 11, phân rã theo PN của Machine và giám sát thời lượng dự kiến; đóng Phase 11. |
| 2026-09-02 đến 2026-09-10 | `5f006a0` đến `de88283` | Production Board, Area Board và PN Tracking của Phase 11. |
| 2026-09-03 đến 2026-09-04 | `bc07d3a` đến `f10d8bd` | Audit độ bao phủ gán chủ cho mọi hành vi mục tiêu; Phase 10.5 Receive Quantity. |
| 2026-09-01 đến 2026-09-02 | `100d59d` đến `94f3202` | Phase 10 Stockroom, phân bổ và lịch sử đã hoàn tất. |
| 2026-08-31 đến 2026-09-01 | `1d52779` đến `7e60dcb` | Phase 9 Undo, Repair, Scrap và thêm số lượng. |
| 2026-08-28 đến 2026-08-31 | `60ac1a6` đến `8ee6874` | Phase 8 tách và gộp. |
| 2026-08-25 đến 2026-08-28 | `a567b02` đến `485775c` | Phase 5 đến 7: chuyển, giao Machine và gia công trực tiếp. |
| 2026-08-18 đến 2026-08-24 | `d6c735f` đến `1fe73e9` | Phase 3, 3.5 và 4: nền tảng domain, cài đặt môi trường, Work Order và release. |
| 2026-07-23 đến 2026-08-17 | `1a3ebbf` đến `ecc1ef4` | Phase 1 và 2 và các vòng thiết kế GUI. |
| 2026-07-22 | `483ef0c` | Phiên bản đầu tiên của roadmap. |
