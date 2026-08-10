# 個股日報推播精簡化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把每日 Telegram 個股日報從約 5 則訊息壓到 1 則，只保留「訊號＋分數＋趨勢／一句話結論／進場-停損-目標價」。

**Architecture:** 不改上游任何產生報告的 Python 程式碼。改用上游既有但預設關閉的 Jinja2 renderer 短路機制（`src/notification.py:1260-1273`），把 `REPORT_TEMPLATES_DIR` 指向新目錄 `templates_tw/`，在裡面放一份自己的 `report_markdown.j2` 接管輸出。模板寫壞時 `render()` 回 `None`，自動退回原本的完整報告。

**Tech Stack:** Jinja2（上游已依賴）、GitHub Actions、Python 3。

## Global Constraints

以下規則來自 spec，每個 task 都適用：

- **不修改上游既有檔案**，唯一例外是 `.github/workflows/00-daily-analysis.yml` 加兩行 env。
- **不動 `REPORT_TYPE`**，維持 `simple`。
- **不動大盤複盤**（`main.py` 的 market review 路徑）。
- **不使用 Markdown 表格、不使用 `#` 標題** —— Telegram legacy Markdown 不渲染表格，`#` 會被 `telegram_sender._convert_to_telegram_markdown()` 剝掉。
- **粗體用 `**text**`** —— `_convert_to_telegram_markdown()` 會轉成 Telegram 的 `*text*`。
- **文字標籤一律取自 `labels.*`**，不自行硬寫中文字串（推播出口有 OpenCC `s2twp` 轉換，硬寫繁中會與之打架）。
- **模板不得 import `_macros.j2`**，`templates_tw/` 只放一個檔案。
- 目標長度：6 檔的輸出 **< 4096 字元**（Telegram 單則上限）。

## File Structure

| 路徑 | 動作 | 責任 |
|------|------|------|
| `templates_tw/report_markdown.j2` | 新增 | 唯一的報告版型定義 |
| `.github/workflows/00-daily-analysis.yml` | 修改（+2 行） | 開啟 renderer、指向新模板目錄 |
| scratchpad 的 `smoke_render.py` | 新增（**不進 repo**） | 純 Jinja2 煙霧測試 |

---

### Task 1: 精簡版 Markdown 模板

**Files:**
- Create: `templates_tw/report_markdown.j2`
- Test: `/private/tmp/claude-501/-Users-hong-Programming-tooling-hermes-personal/adea7e90-2be9-411b-8546-698c3392cd7d/scratchpad/smoke_render.py`（不 commit 進 repo）

**Interfaces:**
- Consumes: `src/services/report_renderer.py` 的 `render()` 所建構的 context。可用的變數僅限：
  `report_date`(str)、`report_timestamp`(str)、`results`(list)、
  `enriched`(list，已按 `sentiment_score` 由高到低排序，元素為 dict：
  `result` / `signal_text` / `signal_emoji` / `stock_name` / `localized_operation_advice` / `localized_trend_prediction`)、
  `buy_count`/`hold_count`/`sell_count`(int)、`market_status_line`(str)、
  `labels`(dict)、`clean_sniper`(callable)、`show_llm_model`(bool)、`models_used`(list[str])。
- `result` 物件用到的屬性：`.code`(str)、`.sentiment_score`(int)、`.dashboard`(dict|None)、`.analysis_summary`(str)。
- `clean_sniper(val)` 對 `None` 與空字串一律回傳字串 `"N/A"`。
- Produces: 檔案 `templates_tw/report_markdown.j2`。Task 2 靠 `REPORT_TEMPLATES_DIR=templates_tw` 找到它。

- [ ] **Step 1: 建立 scratchpad 測試腳本（此時模板還不存在，預期失敗）**

在 scratchpad 建立 `smoke_render.py`：

```python
# -*- coding: utf-8 -*-
"""templates_tw/report_markdown.j2 的純 Jinja2 煙霧測試。
不 import 專案任何模組（會拉進 pandas/numpy/litellm 整條依賴鏈），
改用手寫的假 context —— 形狀複製自 src/services/report_renderer.py 的 context dict。
"""
import re
import sys
from types import SimpleNamespace
from jinja2 import Environment, FileSystemLoader, select_autoescape

TEMPLATES_DIR = "templates_tw"


def clean_sniper(val):
    """複製自 report_renderer.py 的 _clean_sniper_value，保持行為一致。"""
    if val is None:
        return "N/A"
    if isinstance(val, (int, float)):
        return str(val)
    s = str(val).strip() if val else ""
    if not s or s == "N/A":
        return s or "N/A"
    for prefix in ("理想买入点：", "止损位：", "目标位：",
                   "理想买入点:", "止损位:", "目标位:"):
        if s.startswith(prefix):
            return s[len(prefix):]
    return s


LABELS = {
    "dashboard_title": "决策仪表盘",
    "analyzed_prefix": "已分析",
    "stock_unit": "只",
    "buy_label": "买入",
    "watch_label": "观望",
    "sell_label": "卖出",
    "score_label": "评分",
    "ideal_buy_label": "理想买入点",
    "stop_loss_label": "止损位",
    "take_profit_label": "目标位",
    "generated_at_label": "生成时间",
    "analysis_model_label": "分析模型",
}


def make_entry(code, score, emoji, signal, dashboard, summary):
    return {
        "result": SimpleNamespace(
            code=code, sentiment_score=score,
            dashboard=dashboard, analysis_summary=summary,
        ),
        "signal_text": signal,
        "signal_emoji": emoji,
        "stock_name": code,
        "localized_operation_advice": signal,
        "localized_trend_prediction": "偏多",
    }


# 案例 1：完整 dashboard，三個價位都有值
FULL = make_entry("NVDA", 82, "🟢", "买入", {
    "core_conclusion": {"one_sentence": "资料中心需求延续，回档至均线可分批布局。"},
    "battle_plan": {"sniper_points": {
        "ideal_buy": "理想买入点：178.5", "stop_loss": "168.0", "take_profit": "205.0",
    }},
}, "备用摘要")

# 案例 2：dashboard 為 None（缺整個 battle_plan 與 core_conclusion）
NO_DASH = make_entry("MRNA", 41, "🔴", "卖出", None, "管线进度落后，评价面缺乏支撑。")

# 案例 3：sniper 三值皆 None → 價位行必須整行省略
NO_PRICE = make_entry("PLTR", 60, "🟡", "观望", {
    "core_conclusion": {"one_sentence": "估值偏高，等待回档。"},
    "battle_plan": {"sniper_points": {
        "ideal_buy": None, "stop_loss": None, "take_profit": None,
    }},
}, "备用摘要")

ENRICHED = [FULL, NO_PRICE, NO_DASH]

context = {
    "report_date": "2026-08-10",
    "report_timestamp": "2026-08-10 18:00:00",
    "results": [e["result"] for e in ENRICHED],
    "enriched": ENRICHED,
    "buy_count": 1, "hold_count": 1, "sell_count": 1,
    "market_status_line": "美股：三大指数收红，风险偏好回升。",
    "labels": LABELS,
    "clean_sniper": clean_sniper,
    "show_llm_model": True,
    "models_used": ["nemotron-3-ultra-550b-a55b:free"],
    "summary_only": False,
}

env = Environment(loader=FileSystemLoader(TEMPLATES_DIR),
                  autoescape=select_autoescape(default=False))
out = env.get_template("report_markdown.j2").render(**context)

print(out)
print("=" * 40)
print(f"長度：{len(out)} 字元")

failures = []

# 1. 長度必須遠低於 Telegram 單則上限
if len(out) >= 4096:
    failures.append(f"輸出 {len(out)} 字元，超過 Telegram 單則上限 4096")

# 2. 三檔的代碼都要出現
for code in ("NVDA", "PLTR", "MRNA"):
    if code not in out:
        failures.append(f"缺少個股 {code}")

# 3. 有價位的那檔要出現三個數字
for value in ("178.5", "168.0", "205.0"):
    if value not in out:
        failures.append(f"缺少價位 {value}")

# 4. 「理想买入点：」前綴必須已被 clean_sniper 去掉
if "理想买入点：178.5" in out:
    failures.append("clean_sniper 的前綴沒有被去掉")

# 5. 沒有價位的兩檔不得留下空殼價位行（畫面上不該出現 N/A）
if "N/A" in out:
    failures.append("輸出含 N/A，代表沒有價位時仍印出了價位行")

# 6. 不得出現 Markdown 表格與 # 標題（Telegram 不支援）
if re.search(r"^\s*\|", out, re.M):
    failures.append("輸出含 Markdown 表格")
if re.search(r"^#", out, re.M):
    failures.append("輸出含 # 標題")

# 7. 不得有三個以上連續換行（Jinja 空白控制沒做好的徵兆）
if re.search(r"\n{3,}", out):
    failures.append("輸出有連續空行，Jinja 空白控制需修正")

if failures:
    print("\n❌ 失敗：")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)

print("\n✅ 全部通過")
```

- [ ] **Step 2: 執行測試，確認因模板不存在而失敗**

```bash
cd /Users/hong/Programming/tooling/daily_stock_analysis
python3 -m pip install --quiet jinja2
python3 /private/tmp/claude-501/-Users-hong-Programming-tooling-hermes-personal/adea7e90-2be9-411b-8546-698c3392cd7d/scratchpad/smoke_render.py
```

預期：`jinja2.exceptions.TemplateNotFound: report_markdown.j2`

- [ ] **Step 3: 寫模板**

建立 `templates_tw/report_markdown.j2`：

```jinja
🎯 {{ report_date }} {{ labels.dashboard_title }}
{{ labels.analyzed_prefix }} {{ results|length }} {{ labels.stock_unit }} | 🟢{{ labels.buy_label }}:{{ buy_count }} 🟡{{ labels.watch_label }}:{{ hold_count }} 🔴{{ labels.sell_label }}:{{ sell_count }}
{%- if market_status_line %}
{{ market_status_line }}
{%- endif %}
{% for e in enriched %}
{%- set dash = e.result.dashboard or {} %}
{%- set core = dash.get('core_conclusion') or {} %}
{%- set battle = dash.get('battle_plan') or {} %}
{%- set sniper = battle.get('sniper_points') or {} %}
{%- set one = core.get('one_sentence') or e.result.analysis_summary or '' %}
{%- set buy = clean_sniper(sniper.get('ideal_buy')) %}
{%- set stop = clean_sniper(sniper.get('stop_loss')) %}
{%- set target = clean_sniper(sniper.get('take_profit')) %}
{{ e.signal_emoji }} **{{ e.stock_name }}({{ e.result.code }})** | {{ e.signal_text }} | {{ labels.score_label }} {{ e.result.sentiment_score }} | {{ e.localized_trend_prediction }}
{%- if one %}
{{ one }}
{%- endif %}
{%- if buy != 'N/A' or stop != 'N/A' or target != 'N/A' %}
🎯{{ labels.ideal_buy_label }} {{ buy }} ／ 🛑{{ labels.stop_loss_label }} {{ stop }} ／ 🎊{{ labels.take_profit_label }} {{ target }}
{%- endif %}
{% endfor %}
*{{ labels.generated_at_label }}：{{ report_timestamp }}*
{%- if show_llm_model and models_used %}
*{{ labels.analysis_model_label }}：{{ models_used|join(', ') }}*
{%- endif %}
```

要點：所有 `{%- %}` 的 `-` 都是必要的空白控制，拿掉會讓每個 `{% set %}` 各留一行空白，六檔就會多出四十幾行。

- [ ] **Step 4: 執行測試，確認通過**

```bash
cd /Users/hong/Programming/tooling/daily_stock_analysis
python3 /private/tmp/claude-501/-Users-hong-Programming-tooling-hermes-personal/adea7e90-2be9-411b-8546-698c3392cd7d/scratchpad/smoke_render.py
```

預期：印出渲染結果、長度約 400–600 字元、最後一行 `✅ 全部通過`。

若第 7 項（連續空行）失敗，調整 `{%- %}` 的 `-` 位置，不要改測試。
若第 5 項（含 N/A）失敗，代表 `{%- if buy != 'N/A' ... %}` 的條件寫錯。

- [ ] **Step 5: Commit**

```bash
cd /Users/hong/Programming/tooling/daily_stock_analysis
git add templates_tw/report_markdown.j2
git commit -m "feat: 新增精簡版個股日報模板，壓縮 Telegram 推播長度"
```

---

### Task 2: 開啟 renderer 並指向新模板目錄

**Files:**
- Modify: `.github/workflows/00-daily-analysis.yml:411`（在此行之後插入）

**Interfaces:**
- Consumes: Task 1 產出的 `templates_tw/report_markdown.j2`。
- Produces: workflow 執行時 `REPORT_RENDERER_ENABLED=true`、`REPORT_TEMPLATES_DIR=templates_tw` 兩個環境變數，供 `src/config.py` 讀取。

- [ ] **Step 1: 確認插入點**

```bash
cd /Users/hong/Programming/tooling/daily_stock_analysis
sed -n '406,415p' .github/workflows/00-daily-analysis.yml
```

預期看到「運行配置」區塊，L409 為 `REPORT_TYPE`、L411 為 `REPORT_SHOW_LLM_MODEL`。

- [ ] **Step 2: 在 L411 之後插入兩行**

在 `REPORT_SHOW_LLM_MODEL` 那一行下面加入（注意縮排為 10 個空格，與相鄰行對齊）：

```yaml
          REPORT_RENDERER_ENABLED: ${{ vars.REPORT_RENDERER_ENABLED || 'true' }}
          REPORT_TEMPLATES_DIR: ${{ vars.REPORT_TEMPLATES_DIR || 'templates_tw' }}
```

預設值直接給 `true` / `templates_tw`，不需另外去 repo Variables 設定。日後要臨時退回原本的完整報告，在 repo Variables 設 `REPORT_RENDERER_ENABLED=false` 即可，不必改碼。

- [ ] **Step 3: 驗證 YAML 沒有被改壞**

```bash
cd /Users/hong/Programming/tooling/daily_stock_analysis
python3 -c "import yaml,sys; d=yaml.safe_load(open('.github/workflows/00-daily-analysis.yml')); print('YAML OK')"
grep -n "REPORT_RENDERER_ENABLED\|REPORT_TEMPLATES_DIR" .github/workflows/00-daily-analysis.yml
```

預期：印出 `YAML OK`，且 grep 命中兩行。
若 `yaml` 模組不存在，先 `python3 -m pip install --quiet pyyaml`。

- [ ] **Step 4: 確認只動了預期的兩行**

```bash
cd /Users/hong/Programming/tooling/daily_stock_analysis
git diff --stat .github/workflows/00-daily-analysis.yml
```

預期：`1 file changed, 2 insertions(+)`。若有刪除行數，代表誤改，還原重做。

- [ ] **Step 5: Commit**

```bash
cd /Users/hong/Programming/tooling/daily_stock_analysis
git add .github/workflows/00-daily-analysis.yml
git commit -m "feat: 啟用 Jinja2 renderer 並指向 templates_tw 模板目錄"
```

---

### Task 3: 實跑驗收

**Files:** 無（部署與驗證）

**Interfaces:**
- Consumes: Task 1 與 Task 2 的 commit。
- Produces: 一則實際的 Telegram 推播，作為驗收證據。

前置事實（已驗證，不需重查）：local clone 在 `/Users/hong/Programming/tooling/daily_stock_analysis`，
分支為 `main`，remote `origin` 指向 `ethan-tsai-tsai/daily_stock_analysis`，
且 `.github/workflows/00-daily-analysis.yml` 已含 `workflow_dispatch` 觸發條件。

- [ ] **Step 1: 推送**

```bash
cd /Users/hong/Programming/tooling/daily_stock_analysis
git push origin main
```

- [ ] **Step 2: 手動觸發一次**

```bash
cd /Users/hong/Programming/tooling/daily_stock_analysis
gh workflow run 00-daily-analysis.yml
sleep 30 && gh run list --workflow=00-daily-analysis.yml --limit 1
```

注意：完整跑 6 檔約需 19 分鐘（前次實測 1132 秒），且每跑一次消耗約 33 個 Tavily credits（月配額 1000）。**不要反覆觸發**。

- [ ] **Step 3: 確認 renderer 真的被使用**

執行結束後抓 log：

```bash
cd /Users/hong/Programming/tooling/daily_stock_analysis
gh run view --log --workflow=00-daily-analysis.yml 2>/dev/null | grep -i "Report render failed\|Report template not found\|消息块" | head -20
```

判讀：
- 出現 `Report render failed` 或 `Report template not found` → 模板沒被吃到，退回了長報告。檢查 `REPORT_TEMPLATES_DIR` 是否正確傳入。
- 個股日報若仍出現多個「消息块」→ 沒有壓縮成功。
- 理想結果：完全沒有 render 失敗訊息，且個股日報沒有分段。

- [ ] **Step 4: 人工確認 Telegram 實際收到的訊息**

請使用者確認手機上收到的內容：
1. 個股日報是否為單則、
2. 每檔是否都有結論與三個價位、
3. 繁中轉換是否正常（OpenCC `s2twp` 在推播出口作用）、
4. 是否還有殘留的 pipe 符號（表格殘骸）。

這一步無法自動化驗證，必須由使用者回報。

- [ ] **Step 5: 更新 spec 狀態並 commit**

把 spec 開頭的 `狀態：待實作` 改為 `狀態：已實作並驗收（YYYY-MM-DD）`：

```bash
cd /Users/hong/Programming/tooling/daily_stock_analysis
git add docs/superpowers/specs/2026-08-10-telegram-report-condense-design.md
git commit -m "docs: 標記個股日報精簡化已完成驗收"
git push origin main
```

---

## 風險與回退

| 風險 | 徵兆 | 回退方式 |
|------|------|----------|
| 模板欄位路徑與線上真實 `AnalysisResult` 不符 | 推播內容出現空白區塊 | repo Variables 設 `REPORT_RENDERER_ENABLED=false`，立刻退回原報告，不必改碼 |
| 模板渲染例外 | log 出現 `Report render failed` | 上游已自動 fallback，推播不會中斷；照 log 修模板 |
| 假 context 與線上不一致 | Task 1 測試過了但 Task 3 內容不對 | 這正是 Task 3 存在的理由；以 Task 3 為準 |

## 上游同步維護清單

完成後本 fork 相對上游的改動：

| 路徑 | 性質 | 衝突風險 |
|------|------|----------|
| `src/notification_sender/_zh_tw.py` | 新增檔 | 無 |
| `src/notification_sender/telegram_sender.py` | 既有檔 +2 行 | 有 |
| `templates_tw/report_markdown.j2` | 新增檔 | 無 |
| `.github/workflows/00-daily-analysis.yml` | 既有檔 +2 行 | 有 |
| `docs/superpowers/` | 新增目錄 | 無 |

需人工留意的只有兩個既有檔、共四行。
