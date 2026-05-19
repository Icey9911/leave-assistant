"""AI 请假信息提取模块 —— 使用 Claude API 从自然语言中提取结构化请假信息"""

import json
from datetime import date
from anthropic import Anthropic


SYSTEM_PROMPT = """你是一个行政助手，负责从员工输入的自然语言中提取请假信息。

请从用户输入中提取以下字段，以 JSON 格式返回：

{
  "leave_type": "年假/事假/带薪病假/调休/婚假/产假",
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

只返回 JSON，不要包含其他文字。"""


def extract_leave_info(user_input: str, api_key: str, current_date: date | None = None) -> dict:
    """调用 Claude API 提取请假结构化信息"""
    if current_date is None:
        current_date = date.today()

    client = Anthropic(api_key=api_key)

    user_message = f"当前日期：{current_date.strftime('%Y-%m-%d')}（{current_date.strftime('%A')}）\n\n员工输入：{user_input}"

    response = client.messages.create(
        model="claude-3-5-sonnet-20241022",
        max_tokens=500,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )

    # 从响应中提取文本内容
    text_content = ""
    for block in response.content:
        block_type = type(block).__name__
        # 尝试 .text 属性
        text_val = getattr(block, 'text', None)
        if text_val and str(text_val).strip():
            text_content = str(text_val).strip()
            break
        # 跳过思考块
        if hasattr(block, 'thinking'):
            continue

    if not text_content:
        # 紧急回退：尝试第一个 block 的任意文本属性
        if response.content:
            first = response.content[0]
            for attr in ['text', 'thinking', 'output', 'content']:
                val = getattr(first, attr, None)
                if val and str(val).strip():
                    text_content = str(val).strip()
                    break

    if not text_content:
        types = [type(b).__name__ for b in response.content]
        sample = str(response.content)[:500]
        raise ValueError(f"AI 返回了空内容（类型: {types}，详情: {sample}）")

    try:
        if text_content.startswith("```json"):
            text_content = text_content[7:-3].strip()
        elif text_content.startswith("```"):
            text_content = text_content[3:-3].strip()
        result = json.loads(text_content)
    except json.JSONDecodeError:
        # 尝试用正则提取 JSON
        import re
        result = None
        json_match = re.search(r'\{[^{}]*\}', text_content, re.DOTALL)
        if json_match:
            try:
                result = json.loads(json_match.group())
            except json.JSONDecodeError:
                pass
        if result is None:
            result = {
                "leave_type": "未知",
                "start_date": "",
                "start_time": "09:00",
                "end_date": "",
                "end_time": "18:00",
                "days": 0,
                "reason": "",
                "confidence": "low",
                "notes": f"AI 解析失败: {text_content[:100]}",
                "raw_response": text_content
            }

    return result


def analyze_leave_document(image_data: bytes, media_type: str, api_key: str, doc_type: str = "application") -> dict:
    """使用 Claude Vision 分析请假相关文档图片

    Args:
        image_data: 图片二进制数据
        media_type: 图片 MIME 类型 (image/png, image/jpeg 等)
        api_key: Anthropic API Key
        doc_type: "application" 申请表 / "approval" 审批截图

    Returns:
        {
            "has_form_fields": bool,       # 是否包含申请表字段
            "has_applicant_signature": bool, # 是否有申请人签字
            "has_supervisor_signature": bool, # 是否有上级签字
            "has_hr_signature": bool,       # 是否有行政签字
            "is_complete": bool,            # 是否完整
            "summary": str,                 # 摘要说明
            "suggestions": str              # 建议（缺少什么）
        }
    """
    import base64

    client = Anthropic(api_key=api_key)

    image_base64 = base64.b64encode(image_data).decode("utf-8")

    if doc_type == "application":
        instruction = """你是一个专业的行政文档审核专家。请极其仔细地检查这张请假申请表图片中的每一个签字区域。

请逐项检查以下签字位置：

【申请人签字】- 查找"申请人"、"请假人"、"员工签字"等标签旁边的手写痕迹
【直属上级签字】- 查找"上级"、"主管"、"部门负责人"、"经理"、"审批人"等标签旁边的手写痕迹
【行政部签字】- 查找"行政"、"人事"、"HR"等标签旁边的手写痕迹

重要：以下任一情况都视为"有签字"：
- 手写的姓名或签名（哪怕字迹潦草、模糊、笔画很轻）
- 红色/蓝色印章或公章
- 手写日期（日期旁通常伴随签名）
- 审批栏内有任何非打印的手写笔迹
- "同意"、"批准"等手写批语
- 签名栏内有非空白的墨迹

特别提醒：
- 有些签名是简写的草书，不是规整的楷体
- 有些表格把签字放在格子内，字迹可能很淡
- 请放大查看表格边缘和角落的签字区域
- 不要漏掉任何小的手写笔迹

请以 JSON 格式返回（只返回 JSON）：
{
  "has_form_fields": true/false,
  "has_applicant_signature": true/false,
  "has_supervisor_signature": true/false,
  "has_hr_signature": true/false,
  "is_complete": true/false,
  "summary": "简要描述每个签字位的发现",
  "suggestions": "缺少什么签字就写明，完整则写'文档完整'"
}"""
    elif doc_type == "approval":
        instruction = """你是一个专业的行政文档审核专家。请仔细检查这张审批截图。

查找以下审批凭证：
1. "同意"、"批准"、"已审批"、"通过"等文字标识
2. 审批人签字或手写姓名
3. 电子审批系统中的"通过"或"同意"按钮状态
4. OA/钉钉/企业微信等审批截图中的同意标记
5. 审批流程节点显示"已通过"

以下视为有审批签字：
- 审批人栏有手写签名
- 电子审批截图显示"已通过"或绿色勾
- 审批意见栏有文字（即使不是完整签名）
- 任何形式的审批确认标记

请以 JSON 格式返回（只返回 JSON）：
{
  "has_form_fields": false,
  "has_applicant_signature": false,
  "has_supervisor_signature": true/false,
  "has_hr_signature": true/false,
  "is_complete": true/false,
  "summary": "简要描述审批状态",
  "suggestions": "缺少什么审批就写明，完整则写'审批完整'"
}"""
    else:  # sick_cert
        instruction = """你是一个专业的行政文档审核专家。请仔细检查这张带薪病假证明单/诊断证明。

请检查以下关键信息：
1. 医院名称和科室
2. 患者姓名
3. 诊断结果或病情描述
4. 建议休息天数（医嘱休假时长）
5. 医生签字或印章
6. 医院公章/诊断专用章
7. 开具日期

以下视为有效带薪病假证明：
- 有医院抬头或名称
- 有诊断内容或病情说明
- 有医生签字或医院印章
- 有建议休息时间

请以 JSON 格式返回（只返回 JSON）：
{
  "has_form_fields": true/false,
  "has_applicant_signature": false,
  "has_supervisor_signature": false,
  "has_hr_signature": false,
  "is_complete": true/false,
  "summary": "简要描述带薪病假单内容（医院、诊断、建议休息天数）",
  "suggestions": "缺少什么就写明，完整则写'带薪病假单有效'"
}"""

    response = client.messages.create(
        model="claude-3-5-sonnet-20241022",
        max_tokens=1024,
        system="你是一个专业的行政文档审核助手，负责检查请假申请表、审批截图、带薪病假证明单是否完整。回复必须是纯 JSON。",
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": image_base64}},
                {"type": "text", "text": instruction}
            ]
        }]
    )

    # 提取文本内容
    text_content = ""
    block_types = []
    for block in response.content:
        bt = type(block).__name__
        block_types.append(bt)
        if hasattr(block, 'text') and getattr(block, 'text', '').strip():
            text_content = block.text.strip()
            break
        elif hasattr(block, 'thinking'):
            # 尝试用思考内容作为回退
            thinking = getattr(block, 'thinking', '')
            if thinking.strip() and not text_content:
                text_content = thinking.strip()

    if not text_content:
        stop_reason = getattr(response, 'stop_reason', 'unknown')
        return {
            "is_complete": False, "summary": "AI 未返回分析结果",
            "suggestions": f"响应类型: {block_types}, 停止原因: {stop_reason}。请重试或更换图片",
            "raw_types": block_types, "stop_reason": stop_reason
        }

    try:
        # 清理 JSON 响应
        clean = text_content.strip()
        if clean.startswith("```json"):
            clean = clean[7:]
        if clean.startswith("```"):
            clean = clean[3:]
        if clean.endswith("```"):
            clean = clean[:-3]
        clean = clean.strip()
        return json.loads(clean)
    except json.JSONDecodeError:
        # 尝试用正则提取 JSON
        import re
        json_match = re.search(r'\{[^{}]*\}', text_content, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group())
            except json.JSONDecodeError:
                pass
        return {
            "has_form_fields": False, "has_applicant_signature": False,
            "has_supervisor_signature": False, "has_hr_signature": False,
            "is_complete": False, "summary": "AI 解析异常",
            "suggestions": f"解析失败，原始响应: {text_content[:200]}",
            "raw": text_content
        }
