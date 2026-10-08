# Triển khai PartFlow trên Linux VPS

> **Bản gốc chuẩn:** [`VPS.md`](VPS.md), được tạo
> trong gói tài liệu này trên baseline upstream
> `194ffc2e5e8e22c389abecd0830292a6707955d9`.
>
> **Trạng thái:** Đây là hướng production đích cho Phase 16. Development Compose
> hiện tại không phải gói production.
>
> **Quyền chuẩn:** Tiếng Anh là source of truth.

## 1. Khi nào nên chọn VPS

Chọn VPS khi PartFlow cần Docker/Compose ổn định, kiểm soát resource độc lập,
remote access an toàn, provider snapshot cộng với application-level backup hoặc
hướng nâng cấp sạch từ Synology. VPS giữ nguyên kiến trúc
React/FastAPI/PostgreSQL hiện có.

## 2. Artifact Phase 16 bắt buộc

Không triển khai production từ `compose.yaml`. Release phải cung cấp (trạng thái
ở P16-S2, mới triển khai một phần):

- Dockerfile/image frontend và backend production — đã triển khai (stage
  `production`; cấu hình `web` là `frontend/nginx/`);
- production Compose configuration — còn lại (phần còn lại của P16-S2);
- reverse proxy configuration và quy trình certificate — đã ghi tài liệu (§4 và
  [`../DEPLOYMENT.md`](../DEPLOYMENT.md) §3.1); xác minh trên host ở P16-S7;
- migration command/job rõ ràng — còn lại (job `migrate` của Compose là phần còn
  lại của P16-S2; ngữ nghĩa command là P16-S3);
- danh mục secret/configuration — còn lại (phần còn lại của P16-S2); contract
  file password database nằm ở DEPLOYMENT §3.1;
- automation backup và restore — P16-S5;
- command health và reconciliation — `reconcile` đã có (P16-S1); production
  invocation của nó còn lại (phần còn lại của P16-S2, P16-S3);
- logging/monitoring configuration — P16-S6 (access log của `web` là request log
  trong P16-S2);
- quy trình release và rollback gắn với version bất biến — P16-S3.

## 3. Baseline của host

- Linux distribution 64-bit được hỗ trợ và có security update hiện hành;
- deployment account riêng không phải root, dùng SSH key;
- Docker Engine và Compose plugin từ nguồn được hỗ trợ;
- host firewall: SSH chỉ từ nguồn quản trị, HTTP/HTTPS theo phê duyệt, từ chối
  mọi inbound port khác;
- automatic security update hoặc patch window được ghi rõ;
- NTP/timezone policy chính xác;
- persistent storage riêng cho PostgreSQL và local backup staging;
- provider/off-site backup destination được mã hóa;
- monitor resource và disk;
- không chạy workload thử nghiệm không liên quan trên production host.

Không publish PostgreSQL ra Internet. Ưu tiên private provider network cho remote
backup/database service nếu có.

## 4. DNS và TLS

Dùng hostname riêng như `partflow.company.example`. Chỉ point DNS sau khi private
smoke test pass. Reverse proxy terminate TLS, route `/` đến static frontend và
`/api` đến FastAPI. Kiểm tra mở trực tiếp SPA route và `/api/health` qua HTTPS.

Nếu PartFlow chỉ dùng nội bộ, hạn chế truy cập bằng firewall/VPN/private DNS. Dù
có public reachability vẫn cần authentication và authorization đầy đủ; URL bí
mật không phải biện pháp kiểm soát.

Với thiết kế production, host proxy terminate TLS và forward toàn bộ origin đến
tầng `web` trong stack trên địa chỉ loopback, nơi phục vụ build và route `/api`
([`../DEPLOYMENT.md`](../DEPLOYMENT.md) §3.1 liệt kê hành vi proxy bắt buộc).
Caddy trên host:

```caddyfile
partflow.company.example {
    request_body {
        max_size 5MiB
    }
    reverse_proxy 127.0.0.1:18080 {
        transport http {
            response_header_timeout 300s
        }
    }
}
```

`18080` đại diện cho loopback port mà production Compose project publish. Caddy
đặt `X-Forwarded-For` thành client address và đặt `X-Forwarded-Proto`, và tự
redirect HTTP sang HTTPS. Destination là đúng literal `127.0.0.1`, không bao giờ
`localhost`. Body 5 MiB và timeout 300 s cao hơn 4 MiB và 180 s của `web` để JSON
của `web` hoặc của application thắng. Nếu kết nối loopback không đến từ gateway
của `edge` network, đặt `PARTFLOW_TRUSTED_PROXY` thành địa chỉ quan sát được
(DEPLOYMENT §3.1); đây là host check của P16-S7.

**Certificate (Caddy).** Quy tắc chung nằm ở DEPLOYMENT §3.1. Tên public nhận
certificate ACME tự động và gia hạn mà không cần directive thêm (port 80 và 443
phải truy cập được cho challenge). Tên chỉ dùng nội bộ dùng `tls
/etc/caddy/certs/partflow.crt /etc/caddy/certs/partflow.key` (certificate của CA
công ty; file mode 0600 thuộc user Caddy; thay và `caddy reload` trước khi hết
hạn) hoặc `tls internal` (CA riêng của Caddy, khi đó root của nó phải được phân
phối đến workstation như mọi CA nội bộ). Kiểm tra hạn dùng bằng lệnh `openssl
s_client` ở DEPLOYMENT §3.1. Thực hiện và xác minh ở P16-S7.

## 5. Cấu trúc filesystem

Ví dụ:

```text
/srv/partflow/
  releases/<immutable-release>/
  current -> releases/<immutable-release>/
  env/production.env
  data/
  backups/database/
  manifests/
```

Deployment account sở hữu release file. Secret chỉ cho account/service cần thiết
đọc. Dữ liệu PostgreSQL không bao giờ nằm trong Git checkout.

## 6. Triển khai production lần đầu

1. Hoàn tất mọi gate ở [`DEPLOYMENT.md`](../DEPLOYMENT.md) §5.
2. Provision và harden host.
3. Cài đúng release file/image; ghi digest/commit.
4. Tạo production secret và database role theo least privilege. File secret
   `postgres_password` (một dòng, [`../DEPLOYMENT.md`](../DEPLOYMENT.md) §3.1) đến
   cùng production Compose file (phần còn lại của P16-S2); role least-privilege là
   P16-S4, và cho đến lúc đó backend sẽ dùng bootstrap owner role.
5. Start PostgreSQL ở private.
6. Tạo database trống trong volume mới (dữ liệu production không bao giờ bắt đầu
   từ dữ liệu staging hay development; restore vào production cần owner quyết
   định, P16-S5).
7. Chạy `alembic upgrade head` đúng một lần từ release backend image.
8. Start backend, frontend và reverse proxy. Với database chưa có Administrator,
   start backend với một worker (`WEB_CONCURRENCY=1`, một setup token), hoàn tất
   first-run setup (setup token nằm trong backend log) trước khi mở truy cập, rồi
   khởi động lại với số worker đã cấu hình.
   Sau đó enroll từng thiết bị Scan Station (Administration → Scan Stations → `Devices…`).
9. Chạy smoke test và reconciliation qua HTTPS theo runbook.
10. Bật lịch monitoring/backup rồi chạy backup ngay.
11. Thực hiện và đo isolated restore trước khi nhận pilot data.
12. Chỉ mở nguồn network đã duyệt và bắt đầu pilot có kiểm soát.

## 7. Release và rollback

Dùng directory/image bất biến. Build/pull release mới trước khi dừng release cũ.
Backup trước migration. Không deploy trực tiếp từ checkout `main` có thể thay
đổi.

Chỉ rollback application khi code cũ tương thích với schema đã migrate. Nếu
không, phải restore cả database pre-migration và application release tương ứng.
Alembic downgrade không phải rollback tổng quát: migration PartFlow có thể bảo
vệ immutable history bằng cách từ chối downgrade phá dữ liệu.

Theo [`OPERATIONS_RUNBOOK.md`](./OPERATIONS_RUNBOOK.md).

## 8. Chiến lược backup

Dùng PostgreSQL logical dump làm baseline portable; có thể thêm provider volume
snapshot làm lớp thứ hai. Snapshot không thay thế logical restore đã test.

- custom-format `pg_dump` theo lịch;
- checksum và manifest chứa release commit cùng Alembic revision;
- mã hóa khi truyền và khi lưu;
- bản copy off-VPS có retention và failure alert;
- định kỳ restore vào database cô lập;
- RPO/RTO được ghi và đo bằng lần chạy thật.

## 9. Chuyển từ Synology

Dùng quy trình dump/restore cutover trong `SYNOLOGY_NAS.md` §10. Giữ source và
target cùng release, enforce write freeze, verify checksum/reconciliation rồi
mới đổi DNS. Không chạy hai production instance writable trên database đã tách
nhánh.
