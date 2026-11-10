# Triển khai PartFlow trên Synology NAS

> **Bản gốc:** [`SYNOLOGY_NAS.md`](SYNOLOGY_NAS.md). Đồng bộ với bản tiếng Anh
> tại commit `b405c6e` cộng các thay đổi chưa commit ngày 2026-11-09. Bản tiếng
> Anh là nguồn chuẩn (source of truth); khi hai bản khác nhau, bản tiếng Anh
> thắng.

Hướng dẫn này cài PartFlow production trên Synology NAS của công ty, rồi chỉ
cách cập nhật, sao lưu và theo dõi nó. Người đọc là người cài và vận hành
PartFlow: có quyền quản trị DiskStation Manager (DSM) và biết dùng Secure Shell
(SSH) ở mức cơ bản. Các quy tắc chung cho mọi nền tảng nằm ở
[`../DEPLOYMENT.md`](../DEPLOYMENT.md) (§3.1 production stack, §5 yêu cầu
go-live); các quy trình vận hành hằng ngày nằm ở
[`OPERATIONS_RUNBOOK.md`](./OPERATIONS_RUNBOOK.md). Các lệnh production trong
hướng dẫn này đã chạy trên Docker Desktop nhưng chưa chạy trên Synology NAS: khi
một lệnh lỗi trên NAS, hãy sửa nó và ghi lại cách sửa tại đây.

## 1. Bắt đầu từ đây

**Cài production:** kiểm tra §2 và chuẩn bị các thư mục ở §3, rồi làm theo §8
từ bước 1. §8 sẽ chỉ sang §5.2 (reverse proxy và chứng chỉ) đúng lúc cần. Sau
go-live, thiết lập sao lưu ở §6 và giám sát ở §8 "Giám sát và cảnh báo" khi
thuận tiện; chúng được khuyến nghị và không bao giờ chặn việc sử dụng (quyết
định của owner ngày 2026-11-09: production go-live trực tiếp, không pilot và
không release gate, [`../DEPLOYMENT.md`](../DEPLOYMENT.md) §5).

**Staging có giới hạn (§4, §5.1, §7) là tùy chọn.** Nó chạy development stack
với dữ liệu giả hoặc dữ liệu có thể bỏ, để một nhóm nội bộ nhỏ dùng thử
PartFlow. Nó không bao giờ là production, không bao giờ được mở ra internet, và
phải được gỡ khỏi NAS trước khi cài production (§8 bước 1). Production luôn bắt
đầu từ một database mới, rỗng; dữ liệu staging không bao giờ được mang sang.

**Biến shell `PF`.** Mọi lệnh production trong hướng dẫn này và trong runbook
dùng biến này, được đặt trong phiên SSH tại release checkout (thư mục `repo/` ở
§3):

```bash
PF="docker compose -f compose.production.yaml --env-file .env.production"
```

`PF` chỉ là cách viết tắt cho lệnh Compose của production. Nó không phải công
cụ Deployment Admin `pf` ([`SYNOLOGY_ADMIN.md`](./SYNOLOGY_ADMIN.md)), công cụ
này không được cài trên NAS này.

## 2. Danh sách kiểm tra trước khi cài

Kiểm tra và ghi lại các mục sau trước khi cài bất cứ thứ gì:

- model NAS, kiến trúc bộ xử lý, dung lượng bộ nhớ, phiên bản DSM, storage pool
  và volume, hệ thống tệp, dung lượng trống và tình trạng mảng đĩa dự phòng
  (redundant array of independent disks, RAID);
- **Container Manager** có trong Package Center cho NAS này và tạo được Project
  nhiều container từ một tệp Compose;
- kiến trúc bộ xử lý được mọi image mà PartFlow dùng hỗ trợ (các base image
  PostgreSQL, Python, Node.js và nginx);
- còn đủ bộ nhớ và thời gian xử lý sau các tác vụ sẵn có của NAS (§9 giải thích
  cách đo);
- NAS có địa chỉ mạng cục bộ (local area network, LAN) được giữ cố định và cấu
  hình hệ thống tên miền (DNS) và giao thức đồng bộ thời gian mạng (NTP) đúng;
- các thư mục PartFlow (§3) nằm trên một volume được bảo vệ;
- tường lửa DSM và mạng công ty giới hạn được PartFlow trong các subnet hoặc LAN
  ảo (VLAN) dự định;
- có bộ lưu điện (uninterruptible power supply, UPS) được nối, và chức năng tắt
  máy an toàn tự động đã được cấu hình và thử;
- có ít nhất một đích sao lưu được mã hóa nằm ngoài NAS này;
- **Git:** các script production đọc git tag đang được checkout, nên production
  cần một git checkout, không phải một tệp nén; trên DSM lệnh `git` đến từ gói
  **Git Server** trong Package Center;
- **một tài khoản vận hành:** chọn một tài khoản chạy được `docker ps` qua SSH
  mà không báo lỗi quyền, và chạy mọi lệnh PartFlow, `release.sh` và mọi tác vụ
  lập lịch (§6, §8) bằng chính tài khoản đó. Trên DSM, Docker socket thường
  thuộc về `root`, nên tài khoản này thường là `root` (`sudo -i` sau khi đăng
  nhập bằng một tài khoản quản trị có tên). Tài khoản cần có thư mục home: các
  script giữ hồ sơ release trong `~/partflow-deployments` và trạng thái giám
  sát trong `~/partflow-monitoring`.

Nếu NAS này không có Container Manager, không cài các gói Docker không được hỗ
trợ và không hạ mức bảo mật của DSM. Hãy dùng một máy ảo Linux hoặc một máy chủ
riêng ảo (virtual private server, VPS) thay thế ([`VPS.md`](./VPS.md)).

## 3. Sơ đồ thư mục

Tạo một shared folder riêng cho PartFlow trên volume mà quản trị viên đã chọn.
Đừng mặc định volume là `volume1`. Ví dụ:

```text
<NAS_VOLUME>/docker/partflow/
  repo/          production release checkout; .env.production lives here (mode 600)
  secrets/       production secret files only (PARTFLOW_SECRETS_DIR, mode 0700)
  backups/       production backups only (PARTFLOW_BACKUP_DIR, mode 0700)
  staging/       optional restricted staging (§4): its own checkout and dumps
```

Quy tắc:

- `secrets/` và `backups/` nằm ngoài checkout và chỉ production dùng; staging
  không bao giờ đọc hay ghi vào chúng.
- Chỉ tài khoản vận hành (§2) và tài khoản mà Container Manager dùng cần quyền
  ghi. Người dùng DSM thông thường không cần quyền truy cập nào.
- Không bao giờ đặt `.env`, `.env.production`, các tệp bí mật, bản dump
  database hay bản sao lưu trong một shared folder được phục vụ qua web.
- Không bao giờ cấp quyền đọc hoặc ghi cho nhóm `Everyone`.

## 4. Triển khai staging có giới hạn (tùy chọn)

Bỏ qua mục này khi cài production trực tiếp. Staging dùng tệp Compose phát
triển `compose.yaml`, tệp này publish các development server và không được gia
cố bảo mật. Chạy mọi lệnh staging từ `staging/repo/`. Luôn đặt tên project là
`partflow-staging` (`-p partflow-staging`): `compose.yaml` mặc định đặt tên
project là `partflow`, còn §8 bước 1 và `release.sh` tìm `partflow-staging`.

### 4.1 Lấy một revision cố định

Bật SSH cho việc cài đặt nếu chính sách công ty cho phép, đăng nhập bằng một tài
khoản quản trị có tên và clone repository. Checkout một commit hoặc tag cụ thể,
không bao giờ là một nhánh đang thay đổi, và ghi lại commit thực tế:

```bash
cd <NAS_VOLUME>/docker/partflow/staging
git clone https://github.com/CDSemi/part-flow.git repo
cd repo
git fetch --tags --prune
git checkout <approved-commit-or-tag>
git rev-parse HEAD
```

### 4.2 Cấu hình bí mật cho staging

Sao chép tệp môi trường mẫu và bảo vệ nó. Tệp `.env` thật không bao giờ được
commit.

```bash
cp .env.example .env
chmod 600 .env
```

Sau đó sửa `.env`:

- đặt `POSTGRES_PASSWORD` là một mật khẩu dài, duy nhất, chỉ dùng cho staging;
  không bao giờ dùng lại mật khẩu DSM, GitHub, production hay mật khẩu cá nhân;
- giữ một bản sao được bảo vệ trong kho mật khẩu của công ty;
- đặt `SITE_TIMEZONE` là tên múi giờ của nhà máy (ví dụ `America/Los_Angeles`)
  trước lần khởi động đầu tiên. Backend từ chối tên múi giờ không hợp lệ, và
  ngày hoàn thành cùng kết quả đúng hạn hay trễ hạn của các Work Order đã hoàn
  thành được tính theo lịch này. Production sau này phải dùng cùng giá trị.

`compose.yaml` tạo chuỗi kết nối database của backend từ `POSTGRES_USER`,
`POSTGRES_PASSWORD` và `POSTGRES_DB`, nên không cần thiết lập database nào khác.

### 4.3 Giới hạn các cổng được publish

`compose.yaml` publish PostgreSQL ở cổng `5432`, backend ở `8000` và frontend ở
`5173` trên mọi địa chỉ của NAS. Người dùng staging chỉ cần frontend, vì
development server của frontend chuyển tiếp các request `/api` tới backend.
Trước lần khởi động đầu tiên, sửa `compose.yaml` trong checkout staging này:

1. xóa các mục `ports` của service `db` và `backend`;
2. đổi mục của `frontend` để chỉ dùng địa chỉ LAN của NAS:

   ```yaml
   ports:
       - "<NAS LAN address>:5173:5173"
   ```

3. giữ mạng service của Compose ở chế độ riêng (không thay đổi gì khác);
4. trong DSM, Control Panel → Security → Firewall, chỉ cho phép cổng `5173` từ
   các subnet công ty dự định.

Staging được truy cập tại `http://<NAS LAN address>:5173`, không qua một tên
máy (host name); §5.1 giải thích lý do. Không bao giờ commit các thay đổi riêng
của NAS hay bí mật ngược vào repository.

### 4.4 Tạo Project trong Container Manager

Trong DSM:

1. Cài và mở **Container Manager**.
2. Mở **Project** → **Create**.
3. Đặt tên project là `partflow-staging`.
4. Chọn `staging/repo/` làm đường dẫn và tệp `compose.yaml` đã sửa.
5. Xem lại các service, mount, network, cổng và giá trị môi trường.
6. Build và khởi động Project.

Các lệnh SSH tương đương là:

```bash
docker compose -p partflow-staging up -d --build
docker compose -p partflow-staging ps
docker compose ls
```

`docker compose ls` phải liệt kê project với tên `partflow-staging`. Nếu nó hiện
`partflow`, dừng nó bằng `docker compose -p partflow down` (không có `-v`) và
khởi động lại với `-p partflow-staging`.

### 4.5 Áp dụng migration và thiết lập lần đầu

Chờ đến khi `db` và `backend` healthy, rồi nâng cấp schema database một lần và
lưu lại output:

```bash
docker compose -p partflow-staging exec backend uv run alembic current
docker compose -p partflow-staging exec backend uv run alembic upgrade head
docker compose -p partflow-staging exec backend uv run alembic current
```

Không bao giờ chạy `alembic downgrade` trên dữ liệu staging cần giữ mà chưa xem
xét các migration liên quan và chưa tạo một bản dump đã kiểm tra (§6).

Database mới chưa có Administrator. Đọc setup token và hoàn tất thiết lập lần
đầu trước khi bất kỳ ai khác có địa chỉ:

```bash
docker compose -p partflow-staging logs backend | grep "Setup token"
```

Mở `http://<NAS LAN address>:5173`, dùng **Set up PartFlow** với token, rồi
đăng ký từng thiết bị Scan Station trong Administration → Scan Stations →
`Devices…`.

### 4.6 Smoke test

Trên NAS, dùng địa chỉ LAN: với cách bind ở §4.3, không có gì lắng nghe tại
`127.0.0.1:5173`.

```bash
curl --fail --silent --show-error http://<NAS LAN address>:5173/api/health
docker compose -p partflow-staging ps
docker compose -p partflow-staging logs --tail=200 backend frontend db
```

Từ một máy trạm được phép:

1. mở `http://<NAS LAN address>:5173` và kiểm tra chỉ báo kết nối, và kiểm tra
   `http://<NAS LAN address>:5173/api/health` có trả lời;
2. kiểm tra các màn hình định thử đều tải được, và ghi lại những màn hình còn
   chưa có;
3. với các bản ghi thử được chỉ định, thử tạo, release, transfer, Machine,
   correction, stocking và allocation;
4. kiểm tra mỗi thao tác thành công được server xác nhận và màn hình được làm
   mới;
5. dừng backend một lát (`docker compose -p partflow-staging stop backend`),
   kiểm tra thao tác ghi bị chặn, khởi động lại (`… start backend`) và kiểm tra
   các màn hình phục hồi;
6. kiểm tra một máy ngoài các subnet được phép không kết nối được.

### 4.7 Giới hạn của staging

- không có số lượng production thật và không có dữ liệu cá nhân của nhân viên;
- không chuyển tiếp cổng internet hay chuyển đổi địa chỉ mạng (NAT), không
  publish qua QuickConnect và không có tunnel công khai;
- chỉ những người thử có tên, với người theo dõi trong các buổi thử;
- dump định kỳ có thể bỏ (§6) để lặp lại việc thử migration và cập nhật;
- báo rõ cho người thử rằng địa chỉ này là staging.

## 5. DSM Reverse Proxy cho HTTPS nội bộ

Reverse proxy của DSM nhận các kết nối HTTPS (Transport Layer Security, TLS)
cho một tên máy và chuyển tiếp chúng tới một service cục bộ. §5.1 nói về
trường hợp staging và §5.2 về service `web` của production.

### 5.1 Staging (development stack)

**Không thể dùng DSM reverse proxy cho staging với cấu hình phát triển hiện
tại.** Frontend của staging là Vite development server (Vite 6,
`frontend/package.json`). Vite chỉ trả lời các request có header `Host` là
`localhost`, một tên dưới `.localhost` hoặc một địa chỉ IP, và từ chối mọi tên
máy khác bằng HTTP 403 kèm thông báo "Blocked request", trừ khi tên đó được
liệt kê trong `server.allowedHosts`. `frontend/vite.config.ts` không liệt kê
tên nào, và DSM reverse proxy chuyển tiếp đúng tên máy mà trình duyệt đã dùng,
nên một request qua `https://<staging name>` bị từ chối.

Thay vào đó, truy cập staging tại `http://<NAS LAN address>:5173` (§4.3). Cách
này chạy được vì header `Host` khi đó là một địa chỉ IP, và backend phát triển
không yêu cầu HTTPS cho cookie đăng nhập. Lưu lượng không được mã hóa, thêm một
lý do để staging chỉ chứa dữ liệu giả hoặc dữ liệu có thể bỏ. Đừng lách cơ chế
kiểm tra này bằng cách sửa `vite.config.ts` trên NAS.

### 5.2 `web` của production

`compose.production.yaml` publish service `web` của production chỉ trên
`127.0.0.1:${PARTFLOW_HTTP_PORT}` (mặc định `18080`), nên DSM reverse proxy là
lối vào duy nhất. HTTPS là bắt buộc: production đánh dấu cookie đăng nhập là
secure, nên trình duyệt chỉ giữ nó qua HTTPS. Các giá trị bắt buộc lấy từ
[`../DEPLOYMENT.md`](../DEPLOYMENT.md) §3.1.

**Quy tắc reverse proxy.** Trong Control Panel → Login Portal → Advanced →
Reverse Proxy → **Create**:

| Thiết lập | Giá trị |
| --- | --- |
| Giao thức, tên máy, cổng nguồn | HTTPS, tên máy PartFlow, `443` |
| Giao thức, tên máy, cổng đích | HTTP, `127.0.0.1`, giá trị của `PARTFLOW_HTTP_PORT` |
| Custom Header | `X-Forwarded-For` = `$proxy_add_x_forwarded_for` và `X-Forwarded-Proto` = `$scheme` (xác nhận hoặc thêm) |
| Advanced Settings: timeout gửi và đọc của proxy | `300` giây (lớn hơn timeout import 180 giây của `web`) |
| Advanced Settings: kích thước request body | ít nhất 5 MiB (lớn hơn giới hạn 4 MiB của `web`) |

Lý do của các giá trị này:

- dùng `127.0.0.1`, không bao giờ dùng `localhost`, vì `localhost` có thể phân
  giải ra địa chỉ IPv6 trước, nơi `web` không lắng nghe;
- `X-Forwarded-For` đặt địa chỉ client ở cuối để `web` giới hạn tần suất theo
  từng client; nếu DSM không gửi được header này, mọi client dùng chung một
  bucket giới hạn tần suất, hãy ghi lại giới hạn đó;
- timeout ngắn hơn hoặc giới hạn body nhỏ hơn làm việc tải ảnh lên và import
  Work Order thất bại.

Ngoài ra:

- chuyển hướng HTTP sang HTTPS;
- không bao giờ ghi log cookie hay các header `X-PartFlow-Station-Device` và
  `X-PartFlow-CSRF`;
- trong tường lửa DSM và mạng phía trên, chỉ cho phép các nguồn LAN hoặc mạng
  riêng ảo (VPN) đã được duyệt;
- không để lộ trang quản trị DSM qua tên máy PartFlow.

**Chứng chỉ.** Chứng chỉ phải ghi tên máy PartFlow và do một tổ chức cấp chứng
chỉ (certificate authority, CA) công khai dùng giao thức Automatic Certificate
Management Environment (ACME), CA nội bộ của công ty, hoặc một CA riêng cấp.
Người dùng không bao giờ được bấm bỏ qua cảnh báo của trình duyệt để thay thế
([`../DEPLOYMENT.md`](../DEPLOYMENT.md) §3.1). Trong Control Panel → Security →
Certificate → **Add**, chọn một phương án:

1. **Import certificate** từ CA của công ty: import chứng chỉ, khóa riêng của nó
   và chuỗi trung gian.
2. **Get a certificate from Let's Encrypt**, khi tên máy là một tên DNS công
   khai. DSM tự động gia hạn chứng chỉ này.
3. **Create self-signed certificate**, khi không có hai phương án trên. DSM tạo
   trước một chứng chỉ gốc (CA riêng của bạn), rồi tạo chứng chỉ PartFlow được
   nó ký; nhập tên máy PartFlow làm common name và làm tên thay thế của chủ thể
   (subject alternative name, SAN). Xuất chứng chỉ gốc ra.

Sau đó mở **Settings** trên cùng trang và gán chứng chỉ cho tên máy của mục
reverse proxy PartFlow. Với phương án 1 và 3, cài chứng chỉ gốc của CA công ty
hoặc CA riêng làm chứng chỉ tin cậy trên mọi máy trạm và mọi thiết bị Scan
Station, qua hệ thống quản lý thiết bị của công ty hoặc bằng tay. DSM chỉ gia
hạn chứng chỉ Let's Encrypt: thay mọi chứng chỉ khác trước khi hết hạn và gán
lại. Kiểm tra ngày hết hạn bằng lệnh `openssl s_client` ở
[`../DEPLOYMENT.md`](../DEPLOYMENT.md) §3.1; kiểm tra `certificate` của
`check.sh` cảnh báo trước khi hết hạn (§8 Giám sát và cảnh báo).

## 6. Sao lưu

### Sao lưu production

Production dùng định dạng sao lưu của `deploy/production/backup.sh`: mỗi bản sao
lưu là một thư mục có manifest và tệp checksum `SHA256SUMS`
([`OPERATIONS_RUNBOOK.md`](./OPERATIONS_RUNBOOK.md) §3). Sao lưu được khuyến
nghị sau go-live và không bao giờ chặn việc sử dụng; cho đến khi tác vụ hằng
ngày chạy, bản sao lưu duy nhất là các bản mà `release.sh` tạo trước mỗi
release.

1. **Thư mục sao lưu.** Dùng thư mục `backups/` ở §3 (ví dụ
   `<NAS_VOLUME>/docker/partflow/backups`). Nó phải nằm ngoài release checkout,
   thư mục bí mật và mọi thư mục archive, thuộc về tài khoản vận hành (§2), với
   mode 0700. Đặt nó, không có dấu nháy, làm `PARTFLOW_BACKUP_DIR` trong
   `.env.production` trước lệnh `$PF` đầu tiên (§8 bước 2). Nó chỉ chứa bản sao
   lưu production, không bao giờ chứa của staging hay của một lần diễn tập khôi
   phục.
2. **Tác vụ sao lưu hằng ngày.** Control Panel → Task Scheduler → Create →
   Scheduled Task → User-defined script. User: tài khoản vận hành. Lịch: hằng
   ngày lúc 02:00. Task Settings → Run command:

   ```bash
   cd <running release checkout> && deploy/production/backup.sh --kind daily --operator scheduler --keep-daily 14 --keep-weekly 8
   ```

   `--keep-daily` và `--keep-weekly` là bắt buộc; 14 và 8 là ví dụ, owner chọn
   mức lưu giữ thật. Bật "Send run details by email" **chỉ khi script kết thúc
   bất thường**, để mọi mã thoát khác 0 (bản sao lưu bị từ chối, thất bại hoặc
   không kiểm chứng được, hoặc `backup_lock_stale`) đến được quản trị viên. Giữ
   giờ này ngoài các khung release
   ([`OPERATIONS_RUNBOOK.md`](./OPERATIONS_RUNBOOK.md) §5): một bản sao lưu
   đang chạy sẽ làm release dừng lại, và một bản sao lưu hằng ngày bắt đầu
   trong lúc release sẽ bị từ chối. Với lịch hằng ngày, mục tiêu điểm khôi phục
   (recovery point objective, RPO), tức lượng dữ liệu tối đa có thể mất, là 24
   giờ.
3. **Bản sao mã hóa ngoài NAS (Hyper Backup).** Tạo một tác vụ Hyper Backup có
   nguồn là thư mục sao lưu và đích nằm ngoài NAS này (một NAS khác, Synology
   C2 hoặc một dịch vụ tương thích S3). **Bật mã hóa phía client**; owner giữ
   mật khẩu và tệp khóa bên ngoài NAS, vì mất chúng là mất các bản sao ngoài
   NAS. Lập lịch lúc 03:00, sau bản sao lưu. Đặt cơ chế xoay vòng phiên bản của
   chính Hyper Backup theo quyết định của owner, chạy kiểm tra toàn vẹn hằng
   tuần và bật thông báo email khi thất bại. Việc xem các thông báo này là một
   phần của lịch thường kỳ ở
   [`OPERATIONS_RUNBOOK.md`](./OPERATIONS_RUNBOOK.md) §9. Snapshot cục bộ chỉ là
   lớp thứ hai.
4. **Thư mục archive (dự kiến).** PartFlow chưa tạo archive. Khi có, thư mục
   archive có tác vụ Hyper Backup riêng và **không xoay vòng phiên bản**: không
   công cụ nào được xoay vòng hay xóa bớt một archive.
5. **Diễn tập khôi phục.** Một lần sau go-live và sau đó mỗi quý, chạy từ
   release checkout:

   ```bash
   deploy/production/restore-test.sh --backup <latest daily backup> --operator "<name>"
   ```

   Nó khôi phục vào một project tách biệt và không bao giờ đụng tới database
   đang chạy ([`OPERATIONS_RUNBOOK.md`](./OPERATIONS_RUNBOOK.md) §4). Giữ lại
   bằng chứng của nó; các thời gian đo được cho biết thời gian khôi phục cần dự
   tính.

### Dump của staging

Staging (§4) dùng một bản dump PostgreSQL đơn giản thay thế. Chạy từ
`staging/repo/`; các biến trong dấu nháy đơn được mở rộng bên trong container
database, không phải trong shell DSM:

```bash
mkdir -p ../backups
docker compose -p partflow-staging exec -T db sh -c \
  'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom --no-owner --no-privileges' \
  > ../backups/partflow-$(date -u +%Y%m%dT%H%M%SZ).dump
```

Ghi một manifest bên cạnh mỗi bản dump:

```bash
git rev-parse HEAD
docker compose -p partflow-staging exec -T backend uv run alembic current
sha256sum ../backups/partflow-*.dump
```

Sao chép bản dump và manifest của nó tới một đích được mã hóa nằm ngoài NAS.
Trước khi tin một bản dump, hãy khôi phục nó vào một database riêng, rỗng, không
bao giờ đè lên database đang chạy
([`OPERATIONS_RUNBOOK.md`](./OPERATIONS_RUNBOOK.md) §4 liệt kê các bước).
Snapshot của một volume PostgreSQL đang chạy không phải là bản sao lưu database,
trừ khi dùng một quy trình snapshot nhất quán với database đã được ghi lại và
thử nghiệm.

## 7. Quy trình cập nhật staging

Để chuyển staging (§4) sang một revision mới hơn:

1. Báo cho người thử thời gian staging không dùng được.
2. Ghi lại commit hiện tại (`git rev-parse HEAD`) và revision Alembic.
3. Tạo và kiểm tra một bản dump mới (§6, Dump của staging).
4. Fetch và checkout commit đích đã được duyệt (`git fetch --tags --prune`,
   `git checkout <target>`), rồi áp dụng lại các thay đổi cổng ở §4.3 nếu việc
   checkout đã ghi đè chúng.
5. Xem lại các thay đổi của `.env.example`, `compose.yaml`, các Dockerfile, các
   tệp khóa phụ thuộc và các migration.
6. Build và khởi động: `docker compose -p partflow-staging up -d --build`.
7. Chạy `alembic upgrade head` một lần (§4.5).
8. Chạy smoke test ở §4.6 và đối soát (reconciliation,
   [`OPERATIONS_RUNBOOK.md`](./OPERATIONS_RUNBOOK.md) §7):
   `docker compose -p partflow-staging exec -T backend uv run python -m app.cli reconcile`.
9. Giữ commit trước và bản dump cho đến khi người thử xác nhận bản cập nhật.

Không bao giờ dùng một công cụ tự động cập nhật "latest" hay `main` không người
giám sát cho một hệ thống có database. Cập nhật production theo §8, Cập nhật
production.

## 8. Production (go-live)

Production là production stack: `compose.production.yaml`,
`.env.production.example`, các stage `production` của `backend/Dockerfile` và
`frontend/Dockerfile`, và `frontend/nginx/`. Nó không bao giờ là staging với một
địa chỉ mới. [`../DEPLOYMENT.md`](../DEPLOYMENT.md) §5 liệt kê những gì phần mềm
cần để hoạt động; mọi thứ khác được khuyến nghị sau go-live. Chạy mọi bước qua
SSH bằng tài khoản vận hành (§2), từ `repo/` khi bước 2.1 đã tạo nó.

1. **Gỡ staging khỏi NAS này.** `release.sh` từ chối chạy khi một project
   `partflow-staging` còn container trên Docker daemon (tách biệt môi trường,
   [`../DEPLOYMENT.md`](../DEPLOYMENT.md) §6). Dừng và xóa các container staging
   **không có** `-v`. Nếu staging chưa từng được cài trên NAS này, chỉ chạy
   lệnh thứ hai, lệnh này xác nhận không còn gì ở đó:

   ```bash
   docker compose -p partflow-staging down
   docker ps -a --format '{{.Label "com.docker.compose.project"}}' | sort -u
   ```

   Danh sách không được chứa `partflow-staging` hay `partflow` (một project phát
   triển được khởi động không có `-p`; gỡ nó theo cùng cách với `-p partflow`).
   Volume staging chỉ chứa dữ liệu staging và không bao giờ được gắn vào
   production; xóa nó khi không còn cần:
   `docker volume rm partflow-staging_postgres_data`. Sau đó xóa Project
   `partflow-staging` trong Container Manager và đóng cổng `5173` trên tường
   lửa.
2. **Lấy release và tạo cấu hình.**
   1. Clone repository vào `repo/` và checkout release tag (dạng
      `vMAJOR.MINOR.PATCH`, ví dụ `v1.0.0`;
      [`../DEPLOYMENT.md`](../DEPLOYMENT.md) §10):

      ```bash
      cd <NAS_VOLUME>/docker/partflow
      git clone https://github.com/CDSemi/part-flow.git repo
      cd repo
      git checkout <release tag>
      cp .env.production.example .env.production
      chmod 600 .env.production
      PF="docker compose -f compose.production.yaml --env-file .env.production"
      ```

   2. Tạo thư mục bí mật và thư mục sao lưu, mỗi thư mục thuộc về tài khoản vận
      hành:

      ```bash
      install -d -m 0700 <NAS_VOLUME>/docker/partflow/secrets
      install -d -m 0700 <NAS_VOLUME>/docker/partflow/backups
      ```

   3. Điền mọi giá trị trống trong `.env.production`: `PARTFLOW_RELEASE`
      (release tag), `PARTFLOW_SECRETS_DIR` và `PARTFLOW_BACKUP_DIR` (hai đường
      dẫn tuyệt đối ở trên, không có dấu nháy) và `PARTFLOW_SITE_TIMEZONE`
      (bước 3). Để trống `PARTFLOW_COMMIT`, `PARTFLOW_ACCEPT_SCHEMA_REVISION` và
      `PARTFLOW_TRUSTED_PROXY`. Kiểm tra `PARTFLOW_EDGE_SUBNET` (mặc định
      `172.30.250.0/24`) không bị dùng trên NAS hay trong LAN. Mọi lệnh `$PF`
      đều cần `PARTFLOW_BACKUP_DIR`, nên hãy đặt nó trước.
   4. Tạo ba tệp bí mật trong thư mục bí mật **trước mọi lệnh `$PF` khởi động
      `backend` hoặc một ops service**, vì Compose thay một tệp bí mật bị thiếu
      bằng một thư mục rỗng: `postgres_password`, `partflow_app_password` và
      `partflow_maintenance_password`. Mỗi tệp chứa đúng một dòng và có mode
      0444. Hai mật khẩu role dài 16 đến 128 ký tự ASCII in được, không có
      khoảng trắng, và khác nhau. Giữ một bản sao của từng tệp trong kho mật
      khẩu của công ty.

      ```bash
      cd <NAS_VOLUME>/docker/partflow/secrets
      openssl rand -base64 32 > postgres_password
      openssl rand -base64 32 > partflow_app_password
      openssl rand -base64 32 > partflow_maintenance_password
      chmod 0444 postgres_password partflow_app_password partflow_maintenance_password
      cd <NAS_VOLUME>/docker/partflow/repo
      ```

   5. Kiểm tra cấu hình: `$PF config --quiet` không in gì khi cấu hình hợp lệ.
3. **Kiểm tra múi giờ.** `PARTFLOW_SITE_TIMEZONE` là tên múi giờ của nhà máy (ví
   dụ `America/Los_Angeles`) và phải bằng `SITE_TIMEZONE` mà staging đã dùng,
   nếu từng có staging.
4. **Build, rồi tạo database.** Chạy theo đúng thứ tự:

   ```bash
   PARTFLOW_COMMIT=$(git rev-parse HEAD) $PF -f compose.production.build.yaml build
   $PF up -d db
   $PF --profile ops run --rm -T db-roles
   $PF --profile ops run --rm -T migrate --no-backup-reason "first install: empty database"
   ```

   Lệnh build tạo cả hai image (`backend` và `web`); không bao giờ build chỉ một
   image. `db-roles` tạo các database role `partflow_app` và
   `partflow_maintenance`; backend kết nối bằng role ít quyền nhất
   `partflow_app`, không bao giờ bằng owner của database. `migrate` tạo schema
   và áp dụng các quyền (grant). Volume `partflow-production_postgres_data` là
   mới và rỗng: dữ liệu staging không bao giờ được đưa lên. Khôi phục một bản
   sao lưu có sẵn vào production thì theo
   [`OPERATIONS_RUNBOOK.md`](./OPERATIONS_RUNBOOK.md) §6 path 3 hoặc §8 Host
   failure, với sự chấp thuận của owner được ghi lại trước khi `backend` khởi
   động.
5. **Khởi động ứng dụng với một backend worker, và mở HTTPS.**

   ```bash
   PARTFLOW_BACKEND_WORKERS=1 $PF up -d backend web
   curl --fail --silent --show-error http://127.0.0.1:18080/api/health
   ```

   Một worker bảo đảm chỉ có một setup token. Trong lệnh `curl`, dùng cổng bạn
   đã đặt ở `PARTFLOW_HTTP_PORT`; `127.0.0.1` đúng ở đây vì `web` chỉ lắng nghe
   trên địa chỉ loopback. Sau đó tạo quy tắc reverse proxy và chứng chỉ ở §5.2,
   và cài chứng chỉ gốc trên các máy trạm và thiết bị Scan Station nếu chứng
   chỉ của bạn cần.
6. **Thiết lập lần đầu.** Đọc token, mở `https://<PartFlow host name>`, dùng
   **Set up PartFlow**, rồi khởi động lại backend với số worker đã cấu hình:

   ```bash
   $PF logs backend | grep "Setup token"
   $PF up -d backend
   $PF run --rm --no-deps -T backend python -m app.cli reconcile
   ```

   Kết quả đối soát phải báo check "Append-only guards and database-role
   privileges intact" (check (h)) là `pass`. Sau đó đăng ký từng thiết bị Scan
   Station trong Administration → Scan Stations → `Devices…`.
7. **Không bao giờ chạy `down -v`**, và không bao giờ xóa một volume, trên
   project `partflow-production`: việc đó xóa database production. `$PF down`
   không có `-v` là cách an toàn duy nhất để dừng mọi thứ.

Sau go-live, khi thuận tiện, thiết lập các mục khuyến nghị ở
[`../DEPLOYMENT.md`](../DEPLOYMENT.md) §5: bản sao lưu hằng ngày và bản sao mã
hóa ngoài NAS (§6), một lần diễn tập khôi phục có đo thời gian (§6), và các tác
vụ giám sát bên dưới.

### Cập nhật production

Mọi release sau đều chạy qua `deploy/production/release.sh`. Script này tạo một
bản sao lưu đã kiểm chứng, build các image mới mà không đụng tới các image đang
chạy, migrate database, chuyển ứng dụng sang release mới và chạy smoke check.
Chi tiết, tùy chọn và kết quả nằm ở
[`OPERATIONS_RUNBOOK.md`](./OPERATIONS_RUNBOOK.md) §5 và
[`../DEPLOYMENT.md`](../DEPLOYMENT.md) §7. Trong `repo/`:

```bash
git fetch --tags --prune
git checkout <new release tag>
deploy/production/release.sh --release <new release tag> --operator "<name>" --approver "<name>"
```

Một người có thể ghi cùng một tên cho operator và approver. `.env.production`
không được git theo dõi, nên nó vẫn ở nguyên chỗ; `release.sh` ghi tag mới vào
đó lúc chuyển release. Giữ các image của release trước cho đến khi hết thời gian
quan sát (không chạy `docker image prune -a`), vì rollback cần chúng. Nếu bạn
dùng một thư mục checkout mới cho mỗi release thay vì `repo/`, hãy sao chép
`.env.production` vào đó và cập nhật đường dẫn checkout trong mọi tác vụ lập
lịch sau release.

### Giám sát và cảnh báo

Cấu hình Control Panel → Notification → Email trước, để email báo lỗi của Task
Scheduler ra được khỏi NAS. Sau đó tạo hai tác vụ trong Control Panel → Task
Scheduler → Create → Scheduled Task → User-defined script, bằng tài khoản vận
hành sở hữu thư mục sao lưu:

| Tên tác vụ | Lịch | Run command |
| --- | --- | --- |
| `PartFlow check` | hằng ngày từ 00:00, lặp lại mỗi 15 phút đến 23:45 | `cd <running release checkout> && deploy/production/check.sh --url https://<partflow host> --quiet` |
| `PartFlow reconcile` | hằng ngày lúc 04:00 (sau bản sao lưu 02:00) | `cd <running release checkout> && deploy/production/scheduled-reconcile.sh` |

Ở cả hai tác vụ, bật "Send run details by email" **chỉ khi script kết thúc bất
thường**. `check.sh --quiet` không in gì và thoát với mã 0 trừ khi có lỗi cần
báo, và nó chỉ báo lại một lỗi không đổi sau mỗi 6 giờ. Thêm
`--resolve-to 127.0.0.1` vào tác vụ check khi NAS không phân giải được tên máy
PartFlow của chính nó.

Thử đường email một lần: tạm thêm
`--only disk_backup --min-free-percent 100 --renotify-hours 0` vào lệnh của tác
vụ check, chạy tác vụ một lần từ Task Scheduler (**Run**), xác nhận email báo lỗi
đến nơi, rồi bỏ các tùy chọn đã thêm. Mức trống tối thiểu 100 % cố ý làm mọi ổ
đĩa được đo thất bại. Các ngưỡng, mã thoát và ý nghĩa của từng dòng nằm ở
[`OPERATIONS_RUNBOOK.md`](./OPERATIONS_RUNBOOK.md) §2 và §9.

## 9. Dung lượng và độ tin cậy

Một con số bộ nhớ hay bộ xử lý chung chung không chứng minh NAS đã sẵn sàng. Hãy
đo trên chính NAS này, trong các buổi thử staging hoặc những tuần đầu của
production:

- mức dùng bộ xử lý và bộ nhớ lúc rảnh và lúc cao điểm của mọi service PartFlow
  và của các tác vụ sẵn có trên NAS;
- mức tăng dữ liệu và index của PostgreSQL;
- dung lượng tạm cho việc build image và cập nhật;
- thời gian và kích thước sao lưu, và thời gian khôi phục (các dòng tiến độ của
  `backup.sh` và tệp `evidence.json` của `restore-test.sh`);
- thời gian phản hồi của màn hình và API từ các mạng xưởng sản xuất;
- hành vi khi NAS khởi động lại, container khởi động lại, mạng bị gián đoạn và
  UPS tắt máy.

Dùng các số đo này để đặt giới hạn tài nguyên trong `.env.production`:

| Thiết lập | Mặc định |
| --- | --- |
| `PARTFLOW_DB_MEMORY`, `PARTFLOW_DB_CPUS` | `1g`, `1.0` |
| `PARTFLOW_BACKEND_MEMORY`, `PARTFLOW_BACKEND_CPUS` | `1g`, `2.0` |
| `PARTFLOW_WEB_MEMORY`, `PARTFLOW_WEB_CPUS` | `128m`, `0.5` |
| `PARTFLOW_OPS_MEMORY`, `PARTFLOW_OPS_CPUS` | `512m`, `1.0` |

Giữ đủ dung lượng đĩa trống cho database, ít nhất một bộ image release dự phòng,
công việc migration tạm thời và các bản sao lưu cục bộ. Các kiểm tra đĩa của
`check.sh` cảnh báo khi dung lượng trống dưới 15 %:

| Check | Đo |
| --- | --- |
| `disk_data` | volume database |
| `disk_backup` | thư mục sao lưu |
| `disk_docker` | thư mục gốc của Docker (image và log container) |

Mức tăng của database được ghi trong `growth.tsv` ở thư mục trạng thái của
check (`~/partflow-monitoring`). Một bản sao lưu cần dung lượng trống bằng hai
lần bản dump mới nhất cộng 1 GiB trong thư mục sao lưu, và một lần diễn tập
khôi phục cần năm lần bản dump cộng 1 GiB trên thư mục gốc của Docker (lần diễn
tập chạy một backend worker).

## 10. Chuyển từ Synology sang VPS

1. Chuẩn bị VPS theo [`VPS.md`](./VPS.md) ở cùng release ứng dụng và cùng mức
   migration với NAS.
2. Diễn tập trên VPS với một bản sao lưu production gần đây:
   `deploy/production/restore-test.sh --backup <name> --operator "<name>"`.
3. Thông báo và bắt đầu đóng băng ghi (write freeze) production trên NAS:
   `$PF stop backend`.
4. Tạo bản sao lưu cuối cùng trong lúc đóng băng:
   `deploy/production/backup.sh --kind manual --operator "<name>" --reason "move to VPS"`.
   Tệp `SHA256SUMS` của nó là manifest checksum.
5. Sao chép thư mục sao lưu sang VPS qua một kênh được mã hóa và kiểm tra nó ở
   đó bằng `sha256sum -c SHA256SUMS` và `backup-verify`.
6. Khôi phục vào PostgreSQL của VPS bằng quy trình khôi phục vào instance mới ở
   [`OPERATIONS_RUNBOOK.md`](./OPERATIONS_RUNBOOK.md) §6 (một volume
   `postgres_data` mới, rỗng; sự chấp thuận của owner được ghi lại trước khi
   `backend` khởi động).
7. Trước khi mở truy cập, kiểm tra revision của database
   (`$PF run --rm --no-deps -T backend python -m app.cli revision`) và chạy các
   kiểm tra đối soát.
8. Đổi bản ghi DNS nội bộ với thời gian sống (time to live, TTL) ngắn và kiểm
   tra các client.
9. Giữ instance trên NAS ở trạng thái dừng và có thể khôi phục cho đến khi hết
   khung thời gian rollback. Không bao giờ để cả hai instance cùng nhận thao tác
   ghi.

## Lịch sử thay đổi

| Ngày | Commit | Thay đổi |
| --- | --- | --- |
| 2026-11-09 | uncommitted | Khôi phục các mục nhỏ mà lần viết lại đã bỏ sót: kiểm tra health trong smoke test của staging, bản sao ngoài NAS và restore test của dump staging, reconciliation của staging, tên đầy đủ của reconcile check (h) và lệnh `revision` đầy đủ; bước 1 nay áp dụng cả cho NAS chưa từng cài staging. |
| 2026-11-09 | `b405c6e` | Viết lại thành nội dung hiện hành cho người đọc mới; bỏ pilot và release gate theo quyết định của owner. |
| 2026-10-09 | 03bd90c | Thêm các tác vụ giám sát và ngưỡng đĩa của các kiểm tra host. |
| 2026-10-08 | a330383 | Thêm các tác vụ sao lưu production, bản sao mã hóa ngoài NAS và diễn tập khôi phục. |
| 2026-10-08 | 1116b07 | Thêm các database role của production vào các bước cài đặt. |
| 2026-10-08 | b820e7a | Thêm luồng release với `release.sh` cho các release sau. |
| 2026-10-08 | d7e86a9 | Thêm production stack, thiết lập DSM reverse proxy và các phương án chứng chỉ. |
| 2026-10-06 | 702ab1c | Thêm thiết lập Administrator lần đầu và đăng ký thiết bị Scan Station. |
| 2026-09-02 | d2f68de | Phiên bản đầu của hướng dẫn Synology NAS cùng bản dịch tiếng Việt. |
