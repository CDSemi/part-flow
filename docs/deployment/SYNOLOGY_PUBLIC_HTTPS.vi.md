# Truy cập PartFlow từ Internet qua Cloudflare

> **Bản gốc:** [`SYNOLOGY_PUBLIC_HTTPS.md`](./SYNOLOGY_PUBLIC_HTTPS.md). Đồng bộ với bản tiếng Anh tại commit `8a7e2b5` cộng các thay đổi chưa
> commit ngày 2026-11-10. Bản tiếng Anh là nguồn chuẩn (source of truth); khi hai bản khác nhau, bản tiếng Anh có
> hiệu lực.

Hướng dẫn tùy chọn này giúp truy cập PartFlow trên NAS Synology của công ty từ bất cứ đâu, qua một địa chỉ như
`https://partflow.yourdomain.com`, thông qua Cloudflare. Thực hiện nó ở bước HTTPS của quá trình cài đặt
([`SYNOLOGY_NAS.vi.md`](./SYNOLOGY_NAS.vi.md) §9, phương án B), khi PartFlow đã phản hồi trên NAS (`SYNOLOGY_NAS.md` §8).
Hướng dẫn được viết cho người mới làm quen với DNS, Cloudflare, chuyển tiếp cổng (port forwarding) và chứng chỉ, nên
mỗi bước đều có đường dẫn menu, giá trị cần nhập và cách kiểm tra.

**Bạn cần có:**

- PartFlow đã được cài và phản hồi trên NAS: trên NAS, lệnh `curl -fsS http://127.0.0.1:18080/web-health` in ra `live`.
- Một tên miền có DNS do Cloudflare quản lý (trong hướng dẫn này là zone `yourdomain.com`) và một tài khoản Cloudflare
  có quyền thay đổi zone đó.
- Một router có thể chuyển tiếp một cổng và có thể cập nhật bản ghi DNS trên Cloudflare mỗi khi địa chỉ public của
  công ty thay đổi (DNS động, dynamic DNS, DDNS). Hướng dẫn này dùng UniFi gateway; các router khác có cùng chức năng
  nhưng tên gọi khác.
- Một địa chỉ IPv4 public trên kết nối Internet (WAN) của router (§2).

**Điều gì thay đổi về bảo mật.** Bất kỳ ai trên Internet cũng mở được trang đăng nhập PartFlow. PartFlow tự bảo vệ
bằng HTTPS, đăng nhập, giới hạn tần suất (rate limit) và khóa theo từng tài khoản (per-account lockout). Hướng dẫn này
mở một cổng trên router và không bao giờ chuyển tiếp các cổng quản trị DSM. Cổng đó dẫn tới web server của NAS ở cổng
443; bất kỳ ai biết địa chỉ public của công ty đều có thể kết nối tới nó mà không đi qua Cloudflare; §12 hướng dẫn cách
chỉ cho phép Cloudflare kết nối.

## 1. Kết nối hoạt động như thế nào

Trình duyệt luôn kết nối tới Cloudflare ở cổng HTTPS thông thường. Cloudflare kết nối tới địa chỉ public của công ty,
router chuyển tiếp kết nối đó tới NAS, và reverse proxy của DSM chuyển nó tới PartFlow. Cổng public mà Cloudflare dùng
phụ thuộc vào việc cổng 443 của router còn trống hay không:

```text
Case A: router port 443 is free             Case B: router port 443 is used by another service

Browser                                      Browser
  │ HTTPS 443                                  │ HTTPS 443
Cloudflare                                   Cloudflare, Origin Rule: connect on port 8443
  │ HTTPS 443                                  │ HTTPS 8443
Router: public 443 → NAS 443                 Router: public 8443 → NAS 443
  │                                            │
DSM reverse proxy on port 443                DSM reverse proxy on port 443
  │ HTTP                                       │ HTTP
127.0.0.1:18080 → PartFlow web               127.0.0.1:18080 → PartFlow web
```

Trong cả hai trường hợp, người dùng chỉ gõ `https://partflow.yourdomain.com`, không kèm cổng. Các giá trị ví dụ dưới
đây được dùng xuyên suốt; hãy thay bằng giá trị của bạn:

| Mục đích | Ví dụ |
| --- | --- |
| Cloudflare zone (tên miền của bạn) | `yourdomain.com` |
| Tên DDNS (theo dõi địa chỉ public) | `ddns.yourdomain.com` |
| Tên miền truy cập (host name) của PartFlow | `partflow.yourdomain.com` |
| Địa chỉ LAN của NAS | `192.168.0.11` |
| Cổng loopback của PartFlow (`PARTFLOW_HTTP_PORT`) | `18080` |
| Cổng public trong trường hợp B | `8443` |

Nên dùng một tên riêng như `partflow.yourdomain.com` thay vì tên miền gốc, vì dễ định tuyến, kiểm tra và bảo vệ độc
lập hơn.

## 2. Kiểm tra router có địa chỉ IPv4 public

1. Mở **UniFi Network → Settings → Internet** và mở kết nối WAN đang hoạt động.
2. Ghi lại địa chỉ IPv4 của nó.
3. Trên một máy tính trong mạng công ty, mở một trang web "what is my IP" và so sánh: hai địa chỉ phải giống nhau.

Địa chỉ WAN không được nằm trong một trong các dải private hoặc carrier-grade NAT sau: `10.0.0.0/8`, `172.16.0.0/12`,
`192.168.0.0/16`, `100.64.0.0/10`. Nếu nằm trong các dải này, router đang đứng sau một router khác hoặc sau việc chia sẻ
địa chỉ của nhà cung cấp; hãy xử lý với nhà cung cấp Internet trước, vì nếu không Cloudflare không thể kết nối tới NAS.

## 3. Cloudflare: tạo bản ghi DDNS

Bản ghi này luôn chứa địa chỉ public hiện tại của công ty. Bắt đầu nó ở giá trị `0.0.0.0` giúp sau này dễ thấy router
đã cập nhật nó.

1. Đăng nhập **Cloudflare dashboard** và chọn zone `yourdomain.com`.
2. Mở **DNS → Records** và bấm **Add record**.
3. Nhập **Type** `A`, **Name** `ddns`, **IPv4 address** `0.0.0.0`, **TTL** `Auto`.
4. Giữ **Proxy status** ở **DNS only**. Cloudflare có thể không cho bật proxy với `0.0.0.0`; không bao giờ bật proxy
   cho bản ghi này.
5. Bấm **Save**. Không tự gõ địa chỉ public thật: §5 bước 4 kiểm tra rằng router là bên ghi địa chỉ đó.

## 4. Cloudflare: tạo API token cho router

Router cần một token chỉ được thay đổi các bản ghi DNS của đúng zone này và không được làm gì khác.

1. Trong Cloudflare dashboard, mở **Manage account → Account API tokens** và bấm **Create Token**.
2. Nhập tên token, ví dụ `UniFi DDNS`, và trong **Permission policies** bấm vào template **Edit zone DNS**. Không chọn
   template có toàn quyền trên tài khoản.
3. Trên thẻ policy mà template vừa tạo, bấm biểu tượng cây bút (**Edit policy**):
   - đổi phạm vi tài nguyên từ tất cả các zone sang **Specified Domains** và chọn `yourdomain.com`;
   - mở **DNS & Zones** và chọn **Edit** ở dòng **DNS**, **Read** ở dòng **Zone**;
   - để trống mọi quyền khác. Phần này khi đó hiện khoảng `2/12`.
4. **Token expiration:** chọn **No expiration**, trừ khi quy định của công ty yêu cầu đổi token định kỳ.
5. **Client IP address filtering:** để trống. Token phải tiếp tục hoạt động sau khi nhà cung cấp đổi địa chỉ public của
   công ty.
6. Bấm **Review token**, kiểm tra chỉ có `yourdomain.com`, **DNS Edit** và **Zone Read** được liệt kê, rồi bấm
   **Create Token**.
7. Sao chép token ngay và lưu trong kho mật khẩu (password vault) của công ty. Xem token như mật khẩu: không bao giờ
   đưa nó vào git, ảnh chụp màn hình hay tài liệu.

## 5. Router: giữ bản ghi DDNS luôn được cập nhật

1. Mở **UniFi Network → Settings → Internet**, chọn kết nối WAN có địa chỉ public, tìm **Dynamic DNS** và bấm **Create
   New Dynamic DNS**.
2. Chọn **Service** `Cloudflare` và nhập **Hostname** `ddns.yourdomain.com`, **Zone Name** `yourdomain.com` và **API
   Token** (token ở §4). Nếu phiên bản UniFi của bạn hiện một form chung kiểu cũ, hãy dùng các giá trị Cloudflare mà
   UniFi hướng dẫn cho form đó; không bao giờ dùng Global API Key của toàn tài khoản.
3. Bấm **Save**.
4. Kiểm tra kết quả: trong Cloudflare **DNS → Records**, bản ghi `ddns.yourdomain.com` giờ hiện địa chỉ public ở §2
   thay cho `0.0.0.0`. Trên một máy trạm, lệnh sau phải trả về cùng địa chỉ đó:

   ```powershell
   nslookup ddns.yourdomain.com 1.1.1.1
   ```

Bản ghi vẫn giữ **DNS only**.

## 6. Cloudflare: tạo tên miền truy cập của PartFlow

1. Trong Cloudflare **DNS → Records**, bấm **Add record**.
2. Nhập **Type** `CNAME`, **Name** `partflow`, **Target** `ddns.yourdomain.com`, **TTL** `Auto`.
3. Đặt **Proxy status** là **Proxied** (đám mây màu cam). Điều này bắt buộc: khi đó trình duyệt kết nối tới
   Cloudflare chứ không tới công ty, và trong trường hợp B Cloudflare có thể đổi cổng. Địa chỉ public của công ty vẫn
   hiện qua bản ghi DDNS ở §3, vì vậy §12 giới hạn ai được kết nối tới nó.
4. Bấm **Save**.

Lệnh `nslookup partflow.yourdomain.com 1.1.1.1` giờ trả về các địa chỉ của Cloudflare, không phải địa chỉ của công ty.
Đó là điều bình thường.

## 7. Cloudflare: chứng chỉ origin và chế độ mã hóa

Cloudflare kiểm tra chứng chỉ của NAS. Hướng dẫn này dùng chứng chỉ Cloudflare Origin CA, loại mà Cloudflare tin cậy và
không cần mở cổng 80, khác với chức năng Let's Encrypt của DSM.

1. Trong Cloudflare, mở **SSL/TLS → Origin Server** và bấm **Create Certificate**.
2. Chọn **Generate private key and CSR with Cloudflare** và một loại khóa (RSA là lựa chọn đơn giản nhất; ECC cũng dùng
   được trên các phiên bản DSM hiện nay).
3. Trong **Hostnames**, chỉ giữ `partflow.yourdomain.com`; xóa wildcard và tên miền gốc nếu bạn không cần.
4. Chọn thời hạn hiệu lực (mặc định là 15 năm) và bấm **Create**.
5. Cloudflare hiện hai khối văn bản. Lưu **Origin Certificate** thành `partflow-origin.pem` và **Private Key** thành
   `partflow-origin.key`, mỗi khối đúng như hiển thị, gồm cả các dòng `-----BEGIN …-----` và `-----END …-----` và mọi
   ngắt dòng. Cloudflare có thể chỉ hiện khóa riêng một lần; hãy giữ cả hai tệp trong kho mật khẩu.
6. Ghi ngày hết hạn của chứng chỉ vào lịch của bạn: phần giám sát của PartFlow không thấy được ngày này (§13).
7. Mở **SSL/TLS → Overview** và đặt chế độ mã hóa là **Full (strict)**. Không bao giờ dùng **Flexible**, vì nó gửi kết
   nối từ Cloudflare tới NAS không mã hóa. Chế độ này áp dụng cho mọi tên được proxy của zone, nên các dịch vụ khác
   được proxy trong zone cũng cần chứng chỉ hợp lệ.
8. Tùy chọn: để chuyển người gõ `http://` sang `https://`, bật **SSL/TLS → Edge Certificates → Always Use HTTPS**.
   Tùy chọn này cũng áp dụng cho mọi tên được proxy của zone.

## 8. DSM: nhập chứng chỉ và tạo rule reverse proxy

1. Trong DSM, mở **Control Panel → Security → Certificate → Add → Add a new certificate**, chọn **Import certificate**
   và nhập mô tả, ví dụ `PartFlow - partflow.yourdomain.com`.
2. Chọn `partflow-origin.key` cho **Private key** và `partflow-origin.pem` cho **Certificate**. Để trống **Intermediate
   certificate**; chỉ khi DSM từ chối nếu thiếu nó, hãy dùng chứng chỉ gốc Cloudflare Origin CA khớp với loại khóa, lấy
   từ tài liệu Cloudflare Origin CA. Hoàn tất việc nhập; chứng chỉ xuất hiện trong danh sách.
3. Tạo rule reverse proxy đúng như [`SYNOLOGY_NAS.vi.md`](./SYNOLOGY_NAS.vi.md) §9.2 mô tả, với tên miền truy cập
   `partflow.yourdomain.com`. Ở bước cuối của rule, gán chứng chỉ `PartFlow - partflow.yourdomain.com` cho tên miền
   truy cập đó.

## 9. Router: chuyển tiếp cổng HTTPS tới NAS

NAS phải giữ nguyên địa chỉ LAN, vì rule chuyển tiếp trỏ tới địa chỉ đó: kiểm tra rằng việc giữ chỗ địa chỉ
(address reservation) ở `SYNOLOGY_NAS.md` §2 bước 2 đã được thực hiện.

Mở trang chuyển tiếp cổng của UniFi Network. Tùy phiên bản, đó là **Settings → Policy Engine → Port Forwarding**, hoặc
**Settings → Policy Table → Create New Policy → Port Forwarding**. Tạo một rule:

| Field | Trường hợp A (cổng 443 của router còn trống) | Trường hợp B (cổng 443 của router đã bị dùng) |
| --- | --- | --- |
| Name | `PartFlow HTTPS` | `PartFlow HTTPS origin` |
| WAN interface | kết nối WAN mà DDNS dùng | kết nối WAN mà DDNS dùng |
| Incoming port | `443` | `8443` |
| From | Any | Any |
| Forward IP | `192.168.0.11` | `192.168.0.11` |
| Forward port | `443` | `443` |
| Protocol | TCP | TCP |

Trong trường hợp B, giữ nguyên rule hiện có cho cổng 443: một cổng public chỉ dẫn tới được một địa chỉ LAN. DSM vẫn
lắng nghe ở cổng 443; `8443` chỉ tồn tại ở phía public. **From: Any** là thiết lập đơn giản nhất để bắt đầu; §12 giới hạn nó
chỉ cho Cloudflare khi mọi thứ đã hoạt động.

Không bao giờ chuyển tiếp một cổng public tới `5173`, `18080`, `8000`, `5432`, `5000` hoặc `5001`.

## 10. Cloudflare: Origin Rule (chỉ trường hợp B)

Bỏ qua phần này trong trường hợp A. Trong trường hợp B, Cloudflare phải kết nối tới cổng public `8443` thay vì `443`;
việc này chỉ hoạt động vì tên miền truy cập của PartFlow là **Proxied** (§6).

1. Trong Cloudflare dashboard, chọn `yourdomain.com` và mở **Rules → Origin Rules**. Bấm **Create rule** và đặt tên
   `PartFlow origin port`.
2. Ở điều kiện khớp, nhập **Field** `Hostname`, **Operator** `equals`, **Value** `partflow.yourdomain.com`.
3. Ở phần thiết lập origin, bật **Destination Port** và đặt **Rewrite to** là `8443`.
4. Bấm **Deploy**.

## 11. Kiểm tra từ bên ngoài mạng công ty

Kiểm tra từ một thiết bị thật sự ở bên ngoài, không phải từ máy tính trong mạng công ty:

1. Trên điện thoại, tắt Wi-Fi để dùng dữ liệu di động, rồi mở `https://partflow.yourdomain.com`.
2. Kiểm tra PartFlow mở được, trình duyệt báo kết nối an toàn, và địa chỉ không có cổng (`:8443`, `:18080` hay
   `:5173`).
3. Nếu thiết lập lần đầu (first-run setup) đã xong, hãy đăng nhập và mở vài màn hình cùng `/scan-station`; nếu chưa,
   nút **Set up PartFlow** sẽ hiện ra.

Trình duyệt hiện chứng chỉ public của chính Cloudflare, không phải chứng chỉ Origin cài trên DSM: chứng chỉ Origin do
Cloudflare kiểm tra. Khi mọi thứ hoạt động, quay lại [`SYNOLOGY_NAS.vi.md`](./SYNOLOGY_NAS.vi.md) §10.

## 12. Tường lửa và bảo mật

- Chỉ cổng được chuyển tiếp của trường hợp của bạn được mở trên router: public `443` (trường hợp A) hoặc `8443`
  (trường hợp B) tới `443` của NAS.
- Các cổng này luôn ở chế độ private và không bao giờ được chuyển tiếp: `5173` (development server), `18080` (loopback
  của PartFlow), `8000` (backend), `5432` (PostgreSQL), `5000` và `5001` (quản trị DSM). Trang quản trị DSM không bao
  giờ truy cập được qua `partflow.yourdomain.com`.
- **Chỉ cho phép Cloudflare (khuyến nghị).** Bản ghi DDNS công bố địa chỉ của công ty, nên bất kỳ ai cũng có thể kết
  nối trực tiếp tới cổng được chuyển tiếp, không qua Cloudflare, và truy cập mọi thứ mà web server của NAS trả lời ở
  cổng 443: rule của PartFlow, mọi rule reverse proxy khác và mọi dịch vụ web khác của NAS. Hãy bật tường lửa DSM
  ([`SYNOLOGY_NAS.vi.md`](./SYNOLOGY_NAS.vi.md) §11.7) và chỉ cho phép cổng 443 từ các dải IPv4 Cloudflare đã công bố
  (`https://www.cloudflare.com/ips/`), cộng thêm các subnet của công ty cho phương án A, thay vì cho phép từ mọi nơi.
  Rà soát danh sách đó hai lần mỗi năm: danh sách cũ có thể chặn PartFlow.
- Biết những gì khác đang trả lời ở cổng 443 của NAS: trong **Control Panel → Login Portal → Advanced → Reverse
  Proxy**, mọi rule khác cũng truy cập được theo cách tương tự; hãy xóa những rule bạn không cần.
- Giữ tên miền truy cập của PartFlow ở **Proxied**, chế độ mã hóa ở **Full (strict)**, API token chỉ giới hạn trong
  zone, NAS ở địa chỉ cố định, và cổng `18080` chỉ bind vào `127.0.0.1`.
- Tắt UPnP trên router trừ khi có nhu cầu khác đã được tài liệu hóa, và rà soát các rule tường lửa của router.
- Chỉ bật HSTS (trên rule DSM hoặc trong Cloudflare) sau khi HTTPS đã hoạt động ổn định một thời gian: khi đó trình
  duyệt từ chối HTTP thường cho tên đó trong thời gian dài, ngay cả khi sau này bạn cần dùng lại.

## 13. Các hạn chế đã biết

- **Mọi người phụ thuộc vào kết nối Internet.** Người trong công ty cũng truy cập PartFlow qua Cloudflare. Khi kết nối
  Internet của công ty bị mất, không ai mở được PartFlow, kể cả khu vực sản xuất. Nếu nhà máy phải tiếp tục làm việc
  khi không có Internet, hãy thiết lập thêm phương án A của `SYNOLOGY_NAS.md` §9 với một tên miền truy cập nội bộ thứ
  hai và chứng chỉ riêng của nó, như một rule reverse proxy thứ hai tới cùng `127.0.0.1:18080`; người trong công ty
  khi đó dùng địa chỉ này. Trình duyệt giữ việc đăng ký (enroll) Scan Station theo từng địa chỉ, nên một trạm đã đăng
  ký ở địa chỉ public phải được đăng ký lại để hoạt động ở địa chỉ nội bộ.
- **Cloudflare chờ câu trả lời tối đa 100 giây.** Trên các gói tiêu chuẩn của Cloudflare, một yêu cầu không nhận được
  câu trả lời trong 100 giây sẽ kết thúc bằng lỗi Cloudflare 524, bất kể các timeout của DSM đặt thế nào. Việc nhập
  một Work Order thường mất ít hơn một phút (lần nhập lớn nhất được phép mất khoảng nửa phút), nên điều này chỉ đáng
  quan tâm khi máy chủ chậm hoặc bận. Sau lỗi 524, kết quả là chưa rõ: hãy kiểm tra trong PartFlow xem lần nhập đã
  được lưu chưa trước khi làm lại.
- **Giới hạn tần suất đăng nhập được dùng chung.** `web` lấy địa chỉ cuối cùng của header `X-Forwarded-For` làm địa chỉ
  của người dùng. DSM nối thêm địa chỉ mà nó nhận kết nối từ đó, và phía sau Cloudflare đó là một máy chủ Cloudflare.
  Giới hạn đăng nhập (10 mỗi phút, burst 5) khi đó áp dụng theo từng địa chỉ máy chủ Cloudflare, và log hiện các địa
  chỉ Cloudflare. Khi nhiều người đăng nhập cùng lúc, một số người có thể thấy "Too many attempts from this computer";
  họ chờ một phút rồi thử lại. Khóa theo từng tài khoản không bị ảnh hưởng. Không bao giờ cho DSM tin một header do
  client cung cấp như `CF-Connecting-IP` để thay thế, trừ khi NAS chỉ cho phép các địa chỉ của Cloudflare (§12): nếu
  không, bất kỳ ai cũng có thể giả địa chỉ và vượt qua các giới hạn.
- **Chứng chỉ Origin không được giám sát.** `check.sh` chỉ thấy chứng chỉ public của Cloudflare, do Cloudflare tự gia
  hạn. Hãy tự gia hạn chứng chỉ Origin trước ngày bạn đã ghi ở §7. Vì lý do tương tự, không bao giờ thêm
  `--resolve-to 127.0.0.1` vào `check.sh` trong thiết kế này: chỉ Cloudflare mới tin chứng chỉ Origin.

## 14. Xử lý sự cố

| Triệu chứng | Kiểm tra, theo thứ tự này |
| --- | --- |
| Lỗi Cloudflare 521 (web server is down) hoặc 522 (connection timed out) | `web` ở trạng thái healthy trong `$PF ps`; `curl -fsS http://127.0.0.1:18080/web-health` in ra `live` trên NAS; rule DSM tồn tại với nguồn HTTPS 443; tường lửa DSM cho phép kết nối; rule của router dùng đúng cổng public và chuyển tiếp tới `NAS_IP:443`; trong trường hợp B, Origin Rule dùng cùng cổng public với rule của router. |
| Lỗi Cloudflare 525 (SSL handshake failed) | Giao thức nguồn của rule DSM là HTTPS ở cổng 443; chứng chỉ Origin đã được nhập cùng khóa riêng khớp với nó; router chuyển tiếp tới cổng 443 của NAS; trong trường hợp B, cổng của Origin Rule. |
| Lỗi Cloudflare 526 (invalid SSL certificate) | Chứng chỉ Origin chưa hết hạn, có tên `partflow.yourdomain.com`, và được gán cho tên miền truy cập đó trong DSM (`SYNOLOGY_NAS.md` §9.2 bước 6). |
| PartFlow mở được, nhưng đăng nhập không giữ được hoặc phiên bị mất | Các custom header của rule DSM đúng là `X-Forwarded-For` = `$proxy_add_x_forwarded_for` và `X-Forwarded-Proto` = `$scheme`, và địa chỉ là `https://partflow.yourdomain.com`. Cookie đăng nhập của PartFlow cần biết trình duyệt đã dùng HTTPS. |
| Lỗi Cloudflare 524 (a timeout occurred) | Máy chủ trả lời mất hơn 100 giây (§13). Kiểm tra xem thay đổi đã được lưu chưa trước khi làm lại; nếu xảy ra thường xuyên, kiểm tra tải của NAS (`docker stats --no-stream`). |
| Lỗi Cloudflare 523 (origin is unreachable), hoặc bản ghi DDNS vẫn là `0.0.0.0` hoặc giữ địa chỉ cũ | Router có IPv4 public (§2); token đang hoạt động, giới hạn đúng zone, có **DNS Edit** và **Zone Read** và không lọc IP; tên host và tên zone trong router đúng; bản ghi là **DNS only**. Để thử lại, đặt bản ghi về `0.0.0.0`, lưu lại cấu hình DDNS của router và theo dõi bản ghi thay đổi. |
| "Blocked request" (HTTP 403), hoặc lỗi 404, ở cổng `5173` | Đó là development server, không phải production. Production không bao giờ dùng cổng `5173`; hãy dùng đường reverse proxy của hướng dẫn này. |
| Let's Encrypt của DSM thất bại | Không cần: thiết kế này dùng chứng chỉ Cloudflare Origin, và cổng 80 vẫn đóng. |
| Chỉ một trình duyệt báo "Not secure" trong khi các trình duyệt khác hoạt động | Trình duyệt đó còn nhớ một ngoại lệ cũ từ lúc xử lý sự cố. Thử cửa sổ riêng tư hoặc đặt lại quyền của trang trước khi thay đổi máy chủ. |
| "Too many attempts from this computer" khi nhiều người đăng nhập cùng lúc | Giới hạn tần suất dùng chung ở §13; chờ một phút. |

## Lịch sử thay đổi

| Ngày | Commit | Thay đổi |
| --- | --- | --- |
| 2026-11-10 | uncommitted | Cấu trúc lại thành một lộ trình đánh số từ bước kiểm tra địa chỉ public đến bước kiểm tra từ bên ngoài; rule reverse proxy của DSM nay lấy từ hướng dẫn NAS thay vì lặp lại; các checklist theo từng nhà cung cấp được gộp vào các bước; thêm các hạn chế đã biết (phụ thuộc Internet, giới hạn 100 giây của Cloudflare, giới hạn tần suất dùng chung, chứng chỉ Origin không được giám sát), việc cổng được chuyển tiếp bị lộ cùng khuyến nghị chỉ cho phép Cloudflare, các lỗi Cloudflare 522 đến 524, và tùy chọn chuyển hướng HTTPS. |
| 2026-11-10 | `8a7e2b5` | Phiên bản đầu: Cloudflare DDNS với API token giới hạn phạm vi, chứng chỉ Origin CA và Full (strict), chuyển tiếp cổng UniFi cho trường hợp cổng WAN 443 còn trống hoặc đã bị dùng, reverse proxy DSM, xử lý sự cố và kiểm tra đầu-cuối. |
