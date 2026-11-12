# Hướng dẫn triển khai PartFlow

> **Bản gốc:** [`DEPLOYMENT.md`](./DEPLOYMENT.md). Đồng bộ với bản tiếng Anh tại commit `8a7e2b5` cộng các thay đổi chưa
> commit ngày 2026-11-10. Bản tiếng Anh là nguồn chuẩn (source of truth); khi hai bản khác nhau, bản tiếng Anh có
> hiệu lực.

Hướng dẫn này là điểm khởi đầu để cài đặt và vận hành PartFlow trong môi trường production. Hãy đọc §1 và §2 trước: hai
phần này giải thích bạn đang cài gì, mỗi việc cần làm theo tài liệu nào, và các quy tắc an toàn mà mọi quy trình đều dựa
vào. Phần còn lại của hướng dẫn là tài liệu tham chiếu; các quy trình sẽ trỏ về đây khi bạn cần một chi tiết.

## 1. Bắt đầu từ đây

### 1.1 Bạn đang cài gì

Trong production, PartFlow là một tập hợp các Docker container được định nghĩa trong `compose.production.yaml` (Compose
project `partflow-production`). Các image được build ngay trên host từ một tag phát hành của repository này. Ba
container chạy liên tục:

- `db`: PostgreSQL, lưu mọi bản ghi của PartFlow trong Docker volume `partflow-production_postgres_data`;
- `backend`: giao diện lập trình ứng dụng (API) của PartFlow, một ứng dụng FastAPI;
- `web`: nginx, phục vụ các màn hình của PartFlow và chuyển mọi yêu cầu `/api` đến `backend`.

Người dùng không bao giờ kết nối trực tiếp đến các container này. Họ mở `https://<PartFlow host name>`. Reverse proxy
của máy chủ (reverse proxy của DiskStation Manager (DSM) trên Synology NAS, hoặc Caddy trên máy chủ Linux) kết thúc kết
nối HTTPS rồi chuyển yêu cầu đến `web` trên địa chỉ loopback `127.0.0.1` của chính máy chủ, nơi không máy tính nào khác
truy cập được:

```text
Browser or Scan Station
        │  HTTPS, port 443
        ▼
Reverse proxy of the host (DSM reverse proxy, or Caddy on a VPS)
        │  HTTP to 127.0.0.1:18080, reachable only from the host itself
        ▼
web (nginx): PartFlow screens, request size limits, sign-in rate limits
        │  /api
        ▼
backend (FastAPI) ── private container network ── db (PostgreSQL)
```

Ngoài các container, một bản cài đặt có bốn mục trên máy chủ mà bạn tự tạo và chăm sóc:

| Mục | Nội dung | Ví dụ trên NAS |
| --- | --- | --- |
| Thư mục mã nguồn của bản phát hành (release checkout) | Một bản git clone của repository này với tag phát hành đã được checkout. Mọi lệnh chạy từ thư mục gốc của nó. | `/volume1/docker/partflow/repo` |
| `.env.production` | Cấu hình, không chứa mật khẩu nào, đặt trong thư mục gốc của release checkout. | `/volume1/docker/partflow/repo/.env.production` |
| Thư mục bí mật (secrets folder) | Ba tệp mật khẩu của database. | `/volume1/docker/partflow/secrets` |
| Thư mục sao lưu (backup folder) | Mỗi bản sao lưu database một thư mục con. | `/volume1/docker/partflow/backups` |

Các script trong `deploy/production/` làm giúp bạn những việc rủi ro: `release.sh` cài một bản phát hành mới,
`backup.sh` tạo bản sao lưu, `restore-test.sh` chứng minh bản sao lưu khôi phục được, còn `check.sh` và
`scheduled-reconcile.sh` giám sát bản cài đặt.

### 1.2 Cần theo tài liệu nào

| Tôi muốn … | Theo |
| --- | --- |
| Cài PartFlow lần đầu trên Synology NAS của công ty | [`deployment/SYNOLOGY_NAS.vi.md`](./deployment/SYNOLOGY_NAS.vi.md) §1 đến §10, sau đó §11 khi thuận tiện |
| Cho phép mọi người truy cập PartFlow từ Internet (tùy chọn) | [`deployment/SYNOLOGY_PUBLIC_HTTPS.vi.md`](./deployment/SYNOLOGY_PUBLIC_HTTPS.vi.md), ở bước HTTPS của hướng dẫn NAS (§9) |
| Cài bản phát hành mới, sao lưu hoặc kiểm thử bản sao lưu, kiểm tra bản cài đặt, quay lui, hoặc xử lý sự cố | [`deployment/OPERATIONS_RUNBOOK.vi.md`](./deployment/OPERATIONS_RUNBOOK.vi.md) |
| Chạy PartFlow trên máy chủ ảo riêng (virtual private server, VPS) Linux thay cho NAS | [`deployment/VPS.vi.md`](./deployment/VPS.vi.md) |
| Quyết định xem shared web hosting có dùng được không | [`deployment/SHARED_HOSTING.vi.md`](./deployment/SHARED_HOSTING.vi.md) |
| Tra cứu một thiết lập, một giới hạn hoặc chức năng của một container | §3.1 của hướng dẫn này |
| Đặt tên và công bố một bản phát hành (dành cho lập trình viên) | §10 của hướng dẫn này |

Công cụ Deployment Admin `pf` và hướng dẫn của nó [`deployment/SYNOLOGY_ADMIN.vi.md`](./deployment/SYNOLOGY_ADMIN.vi.md)
quản lý các instance staging riêng biệt. Chúng không thuộc bản cài đặt production, và không bước nào ở trên cần đến
chúng.

### 1.3 Những gì được hỗ trợ

| Cách dùng | Trạng thái |
| --- | --- |
| Máy trạm của lập trình viên | Được hỗ trợ với development stack `compose.yaml` ([`README.vi.md`](../README.vi.md) ở thư mục gốc). Hãy thử bản phát hành mới ở đây trước khi cài vào production. |
| Production trên Synology NAS của công ty | Được hỗ trợ. Đây là máy chủ production đang dùng. |
| Production trên Linux VPS | Được hỗ trợ như máy chủ thay thế. |
| Truy cập từ mạng công ty hoặc mạng riêng ảo (VPN) | Được hỗ trợ (hướng dẫn NAS §9, phương án A). |
| Truy cập từ Internet | Được hỗ trợ thông qua Cloudflare như [`deployment/SYNOLOGY_PUBLIC_HTTPS.vi.md`](./deployment/SYNOLOGY_PUBLIC_HTTPS.vi.md) mô tả. Các thiết kế khác không được tài liệu hóa. |
| Stack PartFlow thứ hai (staging) trên máy chủ production | Không được hỗ trợ: `release.sh` từ chối chạy khi trên cùng Docker daemon có Compose project `partflow-staging` (§6). |
| Shared web hosting | Không được hỗ trợ, trừ khi nhà cung cấp chứng minh được mọi yêu cầu của `deployment/SHARED_HOSTING.md`. |

### 1.4 Trạng thái

Production stack, các script của nó và các quy trình phát hành, sao lưu, khôi phục đã hoàn chỉnh. Các bài kiểm thử tự
động của chúng chạy trong một Linux container và trên Docker Desktop. Các hướng dẫn theo nền tảng đi theo các script đó
từng bước; khi một bước hoạt động khác đi trên máy chủ thật, hãy sửa hướng dẫn trong cùng thay đổi với bản sửa lỗi.

Theo quyết định của chủ sở hữu (owner) ngày 2026-11-09, PartFlow được đưa vào sử dụng thật ngay khi production stack
được cài xong: không có giai đoạn chạy thử (pilot) và không có danh sách cổng phát hành (release gates), và các vấn đề
được sửa ở những bản phát hành sau. Chỉ danh sách đầu tiên của §5 là bắt buộc. Sao lưu và giám sát được khuyến nghị
mạnh nhưng không bao giờ chặn việc sử dụng.

## 2. Thuật ngữ, quy ước và quy tắc an toàn

### 2.1 Thuật ngữ

| Thuật ngữ | Ý nghĩa |
| --- | --- |
| Bản phát hành (Release) | Một phiên bản của PartFlow: một git tag như `v1.0.0` (§10.2) và commit mà tag đó trỏ tới. Cả hai image của một bản phát hành được build từ commit đó và mang tag của nó. |
| Release checkout | Bản git clone với tag phát hành đã được checkout. Mọi lệnh chạy từ thư mục gốc của nó. |
| Tài khoản vận hành (Operating account) | Tài khoản duy nhất chạy mọi lệnh PartFlow, `release.sh` và mọi tác vụ theo lịch, đồng thời sở hữu thư mục sao lưu. Trên Synology NAS đó là `root`, vì Docker trên DSM cần như vậy. |
| `PF` | Một biến shell rút gọn lệnh Compose của production stack (bên dưới). Nó không phải công cụ Deployment Admin `pf`. |
| Ops job | Một container chạy một lần (`migrate`, `db-roles`, `backup-tools` hoặc `status`) chỉ chạy khi được khởi động bằng `$PF --profile ops run`. |
| Tạm dừng ghi (Write freeze) | `backend` được dừng có chủ đích để không thể ghi gì, ví dụ trong lúc migration database. `web` vẫn phục vụ các màn hình, các màn hình này hiển thị OFFLINE và chặn mọi thay đổi. |
| Thiết lập lần đầu (First-run setup) | Tạo Administrator đầu tiên trong trình duyệt bằng mã thiết lập (setup token) dùng một lần mà `backend` ghi vào log của nó. |
| Đối soát (Reconciliation) | Lệnh kiểm tra tính nhất quán chỉ đọc `python -m app.cli reconcile`, với các kiểm tra (a) đến (j) trong runbook §7. Nó không bao giờ thay đổi dữ liệu. |
| Thư mục hồ sơ triển khai (Records directory) | Thư mục `~/partflow-deployments` của tài khoản vận hành, mỗi bản phát hành, lần diễn tập khôi phục, lần quay lui hoặc lần khôi phục có một thư mục con. |
| Chủ sở hữu (Owner) | Người phê duyệt các bản phát hành, các lần khôi phục và sửa chữa. Hiện nay owner cũng là người vận hành; một người có thể được ghi tên vừa là người vận hành (operator) vừa là người phê duyệt (approver). |

Mọi lệnh production đều dùng biến shell `PF`. Hãy đặt biến này trong mỗi phiên terminal mới, tại thư mục gốc của release
checkout:

```sh
PF="docker compose -f compose.production.yaml --env-file .env.production"
```

Khi đó `$PF ps` có nghĩa là `docker compose -f compose.production.yaml --env-file .env.production ps`.

### 2.2 Quy tắc an toàn

Các quy tắc này bảo vệ dữ liệu production. Mọi quy trình đều tuân theo chúng; không bao giờ vi phạm một quy tắc để tiết
kiệm thời gian.

1. **Không bao giờ chạy production từ `compose.yaml`.** Tệp đó là development stack: nó chạy các server phát triển tự
   động tải lại, công bố port của database, dùng một database role cho mọi việc và có các giá trị mặc định cho phát
   triển. Production chỉ dùng `compose.production.yaml`.
2. **Không bao giờ chạy `$PF down -v`, và không bao giờ xóa một volume của project `partflow-production`.** Cả hai đều
   xóa database production. `$PF down` không có `-v` là cách an toàn duy nhất để dừng mọi thứ.
3. **Không bao giờ xóa image của bản phát hành trước** (ví dụ bằng `docker image prune -a`) khi bạn còn có thể quay lui:
   quay lui cần chúng.
4. **Production bắt đầu từ một database mới, trống.** Không bao giờ gắn, sao chép hoặc khôi phục dữ liệu phát triển hay
   dữ liệu thử nghiệm vào đó, và không bao giờ cho hai bản cài đặt trỏ vào cùng một database.
5. **Không bao giờ sửa dữ liệu PartFlow trực tiếp trong database.** Lịch sử Movement chỉ được thêm vào (append-only) theo
   thiết kế; việc hiệu chỉnh đi qua các quy trình hiệu chỉnh của chính PartFlow, và mọi việc sửa chữa là quyết định của
   owner.
6. **Không bao giờ lặp lại một lệnh production chưa rõ kết quả dưới một định danh mới.** Một lệnh bị mất câu trả lời
   giữ nguyên `device_event_id` ban đầu và chỉ được thử lại với chính nó (runbook §2 và §8).
7. **Không bao giờ chạy `alembic downgrade` trên production.** Quay lui đi theo cây quyết định trong runbook §6.
8. **Coi bản sao lưu, tệp bí mật và báo cáo đối soát là bí mật.** Chúng chứa băm mật khẩu, bản băm (digest) của token
   phiên và token thiết bị, cùng các giá trị badge.

## 3. Tham chiếu production stack

Phần này là tài liệu tham chiếu. Các bước cài đặt nằm trong các hướng dẫn theo nền tảng
([`deployment/SYNOLOGY_NAS.vi.md`](./deployment/SYNOLOGY_NAS.vi.md), [`deployment/VPS.vi.md`](./deployment/VPS.vi.md)) và
các quy trình vận hành nằm trong [`deployment/OPERATIONS_RUNBOOK.vi.md`](./deployment/OPERATIONS_RUNBOOK.vi.md); các tài
liệu đó trỏ về đây khi bạn cần một chi tiết.

### 3.1 Production stack

#### Các tệp

| Tệp | Mục đích |
| --- | --- |
| `compose.production.yaml` | Production stack: Compose project `partflow-production` cùng các service, network, volume và tệp bí mật của nó. Không có phần build. |
| `compose.production.build.yaml` | Tệp đi kèm chỉ dành cho build, bổ sung hai lần build image. Chỉ dùng với `build`. |
| `.env.production.example` | Mẫu của tệp cấu hình `.env.production`. |
| `backend/Dockerfile`, `frontend/Dockerfile` | Mỗi tệp có một stage `production` mà chỉ bản build production dùng; `frontend/nginx/` chứa cấu hình của `web`. |
| `deploy/production/` | Các script liệt kê ở cuối phần này. Mỗi script in các tùy chọn của nó với `--help`. |

#### Các service

| Service | Chức năng | Chi tiết |
| --- | --- | --- |
| `db` | PostgreSQL 16 (`postgres:16.14`, biến thể Debian) | Chỉ ở network `internal`; không công bố port. Dữ liệu nằm trong volume `partflow-production_postgres_data`. Không bao giờ dùng image `-alpine`: việc sắp xếp văn bản (collation) và kiểm tra định danh "Canonical identity under the running interpreter and database" (kiểm tra đối soát (j)) phụ thuộc vào GNU C Library (glibc). Ghi lỗi không kèm dòng chi tiết (`log_error_verbosity=terse`). Thời gian chờ dừng (stop grace period) 60 s. |
| `backend` | API, image `partflow/backend:<release>` | Các network `internal` và `edge`. Kết nối bằng database role `partflow_app` và chỉ mount tệp mật khẩu của role đó. `SESSION_COOKIE_SECURE`, `ENFORCE_CLIENT_RELEASE` và `DATABASE_ROLES_REQUIRED` được cố định là `true`. Số worker lấy từ `PARTFLOW_BACKEND_WORKERS`; chỉ tin các header được chuyển tiếp từ subnet `edge`. Thời gian chờ dừng 200 s, cao hơn thời gian yêu cầu dài nhất của `web` (180 s), để một lần import đang chạy có thể hoàn tất. `restart: unless-stopped`, không bao giờ `on-failure`, vì với nhiều worker một lỗi cấu hình thoát với mã 0. |
| `web` | nginx, image `partflow/web:<release>` | Network `edge`. Port được công bố duy nhất: `127.0.0.1:${PARTFLOW_HTTP_PORT}` tới port 80 của container; cố ý không có thiết lập nào cho địa chỉ bind. Không có `depends_on`, nên nó vẫn phục vụ các màn hình khi `backend` đang dừng. Thời gian chờ dừng 200 s. |
| `migrate` | Ops job: `python -m app.cli migrate` | Áp dụng các migration database và quyền (grants) trong một transaction. Cần một tùy chọn sao lưu (Migrations, bên dưới). Mount thư mục sao lưu ở chế độ chỉ đọc tại `/backups`. |
| `db-roles` | Ops job: `python -m app.cli provision-roles` (mặc định) hoặc `apply-grants` | Tạo hoặc sửa các database role và áp dụng các quyền (Database roles, bên dưới). Kết nối bằng owner role và mount ba tệp bí mật. |
| `backup-tools` | Ops job: `python -m app.cli backup-manifest`, `backup-verify` hoặc `backup-rotate` | Do `backup.sh` dùng. Không có network, không có database và không có bí mật; chỉ mount thư mục sao lưu tại `/backups`. |
| `status` | Ops job: `python -m app.cli status` | Báo cáo chỉ đọc dưới role `partflow_app`, với thư mục sao lưu được mount chỉ đọc. Do `check.sh` dùng. |

Mỗi service có chính sách restart, giới hạn bộ nhớ và bộ xử lý lấy từ `.env.production`, và xoay vòng log (`json-file`,
10 MiB mỗi tệp, 5 tệp). Mỗi service chạy lâu dài đều có health check; health check của `backend` gọi route liveness, nên
một schema không khớp không bao giờ đánh dấu container là unhealthy. Network `internal` không có đường ra ngoài máy
chủ. Hãy chạy các ops job đọc hoặc ghi thư mục sao lưu với `--user "$(id -u):$(id -g)"`, vì thư mục đó có mode 0700 và
thuộc về tài khoản vận hành.

#### Tệp cấu hình

`.env.production` là bản sao của `.env.production.example` trong thư mục gốc của release checkout. Git bỏ qua nó, mode
của nó là 600, và nó không chứa mật khẩu nào. Hãy viết mọi giá trị không có dấu nháy. Một bài kiểm thử tĩnh kiểm tra
rằng các key của tệp mẫu đúng bằng các tham chiếu `${NAME}` của hai tệp Compose production.

| Key | Ý nghĩa | Bắt buộc hoặc mặc định |
| --- | --- | --- |
| `PARTFLOW_RELEASE` | Tag của bản phát hành đang **chạy**. `release.sh` thay đổi nó khi chuyển sang bản phát hành mới. | bắt buộc |
| `PARTFLOW_COMMIT` | Commit đầy đủ của bản phát hành đang được build. Hãy để trống: lệnh build đặt nó trong shell. | trống |
| `PARTFLOW_ACCEPT_SCHEMA_REVISION` | Chỉ dành cho đường quay lui 2 (runbook §6): revision database duy nhất mà bản phát hành đang chạy được phép phục vụ. `release.sh` xóa nó. | trống |
| `PARTFLOW_SECRETS_DIR` | Đường dẫn tuyệt đối của thư mục bí mật, nằm ngoài release checkout (thư mục mode 0700, tệp mode 0444). | bắt buộc |
| `PARTFLOW_BACKUP_DIR` | Đường dẫn tuyệt đối của thư mục sao lưu production: nằm ngoài release checkout, thư mục bí mật và mọi thư mục lưu trữ (archive), không bao giờ dùng chung với bản cài đặt khác hay một lần diễn tập khôi phục, mode 0700, thuộc về tài khoản vận hành. Nó phải tồn tại trước lệnh `$PF` đầu tiên, vì **mọi** lệnh `$PF` đều cần nó. | bắt buộc |
| `PARTFLOW_SITE_TIMEZONE` | Múi giờ của nhà máy, theo tên IANA như `America/Los_Angeles` (§6). | bắt buộc |
| `PARTFLOW_HTTP_PORT` | Port loopback mà reverse proxy kết nối tới. | `18080` |
| `POSTGRES_USER`, `POSTGRES_DB` | Tên của database owner role và của database. | `partflow_owner`, `partflow` |
| `PARTFLOW_BACKEND_WORKERS` | Số tiến trình worker của `backend`. | `2` |
| `PARTFLOW_EDGE_SUBNET` | Subnet của network `edge`, cũng là các địa chỉ mà `backend` tin cho header được chuyển tiếp. Nó không được dùng ở bất kỳ nơi nào khác trên máy chủ hay mạng công ty. | `172.30.250.0/24` |
| `PARTFLOW_TRUSTED_PROXY` | Một địa chỉ IPv4 mà `web` tin cho header được chuyển tiếp. Để trống nghĩa là tự động phát hiện (Platform proxy requirements, bên dưới). | trống |
| `PARTFLOW_{DB,BACKEND,WEB,OPS}_{MEMORY,CPUS}` | Giới hạn bộ nhớ và bộ xử lý của từng service; các ops job dùng chung các giá trị `OPS`. Giá trị `0` của `CPUS` nghĩa là không giới hạn bộ xử lý, điều mà một host có kernel không hỗ trợ hạn mức bộ xử lý cần đến (hướng dẫn NAS §3). | `1g`/`1.0`, `1g`/`2.0`, `128m`/`0.5`, `512m`/`1.0` |

Các thiết lập sau được cố định trong `compose.production.yaml` và không thể cấu hình: `SESSION_COOKIE_SECURE=true`,
`ENFORCE_CLIENT_RELEASE=true`, `DATABASE_ROLES_REQUIRED=true` (trên `backend` và `migrate`), `DATABASE_HOST=db`,
`DATABASE_PORT=5432`, các đường dẫn tệp bí mật và việc bind loopback của `web`.

Backend đọc kết nối database của nó theo đúng một trong hai dạng: `DATABASE_URL` (phát triển, kiểm thử, tích hợp liên
tục (CI) và staging), hoặc `DATABASE_HOST`, `DATABASE_NAME`, `DATABASE_USER` và `DATABASE_PASSWORD_FILE` với
`DATABASE_PORT` tùy chọn, là dạng mà production stack dùng. Dùng cả hai dạng cùng lúc, hoặc không dạng nào đầy đủ, đều
bị từ chối khi khởi động. Vì ứng dụng tự dựng kết nối, mật khẩu không cần mã hóa đặc biệt nào, và không thông báo lỗi
nào lặp lại một giá trị đầu vào.

#### Tệp bí mật

Không có mật khẩu nào là một giá trị cấu hình và không có mật khẩu nào có giá trị mặc định. Các mật khẩu là ba tệp
trong thư mục bí mật, được mount vào các container dưới `/run/secrets`:

| Tệp | Do ai dùng | Quy tắc |
| --- | --- | --- |
| `postgres_password` | owner role: `db`, `migrate`, `db-roles` | Một dòng. `db` chỉ đọc nó **khi tạo một volume database mới, trống**; `migrate` và `db-roles` đọc nó mỗi lần chạy. `backend` không bao giờ mount nó. |
| `partflow_app_password` | `backend`, `status`, `db-roles` | Một dòng gồm 16 đến 128 ký tự ASCII in được, không có khoảng trắng. |
| `partflow_maintenance_password` | `db-roles` | Cùng quy tắc, và khác với `partflow_app_password`. |

Hãy tạo ba tệp trước lệnh đầu tiên khởi động `backend` hoặc một ops job. Nếu thiếu một tệp, Compose sẽ tạo một
**thư mục** rỗng có tên đó; hãy xóa thư mục đó trước (`rmdir <path>`), rồi mới tạo tệp. `release.sh` từ chối chạy và
không thay đổi gì khi bất kỳ tệp nào trong ba tệp bị thiếu, rỗng hoặc không phải tệp thường. Để tạo chúng, hãy đặt
`PARTFLOW_SECRETS_DIR` trong shell thành cùng đường dẫn tuyệt đối như trong `.env.production` rồi chạy:

```sh
mkdir -p "$PARTFLOW_SECRETS_DIR" && chmod 0700 "$PARTFLOW_SECRETS_DIR"
for name in postgres_password partflow_app_password partflow_maintenance_password; do
    openssl rand -base64 32 > "$PARTFLOW_SECRETS_DIR/$name"
    chmod 0444 "$PARTFLOW_SECRETS_DIR/$name"
done
```

Mỗi tệp chứa đúng một dòng (dấu xuống dòng cuối bị bỏ qua), vì PostgreSQL chỉ lấy mật khẩu của role mới từ dòng đầu
tiên. Tệp có nhiều dòng, rỗng, không đọc được hoặc không phải UTF-8 bị từ chối kèm thông báo nêu tên tệp, không bao giờ
nêu nội dung. Đổi mật khẩu về sau là một việc thường quy (runbook §9).

#### Image và định danh bản phát hành

Image được build trên máy chủ từ bản phát hành đã checkout và không bao giờ được pull (`pull_policy: never`). Luôn build
cả hai image cùng nhau, với tag phát hành và commit đầy đủ của nó đặt trong shell:

```sh
PARTFLOW_RELEASE=<tag> PARTFLOW_COMMIT=$(git rev-parse HEAD) $PF -f compose.production.build.yaml build
```

Ở lần cài đặt đầu tiên, tag đã có sẵn trong `.env.production`, nên có thể bỏ `PARTFLOW_RELEASE=<tag>`. Vì
`compose.production.yaml` không có phần build, `up` hoặc `run` với một tag mà image chưa có sẽ thất bại với
`No such image` thay vì build checkout hiện tại dưới tag đó. Một tag đã tồn tại không bao giờ bị build lại.

- **Image `backend`:** Python 3.12 slim với các dependency production đã khóa, không có test, `.env` hay server reload.
  Nó chạy với user `10001:10001` bằng
  `uvicorn app.main:app --host 0.0.0.0 --port 8000 --no-access-log --log-config app/core/logging.production.json`
  và không bao giờ migrate database khi khởi động.
- **Image `web`:** `nginx:1.30.5-alpine` chính thức đã ghim phiên bản, cùng frontend được build bằng `npm run build`,
  lệnh này cũng kiểm tra rằng bản build không chứa dữ liệu xem trước của phát triển.
- **Định danh bản phát hành.** Lệnh build từ chối một tag không phải gồm 1 đến 64 chữ cái, chữ số, `.`, `_` hoặc `-`
  (hoặc là `development`) và một commit không phải gồm 40 ký tự thập lục phân viết thường. Cả hai image lưu tag và
  commit dưới dạng các label `org.opencontainers.image.version` và `org.opencontainers.image.revision`; image
  `backend` còn lưu dưới dạng `RELEASE_TAG` và `RELEASE_COMMIT` (ngoài một image phát hành, chúng là `development` và
  trống). Image `web` ghi tag vào các màn hình và vào meta tag `partflow-release`. Mỗi trang gửi tag của bản build của nó
  trong request header `X-PartFlow-Release`, mà backend so sánh chính xác với tag của chính nó.

#### Route sức khỏe và cổng ghi (write gate)

Backend có hai route sức khỏe:

- **Liveness, `GET /api/health/live`**, trả lời `{"status":"live","service","release","commit"}` mà không chạm vào
  database. Mọi health check của container đều dùng nó.
- **Readiness, `GET /api/health`**, còn đọc revision của database. Các key của nó là `status`, `service`, `database`,
  `release`, `commit`, `schema`, `expected_revision`, `database_revision` và `accepted_revision`. Nó trả lời `200`
  với `"status":"ok"` chỉ khi `schema` là `current` hoặc `accepted`, `503 not_ready` khi là `mismatch`, và
  `503 unavailable` với `"schema":"unknown"` khi không kết nối được database.

| `schema` | Ý nghĩa |
| --- | --- |
| `current` | Revision của database bằng Alembic head duy nhất của image đang chạy. |
| `accepted` | Database ở đúng một revision được `PARTFLOW_ACCEPT_SCHEMA_REVISION` nêu tên mà image đang chạy không biết (đường quay lui 2, runbook §6). Một revision mà image biết sẽ bị bỏ qua kèm cảnh báo, và lệnh `revision` khi đó báo `"override_ignored": true`. |
| `mismatch` | Bất kỳ revision database nào khác. |
| `unknown` | Không đọc được database. |

**Cổng ghi (write gate).** Backend từ chối mọi `POST`, `PUT`, `PATCH` và `DELETE`, trên bất kỳ path nào, trước khi làm
bất cứ việc gì khác, trong hai trường hợp. Cả hai lần từ chối đều không ghi gì và mang `Cache-Control: no-store`; các
màn hình hiển thị chúng như `GUI_DESIGN.md` §3 quy tắc 13 mô tả.

| Câu trả lời | Khi nào | Ý nghĩa với người dùng |
| --- | --- | --- |
| `409` với `release_mismatch: true` | `X-PartFlow-Release` của yêu cầu bị thiếu hoặc khác bản phát hành đang chạy: trang được tải từ một bản phát hành cũ hơn. | Chắc chắn bị từ chối; không có gì được ghi nhận. Phải tải lại trang. |
| `503` với `not_ready: true` | Schema là `mismatch`, hoặc không xác nhận được readiness. | Kết quả không xác định. |

Các thao tác đọc và các route sức khỏe không bao giờ bị chặn. Việc áp đặt bản phát hành (`ENFORCE_CLIENT_RELEASE`) được
bật trong production và tắt trong phát triển và kiểm thử, và backend từ chối khởi động khi việc áp đặt đang bật mà tag là
`development`. Thao tác ghi duy nhất trên một yêu cầu đọc, thời điểm thấy lần cuối của một thiết bị Scan Station, không
chứa dữ liệu production nào và được chấp nhận trong lúc schema không khớp.

#### Migration và lệnh `revision`

`python -m app.cli migrate` (job `migrate`) áp dụng các migration database đang chờ trên một kết nối trong một
transaction, áp dụng các quyền (grants) của database trong cùng transaction, và in một báo cáo JSON có `result` là
`upgraded`, `already_current`, `refused`, `failed` hoặc `outcome_unknown`.

- Nó cần đúng một trong hai: `--pre-release-backup NAME` (một bản sao lưu của `backup.sh`, được xác minh trước khi
  migrate; khi có migration đang chờ thì kèm cả `--backup-not-before UTC`, thời điểm bắt đầu tạm dừng ghi) hoặc
  `--no-backup-reason TEXT` (chỉ cho lần cài đặt đầu tiên và các buổi diễn tập; lý do được ghi lại). Các quy tắc mà bản
  sao lưu trước phát hành phải đáp ứng nằm trong runbook §5.
- Nó nhận `--lock-timeout SECONDS` (1 đến 600, mặc định 30) và `--backup-dir` (mặc định `/backups`).
- Nó từ chối và không thay đổi gì khi gặp: một migration có thay đổi schema không giao dịch (non-transactional), một
  revision database mà bản phát hành không biết, một `migrate` thứ hai chạy cùng lúc, và một `backend` đang kết nối
  (kiểm tra ở mức cố gắng hết sức: hãy tự dừng `backend`).
- Việc từ chối vì database role hoặc grant (`roles_not_provisioned`, `roles_incomplete`, `role_unsafe`,
  `role_owns_objects`, `foreign_grantor`, `table_unclassified`, `table_missing`, `not_superuser`) kết thúc là `refused`
  với mã thoát (exit code) 1, và phần nâng cấp của cùng lần chạy bị hoàn tác.

Backend không bao giờ tự migrate. `python -m app.cli revision` chỉ đọc: nó in định danh bản phát hành, các revision
mong đợi và revision của database, các revision đang chờ và readiness mà backend sẽ báo, và thoát với mã 0 chỉ khi
schema là `current`.

#### Database role và quyền (grants)

Database production có ba role:

| Role | Do ai dùng | Ghi chú |
| --- | --- | --- |
| Owner (`POSTGRES_USER`, mặc định `partflow_owner`) | `db`, `migrate`, `db-roles`, các bản sao lưu | Superuser PostgreSQL được tạo cùng database; nó sở hữu mọi object. Tên của nó phải khác hai role bên dưới. |
| `partflow_app` | `backend` (API và các lệnh `reconcile`, `revision` cùng các lệnh khôi phục của nó) và `status` | Chỉ giữ các đặc quyền trong bảng tiếp theo. |
| `partflow_maintenance` | chưa có service nào | Dành riêng cho việc bảo trì lưu trữ (archival) đã được lên kế hoạch; chỉ giữ `SELECT` trên một danh sách bảng có tên. |

| Loại bảng | Đặc quyền của `partflow_app` | Bảng |
| --- | --- | --- |
| append-only | `SELECT`, `INSERT` | `part_movements`, `audit_events`, `machine_lifecycle_events`, `quantity_flow_lineage`, `work_order_allocations` |
| guarded update | `SELECT`, `INSERT`, `UPDATE` | `worker_sessions` (một row trigger giới hạn các cột) |
| no update | `SELECT`, `INSERT`, `DELETE` | `assigned_route_steps` |
| read only | `SELECT` | `alembic_version` |
| ordinary | `SELECT`, `INSERT`, `UPDATE`, `DELETE` | mọi bảng khác |

- `provision-roles` (mặc định của `db-roles`) tạo hoặc sửa `partflow_app` và `partflow_maintenance` với
  `LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS`, không có membership, không có thiết lập theo
  role và không sở hữu object nào, và đặt mật khẩu của chúng (SCRAM-SHA-256) từ các tệp bí mật trong một transaction.
  Lặp lại lệnh này là an toàn.
- `apply-grants` suy ra mọi grant từ bảng phân loại trong mã và áp dụng chúng trong một transaction. Nó từ chối trừ khi
  database đang ở Alembic head của bản phát hành.
- Mỗi lần `migrate` áp dụng các grant sau khi nâng cấp trong cùng lần chạy, nên một bản phát hành không bao giờ để lại
  bảng mới không có grant. Một bảng chưa được phân loại, một role có thuộc tính hoặc membership bị cấm, một role sở hữu
  object, hoặc một đặc quyền do một role mà PartFlow không quản lý cấp, sẽ hoàn tác toàn bộ lần chạy.
- Với `DATABASE_ROLES_REQUIRED=true` (production), các role bị thiếu bị từ chối là `roles_not_provisioned`. Khi không
  có nó (phát triển, kiểm thử, staging), báo cáo grants ghi `not_provisioned` và không cấp gì.
- Grants không bao giờ được khôi phục từ bản sao lưu: sau khi khôi phục, hãy chạy `apply-grants`.
- Không role nào giữ `TRUNCATE`, đặc quyền trên sequence, grant option, hay `CREATE` trên database hoặc schema `public`,
  và các đặc quyền của `PUBLIC` cùng các đặc quyền mặc định đều bị gỡ. Bảng phân loại đầy đủ nằm ở
  [`SLICE1_DATA_MODEL.vi.md`](./SLICE1_DATA_MODEL.vi.md) §17.

Các database trigger từ chối một thao tác ghi bị cấm là lớp bảo vệ thứ nhất; các đặc quyền bị thiếu là lớp thứ hai. Do
đó một lỗi `permission denied` (SQLSTATE 42501) trong log backend là một sự cố (runbook §8), không bao giờ là lý do để
cấp thêm quyền. Kiểm tra bảo vệ "Append-only guards and database-role privileges intact" (kiểm tra đối soát (h)) xác
minh lớp bảo vệ này. Một superuser tắt một trigger nằm ngoài khả năng phát hiện của PartFlow; giới hạn này được chấp
nhận.

#### Tiến trình backend và thiết lập lần đầu

`PARTFLOW_BACKEND_WORKERS` đặt số tiến trình worker của `backend` (Compose truyền nó dưới dạng `WEB_CONCURRENCY`, nên
giá trị biến shell `WEB_CONCURRENCY` của riêng bạn không có tác dụng).

- **Thiết lập lần đầu (first-run setup) cần một worker.** Mỗi worker có mã thiết lập (setup token) dùng một lần riêng.
  Với hai worker, trình duyệt có thể đến worker mà bạn chưa sao chép token và nhận `403 setup_token_invalid` (không ghi
  gì cả). Hãy khởi động `backend` bằng `PARTFLOW_BACKEND_WORKERS=1 $PF up -d backend`, đọc token bằng
  `$PF logs backend | grep "Setup token"`, hoàn tất thiết lập, rồi chạy `$PF up -d backend` để trở về số worker đã cấu
  hình. Mỗi lần khởi động `backend` đều ghi một token mới, vì vậy luôn dùng token mới nhất. Setup token là bí mật duy
  nhất từng được ghi vào log backend: hãy hoàn tất thiết lập trước khi bất kỳ ai khác truy cập được PartFlow, và hạn
  chế quyền truy cập log cho đến lúc đó.
- **Một lỗi cấu hình thoát với mã 0** khi có nhiều worker. Hãy đọc log và số lần restart, không đọc mã thoát.
- **Dừng một backend có nhiều worker không từ chối yêu cầu mới ngay lập tức.** Tiến trình chính giữ port mở cho đến khi
  worker cuối cùng thoát, mất tối đa đến thời gian chờ dừng 200 s trong khi một lần import hoàn tất. Một yêu cầu gửi
  trong thời gian đó được nhận nhưng không bao giờ được trả lời: nó kết thúc là 504 từ `web` sau 60 s, hoặc 502. Backend
  chưa bao giờ chạy nó, nhưng người dùng phải coi đó là kết quả không xác định. Vì vậy hãy bắt đầu tạm dừng ghi (write
  freeze) khi không có lần import nào đang chạy.

#### Yêu cầu đối với proxy của nền tảng

Reverse proxy của máy chủ (reverse proxy của DSM, hoặc Caddy trên VPS) phải:

- chấp nhận HTTPS với một chứng chỉ mà mọi máy trạm và Scan Station đều tin cậy, và không bao giờ chuyển HTTP thuần đến
  PartFlow (cùng lắm là chuyển hướng nó sang HTTPS);
- chuyển tiếp đến `http://127.0.0.1:<PARTFLOW_HTTP_PORT>` với đúng địa chỉ `127.0.0.1`, không bao giờ `localhost`, vì
  `localhost` có thể phân giải thành địa chỉ IPv6 `::1` trước, nơi `web` không lắng nghe;
- gửi header `Host`, `X-Forwarded-For` với địa chỉ client được nối vào cuối, và `X-Forwarded-Proto: https`;
- chấp nhận request body ít nhất 5 MiB và chờ câu trả lời ít nhất 300 s (timeout gửi và đọc), để câu trả lời JSON của
  `web` hoặc của ứng dụng đến trước; nếu không, việc tải ảnh lên và import Work Order sẽ thất bại;
- không bao giờ ghi log cookie hay các header `X-PartFlow-Station-Device` và `X-PartFlow-CSRF`;
- chỉ cho phép các mạng dự định, cùng với tường lửa của máy chủ.

**Địa chỉ client thật.** `web` tin các header được chuyển tiếp từ đúng một chặng (hop). Mặc định, chặng đó được phát hiện
khi container khởi động: đó là default gateway của container trên network `edge`, nơi các kết nối đến địa chỉ loopback
của máy chủ đi tới. Chỉ đặt `PARTFLOW_TRUSTED_PROXY` (một địa chỉ IPv4) khi proxy đến từ một địa chỉ khác; nếu không tìm
được địa chỉ nào, `web` từ chối khởi động. `web` lấy địa chỉ `X-Forwarded-For` **cuối cùng** làm client, chỉ gửi địa chỉ
đó đến backend, và chỉ chấp nhận `X-Forwarded-Proto` từ cùng chặng đó. Khi proxy không cung cấp được địa chỉ client, mọi
người dùng dùng chung một bucket giới hạn tần suất (Request limits, bên dưới). `web` không giữ chứng chỉ nào.

#### Yêu cầu về chứng chỉ

Chứng chỉ nêu tên miền truy cập (host name) của PartFlow làm subject alternative name (SAN). Nó đến từ một trong:

- một tổ chức cấp chứng chỉ (CA) công cộng, ví dụ Let's Encrypt qua giao thức Automatic Certificate Management
  Environment (ACME), cho một tên DNS công cộng;
- CA nội bộ của công ty;
- một CA riêng được tạo cho mục đích này, như CA mà DSM tạo (hướng dẫn NAS §9.1);
- Origin CA của Cloudflare, chỉ khi đứng sau proxy của Cloudflare ([`deployment/SYNOLOGY_PUBLIC_HTTPS.vi.md`](./deployment/SYNOLOGY_PUBLIC_HTTPS.vi.md)).

Không bao giờ dùng chứng chỉ tự ký trần, và không bao giờ để người dùng bấm bỏ qua cảnh báo của trình duyệt. Chứng chỉ
gốc của một CA nội bộ hoặc CA riêng được cài là đáng tin cậy trên mọi máy trạm và thiết bị Scan Station, thông qua quản
lý thiết bị của công ty hoặc làm thủ công. Người đã cài chứng chỉ là người gia hạn nó; `check.sh` cảnh báo 21 ngày trước
khi chứng chỉ mà nó thấy hết hạn. Để đọc ngày hết hạn từ bất kỳ máy tính nào:

```sh
openssl s_client -connect <host>:443 -servername <host> </dev/null 2>/dev/null | openssl x509 -noout -subject -enddate
```

#### Giới hạn yêu cầu, giới hạn tần suất và các câu trả lời từ `web`

| Route | Request body lớn nhất | Thời gian chờ `backend` dài nhất |
| --- | --- | --- |
| Mọi route `/api` khác | 1 MiB | 60 s |
| `PUT /api/workers/{id}/avatar`, `PUT /api/users/{id}/avatar`, `PUT /api/part-numbers/image` | 4 MiB | 60 s |
| `POST /api/work-orders/import/preview` | 4 MiB | 60 s |
| `POST /api/work-orders/import` | 4 MiB | 180 s |

Giới hạn 4 MiB cao hơn các giới hạn riêng của ứng dụng (ảnh 2 MiB, tệp import 1 MiB), nên ứng dụng tự trả lời một tệp quá
lớn hợp lý. Lần import lớn nhất được phép, 2,000 Work Order, mất khoảng nửa phút. `web` không bao giờ thử lại một yêu
cầu, nên một yêu cầu của người dùng là nhiều nhất một lần thực thi ở backend.

**Giới hạn tần suất (rate limit), theo từng địa chỉ client.** `POST /api/session` (đăng nhập) cho phép 10 mỗi phút với
burst 5. `PUT /api/session/password`, `POST /api/setup/administrator` và `PUT /api/users/{user_id}/password` cho phép 5
mỗi phút với burst 4. Các thao tác đọc và đăng xuất không bao giờ bị giới hạn. Một mã 429 từ `web` không bao giờ đến
được ứng dụng, nên nó không bao giờ được tính là đăng nhập thất bại và không bao giờ khóa tài khoản. Mã đăng ký thiết bị
(device enrollment code) không bị giới hạn tần suất (50 bit entropy, có hiệu lực 15 phút).

`web` chỉ trả lời cho các tình huống mà chính nó phát hiện; mọi câu trả lời của ứng dụng đi qua nguyên vẹn:

| Status | Khi nào | Thông báo (`detail`) | Ý nghĩa |
| --- | --- | --- | --- |
| 413 | Body lớn hơn mức route cho phép. | `This request is too large for PartFlow. Nothing was changed.` (`request_too_large: true`) | Chắc chắn bị từ chối. |
| 429 | Vượt giới hạn tần suất; gửi `Retry-After: 60`. | `Too many attempts from this computer. Wait a minute, then try again. Nothing was changed.` (`rate_limited: true`) | Chắc chắn bị từ chối. |
| 502 | Không kết nối được `backend`, hoặc `backend` đóng kết nối trước khi trả lời. | `The PartFlow server did not complete the request. If you were saving a change, check whether it was saved before repeating it.` (`server_unavailable: true`) | Kết quả không xác định đối với một thay đổi. |
| 504 | `backend` không trả lời kịp thời. | `The PartFlow server did not answer in time. If you were saving a change, check whether it was saved before repeating it.` (`server_unavailable: true`) | Kết quả không xác định đối với một thay đổi. |

Một lệnh Scan Station có thể đã được lưu được hiển thị là không xác định và được thử lại với `device_event_id` ban đầu
của nó (runbook §2). Các màn hình hiển thị một thông báo dựng sẵn cho mã 413 hoặc 429 không có JSON body, ví dụ mã đến từ
reverse proxy. `web` không thêm header chia sẻ tài nguyên giữa các nguồn (cross-origin resource sharing, CORS) nào.

**Caching và các header bảo mật.** `index.html` và mọi route của ứng dụng một trang (single-page application) là
`no-cache`; các tệp dưới `/assets/` được cache một năm, và một asset bị thiếu chỉ là 404 thuần. Mọi response mang
`X-Content-Type-Options: nosniff`, `Referrer-Policy: same-origin` và một `Content-Security-Policy` chỉ cho phép origin của
PartFlow cho script, style, kết nối và frame, cộng thêm ảnh `blob:` và `data:` cho phần xem trước ảnh cục bộ, và không có
script nội tuyến. Một tính năng cần nguồn khác phải thay đổi chính sách này trong cùng thay đổi. HTTP Strict Transport
Security (HSTS) là việc của reverse proxy; `web` không đặt nó.

#### Log

- **Request id.** Mỗi response mang một id trong `X-Request-ID`. Giá trị của client gồm 1 đến 64 ký tự `A-Za-z0-9._-`
  được giữ lại; nếu không thì một giá trị được sinh ra. `web` truyền nó cho backend, và mọi bản ghi log của yêu cầu đó
  mang nó dưới dạng `request_id`.
- **Log của `web`.** Một bản ghi JSON cho mỗi yêu cầu: địa chỉ client, method, path không kèm query string, status,
  số byte, thời lượng, status của upstream, user agent và `request_id`. Các lần thăm dò sức khỏe không được ghi log.
- **Log của backend.** Một dòng JSON cho mỗi bản ghi trên standard error: `ts`, `level`, `logger`, `message`,
  `request_id`, rồi các trường riêng của bản ghi. Một traceback không bao giờ chứa thông báo lỗi database, nên không câu
  lệnh hay giá trị nào lọt vào log.
- **Bản ghi ứng dụng (`app.access`).** Được ghi ở mức `INFO` cho một thay đổi, cho một lần từ chối có chủ đích
  (`not_ready`, `release_mismatch`, `password_check_busy`, kiểm tra hợp lệ, phân quyền) và cho một thao tác đọc mất một
  giây trở lên (`"slow":true`); ở mức `ERROR` cho một lỗi; ở mức `DEBUG` cho một lần thăm dò sức khỏe hoặc một thao tác
  đọc nhanh. Các trường của nó là `event`, `method`, `route`, `status`, `duration_ms`, `client`, `outcome` (`ok`,
  `created`, `replayed`, `refused` hoặc `error`), `slow`, `refusal` (`type`, `code`, `message`) và `context`: Part
  Number, QuantityFlow, số lượng, Area, Operation, Machine, Work Order, Work Order Demand, `device_event_id`, Scan
  Station, `user_id` và `worker_id` mà yêu cầu nêu tên. `worker_id` chỉ xuất hiện khi lệnh đã đến bước xác định danh
  tính; Worker của một lệnh được phát lại nằm trong Movement của nó.
- **Không bao giờ ghi log:** cookie, query string, các header của PartFlow, request body, token, badge, mật khẩu và các
  giá trị đã quét. Setup token của thiết lập lần đầu là ngoại lệ duy nhất đã được thông báo.
- **Dung lượng.** Mỗi container giữ tối đa 5 tệp log 10 MiB. Để ước chừng, 1,000 lệnh production ghi khoảng 1.1 MB log
  backend và 0.6 MB log `web`, và mỗi board hoặc màn hình Tracking đang mở thêm một bản ghi `web` mỗi 15 s, nên log chỉ
  bao phủ được ít ngày hơn trên một bản cài đặt bận rộn (runbook §2).

#### Script và giám sát

| Script trong `deploy/production/` | Mục đích | Quy trình |
| --- | --- | --- |
| `release.sh` | Cài một bản phát hành mới: sao lưu, build, migration, chuyển đổi và kiểm tra. | runbook §5 |
| `smoke.sh` | Kiểm tra một bản phát hành đang chạy thông qua `web` trên port loopback. | runbook §5 |
| `backup.sh` | Tạo, xác minh và xoay vòng một bản sao lưu. | runbook §3 |
| `restore-test.sh` | Khôi phục một bản sao lưu vào một stack dùng một lần và kiểm tra nó. | runbook §4 |
| `check.sh` | Được trình lập lịch của máy chủ chạy mỗi 15 phút, và thông báo lỗi của trình lập lịch chính là cảnh báo. Nó kiểm tra readiness qua HTTPS, chứng chỉ, các container, số lần restart, lỗi backend, các ổ đĩa và báo cáo `status` chỉ đọc, không bao giờ thay đổi gì, và thoát với mã khác 0 khi có điều cần báo cáo. | runbook §2 |
| `scheduled-reconcile.sh` | Chạy đối soát đầy đủ mỗi ngày một lần, sau bản sao lưu hằng ngày. | runbook §7 |
| `reconcile_regression.py`, `monitor_report.py` | Các helper mà script gọi; không bao giờ chạy bằng tay. | — |

Theo mặc định, `check.sh` báo lỗi khi bản sao lưu mới nhất cũ hơn 26 giờ, một ổ đĩa còn dưới 15 % dung lượng trống,
chứng chỉ hết hạn trong vòng 21 ngày, một container đã restart, hoặc `backend` ghi log một lỗi mới; một lỗi không đổi
được báo lại sau 6 giờ.

## 4. Chọn máy chủ

| Máy chủ | Mức phù hợp | Vai trò |
| --- | --- | --- |
| Synology NAS với Container Manager | Tốt cho một bản cài đặt nội bộ nhỏ khi NAS có lưu trữ đáng tin cậy, đủ bộ nhớ, bộ lưu điện (UPS), giám sát và các bản sao lưu đã được kiểm thử | Production hiện nay |
| Linux VPS | Mục tiêu dễ di chuyển nhất, với khả năng điều khiển Docker dự đoán được và khả năng phục hồi độc lập ngoài địa điểm | Con đường nâng cấp khi NAS không còn đủ hoặc cần một máy chủ bên ngoài |
| Shared hosting như Hawk Host | Có điều kiện và không phải là sự thay thế trực tiếp | Chỉ khi nhà cung cấp chứng minh được mọi yêu cầu của `deployment/SHARED_HOSTING.md`; nếu không thì dùng VPS |

Chuyển từ NAS sang VPS là cài đặt lại cùng bản phát hành cộng với một bản sao lưu đã xác minh và khôi phục (§8), không
phải viết lại ứng dụng.

## 5. Yêu cầu để đưa vào sử dụng (go-live)

Không có pilot và không có danh sách cổng phát hành (§1.4). Danh sách đầu tiên là những gì PartFlow cần để hoạt động
được; danh sách thứ hai bảo vệ dữ liệu nhà máy và được thiết lập sau go-live, khi thuận tiện.

### Cần để PartFlow hoạt động

- **HTTPS tại reverse proxy.** Production stack đánh dấu cookie đăng nhập là secure, nên trình duyệt chỉ giữ nó qua
  HTTPS, và nó chỉ công bố `web` trên `127.0.0.1`, nên reverse proxy là lối vào duy nhất. Proxy phải chấp nhận request
  body ít nhất 5 MiB và chờ ít nhất 300 s, và mọi máy trạm cùng Scan Station phải tin cậy chứng chỉ (§3.1, Platform proxy
  requirements và Certificate requirements).
- **Thiết lập lần đầu** với một backend worker cho đến khi Administrator đầu tiên tồn tại (§3.1, Backend processes và
  first-run setup), sau đó mỗi thiết bị Scan Station được đăng ký trong Administration → Scan Stations. Mã đăng ký và
  token thiết bị là thông tin xác thực bearer: giống cookie phiên, chúng chỉ đi qua HTTPS. Hãy thu hồi một thiết bị bị
  mất hoặc đã ngừng dùng tại đó.
- **Không có Compose project `partflow-staging` trên Docker daemon production** (§6).
- **Bản phát hành từ một git tag.** `release.sh` chỉ cài tag đang được checkout, được đặt tên như §10.2 mô tả, với đầu
  vào build không bị thay đổi, và nó cần tên của một người vận hành và một người phê duyệt. Theo mặc định, nó tạo một
  bản sao lưu đã xác minh trước mỗi bản phát hành.

### Khuyến nghị sau go-live

Mỗi mục là một tác vụ trong hướng dẫn theo nền tảng; các quy trình nằm trong runbook.

- Sao lưu hằng ngày và một bản sao được mã hóa ngoài máy chủ (runbook §3). Cho đến khi nó chạy, các bản sao lưu duy nhất
  là những bản mà `release.sh` tạo trước mỗi bản phát hành.
- Một lần diễn tập khôi phục có bấm giờ, để biết rằng một bản sao lưu khôi phục được và mất bao lâu (runbook §4).
- `check.sh` mỗi 15 phút và `scheduled-reconcile.sh` hằng ngày, cùng thông báo lỗi của trình lập lịch (runbook §2 và
  §7).
- Quy tắc tường lửa chỉ cho phép các mạng dự định, giờ máy chủ chính xác, và có UPS bảo vệ.
- Cập nhật bảo mật có kiểm soát cho máy chủ, cho Docker và cho các base image.

### Kiểm tra Hot list

Kiểm tra "Hot list entries are active demand" (kiểm tra đối soát (i)) chạy truy vấn dưới đây. Trên một database lành mạnh
nó không trả về dòng nào; một dòng được trả về là một mục Hot list mà demand của nó không còn active, và mục đó được gỡ
trong Management → Priority:

```sql
SELECT d.id, d.priority_rank FROM work_order_demands d JOIN work_orders w ON w.id = d.work_order_id WHERE d.priority_rank IS NOT NULL AND (w.completed_at IS NOT NULL OR d.requested_quantity <= d.allocated_quantity);
```

## 6. Tách biệt các môi trường

Các môi trường của PartFlow không bao giờ dùng chung database, bí mật, tên miền truy cập (host name) hay thư mục sao lưu:

- **Development** chạy trên một máy trạm với `compose.yaml` và chỉ dữ liệu của lập trình viên. Đây cũng là nơi một bản
  phát hành mới được thử trước production ([`README.vi.md`](../README.vi.md) ở thư mục gốc).
- **Staging** là tùy chọn và chỉ chứa dữ liệu tổng hợp hoặc đã được làm sạch. Nó không bao giờ chạy trên Docker daemon
  của máy chủ production. Công cụ Deployment Admin `pf` có thể quản lý một instance staging trên máy chủ khác
  ([`deployment/SYNOLOGY_ADMIN.vi.md`](./deployment/SYNOLOGY_ADMIN.vi.md)).
- **Production** chạy trên máy chủ production với `compose.production.yaml` và dữ liệu thật của nhà máy. Nó có Compose
  project, volume database, thư mục bí mật, tên miền truy cập và thư mục sao lưu riêng.

Quy tắc:

- Không bao giờ cho hai môi trường trỏ vào cùng một database, và không bao giờ sao chép dữ liệu production vào
  development mà không có sự phê duyệt rõ ràng của owner và một bản sao đã được làm sạch.
- Production bắt đầu từ một database mới, trống. Một lần khôi phục có chủ đích vào production theo runbook §6 (đường 3,
  hoặc khôi phục sang bản cài mới (new-instance restore) sau khi máy chủ hỏng), với sự phê duyệt của owner được ghi lại
  trước khi `backend` khởi động.
- Không có stack PartFlow thứ hai nào chạy trên Docker daemon production. `release.sh` từ chối chạy khi ở đó có Compose
  project `partflow-staging`.
- Mỗi môi trường đặt múi giờ của nhà máy: `SITE_TIMEZONE` trong development (tên IANA như `America/Los_Angeles`; `UTC`
  khi không đặt) và `PARTFLOW_SITE_TIMEZONE` trong production. Backend từ chối một tên không xác định khi khởi động. Nó
  dùng múi giờ này, không bao giờ dùng giờ cục bộ của trình duyệt, để xác định ngày hoàn thành Work Order (và do đó là
  khoảng Done cùng kết quả đúng hạn hay trễ hạn) và ngày nhận mặc định của các Work Order mới và được import. Hãy dùng
  cùng một giá trị ở mọi nơi.

## 7. Một bản phát hành đến production như thế nào

Mọi bản phát hành đều theo cùng một thứ tự. `deploy/production/release.sh` tự thực hiện các bước 4 đến 11, trừ các kiểm tra
thủ công của bước 9; quy trình, các tùy chọn của nó và việc cần làm khi nó dừng nằm trong runbook §5. Không bao giờ
cài đặt từ một branch hoặc bằng trình cập nhật tự động: mỗi bản phát hành là một tag được cài đặt có chủ đích.

1. Chọn tag phát hành và commit của nó (§10).
2. Kiểm tra rằng tích hợp liên tục đã đạt cho đúng commit đó.
3. Đọc những gì đã thay đổi kể từ bản phát hành đang chạy, đặc biệt là các migration database.
4. Tạo một bản sao lưu đã xác minh ngay trước khi thay đổi; nếu có migration đang chờ, tạo nó bên trong tạm dừng ghi.
5. Build các image mới mà không đụng đến các image đang chạy.
6. Bắt đầu tạm dừng ghi khi có migration đang chờ.
7. Chạy migration một lần, trong một transaction, và giữ báo cáo của nó.
8. Khởi động `backend` mới trong khi `web` vẫn phục vụ các màn hình cũ (các thao tác ghi của chúng bị từ chối), rồi
   chuyển `web`, việc này mở lại việc ghi.
9. Chạy các kiểm tra nhanh sau khi chuyển (smoke checks, `smoke.sh`), rồi kiểm tra thủ công bằng dữ liệu thử: đăng nhập
   và phân quyền, quét tại một Scan Station, và một thay đổi được lưu rồi đọc lại.
10. Chạy đối soát trước và sau thay đổi; một phát hiện (finding) mới sau khi chuyển sẽ dừng `backend` lần nữa.
11. Ghi lại bản phát hành, migration, người vận hành, thời gian và kết quả.
12. Giữ các image của bản phát hành trước và bản sao lưu cho đến khi bạn chắc chắn sẽ không quay lui.

## 8. Chuyển sang máy chủ khác

Một lần chuyển (ví dụ từ NAS sang VPS) cài đặt lại **cùng** bản phát hành trên máy chủ mới và khôi phục một bản sao lưu
cuối cùng được tạo bên trong tạm dừng ghi trên máy chủ cũ. Hai bản cài đặt không bao giờ được nhận thao tác ghi cùng
lúc. Quy trình nằm trong runbook §6 (Moving to another host).

## 9. Tham chiếu bên ngoài

Các tham chiếu này mô tả khả năng của nền tảng, không phải mức sẵn sàng của PartFlow:

- [Synology Container Manager](https://www.synology.com/en-us/dsm/feature/container-manager) và
  [Project help](https://kb.synology.com/en-us/DSM/help/ContainerManager/docker_project?version=7) của nó.
- [Synology Login Portal and reverse proxy help](https://kb.synology.com/en-in/DSM/help/DSM/AdminCenter/system_login_portal_advanced).
- [Cloudflare Origin CA](https://developers.cloudflare.com/ssl/origin-configuration/origin-ca/) và
  [Origin Rules](https://developers.cloudflare.com/rules/origin-rules/).
- [Hawk Host Python application guide](https://www.hawkhost.com/kb/programming/python/how-to-create-python-application/)
  và [remote PostgreSQL guide](https://www.hawkhost.com/kb/web-hosting/how-do-i-allow-remote-postgresql-connections/)
  (`deployment/SHARED_HOSTING.md` giải thích vì sao chúng không chứng minh được tính tương thích).

## 10. Phát hành và đánh phiên bản

Phần này sở hữu quy tắc đặt tên bản phát hành, ghi chú phát hành và danh sách kiểm tra công bố của PartFlow. Một bản
phát hành là một ứng viên có thể truy vết để triển khai, không phải bằng chứng rằng nó sẵn sàng cho production; nó được
cài như §7 mô tả.

### 10.1 Định danh bản phát hành

Một bản phát hành bao gồm frontend, backend và các migration, tất cả từ cùng một commit. Hãy ghi lại git tag và commit
đầy đủ của nó. Các revision Alembic xác định schema database một cách riêng, và metadata của package không phải là lịch
sử phát hành. Cả hai image production đều mang bản phát hành, và `GET /api/health` báo nó (`release`, `commit`).

Hãy tạo một bản phát hành cho một phiên bản được chọn để kiểm thử hoặc triển khai, không phải cho mọi commit. Cài lại
cùng một phiên bản không cần bản phát hành mới. Không bao giờ di chuyển, ghi đè hay dùng lại một tag phát hành đã công
bố, và không bao giờ thay các tệp đã công bố của nó: nội dung thay đổi cần một phiên bản mới.

Hãy dùng các tag có phiên bản, không dùng `Stage`, `Production`, `Latest` hay số của phase. Các tag hiện có theo dạng
khác có thể được giữ như lịch sử nhưng không bao giờ trở thành mục tiêu triển khai di động. Môi trường và mục đích thuộc
về ghi chú phát hành.

### 10.2 Quy ước phiên bản

Dùng [Semantic Versioning](https://semver.org/spec/v2.0.0.html) với tiền tố `v` viết thường cho git tag:
`vMAJOR.MINOR.PATCH`, tùy chọn theo sau là `-alpha.N`, `-beta.N` hoặc `-rc.N`, trong đó `N` bắt đầu từ 1 và không có số
0 đứng đầu. `release.sh` từ chối một bản phát hành production có tag theo dạng khác.

| Tình huống | Ví dụ |
| --- | --- |
| Bản chụp phát triển nội bộ đầu tiên được chọn để kiểm thử | `v0.1.0-alpha.1` |
| Một bản chụp khác của cùng bản phát hành đã lên kế hoạch | `v0.1.0-alpha.2` |
| Một cột mốc phát triển mới với phạm vi lớn hơn rõ rệt | `v0.2.0-alpha.1` |
| Phạm vi dự định đã được hiện thực; vẫn còn kiểm thử rộng hơn | `v0.2.0-beta.1` |
| Ứng viên cho bản phát hành production ổn định đầu tiên | `v1.0.0-rc.1` |
| Bản phát hành production ổn định đầu tiên | `v1.0.0` |
| Sửa lỗi tương thích sau `v1.0.0` | `v1.0.1` |
| Tính năng mới tương thích sau `v1.0.0` | `v1.1.0` |
| Thay đổi phá vỡ hợp đồng được hỗ trợ sau `v1.x` | `v2.0.0` |

Đây là các ví dụ, không phải tag được dành riêng hay một trình tự bắt buộc. Hãy chọn phiên bản chưa dùng tiếp theo dựa
trên lịch sử phát hành thật và phạm vi của thay đổi. Trước 1.0, tính tương thích không được đảm bảo, nhưng các thay đổi
phá vỡ vẫn được tài liệu hóa. Hợp đồng được hỗ trợ gồm hành vi API đã được tài liệu hóa cùng các yêu cầu về cấu hình và
nâng cấp dữ liệu; chỉ một migration database không đòi hỏi phiên bản major mới, nhưng tác động tương thích của nó được
đánh giá.

`alpha`, `beta` và `rc` biểu thị độ chín muồi, không phải máy chủ. Hãy đánh dấu cả ba là GitHub pre-release và không bao
giờ là bản phát hành ổn định mới nhất. Một hậu tố không bao giờ chứng minh rằng một kiểm tra triển khai đã đạt.

### 10.3 Tiêu đề và mô tả bản phát hành

Dùng tiêu đề `PartFlow <tag>` với một mục đích ngắn tùy chọn, ví dụ:
`PartFlow v0.1.0-alpha.1 — Internal Staging`. Hãy viết tiêu đề và mô tả bản phát hành bằng tiếng Anh trừ khi được yêu cầu khác. Với bản phát hành đầu tiên,
hãy tóm tắt phạm vi đã hiện thực; với các bản sau, hãy mô tả các thay đổi từ bản phát hành trước đến đúng commit đích,
không bao giờ là công việc chưa commit. Dùng mẫu này và thay mọi placeholder bằng thông tin đã được xác minh:

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

Không bao giờ suy ra "No new migrations", tính tương thích hay một kiểm tra đã đạt từ tên bản phát hành: hãy kiểm tra
khoảng revision và bằng chứng thực tế. Một kết quả chưa biết giữ là `Pending` hoặc `Not verified`. Hãy giữ kết quả smoke
test trên máy chủ tách biệt với kết quả CI, và không bao giờ đưa bí mật, thông tin xác thực hay chi tiết triển khai
riêng tư vào ghi chú phát hành công khai.

### 10.4 Danh sách kiểm tra công bố

1. Chọn đúng commit đích và bản phát hành trước để so sánh. Giải quyết trước các thay đổi chỉ có ở máy cục bộ.
2. Kiểm tra tag cục bộ và tag từ xa xem có trùng tên không. Xác nhận rằng CI và các kiểm tra chất lượng liên quan đã đạt
   cho đúng commit đó; một lần chạy cho commit khác không được tính.
3. Rà soát các thay đổi, migration, cấu hình, yêu cầu quay lui và các giới hạn đã biết, rồi hoàn thành ghi chú phát
   hành.
4. Tạo một annotated tag tại commit đã chọn (không bao giờ tại đầu một nhánh chưa được kiểm tra) và push tag đó.
5. Trong GitHub Releases, soạn nháp một bản phát hành từ tag đã có: nhập tiêu đề và mô tả, đặt cờ pre-release như §10.2
   nói, và đính kèm các tệp dự định.
6. Công bố, rồi cài nó như §7 và hướng dẫn theo nền tảng mô tả, và giữ bản phát hành trước cùng các bản sao lưu của nó.

Một bản nháp có thể được chuẩn bị trước khi các kiểm tra kết thúc. Công bố một bản phát hành và cài nó là hai thao tác
riêng biệt, và một yêu cầu chuẩn bị thông tin phát hành không cho phép thao tác nào trong hai thao tác đó.

### 10.5 Quy trình hiện tại và ranh giới quay lui

`.github/workflows/ci.yml` chạy cho các lần push vào `main` và cho các pull request. Nó kiểm tra mã, build các image
phát triển và các image production với giá trị dùng một lần, và chạy các bài kiểm thử tĩnh của production. Nó không công
bố image nào và không triển khai gì khi một tag hoặc bản phát hành được tạo. Các tệp nén mã nguồn của GitHub không phải
là image production dựng sẵn, và một tag cố định không đảm bảo các bản build về sau giống hệt khi base image thay đổi.

Phát hành thủ công là đủ ở giai đoạn này. Hồ sơ triển khai giữ ID image cục bộ của mọi bản phát hành. Khi các image
production sau này được công bố lên registry, hãy ghi lại digest của chúng bên cạnh tag phát hành. Đừng thêm một dịch vụ
phiên bản riêng, hệ thống phân cấp nhánh phát hành hay trình công bố tự động chỉ để áp dụng quy ước này.

Việc chuyển tag của ứng dụng không bao giờ hoàn tác một migration: một lần quay lui xác nhận tính tương thích của
schema hoặc khôi phục database (runbook §6), và tính đến các thao tác ghi sau bản sao lưu. Không bao giờ giả định rằng
khôi phục một bản sao lưu cũ hơn sẽ giữ lại các Movement production mới hơn.

Tham chiếu: [Git tags](https://git-scm.com/docs/git-tag) và
[GitHub release management](https://docs.github.com/en/repositories/releasing-projects-on-github/managing-releases-in-a-repository).

## Lịch sử thay đổi

| Ngày | Commit | Thay đổi |
| --- | --- | --- |
| 2026-11-10 | uncommitted | Viết lại thành điểm khởi đầu: bảng tác vụ, một danh sách thuật ngữ và quy tắc an toàn, và §3.1 chỉ để tham chiếu; các bước cài đặt, phát hành và quay lui nay chỉ nằm trong các hướng dẫn theo nền tảng và runbook; truy cập từ Internet được hỗ trợ thông qua Cloudflare. |
| 2026-11-09 | `398c617` | Khôi phục các quy tắc mà lần viết lại đã bỏ sót: bài kiểm thử key cấu hình, quyền truy cập log của setup token, thông tin xác thực thiết bị và việc thu hồi, cập nhật bảo mật và lưu ý về hậu tố phiên bản. |
| 2026-11-09 | `b405c6e` | Viết lại thành nội dung hiện hành cho người đọc mới; bỏ pilot và các cổng phát hành theo quyết định của owner. |
| 2026-10-14 | `2c360cb` | Trỏ đến mục tiêu thiết kế bộ điều khiển tái sử dụng đã lên kế hoạch của Deployment Admin. |
| 2026-10-09 | `03bd90c` | Thêm log có cấu trúc, request id, lệnh status và các script giám sát. |
| 2026-10-08 | `a330383` | Thêm bản sao lưu đã xác minh, diễn tập khôi phục cô lập và bản sao lưu trước phát hành bên trong tạm dừng ghi. |
| 2026-10-08 | `1116b07` | Thêm các database role riêng biệt và các grants của chúng. |
| 2026-10-08 | `b820e7a` | Thêm định danh bản phát hành, readiness, write gate và `release.sh`. |
| 2026-10-08 | `d7e86a9` | Thêm production Compose stack, các image và danh mục cấu hình của nó. |
| 2026-10-07 | `3fb66df` | Thêm lệnh đối soát chỉ đọc. |
| 2026-09-08 | `8d358ee` | Thêm các quy ước phát hành và đánh phiên bản. |
| 2026-09-02 | `d2f68de` | Phiên bản đầu tiên của hướng dẫn triển khai cùng bản tiếng Việt của nó. |
