"""LLM access for the Slack alert and the dashboard assistant. Works with any OpenAI-compatible
endpoint (Groq, Ollama, ...). The alert falls back to a plain template when no LLM is available."""
import json
import re

import httpx

from . import config

ALERT_SYSTEM = (
    "You are a retail data analyst for a fashion brand. Write a Slack alert about the latest "
    "anomaly detection run. Use Slack mrkdwn only: *bold* with single asterisks, bullets starting with '• '. "
    "Structure: a one-line headline; 3 to 5 bullets on the most important recent anomalies, each with a "
    "likely business cause; one line on data quality; one line starting with '*Next step:*'. "
    "Maximum 130 words. Use only the numbers provided, never invent figures. "
    "total_anomalies and by_severity cover the whole history, across all severities; "
    "recent_anomalies are the top ones from the last recent_window_days days."
)
CHAT_SYSTEM = (
    "You are the assistant inside Atelier Watch, an anomaly detection dashboard for a fashion retailer. "
    "Answer questions using ONLY the JSON context below, which describes the latest pipeline run. "
    "If the answer is not in the context, say so plainly and suggest which dashboard view to check. "
    "Be concise: a few sentences or a short bullet list, with concrete numbers, stores, categories and dates. "
    "Never invent figures. total_anomalies and by_severity cover the whole history; recent_anomalies only "
    "the last recent_window_days days.\n\nContext:\n"
)


def available():
    return bool(config.LLM_BASE_URL and config.LLM_MODEL)


def chat(messages):
    """One chat completion. Returns the reply text, or None when the LLM is unavailable or fails."""
    if not available():
        return None
    try:
        r = httpx.post(
            f"{config.LLM_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {config.LLM_API_KEY}"} if config.LLM_API_KEY else {},
            json={"model": config.LLM_MODEL, "temperature": 0.2, "messages": messages,
                  **({"reasoning_effort": config.LLM_REASONING_EFFORT} if config.LLM_REASONING_EFFORT else {})},
            timeout=120,
        )
        r.raise_for_status()
        text = r.json()["choices"][0]["message"]["content"] or ""
    except (httpx.HTTPError, KeyError, IndexError, ValueError) as e:
        print(f"LLM call failed: {type(e).__name__}: {e}")
        return None
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()  # reasoning models
    # models often answer in Markdown: normalise to Slack mrkdwn
    text = re.sub(r"\*\*(.+?)\*\*", r"*\1*", text)
    text = re.sub(r"(?m)^\s*[*-]\s+", "• ", text)
    return text or None


def template(facts):
    lines = [f"*Fashion anomaly report — run #{facts['run_id']}*",
             f"{facts['total_anomalies']} anomalies detected ({facts['by_severity'].get('critical', 0)} critical, "
             f"{facts['by_severity'].get('high', 0)} high)."]
    lines += [f"• *{a['entity']}* on {a['day']}: {a['description']} [{a['severity']}]" for a in facts["recent_anomalies"][:6]]
    dq = facts["data_quality"]
    lines.append(f"Data quality: {dq['rules_failed']} of {dq['rules_total']} rules failed, "
                 f"{dq['rows_quarantined']} rows quarantined.")
    lines.append("*Next step:* review the critical items in the dashboard.")
    return "\n".join(lines)


def summarize(facts):
    """Returns (message, source, model) where source is 'llm' or 'template'."""
    text = chat([{"role": "system", "content": ALERT_SYSTEM},
                 {"role": "user", "content": json.dumps(facts, default=str)}])
    return (text, "llm", config.LLM_MODEL) if text else (template(facts), "template", None)


def answer(facts, history):
    """Dashboard assistant: `history` is the conversation so far, ending with the user's question."""
    return chat([{"role": "system", "content": CHAT_SYSTEM + json.dumps(facts, default=str)}, *history])
