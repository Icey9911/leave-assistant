"""AI 考勤信息提取模块 —— 使用 DeepSeek API"""
import json
from datetime import date
from openai import OpenAI

DEEPSEEK_BASE_URL = "https://api.deepseek.com"

SYSTEM_PROMPT = """你是一个行政助手，负责从员工输入的自然语言中提取请假信息。

请从用户输入中提取以下字段，以 JSON 格式返回：

{
  "leave_type": "年假/事假/带薪病假/调休/婚假/产假/出差/市内公出/漏打卡补卡",
  "start_date": "YYYY-MM-DD",
  "start_time": "HH:MM（24小时制，如未说明具体时间，默认09:00）",
  "end_date": "YYYY-MM-DD",
  "end_time": "HH:MM（24小时制，如未说明具体时间，默认18:00）",
  "reason": "请假事由简短摘要",
  "confidence": "high/medium/low",
  "notes": "需要用户确认的模糊点"
}

规则：
1. 只提到一个日期则起止相同
2. 提到具体时刻必须提取（如"下午1点"→13:00，"下午6点"→18:00）
3. 未说明具体时间：起始默认09:00，结束默认18:00
4. 提到"半天"：上午=09:00-12:00，下午=13:00-18:00
5. "明天""下周X"结合当前日期推算
6. 信息完全明确才设 confidence 为 high
7. 提到"出差""外派""拜访客户"→出差；"外出""公出""市内办事"→市内公出；"漏打卡""忘记打卡""补卡"→漏打卡补卡

只返回 JSON，不要包含其他文字。"""


def _get_client(api_key: str) -> OpenAI:
    return OpenAI(api_key=api_key, base_url=DEEPSEEK_BASE_URL)


def _parse_json_response(text_content: str) -> dict:
    """从 AI 响应中解析 JSON"""
    text_content = text_content.strip()
    if text_content.startswith("```json"):
        text_content = text_content[7:]
    if text_content.startswith("```"):
        text_content = text_content[3:]
    if text_content.endswith("```"):
        text_content = text_content[:-3]
    text_content = text_content.strip()

    try:
        return json.loads(text_content)
    except json.JSONDecodeError:
        import re
        json_match = re.search(r'\{[^{}]*\}', text_content, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group())
            except json.JSONDecodeError:
                pass
        return {
            "leave_type": "未知",
            "start_date": "", "start_time": "09:00",
            "end_date": "", "end_time": "18:00",
            "reason": "", "confidence": "low",
            "notes": f"AI 解析失败: {text_content[:100]}"
        }


def extract_leave_info(user_input: str, api_key: str, current_date: date | None = None) -> dict:
    """调用 DeepSeek API 提取请假结构化信息"""
    if current_date is None:
        current_date = date.today()

    client = _get_client(api_key)

    user_message = f"当前日期：{current_date.strftime('%Y-%m-%d')}（{current_date.strftime('%A')}）\n\n员工输入：{user_input}"

    response = client.chat.completions.create(
        model="deepseek-chat",
        max_tokens=500,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message}
        ],
        temperature=0.1,
    )

    text_content = response.choices[0].message.content or ""
    if not text_content.strip():
        raise ValueError("AI 返回了空内容")

    return _parse_json_response(text_content)


def analyze_leave_document(image_data: bytes, media_type: str, api_key: str, doc_type: str = "application") -> dict:
    """使用 DeepSeek 分析请假相关文档图片

    注意: DeepSeek chat API 不支持图片输入。建议将文档内容用文字描述后使用。
    如果确实需要图片分析，请配置 ANTHROPIC_API_KEY 使用 Claude Vision。
    """
    return {
        "is_complete": False,
        "has_form_fields": False,
        "has_applicant_signature": False,
        "has_supervisor_signature": False,
        "has_hr_signature": False,
        "summary": "DeepSeek 暂不支持图片分析",
        "suggestions": "请用文字描述文档内容，或将 ANTHROPIC_API_KEY 配置到 Secrets 以启用图片分析功能"
    }
