# Tài liệu PartFlow

> **Ngôn ngữ:** Tiếng Anh là source of truth. Các file `*.vi.md` nằm cạnh bản
> gốc là bản dịch tiếng Việt để tiện đọc và không thay thế tài liệu gốc.
>
> **Bản gốc chuẩn:** [`README.md`](README.md), được tạo trong gói tài liệu
> này trên baseline upstream `194ffc2e5e8e22c389abecd0830292a6707955d9`.

## Các đặc tả chuẩn

| Tài liệu | Phạm vi chịu trách nhiệm | Bản gốc tiếng Anh |
| --- | --- | --- |
| [`PROJECT_PROFILE.vi.md`](./PROJECT_PROFILE.vi.md) | Thuật ngữ domain, hành vi nghiệp vụ, invariant, workflow, kiến trúc và phạm vi sản phẩm | [`PROJECT_PROFILE.md`](./PROJECT_PROFILE.md) |
| [`GUI_DESIGN.vi.md`](./GUI_DESIGN.vi.md) | UI đích và hành vi tương tác đã được phê duyệt | [`GUI_DESIGN.md`](./GUI_DESIGN.md) |
| [`IMPLEMENTATION_ROADMAP.vi.md`](./IMPLEMENTATION_ROADMAP.vi.md) | Thứ tự triển khai, ranh giới phase, dependency và giới hạn tạm thời | [`IMPLEMENTATION_ROADMAP.md`](./IMPLEMENTATION_ROADMAP.md) |
| [`SLICE1_DATA_MODEL.vi.md`](./SLICE1_DATA_MODEL.vi.md) | Contract của slice Phase 4 đã triển khai, phụ thuộc các đặc tả chuẩn | [`SLICE1_DATA_MODEL.md`](./SLICE1_DATA_MODEL.md) |

## Triển khai và vận hành

| Tài liệu | Mục đích | Bản gốc tiếng Anh |
| --- | --- | --- |
| [`DEPLOYMENT.vi.md`](./DEPLOYMENT.vi.md) | Trạng thái triển khai, các hướng được hỗ trợ, topology đích và release gate | [`DEPLOYMENT.md`](./DEPLOYMENT.md) |
| [`deployment/SYNOLOGY_NAS.vi.md`](./deployment/SYNOLOGY_NAS.vi.md) | Synology staging nội bộ hiện tại và hướng production ở Phase 16 | [`deployment/SYNOLOGY_NAS.md`](./deployment/SYNOLOGY_NAS.md) |
| [`deployment/VPS.vi.md`](./deployment/VPS.vi.md) | Triển khai production portable trên VPS | [`deployment/VPS.md`](./deployment/VPS.md) |
| [`deployment/SHARED_HOSTING.vi.md`](./deployment/SHARED_HOSTING.vi.md) | Điều kiện tương thích shared hosting và đánh giá Hawk Host | [`deployment/SHARED_HOSTING.md`](./deployment/SHARED_HOSTING.md) |
| [`deployment/OPERATIONS_RUNBOOK.vi.md`](./deployment/OPERATIONS_RUNBOOK.vi.md) | Backup, restore, release, rollback, monitoring và xử lý sự cố | [`deployment/OPERATIONS_RUNBOOK.md`](./deployment/OPERATIONS_RUNBOOK.md) |

## Chính sách bản dịch

[`TRANSLATION_POLICY.vi.md`](./TRANSLATION_POLICY.vi.md) quy định cách tạo, kiểm tra,
đánh dấu và cập nhật bản dịch tiếng Việt. `docs/archive/` không được dịch vì
chứa các bản lịch sử đã bị thay thế. Các HTML mockup là tham chiếu trực quan có
thể chạy, không phải tài liệu mirror, nên cũng không nằm trong phạm vi dịch.
