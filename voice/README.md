# 花花聲音設定

`style.txt` 是 CosyVoice3 每句都會使用的角色語氣。

目前仍以 CosyVoice 官方示範音檔作為基礎聲線。若要讓角色聲音明顯改變，請準備一段 8～12 秒、單一說話者、無音樂與殘響的 WAV，並保留完全一致的逐字稿；之後可替換啟動設定中的 `promptWav` 與 `prompt-text`。

雙擊專案根目錄的 `choose-voice.cmd`，會依序試播六個錄音室中文女聲並讓你選擇。候選音訊來自 MatrixStudio/TTS-SCDuFSC，授權為 CC BY-NC-ND 4.0，限非商業測試使用。

修改 `style.txt` 後，要先執行 `stop-cosyvoice.cmd`，再重新執行 `run.cmd` 才會套用。
