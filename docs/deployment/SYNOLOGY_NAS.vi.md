# Cài đặt PartFlow trên Synology NAS

> **Bản gốc:** [`SYNOLOGY_NAS.md`](./SYNOLOGY_NAS.md). Đồng bộ với bản tiếng Anh tại commit `e0e5056` cộng các thay đổi chưa
> commit ngày 2026-11-12. Bản tiếng Anh là nguồn chuẩn (source of truth); khi hai bản khác nhau, bản tiếng Anh có
> hiệu lực.

Hướng dẫn này cài PartFlow production trên Synology NAS của công ty, từng bước, từ một NAS trống đến khi mọi người làm
việc trong PartFlow qua HTTPS. Sau đó hướng dẫn thiết lập bản sao lưu hằng ngày và việc giám sát, rồi chỉ cách cài một
bản phát hành (release) mới. Hãy làm theo đúng thứ tự các mục và không bỏ qua bước nào: mỗi bước cho biết bạn phải làm
gì, bạn sẽ thấy gì, và phải làm gì khi bạn thấy điều khác.

Bạn cần một tài khoản quản trị (administrator) của DiskStation Manager (DSM) và một máy tính có phần mềm khách SSH
(Secure Shell); không cần kinh nghiệm nào khác. Trước khi bắt đầu, hãy đọc [`../DEPLOYMENT.vi.md`](../DEPLOYMENT.vi.md)
§1 và §2 (mười phút): hai mục này giải thích bạn đang cài gì, các thuật ngữ dùng ở đây và các quy tắc an toàn. Việc vận
hành hằng ngày sau khi cài nằm trong [`OPERATIONS_RUNBOOK.vi.md`](./OPERATIONS_RUNBOOK.vi.md).

## 1. Trước khi bắt đầu

### Kết quả bạn có được khi hoàn thành

- PartFlow chạy trên NAS trong Compose project `partflow-production`, truy cập được tại `https://<HOSTNAME>` từ mạng
  công ty và, nếu bạn chọn, từ Internet qua Cloudflare;
- Administrator đầu tiên đã được tạo và các thiết bị Scan Station đã được đăng ký (enroll);
- sau §11, có một bản sao lưu hằng ngày đã được xác minh cùng một bản sao được mã hóa nằm ngoài NAS, và có email cảnh
  báo khi một kiểm tra thất bại.

### Bạn cần chuẩn bị

- Một Synology NAS chạy DSM 7.2 trở lên, trên đó có thể cài gói **Container Manager** (Package Center chỉ cung cấp gói
  này cho các model hỗ trợ nó).
- Bộ nhớ trống cho PartFlow ngoài phần NAS đã dùng: ba container của PartFlow bị giới hạn tổng cộng khoảng 2,2 GB, cộng
  thêm 512 MB trong lúc một tác vụ chạy một lần (one-shot job) đang chạy (§13).
- Dung lượng trống trên volume cho các image, database và các bản sao lưu cục bộ (§13).
- Truy cập Internet từ NAS: quá trình build tải mã nguồn, các base image và các gói phần mềm.
- Một tài khoản quản trị DSM, và một phần mềm khách SSH trên máy tính của bạn (Windows 10 và 11 có sẵn `ssh` trong
  Terminal và PowerShell).
- Một tên miền truy cập (host name) cho PartFlow, ví dụ `partflow.yourdomain.com`, và một quyết định về cách mọi người
  truy cập:
  - **Phương án A:** chỉ từ mạng công ty hoặc mạng riêng ảo (VPN);
  - **Phương án B:** truy cập cả từ Internet, qua Cloudflare ([`SYNOLOGY_PUBLIC_HTTPS.vi.md`](./SYNOLOGY_PUBLIC_HTTPS.vi.md)).
- Khoảng một đến hai giờ. Việc build các image chiếm phần lớn thời gian đó.

### Các giá trị dùng trong hướng dẫn này

Thay các placeholder này bằng giá trị của bạn ở mọi nơi chúng xuất hiện:

| Placeholder | Ý nghĩa | Ví dụ |
| --- | --- | --- |
| `<NAS_IP>` | Địa chỉ cố định của NAS trong mạng cục bộ (LAN) | `192.168.0.11` |
| `<HOSTNAME>` | Tên mà mọi người gõ trong trình duyệt | `partflow.yourdomain.com` |
| `<TAG>` | Tag phát hành cần cài ([`../DEPLOYMENT.vi.md`](../DEPLOYMENT.vi.md) §10.2) | `v1.0.0` |
| `<TIMEZONE>` | Múi giờ của nhà máy, theo tên IANA | `America/Los_Angeles` |
| `/volume1` | Volume chứa thư mục dùng chung `docker`. Nếu volume của bạn khác, hãy đổi ở mọi lệnh. | `/volume1` |

Cổng `18080` trong hướng dẫn này là giá trị mặc định của `PARTFLOW_HTTP_PORT`. Để thử một bản phát hành mới trước khi
cài, hãy dùng development stack trên một máy trạm (xem [`README.vi.md`](../../README.vi.md) ở thư mục gốc); không bao
giờ cài một PartFlow stack thứ hai trên NAS này.

## 2. Chuẩn bị DSM

Thực hiện các thiết lập một lần này trong giao diện web của DSM.

1. **Cài các gói.** Mở **Package Center**, tìm **Container Manager** và cài đặt. Trong lúc cài, DSM tạo thư mục dùng
   chung `docker` trên volume bạn chọn; hãy ghi nhớ volume đó (hướng dẫn này dùng `/volume1`). Sau đó tìm **Git
   Server** và cài đặt: gói này cung cấp lệnh `git` mà việc cài đặt và mọi bản phát hành đều cần.
2. **Đặt địa chỉ cố định cho NAS.** Dành riêng địa chỉ của NAS trong cài đặt DHCP của router (khuyến nghị), hoặc đặt
   trong **Control Panel → Network → Network Interface**, chọn kết nối LAN, **Edit → IPv4 → Use manual
   configuration**. Ghi địa chỉ đó lại thành `<NAS_IP>`.
3. **Kiểm tra đồng hồ.** Trong **Control Panel → Regional Options → Time**, chọn múi giờ và chọn **Synchronize with NTP
   server**. Bản sao lưu, chứng chỉ và log đều phụ thuộc vào đồng hồ đúng.
4. **Bật SSH.** Trong **Control Panel → Terminal & SNMP → Terminal**, chọn **Enable SSH service** (cổng 22) và bấm
   **Apply**.
5. **Nối bộ lưu điện (UPS), khuyến nghị.** Trong **Control Panel → Hardware & Power → UPS**, bật hỗ trợ UPS để NAS tắt
   an toàn khi mất điện.

## 3. Mở phiên SSH bằng root

Mọi lệnh của PartFlow và mọi tác vụ theo lịch đều chạy bằng `root`, vì Docker trên DSM cần quyền này. `root` là tài
khoản vận hành (operating account) của bản cài này ([`../DEPLOYMENT.vi.md`](../DEPLOYMENT.vi.md) §2.1); hồ sơ của nó
nằm ở `/root/partflow-deployments` và trạng thái giám sát nằm ở `/root/partflow-monitoring`.

1. Trên máy tính của bạn, mở một terminal và kết nối bằng tài khoản quản trị DSM. Lần đầu, trả lời `yes` để tin cậy
   NAS.

   ```sh
   ssh <your DSM administrator>@<NAS_IP>
   ```

2. Chuyển sang `root`. `sudo` hỏi lại cùng mật khẩu đó; sau đó dấu nhắc kết thúc bằng `#`.

   ```sh
   sudo -i
   ```

3. Kiểm tra các công cụ. Mỗi lệnh in ra một phiên bản. `python3` phải từ 3.8 trở lên. Nếu không tìm thấy `git`, nghĩa
   là Git Server chưa được cài (§2 bước 1); nếu `docker` báo lỗi quyền, nghĩa là bạn chưa phải `root`.

   ```sh
   docker version --format '{{.Server.Version}}'
   docker compose version
   git --version
   python3 --version
   ```

4. Kiểm tra xem NAS có giới hạn được mức dùng bộ xử lý của một container hay không. Một số kernel DSM không làm được,
   và khi đó Docker từ chối khởi động mọi container có giới hạn bộ xử lý (`NanoCPUs can not be set`):

   ```sh
   docker info 2>&1 | grep -i 'cfs'
   ```

   Nếu lệnh in ra `No cpu cfs quota support`, hãy ghi nhớ: §5 bước 2 sẽ tắt các giới hạn bộ xử lý. Nếu không in ra gì,
   bạn không cần làm gì.

5. Kiểm tra rằng NAS này không có PartFlow stack nào khác:

   ```sh
   docker ps -a --format '{{.Label "com.docker.compose.project"}}' | sort -u
   ```

   Danh sách không được chứa `partflow-staging` lẫn `partflow`. Nếu một trong hai xuất hiện, nghĩa là một staging hoặc
   development stack cũ vẫn còn: hãy dừng nó bằng `docker compose -p <that name> down` (không dùng `-v`) và xóa
   Project của nó trong Container Manager. Data volume của nó chỉ chứa dữ liệu thử; hãy xóa bằng
   `docker volume rm <that name>_postgres_data` khi bạn chắc chắn không ai còn cần.

**Mọi phiên làm việc sau này** bắt đầu giống nhau: `ssh`, `sudo -i`, rồi hai dòng dưới đây, mở thư mục mã nguồn của bản
phát hành (release checkout) và đặt biến viết tắt `PF`. Phiên mới không nhớ `PF`, nên hãy đặt lại mỗi lần.

```sh
cd /volume1/docker/partflow/repo
PF="docker compose -f compose.production.yaml --env-file .env.production"
```

## 4. Tạo các thư mục của PartFlow

Tạo thư mục bí mật (secrets) và thư mục sao lưu (backup), chỉ `root` đọc được:

```sh
mkdir -p /volume1/docker/partflow/secrets /volume1/docker/partflow/backups
chmod 700 /volume1/docker/partflow/secrets /volume1/docker/partflow/backups
ls -ld /volume1/docker/partflow/secrets /volume1/docker/partflow/backups
```

Cả hai dòng kết quả đều bắt đầu bằng `drwx------` và ghi `root`. Cấu trúc đầy đủ, sau khi §5 tạo `repo/`, là:

```text
/volume1/docker/partflow/
  repo/       the release checkout; .env.production lives here
  secrets/    the three password files (PARTFLOW_SECRETS_DIR)
  backups/    one sub-folder per database backup (PARTFLOW_BACKUP_DIR)
```

Không bao giờ cấp cho nhóm DSM `Everyone` quyền truy cập các thư mục này, không bao giờ chia sẻ chúng với người dùng
DSM thông thường, và không bao giờ đặt chúng trong thư mục mà một web server công bố ra ngoài.

## 5. Tải bản phát hành và cấu hình

1. Tải repository về và checkout tag phát hành. Các tag được liệt kê trên trang GitHub Releases của repository, hoặc
   bằng `git tag --list 'v*'` sau khi clone.

   ```sh
   cd /volume1/docker/partflow
   git clone https://github.com/CDSemi/part-flow.git repo
   cd repo
   git checkout <TAG>
   git describe --tags
   ```

   `git checkout` báo "detached HEAD": điều này bình thường với một bản phát hành. `git describe --tags` in ra
   `<TAG>`.

2. Tạo tệp cấu hình từ mẫu của nó và điền bốn giá trị không có mặc định:

   ```sh
   cp .env.production.example .env.production
   chmod 600 .env.production
   sed -i 's|^PARTFLOW_RELEASE=.*|PARTFLOW_RELEASE=<TAG>|' .env.production
   sed -i 's|^PARTFLOW_SECRETS_DIR=.*|PARTFLOW_SECRETS_DIR=/volume1/docker/partflow/secrets|' .env.production
   sed -i 's|^PARTFLOW_BACKUP_DIR=.*|PARTFLOW_BACKUP_DIR=/volume1/docker/partflow/backups|' .env.production
   sed -i 's|^PARTFLOW_SITE_TIMEZONE=.*|PARTFLOW_SITE_TIMEZONE=<TIMEZONE>|' .env.production
   grep -E '^PARTFLOW_(RELEASE|SECRETS_DIR|BACKUP_DIR|SITE_TIMEZONE|HTTP_PORT|EDGE_SUBNET)=' .env.production
   ```

   Dòng `grep` in ra bốn giá trị bạn đặt và hai giá trị mặc định mà các bước sau dùng (`PARTFLOW_HTTP_PORT` và
   `PARTFLOW_EDGE_SUBNET`); hãy kiểm tra từng giá trị. `PARTFLOW_SITE_TIMEZONE` là múi giờ của nhà máy: nó quyết định
   ngày của việc hoàn thành Work Order, nên hãy đặt trước lần khởi động đầu tiên. Giữ nguyên mọi giá trị khác; từng key
   được giải thích trong [`../DEPLOYMENT.vi.md`](../DEPLOYMENT.vi.md) §3.1.

   **Chỉ khi §3 bước 4 in ra `No cpu cfs quota support`**, hãy tắt các giới hạn bộ xử lý (`0` nghĩa là không giới hạn;
   các giới hạn bộ nhớ vẫn giữ):

   ```sh
   sed -i 's/^\(PARTFLOW_[A-Z]*_CPUS\)=.*/\1=0/' .env.production
   grep '_CPUS=' .env.production
   ```

3. Kiểm tra rằng subnet của mạng nội bộ `edge`, `172.30.250.0/24`, không bị NAS hay mạng công ty sử dụng. Lệnh này
   không được in ra gì:

   ```sh
   ip -4 route | grep '172\.30\.250\.'
   ```

   Nếu nó in ra một dòng, hoặc nếu mạng công ty dùng các địa chỉ `172.30.250.x`, hãy chọn một subnet khác chưa dùng, ví
   dụ `sed -i 's|^PARTFLOW_EDGE_SUBNET=.*|PARTFLOW_EDGE_SUBNET=172.30.251.0/24|' .env.production`.

## 6. Tạo các tệp bí mật

Tạo ba tệp mật khẩu **trước** bất kỳ lệnh nào khởi động `backend` hoặc một tác vụ chạy một lần. Nếu lúc đó thiếu một
tệp, Docker Compose sẽ tạo một thư mục rỗng mang tên tệp đó; hãy xóa thư mục ấy bằng `rmdir` rồi tạo tệp.

```sh
cd /volume1/docker/partflow/secrets
for name in postgres_password partflow_app_password partflow_maintenance_password; do
    openssl rand -base64 32 > "$name"
done
chmod 0444 postgres_password partflow_app_password partflow_maintenance_password
ls -l
cd /volume1/docker/partflow/repo
```

`ls -l` hiển thị ba tệp với quyền `-r--r--r--` và kích thước 45 byte. Mỗi tệp chứa một mật khẩu ngẫu nhiên trên một
dòng. Hãy sao chép ba giá trị (`cat <file name>` hiển thị từng tệp) vào kho mật khẩu của công ty.

Bây giờ đặt biến viết tắt `PF` và kiểm tra cấu hình. `$PF config --quiet` không in gì khi cấu hình hợp lệ; một thông
báo như `Set PARTFLOW_SITE_TIMEZONE in .env.production` chỉ ra một giá trị còn trống cần điền.

```sh
PF="docker compose -f compose.production.yaml --env-file .env.production"
$PF config --quiet
```

## 7. Build các image và tạo database

1. Build hai image, `backend` và `web`, từ bản phát hành đã checkout. Việc này mất 10 đến 30 phút. Nếu nó dừng với lỗi
   mạng, hãy chạy lại đúng lệnh đó.

   ```sh
   PARTFLOW_COMMIT=$(git rev-parse HEAD) $PF -f compose.production.build.yaml build
   docker image ls 'partflow/*'
   ```

   Danh sách hiển thị `partflow/backend` và `partflow/web`, cả hai đều có tag `<TAG>`.

2. Khởi động database và chờ đến khi nó healthy. Chạy lại `$PF ps` cho đến khi dòng `db` hiển thị `(healthy)`, mất
   khoảng 30 giây.

   ```sh
   $PF up -d db
   $PF ps
   ```

3. Tạo hai database role mà PartFlow dùng, rồi tạo schema của database và các quyền (permissions) của nó:

   ```sh
   $PF --profile ops run --rm -T db-roles
   $PF --profile ops run --rm -T migrate --no-backup-reason "first install: empty database"
   ```

   Mỗi lệnh in ra một báo cáo JSON. Báo cáo của `migrate` chứa `"result": "upgraded"`. `db-roles` tạo các role
   `partflow_app`, mà `backend` dùng, và `partflow_maintenance`; `backend` không bao giờ kết nối bằng chủ sở hữu
   database. Nếu `migrate` báo `roles_not_provisioned`, hãy chạy lệnh `db-roles` trước.

Database bắt đầu rỗng, trong volume mới `partflow-production_postgres_data`. Để bắt đầu từ một bản sao lưu có sẵn, ví dụ
khi chuyển PartFlow từ máy chủ khác sang, hãy làm theo runbook §6 (New-instance restore) thay cho bước 3.

## 8. Khởi động PartFlow và kiểm tra trên NAS

Khởi động `backend` với **một** tiến trình worker, để có đúng một mã thiết lập (setup token) cho §10, và khởi động
`web`:

```sh
PARTFLOW_BACKEND_WORKERS=1 $PF up -d backend web
$PF ps
curl -fsS http://127.0.0.1:18080/web-health
curl -fsS http://127.0.0.1:18080/api/health
```

Bạn sẽ thấy:

- `$PF ps` liệt kê `db`, `backend` và `web`, mỗi cái `(healthy)` sau khoảng 30 giây;
- `/web-health` in ra `live`;
- `/api/health` in ra một dòng JSON chứa `"status":"ok"`, `"release":"<TAG>"` và `"schema":"current"`.

Nếu một kiểm tra thất bại, hãy đọc các dòng log cuối bằng `$PF logs --tail=100 backend web`. Địa chỉ
`http://<NAS_IP>:18080` không hoạt động từ máy tính khác: `web` chỉ lắng nghe trên địa chỉ loopback của chính NAS, theo
thiết kế. Mọi người truy cập PartFlow qua HTTPS, mà §9 thiết lập.

## 9. Thiết lập HTTPS

PartFlow yêu cầu HTTPS: cookie đăng nhập của nó được đánh dấu secure, nên trình duyệt chỉ giữ cookie đó qua HTTPS. Reverse
proxy của DSM nhận các kết nối HTTPS cho `<HOSTNAME>` ở cổng 443 và chuyển tiếp chúng tới `http://127.0.0.1:18080`.

- **Phương án A, chỉ mạng công ty hoặc VPN:** làm §9.1, §9.2 và §9.3, rồi tiếp tục với §10.
- **Phương án B, truy cập cả từ Internet:** làm theo [`SYNOLOGY_PUBLIC_HTTPS.vi.md`](./SYNOLOGY_PUBLIC_HTTPS.vi.md) từ đầu
  đến cuối. Hướng dẫn đó dùng §9.2 bên dưới cho quy tắc reverse proxy và kết thúc bằng bài kiểm tra riêng của nó. Sau đó
  tiếp tục với §10.

### 9.1 Tên miền truy cập và chứng chỉ (phương án A)

1. **Làm cho tên miền truy cập trỏ tới NAS.** Trong máy chủ DNS nội bộ của công ty, hoặc trong cài đặt DNS cục bộ của
   router, tạo một bản ghi `A` từ `<HOSTNAME>` tới `<NAS_IP>`. Trên một máy trạm, `nslookup <HOSTNAME>` phải trả lời
   `<NAS_IP>`.
2. **Lấy chứng chỉ cho tên miền truy cập.** Mở **Control Panel → Security → Certificate → Add → Add a new certificate →
   Next** và chọn một nguồn:
   - **Import certificate**, khi công ty có tổ chức cấp chứng chỉ (CA) riêng: chọn khóa riêng, chứng chỉ và chứng chỉ
     trung gian mà CA đã cấp cho `<HOSTNAME>`.
   - **Get a certificate from Let's Encrypt**, chỉ khi `<HOSTNAME>` là một tên DNS công khai phân giải tới địa chỉ công
     khai của công ty và cổng 80 từ Internet đến được NAS. DSM tự gia hạn chứng chỉ này. Với truy cập công khai, phương
     án B là thiết kế được tài liệu hóa.
   - **Create self-signed certificate**, khi không có hai cách trên. Trước tiên DSM tạo một chứng chỉ gốc, chính là CA
     riêng của bạn, rồi tạo chứng chỉ PartFlow do nó ký: nhập `<HOSTNAME>` làm common name và làm subject alternative
     name. Sau đó chọn chứng chỉ mới, chọn **Export certificate**, và chỉ lấy chứng chỉ gốc ra khỏi tệp nén đã tải về
     (DSM thường đặt tên là `syno-ca-cert.pem`). Tệp nén cũng chứa các khóa riêng: không bao giờ phát tệp nén hay một
     tệp khóa cho người khác.
3. **Làm cho mọi thiết bị tin cậy chứng chỉ.** Với CA của công ty hoặc chứng chỉ gốc của DSM, hãy cài chứng chỉ gốc làm
   chứng chỉ tin cậy trên mọi máy trạm và mọi thiết bị Scan Station, qua hệ thống quản lý thiết bị của công ty hoặc làm
   thủ công. Trên Windows: mở tệp, **Install Certificate → Local Machine → Place all certificates in the following
   store → Trusted Root Certification Authorities**. Không bao giờ bảo người dùng bấm bỏ qua cảnh báo của trình duyệt.
4. **Ghi lại ngày hết hạn.** DSM chỉ gia hạn chứng chỉ Let's Encrypt; hãy thay mọi chứng chỉ khác trước khi nó hết hạn.
   Tác vụ `check.sh` ở §11 cảnh báo trước 21 ngày.

### 9.2 Tạo quy tắc reverse proxy

Quy tắc này giống nhau cho phương án A và phương án B.

1. Mở **Control Panel → Login Portal → Advanced → Reverse Proxy** và bấm **Create**.
2. Trên tab **General** nhập:

   | Field | Value |
   | --- | --- |
   | Reverse Proxy Name | `PartFlow` |
   | Source: Protocol, Hostname, Port | `HTTPS`, `<HOSTNAME>`, `443` |
   | Source: Enable HSTS | chưa chọn lúc này; chỉ chọn khi HTTPS đã hoạt động ở mọi nơi |
   | Destination: Protocol, Hostname, Port | `HTTP`, `127.0.0.1`, `18080` |

3. Trên tab **Custom Header**, mở **Create ▼**, chọn **Create** (không chọn WebSocket), và thêm hàng đầu tiên bên dưới.
   Làm tương tự cho hàng thứ hai. Nếu một hàng giống hệt đã có trong danh sách, đừng thêm hai lần.

   | Header Name | Value |
   | --- | --- |
   | `X-Forwarded-For` | `$proxy_add_x_forwarded_for` |
   | `X-Forwarded-Proto` | `$scheme` |

4. Trên tab **Advanced Settings**, đặt **Proxy send timeout** và **Proxy read timeout** là `300` giây. Nếu phiên bản DSM
   của bạn có cài đặt kích thước request body, hãy đặt nó tối thiểu 5 MiB. Giữ nguyên các cài đặt khác.
5. Bấm **Save**.
6. Gán chứng chỉ cho quy tắc: mở **Control Panel → Security → Certificate → Settings**, tìm `<HOSTNAME>` trong danh sách
   dịch vụ, chọn chứng chỉ PartFlow và bấm **OK**. Đừng dựa vào chứng chỉ mặc định của DSM.

Lý do của các giá trị này:

- Đích là `127.0.0.1`, không bao giờ là `localhost` (có thể là địa chỉ IPv6, nơi `web` không lắng nghe), không bao giờ
  là địa chỉ của NAS và không bao giờ là một địa chỉ Docker `172.x.x.x`, vì `web` chỉ lắng nghe trên địa chỉ loopback.
- Giao thức đích là HTTP, vì kết nối nằm trong NAS và `web` không có chứng chỉ.
- `X-Forwarded-Proto` cho PartFlow biết trình duyệt đã dùng HTTPS; `X-Forwarded-For` cho `web` biết địa chỉ của người
  dùng, mà các giới hạn tần suất (rate limit) đăng nhập của nó sử dụng.
- Việc nhập Work Order có thể mất tới 180 giây và ảnh có thể nặng 2 MiB, nên timeout ngắn hơn hoặc kích thước request nhỏ
  hơn sẽ làm chúng thất bại.
- Không bao giờ dùng các cổng DSM 5000 và 5001 cho PartFlow, và không bao giờ để phần quản trị DSM truy cập được qua
  `<HOSTNAME>`.

### 9.3 Kiểm tra HTTPS (phương án A)

Trên một máy trạm của mạng công ty:

1. Mở `https://<HOSTNAME>/api/health`. Trang hiển thị JSON có `"status":"ok"`.
2. Mở `https://<HOSTNAME>`. PartFlow mở ra và hiển thị nút **Set up PartFlow**, và trình duyệt hiển thị kết nối an toàn
   mà không có cảnh báo nào.

Cảnh báo chứng chỉ nghĩa là máy trạm này chưa tin cậy chứng chỉ gốc (§9.1 bước 3), hoặc tên trên thanh địa chỉ không
phải tên trong chứng chỉ. Một trang lỗi của DSM nghĩa là quy tắc reverse proxy không khớp với tên (§9.2).

## 10. Tạo Administrator đầu tiên và đăng ký các Scan Station

Hoàn thành mục này trước khi đưa địa chỉ cho bất kỳ ai: cho đến khi Administrator đầu tiên tồn tại, bất kỳ ai mở được
PartFlow và đọc được log của backend đều có thể tạo một Administrator.

1. Trên NAS, đọc mã thiết lập (setup token):

   ```sh
   $PF logs backend | grep "Setup token" | tail -n 1
   ```

   Dòng đó là một bản ghi log dạng JSON, có thông điệp là
   `PartFlow first-run setup is open: no administrator exists. Setup token: <token> — enter it in PartFlow …`.
   Chỉ sao chép token: tám nhóm, mỗi nhóm bốn ký tự, sau `Setup token:`. Mỗi lần `backend` khởi động lại đều ghi một
   token mới, vì vậy lệnh chỉ giữ dòng mới nhất.

2. Trong trình duyệt, mở `https://<HOSTNAME>`, bấm **Set up PartFlow**, dán token, và tạo Administrator đầu tiên: một
   tên đăng nhập, một tên hiển thị, một vai trò trong số các vai trò được đưa ra (vai trò nào cũng có thể quản trị
   PartFlow) và một mật khẩu. Sau đó thiết lập đóng vĩnh viễn.
3. Trên NAS, đưa `backend` về số worker đã cấu hình và kiểm tra nó healthy trở lại:

   ```sh
   $PF up -d backend
   $PF ps
   ```

4. Kiểm tra rằng cơ chế bảo vệ database đã có hiệu lực. Kết quả hiển thị dòng `"status": "pass"`:

   ```sh
   $PF run --rm --no-deps -T backend python -m app.cli reconcile --check h | grep -A2 '"id": "h"'
   ```

   Đây là kiểm tra bảo vệ (h) "Append-only guards and database-role privileges intact" (reconcile check (h)). Mọi trạng
   thái khác là một sự cố: không bắt đầu dùng PartFlow, và làm theo runbook §7 (Repairing check (h) findings).

5. Kiểm tra rằng việc tải lên đi qua được reverse proxy: trong **Administration → Users**, mở người dùng của chính bạn,
   gán một ảnh 1 đến 2 MB và lưu. Nếu bị từ chối vì quá lớn, xem runbook §8 (Common problems).
6. Đăng ký (enroll) từng thiết bị Scan Station: đăng nhập, mở **Administration → Scan Stations**, chọn **Devices…** của
   station đó, rồi **Enroll device…**, và nhập mã dùng một lần trên station trong vòng 15 phút.

PartFlow giờ đã chạy production. Hãy đưa mọi người địa chỉ `https://<HOSTNAME>`.

## 11. Sau khi đưa vào sử dụng: sao lưu và giám sát

Các tác vụ này được khuyến nghị và không bao giờ chặn việc sử dụng. Cho đến khi bản sao lưu hằng ngày chạy, các bản sao
lưu duy nhất là những bản mà `release.sh` tạo trước mỗi bản phát hành. Hãy làm theo thứ tự này.

### 11.1 Thông báo qua email

DSM gửi email khi một tác vụ theo lịch thất bại; đây là kênh cảnh báo duy nhất. Mở **Control Panel → Notification →
Email**, chọn **Enable email notifications**, nhập cài đặt máy chủ thư (SMTP) và người nhận, rồi bấm **Send a test
email**. Chỉ tiếp tục khi email thử đã đến.

### 11.2 Tác vụ sao lưu hằng ngày

1. Mở **Control Panel → Task Scheduler → Create → Scheduled Task → User-defined script**.
2. **General:** Task `PartFlow backup`, User `root`, chọn **Enabled**.
3. **Schedule:** **Run on the following days: Daily**, giờ chạy đầu tiên `02:00`, một lần mỗi ngày.
4. **Task Settings:** chọn **Send run details by email**, nhập địa chỉ của bạn, và chọn **Send run details only when
   the script terminates abnormally**. Trong **Run command** nhập:

   ```sh
   cd /volume1/docker/partflow/repo && deploy/production/backup.sh --kind daily --operator scheduler --keep-daily 14 --keep-weekly 8
   ```

5. Bấm **OK**. Chọn tác vụ và bấm **Run** để thử. Sau khoảng một phút, thư mục sao lưu có thêm một thư mục con có tên
   kết thúc bằng `-daily`:

   ```sh
   ls /volume1/docker/partflow/backups
   ```

`--keep-daily 14 --keep-weekly 8` giữ 14 bản sao lưu hằng ngày mới nhất cộng một bản mỗi tuần trong 8 tuần; hãy chọn
con số của riêng bạn. Với bản sao lưu hằng ngày, tối đa mất một ngày công việc. Hãy để 02:00 cách xa các thời điểm bạn
cài bản phát hành: một bản sao lưu đang chạy sẽ làm bản phát hành dừng trước khi thay đổi bất cứ điều gì (hãy chạy lại
bản phát hành khi bản sao lưu đã xong), và một bản sao lưu bắt đầu trong lúc đang phát hành bị từ chối (bạn nhận email
báo thất bại của nó; bản sao lưu của ngày hôm sau chạy bình thường, hoặc hãy tạo một bản thủ công, runbook §3). Các bản
sao lưu được giải thích trong runbook §3.

### 11.3 Bản sao mã hóa ngoài NAS

Một bản sao lưu chỉ nằm trên NAS sẽ mất cùng NAS. Hãy sao chép thư mục sao lưu ra nơi khác bằng **Hyper Backup**:

1. Cài **Hyper Backup** từ Package Center, mở nó và tạo một **Data backup task**.
2. Chọn đích nằm ngoài NAS này: một NAS khác, Synology C2 Storage hoặc một dịch vụ tương thích S3.
3. Ở phần nguồn, chọn thư mục `docker/partflow/backups`.
4. Bật **client-side encryption**. Giữ mật khẩu và tệp khóa ngoài NAS: không có chúng thì không đọc được các bản sao.
5. Lên lịch hằng ngày lúc `03:00`, sau bản sao lưu. Đặt việc xoay vòng phiên bản (version rotation) theo quyết định của
   bạn, bật kiểm tra tính toàn vẹn hằng tuần, và bật thông báo email khi thất bại.

Snapshot của volume NAS chỉ là lớp bảo vệ thứ hai: chúng không bao giờ thay thế các bản sao lưu này và bản sao của chúng
nằm ngoài NAS.

### 11.4 Diễn tập khôi phục (restore drill)

Một lần sau khi đưa vào sử dụng (go-live), rồi mỗi quý, hãy chứng minh rằng một bản sao lưu khôi phục được và đo xem mất
bao lâu. Dùng tên của bản sao lưu hằng ngày mới nhất (tên thư mục từ §11.2):

```sh
cd /volume1/docker/partflow/repo
deploy/production/restore-test.sh --backup <backup name> --operator "<your name>"
```

Buổi diễn tập (restore drill) khôi phục bản sao lưu vào một stack tạm thời riêng biệt, kiểm tra nó rồi xóa nó; nó không
bao giờ động đến database production. Mã thoát (exit code) 0 nghĩa là đạt. Tệp bằng chứng và các số đo thời gian của nó
được giải thích trong runbook §4.

### 11.5 Các tác vụ giám sát

Tạo thêm hai tác vụ như ở §11.2 (User `root`, chỉ gửi email khi script kết thúc bất thường):

| Task | Schedule | Run command |
| --- | --- | --- |
| `PartFlow check` | Hằng ngày, lần chạy đầu `00:00`, lặp lại mỗi 15 phút, lần chạy cuối `23:45` | `cd /volume1/docker/partflow/repo && HOME=/root deploy/production/check.sh --url https://<HOSTNAME> --quiet` |
| `PartFlow reconcile` | Hằng ngày lúc `04:00`, sau bản sao lưu | `cd /volume1/docker/partflow/repo && HOME=/root deploy/production/scheduled-reconcile.sh` |

`HOME=/root` cho các script biết nơi lưu trạng thái của chúng, phòng khi Task Scheduler không đặt biến này. Hãy điều
chỉnh lệnh check theo phương án HTTPS của bạn:

- **Phương án A với CA của công ty hoặc của DSM:** sao chép chứng chỉ gốc lên NAS, ví dụ vào
  `/volume1/docker/partflow/ca.pem`, và thêm `--cacert /volume1/docker/partflow/ca.pem`. Nếu bản thân NAS không phân
  giải được `<HOSTNAME>`, hãy thêm cả `--resolve-to 127.0.0.1`.
- **Phương án B (Cloudflare):** không thêm gì. Lệnh check đến PartFlow qua Cloudflare, giống một người dùng.

Thử tác vụ check: chọn nó, bấm **Run**, rồi đọc kết quả trên NAS. Dòng đầu hiển thị thời gian và mã thoát
(`exit 0`); mọi dòng khác bắt đầu bằng `PASS`, hoặc `SKIP` với một kiểm tra không áp dụng (dòng `archival_proposal`
luôn là `SKIP`):

```sh
cat /root/partflow-monitoring/last-check.txt
```

Một dòng `FAIL` nêu tên vấn đề; runbook §2 giải thích từng kiểm tra. `check.sh --quiet` không in gì và kết thúc bình
thường khi mọi thứ đều đạt, nên bạn chỉ nhận email khi có lỗi, và một lỗi không thay đổi được lặp lại mỗi 6 giờ.

### 11.6 Thử email cảnh báo

Cố ý làm cho kiểm tra thất bại một lần, để chứng minh email đến được bạn:

1. Sửa tác vụ `PartFlow check` và thêm `--only disk_backup --min-free-percent 100 --renotify-hours 0` vào cuối lệnh của
   nó. Mức tối thiểu 100 % dung lượng trống sẽ cố ý thất bại.
2. Bấm **Run**. Trong vài phút bạn nhận được email từ DSM báo rằng tác vụ kết thúc bất thường.
3. Sửa tác vụ một lần nữa và xóa ba tùy chọn đã thêm.

### 11.7 Firewall

Chỉ cho phép các mạng cần dùng PartFlow. Mở **Control Panel → Security → Firewall**, chọn **Enable firewall** và sửa các
quy tắc của profile đang dùng:

1. Trước tiên thêm các quy tắc cho phép (allow) cho việc quản trị của bạn: các cổng 5000 và 5001 (DSM) và 22 (SSH) từ
   các máy tính hoặc subnet bạn quản trị từ đó. Nếu thiếu, firewall sẽ khóa bạn khỏi DSM.
2. Thêm một quy tắc cho phép cổng 443 từ các subnet của công ty hoặc VPN (phương án A). Với phương án B, làm theo
   [`SYNOLOGY_PUBLIC_HTTPS.vi.md`](./SYNOLOGY_PUBLIC_HTTPS.vi.md) §12.
3. Đặt hành động cho mọi thứ còn lại là **Deny access**, áp dụng, và kiểm tra rằng bạn vẫn mở được DSM và PartFlow.
4. Kiểm tra rằng các container vẫn hoạt động sau firewall: chạy toàn bộ kiểm tra bằng tay (với cùng các tùy chọn như
   tác vụ theo lịch) và chỉ mong thấy các dòng `PASS` và `SKIP`.

   ```sh
   deploy/production/check.sh --url https://<HOSTNAME> --no-state
   ```

   Nếu một kiểm tra chỉ thất bại kể từ khi bật firewall, nghĩa là firewall chặn các mạng riêng của container. Liệt kê
   các subnet của chúng và thêm một quy tắc cho phép cho từng subnet:

   ```sh
   docker network inspect partflow-production_internal partflow-production_edge --format '{{.Name}} {{range .IPAM.Config}}{{.Subnet}}{{end}}'
   ```

## 12. Cài một bản phát hành mới

Mọi bản phát hành sau này được cài bằng `deploy/production/release.sh`. Script tạo một bản sao lưu đã xác minh, build các
image mới mà không động đến các image đang chạy, migrate database, chuyển PartFlow sang bản phát hành mới và kiểm tra nó.
Hãy chọn thời điểm không có việc nhập Work Order nào đang chạy và bản sao lưu hằng ngày (02:00) cũng không chạy.

```sh
cd /volume1/docker/partflow/repo
git fetch --tags
git checkout <NEW_TAG>
deploy/production/release.sh --release <NEW_TAG> --operator "<your name>" --approver "<your name>"
```

- Mã thoát 0 nghĩa là bản phát hành mới đang chạy và các kiểm tra tự động của nó đã đạt. Sau đó hãy kiểm tra bằng tay
  với dữ liệu thử: đăng nhập, quét ở một Scan Station, và lưu một thay đổi rồi đọc lại. Các trang đang mở hiển thị
  thông báo cập nhật và tự tải lại: Scan Station hoặc Production Board sau khoảng một phút, mọi trang khác khi không ai
  dùng nó trong 2 phút và trên trang không còn gì chưa lưu. Người đang làm việc trên một trang có thể bấm
  **Reload page** sớm hơn.
- Mọi mã thoát khác: đọc thông báo và làm theo runbook §5 (bảng mã thoát). Không có gì tự mở lại khi `release.sh` đã
  dừng việc ghi.
- `.env.production` không được git theo dõi, nên nó ở nguyên chỗ; `release.sh` ghi tag mới vào đó lúc chuyển. Các tác vụ
  theo lịch không cần thay đổi, vì chúng chạy trong cùng thư mục.
- Giữ các image của bản phát hành trước (không bao giờ dùng `docker image prune -a`) cho đến khi bạn chắc chắn sẽ không
  quay lui (rollback) (runbook §6).

## 13. Dung lượng và giới hạn tài nguyên

Một con số chung về bộ nhớ hay bộ xử lý không chứng minh được rằng NAS này đủ lớn. Hãy đo trong những tuần đầu sử dụng:
mức dùng bộ xử lý và bộ nhớ của mọi container PartFlow lúc nghỉ và lúc cao điểm (`docker stats --no-stream`), mức tăng
của database, thời gian và kích thước của các bản sao lưu, thời gian của một buổi diễn tập khôi phục, thời gian phản hồi
từ xưởng sản xuất, và hành vi khi NAS khởi động lại và khi mất điện.

Đặt các giới hạn trong `.env.production` theo các phép đo đó, rồi áp dụng vào lúc ít người dùng bằng
`$PF up -d db backend web`, lệnh này khởi động lại các container có giới hạn thay đổi. Trước đó, hãy lưu các lỗi gần đây
của backend bằng `deploy/production/check.sh --url https://<HOSTNAME> --only errors --no-state` (runbook §2). Trên NAS
không hỗ trợ giới hạn bộ xử lý (§3 bước 4), các giá trị `CPUS` giữ ở `0`.

| Setting | Default |
| --- | --- |
| `PARTFLOW_DB_MEMORY`, `PARTFLOW_DB_CPUS` | `1g`, `1.0` |
| `PARTFLOW_BACKEND_MEMORY`, `PARTFLOW_BACKEND_CPUS` | `1g`, `2.0` |
| `PARTFLOW_WEB_MEMORY`, `PARTFLOW_WEB_CPUS` | `128m`, `0.5` |
| `PARTFLOW_OPS_MEMORY`, `PARTFLOW_OPS_CPUS` (mỗi tác vụ chạy một lần) | `512m`, `1.0` |

Giữ đủ dung lượng đĩa trống cho database, các image của ít nhất một bản phát hành nữa, công việc migration tạm thời và
các bản sao lưu cục bộ. Một bản sao lưu cần dung lượng trống bằng hai lần bản sao lưu mới nhất cộng 1 GiB trong thư mục
sao lưu; một buổi diễn tập khôi phục cần năm lần bản sao lưu cộng 1 GiB trên vùng lưu trữ của Docker. Các kiểm tra đĩa
của `check.sh` thất bại khi dung lượng trống dưới 15 %: `disk_data` cho volume database, `disk_backup` cho thư mục sao
lưu và `disk_docker` cho vùng lưu trữ của Docker (image và log của container). `/root/partflow-monitoring/growth.tsv`
ghi kích thước database mỗi ngày một lần.

## Lịch sử thay đổi

| Ngày | Commit | Thay đổi |
| --- | --- | --- |
| 2026-11-12 | uncommitted | §12: các trang đang mở trong lúc phát hành nay tự tải lại (Scan Station hoặc Production Board sau khoảng một phút, mọi trang khác khi rảnh 2 phút và không còn gì chưa lưu). |
| 2026-11-10 | `e0e5056` | Viết lại thành một quy trình cài đặt tuyến tính từ NAS trống đến khi mọi người làm việc trong PartFlow, với mọi đường dẫn DSM và mọi lệnh theo thứ tự; bước HTTPS đưa ra phương án mạng LAN hoặc hướng dẫn Cloudflare; thêm kiểm tra kernel không hỗ trợ giới hạn bộ xử lý và kiểm tra firewall đối với các mạng của container; phần NAS staging tùy chọn và quy trình chuyển máy được loại bỏ (việc chuyển máy nay nằm trong runbook §6). |
| 2026-11-09 | `398c617` | Khôi phục các mục nhỏ mà bản viết lại đã bỏ sót: kiểm tra tình trạng trong smoke test của staging, bản sao ngoài NAS và kiểm thử khôi phục của các dump staging, đối soát staging, tên đầy đủ của reconcile check (h) và lệnh `revision` đầy đủ; bước 1 nay bao gồm cả NAS chưa từng cài staging. |
| 2026-11-09 | `b405c6e` | Viết lại thành nội dung hiện hành cho người đọc mới; bỏ các cổng pilot và release theo quyết định của chủ sở hữu. |
| 2026-10-09 | `03bd90c` | Thêm các tác vụ giám sát và ngưỡng đĩa của các kiểm tra máy chủ. |
| 2026-10-08 | `a330383` | Thêm các tác vụ sao lưu production, bản sao mã hóa ngoài NAS và buổi diễn tập khôi phục. |
| 2026-10-08 | `1116b07` | Thêm các database role production vào các bước cài đặt. |
| 2026-10-08 | `b820e7a` | Thêm quy trình phát hành với `release.sh` cho các bản phát hành sau. |
| 2026-10-08 | `d7e86a9` | Thêm production stack, các cài đặt reverse proxy của DSM và các phương án chứng chỉ. |
| 2026-10-06 | `702ab1c` | Thêm thiết lập Administrator lần đầu và đăng ký thiết bị Scan Station. |
| 2026-09-02 | `d2f68de` | Phiên bản đầu tiên của hướng dẫn Synology NAS cùng bản dịch tiếng Việt. |
