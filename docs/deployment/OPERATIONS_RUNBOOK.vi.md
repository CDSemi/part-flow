# Runbook vận hành PartFlow

> **Bản gốc:** [`OPERATIONS_RUNBOOK.md`](./OPERATIONS_RUNBOOK.md). Đồng bộ với bản tiếng Anh tại commit `e0e5056` cộng các thay đổi chưa
> commit ngày 2026-11-12. Bản tiếng Anh là nguồn chuẩn (source of truth); khi hai bản khác nhau, bản tiếng Anh có
> hiệu lực.

Runbook này gồm các thủ tục vận hành PartFlow production sau khi đã cài đặt: kiểm tra tình trạng, sao lưu và thử
khôi phục, cài bản phát hành (release) mới, quay lui (rollback), chạy đối soát (reconciliation), xử lý sự cố và lịch
làm việc định kỳ. Tài liệu viết cho người vận hành (operator), hiện cũng là chủ sở hữu (owner). Việc cài đặt nằm
trong [`SYNOLOGY_NAS.vi.md`](./SYNOLOGY_NAS.vi.md) (hoặc [`VPS.vi.md`](./VPS.vi.md)); mọi thiết lập và giới hạn của
hệ thống được mô tả trong [`../DEPLOYMENT.vi.md`](../DEPLOYMENT.vi.md) §3.1.

## Tìm tác vụ của bạn

| Tôi muốn … | Mục |
| --- | --- |
| Kiểm tra PartFlow có khỏe không, hoặc tìm nguyên nhân của một lỗi | §2 |
| Sao lưu thủ công, kiểm tra một bản sao lưu, hoặc hiểu thư mục sao lưu | §3 |
| Chứng minh rằng một bản sao lưu khôi phục được | §4 |
| Cài một bản phát hành mới | §5 |
| Hoàn tác một bản phát hành | §6 |
| Khôi phục sau khi máy chủ hỏng, hoặc chuyển PartFlow sang máy chủ khác | §6 (Khôi phục sang bản cài mới, Chuyển sang máy chủ khác) |
| Chạy hoặc đọc kết quả đối soát | §7 |
| Giải quyết vấn đề: các triệu chứng và sự cố thường gặp | §8 |
| Biết việc cần làm hằng ngày, hằng tuần, hằng tháng; đổi mật khẩu; xóa bản sao lưu cũ | §9 |
| Lấy lại mật khẩu hoặc login name đã quên, đổi tên đăng nhập của một user, hoặc thu hồi quyền truy cập của một user | §8 (Đăng nhập và tài khoản người dùng) |
| Tìm xem một bản phát hành trước đây đã làm gì | §1 |

## Trước khi chạy bất kỳ lệnh nào

1. Mở terminal trên máy chủ (host) bằng tài khoản vận hành (operating account). Trên Synology NAS: dùng `ssh` với tài
   khoản quản trị DSM, rồi chạy `sudo -i` ([`SYNOLOGY_NAS.vi.md`](./SYNOLOGY_NAS.vi.md) §3).
2. Chuyển đến thư mục gốc của thư mục mã nguồn của bản phát hành (release checkout) và đặt biến viết tắt `PF`. Phiên
   terminal mới sẽ quên `PF`, nên hãy đặt lại mỗi lần:

   ```sh
   cd /volume1/docker/partflow/repo
   PF="docker compose -f compose.production.yaml --env-file .env.production"
   ```

   Đây là các đường dẫn của bản cài trên NAS. Trên VPS, thư mục mã nguồn và tệp cấu hình nằm ở chỗ khác
   ([`VPS.vi.md`](./VPS.vi.md) §3 và §6): ở bất cứ chỗ nào runbook này nhắc đến `.env.production`, hãy dùng tệp của
   VPS thay thế.
3. Dùng thư mục mã nguồn của bản phát hành **đang chạy** cho công việc hằng ngày. Khi phát hành, dùng thư mục mã nguồn
   của bản phát hành **mới**. Trong bản cài trên NAS, cả hai là cùng một thư mục `repo/`; lệnh `git checkout <tag>`
   chuyển giữa chúng.

Quy ước: tác vụ chạy một lần (one-shot job) đọc hoặc ghi thư mục sao lưu chạy với `--user "$(id -u):$(id -g)"`, vì
thư mục đó có mode 0700. `<partflow-host>` là tên miền truy cập (host name) mà mọi người gõ trên trình duyệt,
`<backup-dir>` là giá trị của `PARTFLOW_BACKUP_DIR`, `<secrets-dir>` là giá trị của `PARTFLOW_SECRETS_DIR`,
`<records-dir>` là thư mục hồ sơ triển khai (records directory) `~/partflow-deployments`, và `<NAME>` là tên của một
bản sao lưu. Thời gian trong báo cáo và hồ sơ là giờ phối hợp quốc tế (UTC); báo cáo và hồ sơ là các tài liệu JSON.

## 1. Hồ sơ triển khai

Mỗi lần phát hành, diễn tập khôi phục (restore drill), quay lui và khôi phục đều để lại một thư mục trong
`<records-dir>` (mode 0700). Hãy giữ các thư mục này: chúng cho biết bản sao lưu nào thuộc bản phát hành nào và đã
kiểm tra những gì.

| Thư mục | Do ai ghi | Các tệp chính |
| --- | --- | --- |
| `<UTC>-<tag>/` | `release.sh` | `record.json`; `preflight.log`; `pre-reconcile.json` và `post-reconcile.json`, đối soát trước và sau thay đổi; `migrate.json` và `migrate.log`; `env-before.txt`, tệp `.env.production` từ trước khi chuyển; `regression.txt`, các phát hiện (finding) mới xuất hiện sau thay đổi; `smoke.txt`; và một log cho mỗi bước |
| `<UTC>-restore-test-<NAME>/` | `restore-test.sh` | `evidence.json`, kết quả của lần diễn tập (§4) |
| thư mục của bản phát hành bị hoàn tác | bạn, khi quay lui theo đường 3 (path 3) (§6) | `rollback.json`, `rollback-reconcile.json` |
| `<UTC>-restore-<NAME>/` | bạn, khi khôi phục sang bản cài mới (§6) | `restore.json` |

`record.json` chứa bản phát hành (tag, commit, tag trước đó, image ID), các revision của database trước và sau, người
vận hành và người phê duyệt (approver), thời điểm, bản sao lưu trước phát hành và kết quả kiểm tra nó, kết quả
migration, smoke và đối soát, cùng cách lần chạy kết thúc: `outcome`, `freeze_completed_at` (lúc xác nhận `backend`
đã dừng, hoặc `null` nếu không tạm dừng ghi), `writes_reopened_at` và `refrozen`.

Với một thao tác thủ công, ví dụ quay lui theo đường 1, hãy lưu kết quả đầu ra vào thư mục của bản phát hành liên
quan, kèm ghi chú ngắn về việc đã làm, ai làm, khi nào và vì sao.

## 2. Kiểm tra tình trạng và chẩn đoán

### Kiểm tra nhanh (Quick checks)

```sh
$PF ps
$PF logs --since=15m backend web db
curl -fsS https://<partflow-host>/api/health
curl -fsS https://<partflow-host>/api/health/live
$PF run --rm --no-deps -T backend python -m app.cli revision
```

- `$PF ps` liệt kê `db`, `backend` và `web` ở trạng thái đang chạy và `(healthy)`.
- `/api/health` là **trạng thái sẵn sàng (readiness)**: nó báo `release`, `commit` và `schema` (`current`, `accepted`,
  `mismatch` hoặc `unknown`) cùng các revision. `"status":"ok"` với `"schema":"current"` nghĩa là mọi thứ bình thường.
  Mã 503 với `"schema":"mismatch"` nghĩa là revision của database không khớp bản phát hành đang chạy (một lần phát
  hành bị gián đoạn, hoặc sai tag trong `.env.production`: §5 và §6). Mã 503 với `"schema":"unknown"` nghĩa là không
  kết nối được database (`$PF ps db`, `$PF logs db`).
- `/api/health/live` chỉ là **trạng thái còn hoạt động (liveness)**. Các kiểm tra tình trạng (health check) của
  container dùng nó, nên container vẫn healthy trong khi readiness trả 503.
- `revision` in ra cùng các thông tin đó từ một container chạy một lần, và cũng chạy được khi `backend` đang dừng.
- Phản hồi JSON 502 hoặc 504 đến từ `web`: `backend` đang dừng hoặc không trả lời (`$PF ps backend`,
  `$PF logs --tail=100 backend`).

### Kiểm tra toàn bộ máy chủ (The full host check)

`deploy/production/check.sh` chạy mọi kiểm tra của tác vụ theo lịch. Khi chạy thủ công, **luôn thêm `--no-state`**:
nếu không, lần chạy thủ công sẽ làm dịch con trỏ lỗi và mốc khởi động lại, và lần chạy theo lịch kế tiếp sẽ bỏ sót
những gì lần thủ công đã thấy. Thêm các tùy chọn `--cacert` hoặc `--resolve-to` giống như tác vụ theo lịch.

```sh
deploy/production/check.sh --url https://<partflow-host> --no-state
```

Nó in một dòng cho mỗi kiểm tra, `PASS|FAIL|SKIP <id> <reason>`, theo thứ tự này, rồi các dòng `NOTE` tùy chọn:

| Kiểm tra | Thất bại khi |
| --- | --- |
| `https` | `/api/health` không trả 200 với schema `current` và bản phát hành được ghi trong `.env.production` |
| `certificate` | chứng chỉ hết hạn trong vòng 21 ngày (`--min-cert-days`) |
| `containers` | không có đúng một `db`, `backend` và `web` đang chạy và healthy |
| `restarts` | số lần khởi động lại tăng so với lần chạy đầy đủ trước |
| `errors` | `backend` ghi một bản ghi `ERROR` hoặc `CRITICAL` kể từ lần chạy đầy đủ trước (hiển thị tối đa ba request id) |
| `disk_data` | volume của database còn trống dưới 15 % (`--min-free-percent`) |
| `disk_backup` | thư mục sao lưu còn trống dưới 15 % |
| `disk_docker` | bộ nhớ của Docker (image và log của container) còn trống dưới 15 % |
| `disk_archive` | thư mục lưu trữ (archive) còn trống dưới 15 %; `SKIP` khi chưa chỉ định thư mục lưu trữ (`--archive-dir`) |
| `database` | báo cáo `status` không chạy được, không trả lời trong 120 s, hoặc báo lỗi database |
| `schema` | `status` báo schema chưa sẵn sàng |
| `backup_age` | không tìm thấy bản sao lưu đã công bố, không đọc được thư mục sao lưu, hoặc bản sao lưu mới nhất thuộc bất kỳ loại nào hoàn tất cách đây hơn 26 giờ (`--max-backup-age-hours`) |
| `archival_proposal` | luôn `SKIP`: việc lưu trữ lịch sử Movement chưa được triển khai |

- Bốn dòng cuối đến từ một lần chạy `status` chỉ đọc. Khi một bản phát hành đang giữ khóa của nó, hoặc `release.sh`
  đang giữ khóa sao lưu, chúng là `SKIP`. Khi khóa như vậy cũ hơn 4 giờ, lần chạy báo `FAIL database` và nêu tên khóa,
  vì một `release.sh` bị kill sẽ để lại khóa (§3, Khóa sao lưu và khóa phát hành).
- Mã thoát (exit code): 0 không có gì để báo, 1 có ít nhất một `FAIL`, 2 không chạy được kiểm tra; cả 1 lẫn 2 đều
  kích hoạt thông báo lỗi của trình lập lịch. Một tập thất bại không đổi chỉ được báo lại sau 6 giờ
  (`--renotify-hours`); khi đó đầu ra ghi `already reported`.
- `check.sh` giữ trạng thái của nó trong `~/partflow-monitoring` (mode 0700), gồm `last-check.txt` (các dòng của lần
  chạy gần nhất) và `growth.tsv` (kích thước database mỗi ngày một lần).
- Kiểm tra `errors` chỉ đọc log của container `backend` hiện tại, và Docker xóa log của container cùng với container.
  Sau khi `backend` được tạo lại (do phát hành hoặc đổi mật khẩu), lần chạy thêm một dòng `NOTE errors`. **Trước khi
  bạn tạo lại `backend` bằng tay**, hãy chạy `deploy/production/check.sh --url https://<partflow-host> --only errors --no-state`
  và giữ lại những gì nó báo.

### Báo cáo trạng thái (The status report)

```sh
$PF --profile ops run --rm --no-deps -T --user "$(id -u):$(id -g)" status
docker stats --no-stream
```

`status` in một tài liệu JSON về kích thước database, số kết nối, thời gian chờ khóa, số dòng và kích thước Movement,
mức sẵn sàng của schema và tuổi của bản sao lưu. `result` của nó là `ok`, `attention` hoặc `error`, với mã thoát 0, 1
(một phát hiện như `backup_stale` hoặc `schema_not_ready`) hoặc 2 (`configuration_invalid`, `database_unavailable`,
`lock_timeout`, `statement_timeout`, `database_error`, `internal_error`). Thời gian chờ khóa (`database.locks`) chỉ
được báo cáo, không bao giờ được đánh giá. `status` chạy một giao dịch chỉ đọc với vai trò `partflow_app` và giới
hạn chờ khóa 5 s; giữ `--statement-timeout` của nó (mặc định 20 s) dưới 30 s, để nó không bao giờ làm `migrate` của
một bản phát hành phải chờ. `docker stats` hiển thị mức dùng bộ xử lý và bộ nhớ của từng container.

### Log và request id (Logs and request ids)

Backend ghi mỗi dòng một đối tượng JSON. Mọi phản hồi, kể cả từ chối và lỗi, đều mang một request id HTTP trong header
`X-Request-ID`, và mọi bản ghi backend của request đó mang nó dưới dạng `request_id`. Để tìm một request:

```bash
$PF logs --no-log-prefix backend | grep '"request_id":"<id>"'
```

Request id thay đổi mỗi khi request được gửi lại, nên một lệnh sản xuất được theo dõi và thử lại bằng
`device_event_id` của nó (hiển thị trên màn hình là "request identity"), không bao giờ bằng request id. Để theo dõi
một lệnh, một Part Number (PN), một QuantityFlow, một Area, một Work Order Demand hoặc một Scan Station:

```bash
$PF logs --no-log-prefix backend | grep '"device_event_id":"<id>"'
$PF logs --no-log-prefix backend | grep '"part_number":"<PN>"'
$PF logs --no-log-prefix backend | grep -E '"quantity_flow_id":<n>[,}]'
$PF logs --no-log-prefix backend | grep -E '"(area|source_area|target_area|starting_area)_id":<n>[,}]'
$PF logs --no-log-prefix backend | grep -E '"work_order_demand_id":<n>[,}]|"work_order_demand_ids":\[([0-9]+,)*<n>[],]'
$PF logs --no-log-prefix backend | grep '"station_id":"<id>"'
```

Một con số được khớp cùng với `,` hoặc `}` theo sau nó, để `1` không khớp luôn `10`. Một Area cũng được tìm thấy khi nó
là Area nguồn, đích hoặc xuất phát của một lệnh, và một Work Order Demand cũng được tìm thấy bên trong danh sách của
một lần phân bổ. Mỗi bản ghi chứa gì, và điều gì không bao giờ được ghi log, nằm trong
[`../DEPLOYMENT.vi.md`](../DEPLOYMENT.vi.md) §3.1 (Logs).

Phản hồi JSON 502 hoặc 504 từ `web` (`server_unavailable`) nghĩa là kết quả của một thay đổi chưa rõ. Tìm nó theo
`request_id` trong log của `web`, và xác định số phận của thay đổi bằng `device_event_id` gốc của nó. **Không bao giờ
thử lại một lệnh sản xuất đã hết thời gian chờ bằng một `device_event_id` mới** trước khi biết kết quả: chỉ thử lại
bằng `device_event_id` gốc, để một phản hồi không chắc chắn không bao giờ ghi nhận một thay đổi hai lần.

**Log chỉ được giữ trong vài ngày.** Mỗi container giữ tối đa 5 tệp log, mỗi tệp 10 MB. Với 5.000 lệnh mỗi ngày, đó
là khoảng 9 ngày log backend và 18 ngày log `web`; mỗi Production Board đang mở thêm khoảng 3 MB log `web` mỗi ngày,
nên khi mở nhiều board, log `web` chỉ bao phủ vài ngày. **Hãy sao chép những gì một cuộc điều tra cần ngay khi bắt
đầu.**

### Lỗi quyền database (Database permission errors)

Backend kết nối bằng database role `partflow_app`. Lỗi `permission denied` (SQLSTATE 42501) trong log của nó nghĩa
là một đường mã đã cố thay đổi lịch sử được bảo vệ, hoặc thiếu một quyền (grant). Đó là một sự cố (§8), không bao giờ
là lý do để cấp thêm quyền. Chạy `reconcile --check h` (§7) trước mọi việc khác.

## 3. Sao lưu

### Bản sao lưu là gì (What a backup is)

`deploy/production/backup.sh` tạo bản sao lưu logic của database PartFlow trong khi `db` đang chạy. Nó chạy từ thư mục
gốc của một thư mục mã nguồn bản phát hành: của bản phát hành đang chạy với sao lưu theo lịch và thủ công, của bản
phát hành mới khi `release.sh` gọi nó. Một bản sao lưu là một thư mục `<backup-dir>/<NAME>/`:

```text
NAME = <UTC stamp>-<kind>[-<label>]      for example 20261008T020000Z-daily
                                         and 20261008T140000Z-pre-release-v1.0.0-rc.2
```

- `kind` là `daily` (sao lưu theo lịch), `manual` (làm bằng tay, kèm lý do) hoặc `pre-release` (do `release.sh` tạo,
  với tag phát hành đích làm `label`).
- Thư mục chứa đúng bốn tệp: `partflow.dump` (database, được dump bên trong `db` bằng
  `pg_dump --format=custom --no-owner --no-privileges`), `partflow.dump.list` (mục lục của nó), `manifest.json` (khi
  nào, ở đâu và từ bản phát hành nào và revision database nào bản sao lưu được tạo, phiên bản PostgreSQL, các
  checksum, người vận hành và lý do) và `SHA256SUMS`.
- Nó được lắp ráp trong `<backup-dir>/.partial/` và công bố bằng một lần đổi tên, nên một bản sao lưu hoặc tồn tại
  trọn vẹn hoặc không tồn tại. Các mục khác của thư mục, như `@eaDir` của DSM, bị bỏ qua.
- Một bản sao lưu chứa mọi bảng, kể cả hash mật khẩu và các digest của phiên (session) và token thiết bị. **Hãy coi
  thư mục sao lưu và mọi bản sao của nó là bí mật.** Nó không chứa quyền (grants): sau khi khôi phục, chúng được dẫn
  xuất lại từ mã nguồn (§4).
- Thư mục sao lưu chỉ chứa các bản sao lưu production, không bao giờ chứa của bản cài khác hay của một lần diễn tập;
  quy tắc của nó nằm trong [`../DEPLOYMENT.vi.md`](../DEPLOYMENT.vi.md) §3.1 (Configuration file,
  `PARTFLOW_BACKUP_DIR`).
- Một database ở trạng thái không mong đợi (revision không xác định hoặc bảng không xác định) vẫn được sao lưu, kèm
  cảnh báo và `tables_checked: false` trong manifest, vì một bản sao lưu trung thực có giá trị hơn không có gì.
  `migrate` từ chối bản sao lưu như vậy, và lần diễn tập khôi phục thất bại ở bước revision.

### Sao lưu thủ công (Taking a backup by hand)

```sh
deploy/production/backup.sh --kind manual --operator "<your name>" --reason "<why>"
```

Khi thành công, đầu ra chuẩn (standard output) đúng một dòng, `BACKUP <NAME> <absolute path>`; các dòng tiến trình như
`backup: dump ok (1234 ms)` đi ra lỗi chuẩn (standard error). Bản sao lưu hằng ngày theo lịch là
`deploy/production/backup.sh --kind daily --operator scheduler --keep-daily 14 --keep-weekly 8`
([`SYNOLOGY_NAS.vi.md`](./SYNOLOGY_NAS.vi.md) §11.2). `release.sh` tự tạo bản sao lưu trước phát hành; dạng thủ công
của nó, cho một lần phát hành bằng tay, là
`deploy/production/backup.sh --kind pre-release --label <target tag> --tools-release <target tag> --operator "<your name>"`.

| Tùy chọn | Ý nghĩa |
| --- | --- |
| `--kind daily\|manual\|pre-release`, `--operator NAME` | Bắt buộc. |
| `--reason TEXT` | Bắt buộc với `manual`. |
| `--label TAG` | Tag phát hành đích: bắt buộc với `pre-release`, bị từ chối với loại khác. |
| `--tools-release TAG` | Bản phát hành có image `backup-tools` ghi và kiểm tra manifest (mặc định: `PARTFLOW_RELEASE`). |
| `--keep-daily N --keep-weekly N`, hoặc `--no-rotate` | Chỉ với `daily`, và bắt buộc một trong hai: chính sách giữ lại (bên dưới). Không có giá trị mặc định. |
| `--reserve-mib N` | Dung lượng trống giữ lại ngoài hai lần dump mới nhất (mặc định 1024). |

Giá trị văn bản dài 1 đến 500 ký tự, không có ký tự điều khiển, `"` hoặc `\`. Trước khi ghi, `backup.sh` yêu cầu dung
lượng trống bằng hai lần dump mới nhất (hoặc kích thước database khi chưa có dump nào) cộng với phần dự phòng.

| Mã thoát | Ý nghĩa | Cần làm gì |
| --- | --- | --- |
| 0 | Hoàn tất và đã kiểm tra. | Không cần làm gì. |
| 1 | Bị từ chối trước khi ghi: `backup_running`, `backup_lock_stale`, `insufficient_space` hoặc `name_exists`. | Đọc lý do; chưa có gì được ghi. |
| 2 | Không chạy được: tùy chọn, công cụ, cấu hình, `db` không chạy, hoặc không tạo được khóa sao lưu. | Sửa nguyên nhân và chạy lại. |
| 3 | Thất bại: không có bản sao lưu nào được công bố, hoặc bản được công bố không qua kiểm tra. | Không bao giờ dùng bản sao lưu mà thông báo nêu tên; xóa nó sau khi xem xét. |
| 4 | Bản sao lưu đã hoàn tất và được kiểm tra, nhưng việc xoay vòng (rotation) thất bại hoặc phát hiện một bản sao lưu hằng ngày không qua kiểm tra. | Đọc báo cáo xoay vòng trên lỗi chuẩn (Retention, bên dưới). |

### Kiểm tra một bản sao lưu (Verifying a backup)

Kiểm tra bất kỳ bản sao lưu nào, chỉ đọc, bằng chính các kiểm tra mà `migrate` và lần diễn tập khôi phục dùng. Lệnh
thứ hai kiểm tra các tệp độc lập với mọi image; hãy chạy nó sau mỗi lần sao chép.

```sh
$PF --profile ops run --rm -T --user "$(id -u):$(id -g)" backup-tools backup-verify "<NAME>"
(cd "<backup-dir>/<NAME>" && sha256sum -c SHA256SUMS)
```

`backup-verify` in một tài liệu JSON và thoát với 0 (`verified`), 1 (`invalid`: **không dùng bản sao lưu đó**) hoặc 2
(không kiểm tra được). Đặt `PARTFLOW_RELEASE=<tag>` trước lệnh đầu để dùng image `backup-tools` của bản phát hành
khác.

### Giữ lại, bản sao ngoài máy chủ và cảnh báo (Retention, the off-host copy and alerts)

Một bản sao lưu `daily` kết thúc bằng việc xoay vòng các bản sao lưu hằng ngày. Để xem trước mà không xóa gì:

```sh
$PF --profile ops run --rm -T --user "$(id -u):$(id -g)" backup-tools backup-rotate --keep-daily 14 --keep-weekly 8 --dry-run
```

- Nó giữ `--keep-daily` bản sao lưu hằng ngày đã kiểm tra mới nhất, cộng bản sao lưu hằng ngày mới nhất của mỗi tuần
  trong `--keep-weekly` tuần lịch mới nhất (tuần ISO theo UTC; các tuần chồng lên cửa sổ hằng ngày).
- Nó kiểm tra mọi ứng viên trước. Một bản sao lưu hằng ngày không qua kiểm tra không được tính và **không bao giờ bị
  xóa**: `backup.sh` thoát với 4 và bạn xem xét nó (§9).
- **Bản sao lưu pre-release và manual không bao giờ bị xoay vòng**: bạn xóa chúng sau khi xem xét (§9). Không công cụ
  nào từng xoay vòng hay xóa một thư mục lưu trữ.
- Phần còn sót trong `.partial/` cũ hơn 24 giờ bị xóa.
- 14 và 8 chỉ là ví dụ; chủ sở hữu chọn giá trị thực trong lệnh theo lịch. Chúng là tùy chọn bắt buộc, không bao giờ
  là giá trị mặc định dựng sẵn.

Công cụ sao lưu của nền tảng mã hóa thư mục sao lưu và sao chép nó ra ngoài máy chủ, không bao giờ là bản sao tự
làm: Hyper Backup với mã hóa phía máy khách trên NAS ([`SYNOLOGY_NAS.vi.md`](./SYNOLOGY_NAS.vi.md) §11.3), restic
lên object storage trên VPS ([`VPS.vi.md`](./VPS.vi.md) §8).

Cảnh báo, sau khi các lịch đã được thiết lập: sao lưu theo lịch thất bại (mã thoát khác 0) sẽ kích hoạt thông báo lỗi
của trình lập lịch; sao chép ngoài máy chủ thất bại sẽ kích hoạt thông báo riêng của công cụ sao lưu; và kiểm tra
`backup_age` của `check.sh` thất bại khi bản sao lưu mới nhất cũ hơn 26 giờ (§2). Với sao lưu hằng ngày, tối đa 24
giờ công việc có thể bị mất (mục tiêu điểm khôi phục, recovery point objective, RPO). Cho đến khi sao lưu hằng ngày
được lên lịch, các bản sao lưu duy nhất là những bản `release.sh` tạo trước mỗi lần phát hành và mọi bản sao lưu thủ
công.

### Khóa sao lưu và khóa phát hành (Backup lock and release lock)

Các bản sao lưu không bao giờ chạy cùng lúc. Thư mục `<backup-dir>/.backup.lock` là khóa; tệp `owner` của nó chứa
`host`, `pid`, `started_at`, `by` (`backup.sh` hoặc `release.sh`) và `name` của bản sao lưu hoặc `release`. Lần chạy
thứ hai bị từ chối, không bao giờ xếp hàng: `backup_running` nghĩa là một bản sao lưu, hoặc một lần phát hành đang
giữ khóa, đang diễn ra, nên hãy chờ. `release.sh` giữ khóa từ lúc tạm dừng ghi (hoặc từ bản sao lưu của nó) cho đến
khi `backend` mới chạy. **Hãy để sao lưu hằng ngày tránh xa giờ phát hành:** một bản sao lưu đang chạy sẽ chặn lần
phát hành trước khi tạm dừng ghi mà chưa thay đổi gì, và một bản sao lưu hằng ngày bắt đầu trong lúc phát hành sẽ bị
từ chối. Một khóa hoàn toàn không tạo được (thư mục sao lưu đầy, chỉ đọc hoặc hết inode) được báo là không chạy được
(mã thoát 2), không bao giờ là `backup_running`.

`backup_lock_stale` nghĩa là khóa do một lần chạy không còn tồn tại để lại: nó bị kill, hết bộ nhớ, hoặc mất điện.
**Nó không bao giờ tự động bị xóa.** Xóa nó bằng tay sau các kiểm tra này:

```sh
cat "<backup-dir>/.backup.lock/owner"   # host, pid, started_at, by and name|release
ps -p <pid>                             # on that host: must report no such process
$PF exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "SELECT count(*) FROM pg_stat_activity WHERE application_name = '"'"'pg_dump'"'"'"'   # must print 0
rm -r "<backup-dir>/.backup.lock" "<backup-dir>/.partial/<name>"   # the name from the owner file, when it has one
```

**Không bao giờ xóa một khóa có owner là `release.sh` khi lần phát hành đó đang chạy.** Khóa phát hành là một thư mục
rỗng riêng, `<records-dir>/.release.lock`, mà một `release.sh` bị kill để lại. Khi nó còn tồn tại, `check.sh` bỏ qua
các kiểm tra `status`, và sau 4 giờ nó báo `FAIL database`. Khi không có `release.sh` nào đang chạy (lệnh đầu không
in gì), hãy xóa nó:

```sh
ps -ef | grep '[r]elease\.sh'
rmdir ~/partflow-deployments/.release.lock
```

## 4. Diễn tập khôi phục

Một bản sao lưu chỉ được tin cậy khi nó đã được khôi phục thử. Lần diễn tập khôi phục (restore drill) khôi phục một bản
sao lưu vào một stack tạm thời, tách biệt, kiểm tra nó, rồi xóa stack; nó không bao giờ chạm vào database production.
Hãy chạy một lần sau khi đưa vào sử dụng (go-live), mỗi quý, sau một thay đổi schema lớn, và trước khi đổi image
PostgreSQL hoặc thư viện C của máy chủ (glibc).

### Chạy diễn tập (Running the drill)

Chạy từ thư mục gốc của một thư mục mã nguồn bản phát hành:

```sh
deploy/production/restore-test.sh --backup <NAME> --operator "<your name>"
# a candidate PostgreSQL image, and a list of findings that are already known:
deploy/production/restore-test.sh --backup <NAME> --operator "<your name>" \
    --db-image postgres:16.14-bookworm --baseline-report <records-dir>/<release folder>/pre-reconcile.json
```

`deploy/production/restore-test.sh --help` liệt kê mọi tùy chọn: `--release TAG` (bản phát hành chạy trên database đã
khôi phục; mặc định là bản phát hành trong manifest, và một bản sao lưu tạo giữa lúc migration và lúc chuyển thì phải
chỉ định tên), `--tools-release TAG`, `--db-image IMAGE`, `--baseline-report FILE`, `--project`, `--http-port`,
`--edge-subnet`, `--records-dir`, `--space-factor` và `--keep`.

### Lần diễn tập làm gì (What the drill does)

Lần diễn tập chạy trong Compose project riêng `partflow-restore-<suffix>` (không bao giờ là `partflow-production`),
với volume và mạng riêng, cổng loopback và edge subnet riêng, tên database cố định `partflow_restore_test`, mật khẩu
database role dùng một lần (xóa khi kết thúc), một worker backend và thư mục sao lưu được mount **chỉ đọc**. Nó không
bao giờ chạy `migrate`, không bao giờ ghi vào thư mục sao lưu, và từ chối một project đã có container hoặc volume.
Nó cần dung lượng trống bằng năm lần dump cộng 1 GiB trên bộ nhớ Docker (`--space-factor`).

Nó theo quy trình khôi phục chung (bên dưới) và ghi từng bước vào tệp bằng chứng (evidence): `verify` (bản sao lưu),
`db_start` (một database rỗng trên image PostgreSQL của `compose.production.yaml`, hoặc trên `--db-image`), `restore`
(trong một giao dịch), `roles` và `grants`, `revision`, `app_start` (`backend` và `web` sẵn sàng), `reconcile` (đối
soát đầy đủ, phát lại lịch sử Movement so với mọi tổng và phân bổ đã lưu), `smoke`, `evidence`, và `teardown` (`down -v`
chỉ của project diễn tập, sau khi bằng chứng đã tồn tại; `--keep` giữ project và in ra lệnh).

### Kết quả (Result)

- **`passed`:** đối soát thoát với 0, và cả kiểm tra bảo vệ (guard check) (h) lẫn kiểm tra định danh (identity check)
  "Canonical identity under the running interpreter and database" (kiểm tra (j)) đều đạt. Các quyền (grants) trong
  một lần diễn tập luôn mới, nên mọi phát hiện của kiểm tra (h) là lỗi thật. Với `--db-image`, kiểm tra (j) kiểm thử
  image PostgreSQL ứng viên.
- **`passed_with_preexisting_findings`:** với `--baseline-report`, mọi phát hiện đều đã có trong báo cáo đó, và kiểm
  tra (h) đạt.
- **`failed`:** mọi trường hợp khác.

Tệp bằng chứng `<records-dir>/<UTC>-restore-test-<NAME>/evidence.json` ghi kết quả, bước thất bại, các dữ kiện về bản
sao lưu và lần diễn tập, phiên bản và collation của máy chủ, các phiên bản chính của PostgreSQL của bản sao lưu và của
lần diễn tập cùng `server_major_match` (`false` cũng được in ra như một cảnh báo), thời lượng từng bước và các kết quả
đối soát. `restore_to_ready` và `total` cho bạn biết một lần khôi phục thật sẽ mất bao lâu: hãy so với thời gian nhà
máy có thể làm việc mà không có PartFlow (mục tiêu thời gian khôi phục, recovery time objective, RTO).

| Mã thoát | Ý nghĩa |
| --- | --- |
| 0 | `passed` hoặc `passed_with_preexisting_findings` |
| 1 | `failed`, hoặc không đủ dung lượng trống |
| 2 | Không chạy được, hoặc bị ngắt; không còn gì nó đã tạo ra đang chạy, trừ khi có `--keep` |
| 3 | Teardown thất bại: bằng chứng vẫn còn hiệu lực, và thông báo in ra `docker compose -p <project> down -v` |

Việc đọc từng màn hình của dữ liệu đã khôi phục bằng một tài khoản đã đăng nhập không được tự động hóa
(`read_model_readback: not_automated` trong bằng chứng). Để kiểm tra bằng tay, hãy chạy diễn tập với `--keep`, đăng
nhập tại cổng loopback được in ra từ máy chủ, rồi xóa project sau đó bằng lệnh được in ra.

### Quy trình khôi phục chung (The general restore procedure)

Mọi lần khôi phục theo các quy tắc này, dù lần diễn tập làm hay bạn làm bằng tay (§6). **Khôi phục vào một database
riêng, rỗng, không bao giờ đè lên database production duy nhất.**

1. Kiểm tra checksum của dump và manifest.
2. Dùng cùng phiên bản chính của PostgreSQL, hoặc một phiên bản tương thích đã được ghi tài liệu.
3. Tạo một database rỗng.
4. Khôi phục bằng `pg_restore --single-transaction --exit-on-error --no-owner --no-privileges`: một giao dịch, nên
   một lần khôi phục thất bại để lại database rỗng.
5. Tạo các database role (`provision-roles`) và áp dụng các quyền (`apply-grants`) trước khi ứng dụng khởi động: bản
   sao lưu không chứa quyền, và `partflow_app` không thể hoạt động nếu thiếu chúng.
6. Khởi động bản phát hành tương ứng và kiểm tra revision Alembic.
7. Chạy các kiểm tra tình trạng, các màn hình tiêu biểu, đối soát và kiểm tra nhanh sau khi chuyển (smoke checks).
   **Kiểm tra bảo vệ (h) phải đạt trước khi một database đã khôi phục phục vụ production.**
8. Ghi lại việc khôi phục mất bao lâu và kết quả.
9. Chỉ xóa bản sao riêng sau khi bằng chứng đã được giữ.

Không bao giờ đặt tên database production vào một lệnh diễn tập.

## 5. Cài bản phát hành mới

Trên NAS, dạng ngắn nằm trong [`SYNOLOGY_NAS.vi.md`](./SYNOLOGY_NAS.vi.md) §12. Mục này giải thích điều gì xảy ra và
phải làm gì khi `release.sh` dừng.

### Trước khi bảo trì (Before maintenance)

- Chọn đúng tag phát hành và đọc xem nó thay đổi gì, đặc biệt là các migration database của nó: chúng khóa bảng bao
  lâu, chạy bao lâu và cần bao nhiêu đĩa trên dữ liệu có quy mô thực tế.
- Xác nhận tích hợp liên tục (continuous integration, CI) đã đạt cho commit đó.
- Kiểm tra bản sao lưu mới nhất còn gần đây (§3) và bản sao ngoài máy chủ lần cuối đã thành công. Dù vậy `release.sh`
  tự tạo bản sao lưu trước phát hành đã kiểm tra của nó: **bên trong thời gian tạm dừng ghi** khi có migration đang
  chờ, để không gì được ghi giữa bản sao lưu và migration; nếu không thì ngay trước khi chuyển.
- Xác nhận các image của bản phát hành đang chạy vẫn còn trên máy chủ: quay lui theo đường 1 cần chúng (§6), và
  `release.sh` từ chối khởi động nếu thiếu chúng.
- Chọn thời điểm không có lần nhập Work Order nào đang chạy và không có sao lưu nào đang chạy (sao lưu hằng ngày lúc
  02:00 trên NAS).
- Báo cho người dùng khi nào thay đổi bắt đầu và khi nào bạn sẽ quyết định quay lui, và yêu cầu các trạm hoàn tất các
  hộp thoại đang mở.
- `release.sh` chạy đối soát trên bản phát hành đang chạy trước khi thay đổi bất cứ thứ gì, và xử lý các phát hiện của
  nó như trong §7. Mọi lệnh khôi phục trước khi chuyển dùng tag **đang chạy**: tag mới chỉ tồn tại trong shell cho đến
  lúc chuyển.

### Tạm dừng ghi (Write freeze)

Tạm dừng ghi (write freeze) là **dừng `backend`** (`$PF stop backend`); PartFlow không có chế độ bảo trì nào khác.

- `backend` hoàn tất các request đang xử lý, kể cả một lần nhập, và lệnh dừng chờ tối đa 200 s. Với nhiều worker, các
  request gửi trong lúc đó bị treo và kết thúc bằng 504 hoặc 502 ([`../DEPLOYMENT.vi.md`](../DEPLOYMENT.vi.md) §3.1,
  Backend processes). **Chỉ bắt đầu tạm dừng ghi khi không có lần nhập nào đang chạy.**
- Khi `backend` đang dừng, `web` vẫn phục vụ các màn hình và trả lời mọi request `/api` bằng JSON 504 của nó, rồi
  502. Mọi màn hình hiển thị banner OFFLINE trong khoảng một giây, và mọi thay đổi bị chặn.
- Các hộp thoại đang mở giữ bản nháp của chúng. Một thay đổi chạy đua với lệnh dừng có kết quả chưa rõ: nó giữ
  `device_event_id` của mình và được thử lại bằng nó sau khi mở lại việc ghi trên **cùng** bản phát hành. Sau khi mở
  lại trên bản phát hành **mới**, trang cũ không gửi lại được (bị từ chối với 409): người dùng tải lại trang và kiểm
  tra Area hoặc Work Order trước khi lặp lại thao tác.
- Một lần nhập bị cắt ngang chỉ để lại các Work Order trọn vẹn, đã commit.
- `reconcile`, `revision`, `migrate` và sao lưu chạy trong các container chạy một lần trên `db` trong thời gian tạm
  dừng ghi.
- **Mở lại** bằng `$PF up -d backend` trên cùng bản phát hành, chỉ sau khi các kiểm tra bắt buộc đã đạt. Khi chuyển
  bản phát hành, việc mở lại chính là việc chuyển `web`.

### Thực hiện (Execute)

Chạy `release.sh` từ thư mục gốc của thư mục mã nguồn của bản phát hành mới, với tag của nó đã được checkout (trên
NAS: `git fetch --tags` và `git checkout <new tag>` trong `repo/`):

```bash
deploy/production/release.sh --release <new tag> --operator "<your name>" --approver "<your name>"
```

Tag có dạng `vMAJOR.MINOR.PATCH`, tùy chọn theo sau là `-alpha.N`, `-beta.N` hoặc `-rc.N`
([`../DEPLOYMENT.vi.md`](../DEPLOYMENT.vi.md) §10.2). Một người có thể ghi cùng một tên cho người vận hành và người
phê duyệt. `deploy/production/release.sh --help` liệt kê mọi tùy chọn. Các tùy chọn sao lưu là:

- **Không dùng tùy chọn nào (bình thường):** `release.sh` tự tạo bản sao lưu trước phát hành đã kiểm tra.
- **`--pre-release-backup <NAME>`:** dùng một bản sao lưu `backup.sh` có sẵn thay thế. Chỉ được phép cho bản phát hành
  **không có** migration đang chờ; nếu có, `release.sh` dừng mà chưa thay đổi gì, vì khi đó bản sao lưu phải được tạo
  bên trong thời gian tạm dừng ghi.
- **`--no-backup-reason "<text>"`:** không có bản sao lưu trước phát hành, chỉ dành cho diễn tập thử; lý do được ghi
  lại.

Các tùy chọn khác đặt tệp cấu hình, thư mục hồ sơ, các trường hồ sơ tùy chọn như `--rollback-deadline` và
`--observation-owner`, và một lần diễn tập thử trên một Compose project dùng một lần.

`release.sh` chạy và ghi lại các bước sau:

1. **Preflight:** các công cụ, tệp cấu hình, dạng tag, và các đầu vào build khớp với commit của tag (không có tệp bị
   thay đổi hoặc chưa theo dõi). Nó từ chối, không thay đổi gì, khi một tệp bí mật thiếu, rỗng hoặc không phải tệp
   thường, khi tồn tại một Compose project `partflow-staging` trên cùng Docker daemon, khi image của bản phát hành đang
   chạy bị thiếu, và khi một bản phát hành khác đang giữ khóa.
2. Revision database hiện tại, và đối soát với bản phát hành đang chạy (`pre-reconcile.json`).
3. Build các image mới. Một tag đã tồn tại không bao giờ được build lại; nó chỉ được dùng lại khi cả hai image đều
   được build từ commit này với tư cách bản phát hành này.
4. Kiểm tra định danh (j) và `revision` của image mới trên database chưa thay đổi; chúng phải báo bản phát hành và
   commit này.
5. Tạm dừng ghi, khi có migration đang chờ.
6. Bản sao lưu trước phát hành đã kiểm tra (`backup.sh --kind pre-release`), bên trong thời gian tạm dừng ghi khi có
   migration đang chờ. Khóa sao lưu được giữ từ đây cho đến bước 9.
7. `migrate`, kiểm tra bản sao lưu trước rồi áp dụng các migration và các quyền (grants).
8. Đối soát sau migration (`post-reconcile.json`).
9. Chuyển `backend` sang bản phát hành mới, trong khi `web` vẫn phục vụ các màn hình cũ: các lần ghi của chúng bị từ
   chối (409), vì mọi trang đang mở gửi bản phát hành cũ. Sau đó chờ đến khi health báo bản phát hành mới với schema
   `current`.
10. Chuyển `web`, mở lại việc ghi, rồi chạy `smoke.sh`.

Một kiểm tra thất bại sau khi chuyển sẽ dừng `backend` lại.

**Cách `migrate` đánh giá bản sao lưu trước phát hành.** Nó kiểm tra bản sao lưu trước khi kết nối, và chỉ chấp nhận
khi bản sao lưu còn mới và thuộc về database này:

- Dump bắt đầu vào hoặc sau thời điểm do `--backup-not-before` đưa ra, mà `release.sh` truyền vào (thời điểm tạm dừng
  ghi khi có migration đang chờ), để nó chứa mọi lần ghi đã commit.
- Không có `--backup-not-before`, áp dụng giới hạn tuổi 60 phút, nhưng chỉ khi không có migration đang chờ; với
  migration đang chờ, `migrate` từ chối (`backup_freshness_unproven`).
- Revision và tên database của nó bằng của database.

Một lần từ chối (`backup_not_found`, `backup_invalid`, `backup_stale`, `backup_revision_mismatch`,
`backup_freshness_unproven`, `backup_database_mismatch`; mã thoát 1) không thay đổi gì và, trong thời gian tạm dừng
ghi, mở lại việc ghi trên bản phát hành hiện tại. `backup_unreadable` (mã thoát 2) nghĩa là container không đọc được
thư mục sao lưu: nó cần `--user "$(id -u):$(id -g)"`, mà `release.sh` truyền vào.

**Các quyền (grants) bên trong `migrate`.** Mỗi lần `migrate` áp dụng các quyền trong cùng giao dịch với migration.
Một lần từ chối liên quan đến database role (`roles_not_provisioned`, `roles_incomplete`, `role_unsafe`,
`role_owns_objects`, `foreign_grantor`, `table_unclassified`, `table_missing`, `not_superuser`) hoàn tác toàn bộ lần
chạy và dừng lần phát hành trước khi có gì thay đổi. Đọc thông báo, sửa nguyên nhân (`$PF --profile ops run --rm -T
db-roles` cho vấn đề về role; §7, Sửa các phát hiện của kiểm tra (h), cho các trường hợp khác) và chạy lại lần phát
hành.

| Mã thoát | Ý nghĩa | Cần làm gì |
| --- | --- | --- |
| 0 | Hoàn tất. | Theo dõi (bên dưới). |
| 1 | Dừng mà chưa thay đổi gì, hoặc việc ghi được mở lại trên bản phát hành hiện tại. | Đọc thông báo và `record.json`, sửa nguyên nhân, chạy lại. |
| 2 | Không chạy được (tùy chọn, công cụ, cấu hình). | Chưa thay đổi gì; sửa nguyên nhân và chạy lại. |
| 3 | `backend` bị để dừng: việc ghi vẫn bị tạm dừng và không có gì tự mở lại. | Đọc đầu ra của bước thất bại và `regression.txt` (hoặc so sánh `pre-reconcile.json` với `post-reconcile.json`). Sau đó chọn một: **mở lại**, chỉ khi mọi kiểm tra bắt buộc đạt: trước khi chuyển, `$PF up -d backend` (bản phát hành được ghi trong `.env.production`); sau khi chuyển (`.env.production` đã ghi tag mới), `$PF up -d --no-deps backend`, rồi `$PF up -d --no-deps web`, rồi `deploy/production/smoke.sh --release <tag>`. Hoặc **quay lui** (§6). |
| 4 | Bản phát hành mới có thể đang chạy và nhận ghi sau một kiểm tra thất bại, và việc dừng nó thất bại. | Tự chạy `$PF stop backend` ngay, rồi làm theo §6. |
| 130, 143 | Bị ngắt bởi Ctrl-C hoặc tín hiệu TERM (`outcome: interrupted`; bước cuối cho biết ở đâu). Bản thân việc ngắt không khởi động hay dừng gì. | Nếu xảy ra trong `migrate`, kết quả chưa rõ (`migration.result: outcome_unknown`): chạy `$PF run --rm --no-deps -T backend python -m app.cli revision` trước mọi việc khác, rồi `$PF ps`, rồi §6. Sau khi chuyển `backend`, `.env.production` đã ghi tag mới; `env-before.txt` trong thư mục hồ sơ là tệp trước đó. |

Hai tùy chọn xử lý các phát hiện của đối soát trước thay đổi:

- `--accept-pre-release-findings` tiếp tục dù đối soát trước thay đổi có phát hiện, và chỉ chặn với các phát hiện mới
  xuất hiện sau đó (`deploy/production/reconcile_regression.py` so sánh hai báo cáo).
- `--skip-pre-reconcile REASON` dùng cho trạng thái sau khi quay lui theo đường 2, khi không image nào có revision
  database làm head: lý do được ghi lại, và mọi phát hiện sau thay đổi đều chặn.

#### Cùng bản phát hành làm bằng tay (The same release by hand)

Cách làm thủ công tương đương `release.sh`, theo cùng thứ tự, khi tag hiện tại vẫn còn trong `.env.production`:

1. Ghi lại các revision ứng dụng và database hiện tại
   (`$PF run --rm --no-deps -T backend python -m app.cli revision`) và chạy đối soát với tag hiện tại (§7).
2. Build các image mới (không bao giờ một tag đã tồn tại):
   `PARTFLOW_RELEASE=<new> PARTFLOW_COMMIT=$(git rev-parse HEAD) $PF -f compose.production.build.yaml build`. Sau đó
   kiểm thử image mới trên database chưa thay đổi:
   `PARTFLOW_RELEASE=<new> $PF run --rm --no-deps -T backend python -m app.cli reconcile --check j`. Một kiểm tra
   thất bại sẽ dừng lần phát hành; chưa có gì thay đổi.
3. Khi có migration đang chờ, tạm dừng ghi: `$PF stop backend`, kiểm tra `$PF ps --status running -q backend` không in
   gì, rồi ghi lại thời điểm, `T=$(date -u +%Y-%m-%dT%H:%M:%SZ)`. Khi không có migration đang chờ, ghi `T` ngay trước
   bước 3a.
   3a. Tạo bản sao lưu từ thư mục mã nguồn của bản phát hành mới:
   `deploy/production/backup.sh --kind pre-release --label <new> --tools-release <new> --operator "<your name>"`, và
   ghi lại NAME từ dòng `BACKUP`.
4. Migrate:
   `PARTFLOW_RELEASE=<new> $PF --profile ops run --rm -T --user "$(id -u):$(id -g)" migrate --pre-release-backup NAME --backup-not-before "$T"`.
   Không có `--backup-not-before`, `migrate` từ chối một migration đang chờ (`backup_freshness_unproven`); không có
   `--user`, nó không đọc được thư mục sao lưu (`backup_unreadable`). Giữ đầu ra JSON của nó (trường `grants` báo các
   quyền, trường `backup` báo kết quả kiểm tra) và revision mới.
5. Chạy đối soát với tag mới (§7) và so sánh với lần ở bước 1. Chỉ các phát hiện mới chặn bước 7; các phát hiện đã tồn
   tại vẫn là sự cố đang mở để chủ sở hữu quyết định.
6. Ghi `PARTFLOW_RELEASE=<new>` vào `.env.production` và làm rỗng `PARTFLOW_ACCEPT_SCHEMA_REVISION`, rồi chạy
   `$PF up -d --no-deps backend` và kiểm tra health: `release` là tag mới và `schema` là `current`.
7. Chạy `$PF up -d --no-deps web`, mở lại việc ghi. Chạy `deploy/production/smoke.sh --release <new>` và các kiểm tra
   đăng nhập, phân quyền, quét và một thay đổi được lưu rồi đọc lại.
8. Chỉ để việc ghi mở khi mọi kiểm tra bắt buộc đạt; nếu không, chạy lại `$PF stop backend` và làm theo §6.

### Theo dõi (Observe)

Ngay khi `release.sh` kết thúc với 0, hãy tự kiểm tra bằng dữ liệu thử những gì `smoke.sh` không kiểm tra được: đăng
nhập, kiểm tra phân quyền vẫn hoạt động như trước, quét tại một Scan Station (ô quét giữ con trỏ), và lưu một thay đổi
rồi đọc lại. Sau đó, trong thời gian theo dõi bạn đã chọn (ghi lại bằng `--rollback-deadline` và
`--observation-owner`), hãy theo dõi lỗi, thời gian phản hồi, thời gian chờ khóa, các lần khởi động lại, đĩa và những
gì người dùng báo: các dòng `errors`, `restarts` và `disk_*` của `check.sh`, các lần đọc chậm (`"slow":true`) và
`status` với thời gian chờ khóa của nó (§2).

- Một lần kiểm tra trong thời gian tạm dừng ghi báo `backend` đã dừng (một thông báo) và bỏ qua các kiểm tra `status`
  khi khóa phát hành còn tồn tại; sau 4 giờ nó báo `FAIL database`.
- Lần phát hành tạo lại `backend`, làm xóa log của nó: chạy `check.sh --only errors --no-state` trước khi phát hành
  (§2).
- Giữ các image của bản phát hành trước và bản sao lưu trước phát hành (không bao giờ bị xoay vòng) cho đến hạn chót
  quay lui.
- Các trang đang mở trong lúc chuyển hiển thị thông báo cập nhật và phải được tải lại
  ([`../GUI_DESIGN.vi.md`](../GUI_DESIGN.vi.md) §3 rule 13); một Scan Station hoặc Production Board không có người
  trông tự tải lại khi không còn hộp thoại nào đang mở.

## 6. Quay lui

Chọn đường **đầu tiên** có điều kiện đúng. Các lệnh khôi phục luôn chạy với bản phát hành khớp với database. **Không
bao giờ cho rằng `alembic downgrade` là an toàn:** PartFlow bảo vệ lịch sử chỉ-ghi-thêm (append-only), và một lần
downgrade có thể bị từ chối hoặc sẽ làm mất dữ liệu mới được hỗ trợ. Mọi đường đều tạo lại `backend`, việc này xóa
log của nó: hãy giữ lại các lỗi gần đây trước bằng
`deploy/production/check.sh --url https://<partflow-host> --only errors --no-state` (§2).

1. **Đường 1 (path 1): bản phát hành không thay đổi database.** Kiểm tra: lệnh này thoát với 0 và hiển thị
   `"state": "current"` khi bản phát hành trước khớp với database.

   ```sh
   PARTFLOW_RELEASE=<previous tag> $PF run --rm --no-deps -T backend python -m app.cli revision
   ```

   Sau đó quay về bản phát hành trước, cần các image của nó còn trên máy chủ:

   ```sh
   git checkout <previous tag>
   sed -i 's|^PARTFLOW_RELEASE=.*|PARTFLOW_RELEASE=<previous tag>|' .env.production
   $PF up -d backend web
   deploy/production/smoke.sh --release <previous tag>
   ```

   Nếu `up` thất bại với `No such image`, các image trước đã bị xóa và đường 1 là bất khả thi; không bao giờ chạy
   `docker image prune -a` trong thời gian có thể quay lui.

2. **Đường 2 (path 2): database đã được migrate, và bản phát hành trước đã được xác minh hoạt động với schema mới.**
   Chỉ dùng khi sự tương thích đó đã được kiểm tra rõ ràng và ghi lại. Đọc `database_revision` từ lệnh đầu tiên
   bên dưới (nó in ra giá trị này kể cả khi thoát với 1), rồi chuyển sang bản phát hành trước với một giá trị ghi đè
   đúng cho revision đó:

   ```sh
   $PF run --rm --no-deps -T backend python -m app.cli revision
   git checkout <previous tag>
   sed -i 's|^PARTFLOW_RELEASE=.*|PARTFLOW_RELEASE=<previous tag>|' .env.production
   sed -i 's|^PARTFLOW_ACCEPT_SCHEMA_REVISION=.*|PARTFLOW_ACCEPT_SCHEMA_REVISION=<database_revision>|' .env.production
   $PF up -d backend web
   deploy/production/smoke.sh --release <previous tag> --allow-accepted-schema
   ```

   - Giá trị ghi đè nêu đúng một revision mà bản phát hành trước không biết. Một revision nó biết thì bị bỏ qua
     (`revision` hiển thị `override_ignored`, và readiness vẫn là `mismatch`). Nó không bao giờ khớp revision nào
     khác, và lần phát hành kế tiếp xóa nó.
   - Trước khi đặt giá trị ghi đè, bản phát hành trước từ chối mọi thay đổi; các lần đọc của trạm vẫn ghi lại thời
     điểm thấy thiết bị lần cuối, vốn không chứa dữ liệu sản xuất.
   - Trong khi giá trị ghi đè còn hiệu lực, thêm `--allow-accepted-schema` vào lệnh `check.sh` theo lịch; nếu không,
     kiểm tra `https` của nó thất bại.
   - Để phát hành tiến từ trạng thái này, `release.sh` chạy đối soát trước thay đổi bằng image mới, hoặc bạn đưa
     `--skip-pre-reconcile REASON`.

3. **Đường 3 (path 3): database đã được migrate, và bản phát hành trước không tương thích, hoặc chưa rõ có tương thích
   không.** Dừng việc ghi, khôi phục bản sao lưu trước phát hành vào một database mới, rỗng trong cùng máy chủ
   PostgreSQL (database đã migrate giữ nguyên), và khởi động bản phát hành trước trên đó, theo thủ tục path 3 bên
   dưới. Đường 3 không bao giờ bỏ các lần ghi thực hiện sau bản sao lưu; khi có, đó là đường 4.
4. **Đường 4 (path 4): đã có lần ghi sản xuất sau migration.** Không khôi phục đè lên chúng. Giữ cả database hiện tại
   lẫn bản sao lưu trước phát hành, giữ PartFlow ở trạng thái chặn ghi, và để chủ sở hữu quyết định giữa sửa tiến
   (forward fix) hoặc một kế hoạch khôi phục dữ liệu có kiểm toán.

### Thủ tục path 3

Đường 3 chỉ áp dụng khi hồ sơ phát hành cho thấy `writes_reopened_at: null` (lần phát hành dừng ở trạng thái tạm
dừng ghi) hoặc chủ sở hữu ghi nhận rằng không có lần ghi sản xuất nào sau migration; nếu không thì đó là đường 4. Việc
khôi phục vào production cần **sự phê duyệt của chủ sở hữu, được ghi vào `rollback.json` trước khi `backend` khởi
động** trên database đã khôi phục; không có nó, thủ tục dừng sau `reconcile`, với PartFlow ở trạng thái chặn ghi.

Các giá trị của `.env.production` do Compose đọc và không có sẵn trong shell, nên mọi giá trị dưới đây được đặt tường
minh. **Mỗi khối là một script: lưu nó vào thư mục hồ sơ phát hành và chạy bằng `sh -eu <file>`, không bao giờ dán
từng dòng.** Một guard không thỏa mãn sẽ in lý do và dừng script trước lệnh kế tiếp, và mọi lệnh thất bại (`-e`) hoặc
biến chưa đặt (`-u`) cũng vậy.

**Bước A** chạy trong thư mục mã nguồn của bản phát hành **mới**, mà `release.sh` của nó đã tạo bản sao lưu, để image
`backup-tools` của nó kiểm tra bản sao lưu. Trong bản cài trên NAS đây là `repo/` với tag mới vẫn đang được checkout.
Bước A kiểm tra đường 3 có áp dụng không, xác nhận việc tạm dừng ghi và kiểm tra bản sao lưu:

```sh
REC=<release record directory>; CAND=<candidate tag>; PREV=<previous tag>
# Path 3 only when writes were never reopened; drop this guard only for the owner's recorded exception.
python3 -c 'import json,sys; sys.exit(json.load(open(sys.argv[1]))["writes_reopened_at"] is not None)' "$REC/record.json" \
    || { echo "writes were reopened: path 4, not path 3"; exit 1; }
NAME=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["backup"]["name"])' "$REC/record.json")
BPATH=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["backup"]["path"])' "$REC/record.json")
PF="docker compose -f compose.production.yaml --env-file .env.production"
$PF stop backend                                                  # keep or enter the write freeze
RUNNING=$($PF ps --status running -q backend)
[ -z "$RUNNING" ] || { echo "backend is still running: the write freeze is not in place"; exit 1; }
(cd "$BPATH" && sha256sum -c SHA256SUMS)                          # host check, independent of any image
PARTFLOW_RELEASE="$CAND" $PF --profile ops run --rm --no-deps -T --user "$(id -u):$(id -g)" backup-tools backup-verify "$NAME"
echo "BPATH=$BPATH"
```

**Bước B** chạy trong thư mục mã nguồn của bản phát hành **trước**, với các image của bản phát hành trước có trên máy
chủ: trong bản cài trên NAS, chạy `git checkout <previous tag>` trong `repo/` trước. Nó gồm ba script. **B1** kiểm
tra dung lượng và khôi phục vào một database mới. Tên mới không bao giờ là tên đang dùng, và `createdb` từ chối tên đã
tồn tại. Volume database cần dung lượng trống ít nhất bằng kích thước database cộng hai lần dump cộng 1 GiB, cho bản
sao đã khôi phục, nhật ký ghi trước (write-ahead log, WAL) của giao dịch của nó và phần dự phòng:

```sh
BPATH=<backup path printed by step A>
ENV=.env.production
PF="docker compose -f compose.production.yaml --env-file $ENV"
OLDDB=$(sed -n 's/^POSTGRES_DB=//p' "$ENV")
NEWDB="${OLDDB}_r$(date -u +%Y%m%d%H%M)"                          # never the live name; createdb refuses an existing one
echo "NEWDB=$NEWDB OLDDB=$OLDDB"
# Space: the database volume must hold the restored copy, the WAL of its transaction and a reserve (a value not read stops).
FREE_KIB=$($PF exec -T db sh -c 'df -Pk /var/lib/postgresql/data' | awk 'NR==2 {print $4}')
DB_BYTES=$($PF exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "SELECT pg_database_size(current_database())"')
DUMP_BYTES=$(wc -c < "$BPATH/partflow.dump")
[ -n "$FREE_KIB" ] && [ -n "$DB_BYTES" ] && [ $((FREE_KIB * 1024)) -ge $((DB_BYTES + 2 * DUMP_BYTES + 1073741824)) ] \
    || { echo "not enough space (or it could not be read): expand the storage first, or follow path 4"; exit 1; }
$PF exec -T db sh -c 'createdb -U "$POSTGRES_USER" --template=template0 "$1"' sh "$NEWDB"
# A failed restore stops here with nothing restored (one transaction): drop the never-live copy, fix the cause, rerun B1:
#   $PF exec -T db sh -c 'dropdb -U "$POSTGRES_USER" "$1"' sh <NEWDB printed above>
$PF exec -T db sh -c 'pg_restore -U "$POSTGRES_USER" -d "$1" --single-transaction --exit-on-error --no-owner --no-privileges' sh "$NEWDB" \
    < "$BPATH/partflow.dump"
```

Sau đó sửa `.env.production`: `PARTFLOW_RELEASE=<previous tag>`, `POSTGRES_DB=<NEWDB printed by B1>` và
`PARTFLOW_ACCEPT_SCHEMA_REVISION=` (để rỗng). **B2** áp dụng các quyền (grants) và kiểm tra database đã khôi phục bằng
bản phát hành trước; các database role đã tồn tại, vì role thuộc về toàn bộ máy chủ PostgreSQL:

```sh
REC=<release record directory>
PF="docker compose -f compose.production.yaml --env-file .env.production"
$PF --profile ops run --rm -T db-roles apply-grants               # roles already exist in the cluster
$PF run --rm --no-deps -T backend python -m app.cli revision      # "state": "current" (otherwise it exits non-zero: B2 stops)
rc=0
$PF run --rm --no-deps -T backend python -m app.cli reconcile --max-findings 10000 > "$REC/rollback-reconcile.json" || rc=$?
echo "reconcile exit $rc"
```

`reconcile` phải thoát với 0, hoặc với 1 và chỉ có các phát hiện cũng nằm trong `pre-reconcile.json` của bản phát hành
(`deploy/production/reconcile_regression.py` so sánh chúng). Ghi sự phê duyệt của chủ sở hữu vào `$REC/rollback.json`
trước B3, dưới dạng một tài liệu JSON gồm máy chủ, tag của bản phát hành trước và bản phát hành ứng viên, người vận
hành, các thời điểm (UTC), `approved_by` (chủ sở hữu), `approved_at` (UTC), `reason`, tên database mới và cũ, tên bản
sao lưu và cả hai kết quả đối soát.

**B3** khởi động bản phát hành trước trên database đã khôi phục và chỉ chuyển `web` sau khi `backend` báo mình đã sẵn
sàng, để việc ghi chỉ được mở lại trên một backend đã được xác minh. Bước chờ health gọi `127.0.0.1` trên cổng đã
publish, vì `web` chỉ lắng nghe trên địa chỉ loopback:

```sh
PREV=<previous tag>
ENV=.env.production
PF="docker compose -f compose.production.yaml --env-file $ENV"
PORT=$(sed -n 's/^PARTFLOW_HTTP_PORT=//p' "$ENV")
$PF up -d db backend                                              # db recreated for the new POSTGRES_DB (same volume)
# The web switch waits (up to 180 s) for backend to report the previous release with schema current.
ready=; i=0
while [ "$i" -lt 90 ]; do
    i=$((i + 1))
    body=$(curl -fsS --max-time 10 "http://127.0.0.1:$PORT/api/health" 2>/dev/null) || body=
    case $body in *"\"release\":\"$PREV\""*) case $body in *'"schema":"current"'*) ready=1; break ;; esac ;; esac
    sleep 2
done
[ -n "$ready" ] || { echo "backend did not report release $PREV with schema current: web not switched; stop backend and review"; exit 1; }
$PF up -d --no-deps web                                           # the reopen: backend first, then web
deploy/production/smoke.sh --release "$PREV"
```

**Database đã migrate giữ nguyên tên và nội dung** để phân tích theo đường 4. Chủ sở hữu xóa nó sau thời gian theo
dõi, không bao giờ để một công cụ làm việc đó: `$PF exec -T db sh -c 'dropdb -U "$POSTGRES_USER" "$1"' sh <old name>`.

### Khôi phục sang bản cài mới (New-instance restore)

Dùng khi máy chủ hỏng (§8) hoặc khi chuyển sang máy chủ khác (bên dưới). Nó cài bản phát hành lên một máy chủ có
volume database **mới và rỗng**, rồi khôi phục một bản sao lưu vào đó.

1. Chuẩn bị máy chủ theo hướng dẫn của nền tảng, ở **cùng bản phát hành** với bản sao lưu (`release.tag` trong
   `manifest.json` của nó), cho đến và gồm cả bước build image: trên NAS, [`SYNOLOGY_NAS.vi.md`](./SYNOLOGY_NAS.vi.md)
   §1 đến §7 bước 1. Không tạo role hay chạy `migrate` (§7 bước 3 ở đó): lần khôi phục này thay thế chúng.
2. Sao chép thư mục sao lưu vào thư mục sao lưu của máy chủ và kiểm tra nó, như ở bước A:
   `(cd "<backup-dir>/<NAME>" && sha256sum -c SHA256SUMS)` và
   `$PF --profile ops run --rm -T --user "$(id -u):$(id -g)" backup-tools backup-verify "<NAME>"`.
3. Kiểm tra dung lượng theo quy tắc của bước B1 (với kích thước database là 0): dung lượng trống ít nhất bằng hai lần
   dump cộng 1 GiB trên volume database.
4. Khởi động database: `$PF up -d db`.
5. Kiểm tra nó rỗng; lệnh này phải in `0`, nếu không thì dừng, vì volume không phải mới:
   `$PF exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = '"'"'public'"'"'"'`
6. Khôi phục, với `BPATH` đặt thành đường dẫn của thư mục sao lưu (`BPATH=<backup-dir>/<NAME>`):
   `$PF exec -T db sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --single-transaction --exit-on-error --no-owner --no-privileges' < "$BPATH/partflow.dump"`.
   Một lần thất bại được hoàn tác và database vẫn rỗng; kiểm tra của bước 5 đạt lại trước khi thử lại.
7. Tạo các role và áp dụng các quyền: `$PF --profile ops run --rm -T db-roles`, rồi
   `$PF --profile ops run --rm -T db-roles apply-grants`.
8. Chạy `revision` và `reconcile` như ở bước B2, và giữ báo cáo trong thư mục hồ sơ.
9. Ghi sự phê duyệt của chủ sở hữu vào `<records-dir>/<UTC>-restore-<NAME>/restore.json` (cùng các trường như
   `rollback.json`). Sau đó khởi động ứng dụng và kiểm tra nó. Khi sự phê duyệt đã được ghi, `backend` và `web` khởi
   động cùng lúc:

   ```sh
   $PF up -d backend web
   $PF ps
   curl -fsS http://127.0.0.1:<PARTFLOW_HTTP_PORT>/api/health
   deploy/production/smoke.sh --release <tag>
   ```

   Chạy lại `$PF ps` cho đến khi `backend` và `web` đều `(healthy)`; `/api/health` phải hiển thị bản phát hành đã khôi
   phục và `"schema":"current"` trước khi bạn chạy `smoke.sh`.
10. Tiếp tục với bước HTTPS của hướng dẫn nền tảng (NAS: §9). Bỏ qua thiết lập lần đầu (first-run setup): database đã
    khôi phục đã có sẵn các Administrator. Các thiết bị Scan Station tiếp tục hoạt động khi tên miền truy cập giữ
    nguyên; với tên miền truy cập mới, hãy đăng ký lại chúng.

Ghi lại khoảng dữ liệu bị mất: các lần ghi sau `dump_started_at` của bản sao lưu không có trong database đã khôi phục.

### Chuyển sang máy chủ khác (Moving to another host)

Việc chuyển cài cùng một bản phát hành lên máy chủ mới và khôi phục bản sao lưu cuối cùng được tạo bên trong thời gian
tạm dừng ghi trên máy chủ cũ. Hai bản cài không bao giờ được nhận ghi cùng lúc.

1. Chuẩn bị máy chủ mới như bước 1 của khôi phục sang bản cài mới, ở bản phát hành đang chạy trên máy chủ cũ.
2. Diễn tập: sao chép một bản sao lưu gần đây vào thư mục sao lưu của máy chủ mới và chạy diễn tập khôi phục ở đó (§4).
3. Thông báo việc chuyển, rồi bắt đầu tạm dừng ghi trên máy chủ cũ: `$PF stop backend`.
4. Trên máy chủ cũ, tạo bản sao lưu cuối cùng bên trong thời gian tạm dừng ghi:
   `deploy/production/backup.sh --kind manual --operator "<your name>" --reason "move to new host"`.
5. Sao chép thư mục sao lưu đó vào thư mục sao lưu của máy chủ mới qua kết nối mã hóa (ví dụ `scp -r`).
6. Khôi phục nó bằng khôi phục sang bản cài mới ở trên (bước 2 đến 9), có ghi sự phê duyệt của chủ sở hữu.
7. Thiết lập HTTPS trên máy chủ mới cho cùng tên miền truy cập, rồi trỏ tên miền đó sang máy chủ mới: hạ thời gian
   sống (time to live, TTL) của DNS trước đó, đổi bản ghi và kiểm thử từ các máy khách.
8. Giữ `backend` của máy chủ cũ ở trạng thái dừng và dữ liệu của nó không bị đụng đến cho đến khi bạn chắc chắn sẽ
   không quay lại. Không bao giờ khởi động lại nó với việc ghi khi máy chủ mới đang chạy.

## 7. Đối soát

Đối soát (reconciliation) là lệnh chỉ đọc `python -m app.cli reconcile`. Nó chạy các kiểm tra dưới đây trong một
snapshot database chỉ đọc và in một báo cáo JSON ra đầu ra chuẩn; **nó không bao giờ sửa chữa gì**.

### Chạy đối soát (Running it)

Chạy từ thư mục mã nguồn của bản phát hành với tag đang chạy. Nó cũng chạy được khi `backend` đang dừng (`--no-deps`
không khởi động gì khác, nhưng `db` phải đang chạy):

```sh
f="reconcile-$(date -u +%Y%m%dT%H%M%SZ).json"
$PF run --rm --no-deps -T backend python -m app.cli reconcile > "$f"; rc=$?
python3 -c 'import json,sys; r=json.load(open(sys.argv[1])); assert r["exit_code"]==int(sys.argv[2]); print(r["result"], r["exit_code"])' "$f" "$rc" \
  || echo "could not run: no complete report (exit $rc)"
```

Lệnh cuối in `clean 0`, `mismatch 1` hoặc `error 2`, hoặc "could not run" khi báo cáo không đầy đủ.
Để kiểm thử một image mới trước khi phát hành: `PARTFLOW_RELEASE=<new> $PF run --rm --no-deps -T backend python -m app.cli reconcile --check j`.
Dạng dùng khi phát triển nằm trong [`README.vi.md`](../../README.vi.md) ở thư mục gốc; với một bản staging của công cụ
Deployment Admin `pf`, xem [`SYNOLOGY_ADMIN.vi.md`](./SYNOLOGY_ADMIN.vi.md) §14.

### Tùy chọn, mã thoát và quy tắc báo cáo (Options, exit codes and the report rule)

- `--check ID` (lặp lại được, `a` đến `j`; các kiểm tra khác được báo là `skipped`), `--statement-timeout SECONDS`
  (1 đến 3600, mặc định 300) và `--max-findings N` (1 đến 10000, mặc định liệt kê 100 phát hiện cho mỗi kiểm tra;
  `finding_count` luôn giữ số lượng đầy đủ).
- Mã thoát: `0` sạch, `1` không khớp (có ít nhất một phát hiện), `2` không chạy được hoặc không đầy đủ.
- **Mã thoát chỉ có giá trị khi báo cáo là một tài liệu JSON đầy đủ mà `exit_code` bằng đúng nó.** Một báo cáo rỗng
  hoặc không đọc được nghĩa là "không chạy được", bất kể mã thoát.
- Giữ báo cáo trong hồ sơ triển khai (§1).

### Các kiểm tra (Checks)

| Kiểm tra | Nó xác minh gì |
| --- | --- |
| (a) Current positions replay from Movement history | Các vị trí hiện tại bằng kết quả phát lại lịch sử Movement chưa bị đảo ngược. |
| (b) Quantity Flow history and conservation | Mọi flow đang hoạt động hoặc đã đóng đều có lịch sử bảo toàn hợp lệ, cộng các quy tắc liên dòng của [`SLICE1_DATA_MODEL.vi.md`](../SLICE1_DATA_MODEL.vi.md) §17 trừ quy tắc dòng kiểm toán (audit-row). |
| (c) Per-PN quantity balance | Số lượng đưa vào theo từng PN bằng các kết cục đang hoạt động, đã nhập kho, đã loại bỏ và đã đảo ngược của nó. |
| (d) Machine assigned quantities | Số lượng gán cho mỗi Machine bằng các flow trên đó. |
| (e) Release evidence and Work Order status | `released_quantity` của mỗi demand suy ra từ bằng chứng `RECEIVED` của nó. |
| (f) Allocations and Work Order completion | `allocated_quantity` của mỗi demand và `completed_at` của mỗi Work Order suy ra từ các phân bổ đang hoạt động; một phân bổ được cho phép vượt quá demand (`exceeds_demand`) không phải là phát hiện. |
| (g) Retained Movements reference no purged row | Không Movement nào được giữ lại trỏ đến một dòng đã bị xóa hẳn (purged). |
| (h) Append-only guards and database-role privileges intact | Các trigger và hàm bảo vệ có mặt, được bật và không đổi; `partflow_app` và `partflow_maintenance` giữ đúng các quyền (grants) của chúng (không `UPDATE`, `DELETE` hay `TRUNCATE` trên lịch sử chỉ-ghi-thêm), giữ các thuộc tính an toàn và không là thành viên của role nào; `PUBLIC` và các đặc quyền mặc định không chứa gì; và `session_replication_role` không phải `replica`. Nó không thấy được thay đổi của một superuser đã tắt một trigger rồi khôi phục lại sau đó (một giới hạn được chấp nhận). |
| (i) Hot list entries are active demand | Truy vấn Hot list của [`../DEPLOYMENT.vi.md`](../DEPLOYMENT.vi.md) §5 không trả dòng nào. |
| (j) Canonical identity under the running interpreter and database | PN chuẩn hóa, badge Worker không phân biệt hoa thường, quy tắc tiền tố Asset Tag và mọi ràng buộc dạng chuẩn đều đúng theo quy tắc sắp xếp của database đang chạy, phiên bản collation không đổi, và không khóa định danh nào bị trùng. |

Kiểm tra (g) báo `not_applicable` cho đến khi có việc lưu trữ lịch sử Movement, và kiểm tra (h) báo `not_applicable`
chỉ trên database không có các role của PartFlow (phát triển, kiểm thử, staging); cả hai đều trung lập đối với mã
thoát. Trong production, thiếu một role là một phát hiện.

### Sửa các phát hiện của kiểm tra (h) (Repairing check (h) findings)

Đối soát không bao giờ sửa chữa. Các lệnh này do người vận hành chạy, và chủ sở hữu quyết định:

- Một mã về đặc quyền, schema, database hoặc đặc quyền mặc định trên `partflow_app`, `partflow_maintenance` hoặc
  `PUBLIC`: `$PF --profile ops run --rm -T db-roles apply-grants`.
- Một mã role khác `ROLE_OWNS_OBJECTS` (`ROLE_ATTRIBUTE`, `ROLE_MEMBERSHIP`, `ROLE_MISSING`), hoặc một
  `REPLICATION_ROLE_SETTING` giới hạn trong một role (`<db>/<role>` hoặc `*/<role>`):
  `$PF --profile ops run --rm -T db-roles`, rồi `$PF --profile ops run --rm -T db-roles apply-grants`.
- Một phát hiện về database role khác (`PRIVILEGE_EXCESS`, `COLUMN_PRIVILEGE`, `SCHEMA_PRIVILEGE`,
  `DATABASE_PRIVILEGE` hoặc `DEFAULT_PRIVILEGE` cho một bên được cấp không phải `PUBLIC`, `partflow_app` và
  `partflow_maintenance`, hoặc lần từ chối `foreign_grantor`): tìm ra ai đã cấp và vì sao, rồi thu hồi với tư cách
  chủ sở hữu hoặc bên cấp. `apply-grants` cố ý để nguyên nó.
- `TABLE_UNCLASSIFIED` hoặc `TABLE_MISSING` (và các lần từ chối `table_unclassified` và `table_missing`): một bảng được
  tạo hoặc xóa ngoài migration là một sự cố (§8), và chủ sở hữu quyết định. `apply-grants` và `migrate` từ chối cho
  đến khi bảng lạ được xóa (hoặc một bản phát hành phân loại nó) hoặc bảng bị thiếu được khôi phục.
- Một mã trigger (`TRIGGER_MISSING`, `TRIGGER_CHANGED`, `TRIGGER_DISABLED`, `TRIGGER_ENABLE_MODE`), một mã hàm
  (`GUARD_FUNCTION_MISSING`, `GUARD_FUNCTION_CHANGED`), `ROLE_OWNS_OBJECTS` (và lần từ chối `role_owns_objects`), một
  `REPLICATION_ROLE_SETTING` trên toàn database (`<db>/*`) hoặc `REPLICATION_ROLE_ACTIVE`: một sự cố (§8), và chủ sở
  hữu quyết định.

**Không bao giờ sửa bằng cách chạy lại migration hoặc downgrade chúng.** Một lần chạy đua với việc thay đổi quyền hoặc
trigger có thể hiện một phát hiện rồi biến mất: hãy chạy lại trước khi hành động.

### Quy tắc vận hành (Operating rules)

- Một snapshot chỉ đọc: lệnh chỉ lấy khóa bảng dùng chung (shared) và bỏ cuộc sau khi chờ một khóa 5 s (sớm hơn với
  `--statement-timeout` ngắn hơn). **Không bao giờ chạy nó cùng lúc với một migration**, và hãy chạy ngoài giờ cao
  điểm.
- Trước khi nâng cấp Python hoặc dữ liệu Unicode của nó, chạy `--check j` từ image backend mới trên database hiện tại.
  Trước khi đổi glibc hoặc image PostgreSQL, chạy diễn tập khôi phục lên máy chủ ứng viên
  (`deploy/production/restore-test.sh --backup <latest> --db-image <candidate image>`, §4), hoặc, sau khi đổi image
  tại chỗ cùng phiên bản chính, chạy `reconcile --check j` trước khi mở lại việc ghi. Một
  `COLLATION_VERSION_MISMATCH` là quyết định của chủ sở hữu, và chủ sở hữu quyết định việc chuẩn hóa lại
  (re-canonicalization) trước mọi lần nâng cấp.
- Với một Work Order được báo là `not_completed_but_fully_allocated`, `expected` là giá trị phát lại, không phải đề
  xuất sửa chữa; chủ sở hữu chọn ngày hoàn thành trong sự cố.
- Một mã thoát khác 0 là một sự cố (§8). **Không bao giờ sửa trực tiếp lịch sử hay các tổng đã lưu**; chủ sở hữu quyết
  định từng việc sửa chữa, và một phát hiện không bao giờ kích hoạt việc sửa chữa tự động.

### Đối soát theo lịch (Scheduled reconciliation)

`deploy/production/scheduled-reconcile.sh` là dạng chạy hằng ngày, khuyến nghị lúc 04:00, sau sao lưu hằng ngày, từ
thư mục mã nguồn của bản phát hành đang chạy, bằng tài khoản vận hành ([`SYNOLOGY_NAS.vi.md`](./SYNOLOGY_NAS.vi.md)
§11.5).

- Nó chạy đối soát đầy đủ và ghi báo cáo vào `~/partflow-monitoring/reconcile/<UTC timestamp>-reconcile.json`
  (`--reports-dir`; thư mục mode 0700, báo cáo mode 0600).
- Nó in `RECONCILE clean|mismatch|error|could_not_run <report>` và một dòng `FAIL <check> <title>` cho mỗi kiểm tra
  thất bại, và thoát với 0 (sạch), 1 (không khớp) hoặc 2 (không chạy được, kể cả báo cáo không đầy đủ).
- Nó từ chối chạy khi một bản phát hành đang giữ khóa của nó hoặc khóa sao lưu (mã thoát 2), dừng một lần chạy dài hơn
  `--max-runtime-minutes` (mặc định 60), và không bao giờ chạy cùng lúc với một migration.
- `last-result.txt` trong thư mục báo cáo giữ các dòng của lần chạy gần nhất. Mọi mã thoát khác 0 đều đến thông báo
  lỗi của trình lập lịch, thông báo này chỉ nêu id kiểm tra, tiêu đề và số lượng.

Một báo cáo có thể chứa giá trị badge Worker (kiểm tra (j)): giữ báo cáo như bản sao lưu, và không bao giờ đính kèm
chúng vào ticket hay email. Không công cụ nào xóa chúng; hãy xóa bằng tay sau khi xem xét (§9).

## 8. Sự cố và xử lý lỗi

### Các vấn đề thường gặp (Common problems)

| Triệu chứng | Nguyên nhân và cần làm gì |
| --- | --- |
| Mọi màn hình hiển thị OFFLINE | `backend` đang dừng hoặc không healthy: `$PF ps`, `$PF logs --tail=100 backend`. Nếu `release.sh` đã dừng việc ghi (mã thoát 3), làm theo §5 (bảng mã thoát) và §6. |
| `/api/health` trả 503 với `"schema":"mismatch"` | Revision database không khớp bản phát hành trong `.env.production`, thường sau một lần phát hành bị gián đoạn. Chạy `revision` (§2) và làm theo §5 và §6. |
| Một trang báo nó được tải từ bản phát hành cũ hơn, hoặc một lần lưu nhận 409 | Một bản phát hành mới đang chạy: tải lại trang. |
| Một lần lưu kết thúc bằng thông báo 502 hoặc 504 | Kết quả chưa rõ. Kiểm tra xem thay đổi đã được lưu chưa trước khi lặp lại (Nghi ngờ ghi trùng, mất hoặc không chắc chắn, bên dưới). |
| "Too many attempts from this computer" | Giới hạn tần suất (rate limit) đăng nhập: chờ một phút. Phía sau Cloudflare, nhiều người có thể dùng chung giới hạn ([`SYNOLOGY_PUBLIC_HTTPS.vi.md`](./SYNOLOGY_PUBLIC_HTTPS.vi.md) §13). |
| Một ảnh hoặc một lần nhập bị từ chối vì quá lớn, dù nằm trong giới hạn của PartFlow (ảnh 2 MiB, tệp nhập 1 MiB) | Reverse proxy chấp nhận request nhỏ hơn mức PartFlow cần (ít nhất 5 MiB). Trên NAS, kiểm tra quy tắc (`SYNOLOGY_NAS.vi.md` §9.2 bước 4) và đọc giới hạn đang có hiệu lực bằng `nginx -T 2>/dev/null \| grep client_max_body_size` (không có dòng nào nghĩa là mặc định 1 MiB của nginx). Nếu DSM không có thiết lập nào, giữ ảnh dưới giới hạn và ghi lại hạn chế này. |
| Trình duyệt hiển thị cảnh báo chứng chỉ | Thiết bị không tin chứng chỉ gốc, chứng chỉ đã hết hạn, hoặc địa chỉ khác với tên trong chứng chỉ ([`SYNOLOGY_NAS.vi.md`](./SYNOLOGY_NAS.vi.md) §9.1). |
| `check.sh`: `FAIL https ... (curl 60)` | NAS không tin chứng chỉ: thêm `--cacert <root certificate>` (phương án A). Phía sau Cloudflare, bỏ `--resolve-to`. |
| `check.sh`: `FAIL https host name ... not resolved (curl 6)` | Máy chủ không phân giải được tên PartFlow của chính nó: thêm `--resolve-to 127.0.0.1` (phương án A), hoặc sửa DNS. |
| `check.sh`: `FAIL backup_age` | Sao lưu hằng ngày không chạy hoặc thất bại: kiểm tra tác vụ và email của nó, rồi chạy sao lưu thủ công (§3). |
| `check.sh`: `FAIL disk_...` | Áp lực về database hoặc dung lượng lưu trữ, bên dưới. |
| `check.sh` hoặc `release.sh`: một khóa cũ hơn 4 giờ, một bản phát hành khác đang giữ khóa, hoặc `backup_lock_stale` | Một lần chạy bị kill để lại khóa của nó: §3, Khóa sao lưu và khóa phát hành. |
| `backup_running` | Một bản sao lưu, hoặc một lần phát hành đang giữ khóa sao lưu, đang chạy: chờ rồi chạy lại. |
| Email của tác vụ theo lịch ghi `HOME is not set` | Thêm `HOME=/root` ở đầu phần script trong lệnh của tác vụ ([`SYNOLOGY_NAS.vi.md`](./SYNOLOGY_NAS.vi.md) §11.5). |
| `$PF` báo `Set PARTFLOW_... in .env.production` | Giá trị đó đang rỗng trong `.env.production`. |
| Đường dẫn của một tệp bí mật là một thư mục, hoặc "is missing, empty or not a regular file" | Compose đã tạo một thư mục vì tệp bị thiếu: `rmdir` nó và tạo tệp ([`../DEPLOYMENT.vi.md`](../DEPLOYMENT.vi.md) §3.1, Secret files). |
| Một container không khởi động: `NanoCPUs can not be set` | Kernel của máy chủ không giới hạn được mức dùng bộ xử lý: đặt bốn giá trị `PARTFLOW_*_CPUS` thành `0` ([`SYNOLOGY_NAS.vi.md`](./SYNOLOGY_NAS.vi.md) §5 bước 2) rồi khởi động lại. |
| `release.sh`: the build inputs differ from the commit | `git status` liệt kê các tệp đã thay đổi hoặc chưa theo dõi trong `backend/`, `frontend/`, `deploy/production/` hoặc các tệp Compose. Không bao giờ sửa thư mục mã nguồn của bản phát hành: hoàn tác thay đổi (`git checkout -- <file>`) hoặc xóa tệp lạ, ví dụ một thư mục `@eaDir` của DSM, rồi chạy lại `release.sh`. |
| `$PF up` thất bại với `No such image` | Các image của tag trong `.env.production` không có trên máy chủ: sửa `PARTFLOW_RELEASE`, hoặc build các image ([`../DEPLOYMENT.vi.md`](../DEPLOYMENT.vi.md) §3.1, Images and release identity). |
| Không có dòng `Setup token` trong log | Thiết lập đã hoàn tất (đã có một Administrator), hoặc `backend` chưa khởi động. Khi chưa có Administrator nào, `PARTFLOW_BACKEND_WORKERS=1 $PF up -d --force-recreate backend` ghi một token mới. |
| Thiết lập lần đầu trả `setup_token_invalid` | Token đã cũ (mỗi lần khởi động lại `backend` ghi một token mới: dùng `$PF logs backend \| grep "Setup token" \| tail -n 1`), hoặc `backend` đang chạy nhiều worker: khởi động nó với một worker ([`../DEPLOYMENT.vi.md`](../DEPLOYMENT.vi.md) §3.1, Backend processes and first-run setup). |
| Không ai đăng nhập được với tư cách Administrator: quên mật khẩu hoặc login name | Xem mục Đăng nhập và tài khoản người dùng, bên dưới. |
| Không ai còn quyền quản lý các quyền sửa (correction permissions) (backend ghi một cảnh báo khi khởi động) | Xem mục Đăng nhập và tài khoản người dùng, bên dưới. |

### Đăng nhập và tài khoản người dùng (Sign-in and user accounts)

Tài khoản người dùng được quản lý ngay trong PartFlow, ở **Administration → Users**, bởi một user có role giữ quyền
**Manage users and roles**. Các lệnh trong mục này dành cho lúc không còn ai có quyền đó đăng nhập được. Chạy chúng
trên máy chủ, trong thư mục mã nguồn của bản phát hành, sau khi đã đặt `PF` (xem Trước khi chạy bất kỳ lệnh nào).

**Quên mật khẩu.**

- Khi còn một Administrator khác đăng nhập được, người đó mở **Administration → Users** và chọn **Set password…** ở
  dòng của user. User nhận một mật khẩu tạm, mọi phiên đăng nhập của user đó kết thúc, và khóa tài khoản (nếu có)
  được gỡ.
- Khi không ai đăng nhập được, ví dụ Administrator duy nhất quên mật khẩu, đặt lại mật khẩu bằng:

  ```sh
  $PF run --rm --no-deps backend python -m app.cli reset-password --login-name <login name>
  ```

  Lệnh hỏi mật khẩu mới hai lần (`New temporary password`) và không hiện những gì bạn gõ; lệnh không có `-T`, vì nó
  cần terminal cho lời hỏi đó. Mật khẩu là mật khẩu tạm: user chọn mật khẩu mới ở lần đăng nhập sau khi tùy chọn đó
  đang bật. Mọi phiên đăng nhập của user kết thúc và khóa tài khoản được gỡ. Mã thoát 0 nghĩa là xong; 1 nghĩa là bị
  từ chối và không có gì thay đổi (login name không tồn tại, hai lần gõ mật khẩu khác nhau, user chưa từng có mật
  khẩu, hoặc không còn Administrator nào); 2 nghĩa là chưa rõ kết quả hoặc không kết nối được database: hãy chạy lại,
  vì lệnh đặt lại mật khẩu dù lần trước đã thành công hay chưa. Lệnh chỉ chạy khi còn một Administrator và không bao
  giờ tạo Administrator mới.

**Quên login name.** Đọc login name của những user có quyền quản lý user và role từ database. Việc này chỉ đọc, không
thay đổi gì. Mở shell của database:

```sh
$PF exec db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
```

Dán câu truy vấn này, rồi thoát bằng `\q`. Bỏ hai dòng `JOIN role_permissions` và `WHERE` thì câu truy vấn liệt kê
mọi user.

```sql
SELECT u.login_name, u.display_name, r.name AS role, u.is_active
FROM users u
JOIN roles r ON r.id = u.role_id
JOIN role_permissions rp ON rp.role_id = u.role_id
WHERE rp.permission = 'MANAGE_USERS_AND_ROLES'
ORDER BY u.login_name;
```

Login name luôn được lưu bằng chữ thường. Dùng tên vừa tìm được để đăng nhập, hoặc để đặt lại mật khẩu như trên.

**Đổi login name.** Mở **Administration → Users**, bấm vào dòng của user, sửa **Login name** trong **Edit user** rồi
bấm **Save changes**. Login name dài 1 đến 128 ký tự, chỉ gồm chữ `a`–`z`, chữ số và `. _ @ + -`, không có khoảng
trắng; chữ hoa hay thường không quan trọng, vì nó được lưu bằng chữ thường. Tên đó không được trùng với user khác.
Mật khẩu giữ nguyên; từ đó user đăng nhập bằng tên mới. Không bao giờ đổi login name trực tiếp trong database.

**Xóa một user.** PartFlow không bao giờ xóa user, vì lịch sử vẫn tham chiếu tới user đó. Để thu hồi quyền truy cập,
mở user trong **Edit user**, tắt **Active** rồi bấm **Save changes**: mọi phiên đăng nhập của user đó kết thúc và
user không đăng nhập được nữa. Bật **Active** lại để trả quyền truy cập.

- PartFlow từ chối (`last_permission_holder`) việc đổi role hoặc trạng thái nếu việc đó làm không còn user nào đang
  hoạt động, có mật khẩu, được quản lý user và role hoặc quản lý quyền sửa; nhờ vậy không bao giờ có chuyện mọi người
  đều bị khóa ngoài.
- Với một user có role giữ một quyền sửa (correction permission) hoặc **Manage correction permissions**, việc đổi role
  hay **Active** còn cần thêm quyền **Manage correction permissions**; không có quyền đó, **Edit user** hiện role và
  trạng thái ở dạng cố định.

**Không ai còn quyền quản lý các quyền sửa.** Khi không còn user nào đang hoạt động, có mật khẩu, giữ quyền **Manage
correction permissions** (backend ghi một cảnh báo khi khởi động), trả quyền đó lại cho một role có user đang hoạt
động với mật khẩu:

```sh
$PF run --rm --no-deps backend python -m app.cli restore-correction-permission-management --role-name <role>
```

Lệnh không thay đổi gì khác, và từ chối với mã thoát 1 khi đã có người giữ quyền đó, role không tồn tại, hoặc không
có user nào đang hoạt động với mật khẩu thuộc role đó ([`README.vi.md`](../../README.vi.md) ở thư mục gốc, Restoring
the permission to manage correction permissions).

**Không còn Administrator nào.** Khi không còn user nào đang hoạt động, có mật khẩu, được quản lý user và role,
`reset-password` từ chối và PartFlow mở lại bước thiết lập lần đầu: tạo một Administrator mới bằng một mã thiết lập
mới, như [`SYNOLOGY_NAS.vi.md`](./SYNOLOGY_NAS.vi.md) §10 mô tả (với một tiến trình worker của backend, và mã mới nhất
lấy từ `$PF logs backend | grep "Setup token" | tail -n 1`).

### Nghi ngờ ghi trùng, mất hoặc không chắc chắn (Suspected duplicate, lost or uncertain write)

- Dừng quy trình bị ảnh hưởng nếu tính toàn vẹn số lượng có thể bị đe dọa.
- Giữ lại thời điểm request, Scan Station, User hoặc Worker, PN, flow và `device_event_id`.
- Trước mọi lần thử lại, theo dõi request trong log backend theo `request_id`, `device_event_id`, PN, QuantityFlow,
  Scan Station và Worker (§2), và xem kết quả cũng như lịch sử phía máy chủ.
- Chỉ thử lại với `device_event_id` gốc, và chỉ khi phù hợp.
- **Không bao giờ sửa trực tiếp lịch sử Movement.** Chỉ dùng quy trình Undo hoặc hiệu chỉnh sau khi đã biết trạng
  thái đã commit.

### Phát hiện về tính toàn vẹn của các guard (reconcile check (h)) (Guard-integrity finding (reconcile check (h)))

- Tạm dừng ghi (`$PF stop backend`, §5) khi một trigger hoặc hàm bảo vệ bị thay đổi, một trigger bị tắt, hoặc
  `session_replication_role` được đặt trên toàn database hoặc đang có hiệu lực: khi đó các guard không kích hoạt với
  bất kỳ session nào, kể cả `partflow_app`.
- Với thiết lập đó, chủ sở hữu chạy `ALTER DATABASE <db> RESET session_replication_role` bên trong `db`.
- So sánh các trigger và hàm với mã nguồn migration. Chủ sở hữu quyết định cách sửa và có cần kiểm tra lịch sử so với
  bản sao lưu gần nhất hay không.
- Trước khi mở lại việc ghi, chạy `reconcile` và phép thăm dò đặc quyền dưới đây. Nó thử một câu lệnh bị cấm bằng
  `partflow_app` và hoàn tác nó; lặp lại cho từng bảng chỉ-ghi-thêm và từng câu lệnh (`UPDATE`, `DELETE`,
  `TRUNCATE`). Một lần từ chối in `permission denied for table <table>` ra lỗi chuẩn, đó là kết quả mong đợi;
  `UPDATE 0` không kèm lỗi nghĩa là sự bảo vệ đang bị thiếu.

  ```sh
  $PF exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=0 -c BEGIN -c "SET LOCAL ROLE partflow_app" -c "UPDATE part_movements SET id = id WHERE false" -c ROLLBACK'
  ```

### Áp lực về database hoặc dung lượng lưu trữ (Database or storage pressure)

- Chặn các lần ghi mới trước khi đĩa đầy (tạm dừng ghi, §5), và giữ lại log cùng số liệu.
- Cảnh báo dung lượng là một dòng `disk_*` của `check.sh` với phần trống dưới 15 %: `disk_data` cho volume database,
  `disk_backup` cho thư mục sao lưu, `disk_docker` cho bộ nhớ Docker (image và log container), `disk_archive` cho thư
  mục lưu trữ.
- **Không xóa tệp PostgreSQL, volume, dòng Movement hay bản sao lưu để lấy chỗ.**
- Quy tắc về dung lượng: một bản sao lưu cần dung lượng trống gấp hai lần dump mới nhất cộng phần dự phòng trong thư
  mục sao lưu; một lần khôi phục theo đường 3 cần kích thước database cộng hai lần dump cộng 1 GiB trên volume
  database (§6); một khóa sao lưu cũ chặn sao lưu và phát hành cho đến khi nó được xóa (§3).
- Mở rộng dung lượng lưu trữ. Việc lưu trữ (archival) và xóa hẳn (purge) lịch sử Movement đã được lên kế hoạch nhưng
  chưa được triển khai ([`../IMPLEMENTATION_ROADMAP.vi.md`](../IMPLEMENTATION_ROADMAP.vi.md), Phase 16).
- Chạy đối soát trước khi mở lại việc ghi.

### Máy chủ hỏng (Host failure)

- Bảo đảm máy chủ hỏng không thể nhận ghi: không bao giờ được có hai bản đang hoạt động.
- Chuẩn bị một máy chủ khôi phục và khôi phục bản sao lưu đã kiểm tra mới nhất với bản phát hành tương ứng (§6,
  Khôi phục sang bản cài mới).
- Chạy đối soát và kiểm tra nhanh sau khi chuyển.
- Ghi lại khoảng dữ liệu bị mất: các lần ghi sau `dump_started_at` của bản sao lưu.
- Chỉ trỏ các máy khách sang máy chủ khôi phục sau khi sự phê duyệt của chủ sở hữu đã được ghi vào `restore.json`.

## 9. Lịch làm việc định kỳ

Các tác vụ này được khuyến nghị sau khi đưa vào sử dụng (go-live) ([`../DEPLOYMENT.vi.md`](../DEPLOYMENT.vi.md) §5);
không tác vụ nào chặn việc sử dụng. Chủ sở hữu chọn các giá trị giữ lại, lịch chạy và mức mất mát cùng thời gian khôi
phục chấp nhận được; các giá trị ở đây là ví dụ làm việc.

| Khi nào | Tác vụ |
| --- | --- |
| Mỗi 15 phút (tự động) | `check.sh`: tình trạng, các lần khởi động lại, đĩa, chứng chỉ, tuổi bản sao lưu và lỗi backend (§2). Một thất bại không đổi được nhắc lại mỗi 6 giờ. |
| Hằng ngày | Sao lưu theo lịch lúc 02:00 và đối soát theo lịch lúc 04:00 tự chạy. Đọc mọi email báo lỗi, kể cả của bản sao ngoài máy chủ; mã thoát 4 của một bản sao lưu bao gồm cả trường hợp một bản sao lưu hằng ngày không qua kiểm tra. |
| Hằng tuần | Xem xét mức tăng của database (`~/partflow-monitoring/growth.tsv`) và `docker stats --no-stream`. Xem xét các lần đăng nhập thất bại và quyền bị từ chối: `$PF logs --since 168h --no-log-prefix backend \| grep '"logger":"app.access"' \| grep -E '"status":(401\|403)'` (`refusal.type` và `refusal.code` phân biệt đăng nhập bị từ chối hoặc bị khóa, từ chối quyền, `station_device_required`, `station_device_mismatch` và `csrf_rejected`). Kiểm tra các bản cập nhật bảo mật đang chờ của máy chủ. |
| Hằng tháng | Xóa các báo cáo đối soát cũ sau khi xem xét. Xóa các bản sao lưu pre-release và manual đã hết thời gian theo dõi, và các bản sao lưu hằng ngày mà việc xoay vòng báo là không hợp lệ, sau khi xem xét (bên dưới). Cài đặt các bản cập nhật, ưu tiên thử trên hệ thống kiểm thử trước nếu có thể. Xem xét người dùng và vai trò, quy tắc tường lửa và các bí mật. Chạy `reconcile --check h`. |
| Hằng quý, và sau một thay đổi schema lớn | Diễn tập khôi phục (§4); so sánh các thời gian `restore_to_ready` và `total` của nó với thời gian nhà máy có thể làm việc mà không có PartFlow. |
| Trước khi đổi image PostgreSQL hoặc glibc của máy chủ | `restore-test.sh --backup <latest> --db-image <candidate>` (§4, §7). |
| Trước mỗi lần phát hành | Xem xét các migration, quyết định quay lui và các kiểm tra (§5); `release.sh` tạo bản sao lưu. |

**Xóa các bản sao lưu và báo cáo cũ.** Không công cụ nào xóa bản sao lưu pre-release, bản sao lưu manual, bản sao lưu
hằng ngày không qua kiểm tra, hay báo cáo đối soát. Sau khi xem xét, xóa từng cái theo tên; không bao giờ xóa bản sao
lưu hằng ngày đã kiểm tra mới nhất hoặc bất cứ thứ gì trong thư mục lưu trữ:

```sh
rm -r "<backup-dir>/<NAME>"
rm ~/partflow-monitoring/reconcile/<report file>
```

**Đổi mật khẩu.** Trước khi tạo lại `backend`, chạy
`deploy/production/check.sh --url https://<partflow-host> --only errors --no-state` (tạo lại `backend` sẽ xóa log của
nó, §2).

- **Mật khẩu của một role** (`partflow_app_password` hoặc `partflow_maintenance_password`), trong một khoảng tạm dừng
  ghi ngắn, khi không có lần nhập nào đang chạy. Thay tệp (quy tắc nằm trong
  [`../DEPLOYMENT.vi.md`](../DEPLOYMENT.vi.md) §3.1, Secret files), rồi để `db-roles` đặt mật khẩu mới và tạo lại
  `backend`:

  ```sh
  openssl rand -base64 32 > "<secrets-dir>/partflow_app_password.new"
  mv -f "<secrets-dir>/partflow_app_password.new" "<secrets-dir>/partflow_app_password"
  chmod 0444 "<secrets-dir>/partflow_app_password"
  $PF stop backend
  $PF --profile ops run --rm -T db-roles
  $PF up -d --force-recreate --no-deps backend
  ```

  Sau đó kiểm tra health (§2). Lệnh `up -d backend` thông thường không nhận mật khẩu mới, vì container không được
  tạo lại, và chạy `db-roles` trong khi `backend` đang phục vụ làm các kết nối mới của nó thất bại. Với
  `partflow_maintenance_password`, thay tệp đó và chỉ chạy lệnh `db-roles`: hiện chưa có dịch vụ nào dùng role đó, nên
  không cần tạm dừng ghi.
- **Mật khẩu chủ sở hữu** (`postgres_password`): đổi trong database trước, rồi thay tệp. Không cần khởi động lại,
  vì chỉ `migrate` và `db-roles` chạy một lần đọc nó.

  ```sh
  openssl rand -base64 32 > "<secrets-dir>/postgres_password.new"
  cat "<secrets-dir>/postgres_password.new"        # copy this value
  $PF exec db psql -U <POSTGRES_USER> -d <POSTGRES_DB>
  ```

  Trong `psql`, gõ `\password <POSTGRES_USER>`, dán giá trị đã sao chép hai lần, rồi `\q`. Cuối cùng:

  ```sh
  mv -f "<secrets-dir>/postgres_password.new" "<secrets-dir>/postgres_password"
  chmod 0444 "<secrets-dir>/postgres_password"
  ```

## Lịch sử thay đổi

| Ngày | Commit | Thay đổi |
| --- | --- | --- |
| 2026-11-12 | uncommitted | Thêm mục đăng nhập và tài khoản người dùng: lấy lại mật khẩu hoặc login name đã quên, đổi tên đăng nhập, thu hồi quyền truy cập của một user, và cách khôi phục khi không còn Administrator nào. |
| 2026-11-10 | `e0e5056` | Viết lại cho người vận hành mới: mục lục tác vụ và thiết lập mỗi phiên ngay từ đầu, bảng các vấn đề thường gặp, các đường quay lui 1 và 2 từng bước kèm việc chuyển checkout, việc chuyển sang máy chủ khác (chuyển về đây từ hướng dẫn NAS), và phần tham chiếu chi tiết ngắn gọn hơn; số mục và các script của path 3 không đổi. |
| 2026-11-09 | `b405c6e` | Viết lại thành nội dung hiện hành cho người đọc mới; bỏ các cổng pilot và phát hành theo quyết định của chủ sở hữu. |
| 2026-10-09 | `4271f61` | Thêm các tìm kiếm log theo Work Order Demand và kiểm tra lỗi cần chạy trước khi tạo lại `backend`. |
| 2026-10-09 | `03bd90c` | Thêm giám sát: log có cấu trúc kèm request id, lệnh `status`, `check.sh` và đối soát theo lịch. |
| 2026-10-08 | `92727f6` | Làm cho các script path 3 của quay lui dừng ở mọi guard thất bại và chỉ mở lại `web` sau khi `backend` đã sẵn sàng. |
| 2026-10-08 | `a330383` | Thêm sao lưu production, diễn tập khôi phục cô lập, bản sao lưu trước phát hành trong thời gian tạm dừng ghi và quay lui path 3. |
| 2026-10-08 | `1116b07` | Thêm các database role, quyền được áp dụng bởi mọi migration và cách sửa cho reconcile check (h). |
| 2026-10-08 | `b820e7a` | Thêm quy trình phát hành với `release.sh`, tạm dừng ghi và các đường quay lui 1 và 2. |
| 2026-10-08 | `d7e86a9` | Chuyển các thủ tục sang dùng Compose stack production. |
| 2026-10-07 | `3fb66df` | Thêm lệnh đối soát chỉ đọc và các kiểm tra của nó. |
| 2026-09-02 | `d2f68de` | Phiên bản đầu tiên, kèm bản dịch tiếng Việt. |
