# Chỉnh lý ý tưởng CNC Part Flow

### A. Identity và barcode
1. Yes. Trong thực tế, cùng một Part Number có thể xuất hiện đồng thời trong nhiều PO đang active.
2. Một folder được tái sử dụng theo Part Number.
    - Tôi không muốn thêm PoPart hoặc PurchaseOrderLine ID vì thực tế không đủ nhân lực và điều kiện để triển khai ==> Tôi cần một giải phảp hiệu quả cho vấn đề xung đột nhiều PO có thể có chung một hoặc vài Part-Number, nhưng lại không làm mất tính linh hoạt trong thực tế sử dụng.
    - Thực tế hiện tại, khi nhận PO mới, người nhận sẽ tìm folder (có dán barcode) tương ứng cho từng part, nếu phát hiện thấy có Part-Number đang chạy dưới xưởng rồi thì họ sẽ đánh dấu Part-Number của PO mới này là inqueue (và có thể cộng gộp vào số lượng đang inqueue của cùng part-number nếu có)
    - Hoặc manager có thể xem xét liệu có thể add part đó vào số lượng Part Number đang ở WorkCenter nào đó được không. Nếu vậy cần phải quản lý được 1 Part-Number có thể nằm ở nhiều WorkCenter khác nhau.
    - Khi một PN (Part-Number) được nhập kho (COMPLETED), folder chứa hồ sơ bản vẽ của nó sẽ được trả lại về cho machineshop office, họ sẽ check nếu PN này đang có thêm một số lượng mới cần gia công (inqueue) thì họ sẽ tiếp tục chuyển nó tới WorkCenter liên quan để bắt đầu quy trình gia công mới.
3. Job Number là duy nhất trên toàn hệ thống, tuy nhiên tôi không muốn tạo barcode kết hợp PN với Job Number,
4. Hiện tại chưa có barcode, app được xây đựng để tạo ra barcode cho nó sử dụng. Cho nên format là thoải mái thiết kế.
5. Yes, khi scan PN mà thấy có PN giồng vậy đang được gia công thì cho phép chọn PO (active POs) hoặc tự tạo PO tạm thời (chỉ xài nội bộ trong department, ví dụ: PO202606031525-NEW, PO202606031529-REWORK...)
6. Có, số lượng của một PN có thể bị chia ra và nằm ở nhiều work center cùng lúc. Ví dụ 6 part ở Lathe, 4 part vẫn ở Cut.
7. 10 (-2) ban đầu có nghĩa là số lượng gốc ban đầu là 10 nhưng sau đó bị điều chỉnh giảm đi 2 là còn 8. Nhưng giờ tôi không nghĩ vậy nữa, Tôi muốn nó đại diện cho số lượng part đang bị nằm rải ờ các WorkCenter khác (nếu có), ví dụ: "Cut (4), Lathe (6)".
8. Rework áp dụng cho một phần số lượng thôi. Tuy nhiên Rework có thể là một vài part của một PN nào đó đang gia công, cũng có thể là một part hoàn chỉnh (version cũ) cần được gia công lại để upgrade lên version mới. Điều này cũng có nghĩa là PN không đổi cho dù drawing version có thay đổi.
9. Việc theo dõi từng physical piece riêng lẻ nghe hấp dẫn nhưng không khả thi trong thực tế triển khai ở hãng tôi, lý do là chỉ cho 1 barcode đại diện cho 1 PN, và barcode này chỉ dán 1 lần trên bìa folder của PN đó và đc tái sử dụng hoài chứ ko được dán lên từng part. Do đây là gia công cơ khí (bắt đầu từ phôi cho đến thành phẩm), không thể dán lên hoài được, ko có nhân lực để làm.
10. Rework/Modification không có PO thật trong ERP (nó chỉ là thông tin sử dụng nội bộ trong department). Mọi PO từ ERP ra đều xem là gia công mới, cho dù thực tế có thể là dùng part cũ để gia công lại thành version mới.
11. Rework không có và không cần mã riêng. Tuy nhiên mỗi khi scan part, tất cả thông tin tại thời điểm đó cần được khi nhận. Ví dụ ngày nhận, người yêu cầu, lý do và due date riêng
12. Yes, Cùng một Part Number có thể có nhiều rework request đang active đồng thời.
13. Yes, người dùng cần nhập quantity, requester, reason và due date (optional) ngay lúc đó. (Manager/Admin được phép chỉnh sửa lại sau)
14. Bốn máy Lathe cần được theo dõi riêng để biết part đang ở máy nào. Nhưng Lathe chỉ là một ví dụ cụ thể thôi, Mill cũng có nhiều máy, ...
15. Mỗi WorkCenter có 1 scanner + tablet/pc riêng, dùng chung cho nhiều máy thuộc WorkCenter đó.
    - Mỗi máy có 1 barcode riêng.
    - Nếu WorkCenter chỉ có 1 barcode (tức là chỉ có 1 máy hoặc 1 người làm) thì không cần scan riêng, WorkCenter đó tự biết barcode đại diện cho nó là gì.
16. Operator scan khi part được giao đến khâu. Tức là part working tại khâu đó tính từ lúc nó được scan tại khâu đó cho đến khi nó được scan tại khâu tiếp theo.
17. Một route có thể đi qua cùng một Work Center nhiều lần.
18. Khi scan lệch route, sửa route để phản ánh thực tế.
19. Trước mắt Route là chuỗi tuyến tính.
20. Yes, cần theo dõi expected duration cho từng route step để phát hiện bottleneck
21. PO được coi là hoàn thành khi mọi PO Part đã vào Stockroom
22. Không, Part luôn phải qua Stockroom trước khi được phân phối.
23. PO History không được mở lại. Xong là xong luôn. Khi cần thì sẽ tạo PO mới.
24. Tùy theo thiết lập (trong Admin page) mà opertor có cần phải scan barcode của họ để đăng nhập hay không. Nếu không phải đăng nhập thì tên operator có thể để trống, hoặc được Admin/Manager chi3 định luôn nếu vị trí đó chỉ có 1 người làm cố định.
25. Tùy theo thiết lập (trong Admin page) mà opertor có cần phải scan barcode của họ để bắt đầu scan nhận parts tại một WorkCenter hay không.
26. Yes, Tablet scanner nên chạy kiosk mode. (Android tablet có hỗ trợ free ko? hay phải mất phí để bật?)
27. Có, mất mạng tạm thời vẫn scan được, khi kết nối lại với hệ thống thì nó dựa vào thời gian scan để đồng bộ lại dữ liệu.
28. Tôi chưa hài lòng với từ "Work Center" lắm, có đề xuất nào hay hơn ko? ví dụ "Stage"? tôi thích từ đơn hơn. Nhưng chỉ đề xuất nếu nó thực sự hay hơn, phù hợp hơn, chính xác hơn. Đừng cố đề xuất chỉ vì tôi yêu cầu. (tương tự các từ ghép khác như "External Processing", Material Procurement hoặc Awaiting Material, ... có từ đơn nào phù hợp ko?)
