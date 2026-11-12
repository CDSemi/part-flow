# PartFlow

> **Ngôn ngữ:** Đây là bản tiếng Việt của [`README.md`](./README.md). Bản tiếng
> Anh là nguồn chuẩn (source of truth); khi hai bản khác nhau, bản tiếng Anh có
> hiệu lực. Đồng bộ với bản tiếng Anh tại commit `398c617` cộng các thay đổi chưa commit ngày 2026-11-10.
>
> Xem [mục lục tài liệu tiếng Việt](./docs/README.vi.md) và
> [hướng dẫn triển khai](./docs/DEPLOYMENT.vi.md).

PartFlow là hệ thống nội bộ của nhà máy để theo dõi số lượng sản xuất khi chúng
di chuyển qua xưởng. Operator quét barcode tại các Scan Station, và mỗi lần quét
làm thay đổi vị trí hoặc trạng thái của một lượng hàng đều được ghi thành một
Movement bất biến. Người quản lý thấy theo thời gian thực mỗi Part Number (mã chi
tiết, PN) đang ở đâu, mỗi Work Order (lệnh sản xuất, WO) còn bao nhiêu chưa xong,
và cái gì đang trễ.

README này giới thiệu PartFlow cho người mới và là hướng dẫn dành cho lập trình
viên của repository: cách cài đặt, chạy, kiểm thử và kiểm tra ứng dụng trên máy
trạm. README không định nghĩa quy tắc nghiệp vụ hay giao diện người dùng; những
điều đó nằm trong các tài liệu chuẩn liệt kê ở mục
[Đọc tiếp ở đâu](#đọc-tiếp-ở-đâu).

## Ai dùng PartFlow

- **Operator** làm việc tại các Scan Station trong xưởng. Scan Station là một
  trình duyệt trên máy trạm quét barcode mà administrator đã đăng ký (enroll) cho
  một station, và mỗi station gắn với một Area của nhà máy.
- **Người quản lý sản xuất và người lập kế hoạch** dùng các màn hình Management để
  nhập Work Order, đặt ưu tiên, theo dõi Part Number và sửa dữ liệu sản xuất đã
  ghi.
- **Administrator** cấu hình nhà máy (Department, Area, Operation, Machine, Scan
  Station, Worker, người dùng và vai trò).
- **Mọi người trong xưởng** có thể xem Production Board trên màn hình treo tường.

## PartFlow làm được gì hiện nay

Mọi màn hình của giao diện đã duyệt đều đã được hiện thực trên backend thật. Theo
cách nói của người dùng:

- **Work Order.** Nhập một Work Order và các dòng demand của nó (Part Number nào,
  bao nhiêu, khi nào), rồi release số lượng sản xuất như một hành động riêng,
  tường minh, một lần hoặc nhiều phần. Lưu demand không bao giờ tạo ra số lượng
  sản xuất. Work Order cũng có thể được nhập từ file CSV hoặc Excel (`.xlsx`):
  file được kiểm tra trước, và thay đổi đối với các Work Order Open hoặc Released
  đã có chỉ được áp dụng sau khi người dùng gõ xác nhận. Work Order đã hoàn thành
  có lịch sử chỉ đọc với tìm kiếm và bộ lọc.
- **Scan Station.** Quét barcode Part Number, Machine hoặc Area và ghi lại điều
  xảy ra với lượng hàng: chuyển (transfer) nó vào Area của station, gán vào một
  Machine, trả về hàng đợi (`QUEUE`), đánh dấu xong (`DONE`), hoặc hoàn tất xử lý
  trong một Area không có Machine. Mọi hành động đều nhận một phần số lượng, và
  các lượng hàng của cùng một Part Number có thể được gộp một cách tường minh
  (`Combine quantities`). Một Part Number không có demand đang mở có thể được
  nhận vào bằng `Receive Quantity`.
- **Sửa sai tại station.** Hủy hàng (Scrap), thêm số lượng (`Add more quantity`),
  gửi hàng về sửa (`Return quantity for repair`), và Undo một lần quét gần đây.
  Mọi lần sửa sai đều được ghi lại; không có gì trong lịch sử Movement bị ghi đè.
- **Stockroom.** Nhận hàng thành phẩm vào Stockroom (`Receive into Stockroom`) và
  phân bổ (allocate) nó cho demand của Work Order theo thứ tự chuẩn. Một Work
  Order hoàn thành khi demand của nó được phân bổ đủ. Người quản lý có thể phân
  bổ tồn kho sau, đảo ngược một lần phân bổ, và, kèm lý do, sửa một lần phân bổ
  vượt quá demand còn lại.
- **Giám sát.** Production Board hiển thị mọi Part Number đang sản xuất trong một
  Department, tự làm mới và có chế độ kiosk cho màn hình treo tường. Area Board
  hiển thị mọi Area và những gì đang chờ, đang ở trên từng Machine và đã xong. PN
  Tracking hiển thị demand, số lượng hiện tại, lịch sử và nhật ký kiểm toán (audit
  trail) của một Part Number, và là nơi người quản lý điều chỉnh các bước còn lại
  của một route đã gán.
- **Ưu tiên.** Người quản lý duy trì một Hot list xếp hạng các dòng demand của
  Work Order; thứ hạng này quyết định thứ tự trên các board và thứ tự phân bổ
  tồn kho.
- **Dữ liệu gốc (master data).** Machine (với Asset Tag tự cấp, bảo trì và cho
  nghỉ hưu), Part Number (thông tin chi tiết, hình ảnh và nhãn barcode) và Planned
  Route (chuỗi Area và Operation dùng lại được).
- **Worker và đăng nhập.** Worker tự nhận diện tại Scan Station bằng thẻ (badge),
  và một Area có thể yêu cầu quét badge để xác nhận một số hành động. Người dùng
  ứng dụng đăng nhập bằng tên đăng nhập và mật khẩu, và mỗi người dùng được làm gì
  tùy theo quyền của vai trò (role) của họ.
- **Administration.** Cấu hình nhà máy, thiết bị Scan Station, Worker, người dùng,
  vai trò và quyền, thiết lập đăng nhập, thiết lập hiển thị Production Board và
  chính sách Due Soon.

Chưa có: lưu trữ (archival) và xóa vĩnh viễn (purge) lịch sử Movement cũ (thời hạn
lưu giữ có thể được lưu nhưng chưa có gì được lưu trữ hay xóa), và phần thiết lập
Scan behavior trong Administration. Trạng thái hiện thực đầy đủ nằm trong
[`docs/IMPLEMENTATION_ROADMAP.vi.md`](./docs/IMPLEMENTATION_ROADMAP.vi.md).

## Đọc tiếp ở đâu

- [`docs/README.vi.md`](./docs/README.vi.md) — mục lục tài liệu: mỗi tài liệu dùng
  để làm gì.
- [`docs/PROJECT_PROFILE.vi.md`](./docs/PROJECT_PROFILE.vi.md) — đặc tả có thẩm
  quyền: thuật ngữ miền, quy tắc nghiệp vụ và quy trình.
- [`docs/GUI_DESIGN.vi.md`](./docs/GUI_DESIGN.vi.md) — giao diện người dùng đã
  duyệt, với
  [`docs/mockups/partflow-gui-mockup-v18.html`](./docs/mockups/partflow-gui-mockup-v18.html)
  làm tham chiếu trực quan.
- [`docs/IMPLEMENTATION_ROADMAP.vi.md`](./docs/IMPLEMENTATION_ROADMAP.vi.md) —
  những gì đã hiện thực, theo thứ tự nào, và các giới hạn tạm thời.
- [`docs/DEPLOYMENT.vi.md`](./docs/DEPLOYMENT.vi.md) — môi trường production:
  bắt đầu từ §1 của tài liệu này, nơi chỉ rõ cần theo tài liệu nào cho từng việc
  (hướng dẫn cài từng bước trên Synology NAS của công ty là
  [`docs/deployment/SYNOLOGY_NAS.vi.md`](./docs/deployment/SYNOLOGY_NAS.vi.md)).
  Theo quyết định của chủ dự án ngày 2026-11-09, PartFlow đi vào sử dụng thật ngay
  khi production stack được cài đặt: không có giai đoạn chạy thử (pilot) và không
  có danh sách điều kiện chặn phát hành (release gate), và các vấn đề được sửa
  trong các bản phát hành sau.

Phần còn lại của README này là hướng dẫn cho lập trình viên.

## Route và cấu trúc frontend

Route dùng URL có nghĩa, nút back và forward của trình duyệt hoạt động, và một
route không tồn tại hiển thị trạng thái not-found ở cấp ứng dụng:

| URL | View |
|---|---|
| `/scan-station` | Scan Station — Station Selector (root `/` chuyển hướng về đây; không bao giờ tự chuyển vào một station) |
| `/scan-station/:stationId` | Một Scan Station ở chế độ standard (ví dụ `/scan-station/LATHE-ST-01`); Station ID không tồn tại hoặc không active hiển thị lỗi tường minh |
| `/scan-station/:stationId/production` | Cùng Scan Station đó ở chế độ production — thanh điều hướng trên cùng của ứng dụng bị ẩn để operator ở lại station (chỉ là cách trình bày, không phải ranh giới bảo mật) |
| `/production-board` | Production Board (màn hình lớn, chỉ đọc) |
| `/production-board/kiosk` | Production Board ở chế độ kiosk — thanh điều hướng trên cùng bị ẩn và board tự vẽ header cho màn hình treo tường (chỉ là cách trình bày) |
| `/management/area-board` | Management → Area Board (tổng quan All Areas + chi tiết từng Area) |
| `/management/machines` | Management → Machines (vòng đời và bảo trì Machine — dữ liệu gốc sản xuất theo quyền) |
| `/management/tracking` | Management → PN Tracking (danh sách theo PN + lớp chi tiết dạng modeless) |
| `/management/work-orders` | Management → Work Orders |
| `/management/work-orders/completed` | Management → Work Orders → Completed Work Orders (lịch sử chỉ đọc) |
| `/management/planned-routes` | Management → Planned Routes (định nghĩa route dùng lại được — dữ liệu gốc sản xuất theo quyền) |
| `/management/part-numbers` | Management → Part Numbers (metadata PartNumber master và nhãn barcode — dữ liệu gốc sản xuất theo quyền) |
| `/management/priority` | Management → Priority (xếp hạng Hot WO Demand) |
| `/administration` | Administration |

`/management` mở sub view dùng gần nhất trong phiên hiện tại (Area Board ở lần mở
đầu tiên) khi người dùng đã đăng nhập được phép mở nó, nếu không thì mở sub view
đầu tiên mà người dùng được phép mở. Một deep link không bao giờ bị chuyển hướng.
Routing là một router nhỏ dựa trên history (`src/app/router-core.ts` bảng route và
phân giải, `src/app/router-context.ts` Context và `useRouter`,
`src/app/router-provider.tsx` trạng thái history và chuyển hướng,
`src/app/link.tsx` `Link` phía client); không dùng thư viện routing nào.

Cấu trúc frontend:

- `src/styles/` — design token ngữ nghĩa (`tokens.css`; `body.dark` /
  `body.light` cung cấp giá trị) và các primitive dùng chung (`global.css`). CSS
  của component chỉ dùng token ngữ nghĩa.
- `src/app/` — hạ tầng của shell: router; theme provider (mặc định Dark; tùy chọn
  của User đã đăng nhập áp dụng trên mọi route, nếu không thì tùy chọn đã lưu của
  Scan Station trên các route của station, và ở nơi khác lựa chọn chỉ kéo dài
  trong phiên); connectivity provider với phát hiện nhanh (sự kiện online và
  offline của trình duyệt, gọi `/api/health` khoảng mỗi giây với timeout nhỏ hơn
  chu kỳ thăm dò, kiểm tra lại khi tab được focus hoặc hiển thị, và nút Retry tường
  minh; không có ghi lạc quan (optimistic write) — một lần ghi chỉ được hiển thị là
  đã ghi sau khi server xác nhận); session provider; registry các view thật
  (`real-views.ts`); và phần xem trước trạng thái dành cho development
  (`view-state.ts`).
- `src/api/` — lớp API client dùng chung của các view thật: một lõi fetch có kiểu,
  mỏng, chuyển lỗi `{"detail": …}` của backend thành thông báo cho người dùng; mỗi
  mảng backend một module (ví dụ `scan-station.ts`, `production-board.ts`,
  `area-board.ts`, `hot-list.ts`); MỘT mô hình giám sát Area dùng chung mà cả Scan
  Station lẫn Area Board hiển thị (`area-inventory.ts`); ánh xạ snake_case ↔
  camelCase; các hàm hỗ trợ thời lượng ISO 8601; và hook `useApiData` cho loading,
  lỗi và tải lại. Lớp này an toàn cho production và không bao giờ import từ
  `src/mocks/`.
- `src/mocks/` — các bộ dữ liệu mock chỉ dành cho development. Mọi view đã duyệt
  đều là view thật, nên nơi duy nhất còn đọc chúng là phần xem trước Scan Station
  chỉ dành cho development (`ScanStationMockView.tsx`, mở bằng `?preview=mock` trên
  một route Scan Station, sau `import.meta.env.DEV`). Không có gì trong
  `src/mocks` mã hóa quy tắc nghiệp vụ production hay được ghi vào backend, và bản
  build production loại hoàn toàn các bộ dữ liệu này. Các view thật
  (`src/app/real-views.ts`) có mặt trong mọi bản build và làm việc với bề mặt
  `/api` thật.
- Ranh giới production được kiểm tra hai lần. `npm run build` quét các asset được
  sinh ra để tìm các giá trị đánh dấu mock đã biết
  (`scripts/check-production-boundary.mjs`), và `src/production-boundary.test.ts`
  duyệt đồ thị module production một cách bắc cầu từ `src/main.tsx` và báo lỗi nếu
  một module production import từ `src/mocks/`. Phép duyệt dừng ở các lazy import
  được bảo vệ bởi `import.meta.env.DEV`: phần xem trước mock Scan Station, và
  module badge demo chỉ dành cho development của modal đăng nhập Worker
  (`scan-station-dev-badges.tsx`, chỉ được import bởi
  `scan-station-dev-badges-slot.tsx`, mà modal đăng nhập và cổng xác nhận badge
  dùng chung). Một dynamic import production bình thường vẫn được duyệt theo. Các
  kiểu view-model dùng chung nằm trong `src/views/view-models.ts` (chỉ có kiểu, an
  toàn cho production).
- `src/views/<view>/` — mỗi view GUI một thư mục. `src/views/scan-station/barcode.ts`
  chứa phần phân tích barcode `PF:` xác định và chuẩn hóa PN (barcode PN mang chính
  PN ở dạng chuẩn, viết hoa, không khoảng trắng — `PF:PN:<part-number>`).
- `src/components/` — các phần thực sự dùng chung (chấm Area, chip Hot và Type,
  khối trạng thái view, dialog, bàn phím số lượng, và các component giám sát Area
  và Machine dùng chung cho cả Scan Station lẫn phần chi tiết của Area Board).

### Xem trước trạng thái UI (chỉ development)

Thêm `?state=…` vào URL của một view trong bản build development để ép một trạng
thái xác định (mỗi view hiện thực những trạng thái xem trước có ý nghĩa với nó):

- `?state=loading` — trạng thái loading dạng skeleton
- `?state=empty` — trạng thái rỗng
- `?state=error` — trạng thái lỗi
- `?state=long` — bộ dữ liệu dài (PN quá dài, nhiều dòng) trên các view nhiều dữ
  liệu có định nghĩa fixture dữ liệu dài xác định; view không có fixture đó (ví dụ
  Administration, Priority) hiển thị dữ liệu mẫu bình thường

Ví dụ: `http://localhost:5173/management/tracking?state=long`. Việc ép trạng thái
này được bảo vệ bởi `import.meta.env.DEV` và không tồn tại trong bản build
production. Trạng thái mất kết nối là thật: dừng backend (hoặc để health check thất
bại) và shell hiển thị banner OFFLINE cố định với nút Retry, và các điều khiển ghi
bị vô hiệu hóa.

## Yêu cầu

- Docker với Docker Compose v2 (`docker compose`)
- Để phát triển ngoài Docker (tùy chọn): Node.js 24+, Python 3.12+, [uv](https://docs.astral.sh/uv/)

## Thiết lập môi trường

1. Sao chép file môi trường mẫu:

   ```bash
   cp .env.example .env
   ```

2. Điều chỉnh giá trị trong `.env` nếu cần. Đây là thông tin đăng nhập chỉ dành
   cho development; file `.env` thật bị git bỏ qua và không bao giờ được chứa bí
   mật dùng chung hay bí mật production. `SITE_TIMEZONE` (tên múi giờ IANA, mặc
   định `UTC`) là lịch của nhà máy: backend đổi một thời điểm hoàn thành thành
   ngày hoàn thành (done date) theo múi giờ này, dùng chung cho Done range, kết quả
   so với hạn (due outcome) và ngày hiển thị của lịch sử đã hoàn thành. Đặt nó bằng
   múi giờ của nhà máy (ví dụ `America/Los_Angeles`) để "đúng hạn" và "trễ" theo
   ngày của nhà máy, không bao giờ theo ngày của trình duyệt.

Các file lock phụ thuộc (`backend/uv.lock`, `frontend/package-lock.json`) đã được
commit, và các bản build Docker cài đặt đúng theo chúng (`uv sync --frozen`,
`npm ci`). Một checkout sạch không cần bước khởi tạo nào khác.

## Khởi động toàn bộ stack

```bash
docker compose up --build
```

- Frontend: <http://localhost:5173>
- Backend API: <http://localhost:8000>
- Health endpoint: <http://localhost:8000/api/health> (cũng được proxy tại <http://localhost:5173/api/health>);
  chỉ liveness (không cần database): <http://localhost:8000/api/health/live>

Health check của container dùng route liveness, nên stack khởi động ở trạng thái
healthy trước khi chạy `alembic upgrade head`. `/api/health` là kiểm tra readiness
và trả 503 cho đến khi database ở migration head (xem
[Migration database (Alembic)](#migration-database-alembic)).

Frontend phục vụ application shell với mọi view đã duyệt, tất cả đều đọc và ghi
backend thật. Chip kết nối trên thanh điều hướng trên cùng hiển thị trạng thái
thật của backend: CONNECTING…, ONLINE, hoặc OFFLINE kèm banner cố định và nút
Retry.

Dừng stack bằng `Ctrl+C`, rồi:

```bash
docker compose down
```

(`docker compose down -v` còn xóa thêm volume dữ liệu PostgreSQL — thường không cần.)

Sau khi thay đổi phụ thuộc của backend hoặc frontend (`pyproject.toml`/`uv.lock`,
`package.json`/`package-lock.json`), kể cả sau khi pull một thay đổi đã đổi chúng,
hãy build lại và làm mới các anonymous volume chứa phụ thuộc để nội dung `.venv`
hoặc `node_modules` cũ không che khuất image vừa build:

```bash
docker compose up --build -V
```

## Migration database (Alembic)

Áp dụng các migration (từ baseline no-op của repository foundation đến head hiện
tại, `0032_phase14_route_adjusted`):

```bash
docker compose exec backend uv run alembic upgrade head
```

Cho đến khi `alembic upgrade head` đã chạy, backend development trả `/api/health`
với 503 (`schema: mismatch`) và từ chối mọi lần ghi với 503 `not_ready`; các lần
đọc và `/api/health/live` vẫn hoạt động.

Upgrade lên `0030_phase14_station_devices` bị từ chối khi không có role nào tên
Operator, vì Scan Station dùng quyền của role đó. Trên database như vậy, hãy đổi
tên role có quyền mà Scan Station nên dùng thành Operator, chạy upgrade, rồi đổi
tên lại.

### Tạo, reset và kiểm tra development database

Image `postgres` áp dụng `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` từ
`.env` **chỉ một lần — khi volume dữ liệu được khởi tạo lần đầu**. Đổi `.env` về
sau không đổi các role bên trong volume đã có, nên một lệnh `psql -U …` hoặc data
source của IDE không khớp sẽ thất bại với `FATAL: role "…" does not exist`. Luôn
kết nối bằng user mà `.env` của bạn thực sự khai báo, và reset volume khi thông tin
đăng nhập đã đổi:

```bash
# connect with the user from YOUR .env (defaults are in .env.example)
docker compose exec db psql -U <POSTGRES_USER> -d partflow

# create/update the development schema
docker compose up -d db backend
docker compose exec backend uv run alembic upgrade head

# verify the schema
docker compose exec db psql -U <POSTGRES_USER> -d partflow -c "\dt"
docker compose exec db psql -U <POSTGRES_USER> -d partflow -c "\d part_movements"

# full reset — DESTRUCTIVE: deletes the postgres_data volume and all
# development data (everything is recreated by `alembic upgrade head`)
docker compose down -v
docker compose up -d db backend
docker compose exec backend uv run alembic upgrade head
```

Thêm hai lưu ý:

- Áp dụng lại một migration đã bị sửa **trước khi nó từng được commit hoặc chia
  sẻ** cần `alembic downgrade base` + `alembic upgrade head` (hoặc reset toàn bộ ở
  trên). Chỉ chạy việc đó trên development database dùng một lần. Nhiều downgrade
  từ chối, không ghi gì, khi database còn giữ dữ liệu mà revision của chúng đưa vào
  (ví dụ người dùng, Worker Session hoặc route adjustment đã ghi); khi đó hãy dùng
  reset toàn bộ. Một migration đã được commit hoặc chia sẻ không bao giờ bị sửa tại
  chỗ — hãy viết một revision mới.
- Dấu `$` trong `POSTGRES_PASSWORD` có thể xung đột với phép nội suy biến của
  Docker Compose trong `.env`; hãy escape thành `$$` nếu Compose cảnh báo một biến
  chưa đặt.

## First-run setup và account recovery

Một database mới chưa có Administrator. Khi chưa có ai, backend ghi một setup token
dùng một lần vào log khi khởi động, và nút **Set up PartFlow** trên thanh điều
hướng trên cùng mở dialog first-run:

```bash
docker compose logs backend | grep "Setup token"
```

Mở PartFlow, chọn **Set up PartFlow**, dán token và tạo Administrator đầu tiên.
Lần tạo đầu tiên đóng setup vĩnh viễn và token khi đó bị hủy; khởi động lại backend
chỉ thông báo token mới khi vẫn chưa có Administrator. Token là lớp bảo vệ duy nhất
của first-run setup, nên hãy hoàn tất nó trước khi bất kỳ ai khác truy cập được dịch
vụ và hạn chế quyền xem log cho đến lúc đó. Mỗi tiến trình backend thông báo token
riêng của nó.

Trong production stack (`compose.production.yaml`, xem
[Production stack](#production-stack)) các bước này dùng dạng lệnh production.
Khởi động backend với một worker để chỉ có một token, rồi khôi phục số worker đã
cấu hình khi setup xong:

```bash
PF="docker compose -f compose.production.yaml --env-file .env.production"
PARTFLOW_BACKEND_WORKERS=1 $PF up -d backend
$PF logs backend | grep "Setup token"
$PF up -d backend
```

Các lệnh khôi phục dưới đây chạy theo cùng cách trong production, ví dụ
`$PF run --rm --no-deps backend python -m app.cli reset-password --login-name <name>`
(image production không có `uv run`).

### Đặt lại mật khẩu bị quên

Nếu Administrator duy nhất quên mật khẩu, hãy đặt lại từ host:

```bash
docker compose exec backend uv run python -m app.cli reset-password --login-name <name>
```

Mật khẩu mới được gõ tại dấu nhắc, không bao giờ trên dòng lệnh. Lệnh chỉ hoạt động
cho người dùng đã có mật khẩu và chỉ khi đang có một Administrator; nó không bao
giờ tạo Administrator. Lệnh đặt một mật khẩu tạm, kết thúc các phiên đăng nhập của
người dùng và gỡ khóa. Nếu kết nối database thất bại trong lúc lưu, lệnh báo kết
quả không xác định (exit code 2); hãy chạy lại, vì dù thế nào nó cũng đặt lại mật
khẩu.

### Khôi phục quyền quản lý correction permission

Không ai có thể cấp quyền quản lý correction permission
(`MANAGE_CORRECTION_PERMISSIONS`) khi không còn người dùng active có mật khẩu nào
giữ nó. Khi đó backend ghi một cảnh báo lúc khởi động. Để xem ai giữ hai khóa quản
lý quyền, hãy đếm trong database shell
(`docker compose exec db psql -U <POSTGRES_USER> -d partflow`) số người dùng active
có mật khẩu mà role của họ giữ từng khóa:

```sql
SELECT rp.permission, count(*) AS holders
FROM users u
JOIN user_credentials c ON c.user_id = u.id
JOIN role_permissions rp ON rp.role_id = u.role_id
WHERE u.is_active
  AND rp.permission IN ('MANAGE_USERS_AND_ROLES', 'MANAGE_CORRECTION_PERMISSIONS')
GROUP BY rp.permission;
```

Thiếu một dòng nghĩa là không có ai giữ. Cả hai số đếm nên ít nhất là 1, hoặc
`MANAGE_USERS_AND_ROLES` nên vắng mặt (khi đó first-run setup đang mở). Nếu
`MANAGE_CORRECTION_PERMISSIONS` không có ai giữ trong khi `MANAGE_USERS_AND_ROLES`
có, hãy khôi phục nó từ host:

```bash
docker compose exec backend uv run python -m app.cli restore-correction-permission-management --role-name <role>
```

Lệnh chỉ hoạt động khi không có người dùng active có mật khẩu nào được quản lý
correction permission, và role được nêu tên phải có một thành viên như vậy. Lệnh
cấp quyền cho role đó, thêm một audit row và không đổi gì khác. Mỗi lần từ chối (đã
có người giữ, không có role đó, không có người dùng active có mật khẩu trong role)
không ghi gì và thoát với mã 1.

### Session cookie bảo mật

Backend đọc `SESSION_COOKIE_SECURE` (mặc định `false`, vì development stack chạy
trên HTTP thường). Đặt nó thành `true` trong môi trường backend khi PartFlow được
phục vụ qua HTTPS, để session cookie chỉ được gửi trên kết nối bảo mật;
`compose.yaml` development không truyền biến này, còn production stack cố định nó
là `true`.

## Enroll thiết bị Scan Station

Một Scan Station chỉ hoạt động từ trình duyệt mà administrator đã enroll cho
station đó; các station development cũng được enroll theo cùng cách.

1. Đăng nhập bằng người dùng được phép quản lý Scan Station.
2. Mở Administration → Scan Stations → `Devices…` của station và chọn
   `Enroll device…`.
3. Nhập mã dùng một lần (`XXXXX-XXXXX`, hiệu lực 15 phút, dùng một lần, chỉ hiển
   thị một lần) trên station trong thời gian đó. Trình duyệt của station đổi mã lấy
   một device token, giữ token trong bộ nhớ trình duyệt và gửi nó trong
   `X-PartFlow-Station-Device` với mọi request.

Trình duyệt bị xóa bộ nhớ phải được enroll lại. `Re-enroll…` thay một thiết bị khi
mã mới của nó được dùng, và `Revoke…` chặn ngay một thiết bị bị mất. Khi role áp
dụng tại Scan Station giữ một correction permission (ban đầu là Undo recent
eligible scans), việc enroll còn cần quyền quản lý correction permission. Một
station đã enroll được làm gì tùy theo quyền của role được đánh dấu **Applied at
Scan Stations** trong Roles & permissions (ban đầu là Operator). Device token và mã
enroll là thông tin xác thực kiểu bearer: hãy phục vụ PartFlow qua HTTPS và giữ
header này ngoài log của reverse proxy
([hướng dẫn triển khai](./docs/DEPLOYMENT.vi.md)).

## Reconciliation (chỉ đọc)

`python -m app.cli reconcile` kiểm tra rằng trạng thái sản xuất đã lưu khớp với
lịch sử Movement, các allocation và quy tắc định danh chuẩn. Lệnh chạy trong một
snapshot database chỉ đọc, in một báo cáo JSON ra standard output và không bao giờ
thay đổi dữ liệu hay sửa một phát hiện (finding).

```bash
f=reconcile.json
docker compose exec -T backend uv run python -m app.cli reconcile > "$f"; rc=$?
python3 -c 'import json,sys; r=json.load(open(sys.argv[1])); assert r["exit_code"]==int(sys.argv[2]); print(r["result"], r["exit_code"])' "$f" "$rc" \
  || echo "could not run: no complete report (exit $rc)"
docker compose exec -T backend uv run python -m app.cli reconcile --check j     # identity check only
```

Tùy chọn: `--check ID` (lặp lại được, `a` đến `j`), `--statement-timeout SECONDS`
(1-3600, mặc định 300) và `--max-findings N` (1-10000, mặc định 100). Exit code:
`0` sạch, `1` không khớp, `2` không chạy được. Exit status chỉ có giá trị khi báo
cáo là một tài liệu JSON hoàn chỉnh có `exit_code` bằng đúng nó; báo cáo rỗng hoặc
không phân tích được nghĩa là không chạy được. Check (g) báo `not_applicable` cho
đến khi có archival lịch sử Movement, và check (h) báo `not_applicable` trên
database không có các database role của PartFlow, trong đó có development stack.
Không chạy migration trong lúc lệnh đang chạy. Các check, dạng lệnh production và
quy tắc vận hành nằm trong
[`docs/deployment/OPERATIONS_RUNBOOK.vi.md`](./docs/deployment/OPERATIONS_RUNBOOK.vi.md)
§7.

## Thiết lập IntelliJ IDEA / PyCharm (công cụ database và kiểm tra SQL)

Tùy chọn, nhưng nên làm khi làm việc trong IDE JetBrains (đã kiểm chứng với
IntelliJ IDEA 2026.2.1). Thiếu các thiết lập này, IDE báo các "lỗi" SQL gây hiểu
nhầm trong migration Alembic và test database, và có thể chặn commit vì chúng.

1. **Data source.** Cửa sổ Database → `+` → Data Source → PostgreSQL: host
   `localhost`, port `5432`, database `partflow`, user và mật khẩu **lấy từ `.env`
   của bạn** (xem lưu ý về thông tin đăng nhập ở trên). Trong phần chọn schema, hãy
   introspect `public` **và `pg_catalog`** — test schema truy vấn `pg_proc` để kiểm
   tra hàm trigger append-only, và mặc định `pg_catalog` không được introspect.
2. **SQL dialect.** Settings → Languages & Frameworks → SQL Dialects → đặt Project
   SQL Dialect là **PostgreSQL**, để SQL nhúng trong chuỗi Python được phân tích
   đúng cú pháp.
3. **SQL resolution scope.** Settings → Languages & Frameworks → SQL Resolution
   Scopes → ánh xạ project (hoặc thư mục `backend`) tới schema `partflow.public`
   của data source, để tên bảng và hàm trong SQL nhúng được phân giải theo
   development database thật.
4. **Làm mới sau khi migrate.** IDE phân giải tên theo snapshot introspection gần
   nhất — hãy làm mới data source (Ctrl+F5) sau mỗi lần `alembic upgrade` hoặc
   reset, nếu không các bảng mới vẫn "unresolved".

Các cảnh báo còn lại dự kiến, an toàn để bỏ qua:

- Trong chuỗi `CREATE TRIGGER` của một migration, IDE có thể vẫn báo hàm trigger
  hoặc `part_movements` là unresolved: mỗi đoạn SQL nhúng được phân tích độc lập,
  và các đối tượng đó được tạo bởi chính migration ấy (một phần qua
  `op.create_table` của Python, mà bộ phân giải SQL không thấy được).
- Các lớp test pytest hợp lệ không có `__init__`; kiểm tra "Class has no
  `__init__` method" là nhiễu đối với codebase này.

Đây là kết quả phân tích mã của IDE, không phải quality gate của dự án — các gate
chuẩn là các lệnh Docker trong mục "Quality commands". Nếu bước "Analyze code"
của hộp thoại commit chặn commit vì chúng, hãy dùng "Commit Anyway" hoặc tắt bước
đó (Settings → Version Control → Commit).

## Quality commands

Các container Linux là môi trường chuẩn cho mọi quality gate. Chạy các lệnh này
khi Compose stack đang chạy (`docker compose up -d`).

### Backend

```bash
docker compose exec backend uv run ruff format --check .   # formatting check (ruff format . to fix)
docker compose exec backend uv run ruff check .            # lint
docker compose exec backend uv run mypy app tests          # type check (strict)
docker compose exec backend uv run pytest                  # tests (see below)
docker compose exec backend uv run alembic upgrade head    # migrations
```

Bộ test pytest gồm ba loại:

- **Behavior test** — `tests/test_health.py` kiểm tra readiness và liveness với
  phép đọc revision database được mock (sẵn sàng, schema không khớp, override được
  chấp nhận, database không truy cập được; các phản hồi 503 an toàn), cộng một test
  chạy trên database thật.
- **Unit test** không cần database, ví dụ `tests/test_part_number_normalization.py`
  (chuẩn hóa Part Number), `tests/test_password_hashing.py` và
  `tests/test_password_policy.py` (băm mật khẩu scrypt và chính sách mật khẩu),
  `tests/test_station_device_domain.py` (quy tắc mã enroll và tên thiết bị),
  `tests/test_worker_badge_normalization.py` (quy tắc badge Worker chuẩn: trim, viết
  hoa, từ chối `PF:` và độ dài) và `tests/test_route_access.py`. Test cuối kiểm tra
  route registry trong `app/api/route_access.py` và thất bại với bất kỳ route nào
  registry chưa phân loại, nên route mới phải được thêm vào đó.
- **Integration test** — phần lớn các module còn lại. Chúng cần dịch vụ PostgreSQL
  truy cập được qua `DATABASE_URL` và chạy trên các database tạm riêng
  (`partflow_test_*`; test reconciliation dùng database clone từ template).
  `tests/test_database_connectivity.py` gọi `GET /api/health` qua wiring thật của
  ứng dụng, không mock gì. Các module schema (`tests/test_phase<N>_schema.py`) chạy
  migration Alembic thật (upgrade → downgrade → upgrade), mỗi module dừng ở revision
  ranh giới của nó, còn `tests/test_phase13_schema.py` và
  `tests/test_phase14_schema.py` bao phủ các revision từ `0014_phase13_workers` đến
  head duy nhất `0032_phase14_route_adjusted`: các ràng buộc, bộ từ vựng audit được
  mở rộng, các downgrade từ chối và sự tương đồng giữa model và migration.
  `tests/test_release_gate_api.py`, `tests/test_migrate_cli.py` và
  `tests/test_revision_cli.py` bao phủ write gate (409 `release_mismatch`, 503
  `not_ready`), job `migrate` (một transaction, các từ chối, lock) và báo cáo
  `revision`. Các module test API kiểm tra endpoint từ đầu đến cuối.

Test gọi các route cần đăng nhập hoặc cần quyền qua `client_as` / `admin_of` từ
`tests/auth_harness.py`, không qua client ẩn danh. Lời gọi tới route Scan Station
đi qua `station_device_client` (một thiết bị đã enroll cho station được gọi), và
một route station mới phải khai báo `RequireStationDevice` và được phân loại trong
`route_access.py`.

Database role được cấu hình phải là superuser (`partflow_user` của Compose và CI
là superuser), vì bộ test tạo database và các database role tạm
(`partflow_app_t…`, `partflow_maint_t…`), và một số trường hợp đặt
`session_replication_role`.

CI chạy bộ test với `PARTFLOW_TEST_DATABASE_ROLE=app`. Khi đó mọi kết nối tới một
database test chạy dưới một database role ứng dụng tạm, trừ Alembic, `owner_engine`
và các module `database_owner`. Hãy chạy cục bộ theo cùng cách trước khi commit một
thay đổi chạm tới việc ghi database, grant hoặc phần thiết lập test; mặc định cục
bộ vẫn là role owner:

```bash
docker compose exec -e PARTFLOW_TEST_DATABASE_ROLE=app backend uv run pytest
```

### Frontend

```bash
docker compose exec frontend npm run format        # format with Prettier
docker compose exec frontend npm run format:check  # formatting check
docker compose exec frontend npm run lint          # ESLint
docker compose exec frontend npm run typecheck     # TypeScript (strict, no emit)
docker compose exec frontend npm run test          # Vitest + React Testing Library
docker compose exec frontend npm run build         # type check + production build + mock-boundary check
```

`npm run check` chạy format check, lint, type check, test và build trong một lệnh.

### Chạy trực tiếp trên host (tùy chọn, nỗ lực tốt nhất)

Các lệnh trên có thể chạy không cần tiền tố `docker compose exec …` từ `backend/`
(với uv) hoặc `frontend/` (với Node 24), nhưng host không phải môi trường chuẩn:
phiên bản công cụ và hành vi hệ điều hành có thể khác các container Linux mà Docker
và CI dùng. Ghi chú cho backend khi chạy trên host:

- `DATABASE_URL` phải phân giải được. `backend/tests/conftest.py` đặt mặc định cho
  nó là development database từ `.env.example`; mọi thứ ngoài pytest (ví dụ
  `uv run alembic upgrade head`) cần đặt biến này tường minh hoặc có file
  `backend/.env`.
- Integration test của pytest cần dịch vụ `db` của Compose đang chạy (port 5432 của
  nó được publish ra host): test kết nối thực hiện một kiểm tra database thật qua
  `GET /api/health`, và các test tạo và xóa các database tạm `partflow_test_*` bằng
  role đã cấu hình. Nếu `.env` của bạn dùng thông tin đăng nhập riêng, hãy đặt
  `DATABASE_URL` cho khớp trước khi chạy pytest trên host.

Khi kết quả trên host khác kết quả trong container, kết quả container được tính.

## Ghi chú Docker development

Khi dùng môi trường Docker development, hãy coi các container Linux là môi trường
phát triển chuẩn.

### Định dạng frontend

Chạy Prettier **bên trong container frontend** trước khi kiểm tra định dạng:

```bash
docker compose exec frontend npm run format
```

Sau đó kiểm tra:

```bash
docker compose exec frontend npm run format:check
```

Chạy Prettier trực tiếp trên máy host có thể cho kết quả khác với container Linux
mà Docker và GitHub Actions dùng.

### Quality gate frontend đầy đủ

```bash
docker compose exec frontend sh -lc "npm run format:check && npm run lint && npm run typecheck && npm run test && npm run build"
```

### Quality gate backend đầy đủ

```bash
docker compose exec backend sh -lc "uv run ruff format --check . && uv run ruff check . && uv run mypy app tests && uv run pytest"
```

## Production stack

Production chạy một Compose stack riêng, không bao giờ dùng `compose.yaml`. Mục này
chỉ định hướng cho lập trình viên; phần tra cứu của stack (dịch vụ, file cấu hình,
file bí mật, database role, health, các giới hạn, ghi log và script) là
[`docs/DEPLOYMENT.vi.md`](./docs/DEPLOYMENT.vi.md) §3.1, việc cài đặt trên Synology
NAS của công ty nằm ở
[`docs/deployment/SYNOLOGY_NAS.vi.md`](./docs/deployment/SYNOLOGY_NAS.vi.md), và các
quy trình hằng ngày (phát hành, sao lưu, rollback, giám sát) nằm trong
[`docs/deployment/OPERATIONS_RUNBOOK.vi.md`](./docs/deployment/OPERATIONS_RUNBOOK.vi.md).
Các bài kiểm thử tự động của các lệnh và script production chạy trên Docker Desktop
và trong một container Linux.

- **File.** `compose.production.yaml` (Compose project `partflow-production`) định
  nghĩa `db`, `backend`, `web` (nginx, chỉ publish trên `127.0.0.1`) và các job chạy
  một lần `migrate`, `db-roles`, `backup-tools` và `status`. Cấu hình của nó là
  `.env.production`, sao chép từ `.env.production.example` và bị git bỏ qua, cộng
  ba file bí mật và một thư mục sao lưu đã tồn tại. Chỉ bước build dùng
  `compose.production.build.yaml`: `compose.production.yaml` không có phần build,
  nên `up` hoặc `run` với một release thiếu image sẽ thất bại thay vì build từ
  checkout. Các lệnh dùng cách viết tắt
  `PF="docker compose -f compose.production.yaml --env-file .env.production"`.
  Không bao giờ chạy `$PF down -v`: lệnh đó xóa volume database production.
- **Image.** Cả hai Dockerfile kết thúc bằng stage `development` mà `compose.yaml`
  build, và còn chứa stage `production` mà Compose không build theo mặc định
  (`compose.production.build.yaml` chọn stage này):

  ```bash
  docker build --target production backend
  docker build --target production frontend
  ```

  Image `production` của backend chạy dưới người dùng không phải root, không có
  reload server và không chạy migration khi khởi động. Image `production` của
  frontend là nginx chính thức được ghim phiên bản (`web`), phục vụ bản build bất
  biến và proxy `/api`. Mỗi image production mang release của nó
  (`PARTFLOW_RELEASE`, `PARTFLOW_COMMIT`), và cả hai từ chối giá trị thiếu hoặc sai
  dạng.
- **Kết nối database.** Với các image production, backend có thể đọc kết nối
  database từ `DATABASE_HOST`, `DATABASE_NAME`, `DATABASE_USER` và
  `DATABASE_PASSWORD_FILE` (cộng `DATABASE_PORT` tùy chọn) thay cho `DATABASE_URL`;
  đặt cả hai dạng sẽ bị từ chối. Backend production kết nối bằng database role
  quyền tối thiểu `partflow_app`, không bao giờ bằng owner.
- **Script.** `deploy/production/` chứa `release.sh` (trình tự phát hành),
  `smoke.sh` (kiểm tra loopback một release đang chạy), `backup.sh` và
  `restore-test.sh` (sao lưu có kiểm chứng và diễn tập khôi phục cô lập), và
  `check.sh` cùng `scheduled-reconcile.sh` (kiểm tra host và reconciliation hằng
  ngày). Mỗi script in cách dùng với `--help`.

### Kiểm tra artifact production

Phần tĩnh của các kiểm tra artifact production không cần stack đang chạy:

```bash
python3 -B -m unittest discover -s deploy/production/tests -p 'test*.py'
PARTFLOW_RELEASE=s2-check PARTFLOW_COMMIT=$(git rev-parse HEAD) PARTFLOW_SECRETS_DIR=<dir holding postgres_password> PARTFLOW_BACKUP_DIR=<any directory> PARTFLOW_SITE_TIMEZONE=UTC \
  docker compose -f compose.production.yaml -f compose.production.build.yaml --env-file .env.production.example build
```

Lệnh đầu chạy các test tĩnh của mô hình Compose, file môi trường mẫu, các Dockerfile
và cấu hình nginx, và các test của script với lệnh `docker`, `git` và `curl` giả
(`test_release_scripts.py`, `test_backup_scripts.py`,
`test_reconcile_regression.py`, `test_check_script.py`,
`test_scheduled_reconcile.py` và `test_monitor_report.py`). Lệnh thứ hai build cả
hai image production; bản build `web` chạy kiểm tra ranh giới production, và bản
build thiếu `PARTFLOW_COMMIT` thất bại.

Bốn kiểm tra dài hơn khởi động các Compose project dùng một lần trên port loopback,
cần Docker daemon và không thuộc CI. Mỗi kiểm tra ghi một file bằng chứng
(evidence):

```bash
python3 deploy/production/tests/stack_smoke.py --evidence <path.json>             # stack smoke through web (project partflow-s2-smoke)
python3 deploy/production/tests/release_rehearsal.py --evidence <path.json>       # a release rehearsal (project partflow-s3-rehearsal)
python3 deploy/production/tests/backup_rehearsal.py --evidence <path.json>        # backups, restore drill and rollback path 3 (projects partflow-s5-*)
python3 deploy/production/tests/observability_rehearsal.py --evidence <path.json> # structured logs, status, check.sh and scheduled-reconcile.sh
```

## Continuous integration

GitHub Actions (`.github/workflows/ci.yml`) chạy trên mỗi lần push vào `main` và mỗi
pull request:

- **backend** — với một dịch vụ PostgreSQL 16: `uv sync --frozen`, format check,
  lint, mypy, `alembic upgrade head` và pytest, với
  `PARTFLOW_TEST_DATABASE_ROLE=app`;
- **frontend** — với Node.js 24: `npm ci`, format check, lint, type check, test và
  bản build production;
- **docker** — kiểm tra rằng các image development build được
  (`docker compose build`), build các image production
  (`compose.production.build.yaml`, với giá trị release, bí mật, thư mục sao lưu và
  múi giờ dùng một lần) và chạy các test tĩnh của artifact production
  (`deploy/production/tests`).

## Cấu trúc repository

```text
frontend/          Vite + React + TypeScript app (shell + real views for every approved GUI view)
  src/styles/      semantic design tokens and shared primitives
  src/app/         router, theme, connectivity, session, real view registry, dev state preview
  src/api/         typed API client layer of the real views (production-safe)
  src/mocks/       development-only mock datasets (excluded from production builds)
  src/views/       one folder per approved GUI view
  src/components/  shared presentation components
  scripts/         production-boundary check run by npm run build
  nginx/           `web` image configuration (nginx templates, proxy and header snippets, trusted-proxy entrypoint)
  Dockerfile       `production` and `development` (default) stages
backend/
  app/api/         HTTP routes and the route access registry (route_access.py)
  app/application/ application services (every business rule and transaction)
  app/core/        configuration (pydantic-settings) and logging
  app/domain/      framework-independent domain vocabulary (PN normalization, enums)
  app/infrastructure/  database engine, connectivity check, and canonical schema mappings
  app/cli.py       operator commands (reset-password, reconcile, migrate, status, …)
  tests/           pytest suite
  alembic/         migration environment and revisions (baseline up to the current head)
  Dockerfile       `production` and `development` (default) stages
compose.yaml       development stack (db, backend, frontend)
compose.production.yaml  production stack (db, backend, web, migrate, db-roles, backup-tools, status); not for development
compose.production.build.yaml  build-only companion: the production image builds of backend and web
.env.example       development configuration (copy to .env)
.env.production.example  production configuration inventory (copy to .env.production)
deploy/production/release.sh, smoke.sh  release flow and loopback smoke checks (reconcile_regression.py compares reconcile reports)
deploy/production/backup.sh, restore-test.sh  backup artifact and isolated restore drill
deploy/production/check.sh, scheduled-reconcile.sh, monitor_report.py  host checks, daily reconcile and their report helper
deploy/production/tests/  production artifact static tests, script tests, Compose stack smoke and rehearsals
docs/              canonical project documentation
```

## Lịch sử thay đổi

| Ngày | Commit | Thay đổi |
| --- | --- | --- |
| 2026-11-10 | uncommitted | Các liên kết production trỏ tới bộ hướng dẫn triển khai đã viết lại, với `docs/DEPLOYMENT.md` §1 là điểm bắt đầu. |
| 2026-11-09 | `398c617` | Viết lại thành phần giới thiệu cho người mới cộng hướng dẫn cho lập trình viên; bỏ phần tường thuật trạng thái theo từng phase, rút gọn mục production thành phần định hướng, và sửa các lệnh và nhận định đã lỗi thời. |
| 2026-10-09 | `03bd90c` | Giám sát production: log có cấu trúc, mã định danh request, lệnh `status`, kiểm tra host và reconciliation định kỳ. |
| 2026-10-08 | `a330383` | Sao lưu production, bản sao lưu trước phát hành có kiểm chứng, diễn tập khôi phục và rollback path 3. |
| 2026-10-08 | `1116b07` | Các database role production riêng và grant của chúng. |
| 2026-10-08 | `b820e7a` | Luồng phát hành, định danh release, readiness và write gate. |
| 2026-10-08 | `d13e303` đến `25f63bf` | Image production, Compose stack production, danh mục cấu hình và các test tĩnh của nó. |
| 2026-10-07 | `e1dee40`, `918f025` | Nhập Work Order từ file CSV và Excel, gồm cả thay đổi Work Order đang active. |
| 2026-10-07 | `3fb66df` | Lệnh reconciliation chỉ đọc. |
| 2026-10-06 đến 2026-10-07 | `702ab1c` đến `582baf0` | Đăng nhập, thực thi quyền, thiết bị Scan Station đã enroll, sửa allocation trong Management, route adjustment, audit trail theo PN và theme của người dùng. |
| 2026-10-04 đến 2026-10-06 | `c12728d` đến `69b8ec1` | Worker, Worker session và xác nhận badge, quản lý Part Number và Planned Route, thiết lập hiển thị, thời hạn lưu giữ, người dùng và vai trò. |
| 2026-10-04 | `80f7925` đến `9f466fa` | Quản lý ưu tiên (Hot list). |
| 2026-09-02 đến 2026-09-13 | `5f006a0` đến `f016d41` | Production Board, Area Board và PN Tracking. |
| 2026-09-03 đến 2026-09-04 | `40fbcb5` đến `f10d8bd` | `Receive Quantity` tại Scan Station. |
| 2026-09-02 | `d2f68de` | Thêm bản dịch tiếng Việt và các hướng dẫn triển khai. |
| 2026-09-01 đến 2026-09-02 | `100d59d` đến `6fdf20e` | Stockroom, allocation và lịch sử Work Order đã hoàn thành. |
| 2026-08-25 đến 2026-09-01 | `a567b02` đến `7e60dcb` | Transfer tại Scan Station, xử lý trên Machine, xử lý trực tiếp, tách và gộp, Undo và sửa sai. |
| 2026-08-18 đến 2026-08-24 | `bb5b37e` đến `1fe73e9` | Thiết lập môi trường và Machine, nhập và release Work Order, và hướng dẫn thiết lập database và IDE. |
| 2026-07-23 đến 2026-08-17 | `676e750` đến `8c5ac60` | Repository foundation, quy trình phát triển, design system và application shell của frontend, và domain schema. |
