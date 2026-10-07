# PartFlow NAS Admin v2.5

> **Bản tiếng Anh là source of truth.** [English source](./SYNOLOGY_ADMIN.md).
> Baseline đồng bộ: package revision PF-A1.4 (trên commit `53246a1`).
>
> Version: **2.5.0**
> Prepared: **2026-09-11**
> Phạm vi: quản trị **Synology staging** trong LAN giới hạn. Đây chưa phải gói production hardening.

> **Checkpoint Deployment Admin PF-A1.1 (2026-09-15, đã sửa theo audit-r1 và audit-r2) — trạng thái phát triển, chưa phải bản phát hành NAS.**
> Source controller trong repository giờ yêu cầu *protected instance registration*
> (`pf_instance.py`: installation root với `bootstrap/` (launcher `pf`, `bootstrap.conf`,
> verifier `pf_bootstrap.py`), `registry/instances.json` và `registry/reservations/`,
> `locks/`, `releases/<id>/` kèm `control-manifest.json` được pin, `instances/<uuid>/record.json`).
> Launcher đã cài chạy bootstrap verifier trước: interpreter, cấu hình, mọi ancestor và toàn
> bộ cây release đã pin được kiểm tra trước khi bất kỳ code release nào chạy. Khởi tạo
> Controller, `--help`, `instances`, `status`, `backups`, `recoveries` và `doctor` mặc định là
> read-only; `status`/`doctor` in identity, tóm tắt trust và pending journal trước, và
> **không** gọi Git/Docker/Compose khi context hoặc cấu hình runtime bị từ chối. Lệnh
> mutation cần `--instance <slug|uuid>` (hoặc default được bảo vệ), inventory đường dẫn sạch
> (không trùng/lồng/alias giữa các instance), stable lock riêng của instance dưới
> `<root>/locks/` và một route journal tường minh. Đường dẫn managed/authoritative chỉ được
> chấp nhận ở đúng một cách viết POSIX chuẩn (một dấu `/` đầu, không có thành phần rỗng, `.`
> hay `..`, không có `/` cuối); mọi cách viết khác như `//volume1/...` bị từ chối, không bao
> giờ được chuẩn hoá ngầm. Installation root chỉ do bootstrap đã cài quyết định:
> `--installation-root` là handshake verifier→release, và nếu người vận hành tự truyền
> `--installation-root` ở bất kỳ vị trí nào thì toàn bộ lệnh bị từ chối trước khi đọc registry
> hay journal. Hai record đã publish cùng claim một cặp daemon `engine_id` + Compose project
> được `instances` báo `CONFLICT` và cả hai bên đều bị từ chối mutation cho tới khi quản trị
> viên sửa registry. Layout v2.5 mô tả bên dưới là **chưa đăng ký** trong checkpoint này:
> `control/pf.sh` đã cài chỉ in báo cáo read-only bằng shell, không chạy Python nào từ thư
> mục control legacy và chỉ nêu tên pending journal chứ không đọc hay in nội dung.
> `install-control.sh` vẫn là installer legacy v2.5; không chạy nó trên NAS đang hoạt động
> với checkpoint này. Registration hiện chỉ là một transaction Python cho fixture dùng một
> lần (`register_instance`), chưa phải lệnh cho người vận hành.

> **Checkpoint Deployment Admin PF-A1.2 (2026-09-15) — runner, frozen configuration, protected
> source store; vẫn là trạng thái phát triển, chưa phải bản phát hành NAS.** Mọi tiến trình con
> của control release (Git, Docker/Compose, các công cụ SQL trong container `db`, chẩn đoán
> host) giờ đi qua một runner duy nhất (`pf_runner.py`): executable phải được trusted installer
> đăng ký trong `<root>/bootstrap/tools.conf` (`docker`, `docker_compose` tuỳ chọn, `git`,
> `ip`, `hostname`; đường dẫn tuyệt đối, được kiểm qua chuỗi symlink tin cậy và ancestor được
> bảo vệ, không bao giờ tìm trên `PATH`), môi trường con được dựng từ allowlist (`PATH` cố
> định, `HOME` là thư mục riêng `instances/<uuid>/home` của instance, `DOCKER_CONFIG` bên
> trong đó với client config rỗng và thư mục plugin rỗng, `DOCKER_HOST` lấy từ daemon
> endpoint đã đăng ký, `GIT_CONFIG_NOSYSTEM`/`GIT_CONFIG_GLOBAL=/dev/null`), đối số là
> mảng, output được giới hạn và redact (mật khẩu database và dạng URL-encoded của nó không
> bao giờ lọt vào thông báo hay log), mọi lời gọi đều có deadline, timeout hay ngắt giữa
> chừng sẽ kết thúc cả process group. Ngắt giữa chừng nghĩa là `SIGINT`, `SIGTERM`, `SIGHUP`
> (ví dụ phiên SSH bị rớt) hoặc `SIGQUIT`: controller kết thúc process group của tiến trình
> con, ghi effect và chạy bước dừng fail-closed rồi mới nhả instance lock; signal lặp lại
> không cắt ngang quá trình đó (chỉ `SIGKILL` hoặc mất điện mới làm được; phục hồi sau các
> trường hợp đó là việc của lần chạy kế tiếp). Mọi tiến trình con mutation (Compose `up`/`down`/
> `stop`/`build`/`run`, `createdb`/`dropdb`/`pg_restore`, SQL mutation, Docker `tag`/`rm`/
> `image load`, store `fetch`) mang một effect descriptor (kind, verb, targets — không bao giờ
> chứa giá trị ứng dụng), nên timeout hay ngắt giữa chừng của nó được ghi vào
> `instances/<uuid>/operations/<id>/unresolved-effects.json` và `status`/`doctor` liệt kê
> trước mọi lần thử lại; tiến trình con read-only (`ps`, `config`, `logs`, `inspect`,
> `pg_dump`, `pg_restore --list`, health probe, `SELECT`) không ghi gì. Các chương trình
> client PostgreSQL chạy trong service `db` bằng argv trực tiếp (không `sh -c`), kết nối
> bằng `POSTGRES_USER` của cấu hình đã đóng băng.
> `config/.env` được parse nghiêm ngặt (chỉ bảy key PartFlow, không trùng, không `export`,
> không expansion; literal dạng không quote/single quote/double quote; xem mục 6) và mọi
> lệnh mutation đều đóng băng nó trước thành snapshot riêng 0400
> `instances/<uuid>/operations/<id>/app.env`; operation chỉ dùng snapshot đó, sửa
> `config/.env` giữa chừng sẽ bị phát hiện, và giá trị có sẵn không thể đóng băng literal
> (dấu nháy đơn, backslash cuối, ký tự điều khiển) là `migration-issue` tường minh — không
> bao giờ bị ghi đè hay sinh lại. URL kết nối backend được tạo với credential percent-encoded
> dưới tên `PARTFLOW_DATABASE_URL` (`compose.nas.yaml` không còn ghép `POSTGRES_PASSWORD`
> thẳng vào URL). Git đặc quyền không bao giờ chạy trên `repo/`: source được fetch vào bare
> store bảo vệ dưới `<root>/sources/` (cấu hình riêng, không hook/fsmonitor/include/alternates,
> chỉ HTTPS) và export từng blob (submodule, symlink, Git LFS pointer và đường dẫn được track
> mang tên artifact workspace bị bỏ qua — `.env`, `node_modules`, `.venv`, `__pycache__`,
> `.pytest_cache` — đều bị từ chối, nên không có file nào được deploy mà nằm ngoài manifest);
> workspace được so sánh byte/mode với manifest bảo vệ ghi lại khi công cụ deploy một cây;
> cây không có provenance như vậy là `unknown`, không bao giờ được gán commit SHA (mục 8). Chẩn đoán
> read-only đưa cho Compose một env-file rỗng do registration tạo và các giá trị dưới dạng
> biến allowlist, nên Compose không đọc file nào có thể sửa được. Compose passthrough bị giới
> hạn trong các từ đã biết và từ chối dump `config` thô cho tới khi PF-A1.4 bỏ hẳn route này;
> `install-control.sh` vẫn là installer legacy.

> **Checkpoint Deployment Admin PF-A1.3 (2026-10-06) — Compose envelope, daemon binding, exact
> resource inventory; vẫn là trạng thái phát triển, chưa phải bản phát hành NAS.**
> *Daemon binding.* Registration resolve đường dẫn Docker socket (link hệ thống như `/var/run` →
> `/run`) và lưu endpoint `unix://` đã resolve; validation kiểm tra offline (không có thành phần
> symbolic link, ancestor được bảo vệ, socket do root sở hữu và không world-writable). Socket
> không tồn tại chỉ là một note (daemon đang dừng), không phải lỗi trust. Trước Docker/Compose
> child đầu tiên của mỗi process, controller chạy `docker info` một lần và bắt buộc đúng engine
> ID đã đăng ký và daemon rootful; drift (`daemon-drift`), rootless (`daemon-rootless`), câu trả
> lời không dùng được (`daemon-info-invalid`) hoặc không trả lời (`daemon-unreachable`) chặn mọi
> mutation trước bất kỳ confirmation (kể cả prompt `RESUME PURGE`/`RESUME ABORT DEPLOY` của một
> purge hoặc abort bị gián đoạn), ghi journal, pause hay Docker effect nào. `status`/`doctor`
> in dòng daemon, đánh dấu mọi mục Docker sau đó là `unavailable: <code>` mà không liên lạc lại
> daemon, và vẫn in mọi mục không liên quan Docker.
> *Compose envelope.* Trước mọi `up`, `run`, `build`, `create`, `start`, `restart`, `scale` hoặc
> `watch` (managed hay passthrough), controller render `docker compose … config --format json` với đúng input của
> lời gọi đó (installed `compose.nas.yaml`, image override được bảo vệ, frozen env-file và các
> child value hiệu lực, kể cả tên database tạm theo từng lời gọi) vào file riêng 0600
> `compose-<n>.json`, kiểm tra theo allowlist topology PartFlow (ba service, chỉ `postgres_data`
> và `default`, không có option đặc quyền host, không bind mount hay Docker socket, một port
> frontend trên địa chỉ đã duyệt, instance label ở mọi nơi), so sánh literal từng application
> value, và ghi hash input vào `compose-envelope.json`. Chỉ `POSTGRES_DB=pf_migrate_*`/`pf_clean_*`
> (database rehearsal của update và của reset-db) được override theo lời gọi. Compose v1 không
> được hỗ trợ (không render được `config --format json`).
> *Instance label.* `compose.nas.yaml` gắn `io.deploy-admin.instance-id` lên ba service, hai image
> build, volume `postgres_data` và network `default`, từ `DEPLOY_ADMIN_INSTANCE_ID` do controller
> sinh từ registration được bảo vệ.
> *Exact inventory.* Mọi container (kể cả container đã dừng, không bao giờ đọc `Config.Env`), các
> tên volume/network của topology và volume, network, image tag có label được phân loại thành
> owned, excluded (reference, bind path, resource owned ngoài topology, tag sai ngữ pháp, và tag
> foreign-in-use: container của ứng dụng khác dùng tag đó, hoặc dùng image ID đó khi container
> được tạo từ image ID) hoặc blocker (`resource-legacy-unlabeled`, `resource-name-collision`,
> `resource-foreign-claim`, `resource-label-conflict`, `resource-shared`,
> `resource-unsupported-driver`). Tiền tố tên không bao giờ chọn resource nào. `deploy` và
> `restore-instance` exact yêu cầu target trống (`resource-target-not-empty`); `backup`, `update`,
> `rollback`, `reset-db`, `resume`, restore side-by-side, `release-check --apply` và passthrough có
> mutation chạy ownership preflight ngay sau lock (`resource-not-owned`). Resource legacy hoặc
> ngoại lai không bao giờ được tự động adopt (adoption thuộc PF-A2). Container biến mất giữa
> `docker ps -a` và lệnh inspect của nó (container ngắn hạn của ứng dụng khác) khiến inventory
> liệt kê lại; sau ba lần như vậy bước đó dừng với `inventory-unstable`.
> *Closed deletion plan.* `purge` và `abort-deploy` chỉ xóa đúng một plan đã đóng băng
> (`deletion-plan.json`, hash nằm trong journal): mỗi item và mọi container đang dùng volume hoặc
> network được kiểm tra lại ngay trước effect của nó và item còn tồn tại phải vẫn là owned (ví dụ
> image tag mà một container ngoại lai bắt đầu dùng), thay đổi thì dừng với `plan-drift`, resume
> chạy đúng plan đó và không bao giờ thêm resource, và image tag chỉ bị xóa khi purge recovery
> bundle bao phủ image ID của nó. Không prune, không dùng `compose down -v`, không ép xóa image,
> và không bao giờ xóa đường dẫn bind mount. `abort-deploy` không còn chạy
> `compose down --volumes`; nó xóa container, network và volume trong plan và giữ lại image.

> **Checkpoint Deployment Admin PF-A1.4 (2026-10-06) — mọi entry route đi qua các primitive A1,
> không còn Compose catch-all; PF-A1 đóng offline, vẫn là trạng thái phát triển, chưa phải bản
> phát hành NAS.**
> *Không còn Compose catch-all.* `pf` chỉ nhận đúng các lệnh managed ở mục 17 và hai Compose view
> read-only. Mọi từ mà Compose passthrough trước đây chuyển tiếp (`up`, `start`, `restart`,
> `create`, `scale`, `watch`, `unpause`, `down`, `stop`, `kill`, `pause`, `rm`, `run`, `exec`, `cp`,
> `attach`, `build`, `pull`, `push`, `version`, `top`, `images`, `port`, `ls`, `events`, `stats`,
> `wait`, `config`) bị từ chối với `compose-route-removed` kèm tên lệnh managed thay thế; global
> option của Compose hoặc Docker đứng đầu (`-f`, `-p`, `--env-file`, `--project-directory`,
> `--profile`, `-H`, `--context`, …) bị từ chối với `compose-override-refused`; mọi option đứng đầu
> khác, kể cả dạng viết tắt như `--inst`, bị từ chối với `unknown-option`; từ không biết bị từ chối
> với `unknown-command` (tất cả exit 1). Các lần từ chối này xảy ra trước khi đọc registry, lấy lock
> hay khởi động bất kỳ process nào. Mọi lệnh đều từ chối option viết tắt.
> *View read-only.* `pf ps` và `pf logs` (mục 14) dựng lại lệnh Compose chỉ từ các option đã parse
> (service `db`, `backend`, `frontend`) và chạy qua context đã validate, runner và daemon binding,
> output được redact và giới hạn; không lấy lock, không tạo operation. `pf` không kèm lệnh chính là
> `pf ps`.
> *Lệnh chạy không có terminal.* Lệnh khởi động không có terminal (scheduled task, script, `ssh`
> không có `-t`) bị từ chối trước lock: lệnh có hỏi xác nhận gõ tay bị từ chối với
> `terminal-required`; `backup`, `permissions` và `release-check` phải nêu rõ instance
> (`--instance <slug|uuid>`, nếu không thì `instance-required-unattended`) và sau đó cần một grant
> trong protected policy cho loại thao tác đó, mà ở checkpoint này không policy nào cấp
> (`policy-grant-required`, exit 20; grant có từ PF-A4.3). `release-check --apply` bị từ chối với
> `auto-apply-not-permitted` (exit 20) dù có hay không có terminal; `auto_update` trong
> `pf-config.json` chỉ là đề xuất. Wrapper `backup.sh` và `release-check.sh` của scheduler bắt
> buộc `--instance` và chỉ thực thi launcher nằm cạnh nó (mục 13).
> *Fail-closed stop.* Sau một operation thất bại có pending journal, controller chỉ dừng các
> one-off job Compose của chính instance này theo exact inventory (job đã biến mất được báo và bỏ
> qua), rồi dừng các application service; label cũ `partflow.admin.project` không chọn gì cả. Thông
> báo nêu `pf --instance <slug> status`.
> *Thẩm quyền restore.* `recoveries` và `restore-instance` chỉ liệt kê và kiểm tra bundle trong
> thư mục `recovery/<project>/` của chính instance đã chọn. `--project` là tên Compose project,
> không bao giờ là đường dẫn, và khi đi cùng `--instance` thì phải đúng project của instance đó
> (`selection-conflict`). Bundle nằm ngoài thư mục đó bị từ chối (`recovery-outside-instance`), và
> bundle chỉ được restore `deployed.json`, `last-reset.json` và `observed-tags.json` vào protected
> state (`recovery-state-file-refused`).
> Khối này **thay thế** các câu về passthrough trong khối PF-A1.2 và PF-A1.3 ở trên.

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

và, từ PF-A1.3, định danh instance dùng làm label cho mọi resource Compose tạo ra
(`DEPLOY_ADMIN_INSTANCE_ID`, sinh từ registration được bảo vệ, không bao giờ đọc từ `.env`).

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
hay installed Compose file. Xem §14 nếu thật sự cần raw Compose; dạng đó cũng phải khai báo
`DEPLOY_ADMIN_INSTANCE_ID`.

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

> **Cảnh báo (PF-A1.4).** `install-control.sh` cài layout v2.5 cũ, mà launcher của nó ở checkpoint
> này chỉ read-only (mục 15). Trên một NAS v2.5 đang chạy, nó thay thư mục `control/` v2.5 đang
> hoạt động (được archive vào `recovery/control-upgrades/`) bằng một thư mục read-only cho tới khi có
> installer PF-A2. Không chạy nó trên NAS bạn vẫn đang quản trị bằng v2.5.

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
8. Chạy `pf permissions` để chuẩn hóa quyền repo/config/backup/recovery. Ở checkpoint này lệnh
   đó tới launcher read-only cũ, vốn từ chối `permissions` trên installation chưa đăng ký; do
   `set -eu`, installer dừng tại đó và không in thông báo "installation complete". Thư mục control
   vẫn là thư mục read-only cũ cho tới PF-A2.

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

`auto_update` chỉ là đề xuất: apply không người trực cần một grant trong protected policy, mà
checkpoint này không cung cấp (mục 13).

### `config/.env`

Khi brand-new deploy, `deploy` tạo file này bằng wizard từ installed template
`control/nas.env.example`. Script tự sinh `POSTGRES_PASSWORD` 64 ký tự hex bằng secure
randomness và không in password ra terminal.

Ví dụ:

```dotenv
POSTGRES_USER=partflow_staging
POSTGRES_PASSWORD=<generated-secret>
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

Từ PF-A1.2 file này được parse như dữ liệu với grammar nghiêm ngặt: đúng bảy key này, mỗi
key một lần, `KEY=VALUE` không có khoảng trắng quanh `=` và không có `export`; dòng comment
bắt đầu bằng `#`; giá trị hoặc không quote (không khoảng trắng, dấu nháy hay `#`; `$` và `\`
là literal), hoặc single quote (`'...'`, literal, không chứa `'`), hoặc double quote (chỉ
escape `\\` và `\"`, không expand `$`). Giá trị không bao giờ được expand, evaluate hay ghi
lại. Giá trị không thể render literal vào snapshot riêng (dấu nháy đơn, backslash cuối, ký tự
điều khiển) được báo là `migration-issue` và chặn lệnh mutation cho tới khi bạn sửa file bằng
tay; mật khẩu hiện có không bao giờ bị sinh lại.

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

Từ PF-A1.3, một resource topology Compose chưa có label hoặc thuộc instance khác (container,
volume hoặc network) làm `deploy` bị từ chối với `resource-target-not-empty` trước mọi thay
đổi, và `abort-deploy` in rồi đóng băng plan chính xác trước `ABORT DEPLOY <project>`; lệnh này
không bao giờ chạy `compose down --volumes`, giữ lại image, và một lần abort bị gián đoạn sẽ
tiếp tục đúng plan đó sau `RESUME ABORT DEPLOY <project>`.

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
Workspace: provenance git_commit|unknown | manifest commit <SHA hoặc none> | differs from deployed: True/False | changes: ...
```

Từ PF-A1.2 dòng Workspace đến từ phép so sánh byte/mode an toàn theo fd giữa `repo/` và
manifest bảo vệ mà công cụ ghi lại khi deploy một cây
(`instances/<uuid>/artifacts/source-manifest.json`); không có lệnh Git nào chạy trên `repo/`,
metadata `.git` của nó (hook, fsmonitor, filter, include, alternates, remote) là dữ liệu của
người sửa và không bao giờ được dùng. Workspace không có manifest, hoặc khác manifest, có
provenance `unknown`: `status` vẫn chạy, nhưng không có commit SHA nào được bịa ra.

Phép so sánh chỉ bỏ qua artifact workspace *không được track* theo tên (`.env`,
`node_modules`, `.venv`, `__pycache__`, `.pytest_cache`; `.git` là control metadata). Chính
sách này không bao giờ che nội dung được track: commit track một đường dẫn mang các tên đó
bị source store từ chối trước khi export (`unsupported source path (tracked reserved
workspace artifact name)`), candidate tree chứa file như vậy bị từ chối trước khi `repo/`
bị chạm tới (trong `rollback` và `restore-instance`, cùng với bước chứng minh provenance từ
store, trước mọi xác nhận, pending journal, thay đổi `config/.env`, pause, safety snapshot
hay database swap), và manifest liệt kê đường dẫn như vậy không thể được verify — công cụ dừng
fail-closed thay vì báo khớp khi chưa chứng minh.

Manual update khi thấy workspace dirty/khác deployed revision sẽ đưa working tree hiện tại
vào pre-update checkpoint dưới dạng `workspace.tar.gz`, rồi mới thay `repo/` bằng revision
được export từ protected source store. Unattended release update sẽ **refuse** workspace đang
drift thay vì tự xóa local work.

`deploy --current` chứng minh workspace thay vì tin nó: commit mà checkout tự nhận
(`.git/HEAD`, đọc như dữ liệu) được fetch vào protected store, export ra candidate riêng, và
workspace phải bằng đúng cây đó từng byte; nếu không, lệnh dừng kèm danh sách khác biệt và
yêu cầu `--commit`/`--latest`/`--release` tường minh. Cây chép từ ZIP hay checkout chưa xác
minh vì thế không bao giờ được deploy như "current".

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

Từ PF-A1.3, một resource topology Compose chưa có label hoặc thuộc instance khác làm `update`
bị từ chối với `resource-not-owned` ngay sau lock, trước mọi confirmation hay thay đổi.

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

Từ PF-A1.3 trình tự là:

1. **Preliminary plan.** Controller inventory chính xác daemon đã bind (xem ghi chú checkpoint
   PF-A1.3 ở đầu tài liệu) và dựng một deletion plan tham khảo. Bất kỳ blocker nào (resource
   legacy, trùng tên, thuộc instance khác, xung đột label, dùng chung hoặc không được hỗ trợ)
   làm purge bị từ chối với `resource-blocked` ngay tại đây: không confirmation, không pause,
   không bundle.
2. **Summary.** Tool in project, repo, source revision, database, đúng các container, volume và
   network, các image tag đang chờ recovery bundle bao phủ, mọi exclusion được giữ lại và mọi
   bind path được giữ lại, checkpoint, state và environment.
3. **Confirmation đầu tiên** `PURGE <project>`, sau đó dừng application write.
4. **Bundle và binding plan.** Verified recovery bundle được tạo. Sau khi `images.tar` được kiểm
   tra, controller inventory lại, dựng binding plan (image tag có image ID được lưu trong bundle
   trở thành candidate; owned tag khác được giữ lại và báo cáo) và so với preliminary plan, chỉ
   bỏ qua các tag do chính lần purge này tạo. Có khác biệt thì dừng với `plan-changed` và mở lại
   application; khi đó thư mục bundle không có `manifest.json`. Resource trở thành blocker trong
   khoảng này cũng là `plan-changed` (không bao giờ là thông báo `resource-blocked` trước
   confirmation): user ngoại lai của volume hoặc network (`resource-shared`) thì application được
   mở lại; blocker khác thì service vẫn dừng và journal ở `paused`, vì Compose có thể adopt hoặc
   tạo lại resource đó; xử lý xong thì chạy `pf resume`. Nếu không, `resources_before_purge`
   được niêm phong vào manifest từ binding plan.
5. **Destructive confirmation**, sau khi in đầy đủ binding plan:

   ```text
   DELETE <database>
   ERASE <project> <random-challenge>
   ```

   Xóa normal revision backup hoặc reset `pf-config.json` có confirmation riêng. Không có
   `--yes` bypass.
6. **Thực thi khép kín.** Plan được ghi bền vững (`deletion-plan.json`, hash trong journal) trước
   lần xóa đầu tiên. Container, rồi network, volume và các image tag được bao phủ bị xóa lần
   lượt; mỗi item, và mọi container dùng volume hoặc network đó (kể cả container đã dừng), được
   kiểm tra lại ngay trước effect của nó, thay đổi (kể cả item còn tồn tại nhưng không còn owned)
   thì dừng với `plan-drift`. Không prune, không
   bao giờ dùng `compose down -v`, không ép xóa image, và không bao giờ xóa bind-mounted path.

Bảo đảm này giả định daemon tin cậy và không có hoạt động song song: Docker không có
compare-and-delete nguyên tử, nên một Docker administrator chạy song song nằm ngoài bảo đảm.

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
incomplete purge và plan đã đóng băng của nó; sau `RESUME PURGE <project> <recovery-id>` chỉ các
item còn lại trong plan được xử lý (item đã xóa được ghi là `already-absent`), resource mới xuất
hiện không bao giờ được thêm vào, và plan bị thiếu, bị di chuyển, là symlink hoặc bị sửa sẽ bị
từ chối với `plan-invalid`. Journal ghi trước PF-A1.3 (không có plan đóng băng) bị từ chối với
`plan-missing`: hãy review thủ công các resource còn lại.

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

Từ PF-A1.3, kiểm tra target trống chính là exact inventory: bất kỳ container, volume hoặc
network owned hay blocker nào của project đều bị từ chối với `resource-target-not-empty` trước
mọi confirmation.

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

Chỉ check, chạy tương tác:

```sh
sudo pf --instance <slug> release-check
```

Apply một release đã chọn bằng tay với `sudo pf --instance <slug> update --release <tag>`.

**Ở checkpoint này không có scheduled task trên instance do pf quản lý.** Khi không có terminal,
`backup` và `release-check` bị từ chối với `policy-grant-required` (exit 20) và
`release-check --apply` với `auto-apply-not-permitted` (exit 20): thao tác không người trực cần một
grant trong protected policy cho đúng loại thao tác đó, và grant có từ PF-A4.3. Đặt
`"auto_update": true` trong `config/pf-config.json` không bật được gì; nó chỉ là đề xuất. Trong lúc
chờ, hãy chạy checkpoint tương tác rồi copy ra ngoài NAS:

```sh
sudo pf --instance <slug> backup
```

Khi đã có grant, một DSM task root-owned phải nêu instance và chạy wrapper mà installer PF-A2 đặt
cạnh launcher đã cài, hoặc chạy chính launcher:

```sh
<root>/bootstrap/backup.sh --instance <slug>
<root>/bootstrap/release-check.sh --instance <slug> [--channel stable|prerelease] [--apply]
<root>/bootstrap/pf --instance <slug> backup
```

Wrapper chỉ nhận đúng các tham số này (tham số khác in usage và exit 2), chạy với `PATH` cố định
và chỉ thực thi launcher nằm cạnh nó. Bản sao nằm trong thư mục `control/` cũ sẽ tới launcher
read-only cũ, vốn từ chối `backup` và `release-check` trên installation chưa đăng ký.

Khi đã được cấp grant, scheduled update vẫn chặt hơn manual update: phải có eligible published
release, CI success đúng exact SHA, không có migration/config/dependency condition cần human
review, và workspace vẫn khớp deployed revision.

Không schedule `reset-db`, `purge`, `restore-instance` hay destructive interactive command; không
có terminal thì chúng vẫn bị từ chối (`terminal-required`).

## 14. Raw Compose nâng cao

Ưu tiên `sudo pf ...` vì controller pin path và serialize state-changing operation.

Từ PF-A1.4 `pf` không còn chuyển tiếp lệnh Compose. Chỉ còn hai view read-only:

```sh
sudo pf --instance <slug> ps [-a] [-q] [--services] [--status STATUS] [--format table|json] [SERVICE...]
sudo pf --instance <slug> logs [--tail N] [-f] [-t] [--no-color] [--no-log-prefix] [--since V] [--until V] [SERVICE...]
```

`SERVICE` là `db`, `backend` hoặc `frontend`; `STATUS` là một trong `paused`, `restarting`,
`removing`, `running`, `dead`, `created`, `exited`; `V` là khoảng thời gian như `30m`, `2h`, hoặc
ngày/giờ RFC 3339. `--tail` mặc định 200, cho phép 1–10000. `logs -f` kết thúc sau 1 giờ hoặc
64 MiB output với `logs-bound-reached` (exit 1); phần output đã hiện tới lúc đó là đầy đủ. Cả hai
view chạy qua context đã validate, các tool đã đăng ký và daemon binding, redact mật khẩu database,
không lấy lock và không tạo operation. Mọi từ hoặc option Compose khác bị từ chối (mục 16).

Không có route managed để chạy CLI của ứng dụng trong container backend (`exec` và `run` bị từ
chối; route managed thuộc PF-A4).

Nếu thật sự cần raw Compose bên ngoài controller, dạng tương đương là:

```sh
sudo env PARTFLOW_REPO_ROOT=/volume1/docker/partflow/repo \
  PARTFLOW_DATABASE_URL='postgresql+psycopg://<user>:<password percent-encoded>@db:5432/<db>' \
  DEPLOY_ADMIN_INSTANCE_ID=<instance UUID từ 'pf instances'> \
  docker compose \
  --project-directory /volume1/docker/partflow/repo \
  --env-file /volume1/docker/partflow/config/.env \
  -p partflow-staging \
  -f /volume1/docker/partflow/control/compose.nas.yaml \
  ps
```

Từ PF-A1.2 `compose.nas.yaml` lấy URL kết nối backend từ `PARTFLOW_DATABASE_URL`, do
controller sinh với credential percent-encoded; lệnh raw phải tự cung cấp biến này
(controller không bao giờ đưa `.env` cho Compose như file có thể sửa: nó đưa snapshot đã đóng
băng hoặc env-file rỗng do registration tạo, cộng các biến allowlist).

Từ PF-A1.3 `compose.nas.yaml` còn bắt buộc `DEPLOY_ADMIN_INSTANCE_ID`. **UUID sai sẽ gắn nhầm label
cho mọi resource mà Compose tạo sau đó**: các resource này bị phân loại `resource-foreign-claim`
hoặc `resource-label-conflict` và chặn `purge`, `abort-deploy` cùng mọi guarded command cho tới
khi được review.

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

> **Cảnh báo (PF-A1.4).** Ở checkpoint này `install-control.sh` cài layout cũ có launcher chỉ
> read-only: nó từ chối mọi lệnh trừ báo cáo chẩn đoán, kể cả lệnh `pf permissions` cuối cùng của
> chính installer. Chạy nó trên NAS v2.5 đang hoạt động sẽ thay thư mục control v2.5 đang chạy (được
> archive vào `recovery/control-upgrades/`) bằng một thư mục read-only cho tới khi installer PF-A2
> thay thế.

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

Ở checkpoint này installer đó cài layout read-only cũ (xem cảnh báo ở mục 15); không chạy nó trên
NAS v2.5 đang hoạt động.

### `compose-route-removed`, `compose-override-refused`, `unknown-option` hoặc `unknown-command`

`pf` không còn chuyển tiếp lệnh Compose (PF-A1.4). `compose-route-removed` nêu lệnh managed thay
thế từ đó (ví dụ `pf resume`, `pf update` hoặc `pf rollback` thay cho `up`; `pf purge` hoặc
`pf abort-deploy` thay cho `down`); `compose-override-refused` nghĩa là global option của Compose
hoặc Docker như `-f`, `-p` hay `--env-file` (project, file, env-file, thư mục và daemon là cố
định); `unknown-option` là mọi thứ khác đứng trước lệnh, kể cả dạng viết tắt của `--instance`;
`unknown-command` là từ không biết. Không có gì được đọc hay thay đổi. Dùng `pf ps` và `pf logs`
cho view Compose read-only (mục 14).

### `terminal-required`

Lệnh này hỏi xác nhận gõ tay nhưng được khởi động không có terminal (scheduled task, script,
`ssh` không có `-t`). Chạy tương tác: `sudo pf --instance <slug> <command>`. Không có gì bị thay
đổi.

### `instance-required-unattended`

`backup`, `permissions` hoặc `release-check` chạy không có terminal và instance được chọn qua
default được bảo vệ hoặc vì là registration duy nhất. Lệnh không người trực phải nêu instance:
`pf --instance <slug|uuid> <command>`. Không có gì bị thay đổi.

### `policy-grant-required` hoặc `auto-apply-not-permitted` (exit 20)

`backup`, `permissions` hoặc `release-check` chạy không người trực, hoặc mọi `release-check --apply`,
cần một protected policy cho phép đúng loại thao tác đó. Ở checkpoint này không policy nào cấp
(grant có từ PF-A4.3); `auto_update` trong `pf-config.json` không cấp được. Hãy chạy lệnh tương tác,
và apply release bằng tay với `pf --instance <slug> update --release <tag>`. Không có gì bị thay
đổi.

### `selection-conflict`, `recovery-outside-instance` hoặc `recovery-state-file-refused`

`selection-conflict`: `--project` nêu project khác với instance đã chọn bằng `--instance`; chỉ dùng
`--instance`. `recovery-outside-instance`: bundle không phải thư mục nằm trong
`recovery/<project>/` của chính instance đã chọn; bundle của instance khác hoặc bản sao ở nơi khác
không bao giờ được liệt kê hay restore. `recovery-state-file-refused`: manifest của bundle liệt kê
state file khác `deployed.json`, `last-reset.json` hoặc `observed-tags.json`. Không có gì bị thay
đổi.

### `logs-bound-reached`

`pf logs` dừng ở giới hạn của nó (1 giờ với `-f`, nếu không thì deadline chẩn đoán, hoặc 64 MiB
output). Output đã hiện là đầy đủ tới thời điểm đó; thu hẹp bằng `--since`, `--tail` hoặc tên
service.

### Có `.env` nhưng Compose báo thiếu biến

Chạy:

```sh
sudo pf doctor
```

Raw Compose không tự biết external config. Runtime file authoritative là:

```text
/volume1/docker/partflow/config/.env
```

Nếu `doctor` báo `migration-issue` hoặc lỗi parse cho file này thì controller đã từ chối
đề xuất (key lạ/trùng, quoting không hỗ trợ, secret không thể đóng băng literal); sửa file
bằng tay — controller không bao giờ ghi lại nó. Lệnh raw Compose còn cần
`PARTFLOW_DATABASE_URL` và `DEPLOY_ADMIN_INSTANCE_ID` (mục 14); `DEPLOY_ADMIN_INSTANCE_ID` không
bao giờ được chấp nhận trong `config/.env`.

### Docker daemon bị drift, không liên lạc được hoặc rootless

`daemon-drift`: endpoint đã đăng ký trả lời với engine ID khác với engine mà instance đã bind.
Mọi bước Docker/Compose bị từ chối; không có gì bị thay đổi trừ khi thông báo nêu tên một
operation và phase. Bind lại daemon là một installation transaction tường minh (PF-A2); không
sửa record.

`daemon-unreachable`: socket không tồn tại (daemon đang dừng) hoặc daemon không trả lời. Khởi
động Docker (Container Manager) rồi chạy lại; `status` vẫn in mọi mục không liên quan Docker.

`daemon-rootless` / `daemon-info-invalid`: chỉ hỗ trợ daemon local rootful có định danh dùng được.

`daemon-endpoint-*` trong trust summary: đường dẫn socket đã đăng ký không phải socket được bảo
vệ (symbolic link, owner không tin cậy, world-writable, ancestor có thể bị thay). Không có gì bị
liên lạc.

### Compose envelope bị từ chối

`envelope-*` liệt kê từng finding kèm JSON path (không bao giờ in value): option nguy hiểm hoặc
không mong đợi trong model đã resolve, image override được bảo vệ bị sửa (`envelope-override`),
hoặc render thất bại (`envelope-render-failed`: Compose exit, lớn hơn 4 MiB, JSON không hợp lệ
hoặc key trùng; Compose v1 không render được `config --format json` và không được hỗ trợ). Không
có gì được build, create hay start.

### Resource bị chặn khỏi purge hoặc không thuộc instance

`resource-blocked` (purge, abort-deploy), `resource-not-owned` (guarded command) và
`resource-target-not-empty` (deploy, restore exact) liệt kê từng resource kèm class. Review bằng
`sudo pf status`; resource legacy chưa có label (ví dụ từ bản cài v2.5), trùng tên và resource
thuộc instance khác không bao giờ được tự động adopt hay xóa (adoption thuộc PF-A2). `plan-drift`
trong purge hoặc abort nghĩa là một resource trong plan hoặc một user của nó đã thay đổi sau khi
plan được đóng băng (hoặc một item trong plan còn tồn tại nhưng không còn owned); journal giữ
nguyên plan. `inventory-unstable` nghĩa là container liên tục biến mất giữa `docker ps -a` và lệnh
inspect của nó trong ba lần thử; chạy lại khi host bớt bận (purge hoặc abort bị gián đoạn sẽ resume
đúng plan đã đóng băng).

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
| `sudo pf abort-deploy` | Xóa incomplete first deploy trước khi frontend mở (xác nhận `ABORT DEPLOY <project>`; abort bị gián đoạn tiếp tục bằng `RESUME ABORT DEPLOY <project>`) |
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
| `sudo pf release-check --apply` | Bị từ chối ở checkpoint này (exit 20): cần grant trong protected policy (PF-A4.3) |
| `sudo pf resume` | Resume chỉ khi early-failure state chưa thay đổi |
| `sudo pf ps [options] [SERVICE...]` | View container Compose read-only của instance (mục 14) |
| `sudo pf logs [options] [SERVICE...]` | Log service có giới hạn và được redact (mục 14) |

Lệnh khởi động không có terminal phải truyền `--instance <slug|uuid>`; cho tới khi có grant
PF-A4.3, mọi lệnh có lock đều bị từ chối khi không có terminal (`terminal-required`,
`policy-grant-required`).

## 18. Giới hạn validation

Offline tests đi kèm simulate Docker/PostgreSQL nhưng thực sự chạy controller logic,
filesystem/archive/checksum, permission policy, deployed-source/workspace separation,
purge/recovery và path construction. Chúng không thay thế integration rehearsal trên DSM +
Docker + PostgreSQL thật.

Giới hạn của PF-A1.3 (chỉ có bằng chứng offline; Docker-daemon gate và NAS host gate chưa chạy):

- một Docker hoặc root administrator chạy song song nằm ngoài bảo đảm (không có compare-and-delete
  nguyên tử; item và user được kiểm tra lại với daemon tin cậy và không có hoạt động song song);
- volume được tạo lại trong cùng một giây với metadata giống hệt thì không phân biệt được;
- phiên bản Compose trên NAS chưa được kiểm chứng; luật envelope được hiệu chỉnh trên một lần
  render Compose v2 thật (Docker Desktop CLI);
- JSON đã render được kiểm tra, không được dùng lại làm input `-f` khi thực thi;
- các label marker container của Compose dùng cho ownership chưa được hiệu chỉnh trên daemon thật;
- việc bao phủ image theo image ID giả định daemon không có hoạt động song song giữa `image save`
  và binding inventory.

PF-A1.4 bổ sung các giới hạn sau:

- việc phát hiện chạy không người trực dựa trên terminal (stdin không có, đã đóng hoặc không phải
  TTY); `ssh` không có `-t` được tính là không người trực;
- không thao tác không người trực nào, kể cả scheduled backup và release check, chạy trên instance
  do pf quản lý cho tới khi có grant trong protected policy của PF-A4.3;
- checkpoint này không cài wrapper của scheduler (installer PF-A2 sẽ đặt chúng);
- `install-control.sh` vẫn là installer cũ (mục 5 và 15);
- các label one-off của Compose `run` mà `fail_closed` dựa vào mới chỉ được chứng minh offline;
- không có route managed để chạy CLI của ứng dụng trong container backend.

**Đóng PF-A1 (offline).** Với PF-A1.4, mọi entry route dùng các primitive A1 (instance tường minh,
một runner, daemon binding, Compose envelope, exact inventory) và không còn route Compose catch-all;
phạm vi an toàn của PF-A1 mới chỉ được chứng minh offline. A1-T11…T14 vẫn bị chặn vì cần Docker
daemon thật (owner PF-A3.4/PF-A5.1, kể cả label one-off thật của Compose `run`) và A1-T17 cần bằng
chứng DSM ACL/SMB (owner PF-A2.3/PF-A5.1). Không finding nào được đóng toàn bộ và không có gì ở đây
là production-ready.

Trước khi dựa vào v2.5 recovery cho data quan trọng, nên chạy ít nhất một vòng disposable
staging trên NAS thật. Vòng dưới đây bắt đầu bằng `install-control` nên **cần installer PF-A2**;
trước khi có nó, hãy bắt đầu từ một protected layout và chạy tương tác
`<root>/bootstrap/pf --instance <slug> doctor → backup → update → purge → restore-instance →
verify UI/data`.

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
