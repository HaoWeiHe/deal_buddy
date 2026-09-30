# 好朋友優惠 DealBuddy（MVP）

依照 [tech spec](https://claude.ai/code/artifact/dd49aaf2-e063-44b8-92ec-bed1921d2893) 實作的第一版：一個很會薅羊毛、記得你說過什麼的「好朋友」。
你可以問它最近有什麼好康（Pull），它也會每天挑最多 3 則真的跟你有關的優惠主動跟你說（Push）。

## 快速開始

```bash
cd app
pip install -r requirements.txt
cp .env.example .env          # 填 ANTHROPIC_API_KEY；不填也能跑（離線模式）
set -a; source .env; set +a

python -m dealbuddy.seed demo             # 放入 spec 範例的優惠和一位示範使用者
uvicorn dealbuddy.api:app --port 8000     # 網頁聊天：http://localhost:8000
python -m dealbuddy.telegram_bot          # Telegram bot（需 TELEGRAM_BOT_TOKEN）
pip install -r requirements-dev.txt && python -m pytest -q tests   # 測試（設 TEST_DATABASE_URL 可改測 Postgres）
```

定時工作（cron 或雲端排程）：

```bash
python -m dealbuddy.sources   # 每天幾次：小紅書 / IG hashtag / RSS 抓新優惠
python -m dealbuddy.digest    # 只用網頁版時手動跑每日推薦；Telegram bot 會自己推
```

## 部署到 Vercel

1. 在 Vercel 匯入這個 GitHub repo（Framework 選 Other，其他保持預設）。
2. 在 Vercel 專案的 **Storage** 加一個 **Neon Postgres**，它會自動設定 `DATABASE_URL`。
3. 在 **Settings → Environment Variables** 設定：
   - `ANTHROPIC_API_KEY`
   - `DEALBUDDY_APP_TOKEN`：網頁版的密碼。公開部署沒有它會拒絕服務，避免陌生人花你的 API 額度
   - `CRON_SECRET`：隨便一串亂碼，Vercel Cron 會自動帶上
   - 用 Telegram 的話再加 `TELEGRAM_BOT_TOKEN`、`TELEGRAM_WEBHOOK_SECRET`（隨便一串亂碼）
4. 重新部署後：
   - 網頁版：打開 `https://你的網址/?token=你的DEALBUDDY_APP_TOKEN`（之後會記住）
   - 放示範資料（可選）：在本機設好 `DATABASE_URL` 後執行 `python -m dealbuddy.seed demo`
   - 設定 Telegram webhook：`curl -X POST -H "X-App-Token: 你的token" https://你的網址/telegram/setup`
5. 每日推薦：`vercel.json` 設定 Vercel Cron 每天 15:00 UTC（多倫多上午 10 或 11 點）呼叫 `/cron/daily`，會抓 RSS / IG 並送出 Telegram 每日推薦。
   Hobby 方案一天只能跑一次 cron，所以「即時推」只在本機長駐的 `python -m dealbuddy.telegram_bot` 有。

## 小紅書：常駐主機（`deploy/xhs-host`）

xiaohongshu-mcp 需要一直開著、已經登入的瀏覽器，放不進 Vercel。所以另外找一台一直開著的 Linux 機器（$5/月的 VPS 即可，建議 x86、2 GB 以上記憶體，因為 MCP 裡跑的是完整的 Chrome；有多倫多機房的 Vultr、DigitalOcean 都可以，家裡一直開著的電腦也行），用 Docker Compose 跑兩個服務：

- `xiaohongshu-mcp`：社群版 MCP，只開給本機，登入的 cookie 存在 `data/`
- `worker`（`dealbuddy/worker.py`）：每 `XHS_INTERVAL_HOURS` 小時依所有使用者的城市、意圖、喜歡的商家搜小紅書，也順便跑 IG / RSS；每 `WORKER_POLL_MINUTES` 分鐘檢查有沒有新意圖，有就馬上幫那個人搜；有設 `TELEGRAM_BOT_TOKEN` 的話也從這裡送即時推

worker 直接寫進 Vercel 用的同一個 Postgres，Vercel 那邊不用改任何設定。

```bash
git clone https://github.com/HaoWeiHe/deal_buddy && cd deal_buddy/deploy/xhs-host
cp .env.example .env          # 填 DATABASE_URL（從 Vercel 的 Neon 複製）、ANTHROPIC_API_KEY 等
docker compose up -d
docker compose run --rm worker python -m dealbuddy.xhs_login   # 產生 data/xhs-login.png 並等你掃
```

在自己電腦執行 `scp 你的主機:deal_buddy/deploy/xhs-host/data/xhs-login.png .`，用**小號**的小紅書 app 掃碼。登入後 cookie 會保留，過期時 worker 的 log 會提醒你重掃（`docker compose logs -f worker`）。

如果主機上拿不到 QR code（小紅書會把沒登入的機房訪客直接轉到 `/login` 頁，MCP 就等不到登入小視窗），改在自己的電腦登入再把 cookie 複製過去：
從 [xiaohongshu-mcp Releases](https://github.com/xpzouying/xiaohongshu-mcp/releases) 下載 `xiaohongshu-login-darwin-arm64`（或 Windows / Linux 版）執行，用小號掃碼，會在同一個資料夾產生 `cookies.json`；
`scp cookies.json 你的主機:deal_buddy/deploy/xhs-host/data/cookies.json` 後 `docker compose restart`。

注意：
- 不要在 Vercel 設 `XHS_MCP_URL`，MCP 只給這台主機用。
- 建議設 `XHS_MCP_TOKEN`，MCP 的 API 會要求密碼。
- 小號的風險：頻率預設很低（每輪最多讀 10 篇、每次間隔 5 秒）。被要求驗證或登出時 worker 只會停抓小紅書，其他來源照常。

## 兩種模式

| | 有 `ANTHROPIC_API_KEY` | 沒有 |
|---|---|---|
| 對話 | Claude Sonnet 5.5 tool-use agent，會即時網路搜尋 | 規則式，只懂簡單句型 |
| 優惠擷取 | Claude Haiku 4.5（看得懂截圖） | 關鍵字 + 正則 |
| 排序、回饋、推播 | 相同 | 相同 |

排序永遠走公式（spec §7），不靠 LLM 記憶：`Score = max(P, I) × A × F`，`P = 0.5E + 0.3C + 0.2M`。
每則推薦都附理由、期限、條件和來源，分數明細在 API 回傳的 `breakdown`。

## 資料來源（spec §4）

- **小紅書**：自己跑社群版 [xiaohongshu-mcp](https://github.com/xpzouying/xiaohongshu-mcp)，用**專用小號**登入，設定 `XHS_MCP_URL`。
  我們呼叫它的 HTTP API：搜尋「<城市> 羊毛 / 优惠」和每位使用者進行中的意圖（例如「多伦多 洗牙 优惠」），低頻率、每次最多讀 `XHS_MAX_DETAILS_PER_RUN` 篇。
  讀帳號首頁推薦 feed 預設關閉（`XHS_READ_FEED=0`）。
- **Instagram**：官方 Graph API hashtag 搜尋（`IG_USER_ID`、`IG_ACCESS_TOKEN`、`IG_HASHTAGS`）。
- **Facebook**：沒有可用管道，只靠使用者分享。
- **使用者分享**：在聊天裡貼連結、文字或截圖。小紅書/IG/FB 連結讀不到內容時，好朋友會請你截圖。分享本身也會提高對該商家的興趣分數。
- **公開 RSS**：`DEALBUDDY_FEEDS`。

## 語言：不限中文使用者（spec §8、§12）

- 好朋友一律用使用者的語言回答。中文與英文會依使用者寫的字自動切換，第一則訊息參考瀏覽器 / Telegram 的語言；其他語言（法文等）由 agent 記下。
- 優惠抽取時同時產生中文與英文的標題、摘要、條件，所以小紅書的中文筆記會以英文卡片呈現給英文使用者，並附原文標題與連結。
- 推薦理由、每日推薦、Telegram 按鈕、網頁介面都有中英文。截圖：[中文](docs/web-chat.png)、[英文](docs/web-chat-en.png)。

## 信任與指標（spec §2、§12）

- 排序只看使用者模型和優惠本身的價值，收入永遠不進公式。`deals.sponsored=1` 的贊助內容不會進入一般推薦（之後若要顯示，會是獨立、標示清楚的區塊）。
- 使用者回報「已使用」時記下估計省下的金額（`metrics.py`）。`GET /metrics?user_id=` 回傳這個月省了多少，以及相關推薦率、不感興趣率、使用數、週活躍。Telegram `/me` 和網頁「你眼中的我」也會顯示這個月省了多少。

## 地區

跟著使用者設定的常駐城市（聊天裡說「我住多倫多」或 `PATCH /profile`），加上線上優惠。
有旅遊意圖（「下個月去溫哥華玩」）時，目的地的優惠也會納入，並在理由寫「你溫哥華的行程用得到」。

## API

| 方法 | 路徑 | 用途 |
|---|---|---|
| POST | `/chat` | `{user_id, text, image_base64?, lang?}` → `{reply, cards, lang}` |
| POST | `/feedback` | `{user_id, deal_id, signal: useful/saved/used/not_interested, reason?}` |
| GET / PATCH | `/profile?user_id=` | 「好朋友認識的你」，可改城市、推播設定、刪喜好或關閉意圖 |
| DELETE | `/me?user_id=` | 刪除所有個人資料 |
| GET | `/deals` | 優惠庫（除錯用） |
| POST | `/deals/ingest` | 直接丟內容進優惠庫 |
| POST | `/digest/preview?user_id=` | 預覽今天的主動推薦 |
| POST | `/sources/run` | 手動跑一次來源抓取 |
| GET | `/metrics?user_id=&days=30` | 成功指標與每月估計省下金額 |

Telegram 指令：`/today` 今天的推薦、`/me` 你眼中的我、`/forgetme` 刪除資料；每則推薦下方有「有用 / 收藏 / 已用 / 不感興趣」按鈕，不感興趣會再問原因，只降對應的那個維度。

## 程式結構

| 檔案 | 內容 |
|---|---|
| `db.py` | 資料表（spec §10）。有 `DATABASE_URL` 用 Postgres，否則用 SQLite |
| `entities.py` | 商家/品牌/地點正規化（中英、簡繁別名）、城市與都會區 |
| `profile.py` | 使用者模型四層、半衰期 6 個月、意圖生命週期、醫療意圖結束即刪 |
| `extract.py` | 分享內容 → Deal schema（LLM 或離線規則） |
| `deals.py` | 優惠庫：去重合併、過期下架、14 天未驗證、地區過濾 |
| `ranking.py` | 排序公式、推薦理由、卡片 |
| `feedback.py` | 回饋規則（spec §9）、個人折扣門檻學習、連續忽略降權 |
| `tools.py` / `agent.py` | 好朋友 agent 的工具與 Claude 迴圈 |
| `offline.py` | 沒有 API key 時的規則式對話 |
| `chat.py` | 一次對話：存訊息、處理分享、回答、記錄推薦 |
| `i18n.py` | 中英文介面字串與語言偵測 |
| `metrics.py` | 估計省下金額與成功指標 |
| `digest.py` | 每日推薦（≥ 0.6、最多 3 則、安靜時段、沒有就不發）與即時推（≥ 0.85 且 48 小時內到期或對上意圖） |
| `sources.py` | 小紅書 MCP、IG Graph API、RSS |
| `worker.py` / `xhs_login.py` | 常駐主機的定時抓取迴圈、小號掃碼登入 |
| `telegram_bot.py` | Telegram long polling bot |
| `web/index.html` | 網頁聊天 |

## 還沒做 / 已知限制

- 網頁版用瀏覽器產生的 id 識別使用者，只有可選的共用密碼，適合種子使用者試用，不適合公開。
- 向量搜尋（pgvector）還沒接，搜尋靠類別、商家、關鍵字 + LLM。
- 小紅書只用共用小號，依每位使用者的城市、意圖和喜歡的商家組搜尋關鍵字；不讀個人帳號。
- 促銷郵件轉寄、行事曆授權、週期性意圖（每半年洗牙）屬第二階段。
