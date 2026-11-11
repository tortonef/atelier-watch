"""Turn pipeline facts into a Slack alert. Works with any OpenAI-compatible endpoint
(Groq, Ollama, ...); falls back to a plain template when no LLM is configured or it fails."""
import json
import re

import httpx

from . import config

SYSTEM = (
    "You are a retail data analyst for a fashion brand. Write a Slack alert about the latest "
    "anomaly detection run. Use Slack mrkdwn only: *bold* with single asterisks, bullets starting with '• '. "
    "Structure: a one-line headline; 3 to 5 bullets on the most important recent anomalies, each with a "
    "likely business cause; one line on data quality; one line starting with '*Next step:*'. "
    "Maximum 130 words. Use only the numbers provided, never invent figures. "
    "total_anomalies and by_severity cover the whole history, across all severities; "
    "recent_anomalies are the top ones from the last recent_window_days days."
)


def template(facts):
    lines = [f"*Fashion anomaly report — run #{facts['run_id']}*",
             f"{facts['total_anomalies']} anomalies detected ({facts['by_severity'].get('critical', 0)} critical, "
             f"{facts['by_severity'].get('high', 0)} high)."]
    lines += [f"• *{a['entity']}* on {a['day']}: {a['description']} [{a['severity']}]" for a in facts["recent_anomalies"]]
    dq = facts["data_quality"]
    lines.append(f"Data quality: {dq['rules_failed']} of {dq['rules_total']} rules failed, "
                 f"{dq['rows_quarantined']} rows quarantined.")
    lines.append("*Next step:* review the critical items in the dashboard.")
    return "\n".join(lines)


def summarize(facts):
    """Returns (message, source, model) where source is 'llm' or 'template'."""
    if config.LLM_BASE_URL and config.LLM_MODEL:
        try:
            r = httpx.post(
                f"{config.LLM_BASE_URL}/chat/completions",
                headers={"Authorization": f"Bearer {config.LLM_API_KEY}"} if config.LLM_API_KEY else {},
                json={"model": config.LLM_MODEL, "temperature": 0.2, "messages": [
                    {"role": "system", "content": SYSTEM},
                    {"role": "user", "content": json.dumps(facts, default=str)}],
                    **({"reasoning_effort": config.LLM_REASONING_EFFORT} if config.LLM_REASONING_EFFORT else {})},
                timeout=120,
            )
            r.raise_for_status()
            text = r.json()["choices"][0]["message"]["content"]
            text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()  # reasoning models
            # models often answer in Markdown: normalise to Slack mrkdwn
            text = re.sub(r"\*\*(.+?)\*\*", r"*\1*", text)
            text = re.sub(r"(?m)^\s*[*-]\s+", "• ", text)
            if text:
                return text, "llm", config.LLM_MODEL
            print("LLM returned an empty message, using template")
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as e:
            print(f"LLM call failed, using template: {type(e).__name__}: {e}")
    return template(facts), "template", None
