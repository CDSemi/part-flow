# Tài liệu PartFlow

> **Ngôn ngữ:** Đây là bản tiếng Việt của [`README.md`](./README.md). Bản tiếng
> Anh là nguồn chuẩn (source of truth); khi hai bản khác nhau, bản tiếng Anh có
> hiệu lực. Đồng bộ với bản tiếng Anh tại commit `b405c6e` cộng các thay đổi chưa commit ngày 2026-11-09. Mỗi tài liệu đang dùng có một bản dịch tiếng Việt nằm
> cạnh với hậu tố `.vi.md`.

Mục lục này liệt kê mọi tài liệu PartFlow đang dùng và mục đích của từng tài liệu.
Hãy bắt đầu với [README gốc](../README.vi.md) để có phần giới thiệu và hướng dẫn
cho lập trình viên. Mỗi quy tắc có một tài liệu sở hữu: `PROJECT_PROFILE.md` sở hữu
quy tắc nghiệp vụ, `GUI_DESIGN.md` sở hữu giao diện người dùng đã duyệt,
`IMPLEMENTATION_ROADMAP.md` sở hữu thứ tự hiện thực và trạng thái hiện thực hiện
tại, tài liệu slice chứa chi tiết phụ thuộc, và `DEPLOYMENT.md` sở hữu việc triển
khai. Mỗi tài liệu chỉ nêu nội dung hiện tại và kết thúc bằng một bảng lịch sử thay
đổi.

## Các đặc tả chuẩn

| Tài liệu | Phạm vi chịu trách nhiệm | Bản gốc tiếng Anh |
| --- | --- | --- |
| [`PROJECT_PROFILE.vi.md`](./PROJECT_PROFILE.vi.md) | Đặc tả có thẩm quyền: thuật ngữ miền, hành vi nghiệp vụ, bất biến (invariant), quy trình, kiến trúc và phạm vi sản phẩm. | [`PROJECT_PROFILE.md`](./PROJECT_PROFILE.md) |
| [`GUI_DESIGN.vi.md`](./GUI_DESIGN.vi.md) | Giao diện người dùng đích và hành vi tương tác đã được duyệt, với [`mockups/partflow-gui-mockup-v18.html`](./mockups/partflow-gui-mockup-v18.html) làm tham chiếu trực quan. | [`GUI_DESIGN.md`](./GUI_DESIGN.md) |
| [`IMPLEMENTATION_ROADMAP.vi.md`](./IMPLEMENTATION_ROADMAP.vi.md) | Thứ tự hiện thực, ranh giới phase, phụ thuộc, trạng thái hiện thực hiện tại và các giới hạn tạm thời. | [`IMPLEMENTATION_ROADMAP.md`](./IMPLEMENTATION_ROADMAP.md) |
| [`SLICE1_DATA_MODEL.vi.md`](./SLICE1_DATA_MODEL.vi.md) | Mô hình dữ liệu và hợp đồng command của việc nhập Work Order và release sản xuất, cùng các quy tắc lưu trữ mà các slice sau dùng chung; phụ thuộc các đặc tả chuẩn. | [`SLICE1_DATA_MODEL.md`](./SLICE1_DATA_MODEL.md) |

## Triển khai và vận hành

| Tài liệu | Mục đích | Bản gốc tiếng Anh |
| --- | --- | --- |
| [`DEPLOYMENT.vi.md`](./DEPLOYMENT.vi.md) | Điểm bắt đầu để chạy PartFlow ngoài máy trạm: trạng thái hiện tại, hợp đồng của production stack, yêu cầu để đưa vào sử dụng (go-live), và cách đặt tên và công bố bản phát hành. | [`DEPLOYMENT.md`](./DEPLOYMENT.md) |
| [`deployment/SYNOLOGY_NAS.vi.md`](./deployment/SYNOLOGY_NAS.vi.md) | Cài đặt, cập nhật, sao lưu và giám sát PartFlow production trên Synology NAS của công ty, cùng môi trường staging hạn chế tùy chọn. | [`deployment/SYNOLOGY_NAS.md`](./deployment/SYNOLOGY_NAS.md) |
| [`deployment/SYNOLOGY_ADMIN.vi.md`](./deployment/SYNOLOGY_ADMIN.vi.md) | Công cụ Deployment Admin (`pf`) quản lý các instance staging trên Synology, gồm cả thiết kế dự kiến của nó như một controller triển khai dùng lại được. | [`deployment/SYNOLOGY_ADMIN.md`](./deployment/SYNOLOGY_ADMIN.md) |
| [`deployment/VPS.vi.md`](./deployment/VPS.vi.md) | Chạy cùng production stack đó trên một máy chủ riêng ảo (virtual private server, VPS) Linux, host production thay thế. | [`deployment/VPS.md`](./deployment/VPS.md) |
| [`deployment/SHARED_HOSTING.vi.md`](./deployment/SHARED_HOSTING.vi.md) | Vì sao shared web hosting chỉ khả thi có điều kiện và nhà cung cấp phải xác nhận những gì trước, gồm cả kết quả đánh giá Hawk Host. | [`deployment/SHARED_HOSTING.md`](./deployment/SHARED_HOSTING.md) |
| [`deployment/OPERATIONS_RUNBOOK.vi.md`](./deployment/OPERATIONS_RUNBOOK.vi.md) | Các quy trình của người vận hành: hồ sơ triển khai, kiểm tra sức khỏe hệ thống, sao lưu, kiểm tra khôi phục, phát hành, rollback, reconciliation, sự cố và lịch định kỳ. | [`deployment/OPERATIONS_RUNBOOK.md`](./deployment/OPERATIONS_RUNBOOK.md) |

Thư mục `deployment/Synology DSM7 - Staging/` chứa một bộ công cụ staging đã bị thay
thế, chỉ giữ để tham khảo; nó không bao giờ là hướng dẫn hiện hành.

## Chính sách bản dịch

[`TRANSLATION_POLICY.vi.md`](./TRANSLATION_POLICY.vi.md) quy định cách tạo, kiểm
tra, đánh dấu và cập nhật bản dịch tiếng Việt. Các file trong `docs/archive/` là
tài liệu lịch sử đã bị thay thế và cố ý không được dịch. Các HTML mockup là tham
chiếu trực quan có thể chạy, không phải tài liệu cần bản dịch, nên cũng cố ý không
nằm trong phạm vi dịch.

## Lịch sử thay đổi

| Ngày | Commit | Thay đổi |
| --- | --- | --- |
| 2026-11-09 | uncommitted | Viết lại cho người mới đọc: phần giới thiệu, mô tả hiện tại một câu cho mỗi tài liệu, và bảng thay đổi này. |
| 2026-10-14 | `2c360cb` | Mục Deployment Admin nêu mục tiêu thiết kế dự kiến là một controller dùng lại được. |
| 2026-09-02 | `8e5ab18`, `6fae517` | Chuyển các bản dịch tiếng Việt về cạnh bản gốc tiếng Anh và trỏ lại mọi link. |
| 2026-09-02 | `d2f68de` | Tạo mục lục cùng các hướng dẫn triển khai và các bản dịch tiếng Việt. |
