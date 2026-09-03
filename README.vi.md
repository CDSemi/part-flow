# PartFlow

> **Ngôn ngữ:** Đây là bản tiếng Việt của [`README.md`](./README.md).
> File tiếng Anh là nguồn chuẩn (source of truth). Bản dịch này dùng baseline
> upstream commit `6fdf20e` (Phase 10 audit r3).
>
> Xem [mục lục tài liệu tiếng Việt](./docs/README.vi.md) và
> [hướng dẫn triển khai](./docs/DEPLOYMENT.vi.md).

PartFlow là hệ thống nội bộ theo dõi quá trình sản xuất, dùng barcode để ghi
nhận việc di chuyển số lượng chi tiết qua nhà máy.

## Trạng thái hiện tại

Repository hiện có nền tảng từ Phase 1 đến Phase 10 triển khai end to end, và
Production Board của Phase 11:

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
- **Phase 11 (Production Board):** read model board toàn Department derive từ
  projection vị trí hiện tại và Movement history (`GET /api/production-board`:
  phân bổ theo Area / Machine / External activity kèm timestamp vào vị trí,
  stocked và scrapped, Work Order / Job Number context, Hot rank, theo canonical
  board order; `department_id` chọn Department, bỏ trống chỉ khi có một
  Department active) và view board thật trên đó: tự refresh định kỳ, giữ rows
  hoàn chỉnh cuối cùng kèm `Feed stale — reconnecting` khi refresh hoặc kết nối
  lỗi, giữ nguyên presentation đã duyệt (kiosk, pagination + rotation, auto
  scale, điều hướng tay). Area Board và Tracking (phần còn lại của Phase 11)
  vẫn là mock view chỉ development.

Các phase tiếp theo, gồm authentication/authorization và production deployment,
chưa hoàn tất. Vì vậy Compose hiện tại là môi trường phát triển; xem
[`docs/DEPLOYMENT.vi.md`](./docs/DEPLOYMENT.vi.md) trước khi đưa dữ liệu thật vào.

## Thành phần chính

- `frontend/` — Vite + React + TypeScript. Các view có backend (Administration
  Phase 3.5, Machines, Work Orders và Completed Work Orders, Scan Station,
  Production Board) đã kết nối API thật; view còn lại dùng mock chỉ trong development và bị chặn
  khỏi production bundle.
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
| `/management/tracking` | Management → PN Tracking |
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
- `src/app/` — router, theme provider, connectivity provider, registry view thật
  và dev-only, preview state.
- `src/api/` — typed client và mapping `snake_case` ↔ `camelCase`; production-safe,
  không import `src/mocks/`.
- `src/mocks/` — dữ liệu/view mẫu chỉ được lazy-load khi `import.meta.env.DEV`.
- `src/views/<view>/` — mỗi GUI view một thư mục.
- `src/components/` — component dùng chung, gồm Area/Machine monitoring.

Production build quét sentinel và test module graph để bảo đảm mock không lọt vào
bundle. Những view chưa có backend hiển thị thông báo “not connected to a
production data source yet” thay vì giả vờ hoạt động.

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
- unit test normalization PN;
- integration test dùng PostgreSQL thật cho migration/schema, environment API,
  Machine lifecycle, Work Order intake/release, transfer, Machine/direct Area
  processing, split/merge lineage, correction/Undo và Stockroom/allocation.

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
  src/app/         router, theme, connectivity, real/dev view registry
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
