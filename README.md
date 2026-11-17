# FishingLive TikTok Bridge

Python 3.10+ nhận sự kiện TikTok LIVE và xếp vào hàng đợi HTTPS. Roblox server chỉ kéo từng batch rồi xác nhận. Client Roblox không gửi sự kiện TikTok.

Chưa có phòng LIVE thật để nhận sự kiện. Bridge đã triển khai trên Render Free
(`https://python-bridge-6v9w.onrender.com`, `1.4.0`), deployment đã trỏ tới handle thật
`surakun0zh` với `hasSession=false` (nghe không cần session), nhưng lúc kiểm tra 2026-11-17
phòng chưa phát sóng nên `/v1/tiktok` báo `connected=false`, `lastError=UserOfflineError`.
Quy trình bật LIVE thật: `../FISHINGLIVE_PHASE12_TIKTOK_LIVE.md` (§10 là log lần kiểm tra).

## Cài trên Windows PowerShell

```powershell
cd C:\Users\ctytp\Desktop\mapRoblox\tiktok-bridge
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Chạy nhanh trên máy này: bấm đúp `Run.bat`. File đó tự dò Python còn chạy được (`.venv313` nếu
có → `.venv` → `py -3` → `python.exe`) và chỉ nhận interpreter import được `fastapi` + `uvicorn`. Từ
2026-11-17 chỉ còn một venv chuẩn là `.venv` (CPython 3.13.15, đã cài `requirements.txt` +
`TikTokLive 7.0.1`); `.venv313` đã xóa. Kiểm tra nhanh:
`$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe -m unittest discover -s tests` → 42 test qua.

Mở `.env` và đặt hai khóa riêng, mỗi khóa ít nhất 16 ký tự:

- `BRIDGE_INGEST_KEY`: chỉ process Python dùng để đẩy sự kiện vào hàng đợi.
- `BRIDGE_READ_KEY`: chỉ Roblox server dùng để lấy batch và xác nhận.

Không điền session TikTok cho đến khi bạn phê duyệt kết nối thật. Không commit file `.env`.

Chạy mock, không mở TikTok:

```powershell
$env:PYTHONPATH = "src"
python -m fishinglive_bridge
```

Mặc định process chỉ nghe `127.0.0.1:8787`. Địa chỉ này không phải endpoint của Roblox. Roblox HttpService từ chối localhost và từ chối URL không phải `https://`.

Đẩy một comment giả bằng khóa ingest:

```powershell
$headers = @{ "X-Bridge-Key" = $env:BRIDGE_INGEST_KEY }
$body = @{
  eventId = "join-01"
  type = "comment"
  viewerId = "v-01"
  nickname = "Ha"
  timestamp = 1700000001
  payload = @{ text = "cau" }
} | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8787/v1/events" -Headers $headers -Body $body -ContentType "application/json"
```

Roblox lấy batch bằng khóa read trong header `x-api-key` (HttpService chỉ gửi được `x-api-key` và `content-type`):

- `GET /v1/events?limit=20` với header `x-api-key` = `BRIDGE_READ_KEY`
- `POST /v1/events/ack` với cùng header `x-api-key` và body `{ "sequences": [1, 2] }`

`X-Bridge-Key` vẫn là header của khóa ingest. Khóa ingest không được dùng để đọc hoặc ack. Một công cụ local vẫn có thể gửi khóa read trong `X-Bridge-Key`.

Bảng điều khiển local, khi process nghe loopback và không ở chế độ live:

- Mở `http://127.0.0.1:8787/control`
- Trang này không nhận khóa từ trình duyệt. Process tự ghi sự kiện vào hàng đợi RAM.
- Route `/control` trả 404 nếu bind không phải loopback hoặc client không phải localhost.
- Chế độ live không nhận sự kiện giả từ trang này.

Bảng host mới hơn nằm ở `host-controller` và mở bằng `http://127.0.0.1:8787/host`. Trang đó dùng cùng hàng đợi, không nhận khóa trên trình duyệt, và không được đưa lên Cloudflare Tunnel. Xem `host-controller/README.md`.

Sự kiện nằm trong RAM cho đến khi Roblox gửi ack. Hàng đợi không ghi file và không dùng database. Tối đa 2000 sự kiện đang chờ. Khi đầy, like của cùng một viewer được gộp. Gift và comment `cau` / `câu` không bị loại để lấy chỗ. Mất kết nối TikTok tạm thời không xóa phiên. `POST /v1/session/reset` với `{ "confirm": true }` và khóa ingest mới tạo phiên rỗng.

## Kiểm thử

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
```

## Roblox

Trong Studio, trên `ServerScriptService.FishingLive`, đặt attribute khi bạn đã có URL HTTPS công khai:

- `BridgeEnabled` = true
- `BridgeBaseUrl` = `https://ten-mien-cua-ban`

Không đặt `BridgeKey` vào Attribute. Khóa read được chuẩn bị qua Roblox Secrets Store, tên `FISHINGLIVE_BRIDGE_READ_KEY`. `HttpService:GetSecret` trả về kiểu Secret. `BridgePoller` gửi secret đó trong header `x-api-key` cho cả GET batch và POST ack. Không gửi `X-Bridge-Key` từ Roblox. Bản này không tự tạo secret và không bật Allow HTTP Requests.

Polling mặc định 2 giây (`BridgeConfig.PollInterval` trong place Hung1 hiện đặt 1 giây) và chỉ chạy khi `BridgeEnabled` đúng là true. Mỗi batch tối đa 20 sự kiện. Script server bỏ qua localhost, URL `http://`, và không tạo RemoteEvent. `sessionId` đổi khi process Python hoặc lệnh reset đổi phiên. Lỗi mạng không xóa viewer.

Thumbnail TikTok, khi được bật sau này, chỉ lấy lúc viewer tham gia, resize 64×64, giữ tối đa 100 ảnh trong RAM. `GET /v1/overlay/viewers` trả ảnh đó cho OBS. Endpoint này chưa được public và không ghi file ảnh.

Bật Game Settings → Security → Allow HTTP Requests chỉ sau khi bạn phê duyệt. Bản này không tự bật cờ đó.

`BridgePoller` gọi `EventRouter:Ingest`. Lỗi mạng được nuốt, vòng chơi không dừng. Cùng một `eventId` không cộng điểm, quà hoặc lượt like lần thứ hai.

## Phiên bản TikTokLive

Đã cài và đối chiếu TikTokLive 7.0.1:

- `CommentEvent.comment` là alias của `content`.
- `FollowEvent` là `SocialEvent`.
- `LikeEvent.count` là số like gộp trong một sự kiện. Bridge gửi một sự kiện like, không nhân bản theo count.
- `GiftEvent.streaking` đúng khi quà type 1 còn đang nối và `repeat_end` chưa kết thúc. Các sự kiện giữa streak bị bỏ. Chỉ sự kiện cuối được xếp hàng, `amount` là `repeat_count` và không vượt quá 20.
- Không gọi `send_room_chat`.

`TIKTOK_SESSION_ID` và `TIKTOK_TARGET_IDC` chỉ dùng khi `BRIDGE_MODE=live`. Project không đặt biến `WHITELIST_AUTHENTICATED_SESSION_ID_HOST`. Log của bridge thay khóa và session bằng `***`.

### Chế độ username-only (mặc định)

Để trống cả `TIKTOK_SESSION_ID` và `TIKTOK_TARGET_IDC`: listener vào phòng như người xem ẩn danh, không gửi session của chủ phòng cho sign server. Đây là đường an toàn; đặt session chỉ dùng khi tự dựng sign server đã whitelist.

- Trước khi mở websocket, bridge gọi `is_live()` (một request HTTP). Phòng chưa phát → `lastError=UserOfflineError`, `offline=true`, không mở socket. Sau 5 lần "chưa live" liên tiếp vẫn thử một lần thật để một câu trả lời sai không khóa bridge.
- `TIKTOK_ROOM_ID` (tùy chọn, chỉ chữ số): bỏ qua bước scrape HTML `@user/live` khi mở kết nối đầu tiên. Room id học được từ lần kết nối thành công được dùng lại cho các lần sau. Cần thiết vì TikTok trả trang WAF (HTML) cho bước scrape, và đó chính là nguồn gốc lỗi `JSONDecodeError` cũ.
- `ConnectEvent`/`DisconnectEvent` chỉ cập nhật trạng thái, không xếp hàng: schema Roblox vẫn chỉ có `comment`, `like`, `gift`, `follow`.

## Đổi kênh LIVE khi bridge đang chạy (1.4.0)

Bridge `BRIDGE_MODE=live` đổi được tài khoản mà không cần redeploy:

- `GET /v1/tiktok` (header `X-Bridge-Key` = ingest key) trả `username`, `connected`, `lastError`, `offline`, `sessionMode` (`username-only`/`authenticated`), `hasSession`, `envHasSession`, `roomId`, `attempts`, `connects`, `switches`, `tiktokLive`. Không trả session.
- `POST /v1/tiktok/switch` body `{ "username": "...", "sessionId": "...", "targetIdc": "..." }` (cùng header).
  - `sessionId` + `targetIdc` chỉ giữ trong RAM, không ghi log, không trả lại.
  - Không gửi `sessionId` = giữ session đang dùng. Gửi `"sessionId": null` (hoặc `""`) = **bỏ** session cho tiến trình này, chuyển sang username-only; giá trị đang lưu trong `.env`/Render vẫn còn và lần restart sau sẽ dùng lại.
  - Nhập session mà thiếu `targetIdc` → 422.
- Trang `/host` local có nút **Đổi kênh trên Render** (panel LIVE) và ô tick **Bỏ session trên Render (chỉ dùng username)**: trình duyệt gọi local `/host/action` → local gọi Render bằng `BRIDGE_MIRROR_URL` + ingest key. Nhờ vậy khóa không bao giờ xuống trình duyệt và không cần mở Render dashboard. Bỏ trống ô session (không tick) vẫn là "giữ session hiện tại".


## Hosting (Render)

Đã triển khai bản 1.4.0 trên Render Free: `https://python-bridge-6v9w.onrender.com` (health,
batch, ack và `/v1/tiktok` đều đã kiểm). Render Free không có disk và tự ngủ khi rảnh; biến môi
trường và quy trình deploy ở `RENDER_DEPLOY.md`. Nếu chuyển sang máy khác: chạy bridge ở
`127.0.0.1`, cho Caddy/Cloudflare Tunnel cấp URL `https://` công khai, rồi điền URL đó vào
`BridgeBaseUrl`. Không dùng localhost làm endpoint của Roblox server.

## Dọn dẹp 2026-11-17

Đã xóa 23 thư mục `pip-*`, thư mục tạm `tmp6r4udwgr`, `.pytest_cache`, mọi `__pycache__`,
`.venv` hỏng (tạo trên máy cũ) và `.venv313` trùng, rồi dựng lại một `.venv` chuẩn. Output Phase
9F/9G/9H, gói `FishingLive_Render_Ready` (bản 1.0.0) và vài ghi chú cũ đã chuyển sang
`../_cleanup_20261117/` — danh sách đầy đủ và cách khôi phục ở
`../_cleanup_20261117/MANIFEST.md`. Sau khi dọn: 42 test qua, `/v1/health` = `1.4.0`,
`POST /host/action {"action":"live_tiktok_status"}` chạy được trên bridge mock cục bộ.
