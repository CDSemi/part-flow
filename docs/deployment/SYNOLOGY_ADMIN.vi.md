# Hướng dẫn Deployment Admin (`pf`)

> **Bản gốc:** [`SYNOLOGY_ADMIN.md`](./SYNOLOGY_ADMIN.md). Đồng bộ với bản tiếng Anh tại commit `e0e5056` cộng các thay đổi
> chưa commit ngày 2026-11-11. Bản tiếng Anh là nguồn chuẩn (source of truth); khi hai bản khác nhau, bản tiếng Anh có
> hiệu lực.

Hướng dẫn này mô tả Deployment Admin, tức công cụ dòng lệnh `pf` trong `deploy/synology/`, dùng để cài đặt và quản lý các
instance **staging** của PartFlow trên Synology NAS hoặc trên một máy chủ Linux khác có Docker. Tài liệu viết cho người
quản trị một instance staging như vậy. Production không bao giờ dùng `pf`: production được cài đặt và vận hành theo
[`DEPLOYMENT.vi.md`](../DEPLOYMENT.vi.md), [`SYNOLOGY_NAS.vi.md`](./SYNOLOGY_NAS.vi.md) và
[`OPERATIONS_RUNBOOK.vi.md`](./OPERATIONS_RUNBOOK.vi.md).

Các thông báo của `pf` trích dẫn hướng dẫn này theo số mục (ví dụ `SYNOLOGY_ADMIN §16`), vì vậy số mục được giữ cố định.

## 1. Mục đích và tình trạng

`pf` tách riêng hai thứ. **Workspace** là bản checkout PartFlow có thể chỉnh sửa, được những người dùng tin cậy thay đổi
qua chia sẻ tệp Server Message Block (SMB). **Control release** là mã mà `sudo pf` chạy: nó được cài một lần từ một cây
mã đã được review, thuộc sở hữu của root và được kiểm tra trước mỗi lần chạy, nên những người sửa được workspace không
bao giờ thay đổi được những gì chạy bằng quyền root. Mỗi bước vòng đời (deploy, update, backup, rollback, reset database,
purge và restore) chạy theo một plan đã ghi, yêu cầu gõ xác nhận và ghi từng tác động vào journal của operation, nên một
bước bị gián đoạn có thể được tiếp tục hoặc hủy một cách an toàn bằng `pf resume`.

Tình trạng:

- Việc phát triển `pf` đã dừng ngày 2026-10-14, khi chủ sở hữu quyết định xây lại bộ công cụ triển khai thành một dự án
  riêng. Công cụ vẫn nằm trong `deploy/synology/` ở trạng thái cuối cùng của nó, và các test của nó vẫn chạy.
- `pf` chỉ quản lý staging. Một instance staging không bao giờ chạy trên Docker daemon của máy chủ production: script
  phát hành production `release.sh` từ chối chạy khi trên daemon của nó có một Compose project `partflow-staging`
  ([`DEPLOYMENT.vi.md`](../DEPLOYMENT.vi.md) §6).
- `pf` chưa từng chạy trên một NAS thật. Bằng chứng của nó là một bộ test offline và một lần chạy với Docker Engine,
  Docker Compose và PostgreSQL thật bên trong một container Linux cô lập (§18). Hãy coi lần chạy staging đầu tiên trên
  NAS là lần kiểm thử thật đầu tiên của nó.

Yêu cầu: một Synology NAS chạy DiskStation Manager (DSM) hoặc một máy chủ Linux khác, một Docker daemon cục bộ chạy bằng
quyền root (rootful) với Docker Compose v2 (plugin `docker compose`, hoặc một tệp chạy Compose độc lập được đăng ký khi
cài; Compose v1 không được hỗ trợ), Git, và một trình thông dịch Python phiên bản 3.9 trở lên. `pf` chỉ dùng thư viện
chuẩn của Python.

**Installation root** (thư mục gốc cài đặt) chứa control và trạng thái riêng của mọi instance:

```text
<root>/
├── bootstrap/                 launcher pf, verifier pf_bootstrap.py, bootstrap.conf, tools.conf, scheduler wrappers
├── releases/r-<16 hex>/       control releases: immutable, content-addressed, one of them bound
├── install-operations/        the plan and journal of every install operation
├── profiles/, policies/       the PartFlow staging profile and the staging policy
├── registry/, locks/          registered instances, reservations and locks
├── instances/<uuid>/          private state of one instance: record, state, operations, artifacts
└── sources/, staging/, home/  protected Git source store, staging area, private home of child processes
```

`<uuid>` là mã định danh duy nhất toàn cục (universally unique identifier, UUID) của một instance; `pf status` và
`pf instances` in ra mã này. Mỗi instance được đăng ký với bốn thư mục do người quản trị chọn:

| Thư mục | Nội dung | Quyền của nhóm được chọn (§2) |
| --- | --- | --- |
| Workspace | Bản checkout PartFlow có thể chỉnh sửa, thường tên là `repo/`. | Read and edit (đọc và sửa) |
| Configuration | `pf-config.json` và tệp `.env` của ứng dụng (§6). | Read and edit (đọc và sửa) |
| Backups | Các checkpoint theo revision (§10). | View and copy (xem và sao chép) |
| Recovery | Các recovery bundle của purge (§12). | View and copy (xem và sao chép) |

Trong hướng dẫn này, `pf` là launcher đã cài, chạy bằng quyền root: `sudo <root>/bootstrap/pf`, hoặc `sudo pf` khi trình
cài đặt đã tạo launcher toàn cục `/usr/local/bin/pf` cho root này (§5). Khi có nhiều hơn một instance đã đăng ký, thêm
`--instance <slug>` trước lệnh; một lệnh chạy không có terminal luôn phải nêu instance của nó.

Mọi tiến trình con mà `pf` khởi động (Git, Docker và Compose, các chương trình client PostgreSQL bên trong container `db`,
các chẩn đoán máy chủ) đều đi qua một runner duy nhất: chỉ các công cụ đã đăng ký trong `<root>/bootstrap/tools.conf`
được chạy, với một môi trường cố định, một thời hạn và đầu ra có giới hạn, trong đó mật khẩu database bị loại bỏ. Một lần
gián đoạn (Ctrl-C, phiên Secure Shell (SSH) bị đóng, `SIGTERM`) dừng các tiến trình con, ghi lại điều đã xảy ra và để
operation lại cho `pf resume`.

## 2. Mô hình quyền

Mỗi instance theo một permission policy (chính sách quyền). Bạn chọn một nhóm và một mức truy cập cho mỗi scope, còn
`pf` tính ra mode của tệp; nó không bao giờ hỏi một mode dạng bát phân (octal).

| Lựa chọn | Giá trị trong policy | Thành viên của nhóm có thể |
| --- | --- | --- |
| Read and edit (đọc và sửa) | `read_write` | Đọc, tạo, sửa, đổi tên và xóa. |
| View and copy (xem và sao chép) | `read_only` | Đọc và sao chép. |
| No group access (không có quyền nhóm) | `none` | Không gì cả; chỉ chủ sở hữu có quyền. |

| Scope | Thư mục | Những gì có thể chọn | Không lựa chọn nào thay đổi được |
| --- | --- | --- | --- |
| Workspace | Workspace đã đăng ký. | Nhóm, mọi mức truy cập, kế thừa nhóm, chạy script `Owner only` hoặc `Owner and group`. | Chủ sở hữu hiện có của người sửa được giữ nguyên; chỉ các tệp mà source đã deploy đánh dấu là executable mới có bit execute. |
| Configuration | Thư mục configuration đã đăng ký. | Nhóm, mọi mức truy cập, kế thừa nhóm. | Không có gì được phép chạy (executable). |
| Control release | `<root>/releases/<id>/` | Cố định: No group access. | Chủ sở hữu là root, không có quyền ghi cho group hoặc other, không có special bit; chỉ được kiểm tra, không bao giờ bị thay đổi. |
| Backups | Thư mục backups đã đăng ký. | Nhóm, View and copy hoặc No group access. | Chủ sở hữu là root, không có quyền ghi cho group. |
| Recovery bundles | Thư mục recovery đã đăng ký. | Như backups. | Như backups. |
| Private state | `<root>/instances/<uuid>/` | Cố định: chỉ chủ sở hữu. | Thư mục `0700`, tệp `0600`. |

Các mode kết quả (chủ sở hữu luôn đọc và ghi được, other không có gì, setgid chỉ có trên thư mục có kế thừa):

| Quyền | Tệp | Thư mục | Thư mục có kế thừa |
| --- | --- | --- | --- |
| No group access | `0600` | `0700` | `2700` |
| View and copy | `0640` | `0750` | `2750` |
| Read and edit | `0660` | `0770` | `2770` |

Một tệp mà source đã deploy đánh dấu là executable còn có thêm quyền execute cho chủ sở hữu (`Owner only`) hoặc cho chủ
sở hữu và nhóm (`Owner and group`).

**Policy đã phê duyệt và policy suy ra.** Một lần `pf permissions apply` đã xác nhận ghi *permission policy revision*
tiếp theo vào `<root>/instances/<uuid>/permission-policy.json` (root, `0600`); `purge` giữ lại tệp này. Trước lần phê
duyệt đầu tiên, instance dùng một policy suy ra (derived policy): workspace và thư mục configuration nhận
`workspace_write_group` của `pf-config.json` với Read and edit và kế thừa nhóm, còn backup và recovery bundle giữ nhóm mà
thư mục của chúng đang có, với View and copy. Các nhóm trong `pf-config.json` chỉ là đề xuất: `backup_read_group` tự nó
không bao giờ thay đổi quyền, và `workspace_write_group` thôi có hiệu lực khi đã có một revision. Một đề xuất khác với
policy được báo cáo (`permission-group-proposal`) và chỉ có hiệu lực qua `apply`. Thành viên của nhóm backup đọc được nội
dung database và các thông tin đăng nhập được giữ trong recovery bundle.

```sh
pf permissions check [--scope SCOPE]...
pf permissions plan  [--scope SCOPE]... [--details]
pf permissions apply [--scope SCOPE]... [--details]
pf permissions apply --resume | --abandon
```

`SCOPE` là `workspace`, `configuration`, `control`, `backups`, `recovery` hoặc `private_state` (lặp lại được; mặc định là
tất cả các scope).

- `check` và `plan` chỉ đọc: chúng không lấy khóa và không ghi gì. `check` thoát với mã 1 khi có bất kỳ khác biệt nào
  (`permissions-differ`). `plan` cho thấy, với mỗi scope, policy, các thành viên nhóm, các con số thống kê, việc có cần
  editor freeze hay không, các blocker và một plan hash; `--details` liệt kê từng thay đổi.
- `apply` lấy khóa của instance, hỏi nhóm, mức truy cập, kế thừa và, với workspace, việc chạy script, cho thấy các thay
  đổi và plan hash, rồi yêu cầu gõ `APPLY PERMISSIONS <slug>`. Nó ghi lại ý định, áp dụng và kiểm tra mọi scope đã chọn,
  và chỉ sau đó mới ghi revision mới. Một lần apply bị gián đoạn chặn mọi thay đổi khác đối với instance cho đến khi chạy
  `apply --resume` hoặc `apply --abandon` (`permissions-apply-pending`).
- `apply` không bao giờ đi theo một liên kết, không bao giờ vượt qua một mount, không bao giờ thay đổi một mục có hard
  link, một tệp đặc biệt, một mục mang danh sách kiểm soát truy cập (access control list, ACL) hay một mục `@docker`, và
  không bao giờ thay đổi control release. Mỗi trường hợp này là một blocker mà `plan` báo cáo, và `apply` không thay đổi
  gì khi còn một blocker.

**Editor freeze.** Một thay đổi bên dưới thư mục trên cùng của workspace hoặc của configuration cần một editor freeze đã
được kiểm chứng. `apply` trước tiên đặt thư mục của scope thành chỉ chủ sở hữu được vào, để người sửa không thể vào nữa,
rồi tìm trong `/proc` mọi tiến trình vẫn còn dùng một mục. Một tiến trình như vậy làm apply dừng lại
(`editor-freeze-refused`, không có gì thay đổi); hãy chạy `apply` từ bên ngoài các thư mục của scope, ví dụ sau `cd /`.
Freeze không dùng được khi không đọc được `/proc` hoặc khi thư mục của scope mang một ACL (`editor-freeze-unavailable`).
Khi đó, hoặc bỏ scope ra bằng `--scope`, hoặc thay đổi bằng tay: dừng việc sửa thư mục qua SMB, đặt các mode trong bảng
trên bằng quyền root, rồi chạy `pf permissions check`. Freeze không dừng được một dịch vụ chạy bằng root hay một tệp
được ánh xạ vào bộ nhớ (memory-mapped).

**ACL và DSM.** `apply` không bao giờ ghi hay xóa một mục ACL. Một mục có access ACL là một blocker (`scope-entry-acl`);
một thư mục chỉ có default ACL thì được chấp nhận. Quyền Shared Folder của DSM vẫn áp dụng bên trên mode của tệp: hãy
kiểm tra chúng cho từng tài khoản SMB trong DSM Control Panel. Vì vậy một shared folder của DSM có dùng ACL không thể
được `apply` thay đổi hàng loạt.

**Các trạng thái.** `check` và `apply` in ba trạng thái cho mỗi scope: `mode_applied`, `effective_access_verified` và
`future_file_behavior_verified`. Hai trạng thái sau luôn là `not verified`, vì `pf` chỉ tính chúng từ mode của tệp; các
tệp được tạo qua SMB nhận mask của client hoặc của share.

**Sự tin cậy.** Quyền ghi vào workspace và thư mục configuration cho phép một người dùng tin cậy thay đổi mã nguồn của ứng
dụng và các thiết lập lúc chạy, kể cả mật khẩu database trong `.env`. Checkpoint và recovery bundle chứa dữ liệu của ứng
dụng, và recovery bundle còn chứa cả `.env`. Chỉ cấp quyền SMB cho những người được phép xem và thay đổi tất cả những thứ
này.

## 3. Nơi đặt `.env`

Tệp `.env` của ứng dụng nằm trong thư mục configuration, bên ngoài workspace. Ứng dụng không phụ thuộc vào nơi tệp được
lưu, vì `pf` trao cho Compose mọi đầu vào một cách tường minh:

- mọi lệnh có thay đổi trước tiên đóng băng `.env` thành một bản chụp (snapshot) riêng trong thư mục operation của nó và
  đưa bản chụp đó cho Compose; một lần sửa `.env` trong lúc operation chạy sẽ bị phát hiện;
- `PARTFLOW_REPO_ROOT` chỉ ra workspace để build image;
- `PARTFLOW_DATABASE_URL` mang địa chỉ database của backend với thông tin đăng nhập đã được mã hóa phần trăm
  (percent-encoded);
- `DEPLOY_ADMIN_INSTANCE_ID` mang định danh của instance, dùng để gắn nhãn mọi container, volume, network và image mà
  Compose tạo ra; nó đến từ bản đăng ký được bảo vệ và không bao giờ được đọc từ `.env`.

Các chế độ xem chỉ đọc đưa cho Compose một tệp môi trường rỗng được bảo vệ cùng các giá trị được phép, nên Compose không
bao giờ đọc một tệp có thể chỉnh sửa. Việc giữ `.env` bên ngoài workspace cũng giữ nó khỏi Git và cho phép `pf` thay
workspace mà không đụng đến thông tin đăng nhập. Một lệnh `docker compose` thô chạy trong workspace sẽ không tìm thấy cả
`.env` lẫn tệp Compose đã cài; §14 trình bày dạng tường minh.

## 4. Tệp nguồn và control đã cài

Repository chứa mã nguồn của công cụ: `pf.sh` và `compose.nas.yaml` ở thư mục gốc của repository, và trong
`deploy/synology/` có trình cài đặt `install-control.sh`, controller `pf-admin.py` cùng các module `pf_*.py`, các
scheduler wrapper `backup.sh` và `release-check.sh`, các tệp mẫu `pf-config.example.json` và `nas.env.example`, các
schema của bản ghi trong `contracts/`, các test và `TEST_REPORT.md`.

Không tệp nào trong số này của repository được chạy trong quá trình quản trị thông thường. `install-control.sh init` (§5)
là ranh giới tin cậy (trust boundary): nó đọc các tệp đã review như dữ liệu, cho thấy những gì sẽ cài, và cài đúng những
thứ đó thành một control release trong `<root>/releases/`. Launcher đã cài chạy bootstrap verifier trước bất kỳ mã
control nào; verifier kiểm tra trình thông dịch, cấu hình, mọi thư mục cha và toàn bộ cây release đã ghim (pinned).
`pf.sh` trong repository từ chối vận hành; hãy dùng launcher đã cài.

Một lần `update` ứng dụng không bao giờ thay đổi control. Khi một revision PartFlow sau này thay đổi `pf-admin.py`,
`compose.nas.yaml` hoặc một tệp khác của công cụ, hãy review revision đó và cài nó bằng `pf install control` (§15).

## 5. Cài đặt

Khi launcher toàn cục chưa tồn tại, hãy gọi launcher đã cài bằng đường dẫn đầy đủ, `<root>/bootstrap/pf`. `sudo pf` chỉ
đến được cùng root này khi `init` đã tạo `/usr/local/bin/pf`; một tệp có sẵn ở đường dẫn đó, ví dụ launcher của một bản
cài cũ hơn, không bao giờ bị thay thế.

### (a) Cài mới

1. Từ một bản checkout đã review, tạo installation root. Đường dẫn phải chưa tồn tại, hoặc là một thư mục rỗng nằm trên
   cùng hệ thống tệp với thư mục cha; một mount point hoặc chính một shared folder của DSM bị từ chối (`root-exists`).

   ```sh
   sudo sh ./deploy/synology/install-control.sh init --root <root>
   ```

   Các giá trị còn thiếu sẽ được hỏi; `--interpreter`, `--tool <id>=<path>`, `--launcher-path <path>` và
   `--no-launcher` thay cho các giá trị mặc định được phát hiện. Xác nhận bằng `INSTALL CONTROL <release-id>`. Root được
   dựng bên cạnh đích rồi công bố bằng một lần đổi tên, nên một lần `init` thất bại hoặc bị gián đoạn không công bố gì
   cả: hãy chạy lại.
2. Tạo cấu hình quản trị trong một thư mục configuration đã có sẵn và thuộc sở hữu của root:

   ```sh
   sudo <root>/bootstrap/pf config admin --configuration <config> --project <project>
   ```

   Lệnh hỏi nhóm workspace và nhóm backup từ các nhóm đang có trên máy chủ và ghi `<config>/pf-config.json` sau khi bạn
   trả lời `y` (§6).
3. Đăng ký instance với bốn thư mục đã có sẵn của nó:

   ```sh
   sudo <root>/bootstrap/pf install register --slug <slug> --project <project> --workspace <checkout> \
     --configuration <config> --backups <backups> --recovery <recovery>
   ```

   Bước kiểm tra trước (preflight) kiểm tra các thư mục (nó không tạo thư mục nào), cấu hình, các nhóm, sự chồng lấn với
   các instance khác và Docker daemon, rồi liệt kê mọi xung đột cùng một lúc. Xác nhận bằng `REGISTER <slug>`.
4. Tạo `.env` bằng `pf --instance <slug> config app`, hoặc để lần `pf --instance <slug> deploy` đầu tiên hỏi cùng các câu
   hỏi đó (§6, §7).

### (b) Đăng ký một home v2.5

Một bản cài theo bố cục v2.5 cũ hơn (một thư mục home có `repo/`, `control/`, `config/`, `backups/` và `recovery/`) có
thể được đăng ký mà không phải di chuyển dữ liệu:

```sh
sudo <root>/bootstrap/pf install migrate-legacy --legacy-home <home> --workspace <checkout> --slug <slug>
```

Lệnh này sao chép nguyên từng byte một tệp `.env` hoặc `pf-config.json` chỉ có trong bản checkout vào `<home>/config/`
(bản gốc vẫn ở chỗ v2.5 đọc nó), từ chối khi có hai bản sao khác nhau, và đăng ký instance sau `MIGRATE <slug>`. Nó không
di chuyển gì khác: không trạng thái v2.5, không resource Docker và không quyền. Khi thư mục `control/` của v2.5 còn tồn
tại, v2.5 vẫn là control của instance đó: `pf` từ chối mọi thay đổi đối với nó (`legacy-control-active`) và chỉ cung cấp
`status`, `doctor`, `ps` và `logs`. `pf` không bao giờ tiếp quản (adopt) các resource Docker của một bản cài v2.5.

## 6. Các tệp cấu hình

### `pf-config.json`

Các thiết lập quản trị của một instance. `pf config admin` tạo, di chuyển lên schema mới (migrate) hoặc bổ sung tệp này
(§5 trước khi đăng ký, `pf --instance <slug> config admin` sau khi đăng ký). Dạng hiện hành, schema 2, liệt kê mọi khóa:

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

| Khóa | Ý nghĩa |
| --- | --- |
| `repository`, `branch` | Repository GitHub và nhánh mà `--latest` phân giải. |
| `project` | Compose project; phải bằng project đã đăng ký (`admin-config-mismatch`). |
| `environment` | Một nhãn phải bằng môi trường của policy đã phê duyệt cho instance (`staging`); sửa nó không bao giờ thay đổi policy. |
| `release_channel` | Kênh mặc định, `stable` hoặc `prerelease`, cho `--release latest` và `release-check`. |
| `auto_update` | Chỉ là đề xuất: không có gì chạy tự động không người giám sát (§13). |
| `ci_workflow` | Tệp workflow tích hợp liên tục (continuous integration, CI) mà `deploy` và `update` kiểm tra kết quả. |
| `health_timeout_seconds` | Thời gian tối đa các kiểm tra tình trạng (health check) được chờ. |
| `minimum_free_mb` | Dung lượng trống được giữ lại trên mỗi thiết bị (§10). |
| `backup_read_group`, `workspace_write_group` | Các nhóm đề xuất (§2). |

- Một tệp không có `schema_version` là schema 1 cũ: các khóa bị bỏ qua nhận giá trị của schema 1, và tệp vẫn dùng được.
  Chỉ `pf --instance <slug> config admin` mới chuyển nó lên schema 2: các giá trị tường minh được giữ, các giá trị bị bỏ
  qua được ghi với giá trị của schema 1. Một giá trị mà schema 2 từ chối sẽ chặn việc chuyển đổi
  (`admin-config-migration-blocked`).
- Wizard chỉ hỏi hai nhóm: cả hai khi tạo tệp, nếu không thì chỉ hỏi nhóm nào không tồn tại trên máy chủ. Trả lời bằng
  một số trong danh sách các nhóm của máy chủ hoặc bằng tên một nhóm có sẵn; nhóm không bao giờ được tạo mới. Các khóa
  khác hãy sửa bằng tay. `pf` kiểm tra mọi khóa trước khi dùng và từ chối một khóa bị trùng, không biết, bị thiếu hoặc
  sai kiểu.
- Tệp được thay thế qua một tệp tạm `.pf-config.json.pf-config-<8 hex>` bên cạnh nó, giữ chủ sở hữu, nhóm và mode của
  tệp bị thay, và không được ghi khi tệp đã thay đổi sau phần tóm tắt (`config-changed`).

### `.env`

`.env` chứa đúng bảy khóa:

```dotenv
POSTGRES_USER=partflow_staging
POSTGRES_PASSWORD=<generated-secret>
POSTGRES_DB=partflow_staging
SITE_TIMEZONE=America/Los_Angeles
PARTFLOW_BIND_IP=192.168.0.11
PARTFLOW_HTTP_PORT=5173
PARTFLOW_ALLOWED_HOST=localhost
```

`pf --instance <slug> config app` tạo hoặc bổ sung tệp này, và lần `deploy` đầu tiên khi chưa có `.env` chạy cùng wizard
đó. Một tệp mới bắt đầu từ `nas.env.example` đã cài; trong một tệp có sẵn, chỉ những dòng thay đổi mới được viết lại.
Wizard chỉ hỏi các giá trị bị thiếu hoặc không hợp lệ: user database, tên database, múi giờ của nhà máy, chế độ truy cập
(truy cập trực tiếp trên một địa chỉ mạng cục bộ (local area network, LAN), hoặc qua DSM Reverse Proxy trên `127.0.0.1`),
cổng HTTP, và tên máy (host name) của Reverse Proxy.

- **Mật khẩu.** Một `POSTGRES_PASSWORD` có sẵn không bao giờ được hiển thị, thay đổi hay tạo lại. Một mật khẩu bị thiếu
  chỉ được tạo (64 ký tự thập lục phân từ một nguồn ngẫu nhiên an toàn) khi instance chưa từng được deploy. Một mật khẩu
  được giữ mà ngắn hơn 32 ký tự sẽ bị báo (`password-weak-for-new-deployment`), và lần `deploy` đầu tiên từ chối nó.
- **Instance đã deploy.** Khi một instance đã được deploy (hoặc đã chuyển từ v2.5), ba giá trị database không bao giờ
  được hỏi, tạo hay viết lại; một giá trị bị thiếu hoặc không dùng được sẽ bị từ chối (`app-credential-unusable`). Sửa
  chúng bằng tay không thay đổi thông tin đăng nhập bên trong database đang có, và không wizard nào xoay vòng (rotate)
  chúng.
- **Múi giờ.** Một `SITE_TIMEZONE` mới phải có trong dữ liệu múi giờ của máy chủ.
- **Định dạng chặt chẽ.** Mỗi dòng một `KEY=VALUE`, không có khoảng trắng quanh `=`, không có `export`, dòng chú thích bắt
  đầu bằng `#`; một giá trị không có dấu nháy, có dấu nháy đơn hoặc có dấu nháy kép và không bao giờ được khai triển
  (expand). Một giá trị không thể chuyển nguyên văn (một dấu nháy đơn, một dấu gạch chéo ngược ở cuối, một ký tự điều
  khiển) được báo là `migration-issue` và chặn các lệnh có thay đổi cho đến khi bạn sửa tệp bằng tay.
- **ACL.** Một `.env` hoặc `pf-config.json` mang mục ACL sẽ bị từ chối (`config-file-acl`); hãy sửa nó bằng tay.
- **Mã hóa URL.** Các revision của ứng dụng cũ hơn commit `4313863` ngày 2026-10-07, chẳng hạn bản phát hành
  `v0.1.0-alpha.1`, không chạy được migration với một mật khẩu cần mã hóa URL (bất kỳ ký tự nào ngoài chữ cái, chữ số và
  `-._~`); `deploy` và `update` của một revision như vậy dừng ở bước migration. Mật khẩu được tạo tự động không bị ảnh
  hưởng.

## 7. Deploy lần đầu

```sh
pf deploy --latest                       # the tip of the configured branch
pf deploy --commit <full commit hash>
pf deploy --release <tag>                # or: --release latest [--channel prerelease]
pf deploy                                # the current checkout, only when it is proven (§8)
```

`--skip-ci` bỏ qua việc kiểm tra CI như một ngoại lệ thủ công tường minh; nó không phải bằng chứng là CI đã đạt.
`deploy`:

1. từ chối khi đích đã có container, volume hoặc network (`resource-target-not-empty`);
2. tạo hoặc dùng lại `.env` (§6);
3. phân giải source thành một commit chính xác, lấy (fetch) nó vào kho source được bảo vệ và kiểm tra kết quả CI của nó;
4. build image backend và frontend trước khi database tồn tại;
5. kiểm tra dung lượng trống (§10) và yêu cầu gõ `DEPLOY <12 ký tự đầu của commit>`;
6. ghi plan và journal của nó, rồi lưu trữ source chính xác vào một thư mục staging riêng;
7. khởi động một database PostgreSQL mới, chạy `alembic upgrade head`, khởi động backend và kiểm tra tình trạng và schema
   của nó, rồi khởi động frontend và kiểm tra `/api/health` qua frontend;
8. niêm phong **deployment record** (hồ sơ triển khai: bản lưu trữ source cùng manifest của nó, mô hình Compose đã phân
   giải và `.env` đã đóng băng; chỉ root đọc được, được giữ lại kể cả khi `purge`) và ghi con trỏ triển khai
   `deployed.json`;
9. chuyển workspace sang cây đã deploy (§8).

Nếu việc niêm phong thất bại sau khi ứng dụng đã khởi động khỏe, ứng dụng vẫn chạy, lệnh thoát với mã 1 kèm
`deployment-record-incomplete`, và lần `deploy`, `update`, `rollback` hoặc `restore-instance` tiếp theo sẽ niêm phong một
record. Sau khi smoke test giao diện người dùng và các quy trình, hãy tạo checkpoint đầu tiên bằng `pf backup` (§10).

Một lần deploy bị gián đoạn được tiếp tục bằng `pf resume`. Để bỏ một lần deploy đầu tiên chưa hoàn tất, chạy
`pf abort-deploy` và xác nhận `ABORT DEPLOY <project>`. Lệnh được phép cho đến khi con trỏ triển khai được ghi, chỉ xóa
các container, network và volume mà lần deploy này đã tạo, và giữ lại image, workspace và `.env`, nên có thể chạy `deploy`
lại. Khi kết quả của lần migration đầu tiên bị mất, lần deploy dừng ở `needs_operator` và `abort-deploy` là lối đi. Nếu
frontend đã mở, người dùng có thể đã ghi dữ liệu, nên lệnh hủy trước tiên dừng ứng dụng và bảo toàn database hiện tại
thành một checkpoint `before-abort` trong thư mục backups; database phải truy cập được (nếu không thì
`preservation-failed`). Một lần hủy bị gián đoạn được tiếp tục sau `RESUME ABORT DEPLOY <project>`.

## 8. Workspace và revision đã deploy

Workspace là một bản sao làm việc, không phải bản ghi về những gì đang chạy: sửa một tệp qua SMB không thay đổi ứng dụng
đang chạy. `pf status` cho thấy cả hai định danh:

```text
Deployed source: <commit>
Workspace: provenance git_commit|unknown | manifest commit <commit or none> | differs from deployed: True/False | changes: ...
Deployment: <deployment-id> (git_commit <sha12>|unknown), sealed <time>
```

Source đã deploy đến từ deployment record và chỉ được in thành một commit khi kho source được bảo vệ đã chứng minh nó;
nếu không, `status` in `unknown provenance (…)`. Dòng `Deployment:` cũng có thể là
`legacy (no deployment record; created before PF-A3.1)`, `not recorded (seal failed in operation <op>; …)` hoặc
`<id> deployment-artifact-mismatch: <file>: <detail>` (một record bị hỏng, được giữ làm bằng chứng, §16).

- `pf` so sánh workspace từng byte với manifest mà nó đã ghi khi deploy cây đó. Nó không bao giờ chạy Git trong workspace,
  và không bao giờ tin thư mục `.git` của workspace. Một workspace không có manifest như vậy, hoặc khác với manifest, có
  provenance `unknown`.
- Việc so sánh bỏ qua các mục chưa được theo dõi (untracked) có tên `.env`, `node_modules`, `.venv`, `__pycache__` hoặc
  `.pytest_cache` (và `.git`). Một commit theo dõi một đường dẫn có một trong các tên đó sẽ bị từ chối.
- `pf deploy` không có bộ chọn sẽ chứng minh bản checkout trước: nó lấy commit mà bản checkout khai báo vào kho được bảo vệ
  và yêu cầu workspace khớp chính xác với commit đó; nếu không, nó yêu cầu `--commit`, `--latest` hoặc `--release`.
- Không lệnh vòng đời nào thay đổi quyền của các tệp có sẵn trong workspace; hãy dùng `pf permissions` (§2).

**Chuyển workspace.** Sau khi kích hoạt, `deploy`, `update`, `rollback` và `restore-instance` không sao chép tệp vào
workspace. Chúng chuẩn bị (stage) cây đã deploy trong `<workspace parent>/.pf-generations-<instance-uuid>/`, đổi tên
workspace cũ thành một thế hệ được giữ lại (retained generation) `wsg-<time>-<8 hex>` trong thư mục đó, rồi đổi tên cây
đã chuẩn bị thành đường dẫn workspace. Cây cũ được giữ nguyên vẹn, kể cả các tệp untracked và `.git`, và một tệp mà người
sửa lưu trong lúc chuyển sẽ nằm ở đó. Hãy sao chép phần việc đó trở lại bằng quyền root, ví dụ
`sudo cp -a <workspace parent>/.pf-generations-<uuid>/<generation>/<path> <workspace>/<path>`. `pf status` đếm các
thế hệ được giữ lại, và `pf cleanup --apply --generation` loại bỏ một thế hệ (§16).

- Giữa hai lần đổi tên, đường dẫn workspace vắng mặt trong chốc lát. Nếu tiến trình dừng đúng lúc đó, chỉ `pf resume`
  (gắn cây mới) hoặc `pf resume --keep-workspace` (đặt cây cũ trở lại) mới chạy được.
- Khi không thể chuyển (workspace là một mount point hoặc thư mục gốc của một shared folder, thư mục generation không an
  toàn, workspace mang một ACL, hoặc thiết bị không đủ chỗ), operation hoàn tất việc kích hoạt, thoát với mã 1 kèm
  `workspace-sync-pending` và vẫn mở ở `workspace_sync_pending` trong khi ứng dụng vẫn chạy. Hãy sửa nguyên nhân rồi chạy
  `pf resume`, hoặc giữ workspace hiện tại bằng `pf resume --keep-workspace`.
- Một lần `pf backup` thủ công được phép chạy trong khi việc chuyển đang chờ. Khi bản backup đó đang mở, `status` hiển thị
  `waits: backup operation <op> is open; run 'pf --instance <slug> resume --operation <op>' (or add --abandon) first,
  then this operation's routes apply`: hãy hoàn tất hoặc bỏ bản backup đó trước.
- `deploy`, `update`, `rollback` và `restore-instance` chấp nhận `--keep-workspace` để bỏ qua việc chuyển ngay từ đầu.

## 9. Update

```sh
pf update --latest
pf update --commit <full commit hash>
pf update --release <tag>                # or: --release latest [--channel prerelease]
pf update --latest --allow-migrations    # needed when the new revision adds migrations
```

`update` phân giải một commit chính xác, kiểm tra CI (trừ khi có `--skip-ci`), build các image ứng viên và kiểm tra hợp
đồng migration: một migration có sẵn bị sửa hoặc bị xóa luôn bị từ chối. Nó kiểm tra rằng các image đang chạy là image
của deployment record hiện tại (`deployment-image-mismatch`) và còn đủ chỗ, rồi yêu cầu gõ
`UPDATE <12 ký tự đầu của commit>`. Sau đó nó chuẩn bị deployment mới, dừng việc ghi của ứng dụng, chụp và kiểm thử khôi
phục một checkpoint trước update (một workspace khác biệt được lưu vào đó thành `workspace.tar.gz`), diễn tập các
migration mới trên một bản sao của database, migrate database đang chạy, khởi động các image mới, niêm phong deployment
record và chuyển workspace (§8). Không lệnh nào chạy `alembic downgrade`.

Sau một lần gián đoạn, `pf resume`:

- trước bất kỳ thay đổi database nào, mở lại deployment chưa thay đổi và đóng update với trạng thái `cancelled`;
  `pf resume --abandon` (`ABANDON <op8>`) làm cùng việc đó;
- sau một thay đổi trên database đang chạy, tiếp tục tiến về phía trước;
- khi kết quả của lần migration trên database đang chạy bị mất, đọc các Alembic head đang chạy: head bằng đích được tính
  là đã xong, còn mọi head khác dừng ở `needs_operator` (`effect-unknown`), vì không loại trừ được một thay đổi dở dang.
  Hãy khôi phục bằng `pf rollback <before-update checkpoint> --restore-db`, lệnh này bảo toàn database hiện tại trước.

`<op8>` là tám ký tự cuối của định danh operation mà `status` in ra.

## 10. Backup và rollback

`pf backup` chụp một checkpoint theo revision trong `<backups>/revisions/<project>/<checkpoint-id>/` và kiểm thử khôi phục
bản dump database của nó trong một database tạm:

```text
source.tar.gz       the exact deployed source
workspace.tar.gz    only when the workspace differs from the deployed source
database.dump       the active PostgreSQL database
database.list
manifest.json       what was captured, with every payload's size and hash
manifest.sha256
```

`.env` không thuộc checkpoint; các recovery bundle của purge giữ nó (§12).

**Các loại (class).** Một bản chụp là một *healthy checkpoint* (schema đang chạy khớp với các image đã deploy và biết
chính xác source), một *emergency preservation* (bảo toàn khẩn cấp: dữ liệu thực, có ghi lại sự không khớp) hoặc
*partial* (một bản chụp khẩn cấp mà không xác định được một image hoặc source). Chỉ một healthy checkpoint mới là đích
rollback (`checkpoint-not-rollback-target`).

**Mức kiểm chứng.** Một verification record riêng trong `<root>/instances/<uuid>/artifacts/verifications/` cho biết mức:
`captured` (chưa được kiểm thử), `failed` (lần kiểm thử khôi phục gần nhất thất bại), `data-restore` (mọi database đã được
khôi phục từ chính bản dump của bundle và được kiểm tra) hoặc `functional` (đã được khôi phục và kiểm tra trong một bản
sao cô lập của ứng dụng, §12). `pf backup` in cả hai:

```text
Checkpoint class: healthy_checkpoint
Verification level: data_restore_verified (record <verification-id>)
```

**Đọc chặt chẽ.** Mọi lệnh dùng một checkpoint hoặc một bundle trước tiên kiểm tra hash của manifest, chính manifest, và
kích thước cùng hash của mọi payload; một tệp mà manifest không liệt kê sẽ bị từ chối. Không có gì được giải nén hay xác
nhận trước bước đó, và các bản lưu trữ được giải nén mà không có liên kết, tệp đặc biệt, đường dẫn tuyệt đối hay đường dẫn
`..`.

**Từ chối trước khi chụp.** `backup`, `update`, `reset-db` và `purge` từ chối một healthy checkpoint khi các image đang
chạy không phải là image của deployment record (`deployment-image-mismatch`) hoặc khi không chứng minh được source đã
deploy (`deployment-artifact-mismatch`). Chúng cũng từ chối ngay ở bước kiểm tra trước một workspace khác biệt có chứa một
liên kết, một tệp đặc biệt hoặc vượt quá giới hạn của bản lưu trữ (`workspace-unsupported-entry`,
`workspace-archive-limit`; §16).

**Emergency preservation.** Khi một healthy checkpoint bị từ chối nhưng dữ liệu phải được giữ, hãy chạy một cách tương
tác:

```sh
pf --instance <slug> backup --emergency
```

Lệnh cho thấy sự không khớp quan sát được, yêu cầu gõ `EMERGENCY BACKUP <project>`, chụp và kiểm thử khôi phục database
hiện tại, và không bao giờ là đích rollback.

**Dung lượng trống.** Trước khi xác nhận, mọi lệnh vòng đời đo dung lượng nó cần trên mỗi thiết bị (backups, recovery,
workspace, artifacts, private state và thư mục dữ liệu của Docker), cộng thêm mức sàn `minimum_free_mb` một lần cho mỗi
thiết bị, và từ chối khi thiếu bằng `capacity-insufficient`. `capacity-unmeasurable` nghĩa là không đo được thư mục dữ
liệu của Docker; khi đó `backup --emergency` vẫn chạy được. `pf` không bao giờ xóa gì để lấy chỗ.

**Liệt kê.** `pf backups --page N` liệt kê các checkpoint, mới nhất trước, mười cái mỗi trang:

```text
<n>. <bundle-id>  [<healthy|emergency|partial>|<captured|failed|data-restore|functional>]  <reason>  DB=<heads>  source=<git_commit <sha12>|unknown|claimed <sha12>>  [legacy-format-N]
<n>. <folder>  [invalid: <code>]
```

Các checkpoint được ghi trước khi có deployment record (định dạng 1 và 2) được đọc qua một phép chuyển đổi cố định trong
bộ nhớ và không bao giờ bị viết lại; định dạng 1 được đọc thành partial, và commit mà một checkpoint định dạng 2 khai báo
được chứng minh lại trước khi dùng.

**Rollback.**

```sh
pf rollback                               # choose a checkpoint interactively
pf rollback <checkpoint-id>               # code only, keeps the current data (ROLLBACK <checkpoint-id>)
pf rollback <checkpoint-id> --restore-db  # code and database (RESTORE <database> <checkpoint-id>)
```

- Mọi rollback trước tiên dừng việc ghi của ứng dụng và bảo toàn database hiện tại: một healthy checkpoint, hoặc một
  emergency preservation khi schema không khớp với các image. Nếu không chụp và kiểm thử khôi phục được database, rollback
  dừng với `preservation-failed`: không có gì được khôi phục, các dịch vụ vẫn dừng, và `pf resume` mở lại deployment chưa
  thay đổi.
- Checkpoint phải là healthy, các image backend và frontend của nó phải vẫn còn, và source của nó phải khớp với manifest
  (`source-manifest-mismatch`). Một rollback chỉ mã nguồn cần một schema tương thích.
- `--restore-db` khôi phục vào một database ứng viên mới, kiểm tra head, locale, extension, chủ sở hữu và số dòng, và chỉ
  sau đó mới chuyển đổi; một khác biệt sẽ xóa database ứng viên (`checkpoint-incompatible`) và giữ nguyên database hiện
  tại. Database đang hoạt động trước đó được giữ lại dưới tên `pf_keep_*`, nên các lần ghi sau thời điểm checkpoint vẫn
  nằm ở đó và trong bản bảo toàn; không có gì được hợp nhất (merge). Phần tóm tắt đã xác nhận được giữ thành
  `operations/<op>/confirmation-summary.txt`.
- `rollback --restore-db` có thể thay thế (supersede) một `update`, `rollback` hoặc `reset-db` đang mở mà đã dừng ứng dụng
  hoặc cần người vận hành; một rollback chỉ mã nguồn chỉ có thể thay thế một update chưa thay đổi database nào.

Một checkpoint mới nhận quyền backups của policy đang có hiệu lực (§2); một mục kế thừa ACL từ thư mục của nó sẽ dừng bản
backup (`fresh-entry-acl`). Một bản backup bị gián đoạn để operation của nó mở; khi đó `pf backup` hoặc `pf resume` thực
hiện một lần thử mới với một định danh checkpoint mới.

## 11. Reset dữ liệu staging

```sh
pf reset-db
```

`reset-db` giữ phiên bản đã deploy và kích hoạt một database sạch đã được migrate lên các Alembic head hiện tại. Lệnh yêu
cầu gõ `RESET <database>` (ví dụ `RESET partflow_staging`), chụp database hiện tại trước, tạo và migrate một database ứng
viên sạch, chuyển đổi tên các database, và giữ database đang hoạt động trước đó dưới tên `pf_keep_*`, database này không
bao giờ bị xóa. Lệnh không xóa volume PostgreSQL. Dùng `reset-db` để xóa dữ liệu test; dùng `purge` (§12) để trở về một
instance hoàn toàn mới.

Trước khi xác nhận, lệnh quyết định dựa trên những gì nó quan sát được:

- các head đang chạy bằng các head của image backend đang chạy: bản chụp là một healthy checkpoint `before-reset`;
- các head đang chạy khác, nhưng image đang chạy là image mà deployment record ghi (một sự không khớp giữa schema và
  image): bản chụp là một **emergency preservation**, và database sạch được migrate lên các head của image đang chạy. Bản
  bảo toàn đó là dữ liệu và bằng chứng, không bao giờ là đích rollback. Để lấy lại dữ liệu cũ, hãy khôi phục database
  `pf_keep_*` được giữ lại bằng tay: `pf` không có lệnh nào chuyển ngược về nó;
- image đang chạy không phải image đã ghi: bị từ chối với `reset-deployment-image-mismatch` (hãy đưa deployment về đúng
  trước bằng `update` hoặc `rollback`);
- không đọc được hợp đồng của image: bị từ chối với `reset-contract-unknown`.

Một lần chụp thất bại dừng với `preservation-failed` trước khi có gì thay đổi. Sau một lần gián đoạn, `pf resume` làm lại
database ứng viên, còn `pf resume --abandon` trước khi chuyển đổi sẽ xóa nó và mở lại deployment chưa thay đổi. Một lần
chuyển đổi mà tên các database chứng minh là chưa bắt đầu sẽ được làm lại; một lần để tên ở trạng thái đổi dở dang dừng ở
`needs_operator` (`database-switch-unknown`); hãy khôi phục bằng `pf rollback <checkpoint> --restore-db` về một healthy
checkpoint. Một lần reset bắt đầu khi schema và image không khớp không thể mở lại deployment cũ: `pf resume` hoàn tất nó,
còn `pf resume --abandon` xóa database ứng viên và để các dịch vụ ở trạng thái dừng; sau đó chạy lại `pf reset-db`, hoặc
`pf rollback <checkpoint> --restore-db` về một healthy checkpoint.

## 12. Purge, khôi phục và deploy lại từ đầu

`pf purge` lưu mọi thứ cần để dựng lại instance vào một recovery bundle đã được kiểm chứng, rồi xóa các resource Docker
của instance:

```sh
pf instances                       # list the registered instances
pf purge                           # asks whether to delete the checkpoint tree too
pf purge --keep-backups            # keep the revision checkpoints
pf purge --delete-backups          # delete them; they are archived in the bundle
pf purge --reset-admin-config      # also delete pf-config.json
```

Trình tự:

1. **Plan.** `pf` liệt kê chính xác các container, volume, network và image tag của instance theo nhãn instance của
   chúng. Một resource mà nó không thể nhận là của mình (không có nhãn, của bên khác, dùng chung hoặc xung đột) làm purge
   bị từ chối với `resource-blocked` trước khi có gì xảy ra.
2. **Tóm tắt và `PURGE <project>`.** Việc ghi của ứng dụng dừng lại.
3. **Bundle.** Recovery bundle được chụp và mọi database được kiểm thử khôi phục. Database nguồn được kiểm tra bằng lệnh
   bất biến của chính ứng dụng (`app.cli reconcile`); một lỗi sẽ dừng purge (`app-check-failed`).
4. **Kiểm chứng cô lập.** Chính bundle đó được khôi phục vào một Compose project dùng một lần `pfverify-<12 hex>` có
   volume riêng, một network nội bộ, không công bố cổng nào và một mật khẩu database được tạo tự động, rồi được kiểm tra:
   các database, tình trạng, image, source, cấu hình và cùng một kết quả reconcile như database nguồn. Một kiểm tra thất
   bại sẽ hủy purge (`functional-verification-failed`), mở lại ứng dụng và giữ bản sao đã dừng để xem xét.
5. **Plan cuối cùng và các xác nhận.** Plan xóa ràng buộc (binding deletion plan) được in ra và đóng băng. Xác nhận
   `DELETE <database>`, rồi, khi đã chọn, `DELETE BACKUPS <project>` và `RESET ADMIN CONFIG <project>`, và cuối cùng
   `ERASE <project> <random challenge>`. Không có cách nào bỏ qua.
6. **Xóa.** Các container, network, volume và các image tag mà bundle bao phủ được xóa từng cái một, mỗi cái được kiểm tra
   lại ngay trước khi xóa; một thay đổi sẽ dừng lại với `plan-drift`. Không có gì bị prune, `compose down -v` không bao giờ
   được dùng, việc xóa image không bao giờ bị ép buộc và các đường dẫn bind-mount không bao giờ bị xóa. Sự bảo đảm này giả
   định rằng không có người quản trị Docker nào khác thao tác cùng lúc.
7. **Registry.** Record của instance được đánh dấu `state: purged` và giải phóng Compose project của nó, để một instance
   khác có thể đăng ký project đó. Record, định danh và permission policy của nó vẫn còn.

Bundle được lưu trong `<recovery>/<project>/purge-<time>-<sha12>-<suffix>/`:

```text
source.tar.gz, workspace.tar.gz          deployed source; the workspace when it differs
configuration/.env                       the runtime environment (secrets)
configuration/pf-config.json             the administration settings
images.tar                               application and database images, by image ID
postgres-globals.sql                     evidence only, never executed
databases/active.dump, databases/<retained>.dump
revision-checkpoints.tar.gz              the checkpoint history
deployment/deployment-record.json, deployment/compose-resolved.json
state/*.json, manifest.json, manifest.sha256
```

**Purge bị gián đoạn.** Trước `ERASE`, cả `pf resume` lẫn `pf resume --abandon` đều mở lại deployment chưa thay đổi. Sau
`ERASE`, `pf purge` hoặc `pf resume` tiếp tục sau `RESUME PURGE <project> <bundle-id>` với chỉ các mục còn lại trong plan;
không bao giờ thêm mục mới. Khi recovery bundle không còn đọc được hoặc không còn kiểm chứng được, việc xóa vẫn bị chặn
(§16).

**Deploy hoàn toàn mới sau purge.** Chạy `pf deploy --latest`. Purge đã xóa `.env`, nên wizard tạo một tệp mới với một mật
khẩu database mới. Sau `--reset-admin-config`, hãy chạy `pf --instance <slug> config admin` trước.

**Khôi phục chính xác một instance đã purge.**

```sh
pf recoveries [--page N]
pf restore-instance <bundle-id>
```

Đích phải trống (`resource-target-not-empty`). Xác nhận `RESTORE INSTANCE <project>`, rồi `RESTORE <database> <bundle-id>`.
Lệnh khôi phục tạo lại workspace, `.env`, các image, mọi database và lịch sử checkpoint, kiểm tra tình trạng, so sánh các
bất biến của ứng dụng với bundle (`Application invariants: …`, chỉ là bằng chứng) và niêm phong một deployment record mới
có ghi tên bundle. Control release hiện tại và `pf-config.json` hiện tại vẫn có hiệu lực; bản sao `pf-config.json` của
bundle được giữ để so sánh. Một `.env` có sẵn với nội dung khác được giữ thành `.env.proposal-<op8>`, và một lịch sử
checkpoint khác biệt được dời sang `<backups>/revisions/<project>.pre-restore-<op8>`. `pf resume` tiếp tục một lần khôi
phục bị gián đoạn theo chiều tiến; `pf resume --abandon` (`ABANDON RESTORE <project> <op8>`) chỉ dùng được khi đích đang
được chuẩn bị.

- Một bundle chỉ khôi phục vào instance có định danh mà bundle ghi lại. Bundle của một instance khác, kể cả bundle từ một
  máy chủ đã mất, bị từ chối với `restore-target-mismatch`: nó sẽ cần một lối nhập (import route) có phê duyệt policy, điều
  mà control này không cung cấp. Một bundle cũ không ghi định danh chỉ được chấp nhận cho cùng một Compose project.
- Các đường dẫn ghi trong bundle chỉ là thông tin nguồn gốc (provenance); lệnh khôi phục luôn ghi vào các thư mục của
  instance đã chọn.
- Khi daemon không có image `postgres:16`, image database của bundle được nạp từ `images.tar` và gắn tag `postgres:16`; tag
  đó được mọi instance trên daemon dùng chung.
- Một record đã purge được đăng ký lại, trừ khi một instance khác đang giữ project (`instance-claim-taken`).

**Khôi phục song song (side-by-side).** Để xem xét hoặc xuất dữ liệu của một trong các bundle của chính instance trong khi
instance vẫn chạy:

```sh
pf restore-instance <bundle-id> --side-by-side
```

Xác nhận `RESTORE COPY <bundle-id>`. Bundle được khôi phục vào một recovery target được giữ lại, là một Compose project
riêng `pfrecover-<12 hex>` có volume riêng, một network nội bộ, không có listener và một mật khẩu database được tạo tự
động, và được kiểm tra như trong purge. Instance đang chạy không bị thay đổi. `pf status` liệt kê target; hãy xem xét nó
bằng quyền root, ví dụ:

```sh
sudo docker ps --filter label=com.docker.compose.project=pfrecover-0123456789ab
sudo docker exec -it <db container> psql -U <user> -d <database>
```

Xóa nó bằng `pf cleanup --apply --recovery-target <project>` (`REMOVE RECOVERY TARGET <project>`); khi nó còn tồn tại,
instance không thể bị purge (`isolated-topology-present`). `pf resume --abandon` của một lần khôi phục song song bị gián
đoạn sẽ xóa target (`ABANDON RECOVERY TARGET <project>`).

Dữ liệu PartFlow không bao giờ được tự động hợp nhất giữa các database: Part Movement, dòng dõi số lượng (quantity
lineage), allocation và reversal tuân theo các quy tắc nghiệp vụ mà các lệnh SQL insert chung chung sẽ phá vỡ. Hãy khôi
phục dữ liệu cũ song song, và đưa bất cứ thứ gì sang với một quy trình tường minh, hiểu nghiệp vụ.

## 13. Kiểm tra bản phát hành và tác vụ theo lịch

`pf --instance <slug> release-check [--channel stable|prerelease]` cho thấy, một cách tương tác, có hay không một bản phát
hành mới hơn đủ điều kiện; áp dụng nó bằng `pf --instance <slug> update --release <tag>`.

Không có gì chạy tự động không người giám sát trên một instance `pf`. Khi chạy không có terminal, `backup` và
`release-check` bị từ chối với `policy-grant-required` (mã thoát 20), còn `release-check --apply` luôn bị từ chối với
`auto-apply-not-permitted` (mã thoát 20): vận hành không người giám sát cần một policy grant được bảo vệ, và không policy
nào đã cài cấp quyền đó. `"auto_update": true` trong `pf-config.json` không thay đổi gì. Các scheduler wrapper trong
`<root>/bootstrap/` (`backup.sh --instance <slug>` và `release-check.sh --instance <slug>`) bị từ chối theo cùng cách. Các
lệnh yêu cầu gõ xác nhận bị từ chối khi không có terminal (`terminal-required`). Hãy tạo checkpoint bằng tay với
`pf --instance <slug> backup` và sao chép chúng ra ngoài NAS.

## 14. Chế độ xem Compose và truy cập Compose thô

`pf` cung cấp hai chế độ xem Compose chỉ đọc và không chuyển tiếp lệnh Compose nào khác:

```sh
pf --instance <slug> ps [-a] [-q] [--services] [--status STATUS] [--format table|json] [SERVICE...]
pf --instance <slug> logs [--tail N] [-f] [-t] [--no-color] [--no-log-prefix] [--since V] [--until V] [SERVICE...]
```

`SERVICE` là `db`, `backend` hoặc `frontend`; `V` là một khoảng thời gian như `30m` hoặc một ngày giờ theo RFC 3339.
`--tail` mặc định là 200 dòng (1 đến 10000), và `logs -f` dừng sau một giờ hoặc 64 MiB đầu ra với `logs-bound-reached`.
Cả hai chế độ xem không lấy khóa và ẩn mật khẩu database; gõ `pf` mà không kèm lệnh nào thì tương đương `pf ps`. Mọi từ
Compose khác bị từ chối với `compose-route-removed`, thông báo này nêu lệnh được quản lý cần dùng thay thế. Không có lối được quản lý nào để
chạy một lệnh của ứng dụng bên trong container backend.

Khi không tránh được một lệnh Compose thô, ví dụ để chạy kiểm tra đối soát của
[`OPERATIONS_RUNBOOK.vi.md`](./OPERATIONS_RUNBOOK.vi.md) §7 trên một instance staging, hãy đưa cho nó mọi đầu vào một
cách tường minh:

```sh
sudo env PARTFLOW_REPO_ROOT=<workspace> \
  PARTFLOW_DATABASE_URL='postgresql+psycopg://<user>:<percent-encoded password>@db:5432/<database>' \
  DEPLOY_ADMIN_INSTANCE_ID=<instance UUID> \
  docker compose \
  --project-directory <workspace> \
  --env-file <configuration>/.env \
  -p <project> \
  -f <root>/releases/<release-id>/compose.nas.yaml \
  -f <root>/instances/<instance UUID>/state/active-images.yaml \
  ps
```

`pf status` in UUID của instance, installation root và định danh của control release. Thay `ps` bằng lệnh bạn cần, ví dụ
`run --rm --no-deps -T backend python -m app.cli reconcile` cho kiểm tra đối soát.

- Một lệnh thô bỏ qua các khóa và cơ chế bảo vệ của `pf`. Không bao giờ chạy nó khi `pf status` cho thấy một operation
  đang mở, không bao giờ chạy cùng lúc với một lệnh `pf`, và không bao giờ chạy với một `.env` đã sửa sau khi một operation
  đang mở đã đóng băng nó.
- Một `DEPLOY_ADMIN_INSTANCE_ID` sai sẽ gắn nhãn các resource mới với định danh sai; khi đó `pf` phân loại chúng là của
  bên khác hoặc xung đột và từ chối `purge`, `abort-deploy` và các lệnh được bảo vệ khác cho đến khi chúng được xem xét.
- Không bao giờ dùng `docker system prune --volumes` hoặc `docker volume prune` trên một máy chủ có thể chạy các tác vụ
  khác.

## 15. Update control plane

Một lần `update` ứng dụng không bao giờ update control. Hãy cài một control release đã review một cách tường minh:

```sh
sudo <root>/bootstrap/pf install control --source <reviewed repository tree>
sudo <root>/bootstrap/pf install control --release r-<16 hex>   # select a retained release again
```

- Phần tóm tắt cho thấy release đang được gắn và release mới, mọi instance sẽ được gắn vào nó (một control release phục
  vụ toàn bộ root) và việc ứng dụng có cần một operation riêng hay không. Xác nhận `INSTALL CONTROL <release-id>` (hoặc
  `SELECT CONTROL <release-id>`).
- Release được chuẩn bị riêng và smoke-check bằng trình thông dịch đã đăng ký: nó phải import được, nạp được registry và
  mọi record, và chấp nhận `pf-config.json` và `.env` đang dùng của mọi instance. Sau đó nó được gắn và kiểm chứng từ đầu
  đến cuối qua bootstrap thật. Nếu việc đó thất bại, binding trước đó được tự động khôi phục (`install-verify-failed`,
  operation `rolled_back`).
- `app_operation_required: update` nghĩa là release mới thay đổi `compose.nas.yaml`; lần `pf update` tiếp theo của mỗi
  instance sẽ áp dụng topology mới.
- Launcher và verifier được cố định: một ứng viên thay đổi `pf.sh` hoặc `pf_bootstrap.py` bị từ chối
  (`bootstrap-change-unsupported`). Một ứng viên chỉ thay đổi một scheduler wrapper là `control-unchanged`.
- Việc cài một release không bao giờ migrate `pf-config.json`; một release cũ hơn có thể từ chối một tệp schema 2 trong
  smoke check của nó (`install-smoke-failed`, không có gì được gắn).
- Một install operation bị gián đoạn được tiếp tục bằng `pf install resume` hoặc được hoàn tác bằng
  `pf install resume --abandon`.
- `pf install control` bị từ chối khi có bất kỳ operation nào của instance đang mở (`instance-operation-pending`), kể cả
  một journal không hợp lệ hoặc một chỉ mục operation bị tràn. Chỉ có thể quay về một release cũ hơn khi không có operation
  nào đang mở; một release cũ hơn không biết các loại operation và các bản ghi của release mới hơn.

Bước tường minh này là ranh giới bảo mật cho phép workspace vẫn ghi được bởi người dùng: không bao giờ cài một cây mã chưa
review hoặc không rõ nguồn gốc.

**Runner record không có journal.** Runner ghi lại mọi tiến trình con có thay đổi đã hết thời gian hoặc bị ngắt
(`unresolved-effects.json` trong thư mục operation của nó). Một journal đã đóng giải quyết các record của chính operation
đó, nhưng các record trong một thư mục operation không có journal tiếp tục làm `pf install` bị từ chối với
`instance-effects-unresolved`. Các thư mục như vậy đến từ các operation của những control cũ hơn operation journal, và từ
mọi lệnh vòng đời có tiến trình con bị ngắt hoặc hết thời gian trước khi xác nhận, khi chưa có plan nào
(ví dụ Ctrl-C trong lúc `compose build` ứng viên, các kiểm tra hợp đồng `compose run` của `deploy`, `update` và `rollback`,
hoặc `ensure_local_contract`). Hãy xem xét chúng bằng `pf status`, bảo đảm không còn gì của chúng đang chạy, giữ lại thư
mục, và xác nhận đã biết (acknowledge) các record:

```sh
pf resume --operation <op> --acknowledge
```

Xác nhận `ACKNOWLEDGE <op8>`. `pf` trước tiên từ chối khi một container chạy một lần (one-off) thuộc instance vẫn đang
chạy hoặc, khi một record ghi một tác động lên database, khi có một phiên client đang mở trên một database mà các record
nêu (`effect-still-running`). Sau đó nó ghi `operations/<op>/acknowledgement-<hash12>.json`, gắn với các record hiện tại;
một record được thêm sau đó sẽ lại chặn. Một operation có journal, một thư mục không có record hoặc một thư mục đã được
acknowledge bị từ chối với `acknowledge-not-legal`.

## 16. Xử lý sự cố

Bắt đầu với:

```sh
pf --instance <slug> status
pf --instance <slug> status --operation <operation-id>
```

`status` in mọi operation đang mở trước mọi thứ khác:

```text
Operations: <op> <kind> phase <phase> sequence <n> updated <time> [blocking|needs operator]
  last error: <code>: <message>
  unresolved effect: <eid> <type> <target> (<state>)
  next (recorded at sequence <n>): <command>: <description>; ...
```

Sau đó nó liệt kê các operation gần đây, các thế hệ workspace và các runner record; `status --operation` cho thấy các tác
động của một operation cùng trạng thái và bằng chứng của chúng. Chỉ làm theo các bước tiếp theo được in ra. **Không bao
giờ xóa hay sửa một journal:** một thư mục operation là bằng chứng, và plan của nó là sự phê duyệt. Một lần từ chối xảy ra
trước mọi thay đổi kết thúc bằng `Nothing was changed.`; một lần từ chối muộn hơn nêu trạng thái mà nó để lại. Sau một
operation thất bại, `pf` chỉ dừng các job one-off của chính instance này rồi đến các dịch vụ ứng dụng của nó, và thông báo
nêu `pf --instance <slug> status`.

### Operation, journal và resume

| Bạn thấy gì | Cần làm gì |
| --- | --- |
| Một operation khác đang mở (`operation-open`). | Chạy một trong các bước tiếp theo hợp lệ mà thông báo liệt kê. |
| Không có cách tiếp tục tự động nào là an toàn (`operation-needs-operator`, `effect-unknown`, `database-switch-unknown`). | Dùng lối khôi phục được in ra: `rollback <checkpoint> --restore-db` sau một update, rollback hoặc reset; `abort-deploy` cho một lần deploy đầu tiên; `backup --emergency` để bảo toàn dữ liệu trước. |
| Một tiến trình đã ghi, một container one-off thuộc instance hoặc một phiên database vẫn đang chạy (`effect-still-running`). | Chờ, rồi chạy lại `resume`; `resume` không bao giờ dừng nó. Với `effect-probe-unavailable`, không đọc được `/proc` hoặc định danh lần khởi động (boot), nên không có gì được quyết định. |
| `resume` không khởi động được database để quan sát một tác động (`database-unavailable`). | Khởi động Docker rồi thử lại; operation không thay đổi. |
| Ghi chú `db-started`. | Container `db` đã bị dừng từ bên ngoài; `pf` đã khởi động lại chính container đó (không bao giờ tạo container mới) và tiếp tục. |
| Record của instance, policy, control release, profile hoặc daemon đã thay đổi sau khi phê duyệt (`plan-authority-changed`). | Khôi phục trạng thái đã phê duyệt. |
| Một đầu vào đã đóng băng bị thay đổi: một bundle, một checkpoint, `.env` hoặc thiết lập đã đóng băng, hoặc deployment đã chuẩn bị (`plan-input-changed`). | Khôi phục nó nguyên từng byte, rồi `resume`. Khi thông báo nói rằng một recovery bundle không còn đọc được hoặc không còn kiểm chứng được, việc xóa vẫn bị chặn và operation vẫn mở: hãy khôi phục thư mục bundle nguyên từng byte từ một bản sao ngoài NAS, rồi chạy `resume`. |
| Một lệnh yêu cầu các lựa chọn khác với những gì operation đang mở đã phê duyệt (`plan-inputs-conflict`). | Chạy lại mà không có các lựa chọn đó, hoặc dùng `resume`. |
| `abandon-not-legal`, `abandon-in-progress`, `keep-workspace-not-legal`. | Một tác động thực đã bắt đầu, nên chỉ `resume` tiếp tục được; chỉ `resume --abandon` tiếp tục một lần abandon đã được chấp nhận; workspace mới đã được gắn. |
| Workspace, thế hệ được giữ lại hoặc bản stage không phải là những gì journal đã ghi (`workspace-generation-mismatch`). | Khôi phục trạng thái đã ghi (ví dụ xóa một thư mục được tạo ở đường dẫn workspace), rồi `resume` hoặc `resume --keep-workspace`. Với `workspace-validation-failed`, sửa điều được phát hiện, rồi `resume`. |
| Thư mục lịch sử checkpoint không phải là thư mục có trước lần khôi phục, cũng không phải thư mục đã được khôi phục (`checkpoint-history-unknown`). | Dời cây lạ sang một bên bằng quyền root, rồi chạy `resume --operation <op>`. |
| Một bên ghi khác đã thay đổi journal (`journal-changed`). | Chạy `status`. |
| Có nhiều hơn một operation đang mở (`operation-conflict`). | Xem xét chúng bằng `status --operation`. Một bản backup bên cạnh một update đang chờ ở `workspace_sync_pending` là cặp được phép: hãy hoàn tất hoặc bỏ bản backup trước (§8). |
| Một journal hoặc plan không hợp lệ, hoặc có một `state/pending.json` do một control cũ hơn ghi (`operation-journal-invalid`, `journal-format-unsupported`). | Mọi lệnh có thay đổi bị từ chối; `status`, `doctor`, `backups`, `recoveries`, `ps` và `logs` vẫn chạy. Giữ nguyên thư mục và xem xét nó bằng `status --operation`. |
| Có hơn 20000 mục trong `operations/` (`operation-index-overflow`). | Lưu trữ các operation **đã đóng** (`completed`, `cancelled` hoặc `failed_preserved`, như `status --operation` cho thấy) bằng quyền root sang một thư mục bên ngoài `operations/`; không bao giờ dời một operation đang mở hoặc đã bị thay thế. Cho đến lúc đó mọi lệnh có thay đổi bị từ chối và các ứng viên cleanup cũng không được liệt kê. |
| Các runner record chặn `pf install` (`instance-effects-unresolved`, `acknowledge-not-legal`). | §15. |

### Checkpoint, bundle và deployment record

| Bạn thấy gì | Cần làm gì |
| --- | --- |
| Thư mục đã bị thay đổi hoặc hỏng (`manifest-checksum-mismatch`, `manifest-invalid`, `manifest-schema-unsupported`, `bundle-payload-mismatch`, `bundle-unlisted-file`). | Không sửa hay "vá" nó; sao chép nó ra ngoài NAS làm bằng chứng và dùng một checkpoint hoặc bundle khác. Một thư mục được sao chép trở lại phải giống từng byte (không thêm `.DS_Store` hay `Thumbs.db`). Các danh sách hiển thị nó là `[invalid: <code>]`. |
| Một bản lưu trữ chứa một liên kết, một tệp đặc biệt hoặc một đường dẫn không an toàn, bị thay đổi trong lúc đọc, hoặc không đủ chỗ (`archive-member-refused`, `archive-unreadable`, `archive-changed`, `archive-capacity`). | Không có gì được giải nén. Với `archive-capacity`, giải phóng dung lượng rồi thử lại. |
| Workspace khác biệt chứa một tệp lớn hơn 128 MiB, tổng cộng hơn 512 MiB hoặc 200000 tệp, hoặc một liên kết hay tệp đặc biệt (`workspace-archive-limit`, `workspace-unsupported-entry`). | Dời các tệp đó ra khỏi workspace rồi thử lại; trong lúc đó `backup --emergency` bảo toàn database. |
| Source được giải nén khác với manifest của nó (`source-manifest-mismatch`). | Dùng một checkpoint khác. |
| Checkpoint là một bản chụp khẩn cấp hoặc partial (`checkpoint-not-rollback-target`). | Xuất dữ liệu của nó bằng tay, và rollback về một healthy checkpoint. |
| Database ứng viên của `--restore-db` không qua một kiểm tra (`checkpoint-incompatible`). | Không có gì thay đổi: database ứng viên đã bị xóa và database hiện tại không thay đổi. |
| Không chụp và kiểm thử khôi phục được database hiện tại (`preservation-failed`). | Bảo toàn nó bằng tay (một lệnh `pg_dump --format=custom` của database từ dịch vụ `db` sang một nơi chỉ root đọc được) hoặc sửa nguyên nhân rồi chạy `backup --emergency`; sau đó thử lại, hoặc chạy `resume` để mở lại deployment chưa thay đổi. |
| Một tệp của deployment record khác với record (`deployment-artifact-mismatch`). | Giữ thư mục làm bằng chứng; không bao giờ xóa nó. Khi là ghi chú, bản chụp đã tiếp tục với source được chứng minh từ kho được bảo vệ hoặc từ workspace. Khi là lời từ chối, hãy khôi phục kho source được bảo vệ hoặc rollback về một healthy checkpoint. |
| Các image đang chạy không phải image đã ghi (`deployment-image-mismatch`). | Tìm xem ai đã thay đổi các container, bảo toàn dữ liệu bằng `backup --emergency` nếu cần, rồi deploy lại hoặc rollback. |
| Không chuẩn bị được deployment, hoặc kho của nó thiếu chỗ (`deployment-stage-failed`, `artifact-capacity`). | Sửa chi tiết được nêu (thường là dung lượng hoặc quyền của `artifacts/deployments`, nơi cần kích thước source cộng thêm 64 MiB) rồi thử lại; không có gì thay đổi. |
| Ứng dụng chạy nhưng record của nó chưa được niêm phong (`deployment-record-incomplete`). | Lần `deploy`, `update`, `rollback` hoặc `restore-instance` tiếp theo sẽ niêm phong một record. |
| Cổng xóa (deletion gate) của purge từ chối sau `ERASE` (`purge-bundle-unverified`, `purge-writers-running`, `purge-source-changed`). | Ứng dụng đã được mở lại; sửa nguyên nhân được nêu rồi chạy lại `purge`. |
| Một kiểm tra của bước kiểm chứng cô lập, hoặc lệnh reconcile trên database nguồn, thất bại (`functional-verification-failed`, `app-check-failed`). | Không có gì bị xóa. Xem xét bản sao được giữ lại, rồi xóa nó bằng `cleanup --apply`. |
| Một project `pfverify-*` hoặc `pfrecover-*` được giữ lại của instance này vẫn tồn tại (`isolated-topology-present`). | Chạy `cleanup --apply`, thêm `--recovery-target <project>` cho một recovery target. |
| Không dựng được bản sao cô lập trên máy chủ này, hoặc tên ngẫu nhiên của nó đã bị dùng (`verification-isolation-unsupported`, `topology-name-collision`). | Không có gì bị xóa; với trường hợp trùng tên, chạy lại lệnh. |
| `restore-target-mismatch`, `instance-claim-taken`, `registry-busy`. | Bundle thuộc về một instance khác (§12); một instance khác đang giữ project; một lần cài đặt đang giữ khóa registry, nên hãy chạy `resume` sau khi nó xong. |
| Việc nạp `images.tar` sẽ dời một tag đang chỉ đến một image khác (`image-load-would-retag`). | Xóa hoặc đổi tên tag đó trước. |
| Một target song song đã thay đổi trong lúc operation của nó bị gián đoạn và đã bị xóa (`recovery-target-lost`). | Chạy lại lần khôi phục song song. |
| Các ghi chú `verification-record-invalid`, `legacy-manifest-migrated`, `db-image-changed`, `verification-failed`. | Chỉ là thông tin. |

### Quyền

| Bạn thấy gì | Cần làm gì |
| --- | --- |
| `permissions-verb-required` (mã thoát 2). | Dùng `check`, `plan` hoặc `apply`. |
| Policy vi phạm một quy tắc, hoặc yêu cầu chạy script trong workspace là "none" (`permission-policy-invalid`, `permission-policy-unsupported`). | Không có gì thay đổi; chọn lại trong `apply`. |
| Một nhóm của policy, hoặc nhóm của thư mục backups hay recovery, không tồn tại trên máy chủ này (`permission-group-missing`). | Chọn một nhóm có sẵn trong `apply`; nhóm không bao giờ được tạo mới. |
| `permission-policy.json` không phải một tệp đơn `0600` thuộc root hoặc không qua các kiểm tra (`permission-approval-invalid`). | Nó không bao giờ được tự động thay thế và không policy suy ra nào được dùng, nên backup và các lệnh quyền dừng lại. Bằng quyền root, dời nó sang một bên trong cùng thư mục, ví dụ thành `permission-policy.invalid-<UTC time>.json`; khi đó instance dùng policy suy ra, và `apply` ghi một revision 1 mới. |
| Một điều được phát hiện ở thư mục được bảo vệ nằm ngoài các scope (`permissions-context-refused`). | Sửa những gì `pf doctor` cho thấy. |
| Một blocker trong một scope: `scope-path-unsafe`, `scope-entry-link`, `scope-entry-special`, `scope-entry-hardlinked`, `scope-mount-boundary`, `scope-contains-app-storage`, `scope-untrusted-owner`, `scope-entry-acl`, `scope-too-large`. | Xóa liên kết, tệp đặc biệt, hard link hoặc ACL, dời mount hoặc nơi lưu trữ của ứng dụng ra ngoài, hoặc bỏ scope ra bằng `--scope`. |
| Control release vi phạm giới hạn của nó (`control-ceiling`). | Chỉ `pf install control` thay đổi nó. |
| `editor-freeze-unavailable`, `editor-freeze-refused`. | §2, Editor freeze. |
| Một lần apply chưa hoàn tất (`permissions-apply-pending`, `permissions-entry-changed`, `permissions-verify-failed`, `permissions-interrupted`). | `apply --resume` lập plan lại phần còn lại với policy đã đóng băng; `apply --abandon` hoàn tác các thay đổi đã ghi theo thứ tự ngược lại. Với `permissions-already-approved`, chỉ `--resume` mới hoàn tất được. |
| Một dòng của journal tác động quyền không đọc được (`permissions-journal-invalid`). | Không có gì thay đổi và lần apply vẫn mở. Hãy xem xét `<root>/instances/<uuid>/operations/<op>/permission-effects.jsonl` bằng tay. |
| Một mục backup hoặc recovery mới kế thừa ACL từ thư mục của nó (`fresh-entry-acl`). | Operation đã dừng trước khi công bố mục đó. Xóa default ACL khỏi thư mục đó. |
| `permissions-abandon-conflicts`, `permissions-option-invalid`, `permissions-nothing-pending`. | Các đối tượng thay đổi sau lần gián đoạn được liệt kê và để nguyên; `--scope` không thể dùng chung với `--resume` hoặc `--abandon`; không có gì đang chờ. |
| Các kết quả và ghi chú như `permissions-current`, `permissions-applied`, `permission-policy-unapproved`, `workspace-concurrent-entry`. | Thông tin; chạy `check` để xem trạng thái. |
| Người dùng SMB thấy workspace nhưng không sửa được. | Chạy `pf permissions check --scope workspace`, rồi `pf permissions apply --scope workspace` từ `cd /`, và kiểm tra quyền Shared Folder của DSM. |
| Người dùng SMB không thay đổi được checkpoint hoặc recovery bundle. | Đúng thiết kế: chúng vẫn chỉ đọc đối với nhóm. Hãy sao chép chúng sang nơi khác nếu cần một bản có thể sửa. |

### Cài đặt và control

| Bạn thấy gì | Cần làm gì |
| --- | --- |
| `sudo sh ./pf.sh ...` từ chối chạy. | Đúng như dự kiến: chạy `sudo <root>/bootstrap/pf ...`, hoặc tạo một root trước (§5). |
| Bước kiểm tra trước liệt kê các xung đột dạng `code: subject: detail` (`install-preflight-refused`). | Giải quyết mọi mục rồi chạy lại đúng lệnh đó. Với `legacy-env-conflict` hoặc `legacy-admin-config-conflict`, giữ bản đúng trong `config/` và dời bản kia ra ngoài. |
| Một install operation đang mở (`install-operation-pending`). | Làm theo bước tiếp theo mà `pf install status` cho thấy. |
| Instance đã được chuyển từ v2.5 và `control/` của v2.5 vẫn còn (`legacy-control-active`). | Thực hiện thay đổi bằng v2.5. |
| `control-binding-changed`, `install-busy`. | Một lần cài control đã hoàn tất trong lúc lệnh khởi động, hoặc một operation khác đang giữ một khóa; chạy lại lệnh. |
| `resume` tìm thấy một đích mà trình cài đặt không ghi (`install-needs-operator`). | Khôi phục nó như thông báo mô tả, rồi chạy `pf install resume` (hoặc `--abandon` khi được đề xuất). |
| `install-smoke-failed`, `install-verify-failed`. | Ứng viên không được kích hoạt; với `control`, binding trước đó đã được khôi phục. |
| Một root đã công bố hoặc một bản đăng ký đã được dùng thì không thể bỏ (`abandon-not-possible`). | Hoàn tất nó bằng `pf install resume`. |
| Một mục của `<root>/install-operations/` không phải là một operation đọc được (`install-operation-unreadable`). | Xem xét nó bằng quyền root và dời nó ra khỏi thư mục đó (giữ một bản sao), rồi thử lại. |
| `install-interrupted`. | Chạy `pf install resume`. |

### Các wizard cấu hình

Mọi lời từ chối dưới đây không thay đổi gì.

| Bạn thấy gì | Cần làm gì |
| --- | --- |
| `config-option-invalid` (mã thoát 2). | `--configuration` và `--project` chỉ thuộc về `config admin` trước khi đăng ký. |
| `config-cancelled`, `config-current`. | Bạn đã từ chối, hoặc không có gì cần thay đổi. |
| `admin-config-required`, `admin-config-invalid`, `admin-config-migration-blocked`. | Tạo tệp bằng `config admin`, hoặc sửa vấn đề được nêu bằng tay; wizard không bao giờ sửa chữa một tệp. |
| Tệp khai báo một schema mới hơn (`admin-config-version-unsupported`). | Chọn lại control mới hơn (`pf install control --release <id>`) hoặc khôi phục tệp trước đó. |
| `admin-config-mismatch`. | Khôi phục `project` đã đăng ký hoặc `environment` của policy. |
| Một tệp schema 1 trong một thư mục chưa đăng ký được để nguyên (`admin-config-legacy-unregistered`, mã thoát 0). | Đăng ký trước, rồi chạy `config admin` với `--instance`. |
| `.env` không phân tích được (`app-config-invalid`). | Sửa dòng mà thông báo nêu; thông báo không bao giờ hiển thị giá trị. |
| Instance đã được deploy và một giá trị database bị thiếu hoặc không hợp lệ (`app-credential-unusable`). | Khôi phục nó từ recovery bundle hoặc từ hồ sơ của bạn. |
| `migration-issue`, `config-file-acl`, `config-file-unsafe`. | Sửa tệp bằng tay (§6). |
| Máy chủ không có dữ liệu múi giờ để kiểm tra một múi giờ mới (`zone-data-unavailable`). | Cài dữ liệu đó, hoặc ghi giá trị bằng tay. |
| `config-changed`, `config-busy`. | Tệp đã thay đổi trong lúc wizard chạy, hoặc một lần đăng ký đang giữ khóa; chạy lại lệnh. |
| `config-interrupted`, `config-audit-not-recorded` (mã thoát 1). | Tệp đã chứa nội dung mới; chạy lại lệnh để xem lại nó. |
| `config-dir-invalid`, `config-path-conflict`, `registry-record-invalid`, `registry-invalid`. | Thư mục bị thiếu, không an toàn hoặc đang được một instance đã đăng ký dùng, hoặc một record không nạp được; hãy sửa điều đó trước. |
| `admin-example-invalid`, `app-example-invalid`, `app-profile-undeclared`, `config-audit-invalid`. | Control đã cài không chạy được wizard; chạy `doctor` và cài lại control release. |

### Dòng lệnh, terminal và policy

| Bạn thấy gì | Cần làm gì |
| --- | --- |
| `compose-route-removed`, `compose-override-refused`, `unknown-option`, `unknown-command`. | `pf` không chuyển tiếp lệnh Compose nào và không chấp nhận tùy chọn Compose hay Docker nào như `-f`, `-p` hoặc `--env-file`, và các tùy chọn viết tắt bị từ chối. Hãy dùng lệnh được quản lý mà thông báo nêu, hoặc `pf ps` và `pf logs` (§14). |
| `terminal-required`. | Chạy lệnh một cách tương tác, ví dụ qua `ssh -t`. |
| `instance-required-unattended`. | Một lệnh không có terminal phải nêu instance của nó: `pf --instance <slug> <command>`. |
| `policy-grant-required`, `auto-apply-not-permitted` (mã thoát 20). | Vận hành không người giám sát không khả dụng (§13); chạy lệnh một cách tương tác. |
| `selection-conflict`. | `--project` nêu một project khác với `--instance`; chỉ dùng `--instance`. |
| `recovery-outside-instance`, `recovery-state-file-refused`. | Chỉ các bundle trong thư mục `<recovery>/<project>/` của chính instance mới được liệt kê hoặc khôi phục, và một bundle chỉ được khôi phục các tệp trạng thái `deployed.json`, `last-reset.json` và `observed-tags.json`. |
| `logs-bound-reached`. | Đầu ra cho đến giới hạn là đầy đủ; thu hẹp nó bằng `--since`, `--tail` hoặc một tên dịch vụ. |

### Docker daemon, Compose và resource

| Bạn thấy gì | Cần làm gì |
| --- | --- |
| Docker đang dừng hoặc không trả lời (`daemon-unreachable`). | Khởi động Container Manager (hoặc Docker) rồi thử lại; `status` vẫn in mọi phần không cần Docker. |
| Endpoint đã đăng ký trả lời như một engine khác với engine mà instance được gắn (`daemon-drift`). | Mọi bước Docker bị từ chối. Việc gắn lại là một tác vụ cài đặt; không bao giờ sửa record. |
| `daemon-rootless`, `daemon-info-invalid`, `daemon-endpoint-*`. | Chỉ hỗ trợ một daemon rootful cục bộ trên một socket được bảo vệ. |
| Mô hình Compose đã phân giải có một tùy chọn bất thường hoặc một image override đã bị thay đổi, hoặc không render được (`envelope-*`). | Không có gì được build hay khởi động; thông báo liệt kê từng điều phát hiện kèm vị trí của nó (không bao giờ kèm giá trị). |
| `resource-blocked`, `resource-not-owned`, `resource-target-not-empty`. | Xem xét các resource được liệt kê bằng `pf status`; các resource không có nhãn, trùng tên hoặc của bên khác không bao giờ được tự động tiếp quản hay xóa. |
| Một resource trong plan đã thay đổi sau khi plan được đóng băng, hoặc các container liên tục biến mất trong lúc được liệt kê (`plan-drift`, `inventory-unstable`). | Thử lại khi máy chủ yên tĩnh; một lần purge hoặc abort bị gián đoạn sẽ tiếp tục với plan đã đóng băng của nó. |
| Có `.env`, nhưng một lệnh Compose thô báo thiếu biến. | Một lệnh thô không đọc `.env` của thư mục configuration cũng như các giá trị được tạo; dùng `pf doctor`, hoặc dạng đầy đủ ở §14. |

### Dung lượng và phần thừa

`capacity-insufficient: <phase> needs <n> MiB on device <d> (<roles>: <paths>), <m> MiB free including the <f> MiB safety
floor` nêu mọi vai trò dùng chung thiết bị bị thiếu chỗ. Hãy giải phóng dung lượng ở đó (một lịch sử `.pre-restore-*` cũ,
các tệp đã xuất, hoặc các phần thừa bên dưới); không bao giờ xóa healthy checkpoint cuối cùng hoặc một purge bundle để lấy
chỗ. Thư mục dữ liệu của Docker được mọi instance trên daemon dùng chung.

`pf cleanup` liệt kê, mà không thay đổi gì, các phần thừa mà những operation đã đóng ghi lại: các database ứng viên
(`pf_verify_*`, `pf_migrate_*`, `pf_restore_*`, `pf_clean_*`), các project `pfverify-*` được giữ lại, các lần thử bundle
thất bại và image tag của chúng, các bản stage đã bị thay thế, và các mục cần một bộ chọn. Một tên chỉ khớp một mẫu không
bao giờ bị đụng đến. `pf cleanup --apply` xóa tập mặc định sau `CLEANUP <project> <op8>`; một mục đã thay đổi hoặc đang bận
được giữ lại (`cleanup-items-kept`). Các mục cần một bộ chọn:

- `--recovery-target <project>` (`REMOVE RECOVERY TARGET <project>`);
- `--checkpoint-history <project>.pre-restore-<8 hex>` (`DELETE CHECKPOINT HISTORY <name>`), chỉ khi mọi checkpoint trong
  đó cũng có mặt, không thay đổi và đọc được, trong lịch sử đang dùng;
- `--generation wsg-<…>` loại bỏ một thế hệ workspace. Trước tiên hãy đóng mọi trình soạn thảo hoặc phiên SMB đang mở tệp
  trong đó (`generation-in-use`). Cây được lưu trữ kèm một niêm phong vào
  `<backups>/generations/<project>/<generation>/workspace.tar.gz` rồi bị xóa; một liên kết hoặc tệp đặc biệt bên trong sẽ
  bị từ chối (`generation-unsupported-entry`). Để lấy lại một tệp, giải nén nó bằng quyền root ra ngoài workspace, ví dụ
  `sudo tar -xzf <archive> -C /tmp/restore-<generation> <path>`.

Không bao giờ bị dọn: các database `pf_keep_*`, database đang hoạt động, các checkpoint đã niêm phong, các purge bundle,
các deployment đã niêm phong và các tên của operation đang mở.

## 17. Tra cứu lệnh

Mọi lệnh được chạy bằng quyền root qua launcher đã cài (§1); một lệnh chạy không có terminal cần `--instance <slug>`.

| Lệnh | Mục đích |
| --- | --- |
| `pf doctor` | Kiểm tra các công cụ của máy chủ, tính bảo mật của control, mô hình Compose, `.env` và dung lượng trống. |
| `pf status [--operation ID]` | Hiển thị instance, revision đã deploy, workspace, các container và các operation đang mở; hoặc chi tiết một operation. |
| `pf instances` | Liệt kê các instance đã đăng ký. |
| `pf ps [options] [SERVICE...]`, `pf logs [options] [SERVICE...]` | Các chế độ xem Compose chỉ đọc (§14). |
| `pf permissions check \| plan \| apply [--scope S]...` | So sánh, xem trước hoặc áp dụng permission policy (§2). |
| `pf permissions apply --resume \| --abandon` | Hoàn tất (`RESUME PERMISSIONS <slug>`) hoặc hoàn tác (`ABANDON PERMISSIONS <slug>`) một lần apply bị gián đoạn. |
| `pf config admin`, `pf config app` | Tạo, migrate hoặc bổ sung `pf-config.json` hoặc `.env` (§6). |
| `pf deploy [--latest \| --commit SHA \| --release TAG]` | Deploy lần đầu một instance mới (§7). |
| `pf abort-deploy` | Xóa một lần deploy đầu tiên chưa hoàn tất (`ABORT DEPLOY <project>`). |
| `pf update [--latest \| --commit SHA \| --release TAG] [--allow-migrations]` | Update lên một revision chính xác (§9). |
| `pf backup [--emergency]` | Chụp và kiểm thử khôi phục một checkpoint, hoặc bảo toàn dữ liệu khi một healthy checkpoint bị từ chối (§10). |
| `pf backups [--page N]` | Liệt kê các checkpoint. |
| `pf rollback [ID] [--restore-db]` | Rollback mã nguồn, hoặc mã nguồn và database (§10). |
| `pf reset-db` | Kích hoạt một database sạch đã migrate và giữ lại database trước đó (§11). |
| `pf purge [--keep-backups \| --delete-backups] [--reset-admin-config]` | Lưu một recovery bundle đã kiểm chứng, rồi xóa instance (§12). |
| `pf recoveries [--page N]` | Liệt kê các recovery bundle của purge. |
| `pf restore-instance ID [--side-by-side]` | Khôi phục một instance đã purge, hoặc khôi phục một bundle vào một recovery target được giữ lại bên cạnh nó (§12). |
| `pf release-check [--channel C]` | Cho biết có một bản phát hành đủ điều kiện hay không (§13). |
| `pf resume [--operation ID] [--abandon \| --keep-workspace \| --acknowledge]` | Tiếp tục, hủy hoặc hoàn tất một operation, hoặc acknowledge các runner record (`RESUME`, `ABANDON`, `KEEP WORKSPACE` hoặc `ACKNOWLEDGE <op8>`). |
| `pf cleanup [--apply] [--recovery-target P] [--checkpoint-history NAME] [--generation WSG]` | Báo cáo hoặc xóa các phần thừa đã được ghi lại (§16). |
| `... deploy \| update \| rollback \| restore-instance --keep-workspace` | Chạy mà không chuyển workspace (§8). |
| `sh ./deploy/synology/install-control.sh init --root <root>` | Tạo một installation root mới (§5). |
| `pf install status` | Liệt kê các install operation và bước tiếp theo của chúng. |
| `pf install register ...`, `pf install migrate-legacy ...` | Đăng ký một instance (`REGISTER <slug>`), hoặc đăng ký một home v2.5 (`MIGRATE <slug>`) (§5). |
| `pf install control --source DIR \| --release ID` | Cài hoặc chọn một control release (§15). |
| `pf install resume [--operation ID] [--abandon]` | Tiếp tục hoặc hoàn tác một install operation đang mở. |

## 18. Giới hạn kiểm chứng

- Bộ test offline (`python -B -m unittest discover -s deploy/synology/tests -p 'test*.py'`) giả lập Docker và PostgreSQL
  và bao phủ logic của controller, việc xử lý tệp và bản lưu trữ, permission policy, các journal và các luồng purge và
  khôi phục.
- Ngày 2026-10-08, vòng đời đã chạy một lần với Docker Engine 28.5.1, Docker Compose v2.40.3 và PostgreSQL 16 thật bên
  trong một container Docker-in-Docker cô lập, có đặc quyền (privileged), trên một hệ thống tệp Linux thông thường
  (harness nằm trong `deploy/synology/integration/`, kết quả được tóm tắt trong `TEST_REPORT.md`). Các vòng lặp vòng đời
  bắt buộc, việc cô lập của khôi phục song song và một ma trận sự cố (crash matrix) đã ngắt mọi loại operation trước và
  sau các tác động không thể đảo ngược của chúng đều phục hồi với dữ liệu nguyên vẹn, trừ các lỗi mà lần chạy này tìm ra;
  mỗi lỗi đều dừng an toàn (fail closed) mà không mất dữ liệu. Chúng đã được sửa sau đó với các test hồi quy offline, và
  mã đã sửa chưa được chạy lại ở mức thật.
- Chưa bao giờ được kiểm thử: một NAS thật, DSM, btrfs, các client SMB và ACL của DSM, một Docker daemon rootless, nguồn
  phát hành GitHub thật qua HTTPS, mất điện và khởi động lại máy chủ.
- Một lời từ chối đến sau bước dò hợp đồng (contract probe) của `update` (ví dụ "Migration files differ" sau khi build ứng
  viên) vẫn để lại các image tag của bước dò; `purge` xóa chúng.
- Các giới hạn đã khai báo: một người quản trị Docker hoặc root thao tác đồng thời nằm ngoài mọi bảo đảm;
  `effective_access_verified` và `future_file_behavior_verified` không bao giờ được kiểm chứng; checksum chứng minh tính
  toàn vẹn, không chứng minh tác giả; không có gì chạy không người giám sát (§13); các resource Docker của một bản cài v2.5
  không bao giờ được tiếp quản.

Trước khi dựa vào khả năng khôi phục, hãy chạy tương tác một chu trình staging dùng một lần trên chính máy chủ thật:

```text
install-control.sh init
→ pf config admin --configuration <config> --project <project>
→ pf install register
→ pf config app
→ doctor → deploy --latest → backup → update → purge → restore-instance → check the user interface and the data
→ purge → deploy --latest → restore-instance <bundle> --side-by-side
```

`pf` không phải là công cụ production. Production theo [`DEPLOYMENT.vi.md`](../DEPLOYMENT.vi.md).

## Lịch sử thay đổi

| Ngày | Commit | Thay đổi |
| --- | --- | --- |
| 2026-11-11 | uncommitted | Viết lại thành một hướng dẫn ngắn về những gì `pf` làm hiện nay, giữ các số mục mà thông báo của nó trích dẫn; bỏ các ghi chú checkpoint xếp chồng, bố cục v2.5 và thiết kế reusable controller theo kế hoạch, và thêm bảng này. |
| 2026-11-09 | `b405c6e` | Sửa các dữ kiện của thiết kế reusable controller theo kế hoạch. |
| 2026-10-14 | `2c360cb` | Ghi lại mục tiêu thiết kế reusable controller theo kế hoạch. |
| 2026-10-08 | `182b265`, `91e28d6` | Lần chạy tích hợp với Docker, Compose và PostgreSQL thật (PF-A3.4) và các bản sửa cho những phát hiện của nó. |
| 2026-10-08 | `3f0da91`, `1b44831`, `181a806` | Các operation tích hợp (PF-A3.3): kiểm chứng bundle cô lập, recovery target song song, `pf cleanup` và trạng thái purged trong registry. |
| 2026-10-07 | `38a40cf`, `d2d99bf` | Operation journal, `resume` và `--abandon`, và việc chuyển workspace (PF-A3.2). |
| 2026-10-07 | `4d7fe76`, `af77172` | Deployment record, manifest bundle chặt chẽ và emergency preservation (PF-A3.1). |
| 2026-10-07 | `2155c95`, `26b6ed4` | Permission policy theo ngữ nghĩa với `check`, `plan` và `apply` (PF-A2.3). |
| 2026-10-07 | `5d102d1`, `1244457` | Các wizard cấu hình và thiết lập quản trị có phiên bản (PF-A2.2). |
| 2026-10-07 | `964aefb`, `3b81f3a` | Trình cài đặt có bước kiểm tra trước và các control release có journal (PF-A2.1). |
| 2026-10-06 | `4466ea8`, `53246a1`, `7d332e7`, `becd72a` | Gắn daemon, kiểm tra Compose, kiểm kê resource chính xác và bỏ cơ chế chuyển tiếp Compose (PF-A1.3, PF-A1.4). |
| 2026-09-15 | `cef57ea`, `59d8cc0`, `13807ca` | Runner tiến trình có kiểm soát, cấu hình ứng dụng đóng băng và kho source được bảo vệ (PF-A1.2). |
| 2026-09-14 | `6917c3f`, `5d59d2a`, `bc46deb` | Đăng ký instance được bảo vệ, registry, bootstrap verifier và các khóa (PF-A1.1). |
| 2026-09-13 | `1ce5e19` | Tách workspace ghi được khỏi control có đặc quyền (v2.5). |
| 2026-09-09 | `4f7ecd7`, `69c8664`, `e91adc3` | Tạo hướng dẫn với update, rollback, reset database và backup đọc được. |
