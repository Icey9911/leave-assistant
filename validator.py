"""校验模块 —— 假期余额校验、部门冲突检测、规则校验"""

from datetime import date, datetime, timedelta
from data_store import get_employee_by_id, get_approved_records_by_department, get_records_by_employee


class CheckResult:
    """单条校验结果"""
    def __init__(self, passed: bool, level: str, message: str, detail: str = ""):
        self.passed = passed
        self.level = level    # "pass" / "warn" / "fail"
        self.message = message
        self.detail = detail


def validate_leave_request(employee_id: str, leave_type: str,
                           start_date_str: str, end_date_str: str,
                           days: float) -> list[CheckResult]:
    """执行全部校验，返回校验结果列表"""
    results = []
    employee = get_employee_by_id(employee_id)

    if not employee:
        results.append(CheckResult(False, "fail", "员工不存在", f"工号 {employee_id} 未找到"))
        return results

    # 1. 余额校验
    results.append(_check_balance(employee, leave_type, days))

    # 2. 日期有效性校验
    results.append(_check_dates(start_date_str, end_date_str, days))

    # 3. 周末检测
    results.append(_check_weekends(start_date_str, end_date_str))

    # 4. 部门冲突检测
    results.append(_check_department_conflict(employee, start_date_str, end_date_str))

    # 5. 规则校验
    results.extend(_check_rules(employee, leave_type, start_date_str, days))

    return results


def _check_balance(employee: dict, leave_type: str, days: float) -> CheckResult:
    balance = employee["leave_balance"].get(leave_type)
    if balance is None:
        return CheckResult(False, "fail", f"未知的请假类型：{leave_type}")

    # 年假按日期折算可用额度
    check_balance = balance
    if leave_type == "年假":
        today = date.today()
        day_of_year = today.timetuple().tm_yday
        days_in_year = 366 if today.year % 4 == 0 and (today.year % 100 != 0 or today.year % 400 == 0) else 365
        accrued = balance * day_of_year / days_in_year
        check_balance = min(balance, (int(accrued * 2) + 1) / 2 if accrued > 0 else 0)

    if check_balance >= days:
        extra = f"（全年额度 {balance} 天，当前可用 {check_balance} 天）" if leave_type == "年假" else ""
        return CheckResult(True, "pass", f"假期余额充足", f"{leave_type}剩余 {check_balance} 天，本次使用 {days} 天{extra}")
    else:
        extra = f"（全年额度 {balance} 天，当前仅可用 {check_balance} 天）" if leave_type == "年假" else ""
        return CheckResult(False, "fail", f"假期余额不足", f"{leave_type}需要 {days} 天，可用 {check_balance} 天，差额 {days - check_balance} 天{extra}")


def _check_dates(start_str: str, end_str: str, days: float) -> CheckResult:
    try:
        start = datetime.strptime(start_str, "%Y-%m-%d").date()
        end = datetime.strptime(end_str, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return CheckResult(False, "fail", "日期格式无效", "请确认起止日期是否正确填写")

    today = date.today()
    if start < today:
        return CheckResult(True, "warn", "请假开始日期早于今天（补交申请）", f"开始日期 {start_str} < 今天 {today}，请确认为补交")

    if end < start:
        return CheckResult(False, "fail", "结束日期不能早于开始日期", f"{end_str} < {start_str}")

    calculated_days = (end - start).days + 1
    if days > calculated_days:
        return CheckResult(False, "warn", f"请假天数({days}天)超过日期区间({calculated_days}天)", "可能包含非工作日")

    return CheckResult(True, "pass", "日期校验通过")


def _check_weekends(start_str: str, end_str: str) -> CheckResult:
    """检查请假区间是否包含周末"""
    try:
        start = datetime.strptime(start_str, "%Y-%m-%d").date()
        end = datetime.strptime(end_str, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return CheckResult(True, "pass", "周末检测跳过（日期无效）")

    weekend_dates = []
    current = start
    while current <= end:
        if current.weekday() >= 5:  # 周六=5, 周日=6
            weekday_name = ["一","二","三","四","五","六","日"][current.weekday()]
            weekend_dates.append(f"{current.strftime('%m月%d日')}（周{weekday_name}）")
        current += timedelta(days=1)

    if weekend_dates:
        return CheckResult(True, "warn",
            f"包含 {len(weekend_dates)} 个周末日，无需请假，已自动排除",
            "、".join(weekend_dates))
    return CheckResult(True, "pass", "日期区间不包含周末")
    try:
        req_start = datetime.strptime(start_str, "%Y-%m-%d").date()
        req_end = datetime.strptime(end_str, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return CheckResult(True, "pass", "无法进行冲突检测（日期无效）")

    dept_records = get_approved_records_by_department(employee["department"])
    conflicts = []
    for r in dept_records:
        if r["employee_id"] == employee["id"]:
            continue
        r_start = datetime.strptime(r["start_date"], "%Y-%m-%d").date()
        r_end = datetime.strptime(r["end_date"], "%Y-%m-%d").date()
        if req_start <= r_end and req_end >= r_start:
            conflicts.append(f"{r['employee_id']} ({r['start_date']}~{r['end_date']}, {r['leave_type']})")

    if conflicts:
        detail = "同一时间段已批准请假：\n" + "\n".join(f"  - {c}" for c in conflicts)
        return CheckResult(True, "warn", f"部门内 {len(conflicts)} 人同期请假", detail)
    return CheckResult(True, "pass", "无部门内冲突")


def _check_rules(employee: dict, leave_type: str, start_str: str, days: float) -> list[CheckResult]:
    results = []

    try:
        start = datetime.strptime(start_str, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return results

    today = date.today()

    # 事假需提前1天申请
    if leave_type == "事假":
        if (start - today).days < 1:
            results.append(CheckResult(False, "warn", "事假需提前至少1天申请", f"计划 {start_str} 开始，今天 {today}"))

    # 年假最小单位为半天
    if leave_type == "年假" and days < 0.5:
        results.append(CheckResult(False, "fail", "年假最小请假单位为半天（0.5天）"))

    # 病假超过3天需提供医院证明（提示）
    if leave_type == "带薪病假" and days > 3:
        results.append(CheckResult(True, "warn", "病假超过3天，需在销假时提供医院证明"))

    # 近期是否有同类请假（提示是否有异常频率）
    records = get_records_by_employee(employee["id"])
    recent_same_type = [
        r for r in records
        if r["leave_type"] == leave_type and r["status"] == "已批准"
    ]
    if len(recent_same_type) >= 3:
        results.append(CheckResult(True, "warn", f"本年度已使用{leave_type} {len(recent_same_type)} 次，请关注"))

    return results
