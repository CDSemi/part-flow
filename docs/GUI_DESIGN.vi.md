# Thiết kế GUI PartFlow v18

> **Bản gốc chuẩn:** [`GUI_DESIGN.md`](GUI_DESIGN.md).
> Baseline upstream: commit `f96bf09` (Production Board — merged quantity theo mọi nhánh lineage).
> **Trạng thái đồng bộ:** các thay đổi Phase 13 của bản EN đã được dịch theo từng slice đến bản
> đóng Phase 13 (sau commit `dbd42ee`) và các thay đổi Phase 14 slice 1 (sign-in), slice 2 (Administration enforcement) và slice 3 (Management enforcement) và slice 4 (thiết bị Scan Station), slice 5 (Management allocation và correction beyond-demand) và slice 6 (AssignedRoute adjustment) và slice 7 (audit trail theo PN, `Change priority`) và slice 8 (tier theme của User) và Phase 15 slice 1 và 2 (file import; thay đổi Work Order hiện có, §11.7) cùng các chỉnh sửa khi đóng Phase 15, và Phase 16 slice 3 (update notice khi release không khớp, §3 rule 13) đã được dịch theo đúng các đoạn thay đổi, nhưng chưa review diff đầy đủ so với baseline `f96bf09`
> theo TRANSLATION_POLICY §4, nên baseline chưa được nâng; nếu hai bản khác nhau, bản EN đúng.
> File EN là source of truth cho UI; business rule, thuật ngữ và workflow chuẩn
> do [`PROJECT_PROFILE.md`](PROJECT_PROFILE.md) định nghĩa.
>
> **Trạng thái:** Hiện hành, companion của Project Profile v22. Interactive visual
> reference: `mockups/partflow-gui-mockup-v18.html`; version cũ nằm trong
> `docs/archive/`. Mockup là reference, không thay contract bằng văn bản.

---

# 1. Phạm vi

Mười GUI view được duyệt:

1. Scan Station.
2. Production Board.
3. Area Board — All Areas + per-Area detail.
4. Machines.
5. PN Tracking.
6. Work Orders (nhập Work Order thủ công, file import và production release).
7. Planned Routes.
8. Part Numbers.
9. Priority Management.
10. Administration.

## 1.1 Cấu trúc navigation

| Top level | Nội dung |
|---|---|
| Scan Station | Một view |
| Production Board | Một view |
| Management | Area Board · Work Orders · PN Tracking · Priority · Planned Routes · Part Numbers · Machines |
| Administration | Một view có sidebar riêng |

Management mở subview dùng gần nhất trong session khi user được mở nó, nếu không thì subview đầu tiên trong thanh mà user được mở (mặc định Area Board/All Areas cho user được mở nó).
Trong subnav, Part Numbers kế cuối và Machines cuối. Grouping chỉ là navigation;
production master data Machines/Routes/Part Numbers vẫn permission-based trong
Management, không phải Administration.

**Truy cập Management (post-v18, Phase 14).** Management cần user đã sign-in: vào Management khi chưa sign-in sẽ mở dialog sign-in trên một panel sign-in, và sign-in hết hạn giữ nguyên màn hình đang mở cùng công việc của nó cho cùng user (user khác sign-in thì màn hình bắt đầu lại); trong lúc đó các live view (Area Board, PN Tracking) ngừng tự refresh — đóng dialog sign-in thì nó vẫn đóng — và refresh lại khi cùng user sign-in. Mỗi sub view mở cho user giữ **View all current and historical production data** hoặc một permission mà một action của nó dùng: Area Board chỉ quyền trước; Work Orders thêm Create and edit Work Orders, Edit Work Order Demand hoặc Edit Work Order Allocation; PN Tracking thêm Edit Work Order Allocation hoặc Assign Routes; Priority thêm Set Work Order Demand priority hoặc Reorder Hot items; Planned Routes thêm Manage Planned Routes; Part Numbers thêm Manage Part Numbers; Machines thêm Manage Machines. Khi đã sign-in, thanh sub view chỉ liệt kê các sub view user được mở, và sub view user không mở được (ví dụ qua link) cho biết permission nào mở được nó. Control cho thay đổi user không được làm bị ẩn chứ không bị disable, và một ghi chú `View only` nêu permission khi user không được đổi gì trong view; giá trị đã tải vẫn hiện dưới dạng text. Dialog Part Number details mở chỉ đọc với user không được quản lý Part Numbers (nhãn barcode vẫn dùng được, §11.2, §14.2). Undo và Redo của Priority hiện cho user được đổi Hot list. Scan Station và Production Board không bao giờ yêu cầu sign-in. Server tự kiểm tra mọi lượt đọc và ghi của Management; những gì màn hình ẩn chỉ là presentation.

## 1.2 Signed-in User (post-v18)

Application User sign in bằng login name và mật khẩu (IMPLEMENTATION_ROADMAP Phase 14; quyết định owner OD-P1–OD-P5, 2026-10-06). Administration (Phase 14 slice 2) và Management (slice 3) yêu cầu sign-in. Scan Station và Production Board không bao giờ yêu cầu sign-in.

- **Account chip.** Top navigation kết thúc bằng một account chip: `Sign in` (và `Set up PartFlow` khi PartFlow chưa có Administrator) khi chưa ai sign in, còn lại là avatar và tên cùng menu có `Change password…` và `Sign out`. Chip không có ở nơi top navigation bị ẩn — Scan Station production mode và kiosk Production Board.
- **Sign-in là modal phủ lên view hiện tại.** Route và mọi công việc đang mở giữ nguyên. Sign-in hết hạn mở lại sign-in modal phủ lên view hiện tại (trừ khi một lần lưu theme phát hiện nó đã kết thúc: §2.1 — modal khi đó mở ở request bị từ chối kế tiếp).
- **Thay mật khẩu do administrator đặt.** Khi mật khẩu do administrator đặt phải được thay (Administration → Settings → User sign-in, §9), dialog `Choose a new password` không đóng được trừ khi sign out.
- **Set up PartFlow.** Dialog first-run chỉ khả dụng khi chưa có Administrator; nó hỏi setup token được in trong server log và tạo Administrator đầu tiên.
- **Ai không bao giờ bị hỏi.** Scan Station và Production Board không bao giờ hỏi sign-in (Scan Station dùng thiết bị đã enroll thay vào đó, §4.13); Worker được nhận diện bằng badge, không bao giờ bằng User sign-in (§4.12).
- **Ẩn, không disable.** Control mà User không được dùng thì ẩn, không disable.
- **Connectivity.** Mọi sign-in action tuân theo offline write block (§3 rule 6).

---

# 2. Design System

## 2.1 Một token set, hai theme chuyển đổi được

Toàn app có global Dark/Light toggle; mọi view/dialog/toast theo cùng mode. Dark
mặc định cho shop floor. Component chỉ dùng semantic token, không hard-code màu
theme. Status text có variant bảo đảm contrast; Area identity color không đổi.

Toggle ở top nav; production-mode Scan Station và kiosk board dùng compact
borderless control trong header. Persistence đã chốt: authenticated User preference
→ Scan Station preference → Dark default. Worker Session không ảnh hưởng theme.
**Ranh giới triển khai (Phase 13).** Tier Scan Station là thật: trên `/scan-station/<id>` và route production của nó, preference đã lưu của station được áp khi station load (chưa có preference → Dark), và toggle ở đó lưu nó cho station đó khi station đã load và đang kết nối; khi offline, khi station không load được, hoặc khi save không được xác nhận (một notice cảnh báo nói rõ điều đó và cách lưu lại), thay đổi chỉ áp cho session browser hiện tại và không queue gì. Khi không ai đăng nhập, các route khác giữ lựa chọn trong session. **Toggle lưu preference nào (Phase 14, quyết định owner OD-P16):** khi một User đang đăng nhập, preference đã lưu của User đó áp dụng trên mọi route, và toggle lưu nó trên mọi route, kể cả route Scan Station và Kiosk, và không bao giờ lưu preference của Scan Station. Toggle chỉ lưu preference của Scan Station khi browser đó được biết chắc là không ai đăng nhập; khi trạng thái sign-in chưa biết, thay đổi chỉ áp cho session browser hiện tại. Đăng xuất, hoặc một sign-in đã kết thúc, đưa màn hình về preference của Scan Station trên route station và về Dark ở nơi khác; việc lưu theme không bao giờ mở dialog Sign-in. Khi offline, khi đang bắt buộc đổi password, hoặc khi save không được xác nhận, thay đổi chỉ áp cho session browser hiện tại và không queue gì; một save không được xác nhận hoặc một sign-in đã kết thúc hiện cảnh báo (trên Scan Station là notice cảnh báo nổi của nó; ở nơi khác là toast) nói rõ cần làm gì. Đổi theme không bao giờ được audit.

## 2.2 Color token

- Success `#31d287` — recorded/confirmed.
- Warning `#ffb224` — attention, deviation, due soon.
- Error `#ff6166` — rejected/integrity/overdue.
- Info/accent `#4f8cff` — selection/focus/primary.

Area có stable editable identity color dùng xuyên views. Palette ban đầu: Material
`#8b93a8`, Cut `#f5b83d`, Lathe `#3da5ff`, Mill `#9b6ef3`, Manual `#e06fae`,
Deburr `#2fbf9b`, External `#ff8a4c`, Stockroom `#2fca7c`.

## 2.3 Typography

- System font, không webfont; identifiers/quantity/time dùng monospace.
- Shop-floor body ≥16px, PN ≥19px, quantity ≥18px bold; Board PN ≥22px.
- PN luôn một dòng. Container cố định có thể ellipsis + tooltip; Production Board/
  Tracking size column để giữ full PN.
- PN/WO/Job Number là opaque string, không parse/pad/reformat. Revision tách riêng.
- Description free text có thể wrap 2–3 dòng nhưng không dịch chuyển quantity/
  status/date/action column.

## 2.4 Touch và scanner ergonomics

Scan Station touch target ≥48×48, primary action ≥56px. Desktop management dùng
normal controls. Shop-floor action reachable bằng scan hoặc một tap. Keyboard
wedge gửi text + Enter, không custom driver hay scan-mode selector.

## 2.5 Small screen — vertical-scroll-first

- Mọi view browsable chỉ bằng vertical scroll; document không overflow ngang.
- Top nav collapse thành accessible menu; connectivity luôn visible. Management
  subnav là **một** swipeable row, không wrap, active item auto-scroll into view.
- Wide table ẩn low-priority column rồi collapse thành labeled stacked rows. Active
  Machines giữ row line/wrap khi thật sự thiếu; sorting chỉ wide layout.
- Toolbar giữ primary action cùng search; Scan input được ưu tiên hơn manual button.
- Area Board mobile ẩn tabs, dùng `Summary` toggle. Off: snap carousel per-Area
  details với neighbor peek, Area dots, fixed `‹`/`›`; On: All Areas stacked.
- Chỉ ba intentional horizontal region: desktop All Areas board, Management subnav,
  Area detail carousel. Table/card/dialog không buộc phone horizontal-pan.
- Production Board uniform scale để giữ distance-readable table; swipe đổi page.
- PN one-line và scanner-first không suy yếu trên mobile.

---

# 3. Quy tắc interaction toàn cục

1. **Focus:** Scan input lấy lại focus sau action/dialog/session/reconnect. Dialog
   giữ focus; delayed refocus không được kéo ra. Keyboard wedge capture đủ scan.
2. **Ambiguity:** nhiều valid context phải explicit-select, không default/guess,
   zero write trước confirm.
3. **Feedback:** success/warning/error tức thời. Scan Station notification nổi,
   latest only, close được; success ~4s, warning/error ~8s. OFFLINE banner persistent.
4. **Quantity:** hiển thị source available; over-limit reject với lý do, không clamp.
5. **History:** UI append-only; Undo hiển thị `REVERSED`, original vẫn visible.
6. **Connectivity:** browser events + `/api/health` poll ~1s, timeout ngắn hơn
   interval, không overlap/flicker; recheck focus/visibility. Exact banner:
   `⚠ OFFLINE — Connection to the PartFlow server has been lost. Production actions are disabled`.
   Message trái, full-height `Retry connection` rail phải, divider là border-left;
   không queue local write. Chỉ success sau server-confirmed write.
7. **Vocabulary:** chỉ dùng canonical PN/WO/Demand/Route/Movement/DONE/Repair/
   Allocation/Hot. Null external WO hiển thị `—`, không persist.
8. **One-shot:** không Machine Session/armed action/pending PN sau dialog; Cancel zero write.
9. **Nullable due:** `No due date` trong prose hoặc `—` compact; không warning/error.
10. **Professional copy:** rendered string là user-facing, audit-facing hoặc DEV-only.
    Không phơi internal wording như mock/persist/field names trên normal UI. Audit
    surface có canonical enum. `Cancel (Esc)` thống nhất. Shared `DevNotice` chỉ
    development, tối đa một notice/view.
11. **No false scrollbar:** app shell `100dvh` flex; content scroll khi thật sự
    overflow, nav không dùng duplicated height calculation.
12. **Shared UI clock:** fixed timestamps + shared minute/second subscription;
    không per-component drift. Helper format `<1m`, `18m`, `1h 24m`, `2d 03h`;
    due countdown `N days left`, `due today`, `overdue N days`, `No due date`.
    Due Soon window = lead-time percentage của khoảng received → due (số học số
    nguyên chính xác), clamp vào [`minDays`, `maxDays`], do `dueSoonWindowDays` derive
    từ `DueSoonPolicy` do caller cung cấp; policy là cấu hình **Due Soon warning** đã
    lưu của server (Administration → Settings, §9; giá trị ban đầu Minimum 2 ngày,
    15 %, Maximum 7 ngày), được tải như một phần ready state của mỗi view — business
    logic không hard-code số, không có default ở frontend, và lead time không rõ hoặc
    không hợp lệ thì rơi về window tối thiểu của policy; riêng Scan Station đọc nó
    cạnh Area inventory và, khi không tải được, giữ mọi production action và chỉ giữ
    lại phán đoán `soon` sau một thông báo tường minh — display policy không bao giờ
    chặn production. Mock dùng relative offset resolved một lần.
13. **Release thay đổi là trạng thái write-blocked tường minh (post-v18, Phase 16):** mỗi request mang release của page đang tải; server từ chối thay đổi đến từ page của release khác (không ghi gì) và báo release của mình trong health answer của rule 6. Khi release khác nhau (health answer hoặc một lần từ chối như vậy), **update notice** persistent hiện ở chỗ của OFFLINE banner, theo layout hai vùng của rule 6: copy chính xác `⚠ UPDATED — PartFlow was updated on the server. Reload this page to continue. Production actions are disabled`, rail `Reload page`. Production write submission bị disable đúng như khi mất kết nối, read tiếp tục, connectivity chip hiện `UPDATED`; khi mất kết nối thì OFFLINE banner hiện thay thế. **Page không bao giờ tự reload khi có dialog đang mở.** Trên route Scan Station và Production Board (station không người trực và màn hình treo tường) page tự reload khi notice đã hiện 60 s và không có dialog nào mở, tối đa một lần mỗi 10 phút trên mỗi browser tab, và chỉ khi server đã phục vụ page mới; nơi khác chỉ `Reload page` mới reload, sau unsaved-change guard hiện có. Modal Worker sign-in / `Worker session expired`, dialog enrollment (§4.12, §4.13) và dialog `Choose a new password` (§1.2) không đóng được, nên khi page outdated mỗi cái có nút `Reload page` riêng (dialog password còn hiện `PartFlow was updated — reload the page to continue.`), và khi modal Worker sign-in hoặc dialog enrollment đang mở station không tự reload. Notice không lấy focus. Monitoring feed vẫn refresh, nên status `● Live` của §5 vẫn khỏe và notice nêu lý do. Dialog có lần submit trước đó không có câu trả lời giữ trạng thái unknown-outcome: page outdated không thể giải quyết nó, nên sau khi reload operator phải kiểm tra Area (hoặc Work Order) trước khi lặp lại action. Copy Scan Station khi outdated (chính xác): scan input là `PartFlow was updated — reload to continue scanning`; blocked-scan notice là `PartFlow was updated — scanning is paused` / `Reload this page before continuing. No scans or production updates are recorded until then.`; write chưa gửi là `PartFlow was updated — the {what} was not sent and nothing was recorded. Reload the page to continue.`; dòng blocked của dialog là `PartFlow was updated — the {what} cannot be recorded until this page is reloaded.`; lý do enrollment là `PartFlow was updated — reload this page to enroll the device.` Các disabled reason nêu kết nối ở nơi khác (import Work Orders và dialog của nó, §11.7; sign-in; Edit assigned Route) đọc `PartFlow was updated — reload the page to continue.` khi outdated; mọi disabled reason khác giữ nguyên. View mà code không tải được sau một release đưa `Reload page` (`This view could not be loaded — PartFlow may have been updated.` / `Reload the page to continue. The rest of the application is still available.`). Business rule nằm ở PROJECT_PROFILE; rule này chỉ nêu màn hình hiện gì.

---

# 4. Scan Station

Một screen/station, PN-centric và one-shot.

## 4.1 Routing và chọn Station

- `/scan-station` — selector, không auto-redirect. Card cho active Station hiển
  thị ID, Department, Area, individual Operation chips, Machine presence. Full-card
  button mở standard; sibling `Production mode` mở production, không nested control.
- `/scan-station/:stationId` — standard mode có top nav; invalid/inactive ID lỗi.
- `/scan-station/:stationId/production` — ẩn top nav, nhưng đây không phải auth
  boundary. Giữ offline banner và update notice (§3 rule 13), Worker/session, mọi workflow và theme control.

Cả hai mode đều yêu cầu browser là thiết bị đã enroll của Scan Station đó (§4.13); standard mode trên máy của Manager hoặc Administrator được enroll như mọi thiết bị khác.

Worker pill giữ natural height; actions column align ONLINE ở top, theme ở bottom,
không kéo giãn pill. Footer non-interactive: Station ID · mode ·
`Ctrl+Shift+K: switch mode`; shortcut chỉ toggle cùng station, inert trong text
field/dialog khác.

## 4.2 Layout

```text
┌──────────────────────────────────────────────────────────────────────────────┐
│ Dept / AREA / Operations     [Area statistics][Worker session][● Online]    │
│ OFFLINE banner khi mất kết nối — write bị block                            │
├──────────────────────────────────────────────────────────────────────────────┤
│ Scan barcode card                                                          │
│   Scan input ………………………………………… [⌨ Enter PN manually]                         │
│   Last scanned PN ………………………………… [⟲ UNDO]                                 │
├────────────────────┬─────────────────────────────────────────────────────────┤
│ In this Area now   │ Machine cards grid                                    │
│                    │ [Machine 1][Machine 2][Machine 3]                      │
├────────────────────┴─────────────────────────────────────────────────────────┤
│ Station LATHE-ST-01 · Standard mode · Ctrl+Shift+K: switch mode             │
└──────────────────────────────────────────────────────────────────────────────┘
```

Notification nổi ở bottom, không giữ layout space. Card dùng shared shadow token.

## 4.3 Header

Explicit grid: identity + statistics + Worker. Statistics chỉ xuống full-width
second row khi đo natural widths và không đủ; Worker không đứng row riêng, content-
sized. Không Machine pill/state strip.

Worker mode:

- Scanned: avatar span hai line, name và `Session: 10m 23s`; time success, warning
  khi ≤2m; không active thì `No Worker` / `Session · scan badge`.
- Fixed: avatar/name + `Fixed Worker`, không countdown.
- Disabled: không render pill.

Operations là non-interactive chips trên một line. Khi thiếu chỗ: bỏ label, rồi
gộp trailing chips thành `…` tooltip, cuối cùng ẩn row; dựa fit measurement, không
hard-coded breakpoint. Area totals là summary surface duy nhất:

- Có Machines: Total PNs · Total pcs · Queued · On machines · Done · Hot.
- Không Machines: Total PNs · Total pcs · Processing · Done · Hot.

Tone: PN neutral, total pcs muted, queue warning, on-machine/processing info, done
success, Hot error; Hot zero hiển thị `—`.

## 4.4 Scan barcode card

Main input nhận PN/Worker và Machine one-shot shortcut; main input reject
`PF:SCRAP`, Action barcode không tồn tại, raw PN/unknown zero write. Không Enter
button; placeholder: `Scan Part Number, Worker, or Machine barcode · Press Enter`.

Keyboard-wedge capture first character dù input mất focus, submit đúng một lần;
không intercept typing trong input/textarea/select/contenteditable/dialog hay
modifier shortcut. Touch-primary main input dùng `inputMode="none"`; manual dialog
vẫn mở keyboard.

`⌨ Enter PN manually` nằm cùng row, shrink/wrap trước input; nhận canonical PN rule
và có hint nhỏ. DOM order cố định: input row → hint → DevNotice → Last scanned.

Feedback là floating notification có live-region, viewport-safe và không che main
input/action. Offline banner không phải notification.

## 4.5 Last scanned PN và Undo

Label nằm ngoài bordered block. Block có information region trái và full-height
`⟲ UNDO` button rail ở complete right edge, divider `border-left`, không `|`/gap.
Narrow layout wrap summary nhưng action rail giữ full height. Disabled dim toàn
rail, không dim info.

**Undo không được cấp (post-v18, Phase 14).** Khi role áp dụng tại Scan Station không cấp Undo, vùng action `⟲ UNDO` không được hiển thị: block Last Scanned PN khi đó chỉ còn information region, trải hết chiều rộng block — không có `border-left`, không có vùng action trống; quy tắc hai hàng ở màn hình hẹp không đổi.

Chỉ completed PN action cập nhật target; Worker scan/cancel không. Undo mở structured
summary, original event giữ nguyên và reversal auditable. Sau Undo target lùi tới
eligible previous action; none thì disabled.

Reversing Worker theo Area mode. Sau `Confirm reversal` luôn có final gate:
scanned-session + UNDO option ON → active badge scan; otherwise warning question
`Reverse this action?`. Expired session đã bị badge modal block. Production Undo
đảo complete application command, không arbitrary Movement row.

**Undo reason (Phase 13).** Khi Administration → Correction permissions → **Undo
reason** đang On, summary Undo hiện field `Reason` bắt buộc (label `Reason
(required)`, hint `This reason will be included in the reversal history.`) bên dưới
summary và trước final gate; `Confirm reversal` bị disable đến khi field có chữ,
final gate nhắc lại reason cùng các key fact, và reason được ghi trên reversal và
hiện trong Tracking. Reversal bị server từ chối vì thiếu reason không ghi gì:
summary hiện field kèm lời giải thích của server, giữ mọi selection. Khi option Off
(default) không hiện field reason.

## 4.6 One-shot workflow — temporary wizard

Một modal lifecycle: open → select/input → structured confirmation → confirm/cancel
→ clear local state → refocus. Không nested modal hay close/reopen giữa step.

- Zero production write trước final confirmation.
- Summary hai cột term/value, chỉ applicable rows; operational value mạnh hơn,
  actor/station/time muted. Confirmation chips flatten thành plain verification;
  Area có dot; success/warning/error tone chỉ bổ sung, không là distinction duy nhất.
- Action: Back, `Cancel (Esc)` và named confirm (`Confirm receipt`, `Confirm
  assignment`, `Confirm transfer`, `Confirm addition`, `Confirm repair`, `Confirm
  scrap`, `Confirm return to queue`).
- DONE/QUEUE/Undo luôn thêm final gate. Scanned + corresponding option ON dùng badge
  gate (DONE info-blue; QUEUE/Undo warning); mọi mode khác dùng explicit final
  question. PN bold, any active badge confirms/switches Worker; invalid badge giữ
  nguyên, Cancel về summary, no write.
- Back preserve values và quay đúng parent step; direct surface action không Back.
  Escape cancel toàn workflow; selection screen không phải confirmation.
- Description, recap, input guidance và validation có hierarchy khác nhau.
- Focus first useful control; Receive Quantity settings là ngoại lệ, dialog root
  giữ focus để Enter advance; state local, không hidden Context.

**Machine-first:** `Assign to Machine`, Machine preselected. Step 1 chọn Machine +
queued PN bằng cards/buttons hoặc dropdown chỉ khi measured content không fit;
barcode input nhận Machine cùng Area/queued PN. Empty Enter advance khi pair valid;
filled Enter parse scan, không double action. Step 2 quantity MAX default; Step 3
summary `ASSIGNED_TO_MACHINE` → Confirm. Maintenance/other-Area rejected; không
state armed sau close.

**Monitoring row actions:** Machine row có hai action tách biệt:

- `DONE` → quantity (MAX) → summary → final gate → `AREA_COMPLETED`, clear Machine,
  giữ Area, đưa selected quantity sang Finished.
- `QUEUE` → return unfinished quantity bằng `RELEASED_FROM_MACHINE`; không DONE.

Area không Machine chỉ active-processing row có DONE, same wizard nhưng không
Machine field; partial completion giữ remainder processing. Area Board dùng cùng
component nhưng không action.

## 4.7 Resolve PN scan

1. **Không active Demand:** ba-step `Receive Quantity` áp dụng — mở thẳng khi scan
   nếu PN không có active quantity ở đâu cả, và là explicit choice trong dialog
   của mục 2 và 3 khi PN đã có (post-v18; wizard khi đó lấy separate-quantity
   confirmation ở step 3). Default editable MODIFY
   + FLOATING; settings gồm optional due, starting Area/Operation, reason/notes và
   blank WO reuse; quantity step không default; confirmation `Confirm receipt` là
   write point. `received_date` mặc định là scan timestamp — instant do scan
   resolution phát ra lúc mở wizard, giữ nguyên qua mọi bước và gửi kèm
   confirmation, nên receipt confirm sau nửa đêm vẫn ghi đúng ngày đã scan
   (station không đọc đồng hồ của chính nó ở write point; server derive date
   theo lịch site). New PN copy yêu cầu verify; nhiều blank MODIFY WO phải
   explicit selection **ngay trong settings view** — một step view trong cùng
   modal, không bao giờ nested dialog (§4.6) — và `Next` bị chặn tới khi
   operator chọn; không bao giờ đoán. TypeChip và RouteModeChip dùng chung mọi
   view. **Separate-quantity confirmation (post-v18; PROJECT_PROFILE §14):** khi
   PN **đã có active quantity**, confirmation view nêu tên distribution đó
   (`<Area> × <n> pcs`, nối bằng `·` — internal flow id không bao giờ hiển thị),
   nói bằng warning-toned guidance rằng receipt **không join** quantity đó và
   `Combine quantities` mới là nơi gom chúng lại sau này, và có MỘT explicit
   acknowledgement checkbox. `Confirm receipt` **disabled** tới khi checkbox được
   tick, và Enter ở confirmation view không ghi gì khi chưa tick: quyết định không
   bao giờ là một phím. Distribution đến từ scan resolution, và server xét đúng
   rule đó lúc write — quantity chỉ xuất hiện sau khi wizard mở sẽ quay lại thành
   explicit refusal không ghi gì, hiển thị distribution của chính server ở đây,
   xoá acknowledgement, và được trả lời bằng cùng retry dưới cùng
   `device_event_id`.
2. **PN không ở station Area:** resolve source explicit. Khi có nhiều hơn một
   intent áp dụng ở đây — receive transfer, Repair return của Phase 9, và
   (post-v18) `Receive new quantity` khi **server** báo entry condition của
   `Receive Quantity` — dialog `Select an action` hỏi intent TRƯỚC và không suy
   ra gì: quantity đang chờ ở Area khác không bao giờ khiến transfer thành intent
   duy nhất. Còn lại: một source → quantity MAX →
   confirmation transfer; nhiều source → selection trước, không combine. Planned
   deviation cần reason/confirm. Active processing source ghi atomic completion +
   transfer; ready/queued chỉ transfer. Destination no-Machine ghi direct processing.
3. **PN đã ở Area:** action dialog chỉ valid choices: assign queued, complete từng
   active Machine hoặc direct processing, receive/add, combine, Repair, Scrap,
   transfer khi applicable. Không expose invalid action. **`Receive new quantity`**
   (post-v18, Phase 10.5; chỉ khi **server** báo entry condition của
   `Receive Quantity`) đứng ngay sau `Add more quantity` và mở wizard của mục 1:
   subtitle nói rằng quantity đến kèm Work Order riêng và được ghi **riêng** với
   số pcs đang có ở đây — không merge gì cả. Nó không bao giờ thay cho
   `Add more quantity` và ngược lại: correction ghi quantity tìm thấy bên cạnh
   production quantity đang có, receipt đưa vào quantity kèm business demand
   riêng của nó.

Partial action 1..MAX là production behavior từ Phase 8; server split trong command,
client không tự split. `Combine quantities` chỉ cho server-provided compatible
groups, explicit selection/preview/confirm.

Phase 9 correction UI thật:

- Add more quantity: no MAX/default, reason bắt buộc, Operation resolve, ghi
  `QUANTITY_ADJUSTED · INCREASE`.
- Repair: chỉ server-marked eligible source, normal transfer vẫn choice riêng;
  explicit intent/source/quantity/reason, ghi `TRANSFERRED · REPAIR intent`.
- Scrap: một choice per in-Area portion, chuyển §4.9.
- Undo: server preview là authority, skip ineligible newer command, reuse same event
  id khi retry; server-confirmed success rồi re-read inventory/refocus.

Phase 10 Stockroom receiving UI thật (§10) trên cùng Scan Station shell: ở station
bound terminal Area, scan PN mở `Receive into Stockroom` thay vì transfer thường,
chỉ cho source server đánh dấu stockable (`stock_available`; một source trực tiếp,
nhiều source qua explicit selection; terminal station không có gì để receive thì
giải thích bằng stocked/not-yet-allocated quantity, zero write). Wizard giữ
quantity 1..MAX (server split trong cùng command), route-deviation confirmation,
one-shot write model (retry cùng `device_event_id`); `Confirm stocking` là write
point duy nhất, `STOCKED` ghi ở `Recorded event(s)`. Stocked quantity không Undo
(§4.5 skip).

Phase 10.5 `Receive Quantity` UI thật (§4.7 mục 1): server quyết định nơi workflow
áp dụng (`intake_available`: PN không còn active Work Order Demand và Area của
station bắt đầu được production), station không bao giờ tự đoán. PN không có
active quantity ở đâu cả mở wizard thẳng từ scan; PN đã có active quantity vào
wizard qua explicit choice `Receive new quantity` của mục 3 hoặc của intent dialog
mục 2, và confirmation view khi đó chặn `Confirm receipt` sau explicit
acknowledgement. Receipt tạo Quantity Flow RIÊNG và không đổi gì ở quantity đang
có. `received_date` theo SCAN; `Confirm receipt` là write point duy nhất, theo
đúng rejection / unknown-outcome model của Phase 6 (refusal `selection_required`
quay lại settings view dưới `device_event_id` MỚI, lost response đóng băng intent
dưới CÙNG `device_event_id` và receipt đã commit thì replay). Sau khi server
confirm: reload context/inventory, refocus barcode input, và receipt vào session
log NHƯNG không thành Undo target. Chưa có ở đây: Worker identity và badge gate
(Phase 13 — do slice 5 giao), authorization (Phase 14 — do Phase 14 slice 4 giao:
một receipt cần thiết bị station đã enroll và permission Receive quantity into an Area của role
áp dụng tại Scan Station, §4.13).

## 4.8 Nhập quantity

- Dùng real numeric input, focused + selected; keypad touch là supplement, không
  virtual display riêng. Input `inputMode="numeric"` trên non-touch; touch-primary
  có capability treatment để tránh soft keyboard khi keypad là chính.
- Key 0–9, Backspace, Delete/Clear, Enter confirm, Escape cancel; Space ignored.
  Keypad buttons `type=button`, không giữ focus/trigger lại bởi Enter/Space.
- Transfer/assign/DONE/QUEUE/Repair/Scrap có available MAX và default MAX; addition
  không default/MAX. 1..MAX only; inline guidance/error giữ entered value.
- Partial summary phải show selected quantity, source total, remainder; client chỉ
  submit selected value, server chịu trách nhiệm split atomically.

## 4.9 Scrap workflow

Từ PN action dialog. Dialog chỉ nhận context-sensitive `PF:SCRAP`; mỗi scan tăng
pending count một. `Remove one`/`Reset`, available/pending/remaining luôn visible;
unknown barcode inline error, không đổi count. Reason chung bắt buộc. Final summary
gồm PN, source Area/Machine, original/scrap/remaining, Worker/Station/reason;
`Confirm scrap` tạo đúng một operation. Cancel/escape zero write. Main input luôn
reject `PF:SCRAP`.

## 4.10 Area/Machine monitoring layout

Shared với Area Board detail: fixed/growing left Area summary + right Machine grid;
no-Machine Area chỉ full-width summary. Scan Station summary không lặp header stats.

PN row có grid ổn định: Hot+PN/context+quantity; WO/Job+due; in-Area status/time;
scrap text. Long PN ellipsis/tooltip; action nằm separated rail, không whole-row
button. Area có Machine group On Machines / Queue / Finished; no-Machine group In
processing / Finished; terminal Stocked. Machine card hiển thị name, derived state
age, total/PN list; idle empty, maintenance dashed error border + note/return date.
Machine card chỉ ON_MACHINE, finished luôn ở Area summary.

Expected duration (Phase 11, PROJECT_PROFILE §17): row mang thời điểm `expectedBy`
cố định do shared inventory nêu theo từng flow — thời điểm vào position cộng
expected duration hiệu lực (snapshot của Assigned Route Step hiện tại, nếu không
thì Operation default), không có khi cả hai đều thiếu — và `Time in Area` chuyển
warning tone (`tia.long`, tooltip `Exceeds the expected duration`) khi clock chung
vượt nó; row không có thì không cờ. Chỉ advisory: action của row ở Scan Station
giữ y nguyên.

## 4.11 States

Loading skeleton giữ layout; empty giải thích next action; unknown Station explicit
error; disconnected giữ loaded data nhưng block write; **updated** (§3 rule 13) hiện update notice persistent kèm `Reload page`, block production write submission, scan input `PartFlow was updated — reload to continue scanning` và tự reload sau 60 s khi không có dialog mở; validation inline; long-data
preview kiểm tra PN/row wrapping. Không optimistic completion.

**Implementation boundary (Phase 13 — Worker ID modes).** Area Disabled không render Worker pill; Area Fixed Worker render pill với avatar, tên và `Fixed Worker` của Fixed Worker; mọi production action ghi Worker của Area, và mọi production confirmation summary hiện nó thành row `Worker` trước `Scan Station` (bỏ ở Area Disabled). Station đọc lại context ngầm ở mỗi lần scan được resolve và mỗi khi `DONE` / `QUEUE` của Machine card hoặc `DONE` của direct processing mở wizard — một lần đọc lại ngầm thất bại giữ nguyên station, dialog đang mở và pill như lần đọc cuối, không bao giờ chuyển sang trang lỗi — và ô scan mang placeholder §4.4. Ở Area Disabled và Fixed Worker, badge scan được trả lời bằng `Worker badge scans are not used in this Area` và không đổi gì; ở Area Scanned session, nó sign in, switch hoặc refresh Worker Session (§4.12). Dù thế nào cũng không bao giờ đổi Last Scanned PN. Summary Undo hiện `Worker` gốc và `Reversed by` đúng như server preview. Scanned session chọn được trong Administration → Areas, và badge-confirmation gate của `DONE`, `QUEUE` return và Undo (§4.6, §4.12) là thật: server quyết định form của gate, badge của gate đăng nhập Worker đó và được ghi trên action, và gate bị từ chối giữ nguyên draft, selection và quantity.

## 4.12 Worker identification và session

Worker khác User. Worker là Scan-Station audit identity, profile stable id/name/
existing badge/avatar/active, không employee number; non-`PF:` badge khớp chính xác sau khi chuẩn hóa (trim, uppercase) với
active Workers, nên hoa/thường không bao giờ quan trọng. Mode: Disabled, Fixed, Scanned session. Badge ở Disabled/Fixed chỉ
trả explanatory notice, không sign in. Ghi chú triển khai (Phase 13): Disabled và Fixed Worker là thật; runtime Scanned-session — đăng nhập và chuyển badge, sliding timeout phía server, modal chặn sign-in / `Worker session expired` phía trên các dialog đang mở, đếm ngược trên pill và demo badge chỉ-development — là thật; Scanned session chọn được trong Administration → Areas, và badge-confirmation gate của `DONE`, `QUEUE` return và Undo là thật: server quyết định form của gate, badge của gate đăng nhập Worker đó và được ghi trên action, và gate bị từ chối giữ nguyên draft.

Scanned session dùng configurable sliding inactivity timeout; valid production
interaction refresh, invalid không; badge khác switch ngay. Station không session
hoặc expired hiển thị blocking modal (`Worker session expired` / `Scan your badge
to continue.`); chỉ Scan Station block. Open dialog draft giữ dưới modal, valid
badge đóng modal và restore focus đúng context. Khi page outdated (§3 rule 13) modal hiện `Reload page` và badge input bị disable với copy scan outdated; station không tự reload khi modal đang mở. DEV modal có shared demo badge
notice. Sensitive-action badge gate khác sign-in modal: cancellable và confirm actor.

## 4.13 Device enrollment (post-v18, Phase 14)

Scan Station chỉ hoạt động từ browser mà administrator đã enroll cho station đó (IMPLEMENTATION_ROADMAP Phase 14 slice 4; PROJECT_PROFILE §15, §20; quyết định owner OD-P6 và OD-S4-1, 2026-10-06). Enrollment xác thực thiết bị đầu cuối, không bao giờ xác thực một người: nhận diện Worker vẫn như §4.12, và badge Worker không bao giờ cấp quyền.

- **Màn hình enroll.** Station không load được vì thiếu thiết bị đã enroll — browser không giữ thiết bị, thiết bị đã bị revoke hoặc thay thế, hoặc được enroll cho station khác — hiện một panel enroll thay cho station, nêu Station ID và hỏi enrollment code mà administrator đã phát hành cho station này (Administration → Scan Stations, §9). Code gồm mười ký tự hiển thị dạng `XXXXX-XXXXX`; chấp nhận mọi kiểu hoa/thường và có hoặc không có dấu gạch nối hay khoảng trắng. Station ID không tồn tại hoặc inactive vẫn hiện lỗi station không khả dụng tường minh (§4.1) thay vì panel.
- **Khi đang làm việc.** Khi một request bị từ chối vì thiết bị bị revoke hoặc thay thế trong lúc station đang mở, chính màn hình enroll đó hiện thành dialog chặn như modal badge Worker (§4.12): draft, selection đang mở và Worker Session được giữ bên dưới, và không ghi gì thêm cho đến khi thiết bị được enroll lại. Nếu kết quả của action cuối chưa rõ, station giữ nó và yêu cầu operator xác nhận lại cùng intent sau khi enroll; nó không bao giờ khẳng định là chưa ghi gì.
- **Kết nối.** Enroll là một write: bị chặn khi mất kết nối (§3 rule 6) và không queue gì. Ở production mode panel giữ nút `Station Selector`; bản thân mode không bao giờ đổi việc ai đã được enroll.
- **Station bị gắn lại Area.** Station có Area bị đổi trong Administration khi đang mở sẽ tải lại context mà không đòi enroll: enrollment thuộc về Station ID.
- **Action mà role station không cấp.** Station ẩn các action mà role áp dụng tại Scan Station (§9) không cấp — các lựa chọn action của PN, action trên card và row của Machine, các stepper allocation và `⟲ UNDO` (§4.5). Một scan chỉ mở ra action như vậy sẽ hiện thông báo đỏ và không mở gì. Một từ chối với action mà role không cấp (màn hình cũ) là rejection đỏ thông thường, không ghi gì. Một lần scan badge bị từ chối vì thiếu permission scan badge được hiện trong modal badge Worker (§4.12), modal này vẫn mở.

---

# 5. Production Board

Read-only full-screen Department-wide display, không per-Area filter.

- Shared second clock: time mạnh, date dưới, không control-like.
- Columns: No. · Part Number · Areas & Quantities · Time · Due Date · Total Days ·
  Job Numbers. Content-driven sizing; due/total headings không wrap; PN min 15ch,
  long PN expand, không truncate; description/revision line phụ.
- Hot flame chỉ ở No. column trên Board; other views dùng `🔥#n` trước PN. MỌI
  Hot rank đều có row tint, càng hot càng đỏ theo ba tier của Hot presentation
  chung (rank 1 đỏ, rank 2 cam, từ rank 3 trở xuống cùng amber nhạt nhất).
- Location grid explicit fields `Location | Quantity | State/activity | Time`, track
  widths đo từ widest content across all rows/pages. Long Machine/Area không ellipsis.
- Assigned Machine chip + `on machine`; queue/direct processing/done rõ. Quantity
  tone: queue warning, processing/Machine info, done success; state word dim. External
  shows activity chip; READY không hiện Machine là executor.
- Dwell derive từ timestamp/shared clock; amber (`long`, tooltip `Exceeds the
  expected duration`) khi vị trí đã vượt expected duration hiệu lực
  (PROJECT_PROFILE §17 — snapshot của Assigned Route Step hiện tại, nếu không thì
  Operation default): server nêu thời điểm cố định `expected_by` mà portion sớm
  nhất của location vượt expected duration của chính nó (không average, portion mới
  không che portion overdue), clock chung đánh giá; không có expected duration thì
  không cờ — stand-in `≥ 3 ngày` (`LONG_DWELL_MINUTES`) của Phase 2 đã bỏ, không
  có rule cố định thay thế. Chỉ advisory, không block gì. Total row có one
  continuous separator và reconciled quantity. Scrap là plain error text `n scrapped`.
- Due countdown derive; Hot sort trước theo rank, rồi canonical due ordering. Chỉ
  urgency text blink; Hot flame pulse riêng; reduced-motion tắt animation.
- Dynamic pagination đo actual viewport/row; ≥1 row; fallback 10 trong layout-less
  test. Auto rotate chỉ multi-page, dwell theo **seconds per displayed row** và
  **minimum page dwell** của Department (ban đầu 3 s và 6 s) — Department display
  settings (§9), cấu hình theo Department, đi cùng board feed và áp dụng ở lần
  refresh kế tiếp, không bao giờ là hằng số UI hard-code (tooltip rotation indicator
  nêu giá trị của Department). Buttons/dots/arrows/swipe không wrap; manual change restart timer.
- Rotation progress dùng cùng deadline, hidden khi one page; reduced motion giữ
  seconds text nhưng ẩn moving track.
- `Auto scale` default On dùng one uniform zoom, scale up/down để full table width
  fit; pagination chia height budget theo cùng factor. Off trả baseline.
- Footer nằm trong flex flow, không fixed/overlay; controls + aggregate + legend.
  Header identity: Department trên, `Production` + connection-toned `● Live`; healthy
  dot dùng shared heartbeat; stale có `Feed stale — reconnecting`. Khi page outdated (§3 rule 13) mà feed vẫn refresh, `● Live` vẫn khỏe và update notice nêu lý do; `Feed stale — reconnecting` dành cho connecting, offline và refresh lỗi.

**Implementation boundary (Phase 11):** production UI thật trên
`GET /api/production-board` (IMPLEMENTATION_ROADMAP Phase 11): rows đến theo
canonical board order từ read model của **server** — phân bổ theo Area / Machine /
External activity với state derive, timestamp vào vị trí cố định, stocked và
scrapped, Work Order / Job Number context còn mở và Hot rank đều derive server-side (Work Order đã complete không cấp context cho row; rows theo đúng canonical demand ordering — stocked không phải tầng sắp xếp; quantity đã merge đọc qua MỌI nhánh lineage nên dated theo entry cũ nhất của cả khối merge và chỉ nêu Machine hoàn thành khi các nhánh đồng nhất) từ
projection vị trí hiện tại và Movement history — còn mọi giá trị thời gian hiển
thị (dwell theo vị trí và cờ `long` — đánh giá theo thời điểm `expected_by` cố
định của server —, due countdown, `Total Days`, đồng hồ) vẫn
derive lúc render từ UI clock chung (§3.12). Dòng Department nêu Department server
resolve (Department active duy nhất, hoặc Department chỉ định bằng
`?department=<id>` trên URL màn hình — địa chỉ presentation cho màn hình treo
tường, không phải route); cấu hình mơ hồ bị từ chối tường minh. Auto-refresh poll
feed định kỳ (một request in flight, refresh ngay khi kết nối trở lại sau khi
mất): refresh lỗi giữ rows hoàn chỉnh cuối cùng và `● Live` chuyển tone warning
kèm `Feed stale — reconnecting` giống hệt khi connectivity chung không khỏe; load
đầu lỗi là error state có Retry. Vì `● Live` là trạng thái vận hành của chính
board nên nó chỉ xanh khi đã có board hoàn chỉnh trên màn hình — load đầu đang
chạy hoặc đã lỗi đều mang tone warning kèm ghi chú, không bao giờ hiện feed
"live" trên board rỗng. Cả hai render dưới header board luôn hiển thị
(Department, tiêu đề với status, đồng hồ), footer hiện khi đã có board hoàn
chỉnh. Cột Job Numbers nêu mọi demand của row (`<job numbers> · WO <number hoặc —>
[· MODIFY] · <n> pcs`, hoặc `· allocated a/n` khi đã allocate), dòng tên /
revision PN (`{name} · rev {revision}`) lấy từ chi tiết Part Number đã lưu (Phase 13) và vắng khi chưa có. Từ Phase 13, Department của feed mang rotation timing của nó, và board đọc Due Soon policy ở mỗi lần refresh (đọc policy lỗi hành xử đúng như đọc board lỗi). Mọi thứ
khác ở trên — kiosk, pagination và rotation, auto scale, điều hướng tay, location
grid, tooltip, legend — giữ nguyên; mock dataset Phase 2 của board đã bỏ, các
preview `?state=` chỉ development (loading / empty / error / long) render state
xác định mà không request.

## 5.1 Kiosk mode

- `/production-board` standard; `/production-board/kiosk` kiosk. Route explicit,
  không query/local boolean. `Ctrl+Shift+K` toggle, presentation-only.
- Kiosk ẩn top nav nhưng giữ OFFLINE banner và update notice, và board tự reload như §3 rule 13 mô tả; dùng same board header, compact theme
  toggle, live status, clock; full viewport không leftover offset.
- Footer có slide switch `Kiosk` On/Off (v18 — `role="switch"`, accessible name
  `Kiosk mode`, thay nút `Enter kiosk` / `Exit kiosk` của v17); shortcut/theme/
  auto-scale/manual pages vẫn dùng được.
- Không browser fullscreen/security guarantee; wall-display operator vẫn phải cấu
  hình browser/device riêng.

---

# 6. Area Board

Management monitoring view gồm All Areas và per-Area detail; “Manager Summary” đã
retire và content chuyển vào All Areas.

## 6.1 Tab strip và toolbar

Desktop tabs: All Areas default rồi từng Area có dot/count. Toolbar search PN/WO/
Job, sort Due/Priority/Time/Quantity, scope meta PN + pieces, và **trạng thái
feed** của read live — `● Live` tone success với heartbeat chung, hoặc
`● Feed stale — reconnecting` tone warning (§5: cùng câu chữ và cùng ý nghĩa với
Production Board, không bao giờ chỉ bằng màu, và là trạng thái của BOARD chứ
không phải của kết nối).

## 6.2 All Areas overview

Desktop một column mỗi Area, horizontal scroll mặc định; `Wrap columns` cho wrap và
giữ state khi đổi tab. Mobile dùng Summary toggle/carousel như §2.5.

Area column có clickable header/color/name/description/Operation chips; meaningful
stats; **một row mỗi Part Number** (các quantity riêng biệt gộp vào chip portion
của row đó); shared PN-row components với Hot, quantity, WO/Job, due countdown, portion
context, time, scrap; explicit empty. Terminal shows stocked pcs/PNs. Search filter
list (khớp MỌI open demand của PN, kể cả demand nằm trong `+N more`), sort trong
mỗi column SAU khi gộp theo PN — `Quantity` so tổng của PN (`6 + 6` trên `10`),
`Time in Area` lấy portion cũ nhất, `Priority`/`Due date` giữ nguyên semantics;
per-Area detail vẫn sort từng quantity riêng. Mobile dots/buttons derive active page từ scroll,
Summary-on stacks overview; click header jump tới corresponding detail page.

## 6.3 Per-Area detail — shared monitoring layout

```text
[ In this Area now ] [ Machine cards grid                  ]
[ fixed left col   ] [ Machine 1 ][ Machine 2 ][ Machine 3 ]
[ grows vertically ] [ Machine 4 ][ Machine 5 ]            ]
```

Area card có stats và grouped PN list: On Machines/Queue hoặc In processing,
Stocked terminal, Finished READY. Machine cards có state age, assigned totals/list,
idle/maintenance. Grid không chui dưới left card; narrow one-column. No-Machine
render only full-width summary. Hoàn toàn read-only, shared components không action
rail. Sort Time derive timestamp/shared clock. Long PN ellipsis + tooltip; empty
`No production in {Area}`.

**Implementation boundary (Phase 11):** production UI thật trên
`GET /api/area-board` (IMPLEMENTATION_ROADMAP Phase 11). MỘT read của Department
trả về mọi Area ACTIVE mang **cùng model monitoring Area mà Scan Station đọc**
(`app/api/area_inventory.py` — mode của Area, mọi Quantity Flow ACTIVE với
holding state derive ở server, Machine card chỉ giữ quantity đang gán, các nhóm
queued / processing / finished và tổng, demand context MỞ của PN, và scrapped
theo PN trong Area đó — dòng `{n} scrapped` của row dùng chung, nên station cũng
hiển thị), cộng Operation active, và — với terminal Stockroom, nơi quantity đã
hoàn tất sản xuất nên không còn active flow — các stocked line kèm allocation
active của PN và demand context MỞ của PN (Hot rank, Work Order Number và Job
Numbers của row Stockroom theo cùng quy tắc với mọi row monitoring và row
stocked-only của Production Board; `WO — · —` chỉ khi không còn Work Order nào
của PN mở). All
Areas overview và per-Area detail là **hai presentation của cùng một trả lời**:
đổi tab không read lại và hai mode không thể lệch nhau; cả hai render qua
component chung và cùng mapping client với Scan Station.

**Overview theo PN, detail theo từng quantity** — cố ý, vì hai chỗ trả lời hai
câu hỏi khác nhau. Một Part Number trong một Area là ĐÚNG MỘT row overview và
được đếm một lần trong mọi con số PN (count trên tab, dòng meta toolbar,
`Total PNs` / `PNs`), dù PN đó giữ bao nhiêu quantity riêng biệt: các quantity
được gộp vào chip portion của row (`Lathe 3 × 3`, `queue × 2`, `processing × 6`,
`done × 1`), và các chip luôn cộng đủ tổng của row — không mất, không đếm trùng,
kể cả khi PN vừa xử lý nội bộ vừa ở Operation external trong cùng Area (phần dư
direct là chip riêng). Per-Area detail giữ MỘT row cho MỖI quantity riêng biệt,
vì mỗi quantity được scan và thao tác riêng tại Scan Station (§4.10).

**Row được làm CHO cái gì là OPEN demand của PN, không phải nguồn gốc của
quantity.** Hot rank, due countdown, Work Order Number và Job Numbers của mọi
row monitoring lấy từ các OPEN Work Order Demand của PN theo canonical demand
order (PROJECT_PROFILE §18) — demand ĐẦU TIÊN quyết định. Khi PN có nhiều open
demand, row nêu demand quyết định kèm `· +N more`, tooltip liệt kê đầy đủ: không
gộp thành một giá trị mơ hồ, cũng không âm thầm rút còn cái đầu. Work Order đã
complete là lịch sử và không cấp gì cả, kể cả khi quantity nó release vẫn còn
trong Area: row đó đọc `WO — · —` với `No due date` thay vì mượn context không
còn thuộc về nó. Demand mà quantity BẮT NGUỒN từ đó vẫn nằm trên chính quantity
như provenance, và provenance **chỉ là context của workflow và audit**: nó nêu
đúng lô trong dialog thao tác, confirmation và Last Action recap của Scan
Station, nơi operator sắp thao tác trên chính lô đó. Nó không bao giờ cấp dữ
liệu cho một row monitoring, ở cả hai surface — `In this Area now` của station
và row của Area Board là MỘT presentation, nên một quantity không thể đọc thành
làm CHO thứ này ở station và thứ khác trên board — và hai thứ không bao giờ
trộn trong một dòng. Mỗi
row còn mang giá trị monitoring cố định của CHÍNH quantity: timestamp vào Area
(đọc qua mọi nhánh lineage nên quantity đã merge dated theo nhánh cũ nhất) và
Machine ĐÃ HOÀN THÀNH quantity finished — báo từ chính quantity, nên Machine
retired sau khi làm xong vẫn nêu được nơi hoàn thành dù card đã biến mất.
`Time in Area` và due countdown vẫn derive lúc render từ UI clock chung (§3.12);
row Stockroom hiển thị `allocated a/n` thay countdown và không có thời điểm vào.

Department là Department active duy nhất hoặc `?department=<id>`; cấu hình mơ hồ
bị từ chối tường minh. Auto-refresh theo đúng nhịp của Production Board (một
request in flight, refresh ngay khi kết nối trở lại): refresh lỗi giữ board hoàn
chỉnh cuối và chuyển status thành `Feed stale — reconnecting`, load đầu lỗi là
error state có Retry, Department không có Area active là empty state tường minh,
và `?state=loading|empty|error|long` vẫn render preview development mà không
request. Search (chạm tới mọi open demand của PN, không chỉ demand đứng tên
row), bốn thứ tự sort (áp lên overview SAU khi gộp theo PN, nên `Quantity` so
tổng của PN trong Area; `Priority` xếp MỌI Hot rank trước mọi row không rank,
bất kể số rank lớn tới đâu) và các lựa chọn layout (Wrap columns,
Summary toggle và phân trang màn hình hẹp) là presentation state của view — Area
Board không có canonical order để server sở hữu, khác Production Board. Mock
dataset Phase 2 của board này đã bỏ. **Expected duration:** `Time in Area` của
row chuyển warning tone (`tia.long`, tooltip `Exceeds the expected duration`) khi
quantity đã vượt expected duration hiệu lực của position (PROJECT_PROFILE §17 —
snapshot của Assigned Route Step hiện tại, nếu không thì Operation default; không
có cả hai thì không cờ): shared inventory nêu thời điểm `expected_by` cố định theo
từng flow, clock chung đánh giá, và row overview theo PN warning từ portion sớm
nhất của PN — mỗi portion theo expected duration của chính nó, không average — nên
portion mới không che portion overdue. Cùng shared row warning y hệt ở Scan Station
(§4.10); chỉ advisory, mọi action giữ nguyên.

---

# 7. Tracking

Operator title **PN Tracking**, route/internal name giữ Tracking. Filtered results
table + modeless fixed lower-right detail overlay. Whole row toggles selection; PN
cell button là single focusable control. Panel không backdrop/focus trap và không
reflow table; close bằng ✕/Escape/selected-row/outside click; ≤900px spans viewport.

## 7.1 Filter và list

Search PN/WO/Job; filters Area/Operation/Machine/Request Type/Hot/status/due. Columns:
PN+name/Hot, active Demand, distribution dots, active/stocked/scrapped quantity,
next due và status pill (Active / Stocked / Open / Completed — status derive của
IMPLEMENTATION_ROADMAP Phase 11: `Active` khi còn quantity trong production,
`Completed` khi không còn open Work Order Demand, `Stocked` chỉ khi còn stocked
quantity CHƯA ALLOCATE — effective `STOCKED` trừ active allocation — chờ open
demand, và `Open` — implementation thêm vào — khi open demand không có quantity
nào trong production và không còn stock chưa allocate, kể cả stock đã allocate
cho work trước đó). Deleted-master PN vẫn
canonical, metadata `—`; null WO `—`. **Ranh giới triển khai (Phase 11):** list
là production UI thật trên `GET /api/tracking` — search (debounce, trên PN, WO
Number và Job Number của MỌI demand của PN) và mọi select được đánh giá server-side
trên row derive, due window judged trên site calendar theo next due date của PN,
rows theo canonical demand order, và list có bound: `Showing n of m PNs` với
`Show more` mở rộng page một lần tới bound của server rồi yêu cầu thu hẹp search.
Title row mang feed status của live view (`● Live` / `Feed stale — reconnecting`,
§5 — status của chính list, không phải của kết nối), list tự refresh theo nhịp
monitoring chung.

## 7.2 Detail panel

1. PN master/current metadata + derived barcode; absent master không ảnh hưởng history.
2. Active Demand table với allocation progress, labeled separate from Movement. Line được allocate vượt demand bằng một correction được cấp quyền hiển thị `(+n beyond demand)`; summary và progress bar chỉ tính mỗi line đến requested quantity của nó và nêu riêng phần beyond-demand (`Allocated 18 / 20 requested · +2 beyond demand`), để một line còn thiếu không bao giờ bị hiển thị là đã đủ nhờ phần dư của line khác.
3. Current Area/Machine bars, derive Movement.
4. Flow & Routes: shared compact `RouteModeChip`; Planned snapshot state/deviation;
   Floating actual trace, repeated Area/split/Repair; arrows separate siblings;
   finished rack không route step. Flow `PLANNED` có route riêng đã được adjust (Phase 14 slice 6, PROJECT_PROFILE §8.10) hiện `(snapshot, adjusted)` trong Planned note và một ghi chú `Route adjusted …` cho mỗi adjustment — thời điểm, user, reason và "the steps after step n were replaced", kèm ghi chú giữ audit — mọi adjustment, cũ nhất trước, đặt sau các deviation note, không cắt bớt. Ghi chú thuộc về flow có route được adjust: split child hoặc merge result copy route như lúc đó (hiện `(snapshot)`) và dòng position của nó nêu flow nguồn, block của flow nguồn mang các ghi chú.
5. Immutable reverse-chronological Movement history, canonical types, Repair badge,
   DONE vs Stocked distinction; no edit.
6. Scrap history + cumulative/reconciliation.
7. Stocked & Allocation history. Entry Management nêu tên user đã ghi nó; correction beyond-demand được đánh dấu `beyond demand`.
8. Authorized corrections: adjustment, route, allocation, priority, audit; reason
   bắt buộc và tạo new history — mọi correction (allocation beyond demand, reversal) cần reason và tạo new history; allocation thường của stocked quantity để lại cho sau, được cung cấp từ cùng dialog `Adjust WO Allocation` (§11.6), nhận ghi chú tùy chọn. `Change priority` và `View audit trail` không phải correction flow và không nhận reason: `Change priority` là link vào Management → Priority, nơi chứa các action của Hot list (§8 — thay đổi Hot list được thực hiện và audit ở đó: remove và reorder cần xác nhận; add cần chọn tường minh Work Order Demand; OD-P18), hiện cho user được set hoặc reorder priority; `View audit trail` mở audit trail chỉ-đọc của PN (§7.4), hiện cho mọi user được mở Tracking. Vì là điều hướng và là thao tác đọc, cả hai vẫn dùng được khi mất kết nối (§3 rule 6 chỉ chặn write, và Priority tự chặn write của nó); opener của các correction flow vẫn bị vô hiệu khi mất kết nối.

**Dialog Edit assigned Route (Phase 14 slice 6).** `Edit assigned Route…` (Assign and edit Routes) mở `Edit assigned Route — {PN}`: khi PN có nhiều flow `PLANNED` đang active, user phải chọn Quantity Flow tường minh trước (không preselect; flow khác giữ route của chúng); chỉ có một lựa chọn thì preselect, PN không có Planned flow active nhận lời giải thích đơn giản. Các step mà quantity đã tới hoặc history ghi nhận bị **khóa** (`Done` / `Current` / `Recorded`; `Recorded` là step của một arrival đã undo, vẫn giữ), kèm hint chính xác nêu step mà arrival on-route kế tiếp được kiểm tra (hoặc, khi không còn step nào phía sau, nói rằng không có step nào được kỳ vọng và arrival kế tiếp cần xác nhận route deviation). Chỉ future step được sửa, bằng **row** step của Planned Routes (quy tắc step-row §13.2; đánh số tiếp sau các step bị khóa; tail có thể để trống), marker `● Unsaved changes` và guard `Discard unsaved route changes?` của §13.2. Reason bắt buộc. `Review adjustment` dẫn tới bước review (`Adjust the assigned route?`) hiện các step trước và sau — mỗi step dạng `{Area} · {Operation} · Est. {time}` kèm preferred Machine của nó, và nêu tên các step chỉ đổi instructions; route đã đổi kể từ lúc mở bị từ chối bằng `Reload route` (edit bị bỏ); kết quả không rõ cho `Retry` và adjustment chỉ được áp dụng một lần. Chỉ AssignedRoute của chính flow đó đổi — không bao giờ Planned Route, flow khác hay Movement history, và route trước được giữ trong audit history.

## 7.3 States

Per-section skeleton; `No PNs match — clear filters`; section Corrections ẩn hoàn toàn — không bao giờ vô hiệu — với user không giữ permission của nút nào trong đó, và nút user không được dùng thì vắng mặt; vì `View audit trail` theo tập quyền đọc của Tracking, mọi user được mở Tracking đều thấy section với ít nhất nút đó. Tag `authorized actions — recorded with your name` chỉ hiện khi có một correction có ghi nhận (Edit assigned Route, Adjust WO Allocation) được cung cấp (quyết định 2026-10-06, OD-P15). **Ranh giới triển khai (Phase 11):** detail
là production UI thật trên `GET /api/tracking/detail` — mỗi PN được chọn là một
polled read riêng với skeleton loading và error-with-Retry ngay trong panel,
refresh lỗi giữ detail hoàn chỉnh cuối kèm ghi chú `Feed stale — reconnecting`
dưới PN, Movement history theo thứ tự thời gian ngược (`occurred_at`, id
Movement phân định hòa) và phân trang trên đúng thứ tự đó (`Showing n of m
Movements`, `Show older Movements` nối thêm page kế của history bất biến, không
hở khoảng không trùng), Scrap history (§7.2 mục 6) liệt kê các event `SCRAPPED`
của PN như row của chính history đó — timestamp, quantity, Area, reason, event
đã undo vẫn giữ kèm badge `REVERSED` — dưới con số tích lũy net, có `Show older
scrap events`, section Quantity Flows phân trang theo một thứ tự ổn định — flow mới nhất
trước, theo flow id bất biến; status active / closed của flow chỉ là
presentation, không bao giờ là vị trí — mỗi lần một
page có bound (`Show older Quantity Flows`; current quantity vẫn đầy đủ ở
section `Current quantity by Area` bất kể phân trang flow), allocation history
phân trang tương tự (`Show older allocation entries`) — không gì bị cắt ngoài
tầm với —, page đã nối của bất kỳ section nào bị bỏ và đọc lại tới cùng độ sâu
khi refresh dời ranh giới của nó hoặc đổi nội dung các row nó hiển thị (flow
đóng hoặc mở lại, scrap bị undo), không bao giờ khi refresh không đổi gì liên
quan, name / revision / image / ERP
id từ master lấy từ chi tiết Part Number đã lưu từ Phase 13 và render `—` (ảnh mặc định) khi vắng,
và section Corrections (§7.2 mục 8) ẩn hoàn toàn với user không giữ permission của nút đã triển khai nào của nó — không bao giờ render nút vô hiệu. **Ranh giới triển khai (Phase 14 slice 5):** section Corrections chỉ render cho user giữ permission của ít nhất một nút đã triển khai của nó và chỉ hiện các nút đó — `Adjust WO Allocation…` (Edit Work Order Allocation, §11.6) — dưới tag `authorized actions — recorded with your name`, với kết quả của lần adjustment gần nhất là dòng trạng thái dưới nút; nút chưa triển khai thì vắng mặt, không bao giờ vô hiệu. **Ranh giới triển khai (Phase 14 slice 6):** các nút đã triển khai là `Edit assigned Route…` (Assign and edit Routes, §7.2 mục 8) và `Adjust WO Allocation…` (Edit Work Order Allocation, §11.6), theo thứ tự đó, và một dòng trạng thái duy nhất hiện kết quả của correction gần nhất (route adjustment hoặc allocation change); chữ ký refresh `flows` gồm cả route adjustment, nên các page flow đã nối thêm được đọc lại sau đó. **Ranh giới triển khai (Phase 14 slice 7):** các nút đã triển khai là `Edit assigned Route…`, `Adjust WO Allocation…`, `Change priority` (Set Demand Priority hoặc Reorder Hot Items; link vào Priority) và `View audit trail` (bất kỳ key nào mở được Tracking), theo thứ tự đó — nên mọi user mở được Tracking đều thấy section với ít nhất `View audit trail`; tag chỉ hiện khi `Edit assigned Route…` hoặc `Adjust WO Allocation…` đang hiện; hai opener correction-flow đó bị vô hiệu khi không kết nối, còn `Change priority` và `View audit trail` vẫn bật; dòng trạng thái duy nhất vẫn chỉ báo correction gần nhất. Dòng position của một
Quantity Flow thêm ghi chú advisory tường minh `· exceeds expected duration`
(warning tone, viết ra chữ — không bao giờ chỉ màu) khi position đã vượt expected
duration hiệu lực (PROJECT_PROFILE §17 — thời điểm `expected_by` cố định của
server do clock chung đánh giá; không có thì không ghi chú). **Implementation boundary (Phase 13 — Worker identity):** row Movement nêu Worker đã ghi dạng `W: <name>` và ghi chú route-deviation thêm ` by <name>`; row không có Worker đã ghi thì bỏ.

## 7.4 Audit trail (Phase 14)

`View audit trail` (§7.2 mục 8; user được mở Tracking) mở một dialog chỉ-đọc xếp chồng trên detail panel — nó sở hữu Escape và trả focus về nút khi đóng — có tiêu đề `Audit trail — {PN}`. Dialog liệt kê các thay đổi đã ghi của PN, mới nhất trước: chi tiết Part Number của nó (tạo, sửa, thêm / thay / gỡ ảnh, xóa); các Work Order và dòng Work Order Demand yêu cầu nó (tạo, sửa, một Work Order được hoàn tất bởi một thay đổi demand — dòng đã bị xóa vẫn giữ history đã ghi và được đánh dấu `(since deleted)`; thay đổi header của Work Order và việc hoàn tất hiện cho mọi PN mà nó yêu cầu); mọi thay đổi Hot rank của các dòng đó kèm nguyên nhân (một dòng chỉ bị dịch chuyển vì một entry khác được thêm, gỡ hoặc di chuyển sẽ nói rõ như vậy, không bao giờ nói chính nó bị di chuyển hay bị gỡ); các entry Management allocation của nó, mọi allocation reversal (kể cả reversal của Stockroom) và các correction beyond-demand được cấp quyền, có đánh dấu; và các route adjustment của Quantity Flow của nó (step bị thay và step mới, mỗi step kèm Area, Operation, estimated time, preferred Machine và instructions). Mỗi entry hiện thời gian, điều gì đổi và trên Work Order, dòng demand hay Quantity Flow nào, user đã ghi (avatar và tên; không hiện gì khi không có user được ghi — bản ghi của Scan Station và history từ trước khi có sign-in), reason khi có, và giá trị trước → sau bằng ngôn ngữ người dùng (không có field identifier). Production Movement — kể cả Undo — ở lại Movement history (mục 5) và Stockroom allocation thường ở allocation history (mục 7); thay đổi cấu hình không thuộc PN này không được liệt kê. Không gì trong trail có thể sửa hay xóa. Trail phân trang như các history khác của Tracking (`Showing n of m entries`, `Show older entries`, keyset theo thời gian — không gì bị cắt ngoài tầm với) và là snapshot read khi mở: phân trang không bao giờ lặp một entry và không bao giờ bỏ sót entry đã ghi trước khi trail được mở; thay đổi được ghi khi trail đang mở có thể xuất hiện ở page sau hoặc chỉ sau khi mở lại, và page cuối khi đó nói `reopen the audit trail to include changes recorded since it was opened`. Đây là thao tác đọc và vẫn dùng được khi mất kết nối (lỗi đọc hiện kèm Retry). Trạng thái: loading, error kèm Retry, empty `No recorded changes for {PN} yet.`

---

# 8. Priority Management

Hot ranking thuộc WorkOrderDemand. List có `🔥#n`, WO/Job, TypeChip, demand figures,
distribution và due tone.

- Add search/scan: 0 eligible không add; 1 add trực tiếp; nhiều phải chọn exact
  Demand; new entry bottom.
- **Arrival từ Tracking (Phase 14, OD-P18):** `Change priority` của Tracking (user được set hoặc reorder priority) mở view này cho một PN. Khi PN có Hot entry, chúng được highlight và một dòng trạng thái nêu rank của chúng (`{PN} is on the Hot list at #2 and #5.`); nếu không, với user được add, dialog Add mở ra liệt kê tất cả và chỉ những Work Order Demand của PN đó có thể vào Hot list (khớp PN chính xác, không bao giờ tìm theo text; ô search để trống với một lần search khác) — không gì được add cho đến khi một dòng được bấm, kể cả khi chỉ có một dòng; với user chỉ được reorder, một dòng trạng thái nói PN không nằm trong Hot list và tài khoản có thể reorder nhưng không add. Bản thân việc arrival không đổi gì.
- Remove confirm PN/Demand; confirm close rank gap + audit; Undo restore.
- **Automatic removal (quyết định 2026-10-04):** entry có line trở thành allocate đủ (gồm cả Work Order completed) rời list cùng với allocation hoặc demand edit gây ra nó — không confirmation, rank đóng khoảng trống, có audit. List hiển thị lần đọc gần nhất; confirmation thực hiện trên list cũ bị từ chối là stale và hiển thị list hiện tại, còn step Undo/Redo mà entry đã rời list bị bỏ kèm thông báo.
- Drag/Move Up/Down/Undo/Redo đều confirm **trước** apply. Dialog show moved summary,
  impact và Current Position → New Position snapshots chỉ affected rank range.
  Transition `#old → #new`; added/removed dùng `Not listed`; shared content-sized
  tracks align PN/metadata; mobile stacked fallback.
- Apply ranking/Cancel; không renumber sớm; Undo/Redo title user-facing, depth
  unlimited trong session, và lịch sử Undo/Redo thuộc về user đã sign-in: nó kết thúc cùng application session, khi user sign-out hoặc khi user khác sign-in (sign-in hết hạn được cùng user gia hạn thì giữ nguyên); mỗi step audited và cũng cần confirmation.
- Footer diễn giải Hot first → due-date ordering, không phơi field/tie-breaker; footer cũng nói entry tự rời list khi line của nó được allocate đủ.

---

# 9. Administration

Tách production, sidebar:

- Organization: Departments, Areas, Operations, Workers.
- Production setup: Scan Stations, Barcode configuration, Scan behavior.
- Access: Users, Roles & permissions.
- Policies: Worker sessions, Machine assignment, Correction permissions, History
  archival & purge, Department display settings, Settings.

Worker sessions sở hữu default/per-Area sliding timeout và ba independent default-On
badge-gate options cho DONE/QUEUE/Undo; option chỉ đổi form của always-present final
gate. Từ Phase 13, section Worker sessions là thật cho timeout — giá trị default và override theo Area là cấu hình được lưu (số phút nguyên, 1–720, default 15); ba badge-confirmation option cũng là cấu hình được lưu (default On), mỗi công tắc được lưu ngay khi đổi. Policy **Correction permissions** giữ công tắc **Undo
reason** — một option On/Off toàn cục (default Off) bắt buộc mọi Undo phải có reason
(§4.5; PROJECT_PROFILE §16 "require a reason when configured"), do server enforce và
lưu ngay khi đổi; ai được undo hoặc correct là bảng role × correction-permission dưới công tắc Undo reason
(cấu hình từ Phase 13; được enforce khi các slice Phase 14 thêm các kiểm tra — users sign in được từ Phase 14 slice 1, Edit Work Order Allocation được kiểm tra từ slice 3, còn các correction permission khác chưa được kiểm tra). Department
display settings được cấu hình **theo Department, không bao giờ global (quyết định post-v18)** — bảng Department với editor cho Production Board rotation timing (seconds per displayed row 1–60, minimum page dwell 1–300 s; default 3 và 6). Panel **Due Soon warning** của **Settings** sở hữu cấu hình đứng sau mọi due countdown derive (§3.12): **Minimum warning days** (0–365), **Lead-time warning percentage** (1–100) và **Maximum warning days** (0–365, không bao giờ dưới minimum) — giá trị ban đầu 2 ngày, 15 % và 7 ngày; một policy toàn cục; section **Settings** còn chứa panel **User sign-in** (post-v18, Phase 14) — chỉ cho application User, tách khỏi **Worker sessions**: thời hạn user sign-in theo ngày hoặc không bao giờ (ban đầu 30 ngày), số lần sign-in sai trước khi khóa (10) và thời gian khóa (15 phút), và việc users có phải thay mật khẩu do administrator đặt ở lần sign-in kế tiếp hay không (On) — người đã sign in đọc được (người khác thấy lời nhắc sign in) và chỉ users có role được cấu hình system settings mới sửa được; phần còn lại của Settings chưa khả dụng.

Không có Machine, RouteTemplate hay PartNumber registry trong Admin; chúng ở
Management. Mục Machine assignment dưới Policies vẫn là một policy statement (hai mode ownership của Area), không phải Machine registry — từ Phase 13 là section chỉ-đọc: statement hai hàng (không Machine → `Direct processing (no Machines)`; một Machine trở lên → `Queue → assign (one-shot)`, không bao giờ tự động), không có entry action, trỏ tới Management → Machines và bảng Areas. Barcode configuration có persisted Asset Tag format prefix + 1–8 digit
minimum width, live Next Tag/scanned barcode; whitespace/colon prefix invalid, không
trim/clamp; format change không rename old tag hay reset never-reuse sequence.

Phase 3.5 Departments/Areas/Operations/Stations/barcode là real API-backed UI với
loading/error/retry/offline gate. Từ Phase 13, Workers cũng là section thật, cũng như **Users**, **Roles & permissions** và **Correction permissions** (Phase 13 — cấu hình; users sign in từ Phase 14 slice 1, và từ slice 2 mọi section Administration kiểm tra permission của mình; permission của Management được kiểm tra từ Phase 14 slice 3 còn permission của Scan Station từ slice 4; Users và Roles & permissions nói rõ điều đó, còn Correction permissions nêu correction permission nào đã được kiểm tra đến nay), cùng Department display settings, panel Due Soon warning của section Settings, History archival & purge cho retention period và statement Machine assignment (Phase 13); các Admin section
sau vẫn honest unavailable. Các run History archival & purge — archive export, verification, purge, scope/impact preview, reason, cùng data-size threshold và manual trigger — đến ở IMPLEMENTATION_ROADMAP Phase 16; cho đến lúc đó section nêu rằng chúng chưa khả dụng. **Scan behavior** và phần còn lại của general setting chưa có nội dung được định nghĩa (IMPLEMENTATION_ROADMAP `Deferred`): Scan behavior nêu rằng nó chưa khả dụng và setting của nó chưa được định nghĩa, entry action bị disable; section Settings nêu rằng các application setting khác chưa khả dụng. Cả hai không hứa phase nào. Workers
profile tách Users. Ghi chú triển khai (Phase 13): editor Workers lưu badge bằng chữ
in hoa và hiện preview `Saved as:` khi khác với giá trị đã gõ hoặc scan, upload avatar
(PNG, JPEG hoặc WebP; ảnh lớn được resize trước khi upload) hoặc xóa avatar, hiện
initials khi không có avatar, và deactivate Worker mà không có delete nào. Ghi chú triển khai (Phase 13): editor Users lưu tên, login name bằng chữ thường (kèm preview `Saved as:`), đúng một role và avatar tùy chọn, và deactivate user mà không có delete nào; section nêu rằng users sign in bằng login name và mật khẩu, rằng `Set password…` cấp mật khẩu cho user, và những permission nào đã được kiểm tra cho đến nay. Ghi chú triển khai (Phase 14 slice 1): với user administrator, bảng Users còn hiện trạng thái sign-in của từng user, và `Set password…` đặt mật khẩu tạm, kết thúc các sign-in của user và xóa khóa. **Roles & permissions** liệt kê các role có tên — ban đầu Administrator, Manager và Operator với đúng các capability PROJECT_PROFILE §20 liệt kê như initial grant — và sửa tên cùng permission của từng role, nhóm theo Administration, Production master data, Work Orders, priority and reports, và Scan Station; bốn correction permission (Undo recent eligible scans, Perform quantity corrections, Edit Work Order Allocation, Perform authorized historical corrections) chỉ được sửa trong Policies → **Correction permissions**, dưới dạng bảng role × permission dưới công tắc Undo reason. Từ Phase 14 slice 2, các section kiểm tra permission phía server (xem **Truy cập Administration** bên dưới); permission chưa cấp quyền gì được đánh dấu, còn action của Management được kiểm tra từ Phase 14 slice 3 và action của Scan Station từ Phase 14 slice 4.

**Truy cập Administration (post-v18, Phase 14).** Mọi section Administration cần một user đã sign-in: vào Administration khi chưa sign-in sẽ mở dialog sign-in, và một panel sign-in thay cho các section cho đến khi đó (Cancel rời khỏi); sign-in hết hạn mở lại dialog sign-in trên section đang mở (một lần lưu theme phát hiện nó đã kết thúc thì không mở dialog nào, §2.1: dialog mở ở request bị từ chối kế tiếp của section) và giữ công việc cho cùng user, còn user khác sign-in thì các section bắt đầu lại từ đầu. Khi password do administrator đặt vẫn còn phải thay, các section chờ sau dialog `Choose a new password` (công việc đang mở của cùng user được giữ). Mọi user đã sign-in được xem các section. Thay đổi ở mỗi section cần permission của nó (Departments, Areas, Operations, Workers, Scan Stations, Barcode configuration, Worker sessions, Correction permissions, Settings và Roles & permissions / Users đều nêu permission riêng), và thiếu permission thì section là view-only — control bị ẩn và một dòng `View only` nêu permission. Badge Worker chỉ hiện cho user được quản lý Workers. Đổi correction permission, permission quản lý chúng, hoặc user có role giữ chúng cần permission quản lý correction permission, và PartFlow từ chối thay đổi khiến không còn active user có mật khẩu nào được quản lý user và role, hoặc correction permission. Permission chưa cấp quyền gì được đánh dấu như vậy trong Roles & permissions, và role được đánh dấu **Applied at Scan Stations** (ban đầu là Operator) quyết định mỗi Scan Station đã enroll được làm gì. Chỗ nào tài liệu này nói "Admin-only" (History archival & purge) là nói permission cấu hình system settings, ban đầu cấp cho role Administrator (PROJECT_PROFILE §20). Ghi chú triển khai (Phase 14 slice 2): correction permission trong Policies → Correction permissions được sửa bằng permission quản lý correction permission; Edit Work Order Allocation được kiểm tra từ Phase 14 slice 3 (allocate stocked quantity từ Management và reverse allocation); Undo recent eligible scans áp dụng tại các Scan Station qua role được đánh dấu Applied at Scan Stations từ Phase 14 slice 4.

**Thiết bị Scan Station (post-v18, Phase 14 slice 4).** Production setup → **Scan Stations** liệt kê, cho mỗi station, một action `Devices…` mở các thiết bị đã enroll và các enrollment code chưa hết hạn của nó: tên, trạng thái (`Enrolled`, hoặc `Code issued` kèm thời điểm hết hạn) và last seen — request gần nhất của thiết bị, kể cả request bị từ chối. Thiết bị đã bị revoke hoặc code đã hết hạn không còn được liệt kê khi danh sách được tải lại. `Enroll device…` hỏi tên thiết bị và hiện enrollment code dùng một lần (hiệu lực 15 phút, dùng một lần, chỉ hiện một lần) để nhập trên thiết bị station (§4.13); `Re-enroll…` phát hành code thay thế một thiết bị đã enroll khi code được dùng, nên station không bao giờ tối đi ở giữa; `Revoke…` dừng thiết bị ngay lập tức (code đang chờ được hủy theo cách tương tự). Enroll cần permission quản lý Scan Stations và, khi role áp dụng tại Scan Stations giữ một correction permission, cần thêm permission quản lý correction permission; revoke chỉ cần permission đầu. Thiếu permission thì các control tương ứng bị ẩn và một ghi chú nêu điều còn thiếu.

Areas table trình bày Operations, derived assignment mode,
Machines, Worker mode, terminal/active. Active-quantity Area deactivation bị block. Từ Phase 13, bảng và editor Areas cấu hình Worker ID mode — Disabled, Fixed Worker kèm Fixed Worker của nó, hoặc Scanned session (chọn được từ Phase 13 cùng Worker session và badge confirmation); section Worker sessions là thật cho sliding inactivity timeout và các badge-confirmation option (Phase 13).

History maintenance: lossless export → verify → purge exactly archived rows qua
privileged Admin path, preserve related Movement chains, preview scope, reason và
audit; không purge-first. Từ Phase 13 section lưu **retention period** — **No retention period** (trạng thái ban đầu) hoặc một số tháng nguyên từ 12 đến 1200, đồng thời hiện period theo năm và tháng — như cấu hình có audit mà không production workflow nào đọc; lưu nó không archive hay xóa gì. Run archival và purge, data-size threshold và manual request đến ở IMPLEMENTATION_ROADMAP Phase 16; cho đến lúc đó section nêu rằng chúng chưa khả dụng và toàn bộ Movement history vẫn nằm trong database.

---

# 10. Completion / Receiving UI (Stockroom)

Reuse Scan Station. Sau `STOCKED`, allocation dialog gợi ý theo Hot → dated earliest
→ undated oldest received. Row có WO/requested/previous/remaining/proposed với +/-.
Operator adjust được; Confirm chỉ enabled khi allocated total bằng stocked quantity.
Routine không cần Manager; later Admin/Manager adjustment luôn audit.

**Implementation boundary (Phase 10):** production UI thật. Sau khi server xác
nhận write, dialog hiển thị suggestion của server cho đúng stocked quantity (mọi
outstanding line theo canonical ordering, +/− stepper và input clamp), luôn thấy
allocated total so với stocked quantity, `Confirm allocation` chỉ enabled khi bằng
nhau; confirmation gửi stocked quantity làm allocation quantity; server refuse →
dialog giữ mở với lý do và suggestion refresh; lost response → freeze line cho
same-intent retry; success chỉ sau server trả lời (nêu Work Order completed), rồi
inventory refresh và barcode input refocus. `Leave in stock — allocate later` zero
write. Manager adjustment sau này chỉ là API capability đến khi Phase 14
authorization (màn hình Management do Phase 14 slice 5 giao, §11.6). Admin và Manager có thể adjust allocation về sau, mọi thay đổi đều có audit (§11.6).

---

# 11. Work Orders

Management UI cho manual demand, file import (§11.7) + explicit release, không ERP customer/pricing/
invoice/shipping/accounting. Routes: active list `/management/work-orders`, completed
history `/management/work-orders/completed`; both keep Work Orders subnav active.
Details/New là modal trên list, URL không đổi. Native `<input type="date">`, ISO
internal. Phase 4 active workflows real API-backed; trang Completed Work Orders là
production UI thật từ Phase 10 (§11.5). Work Order chỉ rời active list qua
allocation-derived completion (Phase 10, §11.5 — mọi line fully allocated, dù do
allocation cuối hay do demand change): list tăng theo intake, giảm chỉ
theo completion.

## 11.1 WO list

Row: WO number, received/due, demand count/PN preview, Open/Released. Completed không
ở active list. Server-side number search + bound 100 newest; search before bound;
exact duplicate lookup whole history. Toolbar full-width: search, quiet Completed
link, secondary `Import from file…` (Phase 15 — điểm vào §11.7; ẩn, không disable, khi thiếu cả Create and edit Work Orders lẫn Edit Work Order Demand), primary New. Internal null WO hiển thị `—` + label. Whole-row opens details,
focus return khi close.

## 11.2 Work Order Details — modal với demand lines

Accessible modal/focus trap/stacked child dialogs. `Save demand` + `Cancel (Esc)`;
dirty close phải discard confirm. Header identity/meta, editable WO due chỉ Open.

Demand row fields PN, Request Type, qty, due, priority, Jobs, requester/reason/notes.
PN control trên demand line (đổi đích ở Phase 13): chính PN là control, mở dialog dùng chung `Edit Part Number` (§14.2) với icon bút chì (edit) sau PN (với user không được quản lý Part Numbers, PN vẫn là control nhưng không có icon bút chì và mở cùng dialog đó ở chế độ chỉ đọc — `Part Number details`, §14.2 — nên nhãn barcode vẫn dùng được); label in được mở từ trong dialog bằng `Barcode label…` (§14.3), và PN chưa có chi tiết đã lưu mở dialog dưới tên `New Part Number` với PN cố định. Control thay chữ PN tại chỗ (không thêm chiều cao row); draft line có PN chưa có master mang marker `new PN` inline, marker theo sự tồn tại mà dialog quan sát được. Mở dialog chỉ là presentation: không đụng draft, dirty state hay release — write của dialog chỉ liên quan chi tiết Part Number, không bao giờ liên quan demand — và vẫn dùng được trên released line. Trước Phase 13 control mở thẳng label dialog với glyph nhãn vì bút chì sẽ hứa hẹn việc sửa chưa tồn tại; Phase 13 slice 7 đã đổi đích. Link `Barcode label…` của Add Part không đổi. Open WO primary Add Part
manually, barcode optional. Duplicate PN focus existing line.

Released line: qty/due/Jobs editable, PN/Request Type read-only; qty below committed
inline error. Raising qty reopens remaining/release. Released WO không add/remove/
edit header scope, ngoại trừ các allocation action của user được adjust allocation (§11.6). Removal: draft immediate, saved-unreleased confirm, released
disabled với lý do. Due default chỉ propagate tới line còn giữ inherited default;
explicit no-date không inherit lại. User được đổi header Work Order nhưng không được đổi demand line (Create and edit Work Orders mà không có Edit Work Order Demand, Phase 14) đổi WO due date thì chỉ đổi WO due date: mọi line giữ nguyên due date, và dialog nói rõ điều đó cạnh field (`Line due dates stay unchanged — you may not edit demand lines.`). Saved line đang trên Hot list (không có released quantity và không có allocation, hiện tại hay đã reverse — line có allocation history không bao giờ remove được, nên nó nhận plain confirmation và lời từ chối của server) hiện cảnh báo nêu Hot rank (`🔥#n`) và chỉ remove sau khi gõ PN để xác nhận (dialog typed-confirmation dùng chung); removal cũng đưa nó khỏi Hot list và các Hot rank còn lại đóng khoảng trống (quyết định 2026-10-04).

PN lookup chỉ nói “new” sau exact server response; in-flight shows Searching.
Allocation của line đã lưu (Phase 14): mỗi line đã lưu hiện `Allocated a/b` (kèm `+n beyond demand` khi một correction được cấp quyền allocate vượt demand) và, với user được adjust allocation (Edit Work Order Allocation), `Allocate from stock…` và `Reverse…` trên Work Order Open, Released và Completed như nhau; cả hai mở dialog dùng chung của §11.6 và bị vô hiệu khi demand draft còn thay đổi chưa lưu. Qty edit bị giới hạn không đổi ngoài điều đó: Qty không đổi không bao giờ là lỗi (line được allocate vượt demand vẫn sửa được các field khác), và Qty bị đổi không bao giờ thấp hơn released hoặc allocated quantity.
Validation missing PN/non-positive/duplicate, due null valid; first invalid focused,
input preserved. Dirty state là actual diff và guard navigation/back/reload.

## 11.3 New Work Order — manual-first modal và Add Part

Accessible modal over list, focus restore, dirty discard confirmation. Header:
optional WO Number, today received, optional WO due. Blank number stores NULL/`—`;
existing active/completed number opens existing after protecting entered draft.

Add Part multi-step: PN lookup/create (trạng thái triển khai Phase 13 slice 7: search khớp PN và Name / Description đã lưu, mỗi kết quả hiện name đã lưu, hoặc barcode derive khi chưa lưu name) → positive quantity → optional due with
explicit no-date → optional NEW/MODIFY/Job/requester/reason/notes; Back preserves.
Barcode is secondary valid-PN method. Draft line returns to parent. Save requires
≥1 valid line; omission summary for blank WO/due/optional metadata makes consequences
clear before final save, never treats due as error. One transaction, server errors
in place.

## 11.4 Demand save so với production release

Save demand never creates quantity. Per line `Release to production…` separate:
remaining quantity (partial/repeated), Floating default or Planned template,
non-terminal starting Area/Operation, active PN distribution warning/confirmation,
structured summary and `RECEIVED` result. Dirty draft must save/discard/cancel first;
release uses server idempotency. Partly released line remains Open/action enabled;
fully released disables action; WO Released only all lines exhausted.

## 11.5 Completed Work Orders (post-v17)

Real deep-link read-only page. Giá trị trung tâm là `completed_at` (timestamp của
event làm line cuối fully allocated: allocation, hoặc save hạ Qty xuống bằng
allocated quantity / remove line cuối chưa fully allocated) hiển thị thành
**done date** theo múi giờ của site (một rule server-side `SITE_TIMEZONE` cho ngày
hiển thị, Done range và due outcome; không bao giờ theo ngày local của browser).
Bounded default date range, server search WO/PN/Job, done range, due outcome.
Done range preset (`Last 30 days` / `Last 90 days` mặc định / `This year` /
`Last year`) gửi lên server theo tên và được server neo vào ngày hiện tại của
site trên lịch `SITE_TIMEZONE` — browser gần nửa đêm hoặc qua ranh giới năm ở
múi giờ khác không làm lệch cửa sổ; `Custom…` gửi hai ngày site-calendar tường
minh (inclusive done date). Bảng WO Number | Done | Received | Due | Demand lines, không
Status column, không urgency ramp; Due cell có `✓ On time` / `✕ N ngày late` (done
date vs due date, verdict của server trên lịch site) / `—` khi không có due date.
Sort: WO Number/Done/Received/Due là server contract (column + direction đi cùng
query; row thiếu giá trị xếp cuối ở cả hai chiều, Work Order id là tie-breaker;
trang không tự sort lại row đã load), mặc định Done descending; unsorted state
của chu kỳ header quay về default đó, nên riêng header Done (descending chính là
default) chu kỳ là ascending ↔ descending — mọi click đều đổi order, cả hai chiều
của mọi cột đều chọn được. Paging: 50 row đầu theo order hiện tại, `Show more`
nối 50 tiếp qua opaque cursor của server gắn với sort đã phát hành; server chỉ
phát cursor khi thực sự còn row tiếp theo (history kết thúc đúng ranh giới trang
không hiện `Show more` thừa), và continuation của một preset Done range giữ
nguyên range đã resolve ở page đầu — site midnight đi qua giữa hai page không
neo lại query đã load; đổi search/filter/sort reset paging. Row opens read-only
details với done date và allocated quantity (demand vẫn read-only; user được adjust allocation giữ các allocation action của §11.2 — một reversal có thể reopen Work Order, §11.6). No New/demand edit/release — chỉ các allocation action của user được adjust allocation (§11.6); independent
toolbar. Active search/New exact number check can route here.

**Implementation boundary (Phase 10):** production UI thật trên
`GET /api/work-orders/completed` — search (debounced), Done range (preset theo
tên do server neo vào ngày hiện tại của site, hoặc inclusive done date của Custom),
due-outcome filter và sort column/direction là query parameter server-side; done date và due
outcome của mọi row là verdict của server; summary giữ loaded/matching count;
row mở read-only Work Order Details với `Done <date>` ở meta line và allocated
quantity từng demand line. Work Order chỉ complete khi server derive (mọi demand
line fully allocated từ stocked quantity), rời active list và không bao giờ bị
duplicate bởi New Work Order lookup (lookup mở completed details). Mock preview
dev-only cũ của trang này đã bỏ.

## 11.6 Dialog adjust allocation (post-v18, Phase 14)

Dialog dùng chung **`Adjust WO Allocation`** là bề mặt Management duy nhất để allocate stocked quantity để lại cho sau, để thực hiện correction beyond-demand được cấp quyền và để reverse một allocation (PROJECT_PROFILE §8.12 / §18). Chỉ user giữ Edit Work Order Allocation thấy nó; với mọi người khác nó ẩn — không bao giờ render vô hiệu hay vô tác dụng.

- **Điểm vào.** Tracking → Corrections → `Adjust WO Allocation…` (§7.2 mục 8; các Work Order open của PN đang chọn) và Work Order Details (§11.2; một line đã lưu, trên Work Order Open, Released và Completed — kể cả trên trang Completed Work Orders, §11.5, nơi một reversal có thể reopen Work Order). Tracking chỉ liệt kê open demand; Work Order đã hoàn tất được adjust từ Work Order Details của nó.
- **Các bước.** `Overview` (các demand line trong phạm vi với con số allocated / requested, `+n pcs beyond demand` khi áp dụng, và các active allocation, mỗi cái đánh dấu `beyond demand` nếu là correction), `Allocate from stock` (một demand line, không bao giờ vượt remaining demand hoặc available stocked quantity; ghi chú tùy chọn), `Allocate beyond demand` và `Reverse allocation`.
- **Correction beyond-demand.** Bước thứ hai tường minh, có cảnh báo rằng quantity vượt demand, quantity và reason bắt buộc; được ghi như một correction riêng với tên user đã sign-in và đảo ngược được như mọi allocation. Allocation thường ngày không bao giờ vượt remaining demand.
- **Reversal.** Reason bắt buộc; nó append history mới, không bao giờ sửa hay xóa allocation, và có thể làm Work Order đã hoàn tất thành chưa hoàn tất (Work Order reopen và done date của nó bị xóa; lần hoàn tất sau ghi một done date mới). Một correction hay reversal để Work Order vẫn hoàn tất không bao giờ dời done date của nó.
- **Actor.** Mỗi entry hiện user đã ghi nó bằng avatar và tên.
- **Kết quả chưa rõ và sign-in.** Một intent giữ một request identity qua mọi lần resubmit. Input và điều hướng bước bị khóa trong khi một submission đang chạy, và vẫn khóa khi server chưa trả lời, để lần submit kế tiếp chỉ có thể lặp lại đúng intent đó; việc server từ chối tường minh lần resubmit chứng minh chưa có gì được ghi và mở khóa input. Gián đoạn sign-in (phiên đã kết thúc) là request đã được trả lời — chưa có gì được ghi — nên nó giữ intent và request identity để resubmit sau khi sign in lại; nó không khóa cũng không mở khóa input. Đóng dialog khi kết quả chưa rõ sẽ reload host và nói rõ điều đó.
- **Offline.** Khi mất kết nối, các action submit bị chặn; không có gì được xếp hàng.

Được tham chiếu từ §7.2 mục 8, §7.3, §10, §11.2 và §11.5.

## 11.7 Import Work Orders từ file (Phase 15)

Dialog **`Import Work Orders`** tạo Work Order từ file CSV hoặc Excel đã chuẩn bị, hoặc thay đổi các Work Order Open và Released mà file liệt kê (PROJECT_PROFILE §13 *File import*). Nó chỉ lưu business demand — không có gì được release sang production — và hiện cho user giữ Create and edit Work Orders (`MANAGE_WORK_ORDERS`) hoặc Edit Work Order Demand (`EDIT_WORK_ORDER_DEMAND`); những người khác không thấy **`Import from file…`** (ẩn, không disable, §1.1 *Access to Management*). Import cần permission mà nội dung file đòi hỏi: Create and edit Work Orders cho các Work Order nó tạo, Edit Work Order Demand cho các Work Order đã có mà nó thay đổi.

- **Vị trí và luồng.** **`Import from file…`** nằm trong hàng toolbar §11.1, giữa `Completed Work Orders ›` và `＋ New Work Order`. Dialog (extra-wide) đi qua: chọn file `.csv` hoặc `.xlsx` → **Check file** → báo cáo theo từng Work Order → **Import** (nhãn theo *Changes to existing Work Orders*) → báo cáo kết quả. Check file là bắt buộc trước Import; import validate lại đúng file đó, và server từ chối file không phải file đã check (dialog khi đó đề nghị `Check file again`). Chọn file khác xóa báo cáo. Hai link template (`CSV`, `Excel`) tải template chỉ có header.
- **Help (một chỗ).** Cột bắt buộc Work Order Number, Part Number, Requested Quantity; tùy chọn Job Number và Due Date (`YYYY-MM-DD`); mỗi Part Number một dòng; format các cột Work Order Number, Part Number và Job Number là Text để giữ số 0 đầu. Chỉ đọc cột A–BL. File Excel: worksheet đầu tiên được đọc và phải visible, formula được đọc là giá trị lưu lần cuối trong Excel (formula chưa từng được tính được đọc là rỗng), và mọi dòng đều được import, kể cả dòng hidden hoặc filtered. Giới hạn: 1 MB, 2.000 dòng, 500 line mỗi Work Order, 200 ký tự mỗi text cell.
- **Báo cáo.** Một dòng tóm tắt `Will create {a} · Will change {u} · Already in PartFlow {b} · Not imported {c}` (sau import: `Created {a} · Changed {u} · …`), `Worksheet read: {name}` cho file Excel, số dòng đã đọc, số dòng trống bị bỏ qua và các cột bị bỏ qua. Bảng `WO Number | Rows | Lines | Result` liệt kê Work Order REFUSED trước; mỗi dòng mở ra các line của nó (`Row · PN · Qty · Due · Job`; ngày dạng `Jul 24, 2026`, ngày hoặc Job Number vắng là `—`) và, khi bị từ chối, từng lỗi dạng `Row {r} · {column} — {message}`. Nhãn kết quả: `Will be created` (kèm số Part Number mới), `Created`, `Will change — {k} changes` / `Changed — {k} changes` (xem *Changes to existing Work Orders*), `Already in PartFlow — nothing to change` (Work Order Open hoặc Released mà file để nguyên như đã lưu), `Already in PartFlow — not changed by this import` (Work Order completed, kèm `Differs from this file — this Work Order is completed and is never changed.` khi nó khác file; "For completed Work Orders, due dates and Job Numbers are not compared." hiện một lần dưới bảng, và chỉ khi có dòng như vậy; cùng nhãn này đánh dấu Work Order Number mà người khác đã tạo giữa lần check và lúc Import, mà import chưa hề so với file, kèm `Differs from this file — check the file again to see the changes.` khi nó khác file), và `Not imported — fix the rows listed`. Trạng thái không bao giờ chỉ dựa vào màu.
- **Câu nêu điều bị bỏ qua.** Mỗi preview sẽ tạo Work Order đều nêu `Imported Work Orders get no Work Order due date — they stay unscheduled.`; mọi preview nêu `{n} lines have no due date and sort after dated demand.` khi các line mà import ghi không có due date (preview chỉ-update cũng vậy; Due Date trống trên line đã có giữ ngày đã lưu và không được đếm). **Import** (kèm typed confirmation khi file thay đổi Work Order) là sự xác nhận các điều bỏ qua này (như §11.3).
- **Chặn.** Các dòng không có Work Order Number dùng được được liệt kê dưới `Rows without a usable Work Order Number` và làm Import bị disable; khi không có gì để tạo hoặc thay đổi thì hiện `Nothing to create or change.`
- **Offline và đang chạy.** Khi mất kết nối, Check file và Import bị disable (`Reconnect to check or import the file.`; không có gì được xếp hàng). Trong lúc import, dialog không thể đóng hay cancel, hiện `Importing… Keep this page open.`, và rời trang sẽ hỏi xác nhận. Kết quả bị mất hiện `The import may be partly saved. Check the file again: Work Orders already in PartFlow are never duplicated, and changes already saved are not listed again.` và không bao giờ tự động retry. Khi page outdated (§3 rule 13), Check file và Import bị disable với `PartFlow was updated — reload the page to continue.`; một import đang chạy không bao giờ bị reload ngắt (Work Orders chỉ reload thủ công, sau leave guard hiện có).
- **Focus và viewport hẹp.** Focus ban đầu ở file control; báo cáo mới chuyển focus tới heading của nó; lỗi được thông báo dạng alert; đóng dialog trả focus về **`Import from file…`**. Ở viewport hẹp, bảng thành các card có nhãn với footer sticky.
- **Changes to existing Work Orders (Phase 15 slice 2).** Với Work Order Number đã có trong PartFlow, file thay đổi quantity, đặt due date, thêm Job Number và thêm line vào Work Order Open; ô trống và line không có trong file giữ nguyên giá trị đã lưu, và import không bao giờ xóa line (Help nói điều này trong một câu). Dòng `Will change` / `Changed` mở ra **`Show changes`**: một dòng cho mỗi thay đổi — `Row {r} · {PN} · Qty {a} → {b} · Due {a|—} → {b} · Job Numbers {a|—} → {b}` cho line được sửa (chỉ các phần đã đổi, thêm ` · leaves the Hot list` khi một Hot line đã allocate đủ bị hạ quantity), `Row {r} · Add {PN} · Qty {q} · Due … · Job … · new Part Number` cho line được thêm — rồi `Completes the Work Order — every line becomes fully allocated.` khi các thay đổi làm nó completed, và `Kept, not in this file: {PNs}` cho các line đã lưu mà file không liệt kê (dòng kept này cũng hiện trên dòng `Already in PartFlow — nothing to change`). Line được thêm vào Work Order Released, hoặc quantity thấp hơn quantity released hay allocated, làm Work Order đó `Not imported` kèm lỗi theo dòng. **Import** có ít nhất một thay đổi sẽ mở typed confirmation xếp chồng (pattern §11.2): title `Change {m} existing Work Order(s)?`, nội dung `These Work Orders are already in PartFlow. Import applies every change below; each Work Order is saved on its own.` (kèm `It also creates {a} new Work Order(s).`), một vùng cuộn liệt kê **mọi** thay đổi theo từng Work Order dưới `WO {number} · {Open|Released}`, giá trị phải gõ `CHANGE {m}` (nhãn `Work Orders to change`) và nút `Import and change {m} Work Order(s)`; Cancel hoặc Esc không gửi gì. Typed confirmation gắn với các thay đổi đã hiển thị, không phải mọi lần sửa sau đó của Work Order: khi các thay đổi đã hiển thị không còn khớp lúc Import, mọi Work Order cần thay đổi là `Not imported`, và Work Order thay đổi sau khi Import check lại nó là `Not imported`, mỗi Work Order kèm lý do; thay đổi của người khác mà để nguyên các thay đổi đã hiển thị thì không chặn chúng. Khi có Work Order không được thay đổi, một dòng phía trên bảng nói `Some Work Orders were not changed because they changed after the check. Check the file again to see and confirm the current changes.`, và **Check file again** giữ file và đòi typed confirmation mới; các Work Order cần tạo vẫn được import. Nhãn nút: `Import {n} Work Order(s)` khi chỉ tạo, `Change {m} Work Order(s)…` khi chỉ thay đổi, `Create {n} Work Order(s), change {m} Work Order(s)…` khi cả hai. Khi User đang đăng nhập thiếu permission mà file cần, Import bị disable kèm một dòng nhẹ cho mỗi key thiếu (`Creating Work Orders needs the "Create and edit Work Orders" permission.` / `Changing existing Work Orders needs the "Edit Work Order Demand" permission.`); Check file vẫn dùng được cho người giữ một trong hai key. Khi Import bị từ chối vì file giờ cần một permission mà lần check không yêu cầu (ví dụ một số vừa được tạo sau lần check giờ thành thay đổi), không có gì được ghi, báo cáo bị bỏ, alert hiện `Nothing was imported: this file now needs a permission your account does not have. Check the file again to see what it needs.`, và **Check file again** giữ file.

---

# 12. Machines (Management)

Permission-based view cho monitoring/lifecycle/maintenance/asset, không CMMS.

## 12.1 Active Machines table

Search + New; columns Machine | State | Assigned now | Asset | Maintenance; no
Actions. Sort header cycles asc/desc/none, stable, `aria-sort`. State derive
Maintenance > Running if assigned > Idle, elapsed shared clock. Assigned now liệt
kê các phần theo PN của server (`<PN> · <n> pcs`, mỗi PN một dòng — Phase 11,
cùng projection với tổng; `—` khi không có gì) với quantity mang semantic tone;
asset metadata content-sized. Per-row accessible On/Off switch chỉ
mở start/clear dialog và cập nhật sau confirm. Whole row opens Edit; switch stops
propagation.

## 12.2 Maintenance

May start with assigned quantity; nothing moves/releases/completes. Optional note/
return date. Edit in place không đổi start/state. Clear → Running nếu still assigned,
else Idle; confirmation names result.

## 12.3 New / Edit Machine dialog

Read-only identity header Asset Tag + barcode, existing Area fixed + label print.
Label Code128 black-on-white, display name primary, tag/value secondary, print only
label. Name required/unique active per Area với live inline feedback. New form chọn
Area; existing Area chỉ đổi qua retire/reactivate. Optional manufacturer/model/
serial/install/notes.

New staged: Continue → summary → final attention question → Add. Dirty-state choices
preserve input; focus name on New. Lifecycle timeline append-only Retired/Reactivated, mỗi event hiện user đã ghi nó (avatar và tên; event ghi trước khi có sign-in hiện đúng những gì đã ghi).
Existing Danger Zone `Retire…`, blocked when assigned quantity. Replacement guide:
retire old + new record/tag; name reuse allowed.

## 12.4 Retirement, reactivation và replacement

Retire with unsaved edits records Save/Discard/Cancel choice nhưng chỉ apply khi
flow completes; typed Asset Tag gate → summary → final danger question. On confirm
apply edit decision, append RETIRED, set date; never hard-delete.

Retired table sortable/read-only, whole row opens details; Reactivate chỉ trong
details. Reactivate same physical machine: identity header, hard blockers identity/
serial reissue, editable name + Return Area, required reason + checkbox
`This is the same physical machine returning to service — not a replacement.`,
inline validation, summary, final warning question. Confirm clear retirement/
maintenance, reset state time, append REACTIVATED, return Idle. Different physical
Machine luôn new record/tag.

## 12.5 States và implementation boundary

Standard loading/error/empty. Phase 3.5 `/api/machines` real persisted create/edit/
maintenance/retire/reactivate + lifecycle atomic. Stale next-tag precondition reject
without consuming. Phase 6 server derives assigned total/state and retirement block;
shared `machine-state.ts` reused across monitoring.

---

# 13. Planned Routes (Management)

Reusable RouteTemplate definitions; actual Movement không bị rewrite.

## 13.1 Route template list

Search + New; separate Active table (Route, Steps, Status, Used by) và Archived
(plus Duplicate). Area-colored step chips, arrows siblings. Usage dialog lists Flow/
PN/released date/snapshot. Active whole-row edit; usage stops propagation; archive/
delete/duplicate in dialog. Archived row only Duplicate. Với user không được quản lý Planned Routes, row active không kích hoạt được, `+ New Planned Route` và `Duplicate` của row archived vắng mặt, còn `Used by` vẫn có.

## 13.2 Create / Edit dialog

Required name, description, ordered steps: Area, scoped Operation, duration,
stable-id preferred active Machine, instruction. Drag + Up/Down, never drag-only;
add/remove, ≥1 step. Unavailable stored Operation/Machine remains explicit, không
silent-clear. Area change revalidates selections. Dirty guard. Used-template note:
changes future only; existing snapshot untouched. Duplicate handles unsaved choice
and creates active variant draft.

Row step dùng chung (Phase 14 slice 6): row step, validation và đánh số của chúng là một component dùng chung với `Edit assigned Route…` của Tracking (§7.2 mục 8), cũng dùng marker `● Unsaved changes` và guard `Discard unsaved route changes?` của mục này.

## 13.3 Archive so với delete

Never-used delete bằng plain confirm. Ever-used archive: protect unsaved choice,
typed exact route name, explain future unavailability/snapshots/history. Archived
không selectable. Không version system riêng.

## 13.4 States và implementation boundary

Standard loading/error/empty. **Implementation boundary (Phase 13).** View là
view thật trên surface `/api/route-templates`: create, edit (toàn bộ step set),
Duplicate (server create `{name} (variant)`), Archive (route đã từng dùng) và
Delete (route chưa từng dùng), cùng usage dialog (200 Quantity Flow đã release
mới nhất kèm total); write bị chặn khi mất kết nối. Est. time được nhập và hiển
thị bằng duration token dùng chung (`45m`, `4h 00m`, `2d 03h`); Operation hoặc
Machine đã lưu mà không còn được cung cấp thì render `(unavailable)` và phải
thay trước khi lưu (§13.2). Các preview `?state=` chỉ dành cho development vẫn
còn.

---

# 14. Part Numbers (Management) (post-v18)

Optional current PN details, không gate production. Plain user copy, không domain
jargon. Exact description: `Manage optional Part Number details, images, ERP IDs, and barcode labels.`

## 14.1 Part Number list

Search + New; columns Image | Part Number | Name / Description | Revision | ERP ID |
Barcode. Một shared default image. Canonical UPPERCASE mono PN; absent `—`; barcode
derive muted mono, không badge/editable. Whole row opens Edit. Không page-level
deletion note; consequences đặt cạnh action.

## 14.2 New / Edit dialog

User không được quản lý Part Numbers mở cùng dialog này ở chế độ chỉ đọc, từ danh sách Part Numbers và từ PN control của demand line Work Order (§11.2): tiêu đề `Part Number details`, giá trị dạng text (`—` khi vắng), ảnh không có control, giữ `Barcode label…`, không có `Save` và không có section `Delete Part Number Details`, và `Close (Esc)`.

Edit có read-only PN/barcode header + `Barcode label…`; New nhận required PN và
canonicalize. Exact validation copy: internal whitespace error; existing saved
details; valid `✓ Will be saved as {PN} · Barcode {barcode}`. Optional description/
revision/ERP/image; remove image về default. Dirty guard.

Danger Zone `Delete Part Number Details`: plain one-step attention danger confirm,
không typed gate; delete chỉ saved details/image/revision/ERP, không production/
Work Order history, có thể create lại; no archive/soft-delete/active lifecycle.

## 14.3 PN barcode label

Shared Code128 `PF:PN:<part-number>` on white/black label, PN primary dưới bars,
full scanned value muted. `Print Label` chỉ print label; same dialog/encoder được mở
từ Part Numbers (§14.2) và dialog `Edit Part Number` trên demand line (§11.2) qua `Barcode label…`, còn Add Part (§11.2/§11.3) mở trực tiếp. Không barcode config tại đây.

## 14.4 States và implementation boundary

Standard loading/error/empty. **Implementation boundary (Phase 13).** View thật trên `/api/part-numbers`: tìm kiếm phía server có giới hạn (`Showing N of T Part Numbers`, `Show more`, tối đa 200 row), tạo, sửa, upload và gỡ ảnh, hard delete, write bị chặn khi mất kết nối; các preview `?state=` chỉ-development vẫn còn. Dòng bounded-list là chỉ báo trạng thái list (pattern PN Tracking §7.1), không phải page-level note mà §14.1 loại trừ.

---

# 15. Thay đổi từ các version trước

Các entry lịch sử giữ vocabulary cũ khi cần. Chúng không override v18: từ v6 dùng
Work Order; từ v8 bỏ REWORK/temp WO/Machine Session/Action barcode/Recent Scans;
v18 dùng canonical uppercase PN + optional hard-deletable master; post-v18 Worker
session không còn shift end.

## 15.1 Từ GUI Design v17

- Align PN model: trim → reject internal whitespace → uppercase; bỏ preserved case.
- Master là optional metadata; Tracking giữ PN/history khi master deleted; bỏ
  archived/inactive PN.
- Part Numbers Management view mới, shared image/barcode label, deletion semantics;
  copy pass user-facing và danger confirmation.
- Machine copy pass dùng production language, lifecycle history phrasing.
- Whole-row focus ring removed trên Machines/Part Numbers/Routes/WO; Tracking giữ.
- Outside click đóng Tracking panel.
- Resolve all prior open questions: unlimited session ranking history; theme User →
  Station → Dark; deterministic Hot scan; Worker≠User/existing badge/sliding timeout;
  Board Department-wide; per-Department rotation config.
- Phone vertical-scroll-first, one-row Management subnav, content-derived table
  collapse, mobile Area detail carousel, Board scale/swipe.
- Worker countdown/header fit, Board `Production · ● Live`, direction-aware page
  transition, final gates DONE/QUEUE/Undo, Receive settings focus exception.
- Additional same-round refinements preserve wizard Back path, notification, shared
  PN rows, badge attention tone and responsive behaviors.
- Area Board chạy trên read thật của Department (§6, §6.1, §6.3): hai mode dùng
  chung một read, cùng model monitoring và cùng component với Scan Station nên
  không drift. **All Areas overview theo PN** — một row mỗi Part Number trong một
  Area, đếm một lần, các quantity riêng biệt gộp vào chip portion, không mất
  không trùng — còn **per-Area detail theo từng quantity**. Hot rank, due
  countdown, WO Number và Job Numbers của row lấy từ **OPEN demand của PN** theo
  canonical order — trên CẢ HAI surface, kể cả `In this Area now` của Scan
  Station: demand quyết định đứng tên row, các demand còn lại nêu `+N more` kèm
  tooltip đầy đủ và đều tìm được bằng search, Work Order đã complete không cấp
  gì (`WO — · —`, `No due date`), còn demand nguồn gốc ở lại trên quantity làm
  **provenance chỉ cho workflow và audit** — dialog thao tác và recap của Scan
  Station nêu nó, không bao giờ là row monitoring. Search và sort của overview
  làm việc trên row PN đã gộp: search khớp mọi open demand của PN, và `Quantity`
  so tổng của PN trong Area chứ không so một quantity. Quantity finished giữ Machine hoàn thành kể cả sau khi Machine
  đó retired; Stockroom hiện stocked kèm `allocated a/n`. Toolbar thêm **feed
  status** `● Live` / `Feed stale — reconnecting` như Production Board;
  `Total PNs` / `PNs` đếm Part Number chứ không đếm row, và sort `Priority` xếp
  mọi Hot rank trước mọi row không rank.
- PN Tracking chạy trên read model thật (§7, §7.1, §7.3; IMPLEMENTATION_ROADMAP
  Phase 11 — ranh giới triển khai, cộng ba bổ sung presentation): PN Tracking
  thành production UI thật trên `GET /api/tracking` và `GET /api/tracking/detail`,
  thay mock dataset Phase 2; overlay detail modeless v14 và whole-row selection
  giữ nguyên. Mọi giá trị đều là giá trị các monitoring view khác đã derive:
  current quantity theo Area / Machine qua derivation branch-aware của các board,
  open demand context theo canonical order, stocked và scrapped quantity từ
  effective history, PLANNED snapshot judged từ last known step của flow kèm mọi
  deviation đã confirm, và FLOATING actual trace derive từ Movement history —
  giữ repeated Area, `⟲ REPAIR` cho Repair return, split child kế thừa trace của
  nguồn tới điểm split. Movement history giữ original đã undo hiển thị với badge
  `REVERSED` rõ ràng bên cạnh row `REVERSED` đã undo nó. **Bổ sung presentation:**
  (a) **feed status** cạnh title và ghi chú stale dưới PN đang chọn — cùng câu
  `● Live` / `Feed stale — reconnecting` như các board; (b) **status pill thêm
  `Open`** cho PN mà open Work Order Demand không có quantity trong production
  và không còn stock chưa allocate (chưa release, scrap hết, release đã undo,
  hoặc mọi stocked piece đã allocate cho work trước — `Stocked` nghĩa là stock
  CÒN available cho open demand) — không giá trị nào trong ba giá trị đã duyệt
  mô tả trung thực trạng thái đó, nên status filter của list cũng có `Open`; (c)
  **long data có bound** — `Showing n of m PNs` với `Show more` trên list, và
  `Showing n of m …` với `Show older …` trên Movement history (thời gian ngược
  theo timestamp), Scrap history (chính các event SCRAPPED, event đã undo đánh
  dấu `REVERSED`), các Quantity Flow (mỗi lần một page có bound, mới nhất trước
  theo flow id bất biến) và allocation entry — page đã nối được đọc lại khi refresh
  đổi nội dung chúng hiển thị, nên detail live không bao giờ giữ row cũ, trùng
  hay thiếu. Section Corrections vẫn ẩn cho đến khi có authorized corrections
  (Phase 14), các field metadata từ master render `—` cho đến Phase 13. Mockup
  v18 không đổi (feed state, paging control và pill `Open` chỉ có trong
  application).
23. **Expected-duration monitoring trên derivation thật** (§4.10, §5, §6, §7.2;
  PROJECT_PROFILE §17 "Expected duration hiệu lực của một position";
  IMPLEMENTATION_ROADMAP Phase 11 — khép mục audit Phase 11 để mở): dwell theo vị
  trí của Production Board chuyển `long` khi clock chung vượt thời điểm
  `expected_by` cố định của server — thời điểm sớm nhất mà một portion của location
  vượt expected duration hiệu lực của chính nó (snapshot của Assigned Route Step
  hiện tại, nếu không thì Operation default) — thay stand-in `≥ 3 ngày` của Phase
  2, bỏ hẳn không có rule cố định thay thế; `Time in Area` của shared PN row (Scan
  Station và Area Board detail như nhau, row overview warning từ portion sớm nhất
  của PN) và dòng position của flow trên Tracking (`· exceeds expected duration`)
  mang cùng đánh giá advisory, mỗi chỗ có chữ hoặc tooltip bên cạnh tone — không
  bao giờ chỉ màu — và không có expected duration thì không cờ. Mockup v18 không
  đổi (highlight đọc dữ liệu của application).
24. **Automatic removal khỏi Hot list và remove Hot line bằng typed confirmation**
  (§8, §11.2; PROJECT_PROFILE v22 §13, §21 Priority Management mục 11;
  IMPLEMENTATION_ROADMAP Phase 12 — quyết định owner 2026-10-04): entry có line trở
  thành allocate đủ (gồm cả Work Order completed) rời Hot list trong cùng transaction
  với allocation hoặc demand edit gây ra nó, không confirmation và các rank còn lại
  đóng khoảng trống (§8 *Automatic removal*; footer nói rõ điều này); saved Work Order
  Demand line đang trên Hot list chỉ được remove từ Work Order Details sau cảnh báo
  nêu rank `🔥#n` và typed confirmation PN, việc này cũng đưa nó khỏi Hot list (§11.2
  *Removing demand lines*).
25. **Field Undo reason và công tắc policy của nó** (§4.5, §9; PROJECT_PROFILE §16
  "require a reason when configured"; quyết định owner OD-6 ngày 2026-10-04 — bổ
  sung hành vi, không tăng version): khi option **Undo reason** của Administration →
  Correction permissions đang On (toàn cục, default Off), summary Undo hiện field
  `Reason` bắt buộc trước final gate, `Confirm reversal` bị disable đến khi có chữ,
  gate nhắc lại reason, và reversal bị server từ chối vì thiếu reason không ghi gì và
  giữ mọi selection; khi option Off summary Undo không đổi. Administration → Correction
  permissions thật cho riêng công tắc này và nói rõ role-based correction permission
  chưa cấu hình được.
26. **Department display settings và Due Soon policy trở thành cấu hình thật** (§3.12,
  §5, §9; IMPLEMENTATION_ROADMAP Phase 13; owner default OD-5 — bổ sung hành vi,
  không tăng version): Production Board rotation timing được cấu hình theo
  Department (seconds per displayed row 1–60, minimum page dwell 1–300 s; default
  3 s và 6 s) trong Administration → Department display settings và đi cùng board feed,
  áp dụng ở lần refresh kế tiếp của board; Due Soon window là một policy server toàn
  cục sửa ở Administration → Settings → Due Soon warning (Minimum warning days 0–365,
  Lead-time warning percentage 1–100, Maximum warning days 0–365 và không bao giờ dưới
  minimum; giá trị ban đầu 2 ngày, 15 %, 7 ngày), tính bằng số học số nguyên chính xác
  từ khoảng received → due, và được tải như một phần ready state của Production Board,
  Area Board, Scan Station, Priority và Work Orders; Scan Station giữ mọi production
  action khi không tải được policy và chỉ giữ lại phán đoán `soon` sau một thông báo
  tường minh. Các default frontend `DEFAULT_DUE_SOON_POLICY`, `ROTATE_MS_PER_ROW` và
  `ROTATE_MS_MIN` đã bị gỡ.
27. **Setting retention period, statement Machine assignment và các section chưa định nghĩa trung thực** (§9; PROJECT_PROFILE §8.4, §12, §28; IMPLEMENTATION_ROADMAP Phase 13; owner default OD-1 và OD-18 — status text cộng một settings form, không tăng version): Administration → History archival & purge lưu retention period của Movement history (`No retention period`, hoặc `Keep a set period of history` với số tháng nguyên 12–1200 và một dòng quy đổi trung tính `Retention period: 10 years.`), lưu không cần xác nhận vì nó không thực thi gì, và nêu rằng run archival và purge chưa khả dụng và không có gì bị archive hay purge; Administration → Machine assignment trở thành statement chỉ-đọc hai hàng về các mode ownership của Area, không có entry action, và subtitle của nó được làm rõ — mode theo việc Area có Machine nào hay không (một Machine hoạt động như nhiều Machine), không bao giờ là setting theo Area; Scan behavior nêu rằng nó chưa khả dụng và setting của nó chưa được định nghĩa, không hứa phase, entry action vẫn bị disable.
28. **Users, role và correction permission** (§9, §2.1; PROJECT_PROFILE §7, §16, §20; IMPLEMENTATION_ROADMAP Phase 13; owner decision OD-8, OD-19 và OD-P8/P9 — các màn hình cấu hình, không tăng version): Administration → Users là bảng cộng editor cho application account (tên, login name lưu bằng chữ thường kèm preview `Saved as:`, đúng một role, avatar tùy chọn, active status; deactivate, không bao giờ delete) và nêu rằng users chưa đăng nhập được; Roles & permissions là bảng cộng editor cho các role có tên và permission của chúng theo bốn nhóm, với Administrator, Manager và Operator là các role ban đầu; Policies → Correction permissions có thêm bảng role × correction-permission dưới công tắc Undo reason, là editor duy nhất của bốn correction permission; preference theme của User được lưu (§2.1) không có màn hình. Mọi section đều nêu rằng chưa có gì được enforce trước khi sign-in tồn tại, và placeholder nay chỉ phục vụ Scan behavior.
29. **Sign-in cho application User** (§1.2, §9, §2.1; quyết định owner OD-P1–OD-P5, 2026-10-06 — không tăng version): §1.2 mới mô tả account chip ở top navigation (`Sign in` / `Set up PartFlow`, hoặc avatar và tên cùng `Change password…` và `Sign out`; không có ở nơi navigation bị ẩn), sign-in modal phủ lên view hiện tại, dialog `Choose a new password` không đóng được, dialog first-run `Set up PartFlow` với setup token, và các quy tắc Scan Station và Production Board không bao giờ hỏi sign-in còn control không khả dụng thì ẩn, không disable; Administration → Settings có thêm panel **User sign-in** (thời hạn theo ngày hoặc không bao giờ, số lần sign-in sai trước khi khóa, thời gian khóa, ép thay mật khẩu do administrator đặt) bên cạnh Due Soon warning; Users có thêm `Set password…` và cột `Sign-in` cho user administrator, và các ghi chú Users và Roles nay nêu rằng users sign in và permission nào đã được kiểm tra, còn ghi chú Correction permissions vẫn nêu rằng các permission của nó chưa được enforce; User theme tier vẫn được lên kế hoạch ở Phase 14 slice 8. Phase 14 slice 2 mở rộng mục này (không bump version): truy cập Administration và các section nhận biết permission (§1.2, §9; quyết định owner OD-P7, OD-P19). Phase 14 slice 3 lại mở rộng mục này (không bump version): truy cập Management và các Management view nhận biết permission (§1.1, §1.2, §8, §9, §11.2, §12.3, §13.1, §14.2; quyết định owner OD-P7, OD-P10, OD-P17).

30. **Enroll thiết bị Scan Station** (§4.1, §4.5, §4.13, §9; quyết định owner OD-P6, OD-S4-1, OD-S4-9, 2026-10-06 — không bump version): cả hai mode của Scan Station yêu cầu thiết bị đã enroll (§4.1); `⟲ UNDO` không hiển thị khi role áp dụng tại Scan Stations không cấp nó (§4.5); §4.13 mới mô tả màn hình và dialog enroll cùng các action mà station ẩn; Administration → Scan Stations có thêm dialog `Devices…` và Roles & permissions đánh dấu role áp dụng tại Scan Stations (§9).

31. **Management allocation adjustment và correction beyond-demand** (§4.11, §7.2, §7.3, §10, §11.2, §11.5, §11.6; quyết định owner OD-P10, OD-P12/P13, OD-S5-3, 2026-10-06 — không tăng version): Work Order Details cung cấp `Allocate from stock…` và `Reverse…` trên mọi line đã lưu (Work Order Open, Released và Completed) và section Corrections của Tracking cung cấp `Adjust WO Allocation…`, cả hai mở dialog dùng chung của §11.6 với bước thứ hai beyond-demand tường minh; line được allocate vượt demand hiện `(+n beyond demand)` và allocation progress tính riêng phần đó; Qty edit bị giới hạn không bao giờ xét Qty không đổi.

32. **AssignedRoute adjustment** (§7.2, §7.3, §13.2; quyết định owner OD-P11, 2026-10-06 — không tăng version): section Corrections của Tracking cung cấp `Edit assigned Route…`, mở dialog chỉ thay future step của route riêng của một Planned Quantity Flow, với reason bắt buộc và bước review; step mà quantity đã tới hoặc history ghi nhận vẫn bị khóa; block flow hiện `(snapshot, adjusted)` và một ghi chú `Route adjusted …` cho mỗi adjustment; row step của Planned Routes được dùng chung với dialog.

33. **PN audit trail và Change priority** (§7.2 mục 8, §7.3, §7.4, §8; quyết định owner OD-P15, OD-P18, 2026-10-06 — không tăng version): section Corrections của Tracking có thêm `Change priority`, link vào Priority để highlight Hot entry của PN hoặc mở dialog Add liệt kê đúng các demand đủ điều kiện của PN, và `View audit trail`, dialog chỉ-đọc, có phân trang, liệt kê các thay đổi đã ghi của PN (chi tiết Part Number, Work Order và dòng demand, thay đổi Hot rank, entry và reversal Management allocation, route adjustment); section Corrections hiện cho mọi user được mở Tracking.
34. **Theme User tier** (§2.1; quyết định owner OD-P16, 2026-10-06; IMPLEMENTATION_ROADMAP Phase 14 slice 8 — bổ sung hành vi, không tăng version): preference theme của User đang đăng nhập là thật và có ưu tiên hơn preference của Scan Station; khi đã đăng nhập, toggle lưu preference của User trên mọi route, và chỉ lưu của Scan Station khi không ai đăng nhập; đăng xuất quay về preference của Scan Station hoặc Dark; một save không được xác nhận hiện cảnh báo (notice nổi của station trên route Scan Station); thay đổi theme không được audit.

35. **Update notice khi release không khớp và phục hồi khi tải view lỗi** (§3 rule 13, §4.1, §4.11, §4.12, §5, §5.1, §11.7; quyết định owner OD-16-07 và OD-16-18 (default khuyến nghị, owner chưa trả lời); IMPLEMENTATION_ROADMAP Phase 16 slice 3 — bổ sung hành vi, không tăng version): page có release khác server hiện notice `UPDATED` persistent kèm `Reload page` thay cho OFFLINE banner và bị write-block như page mất kết nối; page không bao giờ tự reload khi có dialog mở, tự reload trên route Scan Station và Production Board sau 60 s khi không có dialog mở, và các modal không đóng được (Worker sign-in / expired, enrollment, `Choose a new password`) có `Reload page` riêng; view mà code không tải được đưa `Reload page`.

## 15.2 Từ GUI Design v16

- Worker/session and completion refinements, shared UI clock, route chip consistency,
  production copy guard and focus behavior.
- Completed Work Orders route/history presentation and Machine lifecycle timeline
  were aligned with authoritative model.

## 15.3 Từ GUI Design v15

- Scanner focus hardening, content-measured layouts, confirmation summaries, row
  activation, typed destructive flows, dynamic Board timing and responsive polish.
- Historical Worker shift-window wording was later superseded by sliding timeout.

## 15.4 Từ GUI Design v14

- Touch capability detection, summary verification layout, measured Board tracks,
  modeless Tracking overlay, Machines moved to Management and lifecycle dialog polish.

## 15.5 Từ GUI Design v13

- Professional copy classification/guard, responsive Work Orders, outside mock
  boundary honesty, row/grid refinements and shared PageNote/DevNotice patterns.

## 15.6 Từ GUI Design v12

- Structured confirmation hierarchy/semantic emphasis, shared time derivation,
  Priority snapshot layout, Production Board content sizing/legend/footer refinements.

## 15.7 Từ GUI Design v11

- Board Hot flame in No. column, manual pagination, explicit location presentation,
  Scrap display; Priority Current/New snapshots and Machine/Area visual distinctions.

## 15.8 Từ GUI Design v10

- Work Order details became modal over list, Machine/Area states and Movement/DONE
  presentation became explicit, with shared operator copy and audit distinctions.

## 15.9 Từ GUI Design v9

- Earlier PN archive/preserved-case rules and route/processing refinements. PN
  archive/soft-delete is historical and superseded by v18 hard-deletable metadata.

## 15.10 Từ GUI Design v8

- Scan notifications float; shared PN row grid/action rail; action visibility was
  tightened; direct-processing, partial quantity and one-shot dialog polish.

## 15.11 Từ GUI Design v7

- PN-centric one-shot Scan Station; no Machine Session/Action barcode/Recent Scans.
- Station selector routes, NEW/MODIFY + Repair movement intent, Floating default,
  null WO numbers, PN-in-barcode/create-on-first-use, Scrap/addition, two Area modes,
  shared monitoring layout, fixed quantity keypad, Admin retention specification.

## 15.12 Từ GUI Design v6

- Fast connectivity + server-confirmed write, nullable due dates/canonical ordering.
- Manual-first Work Orders, optional identifiers, Station/Board/Area/Priority UI
  hardening. Historical TMP number rule was later removed.

## 15.13 Từ GUI Design v5

- New Work Order modal, Add Part, Demand removal, native date, validation/dirty
  guard, production mock boundary, realistic identifiers.
- Canonical vocabulary migrated Purchase Order → Work Order; no legacy route.

## 15.14 Từ GUI Design v4

- Global switchable Dark/Light semantic tokens; Area Board truncation/quantity
  anchoring. Theme persistence later resolved.

## 15.15 Từ GUI Design v3

- Manager Summary merged into All Areas; Area Board monitoring context; Purchase
  Orders list/detail/new structure and WO-level due default. Fixed per-view theme
  decisions here were later superseded by global mode.

## 15.16 Từ GUI Design v2

- Management subview grouping, reduced top nav, removal of “Shop floor” group,
  profile alignment for allocation/Hot criteria.

## 15.17 Từ GUI Design v1

- Historical v2 decisions included Machine session, old PO terminology and Hot
  Demand ranking; later versions superseded Machine session and vocabulary while
  retaining audited Priority workflow, Board/Operations/Tracking improvements.

---

# 16. Ngoài phạm vi Phase 2 UI

Localization framework của application UI (app hiện dùng English vocabulary),
charts/analytics dashboards và administrative command barcodes. Phone-width layout
không còn deferred; đã thuộc §2.5.

---

# 17. Open Questions

Không còn. Bảy câu hỏi trước đã được chốt và folded vào §2.1, §4.3, §4.5, §4.12,
§5, §8, §9; tóm tắt ở §15.1.
