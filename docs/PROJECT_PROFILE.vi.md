# Hồ sơ dự án PartFlow v22

> **Bản gốc tiếng Anh:** [`PROJECT_PROFILE.md`](./PROJECT_PROFILE.md). Đồng bộ với bản tiếng Anh tại commit `b405c6e` cộng các thay đổi chưa commit ngày 2026-11-09.
> Bản tiếng Anh là bản có thẩm quyền: nếu hai bản khác nhau, bản tiếng Anh đúng và bản tiếng Việt phải được sửa lại.

> **Trạng thái:** Tài liệu sống (Living Document)
> **Thẩm quyền:** Hồ sơ dự án chuẩn (canonical) về hành vi domain và định hướng sản phẩm của PartFlow

Hồ sơ này là nguồn chuẩn cho từ vựng domain, quy tắc nghiệp vụ, bất biến (invariant), quy trình và phạm vi sản phẩm
của PartFlow. Nếu bạn mới làm quen với PartFlow, hãy đọc tài liệu này trước, và quay lại đây mỗi khi cần biết hệ
thống phải làm gì và vì sao. Tài liệu viết cho tất cả những ai xây dựng, review hoặc vận hành PartFlow. Các tài liệu
chuẩn khác dựa vào hồ sơ này: [`GUI_DESIGN.md`](./GUI_DESIGN.md) sở hữu giao diện người dùng đã được phê duyệt và tham
chiếu về đây cho các quy tắc nghiệp vụ; [`IMPLEMENTATION_ROADMAP.md`](./IMPLEMENTATION_ROADMAP.md) sở hữu thứ tự triển
khai và trạng thái triển khai hiện tại, kể cả các giới hạn tạm thời; các tài liệu slice như
[`SLICE1_DATA_MODEL.md`](./SLICE1_DATA_MODEL.md) chứa chi tiết triển khai cấp dưới và không được mâu thuẫn với hồ sơ
này; [`DEPLOYMENT.md`](./DEPLOYMENT.md) sở hữu việc triển khai hệ thống (deployment). Hồ sơ này mô tả hành vi mục tiêu;
việc một quy tắc đã được triển khai hay chưa được nêu trong roadmap. Mã nguồn trích dẫn hồ sơ này theo số mục (ví dụ
"PROJECT_PROFILE §14"), nên các số mục được giữ ổn định.

Hồ sơ dùng một số chữ viết tắt xuyên suốt: Part Number (PN), Work Order (WO, chỉ dùng ở những chỗ mà §7 Work Order cho
phép), hệ thống hoạch định nguồn lực doanh nghiệp (enterprise resource planning, ERP), sản phẩm khả dụng tối thiểu
(minimum viable product, MVP — bản phát hành sản xuất đầu tiên), giao diện người dùng (user interface, UI) và Admin
(vai trò Administrator, §20). Mọi chữ viết tắt khác được viết đầy đủ ở lần xuất hiện đầu tiên.

---

# 1. Tổng quan dự án (Project Overview)

## Mục đích (Purpose)

PartFlow là một hệ thống theo dõi nội bộ tại xưởng sản xuất, được thiết kế để theo dõi các Part Number (PN) và số lượng sản xuất vật lý khi chúng di chuyển qua các Area sản xuất.

Hệ thống phải luôn trả lời được:

- Part Number nào đang được xử lý.
- Những Area nào hiện đang giữ số lượng của Part Number đó.
- Hiện có bao nhiêu chi tiết vật lý trong mỗi Area.
- Operation nào đang được thực hiện.
- Machine nào hiện đang xử lý một số lượng, khi có áp dụng.
- Những Work Order nào yêu cầu Part Number đó.
- Mỗi Work Order cần bao nhiêu chiếc.
- Công việc sản xuất nào còn lại.
- Mỗi số lượng đang hoạt động dự kiến sẽ đi theo route nào.
- Mỗi số lượng đã đi qua những Area và Machine nào.
- Các số lượng yêu cầu đã được hoàn tất và nhập kho hay chưa.
- Các số lượng đã hoàn tất được phân bổ cho các Work Order như thế nào.

PartFlow được chủ ý tập trung vào việc theo dõi sản xuất gọn nhẹ.

PartFlow không phải là hệ thống hoạch định nguồn lực doanh nghiệp (ERP), hệ thống điều hành sản xuất (MES), hệ thống quản lý tồn kho, công cụ lập lịch sản xuất, hệ thống kế toán hay nền tảng điều khiển máy móc.

Tích hợp ERP có thể được bổ sung sau, nhưng ứng dụng phải luôn sử dụng được một cách độc lập mà không cần kết nối ERP.

Đợt triển khai ban đầu nhắm tới Department Machine Shop. Kiến trúc phải luôn đủ linh hoạt để hỗ trợ thêm các Department khác mà không phải thay đổi mô hình theo dõi cốt lõi.

---

# 2. Mục tiêu thiết kế (Design Goals)

PartFlow được thiết kế xoay quanh các hoạt động thực tế tại xưởng sản xuất thay vì các quy trình sản xuất được lý tưởng hóa.

Hệ thống phải ưu tiên:

1. Độ chính xác của việc theo dõi.
2. Sự đơn giản trong vận hành.
3. Các quy trình nhanh, dựa trên barcode (mã vạch).
4. Lịch sử di chuyển đầy đủ và bất biến.
5. Tối thiểu hóa thao tác của người vận hành.
6. Tính toàn vẹn số lượng đáng tin cậy.
7. Khả năng quan sát sản xuất rõ ràng.
8. Khả năng bảo trì lâu dài.

Bất cứ khi nào phải đánh đổi, khả năng sử dụng thực tế tại xưởng sản xuất được ưu tiên hơn sự hoàn hảo trên lý thuyết, với điều kiện độ chính xác của việc theo dõi và tính toàn vẹn dữ liệu được bảo toàn.

Ứng dụng nên cho phép người vận hành ghi nhận hoạt động sản xuất thực tế với càng ít thao tác càng tốt.

---

# 3. Nguyên tắc thiết kế (Design Principles)

## Ưu tiên máy quét (Scanner First)

Quét barcode là tương tác sản xuất chính.

- Máy quét kiểu keyboard-wedge (máy quét gõ giá trị đã quét như thể đó là dữ liệu nhập từ bàn phím) phải hoạt động được mà không cần trình điều khiển (driver) tùy chỉnh.
- Quy trình thông thường không được đòi hỏi thao tác chuột.
- Giao diện cảm ứng nên được tối ưu cho máy tính bảng và các máy trạm cố định.
- Ô nhập liệu quét phải tự động lấy lại tiêu điểm (focus) sau mỗi thao tác hoàn tất.
- Nhập thủ công phải luôn sẵn có như một phương án dự phòng.

---

## Lấy Part Number làm trung tâm (Part Number-Centric)

Part Number, viết tắt là PN, là định danh chính được theo dõi.

Hệ thống không dùng `Work Order + PN`, một lô vật lý hay một chiếc riêng lẻ làm đối tượng theo dõi chính.

Thay vào đó:

- PN định danh định nghĩa chi tiết có thể tái sử dụng.
- Số lượng vật lý thể hiện trạng thái sản xuất.
- Work Order Demand thể hiện nhu cầu nghiệp vụ.
- Part Movement thể hiện hoạt động sản xuất.
- Work Order Allocation kết nối số lượng đã nhập kho với từng Work Order Demand riêng lẻ, và chỉ sau khi hoàn tất.

---

## Theo dõi dựa trên số lượng (Quantity-Based Tracking)

PartFlow theo dõi các số lượng vật lý, không theo dõi từng chiếc được định danh riêng.

Một PN có thể đồng thời có số lượng:

- ở nhiều Area,
- được gán cho nhiều Machine,
- đang chờ trong hàng chờ và đang được xử lý,
- đi theo các route số lượng khác nhau.

Hệ thống không bao giờ được giả định rằng một PN chỉ có một vị trí hiện tại hoặc chỉ có một đường xử lý đang hoạt động.

---

## Hướng theo sản xuất thực tế (Production-Oriented)

Ứng dụng theo dõi cách sản xuất thực sự di chuyển qua xưởng.

Người vận hành không được bị buộc phải thực hiện các bước hành chính không tương ứng với công việc vật lý thực tế.

Dữ liệu sản xuất nên thể hiện thực tế thay vì một quy trình được lý tưởng hóa.

---

## Sản xuất phản ánh thực tế (Production Reflects Reality)

Route, kế hoạch và thời lượng dự kiến (expected duration) đóng vai trò hướng dẫn.

Lịch sử Movement thực tế vẫn là nguồn sự thật (source of truth).

Nếu sản xuất sai lệch (deviation) so với route đã gán:

- hệ thống nên cảnh báo khi thích hợp,
- trường hợp mơ hồ phải đòi hỏi xác nhận,
- các sai lệch được cho phép phải được ghi lại,
- sản xuất thực tế không được bị từ chối chỉ vì nó khác với kế hoạch.

Thực tế thắng kế hoạch ban đầu.

---

## Lịch sử là bất biến (History Is Immutable)

Mọi di chuyển sản xuất có ý nghĩa phải được ghi lại.

Các sửa sai (correction) phải tạo ra các sự kiện bù trừ, đảo ngược hoặc điều chỉnh.

Các quy trình thông thường không bao giờ được âm thầm sửa đổi hoặc xóa các bản ghi Movement trong lịch sử.

---

## Độc lập với ERP (ERP Independent)

PartFlow phải hoạt động được mà không cần kết nối ERP.

Dữ liệu ERP có thể được:

- nhập thủ công,
- nhập từ tệp,
- đồng bộ hóa sau này thông qua một ranh giới tích hợp tách biệt.

Các quy tắc nghiệp vụ của PartFlow không bao giờ được phụ thuộc vào định dạng phản hồi hoặc giao diện lập trình ứng dụng (API) riêng của ERP.

---

## Thực tế trước, hoàn hảo sau (Practical Before Perfect)

Sản xuất thực tế luôn có các ngoại lệ.

Ứng dụng nên giúp người vận hành xử lý an toàn các ngoại lệ hợp lệ thay vì từ chối mọi sai lệch.

Khi hệ thống không thể xác định ý định với đủ độ tin cậy, hệ thống phải yêu cầu xác nhận rõ ràng thay vì phỏng đoán.

Dữ liệu nhập không xác định hoặc mơ hồ không bao giờ được cập nhật dữ liệu theo dõi.

---

# 4. Bối cảnh vận hành (Operational Context)

Mỗi thiết kế chế tạo có thể tái sử dụng được định danh bằng một Part Number duy nhất.

Với mỗi PN, công ty duy trì một bìa hồ sơ (folder) vật lý có thể tái sử dụng, chứa các bản vẽ.

Bìa hồ sơ:

- thuộc về PN, không thuộc về một Work Order cụ thể,
- được tái sử dụng cho tất cả các Work Order yêu cầu PN đó,
- có thể được tái sử dụng qua nhiều đợt sản xuất,
- mang một barcode PN duy nhất,
- có thể chứa các bản vẽ của phiên bản (revision) hiện hành.

Công ty chủ ý không dán nhãn cho từng chi tiết vật lý vì:

- chi tiết có thể bắt đầu ở dạng nguyên vật liệu thô,
- nhãn có thể không còn nguyên sau khi gia công,
- việc dán nhãn lên từng chiếc là không khả thi về mặt vận hành,
- nhân lực không đủ để đáp ứng việc theo dõi từng chiếc riêng lẻ.

Do đó PartFlow theo dõi các số lượng vật lý gắn với một PN.

---

## Bìa hồ sơ bản vẽ dùng chung (Shared Drawing Folder)

Bìa hồ sơ bản vẽ chủ yếu cần đến trong công việc thiết lập (setup) và tra cứu.

Sau khi thiết lập xong:

- các số lượng sản xuất có thể tiếp tục di chuyển một cách độc lập,
- cùng một bìa hồ sơ có thể tạm thời được một Area khác sử dụng,
- bìa hồ sơ có thể bị tách rời về mặt vật lý khỏi một phần hoặc toàn bộ số lượng đang hoạt động.

Vị trí vật lý của bìa hồ sơ không phải là vị trí sản xuất của số lượng PN.

Hệ thống không bao giờ được giả định rằng:

- bìa hồ sơ và tất cả các chi tiết vật lý luôn ở cùng nhau,
- quét bìa hồ sơ có nghĩa là toàn bộ số lượng đang hoạt động đã di chuyển,
- tại một thời điểm chỉ một Area được làm việc trên PN.

Barcode định danh PN. Số lượng và ngữ cảnh di chuyển do ứng dụng xác định.

---

# 5. Quy tắc theo dõi cốt lõi (Core Tracking Rules)

Đối tượng được theo dõi chính là PN.

Bản thân chuỗi PN là định danh sản xuất ổn định. Một PN:

- là duy nhất trong hệ thống ERP,
- không có định dạng nào được bảo đảm ngoài các quy tắc dạng chuẩn (canonical) (không phân biệt chữ hoa chữ thường, được lưu ở dạng CHỮ IN HOA chuẩn, không có khoảng trắng — §7 Part Number),
- ngoài các quy tắc đó, phải được xem như một chuỗi tùy ý, không mang ý nghĩa cấu trúc (opaque),
- có một bìa hồ sơ bản vẽ có thể tái sử dụng,
- có một barcode PartFlow duy nhất,
- có thể được nhiều Work Order đang hoạt động yêu cầu,
- có thể có nhiều Job Number bên ngoài (chỉ dùng để hiển thị, tìm kiếm, sắp xếp và báo cáo),
- có thể đồng thời có số lượng ở nhiều Area,
- có thể đồng thời có số lượng được gán cho nhiều Machine,
- có thể có các Quantity Flow khác nhau đi theo các Route khác nhau,
- phải luôn truy vết được vĩnh viễn sau lần ghi nhận đầu tiên: mọi bản ghi sản xuất (Work Order Demand, Quantity Flow, Part Movement, Allocation, lịch sử) tự lưu giá trị PN chuẩn mà nó cần — khả năng truy vết không bao giờ phụ thuộc vào sự tồn tại của một bản ghi PartNumber master.

Hệ thống không theo dõi:

- từng chiếc riêng lẻ theo số sê-ri,
- một lô vật lý như một định danh hạng nhất (first-class),
- `Work Order + PN` như định danh di chuyển.

Thay vào đó:

- Movement được ghi nhận ở cấp PN + số lượng.
- Work Order Demand được duy trì riêng.
- Số lượng đã nhập kho được phân bổ cho Work Order Demand sau khi hoàn tất.
- Các thay đổi phân bổ không được viết lại lịch sử Movement tại xưởng sản xuất.

---

# 6. Các bất biến nền tảng (Fundamental Invariants)

Các quy tắc sau là bắt buộc:

1. Một PN đã được ghi nhận không bao giờ được trở nên không thể truy vết.
2. Lịch sử Movement là bất biến.
3. Trạng thái sản xuất hiện tại phải được suy ra từ lịch sử Movement bất cứ khi nào khả thi.
4. Một PN có thể đồng thời có số lượng ở nhiều Area.
5. Một PN có thể đồng thời có số lượng được gán cho nhiều Machine.
6. Số lượng không bao giờ được vô tình bị tạo ra, bị hủy, bị nhân đôi, bị thất lạc hoặc trở thành số âm.
7. Không Movement nào được tiêu thụ nhiều số lượng hơn số lượng có sẵn tại vị trí nguồn của nó.
8. Các lần quét không xác định hoặc không hợp lệ bị từ chối; các lần quét mơ hồ không bao giờ được cập nhật dữ liệu sản xuất cho đến khi sự mơ hồ được xác nhận rõ ràng.
9. Work Order Demand và Movement tại xưởng sản xuất là hai khái niệm tách biệt.
10. Work Order Allocation và Movement tại xưởng sản xuất là hai khái niệm tách biệt.
11. Work Order Allocation có thể thay đổi mà không viết lại lịch sử Movement.
12. Route hướng dẫn sản xuất nhưng không thay thế lịch sử Movement thực tế.
13. Các sửa sai phải bảo toàn sự kiện gốc.
14. Các cập nhật do quét phải mang tính nguyên tử (atomic).
15. Sản phẩm khả dụng tối thiểu (MVP) phải hoạt động được mà không cần kết nối ERP.

---

# 7. Từ vựng chuẩn (Canonical Vocabulary)

Các tên sau phải được sử dụng nhất quán trong tài liệu, mã nguồn, API, các đối tượng cơ sở dữ liệu và giao diện người dùng.

## Part Number

Một định danh có thể tái sử dụng, do ERP định nghĩa, cho một thiết kế chi tiết được chế tạo.

Viết tắt: `PN`.

PN là định danh chính được theo dõi, và **bản thân chuỗi PN là định danh miền nghiệp vụ (domain) ổn định** — không có định danh PN thay thế (surrogate), và dữ liệu sản xuất không bao giờ được liên kết thông qua một `part_number_id`.

Một PN:

- là duy nhất,
- giữ nguyên qua nhiều Work Order,
- được thể hiện bằng barcode của bìa hồ sơ có thể tái sử dụng.

Các quy tắc dạng chuẩn:

- Định danh PN **không phân biệt chữ hoa chữ thường**. Một PN được chuẩn hóa thành **CHỮ IN HOA** trước khi được lưu và trước khi được so sánh: `abc-123`, `ABC-123` và `AbC-123` đều được chuẩn hóa thành `ABC-123`. Hệ thống chỉ lưu, so sánh và hiển thị PN ở dạng chữ in hoa chuẩn.
- Việc chuẩn hóa trước tiên **cắt khoảng trắng ở đầu và cuối** (phần thừa bao quanh dữ liệu nhập — dấu cách, ký tự tab, ký tự xuống dòng quanh giá trị). Sau khi cắt, PN phải không rỗng và phải **không chứa khoảng trắng bên trong** dưới bất kỳ hình thức nào (không có dấu cách, ký tự tab, ký tự xuống dòng hay khoảng trắng nào khác bên trong giá trị). Dữ liệu nhập có khoảng trắng bên trong bị từ chối vì là PN không hợp lệ; khoảng trắng bên trong không bao giờ bị âm thầm xóa bỏ để biến dữ liệu nhập không hợp lệ thành một PN hợp lệ.
- Ngoài hai quy tắc này, PN vẫn là một chuỗi tùy ý, không mang ý nghĩa cấu trúc (opaque): PN không có định dạng cố định, các phân đoạn của PN không bao giờ được phân tích để rút ra ý nghĩa nghiệp vụ, và không bao giờ có định dạng nào được giả định.

Dữ liệu master `PartNumber` là **metadata hiện hành tùy chọn** của một PN (§8.1). Định danh sản xuất và sự thật lịch sử không bao giờ phụ thuộc vào sự tồn tại của một bản ghi PN master.

---

## Work Order

Một đơn vị chứa công việc sản xuất có nguồn gốc bên ngoài, gồm một hoặc nhiều Part Number được yêu cầu cùng các số lượng vật lý mà PartFlow chịu trách nhiệm theo dõi.

Viết tắt: `WO`. Chữ viết tắt này chỉ thích hợp trong các nhãn thu gọn hiển thị cho người dùng, tiêu đề bảng và văn bản mà ở đó thuật ngữ Work Order đã được giới thiệu trước; tên trong mã nguồn vẫn là dạng đầy đủ `WorkOrder`, `WorkOrderDemand` và `WorkOrderAllocation`.

Một Work Order Number:

- có nguồn gốc bên ngoài hoặc được nhập thủ công,
- không có định dạng cố định,
- phải được xem như một chuỗi tùy ý, không mang ý nghĩa cấu trúc (opaque),
- là định danh chuẩn của đơn vị chứa nghiệp vụ mà PartFlow sử dụng,
- không bao giờ bị định dạng lại, đệm thêm ký tự hoặc chuẩn hóa sau khi đã được nhập.

Work Order Number bên ngoài **có thể để trống (nullable)**. Người dùng có thể tạo demand mà không biết Work Order Number bên ngoài: khi Work Order Number được để trống và người dùng xác nhận rõ ràng việc lưu, PartFlow lưu `work_order_number = NULL` trên một Work Order nội bộ. Một Work Order không có số bên ngoài:

- được hiển thị là `—` ở bất cứ đâu Work Order Number được trình bày (ký hiệu giữ chỗ `—` chỉ dùng để hiển thị và **không bao giờ được lưu**),
- được gắn nhãn rõ ràng là Work Order nội bộ không có số bên ngoài,
- sau này có thể nhận Work Order Number bên ngoài thực sự thông qua một lần chỉnh sửa được ghi audit,
- giữ định danh nội bộ ổn định của riêng nó (một khóa cơ sở dữ liệu không bao giờ là định danh hiển thị cho người dùng).

Không bao giờ sinh ra Work Order Number tạm thời (chẳng hạn `TMP-YYYYMMDD-HHMMSS`). Nhiều Work Order có thể đồng thời có số bên ngoài là null; tính duy nhất chỉ áp dụng cho các Work Order Number khác null. Không bao giờ dùng định danh duy nhất toàn cục (UUID) hay bất kỳ định danh máy nào khác làm định danh Work Order hiển thị cho người dùng. Các Work Order Number do người dùng nhập vẫn là chuỗi tùy ý, không mang ý nghĩa cấu trúc và không bao giờ bị định dạng lại.

Work Order Number và Job Number bên ngoài là hai định danh tách biệt (ví dụ Work Order Number `007125` so với Job Number bên ngoài `17555`). Job Number vẫn chỉ là metadata mang tính thông tin trên Work Order Demand: dùng được để hiển thị, tìm kiếm, sắp xếp và báo cáo, nhưng không bao giờ là định danh nội bộ, định danh di chuyển hay khóa quy trình.

---

## Work Order Demand

Số lượng vật lý được yêu cầu của một PN cho một Work Order.

Work Order Demand chứa ngữ cảnh nghiệp vụ cần thiết để trả lời:

- Work Order nào yêu cầu PN,
- số lượng yêu cầu,
- số lượng đã phân bổ,
- số lượng còn thiếu,
- ngày đến hạn (due date),
- Priority,
- Request Type,
- các Job Number bên ngoài (chỉ dùng để hiển thị, tìm kiếm, sắp xếp và báo cáo).

Work Order Demand không xác định vị trí sản xuất hiện tại.

---

## Request Type

Mô tả lý do một Work Order Demand hoặc một demand nội bộ tồn tại.

Các giá trị ban đầu:

- `NEW`
- `MODIFY`

`NEW` là sản xuất được yêu cầu từ bên ngoài. `MODIFY` có nghĩa là số lượng vật lý của một PN được đưa vào để thực hiện công việc sửa đổi — một khái niệm nội bộ của PartFlow, không cần tồn tại trong ERP. Một lần tiếp nhận (intake) MODIFY có thể không có Work Order Number bên ngoài, không có Route định trước và không có Work Order Demand đang hoạt động nào tồn tại từ trước; PartFlow vẫn tạo các bản ghi nội bộ tối thiểu cần thiết cho tính toàn vẹn số lượng và khả năng truy vết (một WorkOrder nội bộ với số là null, một WorkOrderDemand cho PN — hoặc, khi tái sử dụng một Work Order nội bộ, dòng hiện có của nó được tăng lên (§14) — một QuantityFlow và lịch sử Movement bất biến).

**Repair không phải là một Request Type.** `REPAIR` là một ý định di chuyển sản xuất được nêu rõ (một `movement_reason` trên một lần chuyển, §8.11): một phần hoặc toàn bộ số lượng đã ở trong sản xuất quay lại một Area đã đi qua trước đó để sửa công việc đã thực hiện trước đây. Repair không tạo ra số lượng vật lý mới và không tạo ra nhu cầu nghiệp vụ mới.

---

## Department

Một đơn vị tổ chức lớn.

Ví dụ:

- Machine Shop
- Purchasing
- Assembly
- Production
- Stockroom
- Quality Control

Bản phát hành đầu tiên nhắm tới Machine Shop.

---

## Area

Một địa điểm sản xuất vật lý nơi số lượng được tiếp nhận, xếp vào hàng chờ, xử lý, chuyển đi, gửi gia công bên ngoài hoặc nhập kho.

Ví dụ:

- Material
- Cut
- Lathe
- Mill
- Manual
- Deburr
- External
- Stockroom

Một Area có định danh ổn định.

Các thuộc tính hiển thị như tên, mô tả, màu sắc và biểu tượng có thể thay đổi mà không làm thay đổi định danh lịch sử.

---

## Operation

Công việc được thực hiện trong một Area.

Ví dụ:

- Cutting
- Turning
- Milling
- Deburring
- Plating
- Painting
- Testing
- Receiving

Một Area có thể hỗ trợ một hoặc nhiều Operation.

Ví dụ:

- Area: `External`
- Operations: `Plating`, `Painting`, `Testing`

Operation mô tả công việc. Area mô tả địa điểm vật lý.

---

## Machine

Một tài nguyên sản xuất vật lý bên trong một Area.

Machine là tùy chọn.

Một Machine:

- thuộc về đúng một Area,
- có một **Asset Tag** bắt buộc — định danh ổn định, con người đọc được, của máy vật lý, được PartFlow tự động gán khi Machine được tạo (§8.6),
- có barcode riêng, chính là Asset Tag trong không gian tên (namespace) `PF:MACHINE:` (`PF:MACHINE:<asset-tag>`, §10) — không bao giờ là một giá trị độc lập được nhập thủ công,
- trở thành đơn vị thực thi hiện tại khi số lượng được gán cho nó,
- đồng thời xác định vị trí xử lý vật lý trong khi số lượng được gán cho nó.

Một Machine có vòng đời: Machine ở trạng thái hoạt động cho đến khi bị **cho ngừng sử dụng (retire)**. Một Machine đã từng được lịch sử Movement tham chiếu thì không bao giờ bị xóa vĩnh viễn (hard delete) — thay vào đó Machine được cho ngừng sử dụng, vẫn hiển thị cho mục đích hiển thị lịch sử và báo cáo, không bao giờ xuất hiện trong các lựa chọn gán và không chấp nhận lần quét mới nào.

Một Machine đã ngừng sử dụng sau này có thể **quay lại hoạt động** — nhưng chỉ với tư cách là **cùng một máy vật lý** trên **cùng một bản ghi**: việc kích hoạt lại (reactivate) giữ nguyên định danh ổn định, barcode, metadata tài sản và toàn bộ lịch sử, đồng thời xóa ngày ngừng sử dụng. Việc cho ngừng sử dụng và kích hoạt lại được ghi lại dưới dạng các sự kiện audit vòng đời chỉ ghi thêm (append-only) (§8.6). Một máy vật lý **khác** luôn là một bản ghi mới, không bao giờ là một lần kích hoạt lại.

Một Machine giữ một định danh nội bộ ổn định; tên hiển thị cho người vận hành là một thuộc tính riêng và có thể được dùng lại **theo thời gian và qua các lần thay thế** — nhưng tên hiển thị phải **là duy nhất trong số các Machine đang hoạt động của một Area**. Thay thế một Machine vật lý có nghĩa là cho bản ghi cũ ngừng sử dụng và tạo một bản ghi **mới** với định danh ổn định riêng và Asset Tag mới riêng (và do đó có barcode mới). Machine mới có thể dùng lại tên hiển thị quen thuộc gắn với vị trí trên sàn xưởng (ví dụ `Lathe 1`); bản ghi cũ không bao giờ bị đổi tên hay sửa đổi, và hai bản ghi vẫn phân biệt được thông qua Asset Tag và định danh nội bộ. Không có lớp trừu tượng MachineSlot hay WorkCenter — chỉ riêng tên hiển thị mang sự quen thuộc gắn với vị trí trên sàn xưởng.

---

## Quantity Flow — khái niệm theo dõi nội bộ (Quantity Flow (Internal Tracking Concept))

Một phần sản xuất có thể truy vết của số lượng PN khi số lượng đó di chuyển, tách, gộp, xếp vào hàng chờ và được gán cho các Area hoặc Machine.

Quantity Flow không đại diện cho các chiếc được dán nhãn riêng.

Quantity Flow là định danh logic cần thiết để bảo toàn:

- phân bố số lượng hiện tại,
- việc gán route,
- lịch sử tách và gộp,
- tiến trình qua các Area và Machine.

Các Quantity Flow khác nhau của cùng một PN có thể đi theo các Route khác nhau.

---

## Route và Route Mode (Route and Route Mode)

Đường đi sản xuất có thứ tự được dự kiến cho một Quantity Flow.

Mỗi Quantity Flow có một **route mode**:

- `FLOATING` (**mặc định**) — không yêu cầu trình tự định trước. Route thực tế được suy ra từ lịch sử Movement bất biến: mỗi Area đã đi qua kéo dài vết route quan sát được, các Area lặp lại được giữ nguyên, các Quantity Flow khác nhau của cùng một PN có thể có các vết khác nhau, và các flow đã tách tiếp tục một cách độc lập. Một Floating Route không có ảnh chụp (snapshot) AssignedRoute.
- `PLANNED` — flow mang một snapshot AssignedRoute được sao chép từ một Route Template để làm hướng dẫn.

Một Planned Route có thể chứa:

- các Area,
- các Operation,
- thời lượng dự kiến,
- hướng dẫn,
- các Machine được ưu tiên.

Route là hướng dẫn. Lịch sử Movement thực tế vẫn có thẩm quyền trong cả hai chế độ, và không được có một lịch sử route thứ hai có thể sửa đổi nào trùng lặp PartMovement.

---

## Part Movement

Một sự kiện bất biến ghi lại việc di chuyển, gán, hoàn tất, sửa sai, tách hoặc gộp số lượng PN.

---

## Area Completion (DONE)

Việc hoàn tất xử lý một cách rõ ràng tại Area hiện tại đối với một **số lượng vật lý được chọn**:

- hành động/trạng thái hiển thị cho người dùng: `DONE`;
- loại Movement bất biến chuẩn: `AREA_COMPLETED`;
- trạng thái lưu giữ nội bộ được suy ra: `READY_TO_TRANSFER`.

`DONE` có nghĩa là: số lượng vật lý được chọn đã hoàn tất xử lý tại Area hiện tại và sẵn sàng được chuyển sang một Area khác — số lượng đó chờ trên giá thành phẩm của Area. Machine hiện tại được gỡ khỏi vị trí kết quả; Area hiện tại vẫn là vị trí vật lý cho đến khi số lượng được chuyển đi.

`DONE` không bao giờ có nghĩa là: PN hoàn tất, toàn bộ số lượng của PN hoàn tất, Work Order hoàn tất, hoàn tất chế tạo, số lượng đã nhập kho, phê duyệt kiểm soát chất lượng (QC) hay phân bổ cho Work Order Demand. Hoàn tất chế tạo vẫn chỉ được thể hiện bằng `STOCKED` tại Stockroom cuối cùng.

DONE có phạm vi theo số lượng, không bao giờ là một trạng thái PartNumber toàn cục: DONE áp dụng cho một số lượng vật lý được chọn của một Quantity Flow/vị trí hiện tại cụ thể. Cùng một PN có thể đồng thời có số lượng đang chờ, đang ở trên Machine, đang được xử lý trực tiếp, đã xong và sẵn sàng chuyển, đang ở các Area khác, đã nhập kho và đã bị Scrap. Không có `PartNumber.status = DONE`, và trạng thái vẫn có thể được tái dựng từ lịch sử Movement bất biến.

---

## Current Position

Area hiện tại được suy ra và Machine tùy chọn đang giữ một Quantity Flow.

---

## Work Order Allocation

Việc gán số lượng PN đã nhập kho cho các bản ghi Work Order Demand cụ thể.

Work Order Allocation độc lập với Part Movement.

---

## Priority

Một thứ hạng do người quản lý (manager) xác định, được lưu trên Work Order Demand.

Priority là tiêu chí cao nhất cho việc phân bổ và sắp xếp thứ tự công việc.

---

## Hot Part

Một nhãn giao diện người dùng (UI) và nghiệp vụ cho biết Work Order Demand cần được đẩy nhanh.

`Hot Part` không được thay thế `priority_rank` nền tảng.

---

## Worker

Người vận hành thực hiện hoặc xác nhận hoạt động sản xuất.

Định danh Worker tồn tại để phục vụ trách nhiệm giải trình và báo cáo.

Tính đúng đắn của sản xuất không được phụ thuộc vào định danh Worker.

Một Worker chỉ tồn tại trong phạm vi Scan Station và là một khái niệm khác với User: **User** là tài khoản ứng dụng dùng cho Management, Administration và các giao diện khác không thuộc Scan Station; **Worker** là định danh audit của người đang vận hành một Scan Station. Thẻ (badge) của Worker không bao giờ đăng nhập vào PartFlow, và một Worker Session không bao giờ là một phiên xác thực. Worker và User không bao giờ được gộp thành một thực thể.

---

# 8. Mô hình miền (Domain Model)

## 8.1 PartNumber

Đại diện cho **siêu dữ liệu hiện hành, không bắt buộc** của một Part Number (PN). Bản thân chuỗi PN chuẩn (canonical) là định danh sản xuất ổn định (§7 Part Number); PartNumber master chỉ bổ sung siêu dữ liệu hiện hành cho định danh đó khi bản ghi tồn tại.

```text
PN string        = stable production identity
PartNumber master = optional/current metadata for that PN
```

Các thuộc tính điển hình (chỉ mang tính minh họa):

- `part_number` (PN chuẩn viết bằng chữ in hoa — là định danh và khóa tự nhiên)
- `name`
- `description`
- `image_url`
- `current_revision`
- `erp_id`
- `created_at`
- `updated_at`

Quy tắc:

- `part_number` được lưu ở dạng chuẩn (§7 Part Number): được chuẩn hóa thành CHỮ IN HOA, không chứa khoảng trắng, và ngoài ra là một chuỗi tùy ý, không mang ý nghĩa cấu trúc (opaque). Tính duy nhất suy ra trực tiếp từ dạng chuẩn — mỗi PN chuẩn chỉ có thể có một bản ghi master.
- Một PartNumber master **được tạo ở lần sử dụng hợp lệ đầu tiên**: không cần một danh mục PN có thẩm quyền được nạp sẵn, hệ thống hoạch định nguồn lực doanh nghiệp (ERP) không bao giờ được gọi trong giai đoạn sản phẩm khả dụng tối thiểu (MVP), và việc nhập PN thủ công chấp nhận mọi giá trị PN không rỗng sau khi cắt khoảng trắng ở đầu và cuối và không chứa khoảng trắng bên trong (được chuẩn hóa thành chữ in hoa). Bản ghi master chỉ tồn tại để chứa siêu dữ liệu hiện hành và phục vụ việc ánh xạ ERP trong tương lai.
- Định danh sản xuất và sự thật lịch sử **không bao giờ phụ thuộc vào việc bản ghi PartNumber master có tồn tại hay không**. Demand của WorkOrder, các QuantityFlow, PartMovement, Allocation và lịch sử đều tự giữ giá trị PN chuẩn mà chúng cần; việc hiển thị lịch sử không bao giờ cần join qua PN master để biết PN là gì. Việc tra cứu PN master chỉ bổ sung siêu dữ liệu hiện hành khi bản ghi tồn tại.
- Giá trị PN thực tế thường là các chuỗi số gồm nhiều đoạn nối với nhau bằng dấu gạch ngang, có độ dài khác nhau (các dạng như `214-406`, `78-04-0031`, `0455-20-0118-03`, `2027-60-8114-00`). Chuỗi chuẩn phải được giữ nguyên ở mọi nơi; các đoạn của PN không bao giờ được phân tích để rút ra ý nghĩa nghiệp vụ.
- `name`/`description` là văn bản tự do, giữ đúng như khi được cung cấp — thường viết bằng chữ in hoa kèm dấu phẩy, dấu gạch chéo, phân số, kích thước và các chữ viết tắt trong gia công (ví dụ `VALVE, SOLENOID VITON, 3/8`) — và có thể đủ dài để chiếm nhiều dòng hiển thị.
- Barcode (mã vạch) trên bìa hồ sơ chỉ định danh PN và mang chính PN đó: `PF:PN:<part-number>` với PN chuẩn viết bằng chữ in hoa (§10).
- Siêu dữ liệu PartNumber master là dữ liệu master sản xuất, được các vai trò sản xuất được ủy quyền (§20) duy trì trong **Management → Part Numbers** (§21), giống như Machines và Planned Routes. Người dùng được ủy quyền có thể **xóa vĩnh viễn (hard delete)** một bản ghi PartNumber master tại đó (§28 Administrative Archival and Purge): việc xóa chỉ loại bỏ siêu dữ liệu, không bao giờ lan truyền (cascade) sang dữ liệu sản xuất và không bao giờ ảnh hưởng đến khả năng truy vết của PN — các màn hình lịch sử tiếp tục hiển thị chuỗi PN một cách bình thường. Nếu PN được dùng lại sau này, có thể tạo một bản ghi master mới cho cùng định danh PN chuẩn đó. Không có bản lưu trữ (archive) hay bản ghi đánh dấu đã xóa (tombstone) nào của PN được giữ lại chỉ để bảo toàn định danh PN.
- Phiên bản thiết kế hiện hành (current revision) chỉ mang tính thông tin.
- Việc thay đổi phiên bản thiết kế (revision) không tạo ra một PN được theo dõi mới, trừ khi ERP cung cấp một PN khác.
- Barcode PN được dùng lại cho mọi Work Order yêu cầu PN đó.

---

## 8.2 WorkOrder

Đại diện cho một Work Order mà doanh nghiệp nhận được.

Các thuộc tính điển hình (chỉ mang tính minh họa):

- `id`
- `work_order_number`
- `received_date`
- `due_date`
- `status`
- `completed_at`
- `erp_id`
- `created_at`
- `updated_at`

Quy tắc:

- `work_order_number` phải được xem là một chuỗi tùy ý.
- `work_order_number` **có thể null (nullable)** (§7 Work Order). Số null là dữ liệu hợp lệ trên một Work Order nội bộ, được hiển thị là `—` (không bao giờ được lưu dưới dạng một giá trị giữ chỗ), và có thể được thay bằng số thực từ bên ngoài thông qua một lần chỉnh sửa được ghi audit. Cơ sở dữ liệu cho phép nhiều Work Order có số null, đồng thời vẫn bảo đảm tính duy nhất cho các số khác null (chỉ mục duy nhất một phần — partial unique index).
- `received_date` là bắt buộc và mặc định là ngày hiện tại theo lịch của địa điểm (`SITE_TIMEZONE`) khi không có giá trị nào được nhập — khi tạo thủ công và đối với một Work Order được tạo bằng cách nhập file.
- `due_date` có thể null. Việc thiếu ngày đến hạn (due date) của Work Order là dữ liệu hợp lệ, không phải lỗi kiểm tra hợp lệ; ngày này có thể được bổ sung sau. Ngày đến hạn của Work Order được dùng làm giá trị mặc định khi nhập ngày đến hạn cho các dòng demand.
- Một Work Order chứa một hoặc nhiều bản ghi Work Order Demand.
- Một Work Order hoàn tất khi mọi Work Order Demand của nó đã được phân bổ đầy đủ. Trạng thái hoàn tất là **trạng thái được suy ra**: các bản ghi phân bổ (allocation) vẫn là nguồn sự thật.
- `completed_at` ghi lại thời điểm hoàn tất (**ngày hoàn tất (done date)**). Giá trị này được đặt bằng dấu thời gian của sự kiện đã khiến Work Order Demand cuối cùng còn mở trở thành được phân bổ đầy đủ. Sự kiện đó là một trong hai trường hợp sau:
  - sự kiện phân bổ đã phân bổ đầy đủ dòng đó, hoặc
  - một thay đổi demand khiến mọi Work Order Demand còn lại đều được phân bổ đầy đủ — một lần lưu hạ số lượng yêu cầu xuống bằng số lượng đã phân bổ của dòng đó (§13), hoặc việc xóa dòng cuối cùng chưa được phân bổ đầy đủ. Một thay đổi như vậy hoàn tất Work Order trong chính transaction của nó, và được ghi audit với thay đổi đó là nguyên nhân.

  `completed_at` tồn tại để lịch sử hoàn tất vĩnh viễn có thể được sắp xếp, lọc và phân trang theo ngày hoàn tất một cách hiệu quả (cột này được đánh chỉ mục, và việc phân trang dùng keyset pagination trên `(completed_at, id)`). Giá trị này không bao giờ được nhập bằng tay.
- Một lần điều chỉnh phân bổ được ghi audit diễn ra sau đó (§18) có thể khiến Work Order trở lại trạng thái chưa hoàn tất: khi đó `completed_at` bị xóa (và được đặt lại bởi một lần phân bổ hoàn tất sau đó). Work Order quay lại các màn hình đang hoạt động; không có gì bị xóa và nhật ký audit phân bổ giữ lại toàn bộ lịch sử.
- Các Work Order đã hoàn tất rời khỏi các màn hình đang hoạt động nhưng vẫn luôn có trong lịch sử — chúng được trình bày trên một màn hình Completed Work Orders riêng, ở chế độ chỉ đọc đối với demand, nhập liệu và phát hành (người dùng có quyền Edit Work Order Allocation vẫn giữ được việc điều chỉnh phân bổ của §8.12 / §18, và việc này có thể mở lại Work Order), được sắp xếp và lọc mặc định theo ngày hoàn tất, và có thể tìm kiếm theo Work Order Number, PN và Job Number (§21 Work Orders). Vì lịch sử hoàn tất được lưu giữ vĩnh viễn, màn hình đó phải coi danh sách là không giới hạn: việc tìm kiếm, lọc, sắp xếp và phân trang được thực hiện phía máy chủ.
- Tính duy nhất của Work Order Number (và quy tắc không bao giờ trùng lặp, §13) áp dụng trên toàn bộ lịch sử, kể cả các Work Order đã hoàn tất.

---

## 8.3 WorkOrderDemand

Đại diện cho số chiếc vật lý của một PN mà một Work Order yêu cầu.

Các thuộc tính điển hình (chỉ mang tính minh họa):

- `id`
- `work_order_id`
- `part_number` (PN chuẩn viết bằng chữ in hoa — do chính demand giữ)
- `request_type`
- `requested_quantity`
- `allocated_quantity`
- `due_date`
- `priority_rank`
- `job_numbers`
- `requester`
- `reason`
- `notes`
- `created_at`
- `updated_at`

Quy tắc:

- Một PN có thể có nhiều bản ghi Work Order Demand đang hoạt động.
- Số lượng yêu cầu biểu thị số chiếc vật lý.
- `due_date` có thể null. Việc thiếu ngày đến hạn là dữ liệu hợp lệ, không phải lỗi kiểm tra hợp lệ, và không bao giờ chặn việc lưu. Demand không có ngày đến hạn được xếp sau toàn bộ demand có ngày đến hạn (§18 Allocation Order).
- Bản thân PN không sở hữu ngày đến hạn. Một PN được hiển thị mà không có ngày đến hạn nghĩa là Work Order Demand liên quan không có ngày đến hạn.
- Work Order Demand có thể được Admin hoặc Manager chỉnh sửa.
- Khi số lượng sản xuất đã được phát hành cho một Work Order Demand, chỉ còn lại việc chỉnh sửa hạn chế của §13 *Restricted edit after release*: số lượng yêu cầu (không bao giờ thấp hơn số lượng đã phát hành hoặc đã phân bổ), ngày đến hạn và Job Numbers. Dòng đó không còn có thể bị xóa (§13 *Work Order Demand Removal*).
- Priority thuộc về Work Order Demand.
- Work Order Demand không sở hữu Movement trên xưởng sản xuất.
- Work Order Demand không xác định vị trí hiện tại của PN.
- Phân bổ có thể được Admin hoặc Manager điều chỉnh bất cứ lúc nào.

---

## 8.4 Area

Đại diện cho một vị trí vật lý trên xưởng sản xuất.

Các thuộc tính điển hình (chỉ mang tính minh họa):

- `id`
- `department_id`
- `barcode_value`
- `name`
- `description`
- `color`
- `icon_url`
- `is_terminal`
- `is_active`
- `worker_identification_mode`
- `fixed_worker_id` (Fixed Worker, có giá trị khi và chỉ khi Area ở chế độ fixed-Worker, §19)
- `created_at`
- `updated_at`

Quy tắc:

- Định danh và barcode của Area phải luôn ổn định.
- Tên hiển thị của Area có thể thay đổi.
- Một Area có thể chứa không, một hoặc nhiều Machine.
- Một Area có thể hỗ trợ một hoặc nhiều Operation.
- Stockroom thường là một Area cuối (terminal).
- Hành vi gán được suy ra từ các Machine của Area (§12): một Area không có Machine thì xử lý trực tiếp; một Area có Machine — một hay nhiều — luôn dùng `QUEUE_AND_ASSIGN`. Không có cấu hình gán máy theo từng Area và không có việc tự động gán cho các Area chỉ có một Machine.

---

## 8.5 Operation

Đại diện cho một loại công việc mà một Area hỗ trợ.

Các thuộc tính điển hình (chỉ mang tính minh họa):

- `id`
- `area_id`
- `code`
- `name`
- `description`
- `default_expected_duration`
- `is_external`
- `is_active`

Quy tắc:

- Operation không cần barcode khi nó được xác định một cách không mơ hồ từ cấu hình của Area.
- Nếu một Area hỗ trợ nhiều Operation, quy trình phải xác định hoặc xác nhận Operation nào được áp dụng.

---

## 8.6 Machine

Đại diện cho một Machine vật lý hoặc một trạm xử lý.

Các thuộc tính điển hình (chỉ mang tính minh họa):

- `id`
- `area_id`
- `name`
- `asset_tag` (bắt buộc; được gán tự động khi tạo, bất biến, không thể sửa)
- `barcode_value` (luôn bằng `asset_tag` — barcode được suy ra, không bao giờ được nhập)
- `description`
- `manufacturer`
- `model`
- `serial_number`
- `installed_on`
- `notes`
- `maintenance` (ghi đè tường minh: dấu thời gian bắt đầu, ghi chú tùy chọn, ngày dự kiến quay lại tùy chọn)
- `state_changed_at`
- `retired_on`

**Asset Tag** là bắt buộc trên mọi Machine: đó là định danh ổn định, con người đọc được, của tài sản vật lý — nó định danh máy vật lý ngay cả khi tên hiển thị được dùng lại qua các lần thay thế — và nó đồng thời là barcode của Machine (`PF:MACHINE:<asset-tag>`, §10). Các siêu dữ liệu tài sản khác ngoài Asset Tag (nhà sản xuất, model, số sê-ri, ngày lắp đặt, ghi chú) là không bắt buộc — việc theo dõi sản xuất không bao giờ phụ thuộc vào chúng.

Quy tắc về Asset Tag:

- PartFlow tự động gán Asset Tag khi một Machine được tạo; Asset Tag không bao giờ được nhập hay sửa bằng tay.
- Định dạng được cấu hình trong Administration → Barcode configuration và được cố ý giữ đơn giản: một tiền tố cộng với một dãy số được đệm số 0 ở đầu (ví dụ tiền tố `CD-` với 4 chữ số → `CD-0001`, `CD-0002`, …). Không có engine định dạng hay mẫu (template) tổng quát nào.
- Asset Tag là duy nhất và **không bao giờ được dùng lại** — một Machine đã ngừng sử dụng (retired) giữ Asset Tag của nó mãi mãi, và một Machine thay thế luôn nhận một Asset Tag mới.
- Asset Tag không bao giờ thay đổi sau khi Machine được tạo. Việc thay đổi định dạng chỉ áp dụng cho các Machine được tạo sau đó; các Asset Tag hiện có không bao giờ bị đổi tên hay tạo lại.
- Asset Tag là định danh con người đọc được của Machine vật lý — nó không phải là định danh nội bộ trong cơ sở dữ liệu (`id`).

Quy tắc:

- Mọi barcode của Machine phải là duy nhất. Barcode chính là Asset Tag trong không gian tên `PF:MACHINE:` — không có định danh barcode Machine độc lập nào, và barcode không bao giờ được nhập thủ công.
- Một Machine thuộc về đúng một Area. Area của một Machine **đang hoạt động** hiện có là cố định — việc chuyển năng lực sản xuất sang một Area khác là một lần thay thế (cho ngừng sử dụng + tạo bản ghi mới), không bao giờ là một lần chỉnh sửa có thể khiến lịch sử trở nên mơ hồ. Ngoại lệ duy nhất là việc kích hoạt lại cùng một máy vật lý đã được di chuyển vật lý trong thời gian bị ngừng sử dụng (xem bên dưới) — một thay đổi Area chỉ có hiệu lực về sau và không bao giờ đụng đến lịch sử.
- Tên hiển thị phải là **duy nhất trong số các Machine đang hoạt động của một Area**. Việc dùng lại tên theo thời gian và qua các lần thay thế vẫn được phép — quy tắc duy nhất chỉ ràng buộc các Machine đang hoạt động đồng thời trong cùng một Area.
- Việc gán Machine xác định đơn vị đang thực thi công việc hiện tại.
- Một Area không có Machine hoàn toàn không liên quan đến việc gán Machine.
- Hành vi gán được suy ra từ các Machine của Area (§12): khi có Machine, số lượng vào hàng chờ và được gán thông qua quy trình một lần (one-shot) tường minh (nhãn trên giao diện người dùng (UI): `Assign to Machine`) — không bao giờ theo cấu hình và không bao giờ theo số lượng Machine.
- Trạng thái vận hành là **được suy ra**, không bao giờ do người dùng chọn: (1) một ghi đè Maintenance tường minh được ưu tiên; (2) nếu không có, việc có số lượng đang hoạt động được gán nghĩa là Running; (3) nếu không, Machine ở trạng thái Idle.
- Dấu thời gian `state_changed_at` ghi lại thời điểm trạng thái vận hành thay đổi gần nhất; mọi màn hình đều suy ra từ nó khoảng thời gian đã trôi qua trong trạng thái để hiển thị (các định dạng gọn như `18m`, `1h 24m`, `2d 03h`, `<1m`). Không có thời lượng đã định dạng nào được lưu.
- Maintenance có thể bắt đầu trong khi vẫn còn số lượng được gán. Việc bắt đầu bảo trì không bao giờ di chuyển, giải phóng, hoàn tất hay chuyển số lượng và không bao giờ viết lại lịch sử; nó có thể kèm một ghi chú tùy chọn và một ngày dự kiến quay lại tùy chọn. Trong khi bảo trì đang diễn ra, ghi chú và ngày dự kiến quay lại có thể được cập nhật tại chỗ — mà không thay đổi thời điểm bắt đầu bảo trì, trạng thái hay lịch sử. Việc kết thúc bảo trì đưa Machine về Running khi vẫn còn số lượng được gán, nếu không thì về Idle.
- Việc cho ngừng sử dụng bị chặn trong khi đang có số lượng hoạt động được gán — số lượng đó trước tiên phải được hoàn tất hoặc được chuyển đi qua quy trình sản xuất thông thường. Một Machine từng được tham chiếu bởi lịch sử Movement không bao giờ bị xóa vĩnh viễn; nó được cho ngừng sử dụng (cách diễn đạt dành cho operator: `Retired`, `Retire Machine`, `Retired on …`) và chỉ còn được dùng cho việc hiển thị lịch sử và báo cáo.
- Một Machine đã ngừng sử dụng không bao giờ xuất hiện trong các lựa chọn gán và không nhận lần quét mới nào.
- Một Machine đã ngừng sử dụng có thể **quay lại phục vụ (kích hoạt lại (reactivate): RETIRED → ACTIVE)** — **chỉ đối với cùng một máy vật lý**, trên **cùng một bản ghi**: định danh, barcode, siêu dữ liệu tài sản và lịch sử không thay đổi, và `retired_on` được xóa. Trong khi kích hoạt lại, có thể chọn một Area đang hoạt động mới khi máy vật lý đã được di chuyển trong thời gian bị ngừng sử dụng; thay đổi này chỉ có hiệu lực về sau — các Movement trong lịch sử giữ nguyên các Area đã được ghi nhận. Machine được kích hoạt lại quay về trạng thái Idle (trạng thái vận hành vẫn được suy ra — chính số lượng được gán, chứ không phải việc kích hoạt lại, mới khiến nó chuyển sang Running).
- Việc kích hoạt lại bị chặn khi Asset Tag (cũng chính là barcode) hoặc số sê-ri trong thời gian đó đã được cấp lại cho một Machine đang hoạt động khác — với Asset Tag không bao giờ được dùng lại, điều này cho thấy một vấn đề dữ liệu phải được giải quyết trước — khi Area đích không hoạt động và không có Area thay thế nào được chọn, hoặc khi tên hiển thị sẽ trùng với một Machine đang hoạt động của Area đích.
- Việc cho ngừng sử dụng và việc kích hoạt lại được ghi thành **các sự kiện audit vòng đời chỉ ghi thêm (append-only)** (`RETIRED`, `REACTIVATED`): ai, khi nào, lý do, trạng thái trước và sau, cùng Area trước đó và Area hiện tại khi Machine đã được di chuyển.
- Việc thay thế tuân theo §7 Machine: cho bản ghi cũ ngừng sử dụng, tạo một bản ghi mới với định danh ổn định riêng và Asset Tag (cùng barcode) mới riêng; tên hiển thị có thể được dùng lại, và bản ghi cũ không bao giờ bị sửa đổi. Một **máy vật lý khác luôn là một bản ghi mới** — không bao giờ là một lần kích hoạt lại.

---

## 8.7 QuantityFlow

Đại diện cho một khái niệm theo dõi nội bộ dành cho số lượng PN đang hoạt động.

Các thuộc tính điển hình (chỉ mang tính minh họa):

- `id`
- `part_number` (PN chuẩn viết bằng chữ in hoa — do chính flow giữ)
- `quantity`
- `status`
- `route_mode`
- `assigned_route_id` (có thể null)
- `parent_flow_id`
- `created_at`
- `closed_at`

Quy tắc:

- Quantity Flow là cấu trúc theo dõi nội bộ, không phải một lô vật lý có dán nhãn.
- Một Quantity Flow có thể tách thành các flow con.
- Nhiều Quantity Flow của cùng một PN có thể gộp lại với nhau.
- Việc tách và gộp phải bảo toàn đầy đủ lịch sử.
- `route_mode` là `FLOATING` (mặc định) hoặc `PLANNED` (§7 Route and Route Mode); `assigned_route_id` chỉ được đặt cho các flow `PLANNED` — một flow Floating không có AssignedRoute và vết tuyến (route trace) của nó được suy ra từ lịch sử Movement.
- Current Position phải được suy ra từ lịch sử Movement.
- Quantity Flow không được phơi bày cho operator như một sự phức tạp quản trị không cần thiết.

---

## 8.8 RouteTemplate

Đại diện cho một định nghĩa tuyến sản xuất có thể dùng lại. Tên hiển thị cho người dùng là **Planned Routes** (được quản lý trong Management → Planned Routes, §21) — được phân biệt rõ với các vết tuyến thực tế Floating được suy ra từ lịch sử Movement; tên miền nội bộ vẫn là `RouteTemplate`.

Các thuộc tính điển hình (chỉ mang tính minh họa):

- `id`
- `name`
- `description`
- `archived_at`

Một Route Template chứa các Route Step có thứ tự.

Một Route có thể đi qua cùng một Area nhiều lần.

Việc thay đổi một Route Template không bao giờ được làm thay đổi hồi tố các Route đã được gán đang hoạt động — các chỉnh sửa chỉ áp dụng cho các lần gán trong tương lai; các Quantity Flow đã được phát hành giữ nguyên bản chụp (snapshot) Assigned Route của chúng, và một Assigned Route đang trong sản xuất được thay đổi riêng trong quy trình có ghi audit của chính nó, kèm theo lý do (§8.10).

Quy tắc vòng đời:

- Một Route Template chưa từng được tham chiếu bởi một Quantity Flow đã phát hành có thể bị xóa hẳn.
- Một Route Template đã từng được dùng thì được lưu trữ (archive) thay vì bị xóa: nó vẫn hiển thị trong ngữ cảnh lịch sử nhưng không bao giờ được đưa ra để chọn cho các lần gán tuyến mới.
- **Không có hệ thống quản lý phiên bản template riêng**: các bản chụp Assigned Route hiện có bảo toàn các định nghĩa tuyến trong lịch sử, và lịch sử Movement thực tế, bất biến, vẫn là nguồn có thẩm quyền.

---

## 8.9 RouteStep

Đại diện cho một bước dự kiến trong một Route.

Các thuộc tính điển hình (chỉ mang tính minh họa):

- `id`
- `route_template_id`
- `sequence`
- `area_id`
- `operation_id`
- `expected_duration`
- `instructions`
- `preferred_machine_id`

Thời lượng dự kiến (expected duration) chỉ mang tính tham khảo.

Nó có thể được dùng cho:

- chỉ báo quá hạn,
- phát hiện điểm nghẽn,
- phân tích thời gian chờ trong hàng chờ,
- phân tích thời gian xử lý,
- ước tính thời điểm hoàn tất.

Thời lượng dự kiến không bao giờ được chặn sản xuất.

---

## 8.10 AssignedRoute

Đại diện cho một bản chụp Route được gán cho một Quantity Flow **PLANNED**. AssignedRoute là không bắt buộc: các flow Floating (mặc định) không có AssignedRoute.

Quy tắc:

- Nó được sao chép từ một Route Template.
- Nó trở nên độc lập sau khi được gán.
- Các thay đổi template không được làm thay đổi công việc đang hoạt động.
- Người dùng được ủy quyền có thể chỉnh sửa nó.
- Các bước đã qua là bất biến: một lần chỉnh sửa được ủy quyền chỉ thay thế các bước tương lai — những bước nằm sau bước cuối cùng mà bất kỳ Movement nào của flow tham chiếu tới, kể cả các Movement đã được Undo — của chính AssignedRoute thuộc flow được chọn, bắt buộc phải có lý do, và được ghi audit là `ROUTE_ADJUSTED` với đầy đủ danh sách bước trước đó và danh sách bước mới cùng người dùng đang đăng nhập.
- Các Quantity Flow khác nhau của cùng một PN có thể có các Route khác nhau.
- Lịch sử Movement thực tế vẫn là nguồn có thẩm quyền.
- Các sai lệch (deviation) tuyến phải được ghi lại.
- Các chỉnh sửa tuyến phải bảo toàn trạng thái tuyến trước đó trong lịch sử audit.

---

## 8.11 PartMovement

Đại diện cho một sự kiện số lượng bất biến.

Các thuộc tính điển hình (chỉ mang tính minh họa):

- `id`
- `part_number` (PN chuẩn viết bằng chữ in hoa — do chính Movement giữ)
- `quantity_flow_id`
- `quantity`
- `movement_type`
- `from_area_id`
- `to_area_id`
- `source_machine_id`
- `destination_machine_id`
- `operation_id`
- `route_step_id`
- `scan_session_id`
- `worker_id`
- `station_id`
- `occurred_at`
- `server_received_at`
- `movement_reason`
- `reason`
- `reverses_movement_id`
- `device_event_id`
- `metadata`

Các loại Movement ban đầu có thể bao gồm:

- `RECEIVED`
- `TRANSFERRED`
- `ASSIGNED_TO_MACHINE`
- `RELEASED_FROM_MACHINE`
- `AREA_COMPLETED`
- `SPLIT`
- `MERGED`
- `STOCKED`
- `QUANTITY_ADJUSTED`
- `SCRAPPED`
- `ROUTE_ADJUSTED` — được ghi thành một sự kiện audit trên AssignedRoute, không bao giờ thành một PartMovement: nó không di chuyển số lượng nào
- `ROUTE_DEVIATION_CONFIRMED`
- `REVERSED`

`movement_reason` là một ý định di chuyển có kiểu (typed) và không bắt buộc; giá trị đầu tiên của nó là `REPAIR` — việc trả số lượng một cách tường minh về một Area đã đi qua trước đó để sửa lại công việc đã làm trước đó (`movement_type = TRANSFERRED`, `movement_reason = REPAIR`). Repair do người dùng chọn một cách tường minh, không bao giờ được suy luận từ lịch sử tuyến, và không phải là một Work Order Demand cũng không phải là một Request Type. `reason` vẫn là lời giải thích dạng văn bản tự do và là bắt buộc đối với Repair, Scrap và các điều chỉnh số lượng.

`QUANTITY_ADJUSTED` với `direction = INCREASE` ghi lại việc cố ý bổ sung số lượng vật lý (operator được phép thực hiện, bắt buộc có lý do, có thể audit, không bao giờ bị che giấu dưới dạng một lần chuyển thông thường, và không bao giờ thay đổi số lượng yêu cầu). `SCRAPPED` ghi lại số lượng bị hỏng được loại khỏi sản xuất đang hoạt động (§11 Quantity Model).

`AREA_COMPLETED` ghi lại Area Completion (§7 Area Completion): số lượng được chọn đã xử lý xong tại Area hiện tại của nó và đang chờ ở trạng thái `READY_TO_TRANSFER` trên giá hàng đã hoàn thành (finished rack). Sự kiện này xóa Machine hiện tại khỏi vị trí được suy ra, trong khi Area vẫn là vị trí, và nó không tạo ra lượt ghé tuyến (route visit) nào — việc hoàn tất diễn ra bên trong Area nguồn hiện có. Nó khác với `RELEASED_FROM_MACHINE`, loại sự kiện trả số lượng **chưa hoàn thành hoặc đang tạm dừng** từ một Machine về hàng chờ của Area (`RELEASED_FROM_MACHINE → QUEUED`; `AREA_COMPLETED → READY_TO_TRANSFER`), và khác với `STOCKED`, loại sự kiện duy nhất biểu thị việc hoàn tất sản xuất tại Stockroom cuối (terminal).

Khi số lượng vẫn đang được xử lý (`ON_MACHINE`, hoặc đang được xử lý trực tiếp trong một Area không có Machine) được quét vào một Area khác thông qua một lần chuyển thông thường, việc xử lý tại Area nguồn được coi là đã hoàn tất: một **lệnh ứng dụng nguyên tử (atomic application command)** duy nhất ghi thêm `AREA_COMPLETED`, theo ngay sau là `TRANSFERRED` — hoặc mọi bản ghi Movement cần thiết đều được ghi, hoặc không bản ghi nào được ghi. Không cần một thao tác DONE thủ công riêng trước đó. Số lượng đã ở trạng thái `READY_TO_TRANSFER` được chuyển chỉ bằng một `TRANSFERRED` thông thường. Undo một lệnh như vậy sẽ đảo ngược toàn bộ lệnh đó (§16).

Quy tắc:

- Movement ghi lại số lượng PN.
- Một Movement giữ giá trị PN chuẩn của riêng nó: lịch sử vẫn tiếp tục định danh PN (ví dụ `2027-60-8114-00`) ngay cả khi không có bản ghi PartNumber master nào tồn tại (§8.1).
- Movement không gắn với một Work Order Allocation cụ thể.
- Movement không bao giờ được ghi đè một cách âm thầm.
- Các sửa sai (correction) bảo toàn sự kiện gốc.
- Trạng thái hiện tại phải có thể được tái dựng từ lịch sử Movement.

---

## 8.12 WorkOrderAllocation

Đại diện cho việc gán số lượng PN đã nhập kho cho Work Order Demand.

Các thuộc tính điển hình (chỉ mang tính minh họa):

- `id`
- `part_number` (PN chuẩn viết bằng chữ in hoa — do chính allocation giữ)
- `work_order_demand_id`
- `quantity`
- `allocated_at`
- `allocated_by_worker_id`
- `allocation_reason`
- `is_manual_override`
- `reverses_allocation_id`

Quy tắc:

- Phân bổ chỉ diễn ra từ số lượng đã nhập kho.
- Phân bổ không làm thay đổi lịch sử Movement.
- Admin hoặc Manager có thể điều chỉnh phân bổ bất cứ lúc nào.
- Mọi điều chỉnh phải có thể audit được.
- Tổng phân bổ đang hoạt động không bao giờ được vượt quá số lượng đã nhập kho khả dụng.
- Phân bổ không bao giờ được vượt quá phần Work Order Demand còn lại, trừ khi được cho phép một cách tường minh như một sửa sai (correction).

---

## 8.13 Worker

Đại diện cho một operator (§7 Worker — định danh audit trong phạm vi Scan Station, không bao giờ là một tài khoản ứng dụng).

Thuộc tính:

- `id` — định danh nội bộ ổn định
- `name`
- `badge_barcode` — barcode đã được in sẵn trên thẻ nhân viên hiện có của công ty (§10); duy nhất giữa các Worker
- `avatar`
- `is_active`

Worker **không có mã số nhân viên**.

Việc nhận diện Worker có thể được cấu hình theo từng Area.

Các chế độ được hỗ trợ:

- tắt (disabled),
- Worker cố định (fixed Worker),
- Worker Session được quét (§19 — thời gian chờ không hoạt động kiểu trượt (sliding inactivity timeout)).

Định danh Worker không được chi phối các quy tắc nghiệp vụ sản xuất.

---

# 9. Khái niệm ứng dụng (Application Concepts)

## ScanSession

ScanSession là trạng thái tạm thời ở tầng Application, được dùng để điều phối việc quét barcode.

Nó có thể giữ ngữ cảnh tạm thời như:

- Area
- Worker đang hoạt động
- thời điểm hết hạn

**Không có Machine Session bền vững và không có ý định PN (PN intent) bền vững**: mọi thao tác sản xuất tại Scan Station đều là một hộp thoại một lần (one-shot) — việc quét barcode Machine chỉ mở một hộp thoại gán một lần với Machine đó được chọn sẵn, và việc hoàn tất hoặc hủy bất kỳ hộp thoại nào đều xóa ngữ cảnh tạm thời. Chỉ có Worker Session là được duy trì (§19).

ScanSession không bao giờ được trở thành nguồn sự thật cho trạng thái sản xuất.

---

# 10. Mô hình barcode (Barcode Model)

Quét barcode là phương thức tương tác chính.

Mọi barcode phải xác định loại thực thể của nó một cách tất định.

Các định dạng logic:

```text
PF:PN:<part-number>
PF:MACHINE:<asset-tag>
PF:AREA:<stable-id>
PF:SCRAP
```

Không gian tên `PF:` tồn tại để nhận diện các barcode thuộc sở hữu của PartFlow, xác định loại thực thể một cách an toàn, và tránh nhầm lẫn các barcode không liên quan của nhà máy hoặc của nhà cung cấp với các thực thể của PartFlow.

**Thẻ Worker là ngoại lệ có chủ đích duy nhất đối với không gian tên `PF:`.** Barcode thẻ Worker là barcode đã được in sẵn trên thẻ nhân viên hiện có của công ty — PartFlow không bao giờ in hay yêu cầu một barcode Worker riêng, và không có định dạng barcode `PF:WORKER:`. Một giá trị quét được nằm ngoài không gian tên `PF:` được **so khớp chính xác sau khi chuẩn hóa (cắt khoảng trắng ở đầu và cuối, chuyển thành chữ in hoa)** với các giá trị `badge_barcode` của các Worker **đang hoạt động** (§8.13). Khi có đúng một kết quả khớp, giá trị đó định danh Worker tương ứng; nếu không, nó là một barcode không xác định và bị từ chối, không ghi gì — không bao giờ đoán. Barcode thẻ phải luôn là duy nhất giữa các Worker (được so sánh ở dạng chuẩn đó, kể cả các Worker không hoạt động), và thẻ của một Worker không hoạt động không khớp với bất kỳ Worker nào.

Barcode Machine mang Asset Tag của Machine (§8.6) — định danh bất biến, được gán tự động, của máy vật lý (`Asset Tag CD-0512` → barcode được quét `PF:MACHINE:CD-0512`). Không có định danh barcode Machine độc lập nào và không có việc nhập barcode Machine thủ công.

Barcode PN mang chính PN đó, ở dạng chuẩn viết bằng chữ in hoa (§7 Part Number). Quy tắc phân tích:

1. Cắt bỏ các ký tự kết thúc do máy quét thêm vào và khoảng trắng bao quanh ở hai đầu giá trị quét được; không bao giờ loại bỏ bất kỳ thứ gì bên trong giá trị.
2. Yêu cầu tiền tố chính xác `PF:PN:` đối với barcode PN được quét.
3. Coi toàn bộ phần hậu tố là PN ứng viên và chuẩn hóa nó (§7 Part Number): khoảng trắng bao quanh được cắt bỏ; sau khi cắt, ứng viên phải không rỗng và không được chứa khoảng trắng bên trong — một hậu tố có khoảng trắng bên trong là barcode PN không hợp lệ và bị từ chối, không bao giờ được âm thầm làm sạch.
4. Chuẩn hóa ứng viên thành CHỮ IN HOA — đó là PN chuẩn (định danh không phân biệt chữ hoa chữ thường, §7).
5. Không bao giờ phân tích các đoạn của PN và không bao giờ kiểm tra định dạng PN vượt quá các quy tắc về dạng chuẩn.
6. Không yêu cầu PN phải tồn tại trong một danh mục được nạp sẵn — bản ghi PartNumber master được tạo ở lần sử dụng hợp lệ đầu tiên (§8.1); ERP không được gọi trong MVP.

**Không có barcode Action.** Ý định thao tác (Modify intake, Repair, Scrap, bổ sung số lượng) được chọn thông qua các hộp thoại một lần (one-shot) tường minh, không bao giờ thông qua một trạng thái barcode được kích hoạt sẵn (armed) kéo dài. `PF:SCRAP` là một barcode chuyên dụng duy nhất, phụ thuộc ngữ cảnh: nó chỉ được chấp nhận bên trong quy trình Scrap, nơi mỗi lần quét tăng bộ đếm scrap đang chờ (§11); việc quét nó trong ô nhập chính của Scan Station sẽ bị từ chối.

Yêu cầu:

- Giá trị barcode phải là duy nhất.
- Định danh barcode không được phụ thuộc vào tên hiển thị có thể thay đổi.
- Văn bản PN thô từ ERP không được tự động được coi là một barcode PartFlow.
- Việc nhập PN thủ công phải luôn khả dụng và chấp nhận mọi giá trị PN không rỗng sau khi cắt khoảng trắng ở đầu và cuối và không chứa khoảng trắng bên trong; giá trị nhập (kể cả khi nhập bằng chữ thường) được chuẩn hóa thành PN viết bằng chữ in hoa trước khi sử dụng.
- Việc phân tích barcode phải tất định.
- Barcode không xác định phải bị từ chối một cách rõ ràng.
- Các thực thể không hoạt động không được chấp nhận cập nhật sản xuất.

Barcode PN chỉ định danh PN.

Nó không mã hóa:

- Work Order,
- Work Order Demand,
- số lượng,
- Job Number,
- Route,
- Area hiện tại,
- Machine hiện tại.

Những ngữ cảnh đó do ứng dụng xác định.

---

## Phân giải barcode (Barcode Resolution)

Operator không được bị buộc phải chọn một chế độ quét trước mỗi lần quét.

Hệ thống nên tự động nhận diện loại barcode.

Khi chỉ có một ngữ cảnh hợp lệ, ứng dụng có thể tự động tiếp tục.

Khi có nhiều ngữ cảnh hợp lệ, ứng dụng không được đoán.

Ứng dụng phải chỉ đưa ra các lựa chọn liên quan hoặc yêu cầu xác nhận.

Các ví dụ về tình huống mơ hồ bao gồm:

- nhiều Area nguồn hoặc Quantity Flow có thể có (không bao giờ được gộp một cách âm thầm),
- nhiều Operation hợp lệ trong Area,
- việc lựa chọn giữa một lần chuyển thông thường và một lần trả về Repair tường minh tới một Area đã đi qua trước đó,
- nhiều Work Order MODIFY không có số mà đều hợp lý cho cùng một PN (bắt buộc phải chọn một cách tường minh — không bao giờ đoán),
- điểm đến Route ngoài dự kiến trên một Planned Route.

Không có thao tác ghi sản xuất nào được phép diễn ra cho đến khi tình huống mơ hồ được giải quyết.

---

# 11. Mô hình số lượng (Quantity Model)

Số lượng luôn biểu thị chi tiết vật lý.

Một PN có thể được phân bố trên nhiều Area và Machine.

Ví dụ:

```text
PN ABC

Material        5
Cut             4
Lathe 1         3
Lathe 2         2
Mill            6
```

Tổng số lượng đang hoạt động phải luôn có thể đối chiếu được với:

```text
introduced quantity = active quantity + stocked quantity + scrapped quantity
```

trong đó số lượng được đưa vào (introduced quantity) bao gồm các lần phát hành vào sản xuất (production release), các Modify intake và các lần bổ sung `QUANTITY_ADJUSTED` tường minh. Scrap không bao giờ làm giảm số lượng yêu cầu của Work Order Demand.

---

## Tách số lượng (Quantity Splitting)

Số lượng có thể được tách bất cứ khi nào sản xuất cần.

Ví dụ:

```text
Material: 10
```

trở thành:

```text
Cut: 4
Lathe: 6
```

Việc tách:

- tạo ra các Quantity Flow riêng biệt,
- giữ nguyên PN gốc,
- bảo toàn tổng số lượng,
- không sửa đổi Work Order Demand.

---

## Gộp số lượng (Quantity Merging)

Các Quantity Flow của cùng một PN có thể hội tụ lại về sau.

Ví dụ:

```text
Lathe 1: 2
Lathe 2: 4
```

trở thành:

```text
Mill: 6
```

Lịch sử phải bảo toàn việc số lượng được gộp đã đến bằng cách nào.

---

## Toàn vẹn số lượng (Quantity Integrity)

Ứng dụng không bao giờ được:

- vô tình tạo ra số lượng,
- vô tình hủy số lượng,
- nhân bản số lượng,
- làm mất số lượng,
- tạo ra số lượng âm,
- di chuyển nhiều hơn số lượng nguồn khả dụng,
- nhập kho nhiều hơn số lượng khả dụng,
- phân bổ nhiều hơn số lượng đã nhập kho khả dụng.

Mọi sửa sai có chủ ý phải tạo ra một sự kiện tường minh có thể audit được.

Lịch sử không bao giờ được viết lại một cách âm thầm.

---

## Scrap

Số lượng bị hỏng được loại khỏi sản xuất đang hoạt động bằng loại Movement chuẩn `SCRAPPED` (cách diễn đạt trên UI: “Scrap damaged quantity”).

Quy trình Scrap:

- được vào một cách tường minh từ một hộp thoại thao tác PN;
- đếm các chiếc bị hỏng bằng barcode chuyên dụng `PF:SCRAP` — mỗi lần quét tăng số đếm đang chờ thêm một, không có trạng thái sản xuất nào thay đổi trong khi đếm, và số đếm đang chờ có thể được sửa hoặc đặt lại trước khi xác nhận;
- yêu cầu một lý do scrap chung;
- hiển thị một bản tóm tắt cuối cùng (PN, Area, Machine nếu có, số lượng khả dụng ban đầu, số lượng scrap, số lượng còn hoạt động, Worker, Scan Station, lý do) trước khi áp dụng;
- khi Cancel thì hủy bỏ số đếm đang chờ, không ghi gì;
- khi Confirm thì tạo ra **một** thao tác `SCRAPPED` có thể audit được cho tổng số lượng.

Số lượng đã scrap được hiển thị ở mọi nơi một PN được trình bày trong vận hành (Scan Station, Production Board, Area Board, Tracking), và Tracking hiển thị lịch sử scrap một cách tường minh.

---

# 12. Quy tắc sở hữu xử lý (Processing Ownership Rules)

## Area không có Machine (Area Without Machines)

Ví dụ:

- Area: `External`
- Operation: `Plating`

Một Area không có Machine nào thì không có hàng chờ: Area đó trực tiếp sở hữu và xử lý số lượng nhận được.

Khi người vận hành (operator) quét số lượng của một PN vào Area:

- Area nhận quyền sở hữu,
- số lượng được coi là đang được xử lý,
- Operation đã cấu hình được ghi lại,
- Machine vẫn là null.

Giao diện người dùng (UI) không hiển thị thẻ Machine giữ chỗ (placeholder) nào và không hiển thị thống kê đang chờ/đang trên máy (queued/on-machines) cho một Area như vậy.

Nếu Area hỗ trợ nhiều Operation, Operation áp dụng phải được xác định hoặc được xác nhận.

---

## Area có Machine — `QUEUE_AND_ASSIGN` (Area With Machines — `QUEUE_AND_ASSIGN`)

Một Area có một hoặc nhiều Machine luôn dùng `QUEUE_AND_ASSIGN`. Một Machine hoạt động giống hệt như nhiều Machine — hành vi không bao giờ khác nhau theo số Machine, và số lượng **không bao giờ được tự động gán** chỉ vì Area có duy nhất một Machine.

- Số lượng mới nhận đi vào hàng chờ của Area.
- Số lượng được gán cho một Machine thông qua một **quy trình gán một lần (one-shot assignment workflow)** tường minh (quét Machine như một lối tắt, hoặc bắt đầu từ PN); không có Machine Session lưu bền nào (§9).
- Một Machine có thể giữ số lượng của nhiều PN.
- Một dòng PN đang chờ cung cấp thao tác `ASSIGN`; một dòng PN đã gán Machine cung cấp thao tác `QUEUE`, thao tác này trả số lượng về hàng chờ của Area (`RELEASED_FROM_MACHINE`).

Machine vẫn là đơn vị thực hiện hiện tại cho đến khi số lượng:

- được hoàn tất tại Area (`AREA_COMPLETED` — DONE thủ công hoặc hoàn tất ngầm trong một lần chuyển, §7 Area Completion),
- được chuyển,
- được trả về hàng chờ,
- bị đảo ngược (reversed),
- hoặc được gán lại thông qua một sự kiện tường minh.

## Các trạng thái xử lý của Area (Area Processing States)

Đối với một Area có Machine:

```text
QUEUED
  -> ASSIGNED_TO_MACHINE
ON_MACHINE
  -> AREA_COMPLETED
READY_TO_TRANSFER
  -> TRANSFERRED
next Area queue or direct processing
```

Đối với một Area không có Machine:

```text
PROCESSING
  -> AREA_COMPLETED
READY_TO_TRANSFER
  -> TRANSFERRED
next Area queue or direct processing
```

Một dòng PN đã gán Machine cung cấp hai thao tác riêng biệt, không bao giờ được gộp làm một:

- `DONE` — hoàn tất xử lý và đưa số lượng sang trạng thái đã xong (`AREA_COMPLETED → READY_TO_TRANSFER`);
- `QUEUE` — trả số lượng chưa xong hoặc đang tạm dừng về hàng chờ của Area (`RELEASED_FROM_MACHINE → QUEUED`).

Quy trình DONE thủ công xác định PN, Area hiện tại và Machine hiện tại khi có; chọn hoặc xác nhận số lượng (MAX mặc định là số lượng tại vị trí nguồn hiện tại); hiển thị một màn hình xác nhận riêng; không ghi gì trước khi có xác nhận cuối cùng; sau đó ghi thêm một sự kiện `AREA_COMPLETED` bất biến, suy ra `READY_TO_TRANSFER`, xóa Machine hiện tại, giữ Area hiện tại làm vị trí vật lý, và trả focus về ô nhập barcode (mã vạch). Một Area không có Machine dùng cùng quy trình này nhưng không có trường Machine. Hoàn tất một phần là hợp lệ: chỉ số lượng được chọn được hoàn tất; số lượng còn lại giữ nguyên trạng thái hiện có của nó (theo ngữ nghĩa tách khi cần); một thao tác không bao giờ đánh dấu DONE toàn bộ số lượng của PN.

Số lượng đã xong (`READY_TO_TRANSFER`) được trình bày trong bản tóm tắt của Area — thẻ Machine chỉ hiển thị số lượng đang được gán — và không bao giờ được trình bày là đã nhập kho hoặc đã hoàn tất chế tạo (manufacturing-complete).

---

# 13. Tiếp nhận Work Order (Work Order Intake)

Work Order có thể đi vào PartFlow thông qua:

- nhập thủ công,
- nhập từ tệp (file import),
- đồng bộ với hệ thống hoạch định nguồn lực doanh nghiệp (ERP) trong tương lai.

Đối với một Work Order mới nhận:

1. Tạo hoặc tìm Work Order.
2. Tạo hoặc cập nhật Work Order Demand cho từng PN.
3. Tìm bìa hồ sơ PN có thể tái sử dụng.
4. Tạo bản ghi metadata master của PN nếu PN là mới (barcode chứa chính PN chuẩn (canonical), §10).
5. Thêm Work Order Demand mà không tạo một PN được theo dõi riêng.
6. Lưu nhu cầu nghiệp vụ (business demand). Việc lưu Work Order Demand không bao giờ tự động tạo số lượng sản xuất.
7. Xác nhận Route Mode (mặc định là Floating) — và Planned Route nếu được chọn — khi phát hành vào sản xuất.

**Nhập từ tệp (File import).** Work Order có thể được nhập từ một tệp đã chuẩn bị sẵn thay vì gõ tay.

- **Định dạng tệp.** Tệp là một tệp giá trị phân tách bằng dấu phẩy (CSV) mã hóa UTF-8, hoặc một workbook Excel (.xlsx). Với Excel, chỉ worksheet đầu tiên được đọc, và chỉ các giá trị đã lưu của nó: công thức không bao giờ được tính toán.
- **Cột.** Hàng tiêu đề nêu tên các cột cố định của mẫu: Work Order Number, Part Number và Requested Quantity (bắt buộc), cùng Job Number và Due Date (`YYYY-MM-DD`) (tùy chọn).
- **Tệp chính là việc chọn dòng.** Các dòng trong tệp là các dòng sẽ được nhập, nên người chuẩn bị tệp là người chọn chúng. Một tệp có chứa các cột của danh mục vật tư (BOM) nguồn (ví dụ Type, Ref Designator, Issued, Shelf, Cost) bị từ chối. Mọi cột khác không có trong mẫu đều bị bỏ qua và được liệt kê trong báo cáo.
- **Work Order Number.** Mỗi dòng cần một Work Order Number. Các dòng được nhóm theo giá trị này, và nó được lưu đúng như đã viết (một số có khoảng trắng bao quanh bị từ chối). Một dòng không có Work Order Number chặn toàn bộ lần nhập.
- **Một Work Order là một transaction.** Một Work Order có bất kỳ dòng không hợp lệ nào bị từ chối toàn bộ, và các Work Order khác trong tệp vẫn được nhập.
- **Kiểm tra, rồi mới nhập.** Tệp được kiểm tra trước khi được nhập, và bước nhập kiểm tra lại chính tệp đó.
- **Không trùng lặp.** Một Work Order Number đã tồn tại không bao giờ bị tạo trùng: nhập lại cùng một tệp không tạo ra gì. Một Work Order đã hoàn tất không bao giờ bị thay đổi bởi một lần nhập.
- **Work Order mới.** Demand được nhập có Request Type `NEW`, không có ngày đến hạn (due date) của Work Order, chưa được phát hành và không có Priority.
- **Revision.** Cột Revision không được nhập; Revision vẫn thuộc về phần quản lý Part Numbers.

**Nhập từ tệp vào một Work Order đã tồn tại (File import into an existing Work Order).** Với một Work Order Number đã có trong PartFlow, một Work Order đang Open hoặc Released chỉ được cập nhật sau khi danh sách thay đổi theo từng Work Order đã được hiển thị và đã có một xác nhận bằng cách gõ lại.

- Việc cập nhật chính là thao tác sửa demand thông thường: số lượng (không bao giờ thấp hơn mức sàn đã phát hành hoặc đã phân bổ), ngày đến hạn, các Job Number được thêm vào, và các dòng mới trên một Work Order đang Open. Nó kéo theo các hệ quả thông thường của thao tác sửa đó: một dòng Hot đã được phân bổ đủ sẽ rời khỏi Hot list, và một Work Order có tất cả các dòng đã được phân bổ đủ sẽ hoàn tất.
- Ô trống, và các dòng không có trong tệp, giữ nguyên giá trị đã lưu. Một lần nhập không bao giờ xóa một dòng demand.
- Một transaction bao trùm một Work Order.
- Khi các thay đổi đã xác nhận không còn khớp tại bước Import, không Work Order hiện có nào trong tệp bị thay đổi (các Work Order mới vẫn được nhập). Một Work Order bị thay đổi sau khi bước Import đã kiểm tra lại nó cũng không bị thay đổi. Mỗi Work Order như vậy được liệt kê kèm lý do.
- Xác nhận bao trùm các thay đổi đã được hiển thị, nên một thay đổi do người khác thực hiện mà vẫn để các thay đổi đó đúng như đã hiển thị thì không ngăn chúng được áp dụng.

Phát hành vào sản xuất (production release) là một thao tác riêng và tường minh. Khi phát hành vào sản xuất:

1. Xác nhận số lượng phát hành.
2. Xác nhận Route Mode — `FLOATING` (mặc định, không cần Route) hoặc `PLANNED` với một Route được chọn.
3. Xác nhận Area và Operation bắt đầu đã cấu hình.
4. Tạo Quantity Flow cùng với route mode của nó.
5. Chụp snapshot Assigned Route **chỉ** cho một flow `PLANNED` — một lần phát hành Floating không có AssignedRoute.
6. Ghi thêm một Part Movement `RECEIVED` bất biến.
7. Suy ra hoặc cập nhật projection (dữ liệu chiếu) vị trí hiện tại một cách nguyên tử (atomic) cùng với Movement.

Sản xuất mới từ ERP thường dùng Request Type `NEW`.

Area bắt đầu có thể là Material hoặc một Area bắt đầu khác đã được cấu hình. Một Area **cuối (terminal)** không bao giờ là Area bắt đầu: đó là nơi số lượng đã xong kết thúc (§18), nên việc phát hành vào sản xuất vào Area đó bị từ chối.

**Phát hành từng phần và nhiều lần (Partial and repeated release).** Một Work Order Demand có thể được phát hành thành **nhiều phần**: phát hành 20 trong một demand 50 chiếc, rồi 12, rồi 18 là bình thường. Mỗi phần là một lần phát hành tường minh riêng, tạo ra Quantity Flow riêng của nó. Các phần không bao giờ được gộp lại, và mọi phần sau phần đầu tiên đều phải tuân theo quy tắc xác nhận khi đã có số lượng đang hoạt động được nêu bên dưới. Số lượng đã phát hành của demand là tổng của các Movement phát hành ghi nhận nó, và số lượng còn lại (số lượng yêu cầu trừ số lượng đã phát hành) là một giới hạn cứng: phát hành nhiều hơn phần còn lại, hoặc phát hành bất kỳ lượng nào khi không còn gì, đều bị từ chối và không tạo ra gì.

**Sửa có giới hạn sau khi phát hành (Restricted edit after release).** Nhu cầu nghiệp vụ vẫn tiếp tục thay đổi sau khi sản xuất đã bắt đầu — số lượng tăng lên, ngày đến hạn bị dời, Job Number được bổ sung — nên một dòng Work Order Demand đã có số lượng phát hành vẫn **có thể sửa trong giới hạn**; nó không phải là chỉ đọc:

- **Số lượng yêu cầu** có thể được sửa, nhưng không bao giờ thấp hơn phần đã cam kết cho dòng đó: `max(released quantity, allocated quantity)`. Tăng nó luôn hợp lệ trên một Work Order chưa hoàn tất (Work Order đã hoàn tất là lịch sử chỉ đọc, §8.2 và §18) — khi đó dòng có số lượng còn lại để phát hành tiếp, và Work Order của nó theo đó trở lại `Open` (trạng thái ở §8.2 được suy ra từ các dòng demand). Giảm nó xuống đúng bằng số lượng đã cam kết là hợp lệ và chỉ đơn giản là không còn gì để phát hành.
- **Ngày đến hạn** và các **Job Number** bên ngoài có thể được sửa như trên bất kỳ dòng demand nào khác.
- **Part Number** vẫn không thể sửa — như trên mọi dòng demand đã lưu, một PN khác là một dòng mới, không bao giờ là việc viết lại dòng cũ.
- **Request Type** được cố định khi đã có số lượng được phát hành: những gì đã phát hành là đã được phát hành dưới dạng công việc `NEW` hoặc `MODIFY`, và đó là lịch sử. **Người yêu cầu (requester)**, **lý do (reason)** và **ghi chú (notes)** cũng được cố định theo; việc mở chúng ra cho sửa là một quyết định riêng, không phải hệ quả phụ của quy tắc này.
- **Việc xóa vẫn bị từ chối** (§13 *Work Order Demand Removal*), và không bao giờ có thao tác sửa demand nào viết lại số lượng đã phát hành, Quantity Flow, lịch sử Movement hoặc lịch sử phát hành — các điều chỉnh sản xuất về sau đi qua các quy trình sửa sai (correction) và quy trình sản xuất (§16).

Quy tắc được thực thi tại nơi dữ liệu được ghi, không bao giờ chỉ ở UI, và một thao tác sửa một dòng đã phát hành được tuần tự hóa (serialize) với một lần phát hành của cùng dòng đó, để số lượng đã phát hành không bao giờ có thể vượt quá số lượng yêu cầu.

Nếu PN đã có số lượng đang hoạt động, hệ thống phải hiển thị phân bố hiện có và yêu cầu xác nhận ý định một cách tường minh. Một Work Order mới yêu cầu một PN đang hoạt động không bao giờ tự động tạo thêm số lượng vật lý và không bao giờ tự động gộp các Quantity Flow.

Work Order và Work Order Demand đại diện cho nhu cầu nghiệp vụ; việc tạo hoặc sửa Work Order Demand không xác định vị trí sản xuất hiện tại. Phát hành vào sản xuất là thao tác đưa số lượng vật lý vào một cách tường minh. Part Movement vẫn là hoạt động của PN + Quantity Flow + số lượng: Work Order Demand không sở hữu Movement trên xưởng sản xuất, và Work Order Allocation vẫn tách biệt với cả hai.

## Ánh xạ từ hệ thống nguồn (Source-System Mapping)

Khi dữ liệu Work Order bắt nguồn từ hệ thống nguồn bên ngoài (nhập thủ công từ các bản ghi của hệ thống đó, nhập từ tệp, hoặc đồng bộ ERP trong tương lai), hãy dùng ánh xạ chuẩn (canonical) sau:

- Work Order Number nguồn → `WorkOrder.work_order_number`.
- Job Number nguồn → Job Number bên ngoài mang tính thông tin, gắn với `WorkOrderDemand` liên quan.
- Part Number của linh kiện nguồn → `PartNumber`.
- Dòng linh kiện nguồn → `WorkOrderDemand` ứng viên.
- Quantity của linh kiện nguồn → `WorkOrderDemand.requested_quantity` ứng viên, nhưng chỉ khi dòng đó đại diện cho nhu cầu sản xuất mà PartFlow dự định theo dõi.
- Revision nguồn → trường Revision riêng, mang tính thông tin, của PartNumber.

Một Work Order nguồn cũng có thể xác định một cụm lắp ráp cha hoặc cụm được chế tạo cho (build-for assembly) với Part Number, Description và Revision riêng. Hãy coi thông tin đó là ngữ cảnh tiêu đề (header) của Work Order, trừ khi một quy trình đã được phê duyệt yêu cầu tường minh rằng cụm lắp ráp cha phải trở thành WorkOrderDemand hoặc QuantityFlow được theo dõi riêng.

Không bao giờ tự động nhập mọi linh kiện BOM thành một WorkOrderDemand được theo dõi: chỉ các dòng đại diện cho nhu cầu sản xuất do PartFlow quản lý mới đi vào quy trình theo dõi. Các trường danh mục vật tư nguồn như Type (`Fabricate`, `F`, `P`, `SM`, `Commercial`), Ref Designator, Attribute, Issued, Shelf, Cost và Open/Closed không mang ý nghĩa nghiệp vụ nào trong PartFlow: giá trị Type nguồn không phải là Request Type của PartFlow, Quantity BOM nguồn không tự động là số lượng yêu cầu, Issued không phải là Part Movement, Shelf không phải là Area hay vị trí hiện tại, Open/Closed nguồn không phải là trạng thái của PartFlow, và Cost nằm ngoài phạm vi. Chỉ có đúng một khái niệm Work Order — không được đưa vào một aggregate thứ hai giống Work Order cho dữ liệu nguồn.

## Xóa Work Order Demand (Work Order Demand Removal)

Một dòng Work Order Demand chỉ có thể bị xóa khỏi Work Order của nó khi chưa có số lượng sản xuất nào được phát hành cho dòng đó:

- Một dòng nháp chưa lưu có thể được xóa ngay lập tức.
- Một Work Order Demand đã lưu mà chưa có số lượng sản xuất nào được phát hành chỉ có thể bị xóa sau khi có xác nhận tường minh.
- Khi bất kỳ số lượng nào của một Work Order Demand đã được phát hành vào sản xuất, Work Order Demand đó không được bị xóa khỏi Work Orders. Các điều chỉnh về sau đi qua các quy trình sửa sai và quy trình sản xuất (§16); việc xóa không phải là một cơ chế sửa sai.
- Một Work Order Demand đã lưu đang nằm trong Hot list (và ngoài điều đó ra thì có thể xóa được) chỉ bị xóa sau một cảnh báo nêu Hot rank của nó và một xác nhận bằng cách gõ lại; việc xóa đưa nó ra khỏi Hot list — các rank còn lại dồn lên để lấp khoảng trống, có ghi audit (§21 Priority Management, §28) — và xóa dòng đó, tất cả trong một transaction. Không có xác nhận đó thì không có gì bị xóa.

Việc xóa một Work Order Demand không bao giờ được xóa PartNumber master, bất kỳ Quantity Flow nào, bất kỳ Part Movement nào, lịch sử phát hành, hoặc các bản ghi Work Order Demand khác của cùng PN.

---

# 14. Modify Intake và Repair (Modify Intake and Repair)

## Modify Intake

`MODIFY` đưa vào số lượng vật lý của một PN cho công việc sửa đổi. Một lần MODIFY intake có thể không có Work Order Number bên ngoài, không có Route định sẵn, và không có Work Order Demand đang hoạt động nào — PartFlow vẫn tạo các bản ghi nội bộ tối thiểu cần cho tính toàn vẹn số lượng và khả năng truy vết: một `WorkOrder` nội bộ (`work_order_number = NULL`), một `WorkOrderDemand` cho PN (khi tái sử dụng một Work Order nội bộ thì dòng hiện có của nó được tăng lên thay vào đó, như mô tả bên dưới), một `QuantityFlow`, và lịch sử Movement bất biến.

Hộp thoại hiển thị cho người vận hành có tiêu đề **Receive Quantity**, với **Confirm receipt** là thao tác xác nhận cuối cùng; `intake` vẫn là tên quy trình nội bộ (trong định danh, chú thích) và không bao giờ hiển thị cho người vận hành.

Ở đây, một Work Order Demand được tính là **đang hoạt động (active)** dựa trên số lượng nghiệp vụ còn thiếu của nó: `requested_quantity > allocated_quantity`. Số lượng đã phát hành không bao giờ quyết định điều này — một dòng đã phát hành đủ nhưng chưa được phân bổ vẫn là demand đang hoạt động — và `completed_at` là trạng thái tổng hợp của cả Work Order (§8.2), không bao giờ là trạng thái của một dòng: một dòng đã được phân bổ đủ là không hoạt động ngay cả khi Work Order của nó vẫn mở do các dòng khác. Chừng nào còn bất kỳ demand đang hoạt động nào của PN, số lượng của nó thuộc về một lần phát hành vào sản xuất tường minh từ Management (§13) và Scan Station không bao giờ tiếp nhận số lượng đó.

Khi một lần quét mở luồng intake (PN không có Work Order Demand đang hoạt động):

1. Quét hoặc nhập PN (được tạo ở lần sử dụng hợp lệ đầu tiên, §8.1).
2. Xác nhận hoặc thay đổi Request Type — `MODIFY` là **giá trị mặc định, không phải giá trị bắt buộc**.
3. Xác nhận hoặc thay đổi Route Mode — `FLOATING` là **giá trị mặc định, không phải giá trị bắt buộc**; một Planned Route chỉ được chọn khi chọn `PLANNED`.
4. Xác nhận số lượng, ngày đến hạn tùy chọn (thuộc sở hữu của WorkOrderDemand — PN không bao giờ sở hữu ngày đến hạn), và lý do/ghi chú khi áp dụng.
5. Xác nhận Area bắt đầu/hiện tại và, khi cần, Operation. `received_date` mặc định là dấu thời gian của lần quét: thời điểm lần quét PN mở quy trình, được giữ nguyên không đổi qua mọi bước của trình hướng dẫn (wizard) và được đọc thành một ngày lịch theo lịch riêng của địa điểm sản xuất (site) (`SITE_TIMEZONE`, §8.2). Chính LẦN QUÉT xác định ngày nhận, không bao giờ là lần xác nhận — một lần nhận được chuẩn bị trước nửa đêm và được xác nhận sau nửa đêm vẫn thuộc về ngày nó được quét — và thời điểm quét là một phần của ý định đã xác nhận, nên một lần thử lại ghi cùng một ngày.
6. Khi PN đã có số lượng đang hoạt động, xác nhận tường minh rằng số lượng nhận được được ghi nhận thành một Quantity Flow **riêng** (xem *Received quantity is never joined to existing quantity* bên dưới). Xác nhận được trình bày đối chiếu với phân bố đang hoạt động hiện có của PN, và nếu không có xác nhận đó thì không ghi gì.

Khi xác nhận, transaction tạo mới hoặc tái sử dụng PartNumber, tạo mới hoặc tái sử dụng một Work Order MODIFY nội bộ không số phù hợp, tạo WorkOrderDemand (hoặc tăng dòng hiện có của PN trong Work Order được tái sử dụng) và QuantityFlow, ghi Movement ban đầu, thiết lập vị trí hiện tại, và đặt số lượng vào hàng chờ của Area (Area có Machine) hoặc trực tiếp vào trạng thái xử lý tại Area (Area không có Machine).

Việc tái sử dụng Work Order không bao giờ được dựa vào phỏng đoán: nếu cùng PN có đúng một Work Order MODIFY không số đang hoạt động và rõ ràng phù hợp — không có số bên ngoài, chưa hoàn tất, và đã có một dòng demand `MODIFY` cho PN đó — thì tái sử dụng nó; nếu có nhiều Work Order khả dĩ, bắt buộc phải chọn tường minh và không lấy kết quả khớp đầu tiên.

Việc tái sử dụng giữ nguyên quy tắc mỗi Work Order chỉ có một PN chuẩn (canonical): dòng demand hiện có của PN trong Work Order được tái sử dụng được **tăng** thêm đúng bằng số lượng nhận được — đây là thao tác sửa có giới hạn của §13, trong đó việc tăng số lượng yêu cầu của một dòng đã phát hành luôn hợp lệ trên một Work Order chưa hoàn tất — thay vì Work Order có thêm một dòng thứ hai cho cùng PN, và lần nhận phát hành đúng phần tăng thêm đó. Chỉ những gì thao tác sửa có giới hạn cho phép mới bị động đến: số lượng yêu cầu và, khi người vận hành đã nhập, ngày đến hạn. Request Type, người yêu cầu, lý do và ghi chú của một dòng hiện có không bao giờ bị viết lại bởi một lần nhận tại trạm — lý do riêng của lần nhận đi kèm Movement bất biến của nó. Một lần nhận `NEW` không bao giờ tái sử dụng (việc tái sử dụng chỉ được định nghĩa cho các Work Order MODIFY không số) và luôn tạo Work Order nội bộ riêng của nó.

Một lần nhận đã xác nhận không được hoàn tác (undo) tại Scan Station: việc đảo ngược ở §16 khôi phục trạng thái sản xuất và không bao giờ viết lại nhu cầu nghiệp vụ mà một lần nhận đã tạo ra hoặc đã tăng thêm, nên một lần nhận nhầm được sửa qua các quy trình sửa sai sản xuất thay vì bị đảo ngược một nửa.

**Số lượng nhận được không bao giờ được nhập chung vào số lượng hiện có (Received quantity is never joined to existing quantity).** Một lần nhận luôn tạo QuantityFlow RIÊNG của nó. Nếu PN đã có số lượng đang hoạt động, người vận hành phải **xác nhận tường minh** rằng số lượng mới nhận được ghi nhận thành một Quantity Flow **riêng**; hệ thống không bao giờ suy ra ý định chỉ từ danh tính PN, và không ghi gì trước khi có xác nhận đó. Số lượng hiện có không bao giờ bị gộp vào, bị thay đổi, bị kế thừa hoặc bị viết lại bởi một lần nhận: số lượng, route mode, Assigned Route, vị trí hiện tại và trạng thái xử lý của nó giữ nguyên hoàn toàn, và không có Movement nào được ghi cho nó. Xác nhận được trình bày đối chiếu với phân bố đang hoạt động hiện có của PN — số lượng đó ở đâu và có bao nhiêu — và quy tắc được đánh giá lại một cách có thẩm quyền tại thời điểm ghi, nên một lần nhận mà PN của nó có thêm số lượng đang hoạt động trong khoảng giữa lần quét và lần xác nhận sẽ bị từ chối và không ghi gì, cho đến khi người vận hành xác nhận đối chiếu với phân bố hiện tại. Đây cũng chính là xác nhận tường minh mà lần phát hành vào sản xuất ở §13 yêu cầu; lần phát hành đó cũng luôn tạo một flow riêng và không bao giờ gộp.

Những Quantity Flow về sau hóa ra thuộc về nhau được đưa lại với nhau bằng quy trình **Combine quantities** hiện có (`MERGED`, §11; GUI_DESIGN §4.7) — nơi duy nhất mà số lượng từng được gộp, và chỉ cho những phần có ngữ cảnh sản xuất giống hệt nhau. Một lần nhận không bao giờ là nơi đó. Số lượng được phát hiện bên cạnh số lượng vốn đang hoạt động trong Area vẫn được xử lý bằng thao tác sửa sai `QUANTITY_ADJUSTED · INCREASE` của §11, thao tác này không gộp gì cả.

## Repair

`REPAIR` có nghĩa là một phần hoặc toàn bộ số lượng đã ở trong sản xuất phải quay lại một Area đã đi qua trước đó để sửa công việc đã thực hiện trước đó:

```text
A → B → C → D → B
```

Quy tắc:

- Repair không tạo số lượng vật lý mới, không phải là một WorkOrderDemand, và không phải là một Request Type.
- Repair hoạt động trên một QuantityFlow hiện có: Repair một phần sẽ tách flow (phụ thuộc vào SPLIT); Repair toàn bộ flow sẽ di chuyển cả flow.
- Repair được ghi nhận như một ý định di chuyển (movement intent): `movement_type = TRANSFERRED`, `movement_reason = REPAIR` (§8.11) — không có aggregate Repair riêng.
- Việc quay lại một Area đã đi qua **không bao giờ** được tự động coi là Repair — một Area đã đi qua có thể hoàn toàn hợp lệ là bước sản xuất bình thường tiếp theo, và một lần chuyển bình thường đến đó vẫn có thể thực hiện được. Người dùng chọn ý định Repair một cách tường minh (cách diễn đạt trên UI: “Return quantity for repair”, không bao giờ là “Create REPAIR demand”).
- Quy trình Repair thu thập: Area nguồn / QuantityFlow nguồn, Area đích để sửa, số lượng cần sửa, một lý do bắt buộc, PN bị ảnh hưởng, Worker, Scan Station và dấu thời gian, và kết thúc bằng một xác nhận tóm tắt cho biết rõ đây là một movement Repair.
- Lịch sử Movement thực tế vẫn là nguồn có thẩm quyền; lần quay lại để Repair kéo dài dấu vết quan sát được (observed trace) của một Floating Route.

---

# 15. Quy trình quét cốt lõi (Core Scan Workflow)

Quy trình thông thường lấy bàn phím và máy quét làm phương thức chính.

1. Scan Station được gắn với một Area (được chọn qua Station Selector, §21).
2. Người vận hành quét một barcode (PN hoặc Worker; barcode của Machine là một lối tắt gán một lần).
3. Hệ thống xác định loại barcode.
4. Hệ thống kiểm tra trạng thái thực thể và ngữ cảnh quét.
5. Hệ thống xác định PN, số lượng nguồn, ngữ cảnh Operation và Machine; nguồn số lượng luôn tường minh — khi có nhiều hơn một Area nguồn hoặc QuantityFlow hợp lệ, người dùng chọn đúng một (các nguồn không bao giờ bị gộp một cách âm thầm).
6. Hệ thống chỉ yêu cầu nhập số lượng khi cần thiết (chuyển và gán mặc định là MAX khả dụng; việc thêm số lượng không có giá trị mặc định và không có MAX).
7. Hệ thống chỉ yêu cầu xác nhận khi có sự mơ hồ hoặc sai lệch (deviation); mọi thao tác hoàn tất đều hiển thị bản tóm tắt của nó trước Confirm.
8. Hệ thống ghi một Movement bất biến.
9. Hệ thống suy ra trạng thái sản xuất mới.
10. Màn hình Area và các dashboard làm mới ngay lập tức.
11. Ô nhập barcode lấy lại focus và ngữ cảnh hộp thoại tạm thời được xóa — không có gì còn ở trạng thái chờ kích hoạt (armed).

Một lần chuyển mà số lượng nguồn vẫn đang được xử lý sẽ ngầm hoàn tất việc xử lý đó: xác nhận nêu rõ việc hoàn tất này, và một lệnh nguyên tử ghi thêm `AREA_COMPLETED` rồi `TRANSFERRED` (§8.11). Repair vẫn là một ý định di chuyển tường minh — nó không bao giờ được suy ra chỉ vì một Area đã đi qua xuất hiện lại, và các quy tắc về nguồn mơ hồ không thay đổi: nhiều Quantity Flow hoặc vị trí nguồn khả dĩ đòi hỏi phải chọn tường minh và không bao giờ bị gộp một cách âm thầm.

Các thao tác của Scan Station là **một lần (one-shot)**:

- **Machine trước (Machine-first):** quét barcode của Machine mở một hộp thoại gán một lần (nhãn UI `Assign to Machine`) với Machine đã được chọn sẵn; người vận hành chọn hoặc quét PN, nhập số lượng (mặc định MAX), xem lại bản tóm tắt, và xác nhận. Việc gán chỉ áp dụng một lần — lần quét tiếp theo bắt đầu lại từ đầu; nó không bao giờ tạo ra một Machine Session bám dính.
- **PN trước (PN-first):** quét barcode của PN mở hộp thoại một lần phù hợp — intake khi PN không có demand đang hoạt động (§14), chuyển với nguồn tường minh khi số lượng ở nơi khác, hoặc một hộp thoại thao tác chỉ gồm các lựa chọn hiện đang hợp lệ (gán số lượng đang chờ, nhận thêm, thêm số lượng, hoàn tất xử lý — DONE trong một Area xử lý trực tiếp, Repair, Scrap) khi PN đã có số lượng trong Area. Số lượng đã gán Machine được hoàn tất qua thao tác `DONE` của dòng trên thẻ Machine (§12).
- Hoàn tất hoặc hủy một hộp thoại sẽ xóa ngữ cảnh đang chờ; Cancel luôn có nghĩa là không ghi gì.

Hệ thống phải từ chối rõ ràng, không ghi gì, đối với:

- barcode không xác định,
- thực thể không hoạt động,
- tổ hợp Area/Machine không hợp lệ,
- số lượng bất khả thi,
- số lượng vượt quá số lượng nguồn khả dụng,
- sửa sai không được phép,
- sai lệch route khi không có xác nhận bắt buộc.

Một ngữ cảnh PN mơ hồ không chỉ đơn giản bị từ chối. Khi có nhiều ngữ cảnh hợp lệ, hệ thống phải trình bày các lựa chọn liên quan và yêu cầu xác nhận tường minh; sự mơ hồ chưa được giải quyết sẽ chặn việc ghi, và không ghi gì cho đến khi một lựa chọn được xác nhận. Hủy sẽ bỏ ý định đang chờ mà không ghi gì.

Một lần thử lại trùng lặp ở tầng truyền tải (transport) mang cùng event id phải trả về kết quả idempotent ban đầu và không được tạo thêm một Movement khác.

Một lần quét chỉ thành công sau khi máy chủ xác nhận việc ghi đã được lưu. Trạng thái kết nối hiển thị trên UI là cảnh báo sớm, không bao giờ là sự cho phép ghi một Movement theo kiểu lạc quan (optimistic). Nếu kết nối mất giữa lần kiểm tra kết nối thành công gần nhất và một yêu cầu ghi, yêu cầu đó phải thất bại với kết quả "nothing recorded" (không ghi gì); UI không bao giờ được hiển thị một kết quả đã ghi sai sự thật.

---

## Lưu bền cấu hình Scan Station (Scan Station Persistence)

Danh tính của một Scan Station và việc gắn nó với một Area là cấu hình ứng dụng và hạ tầng ổn định. Giao diện Scan Station được truy cập theo từng trạm (`/scan-station/<station-id>`); route Scan Station không kèm trạm hiển thị một Station Selector và không bao giờ tự chọn hoặc âm thầm thay thế một trạm. Một bảng cơ sở dữ liệu như `scan_stations` được cho phép; cấu hình Scan Station không bắt buộc phải là một aggregate cốt lõi của miền nghiệp vụ (domain).

ScanSession vẫn là ngữ cảnh tạm thời. Cả cấu hình Scan Station lẫn ScanSession đều không phải là nguồn sự thật cho trạng thái sản xuất — nguồn đó vẫn là lịch sử Part Movement bất biến. Part Movement ghi danh tính ổn định của trạm (`station_id`) để phục vụ audit.

Một Scan Station chỉ được dùng từ các thiết bị mà một quản trị viên (administrator) đã đăng ký (enrolled) cho nó (§20); việc đăng ký là cấu hình, không bao giờ là trạng thái sản xuất, và không bao giờ được ghi trên một Movement.

---

# 16. Undo và sửa sai (Undo and Correction)

Undo tồn tại để sửa các lỗi quét gần đây.

Undo không được xóa Movement gốc.

Thay vào đó, hệ thống phải:

- tạo một Movement bù trừ hoặc đảo ngược,
- tham chiếu Movement gốc,
- khôi phục trạng thái số lượng được suy ra,
- ghi Worker và dấu thời gian,
- yêu cầu lý do khi được cấu hình.

Undo nhắm vào **thao tác PN đã hoàn tất** đủ điều kiện gần nhất và luôn hiển thị trước một xác nhận tóm tắt (PN, thao tác gốc, số lượng, nguồn và đích, Machine khi áp dụng, Worker, dấu thời gian, và tác động của việc đảo ngược); Cancel không ghi gì. Sau một lần Undo đã xác nhận, ngữ cảnh “last scanned PN” chuyển sang thao tác đủ điều kiện liền trước đó; Undo bị vô hiệu hóa khi không còn gì đủ điều kiện.

Worker được ghi trên lần đảo ngược tuân theo chế độ nhận dạng (ID) Worker của Area (§19): chế độ tắt (disabled) không ghi Worker nào; chế độ Worker cố định (fixed Worker) ghi Worker đã cấu hình; một Worker Session được quét hợp lệ ghi **Worker đang hoạt động tại thời điểm Undo được xác nhận**.

**Xác nhận cuối cùng (Final confirmation).** Undo mang cổng xác nhận cuối cùng dùng chung của các thao tác nhạy cảm (§19):

- trong một Area ở chế độ phiên quét (scanned-session) có bật tùy chọn xác nhận bằng thẻ (badge) `UNDO` (mặc định là bật), một lần quét thẻ Worker sau bản tóm tắt đảo ngược sẽ hoàn tất việc đảo ngược và xác định Worker xác nhận (bất kỳ thẻ đang hoạt động nào; lần quét này cũng đăng nhập Worker đó);
- trong mọi trường hợp khác — một Area ở chế độ Worker cố định hoặc chế độ tắt, hoặc tùy chọn bị tắt — một câu hỏi xác nhận cuối cùng mang tông cảnh báo sẽ nhắc lại các dữ kiện chính trước khi có bất kỳ thứ gì bị đảo ngược;
- bản thân cổng cuối cùng không bao giờ bị bỏ qua.

Ở chế độ phiên quét mà không có phiên đang hoạt động hoặc phiên đã hết hạn, Scan Station vốn đã bị chặn cho đến khi có một lần quét thẻ hợp lệ (§19) — nên Undo chỉ có thể truy cập được khi có một phiên hợp lệ.

Undo trong sản xuất phải hoạt động trên toàn bộ **lệnh ứng dụng (application command)**: khi một thao tác người dùng đã tạo ra nhiều bản ghi Movement liên quan — ví dụ một lần chuyển đã ngầm hoàn tất xử lý tại nguồn (`AREA_COMPLETED` + `TRANSFERRED`, §8.11) — việc đảo ngược bù trừ cả lệnh thay vì đảo ngược một cách mù quáng một dòng tùy ý.

Undo được cấp bởi quyền **Undo recent eligible scans** (§20). "Recent eligible" (gần đây và đủ điều kiện) có nghĩa chính xác là quy tắc đủ điều kiện nêu trên — thao tác PN đã hoàn tất đủ điều kiện gần nhất — không có cửa sổ thời gian riêng nào. Role Operator ban đầu có quyền này.

Các thao tác sửa sai rộng hơn — sửa số lượng, sửa Work Order Allocation và các sửa sai lịch sử được ủy quyền — là các quyền riêng (§20) mà các vai trò Manager và Administrator ban đầu nắm giữ; role nào nắm giữ từng quyền sửa sai là do cấu hình quyết định, và các permission key là thẩm quyền duy nhất.

Mọi thao tác sửa sai phải luôn có thể audit được.

---

# 17. Định tuyến sản xuất (Production Routing)

Mọi Quantity Flow đều có một route mode (§7 Route and Route Mode): mặc định là `FLOATING`, và là `PLANNED` khi muốn có hướng dẫn.

## Floating Route — mặc định (Floating Route (default))

Một Floating Route không có trình tự định sẵn và không có snapshot AssignedRoute. Route thực tế được **suy ra từ lịch sử Movement bất biến**: mỗi Area đã đi qua kéo dài dấu vết quan sát được, các Area lặp lại được giữ nguyên (kể cả các lần quay lại để Repair), các Quantity Flow khác nhau của cùng một PN có thể có dấu vết khác nhau, và các flow đã tách tiếp tục một cách độc lập. Tracking suy ra và hiển thị dấu vết Floating Route từ lịch sử Movement; không có lịch sử route thứ hai nào có thể sửa đổi mà trùng lặp PartMovement.

## Planned Route

Một Planned Route mô tả đường đi sản xuất dự kiến của một Quantity Flow.

Planned Route cung cấp hướng dẫn cho việc lập kế hoạch và theo dõi.

Chúng không ghi đè lịch sử Movement thực tế.

---

## Route Template

Một Route Template là một trình tự Route Step có thể tái sử dụng, được trình bày với người dùng dưới tên **Planned Route** và được quản lý trong Management → Planned Routes (§21). Các template đã từng được dùng sẽ được lưu trữ (archive) thay vì bị xóa; template đã lưu trữ vẫn hiển thị trong ngữ cảnh lịch sử nhưng không bao giờ được đưa ra cho các lần gán route mới (§8.8).

Ví dụ:

```text
Material
→ Cut
→ Lathe
→ Deburr
→ External
→ Stockroom
```

Một Route có thể đi qua cùng một Area nhiều hơn một lần.

Ví dụ:

```text
Material
→ Mill
→ External
→ Mill
→ Stockroom
```

---

## Assigned Route

Khi sản xuất của một flow `PLANNED` bắt đầu, một snapshot Route được gán cho Quantity Flow. Các flow Floating không có Assigned Route.

Quy tắc:

- Các Quantity Flow khác nhau của cùng một PN có thể có các Route khác nhau.
- Một phần được tách ra có thể kế thừa Route của flow cha hoặc nhận một Route đã được sửa đổi.
- Thay đổi Route Template không được ảnh hưởng đến các Route đã gán đang hoạt động.
- Các chỉnh sửa được ủy quyền chỉ ảnh hưởng đến Quantity Flow được chọn, và chỉ các bước tương lai của nó; các bước đã qua không bao giờ thay đổi (§8.10).
- Lịch sử Movement thực tế vẫn là nguồn sự thật.

---

## Sai lệch route (Route Deviation)

Sai lệch route chỉ áp dụng cho Planned Route (một Floating Route không có kỳ vọng nào để mà sai lệch khỏi). Khi số lượng đến một Area không được dự kiến:

1. Cảnh báo người vận hành.
2. Yêu cầu xác nhận khi được cấu hình.
3. Ghi Movement thực tế.
4. Ghi sai lệch.
5. Giữ lại Route trước đó.
6. Cập nhật Route đã gán khi được ủy quyền và phù hợp.
7. Ghi người dùng, dấu thời gian và lý do.

Ứng dụng phải thể hiện sản xuất thực tế thay vì ép sản xuất khớp với một kế hoạch đã lỗi thời.

Một sai lệch không bao giờ tự nó thay đổi Route đã gán; route chỉ thay đổi thông qua thao tác điều chỉnh được ủy quyền và có lý do (§8.10).

---

## Thời lượng dự kiến (Expected Duration)

Mỗi Route Step có thể định nghĩa thời lượng xử lý dự kiến.

Thời lượng dự kiến có thể hỗ trợ:

- Days Left,
- Total Days,
- chỉ báo quá hạn,
- phân tích thời gian chờ,
- phân tích thời gian xử lý,
- khả năng nhìn thấy điểm nghẽn,
- ước tính thời điểm hoàn tất.

Thời lượng dự kiến chỉ mang tính tư vấn và không bao giờ được chặn sản xuất.

### Thời lượng dự kiến hiệu lực của một vị trí (Effective expected duration of a position)

Hai nguồn định nghĩa thời lượng dự kiến: **snapshot tường minh theo từng bước** `AssignedRouteStep.expected_duration` (§8.10) và **giá trị mặc định trực tiếp (live) của Operation** `Operation.default_expected_duration` (§8.5). Việc giám sát đánh giá **vị trí hiện tại (current position)** của số lượng theo đúng một giá trị hiệu lực, được xác định theo thứ tự ưu tiên sau:

- **Quantity Flow PLANNED**: `expected_duration` của Assigned Route Step **hiện tại** khi nó có giá trị; nếu không thì là `default_expected_duration` của Operation được ghi trên vị trí hiệu lực hiện tại. Bước hiện tại được suy ra từ lịch sử Movement bất biến, không bao giờ từ việc so sánh Area có trùng nhau hay không: lần đến (arrival) đã thiết lập vị trí hiện tại (`RECEIVED`, `TRANSFERRED`) hoặc đã hoàn thành một bước của route — khi đó số lượng **đúng route (on route)** tại bước đó — hoặc là một sai lệch đã được xác nhận và không tham chiếu bước nào — khi đó số lượng **lệch route (off route)** và không có bước hiện tại cho đến khi một lần đến sau đó hoàn thành một bước. Một sai lệch quay trở lại Area của một bước trước đó (một lần quay lại để Repair) không làm bước đó trở thành bước hiện tại một lần nữa: route vẫn kỳ vọng bước tiếp theo của nó, và Tracking trình bày bước đã biết là đã xong — số lượng lệch route không có bước hiện tại. Các sự kiện bên trong Area (gán Machine, trả khỏi Machine, `DONE`) giữ nguyên trạng thái mà lần đến của chúng đã thiết lập; một lần đến đã bị Undo được coi như chưa từng xảy ra; một flow con sau khi tách hoặc kết quả của một lần gộp kế thừa trạng thái của (các) nguồn của nó. Do đó, số lượng lệch route lấy giá trị mặc định của Operation.
- **Quantity Flow FLOATING**: `default_expected_duration` của Operation được ghi trên vị trí hiệu lực hiện tại.
- **Không có giá trị từ nguồn áp dụng**: không áp dụng thời lượng dự kiến nào, và không hiển thị cảnh báo thời lượng dự kiến nào. Không có thời lượng dự phòng (fallback) dựng sẵn nào.

Quy tắc:

- Giá trị của bước trong snapshot là tường minh và luôn được ưu tiên hơn giá trị mặc định của Operation; giá trị mặc định của Operation là một phương án dự phòng trực tiếp (live fallback) — việc thay đổi nó có hiệu lực ngay lập tức cho mọi vị trí dựa vào phương án dự phòng, không bao giờ làm thay đổi một snapshot Assigned Route, và không bao giờ được điền ngược vào một snapshot chỉ để mang theo giá trị dự phòng.
- **Thời lượng đã trôi qua là thời gian ở vị trí hiện tại**: `now − entered_at` của vị trí hiệu lực có tính đến nhánh (branch-aware) (§21 — chính là mốc `since` mà mọi màn hình giám sát vốn đã dùng để tính thời gian cho một vị trí). Không có bộ đếm thời gian, dấu thời gian hay trạng thái giám sát lưu bền nào được thêm vào cho việc này.
- Việc giám sát **chỉ mang tính tư vấn**: vượt quá thời lượng dự kiến tạo ra một điểm nhấn cảnh báo và không bao giờ chặn một lần quét, một lần chuyển, `DONE`, một thao tác Machine hay bất kỳ lệnh sản xuất nào khác.
- Khi một vị trí được hiển thị tổng hợp nhiều Quantity Flow hoặc nhiều phần (một vị trí trên Production Board, một dòng PN trong phần tổng quan của Area Board), vị trí đó được cảnh báo ngay khi **ít nhất một** phần được bao gồm đã vượt quá thời lượng dự kiến hiệu lực **của chính nó** — các thời lượng không bao giờ được lấy trung bình, và một phần mới hơn không bao giờ che giấu một phần đã quá hạn.
- Không có quy tắc thời gian lưu cố định nào (chẳng hạn ngưỡng `≥ 3 days` trên Production Board) thay thế cho một thời lượng dự kiến bị thiếu.

---

# 18. Stockroom và phân bổ khi hoàn tất (Stockroom and Completion Allocation)

Stockroom là Area kết thúc thông thường.

Khi số lượng được quét vào Stockroom:

- một Movement `STOCKED` được ghi lại,
- số lượng đó được xem là đã hoàn tất sản xuất,
- số lượng đó trở nên sẵn sàng cho Work Order Allocation.

Movement sản xuất và Work Order Allocation luôn tách biệt nhau.

---

## Thứ tự phân bổ (Allocation Order)

Hệ thống phải đề xuất phân bổ theo thứ tự demand chuẩn (canonical demand ordering):

1. Mức ưu tiên của Work Order Demand cao nhất do người quản lý xác định (Hot rank).
2. Trong cùng một mức ưu tiên:
   - demand **có** ngày đến hạn (due date) đứng trước, ngày đến hạn sớm nhất đứng trước;
   - demand **không có** ngày đến hạn đứng sau toàn bộ demand có ngày đến hạn;
   - demand không có ngày đến hạn được sắp xếp theo `received_date` của Work Order cha, cũ nhất đứng trước.

Các giá trị bằng nhau được phân định bằng một tiêu chí phá hòa (tie-breaker) ổn định và tất định, chẳng hạn thứ tự tạo Work Order Demand hoặc ID nội bộ. Tiêu chí phá hòa là một chi tiết triển khai, không phải quy tắc nghiệp vụ.

Thứ tự demand chuẩn này áp dụng ở mọi nơi có sắp xếp theo ngày đến hạn — đề xuất phân bổ, thứ tự công việc và các màn hình hiển thị được sắp xếp theo demand.

---

## Xác nhận nhận hàng (Receiving Confirmation)

Quy trình Stockroom thông thường nên là:

1. Quét Part Number (PN).
2. Nhập hoặc xác nhận số lượng đã hoàn tất.
3. Xem lại Work Order Allocation được đề xuất.
4. Xác nhận việc phân bổ.

Đề xuất phải hiển thị:

- Work Order bị ảnh hưởng,
- số lượng yêu cầu,
- số lượng đã phân bổ trước đó,
- số lượng còn thiếu,
- số lượng đề xuất.

Việc nhận hàng thường lệ không nên yêu cầu Manager phê duyệt.

Operator có thể xem lại và điều chỉnh Work Order Allocation được đề xuất trước khi xác nhận.

Admin và Manager có thể điều chỉnh Work Order Allocation bất cứ lúc nào.

Mọi thay đổi phải luôn có thể audit được.

Tổng phân bổ đang hoạt động phải bằng phần số lượng đã nhập kho đang được phân bổ và không bao giờ được vượt quá số lượng đã nhập kho khả dụng.

---

## Hoàn tất Work Order (Work Order Completion)

Một Work Order hoàn tất khi tất cả các bản ghi Work Order Demand của nó đã được phân bổ đủ.

Khi hoàn tất:

- Work Order rời khỏi các màn hình sản xuất đang hoạt động,
- Work Order vẫn có sẵn trong History (lịch sử),
- lịch sử Movement giữ nguyên không đổi,
- công việc phát sinh sau đó phải được thể hiện bằng Work Order Demand mới hoặc demand nội bộ mới, thay vì mở lại Movement lịch sử.

---

# 19. Worker Sessions

Việc nhận dạng Worker có thể cấu hình theo từng Area.

Các chế độ được hỗ trợ:

- không nhận dạng Worker,
- Worker cố định,
- phiên barcode (mã vạch) Worker (Worker barcode session).

Khi bật chế độ quét Worker:

- quét thẻ (badge) của một Worker sẽ kích hoạt Worker đó,
- các lần quét tiếp theo dùng Worker đang hoạt động,
- một lần quét Worker không bao giờ thay thế ngữ cảnh PN được quét gần nhất,
- quét thẻ của một Worker khác đang ở trạng thái hoạt động sẽ chuyển Worker đang hoạt động sang Worker đó ngay lập tức — không có bước đăng xuất,
- phiên kết thúc khi một Worker khác đăng nhập hoặc khi phiên hết hạn — không có nút đăng xuất; một thay đổi cấu hình làm phiên mất hiệu lực (Area rời khỏi chế độ phiên barcode Worker, Scan Station bị gán lại hoặc bị vô hiệu hóa, Worker bị vô hiệu hóa) cũng kết thúc phiên.

**Hết hạn.** Một Worker Session được tạo bằng cách quét thẻ sẽ hết hạn thông qua **thời gian chờ không hoạt động trượt (sliding inactivity timeout)**, không bao giờ theo ranh giới ca làm việc — không có khái niệm kết thúc ca và không có chức năng quản lý lịch ca:

- giá trị thời gian chờ được cấu hình trong Administration → Worker sessions: một giá trị chính sách mặc định cùng các giá trị ghi đè tùy chọn theo từng Area; giá trị này không bao giờ được suy ra từ số lượng Worker trong một Area;
- mọi tương tác sản xuất hợp lệ tại Scan Station đều làm mới thời gian chờ — một lần quét được phân giải thành công, một lệnh sản xuất đã được xác nhận, một lần quét thẻ hợp lệ; các lần quét không hợp lệ hoặc không xác định không bao giờ làm mới nó;
- khi hết hạn, **chỉ Scan Station bị chặn**: nó hiển thị một hộp thoại modal chặn, yêu cầu quét thẻ (`Worker session expired` / `Scan your badge to continue.`); mọi màn hình khác của ứng dụng không bị ảnh hưởng;
- một Scan Station ở chế độ phiên quét thẻ được mở khi chưa có phiên hoạt động sẽ hiển thị cùng yêu cầu quét thẻ mang tính chặn đó trước khi cho phép tương tác sản xuất;
- nếu một hộp thoại sản xuất đang mở vào lúc hết hạn, bản nháp và các lựa chọn của nó được giữ lại, nhưng việc xác nhận sản xuất vẫn bị chặn cho đến khi có một lần quét thẻ hợp lệ; sau đó quy trình tiếp tục từ chỗ đang dừng.

**Xác nhận bằng thẻ cho các thao tác nhạy cảm.** Mỗi thao tác trong ba thao tác nhạy cảm của Scan Station — `DONE` (Area completion), `QUEUE` (trả số lượng chưa hoàn thành về hàng chờ) và Undo — có **tùy chọn xác nhận bằng thẻ (badge-confirmation option)** riêng trong chính sách Worker sessions (Administration → Worker sessions), mặc định **bật**. Mọi thao tác nhạy cảm LUÔN kết thúc bằng một cổng xác nhận cuối (final confirmation gate); tùy chọn này chỉ quyết định liệu cổng đó có bắt buộc phải là một lần quét thẻ Worker hay không:

- trong một Area **phiên quét thẻ** có bật tùy chọn, thao tác yêu cầu quét thẻ Worker làm **bước cuối cùng sau bản tóm tắt xác nhận** — các thông tin chính của thao tác đang chờ vẫn được hiển thị, bất kỳ thẻ Worker ACTIVE nào cũng xác nhận được, thẻ nhận dạng Worker xác nhận (được ghi trên thao tác, và được đăng nhập giống hệt một lần quét thẻ bình thường — chuyển và làm mới phiên), thẻ không xác định hoặc không hoạt động bị từ chối và không ghi gì, và hủy cổng xác nhận sẽ quay về bản tóm tắt;
- trong một Area **Worker cố định hoặc tắt nhận dạng Worker** — và trong một Area phiên quét thẻ có tùy chọn bị tắt — cổng xác nhận là một **câu hỏi xác nhận cuối** nhắc lại các thông tin chính (không yêu cầu thẻ) — trình bày với tông thông tin cho DONE, và trình bày dạng cảnh báo cho thao tác trả về QUEUE và Undo;
- bản thân cổng xác nhận không bao giờ là tùy chọn: không có cấu hình nào loại bỏ bước cuối này;
- không bao giờ có gì được ghi lại trước khi cổng xác nhận hoàn tất — cổng xác nhận là một phần của việc xác nhận, không bao giờ là một thao tác ghi riêng.

Danh tính Worker là siêu dữ liệu phục vụ trách nhiệm giải trình (accountability metadata) và không bao giờ được quyết định tính đúng đắn nghiệp vụ.

**Không có Machine session** dưới bất kỳ hình thức nào: việc chọn Machine luôn là một phần của một thao tác gán một lần (§12, §15). Worker đang hoạt động phải luôn được hiển thị trên Scan Station.

Trạng thái phiên giúp giảm việc quét lặp lại nhưng không bao giờ được thay thế lịch sử Movement được lưu bền vững.

---

# 20. Vai trò và quyền (Roles and Permissions)

PartFlow dùng cơ chế phân quyền dựa trên vai trò.

Các vai trò **có tên và có thể chỉnh sửa**. Mỗi năng lực bên dưới là một **permission key**; một vai trò cấp một tập permission key và mỗi User giữ một vai trò. **Permission key là thẩm quyền duy nhất**: việc phân quyền được quyết định bởi các key mà vai trò của User cấp — không bao giờ bởi tên của vai trò — và không vai trò nào là siêu người dùng (superuser) ngầm định. Các danh sách Administrator, Manager và Operator bên dưới là **các vai trò ban đầu cùng các quyền được cấp ban đầu của chúng**, được khởi tạo (seed) đúng như đã liệt kê; một quản trị viên được ủy quyền có thể đổi tên các vai trò này hoặc thay đổi những gì bất kỳ vai trò nào cấp, và mọi thay đổi như vậy đều được ghi audit.

**Các câu nêu tên vai trò và cơ chế bảo vệ việc quản lý quyền.** "Admin" trong tài liệu hồ sơ này là cách viết tắt của vai trò Administrator. Ở bất cứ chỗ nào tài liệu này nêu tên một vai trò là bên thực hiện hoặc cấu hình một việc gì đó trong ứng dụng (ví dụ "Admin or Manager", "Admin-configured"), nó chỉ quyền được cấp ban đầu của vai trò đó: quyền mà thao tác yêu cầu mới là yếu tố quyết định. Việc thay đổi ai giữ một quyền sửa sai (correction permission) hoặc quyền quản lý các quyền sửa sai — bằng cách thay đổi quyền của một vai trò, hoặc vai trò, trạng thái hoạt động hay mật khẩu của một User — tự nó yêu cầu quyền quản lý các quyền sửa sai, và PartFlow không bao giờ cho phép một thay đổi khiến không còn User đang hoạt động nào có mật khẩu mà giữ quyền quản lý người dùng và vai trò, hoặc giữ quyền quản lý các quyền sửa sai.

**Thao tác tại Scan Station.** Các thao tác tại Scan Station được thực hiện bởi các Worker, những người không giữ vai trò nào (§7), trên một thiết bị Scan Station mà quản trị viên đã đăng ký (enroll) cho trạm đó; một thiết bị chưa đăng ký, hoặc đã đăng ký cho trạm khác, không thể thực hiện gì ở đó. Những gì một Scan Station đã đăng ký được phép làm — bao gồm Undo các lần quét gần đây đủ điều kiện (§16) — được quyết định bởi quyền của một vai trò duy nhất được áp dụng tại các Scan Station, ban đầu là vai trò Operator với các quyền được cấp ban đầu liệt kê bên dưới; danh tính Worker không bao giờ cấp hay hạn chế một thao tác.

Machine, Route Template (Planned Routes) và siêu dữ liệu PartNumber master là dữ liệu chủ sản xuất (production master data) — các chức năng quản lý vận hành, không phải quản trị hệ thống. Việc quản lý chúng **dựa trên quyền**: một chuyên viên sản xuất được ủy quyền — ví dụ quản lý sản xuất (Production Manager), kỹ sư quy trình (Process Engineer) hoặc quản lý bảo trì (Maintenance Manager) — có thể quản lý Machine, Route Template và siêu dữ liệu PartNumber master mà không cần là Administrator. Vai trò Administrator ban đầu giữ các năng lực này, nhưng chúng không phải độc quyền của Administrator, và Administration không có màn hình Machines, Route Templates hay Part Numbers trùng lặp (§21).

## Administrator

Vai trò Administrator ban đầu cấp các quyền:

- quản lý Department,
- quản lý Area,
- quản lý Operation,
- quản lý Machine (dùng chung với các vai trò sản xuất được ủy quyền — được quản lý qua Management → Machines, §21),
- quản lý Worker,
- quản lý người dùng và vai trò,
- quản lý Scan Station,
- quản lý cấu hình barcode,
- quản lý Route Template (dùng chung với các vai trò sản xuất được ủy quyền — được quản lý qua Management → Planned Routes, §21),
- quản lý siêu dữ liệu PartNumber master, bao gồm cả xóa vĩnh viễn (hard delete) (dùng chung với các vai trò sản xuất được ủy quyền — được quản lý qua Management → Part Numbers, §21),
- quản lý hành vi quét,
- quản lý chính sách Worker session,
- quản lý các quyền sửa sai,
- chỉnh sửa Work Order Demand,
- chỉnh sửa Work Order Allocation,
- thực hiện các sửa sai lịch sử được ủy quyền,
- cấu hình thiết lập hệ thống.

---

## Manager

Vai trò Manager ban đầu cấp các quyền:

- xem toàn bộ dữ liệu sản xuất hiện tại và lịch sử,
- tạo và chỉnh sửa Work Order,
- chỉnh sửa Work Order Demand,
- đặt mức ưu tiên cho Work Order Demand,
- sắp xếp lại các mục Hot,
- gán và chỉnh sửa Route,
- thực hiện sửa sai số lượng,
- chỉnh sửa Work Order Allocation,
- xử lý các tình huống sản xuất bất thường,
- xuất và in báo cáo.

---

## Operator

Vai trò Operator ban đầu cấp các quyền:

- quét barcode PN,
- quét barcode Machine,
- quét barcode Worker,
- nhận số lượng vào một Area,
- gán số lượng cho một Machine,
- xác nhận số lượng,
- hoàn tất sản xuất vào Stockroom,
- xác nhận phân bổ được đề xuất,
- xem lại và điều chỉnh phân bổ hoàn tất được đề xuất,
- Undo các lần quét gần đây đủ điều kiện.

Không có quyền nào ghi đè trực tiếp lên dữ liệu lịch sử: vai trò Operator ban đầu không giữ quyền sửa sai lịch sử nào, và mọi sửa sai đều dùng các cơ chế bù trừ có thể audit (§16) — lịch sử Movement luôn bất biến.

---

# 21. Các màn hình ứng dụng (Application Views)

## Scan Station

Scan Station là giao diện sản xuất cố định được gán cho một Area. Các trạm được truy cập theo địa chỉ web (URL): đường dẫn Scan Station trần (không kèm trạm cụ thể) hiển thị Station Selector (Station ID, Department, Area, các Operation được hỗ trợ, Area có Machine hay không) và không bao giờ tự động chuyển hướng; một Station ID không xác định hoặc không hoạt động là một lỗi tường minh, không bao giờ là một phương án dự phòng âm thầm. Station ID vẫn chỉ là một dòng chú thích chân trang mờ, không tương tác (bên cạnh nhãn chế độ và gợi ý `Ctrl+Shift+K: switch mode`); việc chuyển trạm đi qua URL của Station Selector, không bao giờ qua chân trang.

Yêu cầu:

- quy trình thông thường gói gọn trên một màn hình,
- không có điều hướng thông thường,
- ô nhập barcode lớn, luôn được focus, nằm ngay dưới phần đầu trang (header) và trải hết chiều rộng (barcode PN và barcode Worker; barcode Machine chỉ là lối tắt cho thao tác gán một lần; không có Action barcode),
- tự động focus lại ô nhập,
- phản hồi kiểm tra hợp lệ tức thì,
- hiển thị Department, danh tính/màu của Area, và các Operation,
- hiển thị thống kê Area ở header, là bề mặt tóm tắt Area duy nhất (Area có Machine: Total PNs, Total pcs, Queued, On machines, Done, Hot — đối soát theo Total pcs = Queued + On machines + Done; Area không có Machine: Total PNs, Total pcs, Processing, Done, Hot — đối soát theo Total pcs = Processing + Done; tông màu số theo ngữ nghĩa: Queued là cảnh báo (warning), On machines/Processing là thông tin (information), Done là thành công (success), Hot là lỗi (error)),
- hiển thị Worker đang hoạt động theo chế độ Worker ID của Area (§19): chế độ phiên quét thẻ hiển thị Worker đang hoạt động kèm đồng hồ đếm ngược thời gian phiên còn lại theo thời gian thực, Area Worker cố định hiển thị Worker đã cấu hình, và Area đã tắt nhận dạng Worker hoàn toàn không hiển thị phần tử Worker nào (danh tính Worker không tồn tại ở đó; chế độ vẫn được hiển thị trong Administration); một Area phiên quét thẻ không có phiên hoạt động — khi mở hoặc sau khi hết hạn — chỉ chặn Scan Station sau một modal quét thẻ, giữ lại bản nháp của bất kỳ hộp thoại nào đang mở trong khi chặn việc xác nhận sản xuất,
- hiển thị PN được quét gần nhất bên trong thẻ quét (scan card), với thao tác Undo được neo ở mép phải của nó (chỉ các thao tác PN đã hoàn tất mới trở thành PN được quét gần nhất; các lần quét Worker và các hộp thoại bị hủy không bao giờ thay thế nó),
- chỉ nhập số lượng khi cần,
- số lượng hiện tại của Area trong bố cục giám sát Area/Machine dùng chung (§ Area Board) — thẻ `In this Area now` ở bên trái, các thẻ Machine nằm trong một lưới bên phải tự xuống dòng bên trong chính lưới đó; không có vùng Machine đối với Area không có Machine; thẻ Machine hiển thị trạng thái vận hành được suy ra kèm thời gian đã trôi qua ở trạng thái đó (`running · 1h 24m`, được suy ra từ dấu thời gian thay đổi trạng thái — §8.6) và, khi đang bảo trì, ghi chú bảo trì cùng ngày dự kiến hoạt động trở lại,
- tách riêng số lượng trên Machine, đang chờ và đã xong (`Finished — ready to move`) (Area có Machine) hoặc các nhóm đang xử lý trực tiếp và đã xong (Area không có Machine) — số lượng đã xong thuộc về phần tóm tắt Area và thẻ Machine chỉ hiển thị số lượng đang thực sự được gán,
- các dòng PN trên thẻ Machine có hai thao tác riêng biệt `DONE` và `QUEUE` (§12),
- Undo được ủy quyền với bước xác nhận bằng bản tóm tắt (§16),
- phương án dự phòng nhập thủ công chấp nhận mọi giá trị PN không rỗng sau khi cắt khoảng trắng ở đầu và cuối và không chứa khoảng trắng bên trong (được chuẩn hóa thành PN chữ in hoa, §7 Part Number).

Không có Machine Session lưu bền, không có ngữ cảnh chờ đã được "nạp sẵn" (pending armed context) và không có danh sách Recent Scans.

---

## Production Board

Production Board là màn hình hiển thị chỉ đọc trên màn hình lớn cho một Department. Nó chỉ áp dụng cho toàn bộ Department: **không có chế độ lọc theo từng Area** — việc giám sát theo từng Area là trách nhiệm của Area Board, và hai màn hình này không bao giờ trùng lặp nhau.

Yêu cầu:

- đọc được từ xa,
- tự động phân trang,
- tự động xoay vòng trang với thời gian dừng **tỷ lệ với số dòng được hiển thị trên trang hiện tại** (mặc định 3 giây cho mỗi dòng được hiển thị, với thời gian dừng tối thiểu mỗi trang là 6 giây); chỉ báo đếm ngược xoay vòng dùng cùng hạn chót và cùng thời lượng mỗi trang; các giá trị này là cấu hình **theo từng Department** (Administration → Department display settings, §22 — không bao giờ là cấu hình toàn cục, không bao giờ là hằng số giao diện người dùng (UI) được viết cứng),
- số dòng mỗi trang thay đổi động,
- sắp xếp theo mức ưu tiên và ngày đến hạn tuân theo thứ tự demand chuẩn (§18),
- làm nổi bật mục quá hạn,
- hiển thị màu Area,
- hiển thị số lượng PN phân bố theo vị trí,
- thời gian ở Area hoặc Machine hiện tại, được hiển thị cho từng phần số lượng phân bố,
- hiển thị rõ số lượng scrap/hư hỏng mà không làm bảng trở nên khó đọc (số lượng scrap có không gian bố cục riêng và không bao giờ chồng lên các trường khác),
- dữ liệu trình bày Area và Machine tường minh: số lượng đang thực sự được gán cho Machine hiển thị Machine dưới dạng một chip gọn với cách diễn đạt trạng thái đầy đủ `on machine`; số lượng đã xong hiển thị Area hiện tại với trạng thái `done`/`ready`, không bao giờ là `on machine` và không bao giờ hiển thị Machine như bên đang thực hiện hiện tại,
- Work Order Number bên ngoài để trống được hiển thị là `—`,
- Part Number được hiển thị trên một dòng duy nhất.

Các cột đề xuất:

| No. | Part Number | Areas and Quantities · Time | Due Date | Total Days | Job Numbers |
|---|---|---|---|---|---|

Days Left được hiển thị bên trong cột Due Date dưới dạng một dòng phụ được làm nổi bật, thay vì là một cột riêng.

Ví dụ về phân bố kèm thời gian tại vị trí:

```text
Cut (3 · 3h 40m), Lathe 1 (4 · 2h 05m), Lathe 2 (2 · 1h 10m), Mill (6 · 45m)
```

Thời gian tại vị trí được làm nổi bật khi vượt quá thời lượng dự kiến hiệu lực của vị trí (§17 Effective expected duration — giá trị snapshot của Assigned Route Step hiện tại, nếu không có thì là giá trị mặc định của Operation; không có giá trị nào khi cả hai đều chưa được cấu hình). Việc làm nổi bật chỉ mang tính tư vấn, và một vị trí gộp nhiều phần số lượng sẽ được làm nổi bật ngay khi bất kỳ phần nào trong đó vượt quá thời lượng dự kiến của chính nó.

---

## Area Board

Area Board là một màn hình Management với hai chế độ nằm sau một dải tab duy nhất: **All Areas overview** và **per-Area detail** (GUI_DESIGN §6). Không có màn hình tóm tắt riêng: nội dung tóm tắt liên Area chính là All Areas overview được mô tả bên dưới.

### All Areas overview

All Areas overview cung cấp cái nhìn tổng quan vận hành được nhóm theo Area.

Mỗi Area nên hiển thị:

- tên và mô tả Area,
- các Operation được hỗ trợ,
- tổng số lượng vật lý,
- số lượng đang chờ,
- việc gán Machine,
- danh sách PN,
- mức ưu tiên,
- ngày đến hạn,
- tìm kiếm và sắp xếp.

Bố cục có thể cuộn ngang khi không chứa vừa tất cả các Area.

### Per-Area detail

Per-Area detail cung cấp một màn hình tập trung vào hoạt động sản xuất hiện do một Area sở hữu. Nó dùng cùng bố cục giám sát Area/Machine dùng chung và cùng các thành phần trình bày như Scan Station (thẻ tóm tắt `In this Area now` ở bên trái, các thẻ giám sát Machine trong lưới bên phải; thẻ tóm tắt trải hết chiều rộng đối với Area không có Machine) — ở dạng chỉ đọc, không có các nút thao tác của Scan Station, và không có sai khác về hình ảnh giữa hai màn hình.

Màn hình này nên hiển thị:

- PN,
- tổng số lượng,
- hàng chờ của Area,
- Operation,
- phân bố trên các Machine,
- số lượng đã xong (`READY_TO_TRANSFER`), được phân biệt với số lượng đang chờ, trên Machine và đang xử lý trực tiếp — hiển thị trong phần tóm tắt Area, không bao giờ bên trong thẻ Machine và không bao giờ hiển thị là Stocked,
- các Work Order đang hoạt động có liên quan,
- Job Number,
- ngày đến hạn,
- mức ưu tiên,
- thời gian ở Area,
- số lượng scrap trong các bản tóm tắt PN,
- tìm kiếm và sắp xếp.

---

## Machines

Machines là màn hình quản lý vòng đời, bảo trì và nhận dạng tài sản của Machine — dữ liệu chủ sản xuất do các vai trò sản xuất được ủy quyền quản lý (§20), được nhóm trong Management cùng với Area Board, Tracking, Work Orders, Planned Routes, Part Numbers và Priority. Trạng thái running và idle được suy ra từ số lượng được gán; maintenance là trạng thái duy nhất được đặt thủ công (§8.6).

Màn hình này phải cung cấp:

- một bảng các Machine đang hoạt động với các cột có thể sắp xếp: danh tính (tên hiển thị và Area), trạng thái vận hành được suy ra kèm thời gian đã trôi qua ở trạng thái đó, các phần PN hiện đang được gán kèm số lượng, siêu dữ liệu tài sản (Asset Tag, nhà sản xuất, model), và một **công tắc Maintenance On/Off** trên mỗi dòng, mở các hộp thoại bắt đầu bảo trì (ghi chú tùy chọn, ngày dự kiến hoạt động trở lại tùy chọn) và kết thúc bảo trì hiện có — không có cột thao tác riêng,
- chỉnh sửa tại chỗ ghi chú bảo trì và ngày dự kiến hoạt động trở lại từ hộp thoại Edit Machine trong khi Machine đang bảo trì (§8.6) — không bao giờ là một thay đổi trạng thái,
- kích hoạt toàn dòng: chọn một dòng Machine sẽ mở hộp thoại Edit Machine,
- một bảng Retired Machines riêng với các cột dữ liệu có thể sắp xếp: tên, `Retired on YYYY-MM-DD`, siêu dữ liệu tài sản, ghi chú — dùng để hiển thị lịch sử và báo cáo, không có cột thao tác; kích hoạt toàn dòng sẽ mở hộp thoại chỉ đọc Retired Machine Details, và **Reactivate** (§8.6 return to service) chỉ nằm bên trong hộp thoại đó; Machine đã ngừng sử dụng vẫn không bao giờ xuất hiện trong các lựa chọn gán và không nhận lần quét mới nào,
- một hộp thoại New Machine / Edit Machine: tên hiển thị (bắt buộc), Area (chọn được đối với Machine mới, cố định đối với Machine đã có — §8.6), danh tính Machine chỉ đọc — Asset Tag được gán tự động khi tạo theo định dạng đã cấu hình (§8.6) và barcode được suy ra từ nó (`PF:MACHINE:<asset-tag>`, §10; không giá trị nào trong hai giá trị này từng được nhập hay chỉnh sửa) — nhà sản xuất, model, số sê-ri, ngày lắp đặt và ghi chú tùy chọn, cùng các sự kiện audit vòng đời chỉ ghi thêm (append-only) của Machine (`RETIRED` / `REACTIVATED` — §8.6),
- một nhãn barcode Machine có thể in từ hộp thoại Edit Machine: tên hiển thị, Asset Tag và barcode `PF:MACHINE:<asset-tag>`,
- tạo theo từng bước: một Machine mới chỉ được thêm sau một bản tóm tắt và một xác nhận tường minh cuối cùng nói rõ rằng bản ghi Machine không thể bị xóa — chỉ có thể cho ngừng sử dụng,
- cho ngừng sử dụng (retire) là thao tác **Danger Zone** của hộp thoại Edit: `Retire…` yêu cầu xác nhận bằng cách gõ lại định danh — luôn là Asset Tag, không bao giờ là tên hiển thị có thể tái sử dụng — sau một cảnh báo tường minh về hậu quả, và một xác nhận tường minh cuối cùng rằng việc ngừng sử dụng được ghi vĩnh viễn trong vòng đời Machine,
- việc ngừng sử dụng bị chặn khi vẫn còn số lượng đang được gán; số lượng đó phải hoàn tất hoặc được chuyển đi qua quy trình sản xuất thông thường trước,
- kích hoạt lại (reactivate) một Machine đã ngừng sử dụng theo §8.6: cùng một máy vật lý trên cùng bản ghi, bắt buộc có lý do, một Area hoạt động mới tùy chọn chỉ áp dụng từ thời điểm đó trở đi, bị chặn khi danh tính đã được cấp lại (reissued) (Asset Tag — cũng chính là barcode — hoặc số sê-ri) và khi trùng tên với một Machine đang hoạt động trong Area đích, đưa Machine trở lại ở trạng thái Idle sau một xác nhận tường minh cuối cùng rằng việc kích hoạt lại được ghi vĩnh viễn trong vòng đời Machine,
- hướng dẫn thay thế theo §7 Machine: cho ngừng sử dụng bản ghi cũ, tạo một bản ghi mới với danh tính riêng và Asset Tag mới được gán tự động của riêng nó (cùng barcode của nó); tên hiển thị có thể được tái sử dụng (là duy nhất trong số các Machine đang hoạt động của một Area).

Việc quản lý Machine chỉ tập trung vào vòng đời, bảo trì và nhận dạng tài sản. PartFlow không phải là hệ thống quản lý bảo trì được vi tính hóa (CMMS): không có phụ tùng thay thế, lịch bảo trì, hợp đồng dịch vụ hay kế toán chi phí.

---

## Tracking

Tracking là giao diện quản lý chính.

Manager phải có thể tìm kiếm và lọc theo:

- PN,
- Work Order,
- Job Number,
- Area,
- Operation,
- Machine,
- Request Type,
- mức ưu tiên,
- trạng thái,
- ngày đến hạn.

Màn hình PN được chọn phải hiển thị:

- dữ liệu chủ của PN,
- barcode,
- hình ảnh và revision (phiên bản),
- Work Order Demand đang hoạt động,
- số lượng yêu cầu theo Work Order,
- số lượng đã phân bổ theo Work Order,
- số lượng còn thiếu theo Work Order,
- số lượng hiện tại theo Area,
- việc gán Machine hiện tại,
- số lượng đã xong (`READY_TO_TRANSFER`) theo Area, khác biệt với việc gán Machine đang hoạt động, xử lý trực tiếp và `STOCKED` — với `AREA_COMPLETED` được hiển thị như một Movement bất biến trong lịch sử và cách diễn đạt dành cho Operator như `Completed processing at Lathe — ready to transfer` trong các bản tóm tắt; kệ chứa hàng đã xong không bao giờ xuất hiện như một Route Step,
- các Quantity Flow cùng Route Mode của chúng,
- Planned Routes và các vết route thực tế của Floating Route,
- lịch sử Movement thực tế,
- lịch sử scrap và số lượng scrap tích lũy,
- thời gian ở mỗi Area,
- số lượng đã nhập kho,
- lịch sử Allocation,
- lịch sử sửa sai,
- hiển thị PN bình thường cho các PN có bản ghi siêu dữ liệu master đã bị xóa: chuỗi PN chuẩn và toàn bộ lịch sử sản xuất vẫn hiển thị đầy đủ; chỉ có siêu dữ liệu lấy từ master (tên, hình ảnh, revision, ánh xạ sang hệ thống hoạch định nguồn lực doanh nghiệp (ERP)) là vắng mặt (§8.1),
- Work Order Number bên ngoài để trống được hiển thị là `—`.

Việc trực quan hóa route hỗ trợ cả hai chế độ. Với Planned Route, nó phải phân biệt các bước đã hoàn tất, đang hoạt động, đang chờ và trong tương lai, cùng các sai lệch (deviation). Với Floating Route, nó suy ra vết thực tế từ lịch sử Movement, hiển thị các Area lặp lại, hiển thị các luồng đã tách một cách độc lập, và đánh dấu tường minh các lần chuyển Repair, ví dụ:

```text
A → B → C → D → B
                ⟲ REPAIR
```

Nó không được ngụ ý rằng toàn bộ PN đang ở cùng một Route Step.

---

## Work Orders

Work Orders là màn hình quản lý để nhập Work Order thủ công và nhập từ tệp (quy trình Work Order Intake, §13, bao gồm §13 *File import*). Đây là một màn hình quản lý dùng giao diện sáng (light theme). Danh sách Work Order trải hết chiều rộng màn hình, ô tìm kiếm của nó và thao tác New Work Order dùng chung một hàng thanh công cụ, và chọn một dòng Work Order sẽ mở hộp thoại Work Order Details.

Màn hình phải hỗ trợ quy trình tối thiểu đã được xác nhận:

1. Tạo hoặc tìm một Work Order.
2. Thêm hoặc cập nhật một hoặc nhiều bản ghi Work Order Demand.
3. Tìm hoặc tạo PartNumber.
4. Tạo barcode PN khi PN là mới.
5. Nhập: Work Order Number (tùy chọn — số để trống được lưu là NULL trên một Work Order nội bộ và hiển thị là `—`, §7 Work Order), ngày nhận (received date) (mặc định là ngày hiện tại), PN (bất kỳ văn bản PN nào không rỗng sau khi cắt khoảng trắng ở đầu và cuối và không chứa khoảng trắng bên trong, được chuẩn hóa thành chữ in hoa — được tạo ở lần dùng đầu tiên, §8.1), Request Type (mặc định `NEW`), số lượng yêu cầu, ngày đến hạn (tùy chọn — thiếu ngày đến hạn là dữ liệu hợp lệ, §8.3), mức ưu tiên khi áp dụng, các Job Number bên ngoài, và người yêu cầu, lý do và ghi chú khi áp dụng.
6. Lưu demand nghiệp vụ mà không tự động tạo số lượng sản xuất.
7. Cung cấp một thao tác `Release to production` riêng và tường minh, tuân theo các bước phát hành trong §13.

Khi phát hành vào sản xuất (production release), màn hình phải xác nhận số lượng phát hành, Route Mode (mặc định là Floating; chỉ chọn Route khi là Planned), cùng Area và Operation bắt đầu đã cấu hình, đồng thời hiển thị Quantity Flow, Route Mode, Area, số lượng và Movement `RECEIVED` được tạo ra.

Completed Work Orders (mọi Work Order Demand đã được phân bổ đủ, §8.2) không bao giờ xuất hiện trong danh sách đang hoạt động. Chúng nằm trên một trang lịch sử chỉ đọc chuyên biệt là **Completed Work Orders**, truy cập từ danh sách: được sắp xếp và lọc mặc định theo ngày hoàn tất (done date) (`completed_at`, mới nhất trước, trong một khoảng mặc định có giới hạn), với ô tìm kiếm riêng (Work Order Number, PN, Job Number), bộ lọc khoảng ngày hoàn tất, bộ lọc kết quả so với hạn (due outcome) và phân trang tăng dần. Lịch sử được lưu giữ vĩnh viễn và không giới hạn, vì vậy việc tìm kiếm, lọc, sắp xếp và phân trang được thực hiện phía máy chủ; trang này không cung cấp thao tác nhập, chỉnh sửa demand hay phát hành; người dùng giữ quyền Edit Work Order Allocation vẫn có các thao tác phân bổ của §8.12 / §18. Khi tìm kiếm trong danh sách đang hoạt động không có kết quả, màn hình chỉ người dùng sang lịch sử đã hoàn tất, và việc nhập số của một Work Order đã hoàn tất trong New Work Order sẽ thông báo rằng Work Order đó đã hoàn tất và mở chi tiết chỉ đọc của nó thay vì tạo bản trùng (tính duy nhất của Work Order Number trải trên toàn bộ lịch sử, §8.2).

Nếu PN đã có số lượng đang hoạt động, màn hình phải hiển thị phân bố hiện có và yêu cầu xác nhận tường minh về ý định; nó không bao giờ được tự động tạo thêm số lượng vật lý hoặc gộp các Quantity Flow.

Work Order Intake không được phát triển thành chức năng kiểu ERP về khách hàng, định giá, lập hóa đơn, vận chuyển, mua hàng hay kế toán.

---

## Planned Routes

Planned Routes là màn hình quản lý các định nghĩa route có thể tái sử dụng (`RouteTemplate`, §8.8) — dữ liệu chủ sản xuất do các vai trò sản xuất được ủy quyền quản lý (§20) trong nhóm Management. Tên gọi này giữ các template tách biệt rõ ràng với các vết route thực tế của Floating Route hiển thị trong Tracking.

Màn hình này phải cung cấp:

- một danh sách có thể tìm kiếm gồm các route template đang hoạt động — tên và mô tả, chuỗi bước Area có thứ tự ở dạng gọn được trình bày thành các chip tô màu theo Area, trạng thái (Active kèm ngày cập nhật) và mức sử dụng — cùng một bảng riêng gồm các template đã lưu trữ (archive) (kèm ngày lưu trữ),
- mức sử dụng của từng template: các Quantity Flow đã được phát hành với template đó (flow id, PN, ngày phát hành), hoặc `Never used`,
- kích hoạt toàn dòng: chọn một dòng template đang hoạt động sẽ mở hộp thoại tạo/chỉnh sửa; các thao tác nhân bản, và lưu trữ (đối với template đã từng được dùng) hoặc xóa (chỉ đối với template chưa từng được dùng), nằm bên trong hộp thoại đó — template đã lưu trữ chỉ cung cấp thao tác nhân bản từ bảng của chúng,
- một hộp thoại tạo/chỉnh sửa: tên, mô tả và các bước có thứ tự (Area, Operation, thời lượng dự kiến (expected duration) mang tính tư vấn, Machine ưa dùng (preferred Machine) tùy chọn, hướng dẫn tùy chọn) với việc sắp xếp lại tường minh (kéo-thả cùng các nút Up/Down) và thêm/xóa bước; ô chọn Operation được giới hạn trong các Operation của Area đã chọn, và ô chọn Machine ưa dùng đưa ra các Machine đang hoạt động của Area, được tham chiếu bằng id Machine ổn định (`preferred_machine_id`, §8.9) — không bao giờ bằng tên hiển thị có thể tái sử dụng,
- việc lưu trữ một template đã từng được dùng được bảo vệ bằng xác nhận bằng cách gõ lại (chính xác tên route) sau một cảnh báo tường minh về hậu quả; việc xóa một template chưa từng được dùng vẫn chỉ là một xác nhận thông thường,
- một ghi chú tường minh khi chỉnh sửa một template đã được dùng: thay đổi chỉ áp dụng cho các lần gán trong tương lai; các Quantity Flow đã phát hành giữ nguyên snapshot Assigned Route của chúng, và một Assigned Route đang trong sản xuất được thay đổi riêng trong quy trình được ghi audit của riêng nó, kèm một lý do (Tracking → Edit assigned Route),
- các template đã lưu trữ được hiển thị trong ngữ cảnh lịch sử nhưng không bao giờ được đưa ra cho việc gán route mới.

Không có hệ thống phiên bản hóa template riêng — các snapshot Assigned Route bảo toàn các định nghĩa lịch sử, và Planned Routes hướng dẫn sản xuất mà không bao giờ thay thế hoặc viết lại lịch sử Movement thực tế.

---

## Part Numbers

Part Numbers là màn hình quản lý siêu dữ liệu PartNumber master (§8.1) — dữ liệu chủ sản xuất do các vai trò sản xuất được ủy quyền quản lý (§20) trong nhóm Management. Bản thân chuỗi PN chuẩn vẫn là danh tính sản xuất ổn định: màn hình này duy trì các bản ghi siêu dữ liệu tùy chọn làm phong phú thêm cho một PN (tên/mô tả, hình ảnh, revision mang tính thông tin, ánh xạ ERP) và không bao giờ là điều kiện để được dùng trong sản xuất — một PN chuẩn vẫn dùng được dù có hay không có bản ghi master.

Màn hình này phải cung cấp:

- một danh sách có thể tìm kiếm gồm các bản ghi PartNumber master (PN, tên/mô tả, revision, ERP id), hiển thị cho mỗi bản ghi: hình ảnh PN (ảnh giữ chỗ PN mặc định dùng chung khi chưa tải lên ảnh tùy chỉnh), PN chuẩn ở dạng chữ in hoa, tên/mô tả, revision mang tính thông tin, ERP id và giá trị barcode PN được suy ra (`PF:PN:<part-number>`, §10) — barcode được suy ra từ PN chuẩn và không bao giờ là một giá trị có thể chỉnh sửa độc lập,
- kích hoạt toàn dòng: chọn một bản ghi sẽ mở hộp thoại chỉnh sửa siêu dữ liệu,
- tạo bản ghi master trước lần dùng đầu tiên trong sản xuất: PN được nhập sẽ được chuẩn hóa (cắt khoảng trắng ở đầu và cuối, từ chối khoảng trắng bên trong, chuyển thành chữ in hoa — §7 Part Number), và mỗi PN chuẩn chỉ có thể có một bản ghi master; việc tạo khi dùng lần đầu ở bước tiếp nhận (intake) (§8.1) không bị ảnh hưởng,
- chỉnh sửa siêu dữ liệu: tên/mô tả, revision mang tính thông tin, ERP id, và tải lên, thay đổi hoặc gỡ bỏ hình ảnh PN (gỡ bỏ sẽ trở về ảnh giữ chỗ mặc định dùng chung),
- xem và in một nhãn barcode PN đơn giản: barcode `PF:PN:<part-number>` với văn bản PN bên dưới — không có cấu hình barcode bổ sung,
- xóa vĩnh viễn (hard delete) một bản ghi master theo §8.1/§28: việc xóa chỉ gỡ bỏ siêu dữ liệu, không bao giờ lan sang WorkOrderDemand, QuantityFlow, PartMovement, Allocation hay lịch sử, mọi bề mặt vẫn hiển thị PN chuẩn bình thường, và sau này có thể tạo lại một bản ghi cho cùng PN chuẩn đó. Không có lưu trữ (archive) PN, không có xóa mềm (soft delete) và không có vòng đời active/inactive.

---

## Quản lý Priority (Priority Management)

Priority thuộc về Work Order Demand.

Hot list được quản lý trong phạm vi Department:

1. Hiển thị các Work Order Demand Hot được sắp xếp theo hạng ưu tiên tường minh.
2. Thêm Work Order Demand vào Hot list bằng cách tìm kiếm và chọn, hoặc bằng cách quét barcode PN. Một barcode PN được quét sẽ được phân giải một cách tất định: không có Work Order Demand đủ điều kiện nào thì không thêm gì; có đúng một thì thêm trực tiếp; có nhiều hơn một thì lọc danh sách ứng viên theo PN đó và yêu cầu chọn tường minh Work Order Demand cụ thể — không bao giờ đoán theo kết quả khớp đầu tiên.
3. Nếu một PN có nhiều bản ghi Work Order Demand đang hoạt động, mỗi Work Order Demand được chọn và xếp hạng riêng.
4. Mặc định thêm các mục Hot mới vào cuối danh sách; việc thêm vào cuối được áp dụng trực tiếp.
5. Cho phép sắp xếp lại bằng kéo-thả.
6. Chỉ cho phép gỡ một mục khỏi Hot list sau một xác nhận tường minh nêu rõ PN và Work Order Demand; hủy thì không thay đổi gì. Sau khi xác nhận, các hạng còn lại được dồn lên để lấp chỗ trống.
7. Yêu cầu xác nhận tường minh trước khi áp dụng bất kỳ thao tác nào làm thay đổi thứ tự của các mục Hot hiện có — kéo-thả, Move Up, Move Down, Undo và Redo. Xác nhận nêu rõ PN và Work Order Demand bị ảnh hưởng, hạng trước đó, hạng mới được đề xuất và loại thao tác; hủy sẽ giữ nguyên danh sách và cả hai lịch sử, và danh sách hiển thị không bao giờ được đánh số lại trước khi xác nhận.
8. Áp dụng mọi thay đổi Hot list đã được xác nhận và ghi lại thay đổi đó trong nhật ký audit.
9. Cung cấp Undo và Redo cho các thay đổi Hot list thay vì một bước lưu-hoặc-hủy riêng; việc lùi lại hoặc tiến tới tự nó là một thay đổi thứ tự và yêu cầu cùng một bước xác nhận như trên. Độ sâu Undo và Redo không giới hạn trong phiên ứng dụng hiện tại — không có giới hạn về số lượng; lịch sử kết thúc cùng với phiên.
10. Dùng hạng đã lưu làm mức ưu tiên cao nhất cho công việc và cho phân bổ.
11. Một mục tự động rời khỏi Hot list khi Work Order Demand của nó trở nên không hoạt động (§14) — tức là đã được phân bổ đủ, bao gồm cả trường hợp Work Order của nó hoàn tất (§18) — trong cùng transaction với thay đổi đã làm nó không hoạt động (một lần phân bổ, hoặc một lần chỉnh sửa demand làm giảm số lượng yêu cầu xuống bằng số lượng đã phân bổ). Không hỏi xác nhận: thao tác kích hoạt chính là thao tác đã được xác nhận. Các hạng còn lại được dồn lên để lấp chỗ trống và mọi thay đổi hạng được ghi audit như một lần gỡ tự động kèm nguyên nhân của nó (§28). Một thao tác đảo ngược hoặc một lần tăng số lượng làm demand hoạt động trở lại sẽ không thêm lại mục đó.

Nhiều Work Order yêu cầu cùng một PN có thể có các mức ưu tiên khác nhau.

---

## Administration

Administration tập trung vào quản trị hệ thống:

- Department,
- Area,
- Operation,
- Scan Station,
- Worker (hồ sơ — tên, barcode thẻ, ảnh đại diện, trạng thái hoạt động; §8.13 — được giữ tách biệt với tài khoản người dùng, không bao giờ gộp chung),
- người dùng,
- vai trò,
- quyền,
- cấu hình barcode (lược đồ tiền tố `PF:` và định dạng Asset Tag của Machine — tiền tố + chuỗi số tuần tự được đệm số 0 ở đầu, §8.6, §10),
- hành vi quét,
- chính sách Worker session (thời gian chờ không hoạt động trượt của phiên quét thẻ — một giá trị mặc định cùng các giá trị ghi đè theo từng Area — và các tùy chọn xác nhận bằng thẻ của `DONE`, thao tác trả về `QUEUE` và Undo, §19),
- thiết lập hiển thị Department (theo từng Department — bao gồm thời gian xoay vòng của Production Board, §21),
- công việc bảo trì lưu trữ (archive) và xóa dọn (purge) lịch sử cùng các thiết lập lưu giữ (retention) (§28 Administrative Archival and Purge),
- thiết lập ứng dụng.

Machine, Route Template và siêu dữ liệu PartNumber master không phải là màn hình của Administration: chúng là dữ liệu chủ sản xuất được quản lý dựa trên quyền trong Management → Machines, Management → Planned Routes và Management → Part Numbers (§20), không có màn hình Administration trùng lặp. Machine vẫn là một phần của điều kiện tiên quyết thiết lập môi trường tối thiểu — được cấu hình qua Management → Machines trước khi chạy sản xuất thực.

Các quy trình quản trị phải luôn tách biệt với việc quét sản xuất thông thường; các thiết lập lưu giữ thuộc về Administration/cấu hình, không bao giờ nằm trong logic quy trình sản xuất.

---

# 22. Phạm vi Department (Department Scope)

Lần triển khai đầu tiên nhắm tới Machine Shop.

Các Department trong tương lai có thể gồm:

- Purchasing,
- Assembly,
- Production,
- Outsourcing,
- Stockroom,
- Quality Control.

Logic domain cốt lõi không được hard-code các giả định riêng của Machine Shop.

Cấu hình Department có thể định nghĩa:

- các Area,
- các Operation,
- các Machine,
- các Route,
- Area bắt đầu,
- Area cuối (terminal Area),
- hành vi quét,
- yêu cầu về Worker,
- thiết lập hiển thị.

---

# 23. Ranh giới ERP (ERP Boundary)

ERP sở hữu việc hoạch định nghiệp vụ và dữ liệu master gốc.

PartFlow sở hữu việc theo dõi sản xuất.

Quy tắc:

- PartFlow phải hoạt động được khi không có kết nối ERP.
- ID của ERP phải tách biệt với ID nội bộ.
- Việc import từ ERP nên có tính idempotent (chạy lại nhiều lần cho cùng một kết quả).
- Mô hình dữ liệu phản hồi của ERP không được rò rỉ vào logic domain.
- Không bao giờ được giả định định dạng của PN và Work Order Number.
- Lịch sử sản xuất do PartFlow sở hữu.
- Thay đổi phía ERP không được xóa lịch sử Movement cục bộ.
- Modify intake và các movement Repair vẫn là khái niệm hợp lệ của PartFlow ngay cả khi ERP không mô hình hóa chúng.

---

# 24. Kiến trúc ứng dụng (Application Architecture)

Stack ưu tiên:

- React hoặc Next.js
- FastAPI
- PostgreSQL

Kiến trúc:

```text
Presentation
    ↓
Application
    ↓
Domain
    ↓
Infrastructure
```

Quy tắc:

- Giữ route và controller mỏng.
- Đặt việc điều phối quét (scan orchestration) trong lớp Application.
- Giữ quy tắc nghiệp vụ trong Domain và Application.
- Domain không được phụ thuộc vào framework.
- Infrastructure không được điều khiển luồng nghiệp vụ.
- Dùng transaction cho các thao tác ghi khi quét.
- Dùng ràng buộc cơ sở dữ liệu cho tính toàn vẹn của định danh và số lượng.
- Bảo toàn lịch sử Movement bất biến.
- Tránh trạng thái toàn cục không cần thiết.
- Giữ phần tích hợp ERP tách biệt.

---

# 25. Yêu cầu về transaction (Transaction Requirements)

Mọi thao tác ghi sản xuất phải mang tính nguyên tử (atomic).

Một transaction quét có thể bao gồm:

1. Phân tích (parse) barcode.
2. Kiểm tra trạng thái thực thể.
3. Kiểm tra quyền.
4. Xác định ngữ cảnh ScanSession (Worker) và dữ liệu nhập của hộp thoại một lần (one-shot).
5. Xác định Quantity Flow nguồn (được chọn tường minh khi có nhiều Quantity Flow hợp lệ).
6. Kiểm tra số lượng khả dụng.
7. Kiểm tra Area, Operation, Machine và Route.
8. Ghi Movement.
9. Cập nhật hoặc dựng lại trạng thái hiện tại được suy ra.
10. Cập nhật các projection.
11. Commit.

Nếu bất kỳ bước bắt buộc nào thất bại, toàn bộ transaction phải được rollback.

Hệ thống không bao giờ được ghi một Movement sản xuất một cách dở dang.

---

# 26. Ghi log (Logging)

Log phải trả lời được:

- Chuyện gì đã xảy ra?
- PN nào?
- Số lượng nào và Quantity Flow nào?
- Work Order Demand nào có liên quan, nếu có?
- Area nào?
- Operation nào?
- Machine nào?
- Worker nào?
- Scan Station nào?
- Vì sao thất bại?
- Hành động đó đã bị đảo ngược hoặc được sửa chưa?

Tránh log ồn ào hoặc trùng lặp.

Không hiển thị exception nội bộ thô cho người vận hành.

---

# 27. Báo cáo (Reporting)

Hệ thống nên hỗ trợ:

- trạng thái Work Order đang hoạt động,
- Work Order Demand đang hoạt động,
- trạng thái PN đang hoạt động,
- số lượng theo Area,
- số lượng theo Machine,
- hàng chờ của Area,
- demand quá hạn,
- demand Hot và demand theo mức ưu tiên,
- Route được gán so với đường đi thực tế,
- lịch sử Movement,
- thời gian ở Area,
- thời gian ở Machine,
- số lượng đã nhập kho,
- Work Order Allocation,
- lịch sử Work Order đã hoàn tất,
- lịch sử sai lệch Route,
- lịch sử sửa sai (correction).

Báo cáo phải phân biệt được:

- demand nghiệp vụ,
- trạng thái số lượng sản xuất,
- lịch sử Movement,
- Allocation khi hoàn tất.

---

# 28. Audit và toàn vẹn dữ liệu (Audit and Data Integrity)

Hệ thống phải lưu giữ nhật ký audit đầy đủ cho:

- việc tạo và sửa Work Order (một lần import ghi lại kênh tiếp nhận của nó),
- việc tạo và sửa Work Order Demand,
- thay đổi mức ưu tiên,
- việc gán Route,
- việc sửa Route,
- việc tách và gộp số lượng,
- việc chuyển giữa các Area,
- việc gán Machine,
- việc hoàn tất vào Stockroom,
- Work Order Allocation,
- việc sửa sai Allocation,
- điều chỉnh số lượng (kể cả việc thêm số lượng có audit),
- các movement Repair,
- các thao tác Scrap,
- Undo,
- Worker Session,
- thay đổi cấu hình quản trị,
- các thao tác lưu trữ (archive) và xóa dọn (purge) quản trị (ai, khi nào, phạm vi, lý do).

Bản ghi sản xuất lịch sử không bao giờ biến mất qua các quy trình thông thường của ứng dụng — trong lúc vận hành sản xuất bình thường, dữ liệu chỉ được ghi thêm (**bất biến trong lúc vận hành sản xuất**).

Khi khả thi, ràng buộc cơ sở dữ liệu nên đảm bảo:

- PN là duy nhất,
- Work Order Number là duy nhất ở những nơi quy tắc nghiệp vụ yêu cầu,
- giá trị barcode là duy nhất,
- quan hệ Machine với Area là duy nhất,
- số lượng không âm,
- quan hệ Area/Machine hợp lệ,
- allocation không vượt quá số lượng tồn kho khả dụng,
- định danh sự kiện gửi lần quét là idempotent (`device_event_id`).

---

## Lưu trữ và xóa dọn quản trị (Administrative Archival and Purge)

Vận hành sản xuất bình thường chỉ ghi thêm (append-only) và không bao giờ âm thầm ghi đè hoặc xóa lịch sử. Tách biệt với điều đó, các Administrator có **thẩm quyền bảo trì tường minh trong mọi môi trường**: việc bảo trì lưu trữ và xóa dọn tường minh của Admin được mô tả dưới đây.

**Xóa master PartNumber.** Bản ghi master PartNumber là metadata hiện hành tùy chọn (§8.1); mỗi bản ghi sản xuất tự giữ giá trị PN chuẩn của riêng nó. Vì vậy, một người dùng được ủy quyền có thể **xóa vĩnh viễn (hard delete)** một bản ghi master PN qua Management → Part Numbers (§20, §21) — ví dụ một mục rác hoặc mục thử nghiệm, hoặc metadata đơn giản là không còn cần nữa — mà không mất định danh sản xuất hay định danh lịch sử nào:

- việc xóa master PN không được xóa hoặc làm thay đổi WorkOrderDemand, QuantityFlow, PartMovement, Allocation hay bất kỳ lịch sử nào;
- việc xóa không bao giờ lan (cascade) sang dữ liệu sản xuất;
- các màn hình lịch sử tiếp tục hiển thị chuỗi PN chuẩn bình thường — chỉ metadata lấy từ master (tên, hình ảnh, revision, ánh xạ ERP) không còn;
- PN đã xóa biến mất khỏi việc tra cứu đang hoạt động dựa trên metadata; vẫn có thể tiếp nhận (intake) PN đó (tạo ở lần dùng đầu tiên, §8.1);
- nếu PN xuất hiện lại sau này, có thể tạo một bản ghi metadata master mới cho cùng định danh PN chuẩn;
- không có kho lưu trữ PN, xóa mềm (soft delete) hay bản ghi bia mộ (tombstone) nào được giữ chỉ để bảo toàn định danh PN — chính chuỗi PN chuẩn đảm nhận trách nhiệm đó.

**Lưu giữ, lưu trữ và xóa dọn lịch sử Movement.** PartMovement là bất biến trong lúc vận hành sản xuất: các quy trình thông thường không bao giờ sửa hoặc xóa bản ghi Movement. Tính bất biến **không** có nghĩa là mọi dòng Movement phải nằm mãi mãi trong cơ sở dữ liệu PostgreSQL chính — lưu trữ và xóa dọn theo một chính sách lưu giữ (retention) tường minh là việc bảo trì vòng đời/lưu trữ, tách biệt với vận hành sản xuất. Khi lịch sử Movement lớn dần, PartFlow có thể áp dụng một **chính sách lưu giữ có thể cấu hình** (ví dụ: giữ N năm lịch sử trong cơ sở dữ liệu chính — thời hạn lưu giữ là cấu hình, không bao giờ là một số năm hard-code trong quy tắc domain). Khi đó, lịch sử cũ hơn được:

1. chọn theo mốc cắt lưu giữ (retention cutoff);
2. xuất không mất mát ra các tệp lưu trữ;
3. kiểm tra là đã xuất thành công;
4. chỉ sau đó mới bị xóa dọn (xóa) khỏi cơ sở dữ liệu chính.

Các tệp lưu trữ trở thành nơi lưu trữ lịch sử dài hạn cho phần lịch sử đã bị xóa dọn. Quy tắc bắt buộc:

- **Vận hành sản xuất bình thường không bao giờ được UPDATE hoặc DELETE các dòng `PartMovement`** — cơ chế bảo vệ chỉ-ghi-thêm (thu hồi quyền UPDATE/DELETE khỏi role của ứng dụng cùng một trigger báo lỗi khi ghi) luôn có hiệu lực đối với ứng dụng. Lưu trữ/xóa dọn chỉ được thực hiện qua một **đường bảo trì Admin đặc quyền riêng** với cơ chế ủy quyền riêng — không bao giờ qua role của ứng dụng, các quy trình thông thường của ứng dụng hay bất kỳ đường ghi sản xuất nào.
- **Không bao giờ xóa dọn trước khi có một lần xuất lưu trữ thành công và đã được kiểm tra.**
- Bản lưu trữ phải giữ đủ dữ liệu để xác định PN, số lượng, loại Movement, Area, Operation, Machine, Worker, các mốc thời gian, quan hệ/ngữ cảnh sửa sai và các trường audit bắt buộc.
- PN trong lịch sử lưu trữ được lưu trực tiếp dưới dạng chuỗi PN chuẩn — không bao giờ phụ thuộc vào master PN.
- **Việc xóa dọn không bao giờ được làm đứt quan hệ giữa các bản ghi Movement.** Các Movement liên quan — ví dụ một bản sửa sai `REVERSED` và Movement gốc mà nó tham chiếu qua `reverses_movement_id`, hoặc các Movement của cùng một lệnh ứng dụng nguyên tử — được lưu trữ và xóa dọn cùng nhau: một Movement còn giữ lại không bao giờ được tham chiếu tới một dòng đã bị xóa dọn. Việc chọn theo mốc cắt lưu giữ phải giữ nguyên vẹn các chuỗi như vậy (giữ lại cả chuỗi cho tới khi mọi thành phần đều nằm sau mốc cắt), và bản lưu trữ bảo toàn ngữ cảnh quan hệ.
- Lưu giữ/xóa dọn là việc bảo trì quản trị tường minh, không bao giờ là một phần của quy trình sản xuất thông thường; nó có thể được kích hoạt bởi chính sách lưu giữ do Admin cấu hình, một ngưỡng kích thước dữ liệu do Admin cấu hình, hoặc một yêu cầu thủ công của Admin.
- Các thao tác bảo trì đòi hỏi ủy quyền Admin tường minh, hiển thị bản xem trước phạm vi/tác động trước khi thực hiện, bắt buộc có lý do và được audit đầy đủ: phạm vi, mốc cắt, ai/cái gì khởi tạo, khi nào và kết quả.
- Các bản ghi còn cần để dựng lại trạng thái sản xuất đang hoạt động không bao giờ bị lưu trữ/xóa dọn; trạng thái sản xuất đang hoạt động và các projection hiện tại phải luôn đối chiếu khớp được sau khi lưu trữ.
- Việc xóa dọn xóa đúng những dòng đã được lưu trữ (lưu trữ/xuất trước, rồi xóa dọn những gì đã được lưu trữ) — không bao giờ dùng việc truncate toàn bảng.
- Thiết lập lưu giữ nằm trong Administration/cấu hình, không nằm trong logic quy trình sản xuất.
- Định dạng tệp lưu trữ và cách truyền tải là các quyết định của giai đoạn triển khai; không có engine lưu trữ nào được triển khai trước giai đoạn roadmap xây dựng nó, và ở đây không giả định thêm hạ tầng lưu trữ nào.

Lưu ý sự phân biệt: *định danh sản xuất phải luôn truy vết được* (luôn đúng — bản ghi sản xuất mang PN chuẩn của chúng, và lịch sử đã lưu trữ vẫn là một phần của hồ sơ vĩnh viễn) **không** giống với *mọi dòng Movement thô phải nằm mãi mãi trong cơ sở dữ liệu chính* (không bắt buộc — có áp dụng lưu trữ theo chính sách lưu giữ).

---

# 29. Phạm vi ban đầu (Initial Scope)

Bản phát hành đầu tiên nên hỗ trợ:

- Machine Shop,
- bản ghi master PN,
- barcode bìa hồ sơ PN dùng lại được,
- nhập Work Order thủ công,
- import Work Order từ tệp,
- Work Order Demand,
- Modify intake,
- các movement Repair tường minh,
- Scrap,
- các Area,
- nhiều Operation cho mỗi Area,
- Machine tùy chọn,
- quét barcode,
- phân bố số lượng,
- tách và gộp số lượng,
- Route của Quantity Flow,
- hoàn tất vào Stockroom,
- Work Order Allocation được gợi ý,
- điều chỉnh Allocation thủ công bởi Admin và Manager,
- nhận diện Worker tùy chọn,
- Scan Station,
- Work Orders,
- Production Board,
- Area Board (All Areas overview + per-Area detail),
- Tracking,
- quản lý Machines,
- quản lý Planned Routes,
- lịch sử Movement bất biến,
- Undo,
- phân quyền theo vai trò.

Đồng bộ ERP có thể được bổ sung sau mà không thay thế mô hình cốt lõi.

---

# 30. Phạm vi tương lai (Future Scope)

Các mục sau nằm ngoài phạm vi ban đầu đã xác nhận, trừ khi được phê duyệt riêng:

- theo dõi từng chiếc riêng lẻ,
- đồng bộ ERP đầy đủ,
- tự động hóa máy móc,
- telemetry của máy điều khiển số bằng máy tính (computer numerical control, CNC),
- lập lịch sản xuất,
- định giá tồn kho,
- kế toán chi phí,
- tính lương,
- tích hợp Internet vạn vật (Internet of Things, IoT),
- phân tích dự báo,
- quản lý chất lượng đầy đủ,
- workflow engine đa dụng,
- đồng bộ lần quét ngoại tuyến (offline).

Các tính năng tương lai phải mở rộng mô hình PN, số lượng, Movement, Route và Allocation hiện có thay vì thay thế nó.

---

# 31. Những điều tường minh không phải mục tiêu (Explicit Non-Goals)

PartFlow không được trở thành:

- một ERP đầy đủ,
- một hệ thống điều hành sản xuất (manufacturing execution system, MES) đầy đủ,
- một hệ thống kế toán,
- một công cụ theo dõi từng chi tiết được đánh số serial riêng,
- một bộ điều khiển CNC,
- một nền tảng telemetry máy móc,
- một hệ thống định giá tồn kho,
- một thứ tự động thay thế ERP.

Không đưa vào các trách nhiệm này nếu không có quyết định dự án tường minh.

---

# 32. Các quyết định còn mở (Remaining Open Decisions)

Chỉ còn các quyết định chưa được giải quyết sau:

1. Số lượng đã nhập kho có được đưa trở lại sản xuất đang hoạt động qua một thao tác đảo ngược có kiểm soát hay không.
2. Việc đồng bộ lần quét ngoại tuyến có được đưa vào một bản phát hành sau hay không.

Việc triển khai phải tránh những giả định khiến các quyết định này khó thay đổi.

---

# 33. Nguyên tắc định hướng (Guiding Principles)

Mọi tính năng tương lai phải củng cố các nguyên tắc sau:

- Theo dõi định danh PN.
- Theo dõi số lượng vật lý, không theo dõi từng chiếc riêng lẻ.
- Giữ Work Order Demand tách biệt với Movement sản xuất.
- Giữ Work Order Allocation tách biệt với Movement sản xuất.
- Mặc định dùng Floating Route; chỉ gán snapshot Planned Route ở nơi muốn có hướng dẫn.
- Suy ra trạng thái sản xuất hiện tại — và vết của Floating Route — từ lịch sử Movement bất biến.
- Bảo toàn tính toàn vẹn số lượng.
- Ưu tiên quy trình lấy máy quét làm đầu (scanner-first).
- Giảm thiểu thao tác của người vận hành.
- Yêu cầu xác nhận khi ý định không rõ ràng.
- Để thực tế sản xuất thắng các kế hoạch đã lỗi thời.
- Giữ độc lập với ERP.
- Bảo toàn khả năng audit đầy đủ.
- Ưu tiên thiết kế đơn giản nhất mà vẫn phản ánh chính xác xưởng sản xuất.

---

# Lịch sử thay đổi

| Ngày | Commit | Thay đổi |
| --- | --- | --- |
| 2026-11-09 | uncommitted | Viết lại thành nội dung hiện hành cho người mới đọc. Hai câu nói chung được thu hẹp theo đúng các quy tắc cụ thể mà chúng mâu thuẫn (và code đang thực hiện): Work Order MODIFY nội bộ được tái sử dụng thì tăng dòng hiện có, và việc tăng số lượng của một dòng đã phát hành chỉ hợp lệ khi Work Order chưa hoàn tất. |
| 2026-10-08 | e13574f | Ngày nhận Work Order bị bỏ trống mặc định là ngày theo lịch của site, kể cả với Work Order được import. |
| 2026-10-07 | 918f025 | Import từ tệp có thể cập nhật Work Order Open và Released hiện có sau danh sách thay đổi và xác nhận bằng cách gõ lại. |
| 2026-10-07 | e1dee40 | Thêm import Work Order mới từ tệp CSV hoặc Excel. |
| 2026-10-07 | 4a77dc4 | Các bước tương lai của Assigned Route có thể được điều chỉnh kèm lý do, audit là `ROUTE_ADJUSTED`; các bước đã qua vẫn bất biến. |
| 2026-10-07 | 98ba54c | Người dùng được sửa allocation vẫn giữ các thao tác allocation trên Work Order đã hoàn tất. |
| 2026-10-07 | f0ae395, 1c5b3ef | Thêm cơ chế bảo vệ quản lý quyền; thao tác Scan Station cần thiết bị đã đăng ký cùng quyền của vai trò áp dụng tại Scan Station. |
| 2026-10-06 | 69b8ec1, 7581844 | Vai trò trở thành có tên và sửa được, permission key là thẩm quyền duy nhất, và các vai trò được liệt kê trở thành quyền cấp ban đầu. |
| 2026-10-06 | 9a7db92 | Work Order hoàn tất khi một lần lưu demand hoặc việc xóa dòng khiến mọi dòng đều được phân bổ đủ. |
| 2026-10-04 | c12728d | Worker không có mã nhân viên, và barcode thẻ được chuẩn hóa và duy nhất giữa các Worker. |
| 2026-10-04 | 9f466fa | Mục Hot không còn hoạt động tự rời Hot list, và dòng demand Hot chỉ bị xóa sau xác nhận bằng cách gõ lại. |
| 2026-09-13 | f016d41, 2410e13, e9a9412 | Định nghĩa thời lượng dự kiến hiệu lực của một vị trí và bước Planned Route hiện tại theo lần đến; bỏ quy tắc cố định 3 ngày. |
| 2026-09-04 | f10d8bd | v22: lần nhận tại Scan Station không bao giờ nhập vào số lượng hiện có, giải quyết quyết định còn mở thứ ba. |
| 2026-09-04 | 40fbcb5, 4fd635f | Receive Quantity tại Scan Station đưa vào một PN chưa có demand đang hoạt động, ghi ngày theo lần quét. |
| 2026-09-02 | d2f68de, 8e5ab18 | Thêm bản tiếng Việt đặt cạnh file tiếng Anh. |
| 2026-08-20 | 813264a | v21: dòng Work Order Demand đã phát hành cho phép sửa có giới hạn thay vì chỉ đọc. |
| 2026-08-20 | fc83be4 | v20: phát hành sản xuất từng phần và nhiều lần; terminal Area không bao giờ là Area bắt đầu. |
| 2026-08-17 | ecc1ef4, 66faca0, aba9c65 | v19: cổng xác nhận cuối cho `DONE`, `QUEUE` và Undo, với tùy chọn xác nhận bằng thẻ cho từng thao tác. |
| 2026-08-14 | 6917029, 6439477 | v18: thẻ nhân viên hiện có thay cho barcode `PF:WORKER:`, Worker Session dùng thời gian chờ trượt theo mức không hoạt động, và Production Board bỏ chế độ theo Area. |
| 2026-08-12 | fa4b51d, c4f18a6, c4509f9 | v16 và v17: chuỗi PN là định danh, metadata master PN là tùy chọn và xóa được, và định nghĩa việc lưu giữ Movement. |
| 2026-08-11 | 9a29c9f, cb8c457 | v14 và v15: Asset Tag của Machine tự cấp với barcode suy ra, và trang lịch sử Completed Work Orders. |
| 2026-08-04 | 74e0386, 01a7904, b59ef51 | v11 đến v13: các màn hình quản lý Machines và Planned Routes cùng vòng đời kích hoạt lại Machine. |
| 2026-07-31 | 8dd337b | v10: hoàn tất `DONE` ở cấp Area (`AREA_COMPLETED`, `READY_TO_TRANSFER`). |
| 2026-07-24 | 027567e, 74edbdd, fbb38cb | v7 đến v9: đổi Purchase Order thành Work Order, ngày và số có thể để trống, thao tác Scan Station một lần, Floating Route, Repair và Scrap. |
| 2026-07-20 | c2aeda5 to 2cb4d1c | v3 đến v6: hồ sơ ban đầu và việc căn chỉnh lại. |
