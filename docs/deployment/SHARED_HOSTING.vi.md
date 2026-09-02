# PartFlow trên shared hosting

> **Bản gốc chuẩn:** [`SHARED_HOSTING.md`](SHARED_HOSTING.md),
> được tạo trong gói tài liệu này trên baseline upstream
> `194ffc2e5e8e22c389abecd0830292a6707955d9`.
>
> **Quyết định:** Shared hosting không phải target PartFlow ưu tiên và không phải
> thay thế drop-in cho deployment Docker. Chỉ dùng sau khi provider xác nhận bằng
> văn bản mọi yêu cầu tương thích trên đúng plan.
>
> **Quyền chuẩn:** Tiếng Anh là source of truth.

## 1. Vì sao chỉ tương thích có điều kiện

PartFlow không phải static site. Hệ thống cần:

- process ASGI tương thích Python 3.12 cho semantics FastAPI/Uvicorn;
- PostgreSQL với migration, constraint, trigger, JSONB, array, index, transaction
  và row locking cùng privilege đủ dùng;
- React/Vite frontend đã build và có SPA fallback;
- same-origin routing từ `/api` vào backend;
- environment variable bền vững và process restart có kiểm soát;
- scheduled backup và maintenance command;
- log, health check và đủ process/database resource;
- đường vận hành cho release và recovery.

Phần lớn shared hosting không cấp Docker daemon hoặc quyền kiểm soát reverse
proxy cấp root. Điều chỉnh PartFlow theo runtime riêng của provider có thể tạo
kiến trúc triển khai thứ hai, khó test và migrate hơn.

## 2. Kết quả kiểm tra Hawk Host tại 2026-09-01

Tài liệu hiện tại của Hawk Host nói Python app được triển khai qua cPanel
**Setup Python App** dùng `mod_passenger`, có environment variable,
start/stop/restart, quản lý configuration và cài module hàng loạt. Hawk Host
cũng có tài liệu PostgreSQL và yêu cầu support whitelist khi client PostgreSQL
kết nối từ xa.

Những điều đó **không** chứng minh đúng shared-hosting plan hỗ trợ native
ASGI/FastAPI chạy lâu dài, Uvicorn process control, PostgreSQL privilege/extension
cần thiết, custom proxy `/api`, background schedule hoặc recovery procedure của
PartFlow. Chỉ có tài liệu `mod_passenger` không đủ chứng minh ASGI compatibility.

Nguồn chính thức:

- [Hawk Host: How to create a Python application](https://www.hawkhost.com/kb/programming/python/how-to-create-python-application/)
- [Hawk Host: Remote PostgreSQL connections](https://www.hawkhost.com/kb/web-hosting/how-do-i-allow-remote-postgresql-connections/)

## 3. Câu hỏi Hawk Host phải trả lời

Gửi support đầy đủ requirement và lấy câu trả lời bằng văn bản:

1. Plan có hỗ trợ FastAPI như native ASGI application không, entry point được
   document là gì? Nếu cần adapter, adapter đó có được chính thức hỗ trợ không?
2. App có thể chạy Uvicorn hoặc ASGI server khác liên tục không; restart,
   timeout, worker count và log được kiểm soát thế nào?
3. Có Python version nào và Python 3.12 có được hỗ trợ suốt vòng đời deployment?
4. Có PostgreSQL 16 local không? Nếu không, version nào và có giới hạn tương
   thích gì?
5. Database role có tạo được schema object mà PartFlow migration cần, gồm
   trigger, constraint, expression/partial index, JSONB, array và row lock?
6. Có thể chạy Alembic qua SSH trong controlled release?
7. Domain có thể route `/api/*` đến Python trong khi phục vụ Vite `dist/` với SPA
   fallback cho mọi route khác?
8. Environment variable/secret có bền vững và không bị web expose hoặc lẫn trong
   backup người khác truy cập?
9. Có hỗ trợ cron job, custom PostgreSQL dump, off-site copy và restore vào
   database mới?
10. Giới hạn CPU, RAM, process, I/O, connection, execution time và inode là gì;
    sự kiện chạm limit được báo thế nào?
11. Có giữ/export application và database log để chẩn đoán vận hành?
12. HTTPS có tự động không và có giới hạn access theo company IP/VPN range?

Bất kỳ câu “không”, không rõ hoặc workaround không được hỗ trợ đều làm plan này
không phù hợp.

## 4. Nếu mọi yêu cầu đều được xác nhận

Shared-hosting deployment sẽ là target Phase 16 riêng:

- build frontend trong CI và chỉ publish `dist/`;
- package backend bằng ASGI entry point do provider document;
- duy trì quy trình cài dependency và restart riêng cho provider;
- cấu hình same-origin `/api` routing và SPA fallback;
- trỏ `DATABASE_URL` vào PostgreSQL của provider;
- chạy Alembic rõ ràng trong maintenance;
- triển khai backup, manifest, restore test, monitoring và rollback tương thích
  provider;
- chạy cùng gate ứng dụng, migration, quantity integrity và authorization như
  production Docker.

Không thay PostgreSQL bằng MySQL và không chuyển business rule backend ra
frontend chỉ để vừa shared hosting.

## 5. Quyết định đề nghị

Dùng Synology cho staging nội bộ có giới hạn. Khi cần external host, chọn Linux
VPS nhỏ chạy Docker Compose. Nó giữ một kiến trúc triển khai, cho phép kiểm soát
đúng ASGI/PostgreSQL và biến việc chuyển Synology sang VPS thành database
migration thay vì port lại ứng dụng.
