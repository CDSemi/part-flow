# Chính sách dịch tài liệu

> **Bản gốc chuẩn:** [`TRANSLATION_POLICY.md`](TRANSLATION_POLICY.md).
> Baseline upstream của gói tài liệu:
> `194ffc2e5e8e22c389abecd0830292a6707955d9`.
>
> **Quyền chuẩn:** Tiếng Anh là source of truth. Bản tiếng Việt chỉ để tiện đọc.

## 1. Quyền chuẩn

Tài liệu tiếng Anh có quyền chuẩn. File tiếng Việt là bản hỗ trợ đọc được dịch
từ file tiếng Anh tương ứng. Nếu hai bản mâu thuẫn, nội dung tiếng Anh lập tức
được ưu tiên và file tiếng Việt phải được sửa lại.

Bản dịch không được thêm, bỏ, nới lỏng hoặc siết chặt business rule, ranh giới
phase, acceptance criterion, yêu cầu UI, kiểm soát triển khai hay quy trình vận
hành.

## 2. Cấu trúc và cách đặt tên

- `README.md` ở root tương ứng với `README.vi.md`.
- `docs/<path>/<NAME>.md` tương ứng với `docs/<path>/<NAME>.vi.md` — bản dịch
  tiếng Việt nằm cùng thư mục với bản gốc tiếng Anh, chỉ khác hậu tố `.vi.md`
  (ví dụ `docs/GUI_DESIGN.md` ↔ `docs/GUI_DESIGN.vi.md`,
  `docs/deployment/VPS.md` ↔ `docs/deployment/VPS.vi.md`). Không có thư mục
  con riêng cho tiếng Việt.
- Mỗi tài liệu tiếng Anh link tới bản dịch của nó từ header ngôn ngữ, và mỗi
  bản dịch link ngược về file `.md` chuẩn; mục lục tài liệu liệt kê cả hai cột.
- Tên file, số mục, code, identifier, API path, database object, command,
  environment variable, barcode format, enum value và UI copy nguyên văn vẫn
  giữ tiếng Anh, trừ khi tài liệu gốc định nghĩa rõ một giá trị đã localized.
- Phần văn xuôi tiếng Việt có thể giữ domain term tiếng Anh nếu dịch sang tiếng
  Việt làm thuật ngữ PartFlow trở nên mơ hồ.

## 3. Header bắt buộc của bản dịch

Mỗi bản tiếng Việt phải ghi rõ:

1. đường dẫn file nguồn tiếng Anh;
2. commit upstream làm baseline tài liệu, hoặc revision của gói nếu file EN được
   tạo sau baseline đó;
3. tiếng Anh vẫn là bản có quyền chuẩn.

Baseline là bằng chứng đồng bộ, không khẳng định file EN mới đã tồn tại trong
commit upstream và không thay thế việc review.

Mirror tiếng Việt là bản dịch **đầy đủ theo ngữ nghĩa**, không phải bản sao máy
móc từng dòng. Có thể gộp phần giải thích lặp lại hoặc chi tiết change-history đã
bị thay thế, nhưng mọi active normative requirement và mọi numbered section phải
được thể hiện với cùng mức bắt buộc.

## 4. Quy trình cập nhật

Khi tài liệu tiếng Anh thay đổi:

1. Sửa file nguồn tiếng Anh trước.
2. Review diff so với source commit được ghi trong file tiếng Việt.
3. Dịch mọi thay đổi có ý nghĩa mà không làm đổi nghĩa.
4. Giữ nguyên số mục và technical literal.
5. Chỉ cập nhật baseline/package revision sau khi bản mirror đã hoàn chỉnh.
6. Chạy các kiểm tra tài liệu ở §5.

Bản dịch chưa đồng bộ phải được đánh dấu rõ ở đầu file cho đến khi cập nhật
xong. Không được để một bản cũ trông như vẫn còn current.

## 5. Kiểm tra

Với từng bản mirror:

- mọi numbered heading và active normative section có heading/subsection dịch
  tương ứng;
- code fence cân bằng; mọi normative command/data example giữ nguyên kỹ thuật;
  example không normative và lặp lại có thể được gộp;
- các local Markdown link phải resolve được;
- identifier và command example giữ nguyên — bản dịch nằm cạnh bản gốc nên
  relative path không cần điều chỉnh;
- các từ mang tính bắt buộc như **must**, **never**, **required**, **refused** và
  **no write** phải giữ nguyên mức độ mạnh;
- table mapping/decision mang tính normative giữ đủ mọi hàng và cột của nguồn;
- không có câu dịch nào mâu thuẫn với canonical source mới hơn.

Kiểm tra tự động giúp tìm thiếu sót cấu trúc, nhưng các thay đổi liên quan đến
business invariant, quantity integrity, Movement immutability, permission,
migration, backup, restore hoặc rollback vẫn phải được review về ngữ nghĩa.

## 6. Phạm vi

Tài liệu project đang active và tài liệu vận hành mới đều có mirror tiếng Việt.
Các mục sau được chủ động loại trừ:

- `docs/archive/`, vì chứa bản lịch sử và prompt cũ đã bị thay thế;
- `docs/mockups/`, vì HTML ở đó là tham chiếu trực quan có thể chạy và UI text
  của chúng được quản lý bởi `GUI_DESIGN.md`;
- `AGENTS.md` và `CLAUDE.md`, vì đây là instruction vận hành AI chứ không phải
  tài liệu project cho người đọc. Nhân đôi chúng bằng ngôn ngữ khác sẽ tạo ra
  các instruction entry point cạnh tranh nhau.
