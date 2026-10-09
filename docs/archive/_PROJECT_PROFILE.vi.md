# Machineshop Parts Flow

## Mô tả:

> **YÊU CẦU XUYÊN SUỐT: Dự án nhằm mục đích keep track Part-Number, cho nên một Part-Number phải có lịch sử xử lý (bắt đầu từ khâu nào, đã đi qua những khâu nào, đang ở khâu nào hoặc đã kết thúc hay chưa). Part-Number một khi đã được ghi nhận thì phải luôn luôn có thể được truy xuất bất kể thời điêm.**

### Đối tượng làm việc:
- Đối tượng làm việc là Part.
- Một Part thuộc một hoặc nhiều P.O. (Purchase orders) và có thể phục vụ cho nhiều Job Numbers.
- Một P.O. là một danh sách các part.
- Một P.O. bao gồm (tạm thời, có thể bổ sung thêm nếu thiếu): PO Number, Received Date, Part List
- Một Part bao gồm (tạm thời, có thể bổ sung thêm nếu thiếu): Part Number, Request Type (VD: New, Rework, Modify), Số lượng, các Job Numbers cần part, Due Date (ngày cụ thể, hoặc chưa xác định), Priority (là thứ tự của part trong danh sách các part được đánh dấu là HOT, cần ưu tiên làm trước), icon/ảnh đại diện (image, ico, url, ...; có thể empty)
    + Part Number và PO number thường không có format cố định, có thể dài ngắn khác nhau, nó được sinh ra bởi ERP nên hãy xem nó đơn giản là một string. Tuy nhiên khi tạo barcode cho Part-Number, tôi muốn thêm thông tin làm sao để khi scan một barcode bất kỳ thì hệ thống đều có thể biết barcode này có phải là của một Part-Number nào đó hay không.
    + Request Type (tạm gọi vậy) là để mô tả tính chất công việc: Tạo mới, làm lại hay sửa chữa...
    + Một Part có thể sẽ có trạng thái cần được ưu tiên (Priority)
    + Để thay đổi giá trị Priority của 1 part: Manager sẽ thay đổi thứ tự của nó trong danh sách các hot parts. (có page riêng để thực hiện)
- Một Part có thể là build from scratch, rework hoặc modify.
- Trong thực tế:
    + Mỗi part sẽ có 1 folder chứa các bản vẽ của nó.
    + Ngoài bìa folder sẽ dán barcode của part number đó vì part barcode không thể dán trực tiếp trên part (không thể bảo toàn trong quá trình chạy CNC).
    + Bản vẽ của part chỉ chứa part number chứ không có barcode. Lý do là các bản vẽ có thể thay đổi lên các version mới, nên việc in barcode ra và dán lên từng bản vẽ sẽ rất mất thời gian và công sức. Chưa kể các folder này có thể tái sử dụng khi có PO. Cho nên để tiết kiệm thời gian và chi phí thì chỉ dán barcode trên bìa folder.
    ** Khi parts di chuyển giữa các khâu thì chúng luôn đi kèm với folder hồ sơ của chúng.**
    + 

### Area (tạm thời gọi là Area, nếu thật sự có từ phù hợp hơn thì hãy kiến nghị, đừng cố kiến nghị cho bằng được, ưu tiên từ đơn)
- Mỗi Area đại diện cho một khâu trong quá trình xử lý part. (ví dụ: Buy marterial, Cut, Lathe, Mill, Manual, Deber, Plating (Outsourcing Services), Stockrom (dã nhập kho))
- Mỗi khâu đa phần chỉ có 1 Operation chính (VD: Lathe là Turning, Cut -> Cutting, Stockrom -> Receiving...). Nhưng cũng có thể có nhiều Operations, ví dụ External -> Plating, Painting, Testing, ...
- Mỗi khâu có thể có nhiều Machines (VD: Mill 1, Miil 2, ...), hoặc chỉ có 1 Machine (VD: Cut, Deburr, ...), hoặc không có Machine (VD: External, Stockroom)
- Mỗi khâu sẽ có kệ hoặc khu vực để parts. Tại đây sẽ có barcode reader và màn hình để scan nhận part.
- Các thuộc tính của khâu xử lý (tạm thời, có thể bổ sung thêm nếu thiếu): ID (barcode), Name (ví dụ: Cut, Lathe...), Department Name (VD: Machineshop, Outsourcing, Stockroom, ...), Color (màu đại diện), Avarta (Icon đại diện)
    + Màu và ảnh đại diện có thể thay đổi trong Admin page.
    + ID được sinh ra khi Admin tạo Area trong Admin page.
    + Area name có thể thay đổi nhưng ID của nó là duy nhất và bất biến.
- Nếu một khâu có nhiều máy, thì mỗi máy sẽ có 1 barcode riêng.
- Nếu khâu chỉ có 1 máy hoặc không có máy thì chỉ có 1 barcode đại diện cho khâu đó.
    
### Phân quyền:
- Admin: Có toàn quyền trên hệ thống.
- Manager: Được phép xem, thay đổi, chỉnh sửa thông tin của đối tượng làm việc. ví dụ như PO, Parts, Part List, Priority Parts (hot request)...
- Operator: Chỉ có thể scan part barcode, hoặc remove part khỏi danh sách Area List (nếu được Admin/Manager cho phép)

## Đề xuất tên / words:
- Nếu có từ thay thế phù hợp hơn cho các từ đang sử dụng như Area, Department, Outsourcing Services, Buy Marterial, ... thì hãy mạnh dạn đề xuất. (Ưu tiên từ đơn, và chỉ đề xuất nếu nó thực sự hay hơn, phù hợp hơn, chính xác hơn. Đừng cố đề xuất chỉ vì tôi yêu cầu)
- Cần đề xuất một bộ từ vựng dành riêng sẽ được sử dụng cho dự án.

## Quy trình:
1. Nhận PO hoặc Part (rework / modify):
    - Nếu là PO thì sẽ được nhập bằng tay / import from file / hoặc được lấy tự động từ ERP (ERP API tạm thời chưa có, nhưng tương lai sẽ bổ sung)
        + Các part trong received PO mặc định sẽ là New và nằm ở khâu 'Buy Marterial'
        + Sẽ có người duyệt danh sách part:
            1. Người nhận sẽ kiếm folder (có dán Part-Number barcode) của part tương ứng.
                + Nếu phát hiện có Part-Number giống vậy đang được gia công rồi thì có thể cập nhật lại thông tin của Part-Number đang chạy là có thêm vài Parts cần làm thêm (đang ở khâu "Buy marterial") hoặc sẽ đánh dấu Part-Number này là Inqueue (tạm gọi vậy).
                + Khi Part-Number đang gia công kia xong mọi công đoạn và hoàn tất việc nhập kho, folder của nó sẽ được trả về, khi này người nhận PO mới kiểm tra xem Part-Number này có số lượng mới đang Inqueue không thì họ sẽ lấy ra cho làm.
                + Vì cùng một Part-Number có thể có trong nhiều PO khác nhau, nên nếu có cùng Part-Number đang inqueue thì chỉ đơn giản là update Po và số lượng tương ứng của PO đó vô cùng Part-Number. (Ví dụ Part-Number ABC, có PO-123 (số lượng 3 cái) và PO-456 (số lượng 5 cái). Khi chuyển từ Inqueue sang working thì tổng số lượng của part ABC cần work là 8 cái, trong đó 3 cái thuộc về PO-123 và 5 cái thuộc về PO-456)
                + Nếu Part có folder thì sẽ được đưa đến khâu đầu tiên cần xử lý (thường là Cut), scan barcode tại khâu đó để cập nhật vô hệ thống, sau đó tại đây họ tự lấy Marterial để làm.
            2. TÍNH NĂNG DỰ KIẾN LÀM: Chỉ định flow cho nó (VD: Cut -> Lathe -> Deber -> O/S -> Restock). Các flow này phải trực quan và có thể tái sử dụng (tạo 1 lần, sử dụng được với mọi part), có thể thêm, xóa, sửa.
                + Khi part được chỉ định flow xong thì flow đó phải là của riêng nó, việc sửa flow được tạo sẵn không ảnh hưởng đến flow của part đã được set.
                + Flow của part đã được set có thể sửa, hoặc tự động cập nhật lại nếu part không đi đúng flow (được scan tại khâu không đúng theo flow, khi đó nó sẽ yêu cầu người scan xác nhận trước khi cập nhật thay đổi trong flow).
    - Nếu là Part REWORK/MODIFY thì người đưa sẽ mang nó đến chính khâu cần làm đầu tiên:
        + Scan barcode của part + REWORK/MODIFY barcode
        + Part được scan sẽ tự tạo PO (VD: REWORK, MODIFY...) nếu chưa có, hoặc bổ sung váo danh sách part (hoặc cập nhật số lượng) của PO đó.
            * Nếu đang có part có cùng Part-Number đang được gia công thì sẽ cho user lựa chọn:
                a. Nếu có Part-Number tương tự đang được gia công thì hỏi user có muốn thêm vô để tăng tổng số lượng part đang gia công không?
                    - Cho phép add vào một trong các active PO của Part-Number đó
                    - Nếu không chỉ định PO cụ thể thì tự động tạo một PO tạm thời cho nó. (VD: PO202606021523-NEW,  PO202606021530-MODIFY, ...)
                b. Nếu không có PO nào có Part-Number giống vậy đang gia công thì:
                    - Hỏi họ có muốn add Part-Number này vô một trong các PO đang active không (cho chọn PO)
                    - Hoặc cho tạo PO giã định tạm thời (ví dụ: REWORK, MODIFY...) cho nó.
        + Nếu chỉ scan barcode của part thì có thể có 2 trường hợp:
            1. Part thuộc một PO nào đó đang được xử lý ==> tự động cập nhật part đã được chuyển sang khâu này hoặc hiện thông báo xác nhận (mặc định là tự động; Admin có thể chỉnh trong Admin Dashboard) ==> Hiện thông báo xác nhận số lượng (cho phép chỉnh số lượng; Admin có thể chỉnh trong Admin Dashboard để không hiện thông báo xác nhận). Nếu số lượng xác nhận khác với số lượng ban đầu trong PO thì thêm (+/- n) vào bên cạnh Quantity. -n là số lượng part bị thiếu, +n là số part tăng thêm do làm dư. VD: 5 (-1)
            2. Part không có trong danh sách các part đang được xử lý của Machineshop ==> Đây có thể part REWORK/MODIFY hoặc cũng có thể là một sự nhầm lẫn nào đó ==> hiện thông báo xác nhận. Cho phép chọn REWORK / MODIFY / CANCEL
2. Sau khi part được scan tại một khâu (receive) thì part sẽ thuộc về khâu đó:
    - Nếu khâu chỉ có 1 người làm (chỉ có 1 máy) thì part xem như đang được gia công tại khâu đó.
    - Nếu khâu có nhiều máy thì part sẽ phải nằm chờ được gia công:
        + Part được để lên kệ nhận hàng.
        + Người phụ trách máy A khi cần gia công part sẽ đến kệ, lây part -> scan part barcode -> scan machine barcode (NOTE: thứ tự scan không quan trọng, cần phải flexible)
        + Sau khi scan part + machine thì part đó xem như đang được gia công tại machine đó.
    - Quá trình gia công chỉ kết thúc khi part được scan tại khâu tiếp theo (receiving)
3. Parts sau khi được xử lý xong ở một khâu: Người tại khâu đó sẽ mang các parts đó (kèm hồ sơ của chúng) đến khâu tiếp theo.
3. Người nhận tại khâu tiếp theo sẽ scan part barchode để xác nhận đã nhận part (cũng như để hệ thống keep track được part đã đi đến khâu nào)
    - Tự động cập nhật part đã được chuyển đến xử lý tại khâu này, không yêu cầu phải bấm nút gì cả (trừ khi Admin bật thông báo yêu cầu xác nhận)
    - Cho phép hoàn tác nếu cảm thấy đã scan sai part. (giống như đi siêu thị, scan đồ vô giỏ hàng, sau đó đổi ý muốn remove món hàng đó ra khỏi giỏ hàng không mua nữa)
4. Khâu cuối cùng là Stockroom:
    - Khi part được scan để nhập kho thì nó được xem như hoàn thành toàn bộ quy trình. Đánh dấu đã nhập kho.
    - Khi toàn bộ part của 1 P.O đã nhập kho thì đưa PO vào danh mục History (để report) và không còn nằm trong danh sách các PO đang xử lý của Machineshop nữa.
    
    
## Quy mô:
- Machineshop chỉ là một Department.
- Ứng dụng này trước mắt phục vụ cho Machineshop. Sau này có thể sẽ mở rộng cho bên mua sắm vật tư hoặc cho bên lắp ráp (Assembly) / sản xuất (Production)
==> Cần tổ chức app để dễ dàng mở rộng ra sau này.

## Các màn hình chính: (các tên gọi bên dưới là tạm thời, cần khuyến nghị tên phù hợp hơn nếu có)

### 1. Scan View:
   - Mỗi màn hình Scan là đại diện của 1 khâu xử lý.
   - Scan view chủ yếu được hiển thị bằng tablet nên hãy tối ưu cho tablet.
   - Scan view là single page cố định, không có route điều hướng đi đâu hết.
   - Khi scan part barcode, nó tự biết là đang scan part cho khâu xử lý nào (do đó không cần scan barcode station làm gì)
   - Cho phép scan Part barcode hoặc REWORK/MODIFY barcode không theo thứ tự: Khi scan 1 barcode, tự động nhận diện barcode đó là của part hay REWORK/MODIFY.
   - Thành phần chính trên màn hình:
     + Department name
     + Area name (tên của khâu xử lý. VD: Lathe, Manual, ...): Chữ bự, ngay đầu, có thể xem như tiêu đề.
     + Dưới Area name có thể là description.
     + Textbox nhập part code: Lớn, focus.
     + Part detail: hiện thông tin của part vừa được scan.
     + Part list:
        + Danh sách các part đang nằm tại khâu này (xếp theo thứ tự mới nhận cho đến cũ nhất)
        + Một part trong danh sách có thể:
            + Xóa: nếu bị xóa thì nó tự động trở về khâu xử lý trước đó.
            + Sửa: điều chỉnh số lượng. tùy theo việc tăng hay giảm số lượng so với PO mà sẽ có dạng mở ngoặc +/-. Ví dụ: 10 (-2), 5 (+3), ...

### 2. Production View:
Đây là giao diện được dùng để hiển thị trên màn hình lớn cho mọi người cùng xem.

Working view cũng là cố định và không cần điều hướng sang các page khác.

Có các thành phần chính:
- Department name
- Part list chứa danh sách của tất cả các part đang được xử lý tại Department.
- Part list có dạng bảng đại khái như sau:

| No. | Part Number       | Areas (Q.ty)      | Job No.      | Due    | Days Left | Total Days |
|-----|-------------------|--------------------|--------------|--------|-----------|------------|
|  1  | PF-BRACKET-00003  | Cut (3), Lathe (6) | 17363, 17493 | Oct 29 |     15    |     20     |
|  2  | PF-PLATE-RET-0099 | Plating (5)        | 16942        | Nov 10 |     27    |     10     |

- Danh sách part được sắp xếp theo thứ tự giảm dần của Hot Request (nếu có), tiếp theo là theo due date (days left từ nhỏ đến lớn)
- 'Day Left' có thể là số âm nếu ngày hiện tại vượt quá 'Due Date'
- 'Day Left' âm phải được tô màu đỏ.
- Thông tin part trong hai cột 'Part Number' và 'Area' phải được tô màu theo màu đại diện của Area.
- Host Parts sẽ nhấp nháy chữ trong cột 'Part Number'
- Danh sách part dài được chia thành nhiều page, và sẽ lần lượt được hiển thị trong N giây. (mặc định là 10 giây, có thể chỉnh trong Admin page)
- Chia pages động theo độ phân giải màn hình: Số lượng parts của 1 page là số lượng vừa đủ để hiển thị trên màn hình. Do đó tùy theo độ phân giải, tùy theo cỡ chữ mà sẽ được chia theo số lượng khác nhau.

### 3. Manager View:
Chủ yếu hiển thị trên Laptop, PC, tablet, phone cá nhân. Có các page sau:

#### Summary Page:
- Màn hình được bố cục theo dạng cột, mỗi cột đại diện cho một khâu xử lý.
- Trong mỗi cột (group) sẽ có:
    + Tên và mô tả của khâu xử lý
    + Tổng số lượng part đang nằm tại khâu.
    + Khung seacrh: cho phép search part number, PO number.
    + tùy chọn sắp xếp: cho phép sort theo due date, part number, PO, hoặc mặc định.
    + Danh sách các parts được sắp xếp mặc định theo thứ tự giảm dần của Hot Request (nếu có), tiếp theo là theo due date (days left từ nhỏ đến lớn)
    + Part list có các thông tin đại khái như sau: Part Number, Q.ty, PO, Days Left

        |------------------------------------------------|
        | ICON | PF-BRACKET-00003 🔥            10 (+5) |
        |      | PO-2026-000123             15 days left |
        |------------------------------------------------|
        | ICON | PF-PLATE-RET-0099                     3 |
        |      | PO-2026-AS123              27 days left |
        |------------------------------------------------|

    + Khi rê chuột vào icon (ảnh đại hiện) thì hiển thị ảnh phóng to (kích thước gốc nếu nhỏ hơn max limit theo settings) của part đó nếu có.
    + Khi click vào days left thì tự chuyển đổi qua lại việc hiển thị giữa days left và due date.
    + Nếu màn hình không thể hiển thị hết các cột thì cho phép slide qua trái/phải.

#### Management Page:
- Giao diện tương tự như Working View.
- Có route sang các page khác.
- Có search box cho từng columns.
- Có sort theo từng column.
- Hiển thị thêm một số thông tin không được hiển thị trong Working View như:
    + PO number
    + Received Date.
    + ...
- Không cần chia pages, thay vào đó hiển thị luôn một danh sách dài tất cả các parts đang xử lý trong Departments.
- Cho phép show/hide coulmns.
- Hỗ trợ Print / Export (PDF, Excel, ...)
- Khi click vô 1 part nó sẽ hiển thị:
    + Chi tiết đang ở khâu nào, đã xử lý qua những khâu nào, thời gian đã hoặc đang xử lý ở mỗi khâu là bao lâu.
        + Trình bày trực quan bằng cách Vẽ đại khái như sau: (khối Area nào đã xử lý xong thì tô dark green, đang xử lý là light blue, chưa xử lý thì không tô)
            ┌───────────────┐    ┌─────────┐    ┌───────────┐    ┌──────────┐    ┌──────────────┐    ┌─────────┐
            │ Marterial: 0d ├───►│ Cut: 1d ├───►│ Lathe: 3d ├───►│ Mill: 4d ├───►│ Plating: 10d ├───►│ Stocked │
            └───────────────┘    └─────────┘    └───────────┘    └──────────┘    └──────────────┘    └─────────┘
        + Part Flow này không cố định, trước mắt nó được xác định dựa trên lịch sử các khâu đã trải qua. Sau này sẽ cải tiến nó thành pre-defined flow sau.
    + Cho phép chỉnh sửa các information của part. ví dụ như: số lượng gốc trong PO, due date, hot request, reverse ngược lại các khâu đã trải qua hoặc trực tiếp nhảy sang khâu khác.
    + Khi một part được set là Hot request:
        1. Hiện một danh sách tất cả các Hot Part đang có trong Department. (Sắp xếp theo mức độ ưu tiên từ cao xuống thấp)
        2. Part mới được add vô mặc định sẽ nằm cuối danh sách.
        3. Part có thể được kéo thả để thay đổi vị trí của nó trong danh sách. (cũng chính là mức độ ưu tiên)
        4. Save hoặc Cancel để đóng danh sách.

#### Settings Page:
- Chứa các settings cho page views.

### 4. Admin View:
- Giao diện gồm những page tương tự như Manager View.
- Chỉ khác phần Settings Page sẽ có thêm 1 số chức năng chỉ dành riêng cho Admin như:
    + Thêm / xóa / sửa các khâu xử lý.
    + Quản lý users.
    + Phân quyền cho Manager / Operator.
    + Bật / tắt các thông báo.
    + Chỉnh sửa các thông số kỹ thuật được đặt trong này (nếu có)
- Manager và Admin Views yêu cầu username/password
