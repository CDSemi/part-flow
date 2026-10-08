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
> backup, restore và rollback path 3 (P16-S5) và các command monitoring (P16-S6)
> vẫn là placeholder và phải được thay bằng tên cuối cùng do repo cung cấp trước
> khi dùng cho production.
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
| Migration output |  |
| Kết quả smoke/reconciliation |  |
| Rollback deadline và observation owner |  |
| Giới hạn đã biết |  |

`deploy/production/release.sh` ghi record này thành `record.json` (cùng output của
mọi bước) trong `<records-dir>/<UTC>-<tag>/`, và các trường ánh xạ vào các dòng
trên: `environment` và `url` (dòng 1), `host` (dòng 2), `release` (tag, commit,
tag trước và image ID cục bộ của `backend` và `web`; dòng 3), `alembic`
(`before`, `after`, `expected`; dòng 4), `operator`, `approver` (dòng 5),
`started_at`, `finished_at` (dòng 6), `backup` (dòng 7; `verified` là `false` cho
đến P16-S5), `migration` (các file `migrate.json` và `migrate.log`; dòng 8),
`reconcile` và `smoke` (dòng 9), `rollback_deadline` và `observation_owner`
(dòng 10), `known_limitations` (dòng 11); `outcome`, `writes_reopened_at` và
`refrozen` nêu cách lần chạy kết thúc. Một thao tác thủ công (ví dụ rollback) ghi
bổ sung vào cùng thư mục với cùng các trường.

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

`/api/health` là readiness: nó báo `release`, `commit`, `schema` (`current`,
`accepted`, `mismatch` hoặc `unknown`), `expected_revision`, `database_revision` và
`accepted_revision`, và trả 503 khi schema không khớp hoặc không kết nối được
database. `/api/health/live` chỉ là liveness (container health check dùng nó): nó
vẫn khỏe khi readiness là 503 trong lúc schema mismatch. `revision` in cùng các
thông tin từ một container one-off và chạy được khi `backend` đang dừng.

Trong production stack, access log của `web` là request log (client address,
method, path không có query string, status, bytes, duration, user agent); nó
không bao giờ chứa query string, cookie hay header PartFlow. Một câu trả lời JSON
502 hoặc 504 đến từ `web` (`server_unavailable`) và nghĩa là kết quả của write
chưa rõ: xử lý bằng `device_event_id` gốc (bên dưới).

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

Tạo custom-format dump:

```bash
mkdir -p backups/database manifests
backup_file="backups/database/partflow-$(date -u +%Y%m%dT%H%M%SZ).dump"
docker compose exec -T db sh -c \
  'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom --no-owner --no-privileges' \
  > "$backup_file"
test -s "$backup_file"
pg_restore --list "$backup_file" > "$backup_file.list"
sha256sum "$backup_file" "$backup_file.list"
```

Nếu host không có `pg_restore`, chạy list check trong PostgreSQL client container
cùng version. Lưu cùng dump:

- UTC timestamp;
- environment;
- Git commit/image digest;
- Alembic current revision;
- PostgreSQL major version;
- checksum dump/list;
- operator và lý do backup.

Mã hóa và copy bundle ra ngoài host. Alert khi scheduled backup bị thiếu, rỗng,
quá cũ hoặc replicate off-site thất bại.

## 4. Restore test — không overwrite ngay

Restore vào database hoặc stack cô lập, tuyệt đối không restore thẳng lên
production database duy nhất:

1. verify checksum của dump và manifest;
2. provision cùng PostgreSQL major version hoặc target đã xác nhận tương thích;
3. tạo restore-test database trống;
4. restore bằng `pg_restore --exit-on-error --no-owner --no-privileges`;
5. start application release tương ứng trỏ vào database đó;
6. verify Alembic revision;
7. chạy health, representative read model, quantity/Movement/allocation
   reconciliation và smoke test được chỉ định;
8. ghi thời gian restore và kết quả;
9. chỉ xóa isolated restore copy sau khi đã giữ lại bằng chứng.

Ví dụ trong Compose project cô lập:

```bash
docker compose exec -T db sh -c \
  'createdb -U "$POSTGRES_USER" partflow_restore_test'
docker compose exec -T db sh -c \
  'pg_restore -U "$POSTGRES_USER" -d partflow_restore_test --exit-on-error --no-owner --no-privileges' \
  < <verified-dump-file>
```

Dùng tên restore-test rõ ràng. Không thay production database name vào command
diễn tập.

## 5. Release và migration

### Trước maintenance

- duyệt chính xác release revision và scope;
- xác nhận kết quả CI/quality của revision đó;
- review mọi migration cùng hành vi downgrade/recovery;
- ước lượng lock/time/disk impact bằng staging data;
- verify off-site backup và tạo pre-release dump mới;
- xác nhận previous release còn dùng được;
- chạy reconciliation (§7) trên release hiện tại, giữ report, và mở incident cho
  mọi finding; trong production stack chạy nó, và mọi CLI recovery, với tag
  **hiện tại**, trước khi tag candidate được ghi vào `.env.production` (candidate
  chỉ nằm trong shell cho đến lúc switch);
- quyết định có cần dừng write hay không (migration đang chờ luôn cần write
  freeze bên dưới);
- thông báo window và deadline quyết định rollback, và nhắc các station hoàn tất
  dialog đang mở;
- lấy pre-release dump càng muộn càng tốt và ghi lại thời điểm: cho đến P16-S5 nó
  được lấy trước freeze, nên write xảy ra giữa dump và freeze **không** nằm trong
  đó; đặt tên nó bằng `--pre-release-backup`.

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
deploy/production/release.sh --release <new-tag> --operator "<name>" --approver "<name>" \
    --pre-release-backup "<dump reference>"
```

(`--no-backup-reason "<text>"` thay cho dump reference khi không có, ví dụ
rehearsal; `deploy/production/release.sh --help` in mọi option.) Nó chạy, ghi lại
từng bước: preflight (tool, environment file, dạng tag, build input sạch); revision
hiện tại và reconcile pre-release với release đang chạy; build candidate (tag đã có
không bao giờ build lại); check (j) và revision của candidate; write freeze khi có
migration đang chờ; `migrate`; reconcile post-release; chuyển `backend` trong khi
`web` vẫn phục vụ bundle trước (write vẫn bị từ chối, mọi page đã tải gửi release
trước và nhận 409), chờ health của release mới và schema `current`; chuyển `web`,
việc này mở lại write; và `smoke.sh`. Một check thất bại sau switch sẽ dừng
`backend` lại.

| Exit | Ý nghĩa | Làm gì |
| --- | --- | --- |
| 0 | hoàn tất | quan sát (bên dưới) |
| 1 | dừng khi chưa đổi gì, hoặc write đã mở lại trên release hiện tại | đọc lý do được in và `record.json`; sửa; chạy lại |
| 2 | không chạy được (cú pháp, tool, environment) | chưa đổi gì; sửa và chạy lại |
| 3 | `backend` bị để dừng | làm theo §6; đọc `regression.txt` hoặc so sánh `pre-reconcile.json` và `post-reconcile.json` |
| 4 | release mới có thể đang chạy và ghi được sau một check thất bại, và re-freeze cũng thất bại | tự chạy `$PF stop backend`, rồi làm theo §6 |

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
3. Freeze khi có migration đang chờ: `$PF stop backend`, rồi xác nhận
   `$PF ps --status running -q backend` không in gì.
4. `PARTFLOW_RELEASE=<new> $PF --profile ops run --rm -T migrate (--pre-release-backup REF | --no-backup-reason TEXT)`;
   lưu JSON output của nó và revision mới.
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
window đã định. Giữ previous release cùng backup. Các page đang mở trong lúc switch
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
   write; restore pre-migration database vào instance sạch và deploy previous
   application release tương ứng. Thủ tục restore là placeholder của P16-S5; việc
   restore pre-release dump không bao giờ bỏ các write sau nó nếu chưa qua
   escalation của path 4.
4. **Đã có production write mới sau migration:** không blindly restore đè lên.
   Escalate; bảo toàn cả current database và pre-release backup, xác định forward
   fix hoặc audited data-recovery plan và giữ application ở write-blocked.

CLI recovery chạy với release khớp database.

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
| (h) | không append-only table nào bị mutate ngoài archive/purge path đã duyệt; |
| (i) | Hot list entry là demand đang active (query của `DEPLOYMENT.md` §5); |
| (j) | canonical identity dưới interpreter và database đang chạy: canonical PN, Worker badge không phân biệt hoa/thường, rule prefix Asset Tag, mọi canonical-form CHECK được đánh giá lại dưới collation và ctype hiện tại của database, collation version và duplicate probe không phụ thuộc index cho các identity key (platform-upgrade identity check). |

(g) và (h) báo `not_applicable` cho đến khi có Movement-history archival và
database-role hardening; chúng trung lập với exit code.

Quy tắc vận hành:

- Một snapshot chỉ đọc; command chỉ lấy table lock `ACCESS SHARE`, trước
  snapshot, và không lấy row lock hay advisory lock. Nó fail sau 5 s chờ table
  lock (sớm hơn khi `--statement-timeout` ngắn hơn), hoặc ngay lập tức khi yêu
  cầu lock của nó bị deadlock với một session khác. Không bao giờ chạy migration
  đồng thời, và chạy ngoài giờ cao điểm.
- Rehearsal platform-upgrade cho (j): với nâng cấp Python/UCD, chạy `--check j`
  từ candidate backend image trên database hiện tại; với thay đổi glibc hoặc
  PostgreSQL image, chạy `--check j` trên bản restore vào candidate server
  (restore drill) hoặc ngay sau in-place upgrade trước khi mở lại write. Nửa
  glibc/PostgreSQL-image **pending cho đến khi có restore drill**. Owner quyết
  định re-canonicalization trước mọi nâng cấp.
- Với Work Order báo `not_completed_but_fully_allocated`, `expected` là giá trị
  replay, không phải đề xuất repair; owner chọn done date trong incident.
- Exit khác 0 là incident (§8). Không bao giờ sửa trực tiếp history hoặc
  projection; owner quyết định từng repair.

Reconciliation mặc định chỉ đọc. Mismatch tạo incident, không tự động repair.

## 8. Xử lý sự cố

### Nghi duplicate, mất hoặc chưa rõ kết quả write

- dừng workflow bị ảnh hưởng nếu quantity integrity có rủi ro;
- giữ request time, station, user/worker, PN, flow và `device_event_id`;
- kiểm tra server result/history trước khi retry;
- chỉ retry bằng idempotency key gốc khi phù hợp;
- không bao giờ sửa Movement history trực tiếp;
- chỉ dùng Undo/correction workflow chuẩn sau khi biết chính xác committed state.

### Áp lực database hoặc storage

- block write mới trước khi hết disk (write freeze, §5);
- giữ log và metric;
- không xóa tùy tiện PostgreSQL file, volume, Movement row hoặc backup;
- mở rộng storage hoặc theo verified archive/purge maintenance path Phase 16;
- chạy reconciliation trước khi mở lại write.

### Host hỏng

- ngăn split-brain: xác nhận instance hỏng không còn nhận write;
- provision recovery host đã duyệt;
- restore verified backup mới nhất và matching release;
- chạy reconciliation và smoke test;
- ghi data-loss window so với RPO đã duyệt;
- chỉ redirect client sau khi được phê duyệt.

## 9. Lịch định kỳ

| Tần suất | Công việc |
| --- | --- |
| Liên tục | Alert health, restart, disk, certificate, backup age và error |
| Hàng ngày | Review backup success, off-site replication và critical error |
| Hàng tuần | Review capacity trend, database growth, failed login/authorization event và security update pending |
| Hàng tháng | Patch staging rồi production; review user/role, firewall rule, secret và liên hệ trong runbook |
| Hàng quý hoặc sau thay đổi schema quan trọng | Full isolated restore drill, bài tập RPO/RTO có đo thời gian và review reconciliation |
| Trước mỗi release | Fresh verified backup, migration review, rollback decision và smoke-test plan |

Tổ chức phải tự đặt RPO, RTO, retention và owner thật. Ví dụ trong runbook là quy
trình, không phải cam kết service level.
