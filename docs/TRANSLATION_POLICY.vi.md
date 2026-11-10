# Chính sách dịch tài liệu

> **Ngôn ngữ:** Đây là bản tiếng Việt của
> [`TRANSLATION_POLICY.md`](./TRANSLATION_POLICY.md). Bản tiếng Anh là nguồn chuẩn
> (source of truth); khi hai bản khác nhau, bản tiếng Anh có hiệu lực. Đồng bộ với
> bản tiếng Anh tại commit `b405c6e` cộng các thay đổi chưa commit ngày 2026-11-09.

Chính sách này giải thích cách viết, kiểm tra và giữ cập nhật các bản dịch tiếng
Việt (mirror) của tài liệu PartFlow. Người đọc là bất kỳ ai sửa một tài liệu tiếng
Anh hoặc bản dịch tiếng Việt của nó. Danh sách tài liệu nằm trong
[mục lục tài liệu](./README.vi.md).

## 1. Quyền chuẩn

Tài liệu tiếng Anh có quyền chuẩn. File tiếng Việt là bản hỗ trợ đọc được dịch từ
file tiếng Anh tương ứng. Nếu hai bản mâu thuẫn, nội dung tiếng Anh lập tức được ưu
tiên và file tiếng Việt phải được sửa lại.

Bản dịch không bao giờ thêm, bỏ, nới lỏng hoặc siết chặt một quy tắc nghiệp vụ,
ranh giới phase, tiêu chí nghiệm thu (acceptance criterion), yêu cầu giao diện
người dùng (UI), biện pháp kiểm soát triển khai hay quy trình vận hành.

## 2. Cấu trúc và cách đặt tên

- `README.md` ở root tương ứng với `README.vi.md`.
- `docs/<path>/<NAME>.md` tương ứng với `docs/<path>/<NAME>.vi.md` — bản dịch tiếng
  Việt nằm cùng thư mục với bản gốc tiếng Anh, chỉ khác hậu tố `.vi.md` (ví dụ
  `docs/GUI_DESIGN.md` ↔ `docs/GUI_DESIGN.vi.md`,
  `docs/deployment/VPS.md` ↔ `docs/deployment/VPS.vi.md`). Không có thư mục con
  riêng cho tiếng Việt.
- Mỗi tài liệu tiếng Anh link tới bản dịch của nó từ header ngôn ngữ, và mỗi bản
  dịch link ngược về file `.md` chuẩn; mục lục tài liệu liệt kê cả hai cột.
- Tên file, số mục, code, identifier, API path, đối tượng database, lệnh, biến môi
  trường, định dạng barcode, giá trị enum và UI copy nguyên văn vẫn giữ tiếng Anh,
  trừ khi tài liệu gốc định nghĩa rõ một giá trị đã được bản địa hóa.
- Văn xuôi tiếng Việt có thể giữ một thuật ngữ miền tiếng Anh khi dịch nó sẽ làm
  bộ từ vựng PartFlow trở nên mơ hồ.

## 3. Header bắt buộc của bản dịch

Mỗi bản dịch tiếng Việt phải ghi rõ:

1. đường dẫn file nguồn tiếng Anh;
2. commit của repository upstream được dùng làm baseline tài liệu (hoặc revision
   của gói tài liệu khi file tiếng Anh được tạo sau baseline đó);
3. tiếng Anh vẫn là bản có quyền chuẩn.

Baseline là bằng chứng đồng bộ, không phải khẳng định rằng một file tiếng Anh mới
viết đã tồn tại trong commit upstream đó, và không thay thế việc review.

Bản dịch tiếng Việt là bản dịch **đầy đủ theo ngữ nghĩa**, không phải bản sao máy
móc từng dòng. Phần giải thích lặp lại có thể được gộp trong tiếng Việt, nhưng mọi
yêu cầu bắt buộc đang có hiệu lực và mọi mục có đánh số phải còn được thể hiện với
cùng mức độ bắt buộc.

Một tài liệu chỉ nêu nội dung hiện tại của nó, và bản dịch dịch nội dung hiện tại
đó. Lịch sử của một tài liệu nằm trong bảng lịch sử thay đổi ở cuối tài liệu. Bản
dịch dịch bảng đó như mọi bảng khác: mọi hàng và mọi cột, dưới heading
`Lịch sử thay đổi` với các cột `Ngày`, `Commit` và `Thay đổi`. Ngày, mã commit và
dấu `uncommitted` giữ nguyên.

## 4. Quy trình cập nhật

Khi một tài liệu tiếng Anh thay đổi:

1. Sửa file nguồn tiếng Anh trước.
2. Review diff so với source commit được ghi trong file tiếng Việt.
3. Dịch mọi thay đổi có ý nghĩa mà không làm đổi nghĩa.
4. Giữ nguyên số mục và technical literal.
5. Chỉ cập nhật baseline/package revision đã ghi sau khi bản dịch đã hoàn chỉnh.
6. Chạy các kiểm tra tài liệu ở §5.

Bản dịch chưa đồng bộ phải được đánh dấu rõ ở đầu file cho đến khi được đồng bộ.
Không bao giờ để nó trông như vẫn còn hiện hành.

## 5. Kiểm tra

Với từng bản dịch:

- mọi heading có đánh số của nguồn và mọi mục bắt buộc đang có hiệu lực có một
  heading dịch tương ứng hoặc một subsection được chỉ rõ;
- code fence cân bằng, và mọi lệnh hoặc ví dụ dữ liệu mang tính bắt buộc giữ nguyên
  về kỹ thuật; các ví dụ lặp lại không mang tính bắt buộc có thể được gộp;
- các link Markdown cục bộ phải phân giải được;
- identifier và ví dụ lệnh giữ nguyên — bản dịch nằm cạnh bản gốc nên đường dẫn
  tương đối không cần điều chỉnh;
- các từ mang tính bắt buộc như **must**, **never**, **required**, **refused** và
  **no write** giữ nguyên mức độ mạnh;
- các bảng ánh xạ và bảng quyết định mang tính bắt buộc, và bảng lịch sử thay đổi,
  giữ đủ mọi hàng và cột của nguồn;
- không có câu dịch nào mâu thuẫn với một nguồn chuẩn mới hơn.

Kiểm tra tự động giúp tìm thiếu sót về cấu trúc, nhưng các thay đổi liên quan đến
bất biến nghiệp vụ, tính toàn vẹn số lượng, tính bất biến của Movement, quyền,
migration, sao lưu, khôi phục hoặc rollback vẫn cần một người review về ngữ nghĩa.

## 6. Phạm vi

Tài liệu dự án đang dùng và tài liệu vận hành mới đều có bản dịch tiếng Việt. Các
mục sau được loại trừ có chủ đích:

- `docs/archive/`, vì chứa các phiên bản lịch sử đã bị thay thế và prompt cũ;
- `docs/mockups/`, vì các file HTML ở đó là tham chiếu trực quan có thể chạy và
  nội dung chữ trên UI của chúng do `GUI_DESIGN.md` quản lý;
- `AGENTS.md` và `CLAUDE.md`, vì đây là hướng dẫn vận hành cho AI chứ không phải
  tài liệu dự án cho người đọc. Nhân đôi chúng bằng ngôn ngữ khác sẽ tạo ra các
  điểm vào hướng dẫn cạnh tranh nhau.

## Lịch sử thay đổi

| Ngày | Commit | Thay đổi |
| --- | --- | --- |
| 2026-11-09 | uncommitted | Căn chỉnh theo quy tắc tài liệu chỉ nêu nội dung hiện tại: bảng lịch sử thay đổi được dịch như mọi bảng khác, và chi tiết lịch sử thay đổi đã bị thay thế không còn là trường hợp được gộp; thêm phần giới thiệu. |
| 2026-09-02 | `8e5ab18`, `6fae517` | Đặt các bản dịch cạnh bản gốc tiếng Anh với hậu tố `.vi.md`, và khôi phục các technical literal trong bản dịch. |
| 2026-09-02 | `d2f68de` | Tạo chính sách cùng các bản dịch tài liệu tiếng Việt. |
