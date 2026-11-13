# Thiết kế GUI PartFlow v18

> **Ngôn ngữ:** Bản dịch tiếng Việt của [GUI_DESIGN.md](./GUI_DESIGN.md). Bản tiếng Anh là nguồn chuẩn (source of
> truth); nếu hai bản khác nhau, bản tiếng Anh đúng.
> Đồng bộ với bản tiếng Anh tại commit `398c617` cộng các thay đổi chưa commit ngày 2026-11-12.

> **Trạng thái:** Hiện hành. Tài liệu này là giao diện người dùng (user interface, UI) mục tiêu đã được duyệt của PartFlow.

## Về tài liệu này

Tài liệu này mô tả mọi màn hình của PartFlow trông như thế nào và hoạt động ra sao: bố cục, các trạng thái, câu chữ
chính xác hiển thị trên màn hình, hành vi bàn phím và máy quét mã vạch (barcode scanner), design token, các breakpoint
theo chiều rộng màn hình và các quy tắc trợ năng (accessibility). Tài liệu được viết cho bất kỳ ai thiết kế, xây dựng,
review hoặc kiểm thử frontend của PartFlow, và cho bất kỳ ai cần biết một màn hình phải hiển thị những gì.

Đây là một trong nhiều tài liệu chuẩn (canonical), mỗi tài liệu có vai trò riêng:

- [PROJECT_PROFILE.md](./PROJECT_PROFILE.md) sở hữu domain: thuật ngữ, quy tắc nghiệp vụ, bất biến (invariant), workflow
  và phạm vi sản phẩm. Tài liệu này không định nghĩa lại các quy tắc đó; khi một màn hình phụ thuộc vào một quy tắc, tài
  liệu này trỏ tới mục tương ứng của PROJECT_PROFILE (viết là "PROJECT_PROFILE §n").
- Tài liệu này (GUI_DESIGN) sở hữu UI đã được duyệt.
- [IMPLEMENTATION_ROADMAP.md](./IMPLEMENTATION_ROADMAP.md) sở hữu thứ tự triển khai và trạng thái triển khai hiện tại,
  bao gồm mọi giới hạn tạm thời của một màn hình.
- Các tài liệu slice (ví dụ [SLICE1_DATA_MODEL.md](./SLICE1_DATA_MODEL.md)) là chi tiết triển khai cấp dưới.
- [DEPLOYMENT.md](./DEPLOYMENT.md) sở hữu việc triển khai (deployment).

Đi kèm tài liệu này là một mockup tương tác: `docs/mockups/partflow-gui-mockup-v18.html`. Các mockup cũ hơn được lưu
trữ trong `docs/archive/`. Mockup là tài liệu tham chiếu trực quan; khi mockup và văn bản này khác nhau, văn bản này
đúng. Mockup tuân theo mô hình định danh Part Number của PROJECT_PROFILE: Part Number chuẩn là chữ hoa, đã cắt khoảng
trắng hai đầu và không có khoảng trắng bên trong, còn bản ghi master của Part Number là metadata tùy chọn và có thể bị
xóa cứng (hard-delete).

Mã nguồn trích dẫn tài liệu này theo số mục (ví dụ `GUI_DESIGN §4.7`), nên các heading có đánh số giữ nguyên số của
chúng. Lịch sử phiên bản là bảng thay đổi ở cuối tài liệu.

**Các chữ viết tắt dùng xuyên suốt.** PN là Part Number. WO là Work Order. Các chữ viết tắt khác được viết đầy đủ ở lần
xuất hiện đầu tiên.

---

# 1. Phạm vi

Tài liệu này bao quát các view ứng dụng liệt kê trong PROJECT_PROFILE §21, được tổ chức thành **mười GUI view**:

1. Scan Station (sản xuất).
2. Production Board (giám sát trên màn hình lớn).
3. Area Board (giám sát: tổng quan **All Areas** cùng một view chi tiết cho mỗi Area; phần tổng quan chứa nội dung mà
   PROJECT_PROFILE §21 gọi là *Manager Summary*).
4. Machines (quản lý vòng đời và bảo trì Machine).
5. Tracking (quản lý, tổ chức xoay quanh Part Number; nhãn mà người vận hành nhìn thấy là **PN Tracking**).
6. Work Orders (quản lý: nhập Work Order thủ công, import từ file và phát hành vào sản xuất (production release);
   PROJECT_PROFILE §21 *Work Orders*).
7. Planned Routes (quản lý các định nghĩa route dùng lại được).
8. Part Numbers (quản lý metadata master PartNumber và nhãn barcode PN; PROJECT_PROFILE §21
   *Part Numbers*).
9. Priority Management (xếp hạng Work Order Demand Hot).
10. Administration (cấu hình).

## 1.1 Cấu trúc điều hướng

Thanh điều hướng trên cùng có bốn mục. Các view quản lý được nhóm thành **các sub view của một view Management**:

| Cấp trên cùng    | Chứa                                                                                           |
|------------------|------------------------------------------------------------------------------------------------|
| Scan Station     | — (một view duy nhất)                                                                          |
| Production Board | — (một view duy nhất)                                                                          |
| Management       | Area Board · Work Orders · PN Tracking · Priority · Planned Routes · Part Numbers · Machines   |
| Administration   | — (một view duy nhất, có sidebar điều hướng riêng như mô tả ở §9)                              |

Chọn Management sẽ mở **sub view dùng gần nhất** khi người dùng được phép mở nó. Nếu không, Management mở sub view đầu
tiên trên thanh mà người dùng được phép mở (Area Board ở lần mở đầu tiên đối với người dùng được phép mở nó; bản thân
Area Board mở ở phần tổng quan All Areas). Management cũng hiện một thanh sub view phụ bên dưới thanh điều hướng trên
cùng. Trên thanh sub view, **Part Numbers đứng kế cuối và Machines đứng cuối, ngay sau nó**.

Thanh sub view nằm bên trong vùng nội dung cuộn và dính vào đỉnh vùng đó dưới dạng một thanh kính mờ (frosted glass) trên
tông panel trong mờ riêng của nó: nội dung view khi cuộn sẽ đi bên dưới thanh và hiện mờ qua đó. Thanh thu gọn lại khi
nội dung cuộn xuống và được khôi phục ở lần cuộn lên đầu tiên hoặc khi gần đỉnh. Banner OFFLINE cố định (§3
rule 6) nằm ngoài vùng cuộn, ở phía trên nó.

Mọi phần khung điều hướng (navigation chrome) đều theo chế độ theme toàn cục (§2.1). Việc nhóm chỉ mang tính điều hướng:
mỗi sub view giữ đặc tả riêng của nó (§6–§8, §11–§14).

Machines (`/management/machines`), Planned Routes (`/management/planned-routes`) và Part Numbers
(`/management/part-numbers`) là các sub view **dữ liệu master sản xuất** (production master data). Đây là các chức năng
quản lý vận hành, được mở theo permission bởi các vai trò sản xuất được ủy quyền (Production Manager, Process Engineer,
Maintenance Manager, hoặc một chuyên viên được ủy quyền khác; PROJECT_PROFILE §20). Chúng được cố ý nhóm dưới Management
chứ không phải Administration.

### Truy cập Management

Management yêu cầu một người dùng đã đăng nhập (§1.2).

- **Chưa đăng nhập.** Vào Management khi chưa đăng nhập sẽ mở dialog đăng nhập phủ lên một panel đăng nhập.
- **Phiên đăng nhập hết hạn.** Một phiên đăng nhập hết hạn giữ nguyên màn hình đang mở và công việc trên đó cho cùng
  người dùng; nếu một người dùng khác đăng nhập, màn hình bắt đầu lại từ đầu. Trong lúc đó các view trực tiếp (Area Board,
  PN Tracking) ngừng làm mới. Đóng dialog đăng nhập thì dialog vẫn đóng. Các view trực tiếp làm mới trở lại khi cùng người
  dùng đó đăng nhập.
- **Những sub view nào mở được.** Mỗi sub view mở cho người dùng giữ **View all current and historical production data**
  hoặc một permission mà một trong các action của sub view đó dùng:

  | Sub view       | Mở với View all current and historical production data, hoặc cũng mở với …                      |
  |----------------|--------------------------------------------------------------------------------------------------|
  | Area Board     | (chỉ permission trước)                                                                           |
  | Work Orders    | Create and edit Work Orders, Edit Work Order Demand, hoặc Edit Work Order Allocation              |
  | PN Tracking    | Edit Work Order Allocation hoặc Assign Routes                                                    |
  | Priority       | Set Work Order Demand priority hoặc Reorder Hot items                                            |
  | Planned Routes | Manage Planned Routes                                                                            |
  | Part Numbers   | Manage Part Numbers                                                                              |
  | Machines       | Manage Machines                                                                                  |

- **Thanh sub view** chỉ liệt kê các sub view mà người dùng đã đăng nhập được phép mở. Một sub view mà người dùng không
  mở được (ví dụ khi đến đó qua một link) sẽ cho biết những permission nào mở được nó.
- **Các control** cho những thay đổi mà người dùng không được phép thực hiện bị ẩn, không phải bị vô hiệu hóa (disable).
  Khi người dùng không được thay đổi gì trong view, một ghi chú `View only` nêu tên permission. Các giá trị đã được tải
  vẫn hiển thị dưới dạng văn bản.
- Dialog chi tiết Part Number mở ở chế độ chỉ đọc cho người dùng không được quản lý Part Numbers; nhãn barcode vẫn truy
  cập được (§11.2, §14.2).
- Undo và Redo của Priority hiện ra cho người dùng được phép thay đổi Hot list.
- Scan Station và Production Board không bao giờ yêu cầu đăng nhập.
- Server tự kiểm tra mọi thao tác đọc và ghi của Management; những gì màn hình ẩn đi chỉ là phần trình bày.

## 1.2 User đã đăng nhập

Các User của ứng dụng đăng nhập bằng tên đăng nhập (login name) và mật khẩu. Administration và Management yêu cầu đăng
nhập. Scan Station và Production Board không bao giờ yêu cầu đăng nhập; thay vào đó, một Scan Station được sử dụng từ một
thiết bị đã được đăng ký (enroll) (§4.13).

- **Account chip.** Thanh điều hướng trên cùng kết thúc bằng một account chip. Khi chưa ai đăng nhập, chip hiển thị
  `Sign in` (và `Set up PartFlow` khi PartFlow chưa có Administrator nào). Ngược lại, chip hiển thị avatar và tên, cùng
  một menu chứa `Change password…` và `Sign out`. Chip không có ở bất kỳ nơi nào thanh điều hướng trên cùng bị ẩn: chế
  độ production của Scan Station và kiosk của Production Board.
- **Đăng nhập là một dialog modal phủ lên view hiện tại.** Route và mọi công việc đang mở vẫn giữ nguyên. Một phiên đăng
  nhập hết hạn sẽ mở lại dialog đăng nhập phủ lên view hiện tại. Ngoại lệ là khi một lần lưu theme phát hiện phiên đăng
  nhập đã kết thúc (§2.1): khi đó dialog mở ở request bị từ chối tiếp theo.
- **Thay mật khẩu do administrator đặt.** Trong khi một mật khẩu do administrator đặt phải được thay
  (Administration → Settings → User sign-in, §9), dialog `Choose a new password` không thể bị đóng, trừ khi
  đăng xuất.
- **Set up PartFlow.** Dialog chạy lần đầu (first-run) chỉ khả dụng khi chưa có Administrator nào. Dialog yêu cầu setup
  token được in trong log của server và tạo Administrator đầu tiên.
- **Ai không bao giờ bị hỏi.** Scan Station và Production Board không bao giờ yêu cầu đăng nhập. Worker được nhận diện
  bằng badge, không bao giờ bằng đăng nhập User (§4.12).
- **Ẩn, không vô hiệu hóa.** Các control mà một User không được dùng sẽ bị ẩn, không phải bị vô hiệu hóa.
- **Kết nối.** Mọi action đăng nhập tuân theo việc chặn ghi khi offline (§3 rule 6).

---

# 2. Design System

## 2.1 Một bộ token, hai theme chuyển đổi được

Ứng dụng có một **chế độ theme toàn cục duy nhất, Dark hoặc Light, mà người dùng có thể chuyển đổi**. **Mọi view đều
theo chế độ đã chọn**; không view nào có theme cố định.

- **Nút chuyển (toggle)** nằm trên thanh điều hướng trên cùng và áp dụng ngay lập tức cho toàn bộ ứng dụng, bao gồm
  khung điều hướng, dialog và toast. Trong **chế độ production** của Scan Station, nơi thanh điều hướng trên cùng bị ẩn,
  một biến thể **không viền** (borderless) thu gọn của cùng control dùng chung đó hiển thị trong nhóm action của header
  station, **ngay bên dưới connectivity chip**, trong một chồng dọc thu gọn, căn phải (§4.1). Biến thể này dùng cùng
  trạng thái theme toàn cục (`aria-pressed`, trạng thái focus nhìn thấy được). Theme đã chọn được giữ nguyên khi chuyển
  giữa route tiêu chuẩn và route production. Header **Kiosk mode** của Production Board (§5.1) dùng lại cùng control
  không viền thu gọn này.
- **Dark là mặc định.** PartFlow ưu tiên xưởng sản xuất (shop floor) trước hết: theme tối giảm chói trong xưởng, dễ đọc
  từ xa và chịu được màn hình chất lượng thấp. Người dùng ở bàn làm việc thích theme sáng cho công việc dữ liệu dày đặc
  chỉ cần chuyển một lần.
- **Chỉ dùng semantic token.** Mọi style của component dùng semantic token (background, panel, border, text, muted,
  status-text, …); hai định nghĩa theme cung cấp các giá trị. Việc thêm mới hoặc đổi style một component không bao giờ
  hard-code một màu riêng của theme.
- **Màu trạng thái** (§2.2) giữ các biến thể *văn bản* theo từng theme để độ tương phản được đảm bảo ở cả hai chế độ (ví
  dụ, văn bản success sáng trên nền tối và đậm hơn trên nền sáng). Nền và sắc màu nhạt (tint) của trạng thái được dùng
  chung. Màu nhận diện Area giống hệt nhau ở cả hai theme.

### Lưu theme

Lựa chọn theme được lưu **theo từng User và theo từng Scan Station** và được xác định theo một thứ tự ưu tiên cố định:

1. preference của User đang đăng nhập;
2. preference của Scan Station;
3. mặc định Dark.

Worker Session không bao giờ ảnh hưởng đến theme. Badge của Worker là danh tính kiểm toán (audit) tại xưởng, không phải
tài khoản ứng dụng (§4.12), nên đăng nhập bằng badge, đăng xuất badge và việc session hết hạn đều giữ nguyên theme.

**Preference của Scan Station.** Trên `/scan-station/<id>` và route production của nó, preference đã lưu của station được
áp dụng khi station tải (không có preference đã lưu nghĩa là Dark). Toggle ở đó lưu preference cho station đó trong khi
station đã được tải và đang kết nối. Thay đổi chỉ áp dụng cho phiên trình duyệt hiện tại, và không có gì được đưa vào hàng
đợi, khi station offline, khi station không tải được, hoặc khi việc lưu không được xác nhận; trong trường hợp cuối, một
notice cảnh báo nói rõ điều đó và giải thích cách lưu lại. Khi chưa ai đăng nhập, các route khác giữ lựa chọn trong phiên
trình duyệt.

**Toggle lưu preference nào.**

- Khi một User đang đăng nhập, preference đã lưu của User đó áp dụng trên mọi route, và toggle lưu preference của User
  trên mọi route, kể cả các route Scan Station và Kiosk, không bao giờ lưu preference của Scan Station.
- Preference của Scan Station chỉ được toggle của nó lưu khi trình duyệt đó được biết chắc là không có ai đăng nhập.
  Khi trạng thái đăng nhập chưa được biết, thay đổi chỉ áp dụng cho phiên trình duyệt hiện tại.
- Đăng xuất, hoặc một phiên đăng nhập đã kết thúc, đưa màn hình về preference của Scan Station trên các route station và
  về Dark ở những nơi khác. Một lần lưu theme không bao giờ mở dialog Sign-in.
- Khi trình duyệt offline, khi đang bắt buộc đổi mật khẩu, hoặc khi việc lưu không được xác nhận, thay đổi chỉ áp dụng
  cho phiên trình duyệt hiện tại và không có gì được đưa vào hàng đợi. Một lần lưu không được xác nhận hoặc một phiên đăng
  nhập đã kết thúc sẽ hiện một cảnh báo nói rõ cần làm gì: trên Scan Station là notice cảnh báo nổi của nó; ở nơi khác là
  một toast.
- Không màn hình Administration nào chỉnh sửa preference theme đã lưu của một User; chỉ toggle mới đặt nó.
- Việc đổi theme không bao giờ được ghi audit.

Cả hai theme dùng chung các color token, thang khoảng cách (spacing scale) và typography, nên sản phẩm có cảm giác là
một hệ thống thống nhất ở cả hai chế độ.

## 2.2 Color token

Màu trạng thái mang ý nghĩa ngữ nghĩa, không bao giờ để trang trí:

| Trạng thái     | Màu       | Ý nghĩa                                                   |
|----------------|-----------|-----------------------------------------------------------|
| **Success**    | `#31d287` | Movement đã ghi nhận, action đã xác nhận                   |
| **Warning**    | `#ffb224` | Cần chú ý, ngữ cảnh đang chờ, lệch route, sắp đến hạn       |
| **Error**      | `#ff6166` | Lượt quét bị từ chối, vi phạm toàn vẹn, quá hạn           |
| **Info / accent** | `#4f8cff` | Lựa chọn, focus, action chính                          |

**Màu Area.** Mỗi Area có một màu nhận diện ổn định, được dùng nhất quán trong **mọi** view (chip, chấm, thanh phân bổ,
header cột). Màu là thuộc tính hiển thị của Area và có thể chỉnh sửa trong Administration mà không ảnh hưởng đến lịch sử
(PROJECT_PROFILE §7 Area).

Bảng màu ban đầu: Material `#8b93a8`, Cut `#f5b83d`, Lathe `#3da5ff`, Mill `#9b6ef3`, Manual `#e06fae`, Deburr
`#2fbf9b`, External `#ff8a4c`, Stockroom `#2fca7c`.

## 2.3 Typography

- **Văn bản UI** dùng bộ font hệ thống (`system-ui, Segoe UI, Roboto…`): không phụ thuộc web font, và hoạt động được khi
  offline.
- **Các định danh** (PN, Work Order Number, Work Order tạm thời, Job Number bên ngoài, id của Quantity Flow, giá trị
  barcode, số lượng, mốc thời gian) dùng font monospace. Định danh phải tách biệt rõ về mặt thị giác với văn xuôi vì người
  vận hành đọc chúng để đối chiếu với phiếu theo hàng (paper traveler) và nhãn bìa hồ sơ.
- **Cỡ chữ tối thiểu tại xưởng:** thân văn bản 16 px, PN ≥ 19 px, số lượng ≥ 18 px đậm. Production Board được thiết kế
  để đọc ở khoảng cách 3–5 m (PN từ 22 px trở lên, các con số chính từ 18 px trở lên).
- **PN không bao giờ xuống dòng.** PN luôn được hiển thị trên một dòng duy nhất. Ở nơi container không thể giãn theo định
  danh (thẻ Area Board và danh sách tổng quan), một PN quá dài được **cắt bớt kèm dấu ba chấm** và PN đầy đủ hiện trong
  tooltip khi rê chuột; các con số quan trọng cho bố cục (số lượng) không bao giờ được dịch chuyển vì độ dài PN. Các view
  có kích thước xoay quanh PN (Production Board, Tracking) tiếp tục định kích thước cột cho vừa PN.
- **Hình dạng thực tế của định danh và mô tả** (PROJECT_PROFILE §8.1):
  - PN là chuỗi mờ (opaque string), thường có nhiều đoạn, có gạch nối và là số, với độ dài khác nhau (từ ngắn `214-406`
    đến dài `0455-20-0118-03` và dài hơn nữa). PN luôn hiển thị ở dạng chữ hoa chuẩn, đúng chính xác là định danh đã lưu.
    UI giữ nguyên chuỗi chính xác, không bao giờ suy ra ý nghĩa từ các đoạn của nó, và việc tìm kiếm và lọc so khớp giá
    trị có gạch nối cùng dấu câu của nó theo đúng nguyên văn.
  - Work Order Number và Job Number bên ngoài cũng là chuỗi mờ, thường trông giống số (Work Order
    Number `007482`, Job Number `18427`). Chúng không bao giờ được phân tích (parse), đệm thêm ký tự hoặc định dạng lại.
    Hai giá trị này là các định danh riêng biệt và không bao giờ được gộp lại.
  - Revision là một trường thông tin tùy chọn riêng biệt (`A`, `B`, `E`, hoặc để trống). Revision không bao giờ được nối
    vào PN, và một Revision trống hiển thị gọn gàng, không có ký tự giữ chỗ gây nhiễu.
  - Tên chi tiết và mô tả là văn bản tự do, thường viết hoa với dấu phẩy, dấu gạch chéo, phân số, kích thước và các chữ
    viết tắt trong gia công, và **có thể xuống dòng tự nhiên thành hai hoặc ba dòng**. Việc xuống dòng của mô tả không bao
    giờ được làm dịch chuyển các cột số lượng, trạng thái, ngày hoặc action: hãy giới hạn chiều rộng của mô tả thay vì nới
    rộng cột định danh.

## 2.4 Công thái học cho cảm ứng và scanner

- **Vùng chạm (touch target).** Kích thước vùng chạm là yêu cầu của Scan Station và của xưởng, không phải quy tắc chung của
  toàn ứng dụng. Các control tương tác mà người vận hành thực sự thao tác bằng cảm ứng trên Scan Station (và trên các bề
  mặt cảm ứng khác tại xưởng) có vùng chạm tối thiểu 48×48 px (`--touch-min`). Các action chính của Scan Station cao ít
  nhất 56 px (`--touch-primary`). Các view quản lý và quản trị hướng desktop giữ kích thước control desktop bình thường.
- **Quét hoặc chạm.** Mọi action sản xuất đều thực hiện được bằng một lượt quét hoặc một lần chạm. Không bao giờ bắt buộc
  dùng chuột trên các view tại xưởng.
- **Hỗ trợ keyboard wedge.** Ô nhập quét là một ô nhập văn bản thông thường, kết thúc bằng Enter: không có driver tùy
  chỉnh và không có chọn chế độ quét (PROJECT_PROFILE §10 Barcode Model). Scanner keyboard wedge là máy quét mã vạch gõ
  giá trị đã giải mã dưới dạng các phím bấm.

## 2.5 Màn hình nhỏ — ưu tiên cuộn dọc

Mọi view đều hỗ trợ viewport có chiều rộng điện thoại (khoảng 390 px trở lên). Các quy tắc là:

- **Cuộn dọc là thao tác duyệt duy nhất.** Mọi view phải duyệt được đầy đủ chỉ bằng cuộn dọc; tài liệu không bao giờ tràn
  theo chiều ngang ở bất kỳ chiều rộng viewport được hỗ trợ nào.
- **Điều hướng thích ứng.** Ở chiều rộng điện thoại:
  - thanh điều hướng ứng dụng trên cùng thu gọn vào sau một nút menu rõ ràng (`aria-expanded`); các link điều hướng mở
    ra thành một panel dọc dưới thanh và đóng lại khi điều hướng, khi nhấn Escape, hoặc khi chạm ra ngoài;
  - thẻ dữ liệu mẫu dành cho phát triển bị ẩn;
  - control Dark/Light thu gọn còn biểu tượng; chữ chỉ chế độ được ẩn về mặt thị giác, không bao giờ bị xóa, nên tên trợ
    năng (accessible name) không thay đổi;
  - connectivity chip (chip trạng thái kết nối) vẫn hiển thị ở mọi chiều rộng.

  Thanh điều hướng sub view của Management là MỘT hàng vuốt được ở mọi chiều rộng và không bao giờ xuống thành hàng thứ
  hai. Ở bất kỳ nơi nào các link không vừa, hàng đó cuộn ngang bên trong vùng riêng của nó, không có thanh cuộn nhìn thấy
  được, và sub view đang hoạt động được đưa vào tầm nhìn. Trên viewport rộng hơn, thanh điều hướng trên cùng hiển thị
  trên một dòng và xuống dòng khi cần.
- **Bảng desktop rộng bỏ cột trước, rồi thu gọn.** Khi viewport không chứa được bố cục cột desktop của một bảng, các cột
  có ưu tiên thấp nhất ẩn trước (cột Asset của Machines, rồi Assigned now). Khi ngay cả các cột còn lại cũng không vừa,
  bảng thu gọn lại:
  - PN Tracking, bảng WO Demand trong overlay chi tiết Tracking, danh sách Work Orders, Planned Routes, bảng Areas của
    Administration và bảng Machines đã ngừng sử dụng (retired) thu về dạng trình bày xếp chồng: hàng header bị ẩn, mỗi
    hàng trở thành một khối dọc, và các ô có ý nghĩa không tự hiển nhiên mang nhãn cột của chúng dưới dạng một chú thích
    nhỏ trên cùng dòng;
  - riêng danh sách Machines đang hoạt động thì giữ mỗi Machine trên một dòng, chỉ xuống dòng khi thực sự không chứa được
    các ô.

  Sắp xếp theo cột là tiện ích của bố cục rộng và không khả dụng khi bảng đang thu gọn; việc kích hoạt cả hàng và các
  control trong hàng vẫn hoạt động không đổi. Các breakpoint thu gọn được suy ra từ chiều rộng nội dung thực của từng
  bảng, không phải từ một breakpoint thiết bị dùng chung.
- **Toolbar giữ action chính trên hàng tìm kiếm.** Trên Machines, `+ New Machine` không bao giờ rơi xuống dưới ô tìm
  kiếm: ô tìm kiếm co lại vào phần chiều rộng còn lại, và nhãn nút xuống thành hai dòng khi cần.
- **Hàng quét và header của Scan Station thích ứng mà không mất ưu tiên cho việc quét.** Nút **⌨ Enter PN manually**
  co lại còn khoảng một nửa chiều rộng tự nhiên (nhãn của nó xuống thành hai dòng) trước khi ô nhập barcode phải nhường
  chiều rộng. Hàng Operations của header giữ trên một dòng và bỏ bớt phần trình bày theo từng tầng, đo theo không gian
  còn trống (bỏ nhãn, gom các chip phía sau vào một chip `…`, rồi ẩn đi) thay vì xuống dòng (§4.3, §4.4).
- **Area Board trở thành view chi tiết theo từng Area dạng phân trang ở chiều rộng điện thoại và máy tính bảng.** Dải
  tab (`All Areas` cùng một tab cho mỗi Area) bị ẩn hoàn toàn, và một **công tắc trượt `Summary` (mặc định tắt)** thay thế
  `Wrap columns`.
  - Summary **tắt** lật qua các **chi tiết theo từng Area**: một bố cục chi tiết dùng chung (§6.3) cho mỗi trang trong một
    carousel vuốt có bắt dính (snap), không có thanh cuộn nhìn thấy được, mỗi trang hẹp hơn viewport một chút để các trang
    lân cận ló ra, các chấm trang mang màu Area ở phía trên các trang (vừa là chỉ báo vừa là đích nhảy trực tiếp), và các
    nút cạnh nổi `‹` / `›` được ghim ở giữa viewport. Việc lật trang không bao giờ quay vòng.
  - Summary **bật** xếp chồng phần tổng quan All Areas (§6.2) thành một danh sách cột dọc duy nhất, rộng toàn màn hình;
    một header thẻ ở đó nhảy thẳng đến trang chi tiết của Area đó.

  Dạng trình bày theo tab với công tắc `Wrap columns` vẫn là dạng trình bày cho desktop và màn hình rộng.
- **Cuộn ngang có chủ đích là một công cụ điều hướng, không bao giờ là hệ quả ngẫu nhiên của bố cục.** Chỉ có đúng ba
  vùng cuộn ngang được chấp thuận:
  1. bảng All Areas trên desktop và máy tính bảng;
  2. hàng điều hướng sub view của Management ở chiều rộng điện thoại (một dải vuốt được, không có thanh cuộn nhìn thấy
     được);
  3. carousel chi tiết phân trang của Area Board trên điện thoại và máy tính bảng khi Summary tắt (lật trang có bắt dính,
     không có thanh cuộn nhìn thấy được).

  Các bề mặt nội dung (bảng, thẻ, dialog) không bao giờ đòi hỏi cuộn ngang trên điện thoại.
- **Production Board thích ứng bằng cách co giãn tỉ lệ, không bằng cách bố cục lại.** Đây là màn hình treo tường hoặc
  kiosk của Department, được thiết kế để đọc ở khoảng cách 3–5 m, với các cột được đo theo nội dung (§2.3, §5); đây không
  phải bề mặt làm việc trên điện thoại, và bố cục của nó không bao giờ bị nén lại với cái giá là vi phạm các quy tắc đọc
  được từ xa. Trên màn hình nhỏ, việc tự động co giãn hiển thị (§5) thu nhỏ toàn bộ board một cách đồng đều để toàn bộ
  chiều rộng bảng vừa khít, và một lần vuốt ngang sẽ đổi trang. Khi tắt Auto scale, board có thể cuộn bên trong vùng
  riêng của nó trên các viewport rất hẹp.
- **Các hành vi viewport hẹp sẵn có** (dialog workflow gần toàn màn hình ở §11, thẻ dòng demand, overlay Tracking trải
  rộng toàn viewport ở §7) vẫn giữ nguyên và bổ sung cho các quy tắc này.
- **Các quy tắc tại xưởng không bị nới lỏng.** Quy tắc PN một dòng (§2.3) và mô hình ưu tiên scanner của Scan Station
  (§2.4) vẫn áp dụng. Hỗ trợ điện thoại là tiện ích để duyệt và giám sát, và không bao giờ làm suy yếu các quy tắc tại
  xưởng.

## Form và dialog

Các quy tắc trình bày sau áp dụng cho form trong dialog của mọi view:

- **Từ bổ nghĩa trong nhãn trường.** Một từ bổ nghĩa đặt trong ngoặc đơn bên trong nhãn trường được hiển thị qua một
  marker dùng chung, tách biệt rõ về mặt thị giác với văn bản nhãn: `.field-optional` (nhẹ, mờ) cho `(optional)`;
  `.field-required` (tông lỗi theo ngữ nghĩa) cho `(required)` (nhãn lý do và lý do scrap trên Scan Station, lý do
  Reactivate của Machine); và `.field-note` (trung tính, nhẹ hơn nhãn một bậc) cho các từ bổ nghĩa trung tính (`PN (queued)`
  trong Assign to Machine, gợi ý `(Asset Tag)` của xác nhận bằng cách gõ).
- **Vị trí từ bổ nghĩa.** Khi nhãn nằm **phía trên** control của nó, từ bổ nghĩa ở trên cùng dòng với nhãn: một nhãn xếp
  chồng dạng flex-column bọc văn bản của nó (kể cả span từ bổ nghĩa) trong MỘT phần tử inline, nên từ bổ nghĩa không bao
  giờ có thể rơi xuống một hàng riêng giữa văn bản và control. Khi nhãn và control nằm **cạnh nhau**, từ bổ nghĩa có thể
  nằm cùng dòng hoặc xuống dòng tự nhiên dưới văn bản nhãn, tùy cách nào vừa với cột.
- **Khoảng cách nhãn.** Mọi form dialog có nhãn nằm trên control dùng một khoảng cách nhất quán 8 px giữa nhãn và control.
- **Khoảng cách của dialog quyết định.** Các dialog quyết định thu gọn (`ConfirmDialog`, `UnsavedChoiceDialog`,
  `TypedConfirmDialog`) dùng chung biến thể khoảng cách `msgdlg` của khung dialog: padding lớn hơn và khoảng thở rõ ràng
  giữa tiêu đề nhỏ phía trên (eyebrow title), phần thân thông điệp và hàng action. Các dialog workflow giữ khoảng cách cơ
  sở chặt hơn.
- **Mũi tên của select.** Mọi select native vẽ một mũi tên thả xuống (chevron) nhất quán, mang tông muted theo theme:
  một token `--select-arrow` theo từng theme được dùng bởi một rule `select` dùng chung với `appearance: none`. Các rule
  bề mặt select đặt `background-color`, không bao giờ dùng thuộc tính viết tắt `background` vốn sẽ xóa mất mũi tên.
- **Chiều cao của select.** Một select cắt giá trị của nó theo content box, và một giá trị cao hơn box đó được căn lên
  trên thay vì căn giữa. Vì vậy một select bị ghim vào một chiều cao cố định (các form cho mọi control cùng một chiều
  cao: các editor của Administration và các dialog Machines) không có padding dọc: giá trị được căn giữa trong toàn bộ
  chiều cao, và các chữ có phần chân (p, q, g, y) không bao giờ bị cắt. Một select có kích thước theo `min-height` không
  cần gì thêm.
- **Tông của Discard.** Trong `UnsavedChoiceDialog` dùng chung, action **Discard** mang tông nguy hiểm (danger) ở mọi nơi
  gọi (Work Orders, Machines, Planned Routes, Part Numbers, Administration): action này luôn vứt bỏ các chỉnh sửa đã
  nhập, nên nó không bao giờ được trông như lựa chọn trung tính bên cạnh Cancel.
- **Đóng khi còn dữ liệu chưa lưu.** Escape, một cú click vào nền (backdrop) và `Cancel (Esc)` là các yêu cầu đóng. Trên
  mọi dialog nhập dữ liệu để lưu hoặc thêm mới (các editor của Administration, `Change password`, `Set up PartFlow` và
  các dialog Management), một dialog có các giá trị vẫn bằng các giá trị lúc mở sẽ đóng ngay,
  không hỏi gì; một giá trị đã gõ rồi khôi phục lại được tính là không thay đổi. Khi có giá trị đã thay đổi, dialog hỏi
  trước, nên dữ liệu đã nhập không bao giờ bị mất một cách âm thầm:
  - một bản ghi đã có: `UnsavedChoiceDialog` dùng chung với tiêu đề `Unsaved changes` (thân
    `You have unsaved changes to {name}.`) cùng `Save changes` (lưu, và đóng khi lưu thành công; một lần lưu bị từ chối
    giữ dialog mở kèm thông báo của nó), `Discard changes` và `Cancel (Esc)`;
  - một bản ghi mới hoặc một lần nhập dùng một lần: một xác nhận `Discard …?` (ví dụ `Discard new Area?`,
    `Discard new password?`, `Discard this enrollment?`) với thân `Your entered information will not be saved.`,
    `Keep editing` và `Discard input`.

  Escape hoặc click vào nền của chính câu hỏi đó quay về dialog với mọi giá trị còn nguyên. Các dialog Administration bỏ
  qua yêu cầu đóng trong khi một lần lưu đang chạy. Các dialog workflow của Scan Station giữ nguyên §4.6 (Escape luôn hủy
  toàn bộ workflow), và dialog đăng nhập đóng ngay.

---

# 3. Quy tắc tương tác toàn cục

Các quy tắc này áp dụng cho mọi view. Chúng diễn đạt các nguyên tắc cốt lõi của PROJECT_PROFILE bằng thuật ngữ UI.

1. **Kỷ luật focus.** Trên Scan Station, ô nhập barcode tự động lấy lại focus sau mỗi thao tác hoàn tất, mỗi lần đóng
   dialog, mỗi lần đổi session và mỗi lần kích hoạt view. Không gì được phép chiếm focus vĩnh viễn. **Khi một lượt quét
   mở một dialog, focus chuyển vào dialog và ở lại đó:** Enter, Escape và Tab thuộc về dialog khi dialog đang mở, và không
   một lần refocus trì hoãn nào đang chờ được phép kéo focus ra khỏi một dialog modal đang mở. Focus chỉ trở về ô nhập
   barcode chính sau khi dialog đóng hoặc workflow hoàn tất. Việc bắt dữ liệu keyboard wedge vẫn áp dụng: khi không có
   dialog nào mở, một lượt quét được bắt trọn vẹn ngay cả khi ô nhập chính không có focus (§4.4).

2. **Mơ hồ thì phải xác nhận.** Bất cứ khi nào một lượt quét phân giải ra nhiều hơn một ngữ cảnh sản xuất hợp lệ, UI hiển
   thị một danh sách lựa chọn rõ ràng (§4.6). UI không bao giờ đoán và không bao giờ âm thầm chọn mặc định; không có gì
   được ghi nhận cho đến khi lựa chọn được đưa ra (PROJECT_PROFILE §10 Barcode Model).

3. **Phản hồi có ba trạng thái và tức thì.** Mỗi lượt quét tạo ra đúng một trong ba kết quả: Success (xanh lá), Warning
   (hổ phách: đã ghi nhận nhưng cần lưu ý, hoặc một action đang chờ), hoặc Error (đỏ: không có gì được ghi nhận). Phản hồi
   cho biết *điều gì đã xảy ra* và *tại sao*, mỗi ý một dòng. Trên Scan Station, phản hồi này hiển thị dưới dạng một **thông
   báo nổi** (§4.4) không chiếm chỗ trong bố cục: chỉ notice mới nhất hiển thị; notice có một nút đóng rõ ràng; notice
   success và trung tính tự đóng sau khoảng 4 s, còn warning và error sau khoảng 8 s; một notice thay thế sẽ khởi động
   lại bộ đếm thời gian. Banner OFFLINE / DISCONNECTED cố định (rule 6) **không** phải là thông báo: nó ở lại cho đến khi
   kết nối được khôi phục.

4. **Tính toàn vẹn của số lượng phải nhìn thấy được.** Việc nhập số lượng hiển thị số lượng nguồn khả dụng. Một nỗ lực
   chuyển nhiều hơn số khả dụng bị từ chối kèm một lỗi rõ ràng: UI giải thích giới hạn thay vì âm thầm cắt bớt
   (PROJECT_PROFILE §11 Quantity Model).

5. **Lịch sử cũng chỉ được nối thêm (append-only) trong UI.** Undo xuất hiện dưới dạng một mục `REVERSED` mới trong danh
   sách quét, và mục gốc vẫn hiển thị. Lịch sử Movement của Tracking không có tác vụ chỉnh sửa hay xóa nào
   (PROJECT_PROFILE §16).

6. **Mất kết nối là một trạng thái chặn ghi rõ ràng, được phát hiện nhanh.**
   - **Phát hiện** kết hợp các sự kiện `online`/`offline` của trình duyệt với việc thăm dò định kỳ (polling); UI không dùng
     kênh WebSocket hay server-sent events (SSE). Một sự kiện `offline` đánh dấu ứng dụng là không khả dụng ngay lập tức;
     một sự kiện `online` kích hoạt một lần kiểm tra sức khỏe (health check) ngay lập tức; `GET /api/health` được thăm dò
     khoảng mỗi giây, với thời gian chờ request ngắn hơn khoảng cách giữa các lần thăm dò và không có các lần thăm dò chồng
     lên nhau; kết nối được kiểm tra lại khi tab nhận focus hoặc khi trạng thái hiển thị của tab thay đổi. Các lần thăm dò
     thụ động không bao giờ chuyển UI sang trạng thái "connecting", nên không có hiện tượng nhấp nháy.
   - **Banner OFFLINE.** Mất kết nối sẽ hiện banner OFFLINE cố định của ứng dụng, và việc gửi ghi dữ liệu sản xuất bị vô
     hiệu hóa khi mất kết nối. **Câu chữ của banner đúng chính xác là
     `⚠ OFFLINE — Connection to the PartFlow server has been lost. Production actions are disabled`: một câu khẳng định
     gọn duy nhất, không có thêm câu giải thích nào** (không giải thích về hàng đợi quét, dữ liệu chỉ đọc hay cách khôi
     phục; chính hành vi chặn ghi là lời giải thích trên mọi bề mặt).
   - **Bố cục banner.** Banner hiển thị thành **hai vùng rõ ràng**, cùng cách phân chia với khối Undo của Scan Station
     (§4.5). Vùng thông điệp lấp đầy phần không gian còn lại bên trái; văn bản của nó xuống dòng tự nhiên trên màn hình
     hẹp, không tràn ngang. **`Retry connection` là một thanh action (action rail) độc lập ở sát mép phải hoàn toàn của
     banner**: toàn bộ vùng đó chính là phần tử nút (không bao giờ là một nút chữ nhỏ đặt sau thông điệp), cao bằng toàn
     bộ banner, không có khoảng hở hay phần nền thừa bên cạnh, và văn bản được căn giữa theo chiều dọc. **Không có phần tử
     phân cách và không có `|`**: một **đường kẻ dọc ngắn, thụt vào so với mép trên và mép dưới của banner**, do chính
     thanh rail vẽ và chỉ để trang trí, chia tách hai vùng; vùng nhấp và hover của thanh rail vẫn trải sát tới mép banner
     và tới tận đường kẻ đó. Thanh rail mang cách trình bày dùng chung `.zone-action`: nó **trong suốt khi ở trạng thái
     nghỉ**, nên trông như một phần bề mặt của chính banner và chỉ văn bản của nó mang màu lỗi; bề mặt lỗi màu sáng chỉ
     xuất hiện khi hover và khi đang nhấn (active), còn focus bàn phím (focus-visible) vẽ một đường viền (outline) tông lỗi
     bên trong vùng. Các trạng thái này áp dụng cho toàn bộ vùng.
   - **Trong khi mất kết nối.** Thông tin chỉ đọc đã được tải vẫn hiển thị ở những nơi khả thi. Không có thao tác ghi sản
     xuất nào được đưa vào hàng đợi cục bộ, không hiển thị chỉ báo Movement đang chờ nào, và UI không bao giờ khẳng định
     rằng các lượt quét sẽ được đồng bộ sau.
   - **Khi kết nối lại**, trạng thái sẵn sàng nhập và focus được khôi phục: ô nhập của Scan Station được bật lại và lấy
     lại focus.
   - **Một lượt quét chỉ thành công sau khi server xác nhận thao tác ghi.** Trạng thái kết nối là cảnh báo sớm, không bao
     giờ là sự cho phép ghi nhận một Movement một cách lạc quan. Nếu kết nối mất đi trong khoảng giữa nhịp heartbeat cuối
     cùng và một request ghi, request đó thất bại với kết quả "không có gì được ghi nhận"; UI không bao giờ được hiển thị
     một kết quả đã ghi nhận sai sự thật (PROJECT_PROFILE §15).
   - Quy tắc này phải được giữ nguyên không đổi trong các giai đoạn sản xuất. Việc đồng bộ lượt quét offline vẫn bị hoãn
     và chưa được duyệt (PROJECT_PROFILE §30, §32.4).

7. **Dùng từ vựng PartFlow ở mọi nơi.** Nhãn UI dùng các tên chuẩn: PN, WO / Work Order, Work Order Demand,
   Request Type (`NEW` / `MODIFY`), Route Mode (`FLOATING` / `PLANNED`), Quantity Flow, Route, các loại Movement
   (`RECEIVED`, `TRANSFERRED`, `ASSIGNED_TO_MACHINE`, `RELEASED_FROM_MACHINE`, `AREA_COMPLETED`, `SPLIT`, `MERGED`,
   `STOCKED`, `QUANTITY_ADJUSTED`, `SCRAPPED`, `REVERSED`, …), trạng thái DONE hướng tới người dùng cùng trạng thái giữ
   `READY_TO_TRANSFER` được suy ra từ nó (PROJECT_PROFILE §7 Area Completion), ý định di chuyển Repair
   (`movement_reason = REPAIR`, không bao giờ là một Request Type), Work Order Allocation, và Hot (PROJECT_PROFILE §7,
   §8.11). Một Work Order Number bên ngoài để trống hiển thị là `—` ở mọi nơi; đây chỉ là cách hiển thị và không bao giờ
   được lưu.

8. **Action một lần, không có trạng thái "nạp sẵn".** Không có Machine Session lâu dài, không có Action barcode được nạp
   sẵn (armed), và không có ý định PN đang chờ nào tồn tại qua một dialog. Mỗi action sản xuất của Scan Station là một
   dialog: mở, hoàn tất hoặc hủy, xóa ngữ cảnh, ô nhập lấy lại focus. Cancel luôn có nghĩa là không ghi gì.

9. **Thiếu ngày đến hạn là dữ liệu hợp lệ.** Ngày đến hạn của Work Order và của dòng demand có thể vắng mặt
   (PROJECT_PROFILE §8.2, §8.3). Một ngày đến hạn bị thiếu hiển thị là `No due date` ở nơi có dòng ghi chú và là `—` ở vị
   trí giá trị ngày dạng thu gọn hoặc trong bảng, một cách nhất quán, và không bao giờ hiển thị như trạng thái lỗi hay
   cảnh báo. Ở bất kỳ nơi nào demand được sắp xếp theo ngày đến hạn, thứ tự demand chuẩn của PROJECT_PROFILE §18 được áp
   dụng: demand có ngày trước, sớm nhất trước; demand không có ngày đứng sau toàn bộ demand có ngày, được sắp theo ngày
   nhận của Work Order cha, với một tiêu chí phân định hòa ổn định và xác định.

10. **Câu chữ chuyên nghiệp dành cho người vận hành, một cách triệt để.** Mọi chuỗi được hiển thị đều được phân loại vào
    đúng một trong các loại sau:
    - **Hướng tới người vận hành hoặc người dùng.** Chuỗi giải thích cần làm gì, điều gì là bắt buộc, điều gì xảy ra sau
      khi xác nhận, tại sao một action không khả dụng hoặc bị từ chối, và cách khôi phục. Chuỗi này không bao giờ nêu chi
      tiết triển khai.
    - **Hướng tới audit hoặc lịch sử.** Dữ liệu đã ghi nhận theo chuẩn: các loại Movement, worker, mốc thời gian, Area,
      Machine, số lượng và các quan hệ đảo ngược (reversal). Tên enum thô chỉ được chấp nhận ở đây, tức là trong lịch sử
      Movement, badge loại và hàng `Recorded event(s)` của phần tóm tắt xác nhận.
    - **Chỉ dành cho phát triển.** Barcode demo và ranh giới dữ liệu mẫu. Câu chữ này ngắn gọn, được cô lập rõ ràng sau
      cách trình bày `DevNotice` dùng chung (được đánh dấu dev), bị loại khỏi các bản build production, không bao giờ lấn
      át các workflow bình thường, và không bao giờ bị lặp lại theo từng toast, dialog hay footer: tối đa một notice phát
      triển ngắn gọn cho mỗi view.

    `DevNotice` dùng chung là một cấu trúc hai phần cố định: `.dev-notice-tag` thu gọn cộng với **một** phần tử
    `.dev-notice-content` bọc toàn bộ phần tử con thành một luồng văn bản inline duy nhất. Câu chữ dài chỉ xuống dòng ở
    ranh giới từ tự nhiên và không bao giờ bị chia ra thành các flex item riêng biệt. Notice lấp đầy chiều rộng khả dụng
    của khối cha (`width: 100%`, `box-sizing: border-box`, `min-width: 0`, không giới hạn `max-width`), và mỗi giá trị
    barcode `<code>` bên trong không thể bị ngắt (`white-space: nowrap`) trong khi câu vẫn xuống dòng giữa các giá trị.

    Câu chữ bình thường không bao giờ để lộ các cách diễn đạt như `(mock)`, `nothing persisted`, `local mock data`,
    `priority_rank`, `movement_reason`, `derived from Movement history`, hoặc các giải thích về kiến trúc. Thay vào đó,
    những phân biệt vận hành chính đáng được diễn đạt bằng ngôn ngữ của người dùng (ví dụ `Requested quantity`,
    `Current recorded location`, `Complete activity history`). Các nút hủy trong toàn bộ các workflow ghi đúng chính xác
    `Cancel (Esc)`.

    Một cơ chế kiểm soát câu chữ hiển thị tự động (`src/rendered-copy.test.ts`) quét các mã nguồn frontend có thể hiển thị
    (đã loại bỏ comment) và mockup hiện tại để tìm các cụm từ hướng tới lập trình viên bị cấm, với một cơ chế danh sách cho
    phép (allowlist) rõ ràng cho các bề mặt audit đã được duyệt. Bản thân các tên Movement chuẩn không bao giờ bị cấm.

11. **Không có thanh cuộn giả.** Khung ứng dụng (application shell) suy ra vùng nội dung khả dụng từ bố cục flex thông
    thường: phần gốc được định kích thước theo viewport động (`100dvh`, với dự phòng `100vh`), các hàng điều hướng giữ
    chiều cao tự nhiên của chúng, và vùng nội dung chỉ cuộn bên trong khi một view thực sự vượt quá nó. Một view có nội
    dung vừa khít hiển thị **không** có thanh cuộn dọc nào. Nội dung thực sự vượt quá viewport (bao gồm các bản xem trước
    `?state=long` dành cho phát triển) cuộn bình thường: phần tràn thật không bao giờ bị ẩn, và dialog, bảng dài và nội
    dung kiểm tra hợp lệ (validation) không bao giờ bị cắt xén. Không view nào lặp lại một phép tính chiều cao điều hướng
    cố định (`100vh − nav`), và thanh điều hướng vẫn hiển thị trong khi nội dung cuộn.

12. **Các giá trị thời gian suy ra dùng chung một đồng hồ UI.**
    - **Cố định và suy ra.** Các mốc thời gian gốc (thời điểm Movement, thay đổi trạng thái, ngày đến hạn, thời điểm vào
      Area) là dữ liệu cố định. Chỉ các giá trị trình bày **được suy ra** (khoảng thời gian đã trôi qua, đếm ngược đến
      hạn, số ngày trong sản xuất, đồng hồ trực tiếp) mới thay đổi theo thời gian thực.
    - **Một đồng hồ dùng chung.** Mọi view suy ra các giá trị đó tại thời điểm render từ mốc thời gian nguồn cố định cộng
      với một đồng hồ dùng chung duy nhất (`src/components/ui-clock.ts`, `useUiClock('minute' | 'second')`: một store
      đăng ký dùng chung cho mỗi độ chính xác, không bao giờ là các bộ hẹn giờ riêng cho từng component vốn sẽ lệch nhau).
      Các giá trị có độ chính xác phút render lại mỗi phút một lần và đồng hồ trực tiếp mỗi giây một lần, không bao giờ
      nhanh hơn mức độ chính xác của chúng cần. Sau một movement hoặc một thay đổi trạng thái, component bị ảnh hưởng render
      lại ngay lập tức và suy ra lại từ nhịp (tick) hiện tại. Các hàm định dạng giới hạn một mốc thời gian mới hơn nhịp
      hiện tại về `<1m` thay vì cho ra giá trị âm. Do đó cùng một mốc thời gian cho ra cùng một giá trị hiển thị trong mọi
      view tại cùng một thời điểm: Scan Station, Area Board, Production Board, Tracking và Machines không bao giờ có thể
      hiển thị khác nhau.
    - **Các helper dùng chung** nằm trong `src/views/dates.ts`:
      - `formatDuration` / `formatElapsedSince` / `elapsedMinutesSince`: một ngôn ngữ khoảng thời gian thu gọn duy nhất
        (`<1m`, `18m`, `1h 24m`, `2d 03h`);
      - `daysBetweenIso`;
      - `dueCountdown`: một ngôn ngữ đếm ngược duy nhất ở mọi nơi (`N days left`, `due today`, `overdue N days`,
        `No due date`), với `soon` nằm trong một **cửa sổ Due Soon tỉ lệ với thời gian chờ (lead time)**;
      - `daysInProductionNote` (`N d`).
    - **Cửa sổ Due Soon** là tỉ lệ phần trăm lead time của khoảng lead time từ ngày nhận → ngày đến hạn (tính toán số
      nguyên chính xác), được giới hạn trong khoảng [`minDays`, `maxDays`] và được `dueSoonWindowDays` suy ra từ
      `DueSoonPolicy` do nơi gọi cung cấp. Policy này là cấu hình **Due Soon warning** được lưu trên server
      (Administration → Settings, §9; giá trị ban đầu Minimum 2 days, 15 %, Maximum 7 days), được tải như một phần trạng
      thái sẵn sàng của mỗi view. Logic nghiệp vụ không bao giờ hard-code các con số này, không có giá trị mặc định phía
      frontend, và một lead time không xác định hoặc không hợp lệ sẽ dùng cửa sổ tối thiểu của policy. Chỉ riêng Scan
      Station đọc policy bên cạnh tồn kho Area của nó; khi không tải được policy, station vẫn giữ mọi action sản xuất và chỉ
      tạm không đưa ra đánh giá `soon`, kèm một notice rõ ràng. Một policy hiển thị không bao giờ chặn sản xuất.
    - Một khoảng thời gian hoặc giá trị đếm ngược đã định dạng không bao giờ được lưu trữ.
    - Các mock phát triển được viết bằng các độ lệch **tương đối**, được phân giải một lần khi module được tải
      (`src/mocks/mock-time.ts`: `minutesAgoIso`, `isoDateIn`), nên dữ liệu mẫu luôn hiển thị đúng tổ hợp trạng thái mong
      muốn bất kể bản build phát triển được mở lúc nào. Từ thời điểm đó trở đi, các giá trị là dữ liệu cố định, giống như
      các mốc thời gian thật từ backend.

13. **Release đã thay đổi là một trạng thái chặn ghi rõ ràng.** Một release là một phiên bản PartFlow đã được triển khai.
    - **Phát hiện.** Mọi request đều mang theo release của trang đang được tải. Server từ chối một thay đổi đến từ một
      trang thuộc release khác (không có gì được ghi nhận) và báo release của chính nó trong câu trả lời health của
      rule 6.
    - **Notice cập nhật.** Khi release khác nhau (trong một câu trả lời health hoặc trong một lần từ chối như vậy),
      **notice cập nhật** (update notice) cố định hiển thị ở vị trí của banner OFFLINE, với bố cục hai vùng của rule 6:
      câu chữ chính xác
      `⚠ UPDATED — PartFlow was updated on the server. Reload this page to continue. Production actions are disabled`,
      và thanh action rail `Reload page`. Việc gửi ghi dữ liệu sản xuất bị vô hiệu hóa đúng như khi mất kết nối, các thao
      tác đọc vẫn tiếp tục, và connectivity chip hiển thị `UPDATED`. Trong khi mất kết nối, banner OFFLINE hiển thị thay
      vào đó. Notice không lấy focus.
    - **Tải lại.** **Trang không bao giờ tự tải lại khi một dialog đang mở.** Trên các route Scan Station và Production
      Board (các station không người trông và màn hình treo tường), trang tự tải lại khi notice đã hiển thị được 60 s mà
      không có dialog nào mở, tối đa một lần mỗi 10 phút cho mỗi tab trình duyệt, và chỉ khi server đã phục vụ trang mới.
      Ở những nơi khác chỉ `Reload page` mới tải lại, sau cơ chế bảo vệ thay đổi chưa lưu sẵn có.
    - **Các dialog không thể đóng.** Modal đăng nhập Worker / `Worker session expired`, dialog đăng ký thiết bị
      (enrollment) (§4.12, §4.13) và dialog `Choose a new password` (§1.2) không thể bị đóng, nên trong khi trang đã lỗi
      thời, mỗi dialog hiển thị nút `Reload page` riêng của nó; dialog mật khẩu còn hiển thị
      `PartFlow was updated — reload the page to continue.` Trong khi modal đăng nhập Worker hoặc dialog enrollment đang
      mở, station không tự tải lại.
    - **Giám sát.** Các feed giám sát tiếp tục làm mới, nên trạng thái `● Live` của §5 vẫn ổn định, và notice nêu rõ lý
      do.
    - **Kết quả không xác định.** Một dialog có lần gửi trước đó không nhận được câu trả lời vẫn giữ trạng thái kết quả
      không xác định của nó. Trang đã lỗi thời không thể giải quyết trạng thái đó, nên sau khi tải lại, người vận hành
      kiểm tra Area (hoặc Work Order) trước khi lặp lại action.
    - **Câu chữ của Scan Station khi đã lỗi thời (chính xác):**
      - các ô nhập quét hiển thị `PartFlow was updated — reload to continue scanning`;
      - notice chặn quét hiển thị `PartFlow was updated — scanning is paused` /
        `Reload this page before continuing. No scans or production updates are recorded until then.`;
      - một thao tác ghi chưa được gửi hiển thị
        `PartFlow was updated — the {what} was not sent and nothing was recorded. Reload the page to continue.`;
      - dòng bị chặn của một dialog hiển thị `PartFlow was updated — the {what} cannot be recorded until this page is reloaded.`;
      - lý do enrollment hiển thị `PartFlow was updated — reload this page to enroll the device.`
    - **Các lý do vô hiệu hóa khác.** Các lý do vô hiệu hóa nêu tên kết nối ở những nơi khác (import Work Orders và dialog
      của nó, §11.7; đăng nhập; Edit assigned Route) hiển thị `PartFlow was updated — reload the page to continue.` trong
      khi đã lỗi thời. Mọi lý do vô hiệu hóa khác không thay đổi.
    - **Một view không thể tải.** Một view có mã không thể tải sau một release sẽ đưa ra `Reload page`
      (`This view could not be loaded — PartFlow may have been updated.` /
      `Reload the page to continue. The rest of the application is still available.`).
    - Các quy tắc nghiệp vụ nằm trong PROJECT_PROFILE; quy tắc này chỉ nêu những gì màn hình hiển thị.

---
# 4. Scan Station

Scan Station là một màn hình cho mỗi station, không có điều hướng trong quá trình sản xuất bình thường. Màn hình được tổ
chức xoay quanh Part Number và chỉ cung cấp các thao tác một lần (one-shot).

## 4.1 Định tuyến và chọn station

Có ba route:

- **`/scan-station` — Station Selector.** Nó không bao giờ tự động chuyển hướng tới một station. Mỗi Scan Station đang
  hoạt động hiển thị thành một thẻ (card) với đủ thông tin để phân biệt: Station ID, Department, Area (kèm màu của Area),
  các Operation được hỗ trợ dưới dạng **từng chip thông tin nhạt riêng lẻ** (cùng cách trình bày chip dùng chung với
  header của station, không bao giờ là văn bản nối bằng dấu phẩy), và Area có Machine hay không.
  - **Bề mặt chọn chính của thẻ là một button phủ toàn bộ thẻ**, mở station ở **standard mode**
    (→ `/scan-station/:stationId`).
  - Một button riêng đứng cạnh, có nhãn đúng chính xác là **`Production mode`**, mở `/scan-station/:stationId/production`.
  - Không có button `Open` độc lập và **không có control tương tác lồng nhau**: button phủ toàn thẻ chỉ chứa nội dung
    trình bày. Cả hai thao tác đều mang accessible name có Station ID
    (`Open LATHE-ST-01`, `Open LATHE-ST-01 in production mode`).
- **`/scan-station/:stationId` — một station ở standard mode** (ví dụ `/scan-station/LATHE-ST-01`). Route này giữ thanh
  điều hướng ứng dụng phía trên như bình thường (để Manager và Administrator sử dụng). Một Station ID không tồn tại hoặc
  không hoạt động sẽ hiện lỗi rõ ràng và không bao giờ âm thầm chuyển sang một station khác.
- **`/scan-station/:stationId/production` — cùng station đó ở production mode.** Thanh điều hướng ứng dụng phía trên (và
  cùng với nó là các liên kết Production Board, Management và Administration) bị ẩn, để người vận hành không thể tùy ý
  rời khỏi station đã được cấu hình.
  - Mode được thể hiện tường minh trong mô hình router (`mode: 'standard' | 'production'`) và trong pathname, không bao
    giờ là query parameter, cú nhấp ẩn hay thao tác bí mật.
  - Đây chỉ là một lựa chọn trình bày, **không bao giờ là ranh giới phân quyền hay bảo mật** (bản thân trình duyệt vẫn
    nằm ngoài tầm kiểm soát của PartFlow).
  - Mọi thứ khác được giữ nguyên: banner OFFLINE thường trực và thông báo cập nhật (§3 rule 13), Worker Session, toàn bộ
    hành vi scan, DONE, QUEUE, Undo, dialog và thông báo, cùng chiều cao chiếm toàn viewport mà không còn khoảng dịch
    dành cho thanh điều hướng trên.
  - Trạng thái kết nối vẫn hiển thị qua chip kết nối bên trong header của station, với **control Dark/Light mode gọn,
    không viền** (cùng component theme dùng chung và cùng trạng thái toàn cục như nút bật/tắt trên thanh điều hướng,
    §2.1) **ngay bên dưới nó**, trong một cột header-actions dọc, gọn, căn phải. Cột hẹp này không bao giờ chèn ép
    Worker Session và không bao giờ buộc thống kê Area xuống dòng xấu ở các độ rộng trung gian.

**Ràng buộc chiều cao của nhóm đầu (head group).** Pill Worker Session giữ **chiều cao tự nhiên ở cả hai mode**; nó
không bao giờ bị kéo giãn. `.ss-headgroup` dùng `align-items: flex-start`. Cột action **không khung** (`.ss-headactions`)
nhận `align-self: stretch` với `justify-content: space-between`, và padding gọn trên chip ONLINE và nút theme giữ tổng
chiều cao tự nhiên của các action thấp hơn chiều cao của pill, nên pill luôn là yếu tố quyết định chiều cao. Chip
`ONLINE` (vẫn giữ khung chip riêng của nó; không có card nhóm và không có separator) căn mép trên thẳng với viền trên
của pill, và control theme căn mép dưới thẳng với viền dưới của pill. Nhóm action của header không bao giờ có thể quyết
định chiều cao của Worker Session.

Cả hai mode đều yêu cầu trình duyệt là một thiết bị đã enroll của Scan Station đó (§4.13). Standard mode trên máy tính
riêng của Manager hoặc Administrator được enroll như mọi thiết bị khác.

**Footer.** Cả hai mode dùng chung **một footer thống nhất, không tương tác**: Station ID mờ, một nhãn mode tinh tế
không tương tác (**`Standard mode`** hoặc **`Production mode`**), và gợi ý phím tắt ngắn gọn
**`Ctrl+Shift+K: switch mode`**.

- Station ID không bao giờ là button ở bất kỳ mode nào. Mục **Scan Station** trên thanh điều hướng trên đã đưa người dùng
  quay về Station Selector, và footer không bao giờ lặp lại hành vi đó.
- Phím tắt **Ctrl+Shift+K** chuyển qua lại giữa route standard và route production của cùng station. Nó hoạt động từ
  ô nhập barcode chính (một đích của scanner mà cơ chế bắt wedge bỏ qua khi có tổ hợp phím bổ trợ) nhưng không có tác
  dụng trong mọi text field, select hoặc dialog đang mở khác. Route riêng và các thao tác tường minh trên Station
  Selector vẫn là lối vào chính.

## 4.2 Bố cục

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ Dept / AREA (color) / Operations   [Area statistics][Worker session][●Online] │
│ (disconnected banner when connectivity is lost — writes blocked)              │
├──────────────────────────────────────────────────────────────────────────────┤
│ Scan barcode card (full width)                                               │
│   Scan input (large, focused) …………………………… [⌨ Enter PN manually]              │
│   Last scanned PN ……………………………………………………… [⟲ UNDO]                            │
├────────────────────┬─────────────────────────────────────────────────────────┤
│ In this Area now   │ Machine cards grid                                      │
│ (fixed left col,   │ [Machine 1][Machine 2][Machine 3]                       │
│  grows vertically) │ [Machine 4][Machine 5]                                  │
├────────────────────┴─────────────────────────────────────────────────────────┤
│ Station LATHE-ST-01 · Standard mode · Ctrl+Shift+K: switch mode (faint)      │
└──────────────────────────────────────────────────────────────────────────────┘
                                        [floating scan notification — no
                                         reserved layout space, bottom edge]
```

Các card dùng cùng token đổ bóng ngữ nghĩa nhẹ như các card của Management (`box-shadow: var(--shadow)`); không có hệ
thống bóng đậm hay hiệu ứng phát sáng trang trí riêng.

## 4.3 Header

**Lưới.** Header là một lưới (grid) tường minh, không xuống dòng dựa trên spacer.

- Nhóm nhận diện bên trái (Department, tên Area kèm màu Area, các Operation được hỗ trợ) và pill **Worker** (không có ở
  Area Disabled, xem bên dưới) luôn cùng nằm trên hàng chính.
- **Thống kê Area** nằm giữa hai nhóm đó khi còn đủ chỗ theo chiều ngang. Chúng chỉ rơi xuống một hàng thứ hai rộng toàn
  bộ, với các ô chia đều chiều rộng, khi bố cục một hàng thật sự không vừa. Điều này được quyết định bằng một phép đo độ
  vừa thực tế trên toàn bộ chiều rộng tự nhiên của ba ô header, không bao giờ bằng breakpoint cố định trong code. Nhóm
  nhận diện giữ Department, tên Area và toàn bộ hàng Operations (nhãn cộng mọi chip) mỗi thứ trên một dòng, nên hàng
  Operations không bao giờ bỏ bớt gì trước khi thống kê rơi xuống.
- Worker session không bao giờ xuống thành một hàng riêng. Pill có **kích thước theo nội dung** (không có chiều rộng cố
  định hay tối thiểu), và tên Worker cùng các dòng phụ của nó không bao giờ xuống dòng.
- **Không có pill Machine session, không có dải trạng thái Machine và không có trạng thái Machine đang hoạt động
  thường trực**.

**Pill Worker tuân theo Worker ID mode của Area.** Không có khái niệm kết thúc ca (shift-end) ở bất kỳ đâu. Pill là một
cấu trúc hai dòng: **avatar có màu nền nhạt, không viền, trải qua cả hai dòng chữ** (hình tròn của nó cao bằng dòng tên
và dòng phụ cộng lại), với tên Worker ở dòng thứ nhất và dòng phụ về mode hoặc đếm ngược nằm bên dưới.

| Worker ID mode   | Nội dung pill |
|------------------|--------------|
| Scanned session  | Avatar và tên của Worker đang hoạt động (`[avatar] H. Nguyen`) với dòng phụ đếm ngược phiên trực tiếp `Session: 10m 23s`. Phút và giây giảm mỗi giây ở độ đậm thường (không in đậm) của dòng phụ, với giá trị thời gian ở **tông success**, lấy từ đồng hồ UI dùng chung (§3 rule 12). Khi gần hết hạn (≤ 2 phút), giá trị và dòng chuyển sang **tông warning**; đây chỉ là thay đổi màu, không bao giờ là thay đổi độ đậm. Khi không có phiên đang hoạt động, pill hiển thị `No Worker` phía trên `Session · scan badge`. |
| Fixed Worker     | Avatar và tên của Worker đã cấu hình với dòng phụ tĩnh `Fixed Worker`; không có đếm ngược. |
| Disabled         | **Hoàn toàn không hiển thị pill Worker.** Danh tính Worker không tồn tại trong Area như vậy, header đơn giản kết thúc sau thống kê Area, và bản thân mode vẫn hiển thị trong Administration → Areas (§9). |

Hành vi phiên, đăng nhập và hết hạn được đặc tả trong §4.12.

**Các chip Operations.** Mỗi Operation được hỗ trợ hiển thị thành một chip thông tin nhạt riêng lẻ: nền panel nhẹ, viền
nhẹ, bo góc, dễ đọc ở cả hai theme, không có màu action và không có hiệu ứng hover giống button. Header của station giữ
Operations trên **một dòng ở mọi độ rộng**, bỏ bớt cách trình bày theo các bậc được đo (bên dưới) thay vì xuống dòng;
các thẻ của Station Selector (§4.1) vẫn cho chip xuống dòng. Tên Area vẫn nổi bật hơn về mặt thị giác. Tên Operation
không bao giờ bị phân tích hay sửa đổi. Các chip là nhãn, không phải control.

**Header hẹp: trình bày Operations theo bậc.** Khi ô nhận diện thật sự không thể chứa toàn bộ hàng một dòng
`Operations: <chips>` (một phép đo độ vừa thực tế so với một bản sao ẩn (ghost) của hàng ở chiều rộng tự nhiên, không
bao giờ là breakpoint cố định trong code), hàng sẽ bỏ bớt cách trình bày theo thứ tự sau:

1. nhãn `Operations:` bị bỏ và các chip giữ dòng một mình;
2. các chip ở cuối được thay bằng một chip `…` có tooltip nêu tên các Operation bị ẩn;
3. khi ngay cả chip đầu tiên cũng không vừa bên cạnh dấu ba chấm, cả hàng bị ẩn.

Phép đo độ vừa của header mô tả ở trên đo ô nhận diện ở toàn bộ chiều rộng tự nhiên của nó (nhãn cộng mọi chip), nên
tổng số liệu của Area luôn rơi xuống hàng thứ hai trước khi hàng Operations bỏ bớt bất cứ thứ gì. Khi có lại chỗ, cách
trình bày đầy đủ được khôi phục.

**Tổng số liệu Area với tông ngữ nghĩa.** Header là **bề mặt tóm tắt duy nhất** cho tổng số liệu Area; card
`In this Area now` không chứa khối thống kê nào (§4.10).

- Area có Machine: Total PNs · Total pcs · Queued · On machines · Done · Hot, cân đối theo
  `Total pcs = Queued + On machines + Done`.
- Area không có Machine: Total PNs · Total pcs · Processing · Done · Hot, cân đối theo `Total pcs = Processing + Done`.

Tông màu của số tuân theo ánh xạ tông thống kê dùng chung của phần trình bày Area. **Tổng PN (Total PNs / PNs) mang màu
trung tính chữ chính (màu chữ chính) trên mọi view, còn Total pcs giữ màu trung tính phụ dịu, cùng màu với chữ của dòng
tổng trên Machine card.** Các thống kê trạng thái giữ tông ngữ nghĩa của chúng: Queued là warning, On machines /
Processing là information, Done là success, Hot là error. Mọi tông đều là token ngữ nghĩa, không bao giờ là màu cố định
trong code riêng cho từng theme. Các giá trị số lượng cân đối luôn hiển thị dạng số; Hot hiển thị `—` khi bằng không.

## 4.4 Card quét barcode

Card Scan Barcode nằm ngay dưới header và trải hết chiều rộng khả dụng. Ô nhập chính hỗ trợ **barcode PN**, **barcode
Worker**, và **barcode Machine chỉ như một lối tắt một lần** (§4.6). Không tồn tại barcode action: `PF:SCRAP` ở ô nhập
chính bị từ chối, vì nó chỉ có hiệu lực bên trong workflow Scrap (§4.9). Barcode không xác định và văn bản PN thô bị
từ chối và không ghi nhận gì.

**Không có button ENTER riêng**: scanner gửi bằng Enter, và placeholder của ô nhập nói rõ điều đó:
`Scan Part Number, Worker, or Machine barcode · Press Enter`. Ô nhập barcode vẫn là control chính và lớn nhất.

**Bắt keyboard wedge: ô nhập chính không bao giờ để mất một lần quét.**

- Khi không có dialog nào mở, barcode được quét vẫn tới ô nhập chính kể cả khi ô nhập không có focus: ký tự quét đầu
  tiên được bắt lại (không bao giờ bị mất) và sau đó focus đi theo lần quét. Một barcode kết thúc bằng Enter được gửi
  đúng một lần.
- Cơ chế bắt không bao giờ chặn việc gõ phím khi focus đang ở trong một text input khác, một textarea, một select, một
  phần tử contenteditable hoặc một workflow dialog đang hoạt động. Nó không bao giờ can thiệp vào phím tắt Ctrl/Alt/Meta,
  việc kích hoạt button bình thường hay điều hướng trợ năng, và không bao giờ chỉ đơn thuần ép focus sau mỗi cú nhấp.
- Sau khi xử lý, hủy, đóng dialog, đổi Worker, kết nối lại hoặc hoàn tất một thao tác, focus được trả về ô nhập barcode
  chính khi phù hợp.
- Dữ liệu nhập không xác định hoặc mơ hồ không bao giờ cập nhật dữ liệu tracking.

**Thiết bị cảm ứng là chính (touch-primary).** Trên thiết bị touch-primary, ô nhập barcode chính hiển thị với
`inputMode="none"`, nên chạm vào nó không bao giờ bật bàn phím ảo của hệ thống: ô nhập được điều khiển bởi scanner hoặc
bàn phím vật lý và không bao giờ là một text field thông thường. Việc phát hiện dùng cùng module phát hiện khả năng
thiết bị dùng chung với ô nhập số lượng (`src/components/touch-device.ts`, §4.8). Bàn phím vật lý, scanner wedge, hành
vi focus và ô nhập của dialog nhập thủ công (một field bàn phím bình thường) không bị ảnh hưởng.

**Nhập PN thủ công** vẫn là một thao tác phụ hiển thị rõ (**⌨ Enter PN manually**) đặt **ngay trong hàng quét**, ở mép
phải của ô nhập: rõ ràng là thứ yếu so với việc quét nhưng đủ lớn để dùng tại xưởng.

- Trên màn hình hẹp, button co lại **trước khi** ô nhập barcode phải nhường chiều rộng. Nó co còn khoảng một nửa chiều
  rộng tự nhiên, cho nhãn xuống thành hai dòng nhưng không bao giờ hẹp hơn từ dài nhất của nó, trong khi ô nhập giữ phần
  chiều rộng còn lại của hàng. Đây là hành vi flex liên tục, không có breakpoint cố định trong code, và button không bao
  giờ rơi xuống dưới ô nhập.
- Nó chấp nhận **mọi giá trị PN không rỗng sau khi trim và không chứa khoảng trắng bên trong** (PROJECT_PROFILE §7,
  §8.1; PN được tạo ở lần sử dụng hợp lệ đầu tiên). Khoảng trắng bao quanh được trim, chữ thường được chấp nhận và chuẩn
  hóa thành PN chữ hoa, còn giá trị có khoảng trắng bên trong bị từ chối kèm giải thích ngay tại chỗ và không bao giờ bị
  âm thầm làm sạch. Giá trị được phân giải thành PN chuẩn (canonical) đúng như một lần quét.
- Phần giải thích phương án dự phòng chỉ còn là một dòng gợi ý nhỏ bên dưới hàng quét; không có hàng button riêng.

**Thứ tự nội dung cố định của card.** Thứ tự DOM của card chính là thứ tự thị giác, thứ tự bàn phím và thứ tự của trình
đọc màn hình, và không bao giờ bị CSS sắp xếp lại:

1. hàng ô nhập barcode chính với **⌨ Enter PN manually**;
2. phần giải thích phương án nhập thủ công ngay bên dưới hàng đó;
3. các barcode demo `DevNotice` dùng chung;
4. phần `LAST SCANNED PN` (§4.5).

**Gợi ý barcode demo.** Nội dung chỉ dành cho development "Demo barcodes (development build only) — …" hiển thị qua
component `DevNotice` dùng chung, cùng khối development như các view Management (§3 rule 10), không qua một khối gợi ý
làm riêng. `.dev-notice code` định kiểu các giá trị barcode: mỗi giá trị không bị ngắt dòng trong khi câu văn xuống dòng
giữa các giá trị, trong một luồng văn bản inline duy nhất rộng toàn bộ của notice (§3 rule 10).

**Thông báo quét nổi.** Phản hồi quét không giữ chỗ bố cục trong card. Nó hiển thị thành một thông báo nổi gần mép dưới
của viewport:

- chỉ thông báo gần nhất được hiển thị, và một button đóng `×` tường minh sẽ tắt nó;
- thông báo success và trung tính tự tắt sau khoảng 4 s, warning và error sau khoảng 8 s; một thông báo thay thế khởi
  động lại bộ đếm giờ, và các bộ đếm giờ được dọn khi bị thay thế và khi unmount;
- thông báo dùng kiểu success, warning và error ngữ nghĩa cùng ngữ nghĩa live-region alert hoặc status, nằm trong
  viewport trên màn hình hẹp, và không bao giờ che ô nhập barcode chính hoặc các action của dialog.

Banner ứng dụng OFFLINE / DISCONNECTED thường trực không phải là thông báo và vẫn hiển thị cho tới khi kết nối được khôi
phục. `Last scanned PN` vẫn cố định bên trong card Scan Barcode; chỉ bề mặt phản hồi là nổi.

## 4.5 Last scanned PN và Undo

Bề mặt Last Scanned PN nằm bên trong card Scan Barcode, với **action Undo neo vào mép phải của nó**. Không có card Undo
riêng và **không có danh sách Recent Scans**.

**Trình bày.**

- Nhãn section chữ hoa gọn nằm **bên ngoài** khối: căn trái, ngay phía trên khối, không bao giờ ở bên trong bề mặt có
  viền.
- Bản thân khối gồm **hai vùng tường minh**, cùng cách chia như một thẻ của Station Selector:
  - **vùng thông tin** (PN, không bao giờ bị xuống dòng tùy tiện, với phần tóm tắt movement bên cạnh) lấp đầy phần
    không gian còn lại ở bên trái;
  - **vùng action `⟲ UNDO`** CHÍNH LÀ toàn bộ mép phải của khối. Toàn bộ vùng là chính phần tử button (không bao giờ là
    một button chữ nhỏ bên trong một container lớn hơn), sát mép phải và cao bằng toàn bộ khối, không có khoảng hở,
    spacer hay phần nền thừa bên cạnh (khối cắt bỏ phần tràn của các phần tử con). Icon và chữ của nó được căn giữa cả
    theo chiều ngang lẫn chiều dọc.
- **Không có phần tử separator và không có ký tự `|`**. Một **đường kẻ dọc ngắn, thụt vào so với mép trên và mép dưới
  của khối**, do chính vùng action vẽ và chỉ để trang trí, ngăn cách nó với vùng thông tin; vùng nhấp và hover của nó
  vẫn trải sát tới viền khối và tới tận đường kẻ đó. Vùng mang cách trình bày `.zone-action` dùng chung (§3 rule 6) trên
  toàn vùng: trong suốt khi nghỉ, nên chỉ icon và chữ mang màu error, bề mặt error nhạt khi hover và khi đang nhấn
  (active), và outline focus-visible tông error dùng chung.
- Khi hẹp, PN giữ hàng thứ nhất và phần tóm tắt chuyển xuống hàng thứ hai riêng, rộng toàn bộ bên trong vùng thông tin,
  trong khi vùng action tiếp tục phủ toàn bộ chiều cao khối ở mép phải. Không bao giờ có tràn ngang.
- Khối không có margin dưới: padding của card cha là khoảng cách bên ngoài duy nhất bên dưới nó.
- Undo bị vô hiệu hóa khiến toàn bộ vùng action không thể nhấp nhưng vẫn giữ bố cục và đường kẻ phân cách của nó
  (không thay đổi kích thước). Icon, chữ và đường kẻ phân cách cùng mờ đi rõ ràng, và không xuất hiện bề mặt hover;
  vùng thông tin không bao giờ bị mờ.

**Hành vi.**

- Lần quét Worker không bao giờ thay thế Last Scanned PN, và dialog bị hủy cũng không bao giờ thay thế nó. Chỉ các thao
  tác liên quan đến PN đã hoàn tất mới trở thành Last Scanned PN.
- Undo nhắm tới thao tác PN đã hoàn tất, đủ điều kiện, gần nhất và luôn hiển thị bản tóm tắt xác nhận trước: PN, thao
  tác gốc, số lượng, nguồn và đích, Machine nếu có, Worker, thời điểm, và tác động của việc đảo ngược. Nó dùng cùng bố
  cục tóm tắt xác nhận có cấu trúc như các wizard sản xuất (§4.6). Cancel không ghi gì.
- Undo tạo lịch sử bù trừ (đảo ngược); thao tác gốc không bao giờ bị xóa. Sau một Undo đã xác nhận, Last Scanned PN lùi
  tới thao tác PN trước đó đủ điều kiện kế tiếp. Khi không còn gì đủ điều kiện, Undo bị vô hiệu hóa.
- **Danh tính Worker cho Undo** tuân theo Worker ID mode của Area (§4.12): Disabled không ghi Worker; Fixed Worker ghi
  Worker đã cấu hình; Scanned session với phiên hợp lệ ghi **Worker đang hoạt động tại thời điểm Undo được xác nhận** vào
  bản ghi (audit) `REVERSED`. Phần xác nhận hiển thị Worker của thao tác gốc làm ngữ cảnh và Worker thực hiện đảo ngược
  một cách riêng biệt.
- **Cổng cuối cho Undo.** Sau `Confirm reversal`, Undo mang **cổng xác nhận cuối (final-confirmation gate)** dùng chung
  (§4.6):
  - trong một Area Scanned session với tùy chọn `UNDO` của Administration BẬT (mặc định), một **cổng quét badge** hoàn
    tất việc đảo ngược; bất kỳ badge Worker đang hoạt động nào cũng xác nhận được và nhận diện Worker xác nhận;
  - trong mọi trường hợp khác (Area Fixed-Worker hoặc Disabled, hoặc tùy chọn TẮT), một **câu hỏi cuối mang tông
    warning** (`Reverse this action?`) nhắc lại các dữ kiện chính trước khi bất cứ điều gì bị đảo ngược.

  Bản thân cổng cuối không bao giờ bị bỏ qua. Ở chế độ Scanned session khi không có phiên đang hoạt động hoặc phiên đã
  hết hạn, badge modal của station (§4.12) đã chặn bề mặt: bắt buộc phải có một lần quét badge hợp lệ trước khi Undo có
  thể mở hoặc xác nhận.
- **Lý do Undo.** Khi Administration → Correction permissions → **Undo reason** đang On, bản tóm tắt Undo hiển thị một
  field `Reason` bắt buộc (nhãn `Reason (required)`, gợi ý `This reason will be included in the reversal history.`)
  bên dưới bản tóm tắt và trước cổng cuối. `Confirm reversal` vẫn bị vô hiệu hóa cho tới khi field có nội dung, cổng
  cuối nhắc lại lý do cùng các dữ kiện chính, và lý do được ghi vào bản đảo ngược và hiển thị trong Tracking. Một lần
  đảo ngược bị server từ chối vì thiếu lý do sẽ không ghi gì: bản tóm tắt hiển thị field kèm giải thích của server và
  giữ mọi lựa chọn. Khi tùy chọn Off (mặc định), không hiển thị field lý do.
- Production Undo vận hành trên toàn bộ application command (PROJECT_PROFILE §16), không trên một dòng Movement tùy ý.
- **Undo không được cấp.** Khi role áp dụng tại Scan Station không cấp quyền Undo, vùng action `⟲ UNDO` không được hiển
  thị. Khối Last Scanned PN khi đó chỉ có vùng thông tin, trải hết chiều rộng của khối, không có đường kẻ phân cách
  và không có vùng action trống. Quy tắc hai hàng trên màn hình hẹp vẫn áp dụng.

## 4.6 Workflow một lần — wizard tạm thời

Không có Machine Session thường trực và không có ý định PN (PN intent) thường trực sau khi một dialog hoàn tất hoặc bị
hủy. Mọi thao tác sản xuất là **một wizard tạm thời trong một vòng đời dialog**:

1. mở;
2. chọn hoặc nhập thông tin;
3. xem lại một view xác nhận riêng;
4. xác nhận hoặc hủy;
5. toàn bộ trạng thái tạm thời được xóa;
6. focus được trả về ô nhập barcode chính.

Các bước là các view hiển thị bên trong cùng một modal dialog. Chúng không bao giờ là dialog lồng nhau, và lớp phủ
(overlay) không bao giờ đóng rồi mở lại giữa các bước.

### Quy tắc wizard (mọi workflow)

- **Không có thao tác sản xuất nào được ghi nhận trước bước xác nhận cuối.** Các bước chọn và nhập không bao giờ ghi.
- **Bản tóm tắt xác nhận có cấu trúc.** View cuối là một bố cục hai cột thuật ngữ/giá trị chỉ hiển thị các field áp
  dụng: Action, PN, Quantity, Request Type, Route Mode, Planned Route, hành vi Work Order, Source, Destination, Machine,
  Area/Operation, Due date, Reason/notes, Worker, Scan Station, và trạng thái hoặc tác động số lượng kết quả. Nó không
  bao giờ là một câu dài được ghép chuỗi và không bao giờ là một ghi chú trong khung viền nét đứt.
  - **Nhấn mạnh hàng.** Một cơ chế nhấn mạnh hàng nhỏ, tùy chọn làm cho các giá trị vận hành chính (PN, Quantity,
    Action, Source, Destination, Machine, Area, Operation, và Reason khi nó quan trọng về mặt vận hành) nổi bật hơn. Các
    thực thể đã chọn có thể hiển thị thành chip nhẹ không tương tác. Các hàng audit và ngữ cảnh thứ yếu (Worker, Scan
    Station, tên sự kiện được ghi nhận, ghi chú trong ngoặc) vẫn hiện diện nhưng dịu về mặt thị giác.
  - **Bố cục kiểm tra.** Cột nhãn của bản tóm tắt có kích thước theo nội dung (`fit-content(230px)`), nên giá trị nằm
    gần nhãn thay vì trôi xa qua một khoảng cách độ rộng cố định. Các chip thực thể bên trong bản tóm tắt xác nhận
    **được làm phẳng thành văn bản kiểm tra thuần** kế thừa kích thước và độ đậm của hàng: một bản xác nhận là để đọc và
    kiểm tra, không bao giờ là một tập phần tử giống badge. PN giữ đặc trưng monospace; phần tóm tắt các bước (step
    recap) và các bước chọn vẫn giữ dáng chip.
  - Không có gì bị ẩn, nhãn không bao giờ trông giống button, độ tương phản được giữ ở cả hai theme, giá trị dài xuống
    dòng an toàn, và màu sắc không bao giờ là yếu tố phân biệt duy nhất.
  - **Tông giá trị ngữ nghĩa.** Giá trị trong bản tóm tắt có thể mang một tông ngữ nghĩa có kiểm soát: success và kết
    quả được ghi nhận dùng tông success, sai lệch dùng tông warning, và số lượng mang tính hủy dùng tông error, còn
    Worker, Scan Station và thời điểm vẫn dịu. Giá trị Area (Source, Destination, Area, `Area queue`) mang chấm màu Area
    bên cạnh văn bản kiểm tra thuần đã làm phẳng. Các tông là bổ sung, không bao giờ là yếu tố phân biệt duy nhất, và
    không bao giờ mỗi field một màu.
  - Cách trình bày giống nhau áp dụng cho mọi dialog xác nhận, kể cả bản tóm tắt Undo.
- **Các button xác nhận.** View xác nhận có **Back**, **Cancel (Esc)** và một **Confirm cuối có nhãn nêu tên thao tác**
  (`Confirm receipt`, `Confirm assignment`, `Confirm transfer`, `Confirm addition`, `Confirm repair`,
  `Confirm scrap`, `Confirm return to queue`). Enter trên view xác nhận kích hoạt Confirm cuối khi thao tác vẫn còn hợp
  lệ.
- **Cổng xác nhận cuối cho các thao tác nhạy cảm.** `DONE` (Complete Area processing), `QUEUE` (Return unfinished
  quantity to queue) và Undo (§4.5) thêm MỘT bước nữa sau bản tóm tắt xác nhận của chúng, LUÔN LUÔN, ở mọi Area. Mỗi
  thao tác có tùy chọn Administration → Worker sessions riêng (mặc định BẬT; §9, PROJECT_PROFILE §19), tùy chọn này chỉ
  quyết định bước cuối đó có phải là một lần quét badge Worker ở nơi có badge hay không.
  - **Cổng quét badge.** Trong một Area **Scanned-session** với tùy chọn BẬT, thao tác xác nhận mở một cổng quét badge
    theo cách trình bày attention dùng chung: **tông information màu xanh dương cho DONE**, và **tông warning cho việc
    trả về QUEUE và Undo** (họ tông toàn cục, §12.4). Các dữ kiện chính của thao tác đang chờ vẫn hiển thị với **PN được
    nhấn mạnh bằng chữ đậm** (`Are you sure Lathe 3 has finished 2 of 5 pcs of **{PN}**?` /
    `Are you sure you want to return 4 of 10 pcs of **{PN}** running on Lathe 2 back to the {Area} queue?` / các dữ
    kiện của việc đảo ngược). **Bất kỳ badge Worker ĐANG HOẠT ĐỘNG nào cũng xác nhận được.** Badge nhận diện Worker xác
    nhận, người được ghi vào thao tác và được đăng nhập đúng như bằng một lần quét badge bình thường. Badge không xác định
    hoặc không hoạt động bị từ chối ngay tại chỗ. Cancel hoặc Escape trả về bản tóm tắt mà không ghi gì.
  - **Câu hỏi xác nhận cuối.** Trong mọi trường hợp khác (một Area **Fixed-Worker hoặc Disabled**, hoặc một Area
    Scanned-session với tùy chọn TẮT), bước cuối thay vào đó là một câu hỏi xác nhận cuối, theo cách trình bày câu hỏi
    cuối (last-question) dùng chung của Machines (§12.4). Nó hiển thị cùng các dữ kiện chính với cùng tông như các cổng
    badge: **tông information** cho DONE (`Confirm finished quantity?` / `Yes — finished`), và **biến thể attention
    warning** cho QUEUE (`Return unfinished quantity?` / `Yes — return to queue`) và cho Undo
    (`Reverse this action?` / `Yes — reverse it`). Cancel trả về bản tóm tắt.
  - Bản thân cổng cuối không bao giờ là tùy chọn; không cấu hình nào loại bỏ bước này. Không có gì được ghi nhận trước
    khi cổng hoàn tất.
- **Back giữ lại mọi giá trị đã nhập trước đó.** Nó chỉ xuất hiện khi tồn tại một view trước đó có ý nghĩa, không bao
  giờ như một Cancel thứ hai.
- **Back về dialog cha.** Một view trước đó có ý nghĩa bao gồm dialog hoặc bước mà dialog hiện tại được mở từ đó trong
  cùng một workflow:
  - mọi dialog mở từ dialog action của PN (gán Machine, nhận/chuyển, chọn nguồn, DONE, thêm số lượng, repair, scrap)
    đều cung cấp Back về dialog action đó;
  - một lần chuyển (transfer) mở từ bước chọn nguồn quay về bước chọn đó với mọi nguồn còn nguyên;
  - một dialog được phân giải từ nhập PN thủ công quay về bước nhập thủ công với PN đã nhập được giữ lại để sửa;
  - khi một dialog có thể tới được qua nhiều lối vào, Back quay về đúng bước mà người dùng thực sự đã đi từ đó (bước
    trước đã lưu của workflow hiện tại, với ngữ cảnh như PN, nguồn và các lựa chọn còn nguyên).

  Back không bao giờ ghi hoặc thay đổi dữ liệu tracking. Các workflow được vào trực tiếp từ bề mặt Scan Station (một
  lần quét, một action trên hàng monitoring, Undo) không có bước dialog trước đó và **không** hiển thị Back; Cancel vẫn
  là lối ra duy nhất ở đó.
- **Escape luôn hủy toàn bộ workflow** từ bất kỳ bước nào và không ghi gì. Workflow bị hủy không bao giờ cập nhật Last
  Scanned PN.
- **Các view chọn** (chọn nguồn, "Select an action") vẫn là view chọn; chúng không phải là bước xác nhận bổ sung.
- **Thứ bậc thông tin của dialog.** Mọi dialog giữ bốn lớp tách biệt rõ về mặt thị giác:
  1. một **mô tả** trung tính, ngắn gọn (mục đích và hệ quả vận hành, không có những khối chữ dài);
  2. một **step recap** thứ yếu, gọn về các lựa chọn trước đó (các thực thể đã chọn quan trọng như Machine, Area,
     Operation hoặc nguồn có thể hiển thị thành chip nhẹ không tương tác);
  3. **hướng dẫn** ngay phía trên ô nhập liên quan (§4.8);
  4. **kiểm tra hợp lệ (validation)** nổi bật hơn bất kỳ chỉ dẫn nào, với lời lẽ có thể hành động được và dữ liệu đã
     nhập được giữ lại.
- **Focus đi theo các bước.** Control hữu ích đầu tiên của mỗi bước nhận focus: ô nhập số lượng được focus với giá trị
  được chọn sẵn ở các bước số lượng, và ô nhập chọn barcode ở Step 1 của việc gán. Back trả focus về control trước đó
  liên quan, và đóng hoặc hoàn tất wizard trả focus về ô nhập barcode chính. Trạng thái wizard là trạng thái cục bộ của
  dialog: không có Context, và không có trạng thái Machine hay PN ẩn nào tồn tại sau wizard. **Bước thiết lập của
  Receive Quantity là ngoại lệ có chủ ý:** nó mở ra mà KHÔNG có field nào được focus; gốc dialog giữ focus, nên một lần
  Enter ngay lập tức sẽ chuyển sang bước số lượng thay vì bị một select đang có focus nuốt mất. Mọi field vẫn có thể
  tới được bằng Tab hoặc chạm.

### Machine trước (Machine-first)

Quét một barcode Machine chỉ đóng vai trò lối tắt: nó mở wizard gán Machine, tiêu đề dialog **`Assign to Machine`**,
với ba view trong một modal dialog và Machine được chọn sẵn. Hành vi một lần được giải thích bằng ngôn ngữ tự nhiên của
người vận hành ("This assignment applies once and closes after confirmation."); các từ ngữ triển khai như "persistent
Machine Session" hay "armed" không bao giờ hiển thị.

**Step 1 chọn Machine và một PN đang xếp hàng (queued).**

- Machine **luôn** được chọn bằng các button hoặc thẻ tường minh, không bao giờ bằng dropdown.
- PN đang xếp hàng mặc định được chọn bằng các button hoặc thẻ tường minh. Nó thu gọn thành một dropdown gọn, có trợ
  năng **chỉ khi chiều cao tự nhiên của dialog với bố cục button PN đầy đủ không vừa chiều cao dialog, vốn bị giới hạn
  bởi viewport**. Độ vừa được đo từ một probe vô hình của bố cục button PN đầy đủ ở chiều rộng của ô, nên quyết định
  không bao giờ phụ thuộc vào mode đang được áp dụng; giới hạn `max-height` của dialog đã bao gồm khoảng cách cần thiết
  tới các mép viewport. Phép đo chạy lại khi viewport thay đổi kích thước và khi PN đang xếp hàng thay đổi, và việc chọn
  quay lại dạng button ngay khi viewport lại vừa chứa chúng. PN dài bị cắt bớt mà không làm dịch chuyển số lượng đang
  xếp hàng.
- Một đường phân cách mảnh, nhạt, rộng toàn bộ, mờ dần về hai đầu trên token viền nhẹ, lặng lẽ chia nhóm chọn Machine và
  nhóm chọn PN đang xếp hàng bên trong lưới dialog.
- Một ô nhập barcode riêng (`Scan Machine or queued PN barcode… (ENTER)`) chấp nhận barcode Machine thuộc Area của
  station hoặc barcode PN đang xếp hàng. Mọi lần quét không hợp lệ (Machine không xác định, thuộc Area khác hoặc không
  hoạt động; PN không có số lượng đang xếp hàng; barcode Worker, scrap hoặc không được hỗ trợ) hiển thị lỗi ngay tại chỗ
  và không thay đổi lựa chọn nào. Quét giá trị thứ hai hoàn tất cặp lựa chọn nhưng không bao giờ tự động chuyển bước hay
  ghi nhận.
- **Enter trên trang chọn.** Khi focus ở ô nhập barcode của Step 1, một lần Enter có đúng một ý nghĩa. Ô nhập có nội
  dung xử lý barcode như một lần quét chọn. Ô nhập **rỗng** chuyển sang trang số lượng khi một Machine **và** một PN đã
  được chọn hợp lệ (dù được chọn bằng quét, button hay dropdown) và không làm gì trong trường hợp khác. Phần xử lý sự
  kiện tiêu thụ phím nhấn, nên một lần Enter không bao giờ vừa quét vừa chuyển bước.
- **Lỗi chọn dùng kiểu guidance-error dùng chung.** Phản hồi chọn không hợp lệ hiển thị như cùng hướng dẫn lỗi `✕` ngay
  tại chỗ như kiểm tra hợp lệ số lượng (§4.8); điều này cũng áp dụng cho ghi chú barcode bị từ chối của dialog Scrap
  (§4.9). Lời thông báo cho barcode không được hỗ trợ hướng tới người vận hành:
  `Scan a Machine in this Area or a Part Number currently waiting in the Area queue. Your current selections were kept.`

**Step 2** nhập số lượng (MAX = số lượng đang xếp hàng, đây là giá trị mặc định).

**Step 3** là bản tóm tắt xác nhận (`ASSIGNED_TO_MACHINE`, PN, số lượng, Area queue → Machine, số lượng còn xếp hàng
sau khi gán, Worker, Station) → `Confirm assignment` → ngữ cảnh được xóa.

Machine không hoạt động (đang bảo trì) và Machine thuộc Area khác bị từ chối và không ghi nhận gì. Không có ngữ cảnh
Machine nào còn ở trạng thái sẵn sàng (armed) sau khi hoàn tất hoặc hủy.

### PN trước (PN-first)

Quét một PN mở wizard một lần áp dụng (§4.7). Vào việc gán Machine từ dialog action của PN sẽ chọn sẵn PN, và Back ở
Step 1 quay về dialog action đó, cùng kiểu Back về dialog cha mà mọi dialog con của dialog action đều cung cấp (quy tắc
wizard ở trên). Lần quét Worker chuyển Worker Session (hiển thị trên header); danh tính Worker không bao giờ quyết định
tính đúng đắn nghiệp vụ.

### Action trên hàng monitoring

Trong một Area **có Machine**, card `In this Area now` **không hiển thị action trên hàng**; việc gán vẫn khả dụng qua
quét PN, quét Machine và dialog action. Các hàng PN trên Machine card mang **hai action riêng biệt**: gọn, với icon nằm
trên nhãn (icon không bao giờ là nguồn ý nghĩa duy nhất), mỗi action có accessible name rõ ràng, và không bao giờ được
gộp lại.

- **`DONE`** (accessible name `Complete Area processing`, tông success) hoàn tất xử lý và chuyển số lượng sang trạng
  thái đã xong. Nó mở wizard DONE thủ công: một bước số lượng với MAX mặc định bằng số lượng tại vị trí nguồn hiện tại,
  sau đó là một view xác nhận riêng với Action `Complete Area processing`, PN, Quantity, Area, Machine khi áp dụng,
  Result `Finished — ready to move`, Worker, Scan Station và sự kiện được ghi nhận `AREA_COMPLETED`, rồi
  `Confirm completion`. Khi xác nhận, số lượng rời khỏi Machine card và xuất hiện dưới `Finished — ready to move` trong
  phần tóm tắt Area; Machine hiện tại được xóa và Area hiện tại vẫn là vị trí vật lý. Số lượng khác của cùng PN không bị
  tác động.
- **`QUEUE`** (accessible name `Return to Area queue`, tông trung tính) trả số lượng chưa xong hoặc đang tạm dừng về
  hàng đợi của Area (`RELEASED_FROM_MACHINE`, số lượng mặc định là MAX, sau đó là một view xác nhận riêng). QUEUE không
  bao giờ đánh dấu số lượng là DONE.

**Ngoại lệ xử lý trực tiếp.** Trong một Area **không có Machine**, số lượng đang được xử lý không có Machine card để
thao tác. Vì vậy, đúng chính xác những hàng `In this Area now` có phần đang được xử lý (`In processing`, hoặc
`External processing` tại một nhà cung cấp bên ngoài) mang **một action trên hàng `DONE` duy nhất**.

- Nó dùng cùng cách trình bày button dùng chung như `DONE` trên Machine card (accessible name `Complete Area processing`,
  tông success, icon nằm trên nhãn `DONE`) trong thanh action tách biệt dùng chung (§4.10). Bản thân hàng PN không bao
  giờ trở thành có thể nhấp, và vùng chạm dành cho xưởng được giữ nguyên.
- Nhấp `DONE` không ghi gì: nó mở **cùng wizard DONE thủ công**, wizard này không hiển thị field Machine. MAX của bước
  số lượng mặc định bằng số lượng đang được xử lý của phần PN được chọn (số lượng nhỏ hơn chỉ hoàn tất một phần). View
  xác nhận (Action `Complete Area processing`, PN, Quantity, Area, Result `Finished — ready to move`, Worker, Scan
  Station, Recorded event `AREA_COMPLETED`, nút chính `Confirm completion`) vẫn là điểm ghi duy nhất.
- Khi xác nhận, đúng một Movement `AREA_COMPLETED` bất biến được ghi nhận. Số lượng được chọn chuyển từ
  `In processing` sang `Finished — ready to move`, Area hiện tại vẫn là vị trí vật lý, và không có Machine nào được gán
  hay xóa. Một lần DONE từng phần để phần còn lại tiếp tục được xử lý, các QuantityFlow khác của cùng PN không bị tác
  động, và Last Scanned PN cùng Undo tuân theo hành vi DONE thủ công.
- **Không có `QUEUE`**: một Area xử lý trực tiếp không có hàng đợi Machine để trả số lượng về.
- Việc hiển thị được quyết định bởi **dữ liệu ngữ nghĩa tường minh** (một Area không có Machine, không phải Area
  terminal (kết thúc), cộng với trạng thái phần đang được xử lý), không bao giờ bởi CSS selector hay tên Area. Các phần
  đã xong
  (`READY_TO_TRANSFER`), số lượng không còn trong Area, và các Area có Machine không bao giờ hiển thị action trên hàng.
- Khi ứng dụng offline hoặc các thao tác ghi bị chặn, `DONE` bị vô hiệu hóa tại chỗ: thanh action giữ bố cục của nó, và
  không workflow nào có thể dẫn tới một trạng thái được ghi sai có thể mở ra.
- Lựa chọn `Complete Area processing` của dialog action PN (§4.7 item 3) vẫn là lối đi dựa trên quét tới cùng wizard đó.

Management → Area Board hiển thị cùng card dùng chung **không có bất kỳ action trên hàng nào** và vẫn hoàn toàn chỉ đọc.
## 4.7 Phân giải khi quét PN

Việc quét một PN mở ra gì phụ thuộc vào nơi quantity của PN đang nằm.

### Mục 1 — PN không có Work Order Demand đang active

Trường hợp này bao gồm cả một PN được thấy lần đầu tiên. **Wizard Receive Quantity ba bước** được áp dụng. Wizard mở
thẳng khi quét nếu PN không có quantity đang active ở bất kỳ đâu. Khi PN đã có quantity đang active, wizard là một lựa
chọn tường minh trong các dialog của mục 2 và mục 3, và khi đó nó dùng xác nhận quantity riêng (separate-quantity
xác nhận) của Bước 3.

Tiêu đề dialog của wizard là **`Receive Quantity`** (`intake` chỉ còn tồn tại như tên workflow nội bộ trong các
identifier và comment; PROJECT_PROFILE §14). Wizard tương đương với workflow "Add Part" của Work Orders, với các giá trị
mặc định có thể sửa `Request Type = MODIFY` và `Route Mode = FLOATING`.

**Bước 1 — Thiết lập.**

- PN được hiển thị nổi bật.
- Dòng mô tả chỉ mang **danh tính của PN**:
  - PN đã biết: `This Part Number has no active production demand. Review the details below before receiving quantity.`;
  - PN mới: `New Part Number. Verify it carefully; it will be registered when you confirm the receipt.`
- Ngay sau đó là hướng dẫn thông tin ℹ `No changes are recorded until you review and confirm the final step.`
  Phần header **không mang câu nào giải thích các giá trị mặc định**: các giá trị mặc định và ngày tháng hiển thị trực
  tiếp trong các field.
- Tiếp theo là các field thiết lập: xem lại và sửa Request Type và Route Mode (Planned Route chỉ khi `PLANNED`); một due
  date tùy chọn (thuộc sở hữu của WorkOrderDemand; label của nó đánh dấu `(optional)` bằng phần tử `.field-optional`
  trầm dùng chung, kiểu label tùy chọn có thể tái sử dụng, không bao giờ là CSS pseudo-content); Area/Operation bắt đầu;
  reason/notes; và hành vi tái sử dụng Work Order nội bộ. Bước này không có quantity.
- `received_date` mặc định là scan timestamp: thời điểm mà scan resolution phát ra khi wizard mở, được giữ nguyên qua mọi
  bước và gửi kèm xác nhận, nên một receipt được xác nhận sau nửa đêm vẫn ghi đúng ngày đã quét. Station không bao giờ
  đọc đồng hồ của chính nó tại write point; server suy ra ngày theo lịch của site.

**Bước 2 — Quantity.** Bước này hiển thị PN và một phần tóm tắt gọn hai dòng của Bước 1.

- Dòng 1 hiển thị `TypeChip` dùng chung (`NEW`/`MODIFY`; cùng chip với Management → Tracking, bắt buộc ở **mọi nơi**
  hiển thị NEW/MODIFY), **`RouteModeChip`** dùng chung (mode và thông tin route trong **một** chip, ngăn cách bằng dấu
  gạch dài: `FLOATING — actual trace` / `PLANNED — <route name>`, tô màu theo mode, dùng chung với Tracking §7.2) và chip
  Operation. **Các dấu phân cách `·` đứng riêng** nằm giữa các chip (`TypeChip · RouteModeChip · Operation chip`); dấu
  phân cách không bao giờ là một phần nội dung của chip. Các chip Request Type và Route Mode dùng chung được căn về
  **một chiều cao thị giác**: số đo DEFAULT của `RouteModeChip` khớp với các chip và pill khác như `TypeChip`, và chỉ
  header flow của Details panel trong Tracking giữ biến thể gọn của nó (§7.2). Màu ngữ nghĩa của chip không thay đổi.
- Dòng 2 là dòng Work Order. Các label `WO` và `Due:` giữ độ đậm bình thường, còn giá trị WO Number và giá trị ngày hiển
  thị in đậm với tông chữ mạnh hơn một chút (`.ss-recap .rval`): **chỉ là nhấn mạnh chữ, không bao giờ là badge hay màu
  action**. `Notes: …` theo sau khi có.
- Sau đó là hướng dẫn thông tin ℹ `Enter the physical quantity received. No default quantity is assumed.` ngay phía trên
  ô nhập, rồi đến keypad.

**Bước 3 — Xác nhận.**

- Phần tóm tắt có cấu trúc hiển thị: Action `Receive Quantity`, PN, quantity, cùng các chip Request Type và Route Mode
  (chip mang tên route, nên **không có row Planned Route riêng**), hành vi Work Order, due date định dạng
  `Aug 12, 2026`, Area/Operation bắt đầu, trạng thái đích, reason/notes, `Existing quantity` khi có, Worker và Scan
  Station.
- **`Confirm receipt`** là write point duy nhất. Mô tả được ghi lại có dạng `received into <Area>…`.
- **Xác nhận quantity riêng** (PROJECT_PROFILE §14). Khi PN **đã có quantity đang active**:
  - view xác nhận nêu tên phân bố đó (`<Area> × <n> pcs`, nối bằng `·`; internal flow id không bao giờ hiển
    thị);
  - một hướng dẫn tông cảnh báo nêu rằng receipt **không nhập vào** quantity đó và `Combine quantities` là nơi các
    quantity được gom lại về sau;
  - view mang MỘT checkbox xác nhận tường minh:
    `Record this as a separate quantity — I reviewed the quantity already in production.`
  - `Confirm receipt` vẫn **bị vô hiệu hóa** cho đến khi checkbox được tick, và Enter trên view xác nhận không ghi gì khi
    checkbox chưa được tick: quyết định không bao giờ là một lần bấm phím.
  - Phân bố đến từ scan resolution, và server xét cùng quy tắc đó khi ghi. Quantity chỉ xuất hiện sau khi wizard mở sẽ
    quay về thành một từ chối tường minh không ghi gì; view khi đó hiển thị phân bố của chính server, xóa xác nhận, và
    operator trả lời bằng cùng một lần retry dưới cùng `device_event_id`.
- **Hành vi Work Order.** Phần tóm tắt nêu tên Work Order MODIFY nội bộ có số trống sẽ được tạo hoặc tái sử dụng
  (`—`). Khi có nhiều ứng viên hợp lý, một lựa chọn tường minh xuất hiện **bên trong view thiết lập** (một step view trong
  cùng modal, không bao giờ là dialog lồng nhau, §4.6), và `Next` bị chặn cho đến khi operator đã chọn một. Nó không bao
  giờ là phỏng đoán.

**Câu chữ hướng tới operator.** Câu chữ của receive, PN chưa biết và nhập thủ công giải thích điều operator phải kiểm
tra và việc xác nhận sẽ làm gì. Câu chữ nội bộ về tạo record hay cơ sở dữ liệu không bao giờ hiển thị: các quy tắc kỹ
thuật create-on-first-use, không phân biệt hoa thường và `received_date` nằm phía sau câu chữ, không nằm trong nó.

### Mục 2 — PN hiện không có trong Area

Nguồn được phân giải tường minh.

- **Chọn intent.** Khi có nhiều hơn một intent áp dụng ở đây (nhận một transfer, Repair return, và
  `Receive new quantity` khi **server** báo điều kiện vào `Receive Quantity`), dialog `Select an action` hỏi intent
  TRƯỚC và không suy ra điều gì. Quantity đang chờ ở một Area khác không bao giờ khiến transfer trở thành intent duy nhất
  có thể, và lựa chọn `Receive new quantity` nêu rằng receipt được ghi riêng với quantity ở nơi khác.
- **Một nguồn hợp lệ** → view quantity (MAX = quantity nguồn khả dụng, cũng là giá trị mặc định) → view xác nhận riêng
  → `Confirm transfer`.
- **Phần tóm tắt bước của view quantity** hiển thị transfer dưới dạng chip Area nguồn → đích cùng với **dòng ngữ cảnh
  Work Order**, không bao giờ là chuỗi hiển thị thô `workOrder`. `WO` vẫn là một label thường; giá trị WO Number mang
  nhấn mạnh chữ `.rval` dùng chung (`—` được nhấn mạnh cho số trống nội bộ); Operation hiển thị thành entity chip dùng
  chung; và một đoạn `NEW`/`MODIFY` hiển thị thành chip Request Type dùng chung. Chuỗi hiển thị được tách ra trong
  **một helper riêng** (`parseWorkOrderLabel` / `WorkOrderRecapLine`), không bao giờ bằng việc parse tùy tiện lặp lại
  trong JSX.
- **Nhiều Area nguồn hoặc QuantityFlow** → trước tiên là một dialog chọn nguồn tường minh. Quantity từ nhiều nguồn
  không bao giờ bị gộp một cách âm thầm.
- Back trên view xác nhận quay về view quantity với lựa chọn nguồn được giữ nguyên.
- **Hoàn tất ngầm tại nguồn.** Khi quantity được transfer vẫn đang được xử lý active tại nguồn (`ON_MACHINE` hoặc xử lý
  trực tiếp), transfer sẽ ngầm hoàn tất việc xử lý đó. View xác nhận nêu rõ việc hoàn tất
  (`Source processing: Completed at {Area} by this transfer`), và row `Recorded events` của nó
  nêu `AREA_COMPLETED, then TRANSFERRED (one command)` (§4.11). Quantity đã ở `READY_TO_TRANSFER` (hoặc vẫn đang xếp hàng) tại nguồn được
  transfer chỉ với `TRANSFERRED` và không bao giờ lặp lại `AREA_COMPLETED`.
- Undo đảo ngược toàn bộ command (§4.5).
- Một quantity một phần chỉ hoàn tất và transfer quantity đã chọn; phần còn lại giữ trạng thái nguồn của nó.

### Mục 3 — PN đã có quantity trong Area

Một action dialog chỉ hiển thị các lựa chọn hiện đang hợp lệ. Đây là một view lựa chọn, không phải một bước xác nhận.
Các lựa chọn `Complete Area processing` (DONE) nằm ngay sau `Assign queued quantity…` và trước
`Receive more quantity…`.

- **Assign queued quantity to a Machine** (chỉ khi có quantity đang xếp hàng): wizard gán (§4.6) với PN được chọn
  sẵn.
- **Complete Area processing on {Machine}** (một lựa chọn cho mỗi Machine mà PN đang được gán active): cùng wizard DONE
  một lần mà row action `DONE` của Machine card mở, với Machine được chọn sẵn và MAX mặc định bằng quantity được gán
  trên Machine đó. Back quay về action dialog này.
- **Complete Area processing** (chỉ với Area xử lý trực tiếp, khi có quantity đang xử lý active): wizard DONE thủ công
  của §4.6 không có field Machine. Cùng wizard đó cũng mở được trực tiếp từ row action `DONE` trên các row
  `In this Area now` đang xử lý active (§4.6, ngoại lệ xử lý trực tiếp).
- **Combine quantities.** Đây là thuật ngữ hướng tới operator; UI không bao giờ nói "merge Quantity Flows".
  - **Khi nào được đưa ra:** chỉ khi PN có hai quantity riêng biệt trở lên trong Area này mà **server** báo là có thể
    gộp, tức là trong một ngữ cảnh production giống hệt nhau (cùng PN, cùng Area, cùng trạng thái xử lý, cùng Machine
    khi gắn với Machine, cùng Operation và ngữ cảnh route). Station không bao giờ tự đánh giá tính tương thích và không
    bao giờ tự động gộp bất cứ thứ gì.
  - **Các lựa chọn:** một lựa chọn cho mỗi nhóm mà server báo, với phụ đề là các quantity, trạng thái và Operation của
    nhóm (`6 pcs + 4 pcs · In processing · Milling`).
  - **Dialog Combine quantities** là một view lựa chọn liệt kê mọi phần của nhóm dưới dạng
    `{qty} pcs · {state} · {Operation}` (`On {Machine}` nêu tên Machine cho các phần gắn với Machine; identifier nội bộ
    không bao giờ hiển thị). Mọi phần đều được chọn sẵn, và operator có thể thu hẹp lựa chọn xuống **ít nhất hai** (action
    chính vẫn bị vô hiệu hóa khi dưới hai). Một bản xem trước kết quả trực tiếp hiển thị kết quả
    (`10 pcs · In processing · Milling`).
  - **Next** mở phần tóm tắt xác nhận riêng (Action `Combine quantities`, PN, Area, `Selected quantities`
    `6 pcs + 4 pcs`, `Resulting quantity` `6 pcs + 4 pcs → 10 pcs`, State, Machine khi gắn với Machine, Operation,
    Worker, Scan Station, Recorded event `MERGED`), mà action chính **`Confirm combine`** của nó là write point duy nhất.
  - Chỉ nguyên phần: một phần của một phần không bao giờ được gộp ở đây (một SPLIT theo sau là một lần gộp vẫn là một
    tổ hợp ở backend, không phải một workflow của operator).
  - **Sau khi thành công.** Thành công chỉ hiển thị sau khi server đã xác nhận command `MERGED`. Các phần bị tiêu thụ
    rời khỏi inventory của Area, quantity kết quả xuất hiện trong cùng Area và trạng thái, các tổng không đổi, Last
    Action ghi `MERGED · 6 pcs + 4 pcs → 10 pcs in {Area}`, focus quay về ô nhập barcode, và lineage vẫn hiển thị trong
    Tracking.
  - **Từ chối hoặc mất phản hồi.** Một từ chối của server (ngữ cảnh của các phần đã thay đổi trong lúc đó) và một phản
    hồi bị mất tuân theo đúng mô hình từ chối và kết quả không xác định dùng chung của §4.11 (`Retry combine`,
    `Retry the same combine` dưới cùng `device_event_id`, `Leave — check the Area`).
  - Back quay về action dialog.
- **Receive more quantity from another Area** (chỉ khi có quantity có thể transfer ở nơi khác; với một lựa chọn nguồn
  tường minh khi có nhiều nguồn).
- **Receive new quantity** (chỉ khi **server** báo điều kiện vào `Receive Quantity`: PN không có Work Order Demand đang
  active và một Area có thể bắt đầu production): wizard của mục 1. Lựa chọn này nằm ngay sau lựa chọn
  `Add more quantity` trong dialog. Phụ đề của nó nêu
  rằng quantity đến kèm Work Order riêng và được ghi **riêng** với số pcs đã có ở đây. Không có gì bị gộp, và wizard
  dùng xác nhận quantity riêng tường minh. Nó không bao giờ thay thế cho `Add more quantity`, và `Add more quantity`
  không bao giờ thay thế cho nó: việc hiệu chỉnh ghi quantity tìm thấy bên cạnh quantity production đang có, còn receipt
  đưa vào quantity kèm nhu cầu nghiệp vụ riêng của nó.
- **Add more quantity.** Hướng dẫn cho operator có nội dung: "Add physical quantity found at this Area that was not
  transferred from another Area. A reason is required."
  - Thao tác này được phép đối với operator, không cần Manager phê duyệt, và bắt buộc có reason và quantity, **không có
    nút MAX và không có giá trị mặc định**.
  - Theo sau là một view xác nhận riêng, mà row `Recorded event` của nó nêu `QUANTITY_ADJUSTED · INCREASE`. Nó không bao
    giờ bị giấu như một transfer thông thường và không bao giờ thay đổi requested quantity của WO Demand.
    `Confirm addition` là write point duy nhất.
  - Quantity được thêm đi vào hàng đợi của Area (Area có Machine) hoặc xử lý trực tiếp (Area không có Machine).
- **Return quantity for repair** (đây là câu chữ của label, không bao giờ là "Create REPAIR demand"; chỉ được đưa ra khi
  quantity ở nơi khác có thể chủ ý quay về Area đã từng đi qua này): chọn intent Repair → chọn Area/QuantityFlow nguồn,
  quantity repair và reason bắt buộc → một view xác nhận riêng nêu rõ `movement_reason = REPAIR` → `Confirm repair`.
  Repair không bao giờ bị chuyển thành một Request Type và không bao giờ được chọn tự động chỉ vì Area xuất hiện sớm
  hơn trong lịch sử; một transfer thông thường tới một Area đã từng đi qua vẫn có thể thực hiện. Một quantity một phần
  đòi hỏi một QuantityFlow SPLIT.
- **Scrap damaged quantity** (§4.9).

## 4.8 Nhập quantity

Nhập quantity là một bước wizard với một keypad cỡ lớn **và một ô nhập văn bản số thật có thể nhận focus**. Khi vào
bước này, ô nhập số nhận focus và giá trị của nó được chọn, nên bàn phím vật lý hoạt động mà không cần click trước. Ô
nhập dùng `inputMode="numeric"` và không có number spinner gốc. Các nút keypad ảo là `type="button"` và không nhận
focus (`tabIndex={-1}`, focus giữ trên ô nhập), nên việc click một nút không bao giờ để focus ở chỗ mà Space hoặc Enter
sẽ kích hoạt lại nó.

**Thiết bị chủ yếu dùng cảm ứng.** Trên thiết bị chủ yếu dùng cảm ứng (touch-primary), ô nhập quantity dùng chung chặn
bàn phím số mềm gốc bằng `inputMode="none"` (không bao giờ là một field `readOnly` vĩnh viễn), trong khi NumPad tùy
biến, hành vi lựa chọn và caret, nhập từ bàn phím vật lý, và hành vi MAX / CLEAR / Backspace / Enter / Escape đều vẫn
hoạt động.

- Touch-primary được phát hiện **chỉ qua các tín hiệu năng lực của trình duyệt** (con trỏ thô, không có hover, có điểm
  chạm thật), không bao giờ bằng dò user-agent hay đoán theo độ rộng màn hình.
- Vì các trình duyệt di động tôn trọng `inputMode="none"` không nhất quán, một **cơ chế dự phòng `window.visualViewport`**
  chạy khi một dialog quantity đang mở: việc chiều cao viewport giảm đáng kể, phù hợp với một bàn phím mềm, sẽ thu gọn
  NumPad trên màn hình của dialog và làm chặt bố cục, để ô nhập, hướng dẫn, thông báo kiểm tra hợp lệ và các action của
  dialog vẫn hiển thị. NumPad quay lại khi viewport trở lại.
- Việc phát hiện được tách riêng trong một module dùng chung, tổng quát hóa (`src/components/touch-device.ts`), module
  này cũng phục vụ ô nhập barcode chính của Scan Station (§4.4). Nó hoàn toàn có thể đảo ngược và không bao giờ đụng tới
  ngữ nghĩa chỉnh sửa quantity nhận biết con trỏ dùng chung.

**Thứ tự hiển thị cố định.** Mọi view quantity dùng một thứ tự: **PN → ngữ cảnh đã chọn hoặc chỉ dẫn →
thông báo hướng dẫn/kiểm tra hợp lệ → ô nhập quantity → numpad → các nút điều hướng**.

**Các loại hướng dẫn.** Hướng dẫn nằm ngay phía trên ô nhập dưới dạng văn bản ngữ nghĩa, không bao giờ nằm trong một
hộp tóm tắt có khung hoặc viền nét đứt và không bao giờ trong một card nặng nề. Có đúng bốn loại khác biệt về thị giác:

| Loại        | Ký hiệu | Dùng cho | Ví dụ |
|-------------|--------|----------|---------|
| Thông tin   | `ℹ`    | Chỉ dẫn và thông tin | `Enter the physical quantity received. No default quantity is assumed.` |
| Cảnh báo    | `⚠`    | Các ràng buộc quan trọng | — |
| Hành động   | `›`    | Các hành động bắt buộc | — |
| Lỗi         | `✕`    | Lỗi kiểm tra hợp lệ | `Quantity cannot exceed the 6 pcs currently available at the source.` |

Không có loại trung tính không ký hiệu. Mọi hướng dẫn mang một ký hiệu nhỏ cộng với một viền nhấn mảnh theo tông ngữ
nghĩa của nó, nên màu sắc không bao giờ là điểm phân biệt duy nhất, và thông báo kiểm tra hợp lệ được hiển thị nổi bật
nhất. Chỉ dẫn của Receive Quantity và Add more quantity, câu nêu MAX và giá trị mặc định của Repair, và dòng trạng thái
Scrap là hướng dẫn thông tin ℹ. Thông báo kiểm tra hợp lệ cập nhật bên cạnh ô nhập, giá trị đã nhập được giữ nguyên, và
**Next vẫn bị vô hiệu hóa khi quantity không hợp lệ**.

**Chỉnh sửa nhận biết con trỏ.** Mọi Quantity NumPad dùng chung, cả bàn phím vật lý lẫn keypad trên màn hình, áp dụng
MỘT mô hình chỉnh sửa giá trị dùng chung:

- một chữ số **thay thế vùng đang chọn tại đúng vị trí của nó** (giá trị mặc định `4` đang được chọn trở thành `2`,
  không bao giờ `42`);
- một chữ số **chèn vào tại caret đã thu gọn** (`23` với caret ở giữa trở thành `243`, không bao giờ `234`);
- một chữ số **chỉ nối vào cuối khi ô nhập không có focus** hoặc không có caret dùng được;
- Backspace xóa vùng đang chọn hoặc chữ số trước caret (chữ số cuối cùng trong cơ chế dự phòng khi không có focus).

Sau một lần chỉnh sửa bằng keypad trên màn hình, focus vẫn ở (hoặc quay về) ô nhập quantity với caret nằm ngay sau chữ
số vừa chèn hoặc vùng vừa thay thế; caret chỉ nhảy về cuối trong cơ chế dự phòng nối vào cuối. Việc làm sạch chỉ giữ
chữ số, số chữ số tối đa, và việc làm sạch khi dán và khi dùng bộ gõ (input method editor, IME) đều vẫn áp dụng.

Các phím vật lý được xử lý tập trung:

```text
0–9       replace the selection / insert at the caret / append (unfocused fallback)
Backspace remove the selection / the digit before the caret (last digit unfocused)
Delete    clear
Enter     advance to the confirmation view (never directly to a write)
Escape    cancel the whole workflow
Space     ignored
Other     ignored
```

Enter ở bước quantity luôn có nghĩa là "chuyển sang view xác nhận", bất kể nút keypad ảo nào đã được click trước đó.
Enter trên view xác nhận thực hiện Confirm cuối cùng. Escape luôn hủy toàn bộ wizard.

**MAX và giá trị mặc định.** Transfer và gán hiển thị **MAX** và mặc định bằng nó; có thể nhập một quantity hợp lệ nhỏ
hơn. Add More Quantity không có MAX và không có giá trị mặc định. Next bị chặn với 0 và với quantity lớn hơn quantity
nguồn khả dụng. Mọi yêu cầu kiểm tra hợp lệ quantity phía server vẫn giữ nguyên (mockup chỉ kiểm tra hợp lệ ở phía hiển
thị).

**Quantity một phần.** Mọi wizard production trên quantity đang có (Transfer, Assign to Machine, QUEUE, Machine DONE và
DONE xử lý trực tiếp) chấp nhận bất kỳ quantity nguyên nào từ **1 đến MAX**.

- Hướng dẫn nêu quantity khả dụng là MAX và nói một quantity nhỏ hơn sẽ làm gì
  (`the rest stays queued / on {Machine} / in processing`).
- `0` và bất cứ giá trị nào trên MAX bị từ chối ngay trong view với một thông báo tường minh và không bao giờ được gửi.
- Một quantity dưới MAX hiển thị trên phần tóm tắt xác nhận dưới dạng `{n} pcs of {MAX}` với một row **Remaining**
  (`Remaining at source`, kèm nơi nó ở lại; `Remaining queued after assignment`; `Remaining on {Machine}`;
  `Remaining in processing`) và trong câu hỏi cuối cùng (`The remaining {m} pcs stay …`).
- Station chỉ gửi quantity đã chọn. **Server** tách Quantity Flow bên trong cùng command đó (phản hồi nêu nguồn bị tiêu
  thụ và phần còn lại); client không bao giờ tách bất cứ thứ gì, và quantity đầy đủ giữ workflow toàn-flow mà không tách
  gì cả.
- Sau khi server đã xác nhận, thông báo thành công nêu phần còn lại (`SPLIT · {m} pcs remain …`), Last Action có dạng
  `SPLIT + {event} · … · qty {n} of {MAX}`, và inventory được đọc lại hiển thị cả hai quantity kết quả trong các trạng
  thái của chúng.

## 4.9 Workflow Scrap

Thuật ngữ chuẩn là `SCRAPPED`; câu chữ trên UI là "Scrap damaged quantity".

**Câu chữ chỉ dẫn chỉ dành cho operator.** Chỉ dẫn của workflow có nội dung
`Scan PF:SCRAP once for each damaged piece, then enter one reason for the total. Nothing is recorded until confirmation.`
Tên event `SCRAPPED` không bao giờ xuất hiện trong câu chữ chỉ dẫn; nó chỉ hiển thị trong row `Recorded event` của phần
tóm tắt xác nhận và trên các bề mặt audit và lịch sử (§3 rule 10).

Một barcode riêng, nhạy theo ngữ cảnh, `PF:SCRAP`, điều khiển việc đếm:

- nó **chỉ** được chấp nhận bên trong workflow Scrap; ô quét chính từ chối nó;
- mỗi lần quét tăng bộ đếm scrap đang chờ thêm một, và việc đếm không thay đổi trạng thái production nào;
- số đếm đang chờ có thể được sửa (−1, `Remove one`) hoặc đặt lại (`Reset`) trước khi xác nhận;
- một reason scrap chung là bắt buộc;
- quantity khả dụng, quantity scrap đang chờ và quantity còn lại vẫn hiển thị trong khi đếm;
- **view xác nhận riêng** hiển thị PN, Area, Machine nếu có, quantity khả dụng ban đầu, quantity scrap, quantity active
  còn lại, Worker, Station và reason dưới dạng một phần tóm tắt có cấu trúc;
- Cancel ở bất kỳ bước nào sẽ bỏ số đếm đang chờ mà không ghi gì. `Confirm scrap` trên view xác nhận là write point duy
  nhất và tạo **một** thao tác `SCRAPPED` có thể audit cho tổng số.

Quantity đã scrap được hiển thị ở mọi nơi PN xuất hiện trong vận hành (Scan Station, Production Board, Area Board,
Tracking).

## 4.10 Bố cục giám sát Area và Machine

Bố cục dùng chung này cũng được dùng bởi phần chi tiết của Area Board (§6.3):

```text
[ In this Area now ] [ Machine cards grid                    ]
[ fixed left col   ] [ Machine 1 ][ Machine 2 ][ Machine 3   ]
[ grows vertically ] [ Machine 4 ][ Machine 5 ]              ]
```

**Các cột.**

- `In this Area now` là cột bên trái. Machine card chỉ chiếm lưới bên phải, xuống dòng thành các hàng bổ sung **bên
  trong** lưới đó, và không bao giờ xuống dòng xuống bên dưới card bên trái (`align-items: start`).
- Card bên trái có thể cao lên theo danh sách PN của nó nhưng không bao giờ cao lên vô hạn (cột của nó bị giới hạn). Nó
  đủ rộng để hiển thị một PN đầy đủ bình thường như `2027-60-8114-00` với khoảng cách hợp lý giữa PN ở bên trái và
  thông tin ngữ cảnh và quantity neo ở bên phải. Các PN cực dài bị cắt bằng dấu ba chấm và có tooltip.
- Cơ chế dự phòng một cột **nhận biết container**, không phải một breakpoint viewport tùy ý: khi view chứa không còn
  vừa ít nhất một Machine card dùng được bên cạnh phần tóm tắt, `In this Area now` lấp đầy toàn bộ chiều rộng hàng và
  các Machine card chuyển xuống dưới nó với độ rộng bình thường.
- Area không có Machine chỉ hiển thị `In this Area now`, trải toàn bộ chiều rộng, không có vùng Machine trống.

**Card `In this Area now`.**

- Card dùng màu của Area dọc theo cạnh trên và trình bày phần mô tả Area gọn của nó (ví dụ `Turning cell · Lathe 1–4`)
  bên trong khối header của card, đúng như các header cột của Management → Area Board → All Areas, không bao giờ là một
  khối riêng có khung hoặc bị chia tách mạnh.
- Card **không mang khối thống kê**: header của Scan Station là bề mặt tổng duy nhất (§4.3). Area Board giữ hàng thống
  kê trên card tóm tắt của nó thông qua tùy chọn `showStats` của component dùng chung.
- Card dùng cùng ngôn ngữ trình bày PN như Management → Area Board → Area, với các nhóm quantity sau: `On Machines` và
  `Area queue — awaiting Machine` khi Area có Machine; một nhóm `In processing` trực tiếp trong trường hợp ngược lại
  (`Stocked` cho Stockroom cuối); và một nhóm `Finished — ready to move` cho quantity `READY_TO_TRANSFER`.
- Các row DONE dùng một trạng thái thành công hoặc sẵn sàng rõ ràng (chip `done`; dòng trạng thái
  `Finished — ready to move`, hoặc `Finished at Lathe 3 — ready to move` khi một Machine đã hoàn tất công việc, như ngữ
  cảnh hoàn tất). Chúng hiển thị quantity của mình, không bao giờ ngụ ý rằng quantity vẫn được gán cho Machine đó, và
  vẫn nằm ở Area hiện tại cho đến khi được transfer. Sau khi DONE được xác nhận, quantity rời khỏi Machine card và xuất
  hiện trong nhóm này; Machine card chỉ hiển thị quantity đang được gán active.

**Bố cục row PN.** Mỗi row PN là một lưới tường minh: thông tin production ở bên trái, và action tùy chọn trong ô riêng
tách biệt ở bên phải.

```text
┌────────────────────────────────────────────────────────────┬──────────┐
│ Hot + PN                         Machine/Queue · quantity  │          │
│ WO Number · Job Numbers                         days left  │  ACTION  │
│ in-Area status                              time in Area   │          │
│ n scrapped                                                 │          │
└────────────────────────────────────────────────────────────┴──────────┘
```

| Dòng | Trái | Phải |
|------|------|-------|
| 1 | Chỉ báo Hot và PN | Ngữ cảnh Machine, hàng đợi hoặc done, và quantity (định dạng với `pcs`) |
| 2 | Work Order Number (`—` khi trống) và Job Number bên ngoài (có thể bị cắt bằng dấu ba chấm; giá trị đầy đủ vẫn xem được qua tooltip `title`) | Đếm ngược hạn được suy ra (§3 rule 12: `N days left`, `due today`, `overdue N days`, `No due date`) |
| 3 (khi có ý nghĩa) | Trạng thái trong Area (`Awaiting Machine`, `On Machine`, `In processing`, `External processing`, `Finished … — ready to move`) | Thời gian trong Area |
| 4 (chỉ khi có quantity đã scrap) | `{n} scrapped` | — |

Quantity đã scrap **chỉ** xuất hiện dưới dạng văn bản dễ đọc này; không có chỉ báo scrap thu gọn, và scrap không bao giờ
được hiển thị hai lần. Các row bên trong một phần tóm tắt Area hiển thị chip ngữ cảnh Machine, hàng đợi hoặc hoàn tất
của chúng. Các row bên trong một Machine card cụ thể truyền tùy chọn trình bày tường minh `showContext={false}`, nên
Machine đã được nêu ở header của card không bao giờ bị lặp lại ở từng row (không bao giờ bị ẩn bằng các CSS selector
dễ vỡ).

**Thanh action.**

- Khi một row có action, chúng nằm trong một ô action riêng ở bên phải, tách biệt bằng khoảng cách và một đường viền
  dọc nhẹ, căn giữa theo chiều dọc và có độ rộng tối thiểu có thể đoán trước, không bao giờ nằm sát ngay bên cạnh PN,
  ngữ cảnh hoặc quantity. Các row của Machine card xếp `DONE` phía trên `QUEUE` bên trong ô đó (§4.6).
- Ở độ rộng rất hẹp, các action có thể xếp xuống dưới nội dung nhưng vẫn tách biệt về thị giác.
- Row PN không bao giờ có thể click như một tổng thể.
- Khả năng hiển thị action: trong một Area có Machine, `In this Area now` không hiển thị row action nào và các row của
  Machine card hiển thị `DONE` và `QUEUE`. Trong một Area xử lý trực tiếp (không có Machine), các row
  `In this Area now` đang xử lý active hiển thị một `DONE` duy nhất (ngoại lệ xử lý trực tiếp của §4.6; không bao giờ
  `QUEUE`, và không bao giờ trên các row đã hoàn tất). Area Board hoàn toàn không truyền action nào (chỉ đọc).

**Trạng thái vận hành của Machine card kèm thời lượng.**

- Pill trạng thái trên header của mọi Machine card giám sát mang trạng thái vận hành được suy ra **kèm thời gian đã
  trôi qua trong trạng thái đó** (`running · 1h 24m`). Nó được suy ra lúc hiển thị từ timestamp `stateChangedAt` dùng
  chung của Machine cộng với đồng hồ UI dùng chung có độ chính xác đến phút (§3 rule 12; PROJECT_PROFILE §8.6).
  `formatStateAge` ủy thác cho ngôn ngữ thời lượng dùng chung (`18m`, `1h 24m`, `2d 03h`, `<1m`); một thời lượng đã định
  dạng không bao giờ được lưu. Vì vậy mọi bề mặt đều hiển thị cùng một thời lượng, và thời lượng **tiếp tục chạy khi
  view vẫn mở** (bảng Machines, card giám sát).
- Các tông màu trạng thái vận hành được dùng chung cho mọi bề mặt: **Running thành công, Idle cảnh báo, Maintenance
  lỗi**, luôn hiển thị bên cạnh chữ trạng thái viết ra, không bao giờ chỉ bằng màu.
- Văn bản trống của một card bảo trì mang ngữ cảnh bảo trì: `Under maintenance · Unavailable for production`, theo sau
  là ghi chú và `expected back YYYY-MM-DD` khi có.
- Dòng tổng `N pcs assigned · M PN(s)` của card mang hai class giá trị ngữ nghĩa, `machine-total-pcs` và
  `machine-total-pns`. **Cả hai dùng chung màu trung tính trầm thứ cấp của văn bản dòng xung quanh và của tổng
  `Total pcs` trên header. Số đếm PN cố ý giữ màu trung tính thứ cấp này mặc dù thống kê `Total PNs` trên header mang
  màu trung tính của văn bản chính** (§4.3). Đây là các class ngữ nghĩa, không bao giờ là `<b>` theo vị trí hay các
  selector nth-child.

**Time in Area và đếm ngược hạn được suy ra.**

- Một row chỉ lưu các timestamp cố định: timestamp vào Area (`enteredAreaAt`, null khi thời gian trôi qua không áp
  dụng) và due date (`due`, ISO hoặc null). Văn bản `Time in Area`, thứ tự sắp xếp của nó và đếm ngược hạn đều được suy
  ra lúc hiển thị từ chúng cộng với đồng hồ UI dùng chung có độ chính xác đến phút (§3 rule 12), theo một ngôn ngữ thời
  lượng và đếm ngược dùng chung duy nhất.
- Một card mà đếm ngược không áp dụng (Stockroom `allocated 50/50`) hiển thị trạng thái nguyên văn của nó thông qua
  `dueText` tùy chọn thay vào đó.
- Các chuỗi đã định dạng được lưu (`timeInArea`, `timeInAreaMinutes`, `dueDays`, `dueClass`) không tồn tại trong mô
  hình row.
- **Expected duration** (PROJECT_PROFILE §17). Row cũng mang thời điểm cố định `expectedBy` mà inventory dùng chung nêu
  cho từng flow: thời điểm vào cộng với expected duration hiệu lực của vị trí đó (giá trị snapshot của Assigned Route
  Step hiện tại, nếu không thì giá trị mặc định của Operation), không có khi cả hai đều không áp dụng. `Time in Area`
  của row chuyển sang tông cảnh báo (`tia.long`, với tooltip viết ra `Exceeds the expected duration`) khi đồng hồ dùng
  chung vượt qua nó; một row không có giá trị này không bao giờ bị gắn cờ. Điều này chỉ mang tính tư vấn: các row
  action của Scan Station giống hệt nhau trong cả hai trường hợp.

**Component dùng chung.** Phần trình bày được xây từ các component dùng chung (`AreaPnRow`, `AreaSummaryCard`,
`MachineMonitoringCard`, `DueStatus`, `QuantityStatus`: đặt tên theo miền nghiệp vụ, không có helper mơ hồ), nên Scan
Station và Area Board không bao giờ lệch nhau. Thay đổi row dùng chung ảnh hưởng tới cả hai bề mặt, và cả hai phải được
kiểm tra.

## 4.11 Các trạng thái

| Trạng thái      | Hành vi |
|-----------------|----------|
| Đang tải        | Các panel skeleton; ô quét bị vô hiệu hóa với placeholder "Connecting…" |
| Inventory trống | Row placeholder "No production in this Area" |
| Mất kết nối     | Banner OFFLINE cố định (§3 rule 6: đúng câu chữ một câu với action trầm `Retry connection`); việc gửi production write bị vô hiệu hóa; các ô quét hiển thị `Disconnected — scanning disabled`; dữ liệu chỉ đọc đã tải vẫn hiển thị |
| Đã cập nhật     | Thông báo cập nhật cố định (§3 rule 13: đúng câu chữ với `Reload page`); việc gửi production write bị vô hiệu hóa; ô quét `PartFlow was updated — reload to continue scanning`; tự tải lại sau 60 s khi không có dialog nào đang mở (§3 rule 13) |
| Đã kết nối lại  | Banner biến mất; ô quét được bật lại và nhận lại focus, sẵn sàng cho lần quét tiếp theo |
| Lỗi             | Thông báo nổi màu đỏ (khoảng 8 s, có thể đóng); ô nhập được xóa và nhận lại focus, sẵn sàng cho lần quét tiếp theo |

### Cách station làm việc với server

Station Selector và Scan Station là UI production dựa trên bề mặt `/api`. Các quy tắc dưới đây áp dụng cho mọi
workflow của station.

**Tải và độ mới của dữ liệu.**

- Station Selector liệt kê các Scan Station đang active cùng Department, Area và các Operation đang active của chúng từ
  cấu hình.
- Một station tải ngữ cảnh Area đã gắn của nó, các Machine của Area và inventory trực tiếp của Area (`In this Area now`);
  các tổng trên header được đối chiếu từ inventory đó. Inventory của Area là phần chia trạng thái của server: `Area queue — awaiting
  Machine`, các Machine card (mọi Machine đang active của Area, CHỈ chứa quantity `ON_MACHINE` của nó, có thể
  có nhiều PN trên một card và trạng thái Running / Idle / Maintenance được suy ra kèm thời lượng), và
  `Finished — ready to move` (chỉ trên phần tóm tắt Area).
- Nếu inventory của Area hoặc các Machine không tải được, station hiển thị một lỗi kèm Retry, không bao giờ hiển thị một
  Area trống. Không có tổng nào hiển thị trước phản hồi inventory đầu tiên.
- Barcode PN và các lần nhập thủ công được phân giải **trên server**. Một barcode không hợp lệ hoặc chưa biết bị từ chối
  mà không ghi gì.
- **Độ mới của cấu hình.** Khi một lần quét phân giải ra một Area khác với Area đang hiển thị (station đã được gắn lại
  sau khi trang tải), workflow đang chờ bị bỏ, ngữ cảnh và inventory được tải lại, và operator quét lại, để một lần xác
  nhận luôn nêu tên đích vừa được phân giải.
- Station đọc lại ngữ cảnh của nó ở chế độ nền sau mỗi lần quét được phân giải và mỗi khi `DONE` / `QUEUE` của Machine
  card hoặc `DONE` xử lý trực tiếp mở wizard của nó. Một lần đọc lại nền thất bại giữ nguyên station, dialog đang mở và
  pill Worker như lần đọc cuối; nó không bao giờ hiển thị trang lỗi.
- **Server phán định mode của Area.** Việc Area có Machine hay không là phán định của server (`has_machines` trên ngữ
  cảnh station và inventory của Area; nó suy ra từ các Machine đang active của Area, PROJECT_PROFILE §12), và trạng thái
  giữ của mọi flow đến từ các read model (`PROCESSING` cho quantity mà một Area không có Machine xử lý trực tiếp).
  Station không bao giờ suy ra xử lý trực tiếp từ tên Area hay một CSS class. Nó theo lần đọc **mới nhất** của server:
  inventory đang hiển thị (được đọc lại sau mọi action, mang các trạng thái flow được suy ra theo cùng mode) quyết định
  phần trình bày và các row action, và PN action dialog đưa ra lựa chọn từ trạng thái của các flow đã phân giải. Một
  Machine đầu tiên được thêm, hoặc Machine cuối cùng bị retire, sau khi station đã tải sẽ được phản ánh bởi lần đọc kế
  tiếp và không bao giờ bị ghi đè bởi ngữ cảnh đã tải trước đó.
- **Operation đã ghi.** Mọi flow mang Operation **đã được ghi** trên nó (mã, tên, cờ external, trạng thái kích hoạt).
  Quantity đang có giữ Operation đó ngay cả sau khi nó đã bị vô hiệu hóa và station không còn đưa nó ra cho các lượt đến
  mới. Trong một Area không có Machine, một row hiển thị `External processing` khi Operation đã ghi của flow là
  external, và `In processing` trong trường hợp ngược lại.

**Ghi: thành công, từ chối và kết quả không xác định.**

- **Chỉ thành công sau server.** Mọi command chỉ báo thành công **sau khi server đã xác nhận việc ghi**. Station khi đó
  tải lại ngữ cảnh station, inventory của Area và các Machine card của nó từ server, ghi Last Action, xóa ngữ cảnh dialog
  (không có ngữ cảnh Machine hay PN nào còn lại sau khi hoàn tất hoặc hủy) và trả focus về ô nhập barcode.
- **Từ chối.** Một từ chối của server (ví dụ: quantity đã di chuyển hoặc đổi trạng thái trong lúc đó, một Machine đã bị
  retire hoặc đưa vào bảo trì sau lần quét, station đã được gắn lại trong lúc đó, một Operation đã bị vô hiệu hóa, hoặc
  một độ lệch Planned Route chưa được xác nhận) ở lại trong dialog cùng lý do của server, và không có gì được ghi. Ngữ
  cảnh station và inventory của Area được đọc lại từ server ngay lập tức, và dialog khi đó chỉ đưa ra `Retry …` hoặc
  Cancel. Back bị rút đi, vì nó sẽ quay về một lựa chọn hoặc snapshot nguồn mà server đã từ chối.
- **Kết quả không xác định.** Một command mà phản hồi bị mất (lỗi mạng hoặc truyền tải sau khi gửi) được trình bày là
  một **kết quả không xác định**, không bao giờ là "nothing was changed". Dialog đóng băng intent đã xác nhận (ví dụ
  nguồn, Operation, reason) và chỉ đưa ra đúng lần retry giống hệt (`Retry the same …`), giữ `device_event_id` của nó
  để một command đã commit được phát lại thay vì ghi hai lần, hoặc rời đi để đọc lại Area (`Leave — check the Area`).
- **Mô hình ghi một lần.** Mọi wizard gửi theo mô hình ghi một lần dùng chung (`useOneShotWrite`), với cùng
  `device_event_id` trên mọi lần retry.
- **Quantity một phần.** Phần tách của server (`source_quantity_flow_id`, `remainder_quantity_flow_id` và
  `remainder_quantity` trên các phản hồi command) được báo trong thông báo và Last Action, và inventory của Area được
  đọc lại từ server mang cả hai quantity kết quả (§4.8).
- Không bao giờ hiển thị trạng thái lạc quan (optimistic).

**Chi tiết transfer.**

- Lựa chọn nguồn tường minh luôn trình bày nhiều nguồn hợp lệ; chúng không bao giờ được chọn tự động hay bị gộp.
- Operation đích được phân giải từ cấu hình Area của station: một Operation đang active duy nhất tự phân giải, và nhiều
  Operation đòi hỏi một lựa chọn tường minh. Tại một đích có nhiều Operation đang active, operator luôn chọn tường minh.
  Operation dự kiến của Planned Route chỉ được đánh dấu như hướng dẫn, và việc chọn một Operation khác được xác nhận như
  một độ lệch route `OPERATION` kèm reason của nó. Một độ lệch Planned Route được xác nhận trong cùng phần tóm tắt với
  một reason bắt buộc.
- Phần tóm tắt xác nhận transfer hiển thị Nguồn → Đích, PN, quantity, Operation, ngữ cảnh Work Order, Route, Scan
  Station và event được ghi.
- Một `transfer_blocked_reason` (một Area cuối) từ chối mọi action Receive/Transfer, ngay cả khi PN đã có quantity trong
  Area.
- Một transfer quantity `ON_MACHINE` thông báo trong phần tóm tắt bước và phần tóm tắt xác nhận rằng việc xử lý tại
  nguồn được hoàn tất bởi transfer (`AREA_COMPLETED, then TRANSFERRED (one command)`), và báo cả hai event được ghi sau khi
  server đã xác nhận toàn bộ command.
- Một transfer mà quantity nguồn đang được xử lý trực tiếp thông báo điều tương tự trong phần tóm tắt bước
  (`still in processing at {Area}: transferring it completes that processing first`) và phần tóm tắt xác nhận
  (`Source processing: Completed at {Area} by this transfer`;
  `Recorded events: AREA_COMPLETED, then TRANSFERRED (one command)`). Quantity đã ở `READY_TO_TRANSFER` (hoặc đang xếp
  hàng) hiển thị `Recorded event: TRANSFERRED` và không bao giờ có lần hoàn tất thứ hai.
- Một transfer VÀO một Area không có Machine nêu đích của nó là `{Area} — direct processing`, và quantity đến dưới dạng
  `In processing`.

**Chi tiết gán Machine, DONE và QUEUE.**

- Quét một barcode `PF:MACHINE:` trên ô nhập chính phân giải Machine **trên server**. Một Machine đã retire, thuộc Area
  khác hoặc đang bảo trì, hoặc một Asset Tag chưa biết, cho ra một thông báo lỗi có thể hành động mà không phân giải gì.
- `Assign to Machine` liệt kê các flow đang xếp hàng của Area như những lựa chọn tường minh; nhiều quantity riêng biệt
  của một PN là nhiều lựa chọn. Phần tóm tắt của nó hiển thị Action, PN, Quantity, Source `Area queue`, Machine đích,
  Scan Station và `ASSIGNED_TO_MACHINE`.
- Action dialog của một PN đang xếp hàng đưa ra việc gán cho từng quantity đang xếp hàng (cùng wizard với PN được chọn
  sẵn), và `Complete Area processing on {Machine}` cho từng quantity được gán Machine.
- Các wizard `DONE` và `QUEUE` của Machine card hiển thị Result `Finished — ready to move` hoặc
  `Queued — awaiting Machine`, với event được ghi `AREA_COMPLETED` hoặc `RELEASED_FROM_MACHINE`.
- Lựa chọn `Complete Area processing` xử lý trực tiếp được đưa ra một lần cho mỗi Quantity Flow đang được xử lý trực
  tiếp của PN. Phần tóm tắt bước của view quantity nêu tên Operation đã ghi, và phần tóm tắt xác nhận của nó cũng hiển
  thị **Operation** (Operation đã ghi). Lần hoàn tất đã xác nhận được gửi tới
  `POST /api/scan-stations/{id}/area-completions` **không có** `machine_id`; server ghi đúng một `AREA_COMPLETED` không có
  Machine. Sau đó quantity chuyển từ `In processing` sang `Finished — ready to move` và mất `DONE` của nó, và Last
  Action ghi `AREA_COMPLETED · {Area} processing → Finished — ready to move`.

**Chi tiết Combine quantities.** `Combine quantities` được đưa ra cho đúng các nhóm mà phân giải PN báo
(`combine_groups`, phán định của server theo chính quy tắc của merge command). Nó mở `scan-station-combine-dialog.tsx`
(một lựa chọn mà các phần dùng `aria-pressed`, một bản xem trước kết quả trực tiếp, phần tóm tắt xác nhận và
`Confirm combine`), gửi `POST /api/scan-stations/{id}/merges` chỉ với các flow đã chọn, báo thành công sau khi server
đã xác nhận command `MERGED`, đọc lại Area và trả lại focus.

**Chi tiết workflow hiệu chỉnh.**

- `Add more quantity` yêu cầu một lựa chọn Operation tường minh khi có nhiều Operation đang active.
- `Return quantity for repair` được đưa ra cho đúng các ứng viên transfer mà **server** đánh dấu là đủ điều kiện repair
  (Area của station đã từng được đi qua); station không bao giờ tự đánh giá lịch sử, và một transfer thông thường tới
  cùng Area vẫn là một lựa chọn riêng, không thay đổi. Một PN **không có** quantity trong Area mà các nguồn của nó gồm
  một nguồn đủ điều kiện repair sẽ mở cùng lựa chọn intent tường minh: Transfer thông thường, giữ mọi nguồn hợp lệ,
  hoặc Repair, thu hẹp lựa chọn nguồn về các nguồn đủ điều kiện repair. Vì vậy Repair cũng truy cập được ở đó và không
  bao giờ bị suy ra. Repair dùng lựa chọn nguồn tường minh khi có nhiều nguồn đủ điều kiện, sau đó là cùng wizard
  transfer mang intent Repair tường minh: quantity từ 1 đến MAX, với một lần trả về một phần được server tách bên trong
  cùng command; reason repair bắt buộc, tách biệt với mọi reason độ lệch route; và một view xác nhận nêu rõ intent
  Repair trên row `Recorded event` của nó (`TRANSFERRED · REPAIR intent`), với `Confirm repair` là write point duy nhất.
- PN action dialog đưa ra một lựa chọn `Scrap damaged quantity` cho mỗi quantity trong Area.
- `scrapped_quantity` của phân giải PN hiển thị trên action dialog và trên dialog không-có-quantity.

**Chi tiết Undo.**

- Station giữ các command đã hoàn tất của phiên này như một ngăn xếp và duyệt nó từ mới nhất trở về qua undo preview
  của server, là nguồn có thẩm quyền về điều kiện đủ. Việc duyệt chỉ dùng thao tác đọc và bỏ qua một command mới hơn
  mà server đánh giá là không đủ điều kiện cho đến khi tìm thấy một command đủ điều kiện.
- Khi không có gì đủ điều kiện, `⟲ UNDO` bị vô hiệu hóa và một thông báo nêu lý do. Phán định này được xác thực lại ở mỗi
  lần đồng bộ server về sau và không bao giờ được cache vĩnh viễn. Undo cũng bị vô hiệu hóa khi không có gì để đảo ngược
  hoặc khi việc ghi bị chặn.
- Khối Last Scanned PN hiển thị mục tiêu do server suy ra, không bao giờ là mục mới nhất thô của phiên, vốn có thể đã
  không còn đủ điều kiện.
- Preview đủ điều kiện đầu tiên hiển thị xác nhận dạng phần tóm tắt có cấu trúc: action ban đầu, quantity, nguồn và
  đích, Machine (danh tính của nó không bao giờ bị bỏ: một Machine của Area khác chuyển về label tường minh
  `Machine #<id>`), timestamp và tác động chính xác của việc đảo ngược. Phần tóm tắt Undo hiển thị `Worker` ban đầu
  và `Reversed by` đúng như server preview.
- Việc đảo ngược đã xác nhận được gửi dưới `device_event_id` riêng của Undo. Sau khi server đã xác nhận, lịch sử ban
  đầu vẫn hiển thị, và Last Scanned PN chuyển sang thao tác trước đó đủ điều kiện kế tiếp khi việc duyệt phân giải lại
  nó từ server. Khi không còn gì đủ điều kiện, khối này báo không có action nào có thể đảo ngược và Undo bị vô hiệu hóa
  ngay lập tức. Area được đọc lại và focus quay về ô nhập barcode.
- Quantity đã stock không bao giờ được undo: ngăn xếp Undo bỏ qua nó (§10).

**Chi tiết Receive Quantity.**

- **Server** phán định nơi wizard áp dụng (`intake_available`: PN không có Work Order Demand đang active và Area của
  station có thể bắt đầu production), và station không bao giờ đoán điều đó.
- Field Planned Route chỉ xuất hiện với `PLANNED` và chỉ liệt kê các Route bắt đầu tại Area này. Bước thiết lập yêu cầu
  một lựa chọn Operation tường minh khi Area cấu hình nhiều Operation.
- Ngày nhận theo lần quét: phân giải phát ra thời điểm, wizard mang nó nguyên vẹn và gửi lại, và server suy ra ngày
  theo lịch của site.
- Một từ chối `selection_required` quay về view thiết lập dưới một `device_event_id` MỚI: một request bị từ chối không
  ghi gì, nên intent đã sửa là một intent khác. Một phản hồi bị mất được đóng băng sau CÙNG `device_event_id` với
  `Retry the same receipt`, mà một receipt đã commit sẽ phát lại bất kể lần retry mất bao lâu.
- Receipt tạo một quantity RIÊNG và không thay đổi gì ở quantity đang có.
- Sau khi server đã xác nhận, ngữ cảnh station và inventory của Area tải lại và ô nhập barcode nhận lại focus. Receipt
  vào session log **mà không** trở thành mục tiêu Undo: một receipt cũng tạo hoặc tăng nhu cầu nghiệp vụ, điều mà một
  lần đảo ngược ở mức Movement không bao giờ ghi lại.
- Một receipt cần một thiết bị station đã enroll và permission Receive quantity into an Area của role áp dụng tại Scan
  Station (§4.13).

**Chi tiết Worker.**

- Mọi production action ghi Worker của Area, và mọi phần tóm tắt xác nhận production hiển thị nó thành row `Worker`
  trước `Scan Station` (bỏ đi ở các Area Disabled).
- Ở các Area Disabled và Fixed Worker, một lần quét badge được trả lời bằng `Worker badge scans are not used in this Area`
  và không thay đổi gì. Ở các Area Scanned session, nó đăng nhập, chuyển hoặc làm mới Worker Session (§4.12). Dù thế nào
  nó cũng không bao giờ thay đổi Last Scanned PN.
- Với gate xác nhận bằng badge của `DONE`, `QUEUE` return và Undo (§4.6, §4.12), server quyết định dạng của gate. Một
  badge ở gate đăng nhập Worker đó và được ghi trên action, và một gate bị từ chối giữ nguyên bản nháp, các lựa chọn và
  quantity.

**Preview phát triển.** Trong một bản build phát triển, phần trình bày đã được phê duyệt của các workflow station cũng
có sẵn dưới dạng một **mock preview chỉ dành cho phát triển** (`?preview=mock` trên một route Scan Station, được ghi nhớ
cho phiên trình duyệt). Nó không bao giờ đi vào một bản build production.

## 4.12 Nhận diện Worker và các phiên

**Worker không phải là User.** Một **Worker** chỉ tồn tại trong phạm vi Scan Station: đó là danh tính audit của người
vận hành station (PROJECT_PROFILE §7 Worker, §8.13, §19). Một **User** là một tài khoản ứng dụng cho Management,
Administration và mọi màn hình bên ngoài Scan Station. Một badge Worker không bao giờ đăng nhập vào PartFlow, và một
Worker Session không bao giờ là một phiên xác thực. Hai thứ này được quản lý trên các màn hình Administration riêng biệt
(Workers và Users, §9) và không bao giờ bị gộp thành một thực thể.

**Barcode của badge.** Barcode của badge Worker là barcode đã được in sẵn trên badge nhân viên hiện có của công ty;
không tồn tại và không cần barcode `PF:WORKER:` riêng. Một giá trị được quét không bắt đầu bằng `PF:` được **so khớp
chính xác sau khi chuẩn hóa (trim, uppercase)** với barcode badge của các Worker **đang active**, nên chữ hoa hay chữ
thường không bao giờ quan trọng. Một giá trị chưa biết hoặc mơ hồ bị từ chối mà không ghi gì và không bao giờ bị đoán
(PROJECT_PROFILE §10). Worker không có mã số nhân viên; một record Worker mang một id nội bộ ổn định, tên, barcode
badge, avatar và trạng thái active.

**Các mode Worker ID.** Việc nhận diện Worker có thể cấu hình theo từng Area (§9 bảng Areas):

| Mode            | Tác động |
|-----------------|--------|
| Disabled        | Không ghi Worker nào; header của station không hiển thị pill Worker. |
| Fixed Worker    | Worker đã cấu hình của Area được ghi tự động. |
| Scanned session | Các lần quét badge mở và chuyển Worker Session. |

Cách trình bày pill trên header theo từng mode được quy định ở §4.3. Ở các Area Disabled và Fixed Worker, một badge
được quét không thực hiện đăng nhập: một badge khớp được trả lời bằng một thông báo giải thích và không thay đổi trạng
thái.

**Scanned session: timeout không hoạt động trượt.** Một Worker Session kết thúc sau một khoảng thời gian không hoạt
động có thể cấu hình, không bao giờ tại ranh giới ca làm việc.

- Timeout được cấu hình trong **Administration → Worker sessions** (§9): một giá trị chính sách mặc định với các giá
  trị ghi đè tùy chọn theo từng Area. Không có quản lý lịch ca, và timeout không bao giờ được suy ra từ số lượng Worker
  trong một Area.
- Mọi **tương tác production hợp lệ** tại Scan Station làm mới timeout: một lần quét PN hoặc Machine được phân giải
  thành công, một production command đã xác nhận, một lần quét badge hợp lệ. Các lần quét không hợp lệ, chưa biết hoặc
  bị từ chối không bao giờ làm mới nó.
- Quét badge của một Worker đang active khác sẽ chuyển Worker đang active **ngay lập tức** và bắt đầu phiên của Worker
  đó; không có bước đăng xuất.

**Modal badge: đăng nhập và hết hạn.** Trong một Area Scanned session, Scan Station đòi hỏi một Worker Session đang
active.

- Mở station khi không có phiên đang active sẽ hiển thị một **modal chặn** yêu cầu quét badge; bề mặt station phía sau
  nó trở nên trơ.
- Khi phiên hết thời gian, **chỉ Scan Station bị chặn**: cùng modal đó xuất hiện với đúng
  `Worker session expired` và `Scan your badge to continue.` (không bao giờ dùng câu chữ `unlock`). Mọi view khác của ứng
  dụng không bị ảnh hưởng; việc chặn chỉ tồn tại trên route Scan Station.
- Nếu một production dialog đang mở khi phiên hết hạn, bản nháp và các lựa chọn của dialog được **giữ nguyên** bên
  dưới, nhưng việc xác nhận production vẫn bị chặn cho đến khi có một lần quét badge hợp lệ. Modal hiển thị phía trên
  dialog.
- Sau một lần quét badge hợp lệ, modal đóng lại, workflow bị gián đoạn tiếp tục ở chỗ cũ, và focus được trả lại theo
  các quy tắc focus của station (§4.4): vào dialog đang mở nếu có, nếu không thì vào ô nhập barcode chính.
- Khi trang đã lỗi thời (§3 rule 13), modal hiển thị `Reload page` và ô nhập badge của nó bị vô hiệu hóa với câu chữ quét
  lỗi thời. Station không tự tải lại khi modal đang mở.
- Các bản build phát triển hiển thị các badge demo `DevNotice` dùng chung bên trong modal (§3 rule 10). Mỗi giá trị
  badge có thể click được và mô phỏng một lần quét badge qua cùng đường so khớp badge, giống hệt các barcode demo của
  scan card (§4.4).

**Xác nhận bằng badge cho các action nhạy cảm.** Tách biệt với modal đăng nhập của phiên, các tùy chọn Administration →
Worker sessions (§9) có thể yêu cầu một **gate quét badge** làm bước cuối cùng của `DONE`, `QUEUE` return và Undo trong
một Area Scanned session (§4.6). Gate nhắc lại các dữ kiện chính của action đang chờ. Bất kỳ badge Worker đang active
nào cũng xác nhận được: badge xác định Worker xác nhận, người được ghi trên action và được đăng nhập đúng như với một lần
quét badge thông thường (chuyển phiên khi đó là một Worker khác, và làm mới timeout). Khác với modal đăng nhập, gate có
thể bị hủy: Cancel hoặc Escape quay về phần tóm tắt xác nhận mà không ghi gì.

## 4.13 Enroll thiết bị

Một Scan Station chỉ hoạt động từ một trình duyệt mà administrator đã enroll cho station đó (PROJECT_PROFILE §15,
§20). Enrollment xác thực thiết bị đầu cuối, không bao giờ xác thực một người: việc nhận diện Worker vẫn như mô tả ở
§4.12, và một badge Worker không bao giờ cấp quyền cho bất cứ điều gì.

- **Màn hình enroll.** Một station không tải được vì không có thiết bị đã enroll (trình duyệt không giữ thiết bị nào,
  thiết bị đã bị revoke hoặc bị thay thế, hoặc nó được enroll cho station khác) hiển thị một panel enroll thay cho
  station. Panel nêu Station ID và yêu cầu enrollment code mà administrator đã phát hành cho station này
  (Administration → Scan Stations, §9). Code gồm mười ký tự hiển thị dạng `XXXXX-XXXXX`; nó được chấp nhận ở mọi kiểu
  chữ hoa hay thường và có hoặc không có dấu gạch nối hay khoảng trắng. Một Station ID chưa biết hoặc không active vẫn
  hiển thị lỗi station không khả dụng tường minh (§4.1) thay vì panel.
- **Cách trình bày enroll.** Panel là một card căn giữa trên bề mặt station, dựng giống khung dialog. Ô nhập code của
  nó, dùng chung với dialog chặn mô tả bên dưới, hiển thị giá trị nhập bằng cùng kiểu chữ monospace lớn, đậm, giãn
  cách như code mà Administration hiển thị khi phát hành (§9), căn giữa trong một ô cao giữ toàn bộ code nhìn thấy được
  đến tận bề rộng điện thoại. Các action của panel (`Station Selector`, `Enroll device`) chia nhau một hàng với độ rộng
  bằng nhau và khoảng cách rõ ràng, và xếp chồng lên nhau trên màn hình hẹp thay vì ép nhãn của chúng.
- **Trong khi làm việc.** Khi một request bị từ chối vì thiết bị đã bị revoke hoặc bị thay thế trong lúc station đang
  mở, cùng màn hình enroll đó xuất hiện như một dialog chặn, giống modal badge Worker (§4.12). Các bản nháp đang mở, các
  lựa chọn và Worker Session được giữ nguyên bên dưới, và không có gì được ghi thêm cho đến khi thiết bị được enroll
  lại. Nếu kết quả của action cuối cùng chưa xác định, station giữ nó và yêu cầu operator xác nhận lại cùng intent sau
  khi enroll; nó không bao giờ khẳng định rằng chưa có gì được ghi.
- **Kết nối.** Enroll là một thao tác ghi: nó bị chặn khi mất kết nối (§3 rule 6), và không có gì được xếp hàng. Ở
  production mode, panel giữ một nút `Station Selector`; bản thân mode không bao giờ thay đổi việc ai đã được enroll.
- **Station được gắn lại.** Một station có Area bị thay đổi trong Administration trong lúc nó đang mở sẽ tải lại ngữ
  cảnh của nó mà không yêu cầu enroll: enrollment thuộc về Station ID.
- **Các action mà role của station không cấp.** Station ẩn các action mà role áp dụng tại Scan Station (§9) không cấp:
  các lựa chọn PN action, các action trên Machine card và row, các stepper allocation và `⟲ UNDO` (§4.5). Một lần quét
  chỉ mở ra một action như vậy sẽ hiển thị một thông báo đỏ và không mở gì. Một từ chối đối với action mà role không
  cấp (trên một màn hình cũ) là một rejection đỏ thông thường mà không ghi gì. Một lần quét badge bị từ chối vì thiếu
  permission quét badge được hiển thị bên trong modal badge Worker (§4.12), modal này vẫn mở.

---
# 5. Production Board

Production Board là màn hình chỉ đọc, toàn màn hình, dành cho các màn hình lớn dùng chung. Nó không có phần tử tương tác
nào ngoài một thao tác cài đặt tùy chọn mà chỉ quản trị viên dùng được. **Board chỉ có phạm vi toàn Department: không có
chế độ lọc theo từng Area.** Việc theo dõi theo từng Area là trách nhiệm của Area Board (§6: All Areas overview và
per-Area detail), và hai view không bao giờ trùng lặp nhau.

### Header và nhận diện

- **Đồng hồ trên header.** Header hiển thị ngày **và giờ** địa phương theo thời gian thực, được điều khiển bởi **UI clock
  chung có độ chính xác đến giây** (§3 rule 12: component đồng hồ subscribe `useUiClock('second')`; nó không bao giờ là
  một timestamp mock tĩnh và không bao giờ là timer do component tự sở hữu). Đồng hồ đọc như **một khối thống nhất**: giờ
  là phần tử nổi bật nhất, ngày nằm ngay bên dưới như một dòng phụ gắn liền rõ ràng. Nó là văn bản thuần trên bề mặt
  header (không border, không background), nên không bao giờ trông giống một control có thể chỉnh sửa, và được định cỡ
  cho màn hình lớn dùng chung ở cả hai theme.
- **Tiêu đề board kèm trạng thái live.** Auto-refresh giữ cho board luôn cập nhật, và tiêu đề board mang trạng thái live.
  Nhóm nhận diện trên header được tạo kiểu giống nhóm nhận diện trên header của Scan Station: một **dòng Department**
  `Machine Shop` (chữ hoa nhỏ, kiểu `.ss-head .dept`) phía trên tiêu đề board **`Production`** theo typography tiêu đề
  Area của Scan Station, với **trạng thái vận hành `● Live` nằm ngay sau tiêu đề**, cách ra một chút, nhỏ hơn tiêu đề, và
  **theo tông kết nối HIỆN TẠI**.
  - Khi trạng thái kết nối chung khỏe, văn bản trạng thái hiển thị theo tông success, và chấm vuông bo góc của nó (theo
    kiểu Area indicator) nhấp nháy theo một animation chung duy nhất `@keyframes pf-heartbeat` (`styles/global.css`).
    Animation đó đứng sau **mọi** chấm trạng thái kết nối khỏe trong ứng dụng, cụ thể là chip kết nối của ứng dụng và
    Scan Station (`ONLINE`) và chấm trạng thái này, nên "đang sống" trông giống nhau ở mọi nơi.
  - Trạng thái đang kết nối và offline không bao giờ nhấp nháy: chấm và văn bản trạng thái chuyển sang tông warning, và
    một ghi chú nhỏ tường minh `Feed stale — reconnecting` xuất hiện bên cạnh, nên trạng thái không bao giờ chỉ được biểu
    thị bằng màu. `prefers-reduced-motion` tắt hiệu ứng nhấp nháy.
  - Các nhãn giữ ý nghĩa riêng biệt: `ONLINE` là kết nối của ứng dụng và Scan Station; `● Live` là trạng thái vận hành
    của board.
  - Cả header standard và header Kiosk (§5.1) đều hiển thị **cùng** nhóm nhận diện và trạng thái live thông qua một
    component chung và trạng thái kết nối chung, không bao giờ có thêm một chip `ONLINE` thứ hai bên cạnh.

### Các cột

Các cột (PROJECT_PROFILE §21) là **No. · Part Number · Areas & Quantities · Time · Due Date · Total Days · Job
Numbers**. Job Numbers được cố ý đặt cuối cùng.

**Định cỡ cột theo nội dung.** Độ rộng cột lấy từ các class `colgroup` mang ngữ nghĩa, không bao giờ từ một tập độ rộng
pixel cố định tùy ý dễ vỡ.

- No. luôn gọn. Part Number, Due Date và Total Days không bao giờ xuống dòng.
- **Due Date và Total Days giữ độ rộng tối thiểu suy ra từ heading đầy đủ của chúng cộng padding của ô**: heading không
  bao giờ xuống dòng, và Total Days không bao giờ bị dính sát vào Job Numbers về mặt thị giác.
- **Job Numbers nhận một phần lớn hơn nhưng có giới hạn**, nên các dòng Job Number tiêu biểu không xuống dòng không cần
  thiết, trong khi Areas & Quantities · Time có đủ độ rộng cho dữ liệu có cấu trúc của nó mà không chiếm quá nhiều khoảng
  trống thừa.
- Ở độ rộng hẹp hơn, bố cục xuống cấp bằng cách cho Job Numbers xuống dòng, không bao giờ bằng cách cắt xén.
- Sau khi mọi cột đã có đủ độ rộng cho nội dung, phần độ rộng bảng còn lại được phân bổ cho Part Number, Areas &
  Quantities · Time, Due Date và một phần lớn hơn có giới hạn cho Job Numbers. No. và Total Days giữ gọn, với Due Date
  và Total Days không bao giờ hẹp hơn heading của chính chúng (heading không bao giờ xuống dòng).

**Part Number** hiển thị trên một dòng với **định cỡ nội tại (intrinsic sizing)**. Một PN ngắn hơn chuẩn 15 ký tự vẫn
giữ một mức nội dung tối thiểu `15ch` monospace thực sự (theo metric font của chính PN, không phải một độ rộng pixel cố
định tùy ý); một PN 15 ký tự như `2027-60-8114-00` hiển thị đầy đủ; và một PN dài hơn sẽ mở rộng cột để hiển thị đầy
đủ. PN **không bao giờ bị cắt ngắn** trên board này. Không dành chỗ cho chip Hot bên trong độ rộng PN. Tên và revision
xuất hiện như một dòng phụ xuống dòng bên dưới PN mà không làm rộng cột.

**Cách trình bày Hot.** Ngọn lửa hiển thị trong **cột No.** (`1 🔥`, `2 🔥`) với nhãn accessible như
`Row 1, Hot priority`. Ô Part Number **chỉ** chứa PN và mô tả: không bao giờ có `🔥#n`, và không bao giờ có ngọn lửa thứ
hai ở chỗ khác trong row. Chú giải ở footer mô tả ngọn lửa ở cột No. Các view khác (Area Board, Tracking, Priority) giữ
cách trình bày chung `🔥#n <PN>` (`HotPn` trong `src/components/indicators.tsx`).

### Areas & Quantities · Time

Cột này dùng một **grid ổn định** với **dữ liệu trình bày tường minh**. Mỗi row vị trí mang nhận diện hoặc nhãn Area, một
tên Machine riêng tùy chọn, một External activity tùy chọn, quantity, trạng thái và thời gian. Nó không bao giờ là một
chuỗi hiển thị ghép (`External — Plating`, `Lathe 2 machine`) mà sau đó phải tách ngược ra.

**Căn thẳng track.** Mọi row đều theo **`Location | Quantity | State/activity | Time`**. Các vị trí location, quantity,
state và time, bao gồm nhãn `total` và tổng quantity, thẳng hàng tại cùng vị trí ngang trên **tất cả các PN row đang
hiển thị**, và track quantity không bao giờ dịch chuyển theo độ dài nhãn Area hoặc Machine của từng row.

- **Track chung đo theo nội dung.** Vì mỗi PN row bố trí các trường vị trí trong grid riêng của nó, sự thẳng hàng giữa
  các row đến từ một **độ rộng track chung được đo**. Mỗi lần board commit đo giá trị thực rộng nhất của từng trường trên
  **tất cả** các row trong bản sao đo ẩn (bản này luôn bố trí từ nội dung thuần, nên việc đo và việc áp dụng không bao giờ
  tác động ngược lên nhau) và công bố bốn độ rộng dưới dạng custom property mà mọi row hiển thị đều dùng. Các giá trị dự
  phòng `ch` nhỏ chỉ áp dụng trong khoảnh khắc trước lần đo đầu tiên, và padding ô được thu gọn còn 12 px và 8 px.
- Do đó mọi row giữ đúng độ rộng mà nội dung thực rộng nhất cần: bốn vị trí thẳng hàng trên mọi PN row **và** qua các lần
  xoay trang, không có khoảng trống thừa đẩy các trường ra xa nhau, và không typography nào bị thu nhỏ.
- Một tên Area hoặc Machine đặc biệt dài sẽ **mở rộng track chung** thay vì bị cắt ngắn (không bao giờ có dấu ba chấm),
  nên không có gì chồng lấn hoặc bị cắt đối với Machine, queue, xử lý trực tiếp, External activity, DONE, Stockroom, thời
  lượng dài hoặc tên Machine dài.
- Một track đệm ở cuối giữ phần độ rộng ô còn thừa ở một chỗ, nên thời gian luôn tách biệt rõ ràng và thẳng hàng **gần
  dữ liệu vị trí của nó** thay vì trôi ra mép ô xa nhất, và các chi tiết vị trí đọc như một nhóm.

**Các row vị trí.**

- Quantity đang được gán Machine hiển thị là `[Area color dot] [Lathe 2 chip]  4  on machine  1h 05m`. Tên Machine nằm
  trong một chip bo tròn gọn (kiểu nhãn: background chip hoặc panel nhẹ, border nhẹ, góc bo tròn, cả hai theme, không
  dùng màu hành động). **Tên Machine đầy đủ luôn hiển thị, không bao giờ có dấu ba chấm**, và văn bản trạng thái đầy đủ
  hướng tới người dùng là **`on machine`** (không có dạng viết tắt ở bất kỳ đâu).
- Quantity đang xếp hàng giữ nhãn `queue`; xử lý trực tiếp hiển thị `processing`.
- **Vị trí External chỉ hiển thị `External`** làm văn bản Area, với activity cụ thể (`plating`, `vendor`,
  `painting`, …) là một **chip thông tin nhẹ ở vị trí state**, thay thế nhãn chung `processing`. Quantity External đã
  DONE hiển thị `done` làm trạng thái hiện tại, với activity chỉ là ngữ cảnh phụ trong tooltip.
- Quantity DONE (`READY_TO_TRANSFER`) được phân biệt trực quan với quantity đang xử lý trên Machine: nó hiển thị Area hiện
  tại với trạng thái ngắn gọn `done`, không bao giờ là `on machine`, và không bao giờ hiển thị Machine là bên đang thực
  hiện. Machine đã hoàn thành công việc chỉ xuất hiện như ngữ cảnh hoàn thành phụ (tooltip).

**Tông trạng thái tô màu giá trị quantity.** Tông ngữ nghĩa, nhất quán với thống kê của Scan Station, nằm trên giá trị
quantity của row: quantity `queue` là warning; quantity `on machine`, `processing` và External activity là information;
quantity `done` là success. Chỉ dùng semantic token. Bản thân các từ trạng thái hiển thị như văn bản mờ nhất của row, và
màu không bao giờ là cách phân biệt duy nhất: văn bản trạng thái viết ra luôn nằm cạnh mọi quantity có tông.

**Thời gian lưu lại theo từng vị trí được suy ra.** Mỗi row vị trí chỉ lưu timestamp vào cố định `since` (null ở nơi
thời gian đã trôi qua không áp dụng, ví dụ Stockroom). Thời gian hiển thị được suy ra lúc render từ giá trị đó cộng UI
clock chung có độ chính xác đến phút (§3 rule 12).

- Thời gian chuyển sang màu amber (`long`, với tooltip viết ra `Exceeds the expected duration`) khi vị trí đã vượt quá
  **thời lượng dự kiến hiệu lực** của nó (PROJECT_PROFILE §17: giá trị snapshot của Assigned Route Step hiện tại, nếu
  không có thì là giá trị mặc định của Operation).
- Server nêu thời điểm cố định `expected_by` mà tại đó phần (portion) có hạn sớm nhất tại vị trí vượt quá thời lượng dự
  kiến của chính nó. Mỗi portion được đánh giá theo thời lượng dự kiến của chính nó, không bao giờ theo giá trị trung
  bình, nên một portion mới hơn không bao giờ che một portion đã quá hạn. Clock chung đánh giá thời điểm đó, và một vị
  trí không có thời lượng dự kiến thì không bao giờ bị gắn cờ. Không có ngưỡng thời gian lưu lại cố định.
- Việc làm nổi bật chỉ mang tính khuyến cáo và không chặn gì.

**Total row.** **Một đường phân cách nét đứt liền mạch** (một phần tử riêng trải dài toàn bộ grid vị trí, không bao giờ
là các mảnh border theo từng ô) nằm giữa các row vị trí và **total row**. Total row bắt đầu ở cột Location (`total`), căn
quantity của nó thẳng với cột quantity, theo sau là `pcs` (Stockroom thêm `stocked`), và giữ chỗ cho vị trí thời gian bên
phải.

**Tông của quantity.** Các quantity theo từng vị trí mang tông trạng thái ngữ nghĩa của row đó (ở trên; tông muted vẫn
giữ ở nơi không có tông trạng thái nào áp dụng, ví dụ Stockroom), và quantity của total row dùng màu văn bản chính
(không có tông trạng thái), nên tổng đối chiếu vẫn dẫn đầu về thị giác. Nhãn vị trí và `pcs` giữ muted, các từ trạng
thái dùng tông dim, và thời gian lưu lại đọc như văn bản phụ.

### Due Date, Total Days và scrap

- **Due Date** mang một dòng phụ được làm nổi bật với đếm ngược hạn được suy ra (§3 rule 12: `N days left`,
  `due today`, `overdue N days`). Amber nghĩa là sắp đến hạn trong cửa sổ Due Soon tỷ lệ với lead time (§3 rule 12), đỏ
  nghĩa là quá hạn, và trung tính nghĩa là còn thoải mái hoặc đã stocked; `✓ stocked` vẫn được suy ra từ tổng stocked.
  Không có cột Days Left riêng.
- **Total Days** tương tự được suy ra từ ngày nhận Work Order (`N d`). Không giá trị nào trong hai giá trị này được lưu.
- Ngày hạn bị thiếu hiển thị là `—` (§3 rule 9). Work Order Number bên ngoài để trống trong metadata Job Numbers cũng
  hiển thị là `—`.
- **Hiển thị scrap.** Một PN có quantity bị scrap hiển thị **`n scrapped` dưới dạng văn bản thuần rõ ràng theo tông error
  ngay trên chính dòng total**, với độ đậm phù hợp, **không có background tô đầy, không border và không bo tròn kiểu
  pill**, neo ở vị trí thời gian bên phải của total row. Không có ký hiệu `⊘`, không có row bảng phụ và không chồng lên
  `pcs`, và không hiển thị gì khi scrap bằng không. Footer mang tổng scrap của Department.

### Thứ tự, mức khẩn cấp và chuyển động

- **Mô hình ưu tiên.** Hot rank của Work Order Demand đến từ Priority Management. Các row Hot được sắp lên đầu theo thứ
  tự rank, với sắc nền row càng đỏ khi rank càng hot. **Mọi Hot rank đều được tô sắc nền**, theo ba tầng của cách trình
  bày Hot chung (rank 1 đỏ, rank 2 cam, mọi rank từ 3 trở xuống cùng một màu amber nhạt nhất): rank thấp hơn thì dịu
  hơn, không bao giờ bị bỏ không xử lý. Các row không Hot theo sau theo thứ tự demand chuẩn (PROJECT_PROFILE §18): các row
  có ngày hạn theo ngày sớm nhất trước, sau đó là các row không có ngày hạn sau tất cả các row có ngày hạn, sắp theo ngày
  nhận Work Order.
- **Mức khẩn cấp theo ngày hạn.** Chỉ văn bản khẩn cấp, tức là đếm ngược hạn được suy ra (`N days left` /
  `overdue N days`, §3 rule 12), nhấp nháy; ngưỡng lấy từ chính sách Due Soon (§3 rule 12, §9). PN và bản thân ngày hạn
  giữ yên. Chú giải ở footer nêu điều này. `prefers-reduced-motion` tắt animation nhấp nháy nhưng vẫn giữ màu và độ đậm
  cảnh báo. **Nhấp nháy chỉ dành cho mức khẩn cấp theo ngày hạn.** Ngọn lửa Hot có nhịp đập riêng, tinh tế và khác biệt
  (bên dưới); không nội dung nào khác của Production Board được nhấp nháy hoặc đập nhịp.
- **Nhịp đập của ngọn lửa Hot.** Biểu tượng `🔥` trong cột No. có một nhịp đập lặp lại tinh tế về độ mờ và tỷ lệ: nhẹ
  nhàng và dễ đọc, không bao giờ nhanh hoặc gây phân tâm, và khác biệt rõ ràng với nhịp nhấp nháy ngày hạn. Chỉ ngọn lửa
  chuyển động, không bao giờ là row, số row hay PN, và ngọn lửa không hiển thị ở bất kỳ chỗ nào khác. Dưới
  `prefers-reduced-motion: reduce`, nhịp đập bị tắt trong khi ngọn lửa và kiểu Hot hiện có vẫn giữ nguyên.

### Phân trang và xoay trang

**Phân trang động.** Các trang được tính từ **chiều cao board thực có sẵn và chiều cao row thực khi render** (thông qua
một bảng đo ẩn).

- Chúng được tính lại khi viewport hoặc container thay đổi kích thước, khi dữ liệu thay đổi, khi theme hoặc metric font
  thay đổi (ResizeObserver cộng window resize), **và ở mỗi nhịp phút chung**: văn bản thời gian lưu lại và đếm ngược được
  suy ra có thể làm thay đổi độ rộng và chiều cao đo được, nên việc đo và tính lại phân trang chạy lại theo mỗi lần suy
  ra lại (§3 rule 12).
- Luôn hiển thị ít nhất một row mỗi trang, và trang đang hoạt động được kẹp lại khi cấu trúc trang thay đổi.
- Trong môi trường mô hình đối tượng tài liệu (document object model, DOM) không có layout (test), áp dụng giá trị dự
  phòng 10 row mỗi trang.

**Xoay trang tự động.** Các trang xoay tự động **chỉ khi có nhiều hơn một trang**, với **thời gian dừng mỗi trang tỷ lệ
với số row hiển thị trên trang hiện tại**. Việc định giờ dùng **số giây cho mỗi row hiển thị** và **thời gian dừng trang
tối thiểu** của Department (ban đầu là 3 s và 6 s). Đây là các thiết lập hiển thị của Department (§9), được cấu hình
theo từng Department, gửi kèm board feed và áp dụng ở lần refresh kế tiếp; chúng không bao giờ là hằng số UI hard-code.
Tooltip của chỉ báo xoay trang nêu các giá trị của Department. Chỉ báo trang chính xác, và một trang duy nhất không bao
giờ tự nhận là đang xoay.

**Điều hướng thủ công.** Footer cũng cung cấp điều hướng thủ công:

- Một nút Previous và một nút Next. Previous bị vô hiệu ở trang đầu và Next ở trang cuối: điều hướng thủ công không bao
  giờ quay vòng, mặc dù xoay trang tự động có thể tiếp tục quay vòng.
- **Các chấm trang có thể click** với nhãn accessible rõ ràng và `aria-current` trên trang đang hoạt động.
- **`ArrowLeft` / `ArrowRight`** điều hướng trang ở cấp cửa sổ bất kể focus (board không có quy trình nhập văn bản thông
  thường). Không có gì xảy ra khi trang được yêu cầu không tồn tại; không quay vòng; các phím bị bỏ qua khi có
  Ctrl/Alt/Meta và không có tác dụng khi một modal dialog của ứng dụng đang hoạt động; và một lần đổi trang hợp lệ sẽ
  tiêu thụ phím (không cuộn ngang).
- **Vuốt cảm ứng.** Một cú vuốt ngang ở bất kỳ đâu trên board điều hướng trang đúng như các nút và phím mũi tên: vuốt
  trái sang trang kế tiếp và vuốt phải về trang trước, được kẹp lại và không quay vòng. Cử chỉ ngắn, cử chỉ chủ yếu theo
  chiều dọc (cuộn) và cử chỉ đa chạm (pinch zoom) không bao giờ đổi trang.
- Mọi lần đổi trang thủ công (nút, chấm, phím mũi tên hoặc vuốt) **khởi động lại timer xoay trang tự động**. Các
  listener được dọn dẹp khi unmount.

**Chuyển cảnh khi đổi trang.** Mọi lần đổi trang (nút, chấm, phím mũi tên, vuốt và cả xoay trang tự động) đều phát lại
một **chuyển cảnh vào ngắn có nhận biết hướng** trên bảng đang hiển thị: một hiệu ứng fade ease-out một lần khoảng
0.28 s trong khi bảng trượt vào từ hướng di chuyển. Đi tới các trang cao hơn thì trượt về phía trước, và lần quay vòng
của xoay trang từ trang cuối về trang đầu cũng vẫn là về phía trước, nên chu kỳ giữ một hướng. Chuyển cảnh được cố ý làm
tinh tế cho một màn hình treo tường không ai chạm vào, và bị tắt dưới `prefers-reduced-motion` (khi đó trang đổi tức
thì).

**Chỉ báo xoay trang.** Khi có nhiều hơn một trang, một chỉ báo tinh tế cho biết khi nào lần xoay trang tự động kế tiếp
sẽ xảy ra: một track tiến độ mỏng tích hợp với các control phân trang của footer cộng một nhãn nhỏ số giây còn lại, phụ
thuộc về thị giác so với các row sản xuất.

- Nó đọc **cùng deadline định giờ và cùng thời lượng mỗi trang đang điều khiển việc xoay trang thực tế** (một nguồn định
  giờ duy nhất, không bao giờ là một timer thứ hai không đồng bộ), nên mọi lần điều hướng thủ công đặt lại cả việc xoay
  trang và chỉ báo cùng lúc, và track luôn phản ánh thời gian dừng tỷ lệ của trang hiện tại.
- Chỉ báo sở hữu nhịp thị giác riêng của nó, nên toàn bộ board không bao giờ re-render theo từng animation frame.
- `prefers-reduced-motion` ẩn track chuyển động trong khi nhãn số giây vẫn còn.
- Chỉ báo bị ẩn hoàn toàn khi chỉ có một trang.

### Tự động co giãn hiển thị

Footer mang một công tắc trượt On/Off `Auto scale` (`role="switch"`, tên accessible `Automatic display scaling`, cùng họ
control trượt với công tắc Kiosk; mặc định **On**).

- Khi On, board áp dụng MỘT hệ số tỷ lệ đồng nhất: CSS zoom trên root của board, tính từ độ rộng bảng max-content nội tại
  đo được (`autoFitScale` trong `board-logic`), nên toàn bộ độ rộng bảng lấp đúng màn hình. Nó **phóng to** trên các màn
  hình treo tường lớn để thu hẹp khoảng trắng giữa các cột, và nó **thu nhỏ trên màn hình nhỏ (điện thoại và máy tính
  bảng) để toàn bộ bảng không xuống dòng vừa với độ rộng mà không cần cuộn ngang**.
- Mọi cột giữ nội dung đầy đủ của nó theo cả hai chiều: nội dung vừa ở mức cơ sở không bao giờ có thể bắt đầu xuống dòng
  ở tỷ lệ đã điều chỉnh.
- Phân trang có tính chiều cao chia ngân sách viewport của nó cho cùng hệ số đó và suy ra lại số row mỗi trang cho
  typography đã co giãn.
- Ngưỡng thu nhỏ (`FIT_SCALE_MIN`) là một chốt chặn gần bằng không có chủ ý chỉ để chống các phép đo suy biến, không bao
  giờ là giới hạn để đảm bảo dễ đọc: màn hình nhỏ thu nhỏ đến mức độ rộng của chúng đòi hỏi.
- Off trả về kích thước cơ sở (khi đó viewport hẹp sẽ xuống dòng hoặc cuộn như mô tả trong §2.5).
- Thiết lập này chỉ thuộc về trình bày và không bao giờ được lưu lại.

### Footer

Footer là một flex child bình thường **neo vào đáy viewport của board** thông qua flex layout của board
(`margin-top: auto`). Nó không bao giờ là `position: fixed` và không bao giờ che nội dung bảng; chiều cao thực của nó
luôn được tính trong phép đo phân trang động; và các trạng thái rỗng hoặc một trang vẫn đặt nó ở đáy mà không tạo
scrollbar. Nội dung của nó được tổ chức thành các nhóm dễ đọc thay vì một câu liền không phân biệt:

- **Một row control**: chỉ báo trang, chỉ báo xoay trang, điều hướng thủ công, tóm tắt tổng hợp, và các công tắc `Kiosk`
  và `Auto scale` (§5.1).
- **Một row chú giải** chỉ mang **quy tắc sắp xếp** hướng tới người dùng:
  `Order: Hot rank first → earliest due date → no due date by oldest received date.` Cả ba cấp đều được nêu; các tiêu
  chí phân định hòa có tính xác định và tên trường của implementation không bao giờ bị lộ ra.
- **Tooltip trên heading cột** chứa các quy ước. `No.` giải thích ngọn lửa Hot 🔥. `Due Date` giải thích số ngày nhấp
  nháy **và cách suy ra cửa sổ cảnh báo Due Soon**: phần trăm của chính sách, khoảng kẹp số ngày tối thiểu–tối đa của
  nó, và một vài ví dụ lead time, với mọi con số được suy ra từ chính sách Due Soon chung (§3 rule 12), không bao giờ
  được lặp lại dưới dạng giá trị literal. Mỗi tooltip là một panel nhỏ dạng khóa/mô tả, ẩn cho đến khi hover, nên chiều
  cao header dính và phép đo phân trang không thay đổi. Không heading cột nào khác mang tooltip như vậy.
- **Tóm tắt tổng hợp** là một cách trình bày nội dòng tiết chế, không phải một hàng pill. Các giá trị số được nhấn mạnh
  bằng độ đậm và typography monospace với tông ngữ nghĩa nhẹ (PN đang hoạt động trung tính, pcs đang sản xuất
  information, pcs stocked success, pcs scrapped error); các nhãn giữ dịu hơn; các nhóm được phân cách bằng dấu phân cách
  mềm `·`; và toàn bộ phần tóm tắt luôn rõ ràng là phụ so với bảng sản xuất.

### Board làm việc với server như thế nào

Production Board là UI production trên `GET /api/production-board`.

- **Read model của server.** Các row đến theo thứ tự board chuẩn từ read model của **server**. Sự phân bổ theo Area,
  Machine và External activity với trạng thái được suy ra, timestamp vào cố định của mỗi vị trí, quantity stocked và
  scrapped, ngữ cảnh Work Order và Job Number đang mở, và Hot rank đều được suy ra trên server từ projection vị trí hiện
  tại và Movement history.
  - Một Work Order đã hoàn thành không bao giờ cung cấp ngữ cảnh cho một row.
  - Các row theo đúng thứ tự demand chuẩn; quantity stocked không phải là một tầng sắp xếp.
  - Quantity đã merge được đọc qua mọi nhánh lineage, nên vị trí của nó được tính ngày từ lần vào cũ nhất của toàn bộ
    khối merge, và một Machine hoàn thành chỉ xuất hiện khi các nhánh thống nhất.
  - Mọi giá trị thời gian hiển thị (thời gian lưu lại theo từng vị trí và cờ `long` của nó, được đánh giá theo thời
    điểm cố định `expected_by` của server; đếm ngược hạn; `Total Days`; đồng hồ) vẫn được suy ra lúc render từ UI clock
    chung (§3 rule 12).
- **Department.** Dòng Department nêu tên Department mà server đã xác định: Department đang hoạt động duy nhất, hoặc
  Department được chỉ định bằng `?department=<id>` trong URL của màn hình (một địa chỉ trình bày cho màn hình treo tường,
  không bao giờ là một route). Một cấu hình mơ hồ bị từ chối một cách tường minh.
- **Auto-refresh** poll feed định kỳ, với một request đang chạy tại một thời điểm, và poll lại ngay lập tức khi kết nối
  trở lại sau khi bị mất.
  - Một lần refresh thất bại giữ các row hoàn chỉnh cuối cùng trên màn hình, và trạng thái `● Live` chuyển sang tông
    warning kèm `Feed stale — reconnecting`, đúng như khi trạng thái kết nối chung không khỏe.
  - Lần tải đầu tiên thất bại là trạng thái error có Retry.
  - Vì `● Live` là trạng thái vận hành của chính board, nó CHỈ hiển thị khỏe khi một board hoàn chỉnh đang ở trên màn
    hình. Một lần tải đầu tiên vẫn đang chạy hoặc đã thất bại mang cùng tông warning và ghi chú, không bao giờ là một
    feed trông như đang live trên một board rỗng. Cả hai hiển thị dưới header board luôn có mặt (Department, tiêu đề kèm
    trạng thái, đồng hồ), và footer được hiển thị khi đã có một board hoàn chỉnh.
- **Job Numbers** nêu mọi demand của row (`<job numbers> · WO <number or —> [· MODIFY] · <n> pcs`, hoặc
  `· allocated a/n` khi việc allocation đã bắt đầu).
- **Tên và revision.** Dòng tên và revision PN của một row (`{name} · rev {revision}`) lấy từ chi tiết Part Number đã lưu
  và không có khi chưa có chi tiết nào.
- **Thiết lập hiển thị.** Department của feed mang thông số định giờ xoay trang của nó, và board đọc chính sách Due Soon
  ở mỗi lần refresh. Một lần đọc chính sách thất bại hành xử đúng như một lần đọc board thất bại.
- **Trang lỗi thời.** Khi trang đã lỗi thời (§3 rule 13) và feed refresh, `● Live` hiển thị khỏe và thông báo cập nhật
  nêu lý do. `Feed stale — reconnecting` được dành cho trạng thái đang kết nối, offline và refresh thất bại.
- **Preview cho phát triển.** Các preview `?state=` chỉ dành cho phát triển (loading, empty, error, long) hiển thị các
  trạng thái xác định mà không gửi request.

## 5.1 Kiosk mode

Kiosk mode là một chế độ trình bày tường minh dành cho màn hình treo tường, có thể truy cập như một route riêng:

- `/production-board`: chế độ standard, giữ thanh điều hướng ứng dụng trên cùng bình thường;
- `/production-board/kiosk`: Kiosk mode.

Chế độ này tường minh trong mô hình router (`mode: 'standard' | 'kiosk'`) và trong pathname, **không bao giờ là một
boolean chỉ nằm trong component hay một query parameter**. **Ctrl+Shift+K** chuyển đổi giữa hai route, phản chiếu phím
tắt chế độ của Scan Station. Nó không có tác dụng trong các control nhập văn bản không liên quan và khi một modal dialog
đang hoạt động; nó giữ trang hiện tại và hành vi của board ở mức thực tế có thể (component board vẫn được mount qua lần
chuyển đổi); và nó chỉ thuộc về trình bày, không bao giờ là một ranh giới phân quyền.

Trong Kiosk mode:

- Thanh điều hướng ứng dụng trên cùng bình thường bị ẩn (bởi application shell, cùng cơ chế với chế độ production của
  Scan Station). Banner OFFLINE thường trực và thông báo cập nhật được giữ lại, và board tự tải lại như §3 rule 13 mô tả.
- Board hiển thị **một header dùng chung với chế độ standard**: **cùng nhóm nhận diện kiểu Scan Station như header
  standard** (dòng Department phía trên tiêu đề `Production` kèm trạng thái `● Live` theo tông kết nối, §5). Header kiosk
  **không có dấu hiệu thương hiệu của ứng dụng** (nhận diện board đảm nhận header) và không bao giờ hiển thị chip kết nối
  `ONLINE`: một trạng thái cho mỗi header, và hai cách trình bày của một board. Sau nhóm nhận diện là một khoảng đệm, rồi
  đến **control Dark/Light gọn không viền** dùng chung (§2.1, chỉ trong kiosk), được hiển thị **bên trong row giờ của
  đồng hồ và căn giữa theo chiều dọc với văn bản giờ hiện tại**, không bao giờ căn giữa theo khối giờ-và-ngày cao hơn.
  Header là một row xuống dòng gọn gàng và vẫn dễ đọc ở độ rộng hẹp hơn; không có ngăn xếp hành động theo chiều dọc.
- **Công tắc kiosk nằm trong row control của footer**: một control sau phần tóm tắt tổng hợp, có kích thước như các
  control phân trang nên chiều cao footer không đổi (nó vẫn là một layout child bình thường được đo). Đó là một **công tắc
  trượt On/Off** `Kiosk` thuộc họ công tắc footer chung (`role="switch"`, tên accessible `Kiosk mode`, `aria-checked`
  phản chiếu route đang hoạt động, trạng thái viết ra `On` / `Off` bên cạnh track), cùng ngôn ngữ control trượt với công
  tắc `Auto scale` và công tắc Maintenance của Machines. Bật On sẽ điều hướng tới `/production-board/kiosk` (tooltip
  `Enter kiosk mode (Ctrl+Shift+K)`); tắt Off sẽ quay về `/production-board` (tooltip `Exit kiosk mode (Ctrl+Shift+K)`).
  Đó là cùng một control ở cùng một vị trí trong cả hai chế độ. Phím tắt `Ctrl+Shift+K` vẫn còn, và không có dòng chú
  giải nào lặp lại nó: phím tắt nằm trong tooltip của control.

Chế độ standard giữ thanh điều hướng ứng dụng bình thường và hiển thị **cùng header board** (nhóm nhận diện chung, tức là
dòng Department và tiêu đề `Production` kèm trạng thái `● Live` theo tông kết nối, §5, và đồng hồ; phần theme và kết nối
ở đó do thanh điều hướng cung cấp), cộng công tắc `Kiosk` ở footer (Off trong chế độ standard).

---

# 6. Area Board

Area Board là một sub view theo dõi của Management và tuân theo chế độ theme toàn cục (§2.1). Nó là một view với hai chế
độ nằm sau một tab strip duy nhất: **All Areas overview** (nội dung *Manager Summary* của PROJECT_PROFILE §21) và
**per-Area detail** (nội dung *Area Board* của PROJECT_PROFILE §21). Tên "Manager Summary" không được dùng trong UI.

## 6.1 Tab strip và toolbar

- Tab strip bắt đầu với **All Areas** (mặc định khi mở lần đầu), theo sau là một tab cho mỗi Area kèm chấm màu và số mục
  của nó. Đúng một tab đang hoạt động.
- Cả hai chế độ dùng chung một toolbar:
  - một ô tìm kiếm văn bản (PN, WO, Job Number) khớp với **mọi** demand đang mở của một PN, nên tìm Work Order hoặc Job
    Number của một demand được tóm tắt là `+N more` vẫn giữ PN đó hiển thị;
  - một bộ chọn sắp xếp (Due date, Priority, Time in Area, Quantity);
  - một phần tóm tắt phạm vi (số PN và tổng số chiếc cho Area đang hoạt động, hoặc trên tất cả các Area trong overview);
  - **trạng thái feed** của lần đọc live: `● Live` theo tông success với nhịp heartbeat kết nối chung, hoặc
    `● Feed stale — reconnecting` theo tông warning (§5: cùng câu chữ và cùng ý nghĩa như của Production Board, không
    bao giờ chỉ bằng màu, và là trạng thái của BOARD chứ không phải của kết nối).
- Độ rộng tối thiểu của ô tìm kiếm bằng độ rộng tối thiểu của select Sort (khoảng 150 px), nên toolbar co lại đều trước
  khi xuống dòng.

## 6.2 All Areas overview

Overview hiển thị một cột cho mỗi Area. Theo mặc định, layout cuộn ngang khi không phải tất cả các Area đều vừa. Một công
tắc trượt **Wrap columns** trong toolbar (viewport rộng, chỉ ở chế độ overview) chuyển sang layout xuống dòng: các cột
chảy sang các row bổ sung trong độ rộng trang, và không có gì cuộn ngang. Lựa chọn này là trạng thái trình bày trong
view: nó được giữ khi chuyển sang một tab detail rồi quay lại, và mặc định là cuộn ngang mỗi lần mở.

**Trình bày hẹp.** Dưới breakpoint điện thoại/máy tính bảng, view bỏ hẳn tab strip và hiển thị một **công tắc trượt
`Summary` (mặc định tắt)** thay cho `Wrap columns`.

- Khi Summary **tắt**, board phân trang qua các **per-Area detail**: một layout detail §6.3 cho mỗi trang trong một
  carousel có thể vuốt. Nó dùng cuộn ngang native với snap bắt buộc (mỗi lần vuốt dừng ở vị trí căn giữa detail của một
  Area), không có scrollbar hiển thị, và các trang hẹp hơn viewport một chút để các trang lân cận ló ra ở hai mép, đó là
  tín hiệu sẵn có cho biết còn trang khác.
  - **Các chấm trang mang màu Area** phía trên các trang phản chiếu màu nhận diện ổn định của Area (§2.2) và đồng thời là
    đích nhảy trực tiếp (`Go to {Area}`, `aria-current` trên trang đang hoạt động, vùng chạm rộng rãi quanh mỗi chấm).
  - **Các nút mép nửa pill nổi `‹` / `›`**, ghim ở giữa viewport trên bề mặt panel mờ, lật trang lùi và tới. Chúng luôn
    hiển thị khi một trang cao cuộn theo chiều dọc và bị vô hiệu ở hai đầu: việc lật trang không bao giờ quay vòng.
  - Trang đang hoạt động được suy ra từ vị trí cuộn, nên vuốt, quán tính, snap, chấm và nút không bao giờ có thể mâu
    thuẫn nhau, và dòng meta PN/pcs của toolbar theo Area của trang hiện tại.
- Khi Summary **bật**, All Areas overview xếp chồng thành danh sách cột dọc toàn chiều rộng (layout wrap của §2.5) với
  dòng meta tất cả các Area. Click vào header của một card sẽ nhảy thẳng tới trang detail của Area đó (Summary tự tắt).
- Tìm kiếm và sắp xếp áp dụng không đổi ở cả hai chế độ. Viewport rộng không bao giờ hiển thị phần khung pager hay công
  tắc Summary.

**Mỗi cột Area hiển thị:**

- một **header có thể click** (màu Area, tên, mô tả, các chip Operation được hỗ trợ) với một gợi ý tương tác
  "detail ›"; click vào đó mở chế độ detail của Area đó, tương đương với chọn tab của nó;
- một row thống kê chỉ với các giá trị có ý nghĩa, không có nhiễu giá trị bằng không: Area có Machine hiển thị **total
  pcs · queued · on machines**, cộng **done** khi có quantity đã xong; Area không có Machine hiển thị **total pcs ·
  processing**, cộng **done** khi có quantity đã xong; cột Stockroom cuối hiển thị **stocked pcs · PNs**;
- danh sách PN của Area: **một row cho mỗi Part Number**, các quantity riêng biệt của nó được gộp vào các chip portion
  của row đó, được xây từ **khung PN-row và các subcomponent dùng chung** (§4.10: `AreaOverviewRow` dùng lại grid row
  chung, `HotPn`, `QuantityStatus`, `DueStatus`). Một row hiển thị PN (🔥 và rank khi là Hot), quantity, WO và Job Number
  (Work Order Number để trống hiển thị là `—`, cắt ngắn kèm tooltip `title`), đếm ngược hạn được suy ra (theo dải màu,
  §3 rule 12; `N days left` trong ngôn ngữ đếm ngược chung duy nhất, với `dueText` nguyên văn của Stockroom như
  `allocated 50/50` ở nơi đếm ngược không áp dụng), các portion của card được gộp thành các chip ngữ cảnh gọn
  (`Lathe 3 × 3`, `queue × 2`, `processing × n`, `done × 1` theo tông success), thời gian trong Area được suy ra, và
  `{n} scrapped` khi có. Không có implementation row JSX hoặc CSS độc lập nào có thể lệch khỏi cách trình bày chung;
- một trạng thái rỗng tường minh cho các Area không có sản xuất.

**Tìm kiếm và sắp xếp trong overview.** Tìm kiếm lọc các danh sách PN, và bộ chọn sắp xếp sắp thứ tự PN trong mỗi cột.
Vì row của overview CHÍNH LÀ Part Number, việc sắp xếp diễn ra **sau** khi gộp và so sánh các giá trị của chính row đó:
`Quantity` sắp theo tổng của PN trong Area đó (một PN giữ `6 + 6` xếp trên một PN giữ `10`), và `Time in Area` theo
portion cũ nhất của nó. `Priority` và `Due date` không bị ảnh hưởng, vì Hot rank và ngày hạn vốn đã ở cấp PN. Per-Area
detail vẫn sắp xếp các quantity riêng biệt, vì ở đó mỗi row có thể được thao tác riêng.

## 6.3 Per-Area detail — layout theo dõi Area/Machine dùng chung

Chế độ detail dùng **cùng layout cấu trúc như Scan Station** (§4.10):

```text
[ In this Area now ] [ Machine cards grid                    ]
[ fixed left col   ] [ Machine 1 ][ Machine 2 ][ Machine 3   ]
[ grows vertically ] [ Machine 4 ][ Machine 5 ]              ]
```

- **Card tóm tắt Area (bên trái).** Nó có màu Area dọc mép trên và hiển thị tên Area, mô tả và các Operation; sau đó là
  row thống kê (Area Board giữ `showStats` bật, bao gồm thống kê Done theo tông success); sau đó là danh sách PN gọn được
  nhóm. Các nhóm là **"On Machines"** và **"Area queue — awaiting Machine"** chỉ khi Area có Machine, một nhóm trực tiếp
  **"In processing"** trong trường hợp ngược lại (Stockroom cuối hiển thị **"Stocked"**), và **"Finished — ready to
  move"** cho quantity `READY_TO_TRANSFER`. Quantity đã xong thuộc về card tóm tắt Area, không bao giờ thuộc về một card
  Machine, và không bao giờ được hiển thị là Stocked hoặc là đã hoàn tất sản xuất. Mỗi mục dùng cách trình bày PN row dạng
  grid chung (§4.10): chỉ báo Hot và PN (`🔥#n` trước PN, §5), ngữ cảnh machine hoặc queue với quantity (`pcs`), WO (hoặc
  `—`) và Job Number kèm tooltip cho giá trị bị cắt ngắn, trạng thái hạn, trạng thái trong Area, thời gian trong Area,
  và văn bản `{n} scrapped` khi có quantity bị scrap. Việc nhóm không bao giờ nhân đôi hoặc làm mất quantity.
- **Card theo dõi Machine (chỉ trong grid bên phải).** Có một card cho mỗi Machine: tên, trạng thái (running / idle /
  maintenance) với tuổi của trạng thái trong pill trạng thái (`running · 1h 24m`, §4.10), tổng quantity được gán, số PN,
  và danh sách PN được gán theo cùng cách trình bày row chung. Machine idle hiển thị một trạng thái rỗng rõ ràng. Machine
  đang maintenance vẫn được phân biệt trực quan (border nét đứt tông error), với ghi chú maintenance và ngày dự kiến quay
  lại trong văn bản rỗng (§4.10). Các card xuống dòng sang các row bổ sung trong grid bên phải và không bao giờ nằm bên
  dưới card bên trái; một phương án dự phòng một cột có chủ ý áp dụng ở các breakpoint hẹp.
- Area không có Machine **chỉ hiển thị card tóm tắt toàn chiều rộng**, không có card Machine giữ chỗ và không có vùng
  Machine trống.
- **Area Board vẫn chỉ đọc.** Các component chung hiển thị mà không có thanh hành động nào của Scan Station (không có nút
  `QUEUE`, không có ô hành động), và không có sự lệch thị giác giữa hai view (dùng chung `AreaPnRow`,
  `AreaSummaryCard`, `MachineMonitoringCard`, `DueStatus`, `QuantityStatus`).
- Tìm kiếm và bộ chọn sắp xếp (§6.1) vẫn thu hẹp và sắp thứ tự các danh sách PN. **Sort: Time in Area** sắp theo số phút
  đã trôi qua được suy ra từ timestamp vào Area cố định (`enteredAreaAt` cộng UI clock chung, §3 rule 12; không có trường
  được lưu `timeInAreaMinutes`), lâu nhất trước.
- Một PN quá dài bị cắt ngắn bằng dấu ba chấm, và hover sẽ hiển thị PN đầy đủ dưới dạng tooltip (§2.3).
- Trạng thái rỗng: "No production in {Area}".

### Area Board làm việc với server như thế nào

Area Board là UI production trên `GET /api/area-board`.

- **Một lần đọc, hai cách trình bày.** MỘT lần đọc Department trả về mọi Area ACTIVE với **cùng mô hình theo dõi Area
  mà Scan Station đọc** (`app/api/area_inventory.py`): chế độ Area, mọi Quantity Flow ACTIVE với trạng thái nắm giữ do
  server suy ra, các card Machine chỉ chứa quantity đang được gán, các nhóm đang xếp hàng, đang xử lý trực tiếp và đã
  xong cùng tổng của chúng, ngữ cảnh demand OPEN của PN, và quantity bị scrap theo từng PN trong Area đó (dòng
  `{n} scrapped` của row chung, vì vậy station cũng hiển thị nó). Nó cũng trả về các Operation đang hoạt động của Area
  và, đối với Stockroom cuối (nơi quantity stocked đã hoàn tất sản xuất và không giữ flow nào đang hoạt động), các dòng
  stocked của nó cùng allocation đang hoạt động của PN và ngữ cảnh demand OPEN của PN: Hot rank, Work Order Number và Job
  Numbers của row Stockroom theo cùng quy tắc như mọi row theo dõi khác và như row chỉ-stocked của Production Board, và nó
  chỉ hiển thị `WO — · —` khi không còn Work Order nào của PN đang mở.
- All Areas overview và per-Area detail là **hai cách trình bày của cùng một câu trả lời đó**, nên chuyển tab không đọc
  lại gì và hai chế độ không bao giờ có thể mâu thuẫn nhau. Cả hai đều hiển thị qua các component chung
  (`AreaSummaryCard`, `MachineMonitoringCard`, `AreaPnRow`, `AreaOverviewRow`) trên cùng phép ánh xạ phía client mà Scan
  Station dùng, đó là điều giữ cho hai view không bị lệch.

**Overview lấy PN làm trung tâm; detail lấy quantity làm trung tâm.** Điều này là có chủ ý, vì chúng trả lời các câu hỏi
khác nhau.

- Một Part Number trong một Area là đúng MỘT row overview và được đếm một lần trong mọi con số PN (số đếm trên tab, dòng
  meta của toolbar, `Total PNs` / `PNs`), bất kể nó giữ bao nhiêu quantity riêng biệt ở đó. Các quantity đó được gộp vào
  các chip portion của row đó (`Lathe 3 × 3`, `queue × 2`, `processing × 6`, `done × 1`), luôn chiếm trọn toàn bộ
  quantity của row: một chiếc không bao giờ bị mất và không bao giờ bị đếm hai lần, và một sự hiện diện hỗn hợp (xử lý
  nội bộ bên cạnh một Operation bên ngoài) giữ phần còn lại trực tiếp của nó làm chip riêng.
- Per-Area detail giữ MỘT row cho mỗi quantity riêng biệt, vì mỗi quantity được quét và thao tác riêng tại Scan Station
  (§4.10).

**Một row được làm CHO điều gì là demand OPEN của PN, không bao giờ là nguồn gốc của nó.**

- Hot rank, đếm ngược hạn, Work Order Number và Job Numbers của mọi row theo dõi đến từ các Work Order Demand OPEN của PN
  theo thứ tự demand chuẩn (PROJECT_PROFILE §18); demand ĐẦU TIÊN xác định chúng.
- Khi một PN có nhiều demand đang mở, row nêu tên demand xác định đó và ghi `· +N more`, với mọi demand được ghi rõ
  trong tooltip của nó. Chúng không bao giờ bị gộp thành một giá trị mơ hồ và không bao giờ bị lặng lẽ rút gọn thành
  demand đầu tiên.
- Một Work Order đã hoàn thành là lịch sử và không cung cấp gì, ngay cả khi quantity mà nó đã release vẫn còn trong Area:
  một row như vậy hiển thị `WO — · —` với `No due date` thay vì mượn một ngữ cảnh mà nó không còn có.
- Demand mà một quantity BẮT NGUỒN từ đó vẫn ở trên quantity như thông tin nguồn gốc (provenance), và provenance **chỉ
  là ngữ cảnh quy trình và kiểm toán**. Nó nêu tên lô trong các action dialog, xác nhận và phần tóm tắt Last Action của
  Scan Station, nơi người vận hành sắp thao tác trên đúng quantity đó. Nó không bao giờ cung cấp cho một row theo dõi trên
  bất kỳ bề mặt nào: `In this Area now` của station và các row của Area Board là MỘT cách trình bày, nên một quantity
  không bao giờ có thể đọc là được làm CHO một thứ ở station và một thứ khác trên board, và hai câu trả lời không bao giờ
  bị trộn trong một dòng.
- Mỗi row cũng mang các giá trị theo dõi cố định của quantity CỦA NÓ: timestamp vào Area (được đọc qua mọi nhánh
  lineage, nên quantity đã merge được tính ngày từ giá trị cũ nhất trong số đó) và Machine đã HOÀN THÀNH quantity đã xong.
  Giá trị sau được báo từ chính quantity, nên một Machine bị retire sau khi hoàn thành công việc vẫn được nêu tên cho lần
  hoàn thành đó dù card của nó đã không còn.
- `Time in Area` và đếm ngược hạn vẫn được suy ra lúc render từ UI clock chung (§3 rule 12). Một row Stockroom ghi
  `allocated a/n` thay vì đếm ngược và hoàn toàn không có thời gian vào.

**Department, refresh và các trạng thái.**

- Department là Department đang hoạt động duy nhất, hoặc Department được chỉ định bằng `?department=<id>`. Một cấu hình
  mơ hồ bị từ chối một cách tường minh.
- Auto-refresh poll feed theo chính lịch của Production Board (một request đang chạy, và poll lại ngay lập tức khi kết
  nối trở lại sau khi bị mất). Một lần refresh thất bại giữ board hoàn chỉnh cuối cùng, với trạng thái trên toolbar
  chuyển thành `Feed stale — reconnecting`. Lần tải đầu tiên thất bại là trạng thái error có Retry. Một Department không
  có Area đang hoạt động là một trạng thái rỗng tường minh.
- `?state=loading|empty|error|long` hiển thị các preview phát triển xác định mà không gửi request.
- Tìm kiếm (bao trùm mọi demand đang mở của một PN, không chỉ demand mà row của nó nêu tên), bốn thứ tự sắp xếp (áp dụng
  cho overview SAU khi gộp theo PN, nên `Quantity` so sánh tổng của PN trong Area; `Priority` xếp mọi Hot rank trước mọi
  row không có rank, bất kể số rank) và các lựa chọn layout (Wrap columns, công tắc Summary ở màn hình hẹp và việc lật
  trang) vẫn là trạng thái trình bày của view. Khác với Production Board, không có thứ tự Area Board chuẩn nào để server
  sở hữu.

**Thời lượng dự kiến.** `Time in Area` của một row chuyển sang tông warning (`tia.long`, với tooltip viết ra
`Exceeds the expected duration`) khi quantity đã vượt quá thời lượng dự kiến hiệu lực của vị trí của nó
(PROJECT_PROFILE §17: giá trị snapshot của Assigned Route Step hiện tại, nếu không có thì là giá trị mặc định của
Operation; không có giá trị nào thì không gắn cờ gì). Inventory chung nêu thời điểm cố định `expected_by` theo từng flow,
và clock chung đánh giá nó. Row overview lấy PN làm trung tâm cảnh báo từ portion có hạn SỚM NHẤT trong sự hiện diện của
PN, mỗi portion được đánh giá theo thời lượng dự kiến của chính nó và không bao giờ theo giá trị trung bình, nên một
portion mới hơn không bao giờ che một portion đã quá hạn. Cùng row chung đó cảnh báo y hệt tại Scan Station (§4.10). Việc
làm nổi bật chỉ mang tính khuyến cáo, và mọi hành động giữ nguyên đúng như cũ.

---

# 7. Tracking

Tracking là giao diện quản lý chính. Nó được **tổ chức xoay quanh Part Number** (PROJECT_PROFILE §21): một bảng kết quả
PN có thể lọc cộng một **overlay detail nổi không modal** cho PN được chọn. Tiêu đề mà người vận hành thấy và nhãn điều
hướng của Management là **PN Tracking**; view giữ tên nội bộ `Tracking`, route `/management/tracking` và nhận diện mục
§7.

**Việc chọn chuyển đổi trên toàn bộ row.** PN được chọn có thể là **không có**. Click vào một PN row chưa được chọn sẽ
chọn nó và mở chi tiết; click lại vào row đang được chọn sẽ bỏ chọn và đóng chi tiết. **Toàn bộ row kết quả là đích click
để chọn**: một nút tương tác trên ô PN là control duy nhất có thể focus trong mỗi row và mang tên accessible cùng cách xử
lý focus hiển thị, trong khi một cú click ở bất kỳ đâu trên row và việc kích hoạt nút dùng chung một handler chuyển đổi.
Không có control tương tác lồng nhau và không có sự khác biệt giữa bàn phím và con trỏ.

**Overlay detail nổi không modal.** Panel detail là một overlay nổi không modal neo ở góc dưới bên phải viewport
(`position: fixed`), có cuộn nội bộ riêng, độ nổi (elevation) chuyên nghiệp, **không có backdrop và không có focus
trap**, được xếp lớp bên dưới thanh điều hướng và các dialog của ứng dụng.

- Mở hoặc đóng panel **không bao giờ thay đổi kích thước hoặc bố trí lại bảng kết quả**: wrapper của bảng là một cột ở
  mọi trạng thái chọn, nên danh sách giữ yên để so sánh trong khi chi tiết đang mở. Điều này cố ý không phải là một modal
  chặn.
- Header của panel mang một **nút đóng (✕) accessible** (tên accessible, vùng chạm ít nhất 48 px). Escape đóng panel (một
  dialog xếp chồng phía trên nó sẽ sở hữu Escape thông qua focus scope riêng của nó), click lại vào row đang được chọn
  cũng vậy; cả hai đều trả focus về row khởi nguồn.
- Một cú click ở bất kỳ đâu bên ngoài mọi row kết quả và bên ngoài chính panel cũng đóng nó (click vào một row khác thì
  thay vào đó chuyển việc chọn, thông qua handler riêng của row đó). Trường hợp click bên ngoài này không di chuyển
  focus.
- Ở 900 px hoặc hẹp hơn, overlay trải theo độ rộng viewport với chiều cao có giới hạn.
- Chi tiết vẫn chỉ đọc, với toàn bộ nội dung tracking được mô tả trong §7.2.

## 7.1 Bộ lọc và danh sách

**Bộ lọc.** Một ô tìm kiếm trên PN, WO và Job Number, và các select cho Area, Operation, Machine, Request Type (`NEW` /
`MODIFY`), ưu tiên (chỉ Hot), trạng thái (Active, Stocked, Open, Completed) và cửa sổ hạn.

**Các cột danh sách.**

- PN (kèm tên). Các mục Hot dùng cách trình bày chuẩn `🔥#n` trước PN (§5). Một PN có bản ghi metadata master đã bị xóa
  vẫn hiển thị PN chuẩn của nó bình thường; chỉ tên và metadata từ bản ghi master hiển thị là không có (`—`).
- Active WO Demand (WO · quantity · chip Request Type; Work Order Number để trống hiển thị là `—`).
- Phân bổ hiện tại (các chấm màu Area).
- Quantity đang hoạt động, quantity stocked, quantity scrapped tích lũy.
- Ngày hạn kế tiếp (`—` khi demand không có ngày hạn, §3 rule 9).
- Pill trạng thái, được suy ra như sau:

  | Trạng thái  | Hiển thị khi |
  |-------------|------------|
  | `Active`    | Có quantity đang trong sản xuất. |
  | `Stocked`   | Chỉ khi quantity stocked CHƯA ĐƯỢC ALLOCATE (`STOCKED` hiệu lực trừ allocation đang hoạt động) đang chờ demand mở. |
  | `Open`      | Demand mở không có quantity trong sản xuất và không có stock chưa được allocate (kể cả stock đã được allocate cho công việc trước đó). |
  | `Completed` | Không còn Work Order Demand nào đang mở. |

**Danh sách làm việc với server như thế nào.** Danh sách là UI production trên `GET /api/tracking`.

- Tìm kiếm (có debounce, trên PN, Work Order Number và Job Number của bất kỳ demand nào của PN) và mọi select được đánh
  giá trên server dựa trên row được suy ra. Cửa sổ hạn được đánh giá theo lịch của site dựa trên ngày hạn kế tiếp của PN.
- Các row đến theo thứ tự demand chuẩn.
- Danh sách có giới hạn: `Showing n of m PNs`, với `Show more` mở rộng trang một lần đến giới hạn của server và sau đó yêu
  cầu thu hẹp tìm kiếm.
- Row tiêu đề mang trạng thái feed của view live (`● Live` / `Feed stale — reconnecting`, §5; trạng thái riêng của danh
  sách, không phải của kết nối), và danh sách tự refresh theo nhịp theo dõi chung.

## 7.2 Các mục của panel detail

1. **PN master.** Ảnh PN (MỘT ảnh PN mặc định giữ chỗ dùng chung khi không có ảnh tùy chỉnh, cùng ảnh mặc định mà
   Management → Part Numbers dùng, §14; không bao giờ có ảnh mặc định thứ hai), PN, tên, revision hiện tại (mang tính
   thông tin), giá trị barcode (`PF:PN:…`, được suy ra từ PN chuẩn), và id hoạch định nguồn lực doanh nghiệp
   (enterprise resource planning, ERP). Bản ghi master là metadata hiện tại tùy chọn (PROJECT_PROFILE §8.1): khi không có
   bản ghi master cho PN, mục này hiển thị PN chuẩn và barcode được suy ra của nó với các trường metadata là không có
   (`—`), và các mục lịch sử và trạng thái hiện tại bên dưới không bị ảnh hưởng. Tên, revision, ảnh và ERP id lấy từ chi
   tiết Part Number đã lưu và hiển thị `—` (ảnh mặc định) khi không có.
2. **Active WO Demand.** Một bảng WO · Request Type · requested · allocated · remaining shortage · due · priority, với
   một thanh tiến độ allocation, được gắn nhãn "business demand — separate from Movement". Một dòng được allocate vượt
   quá demand của nó bởi một hiệu chỉnh có thẩm quyền hiển thị `(+n beyond demand)`. Phần tóm tắt và thanh tiến độ chỉ
   đếm mỗi dòng tối đa đến quantity được yêu cầu của nó và nêu riêng quantity vượt demand
   (`Allocated 18 / 20 requested · +2 beyond demand`), nên một dòng thiếu không bao giờ được hiển thị như đã được phủ bởi
   phần dư của một dòng khác.
3. **Quantity hiện tại theo Area.** Các thanh ngang theo từng Area hoặc Machine theo màu Area, với các row queue được
   phân biệt trực quan, được gắn nhãn "derived from Movement history".
4. **Quantity Flows & Routes.** Một khối cho mỗi Quantity Flow.
   - **Chip header.** Header của khối mang **`RouteModeChip`** dùng chung, cách trình bày route-mode duy nhất ở mọi nơi,
     dùng chung với phần tóm tắt và xác nhận Receive Quantity của Scan Station: thông tin mode và route trong một chip,
     phân cách bằng dấu gạch ngang dài và tô màu theo mode; ở đây là `FLOATING — actual trace` / `PLANNED — snapshot`,
     cố ý dùng **biến thể gọn** (chiều cao chip mặc định ở chỗ khác khớp với các chip khác như `TypeChip`).
   - **Flow `PLANNED`.** Nó hiển thị các chip snapshot AssignedRoute của nó (done → current → queued → future), với các
     sai lệch được đánh dấu ai, khi nào và tại sao, và ghi chú bảo toàn kiểm toán.
   - **Flow `FLOATING`.** Nó hiển thị **dấu vết route thực tế được suy ra từ Movement history**: các Area lặp lại được
     giữ nguyên, các flow tách được hiển thị độc lập, và các lần chuyển Repair được đánh dấu tường minh, ví dụ
     `A → B → C → D → B ⟲ REPAIR`.
   - **Bước và mũi tên** hiển thị như **các flex item anh em riêng biệt** (bước, mũi tên, bước, …): mỗi mũi tên được căn
     giữa giữa các bước kề nhau, mũi tên không bao giờ chồng lên card bước, và việc xuống dòng vẫn dễ đọc.
   - Giá thành phẩm không bao giờ xuất hiện như một bước route: `AREA_COMPLETED` là việc hoàn thành bên trong Area nguồn
     hiện có, và chỉ `TRANSFERRED` mới kéo dài dấu vết. Mục này tránh tường minh việc ngụ ý rằng toàn bộ PN đang ở một
     Route Step.
   - **Route đã điều chỉnh.** Một flow `PLANNED` có route của chính nó đã được điều chỉnh (PROJECT_PROFILE §8.10) hiển thị
     `(snapshot, adjusted)` trong ghi chú Planned của nó và một ghi chú `Route adjusted …` cho mỗi lần điều chỉnh: thời
     gian, người dùng, lý do và "the steps after step n were replaced", kèm ghi chú bảo toàn kiểm toán. Mọi lần điều
     chỉnh đều được hiển thị, cũ nhất trước, sau các ghi chú sai lệch, không có gì bị cắt bớt. Các ghi chú thuộc về flow
     có route đã được điều chỉnh: một flow con tách ra hoặc kết quả merge sao chép route như nó đã là (nó hiển thị
     `(snapshot)`), và dòng vị trí của nó nêu tên flow nguồn, là flow có khối mang các ghi chú.
   - **Thời lượng dự kiến.** Dòng vị trí của một Quantity Flow thêm ghi chú khuyến cáo tường minh
     `· exceeds expected duration` (tông warning, viết ra, không bao giờ chỉ bằng màu) khi vị trí đã vượt quá thời lượng
     dự kiến hiệu lực của nó (PROJECT_PROFILE §17: thời điểm cố định `expected_by` của server được đánh giá bằng clock
     chung; không có thì không có gì).
5. **Movement history (bất biến).** Theo thứ tự thời gian ngược: timestamp, loại Movement (mã màu, tên chuẩn, bao gồm
   `SCRAPPED` và `AREA_COMPLETED`; `AREA_COMPLETED` dùng tông success; các lần chuyển Repair mang badge REPAIR tường
   minh), và mô tả đầy đủ (các Area, quantity, Quantity Flow, các Machine, Worker, station, lý do).
   - Các row Movement nêu tên Worker đã được ghi nhận là `W: <name>`, và các ghi chú sai lệch route thêm ` by <name>`;
     các row không có Worker được ghi nhận thì bỏ phần này.
   - Một bản gốc đã được undo vẫn hiển thị với badge `REVERSED` tường minh, bên cạnh row `REVERSED` đã undo nó.
   - Ở độ rộng điện thoại, một row Movement history cho phần mô tả xuống dòng riêng của nó.
   - Các mục trạng thái hiện tại phân biệt việc gán Machine đang hoạt động, xử lý trực tiếp trong Area, hoàn thành trong
     Area (sẵn sàng chuyển, với cách diễn đạt cho người vận hành như
     `Completed processing at Lathe — ready to transfer` và Machine hoàn thành chỉ là ngữ cảnh), chuyển, và hoàn thành
     vào Stockroom. Quantity sẵn sàng chuyển không bao giờ bị ngụ ý là `STOCKED`.
   - Lịch sử chỉ đọc; không tồn tại gợi ý tương tác chỉnh sửa nào.
6. **Scrap history.** Các sự kiện `SCRAPPED` của PN và quantity scrapped tích lũy, kèm ghi chú đối chiếu
   (introduced = active + stocked + scrapped; scrap không bao giờ làm giảm quantity được yêu cầu).
7. **Stocked & Allocation history.** Quantity stocked và các mục Work Order Allocation, kèm ghi chú thứ tự của
   PROJECT_PROFILE §18, và một trạng thái rỗng khi không có gì stocked. Một mục của Management nêu tên người dùng đã ghi
   nhận nó; một hiệu chỉnh vượt demand được đánh dấu `beyond demand`.
8. **Corrections.** Các hành động chỉ dành cho người dùng có thẩm quyền, dưới dạng các nút tường minh: Quantity
   adjustment, Edit assigned Route, Adjust WO Allocation, Change priority, View audit trail.
   - Mọi hiệu chỉnh (một allocation vượt demand, một lần đảo ngược) bắt buộc có lý do và tạo ra lịch sử mới. Một
     allocation thông thường của quantity stocked đã được để lại cho sau, được cung cấp từ cùng dialog
     `Adjust WO Allocation` (§11.6), nhận một ghi chú tùy chọn.
   - `Change priority` và `View audit trail` không phải là luồng hiệu chỉnh và không nhận lý do. `Change priority` là một
     liên kết vào Management → Priority, nơi chứa các hành động Hot list (§8; thay đổi Hot list được thực hiện và kiểm
     toán ở đó: việc xóa và sắp xếp lại được xác nhận, và việc thêm cần một lựa chọn tường minh Work Order Demand). Nó
     được hiển thị cho người dùng có thể đặt hoặc sắp xếp lại ưu tiên. `View audit trail` mở audit trail chỉ đọc của PN
     (§7.4) và được hiển thị cho mọi người dùng có thể mở Tracking.
   - Vì là một thao tác điều hướng và một thao tác đọc, cả hai vẫn khả dụng khi mất kết nối (§3 rule 6 chặn các thao tác
     ghi, và Priority chặn các thao tác ghi của chính nó). Các nút mở luồng hiệu chỉnh vẫn bị vô hiệu khi mất kết nối.

**Dialog Edit assigned Route.** `Edit assigned Route…` (quyền: Assign and edit Routes) mở
`Edit assigned Route — {PN}`.

1. **Chọn flow.** Khi có nhiều flow `PLANNED` đang hoạt động của PN, người dùng trước tiên chọn Quantity Flow một cách
   tường minh (không có flow nào được chọn sẵn; các flow khác giữ route của chúng). Một flow với một lựa chọn duy nhất
   được chọn sẵn, và một PN không có flow Planned đang hoạt động nhận được một giải thích đơn giản.
2. **Bước bị khóa.** Các bước mà quantity đã đến, hoặc mà lịch sử của nó ghi nhận, bị **khóa** (`Done` /
   `Current` / `Recorded`; `Recorded` là bước của một lần đến đã bị undo, vẫn được giữ). Một gợi ý chính xác nêu tên bước
   mà lần đến đúng route kế tiếp được kiểm tra đối chiếu hoặc, khi không có bước nào theo sau, nói rằng không có bước nào
   được mong đợi và lần đến kế tiếp cần một xác nhận sai lệch route.
3. **Chỉnh sửa các bước tương lai.** Chỉ các bước tương lai được chỉnh sửa, với các **row** bước của Planned Routes (các
   quy tắc step-row của §13.2; việc đánh số tiếp tục sau các bước bị khóa; phần đuôi tương lai có thể được làm rỗng),
   dấu hiệu `● Unsaved changes` của §13.2 và chốt chặn `Discard unsaved route changes?`. Lý do là bắt buộc.
4. **Xem lại.** `Review adjustment` dẫn tới một bước xem lại (`Adjust the assigned route?`) hiển thị các bước trước và
   sau, mỗi bước dưới dạng `{Area} · {Operation} · Est. {time}` cộng Machine ưu tiên của nó, với các bước chỉ thay đổi
   hướng dẫn được nêu tên.
5. **Kết quả.** Một route đã thay đổi kể từ khi được mở bị từ chối với `Reload route` (các chỉnh sửa bị hủy bỏ). Một kết
   quả không xác định cung cấp `Retry`, chỉ áp dụng điều chỉnh đúng một lần.

Chỉ AssignedRoute của chính flow này thay đổi, không bao giờ là Planned Route, các flow khác hay Movement history, và
route trước đó vẫn nằm trong lịch sử kiểm toán.

## 7.3 Các trạng thái

- **Loading** hiển thị skeleton theo từng mục.
- **Kết quả lọc rỗng** hiển thị "No PNs match — clear filters".
- **Hiển thị Corrections.** Mục Corrections bị ẩn hoàn toàn, không bao giờ bị vô hiệu, đối với người dùng không có quyền
  nào trong số các quyền của các nút của nó, và một nút mà người dùng không được dùng thì không có mặt; các nút không bao
  giờ được hiển thị ở dạng không hoạt động. Mục này hiển thị các nút của nó theo thứ tự này: `Edit assigned Route…`
  (Assign and edit Routes), `Adjust WO Allocation…` (Edit Work Order Allocation, §11.6), `Change priority` (Set Demand
  Priority hoặc Reorder Hot Items; một liên kết vào Priority) và `View audit trail` (bất kỳ quyền nào mở được Tracking).
  Vì `View audit trail` theo tập quyền đọc của Tracking, mọi người dùng có thể mở Tracking đều thấy mục này với ít nhất
  nút đó. Một nút hiệu chỉnh chưa được implement thì không có mặt, không bao giờ ở dạng không hoạt động.
- **Tag và dòng trạng thái.** Tag `authorized actions — recorded with your name` chỉ hiển thị khi một hiệu chỉnh có ghi
  nhận (`Edit assigned Route…`, `Adjust WO Allocation…`) được cung cấp. Một dòng trạng thái dưới các nút báo kết quả của
  lần hiệu chỉnh gần nhất (một lần điều chỉnh route hoặc một lần thay đổi allocation).
- **Kết nối.** Hai nút mở luồng hiệu chỉnh bị vô hiệu khi không có kết nối; `Change priority` và
  `View audit trail` vẫn được bật.

**Detail làm việc với server như thế nào.** Detail là UI production trên `GET /api/tracking/detail`.

- Mỗi PN được chọn là một lần đọc được poll riêng, với skeleton loading và lỗi có Retry bên trong panel. Một lần refresh
  thất bại giữ detail hoàn chỉnh cuối cùng, với ghi chú `Feed stale — reconnecting` dưới PN.
- **Movement history** theo thứ tự thời gian ngược theo timestamp (`occurred_at`, với id của Movement để phân định hòa)
  và được phân trang theo cùng thứ tự đó (`Showing n of m Movements`; `Show older Movements` nối thêm trang kế tiếp của
  lịch sử bất biến mà không có khoảng trống hay trùng lặp).
- **Scrap history** (§7.2 mục 6) liệt kê các sự kiện `SCRAPPED` của PN như các row của cùng lịch sử đó (timestamp,
  quantity, Area, lý do; một sự kiện đã bị undo được giữ lại kèm badge `REVERSED`) dưới con số tích lũy ròng, với
  `Show older scrap events`.
- **Quantity Flows** được phân trang theo một thứ tự ổn định: flow mới nhất trước, theo id bất biến của flow. Trạng thái
  đang hoạt động hay đã đóng của một flow là thuộc về trình bày và không bao giờ là vị trí của nó. Mỗi lần chỉ tải một
  trang có giới hạn (`Show older Quantity Flows`); các quantity hiện tại vẫn đầy đủ trong mục
  `Current quantity by Area` bất kể việc phân trang flow.
- **Allocation history** được phân trang tương tự (`Show older allocation entries`). Không có gì bị cắt bớt ngoài tầm
  với.
- Một trang được nối thêm của bất kỳ mục nào bị bỏ đi và đọc lại đến cùng độ sâu khi một lần refresh làm dịch chuyển ranh
  giới của nó hoặc thay đổi nội dung mà các row của nó hiển thị (một flow bị đóng hoặc mở lại, một scrap bị undo, một
  route được điều chỉnh), không bao giờ ở một lần refresh không thay đổi gì liên quan.

## 7.4 Audit trail

`View audit trail` (§7.2 mục 8; cho người dùng có thể mở Tracking) mở một dialog chỉ đọc có tiêu đề
`Audit trail — {PN}`, xếp chồng phía trên panel detail. Dialog sở hữu Escape và trả focus về nút khi đóng.

**Những gì nó liệt kê, mới nhất trước:**

- chi tiết Part Number của PN (được tạo, được chỉnh sửa, ảnh được thêm, thay thế hoặc xóa, bị xóa);
- các Work Order và dòng Work Order Demand yêu cầu PN đó (được tạo, được chỉnh sửa, một Work Order được hoàn thành bởi
  một thay đổi demand). Một dòng đã bị xóa kể từ đó giữ lịch sử đã ghi nhận của nó và được đánh dấu `(since deleted)`. Các
  thay đổi header và việc hoàn thành của một Work Order xuất hiện cho mọi PN mà nó yêu cầu;
- mọi thay đổi Hot rank của các dòng đó, kèm điều đã gây ra nó. Một dòng chỉ bị dịch chuyển vì một mục khác được thêm,
  xóa hoặc di chuyển sẽ nói như vậy, không bao giờ nói rằng chính nó đã bị di chuyển hoặc bị xóa;
- các mục allocation của Management của nó, mọi lần đảo ngược allocation (bao gồm cả đảo ngược Stockroom) và các hiệu
  chỉnh vượt demand có thẩm quyền, được đánh dấu như vậy;
- các lần điều chỉnh route của các Quantity Flow của nó (các bước bị thay thế và các bước mới, mỗi bước kèm Area,
  Operation, thời gian ước tính, Machine ưu tiên và hướng dẫn của nó).

**Mỗi mục hiển thị** thời gian; điều gì đã thay đổi và trên Work Order, dòng demand hoặc Quantity Flow nào; người dùng đã
ghi nhận nó (avatar và tên, hoặc không có gì khi không có người dùng nào được ghi nhận, như với các bản ghi Scan Station
và lịch sử từ trước khi có đăng nhập); lý do khi có được đưa ra; và các giá trị trước → sau bằng ngôn ngữ người dùng
(không có định danh trường).

**Những gì nó không liệt kê.** Các Movement sản xuất, bao gồm cả Undo, vẫn nằm trong Movement history (§7.2 mục 5), và
các allocation Stockroom thông thường vẫn nằm trong allocation history (§7.2 mục 7). Các thay đổi cấu hình không liên
quan đến PN này không được liệt kê. Không có gì trong trail có thể chỉnh sửa hoặc xóa.

**Phân trang.** Nó phân trang như các lịch sử Tracking khác (`Showing n of m entries`, `Show older entries`, keyset theo
thời gian, không có gì bị cắt bớt ngoài tầm với) và là một lần đọc snapshot được lấy khi nó mở: việc phân trang không bao
giờ lặp lại một mục và không bao giờ bỏ qua một mục được ghi nhận trước khi trail được mở. Một thay đổi được ghi nhận
trong khi nó đang mở có thể xuất hiện ở một trang sau hoặc chỉ sau khi mở lại, và khi đó trang cuối cùng nói
`reopen the audit trail to include changes recorded since it was opened`.

**Kết nối và các trạng thái.** Nó là một thao tác đọc và vẫn khả dụng khi mất kết nối (một lỗi đọc hiển thị kèm Retry).
Các trạng thái: loading; error có Retry; rỗng `No recorded changes for {PN} yet.`

---
# 8. Priority Management

Priority Management quản lý danh sách Hot Work Order Demand của Department (PROJECT_PROFILE §21 Priority Management).
Priority thuộc về Work Order Demand, nên nhiều Work Order cho cùng một PN có thể giữ các rank khác nhau.

### Danh sách

Danh sách hiển thị mỗi Hot entry cùng rank của nó theo cách trình bày Hot chuẩn (`🔥#n` ngay trước PN, §5), WO và Job
Number, chip Request Type, các số liệu demand (requested / allocated / shortage), phân bố hiện tại, và due date tô màu
theo thang (`—` khi không có, §3 rule 9).

### Thêm

Nút "+ Add to Hot list" mở một dialog có một ô search duy nhất, nhận cả văn bản tự do (PN, WO, Job Number) *lẫn* các
lần quét barcode PN.

- **Phân giải barcode.** Khi **không có** Work Order Demand đủ điều kiện nào cho PN được quét, không gì được thêm. Khi
  có đúng **một**, nó được thêm trực tiếp. Khi có **nhiều hơn một**, danh sách lọc theo PN đó, và bắt buộc phải có một
  **lựa chọn tường minh** Work Order Demand cụ thể: ứng dụng không bao giờ chọn kết quả khớp đầu tiên khi barcode mơ hồ.
- Entry mới luôn vào ở **cuối** danh sách. Việc thêm một entry mới ở cuối được thực hiện trực tiếp, không cần
  xác nhận đổi thứ tự.

### Đến từ Tracking

`Change priority` của Tracking (dành cho người dùng được đặt hoặc sắp xếp lại priority) mở view này cho một PN. Bản thân việc
đến view này không thay đổi gì.

- Khi PN có các Hot entry, chúng được highlight và một dòng trạng thái nêu các rank của chúng
  (`{PN} is on the Hot list at #2 and #5.`).
- Nếu không, với người dùng được thêm, dialog Add mở ra và liệt kê tất cả và chỉ những Work Order Demand của PN đó có thể vào
  Hot list (khớp PN chính xác, không bao giờ là search theo văn bản; ô search để trống cho một lần search khác).
  Không gì được thêm cho đến khi một dòng được bấm, kể cả khi chỉ có một dòng được liệt kê.
- Với người dùng chỉ được sắp xếp lại, một dòng trạng thái nói rằng PN không nằm trong Hot list và tài khoản có thể sắp xếp
  lại nhưng không được thêm.

### Gỡ bỏ

Mỗi entry có một affordance gỡ bỏ (✕). Bấm vào nó mở một **dialog xác nhận nêu rõ PN và Work Order Demand**; Cancel
không thay đổi gì. Khi Confirm, entry được gỡ bỏ, các rank còn lại dồn lại để lấp khoảng trống, thay đổi được áp dụng và
ghi audit, và Undo có thể khôi phục nó. Xác nhận bảo vệ việc gỡ bỏ; nó không đưa lại một workflow save-or-cancel
riêng.

**Gỡ bỏ tự động.** Một entry có line trở thành được allocate đủ (kể cả do Work Order của nó hoàn tất) rời danh sách cùng
với lần allocation hoặc lần sửa demand đã gây ra điều đó: không có xác nhận, các rank dồn lại để lấp khoảng trống, và
thay đổi được ghi audit. Danh sách hiển thị lần đọc gần nhất của nó. Một xác nhận thực hiện trên một danh sách cũ hơn
bị từ chối vì stale và danh sách hiện tại được hiển thị, còn một step Undo hoặc Redo có entry đã rời danh sách thì bị bỏ
kèm một thông báo.

### Sắp xếp lại bắt buộc xác nhận, trình bày dưới dạng hai snapshot

Mọi thao tác làm thay đổi thứ tự của các Hot entry hiện có (kéo-thả với grip nhìn thấy được, Move Up, Move Down, Undo và
Redo) đều mở một **dialog xác nhận trước khi áp dụng**. Danh sách nhìn thấy **không bao giờ được đánh số lại trước
xác nhận**.

**Tóm tắt.** Dialog mở đầu bằng một tóm tắt chính về mục mà người dùng đã di chuyển
(`Move 2027-60-8114-00 · WO 007010 from #4 to #2`), cộng với một dòng phụ được tính toán như
`2 other demands will shift down.`. Tên của action triển khai chỉ là chi tiết phụ. Bên dưới phần tóm tắt PN đã di
chuyển, một **khối tác động và action gọn** nêu tác động dịch chuyển bằng một câu dễ đọc
(`1 other demand will shift up.`) và tách nhãn `Action` khỏi giá trị của nó. Giá trị action (`Move Down`, …) chỉ được
nhấn mạnh bằng độ đậm và cách trình bày văn bản theo ngữ nghĩa: không có pill trang trí, và không bao giờ nổi hơn phần
tóm tắt PN đã di chuyển.

**Hai snapshot.** Tiếp theo là **bố cục hai snapshot `Current Position` → `New Position`**, giới hạn đúng trong khoảng
rank bị ảnh hưởng (không bao giờ là toàn bộ Hot list), với **một mũi tên chuyển tiếp hướng xuống, căn giữa** giữa hai
phần, khác biệt về thị giác so với các mũi tên rank trên từng dòng.

- Mỗi dòng entry tách rank, hướng, PN và metadata WO/Job, với rank đứng đầu.
- Trong **Current Position**, một **mũi tên ↑/↓ cạnh rank hiện tại** trên từng dòng cho biết entry sẽ di chuyển về đâu
  (↑ về phía rank #1, ↓ ra xa nó). Các dòng giữ theo thứ tự rank hiện tại.
- **New Position** hiển thị thứ tự sau khi áp dụng, không có mũi tên trên từng dòng. Thay vào đó mỗi dòng hiển thị đầy
  đủ chuyển tiếp rank của nó **`#<current-rank> → #<new-rank>`**: rank cũ và mũi tên hiển thị nhạt, rank mới đậm nhất,
  và các dòng được sắp theo rank mới.
- Một entry chỉ tồn tại ở một phía của thay đổi (Undo hoặc Redo khôi phục một entry đã bị gỡ hoặc rút lại một entry đã
  được thêm) ghi rõ phía bị thiếu bằng một placeholder **`Not listed`** rõ ràng: `Not listed → #n` cho một entry được
  khôi phục hoặc mới được liệt kê, `#n → Not listed` cho một entry bị gỡ. Nó không bao giờ bị lược bỏ một cách im lặng.
- Entry được di chuyển trực tiếp được highlight, còn các entry bị dịch chuyển gián tiếp hiển thị ít nhấn mạnh hơn.

**Track vị trí dùng chung, co giãn theo nội dung.** Một grid bao ngoài bao lấy `Current Position`, mũi tên chuyển tiếp
và `New Position`, và các khung section, danh sách và dòng đều tham gia vào các track của nó qua một chuỗi subgrid. Vì
vậy track rank/chuyển tiếp lấy kích thước từ giá trị thực rộng nhất của **một trong hai** phía (ví dụ
`Not listed → #3`), và cột PN nằm ở cùng độ lệch ngang trong cả hai phần: thẳng hàng giữa các phần, giãn cách theo nội
dung thực tế, và không bao giờ chồng lấn. Không có nhãn hay track rank độ rộng cố định nào, nên không có khoảng trống lớn
nào mở ra giữa giá trị vị trí và PN, và rank, PN cùng metadata WO/Job không bao giờ lẫn vào nhau.

**Đường chia snapshot.** Mỗi phần có **một đường kẻ dọc giữa track vị trí dùng chung và cột PN**: một grid item duy nhất
trải qua các dòng, gắn vào cạnh của track dùng chung, không bao giờ là các mảnh border trên từng dòng nội dung. Các dòng
được ghim tường minh vào các grid line của chúng, nên auto-placement không thể né các ô của đường chia. Đường chia chỉ
tồn tại trong môi trường hỗ trợ subgrid; nó bị ẩn trong fallback không có subgrid và trong bố cục xếp chồng ở độ rộng
560 px hoặc hẹp hơn.

**Màn hình hẹp.** Fallback cho màn hình hẹp xếp rank/chuyển tiếp lên trên PN và metadata theo cùng một cách ở cả hai
phần, giữ cho quan hệ rank–PN–WO/Job dễ đọc.

**Typography.** **PN** dùng màu văn bản chính, độ đậm mạnh hơn, monospace và giãn cách rõ ràng. **Work Order và Job
Number** hiển thị thành một chip thông tin nhẹ (`WO 007001 · Job 18112`: nền và viền tinh tế, bo tròn, tông phụ, không
có hành vi nút, toàn bộ metadata trong tooltip), dựng từ các field tường minh `workOrderNumber` và `jobNumber`, không bao
giờ được tách ra từ một chuỗi hiển thị.

**Các nút.** `Apply ranking` và `Cancel (Esc)`. Cancel giữ nguyên thứ tự danh sách và cả lịch sử Undo lẫn Redo. Các
xác nhận của Undo và Redo dùng tiêu đề hướng tới người dùng `Restore previous ranking` và `Reapply ranking` và trình bày
cùng bố cục snapshot.

### Undo và Redo thay cho save-or-cancel

Mọi thay đổi đã được xác nhận đều được áp dụng và ghi audit, và có thể lùi lại và tiến tới bằng các nút Undo và Redo;
mỗi step tự nó là một thay đổi thứ tự và hiển thị cùng xác nhận. Các nút bị vô hiệu hóa khi lịch sử tương ứng trống.
**Độ sâu là không giới hạn trong application session hiện tại:** không có giới hạn số nào. Lịch sử Undo/Redo thuộc về
người dùng đã đăng nhập: nó kết thúc cùng application session, khi người dùng đăng xuất, hoặc khi một người
dùng khác đăng nhập (một lần đăng nhập hết
hạn được chính người dùng đó gia hạn thì giữ nguyên nó).

### Ghi chú footer

Một ghi chú footer nhắc lại các quy tắc bằng ngôn ngữ của người dùng: Hot demand luôn được làm trước, theo thứ tự rank, và việc
sắp thứ tự tuân theo thứ tự demand chuẩn: ① Hot rank; ② demand có ngày, sớm nhất trước, với demand không có ngày xếp sau
mọi demand có ngày, sắp theo ngày nhận Work Order (PROJECT_PROFILE §18). Các định danh triển khai (`priority_rank`,
tie-breaker) không bao giờ hiển thị. Ghi chú cũng nêu rằng một entry tự rời danh sách khi line của nó được allocate đủ.

---

# 9. Administration

Administration tách biệt khỏi production và tập trung vào **quản trị hệ thống**. Điều hướng sidebar của nó được nhóm như
sau:

| Nhóm                 | Các section |
|----------------------|-------------|
| **Organization**     | Departments, Areas, Operations, Workers |
| **Production setup** | Scan Stations, Barcode configuration, Scan behavior |
| **Access**           | Users, Roles & permissions |
| **Policies**         | Worker sessions, Machine assignment, Correction permissions, History archival & purge, Department display settings, Settings |

Mỗi section là một mẫu bảng-cộng-editor chuẩn. Bảng Areas là ví dụ tham chiếu: Area (màu và tên), Operations, Machine
assignment (Direct processing — no Machines / Queue → assign (one-shot); mode suy ra từ việc Area có Machine nào hay
không, một Machine hoạt động như nhiều Machine, không bao giờ từ một cấu hình theo số lượng), Machines, Worker ID mode
(Disabled / Fixed Worker / Scanned session), cờ Terminal, trạng thái Active.

Operations được quản lý như mô tả trong PROJECT_PROFILE §8.5: mỗi Operation thuộc về một Area, và bảng Areas liệt kê các
Operation mà một Area hỗ trợ.

Việc sửa các thuộc tính hiển thị của một Area hiển thị một ghi chú inline rằng định danh và barcode là ổn định và lịch sử
không bị ảnh hưởng. Các thao tác phá hủy (deactivate một Area còn quantity đang hoạt động) bị chặn kèm giải thích, chứ
không phải xác nhận cho qua.

### Policies

- **Worker sessions** sở hữu **sliding inactivity timeout** của Scanned-session (§4.12): một giá trị mặc định với các
  override tùy chọn theo Area, tính bằng số phút nguyên từ 1 đến 720 (mặc định 15), không có lịch ca. Nó cũng sở hữu
  **các option badge-confirmation cho action nhạy cảm**: ba option On/Off độc lập (mặc định On), mỗi option được lưu
  ngay khi được đổi, quyết định liệu cổng xác nhận cuối LUÔN có mặt của `DONE`, `QUEUE` return và Undo (§4.6) có phải là
  một lần quét badge của Worker trong các Area Scanned-session hay không. Khi một option là Off (và luôn luôn trong các
  Area Fixed-Worker và Disabled), cổng thay vào đó là một câu hỏi xác nhận cuối. Bản thân cổng không bao giờ là tùy chọn.
- **Machine assignment** là một policy statement chỉ-đọc về hai mode sở hữu của Area, không phải một Machine registry:
  một statement hai dòng (không có Machine → `Direct processing (no Machines)`; một Machine trở lên →
  `Queue → assign (one-shot)`, không bao giờ tự động) không có entry action, trỏ tới Management → Machines và bảng
  Areas.
- **Correction permissions** giữ công tắc **Undo reason**: một option On/Off toàn cục (mặc định Off) bắt buộc phải có
  reason cho mọi Undo (§4.5; PROJECT_PROFILE §16 "require a reason when configured"), do server enforce và được lưu ngay
  khi được đổi. Ai được undo hoặc correct là một bảng role × correction-permission bên dưới công tắc Undo reason. Section
  nêu những correction permission nào đã được kiểm tra cho đến nay: Edit Work Order Allocation được kiểm tra cho
  allocation từ Management và việc đảo ngược allocation, còn Undo recent eligible scans áp dụng tại các Scan Station qua
  role được đánh dấu Applied at Scan Stations; các correction permission khác chưa được kiểm tra.
- **History archival & purge** (Admin-only) được mô tả dưới heading riêng của nó bên dưới.
- **Department display settings** được cấu hình **theo Department, không bao giờ toàn cục**: một bảng các Department
  với một editor cho thời gian rotation của Production Board (số giây cho mỗi dòng hiển thị 1–60, thời gian dừng tối
  thiểu mỗi trang 1–300 s; mặc định 3 và 6).
- **Settings** chứa hai panel:
  - panel **Due Soon warning** sở hữu cấu hình đứng sau mọi due countdown được suy ra (§3 rule 12): **Minimum
    warning days** (0–365), **Lead-time warning percentage** (1–100) và **Maximum warning days** (0–365, không bao giờ
    dưới giá trị minimum), với các giá trị ban đầu 2 ngày, 15 % và 7 ngày. Đó là một policy toàn cục;
  - panel **User sign-in**, chỉ dành cho các application User và tách biệt khỏi **Worker sessions**: thời hạn đăng nhập của người dùng tính theo ngày hoặc không bao giờ (ban đầu 30 ngày), số lần đăng nhập thất bại trước khi khóa (10) và thời gian
    khóa (15 phút), và việc người dùng có phải thay mật khẩu do administrator đặt ở lần đăng nhập kế tiếp hay không (On). Nó được
    đọc bởi một người dùng đã đăng nhập (người khác thấy lời nhắc đăng nhập) và chỉ người dùng có role được cấu hình system settings mới
    sửa được.

  Section Settings nêu rằng các application setting khác chưa khả dụng.

### Không có section Machines, Route Templates hay PartNumbers

Machines, Planned Routes (Route Templates) và metadata master của PartNumber là production master data do các production
role được cấp quyền sở hữu. Chúng được quản lý trong **Management → Machines** (§12), **Management → Planned Routes**
(§13) và **Management → Part Numbers** (§14), và Administration **không giữ màn hình trùng lặp nào** cho chúng
(PROJECT_PROFILE §20, §21). Việc bảo trì PartNumber master (duy trì metadata và xóa cứng, theo PROJECT_PROFILE §8.1 và
§28) nằm trong Management → Part Numbers.

### Barcode configuration — định dạng Machine Asset Tag

Section Barcode configuration (Production setup) chứa panel **Machine Asset Tag format**: một prefix được cố ý giữ đơn
giản cộng với một dãy số được đệm số 0 (PROJECT_PROFILE §8.6; ví dụ `CD-` + 4 chữ số → `CD-0001`), không bao giờ là
một template engine.

- Panel giải thích rằng mọi Machine nhận Asset Tag của nó tự động khi được tạo trong Management → Machines; rằng các tag
  là duy nhất, không bao giờ được dùng lại (Machine đã ngừng sử dụng giữ tag của mình) và không bao giờ thay đổi; và rằng
  barcode của Machine là Asset Tag trong namespace `PF:MACHINE:`.
- Nó cung cấp một ô nhập văn bản **Prefix** (khoảng trắng và `:` bị từ chối kèm lỗi inline) và một ô nhập **Number
  length (digits)** (1–8; số chữ số là độ rộng tối thiểu, và các dãy số dài hơn không bao giờ bị cắt bớt).
- Nó hiển thị preview trực tiếp các tag kết quả (`CD-0001, CD-0002, …`), **Next Asset Tag** so với các Machine hiện có,
  và **Scanned barcode** kết quả.
- Một ghi chú kết thúc nêu rằng việc đổi định dạng chỉ áp dụng cho các Machine được tạo sau đó: các Asset Tag hiện có
  không bao giờ bị đổi tên hay tạo lại.
- Đây là một form setting, không phải một bảng entry, nên section không hiển thị action `+ New entry`.
- Panel là cấu hình được lưu, dựa trên surface `/api/barcode-configuration`. Nó đọc và lưu định dạng đang dùng; preview
  **Next Asset Tag** theo bộ đếm never-reuse được lưu của server (việc đổi định dạng không bao giờ reset dãy số); và việc
  tạo Machine trong Management → Machines cấp phát từ chính định dạng được lưu này.
- Đầu vào không hợp lệ bị từ chối đúng như được nhập: một prefix chứa khoảng trắng hoặc `:`, hoặc một số chữ số nằm
  ngoài các số nguyên 1–8, hiển thị lỗi inline và vô hiệu hóa Save. Không gì bị trim, clamp hay thay thế để ép cho hợp lệ.

### Cấu hình được lưu và các section chưa khả dụng

Các section **Minimum Environment Setup** của IMPLEMENTATION_ROADMAP (Departments, Areas, Operations, Scan Stations và
Barcode configuration) là UI cấu hình được lưu, dựa trên surface `/api`. Các bảng và editor đọc và ghi môi trường đang
chạy với các trạng thái loading, error và retry tường minh; các lần ghi bị server từ chối được hiển thị tại chỗ (không gì
được âm thầm xác nhận cho qua); và chặn ghi khi offline kiểm soát mọi write action. Bảng và editor Areas cấu hình Worker
ID mode: Disabled, Fixed Worker kèm Fixed Worker của nó, hoặc Scanned session.

Workers, Users, Roles & permissions, Correction permissions, Worker sessions, Department display settings, các panel Due
Soon warning và User sign-in của section Settings, retention period của History archival & purge và statement Machine
assignment cũng là cấu hình được lưu. Users và Roles & permissions nêu rằng người dùng đăng nhập bằng login name và mật khẩu, và
những permission nào đã được kiểm tra cho đến nay.

Mọi thứ khác tự trình bày một cách trung thực. Một section không có nội dung được định nghĩa thì nêu rằng nó chưa khả
dụng, với entry action bị vô hiệu hóa; nó không bao giờ được làm cho trông như đang hoạt động.

- **Scan behavior** nêu rằng nó chưa khả dụng và các setting của nó chưa được định nghĩa, với entry action bị vô hiệu hóa.
- Section Settings nêu rằng các application setting khác chưa khả dụng.
- History archival & purge nêu rằng các lần chạy archival và purge chưa khả dụng (xem bên dưới).

Scan behavior và các general setting còn lại không có nội dung được định nghĩa (IMPLEMENTATION_ROADMAP liệt kê chúng là
`Deferred`). Không statement nào trong số này hứa hẹn một phase. Machines, Planned Routes và Part Numbers vẫn được quản lý
trong Management bởi các production role được cấp quyền.

### Truy cập Administration

- **Đăng nhập.** Mọi section Administration cần một người dùng đã đăng nhập. Vào Administration khi chưa đăng nhập sẽ mở dialog
  đăng nhập, và một panel đăng nhập thay thế các section cho đến khi đó (Cancel rời khỏi nó).
- **Đăng nhập hết hạn.** Một lần đăng nhập hết hạn mở lại dialog đăng nhập trên section đang mở và giữ công việc của nó cho cùng
  người dùng, còn khi một người dùng khác đăng nhập thì các section bắt đầu lại từ đầu. Một lần lưu theme phát hiện phiên đăng nhập đã kết thúc
  thì không mở dialog nào (§2.1): khi đó dialog mở ở request bị từ chối kế tiếp của section.
- **Thay mật khẩu.** Trong khi một mật khẩu do administrator đặt vẫn còn phải được thay, các section chờ sau dialog
  `Choose a new password` (công việc đang mở của cùng người dùng được giữ lại).
- **Xem và thay đổi.** Mọi người dùng đã đăng nhập đều được xem các section. Các thay đổi của mỗi section cần permission riêng
  của nó (Departments, Areas, Operations, Workers, Scan Stations, Barcode configuration, Worker sessions, Correction
  permissions, Settings, và Roles & permissions / Users đều nêu permission của mình). Không có nó thì section là
  view-only: các control của nó bị ẩn và một dòng `View only` nêu permission. Server kiểm tra các permission này.
- Badge của Worker chỉ được hiển thị cho người dùng được quản lý Workers.
- **Bảo vệ Correction permissions.** Việc thay đổi các correction permission, permission quản lý chúng, hoặc người dùng có
  role giữ chúng cần permission quản lý correction permission. Các correction permission trong Policies → Correction
  permissions được sửa bằng permission đó.
- **Quy tắc người giữ cuối cùng.** PartFlow từ chối một thay đổi khiến không còn người dùng active nào có mật khẩu được quản
  lý người dùng và role, hoặc được quản lý correction permission.
- Các permission chưa cấp quyền gì được đánh dấu như vậy trong Roles & permissions.
- **Applied at Scan Stations.** Role được đánh dấu **Applied at Scan Stations** (ban đầu là Operator) quyết định mọi Scan
  Station đã enroll được làm gì.
- Chỗ nào tài liệu này nói "Admin-only" (History archival & purge), nó có nghĩa là permission cấu hình system settings,
  ban đầu được cấp cho role Administrator (PROJECT_PROFILE §20).

### Scan Stations

Editor `New Scan Station` hỏi Area trước và **đề xuất Station ID** từ Area đó: tên Area viết hoa (bỏ dấu, mỗi đoạn ký
tự khác được thay bằng một dấu `-`), tiếp theo là `-ST-` và một số có ít nhất hai chữ số. Số này lớn hơn một so với số
cao nhất mà bất kỳ station hiện có nào đã dùng với cùng tiền tố đó, không phân biệt chữ hoa thường, kể cả các station
inactive và các station đã được gắn sang Area khác, vì một Station ID không bao giờ được đổi tên hay dùng lại
(`CUT-ST-01`, rồi `CUT-ST-02`). Đề xuất đi theo Area đang chọn cho đến khi người dùng gõ một ID của riêng mình; một ID
đã gõ được giữ nguyên, và một ô đã xóa trống lại đi theo Area. Server vẫn kiểm tra ID được nhập.

### Thiết bị Scan Station

Production setup → **Scan Stations** cho mỗi station một action `Devices…` mở các thiết bị đã enroll và các enrollment
code chưa hết hạn của nó, liệt kê tên, trạng thái (`Enrolled`, hoặc `Code issued` kèm thời điểm hết hạn) và last seen
(request gần nhất của thiết bị, kể cả các request bị từ chối). Một thiết bị đã bị revoke hoặc một code đã hết hạn không
còn được liệt kê khi danh sách được tải lại. Ô Devices của bảng hiển thị, trên một dòng trải ngang ô, tên các thiết bị đã
enroll của station (`Not enrolled` khi chưa có thiết bị nào, kèm `· code issued` trong khi có một code đang chờ) và
nút `Devices…`. Khi các tên không vừa, chúng kết thúc bằng dấu ba chấm và hiện đầy đủ khi rê chuột; Station ID, Area
và trạng thái không bao giờ bị xuống dòng vì chúng. Một cú click ở bất kỳ đâu trong ô đó mở danh sách thiết bị, còn
phần còn lại của hàng mở editor của station.

- `Enroll device…` hỏi tên thiết bị và hiển thị một enrollment code dùng một lần (hiệu lực 15 phút, dùng một lần, chỉ
  hiển thị một lần) để nhập trên thiết bị của station (§4.13).
- `Re-enroll…` phát hành một code thay thế một thiết bị đã enroll khi code được dùng, nên station không bao giờ bị tối
  đi ở khoảng giữa.
- `Revoke…` dừng một thiết bị ngay lập tức; một code đang chờ được hủy theo cùng cách.
- Việc enroll cần permission quản lý Scan Stations và, trong khi role áp dụng tại Scan Stations giữ một correction
  permission, cần permission quản lý correction permission. Việc revoke chỉ cần permission đầu tiên.
- Không có các permission đó thì các control tương ứng bị ẩn và một ghi chú nêu điều còn thiếu.

### Workers và Users

Màn hình **Workers** (Organization) quản lý hồ sơ Worker: tên, barcode badge (badge nhân viên sẵn có của công ty,
§4.12), avatar và trạng thái active. Worker có một id nội bộ ổn định và không có mã số nhân viên. Màn hình **Users**
(Access) là riêng biệt và quản lý các tài khoản ứng dụng cùng avatar của chúng. Workers và Users không bao giờ được gộp
thành một thực thể (§4.12, PROJECT_PROFILE §8.13).

- **Editor Workers.** Nó lưu badge bằng chữ in hoa và hiển thị preview `Saved as:` khi giá trị đó khác với những gì đã
  được gõ hoặc quét. Nó upload một avatar (PNG, JPEG hoặc WebP; ảnh lớn được resize trước khi upload) hoặc xóa avatar,
  hiển thị chữ cái đầu khi không có avatar, và deactivate một Worker mà không có thao tác xóa nào.
- **Editor Users.** Nó lưu tên, một login name bằng chữ thường (kèm preview `Saved as:`), đúng một role và một avatar tùy
  chọn, và deactivate một người dùng mà không có thao tác xóa nào. Section nêu rằng người dùng đăng nhập bằng login name và mật khẩu,
  rằng `Set password…` cấp cho một người dùng một mật khẩu, và những permission nào đã được kiểm tra cho đến nay. Với quản trị viên
  người dùng, bảng Users còn có cột `Sign-in` hiển thị trạng thái đăng nhập của từng người dùng, và `Set password…` đặt một
  mật khẩu tạm, kết thúc các phiên đăng nhập của người dùng và xóa khóa.
- **Roles & permissions** liệt kê các role có tên (ban đầu là Administrator, Manager và Operator, với đúng các capability
  mà PROJECT_PROFILE §20 liệt kê là quyền cấp ban đầu) và sửa tên của từng role cùng các permission của nó, nhóm thành
  Administration, Production master data, Work Orders, priority and reports, và Scan Station. Bốn correction permission
  (Undo recent eligible scans, Perform quantity corrections, Edit Work Order Allocation, Perform authorized historical
  corrections) chỉ được sửa trong Policies → **Correction permissions**, dưới dạng một bảng role × permission bên dưới
  công tắc Undo reason.

### History archival & purge

Đây là việc bảo trì Admin-only đối với retention của lịch sử Movement (PROJECT_PROFILE §28 Administrative Archival and
Purge).

- **Thiết kế mục tiêu.** Một retention policy cấu hình được (ví dụ N năm trong database chính), một ngưỡng dung lượng,
  hoặc một yêu cầu thủ công kích hoạt một chuỗi archive export không mất dữ liệu → verification → purge đúng chính xác
  các dòng đã được archive, không bao giờ purge trước. Nó chỉ chạy qua đường bảo trì Admin có đặc quyền (runtime thông
  thường không bao giờ được UPDATE hay DELETE các dòng Movement) và giữ các Movement liên quan đi cùng nhau (ví dụ một
  correction `REVERSED` và bản gốc của nó), nên không dòng nào được giữ lại tham chiếu tới một dòng đã bị purge. Nó có
  preview phạm vi và tác động, một reason bắt buộc, và audit đầy đủ về ai đã chạy gì và khi nào. Các setting retention
  nằm ở đây, không bao giờ trong logic của production workflow.
- **Retention period.** Section lưu **retention period** dưới dạng cấu hình có audit mà không production workflow nào
  đọc. Form cung cấp **No retention period** (trạng thái ban đầu) hoặc **`Keep a set period of history`** với một số
  tháng nguyên từ 12 đến 1200, và một dòng trung tính nhắc lại period theo năm và tháng (ví dụ
  `Retention period: 10 years.`). Việc lưu không hỏi xác nhận nào, vì nó không archive hay xóa gì.
- **Chưa khả dụng.** Các lần chạy archival và purge (archive export, verification, purge, preview phạm vi và tác động,
  reason), ngưỡng dung lượng dữ liệu và các yêu cầu thủ công chưa khả dụng (IMPLEMENTATION_ROADMAP). Cho đến khi chúng
  khả dụng, section nêu rằng chúng chưa khả dụng và toàn bộ lịch sử Movement vẫn nằm trong database.

---

# 10. Completion / Receiving UI (Stockroom Scan Station)

Station Stockroom dùng lại shell của Scan Station với một step bổ sung. Sau Movement `STOCKED`, một **dialog allocation**
hiển thị cách chia được gợi ý trên các Work Order Demand còn tồn đọng theo đúng thứ tự demand chuẩn của PROJECT_PROFILE
§18: ① Hot rank cao nhất; ② demand có ngày, sớm nhất trước, với demand không có ngày xếp sau mọi demand có ngày, sắp theo
ngày nhận Work Order.

- Mỗi dòng hiển thị Work Order, quantity yêu cầu, quantity đã allocate trước đó, shortage còn lại và quantity đề xuất,
  điều chỉnh được bằng các stepper +/− và một ô nhập quantity có clamp. Operator có thể xem lại và điều chỉnh gợi ý
  trước khi xác nhận (PROJECT_PROFILE §18).
- Tổng đã allocate so với quantity đã stock luôn được hiển thị. `Confirm allocation` chỉ được bật khi tổng đã
  allocate bằng quantity đã stock.
- Việc nhận hàng thường ngày không cần Manager tham gia. Administrator và Manager có thể điều chỉnh allocation sau đó,
  với mọi thay đổi đều được ghi audit (§11.6).

### Luồng nhận hàng tại station Stockroom

Việc nhận hàng ở Stockroom là production UI trên surface `/api`, trên cùng shell Scan Station.

1. **Quét.** Tại một station gắn với một Area terminal, một lần quét PN mở `Receive into Stockroom` (không bao giờ là
   transfer thông thường) cho đúng các nguồn mà **server** đánh dấu là stock được (`stock_available` trên kết quả phân
   giải PN): một nguồn thì trực tiếp, nhiều nguồn thì qua lựa chọn nguồn tường minh. Một station terminal không có gì để
   nhận sẽ giải thích điều đó kèm các quantity đã stock và chưa được allocate, và không gì được ghi nhận.
2. **Nhận.** Wizard nhận hàng giữ quantity 1 đến MAX của wizard transfer, với quantity một phần được server tách ra bên
   trong cùng một lệnh, xác nhận lệch route và mô hình ghi one-shot (một response bị mất được thử lại với cùng
   `device_event_id`). `Confirm stocking` là điểm ghi duy nhất, và `STOCKED` được nêu tên trên dòng
   `Recorded event(s)`.
3. **Allocate.** Sau khi server đã xác nhận lần ghi, dialog allocation hiển thị gợi ý của server cho đúng quantity đã
   stock: mọi line còn tồn đọng theo thứ tự demand chuẩn, với Work Order, requested, đã allocate trước đó, shortage còn
   lại và quantity đề xuất. Xác nhận gửi quantity đã stock làm quantity allocation.
   - Một lần server từ chối giữ dialog mở kèm lý do của nó và một gợi ý được làm mới.
   - Một response bị mất đóng băng các line để thử lại cùng một ý định.
   - Thành công chỉ được báo sau khi server trả lời, nêu tên các Work Order đã hoàn tất. Sau đó inventory được làm mới
     và ô nhập barcode được focus lại.
4. **Allocate sau.** `Leave in stock — allocate later` không ghi nhận gì.

Quantity đã stock không bao giờ bị undo: stack Undo bỏ qua nó (§4.5).

---

# 11. Work Orders

Work Orders là một sub view của Management (nó theo theme mode toàn cục, §2.1) dùng để nhập Work Order thủ công, import
từ file (§11.7) và release vào production một cách tường minh (PROJECT_PROFILE §13; §21 *Work Orders*). Nó chỉ xử lý
business demand; nó không phải là chức năng khách hàng, định giá, xuất hóa đơn, giao hàng, mua hàng hay kế toán kiểu
hoạch định nguồn lực doanh nghiệp (enterprise resource planning, ERP).

**Route.** View nằm trên hai route. **Danh sách WO đang hoạt động** trên `/management/work-orders` là màn hình vào. **Trang
Completed Work Orders** chỉ-đọc (§11.5) nằm trên `/management/work-orders/completed`: một route thật có thể deep-link
(không phải toggle hay dialog), vì lịch sử đã hoàn tất là không giới hạn và phải tồn tại qua refresh, deep link và
back/forward của trình duyệt. Cả hai route đều thuộc sub view Work Orders: thanh sub-view của Management giữ **Work
Orders** active trên cả hai, và việc khôi phục sub-view dùng lần cuối (§1.1) luôn vào lại qua danh sách đang hoạt động,
không bao giờ vào sâu bên trong lịch sử.

**Dialog, không phải route.** Chọn một Work Order mở **Work Order Details dưới dạng một modal dialog phủ trên danh sách**:
danh sách vẫn được mount và nhìn thấy phía sau lớp phủ, và URL không bao giờ thay đổi. **New Work Order** cũng là một
**modal dialog phủ trên danh sách WO**. Không có panel hay view chi tiết riêng nào và không có route chi tiết cho từng
Work Order.

**Ngày sửa được dùng control lịch native** (`<input type="date">`) trong mọi form của Work Orders: ngày nhận và WO due
date của New Work Order, due date của Work Order OPEN, và mọi due date của demand line. Giá trị sửa được là ISO
`YYYY-MM-DD` ở bên trong; phần trình bày chỉ-đọc định dạng chúng thành `Jul 24, 2026`. Không thêm dependency date-picker
nào: control native có khả năng truy cập hoạt động ở cả hai theme mode (`color-scheme` theo theme) và vẫn dùng được bằng
bàn phím.

**Work Orders làm việc với server như thế nào.** View Work Orders là UI Management được lưu, dựa trên surface `/api`
(work orders, part numbers, production release, và danh sách route-template chỉ-đọc mà một release `PLANNED` chọn từ
đó).

- Danh sách WO đang hoạt động, New Work Order, Work Order Details, việc lưu và gỡ demand, và việc release vào production
  tường minh của §11.4 đọc và ghi trạng thái backend đang chạy, với cách trình bày loading, error và retry tường minh,
  các lần ghi bị server từ chối được hiển thị tại chỗ, và chặn ghi khi offline kiểm soát mọi write action.
- Trạng thái đã release của một demand line do server suy ra. Một line mà quantity đã được release sẽ đến ở trạng thái
  **Released với chế độ sửa bị hạn chế** ngay từ lần tải đầu tiên (§11.2; PROJECT_PROFILE §13: Qty, due date và Job
  Numbers vẫn sửa được, PN và Request Type cố định, và việc gỡ bị từ chối). Backend enforce cùng hạn chế đó, nên quy tắc
  không bao giờ chỉ nằm ở UI.
- Một Work Order chỉ hiển thị là **Released** khi mọi line đều đã được release đủ. Một demand có thể được release theo
  **từng phần**: một line đã release một phần hiển thị những gì thực sự đã được release (`Released 20/50`), giữ action
  **Release to production…** cho phần còn lại, và giữ Work Order ở trạng thái **Open** cho đến khi không còn gì để
  release.
- **Trang Completed Work Orders (§11.5)** là production UI trên `GET /api/work-orders/completed`. Việc hoàn tất được suy
  ra từ allocation (việc nhận hàng ở Stockroom của §10), và một Work Order đã hoàn tất rời danh sách đang hoạt động ngay
  khi lần allocation làm nó hoàn tất được ghi nhận.

**Kỷ luật về câu chữ.** Mọi câu chữ thông thường của Work Orders là lời lẽ ngắn gọn dành cho operator và manager (cần làm
gì, điều gì sẽ xảy ra, vì sao một action không khả dụng, cách khắc phục), không bao giờ là ghi chú triển khai, giải thích
kiến trúc hay đặc tả lặp lại. Một lời giải thích nằm ở một chỗ: cùng một quy tắc không bao giờ được nhắc lại qua phần giới
thiệu, header của card, gợi ý field, ghi chú footer, toast và xác nhận. Các tên Movement và event hợp lệ hướng tới
audit chỉ nằm trên các surface audit đã được phê duyệt (§3 rule 10).

**Bố cục responsive.**

- Các bảng demand line rộng sửa được chỉ là cách trình bày cho desktop. Ở độ rộng tablet và mobile, mỗi demand line hiển
  thị thành một khối kiểu card có nhãn (nhãn trên giá trị, với PN và các action của dòng trải hết card) thay vì ép một
  bảng desktop cuộn ngang.
- Hai dialog workflow lớn (New Work Order, Work Order Details) được trình bày gần toàn màn hình trên viewport hẹp, với
  heading và các action chính dễ với tới (action của dialog dính trên một nền đặc, không bao giờ che nội dung khi đã cuộn
  tới cuối).
- Vùng cuộn danh sách line bên trong bị bỏ ở các độ rộng này, nên cuộn lồng nhau chỉ tồn tại ở nơi nó rõ ràng có ích
  (danh sách line trên desktop với header dính).
- Mọi field, thông báo validation, PN dài, WO Number và Job Number đều vẫn dễ đọc, và các tiêu chuẩn touch-target hiện
  có vẫn được giữ.
- Một nút đóng ✕ ở góc trên bên phải có thể được thêm sau, nhưng nó phải đi qua logic dirty-close (xác nhận hủy bỏ)
  hiện có thay vì bỏ qua nó.
- Các dialog con tiếp tục xếp chồng, nên chỉ dialog trên cùng sở hữu Escape, hành vi backdrop và việc bẫy focus.

## 11.1 Danh sách WO

- **Các dòng.** Một dòng cho mỗi Work Order: WO Number, ngày nhận, **WO due date** (tô màu theo thang như mọi due date;
  `—` khi không có, §3 rule 9), số demand line kèm preview PN, và trạng thái (**Open** / **Released**). Một Work Order đã
  hoàn tất không bao giờ xuất hiện trong danh sách đang hoạt động (§11.5), nên `Complete` không phải là trạng thái của
  danh sách đang hoạt động.
- **Search.** Search là trên WO Number. Một WO Number đã tồn tại luôn được mở, không bao giờ bị tạo trùng. Khi không
  khớp, nó đề xuất "＋ New Work Order" **và trỏ tới lịch sử đã hoàn tất**:
  `No active Work Order matches "…" — search Completed Work Orders, check the number, or create it with ＋ New Work Order`,
  để không ai tạo lại một Work Order đã chạy tới khi hoàn tất. Search là một truy vấn **server** trên Work Order Number:
  giá trị nhập được gửi đi sau một khoảng dừng gõ ngắn, việc khớp được thực hiện trong database, và danh sách không bao
  giờ được tải về toàn bộ để lọc trong trình duyệt. Nó giới hạn trong số; PN và Job Number được search trên trang
  Completed (§11.5), không phải ở đây.
- **Danh sách đang hoạt động được server giới hạn.** Một Work Order chỉ rời danh sách đang hoạt động qua việc hoàn tất
  được suy ra từ allocation (§11.5: mọi line được allocate đủ, dù là do lần allocation cuối hay một thay đổi demand), nên
  danh sách tăng theo lượng nhận vào và chỉ giảm theo việc hoàn tất. Một lần đọc trả về tối đa một trang của server (100
  Work Order, mới nhất trước), và một trang đầy sẽ nói điều đó trong một dòng nhạt phía trên danh sách:
  `Showing the first 100 Work Orders — refine the search to narrow it.` Search vẫn tìm thấy một Work Order nằm ngoài
  trang đó, vì việc khớp được thực hiện trước giới hạn. Kiểm tra trùng của New Work Order phân giải đúng số trên toàn bộ
  lịch sử và không bao giờ bị giới hạn bỏ sót. Trong khi một lần search đang chạy, cùng dòng nhạt đó hiển thị
  `Searching Work Orders…`, và các dòng đã có trên màn hình giữ nguyên chứ không co lại thành trạng thái loading.
- **Danh sách toàn chiều rộng với một hàng toolbar.** Danh sách WO trải hết chiều rộng view. Ô search, action phụ nhạt
  **`Completed Work Orders ›`** (lối vào §11.5), action phụ **`Import from file…`** (lối vào §11.7; bị ẩn, không phải
  bị vô hiệu hóa, với người dùng không có Create and edit Work Orders cũng như Edit Work Order Demand) và nút **＋ New Work Order** dùng
  chung một hàng toolbar, căn phải theo danh sách, cùng bố cục toolbar như Machines (§12.1). Action chính vẫn là ＋ New
  Work Order; lối vào lịch sử được cố ý làm nhạt hơn.
- **＋ New Work Order mở modal dialog của §11.3 phủ trên danh sách.** Danh sách vẫn được mount và nhìn thấy phía sau lớp
  phủ, và URL vẫn là `/management/work-orders`.
- **Work Order nội bộ.** Các Work Order nội bộ không có số bên ngoài (ví dụ từ một lần nhận MODIFY ở Scan Station,
  PROJECT_PROFILE §14) xuất hiện như mọi Work Order khác. Số trống hiển thị là `—` (chỉ để hiển thị, không bao giờ được
  lưu), dòng được gắn nhãn rõ ràng "internal Work Order — no external number yet", và Work Order được tìm qua preview PN
  của nó. Không bao giờ có Work Order Number tạm nào được sinh ra.
- **Completed Work Orders** (mọi Work Order Demand được allocate đủ) rời danh sách đang hoạt động ngay khi lần allocation
  làm hoàn tất, hoặc lần lưu demand hay gỡ line khiến mọi line còn lại được allocate đủ, được ghi nhận. Chúng vẫn luôn
  khả dụng vĩnh viễn trên **trang Completed Work Orders** (§11.5; PROJECT_PROFILE §8.2). Page note của danh sách liên kết
  tới đó.
- **Demand line chỉ được hiển thị sau khi một Work Order được chọn.** Chọn một dòng mở dialog **Work Order Details**
  (§11.2). **Kích hoạt cả dòng:** toàn bộ dòng là vùng bấm; một nút duy nhất của dòng (tên truy cập
  `Open Work Order {no}`) là lối vào cho bàn phím và trình đọc màn hình. Enter và Space kích hoạt nó, và đóng dialog trả
  focus về dòng. Không có vòng focus nhìn thấy được trên nút của dòng (khác với Tracking, §7).

## 11.2 Work Order Details — modal dialog với các demand line

- **Dialog.** Chọn một Work Order mở một modal dialog có tiêu đề **`Work Order Details`** phủ trên danh sách, dựng trên
  primitive dialog có khả năng truy cập dùng chung (`role="dialog"`, `aria-modal`, bẫy focus, focus ban đầu, yêu cầu đóng
  bằng Escape và backdrop, kích thước responsive với cuộn bên trong). Khi đóng, focus trở về dòng Work Order đã mở nó.
- **Các action chính** là **`Save demand`** và **`Cancel (Esc)`**. Việc đóng qua Cancel, Escape, backdrop hay bất kỳ yêu
  cầu đóng nào khác không bao giờ âm thầm bỏ các chỉnh sửa: một bản nháp dirty sẽ hỏi xác nhận hủy bỏ tường minh
  trước, còn một dialog sạch thì đóng trực tiếp.
- **Các dialog con** (Add Part, xác nhận gỡ bỏ và release) xếp chồng đúng: chỉ dialog đang hoạt động trên cùng xử lý
  Escape, backdrop và bẫy focus, và yêu cầu đóng của một dialog con không bao giờ đóng Work Order Details.
- **Header của dialog.** WO Number, ngày nhận, WO due date (**sửa được bằng control lịch khi Work Order đang Open**), số
  line và trạng thái. Bên trong các dialog (dialog này và §11.3), heading hiển thị dưới dạng eyebrow chữ in hoa dùng
  chung phía trên định danh WO đậm, với dòng meta bên dưới, ngăn cách bằng một đường kẻ mảnh, và giới hạn độ rộng của
  card và ghi chú được bỏ. Đây chỉ là trình bày: các ô nhập ngày bằng lịch native, bố cục demand line kiểu card ở độ
  rộng hẹp, xác nhận hủy bỏ khi đóng lúc dirty, hạn chế sửa line đã release, focus vào control không hợp lệ đầu tiên
  sau một lần lưu thất bại, và các dialog anh em xếp chồng đúng đều giữ như đã đặc tả.
- **Các dòng WorkOrderDemand.** Một bảng sửa được với một dòng cho mỗi PN demand: PN (tra cứu hoặc tạo: mọi văn bản PN
  không rỗng sau khi trim và không chứa khoảng trắng bên trong đều được chấp nhận và được tạo ở lần dùng đầu tiên, chuẩn
  hóa thành PN chữ in hoa; khoảng trắng bên trong bị từ chối, không bao giờ bị âm thầm loại bỏ), Request Type (`NEW` mặc
  định / `MODIFY`), quantity yêu cầu, due date (control lịch), priority khi áp dụng, các Job Number bên ngoài, và
  requester / reason / notes. Danh sách line WO dài cuộn với header dính và số line.
- **Control PN trên một demand line.** Trên mọi line mang một PN chuẩn, **chính PN là control**.
  - Kích hoạt nó mở dialog dùng chung **`Edit Part Number`** (§14.2): sửa metadata, với nhãn barcode in được có thể mở
    từ bên trong qua `Barcode label…` (§14.3), dialog nhãn PN DUY NHẤT trong ứng dụng. Một PN chưa có chi tiết được lưu
    mở cùng dialog đó dưới dạng **`New Part Number`**, với PN cố định.
  - Một **biểu tượng bút chì (sửa)** nhỏ sau PN đánh dấu nó là kích hoạt được, và control thay thế văn bản PN thường tại
    chỗ. Với một người dùng không được quản lý Part Numbers, PN vẫn là control nhưng không có glyph sửa và mở cùng dialog đó ở
    chế độ chỉ-đọc (`Part Number details`, §14.2), nên nhãn barcode vẫn mở được.
  - Control **không tốn thêm chiều cao dòng**. Một draft line chưa lưu có PN chưa có bản ghi master mang marker nhạt
    `new PN` bên cạnh trên cùng dòng; marker theo trạng thái tồn tại mà dialog quan sát được gần nhất.
  - Barcode được suy ra hoàn toàn từ PN chuẩn, nên một line đã lưu và một draft line chưa lưu in ra như nhau. Nó không
    bao giờ được hiển thị như văn bản của dòng; nó luôn là `PF:PN:<part-number>` được suy ra.
  - Việc mở dialog chỉ là trình bày: nó không bao giờ đụng tới bản nháp, trạng thái dirty hay hành vi release (các lần
    ghi riêng của dialog chỉ liên quan tới chi tiết Part Number, không bao giờ tới demand), và nó vẫn khả dụng trên một
    line đã release.
  - Liên kết `Barcode label…` riêng của luồng Add Part vẫn là một liên kết nhãn.
- **Thêm demand line (thủ công trước).** Một Work Order **OPEN** hiển thị một action nổi bật **＋ Add Part manually**;
  các Work Order đã đóng hoặc đã hoàn tất vẫn chỉ-đọc. Add Part manually mở dialog Add Part nhiều bước của §11.3 (PN →
  quantity → due date → metadata tùy chọn). Quét barcode vẫn khả dụng như một **phương thức phụ, tùy chọn**: nó chỉ chấp
  nhận barcode PN hợp lệ và từ chối barcode không xác định. Một line mới vào Work Order dưới dạng một **bản nháp chưa
  lưu** được đánh dấu rõ: Request Type mặc định là `NEW`, và due date mặc định là WO due date khi có. Quét hoặc nhập một
  PN đã có trên Work Order sẽ focus vào line hiện có thay vì thêm một bản trùng; trên một line đã release, thông báo nêu
  tên các field vẫn còn sửa được ở đó.
- **Demand line đã release vẫn sửa được trong giới hạn** (PROJECT_PROFILE §13 *Restricted edit after release*).
  - Một line có production quantity đã release hiển thị **Qty**, **due date** và **Job Numbers** như các field sửa được
    bình thường, trên Work Order Open cũng như Released, nên một Work Order Released vẫn cung cấp **`Save demand`**.
    **PN** và **Request Type** của nó hiển thị như giá trị chỉ-đọc thường.
  - Field Qty không mang ghi chú thường trực nào. Nó chỉ lên tiếng khi giá trị nhập thực sự sai: một giá trị dưới quantity
    đã cam kết biến field thành lỗi đỏ thông thường theo từng field **ngay khi đang gõ** (`≥ 10 pcs released`), xóa lại
    ngay khi giá trị nhập được sửa, và không bao giờ được gửi tới server.
  - Tăng Qty trả lại quantity còn lại cho line, nên Work Order được suy ra trở lại **Open** và line lại cung cấp
    **Release to production…**.
  - Phạm vi chỉ-OPEN vẫn áp dụng: một Work Order Released không hiển thị ＋ Add Part manually, không có hàng quét, không có
    WO due date sửa được và không có action dòng nào ngoại trừ các action allocation của người dùng được điều chỉnh allocation
    (§11.6), và một line đã release không bao giờ có thể bị gỡ.
- **Allocation của một line đã lưu.** Mỗi line đã lưu hiển thị `Allocated a/b` (kèm `+n beyond demand` khi một correction
  được cấp quyền đã allocate vượt quá demand). Với người dùng được điều chỉnh allocation (Edit Work Order Allocation), nó còn
  hiển thị `Allocate from stock…` và `Reverse…` trên Work Order Open, Released và Completed như nhau. Cả hai mở dialog
  dùng chung của §11.6 và bị vô hiệu hóa trong khi bản nháp demand có thay đổi chưa lưu. Ngoài ra, việc sửa Qty bị hạn chế áp
  dụng như đã mô tả: một Qty không đổi không bao giờ là lỗi (một line được allocate vượt demand vẫn giữ các field khác
  sửa được), và một Qty đã đổi không bao giờ thấp hơn quantity đã release hoặc đã allocate.
- **Gỡ demand line** tuân theo quy tắc gỡ Work Order Demand chuẩn (PROJECT_PROFILE §13):
  - một draft line chưa lưu được gỡ ngay lập tức;
  - một line đã lưu không có production quantity đã release chỉ được gỡ sau một dialog xác nhận tường minh;
  - khi bất kỳ quantity nào đã được release vào production, action gỡ của line bị vô hiệu hóa kèm giải thích
    "Cannot remove: production quantity has already been released."; các điều chỉnh về sau đi qua các workflow
    correction và production, không bao giờ qua xóa;
  - một line đã lưu nằm trong Hot list hiển thị cảnh báo nêu Hot rank của nó (`🔥#n`) và chỉ được gỡ sau khi PN được gõ
    để xác nhận (dialog typed-confirmation dùng chung); việc gỡ cũng đưa nó ra khỏi Hot list, và các Hot rank còn lại dồn
    lại để lấp khoảng trống. Điều này áp dụng cho một line không có quantity đã release và không có allocation, hiện tại
    hay đã đảo ngược: một line có lịch sử allocation không bao giờ gỡ được, nên thay vào đó nó nhận xác nhận thường và
    lời từ chối của server;
  - việc gỡ không bao giờ xóa PartNumber master, Quantity Flow, Movement, lịch sử release, hay Work Order Demand khác cho
    cùng PN.
- **Due date mặc định.** Due date của mỗi line mặc định là **WO due date** (khi có) và có thể được sửa theo từng line. Due
  date trống là hợp lệ và hiển thị gọn gàng (§3 rule 9). Thay đổi WO due date chỉ cập nhật các line vẫn đang giữ giá trị
  mặc định được kế thừa trước đó; một line có due date đã được đổi thủ công giữ giá trị của nó, và một line được đặt
  tường minh là **"No due date"** được tính là đã được người dùng sửa và không bao giờ kế thừa một WO due date về sau. Với một
  người dùng được thay đổi header của Work Order nhưng không được thay đổi demand line của nó (Create and edit Work Orders mà
  không có Edit Work Order Demand), việc thay đổi WO due date chỉ thay đổi WO due date: mọi line giữ due date của mình, và
  dialog nói điều đó bên cạnh field (`Line due dates stay unchanged — you may not edit demand lines.`).
- **Tra cứu và tạo PN.** Field PN search các PartNumber hiện có. Một PN mới có thể được tạo inline, việc này hiển thị một
  **preview barcode** (`PF:PN:…`; barcode được suy ra từ chính PN chuẩn) và tạo bản ghi metadata master của PN.
  **"This PN does not exist yet" chỉ bao giờ được nói sau khi server đã trả lời cho đúng chính xác nội dung trong
  field:** trong khi giá trị nhập vẫn đang ổn định hoặc lần tra cứu của nó đang chạy, step nói `Searching Part Numbers…`
  và không đề xuất tạo mới, nên một PN đã tồn tại (đặc biệt là PN một ký tự, được phân giải qua tra cứu chính xác chứ
  không phải contains-search) không bao giờ thoáng bị trình bày như mới. Khi lần tra cứu cho đúng giá trị nhập hiện tại đã
  trả lời rằng PN không tồn tại, step đề xuất `＋ Create new PN "…"`. PN master không có trạng thái active hay inactive:
  một PN chuẩn luôn dùng được, có hay không có bản ghi master.
- **Trạng thái validation.** Lỗi theo từng field (thiếu PN, quantity không phải số nguyên dương, PN trùng). Một dòng có
  lỗi không thể được lưu và **không bao giờ bị âm thầm lọc bỏ**. **Thiếu due date không phải là lỗi và không bao giờ chặn
  việc lưu** (PROJECT_PROFILE §8.3); thay vào đó các ngày bị thiếu được tóm tắt trong xác nhận lưu (§11.3). Sau một
  lần lưu thất bại, control không hợp lệ đầu tiên nhận focus và mọi giá trị đã nhập được giữ lại.
- **Thay đổi chưa lưu** được đánh dấu rõ ("● Unsaved changes") và được bảo vệ. Rời khỏi Work Order (qua action quay lại,
  điều hướng cấp cao nhất, điều hướng phụ của Management, back/forward của trình duyệt, hoặc reload hay đóng tab,
  `beforeunload`) bắt buộc phải có xác nhận tường minh trước khi bản nháp bị hủy. Trạng thái có đúng nghĩa như tên
  gọi: nó là sự khác biệt thực tế giữa bản nháp và Work Order đã tải, không phải "đã từng gõ gì đó". Hoàn tác một chỉnh
  sửa (gõ lại quantity đã cam kết, gỡ lại một draft line, xóa giá trị nhập số bên ngoài) đưa dialog về trạng thái không
  có thay đổi chưa lưu, nên marker biến mất, việc đóng không hỏi gì, việc gỡ line đã lưu khả dụng trở lại, và
  **Release to production…** thôi yêu cầu xử lý bản nháp trước (§11.4), tất cả mà không cần lưu hay reload.

## 11.3 New Work Order — modal dialog ưu tiên nhập thủ công và luồng Add Part

- **Trình bày.** New Work Order mở dưới dạng một **modal dialog phủ trên danh sách WO**. Dialog có `role="dialog"`,
  `aria-modal="true"`, một tên truy cập lấy từ heading nhìn thấy của nó, focus ban đầu bên trong dialog, bẫy focus bàn
  phím, yêu cầu đóng bằng Escape và bấm backdrop, khôi phục focus về nút **＋ New Work Order** khi đóng, và kích thước
  responsive với cuộn bên trong cho danh sách line dài, và nó theo cả hai theme mode. URL không bao giờ thay đổi.
- **Không gì bị âm thầm hủy bỏ.** Escape, bấm backdrop, Cancel hay bất kỳ yêu cầu đóng nào khác trên một dialog có dữ
  liệu đã nhập trước tiên hỏi xác nhận tường minh ("Discard this New Work Order?").
- **Form header.** WO Number (**tùy chọn**), ngày nhận (control lịch, mặc định là hôm nay, luôn khả dụng) và **WO due
  date** (control lịch, **tùy chọn**), là due date mặc định cho các demand line khi được đặt.
  - Dialog giải thích rõ ràng các hệ quả: một WO Number trống lưu một Work Order nội bộ với số **null**, hiển thị là `—`
    và có thể nhận số bên ngoài thật về sau qua một lần sửa có audit (PROJECT_PROFILE §7; không có số tạm nào được sinh
    ra); due date có thể được thêm sau; và Work Order có thể được lưu mà không có due date.
  - Nhập một WO Number đã tồn tại sẽ mở Work Order hiện có thay vì tạo trùng. **Xử lý trùng chỉ áp dụng khi đã nhập một
    WO Number.** Nếu đã có line được nhập, việc mở Work Order hiện có được xác nhận tường minh trước.
  - Kiểm tra tính duy nhất trải trên **toàn bộ lịch sử, kể cả các Work Order đã hoàn tất**: nhập số của một Work Order đã
    hoàn tất sẽ thông báo việc hoàn tất và mở chi tiết chỉ-đọc của nó (§11.5). Một số đã hoàn tất không bao giờ được âm
    thầm dùng lại chỉ vì Work Order đã rời danh sách đang hoạt động.
- **Thêm Part thủ công là workflow chính.** Một action nổi bật **＋ Add Part manually** mở **một dialog nhiều bước có khả
  năng truy cập** (không có các modal lồng nhau xếp chồng):
  1. **PN**: search và chọn một PartNumber hiện có, hoặc tạo tường minh một PartNumber mới (kèm preview barcode, §11.2).
     Search khớp PN cùng Name và Description đã lưu, và mỗi kết quả hiển thị tên đã lưu (barcode được suy ra khi chưa lưu
     tên nào);
  2. **quantity**: một số nguyên dương, với cùng cách tương tác bàn phím số và bàn phím vật lý như dialog quantity của
     Scan Station (§4.8). `QuantityKeypad` dùng chung sở hữu stylesheet của nó (`components/QuantityKeypad.css`, được
     component import), nên step hiển thị đúng ở bất cứ đâu nó được mount, không bao giờ phụ thuộc vào CSS của Scan
     Station hay thứ tự điều hướng route;
  3. **due date**: mặc định là WO due date khi có; lựa chọn tường minh **"No due date"** là hợp lệ;
  4. **metadata tùy chọn**: Job Numbers, Notes, Request Type.

  Điều hướng lùi giữ lại các giá trị đã nhập. Hoàn tất dialog tạo một **draft row** sửa được; không gì được lưu cho đến
  Save demand.
- **Quét barcode vẫn khả dụng như một phương thức phụ, tùy chọn.** Một barcode PN mang chính PN
  (`PF:PN:<part-number>`): toàn bộ hậu tố không rỗng là PN, một PN nằm ngoài catalog được tạo ở lần dùng đầu tiên, và
  barcode không phải PN bị từ chối kèm lỗi, không thêm gì. Quét một PN đã có trên Work
  Order sẽ focus vào line hiện có (để sửa quantity của nó) thay vì thêm một bản trùng. Workflow nhập Work Order **không ưu tiên
  scanner**: đó là công việc tại bàn. Workflow xưởng sản xuất của Scan Station (§4) vẫn ưu tiên scanner.
- **Due date.** Thay đổi WO due date chỉ cập nhật các line vẫn đang kế thừa giá trị mặc định trước đó; các line có due
  date đã được sửa, kể cả "No due date" tường minh, giữ giá trị của chúng (§11.2). Một người dùng không có Edit Work Order Demand
  chỉ thay đổi WO due date, và mọi line giữ due date của mình (§11.2). Request Type và due date vẫn sửa được theo từng
  line trước khi lưu.
- **Save demand với xác nhận về phần bị bỏ trống.** Save validate header và mọi dòng (trạng thái validation của
  §11.2: lỗi theo field, focus vào control không hợp lệ đầu tiên, giữ giá trị, không bao giờ bỏ dòng chưa hoàn chỉnh,
  thiếu due date không bao giờ chặn). Khi WO Number bên ngoài, WO due date hoặc bất kỳ due date nào của line bị thiếu,
  một **dialog xác nhận tóm tắt các phần bị bỏ trống** trước khi lưu bất cứ thứ gì: Work Order được lưu thành một
  Work Order nội bộ không có số bên ngoài (hiển thị `—`), Work Order vẫn chưa được lên lịch, và N line không có due date
  được xếp ưu tiên due-date thấp nhất, sắp theo received date về sau (PROJECT_PROFILE §18). Việc lưu chỉ xảy ra sau xác nhận
  tường minh; Cancel quay lại chỉnh sửa với mọi giá trị được giữ. Khi lưu, dialog đóng, và Work Order mới xuất hiện trong
  danh sách ở trạng thái **Open**. Việc lưu ghi Work Order và các demand line của nó qua surface `/api`: chỉ business
  demand, không bao giờ là production quantity (§11.4).

> **Ghi chú data-model:** WO due date là thuộc tính nullable `due_date` trên WorkOrder (PROJECT_PROFILE §8.2). Nó chỉ là
> giá trị mặc định khi nhập; WorkOrderDemand giữ `due_date` nullable riêng của nó làm giá trị nghiệp vụ có hiệu lực.

## 11.4 Lưu demand so với release vào production

- **Save demand** chỉ lưu các dòng WorkOrder và WorkOrderDemand. Việc lưu không bao giờ tạo production quantity, và UI
  nêu điều này tường minh ("business demand — separate from production").
- Mỗi dòng demand đã lưu mang một action tường minh **Release to production…**, được cung cấp khi line vẫn còn quantity
  còn lại. Một demand có thể được release thành nhiều phần (20 trên 50, rồi 12, rồi 18), và action chỉ đóng lại khi phần
  còn lại về 0.
- **Một lần release luôn chạy trên demand ĐÃ LƯU, và các thay đổi chưa lưu được xử lý tường minh trước.** Các quantity
  release lấy từ Work Order đã cam kết, không bao giờ từ bản nháp trên màn hình, và một lần release đã cam kết sẽ tải lại
  chi tiết, nên release khi đang có chỉnh sửa dở dang vừa gây hiểu nhầm về quantity vừa hủy bản nháp.
  - Vì vậy action vẫn khả dụng trong khi bản nháp dirty (tooltip của nó nói
    `Releasing asks to save or discard demand changes first.`). Khi được dùng, nó mở quyết định ba lựa chọn dùng chung
    **`Unsaved changes`** (§12.4, cùng dialog mà Machines và Planned Routes dùng):
    **`Save demand, then release…`** / **`Discard changes, then release…`** / **`Cancel (Esc)`**.
  - Save chạy đường Save demand thông thường, gồm cả validation và xác nhận về phần bị bỏ trống của nó, và mở release
    trên đúng những gì đã được cam kết. Một lần lưu thất bại không mở release nào và giữ bản nháp.
  - Discard khôi phục bản nháp từ Work Order đã lưu và release cái đó.
  - Save không khả dụng khi bản nháp có lỗi validation, kèm lý do được nêu; Discard vẫn là cách để đi tiếp.
  - Không gì bao giờ được lưu hay release một cách ngầm định.
  - Việc **gỡ** line đã lưu giữ quy tắc chặt hơn của nó: nó vẫn bị vô hiệu hóa cho đến khi bản nháp được lưu hoặc hủy
    (§11.2).
- **Release mở một luồng xác nhận:**
  1. xác nhận quantity release, mặc định là quantity **còn lại** và không bao giờ nhiều hơn nó, với các số liệu requested,
     đã release và còn lại của demand được nêu (backend enforce cùng mức trần đó). Field quantity mang mức trần ngay tại
     chỗ gõ số: `available N pcs` nằm bên trong field, căn phải với tông ghi chú nhạt, theo sau là một phím tắt
     **`MAX`** điền quantity khả dụng (bị vô hiệu hóa khi không còn gì);
  2. xác nhận Route Mode: `FLOATING` (mặc định, không cần Route) hoặc `PLANNED` với một Route được chọn (có ghi chú về
     snapshot);
  3. xác nhận Area và Operation bắt đầu đã cấu hình. Một Area **terminal** không bao giờ được đề xuất và không bao giờ
     được chấp nhận (quantity thành phẩm kết thúc ở đó; production không đi vào đó). Khi Area được chọn có đúng **một**
     Operation active thì không có gì để chọn: Operation đó được chọn sẵn (được suy ra, nên thay đổi Area sẽ suy ra lại
     nó, và một lựa chọn tường minh luôn thắng). Một Area có nhiều Operation giữ lựa chọn mở, và một Area không có
     Operation nào nêu rằng nó không thể bắt đầu một lần release;
  4. **cảnh báo khi PN đã có quantity đang hoạt động**, hiển thị phân bố hiện có và bắt buộc phải xác nhận tường minh ý
     định, không bao giờ tạo hay gộp quantity một cách tự động;
  5. hiển thị một **tóm tắt release trước khi cam kết** (PN, quantity, Route, Area/Operation bắt đầu, Work Order Demand);
  6. khi Confirm, báo cáo kết quả: id của Quantity Flow được tạo, Route được gán, Area bắt đầu, quantity, và Movement
     `RECEIVED` được nối thêm.
- Việc lưu demand không bao giờ tự động kích hoạt một lần release; release luôn là một action tường minh riêng.

## 11.5 Trang Completed Work Orders

Trang là lịch sử chỉ-đọc của mọi Work Order đã hoàn tất (hoàn tất nghĩa là mọi Work Order Demand được allocate đủ,
PROJECT_PROFILE §8.2).

- **Route và lối vào.** Trang nằm trên route riêng của nó, `/management/work-orders/completed`, bên trong sub view Work
  Orders. Lối vào là action toolbar phụ nhạt **`Completed Work Orders ›`** trên danh sách WO đang hoạt động (§11.1), cộng
  với liên kết trong page note của danh sách. Trang mở đầu bằng một action quay lại **`‹ Work Orders`** trở về danh sách
  đang hoạt động.
- **Không giới hạn theo thiết kế.** Các Work Order đã hoàn tất được giữ **vĩnh viễn**, nên trang được thiết kế cho một
  danh sách không giới hạn ngay từ ngày đầu: search, lọc, sắp thứ tự và phân trang là một hợp đồng phía server trên toàn
  bộ lịch sử, và trang không bao giờ giả định rằng lịch sử vừa trong một response.
- **Ngày hoàn tất là giá trị trung tâm của trang.** Đó là `completed_at` của Work Order (PROJECT_PROFILE §8.2): timestamp
  của sự kiện khiến Work Order Demand mở cuối cùng được allocate đủ (lần allocation, hoặc một lần lưu demand hạ Qty của nó
  xuống bằng quantity đã allocate, hoặc việc gỡ line cuối cùng chưa được allocate đủ). Nó được hiển thị dưới dạng **ngày
  hoàn tất** theo lịch, ở múi giờ của site: một quy tắc phía server, `SITE_TIMEZONE`, dùng chung cho ngày hiển thị, khoảng
  Done và kết quả due, không bao giờ là ngày cục bộ của trình duyệt. Một lần correction allocation có audit về sau có thể
  khiến Work Order trở lại chưa hoàn tất (PROJECT_PROFILE §8.2): khi đó nó rời trang này và xuất hiện lại trong danh sách
  đang hoạt động. Không gì bao giờ bị xóa, và audit trail của allocation giữ toàn bộ câu chuyện.
- **Toolbar riêng, độc lập với danh sách đang hoạt động.**
  - Một ô search trên **WO Number, PN và Job Number** (tra cứu lịch sử thường bắt đầu từ một PN hoặc một Job Number,
    không chỉ từ WO Number); search có debounce.
  - Một select **Done range** với các preset `Last 30 days` / **`Last 90 days` (mặc định)** / `This year` /
    `Last year` / `All time` / `Custom…`. Các preset được gửi tới server theo tên và được neo ở đó vào ngày hiện tại của
    site trên lịch `SITE_TIMEZONE`, nên một trình duyệt gần nửa đêm hoặc vượt qua ranh giới năm ở một múi giờ khác không
    bao giờ làm lệch khoảng thời gian. Custom hiển thị hai ô nhập ngày native (quy ước ngày của §11) và gửi các ngày
    tường minh, tính cả hai đầu, theo lịch site đó.
  - Một select **Due outcome** `All` / `On time` / `Late` / `No due date`, cho các truy vấn nhanh về hiệu suất giao hàng.
  - Khoảng 90 ngày mặc định giữ cho truy vấn ban đầu và danh sách nhìn thấy trung thực trước nhiều năm lịch sử. Nó chỉ là
    trạng thái trình bày: không bao giờ được lưu và không bao giờ bị âm thầm mở rộng. Search và các bộ lọc kết hợp (AND).
- **Dòng tóm tắt kết quả.** Dưới toolbar, một dòng nhắc lại những gì thực sự đang được hiển thị: số lượng, đã tải và tổng,
  và khoảng có hiệu lực (ví dụ `Showing 50 of 1,248 completed Work Orders · last 90 days`), nên một view bị giới hạn
  không bao giờ bị nhầm là "tất cả".
- **Bảng.** Các cột là **WO Number | Done | Received | Due | Demand lines**. Không có cột Status (mọi dòng đều Complete)
  và không có thang màu khẩn cấp theo due date (việc trễ theo §3 rule 9 không bao giờ áp dụng cho công việc đã hoàn tất).
  Ô **Due** mang ngày cộng với một dòng kết quả được suy ra, nhạt: `✓ On time`, `✕ N days late` (ngày hoàn tất so với due
  date, phán định của server trên lịch site), hoặc `—` khi Work Order không có due date. Các marker ✓/✕ giữ cho tín hiệu
  không chỉ dựa vào màu sắc (thành ngữ trình bày của §12.3/§12.4). Work Order nội bộ hiển thị đúng như trên danh sách
  đang hoạt động (`—` cộng nhãn nội bộ), và demand line giữ cách trình bày số lượng cộng preview PN.
- **Sắp xếp.** Các cột ngày và định danh (WO Number, Done, Received, Due) mang các header sắp xếp được dùng chung (§12.1:
  `aria-sort` trên ô header, mũi tên ↑/↓, ↕ mờ khi không active, thứ tự ổn định). Sắp xếp ban đầu là **Done giảm dần**
  (hoàn tất gần nhất trước), và trạng thái không sắp xếp của chu kỳ quay về mặc định đó, vì một lịch sử không giới hạn
  không có "thứ tự registry" nào có nghĩa. Vì vậy trên chính header Done, nơi thứ tự giảm dần *là* mặc định, chu kỳ là
  tăng dần ↔ giảm dần, nên mỗi lần bấm đều thay đổi thứ tự và cả hai chiều của mọi cột sắp xếp được đều vẫn với tới được.
  Sắp xếp là một hợp đồng **server** như mọi thứ khác trên trang: cột và chiều được chọn đi cùng truy vấn, các dòng không
  có giá trị (số của một Work Order nội bộ, một due date bị thiếu) xếp cuối ở cả hai chiều, và id Work Order là
  tie-breaker ổn định. Trang không bao giờ sắp xếp lại các dòng mà nó đã tải.
- **Phân trang.** Trang tải 50 dòng khớp đầu tiên theo thứ tự hiện tại. Một action **`Show more`** nối thêm 50 dòng kế
  tiếp, dưới dạng tiếp nối keyset trên cursor mờ của server, gắn với cách sắp xếp mà nó được phát hành cho (không bao giờ
  phân trang theo offset trên một bảng không giới hạn). Server chỉ phát hành cursor khi thực sự còn một dòng tiếp theo,
  nên một lịch sử kết thúc đúng ở ranh giới trang không bao giờ đề xuất một `Show more` rỗng. Phần tiếp nối của một Done
  range preset giữ khoảng mà trang đầu tiên của nó đã phân giải: nửa đêm của site trôi qua giữa hai trang không bao giờ
  neo lại một truy vấn đã tải. Thay đổi search, bộ lọc hoặc sắp xếp sẽ reset phân trang, và dòng tóm tắt giữ cho số đã
  tải và tổng số luôn nhìn thấy được.
- **Kích hoạt dòng** theo mẫu cả-dòng dùng chung (§11.1: nút dòng `Open Work Order {no}`, Enter/Space). Kích hoạt một
  dòng mở cùng dialog **Work Order Details** (§11.2), chỉ-đọc đúng như với mọi Work Order không phải Open (demand của nó
  vẫn chỉ-đọc; người dùng được điều chỉnh allocation giữ các action allocation của §11.2, và một lần đảo ngược có thể mở lại
  Work Order, §11.6), với **`Done <date>`** được thêm vào dòng meta của dialog và quantity đã allocate được hiển thị theo
  từng demand line. Đóng sẽ trả focus về dòng, và URL không bao giờ thay đổi vì dialog.
- **Trạng thái rỗng phân biệt "chưa bao giờ có" với "không có trong khoảng này".** Khi hoàn toàn không có Work Order đã
  hoàn tất nào, một trạng thái rỗng đơn giản được hiển thị. Khi có bộ lọc đang hoạt động, trạng thái rỗng ghi
  `No completed Work Orders match in this range — widen the Done range or clear filters.` Khi một **văn bản search**
  không tìm thấy gì trong một khoảng bị giới hạn, trạng thái rỗng còn đề xuất một action một-cú-bấm
  **`Search all history`** (nó xóa Done range và giữ search): một Work Order không bao giờ được coi là bị thiếu chỉ vì nó
  hoàn tất ngoài khoảng mặc định.
- **Chỉ lịch sử.** Trang không cung cấp New Work Order, không sửa demand và không có action release, chỉ có các action
  allocation của người dùng được điều chỉnh allocation (§11.6). Trạng thái toolbar của nó hoàn toàn độc lập với search của
  danh sách đang hoạt động. Cách trình bày của nó (bảng, các trạng thái, hành vi responsive) khớp với danh sách WO
  (§11.1).
- **Server.** Trang là production UI trên `GET /api/work-orders/completed`. Search, Done range (một preset có tên mà
  server neo vào ngày hiện tại của site, hoặc các ngày hoàn tất tính cả hai đầu của khoảng Custom), bộ lọc due-outcome và
  cột cùng chiều sắp xếp là các tham số truy vấn phía server, và ngày hoàn tất cùng kết quả due của mọi dòng là phán định
  của server trên lịch site. Một Work Order chỉ hoàn tất khi server suy ra điều đó (mọi demand line được allocate đủ từ
  quantity đã stock); khi đó nó rời danh sách đang hoạt động và không bao giờ bị tạo trùng bởi một lần tra cứu số của nó
  từ New Work Order (lần tra cứu mở chi tiết đã hoàn tất).

## 11.6 Dialog điều chỉnh allocation

Dialog dùng chung **`Adjust WO Allocation`** là surface Management duy nhất để allocate quantity đã stock nhưng được để
lại cho sau, cho correction vượt-demand được cấp quyền, và để đảo ngược một allocation (PROJECT_PROFILE §8.12, §18). Nó
chỉ được cung cấp cho người dùng giữ Edit Work Order Allocation và bị ẩn với mọi người khác, không bao giờ được hiển thị ở
dạng bị vô hiệu hóa hay trơ.

- **Các lối vào.** Tracking → Corrections → `Adjust WO Allocation…` (§7.2 mục 8; các Work Order đang mở của PN được
  chọn), và Work Order Details (§11.2; một line đã lưu, trên Work Order Open, Released và Completed, kể cả trên trang
  Completed Work Orders, §11.5, nơi một lần đảo ngược có thể mở lại Work Order). Tracking chỉ liệt kê demand đang mở;
  một Work Order đã hoàn tất được điều chỉnh từ Work Order Details của nó.
- **Các step.**
  - `Overview`: các demand line trong phạm vi với số liệu đã allocate và requested, `+n pcs beyond demand` ở nơi áp dụng,
    và các allocation đang hoạt động, mỗi allocation được đánh dấu `beyond demand` khi nó là một correction.
  - `Allocate from stock`: một demand line, không bao giờ vượt quá demand còn lại của nó hoặc quantity đã stock khả dụng,
    với một ghi chú tùy chọn.
  - `Allocate beyond demand`.
  - `Reverse allocation`.
- **Correction vượt-demand.** Một step thứ hai tường minh với cảnh báo rằng quantity vượt quá demand, quantity, và một
  reason bắt buộc. Nó được ghi nhận như một correction riêng biệt kèm tên của người dùng đã đăng nhập và có thể đảo ngược như mọi
  allocation. Một allocation thường ngày không bao giờ vượt quá demand còn lại.
- **Đảo ngược.** Nó cần một reason bắt buộc. Nó nối thêm lịch sử mới và không bao giờ sửa hay xóa allocation. Nó có thể
  khiến một Work Order đã hoàn tất trở lại chưa hoàn tất: Work Order mở lại và ngày hoàn tất của nó bị xóa, và một lần
  hoàn tất về sau ghi một ngày hoàn tất mới. Một correction hoặc đảo ngược để Work Order vẫn hoàn tất không bao giờ dịch
  chuyển ngày hoàn tất của nó.
- **Người thực hiện.** Mỗi entry hiển thị người dùng đã ghi nhận nó, kèm avatar và tên.
- **Kết quả không xác định và đăng nhập.** Một ý định giữ một định danh request qua mọi lần gửi lại. Các ô nhập và điều
  hướng step bị khóa trong khi một lần gửi đang chạy, và vẫn bị khóa khi server không trả lời, nên lần submit kế tiếp chỉ
  có thể lặp lại cùng ý định đó. Một lời từ chối tường minh cho lần gửi lại đó chứng minh rằng không gì được ghi nhận và
  mở khóa các ô nhập. Một gián đoạn đăng nhập (session đã kết thúc) là một request đã được trả lời trong đó không gì được ghi
  nhận, nên nó giữ ý định và định danh request của nó cho lần gửi lại sau khi đăng nhập lại; nó không khóa cũng không mở
  khóa các ô nhập. Đóng trong khi kết quả chưa xác định sẽ tải lại host và nói điều đó.
- **Offline.** Trong khi mất kết nối, các action submit bị chặn, và không gì được đưa vào hàng đợi.

Dialog này được tham chiếu từ §7.2 mục 8, §7.3, §10, §11.2 và §11.5.

## 11.7 Import Work Orders từ một file

Dialog **`Import Work Orders`** tạo Work Order từ một file CSV hoặc Excel đã chuẩn bị, hoặc thay đổi các Work Order Open
và Released mà file liệt kê (PROJECT_PROFILE §13 *File import*). Nó chỉ lưu business demand; không gì được release vào
production.

**Ai thấy nó.** Dialog được hiển thị cho người giữ Create and edit Work Orders (`MANAGE_WORK_ORDERS`) hoặc Edit Work
Order Demand (`EDIT_WORK_ORDER_DEMAND`); mọi người khác không thấy **`Import from file…`** (bị ẩn, không phải bị vô hiệu hóa, §1.1
*Access to Management*). Việc import cần permission hoặc các permission mà nội dung của file đòi hỏi: Create and edit
Work Orders cho các Work Order nó tạo, và Edit Work Order Demand cho các Work Order hiện có mà nó thay đổi.

- **Vị trí và luồng.** **`Import from file…`** nằm trong hàng toolbar của §11.1 giữa `Completed Work Orders ›` và
  `＋ New Work Order`. Dialog (cực rộng) đi qua các step sau:
  1. chọn một file `.csv` hoặc `.xlsx`;
  2. **Check file**;
  3. đọc báo cáo theo từng Work Order;
  4. **Import** (được gắn nhãn như mô tả dưới *Thay đổi đối với Work Order hiện có*);
  5. đọc báo cáo kết quả.

  Check file là bắt buộc trước Import. Việc import validate lại cùng file đó, và server từ chối một file không phải là
  file đã được kiểm tra (khi đó dialog đề xuất `Check file again`). Chọn một file khác sẽ xóa báo cáo. Hai liên kết
  template (`CSV`, `Excel`) tải về một template chỉ có header.
- **Trợ giúp (ở một chỗ).** Các cột bắt buộc: Work Order Number, Part Number, Requested Quantity. Tùy chọn: Job Number
  và Due Date (`YYYY-MM-DD`). Một dòng cho mỗi Part Number. Định dạng các cột Work Order Number, Part Number và Job Number
  là Text để giữ các số 0 ở đầu. Các cột A–BL được đọc. Với file Excel, worksheet đầu tiên được đọc và phải hiển thị, công
  thức được đọc theo giá trị được lưu lần cuối trong Excel (một công thức chưa bao giờ được tính sẽ đọc là rỗng), và mọi
  dòng đều được import, kể cả dòng bị ẩn hoặc bị lọc. Giới hạn: 1 MB, 2,000 dòng, 500 line mỗi Work Order, 200 ký tự mỗi
  ô văn bản.
- **Báo cáo.**
  - Một dòng tóm tắt `Will create {a} · Will change {u} · Already in PartFlow {b} · Not imported {c}` (sau khi import:
    `Created {a} · Changed {u} · …`), `Worksheet read: {name}` cho file Excel, số dòng đã đọc, số dòng rỗng bị bỏ qua và
    các cột bị bỏ qua.
  - Một bảng `WO Number | Rows | Lines | Result` liệt kê các Work Order REFUSED trước. Mỗi dòng mở ra các line của nó
    (`Row · PN · Qty · Due · Job`; ngày dạng `Jul 24, 2026`, ngày hoặc Job Number bị thiếu là `—`) và, khi bị từ chối,
    mọi lỗi dưới dạng `Row {r} · {column} — {message}`.
  - Các nhãn kết quả:
    - `Will be created` (kèm số Part Number mới) và `Created`;
    - `Will change — {k} changes` / `Changed — {k} changes` (xem *Thay đổi đối với Work Order hiện có*);
    - `Already in PartFlow — nothing to change`: một Work Order Open hoặc Released mà file để nguyên như đã lưu;
    - `Already in PartFlow — not changed by this import`: một Work Order đã hoàn tất, kèm
      `Differs from this file — this Work Order is completed and is never changed.` khi nó khác. Câu
      "For completed Work Orders, due dates and Job Numbers are not compared." được hiển thị một lần dưới bảng, và chỉ
      khi có một dòng như vậy được hiển thị. Cùng nhãn đó đánh dấu một Work Order Number mà ai đó đã tạo giữa lần kiểm
      tra và Import, mà việc import không bao giờ so sánh, kèm
      `Differs from this file — check the file again to see the changes.` khi nó khác;
    - `Not imported — fix the rows listed`.
  - Trạng thái không bao giờ chỉ dựa vào màu sắc.
- **Thông báo về phần bị bỏ trống.** Mọi preview sẽ tạo Work Order đều nêu
  `Imported Work Orders get no Work Order due date — they stay unscheduled.` Mọi preview đều nêu
  `{n} lines have no due date and sort after dated demand.` khi các line mà việc import ghi không có due date (kể cả một
  preview chỉ cập nhật; một Due Date trống trên một line hiện có giữ ngày đã lưu và không được đếm). **Import**
  (kèm xác nhận bằng cách gõ khi file thay đổi Work Order) là xác nhận cho các phần bị bỏ trống này (như ở §11.3).
- **Chặn.** Các dòng không có Work Order Number dùng được được liệt kê dưới `Rows without a usable Work Order Number` và
  vô hiệu hóa Import. Khi không có gì để tạo hay thay đổi, dialog hiển thị `Nothing to create or change.`
- **Offline và đang chạy.**
  - Trong khi mất kết nối, Check file và Import bị vô hiệu hóa (`Reconnect to check or import the file.`), và không gì được
    đưa vào hàng đợi.
  - Trong khi import, dialog không thể bị đóng hay hủy và hiển thị `Importing… Keep this page open.`, và việc rời trang
    sẽ hỏi xác nhận.
  - Một kết quả bị mất hiển thị
    `The import may be partly saved. Check the file again: Work Orders already in PartFlow are never duplicated, and changes already saved are not listed again.`
    và không bao giờ được thử lại tự động.
  - Trong khi trang đã lỗi thời (§3 rule 13), Check file và Import bị vô hiệu hóa kèm
    `PartFlow was updated — reload the page to continue.` Một lần import đang chạy không bao giờ bị ngắt bởi reload (Work
    Orders chỉ reload thủ công, sau leave guard hiện có).
- **Focus và viewport hẹp.** Focus ban đầu nằm trên control chọn file; một báo cáo mới chuyển focus tới heading của nó;
  lỗi được thông báo dưới dạng alert; và việc đóng khôi phục focus về **`Import from file…`**. Trên viewport hẹp, bảng trở
  thành các khối card có nhãn với footer dính.

### Thay đổi đối với Work Order hiện có

- **File có thể thay đổi gì.** Với một Work Order Number đã có trong PartFlow, file thay đổi quantity, đặt due date, thêm
  Job Number, và thêm line vào các Work Order Open. Các ô trống và các line không có trong file giữ giá trị đã lưu của
  chúng, và một lần import không bao giờ gỡ line nào (phần Trợ giúp nêu điều này trong một câu).
- **Hiển thị thay đổi.** Một dòng `Will change` / `Changed` mở ra **`Show changes`**, với một dòng cho mỗi thay đổi:
  - một line được sửa: `Row {r} · {PN} · Qty {a} → {b} · Due {a|—} → {b} · Job Numbers {a|—} → {b}` (chỉ các phần đã
    thay đổi, cộng thêm ` · leaves the Hot list` khi một Hot line được allocate đủ bị hạ xuống);
  - một line được thêm: `Row {r} · Add {PN} · Qty {q} · Due … · Job … · new Part Number`;
  - sau đó `Completes the Work Order — every line becomes fully allocated.` khi các thay đổi làm nó hoàn tất;
  - và `Kept, not in this file: {PNs}` cho các line đã lưu mà file không liệt kê (cùng line được giữ đó hiển thị trên một
    dòng `Already in PartFlow — nothing to change`).
- **Thay đổi bị từ chối.** Một line được thêm vào một Work Order Released, hoặc một quantity dưới quantity đã release hoặc
  đã allocate, khiến Work Order đó thành `Not imported`, kèm lỗi của dòng.
- **Xác nhận bằng cách gõ.** **Import** với ít nhất một thay đổi mở một xác nhận bằng cách gõ xếp chồng (mẫu của §11.2):
  - tiêu đề `Change {m} existing Work Order(s)?`;
  - nội dung `These Work Orders are already in PartFlow. Import applies every change below; each Work Order is saved on its own.`
    (cộng `It also creates {a} new Work Order(s).`);
  - một vùng cuộn liệt kê **mọi** thay đổi theo từng Work Order dưới `WO {number} · {Open|Released}`;
  - giá trị phải gõ `CHANGE {m}` (gắn nhãn `Work Orders to change`) và nút
    `Import and change {m} Work Order(s)`;
  - Cancel hoặc Esc không gửi gì.
- **Xác nhận ràng buộc điều gì.** Xác nhận bằng cách gõ ràng buộc các thay đổi được hiển thị, không phải mọi lần sửa Work
  Order về sau. Khi các thay đổi được hiển thị không còn khớp lúc Import, mọi Work Order cần thay đổi đều thành
  `Not imported`, và một Work Order thay đổi sau khi Import đã kiểm tra lại nó cũng thành `Not imported`, mỗi cái kèm lý
  do. Một thay đổi của người khác để nguyên các thay đổi được hiển thị thì không chặn chúng. Khi một Work Order không được
  thay đổi, một dòng phía trên bảng nói
  `Some Work Orders were not changed because they changed after the check. Check the file again to see and confirm the current changes.`,
  và **Check file again** giữ file và bắt buộc một xác nhận bằng cách gõ mới. Các Work Order cần tạo vẫn được import.
- **Nhãn nút.** `Import {n} Work Order(s)` khi chỉ tạo, `Change {m} Work Order(s)…` khi chỉ thay đổi, và
  `Create {n} Work Order(s), change {m} Work Order(s)…` khi có cả hai.
- **Thiếu permission.** Khi User đã đăng nhập thiếu một permission mà file cần, Import bị vô hiệu hóa kèm một dòng nhạt cho mỗi
  permission key bị thiếu
  (`Creating Work Orders needs the "Create and edit Work Orders" permission.` /
  `Changing existing Work Orders needs the "Edit Work Order Demand" permission.`). Check file vẫn khả dụng cho người giữ
  một trong hai key.
- **Permission cần đến sau lần kiểm tra.** Khi Import bị từ chối vì file giờ cần một permission mà lần kiểm tra đã không
  đòi (ví dụ, một số được tạo kể từ lần kiểm tra giờ biến nó thành một thay đổi), không gì được ghi, báo cáo bị bỏ, alert
  ghi
  `Nothing was imported: this file now needs a permission your account does not have. Check the file again to see what it needs.`,
  và **Check file again** giữ file.

---

# 12. Machines (Management)

Machines là một sub view của Management tại `/management/machines` (nó theo theme mode toàn cục, §2.1). Đây là nơi duy
nhất để giám sát vận hành Machine và cấu hình Machine có thẩm quyền: vòng đời, bảo trì và nhận dạng tài sản
(PROJECT_PROFILE §7 Machine, §8.6, §20).

- **Quyền truy cập dựa trên permission.** Một Production Manager, Process Engineer, Maintenance Manager hoặc một chuyên
  viên được ủy quyền khác quản lý Machines mà không cần là Administrator, và Administration không giữ màn hình Machine
  trùng lặp nào (§9).
- **Phạm vi được giữ tập trung một cách có chủ ý.** PartFlow không phải là hệ thống quản lý bảo trì trên máy tính
  (computerized maintenance management system, CMMS): không có phụ tùng thay thế, không có lịch bảo trì, không có hợp
  đồng dịch vụ, không có kế toán chi phí.

## 12.1 Bảng Active Machines

Bảng có một hàng cho mỗi Machine đang hoạt động, với một ô tìm kiếm (Machine, Area, asset tag, model, nhà sản xuất) và
một action **+ New Machine**. Các cột là **Machine | State | Assigned now | Asset | Maintenance**; không có cột Actions
riêng.

**Header cột có thể sắp xếp.** Mọi header cột là một nút bật/tắt xoay vòng **tăng dần → giảm dần → không sắp xếp**
(không sắp xếp nghĩa là thứ tự trong registry). Header mang một mũi tên chỉ chiều (`↑`/`↓`; một `↕` mờ khi chưa sắp
xếp đánh dấu cột là có thể sắp xếp), và cách sắp xếp đang áp dụng được hiển thị nhấn mạnh bằng tông info (chữ đậm và màu
chữ info). Ô header sở hữu (`th`) mang `aria-sort`. Các giá trị sắp xếp là:

| Cột          | Sắp xếp theo |
|--------------|----------|
| Machine      | Tên hiển thị |
| State        | Trạng thái suy ra, theo thứ tự Running → Idle → Maintenance |
| Assigned now | Tổng số lượng đã gán |
| Asset        | Asset Tag |
| Maintenance  | Override bật đứng trước tắt |

Việc sắp xếp là ổn định: các giá trị bằng nhau giữ thứ tự theo tên, rồi đến thứ tự trong registry.

**Các cột.**

- **Machine.** Tên hiển thị, với Area bên dưới (chấm màu Area và tên Area).
- **State.** Trạng thái vận hành suy ra cùng thời gian đã trôi qua (`Running · 1h 24m`):
  1. một override **Maintenance** tường minh được ưu tiên;
  2. nếu không, có số lượng active đã gán nghĩa là **Running**;
  3. nếu không, trạng thái là **Idle**.

  Người dùng không bao giờ chọn Running hay Idle bằng tay. Thời gian được suy ra khi render từ timestamp dùng chung
  `stateChangedAt` cộng với đồng hồ UI dùng chung có độ chính xác theo phút (§3 rule 12; định dạng gọn `18m`, `1h 24m`,
  `2d 03h`, `<1m`; không bao giờ là một khoảng thời gian đã định dạng được lưu trữ), nên view này, Scan Station và Area
  Board luôn hiển thị cùng một thời gian, và thời gian tiếp tục chạy khi view vẫn mở (§4.10). Khi đang bảo trì, một dòng
  phụ nhẹ nhàng hiển thị ghi chú và `Expected back YYYY-MM-DD` khi có. Cột có độ rộng theo nội dung, bằng dòng
  `<state> · <age>` rộng nhất, dòng này không bao giờ xuống dòng; ghi chú bảo trì xuống dòng bên dưới nó.
- **Assigned now.** Các phần PN hiện đang được gán cho Machine cùng số lượng (`pcs`), mỗi Part Number một dòng
  (`<PN> · <n> pcs`), hoặc `—` khi không có gì được gán. Dấu phân cách và đơn vị `pcs` giữ mờ. Số lượng mang tông thông
  tin khi Machine đang Running và tông lỗi khi nó đang Maintenance: một tín hiệu bổ sung mà cột State lặp lại, không
  bao giờ là sự phân biệt duy nhất.
- **Asset.** Asset tag, nhà sản xuất và model dưới dạng metadata nhẹ nhàng. Cột có độ rộng theo nội dung: **cả** dòng
  `Asset <tag>` và dòng nhà sản xuất và model đều không bao giờ xuống dòng, nên dòng rộng hơn trong hai dòng xác định độ
  rộng tối thiểu của cột. Nhà sản xuất và model được render bằng hai tông trung tính khác nhau (muted và faint), nên
  chúng được đọc như hai giá trị riêng biệt mà không trở thành trạng thái.
- **Maintenance.** Một **công tắc On/Off** trên mỗi hàng (`role="switch"`, `aria-checked` phản ánh trạng thái bảo trì
  thực, accessible name `Maintenance — {name}`). Việc bật/tắt không bao giờ thay đổi trạng thái trực tiếp; nó chỉ **mở
  các dialog hiện có**: Off → On mở Start maintenance (§12.2), và On → Off mở xác nhận Clear maintenance. Công tắc chỉ
  phản ánh thực tế sau khi dialog xác nhận.

**Kích hoạt toàn hàng.** Nhấp vào bất kỳ đâu trên một hàng sẽ mở dialog Edit Machine (§12.3). Nút hàng của ô tên
(accessible name `Edit {name}`) là lối vào cho bàn phím và trình đọc màn hình, kích hoạt bằng Enter/Space; không có focus
ring hiển thị trên nút hàng (khác với Tracking, §7). Ô Maintenance là hòn đảo tương tác duy nhất của hàng: nó chặn lan
truyền sự kiện, nên thao tác công tắc không bao giờ mở dialog.

## 12.2 Bảo trì

Maintenance là một override tường minh và không gì hơn.

- **Bắt đầu bảo trì** được phép khi vẫn còn số lượng được gán. Lời xác nhận được viết ở thì tương lai
  (`will switch to Maintenance`) và nói rõ ràng rằng số lượng đã gán ở nguyên đúng chỗ của nó
  (`… pcs stay assigned to it — nothing is moved or released by this action.`). Bắt đầu bảo trì không bao giờ di chuyển,
  giải phóng, hoàn thành hay chuyển số lượng và không bao giờ viết lại lịch sử.
- Dialog Start maintenance thu thập một lý do hoặc ghi chú tùy chọn và một ngày dự kiến quay lại tùy chọn.
- **Khi một Machine đang bảo trì**, dialog Edit Machine (§12.3) có một phần **Maintenance** có thể chỉnh sửa: lý do
  hoặc ghi chú và ngày dự kiến quay lại, được điền sẵn và lưu bằng `Save changes` thông thường. Nó cập nhật ngữ cảnh tại
  chỗ mà không động đến thời điểm bắt đầu bảo trì hay chính trạng thái. Phần này nêu rằng Maintenance được tắt từ danh
  sách Machines.
- **Xóa bảo trì** đưa Machine về **Running** khi vẫn còn số lượng được gán, và nếu không thì về **Idle**. Lời xác nhận
  nêu tên trạng thái kết quả ở thì tương lai (`will return to Running` / `will return to Idle`).

## 12.3 Dialog New Machine / Edit Machine

- **Identity header đứng đầu.** Dialog mở đầu bằng một **identity header** chỉ đọc trên một panel nhẹ nhàng ngay dưới
  tiêu đề dialog: `Asset Tag` (được nhấn mạnh) và `Barcode` dưới dạng các giá trị monospace điềm tĩnh với nhãn chữ hoa
  nhỏ.
  - **Không có ô nhập barcode, không có ô nhập Asset Tag và không có câu giải thích nào dưới các giá trị**. Asset Tag
    được gán tự động khi Machine được tạo, là giá trị tiếp theo theo định dạng được cấu hình trong Administration →
    Barcode configuration (PROJECT_PROFILE §8.6), và barcode được suy ra từ nó (`PF:MACHINE:<asset-tag>`, §10).
  - Trên một Machine **hiện có**, header còn mang **Area cố định** dưới dạng một hàng toàn chiều rộng (một đường màu
    trong hàng nhãn, chấm màu Area và tên Area, và dòng trợ giúp nhẹ nhàng
    `The Area cannot be changed while the Machine is active. If the physical Machine moves to another Area, retire it first and update the Area when it is reactivated.`)
    và action **`Barcode label…`** mở dialog nhãn có thể in (bên dưới).
- **Dialog nhãn barcode (Machine hiện có).** `Barcode label…` mở `Machine barcode label`, với mô tả
  `Scan this label to identify {Machine}.`. Nó hiển thị tên hiển thị, Asset Tag và một bản render **Code 128** thực của
  giá trị được quét (`PF:MACHINE:<asset-tag>`; bộ mã hóa không phụ thuộc `src/views/code128.ts`, subset B với các bảo vệ
  unit test về cấu trúc) trên một **thẻ nhãn trắng với mực đen ở cả hai theme**, cùng văn bản giá trị. Trên nhãn, **tên
  hiển thị là thông tin chính**, lớn và đậm, trong khi Asset Tag lùi lại thành metadata phụ nhỏ (chính barcode mang nó để
  quét). `Print Label` in đúng chính xác nhãn (print style ẩn mọi thứ khác trên trang); `Cancel (Esc)` quay lại dialog
  Edit.
- **Tên hiển thị.** Nó là bắt buộc và không được trùng với một Machine đang hoạt động của cùng Area: tên hiển thị phải duy
  nhất giữa các Machine đang hoạt động của một Area, và một tên trùng là lỗi trường, không có gì được lưu.
  - Cột tên mang **phản hồi trực tiếp theo từng trường đúng chính xác như dialog Reactivate** (§12.4): một sự trùng tên
    hiển thị `✕ “<name>” already exists in <Area>.` dưới ô nhập; một tên bị thiếu (sau một lần thử lưu) cũng hiển thị lỗi
    của nó tại đó; và một tên được nhập khác với tên đã lưu hiển thị xác nhận mang tông success
    `✓ “<name>” is available in <Area>.`. Không có khối lỗi gom chung ở dưới cùng.
  - Form được bố trí trên một **lưới ba cột**. Trên một Machine **mới**, hàng đầu tiên là Display name · ô chọn Area ·
    Installed date. Trên một Machine hiện có, Display name trải hai cột và Installed date khép lại hàng (Area nằm trong
    identity header). Cột Installed date rộng đúng chính xác một cột metadata, và mọi control của form (ô nhập văn bản, ô
    chọn và ô nhập ngày native, mà trình chỉnh sửa bên trong của nó nếu không sẽ render cao hơn) dùng chung một chiều cao
    control tường minh.
- **Area (Machine mới).** Một ô chọn native, với màu của Area được chọn được xem trước dưới dạng một **đường ngang mảnh
  lấp đầy hàng nhãn** (cùng kiểu xem trước ở hàng nhãn như ô chọn Area của dialog Reactivate), không bao giờ là một
  control tùy biến chỉ vì một màu.
- **Metadata tài sản tùy chọn.** **Nhà sản xuất, model và số serial trên một hàng ba cột** (ngày lắp đặt nằm ở hàng form
  đầu tiên, phía trên), và **Notes dưới dạng một trường toàn chiều rộng** bên dưới. Metadata là tùy chọn: việc theo dõi
  sản xuất không bao giờ phụ thuộc vào nó. **Asset Tag** bắt buộc **nhận dạng tài sản vật lý** ngay cả khi tên hiển thị
  được dùng lại, và nó không bao giờ là một trong các trường có thể chỉnh sửa này. Mọi trường văn bản mang một
  placeholder ví dụ thực tế `e.g. …` (ô nhập ngày native thay vào đó hiển thị gợi ý định dạng riêng của trình duyệt).
- **Việc thêm được chia thành các bước (Machine mới).** Action chính của form là `Continue`: nó kiểm tra hợp lệ và dẫn
  tới một **bản tóm tắt `Confirm new Machine`** nêu rằng chưa có gì được thêm và tóm lại Machine, Area, Asset Tag,
  barcode và bất kỳ metadata tùy chọn nào đã được nhập. `Back` quay lại form với dữ liệu nhập còn nguyên.
  - Tất cả các bản tóm tắt xác nhận của Machines (thêm, retire, reactivate) dùng chung **kiểu tóm tắt xác nhận của Scan
    Station** (§4.6): một danh sách định nghĩa hai cột trên một panel nhẹ nhàng với cột nhãn có độ rộng theo nội dung.
    Tên Machine là hàng chính (lớn hơn, đậm hơn); các hàng ngữ cảnh (barcode, metadata, `Returns as`) lùi lại thành phụ;
    các tông ngữ nghĩa giữ vai trò bổ sung (success cho nhận dạng vật lý đã được xác nhận, warning cho một lần chuyển
    Area hoặc một quyết định về chỉnh sửa chưa lưu đã được ghi nhận, và giá trị `Returns as` `Idle` giữ tông chữ warning
    mà nó mang trên mọi bề mặt giám sát); và barcode được render dưới dạng văn bản kiểm chứng monospace nhẹ nhàng ở tông
    muted, không bao giờ là một badge.
  - Các giá trị barcode được render (`PF:…`) dùng chung MỘT tông đọc monospace muted này trên mọi bề mặt (các bản tóm tắt
    xác nhận, identity header của §12.3 và §12.4, danh sách hệ quả của việc retire, và bản xem trước cấu hình barcode
    trong Administration của §9) thông qua một style dùng chung: barcode là dữ liệu nhận dạng, không bao giờ là sự nhấn
    mạnh.
  - Action `Add Machine` của bản tóm tắt hỏi một câu hỏi tường minh cuối cùng (`Add this Machine?`: một bản ghi Machine
    không thể bị xóa sau khi đã thêm; nó chỉ có thể được retire về sau) trong **biến thể xác nhận attention** (§12.4
    Luồng retire; tông warning, vì nó là vĩnh viễn nhưng mang tính xây dựng). Chỉ lời xác nhận của câu hỏi đó mới thực
    sự thêm Machine.
- **Chỉnh sửa chưa lưu** được đánh dấu rõ ràng (`● Unsaved changes` **bên cạnh tiêu đề dialog**, dùng chung hàng tiêu
  đề), và action hủy là đúng chính xác `Cancel (Esc)` (§3 rule 10). **Đóng khi có dữ liệu nhập chưa lưu sẽ hỏi trước**,
  nên không có gì được lưu hay bị mất một cách âm thầm:
  - trên một Machine hiện có, `UnsavedChoiceDialog` dùng chung đưa ra `Save changes` (lưu và đóng; bị vô hiệu hóa kèm lý
    do được nêu khi form không hợp lệ) / `Discard changes` / `Cancel (Esc)`;
  - trên một Machine mới, một xác nhận `Discard new Machine?` đưa ra `Keep editing` / `Discard input` (chưa có gì được
    thêm, nên không có gì để lưu).
- **Focus ban đầu.** **Dialog New Machine mở với focus ban đầu ở trường Display name**, mục nhập bắt buộc duy nhất. Edit
  Machine không focus trường nào (focus ở gốc dialog, vì toàn bộ bản ghi đều có thể chỉnh sửa như nhau).
- **Dòng thời gian vòng đời (Machine History).** Dialog hiển thị các sự kiện vòng đời chỉ-ghi-thêm của Machine dưới
  tiêu đề `Machine History`.
  - Mỗi sự kiện hiển thị nhãn của nó (`Retired` hoặc `Reactivated`; các giá trị audit được ghi vẫn là `RETIRED` /
    `REACTIVATED`), ngày, người thực hiện (User đã ghi nhận nó, hiển thị bằng avatar và tên; các sự kiện được ghi trước
    khi có đăng nhập hiển thị những gì đã được ghi), lý do, và Area trước → Area hiện tại khi Machine đã chuyển. Khi
    không có sự kiện nào, dòng thời gian hiển thị `No Machine history recorded.`
  - Nó dùng MỘT kiểu trình bày dòng thời gian dọc dùng chung: các marker có vòng theo tông trên một đường ray mảnh, tông
    lỗi cho một lần retire và tông success cho một lần reactivate. Nhãn sự kiện luôn được render, nên màu sắc không bao
    giờ là sự phân biệt duy nhất. Dòng thời gian được dùng chung bởi Edit Machine, dialog Reactivate và dialog Retired
    Machine Details (§12.4).
  - Edit Machine dùng **biến thể gọn** của dòng thời gian: cùng đường ray và marker với mỗi sự kiện trên MỘT dòng (nhãn
    sự kiện, ngày, người thực hiện, lý do, việc chuyển Area nằm cùng dòng), nên phần audit vẫn hiện diện mà không chiếm
    chiều cao của form. Dialog Reactivate và dialog chi tiết giữ kiểu trình bày xếp chồng đầy đủ.
  - Dòng thời gian là chỉ đọc và không bao giờ có thể chỉnh sửa.
- **Danger Zone (chỉ Machine hiện có).** Dialog kết thúc bằng một Danger Zone được tách biệt trực quan chứa action
  `Retire…`; retire không phải là một action trên hàng danh sách (§12.4). Vùng này nêu sự thật hiện tại **tại chỗ**:
  - khi việc retire là có thể, dòng hệ quả
    (`Retiring <name> removes it from every assignment choice and stops its barcode from accepting assignment scans. History is preserved — the Machine is never deleted.`)
    được render bằng một tông đọc lỗi nhẹ;
  - khi vẫn còn số lượng được gán, thông điệp trở thành lời giải thích bị chặn đơn giản ở tông muted trung tính
    (`<name> cannot be retired while <qty> pcs are still assigned. Complete or transfer that quantity through the normal production workflow first.`),
    và **`Retire…` bị vô hiệu hóa**.

  Kiểu dáng lỗi của vùng này giống nhau ở cả hai trạng thái.
- **Hướng dẫn thay thế.** Dialog New Machine mang hướng dẫn thay thế: retire bản ghi Machine cũ và tạo một bản ghi mới.
  Machine mới nhận Asset Tag và barcode mới của riêng nó và có thể dùng lại tên hiển thị quen thuộc; lịch sử luôn giữ
  Machine đã thực sự làm công việc.

## 12.4 Retire, reactivate và thay thế

### Việc retire bị chặn khi còn số lượng active được gán

Danger Zone truyền đạt điều này **tại chỗ** (§12.3: thông điệp bị chặn và một `Retire…` bị vô hiệu hóa). Dialog
`Cannot retire Machine` (với `Close` là action duy nhất) vẫn tồn tại làm phương án dự phòng cho một view đã cũ hoặc một
race, và chốt chặn thực sự vẫn nằm trong quy trình sản xuất, không bao giờ chỉ trong tầng trình bày.

### Luồng retire

Việc retire bắt đầu từ Danger Zone của dialog Edit (§12.3) và dùng một xác nhận hai bước.

1. **Quyết định về chỉnh sửa chưa lưu.** Bắt đầu Retire khi có chỉnh sửa chưa lưu trước tiên đòi hỏi một quyết định
   tường minh ba lựa chọn thông qua `UnsavedChoiceDialog` dùng chung: `Save changes` / `Discard changes` / `Cancel (Esc)`
   (lựa chọn lưu bị vô hiệu hóa, kèm lý do được nêu, khi form không hợp lệ). Các chỉnh sửa không bao giờ được lưu một
   cách âm thầm. Lựa chọn này là một **quyết định được ghi nhận, không phải một action tức thời**: không có gì được lưu
   hay hủy bỏ cho đến khi việc retire hoàn tất, nên hủy bất kỳ bước nào sau đó sẽ quay lại dialog Edit với các chỉnh sửa
   vẫn còn trong form.
2. **Xác nhận bằng cách gõ.** `TypedConfirmDialog` dùng chung (`Retire Machine`) theo sau.
   - Nó **mang tông danger trên toàn bộ dialog** (viền lỗi, lớp phủ bề mặt lỗi mờ, tiêu đề eyebrow mang tông lỗi; bản
     tóm tắt `Confirm retirement` bên dưới dùng chung cách xử lý này), nên ngữ cảnh phá hủy không bao giờ được đọc như
     việc thường lệ.
   - Nó mở với **focus ban đầu ở ô nhập xác nhận bằng cách gõ**, vì chốt chặn là nhiệm vụ của dialog.
   - Nó đòi hỏi **gõ Asset Tag của Machine** (luôn có, không bao giờ là tên hiển thị có thể dùng lại), được so khớp sau
     khi trim và không phân biệt hoa thường. Action xác nhận của nó bị vô hiệu hóa cho đến khi giá trị được gõ khớp.
   - Trường gõ đứng sau một danh sách hệ quả tường minh: Machine biến mất khỏi các lựa chọn gán; barcode của nó
     (`PF:MACHINE:<asset-tag>`) không còn chấp nhận lượt quét gán; toàn bộ lịch sử được giữ nguyên và không có gì bị xóa;
     bản ghi chuyển sang Retired Machines, và chỉ chính máy vật lý đó mới có thể được reactivate về sau.
   - Action xác nhận của nó là `Continue`.
3. **Bản tóm tắt xác nhận retire.** Một **bản tóm tắt `Confirm retirement`** cuối cùng nêu rằng chưa có gì thay đổi và
   tóm lại Machine (như nó sẽ được retire, kèm các chỉnh sửa khi quyết định được ghi nhận là Save), Area, Asset Tag,
   barcode suy ra và quyết định về chỉnh sửa đã ghi nhận, với `Cancel (Esc)` / `Retire Machine`.
4. **Câu hỏi cuối cùng.** Action `Retire Machine` của bản tóm tắt hỏi một câu hỏi tường minh cuối cùng
   (`Retire this Machine?`: Machine sẽ bị retire; lần retire này được ghi nhận vĩnh viễn trong lịch sử Machine, và nếu
   cùng Machine đó được reactivate về sau thì lần retire vẫn được ghi nhận) trong **biến thể xác nhận attention**. Chỉ lời
   xác nhận của câu hỏi đó mới retire Machine: nó áp dụng quyết định về chỉnh sửa đã ghi nhận, thêm một sự kiện audit
   vòng đời `RETIRED` (§12.3) và đặt ngày retire.

Một Machine đã từng được lịch sử tham chiếu **không bao giờ bị xóa cứng**. Cách diễn đạt cho người vận hành là
`Retired`, `Retire Machine` và `Retired on …`.

**Biến thể xác nhận attention.** Nó là một họ dùng chung: một badge biểu tượng ở giữa với quầng theo tông, một tiêu đề
kiểu câu được nhấn mạnh, một bề mặt phủ theo tông, và chính phần mô tả nằm trong một lớp phủ nhẹ cùng tông, với tên
Machine và câu tuyên bố về tính vĩnh viễn ở tông chữ đầy đủ.

- Biến thể danger là đỏ nhạt, và biến thể warning (dùng cho các câu hỏi cuối cùng mang tính cảnh báo) là hổ phách nhạt.
- Biến thể information (`tone-info`) là xanh dương và được dùng cho các chốt cuối mang tính thông tin (§4.6). Nó thuộc
  cùng họ attention toàn cục với danger và warning (được định nghĩa trong `styles/global.css`, có sẵn cho mọi view, không
  bao giờ là một style cục bộ của Scan Station), và badge biểu tượng của nó hiển thị `i`.
- Nội dung của mọi câu hỏi cuối cùng mở đầu bằng điều sẽ xảy ra và nêu tường minh rằng thay đổi được ghi nhận vĩnh viễn
  trong lịch sử Machine, nên biến thể này ngắt việc xác nhận theo phản xạ thay vì được đọc như một thông báo thường lệ.

### Bảng Retired Machines

- **Các cột:** **Machine | Retired | Asset | Notes**: tên và Area, `Retired on YYYY-MM-DD`, metadata tài sản, ghi chú.
  Nó dùng để hiển thị và báo cáo lịch sử, **không có cột action**.
- **Sắp xếp.** Cả bốn cột mang cùng kiểu header có thể sắp xếp như bảng active (§12.1: Machine = tên, Retired = ngày
  retire, Asset = Asset Tag, Notes = văn bản ghi chú), sắp xếp độc lập với trạng thái sắp xếp của bảng active.
- **Kích hoạt toàn hàng mở dialog Retired Machine Details chỉ đọc**, theo cùng mẫu như các hàng active: nút ở ô tên
  (accessible name `Machine details — {name}`) là lối vào cho bàn phím và trình đọc màn hình. Dialog chi tiết mang lối
  vào `Reactivate` duy nhất.
- **Ghi chú trang.** Văn bản giải thích bên dưới bảng là
  `Retired Machines remain in history and cannot receive new work or assignments. Reactivate only the same physical Machine. Replacements may reuse the display name but require a new Machine record and Asset Tag.`
  Nó được render thông qua **kiểu trình bày PageNote dùng chung** (bề mặt info-weak, viền info, tông chữ info, một tag
  nhỏ `NOTE` mở đầu một dòng nội dung inline, toàn chiều rộng phần tử cha): MỘT ghi chú cấp trang được dùng bởi mọi view,
  bao gồm Machines, Work Orders (ghi chú của danh sách và ghi chú về việc gỡ dòng demand trong dialog chi tiết),
  Priority, và phần giải thích Archived Routes của Planned Routes (§13.1).
- Retired Machines không bao giờ xuất hiện trong các lựa chọn gán và không chấp nhận lượt quét mới.

### Dialog Retired Machine Details

Một kiểu trình bày chỉ đọc của một bản ghi đã retire:

- một hàng nhận dạng (tên hiển thị, chấm Area và tên Area, và badge `Retired on …` được nâng lên **tông chữ lỗi trên bề
  mặt error-weak**, khớp với marker retire trên dòng thời gian, để trạng thái then chốt của bản ghi không thể bị bỏ sót);
- panel nhận dạng dùng chung (Asset Tag và barcode `PF:MACHINE:` suy ra, §12.3);
- các hàng tóm tắt cho metadata tài sản (nhà sản xuất, model, số serial, ngày lắp đặt, ngày retire; giá trị vắng mặt hiển
  thị là `—`);
- các ghi chú;
- **dòng thời gian vòng đời** dùng chung (§12.3).

Không có gì có thể chỉnh sửa. Các action là `Close (Esc)` và `Reactivate`, mở quy trình Reactivate theo từng bước (bên
dưới). Khi vào theo cách này, action rời quy trình của dialog Reactivate là **`‹ Back`** (bao gồm cả Escape), quay lại
dialog chi tiết thay vì đóng tất cả.

### Dialog Reactivate Machine

Đây là việc đưa trở lại hoạt động của PROJECT_PROFILE §8.6.

- **Identity header.** Dialog mở đầu bằng **identity header chỉ đọc** (§12.3: Asset Tag và barcode suy ra, không bị
  việc reactivate động đến), được mở rộng bằng một cột nhận dạng thứ ba **`Machine`** trên cùng hàng (nhà sản xuất và
  model ở hai tông trung tính của chúng, `S/N <serial>`; `—` khi không có).
- **Các chốt chặn cứng.** Nếu Asset Tag (cũng chính là barcode) hoặc số serial trong thời gian đó đã được cấp lại cho một
  Machine đang hoạt động khác, một cảnh báo tường minh được render và Confirm bị vô hiệu hóa.
- **Tên hiển thị và Return Area.** Tên hiển thị có thể chỉnh sửa trong dialog và **dùng chung một hàng form với ô chọn
  Area** (`Return Area`: các Area đang hoạt động không phải điểm cuối, với bản xem trước đường màu ở hàng nhãn dùng chung
  của §12.3). Mỗi cột mang phản hồi riêng của nó ngay dưới ô nhập.
  - Dưới Display name: một sự trùng tên với một Machine đang hoạt động của Area đích hiển thị lỗi inline
    `✕ “<name>” already exists in <Area>.`, giới hạn trong cột tên, và việc đổi tên sẽ giải quyết nó (tên hiển thị phải
    duy nhất giữa các Machine **đang hoạt động** của một Area; việc dùng lại qua thời gian vẫn được phép). Một tên hợp lệ
    không rỗng hiển thị xác nhận khả dụng mang tông success (`✓ “<name>” is available in <Area>.`); các marker ✓/✕ giúp
    tín hiệu không chỉ dựa vào màu sắc.
  - Dưới Return Area là ghi chú nhẹ nhàng
    `Change only if the physical Machine moved while retired. Previous production history keeps the original Area.`
- **Kiểm tra hợp lệ tại trường.** **Mọi lỗi kiểm tra hợp lệ được render tại trường bị lỗi của nó** (tên bị thiếu ở cột
  tên, lý do bị thiếu dưới trường Reason, ô xác nhận chưa được đánh dấu bên trong panel Important), không bao giờ là một
  khối gom chung ở dưới cùng và không bao giờ bị lặp đôi. Mỗi lỗi biến mất ngay khi trường của nó được sửa.
- **Focus ban đầu** ở Display name.
- **Lý do và xác nhận.** Một lý do là bắt buộc, cũng như checkbox xác nhận tường minh
  `This is the same physical machine returning to service — not a replacement.` (checkbox và văn bản được tách biệt rõ
  ràng và căn giữa theo chiều dọc với nhau). Checkbox nằm trên một **panel warning `Important (required)`** điềm tĩnh
  (bề mặt warning-weak và viền warning, với marker `⚠ Important` ở tông chữ warning): một tín hiệu hãy-chắc-chắn-trước-khi-
  tiếp-tục, có chủ ý KHÔNG mang tông lỗi hay danger, và chính câu văn giữ tông chữ bình thường. Chỉ việc tiếp tục mà
  không đánh dấu mới gây ra lỗi kiểm tra hợp lệ màu đỏ thông thường.
- **Bản tóm tắt xác nhận reactivate.** Action chính của form là `Continue`: nó kiểm tra hợp lệ và dẫn tới một **bản tóm
  tắt `Confirm reactivation`** cuối cùng nêu rằng chưa có gì thay đổi và tóm lại tên Machine, Area (cũ → mới khi đã
  chuyển), Asset Tag, barcode suy ra, lời xác nhận chỉ đọc `Physical identity — Same physical machine confirmed`, lý do
  và việc quay lại ở trạng thái `Idle`. Ghi chú của nó là
  `The same Machine returns to Active with its existing Asset Tag and barcode. Previous production history is preserved.`,
  theo sau là `The Area change applies from reactivation onward.` khi Area thay đổi. Các action là `Back` (quay lại form
  với trạng thái còn nguyên) / `Reactivate Machine`.
- **Câu hỏi cuối cùng.** Action `Reactivate Machine` của bản tóm tắt hỏi một câu hỏi tường minh cuối cùng
  (`Reactivate this Machine?`: Machine sẽ quay lại hoạt động; lần reactivate này được ghi nhận vĩnh viễn trong lịch sử
  Machine, và lần retire trước đó vẫn là một phần của lịch sử đó) trong **biến thể xác nhận attention** (ở đây là tông
  warning, vì nó là vĩnh viễn nhưng mang tính xây dựng). Chỉ lời xác nhận của câu hỏi đó mới reactivate: nó xóa ngày
  retire, xóa mọi override bảo trì, đặt lại timestamp thay đổi trạng thái, và thêm một sự kiện audit vòng đời
  `REACTIVATED`. Machine quay lại ở trạng thái **Idle** (Running vẫn được suy ra từ số lượng đã gán, §12.1), và audit ghi
  lại ai, khi nào, lý do, và Area trước và Area hiện tại khi đã chuyển.

### Thay thế một Machine vật lý

Retire bản ghi cũ và tạo một bản ghi **mới** với danh tính ổn định riêng và Asset Tag (và barcode) mới, được gán tự động
của riêng nó. Machine mới có thể dùng lại tên hiển thị hướng tới người vận hành (vị trí quen thuộc trên xưởng, ví dụ
`Lathe 1`). Bản ghi cũ không bao giờ bị đổi tên hay biến đổi, và bản ghi cũ và mới vẫn phân biệt được thông qua Asset
Tag và danh tính nội bộ. Không có abstraction MachineSlot hay WorkCenter nào được đưa vào.

## 12.5 Các trạng thái và ranh giới triển khai

Các trạng thái loading, lỗi và rỗng theo kiểu trình bày view-state tiêu chuẩn (`No Machines configured yet.` / một thông
điệp không khớp), với một chu trình loading, lỗi và Retry thực sự đối với API.

View là UI Management được lưu trữ bền vững, dựa trên `/api/machines`.

- Liệt kê, tạo mới với Asset Tag do server gán, giao dịch chỉnh sửa Save-changes duy nhất, override bảo trì, cùng retire
  và reactivate (mỗi thao tác commit nguyên tử cùng sự kiện vòng đời chỉ-ghi-thêm của nó) đều đọc và ghi registry
  Machine thực.
- Khi tạo mới, tag được xem trước đi kèm như một điều kiện tiên quyết lạc quan: một bản xem trước đã cũ bị từ chối mà
  không tiêu thụ gì.
- Server báo cáo **số lượng ACTIVE đã gán** của mỗi Machine, suy ra từ projection sản xuất. Trạng thái suy ra **Running**
  từ đó (§12.1); cột Assigned now liệt kê các phần theo PN của server từ cùng projection mà tổng được cộng, nên phần
  chi tiết và tổng không bao giờ mâu thuẫn; và chốt chặn retire do số lượng đã gán được server thực thi và được phản ánh
  trong Edit Machine.
- Việc suy ra trạng thái là logic trình bày dùng chung, độc lập với framework (`src/views/machine-state.ts`), được dùng
  lại bởi các thẻ giám sát của Scan Station và Area Board.

---

# 13. Planned Routes (Management)

Planned Routes là một sub view của Management tại `/management/planned-routes` (nó theo theme mode toàn cục, §2.1). Nó
chứa các định nghĩa route có thể dùng lại (tên domain nội bộ `RouteTemplate`, PROJECT_PROFILE §8.8) cho các vai trò sản
xuất được ủy quyền, với quyền truy cập dựa trên permission như Machines (§12; PROJECT_PROFILE §20). Tên hướng tới người
dùng **Planned Routes** giữ các định nghĩa tách biệt rõ ràng với **các vết route thực tế Floating** hiển thị trong Tracking
(§7.2). Planned Routes định hướng sản xuất; chúng không bao giờ thay thế hay viết lại lịch sử Movement thực tế bất biến.

## 13.1 Danh sách route template

Một danh sách có thể tìm kiếm (tên route, Operation) với một action **+ New Planned Route**, **được chia thành hai bảng
như Machines**:

- một bảng **Active** với các cột **Planned Route | Steps | Status | Used by** (không có cột Actions);
- một phần **Archived Routes** riêng với các cột **Planned Route | Steps | Archived | Used by | Duplicate**.

**Các cột.**

- **Planned Route.** Tên, với mô tả tùy chọn bên dưới.
- **Steps.** Chuỗi bước có thứ tự dưới dạng **các chip mang màu Area**. Mỗi chip mang một bề mặt được pha màu
  (`color-mix` của màu Area vào panel), một viền màu Area, chấm màu Area, và tên Area ở màu chữ bình thường; các mũi tên
  giữa các chip là các phần tử anh em riêng biệt. Khi không hỗ trợ `color-mix`, chip quay về bề mặt chip trung tính;
  chấm và viền vẫn mang nhận dạng Area, nên color-mix không bao giờ là yếu tố gánh vác.
- **Status.** `Active` cùng ngày cập nhật (bảng active). Bảng archived hiển thị ngày archive, được render nhẹ nhàng về
  mặt thị giác.
- **Used by.** `N Quantity Flows…`, mở dialog sử dụng, hoặc `Never used`.

**Kích hoạt toàn hàng.** Nhấp vào bất kỳ đâu trên một hàng của bảng active sẽ mở dialog Edit (§13.2). Nút hàng của ô tên
(accessible name `Edit {name}`) là lối vào cho bàn phím và trình đọc màn hình, không có focus ring hiển thị trên nút hàng
(khác với Tracking, §7). Ô sử dụng chặn lan truyền sự kiện, nên việc mở dialog sử dụng không bao giờ mở trình chỉnh sửa.
`Duplicate`, `Archive…` và `Delete…` nằm **bên trong dialog Edit** (§13.2, §13.3), không nằm trên các hàng danh sách.
**Các hàng archived không thể nhấp và chỉ có nút `Duplicate` trên từng hàng.** Đối với một người dùng không được phép
quản lý Planned Routes, các hàng active không thể kích hoạt, `+ New Planned Route` và `Duplicate` của các hàng archived
vắng mặt, còn `Used by` vẫn giữ.

**Ghi chú Archived Routes.** Bên dưới bảng Archived Routes, một ghi chú thông tin giải thích các route đã archive. Nó
dùng một component ghi chú trang dùng chung (kiểu trình bày PageNote, §12.4).

**Dialog sử dụng.** Nó liệt kê các Quantity Flow được release với template (flow id, PN, ngày release) và nêu rằng mỗi
Quantity Flow giữ snapshot route riêng của nó từ thời điểm release.

## 13.2 Dialog Create / Edit

- **Các trường.** Tên route (bắt buộc), một mô tả tùy chọn, và **các bước có thứ tự** dưới một hàng header cột có nhãn
  (Area / Operation / Est. time / Preferred Machine). Mỗi hàng bước được trình bày như một mục Priority: tay nắm kéo,
  chỉ số bước, các trường của bước, các control ↑/↓/✕, và một dòng Instructions có nhãn bên dưới.
- **Sắp xếp lại.** Các bước được sắp xếp lại bằng kéo-thả, như danh sách Priority, cùng với các control lên và xuống
  tường minh: **Move Up / Move Down vẫn là đường đi cho bàn phím và cảm ứng, nên việc sắp xếp lại không bao giờ chỉ dựa
  vào kéo**. Nút xóa vẫn có trên từng bước, và một action **+ Add step** thêm một bước vào cuối. Bắt buộc có ít nhất một
  bước.
- **Operation là một ô chọn giới hạn trong các Operation của Area đã chọn.** Một Operation đã lưu mà Area không còn cung
  cấp được render thành một giá trị tường minh `{operation} (unavailable)`: được giữ hiển thị và **không bao giờ bị xóa
  âm thầm**.
- **Preferred Machine là một ô chọn trên các Machine ĐANG HOẠT ĐỘNG của Area đã chọn, được tham chiếu bằng Machine id
  ổn định** (khớp với `preferred_machine_id`; tên hiển thị có thể dùng lại và không thể nhận dạng một lựa chọn ưu tiên),
  với `— no preferred Machine` là mặc định. Một Machine được tham chiếu đã retire hoặc không còn tồn tại được render thành
  một giá trị tường minh `(unavailable)` (`{name} — retired (unavailable)` / id không xác định): được giữ hiển thị và
  không bao giờ bị xóa âm thầm.
- **Thay đổi Area của một bước sẽ đặt lại và kiểm tra lại Operation và Preferred Machine của nó.** Operation chỉ được
  giữ khi Area mới vẫn cung cấp nó, và Preferred Machine chỉ được giữ khi nó thuộc Area mới.
- **Kích thước.** Các combobox của bước có kích thước theo nội dung (`max-content`, có giới hạn trên) và trở thành toàn
  chiều rộng ở breakpoint điện thoại. Các trường thời lượng và hướng dẫn, vốn chỉ có viền dưới, giữ nhãn của chúng và
  một trạng thái focus hiển thị được.
- **Chỉnh sửa chưa lưu** được đánh dấu rõ ràng (`● Unsaved changes`), action hủy là đúng chính xác `Cancel (Esc)`, và
  việc đóng một dialog có thay đổi sẽ hỏi `Discard unsaved route changes?` trước khi hủy bỏ.
- **Các hàng bước dùng chung.** Các hàng bước, việc kiểm tra hợp lệ và việc đánh số của chúng là một component dùng
  chung với `Edit assigned Route…` của Tracking (§7.2 mục 8), vốn cũng dùng marker `● Unsaved changes` và chốt bảo vệ
  `Discard unsaved route changes?` của phần này.
- **Chỉnh sửa một template đã được dùng sẽ hiển thị một ghi chú tường minh:** các thay đổi chỉ áp dụng cho **các lần gán
  trong tương lai**. Các Quantity Flow đã được release với route này giữ nguyên các snapshot Assigned Route của chúng,
  và một Assigned Route đang trong sản xuất được thay đổi riêng trong quy trình được audit của chính nó kèm một lý do
  (Tracking → `Edit assigned Route…`, §7.2 Corrections), không bao giờ ở đây.
- **Các action trong dialog.** `Duplicate`, cùng với `Archive…` (đã từng được dùng) hoặc một `Delete…` mang tông phá
  hủy (chỉ khi chưa từng được dùng), nằm trong dialog. Bắt đầu Duplicate khi có chỉnh sửa chưa lưu đòi hỏi một lựa chọn
  tường minh (`Save, then duplicate` / `Duplicate the saved route` / `Cancel (Esc)`, thông qua `UnsavedChoiceDialog`
  dùng chung). `Duplicate` sao chép template (kể cả một template đã archive, từ bảng của nó) thành một bản nháp mới, chưa
  từng được dùng, đang hoạt động `{name} (variant)` và mở nó để chỉnh sửa.

## 13.3 Archive so với delete

- **Delete chỉ tồn tại cho các template chưa từng được tham chiếu.** Lời xác nhận nêu rằng route chưa từng được dùng bởi
  một Quantity Flow đã release, nên nó có thể được xóa hoàn toàn. Nó vẫn là một dialog xác nhận phá hủy đơn giản.
- **Một template đã từng được dùng sẽ được archive thay vì xóa**, được bảo vệ bởi một xác nhận bằng cách gõ.
  1. Bắt đầu Archive khi có chỉnh sửa chưa lưu trước tiên đòi hỏi một quyết định tường minh `Save changes, then archive`
     / `Discard changes` / `Cancel (Esc)` (`UnsavedChoiceDialog` dùng chung).
  2. Sau đó `TypedConfirmDialog` dùng chung (`Archive Planned Route`) đòi hỏi **gõ đúng chính xác tên route** (được so
     khớp sau khi trim và không phân biệt hoa thường) sau cảnh báo hệ quả bốn phần: route không còn được đưa ra cho các
     lần gán trong tương lai; các Quantity Flow đã được release với nó giữ các snapshot Assigned Route của chúng; lịch sử
     Movement thực tế không thay đổi; và route vẫn hiển thị để làm ngữ cảnh lịch sử.
- Các template đã archive không bao giờ được đưa ra cho các lần gán route mới.
- **Không có hệ thống phiên bản template riêng**: các snapshot Assigned Route hiện có lưu giữ các định nghĩa route lịch
  sử (PROJECT_PROFILE §8.8, §8.10).

## 13.4 Các trạng thái và ranh giới triển khai

Các trạng thái loading, lỗi và rỗng theo kiểu trình bày view-state tiêu chuẩn (`No Planned Routes defined yet.` / một
thông điệp không khớp).

View là UI sản xuất trên bề mặt `/api/route-templates`: tạo mới, chỉnh sửa (toàn bộ tập bước), Duplicate (một thao tác
tạo trên server cho `{name} (variant)`), Archive (đã từng được dùng) và Delete (chưa từng được dùng), và dialog sử dụng
(200 Quantity Flow được release gần nhất, kèm tổng số). Các thao tác ghi bị chặn khi mất kết nối. Est. time được nhập và
hiển thị bằng các duration token dùng chung (`45m`, `4h 00m`, `2d 03h`). Một Operation hoặc Machine đã lưu mà không còn
được cung cấp sẽ render `(unavailable)` và phải được thay thế trước khi lưu (§13.2). Các bản xem trước `?state=` chỉ dành
cho phát triển là có sẵn.

---

# 14. Part Numbers (Management)

Part Numbers là một sub view của Management tại `/management/part-numbers` (nó theo theme mode toàn cục, §2.1). Đây là
nơi duy nhất cho **metadata gốc của PartNumber** (PROJECT_PROFILE §8.1, §21 *Part Numbers*): dữ liệu gốc của sản xuất,
được quản lý theo permission bởi các vai trò sản xuất được ủy quyền, như Machines và Planned Routes (§12, §13;
PROJECT_PROFILE §20).

- Chính chuỗi PN chuẩn hóa vẫn là danh tính sản xuất ổn định. Các bản ghi ở đây **chỉ là metadata hiện tại tùy chọn**
  (hình ảnh, tên/mô tả, revision mang tính thông tin, ánh xạ hoạch định nguồn lực doanh nghiệp (enterprise resource
  planning, ERP)) và không bao giờ chặn việc sử dụng trong sản xuất: một PN chuẩn hóa luôn có thể sử dụng, dù có hay
  không có bản ghi gốc.
- Trong thanh sub view của Management, view nằm **ở vị trí kế cuối, ngay trước Machines** (§1.1).
- View theo họ trình bày của Machines (§12): cùng các kiểu page header, toolbar (tìm kiếm và action chính), bảng, dialog
  và Danger Zone.

**Kỷ luật về câu chữ.** Toàn bộ câu chữ thông thường hướng tới người dùng trên view này dùng ngôn ngữ sản xuất đơn giản
("Part Number details", "saved details", "production history"), không bao giờ dùng từ vựng domain hay triển khai
(`canonical`, `identity`, `metadata record`, `master record`, `Movement history`), vốn chỉ nằm trong đặc tả này và trong
code. Mô tả trang là đúng chính xác `Manage optional Part Number details, images, ERP IDs, and barcode labels.`

## 14.1 Danh sách Part Number

Một danh sách có thể tìm kiếm (PN, tên/mô tả, revision, ERP id) với một action **+ New Part Number**, dùng chung bố cục
toolbar của Machines (§12.1). Các cột là **Image | Part Number | Name / Description | Revision | ERP ID | Barcode**.

- **Image.** Hình ảnh PN đã upload của bản ghi, hoặc **MỘT placeholder hình ảnh PN mặc định dùng chung**, đúng chính xác
  placeholder mà panel Part Details của Tracking dùng (§7.2). Không có hình ảnh mặc định thứ hai nào ở bất cứ đâu.
- **Part Number.** PN chuẩn hóa **CHỮ HOA** trong kiểu trình bày monospace tiêu chuẩn.
- **Name / Description.** Tên hoặc mô tả gốc; giá trị vắng mặt render `—` (§3 rule 9).
- **Revision.** Revision hiện tại mang tính thông tin, `—` khi vắng mặt.
- **ERP ID.** Id ánh xạ ERP, `—` khi vắng mặt.
- **Barcode.** Giá trị suy ra `PF:PN:<part-number>` ở MỘT tông đọc barcode monospace muted dùng chung (§12.3): một giá
  trị nhận dạng được suy ra, không bao giờ là một trường có thể chỉnh sửa độc lập, và không bao giờ là một badge.
- **Kích hoạt toàn hàng.** Nhấp vào bất kỳ đâu trên một hàng sẽ mở dialog Edit Part Number (§14.2). Nút hàng của ô PN
  (accessible name `Edit {pn}`) là lối vào cho bàn phím và trình đọc màn hình, theo cùng mẫu toàn hàng như các hàng
  Machines, Planned Routes và Tracking, nhưng không có focus ring hiển thị trên nút hàng (khác với Tracking, §7).

**Không có ghi chú cấp trang bên dưới bảng**: ngữ nghĩa của việc xóa được giải thích tại nơi có action, trong phần xóa
của dialog Edit và lời xác nhận của nó (§14.2), và không bao giờ được lặp lại ở cấp trang.

## 14.2 Dialog New / Edit Part Number

**Chỉ đọc đối với người dùng không được phép quản lý Part Numbers.** Một người dùng như vậy mở cùng dialog ở chế độ chỉ
đọc, từ danh sách Part Numbers và từ control PN của các dòng demand của Work Order (§11.2). Nó có tiêu đề
`Part Number details`, với các giá trị dưới dạng văn bản (`—` khi vắng mặt), hình ảnh không có các control của nó,
`Barcode label…` được giữ lại, không có `Save`, không có phần `Delete Part Number Details`, và `Close (Esc)`.

- **Edit mở đầu bằng identity header chỉ đọc** (kiểu của §12.3): `Part Number` (được nhấn mạnh) và `Barcode` suy ra
  (`PF:PN:<part-number>`) dưới dạng các giá trị điềm tĩnh trên một panel nhẹ nhàng. Không có ô nhập PN và không có ô
  nhập barcode trên một bản ghi hiện có (PN là danh tính và không bao giờ được chỉnh sửa; barcode luôn được suy ra từ
  nó). Header cũng chứa lối vào **`Barcode label…`**, mở dialog nhãn có thể in (§14.3).
- **New Part Number** thu thập PN làm trường bắt buộc duy nhất của nó. Dữ liệu nhập được chuẩn hóa khi nhập đúng chính
  xác như mọi đường nhập PN khác (§4.4, §11.2: được trim; khoảng trắng bên trong bị từ chối kèm một lời giải thích inline
  và không bao giờ bị loại bỏ âm thầm; chữ thường được chuẩn hóa thành chữ hoa). Câu chữ phản hồi của trường là:
  - khoảng trắng bên trong → `Part Number cannot contain spaces or other whitespace.`;
  - một bản ghi đã tồn tại → `Part Number "{PN}" already has saved details.` (một bản ghi gốc cho mỗi PN chuẩn hóa);
  - một mục nhập hợp lệ → dòng trực tiếp mang tông success `✓ Will be saved as {PN} · Barcode {barcode}`.

  Tạo một bản ghi ở đây là việc nhập metadata tùy chọn trước; việc tạo khi dùng lần đầu tại khâu tiếp nhận (§4.7, §11.2)
  vẫn áp dụng.
- **Các trường metadata.** Name / Description, Revision (mang tính thông tin) và ERP ID, tất cả đều tùy chọn, được bố trí
  trên lưới form của Machines với placeholder `e.g. …`.
- **Image.** Hình ảnh hiện tại (hoặc placeholder mặc định dùng chung) với `Upload image…` / `Change image…` và, khi có
  một hình ảnh tùy chỉnh, `Remove image`; việc gỡ bỏ quay về placeholder mặc định dùng chung. Dòng trợ giúp nhẹ nhàng là
  `If no image is uploaded, the default Part Number image is shown.` Hình ảnh chỉ là metadata gốc.
- **Chỉnh sửa chưa lưu** được đánh dấu rõ ràng (`● Unsaved changes` bên cạnh tiêu đề dialog), action hủy là đúng chính
  xác `Cancel (Esc)` (§3 rule 10), và việc đóng khi có dữ liệu nhập chưa lưu sẽ hỏi trước: `UnsavedChoiceDialog` dùng
  chung trên một bản ghi hiện có (`Save changes` / `Discard changes` / `Cancel (Esc)`, nội dung
  `You have unsaved changes to {PN}.`), và một xác nhận `Discard new Part Number?` trên một bản ghi mới (nội dung
  `Your entered information will not be saved.`). Dialog New mở với focus ban đầu ở trường PN.
- **Phần xóa (bản ghi hiện có).** Dialog kết thúc bằng phần hướng tới người dùng **`Delete Part Number Details`** theo
  kiểu trình bày Danger Zone của §12.3: mô tả
  `This removes the saved image, description, revision, and ERP ID for {PN}. Production tracking and Work Order history are not affected.`
  và action `Delete details…`.
  - Việc xóa là một **xác nhận phá hủy đơn giản theo biến thể xác nhận attention** (§12.4; tông danger: badge biểu tượng
    ở giữa, tiêu đề mang tông danger được nhấn mạnh, phần mô tả trong lớp phủ danger nhẹ với PN ở tông chữ đầy đủ, không
    bao giờ toàn bộ thông điệp màu đỏ, và action xác nhận mang tông danger).
  - Nó là **cảnh báo mạnh nhất của luồng** (phần xóa của dialog Edit ở trên giữ cách xử lý danger zone nhẹ hơn) trong khi
    vẫn là MỘT xác nhận đơn giản: **không có xác nhận bằng cách gõ và không có bước bổ sung**, vì việc xóa có thể khôi
    phục bằng cách tạo lại bản ghi.
  - Nó có tiêu đề `Delete Part Number details?`, với action xác nhận `Delete details` và nội dung
    `This permanently removes the saved image, description, revision, and ERP ID for {PN}. The Part Number and its production history remain available.`
  - Chỉ bản ghi metadata gốc bị xóa (PROJECT_PROFILE §8.1). Work Order Demand, số lượng sản xuất, lịch sử Movement và
    các allocation không bao giờ bị động đến hay bị xóa lan theo. Mọi màn hình tiếp tục hiển thị PN chuẩn hóa với các
    trường suy ra từ bản ghi gốc vắng mặt (`—`, §7.1), và một bản ghi có thể được tạo lại về sau cho cùng PN.
  - **Không có archive PN, không có xóa mềm và không có vòng đời active/inactive** (PROJECT_PROFILE §8.1).

## 14.3 Nhãn barcode Part Number

`Barcode label…` mở **`Part Number barcode label`**, với mô tả
`Scan this label to identify Part Number {PN}.` Nó hiển thị một bản render **Code 128** thực của giá trị được quét
`PF:PN:<part-number>` (bộ mã hóa không phụ thuộc dùng chung `src/views/code128.ts`, §12.3) trên **thẻ nhãn trắng với mực
đen ở cả hai theme**, với **văn bản PN bên dưới barcode** là văn bản chính của nhãn và giá trị được quét đầy đủ là dòng
kiểm chứng monospace nhẹ nhàng. `Print Label` in đúng chính xác nhãn (print style ẩn mọi thứ khác); `Cancel (Esc)` quay
lại dialog.

Dialog được giữ đơn giản một cách có chủ ý: không có cấu hình barcode nào ở đây (sơ đồ namespace `PF:` vẫn nằm trong
Administration → Barcode configuration, §9). Dialog này là **MỘT bề mặt nhãn PN duy nhất** trong ứng dụng:
Management → Part Numbers (§14.2) và dialog `Edit Part Number` của dòng demand (§11.2) đi tới nó thông qua
`Barcode label…`, và luồng Add Part (§11.2, §11.3) mở nó trực tiếp. Tất cả chúng render các vạch thông qua một component
Code 128 dùng chung; không có thẻ nhãn PN thứ hai và không có bộ mã hóa thứ hai nào ở bất cứ đâu.

## 14.4 Các trạng thái và ranh giới triển khai

Các trạng thái loading, lỗi và rỗng theo kiểu trình bày view-state tiêu chuẩn (`No Part Number details have been added yet.`
/ thông điệp không khớp `No saved Part Number details match "{search}".`).

View là UI sản xuất trên bề mặt `/api/part-numbers`: một tìm kiếm có giới hạn trên server
(`Showing N of T Part Numbers`, `Show more`, tối đa 200 hàng), tạo mới, chỉnh sửa, upload và gỡ bỏ hình ảnh, và xóa cứng,
với các thao tác ghi bị chặn khi mất kết nối. Các bản xem trước `?state=` chỉ dành cho phát triển là có sẵn. Dòng danh
sách có giới hạn là một chỉ báo trạng thái danh sách (mẫu PN Tracking của §7.1), không phải ghi chú cấp trang mà §14.1
loại trừ.

---

# 15. Lịch sử phiên bản

Lịch sử phiên bản của tài liệu này là bảng thay đổi ở cuối tài liệu (Lịch sử thay đổi).

---

# 16. Ngoài phạm vi của UI Phase 2

UI được phê duyệt có chủ ý loại bỏ những thứ sau: một framework bản địa hóa (UI được phát hành bằng tiếng Anh, dùng từ
vựng của PROJECT_PROFILE), biểu đồ và dashboard phân tích, và barcode lệnh quản trị.

---

# 17. Câu hỏi mở

Không có câu hỏi mở nào.

---

# Lịch sử thay đổi

| Ngày | Commit | Thay đổi |
|---|---|---|
| 2026-11-12 | uncommitted | Mọi dialog nhập liệu đóng ngay khi chưa thay đổi và hỏi trước khi bỏ dữ liệu đã thay đổi; các select bị ghim chiều cao cố định bỏ padding dọc để phần chân chữ không bao giờ bị cắt; một Scan Station mới đề xuất Station ID từ Area của nó, và ô Devices của nó nêu tên các thiết bị đã enroll và cả ô mở danh sách thiết bị; panel enroll trở thành một card căn giữa với ô nhập code lớn. |
| 2026-11-09 | 398c617 | Căn chỉnh theo code: các vùng action Undo và Retry trong suốt khi nghỉ với đường kẻ phân cách thụt vào, và một transfer `ON_MACHINE` thông báo `AREA_COMPLETED, then TRANSFERRED (one command)`. |
| 2026-11-09 | 398c617 | Viết lại thành nội dung hiện tại cho người đọc mới; các yêu cầu chỉ nằm trong ghi chú phiên bản cũ (§15.1–§15.17 trước đây) được chuyển vào phần thân; không có quy tắc nào thay đổi. |
| 2026-10-06 to 2026-10-08 | 702ab1c … 7b24d10 | Thêm đăng nhập User với Management và Administration nhận biết permission, đăng ký thiết bị Scan Station, điều chỉnh allocation, chỉnh sửa assigned route, audit trail của PN, theme theo từng User, import file Work Order và thông báo cập nhật phiên bản phát hành. |
| 2026-10-04 to 2026-10-06 | 9f466fa … 69b8ec1 | Đặc tả Workers, các chế độ Worker ID, badge session và các chốt chặn, lý do Undo, Part Numbers, Planned Routes, cài đặt hiển thị và Due Soon, lưu giữ dữ liệu, Users và vai trò; các mục Hot đã được allocate đầy đủ tự động rời khỏi danh sách. |
| 2026-08-25 to 2026-09-13 | d7bcd06 … f016d41 | Đưa các quy trình của Scan Station, Production Board, Area Board, PN Tracking và Completed Work Orders lên dữ liệu server, với trạng thái feed, phân trang, Receive Quantity bên cạnh số lượng hiện có và cảnh báo về thời lượng dự kiến. |
| 2026-08-13 to 2026-08-24 | c4509f9 … 1fe73e9 | Thêm trang Part Numbers, bố cục cho chiều rộng điện thoại, các bước xác nhận cuối cùng cho DONE, QUEUE và Undo, Worker badge session, một nhãn PN dùng chung, giới hạn chỉnh sửa demand đã release và danh sách Work Order có giới hạn từ server. |
| 2026-08-12 | fa4b51d, c4f18a6 | GUI Design v18: PN chuẩn hóa chữ hoa không có khoảng trắng bên trong trở thành danh tính và bản ghi Part Number trở thành tùy chọn và có thể xóa; việc giữ nguyên kiểu chữ, archive PN và trạng thái PN inactive bị loại bỏ. |
| 2026-08-05 to 2026-08-12 | 5a18ce6 … cb8c457 | GUI Design v17: row action DONE cho các Area không có Machine, các dialog Machines với Asset Tag tự động và xác nhận theo từng bước, tên PN Tracking, và Work Order đã hoàn thành trên trang riêng. |
| 2026-08-05 | b59ef51 … 53481a8 | GUI Design v16: một đồng hồ dùng chung cho thời gian đã trôi qua và đếm ngược, focus được giữ bên trong các dialog do lượt quét mở ra, khâu tiếp nhận được đổi tên thành Receive Quantity, và một banner offline ngắn hơn. |
| 2026-08-05 | 01a7904 | GUI Design v15: reactivate Machine, các dialog xác nhận bằng cách gõ và ba lựa chọn, chỉnh sửa bằng nhấp hàng trong Machines và Planned Routes, xoay vòng tính theo số hàng mỗi trang, và bốn loại hướng dẫn. |
| 2026-08-04 | 74e0386, cca711b | GUI Design v14: các view Machines và Planned Routes, thời gian trạng thái Machine trên các thẻ giám sát, một panel chi tiết Tracking nổi, và các cột dùng chung được đo theo nội dung trên board và trong Priority. |
| 2026-08-04 | 1a043cc | GUI Design v13: Kiosk mode của Production Board và chỉ báo xoay vòng, thẻ Station Selector toàn thẻ, một footer Scan Station dùng chung, nhập số lượng nhận biết cảm ứng và các dialog Work Order responsive. |
| 2026-08-03 | 1c6e73e | GUI Design v12: chỉnh sửa số lượng nhận biết con trỏ, không có thanh cuộn giả, và tinh chỉnh phân cấp dialog, mức nhấn mạnh xác nhận, đồng hồ board, các cột, footer và chuyển tiếp thứ hạng Priority. |
| 2026-08-03 | 4271796 | GUI Design v11: chế độ sản xuất của Scan Station, phân trang board thủ công, ngọn lửa Hot trong cột No., xác nhận Priority hai snapshot và một chốt bảo vệ tự động cho câu chữ được render. |
| 2026-07-31 | 8dd337b | GUI Design v10: hoàn thành DONE cấp Area với nhóm đã hoàn thành của nó, tổng số trong header Scan Station được cấu trúc lại, và Work Order Details dưới dạng dialog modal. |
| 2026-07-30 | 79ada7d | GUI Design v9: mọi action của Scan Station trở thành một wizard nhiều bước với view xác nhận riêng, với thông báo quét nổi và bố cục hàng PN dạng lưới. |
| 2026-07-28 | fbb38cb | GUI Design v8: Scan Station được xây dựng lại quanh các action PN một lần không có Machine session, với Floating route, Work Order Number để trống, ý định Repair, Scrap và bổ sung số lượng. |
| 2026-07-26 | 74edbdd | GUI Design v7: phát hiện kết nối nhanh, ngày đến hạn tùy chọn, nhập Work Order thủ công là chính, đồng hồ board trực tiếp với phân trang được đo, và xác nhận trước khi sắp xếp lại Priority. |
| 2026-07-24 | 766ed7a, d5d5a1c, 027567e | GUI Design v6: Purchase Orders được đổi tên thành Work Orders, với New Work Order là một dialog modal có thêm dòng, quy tắc gỡ bỏ, ô nhập ngày native và chốt bảo vệ thay đổi chưa lưu. |
| 2026-07-22 | f47da8e | GUI Design v5: nút bật/tắt theme Dark/Light toàn cục và cắt ngắn bằng dấu ba chấm cho các PN dài trên thẻ Area Board. |
| 2026-07-22 | bf2183d | GUI Design v4: Manager Summary được gộp vào Area Board thành tổng quan All Areas, và PO Intake được cấu trúc lại thành Purchase Orders. |
| 2026-07-22 | 8f642e4, 2a53463 | GUI Design v3: Area Board, Manager Summary, Tracking, PO Intake và Priority được nhóm thành các sub view của một view Management. |
| 2026-07-22 | b45a81f, 7bd5180 | GUI Design v2: Machine session thay thế việc quét Machine theo từng Part, Hot demand được xếp hạng theo Department với Undo và Redo, và Manager Summary cùng Priority Management được đặc tả. |
| 2026-07-20 | c2aeda5 | GUI Design v1: thiết kế GUI ban đầu. |
