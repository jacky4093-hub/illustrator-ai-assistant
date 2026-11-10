# 第一階段測試報告

測試日期：2026-11-10

測試環境：Claude Code 雲端容器，Linux x86_64。**不是** macOS iMac，也**不是** Windows。

| 項目 | 版本 |
|---|---|
| Claude Code | 2.1.322 |
| Python | 3.13.18（numpy 2.5.3、Pillow 12.3.0，皆為環境預裝） |
| Node.js | v22.23.4（Playwright 1.63.0 + Chromium，環境預裝） |
| Git | 2.43.0 |

本次沒有安裝任何套件，也沒有修改全域設定。

## 結果

| 項目 | 結果 | 證據 |
|---|---|---|
| Plugin manifest 驗證 | PASS | `claude plugin validate . --strict` 輸出 `Validation passed` |
| Skill frontmatter 驗證 | PASS | `claude plugin validate skills` 輸出 `Validation passed` |
| 自動化單元／整合測試（21 項） | PASS | `python3 -m unittest discover -s tests -v` 輸出 `Ran 21 tests … OK` |
| 圖片尺寸與比例保留 | PASS | 240×160 原圖對應 SVG `viewBox="0 0 240 160"`，preview.png 為 240×160 |
| 影像分類（4 張自製圖） | PASS | flat×2、gradient、photographic 皆分類正確 |
| 未簡化描邊為像素精確 | PASS | 內部渲染後的標籤圖與原標籤圖完全一致 |
| SVG 可解析，且不含嵌入點陣圖 | PASS | 驗證器會拒絕 `<image>`、`data:`、錯誤比例及沒有 path 的 SVG |
| Chromium 獨立渲染 | PASS | Chromium 與內部 rasterizer 平均差異 < 1/255 |
| 平面圖形還原度 | PASS | flat_shapes：像素吻合 0.988、ΔE 0.43、最小 IoU 0.988；ring_with_hole 也通過 |
| 漸層還原 | **FAIL（未還原）** | 數值門檻雖然通過（ΔE 1.93），但輸出的是 24 條平色帶，不是向量漸層。報告會標示 `NOT RESTORED` |
| 照片／雜訊圖 | FAIL（預期如此） | 所有區塊都小於最小面積，沒有產生 path。驗證器判定 FAIL，失敗原因記錄在 failures |
| 有限次修正迴圈 | PASS | 次數上限為 3（硬上限 5）。參數無法再調整時提前停止 |
| 人工確認流程 | PASS | `vf.py review` 會寫入 APPROVED／REJECTED，`ai_file` 仍維持 NOT VERIFIED |
| 產生的 .jsx 語法 | PASS（僅 JS 語法） | 經過 Node `new Function` 解析。這不代表 ExtendScript／Illustrator 相容 |
| .jsx 在 Illustrator 執行 | NOT VERIFIED | 從未執行過 |
| .ai 檔儲存 | NOT VERIFIED | 從未產生過 |
| Illustrator SVG／PNG 匯出 | NOT VERIFIED | 從未執行過 |
| Windows 10/11 執行 | NOT VERIFIED | 無 Windows 環境 |
| macOS 執行 | NOT VERIFIED | 本次在 Linux 容器執行 |
| 文字辨識 | NOT VERIFIED | 尚未實作 |

## 已知弱點

- 「漸層」圖在數值上會 PASS，所以數值門檻本身不足以判斷漸層是否還原。目前靠分類結果標示 `gradients: NOT RESTORED`，並強制人工確認。
- 三角形底角這類與斜邊相鄰的尖角，可能偏移不到 1 px。
- 範例 `.jsx` 內嵌的是本測試容器的 Linux 路徑。在 Windows 上必須重新執行 `vf.py run`，產生的 `.jsx` 才會帶正確路徑。
