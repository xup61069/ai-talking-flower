# Changelog

本專案所有重要變更皆記錄於此。
格式基於 [Keep a Changelog](https://keepachangelog.com/zh-TW/1.1.0/)，版本遵循 [語意化版本](https://semver.org/lang/zh-TW/)。

## [Unreleased]

### 規劃中
- 技能插件化（skills/ registry）+ 中央氣象署天氣 skill
- 真串流 ASR partial 上屏
- 喚醒詞模式、向量記憶檢索、情緒語音標籤

## [0.1.0] - 2026-08-22

首個正式版本。Windows 本機即時語音陪伴角色「花花」：收音 → AEC → VAD → ASR → 直達指令/LLM → 串流 TTS，全離線。

### 新增
- **語音管線**：Volt 1 WASAPI 收音、WebRTC AEC3 回音消除（含掃頻校準 + ERLE 估計）、TEN VAD（能量式備援）、FunASR Streaming Paraformer 繁中辨識（process 級模型快取）
- **大腦**：llama.cpp OpenAI 相容串流（Qwen）、SpeechChunker 智慧斷句（soft_split 預設開啟）
- **聲音**：Kokoro / CosyVoice3 雙引擎 HTTP PCM 串流 TTS、Windows SAPI 備援；20ms 交叉淡化防爆音；soxr 有狀態重採樣（scipy overlap-save 備援）；播放端真實 RMS 回傳驅動 UI 音波與嘴型
- **直達指令**：時間報時、相對/絕對時間提醒（「明天早上八點半」「每天晚上九點」，支援每日重複排程）、性格切換、音量語速調整——全中文數字解析（二十五分鐘、一個半小時）
- **記憶**：SQLite 對話歷史、關鍵字搜尋、自動摘要持久化（data/summary.txt 重啟不丟）
- **網頁控制台**：FastAPI + WebSocket 即時事件流、雙層設定覆蓋（config.toml + settings.json，schema 版本化遷移）、4 性格預設持久化、Token 認證（SHA256）、AEC 一鍵校準、CosyVoice 音色上傳熱載、玻璃質感主題 ×4、戳戳互動、HUD 延遲指標 + TTFA sparkline、效能歷史 API
- **工程**：74 項單元測試（含 UI DOM id 契約、重採樣連續性 RMS<1%、執行緒安全 bus）、CI 三層（lint/core/heavy）、TTS server 守護程序（健康輪詢自動拉起）、AEC DLL 交付腳本（SHA256 校驗）

### 修復（四輪體檢共 22 項）
- CosyVoice 熱載 `global` 缺失導致 style 無效
- AEC 校準相關方向反轉（峰值繞環被砍）
- 回答期間 AEC capture 中斷致濾波器失配
- 直達指令污染 LLM 記憶、音量/人設重啟丟失
- 校準掃頻誤觸發自問自答（manual_busy 閘門）
- StatusBus 跨執行緒 publish 的 asyncio.Queue 競態
- soxr 失敗回傳錯誤取樣率音訊（24k 當 48k 播）
- 其餘詳見 git log（cee411d..3e567ef）

### 安全
- Web 控制台預設綁 127.0.0.1；`--host 0.0.0.0` 時警告並建議啟用 Token
- Token 以 SHA256 儲存，明碼不落盤

[Unreleased]: https://github.com/xup61069/ai-talking-flower/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/xup61069/ai-talking-flower/releases/tag/v0.1.0
