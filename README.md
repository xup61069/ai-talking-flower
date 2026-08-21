# AI 閒聊花花（Windows 本地版）🌸

一個住在你桌上的智慧語音陪伴角色：**收音 → 回音消除 → 語音辨識 → 思考決策 → 語音直達指令 / 串流語音合成**。全部在本機端離線流暢執行，隱私安全無憂。

![版本](https://img.shields.io/badge/version-0.1.0-ff6ea3) ![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-4ee49d) [![CI](https://github.com/xup61069/ai-talking-flower/actions/workflows/ci.yml/badge.svg)](https://github.com/xup61069/ai-talking-flower/actions/workflows/ci.yml)

> 變更紀錄見 [CHANGELOG.md](CHANGELOG.md)。

---

## 🌟 核心特色與架構

```text
Volt 1 / 麥克風持續收音
→ WebRTC AEC3 消除花花自己的喇叭回音
→ TEN VAD 即時判斷開始與結束說話
→ FunASR Streaming Paraformer 繁體中文語音辨識（真串流：邊說邊辨，部分文字即時上屏）
→ 語音直達指令引擎（VoiceCommander / skills 插件）攔截時間/提醒/天氣/性格/音量指令（0ms LLM 延遲）
  ↳ 若非直達指令 → llama.cpp (Qwen) 串流思考
→ 智慧平滑斷句（SpeechChunker）降低首音延遲 (TTFA)
→ TTS 串流播放（Kokoro 首音 ~0.1s / CosyVoice3 角色聲音）
```

---

## 🚀 第一次執行

直接雙擊 `run.cmd` 即可啟動花花與網頁控制台：

- **Kokoro 引擎**：約 10 秒（預設，極速首音 0.1 秒）
- **CosyVoice3 引擎**：約 30 秒（擬真人聲、支援風格指令與音色複製）

### 取得 AEC 回音消除 DLL（建議）

`native/webrtc-apm.dll` 因授權因素不在 git 內。沒有它程式仍可執行，但回音消除會關閉（插話/自聽干擾風險）。執行一次：

```powershell
./tools/fetch_native.ps1
```

腳本會從 GitHub Release 或本機備份路徑（`tools/native-backup/webrtc-apm.dll`）取得並以 SHA256 校驗。

### 區網暴露與 Token 認證

控制台**預設僅綁 `127.0.0.1:7860`**，安全無虞。若需從手機或其他裝置存取而改用 `--host 0.0.0.0`，請務必設定 Token：

```powershell
# 在 data/settings.json 加入（或透過 UI 全設定修改 web.auth_token）
{ "web.auth_token": "你的祕密字串" }
```

設定後所有 `/api/*` 請求需帶 `X-Auth-Token: 你的祕密字串` header；瀏覽器開啟頁面時會自動彈出輸入框。Token 以 SHA256 儲存，明碼不落盤。

### 系統環境診斷
在 PowerShell 中執行以下指令可快速檢查硬體、音效卡、llama-server 與 TTS 服務：

```powershell
./run.cmd --check
./run.cmd --check --load-asr
```

---

## 🎨 網頁控制台 2.0（Cyber-Botanical Glassmorphism）

- **🌸 動態花花立繪互動**：具備呼吸擺動、眨眼、思考與對齊音訊的開闔嘴型（mouth flap）；點擊花花立繪或按 `空白鍵` 可進行「戳戳互動」，觸發花瓣跳躍動畫與甜美語音回應。
- **🌊 即時音波視覺化（Oscilloscope）**：花花立繪下方配備流暢的螢光霓虹正弦音波 Canvas，即時反映說話、聆聽與思考狀態。
- **🎵 Web Audio 合成音效**：內建免外部檔案的 Web Audio 合成器，戳戳互動、提醒鬧鐘到期與狀態切換皆有悅耳的物理音效回饋。
- **🎨 4 款玻璃質感主題**：支援「🌸 櫻花星夜（預設）」、「🍃 賽博薄荷」、「🌅 琥珀日暮」、「🌌 深淵黑洞（OLED 純黑）」，一鍵無縫切換並記憶偏好。
- **⚡ 即時語音直達指令（VoiceCommander / 技能插件系統）**：
  - **時間報時**：「現在幾點？」、「今天星期幾？」、「現在日期」
  - **定時提醒**：「5分鐘後提醒我喝水」、「半小時後叫我開會」、「明天早上八點半叫我起床」、「每天晚上九點提醒我吃藥」（支援每日重複）
  - **性格切換**：「切換到夜間模式」、「換成吐槽花花」、「換回元氣花花」
  - **音量語速調整**：「大聲一點」、「音量調小」、「說話快一點」、「講話慢一點」
  - **天氣查詢**（需設定 CWA 授權碼）：「明天會下雨嗎？」、「今天天氣如何」——中央氣象署在地資料，0ms 直達
  - 新技能以 `skills/` 插件加入：`@register_skill("名稱")` 一個裝飾器即插即用，不再改核心碼
- **🗣️ 喚醒詞模式（低成本版）**：設定 `interaction.wake_word = "花花"` 後，需先叫名字才回應（「花花現在幾點」）；單叫一聲會得到「我在喔！」回應；留空則隨時聆聽。UI 設定頁可即時切換
- **🎭 多重性格預設**：內建「元氣日常花花 🌸」、「暖心夜間花花 🌙」、「辦公摸魚花花 💼」、「幽默吐槽花花 🧠」等風格，即時調整人設提示詞、語速與溫度。
- **⏰ SQLite 定時提醒排程**：支援秒級定時排程，時間到時花花主動發聲提醒並在 UI 彈出提醒通知。
- **📊 延遲監控 HUD**：即時顯示 ASR 辨識耗時、LLM 首字生成延遲（TTFT）、TTS 首段發聲延遲（TTFA）及全回合總耗時。
- **🧠 記憶搜尋與管理**：支援即時關鍵字搜尋對話歷史、單筆刪除、匯出 JSON 或清空；舊對話自動提煉為背景摘要常駐。
- **📡 一鍵 AEC 回音延遲校準**：自動播放掃頻音量測實際延遲並寫入配置。

---

## 🗣️ 切換 TTS 引擎

編輯 `config.toml` 中的 `[tts]`：

```toml
[tts]
backend = "kokoro"      # 快速預設（首音 0.1 秒）
# backend = "cosyvoice" # 角色聲音，使用 voice/style.txt 的風格

base_url = "http://127.0.0.1:50001"   # kokoro
# base_url = "http://127.0.0.1:50000" # cosyvoice
```

切換後執行 `stop-kokoro.cmd`（或 `stop-cosyvoice.cmd`），再重新執行 `run.cmd` 即可。

---

## 🧪 單元測試

本專案具備完整的自動化單元測試覆蓋（包含 ASR、TTS 清理、斷句、AEC3、記憶管理、定時排程、多性格、語音直達指令及 Web API）：

```powershell
python -m unittest discover -s tests -v
```

---

## 🛡️ 提示詞防洩漏與音訊保護機制

- **語音風格指令淨化**：自動過濾 `<|...|>` 標籤，防止自回歸 TTS 模型把提示詞或語氣指令唸出來。
- **多層角色標籤過濾**：自動剃除開頭的 `花花：`、`回答：`、`Assistant:` 等 LLM 前綴。
- **音訊接縫 20ms 交叉淡化**：消除 TTS 分塊串流時的接縫相位不連續與爆音。
- **Web 控制台綁定**：預設 `127.0.0.1:7860`，若 `--host 0.0.0.0` 會警告：`/api/action` 可執行 PowerShell、`/api/voice-ref` 可寫檔，切勿暴露公網。

---

## 🔧 更新日誌（2026-08-22 體檢修復）

**P0 關鍵修復（重啟不再丟失、回音更穩）：**
- `tools/cosyvoice_server.py` 熱載補 `global`（`#1`）：`/reload`、`/speaker` 真正寫入全域，style 即時生效
- `controller.py` 回答期間持續餵 `aec.process_capture`（`#3`）：濾波器不中斷，恢復聆聽前 500 ms 抑制不掉線
- `commands.py`/`controller.py` 直達指令不寫 `memory`（`#4` 前半）：「大聲一點」等不再污染 LLM 上下文
- `commands.py` + `settings.py` 音量/語速/人設同步 `store.set`（`#4` 後半/`#5`）：重啟後保留，`app.persona_preset` 持久化到 `settings.json`
- `controller.py` 摘要持久化到 `data/summary.txt`（`#7`）：重啟後背景摘要不消失

**P1 品質與量測：**
- `tools/calibrate_aec.py`（`#2`）：`irfft(rfft(rec)*conj(rfft(ref)))` 正方向 + 零填充線性相關 + 原始波形優先/包絡回退，附 ERLE dB 輸出；修正常繞環到 `N-D` 被砍掉的 bug
- `src/talking_flower/asr.py`（`#6`）：process 級 `Paraformer` 快取，`RestartRequired` 不重載模型
- `controller.py` + `tts.py` 真實 TTFA（`#8`）：首個 HTTP 音訊 byte 到達才計時，HUD 不再樂觀
- `llm.py`/`controller.py`/`config.toml`（`#9`）：`SpeechChunker(soft_split=True)` 預設開啟 + `max_tokens 64→120`，長回覆不截斷、逗號可提前斷句
- `src/talking_flower/audio.py`/`tts.py`（重採樣升級）：`scipy.signal.resample_poly` 抗混疊取代 boxcar/`np.interp`，支援任意倍率（`pyproject.toml` 新增 `scipy`）
- `reminders.py`/`controller.py`（`#10`）：`pop_due()` 節流至 `next_due_in()` 排程（50 Hz → 0.2–5 s），並每小時 GC 7 天前的 `spoken=1` 舊提醒
- `commands.py` 中文數字全量 parser：`二十五分鐘/一個半小時/十一分鐘` 正確解析；`config.toml` 註解修正為串流

**工程：**
- `ui/` 拆檔：`index.html 1355→272 行` + `ui/theme.css` + `ui/app.js`，維護性大幅提升
- `.github/workflows/ci.yml`：`ruff` + `unittest` 自動化，Python 3.13
- `pyproject.toml` / `config.toml` 一致化，README 明確 `scipy` 與 `--host` 安全警告
