# PartFlow trên shared hosting

> **Bản gốc chuẩn:** [`SHARED_HOSTING.md`](SHARED_HOSTING.md). Đồng bộ với bản tiếng Anh tại commit `b405c6e`. Tiếng Anh là source of truth.

Tài liệu này ghi lại vì sao shared web hosting chỉ khả thi có điều kiện cho PartFlow, và nhà cung cấp phải xác nhận
bằng văn bản những gì trước khi có ai cân nhắc nó. Hãy đọc trước khi bỏ tiền cho một gói shared hosting. Quyết định là:
shared hosting không phải target PartFlow ưu tiên và không phải thay thế drop-in cho deployment Docker. Chỉ dùng sau khi
nhà cung cấp xác nhận bằng văn bản mọi yêu cầu bên dưới trên đúng gói đó. Các host mà PartFlow hỗ trợ là Synology NAS của
công ty ([`SYNOLOGY_NAS.md`](./SYNOLOGY_NAS.md)) và, như phương án thay thế, một máy chủ ảo riêng Linux
([`VPS.md`](./VPS.md)).

## 1. Vì sao tính tương thích là có điều kiện

PartFlow không phải site tĩnh. Nó đòi hỏi:

- một process tương thích Python 3.12 phục vụ ASGI (Asynchronous Server Gateway Interface), để FastAPI và Uvicorn hoạt
  động đúng thiết kế;
- PostgreSQL với migration, constraint, trigger, JSONB, array, index, hành vi transaction và locking, và đủ đặc quyền;
- frontend React và Vite đã build với SPA fallback (single-page application fallback), nghĩa là mọi route không biết đều
  trả về `index.html` của ứng dụng;
- định tuyến same-origin, để `/api` tới backend trên cùng tên host với frontend;
- biến môi trường bền vững và việc restart process có kiểm soát;
- backup và lệnh bảo trì theo lịch;
- log, health check, và đủ tài nguyên process và database;
- một cách để release phiên bản mới và để khôi phục.

Hầu hết shared hosting không có Docker daemon và không cho kiểm soát reverse proxy ở mức root. Việc điều chỉnh PartFlow
theo runtime riêng của một nhà cung cấp sẽ tạo ra kiến trúc triển khai thứ hai, khó kiểm thử và khó di chuyển hơn.

## 2. Phát hiện về Hawk Host tính đến 2026-09-01

Tài liệu của Hawk Host ngày 2026-09-01 nêu rằng ứng dụng Python được triển khai qua công cụ cPanel **Setup Python
App**, dùng `mod_passenger`. Công cụ này có biến môi trường, điều khiển start, stop và restart, quản lý cấu hình, và cài
nhiều module cùng lúc. Hawk Host cũng tài liệu hóa việc truy cập PostgreSQL và việc whitelist phía support cho client
PostgreSQL từ xa.

Những dữ kiện này **không** chứng minh rằng đúng gói shared hosting đó hỗ trợ một ứng dụng ASGI (FastAPI) native chạy
liên tục, việc điều khiển process Uvicorn, các extension và đặc quyền PostgreSQL mà PartFlow cần, proxy `/api` tùy chỉnh,
lịch chạy nền, hay các quy trình khôi phục của PartFlow. Chỉ riêng tài liệu `mod_passenger` không phải bằng chứng về
tương thích ASGI.

Tham chiếu chính thức:

- [Hawk Host: How to create a Python application](https://www.hawkhost.com/kb/programming/python/how-to-create-python-application/)
- [Hawk Host: Remote PostgreSQL connections](https://www.hawkhost.com/kb/web-hosting/how-do-i-allow-remote-postgresql-connections/)

## 3. Các câu hỏi nhà cung cấp phải trả lời

Gửi cho đội hỗ trợ của nhà cung cấp đúng các yêu cầu này và nhận câu trả lời bằng văn bản. Các câu hỏi lấy Hawk Host làm
ví dụ, nhưng áp dụng cho mọi nhà cung cấp shared hosting.

1. Gói này có hỗ trợ FastAPI như một ứng dụng ASGI native không, và entry point được tài liệu hóa là gì? Nếu cần
   adapter, adapter đó có được hỗ trợ chính thức không?
2. Một ứng dụng có thể chạy Uvicorn hoặc ASGI server khác liên tục không, và restart, timeout, số worker và log được
   kiểm soát thế nào?
3. Có những phiên bản Python nào, và Python 3.12 có được hỗ trợ trong suốt vòng đời của deployment không?
4. PostgreSQL 16 có sẵn tại chỗ không? Nếu không, phiên bản nào có sẵn và áp dụng những giới hạn tương thích nào?
5. Database role có thể tạo các schema object mà migration của PartFlow yêu cầu không, gồm trigger, constraint,
   expression index và partial index, JSONB, array, và row lock?
6. Alembic (công cụ migration database) có chạy được qua SSH trong một release có kiểm soát không?
7. Domain có thể định tuyến `/api/*` tới Python trong khi phục vụ thư mục Vite `dist/` với SPA fallback cho mọi route
   khác không?
8. Biến môi trường và secret có bền vững không, và chúng có bị loại khỏi truy cập web và khỏi backup dùng chung với người
   dùng khác không?
9. Cron job, PostgreSQL dump tùy chỉnh, bản sao off-site, và restore vào database mới có được hỗ trợ không?
10. Áp dụng những giới hạn nào về CPU, bộ nhớ (RAM), process, input và output, kết nối, thời gian thực thi và inode, và
    các sự kiện chạm giới hạn được báo cáo thế nào?
11. Log của ứng dụng và database có thể được giữ hoặc xuất ra để chẩn đoán không?
12. HTTPS có tự động không, và có thể hạn chế truy cập theo dải IP của công ty hoặc mạng riêng ảo (virtual private
    network, VPN) không?

Bất kỳ câu "không" chưa giải quyết, hoặc câu trả lời phụ thuộc vào một workaround không được hỗ trợ, đều khiến gói không phù
hợp.

## 4. Nếu mọi yêu cầu đều được xác nhận

Một deployment shared hosting sẽ là một target triển khai riêng với quy trình riêng:

- build frontend trong tích hợp liên tục (continuous integration, CI) và chỉ publish `dist/`;
- đóng gói backend theo ASGI entry point mà nhà cung cấp đã tài liệu hóa;
- duy trì quy trình riêng của nhà cung cấp để cài dependency và restart ứng dụng;
- cấu hình định tuyến `/api` same-origin và SPA fallback;
- trỏ `DATABASE_URL` tới database PostgreSQL của nhà cung cấp;
- chạy Alembic tường minh trong lúc bảo trì;
- triển khai các quy trình backup, manifest, restore test, giám sát và rollback hoạt động trong giới hạn của nhà cung
  cấp;
- vượt qua cùng các kiểm tra về ứng dụng, migration, toàn vẹn số lượng và phân quyền như Docker production stack.

Không thay PostgreSQL bằng MySQL, và không chuyển business rule của backend sang frontend chỉ để vừa với shared hosting.

## 5. Quyết định khuyến nghị

PartFlow chạy production trên Synology NAS của công ty. Khi cần một host bên ngoài, hãy chọn một Linux VPS nhỏ với
Docker Compose. Nó giữ một kiến trúc triển khai duy nhất, cho kiểm soát đúng đắn ASGI và PostgreSQL, và biến việc chuyển
từ Synology NAS sang VPS thành một cuộc di chuyển database thay vì port ứng dụng.

## Lịch sử thay đổi

| Ngày | Commit | Thay đổi |
| --- | --- | --- |
| 2026-11-09 | `b405c6e` | Viết lại thành nội dung hiện hành cho người đọc mới; khuyến nghị host nay nêu Synology NAS của công ty là production. |
| 2026-09-02 | `8e5ab18` | Chuyển bản dịch tiếng Việt sang cạnh bản tiếng Anh và trỏ lại các link. |
| 2026-09-02 | `d2f68de` | Phiên bản đầu của đánh giá shared hosting cùng bản dịch tiếng Việt. |
