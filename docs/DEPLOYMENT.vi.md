# Hướng dẫn triển khai PartFlow

> **Bản gốc chuẩn:** [`DEPLOYMENT.md`](DEPLOYMENT.md), được tạo trong gói
> tài liệu này trên baseline upstream
> `194ffc2e5e8e22c389abecd0830292a6707955d9`.
>
> **Quyền chuẩn:** Tiếng Anh là source of truth. File này mô tả những gì có thể
> triển khai hiện tại, những gì Phase 16 phải bổ sung và contract vận hành
> portable cho Synology NAS, VPS và shared hosting có điều kiện.

## 1. Trạng thái roadmap

PartFlow có phase triển khai: **Phase 16 — Deployment, Production Hardening,
and Admin Maintenance** trong `IMPLEMENTATION_ROADMAP.md`. Phase 16 bao gồm
backup, migration, HTTPS/truy cập nội bộ, observability, rollback,
reconciliation, pilot deployment và bảo trì archive/purge dành cho Admin.

Tại source commit `194ffc2e5e8e22c389abecd0830292a6707955d9`, repo đã triển khai
end to end từ Phase 1 đến Phase 10 (Stockroom và WorkOrderAllocation, cả backend
lẫn frontend); các read model monitoring (Phase 11), Priority Management
(Phase 12) và Administration đầy đủ (Phase 13) vẫn là preview chỉ có ở
development hoặc trạng thái unavailable được ghi rõ. Authentication và role
enforcement thuộc Phase 14. Production hardening và artifact triển khai
production thuộc Phase 16.

Vì vậy:

| Mục đích | Repo hiện tại | Quyết định |
| --- | --- | --- |
| Máy developer | Được hỗ trợ | Dùng `../compose.yaml` theo root README. |
| Synology staging/test nội bộ | Được hỗ trợ có giới hạn | Chỉ trong LAN, dùng dữ liệu giả/không phải production, người dùng được kiểm soát và backup rõ ràng. Xem [`deployment/SYNOLOGY_NAS.md`](./deployment/SYNOLOGY_NAS.md). |
| Pilot hoặc production | Chưa sẵn sàng | Chờ authorization Phase 14 cùng artifact và gate Phase 16 ở §5. |
| Mở ra Internet | Hiện tại bị cấm | Ứng dụng chưa có ranh giới authentication production và Compose hiện tại đang expose các service development. |

Triển khai staging nội bộ không có nghĩa Phase 16 đã hoàn thành.

## 2. Vì sao Compose hiện tại chỉ dành cho development

Repo tự ghi rõ `../compose.yaml` và hai Dockerfile là artifact development. Các
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

1. Chọn và ghi lại release commit/tag bất biến.
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
