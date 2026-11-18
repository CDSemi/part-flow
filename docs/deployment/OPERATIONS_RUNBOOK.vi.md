# Runbook vận hành PartFlow

> **Bản gốc:** [`OPERATIONS_RUNBOOK.md`](./OPERATIONS_RUNBOOK.md). Đồng bộ với bản tiếng Anh tại commit `1874636` cộng các thay đổi
> chưa commit ngày 2026-11-18. Bản tiếng Anh là nguồn chuẩn (source of truth); khi hai bản khác nhau, bản tiếng Anh có
> hiệu lực.

Runbook này gồm các thủ tục vận hành PartFlow production sau khi đã cài đặt: kiểm tra tình trạng, sao lưu và thử
khôi phục, cài bản phát hành (release) mới, quay lui (rollback), chạy đối soát (reconciliation), xử lý sự cố, lịch
làm việc định kỳ và việc lưu trữ lịch sử Movement. Tài liệu viết cho người vận hành (operator), hiện cũng là chủ sở hữu (owner). Việc cài đặt nằm
trong [`SYNOLOGY_NAS.vi.md`](./SYNOLOGY_NAS.vi.md) (hoặc [`VPS.vi.md`](./VPS.vi.md)); mọi thiết lập và giới hạn của
hệ thống được mô tả trong [`../DEPLOYMENT.vi.md`](../DEPLOYMENT.vi.md) §3.1.

## Tìm tác vụ của bạn

| Tôi muốn … | Mục |
| --- | --- |
| Kiểm tra PartFlow có khỏe không, hoặc tìm nguyên nhân của một lỗi | §2 |
| Sao lưu thủ công, kiểm tra một bản sao lưu, hiểu thư mục sao lưu hoặc tác vụ sao lưu mỗi giờ | §3 |
| Chứng minh rằng một bản sao lưu khôi phục được | §4 |
| Cài một bản phát hành mới | §5 |
| Hoàn tác một bản phát hành | §6 |
| Khôi phục sau khi máy chủ hỏng, hoặc chuyển PartFlow sang máy chủ khác | §6 (Khôi phục sang bản cài mới, Chuyển sang máy chủ khác) |
| Chạy hoặc đọc kết quả đối soát | §7 |
| Giải quyết vấn đề: các triệu chứng và sự cố thường gặp | §8 |
| Biết việc cần làm hằng ngày, hằng tuần, hằng tháng; đổi mật khẩu; xóa bản sao lưu cũ | §9 |
| Lấy lại mật khẩu hoặc login name đã quên, đổi tên đăng nhập của một user, hoặc thu hồi quyền truy cập của một user | §8 (Đăng nhập và tài khoản người dùng) |
| Tìm xem một bản phát hành trước đây đã làm gì | §1 |
| Dùng lệnh ngắn `pf` để cài đặt, cập nhật, sao lưu, khôi phục hoặc xem trạng thái | Trước khi chạy bất kỳ lệnh nào |
| Xóa các Work Order và Part Number chỉ được nhập để thử nghiệm (một yêu cầu xóa vĩnh viễn) | §10 |
| Hiểu job lưu trữ lịch sử, các lần lưu trữ tự động của nó, các bản sao lưu trước purge, việc purge của nó và cách nó thực hiện một yêu cầu xóa vĩnh viễn, phê duyệt hoặc bác bỏ một lần lưu trữ được đề xuất, kiểm tra hoặc xác nhận một lần lưu trữ, xử lý một lần lưu trữ thất bại, kiểm tra một bản lưu trữ, hoặc lấy lại dung lượng đĩa sau một lần purge | §11 |
| Xử lý một phát hiện của đối soát sau một lần purge lịch sử Movement (`archive.sh` thoát với mã 4) | §8 (Phát hiện của đối soát sau một lần purge) |
| Xử lý một phát hiện của đối soát sau một lần xóa vĩnh viễn (`archive.sh` thoát với mã 4) | §8 (Phát hiện của đối soát sau một lần xóa) |

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

**Lệnh ngắn `pf`.** `deploy/production/pf` chạy mỗi quy trình đã mô tả bằng một lệnh, theo đúng thứ tự, và dừng ở
bước đầu tiên thất bại; nó không thêm quy trình nào của riêng nó. `deploy/production/pf --help` in ra các tùy chọn của
nó.

| Lệnh | Những gì nó chạy |
| --- | --- |
| `pf install` | Lần cài đặt đầu tiên của bản phát hành đã checkout trên một database trống: [`SYNOLOGY_NAS.vi.md`](./SYNOLOGY_NAS.vi.md) §7 và §8. |
| `pf update <tag>` | `git fetch --tags`, `git checkout <tag>`, rồi `release.sh` (§5); người phê duyệt mặc định là người vận hành. |
| `pf backup [--reason TEXT]` | Một bản sao lưu thủ công (§3). |
| `pf restore <NAME> --approved-by <owner>` | Khôi phục vào instance mới (new-instance restore) của §6 vào một database trống, với sự phê duyệt của chủ sở hữu được ghi vào `restore.json` trước khi `backend` khởi động. |
| `pf status [--url https://<partflow-host>]` | Các container, `/api/health` và tóm tắt của `status` (§2); với `--url`, `check.sh --no-state`. |
| `pf delete-permanently --work-order <number> --part-number <PN> --reason TEXT [--confirm]` | Bản xem trước việc xóa các Work Order và Part Number chỉ nhập để thử nghiệm; với `--confirm`, một yêu cầu xóa vĩnh viễn, được job lưu trữ lịch sử thực hiện ngay (bản sao lưu trước purge của nó, việc xóa trong một transaction và đối soát), và trạng thái cuối cùng của yêu cầu (§10). Nó cần job lưu trữ lịch sử đã được thiết lập. |
| `pf archive` | Chạy ngay job lưu trữ lịch sử, tức `archive.sh` không có `--scheduled` (§11): trước hết nó đánh giá các lần lưu trữ tự động (nó tạo một lần khi chúng đang bật và đến hạn), rồi thực hiện một yêu cầu xóa vĩnh viễn đang mở (bản sao lưu trước purge của yêu cầu và việc xóa), hoặc làm phần còn lại của phần việc hằng giờ: xuất, kiểm tra và sao chép một lần lưu trữ đã được phê duyệt, tạo bản sao lưu trước purge của lần đó và **purge** (xóa hẳn) lịch sử Movement đã lưu trữ khỏi database; rồi chạy đối soát. |

Quy ước: tác vụ chạy một lần (one-shot job) đọc hoặc ghi thư mục sao lưu chạy với `--user "$(id -u):$(id -g)"`, vì
thư mục đó có mode 0700. `<partflow-host>` là tên miền truy cập (host name) mà mọi người gõ trên trình duyệt,
`<backup-dir>` là giá trị của `PARTFLOW_BACKUP_DIR`, `<archive-dir>` là giá trị của `PARTFLOW_ARCHIVE_DIR`,
`<secrets-dir>` là giá trị của `PARTFLOW_SECRETS_DIR`,
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
| `archive/<UTC>/` | `archive.sh`, chỉ cho một lần khởi động đã làm gì đó hoặc thất bại (§11) | một báo cáo JSON và một log cho mỗi bước (`recover`; `evaluate`, việc đánh giá các lần lưu trữ tự động; `next`, rồi `next-2`, `next-3` và tiếp theo; `export`, `verify`, `copy`, `purge`, `start-deletion`, `delete`, `status`); `check.json`, lần kiểm tra đã tìm thấy một yêu cầu xóa vĩnh viễn; `backup.out` và `backup.log` của bản sao lưu trước purge; `reconcile.json` và `reconcile.log`, đối soát sau một lần purge hoặc một lần xóa; và `messages.log` |
| `archive/hourly-slot` | `archive.sh --scheduled` (§11) | dấu mốc hằng giờ: phút của lần làm việc hằng giờ gần nhất đã được thử |
| `archive/reconcile-after-purge` | `archive.sh` (§11) | bản ghi sự cố của một lần đối soát sau một lần purge hoặc một lần xóa vĩnh viễn chưa kết thúc sạch: lần lưu trữ hoặc yêu cầu xóa, kết quả, các kiểm tra thất bại và đường dẫn của báo cáo. Khi nó còn tồn tại, `archive.sh` không làm việc với lần lưu trữ nào và không thực hiện yêu cầu xóa nào; bạn xóa nó khi đóng sự cố (§8, Phát hiện của đối soát sau một lần purge, hoặc sau một lần xóa) |

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
| `disk_archive` | thư mục lưu trữ (archive, `PARTFLOW_ARCHIVE_DIR`) còn trống dưới 15 %; `SKIP` khi việc lưu trữ chưa được thiết lập (giá trị đang trống) |
| `database` | báo cáo `status` không chạy được, không trả lời trong 120 s, hoặc báo lỗi database |
| `schema` | `status` báo schema chưa sẵn sàng |
| `backup_age` | không tìm thấy bản sao lưu đã công bố, không đọc được thư mục sao lưu, hoặc bản sao lưu mới nhất thuộc bất kỳ loại nào (kể cả bản sao lưu trước purge) hoàn tất cách đây lâu hơn khoảng thời gian sao lưu theo lịch của Administration → Backups cộng 2 giờ (ban đầu là 3 giờ; `--max-backup-age-hours` đặt giới hạn khác) |
| `archival_proposal` | `history-archive status` chỉ đọc báo một phát hiện, được nêu tên trong dòng: `copy_confirmation_overdue`, `approval_overdue`, `last_run_failed` (thay vào đó là `authorization_failed`, với một lần lưu trữ đã thất bại vì nó không còn chạy được theo thẩm quyền của nó), `standing_authorization_invalid`, `archive_dir_unusable`, `copy_dir_unusable` hoặc `receipt_not_copied` (§11); một yêu cầu xóa vĩnh viễn vẫn còn mở (`APPROVED` hoặc `RUNNING`) hơn 15 phút sau khi được yêu cầu (`--deletion-wait-minutes`), tức tác vụ lưu trữ lịch sử chưa thực hiện hoặc chưa hoàn tất nó: `deletion_request_waiting` (§10), dòng này nêu tên lần lưu trữ mà một yêu cầu `APPROVED` đang chờ phía sau; hoặc đối soát sau một lần purge hoặc một lần xóa chưa kết thúc sạch, được nêu đầu tiên là `reconcile_after_purge` (§8); `SKIP` khi việc lưu trữ chưa được thiết lập, trừ `deletion_request_waiting`: PartFlow vẫn ghi lại các yêu cầu xóa, và khi đó không có gì thực hiện chúng |

- Các dòng `database`, `schema` và `backup_age` đến từ một lần chạy `status` chỉ đọc, còn `archival_proposal` đến từ một
  lần chạy `history-archive status` chỉ đọc trong dịch vụ `maintenance` (§11) và từ bản ghi sự cố
  `<records-dir>/archive/reconcile-after-purge` của `archive.sh`, mà `check.sh` chỉ đọc. Khi việc lưu trữ chưa được
  thiết lập, `archival_proposal` thay vào đó đến từ kiểm tra nhẹ của `archive.sh`: `history-archive settings` bằng
  `docker compose exec` trong container `backend` đang chạy (tối đa 30 giây), cho biết có yêu cầu xóa nào đang mở không
  và nó được yêu cầu lúc nào. Khi một bản phát hành đang giữ khóa của nó, hoặc `release.sh` đang giữ khóa sao lưu, bốn
  dòng này là `SKIP`. Khi khóa như vậy cũ hơn 4 giờ, lần chạy báo `FAIL database` và nêu tên khóa, vì một `release.sh`
  bị kill sẽ để lại khóa (§3, Khóa sao lưu và khóa phát hành).
- Mã thoát (exit code): 0 không có gì để báo, 1 có ít nhất một `FAIL`, 2 không chạy được kiểm tra; cả 1 lẫn 2 đều
  kích hoạt thông báo lỗi của trình lập lịch. Một tập thất bại không đổi chỉ được báo lại sau 6 giờ
  (`--renotify-hours`); khi đó đầu ra ghi `already reported`.
- `check.sh` giữ trạng thái của nó trong `~/partflow-monitoring` (mode 0700), gồm `last-check.txt` (các dòng của lần
  chạy gần nhất), `growth.tsv` (kích thước database mỗi ngày một lần), `archive-status.json` (báo cáo
  `history-archive status` gần nhất) và `archive-settings.json` (báo cáo `history-archive settings` gần nhất, khi việc
  lưu trữ chưa được thiết lập).
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
là khoảng 9 ngày log backend và 18 ngày log `web`. Các lần Production Board tự làm mới thành công không được ghi log,
nhưng mỗi Area Board hoặc màn hình Tracking đang mở thêm các bản ghi `web` mỗi 15 giây, nên khi mở nhiều màn hình như
vậy, log `web` bao phủ ít ngày hơn. **Hãy sao chép những gì một cuộc điều tra cần ngay khi bắt đầu.**

### Lỗi quyền database (Database permission errors)

Backend kết nối bằng database role `partflow_app`. Lỗi `permission denied` (SQLSTATE 42501) trong log của nó nghĩa
là một đường mã đã cố thay đổi lịch sử được bảo vệ, hoặc thiếu một quyền (grant). Đó là một sự cố (§8), không bao giờ
là lý do để cấp thêm quyền. Chạy `reconcile --check h` (§7) trước mọi việc khác.

## 3. Sao lưu

### Bản sao lưu là gì (What a backup is)

`deploy/production/backup.sh` tạo bản sao lưu logic của database PartFlow trong khi `db` đang chạy. Nó chạy từ thư mục
gốc của một thư mục mã nguồn bản phát hành: của bản phát hành đang chạy với sao lưu theo lịch, thủ công và trước purge,
của bản phát hành mới khi `release.sh` gọi nó. Một bản sao lưu là một thư mục `<backup-dir>/<NAME>/`:

```text
NAME = <UTC stamp>-<kind>[-<label>]      for example 20261008T020000Z-scheduled,
                                         20261008T140000Z-pre-release-v1.0.0-rc.2,
                                         20271110T050102Z-pre-purge-run-3-20271110T042000Z
                                         and 20271110T050132Z-pre-purge-deletion-7-20271110T050130Z
```

- `kind` là `scheduled` (tác vụ sao lưu chạy mỗi giờ), `manual` (làm bằng tay, kèm lý do), `pre-release` (do
  `release.sh` tạo, với tag phát hành đích làm `label`) hoặc `pre-purge` (do `archive.sh` tạo ngay trước khi purge một
  lần lưu trữ, với tên bản lưu trữ `run-<id>-<giờ UTC>` của lần đó làm `label`, hoặc ngay trước lần xóa của một yêu cầu
  xóa vĩnh viễn, với nhãn `deletion-<id>-<giờ UTC>` của yêu cầu, §11). `daily` là loại sao lưu theo lịch của các bản
  phát hành trước: các bản sao lưu đó vẫn được kiểm tra, khôi phục và xoay vòng như bản sao lưu theo lịch. Mọi loại đều
  được kiểm tra và khôi phục theo cùng một cách.
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

Khi thành công, đầu ra chuẩn (standard output) đúng một dòng, `BACKUP <NAME> <absolute path>`; một bản sao lưu trước
purge thêm giá trị thứ ba, thời điểm bản dump của nó bắt đầu (`BACKUP <NAME> <absolute path> <YYYY-MM-DDTHH:MM:SSZ>`,
đọc từ báo cáo kiểm tra của nó), mà bước purge hoặc bước xóa ghi lại. Các dòng tiến trình như
`backup: dump ok (1234 ms)` đi ra lỗi chuẩn (standard error). Tác vụ sao lưu chạy
`deploy/production/backup.sh --kind scheduled --operator scheduler` mỗi giờ
([`SYNOLOGY_NAS.vi.md`](./SYNOLOGY_NAS.vi.md) §11.2); mục Sao lưu theo lịch (Scheduled backups), bên dưới, giải thích
nó. `release.sh` tự tạo bản sao lưu trước phát hành; dạng thủ công của nó, cho một lần phát hành bằng tay, là
`deploy/production/backup.sh --kind pre-release --label <target tag> --tools-release <target tag> --operator "<your name>"`.
`archive.sh` tự tạo bản sao lưu trước purge (§11, Bản sao lưu trước purge).

| Tùy chọn | Ý nghĩa |
| --- | --- |
| `--kind scheduled\|manual\|pre-release\|pre-purge`, `--operator NAME` | Bắt buộc. `--kind daily`, `--keep-daily` và `--keep-weekly` của các bản phát hành trước bị từ chối kèm lệnh cần dùng thay thế. |
| `--reason TEXT` | Bắt buộc với `manual`. Mặc định của `pre-purge` là `pre-purge backup for <archive name>`, hoặc `pre-purge backup for deletion request <id>`. |
| `--label TAG` | Tag phát hành đích với `pre-release`; với `pre-purge`, tên bản lưu trữ của lần lưu trữ, `run-<id>-<giờ UTC>`, hoặc nhãn của yêu cầu xóa, `deletion-<id>-<giờ UTC>`. Bắt buộc với hai loại này, bị từ chối với loại khác. |
| `--tools-release TAG` | Bản phát hành có image `backup-tools` ghi và kiểm tra manifest (mặc định: `PARTFLOW_RELEASE`). |
| `--no-rotate` | Chỉ với `scheduled`: giữ mọi bản sao lưu theo lịch và trước purge trong lần chạy này. |
| `--reserve-mib N` | Dung lượng trống giữ lại ngoài hai lần dump mới nhất (mặc định 1024). |
| `--lock-wait-minutes N` | Khi một bản sao lưu khác đang giữ khóa sao lưu, chờ nó tối đa N phút, kiểm tra lại mỗi 15 giây (0 đến 1440; mặc định 0, bị từ chối ngay). Một khóa cũ (stale) bị từ chối ngay. `archive.sh` truyền vào đây tùy chọn `--backup-wait-minutes` của chính nó (mặc định 60). |

Giá trị văn bản dài 1 đến 500 ký tự, không có ký tự điều khiển, `"` hoặc `\`. Trước khi ghi, `backup.sh` yêu cầu dung
lượng trống bằng hai lần dump mới nhất thuộc bất kỳ loại nào (hoặc kích thước database khi chưa có dump nào) cộng với
phần dự phòng.

| Mã thoát | Ý nghĩa | Cần làm gì |
| --- | --- | --- |
| 0 | Hoàn tất và đã kiểm tra, hoặc (`scheduled`) chưa có bản sao lưu nào đến hạn: khi đó đầu ra chuẩn là `NOT_DUE <NAME>`, bản sao lưu theo lịch mới nhất. | Không cần làm gì. |
| 1 | Bị từ chối trước khi ghi: `backup_running` (kể cả khi bản kia vẫn chạy sau `--lock-wait-minutes`), `backup_lock_stale`, `insufficient_space` hoặc `name_exists`. | Đọc lý do; chưa có gì được ghi. |
| 2 | Không chạy được: tùy chọn, công cụ, cấu hình, `db` không chạy, không tạo được khóa sao lưu, hoặc (`scheduled`) không đọc được chính sách sao lưu từ database. | Sửa nguyên nhân và chạy lại. |
| 3 | Thất bại: không có bản sao lưu nào được công bố, bản được công bố không qua kiểm tra, hoặc (`pre-purge`) báo cáo kiểm tra của nó không nêu thời điểm bắt đầu dump. | Không bao giờ dùng bản sao lưu mà thông báo nêu tên; xóa nó sau khi xem xét. |
| 4 | Bản sao lưu đã hoàn tất và được kiểm tra, nhưng việc xoay vòng (rotation) thất bại hoặc phát hiện một bản sao lưu không qua kiểm tra. | Đọc báo cáo xoay vòng trên lỗi chuẩn (Retention, bên dưới). |

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

### Sao lưu theo lịch (Scheduled backups)

Tác vụ sao lưu chạy `backup.sh --kind scheduled` mỗi giờ ([`SYNOLOGY_NAS.vi.md`](./SYNOLOGY_NAS.vi.md) §11.2,
[`VPS.vi.md`](./VPS.vi.md) §8). **Administration → Backups** trong PartFlow đặt tần suất tạo bản sao lưu và số bản sao
lưu theo lịch và trước purge được giữ; một thay đổi có hiệu lực từ lần chạy kế tiếp của tác vụ. Mỗi lần chạy:

1. lấy khóa sao lưu, rồi đọc chính sách sao lưu từ database (`python -m app.cli backup-policy` trong một container
   `backend` dùng một lần, với role ứng dụng); khi không đọc được chính sách, nó thoát với 2;
2. hỏi `backup-due` xem có bản sao lưu nào đến hạn không: khi bản sao lưu theo lịch mới nhất bắt đầu cách đây chưa tới
   khoảng thời gian đã đặt (trừ mười phút, để một tác vụ mỗi giờ không bao giờ bỏ qua một giờ), nó không ghi gì, in
   `NOT_DUE <NAME>` và thoát với 0;
3. nếu không thì tạo và kiểm tra bản sao lưu, rồi áp dụng chính sách giữ lại (bên dưới).

| Administration → Backups | Giá trị ban đầu | Cho phép |
| --- | --- | --- |
| Scheduled backup | Every hour | mỗi 1, 2, 3, 4, 6, 8, 12 hoặc 24 giờ |
| Hourly backups kept | 48 | 0 đến 720 |
| Daily backups kept | 30 | 1 đến 3650 |
| Weekly backups kept | 52 | 0 đến 520 |
| Pre-purge backups kept | 3 | 1 đến 100 |

Việc thay đổi các giá trị này cần permission Configure system settings, và mọi thay đổi đều được audit. Khi không đọc
được chính sách, hoặc chính sách thiếu một trong các giá trị này, lần chạy thoát với 2 và không ghi gì.

### Giữ lại, bản sao ngoài máy chủ và cảnh báo (Retention, the off-host copy and alerts)

Một bản sao lưu theo lịch kết thúc bằng việc xoay vòng các bản sao lưu theo lịch và trước purge, với các giá trị của
Administration → Backups. Để xem trước mà không xóa gì (ở đây với các giá trị ban đầu):

```sh
$PF --profile ops run --rm -T --user "$(id -u):$(id -g)" backup-tools backup-rotate --keep-hourly 48 --keep-daily 30 --keep-weekly 52 --keep-pre-purge 3 --dry-run
```

- Nó giữ bản sao lưu theo lịch đã kiểm tra mới nhất của mỗi giờ trong `--keep-hourly` giờ, mỗi ngày trong
  `--keep-daily` ngày và mỗi tuần lịch trong `--keep-weekly` tuần lịch mới nhất có một bản (giờ, ngày và tuần ISO theo
  UTC; các khoảng chồng lên nhau), nên bản sao lưu theo lịch mới nhất luôn được giữ. Các bản sao lưu hằng ngày của các
  bản phát hành trước được tính là bản sao lưu theo lịch.
- Trong các bản sao lưu trước purge đã kiểm tra, của các lần lưu trữ và của các yêu cầu xóa vĩnh viễn gộp chung, nó giữ
  `--keep-pre-purge` bản mới nhất và xóa các bản cũ hơn; hai chính sách giữ lại không bao giờ tính bản sao lưu của
  nhau. Một bản sao lưu trước purge không bao giờ bị xoay vòng bởi chính lần chạy của nó. Với một image từ trước khi có
  purge, mà chính sách sao lưu không có giá trị cho bản sao lưu trước purge, việc xoay vòng để nguyên mọi bản sao lưu
  trước purge.
- **Các bản sao lưu trước purge của các lần xóa và của các lần purge dùng chung Pre-purge backups kept.** Các lần xóa
  thường diễn ra nhiều hơn các lần purge rất nhiều: với giá trị ban đầu là 3, ba lần xóa sau một lần purge sẽ xoay vòng
  bản sao lưu trước purge của lần purge đó ở bản sao lưu theo lịch kế tiếp. Nếu bản sao lưu đó còn quan trọng (một lần
  purge mà bạn có thể vẫn muốn hoàn tác), hãy tăng Pre-purge backups kept trong Administration → Backups trước một đợt
  xóa dữ liệu thử nghiệm.
- Nó kiểm tra mọi ứng viên trước. Một bản sao lưu không qua kiểm tra không được tính và **không bao giờ bị
  xóa**: `backup.sh` thoát với 4 và bạn xem xét nó (§9).
- **Bản sao lưu pre-release và manual không bao giờ bị xoay vòng**: bạn xóa chúng sau khi xem xét (§9). Không công cụ
  nào từng xoay vòng hay xóa một thư mục lưu trữ.
- Phần còn sót trong `.partial/` cũ hơn 24 giờ bị xóa.
- Với các giá trị ban đầu, thư mục sao lưu chứa tối đa 130 bản sao lưu theo lịch (48 + 30 + 52, ít hơn khi các khoảng
  chồng lên nhau) và 3 bản sao lưu trước purge. Hãy dự trù dung lượng cho chừng ấy bản dump, cộng các bản sao lưu
  pre-release và manual.

Công cụ sao lưu của nền tảng mã hóa thư mục sao lưu và sao chép nó ra ngoài máy chủ, không bao giờ là bản sao tự
làm: Hyper Backup với mã hóa phía máy khách trên NAS ([`SYNOLOGY_NAS.vi.md`](./SYNOLOGY_NAS.vi.md) §11.3), restic
lên object storage trên VPS ([`VPS.vi.md`](./VPS.vi.md) §8).

Cảnh báo, sau khi các lịch đã được thiết lập: sao lưu theo lịch thất bại (mã thoát khác 0) sẽ kích hoạt thông báo lỗi
của trình lập lịch; sao chép ngoài máy chủ thất bại sẽ kích hoạt thông báo riêng của công cụ sao lưu; và kiểm tra
`backup_age` của `check.sh` thất bại khi bản sao lưu mới nhất cũ hơn khoảng thời gian của Administration → Backups
cộng 2 giờ (§2). Với một bản sao lưu mỗi giờ, tối đa một giờ công việc có thể bị mất (mục tiêu điểm khôi phục, recovery
point objective, RPO); sau khi mất máy chủ, là mọi thứ kể từ lần sao chép ngoài máy chủ cuối cùng. Cho đến khi tác
vụ sao lưu được lên lịch, các bản sao lưu duy nhất là những bản `release.sh` tạo trước mỗi lần phát hành và mọi bản sao
lưu thủ công.

### Khóa sao lưu và khóa phát hành (Backup lock and release lock)

Các bản sao lưu không bao giờ chạy cùng lúc. Thư mục `<backup-dir>/.backup.lock` là khóa; tệp `owner` của nó chứa
`host`, `pid`, `started_at`, `by` (`backup.sh` hoặc `release.sh`) và `name` của bản sao lưu hoặc `release`. Lần chạy
thứ hai bị từ chối, không bao giờ xếp hàng: `backup_running` nghĩa là một bản sao lưu, hoặc một lần phát hành đang
giữ khóa, đang diễn ra, nên hãy chờ. Ngoại lệ duy nhất là một bản sao lưu trước purge của `archive.sh` (trước một lần
purge hoặc một lần xóa), bản này chờ khóa (`--lock-wait-minutes`, §11); khi đó không lần phát hành nào có thể giữ khóa,
vì `release.sh` từ chối khởi động khi `archive.sh` đang làm việc. Một bản sao lưu theo lịch bắt đầu trong lúc bản sao
lưu trước purge đang chạy bị từ chối như trong lúc phát hành (một email báo lỗi; lần chạy kế tiếp sao lưu như bình
thường). `release.sh` giữ khóa từ lúc tạm dừng ghi (hoặc từ bản sao lưu của nó) cho đến khi `backend` mới chạy. **Hãy
bắt đầu một lần phát hành ngay sau một bản sao lưu theo lịch:** một bản sao lưu đang chạy sẽ chặn lần phát hành trước
khi tạm dừng ghi mà chưa thay đổi gì (hãy bắt đầu lại khi bản sao lưu đã xong), và một bản sao lưu theo lịch bắt đầu
trong lúc phát hành sẽ bị từ chối (mã thoát 1, kèm email báo lỗi của nó; lần phát hành tự tạo bản sao lưu của mình, và
lần chạy kế tiếp sao lưu như bình thường). Một khóa hoàn toàn không tạo được (thư mục sao lưu đầy, chỉ đọc hoặc hết
inode) được báo là không chạy được (mã thoát 2), không bao giờ là `backup_running`.

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
- Chọn thời điểm không có lần nhập Work Order nào đang chạy, và bắt đầu ngay sau khi một bản sao lưu theo lịch đã xong
  (tác vụ sao lưu bắt đầu vào mỗi giờ tròn trên NAS).
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
   commit này. Khi `revision` báo `blocked_by_data`, lần phát hành dừng ở đây và không thay đổi gì: `blocking_data`
   trong `candidate-revision.json` liệt kê các bản ghi đã lưu mà bản phát hành mới chưa chấp nhận được (ví dụ hai Area
   cùng tên trong một Department). Sửa chúng trong PartFlow, rồi chạy lại lần phát hành.
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
- Các trang đang mở trong lúc chuyển hiển thị thông báo cập nhật và tự tải lại
  ([`../GUI_DESIGN.vi.md`](../GUI_DESIGN.vi.md) §3 rule 13): Scan Station hoặc Production Board sau 10 giây đếm ngược
  trong thông báo, mọi trang khác khi không ai dùng nó trong 1 phút và trên trang không còn gì chưa lưu (thông báo đếm
  ngược 10 giây cuối của phút đó), và không bao giờ khi đang có hộp thoại mở. Không ai phải đi tới từng máy; người đang
  làm việc trên một trang có thể bấm `Reload page` sớm hơn.

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

**Việc khôi phục và job lưu trữ lịch sử (Restores and the history archive job).** Trước khi khôi phục một bản sao lưu
vào production (đường 3, việc khôi phục sang bản cài mới bên dưới, hoặc §8 Phát hiện của đối soát sau một lần purge
hoặc sau một lần xóa), hãy tắt tác vụ lưu trữ lịch sử trong trình lập lịch của máy chủ (trên NAS: bỏ chọn **Enabled**
trong Task Scheduler), và chỉ bật lại sau khi bạn đã kiểm tra các lần lưu trữ và các yêu cầu xóa vĩnh viễn trong
Administration → History archival & purge. Một database đã khôi phục giữ mỗi lần lưu trữ và mỗi yêu cầu xóa ở trạng
thái của nó lúc dump của bản sao lưu bắt đầu:

- Một lần lưu trữ ở `RUNNING` sẽ bị lần khởi động kế tiếp của job chuyển sang thất bại, và không có gì bị purge lại.
  Vì vậy một lần purge được hoàn tác bằng cách khôi phục bản sao lưu trước purge của lần lưu trữ đó (§8, Phát hiện
  của đối soát sau một lần purge), không bao giờ bằng một bản sao lưu muộn hơn.
- Một lần lưu trữ ở `PURGING` được tiếp tục purge ở lần khởi động kế tiếp, trừ khi tác vụ bị tắt hoặc bản ghi sự cố
  `<records-dir>/archive/reconcile-after-purge` còn tồn tại (§8). Một bản sao lưu giữ một lần lưu trữ ở `PURGING` khi
  nó được tạo trong lúc purge (bản sao lưu mỗi giờ chạy khi một lần purge dài đang làm việc) hoặc khi một lần purge
  đã dừng đang chờ được tiếp tục.
- Một yêu cầu xóa ở `RUNNING` sẽ bị lần khởi động kế tiếp chuyển sang thất bại, và không có gì bị xóa lại: chính bản
  sao lưu trước purge của một lần xóa giữ yêu cầu của nó ở `RUNNING` (§8, Phát hiện của đối soát sau một lần xóa).
- Một yêu cầu xóa ở `APPROVED` sẽ được lần khởi động kế tiếp thực hiện, dù việc xóa của nó có thể đã xảy ra trong
  database mà lần khôi phục đã thay thế, và không ai được hỏi lại: một bản sao lưu giữ một yêu cầu ở `APPROVED` khi nó
  được tạo giữa lúc yêu cầu và lúc xóa (ví dụ trong lúc yêu cầu chờ phía sau một lần export hoặc purge dài, một bản ghi
  sự cố hoặc một tác vụ bị tắt). Sau khi khôi phục và trước khi bật lại tác vụ, hãy hủy trong PartFlow (Administration
  → History archival & purge, **Permanent deletion requests**) mọi yêu cầu xóa ở `APPROVED` không còn cần nữa.

### Thủ tục path 3

Đường 3 chỉ áp dụng khi hồ sơ phát hành cho thấy `writes_reopened_at: null` (lần phát hành dừng ở trạng thái tạm
dừng ghi) hoặc chủ sở hữu ghi nhận rằng không có lần ghi sản xuất nào sau migration; nếu không thì đó là đường 4. Việc
khôi phục vào production cần **sự phê duyệt của chủ sở hữu, được ghi vào `rollback.json` trước khi `backend` khởi
động** trên database đã khôi phục; không có nó, thủ tục dừng sau `reconcile`, với PartFlow ở trạng thái chặn ghi. Hãy
tắt tác vụ lưu trữ lịch sử trước bước A (Việc khôi phục và job lưu trữ lịch sử, ở trên).

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
11. Chỉ tạo (hoặc bật) tác vụ lưu trữ lịch sử (NAS: [`SYNOLOGY_NAS.vi.md`](./SYNOLOGY_NAS.vi.md) §11.8) sau khi bạn đã
    kiểm tra các lần lưu trữ và các yêu cầu xóa vĩnh viễn trong Administration → History archival & purge: một lần lưu
    trữ mà bản sao lưu giữ ở `PURGING` sẽ được tiếp tục purge, và một yêu cầu xóa mà nó giữ ở `APPROVED` sẽ được thực
    hiện, ở lần khởi động đầu tiên của tác vụ. Hãy hủy trong PartFlow mọi yêu cầu xóa ở `APPROVED` không còn cần nữa
    trước khi bật tác vụ (Việc khôi phục và job lưu trữ lịch sử, ở trên).

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
9. Chuyển cả thư mục lưu trữ (§11, Chuyển sang máy chủ mới): các bản lưu trữ thuộc về bản cài đặt, và không nơi nào
   khác giữ chúng. Thư mục sao chép ngoài máy chủ, nếu có, vẫn ở nguyên chỗ; chỉ đặt `PARTFLOW_ARCHIVE_COPY_DIR` trên
   máy chủ mới khi thư mục đó có thể được mount ở đó.

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
Dạng dùng khi phát triển nằm trong [`README.vi.md`](../../README.vi.md) ở thư mục gốc.

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
| (a) Current positions replay from Movement history | Các vị trí hiện tại bằng kết quả phát lại lịch sử Movement chưa bị đảo ngược; một flow có lịch sử đã bị một lần lưu trữ purge lấy trạng thái mong đợi từ bản ghi chuyển tiếp (carry-forward record) của nó (§11). |
| (b) Quantity Flow history and conservation | Mọi flow đang hoạt động hoặc đã đóng đều có lịch sử bảo toàn hợp lệ, cộng các quy tắc liên dòng của [`SLICE1_DATA_MODEL.vi.md`](../SLICE1_DATA_MODEL.vi.md) §17 trừ quy tắc dòng kiểm toán (audit-row); một flow có lịch sử đã bị purge được kiểm tra qua bản ghi chuyển tiếp của nó. |
| (c) Per-PN quantity balance | Số lượng đưa vào theo từng PN bằng các kết cục đang hoạt động, đã nhập kho, đã loại bỏ và đã đảo ngược của nó, kể cả các bản ghi chuyển tiếp của những flow đã bị purge. |
| (d) Machine assigned quantities | Số lượng gán cho mỗi Machine bằng các flow trên đó. |
| (e) Release evidence and Work Order status | `released_quantity` của mỗi demand suy ra từ bằng chứng `RECEIVED` của nó và từ các bản ghi chuyển tiếp của những flow đã bị purge. |
| (f) Allocations and Work Order completion | `allocated_quantity` của mỗi demand và `completed_at` của mỗi Work Order suy ra từ các phân bổ đang hoạt động, và không PN nào được phân bổ vượt quá số lượng đã nhập kho của nó, kể cả số lượng đã nhập kho của những flow đã bị purge; một phân bổ được cho phép vượt quá demand (`exceeds_demand`) không phải là phát hiện. |
| (g) Retained Movements reference no purged row | Việc purge của lưu trữ lịch sử Movement để lại các thành phần flow trọn vẹn: không flow nào có bản ghi đã purge còn Movement, không cạnh lineage nào nối một flow đã purge với một flow được giữ lại, mọi bản ghi đã purge thuộc về một lần lưu trữ đã bắt đầu purge, mọi bản ghi của một lần purge đã kết thúc đều đã được purge hoặc bỏ qua, và các số đếm purge của mỗi lần lưu trữ bằng số bản ghi đã purge của nó. |
| (h) Append-only guards and database-role privileges intact | Các trigger và hàm bảo vệ có mặt, được bật và không đổi: kể cả row guard `trg_part_movements_guard_delete` của việc purge, các row guard `trg_<table>_guard_delete` của chín bảng khác mà một lần xóa vĩnh viễn xóa trong đó, các guard của yêu cầu xóa (`deletion_requests`, `deletion_request_rows`) và các guard cấp câu lệnh (statement guard) từ chối `UPDATE` của role bảo trì trên `work_orders` và `work_order_demands`; `partflow_app` và `partflow_maintenance` giữ đúng các quyền (grants) của chúng (`partflow_app` không có `UPDATE`, `DELETE` hay `TRUNCATE` trên lịch sử chỉ-ghi-thêm; `partflow_maintenance` có `DELETE` trên đúng mười bảng mà một lần xóa vĩnh viễn xóa trong đó, và các row guard của chúng giới hạn nó vào các Movement đã lưu trữ của một lần lưu trữ ở `PURGING` và vào các dòng đã lưu của một yêu cầu xóa ở `RUNNING`), giữ các thuộc tính an toàn và không là thành viên của role nào; `PUBLIC` và các đặc quyền mặc định không chứa gì; và `session_replication_role` không phải `replica`. Nó không thấy được thay đổi của một superuser đã tắt một trigger rồi khôi phục lại sau đó (một giới hạn được chấp nhận). |
| (i) Hot list entries are active demand | Truy vấn Hot list của [`../DEPLOYMENT.vi.md`](../DEPLOYMENT.vi.md) §5 không trả dòng nào. |
| (j) Canonical identity under the running interpreter and database | PN chuẩn hóa, badge Worker không phân biệt hoa thường, quy tắc tiền tố Asset Tag và mọi ràng buộc dạng chuẩn đều đúng theo quy tắc sắp xếp của database đang chạy, phiên bản collation không đổi, và không khóa định danh nào bị trùng. |

Kiểm tra (h) báo `not_applicable` chỉ trên database không có các role của PartFlow (phát triển, kiểm thử, staging);
điều này trung lập đối với mã thoát. Trong production, thiếu một role là một phát hiện. Ngay sau mỗi lần purge và mỗi
lần xóa vĩnh viễn, `archive.sh` tự chạy đối soát đầy đủ (§11); một phát hiện khi đó là một sự cố (§8, Phát hiện của đối
soát sau một lần purge, hoặc sau một lần xóa).

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

`deploy/production/scheduled-reconcile.sh` là dạng chạy hằng ngày, khuyến nghị lúc 04:00, từ thư mục mã nguồn
của bản phát hành đang chạy, bằng tài khoản vận hành ([`SYNOLOGY_NAS.vi.md`](./SYNOLOGY_NAS.vi.md)
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
| `check.sh`: `FAIL backup_age` | Sao lưu theo lịch không chạy hoặc thất bại: kiểm tra tác vụ và email của nó, rồi chạy sao lưu thủ công (§3). |
| `check.sh`: `FAIL disk_...` | Áp lực về database hoặc dung lượng lưu trữ, bên dưới. |
| `check.sh`: `FAIL archival_proposal`, hoặc email báo lỗi của tác vụ lưu trữ | §11: dòng kiểm tra và email nêu tên phát hiện hoặc bước. |
| `archive.sh` thoát với mã 4, hoặc từ chối với mã thoát 1 vì bản ghi sự cố tồn tại; hoặc `check.sh`: `FAIL archival_proposal reconcile_after_purge` | Đối soát sau một lần purge hoặc một lần xóa vĩnh viễn chưa kết thúc sạch: một sự cố, và job lưu trữ không làm việc với lần lưu trữ nào và không thực hiện yêu cầu xóa nào cho đến khi sự cố được đóng (Phát hiện của đối soát sau một lần purge, hoặc sau một lần xóa, bên dưới). |
| `check.sh`: `FAIL archival_proposal deletion_request_waiting` | Một yêu cầu xóa vĩnh viễn vẫn còn mở hơn 15 phút sau khi được yêu cầu (§10). `APPROVED`: tác vụ lưu trữ lịch sử chưa khởi động `archive.sh`, mọi lần khởi động đều thất bại, hoặc yêu cầu đang chờ phía sau lần lưu trữ mà dòng này nêu tên, phần việc của lần lưu trữ đó đi trước. `RUNNING`: một lần khởi động đã dừng trong lúc xóa, và chưa có lần khởi động nào sau đó chuyển yêu cầu sang `FAILED`. Kiểm tra tác vụ đã được bật chưa và đọc các email báo lỗi của nó, hoặc chạy `deploy/production/pf archive` và đọc nó báo gì. Khi dòng này nói việc lưu trữ chưa được thiết lập, không có gì thực hiện yêu cầu: hãy thiết lập tác vụ lưu trữ lịch sử ([`SYNOLOGY_NAS.vi.md`](./SYNOLOGY_NAS.vi.md) §11.8) hoặc hủy yêu cầu trong PartFlow. |
| `archive.sh` hoặc `pf delete-permanently` thoát với mã 3: một yêu cầu xóa `FAILED` | Không có gì bị xóa. Lý do nêu nguyên nhân: dữ liệu đã thay đổi sau yêu cầu, user yêu cầu không còn hoạt động hoặc không còn giữ permission, hoặc job đã dừng khi yêu cầu ở `RUNNING`. Yêu cầu xóa lại nếu vẫn còn cần (§10). |
| `check.sh`: `FAIL archival_proposal approval_overdue` | Một lần lưu trữ tự động đã chờ phê duyệt (`PROPOSED`) lâu hơn **Report a waiting run after** (ban đầu là 7 ngày). Một người giữ Run history archival and purge phê duyệt nó, kèm một lý do của riêng họ, hoặc bác bỏ nó trong Administration → History archival & purge (§11, Các lần lưu trữ tự động). |
| `archive.sh` thoát với mã 3, hoặc `check.sh`: `FAIL archival_proposal authorization_failed` | Một lần lưu trữ đã thất bại trước bản sao lưu trước purge hoặc trước việc purge của nó vì nó không còn chạy được theo thẩm quyền của nó: user đã yêu cầu, đã phê duyệt nó hoặc đã tắt bước phê duyệt không còn hoạt động hoặc không còn giữ Run history archival and purge, hoặc bước phê duyệt đã được bật lại sau khi lần lưu trữ được tạo mà không cần phê duyệt. Không có gì bị xóa; lý do nêu nguyên nhân. Hãy yêu cầu một lần lưu trữ mới, phê duyệt lần được đề xuất kế tiếp, hoặc nhờ một người giữ permission lưu lại thiết lập phê duyệt (§11, Các lần lưu trữ tự động). |
| `archive.sh` thoát với mã 3, hoặc `check.sh`: `FAIL archival_proposal last_run_failed`, với một lần lưu trữ tự động được tạo mà không cần phê duyệt | Ngoài các nguyên nhân của Một lần lưu trữ thất bại (§11), các thiết lập có thể đã chặn nó trước bản sao lưu trước purge hoặc trước việc purge của nó: **Automatic runs** đã bị tắt, hoặc thời hạn lưu giữ không còn cho phép mốc cắt của nó (thời hạn đã được kéo dài, hoặc bị xóa, sau khi lần lưu trữ được tạo). Không có gì bị xóa, và ủy quyền thường trực vẫn còn hiệu lực. Nếu đó là chủ ý thì không cần làm gì thêm, dù `check.sh` vẫn báo thất bại cho đến khi một lần lưu trữ sau kết thúc; nếu không, hãy bật lại Automatic runs hoặc yêu cầu một lần lưu trữ (§11, Các lần lưu trữ tự động). |
| `check.sh`: `FAIL archival_proposal standing_authorization_invalid` | Các lần lưu trữ tự động đang bật mà không có bước phê duyệt, và ủy quyền thường trực của nó không còn hiệu lực: một lần lưu trữ được tạo theo ủy quyền đó đã thất bại vì user đã tắt bước đó không còn chạy được nó, hoặc user đó không còn hoạt động hoặc không còn giữ permission. Không lần lưu trữ tự động nào được tạo cho đến khi một người giữ Run history archival and purge lưu lại thiết lập phê duyệt (§11, Các lần lưu trữ tự động). |
| `archive.sh` thoát với mã 2: "history-archive evaluate could not run" | Việc đánh giá các lần lưu trữ tự động không xong (ví dụ trong giới hạn thời gian của bản xem trước) và không tạo lần lưu trữ nào; phần việc với lần lưu trữ đang mở vẫn tiếp tục. Nếu nó lặp lại, hãy tăng **Preview time limit** trong Administration → History archival & purge, hoặc kiểm tra database (§2). |
| `archive.sh`: bản sao lưu trước purge thất bại | Không có gì bị xóa; lần khởi động hằng giờ kế tiếp chuyển lần lưu trữ sang `FAILED`. Sửa nguyên nhân (một bản sao lưu giữ khóa sao lưu lâu hơn `--backup-wait-minutes`, một khóa sao lưu cũ (§3), thư mục sao lưu thiếu dung lượng, một lỗi dump), rồi yêu cầu một lần lưu trữ mới, hoặc chờ lần lưu trữ tự động kế tiếp (§11, Bản sao lưu trước purge). |
| `archive.sh`: một lần lưu trữ `FAILED while purging` | Chỉ các thành phần flow trọn vẹn đã bị xóa, và biên nhận purge liệt kê chúng; đối soát đã chạy sau đó. Yêu cầu một lần lưu trữ mới cho phần còn lại (§11, Một lần lưu trữ thất bại). |
| `archive.sh` thoát với mã 2: "the purge of run N stopped", lần lưu trữ ở lại `PURGING` | Một lỗi database tạm thời (mất kết nối, deadlock, hết thời gian chờ, máy chủ database tắt); lô đang chạy đã được rollback. Khi database trả lời trở lại, lần khởi động kế tiếp tiếp tục purge và đối soát khi nó kết thúc. Nếu nó cứ dừng lại, hãy kiểm tra database (§2). |
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
| Một user mất app xác thực (authenticator app) hoặc passkey của xác thực hai bước (two-step sign-in) và các mã khôi phục (recovery code) | Xem mục Đăng nhập và tài khoản người dùng, bên dưới. |
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
  từ chối và không có gì thay đổi (login name không tồn tại, hai lần gõ mật khẩu khác nhau, mật khẩu vi phạm quy tắc
  mật khẩu của Administration → Security — thông báo nêu tên từng quy tắc —, user chưa từng có mật khẩu, hoặc không còn
  Administrator nào); 2 nghĩa là chưa rõ kết quả hoặc không kết nối được database: hãy chạy lại,
  vì lệnh đặt lại mật khẩu dù lần trước đã thành công hay chưa. Lệnh chỉ chạy khi còn một Administrator và không bao
  giờ tạo Administrator mới.

**Mất app xác thực hoặc passkey (xác thực hai bước).** Một user mất app hoặc một passkey đăng nhập bằng một cái khác,
bằng một trong các mã khôi phục của mình, hoặc bằng một mã mà PartFlow gửi qua email khi tài khoản có địa chỉ email và
đã thiết lập một outgoing mail server. Sau đó user gỡ cái đã mất và thêm một cái mới từ menu tài khoản
(`Two-step sign-in…`). Một user không có cách nào trong số này:

- Khi còn một quản trị viên người dùng (user administrator) khác đăng nhập được, người đó chọn **Turn off two-step…** ở
  dòng của user trong **Administration → Users**. User sau đó đăng nhập chỉ bằng mật khẩu. Hãy giữ **Sign them out
  everywhere** được đánh dấu khi thiết bị bị mất có thể nằm trong tay người khác; nếu mật khẩu cũng có thể đã bị biết,
  hãy chọn thêm **Set password…**.
- Khi không ai đăng nhập được, ví dụ Administrator duy nhất mất app và các passkey, tắt nó bằng:

  ```sh
  $PF run --rm --no-deps -T backend python -m app.cli reset-two-step --login-name <login name> --end-sessions
  ```

  Lệnh xóa app xác thực, các passkey và các mã khôi phục. `--end-sessions` cũng đăng xuất user ở mọi nơi; bỏ nó đi để
  giữ các phiên đăng nhập của user. Mã thoát 0 nghĩa là xong (hoặc đã tắt sẵn, lệnh sẽ nói rõ); 1 nghĩa là bị từ chối
  và không có gì thay đổi (login name không tồn tại); 2 nghĩa là chưa rõ kết quả hoặc không kết nối được database: hãy
  chạy lại.

Một user bị khóa sau quá nhiều mã sai thì chờ khóa kết thúc (Security → User sign-in đặt thời gian khóa), hoặc một
quản trị viên người dùng đặt mật khẩu mới, việc này gỡ khóa.

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

### Phát hiện của đối soát sau một lần purge (Reconcile finding after a purge)

Ngay sau mỗi lần purge lịch sử Movement, `archive.sh` chạy đối soát đầy đủ (§11). Khi nó không kết thúc sạch (một phát
hiện, một lỗi, hoặc không có báo cáo hoàn chỉnh), `archive.sh` thoát với mã 4, việc này gửi email báo lỗi, và ghi bản
ghi sự cố `<records-dir>/archive/reconcile-after-purge`: lần lưu trữ, kết quả, các kiểm tra thất bại và đường dẫn của
báo cáo. Khi đó `check.sh` báo `FAIL archival_proposal reconcile_after_purge` cho đến khi bản ghi bị xóa. Khi bản ghi
còn tồn tại, job dừng ở trạng thái an toàn (fail closed): mỗi lần khởi động chỉ chạy `recover` và `status`, không làm
việc với lần lưu trữ nào (không export, kiểm tra, sao chép, bản sao lưu trước purge hay purge, kể cả việc tiếp tục purge
một lần lưu trữ bị bỏ lại ở `PURGING`), không thực hiện yêu cầu xóa vĩnh viễn nào và thoát với mã 1, nên tác vụ lưu trữ
lịch sử gửi một email báo lỗi mỗi giờ cho đến khi sự cố được đóng. Việc purge có thể đã làm hỏng lịch sử hoặc số lượng,
nên hãy xử lý nó như một sự cố:

1. **Ngừng yêu cầu lần lưu trữ và yêu cầu xóa.** Bản thân job không làm việc với lần lưu trữ nào và không thực hiện yêu
   cầu xóa nào khi bản ghi còn tồn tại. Báo mọi người có quyền **Run history archival and purge** hoặc **Delete Work
   Orders and Part Numbers permanently** không gửi yêu cầu nào, và hủy một lần lưu trữ hoặc một yêu cầu xóa đã được yêu
   cầu trong lúc đó (Cancel trong Administration → History archival & purge, làm được khi lần lưu trữ ở `APPROVED`
   hoặc `WAITING_FOR_COPY` và khi yêu cầu xóa ở `APPROVED`; một lần lưu trữ tự động được đề xuất, `PROPOSED`, thì bị
   bác bỏ thay vì hủy, và job không tạo lần lưu trữ tự động nào khi bản ghi còn tồn tại): nếu không, job sẽ thực hiện
   nó khi sự cố được đóng. Để dừng các email báo lỗi mỗi giờ, hãy tắt tác vụ lưu trữ lịch sử trong trình lập lịch của
   máy chủ (trên NAS: bỏ chọn **Enabled** trong Task Scheduler). Giữ bản sao lưu trước purge, thư mục lưu trữ và biên
   nhận purge: chúng là bằng chứng và là đường quay lại.
2. **Khi đối soát không chạy được hoặc kết thúc bằng lỗi**, hãy chạy lại nó bằng tay (§7, Chạy đối soát). Khi nó sạch,
   lần purge được xác nhận: đóng sự cố (bước 5). Khi nó có phát hiện, đi tiếp.
3. **So sánh các phát hiện với biên nhận.** Báo cáo `<records-dir>/archive/<UTC>/reconcile.json` (đường dẫn của nó nằm
   trong bản ghi sự cố và trong email) nêu kiểm tra, thực thể và Part Number của mỗi phát hiện. Biên nhận purge
   `<archive-dir>/<archive name>.purged.json` liệt kê mọi thành phần của lần lưu trữ với kết cục (`PURGED`, `SKIPPED`
   hoặc `RETAINED`), các Part Number, các flow và số Movement của nó. Các lệnh sau liệt kê các Part Number của những
   thành phần đã purge và của các phát hiện (không bao giờ đính kèm một báo cáo vào ticket hay email: phát hiện của
   kiểm tra (j) có thể chứa giá trị badge của Worker):

   ```sh
   jq -r '.components[] | select(.outcome == "PURGED") | .part_numbers[]' "<archive-dir>/<archive name>.purged.json" | sort -u
   jq -r '.checks[] | select(.status != "pass") | .id + " " + .title' "<report>"
   jq -r '.checks[].findings[] | .part_number // empty' "<report>" | sort -u
   ```

   Hãy so sánh thêm với lần đối soát theo lịch gần nhất trước lần purge (`~/partflow-monitoring/reconcile/`). Một phát
   hiện đã có từ trước, trên một Part Number mà biên nhận không liệt kê là `PURGED`, không đến từ lần purge: đó là một
   sự cố đã có (§7, Quy tắc vận hành). Một phát hiện mới trên một Part Number đã purge, hoặc bất kỳ phát hiện nào của
   kiểm tra (g), chỉ về lần purge.
4. **Chủ sở hữu quyết định: sửa tiến (fix forward), hay khôi phục bản sao lưu trước purge.**
   - Sửa tiến giữ mọi thay đổi đã thực hiện kể từ lần purge. Không bao giờ sửa lịch sử hoặc các tổng đã lưu bằng tay
     (§7, Quy tắc vận hành); chủ sở hữu quyết định từng lần sửa.
   - Khôi phục bản sao lưu trước purge mang các dòng đã purge trở lại, nhưng **bỏ mất mọi thay đổi được ghi nhận kể từ
     khi bản dump của nó bắt đầu** (`dump_started_at` trong `manifest.json` của bản sao lưu, cũng là
     `pre_purge_backup.dump_started_at` trong biên nhận), vì production vẫn tiếp tục trong và sau lần purge. Chỉ chọn
     cách này khi các phát hiện cho thấy lần purge đã làm hỏng lịch sử hoặc số lượng mà không thể sửa tiến, và chủ sở
     hữu chấp nhận mất mát đó; hãy ghi lại quyết định và khoảng thời gian bị mất.

   **Bản sao lưu nào (Which backup).** Hãy khôi phục bản sao lưu trước purge mà biên nhận nêu tên, không bao giờ một
   bản sao lưu muộn hơn, và tắt tác vụ lưu trữ lịch sử trước nếu bước 1 chưa làm (§6, Việc khôi phục và job lưu trữ
   lịch sử). Một bản sao lưu muộn hơn, như bản sao lưu mỗi giờ được tạo trong lúc purge chạy, giữ lần lưu trữ ở
   `PURGING`, và job tiếp tục purge đó ở lần khởi động đầu tiên sau khi bản ghi sự cố bị xóa và tác vụ được bật. Bản sao
   lưu trước purge giữ lần lưu trữ ở `RUNNING`: lần khởi động kế tiếp chuyển nó sang thất bại, và không có gì bị purge
   lại.

   **Cách khôi phục.** Làm theo Thủ tục path 3 (§6) với các thay đổi sau. Thay cho guard bản ghi phát hành của bước A,
   hãy dừng ghi (`$PF stop backend`, và kiểm tra rằng `$PF ps --status running -q backend` không in gì), rồi kiểm tra
   bản sao lưu bằng `(cd "<backup-dir>/<NAME>" && sha256sum -c SHA256SUMS)` và
   `$PF --profile ops run --rm --no-deps -T --user "$(id -u):$(id -g)" backup-tools backup-verify "<NAME>"`, trong đó
   `<NAME>` là bản sao lưu trước purge mà biên nhận nêu tên. Chạy B1 với `BPATH=<backup-dir>/<NAME>`, và B2 với `REC` là
   một thư mục sự cố mới mà bạn tạo trước (`mkdir <records-dir>/<UTC>-restore-<NAME>`); ghi sự phê duyệt của chủ sở hữu
   vào `restore.json` ở đó (các trường của `rollback.json`) trước B3; chạy B3 với `PREV` là bản phát hành đang chạy,
   tức tag trong `.env.production`: bản phát hành không đổi, và thư mục mã nguồn giữ nguyên. Database đã bị purge giữ
   nguyên tên và nội dung để phân tích.

   **Sau khi khôi phục**, database giữ lần lưu trữ ở `RUNNING`, như lúc bản sao lưu được tạo. Hãy chạy
   `deploy/production/pf archive` một lần: bước `recover` của nó chuyển lần lưu trữ sang `FAILED` ("…also after a
   restore of its pre-purge backup). Nothing was deleted; request a new run."), nên nó không bao giờ bị purge lại; một
   lần lưu trữ thất bại là kết thúc, và chỉ một yêu cầu mới mới khởi động một lần lưu trữ mới, với export, bản sao và
   bản sao lưu trước purge của riêng nó. `archive.sh` thoát với mã 3 cho lần lưu trữ đó và nói "Nothing was deleted in
   this database", điều đúng với database đã khôi phục: nó lại có mọi Movement đã bị purge. Vì bản ghi sự cố vẫn còn,
   lần khởi động đó không làm việc với lần lưu trữ nào khác. `check.sh` báo `last_run_failed` cho lần lưu trữ đó cho
   đến khi một lần lưu trữ sau kết thúc.

   **Những gì vẫn báo về lần purge.** Thư mục lưu trữ `<archive-dir>/<archive name>/`, biên nhận
   `<archive name>.purged.json` của nó (trạng thái `COMPLETED` hoặc `COMPLETED_WITH_SKIPS`, kết quả `PURGED`) và các
   bản sao của chúng trong thư mục sao chép được giữ làm bằng chứng: hãy để nguyên chúng. `recover` và `status` chỉ đọc
   biên nhận của những lần lưu trữ đã ghi một SHA-256 biên nhận, và lần lưu trữ đã khôi phục không có, nên không gì báo
   rằng biên nhận này nay mô tả database mà việc khôi phục đã thay thế: hãy ghi điều đó lại trong thư mục sự cố, cạnh
   `restore.json`. Một lần lưu trữ sau sẽ lưu trữ lại cùng lịch sử đó vào một thư mục mới. Số thứ tự lần lưu trữ có thể
   lặp lại sau một lần khôi phục; thời gian trong tên thư mục phân biệt các thư mục.
5. **Đóng sự cố.** Khi quyết định của chủ sở hữu đã được thực hiện và đối soát (§7) sạch, hãy kiểm tra các lần lưu
   trữ và các yêu cầu xóa vĩnh viễn trong Administration → History archival & purge (một lần lưu trữ ở `PURGING` được
   tiếp tục purge, và một yêu cầu xóa ở `APPROVED` được thực hiện, ở lần khởi động kế tiếp), xóa bản ghi sự cố, và chỉ
   khi đó mới bật lại tác vụ nếu bạn đã tắt nó:

   ```sh
   rm ~/partflow-deployments/archive/reconcile-after-purge
   ```

   Chỉ việc xóa bản ghi mới đóng sự cố: lần khởi động kế tiếp làm việc với các lần lưu trữ và các yêu cầu xóa trở lại,
   ví dụ tiếp tục purge một lần lưu trữ bị bỏ lại ở `PURGING`. Các báo cáo vẫn nằm trong thư mục hồ sơ của chúng.

### Phát hiện của đối soát sau một lần xóa (Reconcile finding after a deletion)

Ngay sau mỗi lần xóa vĩnh viễn (§10), `archive.sh` chạy đối soát đầy đủ, như sau một lần purge. Khi nó không kết thúc
sạch, đó là cùng sự cố như sau một lần purge (Phát hiện của đối soát sau một lần purge, ở trên): `archive.sh` thoát với
mã 4 và ghi cùng bản ghi sự cố `<records-dir>/archive/reconcile-after-purge`, bản ghi này nêu yêu cầu xóa (`request`)
và bản sao lưu trước purge của nó thay cho một lần lưu trữ, `check.sh` báo `FAIL archival_proposal
reconcile_after_purge`, và khi bản ghi còn tồn tại, job không làm việc với lần lưu trữ nào và không thực hiện yêu cầu
xóa nào. `archive.sh` ghi cùng bản ghi đó khi nó bị ngắt trong lúc xóa hoặc trước khi đối soát sau lần xóa kết thúc:
khi đó lần xóa có thể đã commit mà chưa được kiểm tra. Hãy làm theo các bước của sự cố sau purge, với những khác biệt
sau:

1. **Ngừng yêu cầu xóa và yêu cầu lần lưu trữ**, và hủy một yêu cầu xóa đã được tạo trong lúc đó (nó ở `APPROVED`):
   nếu không, job sẽ thực hiện nó khi sự cố được đóng.
2. **Đọc trạng thái của yêu cầu**, chỉ đọc:
   `$PF --profile ops run --rm --no-deps -T --user "$(id -u):$(id -g)" maintenance status` (khóa
   `last_finished_deletion_request`, hoặc `open_deletion_request` khi nó còn mở). `COMPLETED` nghĩa là lần xóa đã
   commit, sau bản sao lưu trước purge mà nó nêu tên (`pre_purge_backup_name`). `RUNNING` nghĩa là nó chưa commit
   (`archive.sh` bị ngắt), nên không có gì bị xóa, và lần khởi động kế tiếp của job chuyển nó sang `FAILED`.
3. **Khi đối soát không chạy được, kết thúc bằng lỗi hoặc bị ngắt**, hãy chạy lại nó bằng tay (§7). Khi nó sạch, hãy
   đóng sự cố (bước 6).
4. **So sánh các phát hiện với lần xóa.** Các dòng audit `DELETED` của yêu cầu nêu tên mọi Work Order và Part Number đã
   bị xóa, và yêu cầu liệt kê số dòng của nó theo từng bảng. Một phát hiện trên một Part Number mà lần xóa không chạm
   tới, và đã có trong lần đối soát theo lịch gần nhất, không đến từ lần xóa.
5. **Chủ sở hữu quyết định: sửa tiến, hoặc khôi phục bản sao lưu trước purge của lần xóa**, tức bản sao lưu mà yêu cầu
   nêu tên, `<giờ UTC>-pre-purge-deletion-<yêu cầu>-<giờ UTC>`. Khôi phục nó đem các bản ghi đã xóa trở lại và **bỏ
   mọi thay đổi được ghi từ lúc dump của nó bắt đầu**, như với một lần purge; hãy ghi lại quyết định và khoảng thời
   gian bị mất. Khôi phục nó như sự cố sau purge mô tả (bước 4, Cách khôi phục), với bản sao lưu đó thay cho bản của
   một lần lưu trữ. **Sau khi khôi phục**, database giữ yêu cầu ở `RUNNING`, như lúc bản sao lưu được tạo. Chạy
   `deploy/production/pf archive` một lần: `recover` của nó chuyển yêu cầu sang `FAILED`, nên không có gì bị xóa lại;
   `archive.sh` thoát với mã 3 cho yêu cầu đó và nói "Nothing was deleted by them in this database", điều đúng với
   database đã khôi phục: nó lại chứa mọi bản ghi đã bị xóa. Vì bản ghi sự cố vẫn còn, lần khởi động đó không làm gì
   khác.
6. **Đóng sự cố** như sau một lần purge (bước 5 của sự cố sau purge): khi quyết định của chủ sở hữu đã được thực hiện
   và đối soát sạch, hãy kiểm tra các lần lưu trữ và các yêu cầu xóa trong Administration → History archival &
   purge, xóa bản ghi sự cố, và chỉ khi đó mới bật lại tác vụ nếu bạn đã tắt nó.

### Áp lực về database hoặc dung lượng lưu trữ (Database or storage pressure)

- Chặn các lần ghi mới trước khi đĩa đầy (tạm dừng ghi, §5), và giữ lại log cùng số liệu.
- Cảnh báo dung lượng là một dòng `disk_*` của `check.sh` với phần trống dưới 15 %: `disk_data` cho volume database,
  `disk_backup` cho thư mục sao lưu, `disk_docker` cho bộ nhớ Docker (image và log container), `disk_archive` cho thư
  mục lưu trữ.
- **Không xóa tệp PostgreSQL, volume, dòng Movement hay bản sao lưu để lấy chỗ.**
- Quy tắc về dung lượng: một bản sao lưu cần dung lượng trống gấp hai lần dump mới nhất cộng phần dự phòng trong thư
  mục sao lưu; một lần khôi phục theo đường 3 cần kích thước database cộng hai lần dump cộng 1 GiB trên volume
  database (§6); một khóa sao lưu cũ chặn sao lưu và phát hành cho đến khi nó được xóa (§3).
- Mở rộng dung lượng lưu trữ. History archival & purge (§11) xóa lịch sử Movement cũ khỏi database, nhưng việc đó chỉ
  giải phóng các dòng bên trong bảng cho Movement mới, nên database ngừng lớn lên mà các tệp của nó không nhỏ đi; dung
  lượng đĩa chỉ được trả lại bằng `VACUUM FULL part_movements`, lệnh này cần dung lượng trống lớn bằng bảng (§11, Lấy
  lại dung lượng đĩa sau một lần purge). Mỗi lần purge, và mỗi lần xóa vĩnh viễn (§10), cũng thêm một bản sao lưu trước
  purge vào thư mục sao lưu.
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
| Mỗi 15 phút (tự động) | `check.sh`: tình trạng, các lần khởi động lại, đĩa, chứng chỉ, tuổi bản sao lưu, lỗi backend và trạng thái lưu trữ (§2). Một thất bại không đổi được nhắc lại mỗi 6 giờ. |
| Mỗi 5 phút (tự động) | `archive.sh --scheduled`: mỗi lần khởi động thực hiện ngay một yêu cầu xóa vĩnh viễn đang mở (bản sao lưu trước purge, việc xóa và đối soát sau đó, §10); mỗi giờ một lần, vào Start minute của Administration → History archival & purge, job lưu trữ lịch sử trước hết đánh giá các lần lưu trữ tự động (nó tạo một lần khi chúng đang bật và đến hạn), rồi xử lý một lần lưu trữ đã được phê duyệt: export, kiểm tra, bản sao ngoài máy chủ, bản sao lưu trước purge, purge và đối soát sau đó (§11). |
| Hằng ngày | Sao lưu theo lịch (mỗi giờ) và đối soát theo lịch lúc 04:00 tự chạy. Đọc mọi email báo lỗi, kể cả của bản sao ngoài máy chủ; mã thoát 4 của một bản sao lưu bao gồm cả trường hợp một bản sao lưu theo lịch hoặc trước purge không qua kiểm tra, và mã thoát 4 của job lưu trữ lịch sử là một sự cố (§8). |
| Hằng tuần | Xem xét mức tăng của database (`~/partflow-monitoring/growth.tsv`) và `docker stats --no-stream`. Xem xét các lần đăng nhập thất bại và quyền bị từ chối: `$PF logs --since 168h --no-log-prefix backend \| grep '"logger":"app.access"' \| grep -E '"status":(401\|403)'` (`refusal.type` và `refusal.code` phân biệt đăng nhập bị từ chối hoặc bị khóa, từ chối quyền, `station_device_required`, `station_device_mismatch` và `csrf_rejected`). Kiểm tra các bản cập nhật bảo mật đang chờ của máy chủ. |
| Hằng tháng | Xác nhận bản sao ngoài máy chủ của mọi lần lưu trữ đang chờ việc đó, và phê duyệt hoặc bác bỏ mọi lần lưu trữ tự động đang chờ phê duyệt (§11). Xóa các báo cáo đối soát cũ sau khi xem xét. Xóa các bản sao lưu pre-release và manual đã hết thời gian theo dõi, và các bản sao lưu theo lịch hoặc trước purge mà việc xoay vòng báo là không hợp lệ, sau khi xem xét (bên dưới). Cài đặt các bản cập nhật, ưu tiên thử trên hệ thống kiểm thử trước nếu có thể. Xem xét người dùng và vai trò, quy tắc tường lửa và các bí mật. Chạy `reconcile --check h`. |
| Hằng quý, và sau một thay đổi schema lớn | Diễn tập khôi phục (§4); so sánh các thời gian `restore_to_ready` và `total` của nó với thời gian nhà máy có thể làm việc mà không có PartFlow. |
| Trước khi đổi image PostgreSQL hoặc glibc của máy chủ | `restore-test.sh --backup <latest> --db-image <candidate>` (§4, §7). |
| Trước mỗi lần phát hành | Xem xét các migration, quyết định quay lui và các kiểm tra (§5); `release.sh` tạo bản sao lưu. |

**Xóa các bản sao lưu và báo cáo cũ.** Không công cụ nào xóa bản sao lưu pre-release, bản sao lưu manual, bản sao lưu
theo lịch hoặc trước purge không qua kiểm tra, hay báo cáo đối soát; việc xoay vòng tự giữ các bản sao lưu trước purge
mới nhất (§3). Sau khi xem xét, xóa từng cái theo tên; không bao giờ xóa bản sao lưu theo lịch đã kiểm tra mới nhất,
bản sao lưu trước purge của một sự cố đang mở (§8, Phát hiện của đối soát sau một lần purge, hoặc sau một lần xóa) hoặc
bất cứ thứ gì trong thư mục lưu trữ:

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
  `partflow_maintenance_password`, thay tệp đó và chỉ chạy lệnh `db-roles` khi job lưu trữ lịch sử không làm việc
  (không có `~/partflow-deployments/.archive.lock`), tránh thời điểm Start minute của nó và khi không có yêu cầu xóa
  vĩnh viễn nào đang mở: chỉ job một lần (one-shot) đó dùng role này (§11), nên không cần tạm dừng ghi. Một lần purge
  không còn kết nối được tới database thì ở lại `PURGING`, và lần khởi động kế tiếp tiếp tục nó; một lần xóa không còn
  kết nối được tới nó thì không xóa gì, và yêu cầu của nó chuyển sang `FAILED` ở lần khởi động kế tiếp; một lần đánh
  giá các lần lưu trữ tự động không còn kết nối được tới nó thì không tạo lần lưu trữ nào, và phần việc hằng giờ kế
  tiếp đánh giá lại.
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

## 10. Xóa vĩnh viễn dữ liệu thử nghiệm

Lịch sử production chỉ được ghi thêm (append-only). PartFlow chỉ xóa một Work Order đã có lịch sử sản xuất hay một
allocation, hoặc một Movement, theo hai cách: việc purge của History archival & purge xóa lịch sử Movement đã lưu trữ
khi bản lưu trữ của nó đã được sao chép ra ngoài máy chủ và một bản sao lưu trước purge đã được tạo (§11;
[`../PROJECT_PROFILE.md`](../PROJECT_PROFILE.md), Administrative Archival and Purge), và một **yêu cầu xóa vĩnh viễn**
(permanent deletion request) xóa các Work Order và Part Number đã được nhập **chỉ để thử nghiệm** trên production, như
thể chúng chưa từng được nhập, và giữ lại một dòng audit cho mỗi Work Order và mỗi Part Number đã xóa. Không bao giờ
dùng một yêu cầu xóa để sửa dữ liệu production thật; hãy dùng các chức năng sửa sai mà PartFlow cung cấp.

Có hai cách yêu cầu một lần xóa, và cả hai đều dẫn đến cùng một loại yêu cầu:

- **Trong PartFlow**, bởi một user có role giữ permission **Delete Work Orders and Part Numbers permanently** (ban đầu
  chỉ role Super User; Administration → Roles & permissions có thể cấp nó cho role khác): **Delete permanently…** trong
  Work Order Details yêu cầu xóa Work Order đó, và trong hộp thoại Part Number yêu cầu xóa Part Number đó cùng toàn bộ
  dữ liệu của nó ([`../GUI_DESIGN.vi.md`](../GUI_DESIGN.vi.md)).
- **Bởi người vận hành**, bằng `pf delete-permanently` (bên dưới), lệnh này có thể nêu tên nhiều Work Order và Part
  Number.

Yêu cầu chính là sự phê duyệt. PartFlow hiển thị cùng bản xem trước và các trường hợp từ chối theo cả hai cách, hỏi lý
do, và ghi lại yêu cầu cùng mọi dòng mà nó sẽ xóa; mỗi lúc có nhiều nhất một yêu cầu đang mở. **Job lưu trữ lịch sử**
(§11) thực hiện yêu cầu ở lần khởi động kế tiếp của nó, trong vòng 5 phút: nó tạo một bản sao lưu trước purge của
database, xóa các dòng đã lưu trong **một** giao dịch (transaction) database, rồi chạy đối soát đầy đủ. Bản thân ứng
dụng web không bao giờ xóa gì. **Vì vậy một lần xóa cần job lưu trữ lịch sử đã được thiết lập:** thư mục lưu trữ
(`PARTFLOW_ARCHIVE_DIR`) và tác vụ lưu trữ lịch sử khởi động `archive.sh` mỗi 5 phút
([`SYNOLOGY_NAS.vi.md`](./SYNOLOGY_NAS.vi.md) §11.8). Không có chúng, một yêu cầu tạo trong PartFlow chỉ chờ, còn
`pf delete-permanently --confirm` từ chối trước khi ghi lại bất cứ điều gì.

### Yêu cầu và các trạng thái của nó

| Trạng thái | Hiển thị trong PartFlow | Ý nghĩa |
| --- | --- | --- |
| `APPROVED` | Waiting for the server | Đã được ghi lại. Tác vụ lưu trữ lịch sử thực hiện nó ở lần khởi động kế tiếp. Vẫn có thể hủy nó trong PartFlow: **Cancel** trong hộp thoại, hoặc trong Administration → History archival & purge, **Permanent deletion requests**. |
| `RUNNING` | Deleting… | Job đã bắt đầu nó: tạo bản sao lưu trước purge và xóa. Không thể hủy. |
| `COMPLETED` | Deleted | Đã xóa, sau bản sao lưu trước purge mà yêu cầu nêu tên. |
| `FAILED` | Failed — nothing was deleted | Không có gì bị xóa; lý do được hiển thị. Yêu cầu xóa lại nếu vẫn còn cần. |
| `CANCELLED` | Cancelled | Đã bị hủy trước khi job bắt đầu nó; không có gì bị xóa. |

Khi job bắt đầu yêu cầu và một lần nữa ngay trước khi xóa, nó kiểm tra rằng user đã yêu cầu vẫn còn hoạt động và vẫn
giữ permission; nếu không, yêu cầu chuyển sang `FAILED`. Yêu cầu của người vận hành không có user nào để kiểm tra:
thẩm quyền của nó là thông tin xác thực của database owner đã ghi nó. Chính việc xóa trước hết lấy các khóa của các
Part Number và các dòng của nó, rồi tính lại kế hoạch: khi dữ liệu đã thay đổi sau yêu cầu (ví dụ một lần quét trên
một trong các flow của nó, hoặc một trong các demand line của nó được đưa vào Hot list), yêu cầu chuyển sang `FAILED`
("The data changed after the request; nothing was deleted. Request it again."), kèm phần khác biệt. Một yêu cầu bị bỏ
lại ở `RUNNING` bởi một job đã dừng, hoặc bởi một bản sao lưu trước purge thất bại, chuyển sang `FAILED` ở lần khởi động
kế tiếp, và không có gì bị xóa. PartFlow vẫn hoạt động trong lúc xóa; chỉ một thao tác trên cùng một trong các Part
Number đó phải chờ một transaction của nó.

`check.sh` báo `FAIL archival_proposal deletion_request_waiting` khi một yêu cầu vẫn còn mở (`APPROVED` hoặc
`RUNNING`) hơn 15 phút sau khi được yêu cầu (`--deletion-wait-minutes`): tác vụ lưu trữ lịch sử chưa thực hiện hoặc
chưa hoàn tất nó, nên hãy kiểm tra tác vụ đã được bật chưa và đọc các email báo lỗi của nó. Một yêu cầu `APPROVED` đang
chờ phía sau một lần lưu trữ ở `RUNNING` hoặc `PURGING` được nêu như vậy: phần việc của lần lưu trữ đó đi trước. Khi
việc lưu trữ chưa được thiết lập, `check.sh` vẫn tìm thấy một yêu cầu như vậy (qua kiểm tra nhẹ của `archive.sh` trong
container `backend` đang chạy) và báo rằng không có gì thực hiện nó. Một yêu cầu `FAILED` không phải là lỗi của
`check.sh`: người yêu cầu thấy nó trong PartFlow.

### Xem trước, rồi xác nhận

Cách của người vận hành, `pf delete-permanently`:

1. Xem những gì sẽ bị xóa. Không có gì bị thay đổi:

   ```sh
   deploy/production/pf delete-permanently --work-order "<Work Order Number>" --part-number "<Part Number>" \
       --reason "<lý do xóa dữ liệu>"
   ```

   Lặp lại `--work-order` và `--part-number` cho mỗi bản ghi; phải có ít nhất một trong hai. Viết Work Order Number
   đúng như PartFlow hiển thị, kể cả khoảng trắng và chữ hoa: số này được so khớp chính xác. Part Number được chuẩn hóa
   như PartFlow chuẩn hóa mọi Part Number (bỏ khoảng trắng ở hai đầu, chuyển thành chữ hoa). `--reason` là bắt buộc
   và được ghi lại; `--operator` mặc định là tên đăng nhập của bạn, như các lệnh `pf` khác. Bản xem trước cũng chạy
   được khi việc lưu trữ chưa được thiết lập.
2. Đọc báo cáo mà lệnh in ra standard output (một tài liệu JSON): `work_orders` và `part_numbers` nêu tên các bản ghi,
   `rows` cho biết số dòng của mỗi bảng sẽ bị xóa, `kept_audit_rows` liệt kê các dòng audit được giữ lại vì chúng cũng
   liên quan đến các bản ghi còn lại, và `refusals` liệt kê mọi lý do khiến việc xóa không thể được yêu cầu.
3. Yêu cầu nó: chạy lại đúng lệnh đó với `--confirm`. Khi đó `pf` chạy các bước sau và dừng ở bước đầu tiên thất bại:
   1. nó kiểm tra rằng job lưu trữ lịch sử có thể thực hiện yêu cầu: `PARTFLOW_ARCHIVE_DIR` nêu một thư mục tồn tại, và
      không có bản ghi sự cố nào của §8; nếu không, nó thoát với mã 2 và không ghi lại gì;
   2. nó ghi lại yêu cầu: kế hoạch một lần nữa và yêu cầu trong một transaction (tài liệu JSON của nó, `result`
      `requested`, đi ra standard output); một lần từ chối dừng ở đây, không ghi lại gì;
   3. nó chạy ngay job lưu trữ lịch sử (`archive.sh`, như `pf archive`), job này bắt đầu yêu cầu, tạo bản sao lưu trước
      purge của nó, xóa và chạy đối soát (§11); như mọi phần việc hằng giờ, lần khởi động này trước hết đánh giá các lần
      lưu trữ tự động;
   4. nó đọc trạng thái cuối cùng của yêu cầu từ `history-archive status` và báo nó (Mã thoát, bên dưới).

   Khi một `archive.sh` khác đang giữ khóa lưu trữ (ví dụ việc purge dài của một lần lưu trữ), yêu cầu ở lại
   `APPROVED`, và tác vụ lưu trữ lịch sử thực hiện nó ở lần khởi động kế tiếp khi khóa đã được giải phóng: `pf` báo như
   vậy và thoát với mã 2. Một lần khởi động đã thực hiện một lần xóa không làm phần việc lưu trữ nào; lần khởi động kế
   tiếp làm nó.

Lệnh ghi lại yêu cầu bằng database role chủ sở hữu (owner), trong service chạy một lần `db-roles`: chỉ owner mới được
ghi yêu cầu của một người vận hành (PartFlow ghi yêu cầu của các user bằng database role của ứng dụng, và chỉ role bảo
trì của job lưu trữ lịch sử mới xóa). Khi không dùng `pf`, hãy chạy
`$PF --profile ops run --rm --no-deps -T db-roles delete-permanently <các tùy chọn như trên> --operator "<tên bạn>" --confirm`,
rồi `deploy/production/pf archive`; `--backup-name` của các bản phát hành trước bị từ chối, vì job tự tạo bản sao lưu
trước purge. Lệnh không lấy khóa bảng nào; `--lock-timeout` (mặc định 30 giây) giới hạn thời gian việc ghi lại yêu cầu
chờ một khóa. Dù vậy hãy yêu cầu một lần xóa vào lúc ít hoạt động: một lần quét trên một trong các flow của nó giữa
lúc yêu cầu và lúc xóa sẽ làm yêu cầu thất bại.

### Những gì bị xóa, và những gì còn lại

| Bị xóa cho một Work Order | Bị xóa cho một Part Number |
| --- | --- |
| Work Order, các demand line của nó, mọi allocation của các line đó, mọi Quantity Flow đã release cho các line đó và mọi flow liên kết với chúng qua một lần tách (split) hay gộp (combine), mọi Movement và bản ghi lineage của các flow đó, các bản sao Assigned Route của các flow đó cùng các bước của chúng, và các dòng audit chỉ liên quan đến các bản ghi này. | Bản ghi Part Number cùng thông tin chi tiết và hình ảnh, mọi Quantity Flow của Part Number với cùng phạm vi liên kết, mọi demand line và allocation của Part Number, và các dòng audit chỉ liên quan đến các bản ghi này. Mọi Work Order có một line của Part Number phải được xóa trong cùng yêu cầu; một Work Order nội bộ (không có Work Order Number, do một lần nhận hàng tại Scan Station tạo ra) mà mọi line đều thuộc Part Number đang bị xóa sẽ bị xóa cùng. |

Dữ liệu gốc (master data) được giữ lại: Department, Area, Operation, Machine, Worker, Worker Session, Scan Station,
Route Template và các bản ghi Part Number không được nêu tên. Sau đó có thể nhập lại một Work Order Number đã xóa.

**Dòng audit duy nhất.** Với mỗi Work Order và Part Number đã xóa, job thêm một dòng audit (sự kiện `DELETED`) với các
giá trị cuối cùng của bản ghi; tác nhân (actor) của nó là user đã yêu cầu việc xóa (không có với yêu cầu của người vận
hành), và metadata (`permanent_deletion`) của nó chứa số thứ tự yêu cầu, người vận hành của một yêu cầu từ người vận
hành, lý do, bản sao lưu trước purge, tên của mọi bản ghi bị xóa cùng lúc, và số dòng đã xóa của mỗi bảng; thời điểm
của dòng là thời điểm xóa. Mỗi thay đổi trạng thái của một yêu cầu thêm một dòng audit của chính yêu cầu (ai đã yêu cầu
hoặc hủy nó, và khi nào). Một dòng audit cũng liên quan đến các bản ghi còn lại (ví dụ một thay đổi Hot list cũng dời
một line còn lại) được giữ nguyên. Các dòng audit ghi lại một lần xóa vĩnh viễn trước đó (các dòng `DELETED`) và các
dòng audit của các yêu cầu xóa luôn được giữ lại: một lần xóa sau không bao giờ xóa chúng, kể cả khi một Work Order
Number hoặc Part Number đã bị xóa được nhập lại và bị xóa lần nữa.

**Dữ liệu đã xóa vẫn còn trong các bản sao lưu.** Mọi bản sao lưu được tạo trước khi xóa, kể cả bản sao lưu trước purge
mà job tạo ngay trước đó (`<giờ UTC>-pre-purge-deletion-<yêu cầu>-<giờ UTC>`, §11, Bản sao lưu trước purge), và mọi bản
sao ngoài máy chủ (off-host) của các bản sao lưu đó vẫn chứa dữ liệu đã xóa cho đến khi chúng hết hạn theo chính sách
giữ lại của §3. Khôi phục một bản sao lưu như vậy sẽ đưa dữ liệu trở lại (§6, Việc khôi phục và job lưu trữ lịch sử).

### Các trường hợp từ chối

Yêu cầu bị từ chối, không có gì được ghi lại hay bị xóa, và từng liên kết được nêu tên khi dữ liệu cần xóa liên kết với
dữ liệu còn lại:

| Mã | Ý nghĩa | Cần làm gì |
| --- | --- | --- |
| `unknown_work_order`, `unknown_part_number` | Không có Work Order nào có đúng số này, hoặc Part Number không có bản ghi cũng không có dữ liệu nào. | Kiểm tra lại cách viết. |
| `part_number_used_by_work_order` | Một Work Order không bị xóa có một demand line của Part Number. | Xóa luôn Work Order đó trong cùng yêu cầu, hoặc giữ Part Number. |
| `on_hot_list` | Một demand line cần xóa đang ở trong Hot list; xóa nó sẽ để lại một khoảng trống trong thứ hạng của các line còn lại. | Gỡ nó khỏi Hot list trong PartFlow trước. |
| `lineage_link` | Một lần tách hay gộp liên kết một flow cần xóa với một flow còn lại (đã release cho một Work Order còn lại, hoặc số lượng được thêm không qua Work Order). | Xóa Work Order đó trong cùng yêu cầu, hoặc giữ dữ liệu. |
| `stock_needed` | Số lượng đã nhập kho của các flow cần xóa vẫn cần cho các allocation của các Work Order còn lại. | Đảo ngược (reverse) các allocation đó trong PartFlow trước, hoặc xóa luôn các Work Order đó. |
| `allocation_command_shared`, `allocation_reversal_link` | Một allocation, hoặc lần đảo ngược của nó, cũng liên quan đến một demand line còn lại. | Xóa Work Order đó trong cùng yêu cầu, hoặc giữ dữ liệu. |
| `movement_command_shared`, `movement_reversal_link`, `route_snapshot_shared`, `movement_references_deleted_data` | Một lệnh sản xuất, một lần Undo, một bản sao Assigned Route hoặc bản ghi của một Movement cũng liên quan đến một flow còn lại. | Xóa Work Order của flow đó trong cùng yêu cầu, hoặc giữ dữ liệu. |
| `archived_history` | Lịch sử của một flow cần xóa đã được một lần lưu trữ lưu trữ (§11), và có thể đã bị purge: một lần xóa không bao giờ xóa lịch sử đã lưu trữ. | Giữ dữ liệu. |
| `request_open` | Một yêu cầu xóa khác vẫn đang mở (`APPROVED` hoặc `RUNNING`): "Deletion request N is still open (STATE). Cancel it or wait until it finishes before requesting another deletion." | Chờ đến khi nó kết thúc, hoặc hủy nó trong PartFlow khi nó ở `APPROVED`. |
| `owner_role_required` | Lệnh không chạy bằng database role chủ sở hữu. | Chạy nó qua `pf` hoặc service `db-roles`. |

Trong PartFlow, hộp thoại hiển thị cùng các trường hợp từ chối; ở đó một yêu cầu khác đang mở là một câu trả lời riêng,
với cùng văn bản, thay vì một trường hợp từ chối của bản xem trước.

### Mã thoát

| Mã thoát | `pf delete-permanently` |
| --- | --- |
| 0 | Đã hiển thị bản xem trước, hoặc (với `--confirm`) yêu cầu đã được thực hiện: các bản ghi đã bị xóa (`COMPLETED`). |
| 1 | Bị từ chối: không có gì được yêu cầu hay bị xóa (Các trường hợp từ chối, ở trên). |
| 2 | Không thể chạy, hoặc yêu cầu đã được ghi lại nhưng chưa được thực hiện. Không có gì được ghi lại khi việc lưu trữ chưa được thiết lập, bản ghi sự cố tồn tại, không kết nối được database hoặc không được cấp khóa kịp thời. Yêu cầu chờ ở `APPROVED` khi job không thể bắt đầu nó (một `archive.sh` khác giữ khóa lưu trữ, một lần phát hành đang chạy): tác vụ lưu trữ lịch sử thực hiện nó ở lần khởi động kế tiếp. Nó ở lại `RUNNING` khi job dừng trước khi việc xóa commit (bản sao lưu trước purge của nó thất bại, một lỗi database): không có gì bị xóa, và lần khởi động kế tiếp chuyển nó sang `FAILED`. Khi kết nối database bị lỗi đúng lúc ghi lại yêu cầu, yêu cầu có thể tồn tại hoặc không: hãy đọc nó trong Administration → History archival & purge. |
| 3 | Yêu cầu `FAILED`: không có gì bị xóa, và lý do được in ra. |
| 4 | Các bản ghi đã bị xóa, nhưng đối soát sau lần xóa chưa kết thúc sạch: một sự cố (§8, Phát hiện của đối soát sau một lần xóa). |

Khi không dùng `pf`, bản thân lệnh `delete-permanently` thoát với 0 (đã hiển thị bản xem trước, hoặc yêu cầu đã được
ghi lại), 1 (bị từ chối, không ghi lại gì) hoặc 2 (thất bại hoặc lỗi cách dùng, không ghi lại gì; hoặc
`outcome_unknown`, như trên).

## 11. Lưu trữ lịch sử (History archival)

PartFlow chuyển lịch sử Movement cũ ra khỏi database vào các tệp lưu trữ rồi xóa nó khỏi database: đó là History
archival & purge ([`../PROJECT_PROFILE.vi.md`](../PROJECT_PROFILE.vi.md), Administrative Archival and Purge). Một lần
lưu trữ (run) xuất lịch sử Movement của các flow đã đóng cũ hơn mốc cắt (cutoff) của nó vào một thư mục lưu trữ, kiểm
tra bản lưu trữ, sao chép nó ra ngoài máy chủ, tạo một bản sao lưu database trước purge, rồi **purge** (xóa hẳn): nó
xóa các Movement đã lưu trữ khỏi database và giữ một bản ghi chuyển tiếp (carry-forward record) cho mỗi flow đã lưu
trữ, để mọi số lượng, trạng thái và kiểm tra vẫn chính xác. Sau khi purge, lịch sử đó nằm trong thư mục lưu trữ và bản
sao ngoài máy chủ của nó (và trong các bản sao lưu trước purge còn được giữ), không còn trong database; các lệnh có lịch
sử đã được lưu trữ không thể hoàn tác (Undo) được nữa. Cùng job đó cũng thực hiện các yêu cầu xóa vĩnh viễn dữ liệu thử
nghiệm (§10), và tạo các **lần lưu trữ tự động** (automatic runs) khi chúng được bật (Các lần lưu trữ tự động, bên
dưới). Các yêu cầu xem và các nhóm tùy chọn là những bước xây dựng sau.

Một người dùng có quyền **Run history archival and purge** đặt thời hạn lưu giữ và các thiết lập khác, xem bản xem
trước và yêu cầu một lần lưu trữ trong **Administration → History archival & purge**. Một lần lưu trữ được yêu cầu
(thủ công) không cần phê duyệt nào khác: yêu cầu chính là sự phê duyệt. Một lần lưu trữ tự động chờ một người dùng như
vậy phê duyệt, trừ khi bước phê duyệt (approval step) bị tắt: khi đó nó chạy theo ủy quyền thường trực (standing
authorization) của người dùng đã tắt bước đó (Các lần lưu trữ tự động, bên dưới). Ứng dụng web không bao giờ đụng đến
thư mục lưu trữ. **Job lưu trữ lịch sử** (history archive job) trên máy chủ làm phần việc đó.

### Job lưu trữ làm gì (What the archive job does)

Một tác vụ trên máy chủ khởi động `deploy/production/archive.sh --scheduled` mỗi 5 phút
([`SYNOLOGY_NAS.vi.md`](./SYNOLOGY_NAS.vi.md) §11.8). Mỗi lần khởi động trước hết chạy một kiểm tra nhẹ (cheap check):
`python -m app.cli history-archive settings` bằng `docker compose exec` trong container `backend` đang chạy, hai lần
đọc nhanh bằng role database của ứng dụng cho biết có yêu cầu xóa vĩnh viễn nào đang mở không và cho biết Start minute.
Một lần khởi động tìm thấy một yêu cầu đang mở sẽ thực hiện nó ngay, bất kể giờ nào (các bước xóa bên dưới). Mỗi giờ một
lần, ở lần khởi động đầu tiên vào hoặc sau **Start minute** đặt trong Administration (ban đầu là 20), nó làm **phần
việc hằng giờ**, bắt đầu bằng việc đánh giá các lần lưu trữ tự động; mọi lần khởi động khác kết thúc trong vài giây và
in `NOT_DUE`. Lần khởi động đầu tiên sau khi tạo tác vụ chỉ ghi lại giờ hiện tại, nên phần việc hằng giờ đầu tiên đến
vào Start minute kế tiếp. Một lần khởi động đã thực hiện một lần xóa không làm phần việc lưu trữ nào và để giờ đó cho
lần khởi động kế tiếp, lần này làm phần việc hằng giờ 5 phút sau. Khi kiểm tra không có câu trả lời (ví dụ `backend` đã
dừng), lần khởi động coi như không có yêu cầu nào đang mở: phần việc hằng giờ vẫn diễn ra như thường (khi đó Start
minute được đọc trong dịch vụ `maintenance`), và yêu cầu chờ một lần khởi động sau. `deploy/production/pf archive`
chạy ngay phần việc hằng giờ, bất kể giờ nào: việc đánh giá các lần lưu trữ tự động, rồi trước hết một yêu cầu xóa đang
mở, nếu không thì phần việc với một lần lưu trữ.

Các bước chạy trong dịch vụ một lần (one-shot) `maintenance`, dịch vụ này kết nối bằng role database bảo trì
(`partflow_maintenance`, được đọc lịch sử, tạo các lần lưu trữ tự động (guard của bảng các lần lưu trữ không nhận lần
lưu trữ nào khác từ nó), ghi các bản ghi lưu trữ và chỉ xóa những gì các row guard cho phép: các Movement đã lưu trữ
của một lần lưu trữ ở `PURGING`, và các dòng đã lưu của một yêu cầu xóa đang ở `RUNNING`) và là dịch vụ duy nhất mount
thư mục lưu trữ:

1. `recover`: một lần lưu trữ bị bỏ lại ở `RUNNING` bởi một job đã dừng (khởi động lại, mất điện), hoặc có bản sao
   lưu trước purge thất bại, trở thành `FAILED`, và một yêu cầu xóa bị bỏ lại ở `RUNNING` cũng vậy (không có gì bị xóa:
   một lần xóa là một transaction); các thư mục và tệp biên nhận dở dang của những lần đã kết thúc được xóa khỏi
   `.partial/`; và một biên nhận purge còn thiếu trong thư mục sao chép được sao chép lại vào đó. Một lần lưu trữ bị bỏ
   lại ở `PURGING` không bị đụng đến: việc purge của nó được tiếp tục bên dưới. Khi `recover` không kết thúc với mã
   thoát 0, không lần lưu trữ hay yêu cầu nào được xử lý trong lần khởi động đó (chỉ `status` chạy). Cũng vậy khi bản
   ghi sự cố `<records-dir>/archive/reconcile-after-purge` còn tồn tại (§8, Phát hiện của đối soát sau một lần purge,
   hoặc sau một lần xóa): lần khởi động khi đó chỉ chạy `recover` và `status`, không tạo lần lưu trữ tự động nào, không
   làm việc với lần lưu trữ nào, kể cả việc tiếp tục purge một lần lưu trữ bị bỏ lại ở `PURGING`, không thực hiện yêu
   cầu xóa nào, và thoát với mã 1 (mã 3 khi `recover` đã chuyển một lần lưu trữ hoặc một yêu cầu sang thất bại).
2. `evaluate`, chỉ trong phần việc hằng giờ, không bao giờ trong một lần khởi động chỉ thực hiện một yêu cầu xóa: việc
   đánh giá các lần lưu trữ tự động (Các lần lưu trữ tự động, bên dưới). Nó tạo lần lưu trữ mà các thiết lập đòi hỏi
   lúc này, hoặc cho biết vì sao nó không tạo lần nào, và `archive.sh` ghi log quyết định của nó. Khi nó bị từ chối
   (mã thoát 1) hoặc không chạy được hay không xong (mã thoát 2, ví dụ trong giới hạn thời gian của bản xem trước), nó
   không tạo lần lưu trữ nào, `archive.sh` báo lỗi trong mã thoát của mình, và phần việc vẫn tiếp tục với bước kế tiếp.
3. `next`, lặp lại sau mỗi bước: việc cần làm và bước kế tiếp của nó. Một yêu cầu xóa đang mở đi trước. Một yêu cầu được
   tạo trong lúc lần khởi động này đang làm các bước của một lần lưu trữ sẽ chờ đến khi lần lưu trữ đó chờ bản sao,
   purge hoặc kết thúc, và một lần khởi động sau nhận nó. Mọi bước của một lần lưu trữ có thể diễn ra trong một lần
   khởi động, kể cả các bước của một lần lưu trữ tự động mà `evaluate` vừa tạo ở `APPROVED`. Một lần lưu trữ đang chờ
   phê duyệt (`PROPOSED`) chờ một người: `next` trả lời `wait`, và job không làm gì với nó.
4. Với một yêu cầu xóa, các bước mà `next` nêu:
   - `start_deletion`: `start-deletion` chuyển yêu cầu sang `RUNNING`, tại một giây tròn dùng để đặt tên bản sao lưu
     trước purge của nó, sau khi kiểm tra lại user đã yêu cầu (nếu không, nó chuyển sang `FAILED`, không xóa gì, mã
     thoát 3).
   - `deletion_backup`: bản sao lưu trước purge của yêu cầu,
     `backup.sh --kind pre-purge --label deletion-<id>-<giờ UTC>` (Bản sao lưu trước purge, bên dưới), rồi `delete`,
     lệnh này xóa các dòng đã lưu của yêu cầu trong một transaction và chuyển nó sang `COMPLETED`. Một thất bại chuyển
     nó sang `FAILED` mà không xóa gì (mã thoát 3). Một lỗi database tạm thời (mất kết nối, deadlock, hết thời gian chờ)
     rollback transaction và để yêu cầu ở lại `RUNNING`, và `delete` thoát với mã 2. Mỗi khi `delete` thoát với mã 2,
     kể cả khi kết nối bị lỗi đúng lúc việc xóa commit, hoặc không cho một báo cáo hợp lệ, lần khởi động đọc `status`:
     một yêu cầu `COMPLETED` được đối soát ngay, một yêu cầu `FAILED` đã không xóa gì (mã thoát 3), một yêu cầu vẫn
     `RUNNING` đã không xóa gì và lần khởi động kế tiếp chuyển nó sang `FAILED` (mã thoát 2), và khi `status` không cho
     biết được, đối soát vẫn chạy.
   - Rồi đối soát đầy đủ (bước 6), và lần khởi động kết thúc.
5. Với một lần lưu trữ, bước mà `next` nêu:
   - `export` ghi bản lưu trữ vào `<archive-dir>/.partial/`. Khi không có gì đủ điều kiện, lần lưu trữ kết thúc ở
     `NOTHING_ELIGIBLE`.
   - `verify` đọc lại mọi tệp, so sánh với database và công bố thư mục `<archive-dir>/run-<id>-<giờ UTC>/`. Một thành
     phần đã thay đổi kể từ lúc export bị bỏ qua và ở lại trong database; khi mọi thành phần đều đã thay đổi, lần lưu
     trữ kết thúc ở `COMPLETED_WITH_SKIPS` với kết quả `NOTHING_PURGED`, không cần bản sao hay bản sao lưu.
   - `copy` sao chép bản lưu trữ sang thư mục sao chép ngoài máy chủ và kiểm tra bản sao, hoặc chấp nhận một xác nhận
     bản sao; nếu không có cả hai, lần lưu trữ chờ ở `WAITING_FOR_COPY` (Bản sao ngoài máy chủ, bên dưới).
   - `backup`: bản sao lưu trước purge (bên dưới). `next` chỉ giao bước này sau khi kiểm tra thẩm quyền của lần lưu
     trữ, và `purge` kiểm tra lại trước khi lần lưu trữ trở thành `PURGING`: user đã yêu cầu lần lưu trữ, user đã phê
     duyệt nó, hoặc, với một lần lưu trữ được tạo theo ủy quyền thường trực, các thiết lập (bước phê duyệt không được
     bật lên kể từ đó, **Automatic runs** vẫn bật, mốc cắt vẫn được thời hạn lưu giữ cho phép) rồi đến user đã tắt
     bước phê duyệt (Các lần lưu trữ tự động, bên dưới). Nếu không, lần lưu trữ trở thành `FAILED` và không có gì bị
     xóa.
   - `purge` với bản sao lưu đó: lần lưu trữ trở thành `PURGING`, và các Movement đã lưu trữ của nó bị xóa theo từng lô
     gồm các thành phần flow trọn vẹn, tối đa **Movements per purge batch** và **Part Numbers per purge batch** của
     Administration (ban đầu 50,000 và 20). Mỗi lô là một transaction dưới các khóa của các Part Number của nó; một
     thành phần đã thay đổi kể từ lúc export bị bỏ qua và ở lại. Biên nhận purge
     `<archive-dir>/<archive name>.purged.json` được ghi (và được sao chép sang thư mục sao chép), và lần lưu trữ kết
     thúc ở `COMPLETED` (kết quả `PURGED`) hoặc `COMPLETED_WITH_SKIPS`. Một lỗi database tạm thời trong lúc purge (mất
     kết nối, deadlock, lỗi serialization, hết thời gian chờ khóa hoặc câu lệnh, máy chủ database tắt) rollback lô đang
     chạy và để lần lưu trữ ở lại `PURGING`; lệnh purge thoát với mã 2. Một lần lưu trữ bị một lần khởi động trước bỏ
     lại ở `PURGING` (do một lỗi như vậy, hoặc do khởi động lại) được tiếp tục chỉ bằng `purge`, không cần bản sao lưu
     mới: chỉ các thành phần trọn vẹn từng bị xóa. Mọi thất bại khác trong lúc purge (số bị xóa khác với các bản ghi,
     guard từ chối, bản lưu trữ đã công bố bị thay đổi, một lỗi nội bộ) chuyển lần lưu trữ sang `FAILED`, kèm biên
     nhận của nó (Một lần lưu trữ thất bại, bên dưới).
6. Sau một lần purge đã kết thúc, hoặc đã thất bại khi lần lưu trữ ở `PURGING`, và sau một lần xóa: đối soát đầy đủ
   (§7), trong một container `backend` dùng một lần với role database của ứng dụng. Khi nó không kết thúc sạch, đó là
   một sự cố (§8, Phát hiện của đối soát sau một lần purge, hoặc sau một lần xóa). Một lần purge thoát với mã 2 không
   được đối soát trong lần khởi động đó: lần khởi động kế tiếp tiếp tục nó và đối soát khi nó kết thúc. Một lần khởi
   động không bao giờ bắt đầu thêm một lần lưu trữ khác sau một lần purge, và không làm phần việc lưu trữ nào sau một
   lần xóa.
7. `status`: lần lưu trữ đang mở, lần đã kết thúc gần nhất, yêu cầu xóa đang mở và yêu cầu xóa đã kết thúc gần nhất,
   các lần lưu trữ tự động cùng ủy quyền thường trực của chúng, và dung lượng trống, cho việc giám sát, kèm các phát
   hiện của nó (Kiểm tra một lần lưu trữ, bên dưới).

Production vẫn tiếp tục trong một lần lưu trữ và một lần xóa: bước export đọc một snapshot database, mỗi lô purge chỉ
giữ các khóa của các Part Number của nó trong transaction của chính nó, và một lần xóa giữ các khóa của các Part Number
và các dòng của nó trong một transaction duy nhất. Một lần lưu trữ lớn đầu tiên có thể kéo dài; các lần khởi động sau
khi đó sẽ chờ, và một yêu cầu xóa cũng vậy. Start minute thay đổi có hiệu lực từ phần việc hằng giờ kế tiếp. **Hãy chọn
Start minute** tránh xa bản sao lưu hằng giờ ở phút 0, vì một bản sao lưu trước purge chờ một bản sao lưu đang chạy, và
tránh xa các thời điểm bạn cài bản phát hành: một bản phát hành bị từ chối khi job đang làm việc, và một lần purge có
thể kéo dài.

**Mã thoát của `archive.sh`.** Email báo lỗi của tác vụ trên máy chủ là cảnh báo. Khi thành công, nó in một dòng:
`NOT_DUE`; `DEFERRED archive_running` (lần khởi động trước vẫn đang làm việc); với một phần việc hằng giờ không làm việc
với lần lưu trữ nào, `EVALUATE <result> <run> <state>`: kết quả của việc đánh giá các lần lưu trữ tự động (Các lần lưu
trữ tự động, bên dưới), rồi lần lưu trữ đang mở mà job đã thấy và trạng thái của nó, hoặc `-` khi không có, ví dụ
`EVALUATE off - -` (các lần lưu trữ tự động đang tắt, không lần lưu trữ nào đang mở), `EVALUATE created 7 PROPOSED`
(một lần lưu trữ đã được đề xuất và chờ phê duyệt) hoặc `EVALUATE run_open 3 WAITING_FOR_COPY`; với một phần việc hằng
giờ đã làm việc với một lần lưu trữ, `ARCHIVE <outcome> <run> <state>`, ví dụ `ARCHIVE completed 3 COMPLETED`; hoặc,
với một lần khởi động đã làm việc với một yêu cầu xóa, `DELETION <outcome> <request> <state>`, ví dụ
`DELETION deleted 7 COMPLETED`.

| Mã thoát | Ý nghĩa | Cần làm gì |
| --- | --- | --- |
| 0 | Xong, hoặc không có gì đến hạn. | Không cần gì. |
| 1 | Bị từ chối, không có gì thay đổi: một bản phát hành đang chạy, một `archive.sh` khác giữ khóa lưu trữ, một lệnh history-archive khác đang chạy, một bước bị từ chối (kể cả việc đánh giá các lần lưu trữ tự động), hoặc bản ghi sự cố của một lần đối soát sau một lần purge hoặc một lần xóa còn tồn tại. | Không cần gì: lần khởi động sau sẽ thử lại. Nếu bị từ chối lặp lại: Các khóa, bên dưới; bản ghi sự cố: §8. |
| 2 | Không thể chạy: cấu hình, Docker hoặc database, hoặc một bước có kết quả không xác định; hoặc việc đánh giá các lần lưu trữ tự động không chạy được hoặc không xong trong giới hạn thời gian của bản xem trước (nó không tạo lần lưu trữ nào, và phần việc với lần lưu trữ đang mở vẫn tiếp tục); hoặc một bản sao lưu trước purge thất bại; hoặc một lần purge hay một lần xóa dừng vì một lỗi database tạm thời (lần lưu trữ ở lại `PURGING`, yêu cầu xóa ở lại `RUNNING`). | Đọc email; sửa nguyên nhân. Một lần lưu trữ hoặc một yêu cầu xóa bị bỏ lại ở `RUNNING` sẽ được bước `recover` của lần khởi động sau chuyển sang thất bại, và không có gì bị xóa (khi đó hãy yêu cầu lại); một lần lưu trữ bị bỏ lại ở `PURGING` được lần khởi động sau tiếp tục, và lần đó đối soát khi việc purge kết thúc. Một lần đánh giá cứ hết thời gian: hãy tăng **Preview time limit** trong Administration → History archival & purge. |
| 3 | Một lần lưu trữ hoặc một yêu cầu xóa thất bại: nó hiện là `FAILED`. Một yêu cầu xóa thất bại đã không xóa gì. | Một lần lưu trữ thất bại, bên dưới (một lần lưu trữ không còn chạy được theo thẩm quyền của nó: Các lần lưu trữ tự động, bên dưới); yêu cầu xóa lại nếu vẫn còn cần (§10). |
| 4 | Đối soát sau một lần purge hoặc một lần xóa phát hiện sai lệch, kết thúc bằng lỗi hoặc không chạy được. | Một sự cố: §8, Phát hiện của đối soát sau một lần purge, hoặc sau một lần xóa. Cho đến khi bản ghi của nó bị xóa, mọi lần khởi động đều từ chối làm việc với lần lưu trữ hay yêu cầu xóa (mã thoát 1). |

`archive.sh --help` liệt kê các tùy chọn của nó; `--backup-wait-minutes N` (0 đến 1440, mặc định 60) là thời gian một
bản sao lưu trước purge chờ một bản sao lưu khác. Một lần khởi động theo lịch chỉ báo một lần từ chối hoặc thất bại một
lần cho giờ của nó, không bao giờ ở mỗi lần khởi động 5 phút, với một ngoại lệ: khi một yêu cầu xóa đang mở, mỗi lần
khởi động đều cố thực hiện nó, nên một thất bại ngăn nó bắt đầu (ví dụ dịch vụ `maintenance` không kết nối được
database) được báo ở mỗi lần khởi động cho đến khi được sửa hoặc yêu cầu bị hủy. Cho đến khi một lần khởi động tìm thấy
một yêu cầu đang mở, những gì ngăn nó tìm (một bản phát hành đang chạy, bản ghi sự cố, việc lưu trữ chưa được thiết
lập, `backend` đã dừng) kết thúc nó một cách im lặng trong giờ đó, và lần khởi động hằng giờ sẽ báo. Báo cáo của một
lần khởi động đã làm gì đó hoặc thất bại nằm trong `<records-dir>/archive/<UTC>/` (§1).

**Các khóa (Locks).** `archive.sh` giữ `<records-dir>/.archive.lock` (tệp `owner` của nó ghi máy chủ, mã tiến trình
(pid), thời điểm bắt đầu, `archive.sh`, mã lần khởi động (boot id) của kernel và thời điểm bắt đầu của tiến trình theo
cách kernel đếm) chỉ trong phần việc hằng giờ và khi nó thực hiện một yêu cầu xóa, không bao giờ khi một lần lưu trữ
chỉ đang chờ xác nhận bản sao và không bao giờ cho một lần khởi động chỉ kiểm tra giờ. Nó giữ khóa trong suốt phần
việc: kể cả thời gian chờ khóa sao lưu, bản sao lưu trước purge, việc purge hoặc việc xóa và đối soát sau đó. Nó từ chối
khi một bản phát hành đang chạy (`<records-dir>/.release.lock`), và `release.sh` từ chối, không thay đổi gì, khi khóa
lưu trữ đang bị giữ: hãy chạy lại bản phát hành vài phút sau. Tốt nhất hãy bắt đầu bản phát hành vài phút sau đầu giờ và
trước Start minute khá lâu. Khi chủ của khóa vẫn đang chạy, một lần khởi động theo lịch kết thúc bằng
`DEFERRED archive_running` (mã thoát 0) và một lần khởi động thủ công (`pf archive`) bị từ chối. Một khóa mà chủ của nó
chắc chắn không còn tồn tại được `archive.sh` kế tiếp tự động xóa: cùng máy chủ, không có container `maintenance` nào
đang chạy, và hoặc pid của nó đã mất, hoặc máy chủ đã khởi động lại từ đó (boot id khác), hoặc pid đó nay thuộc về một
tiến trình khác (thời điểm bắt đầu khác). `release.sh` bỏ qua một khóa như vậy theo cùng quy tắc. Trước đó khóa được đổi
tên, để trong hai lần khởi động chỉ một lần đi tiếp. Một tệp owner không có boot id và thời điểm bắt đầu (do một
`archive.sh` cũ hơn ghi), hoặc một máy chủ không có `/proc`, chỉ được xét theo pid. Khi một lần từ chối nói rằng khóa
không thể kiểm tra, hoặc khi các lần khởi động theo lịch cứ in `DEFERRED` dù không có job lưu trữ nào chạy, hãy kiểm tra
và xóa nó bằng tay:

```sh
cat ~/partflow-deployments/.archive.lock/owner
ps -ef | grep '[a]rchive\.sh'                        # must print nothing
docker ps --filter label=com.docker.compose.service=maintenance   # must list no container
rm -r ~/partflow-deployments/.archive.lock
```

### Các lần lưu trữ tự động (Automatic runs)

Khi **Automatic runs** được bật trong Administration → History archival & purge (ban đầu chúng tắt), phần việc hằng
giờ bắt đầu bằng `history-archive evaluate`, lệnh này quyết định có cần một lần lưu trữ ngay lúc này không và, nếu cần,
tạo lần đó. Bản thân việc đánh giá không xóa gì: một lần lưu trữ tự động được xuất, kiểm tra, sao chép, sao lưu và
purge đúng như một lần lưu trữ được yêu cầu. Không có tác vụ lưu trữ lịch sử
([`SYNOLOGY_NAS.vi.md`](./SYNOLOGY_NAS.vi.md) §11.8) thì không bao giờ có lần lưu trữ tự động nào được tạo. Các thiết
lập, trong Administration → History archival & purge:

- **Automatic runs**: bật hoặc tắt. Tắt chúng thì không lần lưu trữ tự động mới nào được tạo, và cũng chặn một lần lưu
  trữ được tạo mà không cần phê duyệt trước khi nó purge (Việc kiểm tra lại thẩm quyền của một lần lưu trữ, bên dưới).
- **Thước đo** (measure) quyết định rằng cần một lần lưu trữ, với giới hạn riêng của nó: số dòng Movement đang có (ban
  đầu là trên 5,000,000), tuổi của lịch sử đang giữ (Movement cũ nhất của nó cũ hơn mốc cắt lưu giữ hơn 1 tháng, ban
  đầu) hoặc kích thước database (ban đầu là trên 20 GiB). Giới hạn 0 cho phép mọi lần đánh giá tìm thấy lịch sử đủ
  điều kiện tạo một lần lưu trữ.
- **Bước phê duyệt** (approval step, ban đầu bật): một lần lưu trữ tự động có chờ một người phê duyệt hay không.
- **Số ngày giữa các lần lưu trữ tự động** (ban đầu là 7), tính từ lúc kết thúc của lần lưu trữ tự động gần nhất, dù nó
  kết thúc thế nào (hoàn tất, bị bác bỏ, bị hủy hoặc thất bại); các lần lưu trữ được yêu cầu không được tính.
- **Report a waiting run after** (ban đầu là 7 ngày) cũng giới hạn thời gian một lần lưu trữ được đề xuất được chờ phê
  duyệt.

`evaluate` kiểm tra các điều kiện theo thứ tự này và dừng ở điều kiện đầu tiên không thỏa. `archive.sh` ghi log kết
quả của nó kèm một thông điệp nêu các giá trị nó đã đọc:

| Kết quả | Ý nghĩa |
| --- | --- |
| `off` | Các lần lưu trữ tự động đang tắt. |
| `no_retention_period` | Chưa đặt thời hạn lưu giữ, nên không có lịch sử nào đủ điều kiện. |
| `run_open` | Có một lần lưu trữ đang mở, kể cả một lần được đề xuất: không lần lưu trữ tự động nào được tạo khi có một lần đang mở. |
| `not_due` | Lần lưu trữ tự động gần nhất kết thúc chưa đủ số ngày giữa các lần lưu trữ tự động; thông điệp cho biết khi nào lần kế tiếp đến hạn. |
| `authorization_invalid` | Bước phê duyệt đang tắt, và ủy quyền thường trực của nó không còn hiệu lực (bên dưới); thông điệp cho biết vì sao. |
| `below_limit` | Thước đo không vượt quá giới hạn của nó. |
| `nothing_eligible` | Thước đo vượt giới hạn, nhưng không có gì đủ điều kiện tại mốc cắt lưu giữ. Khi đó mỗi phần việc hằng giờ đọc lại bản xem trước. |
| `settings_changed` | Công tắc bật hoặc thiết lập phê duyệt đã thay đổi trong lúc đánh giá: không lần lưu trữ nào được tạo, và phần việc hằng giờ kế tiếp đánh giá lại. |
| `created` | Một lần lưu trữ đã được tạo: `PROPOSED`, hoặc `APPROVED` theo ủy quyền thường trực. |

Tất cả các kết quả này thoát với mã 0. Thước đo và bản xem trước của lịch sử đủ điều kiện dùng chung **Preview time
limit** của Administration (ban đầu là 90 giây): một lần đánh giá hết thời gian đó thì không tạo lần lưu trữ nào và
thoát với mã 2, giống như khi nó không kết nối được tới database, và một lệnh history-archive khác đang giữ khóa của job
thì làm nó bị từ chối với mã thoát 1. Khi đó `archive.sh` báo lỗi trong mã thoát của mình, và lần khởi động vẫn làm
việc với lần lưu trữ đang mở. Hãy tăng giới hạn thời gian của bản xem trước khi việc đánh giá cứ hết thời gian.

**Một lần lưu trữ được đề xuất.** Khi bước phê duyệt bật, một lần lưu trữ tự động được tạo ở `PROPOSED`, kèm các số
đếm của bản xem trước và một lý do tự động, ví dụ "Live Movement rows 5,120,334 above the limit 5,000,000; retention 24
months". Job không làm gì với nó. Một người giữ Run history archival and purge quyết định trong Administration →
History archival & purge:

- **Approve…** (phê duyệt), kèm một lý do của riêng họ: lần lưu trữ trở thành `APPROVED`, và phần việc hằng giờ kế tiếp
  xuất nó. User đã phê duyệt nó là user mang thẩm quyền của nó.
- **Dismiss…** (bác bỏ), không cần lý do: lần lưu trữ trở thành `DISMISSED` và kết thúc; số ngày giữa các lần lưu trữ
  tự động được tính từ lúc nó bị bác bỏ.

Một lần lưu trữ được đề xuất không thể bị hủy. Trong khi nó chờ, không thể yêu cầu lần lưu trữ nào khác và không lần lưu
trữ tự động nào khác được tạo. Khi nó đã chờ lâu hơn **Report a waiting run after**, `check.sh` báo
`FAIL archival_proposal approval_overdue` cho đến khi nó được phê duyệt hoặc bị bác bỏ.

**Ủy quyền thường trực (standing authorization).** Tắt bước phê duyệt cần Run history archival and purge, một lý do và
lời xác nhận gõ lại `RUN WITHOUT APPROVAL`. Dòng audit của thay đổi đó là **ủy quyền thường trực**: từ đó, một lần lưu
trữ tự động được tạo ở `APPROVED`, ghi lại ủy quyền đó, và chính lần khởi động đó làm việc với nó, nên không ai xem xét
bản xem trước của nó trước khi nó chạy. Lưu lại thiết lập phê duyệt kèm một lý do và lời xác nhận trong khi bước đó đang
tắt sẽ gia hạn nó (một ủy quyền thường trực mới); bật bước đó lên sẽ thu hồi nó. Ủy quyền thường trực còn hiệu lực khi
user đã tắt bước đó vẫn đang hoạt động và vẫn giữ Run history archival and purge, và khi chưa có lần lưu trữ nào được
tạo theo nó thất bại vì user đó. Sau một thất bại như vậy, nó vẫn mất hiệu lực, kể cả khi user đó hoạt động lại và giữ
lại permission, cho đến khi một người giữ permission lưu lại thiết lập phê duyệt (gia hạn nó, hoặc bật bước phê
duyệt). Khi nó không còn hiệu lực, `evaluate` trả lời `authorization_invalid` và không tạo lần lưu trữ nào,
Administration hiển thị lý do, và `check.sh` báo `FAIL archival_proposal standing_authorization_invalid` khi các lần lưu
trữ tự động đang bật.

**Việc kiểm tra lại thẩm quyền của một lần lưu trữ.** Ngay trước khi giao bản sao lưu trước purge, và một lần nữa ngay
trước khi purge, job kiểm tra thẩm quyền của lần lưu trữ:

- một lần lưu trữ được yêu cầu: user đã yêu cầu nó;
- một lần được đề xuất đã được phê duyệt: user đã phê duyệt nó;
- một lần lưu trữ được tạo theo ủy quyền thường trực, mà không người nào đã xem xét, theo thứ tự này: bước phê duyệt,
  bước này không được bật lên kể từ khi lần lưu trữ được tạo (gia hạn ủy quyền thì không tính); **Automatic runs**,
  phải vẫn bật; mốc cắt của lần lưu trữ, mà thời hạn lưu giữ như nó đang được đặt lúc này phải vẫn cho phép (nó không
  còn cho phép khi thời hạn đã được kéo dài qua mốc đó, hoặc bị xóa); rồi đến user của ủy quyền mà lần lưu trữ được tạo
  theo.

User đó phải vẫn đang hoạt động và vẫn giữ Run history archival and purge. Dù kiểm tra nào thất bại, lần lưu trữ trở
thành `FAILED` và không có gì bị xóa, và `archive.sh` thoát với mã 3:

- Khi một user không còn hoạt động hoặc không còn giữ permission, hoặc bước phê duyệt đã được bật lên, `check.sh` báo
  `authorization_failed` kèm lý do (thay cho `last_run_failed`) cho đến khi một lần lưu trữ sau kết thúc. Khi đó hãy
  yêu cầu một lần lưu trữ mới, hoặc phê duyệt lần được đề xuất kế tiếp. Khi user của ủy quyền thường trực làm lần lưu
  trữ thất bại, ủy quyền đó vẫn mất hiệu lực, và các lần lưu trữ tự động chờ cho đến khi một người giữ permission lưu
  lại thiết lập phê duyệt.
- Khi **Automatic runs** đang tắt hoặc thời hạn lưu giữ không còn cho phép mốc cắt, lần lưu trữ thất bại như mọi lần
  lưu trữ thất bại khác: `check.sh` báo `last_run_failed` kèm lý do cho đến khi một lần lưu trữ sau kết thúc, và ủy
  quyền thường trực vẫn còn hiệu lực cho lần lưu trữ tự động kế tiếp.

Vì vậy, một lần lưu trữ được tạo mà không cần phê duyệt vẫn có thể bị chặn trước khi nó purge: hãy tắt **Automatic
runs**, hoặc bật bước phê duyệt (việc này cũng thu hồi ủy quyền thường trực), và lần kiểm tra lại kế tiếp của nó làm
nó thất bại, không xóa gì. Kéo dài thời hạn lưu giữ sẽ chặn một lần lưu trữ như vậy có mốc cắt mà thời hạn mới không
còn cho phép. Một lần lưu trữ đã ở `PURGING` thì đã qua cả hai lần kiểm tra lại. Các thiết lập này không bao giờ chặn
một lần lưu trữ được yêu cầu hay một lần được đề xuất đã được phê duyệt: hãy hủy một lần như vậy trong Administration
khi nó đang `APPROVED` hoặc `WAITING_FOR_COPY`.

### Kiểm tra một lần lưu trữ (Checking a run)

- **Administration → History archival & purge** liệt kê mọi lần lưu trữ với trạng thái, các số đếm và, khi đã xuất,
  tên bản lưu trữ và SHA-256 của manifest.
- `check.sh` in dòng `archival_proposal` (§2): `PASS` với lần đang mở (một lần ở `PURGING` kèm những gì nó đã purge
  tới lúc đó; một lần đang chờ phê duyệt kèm thời gian nó đã chờ,
  `run 7 PROPOSED waiting 2.0 days for approval (limit 7 days)`), lần đã kết thúc gần nhất (kết quả của nó, những gì
  nó đã purge và đã bỏ qua), dung lượng trống, và yêu cầu xóa vĩnh viễn đang mở và yêu cầu đã kết thúc gần nhất
  (`deletion request 7 APPROVED for 2 min`, `last deletion request 6 COMPLETED`), hoặc `FAIL` nêu tên từng phát hiện:
  `copy_confirmation_overdue` (một lần lưu trữ đã chờ xác nhận bản sao lâu hơn thiết lập **Report a waiting run
  after**), `approval_overdue` (một lần lưu trữ tự động đã chờ phê duyệt, `PROPOSED`, lâu hơn thiết lập đó: hãy phê
  duyệt hoặc bác bỏ nó), `last_run_failed` (lần đã kết thúc gần nhất là `FAILED`), `authorization_failed` (được nêu
  thay cho `last_run_failed` khi lần đó thất bại vì nó không còn chạy được theo thẩm quyền của nó, kèm lý do: Các lần
  lưu trữ tự động, ở trên), `standing_authorization_invalid` (các lần lưu trữ tự động đang bật mà không có bước phê
  duyệt, và ủy quyền thường trực của nó không còn hiệu lực, kèm lý do: Các lần lưu trữ tự động, ở trên),
  `archive_dir_unusable` (thư mục lưu trữ không tồn tại hoặc không ghi được), `copy_dir_unusable` (thư mục sao chép đã
  được đặt nhưng chưa mount, không tồn tại, không ghi được hoặc nằm trên chính hệ thống tệp của thư mục lưu trữ),
  `receipt_not_copied` (biên nhận purge của một lần lưu trữ mà job đã sao chép ra ngoài máy chủ bị thiếu hoặc khác
  trong thư mục sao chép; lần khởi động kế tiếp sao chép lại nó) hoặc `deletion_request_waiting` (một yêu cầu xóa vẫn
  còn mở, `APPROVED` hoặc `RUNNING`, hơn 15 phút sau khi được yêu cầu, `--deletion-wait-minutes`: tác vụ lưu trữ lịch
  sử chưa thực hiện hoặc chưa hoàn tất nó, nên hãy kiểm tra tác vụ đã được bật chưa; một yêu cầu `APPROVED` đang chờ
  phía sau một lần lưu trữ ở `RUNNING` hoặc `PURGING` nêu tên lần lưu trữ đó). Một yêu cầu xóa `FAILED` không phải là
  phát hiện. Sự cố của một lần đối soát sau một lần purge hoặc một lần xóa đứng đầu, là `reconcile_after_purge` (§8).
- Báo cáo đầy đủ, chỉ đọc; nó cũng nêu yêu cầu xóa đang mở (`open_deletion_request`, kèm thời gian chờ của nó), yêu
  cầu đã kết thúc gần nhất (`last_finished_deletion_request`) và các lần lưu trữ tự động (`automatic_runs`: chúng có
  đang bật không, bước phê duyệt có đang bật không, và ủy quyền thường trực cùng hiệu lực và `problem` của nó):

  ```sh
  $PF --profile ops run --rm --no-deps -T --user "$(id -u):$(id -g)" maintenance status
  ```

| Trạng thái | Ý nghĩa |
| --- | --- |
| `PROPOSED` | Một lần lưu trữ tự động đang chờ một người giữ Run history archival and purge phê duyệt hoặc bác bỏ (Các lần lưu trữ tự động, ở trên). Job không làm gì với nó. Không thể hủy: hãy bác bỏ nó. |
| `APPROVED` | Đã được yêu cầu trong Administration, là một lần được đề xuất đã được phê duyệt, hoặc là một lần lưu trữ tự động được tạo theo ủy quyền thường trực; phần việc hằng giờ kế tiếp sẽ xuất nó (một lần lưu trữ tự động được tạo ở `APPROVED` thì ngay trong lần khởi động đó). Vẫn có thể hủy. |
| `RUNNING` | Job đang xuất, kiểm tra hoặc sao chép nó, hoặc tạo bản sao lưu trước purge của nó. Không thể hủy. |
| `WAITING_FOR_COPY` | Đã kiểm tra và công bố; đang chờ bản sao ngoài máy chủ hoặc một xác nhận. Có thể hủy. |
| `PURGING` | Job đang xóa các Movement đã lưu trữ của nó, từng lô một. Không thể hủy; một lần purge đã dừng (khởi động lại, hoặc một lỗi database tạm thời như mất kết nối hay deadlock) được lần khởi động kế tiếp tiếp tục. |
| `COMPLETED` | Đã sao chép ra ngoài máy chủ và đã purge: kết quả là `PURGED`. Một lần lưu trữ kết thúc trước khi có việc purge có kết quả `EXPORT_ONLY` (đã xuất, không có gì bị xóa). |
| `COMPLETED_WITH_SKIPS` | Đã kết thúc, nhưng một số thành phần đã thay đổi sau khi export và ở lại trong database; biên nhận liệt kê chúng. Kết quả là `PURGED`, hoặc `NOTHING_PURGED` khi mọi thành phần đều bị bỏ qua. `check.sh` cho qua và nêu các phần bỏ qua. |
| `NOTHING_ELIGIBLE` | Không có gì đủ điều kiện tại mốc cắt (cutoff) của nó khi bước export bắt đầu. |
| `DISMISSED` | Một lần được đề xuất đã bị một người giữ Run history archival and purge bác bỏ. Không có gì được xuất hay bị xóa. |
| `CANCELLED` | Đã bị hủy trong Administration (làm được khi đang `APPROVED` hoặc `WAITING_FOR_COPY`). Thư mục lưu trữ đã công bố vẫn được giữ. |
| `FAILED` | Một kiểm tra thất bại; lý do được hiển thị (với một lần lưu trữ không còn chạy được theo thẩm quyền của nó, cũng là một thất bại về ủy quyền: Các lần lưu trữ tự động, ở trên). Không có gì bị xóa, trừ khi lần lưu trữ thất bại khi đang `PURGING` (số bị xóa khác với các bản ghi của nó, guard từ chối, bản lưu trữ đã công bố bị thay đổi, một lỗi nội bộ; một lỗi database tạm thời thì để nó ở lại `PURGING`): khi đó nó giữ các thành phần trọn vẹn đã purge, và biên nhận của nó liệt kê chúng. |

### Bản sao lưu trước purge (The pre-purge backup)

Ngay trước một lần purge, `archive.sh` tạo một bản sao lưu loại `pre-purge`:
`deploy/production/backup.sh --kind pre-purge --label <archive name> --operator archive.sh` (§3), có tên
`<UTC stamp>-pre-purge-<archive name>`. Ngay trước một lần xóa, nó tạo một bản với nhãn của yêu cầu xóa,
`--label deletion-<id>-<giờ UTC> --reason "pre-purge backup for deletion request <id>"`, có tên
`<UTC stamp>-pre-purge-deletion-<id>-<giờ UTC>`, trong đó giờ UTC là giây mà yêu cầu được bắt đầu. Nó được công bố và
kiểm tra như mọi bản sao lưu, và bước purge hoặc bước xóa ghi lại tên của nó và thời điểm bản dump của nó bắt đầu.
Khôi phục nó sẽ mang lại phần lịch sử mà lần purge đã xóa, hoặc các bản ghi mà lần xóa đã xóa (§8, Phát hiện của đối
soát sau một lần purge, hoặc sau một lần xóa).

- Khi một bản sao lưu khác đang giữ khóa sao lưu (bản sao lưu hằng giờ, một bản thủ công), bản sao lưu trước purge chờ
  nó, kiểm tra lại mỗi 15 giây, tối đa `--backup-wait-minutes` của `archive.sh` (mặc định 60 phút; `0` bị từ chối
  ngay). Một khóa sao lưu cũ (stale) bị từ chối ngay (§3, Khóa sao lưu và khóa phát hành).
- Khi nó thất bại (một bản sao lưu giữ khóa lâu hơn, một khóa cũ, thiếu dung lượng, một lỗi dump), không có gì bị xóa:
  lần lưu trữ hoặc yêu cầu xóa ở lại `RUNNING`, lần khởi động kế tiếp chuyển nó sang `FAILED` (`recover`), và phải yêu
  cầu lại một lần lưu trữ mới, hoặc việc xóa. `archive.sh` thoát với mã 2. Đây là chủ ý: một database được khôi phục từ
  một bản sao lưu trước purge cũng giữ lần lưu trữ hoặc yêu cầu xóa của nó ở `RUNNING`, và `recover` phải chuyển nó
  sang thất bại để nó không bao giờ bị purge hay xóa lại.
- Các bản sao lưu trước purge chiếm dung lượng trong thư mục sao lưu, mỗi bản một bản dump. Việc xoay vòng các bản sao
  lưu theo lịch giữ **Pre-purge backups kept** bản mới nhất của Administration → Backups (ban đầu là 3, từ 1 đến 100),
  của các lần lưu trữ và của các yêu cầu xóa gộp chung, và xóa các bản cũ hơn (§3, Giữ lại). Vì các lần xóa dùng chung
  con số đó, một loạt lần xóa sau một lần purge có thể xoay vòng mất bản sao lưu trước purge của lần purge đó: hãy tăng
  con số này trước một đợt xóa dữ liệu thử nghiệm nếu bản sao lưu đó còn quan trọng.
- Bản sao ngoài máy chủ của thư mục sao lưu (Hyper Backup trên NAS) phải bao gồm chúng như mọi bản sao lưu.
- Khi một lần lưu trữ đã được purge, thư mục lưu trữ của nó và bản sao ngoài máy chủ của bản lưu trữ là những bản duy
  nhất của phần lịch sử đó, ngoài các bản sao lưu trước purge còn được giữ: hãy giữ cả hai bản cẩn thận như các bản sao
  lưu.

### Lấy lại dung lượng đĩa sau một lần purge (Returning disk space after a purge, optional)

Một lần purge giải phóng các dòng của những Movement đã xóa bên trong bảng: vacuum tự động của PostgreSQL cho các
Movement mới dùng lại chỗ đó, nên database ngừng lớn lên, nhưng các tệp của nó trên đĩa không nhỏ đi. Chỉ
`VACUUM FULL part_movements` trả dung lượng lại cho đĩa. Lệnh này ghi một bản sao mới của bảng và các index của nó,
**khóa độc quyền bảng trong khi chạy** (mọi trang, mọi lần quét và mọi lệnh đọc hoặc ghi Movement đều phải chờ), và cần
dung lượng trống trên volume database lớn bằng bảng cùng các index của nó. Chỉ chạy nó khi cần dung lượng đĩa, khi ứng
dụng đã dừng và sau một bản sao lưu:

1. Chọn một lúc vắng. Kiểm tra rằng job lưu trữ lịch sử không làm việc (không có
   `~/partflow-deployments/.archive.lock`), và tắt tác vụ của nó cho đến khi xong.
2. So sánh kích thước của bảng cùng các index (lệnh thứ nhất, theo KiB) với dung lượng trống của volume database (cột
   `Available` của lệnh thứ hai, theo KiB); dung lượng trống phải lớn hơn:

   ```sh
   $PF exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "SELECT pg_total_relation_size('"'"'part_movements'"'"') / 1024"'
   $PF exec -T db df -Pk /var/lib/postgresql/data
   ```

3. Dừng ứng dụng, tức là tạm dừng ghi (§5): `$PF stop backend`.
4. Tạo một bản sao lưu:
   `deploy/production/backup.sh --kind manual --operator "<your name>" --reason "before VACUUM FULL"`.
5. Chạy nó; việc này có thể kéo dài với một bảng lớn:

   ```sh
   $PF exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "VACUUM (FULL, VERBOSE, ANALYZE) part_movements"'
   ```

6. Khởi động lại ứng dụng (`$PF up -d backend`), kiểm tra health (§2) và chạy đối soát (§7). Bật lại tác vụ lưu trữ lịch
   sử.

### Bản sao ngoài máy chủ và việc xác nhận (The off-host copy and its confirmation)

Một bản lưu trữ chỉ nằm trên máy chủ sẽ mất cùng máy chủ, nên mỗi lần lưu trữ sao chép bản lưu trữ của nó ra ngoài
máy chủ trước khi bị purge. Có hai cách:

- **Có thư mục sao chép** (`PARTFLOW_ARCHIVE_COPY_DIR`, [`../DEPLOYMENT.vi.md`](../DEPLOYMENT.vi.md) §3.1): một thư mục
  trên **thiết bị khác**, như một thư mục chia sẻ qua mạng hoặc một ổ USB được mount trên máy chủ. Job sao chép bản
  lưu trữ vào đó, đọc lại bản sao, kiểm tra mọi SHA-256 và tiếp tục với bản sao lưu trước purge và việc purge mà không
  cần người; sau khi purge, nó cũng sao chép biên nhận purge tới đó và kiểm tra nó. `archive.sh` chỉ
  mount thư mục sao chép khi nó tồn tại, ghi được, và nằm trên một hệ thống tệp không phải của thư mục lưu trữ và không
  phải hệ thống tệp gốc `/` của máy chủ. Nó kiểm tra lại điều này trước mỗi bước, nên một ổ đĩa hoặc thư mục chia sẻ
  bị mất kết nối trong một lần export dài được phát hiện trước bước copy, và việc mount không bao giờ tạo ra một thư mục
  bị thiếu. Khi một ổ đĩa hoặc thư mục chia sẻ chưa được mount, đường dẫn của nó là một thư mục rỗng trên hệ thống tệp
  chứa thư mục mount, thường là `/`; khi đó job không sao chép gì, ghi lý do trong đầu ra, và lần lưu trữ chờ. Khi thư
  mục có lại, phần việc hằng giờ kế tiếp sẽ sao chép bản lưu trữ.
- **Không có thư mục sao chép**, lần lưu trữ chờ ở `WAITING_FOR_COPY`:
  1. Sao chép thư mục `<archive-dir>/<archive name>` ra ngoài máy chủ. Trên NAS, thư mục lưu trữ nằm trong tác vụ
     Hyper Backup ([`SYNOLOGY_NAS.vi.md`](./SYNOLOGY_NAS.vi.md) §11.3): hãy chờ lần chạy thành công kế tiếp của nó.
     Nếu không, sao chép bằng `scp -r` hoặc sang một ổ USB.
  2. Kiểm tra bản sao tại nơi nó đang nằm (Kiểm tra một bản lưu trữ, bên dưới).
  3. Trong Administration → History archival & purge, so sánh SHA-256 của manifest hiển thị cho lần lưu trữ với của bản
     sao (`sha256sum manifest.json`), rồi chọn **Confirm copy…** trên lần lưu trữ đó, đánh dấu
     **The archive folder is copied off the server** và chọn **Confirm copy**.
  4. Phần việc hằng giờ kế tiếp tiếp tục lần lưu trữ: bản sao lưu trước purge và việc purge. Job không sao chép biên
     nhận purge cho lần lưu trữ như vậy: biên nhận nằm cạnh thư mục lưu trữ, nên lần chạy kế tiếp của tác vụ Hyper
     Backup sao chép nó ra ngoài máy chủ cùng thư mục.

Một lần lưu trữ chờ xác nhận lâu hơn thiết lập **Report a waiting run after** trong Administration (ban đầu là
7 ngày) khiến `check.sh` báo
`FAIL archival_proposal copy_confirmation_overdue`; cùng thiết lập đó giới hạn thời gian một lần lưu trữ được đề xuất
chờ phê duyệt (`approval_overdue`, Các lần lưu trữ tự động, ở trên). Một lần lưu trữ đang chờ không bao giờ chặn một
bản phát hành.

### Một lần lưu trữ thất bại (A failed run)

Một lần lưu trữ trở thành `FAILED` khi một kiểm tra thất bại: ví dụ mốc cắt trở nên mới hơn thời hạn ngắn nhất được
phép, số đếm vượt quá bản xem trước, các tệp khác với database, thư mục đã công bố bị thay đổi hoặc biến mất, lần lưu
trữ không còn chạy được theo thẩm quyền của nó (user đã yêu cầu, đã phê duyệt nó hoặc đã tắt bước phê duyệt không còn
hoạt động hoặc không còn giữ Run history archival and purge, hoặc bước phê duyệt đã được bật lại sau khi một lần lưu
trữ tự động được tạo mà không cần phê duyệt), các thiết lập không còn cho phép một lần lưu trữ tự động được tạo mà
không cần phê duyệt chạy (**Automatic runs** đã bị tắt, hoặc thời hạn lưu giữ không còn cho phép mốc cắt của nó: Các
lần lưu trữ tự động, ở trên), hoặc job dừng khi lần lưu trữ đang `RUNNING` (kể cả sau một bản sao lưu trước purge thất
bại). Khi đó `archive.sh` thoát với mã 3 một lần, và `check.sh` báo `last_run_failed`, hoặc `authorization_failed` với
một lần lưu trữ không còn chạy được theo thẩm quyền của nó, cho đến khi một lần lưu trữ sau kết thúc.

1. Đọc lý do: trong Administration, trong email báo lỗi, hoặc trong `<records-dir>/archive/<UTC>/` (tệp `.json` và
   `.log` của bước đó).
2. Sửa nguyên nhân, ví dụ giải phóng dung lượng trong thư mục lưu trữ hoặc thư mục sao lưu, hoặc mount thư mục sao
   chép. Khi user của ủy quyền thường trực làm lần lưu trữ thất bại, một người giữ Run history archival and purge lưu
   lại thiết lập phê duyệt (Các lần lưu trữ tự động, ở trên).
3. Yêu cầu một lần lưu trữ mới trong Administration, hoặc để các lần lưu trữ tự động tạo lần kế tiếp. Một lần thất bại
   không bao giờ được tiếp tục; lần mới ghi một thư mục lưu trữ mới.

Không có gì bị xóa, trừ khi lần lưu trữ thất bại khi đang `PURGING` (số bị xóa khác với các bản ghi của nó, guard từ
chối, bản lưu trữ đã công bố bị thay đổi, một lỗi nội bộ): khi đó nó giữ các thành phần trọn vẹn đã purge, biên nhận
`<archive name>.purged.json` của nó (trạng thái `FAILED`) liệt kê chúng là `PURGED` và phần còn lại là `RETAINED` hoặc
`SKIPPED`, và đối soát đã chạy sau nó như sau mọi lần purge. Một lần lưu trữ mới sẽ lưu trữ và purge phần còn lại. Một
lỗi database tạm thời trong lúc purge không bao giờ làm lần lưu trữ thất bại: nó ở lại `PURGING`, và lần khởi động kế
tiếp tiếp tục nó (Job lưu trữ làm gì, ở trên). Một thư mục mà lần thất bại đã công bố vẫn giữ nguyên; hãy giữ nó, và
không bao giờ xóa thư mục hay biên nhận của một lần lưu trữ đã purge bất cứ thứ gì. `recover` xóa các thư mục và tệp
biên nhận dở dang trong `.partial/` của những lần đã kết thúc và giữ mọi mục có tên không thuộc về lần lưu trữ nào; mục
như vậy được nêu tên trong đầu ra: hãy xem xét và xóa nó bằng tay. Không bao giờ thay đổi hoặc xóa một thư mục đã công
bố hay một biên nhận purge của một lần `COMPLETED` hoặc `COMPLETED_WITH_SKIPS`.

### Kiểm tra một bản lưu trữ (Checking an archive)

Mỗi thư mục lưu trữ chứa `movements.jsonl.gz`, `flows.jsonl.gz`, `lineage.jsonl.gz`, `assigned_routes.jsonl.gz`,
`context.json`, `manifest.json`, `README.txt` và `SHA256SUMS`. Chỉ cần các công cụ chuẩn để kiểm tra nó, dù thư mục ở
đâu (thư mục lưu trữ, thư mục sao chép hoặc một bản sao đã khôi phục):

```sh
cd <archive-dir>/run-<id>-<UTC time>
sha256sum -c SHA256SUMS
```

Mọi dòng phải kết thúc bằng `OK`. SHA-256 của `manifest.json` (`sha256sum manifest.json`) là SHA-256 của manifest mà
Administration hiển thị. Các tệp đọc được bằng `zcat` và `jq`; `README.txt` mô tả chúng. Lịch sử đã lưu trữ không bao
giờ được nhập trở lại các bảng đang dùng.

Cạnh thư mục của mỗi lần lưu trữ đã purge là **biên nhận purge** (purge receipt) của nó
`<archive-dir>/run-<id>-<giờ UTC>.purged.json` (mode 0600; cũng có trong thư mục sao chép với một lần lưu trữ mà job đã
sao chép tới đó): một dòng JSON, đọc bằng `jq .`. Nó nêu lần lưu trữ, kết quả và bản sao lưu trước purge của lần đó,
và liệt kê mọi thành phần với kết cục (`PURGED`, `SKIPPED` hoặc `RETAINED`), các Part Number, các flow và số Movement
của nó. Administration hiển thị SHA-256 của nó, giá trị mà `sha256sum <archive name>.purged.json` cho ra.
`sha256sum -c SHA256SUMS` chỉ kiểm tra thư mục lưu trữ: biên nhận nằm ngoài thư mục đó. Sau một lần purge, thư mục
lưu trữ, biên nhận của nó và các bản sao ngoài máy chủ của chúng là những bản duy nhất của phần lịch sử đó ngoài các bản
sao lưu: không bao giờ xóa chúng.

### Chuyển sang máy chủ mới (Moving to a new host)

Thư mục lưu trữ thuộc về bản cài đặt: không bản sao lưu database nào chứa các bản lưu trữ. Khi PartFlow chuyển sang
máy chủ khác (§6, Chuyển sang máy chủ khác), hãy sao chép toàn bộ thư mục lưu trữ sang máy chủ mới, giữ chủ sở hữu và
mode 0700 của nó, và đặt `PARTFLOW_ARCHIVE_DIR` ở đó trước lần lưu trữ đầu tiên. Tạo tác vụ lưu trữ lịch sử trên máy
chủ mới ([`SYNOLOGY_NAS.vi.md`](./SYNOLOGY_NAS.vi.md) §11.8), và xóa nó trên máy chủ cũ.

## Lịch sử thay đổi

| Ngày | Commit | Thay đổi |
| --- | --- | --- |
| 2026-11-18 | uncommitted | §11: các lần lưu trữ tự động: `evaluate` ở đầu phần việc hằng giờ và dòng `EVALUATE` của `archive.sh`, các trạng thái `PROPOSED` và `DISMISSED`, việc phê duyệt và bác bỏ trong Administration, ủy quyền thường trực và việc kiểm tra lại thẩm quyền của một lần lưu trữ, việc này cũng chặn một lần lưu trữ được tạo mà không cần phê duyệt trước khi nó purge khi Automatic runs bị tắt, bước phê duyệt được bật lên hoặc thời hạn lưu giữ không còn cho phép mốc cắt của nó; §2, §8 và §11: `approval_overdue`, `authorization_failed` và `standing_authorization_invalid`; §1, §8, §9, §10 và bảng `pf` cho việc đánh giá và các lần lưu trữ được đề xuất. |
| 2026-11-18 | `1874636` | §10: các yêu cầu xóa vĩnh viễn, được tạo trong PartFlow bởi người giữ Delete Work Orders and Part Numbers permanently hoặc bằng `pf delete-permanently`, được job lưu trữ lịch sử thực hiện trong vòng 5 phút sau một bản sao lưu trước purge, và cần job đó đã được thiết lập; các dòng audit của các lần xóa trước được giữ lại; §11: kiểm tra nhẹ ở mỗi lần khởi động, các bước xóa, nhãn sao lưu `deletion-<id>-<giờ UTC>`, dòng `DELETION` và mọi mã thoát 2 của `delete` được làm rõ qua `status`; §8: sự cố của một phát hiện đối soát sau một lần xóa, và `deletion_request_waiting`; §3 và §11: các lần xóa và các lần purge dùng chung Pre-purge backups kept; §6: hủy các yêu cầu `APPROVED` không còn cần sau một lần khôi phục; §2, §7, §9 và bảng `pf` cho các yêu cầu. |
| 2026-11-18 | `d094f5e` | §6 và §8: tắt tác vụ lưu trữ lịch sử trước mọi lần khôi phục, vì một lần lưu trữ `PURGING` được khôi phục sẽ tiếp tục purge; §8 và §11: một lỗi database tạm thời để lần purge ở lại `PURGING` cho lần khởi động kế tiếp, lần đó đối soát khi nó kết thúc. |
| 2026-11-18 | `d094f5e` | §11: job lưu trữ lịch sử purge lịch sử Movement đã lưu trữ sau bản sao ngoài máy chủ và một bản sao lưu trước purge, rồi đối soát; các trạng thái `PURGING` và `COMPLETED_WITH_SKIPS`, các kết quả `PURGED` và `NOTHING_PURGED`, biên nhận purge, việc tiếp tục một lần purge, bản sao lưu trước purge và thời gian chờ của nó, và lấy lại dung lượng đĩa bằng `VACUUM FULL`; §8: sự cố của một phát hiện đối soát sau một lần purge, trong lúc đó job lưu trữ không làm việc với lần lưu trữ nào; §3: loại sao lưu `pre-purge`, việc xoay vòng nó và `--lock-wait-minutes`; §1, §2, §7, §9, §10 và bảng `pf` cho việc purge. |
| 2026-11-17 | `2f12d33` | §11 và bảng `pf`: job lưu trữ lịch sử (`archive.sh`, `pf archive`), chỉ xuất và sao chép bản lưu trữ và không bao giờ xóa gì; §1, §2, §6, §8 và §9 cho hồ sơ, các dòng kiểm tra, việc chuyển máy chủ, sự cố và lịch làm việc của nó. |
| 2026-11-17 | `4539a2b` | Lệnh khôi phục từ chối mật khẩu vi phạm quy tắc mật khẩu, và các thiết lập đăng nhập chuyển sang Administration → Security. |
| 2026-11-17 | `01ce427` | §10 và bảng `pf`: `pf delete-permanently` xóa các Work Order và Part Number chỉ nhập để thử nghiệm, sau một lần xem trước và một bản sao lưu đã kiểm tra. |
| 2026-11-17 | `1fc96e1` | Các vấn đề thường gặp và Đăng nhập và tài khoản người dùng: mất một passkey, và một lần reset cũng xóa các passkey. |
| 2026-11-17 | `aa6287d` | Các vấn đề thường gặp và Đăng nhập và tài khoản người dùng: mất app xác thực của xác thực hai bước, đăng xuất user ở mọi nơi, và khóa sau các mã sai. |
| 2026-11-16 | `a4cf9a8` | §7: dạng reconcile cho một instance staging của Deployment Admin được bỏ cùng với công cụ đó. |
| 2026-11-16 | `5708664` | Trước khi chạy bất kỳ lệnh nào: lệnh ngắn `deploy/production/pf`. |
| 2026-11-16 | `c1f042b` | §2, §3, §5, §8, §9: tác vụ sao lưu chạy `backup.sh --kind scheduled` mỗi giờ; Administration → Backups đặt khoảng thời gian và số bản sao lưu hourly, daily và weekly được giữ; giới hạn `backup_age` đi theo khoảng thời gian đó. |
| 2026-11-14 | `2a24c2a` | §5: lần phát hành dừng trước khi tạm dừng ghi khi `revision` của image mới báo `blocked_by_data`. |
| 2026-11-13 | `4a99914` | §2: các lần Production Board tự làm mới thành công không còn nằm trong log `web`. |
| 2026-11-12 | `de4d99f` | §5 Observe: Scan Station hoặc Production Board tự tải lại sau 10 giây đếm ngược trong thông báo thay vì sau 60 giây, và mọi trang khác khi rảnh 1 phút thay vì 2 phút; §2: mỗi Production Board đang mở thêm khoảng 9 MB log `web` mỗi ngày thay vì 3 MB. |
| 2026-11-12 | `77a2dfb` | §5 Observe: các trang đang mở trong lúc phát hành nay tự tải lại (Scan Station hoặc Production Board sau 60 giây, mọi trang khác khi rảnh 2 phút và không còn gì chưa lưu), nên không ai phải đi tới từng máy. |
| 2026-11-12 | `3fb7201` | Thêm mục đăng nhập và tài khoản người dùng: lấy lại mật khẩu hoặc login name đã quên, đổi tên đăng nhập, thu hồi quyền truy cập của một user, và cách khôi phục khi không còn Administrator nào. |
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
