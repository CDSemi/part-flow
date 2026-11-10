# Triển khai PartFlow trên Linux VPS

> **Bản gốc chuẩn:** [`VPS.md`](VPS.md). Đồng bộ với bản tiếng Anh tại commit `2c360cb` cộng các thay đổi chưa commit
> ngày 2026-11-09. Tiếng Anh là source of truth.

Tài liệu này mô tả cách chạy PartFlow trên máy chủ ảo riêng (virtual private server, VPS) chạy Linux. VPS là host
production thay thế: host đang dùng hiện nay là Synology NAS của công ty (xem [`SYNOLOGY_NAS.md`](./SYNOLOGY_NAS.md)).
Hãy đọc tài liệu này khi NAS không còn đủ, hoặc khi công ty cần một host bên ngoài. Hãy đọc
[`../DEPLOYMENT.md`](../DEPLOYMENT.md) trước: tài liệu đó sở hữu production stack, các tệp cấu hình và các yêu cầu go-live
mà tài liệu này chỉ áp dụng cho VPS. Vận hành hằng ngày (kiểm tra sức khỏe, backup, restore, release, rollback) nằm ở
[`OPERATIONS_RUNBOOK.md`](./OPERATIONS_RUNBOOK.md).

Các lệnh trong tài liệu này dùng biến shell `PF`, bạn định nghĩa một lần trong release checkout:

```sh
PF="docker compose -f compose.production.yaml --env-file /srv/partflow/env/production.env"
```

`PF` chỉ là cách viết tắt của lệnh Docker Compose cho production stack. Nó không phải công cụ Deployment Admin `pf`.
Chạy mọi lệnh `$PF` từ thư mục của release checkout. Trên VPS, tệp cấu hình nằm ngoài các thư mục release, tại
`/srv/partflow/env/production.env` (§5), để mọi release, mọi lệnh `$PF` và mọi job theo lịch đọc cùng một tệp. Các script và systemd unit trong tài liệu này chưa được chạy
trên VPS.

## 1. Khi nào nên chọn VPS

Chọn VPS khi PartFlow cần hành vi Docker và Compose ổn định, kiểm soát tài nguyên độc lập, truy cập từ xa an toàn,
snapshot của nhà cung cấp bên cạnh backup ở tầng ứng dụng, hoặc hướng nâng cấp sạch từ Synology NAS. VPS giữ nguyên
kiến trúc React, FastAPI và PostgreSQL hiện có.

## 2. Release cung cấp những gì

Không bao giờ triển khai production từ `compose.yaml`: đó là development stack. Production release cung cấp:

- image frontend và backend production (các stage `production` của Dockerfile; cấu hình web tier nằm ở
  `frontend/nginx/`);
- cấu hình Compose production, `compose.production.yaml` ([`../DEPLOYMENT.md`](../DEPLOYMENT.md) §3.1);
- một job migration tường minh: service Compose `migrate` (profile `ops`), chạy `python -m app.cli migrate` trong một
  transaction;
- danh mục cấu hình và secret: `.env.production.example` và các secret file trong `PARTFLOW_SECRETS_DIR`
  (DEPLOYMENT §3.1);
- reverse proxy và quy trình chứng chỉ (§4 bên dưới và DEPLOYMENT §3.1);
- tự động hóa release: `deploy/production/release.sh` và `smoke.sh`, định danh release (`revision`), và các endpoint
  readiness và liveness (xem runbook §5 và §6 cho quy trình release và rollback, gồm rollback path 3);
- tự động hóa backup và restore: `deploy/production/backup.sh` và `restore-test.sh` (runbook §3 và §4);
- các lệnh sức khỏe, giám sát và reconciliation: `reconcile`, `check.sh`, `scheduled-reconcile.sh`, lệnh `status` và
  log JSON mang HTTP request id (runbook §2 và §7).

## 3. Baseline của host

VPS cần có:

- một bản phân phối Linux 64-bit được hỗ trợ, có cập nhật bảo mật hiện hành;
- một tài khoản triển khai riêng, không phải root, đăng nhập bằng khóa SSH;
- Docker Engine và Docker Compose plugin từ nguồn được hỗ trợ;
- firewall của host cho phép SSH từ các nguồn quản trị và HTTP, HTTPS theo phê duyệt, và chặn mọi cổng inbound khác;
- cập nhật bảo mật tự động, hoặc một khung thời gian vá lỗi đã được ghi lại;
- thiết lập giờ (Network Time Protocol, NTP) và múi giờ đúng;
- lưu trữ bền vững tách biệt cho PostgreSQL và cho vùng đệm backup cục bộ;
- đích backup được mã hóa của nhà cung cấp hoặc off-site;
- giám sát tài nguyên và đĩa: `check.sh` chạy bằng systemd timer ở §8, cộng với việc xem CPU và bộ nhớ thủ công bằng
  `docker stats`;
- không có workload thử nghiệm không liên quan trên host production.

Không bao giờ publish PostgreSQL ra internet. Khi nhà cung cấp có mạng riêng, hãy dùng nó cho các dịch vụ backup hoặc
database từ xa.

## 4. DNS và TLS

Dùng một hostname riêng như `partflow.company.example`. Chỉ trỏ bản ghi DNS về VPS sau khi smoke test riêng tư thành
công. Reverse proxy kết thúc Transport Layer Security (TLS) và chuyển tiếp toàn bộ origin tới tầng `web` của Compose
stack trên địa chỉ loopback; `web` phục vụ frontend đã build và định tuyến `/api` tới backend
([`../DEPLOYMENT.md`](../DEPLOYMENT.md) §3.1 liệt kê hành vi proxy mà stack yêu cầu). Sau khi proxy chạy, hãy xác nhận
các route của single-page application tải trực tiếp được và `/api/health` trả lời qua HTTPS.

Nếu PartFlow chỉ dùng nội bộ, hãy hạn chế truy cập bằng firewall, mạng riêng ảo (virtual private network, VPN) hoặc
DNS riêng. Truy cập được từ công khai vẫn đòi hỏi xác thực và phân quyền đầy đủ của ứng dụng; URL bí mật không phải một
biện pháp kiểm soát.

Ví dụ cấu hình Caddy trên host:

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

Ghi chú về ví dụ:

- `18080` là `PARTFLOW_HTTP_PORT`, cổng loopback mà Compose project production publish.
- Đích là chuỗi `127.0.0.1` nguyên văn, không bao giờ là `localhost`.
- Caddy đặt `X-Forwarded-For` bằng địa chỉ client và `X-Forwarded-Proto`, và tự chuyển HTTP sang HTTPS.
- Giới hạn body 5 MiB và timeout 300 s đặt cao hơn giới hạn 4 MiB và 180 s của `web`, để `web` hoặc ứng dụng trả lỗi JSON
  của chính nó thay vì proxy cắt request.
- Nếu kết nối loopback không đến từ gateway của mạng Compose `edge`, hãy đặt `PARTFLOW_TRUSTED_PROXY` bằng địa chỉ bạn
  quan sát được (DEPLOYMENT §3.1). Hãy kiểm tra điều này trên VPS.

**Chứng chỉ (Caddy).** Các quy tắc chung nằm ở DEPLOYMENT §3.1.

- Tên công khai nhận chứng chỉ tự động từ một tổ chức cấp chứng chỉ (certificate authority, CA) qua giao thức ACME và
  tự gia hạn, không cần directive thêm. Cổng 80 và 443 phải truy cập được để hoàn tất challenge.
- Tên chỉ dùng nội bộ dùng `tls /etc/caddy/certs/partflow.crt /etc/caddy/certs/partflow.key` (chứng chỉ từ CA của công ty;
  các tệp có mode 0600 và thuộc người dùng Caddy; thay chúng và chạy `caddy reload` trước khi hết hạn) hoặc
  `tls internal` (CA riêng của Caddy, khi đó chứng chỉ gốc của nó phải được phân phối tới mọi máy trạm như mọi CA nội bộ).
- Kiểm tra ngày hết hạn bằng lệnh `openssl s_client` ở DEPLOYMENT §3.1.

## 5. Bố cục hệ thống tệp

Bố cục ví dụ:

```text
/srv/partflow/
  releases/<immutable-release>/
  current -> releases/<immutable-release>/
  env/production.env
  data/
  backups/database/        = PARTFLOW_BACKUP_DIR (one directory per backup, mode 0700)
```

Mỗi thư mục backup chứa manifest riêng của nó; không có thư mục `manifests/` riêng. Tài khoản triển khai sở hữu các
tệp release. Secret chỉ đọc được bởi tài khoản hoặc service cần chúng. Dữ liệu PostgreSQL không bao giờ nằm trong một Git
checkout.

## 6. Triển khai production lần đầu

1. Đọc các yêu cầu go-live ở [`../DEPLOYMENT.md`](../DEPLOYMENT.md) §5.
2. Cấp phát và hardening host (§3).
3. Cài đúng các tệp và image của release, và ghi lại digest hoặc commit của chúng.
4. Tạo secret và cấu hình production trước mọi lệnh `$PF` khởi động `backend` hoặc một ops service:
   - ba secret file `postgres_password`, `partflow_app_password` và `partflow_maintenance_password` trong
     `PARTFLOW_SECRETS_DIR`, mỗi tệp một dòng ([`../DEPLOYMENT.md`](../DEPLOYMENT.md) §3.1);
   - `/srv/partflow/env/production.env` (mode 600), sao chép từ `.env.production.example` rồi điền giá trị;
   - thư mục backup, `install -d -m 0700 /srv/partflow/backups/database`, với `PARTFLOW_BACKUP_DIR` đặt bằng nó (không
     có dấu nháy) trong `/srv/partflow/env/production.env`: mọi lệnh `$PF` đều cần khóa này.

   Sau đó build cả hai image:
   `PARTFLOW_COMMIT=$(git rev-parse HEAD) $PF -f compose.production.build.yaml build`.
5. Khởi động PostgreSQL ở chế độ riêng tư bằng `$PF up -d db`, rồi tạo các database role bằng
   `$PF --profile ops run --rm -T db-roles`. Backend kết nối bằng `partflow_app`, không bao giờ bằng database owner.
6. Bắt đầu từ một database rỗng trong volume mới. Dữ liệu production không bao giờ khởi đầu từ dữ liệu staging hay
   development. Việc restore vào production theo [`OPERATIONS_RUNBOOK.md`](./OPERATIONS_RUNBOOK.md) §6 (rollback path 3)
   hoặc §8 (host failure), với sự chấp thuận của owner được ghi lại trước khi `backend` khởi động.
7. Chạy migration một lần từ image backend của release:
   `$PF --profile ops run --rm -T migrate --no-backup-reason "first install: empty database"`. Lệnh này cũng áp dụng các
   database grant.
8. Khởi động backend, `web` và reverse proxy của host (§4). Trên database chưa có Administrator, khởi động backend với
   một worker, vì first-run setup cần đúng một setup token:
   `PARTFLOW_BACKEND_WORKERS=1 $PF up -d backend web`. Đọc setup token từ log của backend bằng
   `$PF logs backend | grep "Setup token"` và hoàn tất first-run setup trước khi mở truy cập. Sau đó khởi động lại backend
   với số worker đã cấu hình (`$PF up -d backend`) và enroll từng thiết bị Scan Station
   (Administration → Scan Stations → `Devices…`).
9. Chạy các kiểm tra smoke và reconciliation của runbook qua HTTPS. Kiểm tra toàn vẹn guard, reconcile check (h), phải
   báo `pass`.
10. Khuyến nghị, và không bao giờ chặn việc sử dụng: bật các timer backup, check và reconcile cùng alert unit ở §8, rồi
    chạy backup đầu tiên ngay:
    `deploy/production/backup.sh --kind manual --operator "<name>" --reason "initial backup"`.
11. Khuyến nghị: thực hiện và đo thời gian một lần restore cô lập của backup đó
    ([`../DEPLOYMENT.md`](../DEPLOYMENT.md) §5):
    `deploy/production/restore-test.sh --backup <that backup> --operator "<name>"`.
12. Chỉ mở các nguồn mạng đã được phê duyệt và bắt đầu dùng PartFlow.

## 7. Release và rollback

Dùng thư mục release và image bất biến. Build hoặc pull release mới trước khi dừng release cũ. Backup trước khi
migration. Không bao giờ triển khai trực tiếp từ checkout `main` có thể thay đổi.

Chạy `deploy/production/release.sh` từ thư mục release mới với `--env-file /srv/partflow/env/production.env`, để nó
cập nhật đúng tệp cấu hình mà mọi job theo lịch đọc.

`release.sh` tạo backup pre-release đã xác minh bên trong write freeze. Chỉ được rollback ứng dụng khi code cũ tương
thích với schema đã migrate. Nếu không, hãy restore database trước migration và release ứng dụng tương ứng cùng nhau
(rollback path 3 của runbook: một database mới trong cùng PostgreSQL instance, với sự chấp thuận của owner được ghi
lại). Alembic downgrade không phải cách rollback chung: các migration của PartFlow có thể bảo vệ lịch sử bất biến bằng
cách từ chối downgrade phá hủy.

Làm theo [`OPERATIONS_RUNBOOK.md`](./OPERATIONS_RUNBOOK.md).

## 8. Chiến lược backup

Dùng PostgreSQL logical dump làm baseline di động, và tùy chọn thêm snapshot volume của nhà cung cấp làm lớp thứ hai.
Snapshot không bao giờ thay thế một logical restore đã được kiểm thử. Artifact backup, việc xác minh, các quy tắc
retention và restore drill được mô tả ở [`OPERATIONS_RUNBOOK.md`](./OPERATIONS_RUNBOOK.md) §3 và §4. Một chiến lược
backup gồm:

- `pg_dump` định dạng custom theo lịch (`backup.sh`, mỗi backup một thư mục);
- checksum và manifest ghi lại release commit và Alembic revision;
- mã hóa khi truyền và khi lưu;
- bản sao ngoài VPS có retention và cảnh báo khi lỗi;
- restore định kỳ vào một database cô lập;
- mục tiêu điểm khôi phục (recovery point objective, RPO) và mục tiêu thời gian khôi phục (recovery time objective,
  RTO) đo từ các lần chạy thực tế.

Backup và giám sát được khuyến nghị sau go-live và không bao giờ chặn việc sử dụng.

**Lịch (systemd).** Hai unit chạy backup hằng ngày:

- `partflow-backup.service`: `Type=oneshot`, `User=<deploy account>`, `WorkingDirectory=/srv/partflow/current`,
  `OnFailure=` một unit gửi mail cho quản trị viên, và
  `ExecStart=/srv/partflow/current/deploy/production/backup.sh --kind daily --keep-daily 14 --keep-weekly 8 --operator scheduler --env-file /srv/partflow/env/production.env`.
  Giá trị 14 và 8 là giá trị giữ chỗ: owner đặt retention thật, và cả hai option đều bắt buộc.
- `partflow-backup.timer`: `OnCalendar=*-*-* 02:00:00` và `Persistent=true`.

`ExecStart=` cần đường dẫn tuyệt đối, vì `WorkingDirectory=` không phân giải đường dẫn tương đối và systemd từ chối
unit. Kiểm tra cả hai unit bằng `systemd-analyze verify partflow-backup.service partflow-backup.timer`. Exit khác 0,
kể cả một backup daily không qua xác minh, làm unit thất bại. Giữ lịch ngoài các khung bảo trì, vì một release giữ
backup lock.

**Giám sát (systemd).** Xác minh cả năm unit bằng `systemd-analyze verify`, và dùng đường dẫn `ExecStart=` tuyệt đối
như trên. `partflow-alert@.service` là bộ thông báo lỗi của mọi unit:

```ini
[Service]
Type=oneshot
ExecStart=/bin/sh -c 'journalctl -u %i -n 60 --no-pager | mail -s "PartFlow alert: %i failed on %H" <ops address>'
```

`%i` và `%H` là specifier của systemd (systemd không bao giờ chạy `$(...)`). Bộ thông báo cần một mail transfer agent
đã cấu hình trên host, ví dụ msmtp, và quyền đọc journal cho người dùng của unit; cả hai là cấu hình host.

Hai service còn lại dùng `Type=oneshot`, `User=<deploy account>`, `WorkingDirectory=/srv/partflow/current` và
`OnFailure=partflow-alert@%n.service`:

- `partflow-check.service` chạy
  `ExecStart=/srv/partflow/current/deploy/production/check.sh --url https://<host> --env-file /srv/partflow/env/production.env`;
  `partflow-check.timer` có `OnCalendar=*:0/15`.
- `partflow-reconcile.service` chạy
  `ExecStart=/srv/partflow/current/deploy/production/scheduled-reconcile.sh --env-file /srv/partflow/env/production.env`;
  `partflow-reconcile.timer` có `OnCalendar=*-*-* 04:00:00` và `Persistent=true`.

`OnFailure=` của backup unit cũng là `partflow-alert@%n.service`. Nếu dùng cron thay systemd, hãy đặt `MAILTO=` và dùng
`--quiet` trên cả hai dòng, vì cron gửi mail mỗi khi job in ra bất cứ thứ gì:

```cron
*/15 * * * * cd /srv/partflow/current && deploy/production/check.sh --url https://<host> --env-file /srv/partflow/env/production.env --quiet
0 4 * * * cd /srv/partflow/current && deploy/production/scheduled-reconcile.sh --env-file /srv/partflow/env/production.env --quiet
```

**Bản sao ngoài VPS (restic).** Đặt `RESTIC_REPOSITORY` là vị trí object storage và `RESTIC_PASSWORD_FILE` là một key
file mode 0400. Giữ quyền lưu giữ khóa đó ngoài VPS: mất khóa là mất các bản sao. Sau khi backup unit đã chạy:

1. `restic backup --tag database /srv/partflow/backups/database`
2. `restic forget --tag database --keep-daily 14 --keep-weekly 8 --prune` (retention khớp với retention của
   `backup-rotate`)
3. `restic check`, hằng tuần

Lỗi ở bất kỳ bước nào làm unit của nó thất bại (`OnFailure=`). Khi dùng thư mục archive, hãy sao chép nó vào một
repository hoặc tag riêng mà `forget` không bao giờ nhắm tới: archive không bao giờ bị rotate hay prune.

## 9. Chuyển từ Synology

Dùng cutover backup và restore ở [`SYNOLOGY_NAS.md`](./SYNOLOGY_NAS.md) §10: nó dùng cùng artifact backup và việc
restore vào instance mới của runbook §6. Giữ nguồn và đích cùng một release, áp dụng write freeze, xác minh checksum và
reconciliation, rồi mới chuyển DNS. Không bao giờ chạy hai production instance có thể ghi trên các database đã phân kỳ.

## Lịch sử thay đổi

| Ngày | Commit | Thay đổi |
| --- | --- | --- |
| 2026-11-09 | not yet committed | Viết lại thành nội dung hiện hành cho người đọc mới; owner đã bỏ pilot và các release gate. |
| 2026-10-09 | `03bd90c` | Thêm các unit giám sát, bộ thông báo alert và phương án cron. |
| 2026-10-08 | `a330383` | Thêm tự động hóa backup production, các unit lịch và bản sao restic ngoài VPS. |
| 2026-10-08 | `1116b07` | Thêm bước tạo database role vào quy trình triển khai ban đầu. |
| 2026-10-08 | `b820e7a` | Thêm release flow và quy tắc rollback gắn với schema đã migrate. |
| 2026-10-08 | `d7e86a9` | Thêm production Compose stack, ví dụ Caddy và quy trình chứng chỉ. |
| 2026-10-06 | `702ab1c` | Thêm đăng nhập và first-run setup vào quy trình triển khai ban đầu. |
| 2026-09-02 | `d2f68de` | Phiên bản đầu của hướng dẫn VPS cùng bản dịch tiếng Việt. |
