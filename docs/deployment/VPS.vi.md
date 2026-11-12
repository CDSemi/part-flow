# Triển khai PartFlow trên VPS Linux

> **Bản gốc:** [`VPS.md`](./VPS.md). Đồng bộ với bản tiếng Anh tại commit `8a7e2b5` cộng các thay đổi chưa
> commit ngày 2026-11-10. Bản tiếng Anh là nguồn chuẩn (source of truth); khi hai bản khác nhau, bản tiếng Anh có
> hiệu lực.

Hướng dẫn này cài PartFlow trên một máy chủ ảo riêng (virtual private server, VPS) chạy Linux, là máy chủ production
thay thế. Máy chủ đang dùng hiện nay là Synology NAS của công ty ([`SYNOLOGY_NAS.vi.md`](./SYNOLOGY_NAS.vi.md)); hãy
đọc hướng dẫn này khi NAS không còn đủ dùng hoặc công ty cần một máy chủ bên ngoài. Đọc trước
[`../DEPLOYMENT.vi.md`](../DEPLOYMENT.vi.md) §1 và §2. Việc cài đặt dùng cùng stack production và cùng các lệnh như
hướng dẫn NAS; hướng dẫn này đưa các lệnh đó với các thư mục của VPS, thêm Caddy cho HTTPS và systemd cho các tác vụ
theo lịch, và chỉ dẫn sang hướng dẫn NAS ở những chỗ giải thích giống hệt. Việc vận hành hằng ngày nằm trong
[`OPERATIONS_RUNBOOK.vi.md`](./OPERATIONS_RUNBOOK.vi.md). Các script và systemd unit trong hướng dẫn này chưa từng
được chạy trên một VPS: hãy sửa lại hướng dẫn khi một bước hoạt động khác đi.

## 1. Khi nào nên chọn VPS

Chọn VPS khi PartFlow cần Docker hoạt động ổn định, dễ dự đoán, có bộ xử lý và bộ nhớ riêng, truy cập từ xa an toàn,
có snapshot của nhà cung cấp bên cạnh các bản sao lưu của PartFlow, hoặc cần một máy chủ nằm ngoài công ty. Ứng dụng
không đổi: cùng các image, cùng tệp Compose, cùng script và cùng bản sao lưu.

## 2. Yêu cầu đối với máy chủ

- Một bản phân phối Linux 64-bit được hỗ trợ, có cập nhật bảo mật mới nhất, và bật cập nhật bảo mật tự động hoặc có
  một khung giờ vá lỗi cố định.
- Docker Engine và plugin Docker Compose từ một nguồn được hỗ trợ, cùng `git`, `curl`, `openssl` và Python 3.8 trở
  lên (`python3`).
- Một tài khoản triển khai riêng, đăng nhập bằng khóa SSH và được phép chạy Docker. Đây là tài khoản vận hành
  (operating account) ([`../DEPLOYMENT.vi.md`](../DEPLOYMENT.vi.md) §2.1): tài khoản này chạy mọi lệnh, sở hữu thư
  mục sao lưu và chạy các tác vụ theo lịch.
- Tường lửa của máy chủ cho phép SSH từ địa chỉ của các quản trị viên, cho phép HTTP và HTTPS theo quyết định đã
  chọn, và từ chối mọi cổng khác. Không bao giờ công khai PostgreSQL; hãy dùng mạng riêng của nhà cung cấp cho mọi
  dịch vụ từ xa.
- Đồng hồ chính xác (giao thức đồng bộ thời gian, network time protocol, NTP) và đủ dung lượng đĩa cho database, các
  image và các bản sao lưu cục bộ.
- Một nơi lưu bản sao lưu được mã hóa nằm ngoài VPS (§8), và không chạy các khối lượng công việc không liên quan trên
  máy chủ production.

## 3. Cấu trúc thư mục và cách viết tắt `PF`

```text
/srv/partflow/
  releases/<tag>/        one release checkout per release
  current                link to the running release's checkout; the schedules run from it
  env/production.env     the configuration, outside every checkout (mode 600)
  secrets/               the three password files (PARTFLOW_SECRETS_DIR, mode 0700)
  backups/database/      one sub-folder per backup (PARTFLOW_BACKUP_DIR, mode 0700)
```

Tài khoản triển khai sở hữu mọi thứ dưới `/srv/partflow`. Vì tệp cấu hình nằm ngoài các thư mục mã nguồn, mọi bản
phát hành, mọi lệnh `$PF` và mọi tác vụ theo lịch đều đọc cùng một tệp. Vì vậy mọi script đều cần
`--env-file /srv/partflow/env/production.env`, và `PF` là:

```sh
PF="docker compose -f compose.production.yaml --env-file /srv/partflow/env/production.env"
```

## 4. Cài đặt PartFlow

Chạy các bước này bằng tài khoản triển khai. `<TAG>` là tag phát hành, `<TIMEZONE>` là múi giờ của nhà máy (ví dụ
`America/Los_Angeles`). Mỗi bước tương ứng với phần của hướng dẫn NAS được nêu trong ngoặc; phần đó giải thích những
gì bạn sẽ thấy.

1. Tạo các thư mục:

   ```sh
   sudo mkdir -p /srv/partflow/releases /srv/partflow/env /srv/partflow/secrets /srv/partflow/backups/database
   sudo chown -R "$(id -un):$(id -gn)" /srv/partflow
   chmod 700 /srv/partflow/secrets /srv/partflow/backups/database
   ```

2. Tải bản phát hành về và đánh dấu nó là bản đang chạy ([`SYNOLOGY_NAS.vi.md`](./SYNOLOGY_NAS.vi.md) §5 bước 1):

   ```sh
   cd /srv/partflow/releases
   git clone https://github.com/CDSemi/part-flow.git <TAG>
   cd <TAG>
   git checkout <TAG>
   ln -sfn /srv/partflow/releases/<TAG> /srv/partflow/current
   ```

3. Tạo tệp cấu hình (hướng dẫn NAS §5 bước 2 và 3). Lệnh cuối kiểm tra subnet của mạng `edge` nội bộ có chưa được dùng
   hay không: lệnh này không được in ra gì; nếu có in ra, hãy chọn một subnet khác cho `PARTFLOW_EDGE_SUBNET` như
   hướng dẫn NAS §5 bước 3 chỉ.

   ```sh
   cp .env.production.example /srv/partflow/env/production.env
   chmod 600 /srv/partflow/env/production.env
   E=/srv/partflow/env/production.env
   sed -i 's|^PARTFLOW_RELEASE=.*|PARTFLOW_RELEASE=<TAG>|' "$E"
   sed -i 's|^PARTFLOW_SECRETS_DIR=.*|PARTFLOW_SECRETS_DIR=/srv/partflow/secrets|' "$E"
   sed -i 's|^PARTFLOW_BACKUP_DIR=.*|PARTFLOW_BACKUP_DIR=/srv/partflow/backups/database|' "$E"
   sed -i 's|^PARTFLOW_SITE_TIMEZONE=.*|PARTFLOW_SITE_TIMEZONE=<TIMEZONE>|' "$E"
   ip -4 route | grep '172\.30\.250\.'
   ```

4. Tạo ba tệp bí mật (secret files), rồi kiểm tra cấu hình (hướng dẫn NAS §6):

   ```sh
   for name in postgres_password partflow_app_password partflow_maintenance_password; do
       openssl rand -base64 32 > "/srv/partflow/secrets/$name"
       chmod 0444 "/srv/partflow/secrets/$name"
   done
   PF="docker compose -f compose.production.yaml --env-file /srv/partflow/env/production.env"
   $PF config --quiet
   ```

5. Build các image và tạo database (hướng dẫn NAS §7):

   ```sh
   PARTFLOW_COMMIT=$(git rev-parse HEAD) $PF -f compose.production.build.yaml build
   $PF up -d db
   $PF --profile ops run --rm -T db-roles
   $PF --profile ops run --rm -T migrate --no-backup-reason "first install: empty database"
   ```

   Production bắt đầu từ một database mới, trống. Nếu muốn bắt đầu từ một bản sao lưu, hãy làm theo runbook §6
   (New-instance restore, khôi phục sang bản cài mới) thay cho hai lệnh cuối.

6. Khởi động PartFlow với một tiến trình worker của backend và kiểm tra ngay trên máy chủ (hướng dẫn NAS §8):

   ```sh
   PARTFLOW_BACKEND_WORKERS=1 $PF up -d backend web
   curl -fsS http://127.0.0.1:18080/api/health
   ```

7. Thiết lập HTTPS bằng Caddy (§5).
8. Tạo Administrator đầu tiên, trả về số tiến trình worker đã cấu hình, kiểm tra việc bảo vệ database và đăng ký các
   Scan Station, đúng như hướng dẫn NAS §10 mô tả (các lệnh giống nhau, chỉ dùng `PF` này).
9. Mở tường lửa cho các nguồn đã được phê duyệt và bắt đầu dùng PartFlow. Sau đó thiết lập việc sao lưu và giám sát ở
   §8, thực hiện ngay một bản sao lưu đầu tiên và chạy một lần diễn tập khôi phục (restore drill) trên bản đó (runbook
   §4); các việc này được khuyến nghị và không bao giờ chặn việc sử dụng:

   ```sh
   deploy/production/backup.sh --kind manual --operator "<your name>" --reason "initial backup" --env-file /srv/partflow/env/production.env
   deploy/production/restore-test.sh --backup <backup name> --operator "<your name>" --env-file /srv/partflow/env/production.env
   ```

## 5. HTTPS với Caddy

Dùng một tên miền truy cập (host name) riêng, ví dụ `partflow.company.example`, và chỉ trỏ bản ghi DNS của nó về VPS
sau khi bước kiểm tra ở §4 bước 6 đã đạt. Caddy kết thúc HTTPS và chuyển tiếp mọi thứ tới `web` trên địa chỉ loopback
([`../DEPLOYMENT.vi.md`](../DEPLOYMENT.vi.md) §3.1, Platform proxy requirements). Ví dụ `/etc/caddy/Caddyfile`:

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

- `18080` là `PARTFLOW_HTTP_PORT`, và đích đến là đúng chuỗi `127.0.0.1`, không bao giờ là `localhost`.
- Caddy gửi `X-Forwarded-For` kèm địa chỉ của client và `X-Forwarded-Proto`, và tự chuyển hướng HTTP sang HTTPS.
- Giới hạn body 5 MiB và timeout 300 s cao hơn các giới hạn của `web` (4 MiB và 180 s), để `web` hoặc ứng dụng trả lời
  bằng thông báo của chính nó thay vì để Caddy cắt ngang yêu cầu.
- Kiểm tra rằng `web` thấy địa chỉ thật của người dùng: sau vài yêu cầu đi qua Caddy, `$PF logs --tail=20 web` phải
  hiện địa chỉ máy tính của chính bạn làm client, không phải một địa chỉ `172.x.x.x`. Nếu không, kết nối loopback
  không đến từ gateway của mạng `edge`: hãy đặt `PARTFLOW_TRUSTED_PROXY` thành địa chỉ mà log hiển thị và chạy
  `$PF up -d web` (`../DEPLOYMENT.vi.md` §3.1, Platform proxy requirements).

**Chứng chỉ.** Một tên công khai tự động nhận chứng chỉ từ một tổ chức cấp chứng chỉ (certificate authority) công khai
qua ACME, không cần chỉ thị thêm, và Caddy tự gia hạn; cổng 80 và 443 phải truy cập được để việc này hoạt động. Một
tên chỉ dùng nội bộ dùng một trong hai cách: `tls /etc/caddy/certs/partflow.crt /etc/caddy/certs/partflow.key` (chứng
chỉ của tổ chức cấp chứng chỉ của công ty; quyền 0600, thuộc sở hữu của người dùng Caddy; hãy thay chứng chỉ và chạy
`caddy reload` trước khi nó hết hạn) hoặc `tls internal` (tổ chức cấp chứng chỉ riêng của Caddy, khi đó chứng chỉ gốc
của nó phải được cài trên mọi máy trạm). Nếu PartFlow chỉ dùng nội bộ, hãy hạn chế truy cập bằng tường lửa, VPN hoặc
DNS riêng; một địa chỉ bí mật không phải là biện pháp bảo vệ.

Sau khi Caddy chạy, hãy kiểm tra rằng `https://partflow.company.example/api/health` trả lời `"status":"ok"` và một
địa chỉ PartFlow như `https://partflow.company.example/scan-station` mở được trực tiếp.

## 6. Cài bản phát hành mới và quay lui

Dùng một thư mục mã nguồn mới cho mỗi bản phát hành, và không bao giờ cài từ một branch:

```sh
cd /srv/partflow/releases
git clone https://github.com/CDSemi/part-flow.git <NEW_TAG>
cd <NEW_TAG>
git checkout <NEW_TAG>
deploy/production/release.sh --release <NEW_TAG> --operator "<your name>" --approver "<your name>" --env-file /srv/partflow/env/production.env
ln -sfn /srv/partflow/releases/<NEW_TAG> /srv/partflow/current
```

Chỉ chạy lệnh cuối sau khi `release.sh` đã thoát với mã 0, để các tác vụ theo lịch chạy script của bản phát hành mới.
Với bất kỳ mã thoát nào khác, hãy làm theo runbook §5. Việc quay lui (rollback) làm theo runbook §6: "thư mục mã nguồn
của bản phát hành trước" là `/srv/partflow/releases/<previous tag>`, nên hãy chạy các lệnh của nó tại đó thay cho
`git checkout <previous tag>`, và sau khi quay lui, hãy trỏ liên kết `current` lại vào đó. Hãy giữ thư mục mã nguồn
và các image của bản phát hành trước cho đến khi bạn chắc chắn sẽ không quay lui. Hoàn tác các migration bằng
`alembic downgrade` không bao giờ là một lần quay lui.

Runbook viết các lệnh cho NAS, nơi tệp cấu hình là `.env.production` trong thư mục mã nguồn. Trên VPS, hãy dùng
`/srv/partflow/env/production.env` thay cho nó: trong `PF` (§3), trong mọi lệnh `sed` sửa tệp cấu hình, dưới dạng
`--env-file /srv/partflow/env/production.env` cho mọi script (`release.sh`, `backup.sh`, `restore-test.sh`,
`smoke.sh`, `check.sh`, `scheduled-reconcile.sh`), và trong các dòng `ENV=` và `PF=` của các script quay lui đường 3
sau khi bạn đã lưu chúng thành tệp.

## 7. Chuyển từ Synology NAS sang

Làm theo runbook §6 (Moving to another host): cài cùng bản phát hành đó trên VPS như ở §4 cho đến bước build image,
tạm dừng ghi (write freeze) trên NAS, thực hiện bản sao lưu cuối cùng, sao chép và xác minh nó trên VPS, khôi phục nó,
thiết lập HTTPS, và chỉ sau đó mới chuyển bản ghi DNS. Không bao giờ để cả hai máy chủ cùng nhận ghi.

## 8. Sao lưu, giám sát và cảnh báo

Định dạng bản sao lưu, việc xác minh, chính sách lưu giữ và diễn tập khôi phục nằm trong runbook §3 và §4. Trên VPS,
có thể thêm snapshot của nhà cung cấp làm lớp thứ hai, nhưng chúng không bao giờ thay thế một lần khôi phục logic đã
được kiểm thử. Sao lưu và giám sát được khuyến nghị sau khi đưa vào sử dụng (go-live) và không bao giờ chặn việc sử
dụng.

**Unit cảnh báo.** `/etc/systemd/system/partflow-alert@.service` gửi thư cho các quản trị viên khi một unit khác bị
lỗi. Unit này cần một mail transfer agent trên máy chủ (ví dụ msmtp) và quyền đọc journal cho người dùng của nó:

```ini
[Service]
Type=oneshot
ExecStart=/bin/sh -c 'journalctl -u %i -n 60 --no-pager | mail -s "PartFlow alert: %i failed on %H" <ops address>'
```

`%i` và `%H` là các specifier của systemd; systemd không bao giờ chạy `$(...)`.

**Service và timer.** Mỗi service có `Type=oneshot`, `User=<deployment account>`,
`WorkingDirectory=/srv/partflow/current` và `OnFailure=partflow-alert@%n.service`, và `ExecStart=` của nó dùng đường
dẫn tuyệt đối, vì systemd từ chối đường dẫn tương đối:

| Service | `ExecStart=` | Timer |
| --- | --- | --- |
| `partflow-backup.service` | `/srv/partflow/current/deploy/production/backup.sh --kind daily --keep-daily 14 --keep-weekly 8 --operator scheduler --env-file /srv/partflow/env/production.env` | `partflow-backup.timer`: `OnCalendar=*-*-* 02:00:00`, `Persistent=true` |
| `partflow-check.service` | `/srv/partflow/current/deploy/production/check.sh --url https://<host> --env-file /srv/partflow/env/production.env` | `partflow-check.timer`: `OnCalendar=*:0/15` |
| `partflow-reconcile.service` | `/srv/partflow/current/deploy/production/scheduled-reconcile.sh --env-file /srv/partflow/env/production.env` | `partflow-reconcile.timer`: `OnCalendar=*-*-* 04:00:00`, `Persistent=true` |

14 và 8 là các giá trị lưu giữ làm ví dụ; cả hai tùy chọn đều bắt buộc. Hãy kiểm tra từng unit bằng
`systemd-analyze verify` trước khi bật các timer bằng `systemctl enable --now <timer>`. Mọi mã thoát khác 0, kể cả một
bản sao lưu hằng ngày không qua được bước xác minh, đều làm unit của nó thất bại và gửi cảnh báo. Hãy đặt giờ sao lưu
cách xa giờ phát hành, vì một lần phát hành giữ khóa sao lưu.

Nếu dùng cron thay cho systemd, hãy đặt `MAILTO=` và dùng `--quiet`, vì cron gửi thư mỗi khi một tác vụ in ra gì đó:

```cron
*/15 * * * * cd /srv/partflow/current && deploy/production/check.sh --url https://<host> --env-file /srv/partflow/env/production.env --quiet
0 4 * * * cd /srv/partflow/current && deploy/production/scheduled-reconcile.sh --env-file /srv/partflow/env/production.env --quiet
```

**Bản sao mã hóa nằm ngoài VPS (restic).** Đặt `RESTIC_REPOSITORY` thành vị trí lưu trữ đối tượng (object storage) và
`RESTIC_PASSWORD_FILE` thành một tệp khóa có quyền 0400, và giữ một bản sao của khóa đó bên ngoài VPS: không có nó thì
không đọc được các bản sao. Sau khi unit sao lưu đã chạy:

1. `restic backup --tag database /srv/partflow/backups/database`
2. `restic forget --tag database --keep-daily 14 --keep-weekly 8 --prune`, với cùng chính sách lưu giữ như các bản sao
   lưu
3. `restic check`, hằng tuần

Thất bại của bất kỳ lệnh nào trong số này làm unit của nó thất bại và gửi cảnh báo. Khi sau này dùng một thư mục lưu
trữ (archive folder), hãy sao chép nó sang một repository restic riêng, hoặc dưới một tag restic mà `forget` không bao giờ nhắm tới: các
bản lưu trữ không bao giờ bị xoay vòng hay xóa.

## Lịch sử thay đổi

| Ngày | Commit | Thay đổi |
| --- | --- | --- |
| 2026-11-10 | uncommitted | Viết lại với đầy đủ lệnh cài đặt cho các thư mục của VPS, một thư mục mã nguồn cho mỗi bản phát hành cùng liên kết `current` cho các tác vụ theo lịch, các systemd unit gom trong một bảng, và việc chuyển từ NAS trỏ sang runbook. |
| 2026-11-09 | `b405c6e` | Viết lại thành nội dung hiện hành cho người đọc mới; bỏ pilot và các cổng phát hành theo quyết định của chủ sở hữu. |
| 2026-10-09 | `03bd90c` | Thêm các unit giám sát, trình gửi cảnh báo và phương án thay thế bằng cron. |
| 2026-10-08 | `a330383` | Thêm tự động hóa sao lưu production, các unit theo lịch và bản sao restic nằm ngoài VPS. |
| 2026-10-08 | `1116b07` | Thêm bước tạo database role vào lần triển khai đầu tiên. |
| 2026-10-08 | `b820e7a` | Thêm quy trình phát hành và quy tắc quay lui gắn với schema đã migration. |
| 2026-10-08 | `d7e86a9` | Thêm stack Compose production, ví dụ Caddy và quy trình chứng chỉ. |
| 2026-10-06 | `702ab1c` | Thêm đăng nhập và thiết lập lần đầu (first-run setup) vào lần triển khai đầu tiên. |
| 2026-09-02 | `d2f68de` | Phiên bản đầu tiên của hướng dẫn VPS cùng bản dịch tiếng Việt. |
