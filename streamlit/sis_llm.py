"""Cortex-only LLM for Streamlit in Snowflake. No Ollama, no dotenv."""
import json
import re

LAST_USED_LLM = {"backend": "snowflake_cortex", "model": "llama3.1-70b", "reason": "SiS native"}


def complete(session, prompt, model="llama3.1-70b"):
    row = session.sql(
        "SELECT SNOWFLAKE.CORTEX.COMPLETE(?, ?)", params=[model, prompt]
    ).collect()
    text = row[0][0]
    if isinstance(text, str):
        try:
            text = json.loads(text)
        except (ValueError, TypeError):
            pass
    return text


def ask_json(session, prompt, model="llama3.1-70b", retries=3):
    last_err = None
    for _ in range(retries):
        response = complete(session, prompt, model)
        if isinstance(response, dict):
            return response
        if isinstance(response, str):
            cleaned = response.strip()
            if cleaned.startswith("```json"):
                cleaned = cleaned[7:]
            elif cleaned.startswith("```"):
                cleaned = cleaned[3:]
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
            cleaned = cleaned.strip()
            try:
                return json.loads(cleaned)
            except ValueError as e:
                match = re.search(r"\{.*\}", cleaned, re.DOTALL)
                if match:
                    json_str = re.sub(r",\s*([\]}])", r"\1", match.group(0))
                    try:
                        return json.loads(json_str)
                    except ValueError as e2:
                        last_err = e2
                else:
                    last_err = e
        prompt += f"\n\nPrevious attempt failed: {last_err}. Return strictly valid JSON."
    raise ValueError(f"Failed to parse JSON after {retries} attempts: {last_err}")


def chat_with_tools(session, messages, tools, model="llama3.1-70b"):
    prompt_content = ""
    for m in messages:
        prompt_content += f"{m['role'].capitalize()}: {m['content']}\n\n"
    tool_example = '{"tool_calls": [{"function": {"name": "<tool_name>", "arguments": {"<arg_name>": "<arg_value>"}}}]}'
    cortex_prompt = (
        prompt_content
        + "You have access to the following tools:\n" + json.dumps(tools, indent=2) + "\n"
        + "To use a tool, output a JSON object EXACTLY matching this format and nothing else:\n"
        + tool_example + "\n"
    )
    text = complete(session, cortex_prompt, model)
    if isinstance(text, str):
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group(0))
                if "tool_calls" in parsed:
                    return parsed["tool_calls"]
            except (ValueError, TypeError):
                pass
    return []
