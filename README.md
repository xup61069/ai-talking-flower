# AI 閒聊花花（Windows 本地版）

一個住在你桌上的語音陪伴角色：收音 → 辨識 → 思考 → 用會說話的花回答。全部在本機執行，資料不會上雲。

目前版本已固定使用 Volt 1 的第一個輸入聲道，並連接電腦上正在運行的 llama.cpp／Qwen：

```text
http://127.0.0.1:8080/v1
Qwen3.5-4B-MTP-GGUF
```

完整流程：

```text
Volt 1 持續收音
→ WebRTC AEC3 消除花花自己的喇叭回音
→ TEN VAD 判斷開始與結束說話
→ FunASR Streaming Paraformer
→ llama.cpp 串流回答
→ 分句後交給常駐的 TTS server（Kokoro 預設，CosyVoice3 可切換）
→ TTS 邊生成邊串流播放，第一段音訊一出來就開聲
```

TTS 串流是這次改進的核心：兩個 server 都把音訊切成小塊即時吐出，播放端邊收邊播，
不再等整句生成完。Kokoro 在 RTX 5080 實測首音約 **0.1 秒**，CosyVoice3 約 **2 秒**
（舊版是整句生成完才回應，CosyVoice3 要等 7～9 秒）。

插話功能目前已關閉。花花回答期間會丟棄麥克風輸入，回答結束後再清空殘留音框並恢復聆聽，因此喇叭回音不會中止回答。

## 第一次執行

直接雙擊 `run.cmd` 就能開始。第一次會在背景載入 TTS server：

- Kokoro：約 10 秒（預設，快速）
- CosyVoice3：約 30 秒（角色聲音、品質較高）

之後服務會保持常駐，所以再次啟動會快很多。

要先檢查全部元件，可在 PowerShell 進入此資料夾後執行：

```powershell
./run.cmd --check
./run.cmd --check --load-asr
```

第一行會檢查 Volt 1、llama-server、TTS server 和 AEC3。第二行還會實際載入 Paraformer；模型已下載完成，通常只需等待載入記憶體。

## 網頁控制台

啟動花花後，瀏覽器會自動開啟控制台（網址 `http://127.0.0.1:7860`，可用 `--host`／`--port` 改，`--no-web` 關掉）：

- **設定**：所有可調整的參數（音量、語速、temperature、人設語氣、聆聽與插話、裝置、模型…）。改「即時」參數立刻生效；改「需重啟」參數會自動重建管線。
- **記憶**：檢視目前的對話、匯出或清除；超過 `recent_turns` 的舊訊息會自動濃縮成背景摘要（每新增 8 則重建一次）。
- **碎碎念**：`config.toml` 的 `[idle_chat]` 啟用後，花花安靜超過 `timeout_s` 會主動說一句話；控制台可直接開關與改時間。
- **人設／語氣**：`config.toml` 的 `[llm] persona` 即時生效，也能在控制台直接改。
- **音色**：CosyVoice 模式可切換 `voice/active.json` 內的候選音色，並即時編輯 `voice/style.txt`。要換成自己的聲線，在控制台「上傳參考音檔」：一段 **8～12 秒、單一說話者、乾淨無殘響的 WAV**（檔名、腔調、音色都會跟著參考音走），附上與內容一字不差的逐字稿，套用後立即生效；重啟後也會沿用。
- **回音延遲校準**：一鍵播放掃頻音並量測「輸出→輸入」的實際延遲，自動寫入 `aec.delay_ms` 並即時套用。

設定儲存在 `data/settings.json`（覆蓋 `config.toml` 的基底值），刪掉該檔即回到 `config.toml` 設定。Web UI 需要 `fastapi` 與 `uvicorn`：`pip install -e .[ui]`。

## 切換 TTS 引擎

編輯 `config.toml` 的 `[tts]` 區段：

```toml
[tts]
backend = "kokoro"      # 快速預設
# backend = "cosyvoice" # 角色聲音，使用 voice/style.txt 的風格

base_url = "http://127.0.0.1:50001"   # kokoro
# base_url = "http://127.0.0.1:50000" # cosyvoice
```

改完後先執行 `stop-kokoro.cmd`（或 `stop-cosyvoice.cmd`），再重新執行 `run.cmd`。

## 測試 Qwen 與朗讀

```powershell
./run.cmd --text "我回來了"
```

只看文字、不朗讀：

```powershell
./run.cmd --text "我回來了" --no-speak
```

要量測 TTS server 的首音延遲與 RTF：

```powershell
python tools/benchmark_stream.py --url http://127.0.0.1:50001/v1/tts
```

## 開始持續聆聽

```powershell
./run.cmd
```

按 `Ctrl+C` 停止花花。對話文字會存放在 `data/flower.db`，原始麥克風音訊不會寫入硬碟。

TTS server 會繼續留在顯卡上，讓下次立即可用。要釋放顯示記憶體，雙擊 `stop-kokoro.cmd` 或 `stop-cosyvoice.cmd`。

## 聲音風格

CosyVoice3 模式會套用 `voice/style.txt`，讓聲音偏年輕、可愛、輕快並略帶調皮。修改文字後，先執行 `stop-cosyvoice.cmd`，再重新執行 `run.cmd`。

Kokoro 模式使用 `zf_001` 中文女聲，`choose-voice.cmd` 的六個候選樣本是給 CosyVoice3 參考用的。

服務啟動時會先暖機一次，並在每句開始前重設串流音塊，避免常駐一段時間後第一段聲音愈來愈慢。

## 裝置設定

目前 `config.toml` 使用：

```toml
input_device = "INPUT (2- Volt 1)"
input_hostapi = "Windows WASAPI"
input_channel = 0
```

裝置編號不會寫死，重新插拔 Volt 1 後仍會依名稱尋找。列出所有裝置：

```powershell
./run.cmd --list-devices
```

`output_device` 留空時使用 Windows 目前的預設輸出。要固定輸出到花花的音效卡，可把裝置名稱的一部分填入 `config.toml`。

## 回音與插話設定

目前設定為不可打斷：

```toml
[interaction]
barge_in_enabled = false
```

即使 AEC3 效果不理想，花花說話期間也不會把自己的聲音當成插話。之後完成實際環境校準，才建議改回 `true`。

目前 AEC3 的播放延遲預估是 90 ms。換喇叭、改 Windows 緩衝或改接線後，如果花花偶爾把自己的聲音當成你，可在 `config.toml` 微調，或直接在網頁控制台按「開始校準」自動量測：

```toml
[aec]
delay_ms = 90
```

校準會播放掃頻音並量測實際延遲後自動寫入。建議一次調整 10～20 ms。真正的聲學回音消除仍受喇叭音量、麥克風距離與房間反射影響。

## 本機測試

```powershell
python -m unittest discover -s tests -v
```

## 疑難排解：爆音

播放端把一輪對話的所有句子送進同一個輸出串流，避免逐句開關 WASAPI 造成尾音截斷；佇列暫時空著時會餵入靜音框維持裝置緩衝，並在分塊接縫做 20 ms 交叉淡化。若仍聽到爆音，可把實際送給喇叭的波形錄下來分析：

```powershell
$env:FLOWER_TTS_DUMP = "$PWD\data\tts-dump.npy"
python -m talking_flower --text "跟我說說你今天過得怎麼樣，多說幾句。"
```

然後用 numpy 檢查 dump：相鄰樣本跳變應低於約 0.2（若出現 0.5 以上跳變代表接縫破音），零值段只應出現在句子之間。

## 安裝與環境

本專案需要以下本機元件：

1. **Python 3.13**（conda 或 venv），安裝 `pip install -e .`
2. **TTS 環境**（二選一，可都裝）：
   - Kokoro：`pip install kokoro fastapi uvicorn torch`（CUDA 版 torch）
   - CosyVoice：參考 `third_party/CosyVoice` 的依賴（`tools/` 內有 `cosyvoice-windows-runtime.txt`）
3. **CosyVoice 模型**（只用 CosyVoice 模式才需要）：
   - 把 CosyVoice 程式碼與 `pretrained_models/Fun-CosyVoice3-0.5B`、`asset/zero_shot_prompt.wav` 放到 `third_party/CosyVoice`（約 8.3 GB，已 gitignore）
4. **llama.cpp**（或任何 OpenAI 相容 server）在 `127.0.0.1:8080` 提供 Qwen 模型
5. **Volt 1 音效卡**（或把 `config.toml` 改成其他輸入裝置）

啟動腳本會依序尋找 `FLOWER_KOKORO_PYTHON`／`FLOWER_TTS_PYTHON` 環境變數、本機固定路徑、PATH 上的 `python`。若你的環境不同，設定環境變數即可。

`voice/candidates/` 的六個聲音樣本來自 MatrixStudio/TTS-SCDuFSC，授權為 **CC BY-NC-ND 4.0**，因此未隨 repo 散布；`choose-voice.cmd` 功能需要自行取得這些樣本。

`native/THIRD-PARTY-NOTICES.txt` 內含隨附 WebRTC APM 元件的授權資訊；`native/webrtc-apm.dll` 二進位檔未隨 repo 散布。

## 目前仍可加強

- 用你指定的聲音樣本取代 CosyVoice 官方測試聲音。
- 依實際喇叭與 Volt 1 擺位微調 AEC 延遲和插話門檻（校準工具已就緒）。
- 加入 Live2D 立繪或視覺回饋。
- 為多個句子之間的停頓做「下一句預生成」，讓長回答的句子間距更短。
