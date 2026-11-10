# Hướng dẫn triển khai PartFlow

> **Bản gốc chuẩn:** [`DEPLOYMENT.md`](DEPLOYMENT.md).
> Đồng bộ với bản tiếng Anh tại commit `b405c6e` cộng các thay đổi chưa commit ngày 2026-11-09.
> Bản tiếng Anh là source of truth; khi hai bản khác nhau, bản tiếng Anh được áp dụng.

Hướng dẫn này là điểm bắt đầu để cài đặt và vận hành PartFlow bên ngoài máy của developer. Người đọc là người cài đặt,
phát hành và bảo trì PartFlow: hiện nay đó là owner, vừa là operator vừa là người phê duyệt. Tài liệu giải thích nên
dùng stack nào, production stack gồm những gì và được cấu hình ra sao, cần gì trước khi dùng thật, và release được đặt
tên và phát hành như thế nào. Hãy đọc §1 đến §3 trước. Các hướng dẫn theo nền tảng ở §8 áp dụng tài liệu này cho từng
loại host, còn [`deployment/OPERATIONS_RUNBOOK.md`](./deployment/OPERATIONS_RUNBOOK.md) chứa các quy trình hằng ngày
(health, backup, restore, release, rollback, reconciliation và sự cố).

Các lệnh trong hướng dẫn này dùng biến shell `PF`, được định nghĩa một lần trong thư mục của release checkout:

```sh
PF="docker compose -f compose.production.yaml --env-file .env.production"
```

`PF` chỉ là cách viết tắt cho lệnh Docker Compose của production stack. Nó không phải công cụ Deployment Admin `pf`.
Hãy chạy mọi lệnh `$PF` từ thư mục của release checkout.

## 1. Trạng thái hiện tại

**Những gì đã có.** Repo chứa một production stack hoàn chỉnh (§3.1): production image cho backend và cho tầng `web`,
file Compose `compose.production.yaml`, bảng kê configuration và secret, các database role tách riêng, một release
script biết freeze write, backup, migrate và chuyển release, backup đã verify cùng restore drill cô lập, structured
log, lệnh reconciliation chỉ đọc và các script kiểm tra host để giám sát theo lịch. Thứ tự và lịch sử triển khai nằm ở
[`IMPLEMENTATION_ROADMAP.md`](./IMPLEMENTATION_ROADMAP.md).

**Những gì chưa chạy trên host thật.** Các lệnh và script này đã chạy trên Docker Desktop và trong một Linux
container, nhưng chưa chạy trên Synology NAS hay VPS. Các thiết lập host (reverse proxy của DiskStation Manager (DSM),
certificate, các task theo lịch cùng failure notification của chúng, và giới hạn tài nguyên đo trên host) được làm
trong lúc cài đặt trên NAS và sau đó.

**Quyết định của owner (2026-11-09): go-live trực tiếp.** PartFlow được dùng thật trên Synology NAS của công ty ngay
khi production stack được cài xong. Không có giai đoạn pilot và không có danh sách release gate. Lỗi được sửa ở các
release sau trong lúc hệ thống đang được dùng. Chỉ những gì phần mềm cần để chạy được là bắt buộc (§5); backup và
giám sát được khuyến nghị sau go-live và không bao giờ chặn việc sử dụng hay một release.

| Mục đích | Trạng thái | Cách làm |
| --- | --- | --- |
| Máy developer | Được hỗ trợ | Dùng `compose.yaml` theo root README. |
| Synology staging hoặc test nội bộ | Được hỗ trợ có giới hạn | Chỉ trong mạng cục bộ (LAN), dữ liệu giả hoặc không phải production, người dùng được kiểm soát và backup rõ ràng. Xem [`deployment/SYNOLOGY_NAS.md`](./deployment/SYNOLOGY_NAS.md). |
| Dùng production | Được hỗ trợ | Cài production stack (§3.1) trong LAN của xưởng theo các yêu cầu ở §5; các bước trên NAS nằm ở [`deployment/SYNOLOGY_NAS.md`](./deployment/SYNOLOGY_NAS.md) §8. Các release sau dùng `release.sh`. |
| Mở ra Internet | Không được hỗ trợ | PartFlow chỉ chạy trong LAN của xưởng. Mở ra Internet cần một quyết định riêng của owner (firewall, mạng riêng ảo (VPN) hoặc certificate public). `compose.yaml` expose các service development (§2). |

## 2. Vì sao Compose stack development không dùng cho production

`compose.yaml` và stage `development` mặc định của mỗi Dockerfile là artifact development. Mỗi Dockerfile còn có một
stage `production`, chỉ production stack build stage này (§3.1). Không bao giờ chạy production từ `compose.yaml`, vì
trong stack đó:

- backend khởi động Uvicorn với `--reload`;
- frontend chạy Vite development server thay vì phục vụ một production build bất biến;
- thư mục source và thư mục dependency được bind-mount vào container;
- port của PostgreSQL, backend và frontend được publish trên host;
- có sẵn giá trị credential mặc định cho development;
- database và application dùng chung một PostgreSQL role (production stack dùng các role riêng, §3.1);
- không có reverse proxy, chính sách Transport Layer Security (TLS), nơi lưu secret, log rotation, image tag theo
  release, backup theo lịch, restore drill hay lệnh rollback;
- một số view đã duyệt vẫn là preview chỉ dành cho development hoặc còn chờ tích hợp backend và frontend thật.

Đăng nhập, kiểm tra permission phía server và yêu cầu thiết bị đã enroll cho các route Scan Station hoạt động giống
nhau ở cả hai stack. Không bao giờ che các giới hạn trên phía sau reverse proxy của NAS hay một tên DNS public.

## 3. Topology portable mục tiêu

Production stack dùng một topology chung trên Synology NAS và trên VPS sau này. TLS kết thúc tại platform proxy
(reverse proxy của DSM trên NAS, hoặc Caddy trên VPS). Bên trong stack, `web` (nginx) phục vụ frontend build bất biến
và chuyển `/api` tới backend; `web` chỉ được publish trên địa chỉ loopback của host, nên platform proxy là lối vào duy
nhất.

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

- chỉ expose HTTPS cho client, và HTTP chỉ để chuyển hướng sang HTTPS;
- giữ PostgreSQL private và không bao giờ publish port 5432 ra mạng không tin cậy;
- giữ backend private; `web` tới backend qua mạng container private;
- phục vụ frontend và API từ cùng một origin, để browser tiếp tục dùng các URL `/api` tương đối;
- giữ dữ liệu PostgreSQL và backup bên ngoài container, vì container có thể bị thay bất cứ lúc nào;
- chạy schema migration như một bước release rõ ràng, không bao giờ như tác dụng phụ khi một application container
  khởi động;
- định danh mọi lần triển khai bằng một Git tag và commit bất biến;
- dùng một định dạng backup chung trên NAS và trên VPS (backup artifact của
  [`deployment/OPERATIONS_RUNBOOK.md`](./deployment/OPERATIONS_RUNBOOK.md) §3).

### 3.1 Production stack

Mục này là contract của production stack. Nó định nghĩa các service, file configuration và các secret file, cách build
image, cách cài stack lần đầu, và hành vi của health, release, database role, backup và giám sát. Các quy trình từng
bước nằm ở [`deployment/OPERATIONS_RUNBOOK.md`](./deployment/OPERATIONS_RUNBOOK.md); thiết lập host nằm trong các
hướng dẫn theo nền tảng (§8).

#### Service và network

`compose.production.yaml` định nghĩa Compose project `partflow-production`:

| Service | Vai trò | Network | Ghi chú |
| --- | --- | --- | --- |
| `db` | PostgreSQL `postgres:16.14`, bản Debian | `internal` (không có route ra ngoài) | Không bao giờ dùng image `-alpine`: collation và check định danh canonical (reconcile check (j)) phụ thuộc vào thư viện GNU C (glibc). Volume `postgres_data`; không publish port; thời gian chờ dừng 60 s; chạy với `log_error_verbosity=terse`. |
| `backend` | image `partflow/backend:${PARTFLOW_RELEASE}`, stage `production` | `internal`, `edge` | Kết nối bằng application role `partflow_app` và chỉ mount `partflow_app_password`. `SESSION_COOKIE_SECURE=true`, `ENFORCE_CLIENT_RELEASE=true` và `DATABASE_ROLES_REQUIRED=true` là cố định. `WEB_CONCURRENCY` lấy từ `PARTFLOW_BACKEND_WORKERS` (mặc định 2) và `FORWARDED_ALLOW_IPS` là subnet edge. Thời gian chờ dừng 200 s, lớn hơn upstream timeout dài nhất của `web` là 180 s. `restart: unless-stopped`, không bao giờ `on-failure`, vì với nhiều worker một lần từ chối configuration thoát với code 0. |
| `web` | image `partflow/web:${PARTFLOW_RELEASE}`, stage `production` | `edge` | Port duy nhất được publish, `127.0.0.1:${PARTFLOW_HTTP_PORT}:80`; cố ý không có biến cho địa chỉ bind. Không có `depends_on`, nên vẫn phục vụ application shell khi `backend` đang dừng. Thời gian chờ dừng 200 s, để một lần import đang chạy có thể hoàn tất. |
| `migrate` | one-shot `python -m app.cli migrate` từ backend image | `internal` | Profile `ops`, không bao giờ được `up` khởi động. Một connection, một transaction. Không có tùy chọn backup thì là lỗi cách dùng. Mount thư mục backup chỉ đọc tại `/backups` để verify pre-release backup. |
| `db-roles` | one-shot `python -m app.cli provision-roles` (mặc định) hoặc `apply-grants` (đối số) từ backend image | `internal` | Profile `ops`. Kết nối bằng owner role và mount cả ba secret file. |
| `backup-tools` | one-shot `python -m app.cli` từ backend image, chạy với `backup-manifest`, `backup-verify` hoặc `backup-rotate` (được `deploy/production/backup.sh` dùng) | không có (`network_mode: none`) | Profile `ops`. Không database, không secret; chỉ mount thư mục backup tại `/backups`. |
| `status` | one-shot `python -m app.cli status` từ backend image | `internal` | Profile `ops`. Báo cáo chỉ đọc bằng `partflow_app`, mount thư mục backup chỉ đọc (được `deploy/production/check.sh` dùng). |

Mọi service có restart policy, giới hạn bộ nhớ và bộ xử lý lấy từ file configuration, và log rotation `json-file`
(10 MiB mỗi file, 5 file). Mọi service chạy lâu dài có health check; check của `backend` gọi route liveness, nên schema
mismatch không bao giờ làm container unhealthy. Giới hạn tài nguyên là giá trị khởi đầu; hãy đo trên production host
và điều chỉnh. Chạy các service `ops` có đọc hoặc ghi thư mục backup với `--user "$(id -u):$(id -g)"`, vì thư mục đó
có mode 0700 và thuộc tài khoản của operator.

#### File configuration

`.env.production` được copy từ `.env.production.example`, bị git bỏ qua, có mode 600 và chỉ chứa giá trị không bí
mật. Một static test kiểm tra rằng tập các tham chiếu `${NAME}` trong `compose.production.yaml` và
`compose.production.build.yaml` bằng đúng tập các key của file example.

| Key | Ý nghĩa | Bắt buộc hoặc mặc định |
| --- | --- | --- |
| `PARTFLOW_RELEASE` | Tag của image **đang chạy** (§10). | bắt buộc |
| `PARTFLOW_COMMIT` | Commit đầy đủ của release đang được build. Để trống trong file; `release.sh` truyền nó trong shell lúc build. | bắt buộc khi build |
| `PARTFLOW_ACCEPT_SCHEMA_REVISION` | Chỉ dùng cho rollback path 2: database revision duy nhất mà release đang chạy được phép phục vụ (`deployment/OPERATIONS_RUNBOOK.md` §6). `release.sh` xóa giá trị này. | trống |
| `PARTFLOW_SECRETS_DIR` | Đường dẫn tuyệt đối của thư mục secret, nằm ngoài checkout, chỉ cho production (thư mục mode 0700, mỗi file mode 0444). | bắt buộc |
| `PARTFLOW_BACKUP_DIR` | Đường dẫn tuyệt đối của thư mục backup production, nơi `backup.sh` ghi một thư mục con cho mỗi backup. Nằm ngoài checkout, thư mục secret và mọi thư mục archive, và không bao giờ là thư mục của staging hay của một drill. Thư mục phải có sẵn với mode 0700, thuộc tài khoản chạy `backup.sh` và `release.sh`. **Mọi** lệnh `$PF` đều cần key này; ghi không có dấu ngoặc kép. Công cụ backup của nền tảng mã hóa thư mục này và sao chép nó ra ngoài host. | bắt buộc |
| `PARTFLOW_SITE_TIMEZONE` | Múi giờ lịch của xưởng, bằng giá trị của staging (§6). | bắt buộc |
| `PARTFLOW_HTTP_PORT` | Port loopback mà platform proxy kết nối tới. | bắt buộc (ví dụ `18080`) |
| `POSTGRES_USER`, `POSTGRES_DB` | Owner role và database. | bắt buộc (ví dụ `partflow_owner`, `partflow`) |
| `PARTFLOW_BACKEND_WORKERS` | Số Uvicorn worker. | `2` |
| `PARTFLOW_EDGE_SUBNET` | Subnet của network `edge`, cũng là các địa chỉ Uvicorn tin cho forwarded header. Chọn subnet chưa được dùng trên host và trong LAN. | `172.30.250.0/24` |
| `PARTFLOW_TRUSTED_PROXY` | Một địa chỉ IPv4 mà `web` tin cho forwarded header; để trống nghĩa là tự phát hiện (Yêu cầu đối với platform proxy, bên dưới). | trống |
| `PARTFLOW_{DB,BACKEND,WEB,OPS}_{MEMORY,CPUS}` | Giới hạn tài nguyên. | `1g`/`1.0`, `1g`/`2.0`, `128m`/`0.5`, `512m`/`1.0` |

Cố định trong Compose và không cấu hình được: `SESSION_COOKIE_SECURE=true`, `ENFORCE_CLIENT_RELEASE=true`,
`DATABASE_HOST=db`, `DATABASE_PORT=5432`, đường dẫn mount secret và việc bind vào loopback.

Backend lấy kết nối database từ đúng một trong hai dạng: `DATABASE_URL` (development, test, tích hợp liên tục (CI) và
staging), hoặc `DATABASE_HOST`, `DATABASE_NAME`, `DATABASE_USER` và `DATABASE_PASSWORD_FILE` kèm `DATABASE_PORT` tùy
chọn (mặc định 5432), là dạng production stack dùng. Có cả hai dạng, hoặc không dạng nào đầy đủ, đều bị từ chối khi
khởi động. Application tự tạo URL kết nối, nên ký tự đặc biệt trong password không cần mã hóa thủ công. Không lỗi
validation nào lặp lại giá trị đầu vào.

#### Secret file

Không secret nào là giá trị environment và không secret nào có giá trị mặc định được commit. Secret là các file trong
`PARTFLOW_SECRETS_DIR`, được mount dưới `/run/secrets`:

| File | Được dùng bởi | Quy tắc |
| --- | --- | --- |
| `postgres_password` | owner role: `db`, `migrate`, `db-roles` | Một dòng. `db` **chỉ đọc file này khi khởi tạo một data volume mới**; `migrate` và `db-roles` đọc nó mỗi lần khởi động. `backend` không bao giờ mount nó. |
| `partflow_app_password` | `backend`, `status`, `db-roles` | Một dòng, 16 đến 128 ký tự ASCII in được, không có khoảng trắng. |
| `partflow_maintenance_password` | `db-roles` | Cùng quy tắc, và khác với `partflow_app_password`. |

Tạo các secret file như sau, trước lệnh `$PF` đầu tiên khởi động `backend` hoặc một service `ops`. Compose thay một
file bị thiếu bằng một thư mục rỗng, và `release.sh` từ chối, không thay đổi gì, khi bất kỳ file nào trong ba file bị
thiếu, rỗng hoặc không phải file thường. Nếu Compose đã tạo một thư mục ở đường dẫn đó, hãy xóa thư mục đó trước.

```sh
install -d -m 0700 "$PARTFLOW_SECRETS_DIR"
for name in postgres_password partflow_app_password partflow_maintenance_password; do
    openssl rand -base64 32 > "$PARTFLOW_SECRETS_DIR/$name"
    chmod 0444 "$PARTFLOW_SECRETS_DIR/$name"
done
```

Trước khi chạy các lệnh này, đặt `PARTFLOW_SECRETS_DIR` trong shell bằng cùng đường dẫn tuyệt đối như trong
`.env.production`. Mỗi file phải chứa đúng một dòng (dấu xuống dòng ở cuối được bỏ qua), vì `initdb` của PostgreSQL
chỉ lấy password của role mới từ dòng đầu tiên. File nhiều dòng, rỗng, không đọc được hoặc không phải UTF-8 bị từ
chối với thông báo nêu đường dẫn file và không bao giờ nêu nội dung.

Để đổi password về sau:

- **Password của owner:** chạy `ALTER ROLE <POSTGRES_USER> PASSWORD …` bên trong `db` trước, rồi thay file. Không cần
  khởi động lại service nào.
- **Password của role:** thay file của role, chạy `$PF stop backend`, `$PF --profile ops run --rm -T db-roles`, rồi
  `$PF up -d --force-recreate --no-deps backend`. Backend đọc password file một lần cho mỗi process, nên phải được tạo
  lại. Làm việc này trong một write freeze ngắn (`deployment/OPERATIONS_RUNBOOK.md` §9).

#### Build image

Image được build tại chỗ từ release đã checkout và không bao giờ được pull (`pull_policy: never`). Chỉ file đi kèm dành
riêng cho build `compose.production.build.yaml` có phần build, và nó chỉ được dùng với `build`:

```sh
PARTFLOW_RELEASE=<new> PARTFLOW_COMMIT=$(git rev-parse HEAD) $PF -f compose.production.build.yaml build
```

Luôn build cả hai image cùng nhau. Vì `compose.production.yaml` không có phần build, `up` hoặc `run` với một tag chưa
có image sẽ thất bại với `No such image` thay vì build checkout hiện tại dưới tag đó. Một tag đã có không bao giờ được
build lại.

- **Image `backend` (stage `production`):** Python 3.12 slim, các dependency không phải development đã khóa phiên bản,
  không có `tests/`, không có `.env` và không có reload server; chạy bằng user `10001:10001` với lệnh khởi động
  `uvicorn app.main:app --host 0.0.0.0 --port 8000 --no-access-log --log-config app/core/logging.production.json`.
  Image này không bao giờ chạy migration khi khởi động.
- **Image `web` (stage `production`):** image chính thức `nginx:1.30.5-alpine` đã ghim phiên bản, kèm frontend build
  bất biến từ `npm run build`, trong đó có production-boundary check (không có dữ liệu preview development trong
  build).

#### Mô hình process và lần chạy đầu

`WEB_CONCURRENCY` đặt số Uvicorn worker và `FORWARDED_ALLOW_IPS` đặt các địa chỉ proxy mà Uvicorn tin cho forwarded
header. Compose đặt cả hai bên trong container, nên giá trị `WEB_CONCURRENCY` trong shell của operator không có tác
dụng.

- **First-run setup chạy với một worker.** Mỗi worker có setup token riêng và giới hạn băm password riêng. Với hai
  worker, một request setup có thể tới worker có token không phải token đã được copy và bị từ chối với
  `403 setup_token_invalid` (không ghi gì). Khởi động backend bằng `PARTFLOW_BACKEND_WORKERS=1 $PF up -d backend`, đọc
  token bằng `$PF logs backend | grep "Setup token"`, hoàn tất setup trong browser, rồi khôi phục số worker đã cấu hình
  bằng `$PF up -d backend`. Setup token là bí mật duy nhất từng được ghi vào log backend: hãy hoàn tất first-run
  setup trước khi mở quyền truy cập cho người dùng, và giới hạn quyền đọc log cho đến lúc đó.
- **Một lần từ chối configuration thoát với code 0** khi có nhiều hơn một worker. Dòng log và số lần restart, không
  phải exit code, cho biết lỗi.
- **Dừng một backend nhiều worker không từ chối kết nối mới ngay lập tức.** Uvicorn supervisor giữ socket lắng nghe mở
  cho đến khi worker cuối cùng thoát (tối đa 200 s thời gian chờ dừng trong lúc một lần import hoàn tất). Một request
  gửi trong lúc đó được nhận nhưng không bao giờ được trả lời: nó kết thúc thành 504 của `web` sau read timeout 60 s,
  hoặc thành 502 khi supervisor thoát. Backend chưa từng chạy request đó, nhưng client phải coi kết quả là không rõ.
  Với một worker, request như vậy bị từ chối ngay (502). Vì vậy hãy bắt đầu write freeze khi không có lần import nào
  đang chạy.

**Cài đặt lần đầu.** Chạy các bước sau từ release checkout, với release tag đã được đặt làm `PARTFLOW_RELEASE` trong
`.env.production`:

1. Tạo thư mục backup bằng `install -d -m 0700 <path>` và đặt `PARTFLOW_BACKUP_DIR` trong `.env.production`; mọi lệnh
   Compose của stack đều cần key này.
2. Tạo ba secret file (Secret file, ở trên).
3. Build cả hai image: `PARTFLOW_COMMIT=$(git rev-parse HEAD) $PF -f compose.production.build.yaml build`.
4. Khởi động database: `$PF up -d db`.
5. Tạo các database role: `$PF --profile ops run --rm -T db-roles`.
6. Áp schema và grant:
   `$PF --profile ops run --rm -T migrate --no-backup-reason "first install: empty database"`.
7. Chạy first-run setup với một worker (ở trên), rồi khởi động application: `$PF up -d backend web`.
8. Chạy `$PF run --rm --no-deps -T backend python -m app.cli reconcile` và kiểm tra rằng check "Append-only guards and
   database-role privileges intact" (check (h)) báo `pass`.

#### Health và release identity

Một release là tag `PARTFLOW_RELEASE` cộng với commit đầy đủ 40 ký tự `PARTFLOW_COMMIT`. Cả hai image của một release
được build từ cùng một commit và mang cùng một identity.

- `compose.production.build.yaml` truyền `PARTFLOW_RELEASE` và `PARTFLOW_COMMIT` làm build argument cho cả hai image.
  Build thất bại nếu không có tag hợp lệ (chữ cái, chữ số, `.`, `_` và `-`, tối đa 64 ký tự, không phải `development`)
  hoặc không có commit 40 ký tự hexa viết thường.
- Cả hai image lưu identity dưới dạng label `org.opencontainers.image.version` và
  `org.opencontainers.image.revision`. Image `web` còn nhúng tag vào frontend bundle (`VITE_PARTFLOW_RELEASE`) và vào
  meta tag `partflow-release` của trang được phục vụ.
- Backend đọc `RELEASE_TAG` và `RELEASE_COMMIT`, do stage `production` đặt (ngoài release image, mặc định là
  `development` và không có commit). Compose không bao giờ đặt `RELEASE_TAG`.
- Mỗi trang gửi release của bundle của nó trong request header `X-PartFlow-Release`; việc so sánh với release của
  backend là so sánh chuỗi chính xác.

Backend có hai route health:

- **Liveness, `GET /api/health/live`**, trả `{"status":"live","service","release","commit"}` mà không chạm vào
  database. Mọi container health check dùng route này.
- **Readiness, `GET /api/health`**, đọc database revision. Các key của nó là `status`, `service`, `database`,
  `release`, `commit`, `schema`, `expected_revision`, `database_revision` và `accepted_revision`. Nó trả `200` với
  `"status":"ok"` chỉ khi `schema` là `current` hoặc `accepted`, `503 not_ready` khi `mismatch`, và `503 unavailable`
  với `"schema":"unknown"` khi không tới được database.

| Giá trị `schema` | Ý nghĩa |
| --- | --- |
| `current` | Database revision bằng Alembic head duy nhất của image. |
| `accepted` | Database đang ở đúng revision được nêu bởi `PARTFLOW_ACCEPT_SCHEMA_REVISION`, mà image này không biết (rollback path 2, `deployment/OPERATIONS_RUNBOOK.md` §6). Nêu một revision mà image biết sẽ bị bỏ qua kèm cảnh báo, và `revision` báo `override_ignored`. |
| `mismatch` | Mọi database revision khác. |
| `unknown` | Không đọc được database. |

#### Write gate

Backend từ chối mọi `POST`, `PUT`, `PATCH` và `DELETE`, trên mọi đường dẫn, trước khi định tuyến, trước check chống
giả mạo request liên trang (CSRF) và trước khi đọc body, trong hai trường hợp. Cả hai lần từ chối đều không ghi gì và
mang `Cache-Control: no-store`.

| Phản hồi | Khi nào | Ý nghĩa với client |
| --- | --- | --- |
| `409` với `release_mismatch: true` | Enforcement đang bật và `X-PartFlow-Release` của request bị thiếu hoặc khác `RELEASE_TAG`. | Từ chối chắc chắn: request đó không ghi gì (một lần thử trước đó chưa được trả lời vẫn là không rõ). |
| `503` với `not_ready: true` | Schema là `mismatch` hoặc không xác nhận được readiness; gate đóng khi lỗi. | Kết quả không rõ. |

Request đọc và route health không bao giờ bị gate chặn. Enforcement (`ENFORCE_CLIENT_RELEASE`) cố định bật trong
`compose.production.yaml` và tắt trong development và test; backend từ chối khởi động khi enforcement bật với tag
`development`. Lần ghi duy nhất trên method an toàn, cập nhật `last_seen_at` của thiết bị Scan Station, không chứa dữ
liệu production và được chấp nhận khi mismatch. Trạng thái màn hình cho các phản hồi này là `GUI_DESIGN.md` §3 quy tắc
13.

#### Migration và lệnh `revision`

`python -m app.cli migrate` (service `migrate`) áp các migration đang chờ trong một transaction trên một connection, áp
grant trong cùng transaction đó (Database role và grant, bên dưới) và in một báo cáo JSON.

- Lệnh cần đúng một trong `--pre-release-backup NAME` hoặc `--no-backup-reason TEXT`. `NAME` là một thư mục backup do
  `backup.sh` tạo; nó được verify trước migration và được ghi lại, và khi có migration đang chờ thì `migrate` còn cần
  `--backup-not-before UTC`, thời điểm write freeze bắt đầu. Lý do được ghi lại (ví dụ
  `first install: empty database`). Quy tắc về độ mới và quyền sở hữu nằm ở `deployment/OPERATIONS_RUNBOOK.md` §5.
- Lệnh nhận `--lock-timeout SECONDS` (1 đến 600, mặc định 30) và `--backup-dir` (điểm mount, mặc định `/backups`).
- Lệnh từ chối, không thay đổi gì: một file revision có thay đổi schema không transactional (`autocommit_block`,
  `CONCURRENTLY`), một database revision mà release này không biết, một `migrate` chạy đồng thời, và một backend
  session đang kết nối (kiểm tra theo khả năng tốt nhất, không phải bằng chứng rằng `backend` đã dừng). Database đã ở
  head trả về `already_current`.
- Một lần từ chối về role hoặc grant (`roles_not_provisioned`, `roles_incomplete`, `role_unsafe`, `role_owns_objects`,
  `foreign_grantor`, `table_unclassified`, `table_missing`, `not_superuser`) cho kết quả `refused` với exit code 1, và
  phần upgrade của cùng lần chạy bị rollback.

Backend không bao giờ migrate. `python -m app.cli revision` chỉ đọc: nó in release identity, revision mong đợi và
database revision, các revision đang chờ và readiness mà backend sẽ báo, và chỉ thoát với 0 khi schema là `current`.

#### Database role và grant

Production database có ba role:

| Role | Được dùng bởi | Ghi chú |
| --- | --- | --- |
| Owner (`POSTGRES_USER`, mặc định `partflow_owner`) | `db`, `migrate`, `db-roles`, backup | Superuser khởi tạo của PostgreSQL; sở hữu mọi object. Tên phải khác hai role bên dưới. |
| `partflow_app` | `backend` (API, `reconcile`, `revision` và các lệnh khôi phục) và `status` | Chỉ giữ các quyền trong bảng bên dưới. |
| `partflow_maintenance` | chưa service nào dùng | Chỉ giữ SELECT trên một danh sách bảng được nêu tên; dành cho việc bảo trì archive đã lên kế hoạch. |

`provision-roles` tạo cả hai role không phải owner với `LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION
NOBYPASSRLS`, không membership, không setting riêng cho role và không sở hữu object nào. Tên của chúng cố định trong
code và trong `compose.production.yaml`.

| Nhóm | Quyền của `partflow_app` | Bảng |
| --- | --- | --- |
| append-only | SELECT, INSERT | `part_movements`, `audit_events`, `machine_lifecycle_events`, `quantity_flow_lineage`, `work_order_allocations` |
| guarded update | SELECT, INSERT, UPDATE | `worker_sessions` (row trigger của bảng giới hạn các cột) |
| no update | SELECT, INSERT, DELETE | `assigned_route_steps` |
| read only | SELECT | `alembic_version` |
| ordinary | SELECT, INSERT, UPDATE, DELETE | mọi bảng khác |

Không role nào giữ TRUNCATE, quyền trên sequence (các bảng dùng identity column), grant option, hay `CREATE` trên
schema `public` hoặc trên database, và quyền PUBLIC cùng default privilege bị gỡ bỏ. Phân loại đầy đủ nằm ở
`SLICE1_DATA_MODEL.md` §17. Các trigger báo lỗi khi có lần ghi bị cấm là lớp bảo vệ thứ nhất; các quyền bị thu hồi là
lớp thứ hai. Vì vậy một lỗi `permission denied` (SQLSTATE 42501) trong log backend là một sự cố
(`deployment/OPERATIONS_RUNBOOK.md` §2) và không bao giờ được sửa bằng cách cấp thêm quyền. Một superuser tắt trigger
nằm ngoài khả năng phát hiện của PartFlow; đây là giới hạn được chấp nhận.

- `provision-roles` đọc `partflow_app_password` và `partflow_maintenance_password` trước khi kết nối, tạo hoặc sửa hai
  role và đặt password SCRAM-SHA-256 của chúng trong một transaction. Chạy lại nhiều lần vẫn an toàn.
- `apply-grants` suy ra mọi grant từ phân loại bảng trong code và áp chúng trong một transaction. Lệnh từ chối nếu
  database chưa ở Alembic head của release.
- Mỗi `migrate` chạy phần grant trong transaction của nó sau upgrade, nên một release không bao giờ để lại bảng mới mà
  thiếu grant. Một bảng chưa được phân loại, một role có thuộc tính hoặc membership bị cấm, một role sở hữu object, hay
  một quyền được cấp bởi role mà PartFlow không quản lý sẽ rollback toàn bộ lần chạy (kết quả `migrate` là `refused`,
  exit code 1, không thay đổi gì).
- Với `DATABASE_ROLES_REQUIRED=true` (cố định trên `backend` và `migrate` trong `compose.production.yaml`), thiếu role
  cũng bị từ chối với `roles_not_provisioned`. Khi không đặt (development, test và staging), báo cáo grant là
  `not_provisioned` và không cấp gì.
- Grant không bao giờ được restore từ dump. Mỗi `migrate` suy ra lại grant từ code, và sau một lần restore bạn chạy
  `apply-grants`.
- Check "Append-only guards and database-role privileges intact" (reconcile check (h)) xác minh kết quả.

#### Release

`deploy/production/release.sh` chạy trình tự release của §7. Chạy script từ thư mục gốc của repo trong release
checkout, với release tag đã được checkout; `deploy/production/release.sh --help` in cách dùng.

```sh
deploy/production/release.sh --release <new> --operator <name> --approver <name>
```

| Tùy chọn | Ý nghĩa |
| --- | --- |
| `--release TAG`, `--operator NAME`, `--approver NAME` | Bắt buộc. Một người có thể ghi cùng một tên cho operator và approver. |
| (không có tùy chọn backup) | `release.sh` tự lấy pre-release backup đã verify bằng `backup.sh`, bên trong write freeze khi có migration đang chờ. |
| `--pre-release-backup NAME` | Dùng một backup `backup.sh` đã có; bị từ chối khi có migration đang chờ. |
| `--no-backup-reason TEXT` | Không có pre-release backup (chỉ cho lần cài đầu và diễn tập); lý do được ghi lại. |
| `--accept-pre-release-findings` | Lần reconciliation trước release có finding: tiếp tục, và chỉ chặn với finding không có trong lần đó. |
| `--skip-pre-reconcile REASON` | Không image nào có database revision làm head (trạng thái của rollback path 2): bỏ qua reconciliation trước release; khi đó mọi finding sau release đều chặn. |
| `--env-file`, `--records-dir` | File configuration (mặc định `.env.production`) và thư mục record (mặc định `$HOME/partflow-deployments`, mode 0700). |
| `--environment`, `--url`, `--rollback-deadline`, `--observation-owner`, `--known-limitations` | Các trường record tùy chọn. Giá trị văn bản dài 1 đến 500 ký tự, không có ký tự điều khiển, `"` hay `\`. |
| `--rehearsal --project NAME` | Diễn tập trên một Compose project dùng một lần, không bao giờ là `partflow-production`. |

Trước khi thay đổi bất cứ thứ gì, `release.sh` từ chối khi tag không phải release tag theo §10.2, khi tag không phải
commit đang checkout hoặc build input khác với commit đó, khi có project `partflow-staging` trên Docker daemon (§6),
khi thiếu một secret file, khi image của release đang chạy không có trên host (rollback sẽ không thể thực hiện), hoặc
khi một release khác đang giữ lock.

| Exit code | Ý nghĩa |
| --- | --- |
| 0 | Hoàn tất. |
| 1 | Dừng mà không thay đổi gì, hoặc write đã mở lại trên release hiện tại. |
| 2 | Không chạy được. |
| 3 | `backend` bị để ở trạng thái dừng; làm theo `deployment/OPERATIONS_RUNBOOK.md` §6. |
| 4 | Release mới có thể đang chạy và ghi được sau một check thất bại. |
| 130 hoặc 143 | Bị ngắt bởi Ctrl-C hoặc TERM; record nêu bước đang chạy, và một `migrate` bị ngắt có kết quả không rõ. |

Output của từng bước và `record.json` (deployment record của `deployment/OPERATIONS_RUNBOOK.md` §1) được ghi vào
`<records-dir>/<UTC>-<tag>/`. `deploy/production/smoke.sh --release TAG [--env-file …] [--project NAME]
[--allow-accepted-schema]` chạy các check qua loopback: trang được phục vụ và release của nó, fallback của single-page
app, release identity và schema trong health và liveness, JSON 404 cho một đường dẫn API không tồn tại, 409 của write
gate khi thiếu release header và việc cho qua khi có header, và identity của các image đang chạy.

`.env.production` luôn nêu release **đang chạy**. Candidate tag chỉ nằm trong shell, hoặc trong environment theo từng
lệnh của script, cho đến lúc chuyển, nên mọi lệnh khôi phục, reconciliation hay `up` trước lúc chuyển đều dùng image
hiện tại với schema hiện tại. Bản thủ công tương đương với `release.sh`, theo cùng thứ tự, nằm ở
`deployment/OPERATIONS_RUNBOOK.md` §5. Không có gì lên lịch chạy `release.sh`, không có updater nào chạy trên host, và
không script nào xóa, prune hay gắn lại tag cho image.

#### Rollback

Cây quyết định, path 1 và 2 và schema override nằm ở `deployment/OPERATIONS_RUNBOOK.md` §6.

- **Code application không có thay đổi schema:** đặt lại tag trước trong `.env.production` và chạy
  `$PF up -d backend web`. Việc này cần image của release trước còn trên host, nên hãy giữ chúng suốt rollback window
  và không bao giờ chạy `docker image prune -a`; khi chúng không còn, `up` thất bại với `No such image` và không khởi
  động gì.
- **Rollback schema:** restore pre-release backup vào một database mới (path 3 trong cùng mục). Việc này cần phê duyệt
  của owner được ghi lại. Không bao giờ có down-migration.
- **Không bao giờ chạy `down -v`, hoặc xóa volume, trên project `partflow-production`:** việc đó xóa
  `partflow-production_postgres_data`. `$PF down` không có `-v` là cách duy nhất để dừng toàn bộ.

#### Giới hạn request và timeout (`web`)

| Route | Giới hạn body | Upstream timeout |
| --- | --- | --- |
| Mặc định cho mọi route `/api` | 1 MiB | 60 s |
| `PUT /api/workers/{id}/avatar`, `PUT /api/users/{id}/avatar`, `PUT /api/part-numbers/image` | 4 MiB | 60 s |
| `POST /api/work-orders/import/preview` | 4 MiB | 60 s |
| `POST /api/work-orders/import` | 4 MiB | 180 s (đọc và gửi) |

Giới hạn 4 MiB cao hơn giới hạn của chính application (ảnh 2 MiB, file import 1 MiB), nên JSON 413 của application trả
lời cho một file quá lớn nhưng hợp lý; `web` chỉ trả lời trước với một body lớn rõ ràng. `web` không bao giờ thử lại
request lên upstream: một request của client tối đa là một lần thực thi ở backend.

**Rate limit (theo địa chỉ client được chuyển tiếp).** `POST /api/session` cho phép 10 lần mỗi phút với burst 5 (sáu
lần thử cùng lúc, sau đó thêm một lần mỗi 6 s). `PUT /api/session/password`, `POST /api/setup/administrator` và
`PUT /api/users/{user_id}/password` cho phép 5 lần mỗi phút với burst 4 (năm lần cùng lúc, sau đó một lần mỗi 12 s).
Các request đọc như `GET /api/session` và đăng xuất không bao giờ bị giới hạn. Một 429 từ `web` không bao giờ tới
application, nên không bao giờ được tính là lần đăng nhập thất bại và không bao giờ khóa tài khoản; khóa theo tài khoản
và giới hạn theo địa chỉ là hai lớp độc lập. Mã kích hoạt thiết bị không bị rate limit (50 bit entropy, sống 15 phút).

**Phản hồi do `web` tạo ra.** `web` chỉ tự trả lời cho các tình huống nó tự phát hiện; mọi phản hồi của backend, kể cả
401, 403, 409, 413, 422 và 503 của chính application, đi qua không thay đổi.

| Status | Khi nào | Body `detail` | Ý nghĩa với client |
| --- | --- | --- | --- |
| 413 | body vượt giới hạn của location | `This request is too large for PartFlow. Nothing was changed.` (`request_too_large: true`) | từ chối chắc chắn |
| 429 | vượt rate limit (gửi `Retry-After: 60`) | `Too many attempts from this computer. Wait a minute, then try again. Nothing was changed.` (`rate_limited: true`) | từ chối chắc chắn |
| 502 | không tới được backend, hoặc backend đóng kết nối trước khi trả lời | `The PartFlow server did not complete the request. If you were saving a change, check whether it was saved before repeating it.` (`server_unavailable: true`) | kết quả không rõ với một lần ghi |
| 504 | backend không trả lời trong thời gian timeout | `The PartFlow server did not answer in time. If you were saving a change, check whether it was saved before repeating it.` (`server_unavailable: true`) | kết quả không rõ với một lần ghi |

502 hoặc 504 giữ status 5xx, nên một lần ghi của station có thể đã commit được hiển thị là không rõ và được thử lại với
cùng `device_event_id` (`deployment/OPERATIONS_RUNBOOK.md` §2). Frontend hiển thị thông báo có sẵn cho 413 hoặc 429
không mang JSON `detail`, ví dụ từ một platform proxy. `web` không thêm header chia sẻ tài nguyên khác nguồn gốc (CORS)
và không viết lại gì khác.

**Cache và header.** `index.html` và mọi fallback của single-page app là `no-cache`; `/assets/*` bất biến trong một
năm, và một asset bị thiếu là 404 thông thường, không bao giờ là trang application. Mọi phản hồi mang
`X-Content-Type-Options: nosniff`, `Referrer-Policy: same-origin` và một `Content-Security-Policy` cơ bản (script, style,
kết nối và frame cùng origin; ảnh `blob:` và `data:` cho preview ảnh cục bộ; không có inline script). Ai thêm một tính
năng cần nguồn khác thì sửa policy trong cùng thay đổi đó. HTTP Strict Transport Security (HSTS) là trách nhiệm của
platform proxy; `web` không đặt nó.

#### Logging

- **Request id.** Mọi phản hồi trả về một HTTP request id trong `X-Request-ID`. `web` nhận giá trị từ client dài 1 đến
  64 ký tự thuộc `A-Za-z0-9._-` hoặc tự tạo một id, chuyển nó cho backend và ghi đè bản header của backend; backend dùng
  cùng quy tắc và nếu không hợp lệ thì tạo một id mới 32 ký tự hexa.
- **Log của `web`.** Một bản ghi edge JSON cho mỗi request: địa chỉ client, method, đường dẫn không có query string,
  status, số byte, thời gian, upstream status, user agent và `request_id`. Health probe không được ghi.
- **Log của backend.** Backend khởi động với `--log-config app/core/logging.production.json`: mọi bản ghi, kể cả của
  Uvicorn, là một dòng JSON trên standard error (`ts`, `level`, `logger`, `message`, `request_id`, rồi các trường của
  bản ghi). Access log riêng của Uvicorn vẫn tắt. Traceback không bao giờ chứa thông báo exception của database, nên
  không dòng `DETAIL`, câu lệnh hay giá trị tham số nào lọt vào log.
- **Bản ghi application (`app.access`).** Ghi ở INFO cho một lần ghi, cho một lần từ chối có chủ đích (`not_ready`,
  `release_mismatch`, `password_check_busy`, validation, authorization) và cho một request đọc từ một giây trở lên
  (`"slow":true`); ở ERROR cho một lỗi; ở DEBUG cho một lần poll health hoặc một request đọc nhanh thông thường. Bản
  ghi nêu Part Number, QuantityFlow, Work Order Demand, Area, Operation, Machine, Worker (chỉ sau khi xác định danh
  tính), Scan Station và `device_event_id` của một production command và lý do của một lần từ chối.
- **Không bao giờ được ghi:** cookie, query string, mọi PartFlow header, request body, token, badge, password và giá
  trị quét. Setup token của first-run là ngoại lệ duy nhất đã được thông báo. `db` chạy với
  `log_error_verbosity=terse`.
- **Dung lượng.** Container rotate log bằng driver `json-file` (`max-size` 10m, `max-file` 5). Ước lượng thô: 1.000
  production command tạo khoảng 1,1 MB log backend và 0,6 MB log `web`, và mỗi board hoặc màn hình Tracking đang mở
  thêm bản ghi `web` mỗi 15 s, nên ngân sách lưu giữ được tính theo từng nguồn (`deployment/OPERATIONS_RUNBOOK.md` §2).

#### Giám sát

- **Service `status`.** `$PF --profile ops run --rm --no-deps -T --user "$(id -u):$(id -g)" status` in một báo cáo chỉ
  đọc bằng `partflow_app`, với thư mục backup được mount chỉ đọc.
- **`deploy/production/check.sh`.** Được scheduler của host chạy mỗi 15 phút; failure notification của scheduler là
  kênh cảnh báo. Script đánh giá readiness qua HTTPS, certificate, các container, số lần restart, lỗi của backend, các
  ổ đĩa và báo cáo `status`, in một dòng `PASS`, `FAIL` hoặc `SKIP` cho mỗi check, không bao giờ thay đổi service, và
  thoát khác 0 khi có lỗi cần thông báo. Một tập lỗi không đổi được thông báo lại sau 6 giờ theo mặc định.
- **`deploy/production/scheduled-reconcile.sh`.** Chạy `reconcile` đầy đủ, chỉ đọc, mỗi ngày một lần sau backup hằng
  ngày, và thoát 0 khi sạch, 1 khi có mismatch và 2 khi không chạy được.

| Check | Ngưỡng mặc định |
| --- | --- |
| Tuổi của backup mới nhất | cũ hơn 26 giờ thì lỗi |
| Dung lượng đĩa trống | dưới 15 % thì lỗi |
| Hạn certificate | hết hạn trong vòng 21 ngày thì lỗi |
| Số lần restart container | tăng bất kỳ thì lỗi |
| Lỗi của backend | có bất kỳ bản ghi lỗi mới nào thì lỗi |

Lệnh, lịch chạy và ngưỡng nằm ở `deployment/OPERATIONS_RUNBOOK.md` §2, §7 và §9 và trong các hướng dẫn theo nền tảng
([`deployment/SYNOLOGY_NAS.md`](./deployment/SYNOLOGY_NAS.md) §8, [`deployment/VPS.md`](./deployment/VPS.md) §8).

#### Yêu cầu đối với platform proxy

Reverse proxy của DSM, hoặc Caddy trên VPS, phải:

- kết thúc HTTPS bằng một certificate mà các workstation của công ty tin, và chỉ gửi HTTP như một lần chuyển hướng
  sang HTTPS;
- chuyển tiếp tới `http://127.0.0.1:<port>`, dùng đúng chuỗi `127.0.0.1` và không bao giờ dùng `localhost`, vì
  `localhost` có thể được phân giải thành địa chỉ IPv6 `::1` trước trong khi `web` chỉ lắng nghe trên loopback IPv4;
- gửi `Host`, `X-Forwarded-For` (với địa chỉ client được nối vào cuối) và `X-Forwarded-Proto: https`;
- nhận request body ít nhất 5 MiB và dùng send timeout và read timeout ít nhất 300 s, để phản hồi JSON của `web` hoặc
  của application tới trước;
- không bao giờ ghi log cookie, `X-PartFlow-Station-Device` hay `X-PartFlow-CSRF`;
- chỉ cho các nguồn LAN hoặc VPN đã được duyệt truy cập, cùng với firewall của host.

**Địa chỉ client thật.** `web` tin forwarded header từ đúng một hop. Mặc định hop đó được phát hiện khi container khởi
động, là default gateway của nó trên network edge (địa chỉ mà các kết nối loopback của host đi tới từ đó). Chỉ đặt
`PARTFLOW_TRUSTED_PROXY` (một địa chỉ IPv4) khi platform proxy tới từ một địa chỉ khác. Nếu không cách nào cho ra địa
chỉ, container từ chối khởi động. Từ hop đó, `web` lấy địa chỉ **cuối cùng** trong `X-Forwarded-For` làm client và chỉ
gửi địa chỉ đó cho backend; `X-Forwarded-Proto` chỉ được chấp nhận từ cùng hop đó. Nếu proxy không cung cấp được địa
chỉ client, mọi client dùng chung một bucket rate limit; hãy ghi lại điều này và để owner quyết định. `web` không
publish certificate và không đọc cấu hình TLS nào.

Thiết lập DSM nằm ở [`deployment/SYNOLOGY_NAS.md`](./deployment/SYNOLOGY_NAS.md) §5 và ví dụ Caddy nằm ở
[`deployment/VPS.md`](./deployment/VPS.md) §4.

#### Quy trình certificate

Certificate nêu hostname của PartFlow làm tên thay thế của chủ thể (SAN). Nó được cấp bởi một trong các nơi sau:

- một tổ chức cấp chứng chỉ (CA) public dùng giao thức Automatic Certificate Management Environment (ACME), cho một
  tên DNS public;
- CA nội bộ của công ty;
- một CA riêng được tạo trên host cho mục đích này, cho một tên chỉ dùng nội bộ (các bước DSM nằm ở
  `deployment/SYNOLOGY_NAS.md` §5.2).

Không bao giờ dùng certificate tự ký đơn thuần, và không bao giờ chấp nhận certificate trên từng workstation bằng
cách bỏ qua cảnh báo của browser. Root certificate của CA nội bộ hoặc CA riêng được cài là tin cậy trên mọi
workstation và barcode terminal, qua công cụ quản lý thiết bị của công ty hoặc bằng tay. Deployment administrator chịu
trách nhiệm gia hạn; `check.sh` giám sát hạn dùng (check `certificate`, cảnh báo khi còn dưới 21 ngày,
`deployment/OPERATIONS_RUNBOOK.md` §9). Để kiểm tra hạn dùng từ bất kỳ client nào:

```sh
openssl s_client -connect <host>:443 -servername <host> </dev/null 2>/dev/null | openssl x509 -noout -subject -enddate
```

Các bước theo nền tảng nằm ở `deployment/SYNOLOGY_NAS.md` §5 và `deployment/VPS.md` §4.

#### Tách biệt environment

Production dùng Compose project, database volume, thư mục secret, hostname và vị trí backup (`PARTFLOW_BACKUP_DIR`)
riêng; không thứ nào trong số đó được dùng chung với hoặc trỏ tới một stack staging hay development.

- Production bắt đầu từ một database volume mới, rỗng, rồi migration, rồi first-run setup. Dữ liệu staging hoặc
  development không bao giờ được gắn vào, dùng lại hay copy vào đó.
- Một lần restore có chủ đích vào production theo `deployment/OPERATIONS_RUNBOOK.md` §6 path 3 hoặc quy trình host
  failure của §8 (restore sang instance mới), và phê duyệt của owner được ghi lại trước khi `backend` khởi động.
- Staging do công cụ Deployment Admin `pf` quản lý không được cài trên Docker daemon của production, và `release.sh`
  từ chối khi có project `partflow-staging` ở đó. Staging thủ công bằng `compose.yaml` của
  `deployment/SYNOLOGY_NAS.md` §4 được dừng, xóa container và **giữ lại** volume, trước khi production khởi động.
- `PARTFLOW_SITE_TIMEZONE` bằng giá trị của staging (§6).
- Không bao giờ chạy `down -v`, hoặc xóa volume, trên production project: việc đó xóa database.

#### Lệnh của operator

| Mục đích | Lệnh |
| --- | --- |
| Preflight (§6, tách biệt environment) | `docker ps -a --format '{{.Label "com.docker.compose.project"}}' \| sort -u` không liệt kê `partflow-staging` |
| Validate configuration | `$PF config --quiet` |
| Build một release (tag và commit trong shell; cách dùng duy nhất của file build) | `PARTFLOW_RELEASE=<new> PARTFLOW_COMMIT=$(git rev-parse HEAD) $PF -f compose.production.build.yaml build` |
| Khởi động database | `$PF up -d db` |
| Áp migration và grant (một lần mỗi release, khi `backend` đã dừng) | `PARTFLOW_RELEASE=<new> $PF --profile ops run --rm -T --user "$(id -u):$(id -g)" migrate (--pre-release-backup NAME --backup-not-before UTC \| --no-backup-reason TEXT)` (lần cài đầu: tag đã có trong `.env.production`) |
| Lấy backup (hằng ngày, thủ công) | `deploy/production/backup.sh --kind daily --operator … --keep-daily N --keep-weekly N` · `--kind manual --operator … --reason …` (`OPERATIONS_RUNBOOK.md` §3) |
| Verify một backup, hoặc xem trước việc dọn backup theo retention | `$PF --profile ops run --rm -T --user "$(id -u):$(id -g)" backup-tools backup-verify NAME` · `… backup-tools backup-rotate --keep-daily N --keep-weekly N --dry-run` |
| Restore drill trong một project cô lập | `deploy/production/restore-test.sh --backup NAME --operator …` (`OPERATIONS_RUNBOOK.md` §4) |
| Tạo hoặc sửa database role; đặt hoặc xoay vòng password của chúng | `$PF --profile ops run --rm -T db-roles` (= `provision-roles`) |
| Áp grant ngoài một lần migrate (sau restore, hoặc để sửa sai lệch) | `$PF --profile ops run --rm -T db-roles apply-grants` |
| Check toàn vẹn guard (reconcile check (h), bằng `partflow_app`) | `$PF run --rm --no-deps -T backend python -m app.cli reconcile --check h` |
| Privilege probe (bằng chứng; một lần gọi cho mỗi bảng và câu lệnh, không dòng nào, được rollback) | `$PF exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=0 -c BEGIN -c "SET LOCAL ROLE partflow_app" -c "UPDATE part_movements SET id = id WHERE false" -c ROLLBACK'` (bị từ chối = stderr có `permission denied for table <t>`; được phép = stdout có `UPDATE 0` và không có `ERROR`; các biến được mở rộng bên trong `db`) |
| Release (toàn bộ trình tự của §7) | `deploy/production/release.sh --release <new> --operator … --approver … [--pre-release-backup NAME \| --no-backup-reason TEXT]` |
| Smoke check cho release đang chạy | `deploy/production/smoke.sh --release <tag>` |
| Revision mong đợi và database revision, readiness | `$PF run --rm --no-deps -T backend python -m app.cli revision` |
| Khởi động hoặc tạo lại application | `$PF up -d backend web` |
| Trạng thái và log | `$PF ps` · `$PF logs --since=15m backend web db` |
| Health (readiness: release, schema, revision) qua `web` trên host | `curl --fail --silent --show-error http://127.0.0.1:${PARTFLOW_HTTP_PORT}/api/health` · liveness: `…/api/health/live` |
| Reconciliation (cả khi `backend` đang dừng) | `$PF run --rm --no-deps -T backend python -m app.cli reconcile` |
| Diễn tập identity của một candidate image (check (j)) | `PARTFLOW_RELEASE=<new> $PF run --rm --no-deps -T backend python -m app.cli reconcile --check j` |
| Lệnh khôi phục | `$PF run --rm --no-deps backend python -m app.cli reset-password …` · `… restore-correction-permission-management …` |
| Write freeze | `$PF stop backend` (có thể chờ tới 200 s khi một lần import đang hoàn tất, và các request gửi trong lúc đó treo tới 60 s, xem Mô hình process và lần chạy đầu: bắt đầu khi không có lần import nào đang chạy) · mở lại bằng `$PF up -d backend` trên cùng release; ở lần chuyển release, việc mở lại là bước chuyển `web` của `release.sh` (§7) |
| First-run setup | `PARTFLOW_BACKEND_WORKERS=1 $PF up -d backend`, đọc token bằng `$PF logs backend \| grep "Setup token"`, hoàn tất setup, rồi `$PF up -d backend` |

## 4. Quyết định nền tảng

| Nền tảng | Mức phù hợp | Vai trò khuyến nghị |
| --- | --- | --- |
| Synology NAS với Container Manager | Tốt cho triển khai nội bộ nhỏ khi model hỗ trợ các container cần thiết và NAS có lưu trữ, bộ nhớ, giám sát đáng tin cậy, có nguồn điện liên tục (UPS) và backup đã kiểm thử | Production hiện tại (quyết định của owner 2026-11-09, §1) |
| Linux VPS | Đích portable tốt nhất về lâu dài | Hướng nâng cấp ưu tiên cho production, truy cập từ xa, kiểm soát Docker ổn định và khôi phục off-site độc lập |
| Shared hosting như Hawk Host | Có điều kiện và không dùng ngay được | Chỉ dùng nếu nhà cung cấp chứng minh hỗ trợ process ASGI/FastAPI native, PostgreSQL, routing cần thiết, migration, job và các cơ chế khôi phục; nếu không thì chọn VPS |

Chuyển từ Synology sang VPS là triển khai lại một release cộng với một lần backup và restore PostgreSQL đã verify,
không phải viết lại application.

## 5. Yêu cầu khi go-live

Không có pilot và không có danh sách release gate (quyết định của owner 2026-11-09, §1); không mục nào trong phần này
chặn việc dùng thật hay một release. Danh sách đầu là những gì phần mềm cần để chạy được. Danh sách sau bảo vệ dữ liệu
của xưởng và được thiết lập sau go-live, khi tiện.

### Cần để phần mềm chạy được

- **HTTPS ở platform proxy.** `compose.production.yaml` cố định `SESSION_COOKIE_SECURE=true`, nên browser chỉ giữ
  cookie đăng nhập qua HTTPS, và chỉ publish `web` trên `127.0.0.1`, nên platform reverse proxy là lối vào duy nhất
  (§3.1, Yêu cầu đối với platform proxy; thiết lập DSM nằm ở `deployment/SYNOLOGY_NAS.md` §5.2). Proxy phải nhận
  request body ít nhất 5 MiB và dùng timeout ít nhất 300 s, nếu không thì upload ảnh và import Work Order sẽ lỗi; lần
  import lớn nhất cho phép, 2.000 Work Order, mất khoảng nửa phút. Mọi workstation và Scan Station phải tin
  certificate (§3.1, Quy trình certificate).
- **First-run setup** với một backend worker cho đến khi có Administrator đầu tiên (§7 bước 8; setup token nằm trong
  log backend), rồi enroll từng thiết bị Scan Station ở Administration → Scan Stations. Enrollment code và device
  token là bearer credential: giống session cookie, chúng chỉ được truyền qua HTTPS. Thiết bị bị mất hoặc ngừng
  dùng phải được revoke tại đó.
- **Không có Compose project `partflow-staging` trên Docker daemon của production.** `release.sh` từ chối chạy khi
  còn project đó (§3.1, Tách biệt environment). Hãy thử thay đổi trên workstation.
- **Release từ git tag.** `release.sh` chỉ build đúng tag đang checkout (đặt tên theo §10.2) với build input không
  đổi, và cần `--operator` và `--approver`; một người có thể ghi cùng một tên cho cả hai. Mặc định nó lấy một backup
  đã verify trước mỗi release.

### Nên làm sau go-live

Mỗi mục là một platform task hoặc một lệnh; chi tiết nằm ở `deployment/OPERATIONS_RUNBOOK.md`.

- Task `backup.sh` hằng ngày và một bản sao mã hóa ngoài host (OPERATIONS_RUNBOOK §3). Cho đến khi task này chạy,
  backup duy nhất là các backup `release.sh` lấy trước mỗi release.
- Một lần chạy `restore-test.sh` có đo thời gian, để biết backup restore được và mất bao lâu (OPERATIONS_RUNBOOK §4).
- `check.sh` mỗi 15 phút và `scheduled-reconcile.sh` hằng ngày, kèm failure notification của nền tảng
  (OPERATIONS_RUNBOOK §9 và §7).
- Firewall chỉ cho các mạng của xưởng truy cập, giờ của host chính xác, và có UPS.
- Cập nhật bảo mật có kiểm soát cho DSM, Container Manager và các base image.

Check "Hot list entries are active demand" (reconcile check (i), `deployment/OPERATIONS_RUNBOOK.md` §7) chạy query Hot
list dưới đây. Trên database bình thường query không trả về dòng nào; mỗi dòng trả về là một Hot list entry không còn
active, được gỡ ở Management → Priority:
`SELECT d.id, d.priority_rank FROM work_order_demands d JOIN work_orders w ON w.id = d.work_order_id WHERE d.priority_rank IS NOT NULL AND (w.completed_at IS NOT NULL OR d.requested_quantity <= d.allocated_quantity);`

## 6. Tách biệt environment

Dùng database, secret, URL và vị trí backup riêng cho:

- `development`: chỉ dữ liệu của developer;
- `staging`: dữ liệu giả hoặc đã làm sạch, dùng để diễn tập release;
- `production`: dữ liệu của xưởng đã được phép.

Không bao giờ restore dữ liệu production vào development khi chưa được phép rõ ràng và chưa làm sạch. Không bao giờ cho
staging và production trỏ tới cùng một database. Các quy tắc cho production nằm ở §3.1, Tách biệt environment.

Mọi environment đặt `SITE_TIMEZONE` (tên múi giờ IANA như `America/Los_Angeles`; `UTC` khi không đặt) theo múi giờ lịch
của xưởng; production stack đặt nó từ `PARTFLOW_SITE_TIMEZONE`. Backend validate giá trị này khi khởi động và dùng nó,
không bao giờ dùng giờ local của browser, để ghi ngày hoàn thành của Work Order (do đó cả Done range lẫn kết quả đúng
hạn hay trễ hạn của lịch sử đã hoàn thành) và ngày nhận mặc định của Work Order mới và Work Order được import. Staging
và production phải dùng cùng một giá trị.

## 7. Luồng triển khai chung

Mọi nền tảng theo cùng thứ tự release. `deploy/production/release.sh` và `smoke.sh` thực hiện các bước 5 đến 11 và lấy
pre-release backup của bước 4 (§3.1, Release); các lệnh thủ công và điểm quyết định nằm ở
[`deployment/OPERATIONS_RUNBOOK.md`](./deployment/OPERATIONS_RUNBOOK.md) §5.

1. Chọn và ghi lại một release tag và commit bất biến theo §10.
2. Kiểm tra CI đã pass cho đúng commit đó.
3. Đọc migration note từ release đang chạy đến release đích.
4. Kiểm tra backup mới nhất và lấy một pre-release backup mới. `release.sh` lấy backup này, đã verify, bên trong write
   freeze khi có migration đang chờ (bước 6), và `migrate` từ chối một backup cũ, của database khác hoặc bị sửa đổi
   (`deployment/OPERATIONS_RUNBOOK.md` §3 và §5).
5. Build image đích mà không thay thế release đang chạy; `release.sh` build candidate và không bao giờ chạm vào release
   đang chạy.
6. Khi có migration đang chờ, vào write freeze bằng cách dừng `backend` (`deployment/OPERATIONS_RUNBOOK.md` §5).
7. Chạy migration đúng một lần và giữ output. `migrate` áp nó trong một transaction, từ chối khi `backend` đang kết nối
   và từ chối thay đổi schema không transactional.
8. Chuyển application. `backend` khởi động trước trên release mới trong khi `web` vẫn phục vụ bundle trước, nên write
   gate từ chối các lần ghi từ những trang đang mở; sau khi health check đạt, `web` được chuyển và write mở lại. Với
   database chưa có Administrator, khởi động backend với một worker (`PARTFLOW_BACKEND_WORKERS=1 $PF up -d backend`,
   để chỉ có một setup token, §3.1), hoàn tất first-run setup (setup token nằm trong log backend) trước khi mở truy
   cập, rồi khởi động lại với số worker đã cấu hình (`$PF up -d backend`). Sau đó enroll từng thiết bị Scan Station
   (Administration → Scan Stations).
9. Chạy smoke check cho health, API, giao diện người dùng, authorization, scan-focus và ghi rồi đọc lại, bằng dữ liệu
   test được chỉ định.
10. Chạy các check reconciliation về số lượng và Movement. `release.sh` chạy reconciliation trước và sau release; một
    check thất bại sau lần chuyển sẽ dừng `backend` lại.
11. Ghi lại release đã deploy, migration head, operator, thời gian và kết quả (`release.sh` ghi `record.json`).
12. Giữ release trước và pre-release backup cho đến hết observation window.

## 8. Hướng dẫn theo nền tảng

- [`deployment/SYNOLOGY_NAS.md`](./deployment/SYNOLOGY_NAS.md): staging và production trên Synology NAS của công ty.
- [`deployment/SYNOLOGY_ADMIN.md`](./deployment/SYNOLOGY_ADMIN.md): công cụ Deployment Admin `pf` cho staging; §1 của
  tài liệu đó ghi mục tiêu thiết kế controller tái sử dụng đã lên kế hoạch.
- [`deployment/VPS.md`](./deployment/VPS.md): production trên một Linux VPS.
- [`deployment/SHARED_HOSTING.md`](./deployment/SHARED_HOSTING.md): các điều kiện để dùng shared hosting.
- [`deployment/OPERATIONS_RUNBOOK.md`](./deployment/OPERATIONS_RUNBOOK.md): vận hành hằng ngày.

## 9. Tài liệu tham khảo nền tảng bên ngoài

Các tài liệu này mô tả khả năng của nền tảng, không phải mức sẵn sàng của PartFlow:

- [Synology Container Manager](https://www.synology.com/en-us/dsm/feature/container-manager) mô tả các Project nhiều
  container từ file Compose.
- [Synology Container Manager Project help](https://kb.synology.com/en-us/DSM/help/ContainerManager/docker_project?version=7)
  là tài liệu giao diện để tạo và vận hành một Project.
- [Hawk Host Python application guide](https://www.hawkhost.com/kb/programming/python/how-to-create-python-application/)
  mô tả triển khai Python qua `mod_passenger`; tự nó không chứng minh khả năng tương thích ASGI/FastAPI native.
- [Hawk Host remote PostgreSQL guide](https://www.hawkhost.com/kb/web-hosting/how-do-i-allow-remote-postgresql-connections/)
  xác nhận PostgreSQL có trong môi trường đó và truy cập từ xa cần bộ phận hỗ trợ của nhà cung cấp đưa vào whitelist;
  vẫn phải xác nhận khả năng của từng gói trước khi chọn shared hosting.

## 10. Release và Versioning

Mục này sở hữu quy tắc đặt tên release, release note và checklist phát hành của PartFlow. Một release là một ứng viên
triển khai có thể truy vết, không phải bằng chứng sẵn sàng cho production; nó được triển khai như §7 mô tả.

### 10.1 Định danh release

Dùng một application release chung cho frontend, backend và migration, tất cả từ cùng một commit. Ghi lại Git tag và
commit SHA đầy đủ của nó. Alembic revision định danh database schema một cách riêng; package metadata không phải lịch
sử release. Release identity được nhúng vào cả hai production image và được `GET /api/health` báo (`release`,
`commit`) (§3.1, Health và release identity).

Tạo release cho một phiên bản được chọn để test hoặc triển khai, không phải cho mọi commit. Triển khai lại cùng phiên
bản không cần release mới. Không bao giờ di chuyển, ghi đè hoặc dùng lại tag của một release đã phát hành, hay thay
build artifact đã phát hành của nó. Nội dung release thay đổi cần một version mới.

Dùng tag theo version, không dùng `Stage`, `Production`, `Latest` hay số phase. Các tag không theo version đã có có thể
giữ làm mốc lịch sử, nhưng không được trở thành đích triển khai di động. Environment và mục đích nằm trong release
note.

### 10.2 Quy ước version

Dùng [Semantic Versioning](https://semver.org/spec/v2.0.0.html) với tiền tố `v` viết thường cho Git tag:
`vMAJOR.MINOR.PATCH`, có thể thêm `-alpha.N`, `-beta.N` hoặc `-rc.N`. Bắt đầu `N` từ 1 và không dùng số 0 ở đầu.
`release.sh` từ chối một production release có tag không theo dạng này.

| Tình huống | Ví dụ |
| --- | --- |
| Snapshot development nội bộ đầu tiên được chọn cho staging | `v0.1.0-alpha.1` |
| Snapshot tiếp theo của cùng release đang dự kiến | `v0.1.0-alpha.2` |
| Mốc development mới có phạm vi lớn hơn đáng kể | `v0.2.0-alpha.1` |
| Đã triển khai phạm vi dự kiến; còn cần kiểm thử rộng hơn | `v0.2.0-beta.1` |
| Ứng viên cho production release ổn định đầu tiên | `v1.0.0-rc.1` |
| Production release ổn định đầu tiên | `v1.0.0` |
| Sửa lỗi tương thích sau `v1.0.0` | `v1.0.1` |
| Bổ sung tính năng tương thích sau `v1.0.0` | `v1.1.0` |
| Thay đổi phá vỡ contract được hỗ trợ sau `v1.x` | `v2.0.0` |

Đây là ví dụ, không phải tag dành sẵn hay chuỗi bắt buộc. Chọn version chưa dùng tiếp theo dựa trên lịch sử release
thực tế và phạm vi thay đổi. Trước 1.0, tính tương thích không được bảo đảm, nhưng breaking change vẫn phải được ghi
lại.

Với PartFlow, contract được hỗ trợ gồm hành vi API đã được tài liệu hóa và các yêu cầu về configuration và nâng cấp dữ
liệu. Riêng một database migration không bắt buộc tăng major; hãy đánh giá tác động của nó đến tính tương thích.

`alpha`, `beta` và `rc` thể hiện mức độ hoàn thiện của release, không phải host triển khai. Đánh dấu cả ba là GitHub
pre-release và không bao giờ chỉ định chúng là bản stable mới nhất. Có thể diễn tập một version stable trên staging
trước production. Hậu tố không bao giờ chứng minh rằng việc kiểm tra triển khai đã đạt.

### 10.3 Release title và description

Dùng `PartFlow <tag>` kèm một mục đích ngắn tùy chọn, ví dụ `PartFlow v0.1.0-alpha.1 — Internal Staging`. Giữ release
title và description bằng tiếng Anh trừ khi được yêu cầu rõ ràng khác đi.

Với release đầu tiên, tóm tắt phạm vi đã triển khai. Với các release sau, mô tả thay đổi từ release trước được chọn
đến đúng commit đích, không chỉ commit mới nhất hay kế hoạch phase. Không đưa vào công việc chưa commit.

Dùng template description này, thay các placeholder bằng thông tin đã xác minh:

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

Không bao giờ suy ra "No new migrations", tính tương thích hay kết quả validation thành công từ tên release; hãy kiểm
tra khoảng revision và bằng chứng thực tế. Kết quả chưa biết giữ là `Pending` hoặc `Not verified` trong bản nháp và
không bao giờ được báo là đã pass. Giữ kết quả smoke test trên NAS tách riêng khỏi kết quả CI. Không bao giờ đưa
secret, credential hay chi tiết triển khai riêng tư vào release note public.

### 10.4 Checklist phát hành

1. Chọn đúng commit đích và release trước dùng để so sánh. Giải quyết mọi thay đổi chỉ có ở máy local trước khi chọn
   source để phát hành.
2. Kiểm tra trùng tag ở local và remote. Xác nhận CI của repo và các quality check liên quan đã pass cho đúng commit
   đó; một lần chạy thành công cho commit khác không được tính.
3. Rà soát thay đổi, migration, configuration, yêu cầu rollback và các giới hạn đã biết. Hoàn tất release note.
4. Tạo annotated tag tại commit đã chọn, không phải tại đầu một branch đang di chuyển chưa được kiểm tra, và push đúng
   tag đó.
5. Trong GitHub Releases, tạo bản nháp release từ tag đã có. Nhập title và description, đặt cờ pre-release nhất quán
   với §10.2, và đính kèm các build artifact dự định trước khi phát hành.
6. Phát hành, rồi triển khai theo §7 và hướng dẫn của nền tảng, ghi lại tag, commit, database revision, operator, thời
   gian và kết quả smoke test. Giữ release trước và các backup cần thiết.

Có thể chuẩn bị bản nháp trước khi validation hoàn tất. Phát hành release và triển khai nó là hai thao tác riêng; một
yêu cầu chuẩn bị thông tin release không cho phép làm thao tác nào trong hai thao tác đó.

### 10.5 Workflow hiện tại và ranh giới rollback

`.github/workflows/ci.yml` chạy cho các lần push lên `main` và cho pull request. Workflow kiểm tra code, build
development image và production image với giá trị dùng một lần, và chạy các production static test. Nó không phát hành
release image hay triển khai gì khi một tag hoặc release được tạo. Source archive của GitHub không phải production
image build sẵn, và một source tag cố định không bảo đảm các lần build lại về sau giống hệt nhau khi base image có thể
thay đổi.

Release thủ công là đủ cho giai đoạn này. Deployment record giữ local image ID của mọi release. Khi việc phát hành
production image lên registry được triển khai, hãy ghi registry digest bên cạnh release tag và giữ lại các artifact đã
triển khai. Không đưa vào một version service riêng, hệ thống branch release hay công cụ phát hành tự động chỉ để áp
dụng quy ước này.

Rollback theo operations runbook: chuyển application tag không hoàn tác migration. Hãy xác nhận tính tương thích của
schema hoặc dùng đường khôi phục database, có tính đến các lần ghi sau thời điểm backup. Không bao giờ giả định rằng
restore một backup cũ hơn giữ lại các Movement production mới hơn.

Tham khảo: [Git tags](https://git-scm.com/docs/git-tag) và
[GitHub release management](https://docs.github.com/en/repositories/releasing-projects-on-github/managing-releases-in-a-repository).

## Lịch sử thay đổi

| Ngày | Commit | Thay đổi |
| --- | --- | --- |
| 2026-11-09 | uncommitted | Khôi phục các quy tắc mà lần viết lại đã bỏ sót: test về key configuration, quyền đọc log khi còn setup token, credential và revoke thiết bị, cập nhật bảo mật và lưu ý về hậu tố version. |
| 2026-11-09 | `b405c6e` | Viết lại thành nội dung hiện hành cho người mới đọc; bỏ pilot và release gate theo quyết định của owner. |
| 2026-10-14 | `2c360cb` | Trỏ tới mục tiêu thiết kế controller tái sử dụng đã lên kế hoạch của Deployment Admin. |
| 2026-10-09 | `03bd90c` | Thêm structured log, request id, lệnh status và các script giám sát. |
| 2026-10-08 | `a330383` | Thêm backup đã verify, restore drill cô lập và pre-release backup bên trong write freeze. |
| 2026-10-08 | `1116b07` | Thêm các database role tách riêng và grant của chúng. |
| 2026-10-08 | `b820e7a` | Thêm release identity, readiness, write gate và `release.sh`. |
| 2026-10-08 | `d7e86a9` | Thêm production Compose stack, các image và bảng kê configuration. |
| 2026-10-07 | `3fb66df` | Thêm lệnh reconciliation chỉ đọc. |
| 2026-09-08 | `8d358ee` | Thêm quy ước release và versioning. |
| 2026-09-02 | `d2f68de` | Phiên bản đầu của hướng dẫn triển khai cùng bản tiếng Việt. |
