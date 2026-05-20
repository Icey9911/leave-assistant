"""审批单生成模块 —— 生成格式化考勤审批单"""

from datetime import date
from data_store import get_employee_by_id


def generate_approval_form(employee_id: str, leave_info: dict, check_results: list) -> str:
    """生成 Markdown 格式的请假审批单"""
    employee = get_employee_by_id(employee_id)

    if not employee:
        return "## 错误：未找到员工信息"

    results_md = _format_check_results(check_results)

    form_md = f"""---
## 员工考勤审批单

| 项目 | 内容 |
|------|------|
| **申请人** | {employee['name']} |
| **工号** | {employee['id']} |
| **部门** | {employee['department']} |
| **请假类型** | {leave_info.get('leave_type', '')} |
| **开始时间** | {leave_info.get('start_date', '')} {leave_info.get('start_time', '')} |
| **结束时间** | {leave_info.get('end_date', '')} {leave_info.get('end_time', '')} |
| **请假天数** | {leave_info.get('days', 0)} 天 |
| **请假事由** | {leave_info.get('reason', '')} |
| **申请日期** | {date.today().strftime('%Y-%m-%d')} |

---

### 校验结果
{results_md}

---

### 审批意见

| 角色 | 审批人 | 意见 | 日期 |
|------|--------|------|------|
| 直属上级 | ________ | ________ | ________ |
| 部门负责人 | ________ | ________ | ________ |
| 行政部 | ________ | ________ | ________ |

---

*本单由考勤智能助手自动生成*
"""
    return form_md


def _format_check_results(check_results: list) -> str:
    if not check_results:
        return "（无校验结果）\n"

    lines = []
    for r in check_results:
        if r.level == "pass":
            icon = "✅"
        elif r.level == "warn":
            icon = "⚠️"
        else:
            icon = "❌"

        lines.append(f"- {icon} **{r.message}**")
        if r.detail:
            lines.append(f"  {r.detail}")

    return "\n".join(lines)
