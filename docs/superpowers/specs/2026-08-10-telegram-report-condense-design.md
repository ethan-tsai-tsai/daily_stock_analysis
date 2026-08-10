# 個股日報推播精簡化設計

- 日期：2026-08-10
- 狀態：待實作
- 影響範圍：本 fork 的 Telegram 每日推播（個股日報）

## 問題

每日推播的個股日報過長。實測 6 檔（NVDA,MSFT,GOOGL,AAPL,PLTR,MRNA）會被切成
**6 則 Telegram 訊息**（`_send_telegram_chunked` 以 4096 字元為上限分段），
其中大盤複盤約 1 則、個股日報約 5 則。使用者在手機上不會讀完。

使用者要的是：**每檔的結論 + 一句話理由 + 關鍵價位**，其餘全部不要。

## 已查證的事實

以下每一點都對應到程式碼位置，實作時不要重新猜測。

### `REPORT_TYPE` 在匯總路徑下沒有精簡效果

`src/notification.py:413-423` 的 `generate_aggregate_report()` 只有兩條分岔：

```python
normalized_type = self._normalize_report_type(report_type)
if normalized_type == ReportType.BRIEF:
    return self.generate_brief_report(results, report_date=report_date)
return self.generate_dashboard_report(results, report_date=report_date)
```

`ReportType.SIMPLE` / `ReportType.FULL` 在整個 `notification.py` 從未出現。
因此 workflow 目前設定的 `REPORT_TYPE=simple`（`.github/workflows/00-daily-analysis.yml:409`）
與 `full` 產出完全相同 —— 現在跑的其實是完整版。

### `brief` 不含任何價位，不符需求

`generate_brief_report`（`src/notification.py:1844-1902`）與 `templates/report_brief.j2`
每檔只輸出一行：訊號 emoji + 訊號文字 + 分數 + 一句話結論（**截斷 60 字**）。
`ideal_buy` / `secondary_buy` / `stop_loss` / `take_profit` / 倉位 / 支撐壓力全部不輸出。

### `REPORT_SUMMARY_ONLY` 會連價位一起砍掉

`src/notification.py:1313` 用 `if not self._report_summary_only:` 把 L1314-1572
的整段個股詳情包起來跳過，價位與詳情是綁在一起被砍的，無法只留價位。

另外 `generate_brief_report` 內部硬寫死 `summary_only=False`（L1870），
所以 `REPORT_TYPE=brief` 時這個開關完全無效。

### Jinja2 renderer 有安全的短路與 fallback

`src/notification.py:1260-1273`：

```python
if getattr(config, 'report_renderer_enabled', False) and results:
    from src.services.report_renderer import render
    out = render(platform='markdown', results=results, report_date=report_date,
                 summary_only=self._report_summary_only, extra_context={...})
    if out:
        return out
# 以下為原本的 Python 組字串邏輯
```

`render()`（`src/services/report_renderer.py`）在三種情況回傳 `None`：
jinja2 未安裝、模板檔不存在、`template.render()` 拋例外（整段包在 try/except）。
回傳 `None` 就往下走原邏輯。

**因此模板寫壞的最壞後果是退回原本的長報告，不會讓推播失敗。**

### 模板目錄可外移

`_resolve_templates_dir()`（`report_renderer.py`）讀 `config.report_templates_dir`，
相對路徑則以專案根目錄為基準；`FileSystemLoader(str(templates_dir))` +
`template_name = f"report_{platform}.j2"`。所以另開目錄放同名模板即可完全接管，
不必覆寫上游 `templates/` 底下任何檔案。

### 模板可用的 context

由 `report_renderer.py` 的 `context` dict 提供，實作時只能用這些：

- `report_date`、`report_timestamp`
- `enriched` —— **已按 `sentiment_score` 由高到低排序**，每個元素為
  `{result, signal_text, signal_emoji, stock_name, localized_operation_advice, localized_trend_prediction}`，
  其中 `stock_name` 已經過 `_escape_md`（跳脫 `*` 與 `_`）
- `buy_count` / `hold_count` / `sell_count`、`market_status_line`
- `labels`（依 `report_language` 取得的欄位標籤）
- `clean_sniper(val)` —— 去掉「理想买入点：」這類前綴，`None` 回 `"N/A"`
- `show_llm_model`、`models_used`

價位路徑：`result.dashboard.battle_plan.sniper_points.{ideal_buy, stop_loss, take_profit}`。
一句話結論：`result.dashboard.core_conclusion.one_sentence`，取不到時退回 `result.analysis_summary`。

### Telegram 不支援 Markdown 表格

`telegram_sender.py` 用 `parse_mode: "Markdown"`（legacy）。上游模板大量使用
`| 欄 | 欄 |` 表格，在 Telegram 上不會被渲染成表格，而是一堆散落的 pipe 符號。
新模板一律用單行格式，順帶解掉這個既有問題。

### workflow 只傳固定幾個 env

`.github/workflows/00-daily-analysis.yml:409-412` 逐項列舉 env，
`REPORT_RENDERER_ENABLED` 與 `REPORT_TEMPLATES_DIR` 不在其中。
**只在 repo Variables 設定不會生效**，必須改 workflow。

## 設計

### 1. 新增 `templates_tw/report_markdown.j2`

新目錄，不碰上游 `templates/`。模板**不 import `_macros.j2`**，因此該目錄只需這一個檔案。

輸出結構：

```
🎯 {報告日期} {dashboard_title}
共 {N} 檔 | 🟢買:{buy_count} 🟡觀望:{hold_count} 🔴賣:{sell_count}
{market_status_line}（有才輸出）

{emoji} *{股名}({代碼})* | {訊號文字} | {分數} | {趨勢}
{一句話結論}
🎯{進場} ／ 🛑{停損} ／ 🎊{目標}

（每檔重複，已按分數高到低）

產出時間：{report_timestamp}
分析模型：{models_used}（show_llm_model 為真才輸出）
```

規則：

- 標籤一律取自 `labels`（`score_label`、`ideal_buy_label`、`stop_loss_label`、
  `take_profit_label` 等），不自行硬寫中文字串，避免與推播出口的 OpenCC 轉換打架。
- 價位三個值只要**全部**是 `N/A` 或空，就整行不輸出，不留下空殼。
- 一句話結論若為空則該行不輸出。
- 不使用任何 Markdown 表格、不使用 `#` 標題（Telegram 會剝掉）。
- 忽略 `summary_only`：這個模板本身已經是摘要，不再受該開關影響。

**移除的區塊**：數據透視（均線/乖離/支撐壓力/量能/籌碼）、訊號歸因、
多策略綜合、階段決策、財務摘要、風險提示與正面催化清單、歷史對比、market snapshot。

預估長度：6 檔約 20 行、900 字元以內，**壓到 1 則訊息**。

### 2. workflow 加兩行 env

`.github/workflows/00-daily-analysis.yml`，接在 L411 附近的「運行配置」區塊：

```yaml
REPORT_RENDERER_ENABLED: ${{ vars.REPORT_RENDERER_ENABLED || 'true' }}
REPORT_TEMPLATES_DIR: ${{ vars.REPORT_TEMPLATES_DIR || 'templates_tw' }}
```

預設值直接給 `true` / `templates_tw`，不需要另外去 repo 設 Variables；
若日後想臨時退回原本的完整報告，設 `vars.REPORT_RENDERER_ENABLED=false` 即可，不用改碼。

### 3. 失敗行為

| 情況 | 結果 |
|------|------|
| 模板語法錯誤 | `render()` 捕捉例外回 `None` → 退回原本完整報告 |
| 模板檔找不到 | `render()` 回 `None` → 退回原本完整報告 |
| 某檔缺 `dashboard` | 該檔只出標題行與結論行，價位行省略 |
| 想臨時關掉 | repo Variables 設 `REPORT_RENDERER_ENABLED=false` |

不需要額外寫錯誤處理，上游的短路邏輯已經涵蓋。

## 驗證計畫

**本地無法完整跑 `render()`** —— 它 import 了 `src.analyzer`、`src.config` 等模組，
會拉進 pandas / numpy / litellm 整條依賴鏈（`requirements.txt` 3.8K），
為了驗證一個模板裝這些不划算。改用兩段式：

1. **本地純 Jinja2 煙霧測試**：只 `pip install jinja2`，自建一份與
   `report_renderer.py` 的 `context` 同形狀的假 context（含一檔有完整 dashboard、
   一檔缺 `battle_plan`、一檔 sniper 全為 `N/A`），直接用
   `Environment(loader=FileSystemLoader('templates_tw'))` 渲染。
   斷言：能渲染成功、輸出長度 < 4096、三種邊界情況都不留空殼行。
   **測試腳本放 scratchpad，不進 fork**（它依賴手寫的假 context，
   與線上真實資料脫鉤，留在 repo 只會變成需要跟著上游維護的死代碼）。
2. **實跑驗證**：`workflow_dispatch` 手動觸發一次，確認 Telegram 只收到
   1 則個股日報（大盤複盤仍是獨立的另 1 則），且價位與結論都在。

第 1 步驗證模板語法與欄位路徑，第 2 步才是真正的驗收 —— 因為假 context
無法保證與線上 `AnalysisResult` 完全一致。

## 範圍界線（本次不做）

- **不動大盤複盤**。它是獨立推送路徑（`main.py:900-989`，`main.py:943` 記錄推送成功），
  產出 `reports/market_review_YYYYMMDD.md` 實測 3993 bytes ≈ 1 則訊息，長度合理。
  做完本案後總量從 6 則降到約 2 則；若之後仍嫌長再單獨處理。
- **不做 HTML 報告**。專案沒有 HTML 模板（`templates/` 只有 4 個 `.j2`，全是 Markdown），
  Telegram sender 也不支援 `sendDocument`（只有 `sendMessage` L54/L87 與 `sendPhoto` L355，
  全 repo 對 `sendDocument` 零命中）。要做等於自建模板加自架 host，
  而 fork 是 public repo，用 GitHub Pages 會公開自選股與分析內容。投報率不符。
- **不動 `REPORT_TYPE`**，維持 `simple`。本設計走 renderer 短路，與該設定無關。
- **不改上游任何既有檔案**（除了 workflow 那兩行）。

## 上游同步維護清單

本 fork 相對上游的改動，同步時需留意：

| 路徑 | 性質 | 衝突風險 |
|------|------|----------|
| `src/notification_sender/_zh_tw.py` | 新增檔 | 無 |
| `src/notification_sender/telegram_sender.py` | 既有檔 +2 行（OpenCC 轉換） | 有 |
| `templates_tw/report_markdown.j2` | 新增檔 | 無 |
| `.github/workflows/00-daily-analysis.yml` | 既有檔 +2 行（本案） | 有 |
| `docs/superpowers/specs/` | 新增目錄 | 無 |

需要人工留意的只有兩個既有檔、共四行。
