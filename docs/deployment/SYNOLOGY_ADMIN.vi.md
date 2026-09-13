# PartFlow NAS Admin v2.5

> **Bản tiếng Anh là source of truth.** [English source](./SYNOLOGY_ADMIN.md).
>
> Version: **2.5.0**
> Prepared: **2026-09-11**
> Phạm vi: quản trị **Synology staging** trong LAN giới hạn. Đây chưa phải gói production hardening.

## 1. Mục đích

PartFlow NAS Admin tách repository application có thể sửa qua SMB ra khỏi lifecycle
controller chạy với quyền cao. Nhờ vậy các DSM user được tin cậy có thể sửa repository
mà không đồng thời được quyền sửa code thực thi bởi `sudo pf ...`.

Layout vận hành:

```text
/volume1/docker/partflow/
├── repo/                              # application working tree; users đọc/ghi/xóa
├── control/                           # lifecycle control plane đã cài; root mới được sửa
│   ├── pf.sh
│   ├── pf-admin.py
│   ├── compose.nas.yaml
│   ├── backup.sh
│   ├── release-check.sh
│   ├── pf-config.example.json
│   └── nas.env.example
├── config/                            # host/runtime config; trusted users có thể sửa
│   ├── .env
│   └── pf-config.json
├── backups/                           # revision checkpoint; users chỉ đọc/copy
├── recovery/                          # purge/control-upgrade recovery; users chỉ đọc/copy
└── .pf-state-<project>/               # lock/journal/image state; chỉ root
```

Trong repository vẫn giữ source/reference của `pf.sh`, `compose.nas.yaml` và
`deploy/synology/*` để version-control và review. Sau khi cài control plane, các bản trong
repo **không phải** bản được chạy cho thao tác quản trị NAS thông thường.

## 2. Quyền truy cập

Mặc định dùng DSM group `users` cho quyền ghi repository/config và quyền đọc backup.
Có thể đổi group trong `pf-config.json` nếu sau này muốn dùng một trusted group riêng.

| Path | Mode điển hình | Quyền |
| --- | --- | --- |
| directory trong `repo/` | `2770` | owner + `workspace_write_group` đọc/ghi/xóa; setgid giữ group |
| file thường trong `repo/` | `0660` | owner + `workspace_write_group` đọc/ghi |
| file executable sẵn có trong `repo/` | `0770` | chỉ là source/working-tree executable, không phải privileged control plane |
| directory `control/` | `0750` | root sửa; `users` được browse/read |
| `control/pf.sh`, `backup.sh`, `release-check.sh` | `0740` | root thực thi/sửa; `users` chỉ đọc |
| file khác trong `control/` | `0640` | root sửa; `users` chỉ đọc |
| directory `config/` | `2770` | trusted `users` có thể tạo/sửa/xóa config |
| `config/.env`, `config/pf-config.json` | `0660` | trusted `users` đọc/ghi |
| directory `backups/`, `recovery/` | `0750` | group được cấu hình chỉ browse/copy, không sửa/xóa |
| file backup/recovery | `0640` | group được cấu hình đọc/copy, không ghi |
| `.pf-state-*` | `0700` | chỉ root |

DSM Shared Folder ACL vẫn có hiệu lực. Muốn sửa `repo/` qua SMB thì DSM account/group đó
cũng phải có **Read/Write** trên shared folder chứa PartFlow. POSIX permission không thể
vượt qua một DSM ACL đang deny.

Có thể chuẩn hóa lại permission bất cứ lúc nào:

```sh
sudo pf permissions
```

### Hệ quả của trust policy

Việc cho `users` ghi `repo/` và `config/` là chủ ý theo mô hình sử dụng này. Trusted user
có thể thay đổi source application và runtime setting, kể cả PostgreSQL password trong
`config/.env`. Backup/recovery cũng có dữ liệu application; purge recovery còn giữ bản sao
`.env`. Chỉ cấp SMB access cho những người được phép xem và sửa các thông tin này.

## 3. Kéo `.env` ra ngoài repo có ảnh hưởng application không?

**Không**, miễn là PartFlow chạy qua installed controller.

Application không quan tâm file `.env` nằm ở directory nào trên NAS. Controller truyền
file bên ngoài cho Docker Compose một cách explicit:

```text
--env-file /volume1/docker/partflow/config/.env
```

Đồng thời controller truyền đúng repository dùng làm build context:

```text
PARTFLOW_REPO_ROOT=/volume1/docker/partflow/repo
```

Installed `control/compose.nas.yaml` sử dụng path đó:

```yaml
backend:
  build:
    context: "${PARTFLOW_REPO_ROOT}/backend"

frontend:
  build:
    context: "${PARTFLOW_REPO_ROOT}/frontend"
```

Các environment value bên trong container vẫn giống trước đây. Chỉ thay đổi **vị trí lưu
file trên host**.

Cách tách này còn tránh add nhầm `.env` vào Git và cho phép thay sạch toàn bộ `repo/` khi
update mà không đụng runtime credential.

Quy tắc vận hành từ v2.5 là:

```sh
sudo pf ...
```

Không giả định `docker compose` chạy trực tiếp trong `repo/` sẽ tự tìm được external `.env`
hay installed Compose file. Xem §14 nếu thật sự cần raw Compose.

## 4. Source files và installed control files

Repository giữ các source được version-control:

```text
repo/
├── pf.sh
├── compose.nas.yaml
└── deploy/synology/
    ├── install-control.sh
    ├── pf-admin.py
    ├── backup.sh
    ├── release-check.sh
    ├── pf-config.example.json
    ├── nas.env.example
    ├── TEST_REPORT.md
    └── tests/
```

`install-control.sh` copy các lifecycle source đã review sang `control/`, đổi chúng thành
root-owned và chỉ-read đối với `users`, rồi cài launcher rất nhỏ:

```text
/usr/local/bin/pf
```

Sau khi cài, `pf.sh` trong repository cố ý từ chối chạy operational command. Dùng:

```sh
sudo pf status
```

không dùng:

```sh
sudo sh ./pf.sh status
```

Application update **không** âm thầm update privileged control plane. Khi revision mới có
thay đổi `pf-admin.py`, `compose.nas.yaml` hay lifecycle source khác, hãy review rồi cài lại
control plane một cách explicit.

## 5. Cài mới control plane hoặc migrate từ Admin v2.4.x

Từ repository root:

```sh
cd /volume1/docker/partflow/repo
```

Vì `repo/` cho users quyền ghi, trước khi chạy installer bằng root cần bảo đảm source revision
là bản bạn tin cậy. Tối thiểu hãy xem Git status và các thay đổi trong deployment/control.

Chạy:

```sh
sudo sh ./deploy/synology/install-control.sh
```

Installer hiển thị target path và yêu cầu nhập chính xác:

```text
INSTALL CONTROL
```

Installer migrate an toàn như sau:

1. Tạo `/volume1/docker/partflow/config/`.
2. Chuyển `repo/.env` cũ sang `config/.env` nếu có.
3. Chuyển/copy `deploy/synology/pf-config.json` cũ sang `config/pf-config.json`.
4. Nếu bản cũ và bản mới của `.env` hoặc `pf-config.json` cùng tồn tại nhưng khác nhau, installer dừng để bạn reconcile; không âm thầm chọn một bản.
5. Nếu đã có `control/`, archive nó vào `recovery/control-upgrades/` trước khi thay.
6. Cài bản `control/` root-owned mới.
7. Cài `/usr/local/bin/pf` nếu path đó chưa bị phần mềm khác sử dụng.
8. Chạy `pf permissions` để chuẩn hóa quyền repo/config/backup/recovery.

Installer không xóa container, volume, database, revision backup hay application source.
Đây không phải redeploy và cũng không reset database.

Kiểm tra sau khi cài:

```sh
sudo pf doctor
sudo pf status
```

Nếu `/usr/local/bin/pf` không thể cài vì đã có file không thuộc PartFlow, dùng trực tiếp:

```sh
sudo /volume1/docker/partflow/control/pf.sh status
```

## 6. Configuration files

### `config/pf-config.json`

Đây là NAS-local administration config thực sự được dùng. Nếu chưa có, controller tạo từ
root-owned `control/pf-config.example.json`.

Default:

```json
{
  "repository": "CDSemi/part-flow",
  "branch": "main",
  "project": "partflow-staging",
  "environment": "staging",
  "release_channel": "stable",
  "auto_update": false,
  "ci_workflow": "ci.yml",
  "health_timeout_seconds": 180,
  "minimum_free_mb": 2048,
  "backup_read_group": "users",
  "workspace_write_group": "users"
}
```

Trusted users có thể sửa file này qua SMB. Controller validate key được hỗ trợ, project name,
boolean, numeric value và DSM group trước khi sử dụng.

### `config/.env`

Khi brand-new deploy, `deploy` tạo file này bằng wizard từ installed template
`control/nas.env.example`. Script tự sinh `POSTGRES_PASSWORD` 64 ký tự hex bằng secure
randomness và không in password ra terminal.

Ví dụ:

```dotenv
POSTGRES_USER=partflow_staging
POSTGRES_PASSWORD=<generated secret>
POSTGRES_DB=partflow_staging
SITE_TIMEZONE=America/Los_Angeles
PARTFLOW_BIND_IP=192.168.0.11
PARTFLOW_HTTP_PORT=5173
PARTFLOW_ALLOWED_HOST=localhost
```

Sau khi PostgreSQL đã initialize, sửa `POSTGRES_USER`, `POSTGRES_PASSWORD` hay `POSTGRES_DB`
trong file **không đồng nghĩa** credential/database thật bên trong PostgreSQL cũng tự đổi.
Đừng tùy tiện sửa các field này trên live instance; cần managed deployment/recovery hoặc
một credential/database migration có kế hoạch.

## 7. New deployment

Brand-new staging thường dùng:

```sh
sudo pf deploy --latest
```

Các source selector khác:

```sh
sudo pf deploy                         # current clean Git checkout
sudo pf deploy --commit FULL_SHA
sudo pf deploy --release TAG
sudo pf deploy --release latest --channel prerelease
```

Flow new deploy:

1. Xác nhận Compose project chưa có managed deployment record/container/volume.
2. Tạo hoặc reuse `config/.env`.
3. Chỉ hỏi những field deployment-specific không thể suy luận an toàn.
4. Resolve source thành exact commit SHA.
5. Verify CI, trừ khi explicit dùng `--skip-ci` cho manual staging.
6. Build backend/frontend candidate trước khi tạo database.
7. Yêu cầu `DEPLOY <SHA12>`.
8. Start PostgreSQL và xác nhận DB còn mới/uninitialized.
9. Chạy `alembic upgrade head`.
10. Start backend, check health/schema.
11. Start frontend, check `/api/health` qua frontend proxy.
12. Ghi deployed revision vào external `.pf-state-<project>/deployed.json`.

Sau khi smoke test UI/workflow/firewall:

```sh
sudo pf backup
```

Nếu first deploy fail trước khi frontend có thể mở cho client:

```sh
sudo pf abort-deploy
```

Command có confirmation, chỉ xóa resource của incomplete first deployment và giữ repo cùng
`config/.env` để có thể retry.

## 8. Repository được sửa tự do và deployed revision

Từ v2.5, `repo/` là working tree chứ không phải bằng chứng duy nhất về code đang chạy.
Running application sử dụng image đã build/pin từ trước; sửa source qua SMB **không** làm
application đang chạy đổi ngay.

Kiểm tra:

```sh
sudo pf status
```

Nó hiển thị:

```text
Deployed source: <SHA>
Workspace HEAD: <SHA hoặc non-git>
Workspace differs from deployed: True/False
Workspace changes: ...
```

Manual update khi thấy workspace dirty/khác deployed revision sẽ đưa working tree hiện tại
vào pre-update checkpoint dưới dạng `workspace.tar.gz`, rồi mới thay `repo/` bằng revision
được chọn. Unattended release update sẽ **refuse** workspace đang drift thay vì tự xóa local work.

`deploy --current` cũng chỉ nhận clean Git checkout để deployed identity luôn là exact commit.

## 9. Manual update

Staging thường dùng:

```sh
sudo pf update --latest
```

Hoặc:

```sh
sudo pf update --commit FULL_SHA
sudo pf update --release TAG
sudo pf update --release latest --channel prerelease
```

Update resolve exact SHA, check CI, clone candidate riêng, build image riêng, kiểm migration
contract, yêu cầu confirmation, stop application write, tạo verified pre-update checkpoint,
rehearse migration được cho phép, thay writable repository rồi activate image mới.

Nếu migration thay đổi:

```sh
sudo pf update --latest --allow-migrations
```

Historical migration đã tồn tại mà bị sửa/xóa vẫn bị refuse. Tool không tự chạy
`alembic downgrade`.

`--skip-ci` chỉ là manual staging exception, không được coi là CI pass.

## 10. Backup và rollback

Tạo verified revision checkpoint:

```sh
sudo pf backup
```

Checkpoint v2.5 gồm:

```text
source.tar.gz          exact deployed source revision
workspace.tar.gz       chỉ có khi writable repo khác deployed source
database.dump          active PostgreSQL database
database.list
manifest.json
manifest.sha256
```

Active DB dump được restore thử vào temporary DB để verify. Normal revision `source.tar.gz`
không còn chứa runtime `.env`; file đó nằm ngoài repo. Full purge-recovery bundle sẽ lưu
`.env` riêng.

Danh sách backup, mới nhất trước, 10 bản/trang:

```sh
sudo pf backups --page 1
```

Interactive rollback:

```sh
sudo pf rollback
```

Code-only rollback, giữ data hiện tại và yêu cầu schema compatibility:

```sh
sudo pf rollback BACKUP_ID
```

Rollback app + database:

```sh
sudo pf rollback BACKUP_ID --restore-db
```

Database form có confirmation mạnh hơn, restore dump cũ vào DB mới rồi giữ active DB trước đó
với tên `pf_keep_*`, không âm thầm xóa newer writes.

## 11. Reset staging data

Muốn giữ application/version hiện tại nhưng dùng database sạch:

```sh
sudo pf reset-db
```

Confirmation dùng tên DB thật:

```text
RESET partflow_staging
```

`reset-db` tạo và verify checkpoint trước, tạo DB mới tới current Alembic head, switch DB
transactionally và giữ lại DB cũ. Nó không xóa PostgreSQL Docker volume.

Dùng `reset-db` khi chỉ muốn clear test data. Dùng `purge` khi muốn đưa instance thật sự
về trạng thái có thể new deploy lại từ đầu.

## 12. Full purge, recovery và clean redeploy

### Liệt kê/chọn instance

```sh
sudo pf instances
```

Nếu có nhiều managed PartFlow Compose project, `purge` cho menu phân trang 10 item/trang,
trừ khi chọn thẳng project:

```sh
sudo pf purge
sudo pf purge --project partflow-staging
```

### Chuỗi an toàn trước khi purge

Tool in summary gồm project, repo, source revision, database, container, volume, network,
image tag, checkpoint, state và environment. Sau đó yêu cầu nhiều confirmation.

Đầu tiên:

```text
PURGE <project>
```

Controller stop application write và tạo verified recovery bundle. Chỉ khi backup hoàn tất
mới hỏi destructive confirmation tiếp theo, gồm:

```text
DELETE <database>
ERASE <project> <random-challenge>
```

Xóa normal revision backup hoặc reset `pf-config.json` có confirmation riêng. Không có
`--yes` bypass.

### Purge recovery bundle

Nằm tại:

```text
recovery/<project>/purge-<timestamp>-<sha12>-<suffix>/
```

Nó giữ tối đa functional state có thể dựng lại an toàn:

```text
source.tar.gz                 exact deployed source
workspace.tar.gz              editable repo hiện tại nếu khác deployed source
configuration/.env            external runtime environment
configuration/pf-config.json  snapshot admin settings
images.tar                    current/available PartFlow application images
postgres-globals.sql
databases/active.dump
databases/<retained>.dump
revision-checkpoints.tar.gz
state/*.json
manifest.json
manifest.sha256
```

Database dump được restore-test. Nếu PostgreSQL data volume tồn tại nhưng không tạo được
recoverable backup, purge refuse xóa volume đó.

Recovery bundle nhằm dựng lại **functional PartFlow state**, không cố khôi phục Docker
container ID/network ID giống từng byte.

### Giữ/xóa revision backups khi purge

Giữ normal checkpoints:

```sh
sudo pf purge --keep-backups
```

Archive chúng vào recovery rồi xóa normal checkpoint tree:

```sh
sudo pf purge --delete-backups
```

Reset luôn local admin config:

```sh
sudo pf purge --reset-admin-config
```

Root-owned `control/` vẫn được giữ để có thể deploy sạch ngay sau purge.

### Purge bị gián đoạn

Nếu mất điện/SSH sau khi destructive deletion đã bắt đầu, chạy `purge` lại. Journal nhận diện
incomplete purge và yêu cầu resume confirmation trước khi tiếp tục dựa trên verified bundle.

### Brand-new deploy sau purge

```sh
sudo pf deploy --latest
```

Full purge thông thường xóa `config/.env`, vì vậy deploy wizard sẽ tạo environment mới và
PostgreSQL password mới. Nếu một recovery path cụ thể giữ external configuration thì deploy
sẽ validate trước khi reuse.

Smoke test xong:

```sh
sudo pf backup
```

### Restore nguyên functional instance đã purge

Xem recovery:

```sh
sudo pf recoveries
sudo pf recoveries --page 2
sudo pf recoveries --project partflow-staging
```

Restore vào target project đang trống:

```sh
sudo pf restore-instance RECOVERY_ID
```

Restore sẽ đưa saved repository workspace trở lại, restore `config/.env`, load saved image,
recreate/restore database set, restore checkpoint history/state rồi health-check backend/frontend.
Installed root-owned control plane hiện tại được giữ; recovery không downgrade lifecycle
controller giữa operation. `config/pf-config.json` hiện tại cũng tiếp tục là authoritative config;
bản được lưu trong recovery chỉ để compare/reapply thủ công, không bị activate giữa restore.

### Khôi phục old data side-by-side

Nếu instance mới đã chạy nhưng cần xem/export data cũ:

```sh
sudo pf restore-instance RECOVERY_ID --side-by-side
```

Old active DB được restore dưới tên `pf_recovery_*`. Current app/database không bị thay.

PartFlow **không** generic auto-merge recovered DB vào active DB mới. `PartMovement`, quantity
lineage, allocation, reversal và derived current state có domain invariant không thể merge
an toàn bằng generic SQL `INSERT`. Hãy restore side-by-side rồi xây explicit domain-aware
import/reconciliation cho đúng loại data thật sự cần mang qua.

## 13. Release check và scheduled task

Chỉ check:

```sh
sudo pf release-check
```

Muốn cho phép unattended staging update, sửa:

```text
/volume1/docker/partflow/config/pf-config.json
```

và đặt:

```text
"auto_update": true
```

Scheduled update chặt hơn manual update: phải có eligible published release, CI success đúng
exact SHA, không có migration/config/dependency condition cần human review, và workspace vẫn
khớp deployed revision.

DSM Task Scheduler nên gọi root-owned wrapper:

```sh
/volume1/docker/partflow/control/backup.sh
```

và:

```sh
/volume1/docker/partflow/control/release-check.sh --apply
```

Không schedule `reset-db`, `purge`, `restore-instance` hay destructive interactive command.

## 14. Raw Compose nâng cao

Ưu tiên `sudo pf ...` vì controller pin path và serialize state-changing operation.

Nếu thật sự cần raw Compose, dạng tương đương là:

```sh
sudo env PARTFLOW_REPO_ROOT=/volume1/docker/partflow/repo \
  docker compose \
  --project-directory /volume1/docker/partflow/repo \
  --env-file /volume1/docker/partflow/config/.env \
  -p partflow-staging \
  -f /volume1/docker/partflow/control/compose.nas.yaml \
  ps
```

Raw Docker/Compose bỏ qua controller lock, recovery check và destructive guard. Không chạy
song song với `pf update`, `pf backup`, `pf reset-db`, `pf purge` hoặc `pf restore-instance`.

Không dùng các lệnh rộng như:

```text
docker system prune --volumes
docker volume prune
```

để reset PartFlow trên NAS có thể đang host workload khác.

## 15. Update control plane

Application `update` cố ý không self-update `control/`.

Khi một reviewed repository revision có Admin version mới:

```sh
cd /volume1/docker/partflow/repo
# Review deployment/control changes và Git status trước.
sudo sh ./deploy/synology/install-control.sh
sudo pf doctor
```

Installer archive control cũ tại:

```text
recovery/control-upgrades/
```

Explicit install step chính là security boundary cho phép `repo/` writable bởi users.
Không chạy `install-control.sh` chưa review/không rõ nguồn bằng `sudo`.

## 16. Troubleshooting

### SMB thấy `repo/` nhưng không sửa được

Trước tiên:

```sh
sudo pf permissions
```

Sau đó kiểm tra DSM Shared Folder permission phải cho account/group Read/Write.

### SMB không sửa/xóa được backup/recovery

Đúng thiết kế. Đây là recovery artifact nên group chỉ read/copy. Muốn chỉnh thì copy sang vị
trí khác.

### `sudo sh ./pf.sh ...` bị từ chối

Đúng behavior của v2.5. Bản trong repo chỉ là source. Dùng:

```sh
sudo pf ...
```

hoặc cài/update control trước:

```sh
sudo sh ./deploy/synology/install-control.sh
```

### Có `.env` nhưng Compose báo thiếu biến

Chạy:

```sh
sudo pf doctor
```

Raw Compose không tự biết external config. Runtime file authoritative là:

```text
/volume1/docker/partflow/config/.env
```

### Có local source edit trước update

Xem:

```sh
sudo pf status
```

Manual update lưu workspace khác biệt vào `workspace.tar.gz` trước khi replace. Unattended
update refuse drift và chờ manual review.

### Lifecycle operation dang dở

Chạy:

```sh
sudo pf status
```

Sau đó dùng recovery phù hợp (`resume`, `rollback`, chạy lại/resume `purge`, hoặc
`restore-instance`) thay vì tự xóa state file.

## 17. Command reference

| Command | Mục đích |
| --- | --- |
| `sudo pf doctor` | Validate host tool, control security, Compose, env và capacity cơ bản |
| `sudo pf permissions` | Chuẩn hóa repo/config writable và backup/recovery read-only |
| `sudo pf status` | Deployed revision, workspace drift, DB revision, container, pending operation |
| `sudo pf deploy --latest` | Brand-new staging từ latest configured branch SHA |
| `sudo pf deploy --commit FULL_SHA` | Brand-new deploy từ exact commit |
| `sudo pf deploy --release TAG` | Brand-new deploy từ published release |
| `sudo pf abort-deploy` | Xóa incomplete first deploy trước khi frontend mở |
| `sudo pf update --latest` | Managed staging update theo latest branch SHA |
| `sudo pf update --commit FULL_SHA` | Update tới exact commit |
| `sudo pf update --release TAG` | Update tới release |
| `sudo pf backup` | Tạo và restore-test revision checkpoint |
| `sudo pf backups --page N` | List checkpoint, 10/trang |
| `sudo pf rollback [BACKUP_ID]` | Code rollback, giữ current DB |
| `sudo pf rollback BACKUP_ID --restore-db` | Restore code + selected database state |
| `sudo pf reset-db` | Kích hoạt clean migrated DB, vẫn giữ recoverability |
| `sudo pf instances` | List managed PartFlow instances |
| `sudo pf purge [--project NAME]` | Full recoverable purge một staging instance |
| `sudo pf recoveries` | List purge recovery bundles |
| `sudo pf restore-instance RECOVERY_ID` | Dựng lại functional instance đã purge |
| `sudo pf restore-instance RECOVERY_ID --side-by-side` | Restore old DB bên cạnh current instance |
| `sudo pf release-check` | Check eligible release, không apply |
| `sudo pf release-check --apply` | Unattended update chỉ khi mọi gate pass |
| `sudo pf resume` | Resume chỉ khi early-failure state chưa thay đổi |

## 18. Giới hạn validation

Offline tests đi kèm simulate Docker/PostgreSQL nhưng thực sự chạy controller logic,
filesystem/archive/checksum, permission policy, deployed-source/workspace separation,
purge/recovery và path construction. Chúng không thay thế integration rehearsal trên DSM +
Docker + PostgreSQL thật.

Trước khi dựa vào v2.5 recovery cho data quan trọng, nên chạy ít nhất một vòng disposable
staging trên NAS thật:

```text
install-control
→ doctor
→ backup
→ update
→ purge
→ restore-instance
→ verify UI/data
→ purge
→ deploy --latest
→ restore old DB --side-by-side
```

Không coi staging procedure là production-ready cho tới khi các production phase và
backup/disaster-recovery gate của repository được hoàn thành riêng.
