# Runbook vận hành PartFlow

> **Bản gốc chuẩn:** [`OPERATIONS_RUNBOOK.md`](OPERATIONS_RUNBOOK.md),
> được tạo trong gói tài liệu này trên baseline upstream
> `194ffc2e5e8e22c389abecd0830292a6707955d9`.
>
> **Trạng thái:** Mẫu quy trình vận hành chuẩn cho Phase 16. Production Compose
> file đã có (P16-S2): `PF` bên dưới là
> `docker compose -f compose.production.yaml --env-file .env.production`, chạy từ
> release checkout ([`../DEPLOYMENT.md`](../DEPLOYMENT.md) §3.1). Các command
> release, migration, write freeze và rollback (path 1 và 2) đã là thật (P16-S3:
> `deploy/production/release.sh`, `smoke.sh`, `migrate`, `revision`); các command
> backup, restore drill và rollback path 3 đã là thật (P16-S5:
> `deploy/production/backup.sh`, `deploy/production/restore-test.sh`,
> `backup-manifest`, `backup-verify`, `backup-rotate`), dù chưa có schedule,
> off-host replication, drill hay path 3 nào được chạy trên pilot host (P16-S7);
> các command monitoring đã là thật (P16-S6: `deploy/production/check.sh`,
> `deploy/production/scheduled-reconcile.sh` và `python -m app.cli status`), dù
> schedule, failure notification và mọi lần chạy trên pilot host thuộc P16-S7.
>
> **Quyền chuẩn:** Tiếng Anh là source of truth.

## 1. Deployment record bắt buộc

Ghi cho mỗi environment và release:

| Trường | Giá trị |
| --- | --- |
| Environment và URL |  |
| Host/model/provider |  |
| Release Git commit/tag và image digest |  |
| Alembic revision trước/sau |  |
| Deployment operator và approver |  |
| Thời gian bắt đầu/kết thúc (UTC) |  |
| Path, checksum và kết quả verify của pre-release backup |  |
| Migration output (gồm báo cáo grants) và báo cáo provisioning database role |  |
| Kết quả smoke/reconciliation |  |
| Rollback deadline và observation owner |  |
| Giới hạn đã biết |  |

`deploy/production/release.sh` ghi record này thành `record.json` (cùng output của
mọi bước) trong `<records-dir>/<UTC>-<tag>/`, và các trường ánh xạ vào các dòng
trên: `environment` và `url` (dòng 1), `host` (dòng 2), `release` (tag, commit,
tag trước và image ID cục bộ của `backend` và `web`; dòng 3), `alembic`
(`before`, `after`, `expected`; dòng 4), `operator`, `approver` (dòng 5),
`started_at`, `finished_at` (dòng 6), `backup` (dòng 7: `kind`, `reference`,
`name`, `path` tuyệt đối, `verified`, `verification`, `freshness`, `taken_by`
(`release.sh`, `operator` hoặc `null`), `manifest_sha256` và các manifest fact ở
§3), `migration` (các file `migrate.json` và `migrate.log`; dòng 8; `migrate.json` mang kết quả `grants`, và các báo cáo JSON `provision-roles` và `apply-grants` của một lần chạy thủ công được giữ cạnh nó),
`reconcile` và `smoke` (dòng 9), `rollback_deadline` và `observation_owner`
(dòng 10), `known_limitations` (dòng 11); `freeze_completed_at` (thời điểm
`backend` được xác nhận đã dừng; `null` khi không có freeze), `outcome`,
`writes_reopened_at` và `refrozen` nêu cách lần chạy kết thúc. Một thao tác thủ
công (ví dụ rollback) ghi bổ sung vào cùng thư mục với cùng các trường. Evidence
của restore drill (`evidence.json`, §4) cùng `rollback.json` của path 3 và
`restore.json` của new-instance restore (§6) được ghi dưới cùng records
directory.

## 2. Health và chẩn đoán

Kiểm tra tối thiểu (development và staging stack):

```bash
docker compose ps
docker compose logs --since=15m backend frontend db
curl --fail --silent --show-error https://<partflow-host>/api/health
```

Production stack (`PF` như trên; health qua hostname):

```bash
$PF ps
$PF logs --since=15m backend web db
curl --fail --silent --show-error https://<partflow-host>/api/health
curl --fail --silent --show-error https://<partflow-host>/api/health/live
$PF run --rm --no-deps -T backend python -m app.cli revision
```

Các command monitoring cho production (P16-S6; chạy từ checkout của release đang
chạy, bằng account sở hữu `PARTFLOW_BACKUP_DIR`; schedule được cài trên pilot host
ở P16-S7):

```bash
# manual diagnosis: the same checks as the scheduled run, without touching its state
deploy/production/check.sh --url https://<partflow-host> --no-state
# database size, connections, lock waits, Movement rows and size, schema readiness, backup age
$PF --profile ops run --rm --no-deps -T --user "$(id -u):$(id -g)" status
docker stats --no-stream
```

`check.sh` in một dòng cho mỗi check, `PASS|FAIL|SKIP <id> <reason>`, theo thứ tự:
`https` (`/api/health` trả 200 với `schema` là current và release của
`.env.production`), `certificate` (không hết hạn trong 21 ngày), `containers`
(đúng một `db`, `backend` và `web` ở trạng thái `running`, healthy), `restarts`
(restart count không tăng kể từ lần chạy đầy đủ trước), `errors` (không có record
`ERROR` hay `CRITICAL` của backend kể từ lần chạy đầy đủ trước, kèm tối đa ba
request id), `disk_data` (database volume), `disk_backup` (backup directory),
`disk_docker` (Docker root: image và container log), `disk_archive` (archive
directory; `SKIP` cho đến khi được cấu hình, P16-S8), rồi `database`, `schema`,
`backup_age` và `archival_proposal` từ một lần chạy `status` (dòng cuối là `SKIP`
cho đến P16-S10). Một disk là `FAIL` khi còn trống dưới 15 % và `backup_age` là
`FAIL` khi backup đã publish mới nhất, thuộc bất kỳ loại nào, cũ hơn 26 giờ. Exit
code: 0 không có gì để thông báo, 1 có ít nhất một `FAIL` cần thông báo, 2 check
không chạy được (cũng thông báo). Một tập lỗi không đổi chỉ được thông báo lại sau
6 giờ (`--renotify-hours`; output khi đó ghi `already reported`). State nằm trong
`~/partflow-monitoring` (`--state-dir`, mode 0700): `restarts` (baseline restart),
`errors-since` (log cursor), `alert-state` (re-notification), `last-check.txt`
(mọi lần chạy), `status.json`, `status.err` và `growth.tsv` (mỗi ngày một dòng:
database bytes, Movement rows và Movement bytes). **Luôn thêm `--no-state` cho lần
chạy thủ công:** nếu không, lần chạy thủ công làm tiến error cursor và baseline
restart, nên lần chạy theo lịch kế tiếp sẽ bỏ sót những error và restart mà lần
chạy thủ công đã thấy.

`status` in một JSON document (`result` là `ok`, `attention` hoặc `error`) với exit
code 0, 1 (một finding như `backup_stale` hoặc `schema_not_ready`) hoặc 2
(`configuration_invalid`, `database_unavailable`, `lock_timeout`,
`statement_timeout`, `database_error`, `internal_error`). `database.locks` chứa
`waiting` và `longest_wait_seconds`, con số lock-wait của checklist bên dưới; nó
chỉ được báo cáo, không phải ngưỡng. Nó chạy một transaction chỉ đọc bằng application
role với lock timeout 5 s; giữ `--statement-timeout` (mặc định 20 s) dưới 30 s để
`migrate` của một release không bao giờ bị nó làm abort.

Log. Trong production stack, backend ghi mỗi record một JSON object trên một dòng
(`ts`, `level`, `logger`, `message`, `request_id`, rồi các field của record). Mọi
response, kể cả refusal và failure, mang **HTTP request id** trong header
`X-Request-ID` (giá trị của client được giữ khi dài 1-64 ký tự thuộc
`A-Za-z0-9._-`, nếu không backend tự sinh), và mọi record backend của request đó
mang nó trong `request_id`. Để tìm một request:

```bash
$PF logs --no-log-prefix backend | grep '"request_id":"<id>"'
```

HTTP request id đổi sau mỗi lần resubmit. Vì vậy một production command luôn được
đối chiếu và retry bằng `device_event_id` của nó (the GUI's "request identity"),
không bao giờ bằng `request_id`. Để lần theo một command hoặc một Station, lọc các
record `app.access` theo field chúng mang:

```bash
$PF logs --no-log-prefix backend | grep '"device_event_id":"<id>"'
$PF logs --no-log-prefix backend | grep '"part_number":"<PN>"'
$PF logs --no-log-prefix backend | grep '"quantity_flow_id":<n>'
$PF logs --no-log-prefix backend | grep '"area_id":<n>'
$PF logs --no-log-prefix backend | grep '"station_id":"<id>"'
```

Một record `app.access` chứa `event`, `method`, `route` (route template; `path` chỉ
khi không route nào khớp), `status`, `duration_ms`, `client`, `outcome` (`ok`,
`created`, `replayed`, `refused` hoặc `error`), `slow`, `refusal` (`type`, `code`,
`message`) và `context`: PN, QuantityFlow, quantity, Area, Operation, Machine, Work
Order, `device_event_id`, Scan Station, `user_id` và `worker_id` mà request nêu.
`worker_id` chỉ xuất hiện trên record của command đã đi tới bước resolve identity
(command được tạo hoặc identity refusal): Worker của một command replay nằm trong
Movement của nó. Không bao giờ log: request hoặc response body, query string,
cookie, device token, CSRF token, badge, password, giá trị đã scan và dòng
`DETAIL` của PostgreSQL (database chạy với `log_error_verbosity=terse`). Ngoại lệ
duy nhất là first-run setup token: nó được in một lần, cho đến khi setup hoàn tất.
Read thường dưới 1 giây và mọi health poll không được ghi ở mức `INFO`
(`"slow":true` đánh dấu read từ 1 giây trở lên); designed refusal (`not_ready`,
`release_mismatch`, `password_check_busy`, một request bị từ chối) ở mức `INFO`, và
chỉ failure thật mới là `ERROR`. Khối lượng log backend đo được trong rehearsal:
khoảng 1,1 MB cho mỗi 1.000 command, nên rotation json-file 10 MB x 5 của stack
chứa khoảng 9 ngày log ở mức 5.000 command mỗi ngày; rehearsal là dữ liệu tổng
hợp, nên P16-S7 đo lại trên pilot host. `web` ghi một edge record JSON cho mỗi
request với cùng `request_id` (và upstream status), nên một `502` hoặc `504` từ
`web` được tìm theo `request_id` trong log `web` như mọi request khác; khối lượng
`web` đo được trong cùng rehearsal: khoảng 0,6 MB cho mỗi 1.000 command (khoảng 18
ngày rotation ở mức 5.000 command mỗi ngày).

`/api/health` là readiness: nó báo `release`, `commit`, `schema` (`current`,
`accepted`, `mismatch` hoặc `unknown`), `expected_revision`, `database_revision` và
`accepted_revision`, và trả 503 khi schema không khớp hoặc không kết nối được
database. `/api/health/live` chỉ là liveness (container health check dùng nó): nó
vẫn khỏe khi readiness là 503 trong lúc schema mismatch. `revision` in cùng các
thông tin từ một container one-off và chạy được khi `backend` đang dừng.

`web` ghi edge record của mọi request (JSON: client address, method, path không có
query string, status, bytes, duration, upstream status, user agent, `request_id`) và
backend ghi application record của mọi write, refusal, failure và slow read; cả hai
không bao giờ chứa query string, cookie hay header PartFlow. Một câu trả lời JSON
502 hoặc 504 đến từ `web` (`server_unavailable`) và nghĩa là kết quả của write
chưa rõ: xử lý bằng `device_event_id` gốc (bên dưới).

Trong production stack, backend kết nối bằng `partflow_app`. Một `permission denied` (SQLSTATE 42501) trong log backend nghĩa là một code path đã thử đổi history được bảo vệ hoặc thiếu một grant: một incident (§8), không bao giờ được sửa bằng cách cấp thêm quyền. Chạy `reconcile --check h` (§7) trước mọi việc khác.

Sau đó kiểm tra:

- CPU, RAM, disk, I/O, thời gian và lần reboot gần nhất của host;
- container restart count và health status;
- reverse proxy cùng ngày hết hạn certificate;
- PostgreSQL connection, lock, storage growth và backup age;
- trạng thái kết nối browser và write có bị block đúng hay không;
- error được correlate theo PN, QuantityFlow, Area, Operation, Machine, Worker,
  Scan Station và `device_event_id` khi liên quan.

Không retry một production command bị timeout bằng `device_event_id` mới khi
chưa xác định kết quả ban đầu. Query/retry bằng idempotency key cũ để response
không chắc chắn ở client không tạo write trùng.

## 3. Logical database backup

`deploy/production/backup.sh` (P16-S5) thực hiện backup. Chạy nó từ repository
root của một release checkout (checkout của release đang chạy cho backup theo
lịch và thủ công; `release.sh` gọi nó từ checkout của candidate) khi `db` của
production stack đang chạy. Dump được stream từ **bên trong `db`**
(`pg_dump --format=custom --no-owner --no-privileges --lock-wait-timeout=60s`,
một snapshot, client và server là cùng một binary), được list bằng `pg_restore
--list`, và được publish cùng manifest và checksum bởi service one-shot
`backup-tools` (không có network, chỉ mount backup directory). Repo cung cấp
artifact, việc verify và retention; mã hóa và off-host replication thuộc về
platform tool (bên dưới).

### Artifact và tên

Một backup là một thư mục `<backup-dir>/<NAME>/`, trong đó `<backup-dir>` là
`PARTFLOW_BACKUP_DIR` của `.env.production` và

```text
NAME = <UTC stamp>-<kind>[-<label>]      for example 20261008T020000Z-daily
                                         and 20261008T140000Z-pre-release-v1.0.0-rc.2
```

`kind` là `daily`, `manual` hoặc `pre-release`; `label` (release tag đích) bắt
buộc với `pre-release` và bị cấm với các loại khác; stamp là thời điểm UTC
`backup.sh` bắt đầu. Thư mục chứa đúng `partflow.dump`, `partflow.dump.list`
(`pg_restore --list`), `manifest.json` và `SHA256SUMS`. Nó được dựng dưới
`<backup-dir>/.partial/` và publish bằng một lần rename nguyên tử, nên một backup
hoặc tồn tại đầy đủ hoặc không tồn tại; crash chỉ để lại entry trong `.partial/`.
Entry không thuộc grammar (ví dụ `@eaDir` của DSM) bị bỏ qua và được báo cáo,
không bao giờ gây lỗi.

### Thực hiện backup

```bash
# scheduled daily backup (14 and 8 are placeholders for the owner's retention)
deploy/production/backup.sh --kind daily --operator scheduler --keep-daily 14 --keep-weekly 8
# manual backup (a reason is required)
deploy/production/backup.sh --kind manual --operator "<name>" --reason "<why>"
```

Backup `pre-release` do `release.sh` thực hiện (§5); dạng thủ công là
`backup.sh --kind pre-release --label <target tag> --tools-release <target tag>
--operator "<name>"`. Khi thành công, stdout đúng một dòng,
`BACKUP <NAME> <absolute path>`; tiến trình và báo cáo của các tool đi ra stderr.
`deploy/production/backup.sh --help` in mọi option:

| Option | Ý nghĩa |
| --- | --- |
| `--kind daily\|manual\|pre-release`, `--operator NAME` | bắt buộc |
| `--reason TEXT` | bắt buộc với `manual`; mặc định `scheduled daily backup` (daily) và `pre-release backup for TAG` (pre-release) |
| `--label TAG` | release tag đích; bắt buộc với `pre-release`, bị từ chối với loại khác |
| `--tools-release TAG` | image `backup-tools` ghi và verify manifest (mặc định: `PARTFLOW_RELEASE` của env file; dùng tag của candidate khi release đang chạy có trước P16-S5) |
| `--keep-daily N --keep-weekly N` hoặc `--no-rotate` | chỉ `daily`; retention của owner (bên dưới). Không có mặc định: lệnh theo lịch nêu rõ giá trị |
| `--reserve-mib N` | dung lượng trống giữ lại ngoài hai lần dump mới nhất (mặc định 1024) |
| `--lock-held-by-release DIR` | chỉ `release.sh` (nó giữ backup lock) |
| `--rehearsal --project NAME` | một Compose project tạm (không bao giờ `partflow-production`) |

Giá trị text dài 1-500 ký tự, không có control character, `"` hay `\`. Mỗi bước làm
gì, theo thứ tự (`backup: <step> ok (<ms> ms)` trên stderr):

1. `preflight`: tool, env file (`PARTFLOW_RELEASE`, `PARTFLOW_BACKUP_DIR`),
   `config`, `db` đang chạy, image `backup-tools`, và backup lock (bên dưới);
2. `identify`: release đang chạy, commit, image ID và Alembic revision mong đợi
   (best effort, không bao giờ gây lỗi);
3. `space`: phải còn trống trong backup directory ít nhất hai lần dump mới nhất
   (hoặc kích thước database khi chưa có dump) cộng reserve, nếu không bị từ chối
   và không ghi gì;
4. `dump`, `list` và `revision`: ba lệnh exec trong `db` với `TZ=UTC` (archive lưu
   các trường theo giờ local);
5. `manifest`: `backup-manifest` publish thư mục; `verify`: `backup-verify` kiểm
   tra nó; `rotate`: `backup-rotate` cho backup `daily`; `done`.

| Exit | Ý nghĩa | Làm gì |
| --- | --- | --- |
| 0 | hoàn tất | - |
| 1 | bị từ chối trước khi ghi (`backup_running`, `backup_lock_stale`, `insufficient_space`, `name_exists`) | đọc lý do được in; chưa ghi gì |
| 2 | không chạy được (cú pháp, tool, environment, `db` không chạy, không tạo được backup lock) | sửa và chạy lại |
| 3 | thất bại: không backup nào được publish, hoặc backup đã publish không qua verify | không dùng backup mà message nêu tên; xóa nó sau khi review |
| 4 | backup đầy đủ và đã verify, nhưng rotation thất bại hoặc phát hiện một backup daily không qua verify | review báo cáo `backup-rotate` trên stderr; xem Retention |

Mọi exit khác 0 được báo bằng failure notification của scheduler trên host.

### Manifest và verify

`manifest.json` (`manifest_version` 1) ánh xạ vào các trường deployment record ở
§1 và vào những gì một restore cần:

| Trường record | Manifest key |
| --- | --- |
| UTC timestamp | `dump_started_at`, `completed_at` |
| environment, host | `environment`, `host` |
| Git commit/image digest | `release.commit`, `release.tag`, `images.backend`, `images.web`, `images.db` |
| Alembic current revision | `alembic_revision` (và `alembic_rows`); `release.expected_revision` là revision release đang chạy mong đợi |
| PostgreSQL major version | `database.server_major` (và `server_version`, `pg_dump_version`, `database.name`) |
| checksum dump/list | `files[].sha256` và `SHA256SUMS` |
| operator và lý do backup | `operator`, `reason` |

Nó cũng ghi `kind`, `label`, image `tool` đã ghi nó, và các `dump` fact (`options`,
`toc_entries`, `table_data_entries`, `tables_checked`, `extra_tables`).
`tables_checked: true` nghĩa là mọi table trong table classification của release
đều có data entry; dump của revision khác được publish với `tables_checked: false`.
`alembic_version` không chứa đúng một revision hợp lệ, hoặc table mà release không
biết, được publish kèm warning (một backup trung thực của database đã lệch có giá
trị hơn không có backup); `migrate` từ chối backup như vậy (§5) và restore drill
thất bại ở bước revision.

Verify bất kỳ backup nào (chỉ đọc; cùng các check chạy trong `migrate` và trong
drill), và độc lập với mọi image sau mỗi lần copy:

```bash
$PF --profile ops run --rm -T --user "$(id -u):$(id -g)" backup-tools backup-verify "<NAME>"
(cd "<backup-dir>/<NAME>" && sha256sum -c SHA256SUMS)
```

(thêm tiền tố `PARTFLOW_RELEASE=<tag>` vào lệnh đầu để dùng image `backup-tools`
của release khác). `backup-verify` in một JSON document với các check
`directory`, `files`, `sha256sums`, `manifest`, `dump_header`, `list` và
`expect_database` (`--expect-database NAME`), exit 0 `verified`, 1 `invalid`
(`backup_invalid`: không dùng backup) hoặc 2 `failed`. Không có `--user`, container
không đọc được backup directory mode 0700.

### Backup directory

- một directory tuyệt đối đã tồn tại, nằm ngoài repository checkout, secrets
  directory và mọi archive directory; **chỉ production** (không bao giờ của
  staging hay của một drill); mode 0700, thuộc account chạy `backup.sh` và
  `release.sh`; file mode 0600; được tạo trước lệnh `$PF` đầu tiên, vì mọi lệnh
  Compose của stack cần `PARTFLOW_BACKUP_DIR` (`DEPLOYMENT.md` §3.1);
- dump chứa mọi table, kể cả credential hash và digest của session/device token:
  coi directory và mọi bản copy là secret;
- dung lượng trống: `2 x` dump mới nhất cộng reserve (mặc định 1 GiB).

Dump không mang grant theo thiết kế (`--no-privileges`): database role và privilege của chúng được derive lại sau restore (§4).

### Retention, off-host copy và alert

`backup.sh --kind daily` áp dụng `backup-rotate` (có thể chạy riêng, `--dry-run`
chỉ báo cáo mà không xóa):

```bash
$PF --profile ops run --rm -T --user "$(id -u):$(id -g)" backup-tools backup-rotate --keep-daily 14 --keep-weekly 8 --dry-run
```

Nó giữ `--keep-daily` backup daily đã verify mới nhất cùng backup daily mới nhất
của mỗi tuần trong `--keep-weekly` tuần ISO mới nhất (UTC; các tuần chồng lên cửa
sổ daily, giống restic `forget --keep-daily --keep-weekly`). Mỗi candidate được
verify trước; candidate không qua verify không được đếm và **không bao giờ bị
xóa** (exit 4: operator review nó, §9). **Backup pre-release và manual không bao
giờ bị rotate** (operator xóa chúng sau observation window, §9), và **archive
directory không bao giờ bị rotate hay prune bởi bất kỳ tool nào**, kể cả platform
tool. Phần còn sót trong `.partial/` cũ hơn 24 giờ bị xóa. Giá trị 14 và 8 là
placeholder: owner đặt giá trị thật trong lệnh theo lịch; chúng là option bắt
buộc, không bao giờ là default trong code.

Mã hóa và copy backup directory ra ngoài host bằng platform tool, không bao giờ làm
tay: Hyper Backup với client-side encryption tới đích off-NAS trên Synology
(`SYNOLOGY_NAS.md` §6), restic tới object storage trên VPS (`VPS.md` §8). Alert:
một lần chạy theo lịch thất bại (mọi exit khác 0, kể cả daily không qua verify) là
failure notification của scheduler, và replication thất bại là notification của
chính platform tool; cả hai được review hằng ngày (§9). Alert "backup quá cũ" là
`check.sh` `backup_age`: backup đã publish mới nhất, thuộc bất kỳ loại nào, cũ hơn
26 giờ (§2, §9; P16-S6); nó được cài trên pilot host ở P16-S7. Recovery point objective là daily schedule (24 giờ) cho đến khi
owner duyệt RPO và RTO (P16-S7).

### Backup lock

Các backup được tuần tự hóa bằng thư mục `<backup-dir>/.backup.lock` (file `owner`
của nó chứa `host`, `pid`, `started_at`, `by=backup.sh|release.sh` và `name` hoặc
`release`). Lần chạy thứ hai bị từ chối, không bao giờ xếp hàng: `backup_running`
nghĩa là một backup (hoặc một release đang giữ lock) đang chạy, nên hãy chờ.
`release.sh` giữ lock từ write freeze (hoặc bước backup) đến lúc chuyển `backend`,
nên hãy lên lịch daily backup ngoài maintenance window: một daily đang chạy sẽ
dừng release trước freeze mà không đổi gì, và một daily bắt đầu trong lúc release
sẽ bị từ chối. Lock hoàn toàn không tạo được (thư mục backup đầy, hết inode hoặc
read-only) được báo kèm lỗi `mkdir` là không chạy được (exit 2), không bao giờ là
`backup_running`; khi đó `release.sh` dừng với `could_not_run` trước freeze, không
đổi gì.

`backup_lock_stale` nghĩa là lock do một lần chạy không còn tồn tại để lại (bị
kill, bị out-of-memory kill hoặc mất điện); nó không bao giờ tự bị phá. Khôi phục
thủ công:

```bash
cat "<backup-dir>/.backup.lock/owner"   # host, pid, started_at, by and name|release
ps -p <pid>                             # on that host: must report no such process
$PF exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "SELECT count(*) FROM pg_stat_activity WHERE application_name = '"'"'pg_dump'"'"'"'   # must print 0
rm -r "<backup-dir>/.backup.lock" "<backup-dir>/.partial/<name>"   # the name from the owner file, when it has one
```

Không bao giờ xóa lock mà owner là `release.sh` khi release đó đang chạy. Khi
`release.sh` đang giữ backup lock (hoặc release lock của nó tồn tại), `check.sh`
bỏ qua các check `status` của mình (§5 Quan sát).

## 4. Restore test — không overwrite ngay

Restore vào database hoặc stack cô lập, tuyệt đối không restore thẳng lên
production database duy nhất:

1. verify checksum của dump và manifest;
2. provision cùng PostgreSQL major version hoặc target đã xác nhận tương thích;
3. tạo restore-test database trống;
4. restore bằng `pg_restore --single-transaction --exit-on-error --no-owner --no-privileges`
   (một transaction: restore thất bại để lại target trống);
5. start application release tương ứng trỏ vào database đó;
6. verify Alembic revision;
7. chạy health, representative read model, quantity/Movement/allocation
   reconciliation và smoke test được chỉ định;
8. ghi thời gian restore và kết quả;
9. chỉ xóa isolated restore copy sau khi đã giữ lại bằng chứng.

Khi restore vào một cluster mới, hãy provision database role trước (`provision-roles`, với password tạm trong restore drill), và chạy `apply-grants` trên database đã restore trước khi start application (bước 5): dump không chứa grant, nên database đã restore không có grant nào cho đến khi `apply-grants` derive lại, và application role không thể hoạt động nếu thiếu chúng. Reconcile check (h) phải pass trước khi một database đã restore phục vụ production: path 3 và new-instance restore (§6) chạy `reconcile` trước khi `backend` start. `restore-test.sh` restore trong project riêng của nó (bên dưới) và chạy `reconcile` đầy đủ khi application đã sẵn sàng; check (h) không `pass` làm drill thất bại.

Với production stack, toàn bộ quy trình là `deploy/production/restore-test.sh`
(P16-S5), chạy từ repository root của một release checkout:

```bash
deploy/production/restore-test.sh --backup <NAME> --operator "<name>"
# a candidate PostgreSQL image (glibc or image change), a baseline of known findings:
deploy/production/restore-test.sh --backup <NAME> --operator "<name>" \
    --db-image postgres:16.14-bookworm --baseline-report <records>/<release>/pre-reconcile.json
```

`deploy/production/restore-test.sh --help` in mọi option: `--release TAG` (release
chạy trên database đã restore; mặc định là release trong manifest, và backup lấy
giữa lúc migration và lúc ghi lại environment cần nêu tên release), `--tools-release TAG`,
`--db-image IMAGE`, `--baseline-report FILE`, `--project`, `--http-port`,
`--edge-subnet`, `--records-dir`, `--space-factor` và `--keep`.

Cô lập, do script thực thi: Compose project riêng `partflow-restore-<suffix>`
(không bao giờ `partflow-production`), với volume và network riêng, loopback port
và edge subnet riêng (không phải của production), database name cố định
`partflow_restore_test`, **password database-role tạm được sinh ra** (secret file
bị xóa khi teardown), một backend worker, và backup directory được mount **read-only**;
nó không bao giờ chạy `migrate`, không bao giờ ghi vào backup directory và từ chối
một project đã có container hoặc volume. Nó cần `5 x` dump cộng 1 GiB trống trên
Docker root (`--space-factor`).

Script thực hiện chín bước trên và ghi từng bước vào evidence: `verify` (bước 1:
`backup-verify`), `db_start` (bước 2 và 3: database trống trên PostgreSQL image
mà `compose.production.yaml` của checkout pin, hoặc `--db-image`; evidence so major
của nó với major của manifest), `restore` (bước 4, một transaction), `roles` và
`grants` (`provision-roles`, rồi `apply-grants` trước khi application start),
`revision` (bước 6: state `current` và revision của manifest), `app_start` (bước 5
và health của bước 7: `backend` và `web` lên, readiness `current`), `reconcile`
(`reconcile` đầy đủ: các check (a)-(f) của nó replay Movement history với mọi
projection và allocation đã lưu, đó là evidence về quantity, movement và
allocation của bước 7), `smoke` (`deploy/production/smoke.sh`, smoke được chỉ
định), `evidence` (bước 8) và `teardown` (bước 9: `down -v` chỉ cho drill project,
sau khi evidence file đã tồn tại và chỉ khi lần chạy này tạo project; `--keep` giữ
nó và in command).

Quy tắc pass: `passed` cần reconcile exit 0 **và** check (h) `pass` **và** check
(j) `pass` (grant vừa được áp dụng mới trên drill, nên một finding (h) hoặc
`not_applicable` là defect thật; với `--db-image`, check (j) là nửa
glibc/PostgreSQL-image của platform-upgrade identity check, §7).
`passed_with_preexisting_findings` cần `--baseline-report` và mọi finding, kể cả
của (j), đều có trong report đó (`reconcile_regression.py`), với (h) `pass`. Mọi
trường hợp khác là `failed`. Evidence
`<records-dir>/<UTC>-restore-test-<NAME>/evidence.json` ghi outcome, bước thất
bại, các fact của backup và drill, version/collation/collation version của drill
server, PostgreSQL major của backup và của drill server cùng `server_major_match`
(`false` cũng được in thành warning: không phải drill cùng major; `null` khi một
trong hai không rõ), timing từng bước (`restore_to_ready` và `total` là input đo RTO) và các
trạng thái reconcile.

| Exit | Ý nghĩa |
| --- | --- |
| 0 | `passed` hoặc `passed_with_preexisting_findings` |
| 1 | `failed`, hoặc không đủ dung lượng trống |
| 2 | không chạy được, hoặc bị ngắt (không để lại gì đang chạy trừ khi `--keep`) |
| 3 | teardown thất bại: evidence vẫn có giá trị, message in `docker compose -p <project> down -v` |

**Giới hạn đã ghi nhận:** việc đọc lại có xác thực từng màn hình read model chưa
được tự động hóa (cần account thật của dữ liệu đã restore); evidence ghi
`read_model_readback: not_automated`. Owner có thể kiểm tra thủ công: chạy drill
với `--keep`, đăng nhập vào loopback port được in ra, rồi teardown project bằng
command được in.

Dùng tên restore-test rõ ràng. Không thay production database name vào command
diễn tập.

## 5. Release và migration

### Trước maintenance

- duyệt chính xác release revision và scope;
- xác nhận kết quả CI/quality của revision đó;
- review mọi migration cùng hành vi downgrade/recovery;
- ước lượng lock/time/disk impact bằng staging data;
- verify off-site backup (notification gần nhất của platform tool) và backup mới
  nhất còn gần đây (§3); `release.sh` lấy pre-release backup đã verify **bên trong
  write freeze** khi có migration đang chờ (không có khoảng hở giữa backup và
  migration), hoặc trước bước switch trong trường hợp khác;
- xác nhận previous release còn dùng được;
- chạy reconciliation (§7) trên release hiện tại, giữ report, và mở incident cho
  mọi finding; trong production stack chạy nó, và mọi CLI recovery, với tag
  **hiện tại**, trước khi tag candidate được ghi vào `.env.production` (candidate
  chỉ nằm trong shell cho đến lúc switch);
- quyết định có cần dừng write hay không (migration đang chờ luôn cần write
  freeze bên dưới);
- thông báo window và deadline quyết định rollback, và nhắc các station hoàn tất
  dialog đang mở;
- lên lịch daily backup ngoài maintenance window: một backup đang chạy dừng
  release trước freeze mà không đổi gì (`backup_running`, §3), và một daily bắt đầu
  trong lúc release sẽ bị từ chối.

### Write freeze

Write freeze là **dừng `backend`** (`$PF stop backend`); không có application mode
nào khác. `uvicorn` hoàn tất các request đang chạy, kể cả import, và lệnh dừng chờ
đến 200 s; với hơn một worker, supervisor giữ socket mở cho đến khi worker cuối
thoát, nên request gửi trong lúc đó treo và kết thúc bằng 504 hoặc 502 của `web`
(DEPLOYMENT §3.1 Process model): bắt đầu freeze khi không có import nào đang chạy.
Khi `backend` đang dừng, `web` vẫn phục vụ shell và trả `/api/*` bằng JSON 504 rồi
502, mọi client chuyển sang OFFLINE banner trong khoảng một giây và mọi write
control bị chặn. Dialog đang mở giữ draft; một write va vào lúc dừng là unknown
outcome, giữ `device_event_id` của nó và được retry bằng chính nó sau khi mở lại
trên **cùng** release. Sau khi mở lại trên release **mới**, page cũ không gửi được
nó (bị từ chối 409); operator reload và kiểm tra Area hoặc Work Order trước khi lặp
lại action. Một import bị cắt chỉ để lại các Work Order đã commit nguyên vẹn.
`reconcile`, `revision`, `migrate` và backup chạy từ container one-off trên `db`
khi đang freeze. **Mở lại** bằng `$PF up -d backend` trên cùng release, chỉ sau các
check của bước bên dưới; ở một release switch, việc mở lại là bước chuyển `web`.

### Thực hiện

Chạy `deploy/production/release.sh` từ repository root của release checkout, với
release tag đã checkout:

```bash
deploy/production/release.sh --release <new-tag> --operator "<name>" --approver "<name>"
```

(`deploy/production/release.sh --help` in mọi option.) Khi không có option backup
nào, `release.sh` tự lấy pre-release backup đã verify. `--pre-release-backup <NAME>`
nêu tên một backup `backup.sh` đã có và chỉ dành cho release **không** có migration
đang chờ (nếu có, `release.sh` dừng mà không đổi gì, vì backup phải được lấy bên
trong freeze); `--no-backup-reason "<text>"` chỉ dành cho lần cài đặt đầu tiên hoặc
rehearsal (được ghi lại). Nó chạy, ghi lại từng bước: preflight (tool, environment file, dạng tag, build input sạch); revision
hiện tại và reconcile pre-release với release đang chạy; build candidate (tag đã có
không bao giờ build lại, và chỉ được dùng lại khi cả hai image được build từ commit này
với đúng release này); check (j) và revision của candidate (phải báo đúng release và
commit này); write freeze khi có
migration đang chờ; pre-release backup đã verify (bước 6a, `pre_release_backup`:
`backup.sh --kind pre-release`, sau freeze khi có migration đang chờ, giữ backup lock
từ freeze đến lúc chuyển `backend`); `migrate` (verify backup trước và cũng áp dụng grant); reconcile post-release; chuyển `backend` trong khi
`web` vẫn phục vụ bundle trước (write vẫn bị từ chối, mọi page đã tải gửi release
trước và nhận 409), chờ health của release mới và schema `current`; chuyển `web`,
việc này mở lại write; và `smoke.sh`. Một check thất bại sau switch sẽ dừng
`backend` lại. Preflight của nó cũng từ chối, không đổi gì, khi `partflow_app_password`, `partflow_maintenance_password` hoặc `postgres_password` thiếu, rỗng hoặc không phải regular file.

`migrate --pre-release-backup NAME` verify backup theo quy tắc `backup-verify` trước khi nó kết nối, và chỉ chấp nhận khi backup còn mới và thuộc đúng database này: dump phải **bắt đầu tại hoặc sau** thời điểm write-freeze (`--backup-not-before`, do `release.sh` truyền: thời điểm freeze khi có migration đang chờ, nếu không là thời điểm release bắt đầu hoặc record hoàn tất mới nhất), nên nó chứa mọi write mà `backend` đã commit; không có option đó thì áp dụng giới hạn tuổi 60 phút, nhưng chỉ khi không có migration đang chờ (có migration thì `migrate` từ chối `backup_freshness_unproven`). Revision và database name của backup phải bằng của database. Một từ chối (`backup_not_found`, `backup_invalid`, `backup_stale`, `backup_revision_mismatch`, `backup_freshness_unproven`, `backup_database_mismatch`; exit 1) không đổi gì và, khi đang frozen, mở lại write trên release hiện tại như mọi từ chối khác; backup directory không đọc được là `backup_unreadable` (exit 2: container cần `--user "$(id -u):$(id -g)"`, mà `release.sh` truyền). Free text bị từ chối: `--pre-release-backup` nêu tên một backup directory.

Mọi `migrate` áp dụng grant trong cùng transaction với upgrade. Kết quả `refused` với một mã database-role (`roles_not_provisioned`, `roles_incomplete`, `role_unsafe`, `role_owns_objects`, `foreign_grantor`, `table_unclassified`, `table_missing`, `not_superuser`) rollback toàn bộ lần chạy và dừng release trước khi đổi gì (exit 1, như mọi từ chối khác); đọc thông báo được in, sửa nguyên nhân (`$PF --profile ops run --rm -T db-roles` cho mã role, `DEPLOYMENT.md` §3.1) rồi chạy lại. Khi cài đặt lần đầu, thứ tự là: secret file, build, `db`, `db-roles`, `migrate`, first-run setup (`DEPLOYMENT.md` §3.1).

| Exit | Ý nghĩa | Làm gì |
| --- | --- | --- |
| 0 | hoàn tất | quan sát (bên dưới) |
| 1 | dừng khi chưa đổi gì, hoặc write đã mở lại trên release hiện tại | đọc lý do được in và `record.json`; sửa; chạy lại |
| 2 | không chạy được (cú pháp, tool, environment) | chưa đổi gì; sửa và chạy lại |
| 3 | `backend` bị để dừng | làm theo §6; đọc `regression.txt` hoặc so sánh `pre-reconcile.json` và `post-reconcile.json` |
| 4 | release mới có thể đang chạy và ghi được sau một check thất bại, và re-freeze cũng thất bại | tự chạy `$PF stop backend`, rồi làm theo §6 |
| 130, 143 | bị ngắt bởi Ctrl-C hoặc TERM (record `outcome: interrupted`, bước cuối cho biết bị ngắt ở đâu); việc ngắt không start hay stop service nào | bị ngắt trong `migrate`: kết quả không rõ (record `migration.result: outcome_unknown`, `alembic.after: null`), nên chạy `$PF run --rm --no-deps -T backend python -m app.cli revision` trước mọi việc khác; rồi `$PF ps` và §6 (sau `switch_backend`, `.env.production` đã ghi tag mới; `env-before.txt` trong record directory là file trước đó) |

`--accept-pre-release-findings` tiếp tục khi reconcile pre-release có finding và chỉ
chặn với finding không có trong đó (`deploy/production/reconcile_regression.py` so
sánh hai report). `--skip-pre-reconcile REASON` dành cho trạng thái rollback path 2
mà không image nào có database revision làm head: lý do được ghi lại và mọi finding
sau release đều chặn.

Dạng thủ công tương đương, cùng thứ tự, với tag hiện tại trong `.env.production`:

1. Ghi application và Alembic revision hiện tại
   (`$PF run --rm --no-deps -T backend python -m app.cli revision`) và chạy
   reconcile pre-release với tag hiện tại (§7).
2. Build: `PARTFLOW_RELEASE=<new> PARTFLOW_COMMIT=$(git rev-parse HEAD) $PF -f compose.production.build.yaml build`
   (không bao giờ dùng tag đã có), rồi rehearsal candidate image trên database chưa
   đổi:
   `PARTFLOW_RELEASE=<new> $PF run --rm --no-deps -T backend python -m app.cli reconcile --check j`
   (check thất bại thì dừng release; chưa có gì thay đổi).
3. Freeze khi có migration đang chờ: `$PF stop backend`, xác nhận
   `$PF ps --status running -q backend` không in gì, rồi ghi lại thời điểm,
   `T=$(date -u +%Y-%m-%dT%H:%M:%SZ)` (khi không có migration đang chờ, ghi `T`
   trước bước 3a).
   3a. Lấy backup từ candidate checkout:
   `deploy/production/backup.sh --kind pre-release --label <new> --tools-release <new> --operator "<name>"`;
   ghi lại NAME trong dòng `BACKUP`.
4. `PARTFLOW_RELEASE=<new> $PF --profile ops run --rm -T --user "$(id -u):$(id -g)" migrate --pre-release-backup NAME --backup-not-before "$T"`
   (`--no-backup-reason TEXT` thay cho backup chỉ với lần cài đặt đầu tiên hoặc
   rehearsal). Không có `--backup-not-before`, `migrate` từ chối migration đang chờ
   (`backup_freshness_unproven`), và không có `--user` nó không đọc được backup
   directory mode 0700 (`backup_unreadable`).
   Lưu JSON output của nó (trường `grants` báo grant đã áp dụng, trường `backup`
   báo kết quả verify) và revision mới.
5. Chạy reconcile post-release với tag mới (§7) và so sánh với report pre-release:
   chỉ finding không có trong đó mới chặn bước 7; finding đã có từ trước vẫn là
   incident mở theo quyết định của owner.
6. Ghi `PARTFLOW_RELEASE=<new>` vào `.env.production` (và xóa
   `PARTFLOW_ACCEPT_SCHEMA_REVISION`), rồi `$PF up -d --no-deps backend`; check
   health: `release` là tag mới và `schema` là `current`. Với database chưa có
   Administrator, hoàn tất first-run setup (setup token nằm trong backend log)
   trước khi mở truy cập; start backend với một worker cho bước này
   (`PARTFLOW_BACKEND_WORKERS=1 $PF up -d backend`, rồi `$PF up -d backend`). Sau
   đó enroll từng thiết bị Scan Station (Administration → Scan Stations →
   `Devices…`).
7. `$PF up -d --no-deps web`: việc này mở lại write. Chạy
   `deploy/production/smoke.sh --release <new>` và các check authorization,
   scan-focus/connectivity và write/read-back được chỉ định.
8. Chỉ mở lại write khi mọi kiểm tra bắt buộc pass; nếu không, `$PF stop backend`
   lại và làm theo §6. Khi `release.sh` exit 3, đọc `regression.txt` (hoặc so sánh
   hai report) rồi mở lại như bước 6 và 7 hoặc làm theo §6.

### Quan sát

Monitor error, latency, lock, restart, disk và phản hồi operator trong observation
window đã định: các dòng của `check.sh` (`errors`, `restarts`, `disk_*`), slow read
(`"slow":true`) và `status`, gồm cả lock wait của nó (§2). Một check trong lúc write
freeze báo `backend` đã dừng (một notification) và bỏ qua các check `status` khi
release lock còn tồn tại. Giữ previous release cùng pre-release backup (không bao giờ bị rotate). Các page đang mở trong lúc switch
hiện update notice và reload (GUI_DESIGN §3 rule 13); một Scan Station hoặc
Production Board không người trực tự reload khi không còn dialog nào mở.

## 6. Cây quyết định rollback

1. **Không có schema migration:** redeploy previous immutable application release:
   đặt tag trước vào `.env.production`, `$PF up -d backend web`, rồi
   `deploy/production/smoke.sh --release <previous>`. Nó cần image của release
   trước còn trên host: giữ chúng trong suốt rollback window và không bao giờ chạy
   `docker image prune -a`; `release.sh` từ chối bắt đầu khi chúng thiếu.
2. **Schema đã migrate và backward-compatible:** chỉ deploy release trước nếu
   compatibility đã được verify rõ và ghi lại. Đọc `database_revision` từ
   `$PF run --rm --no-deps -T backend python -m app.cli revision` (chạy với release
   mới), đặt `PARTFLOW_RELEASE=<previous>` và
   `PARTFLOW_ACCEPT_SCHEMA_REVISION=<database_revision>` trong `.env.production`,
   `$PF up -d backend web`, rồi
   `deploy/production/smoke.sh --release <previous> --allow-accepted-schema`.
   Override nêu đúng một revision mà release trước không biết (revision nó biết thì
   bị bỏ qua, `revision` hiện `override_ignored`, và readiness vẫn là `mismatch`),
   nó không bao giờ khớp revision nào khác, và release forward kế tiếp xóa nó. Trước
   khi override được đặt, release trước từ chối mọi thay đổi; station read vẫn ghi
   thời điểm last-seen của thiết bị (device bookkeeping, không có dữ liệu
   production). Release forward từ trạng thái này dùng candidate image cho
   reconcile pre-release (`release.sh` làm vậy) hoặc `--skip-pre-reconcile REASON`.
3. **Schema đã migrate nhưng không backward-compatible hoặc chưa rõ:** stop
   write; restore pre-migration database vào một database mới, trống trong
   PostgreSQL instance của production (`createdb --template=template0`, restore
   trong một transaction; database đã migrate được giữ nguyên) và deploy previous
   application release tương ứng, theo *Thủ tục path 3* bên dưới. Việc restore
   pre-release backup không bao giờ bỏ các write sau nó nếu chưa qua escalation
   của path 4.
4. **Đã có production write mới sau migration:** không blindly restore đè lên.
   Escalate; bảo toàn cả current database và pre-release backup, xác định forward
   fix hoặc audited data-recovery plan và giữ application ở write-blocked.
   Pre-release backup và database đã migrate đều được bảo toàn.

CLI recovery chạy với release khớp database.

### Thủ tục path 3

*Ghi chú về wording:* trước P16-S5, path 3 ghi "restore pre-migration database
vào instance sạch". P16-S5 triển khai nó thành một database mới trong cùng
instance (không bao giờ overwrite, không cần đổi volume, và database role là
cluster-global); việc chỉnh câu này đang chờ owner chấp nhận
(`IMPLEMENTATION_ROADMAP.md`, slice P16-S5). Nếu owner muốn một volume mới thì chỉ
bước B đổi, không gì khác.

Path 3 chỉ áp dụng khi release record cho thấy `writes_reopened_at: null` (release
dừng ở trạng thái frozen) hoặc owner ghi lại rằng không có production write nào
sau migration; nếu không thì là path 4. Restore vào production cần **sự phê duyệt
của owner, được ghi trong `rollback.json` trước khi `backend` start** trên
database đã restore; không có nó, thủ tục dừng sau `reconcile` với application ở
write-blocked.

Các key của `.env.production` do Compose đọc và không được export vào shell, nên
mọi giá trị bên dưới được đặt tường minh. Mỗi block là một script: lưu nó trong
release record directory và chạy bằng `sh -eu <file>`, không bao giờ paste từng
dòng. Một guard không thỏa sẽ in lý do và dừng script trước lệnh kế tiếp, và mọi
lệnh thất bại cũng vậy (`-e`). Bước A chạy trong checkout của release
**candidate** (`release.sh` của nó đã lấy backup, nên nó có `backup-tools` của
P16-S5 dù release trước có trước P16-S5):

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

Bước B chạy trong checkout của release **trước**, với image của nó còn trên host
(từ đây không dùng service P16-S5 nào), thành ba script. B1 kiểm tra dung lượng
và restore vào một database mới:

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

Sau đó sửa `.env.production`: `PARTFLOW_RELEASE=<previous tag>`,
`POSTGRES_DB=<NEWDB do B1 in ra>` và `PARTFLOW_ACCEPT_SCHEMA_REVISION=` (để
trống). B2 kiểm tra database đã restore với release trước:

```sh
REC=<release record directory>
PF="docker compose -f compose.production.yaml --env-file .env.production"
$PF --profile ops run --rm -T db-roles apply-grants               # roles already exist in the cluster
$PF run --rm --no-deps -T backend python -m app.cli revision      # "state": "current" (otherwise it exits non-zero: B2 stops)
rc=0
$PF run --rm --no-deps -T backend python -m app.cli reconcile --max-findings 10000 > "$REC/rollback-reconcile.json" || rc=$?
echo "reconcile exit $rc"
```

Sự phê duyệt của owner được ghi trong `$REC/rollback.json` trước B3 (xem trên).
B3 start release trước trên database đã restore và chỉ chuyển `web` sau khi
`backend` báo sẵn sàng (thứ tự S3 DV-8):

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

`reconcile` phải exit 0, hoặc exit 1 chỉ với các finding có trong
`pre-reconcile.json` của release (`deploy/production/reconcile_regression.py`).
`rollback.json` mang các trường của §1 cùng `approved_by` (owner), `approved_at`
(UTC) và `reason`, tên database mới và cũ, tên backup và cả hai kết quả
reconcile. **Database đã migrate giữ nguyên tên và nội dung** cho phân tích path 4;
owner xóa nó sau observation window
(`$PF exec -T db sh -c 'dropdb -U "$POSTGRES_USER" "$1"' sh <old name>`), không
bao giờ do một tool.

### New-instance restore

Cho host hỏng (§8) hoặc di chuyển (`SYNOLOGY_NAS.md` §10), trên host mới với release
checkout khớp (release đã lấy backup, tối thiểu P16-S5) và một volume
`postgres_data` **mới, trống**. Backup directory đã copy được kiểm tra trước
(`sha256sum -c SHA256SUMS` và `backup-verify`, như bước A). Sau đó, với cùng `PF`,
`BPATH` và quy tắc dung lượng như bước B1 (`DB_BYTES=0`):

1. `$PF up -d db`;
2. kiểm tra rỗng: `$PF exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = '"'"'public'"'"'"'` phải in `0`, nếu không thì dừng: volume không phải mới;
3. dòng `pg_restore` của bước B1 vào database của container, `$PF exec -T db sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --single-transaction --exit-on-error --no-owner --no-privileges' < "$BPATH/partflow.dump"` (thất bại thì rollback, database vẫn trống, và kiểm tra rỗng lại pass trước khi thử lại);
4. `$PF --profile ops run --rm -T db-roles`, rồi `… -T db-roles apply-grants`;
5. `revision` và `reconcile` như bước B2, giữ report trong records directory;
6. sự phê duyệt của owner được ghi trong `<records-dir>/<UTC>-restore-<NAME>/restore.json` (cùng các trường như `rollback.json`), rồi `$PF up -d backend`, chờ health như bước B3, `$PF up -d --no-deps web` và `deploy/production/smoke.sh --release <tag>`.

Ghi lại data-loss window so với RPO đã duyệt.

Không mặc định `alembic downgrade` an toàn. PartFlow chủ động bảo vệ append-only
history và downgrade có thể bị từ chối hoặc làm mất loại dữ liệu mới.

## 7. Reconciliation

Reconciliation là command backend chỉ đọc `python -m app.cli reconcile` (Phase 16
slice 1). Nó chạy các check dưới đây trong một database snapshot chỉ đọc và in
một JSON report ra stdout; nó không bao giờ repair gì.

```bash
# development stack
f=reconcile.json
docker compose exec -T backend uv run python -m app.cli reconcile > "$f"; rc=$?
python3 -c 'import json,sys; r=json.load(open(sys.argv[1])); assert r["exit_code"]==int(sys.argv[2]); print(r["result"], r["exit_code"])' "$f" "$rc" \
  || echo "could not run: no complete report (exit $rc)"
docker compose exec -T backend uv run python -m app.cli reconcile --check j     # identity check only
```

- **Staging do pf quản lý:** `pf` từ chối `exec`/`run`, nên chạy dạng raw Compose
  trong `SYNOLOGY_ADMIN.md` §14 bên ngoài controller, không bao giờ chạy đồng thời
  với `pf update`, `pf backup`, `pf reset-db`, `pf purge` hoặc
  `pf restore-instance`. Ghi report vào home của operator, không bao giờ vào thư
  mục do pf quản lý:

  ```sh
  f="$HOME/partflow-reconcile-$(date -u +%Y%m%dT%H%M%SZ).json"
  sudo env PARTFLOW_REPO_ROOT=/volume1/docker/partflow/repo     PARTFLOW_DATABASE_URL='postgresql+psycopg://<user>:<percent-encoded password>@db:5432/<db>'     DEPLOY_ADMIN_INSTANCE_ID=<instance UUID từ 'pf instances'>     docker compose     --project-directory /volume1/docker/partflow/repo     --env-file /volume1/docker/partflow/config/.env     -p partflow-staging     -f /volume1/docker/partflow/control/compose.nas.yaml     exec -T backend uv run python -m app.cli reconcile > "$f"; rc=$?
  python3 -c 'import json,sys; r=json.load(open(sys.argv[1])); assert r["exit_code"]==int(sys.argv[2]); print(r["result"], r["exit_code"])' "$f" "$rc"     || echo "could not run: no complete report (exit $rc)"
  ```

  Path, project name và UUID là của chính instance đó (giá trị ví dụ lấy từ
  `SYNOLOGY_ADMIN.md` §14). Cả ba biến đều bắt buộc; thiếu biến thì Compose từ
  chối khởi động và không ghi report nào. `DEPLOY_ADMIN_INSTANCE_ID` sai sẽ gắn
  nhầm label mọi resource mà Compose tạo ra (§14); bản thân `exec` không tạo
  container, volume hay network nào.
- **Production:** chạy từ release checkout với tag đang chạy; nó cũng chạy khi
  `backend` đang dừng (`--no-deps` không start service nào khác, và database phải
  đang chạy):

  ```sh
  f="reconcile-$(date -u +%Y%m%dT%H%M%SZ).json"
  $PF run --rm --no-deps -T backend python -m app.cli reconcile > "$f"; rc=$?
  ```

  rồi áp dụng cùng bước kiểm tra report như trên. Dạng candidate-image cho check
  (j), trước lúc switch một release, là
  `PARTFLOW_RELEASE=<new> $PF run --rm --no-deps -T backend python -m app.cli reconcile --check j`
  (§5, Thực hiện, bước 2).
- **Option:** `--check ID` (lặp lại được, `a` đến `j`; các check còn lại là
  `skipped`), `--statement-timeout SECONDS` (1-3600, mặc định 300),
  `--max-findings N` (1-10000, mặc định liệt kê 100 finding mỗi check;
  `finding_count` vẫn đầy đủ).
- **Exit code:** `0` sạch, `1` mismatch (có ít nhất một finding), `2` không chạy
  được hoặc report không đầy đủ (check `error` hoặc lỗi cấp run thắng finding).
  Exit status chỉ có giá trị **khi** report là một JSON document hoàn chỉnh có
  `exit_code` bằng nó; report rỗng hoặc không parse được nghĩa là "không chạy
  được" bất kể status (chính Compose thoát với 1 trước khi chạy gì khi service
  đã dừng hoặc project sai).
- Lưu report trong deployment record (§1, "Smoke/reconciliation results").

Các check, mỗi check giữ yêu cầu gốc làm định nghĩa:

| Check | Yêu cầu |
| --- | --- |
| (a) | current-position projection replay được từ non-reversed Movement history; |
| (b) | mỗi active/closed flow có conservation history hợp lệ, cùng các invariant cross-row của SLICE1 §17 trừ invariant audit-row (được bảo đảm bởi transaction protocol và đã có API test hiện có phủ); |
| (c) | introduced quantity theo PN reconcile với active, stocked, scrapped và reversed outcome theo canonical rule; |
| (d) | assigned quantity trên Machine reconcile với flow đang ở từng Machine; |
| (e) | `released_quantity` của demand được derive từ evidence `RECEIVED`; |
| (f) | `allocated_quantity` của demand và `completed_at` của Work Order reconcile với active allocation row; correction beyond-demand được cấp quyền (các allocation row ghi `exceeds_demand`, Phase 14 slice 5) không bị báo là allocation vượt requested quantity; |
| (g) | không retained Movement nào reference row đã purge; |
| (h) | không append-only table nào bị mutate ngoài archive/purge path đã duyệt; được hiện thực thành một guard-integrity check: guard trigger hiện diện, được bật và không đổi, source của guard function không đổi, `partflow_app` và `partflow_maintenance` giữ đúng grant của chúng (không UPDATE, DELETE hay TRUNCATE trên history append-only), các role giữ attribute an toàn và không có membership, PUBLIC và default privilege không giữ gì, và `session_replication_role` không bị đặt thành `replica`. Nó không thấy được một mutation do superuser thực hiện sau khi tắt trigger hoặc đặt `session_replication_role` trong session của chính nó rồi khôi phục (giới hạn đã chấp nhận, quyết định owner OD-16-09); |
| (i) | Hot list entry là demand đang active (query của `DEPLOYMENT.md` §5); |
| (j) | canonical identity dưới interpreter và database đang chạy: canonical PN, Worker badge không phân biệt hoa/thường, rule prefix Asset Tag, mọi canonical-form CHECK được đánh giá lại dưới collation và ctype hiện tại của database, collation version và duplicate probe không phụ thuộc index cho các identity key (platform-upgrade identity check). |

(g) báo `not_applicable` cho đến khi có Movement-history archival, và (h)
chỉ báo `not_applicable` trên database không có các database role của PartFlow
(development, test, staging); cả hai trung lập với exit code. Trong
production stack (`DATABASE_ROLES_REQUIRED=true`) một role thiếu là một finding.

Ánh xạ sửa chữa cho finding của check (h) (reconciliation không bao giờ repair; các lệnh dưới đây là của operator, owner quyết định):

- mã privilege trên `partflow_app`, `partflow_maintenance` hoặc PUBLIC, và mã schema, database và default-privilege cho các grantee đó: `$PF --profile ops run --rm -T db-roles apply-grants`;
- mã role trừ `ROLE_OWNS_OBJECTS` (`ROLE_ATTRIBUTE`, `ROLE_MEMBERSHIP`, `ROLE_MISSING`) và một `REPLICATION_ROLE_SETTING` theo phạm vi role (`<db>/<role>` hoặc `*/<role>`): `$PF --profile ops run --rm -T db-roles`, rồi `… db-roles apply-grants`;
- finding về một database role khác (mọi mã privilege, column, schema, database hoặc default-privilege — `PRIVILEGE_EXCESS`, `COLUMN_PRIVILEGE`, `SCHEMA_PRIVILEGE`, `DATABASE_PRIVILEGE`, `DEFAULT_PRIVILEGE` — có grantee là role khác PUBLIC, `partflow_app` và `partflow_maintenance`; một từ chối `foreign_grantor` của `apply-grants` hoặc `migrate`): xem xét ai đã cấp và vì sao, và revoke nó bằng owner hoặc grantor; `apply-grants` cố ý để nguyên nó;
- `TABLE_UNCLASSIFIED` và `TABLE_MISSING` (và các từ chối `table_unclassified`, `table_missing` của `apply-grants` hoặc `migrate`): một table được tạo hoặc bị drop ngoài migration là một incident (§8); owner quyết định. `apply-grants` và `migrate` từ chối cho đến khi table lạ được gỡ bỏ (hoặc một release phân loại nó), hoặc table bị thiếu được khôi phục;
- mã trigger (`TRIGGER_MISSING`, `TRIGGER_CHANGED`, `TRIGGER_DISABLED`, `TRIGGER_ENABLE_MODE`), mã function (`GUARD_FUNCTION_MISSING`, `GUARD_FUNCTION_CHANGED`), `ROLE_OWNS_OBJECTS` (và từ chối `role_owns_objects`), một `REPLICATION_ROLE_SETTING` toàn database (`<db>/*`) và `REPLICATION_ROLE_ACTIVE`: một incident (§8); owner quyết định.

Không bao giờ sửa bằng cách chạy lại hoặc downgrade migration. Một lần chạy đua với thay đổi grant hoặc trigger có thể cho finding tạm thời: chạy lại trước khi hành động.


Quy tắc vận hành:

- Một snapshot chỉ đọc; command chỉ lấy table lock `ACCESS SHARE`, trước
  snapshot, và không lấy row lock hay advisory lock. Nó fail sau 5 s chờ table
  lock (sớm hơn khi `--statement-timeout` ngắn hơn), hoặc ngay lập tức khi yêu
  cầu lock của nó bị deadlock với một session khác. Không bao giờ chạy migration
  đồng thời, và chạy ngoài giờ cao điểm.
- Rehearsal platform-upgrade cho (j): với nâng cấp Python/UCD, chạy `--check j`
  từ candidate backend image trên database hiện tại; với thay đổi glibc hoặc
  PostgreSQL image, chạy restore drill vào candidate server,
  `deploy/production/restore-test.sh --backup <latest> --db-image <candidate image>`
  (§4; `reconcile` đầy đủ của nó thực thi check (j) và các trigger definition của
  check (h) trên server đó), hoặc, sau một thay đổi image tại chỗ cùng major
  version, `reconcile --check j` trước khi mở lại write (`COLLATION_VERSION_MISMATCH`
  là quyết định của owner). Owner quyết định re-canonicalization trước mọi nâng
  cấp.
- Với Work Order báo `not_completed_but_fully_allocated`, `expected` là giá trị
  replay, không phải đề xuất repair; owner chọn done date trong incident.
- Exit khác 0 là incident (§8). Không bao giờ sửa trực tiếp history hoặc
  projection; owner quyết định từng repair.

Dạng chạy theo lịch (P16-S6; daily schedule được cài trên pilot host ở P16-S7):
`deploy/production/scheduled-reconcile.sh`, chạy hằng ngày lúc 04:00 từ checkout
của release đang chạy, bằng account sở hữu `PARTFLOW_BACKUP_DIR`. Nó chạy
`reconcile` đầy đủ trong production stack, ghi report vào
`~/partflow-monitoring/reconcile/<UTC timestamp>-reconcile.json` (`--reports-dir`;
thư mục 0700, report 0600), và áp quy tắc exit code ở trên qua `monitor_report.py`:
nó in `RECONCILE clean|mismatch|error|could_not_run <report>` cùng một dòng
`FAIL <check> <title>` cho mỗi check thất bại, và thoát 0 (clean), 1 (mismatch)
hoặc 2 (không chạy được, kể cả khi report không đầy đủ). Nó từ chối chạy khi một
release đang giữ lock của nó hoặc backup lock (exit 2), dừng một lần chạy dài hơn
`--max-runtime-minutes` (60) và không bao giờ chạy đồng thời với một migration.
`last-result.txt` trong reports directory giữ các dòng của lần chạy cuối. Mọi exit
khác 0 đều tới failure notification của scheduler; notification chỉ nêu check id,
title và số lượng. **Xử lý report:** report có mode 0600 và có thể chứa giá trị
badge của Worker (check (j)). Giữ report như backup và không bao giờ đính kèm vào
ticket hay email.

Reconciliation mặc định chỉ đọc. Mismatch tạo incident, không tự động repair.

## 8. Xử lý sự cố

### Nghi duplicate, mất hoặc chưa rõ kết quả write

- dừng workflow bị ảnh hưởng nếu quantity integrity có rủi ro;
- giữ request time, station, user/worker, PN, flow và `device_event_id`;
- đối chiếu log backend theo `request_id`, `device_event_id`, PN, QuantityFlow,
  Scan Station và Worker (command ở §2) trước khi retry;
- kiểm tra server result/history trước khi retry;
- chỉ retry bằng idempotency key gốc khi phù hợp;
- không bao giờ sửa Movement history trực tiếp;
- chỉ dùng Undo/correction workflow chuẩn sau khi biết chính xác committed state.

### Finding guard-integrity (reconcile check (h))

- freeze write (`$PF stop backend`, §5) khi một trigger hoặc guard function đã đổi, một trigger bị tắt, hoặc `session_replication_role` được đặt toàn database hoặc đang active: khi đó các guard thông thường không kích hoạt cho bất kỳ session nào, kể cả `partflow_app`;
- với setting đó, owner chạy `ALTER DATABASE <db> RESET session_replication_role` trong `db`;
- so sánh trigger và function với migration source;
- owner quyết định cách sửa và có cần xác minh history với backup gần nhất hay không;
- chạy `reconcile` và privilege probe (`DEPLOYMENT.md` §3.1) trước khi mở lại write.

### Áp lực database hoặc storage

- block write mới trước khi hết disk (write freeze, §5);
- giữ log và metric;
- alert storage là một dòng `disk_*` của `check.sh` với dưới 15 % còn trống:
  `disk_data` đo database volume, `disk_backup` đo backup directory, `disk_docker`
  đo Docker root (image và container log) và `disk_archive` đo archive directory;
- không xóa tùy tiện PostgreSQL file, volume, Movement row hoặc backup;
- backup cần `2 x` dump mới nhất cộng reserve trống trong backup directory, một
  restore path 3 cần kích thước database cộng `2 x` dump cộng 1 GiB trống trên
  database volume (§6), và một backup lock stale (`backup_lock_stale`) chặn backup
  và release cho đến khi được xóa (§3);
- mở rộng storage hoặc theo verified archive/purge maintenance path Phase 16;
- chạy reconciliation trước khi mở lại write.

### Host hỏng

- ngăn split-brain: xác nhận instance hỏng không còn nhận write;
- provision recovery host đã duyệt;
- restore verified backup mới nhất và matching release bằng new-instance restore
  (§6);
- chạy reconciliation và smoke test;
- ghi data-loss window so với RPO đã duyệt;
- chỉ redirect client sau khi được phê duyệt: sự phê duyệt của owner được ghi
  trong `restore.json` trước khi `backend` start.

## 9. Lịch định kỳ

| Tần suất | Công việc |
| --- | --- |
| Liên tục | `check.sh` mỗi 15 phút (P16-S6; lên lịch trên pilot host ở P16-S7): alert health, restart, disk, certificate, backup age và error với ngưỡng OD-16-11: backup cũ hơn 26 h, disk còn trống dưới 15 %, certificate hết hạn trong 21 ngày, restart count tăng, bất kỳ record error nào của backend (archival proposal đến cùng P16-S10). Notification là failure notification của scheduler; một lỗi không đổi được nhắc lại mỗi 6 h |
| Hàng ngày | `backup.sh --kind daily --keep-daily 14 --keep-weekly 8` theo lịch (giá trị của owner); review notification của scheduler và của platform tool (exit 4 gồm cả daily không qua verify) cùng off-site replication; review critical error; `scheduled-reconcile.sh` lúc 04:00 (sau backup 02:00 của các platform guide); review `last-check.txt`, `<reports-dir>/last-result.txt` và các notification |
| Hàng tuần | Review capacity trend và database growth (`growth.tsv` và `docker stats --no-stream`), failed login/authorization event (`$PF logs --since 168h --no-log-prefix backend \| grep '"logger":"app.access"' \| grep -E '"status":(401\|403)'`: `refusal.type` và `refusal.code` phân biệt sign-in bị từ chối hoặc bị khóa, permission denied, `station_device_required` hoặc `station_device_mismatch`, và `csrf_rejected`; các dòng sign-in của `app.application.authentication` chỉ nêu user id), và security update pending (việc của host, P16-S7) |
| Hàng tháng | Xóa thủ công các reconcile report cũ sau khi review (không tool nào xóa chúng; chúng có thể chứa giá trị badge); patch staging rồi production; review user/role, firewall rule, secret và liên hệ trong runbook; review database role bằng `reconcile --check h`; xóa backup pre-release và manual đã hết observation window, và backup daily mà rotation báo invalid sau khi review (không bao giờ xóa archive) |
| Khi rotate password của role | Một write freeze ngắn: thay role file, `$PF stop backend`, `$PF --profile ops run --rm -T db-roles`, `$PF up -d --force-recreate --no-deps backend`, rồi check health. `up -d backend` thông thường không nhận password mới (container không được tạo lại), và chạy `db-roles` trong khi backend đang phục vụ làm các connection mới của nó thất bại. Password owner: `ALTER ROLE … PASSWORD` trong `db` trước, rồi thay `postgres_password` (không restart service: chỉ `migrate` và `db-roles` one-shot dùng nó) |
| Hàng quý hoặc sau thay đổi schema quan trọng | `restore-test.sh --backup <latest daily>` (§4), so timing của nó với RTO, bài tập RPO/RTO có đo thời gian và review reconciliation |
| Trước khi đổi PostgreSQL image hoặc glibc của host | `restore-test.sh --backup <latest> --db-image <candidate>` (§7) |
| Trước mỗi release | `release.sh` lấy pre-release backup đã verify (§5); migration review, rollback decision và smoke-test plan |

Tổ chức phải tự đặt RPO, RTO, retention và owner thật. Ví dụ trong runbook là quy
trình, không phải cam kết service level.
