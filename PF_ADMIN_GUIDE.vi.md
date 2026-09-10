# PartFlow NAS Admin v2 — Hướng dẫn sử dụng

> Bản dịch từ [PF_ADMIN_GUIDE.md](PF_ADMIN_GUIDE.md); tiếng Anh là nguồn chuẩn của bộ công cụ.
> Phiên bản **2.0.0**, ngày **2026-09-08**.
> Repo đối chiếu: `CDSemi/part-flow@8d358eea0582b2e910df60569ad9865fd78f9d98`.
> Phạm vi: stack **staging nội bộ trên Synology**, chưa phải production.
> Bộ này không tự commit, push, xuất bản release hoặc tạo lịch DSM.

## 1. Nên cập nhật theo commit hay release?

**Dùng cả hai, nhưng cho hai mục đích khác nhau.** Khi đang phát triển và thử nghiệm,
chủ động update staging theo commit mới nhất. Khi chọn một bản để kiểm thử có tổ
chức hoặc triển khai, tạo release. Không cần release cho từng commit nhỏ.
Lịch tự động chỉ theo release đã xuất bản, không tự bám `main`.

Tại lần kiểm tra repo, `v0.1.0-alpha.1` là **pre-release**, trỏ tới
`d277f8e53a7ca79e0211c211a344dce60e8c7d7f`; nhánh `main` đã có commit mới hơn.
GitHub không đưa pre-release vào endpoint `releases/latest`. Vì vậy, kênh chỉ lấy
stable có thể báo chưa có release phù hợp; script không tự chuyển sang lấy `main`.

| Kênh | Cách chọn |
| --- | --- |
| `stable` | Release mới nhất do GitHub xác định, không phải draft hoặc pre-release |
| `prerelease` | Bản có thời điểm xuất bản mới nhất trong cả stable và pre-release, bỏ draft |

Script luôn phân giải branch/tag thành **SHA đầy đủ**, rồi checkout SHA đó.
Không lấy `target_commitish: main` trong thông tin release làm source cố định.
Tag đã từng được script ghi nhận mà đổi sang SHA khác sẽ bị từ chối. Không tái sử dụng tag.

## 2. Các file trong bộ này

| File | Vai trò |
| --- | --- |
| `pf.sh` | Lệnh vào chính, tìm Python, giữ khả năng chuyển tiếp lệnh Compose |
| `pf-admin.py` | Update, backup, rollback, reset và kiểm tra release |
| `backup.sh` | Lệnh backup thủ công hoặc chạy theo lịch |
| `release-check.sh` | Lệnh cho Task Scheduler; mặc định chỉ kiểm tra |
| `pf-config.example.json` | Cấu hình quản trị mẫu, không chứa mật khẩu |
| `compose.nas.yaml`, `nas.env.example` | Bản tham chiếu giữ nguyên từ gói staging trước |
| `pf-admin-tests/test_pf_admin.py`, `TEST_REPORT.md` | Kiểm thử offline và giới hạn kiểm chứng |

Phần điều phối dùng thư viện chuẩn Python để xử lý JSON, file backup và trạng thái
lỗi, thay vì parse JSON hoặc thực thi metadata bằng shell. Không cần cài gói pip.
Script quản trị và cấu hình NAS **không tự bị thay thế** khi cập nhật source từ GitHub.

## 3. Điều kiện và cách nâng cấp từ bộ cũ

Bộ này dành cho một stack staging **đã khởi tạo** theo hướng dẫn DSM trước đó.
Database phải chạy PostgreSQL 16 và đã migrate. Project phải có đúng một container
`db`, `backend`, `frontend`. Khi cài mới hoàn toàn, vẫn làm theo hướng dẫn triển khai
NAS trước; chỉ dùng các lệnh quản trị vòng đời sau khi stack đã sẵn sàng.

Trên NAS cần Git, Python **3.9 trở lên**, Docker và một Compose CLI hoạt động.
Ưu tiên Python còn được bảo trì, được hỗ trợ cho đúng model/DSM. Không thay thế
Python hệ thống của DSM. Python trên host chỉ chạy công cụ quản trị; Python/Node
của ứng dụng vẫn ở trong container. Không cần jq hoặc thư viện Python ngoài.

Kiểm tra bằng SSH:

```sh
python3 --version
git --version
sudo docker version
sudo docker compose version
# If Compose v2 is unavailable:
sudo docker-compose version
```

`pf.sh` thử các tên Python phổ biến và đường dẫn package Python 3.9 thường gặp của
Synology. Có thể chỉ định executable thực tế:

```sh
sudo env PF_PYTHON=/absolute/path/to/python3 sh ./pf.sh doctor
```

Thay đường dẫn mẫu bằng executable đã kiểm tra. Nếu cần, đặt cùng `PF_PYTHON` trong
script Task Scheduler. Không cài ép package không hỗ trợ NAS chỉ để bỏ qua lỗi.

### Nâng cấp mà không reset dữ liệu

**Giữ nguyên `.env`, Compose đang dùng, source, project name và Docker volume.**
Sao lưu script cũ trước:

```sh
cd /volume1/docker/partflow
saved="backups/admin-tools-$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$saved"
chmod 700 "$saved"
for file in pf.sh backup.sh compose.nas.yaml; do
    if [ -f "repo/$file" ]; then
        cp -p "repo/$file" "$saved/"
    fi
done
```

Upload/thay `pf.sh`, `pf-admin.py`, `backup.sh`, `release-check.sh` và
`pf-config.example.json` vào **cùng thư mục `repo/` với `.env` hiện có**. Có thể chép
hai hướng dẫn mới vào đó. Không ghi đè `compose.nas.yaml` nếu đã chỉnh riêng cho NAS;
file trong ZIP chỉ là bản tham chiếu cũ, không có thay đổi.
`pf-admin-tests/` là kiểm thử của công cụ quản trị, không phải test nghiệp vụ của app;
nên chạy trên workstation hoặc bản sao kiểm thử riêng.

```sh
cd /volume1/docker/partflow/repo
test -f pf-config.json || cp pf-config.example.json pf-config.json
chmod 600 .env pf-config.json
sudo sh ./pf.sh doctor
sudo sh ./pf.sh status
sudo sh ./pf.sh backup
```

Giữ `project` là `partflow-staging` khi nâng cấp từ bộ cũ. Đổi tên này có thể chọn
một deployment/volume khác. Bản source tải ZIP phải có `DEPLOYED_SOURCE.txt` chứa
SHA thực tế đủ 40 ký tự. Với Git checkout, giá trị này phải khớp Git HEAD.

Các thư mục trạng thái và backup được tạo **ngoài source có thể bị thay thế**:

```text
partflow/
  repo/                                    application checkout + local controls
  .pf-state-partflow-staging/               lock, journal, selected images
  backups/
    revisions/
      partflow-staging/
        <backup-id>/
          source.tar.gz
          database.dump
          database.list
          manifest.json
          manifest.sha256
```

## 4. Bảng lệnh

Chạy từ `repo/`, thống nhất cùng tài khoản/quyền quản trị.

| Lệnh | Tác dụng |
| --- | --- |
| `sudo sh ./pf.sh doctor` | Kiểm tra công cụ, Compose và dung lượng tối thiểu trên volume source |
| `sudo sh ./pf.sh status` | Source, container, revision database và thao tác dang dở |
| `sudo sh ./pf.sh update --latest` | Update thủ công theo commit mới nhất của nhánh cấu hình |
| `sudo sh ./pf.sh update --commit FULL_SHA` | Update thủ công tới commit cụ thể |
| `sudo sh ./pf.sh update --release TAG` | Update thủ công tới release đã xuất bản |
| `sudo sh ./pf.sh update --release latest --channel prerelease` | Chọn bản xuất bản mới nhất phù hợp cho staging |
| `sudo sh ./pf.sh backups --page 1` | Danh sách backup mới nhất trước, 10 bản/trang |
| `sudo sh ./pf.sh rollback` | Mở menu chọn backup có phân trang |
| `sudo sh ./pf.sh rollback BACKUP_ID` | Chọn trực tiếp backup; chỉ rollback code, giữ dữ liệu hiện tại |
| `sudo sh ./pf.sh rollback BACKUP_ID --restore-db` | Khôi phục code và database của backup đã chọn |
| `sudo sh ./pf.sh reset-db` | Chuyển instance sang database sạch, đã migrate |
| `sudo sh ./pf.sh backup` | Backup đầy đủ và thử restore, không dừng ứng dụng |
| `sudo sh ./pf.sh release-check` | Chỉ kiểm tra release, không cập nhật app/database |
| `sudo sh ./pf.sh release-check --apply` | Chỉ tự update khi cấu hình cho phép và vượt qua các chốt kiểm tra |
| `sudo sh ./pf.sh resume` | Chạy lại bản chưa thay đổi sau lỗi xảy ra sớm |

Thay `FULL_SHA`, `TAG`, `BACKUP_ID` bằng giá trị thật. `update` không có lựa chọn
sẽ dùng kênh release, không mặc định bám nhánh. Các lệnh Compose cũ như `ps`, `logs`,
`exec`, `build`, `up`, `stop` vẫn dùng được. Project và file Compose được giữ cố định.
Các tùy chọn xóa volume của `down`/`rm` bị chặn. Người có quyền Docker trực tiếp
vẫn có thể vượt qua công cụ này; đây không phải lớp phân quyền bảo mật.

## 5. Update thủ công

Đối với thay đổi nhỏ khi đang phát triển:

```sh
sudo sh ./pf.sh update --latest
```

Script chốt SHA, kiểm tra lần chạy `ci.yml` dạng push mới nhất cho đúng SHA, clone
checkout mới và build image có tag riêng mà chưa thay image đang chạy. Sau đó yêu
cầu nhập:

```text
UPDATE <12-character-target-SHA>
```

Sau xác nhận, script dừng frontend/backend, backup source/database cũ, thử restore
đầy đủ vào database tạm, chạy migration đã được cho phép nếu có, thay source ứng
dụng và chạy các image đã chọn. Backend và revision database được kiểm tra trước
khi mở frontend. Cuối cùng kiểm tra `/api/health` qua frontend.

`.env`, `compose.nas.yaml`, script quản trị, cấu hình và hướng dẫn cục bộ được giữ.
Các sửa source khác được backup, không tự merge vào checkout mới.
Việc thay source **không phải atomic directory swap**: nó được làm khi app đã dừng,
có journal để nhận diện lỗi dang dở. Không sửa/upload source hoặc chạy Docker trực
tiếp đồng thời với thao tác quản trị.

Một bản cài từ ZIP có thể được chuyển thành Git checkout do công cụ quản lý bằng
update thủ công, kể cả khi SHA được chọn vẫn như cũ. Auto-update yêu cầu checkout
này và source ứng dụng không có sửa đổi cục bộ chưa được quản lý.

### Khi có migration

Đọc thay đổi và phương án phục hồi trước, rồi cho phép rõ ràng:

```sh
sudo sh ./pf.sh update --latest --allow-migrations
# Or select the exact release:
sudo sh ./pf.sh update --release TAG --allow-migrations
```

Migration được chạy thử trên bản database đã restore riêng trước. Chỉ khi chạy
thử đạt mới migrate database đang sử dụng. Nếu một migration cũ bị sửa/xóa, script
vẫn từ chối dù có cờ này. Nhiều Alembic head hoặc schema hiện tại không nhất quán
cần được xử lý thủ công.

`--skip-ci` là ngoại lệ **thủ công dành cho staging**, không có nghĩa CI đã đạt.
Lịch tự động không dùng ngoại lệ này. Không lệnh nào tự chạy `alembic downgrade`.

## 6. Backup và rollback

Mỗi update/reset/rollback có một checkpoint trước khi thay source hoặc dữ liệu
đang dùng. Checkpoint gồm source thực tế, PostgreSQL custom dump, Alembic revision,
PostgreSQL major version, SHA source, checksum và tham chiếu/ID image được giữ cục bộ.
Script thực sự yêu cầu `pg_restore --exit-on-error` vào database tạm và đối chiếu
Alembic revision. Đây không chỉ là đọc danh sách archive, nhưng **chưa phải kiểm thử
ứng dụng hoặc reconciliation số lượng đầy đủ**.

Archive source bỏ `.git`, môi trường ảo, thư mục dependency và cache.
Nó **có chứa `.env` và file quản trị cục bộ**: bảo vệ cả checkpoint như dữ liệu bí mật.
Metadata là JSON, không được thực thi như shell. Checksum phát hiện hỏng dữ liệu;
không phải chữ ký chống người có thể sửa cả dữ liệu và checksum.

### Chọn bản cần rollback

```sh
sudo sh ./pf.sh rollback
```

Danh sách mới nhất trước, 10 bản/trang. Dùng `n`, `p`, `q` hoặc số được hiển thị.
Chọn trực tiếp sẽ bỏ qua menu, nhưng không bỏ bước xác nhận:

```sh
sudo sh ./pf.sh rollback BACKUP_ID
```

Có thể dùng SHA đầy đủ nếu chỉ có đúng một checkpoint khớp. Nếu nhiều backup cùng
SHA, phải chọn backup ID. Backup chưa hoàn chỉnh hoặc source chưa xác minh không
được dùng làm đích rollback code. Các file dump từ script cũ vẫn giữ nguyên, nhưng
không xuất hiện như backup revision vì chúng không có source đi kèm.

### Mặc định: chỉ rollback code

Dữ liệu hiện tại được giữ. Fingerprint các file migration hiện hành và revision
Alembic trong database phải khớp checkpoint. Checksum và image được kiểm tra trước.
Câu xác nhận là `ROLLBACK BACKUP_ID`.

Đây là chốt tương thích cấu trúc **thận trọng, không phải chứng minh tương thích
nghiệp vụ**. Cùng Alembic head chưa bảo đảm code cũ hiểu được mọi dữ liệu mà code mới
đã tạo. Cần đọc những thay đổi về ý nghĩa dữ liệu trước khi xác nhận.

### Khôi phục cả code và database: phải chọn rõ

```sh
sudo sh ./pf.sh rollback BACKUP_ID --restore-db
```

Câu xác nhận:

```text
RESTORE <current-database-name> <backup-id>
```

Script backup trạng thái hiện tại thêm một lần, restore dump được chọn vào database
mới và kiểm tra. Sau đó đổi tên database cũ và database đã chuẩn bị trong một
transaction trên database quản trị PostgreSQL. Bản đang dùng trước đó được giữ dưới
tên `pf_keep_<timestamp>_<suffix>`, không nhận kết nối mới. App chạy image tương ứng
với database đã khôi phục.

**Dữ liệu ghi sau thời điểm backup được chọn sẽ không còn trong app đang hoạt động.**
Nó vẫn ở checkpoint an toàn vừa tạo và database được giữ lại; không tự được merge
vào lịch sử đã restore. Không sửa trực tiếp Movement history để trộn dữ liệu.
Giữ bản cũ là bảo vệ dữ liệu, không phải cơ chế reconciliation tự động.

Nếu image cũ bị xóa/prune, rollback dừng thay vì tự build một image có thể khác từ
base tag đã thay đổi. Archive source không chứa image layers. Chỉ checkpoint này
**chưa đủ** cho disaster recovery ngoài NAS. Cần bảo toàn/export các image riêng
trước khi dựa vào khả năng khôi phục trên host khác. Bộ này không tự publish registry
hoặc export image.

## 7. Reset dữ liệu staging

```sh
sudo sh ./pf.sh reset-db
```

Phải gõ đúng tên database đang dùng, ví dụ:

```text
RESET partflow_staging
```

Không có `--yes` để bỏ qua. Reset và rollback yêu cầu terminal tương tác; không
đặt chúng trong Task Scheduler.

Reset **không xóa Docker volume** và không chạy DELETE/TRUNCATE tùy tiện. Nó dừng
app, tạo và thử restore backup, tạo database mới rỗng, chạy migrations từ image
hiện hành, rồi đổi tên database trong một transaction. Database trước đó vẫn được
giữ, khóa kết nối mới. Tên database trong cấu hình và URL ứng dụng không đổi.

Toàn bộ dữ liệu người dùng tạo, kể cả master data/cấu hình môi trường, biến mất khỏi
instance đang dùng. Các giá trị mặc định do migration tạo vẫn có thể tồn tại.
Cần cấu hình lại Departments, Areas, Operations, Machines và Scan Stations.
Nếu còn kết nối database từ IDE hoặc phiên khác, script từ chối đổi database chứ
không tự giết các phiên đó.

**Khi go-live, nên tạo môi trường/database production riêng và thiết lập master data
sạch.** Xóa dữ liệu thử không tạo ra authentication, server production, secret
handling, monitoring, backup retention hay phê duyệt production. Các lệnh thay đổi
vòng đời của bộ này chủ động từ chối `environment` khác `staging`.
Không đổi nhãn database production thật thành staging để vượt qua chốt này.

## 8. Kiểm tra hoặc cập nhật release định kỳ

Bắt đầu bằng chế độ chỉ kiểm tra. Trong DSM:

**Control Panel → Task Scheduler → Create → Scheduled Task → User-defined script**

Chọn tài khoản quản trị/root có quyền Docker. Anh tự đặt lịch trong DSM; bộ này
không tự tạo task.

Kiểm tra các pre-release dùng cho staging:

```sh
sh /volume1/docker/partflow/repo/release-check.sh --channel prerelease
```

Khi repo chỉ có `v0.1.0-alpha.1`, kênh mặc định `stable` chưa có bản phù hợp là bình
thường. Script không tự dùng `main` để bù vào.

Để cho phép tự update staging, sửa `pf-config.json`:

```json
{
  "repository": "CDSemi/part-flow",
  "branch": "main",
  "project": "partflow-staging",
  "environment": "staging",
  "release_channel": "prerelease",
  "auto_update": true,
  "ci_workflow": "ci.yml",
  "health_timeout_seconds": 180,
  "minimum_free_mb": 2048
}
```

Sau đó đặt lệnh sau **trong khung giờ bảo trì đã chấp thuận**, chẳng hạn một khung
ban đêm dành cho staging, không phải lúc đang nhập dữ liệu thử:

```sh
sh /volume1/docker/partflow/repo/release-check.sh --apply
```

Cần cả cờ `--apply` và cấu hình bật tự động. Auto-update đòi hỏi release đã xuất bản,
CI đạt cho đúng SHA, Git checkout sạch do công cụ quản lý, commit mới nằm tiếp theo
trong lịch sử hiện hành — không tự downgrade/nhảy nhánh — cùng migration files và
schema head, đồng thời không thay đổi các file triển khai/cấu hình trong
`AUTO_REVIEW_PATHS` của controller. Image đích phải build và vượt qua health check.
Release cần migration sẽ dừng ở yêu cầu update thủ công.

Script khóa để các thao tác quản trị không chạy chồng. Thao tác bị gián đoạn để lại
journal chặn những lần auto-update sau. Script không tự restore database cũ khi
health check thất bại vì có thể làm biến mất dữ liệu mới trên app. Những chốt này
không hứa hẹn zero downtime hoặc bảo đảm hoàn toàn tính đúng đắn nghiệp vụ.

| Exit code | Ý nghĩa |
| --- | --- |
| `0` | Hoàn tất, kết quả chỉ kiểm tra, không có SHA mới hoặc chưa có release phù hợp |
| `1` | Lỗi, thao tác bị từ chối, tự động chưa bật hoặc có thao tác dang dở |
| `2` | Lỗi tham số hoặc điều kiện runtime |
| `20` | Hoãn update: CI chưa đạt, thay migration/cấu hình, lịch sử source không phù hợp... |

Bật thông báo kết quả/lỗi task trong DSM và đọc stdout/stderr. Bản cần xem xét thủ
công trả mã khác 0 để không bị hiểu nhầm là đã triển khai. Repo public hiện tại
không cần token cho các lần đọc thông thường. `GITHUB_TOKEN` tùy chọn được đọc từ
process environment để xử lý giới hạn API; không đưa token vào URL, file được commit
hoặc log. Token này không tự cấu hình xác thực Git clone nếu repo sau này thành private.

## 9. Backup định kỳ

`backup.sh` mới tạo checkpoint revision, không còn dùng cấu trúc bốn file dump cũ:

```sh
sh /volume1/docker/partflow/repo/backup.sh
```

Đặt lịch riêng, tránh trùng update. Backup độc lập không dừng app; PostgreSQL cung
cấp snapshot nhất quán cho logical dump. Bước thử restore tạo rồi xóa database tạm.
Cần đủ chỗ cho dump, source archive, database thử restore, image mới, và database
được giữ lại sau reset/restore.

Chép cả thư mục checkpoint sang nơi được mã hóa **ngoài NAS**. Tự cấu hình retention
và cảnh báo riêng. Bộ này không âm thầm xóa checkpoint cũ, image được giữ, database
`pf_keep_*` hoặc database thử thất bại. Chúng dùng dung lượng cho đến khi quản trị
viên xác minh và dọn theo phương án phục hồi.
`minimum_free_mb` chỉ là ngưỡng sàn trên filesystem source/backup, không chứng minh
đủ dung lượng cho Docker database volume có thể nằm ở nơi khác.

## 10. Xử lý khi thao tác thất bại

```sh
sudo sh ./pf.sh status
sudo sh ./pf.sh logs --tail=150 backend frontend db
```

Đọc phase và checkpoint ID. Nếu chưa thay source/database đang dùng — phase `paused`
hoặc `backup-ready` — sửa lỗi rồi chạy `resume`. Lệnh này kiểm tra lại schema/image
và yêu cầu `RESUME <database-name>`.

Nếu update chỉ đổi app và không có migration, có thể rollback code sau lỗi khi chốt
schema vẫn đạt. Nếu đã migrate/reset/restore hoặc không chắc dữ liệu/source đã đổi
thế nào, dùng checkpoint trước đó với `rollback BACKUP_ID --restore-db` sau khi xác
định ranh giới mất dữ liệu trên app. Trong phục hồi, script có thể tạo một checkpoint
khẩn cấp chỉ bảo toàn dữ liệu; checkpoint đó không được coi là source rollback đã xác minh.

Các container chạy tác vụ một lần có label quản trị. Khi bắt được lỗi, controller
cố dừng các tác vụ của nó cùng frontend/backend. Mất điện hoặc hard kill không thể
chạy cleanup: sau khi NAS khởi động lại phải xem journal, container và database
trước khi cho người dùng truy cập. Không xóa `pending.json` hay dùng DSM UI để bật
frontend chỉ nhằm bỏ qua cảnh báo. Thao tác Docker/DSM trực tiếp nằm ngoài lock/chốt
của script.

Không chạy `docker system prune -a`, xóa volume hoặc prune image đang dùng làm
phương án rollback. Disaster recovery toàn host và reconciliation nghiệp vụ tự động
chưa thuộc phạm vi công cụ staging này.

## 11. Giới hạn kiểm chứng và lần thử đầu trên NAS

Xem `TEST_REPORT.md`. Bộ đã được kiểm tra bằng thao tác filesystem/archive thực,
Git clone/checkout cục bộ thực, kiểm tra cú pháp shell và các workflow mô phỏng offline.
Docker, SQL thực trên PostgreSQL 16 và Synology **chưa được chạy** trong môi trường
kiểm thử này. Code sandbox không phân giải được github.com nên chưa chạy clone online
thật ở đây; việc đọc repo được thực hiện qua GitHub connector. NAS cần DNS và HTTPS
riêng hoạt động để clone/update.

Trước khi bật `--apply`, dùng dữ liệu staging bỏ được để thử: backup, một vòng update/
rollback code, reset rồi restore, nhập sai câu xác nhận và tình huống dịch vụ lỗi.
Đối chiếu số lượng/lịch sử trên UI và kiểm tra backup ngoài NAS. Không chạy bộ integration
test của ứng dụng trên database đang cần giữ dữ liệu.

## 12. Nguồn đối chiếu

- [Deployment và Release policy của PartFlow tại commit đã kiểm tra](https://github.com/CDSemi/part-flow/blob/8d358eea0582b2e910df60569ad9865fd78f9d98/docs/DEPLOYMENT.md)
- [CI workflow của PartFlow](https://github.com/CDSemi/part-flow/blob/8d358eea0582b2e910df60569ad9865fd78f9d98/.github/workflows/ci.yml)
- [GitHub REST releases](https://docs.github.com/en/rest/releases/releases)
- [GitHub REST workflow runs](https://docs.github.com/en/rest/actions/workflow-runs)
- [PostgreSQL 16 ALTER DATABASE](https://www.postgresql.org/docs/16/sql-alterdatabase.html)
- [Mã nguồn đổi tên database của PostgreSQL 16](https://github.com/postgres/postgres/blob/REL_16_STABLE/src/backend/commands/dbcommands.c)
