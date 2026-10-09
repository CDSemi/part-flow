# PartFlow NAS Admin v2.5

> **Bản tiếng Anh là source of truth.** [English source](./SYNOLOGY_ADMIN.md).
> Baseline đồng bộ: package revision PF-A3.4 (chưa commit, trên `7b24d10`).
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
> `terminal-required`; `backup` và `release-check` phải nêu rõ instance
> (`--instance <slug|uuid>`, nếu không thì `instance-required-unattended`) và sau đó cần một grant
> trong protected policy cho loại thao tác đó, mà ở checkpoint này không policy nào cấp
> (`policy-grant-required`, exit 20; grant có từ PF-A4.3). PF-A2.3: `permissions check` và
> `permissions plan` là read-only, chạy được không cần terminal và không cần grant; `permissions apply` chỉ chạy
> với terminal (`terminal-required`). `release-check --apply` bị từ chối với
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
> (`selection-conflict`). Bundle nằm ngoài thư mục đó, hoặc link trỏ tới một bundle như vậy, không
> bao giờ được liệt kê và bị từ chối (`recovery-outside-instance`). Bundle chỉ được restore
> `deployed.json`, `last-reset.json` và `observed-tags.json` vào protected state, và manifest phải
> nêu chúng dưới dạng một danh sách (`recovery-state-file-refused`).
> Khối này **thay thế** các câu về passthrough trong khối PF-A1.2 và PF-A1.3 ở trên.

> **Checkpoint Deployment Admin PF-A2.1 (2026-10-07) — installer và các generation của control; vẫn là
> trạng thái phát triển, chưa phải bản phát hành NAS.**
> *Installation root mới.* `sudo sh ./deploy/synology/install-control.sh init --root <root>` là verb duy
> nhất của installer trong repository: nó đọc các file repository đã review như dữ liệu, hiển thị mọi
> conflict trước khi di chuyển bất cứ thứ gì, hỏi `INSTALL CONTROL <release-id>`, dựng root trong một thư
> mục anh em riêng tư có lock, smoke-check release mới bằng interpreter đã đăng ký rồi publish root bằng
> một lần rename. Nó ghi staging policy và profile `partflow-staging-legacy`, đặt các wrapper của
> scheduler vào `<root>/bootstrap/`, và chỉ tạo global launcher (mặc định `/usr/local/bin/pf`) khi path
> đó chưa tồn tại; launcher đã có (của v2.5, của root khác hay bất kỳ file nào) không bao giờ bị thay.
> *Verb đã cài.* Từ control đã cài: `pf install status | register | migrate-legacy | control | resume`.
> Mọi verb trừ `status` cần terminal; input chưa truyền được hỏi lần lượt, một preflight read-only báo
> **mọi** conflict cùng lúc (exit 1, không thay đổi gì), và plan đã đóng băng được xác nhận bằng cụm gõ tay
> (`REGISTER <slug>`, `MIGRATE <slug>`, `INSTALL CONTROL <id>`, `SELECT CONTROL <id>`, `RESUME <op>`,
> `ABANDON <op>`).
> *Journal và resume.* Mỗi operation ghi `plan.json` và `journal.json` dưới
> `<root>/install-operations/<id>/` trước effect đầu tiên và journal mọi effect trước và sau khi chạy.
> Operation bị gián đoạn vẫn mở; `pf install resume` quan sát mọi effect trên disk trước khi quyết định,
> và `pf install resume --abandon` khôi phục trạng thái trước đó khi hợp lệ. Effect gặp trạng thái mà
> installer không ghi sẽ dừng ở `needs_operator`, nêu target và bước tiếp theo; đó không bao giờ là ngõ
> cụt. Khi install operation còn mở, các route nó ảnh hưởng bị từ chối với `install-operation-pending`.
> *Generation của control.* `pf install control --source <reviewed tree>` stage một release
> content-addressed (`releases/r-<16 hex>`), smoke-check nó (kể cả cấu hình live của mọi instance), kiểm
> tra lại, rồi chuyển `bootstrap.conf` và mọi instance record như một binding set dưới registry lock và
> instance lock, verify end-to-end qua bootstrap thật và tự động khôi phục binding trước đó nếu verify thất
> bại. Luôn đúng một generation được bind; release cũ được giữ lại và có thể chọn lại bằng `--release`.
> Không restart, build, pull hay migrate application; thay đổi `compose.nas.yaml` được báo là một
> application operation riêng.
> *Default và v2.5.* Không install operation nào đổi registry default. `migrate-legacy` copy nguyên byte
> các file `.env`/`pf-config.json` chỉ có ở vị trí cũ vào `config/` (bản cũ vẫn giữ), đăng ký instance với
> các path của người dùng và để `control/` v2.5 cùng launcher của nó tiếp tục là control plane đang hoạt
> động: lệnh mutating của pf trên instance đó vẫn bị từ chối (`legacy-control-active`) cho tới khi có legacy
> adoption (OD-A21-05); `status`, `doctor`, `ps` và `logs` vẫn chạy.
> Khối này **thay thế** các cảnh báo về `install-control.sh` trong khối PF-A1.4 và ở mục 5 và 15.

> **Checkpoint Deployment Admin PF-A2.2 (2026-10-07) — config wizard và schema migration của admin config; vẫn
> là trạng thái phát triển, chưa phải bản phát hành NAS.**
> *Admin configuration có version.* `pf-config.json` có hai dạng đọc được: legacy schema 1 (không có
> `schema_version`; key bị bỏ qua nhận implicit value đã đóng băng) và schema 2 (`"schema_version": 2` và mọi
> key đều explicit). Việc load không bao giờ migrate: `status`, `doctor`, mọi lifecycle command, preflight của
> installer và smoke của control install đọc cả hai dạng nguyên trạng. File có key trùng, key lạ, thiếu key
> (schema 2), sai kiểu hoặc version không hỗ trợ bị từ chối với problem đầu tiên.
> *`pf config admin`.* Tạo `pf-config.json` từ example đã cài, migrate file schema 1 sang schema 2 (giữ
> explicit value, ghi implicit value bằng default đã đóng băng của schema 1) hoặc hoàn thiện file schema 2.
> Wizard chỉ hỏi các group bị thiếu hoặc không chắc chắn, từ danh sách read-only các group của host (không bao
> giờ tạo group), hiển thị summary, hỏi `Write <path>? [y/N]` và ghi nguyên tử, không ghi đè lên chỉnh sửa xảy
> ra trong lúc đó. Trước registration, `pf config admin --configuration <dir> --project <project>` tạo file mà
> `pf install register` cần.
> *`pf config app`.* Tạo hoặc hoàn thiện `.env` theo profile của instance: secret hiện có được giữ nguyên
> từng byte; database password chỉ được sinh khi nó vắng mặt và instance chưa từng deploy; credential database
> không bao giờ được hỏi hoặc ghi lại sau lần deploy đầu tiên; timezone mới phải có trong zone data đã cài
> trên host. Lần `pf deploy` đầu tiên khi chưa có `.env` chạy cùng wizard này.
> *Audit.* Một lần ghi bằng `pf config admin` hoặc `pf config app` trên instance đã đăng ký lưu
> `config-change.json` trong thư mục operation (tên key và `unchanged`/`set` cho secret; không có giá trị secret
> và không có hash của `.env`). Wizard chạy bên trong lần `pf deploy` đầu tiên không ghi `config-change.json`:
> `operation.json` và snapshot `app.env` đã đóng băng của deploy operation đó là bằng chứng. Pre-registration
> mode không ghi record (chưa có instance).
> *Audit fixes (chưa commit, trên `5d102d1`).* Wizard pre-registration từ chối khi có bất kỳ instance record đã
> đăng ký nào không load được (`registry-record-invalid`), và copy `admin-config-invalid` của nó lặp lại
> `--configuration`/`--project`; chọn DSM Reverse Proxy trong `config app` sẽ hỏi lại hostname `localhost` đang
> được giữ; `SITE_TIMEZONE` hiện có nhưng không phải tên zone (có thành phần `.`/`..`) được hỏi lại thay vì giữ;
> lỗi parser của `.env` không bao giờ hiện một ký tự nào của dòng; interrupt sau khi ghi được báo là
> `config-interrupted` hoặc `config-audit-not-recorded` (file đã chứa nội dung mới); dòng hoàn tất của
> `install-control.sh init` nêu `config admin --configuration`.
> Khối này **thay thế** các bước "tạo cấu hình bằng tay" trong khối PF-A2.1 và ở mục 5.

> **Warning (PF-A2.3).** Checkpoint Deployment Admin PF-A2.3 (2026-10-07) — permission policy theo ngữ nghĩa,
> `check`/`plan`/`apply`, scope floor và apply có thể resume; vẫn là **trạng thái phát triển, chưa phải bản phát
> hành NAS**. Chỉ có bằng chứng offline: không có gì được chạy trên DSM host, SMB client hay Docker daemon thật.
> *Lệnh.* `pf permissions check` (so sánh, read-only, exit 1 khi có khác biệt), `pf permissions plan` (xem trước
> với thành viên group, số lượng và plan hash, read-only) và `pf permissions apply` (wizard đánh số, một lần xác
> nhận gõ tay `APPLY PERMISSIONS <slug>`, sau đó apply có fence, có journal và được verify; chỉ chạy với
> terminal). `pf permissions` không kèm verb không còn thay đổi gì: bị từ chối với `permissions-verb-required`
> (exit 2). `check` và `plan` không lấy lock và không ghi gì ở bất kỳ đâu.
> *Approval.* Một lần `apply` đã xác nhận ghi **permission policy revision** N vào
> `<root>/instances/<uuid>/permission-policy.json` (root, `0600`, được `purge` giữ lại). Trước approval đầu tiên
> instance dùng derived policy: backup và recovery bundle giữ group của thư mục chứa chúng, workspace và
> configuration dùng `workspace_write_group`. `backup_read_group` là đề xuất cho mọi instance,
> `workspace_write_group` là đề xuất khi đã có revision; chỉ sửa `pf-config.json` không còn thay đổi ai được đọc
> backup hay recovery bundle.
> *Flow.* Không lifecycle command nào còn duyệt một cây editable: `deploy --current` không đổi permission của
> workspace, còn backup, recovery bundle, source được thay hoặc restore, `.env` được restore và state file được
> restore là bản copy chỉ có nội dung, nhận target policy tường minh và được verify.
> Khối này **thay thế** các bước `sudo pf permissions` ở mục 2 và 16 và ghi chú `backup_read_group` của khối
> PF-A2.2 và mục 6.

> **Warning (PF-A3.1).** Checkpoint Deployment Admin PF-A3.1 (2026-10-07) — deployed-source artifact bất biến,
> lifecycle wire schema chặt và emergency preservation; vẫn là **trạng thái phát triển, chưa phải bản phát hành
> NAS**. Chỉ có bằng chứng offline và filesystem: Docker và PostgreSQL được mô phỏng, không có gì được chạy trên DSM
> host, Docker daemon hay PostgreSQL server thật.
> *Deployment record.* Mỗi lần `deploy`, `update`, `rollback` và `restore-instance` thành công giờ seal một
> **deployment record** trong `<root>/instances/<uuid>/artifacts/deployments/<deployment-id>/` (chỉ root,
> `0700`/`0600`): source archive đúng như đã deploy và manifest của nó, Compose model đã resolve, `.env` đã freeze
> (có secret) và chính record. `deployed.json` trỏ tới nó. Record còn nguyên khi checkout đổi, khi mất `repo/.git`
> và sau `purge`.
> *Bundle.* Checkpoint và purge recovery bundle mới được ghi theo manifest `schema_version` 1
> (`contracts/lifecycle-records.schema.json`) và được đọc chặt: đúng byte của manifest, size và hash của từng
> payload, không link, không file ngoài danh sách, trước khi extract, xác nhận hay ghi journal bất cứ gì. Bundle cũ
> format 1 và 2 vẫn dùng được qua một migration tường minh, tất định, chỉ trong bộ nhớ; không có gì trên đĩa bị ghi lại.
> File mà bundle như vậy không liệt kê chỉ được ghi nhận và không bao giờ được mở: `restore-instance` chỉ lấy `.env`
> runtime và mọi state file từ payload đã verify (`.env` của format 1 lấy từ source archive đã verify).
> *Class và level.* Một capture là **healthy checkpoint**, **emergency preservation** hoặc **partial**; verification
> level (`captured`, `failed`, `data-restore`) lấy từ verification record riêng trong `artifacts/verifications/`.
> Chỉ healthy checkpoint mới là rollback target.
> *Emergency.* `pf backup --emergency` (chỉ với terminal, `EMERGENCY BACKUP <project>`) giữ lại data thực tế khi
> healthy checkpoint bị từ chối, và mọi `rollback` giờ preserve database hiện tại trước và từ chối
> (`preservation-failed`) khi không làm được.
> *Downgrade.* Quay về control PF-A2.3 là **không được hỗ trợ** khi đã có bundle schema 1 (listing, chọn rollback và
> purge của bản cũ lỗi trên manifest mới).
> Khối này **thay thế** nội dung checkpoint ở mục 10 và dòng `Deployed source` ở mục 8.

> **Warning (PF-A3.2).** Checkpoint Deployment Admin PF-A3.2 (2026-10-07) — operation journal, `resume` và
> `--abandon`, workspace generation switch; vẫn là **trạng thái phát triển, chưa phải bản phát hành NAS**. Chỉ có bằng
> chứng offline, filesystem và installed CLI với Docker daemon giả: không dùng DSM host, Docker daemon, PostgreSQL
> server hay SMB client thật.
> *Journal.* Mỗi `deploy`, `update`, `rollback`, `reset-db`, `backup`, `purge`, `restore-instance` và `abort-deploy`
> ghi một plan đóng băng và một journal trong `<root>/instances/<uuid>/operations/<operation-id>/` (`plan.json` ghi một
> lần, sau đó là các generation `journal.json` có fsync; `attempts.json`, `children.json`, `deletion-progress.json`,
> `admin-config.json` và `app.env` đã đóng băng). `state/pending.json` cũ chỉ còn dùng cho `pf permissions apply`;
> mọi `pending.json` khác bị từ chối (`journal-format-unsupported`). Mỗi effect được ghi journal là "dự định" trước
> khi bắt đầu và chỉ "complete" sau khi kết quả đã được quan sát.
> *Resume.* `pf resume [--operation ID] [--abandon | --keep-workspace]` vào lại operation đang mở: kiểm tra lại các
> authority đã được duyệt, từ chối khi một child đã ghi nhận, một one-off container của instance hoặc một database
> session của operation vẫn chạy (`effect-still-running`; không bao giờ dừng chúng), quan sát effect chưa rõ kết quả,
> rồi hoặc mở lại deployment không đổi (chưa có effect nào về data hay source), hoặc tiếp tục về phía trước, hoặc dừng ở
> `needs_operator` khi chạy lại có thể lặp một thay đổi. `purge`, `restore-instance <cùng bundle>`, `abort-deploy` và
> `backup` vào lại operation đang mở của chính chúng theo cùng cách.
> *Workspace.* `repo/` không còn bị thay thế tại chỗ: sau activation, cây đã deploy được stage bên cạnh và đổi bằng hai
> lần rename; cây cũ được giữ, không bao giờ xóa, thành retained generation trong
> `<thư mục cha của workspace>/.pf-generations-<instance-uuid>/`.
> *Chẩn đoán.* `status`, `doctor` và `instances` hiển thị operation đang mở từ các file được bảo vệ trước mọi thứ khác;
> `pf status --operation ID` hiển thị chi tiết một operation.
> *Downgrade.* Chỉ có thể quay về control PF-A3.1 khi không có operation nào đang mở; khi đó mất phần hiển thị journal
> và generation switch, và runner record đã được journal reconcile lại bị coi là chưa giải quyết.
> Khối này **thay thế** câu "PF-A3.2 dọn nó" của A3.1 (mục 7) và việc thay workspace tại chỗ ở mục 8 và 9.

> **Warning (PF-A3.3).** Checkpoint Deployment Admin PF-A3.3 (2026-10-08) — integrated operations: functional
> verification đúng bundle trong một isolated topology, side-by-side recovery target, bảo vệ dữ liệu hiện tại trong
> `reset-db`/`rollback`/`abort-deploy`, mô hình capacity, `pf cleanup` và acknowledgement runner record; vẫn là
> **trạng thái phát triển, chưa phải bản phát hành NAS**. Functional verification và isolated topology chỉ được chứng
> minh **offline với Docker daemon giả**: chưa quan sát network, port, egress Docker hay PostgreSQL thật, và tính cách
> ly chỉ được chứng minh từ cấu hình daemon báo về (PF-A3.4).
> *Instance purge.* Trước mọi thao tác xóa, đúng purge bundle được restore vào một Compose project tạm
> `pfverify-<12 hex>` (volume riêng, internal network, không publish port, database password sinh mới) và được kiểm tra
> functional; deletion gate yêu cầu record `functional_recovery_verified` đó. Sau khi xóa, registry record được đánh dấu
> `state: purged` và nhả project claim (mục 12).
> *Recovery.* `restore-instance` chỉ nhận bundle của chính instance (`restore-target-mismatch`), và `--side-by-side` giờ
> tạo một recovery target `pfrecover-<12 hex>` được giữ lại và cách ly, thay cho database `pf_recovery_*`.
> `pf cleanup` báo cáo và xóa các phần thừa đã được ghi nhận; `pf resume --operation <op> --acknowledge` đóng các runner
> record của operation không có journal (mục 15 và 16).

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

Từ PF-A2.3, permission của một instance đã đăng ký tuân theo một **permission policy theo ngữ nghĩa**. Không có
gì hỏi hay chấp nhận mode octal, `+x-r` hay một dạng số thứ hai: mode là output, chỉ hiển thị khi yêu cầu. Các
dòng v2.5 cho `control/` và `.pf-state-*` không còn áp dụng: control release nằm ở `<root>/releases/<id>/`, private
state nằm ở `<root>/instances/<uuid>/`.

### Lựa chọn và scope

Mỗi scope có group nhận một trong ba lựa chọn:

| Lựa chọn | Giá trị JSON | Ý nghĩa với thành viên group |
| --- | --- | --- |
| Xem và chỉnh sửa | `read_write` | đọc, tạo, sửa, đổi tên và xóa |
| Xem và sao chép | `read_only` | đọc và copy |
| Không truy cập qua group | `none` | không có quyền; chỉ owner |

| Scope | Path | Có thể chọn | Floor (không lựa chọn nào đổi được) |
| --- | --- | --- | --- |
| Workspace | `workspace` đã đăng ký | group, mọi lựa chọn, kế thừa group, chạy script `Owner only` hoặc `Owner and group` | giữ owner hiện có của người sửa; bit execute chỉ cho file mà manifest của source đã deploy đánh dấu executable |
| Configuration | `configuration` đã đăng ký | group, mọi lựa chọn, kế thừa group | không có lựa chọn executable |
| Control release | `<root>/releases/<id>/` | cố định: Không truy cập qua group | được kiểm tra, `apply` không bao giờ đổi: owner uid 0, không có group/other write, không có special bit |
| Backups | `backups` đã đăng ký | group, Xem và sao chép hoặc Không truy cập qua group | owner uid 0, không group write, không owner được import |
| Recovery bundles | `recovery` đã đăng ký | group, Xem và sao chép hoặc Không truy cập qua group | như backups |
| Private state | `<root>/instances/<uuid>/` | cố định: chỉ owner | directory `0700`, file `0600`, không có group |

Bảng mode (chi tiết audit; owner luôn giữ đọc/ghi, `other` không bao giờ có bit nào, setgid chỉ có trên directory
khi kế thừa group, setuid và sticky không bao giờ có):

| Access | File | Directory | Directory kế thừa group |
| --- | --- | --- | --- |
| Không truy cập qua group | `0600` | `0700` | `2700` |
| Xem và sao chép | `0640` | `0750` | `2750` |
| Xem và chỉnh sửa | `0660` | `0770` | `2770` |

File mà source manifest được bảo vệ đánh dấu executable nhận file mode cộng owner execute (`Owner only`) hoặc
owner và group execute (`Owner and group`). Không quy tắc nào nhìn vào phần mở rộng file, và không gì dưới `.git`,
`node_modules` hay tên bị loại trừ khác được chỉ định executable. "No script execution" không được đề nghị cho
workspace: file được đánh dấu executable trong source đã deploy giữ bit owner execute để workspace vẫn khớp
source manifest (`permission-policy-unsupported` nếu policy như vậy được gửi vào).

### Permission policy revision, derived policy và đề xuất

"Permission policy revision N" là permission policy đã được duyệt của một instance. Nó không liên quan tới
"approved policy revision" của environment policy (`staging` revision 1), thứ mà không lệnh permission nào thay
đổi.

- **Đã duyệt.** Một lần `pf permissions apply` đã xác nhận và verify mọi scope được chọn ghi revision N+1 vào
  `<root>/instances/<uuid>/permission-policy.json` (root, `0600`). Một bản copy từng byte nằm trong thư mục
  operation của lần apply đó, nên chuỗi revision có thể lần ngược về revision 1. `purge` giữ record; instance
  được đăng ký mới bắt đầu không có record (record không nằm trong recovery bundle).
- **Derived (chưa duyệt).** Workspace và configuration: `workspace_write_group`, Xem và chỉnh sửa, kế thừa group,
  chạy script `Owner and group` (các mode A1/A2.2 `2770`/`0660`/`0770`). Control: Không truy cập qua group.
  Backups và recovery: Xem và sao chép cho group đang sở hữu thư mục backups và recovery. Chỉ root mới đổi được
  group đó (`validate_context` yêu cầu các thư mục này thuộc root và không group-writable). Gid của thư mục không
  có tên group sẽ dừng với `permission-group-missing` cho tới khi `apply` duyệt một group có tên.
- **Đề xuất.** `backup_read_group` trong `pf-config.json` là đề xuất cho mọi instance: tự nó không bao giờ đổi
  ai được đọc backup hay recovery bundle. `workspace_write_group` là đề xuất khi đã có revision; trước approval
  đầu tiên nó vẫn đặt group cho file pf tạo trong workspace và configuration (giới hạn đã công bố). `doctor`,
  `check`, `plan` và wizard `apply` hiển thị đề xuất khác biệt (`permission-group-proposal`); nó chỉ có hiệu lực
  khi `apply` ghi một revision mới.
- Cảnh báo về group backup được hiển thị ở mọi nơi chọn group đó: thành viên group đọc backup có thể đọc nội dung
  database và mọi credential nằm trong backup hoặc recovery bundle.

### `check`, `plan` và `apply`

```sh
sudo pf --instance <slug> permissions check [--scope SCOPE]...
sudo pf --instance <slug> permissions plan  [--scope SCOPE]... [--details]
sudo pf --instance <slug> permissions apply [--scope SCOPE]... [--details]
sudo pf --instance <slug> permissions apply --resume | --abandon
```

`SCOPE` là `workspace`, `configuration`, `control`, `backups`, `recovery` hoặc `private_state` (lặp lại được;
mặc định là tất cả).

- `check` và `plan` là read-only: không lock, không thư mục operation, không ghi gì ở bất kỳ đâu. Chúng ánh xạ
  các finding của protected context vào scope: finding nằm tại, phía trên hoặc phía dưới thư mục workspace,
  configuration, backups hay recovery sẽ chặn scope đó (`scope-path-unsafe`) và các scope còn lại vẫn được báo
  (khi chưa có policy được duyệt, thư mục backups hay recovery bị thiếu hoặc không an toàn hiển thị "group
  unavailable": group của nó không được đọc từ thư mục đó); mọi finding bị từ chối khác, và finding nằm tại hoặc phía
  trên installation root (kể cả khi nó cũng nằm phía trên một thư mục scope), dừng với `permissions-context-refused`
  trước khi đọc bất kỳ thư mục nào. `check`
  exit 1 khi có entry khác biệt hoặc control release vượt ceiling (`permissions-differ`).
- `plan` in theo từng scope: policy, thành viên group (tối đa 20; thành viên primary-group và directory-service
  không được liệt kê), số lượng (group, mode, special bit), có cần freeze không, các blocker và, với
  `--details`, từng thay đổi dạng octal và symbolic. `Plan hash` là sha256 của danh sách thay đổi chính xác;
  `apply` hiển thị cùng hash cho cùng policy trên một cây không đổi. Khi `pf-config.json` có đề xuất khác biệt,
  một khối thứ hai hiển thị candidate áp dụng đề xuất đó.
- `apply` lấy instance lock, hỏi wizard đánh số (group, access, kế thừa group và, với workspace, chạy script;
  Enter giữ default đang hiển thị, `q` để hủy), lập plan, hiển thị thay đổi policy và plan hash, rồi hỏi một lần
  xác nhận gõ tay `APPLY PERMISSIONS <slug>`. Sau đó nó revalidate context, record, các group và identity của thư
  mục, ghi intent (`permission-plan.json`, `permission-changes.jsonl` và `state/pending.json`), apply và verify
  mọi scope được chọn, và chỉ sau đó mới ghi revision mới. Policy đã duyệt không đổi trên cây đã đúng là
  `permissions-current` (exit 0, không ghi gì).

Apply không bao giờ đi theo link, không vượt mount, không đổi entry hard-link, special, có ACL hoặc `@docker`,
không bao giờ chown một owner không tin cậy vào protected scope và không bao giờ đổi control release: mỗi trường
hợp là một blocker được `plan` báo, và `apply` không đổi gì khi còn blocker.

### Editor freeze

Thay đổi phía dưới root của workspace hoặc configuration (bulk change) cần một editor freeze đã được verify.
`apply` trước hết đặt root của mỗi scope như vậy thành owner-only (giữ bit setgid), nên người sửa không vào được
thư mục nữa, rồi lấy inventory chính thức của các thư mục đã fence và quét `/proc` tìm mọi process khác có
working directory, root hoặc file đang mở là một entry của plan hoặc của inventory đó (nên entry mà người sửa tạo
ra trong lúc plan đang được đọc cũng tính). Có holder thì fence được gỡ lại và lệnh bị từ chối với `editor-freeze-refused` (không thay đổi gì).
Phép quét cũng báo cả shell đã khởi động lệnh: **hãy chạy `apply` từ ngoài các thư mục scope, ví dụ `cd /`**.
Freeze không khả dụng khi không đọc được `/proc` hoặc root của scope có ACL; khi đó `plan` báo
`editor-freeze-unavailable` và bulk apply của scope đó bị chặn. Bỏ chọn scope bằng `--scope`, hoặc đổi bằng tay:
dừng sửa thư mục qua SMB, áp dụng mode của bảng trên, rồi chạy `pf permissions check`. Thay đổi chỉ ở root không
cần freeze. Lock userspace không bao giờ khóa được SMB client, và fence không chặn được root service hay file được
memory-map (giới hạn đã công bố).

### ACL và DSM

`apply` không bao giờ ghi hay xóa ACL entry. Entry có access ACL, hoặc ACL mà control này không đọc được, là
blocker (`scope-entry-acl`): đổi mode sẽ ghi lại ACL mask. Directory chỉ có default ACL được chấp nhận và được báo
trong phần hành vi file tương lai. DSM Shared Folder permission vẫn có hiệu lực và POSIX mode không vượt qua một
DSM deny: hãy kiểm tra Shared Folder permission cho từng SMB account từ share root (DSM Control Panel), không chỉ
từ SSH shell. DSM share có ACL sẽ bị chặn bulk apply cho tới khi PF-A5.1 cung cấp DSM adapter.

### Ba trạng thái

`check` và `apply` in ba trạng thái cho mỗi scope và không bao giờ gộp thành một dòng xanh: `mode_applied`
(`yes`, `partial (N differ)`, `no`, `not-selected` hoặc `check-only`), `effective_access_verified` và
`future_file_behavior_verified`. Hai trạng thái sau luôn là `not verified` ở checkpoint này: chúng chỉ được tính từ
mode bit. Setgid trên directory mang theo group, không mang quyền ghi; file tạo qua SMB theo umask của client hoặc
create mask của share, và default ACL có thể thay đổi chúng. File do pf tạo có mode tường minh và được verify sau
khi tạo.

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

Từ PF-A2.1, control plane đã cài là một protected installation root:

```text
<root>/
├── bootstrap/            # pf (launcher), pf_bootstrap.py (verifier), bootstrap.conf, tools.conf,
│                         # backup.sh, release-check.sh (scheduler wrappers)
├── releases/r-<16 hex>/  # immutable, content-addressed control releases (control-manifest.json)
├── install-operations/   # one plan.json + journal.json per install operation (kept as the audit record)
├── profiles/             # partflow-staging-legacy.json
├── policies/             # staging.json
├── registry/, locks/, instances/<uuid>/, staging/, sources/, home/
```

`install-control.sh init` là trust boundary: administrator chọn và chạy chính các byte repository đã
review. Nó đọc file candidate như dữ liệu (không follow link, giới hạn kích thước, parse như Python 3.9,
chỉ lấy literal) và chỉ chạy code mới trong smoke check sau khi xác nhận gõ tay; thứ được cài đúng là thứ
summary đã hiển thị. Mọi thay đổi sau đó chạy từ control đã cài và đã verify
(`<root>/bootstrap/pf install …`), không bao giờ từ repository.

Sau khi cài, `pf.sh` trong repository cố ý từ chối chạy operational command. Dùng:

```sh
sudo <root>/bootstrap/pf status
```

(hoặc `sudo pf status` khi `init` đã tạo global launcher cho root này), không dùng:

```sh
sudo sh ./pf.sh status
```

Application update **không** âm thầm update privileged control plane. Khi revision mới có
thay đổi `pf-admin.py`, `compose.nas.yaml` hay lifecycle source khác, hãy review revision đó rồi cài
nó một cách explicit bằng `pf install control` (mục 15).

## 5. Cài mới hoặc migrate

Mọi lệnh dưới đây có dạng `<root>/bootstrap/pf` chỉ được viết thành `sudo pf …` khi global launcher do
`init` tạo cho chính root này. Trên NAS vẫn chạy v2.5, `/usr/local/bin/pf` là launcher v2.5 (hoặc của root
khác) và không tới được root mới.

### (a) Cài mới

1. Từ một repository checkout đã review, khởi tạo root mới (chưa tồn tại hoặc rỗng):

   ```sh
   sudo sh ./deploy/synology/install-control.sh init --root <root>
   ```

   Input còn thiếu sẽ được hỏi; `--interpreter`, `--tool <id>=<path>`, `--launcher-path <path>` và
   `--no-launcher` thay các giá trị mặc định đã phát hiện (hiển thị trong summary). Xác nhận bằng
   `INSTALL CONTROL <release-id>`. Mọi verb khác của `install-control.sh` bị từ chối với
   `installer-verb-installed-only`. Root rỗng đã tồn tại phải nằm trên cùng filesystem với thư mục cha:
   một mount point hoặc chính một DSM shared folder bị từ chối (`root-exists`), vì root được publish bằng
   một lần rename nguyên tử của thư mục build bên cạnh; hãy chỉ định một đường dẫn chưa tồn tại bên dưới
   nó. Nếu bước publish thất bại hoặc bị gián đoạn thì chưa có gì được publish: thư mục build bị xóa và bạn
   chạy lại đúng lệnh `install-control.sh init` (không có `resume` cho một root chưa tồn tại).
2. Tạo admin configuration bằng pre-registration wizard (thư mục phải có sẵn, thuộc root, nằm trong các
   ancestor được bảo vệ; chưa có gì được đăng ký):

   ```sh
   sudo <root>/bootstrap/pf config admin --configuration <config> --project <project>
   ```

   Wizard hỏi workspace group và backup group từ các group đang có trên host, hiển thị summary và ghi
   `<config>/pf-config.json` (schema 2, `root:<workspace group>`, `0660`) sau khi trả lời `y`. Wizard từ chối
   một thư mục đang được install operation mở, pending registration hoặc instance đã đăng ký sử dụng
   (`install-operation-pending`, `registry-pending`, `config-path-conflict`), và từ chối mọi thư mục khi một
   instance record đã đăng ký không load được (`registry-record-invalid`). File legacy schema 1 đã có được giữ
   nguyên (`admin-config-legacy-unregistered`). Dòng hoàn tất của `init` nêu đúng lệnh này.
3. Đăng ký instance với các thư mục đã có:

   ```sh
   sudo <root>/bootstrap/pf install register --slug <slug> --project <project> --workspace <checkout> \
     --configuration <config> --backups <backups> --recovery <recovery>
   ```

   Preflight kiểm tra các path (không tạo gì), admin configuration và group của nó, managed-path inventory
   và Docker daemon (một lần `docker info` read-only). Xác nhận bằng `REGISTER <slug>`. Registry default
   không bao giờ bị đổi.
4. Tạo `.env` bằng `sudo <root>/bootstrap/pf --instance <slug> config app`, hoặc để lần
   `pf --instance <slug> deploy` đầu tiên hỏi đúng các câu hỏi đó.

### (b) Home v2.5

```sh
sudo <root>/bootstrap/pf install migrate-legacy --legacy-home <home> --workspace <checkout> --slug <slug>
```

- `configuration`, `backups` và `recovery` là `<home>/config`, `<home>/backups` và `<home>/recovery`;
  workspace là checkout bạn chỉ định, tên gì cũng được; project lấy từ `pf-config.json` đang có hiệu lực.
- Conflict không bao giờ được tự giải quyết: khi `<checkout>/.env` và `<home>/config/.env` (hoặc
  `<checkout>/deploy/synology/pf-config.json` và `<home>/config/pf-config.json`) cùng tồn tại và khác nhau,
  preflight từ chối và nêu cả hai. File chỉ có ở vị trí cũ được copy nguyên byte (giữ mode và group của
  nguồn) vào một tên staged riêng tư, được validate, rồi publish mà không ghi đè; bản cũ vẫn ở nguyên chỗ
  (v2.5 vẫn đọc nó).
- Operation lock của v2.5 `<home>/.pf-state-<project>/operation.lock` được giữ trong suốt operation;
  `pending.json` của v2.5 làm migration bị từ chối (hoàn tất hoặc xử lý nó bằng v2.5 trước). Các state file
  khác của v2.5 được báo cáo, không được import.
- Được migrate: các bản copy cấu hình và registration. Không được migrate: state v2.5, Docker resource
  (không adoption), quyền truy cập, thư mục `control/` v2.5 và launcher v2.5.
- v2.5 vẫn là control plane cho mọi thay đổi của instance; pf chỉ cho view read-only (`status`, `doctor`,
  `ps`, `logs`) và từ chối mutation với `legacy-control-active` cho tới khi có legacy adoption.
- Instance đã migrate giữ `pf-config.json` schema 1 (v2.5 đọc cùng file đó và sẽ từ chối `schema_version`).
  `pf config` bị từ chối như mọi route mutating (`legacy-control-active`) cho tới khi có adoption, và với
  `pf config app` instance được tính là đã deploy (database của nó đã tồn tại).

> **Cảnh báo (PF-A2.1).** Đây là checkpoint phát triển. Không migrate NAS v2.5 đang chạy trước khi có
> sub-slice adoption của PF-A2 (OD-A21-05) và quyết định về grant không người trực (OD-A14-13). DSM
> shared-folder ACL và `backups/` group-writable bị preflight từ chối cho tới PF-A2.3/PF-A5 (A1-T17).

> **Cảnh báo (PF-A2.2).** Các config wizard là checkpoint phát triển, mới chỉ được chứng minh offline. File cấu
> hình có ACL entry (DSM shared folder thường có) bị từ chối (`config-file-acl`) và phải sửa bằng tay cho tới
> PF-A2.3. Mật khẩu cần URL encoding vẫn làm `deploy` và `update` dừng ở bước database migration (mục 6, `.env`).

## 6. Configuration files

### `config/pf-config.json`

Đây là NAS-local administration config thực sự được dùng. `pf config admin` tạo, migrate hoặc hoàn thiện
nó (mục 5 (a) trước registration, `sudo pf --instance <slug> config admin` sau đó); `pf install migrate-legacy`
chỉ copy nguyên byte một file cũ đã có.

Template schema 2 (`pf-config.example.json` đã cài, input bất biến của wizard):

```json
{
  "schema_version": 2,
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

- **Hai schema.** File không có `schema_version` là legacy schema 1: mọi key bị bỏ qua nhận giá trị schema 1
  đã đóng băng (giá trị của template ở trên), và file vẫn dùng được ở mọi nơi. Schema 2 liệt kê đủ mọi key;
  thiếu key, key lạ, key trùng hoặc sai kiểu đều bị từ chối. `branch` phải là tên branch (không có `..`,
  `//` hoặc `/` ở cuối), `ci_workflow` là tên file workflow, các group là tên không có ký tự điều khiển, `:`
  hoặc khoảng trắng ở hai đầu. Mọi `schema_version` khác (kể cả `1` viết explicit) là
  `admin-config-version-unsupported`.
- **Migration là explicit.** Chỉ `sudo pf --instance <slug> config admin` chuyển schema 1 sang schema 2:
  explicit value được giữ và implicit value được ghi bằng giá trị schema 1 đã đóng băng, không bao giờ bằng
  example hiện tại. Giá trị mà schema 2 từ chối sẽ chặn migration (`admin-config-migration-blocked`) và không
  bao giờ được sửa tự động. Cả `pf install control` lẫn `migrate-legacy` đều không đổi file. Thư mục chưa đăng
  ký (có thể là `config/` của home v2.5) và instance v2.5 còn active không bao giờ bị migrate.
- **Câu hỏi.** Wizard chỉ hỏi `workspace_write_group` và `backup_read_group`: hỏi cả hai khi tạo file, còn lại
  chỉ hỏi group không tồn tại trên host này (không có default; không bao giờ âm thầm chọn một group rộng hơn).
  Câu trả lời là số thứ tự trong danh sách group của host (`users`, rồi các group có gid từ 1000) hoặc đúng tên
  của một group đang tồn tại. Không bao giờ tạo group. `branch`, `ci_workflow`, `release_channel`,
  `auto_update`, `health_timeout_seconds` và `minimum_free_mb` được hiển thị nhưng không hỏi; hãy đổi bằng tay.
- **Environment label.** `environment` phải bằng environment của approved policy của instance. Đó chỉ là
  label: đổi nó không bao giờ đổi policy (`admin-config-mismatch`); đổi policy là một approval riêng (PF-A4.3).
  `project` phải bằng Compose project đã đăng ký.
- **Group là đề xuất (PF-A2.3).** `backup_read_group` luôn là đề xuất: backup và recovery bundle giữ group của
  thư mục chứa chúng, hoặc group của permission policy revision N, cho tới khi `pf permissions apply` duyệt thay
  đổi. `workspace_write_group` trở thành đề xuất khi đã có permission policy revision; trước đó nó vẫn đặt group
  cho file pf tạo trong workspace và configuration. Summary của wizard nêu rõ trường hợp nào đang áp dụng.
- **Ghi file.** File được thay qua một file tạm riêng `.pf-config.json.pf-config-<8 hex>` (với `.env`:
  `.env.pf-config-<8 hex>`) trong cùng thư mục, giữ owner, group và mode của file bị thay; file mới của instance
  đã đăng ký nhận configuration target của permission policy đang hiệu lực (owner root, group của configuration,
  `0660`, `0640` hoặc `0600`) và được kiểm tra sau khi tạo; trước registration nó là `root:<workspace group>`
  `0660`. Nếu file đổi sau summary thì không ghi gì (`config-changed`). Các tên tạm này
  được dành riêng: phần còn sót của một lần chạy bị gián đoạn được lần chạy sau xóa, còn file trùng dạng tên đó
  mà không phải phần còn sót thì bị từ chối (`config-file-unsafe`), không bao giờ bị xóa.
- **Downgrade.** Control release cũ hơn PF-A2.2 từ chối `schema_version`. Chọn release như vậy bằng
  `pf install control --release` sau khi đã migrate sẽ dừng ở smoke check (`install-smoke-failed`), và không có
  gì được bind.

`auto_update` chỉ là đề xuất: apply không người trực cần một grant trong protected policy, mà
checkpoint này không cung cấp (mục 13).

### `config/.env`

`sudo pf --instance <slug> config app` tạo hoặc hoàn thiện file này theo profile của instance, và lần `deploy`
đầu tiên khi chưa có `.env` chạy cùng wizard này (bên trong `deploy` không ghi `config-change.json`;
`operation.json` và snapshot `app.env` đã đóng băng của deploy operation là bằng chứng). File mới bắt đầu từ `nas.env.example` đã cài (giữ comment);
nếu không thì chỉ các dòng thay đổi được ghi lại và mọi dòng khác giữ nguyên từng byte. Wizard cần
`pf-config.json` hợp lệ (`admin-config-required`). Wizard chỉ hỏi giá trị thiếu hoặc không hợp lệ, theo thứ tự:
PostgreSQL user, PostgreSQL database, factory timezone, access mode (và địa chỉ LAN), HTTP port, và hostname
của Reverse Proxy (chỉ hỏi khi bind `127.0.0.1`, không có default; Direct LAN dùng `localhost`). Khi access mode
được hỏi và bạn chọn DSM Reverse Proxy, `PARTFLOW_ALLOWED_HOST=localhost` đang được giữ sẽ được hỏi lại, vì
proxy cần đúng hostname nội bộ.

- **Secret.** `POSTGRES_PASSWORD` hiện có không bao giờ được hiển thị, đổi, sinh lại hoặc quote lại (summary:
  `unchanged`). Password thiếu hoặc rỗng được sinh thành 64 ký tự hex bằng secure randomness (summary: `set`)
  chỉ khi instance chưa từng deploy.
- **Instance đã deploy.** Instance được tính là đã deploy khi có `state/deployed.json`, khi một v2.5 migration
  đã hoàn tất gắn với nó, hoặc khi state của nó không đọc được. Khi đó `POSTGRES_USER`, `POSTGRES_PASSWORD` và
  `POSTGRES_DB` không bao giờ được hỏi, sinh hoặc ghi lại; giá trị thiếu hoặc không dùng được bị từ chối
  (`app-credential-unusable`) kèm hướng dẫn khôi phục. Full `purge` xóa state, nên instance đã purge lại là
  instance mới; instance đã migrate rồi purge vẫn được tính là đã deploy.
- **Độ dài password.** Password được giữ nhưng ngắn hơn 32 ký tự sẽ được báo
  (`password-weak-for-new-deployment`): lần `deploy` đầu tiên sẽ từ chối nó. Hãy đặt password dài hơn bằng
  tay, hoặc để dòng thành `POSTGRES_PASSWORD=` (rỗng) rồi chạy lại `config app` để sinh password.
- **Timezone.** `SITE_TIMEZONE` mới hoặc đang thiếu phải có trong zone data đã cài trên host. Giá trị hiện có mà
  host không biết được giữ kèm note (`zone-unknown-on-host`); giá trị hiện có nhưng hoàn toàn không phải tên
  zone (có thành phần `.` hoặc `..`) được hỏi lại như giá trị không hợp lệ; khi host không có zone data, giá trị
  hiện có được giữ kèm note còn giá trị thiếu bị từ chối (`zone-data-unavailable`). Backend kiểm tra giá trị
  bằng zone data của chính image khi khởi động; wizard không kiểm tra zone data đó.
- **ACL.** `.env` hoặc `pf-config.json` có ACL entry bị từ chối trước câu hỏi đầu tiên (`config-file-acl`):
  thay file sẽ làm mất ACL. Hãy sửa file như vậy bằng tay; thay file mà vẫn giữ ACL thuộc PF-A5.1. `.env` được tạo
  nhận configuration target của permission policy đang hiệu lực; khi lần `deploy` đầu tiên dùng lại `.env` có sẵn,
  chỉ file đó nhận cùng target.
- **Database URL.** Controller truyền `PARTFLOW_DATABASE_URL` với credential đã percent-encode; không có gì bị
  ghép thô. Giới hạn (cho tới khi bản sửa app-lane của `backend/alembic/env.py`, P16-S3 hoặc sau đó, được
  deploy): bước migration của backend đưa URL đó vào config parser của Alembic, vốn từ chối `%`. Vì vậy password
  cần encoding (bất kỳ ký tự nào ngoài chữ, số và `-._~`) làm `deploy` và `update` dừng ở bước migration, trước
  khi version mới được kích hoạt. Password được sinh không bị ảnh hưởng; password hiện có không bao giờ bị đổi
  để né lỗi này.

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
một credential/database migration có kế hoạch; không wizard nào rotate credential.

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
2. Tạo hoặc reuse `config/.env` (với configuration permission target, PF-A2.3).
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

Từ PF-A3.1, trước bước 7 có kiểm tra dung lượng của deployment artifact store (`artifact-capacity`: cần trống kích
thước source cộng 64 MiB trong `<root>/instances/<uuid>/artifacts/deployments`), và ngay sau bước 7, trước mọi thay
đổi, deployment được **stage**: cây source chính xác được archive cùng manifest vào thư mục private `.staging-<id>`.
Stage lỗi (`deployment-stage-failed`) không đổi application, database hay workspace. Sau khi health check pass,
staging được **seal**: thêm identity của database image, Compose model đã resolve và `.env` đã freeze, thư mục được
đổi tên thành deployment ID và `deployed.json` có thêm `deployment_id` và `deployment_record_sha256`. Record chứa
secret và chỉ root đọc được; nó không bao giờ nằm trong checkpoint (checkpoint tham chiếu nó bằng ID và hash) và còn
nguyên khi checkout đổi, khi mất `repo/.git` và sau `purge`.

Seal lỗi sau một lần activate healthy không dừng application: operation được đóng, `deployed.json` ghi
`deployment_seal_failed` thay cho record, lệnh exit 1 với `deployment-record-incomplete`, và `status` báo
`Deployment: not recorded (…)`. Lần `deploy`, `update`, `rollback` hay `restore-instance` kế tiếp sẽ seal record.
Seal không được thử lại trong cùng operation: nếu workspace refresh của nó còn đang chờ, lần `resume` sau hoàn tất
refresh rồi đóng operation ở `failed_preserved` với cùng exit 1 và cùng thông báo. Thư
mục staging còn lại sau một lần chạy bị ngắt được `status` báo (`Unsealed deployment staging: N`) và không bao giờ
được dùng; PF-A3.2 sẽ dọn.

Từ PF-A2.3, cây source mới được copy chỉ phần nội dung và chỉ các tên mà `deploy` đã copy nhận target của
workspace. Việc copy đi qua các handle thư mục được giữ và không follow link ở cả hai phía: một entry nguồn bị tráo
thành link hay file khác, hoặc một thư mục mới bị người sửa tráo thành link trong lúc đang được ghi, sẽ làm việc copy
dừng trước khi đọc hay ghi bất cứ gì qua nó; `deploy` không có selector (`--current`) không đổi permission nào của workspace và in
`Workspace permissions were not changed; check them with '<pf> permissions check --scope workspace'.`

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

Từ PF-A3.2, deploy ghi plan và journal ngay sau `DEPLOY <SHA12>` (mục 16, Lifecycle operation dang dở). Các effect
chạy theo thứ tự: staging, khởi động database, initial migration, backend, frontend, seal, `deployed.json`, rồi
**workspace refresh** (mục 8). Deploy bị gián đoạn được tiếp tục bằng `sudo pf --instance <slug> resume`;
`abort-deploy` chỉ hợp lệ cho tới khi effect frontend bắt đầu (sau đó bị từ chối với `operation-open` và `resume` hoàn
tất deploy). Khi kết quả của initial migration bị mất, operation dừng ở `needs_operator`; `abort-deploy` khi đó
supersede deploy và chỉ xóa resource đã đóng băng của chính nó, sau đó `deploy` có thể chạy lại. Staging riêng của
một operation bị `cancelled` được xóa (`note: staging-removed`); thư mục `.staging-*` không còn được tham chiếu sẽ bị
xóa ở lần staging kế tiếp, trừ staging của deployment đang chạy mà seal thất bại (`note: unsealed-active-staging`).
Staging của một operation bị supersede được giữ khi recovery supersede nó còn mở, và bị xóa ở lần staging kế tiếp
khi recovery đó (hoặc recovery cuối cùng của một chuỗi) đã hoàn tất.

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
Deployment: <deployment-id> (git_commit <sha12>|unknown), sealed <stamp>
```

Từ PF-A3.1, deployed source lấy từ **deployment record** (mục 7), không từ `repo/` hay `.git` của nó.
`Deployed source:` chỉ in commit khi protected source store đã chứng minh được; nếu không nó in
`unknown provenance (…)` và không bao giờ in commit chỉ được claim. `deployed.json` `sha` cũng chỉ được ghi cho commit
đã chứng minh (ngược lại là `null`); một `sha` từ trước A3.1 do `rollback:` hay `restore:` ghi chỉ được tính là
deployed commit khi protected source manifest ghi đúng commit đó. `pf release-check` in cùng dòng `Deployed source:`.
Dòng `Deployment:` là một trong:

```text
Deployment: <id> (git_commit <sha12>|unknown), sealed <stamp>
Deployment: legacy (no deployment record; created before PF-A3.1)
Deployment: not recorded (seal failed in operation <op>; the next deploy/update/rollback seals one)
Deployment: <id> deployment-artifact-mismatch: <file>: <detail>
```

kèm `Unsealed deployment staging: N` và `Unreferenced deployments: N` khi khác 0. Deployment có file khác với record
của nó được giữ làm bằng chứng và không bao giờ được dùng làm source (mục 16).

Không lifecycle command nào đổi permission của file workspace có sẵn (PF-A2.3); `pf permissions check
--scope workspace` báo chúng và `pf permissions apply` đổi chúng dưới một editor freeze.

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

**Workspace generation switch (PF-A3.2).** `deploy`, `update`, `rollback` và `restore-instance` không còn chép cây mới
vào `repo/`. Sau activation, seal và `deployed.json`, chúng:

1. stage cây đã deploy thành `stage-<generation>` trong generation container
   `<thư mục cha của workspace>/.pf-generations-<instance-uuid>/` (root, `0700`, cùng filesystem với `repo/`),
2. rename `repo/` thành retained generation `<container>/<generation>` (`wsg-<stamp>-<8 hex>`),
3. rename stage thành `repo/`,
4. ghi lại protected source manifest của workspace mới.

Cây cũ được **giữ** làm retained generation chưa seal; không có gì bên trong bị xóa, kể cả file untracked và `.git`
của nó. File mà editor đang mở, hoặc lưu trong lúc switch chạy, sẽ nằm trong retained generation, không nằm trong
`repo/` mới. Chép công việc như vậy về bằng root, ví dụ
`sudo cp -a /volume1/partflow/.pf-generations-<uuid>/<generation>/<path> /volume1/partflow/repo/<path>`; công cụ không
bao giờ chạy Git hay chương trình nào khác bên trong retained generation, nên hook `.git` hay thiết lập `fsmonitor`
của nó không có tác dụng. `pf status` đếm các retained generation (`Workspace generations: N unsealed in <container>
(latest <generation> from operation <op>)`) và báo `stage-*` còn sót là `in progress` hoặc `superseded-stage`;
retention thuộc PF-A5.1.

Giữa hai lần rename, `repo/` vắng mặt trong chốc lát. Nếu process bị ngắt đúng lúc đó, mọi lệnh trừ `resume` đều bị từ
chối (đường dẫn đã đăng ký bị thiếu) và `status` in `workspace: the registered workspace path may be absent between
the two renames of the workspace switch; only 'resume' (or 'resume --keep-workspace') may continue`.
`sudo pf --instance <slug> resume` bind cây đã stage; `resume --keep-workspace` thì rename cây cũ trở lại.

Switch không khả dụng — khi đó operation hoàn tất activation, seal và pointer, thoát 1 với `workspace-sync-pending`
và vẫn mở ở `workspace_sync_pending` trong khi application vẫn chạy — khi `repo/` là mount point, subvolume hoặc gốc
shared folder (`workspace-is-mount-point`), container không an toàn hoặc trùng một đường dẫn đã đăng ký
(`generation-container-unsafe`, `generation-container-collision`), chính `repo/` mang ACL (`workspace-root-acl`) hoặc
thiết bị không đủ chỗ cho cây stage cộng `minimum_free_mb` (`workspace-capacity`). Sửa nguyên nhân rồi chạy `resume`,
hoặc chạy `resume --keep-workspace` để giữ workspace hiện tại (manifest và provenance để nguyên như đã quan sát). Vẫn
có thể chạy `pf backup` thủ công khi switch đang chờ và chưa bắt đầu. Khi backup đó còn mở (bị gián đoạn),
operation đang chờ bị giữ lại: `status` thêm dòng `waits: backup operation <op> is open; run 'pf --instance <slug>
resume --operation <op>' (or add --abandon) first, then this operation's routes apply`, và `resume --operation
<switch-op>` (kể cả với `--keep-workspace`) bị từ chối với `operation-open`, nêu `resume --operation <backup-op>` của
backup và `--abandon` của nó. Khi backup đã đóng, `resume` hoặc `resume --keep-workspace` của operation đang chờ lại
có hiệu lực. `deploy`, `update`, `rollback` và
`restore-instance` cũng nhận `--keep-workspace` để bỏ qua refresh ngay từ đầu; bản tóm tắt xác nhận hiển thị lựa chọn
này cùng số lượng và dung lượng retained generation.

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

Từ PF-A3.1, trước confirmation `UPDATE`, update kiểm tra backend/frontend image đang chạy đúng là của deployment
record hiện tại (`deployment-image-mismatch`) và deployment artifact store còn chỗ (`artifact-capacity`); nó stage
deployment mới ngay sau confirmation, trước khi dừng application write, và seal sau health check (mục 7). Commit hiện
tại được đọc từ deployment record (rồi tới `deployed.json` `sha` đã chứng minh). Khi không biết commit, **manual**
update vẫn chạy — pre-update checkpoint lấy source từ deployment record — còn automatic update bị hoãn (`Automatic
update refuses a deployment whose source commit is unknown; run a manual update.`). Pre-update checkpoint là healthy
checkpoint schema 1 có verification record (mục 10).

**Phase và resume (PF-A3.2).** Một update chạy `preparing` (staging), `preserving` (dừng, pre-update checkpoint),
`migrating` (restore rehearsal candidate, rehearsal migration, drop candidate, live migration), `activating` (backend,
frontend, seal, pointer) và `syncing-workspace` (mục 8). Sau khi bị gián đoạn, `pf resume`:

- **trước mọi effect database** (staging, dừng, checkpoint, rehearsal candidate): drop candidate của chính operation
  theo tên đã lập kế hoạch, mở lại deployment không đổi và đóng update ở `cancelled` (nghĩa cũ của `pf resume`);
  `pf resume --abandon` làm đúng như vậy (`ABANDON <op8>`);
- **sau một live database effect**: tiếp tục về phía trước theo journal;
- khi **kết quả của live migration bị mất**: đọc live Alembic heads. Heads bằng target được tính là đã xong (upgrade
  không bao giờ bị lặp); heads vẫn ở revision trước, hoặc heads khác, dừng ở `needs_operator` (`effect-unknown`) vì không
  thể loại trừ một thay đổi non-transactional dang dở. Route được hỗ trợ khi đó không mất dữ liệu:
  `sudo pf --instance <slug> rollback <before-update checkpoint> --restore-db` supersede update, preserve database hiện
  tại trước rồi restore checkpoint. Update khi đó được liệt kê là `superseded`.

## 10. Backup và rollback

Tạo verified revision checkpoint:

```sh
sudo pf backup
```

Checkpoint gồm:

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

**Manifest schema 1 (PF-A3.1).** `manifest.json` của checkpoint mới theo record `recovery_manifest` của
`deploy/synology/contracts/lifecycle-records.schema.json`; `manifest.sha256` là SHA-256 của đúng byte của file. Nó
ghi:

- **capture class**: `healthy_checkpoint` (schema live khớp image đã deploy, biết chính xác deployed source và mọi
  image đều được nhận diện), `emergency_preservation` (data thực tế, mismatch được ghi làm bằng chứng) hoặc
  `partial` (emergency capture mà một image hay source không nhận diện được);
- quiescence (backend/frontend có đang chạy không), origin và provenance của source, deployment mà nó gắn với (ID
  và hash của record; bản thân record không bao giờ được copy vào checkpoint vì chứa secret), identity của image
  backend/frontend/db (ID, platform, repo digest), version của PostgreSQL server, và với từng database store: owner,
  encoding/collation/ctype, extension, Alembic head và — chỉ khi writer đã dừng (trước `update`, `reset-db`,
  `rollback`, `purge`) — row count chính xác; `pf backup` khi writer đang chạy không ghi row count;
- mọi payload với type, size và hash, cùng các exclusion và manual prerequisite mà restore cần.

**Verification level.** Restore test ghi một **verification record** riêng trong
`<root>/instances/<uuid>/artifacts/verifications/<bundle-id>/` (chỉ root); bundle không bao giờ bị sửa sau đó. Level
mà `backup`, `backups` và `recoveries` hiển thị được tính từ các record này: `captured` (đã seal, chưa có record),
`failed` (restore test gần nhất lỗi), `data-restore` (mọi store được restore từ chính dump của bundle, có kiểm tra
head, locale và row count) và `functional` (từ PF-A3.3: đúng purge bundle hoặc một side-by-side target được restore
và kiểm tra trong một isolated topology, mục 12). Record hỏng được báo là
`note: verification-record-invalid: …; ignored.` và không nâng level. `pf backup` giờ in:

```text
Checkpoint class: healthy_checkpoint
Verification level: data_restore_verified (record <verification-id>)
```

**Đọc chặt.** Mọi consumer (listing, rollback, purge, restore) trước hết đọc bundle chặt: byte của manifest phải khớp
`manifest.sha256`, manifest phải hợp lệ, và mọi payload phải là file thường một link với đúng size và hash đã ghi;
file mà manifest không liệt kê bị từ chối. Không có gì được extract, xác nhận hay ghi journal trước bước này. Sau đó
archive được extract bằng importer an toàn, từ chối mọi link hay special file, path tuyệt đối hoặc `..`, member trùng
hay quá lớn và archive thay đổi trong lúc đọc (`archive-member-refused`, `archive-unreadable`, `archive-changed`,
`archive-capacity`).

**Trước một capture.** `pf backup`, `update`, `reset-db` và `purge` từ chối healthy checkpoint trước mọi thay đổi khi
backend/frontend image đang chạy không phải của deployment hiện tại (`deployment-image-mismatch`), hoặc khi hoàn toàn
không chứng minh được deployed source (refusal `deployment-artifact-mismatch`, hoặc refusal cũ "Cannot reconstruct
the exact deployed source revision" cho deployment không có record). Khi chỉ file của deployment record bị hỏng
nhưng source vẫn chứng minh được từ protected source store hoặc workspace, capture tiếp tục với
`note: deployment-artifact-mismatch: …` và ghi record là excluded.

**Emergency preservation.** Khi healthy checkpoint bị từ chối nhưng data cần được giữ, chạy tương tác:

```sh
sudo pf --instance <slug> backup --emergency
```

Lệnh in contract quan sát được (live head, image head, image ID đang chạy và kỳ vọng), hỏi
`EMERGENCY BACKUP <project>`, capture và restore-test database hiện tại rồi in `Emergency preservation <id> captured
(<level>). It is evidence and data for repair or export, not a rollback target.` Khi workspace bị lệch vượt giới
hạn archive (mục 16, `workspace-archive-limit`) lệnh vẫn giữ data và ghi workspace là excluded. Lệnh bị từ chối khi
không có terminal và không bao giờ được `backup.sh` chạy. Khi một deploy, update, rollback hay reset-db bị ngắt, `status` liệt
kê nó như một route hợp lệ; nó không thay đổi operation bị ngắt. Emergency hay partial capture **không bao giờ là
rollback target** (`checkpoint-not-rollback-target`).

**Preservation khi rollback.** Mọi `rollback`, code-only hay `--restore-db`, giờ preserve database hiện tại sau khi
dừng application write và trước khi restore hay switch bất cứ gì: healthy checkpoint khi live contract khớp, ngược
lại là emergency preservation. Nếu database hiện tại không capture **và** restore-test được, rollback dừng với
`preservation-failed`: không có gì được restore hay switch, service vẫn dừng, và `pf resume` mở lại deployment không
đổi (mục 16 có bước `pg_dump` thủ công).

**Kiểm tra khi rollback.** Checkpoint được chọn phải là healthy checkpoint; image ID backend/frontend của nó phải vẫn
được giữ; database image khác chỉ in `note: db-image-changed: …` khi PostgreSQL major khớp. Source được extract bằng
importer an toàn và so với source manifest đã ghi (`source-manifest-mismatch`). Code-only rollback không dùng dump,
nên level `failed` chỉ là note (`note: verification-failed: …`). `--restore-db` restore vào candidate được tạo với
encoding và locale của store, kiểm tra head, locale, extension sẵn có, owner và (khi có ghi) row count, ghi
verification record **trước** khi switch, và khi có khác biệt thì drop candidate và từ chối với
`checkpoint-incompatible`, database hiện tại không đổi.

**Checkpoint cũ (legacy).** Checkpoint format 1 và 2 ghi trước PF-A3.1 được đọc qua migration tường minh, tất định
(`note: legacy-manifest-migrated: <id> format <n> read as <class>; limitations: …`), không bao giờ bị ghi lại.
Checkpoint format 2 chỉ là healthy khi nó đã ghi source verified, restore test passed, cả hai image và migration
fingerprint; nó được liệt kê với `legacy-format-2` và `source=claimed <sha12>`: commit được claim chỉ là giả thuyết
mà protected store phải chứng minh lại trước khi dùng. Mọi checkpoint format 1 đọc ra là `partial` (trước đây cũng
không rollback được). Không có verification record nào được tổng hợp từ claim `restore_test` cũ: level giữ ở
`captured` cho tới khi một `--restore-db` verify nó trên candidate.

Từ PF-A2.3, checkpoint mới nhận backups target của permission policy đang hiệu lực (group của thư mục backups,
hoặc của revision đã duyệt; Xem và sao chép cho directory `0750` và file `0640`), được đặt tường minh và verify.
Chỉ checkpoint mới bị thay đổi. Entry của checkpoint kế thừa ACL từ thư mục chứa nó sẽ dừng backup
(`fresh-entry-acl`) trước khi được publish.

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

`pf backups` in mỗi checkpoint một dòng (PF-A3.1):

```text
<n>. <bundle-id>  [<healthy|emergency|partial>|<captured|failed|data-restore|functional>]  <reason>  DB=<heads>  source=<git_commit <sha12>|unknown|claimed <sha12>>  [legacy-format-N]
<n>. <folder>  [invalid: <code>]
```

**Journal cho backup và rollback (PF-A3.2).** `pf backup` ghi một plan (capture, rồi verification). Lỗi capture hoặc
verification bị bắt sẽ đóng nó ở `failed_preserved` (không chặn; thư mục dang dở được liệt kê là `bundle-attempt` và
không bao giờ được chọn); chỉ khi process bị ngắt nó mới còn mở, và `pf backup` hoặc `pf resume` khi đó tạo một lần
thử mới với bundle ID mới (candidate `pf_verify_*` verify dở của plan được drop trước). `pf rollback ... --restore-db`
có thể **supersede** một `update`, `rollback` hoặc `reset-db` đang mở đã dừng application (hoặc đang ở
`needs_operator`); code-only rollback chỉ được supersede một update không có database effect nào, nếu không sẽ bị từ
chối với `review recovery with --restore-db`. Restore rollback candidate bị gián đoạn sẽ được drop và restore lại từ
cùng checkpoint; checkpoint đã thay đổi trong lúc đó bị từ chối với `plan-input-changed` trước xác nhận
`RESUME <op8>` (không ghi gì). Một operation superseding được resume trước khi thay đổi gì sẽ **withdraw**: không khởi
động service nào, và operation bị supersede cùng các route của nó lại có hiệu lực. `pf resume --abandon` của một
rollback trước database switch drop candidate `pf_restore_*` của chính nó, rồi mở lại deployment không đổi (hoặc, với
rollback superseding, withdraw và để service ở trạng thái dừng).

**Lựa chọn dữ liệu và capacity (PF-A3.3).** Xác nhận của `rollback ... --restore-db` giờ kết thúc bằng một dòng lựa
chọn dữ liệu: database đang hoạt động quay về checkpoint đã chọn (kèm thời điểm tạo); dữ liệu ghi sau thời điểm đó vẫn
nằm trong database giữ lại `pf_keep_*` và trong preservation `before-rollback`, và không có gì được merge. Đúng bản tóm
tắt operator đã xác nhận được giữ ở `operations/<op>/confirmation-summary.txt` (0600; SHA-256 của nó là
`confirmation.summary_sha256` của plan) và `pf status --operation <op>` in nó ra. Trước xác nhận, mọi lifecycle command
đo dung lượng trống cần trên từng device (`statvfs`): backups, recovery, workspace, artifacts, private state và Docker
root (`DockerRootDir` của daemon). Nhu cầu của các role trên cùng một device được cộng dồn và floor `minimum_free_mb`
chỉ tính một lần cho mỗi device; thiếu thì từ chối với `capacity-insufficient` và không thay đổi gì, kết quả đo được ghi
vào `operations/<op>/capacity.json`. Mọi `pf backup` cần chỗ trên Docker root cho restore test; khi không đo được Docker
root, lệnh từ chối với `capacity-unmeasurable` và chỉ ra `pf backup --emergency`, lệnh này bảo toàn database mà không
cần kiểm tra đó. Nhu cầu được kiểm tra lại trước mỗi effect; pf không bao giờ xóa gì để lấy chỗ.

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

Từ PF-A3.1, checkpoint `before-reset` là healthy checkpoint schema 1 có verification record, và `reset-db` trước hết
chạy cùng kiểm tra gắn image với deployment như `pf backup` (`deployment-image-mismatch` trước mọi thay đổi).

Từ PF-A3.3, `reset-db` quyết định theo contract quan sát được, trước xác nhận:

- live head bằng head của image đang chạy: healthy checkpoint `before-reset`, như trước;
- **schema/image mismatch** (live head khác head của backend image đang chạy, và image đang chạy đúng là image mà
  deployment record ghi): database hiện tại được capture thành **emergency preservation** trước (là dữ liệu và bằng
  chứng, không bao giờ là rollback target), sau đó database sạch được migrate tới **head của image đang chạy**;
- **deployment-image mismatch** (backend image đang chạy không phải image deployment record ghi) bị từ chối với
  `reset-deployment-image-mismatch`; hãy đưa deployment về đúng trước (`update` hoặc `rollback`);
- image contract không đọc được bị từ chối với `reset-contract-unknown`.

Bản tóm tắt xác nhận nêu preservation và database giữ lại (`pf_keep_*`, database đang hoạt động trước đó, không bao
giờ bị drop) và được giữ thành `confirmation-summary.txt`. Lỗi preservation dừng với `preservation-failed` trước mọi
candidate hay switch. Không bao giờ chạy `alembic downgrade`.

Từ PF-A3.2, `reset-db` bị gián đoạn drop candidate `pf_clean_*` của chính nó và làm lại; `pf resume --abandon` trước
database switch thì drop candidate và mở lại deployment không đổi. Database switch mà tên
database chứng minh là chưa bắt đầu sẽ được làm lại; switch để lại tên đổi nửa chừng sẽ dừng ở `needs_operator`
(`database-switch-unknown`) và được khôi phục bằng `rollback <before-reset checkpoint> --restore-db`.

Một `reset-db` bắt đầu trên **schema/image mismatch** không bao giờ mở lại được deployment không đổi, vì database của
nó không ở head của image đang chạy. Khi nó bị gián đoạn trước database switch, `pf resume` đi **tiếp (forward)**: quan
sát lại emergency preservation (hoặc capture lại) rồi hoàn tất reset. `pf resume --abandon` drop candidate
`pf_clean_*` và đóng operation `cancelled` **không mở lại**: application service giữ nguyên như reset đã để lại
(thường là đã dừng), và thông báo nêu các route còn lại: một `pf reset-db` mới (preserve dữ liệu hiện tại lần nữa) hoặc
`pf rollback <checkpoint> --restore-db` về một healthy checkpoint. Điều tương tự áp dụng sau
`reset-images-unidentified` của một reset như vậy: thông báo của nó nêu `resume --operation <op> --abandon`.

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

Từ PF-A3.1, bundle là manifest `purge-bundle` schema 1 và chứa thêm:

```text
deployment/deployment-record.json    deployment record hiện tại (khi đã seal và còn nguyên)
deployment/compose-resolved.json     Compose model đã resolve (nhạy cảm)
```

`images.tar` giờ chứa cả layer của database image, được save theo image ID (từ PF-A3.3 được `restore-instance` dùng khi
không có `postgres:16`, mục "Restore nguyên functional instance đã purge").
Mọi payload copy từ checkpoint `before-purge` hay từ deployment record đều được hash lại (`bundle-payload-mismatch`
dừng purge trước khi xóa và mở lại application). Database `pf_keep_*` được giữ lại được dump với head thật (chỉ cho
phép connection trong lúc dump rồi đóng lại); `postgres-globals.sql` và danh sách role chỉ là bằng chứng và không bao
giờ được thực thi. Sau khi bundle được seal, mọi store được restore **từ chính payload của bundle** vào database tạm
và được kiểm tra; record `data_restore_verified` thu được, gắn với hash manifest của bundle này, là bắt buộc trước lần
xóa đầu tiên (nếu không: `purge-bundle-unverified`, purge dừng trước khi xóa và application được mở lại). Từ PF-A3.3
kiểm tra đó được thay bằng functional verification của mục con tiếp theo. Dòng của
`pf recoveries` là:

```text
<n>. <bundle-id>  [<level>]  project=<project>  db=<active database>  source=<git_commit <sha12>|unknown|claimed <sha12>>  derived_from=<checkpoint id>  [legacy-format-N]
```

Recovery bundle nhằm dựng lại **functional PartFlow state**, không cố khôi phục Docker
container ID/network ID giống từng byte.

Từ PF-A2.3, file trong bundle là bản copy chỉ có nội dung (không copy mode, owner hay ACL) và bundle nhận recovery
target của permission policy đang hiệu lực. Purge giữ permission policy record
(`<root>/instances/<uuid>/permission-policy.json`); instance được deploy lại hoặc restore vẫn chịu record đó.
Record không nằm trong bundle: instance được đăng ký mới bắt đầu với derived policy.

### Instance purge: kiểm tra final bundle trong isolated topology

Từ PF-A3.3, instance purge kiểm tra functional **đúng final bundle** trước khi xóa bất cứ thứ gì. Application vẫn dừng
(backend và frontend) từ lúc dừng writer cho đến khi purge kết thúc hoặc bị hủy; không có gì khởi động chúng ở giữa.

1. Preview từ chối trước `PURGE <project>` khi một isolated topology được giữ lại của instance này vẫn còn
   (`isolated-topology-present`: nó chạy image của instance, nên không thể phân loại tag của các image đó; xóa bằng
   `pf cleanup --apply`, thêm `--recovery-target <project>` nếu là side-by-side target), khi isolated model của
   deployment hiện tại không render được trên host này (`verification-isolation-unsupported`), khi tên project sinh ra
   đã được dùng (`topology-name-collision`) hoặc khi thiếu dung lượng (`capacity-insufficient`).
2. Sau khi bundle được seal, database nguồn được kiểm tra bằng lệnh invariant của chính application (`app.cli reconcile`
   trong backend image đang chạy). Image không có lệnh đó được ghi là `unavailable:`; lỗi hoặc report không đọc được
   sẽ dừng instance purge (`app-check-failed`) và mở lại application.
3. Bundle được restore vào một Compose project tạm `pfverify-<12 hex>`: data volume và internal network riêng, **không
   publish port** (kể cả loopback), `restart: "no"`, image theo ID từ bundle, và một **database password sinh mới** chỉ
   tồn tại trong `operations/<op>/isolated/<project>/app.env` và `compose.json` cho đến lần teardown cuối (topology
   chưa từng có container, ví dụ sau khi render-back bị từ chối, mất cả hai file khi purge dừng). Password của bundle
   không bao giờ được ghi vào file operation nào.
4. Các kiểm tra (đều ghi trong record `functional_recovery_verified`): cách ly theo những gì daemon báo (internal
   network, không có host port binding, volume riêng, restart policy), mọi store (head, locale, owner, extension, row
   count), health của backend và frontend, image ID đang chạy, bằng chứng image archive, source digest, cấu hình và
   deployment record, và application invariant: database đã restore phải cho cùng kết quả reconcile như nguồn
   (`clean`/`clean`, hoặc cùng số mismatch); `unavailable` ở cả hai phía được ghi là `not_run unavailable:`.
5. Topology được teardown bằng deletion plan đóng băng của chính nó (chỉ resource mang project và topology UUID của
   nó); teardown không hoàn tất được thì giữ stack, ghi nó là retained `isolated-topology` và vẫn hủy purge.
6. Deletion gate (sau `ERASE ...`) yêu cầu functional record đã passed của **đúng bundle này, đúng manifest hash này và
   đúng operation này**, writer đã dừng và nguồn không đổi (database, row count, head); nếu không thì
   `purge-bundle-unverified`, `purge-writers-running` hoặc `purge-source-changed`, và application được mở lại.

Kiểm tra thất bại sẽ đóng instance purge ở `cancelled` với `functional-verification-failed` nêu tên kiểm tra, mở lại
application và giữ topology đã dừng để xem xét; `pf cleanup --apply` xóa nó. Nếu bị gián đoạn ở `verifying`,
`pf resume` teardown topology theo plan của nó và mở lại application.

Sau khi xóa, instance purge đánh dấu registry record `state: purged` qua một registry transaction (quyết định của owner
OD-A33-08): record giữ UUID và lịch sử, và nhả Compose project claim, nên instance khác có thể đăng ký project đó.
`pf status` in `Lifecycle: purged by instance purge <op>` và `instances` hiển thị `state=purged`. `restore-instance` một
bundle của chính instance này sẽ claim lại project (bị từ chối với `instance-claim-taken` khi instance khác đang giữ). Một `deploy` mới của purged record cũng làm vậy
(kiểm tra lại claim dưới registry lock trước lần ghi đầu tiên); `pf abort-deploy` của lần deploy dang dở đó, và
`pf resume --abandon` của một restore đã đăng ký lại record, đưa nó về `state: purged`. Việc ghi state cần registry
lock; khi một installation transaction đang giữ lock, operation dừng với `registry-busy` (không đổi gì) và `pf resume`
tiếp tục nó. Nếu lần abandon đó bị ngắt sau khi record đã về `state: purged` nhưng trước khi restore được đóng, chỉ
`pf resume --abandon` tiếp tục nó (`resume` từ chối với `abandon-in-progress`); nó đóng restore `cancelled` mà không ghi
record lần nữa. Mọi thay đổi khác của record vẫn bị từ chối với `plan-authority-changed`.

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

Từ PF-A3.2, purge có duyệt hai giai đoạn: plan được ghi ở `PURGE <project>`; deletion plan ràng buộc và lựa chọn về
backup/admin-config chỉ được đóng băng vào journal sau `ERASE ...`. Lỗi hoặc xác nhận bị từ chối trước đó sẽ mở lại
application ngay và đóng purge ở `cancelled` (hành vi không đổi). Purge bị gián đoạn trước thời điểm đó được mở lại
như nhau bởi `pf resume` hoặc `pf resume --abandon`: mọi store mà plan ghi là không cho kết nối được đặt lại
`ALLOW_CONNECTIONS false` và deployment không đổi được mở lại. Journal và instance lock nằm ngoài mọi thứ purge xóa,
nên `pf status` hiển thị purge (và bước kế tiếp) kể cả khi không còn `.env` và daemon. `pf purge` vào lại purge đang
mở ở `deleting` hoặc `finalizing` (alias của `pf resume`, `RESUME PURGE <project> <bundle-id>`); yêu cầu lựa chọn
backup hoặc admin-config khác với lựa chọn đã duyệt bị từ chối với `plan-inputs-conflict`. Purge với
`--reset-admin-config` resume bằng admin configuration mà nó đã đóng băng. Nếu recovery bundle không còn đọc hoặc
verify được, deletion vẫn bị chặn (mục 16).

### Brand-new deploy sau purge

```sh
sudo pf deploy --latest
```

Full purge thông thường xóa `config/.env`, vì vậy deploy wizard sẽ tạo environment mới và
PostgreSQL password mới. Nếu một recovery path cụ thể giữ external configuration thì deploy
sẽ validate trước khi reuse.
`purge --reset-admin-config` xóa thêm `pf-config.json`; purge khi đó nêu các bước tiếp theo:
`sudo pf --instance <slug> config admin`, rồi `sudo pf --instance <slug> deploy --latest` (dùng launcher ở mục 5
khi `sudo pf` không tới root này).

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
Từ PF-A3.1, bundle được đọc chặt trước mọi confirmation, deployed source được extract từ source payload của bundle
bằng importer an toàn và so với manifest đã ghi, và chính source đó (không phải workspace đã lưu) được stage thành
deployment mới; sau health check một deployment record mới được seal với `restored_from` ghi tên bundle, và một
`deployed.json` mới trỏ tới nó. `state/deployed.json` trong bundle chỉ còn là bằng chứng.
Installed root-owned control plane hiện tại được giữ; recovery không downgrade lifecycle
controller giữa operation. `config/pf-config.json` hiện tại cũng tiếp tục là authoritative config;
bản được lưu trong recovery chỉ để compare/reapply thủ công, không bị activate giữa restore.

Từ PF-A3.2, `restore-instance` ghi plan có bundle ID và manifest hash, và đóng băng `.env` của bundle trước effect đầu
tiên. `pf resume` (hoặc `pf restore-instance <cùng bundle>`) tiếp tục về phía trước; bundle khác bị từ chối với
`plan-inputs-conflict`. Trong lúc chỉ mới chuẩn bị target (staging, `.env`, nạp image, khởi động database),
`pf resume --abandon` (`ABANDON RESTORE <project> <op8>`) xóa những gì restore này tạo ra — resource đã đóng băng của
nó, `.env` nó đã ghi (chỉ khi byte không đổi) và staging của nó — và trả lại `.env` đã sửa; khi data restore đã bắt đầu
thì chỉ còn tiếp tục về phía trước. `config/.env` có sẵn với byte khác không bao giờ bị ghi đè: nó được rename thành
`config/.env.proposal-<op8>` (`note: env-proposal-preserved`). Checkpoint history có sẵn khác với của bundle được rename
thành `backups/revisions/<project>.pre-restore-<op8>` (không bao giờ xóa, không được `backups` liệt kê); history giống hệt
thì để nguyên.

Từ PF-A3.3, restore target được quyết định theo **danh tính**, trước mọi xác nhận:

- một bundle chỉ restore vào instance có UUID mà bundle ghi; bundle của instance khác (kể cả bundle mang từ một host
  đã mất) bị từ chối với `restore-target-mismatch` cho cả exact restore lẫn side-by-side, và `pf` không restore được
  nó trong checkpoint này (OD-A33-09: sai khác đã khai báo so với design r3 LIFECYCLE §9, đang chờ owner phê duyệt).
  Legacy bundle không có UUID chỉ được nhận khi cùng Compose project;
- đường dẫn workspace, repository và home ghi trong bundle **chỉ là provenance**: restore luôn ghi vào đường dẫn của
  instance đã chọn và in `note: bundle-workspace-differs` khi chúng khác; không ghi gì dưới đường dẫn đã ghi trong
  bundle;
- **database image**: khi daemon không có tag `postgres:16`, database image của bundle được nạp từ `images.tar` (sau
  khi chứng minh hash config và layer của archive) và được tag `postgres:16` (`note: db-image-tagged`). Tag này có hiệu
  lực trên toàn daemon: bản tóm tắt liệt kê các instance đã đăng ký khác trên cùng daemon đang dùng nó. Tag có sẵn trỏ
  tới image ID khác được giữ nguyên (`note: db-image-changed`); legacy bundle không ghi database image thì dùng tag cục
  bộ (`note: db-image-unrecorded`);
- sau khi frontend khởi động, application invariant của database đã restore được so với oracle của bundle và in ra
  `Application invariants: <clean|mismatch …|could not run|unavailable>`; dòng này là bằng chứng và không dừng restore;
- registry record đã purged được claim lại (effect `registry:state=registered`) trừ khi instance khác đang giữ project
  (`instance-claim-taken`).

### Side-by-side recovery

Nếu instance đang chạy nhưng cần xem hoặc export dữ liệu của một bundle **của chính nó**:

```sh
sudo pf restore-instance RECOVERY_ID --side-by-side
```

Từ PF-A3.3 lệnh này tạo một **recovery target** được giữ lại: một Compose project riêng `pfrecover-<12 hex>` với data
volume và internal network riêng, **không có listener (kể cả loopback)**, không scheduler và database password sinh
mới. Đúng bundle được restore vào đó và được kiểm tra functional như trong instance purge (record giữ
`removed: false`). Instance đang chạy, dữ liệu, listener, image override (`active-images.yaml`), image tag, workspace và
deployment pointer của nó không thay đổi. Khi một image của bundle không còn trên daemon, effect `image-load` chỉ load
`images.tar` (archive proof và kiểm tra retag chạy lại ngay trước khi load) rồi yêu cầu mọi image ID của target; nó không
bao giờ ghi image override và không kiểm tra tag nào theo override đó. Việc load có thể thêm lại tag của chính bundle;
nó không bao giờ trỏ lại một tag đang có (`image-load-would-retag`). Xác nhận bằng `RESTORE COPY <bundle-id>`; lệnh được ghi journal thành operation
`restore-side-by-side`, `pf resume` tiếp tục nó, cùng lệnh với cùng bundle sẽ vào lại nó, và `pf resume --abandon`
(`ABANDON RECOVERY TARGET <project>`) teardown target. Target có data volume hoặc data check bị thay đổi trong lúc
operation bị gián đoạn sẽ bị xóa và báo `recovery-target-lost`. Chế độ database `pf_recovery_*` cũ không còn; database
`pf_recovery_*` có sẵn chỉ được báo cáo (`status`, `cleanup`), không bao giờ bị drop.

`pf status` liệt kê `Recovery targets: <project> from bundle <id> (operation <op> …)`. Xem target với quyền root theo
project label, ví dụ:

```sh
sudo docker ps --filter label=com.docker.compose.project=pfrecover-0123456789ab
sudo docker exec -it <db container> psql -U <user> -d <database>
```

Xóa nó bằng `sudo pf cleanup --apply --recovery-target <project>` (`REMOVE RECOVERY TARGET <project>`); target đang
chạy cũng chặn instance purge (`isolated-topology-present`).

PartFlow **không** generic auto-merge recovered DB vào active DB mới. `PartMovement`, quantity
lineage, allocation, reversal và derived current state có domain invariant không thể merge
an toàn bằng generic SQL `INSERT`. Hãy restore side-by-side rồi xây explicit domain-aware
import/reconciliation cho đúng loại data thật sự cần mang qua.

### Hủy lần deploy đầu tiên sau khi frontend đã mở truy cập

Từ PF-A3.3, `pf abort-deploy` cũng được phép sau khi lần deploy đầu tiên chưa hoàn tất đã mở truy cập frontend (và
trước khi deployment pointer được ghi). Người dùng có thể đã ghi dữ liệu, nên abort **bảo toàn database hiện tại trước**:
nó dừng backend và frontend, capture một checkpoint `before-abort` (emergency preservation khi contract không đúng)
trong `backups/revisions/<project>/`, rồi mới xóa các resource đã đóng băng của lần deploy chưa hoàn tất. Checkpoint
còn lại sau khi abort. Database phải truy cập được, nếu không abort bị từ chối trước xác nhận (`preservation-failed`);
lỗi capture để abort mở ở `preserving` mà không xóa gì (`pf resume` thử lại việc bảo toàn, `pf resume --abandon` đóng
nó và deploy lại chặn như trước). Bản tóm tắt xác nhận được giữ thành `confirmation-summary.txt`. Trước khi frontend mở,
abort không thay đổi.

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

Khi đã có grant, một DSM task root-owned phải nêu instance và chạy wrapper mà `install-control.sh init`
đặt trong `<root>/bootstrap/` cạnh launcher đã cài (PF-A2.1), hoặc chạy chính launcher; trước đó các wrapper
đã đặt bị từ chối như mọi lệnh không người trực khác:

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

Từ PF-A3.1, output của `backup` có thêm dòng `Checkpoint class:` và `Verification level:` (mục 10), và `backup` theo
lịch, khi đã được cấp grant, từ chối `deployment-image-mismatch` giống hệt lệnh chạy tay. `backup --emergency` không
bao giờ được schedule: nó cần terminal và `backup.sh` không truyền cờ này.

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

Từ PF-A2.2 `pf config` là nhóm config wizard (`pf config admin`, `pf config app`). `pf config` đứng một mình
hoặc đi với từ khác (ví dụ `pf config --services` trước đây) vẫn bị từ chối với `compose-route-removed`; raw
Compose model không được cung cấp (`pf doctor` validate nó một cách riêng tư).

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

**PF-A3.2:** không chạy dạng raw khi `pf status` đang hiển thị operation mở — nhất là ở `syncing-workspace` hoặc
`workspace_sync_pending`, khi đường dẫn workspace truyền vào `--project-directory` và `PARTFLOW_REPO_ROOT` có thể vắng
mặt — và không bao giờ với `config/.env` đã sửa mà operation đang mở chưa đóng băng (controller dùng `app.env` đã đóng
băng của từng operation).

Không dùng các lệnh rộng như:

```text
docker system prune --volumes
docker volume prune
```

để reset PartFlow trên NAS có thể đang host workload khác.

## 15. Update control plane

Application `update` cố ý không update control plane. Cài một control release đã review một cách
explicit:

```sh
sudo <root>/bootstrap/pf install control --source <reviewed repository tree>
```

- Summary hiển thị release đang bind và release mới (checkpoint, số file, inventory hash), mọi instance
  được rebind (mỗi root một control release), default (không đổi) và việc application có cần một operation
  riêng hay không.
- Sau `INSTALL CONTROL <release-id>`, release được stage riêng tư và smoke-check bằng interpreter đã đăng
  ký: code mới phải import được, load được registry và mọi record, và chấp nhận `pf-config.json` và `.env`
  live của mọi instance mà release đang chạy chấp nhận. Release được verify lại, publish, bind
  (`bootstrap.conf`, rồi mọi record) và verify end-to-end qua bootstrap thật. Nếu verify đó thất bại,
  binding trước đó được tự động khôi phục (`install-verify-failed`, operation `rolled_back`); release mới vẫn
  được giữ lại và không hoạt động.
- `app_operation_required: update` nghĩa là release mới thay đổi `compose.nas.yaml`: application đang chạy
  giữ nguyên container, và lần `pf --instance <slug> update` tiếp theo áp dụng topology mới.
- Quay lại (hoặc tiến tới) một release đã giữ bằng
  `sudo <root>/bootstrap/pf install control --release r-<16 hex>` (`SELECT CONTROL <id>`), chạy đúng các
  kiểm tra smoke, contract và bootstrap như trên. Source tree có release đã được publish cũng được chọn theo
  cách đó.
- Operation bị gián đoạn được tiếp tục bằng `pf install resume` hoặc hủy bằng
  `pf install resume --abandon`; rollback bị gián đoạn luôn hoàn tất việc khôi phục.
- `--source` luôn chỉ một source tree và `--release` luôn chỉ một release id đã giữ; giá trị sai loại bị từ
  chối, không bao giờ được hiểu lại. Chỉ câu trả lời gõ tại prompt `candidate` mới được phân loại (bắt đầu
  bằng `/` là source tree).
- Candidate chỉ thay đổi một scheduler wrapper (`backup.sh`, `release-check.sh`) là `control-unchanged`
  (exit 0): thông báo nêu wrapper khác nhau. Thay đổi chỉ-wrapper không được cài riêng; nó được cài cùng
  control release kế tiếp có file thay đổi.
- Gián đoạn trong lúc tự khôi phục sau khi verify thất bại là `install-interrupted`: operation ở lại
  `rolling_back` và mọi route của instance bị từ chối cho tới khi `pf install resume` hoàn tất việc khôi phục.
- Byte của launcher và verifier bị đóng băng: candidate thay đổi `pf.sh` hoặc `pf_bootstrap.py` bị từ chối
  với `bootstrap-change-unsupported` (launcher migration thuộc PF-A4.3).
- Cài hoặc chọn release không bao giờ migrate `pf-config.json`. Khi file của một instance đã là schema 2,
  release được giữ lại nhưng cũ hơn PF-A2.2 sẽ từ chối nó ở smoke check (`install-smoke-failed`, không bind
  gì); hãy khôi phục file schema 1 trước nếu thật sự cần quay lại.
- Archive cũ `recovery/control-upgrades/` không còn áp dụng; release cũ nằm dưới `<root>/releases/`.
- **PF-A2.2 → PF-A2.3.** Byte của bootstrap và install contract không đổi, nên `pf install control --source` hợp
  lệ. Khi chưa có approval, derived policy giữ group và mode của A2.2, ngoại trừ: backup và recovery giữ group đã
  có trên thư mục của chúng, `deploy --current` không còn đổi workspace, bản copy mới chỉ có nội dung và
  `pf permissions` không kèm verb bị từ chối. **Downgrade** sau approval: control cũ hơn bỏ qua
  `permission-policy.json` và quay lại group trong `pf-config.json`. Installer từ chối mọi lần đổi control,
  upgrade hay downgrade, khi một lần permission apply đang mở (`instance-operation-pending`): hãy hoàn tất bằng
  `--resume` hoặc `--abandon` trước.

- **PF-A3.1 → PF-A3.2.** Byte của bootstrap không đổi. `pf install control` bị từ chối khi bất kỳ instance
  operation nào đang mở (`instance-operation-pending`, nêu tên operation; cả khi journal không hợp lệ hoặc operation
  index tràn). Runner record (`unresolved-effects.json`) của operation có journal được reconcile bởi journal đã đóng
  của nó (`status --operation` hiển thị `reconciled by journal sequence <n>`), và record của operation bị supersede
  được reconcile bởi journal đóng của recovery cuối cùng trong chuỗi supersede; record trong thư mục operation không có
  journal vẫn bị từ chối với `instance-effects-unresolved`. Các thư mục đó đến từ `backup --emergency`, side-by-side
  restore, operation trước A3.2, và từ bất kỳ lệnh lifecycle nào có child process
  bị ngắt hoặc hết thời gian trước khi xác nhận, khi chưa có plan (ví dụ Ctrl-C trong lúc `compose build` candidate,
  các kiểm tra contract `compose run` của `deploy`, `update` và `rollback`, hoặc `ensure_local_contract`): kiểm tra
  bằng `pf status`, xác nhận không còn gì của chúng đang chạy và giữ nguyên thư mục. Từ PF-A3.3 hãy acknowledge chúng
  bằng `sudo pf resume --operation <op> --acknowledge` (`ACKNOWLEDGE <op8>`): pf trước hết từ chối khi một one-off
  container của instance vẫn chạy, hoặc, khi một record nêu database effect, khi còn client session trên database mà các
  record nêu (mọi client session khi record đó không nêu database; khi đó hãy dừng application trước khi acknowledge)
  (`effect-still-running`). Session của chính application đang chạy trên database của nó không bao giờ chặn record
  không có database effect. Sau đó pf in ra những gì nó quan sát được (chỉ danh tính), và ghi `operations/<op>/acknowledgement-<hash12>.json` (0600, tạo độc quyền) gắn với hash của các
  record hiện tại. Khi đó các record không còn chặn `pf install`; `pf status` đếm chúng là đã acknowledge. Record được
  thêm vào sau đó sẽ mở lại. Operation có journal, thư mục không có record hoặc đã được acknowledge bị từ chối với
  `acknowledge-not-legal`.
- **PF-A3.2 → PF-A3.3.** Byte của bootstrap không đổi; `pf install control` bị từ chối khi bất kỳ instance operation
  nào đang mở. **Downgrade** về control PF-A3.2 chỉ khi không có operation nào đang mở (kể cả `restore-side-by-side`
  hay `cleanup`): control cũ không biết các operation kind mới, recovery target hay file acknowledgement (record đã
  acknowledge lại bị coi là chưa giải quyết ở đó), và nó không tự ghi registry state `purged` (record đã được đánh dấu
  `purged` vẫn giữ nguyên).

Bước install explicit này chính là security boundary cho phép `repo/` writable bởi users.
Không cài tree chưa review/không rõ nguồn bằng `sudo`.

## 16. Troubleshooting

### SMB thấy `repo/` nhưng không sửa được

So sánh trước, rồi apply permission policy (chạy từ ngoài các thư mục, ví dụ `cd /`):

```sh
sudo pf --instance <slug> permissions check --scope workspace
sudo pf --instance <slug> permissions apply --scope workspace
```

Sau đó kiểm tra từ share root rằng DSM Shared Folder permission cho account/group Read/Write.

### Mã của `pf permissions`

- `permissions-verb-required` (exit 2): `pf permissions` không kèm verb không còn thay đổi gì; dùng `check`, `plan`
  hoặc `apply`.
- `permission-policy-invalid`: policy (hoặc derived policy, ví dụ tên group có `/`) vi phạm một quy tắc; không thay
  đổi gì.
- `permission-group-missing`: một group của policy, hoặc gid trên thư mục backups/recovery, không có group trên host
  này. Group không bao giờ được tạo; hãy chọn group có sẵn trong `pf permissions apply`.
- `permission-policy-unsupported`: lựa chọn hợp lệ nhưng control này không kích hoạt (chạy script "none" cho
  workspace).
- `permission-approval-invalid`: `permission-policy.json` không phải file thuộc root, `0600`, một link, hoặc sai
  schema hay chuỗi revision. Nó không bao giờ được thay tự động và không dùng derived policy: backup và các lệnh
  permission dừng lại. Cách xử lý bằng tay: với quyền root, chuyển nó sang tên khác trong cùng thư mục, ví dụ
  `permission-policy.invalid-<UTC>.json`; khi đó instance dùng derived policy (backup giữ group của thư mục chứa
  chúng, nên không mở rộng quyền), và `pf permissions apply` ghi revision 1 mới.
- `permissions-context-refused`: finding của protected context nằm ngoài các scope, hoặc nằm trên một thư mục phía
  trên installation root (kể cả khi nó cũng nằm phía trên một scope); `check`/`plan` không đọc scope nào. Sửa các finding được hiển thị (`pf doctor`).
- `scope-path-unsafe`, `scope-entry-link`, `scope-entry-special`, `scope-entry-hardlinked`,
  `scope-mount-boundary`, `scope-contains-app-storage`, `scope-untrusted-owner`, `scope-entry-acl`,
  `scope-too-large`: blocker; `apply` không đổi gì khi còn blocker. Gỡ link, special file, hard link hay ACL, chuyển
  mount hoặc application storage ra khỏi thư mục, hoặc bỏ chọn scope bằng `--scope`.
- `control-ceiling`: control release đã cài vượt ceiling; nó chỉ được đổi bởi `pf install control`.
- `editor-freeze-unavailable` / `editor-freeze-refused`: xem mục 2 (Editor freeze).
- `permissions-blocked`, `permissions-cancelled`, `permissions-current`, `permissions-changed-before-apply`: không
  thay đổi gì; chạy lại lệnh để xem lại.
- `permissions-apply-pending`: một lần apply bị gián đoạn đang mở; mọi lệnh mutating khác (kể cả `backup` theo
  lịch) bị từ chối cho tới `pf permissions apply --resume` hoặc `--abandon`.
- `permissions-entry-changed`, `permissions-verify-failed`, `permissions-interrupted`: apply **chưa hoàn tất**;
  `--resume` lập lại plan cho các thay đổi còn lại với policy đã đóng băng, `--abandon` bù trừ các thay đổi đã ghi
  theo thứ tự ngược.
- `permissions-already-approved`: lần apply bị gián đoạn đã ghi revision của nó; chỉ `--resume` mới hoàn tất được.
- `permissions-abandon-conflicts` (exit 1): object bị đổi sau khi gián đoạn được để nguyên và liệt kê.
- `permissions-journal-invalid`: một dòng effect journal không đọc được; không thay đổi gì và journal vẫn mở. Hãy
  xem `<root>/instances/<uuid>/operations/<op-id>/permission-effects.jsonl` bằng tay.
- `fresh-entry-acl`: entry backup hoặc recovery mới kế thừa ACL từ thư mục chứa nó; thao tác dừng trước khi
  publish. Hãy gỡ default ACL khỏi thư mục đó. Trong workspace hoặc configuration, entry mới giữ mode lúc được tạo
  (một note).
- `permissions-nothing-pending`: `apply --resume` hoặc `--abandon` không tìm thấy apply nào bị gián đoạn; không
  thay đổi gì. Chạy `pf permissions check` để xem trạng thái hiện tại.
- `permissions-option-invalid` (exit 2): `--resume` và `--abandon` làm việc trên plan đã đóng băng của apply bị
  gián đoạn, nên không kết hợp được với `--scope`. Chạy lại lệnh mà không có `--scope`.
- `permission-policy-unapproved` (note): chưa có policy nào được duyệt; derived policy đang có hiệu lực (group của
  backups và recovery lấy từ thư mục của chúng, group của workspace và configuration lấy từ `pf-config.json`). Chạy
  `pf permissions apply` để duyệt một policy.
- `permission-entry-unplanned` (note): một entry xuất hiện sau khi plan được hiển thị (trong scope đã fence: sau
  inventory chính thức, vốn đã đặt target cho các entry mới trước đó) và được giữ nguyên. Chạy
  `pf permissions check` và apply lại nếu nó khác target.
- `permission-entry-gone` (note): một entry của plan đã biến mất trước khi được verify; không cần làm gì thêm.
- `workspace-concurrent-entry` (note): người sửa tạo một entry trong workspace trong lúc một thao tác (ví dụ
  `deploy` hoặc `update`) publish các entry workspace mới; entry đó được giữ nguyên. Hãy xem lại nó, rồi chạy
  `pf permissions check --scope workspace`.
- `workspace-no-source-manifest` (note): chưa có protected source manifest, nên không file workspace nào được chỉ
  định là executable. Deploy một source (`pf deploy`/`pf update`) để ghi manifest.
- `permissions-applied`: apply đã hoàn tất và verify mọi scope được chọn; dòng này nêu revision của policy đang có
  hiệu lực.

### SMB không sửa/xóa được backup/recovery

Đúng thiết kế. Đây là recovery artifact nên group chỉ read/copy. Muốn chỉnh thì copy sang vị
trí khác.

### `sudo sh ./pf.sh ...` bị từ chối

Đúng thiết kế. Bản trong repo chỉ là source. Chạy launcher đã cài:

```sh
sudo <root>/bootstrap/pf ...
```

hoặc tạo installation root mới trước (mục 5 (a)):

```sh
sudo sh ./deploy/synology/install-control.sh init --root <root>
```

### `install-preflight-refused`

Preflight liệt kê mọi conflict cùng lúc, mỗi dòng dạng `code: subject: detail`, và không thay đổi gì.
Giải quyết mọi mục rồi chạy lại đúng lệnh đó. Các code thường gặp: `root-exists`, `init-leftover-unknown`,
`source-*`, `install-contract-incompatible`, `release-not-retained`, `release-id-collision`,
`interpreter-*`, `free-space`, `registry-*`, `slug-*`, `project-*`, `daemon-*`, `registered-path-missing`,
`path-*`, `storage-replaceable`, `acl-*`, `admin-config-*`, `group-missing`, `legacy-*`,
`instance-operation-pending`, `instance-effects-unresolved`, `launcher-parent-untrusted`.
`legacy-env-conflict`/`legacy-admin-config-conflict`: không bản nào được tự động chọn; giữ bản đúng trong
`config/`, chuyển bản kia ra khỏi cả hai vị trí.

### `install-operation-pending`, `legacy-control-active` hoặc `control-binding-changed`

`install-operation-pending`: một install operation đang mở (bị gián đoạn, crash hoặc `needs_operator`).
Xem bằng `sudo <root>/bootstrap/pf install status` và làm theo bước tiếp theo của nó; cho tới khi nó kết
thúc, `control` hoặc `init` đang mở từ chối route mutating của mọi instance, còn `register`/`migrate-legacy`
đang mở từ chối chính instance của nó. `legacy-control-active`: instance được migrate từ v2.5 và `control/`
v2.5 của nó vẫn còn; tiếp tục dùng v2.5 để thay đổi cho tới khi có legacy adoption.
`control-binding-changed`: một lần cài control đã hoàn tất trong lúc lệnh đang khởi động; không thay đổi
gì, hãy chạy lại.

### `install-needs-operator`, `bootstrap-change-unsupported` hoặc `install-busy`

`install-needs-operator`: resume gặp một target ở trạng thái mà installer không ghi; thông báo nêu target,
các hash kỳ vọng và thứ đã gặp, và journal ghi lại bằng chứng (với `.env` thông báo nêu bản copy dự kiến
và thứ đã gặp, không bao giờ nêu hash của file). Khôi phục target, rồi chạy `pf install resume` (hoặc
`pf install resume --abandon` khi được đề xuất). Global launcher do writer khác tạo trong lúc đó không bao giờ
là lý do: resume để nguyên nó với note `launcher-left`. `bootstrap-change-unsupported`:
candidate thay đổi launcher hoặc verifier; cài vào root mới hoặc giữ nguyên các byte đó. `install-busy`: một
operation khác đang giữ lock cần dùng (registry, instance, v2.5 hoặc thư mục build của init); không thay đổi
gì, thử lại khi nó xong.

### `install-smoke-failed`, `install-verify-failed` hoặc `abandon-not-possible`

`install-smoke-failed`: candidate không qua smoke check (ví dụ từ chối cấu hình live của một instance); nó
không được kích hoạt và staging của nó đã bị xóa. `install-verify-failed`: với `control`, binding trước đó
được khôi phục (`rolled_back`); với kind khác, operation ở lại `needs_operator` kèm bước tiếp theo.
`abandon-not-possible`: một `init` đã publish root, hoặc một registration đã được dùng (đã đặt default, đã
ghi operation, record đã đổi, lock đang bị giữ), không thể abandon; hoàn tất nó bằng `pf install resume`.

### `install-operation-unreadable` hoặc `install-interrupted`

`install-operation-unreadable`: một entry trong `<root>/install-operations/` không phải install operation
đọc được (ví dụ một thư mục lạc). Nó từ chối mọi route (fail closed) và `resume` không thể tiếp tục nó. Kiểm
tra nó bằng root và chuyển nó ra khỏi `install-operations/` (giữ một bản copy: journal của operation là bằng
chứng), rồi chạy lại lệnh. `install status` và thông báo gate nêu cùng bước đó. `install-interrupted`:
operation dừng sau commit point hoặc trong lúc tự khôi phục; nó vẫn mở ở phase mà thông báo nêu. Chạy
`pf install resume`. Khi thông báo nói operation đã cancelled nhưng việc dọn dẹp bị gián đoạn thì không có
gì khác bị thay đổi: phần còn lại nằm trong thư mục operation của nó hoặc được nhận ra ở lần chạy kế tiếp
của cùng lệnh.

### Từ chối và note của `pf config`

Mỗi lần từ chối dưới đây không thay đổi gì; thông báo nêu lệnh tiếp theo.

- `config-option-invalid` (exit 2): `--configuration [--project]` chỉ là dạng pre-registration của
  `config admin`; không được kết hợp với `--instance` hoặc dùng với `config app`, và `--project` cần
  `--configuration`.
- `config-cancelled`: `q`, hết input, Ctrl-C hoặc bất kỳ câu trả lời nào ngoài `y`/`yes` ở `Write …? [y/N]`.
  Password đã sinh bị bỏ đi.
- `config-current` (exit 0): không có gì để đổi. Các note phía sau vẫn có giá trị.
- `admin-config-required`: `config app` cần `pf-config.json` hợp lệ; chạy `config admin` trước.
- `admin-config-invalid`: file có lỗi parse, key, kiểu hoặc rule (hiển thị lỗi đầu tiên, kèm số lỗi còn lại).
  Sửa bằng tay; wizard không bao giờ sửa file. Trước khi đăng ký, copy lặp lại `--configuration <dir>` (và
  `--project`) để lần chạy sau kiểm tra lại đúng thư mục đó.
- `admin-config-version-unsupported`: file khai báo `schema_version` khác. Control mới hơn đã ghi file này:
  chọn lại control đó (`pf install control --release <id>`) hoặc khôi phục file trước đó.
- `admin-config-migration-blocked`: một explicit value của schema 1 không hợp lệ trong schema 2; sửa bằng tay.
- `admin-config-mismatch`: `project` khác registration, hoặc `environment` khác approved policy. Khôi phục giá
  trị đã đăng ký; đổi policy là một approval riêng.
- `admin-config-legacy-unregistered` (exit 0): file schema 1 trong thư mục chưa đăng ký được giữ nguyên; đăng
  ký trước, rồi chạy `config admin` với `--instance`.
- `admin-example-invalid`, `app-example-invalid`: example đã cài không dùng được; chạy `doctor` và cài lại
  control release.
- `app-config-invalid`: `.env` không parse được (key lạ hoặc trùng, `export`, quote, ký tự xuống dòng).
  Thông báo nêu số dòng; không bao giờ hiện ký tự nào của giá trị (escape không hỗ trợ, UTF-8 không hợp lệ
  hoặc ký tự điều khiển chỉ được mô tả, không được in ra).
- `app-credential-unusable`: instance được tính là đã deploy và một credential bị thiếu hoặc không hợp lệ; khôi
  phục nó từ recovery bundle hoặc hồ sơ của bạn.
- `app-profile-undeclared`: profile của instance không khai báo application variable nào trong control này.
- `migration-issue`: giá trị hiện có không render literal được (dấu nháy đơn, backslash cuối, ký tự điều khiển)
  hoặc password ngắn hơn 4 ký tự; sửa file bằng tay.
- `zone-data-unavailable`: host không có zone data để kiểm tra `SITE_TIMEZONE` mới; cài zone data hoặc ghi giá
  trị bằng tay. Ở dạng note: giá trị hiện có đã được giữ.
- `config-file-acl`: file có ACL entry; áp dụng bằng tay các thay đổi được liệt kê (PF-A2.3).
- `config-file-unsafe`: file là link, file đặc biệt hoặc có hard link, hoặc thư mục chứa một tên tạm dành riêng
  nhưng không phải phần còn sót của lần chạy bị gián đoạn; hãy kiểm tra, không có gì bị xóa.
- `config-changed`: file đã đổi trong lúc wizard chạy; chạy lại lệnh để xem file mới.
- `config-interrupted` (exit 1): một interrupt (Ctrl-C, `SIGTERM`, `SIGHUP` khi SSH bị ngắt) đến trong lúc
  publish file, và file đã chứa nội dung mới. Không có gì khác bị đổi và không ghi change record; chạy lại lệnh
  để xem file (lệnh sẽ báo `config-current`). Interrupt trước khi publish là `config-cancelled` và file giữ
  nguyên.
- `config-audit-not-recorded` (exit 1): file đã được ghi (thông báo nói file còn chứa nội dung mới hay không),
  nhưng việc ghi `config-change.json` bị lỗi hoặc bị gián đoạn. Bản thân thay đổi đã hoàn tất; chạy lại lệnh để
  xem file.
- `config-busy`: một registration khác đang giữ registry lock; thử lại.
- `config-dir-invalid`, `config-path-conflict`: thư mục pre-registration bị thiếu, không canonical, nằm trong
  ancestor có thể bị thay thế, hoặc đang được instance đã đăng ký sử dụng (khi đó dùng
  `--instance <slug> config admin`).
- `registry-record-invalid`, `registry-invalid` (pre-registration): một instance record đã đăng ký (hoặc
  registry) không load được, nên wizard không biết thư mục có thuộc về nó hay không; không hỏi và không tạo gì.
  Hãy sửa record trước (giống như với `pf install register`).
- `config-audit-invalid`: lỗi nội bộ; change record không qua schema và không có gì được ghi.
- Note: `config-temp-removed` (đã xóa file tạm còn sót của lần chạy bị gián đoạn), `zone-unknown-on-host`,
  `password-weak-for-new-deployment`, `implicit-materialized`.

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

`backup` hoặc `release-check` chạy không có terminal và instance được chọn qua
default được bảo vệ hoặc vì là registration duy nhất. Lệnh không người trực phải nêu instance:
`pf --instance <slug|uuid> <command>`. Không có gì bị thay đổi.

### `policy-grant-required` hoặc `auto-apply-not-permitted` (exit 20)

`backup` hoặc `release-check` chạy không người trực, hoặc mọi `release-check --apply`,
cần một protected policy cho phép đúng loại thao tác đó. Ở checkpoint này không policy nào cấp
(grant có từ PF-A4.3); `auto_update` trong `pf-config.json` không cấp được. Hãy chạy lệnh tương tác,
và apply release bằng tay với `pf --instance <slug> update --release <tag>`. Không có gì bị thay
đổi.

### `selection-conflict`, `recovery-outside-instance` hoặc `recovery-state-file-refused`

`selection-conflict`: `--project` nêu project khác với instance đã chọn bằng `--instance`; chỉ dùng
`--instance`. `recovery-outside-instance`: bundle không phải thư mục nằm trong
`recovery/<project>/` của chính instance đã chọn; bundle của instance khác, bản sao ở nơi khác và
link trỏ tới chúng không bao giờ được liệt kê hay restore. `recovery-state-file-refused`: manifest
của bundle liệt kê state file khác `deployed.json`, `last-reset.json` hoặc `observed-tags.json`,
danh sách state file không phải là một list, hoặc liệt kê một state file không có payload `state/<name>` đã
verify (bundle legacy khi đó báo `manifest-schema-unsupported`). Không có gì bị thay đổi.

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

### Code của lifecycle bundle và deployment record (PF-A3.1)

Refusal trước mọi tác động kết thúc bằng `Nothing was changed.`; refusal muộn hơn nêu trạng thái thực tế.

- `manifest-checksum-mismatch`, `manifest-invalid`, `manifest-schema-unsupported`, `bundle-payload-mismatch`,
  `bundle-unlisted-file`: thư mục bundle đã bị sửa, hỏng hoặc được ghi bởi control không được hỗ trợ. Đừng sửa hay
  "vá" nó; copy ra ngoài NAS làm bằng chứng và dùng checkpoint hay recovery bundle khác. `pf backups` và
  `pf recoveries` liệt kê thư mục như vậy là `[invalid: <code>]` (thư mục có hơn 20000 entry là
  `[invalid: bundle-unlisted-file]`). Bundle copy ngược từ ngoài NAS về phải giống từng byte (không thêm file như
  `.DS_Store` hay `Thumbs.db`).
- `archive-member-refused`, `archive-unreadable`, `archive-changed`, `archive-capacity`: archive payload chứa link,
  special file, path không an toàn hay trùng, vượt giới hạn của importer (header PAX hay GNU long-name lớn hơn 64 KiB
  bị từ chối trước khi được đọc), thay đổi trong lúc đọc, hoặc không đủ chỗ; không có gì được extract. Với
  `archive-capacity` hãy giải phóng dung lượng rồi chạy lại.
- `workspace-archive-limit`: workspace có thể sửa khác deployed source và chứa file lớn hơn 128 MiB, tổng cộng hơn
  512 MiB hay 200000 file, hoặc path mà archive không chứa được. `pf backup`, `update`, `reset-db`, `purge` và
  `rollback` từ chối ngay ở preflight, trước mọi xác nhận hay pause (ứng dụng vẫn chạy): capture của chúng không bao
  giờ tiếp tục khi thiếu workspace bị lệch, thứ mà một lần thay source sau đó sẽ xóa mất. Chuyển các file đó ra khỏi
  repository workspace rồi chạy lại; trong lúc đó `pf --instance <slug> backup --emergency` giữ database và ghi
  workspace là excluded.
- `source-manifest-mismatch`: source đã extract khác source manifest đã ghi; dùng checkpoint khác.
- `checkpoint-not-rollback-target`: checkpoint được chọn là emergency hay partial capture. Restore data của nó thủ
  công hoặc export từ nó; chọn healthy checkpoint cho `rollback`.
- `checkpoint-incompatible`: candidate của `--restore-db` không qua một kiểm tra (locale, extension, owner, head hay
  row count); candidate đã bị drop và database hiện tại không đổi.
- `preservation-failed`: rollback không capture và restore-test được database hiện tại; không có gì được restore hay
  switch và service vẫn dừng. Preserve database thủ công — một `pg_dump --format=custom` của database được nêu tên từ
  service `db` vào chỗ được bảo vệ, chỉ root đọc được — hoặc sửa nguyên nhân rồi chạy
  `pf --instance <slug> backup --emergency`; sau đó chạy lại, hoặc chạy `pf --instance <slug> resume` để mở lại
  deployment không đổi. Khi rollback đã đi qua điểm đó (`resume` không còn là route hợp lệ), hãy rollback lại về một
  healthy checkpoint với `--restore-db`: preservation capture mà journal ghi có thể là emergency hay partial capture,
  vốn là bằng chứng và data, không bao giờ là rollback target.
- `deployment-artifact-mismatch`: một file của deployment record hiện tại khác record. **Đừng xóa thư mục**; giữ làm
  bằng chứng. Ở dạng note, capture đã tiếp tục với source từ protected source store hoặc workspace đã chứng minh và
  lần `update` kế tiếp seal record mới. Ở dạng refusal, deployed source không chứng minh được: khôi phục protected
  source store, hoặc rollback về một healthy checkpoint (rollback preserve data hiện tại trước).
- `deployment-image-mismatch`: backend/frontend image đang chạy không phải của deployment hiện tại, nên healthy
  checkpoint sẽ gắn sai image. Tìm ai đã đổi container, preserve data bằng `pf backup --emergency` nếu cần, rồi
  deploy lại hoặc rollback. Sau pause, thông báo nêu trạng thái đã dừng và `pf resume` mở lại deployment không đổi.
- `deployment-stage-failed`: không stage được deployment; application, database và workspace không đổi. Sửa theo
  chi tiết (thường là dung lượng hay permission trong `artifacts/deployments`) rồi chạy lại.
- `deployment-record-incomplete`: application đã được activate và healthy, nhưng record không seal được. `status` báo
  `Deployment: not recorded (…)`; trong lúc đó capture dùng bằng chứng từ protected store hay workspace, và khi không
  chứng minh được commit nào thì healthy checkpoint và `update` từ chối trong preflight — dùng `rollback` (nó lùi về
  emergency preservation) hoặc `backup --emergency`. Lần deploy, update, rollback hay restore-instance kế tiếp seal
  record.
- `artifact-capacity`: deployment artifact store cần kích thước source cộng 64 MiB; giải phóng dung lượng rồi chạy
  lại. Không có gì trong PF-A3.1 xóa deployment artifact (retention là PF-A5.1).
- `purge-bundle-unverified`: purge bundle không có record `data_restore_verified` passed cho manifest của nó; việc
  xóa bị chặn và application được mở lại. Chạy lại `purge` sau khi sửa lỗi restore được in trước đó.
- Note: `verification-record-invalid` (verification record hỏng bị bỏ qua; level lùi lại), `legacy-manifest-migrated`
  (bundle cũ được đọc qua migration), `db-image-changed` (PostgreSQL image khác nhưng cùng major),
  `verification-failed` (code-only rollback của checkpoint có restore test gần nhất lỗi; dump không được dùng).

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
sudo pf --instance <slug> status
sudo pf --instance <slug> status --operation <operation-id>
```

`status` in, trước mọi kiểm tra live, một khối cho mỗi operation đang mở:

```text
Operations: <op> <kind> phase <phase> sequence <n> updated <stamp> [blocking|needs operator]
  last error: <code>: <message>
  unresolved effect: <eid> <type> <target> (<state>)
  next (recorded at sequence <n>): <command>: <description>; ...
```

tiếp theo là `Recent operations:`, `Workspace generations:` và `Runner effects:` khi có. `status --operation` liệt kê
mọi effect với trạng thái và evidence, các attempt và child process đã ghi nhận (không có secret, không có giá trị đã
đóng băng). Chỉ dùng các bước kế tiếp được in ra. **Không bao giờ xóa hay sửa journal**: thư mục operation là bằng
chứng và plan của nó là sự phê duyệt.

Các code:

- `operation-open` — một lệnh không phải bước kế tiếp hợp lệ của operation đang mở; thông báo liệt kê các bước hợp lệ.
- `operation-needs-operator` / `effect-unknown` / `database-switch-unknown` — không có cách tự động tiếp tục an toàn;
  dùng route khôi phục được in ra (`rollback <checkpoint> --restore-db` cho update, rollback hoặc reset, `abort-deploy`
  cho lần deploy đầu, `backup --emergency` để preserve data trước).
- `effect-still-running` — một child process group đã ghi nhận, một one-off container của instance hoặc một database
  session vẫn chạy; `resume` không bao giờ dừng nó. Chờ rồi chạy lại `resume`. Với `resume --operation <op>
  --acknowledge` chỉ one-off container của instance và session trên các database mà record nêu được tính (mục 15). `effect-probe-unavailable` — có process
  đã ghi nhận nhưng không đọc được `/proc` hoặc boot ID ở đây; không quyết định gì khi thiếu probe đó.
- `database-unavailable` — `resume` không khởi động được database service để quan sát một effect; operation không đổi.
- `plan-authority-changed` — instance record, policy, control release, profile hoặc daemon đã đổi sau khi duyệt; khôi
  phục trạng thái đã duyệt hoặc nhờ administrator review. `plan-input-changed` — một input đã đóng băng (bundle,
  checkpoint, `app.env` hoặc `admin-config.json` đã đóng băng, deployment đã stage) đã thay đổi; khôi phục nó đúng từng
  byte. `plan-inputs-conflict` — một alias yêu cầu lựa chọn khác với lựa chọn đã duyệt.
- `abandon-not-legal` (một live effect đã bắt đầu), `abandon-in-progress` (chỉ `resume --abandon` tiếp tục một restore
  abandon đã được chấp nhận), `keep-workspace-not-legal` (workspace mới đã được bind). Khi `--abandon` hợp lệ trước một
  data effect, nó làm đúng việc `resume` làm ở đó rồi hủy: candidate database của operation được drop, store không cho
  kết nối của purge được đóng lại và deployment không đổi được mở lại (rollback superseding thì withdraw).
- Các từ chối phát hiện khi quan sát (`workspace-generation-mismatch`, `checkpoint-history-unknown`,
  `keep-workspace-not-legal`, `plan-input-changed`) được quyết định trước xác nhận gõ tay: không ghi attempt hay
  approval nào và các file của operation không đổi.
- `workspace-generation-mismatch` — `repo/`, retained generation hoặc stage không phải thứ journal đã ghi; không có gì bị
  di chuyển. Khôi phục trạng thái đã ghi (ví dụ xóa thư mục được tạo ở `repo/` trong lúc đó) rồi chạy `resume`, hoặc
  `resume --keep-workspace`. `workspace-validation-failed` — workspace đã bind không validate; sửa finding rồi chạy
  `resume`. `checkpoint-history-unknown` — chuyển cây lạ sang chỗ khác bằng root rồi `resume`.
- `journal-changed` — một writer khác đã đổi journal; process đã dừng. Chạy `status`.
- `operation-conflict` — có hơn một operation đang mở (không phải cặp được phép: một backup cạnh một update đang chờ ở
  `workspace_sync_pending`); mọi route thay đổi bị từ chối cho tới khi administrator review bằng `status --operation`.
  Với cặp được phép, `resume` không có `--operation` nêu tên backup: hoàn tất hoặc abandon nó bằng `resume --operation
  <backup-op> [--abandon]` trước; operation đang chờ được giữ nguyên tới lúc đó (mục 8).
- `operation-journal-invalid` / `journal-format-unsupported` — journal hoặc plan không validate, hoặc có
  `state/pending.json` không phải permission apply (chỉ một lần đổi control không được hỗ trợ mới ghi ra nó). Mọi route
  thay đổi bị từ chối; `status`, `doctor`, `backups`, `recoveries`, `ps` và `logs` vẫn chạy. Giữ nguyên thư mục, so
  sánh với `status --operation` và liên hệ người phụ trách control trước khi di chuyển bất cứ thứ gì.
- `operation-index-overflow` — hơn 20000 entry trong `operations/`. Lưu trữ các operation **đã đóng** (`completed`,
  `cancelled`, `failed_preserved`, như `status --operation` hiển thị) bằng root sang thư mục ngoài `operations/`, không
  bao giờ là operation đang mở hay bị supersede; retention thuộc PF-A5.1.
- Purge ở `deleting` mà recovery bundle không còn đọc hoặc verify được (`plan-input-changed: recovery bundle ... no
  longer reads or verifies`) giữ deletion bị chặn: khôi phục thư mục bundle đúng từng byte từ bản sao ngoài NAS rồi
  chạy `resume`.
- Trong khoảng workspace switch chỉ `resume` và `resume --keep-workspace` được chạy (mục 8).

### Mã của integrated operation (PF-A3.3)

- `isolated-topology-present` — một stack `pfverify-*` được giữ lại hoặc một recovery target `pfrecover-*` của instance
  này vẫn còn; chạy `pf cleanup --apply` (thêm `--recovery-target <project>` cho recovery target), rồi chạy lại instance
  purge.
- `verification-isolation-unsupported` — isolated model không render hoặc validate được trên host này (hoặc legacy
  bundle không có image dùng được); không có gì bị xóa. `topology-name-collision` — chạy lại lệnh.
- `functional-verification-failed` — một kiểm tra của isolated verification thất bại (thông báo nêu tên kiểm tra);
  instance purge bị hủy và application được mở lại, hoặc side-by-side restore đóng ở `failed_preserved`. Topology đã
  dừng được giữ để xem xét; xóa bằng `pf cleanup --apply`.
- `app-check-failed` — lệnh application invariant lỗi hoặc cho report không đọc được trên database nguồn; không có gì
  bị xóa. Output của lệnh không bao giờ được lưu hay in ra (chỉ số lượng theo từng kiểm tra). Lệnh chạy trong giới hạn
  mười phút của runner cho mỗi child; database cần lâu hơn sẽ dừng instance purge theo cùng cách.
- `purge-bundle-unverified`, `purge-writers-running`, `purge-source-changed` — deletion gate từ chối sau `ERASE`;
  application được mở lại (hoặc, khi chính việc mở lại không thể tiến hành, purge vẫn mở để `resume`/`--abandon`).
- `restore-target-mismatch` — bundle thuộc instance khác (mục 12). `instance-claim-taken` — một instance đã đăng ký
  khác đang giữ project của record đã purged này. `registry-busy` — một installation transaction đang giữ registry lock;
  chạy `resume` sau khi nó kết thúc.
- `image-load-would-retag` — nạp `images.tar` của bundle sẽ dời một tag đang trỏ tới image khác ở đây; không nạp gì.
  Hãy xóa hoặc đổi tên tag đó trước.
- `recovery-target-lost` — một side-by-side target bị thay đổi trong lúc operation của nó bị gián đoạn; nó đã bị xóa.
  Chạy lại side-by-side restore.
- `reset-contract-unknown`, `reset-deployment-image-mismatch`, `preservation-failed` — mục 11 và "Hủy lần deploy đầu
  tiên sau khi frontend đã mở truy cập".
- `effect-still-running`, `acknowledge-not-legal` — mục 15.

### Từ chối vì capacity

`capacity-insufficient: <phase> needs <n> MiB on device <d> (<roles>: <paths>), <m> MiB free including the <f> MiB
safety floor` nêu mọi role dùng chung device bị thiếu. Giải phóng chỗ trên device đó (history `.pre-restore-*` cũ, file
đã export, hoặc phần thừa của `pf cleanup --apply`); không bao giờ xóa healthy checkpoint cuối cùng hay purge bundle để
lấy chỗ. Docker root được dùng chung bởi mọi instance trên daemon, nên image và volume của instance khác cũng tính ở đó.
Nhu cầu trên recovery device của instance purge gồm cả image archive của final bundle (kích thước `docker image inspect`
của các tag được lưu và database image); image không có kích thước bị từ chối với `capacity-unmeasurable`.
`capacity-unmeasurable` nghĩa là daemon không báo `DockerRootDir` dùng được hoặc không đo được nó; `pf backup
--emergency` vẫn bảo toàn database. Hạ `minimum_free_mb` là thay đổi admin configuration, không phải route được khuyến
nghị.

### Dọn phần thừa

`sudo pf cleanup` chỉ đọc (lock observe-only, không tạo thư mục operation): nó liệt kê phần thừa mà các operation **đã
đóng** ghi nhận: candidate `pf_verify_*`, `pf_migrate_*`, `pf_restore_*` và `pf_clean_*`, topology `pfverify-*` được giữ
lại, thư mục bundle-attempt và image tag của chúng, stage bị supersede, và các mục chỉ xóa theo selector. Một tên chỉ
khớp prefix không bao giờ là căn cứ: database không operation nào ghi nhận thì không được liệt kê và không bị động tới.
`sudo pf cleanup --apply` xóa tập mặc định sau `CLEANUP <project> <op8>` dưới dạng một operation `cleanup` có journal;
mỗi mục được quan sát lại ngay trước khi xóa và được giữ khi đã thay đổi (`cleanup-item-changed`) hoặc đang bận
(`cleanup-item-busy`); cleanup sau đó tiếp tục và đóng ở `failed_preserved` (`cleanup-items-kept`). Các mục chỉ xóa theo
selector cần selector và câu xác nhận riêng:

- `--recovery-target <project>` (`REMOVE RECOVERY TARGET <project>`);
- `--checkpoint-history <name>` (`DELETE CHECKPOINT HISTORY <name>`): một history `.pre-restore-*` bị dời chỗ, chỉ khi
  mọi checkpoint trong đó cũng có trong active history với cùng manifest và bản active đó đọc strict được (hash của
  payload), và history không chứa gì khác (nếu không: `cleanup-history-unique-checkpoint`, nêu checkpoint hoặc entry
  kia; thư mục bị xóa nguyên khối);
- `--generation <wsg-…>`: một retained workspace generation (mục con tiếp theo).

Không bao giờ dọn: database giữ lại `pf_keep_*`, database `pf_recovery_*` của chế độ side-by-side cũ, database đang
hoạt động, tên thuộc operation đang mở, checkpoint đã seal, purge bundle, deployment đã seal, `unsealed-active-staging`
và image tag được nạp bởi một restore bị abandon. `cleanup-nothing` và `cleanup-target-unknown` không thay đổi gì.

### Gỡ một retained workspace generation

`sudo pf cleanup --apply --generation wsg-<…>` seal generation trước khi xóa: trước hết đóng mọi editor hay SMB session
đang mở file trong đó (handle đang mở bị từ chối với `generation-in-use`; `/proc` không đọc được thì
`generation-handles-unverifiable`). Cây được archive vào `backups/generations/<project>/<generation>/workspace.tar.gz`
kèm `seal.json` (hash của cây và của archive); link hoặc file đặc biệt bên trong bị từ chối với
`generation-unsupported-entry`, cây thay đổi trong lúc seal thì `generation-unstable`, và ghi sau khi seal thì giữ cây
(`cleanup-item-changed`). Seal hợp lệ của một lần cleanup trước không bao giờ bị thay thế: khi cây vẫn có đúng nội dung đã
seal thì seal đó được dùng lại và cây bị xóa; khi cây đã đổi từ đó (ví dụ sau một lần xóa bị gián đoạn) thì giữ cả hai với
`generation-seal-exists` — archive trước có thể là bản đầy đủ duy nhất. So sánh hai bản và chỉ xóa thư mục generation bằng
tay khi không còn cần gì trong đó. Để lấy lại một file, giải nén nó từ archive với quyền root ra một vị trí ngoài `repo/`, ví
dụ `sudo tar -xzf <archive> -C /tmp/restore-<generation> <path>`.

## 17. Command reference

| Command | Mục đích |
| --- | --- |
| `sudo pf doctor` | Validate host tool, control security, Compose, env và capacity cơ bản |
| `sudo pf permissions check [--scope S]…` | So sánh mọi scope với permission policy đang hiệu lực (read-only; exit 1 khi khác biệt) |
| `sudo pf permissions plan [--scope S]… [--details]` | Xem trước group, thành viên, số lượng và plan hash (read-only) |
| `sudo pf permissions apply [--scope S]… [--details]` | Wizard, `APPLY PERMISSIONS <slug>`, apply có fence và được verify; ghi permission policy revision tiếp theo |
| `sudo pf permissions apply --resume \| --abandon` | Hoàn tất (`RESUME PERMISSIONS <slug>`) hoặc bù trừ (`ABANDON PERMISSIONS <slug>`) một lần apply bị gián đoạn |
| `sudo pf status` | Deployed revision, workspace drift, DB revision, container, pending operation |
| `sudo pf deploy --latest` | Brand-new staging từ latest configured branch SHA |
| `sudo pf deploy --commit FULL_SHA` | Brand-new deploy từ exact commit |
| `sudo pf deploy --release TAG` | Brand-new deploy từ published release |
| `sudo pf abort-deploy` | Xóa incomplete first deploy (xác nhận `ABORT DEPLOY <project>`; sau khi frontend đã mở, database hiện tại được bảo toàn trước; abort bị gián đoạn tiếp tục bằng `RESUME ABORT DEPLOY <project>`) |
| `sudo pf update --latest` | Managed staging update theo latest branch SHA |
| `sudo pf update --commit FULL_SHA` | Update tới exact commit |
| `sudo pf update --release TAG` | Update tới release |
| `sudo pf backup` | Tạo và restore-test healthy revision checkpoint (in class và level) |
| `sudo pf backup --emergency` | Giữ data thực tế khi healthy checkpoint bị từ chối (`EMERGENCY BACKUP <project>`; chỉ với terminal; không bao giờ là rollback target) |
| `sudo pf backups --page N` | List checkpoint, 10/trang: `[class\|level]`, reason, DB head, source provenance, legacy format |
| `sudo pf rollback [BACKUP_ID]` | Code rollback, giữ current DB |
| `sudo pf rollback BACKUP_ID --restore-db` | Restore code + selected database state |
| `sudo pf reset-db` | Kích hoạt clean migrated DB, vẫn giữ recoverability |
| `sudo pf instances` | List managed PartFlow instances |
| `sudo pf purge [--project NAME]` | Full recoverable purge một staging instance |
| `sudo pf recoveries` | List purge recovery bundles: `[level]`, project, active database, source provenance, `derived_from` |
| `sudo pf restore-instance RECOVERY_ID` | Dựng lại functional instance đã purge |
| `sudo pf restore-instance RECOVERY_ID --side-by-side` | Restore một bundle của chính instance vào một recovery target `pfrecover-*` cách ly, được giữ lại bên cạnh nó (`RESTORE COPY <bundle-id>`) |
| `sudo pf release-check` | Check eligible release, không apply |
| `sudo pf release-check --apply` | Bị từ chối ở checkpoint này (exit 20): cần grant trong protected policy (PF-A4.3) |
| `sudo pf resume [--operation ID]` | Vào lại operation đang mở theo journal: mở lại không đổi, tiếp tục về phía trước hoặc dừng ở `needs_operator` (`RESUME <op8>`) |
| `sudo pf resume --abandon` | Hủy operation khi hợp lệ (trước data effect: drop candidate của operation, mở lại deployment không đổi, `ABANDON <op8>`; restore khi target đang được chuẩn bị: `ABANDON RESTORE <project> <op8>`) |
| `sudo pf resume --keep-workspace` | Hoàn tất operation mà không refresh `repo/` (`KEEP WORKSPACE <op8>`) |
| `sudo pf status --operation ID` | Chi tiết một operation: effect, evidence, attempt, child |
| `sudo pf resume --operation ID --acknowledge` | Acknowledge runner record của thư mục operation không có journal (`ACKNOWLEDGE <op8>`; mục 15) |
| `sudo pf cleanup` | Báo cáo phần thừa đã ghi nhận của các operation đã đóng (chỉ đọc) |
| `sudo pf cleanup --apply` | Xóa tập phần thừa mặc định (`CLEANUP <project> <op8>`; mục 16) |
| `sudo pf cleanup --apply --recovery-target P \| --checkpoint-history NAME \| --generation WSG` | Xóa thêm một mục chỉ xóa theo selector (câu xác nhận riêng) |
| `... deploy\|update\|rollback\|restore-instance --keep-workspace` | Chạy không có workspace generation switch |
| `sudo pf ps [options] [SERVICE...]` | View container Compose read-only của instance (mục 14) |
| `sudo pf logs [options] [SERVICE...]` | Log service có giới hạn và được redact (mục 14) |
| `sudo sh ./deploy/synology/install-control.sh init --root <root>` | Khởi tạo protected installation root mới (mục 5 (a)) |
| `sudo <root>/bootstrap/pf install status` | Liệt kê install operation, phase và bước tiếp theo (read-only) |
| `sudo <root>/bootstrap/pf install register …` | Đăng ký instance với các thư mục đã có (`REGISTER <slug>`) |
| `sudo <root>/bootstrap/pf install migrate-legacy …` | Copy cấu hình v2.5 và đăng ký; v2.5 vẫn là control plane (`MIGRATE <slug>`) |
| `sudo <root>/bootstrap/pf install control --source DIR \| --release ID` | Cài hoặc chọn control release (`INSTALL CONTROL`/`SELECT CONTROL <id>`) |
| `sudo <root>/bootstrap/pf install resume [--operation ID] [--abandon]` | Tiếp tục hoặc abandon install operation đang mở (`RESUME`/`ABANDON <op>`) |
| `sudo <root>/bootstrap/pf config admin --configuration DIR [--project P]` | Tạo hoặc hoàn thiện `pf-config.json` trước registration (không có `--instance`; chỉ schema 2) |
| `sudo pf --instance <slug> config admin` | Tạo, migrate (schema 1 → 2) hoặc hoàn thiện `pf-config.json` của instance (`[y/N]`) |
| `sudo pf --instance <slug> config app` | Tạo hoặc hoàn thiện `.env` của instance theo profile của nó (`[y/N]`) |

Lệnh khởi động không có terminal phải truyền `--instance <slug|uuid>`; cho tới khi có grant
PF-A4.3, mọi lệnh có lock đều bị từ chối khi không có terminal (`terminal-required`,
`policy-grant-required`). `pf permissions` không kèm verb bị từ chối (`permissions-verb-required`, exit 2); thao
tác sửa trước đây của nó nay là `pf permissions apply`.

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
- các label marker container của Compose dùng cho ownership chưa được hiệu chỉnh trên daemon thật *(đã được PF-A3.4 thay
  thế trên Engine 28.5.1/Compose v2.40.3: ownership inventory của pf đã phân loại container, volume và network Compose
  thật theo các label này trong mọi lần chạy; xem khối PF-A3.4)*;
- việc bao phủ image theo image ID giả định daemon không có hoạt động song song giữa `image save`
  và binding inventory.

PF-A1.4 bổ sung các giới hạn sau:

- việc phát hiện chạy không người trực dựa trên terminal (stdin không có, đã đóng hoặc không phải
  TTY); `ssh` không có `-t` được tính là không người trực;
- không thao tác không người trực nào, kể cả scheduled backup và release check, chạy trên instance
  do pf quản lý cho tới khi có grant trong protected policy của PF-A4.3;
- các label one-off của Compose `run` mà `fail_closed` dựa vào mới chỉ được chứng minh offline *(đã được PF-A3.4 thay
  thế trên Engine 28.5.1/Compose v2.40.3: label `com.docker.compose.oneoff` thật của các one-off migration và contract
  của pf đã được quan sát, và không one-off nào sống lâu hơn một lần chạy bị ngắt; xem khối PF-A3.4)*;
- không có route managed để chạy CLI của ứng dụng trong container backend.

Giới hạn của PF-A2.1:

- chỉ offline: không có gì được cài lên, hay chạy với, NAS, DSM host hoặc Docker daemon thật;
- global launcher thật `/usr/local/bin/pf` chưa được kiểm thử (test dùng launcher path cô lập);
- A2-T04 với Docker daemon thật chưa chạy (chỉ có bằng chứng identity offline);
- không khẳng định độ bền khi mất điện hoặc reboot ngoài fsync và rename trên filesystem đã kiểm thử;
- mỗi installation root một control release;
- byte của launcher và verifier bị đóng băng cho tới PF-A4.3 (`bootstrap-change-unsupported`);
- không adoption Docker resource cũ, không retire control v2.5, không import state v2.5;
- không tạo cấu hình (PF-A2.2) và không thay đổi quyền truy cập (PF-A2.3);
- release cũ và registration đã discard không được dọn dẹp (PF-A5.1).

Giới hạn của PF-A2.2:

- chỉ offline, như PF-A2.1;
- zone data của backend image không được kiểm tra (chưa có owner); backend vẫn từ chối zone không biết khi khởi
  động;
- file cấu hình có ACL entry bị từ chối và cần sửa bằng tay; race SMB giữa lần so sánh cuối của wizard và thao
  tác rename không thể đóng bằng lock;
- password cần URL encoding sẽ lỗi ở bước migration của backend cho tới khi bản sửa `env.py` của app-lane được
  deploy;
- thay đổi `backup_read_group` có hiệu lực mà không có approval gắn với revision (PF-A2.3).

Giới hạn của PF-A2.3 (permission compiler và hành vi filesystem được test offline trên filesystem Linux thật với
quyền root trong container dùng một lần; không có khẳng định nào về DSM hay SMB):

- `effective_access_verified` và `future_file_behavior_verified` không bao giờ được verify: không quan sát test SMB
  account, DSM Shared Folder permission, DSM ACL hay create mask của share nào (PF-A5.1);
- scope có ACL không thể bulk apply, không bao giờ ghi ACL, và entry mới kế thừa ACL sẽ dừng các protected flow và
  giữ mode lúc tạo trong editable scope;
- editor freeze không chặn được root service hay file được memory-map; Docker data root tùy chỉnh trên cùng
  device nằm trong một scope không được phát hiện offline;
- trước approval đầu tiên `workspace_write_group` vẫn đặt group cho file pf tạo trong editable scope; chạy script
  "none" cho workspace không kích hoạt được;
- control release được kiểm tra, không bao giờ bị đổi, và cố định ở Không truy cập qua group;
- file cấu hình có ACL vẫn bị config wizard từ chối (PF-A5.1);
- permission policy record không hợp lệ chỉ có cách xử lý bằng tay (mục 16).

Giới hạn PF-A3.1 (chỉ có bằng chứng offline và filesystem; Docker và PostgreSQL được mô phỏng):

- không khẳng định hành vi Docker, Compose hay PostgreSQL thật nào; case emergency preservation A3-T03 bị blocked ở mức
  Docker/PostgreSQL thật mà nó yêu cầu (PF-A3.4) *(đã được PF-A3.4 thay thế trên Engine 28.5.1/Compose v2.40.3: A3-T03
  passed ở mức thật)*;
- *(đã được PF-A3.3 thay thế, xem giới hạn của nó bên dưới)* instance purge từng được gate bằng record
  `data_restore_verified` từ chính payload của purge bundle; từ PF-A3.3 nó được gate bằng record
  `functional_recovery_verified` từ một isolated topology (chỉ có bằng chứng từ daemon giả);
- *(đã được PF-A3.3 thay thế)* emergency preservation từng chỉ được nối vào `rollback` và `backup --emergency`; từ
  PF-A3.3 nó còn bao phủ `reset-db` và `abort-deploy`; instance purge vẫn gate bằng healthy checkpoint và
  `restore-instance` không có dữ liệu hiện tại để bảo toàn;
- staging chưa seal và deployment bị thay thế không bao giờ được dọn (PF-A3.2/PF-A5.1); việc thay workspace vẫn làm tại
  chỗ (PF-A3.2); layer database image đã archive được dùng khi restore từ PF-A3.3;
- downgrade về control PF-A2.3 không được hỗ trợ khi còn bất kỳ bundle schema 1 nào;
- checksum chứng minh tính toàn vẹn, không chứng minh tác giả: bundle không có trust anchor nào ngoài các thư mục
  được bảo vệ, thuộc root.

Giới hạn PF-A3.2 (bằng chứng offline, filesystem và installed CLI với Docker daemon giả; không chạy gì trên DSM,
btrfs, SMB, Docker daemon hay PostgreSQL thật):

- A3-T04 và A3-T05 bị chặn ở mức Docker/PostgreSQL thật (PF-A3.4) *(đã được PF-A3.4 thay thế trên Engine 28.5.1/Compose
  v2.40.3: A3-T04 và A3-T05 đã chạy ở mức thật thành crash matrix, với các finding còn mở của khối PF-A3.4)*; phần
  DSM/btrfs/SMB của A3-T10 bị chặn (PF-A5.1); các case restart qua installed CLI chạy trên application plane mô phỏng
  của Docker daemon giả, với release source chỉ dùng cho test (xem `TEST_REPORT.md`);
- kết quả live migration bị mất mà heads không đổi không bao giờ được thử lại (`needs_operator`) cho tới khi profile
  khai báo upgrade có transaction (PF-A4.1);
- một SIGKILL giữa lúc child khởi động và lúc ghi `children.json` chỉ còn probe daemon và database cho child đó; probe
  database đếm mọi client session, nên một session thoáng qua sẽ từ chối cho tới lần `resume` sau;
- độ bền của workspace staging dựa vào fsync từng thư mục và tệp đã stage cùng generation container trước các lần
  rename (PF-A3.4: `os.sync()` toàn cục không bao giờ trả về khi một filesystem không liên quan bị treo); chưa chứng
  minh trường hợp mất điện;
- workspace root có ACL, workspace là mount point hoặc container không an toàn sẽ kết thúc update ở
  `workspace_sync_pending` cho tới khi dùng `--keep-workspace` hoặc sửa nguyên nhân;
- `restore-instance` không có abandon khi data restore đã bắt đầu; `backup --emergency` vẫn không có journal
  (*đã được PF-A3.3 thay thế*: `abort-deploy` sau khi frontend đã mở bảo toàn dữ liệu trước rồi được phép; side-by-side
  restore là operation `restore-side-by-side` có journal);
- *(đã được PF-A3.3 thay thế bằng `pf cleanup`, mục 16)* thư mục capture dang dở, `pf_verify_*` còn lại sau
  verification thất bại, stage bị supersede, checkpoint history bị dời chỗ và retained workspace generation nay được dọn
  khi có yêu cầu; image tag do restore bị abandon nạp vào vẫn chỉ được báo cáo, không bao giờ bị dọn;
- runner record của operation không có journal chặn `pf install` cho tới PF-A3.3, bản này thêm đường acknowledgement
  (mục 15); ngoài `backup --emergency`, chúng gồm mọi child lifecycle bị ngắt hoặc hết thời gian trước khi xác
  nhận (`compose build` candidate, kiểm tra contract `compose run`, `ensure_local_contract`);
- hơn 20000 entry trong `operations/` từ chối mọi route thay đổi cho tới khi các operation đã đóng được lưu trữ.

Giới hạn PF-A3.3 (bằng chứng offline, filesystem và installed CLI với Docker daemon giả; không chạy gì trên DSM,
Docker daemon hay PostgreSQL thật):

- A3-T06, A3-T07, A3-T08, A3-T15, A3-T16 và phần Docker storage của A3-T14 bị chặn ở mức Docker/PostgreSQL thật cho tới
  PF-A3.4 *(đã được PF-A3.4 thay thế trên Engine 28.5.1/Compose v2.40.3: A3-T06, A3-T07, A3-T08 và A3-T15 passed ở mức
  thật; A3-T16 passed trừ R65 (F-A34-03); phần Docker storage của A3-T14 từ chối đúng, nhưng các kiểm tra no-effect của
  nó failed (F-A34-04, F-A34-06))*; phần filesystem của A3-T14 dùng `statvfs` thật;
- tính cách ly của topology `pfverify-*`/`pfrecover-*` chỉ được chứng minh từ cấu hình daemon báo về (internal network,
  port binding, restart policy, mount); hành vi network, port và egress thật chưa được quan sát *(đã được PF-A3.4 thay
  thế trên Engine 28.5.1/Compose v2.40.3: LOOP-07 đã quan sát internal network, không publish port, egress thất bại và
  tên instance không resolve được từ bên trong một recovery target thật)*;
- oracle application invariant là `app.cli reconcile` của image đã deploy; image không có lệnh này được ghi là
  `unavailable`, và output của reconcile không bao giờ được lưu;
- quyết định của owner, đã áp dụng: instance purge ghi registry tombstone `state: purged` và nhả project claim
  (OD-A33-08, đúng design);
- sai khác đã khai báo so với design r3 LIFECYCLE §9, **đang chờ owner phê duyệt** (mỗi mục đều fail closed): recovery
  target không có listener và chỉ truy cập bằng `docker exec` với quyền root (OD-A33-06); nó được ghi nhận bởi chính
  operation `restore-side-by-side`, không phải instance đã đăng ký (OD-A33-07); bundle của instance khác, kể cả từ một
  host đã mất, bị từ chối trước mọi xác nhận (`restore-target-mismatch`, OD-A33-09). Việc nghiệm thu PF-A3.3 phụ thuộc
  vào phê duyệt đó;
- các quyết định này thay thế ba giới hạn PF-A3.2 ở trên: `abort-deploy` sau khi frontend đã mở sẽ bảo toàn trước,
  emergency preservation bao gồm `reset-db` và `abort-deploy` (instance purge vẫn gate bằng healthy checkpoint vì final
  bundle của nó phải kiểm tra functional được; `restore-instance` không có dữ liệu hiện tại cần bảo toàn, target của nó
  trống), và runner record không có journal đã có route acknowledgement.

Kết quả và giới hạn của PF-A3.4 (Docker, Compose và PostgreSQL thật trong một container Linux generic cô lập; không
chạy gì trên NAS, DSM, btrfs hay SMB; bằng chứng nằm trong `_claude_outputs/ops/PF-A3.4/`):

- môi trường: một container `docker:28.5.1-dind` privileged (Docker Engine 28.5.1, Compose v2.40.3, PostgreSQL 16.15
  từ `postgres:16` được pin theo digest, Alpine CPython 3.12 làm interpreter của pf) trên Docker Desktop for Windows;
  Docker root và cây instance là các image ext4 sparse được mount qua loop device; mọi base image được pin theo digest
  (`evidence/<run>/base-images.json`). Launcher đã cài `/usr/local/bin/pf` chạy ở terminal được script hóa với quyền
  root của container. Container, volume, image và network riêng của daemon host được chứng minh không đổi bằng
  inventory diff của mọi evidence run;
- passed ở mức thật: LOOP-01/A3-T11 và LOOP-02/A3-T12 (các loop bắt buộc), LOOP-03 cùng A3-T03, LOOP-05 (không instance
  nào khác thay đổi quanh mọi bước của alpha), LOOP-06 (phần Linux generic của A3-T10), LOOP-07/A3-T15 (cách ly
  side-by-side được quan sát thật: internal network, không publish port, không có egress, không chia sẻ credential hay
  job), A1-T11, A1-T12, A1-T14, A1-T13 trừ phần rootless, A2-T04 (registration và control-only upgrade không restart
  container ứng dụng nào), A3-T06, A3-T07, A3-T08 và C-CONC-01 (writer chạy song song quanh capture và lúc dừng
  service). Crash matrix thật (`CRASH_MATRIX.json`, row R01–R66 và boundary row B01–B34) đã ngắt các loại lifecycle
  trước và sau các effect không đảo ngược được; mọi route đã chạy đều phục hồi với oracle dữ liệu và schema nguyên vẹn,
  trừ các row nêu bên dưới. Ngõ cụt của R33 để instance ở trạng thái purged trong một run, nên mười một boundary row và
  R43 của run đó không đạt được precondition (A3-T05 passed trừ R43);
- lỗi mà các lần chạy thật tìm ra và slice này đã sửa (mỗi lỗi có regression test offline): bước kiểm tra tương thích
  database từ chối tên extension `uuid-ossp` mà `postgres:16` liệt kê là available, nên mọi `rollback --restore-db` đều
  lỗi (F-A34-01); archive globals của purge bundle truyền `-d postgres` cho `pg_dumpall`, mà libpq đọc như connection
  string, nên mọi purge thật bị hủy sau capture (F-A34-02); purge bundle giữ lại mọi database của instance, và
  `restore-instance` chuyển một database `pf_restore_*` hoặc `pf_migrate_*` còn sót được giữ lại sang nhánh
  rollback/update, nên mọi lần thử đều kết thúc `plan-input-changed … (unreadable)` khi instance đã purge (F-A34-09, row
  R33; được sửa sau các evidence run, nên lần chạy lại thật vẫn đang chờ);
- finding còn mở, **cần thay đổi** (mỗi mục đều fail closed; không mục nào làm mất dữ liệu): không route nào của pf khởi
  động container db của instance khi nó bị stop hoặc kill từ bên ngoài (`unless-stopped` không khởi động lại nó). Khi đó
  `backup`, `update` và `rollback` từ chối với "The database container is not running.", còn `resume` forward của một
  activation bị ngắt chỉ khởi động lại backend và lần nào cũng trượt health check, nên cách duy nhất để operator đi tiếp
  là `docker start` container db bằng tay (F-A34-03; row R10 và R65). Các lần từ chối sau preflight chỉ đọc không hoàn
  toàn không có tác dụng phụ: contract probe giữ lại image tag `*-inspect-*`/`*-observe-*`, chạy một probe container và
  ghi lại `state/inspect-images.yaml`, và một `restore-instance` bị từ chối trên instance đã purge tạo thư mục `state/`
  rỗng của nó (F-A34-04, F-A34-06; ngoài ra các lần từ chối Docker-storage của A3-T14 là đúng). Một link hoặc entry đặc
  biệt trong workspace bị drift chỉ bị capture từ chối sau khi service đã dừng (F-A34-05, quan sát). `restore-instance`
  lên một volume mới từng lỗi với "the database system is shutting down" hai giây sau khi container db mới báo healthy,
  khớp với server khởi tạo tạm thời của image; `pf resume` đã hoàn tất nó (F-A34-07, R31). Verification checkpoint của
  một purge từng lỗi vì `createdb` thoát với mã 1 trên một server healthy, và pf báo là "locale … is not available", che
  mất lỗi thật; purge fail closed (F-A34-08, R29). `pg_dump` của `backup --emergency` là child chỉ đọc và không bao giờ
  để lại runner record, nên acknowledgement của A12r2-F06 chỉ được chứng minh trên một runner record thật của `deploy`
  build (R41; R66 blocked);
- giới hạn đã khai báo: release source là test seam (một approved remote `file://` cục bộ với câu trả lời GitHub mô
  phỏng; không liên hệ HTTPS hay GitHub); không có rootless daemon (phần rootless của A1-T13 blocked, PF-A5.1); launcher
  chỉ chạy với quyền root của container; restart daemon không phải mất điện hay reboot host; lần dừng I8 ở lần rename
  workspace đầu tiên quan sát được pf nằm giữa hai lần rename (R51), và một chỉnh sửa muộn qua file đang mở vẫn còn
  (L6); OD-A33-06, OD-A33-07 và OD-A33-09 vẫn chờ owner phê duyệt, và hành vi đã ship được test nguyên trạng;
- OD-A33-12 vẫn mở: khi db bị dừng, cả `resume` forward lẫn `rollback --restore-db` thay thế đều là ngõ cụt (R65); sau
  khi db được khởi động bằng tay, `resume` forward hoàn tất và `pf_keep_*` được giữ lại chứa dữ liệu trước switch, nên
  bằng chứng chỉ vào khoảng trống route F-A34-03 chứ không phải việc thiếu reverse switch;
- không có gì ở đây là production-ready; các phần DSM/SMB (A3-T10, A1-T17; PF-A5.1) không đổi.

**Đóng PF-A1 (offline).** Với PF-A1.4, mọi entry route dùng các primitive A1 (instance tường minh,
một runner, daemon binding, Compose envelope, exact inventory) và không còn route Compose catch-all;
phạm vi an toàn của PF-A1 mới chỉ được chứng minh offline. A1-T11…T14 vẫn bị chặn vì cần Docker
daemon thật (owner PF-A3.4/PF-A5.1, kể cả label one-off thật của Compose `run`) và A1-T17 cần bằng
chứng DSM ACL/SMB (owner PF-A2.3/PF-A5.1). Không finding nào được đóng toàn bộ và không có gì ở đây
là production-ready.

Trước khi dựa vào v2.5 recovery cho data quan trọng, nên chạy tương tác ít nhất một vòng disposable
staging trên NAS thật. Con đường cho NAS v2.5 cần legacy adoption (OD-A21-05) trước khi migrate một NAS v2.5
đang chạy.

```text
install-control.sh init
→ pf config admin --configuration <config> --project <project>
→ pf install register
→ pf config app
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
