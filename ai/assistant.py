"""AI智能问答（硅基流动API，兼容OpenAI格式）"""
import sys
import os
import time
import requests
from typing import Optional, List, Dict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.settings import get_settings, get_ai_config

# 会话历史（内存态，重启清空）
_history: List[Dict] = []


def build_stock_context(df, stock_name: str, days: int = 10) -> str:
    """从K线数据构建上下文"""
    if df is None or df.empty:
        return ""
    recent = df.tail(days)
    lines = [f"股票：{stock_name}，最近{len(recent)}个交易日K线："]
    for _, r in recent.iterrows():
        d = r["交易日期"].strftime("%Y-%m-%d") if hasattr(r["交易日期"], "strftime") else str(r["交易日期"])
        lines.append(
            f"{d} 开{r['开盘价']:.2f} 高{r['最高价']:.2f} "
            f"低{r['最低价']:.2f} 收{r['收盘价']:.2f} 量{int(r['成交量'])}"
        )
    return "\n".join(lines)


def build_news_context(news_items: list) -> str:
    """从资讯构建上下文"""
    if not news_items or "error" in (news_items[0] if news_items else {}):
        return ""
    lines = ["近期相关资讯："]
    for item in news_items[:8]:
        lines.append(f"- [{item.get('time', '')}] {item.get('title', '')}")
    return "\n".join(lines)


def ask_ai(question: str, context: str = "", keep_history: bool = True) -> str:
    """向大模型提问"""
    global _history
    cfg = get_ai_config()
    api_key = cfg["api_key"]
    model = cfg["model"]

    if not api_key:
        return f"请先在「设置」页填写 {cfg['provider_name']} 的 API Key（{cfg['site']} 获取）"

    messages = [{"role": "system",
                 "content": "你是一个专业的股票量化交易助手。用简洁清晰的中文回答，不要过度使用列表。"}]
    if context:
        messages.append({"role": "system", "content": f"背景信息：\n{context}"})

    if keep_history:
        messages.extend(_history[-10:])
    messages.append({"role": "user", "content": question})

    last_exc = None
    for attempt in range(3):
        try:
            resp = requests.post(
                cfg["url"],
                headers={"Authorization": f"Bearer {api_key}",
                         "Content-Type": "application/json"},
                json={"model": model, "messages": messages,
                      "temperature": 0.7, "max_tokens": 4096},
                timeout=90,
            )
            if resp.status_code == 200:
                msg = resp.json()["choices"][0]["message"]
                answer = (msg.get("content") or "").strip()
                if not answer:
                    reasoning = (msg.get("reasoning_content") or "").strip()
                    answer = reasoning or "（模型返回了空回复，请换个问法重试）"
                if keep_history:
                    _history.append({"role": "user", "content": question})
                    _history.append({"role": "assistant", "content": answer})
                    _history = _history[-20:]
                return answer
            # 429 限流：自动退避重试（免费模型高峰期常见）
            if resp.status_code == 429:
                if attempt < 2:
                    time.sleep(8 * (attempt + 1))
                    continue
                return (f"模型当前访问量过大，已自动重试3次仍被限流。\n"
                        f"——{cfg['provider_name']} 的免费模型在高峰期会限制并发，"
                        f"这不是你的 Key 或程序的问题。可以：①稍等一两分钟再问；"
                        f"②到「设置」页换一个模型或服务商。请求模型: {model}")
            return (f"API错误 ({resp.status_code}): {resp.text[:200]}\n"
                    f"——当前服务商: {cfg['provider_name']}，请求模型: {model}。"
                    f"模型名不对请到「设置」页修改 AI 模型后重试")
        except Exception as e:
            last_exc = e
            if attempt < 2:
                time.sleep(3)
    return f"请求出错（已重试3次）: {last_exc}"


def get_history() -> List[Dict]:
    return list(_history)


def clear_history():
    global _history
    _history = []
