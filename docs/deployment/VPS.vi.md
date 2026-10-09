# Triển khai PartFlow trên Linux VPS

> **Bản gốc chuẩn:** [`VPS.md`](VPS.md), được tạo
> trong gói tài liệu này trên baseline upstream
> `194ffc2e5e8e22c389abecd0830292a6707955d9`.
>
> **Trạng thái:** Đây là hướng production đích cho Phase 16. Development Compose
> hiện tại không phải gói production.
>
> **Quyền chuẩn:** Tiếng Anh là source of truth.

## 1. Khi nào nên chọn VPS

Chọn VPS khi PartFlow cần Docker/Compose ổn định, kiểm soát resource độc lập,
remote access an toàn, provider snapshot cộng với application-level backup hoặc
hướng nâng cấp sạch từ Synology. VPS giữ nguyên kiến trúc
React/FastAPI/PostgreSQL hiện có.

## 2. Artifact Phase 16 bắt buộc

Không triển khai production từ `compose.yaml`. Release phải cung cấp (trạng thái
ở P16-S2, đã triển khai trừ mục ghi còn lại):

- Dockerfile/image frontend và backend production — đã triển khai (stage
  `production`; cấu hình `web` là `frontend/nginx/`);
- production Compose configuration — đã triển khai (`compose.production.yaml`,
  [`../DEPLOYMENT.md`](../DEPLOYMENT.md) §3.1);
- reverse proxy configuration và quy trình certificate — đã ghi tài liệu (§4 và
  [`../DEPLOYMENT.md`](../DEPLOYMENT.md) §3.1); xác minh trên host ở P16-S7;
- migration command/job rõ ràng — đã triển khai (job `migrate` của Compose,
  profile `ops`, chạy `python -m app.cli migrate` trong một transaction; P16-S3,
  [`../DEPLOYMENT.md`](../DEPLOYMENT.md) §3.1);
- danh mục secret/configuration — đã triển khai (`.env.production.example`, secret
  file `postgres_password`; DEPLOYMENT §3.1);
- automation backup và restore — đã triển khai (P16-S5: `deploy/production/backup.sh`,
  `restore-test.sh`, [`OPERATIONS_RUNBOOK.md`](./OPERATIONS_RUNBOOK.md) §3 và §4);
  chạy trên VPS ở P16-S7;
- command health và reconciliation — `reconcile` đã có (P16-S1); production
  invocation của nó đã triển khai (P16-S2: các lệnh ở DEPLOYMENT §3.1); phần
  automation release quanh nó đã triển khai (P16-S3: `deploy/production/release.sh`
  và `smoke.sh`, `revision`, endpoint readiness và liveness), chưa được thực thi
  trên VPS (P16-S7);
- logging/monitoring configuration — P16-S6 (access log của `web` là request log
  trong P16-S2);
- quy trình release và rollback gắn với version bất biến — đã triển khai (P16-S3:
  [`OPERATIONS_RUNBOOK.md`](./OPERATIONS_RUNBOOK.md) §5 và §6 và `release.sh`;
  rollback path 3 đã triển khai ở P16-S5, §6 của runbook).

## 3. Baseline của host

- Linux distribution 64-bit được hỗ trợ và có security update hiện hành;
- deployment account riêng không phải root, dùng SSH key;
- Docker Engine và Compose plugin từ nguồn được hỗ trợ;
- host firewall: SSH chỉ từ nguồn quản trị, HTTP/HTTPS theo phê duyệt, từ chối
  mọi inbound port khác;
- automatic security update hoặc patch window được ghi rõ;
- NTP/timezone policy chính xác;
- persistent storage riêng cho PostgreSQL và local backup staging;
- provider/off-site backup destination được mã hóa;
- monitor resource và disk;
- không chạy workload thử nghiệm không liên quan trên production host.

Không publish PostgreSQL ra Internet. Ưu tiên private provider network cho remote
backup/database service nếu có.

## 4. DNS và TLS

Dùng hostname riêng như `partflow.company.example`. Chỉ point DNS sau khi private
smoke test pass. Reverse proxy terminate TLS, route `/` đến static frontend và
`/api` đến FastAPI. Kiểm tra mở trực tiếp SPA route và `/api/health` qua HTTPS.

Nếu PartFlow chỉ dùng nội bộ, hạn chế truy cập bằng firewall/VPN/private DNS. Dù
có public reachability vẫn cần authentication và authorization đầy đủ; URL bí
mật không phải biện pháp kiểm soát.

Với thiết kế production, host proxy terminate TLS và forward toàn bộ origin đến
tầng `web` trong stack trên địa chỉ loopback, nơi phục vụ build và route `/api`
([`../DEPLOYMENT.md`](../DEPLOYMENT.md) §3.1 liệt kê hành vi proxy bắt buộc).
Caddy trên host:

```caddyfile
partflow.company.example {
    request_body {
        max_size 5MiB
    }
    reverse_proxy 127.0.0.1:18080 {
        transport http {
            response_header_timeout 300s
        }
    }
}
```

`18080` đại diện cho `PARTFLOW_HTTP_PORT`, loopback port mà production Compose project publish. Caddy
đặt `X-Forwarded-For` thành client address và đặt `X-Forwarded-Proto`, và tự
redirect HTTP sang HTTPS. Destination là đúng literal `127.0.0.1`, không bao giờ
`localhost`. Body 5 MiB và timeout 300 s cao hơn 4 MiB và 180 s của `web` để JSON
của `web` hoặc của application thắng. Nếu kết nối loopback không đến từ gateway
của `edge` network, đặt `PARTFLOW_TRUSTED_PROXY` thành địa chỉ quan sát được
(DEPLOYMENT §3.1); đây là host check của P16-S7.

**Certificate (Caddy).** Quy tắc chung nằm ở DEPLOYMENT §3.1. Tên public nhận
certificate ACME tự động và gia hạn mà không cần directive thêm (port 80 và 443
phải truy cập được cho challenge). Tên chỉ dùng nội bộ dùng `tls
/etc/caddy/certs/partflow.crt /etc/caddy/certs/partflow.key` (certificate của CA
công ty; file mode 0600 thuộc user Caddy; thay và `caddy reload` trước khi hết
hạn) hoặc `tls internal` (CA riêng của Caddy, khi đó root của nó phải được phân
phối đến workstation như mọi CA nội bộ). Kiểm tra hạn dùng bằng lệnh `openssl
s_client` ở DEPLOYMENT §3.1. Thực hiện và xác minh ở P16-S7.

## 5. Cấu trúc filesystem

Ví dụ:

```text
/srv/partflow/
  releases/<immutable-release>/
  current -> releases/<immutable-release>/
  env/production.env
  data/
  backups/database/        = PARTFLOW_BACKUP_DIR (one directory per backup, mode 0700)
```

Manifest của backup nằm trong từng backup directory (không có `manifests/` riêng).
Deployment account sở hữu release file. Secret chỉ cho account/service cần thiết
đọc. Dữ liệu PostgreSQL không bao giờ nằm trong Git checkout.

## 6. Triển khai production lần đầu

1. Hoàn tất mọi gate ở [`DEPLOYMENT.md`](../DEPLOYMENT.md) §5.
2. Provision và harden host.
3. Cài đúng release file/image; ghi digest/commit.
4. Tạo production secret và database role theo least privilege. Tạo ba secret
   file `postgres_password`, `partflow_app_password` và
   `partflow_maintenance_password` trong `PARTFLOW_SECRETS_DIR` (mỗi file một
   dòng, [`../DEPLOYMENT.md`](../DEPLOYMENT.md) §3.1) và `.env.production` từ
   `.env.production.example`, trước mọi lệnh `$PF` khởi động `backend` hoặc một
   ops service. `PF` bên dưới là `docker compose -f compose.production.yaml
   --env-file .env.production`. Build cả hai image bằng
   `PARTFLOW_COMMIT=$(git rev-parse HEAD) $PF -f compose.production.build.yaml build`.
   Trước tiên tạo backup directory (`install -d -m 0700 /srv/partflow/backups/database`)
   và đặt `PARTFLOW_BACKUP_DIR` trỏ tới nó trong `.env.production` (không có dấu
   nháy): mọi lệnh `$PF` đều cần key này.
5. Start PostgreSQL ở private: `$PF up -d db`, rồi tạo database role:
   `$PF --profile ops run --rm -T db-roles` (P16-S4; backend kết nối bằng
   `partflow_app`, không bao giờ bằng owner).
6. Tạo database trống trong volume mới (dữ liệu production không bao giờ bắt đầu
   từ dữ liệu staging hay development; restore vào production theo
   [`OPERATIONS_RUNBOOK.md`](./OPERATIONS_RUNBOOK.md) §6 path 3 hoặc §8 host hỏng,
   với sự phê duyệt của owner được ghi lại trước khi `backend` start).
7. Chạy migration đúng một lần từ release backend image:
   `$PF --profile ops run --rm -T migrate --no-backup-reason "first install: empty database"`;
   lệnh này cũng áp dụng grant.
8. Start backend, `web` và host reverse proxy (§4). Với database chưa có
   Administrator, start backend với một worker
   (`PARTFLOW_BACKEND_WORKERS=1 $PF up -d backend web`, một setup token), hoàn tất
   first-run setup (setup token nằm trong backend log:
   `$PF logs backend | grep "Setup token"`) trước khi mở truy cập, rồi khởi động
   lại với số worker đã cấu hình (`$PF up -d backend`).
   Sau đó enroll từng thiết bị Scan Station (Administration → Scan Stations → `Devices…`).
9. Chạy smoke test và reconciliation qua HTTPS theo runbook; reconcile
   check (h) phải báo `pass`.
10. Bật monitoring và backup schedule (systemd timer ở §8), rồi chạy backup ngay:
    `deploy/production/backup.sh --kind manual --operator "<name>" --reason "initial backup"`.
11. Thực hiện và đo isolated restore trước khi nhận pilot data:
    `deploy/production/restore-test.sh --backup <backup đó> --operator "<name>"`.
12. Chỉ mở nguồn network đã duyệt và bắt đầu pilot có kiểm soát.

Để chuyển một rehearsal stack cài trước P16-S4, theo thứ tự: (1) tạo
`partflow_app_password` và `partflow_maintenance_password` trước mọi lệnh `$PF`
của Compose file mới chạy `backend` hoặc `db-roles`; (2) build cả hai image với
`PARTFLOW_COMMIT` (`PARTFLOW_RELEASE=<tag> PARTFLOW_COMMIT=$(git rev-parse HEAD)
$PF -f compose.production.build.yaml build backend web`), không bao giờ chỉ
`backend`; (3) `PARTFLOW_RELEASE=<tag> $PF --profile ops run --rm -T db-roles`;
(4) `PARTFLOW_RELEASE=<tag> $PF --profile ops run --rm -T db-roles apply-grants`
(tiền tố tag chạy image mới: `.env.production` vẫn ghi release đang chạy); (5)
`deploy/production/release.sh`
([`../DEPLOYMENT.md`](../DEPLOYMENT.md) §3.1, Chuyển một stack cài trước
P16-S4). Một rehearsal stack cài trước P16-S5 trước tiên tạo backup directory và đặt
`PARTFLOW_BACKUP_DIR` **trước mọi lệnh `$PF` với checkout P16-S5**, rồi chạy
`release.sh` (Chuyển một stack cài trước P16-S5, cùng mục).

## 7. Release và rollback

Dùng directory/image bất biến. Build/pull release mới trước khi dừng release cũ.
Backup trước migration. Không deploy trực tiếp từ checkout `main` có thể thay
đổi.

`release.sh` lấy pre-release backup đã verify bên trong write freeze.
Chỉ rollback application khi code cũ tương thích với schema đã migrate. Nếu
không, phải restore cả database pre-migration và application release tương ứng
(rollback path 3 của runbook: một database mới trong cùng instance, với sự phê
duyệt của owner được ghi lại).
Alembic downgrade không phải rollback tổng quát: migration PartFlow có thể bảo
vệ immutable history bằng cách từ chối downgrade phá dữ liệu.

Theo [`OPERATIONS_RUNBOOK.md`](./OPERATIONS_RUNBOOK.md).

## 8. Chiến lược backup

Dùng PostgreSQL logical dump làm baseline portable; có thể thêm provider volume
snapshot làm lớp thứ hai. Snapshot không thay thế logical restore đã test.
Artifact, việc verify, retention và restore drill nằm trong
[`OPERATIONS_RUNBOOK.md`](./OPERATIONS_RUNBOOK.md) §3 và §4 (đã triển khai ở
P16-S5; các unit và task dưới đây được cài và chạy trên VPS ở P16-S7):

- custom-format `pg_dump` theo lịch (`backup.sh`, một directory cho mỗi backup);
- checksum và manifest chứa release commit cùng Alembic revision;
- mã hóa khi truyền và khi lưu;
- bản copy off-VPS có retention và failure alert;
- định kỳ restore vào database cô lập;
- RPO/RTO được ghi và đo bằng lần chạy thật.

**Schedule (systemd).** `partflow-backup.service`, `Type=oneshot`,
`User=<deploy account>`, `WorkingDirectory=/srv/partflow/current`,
`ExecStart=deploy/production/backup.sh --kind daily --keep-daily 14 --keep-weekly 8 --operator scheduler --env-file /srv/partflow/env/production.env`
(14 và 8 là placeholder: owner đặt giá trị thật, và các option là bắt buộc),
`OnFailure=` một unit gửi mail cho administrator; và `partflow-backup.timer` với
`OnCalendar=*-*-* 02:00:00` và `Persistent=true`. Một exit khác 0, kể cả daily không
qua verify, làm unit thất bại. Giữ schedule ngoài maintenance window (một release
giữ backup lock).

**Bản copy off-VPS (restic).** `RESTIC_REPOSITORY` trỏ tới object storage, và
`RESTIC_PASSWORD_FILE` tới một key file mode 0400 do custody ngoài VPS giữ (mất nó
là mất các bản copy). Sau backup unit:
`restic backup --tag database /srv/partflow/backups/database`, rồi
`restic forget --tag database --keep-daily 14 --keep-weekly 8 --prune` (retention
khớp `backup-rotate`), và `restic check` hằng tuần; thất bại làm unit của nó thất
bại (`OnFailure=`). Archive directory (Phase 16 slice S8) đi vào repository hoặc tag
riêng mà `forget` không bao giờ nhắm tới: archive không bao giờ bị rotate hay prune.

## 9. Chuyển từ Synology

Dùng quy trình backup và restore cutover trong `SYNOLOGY_NAS.md` §10 (cùng backup
artifact và new-instance restore trong runbook §6). Giữ source và
target cùng release, enforce write freeze, verify checksum/reconciliation rồi
mới đổi DNS. Không chạy hai production instance writable trên database đã tách
nhánh.
