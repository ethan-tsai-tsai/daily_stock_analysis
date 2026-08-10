# -*- coding: utf-8 -*-
"""大盤複盤推播內容壓縮：只保留開頭那句摘要。

零依賴純函式模組：不得 import 專案任何其他模組，方便獨立測試。
"""

_SIMPLIFIED_KEYWORD = "大盘复盘"
_TRADITIONAL_KEYWORD = "大盤複盤"

# 標題只會出現在內容最前面幾行（metadata 隱藏行 + 外層標題 + 內層 H2 標題），
# 限制搜尋範圍可避免誤判本文深處剛好出現同樣字樣的內容。
_TITLE_SEARCH_LIMIT = 8

# MARKET_REVIEW_REGION 設多市場（例如 "cn,us"）時，run_market_review() 會把各市場的
# 複盤逐一組字串再合併成一份推播內容。合併時插入的分隔文字（含開頭的 "> "）就是這裡的字串。
# 出處：src/core/market_review.py:147 -- _get_market_review_text() zh 語言分支的 "separator" 欄位。
# 實際組裝方式見 src/core/market_review.py:262：
#   review_report = f"\n\n---\n\n{review_text['separator']}\n\n".join(parts)
# 這段分隔文字本身也以 "> " 開頭，若不特別偵測，下方逐行掃描邏輯會把它誤判成第一個市場的
# 摘要行而提早 return，靜默丟掉第二個以後市場的完整內容。
# 若日後上游調整這段文字，務必同步更新此常數（同步時請比對上述行號）。
_MULTI_MARKET_SEPARATOR = "> 以下为下一市场大盘复盘"


def _contains_market_review_keyword(text: str) -> bool:
    return _SIMPLIFIED_KEYWORD in text or _TRADITIONAL_KEYWORD in text


def condense_market_review(content: str) -> str:
    """大盤複盤推播只留開頭那句摘要；不是大盤複盤、或抓不到摘要時原樣回傳。

    行為：
    - 標題行（開頭幾行內）含「大盘复盘」（簡體，寬鬆也接受繁體「大盤複盤」）才視為大盤複盤。
    - 內容為多市場合併（MARKET_REVIEW_REGION 設多個市場時）→ 原樣回傳完整內容，不壓縮。
      多市場的正確壓縮語意（每個市場各留一句？標題怎麼組？）沒有被規格定義過，寧可不壓縮
      也不要靜默漏掉第一個市場以外的摘要。
    - 標題行之後，略過同樣含關鍵字的重述標題行（例如內文自帶的 H2 標題），
      在遇到第一個「不含關鍵字」的 `#` 開頭子標題之前，找第一個 `>` 開頭的摘要行。
    - 找不到摘要、或不是大盤複盤 → 原樣回傳完整內容（fail-safe，絕不拋例外或回傳空字串）。
    """
    if not content:
        return content

    if _MULTI_MARKET_SEPARATOR in content:
        # 多市場合併內容：逐行掃描邏輯只認得單一市場的結構，會把分隔文字本身
        # （以 "> " 開頭）誤判成摘要行而提早 return，靜默丟掉後面市場的內容。
        # fail-safe：原樣回傳，不做壓縮。
        return content

    lines = content.split("\n")

    title_idx = None
    for idx, line in enumerate(lines[:_TITLE_SEARCH_LIMIT]):
        if _contains_market_review_keyword(line):
            title_idx = idx
            break

    if title_idx is None:
        return content

    title_line = lines[title_idx].strip()

    for line in lines[title_idx + 1:]:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            if _contains_market_review_keyword(stripped):
                # 內文自帶的重述標題（例如 "## 2026-08-10 大盘复盘"），繼續往下找摘要
                continue
            break  # 碰到真正的子標題卻還沒看到摘要，放棄
        if stripped.startswith(">"):
            summary = stripped[1:].strip()
            if summary:
                return f"{title_line}\n{summary}"
            break
        # 標題與摘要之間出現非預期的內容，保守放棄
        break

    return content
