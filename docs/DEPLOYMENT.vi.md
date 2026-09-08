# Hướng dẫn triển khai PartFlow

> **Bản gốc chuẩn:** [`DEPLOYMENT.md`](DEPLOYMENT.md).
> Bản tiếng Việt được dịch từ file tiếng Anh tương ứng.
>
> **Quyền chuẩn:** Tiếng Anh là source of truth. File này mô tả những gì có thể
> triển khai hiện tại, những gì Phase 16 phải bổ sung và contract vận hành
> portable cho Synology NAS, VPS và shared hosting có điều kiện.

## 1. Trạng thái roadmap

PartFlow có phase triển khai: **Phase 16 — Deployment, Production Hardening,
and Admin Maintenance** trong `IMPLEMENTATION_ROADMAP.md`. Phase 16 bao gồm
backup, migration, HTTPS/truy cập nội bộ, observability, rollback,
reconciliation, pilot deployment và bảo trì archive/purge dành cho Admin.

Tại source commit `d277f8e53a7ca79e0211c211a344dce60e8c7d7f`, repo đã triển khai
end to end từ Phase 1 đến Phase 10, cùng Phase 10.5 — Scan Station Receive
Quantity và các read model cùng frontend view thật của Production Board,
Area Board và PN Tracking thuộc Phase 11. Priority Management (Phase 12) và
Administration đầy đủ (Phase 13) vẫn là preview chỉ có ở development hoặc
trạng thái unavailable được ghi rõ. Authentication và role enforcement thuộc
Phase 14. Production hardening và artifact triển khai production thuộc Phase 16.

Vì vậy:

| Mục đích | Repo hiện tại | Quyết định |
| --- | --- | --- |
| Máy developer | Được hỗ trợ | Dùng `compose.yaml` theo root README. |
| Synology staging/test nội bộ | Được hỗ trợ có giới hạn | Chỉ trong LAN, dùng dữ liệu giả/không phải production, người dùng được kiểm soát và backup rõ ràng. Xem [`deployment/SYNOLOGY_NAS.md`](./deployment/SYNOLOGY_NAS.md). |
| Pilot hoặc production | Chưa sẵn sàng | Chờ authorization Phase 14 cùng artifact và gate Phase 16 ở §5. |
| Mở ra Internet | Hiện tại bị cấm | Ứng dụng chưa có ranh giới authentication production và Compose hiện tại đang expose các service development. |

Triển khai staging nội bộ không có nghĩa Phase 16 đã hoàn thành.

## 2. Vì sao Compose hiện tại chỉ dành cho development

Repo tự ghi rõ `compose.yaml` và hai Dockerfile là artifact development. Các
giới hạn đã quan sát được gồm:

- backend chạy Uvicorn với `--reload`;
- frontend chạy Vite development server thay vì phục vụ production build bất
  biến;
- source directory và dependency directory được bind mount;
- các port PostgreSQL, backend và frontend đều được publish ra host;
- có credential mặc định dành cho development;
- database và ứng dụng dùng chung PostgreSQL role do Compose tạo;
- chưa có production reverse proxy, TLS policy, secret store, log rotation,
  release image tag, scheduled backup job, restore drill hoặc command rollback;
- authentication/role enforcement Phase 14 chưa được triển khai;
- một số view đã duyệt vẫn là preview chỉ có ở development hoặc còn chờ tích
  hợp backend/frontend thật.

Không được che các giới hạn này bằng reverse proxy của NAS hoặc public DNS name.

## 3. Topology portable đích

Gói production Phase 16 nên giữ cùng một topology trên Synology và VPS sau này:

```text
Browser / barcode workstation
            |
          HTTPS
            |
Reverse proxy (điểm vào LAN/public duy nhất)
       |                    |
       | /                  | /api
       v                    v
Static frontend         FastAPI backend
                             |
                     private container network
                             |
                         PostgreSQL
```

Các ranh giới bắt buộc:

- chỉ expose HTTPS cho client; HTTP chỉ được dùng để redirect nếu cần;
- giữ PostgreSQL private, tuyệt đối không publish port 5432 ra mạng không đáng
  tin cậy;
- giữ backend private khi reverse proxy có thể route `/api` nội bộ;
- phục vụ frontend và API chung origin để browser tiếp tục dùng URL `/api`
  tương đối như hiện tại;
- persist dữ liệu PostgreSQL và output backup bên ngoài container tạm thời;
- chạy schema migration như một release step rõ ràng, không phải side effect
  không kiểm soát mỗi khi replica khởi động;
- định danh mỗi lần triển khai bằng Git commit hoặc image tag bất biến;
- dùng cùng một format backup portable giữa NAS và VPS.

## 4. Chọn nền tảng

| Nền tảng | Mức phù hợp | Vai trò đề nghị |
| --- | --- | --- |
| Synology NAS có Container Manager | Phù hợp cho deployment nội bộ nhỏ nếu model hỗ trợ đủ container và NAS có storage, RAM, monitoring, UPS cùng backup đã test | Staging nội bộ hiện tại; pilot/production chỉ sau khi qua gate Phase 16 |
| Linux VPS | Target portable lâu dài tốt nhất | Hướng nâng cấp production ưu tiên, cho phép kiểm soát Docker, remote access và phục hồi off-site độc lập |
| Shared hosting như Hawk Host | Có điều kiện, không phải drop-in | Chỉ dùng nếu provider chứng minh hỗ trợ ASGI/FastAPI native, PostgreSQL, routing, migration, job và recovery cần thiết; nếu không hãy dùng VPS |

Chuyển từ Synology sang VPS phải là redeploy release cộng với PostgreSQL
dump/restore đã xác minh, không phải viết lại ứng dụng.

## 5. Production release gate

PartFlow chỉ được vào pilot/production khi toàn bộ gate sau đã đạt.

### Ứng dụng và authorization

- Authentication và server-side role enforcement Phase 14 hoàn tất và đã test;
  ẩn navigation không bao giờ là authorization.
- Mọi production view nằm trong pilot scope đều dùng API thật; không nhầm mock
  hoặc placeholder chưa kết nối với tính năng vận hành.
- Production write vẫn bị block khi mất kết nối và không bao giờ được queue cục
  bộ.
- Toàn bộ quality gate của repo và migration test pass trên đúng release commit.

### Production artifact

- backend image production không chạy reload server và có process model được
  ghi rõ;
- frontend production là Vite build bất biến do production web server phục vụ;
- production Compose có restart policy, health check, private network,
  persistent volume, resource limit thận trọng và không có development bind
  mount;
- reverse proxy chịu trách nhiệm TLS, SPA fallback, request limit và route
  `/api`;
- configuration bắt buộc được validate lúc startup và secret không có default
  đã commit;
- image hoặc release version bất biến và được giữ đủ lâu để rollback code.

### An toàn dữ liệu và vận hành

- PostgreSQL logical backup chạy tự động theo lịch, được mã hóa và sao chép
  off-host/off-NAS, có retention và monitoring;
- restore vào database cô lập đã được test và đo thời gian;
- mỗi migration có backup, forward plan, đánh giá compatibility, smoke test và
  recovery plan;
- rollback dùng release ứng dụng tương thích trước đó, hoặc restore database
  pre-migration khớp phiên bản nếu rollback schema không an toàn;
- health, log, disk, tuổi backup, tăng trưởng database và số lần container
  restart đều được monitor;
- reconciliation check cho Movement/quantity chạy và alert nhưng không mutate
  dữ liệu;
- incident owner, maintenance window, RPO và RTO được phê duyệt rõ;
- điều kiện bắt đầu pilot, kết thúc pilot và escalation được ghi lại.

### Network và host

- client dùng HTTPS hoặc ngoại lệ isolated LAN đã được chấp thuận chính thức;
- firewall chỉ cho phép source và port cần thiết;
- DSM/VPS, Container Manager/Docker và base image được cập nhật bảo mật có kiểm
  soát;
- đồng bộ thời gian NAS/VPS chính xác;
- host có UPS hoặc chiến lược mất điện được ghi rõ;
- cảnh báo capacity chừa đủ disk cho PostgreSQL, image update, migration tạm và
  backup.

## 6. Tách biệt environment

Dùng database, secret, URL và vị trí backup riêng cho:

- `development` — chỉ dữ liệu developer;
- `staging` — dữ liệu giả hoặc đã sanitize, dùng diễn tập release;
- `production` — dữ liệu nhà máy đã được phép.

Không restore dữ liệu production vào development nếu chưa được phép và chưa
sanitize. Không bao giờ cho staging và production dùng chung database.

Mọi môi trường đặt `SITE_TIMEZONE` (tên múi giờ IANA; `UTC` khi không đặt) theo
lịch của nhà máy: backend validate giá trị này khi khởi động và derive done date
của Work Order đã completed — do đó cả Done range lẫn kết quả on time / late
của completed history — từ múi giờ này, không bao giờ từ giờ local của browser.
Staging và production phải dùng cùng một giá trị.

## 7. Luồng triển khai chung

Mọi nền tảng dùng cùng thứ tự release:

1. Chọn và ghi lại release commit/tag bất biến theo §10.
2. Xác nhận CI và quality gate trên đúng revision đó.
3. Đọc migration note từ revision đang chạy đến target.
4. Kiểm tra backup mới nhất và tạo backup pre-release mới.
5. Build hoặc pull target image mà chưa thay thế release đang chạy.
6. Vào maintenance mode/window đã duyệt nếu cần.
7. Chạy Alembic migration đúng một lần và lưu output.
8. Khởi động target application release.
9. Chạy health, API, UI, authorization, scan-focus và write/read-back smoke test
   bằng dữ liệu test được chỉ định.
10. Chạy quantity/Movement reconciliation.
11. Ghi revision đã deploy, migration head, operator, thời gian và kết quả.
12. Giữ release trước và backup pre-release đến hết observation window.

Command và điểm quyết định chi tiết nằm trong
[`deployment/OPERATIONS_RUNBOOK.md`](./deployment/OPERATIONS_RUNBOOK.md).

## 8. Hướng dẫn theo nền tảng

- [`deployment/SYNOLOGY_NAS.md`](./deployment/SYNOLOGY_NAS.md)
- [`deployment/VPS.md`](./deployment/VPS.md)
- [`deployment/SHARED_HOSTING.md`](./deployment/SHARED_HOSTING.md)
- [`deployment/OPERATIONS_RUNBOOK.md`](./deployment/OPERATIONS_RUNBOOK.md)

## 9. Tham chiếu nền tảng bên ngoài

Các nguồn sau mô tả capability của nền tảng, không chứng minh PartFlow đã sẵn
sàng:

- [Synology Container Manager](https://www.synology.com/en-us/dsm/feature/container-manager)
  ghi nhận hỗ trợ Project nhiều container từ Compose file.
- [Synology Container Manager Project help](https://kb.synology.com/en-us/DSM/help/ContainerManager/docker_project?version=7)
  là tài liệu UI để tạo và vận hành Project.
- [Hawk Host Python application guide](https://www.hawkhost.com/kb/programming/python/how-to-create-python-application/)
  mô tả triển khai Python bằng `mod_passenger`; riêng điều đó không chứng minh
  tương thích ASGI/FastAPI native.
- [Hawk Host remote PostgreSQL guide](https://www.hawkhost.com/kb/web-hosting/how-do-i-allow-remote-postgresql-connections/)
  xác nhận có PostgreSQL trong môi trường đó và remote access cần provider
  whitelist; vẫn phải xác minh capability của đúng plan trước khi chọn shared
  hosting.

## 10. Phát hành và quản lý phiên bản (Release and Versioning)

Mục này là nguồn chuẩn cho cách đặt tên release, release notes và checklist
phát hành của PartFlow. Release là một bản ứng viên triển khai có thể truy vết,
không phải bằng chứng đã sẵn sàng cho production. Các gate ở §5 vẫn áp dụng
cho pilot/production.

### 10.1 Định danh release

Dùng một application release chung cho frontend, backend và migration từ cùng
một commit. Ghi lại Git tag và commit SHA đầy đủ. Alembic revision định danh
riêng database schema; package metadata không phải lịch sử release.

Tạo release cho phiên bản được chọn để test hoặc deploy, không phải cho mọi
commit. Deploy lại cùng phiên bản không cần tạo release mới. Không di chuyển,
ghi đè hoặc tái sử dụng tag của release đã phát hành, cũng không thay thế build
artifact đã phát hành của release đó. Nội dung release thay đổi phải dùng
version mới.

Dùng tag theo version, không dùng `Stage`, `Production`, `Latest` hoặc số phase.
Tag không theo version đã có có thể giữ làm mốc lịch sử, nhưng không được dùng
làm target triển khai liên tục thay đổi. Environment và phase nằm trong notes.

### 10.2 Quy ước version

Dùng [Semantic Versioning](https://semver.org/spec/v2.0.0.html), với tiền tố `v`
viết thường cho Git tag: `vMAJOR.MINOR.PATCH`, có thể thêm hậu tố `-alpha.N`,
`-beta.N` hoặc `-rc.N`. Bắt đầu `N` từ 1; không thêm số 0 ở đầu.

| Tình huống | Ví dụ |
| --- | --- |
| Snapshot development nội bộ đầu tiên được chọn cho staging | `v0.1.0-alpha.1` |
| Snapshot tiếp theo của cùng release đang dự kiến | `v0.1.0-alpha.2` |
| Mốc development mới có phạm vi chức năng lớn hơn đáng kể | `v0.2.0-alpha.1` |
| Đã triển khai phạm vi dự kiến; còn cần kiểm thử rộng hơn | `v0.2.0-beta.1` |
| Bản ứng viên cho lần phát hành production ổn định đầu tiên | `v1.0.0-rc.1` |
| Bản production ổn định đầu tiên được chấp nhận, sau khi đạt §5 | `v1.0.0` |
| Sửa lỗi tương thích sau `v1.0.0` | `v1.0.1` |
| Bổ sung tính năng tương thích sau `v1.0.0` | `v1.1.0` |
| Thay đổi phá vỡ contract được hỗ trợ sau `v1.x` | `v2.0.0` |

Đây là ví dụ, không phải tag đã dành trước hoặc chuỗi bắt buộc. Chọn version
chưa được dùng tiếp theo dựa trên lịch sử release thực tế và phạm vi thay đổi.
Trước 1.0, compatibility chưa được bảo đảm; vẫn phải ghi rõ breaking change.

Với PartFlow, contract được hỗ trợ bao gồm hành vi API đã được tài liệu hóa và
yêu cầu configuration/nâng cấp dữ liệu. Chỉ có database migration không đồng
nghĩa phải tăng major; cần đánh giá tác động đến compatibility.

`alpha`, `beta` và `rc` thể hiện mức độ hoàn thiện của release, không phải host
triển khai. Đánh dấu cả ba là GitHub pre-release; không chỉ định chúng là bản
stable mới nhất. Có thể diễn tập bản stable trên staging trước production.
Hậu tố không miễn trừ §5 và không chứng minh deployment validation đã đạt.

### 10.3 Release title và description

Dùng `PartFlow <tag>`, có thể thêm mục đích ngắn:
`PartFlow v0.1.0-alpha.1 — Internal Staging`. Giữ release title và description
bằng tiếng Anh trừ khi được yêu cầu rõ ràng dùng ngôn ngữ khác.

Với release đầu tiên, tóm tắt phạm vi đã triển khai. Với release tiếp theo,
mô tả thay đổi từ release trước được chọn đến đúng target commit, không chỉ
dựa vào commit mới nhất hoặc kế hoạch phase. Không đưa công việc chưa commit
vào release.

Dùng mẫu Description sau, thay placeholder bằng thông tin đã xác minh:

```markdown
## Summary
<Release purpose and intended use.>

## Changes
<Implemented additions, fixes, and breaking changes since the previous release;
summarize available functionality for an initial release.>

## Deployment
- Source commit: <full commit SHA>
- Previous release: <tag, or Initial release>
- Intended use: <internal staging, release validation, or production>
- GitHub pre-release: <Yes or No>
- Deployment guide: <repository guide path>

## Database and configuration
- Migrations: <required revisions and upgrade notes, or No new migrations>
- Configuration: <required changes, or No changes>
- Rollback: <application/schema compatibility and recovery requirements>

## Known limitations
<Relevant restrictions and unavailable features.>

## Validation
<Completed checks and their evidence for this exact commit;
identify pending or unverified checks explicitly.>
```

Không suy ra "No new migrations", compatibility hoặc validation thành công
từ tên release. Kiểm tra khoảng revision và bằng chứng thực tế. Kết quả chưa
biết phải ghi `Pending` hoặc `Not verified` trong draft; chúng không đáp ứng
release gate. Tách kết quả smoke test trên NAS khỏi kết quả CI. Không đưa secret,
credential hoặc chi tiết triển khai riêng tư vào release notes công khai.

### 10.4 Checklist phát hành

1. Chọn đúng target commit và release trước dùng làm mốc so sánh. Giải quyết
   các thay đổi chỉ có ở local trước khi chọn source để phát hành.
2. Kiểm tra tag local và remote để tránh trùng. Xác nhận CI của repo và các
   quality gate liên quan đã đạt trên đúng commit đó; run thành công của
   revision khác không được tính.
3. Rà soát thay đổi, migration, configuration, yêu cầu rollback và giới hạn đã
   biết. Hoàn thiện release notes; áp dụng §5 cho pilot/production.
4. Tạo annotated tag tại commit đã chọn, không tạo tại đầu branch đang thay đổi
   mà chưa được kiểm tra, rồi push riêng tag đó.
5. Trong GitHub Releases, tạo draft từ tag đã có. Nhập title và description,
   đặt pre-release flag nhất quán với §10.2 và đính kèm các build artifact
   dự định phát hành trước khi publish.
6. Publish khi đã đáp ứng các gate áp dụng. Deploy theo §7 và runbook của nền
   tảng; ghi tag, SHA, database revision, operator, thời gian và kết quả smoke
   test. Giữ release trước và các backup cần thiết.

Có thể chuẩn bị draft trước khi validation hoàn tất. Publish release và deploy
là hai thao tác riêng; yêu cầu chuẩn bị release info không tự động cho phép
thực hiện thao tác nào trong hai thao tác này.

### 10.5 Workflow hiện tại và ranh giới rollback

Tại source revision ở §1, `.github/workflows/ci.yml` chạy khi push vào `main`
và khi có pull request. Workflow kiểm tra code và build development image;
không publish release image hoặc deploy lên Synology khi tạo tag/release.
Source archive của GitHub không phải production image đã build sẵn. Source tag
cố định cũng không bảo đảm lần build lại sau đó giống hệt khi base image có
thể thay đổi.

Release thủ công là đủ ở giai đoạn này. Khi triển khai việc publish production
image, ghi image digest cùng release tag và giữ lại artifact đã deploy. Không
tạo version service riêng, hệ thống nhiều cấp release branch hoặc cơ chế
publish tự động chỉ để áp dụng quy ước này.

Rollback theo operations runbook: đổi application tag không hoàn tác migration.
Xác nhận schema compatibility hoặc dùng kế hoạch phục hồi database đã duyệt,
có tính đến dữ liệu được ghi sau backup. Không bao giờ giả định restore backup
cũ sẽ giữ được các production Movement mới hơn.

Tham chiếu: [Git tags](https://git-scm.com/docs/git-tag) và
[GitHub release management](https://docs.github.com/en/repositories/releasing-projects-on-github/managing-releases-in-a-repository).
