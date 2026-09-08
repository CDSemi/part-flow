# PartFlow — Hướng dẫn triển khai trên Synology NAS (DSM 7)

> **Mục đích:** Hướng dẫn từng bước triển khai PartFlow lên Synology NAS chạy DSM 7 để dùng làm **internal staging/test**.
>
> **Repository:** `CDSemi/part-flow`  
> **GitHub:** https://github.com/CDSemi/part-flow  
> **Revision đã kiểm tra:** `d277f8e53a7ca79e0211c211a344dce60e8c7d7f`
>
> **Quan trọng:** Ở revision này, PartFlow **chưa phải production-ready**. Frontend vẫn chạy Vite development server, backend vẫn dùng Uvicorn `--reload`, và production authentication / authorization / hardening chưa hoàn tất. Không mở instance này ra Internet và không dùng dữ liệu production thật.

---

## 1. Kiến trúc triển khai đề xuất

```text
Browser / barcode workstation
           |
           | HTTP hoặc HTTPS nội bộ
           v
      Synology NAS
           |
           v
       frontend
       :5173
           |
           | /api -> backend:8000
           v
       backend
           |
           | db:5432
           v
      PostgreSQL
```

Nguyên tắc:

- Chỉ frontend được publish ra LAN.
- Backend không publish port `8000` ra LAN.
- PostgreSQL không publish port `5432` ra LAN.
- Frontend gọi API bằng relative URL `/api`.
- Dữ liệu PostgreSQL nằm trong Docker volume.
- Database migration chạy bằng Alembic như một bước riêng.
- Không dùng automatic updater để tự động deploy `latest/main`.

---

## 2. Kiểm tra NAS

Trong DSM mở:

**Control Panel → Info Center → General**

Ghi lại:

- Model NAS
- DSM version
- CPU architecture
- RAM
- Volume đang sử dụng
- Dung lượng còn trống

Sau đó mở **Package Center** và tìm:

- **Container Manager**, hoặc
- **Docker**

Nếu có **Container Manager**, cài Container Manager.

Nếu NAS chỉ có package **Docker** chính thức của Synology thì vẫn có thể dùng, nhưng cần kiểm tra Docker Compose qua SSH.

Nếu Package Center không cung cấp Container Manager hoặc Docker cho model đó, không cài ép package dành cho NAS khác.

---

## 3. Chuẩn bị IP và thư mục

Nên reserve một IP LAN cố định cho NAS.

Ví dụ trong tài liệu:

```text
NAS IP:       192.168.1.50
App port:     5173
Source path:  /volume1/docker/partflow/repo
```

Thay các giá trị trên bằng cấu hình thật của NAS.

### 3.1. Tạo thư mục

Trong File Station tạo:

```text
docker/
└── partflow/
    ├── repo/
    └── backups/
        └── database/
```

Đường dẫn tương ứng có thể là:

```text
/volume1/docker/partflow/repo
/volume1/docker/partflow/backups/database
```

Nếu NAS sử dụng `volume2`, thay `/volume1` bằng volume thực tế.

Không cấp quyền `Everyone` read/write.

`.env`, database dump và deployment files không nên nằm trong web-served shared folder.

---

## 4. Bật SSH

Trong DSM:

**Control Panel → Terminal & SNMP → Terminal**

Bật:

```text
Enable SSH service
```

Chỉ cho phép SSH từ mạng nội bộ quản trị.

Trên Windows mở PowerShell:

```powershell
ssh YOUR_DSM_USERNAME@192.168.1.50
```

Thay `YOUR_DSM_USERNAME` và `192.168.1.50` bằng tài khoản DSM và IP thực tế.

Khi nhập mật khẩu SSH mà màn hình không hiện ký tự là bình thường.

---

## 5. Kiểm tra Docker

Sau khi SSH vào NAS:

```bash
uname -m
uname -r
sudo docker version
```

Kiểm tra Docker Compose:

```bash
sudo docker compose version
```

Nếu lệnh trên không tồn tại:

```bash
sudo docker-compose version
```

Chỉ cần một trong hai lệnh Compose hoạt động.

Nếu cả hai đều không có, dừng tại đây và xử lý Docker / Compose trước.

---

## 6. Kiểm tra base images

PartFlow hiện dùng:

- PostgreSQL 16
- Python 3.12
- Node.js 24

Kiểm tra NAS có chạy được các image tương ứng hay không:

```bash
sudo docker run --rm node:24-alpine node --version
```

```bash
sudo docker run --rm python:3.12-slim python --version
```

```bash
sudo docker run --rm postgres:16 postgres --version
```

Nếu gặp các lỗi kiểu:

```text
Illegal instruction
exec format error
```

hoặc lỗi architecture/runtime tương tự, dừng deployment.

Không giải quyết bằng cách bật `privileged` hoặc vô hiệu hóa bảo vệ container.

---

## 7. Tải đúng revision PartFlow

Revision dùng cho hướng dẫn này:

```text
d277f8e53a7ca79e0211c211a344dce60e8c7d7f
```

Có thể tải ZIP tại:

```text
https://github.com/CDSemi/part-flow/archive/d277f8e53a7ca79e0211c211a344dce60e8c7d7f.zip
```

Giải nén trên máy Windows rồi upload source vào:

```text
/volume1/docker/partflow/repo
```

Cấu trúc đúng:

```text
repo/
├── backend/
├── frontend/
├── docs/
├── compose.yaml
├── .env.example
└── ...
```

Không để lồng thêm một cấp như:

```text
repo/
└── part-flow-d277f8.../
    ├── backend/
    └── frontend/
```

---

## 8. Bộ file staging dành riêng cho Synology

Không nên dùng trực tiếp `compose.yaml` gốc để deploy staging trên NAS vì compose gốc publish `5432`, `8000` và `5173`, đồng thời vẫn mang cấu hình development.

Nên dùng compose riêng cho NAS, ví dụ:

```text
compose.nas.yaml
nas.env.example
pf.sh
backup.sh
```

Cấu trúc:

```text
repo/
├── backend/
├── frontend/
├── docs/
├── compose.yaml
├── compose.nas.yaml
├── nas.env.example
├── pf.sh
├── backup.sh
└── ...
```

Nếu đã tải bộ `PartFlow_Synology_DSM7_Staging.zip`, chép các file đó vào root của `repo`.

---

## 9. Ghi lại revision đang deploy

Trong SSH:

```bash
cd /volume1/docker/partflow/repo
```

Tạo file:

```bash
printf '%s\n' \
  'd277f8e53a7ca79e0211c211a344dce60e8c7d7f' \
  > DEPLOYED_SOURCE.txt
```

Sau này khi update PartFlow, cập nhật file này theo đúng commit thực tế đang deploy.

---

## 10. Tạo `.env`

Trong thư mục repo:

```bash
cd /volume1/docker/partflow/repo
```

Chạy:

```bash
test -f .env || cp nas.env.example .env
chmod 600 .env
```

Sinh password database:

```bash
openssl rand -hex 32
```

Lưu password đó vào password manager của công ty.

Ví dụ `.env`:

```dotenv
POSTGRES_USER=partflow_staging
POSTGRES_PASSWORD=PASTE_THE_GENERATED_HEX_PASSWORD_HERE
POSTGRES_DB=partflow_staging

SITE_TIMEZONE=America/Los_Angeles

PARTFLOW_BIND_IP=192.168.1.50
PARTFLOW_HTTP_PORT=5173
PARTFLOW_ALLOWED_HOST=localhost
```

Thay password và IP bằng giá trị thực tế.

### 10.1. `SITE_TIMEZONE`

Nếu factory ở California:

```dotenv
SITE_TIMEZONE=America/Los_Angeles
```

Backend dùng timezone này cho factory calendar, bao gồm logic ngày hoàn thành và on-time/late. Không lấy timezone từ browser.

### 10.2. `DATABASE_URL`

Không cần tự đặt `DATABASE_URL` nếu `compose.nas.yaml` tự tạo URL từ:

```text
POSTGRES_USER
POSTGRES_PASSWORD
POSTGRES_DB
```

Backend sẽ kết nối tới service:

```text
db:5432
```

### 10.3. Không đổi credential tùy tiện sau khi DB đã được tạo

Các biến PostgreSQL chỉ được image dùng khi volume database được initialize lần đầu. Đổi `.env` về sau không tự đổi role/password đã tồn tại trong database.

Không xóa database volume chỉ để sửa lỗi password.

---

## 11. Firewall

Trước khi mở PartFlow cho người dùng staging:

**Control Panel → Security → Firewall**

Chỉ cho phép port:

```text
TCP 5173
```

rồi giới hạn theo subnet hoặc workstation staging cần thiết.

Không mở `5432` hoặc `8000` ra LAN.

Không tạo:

- Router port forwarding
- Public tunnel
- QuickConnect publication cho PartFlow
- Public reverse proxy

Instance này chỉ dành cho internal staging.

---

## 12. Kiểm tra Compose

Vào thư mục:

```bash
cd /volume1/docker/partflow/repo
```

Chạy:

```bash
sudo sh ./pf.sh config -q
```

Nếu thành công thường không có output.

Nếu có lỗi về environment variable, YAML hoặc unsupported option thì xử lý trước khi build.

Nên dùng:

```bash
sudo sh ./pf.sh ...
```

thay vì chạy `docker compose ...` trực tiếp, để tránh vô tình dùng `compose.yaml` development gốc.

---

## 13. Build backend

```bash
sudo sh ./pf.sh build backend
```

Theo dõi **DSM → Resource Monitor**.

Nếu build thất bại, không tiếp tục deployment.

---

## 14. Build frontend

```bash
sudo sh ./pf.sh build frontend
```

Tiếp tục theo dõi tài nguyên NAS.

---

## 15. Khởi động PostgreSQL

```bash
sudo sh ./pf.sh up -d db
```

Kiểm tra:

```bash
sudo sh ./pf.sh ps
```

Chờ `db` chuyển sang `healthy`.

Nếu không healthy:

```bash
sudo sh ./pf.sh logs --tail=100 db
```

Không chạy migration cho đến khi database healthy.

---

## 16. Chạy database migration

Khi PostgreSQL healthy:

```bash
sudo sh ./pf.sh run --rm --no-deps backend \
  uv run alembic upgrade head
```

Kiểm tra revision hiện tại:

```bash
sudo sh ./pf.sh run --rm --no-deps backend \
  uv run alembic current
```

Nếu migration lỗi: **dừng tại đây**.

Không chạy `alembic downgrade`, xóa volume hay `down -v` để thử cho chạy.

---

## 17. Khởi động backend và frontend

```bash
sudo sh ./pf.sh up -d backend frontend
```

Kiểm tra:

```bash
sudo sh ./pf.sh ps
```

Mục tiêu:

```text
db         Up / healthy
backend    Up / healthy
frontend   Up / healthy
```

Xem log:

```bash
sudo sh ./pf.sh logs --tail=100 backend frontend db
```

Trong port mapping, chỉ frontend nên được publish ra LAN.

Ví dụ frontend:

```text
192.168.1.50:5173->5173/tcp
```

Database có thể hiện `5432/tcp`, nhưng không được hiện:

```text
0.0.0.0:5432->5432/tcp
```

Backend tương tự không được publish `8000` ra LAN.

---

## 18. Kiểm tra health

Trên NAS:

```bash
curl --fail --silent --show-error \
  http://192.168.1.50:5173/api/health
```

Hoặc từ workstation mở:

```text
http://192.168.1.50:5173/api/health
```

Kết quả đúng phải có dạng:

```json
{
  "status": "ok",
  "service": "...",
  "database": "connected"
}
```

Health endpoint xác nhận backend kết nối được PostgreSQL. Nó không thay thế kiểm tra schema và business workflow.

---

## 19. Mở PartFlow

Trên workstation staging:

```text
http://192.168.1.50:5173
```

Không dùng `localhost` trừ khi browser chạy trực tiếp trên NAS.

---

## 20. Smoke test tối thiểu

Nên dùng dữ liệu synthetic.

| Thành phần | Giá trị test |
|---|---|
| Department | `TEST` |
| Area đầu | `TEST-A` |
| Area tiếp theo | `TEST-B` |
| Work Order | `TEST-WO-001` |
| Part Number | `TEST-PN-001` |
| Quantity | `10` |

Có thể bắt đầu với Area không dùng Machine để giảm độ phức tạp.

Quy trình:

1. Tạo Department.
2. Tạo Area.
3. Tạo Operation.
4. Tạo Scan Station.
5. Tạo Work Order.
6. Add PN / quantity.
7. Release quantity.
8. Kiểm tra quantity xuất hiện ở Area ban đầu.
9. Transfer một phần quantity sang Area tiếp theo.
10. Kiểm tra current state.
11. Refresh browser.
12. Mở từ workstation khác.
13. Đối chiếu PN Tracking / Area Board / Production Board trong phần đã có real API.

Ví dụ:

```text
Initial quantity: 10
Transfer:          4
Expected:
  TEST-A = 6
  TEST-B = 4
```

---

## 21. Kiểm tra behavior khi backend mất kết nối

Dừng backend:

```bash
sudo sh ./pf.sh stop backend
```

Frontend phải:

- phát hiện mất kết nối
- hiển thị trạng thái offline
- không cho ghi production write không chắc chắn

Khởi động lại:

```bash
sudo sh ./pf.sh start backend
```

Kiểm tra frontend recover connection.

---

## 22. Kiểm tra firewall thật

Từ workstation được phép, `http://NAS-IP:5173` phải truy cập được.

Từ workstation hoặc subnet không được phép, cùng URL phải bị chặn.

Không chỉ dựa vào việc rule nhìn có vẻ đúng trong DSM.

---

## 23. Backup PostgreSQL

Chạy:

```bash
cd /volume1/docker/partflow/repo
sh ./backup.sh
```

Backup được đặt tại:

```text
/volume1/docker/partflow/backups/database/
```

Một lần backup nên tạo:

```text
partflow-YYYYMMDDTHHMMSSZ.dump
partflow-YYYYMMDDTHHMMSSZ.dump.list
partflow-YYYYMMDDTHHMMSSZ.manifest.txt
partflow-YYYYMMDDTHHMMSSZ.sha256
```

| File | Nội dung |
|---|---|
| `.dump` | PostgreSQL custom-format dump |
| `.list` | Danh sách object trong dump |
| `.manifest.txt` | Deployment metadata |
| `.sha256` | Checksums |

Backup local trên NAS không đủ để bảo vệ khỏi mất NAS.

Phải copy backup sang vị trí off-NAS, encrypted và có retention policy.

---

## 24. Restore test

Không restore đè lên database staging đang chạy.

Chọn backup:

```bash
BACKUP="../backups/database/REPLACE_WITH_YOUR_BACKUP_FILE.dump"
```

Tạo database restore-test:

```bash
sudo sh ./pf.sh exec -T db sh -c \
  'createdb -U "$POSTGRES_USER" partflow_restore_check'
```

Restore:

```bash
sudo sh ./pf.sh exec -T db sh -c \
  'pg_restore -U "$POSTGRES_USER" \
    -d partflow_restore_check \
    --exit-on-error \
    --no-owner \
    --no-privileges' \
  < "$BACKUP"
```

Kiểm tra Alembic revision:

```bash
sudo sh ./pf.sh exec -T db sh -c \
  'psql -X -v ON_ERROR_STOP=1 \
    -U "$POSTGRES_USER" \
    -d partflow_restore_check \
    -c "SELECT version_num FROM alembic_version;"'
```

Kiểm tra movement count:

```bash
sudo sh ./pf.sh exec -T db sh -c \
  'psql -X -v ON_ERROR_STOP=1 \
    -U "$POSTGRES_USER" \
    -d partflow_restore_check \
    -c "SELECT count(*) FROM part_movements;"'
```

Restore test production đầy đủ sau này còn phải chạy application release tương ứng, verify health, verify read models, reconciliation và smoke tests.

---

## 25. Scheduled backup bằng DSM Task Scheduler

Trong DSM:

**Control Panel → Task Scheduler → Create → Scheduled Task → User-defined script**

Ví dụ:

```text
Task name: PartFlow staging database backup
User: root
Schedule: Daily, ngoài giờ làm việc
```

Script:

```bash
sh /volume1/docker/partflow/repo/backup.sh
```

Sau khi tạo:

1. Run task manually.
2. Kiểm tra task result.
3. Kiểm tra file backup.
4. Kiểm tra `.sha256`.
5. Kiểm tra alert/email nếu task fail.

Scheduled backup không thay thế off-NAS copy.

---

## 26. HTTPS nội bộ bằng DSM Reverse Proxy

Chỉ làm bước này sau khi truy cập bằng IP hoạt động ổn.

Ví dụ hostname nội bộ:

```text
partflow.example.com
```

DNS nội bộ:

```text
partflow.example.com -> 192.168.1.50
```

### 26.1. Sửa `.env`

```dotenv
PARTFLOW_BIND_IP=127.0.0.1
PARTFLOW_HTTP_PORT=5173
PARTFLOW_ALLOWED_HOST=partflow.example.com
```

Recreate frontend:

```bash
sudo sh ./pf.sh up -d --force-recreate frontend
```

### 26.2. DSM Reverse Proxy

Vào:

**Control Panel → Login Portal → Advanced → Reverse Proxy**

Tạo rule:

| Setting | Value |
|---|---|
| Description | `PartFlow Staging` |
| Source protocol | `HTTPS` |
| Source hostname | `partflow.example.com` |
| Source port | `443` |
| Destination protocol | `HTTP` |
| Destination hostname | `127.0.0.1` |
| Destination port | `5173` |

Nếu DSM có tùy chọn WebSocket headers, bật WebSocket forwarding.

Frontend hiện là Vite development server và có sử dụng WebSocket.

### 26.3. Certificate

Vào:

**Control Panel → Security → Certificate**

Gán certificate phù hợp cho hostname PartFlow.

Workstations phải trust certificate. Không yêu cầu người dùng bỏ qua certificate warning.

### 26.4. Kiểm tra

```text
https://partflow.example.com/
https://partflow.example.com/api/health
https://partflow.example.com/management/tracking
```

Refresh trực tiếp trên route con để kiểm tra routing.

HTTPS không làm ứng dụng trở thành production-ready và không thay thế authentication / authorization.

---

## 27. Các lệnh vận hành

Luôn chạy trong:

```bash
cd /volume1/docker/partflow/repo
```

### Xem trạng thái

```bash
sudo sh ./pf.sh ps
```

### Xem log

```bash
sudo sh ./pf.sh logs -f --tail=100 backend frontend db
```

`Ctrl+C` chỉ thoát khỏi log viewer.

### Dừng frontend và backend

```bash
sudo sh ./pf.sh stop frontend backend
```

Database vẫn chạy.

### Chạy lại frontend/backend

```bash
sudo sh ./pf.sh up -d backend frontend
```

### Dừng toàn bộ

```bash
sudo sh ./pf.sh stop
```

### Chạy lại toàn bộ

```bash
sudo sh ./pf.sh up -d
```

---

## 28. Không sử dụng các lệnh destructive sau trên instance cần giữ dữ liệu

Không chạy tùy tiện:

```bash
docker compose down -v
```

Không chạy:

```bash
docker volume prune
```

Không chạy:

```bash
docker system prune --volumes
```

Không xóa Docker volume PostgreSQL để "reset".

Movement history và tracking data của PartFlow cần được bảo toàn.

---

## 29. Quy trình update PartFlow

Không auto-update từ `main`.

Quy trình đề xuất:

```text
1. Chọn exact Git commit/tag.
2. Ghi lại revision hiện tại.
3. Ghi lại Alembic revision hiện tại.
4. Tạo fresh database backup.
5. Verify backup.
6. Giữ source/release cũ.
7. Đưa source release mới vào.
8. Review:
   - .env changes
   - compose changes
   - Dockerfile changes
   - dependency lock changes
   - Alembic migrations
9. Build backend.
10. Build frontend.
11. Run alembic upgrade head một lần.
12. Start backend/frontend.
13. Run health check.
14. Run smoke tests.
15. Verify tracking data.
16. Giữ release cũ + backup trong observation window.
```

Build:

```bash
sudo sh ./pf.sh build backend
sudo sh ./pf.sh build frontend
```

Migration:

```bash
sudo sh ./pf.sh run --rm --no-deps backend \
  uv run alembic upgrade head
```

Start:

```bash
sudo sh ./pf.sh up -d backend frontend
```

Nếu migration fail, không tiếp tục mở application cho người dùng.

Không mặc định `alembic downgrade` là rollback an toàn.

---

## 30. Troubleshooting

| Hiện tượng | Kiểm tra |
|---|---|
| `docker compose` không tồn tại | Thử `docker-compose` |
| `docker-compose` cũng không có | Kiểm tra package Docker / Container Manager |
| `Unsupported config option` | Có thể Compose quá cũ hoặc dùng sai compose file |
| `port is already allocated` | Đổi `PARTFLOW_HTTP_PORT` |
| Browser không mở app | IP, firewall, frontend container, published port |
| UI báo OFFLINE | Backend / DB / `/api/health` |
| `relation ... does not exist` | Alembic migration chưa hoàn tất |
| `password authentication failed` | `.env` không khớp DB đã initialize |
| `Blocked request. This host is not allowed` | Kiểm tra `PARTFLOW_ALLOWED_HOST` |
| `exec format error` | CPU architecture / image compatibility |
| `Illegal instruction` | CPU/runtime không hỗ trợ image |
| DB healthy nhưng app lỗi | Health DB không đồng nghĩa schema/business state đúng |
| UI có dữ liệu mock | Một số view vẫn development-only ở revision hiện tại |

---

## 31. Điều kiện coi staging deployment ban đầu đã thành công

- [ ] Docker hoạt động.
- [ ] Compose hoạt động.
- [ ] `node:24-alpine` chạy được.
- [ ] `python:3.12-slim` chạy được.
- [ ] `postgres:16` chạy được.
- [ ] `db` healthy.
- [ ] Alembic migration thành công.
- [ ] `backend` healthy.
- [ ] `frontend` healthy.
- [ ] Chỉ frontend publish ra LAN.
- [ ] `/api/health` trả HTTP 200.
- [ ] Browser mở PartFlow được.
- [ ] Một workflow ghi dữ liệu test hoạt động.
- [ ] Refresh vẫn thấy dữ liệu.
- [ ] Tracking / Area Board phản ánh state đúng trong scope đã implement.
- [ ] Backend mất kết nối thì frontend block writes.
- [ ] Firewall chặn nguồn không được phép.
- [ ] Database backup tạo thành công.
- [ ] Backup archive đọc được.
- [ ] Restore test vào database riêng thành công.
- [ ] Có off-NAS backup.
- [ ] Instance được ghi rõ là staging/test.

---

## 32. Những gì chưa được coi là production-ready

Ở revision:

```text
d277f8e53a7ca79e0211c211a344dce60e8c7d7f
```

deployment này vẫn là staging vì:

- backend dùng Uvicorn development configuration với `--reload`
- frontend dùng Vite development server
- chưa có production image topology hoàn chỉnh
- authentication / role enforcement production chưa phải final gate
- chưa có production-grade secret handling
- chưa có production release artifact / immutable image policy hoàn chỉnh
- chưa có production monitoring / alerting hoàn chỉnh
- chưa có final backup / restore / RPO / RTO approval
- chưa có final rollback and reconciliation package
- một số UI surface vẫn là development-only preview hoặc chưa kết nối production API đầy đủ

Không biến staging thành production chỉ bằng cách:

- thêm HTTPS
- thêm hostname
- đặt reverse proxy
- đưa NAS ra Internet

Production deployment cần hoàn tất production hardening theo roadmap của PartFlow.

---

## 33. Mục tiêu lâu dài

Kiến trúc nên giữ portable:

```text
Browser
   |
 HTTPS
   |
Reverse Proxy
   |
   +------> Frontend
   |
   +------> /api -> FastAPI
                     |
                     v
                 PostgreSQL
```

Khi chuyển từ Synology NAS sang VPS:

1. Deploy cùng application release.
2. Deploy cùng schema revision.
3. Rehearse PostgreSQL dump/restore.
4. Freeze writes.
5. Tạo final backup.
6. Verify checksum.
7. Restore trên VPS.
8. Verify Alembic revision.
9. Run reconciliation.
10. Run smoke tests.
11. Switch internal DNS.
12. Giữ NAS instance stopped nhưng recoverable trong rollback window.

Không để NAS và VPS cùng nhận production writes.

---

## 34. Tài liệu PartFlow liên quan trong repository

```text
docs/DEPLOYMENT.md
docs/deployment/SYNOLOGY_NAS.md
docs/deployment/OPERATIONS_RUNBOOK.md
docs/IMPLEMENTATION_ROADMAP.md
README.md
```

English documentation trong repo là source of truth nếu nội dung thay đổi sau revision được ghi ở đầu tài liệu này.

---

## 35. External references

Synology Container Manager:

```text
https://www.synology.com/en-us/dsm/feature/container-manager
```

Synology DSM Reverse Proxy:

```text
https://kb.synology.com/en-us/DSM/help/DSM/AdminCenter/system_login_portal_advanced?version=7
```

Docker port publishing:

```text
https://docs.docker.com/engine/network/port-publishing/
```

Docker firewall behavior:

```text
https://docs.docker.com/engine/network/packet-filtering-firewalls/
```

Vite server options:

```text
https://vite.dev/config/server-options
```

---

## Revision note

Guide này được đóng gói ngày **2026-09-08** và được đối chiếu với `CDSemi/part-flow` branch `main` tại commit:

```text
d277f8e53a7ca79e0211c211a344dce60e8c7d7f
```

Nếu `main` thay đổi sau commit này, cần review lại:

```text
compose.yaml
backend/Dockerfile
frontend/Dockerfile
.env.example
docs/DEPLOYMENT.md
docs/deployment/SYNOLOGY_NAS.md
docs/deployment/OPERATIONS_RUNBOOK.md
Alembic migrations
```

trước khi áp dụng guide cho release mới.
