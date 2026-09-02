# Runbook vận hành PartFlow

> **Bản gốc chuẩn:** [`OPERATIONS_RUNBOOK.md`](OPERATIONS_RUNBOOK.md),
> được tạo trong gói tài liệu này trên baseline upstream
> `194ffc2e5e8e22c389abecd0830292a6707955d9`.
>
> **Trạng thái:** Mẫu quy trình vận hành chuẩn cho Phase 16. Các command phụ
> thuộc production Compose tương lai phải được thay bằng tên cuối cùng do repo
> cung cấp trước khi dùng cho production.
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

## 2. Health và chẩn đoán

Kiểm tra tối thiểu:

```bash
docker compose ps
docker compose logs --since=15m backend frontend db
curl --fail --silent --show-error https://<partflow-host>/api/health
```

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
- quyết định có cần dừng write hay không;
- thông báo window và deadline quyết định rollback.

### Thực hiện

1. Ghi application và Alembic revision hiện tại.
2. Build/pull target image bất biến.
3. Stop hoặc block write nếu cần.
4. Chạy production repository job `alembic upgrade head` rõ ràng đúng một lần.
5. Lưu migration output và revision mới.
6. Start/recreate application service ở target release.
7. Check health nội bộ và qua HTTPS.
8. Chạy smoke test authorization, SPA route, `/api`, scan-focus/connectivity và
   designated write/read-back.
9. Chạy reconciliation.
10. Chỉ mở lại write khi mọi kiểm tra bắt buộc pass.

### Quan sát

Monitor error, latency, lock, restart, disk và phản hồi operator trong observation
window đã định. Giữ previous release cùng backup.

## 6. Cây quyết định rollback

1. **Không có schema migration:** redeploy previous immutable application
   release rồi chạy smoke test.
2. **Schema đã migrate và backward-compatible:** chỉ deploy release trước nếu
   compatibility đã được verify rõ trước migration.
3. **Schema đã migrate nhưng không backward-compatible hoặc chưa rõ:** stop
   write; restore pre-migration database vào instance sạch và deploy previous
   application release tương ứng.
4. **Đã có production write mới sau migration:** không blindly restore đè lên.
   Escalate; bảo toàn cả current database và pre-release backup, xác định forward
   fix hoặc audited data-recovery plan và giữ application ở write-blocked.

Không mặc định `alembic downgrade` an toàn. PartFlow chủ động bảo vệ append-only
history và downgrade có thể bị từ chối hoặc làm mất loại dữ liệu mới.

## 7. Reconciliation

Phase 16 phải cung cấp command read-only tối thiểu để verify:

- current-position projection replay được từ non-reversed Movement history;
- mỗi active/closed flow có conservation history hợp lệ;
- introduced quantity theo PN reconcile với active, stocked, scrapped và
  reversed outcome theo canonical rule;
- assigned quantity trên Machine reconcile với flow đang ở từng Machine;
- `released_quantity` của demand được derive từ evidence `RECEIVED`;
- `allocated_quantity` của demand và `completed_at` của Work Order reconcile với
  active allocation row;
- không retained Movement nào reference row đã purge;
- không append-only table nào bị mutate ngoài archive/purge path đã duyệt.

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

- block write mới trước khi hết disk;
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
