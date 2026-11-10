# Runbook vận hành PartFlow

> **Bản gốc:** [`OPERATIONS_RUNBOOK.md`](OPERATIONS_RUNBOOK.md). Đồng bộ với bản tiếng Anh tại commit `2c360cb` cộng
> các thay đổi chưa commit ngày 2026-11-09. Bản tiếng Anh là source of truth.

Runbook này là sổ quy trình của operator cho production stack của PartFlow: cần ghi lại gì cho mỗi release, cách kiểm
tra health và tìm ra sự cố, cách lấy và kiểm thử backup, cách release và rollback, cách chạy reconciliation, cần làm gì
khi có incident và việc gì làm theo lịch định kỳ. Tài liệu viết cho người vận hành PartFlow trên Synology NAS của công
ty; người này vừa là operator vừa là approver. Hãy đọc trước [`../DEPLOYMENT.md`](../DEPLOYMENT.md) §3.1 (production
stack) và §5 (go-live cần gì), và [`SYNOLOGY_NAS.md`](SYNOLOGY_NAS.md) §8 cho việc cài đặt trên NAS.

**Trạng thái.** Mọi command trong runbook này đã có trong repository và đã được chạy trên Docker Desktop và trong một
Linux container, nhưng chưa chạy trên Synology NAS. Các lịch chạy (daily backup, `check.sh` và reconciliation theo lịch)
được khuyến nghị sau go-live và không bao giờ chặn việc sử dụng (`DEPLOYMENT.md` §5).

**Quy ước dùng trong mọi phần.**

- `PF` là một biến shell mà bạn đặt trong release checkout trước khi chạy các command:

  ```bash
  PF="docker compose -f compose.production.yaml --env-file .env.production"
  ```

  Nó không phải tool Deployment Admin `pf`. Mọi command `$PF` và mọi script `deploy/production/*.sh` chạy từ
  repository root của một release checkout: checkout của release **đang chạy** cho công việc hằng ngày, checkout của
  release **candidate** trong lúc release.
- Chạy các command bằng account sở hữu backup directory (`PARTFLOW_BACKUP_DIR` trong `.env.production`). Directory đó
  có mode 0700, nên container one-off nào đọc nó đều được start với `--user "$(id -u):$(id -g)"`; thiếu option này
  container không đọc được nó.
- `<partflow-host>` là host name người dùng gõ vào trình duyệt, `<backup-dir>` là `PARTFLOW_BACKUP_DIR`, và
  `<records-dir>` là directory chứa deployment record (mặc định `$HOME/partflow-deployments`).
- Thời gian tính theo giờ phối hợp quốc tế (UTC). Report và record là tài liệu JavaScript Object Notation (JSON).

## 1. Deployment record bắt buộc

Mỗi release và mỗi thao tác thủ công (ví dụ một rollback) đều để lại một record. `deploy/production/release.sh` ghi nó
thành `record.json`, cùng output của từng bước, trong `<records-dir>/<UTC>-<tag>/`; một thao tác thủ công ghi thêm file
của nó vào cùng directory với cùng các trường. Evidence của restore drill (`evidence.json`, §4), `rollback.json` của
path 3 và `restore.json` của new-instance restore (§6) được ghi dưới cùng records directory.

| Trường | Key trong `record.json` |
| --- | --- |
| Environment và URL | `environment`, `url` (option `--environment`, `--url`) |
| Host | `host` (host name) |
| Release tag, commit và image | `release`: `tag`, `commit`, `previous_tag` và image ID local của `backend` và `web` |
| Alembic revision trước và sau | `alembic`: `before`, `after`, `expected` |
| Operator và approver | `operator`, `approver` (một người có thể ghi cùng một tên cho cả hai) |
| Thời gian bắt đầu và kết thúc (UTC) | `started_at`, `finished_at` |
| Pre-release backup: path, checksum và kết quả verify | `backup`: `kind`, `reference`, `name`, `path` tuyệt đối, `verified`, `verification`, `freshness`, `taken_by` (`release.sh`, `operator` hoặc `null`), `manifest_sha256` và các thông tin manifest ở §3 |
| Output của migration, grants report và report database-role | `migration`: các file `migrate.json` (chứa kết quả `grants`) và `migrate.log`; JSON report của một lần chạy `provision-roles` hoặc `apply-grants` thủ công được giữ bên cạnh |
| Kết quả smoke và reconciliation | `reconcile`, `smoke` |
| Deadline rollback và người theo dõi (observation owner) | `rollback_deadline`, `observation_owner` (option `--rollback-deadline`, `--observation-owner`) |
| Giới hạn đã biết | `known_limitations` (option `--known-limitations`) |
| Lần chạy kết thúc thế nào | `freeze_completed_at` (thời điểm xác nhận `backend` đã dừng; `null` khi không có freeze), `outcome`, `writes_reopened_at`, `refrozen` |

## 2. Health và chẩn đoán

### Kiểm tra nhanh

Trên development stack (`compose.yaml`):

```bash
docker compose ps
docker compose logs --since=15m backend frontend db
curl --fail --silent --show-error https://<partflow-host>/api/health
```

Trên production stack:

```bash
$PF ps
$PF logs --since=15m backend web db
curl --fail --silent --show-error https://<partflow-host>/api/health
curl --fail --silent --show-error https://<partflow-host>/api/health/live
$PF run --rm --no-deps -T backend python -m app.cli revision
```

`/api/health` là readiness. Nó báo `release`, `commit`, `schema` (`current`, `accepted`, `mismatch` hoặc `unknown`),
`expected_revision`, `database_revision` và `accepted_revision`, và trả 503 khi schema mismatch hoặc khi không kết nối
được database. `/api/health/live` chỉ là liveness và được health check của container dùng, nên container vẫn healthy
trong khi readiness là 503 lúc schema mismatch. `revision` in cùng các thông tin đó từ một container one-off và vẫn chạy
được khi `backend` đang dừng.

### Host check và command `status`

Chạy các lệnh này từ checkout của release đang chạy:

```bash
# manual diagnosis: the same checks as the scheduled run, without touching its state
deploy/production/check.sh --url https://<partflow-host> --no-state
# database size, connections, lock waits, Movement rows and size, schema readiness, backup age
$PF --profile ops run --rm --no-deps -T --user "$(id -u):$(id -g)" status
docker stats --no-stream
```

**Luôn thêm `--no-state` khi chạy `check.sh` thủ công.** Thiếu nó, lần chạy thủ công sẽ đẩy error cursor và restart
baseline lên, nên lần chạy theo lịch kế tiếp sẽ bỏ sót các error và restart mà lần chạy thủ công đã thấy.

`check.sh` in một dòng cho mỗi check, `PASS|FAIL|SKIP <id> <reason>`, theo thứ tự sau, rồi đến các dòng `NOTE` tùy
chọn:

| Check | Thất bại khi |
| --- | --- |
| `https` | `/api/health` không trả 200 với `schema` `current` và release của `.env.production` |
| `certificate` | certificate hết hạn trong vòng 21 ngày (`--min-cert-days`) |
| `containers` | không có đúng một `db`, `backend` và `web` đang chạy và healthy |
| `restarts` | một restart count tăng kể từ lần chạy đầy đủ trước |
| `errors` | backend đã ghi một record `ERROR` hoặc `CRITICAL` kể từ lần chạy đầy đủ trước (hiện tối đa ba request id) |
| `disk_data` | database volume còn trống dưới 15 % (`--min-free-percent`) |
| `disk_backup` | backup directory còn trống dưới 15 % |
| `disk_docker` | Docker root (image và log của container) còn trống dưới 15 % |
| `disk_archive` | archive directory còn trống dưới 15 %; `SKIP` khi chưa cấu hình archive directory (`--archive-dir`) |
| `database` | lần chạy `status` không chạy được, không trả lời trong 120 s, hoặc báo lỗi database |
| `schema` | `status` báo schema chưa sẵn sàng (`schema_not_ready`) |
| `backup_age` | không tìm thấy published backup nào, không đọc được backup directory, hoặc published backup mới nhất thuộc bất kỳ loại nào đã hoàn tất cách đây hơn 26 giờ (`--max-backup-age-hours`) |
| `archival_proposal` | luôn là `SKIP`: archival cho Movement history chưa được triển khai |

Bốn check cuối lấy từ một lần chạy `status` read-only. Khi một release giữ release lock, hoặc `release.sh` giữ backup
lock, bốn check này là `SKIP`. Khi lock đó đã cũ hơn 4 giờ, lần chạy báo `FAIL database` kèm tên lock, vì một
`release.sh` bị kill sẽ để lại lock; gỡ nó như phần Backup lock của §3 mô tả. Hãy lên lịch `check.sh` cùng lúc với daily
backup: không có daily backup thì `backup_age` thất bại ngay khi backup gần nhất cũ hơn 26 giờ.

Exit code: 0 không có gì cần báo, 1 có ít nhất một `FAIL` cần báo, 2 check không chạy được (trường hợp này cũng báo).
Một tập failure không đổi chỉ được báo lại sau 6 giờ (`--renotify-hours`); output khi đó ghi `already reported`.
`deploy/production/check.sh --help` in mọi option, gồm `--cacert` cho một tổ chức cấp chứng chỉ (certificate authority,
CA) riêng và `--resolve-to` khi host không phân giải được tên của chính nó.

`check.sh` giữ state trong `~/partflow-monitoring` (`--state-dir`, mode 0700): `restarts` (restart baseline),
`errors-since` (log cursor), `alert-state` (báo lại), `last-check.txt` (mỗi lần chạy), `status.json`, `status.err` và
`growth.tsv` (mỗi ngày một dòng có ngày: số byte của database, số dòng Movement và số byte của Movement).

Check `errors` chỉ đọc log của container `backend` hiện tại, và Docker xóa log của container cùng với container. Khi
`backend` đã được tạo lại kể từ lần chạy đầy đủ trước (do một release hoặc một lần rotate role password), lần chạy thêm
`NOTE errors backend was recreated ...`, và các record của container bị thay thế sau cursor không còn đọc được nữa.
**Trước khi tự tạo lại `backend`**, hãy chạy
`deploy/production/check.sh --url https://<partflow-host> --only errors --no-state` và giữ lại những gì nó báo.

`status` in một tài liệu JSON có `result` là `ok`, `attention` hoặc `error`, với exit code 0, 1 (một finding như
`backup_stale` hoặc `schema_not_ready`) hoặc 2 (`configuration_invalid`, `database_unavailable`, `lock_timeout`,
`statement_timeout`, `database_error`, `internal_error`). `database.locks` chứa `waiting` và `longest_wait_seconds`;
con số lock wait này chỉ được báo, không bao giờ là ngưỡng. `status` chạy một transaction read-only bằng application
role với lock timeout 5 s. Giữ `--statement-timeout` của nó (mặc định 20 s) dưới 30 s để nó không bao giờ làm hỏng
`migrate` của một release.

### Log và HTTP request id

Trong production stack, backend ghi mỗi dòng một JSON object: `ts`, `level`, `logger`, `message`, `request_id`, rồi đến
các trường riêng của record. Mọi response, kể cả từ chối và lỗi, đều mang **HTTP request id** trong header
`X-Request-ID`. Giá trị riêng của client được giữ khi nó dài 1 đến 64 ký tự thuộc `A-Za-z0-9._-`; nếu không, backend tự
tạo một giá trị. Mọi backend record của request đó mang nó trong `request_id`. Để tìm một request:

```bash
$PF logs --no-log-prefix backend | grep '"request_id":"<id>"'
```

HTTP request id đổi sau mỗi lần gửi lại. Vì vậy một production command luôn được đối chiếu và retry bằng
`device_event_id` của nó ("request identity" trên giao diện), không bao giờ bằng `request_id`. Để theo dõi một command,
một Part Number (PN) hoặc một Scan Station, lọc các record `app.access` theo các trường chúng mang:

```bash
$PF logs --no-log-prefix backend | grep '"device_event_id":"<id>"'
$PF logs --no-log-prefix backend | grep '"part_number":"<PN>"'
$PF logs --no-log-prefix backend | grep -E '"quantity_flow_id":<n>[,}]'
$PF logs --no-log-prefix backend | grep -E '"(area|source_area|target_area|starting_area)_id":<n>[,}]'
$PF logs --no-log-prefix backend | grep -E '"work_order_demand_id":<n>[,}]|"work_order_demand_ids":\[([0-9]+,)*<n>[],]'
$PF logs --no-log-prefix backend | grep '"station_id":"<id>"'
```

Một id dạng số được khớp cùng dấu `,` hoặc `}` đóng sau nó để `1` không khớp luôn cả `10`. Một Area cũng được khớp khi
là Area nguồn, Area đích hoặc Area bắt đầu của một command, và một Work Order Demand cũng được khớp bên trong danh sách
`work_order_demand_ids` của một allocation.

Một record `app.access` chứa `event`, `method`, `route` (route template; chỉ là `path` khi không khớp route nào),
`status`, `duration_ms`, `client`, `outcome` (`ok`, `created`, `replayed`, `refused` hoặc `error`), `slow`, `refusal`
(`type`, `code`, `message`) và `context`: PN, QuantityFlow, quantity, Area, Operation, Machine, Work Order, Work Order
Demand (các dòng của một allocation dưới dạng `work_order_demand_ids`), `device_event_id`, Scan Station, `user_id` và
`worker_id` mà request nêu ra. `worker_id` chỉ xuất hiện trên record của command đã đi tới bước xác định danh tính (một
command được tạo hoặc một từ chối về danh tính); Worker của một command được replay nằm trong Movement của nó.

Những gì được ghi log và ở level nào:

- Không bao giờ ghi log: body của request hoặc response, query string, cookie, device token, token chống giả mạo
  request liên trang (cross-site request forgery, CSRF), badge, password, giá trị được scan và các dòng `DETAIL` của
  PostgreSQL (database chạy với `log_error_verbosity=terse`). Ngoại lệ duy nhất là first-run setup token, được in một
  lần cho đến khi setup hoàn tất.
- Các lần đọc thường lệ nhanh hơn 1 giây và mọi health poll không được ghi ở `INFO`; `"slow":true` đánh dấu một lần đọc
  mất 1 giây trở lên.
- Các từ chối có chủ đích (`not_ready`, `release_mismatch`, `password_check_busy`, một request bị từ chối) là `INFO`;
  chỉ lỗi thật mới là `ERROR`.
- `web` ghi mỗi request một edge record JSON: địa chỉ client, method, path không có query string, status, số byte,
  thời lượng, upstream status, user agent và cùng `request_id`. Cả `web` lẫn backend đều không ghi query string,
  cookie hay header riêng của PartFlow.

Một câu trả lời JSON 502 hoặc 504 đến từ `web` (`server_unavailable`) và có nghĩa là kết quả của một write chưa rõ. Tìm
nó theo `request_id` trong log của `web`, và xác định kết quả write bằng `device_event_id` gốc. **Không retry một
production command bị timeout bằng `device_event_id` mới** cho đến khi biết kết quả gốc; chỉ retry bằng idempotency key
gốc, để một câu trả lời chưa chắc chắn không bao giờ làm nhân đôi một write.

### Thời gian giữ log

Mỗi container giữ tối đa 5 file log json-file, mỗi file 10 MB. Lượng đó chứa được bao nhiêu ngày tùy vào tải:

- Command của station: khoảng 1,1 MB log backend và 0,6 MB log `web` cho mỗi 1.000 command, đo bằng tải giả lập theo
  output của `docker compose logs`.
- Màn hình polling: mỗi Production Board, Area Board hoặc màn hình Tracking đang mở refresh mỗi 15 s, và mỗi request là
  một record của `web`. Production Board gửi 2 request mỗi lần refresh, khoảng 11.500 record hay chừng 3 MB log `web`
  mỗi ngày cho mỗi board đang mở. Backend chỉ ghi các lần đọc này khi chúng mất 1 giây trở lên.
- Trên đĩa, json-file driver bọc và escape mọi dòng, nên một file chứa ít record hơn các con số trên.

Riêng command (5.000 mỗi ngày) sẽ cho khoảng 9 ngày log backend và 18 ngày log `web`; với ba Production Board mở cả
ngày, log `web` chỉ giữ được vài ngày. **Sao chép những gì một cuộc điều tra cần ngay khi nó bắt đầu.** Hãy đo con số
thật trên NAS, với các màn hình thật của nó.

### Lỗi quyền database

Trong production stack, backend kết nối bằng database role `partflow_app`. Một `permission denied` (SQLSTATE 42501)
trong backend log nghĩa là một đường code đã cố thay đổi history được bảo vệ hoặc thiếu một grant. Đó là một incident
(§8) và không bao giờ được sửa bằng cách cấp thêm quyền. Chạy `reconcile --check h` (§7) trước mọi việc khác.

### Những gì cần kiểm tra thêm

- CPU, bộ nhớ, đĩa, input/output của đĩa, đồng hồ của host và lần reboot gần đây nếu có;
- restart count và health status của container;
- reverse proxy và hạn của certificate;
- kết nối, lock, mức tăng dung lượng và tuổi backup của PostgreSQL;
- trạng thái kết nối trên trình duyệt, và write có bị chặn đúng hay không;
- lỗi được đối chiếu theo PN, QuantityFlow, Area, Operation, Machine, Worker, Scan Station và `device_event_id`.

## 3. Logical database backup

`deploy/production/backup.sh` lấy backup. Chạy nó từ repository root của một release checkout (checkout của release
đang chạy cho backup theo lịch và backup thủ công; `release.sh` gọi nó từ checkout của candidate) trong khi `db` của
production stack đang chạy. Dump được stream từ **bên trong `db`**
(`pg_dump --format=custom --no-owner --no-privileges --lock-wait-timeout=60s`, một snapshot, client và server là cùng
một binary), được liệt kê bằng `pg_restore --list`, và được publish cùng manifest và checksum bởi service one-shot
`backup-tools`, vốn không có network và chỉ thấy backup directory. Repository cung cấp backup, việc verify và
retention; mã hóa và bản sao ra ngoài host thuộc về tool backup của platform (bên dưới).

### Artifact và tên

Một backup là một directory `<backup-dir>/<NAME>/`:

```text
NAME = <UTC stamp>-<kind>[-<label>]      for example 20261008T020000Z-daily
                                         and 20261008T140000Z-pre-release-v1.0.0-rc.2
```

`kind` là `daily`, `manual` hoặc `pre-release`. `label` là tag của release đích: bắt buộc với `pre-release` và bị từ
chối với loại khác. Stamp là thời điểm UTC `backup.sh` bắt đầu. Directory chứa đúng `partflow.dump`,
`partflow.dump.list` (output của `pg_restore --list`), `manifest.json` và `SHA256SUMS`. Nó được ghép dưới
`<backup-dir>/.partial/` và publish bằng một lần rename atomic, nên một backup hoặc tồn tại đầy đủ hoặc không tồn tại;
một lần crash chỉ để lại các entry trong `.partial/`. Các entry có tên không theo mẫu này (ví dụ folder `@eaDir` của
DiskStation Manager (DSM)) được bỏ qua và báo ra, không bao giờ gây lỗi.

### Lấy backup

```bash
# scheduled daily backup (14 and 8 are placeholders for the owner's retention)
deploy/production/backup.sh --kind daily --operator scheduler --keep-daily 14 --keep-weekly 8
# manual backup (a reason is required)
deploy/production/backup.sh --kind manual --operator "<name>" --reason "<why>"
```

`release.sh` tự lấy backup `pre-release` (§5); dạng thủ công là
`backup.sh --kind pre-release --label <target tag> --tools-release <target tag> --operator "<name>"`. Khi thành công,
standard output là đúng một dòng, `BACKUP <NAME> <absolute path>`; tiến trình và report của tool đi ra standard error.
`deploy/production/backup.sh --help` in mọi option:

| Option | Ý nghĩa |
| --- | --- |
| `--kind daily\|manual\|pre-release`, `--operator NAME` | bắt buộc |
| `--reason TEXT` | bắt buộc với `manual`; mặc định là `scheduled daily backup` (daily) và `pre-release backup for TAG` (pre-release) |
| `--label TAG` | tag của release đích; bắt buộc với `pre-release`, bị từ chối với loại khác |
| `--tools-release TAG` | image `backup-tools` ghi và verify manifest (mặc định: `PARTFLOW_RELEASE` của env file) |
| `--keep-daily N --keep-weekly N` hoặc `--no-rotate` | chỉ cho `daily`, và bắt buộc có một trong hai: retention của owner (bên dưới); không có giá trị mặc định |
| `--reserve-mib N` | dung lượng trống giữ thêm ngoài hai lần dump mới nhất (mặc định 1024) |
| `--lock-held-by-release DIR` | chỉ dành cho `release.sh` (nó giữ backup lock) |
| `--rehearsal --project NAME` | một Compose project dùng một lần (không bao giờ là `partflow-production`) |

Giá trị text dài 1 đến 500 ký tự, không có ký tự điều khiển, `"` hay `\`. Các bước, theo thứ tự, mỗi bước được báo là
`backup: <step> ok (<ms> ms)` trên standard error:

1. `preflight`: các tool, env file (`PARTFLOW_RELEASE`, `PARTFLOW_BACKUP_DIR`), Compose `config`, `db` đang chạy,
   image `backup-tools` và backup lock (bên dưới);
2. `identify`: release đang chạy, commit, image ID và Alembic revision mong đợi (cố gắng hết mức, không bao giờ gây
   lỗi);
3. `space`: backup directory phải còn trống ít nhất hai lần dump mới nhất (hoặc kích thước database khi chưa có dump
   nào) cộng phần dự trữ; nếu không backup bị từ chối và không ghi gì;
4. `dump`, `list` và `revision`: ba lệnh gọi bên trong `db`, chạy với `TZ=UTC` vì archive lưu các trường theo giờ
   local;
5. `manifest` (`backup-manifest` publish directory), `verify` (`backup-verify` kiểm tra nó), `rotate`
   (`backup-rotate`, chỉ cho backup `daily`), rồi `done`.

| Exit | Ý nghĩa | Làm gì |
| --- | --- | --- |
| 0 | hoàn tất | không cần làm gì |
| 1 | bị từ chối trước khi ghi (`backup_running`, `backup_lock_stale`, `insufficient_space`, `name_exists`) | đọc lý do được in; không có gì được ghi |
| 2 | không chạy được (cú pháp, tool, environment, `db` không chạy, không tạo được backup lock) | sửa nguyên nhân và chạy lại |
| 3 | thất bại: không backup nào được publish, hoặc backup đã publish không qua verify | không bao giờ dùng backup mà thông báo nêu tên; xóa nó sau khi xem xét |
| 4 | backup đầy đủ và đã verify, nhưng rotation thất bại hoặc tìm thấy một daily backup không qua verify | xem report của `backup-rotate` trên standard error (xem Retention bên dưới) |

Khi daily backup đã được lên lịch, failure notification của scheduler trên host báo mọi exit khác 0.

### Manifest và verify

`manifest.json` (`manifest_version` 1) chứa các thông tin deployment record của §1 và những gì một lần restore cần:

| Trường record | Key trong manifest |
| --- | --- |
| Timestamp UTC | `dump_started_at`, `completed_at` |
| Environment, host | `environment`, `host` |
| Git commit và image | `release.commit`, `release.tag`, `images.backend`, `images.web`, `images.db` |
| Alembic revision | `alembic_revision` (và `alembic_rows`); `release.expected_revision` là revision release đang chạy mong đợi |
| PostgreSQL major version | `database.server_major` (và `server_version`, `pg_dump_version`, `database.name`) |
| Checksum của dump và list | `files[].sha256` và `SHA256SUMS` |
| Operator và lý do | `operator`, `reason` |

Nó cũng ghi `kind`, `label`, image `tool` đã ghi nó và các thông tin `dump` (`options`, `toc_entries`,
`table_data_entries`, `tables_checked`, `extra_tables`). `tables_checked: true` nghĩa là mọi table trong bảng phân loại
table của release đều có một data entry; dump của một revision khác được publish với `tables_checked: false`. Một
database mà `alembic_version` không chứa đúng một revision hợp lệ, hoặc có table mà release không biết, vẫn được backup
và publish kèm cảnh báo, vì một backup trung thực của database bị lệch vẫn đáng giá hơn không có backup; nhưng `migrate`
từ chối backup đó (§5) và restore drill thất bại ở bước revision.

Verify bất kỳ backup nào, read-only, bằng cùng các check mà `migrate` và drill chạy, và độc lập với mọi image sau mỗi
lần sao chép:

```bash
$PF --profile ops run --rm -T --user "$(id -u):$(id -g)" backup-tools backup-verify "<NAME>"
(cd "<backup-dir>/<NAME>" && sha256sum -c SHA256SUMS)
```

Thêm tiền tố `PARTFLOW_RELEASE=<tag>` vào lệnh thứ nhất để dùng image `backup-tools` của release khác. `backup-verify`
in một tài liệu JSON với các check `directory`, `files`, `sha256sums`, `manifest`, `dump_header`, `list` và
`expect_database` (option `--expect-database NAME`). Exit code của nó là 0 `verified`, 1 `invalid` (`backup_invalid`:
**không dùng backup đó**) hoặc 2 `failed`.

### Backup directory

- Đó là một directory tuyệt đối đã tồn tại, nằm ngoài repository checkout, secrets directory và mọi archive directory.
  Nó **chỉ chứa backup production**, không bao giờ của staging hay của drill. Mode của nó là 0700, owner là account
  chạy `backup.sh` và `release.sh`, và các file có mode 0600. Tạo nó trước lệnh `$PF` đầu tiên, vì mọi Compose
  command của stack đều cần `PARTFLOW_BACKUP_DIR` (`DEPLOYMENT.md` §3.1).
- Một dump chứa mọi table, kể cả password hash và digest của session token và device token. **Coi directory và mọi
  bản sao của nó là bí mật.**
- Giữ dung lượng trống bằng hai lần dump mới nhất cộng phần dự trữ (mặc định 1 GiB).

Theo thiết kế, dump không chứa grant (`--no-privileges`): sau một lần restore, database role và privilege của chúng được
derive lại từ code (§4).

### Retention, bản sao ngoài host và alert

`backup.sh --kind daily` áp dụng `backup-rotate`. Nó cũng có thể chạy riêng; `--dry-run` báo cáo mà không xóa:

```bash
$PF --profile ops run --rm -T --user "$(id -u):$(id -g)" backup-tools backup-rotate --keep-daily 14 --keep-weekly 8 --dry-run
```

Nó giữ `--keep-daily` daily backup mới nhất đã verify, cộng daily backup mới nhất của mỗi tuần trong `--keep-weekly`
tuần lịch mới nhất (tuần ISO 8601, UTC; các tuần chồng lên cửa sổ daily, giống restic
`forget --keep-daily --keep-weekly`). Mọi ứng viên được verify trước; ứng viên nào không qua verify thì không được tính
và **không bao giờ bị xóa** (exit 4: operator xem xét nó, §9). **Pre-release backup và manual backup không bao giờ bị
rotate**: operator xóa chúng sau observation window (§9). **Không tool nào được rotate hay prune một archive
directory**, kể cả tool của platform. Phần còn sót trong `.partial/` cũ hơn 24 giờ bị xóa. Các giá trị 14 và 8 là giá
trị giữ chỗ: owner đặt giá trị thật trong command theo lịch; chúng là option bắt buộc, không bao giờ là mặc định trong
code.

Mã hóa backup directory và sao chép nó ra ngoài host bằng tool của platform, không bao giờ làm bằng tay: Hyper Backup với
mã hóa phía client tới một đích nằm ngoài NAS trên Synology (`SYNOLOGY_NAS.md` §6), restic tới object storage trên một
máy chủ riêng ảo (virtual private server) (`VPS.md` §8).

Alert, khi các lịch chạy đã được cài:

- một lần backup theo lịch thất bại (mọi exit khác 0, kể cả một daily backup không qua verify) kích hoạt failure
  notification của scheduler trên host;
- một lần sao chép ra ngoài host thất bại kích hoạt notification riêng của tool platform;
- "backup quá cũ" là check `backup_age` của `check.sh`: published backup mới nhất thuộc bất kỳ loại nào cũ hơn 26 giờ
  (§2).

Xem các notification này hằng ngày (§9). Với daily backup, mục tiêu điểm khôi phục (recovery point objective, RPO),
tức lượng công việc gần nhất có thể bị mất, là 24 giờ. Cho đến khi daily backup được lên lịch, các backup duy nhất là
các backup `release.sh` lấy trước mỗi release và các manual backup.

### Backup lock

Các lần backup được tuần tự hóa bằng directory `<backup-dir>/.backup.lock`. File `owner` của nó chứa `host`, `pid`,
`started_at`, `by=backup.sh|release.sh` và `name` hoặc `release`. Lần chạy thứ hai bị từ chối, không bao giờ xếp hàng:
`backup_running` nghĩa là một backup, hoặc một release đang giữ lock, đang chạy, nên hãy chờ. `release.sh` giữ lock từ
write freeze (hoặc từ bước backup của nó) cho đến lúc chuyển `backend`. **Lên lịch daily backup ngoài maintenance
window**: một daily backup đang chạy làm release dừng trước freeze mà không đổi gì, và một daily backup bắt đầu trong lúc
release bị từ chối.

Một lock hoàn toàn không tạo được (backup directory đầy, hết inode hoặc read-only) được báo cùng lỗi `mkdir` là
không chạy được (exit 2), không bao giờ là `backup_running`; khi đó `release.sh` dừng với `could_not_run` trước freeze,
không đổi gì.

`backup_lock_stale` nghĩa là lock được để lại bởi một lần chạy không còn tồn tại (nó bị kill, bị trình xử lý hết bộ nhớ
kill, hoặc mất điện). **Nó không bao giờ bị tự động phá.** Khôi phục bằng tay:

```bash
cat "<backup-dir>/.backup.lock/owner"   # host, pid, started_at, by and name|release
ps -p <pid>                             # on that host: must report no such process
$PF exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "SELECT count(*) FROM pg_stat_activity WHERE application_name = '"'"'pg_dump'"'"'"'   # must print 0
rm -r "<backup-dir>/.backup.lock" "<backup-dir>/.partial/<name>"   # the name from the owner file, when it has one
```

**Không bao giờ gỡ một lock có owner là `release.sh` trong khi release đó đang chạy.** Khi `release.sh` giữ backup lock
(hoặc release lock của nó tồn tại), `check.sh` bỏ qua các check `status` của nó (§5, Quan sát); khi lock đó cũ hơn 4
giờ, `check.sh` báo `FAIL database` thay vào đó (§2). Release lock do một `release.sh` bị kill để lại là directory rỗng
`<records-dir>/.release.lock`: khi không có `release.sh` nào chạy (kiểm tra bằng `ps`), gỡ nó bằng `rmdir`.

## 4. Restore test — không bao giờ ghi đè trước

Restore vào một database cô lập hoặc một stack cô lập, **không bao giờ restore thẳng đè lên database production duy
nhất**. Thủ tục có chín bước:

1. verify checksum của dump và manifest;
2. chuẩn bị cùng PostgreSQL major version, hoặc một đích tương thích đã được ghi lại;
3. tạo một database restore-test trống;
4. restore bằng `pg_restore --single-transaction --exit-on-error --no-owner --no-privileges` (một transaction, nên một
   lần restore thất bại để lại đích trống);
5. start application release tương ứng trên database đó;
6. verify Alembic revision;
7. chạy health, các read model tiêu biểu, reconciliation về quantity, Movement và allocation, và các smoke test được
   chỉ định;
8. ghi lại thời lượng restore và kết quả;
9. chỉ hủy bản sao cô lập sau khi evidence đã được giữ lại.

Một dump không chứa grant, nên database đã restore không có grant nào cho đến khi `apply-grants` derive chúng, và
application role không làm việc được nếu thiếu chúng. Vì vậy, khi restore vào một cluster mới, hãy provision database
role trước (`provision-roles`; restore drill dùng password dùng một lần) và chạy `apply-grants` trên database đã restore
trước khi start application (bước 5). **Reconcile check (h) phải pass trước khi một database đã restore phục vụ
production**: path 3 và new-instance restore (§6) chạy `reconcile` trước khi `backend` start.

### Script restore drill

Trên production host, toàn bộ thủ tục là `deploy/production/restore-test.sh`, chạy từ repository root của một release
checkout:

```bash
deploy/production/restore-test.sh --backup <NAME> --operator "<name>"
# a candidate PostgreSQL image (glibc or image change), a baseline of known findings:
deploy/production/restore-test.sh --backup <NAME> --operator "<name>" \
    --db-image postgres:16.14-bookworm --baseline-report <records>/<release>/pre-reconcile.json
```

`deploy/production/restore-test.sh --help` in mọi option: `--release TAG` (release chạy trên database đã restore; mặc
định là release ghi trong manifest, và một backup lấy giữa một migration và lần ghi lại env file cần nêu rõ nó),
`--tools-release TAG`, `--db-image IMAGE`, `--baseline-report FILE`, `--project`, `--http-port`, `--edge-subnet`,
`--records-dir`, `--space-factor` và `--keep`.

Script bắt buộc sự cô lập. Drill chạy trong Compose project riêng `partflow-restore-<suffix>` (không bao giờ là
`partflow-production`) với volume và network riêng, loopback port và edge subnet riêng (không trùng của production),
database name cố định `partflow_restore_test`, **database-role password dùng một lần được sinh ra** (các secret file
bị xóa khi teardown), một backend worker, và backup directory được mount **read-only**. Nó không bao giờ chạy
`migrate`, không bao giờ ghi vào backup directory, và từ chối một project đã có container hoặc volume. Nó cần dung lượng
trống bằng 5 lần dump cộng 1 GiB trên Docker root (`--space-factor`).

Script thực hiện chín bước và ghi từng bước vào evidence:

| Bước evidence | Bước thủ tục |
| --- | --- |
| `verify` | 1: `backup-verify` |
| `db_start` | 2 và 3: một database trống trên PostgreSQL image mà `compose.production.yaml` của checkout pin, hoặc `--db-image`; evidence so major version của nó với của manifest |
| `restore` | 4, trong một transaction |
| `roles`, `grants` | `provision-roles`, rồi `apply-grants`, trước khi application start |
| `revision` | 6: state `current` và revision của manifest |
| `app_start` | 5 và phần health của 7: `backend` và `web` chạy, readiness `current` |
| `reconcile` | phần reconciliation của 7: `reconcile` đầy đủ, với các check (a) đến (f) replay Movement history đối chiếu mọi projection và allocation đã lưu |
| `smoke` | phần smoke của 7: `deploy/production/smoke.sh` |
| `evidence` | 8 |
| `teardown` | 9: `down -v` chỉ của drill project, sau khi file evidence tồn tại, và chỉ khi lần chạy này đã tạo project; `--keep` giữ nó lại và in command |

Quy tắc pass:

- `passed` cần reconcile exit 0 **và** check (h) `pass` **và** check (j) `pass`. Grant được áp dụng mới trong drill, nên
  một finding hoặc `not_applicable` của check (h) là một lỗi thật. Với `--db-image`, check (j) là nửa glibc (thư viện C
  của GNU) và PostgreSQL image của platform-upgrade identity check (§7).
- `passed_with_preexisting_findings` cần `--baseline-report`, mọi finding (kể cả của check (j)) có mặt trong report đó
  (`reconcile_regression.py` so sánh chúng), và check (h) `pass`.
- Mọi trường hợp khác là `failed`.

File evidence `<records-dir>/<UTC>-restore-test-<NAME>/evidence.json` ghi outcome, bước thất bại, thông tin backup và
drill, version, collation và collation version của drill server, PostgreSQL major version của backup và của drill
server cùng `server_major_match` (`false` cũng được in thành cảnh báo, vì đó không phải drill cùng major; `null` khi
một trong hai không rõ), thời gian của từng bước (`restore_to_ready` và `total` đo một lần restore mất bao lâu) và các
status của reconcile.

| Exit | Ý nghĩa |
| --- | --- |
| 0 | `passed` hoặc `passed_with_preexisting_findings` |
| 1 | `failed`, hoặc không đủ dung lượng trống |
| 2 | không chạy được, hoặc bị ngắt (không gì đã tạo còn chạy, trừ khi có `--keep`) |
| 3 | teardown thất bại: evidence vẫn hợp lệ, và thông báo in `docker compose -p <project> down -v` |

**Giới hạn đã biết:** việc đọc lại từng màn hình read model bằng một account đã đăng nhập chưa được tự động hóa, vì nó
cần một account thật của dữ liệu đã restore; evidence ghi `read_model_readback: not_automated`. Để kiểm tra bằng tay,
chạy drill với `--keep`, đăng nhập trên loopback port được in ra, rồi teardown project bằng command được in ra sau đó.

Dùng tên restore-test tường minh. **Không bao giờ đưa tên database production vào một command drill.**

## 5. Release và migration

### Trước maintenance (Before maintenance)

- Chọn chính xác release tag và biết nó thay đổi những gì.
- Xác nhận tích hợp liên tục (continuous integration, CI) đã pass cho commit đó.
- Xem xét mọi migration của release và cách khôi phục nếu nó hỏng, và ước lượng thời gian lock, thời lượng và dung
  lượng đĩa của nó trên dữ liệu có kích thước thực tế.
- Kiểm tra backup mới nhất còn gần đây (§3) và, khi đã cấu hình bản sao ngoài host, notification gần nhất của nó báo
  thành công. Dù sao `release.sh` cũng tự lấy pre-release backup đã verify: **bên trong write freeze** khi có migration
  đang chờ (nên không có khoảng hở giữa backup và migration), còn lại thì trước bước switch.
- Xác nhận image của release trước vẫn còn trên host: rollback path 1 cần chúng (§6), và `release.sh` từ chối bắt đầu
  khi thiếu chúng.
- Chạy reconciliation (§7) trên release hiện tại, giữ report, và coi mọi finding là một incident (§8). `release.sh` tự
  làm việc này. Chạy nó, và mọi command khôi phục, với tag **hiện tại**, trước khi tag candidate được ghi vào
  `.env.production`; tag candidate chỉ nằm trong shell cho đến lúc switch.
- Quyết định có cần dừng write hay không. Một migration đang chờ luôn cần write freeze bên dưới.
- Báo cho người dùng khi nào window bắt đầu và khi nào bạn sẽ quyết định rollback, và nhắc các station hoàn tất các
  dialog đang mở.
- Lên lịch daily backup ngoài maintenance window: một backup đang chạy làm release dừng trước freeze mà không đổi gì
  (`backup_running`, §3), và một daily backup bắt đầu trong lúc release bị từ chối.

### Write freeze

Write freeze là **dừng `backend`** (`$PF stop backend`); không có maintenance mode nào khác.

- `uvicorn` hoàn tất các request đang xử lý, kể cả import, và lệnh dừng chờ tối đa 200 s. Với hơn một worker,
  supervisor giữ socket mở cho đến khi worker cuối cùng thoát, nên các request gửi trong lúc đó bị treo và kết thúc bằng
  504 hoặc 502 (`DEPLOYMENT.md` §3.1, Process model). **Bắt đầu freeze khi không có import nào đang chạy.**
- Khi `backend` đang dừng, `web` vẫn phục vụ application shell và trả `/api/*` bằng JSON 504 rồi 502. Mọi client hiện
  OFFLINE banner trong khoảng một giây, và mọi write control bị chặn.
- Dialog đang mở giữ draft. Một write va đúng lúc dừng có kết quả chưa rõ; nó giữ `device_event_id` và được retry bằng
  chính id đó sau khi mở lại trên **cùng** release. Sau khi mở lại trên release **mới**, page cũ không gửi được nó (bị
  từ chối với 409): operator reload page và kiểm tra Area hoặc Work Order trước khi lặp lại thao tác.
- Một import bị cắt ngang chỉ để lại các Work Order nguyên vẹn đã commit.
- `reconcile`, `revision`, `migrate` và backup chạy từ container one-off trên `db` trong lúc stack bị freeze.
- **Mở lại** bằng `$PF up -d backend` trên cùng release, và chỉ sau khi các check của các bước bên dưới đã pass. Ở một
  release switch, việc mở lại là bước chuyển `web`.

### Thực hiện (Execute)

Chạy `release.sh` từ repository root của release checkout, với release tag đã được checkout:

```bash
deploy/production/release.sh --release <new-tag> --operator "<name>" --approver "<name>"
```

`deploy/production/release.sh --help` in mọi option. Tag có dạng `vMAJOR.MINOR.PATCH`, có thể kèm `-alpha.N`, `-beta.N`
hoặc `-rc.N`. Các option backup:

- Khi không có option backup nào, `release.sh` tự lấy pre-release backup đã verify.
- `--pre-release-backup <NAME>` nêu tên một backup `backup.sh` đã có. Nó chỉ được phép cho release **không** có
  migration đang chờ; nếu có, `release.sh` dừng mà không đổi gì, vì backup phải được lấy bên trong freeze.
- `--no-backup-reason "<text>"` chỉ dành cho lần cài đặt đầu tiên hoặc một rehearsal, và lý do được ghi lại.

`release.sh` chạy và ghi lại các bước sau:

1. preflight: các tool, environment file, dạng tag và build input sạch. Nó cũng từ chối, không đổi gì, khi
   `partflow_app_password`, `partflow_maintenance_password` hoặc `postgres_password` thiếu, rỗng hoặc không phải
   regular file, khi có một Compose project `partflow-staging` trên cùng Docker daemon, và khi image của release đang
   chạy bị thiếu;
2. revision hiện tại, và reconciliation pre-release với release đang chạy;
3. build candidate: một tag đã có không bao giờ được build lại, và chỉ được dùng lại khi cả hai image được build từ
   commit này với đúng release này;
4. check (j) và `revision` của candidate, phải báo đúng release và commit này;
5. write freeze, khi có migration đang chờ;
6. pre-release backup đã verify (bước `pre_release_backup`: `backup.sh --kind pre-release`), sau freeze khi có
   migration đang chờ; backup lock được giữ từ freeze cho đến lúc chuyển `backend`;
7. `migrate`, verify backup trước và cũng áp dụng grant;
8. reconciliation post-release;
9. chuyển `backend` trong khi `web` vẫn phục vụ bundle trước (write vẫn bị từ chối, vì mọi page đã tải gửi release
   trước và nhận 409), rồi chờ health báo release mới với schema `current`;
10. chuyển `web`, việc này mở lại write, rồi `smoke.sh`.

Một check thất bại sau switch sẽ dừng `backend` lại.

**`migrate` đánh giá pre-release backup thế nào.** `migrate --pre-release-backup NAME` verify backup theo quy tắc
`backup-verify` trước khi kết nối, và chỉ chấp nhận khi backup còn mới và thuộc đúng database này:

- Dump phải **bắt đầu tại hoặc sau** thời điểm write freeze được truyền bằng `--backup-not-before` (do `release.sh`
  truyền: thời điểm freeze khi có migration đang chờ, nếu không là thời điểm nó bắt đầu hoặc record hoàn tất mới nhất),
  để nó chứa mọi write mà `backend` đã commit.
- Không có `--backup-not-before` thì áp dụng giới hạn tuổi 60 phút, nhưng chỉ khi không có migration đang chờ; khi có
  migration đang chờ, `migrate` từ chối (`backup_freshness_unproven`).
- Revision và database name của backup phải bằng của database.

Một lần từ chối (`backup_not_found`, `backup_invalid`, `backup_stale`, `backup_revision_mismatch`,
`backup_freshness_unproven`, `backup_database_mismatch`; exit 1) không đổi gì và, khi đang freeze, mở lại write trên
release hiện tại như mọi từ chối khác. Một backup directory mà container không đọc được là `backup_unreadable` (exit 2):
container cần `--user "$(id -u):$(id -g)"`, mà `release.sh` truyền vào. Free text bị từ chối: `--pre-release-backup`
nêu tên một backup directory.

**Grant bên trong `migrate`.** Mọi `migrate` áp dụng grant trong cùng transaction với upgrade. Kết quả `refused` với một
mã database-role (`roles_not_provisioned`, `roles_incomplete`, `role_unsafe`, `role_owns_objects`, `foreign_grantor`,
`table_unclassified`, `table_missing`, `not_superuser`) rollback toàn bộ lần chạy và dừng release trước khi đổi gì
(exit 1, như mọi từ chối khác). Đọc thông báo được in, sửa nguyên nhân (`$PF --profile ops run --rm -T db-roles` cho mã
role, `DEPLOYMENT.md` §3.1) rồi chạy release lại. Khi cài đặt lần đầu, thứ tự là: secret file, build, `db`, `db-roles`,
`migrate`, first-run setup (`DEPLOYMENT.md` §3.1).

| Exit | Ý nghĩa | Làm gì |
| --- | --- | --- |
| 0 | hoàn tất | quan sát (bên dưới) |
| 1 | dừng khi chưa đổi gì, hoặc write đã mở lại trên release hiện tại | đọc lý do được in và `record.json`; sửa nguyên nhân; chạy lại |
| 2 | không chạy được (cú pháp, tool, environment) | chưa đổi gì; sửa nguyên nhân và chạy lại |
| 3 | `backend` bị để ở trạng thái dừng | làm theo §6; đọc `regression.txt` hoặc so sánh `pre-reconcile.json` và `post-reconcile.json` |
| 4 | release mới có thể đang chạy và ghi được sau một check thất bại, và re-freeze cũng thất bại | tự chạy `$PF stop backend`, rồi làm theo §6 |
| 130, 143 | bị ngắt bằng Ctrl-C hoặc TERM (record `outcome: interrupted`; bước cuối cho biết bị ngắt ở đâu); bản thân việc ngắt không start hay stop service nào | nếu bị ngắt trong `migrate` thì kết quả chưa rõ (record `migration.result: outcome_unknown`, `alembic.after: null`): chạy `$PF run --rm --no-deps -T backend python -m app.cli revision` trước mọi việc khác; rồi `$PF ps` và §6 (sau `switch_backend`, `.env.production` đã ghi tag mới; `env-before.txt` trong record directory là file trước đó) |

Hai option xử lý finding của reconciliation pre-release:

- `--accept-pre-release-findings` tiếp tục khi reconciliation pre-release có finding, và chỉ chặn với finding không có
  trong đó (`deploy/production/reconcile_regression.py` so sánh hai report).
- `--skip-pre-reconcile REASON` dành cho trạng thái rollback path 2, khi không image nào có database revision làm head:
  lý do được ghi lại, và mọi finding sau release đều chặn.

#### Cùng release đó làm bằng tay (The same release by hand)

Dạng thủ công tương đương, cùng thứ tự, với tag hiện tại vẫn nằm trong `.env.production`:

1. Ghi lại application revision và Alembic revision hiện tại
   (`$PF run --rm --no-deps -T backend python -m app.cli revision`) và chạy reconciliation pre-release với tag hiện tại
   (§7).
2. Build candidate (không bao giờ dùng một tag đã có):
   `PARTFLOW_RELEASE=<new> PARTFLOW_COMMIT=$(git rev-parse HEAD) $PF -f compose.production.build.yaml build`. Rồi thử
   candidate image trên database chưa đổi:
   `PARTFLOW_RELEASE=<new> $PF run --rm --no-deps -T backend python -m app.cli reconcile --check j`. Một check thất bại
   sẽ dừng release; chưa có gì thay đổi.
3. Khi có migration đang chờ, freeze: `$PF stop backend`, xác nhận `$PF ps --status running -q backend` không in gì,
   rồi ghi lại thời điểm, `T=$(date -u +%Y-%m-%dT%H:%M:%SZ)`. Khi không có migration đang chờ, ghi `T` trước bước 3a.
   3a. Lấy backup từ candidate checkout:
   `deploy/production/backup.sh --kind pre-release --label <new> --tools-release <new> --operator "<name>"`, và ghi lại
   NAME trong dòng `BACKUP`.
4. Migrate:
   `PARTFLOW_RELEASE=<new> $PF --profile ops run --rm -T --user "$(id -u):$(id -g)" migrate --pre-release-backup NAME --backup-not-before "$T"`
   (`--no-backup-reason TEXT` thay cho backup chỉ với lần cài đặt đầu tiên hoặc một rehearsal). Không có
   `--backup-not-before`, `migrate` từ chối một migration đang chờ (`backup_freshness_unproven`); không có `--user`, nó
   không đọc được backup directory (`backup_unreadable`). Giữ JSON output của nó (trường `grants` báo các grant đã áp
   dụng, trường `backup` báo kết quả verify) và revision mới.
5. Chạy reconciliation post-release với tag mới (§7) và so sánh với report pre-release. Chỉ các finding không có trong
   report pre-release mới chặn bước 7; các finding đã có từ trước vẫn là incident mở để owner quyết định.
6. Ghi `PARTFLOW_RELEASE=<new>` vào `.env.production` và xóa giá trị `PARTFLOW_ACCEPT_SCHEMA_REVISION`, rồi chạy
   `$PF up -d --no-deps backend` và kiểm tra health: `release` là tag mới và `schema` là `current`. Với một database
   chưa có Administrator, hoàn tất first-run setup (setup token nằm trong backend log) trước khi mở truy cập, với
   backend được start bằng một worker cho bước này (`PARTFLOW_BACKEND_WORKERS=1 $PF up -d backend`, rồi
   `$PF up -d backend`). Sau đó enroll từng thiết bị Scan Station (Administration → Scan Stations → `Devices…`).
7. Chạy `$PF up -d --no-deps web`; việc này mở lại write. Chạy `deploy/production/smoke.sh --release <new>` và các
   check được chỉ định về authorization, scan focus và kết nối, và write cùng read-back.
8. Chỉ giữ write mở khi mọi check bắt buộc đều pass; nếu không, chạy lại `$PF stop backend` và làm theo §6. Khi
   `release.sh` exit 3, đọc `regression.txt` (hoặc so sánh hai report) rồi hoặc mở lại như bước 6 và 7, hoặc làm theo
   §6.

### Quan sát (Observe)

Trong observation window bạn đã chọn (ghi lại bằng `--rollback-deadline` và `--observation-owner`), theo dõi error,
latency, lock, restart, đĩa và phản hồi của người dùng: các dòng `errors`, `restarts` và `disk_*` của `check.sh`, các
lần đọc chậm (`"slow":true`) và `status` cùng lock wait của nó (§2).

- Một check trong lúc write freeze báo `backend` đã dừng (một notification), và bỏ qua các check `status` khi release
  lock còn tồn tại; khi lock cũ hơn 4 giờ, nó báo `FAIL database`.
- Release tạo lại `backend`, việc này xóa log của nó: chạy `check.sh --only errors --no-state` trước release (§2).
- Giữ image của release trước và pre-release backup (không bao giờ bị rotate) cho đến deadline rollback.
- Các page đang mở trong lúc switch hiện update notice và phải được reload ([`GUI_DESIGN.md`](../GUI_DESIGN.md) §3 rule
  13); một Scan Station hoặc Production Board không người trực tự reload khi không còn dialog nào mở.

## 6. Cây quyết định rollback

Chọn path đầu tiên có điều kiện đúng. Các command khôi phục chạy với release khớp với database.

1. **Không có schema migration nào:** redeploy release bất biến trước đó. Đặt tag trước vào `.env.production`, chạy
   `$PF up -d backend web`, rồi `deploy/production/smoke.sh --release <previous>`. Việc này cần image của release trước
   còn trên host: giữ chúng trong suốt rollback window và **không bao giờ chạy `docker image prune -a`**; `release.sh`
   từ chối bắt đầu khi chúng thiếu.
2. **Schema đã được migrate và backward-compatible:** chỉ deploy release trước nếu tính tương thích đã được verify rõ
   ràng và ghi lại. Đọc `database_revision` từ `$PF run --rm --no-deps -T backend python -m app.cli revision` (chạy với
   release mới), đặt `PARTFLOW_RELEASE=<previous>` và `PARTFLOW_ACCEPT_SCHEMA_REVISION=<database_revision>` trong
   `.env.production`, chạy `$PF up -d backend web`, rồi
   `deploy/production/smoke.sh --release <previous> --allow-accepted-schema`.
   - Override nêu đúng một revision mà release trước không biết. Một revision nó biết thì bị bỏ qua (`revision` hiện
     `override_ignored`, và readiness vẫn là `mismatch`). Nó không bao giờ khớp revision nào khác, và release forward
     kế tiếp sẽ xóa nó.
   - Trước khi override được đặt, release trước từ chối mọi thay đổi; station read vẫn ghi thời điểm last-seen của
     thiết bị (device bookkeeping, không có dữ liệu production).
   - Để release forward từ trạng thái này, reconciliation pre-release dùng candidate image (`release.sh` làm vậy) hoặc
     `--skip-pre-reconcile REASON`.
3. **Schema đã được migrate và không backward-compatible, hoặc chưa rõ tính tương thích:** dừng write; restore database
   trước migration vào một database mới, trống trong PostgreSQL instance của production
   (`createdb --template=template0`, restore trong một transaction; database đã migrate được giữ nguyên) và deploy
   release trước tương ứng, theo Thủ tục path 3 bên dưới. Việc restore pre-release backup không bao giờ bỏ các write
   sau nó nếu chưa qua escalation của path 4.
4. **Đã có production write mới sau migration:** không restore mù quáng đè lên chúng. Escalate; giữ cả database hiện
   tại lẫn pre-release backup, quyết định một bản sửa forward hoặc một kế hoạch khôi phục dữ liệu có audit, và giữ
   application ở trạng thái write-blocked.

**Không bao giờ coi `alembic downgrade` là an toàn.** PartFlow cố ý bảo vệ history append-only, và một downgrade có thể
bị từ chối hoặc sẽ bỏ mất dữ liệu mới được hỗ trợ.

### Thủ tục path 3

Path 3 chỉ áp dụng khi release record cho thấy `writes_reopened_at: null` (release dừng ở trạng thái freeze) hoặc owner
ghi lại rằng không có production write nào sau migration; nếu không thì là path 4. Restore vào production cần **sự phê
duyệt của owner, được ghi trong `rollback.json` trước khi `backend` start** trên database đã restore; không có nó, thủ
tục dừng sau `reconcile` với application ở trạng thái write-blocked.

Các key của `.env.production` do Compose đọc và không được export vào shell, nên mọi giá trị bên dưới được đặt tường
minh. **Mỗi block là một script: lưu nó trong release record directory và chạy bằng `sh -eu <file>`, không bao giờ
paste từng dòng.** Một guard không thỏa sẽ in lý do và dừng script trước command kế tiếp, và mọi command thất bại
(`-e`) hoặc biến chưa đặt (`-u`) cũng vậy.

**Bước A** chạy trong checkout của release **candidate**: `release.sh` của nó đã lấy backup, nên image `backup-tools`
của nó verify được backup. Bước A kiểm tra path 3 có áp dụng không, xác nhận write freeze và verify backup:

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

**Bước B** chạy trong checkout của release **trước**, với image của release trước có sẵn, dưới dạng ba script. **B1**
kiểm tra dung lượng và restore vào một database mới. Tên mới không bao giờ là tên đang dùng, và `createdb` từ chối một
tên đã tồn tại. Quy tắc dung lượng là: database volume phải còn trống ít nhất kích thước database cộng hai lần dump cộng
1 GiB, cho bản sao đã restore, nhật ký ghi trước (write-ahead log, WAL) của transaction và phần dự trữ:

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
`PARTFLOW_ACCEPT_SCHEMA_REVISION=` (để trống). **B2** áp dụng grant và kiểm tra database đã restore với release trước;
các database role đã tồn tại, vì role thuộc về toàn bộ PostgreSQL cluster:

```sh
REC=<release record directory>
PF="docker compose -f compose.production.yaml --env-file .env.production"
$PF --profile ops run --rm -T db-roles apply-grants               # roles already exist in the cluster
$PF run --rm --no-deps -T backend python -m app.cli revision      # "state": "current" (otherwise it exits non-zero: B2 stops)
rc=0
$PF run --rm --no-deps -T backend python -m app.cli reconcile --max-findings 10000 > "$REC/rollback-reconcile.json" || rc=$?
echo "reconcile exit $rc"
```

`reconcile` phải exit 0, hoặc exit 1 với chỉ những finding cũng có trong `pre-reconcile.json` của release
(`deploy/production/reconcile_regression.py` so sánh chúng). Ghi sự phê duyệt của owner vào `$REC/rollback.json` trước
B3. `rollback.json` mang các trường của §1 cộng `approved_by` (owner), `approved_at` (UTC) và `reason`, tên database mới
và cũ, tên backup và cả hai kết quả reconciliation.

**B3** start release trước trên database đã restore và chỉ chuyển `web` sau khi `backend` tự báo đã sẵn sàng, để write
chỉ mở lại trên một backend đã được kiểm chứng. Vòng chờ health gọi `127.0.0.1` trên port đã publish vì `web` chỉ được
publish trên địa chỉ loopback:

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

**Database đã migrate giữ nguyên tên và nội dung** để phân tích cho path 4. Owner xóa nó sau observation window, không
bao giờ là một tool:
`$PF exec -T db sh -c 'dropdb -U "$POSTGRES_USER" "$1"' sh <old name>`.

### New-instance restore

Dùng thủ tục này sau khi host hỏng (§8) hoặc khi chuyển host (`SYNOLOGY_NAS.md` §10). Làm việc trên host mới, trong
checkout của release đã lấy backup, với một volume `postgres_data` **mới, trống**. Trước hết kiểm tra backup directory
đã sao chép (`sha256sum -c SHA256SUMS` và `backup-verify`, như ở bước A). Sau đó, với cùng `PF`, `BPATH` và quy tắc
dung lượng như bước B1 (với `DB_BYTES=0`):

1. Start database: `$PF up -d db`.
2. Kiểm tra nó trống:
   `$PF exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = '"'"'public'"'"'"'`
   phải in `0`; nếu không thì dừng, vì volume không phải mới.
3. Restore vào database của container bằng dòng `pg_restore` của bước B1:
   `$PF exec -T db sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --single-transaction --exit-on-error --no-owner --no-privileges' < "$BPATH/partflow.dump"`.
   Một lần thất bại sẽ rollback, database vẫn trống, và kiểm tra trống lại pass trước khi thử lại.
4. Provision role và áp dụng grant: `$PF --profile ops run --rm -T db-roles`, rồi
   `$PF --profile ops run --rm -T db-roles apply-grants`.
5. Chạy `revision` và `reconcile` như ở bước B2, và giữ report trong records directory.
6. Ghi sự phê duyệt của owner vào `<records-dir>/<UTC>-restore-<NAME>/restore.json` (cùng các trường như
   `rollback.json`). Rồi chạy `$PF up -d backend`, vòng chờ health của bước B3, `$PF up -d --no-deps web` và
   `deploy/production/smoke.sh --release <tag>`.

Ghi lại khoảng dữ liệu bị mất: các write xảy ra sau `dump_started_at` của backup không có trong database đã restore.

## 7. Reconciliation

Reconciliation là command backend read-only `python -m app.cli reconcile`. Nó chạy các check bên dưới trong một
database snapshot read-only và in một JSON report ra standard output; **nó không bao giờ sửa gì cả**.

### Chạy reconciliation

Trên development stack:

```bash
# development stack
f=reconcile.json
docker compose exec -T backend uv run python -m app.cli reconcile > "$f"; rc=$?
python3 -c 'import json,sys; r=json.load(open(sys.argv[1])); assert r["exit_code"]==int(sys.argv[2]); print(r["result"], r["exit_code"])' "$f" "$rc" \
  || echo "could not run: no complete report (exit $rc)"
docker compose exec -T backend uv run python -m app.cli reconcile --check j     # identity check only
```

Trên production stack, chạy nó từ release checkout với tag đang chạy. Nó vẫn chạy được khi `backend` đang dừng
(`--no-deps` không start service nào khác, nhưng database phải đang chạy):

```sh
f="reconcile-$(date -u +%Y%m%dT%H%M%SZ).json"
$PF run --rm --no-deps -T backend python -m app.cli reconcile > "$f"; rc=$?
```

Rồi áp dụng cùng phép kiểm tra report (dòng `python3` ở trên). Trước khi switch một release, candidate image chạy check
(j) bằng
`PARTFLOW_RELEASE=<new> $PF run --rm --no-deps -T backend python -m app.cli reconcile --check j` (§5, Thực hiện: bước 4
của `release.sh`, bước 2 của release làm bằng tay).

Trên một staging instance do tool Deployment Admin `pf` quản lý: `pf` từ chối `exec` và `run`, nên chạy dạng Compose
thô của `SYNOLOGY_ADMIN.md` §14 bên ngoài tool, không bao giờ cùng lúc với `pf update`, `pf backup`, `pf reset-db`,
`pf purge` hoặc `pf restore-instance`. Ghi report vào home directory của operator, không bao giờ vào một directory do
`pf` quản lý:

```sh
f="$HOME/partflow-reconcile-$(date -u +%Y%m%dT%H%M%SZ).json"
sudo env PARTFLOW_REPO_ROOT=/volume1/docker/partflow/repo \
    PARTFLOW_DATABASE_URL='postgresql+psycopg://<user>:<percent-encoded password>@db:5432/<db>' \
    DEPLOY_ADMIN_INSTANCE_ID=<instance UUID from 'pf instances'> \
    docker compose \
    --project-directory /volume1/docker/partflow/repo \
    --env-file /volume1/docker/partflow/config/.env \
    -p partflow-staging \
    -f /volume1/docker/partflow/control/compose.nas.yaml \
    exec -T backend uv run python -m app.cli reconcile > "$f"; rc=$?
python3 -c 'import json,sys; r=json.load(open(sys.argv[1])); assert r["exit_code"]==int(sys.argv[2]); print(r["result"], r["exit_code"])' "$f" "$rc" \
    || echo "could not run: no complete report (exit $rc)"
```

Các path, project name và instance identifier là của chính instance đó (đang hiện các giá trị ví dụ của
`SYNOLOGY_ADMIN.md` §14). Cả ba biến đều bắt buộc: thiếu chúng Compose từ chối start và không ghi report nào. Một
`DEPLOY_ADMIN_INSTANCE_ID` sai sẽ gắn nhãn sai mọi resource mà Compose tạo (`SYNOLOGY_ADMIN.md` §14); bản thân `exec`
không tạo container, volume hay network nào.

### Option, exit code và quy tắc report

- `--check ID` (lặp được, từ `a` đến `j`; các check khác được báo là `skipped`), `--statement-timeout SECONDS` (1 đến
  3600, mặc định 300), `--max-findings N` (1 đến 10000, mặc định liệt kê 100 finding mỗi check; `finding_count` luôn
  giữ số đếm đầy đủ).
- Exit code: `0` sạch, `1` mismatch (ít nhất một finding), `2` không chạy được hoặc chưa đầy đủ (một check `error` hoặc
  một lỗi ở mức lần chạy thắng các finding).
- **Exit status chỉ có giá trị khi report chứa một tài liệu JSON đầy đủ có `exit_code` bằng nó.** Một report rỗng hoặc
  không parse được nghĩa là "không chạy được", bất kể status là gì. (Bản thân Compose exit 1 trước khi chạy gì khi
  service đang dừng hoặc project sai.)
- Giữ report trong deployment record (§1, "Kết quả smoke và reconciliation").

### Các check

Mỗi check giữ yêu cầu gốc làm định nghĩa:

| Check | Yêu cầu |
| --- | --- |
| (a) | các current-position projection replay được từ Movement history chưa bị reverse; |
| (b) | mọi flow active hoặc closed có một conservation history hợp lệ, cộng các invariant cross-row của [`SLICE1_DATA_MODEL.md`](../SLICE1_DATA_MODEL.md) §17 trừ invariant audit-row (được transaction protocol bảo đảm và các API test bao phủ); |
| (c) | quantity được đưa vào theo từng PN khớp với các kết quả active, stocked, scrapped và reversed theo các quy tắc chuẩn; |
| (d) | quantity được gán cho Machine khớp với các flow hiện đang nằm trên mỗi Machine; |
| (e) | `released_quantity` của demand được derive từ bằng chứng `RECEIVED`; |
| (f) | `allocated_quantity` của demand và `completed_at` của Work Order khớp với các allocation row active; một correction vượt demand có ủy quyền (allocation row được ghi `exceeds_demand`) không bị báo là allocation vượt quantity yêu cầu; |
| (g) | không Movement nào được giữ lại tham chiếu một row đã bị purge; |
| (h) | không table append-only nào bị thay đổi ngoài một đường archival hoặc purge đã được duyệt, kiểm tra dưới dạng guard integrity: các guard trigger có mặt, đang bật và không đổi, source của các guard function không đổi, `partflow_app` và `partflow_maintenance` giữ đúng grant của chúng (không có UPDATE, DELETE hay TRUNCATE trên history append-only), các role giữ attribute an toàn và không có membership, PUBLIC và default privilege không giữ gì, và `session_replication_role` không được đặt là `replica`. Nó không thấy được một thay đổi do superuser thực hiện khi đã tắt một trigger hoặc đặt `session_replication_role` trong session của chính mình rồi khôi phục lại sau đó (một giới hạn đã chấp nhận); |
| (i) | các entry của Hot list là demand đang active (query của `DEPLOYMENT.md` §5); |
| (j) | danh tính chuẩn dưới interpreter và database đang chạy: PN chuẩn, Worker badge không phân biệt hoa thường, quy tắc prefix của Asset Tag, mọi CHECK constraint về dạng chuẩn được đánh giá lại dưới collation và phân loại ký tự của database đang chạy, collation version, và các probe trùng lặp của identity key không dựa vào index (platform-upgrade identity check). |

Check (g) báo `not_applicable` cho đến khi có archival cho Movement history, và check (h) chỉ báo `not_applicable` trên
database không có các database role của PartFlow (development, test, staging); cả hai đều trung tính với exit code.
Trong production stack (`DATABASE_ROLES_REQUIRED=true`), một role bị thiếu là một finding.

### Sửa các finding của check (h)

Reconciliation không bao giờ sửa. Các command bên dưới là của operator, và owner quyết định:

- mã privilege trên `partflow_app`, `partflow_maintenance` hoặc PUBLIC, và mã schema, database và default-privilege cho
  các grantee đó: `$PF --profile ops run --rm -T db-roles apply-grants`;
- mã role trừ `ROLE_OWNS_OBJECTS` (`ROLE_ATTRIBUTE`, `ROLE_MEMBERSHIP`, `ROLE_MISSING`) và một
  `REPLICATION_ROLE_SETTING` theo role (`<db>/<role>` hoặc `*/<role>`): `$PF --profile ops run --rm -T db-roles`, rồi
  `$PF --profile ops run --rm -T db-roles apply-grants`;
- một finding cho database role khác (mọi mã privilege, column, schema, database hoặc default-privilege —
  `PRIVILEGE_EXCESS`, `COLUMN_PRIVILEGE`, `SCHEMA_PRIVILEGE`, `DATABASE_PRIVILEGE`, `DEFAULT_PRIVILEGE` — có grantee là
  một role khác PUBLIC, `partflow_app` và `partflow_maintenance`; một từ chối `foreign_grantor` của `apply-grants` hoặc
  `migrate`): tìm hiểu ai đã cấp và vì sao, rồi revoke nó với tư cách owner hoặc người cấp; `apply-grants` cố ý để
  nguyên nó;
- `TABLE_UNCLASSIFIED` và `TABLE_MISSING` (và các từ chối `table_unclassified` và `table_missing` của `apply-grants`
  hoặc `migrate`): một table được tạo hoặc xóa ngoài migration là một incident (§8) và owner quyết định. `apply-grants`
  và `migrate` từ chối cho đến khi table lạ được gỡ (hoặc một release phân loại nó) hoặc table bị thiếu được khôi phục;
- mã trigger (`TRIGGER_MISSING`, `TRIGGER_CHANGED`, `TRIGGER_DISABLED`, `TRIGGER_ENABLE_MODE`), mã function
  (`GUARD_FUNCTION_MISSING`, `GUARD_FUNCTION_CHANGED`), `ROLE_OWNS_OBJECTS` (và từ chối `role_owns_objects`), một
  `REPLICATION_ROLE_SETTING` toàn database (`<db>/*`) và `REPLICATION_ROLE_ACTIVE`: một incident (§8), và owner quyết
  định.

**Không bao giờ sửa bằng cách chạy lại migration hoặc downgrade chúng.** Một lần chạy đua với một thay đổi grant hoặc
trigger có thể hiện một finding rồi biến mất: chạy lại trước khi hành động.

### Quy tắc vận hành

- Một snapshot read-only. Command chỉ lấy table lock `ACCESS SHARE`, trước snapshot, và không lấy row lock hay
  advisory lock. Nó thất bại sau khi chờ 5 s cho một table lock (sớm hơn khi `--statement-timeout` ngắn hơn), hoặc ngay
  lập tức khi yêu cầu lock của nó deadlock với session khác. **Không bao giờ chạy nó cùng lúc với một migration**, và
  chạy vào giờ thấp điểm.
- Rehearsal nâng cấp platform cho check (j): với một lần nâng cấp Python hoặc Unicode Character Database (UCD), chạy
  `--check j` từ candidate backend image trên database hiện tại. Với một thay đổi glibc hoặc PostgreSQL image, chạy
  restore drill lên candidate server,
  `deploy/production/restore-test.sh --backup <latest> --db-image <candidate image>` (§4; `reconcile` đầy đủ của nó chạy
  check (j) và các định nghĩa trigger của check (h) trên server đó), hoặc, sau một lần đổi image tại chỗ cùng major
  version, chạy `reconcile --check j` trước khi mở lại write. Một `COLLATION_VERSION_MISMATCH` là quyết định của owner.
  Owner quyết định việc chuẩn hóa lại trước mọi lần nâng cấp.
- Với một Work Order được báo `not_completed_but_fully_allocated`, `expected` là giá trị replay, không phải đề xuất sửa;
  owner chọn ngày hoàn tất trong incident.
- Một exit khác 0 là một incident (§8). **Không bao giờ sửa trực tiếp history hay projection**; owner quyết định từng
  lần sửa. Một mismatch không bao giờ kích hoạt sửa tự động.

### Reconciliation theo lịch

`deploy/production/scheduled-reconcile.sh` là dạng chạy hằng ngày, khuyến nghị lúc 04:00, từ checkout của release đang
chạy, bằng account sở hữu `PARTFLOW_BACKUP_DIR`.

- Nó chạy `reconcile` đầy đủ trong production stack và ghi report vào
  `~/partflow-monitoring/reconcile/<UTC timestamp>-reconcile.json` (`--reports-dir`; directory mode 0700, report mode
  0600).
- Nó áp dụng quy tắc exit code ở trên thông qua `monitor_report.py`: nó in
  `RECONCILE clean|mismatch|error|could_not_run <report>` và một dòng `FAIL <check> <title>` cho mỗi check thất bại, và
  exit 0 (sạch), 1 (mismatch) hoặc 2 (không chạy được, kể cả một report chưa đầy đủ).
- Nó từ chối chạy khi một release giữ lock của nó hoặc backup lock (exit 2), dừng một lần chạy dài hơn
  `--max-runtime-minutes` (mặc định 60), và không bao giờ chạy cùng lúc với một migration.
- `last-result.txt` trong reports directory chứa các dòng của lần chạy gần nhất. Mọi exit khác 0 đi tới failure
  notification của scheduler, vốn chỉ nêu check id, tiêu đề và số đếm.

**Xử lý report:** một report có mode 0600 và có thể chứa giá trị Worker badge (check (j)). Giữ report như backup, và
không bao giờ đính kèm chúng vào ticket hay email. Không tool nào xóa chúng; hãy dọn bằng tay (§9).

## 8. Xử lý sự cố

### Nghi write bị trùng, bị mất hoặc chưa rõ kết quả

- Dừng workflow bị ảnh hưởng nếu tính toàn vẹn của quantity có thể gặp rủi ro.
- Giữ lại thời điểm request, station, User hoặc Worker, PN, flow và `device_event_id`.
- Trước mọi lần retry, đối chiếu backend log theo `request_id`, `device_event_id`, PN, QuantityFlow, Scan Station và
  Worker (các command ở §2), và xem kết quả và history phía server.
- Chỉ retry bằng idempotency key gốc, và chỉ khi phù hợp.
- **Không bao giờ sửa trực tiếp Movement history.**
- Chỉ dùng workflow Undo hoặc correction chuẩn sau khi đã biết trạng thái đã commit.

### Finding guard-integrity (reconcile check (h))

- Freeze write (`$PF stop backend`, §5) khi một trigger hoặc guard function đã thay đổi, một trigger bị tắt, hoặc
  `session_replication_role` được đặt cho toàn database hoặc đang có hiệu lực: khi đó các guard thông thường không chạy
  cho bất kỳ session nào, kể cả `partflow_app`.
- Với setting đó, owner chạy `ALTER DATABASE <db> RESET session_replication_role` bên trong `db`.
- So sánh các trigger và function với source của migration.
- Owner quyết định cách sửa và có cần đối chiếu history với backup gần nhất hay không.
- Chạy `reconcile` và privilege probe (`DEPLOYMENT.md` §3.1) trước khi mở lại write.

### Áp lực database hoặc storage

- Chặn write mới trước khi đĩa đầy (write freeze, §5).
- Giữ lại log và metric.
- Alert về storage là một dòng `disk_*` của `check.sh` dưới 15 % trống: `disk_data` đo database volume, `disk_backup`
  đo backup directory, `disk_docker` đo Docker root (image và log của container) và `disk_archive` đo archive
  directory.
- **Không xóa tùy tiện file PostgreSQL, volume, Movement row hay backup.**
- Nắm các quy tắc dung lượng: một backup cần trống hai lần dump mới nhất cộng phần dự trữ trong backup directory; một
  lần restore path 3 cần trống kích thước database cộng hai lần dump cộng 1 GiB trên database volume (§6); và một
  backup lock cũ (`backup_lock_stale`) chặn backup và release cho đến khi được gỡ (§3).
- Mở rộng storage. Việc bảo trì archive và purge cho Movement history đã được lên kế hoạch nhưng chưa được triển khai
  ([`../IMPLEMENTATION_ROADMAP.md`](../IMPLEMENTATION_ROADMAP.md), Phase 16).
- Chạy reconciliation trước khi mở lại write.

### Host failure (host hỏng)

- Ngăn hai bản đang chạy cùng lúc: xác nhận instance hỏng không thể nhận write.
- Chuẩn bị host khôi phục.
- Restore backup đã verify mới nhất với release tương ứng, dùng new-instance restore (§6).
- Chạy reconciliation và smoke test.
- Ghi lại khoảng dữ liệu bị mất: các write xảy ra sau `dump_started_at` của backup.
- Chỉ chuyển client sang sau khi được phê duyệt: sự phê duyệt của owner được ghi trong `restore.json` trước khi
  `backend` start.

## 9. Lịch định kỳ

Các lịch bên dưới được khuyến nghị sau go-live (`DEPLOYMENT.md` §5); không lịch nào chặn việc sử dụng.

| Tần suất | Công việc |
| --- | --- |
| Liên tục | `check.sh` mỗi 15 phút: health, restart, đĩa, certificate, tuổi backup và error của backend, với các ngưỡng của §2 (backup cũ hơn 26 h, đĩa trống dưới 15 %, certificate hết hạn trong vòng 21 ngày, mọi lần tăng restart count, mọi error record của backend). Notification là failure notification của scheduler; một failure không đổi được nhắc lại mỗi 6 h. |
| Hằng ngày | `backup.sh --kind daily --keep-daily 14 --keep-weekly 8` theo lịch (với giá trị của owner) lúc 02:00, như trong các hướng dẫn platform; xem notification của scheduler và của tool platform (exit 4 gồm cả một daily backup không qua verify) và bản sao ngoài host; xem các error nghiêm trọng; `scheduled-reconcile.sh` lúc 04:00, sau backup; xem `last-check.txt`, `<reports-dir>/last-result.txt` và các notification. |
| Hằng tuần | Xem xu hướng dung lượng và mức tăng của database (`growth.tsv` và `docker stats --no-stream`); xem các lần đăng nhập thất bại và sự kiện authorization bằng `$PF logs --since 168h --no-log-prefix backend \| grep '"logger":"app.access"' \| grep -E '"status":(401\|403)'` (`refusal.type` và `refusal.code` phân biệt một lần đăng nhập bị từ chối hoặc bị khóa, một permission denied, `station_device_required` hoặc `station_device_mismatch`, và `csrf_rejected`; các dòng đăng nhập `app.application.authentication` chỉ nêu user id); xem các bản cập nhật bảo mật đang chờ của host. |
| Hằng tháng | Dọn bằng tay các reconciliation report cũ sau khi xem (không tool nào xóa chúng; chúng có thể chứa giá trị badge); áp dụng các bản cập nhật, thử trên một hệ thống test trước khi có thể; xem lại user và role, firewall rule, secret và các liên hệ trong runbook này; xem lại database role bằng `reconcile --check h`; xóa pre-release backup và manual backup đã hết observation window, và các daily backup mà rotation báo invalid, sau khi xem xét (không bao giờ xóa archive). |
| Khi rotate role password | Trước tiên chạy `check.sh --url https://<partflow-host> --only errors --no-state` (tạo lại `backend` sẽ xóa log của nó, §2). Rồi một write freeze ngắn: thay file password của role, `$PF stop backend`, `$PF --profile ops run --rm -T db-roles`, `$PF up -d --force-recreate --no-deps backend`, rồi kiểm tra health. Một lệnh `up -d backend` thường không nhận password mới (container không được tạo lại), và chạy `db-roles` trong khi backend đang phục vụ làm các kết nối mới của nó thất bại. Với owner password, chạy `ALTER ROLE … PASSWORD` bên trong `db` trước, rồi thay `postgres_password` (không cần restart service nào: chỉ `migrate` và `db-roles` one-shot dùng nó). |
| Hằng quý, hoặc sau một thay đổi schema đáng kể | `restore-test.sh --backup <latest daily>` (§4); so thời gian `restore_to_ready` và `total` của nó với khoảng thời gian nhà máy có thể làm việc mà không có PartFlow (mục tiêu thời gian khôi phục, recovery time objective, RTO), và xem lại kết quả reconciliation. |
| Trước khi đổi PostgreSQL image hoặc glibc của host | `restore-test.sh --backup <latest> --db-image <candidate>` (§7). |
| Trước mỗi release | `release.sh` lấy pre-release backup đã verify (§5); xem lại các migration, quyết định rollback và kế hoạch smoke test. |

Owner chọn các giá trị retention, các lịch chạy và điểm khôi phục cùng thời gian khôi phục chấp nhận được. Các giá trị
trong runbook này là ví dụ làm việc, không phải cam kết mức dịch vụ.

## Lịch sử thay đổi

| Ngày | Commit | Thay đổi |
| --- | --- | --- |
| 2026-11-09 | chưa commit | Viết lại thành nội dung hiện hành cho người mới đọc; pilot và release gate được bỏ theo quyết định của owner. |
| 2026-10-09 | 4271f61 | Thêm cách tìm log theo Work Order Demand và check error cần chạy trước khi tạo lại `backend`. |
| 2026-10-09 | 03bd90c | Thêm monitoring: structured log có request id, command `status`, `check.sh` và reconciliation theo lịch. |
| 2026-10-08 | 92727f6 | Làm cho các script rollback path 3 dừng ở mọi guard không thỏa và chỉ mở lại `web` sau khi `backend` sẵn sàng. |
| 2026-10-08 | a330383 | Thêm backup production, restore drill cô lập, pre-release backup bên trong write freeze và rollback path 3. |
| 2026-10-08 | 1116b07 | Thêm các database role, grant được áp dụng bởi mọi migration và cách sửa cho reconcile check (h). |
| 2026-10-08 | b820e7a | Thêm release flow với `release.sh`, write freeze và rollback path 1 và 2. |
| 2026-10-08 | d7e86a9 | Trỏ các thủ tục tới production Compose stack. |
| 2026-10-07 | 3fb66df | Thêm command reconciliation read-only và các check của nó. |
| 2026-09-02 | d2f68de | Phiên bản đầu tiên, cùng bản tiếng Việt. |
