# FISHINGLIVE HOST CONTROLLER

Bảng điều khiển tiếng Việt cho HOST giả lập TikTok LIVE trước khi phát thật. Trang này không tự cộng điểm trên trình duyệt. Mọi nút gửi gọi Python Bridge đang chạy, rồi bridge xếp vào hàng đợi RAM hiện có.

Đường dẫn máy này:

`C:\Users\Tan Hung\Desktop\FishingLive\mapRoblox\tiktok-bridge\host-controller`

## Mở trang

1. Bridge ở chế độ `mock`, nghe `127.0.0.1`.
2. Trong PowerShell:

```powershell
cd C:\Users\Tan Hung\Desktop\FishingLive\mapRoblox\tiktok-bridge
$env:PYTHONPATH = "src"
python -m fishinglive_bridge
```

3. Mở `http://127.0.0.1:8787/host`.

Không mở file HTML bằng `file://`. Trang đó không có CORS và không chứa khóa. Không đưa URL này vào Cloudflare Tunnel. Route `/host` trả 404 nếu request có header tunnel, host công khai, hoặc không đến từ localhost.

## Bảo mật

- `BRIDGE_INGEST_KEY` và `BRIDGE_READ_KEY` chỉ nằm trong process Python.
- JavaScript không nhận hai khóa đó, không nhận session TikTok, không nhận secret Roblox.
- Hành động quản trị cần cookie `fl_host_csrf` (HttpOnly, SameSite=Strict) và header `X-CSRF-Token` lấy từ `GET /host/session`.
- Origin lạ và `Sec-Fetch-Site: cross-site` bị từ chối.
- Chế độ `live` không nhận sự kiện giả và không chạy Force.

## Dữ liệu nào là thật

| Thẻ | Nguồn |
|---|---|
| Python Bridge, session, độ dài hàng đợi, RSS, CPU | Process bridge |
| Like, quà, viewer, mở khóa 500 tim | Phép chiếu từ sự kiện bridge đã nhận |
| Cá, trượt, NPC, FPS Roblox, điểm TOP 10, trạng thái giật | Để trống, chữ "Chưa có dữ liệu", cho đến khi Roblox có telemetry |
| Roblox ACK | Chỉ sau `POST /v1/events/ack` thật |

`ROBLOX DISCONNECTED` hiện khi chưa có lần kéo batch trong 12 giây. HTTP 200 của bridge không được hiểu là Roblox đã xử lý.

## Endpoint dùng lại

- `POST /v1/events`, `GET /v1/events`, `POST /v1/events/ack`, `POST /v1/session/reset`, `GET /v1/health`
- Hàng đợi vẫn là `EventQueue`, trần 2000. Không có queue thứ hai.

## Endpoint mới, chỉ localhost

- `GET /host`, `/host/styles.css`, `/host/app.js`
- `GET /host/session`
- `GET /host/dashboard`, `/host/viewers`, `/host/baits`, `/host/leaderboard`, `/host/struggle`, `/host/results`, `/host/events`, `/host/metrics`, `/host/stress`
- `POST /host/action`

Schema sự kiện không thêm loại mới. Like, comment, gift vẫn là bốn loại cũ. Force và một số cảnh camera chỉ được ghi nhận trên bridge vì game chưa có kênh nhận khi HTTP Roblox đang tắt.

## Mồi

Năm cấp lấy theo cấu hình mock đang lưu trong place: 10, 30, 50, 100, 200 xu. `approvedForLive` vẫn false. Nút xem trước không đổi xác suất cá và không ghi đè giá.

## Bài test tải

Có nút dừng. Máy chủ nghỉ khoảng 25 ms giữa các sự kiện và ngừng trước khi hàng vượt 2000. Quà và comment `cau`/`câu` từ trang này cũng không được đẩy quá trần.

## Chưa làm

- Không bật Allow HTTP Requests.
- Không publish place.
- Không nối TikTok thật.
- Không sang phase tiếp theo.
