# FishingLive TikTok Bridge

Python 3.10+ nhận sự kiện TikTok LIVE và xếp vào hàng đợi HTTPS. Roblox server chỉ kéo từng batch rồi xác nhận. Client Roblox không gửi sự kiện TikTok.

Chưa triển khai hosting. Chưa kết nối tài khoản TikTok thật.

## Cài trên Windows PowerShell

```powershell
cd C:\Users\Tan Hung\Desktop\FishingLive\mapRoblox\tiktok-bridge
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

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

Polling mặc định 2 giây và chỉ chạy khi `BridgeEnabled` đúng là true. Mỗi batch tối đa 20 sự kiện. Script server bỏ qua localhost, URL `http://`, và không tạo RemoteEvent. `sessionId` đổi khi process Python hoặc lệnh reset đổi phiên. Lỗi mạng không xóa viewer.

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

## Hosting khi bạn muốn công khai

Chưa triển khai. Cần phê duyệt trước. Phương án phù hợp với giới hạn Roblox:

1. Một máy ảo nhỏ chạy bridge ở `127.0.0.1`.
2. Caddy hoặc Cloudflare Tunnel cấp URL `https://` công khai và chuyển tiếp về process đó.
3. Điền URL đó vào attribute `BridgeBaseUrl`. Không đưa `http://127.0.0.1` vào game.

Không dùng localhost làm endpoint của Roblox server.
