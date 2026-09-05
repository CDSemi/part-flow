# Thiết kế GUI PartFlow v18

> **Bản gốc chuẩn:** [`GUI_DESIGN.md`](GUI_DESIGN.md).
> Baseline upstream: commit `f96bf09` (Production Board — merged quantity theo mọi nhánh lineage).
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
6. Work Orders.
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

Management mở subview dùng gần nhất trong session, mặc định Area Board/All Areas.
Trong subnav, Part Numbers kế cuối và Machines cuối. Grouping chỉ là navigation;
production master data Machines/Routes/Part Numbers vẫn permission-based trong
Management, không phải Administration.

---

# 2. Design System

## 2.1 Một token set, hai theme chuyển đổi được

Toàn app có global Dark/Light toggle; mọi view/dialog/toast theo cùng mode. Dark
mặc định cho shop floor. Component chỉ dùng semantic token, không hard-code màu
theme. Status text có variant bảo đảm contrast; Area identity color không đổi.

Toggle ở top nav; production-mode Scan Station và kiosk board dùng compact
borderless control trong header. Persistence đã chốt: authenticated User preference
→ Scan Station preference → Dark default. Worker Session không ảnh hưởng theme.
Phase 2 chỉ giữ session; persistence đến khi User/Station config hoàn chỉnh.

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
    Due Soon window = lead-time ratio clamp bởi config; default min 2d, 15%, max
    7d. Mock dùng relative offset resolved một lần.

---

# 4. Scan Station

Một screen/station, PN-centric và one-shot.

## 4.1 Routing và chọn Station

- `/scan-station` — selector, không auto-redirect. Card cho active Station hiển
  thị ID, Department, Area, individual Operation chips, Machine presence. Full-card
  button mở standard; sibling `Production mode` mở production, không nested control.
- `/scan-station/:stationId` — standard mode có top nav; invalid/inactive ID lỗi.
- `/scan-station/:stationId/production` — ẩn top nav, nhưng đây không phải auth
  boundary. Giữ offline banner, Worker/session, mọi workflow và theme control.

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

Chỉ completed PN action cập nhật target; Worker scan/cancel không. Undo mở structured
summary, original event giữ nguyên và reversal auditable. Sau Undo target lùi tới
eligible previous action; none thì disabled.

Reversing Worker theo Area mode. Sau `Confirm reversal` luôn có final gate:
scanned-session + UNDO option ON → active badge scan; otherwise warning question
`Reverse this action?`. Expired session đã bị badge modal block. Production Undo
đảo complete application command, không arbitrary Movement row.

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
(Phase 13), authorization (Phase 14).

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

## 4.11 States

Loading skeleton giữ layout; empty giải thích next action; unknown Station explicit
error; disconnected giữ loaded data nhưng block write; validation inline; long-data
preview kiểm tra PN/row wrapping. Không optimistic completion.

## 4.12 Worker identification và session

Worker khác User. Worker là Scan-Station audit identity, profile stable id/name/
existing badge/avatar/active, không employee number; non-`PF:` badge exact-match
active Workers. Mode: Disabled, Fixed, Scanned session. Badge ở Disabled/Fixed chỉ
trả explanatory notice, không sign in.

Scanned session dùng configurable sliding inactivity timeout; valid production
interaction refresh, invalid không; badge khác switch ngay. Station không session
hoặc expired hiển thị blocking modal (`Worker session expired` / `Scan your badge
to continue.`); chỉ Scan Station block. Open dialog draft giữ dưới modal, valid
badge đóng modal và restore focus đúng context. DEV modal có shared demo badge
notice. Sensitive-action badge gate khác sign-in modal: cancellable và confirm actor.

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
- Dwell derive từ timestamp/shared clock; unusually long amber. Total row có one
  continuous separator và reconciled quantity. Scrap là plain error text `n scrapped`.
- Due countdown derive; Hot sort trước theo rank, rồi canonical due ordering. Chỉ
  urgency text blink; Hot flame pulse riêng; reduced-motion tắt animation.
- Dynamic pagination đo actual viewport/row; ≥1 row; fallback 10 trong layout-less
  test. Auto rotate chỉ multi-page, default 3s/displayed row, min 6s, config per
  Department. Buttons/dots/arrows/swipe không wrap; manual change restart timer.
- Rotation progress dùng cùng deadline, hidden khi one page; reduced motion giữ
  seconds text nhưng ẩn moving track.
- `Auto scale` default On dùng one uniform zoom, scale up/down để full table width
  fit; pagination chia height budget theo cùng factor. Off trả baseline.
- Footer nằm trong flex flow, không fixed/overlay; controls + aggregate + legend.
  Header identity: Department trên, `Production` + connection-toned `● Live`; healthy
  dot dùng shared heartbeat; stale có `Feed stale — reconnecting`.

**Implementation boundary (Phase 11):** production UI thật trên
`GET /api/production-board` (IMPLEMENTATION_ROADMAP Phase 11): rows đến theo
canonical board order từ read model của **server** — phân bổ theo Area / Machine /
External activity với state derive, timestamp vào vị trí cố định, stocked và
scrapped, Work Order / Job Number context còn mở và Hot rank đều derive server-side (Work Order đã complete không cấp context cho row; rows theo đúng canonical demand ordering — stocked không phải tầng sắp xếp; quantity đã merge đọc qua MỌI nhánh lineage nên dated theo entry cũ nhất của cả khối merge và chỉ nêu Machine hoàn thành khi các nhánh đồng nhất) từ
projection vị trí hiện tại và Movement history — còn mọi giá trị thời gian hiển
thị (dwell theo vị trí và cờ `long`, due countdown, `Total Days`, đồng hồ) vẫn
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
revision PN chỉ render khi Part Numbers management (Phase 13) cung cấp. Mọi thứ
khác ở trên — kiosk, pagination và rotation, auto scale, điều hướng tay, location
grid, tooltip, legend — giữ nguyên; mock dataset Phase 2 của board đã bỏ, các
preview `?state=` chỉ development (loading / empty / error / long) render state
xác định mà không request.

## 5.1 Kiosk mode

- `/production-board` standard; `/production-board/kiosk` kiosk. Route explicit,
  không query/local boolean. `Ctrl+Shift+K` toggle, presentation-only.
- Kiosk ẩn top nav nhưng giữ OFFLINE banner; dùng same board header, compact theme
  toggle, live status, clock; full viewport không leftover offset.
- Footer có `Exit kiosk`; shortcut/theme/auto-scale/manual pages vẫn dùng được.
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
queued / processing / finished và tổng), cộng Operation active, scrapped theo PN
trong Area đó, và — với terminal Stockroom, nơi quantity đã hoàn tất sản xuất
nên không còn active flow — các stocked line kèm allocation active của PN. All
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
dataset Phase 2 của board này đã bỏ. **Chưa có:** highlight thời gian chờ theo
expected duration vẫn là phần mở của Phase 11.

---

# 7. Tracking

Operator title **PN Tracking**, route/internal name giữ Tracking. Filtered results
table + modeless fixed lower-right detail overlay. Whole row toggles selection; PN
cell button là single focusable control. Panel không backdrop/focus trap và không
reflow table; close bằng ✕/Escape/selected-row/outside click; ≤900px spans viewport.

## 7.1 Filter và list

Search PN/WO/Job; filters Area/Operation/Machine/Request Type/Hot/status/due. Columns:
PN+name/Hot, active Demand, distribution dots, active/stocked/scrapped quantity,
next due và status. Deleted-master PN vẫn canonical, metadata `—`; null WO `—`.

## 7.2 Detail panel

1. PN master/current metadata + derived barcode; absent master không ảnh hưởng history.
2. Active Demand table với allocation progress, labeled separate from Movement.
3. Current Area/Machine bars, derive Movement.
4. Flow & Routes: shared compact `RouteModeChip`; Planned snapshot state/deviation;
   Floating actual trace, repeated Area/split/Repair; arrows separate siblings;
   finished rack không route step.
5. Immutable reverse-chronological Movement history, canonical types, Repair badge,
   DONE vs Stocked distinction; no edit.
6. Scrap history + cumulative/reconciliation.
7. Stocked & Allocation history.
8. Authorized corrections: adjustment, route, allocation, priority, audit; reason
   bắt buộc và tạo new history.

## 7.3 States

Per-section skeleton; `No PNs match — clear filters`; unauthorized user không thấy
Corrections thay vì disabled controls.

---

# 8. Priority Management

Hot ranking thuộc WorkOrderDemand. List có `🔥#n`, WO/Job, TypeChip, demand figures,
distribution và due tone.

- Add search/scan: 0 eligible không add; 1 add trực tiếp; nhiều phải chọn exact
  Demand; new entry bottom.
- Remove confirm PN/Demand; confirm close rank gap + audit; Undo restore.
- Drag/Move Up/Down/Undo/Redo đều confirm **trước** apply. Dialog show moved summary,
  impact và Current Position → New Position snapshots chỉ affected rank range.
  Transition `#old → #new`; added/removed dùng `Not listed`; shared content-sized
  tracks align PN/metadata; mobile stacked fallback.
- Apply ranking/Cancel; không renumber sớm; Undo/Redo title user-facing, depth
  unlimited trong session; mỗi step audited và cũng cần confirmation.
- Footer diễn giải Hot first → due-date ordering, không phơi field/tie-breaker.

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
gate. Phase 2 preview only; timeout read-only, toggles session-only. Department
display config per Department. Due Soon settings default 2d/15%/7d.

Không có Machine, RouteTemplate hay PartNumber registry trong Admin; chúng ở
Management. Barcode configuration có persisted Asset Tag format prefix + 1–8 digit
minimum width, live Next Tag/scanned barcode; whitespace/colon prefix invalid, không
trim/clamp; format change không rename old tag hay reset never-reuse sequence.

Phase 3.5 Departments/Areas/Operations/Stations/barcode là real API-backed UI với
loading/error/retry/offline gate. Later Admin sections honest unavailable. Workers
profile tách Users. Areas table trình bày Operations, derived assignment mode,
Machines, Worker mode, terminal/active. Active-quantity Area deactivation bị block.

History maintenance: lossless export → verify → purge exactly archived rows qua
privileged Admin path, preserve related Movement chains, preview scope, reason và
audit; không purge-first.

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
authorization.

---

# 11. Work Orders

Management UI cho manual demand + explicit release, không ERP customer/pricing/
invoice/shipping/accounting. Routes: active list `/management/work-orders`, completed
history `/management/work-orders/completed`; both keep Work Orders subnav active.
Details/New là modal trên list, URL không đổi. Native `<input type="date">`, ISO
internal. Phase 4 active workflows real API-backed; trang Completed Work Orders là
production UI thật từ Phase 10 (§11.5). Work Order chỉ rời active list qua
allocation-derived completion (Phase 10, §11.5): list tăng theo intake, giảm chỉ
theo completion.

## 11.1 WO list

Row: WO number, received/due, demand count/PN preview, Open/Released. Completed không
ở active list. Server-side number search + bound 100 newest; search before bound;
exact duplicate lookup whole history. Toolbar full-width: search, quiet Completed
link, primary New. Internal null WO hiển thị `—` + label. Whole-row opens details,
focus return khi close.

## 11.2 Work Order Details — modal với demand lines

Accessible modal/focus trap/stacked child dialogs. `Save demand` + `Cancel (Esc)`;
dirty close phải discard confirm. Header identity/meta, editable WO due chỉ Open.

Demand row fields PN, Request Type, qty, due, priority, Jobs, requester/reason/notes.
PN control mở shared barcode label; new marker inline. Open WO primary Add Part
manually, barcode optional. Duplicate PN focus existing line.

Released line: qty/due/Jobs editable, PN/Request Type read-only; qty below committed
inline error. Raising qty reopens remaining/release. Released WO không add/remove/
edit header scope. Removal: draft immediate, saved-unreleased confirm, released
disabled với lý do. Due default chỉ propagate tới line còn giữ inherited default;
explicit no-date không inherit lại.

PN lookup chỉ nói “new” sau exact server response; in-flight shows Searching.
Validation missing PN/non-positive/duplicate, due null valid; first invalid focused,
input preserved. Dirty state là actual diff và guard navigation/back/reload.

## 11.3 New Work Order — manual-first modal và Add Part

Accessible modal over list, focus restore, dirty discard confirmation. Header:
optional WO Number, today received, optional WO due. Blank number stores NULL/`—`;
existing active/completed number opens existing after protecting entered draft.

Add Part multi-step: PN exact lookup/create → positive quantity → optional due with
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

Real deep-link read-only page. Giá trị trung tâm là `completed_at` hiển thị thành
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
details với done date và allocated quantity. No New/edit/release; independent
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

---

# 12. Machines (Management)

Permission-based view cho monitoring/lifecycle/maintenance/asset, không CMMS.

## 12.1 Active Machines table

Search + New; columns Machine | State | Assigned now | Asset | Maintenance; no
Actions. Sort header cycles asc/desc/none, stable, `aria-sort`. State derive
Maintenance > Running if assigned > Idle, elapsed shared clock. Assigned quantity
semantic tone; asset metadata content-sized. Per-row accessible On/Off switch chỉ
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
preserve input; focus name on New. Lifecycle timeline append-only Retired/Reactivated.
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
delete/duplicate in dialog. Archived row only Duplicate.

## 13.2 Create / Edit dialog

Required name, description, ordered steps: Area, scoped Operation, duration,
stable-id preferred active Machine, instruction. Drag + Up/Down, never drag-only;
add/remove, ≥1 step. Unavailable stored Operation/Machine remains explicit, không
silent-clear. Area change revalidates selections. Dirty guard. Used-template note:
changes future only; existing snapshot untouched. Duplicate handles unsaved choice
and creates active variant draft.

## 13.3 Archive so với delete

Never-used delete bằng plain confirm. Ever-used archive: protect unsaved choice,
typed exact route name, explain future unavailability/snapshots/history. Archived
không selectable. Không version system riêng.

## 13.4 States và Phase 2 boundary

Standard loading/error/empty. Phase 2 dev-only mock state với one DevNotice; real
backend phase sau.

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
từ Part Numbers, Work Order line và Add Part. Không barcode config tại đây.

## 14.4 States và Phase 2 boundary

Standard loading/error/empty. Phase 2 dev-only mock with one DevNotice; deliberate
deleted-master PN absent để test Tracking behavior.

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
