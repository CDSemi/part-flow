# Triển khai PartFlow trên Synology NAS

> **Bản gốc chuẩn:** [`docs/deployment/SYNOLOGY_NAS.md`](../../deployment/SYNOLOGY_NAS.md),
> được tạo trong gói tài liệu này trên baseline upstream
> `194ffc2e5e8e22c389abecd0830292a6707955d9`.
>
> **Trạng thái:** Repo hiện tại chỉ hỗ trợ staging nội bộ có giới hạn. Hướng
> production trong tài liệu này chỉ có thể thực hiện sau khi hoàn tất gate Phase
> 14 và Phase 16 trong [`DEPLOYMENT.md`](../DEPLOYMENT.md).
>
> **Quyền chuẩn:** Tiếng Anh là source of truth.

## 1. Mục đích sử dụng

Trước mắt dùng Synology NAS của hãng để một nhóm nội bộ có kiểm soát thử các
surface thật từ Phase 3.5–10 và kiểm tra tính thuận tiện trên shop floor. Chỉ
dùng dữ liệu giả hoặc có thể bỏ. Không gọi instance này là production và không
expose nó ra Internet.

Sau Phase 14/16, chính NAS đó có thể host pilot hoặc production nhỏ nếu vượt qua
gate về phần cứng, vận hành, backup và recovery dưới đây.

## 2. Checklist trước khi cài

Ghi lại kết quả của từng mục:

- model Synology, kiến trúc CPU, RAM đã lắp, DSM version, storage pool/volume,
  filesystem, dung lượng trống và tình trạng RAID;
- model đó có Container Manager và có thể tạo Project nhiều container từ
  Compose file;
- kiến trúc CPU của NAS được mọi PartFlow/PostgreSQL image đã chọn hỗ trợ;
- còn đủ RAM và CPU ổn định sau các workload NAS hiện có;
- NAS có địa chỉ LAN reserved và DNS/NTP đúng;
- project directory và backup directory nằm trên volume được bảo vệ;
- DSM firewall và network của hãng giới hạn service vào đúng VLAN/subnet;
- UPS và automatic safe shutdown đã cấu hình và test;
- ít nhất một backup destination được mã hóa nằm bên ngoài NAS này;
- administrator có thể dùng DSM và SSH khi cài đặt hoặc recovery.

Nếu model không có Container Manager, không cài Docker package không được hỗ trợ
hoặc làm yếu DSM. Hãy dùng Linux VM/VPS.

## 3. Cấu trúc thư mục

Tạo một shared folder riêng. Không giả định NAS nào cũng dùng `volume1`; dùng
volume thật do administrator chọn. Ví dụ:

```text
<NAS_VOLUME>/docker/partflow/
  repo/                 release đã checkout
  backups/
    database/
    manifests/
  restore-tests/
```

Permission:

- chỉ deployment administrator và account Container Manager dùng cần quyền
  write;
- DSM user thông thường không cần quyền vào filesystem;
- `.env`, database dump và manifest không được đặt trong shared folder phục vụ
  web;
- không cấp read/write rộng cho `Everyone`.

## 4. Triển khai staging có giới hạn ngay bây giờ

### 4.1 Lấy revision cố định

Tạm bật SSH nếu policy của hãng cho phép, đăng nhập bằng named admin account và
clone repo vào `repo/`. Checkout commit hoặc tag cụ thể; không triển khai một
branch đang di chuyển nếu chưa ghi lại commit đã resolve.

```bash
git clone https://github.com/CDSemi/part-flow.git repo
cd repo
git fetch --tags --prune
git checkout <approved-commit-or-tag>
git rev-parse HEAD
```

Nếu NAS không có Git, tải archive của approved commit trên workstation đáng tin
cậy, verify rồi extract vào `repo/`.

### 4.2 Cấu hình staging secret

Copy `.env.example` thành `.env` và thay mọi credential. `.env` thật không được
commit.

```bash
cp .env.example .env
chmod 600 .env
```

Yêu cầu:

- tạo PostgreSQL password dài và riêng;
- dùng database và credential chỉ dành cho staging;
- không dùng lại password DSM, GitHub, production hoặc password cá nhân;
- giữ recovery copy được bảo vệ trong secret manager/password vault của hãng.

`compose.yaml` hiện tại tạo `DATABASE_URL` của backend container từ
`POSTGRES_USER`, `POSTGRES_PASSWORD` và `POSTGRES_DB`, đồng thời truyền
`SITE_TIMEZONE` vào backend (mặc định `UTC`). Đặt `SITE_TIMEZONE` theo múi giờ
IANA của nhà máy (ví dụ `America/Los_Angeles`) trước lần khởi động đầu tiên:
backend từ chối tên múi giờ không hợp lệ, và done date cùng due outcome của
completed history được xét trên lịch này.

### 4.3 Giới hạn port được publish

Compose hiện tại publish PostgreSQL `5432`, backend `8000` và frontend `5173`.
Client staging thông thường chỉ cần frontend vì Vite proxy `/api` sang backend.

Trước khi start:

1. xóa `ports` của `db` và `backend` trong bản Compose dành riêng cho NAS;
2. chỉ publish frontend `5173` trên địa chỉ LAN reserved, hoặc bind vào
   `127.0.0.1` nếu dùng DSM Reverse Proxy;
3. giữ Compose service network ở private;
4. thêm DSM firewall rule chỉ cho subnet của hãng được vào port frontend/reverse
   proxy đã chọn.

Không commit thay đổi port hoặc secret dành riêng cho NAS về repo.

### 4.4 Tạo Container Manager Project

Trong DSM:

1. Cài/mở **Container Manager**.
2. Vào **Project** → **Create**.
3. Đặt tên project `partflow-staging`.
4. Chọn project path `repo/` và Compose file đã chuẩn bị.
5. Review service, mount, network, port và environment value mà UI render trước
   khi build.
6. Build và start Project.

Command SSH tương đương, nếu policy cho phép dùng Docker Compose:

```bash
docker compose up -d --build
docker compose ps
```

### 4.5 Chạy migration

Chờ PostgreSQL và backend healthy, rồi chạy canonical Alembic upgrade command
đúng một lần:

```bash
docker compose exec backend uv run alembic current
docker compose exec backend uv run alembic upgrade head
docker compose exec backend uv run alembic current
```

Lưu output vào deployment record. Không chạy `alembic downgrade` trên dữ liệu
staging cần giữ nếu chưa review migration cụ thể và chưa có backup đã verify.

### 4.6 Smoke test

Từ NAS:

```bash
curl --fail --silent --show-error http://127.0.0.1:5173/api/health
docker compose ps
docker compose logs --tail=200 backend frontend db
```

Từ workstation được phép:

- mở frontend URL;
- kiểm tra connected indicator và `/api/health`;
- kiểm tra các real view thuộc scope và ghi rõ view nào còn pending/unavailable;
- dùng test record được chỉ định để thử create, release, transfer, Machine,
  correction, stocking và allocation trong phạm vi đã triển khai;
- xác nhận success chỉ hiện sau server confirmation và read model được refresh;
- tạm dừng backend để kiểm tra production write bị block, sau đó bật lại và kiểm
  tra focus/readiness được phục hồi;
- xác nhận client ngoài subnet được phép không kết nối được.

### 4.7 Giới hạn staging

- không dùng quantity production thật hoặc dữ liệu nhân viên thật;
- không Internet/NAT port forwarding, QuickConnect publication hoặc public
  tunnel;
- chỉ named tester;
- có người quan sát thủ công trong test session;
- backup disposable đều đặn để có thể diễn tập migration/update nhiều lần;
- đánh dấu rõ URL và mọi communication là staging.

## 5. DSM Reverse Proxy cho HTTPS nội bộ

Với internal DNS name, tạo DSM reverse-proxy rule có source HTTPS và destination
là frontend local. Route toàn bộ origin về frontend; Vite proxy hiện tại sẽ
chuyển `/api` sang backend.

Kiểm soát:

- dùng certificate được workstation của hãng tin cậy;
- giữ original host và forwarding header;
- chỉ cho source LAN/VPN cần thiết qua DSM firewall và upstream network rule;
- không expose DSM administration qua PartFlow hostname;
- test deep application route trực tiếp, không chỉ `/`;
- test `/api/health` qua URL nội bộ mà client thật sử dụng.

Điều này bảo vệ transport tốt hơn nhưng không biến development server hoặc ứng
dụng chưa authentication thành production-ready.

## 6. Backup dữ liệu staging

Tạo logical PostgreSQL dump từ trong database container. Các biến dưới đây được
expand trong container, không phải DSM shell:

```bash
mkdir -p ../backups/database
docker compose exec -T db sh -c \
  'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom --no-owner --no-privileges' \
  > ../backups/database/partflow-$(date -u +%Y%m%dT%H%M%SZ).dump
```

Ghi manifest cạnh mỗi dump:

```bash
git rev-parse HEAD
docker compose exec -T backend uv run alembic current
sha256sum ../backups/database/partflow-*.dump
```

Copy dump và manifest đến nơi mã hóa ngoài NAS. Snapshot volume PostgreSQL đang
chạy không được là backup database duy nhất, trừ khi dùng quy trình snapshot
database-consistent đã được ghi rõ và test.

Làm restore test theo [`OPERATIONS_RUNBOOK.md`](./OPERATIONS_RUNBOOK.md) trước
khi tin rằng backup dùng được.

## 7. Quy trình update staging

1. Thông báo maintenance window cho staging.
2. Ghi Git commit và Alembic revision hiện tại.
3. Tạo và verify database dump mới.
4. Fetch và checkout approved target commit.
5. Review diff của `.env`, Compose, Dockerfile, dependency lock và migration.
6. Build target image.
7. Chạy `alembic upgrade head` đúng một lần.
8. Recreate/start service.
9. Chạy đầy đủ smoke test và reconciliation check.
10. Giữ commit cũ và backup qua hết observation window.

Không dùng auto-updater không giám sát theo `latest/main` cho hệ thống nhà máy có
database.

## 8. Chuyển sang production sau Phase 16

Không chuyển production chỉ bằng cách đổi URL. Thay development stack bằng
artifact production Phase 16 và verify toàn bộ gate:

- frontend/backend image production bất biến;
- production Compose không source bind mount, không reload/dev server, không
  publish database port, có restart/resource/logging policy rõ;
- private backend/database network và một reverse-proxy entry point;
- authentication/authorization Phase 14;
- secret handling và database role tách biệt theo least privilege;
- logical backup theo lịch, replicate off-NAS có mã hóa, retention alert và
  restore drill thành công;
- monitor health, log, disk, backup age, restart count và database growth;
- release, migration, rollback, reconciliation và incident runbook đã được
  administrator thật sự diễn tập;
- RPO/RTO và pilot scope được phê duyệt.

## 9. Capacity và reliability

Không coi một con số RAM/CPU chung chung là bằng chứng sẵn sàng. Đo trong staging
test thực tế:

- CPU/RAM lúc idle và peak của mọi service cùng workload NAS hiện có;
- tốc độ tăng data/index PostgreSQL;
- dung lượng tạm cho build/update image;
- thời gian/kích thước backup và thời gian restore;
- latency UI/API từ shop-floor VLAN;
- hành vi khi reboot NAS, restart container, gián đoạn mạng và UPS shutdown.

Đặt disk alert sao cho còn đủ chỗ cho database, ít nhất một bộ image update,
temporary migration work và local backup staging window.

## 10. Chuyển từ Synology sang VPS

1. Provision VPS theo [`VPS.md`](./VPS.md) với cùng release và migration level.
2. Diễn tập dump/restore bằng staging data.
3. Lên lịch production write freeze.
4. Tạo logical dump cuối và checksum manifest.
5. Chuyển qua kênh mã hóa và verify checksum.
6. Restore vào PostgreSQL trên VPS.
7. Chạy Alembic `current` và reconciliation trước khi mở truy cập.
8. Đổi internal DNS với TTL được kiểm soát và test client.
9. Giữ NAS instance ở trạng thái stopped nhưng còn recover được đến hết
   rollback window; tuyệt đối không để cả hai instance nhận write.
