# HTTPS công khai cho PartFlow trên Synology
## Cloudflare + UniFi Gateway + Synology DSM Reverse Proxy

> **Bản tiếng Anh là nguồn chuẩn (source of truth).**  
> Bản tiếng Việt này là mirror của [`SYNOLOGY_PUBLIC_HTTPS.md`](./SYNOLOGY_PUBLIC_HTTPS.md).

Tài liệu này hướng dẫn **từ đầu đến cuối** cách đưa **PartFlow production đang chạy trong Synology Container Manager** ra Internet bằng một URL HTTPS sạch như:

```text
https://partflow.yourdomain.com
```

Tài liệu được viết cho người mới làm quen với DDNS, Cloudflare proxy, port forwarding, certificate và DSM reverse proxy. Vì vậy mỗi phần đều ghi rõ đường dẫn menu, nút cần bấm, field cần điền, giá trị cần nhập và cách kiểm tra kết quả.

Kiến trúc khuyến nghị dùng:

- **Cloudflare** cho authoritative DNS, proxy, TLS ở edge, Origin CA và Origin Rules khi cần.
- **UniFi Gateway / UDM** cho Cloudflare DDNS và WAN port forwarding.
- **Synology DSM** để nhận HTTPS và reverse proxy vào PartFlow.
- **Synology Container Manager** để chạy PartFlow production containers.

Tài liệu bao gồm cả hai trường hợp:

1. Public WAN TCP 443 đang rảnh.
2. Public WAN TCP 443 đã được một service khác sử dụng.

---

# 1. Các giá trị ví dụ dùng trong tài liệu

Thay các giá trị này bằng môi trường thực tế của bạn.

| Mục đích | Ví dụ |
| --- | --- |
| Cloudflare zone | `yourdomain.com` |
| DDNS hostname | `ddns.yourdomain.com` |
| PartFlow public hostname | `partflow.yourdomain.com` |
| LAN IP của Synology NAS | `192.168.0.11` |
| PartFlow production host port | `18080` |
| Public WAN port thay thế khi 443 đã bị dùng | `8443` |

Tương đương:

```text
ZONE               = yourdomain.com
DDNS_HOST          = ddns.yourdomain.com
APP_HOST           = partflow.yourdomain.com
NAS_IP             = 192.168.0.11
PARTFLOW_HTTP_PORT = 18080
ALT_WAN_PORT       = 8443
```

Nên dùng một hostname riêng như `partflow.yourdomain.com` thay vì đặt PartFlow trực tiếp ở zone apex vì dễ route, troubleshoot, thay thế và bảo vệ độc lập hơn.

---

# 2. Cấu trúc PartFlow production mong đợi

PartFlow production chỉ publish `web` container trên loopback của NAS:

```yaml
ports:
  - "127.0.0.1:${PARTFLOW_HTTP_PORT}:80"
```

Giá trị production mặc định:

```text
PARTFLOW_HTTP_PORT=18080
```

Đường local đúng:

```text
DSM host 127.0.0.1:18080
        ↓
PartFlow web container :80
        ↓
PartFlow backend
        ↓
PostgreSQL
```

Không dùng port `5173` cho production. Port `5173` thuộc Vite development/staging.

Docker network mong đợi:

```text
partflow-production_edge
  ├─ web
  └─ backend

partflow-production_internal
  ├─ backend
  └─ db
```

Không thay đổi cách tách network này chỉ để truy cập từ Internet.

---

# 3. Đường traffic cuối cùng

## 3.1 Trường hợp A — Public WAN TCP 443 đang rảnh

```text
Browser
  │ HTTPS :443
  ▼
Cloudflare
  │ HTTPS :443
  ▼
Public WAN IP :443
  │
  ▼
UniFi Gateway
  │ WAN 443 → NAS 443
  ▼
Synology DSM Reverse Proxy
  │ HTTPS partflow.yourdomain.com:443
  ▼
HTTP 127.0.0.1:18080
  │
  ▼
PartFlow
```

## 3.2 Trường hợp B — Public WAN TCP 443 đã được dùng

Giữ nguyên service hiện đang dùng WAN 443.

Cloudflare vẫn nhận browser ở HTTPS port 443 bình thường, nhưng Origin Rule đổi port mà Cloudflare dùng để kết nối về public WAN IP.

```text
Browser
  │ HTTPS :443
  ▼
Cloudflare
  │ Origin Rule: destination port → 8443
  ▼
Public WAN IP :8443
  │
  ▼
UniFi Gateway
  │ WAN 8443 → NAS 443
  ▼
Synology DSM Reverse Proxy :443
  │
  ▼
HTTP 127.0.0.1:18080
  │
  ▼
PartFlow
```

Người dùng vẫn chỉ nhập:

```text
https://partflow.yourdomain.com
```

Browser không cần thấy `:8443`.

---

# Phần A — Cloudflare DDNS

# 4. Xác nhận UniFi có public WAN IPv4

Trước khi tạo DDNS hoặc Port Forward:

1. Mở **UniFi Network**.
2. Vào:
   ```text
   Settings → Internet
   ```
3. Mở WAN đang kết nối Internet.
4. Ghi lại WAN IPv4 address.
5. Mở một dịch vụ "what is my IP" từ browser để xem public IPv4.
6. Xác nhận hai IP giống nhau.

WAN IP của UniFi không nên nằm trong các dải private/CGNAT sau:

```text
10.0.0.0/8
172.16.0.0/12
192.168.0.0/16
100.64.0.0/10
```

Nếu nằm trong các dải này, cần xử lý double NAT hoặc CGNAT trước.

---

# 5. Tạo A record ban đầu cho DDNS trong Cloudflare

Nên dùng `0.0.0.0` làm giá trị ban đầu. Cách này giúp kiểm tra DDNS rất trực quan: sau khi UniFi chạy thành công, record phải đổi từ `0.0.0.0` sang public WAN IPv4 thật.

1. Đăng nhập **Cloudflare Dashboard**.
2. Chọn zone:
   ```text
   yourdomain.com
   ```
3. Menu bên trái chọn:
   ```text
   DNS → Records
   ```
4. Bấm:
   ```text
   Add record
   ```
5. Điền:
   ```text
   Type:          A
   Name:          ddns
   IPv4 address:  0.0.0.0
   TTL:           Auto
   ```
6. Để record ở trạng thái **DNS only**. Với placeholder như `0.0.0.0`, UI Cloudflare hiện tại có thể tự làm tùy chọn Proxy không khả dụng; không cần cố bật Proxied cho record DDNS này.
7. Bấm:
   ```text
   Save
   ```

Record ban đầu về logic là:

```text
ddns.yourdomain.com → 0.0.0.0
```

Không tự tay đổi `0.0.0.0` sang WAN IP. Bước tiếp theo dùng chính thay đổi này để xác nhận UniFi đã update DDNS thật sự.

---

# 6. Tạo Cloudflare API token cho UniFi DDNS

Dùng **Account API token** và chỉ cấp quyền tối thiểu mà UniFi/Inadyn cần.

## 6.1 Mở trang Account API token

1. Trong Cloudflare Dashboard, mở:
   ```text
   Manage account
   ```
2. Chọn:
   ```text
   Account API tokens
   ```
3. Bấm:
   ```text
   Create Token
   ```

## 6.2 Chọn template có sẵn `Edit zone DNS`

Trong trang **Create a token**:

1. Nhập tên dễ nhận biết, ví dụ:
   ```text
   UniFi DDNS
   ```
2. Trong **Permission policies**, tìm template:
   ```text
   Edit zone DNS
   ```
3. Bấm vào **Edit zone DNS**.

Template này đã tạo sẵn quyền DNS Write. Không chọn `Full account and zone access`, `Write all resources` hoặc template có quyền quá rộng.

## 6.3 Sửa policy của template

Sau khi chọn template:

1. Trong **Permission policies**, tìm policy card vừa được tạo.
2. Bấm **biểu tượng cây bút** bên phải:
   ```text
   Edit policy
   ```
3. Ở đầu policy editor, đổi phạm vi từ tất cả domain/zone sang:
   ```text
   Specified Domains
   ```
4. Ở dropdown bên cạnh, chọn:
   ```text
   yourdomain.com
   ```
5. Xổ nhóm:
   ```text
   DNS & Zones
   ```
6. Dòng **DNS**, chọn:
   ```text
   Edit
   ```
7. Dòng **Zone**, chọn:
   ```text
   Read
   ```
8. Các quyền khác trong `DNS & Zones` để trống.

Permission cuối cùng phải đúng:

```text
DNS  → Edit
Zone → Read
```

UI nên hiện khoảng:

```text
DNS & Zones   2/12
```

## 6.4 Token expiration

Trong **Token expiration**:

1. Chọn:
   ```text
   No expiration
   ```
   trừ khi công ty bắt buộc rotate token định kỳ.

## 6.5 Client IP address filtering

Trong **Client IP address filtering**:

1. Để field IP/CIDR trống.
2. Không giới hạn token theo WAN IP hiện tại.

DDNS phải tiếp tục hoạt động sau khi ISP đổi WAN IP.

## 6.6 Review và tạo token

1. Bấm:
   ```text
   Review token
   ```
2. Kiểm tra:
   - chỉ có `yourdomain.com`;
   - có `DNS Edit`;
   - có `Zone Read`;
   - không có quyền dư.
3. Bấm:
   ```text
   Create Token
   ```
4. Copy token secret ngay và lưu ở nơi an toàn.

Xem token như password. Không commit vào Git, không đưa vào screenshot hay tài liệu.

---

# 7. Cấu hình Cloudflare DDNS trong UniFi

## 7.1 Mở cấu hình WAN

1. Mở **UniFi Network**.
2. Vào:
   ```text
   Settings → Internet
   ```
3. Chọn WAN đang có Internet.
4. Tìm:
   ```text
   Dynamic DNS
   ```
5. Bấm:
   ```text
   Create New Dynamic DNS
   ```

## 7.2 Điền thông tin Cloudflare

Chọn:

```text
Service: Cloudflare
```

Với form Cloudflare-specific hiện tại:

```text
Hostname:  ddns.yourdomain.com
Zone Name: yourdomain.com
API Token: <token tạo ở Mục 6>
```

Nếu build UniFi của bạn hiện form generic kiểu Inadyn cũ, dùng các label mà build đó cung cấp và mapping Cloudflare/Inadyn tương ứng. Khi có API-token native thì không dùng Global API Key.

Bấm:

```text
Save
```

## 7.3 Xác minh UniFi thật sự update DDNS

Quay lại:

```text
Cloudflare Dashboard
→ yourdomain.com
→ DNS
→ Records
```

Tìm:

```text
ddns.yourdomain.com
```

Nếu DDNS chạy đúng, nội dung phải đổi từ:

```text
0.0.0.0
```

sang public WAN IPv4 thật.

Đây là cách kiểm tra trực quan dễ nhất rằng UniFi đã thực sự update record.

Sau đó từ workstation chạy:

```powershell
nslookup ddns.yourdomain.com 1.1.1.1
```

IPv4 trả về phải giống WAN IPv4 trên UniFi.

Giữ DDNS record ở trạng thái **DNS only**.

---

# Phần B — Tạo public hostname cho PartFlow

# 8. Tạo `partflow.yourdomain.com` trong Cloudflare DNS

Thực hiện đầy đủ từng bước:

1. Đăng nhập **Cloudflare Dashboard**.
2. Chọn:
   ```text
   yourdomain.com
   ```
3. Menu bên trái bấm:
   ```text
   DNS
   ```
4. Chọn:
   ```text
   Records
   ```
5. Bấm:
   ```text
   Add record
   ```
6. Điền:
   ```text
   Type:    CNAME
   Name:    partflow
   Target:  ddns.yourdomain.com
   TTL:     Auto
   ```

## 8.1 Chọn Proxy status

Cấu hình cuối cùng của tài liệu này dùng:

```text
Proxy status: Proxied
```

Cloud icon phải màu cam.

Nếu public WAN TCP 443 đã bị service khác dùng và bạn cần Cloudflare Origin Rule thì **bắt buộc** PartFlow record phải Proxied.

Nếu WAN 443 đang rảnh, trong lúc troubleshoot cấp thấp có thể tạm dùng DNS only, nhưng cấu hình cuối cùng vẫn khuyến nghị Proxied.

7. Bấm:
   ```text
   Save
   ```

Quan hệ DNS cuối:

```text
ddns.yourdomain.com
    A → public WAN IPv4 hiện tại
    DNS only

partflow.yourdomain.com
    CNAME → ddns.yourdomain.com
    Proxied
```

Khi `partflow.yourdomain.com` đang Proxied, `nslookup` thường trả về Cloudflare IP thay vì WAN IP thật. Đây là đúng.

---

# Phần C — TLS certificate cho NAS qua Cloudflare

# 9. Tạo Cloudflare Origin CA certificate

Tài liệu này khuyến nghị **Cloudflare Origin CA** thay cho DSM Let's Encrypt vì PartFlow chủ đích luôn đi qua Cloudflare và Origin CA không cần mở WAN port 80 để HTTP-01 validation.

## 9.1 Mở Origin Server

1. Trong Cloudflare Dashboard chọn:
   ```text
   yourdomain.com
   ```
2. Mở:
   ```text
   SSL/TLS
   ```
3. Chọn:
   ```text
   Origin Server
   ```
4. Bấm:
   ```text
   Create Certificate
   ```

## 9.2 Cấu hình certificate

1. Chọn:
   ```text
   Generate private key and CSR with Cloudflare
   ```
2. Chọn key type có sẵn. RSA là lựa chọn dễ tương thích; ECC cũng dùng được trên DSM hiện đại.
3. Trong **Hostnames**, nhập:
   ```text
   partflow.yourdomain.com
   ```
4. Xóa wildcard hoặc apex hostname không cần thiết.
5. Chọn thời hạn certificate phù hợp policy của công ty.
6. Bấm:
   ```text
   Create
   ```

## 9.3 Lưu certificate và private key

Cloudflare hiện hai block:

```text
Origin Certificate
Private Key
```

Lưu ngay thành file, ví dụ:

```text
partflow-origin.pem
partflow-origin.key
```

Khi lưu:

- giữ nguyên dòng `-----BEGIN ...-----` và `-----END ...-----`;
- giữ nguyên line break;
- không thêm space/text;
- bảo vệ private key cẩn thận.

Private key có thể chỉ được Cloudflare hiển thị một lần.

---

# 10. Đặt Cloudflare SSL/TLS = `Full (strict)`

1. Trong Cloudflare Dashboard chọn:
   ```text
   yourdomain.com
   ```
2. Mở:
   ```text
   SSL/TLS → Overview
   ```
3. Tìm **SSL/TLS encryption mode**.
4. Chọn:
   ```text
   Full (strict)
   ```

Không dùng:

```text
Flexible
```

Đường mã hóa đúng:

```text
Browser ─HTTPS→ Cloudflare ─HTTPS→ Synology DSM
```

---

# Phần D — Xác minh PartFlow production trên Synology

# 11. Kiểm tra PartFlow web container trong Container Manager

1. Đăng nhập DSM.
2. Mở:
   ```text
   Container Manager
   ```
3. Chọn:
   ```text
   Container
   ```
4. Mở:
   ```text
   partflow-production-web-1
   ```
5. Xác nhận status:
   ```text
   Healthy
   ```
6. Trong **General**, tìm phần **Port**.
7. Xác nhận mapping tương ứng:
   ```text
   Container: 80/tcp
   Host:      18080
   ```

Production Compose còn bind host port này vào:

```text
127.0.0.1
```

nên mapping thực tế là:

```text
127.0.0.1:18080 → container:80
```

Không đổi thành `0.0.0.0` hoặc NAS LAN IP.

---

# 12. Test PartFlow local trên NAS

Nếu NAS đã bật SSH:

1. SSH vào NAS.
2. Chạy:
   ```bash
   docker ps --filter name=partflow-production-web-1
   ```
3. Xác nhận `PORTS` có:
   ```text
   127.0.0.1:18080->80/tcp
   ```
4. Test health:
   ```bash
   curl -fsS http://127.0.0.1:18080/web-health
   ```
5. Kết quả mong đợi:
   ```text
   live
   ```
6. Test root:
   ```bash
   curl -I http://127.0.0.1:18080/
   ```

Có HTTP response thành công nghĩa là DSM đã có destination local đúng để reverse proxy.

Việc máy khác trong LAN không truy cập được:

```text
http://192.168.0.11:18080
```

là bình thường vì PartFlow production chỉ bind vào loopback.

---

# Phần E — Import Origin certificate vào DSM

# 13. Import Cloudflare Origin CA certificate

1. Trong DSM mở:
   ```text
   Control Panel
   ```
2. Vào:
   ```text
   Security
   ```
3. Chọn tab:
   ```text
   Certificate
   ```
4. Bấm:
   ```text
   Add
   ```
5. Chọn:
   ```text
   Add a new certificate
   ```
6. Chọn:
   ```text
   Import certificate
   ```
7. Description nhập:
   ```text
   PartFlow - partflow.yourdomain.com
   ```
8. **Private key** upload:
   ```text
   partflow-origin.key
   ```
9. **Certificate** upload:
   ```text
   partflow-origin.pem
   ```
10. Nếu DSM bắt buộc intermediate/certificate-chain file, dùng Cloudflare Origin CA root tương ứng. Nếu field optional và DSM import được khi để trống thì để trống.
11. Hoàn tất import.

Certificate mới phải xuất hiện trong danh sách certificate của DSM.

---

# Phần F — DSM Reverse Proxy

# 14. Tạo PartFlow Reverse Proxy rule

1. Trong DSM mở:
   ```text
   Control Panel
   ```
2. Mở:
   ```text
   Login Portal
   ```
3. Chọn tab:
   ```text
   Advanced
   ```
4. Mở:
   ```text
   Reverse Proxy
   ```
5. Bấm:
   ```text
   Create
   ```

## 14.1 General tab — Source

Điền:

```text
Reverse Proxy Name: PartFlow

Source
Protocol: HTTPS
Hostname: partflow.yourdomain.com
Port:     443
```

Trong lúc setup ban đầu, để HSTS tắt. Chỉ bật sau khi HTTPS đã ổn định.

## 14.2 General tab — Destination

Điền:

```text
Destination
Protocol: HTTP
Hostname: 127.0.0.1
Port:     18080
```

Quan trọng:

- dùng `127.0.0.1`;
- không dùng `localhost`;
- không dùng NAS LAN IP;
- không dùng Docker IP `172.x.x.x`;
- không dùng port `5173`;
- Destination Protocol là `HTTP`, không phải `HTTPS`.

Đường local đúng:

```text
DSM Reverse Proxy
      ↓ HTTP
127.0.0.1:18080
      ↓
PartFlow web container :80
```

Chưa Save; cấu hình tiếp Custom Header và Advanced Settings.

---

# 15. Cấu hình tab DSM Custom Header

Làm đúng theo UI DSM:

1. Khi đang edit rule **PartFlow**, bấm tab:
   ```text
   Custom Header
   ```
2. Bấm dropdown:
   ```text
   Create ▼
   ```
3. Chọn:
   ```text
   Create
   ```
   Không chọn `WebSocket` cho PartFlow production thông thường.
4. Trong row mới nhập:
   ```text
   Header Name: X-Forwarded-For
   Value:       $proxy_add_x_forwarded_for
   ```
5. Bấm lại:
   ```text
   Create ▼
   ```
6. Chọn:
   ```text
   Create
   ```
7. Row thứ hai nhập:
   ```text
   Header Name: X-Forwarded-Proto
   Value:       $scheme
   ```

Bảng cuối cùng phải có đúng hai dòng:

```text
X-Forwarded-For    $proxy_add_x_forwarded_for
X-Forwarded-Proto  $scheme
```

Nếu DSM đã có một dòng giống hệt thì không tạo duplicate.

`X-Forwarded-Proto` rất quan trọng vì browser dùng HTTPS nhưng DSM kết nối local tới PartFlow bằng HTTP.

---

# 16. Cấu hình DSM Advanced Settings

1. Trong cùng Reverse Proxy rule, bấm:
   ```text
   Advanced Settings
   ```
2. Đặt **proxy read timeout**:
   ```text
   300 seconds
   ```
3. Đặt **proxy send timeout**:
   ```text
   300 seconds
   ```
4. Nếu DSM build của bạn có field giới hạn request body theo rule, đặt ít nhất:
   ```text
   5 MiB
   ```
5. Các setting khác giữ default trừ khi deployment docs yêu cầu khác.
6. Bấm:
   ```text
   Save
   ```

---

# 17. Gán Cloudflare Origin certificate cho Reverse Proxy hostname

Import certificate chưa đủ; phải gán certificate đúng hostname/service.

1. Mở:
   ```text
   Control Panel → Security → Certificate
   ```
2. Bấm:
   ```text
   Settings
   ```
3. Tìm service/hostname:
   ```text
   partflow.yourdomain.com
   ```
4. Ở certificate dropdown, chọn:
   ```text
   PartFlow - partflow.yourdomain.com
   ```
5. Bấm:
   ```text
   OK / Apply
   ```

Không để PartFlow phụ thuộc vào certificate nào tình cờ đang là DSM default certificate.

---

# Phần G — UniFi Port Forwarding

# 18. Đặt LAN IP cố định cho NAS

Trước khi Port Forward, NAS phải giữ một LAN IP cố định.

Ví dụ:

```text
192.168.0.11
```

Trong UniFi dùng **Fixed IP / DHCP reservation**, hoặc dùng static IP trên NAS được quản lý cẩn thận.

Xác nhận NAS vẫn truy cập được ở IP này trước khi tiếp tục.

---

# 19. Trường hợp A — WAN TCP 443 đang rảnh

Nếu public WAN TCP 443 chưa bị service khác dùng:

1. Mở **UniFi Network**.
2. Vào trang Port Forwarding. Tùy phiên bản Network:
   ```text
   Settings → Policy Engine → Port Forwarding
   ```
   hoặc:
   ```text
   Settings → Policy Table → Create New Policy → Port Forwarding
   ```
3. Bấm:
   ```text
   Create / New Port Forward
   ```
4. Điền:
   ```text
   Name:          PartFlow HTTPS
   WAN Interface: WAN đang dùng cho DDNS
   Incoming Port: 443
   From:          Any
   Forward IP:    192.168.0.11
   Forward Port:  443
   Protocol:      TCP
   ```
5. Save/apply.

Không forward trực tiếp WAN vào:

```text
5173
18080
8000
5432
5000
5001
```

Trường hợp A không cần Cloudflare Origin Rule đổi port.

---

# 20. Trường hợp B — WAN TCP 443 đã được service khác dùng

UniFi không thể gửi cùng public WAN TCP 443 tới hai LAN destination khác nhau.

Giữ nguyên rule 443 hiện có.

Tạo origin port riêng cho PartFlow:

1. Mở **UniFi Network**.
2. Vào:
   ```text
   Settings → Policy Engine → Port Forwarding
   ```
   hoặc trang **Policy Table → Port Forwarding** tương đương.
3. Bấm:
   ```text
   Create / New Port Forward
   ```
4. Điền:
   ```text
   Name:          PartFlow HTTPS Origin
   WAN Interface: WAN đang dùng cho DDNS
   Incoming Port: 8443
   From:          Any
   Forward IP:    192.168.0.11
   Forward Port:  443
   Protocol:      TCP
   ```
5. Save/apply.

NAT cuối cùng:

```text
Public WAN :8443 → 192.168.0.11:443
```

DSM vẫn nhận HTTPS bình thường ở port 443. Port 8443 chỉ tồn tại ở phía public WAN.

---

# Phần H — Cloudflare Origin Rule khi WAN 443 đã bị dùng

# 21. Tạo Origin Rule

Phần này chỉ cần cho **Trường hợp B**.

Trước khi làm, xác nhận:

```text
partflow.yourdomain.com
```

đang **Proxied** trên Cloudflare.

Sau đó:

1. Đăng nhập **Cloudflare Dashboard**.
2. Chọn:
   ```text
   yourdomain.com
   ```
3. Menu bên trái mở:
   ```text
   Rules
   ```
4. Chọn:
   ```text
   Origin Rules
   ```
5. Bấm:
   ```text
   Create rule
   ```
6. Rule name:
   ```text
   PartFlow origin port
   ```
7. Điều kiện:
   ```text
   Field:    Hostname
   Operator: equals
   Value:    partflow.yourdomain.com
   ```
8. Trong origin parameter actions, bật:
   ```text
   Destination Port
   ```
9. Đặt:
   ```text
   Rewrite to: 8443
   ```
10. Save/deploy rule.

Luồng đầy đủ:

```text
Browser :443
    ↓
Cloudflare :443
    ↓ Origin Rule
Public WAN :8443
    ↓ UniFi Port Forward
NAS :443
    ↓ DSM Reverse Proxy
127.0.0.1:18080
    ↓
PartFlow
```

Browser không cần biết port 8443.

---

# Phần I — Kiểm tra end-to-end

# 22. Kiểm tra DDNS

1. Trong Cloudflare:
   ```text
   DNS → Records
   ```
2. Xác nhận:
   ```text
   ddns.yourdomain.com
   ```
   đã là public WAN IPv4 hiện tại, không còn `0.0.0.0`.
3. Chạy:
   ```powershell
   nslookup ddns.yourdomain.com 1.1.1.1
   ```
4. Xác nhận kết quả giống WAN IPv4 trên UniFi.

---

# 23. Kiểm tra PartFlow public DNS record

1. Trong Cloudflare:
   ```text
   DNS → Records
   ```
2. Xác nhận:
   ```text
   partflow.yourdomain.com
   ```
   là:
   ```text
   CNAME → ddns.yourdomain.com
   Proxied
   ```
3. Chạy:
   ```powershell
   nslookup partflow.yourdomain.com 1.1.1.1
   ```

Vì record đang Proxied, kết quả thường là Cloudflare IP. Đây là đúng.

---

# 24. Test từ ngoài LAN

Không chỉ test từ máy đang nằm trong cùng LAN công ty.

1. Trên điện thoại, tắt Wi-Fi và dùng cellular.
2. Mở:
   ```text
   https://partflow.yourdomain.com
   ```
3. Xác nhận:
   - PartFlow load được;
   - browser báo HTTPS hợp lệ;
   - login chạy;
   - `/scan-station` mở được;
   - các thao tác có API chạy;
   - URL không có `:8443`, `:18080` hay `:5173`.
4. Nếu có thể, test thêm từ một máy tính ngoài mạng công ty.

Test external thành công mới xác nhận toàn bộ đường đi, không chỉ reverse proxy local.

---

# 25. Hiểu certificate mà browser nhìn thấy

Khi Cloudflare Proxy bật:

```text
Browser
  │ public Cloudflare edge certificate
  ▼
Cloudflare
  │ validate Cloudflare Origin CA certificate
  ▼
DSM
```

Browser bình thường thấy public Cloudflare certificate, không thấy Origin CA certificate được cài trên DSM.

Nếu chỉ một browser báo `Not secure`, nhưng:

- certificate panel báo valid and trusted;
- máy khác chạy bình thường;
- điện thoại chạy bình thường;

thì browser đó có thể còn nhớ certificate exception hoặc insecure-content permission từ lúc troubleshoot.

Hãy test InPrivate/Incognito hoặc reset site permissions trước khi sửa server.

---

# Phần J — Security rules

# 26. Các port phải giữ private

Không public trực tiếp các port sau:

```text
5173   development/staging Vite
18080  PartFlow production loopback endpoint
8000   PartFlow backend
5432   PostgreSQL
5000   DSM HTTP management
5001   DSM HTTPS management
```

Chỉ expose origin HTTPS port cần thiết:

```text
Trường hợp A: WAN 443  → NAS 443
Trường hợp B: WAN 8443 → NAS 443
```

PartFlow public hostname không được expose DSM administration interface.

---

# 27. Hardening khuyến nghị sau khi hệ thống chạy ổn

Sau khi end-to-end test thành công:

1. Giữ `partflow.yourdomain.com` **Proxied**.
2. Giữ Cloudflare SSL/TLS = **Full (strict)**.
3. DDNS API token chỉ giới hạn đúng domain và quyền cần thiết.
4. NAS dùng fixed LAN IP.
5. PartFlow port `18080` chỉ bind vào `127.0.0.1`.
6. Backend và PostgreSQL port không publish.
7. Rà lại DSM firewall.
8. Rà lại UniFi WAN/firewall rules.
9. Tắt UPnP trừ khi có yêu cầu đã được tài liệu hóa.
10. Có thể giới hạn PartFlow origin port chỉ nhận Cloudflare source IP ranges nếu quy trình vận hành của bạn có thể cập nhật các range này khi Cloudflare thay đổi.
11. Chỉ bật HSTS sau khi HTTPS đã ổn định và hiểu rõ cách recovery.

---

# Phần K — Troubleshooting

# 28. `404 Not Found` khi test `NAS_IP:5173`

Không troubleshoot PartFlow production bằng:

```text
https://192.168.0.11:5173
```

Production dùng:

```text
127.0.0.1:18080 → web container :80
```

Port 5173 thuộc development/staging.

---

# 29. Vite báo `Blocked request` / HTTP 403

Thông thường nghĩa là request đang tới development/staging Vite server, không phải production nginx web container.

Không làm yếu Vite host restriction để workaround production.

Dùng đường DSM production reverse proxy.

---

# 30. Cloudflare 521 — origin không tới được

Kiểm tra theo thứ tự:

1. `partflow-production-web-1` đang `Healthy`.
2. `curl http://127.0.0.1:18080/web-health` chạy trên NAS.
3. DSM Reverse Proxy tồn tại và Source là HTTPS 443.
4. DSM firewall cho phép connection.
5. UniFi Port Forward dùng đúng public WAN port.
6. Destination của Port Forward là `NAS_IP:443`.
7. Với Trường hợp B, Cloudflare Origin Rule rewrite đúng port mà UniFi đang listen.

---

# 31. Cloudflare 525 — SSL handshake failed

Kiểm tra:

1. DSM Reverse Proxy Source Protocol là `HTTPS`.
2. DSM đang listen port 443.
3. Cloudflare Origin CA certificate đã import.
4. Certificate và private key khớp nhau.
5. UniFi forward tới NAS TCP 443.
6. Cloudflare Origin Rule đang dùng đúng public WAN port.

---

# 32. Cloudflare 526 — Invalid SSL certificate

Khi dùng `Full (strict)` kiểm tra:

1. Origin CA certificate chưa hết hạn.
2. Hostname của certificate có:
   ```text
   partflow.yourdomain.com
   ```
3. DSM đã gán đúng certificate cho PartFlow reverse proxy hostname.
4. Cloudflare đang tới đúng NAS reverse proxy.

---

# 33. PartFlow load nhưng login/session lỗi

Kiểm tra tab DSM Custom Header.

Phải có:

```text
X-Forwarded-For    $proxy_add_x_forwarded_for
X-Forwarded-Proto  $scheme
```

Browser public phải dùng:

```text
https://partflow.yourdomain.com
```

PartFlow production dùng secure cookie và phải biết request gốc từ client là HTTPS.

---

# 34. DDNS không update

Kiểm tra:

1. UniFi WAN có public IPv4.
2. Cloudflare API token còn active.
3. Token scope đúng domain.
4. Có `DNS → Edit`.
5. Có `Zone → Read`.
6. Client IP filtering để trống.
7. DDNS hostname đúng.
8. Zone name đúng.
9. DDNS record vẫn là DNS only.

Một cách test lại rất rõ ràng là tạm đổi content DDNS về:

```text
0.0.0.0
```

sau đó làm UniFi refresh/re-save DDNS profile và xác nhận public WAN IP xuất hiện lại.

---

# 35. DSM Let's Encrypt không cấp được certificate

DSM Let's Encrypt thường dựa vào public HTTP validation và có thể cần public TCP 80 đi tới NAS TCP 80.

Tài liệu này chủ đích dùng Cloudflare Origin CA nên PartFlow origin certificate không cần port 80.

Không mở port 80 chỉ vì một workflow certificate cũ yêu cầu nếu bạn đã chọn thiết kế Cloudflare Origin CA.

---

# Phần L — Checklist cuối

# 36. Cloudflare checklist

```text
[ ] Zone active trên Cloudflare
[ ] ddns.yourdomain.com A record tồn tại
[ ] Initial DDNS value = 0.0.0.0
[ ] UniFi đã đổi DDNS value sang public WAN IPv4 thật
[ ] DDNS record = DNS only
[ ] API token dùng template Edit zone DNS
[ ] API token scope = Specified Domains → yourdomain.com
[ ] API token permissions = DNS Edit + Zone Read
[ ] Client IP filtering để trống
[ ] partflow.yourdomain.com CNAME → ddns.yourdomain.com
[ ] PartFlow CNAME = Proxied
[ ] Đã tạo Cloudflare Origin CA certificate
[ ] SSL/TLS mode = Full (strict)
[ ] Chỉ Case B: Origin Rule rewrite destination port → 8443
```

# 37. UniFi checklist

```text
[ ] WAN IPv4 là public
[ ] Cloudflare DDNS profile đã save và hoạt động
[ ] NAS có fixed LAN IP
[ ] Case A: WAN TCP 443  → NAS TCP 443
    HOẶC
[ ] Case B: WAN TCP 8443 → NAS TCP 443
[ ] Không WAN forward tới 5173
[ ] Không WAN forward tới 18080
[ ] Không WAN forward backend/database ports
```

# 38. Synology checklist

```text
[ ] partflow-production-web-1 = Healthy
[ ] Production mapping = 127.0.0.1:18080 → container:80
[ ] /web-health trả về live
[ ] Cloudflare Origin CA certificate đã import
[ ] Origin certificate đã gán cho partflow.yourdomain.com
[ ] Reverse Proxy source = HTTPS partflow.yourdomain.com:443
[ ] Reverse Proxy destination = HTTP 127.0.0.1:18080
[ ] X-Forwarded-For = $proxy_add_x_forwarded_for
[ ] X-Forwarded-Proto = $scheme
[ ] Proxy read timeout = 300 seconds
[ ] Proxy send timeout = 300 seconds
```

# 39. External validation checklist

```text
[ ] https://partflow.yourdomain.com mở được từ cellular/external Internet
[ ] Browser báo HTTPS certificate hợp lệ
[ ] Login chạy
[ ] Scan Station route chạy
[ ] API operations chạy
[ ] Public URL không cần thêm port
```

---

# 40. Cấu hình cuối khuyến nghị khi WAN 443 đã bị dùng

```text
Cloudflare
  ddns.yourdomain.com
      A → dynamic public WAN IPv4
      DNS only

  partflow.yourdomain.com
      CNAME → ddns.yourdomain.com
      Proxied

  SSL/TLS
      Full (strict)

  Origin CA
      certificate cho partflow.yourdomain.com

  Origin Rule
      hostname = partflow.yourdomain.com
      destination port = 8443

UniFi Gateway
  Dynamic DNS
      Cloudflare
      ddns.yourdomain.com

  Port Forward
      WAN TCP 8443
          → 192.168.0.11 TCP 443

Synology DSM
  Certificate
      Cloudflare Origin CA
      gán cho partflow.yourdomain.com

  Reverse Proxy
      Source:
          HTTPS partflow.yourdomain.com:443
      Destination:
          HTTP 127.0.0.1:18080

PartFlow
  web container
      127.0.0.1:18080 → container:80
```

Public URL:

```text
https://partflow.yourdomain.com
```

---

# Tài liệu tham khảo

- Ubiquiti — UniFi Gateway Dynamic DNS  
  https://help.ui.com/hc/en-us/articles/9203184738583-UniFi-Gateway-Dynamic-DNS
- Ubiquiti — UniFi Gateway Port Forwarding  
  https://help.ui.com/hc/en-us/articles/235723207-UniFi-Gateway-Port-Forwarding
- Cloudflare — Create API token  
  https://developers.cloudflare.com/fundamentals/api/get-started/create-token/
- Cloudflare — API token templates  
  https://developers.cloudflare.com/fundamentals/api/reference/template/
- Cloudflare — Proxy status  
  https://developers.cloudflare.com/dns/proxy-status/
- Cloudflare — Origin Rules  
  https://developers.cloudflare.com/rules/origin-rules/
- Cloudflare — Origin CA  
  https://developers.cloudflare.com/ssl/origin-configuration/origin-ca/
- Cloudflare — Full (strict)  
  https://developers.cloudflare.com/ssl/origin-configuration/ssl-modes/full-strict/
- Synology — Login Portal / Reverse Proxy  
  https://kb.synology.com/en-in/DSM/help/DSM/AdminCenter/system_login_portal_advanced
- Inadyn — Cloudflare provider / token permissions  
  https://github.com/troglobit/inadyn
