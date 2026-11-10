# Slice 1 — Data model nhập Work Order thủ công và release sản xuất

> **Bản gốc:** [`SLICE1_DATA_MODEL.md`](SLICE1_DATA_MODEL.md). Đồng bộ với bản tiếng Anh tại commit `b405c6e` cộng các thay đổi chưa commit ngày 2026-11-09. Bản tiếng Anh là nguồn chuẩn (source of truth); khi hai bản khác nhau, bản
> tiếng Anh thắng.

Tài liệu này đặc tả data model và hợp đồng (contract) của các command trong business slice đầu tiên của PartFlow: nhập
tay một Work Order cùng Work Order Demand của nó, rồi release production quantity một cách tường minh vào một starting
Area đã cấu hình. Người đọc là developer và reviewer khi thay đổi phần lưu trữ Work Order, Part Number (PN), Quantity
Flow, Assigned Route, Part Movement hoặc audit, và bất kỳ ai lần theo một comment trong source code trích dẫn tài liệu
này theo số mục (ví dụ `SLICE1 §14`).

Đây là chi tiết triển khai cấp dưới (subordinate). `PROJECT_PROFILE.md` sở hữu thuật ngữ domain, business rule,
invariant và workflow; `GUI_DESIGN.md` sở hữu giao diện người dùng (user interface, UI) đã duyệt;
`IMPLEMENTATION_ROADMAP.md` sở hữu thứ tự triển khai và trạng thái triển khai hiện tại; `DEPLOYMENT.md` sở hữu việc
triển khai. Tài liệu này không bao giờ ghi đè các tài liệu đó: khi nó và implementation khác nhau, đó là lỗi (defect)
của một trong hai, không bao giờ là giấy phép để tự phát minh behavior. Các rule được nhắc lại ở đây chủ yếu đến từ
PROJECT_PROFILE §7, §8, §13, §17, §18, §21 (Work Orders), §24 đến §25 và §28, và từ GUI_DESIGN §11 (Work Orders).

Schema mô tả ở đây là schema hiện tại tại migration head `0032_phase14_route_adjusted`. Các bảng của slice được tạo
bởi các migration `0002_phase3_domain`, `0004_phase4_audit` và `0005_phase4_release_index`; các migration sau đó thêm
cột, mở rộng check và thêm bảng, và mỗi mục nêu hình dạng hiện tại.

Các chữ viết tắt dùng trong toàn tài liệu: Part Number (PN); khóa chính (primary key, PK); khóa ngoại (foreign key,
FK); hệ thống hoạch định nguồn lực doanh nghiệp (enterprise resource planning, ERP); định danh duy nhất toàn cục
(universally unique identifier, UUID); giao diện lập trình ứng dụng (application programming interface, API); kiểu JSON
nhị phân của PostgreSQL (`jsonb`); giờ phối hợp quốc tế (Coordinated Universal Time, UTC).

---

## 1. Slice Goals and Non-Goals

**Mục tiêu.** Slice triển khai trọn vẹn workflow business đầu tiên:

1. Tạo hoặc tìm một WorkOrder.
2. Tạo hoặc tìm một PartNumber (cùng barcode PartFlow duy nhất của nó).
3. Tạo hoặc cập nhật các dòng WorkOrderDemand của Work Order.
4. Lưu business demand **mà không** tạo production quantity.
5. Cung cấp một command **production release** riêng và tường minh. Command tạo một QuantityFlow với route mode của
   nó (`FLOATING` theo mặc định, không có AssignedRoute; hoặc `PLANNED`, có một snapshot AssignedRoute), append một
   PartMovement `RECEIVED` bất biến, và thiết lập vị trí hiện tại. Toàn bộ việc này nằm trong một transaction và có
   tính idempotent.

**Ngoài phạm vi.** Các khả năng sau nằm ngoài slice này: workflow chuyển (transfer) tại Scan Station, gán Machine,
session của Worker và Machine, SPLIT và MERGED, Undo và correction, hoàn tất vào Stockroom, WorkOrderAllocation, import
Work Order từ file, xác thực và role, tích hợp ERP, và mọi hành vi offline. Phần lớn các khả năng này hiện đã có và được
đặc tả ở nơi khác; §18 cho biết ở đâu.

---

## 2. Required Entities and Responsibilities

| # | Entity | Trách nhiệm trong slice này |
|---|--------|------------------------------|
| 1 | `Department` | Đơn vị tổ chức sở hữu các Area và là ngữ cảnh cấu hình (dữ liệu ban đầu: Machine Shop). |
| 2 | `Area` | Định danh vị trí vật lý ổn định. Cung cấp starting Area đã cấu hình cho release và là đích của `RECEIVED`. |
| 3 | `Operation` | Công việc mà một Area hỗ trợ. Operation của starting Area được xác định hoặc xác nhận khi release và được ghi trên Movement `RECEIVED`. |
| 4 | `PartNumber` | Bản ghi tùy chọn chứa metadata hiện tại của một PN canonical. Bản thân chuỗi PN là định danh sản xuất ổn định (PROJECT_PROFILE §7 và §8.1); các dòng sản xuất giữ giá trị PN canonical của riêng chúng và không bao giờ phụ thuộc vào bản ghi này. |
| 5 | `WorkOrder` | Vỏ đơn hàng business: Work Order Number bên ngoài có thể null (một chuỗi tùy ý, không diễn giải; `NULL` khi chưa biết, hiển thị `—`, §5), ngày nhận, ngày đến hạn có thể null và một status. |
| 6 | `WorkOrderDemand` | Số lượng yêu cầu của một PN cho một Work Order: request type, requested quantity, due date, priority, các Job Number bên ngoài, requester, reason và notes. Chỉ là business demand và không bao giờ là vị trí sản xuất. |
| 7 | `RouteTemplate` | Định nghĩa route dùng lại được, có thể chọn khi release. |
| 8 | `RouteStep` | Bước có thứ tự của một RouteTemplate: sequence, Area, Operation, thời lượng dự kiến, Machine ưu tiên và hướng dẫn. |
| 9 | `AssignedRoute` | Snapshot độc lập của một route, được gán cho một QuantityFlow **PLANNED** khi release. Thay đổi template không bao giờ làm thay đổi nó. Các bước đã qua là bất biến; chỉ các bước tương lai chưa được tham chiếu mới thay đổi, qua command `ROUTE_ADJUSTED` có audit (§16). Nó là tùy chọn: flow Floating (mặc định) không có. |
| 10 | `QuantityFlow` | Phần có thể truy vết của số lượng vật lý của một PN, được tạo bởi release; là đơn vị sẽ di chuyển về sau. Nó mang `route_mode` (`FLOATING` theo mặc định, hoặc `PLANNED`), tham chiếu snapshot `assigned_route_id` có thể null (chỉ đặt cho `PLANNED`, PROJECT_PROFILE §8.7), và các trường projection vị trí hiện tại được dẫn xuất. |
| 11 | `PartMovement` | Bản ghi sự kiện bất biến và là nguồn sự thật duy nhất của trạng thái sản xuất. Slice này chỉ tạo `RECEIVED`. |
| 12 | Projection vị trí hiện tại | Trạng thái dẫn xuất được duy trì và dựng lại được trên `QuantityFlow` (`current_area_id` và `current_machine_id`). |
| 13 | Audit event | Dòng audit tổng quát chỉ-append cho thay đổi master data, business demand và cấu hình (§16). Đây là hạ tầng lưu trữ, không phải domain aggregate. |

Cấu hình Scan Station (`scan_stations`) là cấu hình ứng dụng và hạ tầng ổn định, **không** phải domain aggregate cốt lõi
(PROJECT_PROFILE §15 Scan Station Persistence). Một release khởi động từ Management không liên quan tới Scan Station nào:
Movement `RECEIVED` của nó để `station_id` là NULL. Bảng `scan_stations` thuộc phần thiết lập môi trường tối thiểu, và
`part_movements.station_id` thuộc các Movement do station ghi (§18).

---

## 3. Relationships

```
Department   1 ──── *  Area
Area         1 ──── *  Operation
WorkOrder 1 ─── *  WorkOrderDemand
RouteTemplate 1 ─── *  RouteStep
QuantityFlow 1 ──── 0..1 AssignedRoute      (snapshot; via quantity_flows.assigned_route_id, PLANNED only)
AssignedRoute 1 ─── *  AssignedRouteStep    (snapshot rows)
QuantityFlow 1 ──── *  PartMovement
Area         1 ──── *  PartMovement (to)    (required; `RECEIVED` has from_area_id NULL)
Operation    1 ──── *  PartMovement         (nullable; recorded when resolved)
```

PN được mang **theo giá trị**, dưới dạng chuỗi PN canonical viết hoa (PROJECT_PROFILE §7), và không bao giờ qua một
`part_number_id` thay thế:

```
WorkOrderDemand.part_number   (canonical PN, kept by the demand)
QuantityFlow.part_number      (canonical PN, kept by the flow)
PartMovement.part_number      (canonical PN, kept by the Movement)
```

**Không có foreign key nào từ các dòng sản xuất tới `part_numbers`.** PartNumber master là metadata hiện tại tùy chọn và
có thể bị xóa cứng (hard delete) mà không đụng tới dữ liệu sản xuất (PROJECT_PROFILE §8.1 và §28). Sự nhất quán PN giữa
một Movement và flow của nó vẫn được ép buộc về mặt cấu trúc, bằng một foreign key kết hợp `(quantity_flow_id,
part_number)` tham chiếu `quantity_flows (id, part_number)` (§17).

PartMovement **không** mang `work_order_demand_id`. Một release được khởi động từ một WorkOrderDemand trên UI, và ngữ
cảnh đó được ghi lại mang tính thông tin trong `metadata` của Movement (§11), nhưng Movement vẫn là hoạt động tại xưởng
ở mức PN, QuantityFlow và số lượng. WorkOrderDemand không sở hữu Movement, và WorkOrderAllocation vẫn tách biệt khỏi cả
hai.

---

## 4. Business Invariants

1. Định danh PN chính là chuỗi PN canonical (viết hoa và không chứa khoảng trắng, PROJECT_PROFILE §7). Nó ổn định, duy
   nhất theo cấu trúc và không bao giờ được suy ra từ tên hiển thị. Các dòng sản xuất mang trực tiếp giá trị canonical
   đó và vẫn hợp lệ dù có hay không có bản ghi PartNumber master.
2. Lưu hoặc sửa một WorkOrder hoặc WorkOrderDemand không bao giờ tạo, thay đổi hay hủy production quantity.
3. Trong slice này, production quantity đi vào hệ thống **chỉ** qua một command release tường minh append một Movement
   `RECEIVED`. (Ngoài slice này, PROJECT_PROFILE còn đưa số lượng vào qua một lần nhận (receipt) tại Scan Station, cũng
   append một `RECEIVED`, và qua một lần cộng thêm `QUANTITY_ADJUSTED` tường minh.) Mọi QuantityFlow được tạo bởi
   release có `RECEIVED` là Movement đầu tiên của nó. Mọi flow đều mang một route mode: một flow `PLANNED` có đúng một
   snapshot AssignedRoute, còn một flow `FLOATING` không có snapshot nào; route trace của nó được dẫn xuất chỉ từ lịch
   sử Movement.
4. Mọi số lượng là số nguyên dương: requested quantity của WorkOrderDemand > 0, số lượng flow > 0, số lượng Movement >
   0.
5. `RECEIVED.quantity` bằng số lượng của flow được tạo.
6. Một flow chiếm đúng một Area hiện tại. Khi một PN trải trên nhiều Area, mỗi Area giữ flow riêng của nó.
7. Release không bao giờ gộp với số lượng active hiện có và không bao giờ ngầm tạo thêm số lượng. Release một PN đang có
   số lượng active cần một xác nhận ý định tường minh trên UI (§8).
8. Lịch sử Movement là bất biến và chỉ-append, và trạng thái hiện tại phải dựng lại được từ nó (§15).
9. Việc tạo QuantityFlow (mang projection `current_area_id` ban đầu), snapshot AssignedRoute và việc insert Movement
   commit nguyên tử, hoặc không gì cả (§13).
10. Một lần gửi release trùng với cùng `device_event_id` và cùng request đã chuẩn hóa trả về kết quả gốc và không tạo gì.
    Cùng `device_event_id` với một request đã chuẩn hóa khác là xung đột idempotency và không tạo gì (§14).
11. Entity không active (Area, Operation, RouteTemplate) không bao giờ nhận release. PartNumber master không có vòng đời
    active hay inactive: chuỗi PN canonical luôn dùng được, có hay không có bản ghi master.

---

## 5. WorkOrder and WorkOrderDemand Validation

### Work Order Number

- `work_order_number` là một chuỗi tùy ý, không diễn giải và **có thể null** (PROJECT_PROFILE §7 Work Order).
- Khi người dùng xác nhận lưu với Work Order Number để trống, `NULL` được lưu và Work Order là một Work Order nội bộ. UI
  hiển thị `—` (placeholder không bao giờ được lưu), gắn nhãn dòng là Work Order nội bộ không có số bên ngoài, và cho
  phép thêm số thật về sau qua một lần sửa có audit.
- Nhiều Work Order có thể cùng giữ `NULL`. Tính duy nhất chỉ áp dụng cho số khác null (một partial unique index, §17).
  Không bao giờ sinh Work Order Number tạm.
- Work Order Number được nhập vào được lưu nguyên văn và không bao giờ bị định dạng lại.
- Tạo một Work Order với một Work Order Number (khác null) đã tồn tại sẽ đưa ra Work Order hiện có thay vì tạo bản trùng.
  Xử lý trùng này chỉ áp dụng khi có nhập Work Order Number.
- Import từ file tuân theo cùng rule một cách idempotent. Mỗi dòng import bắt buộc có Work Order Number, nên Work Order
  có số `NULL` chỉ được tạo thủ công.

### Ngày

- `received_date` là bắt buộc. Khi không nhập, nó mặc định là ngày hiện tại theo lịch của nhà máy (múi giờ
  `SITE_TIMEZONE`), cả khi tạo thủ công lẫn với Work Order được tạo bởi import từ file.
- `work_orders.due_date` có thể null: thiếu due date của Work Order là dữ liệu hợp lệ, không phải lỗi validation
  (PROJECT_PROFILE §8.2). Nó chỉ là giá trị mặc định khi nhập cho due date của các dòng demand.

### Dòng demand

- Mỗi WorkOrderDemand bắt buộc có:
  - một `part_number` canonical hợp lệ, được chuẩn hóa thành chữ hoa và từ chối khoảng trắng (PROJECT_PROFILE §7). Bản
    ghi metadata PartNumber master được tạo ở lần dùng hợp lệ đầu tiên, nhưng dòng demand không bao giờ phụ thuộc vào
    nó;
  - `request_type IN ('NEW','MODIFY')`. Nhập thủ công mặc định là `NEW`; intake tại Scan Station mặc định là `MODIFY`.
    Repair là một ý định di chuyển (movement intent), không bao giờ là request type;
  - `requested_quantity > 0`.
- `due_date` có thể null. Thiếu due date là dữ liệu hợp lệ, không bao giờ là lỗi validation và không bao giờ chặn việc
  lưu (PROJECT_PROFILE §8.3).
- `job_numbers` lưu các Job Number bên ngoài dưới dạng dữ liệu: một danh sách chuỗi tùy ý, tìm kiếm, hiển thị và sắp xếp
  được, và không bao giờ là domain aggregate.

### Khóa sắp xếp demand canonical

Mọi nơi sắp xếp demand đều dùng khóa sắp xếp demand canonical (PROJECT_PROFILE §18):

1. `priority_rank` (hạng Hot trước);
2. rồi `due_date` tăng dần, với **NULLS LAST**;
3. rồi, với demand không có ngày, `received_date` của WorkOrder cha tăng dần;
4. rồi một tiêu chí phân định ổn định và xác định (thứ tự tạo, tức `id` nội bộ).

Slice này tự nó không thực hiện sắp xếp nào như vậy. Những nơi dùng thứ tự này (allocation, board, priority) sở hữu các
index hỗ trợ của chúng.

### Một dòng cho mỗi Part Number trên một Work Order

Một PN canonical xuất hiện **tối đa một lần** trong các dòng WorkOrderDemand hiện tại của một WorkOrder. Cùng một PN trên
cùng một Work Order là một dòng demand mà số lượng được sửa, không bao giờ là dòng thứ hai (GUI_DESIGN §11.2 và §11.3: UI
đưa focus tới dòng hiện có).

Cố ý **không** có unique index cho rule này (§17 không định nghĩa). Application layer ép buộc nó và vì thế phải tự tuần
tự hóa nó:

1. Một lần lưu có thêm dòng demand sẽ lấy row lock của WorkOrder cha.
2. Sau khi được cấp lock, nó đọc lại tập PN canonical của Work Order đó. Vì vậy hai lần lưu đồng thời cùng thêm một PN
   không thể cùng vượt qua một snapshot đọc trước khi lấy lock.
3. Bên thua bị từ chối với lỗi demand trùng thông thường và không ghi gì.

Thứ tự lock vẫn là demand (id tăng dần) trước WorkOrder, cùng thứ tự mà command xóa demand dùng, và command release
không bao giờ đi ngược nó.

### Priority rank (Hot list)

- `priority_rank` có thể null và là nơi lưu priority duy nhất. Một demand là **Hot** đúng khi rank khác NULL, và rank 1
  là priority cao nhất. Không có cờ và không có bảng riêng.
- Các demand có rank mang đúng các rank `1..N`, **duy nhất và liên tục** (ép buộc bởi §17).
- `priority_rank` có đúng hai nơi ghi (IMPLEMENTATION_ROADMAP Phase 12): command Hot list
  (`POST /api/hot-list/changes`) và việc gỡ tự động hoặc gỡ khi xóa dòng trong `app/application/hot_ranks.py`. Tạo và
  sửa Work Order từ chối trường này khi nhập.
- Tại mọi trạng thái đã commit, không demand có rank nào là inactive. Một mục Hot có demand trở thành inactive (Work
  Order của nó hoàn tất, hoặc dòng được allocate đủ) được gỡ tự động, trong cùng transaction, bởi lần xác nhận
  allocation hoặc lần lưu Work Order làm giảm số lượng đã gây ra điều đó.
- Điều kiện được vào danh sách là một Application rule được đánh giá trên dòng đã lock và đọc lại: demand tồn tại, chưa
  có rank, Work Order của nó chưa hoàn tất, và nó **active** (PROJECT_PROFILE §14: `requested_quantity >
  allocated_quantity`). Mỗi dòng demand được chọn và xếp hạng riêng.
- **Một dòng demand Hot chỉ bị xóa khi có cờ xác nhận tường minh.** Không có cờ, sau khi mọi rule xóa khác đã qua, việc
  xóa trả về 409, không ghi gì và nêu rank hiện tại của dòng. Có cờ, mục rời Hot list (đánh số lại liên tục, có audit)
  và dòng bị xóa trong một transaction, dưới Hot advisory lock và các row lock, nên nó tuần tự với command Hot list.
- Command Hot list, và việc gỡ tự động bên trong một allocation làm hoàn tất, cũng có thể đổi rank của một demand thuộc
  Work Order **đã hoàn tất**, kèm dòng audit của nó (§16).

### Sửa demand

- Được phép sửa WorkOrderDemand (Administrator và Manager, PROJECT_PROFILE §8.3). Các lần sửa được audit (§16) và không
  bao giờ đụng tới QuantityFlow hay Movement.
- Một dòng đã release production quantity chỉ còn quyền sửa **hạn chế** (§8a; PROJECT_PROFILE §13; GUI_DESIGN §11).
  Application layer ép buộc điều này, không chỉ UI:
  - `requested_quantity`, `due_date` và `job_numbers` vẫn sửa được;
  - `requested_quantity` không bao giờ được thấp hơn `max(released_quantity, allocated_quantity)`;
  - `request_type`, `requester`, `reason` và `notes` bị từ chối (`part_number` không sửa được trên bất kỳ dòng đã lưu
    nào);
  - một lần sửa mang số lượng không đổi thì không bị xét, nên một dòng đã được allocate vượt demand bởi một correction
    có thẩm quyền vẫn sửa được các trường khác.
- Việc xóa một dòng như vậy vẫn bị từ chối theo PROJECT_PROFILE §13.

---

## 6. PartNumber Normalization, Creation, and Barcode

- Mọi PN đi vào hệ thống đều được chuẩn hóa trước (PROJECT_PROFILE §7):
  1. khoảng trắng đầu và cuối được **cắt bỏ (trim)**;
  2. sau khi trim, giá trị phải khác rỗng và **không chứa khoảng trắng bên trong** (dấu cách, tab, xuống dòng); giá trị
     như vậy bị từ chối, không bao giờ bị lặng lẽ loại bỏ;
  3. giá trị được chuẩn hóa thành **CHỮ HOA**.

  `abc-123`, `ABC-123`, `AbC-123` và `" ABC-123 "` đều là PN canonical `ABC-123`; `"ABC 123"` và `"ABC\t123"` không hợp
  lệ.
- Bản ghi PartNumber master được **tạo ở lần dùng hợp lệ đầu tiên** (PROJECT_PROFILE §8.1); không cần catalogue nạp
  sẵn. Master là metadata hiện tại tùy chọn, có khóa là PN canonical. Các dòng sản xuất không bao giờ tham chiếu nó bằng
  foreign key, nên nó có thể bị xóa cứng (và tạo lại về sau cho cùng PN canonical) mà không đụng tới dữ liệu sản xuất
  (PROJECT_PROFILE §28).
- `POST /api/part-numbers` chỉ tạo mới. Nó lấy PN advisory lock, nên mọi nơi tạo bản ghi master đều tuần tự theo từng PN.
  `GET /api/part-numbers?search=` cũng khớp theo tên đã lưu. Management → Part Numbers sửa master, quản lý ảnh của nó và
  xóa cứng nó. Việc tạo ở lần dùng đầu tiên tuân theo cùng các rule.
- Barcode PN mang chính PN canonical: `PF:PN:<part-number>` (PROJECT_PROFILE §10). Vì PN là chữ hoa canonical, giá trị
  barcode hoàn toàn được dẫn xuất, và không cần một khóa barcode lưu riêng, duy nhất riêng cho PN. Barcode chỉ định danh
  PN và không mã hóa Work Order, số lượng, route hay vị trí nào.

---

## 7. Separation of Demand Save from Production Release

Lưu demand và production release là hai command khác nhau với ranh giới transaction khác nhau:

- **Lưu demand** chỉ ghi `work_orders` và `work_order_demands` (cùng các dòng audit của chúng, §16). Nó không tạo
  QuantityFlow, không tạo Movement và không thay đổi projection nào. UI ghi nhãn demand là "business demand — separate
  from production".
- **Release to production** (§8) không bao giờ bị kích hoạt ngầm bởi việc lưu, import hay sửa demand.

Điều này giữ ranh giới canonical: WorkOrder và WorkOrderDemand biểu diễn business demand và không bao giờ xác định vị trí
sản xuất hiện tại; production release đưa số lượng vật lý vào một cách tường minh.

---

## 8. Explicit Release Command

**Đầu vào:** Work Order và dòng WorkOrderDemand mà release được khởi động từ đó, PN, số lượng release, Route Mode
(`FLOATING` theo mặc định; `PLANNED` kèm lựa chọn template), starting Area và Operation đã xác nhận, cờ xác nhận số lượng
active, và `device_event_id`. Command yêu cầu dòng demand, và PN phải là PN của chính dòng đó. Dòng demand được ghi lại
như ngữ cảnh thông tin để hiển thị audit và là bằng chứng release của §8a; nó không bao giờ làm demand sở hữu Movement
(§3). Một release `FLOATING` không nhận RouteTemplate, và một release `PLANNED` bắt buộc có một.

**Các bước** (một transaction, §13):

1. **Kiểm tra hợp lệ.** PN canonical hợp lệ (đã chuẩn hóa: trim, chữ hoa, không có khoảng trắng bên trong). Area active
   và được cấu hình là starting Area. Một Area **terminal** (`areas.is_terminal`, đầu Stockroom của luồng,
   PROJECT_PROFILE §18) không bao giờ là starting Area; Application layer từ chối nó, không chỉ UI release. Operation
   hợp lệ cho Area đó. RouteTemplate active **khi release là `PLANNED`**. Số lượng lớn hơn 0 và nằm trong remaining
   quantity của demand khởi động (§8a). Một dòng demand đã được allocate đủ từ số lượng đã nhập kho (stocked) thì không
   cần sản xuất và bị từ chối (PROJECT_PROFILE §18). PartNumber master không có trạng thái active hay inactive, và sự
   tồn tại của nó không bao giờ là điều kiện tiên quyết của release.
2. **Xác nhận số lượng active.** Nếu PN có flow active, request phải mang cờ xác nhận tường minh mà UI đặt sau khi hiển
   thị phân bố hiện có; nếu không thì bị từ chối. Không bao giờ tự tạo hay tự gộp.
3. **Snapshot route.** AssignedRoute được snapshot **chỉ cho release `PLANNED`** (§10); release `FLOATING` không tạo
   snapshot nào.
4. **Tạo flow.** QuantityFlow được tạo với `route_mode`, `assigned_route_id` (id của snapshot với `PLANNED`, NULL với
   `FLOATING`, PROJECT_PROFILE §8.7) và projection ban đầu: `current_area_id` là starting Area đã xác nhận (§9, §15).
5. **Append Movement.** PartMovement `RECEIVED` được append. Với flow `PLANNED`, nó tham chiếu bước đầu tiên của
   snapshot; với flow `FLOATING`, `assigned_route_step_id` giữ NULL. Nó ghi Operation đã xác định. `metadata` của nó
   mang request fingerprint (§14), User đã đăng nhập thực hiện release dưới dạng `context.actor_user_id`, và ngữ cảnh
   WorkOrderDemand (§11).
6. **Commit và trả về** flow id, route mode, id snapshot route khi là planned, starting Area, Operation, số lượng và
   Movement id.

Transaction release **không** append dòng `audit_events` tổng quát nào: Movement `RECEIVED` tự nó là bản ghi audit sản
xuất bất biến (§16).

Mọi command có thể làm một PN canonical có số lượng active hoặc demand active đều lấy một PN-level advisory lock dùng
chung, và release lấy nó trước mọi row lock. Vì vậy hai release đồng thời chưa xác nhận của cùng một PN, hoặc một release
và một lần nhận (receipt) tại Scan Station của PN đó, không thể cùng vượt qua kiểm tra số lượng active ở bước 2.

### 8a. Partial and repeated release of one demand

Một WorkOrderDemand có thể được release thành **nhiều phần**, ví dụ 20 trên 50, rồi 12, rồi 18. Mỗi phần là một release
độc lập với `device_event_id` riêng, QuantityFlow riêng và Movement `RECEIVED` riêng. Từ phần thứ hai trở đi PN đã có số
lượng active, nên mỗi phần cũng cần xác nhận tường minh riêng theo §8 bước 2. Không bao giờ gộp gì.

- **Released quantity là dẫn xuất.** `released_quantity(demand)` là tổng `RECEIVED.quantity` trên các Movement có
  `metadata.context.work_order_demand_id` là demand đó (§11, §14). Không có bộ đếm lưu trữ và không có cột. Vì
  `part_movements` là chỉ-append, giá trị không bao giờ lùi hay lệch và không cần đối soát.
- **Remaining quantity là giới hạn cứng.** `remaining_quantity = requested_quantity − released_quantity` được ép buộc
  trên server. Một release vượt quá nó, hoặc mọi release khi nó đã về 0, bị từ chối và không tạo gì. Dòng demand bị lock
  `FOR UPDATE` trong transaction release, nên hai release từng phần đồng thời của cùng một demand được tuần tự hóa và
  không bao giờ cùng nhau release vượt.
- **Sửa hạn chế.** Một demand có bất kỳ số lượng đã release nào giữ quyền sửa hạn chế của PROJECT_PROFILE §13
  (GUI_DESIGN §11):
  - command sửa demand (§7) chấp nhận `requested_quantity`, `due_date` và `job_numbers` cho dòng như vậy và từ chối
    `request_type`, `requester`, `reason` và `notes`;
  - `requested_quantity` không được thấp hơn `max(released_quantity, allocated_quantity)`. Một lần sửa mang số lượng
    không đổi thì không bị xét, nên một dòng đã được allocate vượt demand bởi một correction có thẩm quyền vẫn sửa được
    các trường khác. Vì vậy không thể hạ số lượng xuống dưới mức đã release, còn tăng nó chỉ đơn giản khôi phục
    remaining quantity (và trạng thái dẫn xuất `OPEN` của Work Order);
  - một mục `line_edits` bị từ chối thì không ghi gì, kể cả các trường hợp lệ đi kèm, vì một lần lưu là một transaction;
  - command sửa lấy CÙNG row lock như release trước khi quyết định, và tính lại released quantity dưới lock đó. Hai bên
    tuần tự theo bất kỳ thứ tự đến nào: release commit trước thì lần sửa nhìn thấy nó (và từ chối số lượng thấp hơn
    nó), còn lần sửa commit trước thì release nhìn thấy nó (release đọc lại dòng và giới hạn theo requested quantity
    mới). Vì vậy `released_quantity > requested_quantity` là không thể đạt tới, không chỉ là khó xảy ra;
  - việc xóa vẫn bị từ chối theo PROJECT_PROFILE §13, và một dòng trên Hot list chỉ bị xóa khi có xác nhận của §5;
  - việc sửa header và số của Work Order không bị ảnh hưởng.
- **Read model.** Các read model trả về `released_quantity` và `remaining_quantity` cho từng demand. Một Work Order hiển
  thị `RELEASED` chỉ khi **mọi** dòng demand hiện tại có `remaining_quantity = 0`. Một dòng mới release một phần giữ nó ở
  `OPEN`, vì phần còn lại vẫn release được. `work_orders.status` lưu trữ vẫn là `OPEN`; `RELEASED` và `COMPLETED` là các
  giá trị đọc được dẫn xuất (§17).

---

## 9. QuantityFlow Creation

- Các cột theo PROJECT_PROFILE §8.7. Một release ghi:
  - `id`;
  - `part_number`, PN canonical chữ hoa do chính flow giữ;
  - `quantity`;
  - `status`, là `ACTIVE` khi tạo;
  - `route_mode`, `FLOATING` theo mặc định hoặc `PLANNED`;
  - `assigned_route_id`, tham chiếu snapshot có thể null: là id của AssignedRoute đúng khi `route_mode = 'PLANNED'`,
    NULL với `FLOATING`;
  - `created_at`, và `closed_at` là NULL.
- Các cột projection được duy trì là `current_area_id` và `current_machine_id`, cùng `updated_at`. `current_area_id` là
  NOT NULL và được chính câu INSERT đặt thành starting Area đã xác nhận, nên một dòng QuantityFlow không bao giờ tồn tại
  mà thiếu Area hiện tại hợp lệ. Release để `current_machine_id` là NULL; các Movement xử lý trên Machine duy trì nó
  (§15).
- Lineage không thuộc release. Nó được ghi trong bảng cạnh (edge table) chỉ-append `quantity_flow_lineage` (cha tới con,
  một cạnh cho mỗi con của SPLIT và mỗi nguồn của MERGED), vì một cột tự tham chiếu duy nhất như `parent_flow_id` không
  thể biểu diễn việc gộp N flow thành một (§18).
- Trong slice này số lượng của một flow là bất biến (slice không có SPLIT, MERGED hay `QUANTITY_ADJUSTED`), nên có thể
  kiểm chứng bảo toàn dưới dạng Σ(số lượng flow active theo PN) = Σ(số lượng `RECEIVED` theo PN). Ngoài slice, số lượng
  của một flow vẫn không bao giờ thay đổi ngoài lineage của nó: một lần cộng thêm `QUANTITY_ADJUSTED` đưa vào một flow
  MỚI thay vì sửa số lượng hiện có.

---

## 10. AssignedRoute Snapshot Creation (`PLANNED` releases only)

- Một release `PLANNED` sao chép các bước của RouteTemplate đã chọn vào `assigned_routes` và `assigned_route_steps`:
  `sequence`, `area_id`, `operation_id`, `expected_duration`, `instructions` và `preferred_machine_id`. Bản sao snapshot
  khi split và merge sao chép cùng các trường đó. Tiêu chí tương thích khi gộp ("snapshot bằng nhau về cấu trúc") bao gồm
  `preferred_machine_id`, nên các flow có snapshot chỉ khác nhau ở Machine ưu tiên không bao giờ được gộp.
- Một release `FLOATING` không tạo snapshot; dấu vết route của flow được dẫn xuất từ lịch sử Movement (PROJECT_PROFILE
  §17).
- Snapshot tham chiếu template nguồn của nó (`source_route_template_id`, mang tính thông tin) nhưng độc lập: các lần sửa
  template về sau không bao giờ làm thay đổi nó (PROJECT_PROFILE §8.10).
- Flow tham chiếu snapshot của nó qua `quantity_flows.assigned_route_id` (PROJECT_PROFILE §8.7), foreign key duy nhất
  giữa hai bảng; `assigned_routes` không mang cột ngược `quantity_flow_id`. Tối đa một flow tham chiếu một snapshot
  (`UNIQUE (assigned_route_id)`), và tham chiếu có mặt đúng khi `route_mode = 'PLANNED'` (một CHECK, §17).
- Một route deviation được ghi trên Movement đến (arrival) của lần transfer, không bao giờ trên snapshot. Các bước tương
  lai của snapshot chỉ thay đổi qua Assigned Route adjustment có audit (§16).
- Bước đầu tiên của snapshot phải khớp với starting Area đã xác nhận (và với Operation, khi bước có chỉ định). Không khớp
  là lỗi validation, không phải một điều chỉnh âm thầm.

---

## 11. RECEIVED PartMovement

Các cột theo PROJECT_PROFILE §8.11. Một release ghi Movement `RECEIVED` như sau:

- `movement_type = 'RECEIVED'`, loại duy nhất slice này tạo ra. Check movement-type liệt kê mọi loại hiện tại (§17).
- `from_area_id` là NULL; `to_area_id` là starting Area (NOT NULL); `operation_id` là Operation khởi đầu đã xác định
  (NOT NULL, vì một release hợp lệ luôn xác định hoặc xác nhận tường minh một Operation, §12).
- `quantity` là số lượng của flow; foreign key kết hợp bảo đảm PN khớp với flow.
- `assigned_route_step_id` tham chiếu `assigned_route_steps.id`, một bước snapshot, và không bao giờ là dòng template
  `route_steps` có thể thay đổi, nên các lần sửa template không bao giờ làm thay đổi ngữ cảnh route mà một Movement hiện
  có đã ghi. Một bước được tham chiếu là bước đã qua và không bao giờ thay đổi: trigger cấm UPDATE của §17 từ chối mọi
  lần cập nhật, và foreign key từ chối việc xóa nó.
  - Cột này **có thể null**. Nó được đặt cho `RECEIVED` của flow `PLANNED` (bước đầu tiên của snapshot, mà §10 yêu cầu
    khớp với starting Area đã xác nhận) và NULL cho flow `FLOATING`, vốn không có AssignedRoute.
  - Sự khớp của nó với flow là một **invariant liên bảng** mà không CHECK PostgreSQL thông thường nào biểu diễn được:
    `RECEIVED` của một flow `PLANNED` phải tham chiếu một bước thuộc AssignedRoute **của chính flow đó**, và của flow
    `FLOATING` phải giữ NULL. Giao thức transaction release (§13) ép buộc nó, và các kiểm tra đối soát cùng test kiểm
    chứng nó (§17).
  - Tên cố ý không phải `route_step_id` chung chung, vốn sẽ mơ hồ giữa `route_steps` và `assigned_route_steps`. Nó làm
    rõ thuộc tính minh họa `route_step_id` của PROJECT_PROFILE §8.11.
- Các cột mà release để NULL, vì một release từ Management không liên quan tới Scan Station, Worker hay Machine nào:
  - `station_id` (Scan Station của một Movement do station ghi, foreign key tới `scan_stations.station_id`);
  - `worker_id` (Worker được nhận diện tại station) và `scan_session_id` (Worker Session đã quét, foreign key tới
    `worker_sessions`, tức `ScanSession` của PROJECT_PROFILE);
  - `source_machine_id` và `destination_machine_id`;
  - `movement_reason` (cột canonical cho movement intent có kiểu, với giá trị duy nhất `REPAIR` chỉ tồn tại trên một
    `TRANSFERRED`, PROJECT_PROFILE §8.11), `reason` dạng văn bản tự do, và `reverses_movement_id`.
- `command_sequence` là 1: một release là command một Movement.
- `occurred_at` và `server_received_at`: xem §14.
- `device_event_id` là NOT NULL và là khóa idempotency (§14). Nó duy nhất cùng với `command_sequence`; vì một release có
  đúng một Movement, một `device_event_id` gọi tên đúng một release.
- `metadata` mang request fingerprint xác định (§14). Nó ghi User đã đăng nhập thực hiện release dưới dạng
  `context.actor_user_id` (do server suy ra, không bao giờ lấy từ request; các release ghi trước khi có đăng nhập không
  mang giá trị này) và WorkOrderDemand khởi động dưới dạng `context.work_order_demand_id` để hiển thị audit. Không điều
  nào trong đó tạo quyền sở hữu: WorkOrderDemand không bao giờ sở hữu Movement (§3).
- Dòng là bất biến và chỉ-insert. UPDATE và DELETE bị thu hồi khỏi application role, và một trigger raise-on-write từ
  chối chúng, theo rule ưu tiên ràng buộc PostgreSQL trước. Việc thu hồi được `apply-grants` áp dụng trên production
  stack; database development và test giữ một owner role (§17). Cơ chế bảo vệ này ràng buộc ứng dụng mọi lúc. Bảo trì
  retention lịch sử Movement (PROJECT_PROFILE §28) là một đường riêng, có đặc quyền, dành cho Administrator và nằm ngoài
  slice này.

---

## 12. Starting Area and Operation Resolution

- Starting Area đến từ cấu hình Department và route (PROJECT_PROFILE §22): bước đầu tiên của snapshot route, với giá trị
  mặc định do cấu hình đặt (ví dụ Material). UI release xác nhận nó; nó không bao giờ được đoán.
- Operation được xác định từ cấu hình của starting Area. Một Operation duy nhất được hỗ trợ thì tự xác định. Nhiều
  Operation được hỗ trợ thì cần xác nhận tường minh trong luồng release; sự mơ hồ chặn việc ghi cho tới khi được xác nhận.
- Vì vậy Operation tồn tại trong schema trước mọi Movement mang Operation: `RECEIVED` ghi nó ngay từ đầu.

---

## 13. Transaction Boundary

Một lần gửi release là một database transaction:

```
BEGIN
  idempotency check on device_event_id + request fingerprint (§14)
  validate PN / Area / Operation / RouteTemplate / quantity
  active-quantity confirmation check
  INSERT assigned_routes + assigned_route_steps   -- PLANNED releases only
  INSERT quantity_flows            (route_mode; assigned_route_id = snapshot id
                                    for PLANNED, NULL for FLOATING;
                                    current_area_id = confirmed starting Area)
  INSERT part_movements (RECEIVED; assigned_route_step_id = snapshot first
                         step for PLANNED, NULL for FLOATING)
COMMIT
```

- Kiểm tra idempotency chạy đầu tiên, nên một lần thử lại của release đã commit trả về kết quả gốc kể cả khi các entity
  đã đổi trạng thái kể từ đó. Nó chạy lại sau khi được cấp PN advisory lock (§8), nên một lần thử lại giống hệt chạy
  đồng thời đã chờ lock sẽ phát lại kết quả gốc thay vì vấp phải số lượng mà chính bản gốc của nó đã làm thành active.
- QuantityFlow được insert hoàn chỉnh, với projection `current_area_id` ban đầu đã được đặt. Không bao giờ tồn tại dòng
  trung gian không hợp lệ hoặc thiếu projection (`current_area_id` là NOT NULL).
- Mọi lỗi đều rollback toàn bộ. Một Movement không bao giờ được ghi mà thiếu flow và snapshot của nó, và ngược lại;
  không trạng thái release dở dang nào có thể commit.
- Không ghi dòng `audit_events` tổng quát nào ở đây: Movement `RECEIVED` là bản ghi audit sản xuất (§16).
- PartMovement vẫn là nguồn sự thật, và projection vẫn dựng lại được từ nó (§15).
- Lưu demand là một transaction riêng, xảy ra trước đó (§7).
- Không có hiệu ứng phụ bên ngoài nào nằm trong transaction.

---

## 14. Idempotency and Retry Behavior

### Khóa release và fingerprint

- Client sinh một `device_event_id` (một UUID) cho mỗi lần gửi release và dùng lại nó ở mọi lần thử lại tầng truyền tải
  (timeout, mất kết nối, kết quả không rõ). Unique constraint trên `(device_event_id, command_sequence)` bảo đảm ghi tối
  đa một lần, vì release là command một Movement.
- Một `device_event_id` trùng chỉ an toàn khi nó mang **cùng request đã chuẩn hóa**. Khi nhận, server tính một **request
  fingerprint** xác định: một hash canonical tối thiểu trên PartNumber, số lượng release, Route Mode (và RouteTemplate
  khi `PLANNED`), starting Area, Operation và ngữ cảnh WorkOrderDemand khởi động. Nó lưu fingerprint trong `metadata` của
  Movement `RECEIVED` (§11). Không có bảng idempotency riêng: dòng Movement, tìm theo `device_event_id` của nó, chính là
  bản ghi idempotency.
- Kết quả khi phát lại:
  - cùng `device_event_id` và cùng fingerprint: trả về nguyên vẹn kết quả gốc đã commit và không tạo gì. Kết quả được dựng
    từ Movement `RECEIVED` bất biến, nên nó gồm cả starting Area tại thời điểm release kể cả khi flow đã di chuyển;
  - cùng `device_event_id` và fingerprint khác: trả về lỗi xung đột idempotency tường minh và không tạo gì. Sự không khớp
    báo hiệu lỗi phía client (một id bị dùng lại sai cho ý định mới) và không bao giờ được âm thầm chấp nhận.
- Một ý định release **mới** nhận `device_event_id` mới. Khi đó nó bị rule xác nhận số lượng active (§8 bước 2) chặn
  lại, nên một lần release trùng do vô ý vẫn cần con người xác nhận tường minh.

### Phát lại bởi một User khác

Một command Management được phát lại bởi một User khác với User đã ghi nó bị từ chối với 409 `recorded_by_another_user`,
và không ghi gì. Điều này áp dụng cho release, Management allocation và lần đảo ngược (reversal) của nó, beyond-demand
correction, thay đổi Hot list và Assigned Route adjustment. Một bản ghi tạo trước khi có đăng nhập, không có User được
ghi, được coi là do User khác ghi. Cùng một User phát lại từ session hoặc trình duyệt khác thì phát lại bình thường.
Fingerprint được kiểm tra trước, và danh tính không bao giờ là một phần của fingerprint.

### Idempotency của các command Management liên quan

- **Beyond-demand correction.** Correction là một command có kiểu, dùng chung không gian `device_event_id` của các
  allocation. Fingerprint của nó mang giá trị `command` riêng và không bao giờ mang danh tính người thực hiện.
- **Assigned Route adjustment.** Dòng audit của adjustment là bản ghi idempotency của nó. UNIQUE partial index trên
  `device_event_id` của nó (§17) làm nó không có race, khác với Hot list vốn dùng advisory lock (§16). Fingerprint bao
  gồm flow, các id bước tương lai đã đọc, các bước mới và lý do, không bao giờ gồm danh tính người thực hiện. Một lần
  phát lại trả về kết quả đã ghi chỉ từ dòng audit.

### Bộ đọc audit trail

`GET /api/tracking/audit-trail?part_number=` chỉ đọc và không ghi gì. Với một PN canonical, nó liệt kê:

- các dòng `audit_events` của bản ghi master của PN;
- các dòng `audit_events` của các Work Order và dòng Work Order Demand yêu cầu PN đó. Một dòng đã bị xóa được tìm lại qua
  dòng `CREATED` của nó; các dòng header và hoàn tất của một Work Order được dùng chung cho mọi PN trên Work Order;
- các dòng `audit_events` của các dòng `AssignedRoute` thuộc các flow của PN;
- các dòng Management và mọi lần đảo ngược trong `work_order_allocations` trên các dòng demand đó, vốn là bản ghi audit
  của chính chúng.

Các dòng được sắp theo `(occurred_at DESC, source, id DESC)` và phân trang bằng con trỏ `(source, id)`. Bộ đọc không thêm
index hay đối tượng schema nào. Các truy vấn của nó dùng `ix_audit_events_entity_type_entity_id_id`; truy vấn tìm dòng đã
xóa chỉ dùng được phần tiền tố `entity_type` của index đó, nên nó quét các dòng audit `WorkOrderDemand` của mọi event
type (kể cả các dòng rank Hot), và phạm vi được tính lại ở mỗi trang.

### Phạm vi

- Idempotency trong slice này là thuộc tính của command **release**, vì release là command duy nhất ở đây đưa số lượng
  vật lý vào và là command mà một lần thử lại có thể nhân đôi số lượng.
- Lưu demand (§7) cố ý không mang khóa idempotency. Nó chỉ ghi business demand: lưu lặp lại không tạo QuantityFlow và
  Movement, một Work Order Number đã nhập là duy nhất (§5, partial unique index), và kết quả lặp lại được duy nhất là một
  Work Order nội bộ thứ hai giữ `NULL`, điều mà §5 cho phép tường minh. Thêm khóa ở đó sẽ đổi hợp đồng intake mà không
  bảo vệ tính toàn vẹn số lượng.
- Import từ file cũng không cần khóa: Work Order Number bắt buộc và duy nhất là khóa idempotency của nó, nên phát lại
  một file không tạo gì mới.

### Ngữ nghĩa online

`occurred_at` và `server_received_at` có thể cùng do server gán và bằng nhau. **Không có hàng đợi sự kiện offline cục
bộ**: một lần gửi không tới được server sẽ thất bại một cách hiển thị và bị chặn khi mất kết nối (GUI_DESIGN §3.6).
`device_event_id` phục vụ idempotency của request hiện nay và vẫn tương thích với bất kỳ thiết kế offline nào trong
tương lai được duyệt riêng.

---

## 15. Derived Current-Position Projection

- `quantity_flows.current_area_id` và `quantity_flows.current_machine_id` là một projection được duy trì, một biện pháp
  hiệu năng cho các đường đọc nóng (tồn theo Area, board).
- `current_area_id` được chính câu INSERT QuantityFlow đặt khi release, trong cùng transaction với Movement `RECEIVED`
  (§13). Các loại Movement về sau cập nhật projection trong transaction Movement của chính chúng. Ví dụ, `AREA_COMPLETED`
  xóa `current_machine_id` và giữ `current_area_id`: số lượng đã xong chờ trong Area của nó ở trạng thái
  `READY_TO_TRANSFER`.
- Lịch sử PartMovement vẫn là nguồn sự thật. Giá trị của `current_area_id` được định nghĩa là `to_area_id` của Movement
  có hiệu lực mới nhất của flow, và một thủ tục replay phải dựng lại (hoặc khẳng định) được mọi projection chỉ từ lịch sử
  Movement.
- Không gì đọc projection như thẩm quyền cho một quyết định quan trọng về tính đúng mà không giữ row lock của flow bên
  trong một transaction.

---

## 16. Audit Persistence Model

### Những gì slice này audit

Slice audit (PROJECT_PROFILE §28):

- việc tạo và sửa Work Order, và việc tạo và sửa WorkOrderDemand: ai, khi nào và cái gì đã thay đổi;
- việc tạo PN master (barcode được dẫn xuất từ PN canonical, §6, nên không cấp gì riêng);
- production release, được audit bởi chính PartMovement `RECEIVED`, bản ghi sản xuất bất biến. User thực hiện release
  (`context.actor_user_id`) và ngữ cảnh WorkOrderDemand nằm trong `metadata` của nó (§11).

Bản ghi lịch sử không bao giờ biến mất, và lịch sử sửa demand không bao giờ được âm thầm ghi đè giá trị trước đó.

### Hai cơ chế

Lưu trữ audit có hai cơ chế bổ trợ nhau với trách nhiệm khác nhau:

1. **PartMovement là bản ghi audit sản xuất.** Nó là nguồn sự thật duy nhất của trạng thái sản xuất, replay được thành
   projection (§15), và là bản ghi audit duy nhất cho production release và mọi hoạt động sản xuất. Một hành động sản
   xuất có một bản ghi audit: transaction release **không** ghi dòng audit tổng quát trùng lặp nào (§13).
2. **Một bảng tổng quát chỉ-append, `audit_events`, chỉ ghi các thay đổi master data, business demand và cấu hình.** Nó
   bao gồm WorkOrder, WorkOrderDemand và PartNumber; Worker, các entity cấu hình môi trường, cấu hình Machine, application
   policy, Planned Routes, User và Role, và thiết bị Scan Station; và correction `ROUTE_ADJUSTED` đối với các bước tương
   lai của một AssignedRoute, là correction hướng dẫn route duy nhất được audit ở đây. Correction đó không di chuyển số
   lượng, nên nó không bao giờ là một Movement. Các dòng là lịch sử mô tả để hiển thị và truy trách nhiệm. Chúng **không
   bao giờ** được replay để dựng trạng thái và không bao giờ mô tả di chuyển số lượng. Đây cố ý không phải một framework
   event sourcing.

### Các cột của `audit_events`

Các ràng buộc được liệt kê ở §17.

| Cột | Ý nghĩa |
|---|---|
| `id` | Khóa chính identity kiểu `bigint`; thứ tự của nó là thứ tự ghi. |
| `event_type` | `CREATED`, `UPDATED`, `DELETED` (xóa cứng bản ghi master và cấu hình) hoặc `ROUTE_ADJUSTED`. |
| `entity_type` | Entity được audit: `WorkOrder`, `WorkOrderDemand`, `PartNumber`, `Worker`, `Department`, `Area`, `Operation`, `ScanStation`, `MachineAssetTagConfig`, `Machine`, `ApplicationPolicy`, `RouteTemplate`, `User`, `Role`, `ScanStationDevice` hoặc `AssignedRoute`. |
| `entity_id` | Khóa của dòng được audit, lưu dạng text. Nó đa hình theo thiết kế, nên không có foreign key. Với `WorkOrder` và `WorkOrderDemand` đó là khóa chính nội bộ; với `PartNumber` đó là chuỗi PN canonical (khóa tự nhiên của master); các entity type khác được mô tả bên dưới. Tính toàn vẹn được bảo đảm bằng việc ghi dòng audit trong cùng transaction với thay đổi được audit. |
| `actor_reference` | Text cũ có thể null, được giữ cho lịch sử và không bao giờ được điền ngược (backfill). Không nơi ghi nào đã chuyển đổi còn ghi nó, nên nó là NULL trên mọi dòng mới. Trên các dòng cũ nó giữ một định danh tác nhân development hoặc hệ thống được cấu hình tường minh, hoặc NULL. |
| `actor_user_id` | Foreign key kiểu integer có thể null tới `users (id)` (`fk_audit_events_actor_user_id_users`): User đã đăng nhập, **do server suy ra chỉ từ principal của session và không bao giờ từ body của request**. Xem "User thực hiện" bên dưới. |
| `occurred_at` | `timestamptz`. |
| `before_data` / `after_data` | Snapshot `jsonb` của các trường được audit; `before_data` là NULL với sự kiện tạo. |
| `metadata` | `jsonb` cho chi tiết ngữ cảnh. |

### Ánh xạ sự kiện cho các entity của slice này

- Tạo và sửa Work Order: `CREATED` và `UPDATED` trên `WorkOrder`.
- Tạo và sửa WorkOrderDemand: `CREATED` và `UPDATED` trên `WorkOrderDemand`.
- Tạo PN master: `CREATED` trên `PartNumber`, với PN canonical là `entity_id`. Sửa PN master: `UPDATED`. Đổi ảnh:
  `UPDATED` (ghi digest của ảnh). Xóa cứng: `DELETED`, với `before_data` giữ snapshot cộng digest của ảnh. Snapshot
  `PartNumber` là `{part_number, name, current_revision, erp_id}`.
- Production release ánh xạ tới **không** dòng `audit_events` nào; bản ghi audit của nó là Movement `RECEIVED` (§13).

### Thay đổi priority của Hot list

- Một thay đổi priority được audit thành một dòng `UPDATED` trên `WorkOrderDemand`, mỗi dòng cho một demand có
  `priority_rank` thay đổi (kể cả các demand được đánh số lại để lấp khoảng trống), ghi trong cùng transaction với rank:
  - `before_data = {"priority_rank": old}` và `after_data = {"priority_rank": new}`, trong đó NULL nghĩa là ngoài danh
    sách;
  - `metadata.hot_list_change` giữ `device_event_id` của command, `action` của nó, request `fingerprint`, `sequence` của
    dòng trong command, và một **snapshot danh tính** chụp tại thời điểm command (`work_order_demand_id`, `part_number`,
    `work_order_id`, `work_order_number`). Snapshot cho phép một lần phát lại command dựng lại `changes` của nó chỉ từ
    các dòng audit, kể cả sau khi một demand đã bị xóa.
- Dòng audit cũng là bản ghi idempotency của command. Khác với cơ chế unique constraint của §14, việc tra cứu được làm
  cho không có race bởi Hot advisory lock.
- Các thay đổi rank thực hiện ngoài command ghi cùng loại dòng. Khi đó `metadata.hot_list_change` mang:
  - `action` `AUTO_REMOVE` (một allocation hoặc một lần lưu Work Order làm giảm số lượng đã làm demand thành inactive)
    hoặc `LINE_DELETE` (việc xóa có xác nhận một dòng Hot);
  - `sequence`, snapshot danh tính, và một khối `cause` (`trigger`, `reference`, và danh sách `removed` với từng
    `reason`);
  - **không** có `device_event_id` hay `fingerprint` ở cấp đó, nên việc tra cứu idempotency của command không bao giờ
    nhìn thấy chúng.
- Không thêm từ vựng audit nào cho priority.
- Việc ghi rank trên demand của một Work Order đã hoàn tất là một ngoại lệ được chấp nhận và được ghi nhận đối với việc
  Work Order đó chỉ-đọc (IMPLEMENTATION_ROADMAP Phase 12): ghi priority không phải là sửa Work Order. Hai nơi thực hiện
  điều này: việc gỡ tự động bên trong allocation làm hoàn tất, và thao tác REMOVE hoặc MOVE của manager, qua command,
  với một mục còn sót lại từ trước khi rule đó tồn tại.

### Dấu hiệu intake của import từ file

- Một Work Order được tạo bởi import từ file mang `metadata = {"intake": {"channel": "FILE_IMPORT"}}` chỉ trên dòng
  `WorkOrder` `CREATED` của nó; các dòng demand và Part Number của nó không thay đổi. Một Work Order tạo thủ công không
  ghi khóa `intake` nào.
- Một lần lưu import thay đổi một Work Order hiện có mang cùng dấu hiệu trên mỗi dòng `WorkOrderDemand` `UPDATED` hoặc
  `CREATED` mà nó append, và không ghi dòng header `WorkOrder` nào. Các dòng Part Number `CREATED`, hoàn tất và rank Hot
  mà nó có thể gây ra giữ metadata riêng và không có khóa `intake`.
- Dấu hiệu này không cần cột, index hay CHECK nào. Lượt import, batch của nó và tên file không được lưu.

### Cấu hình Worker

- Worker được audit với `entity_type` `Worker` (cấu hình danh tính audit tại Scan Station, không bao giờ là hoạt động sản
  xuất). Event type `DELETED` được dùng cho việc xóa cứng bản ghi master và cấu hình; nơi ghi đầu tiên của nó là việc xóa
  cứng Part Number.
- Mỗi lần ghi Worker có hiệu lực append đúng một dòng trong cùng transaction với lần ghi. `entity_id` là id nội bộ của
  Worker dạng text, `actor_reference` là NULL và `metadata` là NULL.
- Một dòng hồ sơ chụp `{name, badge_barcode, is_active}` (`before_data` NULL với `CREATED`). Một dòng avatar chụp
  `{"avatar": null | {content_type, byte_size, sha256}}`: một digest, không bao giờ là byte ảnh.
- Dòng Worker bị lock trước, nên trong mỗi khía cạnh (hồ sơ, avatar), `before_data` của một dòng là `after_data` của dòng
  trước. Hai khía cạnh đan xen và không nối chuỗi với nhau.
- Lần ghi bị từ chối và lần ghi không thay đổi gì thì không append gì.

### Cấu hình môi trường

- Các lần ghi môi trường được audit với năm giá trị `entity_type`: `Department`, `Area`, `Operation`, `ScanStation` và
  `MachineAssetTagConfig` (định dạng Asset Tag).
- `entity_id` là id nội bộ dạng text với Department, Area và Operation; là Station ID với `ScanStation`; và là `'1'` với
  singleton `MachineAssetTagConfig`.
- Mỗi lần tạo hoặc cập nhật có hiệu lực append đúng một dòng `CREATED` hoặc `UPDATED` trong cùng transaction. Một PATCH
  nhiều trường là một dòng; lần ghi không thay đổi gì, bị từ chối hoặc thua race thì không append gì.
- Các snapshot là danh sách trường tường minh:
  - Department: `{name, is_active, board_seconds_per_row, board_min_page_seconds}`;
  - Area: `{department_id, name, barcode_value, description, color, icon_url, is_terminal, is_active,
    worker_identification_mode, fixed_worker_id}`, cộng `worker_session_timeout_minutes` (giá trị ghi đè theo Area,
    `null` khi không đặt);
  - Operation: `{area_id, code, name, description, default_expected_duration_seconds, is_external, is_active}`, với
    thời lượng tính bằng giây dạng số JSON và `null` khi không đặt;
  - ScanStation: `{area_id, is_active}`;
  - MachineAssetTagConfig: `{prefix, digits}`. `next_sequence` (bộ đếm không-dùng-lại khi tạo Machine) không phải cấu
    hình và không bao giờ được audit.
- Một cột thêm về sau chỉ được audit khi nó được thêm vào snapshot của entity.
- Mỗi lần cập nhật lock dòng của nó trước, theo chế độ mà chính câu UPDATE của nó dùng, và chụp snapshot dưới lock. Vì
  vậy các dòng liên tiếp của một entity nối chuỗi: mỗi `before_data` bằng `after_data` trước đó trên mọi khóa có mặt ở cả
  hai.
- Không có backfill. Dòng audit đầu tiên của một cấu hình đã tồn tại trước khi bắt đầu audit là một `UPDATED` có
  `before_data` giữ trạng thái tìm thấy.
- Theme ưa thích của Scan Station (`scan_stations.theme_preference`) là tùy chọn hiển thị, không phải cấu hình. Nó không
  bao giờ được audit và không bao giờ vào snapshot `ScanStation`.

### Cấu hình Machine

- Các lần ghi cấu hình Machine được audit với `entity_type` `Machine`; `entity_id` là id nội bộ của Machine dạng text.
- Mỗi lần tạo, sửa metadata hoặc ngữ cảnh bảo trì, bắt đầu bảo trì hoặc kết thúc bảo trì có hiệu lực append đúng một dòng
  `CREATED` hoặc `UPDATED` trong cùng transaction.
- Snapshot là danh sách khóa tường minh `{area_id, name, asset_tag, description, manufacturer, model, serial_number,
  installed_on, notes, maintenance_since, maintenance_note, maintenance_expected_return}`. Ngày là text ISO-8601, và
  `maintenance_since` là một thời điểm UTC ISO-8601, nên text không bao giờ phụ thuộc múi giờ của kết nối.
- Các khóa bị loại: `id` (nó là `entity_id`); `retired_on` (thuộc `machine_lifecycle_events`, nên không chuyển trạng
  thái nào được ghi hai lần); `state_changed_at` (tuổi của trạng thái runtime, do các command sản xuất thay đổi);
  `created_at` và `updated_at`.
- Việc cho nghỉ (retirement) và kích hoạt lại (reactivation) chỉ được ghi trong `machine_lifecycle_events`. Một lần cho
  nghỉ hoặc kích hoạt lại đơn thuần không append dòng audit nào. Bản nháp Save mà một lần cho nghỉ áp dụng, hoặc việc đổi
  tên, chuyển Area hay kết thúc bảo trì của một lần kích hoạt lại, append một dòng `UPDATED` có `metadata` là
  `{"machine_lifecycle_event_id": <id>}`.
- Mỗi lần ghi Machine lock dòng Machine trước (`FOR NO KEY UPDATE`; cho nghỉ giữ `FOR UPDATE`; tạo mới không lấy lock
  nào trên `machines`), nên các dòng liên tiếp của một Machine nối chuỗi như với các entity môi trường.
- Không có backfill.

### Application policy, mật khẩu và đăng nhập

- Các lần ghi policy toàn cục được audit với `entity_type` `ApplicationPolicy`. `entity_id` là mục Administration:
  `worker-sessions` (timeout của Worker session), `correction-permissions` (policy lý do Undo), `due-soon` (panel cảnh báo
  Due Soon trong Settings), `data-retention` (History archival & purge) và `sign-in` (panel Settings → User sign-in).
- Mỗi lần `PUT` có hiệu lực trên một trong các mục này append đúng một dòng `UPDATED` trong cùng transaction; một `PUT`
  không thay đổi gì thì không append gì. Các snapshot là:

  | Endpoint | Snapshot |
  |---|---|
  | `PUT /api/policies/worker-sessions` | `{worker_session_timeout_minutes, badge_confirm_done, badge_confirm_queue, badge_confirm_undo}` |
  | `PUT /api/policies/correction-permissions` | `{undo_reason_required}` |
  | `PUT /api/policies/due-soon` | `{due_soon_min_days, due_soon_lead_time_percent, due_soon_max_days}` |
  | `PUT /api/policies/data-retention` (đặt hoặc xóa khoảng thời gian) | `{retention_period_months}` |
  | `PUT /api/policies/sign-in` | `{user_session_expires, user_session_days, sign_in_lockout_attempts, sign_in_lockout_minutes, require_password_change}` |

  Dòng `sign-in` mang `actor_user_id` là User đã đăng nhập.
- Các lần ghi mật khẩu append dòng `UPDATED` cho entity type `User` với snapshot `{password_set, password_temporary,
  password_changed_at}` (`password_changed_at` dạng text ISO-8601; không bao giờ là hash, salt, token hay mật khẩu) và
  khóa metadata `password_change` (`SET_BY_ADMINISTRATOR`, `CHANGED_BY_USER` hoặc `RECOVERY_CLI`; lệnh khôi phục còn ghi
  `source: cli`).
- Administrator đầu tiên được tạo bởi first-run setup append dòng `User` `CREATED` và dòng mật khẩu `UPDATED` với
  `source: first-run-setup` và `actor_user_id` NULL.
- Đăng nhập, đăng xuất, lần thử thất bại, khóa tài khoản và kết thúc session được ghi log, không bao giờ được audit.
- Các dòng Worker Session và việc đóng chúng, kể cả đăng nhập tại cổng xác nhận (gate), là danh tính audit sản xuất
  (PROJECT_PROFILE §9 ScanSession và §19 Worker Sessions; `worker_sessions`, `scan_session_id`), không bao giờ là dòng
  `audit_events`.

### Planned Routes

- Các lần ghi cấu hình Planned Routes được audit với `entity_type` `RouteTemplate`; `entity_id` là id nội bộ của template
  dạng text.
- Mỗi lần ghi có hiệu lực append đúng một dòng trong cùng transaction: `CREATED` (tạo), `UPDATED` (sửa hoặc lưu trữ
  (archive)) hoặc `DELETED` (xóa một template chưa từng được dùng). Lần ghi không thay đổi gì hoặc bị từ chối thì không
  append gì.
- Snapshot là danh sách khóa tường minh `{name, description, archived_at, steps}`, với `archived_at` dạng text UTC
  ISO-8601 và mỗi bước là `{sequence, area_id, operation_id, expected_duration_seconds, preferred_machine_id,
  instructions}` (thời lượng tính bằng giây dạng số JSON).
- Snapshot Assigned Route và release không bao giờ được audit dưới dạng `RouteTemplate`. Chỉ việc điều chỉnh một snapshot
  được audit, dưới dạng `AssignedRoute` (bên dưới).

### User và role

- User và role được audit với `entity_type` `User` và `Role`. `entity_id` là id nội bộ dạng text, `actor_reference` giữ
  NULL và `actor_user_id` mang User đã đăng nhập.
- Mỗi lần ghi có hiệu lực append đúng một dòng trong cùng transaction:
  - tạo role là `CREATED` với `before_data` NULL và `after_data` `{name, permissions}` (các permission key sắp theo giá
    trị);
  - đổi tên role, hoặc một thay đổi grant hay revoke, là một dòng `UPDATED` với cùng snapshot trước và sau;
  - tạo user là `CREATED` với snapshot hồ sơ `{login_name, display_name, role_id, is_active}`;
  - sửa hồ sơ user là một dòng `UPDATED` với snapshot đó trước và sau;
  - đổi avatar user là một dòng `UPDATED` với `{"avatar": null | digest}`: một digest, không bao giờ là byte ảnh, như với
    Worker.
- Mỗi lần ghi role và user trước tiên lấy advisory lock `partflow:user-administration` rồi đọc lại các quyền của user đang
  thực hiện. Cơ chế bảo vệ quản lý quyền và các số đếm người nắm quyền là các lần đọc thường dưới lock đó. Sau đó dòng
  role hoặc user bị lock, nên các dòng liên tiếp của một entity nối chuỗi.
- Ba role được seed không có dòng `CREATED`; `before_data` của lần sửa đầu tiên là giá trị seed.
- Theme ưa thích của User (`users.theme_preference`) là tùy chọn hiển thị chỉ được ghi bởi nút chuyển của chính User
  (`PUT /api/session/theme-preference`). Nó không bao giờ được audit, không bao giờ nằm trong snapshot `User`, và không
  thay đổi `users.updated_at`. Lần ghi của nó chỉ lock dòng User (`FOR NO KEY UPDATE`) và không lấy advisory lock
  user-administration, vì nó không thay đổi grant, role, credential hay hoạt động nào.
- Lần ghi không thay đổi gì hoặc bị từ chối thì không append gì.

### Thiết bị Scan Station

- Việc enroll thiết bị station được audit với `entity_type` `ScanStationDevice`; `entity_id` là id nội bộ của thiết bị
  dạng text.
- Cấp một mã enrollment append một dòng `CREATED` mang `actor_user_id` của User cấp mã.
- Việc tự kích hoạt của station append một dòng `UPDATED` không có User (`actor_user_id` NULL, `metadata.source =
  "station-activation"`). Kích hoạt một thiết bị thay thế thiết bị khác append thêm một dòng `UPDATED` cho thiết bị bị
  thay (`metadata.replaced_by_device_id`).
- Thu hồi append một dòng `UPDATED` mang User thu hồi.
- Mã, token hay digest không bao giờ được audit, và `last_seen_at` là metadata liên lạc của thiết bị, không được audit.
- Các command Scan Station đọc grant của role áp dụng tại Scan Station bằng một lần đọc thường và không bao giờ lock
  `users`, `roles` hay `role_permissions`.

### Assigned Route adjustment

- Việc điều chỉnh có thẩm quyền các bước tương lai của một flow `PLANNED` được audit với event type `ROUTE_ADJUSTED` và
  `entity_type` `AssignedRoute`; `entity_id` là `assigned_routes.id` dạng text.
- Mỗi lần điều chỉnh đã commit append đúng một dòng trong cùng transaction; một lần từ chối hoặc một lần phát lại
  idempotent không append gì.
- `before_data` và `after_data` là `{steps: [{id, sequence, area_id, operation_id, expected_duration_seconds,
  preferred_machine_id, instructions}]}` cho toàn bộ route, kể cả các bước đã qua, kèm id bước. Movement và metadata
  deviation gọi tên bước theo id, nên audit phải phân giải được chúng, và các bước mà một lần điều chỉnh xóa chỉ còn tồn
  tại ở đây.
- `metadata.route_adjustment` là `{device_event_id, fingerprint, quantity_flow_id, part_number, reason,
  kept_through_sequence}`, và `actor_user_id` mang User đã đăng nhập.
- Thứ tự lock là: Part Number advisory lock; rồi dòng `quantity_flows` `FOR UPDATE`; rồi Machine, Area và Operation `FOR
  KEY SHARE` theo thứ tự tăng dần; rồi DELETE và INSERT các bước và INSERT audit.
- Không cột `quantity_flows`, Movement, Route Template hay snapshot nào khác được ghi.

### Dòng allocation là bản ghi audit của chính nó

Beyond-demand correction ghi một dòng `work_order_allocations` chỉ-append (với `exceeds_demand`, lý do bắt buộc,
`actor_user_id`, và các con số trước và yêu cầu trong metadata command của nó) và không ghi dòng `audit_events` nào. Các
lần gỡ khỏi Hot list mà nó kích hoạt được audit như mô tả ở trên.

### User thực hiện

- Các nơi ghi điền `actor_user_id`:
  - các lần ghi mật khẩu và các lần ghi policy đăng nhập của user;
  - mọi lần ghi cấu hình Administration: Departments, Areas, Operations, Scan Stations, định dạng Asset Tag, Workers, các
    mục policy, Roles và Users;
  - mọi lần ghi Management: Machines kể cả các sự kiện vòng đời của chúng, Planned Routes, Part Numbers, Work Orders và
    demand, Hot list, Management allocation và reversal, và các dòng hoàn tất Work Order và gỡ Hot mà các lần ghi đó gây
    ra.
- Các dòng do command Scan Station ghi (receipt, allocation tại station và các lần gỡ Hot của chúng, và việc tạo Part
  Number và Work Order của một lần intake tại station) giữ `actor_user_id` NULL. Các dòng cũ cũng giữ NULL.
- `machine_lifecycle_events` và `work_order_allocations` có cùng foreign key `actor_user_id` có thể null
  (`fk_machine_lifecycle_events_actor_user_id_users`, `fk_work_order_allocations_actor_user_id_users`). Cột text cũ
  `machine_lifecycle_events.actor` của chúng (từng lấy từ body request cho nghỉ và kích hoạt lại Machine, nay không còn
  mang nó) được giữ cho lịch sử, không còn được ghi và không bao giờ được backfill.
- Qua các foreign key này, mọi command Management và mọi lần ghi cấu hình có audit lấy lock `FOR KEY SHARE` trên dòng
  `users` của User thực hiện tại câu INSERT. Lock này chỉ xung đột với việc đổi login name của User đó và không tạo chu
  trình với các lock khác (kể cả lock user-administration ở trên).
  - Trong lúc đổi tên như vậy, một command Management chờ **trong khi giữ các advisory lock của nó**: PN lock của nó và,
    với một allocation, một thay đổi Hot list, hoặc một lần sửa số lượng Work Order hay xóa dòng có xác nhận, là Hot
    list lock. Các command xếp hàng trên các lock đó (mọi allocation tại station, receipt và reversal của PN đó, thay đổi
    Hot list, các lần lưu Work Order lấy Hot lock) chờ cho tới khi việc đổi tên commit. Thời gian chờ bị giới hạn bởi
    transaction ngắn của việc đổi tên, và không có chu trình.
  - Một lần ghi cấu hình có audit cũng chờ theo cùng cách, chỉ sau việc đổi login name của User thực hiện.
- Command Scan Station không bao giờ đọc hay lock `users`, `roles`, `role_permissions`, `user_credentials` hay
  `user_sessions`. Command sản xuất không bao giờ đọc hay lock `users`, `roles` hay `role_permissions`.

### Quy tắc

- Mỗi dòng audit commit trong **cùng transaction** với thay đổi mà nó ghi. Một lần ghi có audit mà thiếu dòng audit, hoặc
  một dòng audit mà thiếu lần ghi, phải là không thể.
- Dòng audit là chỉ-append. UPDATE và DELETE bị thu hồi khỏi application role, và một trigger raise-on-write từ chối
  chúng, đúng như với `part_movements`. Trigger tồn tại từ khi bảng được tạo; việc thu hồi trên application role được
  `apply-grants` áp dụng trên production stack, nơi backend kết nối dưới `partflow_app` (§17). Database development và
  test giữ một owner role.
- Một lần sửa append một dòng `UPDATED` mới; các dòng trước không bao giờ bị ghi lại.

---

## 17. Database Constraints and Indexes

Mục này liệt kê các bảng, cột, ràng buộc và index hiện tại mà slice dùng hoặc mà tài liệu trích dẫn ở đây. Migration đã
thêm một cột hoặc ràng buộc sau khi bảng được tạo được ghi trong ngoặc khi điều đó giúp tìm nó.

### Bảng cấu hình

**`departments`**: PK `id`; `UNIQUE (name)`; `is_active`; `created_at`, `updated_at`. Thời gian xoay vòng của Production
Board được cấu hình theo từng Department, tính bằng giây nguyên (`0025_phase13_display_settings`):
`board_seconds_per_row integer NOT NULL DEFAULT 3` với `ck_departments_board_seconds_per_row_range` (1–60), và
`board_min_page_seconds integer NOT NULL DEFAULT 6` với `ck_departments_board_min_page_seconds_range` (1–300).

**`areas`**: PK `id`; `department_id NOT NULL` FK; `name NOT NULL`; `barcode_value` có thể null với `UNIQUE
(barcode_value)` khi đã gán và check không gian tên `PF:AREA:` `ck_areas_barcode_value_namespace` (barcode được server gán
một lần và được bảo vệ bởi trigger `trg_areas_forbid_barcode_change`); `description`, `color`, `icon_url` có thể null;
`is_terminal boolean NOT NULL DEFAULT false`; `is_active`; `created_at`, `updated_at`. Migration thiết lập môi trường
`0003_phase35_environment` đã thêm các cột hiển thị, `is_terminal` và cơ chế bảo vệ barcode. Các cột khác:

- `worker_identification_mode text NOT NULL DEFAULT 'DISABLED'` (`ck_areas_worker_identification_mode`: `DISABLED`,
  `FIXED` hoặc `SCANNED`) và `fixed_worker_id` (FK có thể null `fk_areas_fixed_worker_id_workers`;
  `ck_areas_fixed_worker_shape`: một Fixed Worker tồn tại đúng khi ở chế độ `FIXED`), thêm bởi
  `0019_phase13_worker_identity`. Cùng migration đó thêm các foreign key có thể null `part_movements.worker_id` và
  `work_order_allocations.allocated_by_worker_id`, mỗi cái kèm một CHECK rằng có Worker thì bắt buộc có `station_id`
  (`ck_part_movements_worker_requires_station`, `ck_work_order_allocations_worker_requires_station`), không index, không
  backfill và downgrade từ chối.
- `worker_session_timeout_minutes integer`, giá trị ghi đè theo Area có thể null (`ck_areas_worker_session_timeout_range`:
  NULL hoặc 1–720), thêm bởi `0020_phase13_worker_sessions`.

Không có cột `machine_assignment_mode`: hành vi gán được suy ra từ các Machine của Area (PROJECT_PROFILE §12).

**`operations`**: PK `id`; `area_id NOT NULL` FK; `code NOT NULL`, `UNIQUE (area_id, code)`; `name`; `description` có
thể null; `default_expected_duration` có thể null; `is_external boolean NOT NULL DEFAULT false`; `is_active`;
`created_at`, `updated_at`.

### Part Number master

**`part_numbers`**: master metadata hiện tại tùy chọn.

- PK `part_number text`: PN canonical chữ hoa, một khóa tự nhiên không có id thay thế.
- `CHECK (part_number = upper(part_number COLLATE "C") AND part_number COLLATE "C" !~ '[[:space:]]' AND part_number <>
  '')` ép buộc dạng canonical. Cả hai mệnh đề chạy dưới collation `"C"`, nên chúng chỉ kiểm tra ASCII và độc lập với thư
  viện C của hệ điều hành (`0018_phase13_pn_check_collation`). Chữ hoa Unicode đầy đủ và việc từ chối mọi khoảng trắng
  Unicode thuộc về phần chuẩn hóa domain (§6).
- `created_at`, `updated_at`. Không có `is_active`: PN master không có vòng đời active hay inactive.
- Các chi tiết có thể null `name`, `current_revision` và `erp_id` (văn bản tự do, không duy nhất, không index), và ảnh
  trên dòng: `image` (`bytea`), `image_type`, `image_updated_at`, với `ck_part_numbers_image_shape` (cả ba hoặc không
  có), `ck_part_numbers_image_type` (`image/png`, `image/jpeg`, `image/webp`) và `ck_part_numbers_image_size` (1 byte tới
  2 MiB) (`0023_phase13_part_number_master`).
- Barcode PN được dẫn xuất (`'PF:PN:' || part_number`, PROJECT_PROFILE §10). Không có cột `barcode_value` lưu trữ và
  không có expression index `lower(...)`; chữ hoa canonical làm chúng không cần thiết.
- Không bảng sản xuất nào tham chiếu bảng này bằng foreign key, nên một dòng master có thể bị xóa cứng (và tạo lại về sau
  cho cùng PN canonical) theo PROJECT_PROFILE §28.

### Bảng business demand

**`work_orders`**:

- PK `id`, định danh nội bộ ổn định và không bao giờ là định danh hiển thị cho người dùng.
- `work_order_number` **có thể null**, với **partial unique index** `uq_work_orders_work_order_number`
  (`UNIQUE (work_order_number) WHERE work_order_number IS NOT NULL`), nên nhiều Work Order nội bộ có thể giữ `NULL` trong
  khi các số khác null vẫn duy nhất (§5).
- `received_date NOT NULL`; `due_date` có thể null (thiếu due date của Work Order là dữ liệu hợp lệ, §5).
- `status text NOT NULL DEFAULT 'OPEN'`. Giá trị lưu trữ là `OPEN`; `RELEASED` (§8a) và `COMPLETED` là các giá trị đọc
  được dẫn xuất và không bao giờ được lưu.
- `completed_at`, có thể null: cột ngày hoàn tất canonical (PROJECT_PROFILE §8.2). Nó được dẫn xuất từ các sự kiện
  allocation và không bao giờ được nhập tay: một projection được duy trì của các dòng allocation. Partial index
  `ix_work_orders_completed_at_id` trên `(completed_at, id) WHERE completed_at IS NOT NULL` phục vụ lịch sử hoàn tất được
  phân trang theo keyset.
- `created_at`, `updated_at`.

**`work_order_demands`**:

- PK `id`; FK `work_order_id NOT NULL`.
- `part_number text NOT NULL`: PN canonical chữ hoa do demand giữ, với cùng CHECK dạng canonical như `part_numbers` và
  **không có foreign key** tới master.
- `request_type NOT NULL CHECK (request_type IN ('NEW','MODIFY'))`.
- `requested_quantity int NOT NULL CHECK (requested_quantity > 0)`.
- `allocated_quantity int NOT NULL DEFAULT 0 CHECK (allocated_quantity >= 0)`: một projection được duy trì của các dòng
  allocation active của demand.
- `due_date` có thể null. Thiếu due date là dữ liệu hợp lệ (§5), không bao giờ bị bắt buộc bởi rule validation hay ràng
  buộc; demand không có ngày được xếp sau demand có ngày theo khóa sắp xếp demand canonical (§5).
- `priority_rank` có thể null, với `CHECK (priority_rank IS NULL OR priority_rank >= 1)`
  (`ck_work_order_demands_priority_rank_positive`) và `UNIQUE (priority_rank)` không trì hoãn được
  (`uq_work_order_demands_priority_rank`; các NULL vẫn khác nhau). Command Hot list và việc gỡ tự động hoặc gỡ khi xóa
  dòng đều ghi theo hai lần flush, xóa rồi mới gán, nên không bên nào tạo ra bản trùng tạm thời. Migration thêm cả hai
  ràng buộc (`0013_phase12_priority`) trước tiên chạy một kiểm tra trước có từ chối, giữ nguyên database khi các rank hiện
  có không đúng là `1..N`.
- `job_numbers text[] NOT NULL DEFAULT '{}'`: các chuỗi bên ngoài tùy ý được giữ nguyên văn; danh sách rỗng là hợp lệ.
  Nó chỉ là metadata: không có aggregate `Job`, không foreign key, và không GIN index.
- `requester`, `reason`, `notes` có thể null; `created_at`, `updated_at`.
- Index `ix_work_order_demands_work_order_id` trên `(work_order_id)` và `ix_work_order_demands_part_number` trên
  `(part_number)`.

### Bảng route

**`route_templates`**: PK `id`; `name NOT NULL`; `description` có thể null; `archived_at timestamptz` có thể null
(`NULL` nghĩa là active; một template đã archive không bao giờ được đề xuất cho việc gán route mới); `created_at`,
`updated_at`. **Không có cột `version` và không có framework version hóa template** (PROJECT_PROFILE §8.8): các snapshot
`assigned_routes` hiện có lưu giữ định nghĩa route lịch sử. Một template đã từng được một dòng `assigned_routes` tham
chiếu thì được archive thay vì xóa; `DELETE` cứng chỉ hợp lệ với template chưa từng được tham chiếu.

**`route_steps`**: PK `id`; `route_template_id NOT NULL` FK; `sequence NOT NULL`, `UNIQUE (route_template_id,
sequence)`; `area_id NOT NULL` FK; `operation_id` FK có thể null; `expected_duration` có thể null; `instructions` có thể
null; `preferred_machine_id`, tham chiếu Machine ưu tiên canonical (PROJECT_PROFILE §8.9), một FK có thể null
`fk_route_steps_preferred_machine_id_machines` tới `machines.id` (NO ACTION, vì Machine được cho nghỉ, không bao giờ bị
xóa; `0024_phase13_planned_routes`). Sự khớp giữa Machine ưu tiên và Area của bước là một Application rule chỉ kiểm tra
khi lưu, nên một giá trị ưu tiên đã lưu có thể trở nên lỗi thời và khi đó được hiển thị `(unavailable)`.

**`assigned_routes`**: PK `id`; `source_route_template_id` FK có thể null (mang tính thông tin; được index bởi
`ix_assigned_routes_source_route_template_id`, phục vụ danh sách sử dụng của Planned Routes, kiểm tra đã-từng-dùng và
kiểm tra foreign key khi xóa template); `created_at` (thời điểm snapshot). Snapshot **không** mang tham chiếu ngược
`quantity_flow_id`: flow sở hữu tham chiếu nó qua `quantity_flows.assigned_route_id` (PROJECT_PROFILE §8.7), foreign key
duy nhất giữa hai bảng.

**`assigned_route_steps`**: PK `id`; `assigned_route_id NOT NULL` FK; `sequence NOT NULL`, `UNIQUE (assigned_route_id,
sequence)`; `area_id NOT NULL` FK; `operation_id` FK có thể null; `expected_duration` có thể null; `instructions` có thể
null; `preferred_machine_id` có thể null. Đây là bản sao snapshot của các trường bước template (§10).

- `preferred_machine_id` là một số nguyên thường **không có foreign key**. Mọi nơi ghi đều kiểm tra nó: nó được sao từ
  một cột template đã được FK kiểm tra (hoặc từ một snapshot khác), hoặc được command `ROUTE_ADJUSTED` ghi sau khi
  `route_templates.lock_step_references` kiểm tra nó dưới `FOR KEY SHARE`; và Machine không bao giờ bị xóa. Một foreign
  key sẽ khiến release, receipt, split và merge lấy row lock của Machine sau một lock Area hay Machine, đi ngược thứ tự
  lock của sản xuất.
- Các bước đã qua là bất biến trong database (`0032_phase14_route_adjusted`). Trigger cấp câu lệnh
  `trg_assigned_route_steps_forbid_update` (function `partflow_assigned_route_steps_forbid_update`) từ chối mọi UPDATE,
  và một bước được Movement tham chiếu không bao giờ bị xóa (foreign key `part_movements.assigned_route_step_id` là `NO
  ACTION`). DELETE vẫn được phép với các bước tương lai chưa được tham chiếu mà một lần điều chỉnh thay thế.

### Bảng sản xuất

**`quantity_flows`**:

- PK `id`.
- `part_number text NOT NULL`: PN canonical chữ hoa do flow giữ, với cùng CHECK dạng canonical và **không có foreign key**
  tới master.
- `quantity int NOT NULL CHECK (quantity > 0)`.
- `status NOT NULL DEFAULT 'ACTIVE'` với `ck_quantity_flows_status` (`ACTIVE`, `SPLIT`, `MERGED`, `SCRAPPED`,
  `REVERSED`, `STOCKED`) và `ck_quantity_flows_status_closed_at` (`closed_at` được đặt đúng khi status không phải
  `ACTIVE`).
- `route_mode NOT NULL DEFAULT 'FLOATING' CHECK (route_mode IN ('FLOATING','PLANNED'))`.
- `assigned_route_id` FK có thể null tới `assigned_routes (id)`, với `UNIQUE (assigned_route_id)` (tối đa một flow cho
  mỗi snapshot) và `CHECK ((route_mode = 'PLANNED') = (assigned_route_id IS NOT NULL))` (một flow `PLANNED` luôn tham
  chiếu snapshot của nó và một flow `FLOATING` không bao giờ, PROJECT_PROFILE §8.7).
- `current_area_id NOT NULL` FK (đặt khi INSERT, §13); `current_machine_id` FK có thể null tới `machines.id`.
- `UNIQUE (id, part_number)`, đích của foreign key kết hợp.
- `created_at`, `updated_at`, `closed_at` có thể null.
- Index: `(part_number) WHERE status = 'ACTIVE'`, `(current_area_id)` và `(current_machine_id)`.
- Không có cột lineage nào trên bảng này; lineage nằm trong edge table `quantity_flow_lineage` (§9, §18).

**`part_movements`**:

- PK `id`, một identity kiểu `bigint` có thứ tự là thứ tự sự kiện.
- `quantity_flow_id NOT NULL` và `part_number text NOT NULL` (PN canonical chữ hoa do Movement giữ, nên lịch sử nhận diện
  PN của nó mà không cần join tới master), với FK kết hợp `(quantity_flow_id, part_number)` tới `quantity_flows (id,
  part_number)`.
- `movement_type NOT NULL` với `ck_part_movements_movement_type`, chấp nhận `RECEIVED`, `TRANSFERRED`,
  `ASSIGNED_TO_MACHINE`, `RELEASED_FROM_MACHINE`, `AREA_COMPLETED`, `SPLIT`, `MERGED`, `SCRAPPED`, `QUANTITY_ADJUSTED`,
  `REVERSED` và `STOCKED`. Danh sách mở rộng theo kiểu chỉ-thêm.
- `quantity int NOT NULL CHECK (quantity > 0)`.
- `from_area_id` FK có thể null; `to_area_id NOT NULL` FK.
- Check hình dạng theo loại `ck_part_movements_movement_shape`. Nhánh `RECEIVED` của nó yêu cầu `from_area_id IS NULL`
  và không có Machine nguồn hay đích; nó không ràng buộc `station_id`, nên một receipt tại Scan Station có thể ghi station
  của nó trong khi một release từ Management để NULL.
- `operation_id NOT NULL` FK.
- `assigned_route_step_id` FK có thể null tới `assigned_route_steps (id)` (đặt cho flow `PLANNED`, NULL cho flow
  `FLOATING`, §11).
- `station_id` FK có thể null tới `scan_stations.station_id`; `source_machine_id` và `destination_machine_id` FK có thể
  null tới `machines.id`; `worker_id` FK có thể null tới `workers.id`; `scan_session_id` có thể null (bên dưới).
- `occurred_at timestamptz NOT NULL`; `server_received_at timestamptz NOT NULL`.
- `device_event_id NOT NULL` và `command_sequence integer NOT NULL DEFAULT 1` (`command_sequence >= 1`), với unique
  constraint `uq_part_movements_device_event_id_command_sequence` trên `(device_event_id, command_sequence)`. Một
  `device_event_id` định danh một command ứng dụng, vốn có thể append nhiều Movement; một release append một.
- `movement_reason`, `reason` và `reverses_movement_id` (FK tới `part_movements`, `UNIQUE`), với các check của chúng
  `ck_part_movements_movement_reason`, `ck_part_movements_reason_required` và `ck_part_movements_reverses_shape`.
- `metadata jsonb`.
- Index `(quantity_flow_id, id)`.
- **Partial expression index** `ix_part_movements_received_demand_context` trên đường bằng chứng release:
  `((metadata['context'] ->> 'work_order_demand_id')::int) WHERE movement_type = 'RECEIVED'`. Nó tồn tại vì
  `released_quantity` được dẫn xuất từ ngữ cảnh metadata đó ở mỗi lần đọc Work Order (§8a), và lịch sử là chỉ-append và
  được giữ nhiều năm (PROJECT_PROFILE §28), nên thiếu nó thì đường đọc nóng sẽ quét toàn bảng. Biểu thức được lưu phải
  giống hệt biểu thức mà Application layer phát ra (dạng **subscript** JSONB; dạng toán tử `->` là một nút biểu thức khác
  và sẽ không khớp). Index **không** thêm cột, foreign key hay bộ đếm lưu trữ nào; việc dẫn xuất vẫn là nguồn sự thật
  duy nhất.
- `ix_part_movements_part_number_occurred_at_id` trên `(part_number, occurred_at, id)`, phục vụ lịch sử Movement theo PN
  của Tracking theo thứ tự thời gian với phân trang keyset (`0012_phase11_tracking_index`).
- `ix_part_movements_assigned_route_step_id` trên `(assigned_route_step_id)`, phục vụ kiểm tra của foreign key khi một
  lần điều chỉnh xóa một bước tương lai chưa được tham chiếu, và ranh giới "bước này có được tham chiếu không" của việc
  sửa.
- Cơ chế bảo vệ bất biến: thu hồi UPDATE và DELETE, cộng trigger raise `trg_part_movements_forbid_mutation`.

**`part_movements.scan_session_id`** (`0020_phase13_worker_sessions`): `bigint` có thể null (lịch sử giữ NULL và không
bao giờ được backfill); `ck_part_movements_session_requires_worker` (`scan_session_id IS NULL OR worker_id IS NOT
NULL`) và FK kết hợp `fk_part_movements_scan_session_worker_sessions` `(scan_session_id, worker_id, station_id)` tới
`worker_sessions (id, worker_id, station_id)`, nên một Movement không bao giờ có thể gọi tên session của Worker khác hay
station khác. Không có index, vì không bộ đọc nào lọc theo nó. Upgrade kiểm chứng CHECK và foreign key bằng một lần quét
`part_movements`; downgrade từ chối khi còn bất kỳ session, giá trị ghi đè theo Area hay timeout khác mặc định nào.

### Bảng audit

**`audit_events`**:

- PK `id`, một identity kiểu `bigint`.
- `event_type NOT NULL` với `ck_audit_events_event_type`: `CREATED`, `UPDATED`, `DELETED`, `ROUTE_ADJUSTED`.
- `entity_type NOT NULL` với `ck_audit_events_entity_type`: `WorkOrder`, `WorkOrderDemand`, `PartNumber`, `Worker`,
  `Department`, `Area`, `Operation`, `ScanStation`, `MachineAssetTagConfig`, `Machine`, `ApplicationPolicy`,
  `RouteTemplate`, `User`, `Role`, `ScanStationDevice`, `AssignedRoute`. Cả hai danh sách mở rộng theo kiểu chỉ-thêm.
- `entity_id NOT NULL` (không foreign key: đa hình, §16).
- `actor_reference` có thể null; `actor_user_id` FK có thể null `fk_audit_events_actor_user_id_users` tới `users (id)`.
- `occurred_at timestamptz NOT NULL`; `before_data jsonb` có thể null; `after_data jsonb` có thể null; `metadata jsonb`.
- Index `ix_audit_events_entity_type_entity_id_id` trên `(entity_type, entity_id, id)`.
- Partial expression index `ix_audit_events_hot_list_device_event_id` trên `(metadata['hot_list_change'] ->>
  'device_event_id') WHERE entity_type = 'WorkOrderDemand'`, cho việc tra cứu idempotency của command Hot list. Dạng lưu
  trữ là toán tử `->>` tường minh trên subscript JSONB, và việc tra cứu của Application phát ra cùng biểu thức.
- UNIQUE partial expression index `uq_audit_events_route_adjustment_device_event_id` trên
  `(metadata['route_adjustment'] ->> 'device_event_id') WHERE entity_type = 'AssignedRoute'` (dạng subscript JSONB, tạo
  bằng SQL thô để biểu thức được lưu là biểu thức mà Application phát ra). Nó làm idempotency của adjustment không có
  race.
- Cơ chế bảo vệ chỉ-append: thu hồi UPDATE và DELETE, cộng trigger raise `trg_audit_events_forbid_mutation` (§16).

### Bảng policy và Worker Session

**`application_policy`** (`0020_phase13_worker_sessions`) là singleton có kiểu duy nhất cho các policy toàn cục:

- PK `id` với `ck_application_policy_singleton` (`id = 1`), được migration seed nên dòng luôn tồn tại; `created_at`,
  `updated_at`.
- `worker_session_timeout_minutes integer NOT NULL DEFAULT 15` với
  `ck_application_policy_worker_session_timeout_range` (1–720).
- `badge_confirm_done`, `badge_confirm_queue` và `badge_confirm_undo` (`0021_phase13_badge_confirmation`):
  `boolean NOT NULL DEFAULT true`, mỗi cái cho một hành động nhạy cảm, quyết định hình thức cổng xác nhận cuối của nó
  trong các Area dùng Scanned session.
- `undo_reason_required` (`0022_phase13_undo_reason_policy`): `boolean NOT NULL DEFAULT false`. Khi nó là true, command
  Undo từ chối một reversal không có lý do.
- `due_soon_min_days` (mặc định 2, `ck_application_policy_due_soon_min_days_range`, 0–365),
  `due_soon_lead_time_percent` (mặc định 15, `ck_application_policy_due_soon_lead_time_percent_range`, 1–100) và
  `due_soon_max_days` (mặc định 7, `ck_application_policy_due_soon_max_days_range`, 0–365), với
  `ck_application_policy_due_soon_window_order` (`due_soon_min_days <= due_soon_max_days`)
  (`0025_phase13_display_settings`). Chúng là `integer NOT NULL` và giữ policy cảnh báo Due Soon toàn cục duy nhất.
- `retention_period_months integer NULL` với `ck_application_policy_retention_period_range` (NULL hoặc 12–1200)
  (`0027_phase13_retention_period`): khoảng thời gian retention của lịch sử Movement tính bằng tháng nguyên, NULL nghĩa là
  không có khoảng retention. Đây là cột policy duy nhất không có server default, vì PROJECT_PROFILE §28 cấm một con số
  retention cố định trong code. Nó được lưu cho việc bảo trì lưu trữ (archival) và không đường sản xuất nào đọc nó.
- Các cột đăng nhập và role của Scan Station được liệt kê ở "Đăng nhập" và "Thiết bị Scan Station" bên dưới.
- Các policy về sau được thêm dưới dạng cột có kiểu, có server default khi tồn tại giá trị mặc định canonical. Không có
  kho key/value.

**`worker_sessions`** (`0020_phase13_worker_sessions`) là `ScanSession` của PROJECT_PROFILE:

- PK `id bigint` identity (không bao giờ được API trả ra); `station_id` FK tới `scan_stations (station_id)`; `area_id` FK
  tới `areas (id)` (Area của station lúc đăng nhập); `worker_id` FK tới `workers (id)`; `started_at`, `expires_at
  timestamptz NOT NULL`; `ended_at timestamptz` và `end_reason text` có thể null.
- CHECK: `end_reason IN ('SWITCHED','EXPIRED','AREA_MODE_CHANGED','STATION_CHANGED','WORKER_DEACTIVATED')`;
  `(ended_at IS NULL) = (end_reason IS NULL)`; `expires_at > started_at`; `ended_at` NULL hoặc trong `[started_at,
  expires_at]`; `end_reason = 'EXPIRED'` chỉ khi `ended_at = expires_at`.
- `UNIQUE (id, worker_id, station_id)` là đích của foreign key kết hợp.
- Index: partial UNIQUE `uq_worker_sessions_open_station` trên `(station_id) WHERE ended_at IS NULL` (tối đa một session
  mở cho mỗi station) và `ix_worker_sessions_open_worker` trên `(worker_id) WHERE ended_at IS NULL`.
- Một trigger bảo vệ thay đổi cấm DELETE và TRUNCATE và chỉ cho UPDATE thay đổi `expires_at`, `ended_at` và `end_reason`
  của một dòng đang mở.
- Các foreign key giữ `NO ACTION`, vì station, Area và Worker không bao giờ bị xóa.

### User và role

**`roles`** (`0028_phase13_users_roles`): các role có tên sửa được. PK `id` (identity); `name text NOT NULL` với UNIQUE
thường `uq_roles_name` (phân biệt hoa thường, trên tên đã trim); `created_at`, `updated_at`. Không có cờ active và không
có xóa: một role được đổi tên, không bao giờ bị gỡ bỏ. Migration seed `Administrator`, `Manager` và `Operator` với đúng
các grant mà PROJECT_PROFILE §20 liệt kê (17, 10 và 10 permission key). Chúng là các dòng bình thường, và không gì được
gắn khóa theo tên của chúng.

**`role_permissions`** (`0028_phase13_users_roles`): một dòng cho mỗi grant. PK `pk_role_permissions` `(role_id,
permission)`; `role_id` FK `fk_role_permissions_role_id_roles` tới `roles (id)`; `permission text NOT NULL` với
`ck_role_permissions_permission_known`, chấp nhận đúng bộ từ vựng 35 key (mỗi key cho một khả năng của PROJECT_PROFILE
§20: `Permission` trong `app/domain/enums.py`, được lặp lại dạng literal trong migration và được một schema test ghim cho
bằng nhau). Một khả năng mới về sau mở rộng CHECK; một key không bao giờ bị đổi tên.

**`users`** (`0028_phase13_users_roles`): tài khoản ứng dụng, không bao giờ là Worker.

- PK `id` (identity).
- `login_name text NOT NULL` với UNIQUE `uq_users_login_name` (trên cả User active và inactive) và
  `ck_users_login_name_canonical` (`login_name COLLATE "C" ~ '^[a-z0-9._@+-]{1,128}$'`), nên giá trị lưu trữ là dạng ASCII
  chữ thường đã trim mà Application tạo ra.
- `display_name text NOT NULL`.
- `role_id integer NOT NULL` FK `fk_users_role_id_roles` tới `roles (id)` (đúng một role).
- Avatar tùy chọn `avatar_image`, `avatar_image_type`, `avatar_image_updated_at` dưới `ck_users_avatar_image_shape`,
  `ck_users_avatar_image_type` và `ck_users_avatar_image_size` (các rule avatar của `workers`: cả ba hoặc không có, PNG,
  JPEG hoặc WebP, 1 byte tới 2 MiB).
- `theme_preference text` có thể null với `ck_users_theme_preference` (`DARK` hoặc `LIGHT`; NULL nghĩa là không có ưa
  thích), chỉ được ghi bởi nút chuyển của chính User (`PUT /api/session/theme-preference`; không audit) và được đọc cùng
  principal của session.
- `is_active boolean NOT NULL DEFAULT true`; `created_at`, `updated_at`.
- Không có cột credential và không có foreign key giữa `users` và `workers` theo chiều nào.
- Downgrade của `0028_phase13_users_roles` từ chối khi còn bất kỳ dòng `users` nào hoặc bất kỳ dòng audit `User` hay
  `Role` nào.

### Đăng nhập

Migration đăng nhập `0029_phase14_sign_in` thêm những thứ sau.

- Các cột `application_policy`: `user_session_expires boolean NOT NULL DEFAULT true`, `user_session_days integer NOT NULL
  DEFAULT 30`, `sign_in_lockout_attempts integer NOT NULL DEFAULT 10`, `sign_in_lockout_minutes integer NOT NULL DEFAULT
  15` và `require_password_change boolean NOT NULL DEFAULT true`, với `ck_application_policy_user_session_days_range`
  (1–365), `ck_application_policy_sign_in_lockout_attempts_range` (3–100) và
  `ck_application_policy_sign_in_lockout_minutes_range` (1–1440).
- **`user_credentials`**: PK `pk_user_credentials` `user_id` (FK `fk_user_credentials_user_id_users` tới `users (id)`:
  một credential cho mỗi User); `password_hash text NOT NULL` (scrypt; `ck_user_credentials_password_hash_format`: bắt
  đầu bằng `scrypt$`); `password_is_temporary boolean NOT NULL`; `password_changed_at timestamptz NOT NULL`;
  `failed_attempts integer NOT NULL DEFAULT 0` (`ck_user_credentials_failed_attempts_non_negative`); `locked_until
  timestamptz` có thể null; `created_at`. Credential là một bảng riêng, không bao giờ là cột của `users`, nên một response
  serialize một user không bao giờ có thể mang nó.
- **`user_sessions`**: PK `pk_user_sessions` `id bigint` identity; `user_id` FK `fk_user_sessions_user_id_users`;
  `token_digest bytea NOT NULL` (digest SHA-256 của một token không diễn giải; bản thân token không bao giờ được lưu) với
  UNIQUE `uq_user_sessions_token_digest` và `ck_user_sessions_token_digest_length` (32 byte); `created_at`; `ended_at` và
  `end_reason` cùng có thể null (`ck_user_sessions_end_shape`), với `ck_user_sessions_end_reason` (`SIGNED_OUT`,
  `REPLACED`, `PASSWORD_CHANGED`, `PASSWORD_RESET`, `USER_DEACTIVATED`). Các dòng đã kết thúc được giữ lại.
  `ix_user_sessions_user_id_open` index các session đang mở của một User.
- `audit_events`, `machine_lifecycle_events` và `work_order_allocations` mỗi bảng có thêm `actor_user_id` có thể null với
  foreign key tên `fk_<table>_actor_user_id_users` (§16).
- Downgrade từ chối khi còn bất kỳ credential, session, giá trị `actor_user_id` hay dòng audit policy `sign-in` nào, hoặc
  khi bất kỳ cột policy đăng nhập nào khác giá trị mặc định.

### Thiết bị Scan Station

Migration `0030_phase14_station_devices` thêm những thứ sau.

- **`scan_station_devices`**: PK `pk_scan_station_devices` `id` (identity); `station_id text NOT NULL` FK
  `fk_scan_station_devices_station_id_scan_stations` tới `scan_stations (station_id)`; `label text NOT NULL` (đã trim,
  1–80 ký tự, một Application rule); `enrollment_code_digest bytea` có thể null với UNIQUE
  `uq_scan_station_devices_enrollment_code_digest` và `ck_scan_station_devices_enrollment_code_digest_length`
  (`octet_length = 32`); `enrollment_expires_at timestamptz NOT NULL`; `token_digest bytea` có thể null với UNIQUE
  `uq_scan_station_devices_token_digest` và `ck_scan_station_devices_token_digest_length` (`octet_length = 32`);
  `replaces_device_id integer` FK có thể null `fk_scan_station_devices_replaces_device_id_scan_station_devices` tới
  `scan_station_devices (id)`; `issued_at timestamptz NOT NULL DEFAULT now()`; `activated_at`, `last_seen_at`,
  `revoked_at` timestamptz có thể null; `revoked_reason text` có thể null.
- Chỉ lưu digest SHA-256. Một dòng đang chờ giữ digest của mã, và việc kích hoạt xóa nó đồng thời đặt digest của token,
  nên hai giá trị không bao giờ cùng tồn tại (`ck_scan_station_devices_code_or_token`) và một mã đã dùng không thể khớp
  lại.
- Các check khác: `ck_scan_station_devices_activation_shape` (có digest token đúng khi `activated_at` được đặt),
  `ck_scan_station_devices_revocation_shape` (`revoked_at` đúng khi `revoked_reason` được đặt),
  `ck_scan_station_devices_revoked_reason` (`REVOKED`, `REPLACED`), `ck_scan_station_devices_replaced_shape` (`REPLACED`
  chỉ cho thiết bị đã kích hoạt), `ck_scan_station_devices_last_seen_shape` và `ck_scan_station_devices_no_self_replace`.
- Ứng dụng không bao giờ xóa dòng, và không có index phụ.
- `application_policy` có thêm `scan_station_role_id integer NOT NULL` với
  `fk_application_policy_scan_station_role_id_roles` tới `roles (id)`: role áp dụng tại Scan Station. Migration đặt nó
  một lần thành role Operator được seed (nơi duy nhất một role được phân giải theo tên; upgrade bị từ chối khi không có
  role nào tên Operator), và không API nào ghi nó.
- `ck_audit_events_entity_type` được mở rộng với `ScanStationDevice`.
- Downgrade từ chối khi còn bất kỳ dòng `scan_station_devices` hay dòng audit `ScanStationDevice` nào; nếu không, nó khôi
  phục CHECK entity-type trước đó và bỏ con trỏ cùng bảng.

### Beyond-demand correction

Migration `0031_phase14_beyond_demand` thêm `work_order_allocations.exceeds_demand boolean NOT NULL DEFAULT false` (mọi
dòng lịch sử giữ `false`) và `ck_work_order_allocations_exceeds_demand_shape` (`NOT exceeds_demand OR (source =
'MANAGEMENT' AND allocation_reason IS NOT NULL AND reverses_allocation_id IS NULL AND station_id IS NULL AND
allocated_by_worker_id IS NULL AND actor_user_id IS NOT NULL)`). Vì vậy một correction là một dòng Management có lý do,
không bao giờ là một reversal, không bao giờ được quy cho một Scan Station hay Worker, và luôn được ghi kèm một User đã
đăng nhập. Downgrade từ chối khi còn bất kỳ dòng correction nào; nếu không, nó bỏ CHECK và cột.

### Ranh giới migration của slice

Các migration đã tạo schema của slice này (`0002_phase3_domain`, `0004_phase4_audit` và `0005_phase4_release_index`)
**không** chứa foreign key nào tới một bảng mà chúng không tạo. `scan_stations` và `machines` là cấu hình thiết lập môi
trường do `0003_phase35_environment` tạo (§2, §18); các cột station, Machine, Worker và correction của `part_movements`
và `quantity_flows` được các migration về sau thêm vào (§18).

### Invariant liên dòng

PostgreSQL không thể biểu diễn khai báo các invariant sau:

- projection khớp với Movement có hiệu lực mới nhất (§15);
- Movement đầu tiên của một flow được tạo bởi release là `RECEIVED` của nó;
- `assigned_route_step_id` của một Movement khớp với route mode của flow và thuộc AssignedRoute của chính flow đó (§11);
- mọi thay đổi được audit commit cùng dòng audit của nó (§16);
- một PN canonical xuất hiện tối đa một lần trong các dòng demand hiện tại của một Work Order (§5).

Giao thức transaction ép buộc chúng (§13, §16, gồm cả row lock WorkOrder tuần tự hóa việc thêm dòng demand), và các kiểm
tra replay và đối soát (§15) cùng test đồng thời kiểm chứng chúng.

### Database role và grant

- Đặc quyền không thuộc migration nào. Chúng được dẫn xuất từ một bảng phân loại trong code
  (`backend/app/infrastructure/database_privileges.py`) và được áp dụng bởi `python -m app.cli apply-grants`, mà mọi lần
  `migrate` cũng chạy trong transaction riêng.
- Production stack có ba database role:
  - owner (`POSTGRES_USER`, một superuser), sở hữu mọi đối tượng và chỉ được `db`, `migrate` và `db-roles` dùng;
  - `partflow_app`, được backend dùng;
  - `partflow_maintenance`, đã được tạo nhưng không service nào dùng cho tới khi có bảo trì lưu trữ (archival).
- Cả hai role ứng dụng là `LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS`, không là thành viên
  role nào và không sở hữu gì. Đặc quyền là tập chính xác; không bao giờ cấp TRUNCATE, REFERENCES, TRIGGER hay grant
  option.

| Class | `partflow_app` privileges | Tables (29 at head `0032_phase14_route_adjusted`) |
|---|---|---|
| `APPEND_ONLY` | SELECT, INSERT | `part_movements`, `audit_events`, `machine_lifecycle_events`, `quantity_flow_lineage`, `work_order_allocations` |
| `GUARDED_UPDATE` | SELECT, INSERT, UPDATE | `worker_sessions` (trigger dòng vẫn từ chối mọi thay đổi trừ hết hạn và kết thúc session đang mở) |
| `NO_UPDATE` | SELECT, INSERT, DELETE | `assigned_route_steps` (bước đã qua bất biến nhờ trigger và đặc quyền; một lần điều chỉnh xóa các bước tương lai chưa được tham chiếu) |
| `READ_ONLY` | SELECT | `alembic_version` |
| `ORDINARY` | SELECT, INSERT, UPDATE, DELETE | `application_policy`, `areas`, `assigned_routes`, `departments`, `machine_asset_tag_config`, `machines`, `operations`, `part_numbers`, `quantity_flows`, `role_permissions`, `roles`, `route_steps`, `route_templates`, `scan_station_devices`, `scan_stations`, `user_credentials`, `user_sessions`, `users`, `work_order_demands`, `work_orders`, `workers` |

- `partflow_maintenance` chỉ có SELECT, trên `alembic_version`, `application_policy`, `areas`, `machines`, `operations`,
  `part_movements`, `quantity_flow_lineage`, `quantity_flows`, `role_permissions`, `roles`, `scan_stations`, `users`,
  `worker_sessions` và `workers`.
- Không role nào có đặc quyền sequence (cột identity), hay `CREATE` trên schema `public` hoặc trên database. PUBLIC
  không có gì trên bất kỳ bảng hay sequence nào của PartFlow, và default privilege cho PUBLIC và cả hai role đều bị gỡ.
- Cơ chế bảo vệ cột trên `areas` và `machines` vẫn chỉ dùng trigger.
- Grant không bao giờ được khôi phục từ bản dump: mỗi lần `migrate` dẫn xuất lại chúng từ code head, và `apply-grants` làm
  vậy sau một lần restore.
- Một bảng mới phải được phân loại trong cùng commit; nếu không thì một test thất bại, và `apply-grants` cùng `migrate` từ
  chối bảng chưa phân loại.
- Check (h) của lệnh đối soát kiểm chứng kết quả (trigger, hash mã nguồn của các function bảo vệ, grant, thuộc tính role)
  và báo `not_applicable` trên database không có các role này.
- Một superuser vô hiệu hóa trigger nằm ngoài khả năng phát hiện; đây là giới hạn được chấp nhận (IMPLEMENTATION_ROADMAP
  Phase 16).

---

## 18. Explicitly Deferred Capabilities

Các khả năng mà slice này để ngoài chia thành hai nhóm: những khả năng đã được công việc về sau triển khai, và những khả
năng vẫn còn hoãn.

### Đã triển khai sau slice này

Mỗi khả năng sau đã được triển khai. Bảng nêu schema mà nó thêm và nơi nó được đặc tả hiện nay; các cột và ràng buộc
hiện tại được liệt kê ở §17, và phase của roadmap chỉ được nêu để tìm mục tương ứng trong `IMPLEMENTATION_ROADMAP.md`.

| Khả năng | Schema đã thêm | Nơi đặc tả hiện nay |
|---|---|---|
| Thiết lập môi trường | các bảng `scan_stations` và `machines`, `areas.is_terminal` và các trường cấu hình Area và Operation (`0003_phase35_environment`) | PROJECT_PROFILE §8.4 tới §8.6 và §15; IMPLEMENTATION_ROADMAP Phase 3.5 |
| Transfer tại Scan Station, `TRANSFERRED` | check movement-type mở rộng; `part_movements.station_id` (FK có thể null tới `scan_stations.station_id`, NULL trên `RECEIVED` khởi động từ Management); check hình dạng theo loại `ck_part_movements_movement_shape` thay cho check `RECEIVED` một loại. Metadata của `TRANSFERRED` mang cùng cơ chế idempotency request-fingerprint như §14, cộng một khối `route_deviation` cho một deviation Planned Route đã xác nhận (`0006_phase5_transfer`) | PROJECT_PROFILE §15 và §17; IMPLEMENTATION_ROADMAP Phase 5 |
| Gán Machine (một lần, không session), `ASSIGNED_TO_MACHINE` và `RELEASED_FROM_MACHINE` | `quantity_flows.current_machine_id` (FK có thể null tới `machines.id`, có index); các cột Machine của Movement `source_machine_id` và `destination_machine_id`; check movement-type mở rộng; check hình dạng ghim các tham chiếu Area, Station và Machine của từng loại. `part_movements.command_sequence` (NOT NULL, mặc định 1) đánh số các Movement của một command ứng dụng, và `UNIQUE (device_event_id, command_sequence)` thay cho `UNIQUE (device_event_id)`; release vẫn là command một Movement và idempotency §14 của nó không đổi (`0007_phase6_machine_assignment`) | PROJECT_PROFILE §12; IMPLEMENTATION_ROADMAP Phase 6 |
| Hoàn tất Area, `AREA_COMPLETED`, và trạng thái chờ dẫn xuất `READY_TO_TRANSFER` | `AREA_COMPLETED` xóa `quantity_flows.current_machine_id` trong khi `current_area_id` giữ nguyên. Một transfer từ số lượng `ON_MACHINE` append `AREA_COMPLETED` (sequence 1) và `TRANSFERRED` (sequence 2) dưới MỘT `device_event_id` trong MỘT transaction. Replay projection dẫn xuất `QUEUED`, `ON_MACHINE` và `READY_TO_TRANSFER` từ Movement mới nhất. `0008_phase7_direct_processing` mở rộng nhánh hình dạng `AREA_COMPLETED` để cho phép Machine nguồn NULL (một lần hoàn tất không bao giờ mang Machine đích) | PROJECT_PROFILE §7 Area Completion và §12; IMPLEMENTATION_ROADMAP Phase 6 và 7 |
| Xử lý trực tiếp tại Area (Area không có Machine) | chỉ phần mở rộng hình dạng `AREA_COMPLETED` ở trên, không có cột. Trạng thái chờ dẫn xuất `PROCESSING` (một Movement đến trong Area không có Machine active) không bao giờ được lưu, và không tồn tại `machine_assignment_mode`: hành vi được suy ra từ các Machine của Area (`0008_phase7_direct_processing`) | PROJECT_PROFILE §12; IMPLEMENTATION_ROADMAP Phase 7 |
| SPLIT và MERGED, di chuyển một phần | các loại `SPLIT` và `MERGED` với một nhánh hình dạng dùng chung; các check vòng đời `quantity_flows.status` và `closed_at`; edge table chỉ-append `quantity_flow_lineage` (cha tới con, quan hệ, `device_event_id`) thay cho cột `parent_flow_id`, với một cạnh cho mỗi con của SPLIT (1 tới N) và một cho mỗi nguồn của MERGED (N tới 1) (`0009_phase8_split_merge`) | PROJECT_PROFILE §11; IMPLEMENTATION_ROADMAP Phase 8 |
| Undo và correction, `REVERSED`, `reverses_movement_id`; Repair (`movement_reason = REPAIR`), `SCRAPPED`, cộng thêm `QUANTITY_ADJUSTED` | `movement_reason` (chỉ `REPAIR`, chỉ trên `TRANSFERRED`); `reason` dạng văn bản tự do (bắt buộc với Scrap, điều chỉnh và Repair, ép buộc bằng CHECK); `reverses_movement_id` (FK tới `part_movements`, đặt đúng trên `REVERSED`, UNIQUE, nên tối đa một reversal cho mỗi bản gốc); các check movement-type và hình dạng mở rộng với `SCRAPPED`, `QUANTITY_ADJUSTED` và `REVERSED`; `quantity_flows.status` mở rộng với `SCRAPPED` và `REVERSED` (`0010_phase9_undo_corrections`). Undo append các dòng `REVERSED` bù trừ cho TOÀN BỘ command, và mọi phép dẫn xuất loại trừ cặp đã đảo ngược, nên trạng thái được khôi phục là Movement trước command bị undo. `QUANTITY_ADJUSTED` đưa vào một flow MỚI (giống `RECEIVED`, ghi tại station) và không bao giờ sửa số lượng hiện có. Policy lý do Undo (`application_policy.undo_reason_required`, `0022_phase13_undo_reason_policy`) yêu cầu `reason` trên các dòng `REVERSED` khi đang bật; command Undo ép buộc điều này, không bao giờ là CHECK, vì nó phụ thuộc cấu hình | PROJECT_PROFILE §14 và §16; IMPLEMENTATION_ROADMAP Phase 9 |
| Stockroom, `STOCKED`, WorkOrderAllocation | các check movement-type và hình dạng mở rộng với `STOCKED` (cùng hình dạng như `TRANSFERRED`: hai Area khác nhau tại một Station, không Machine; đích terminal là một Application rule được đánh giá dưới lock Area); `quantity_flows.status` mở rộng với `STOCKED` (flow đóng ở trạng thái hoàn tất sản xuất); `work_orders.completed_at` là projection của các dòng allocation; bảng chỉ-append `work_order_allocations` gồm các dòng allocation và reversal (PN canonical theo giá trị, FK tới demand, `reverses_allocation_id` UNIQUE, `device_event_id` và `command_sequence`), không bao giờ tham chiếu Movement hay QuantityFlow, vì allocation tách biệt khỏi Movement theo thiết kế; `work_order_demands.allocated_quantity` là projection được duy trì của các dòng allocation active của demand (`0011_phase10_stock_allocation`). `exceeds_demand` được thêm bởi `0031_phase14_beyond_demand` | PROJECT_PROFILE §8.12 và §18; IMPLEMENTATION_ROADMAP Phase 10 |
| Read model giám sát | các truy vấn dẫn xuất từ Movement; index Tracking `ix_part_movements_part_number_occurred_at_id` (`0012_phase11_tracking_index`) | PROJECT_PROFILE §21; IMPLEMENTATION_ROADMAP Phase 11 |
| Quản lý priority và Hot list | không có cột mới: `priority_rank` hiện có nhận CHECK dương và UNIQUE (liên tục `1..N`, §5 và §17) sau một kiểm tra trước có từ chối, cộng expression index của audit (§17). Các nơi ghi nó là command Hot list và việc gỡ tự động hoặc gỡ khi xóa dòng (`hot_ranks`), và cả hai đều audit qua `audit_events` (§16) (`0013_phase12_priority`) | PROJECT_PROFILE §7 Priority và §21 Priority Management; IMPLEMENTATION_ROADMAP Phase 12 |
| Administration | các bảng master data đã có sẵn; thiết lập hiển thị của Department và policy Due Soon (`0025_phase13_display_settings`); thiết lập khoảng retention lịch sử Movement (`0027_phase13_retention_period`). Việc thực thi retention vẫn còn hoãn (bên dưới) | PROJECT_PROFILE §21 Administration; IMPLEMENTATION_ROADMAP Phase 3.5 và 13 |
| Quản lý metadata PartNumber, ảnh và xóa cứng | các cột có thể null trên `part_numbers`; không đụng bảng sản xuất nào (`0023_phase13_part_number_master`) | PROJECT_PROFILE §8.1; §6 và §16 ở đây |
| Theme ưa thích của Scan Station (tầng station) | một cột có thể null `scan_stations.theme_preference` với một CHECK (`DARK` hoặc `LIGHT`); không audit (`0026_phase13_station_theme`) | GUI_DESIGN; §16 ở đây |
| Theme ưa thích của User (tầng User) | một cột có thể null `users.theme_preference` với một CHECK (`DARK` hoặc `LIGHT`); không audit (không có migration riêng; cột đến từ `0028_phase13_users_roles`) | GUI_DESIGN; §16 và §17 ở đây |
| Quản lý Planned Routes | hai cột có thể null `preferred_machine_id` (FK trên `route_steps`, không có trên `assigned_route_steps`), một index và từ vựng audit `RouteTemplate`; không backfill (`0024_phase13_planned_routes`) | PROJECT_PROFILE §8.8, §8.9 và §17; §10, §16 và §17 ở đây |
| Xác thực và role | user, role và permission dưới dạng cấu hình (`0028_phase13_users_roles`); đăng nhập, session và cột tác nhân (`0029_phase14_sign_in`); ép buộc quyền trên các lần đọc và ghi của Administration và Management (không migration); enroll thiết bị Scan Station và quyền tại station (`0030_phase14_station_devices`). Không có ràng buộc schema nào với Movement; `actor_user_id` nằm cạnh cột text cũ `actor_reference`, vốn không bao giờ được backfill (§16) | PROJECT_PROFILE §20; IMPLEMENTATION_ROADMAP Phase 14 |
| AssignedRoute adjustment (`ROUTE_ADJUSTED`) | mở rộng từ vựng audit, UNIQUE partial index cho idempotency, index `part_movements.assigned_route_step_id` và trigger cấm UPDATE trên `assigned_route_steps`; không có loại Movement nào (`0032_phase14_route_adjusted`) | PROJECT_PROFILE §8.10; §14, §16 và §17 ở đây |
| Bộ đọc audit trail theo PN | không có: nó chỉ đọc `audit_events` và `work_order_allocations` | §14 ở đây; IMPLEMENTATION_ROADMAP Phase 14 |
| Import Work Order từ file | không có: Work Order mới, và thay đổi Work Order Open và Released qua việc sửa demand thông thường, dùng lại validation của §5 một cách idempotent | PROJECT_PROFILE §13; §5, §14 và §16 ở đây; IMPLEMENTATION_ROADMAP Phase 15 |
| Nhận diện Worker và lưu trữ ScanSession | bảng `workers` (badge UNIQUE trên dạng canonical, avatar trên dòng) và phần mở rộng từ vựng audit (`0014_phase13_workers`); `part_movements.worker_id`, `work_order_allocations.allocated_by_worker_id`, `areas.worker_identification_mode` và `areas.fixed_worker_id` (`0019_phase13_worker_identity`); `part_movements.scan_session_id`, `worker_sessions`, `application_policy` và `areas.worker_session_timeout_minutes` (`0020_phase13_worker_sessions`); ba cột xác nhận badge của `application_policy` (`0021_phase13_badge_confirmation`) | PROJECT_PROFILE §8.13 và §19; §11, §16 và §17 ở đây |

### Vẫn còn hoãn

| Khả năng | Trạng thái | Hướng đi |
|---|---|---|
| Trường và đồng bộ ERP | hoãn và chưa được duyệt (IMPLEMENTATION_ROADMAP Deferred) | một ranh giới tích hợp tách biệt; `device_event_id` vẫn tương thích |
| Đồng bộ quét offline | một quyết định còn mở (PROJECT_PROFILE §32 mục 2); các lần ghi sản xuất vẫn bị chặn khi mất kết nối | `device_event_id` được chọn để một thiết kế offline được duyệt trong tương lai không cần đổi tên (§20) |
| Đưa số lượng đã nhập kho trở lại sản xuất active | một quyết định còn mở (PROJECT_PROFILE §32 mục 1) | về sau có thể mở rộng check movement-type theo kiểu chỉ-thêm (§20) |
| Lưu trữ và xóa (archival và purge) lịch sử Movement bởi quản trị | được lên kế hoạch trong IMPLEMENTATION_ROADMAP Phase 16; khoảng retention đã được lưu (`application_policy.retention_period_months`) nhưng không đường sản xuất nào đọc | một đường bảo trì riêng có đặc quyền dưới role `partflow_maintenance` (PROJECT_PROFILE §28; §11 và §17 ở đây) |
| Các khả năng import file bổ sung | hoãn cho tới khi một quyết định của owner yêu cầu (IMPLEMENTATION_ROADMAP Deferred) | không có thay đổi schema nào được lên kế hoạch |

---

## 19. Acceptance Criteria

1. Lưu một Work Order cùng WorkOrderDemand không tạo QuantityFlow, không tạo PartMovement và không thay đổi projection.
2. Tạo một PN mới (ở lần dùng hợp lệ đầu tiên) chuẩn hóa giá trị nhập thành PN canonical: `abc-123`, `AbC-123`,
   `ABC-123` và `" ABC-123 "` đều quy về một PN canonical duy nhất `ABC-123` (khoảng trắng bao quanh được trim; được
   kiểm chứng bằng ràng buộc, nên không thể có dòng master thứ hai cho cùng PN canonical), và mọi giá trị có khoảng trắng
   bên trong sau khi trim (`"ABC 123"`, `"ABC\t123"`, `"ABC\n123"`) bị từ chối và không ghi gì. Barcode được dẫn xuất
   thành `PF:PN:<part-number>` từ PN canonical. Việc tạo master append một audit event `CREATED`.
3. Một release tạo đúng một QuantityFlow (với route mode của nó), một Movement `RECEIVED`, và, chỉ với release `PLANNED`,
   một snapshot AssignedRoute, một cách nguyên tử và không có audit event tổng quát. Một release `FLOATING` (mặc định)
   không tạo AssignedRoute. Nếu bất kỳ phần nào thất bại, không gì được commit: không trạng thái đã commit nào có thể chứa
   một QuantityFlow thiếu Movement `RECEIVED`, một flow `PLANNED` thiếu snapshot, hay một flow `FLOATING` có snapshot
   (được kiểm chứng bằng ràng buộc qua CHECK `assigned_route_id` của flow, §17), và không bao giờ quan sát được trạng thái
   release dở dang.
4. `current_area_id` được chính câu INSERT QuantityFlow đặt thành starting Area đã xác nhận; cột là NOT NULL, và release
   không cần cập nhật projection nào sau khi insert.
5. Mọi Movement `RECEIVED` ghi một Operation đã xác định (`operation_id NOT NULL`, được kiểm chứng bằng ràng buộc). Với
   flow `PLANNED`, nó tham chiếu bước đầu tiên của snapshot AssignedRoute (`assigned_route_step_id` đặt thành một dòng
   `assigned_route_steps`, không bao giờ là dòng `route_steps`); với flow `FLOATING`, `assigned_route_step_id` là NULL.
6. Release với PN không hợp lệ, Area không active, Operation không active, RouteTemplate không active (khi `PLANNED`),
   hoặc số lượng ≤ 0 bị từ chối và không ghi gì.
7. Release một PN đã có số lượng active mà không có cờ xác nhận tường minh bị từ chối và không ghi gì; có xác nhận thì nó
   tạo một flow riêng và không bao giờ gộp.
8. Thử lại một release với cùng `device_event_id` và cùng request đã chuẩn hóa (fingerprint khớp) trả về kết quả gốc và
   không tạo gì.
9. Dùng lại một `device_event_id` với request đã chuẩn hóa khác (fingerprint không khớp) trả về lỗi xung đột idempotency
   tường minh và không tạo gì.
10. Sửa một RouteTemplate sau release không thay đổi snapshot AssignedRoute nào, và các lần sửa template không bao giờ
    làm thay đổi ngữ cảnh route mà bất kỳ Movement hiện có nào đã ghi.
11. Thủ tục dựng lại projection tái tạo `current_area_id` cho mọi flow chỉ từ lịch sử Movement.
12. Application role không thể cập nhật hay xóa dòng `part_movements` và `audit_events`. Trigger được kiểm chứng bởi các
    schema test; quyền được kiểm chứng bởi các test database role (`tests/test_database_roles.py`) và bằng việc chạy bộ
    test dưới application role.
13. Mọi lần tạo hoặc sửa WorkOrder và WorkOrderDemand append một dòng `audit_events` (`CREATED` hoặc `UPDATED`, với
    `before_data` và `after_data`) trong cùng transaction với thay đổi; các lần sửa giữ nguyên mọi dòng audit trước đó
    (lịch sử chỉ-append, được kiểm chứng bằng test).
14. Các lần ghi của slice này chỉ append dòng `audit_events` cho `WorkOrder`, `WorkOrderDemand` và `PartNumber`, và
    `audit_events` không bao giờ chứa dòng cho production release hay bất kỳ hoạt động sản xuất nào khác (được kiểm chứng
    bằng ràng buộc và test; CHECK entity-type của §17 không chấp nhận entity sản xuất nào).
15. Bảo toàn được giữ: Σ(số lượng flow active theo PN) = Σ(số lượng `RECEIVED` theo PN).
16. Release vào một Area **terminal** bị từ chối và không ghi gì, bất kể UI đã đề xuất gì.
17. Một demand được release theo từng phần cho tới khi remaining quantity hết: mỗi phần tạo QuantityFlow và `RECEIVED`
    riêng (không bao giờ gộp), released và remaining quantity được dẫn xuất chỉ từ lịch sử Movement, một release vượt
    phần còn lại, hoặc mọi release khi nó đã là 0, bị từ chối và không ghi gì, và Work Order hiển thị `RELEASED` chỉ khi
    phần còn lại của mọi dòng là 0.
18. Một dòng demand đã có số lượng release nhận quyền sửa hạn chế: `requested_quantity` (không bao giờ thấp hơn số lượng
    đã release hoặc đã allocate), `due_date` và `job_numbers` lưu được với một dòng audit `UPDATED` bình thường; tăng số
    lượng khôi phục remaining quantity và trạng thái dẫn xuất `OPEN` của Work Order; và việc sửa `request_type`,
    `requester`, `reason` hay `notes`, hoặc một số lượng thấp hơn mức đã cam kết, bị từ chối và hoàn toàn không ghi gì.
    Lịch sử audit trước đó, số lượng đã release và việc sửa header Work Order không bị ảnh hưởng.
19. Các migration đã tạo schema của slice này (`0002_phase3_domain`, `0004_phase4_audit`, `0005_phase4_release_index`)
    không chứa foreign key nào tới một bảng mà chúng không tạo, và không chứa cột hoãn nào: không có tham chiếu tới
    `machines`, `workers`, `scan_sessions`, `scan_stations` hay bảng user, và không có `current_machine_id`,
    `parent_flow_id`, `station_id`, `worker_id`, `scan_session_id`, `reverses_movement_id`, `movement_reason`,
    `is_terminal`, `worker_identification_mode`, `preferred_machine_id` hay `completed_at`. Hoàn toàn không có
    `machine_assignment_mode`: hành vi của Area được suy ra từ các Machine của nó (PROJECT_PROFILE §12).

---

## 20. Remaining Uncertainty

Chỉ các quyết định còn mở của PROJECT_PROFILE §32 liên quan tới slice này, và không cái nào chặn nó:

- **Đưa số lượng từ kho trở lại sản xuất active** (PROJECT_PROFILE §32 mục 1) về sau có thể mở rộng check movement-type;
  thay đổi sẽ theo kiểu chỉ-thêm. Cho tới khi được quyết định, một command `STOCKED` không bao giờ bị undo; điều chỉnh
  allocation là đường correction.
- **Đồng bộ quét offline** (PROJECT_PROFILE §32 mục 2): slice này giả định ngữ nghĩa online đồng bộ (§14).
  `device_event_id` được chọn để một thiết kế offline được duyệt trong tương lai không cần đổi tên.

---

## Lịch sử thay đổi

| Ngày | Commit | Thay đổi |
|---|---|---|
| 2026-11-09 | uncommitted | Viết lại thành nội dung hiện tại cho người đọc mới; phần mô tả schema nay khớp với migration head `0032_phase14_route_adjusted` và code release; không thay đổi rule nào. |
| 2026-10-08 | `1116b07` | Database role và grant cho production, với việc thu hồi chỉ-append do `apply-grants` áp dụng. |
| 2026-10-07 tới 2026-10-08 | `e1dee40`, `918f025`, `e13574f` | Import Work Order từ file: dấu hiệu intake, idempotency theo Work Order Number, ngày nhận mặc định theo lịch nhà máy. |
| 2026-10-07 | `961c0a1`, `1c9d755`, `582baf0` | Bộ đọc audit trail theo PN chỉ-đọc; tầng theme của User. |
| 2026-10-07 | `4a77dc4`, `c939141` | Assigned Route adjustment (`ROUTE_ADJUSTED`), các bước snapshot đã qua bất biến và index idempotency của adjustment. |
| 2026-10-07 | `98ba54c` | Management allocation và beyond-demand correction (`exceeds_demand`). |
| 2026-10-06 tới 2026-10-07 | `702ab1c`, `f0ae395`, `77fd32c`, `1c5b3ef` | Đăng nhập, ép buộc quyền, các cột `actor_user_id` và thiết bị Scan Station. |
| 2026-10-04 tới 2026-10-06 | `c12728d` tới `69b8ec1` | Administration đầy đủ: Workers, audit cấu hình, nhận diện và session Worker, policy, quản lý Part Numbers, Planned Routes, thiết lập hiển thị, khoảng retention, User và Role. |
| 2026-10-04 | `80f7925`, `9f466fa` | Priority Hot list: rank duy nhất và liên tục, tự gỡ mục inactive và xóa có xác nhận một dòng Hot. |
| 2026-09-02 tới 2026-09-04 | `d2f68de`, `8e5ab18`, `f10d8bd` | Thêm bản dịch tiếng Việt; một receipt tại Scan Station không bao giờ gộp vào số lượng hiện có. |
| 2026-08-26 tới 2026-09-01 | `b031ad6` tới `100d59d` | Ghi nhận đã triển khai xử lý trực tiếp tại Area, SPLIT và MERGED, Undo và correction, Stockroom và allocation. |
| 2026-08-20 tới 2026-08-25 | `fc83be4` tới `a567b02` | Release từng phần của một demand, sửa hạn chế sau release, index bằng chứng release và transfer tại Scan Station. |
| 2026-08-17 | `2a57c50`, `6a16fe1` | Đồng bộ với PROJECT_PROFILE v19; sự khớp route-step được nêu là invariant liên bảng. |
| 2026-08-12 | `fa4b51d`, `c4f18a6` | Chuỗi PN canonical làm định danh, PN master tùy chọn xóa cứng được, chuẩn hóa trim-rồi-từ-chối. |
| 2026-07-28 tới 2026-08-04 | `fbb38cb` tới `74e0386` | Route floating, Work Order Number có thể null, trạng thái hoàn tất Area và quản lý Planned Routes. |
| 2026-07-22 tới 2026-07-26 | `0e659ac` tới `74edbdd` | Data model đầu tiên của slice: audit một bản ghi, `RECEIVED` chặt chẽ, idempotency fingerprint, due date có thể null. |
