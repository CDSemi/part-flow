# Hướng dẫn triển khai PartFlow

> **Bản gốc chuẩn:** [`DEPLOYMENT.md`](DEPLOYMENT.md).
> Bản tiếng Việt được dịch từ file tiếng Anh tương ứng.
>
> **Quyền chuẩn:** Tiếng Anh là source of truth. File này mô tả những gì có thể
> triển khai hiện tại, những gì Phase 16 phải bổ sung và contract vận hành
> portable cho Synology NAS, VPS và shared hosting có điều kiện.

## 1. Trạng thái roadmap

PartFlow có phase triển khai: **Phase 16 — Deployment, Production Hardening,
and Admin Maintenance** trong `IMPLEMENTATION_ROADMAP.md`. Phase 16 bao gồm
backup, migration, HTTPS/truy cập nội bộ, observability, rollback,
reconciliation, pilot deployment và bảo trì archive/purge dành cho Admin.

Repo đã đóng Phase 1 đến Phase 15, gồm cả Phase 10.5 — Scan Station Receive
Quantity, các view giám sát của Phase 11, Priority Management (Phase 12),
Administration đầy đủ (Phase 13), Phase 14 — Authentication, Role Enforcement,
and Authorized Management Corrections (sign-in cho application User,
permission enforcement phía server trên mọi đọc và write Administration và
Management, và các route Scan Station yêu cầu thiết bị station do administrator
enroll, §2) và Phase 15 — File-Based Work Order Import (đóng 2026-10-08).
Phase 16 đang thực hiện: slice 1 (lệnh `reconcile` chỉ đọc), slice 2
(production artifact: image backend và `web` production, `compose.production.yaml`,
bảng kê configuration và secret, và network rate limiting, §3.1), slice 3
(release flow: release identity, liveness và readiness, backend write gate,
`migrate`, `release.sh` và `smoke.sh`, §3.1) và slice 4 (database-role hardening: các database role `partflow_app` và
`partflow_maintenance`, `provision-roles`, grant được áp dụng bởi mọi `migrate` và bởi `apply-grants`, và
reconcile check (h), §3.1) và slice 5 (backup: backup artifact đã verify, pre-release backup trong write
freeze, restore drill cô lập và rollback path 3, §3.1) và slice 6 (observability: structured log với HTTP request id,
`status`, `check.sh` và `scheduled-reconcile.sh`, §3.1) đã triển khai. Phase 16 vẫn sở hữu
TLS trên host, việc chạy backup và drill trên pilot host và các gate (§5 và `IMPLEMENTATION_ROADMAP.md`).

Vì vậy:

| Mục đích | Repo hiện tại | Quyết định |
| --- | --- | --- |
| Máy developer | Được hỗ trợ | Dùng `compose.yaml` theo root README. |
| Synology staging/test nội bộ | Được hỗ trợ có giới hạn | Chỉ trong LAN, dùng dữ liệu giả/không phải production, người dùng được kiểm soát và backup rõ ràng. Xem [`deployment/SYNOLOGY_NAS.md`](./deployment/SYNOLOGY_NAS.md). |
| Pilot hoặc production | Chưa sẵn sàng | Production artifact, release flow, database-role hardening, backup và observability đã có (§3.1: image, `web`, `compose.production.yaml`, bảng kê configuration, `release.sh`, `partflow_app`, `backup.sh`, `restore-test.sh`, `check.sh`, `scheduled-reconcile.sh`, `status`), nhưng các pilot gate ở §5 vẫn còn lại (Phase 16: P16-S7). |
| Mở ra Internet | Hiện tại bị cấm | TLS do platform proxy kết thúc, và chưa host nào cấu hình hay xác minh nó (P16-S7); các gate §5 chưa đạt. Network rate limiting đã có trong `web`; `compose.yaml` vẫn expose các service development (§2). |

Triển khai staging nội bộ không có nghĩa Phase 16 đã hoàn thành.

## 2. Vì sao Compose hiện tại chỉ dành cho development

Repo tự ghi rõ `compose.yaml` là artifact development, cùng với stage
`development` mặc định (cuối cùng) của mỗi Dockerfile; cả hai Dockerfile nay còn
có stage `production` mà Compose không build (§3.1). `compose.yaml` không đổi và
vẫn chỉ dành cho development. Các giới hạn đã quan sát được gồm:

- backend chạy Uvicorn với `--reload`;
- frontend chạy Vite development server thay vì phục vụ production build bất
  biến;
- source directory và dependency directory được bind mount;
- các port PostgreSQL, backend và frontend đều được publish ra host;
- có credential mặc định dành cho development;
- database và ứng dụng dùng chung PostgreSQL role do Compose tạo (production stack dùng các database role riêng, §3.1);
- chưa có production reverse proxy, TLS policy, secret store, log rotation,
  release image tag, scheduled backup job, restore drill hoặc command rollback;
- Phase 14 đã có sign-in cho application User và permission check phía server bao phủ mọi đọc và write Administration và Management; mọi route Scan Station yêu cầu thiết bị station do administrator enroll cho station đó và mỗi action của station cần permission của role áp dụng tại Scan Station (Phase 14 slice 4) — truy cập station ẩn danh trên toàn mạng đã được đóng;
- một số view đã duyệt vẫn là preview chỉ có ở development hoặc còn chờ tích
  hợp backend/frontend thật.

Không được che các giới hạn này bằng reverse proxy của NAS hoặc public DNS name.

**Bước nâng cấp cho Phase 14 slice 2 (Administration enforcement).** Trước và sau khi deploy nó, đếm các active user có mật khẩu mà role giữ từng permission-management key (chạy trong database shell, ví dụ `docker compose exec db psql -U <POSTGRES_USER> -d partflow -c "..."`):

```sql
SELECT rp.permission, count(*) AS holders
FROM users u
JOIN user_credentials c ON c.user_id = u.id
JOIN role_permissions rp ON rp.role_id = u.role_id
WHERE u.is_active
  AND rp.permission IN ('MANAGE_USERS_AND_ROLES', 'MANAGE_CORRECTION_PERMISSIONS')
GROUP BY rp.permission;
```

Thiếu row nghĩa là không có holder. Kỳ vọng cả hai số đếm ít nhất là 1, hoặc hoàn toàn không có row `MANAGE_USERS_AND_ROLES` (khi đó first-run setup đang mở). Thiếu row `MANAGE_CORRECTION_PERMISSIONS` trong khi `MANAGE_USERS_AND_ROLES` có holder nghĩa là không ai được quản lý correction permission: trước khi deploy, cấp nó trong Administration; sau khi deploy, chạy `docker compose exec backend uv run python -m app.cli restore-correction-permission-management --role-name <role>` (xem `README.md`). Backend cũng ghi cảnh báo lúc khởi động ở trạng thái đó.

## 3. Topology portable đích

Gói production Phase 16 nên giữ cùng một topology trên Synology và VPS sau này.
TLS kết thúc tại platform proxy (DSM reverse proxy, hoặc Caddy trên VPS);
`web` (nginx) nằm trong stack phục vụ build bất biến và `/api`, và chỉ được
publish trên địa chỉ loopback của host (quyết định của owner OD-16-02):

```text
Browser / barcode workstation
            |
          HTTPS
            |
Platform TLS proxy (DSM reverse proxy | Caddy on a VPS)
            |   http://127.0.0.1:<port>  (loopback only)
            v
web (nginx): static build, SPA fallback, request limits, rate limits
            |
          /api
            v
FastAPI backend
            |
    private container network
            |
        PostgreSQL
```

Các ranh giới bắt buộc:

- chỉ expose HTTPS cho client; HTTP chỉ được dùng để redirect nếu cần;
- giữ PostgreSQL private, tuyệt đối không publish port 5432 ra mạng không đáng
  tin cậy;
- giữ backend private khi reverse proxy có thể route `/api` nội bộ;
- phục vụ frontend và API chung origin để browser tiếp tục dùng URL `/api`
  tương đối như hiện tại;
- persist dữ liệu PostgreSQL và output backup bên ngoài container tạm thời;
- chạy schema migration như một release step rõ ràng, không phải side effect
  không kiểm soát mỗi khi replica khởi động;
- định danh mỗi lần triển khai bằng Git commit hoặc image tag bất biến;
- dùng cùng một format backup portable giữa NAS và VPS (đã triển khai: backup
  artifact của P16-S5, `deployment/OPERATIONS_RUNBOOK.md` §3).

### 3.1 Production stack (Phase 16 slice 2 đến 6)

**Trạng thái.** Đã triển khai (P16-S2): các stage `production` của
`backend/Dockerfile` và `frontend/Dockerfile`, cấu hình `web` trong
`frontend/nginx/`, các database setting của backend bên dưới,
`compose.production.yaml` (Compose project `partflow-production`),
`.env.production.example`, các static test production
(`deploy/production/tests/test_production_artifacts.py`) và Compose stack smoke
(`deploy/production/tests/stack_smoke.py`). Bằng chứng chỉ gồm Windows/Docker
Desktop và một Linux container; không có gì trong mục này đã được xác minh trên
Synology NAS hay VPS (đó là P16-S7). Đã triển khai (P16-S3): release identity,
liveness và readiness, backend write gate, `migrate` và `revision`, `release.sh`
và `smoke.sh` cùng `reconcile_regression.py`, và update notice ở frontend (các
mục con bên dưới). Cả hai production image nhận `PARTFLOW_RELEASE` và `PARTFLOW_COMMIT` làm build argument và mang release identity (stage `production` của backend đặt `RELEASE_TAG` và `RELEASE_COMMIT`); production stack smoke và release rehearsal đã chạy (Trạng thái, Bằng chứng). Đã triển khai (P16-S4): các database role `partflow_app` và `partflow_maintenance`, `provision-roles` và `apply-grants`, service `db-roles` và reconcile check (h) (Database role và grant, bên dưới). Đã triển khai (P16-S5): `deploy/production/backup.sh` (backup daily, manual và pre-release: `pg_dump` custom-format từ bên trong `db`, được publish thành một directory cùng manifest và `SHA256SUMS`), các command `backup-manifest`, `backup-verify` và `backup-rotate` của service `backup-tools`, các quy tắc verify và freshness mà `migrate` áp dụng cho pre-release backup, backup mà `release.sh` lấy bên trong write freeze, `deploy/production/restore-test.sh` (restore drill cô lập) và rollback path 3 cùng new-instance restore đã được tài liệu hóa (`deployment/OPERATIONS_RUNBOOK.md` §3, §4 và §6). Bằng chứng chỉ gồm Windows/Docker Desktop và một Linux container; schedule, off-host replication, drill có đo thời gian đầu tiên và path 3 trên một host thuộc P16-S7. Đã triển khai (P16-S6): structured JSON log với HTTP request id, command và service `status`, `deploy/production/check.sh` và `deploy/production/scheduled-reconcile.sh` (Logging và monitoring, bên dưới); schedule của chúng và failure notification trên một host thuộc P16-S7.

**Service và network (`compose.production.yaml`).**

| Service | Vai trò | Network | Ghi chú |
| --- | --- | --- | --- |
| `db` | PostgreSQL `postgres:16.14` (biến thể Debian, không bao giờ `-alpine`: collation và reconcile check (j) phụ thuộc glibc) | `internal` (không có route ra ngoài) | volume `postgres_data`; không publish port; stop grace 60 s |
| `backend` | image `partflow/backend:${PARTFLOW_RELEASE}`, stage `production` | `internal`, `edge` | kết nối bằng `partflow_app` (`DATABASE_ROLES_REQUIRED=true`) và chỉ mount `partflow_app_password`; `SESSION_COOKIE_SECURE=true` cố định; `WEB_CONCURRENCY` lấy từ `PARTFLOW_BACKEND_WORKERS` (mặc định 2); `FORWARDED_ALLOW_IPS` = edge subnet; stop grace 200 s (cao hơn upstream timeout dài nhất 180 s của `web`); `restart: unless-stopped` (không bao giờ `on-failure`: với nhiều worker, một lần từ chối configuration thoát với mã `0`) |
| `web` | image `partflow/web:${PARTFLOW_RELEASE}`, stage `production` | `edge` | port publish duy nhất, `127.0.0.1:${PARTFLOW_HTTP_PORT}:80` (không có biến cho bind address); không có `depends_on`, nên nó vẫn phục vụ shell khi `backend` đang dừng |
| `migrate` | `python -m app.cli migrate` one-shot từ image backend (entrypoint; một connection, một transaction) | `internal` | profile `ops`: không bao giờ được `up` khởi động; chạy bằng `--profile ops run --rm -T --user "$(id -u):$(id -g)" migrate (--pre-release-backup NAME \| --no-backup-reason TEXT)`; thiếu backup option là lỗi cú pháp; mount backup directory read-only tại `/backups` để verify backup được nêu tên (`--user` cho phép nó đọc directory mode 0700) |
| `backup-tools` | `python -m app.cli` one-shot từ image backend; chạy với `backup-manifest`, `backup-verify` hoặc `backup-rotate` (`deploy/production/backup.sh`) | không có (`network_mode: none`) | profile `ops`: không bao giờ được `up` khởi động; không database, không secret; chỉ mount backup directory tại `/backups`; chạy với `--user "$(id -u):$(id -g)"` |
| `db-roles` | `python -m app.cli provision-roles` (lệnh mặc định) hoặc `apply-grants` (tham số) one-shot từ image backend; kết nối bằng owner và mount cả ba secret file | `internal` | profile `ops`: không bao giờ được `up` khởi động; chạy bằng `--profile ops run --rm -T db-roles [apply-grants]` |

Image được build cục bộ từ release đã checkout, qua file đi kèm chỉ để build
`compose.production.build.yaml`, và không bao giờ pull (`pull_policy: never`); tag
là `PARTFLOW_RELEASE` và build còn cần release commit (`PARTFLOW_COMMIT`, xem
Release identity). `compose.production.yaml` không có phần build, nên `up` hay
`run` với một tag thiếu image sẽ thất bại với `No such image` thay vì build
checkout hiện tại dưới tag đó. Mỗi service có restart policy,
health check (trừ các service `ops` one-shot; check của `backend` là route liveness, xem Readiness và write gate), giới hạn memory và CPU lấy từ file môi trường (giá
trị khởi đầu, sẽ đo trên host pilot ở P16-S7) và log rotation `json-file` (10 MiB,
5 file). Secret được mount dạng file dưới `/run/secrets`; không secret nào là
giá trị environment và không secret nào có default đã commit. Owner role (`POSTGRES_USER`, một
superuser) chỉ do `db`, `migrate` và `db-roles` dùng; `backend` kết nối bằng `partflow_app` và
không bao giờ mount password của owner.

**Image.** `backend` (stage `production`): Python 3.12 slim, dependency không
phải development đã lock, không có `tests/`, không có `.env`, không có reload
server, chạy bằng user `10001:10001`; lệnh start là `uvicorn app.main:app --host
0.0.0.0 --port 8000 --no-access-log --log-config app/core/logging.production.json`.
Nó không bao giờ chạy migration khi start.
`web` (stage `production`): official `nginx:1.30.5-alpine` đã pin cùng build bất
biến từ `npm run build` (gồm production-boundary check). Hai stage `development`
mặc định không đổi.

**Cấu hình database của backend.** Backend lấy kết nối từ đúng một trong: `DATABASE_URL`
(development, test, CI, staging), hoặc `DATABASE_HOST`, `DATABASE_NAME`,
`DATABASE_USER` và `DATABASE_PASSWORD_FILE` cùng `DATABASE_PORT` tùy chọn (mặc
định 5432). Có cả hai dạng, hoặc không dạng nào đầy đủ, đều bị từ chối lúc
startup. File password phải có đúng một dòng (line break ở cuối được bỏ qua), vì
`initdb` của PostgreSQL chỉ lấy password của role mới từ dòng đầu; file nhiều
dòng, rỗng, không đọc được hoặc không phải UTF-8 bị từ chối với thông báo nêu
đường dẫn file và không bao giờ nêu nội dung. Không validation error nào echo giá
trị input. URL được ghép trong application, nên ký tự đặc biệt trong password
không cần encode thủ công.

**Database role và grant (P16-S4).** Production database có ba role. Owner (`POSTGRES_USER`, mặc định `partflow_owner`) là superuser bootstrap của PostgreSQL: nó sở hữu mọi object và do `db`, `migrate`, `db-roles` và backup dùng. `partflow_app` do `backend` dùng (API, `reconcile`, `revision` và các recovery CLI). `partflow_maintenance` được provision và cấp SELECT trên các table được nêu tên, và chưa service nào dùng nó cho đến slice archival. Cả hai do `provision-roles` tạo với `LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS`, không membership, không setting theo role và không sở hữu object nào; tên được cố định trong code và trong `compose.production.yaml`, và tên owner phải khác cả hai.

| Class | Privilege của `partflow_app` | Table |
| --- | --- | --- |
| append-only | SELECT, INSERT | `part_movements`, `audit_events`, `machine_lifecycle_events`, `quantity_flow_lineage`, `work_order_allocations` |
| guarded update | SELECT, INSERT, UPDATE | `worker_sessions` (row trigger của nó giới hạn các cột) |
| no update | SELECT, INSERT, DELETE | `assigned_route_steps` |
| read only | SELECT | `alembic_version` |
| ordinary | SELECT, INSERT, UPDATE, DELETE | 21 table còn lại |

Không role nào có TRUNCATE, sequence privilege (identity column), grant option, hay `CREATE` trên schema `public` hoặc trên database, và privilege của PUBLIC cùng default privilege bị gỡ. `partflow_maintenance` chỉ có SELECT trên 14 table được nêu tên. Bảng phân loại đầy đủ là `SLICE1_DATA_MODEL.md` §17. Raise-on-write trigger vẫn là lớp thứ nhất; việc revoke là lớp thứ hai. Vì vậy một `permission denied` (SQLSTATE 42501) trong log backend là một incident (`deployment/OPERATIONS_RUNBOOK.md` §2), không bao giờ được sửa bằng cách cấp thêm quyền.

`provision-roles` đọc `partflow_app_password` và `partflow_maintenance_password` (mỗi file một dòng, 16 đến 128 ký tự ASCII in được không có khoảng trắng, hai password khác nhau) trước khi kết nối, tạo hoặc sửa hai role và đặt password SCRAM-SHA-256 của chúng trong một transaction, và lặp lại an toàn. `apply-grants` derive mọi grant từ bảng phân loại table trong code và áp dụng trong một transaction; nó từ chối trừ khi database đang ở Alembic head của release. Mọi `migrate` chạy nó trong transaction của mình sau upgrade, nên một release không bao giờ để một table mới thiếu grant: table chưa có class, role có attribute hoặc membership bị cấm, role sở hữu object, hoặc privilege do một role mà PartFlow không quản lý cấp sẽ rollback toàn bộ lần chạy (kết quả `migrate` `refused`, exit 1, không đổi gì). Với `DATABASE_ROLES_REQUIRED=true` (cố định bật trên `backend` và `migrate` trong `compose.production.yaml`) role thiếu cũng bị từ chối (`roles_not_provisioned`); khi không có nó (development, test, staging) báo cáo grants là `not_provisioned` và không cấp gì. Grant không bao giờ được restore từ dump: chúng được derive lại từ code head bởi mọi `migrate` và bởi `apply-grants` sau restore. Password của một role được rotate trong một write freeze ngắn (`deployment/OPERATIONS_RUNBOOK.md` §9). Reconcile check (h) xác minh kết quả (gate ở §5); superuser tắt trigger nằm ngoài khả năng phát hiện (đã chấp nhận).

**Process model.** `WEB_CONCURRENCY` đặt số uvicorn worker và
`FORWARDED_ALLOW_IPS` đặt các địa chỉ proxy mà uvicorn tin cậy cho forwarded
header; Compose đặt cả hai bên trong container (`WEB_CONCURRENCY` lấy từ
`PARTFLOW_BACKEND_WORKERS`), nên giá trị `WEB_CONCURRENCY` trong shell của
operator không có tác dụng. Mỗi worker có setup token first-run riêng và giới hạn
password-hashing riêng, nên **first-run setup chạy với một worker**
(`PARTFLOW_BACKEND_WORKERS=1 $PF up -d backend`) và số worker cấu hình được khôi
phục sau đó (`$PF up -d backend`); với hai worker, một request có thể đến
worker không giữ token đã được copy và bị từ chối `403 setup_token_invalid`
(không write). Với hơn một worker, việc từ chối cấu hình lúc startup dừng
container với exit code `0`; tín hiệu lỗi là dòng log và số lần restart, không
phải exit code. Dừng hoặc tạo lại backend có hơn một worker không từ chối kết
nối mới ngay: uvicorn supervisor giữ listening socket mở cho đến khi worker cuối
cùng thoát (đến 200 s stop grace khi một import đang hoàn tất), nên request gửi
trong lúc đó được nhận nhưng không bao giờ được trả lời. Nó kết thúc bằng 504 của
`web` sau read timeout 60 s, hoặc bằng 502 khi supervisor thoát; backend chưa
chạy request đó, nhưng client phải coi nó là kết quả không rõ. Với một worker,
request như vậy bị từ chối ngay (502). Bắt đầu write freeze khi không có import
nào đang chạy.

**Release identity (P16-S3).** Một release là tag `PARTFLOW_RELEASE` cộng commit
đầy đủ 40 ký tự `PARTFLOW_COMMIT`; cả hai image của một release được build từ
một commit và mang cùng identity. Build argument `PARTFLOW_RELEASE` và
`PARTFLOW_COMMIT` được `compose.production.build.yaml` truyền cho `backend` và
`web`; build thất bại nếu thiếu tag hợp lệ (chữ, số, `.`, `_`, `-`, tối đa 64,
không phải `development`) hoặc thiếu commit 40 hex viết thường. Image `web` lưu
chúng thành image label `org.opencontainers.image.version` và
`org.opencontainers.image.revision` và nhúng tag vào bundle
(`VITE_PARTFLOW_RELEASE`) và vào meta tag `partflow-release` của shell được phục
vụ; backend đọc `RELEASE_TAG` và `RELEASE_COMMIT` (mặc định `development` và
không có ngoài release image) và báo chúng trong `GET /api/health` (`release`,
`commit`) và `GET /api/health/live`. Compose không bao giờ đặt `RELEASE_TAG`.
Một page gửi release của bundle trong request header `X-PartFlow-Release`; so
sánh là bằng chuỗi chính xác. Tag đã có không bao giờ build lại. Stage `production` của backend có cùng build argument, cùng kiểm tra release và commit và cùng label như `web`.

**Readiness và write gate (P16-S3).** `GET /api/health/live` trả
`{"status":"live","service","release","commit"}` mà không chạm database và là
thứ mọi container health check dùng, nên schema mismatch không bao giờ làm
container unhealthy. `GET /api/health` là readiness: nó đọc database revision
(thay cho `SELECT 1` trước đây) và trả `200` với `"status":"ok"` chỉ khi schema là
`current` hoặc `accepted`, `503 not_ready` khi `mismatch`, và `503 unavailable`
với `"schema":"unknown"` khi không kết nối được database. Các key là `status`,
`service`, `database`, `release`, `commit`, `schema`, `expected_revision`,
`database_revision` và `accepted_revision`. `schema` là `current` (database
revision bằng Alembic head duy nhất của image), `accepted` (database ở đúng một
revision được `PARTFLOW_ACCEPT_SCHEMA_REVISION` nêu tên, mà image không biết),
`mismatch` hoặc `unknown`. Override theo từng revision và chỉ được chấp nhận cho
revision mà image này không biết (rollback path 2,
`deployment/OPERATIONS_RUNBOOK.md` §6); nêu tên revision mà image biết thì bị bỏ
qua kèm warning, và `revision` báo `override_ignored`. Backend từ chối mọi `POST`,
`PUT`, `PATCH` và `DELETE`, trên mọi path, trước routing, CSRF hay bất kỳ lần đọc
body nào, trong hai trường hợp, cả hai không ghi gì và có `Cache-Control:
no-store`: `409` với `release_mismatch: true` khi enforcement bật và
`X-PartFlow-Release` của request thiếu hoặc khác `RELEASE_TAG`; `503` với
`not_ready: true` khi schema là `mismatch` hoặc không xác nhận được readiness (gate
fail closed). Read và health không bao giờ bị gate. Enforcement
(`ENFORCE_CLIENT_RELEASE`) được cố định bật trong `compose.production.yaml` và tắt
ở development và test; nó từ chối khởi động với tag `development`. Write duy nhất
trên safe method là việc ghi `last_seen_at` của một Scan Station device (không có
dữ liệu production); nó được chấp nhận khi mismatch. Client coi 409
`release_mismatch` là từ chối dứt khoát (request đó không ghi gì, và một lần thử
trước chưa có câu trả lời vẫn là unknown) và 503 là unknown outcome; trạng thái
màn hình là `GUI_DESIGN.md` §3 rule 13.

**Migration job và `revision` (P16-S3).** `python -m app.cli migrate` áp các
migration đang chờ trong một transaction trên một connection, chạy hook
`apply-grants` trong cùng transaction (nó áp dụng grant, xem Database role và grant) và in một báo cáo
JSON; nó cần đúng một trong `--pre-release-backup NAME` (một backup directory do
`backup.sh` lấy, được verify trước migration và được ghi lại; khi có migration đang
chờ nó còn cần `--backup-not-before UTC`, thời điểm write-freeze) hoặc
`--no-backup-reason TEXT` (được ghi lại; ví dụ `first install: empty database`) và
nhận `--lock-timeout SECONDS` (1-600, mặc định 30). `--backup-dir` nêu tên mount
(mặc định `/backups`). Quy tắc freshness và ownership nằm ở
`deployment/OPERATIONS_RUNBOOK.md` §5. Nó từ chối, không đổi gì, một revision file có DDL
không transactional (`autocommit_block`, `CONCURRENTLY`), một database revision mà
release này không biết, một `migrate` đồng thời, và (best effort, không phải bằng
chứng `backend` đã dừng) một backend session đang kết nối; database đã ở head trả
`already_current`. Một từ chối về role hoặc grant (`roles_not_provisioned`, `roles_incomplete`, `role_unsafe`, `role_owns_objects`, `foreign_grantor`, `table_unclassified`, `table_missing`, `not_superuser`) là `refused` với exit 1 và rollback upgrade của cùng lần chạy. Backend không bao giờ migrate. `python -m app.cli revision` chỉ
đọc và in release identity, revision mong đợi và revision của database, các
revision đang chờ và readiness mà backend sẽ báo; exit 0 chỉ khi schema là
`current`.

**Cài đặt đầu tiên (P16-S3, mở rộng bởi P16-S4).** Từ release checkout, với tag trong
`.env.production`, theo thứ tự: tạo backup directory (`install -d -m 0700 <path>`) và đặt `PARTFLOW_BACKUP_DIR` trong `.env.production` (mọi lệnh Compose của stack cần key này); tạo ba secret file (`postgres_password`, `partflow_app_password`, `partflow_maintenance_password`) trước mọi lệnh `$PF` khởi động `backend` hoặc một ops service, vì Compose thay file thiếu bằng một directory rỗng; build **cả hai** image bằng `PARTFLOW_COMMIT=$(git rev-parse HEAD) $PF -f compose.production.build.yaml build`; khởi động database (`$PF up -d db`); tạo role (`$PF --profile ops run --rm -T db-roles`); áp schema và grant (`$PF --profile ops run --rm -T migrate --no-backup-reason "first install: empty database"`); khởi động `backend` với một worker cho first-run setup như Process model mô tả, rồi `$PF up -d backend web`; cuối cùng chạy `reconcile` và kỳ vọng check (h) `pass`.

**Chuyển một stack cài trước P16-S4** (chỉ rehearsal stack; chưa có pilot nào trước P16-S7). Database phải đã ở head của candidate, nếu không `apply-grants` từ chối `revision_mismatch` và không đổi gì. Theo thứ tự: (1) tạo `partflow_app_password` và `partflow_maintenance_password` trước mọi lệnh `$PF` của Compose file mới chạy `backend` hoặc `db-roles` (xóa directory rỗng mà Compose đã tạo ở đường dẫn thiếu); (2) build cả hai image, không bao giờ chỉ `backend` và không bao giờ thiếu `PARTFLOW_COMMIT`: `PARTFLOW_RELEASE=<tag> PARTFLOW_COMMIT=$(git rev-parse HEAD) $PF -f compose.production.build.yaml build backend web`; (3) `PARTFLOW_RELEASE=<tag> $PF --profile ops run --rm -T db-roles`; (4) `PARTFLOW_RELEASE=<tag> $PF --profile ops run --rm -T db-roles apply-grants`; (5) `deploy/production/release.sh` như thường lệ. Các bước đầu của nó chạy image hiện tại qua `backend` bằng `partflow_app`, nên bước 3 và 4 phải đến trước, và bước build sau đó dùng lại image của bước 2.

**Chuyển một stack cài trước P16-S5** (chỉ rehearsal stack; chưa có pilot nào trước P16-S7). Theo thứ tự: (1) tạo backup directory (`install -d -m 0700 <path>`, thuộc account chạy `backup.sh` và `release.sh`) và đặt `PARTFLOW_BACKUP_DIR` trong `.env.production` **trước mọi lệnh `$PF` với checkout P16-S5**, nếu không mọi lệnh Compose thất bại vì thiếu key; (2) `deploy/production/release.sh` như thường lệ: pre-release backup đầu tiên của nó dùng `backup-tools` của candidate (`--tools-release`), vì release đang chạy có trước P16-S5; (3) cài daily schedule và task của platform tool (`deployment/SYNOLOGY_NAS.md` §6, `deployment/VPS.md` §8); (4) chạy drill đầu tiên, `deploy/production/restore-test.sh --backup <NAME> --operator "<name>"`.

**Release (P16-S3).** `deploy/production/release.sh` chạy release sequence của §7
từ repository root của release checkout với release tag đã checkout
(`deploy/production/release.sh --help` in cách dùng): `--release TAG --operator
NAME --approver NAME`, tùy chọn một trong `--pre-release-backup NAME` (một backup
`backup.sh` đã có, chỉ cho release không có migration đang chờ) hoặc
`--no-backup-reason TEXT` (cài đặt đầu tiên hoặc rehearsal); khi không có option
nào, `release.sh` tự lấy pre-release backup đã verify, bên trong write freeze khi có
migration đang chờ (P16-S5); tùy chọn `--accept-pre-release-findings` (tiếp tục khi
pre-release reconcile có finding và chỉ chặn với finding không có trong đó) hoặc
`--skip-pre-reconcile REASON` (không image nào có database revision làm head:
trạng thái rollback path 2; khi đó mọi finding sau release đều chặn),
`--env-file`, `--records-dir` và các trường record `--environment`, `--url`,
`--rollback-deadline`, `--observation-owner` và `--known-limitations`;
`--rehearsal --project NAME` chạy trên Compose project tạm (không bao giờ
`partflow-production`). Exit code: 0 hoàn tất; 1 dừng khi chưa đổi gì hoặc write
đã mở lại trên release hiện tại; 2 không chạy được; 3 `backend` bị để dừng (làm
theo `deployment/OPERATIONS_RUNBOOK.md` §6); 4 release mới có thể đang chạy và
ghi được sau một check thất bại; 130 hoặc 143 bị ngắt bởi Ctrl-C hoặc TERM (record
ghi tên bước; `migrate` bị ngắt có kết quả không rõ). Output của từng bước và `record.json` (record ở
`OPERATIONS_RUNBOOK.md` §1) được ghi vào `<records-dir>/<UTC>-<tag>/` (records
directory mặc định `$HOME/partflow-deployments`, mode 0700).
`deploy/production/smoke.sh --release TAG [--env-file …] [--project NAME]
[--allow-accepted-schema]` chạy các loopback check (shell được phục vụ và release
của nó, SPA fallback, release identity và schema của health và liveness, JSON 404
cho API path không tồn tại, 409 của gate khi thiếu release header và việc nó cho
đi qua khi có header, và image identity đang chạy). Không gì lên lịch cho
`release.sh`; không có updater nào chạy trên host, và không script nào xóa, prune
hay re-tag image.

**Rollback (P16-S3, P16-S5).** Cây quyết định, path 1 và 2 và schema override nằm trong
`deployment/OPERATIONS_RUNBOOK.md` §6; image của release trước ở lại host trong
suốt rollback window. Path 3 (restore pre-release backup vào một database mới)
nằm trong cùng mục; nó cần sự phê duyệt của owner được ghi lại.

**Request limit và timeout (`web`).**

| Route | Giới hạn body | Upstream timeout |
| --- | --- | --- |
| Mặc định cho mọi route `/api` | 1 MiB | 60 s |
| `PUT /api/workers/{id}/avatar`, `PUT /api/users/{id}/avatar`, `PUT /api/part-numbers/image` | 4 MiB | 60 s |
| `POST /api/work-orders/import/preview` | 4 MiB | 60 s |
| `POST /api/work-orders/import` | 4 MiB | 180 s (read và send) |

Giới hạn 4 MiB cao hơn giới hạn của chính application (ảnh 2 MiB, file import
1 MiB), nên JSON 413 của application thắng với file quá cỡ nhưng hợp lý; `web`
chỉ trả lời trước với body lớn hơn rõ rệt. `web` không bao giờ retry request
lên upstream: một request của client là nhiều nhất một lần thực thi ở backend.

**Rate limit (`web`, theo client IP đã forward).** `POST /api/session` 10 mỗi
phút, burst 5 (sáu lần thử cùng lúc, rồi thêm một lần mỗi 6 s); `PUT
/api/session/password`, `POST /api/setup/administrator` và `PUT
/api/users/{user_id}/password` 5 mỗi phút, burst 4 (năm lần cùng lúc, rồi thêm
một lần mỗi 12 s). Các lệnh đọc như `GET /api/session` và sign-out không bao giờ
bị giới hạn. Mã 429 từ `web` không bao giờ đến application, nên không bao giờ
được tính là sign-in thất bại và không bao giờ khóa account; khóa theo account
và giới hạn theo IP là hai lớp độc lập. Mã kích hoạt thiết bị không bị rate
limit (50 bit entropy, hiệu lực 15 phút).

**Response do `web` sinh ra.** Chúng chỉ xuất hiện với điều kiện `web` tự phát
hiện; mọi response của backend, gồm 401, 403, 409, 413, 422 và 503 của chính
application, đi qua không đổi.

| Status | Khi nào | `detail` của body | Ý nghĩa với client |
| --- | --- | --- | --- |
| 413 | body vượt giới hạn của location | `This request is too large for PartFlow. Nothing was changed.` (`request_too_large: true`) | từ chối dứt khoát |
| 429 | vượt rate limit (gửi `Retry-After: 60`) | `Too many attempts from this computer. Wait a minute, then try again. Nothing was changed.` (`rate_limited: true`) | từ chối dứt khoát |
| 502 | backend không với tới được, hoặc nó đóng kết nối trước khi trả lời | `The PartFlow server did not complete the request. If you were saving a change, check whether it was saved before repeating it.` (`server_unavailable: true`) | kết quả write không rõ |
| 504 | backend không trả lời trong timeout | `The PartFlow server did not answer in time. If you were saving a change, check whether it was saved before repeating it.` (`server_unavailable: true`) | kết quả write không rõ |

502 hoặc 504 giữ nguyên status 5xx, nên một station write có thể đã commit được
hiển thị là không rõ và retry với cùng `device_event_id`
(`deployment/OPERATIONS_RUNBOOK.md` §2). Frontend hiển thị thông báo built-in
cho 413 hoặc 429 không mang JSON `detail` (ví dụ từ một platform proxy). `web`
không thêm CORS header và không viết lại gì khác.

**Caching và header.** `index.html` và mọi single-page-app fallback là
`no-cache`; `/assets/*` immutable một năm, và asset thiếu là 404 thuần, không
bao giờ là application shell. Mọi response mang `X-Content-Type-Options:
nosniff`, `Referrer-Policy: same-origin` và `Content-Security-Policy` nền tảng
(script, style, connection và frame cùng origin; ảnh `blob:` và `data:` cho
preview ảnh cục bộ; không inline script). Ai thêm tính năng cần nguồn khác thì
sửa policy trong cùng thay đổi. HSTS thuộc platform proxy và được xem xét ở
P16-S7.

**Client address thật.** `web` chỉ tin cậy forwarded header từ đúng một hop, được
phát hiện khi container khởi động là default gateway của nó trên edge network
(địa chỉ mà kết nối loopback của host đi đến), hoặc đặt tường minh bằng
`PARTFLOW_TRUSTED_PROXY` (một địa chỉ IPv4). Nếu không có địa chỉ nào, container
từ chối khởi động. Từ hop đó `web` lấy địa chỉ `X-Forwarded-For` **cuối cùng** làm
client và thay header gửi cho backend bằng đúng địa chỉ đó; `X-Forwarded-Proto`
chỉ được chấp nhận từ cùng hop. `web` không publish certificate và không đọc cấu
hình TLS.

**Request log.** Backend ghi một application record JSON (logger `app.access`) cho
mỗi write, refusal, failure và slow read, mang HTTP request id (`X-Request-ID`) mà
mọi response trả về. `web` ghi một edge record JSON cho mỗi request (client
address, method, path không có query string, status, bytes, duration, upstream
status, user agent, `request_id`); cả hai log mang cùng HTTP request id
(`X-Request-ID`): `web` chấp nhận giá trị của client gồm 1-64 ký tự `A-Za-z0-9._-`
hoặc tự sinh một id, chuyển nó cho backend và ghi đè bản của backend trong header.
Không log nào chứa cookie, query string hay header PartFlow nào; health probe bị
loại khỏi log của `web` và nằm dưới INFO trong log của backend. Access log của
chính uvicorn vẫn tắt.

**Logging và monitoring (P16-S6).** Backend production start với
`--log-config app/core/logging.production.json`: mọi record, kể cả của uvicorn, là
một dòng JSON trên stderr (`ts`, `level`, `logger`, `message`, `request_id`, rồi
các field của record; một traceback không bao giờ chứa message của exception
database, nên không có dòng `DETAIL`, statement hay giá trị parameter nào lọt vào
log). Request id là `X-Request-ID` của request khi dài 1-64 ký tự thuộc
`A-Za-z0-9._-`, nếu không là một id 32-hex mới; backend echo đúng một
`X-Request-ID` trên mọi response. Record `app.access` được ghi ở INFO cho write,
designed refusal (`not_ready`, `release_mismatch`, `password_check_busy`,
validation, authorization) và read từ một giây trở lên (`"slow":true`), ở ERROR cho
failure, và ở DEBUG cho health poll hoặc read nhanh thông thường. Nó nêu PN,
QuantityFlow, Work Order Demand, Area, Operation, Machine, Worker (chỉ sau identity
resolution), Scan Station và `device_event_id` của một production command cùng lý do của một
refusal, và không bao giờ nêu request body, query string, cookie, token, badge,
password hay giá trị đã scan; setup token first-run vẫn là ngoại lệ duy nhất được
công bố. `db` chạy với `log_error_verbosity=terse`. Container rotate bằng driver
json-file (`max-size` 10m, `max-file` 5); khoảng 1,1 MB log backend và 0,6 MB log `web`
cho mỗi 1.000 command được đo trong rehearsal tổng hợp, vốn không có display polling
và đo output của `docker compose logs`, không phải kích thước file json-file trên
disk: mỗi board hoặc màn hình Tracking đang mở thêm record `web` mỗi 15 s, nên
ngân sách giữ log tính theo từng nguồn (`deployment/OPERATIONS_RUNBOOK.md` §2). Service ops `status`
(`python -m app.cli status`, profile `ops`) là báo cáo chỉ đọc bằng application
role với backup directory mount read-only; chạy với
`--no-deps -T --user "$(id -u):$(id -g)"`. `deploy/production/check.sh` đánh giá
HTTPS readiness, certificate, container, restart, backend error, disk và báo cáo
`status` với ngưỡng OD-16-11 (backup cũ hơn 26 giờ, disk còn trống dưới 15 %,
certificate hết hạn trong 21 ngày, restart count tăng, bất kỳ record error nào của
backend) và thoát khác 0 để tới failure notification của host scheduler;
`deploy/production/scheduled-reconcile.sh` chạy `reconcile` hằng ngày và áp quy tắc
exit code của nó. Command, schedule và ngưỡng nằm ở
`deployment/OPERATIONS_RUNBOOK.md` §2, §7 và §9 và ở các platform guide
([`deployment/SYNOLOGY_NAS.md`](./deployment/SYNOLOGY_NAS.md) §8,
[`deployment/VPS.md`](./deployment/VPS.md) §8); cài chúng và chứng minh một
notification trên host là P16-S7.

**Yêu cầu với platform proxy.** DSM reverse proxy (hoặc Caddy) phải: kết thúc
HTTPS bằng certificate mà workstation công ty tin cậy; chỉ gửi HTTP dưới dạng
redirect sang HTTPS; forward đến `http://127.0.0.1:<port>` (đúng literal
`127.0.0.1`, không bao giờ `localhost`, vì có thể resolve ra `::1` trước trong
khi `web` chỉ publish trên IPv4 loopback) cùng `Host`, `X-Forwarded-For` (client
address được append ở cuối) và `X-Forwarded-Proto: https`; nhận request body tối
thiểu 5 MiB và dùng send/read timeout tối thiểu 300 s, để JSON của `web` hoặc của
application thắng; không bao giờ log cookie, `X-PartFlow-Station-Device` hay
`X-PartFlow-CSRF`; và chỉ cho phép các nguồn LAN hoặc VPN đã duyệt, cùng với
firewall của host. Nếu proxy không cung cấp được client address, mọi client dùng
chung một rate-limit bucket; ghi lại và để owner quyết định. Thiết lập DSM nằm ở
[`deployment/SYNOLOGY_NAS.md`](./deployment/SYNOLOGY_NAS.md) §5 và ví dụ Caddy ở
[`deployment/VPS.md`](./deployment/VPS.md) §4. Các yêu cầu này đã được ghi tại
đây; thiết lập trên host **được thực hiện và xác minh ở P16-S7**.

**Quy trình certificate.** Certificate nêu hostname PartFlow (SAN) và do CA ACME
công khai (tên DNS public) hoặc CA nội bộ của công ty (tên chỉ dùng nội bộ) cấp;
không bao giờ self-signed theo từng host và không bao giờ được chấp nhận theo
từng workstation bằng cách bỏ qua cảnh báo của browser. CA cấp certificate nội bộ
được phân phối đến workstation và barcode terminal bằng device management của
công ty. Deployment administrator sở hữu việc gia hạn; hạn dùng được giám sát bởi
`check.sh` `certificate` (alert dưới 21 ngày, `deployment/OPERATIONS_RUNBOOK.md`
§9). Kiểm tra hạn dùng từ bất kỳ client nào: `openssl s_client -connect
<host>:443 -servername <host> </dev/null 2>/dev/null | openssl x509 -noout
-subject -enddate`. Các bước theo nền tảng nằm ở SYNOLOGY_NAS §5 và VPS §4. Thực
hiện và xác minh ở P16-S7.

**Tách biệt environment (OD-16-01).** Production dùng Compose project, database
volume, thư mục secret, hostname và vị trí backup riêng (`PARTFLOW_BACKUP_DIR`); không cái nào
được dùng chung hay trỏ vào stack staging hoặc development. Production bắt đầu từ
database volume mới, rỗng, rồi migration, rồi first-run setup; dữ liệu
staging hoặc development không bao giờ được attach, tái sử dụng hay copy vào đó,
và việc restore có chủ đích vào production theo `deployment/OPERATIONS_RUNBOOK.md`
§6 path 3 hoặc §8 host hỏng, và sự phê duyệt của owner được ghi lại trước khi
`backend` start. Trong thời gian pilot, pf-managed staging không được cài trên Docker daemon
của pilot, và staging thủ công bằng `compose.yaml` ở SYNOLOGY_NAS §4 được dừng,
container được xóa và **không** xóa volume, trước khi production khởi động.
`SITE_TIMEZONE` bằng giá trị của staging (§6). Không bao giờ chạy `down -v` (hay
xóa volume) trên production project: nó xóa database.

**Bảng kê configuration và secret.** `.env.production` (sao chép từ
`.env.production.example`, bị git bỏ qua, mode 600) chỉ chứa giá trị không phải
secret; tập các tham chiếu `${NAME}` trong `compose.production.yaml` bằng tập key
của file example (có test).

| Key | Ý nghĩa | Bắt buộc / mặc định |
| --- | --- | --- |
| `PARTFLOW_RELEASE` | tag của image **đang chạy** (§10) | bắt buộc |
| `PARTFLOW_COMMIT` | commit đầy đủ của release đang được build; rỗng trong file, `release.sh` truyền trong shell lúc build | bắt buộc để build |
| `PARTFLOW_ACCEPT_SCHEMA_REVISION` | chỉ cho rollback path 2: đúng một database revision mà release đang chạy được phục vụ (`OPERATIONS_RUNBOOK.md` §6); `release.sh` xóa nó | rỗng |
| `PARTFLOW_SECRETS_DIR` | đường dẫn tuyệt đối của thư mục secret, ngoài checkout, chỉ cho production (thư mục 0700, mỗi file 0444) | bắt buộc |
| `PARTFLOW_BACKUP_DIR` | đường dẫn tuyệt đối của backup directory production (`backup.sh` ghi một thư mục con cho mỗi backup), ngoài checkout, thư mục secret và mọi archive directory, chỉ cho production (không bao giờ của staging hay của drill); một directory đã tồn tại, mode 0700, thuộc account chạy `backup.sh` và `release.sh`; **mọi** lệnh `$PF` đều cần, viết không có dấu nháy; được mã hóa và sao chép off-host bởi platform tool | bắt buộc |
| `PARTFLOW_SITE_TIMEZONE` | múi giờ lịch nhà máy, bằng staging (§6) | bắt buộc |
| `PARTFLOW_HTTP_PORT` | loopback port mà platform proxy kết nối | bắt buộc (ví dụ `18080`) |
| `POSTGRES_USER`, `POSTGRES_DB` | role bootstrap (owner) và database | bắt buộc (ví dụ `partflow_owner`, `partflow`) |
| `PARTFLOW_BACKEND_WORKERS` | số uvicorn worker | `2` |
| `PARTFLOW_EDGE_SUBNET` | subnet của network `edge`; cũng là trusted proxy của uvicorn | `172.30.250.0/24` |
| `PARTFLOW_TRUSTED_PROXY` | một địa chỉ IPv4 mà `web` tin cho forwarded header; rỗng = tự phát hiện | rỗng |
| `PARTFLOW_{DB,BACKEND,WEB,OPS}_{MEMORY,CPUS}` | giới hạn tài nguyên | `1g`/`1.0`, `1g`/`2.0`, `128m`/`0.5`, `512m`/`1.0` |

Cố định trong Compose, không cấu hình được: `SESSION_COOKIE_SECURE=true`,
`ENFORCE_CLIENT_RELEASE=true`,
`DATABASE_HOST=db`, `DATABASE_PORT=5432`, các đường mount secret và loopback bind.
Các secret file là `postgres_password` (owner), `partflow_app_password` và
`partflow_maintenance_password`, mỗi file đúng một dòng; hai role file dài 16 đến 128 ký tự ASCII in được không có khoảng trắng và khác nhau
(ví dụ `openssl rand -base64 32`). `db` chỉ đọc `postgres_password` **khi một data volume mới được khởi tạo**; `migrate` và `db-roles` đọc nó ở mỗi lần start, và `backend` không bao giờ mount nó. Để đổi nó trên
database đã có, chạy `ALTER ROLE <POSTGRES_USER> PASSWORD …` trong `db` trước, rồi
thay file (không cần restart service). `backend` chỉ mount `partflow_app_password`, đọc một lần mỗi process: password của một role được
đổi bằng cách thay file của nó, `$PF stop backend`, `$PF --profile ops run --rm -T db-roles`, rồi `$PF up -d --force-recreate --no-deps backend`
(`deployment/OPERATIONS_RUNBOOK.md` §9). `release.sh` từ chối, không đổi gì, khi bất kỳ file nào trong ba file thiếu, rỗng hoặc không phải regular file.

**Lệnh vận hành.** Chạy từ release checkout, với
`PF="docker compose -f compose.production.yaml --env-file .env.production"`.

| Mục đích | Lệnh |
| --- | --- |
| Preflight (§6, tách biệt môi trường) | `docker ps -a --format '{{.Label "com.docker.compose.project"}}' \| sort -u` không liệt kê `partflow-staging` |
| Validate configuration | `$PF config --quiet` |
| Build một release (tag và commit trong shell; cách dùng duy nhất của file build) | `PARTFLOW_RELEASE=<new> PARTFLOW_COMMIT=$(git rev-parse HEAD) $PF -f compose.production.build.yaml build` |
| Khởi động database | `$PF up -d db` |
| Áp dụng migration và grant (một lần mỗi release, khi `backend` đang dừng) | `PARTFLOW_RELEASE=<new> $PF --profile ops run --rm -T --user "$(id -u):$(id -g)" migrate (--pre-release-backup NAME --backup-not-before UTC \| --no-backup-reason TEXT)` (cài đặt đầu tiên: tag đã nằm trong `.env.production`) |
| Thực hiện backup (daily, manual) | `deploy/production/backup.sh --kind daily --operator … --keep-daily N --keep-weekly N` · `--kind manual --operator … --reason …` (`OPERATIONS_RUNBOOK.md` §3) |
| Verify một backup, hoặc xem trước retention | `$PF --profile ops run --rm -T --user "$(id -u):$(id -g)" backup-tools backup-verify NAME` · `… backup-tools backup-rotate --keep-daily N --keep-weekly N --dry-run` |
| Restore drill trong project cô lập | `deploy/production/restore-test.sh --backup NAME --operator …` (`OPERATIONS_RUNBOOK.md` §4) |
| Tạo hoặc sửa database role; đặt hoặc rotate password của chúng | `$PF --profile ops run --rm -T db-roles` (= `provision-roles`) |
| Áp grant ngoài một migrate (sau restore, hoặc để sửa drift) | `$PF --profile ops run --rm -T db-roles apply-grants` |
| Guard-integrity check (reconcile check (h), bằng `partflow_app`) | `$PF run --rm --no-deps -T backend python -m app.cli reconcile --check h` |
| Privilege probe (bằng chứng; một lần gọi cho mỗi table và statement, không row nào, rollback) | `$PF exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=0 -c BEGIN -c "SET LOCAL ROLE partflow_app" -c "UPDATE part_movements SET id = id WHERE false" -c ROLLBACK'` (bị từ chối = stderr có `permission denied for table <t>`; được phép = stdout có `UPDATE 0` và không có `ERROR`; biến được expand bên trong `db`) |
| Release (toàn bộ sequence ở §7) | `deploy/production/release.sh --release <new> --operator … --approver … [--pre-release-backup NAME \| --no-backup-reason TEXT]` |
| Smoke check của release đang chạy | `deploy/production/smoke.sh --release <tag>` |
| Revision mong đợi và của database, readiness | `$PF run --rm --no-deps -T backend python -m app.cli revision` |
| Khởi động hoặc tạo lại application | `$PF up -d backend web` |
| Trạng thái và log | `$PF ps` · `$PF logs --since=15m backend web db` |
| Health (readiness: release, schema, revision) qua `web` trên host | `curl --fail --silent --show-error http://127.0.0.1:${PARTFLOW_HTTP_PORT}/api/health` · liveness: `…/api/health/live` |
| Reconciliation (cả khi `backend` đang dừng) | `$PF run --rm --no-deps -T backend python -m app.cli reconcile` |
| Rehearsal identity của candidate image (check (j)) | `PARTFLOW_RELEASE=<new> $PF run --rm --no-deps -T backend python -m app.cli reconcile --check j` |
| CLI recovery | `$PF run --rm --no-deps backend python -m app.cli reset-password …` · `… restore-correction-permission-management …` |
| Write freeze | `$PF stop backend` (có thể chờ đến 200 s khi một import đang chạy, và request gửi trong lúc đó treo đến 60 s, xem Process model: bắt đầu khi không có import nào đang chạy) · mở lại bằng `$PF up -d backend` trên cùng release; ở một release switch, việc mở lại là bước chuyển `web` của `release.sh` (§7) |
| First-run setup | `PARTFLOW_BACKEND_WORKERS=1 $PF up -d backend`, đọc token bằng `$PF logs backend \| grep "Setup token"`, hoàn tất setup, rồi `$PF up -d backend` |

**Release sequence.** `deploy/production/release.sh` thực hiện release sequence
của §7; dạng thủ công tương đương, cùng thứ tự, nằm trong
`deployment/OPERATIONS_RUNBOOK.md` §5. `.env.production` luôn ghi release **đang
chạy**; tag candidate chỉ nằm trong shell (hoặc trong environment từng lệnh của
script) cho đến lúc switch, nên mọi CLI recovery, reconcile hay `up` trước
switch đều dùng image hiện tại với schema hiện tại.

Rollback code application khi không đổi schema: khôi phục tag trước trong
`.env.production` và `$PF up -d backend web`. Việc này cần image của release
trước còn trên host, nên giữ chúng (không `docker image prune -a`) trong suốt
rollback window; nếu chúng đã mất, `up` thất bại với `No such image` và không
khởi động gì. Rollback schema là đường restore
của runbook (path 3, `deployment/OPERATIONS_RUNBOOK.md` §6), không bao giờ là down-migration ở đây. **Không bao giờ
chạy `down -v`, hay xóa volume, trên project `partflow-production`**: nó xóa
`partflow-production_postgres_data`; `$PF down` không có `-v` là dạng dừng-tất-cả
duy nhất.

**Bằng chứng.** 29 static test trong `deploy/production/tests` (Compose model,
file environment example, Dockerfile và cấu hình nginx; CI chạy, và đã được kiểm
tra bằng 21 đột biến có chủ đích của artifact), các lần build production image
(build `web` chạy production-boundary check: 59 asset, không có sentinel nào
trong 11 mock sentinel), và Compose stack smoke trên Docker Desktop (các case
SM-1…SM-22, gồm rate limit, các câu trả lời JSON do proxy sinh, content security
policy trong trình duyệt thật, first-run một worker, các lệnh reconcile, và thời
gian import 2.000 Work Order là 18,06 s để tạo và 31,33 s để đổi quantity, thấp
hơn 180 s của `web`). Bằng chứng P16-S3: bộ static và script production (93 test, đều pass), 51 test release-script và reconcile-regression pass dưới `dash` trong Linux container, `sh -n` trên cả hai script, cả hai production image được build với commit đủ và build fail khi thiếu, Compose stack smoke pass (SM-1…SM-19 và SM-21…SM-28; SM-20 là browser check thủ công của S2), release rehearsal pass (RH-1…RH-8 và RH-10: một release có migration, write freeze, switch hai bước, migration bị từ chối khi backend đang kết nối, và độ trễ `/api/health` và `/api/health/live` qua `web`), và browser check update notice cùng automatic reload của kiosk Production Board trên rehearsal stack. Chưa chạy (P16-S7): các case reload của Scan Station cần Area, Operation và station đã enroll được cấu hình, và mọi host check NAS và VPS. Bằng chứng P16-S4: bộ static và script production (98 test trên host và 63 test release-script, reconcile-regression và rehearsal dưới `dash` trong Linux container), Compose stack smoke pass ở lần chạy thứ hai (39 case tự động, gồm privilege probe, việc rotate password của role và các check secret file; SM-20 là browser check thủ công của S2), và release rehearsal pass (RH-1…RH-8 và RH-10, cùng việc chuyển một stack cài trước P16-S4 là RH-11 và việc nó bị từ chối khi thiếu role file là RH-11b). Bằng chứng P16-S5: bộ static và script production (154 test trên host, cùng 116 test backup-script, release-script, reconcile-regression và rehearsal dưới `dash` trong Linux container), `sh -n` sạch trên `release.sh`, `backup.sh`, `restore-test.sh` và `smoke.sh`, Compose stack smoke pass (38 case) và release rehearsal pass (RH-1…RH-8 và RH-10, với RH-5 và RH-11 dùng automatic backup) với backup directory tạm, và backup rehearsal (`deploy/production/tests/backup_rehearsal.py`) pass trên image thật với dữ liệu tổng hợp: một daily backup cùng `sha256sum -c` trên host, một isolated drill (reconcile (h) và (j) `pass`, một ảnh đã upload được đọc lại với SHA-256 bằng nhau, 14,2 s từ restore đến ready và 32,3 s tổng trên host đó, production không bị đụng tới), một drill trên `postgres:16.14-bookworm` (glibc collation version 2.36 so với 2.41, (h) và (j) `pass`), rollback path 3 từ một release bị để ở trạng thái stopped frozen (dump nằm trong freeze, các command được tài liệu hóa cùng các lần sửa `.env.production` và một vòng chờ health do harness thêm vào, database đã migrate được giữ lại; sau đó audit P16-S5 biến các block của RUNBOOK thành script `sh -eu` fail-closed có vòng chờ đó, đã kiểm bằng fake và chưa chạy lại trên stack), một restore thất bại có tính nguyên tử, các từ chối stale, tampered và đồng thời, và một backup lấy trong lúc có write. Chưa chạy: loại host thứ hai (DR-9) và hành vi fail-closed khi thiếu backup mount trên Linux engine (Docker Desktop tạo directory còn thiếu; P16-S7). Các host check trên Synology NAS và VPS không thuộc bằng chứng này.

## 4. Chọn nền tảng

| Nền tảng | Mức phù hợp | Vai trò đề nghị |
| --- | --- | --- |
| Synology NAS có Container Manager | Phù hợp cho deployment nội bộ nhỏ nếu model hỗ trợ đủ container và NAS có storage, RAM, monitoring, UPS cùng backup đã test | Staging nội bộ hiện tại; pilot/production chỉ sau khi qua gate Phase 16 |
| Linux VPS | Target portable lâu dài tốt nhất | Hướng nâng cấp production ưu tiên, cho phép kiểm soát Docker, remote access và phục hồi off-site độc lập |
| Shared hosting như Hawk Host | Có điều kiện, không phải drop-in | Chỉ dùng nếu provider chứng minh hỗ trợ ASGI/FastAPI native, PostgreSQL, routing, migration, job và recovery cần thiết; nếu không hãy dùng VPS |

Chuyển từ Synology sang VPS phải là redeploy release cộng với PostgreSQL
dump/restore đã xác minh, không phải viết lại ứng dụng.

## 5. Production release gate

PartFlow chỉ được vào pilot/production khi toàn bộ gate sau đã đạt.

### Ứng dụng và authorization

- Authentication và server-side role enforcement Phase 14 hoàn tất và đã test;
  ẩn navigation không bao giờ là authorization.
- Mọi thiết bị Scan Station đã được enroll, tên của nó được ghi lại, và thiết bị bị mất hoặc ngừng dùng được revoke. Enrollment code và device token là bearer credential: giống session cookie, chúng chỉ đi qua HTTPS hoặc LAN cô lập được chấp nhận chính thức (Network and host bên dưới), và reverse proxy không bao giờ log header `X-PartFlow-Station-Device`.
- `SESSION_COOKIE_SECURE=true` được đặt phía sau TLS. Setup token của first-run
  là secret duy nhất từng được ghi vào backend log: hoàn tất first-run setup
  trước khi mở service ra ngoài và hạn chế quyền đọc log cho đến lúc đó (structured
  log giữ nguyên ngoại lệ này; không secret, cookie, device token, badge hay
  request body nào khác bị log — test P16-S6).
- Mọi production view nằm trong pilot scope đều dùng API thật; không nhầm mock
  hoặc placeholder chưa kết nối với tính năng vận hành.
- Production write vẫn bị block khi mất kết nối và không bao giờ được queue cục
  bộ.
- Toàn bộ quality gate của repo và migration test pass trên đúng release commit.

### Production artifact

- backend image production không chạy reload server và có process model được
  ghi rõ (mỗi process backend thông báo setup token first-run riêng của nó khi chưa
  có Administrator; lần tạo đầu tiên đóng setup cho tất cả) — **đã triển khai**
  (stage `production` của `backend/Dockerfile`; process model ở §3.1);
- frontend production là Vite build bất biến do production web server phục vụ —
  **đã triển khai** (`web`, §3.1);
- production Compose có restart policy, health check, private network,
  persistent volume, resource limit thận trọng và không có development bind
  mount — **đã triển khai** (`compose.production.yaml`, §3.1; resource limit là
  giá trị khởi đầu, đo ở P16-S7);
- reverse proxy chịu trách nhiệm TLS, SPA fallback, request limit và route
  `/api` — proxy phải nhận request body tối thiểu 3 MiB trên các route upload ảnh
  (`PUT /api/workers/{id}/avatar`, `PUT /api/users/{id}/avatar`, `PUT /api/part-numbers/image?number=…`), vì
  application nhận ảnh đến 2 MiB; mã 413 do proxy tự sinh không có JSON `detail`,
  nên UI chỉ có thể hiện lỗi chung; tương tự, proxy phải nhận request body tối
  thiểu 2 MiB trên các route import Work Order (`POST /api/work-orders/import/preview`
  và `POST /api/work-orders/import`), vì application nhận file đến 1 MiB và JSON
  413 của nó phải thắng, và read timeout trên `POST /api/work-orders/import` phải
  vượt trường hợp xấu nhất đã đo — một lần chạy trên môi trường development với
  file lớn nhất được phép (2.000 Work Order một line, mỗi Work Order một
  transaction) mất 26,03 s để import (0,3 s để check, 7,48 s để replay thành
  đã-import), và một lần chạy trong đó mỗi Work Order trong số đó đổi quantity
  (PF-2) mất 27,23 s để import (2,45 s để check; replay lại chính file đó, khi
  tất cả đã như đã lưu, mất 2,48 s để check và 2,45 s để import); vẫn khuyến nghị read timeout tối thiểu 120 s. `web` triển khai các giới hạn này với giá trị ở §3.1 (4 MiB trên
  năm route đó, 1 MiB ở nơi khác, read timeout import 180 s) — **đã triển khai**;
  platform proxy phía trước nó phải nhận tối thiểu 5 MiB và dùng timeout tối
  thiểu 300 s, và TLS kết thúc ở đó — đã ghi ở §3.1, **thực hiện và xác minh ở
  P16-S7**;
- configuration bắt buộc được validate lúc startup và secret không có default
  đã commit — **đã triển khai** (§3.1: backend validate database setting; Compose
  cố định cookie setting và bắt buộc time zone cùng thư mục secret);
- image hoặc release version bất biến và được giữ đủ lâu để rollback code —
  **đã triển khai một phần** (image được gắn tag bằng `PARTFLOW_RELEASE`, không
  bao giờ pull và tag đã có không bao giờ build lại; image `web` mang release
  identity và deployment record giữ image ID của mọi release; image backend cũng mang nó, §3.1; image của release trước ở lại host
  trong suốt rollback window, `release.sh` từ chối bắt đầu khi chúng thiếu);
- readiness endpoint và backend write gate (page hoặc schema không khớp bị từ chối
  mà không ghi gì) — **đã triển khai** (§3.1, P16-S3).

Các gate ở trên vẫn là gate cho đến khi P16-S7 ghi nhận bằng chứng đạt.

### An toàn dữ liệu và vận hành

- PostgreSQL logical backup chạy tự động theo lịch, được mã hóa và sao chép
  off-host/off-NAS, có retention và monitoring — đã triển khai bởi `backup.sh`,
  `backup-rotate` và các task của platform tool (P16-S5; schedule, replication và
  alert tuổi backup là `check.sh` `backup_age`, P16-S6; schedule, replication và
  alert được chạy trên pilot host ở P16-S7); xem
  `deployment/OPERATIONS_RUNBOOK.md` §3;
- restore vào database cô lập đã được test và đo thời gian — đã triển khai bởi
  `restore-test.sh` (P16-S5); drill có đo thời gian đầu tiên trên pilot host là
  P16-S7 (`deployment/OPERATIONS_RUNBOOK.md` §4);
- mỗi migration có backup, forward plan, đánh giá compatibility, smoke test và
  recovery plan — backup đã verify do `release.sh` lấy bên trong write freeze và do
  `migrate` kiểm tra (P16-S5);
- rollback dùng release ứng dụng tương thích trước đó, hoặc restore database
  pre-migration khớp phiên bản nếu rollback schema không an toàn — path 3, đã
  triển khai (P16-S5; `deployment/OPERATIONS_RUNBOOK.md` §6), bằng chứng trên pilot
  host: P16-S7;
- health, log, disk, tuổi backup, tăng trưởng database và số lần container
  restart đều được monitor — đã triển khai bởi `check.sh` và `status` với ngưỡng
  OD-16-11 (P16-S6); cài và chứng minh trên pilot host: P16-S7;
- reconciliation check cho Movement/quantity chạy và alert nhưng không mutate
  dữ liệu — `scheduled-reconcile.sh` hằng ngày (P16-S6); schedule trên host
  P16-S7;
- backend kết nối bằng `partflow_app`, role không có UPDATE, DELETE hay TRUNCATE privilege trên history append-only (UPDATE chỉ trên `worker_sessions`, DELETE chỉ trên `assigned_route_steps`) — được chứng minh bằng privilege probe (§3.1) và reconcile check (h) sạch trên production database; raise-on-write trigger vẫn là lớp thứ nhất; một superuser (owner role) tắt trigger nằm ngoài khả năng phát hiện — đã chấp nhận (quyết định owner OD-16-09);
- database đã chạy các commit Phase 12 chưa phát hành (`80f7925` … `b9785d2`)
  phải qua check chỉ đọc này trước khi dựa vào Hot list — nó phải trả về 0 row,
  nếu không các inactive Hot entry được liệt kê phải được remove trong Management →
  Priority (automatic removal trong Phase 12 của `IMPLEMENTATION_ROADMAP.md` chỉ
  phủ các thay đổi sau nó):
  `SELECT d.id, d.priority_rank FROM work_order_demands d JOIN work_orders w ON w.id = d.work_order_id WHERE d.priority_rank IS NOT NULL AND (w.completed_at IS NOT NULL OR d.requested_quantity <= d.allocated_quantity);`
  Query này là check (i) của command read-only `reconcile`
  (`deployment/OPERATIONS_RUNBOOK.md` §7).
- incident owner, maintenance window, RPO và RTO được phê duyệt rõ;
- điều kiện bắt đầu pilot, kết thúc pilot và escalation được ghi lại.

### Network và host

- client dùng HTTPS hoặc ngoại lệ isolated LAN đã được chấp thuận chính thức;
- firewall chỉ cho phép source và port cần thiết;
- DSM/VPS, Container Manager/Docker và base image được cập nhật bảo mật có kiểm
  soát;
- đồng bộ thời gian NAS/VPS chính xác;
- host có UPS hoặc chiến lược mất điện được ghi rõ;
- cảnh báo capacity chừa đủ disk cho PostgreSQL, image update, migration tạm và
  backup — các check `disk_data`, `disk_backup`, `disk_docker` và `disk_archive`
  của `check.sh` alert khi còn trống dưới 15 % (P16-S6).

## 6. Tách biệt environment

Dùng database, secret, URL và vị trí backup riêng cho:

- `development` — chỉ dữ liệu developer;
- `staging` — dữ liệu giả hoặc đã sanitize, dùng diễn tập release;
- `production` — dữ liệu nhà máy đã được phép.

Không restore dữ liệu production vào development nếu chưa được phép và chưa
sanitize. Không bao giờ cho staging và production dùng chung database.

Mọi môi trường đặt `SITE_TIMEZONE` (tên múi giờ IANA; `UTC` khi không đặt) theo
lịch của nhà máy: backend validate giá trị này khi khởi động và derive done date
của Work Order đã completed — do đó cả Done range lẫn kết quả on time / late
của completed history — từ múi giờ này, không bao giờ từ giờ local của browser.
Staging và production phải dùng cùng một giá trị.

## 7. Luồng triển khai chung

Mọi nền tảng dùng cùng thứ tự release:

1. Chọn và ghi lại release commit/tag bất biến theo §10.
2. Xác nhận CI và quality gate trên đúng revision đó.
3. Đọc migration note từ revision đang chạy đến target.
4. Kiểm tra backup mới nhất và tạo backup pre-release mới: `release.sh` lấy nó
   bên trong write freeze khi có migration đang chờ (bước 6), đã verify, và
   `migrate` từ chối backup stale, của database khác hoặc bị tamper
   (`deployment/OPERATIONS_RUNBOOK.md` §3 và §5).
5. Build target image mà chưa thay thế release đang chạy (`release.sh` build
   candidate và không bao giờ chạm release đang chạy).
6. Vào write freeze (dừng `backend`, `OPERATIONS_RUNBOOK.md` §5) khi có migration
   đang chờ.
7. Chạy migration đúng một lần và lưu output: `migrate` áp nó trong một
   transaction, từ chối khi `backend` đang kết nối và từ chối DDL không
   transactional.
8. Chuyển application. `backend` khởi động trước trên release mới trong khi `web`
   vẫn phục vụ bundle trước, nên write vẫn bị release gate từ chối; sau khi health
   check đạt, `web` được chuyển và write mở lại.
   Với database chưa có Administrator,
   khởi động backend với một worker
   (`PARTFLOW_BACKEND_WORKERS=1 $PF up -d backend`, nên chỉ có một setup token,
   §3.1), hoàn tất first-run setup (setup token nằm trong backend log) trước khi
   mở truy cập, rồi khởi động lại với số worker đã cấu hình (`$PF up -d backend`). Sau đó enroll từng thiết bị Scan Station (Administration → Scan Stations).
9. Chạy health, API, UI, authorization, scan-focus và write/read-back smoke test
   bằng dữ liệu test được chỉ định.
10. Chạy quantity/Movement reconciliation (`release.sh` chạy reconcile pre-release
    và post-release; một check thất bại sau switch sẽ dừng `backend` lại).
11. Ghi revision đã deploy, migration head, operator, thời gian và kết quả
    (`release.sh` ghi `record.json`).
12. Giữ release trước và backup pre-release đến hết observation window.

`deploy/production/release.sh` và `smoke.sh` thực hiện các bước 5 đến 11 (§3.1,
Release). Command và điểm quyết định chi tiết nằm trong
[`deployment/OPERATIONS_RUNBOOK.md`](./deployment/OPERATIONS_RUNBOOK.md).

## 8. Hướng dẫn theo nền tảng

- [`deployment/SYNOLOGY_NAS.md`](./deployment/SYNOLOGY_NAS.md)
- [`deployment/VPS.md`](./deployment/VPS.md)
- [`deployment/SHARED_HOSTING.md`](./deployment/SHARED_HOSTING.md)
- [`deployment/OPERATIONS_RUNBOOK.md`](./deployment/OPERATIONS_RUNBOOK.md)

## 9. Tham chiếu nền tảng bên ngoài

Các nguồn sau mô tả capability của nền tảng, không chứng minh PartFlow đã sẵn
sàng:

- [Synology Container Manager](https://www.synology.com/en-us/dsm/feature/container-manager)
  ghi nhận hỗ trợ Project nhiều container từ Compose file.
- [Synology Container Manager Project help](https://kb.synology.com/en-us/DSM/help/ContainerManager/docker_project?version=7)
  là tài liệu UI để tạo và vận hành Project.
- [Hawk Host Python application guide](https://www.hawkhost.com/kb/programming/python/how-to-create-python-application/)
  mô tả triển khai Python bằng `mod_passenger`; riêng điều đó không chứng minh
  tương thích ASGI/FastAPI native.
- [Hawk Host remote PostgreSQL guide](https://www.hawkhost.com/kb/web-hosting/how-do-i-allow-remote-postgresql-connections/)
  xác nhận có PostgreSQL trong môi trường đó và remote access cần provider
  whitelist; vẫn phải xác minh capability của đúng plan trước khi chọn shared
  hosting.

## 10. Phát hành và quản lý phiên bản (Release and Versioning)

Mục này là nguồn chuẩn cho cách đặt tên release, release notes và checklist
phát hành của PartFlow. Release là một bản ứng viên triển khai có thể truy vết,
không phải bằng chứng đã sẵn sàng cho production. Các gate ở §5 vẫn áp dụng
cho pilot/production.

### 10.1 Định danh release

Dùng một application release chung cho frontend, backend và migration từ cùng
một commit. Ghi lại Git tag và commit SHA đầy đủ. Alembic revision định danh
riêng database schema; package metadata không phải lịch sử release. Release
identity được nhúng vào cả hai production image và được `GET /api/health` báo
(`release`, `commit`) (§3.1).

Tạo release cho phiên bản được chọn để test hoặc deploy, không phải cho mọi
commit. Deploy lại cùng phiên bản không cần tạo release mới. Không di chuyển,
ghi đè hoặc tái sử dụng tag của release đã phát hành, cũng không thay thế build
artifact đã phát hành của release đó. Nội dung release thay đổi phải dùng
version mới.

Dùng tag theo version, không dùng `Stage`, `Production`, `Latest` hoặc số phase.
Tag không theo version đã có có thể giữ làm mốc lịch sử, nhưng không được dùng
làm target triển khai liên tục thay đổi. Environment và phase nằm trong notes.

### 10.2 Quy ước version

Dùng [Semantic Versioning](https://semver.org/spec/v2.0.0.html), với tiền tố `v`
viết thường cho Git tag: `vMAJOR.MINOR.PATCH`, có thể thêm hậu tố `-alpha.N`,
`-beta.N` hoặc `-rc.N`. Bắt đầu `N` từ 1; không thêm số 0 ở đầu.

| Tình huống | Ví dụ |
| --- | --- |
| Snapshot development nội bộ đầu tiên được chọn cho staging | `v0.1.0-alpha.1` |
| Snapshot tiếp theo của cùng release đang dự kiến | `v0.1.0-alpha.2` |
| Mốc development mới có phạm vi chức năng lớn hơn đáng kể | `v0.2.0-alpha.1` |
| Đã triển khai phạm vi dự kiến; còn cần kiểm thử rộng hơn | `v0.2.0-beta.1` |
| Bản ứng viên cho lần phát hành production ổn định đầu tiên | `v1.0.0-rc.1` |
| Bản production ổn định đầu tiên được chấp nhận, sau khi đạt §5 | `v1.0.0` |
| Sửa lỗi tương thích sau `v1.0.0` | `v1.0.1` |
| Bổ sung tính năng tương thích sau `v1.0.0` | `v1.1.0` |
| Thay đổi phá vỡ contract được hỗ trợ sau `v1.x` | `v2.0.0` |

Đây là ví dụ, không phải tag đã dành trước hoặc chuỗi bắt buộc. Chọn version
chưa được dùng tiếp theo dựa trên lịch sử release thực tế và phạm vi thay đổi.
Trước 1.0, compatibility chưa được bảo đảm; vẫn phải ghi rõ breaking change.

Với PartFlow, contract được hỗ trợ bao gồm hành vi API đã được tài liệu hóa và
yêu cầu configuration/nâng cấp dữ liệu. Chỉ có database migration không đồng
nghĩa phải tăng major; cần đánh giá tác động đến compatibility.

`alpha`, `beta` và `rc` thể hiện mức độ hoàn thiện của release, không phải host
triển khai. Đánh dấu cả ba là GitHub pre-release; không chỉ định chúng là bản
stable mới nhất. Có thể diễn tập bản stable trên staging trước production.
Hậu tố không miễn trừ §5 và không chứng minh deployment validation đã đạt.

### 10.3 Release title và description

Dùng `PartFlow <tag>`, có thể thêm mục đích ngắn:
`PartFlow v0.1.0-alpha.1 — Internal Staging`. Giữ release title và description
bằng tiếng Anh trừ khi được yêu cầu rõ ràng dùng ngôn ngữ khác.

Với release đầu tiên, tóm tắt phạm vi đã triển khai. Với release tiếp theo,
mô tả thay đổi từ release trước được chọn đến đúng target commit, không chỉ
dựa vào commit mới nhất hoặc kế hoạch phase. Không đưa công việc chưa commit
vào release.

Dùng mẫu Description sau, thay placeholder bằng thông tin đã xác minh:

```markdown
## Summary
<Release purpose and intended use.>

## Changes
<Implemented additions, fixes, and breaking changes since the previous release;
summarize available functionality for an initial release.>

## Deployment
- Source commit: <full commit SHA>
- Previous release: <tag, or Initial release>
- Intended use: <internal staging, release validation, or production>
- GitHub pre-release: <Yes or No>
- Deployment guide: <repository guide path>

## Database and configuration
- Migrations: <required revisions and upgrade notes, or No new migrations>
- Configuration: <required changes, or No changes>
- Rollback: <application/schema compatibility and recovery requirements>

## Known limitations
<Relevant restrictions and unavailable features.>

## Validation
<Completed checks and their evidence for this exact commit;
identify pending or unverified checks explicitly.>
```

Không suy ra "No new migrations", compatibility hoặc validation thành công
từ tên release. Kiểm tra khoảng revision và bằng chứng thực tế. Kết quả chưa
biết phải ghi `Pending` hoặc `Not verified` trong draft; chúng không đáp ứng
release gate. Tách kết quả smoke test trên NAS khỏi kết quả CI. Không đưa secret,
credential hoặc chi tiết triển khai riêng tư vào release notes công khai.

### 10.4 Checklist phát hành

1. Chọn đúng target commit và release trước dùng làm mốc so sánh. Giải quyết
   các thay đổi chỉ có ở local trước khi chọn source để phát hành.
2. Kiểm tra tag local và remote để tránh trùng. Xác nhận CI của repo và các
   quality gate liên quan đã đạt trên đúng commit đó; run thành công của
   revision khác không được tính.
3. Rà soát thay đổi, migration, configuration, yêu cầu rollback và giới hạn đã
   biết. Hoàn thiện release notes; áp dụng §5 cho pilot/production.
4. Tạo annotated tag tại commit đã chọn, không tạo tại đầu branch đang thay đổi
   mà chưa được kiểm tra, rồi push riêng tag đó.
5. Trong GitHub Releases, tạo draft từ tag đã có. Nhập title và description,
   đặt pre-release flag nhất quán với §10.2 và đính kèm các build artifact
   dự định phát hành trước khi publish.
6. Publish khi đã đáp ứng các gate áp dụng. Deploy theo §7 và runbook của nền
   tảng; ghi tag, SHA, database revision, operator, thời gian và kết quả smoke
   test. Giữ release trước và các backup cần thiết.

Có thể chuẩn bị draft trước khi validation hoàn tất. Publish release và deploy
là hai thao tác riêng; yêu cầu chuẩn bị release info không tự động cho phép
thực hiện thao tác nào trong hai thao tác này.

### 10.5 Workflow hiện tại và ranh giới rollback

Tại source revision ở §1, `.github/workflows/ci.yml` chạy khi push vào `main`
và khi có pull request. Workflow kiểm tra code và build development image;
không publish release image hoặc deploy lên Synology khi tạo tag/release.
Source archive của GitHub không phải production image đã build sẵn. Source tag
cố định cũng không bảo đảm lần build lại sau đó giống hệt khi base image có
thể thay đổi.

Release thủ công là đủ ở giai đoạn này. Deployment record giữ image ID cục bộ của
mọi release (P16-S3); khi triển khai việc publish production
image, ghi registry digest cùng release tag và giữ lại artifact đã deploy. Không
tạo version service riêng, hệ thống nhiều cấp release branch hoặc cơ chế
publish tự động chỉ để áp dụng quy ước này.

Rollback theo operations runbook: đổi application tag không hoàn tác migration.
Xác nhận schema compatibility hoặc dùng kế hoạch phục hồi database đã duyệt,
có tính đến dữ liệu được ghi sau backup. Không bao giờ giả định restore backup
cũ sẽ giữ được các production Movement mới hơn.

Tham chiếu: [Git tags](https://git-scm.com/docs/git-tag) và
[GitHub release management](https://docs.github.com/en/repositories/releasing-projects-on-github/managing-releases-in-a-repository).
