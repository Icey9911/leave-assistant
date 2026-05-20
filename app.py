"""请假单智能处理助手 —— 账号登录 + 员工自助 + 管理员审批 + 文档审核"""

import streamlit as st
import base64
import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path

from data_store import (
    get_all_employees, get_employee_by_id, get_employee_by_name,
    add_leave_record, update_leave_balance, get_all_leave_records,
    approve_record, reject_record, get_pending_records, review_record, get_approved_records
)
from ai_extractor import extract_leave_info
from validator import validate_leave_request
from form_generator import generate_approval_form


# ==================== 配置 ====================
UPLOAD_DIR = Path(__file__).parent / "uploads"
APPLICATION_DIR = UPLOAD_DIR / "applications"
APPROVAL_DIR = UPLOAD_DIR / "approvals"
APPLICATION_DIR.mkdir(parents=True, exist_ok=True)
APPROVAL_DIR.mkdir(parents=True, exist_ok=True)
SICK_CERT_DIR = UPLOAD_DIR / "sick_certs"
SICK_CERT_DIR.mkdir(parents=True, exist_ok=True)
ADMIN_PASSWORD_FILE = Path(__file__).parent / "data" / "admin_password.json"


def _load_admin_password() -> str:
    """从文件加载管理员密码"""
    if ADMIN_PASSWORD_FILE.exists():
        try:
            with open(ADMIN_PASSWORD_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("admin_password", "admin123")
        except Exception:
            pass
    return "admin123"


def _save_admin_password(new_pwd: str) -> None:
    """保存管理员密码到文件"""
    if new_pwd.strip():
        ADMIN_PASSWORD_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(ADMIN_PASSWORD_FILE, "w", encoding="utf-8") as f:
            json.dump({"admin_password": new_pwd.strip()}, f)


ADMIN_PASSWORD = _load_admin_password()
API_KEY_FILE = Path(__file__).parent / "data" / "api_key.json"


def _load_api_key() -> str:
    """加载 API Key（优先 Streamlit Secrets，其次本地文件）"""
    # 优先从 Streamlit Secrets 读取（云端部署）
    try:
        secret_key = st.secrets.get("DEEPSEEK_API_KEY", "")
        if secret_key:
            return secret_key
    except Exception:
        pass
    # 回退到本地文件（本地开发）
    if API_KEY_FILE.exists():
        try:
            with open(API_KEY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("api_key", "")
        except Exception:
            pass
    return ""


def _save_api_key(key: str) -> None:
    """保存 API Key 到文件"""
    if key.strip():
        API_KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(API_KEY_FILE, "w", encoding="utf-8") as f:
            json.dump({"api_key": key.strip()}, f)


# ==================== 辅助函数 ====================
def _parse_date(date_str: str):
    if not date_str: return date.today()
    try: return datetime.strptime(date_str, "%Y-%m-%d").date()
    except: return date.today()


def _parse_time(time_str: str) -> tuple:
    """解析时间字符串，返回 (小时, 分钟)"""
    if not time_str: return (9, 0)
    try:
        parts = time_str.strip().split(":")
        return (int(parts[0]), int(parts[1]) if len(parts) > 1 else 0)
    except: return (9, 0)


def _is_weekend(d: date) -> bool:
    """判断是否为周末（周六/周日）"""
    return d.weekday() >= 5


def _calc_days(start_date, start_time_str: str, end_date, end_time_str: str) -> tuple[float, list[str]]:
    """按行政考勤规则计算天数（自动排除周末）。
    返回 (有效请假天数, 被排除的周末日期列表)
    """
    sh, sm = _parse_time(start_time_str)
    eh, em = _parse_time(end_time_str)
    start_mins = sh * 60 + sm
    end_mins = eh * 60 + em

    total = 0.0
    excluded_weekends = []
    current = start_date
    while current <= end_date:
        if _is_weekend(current):
            excluded_weekends.append(current.strftime("%m月%d日（周%s）" % ["一","二","三","四","五","六","日"][current.weekday()]))
        elif current == start_date and current == end_date:
            total += _partial_day(start_mins, end_mins)
        elif current == start_date:
            total += _partial_day(start_mins, 18 * 60)
        elif current == end_date:
            total += _partial_day(9 * 60, end_mins)
        else:
            total += 1.0
        current += timedelta(days=1)

    return total, excluded_weekends


def _partial_day(start_mins: int, end_mins: int) -> float:
    """计算一天内部分时段的天数"""
    if end_mins <= start_mins:
        return 0

    days = 0.0

    # 上午段 09:00-12:00：覆盖≥2小时计0.5天
    morning_start = max(start_mins, 9 * 60)
    morning_end = min(end_mins, 12 * 60)
    if morning_end - morning_start >= 2 * 60:
        days += 0.5

    # 下午段 13:00-18:00：覆盖≥2小时计0.5天
    afternoon_start = max(start_mins, 13 * 60)
    afternoon_end = min(end_mins, 18 * 60)
    if afternoon_end - afternoon_start >= 2 * 60:
        days += 0.5

    # 如果起始时间覆盖很少，但总时长≥1小时，至少算0.5天
    if days == 0 and (end_mins - start_mins) >= 60:
        days = 0.5

    return days


def _calc_shanghai_leave(graduation_date: str, hire_date: str, gender: str) -> dict:
    """根据上海市员工假期标准自动计算各类假期天数

    年假（累计工龄）：1-10年5天，10-20年10天，20年以上15天
    婚假：10天（上海标准）
    产假：女128天，男0天（上海）
    陪产假：男10天，女0天（上海）
    病假/事假/调休：默认值
    """
    today = date.today()
    try: grad = datetime.strptime(graduation_date, "%Y-%m-%d").date()
    except: grad = today
    try: hire = datetime.strptime(hire_date, "%Y-%m-%d").date()
    except: hire = today

    # 累计工龄（从毕业时间算起）
    work_years = today.year - grad.year
    if today.month < grad.month or (today.month == grad.month and today.day < grad.day):
        work_years -= 1

    if work_years >= 20: annual = 15.0
    elif work_years >= 10: annual = 10.0
    elif work_years >= 1: annual = 5.0
    else: annual = 0.0

    return {
        "年假": annual,
        "事假": 10.0,
        "带薪病假": 5.0,
        "调休": 3.0,
        "婚假": 10.0,
        "陪产假": 10.0 if gender == "男" else 0.0,
        "产假": 128.0 if gender == "女" else 0.0,
    }


def _get_available_annual_leave(full_annual: float) -> float:
    """按当前日期折算年假可用额度
    全年年假 * 已过天数/365，向上取整到0.5天
    例：5天年假，5月19日≈2.5天，11月1日≈4.0天
    """
    today = date.today()
    day_of_year = today.timetuple().tm_yday
    days_in_year = 366 if today.year % 4 == 0 and (today.year % 100 != 0 or today.year % 400 == 0) else 365
    accrued = full_annual * day_of_year / days_in_year
    # 向上取整到0.5天
    available = (int(accrued * 2) + 1) / 2 if accrued > 0 else 0
    return min(full_annual, max(0, available))


def _save_uploaded_image(uploaded_file, subdir: Path, emp_id: str) -> str:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    ext = uploaded_file.name.split(".")[-1] if "." in uploaded_file.name else "png"
    filename = f"{emp_id}_{ts}.{ext}"
    filepath = subdir / filename
    with open(filepath, "wb") as f: f.write(uploaded_file.getvalue())
    return str(filepath.relative_to(Path(__file__).parent))


def _check_missing_approvals(emp_id: str) -> list[dict]:
    records = get_all_leave_records()
    today = date.today()
    if today.month == 12:
        next_month_first = date(today.year + 1, 1, 1)
    else:
        next_month_first = date(today.year, today.month + 1, 1)
    reminders = []
    for r in records:
        if r.get("employee_id") != emp_id: continue
        if r.get("status") != "待审批": continue
        if r.get("application_image") and not r.get("approval_image"):
            days_left = (next_month_first - today).days
            reminders.append({
                "record_id": r["record_id"], "leave_type": r["leave_type"],
                "start_date": r["start_date"], "days_left": days_left,
                "urgent": days_left <= 3,
                "remind_type": "approval"
            })
        # 病假缺病假单：下个月前3个工作日提醒
        if r.get("leave_type") == "带薪病假" and not r.get("sick_certificate_image"):
            # 计算下月前3个工作日
            first_of_month = next_month_first
            workdays = 0
            d = first_of_month
            while workdays < 3:
                if d.weekday() < 5:  # 周一到周五
                    workdays += 1
                d += timedelta(days=1)
            deadline = d - timedelta(days=1)  # 第3个工作日的日期
            days_until_deadline = (deadline - today).days
            if days_until_deadline >= 0:  # 还在提醒期内
                reminders.append({
                    "record_id": r["record_id"], "leave_type": r["leave_type"],
                    "start_date": r["start_date"], "days_left": days_until_deadline,
                    "urgent": days_until_deadline <= 1,
                    "remind_type": "sick"
                })
    return reminders


# ==================== 登录持久化（URL 参数） ====================
def _encode_login_token(emp_id: str, role: str) -> str:
    data = json.dumps({"id": emp_id, "role": role})
    return base64.urlsafe_b64encode(data.encode()).decode()


def _decode_login_token(token: str) -> dict | None:
    try:
        data = base64.urlsafe_b64decode(token.encode()).decode()
        return json.loads(data)
    except Exception:
        return None


# ==================== 页面配置 ====================
st.set_page_config(page_title="请假单智能处理助手", page_icon="📋", layout="wide")

# ==================== 会话状态 ====================
for key, default in {
    "logged_in": False,
    "user_role": None,
    "current_employee": None,
    "api_key": _load_api_key(),   # 启动时自动加载已保存的 API Key
    "extracted_info": None,
    "check_results": None,
    "form_md": None,
    "app_image_path": None,
    "approval_image_path": None,
    "sick_cert_image_path": None,
}.items():
    if key not in st.session_state:
        st.session_state[key] = default

# 自动登录：刷新页面后通过 URL 参数恢复登录态
if not st.session_state.logged_in and not st.session_state.get("_auto_login_checked"):
    st.session_state["_auto_login_checked"] = True
    token = st.query_params.get("user")
    if token:
        info = _decode_login_token(token)
        if info:
            role = info.get("role")
            emp_id = info.get("id")
            if role == "admin":
                st.session_state.logged_in = True
                st.session_state.user_role = "admin"
                st.session_state.current_employee = None
            elif role == "employee":
                emp = get_employee_by_id(emp_id)
                if emp:
                    st.session_state.logged_in = True
                    st.session_state.user_role = "employee"
                    st.session_state.current_employee = emp


# ==================== 未登录 → 显示登录页 ====================
if not st.session_state.logged_in:
    st.title("📋 请假单智能处理助手")
    st.caption("请登录后使用")

    col_center = st.columns([1, 2, 1])[1]
    with col_center:
        login_tab = st.radio("选择登录方式", ["👤 员工登录", "🔑 管理员登录"], horizontal=True)

        if "员工" in login_tab:
            st.subheader("员工登录")
            employees = get_all_employees()
            emp_options = {f"{e['name']} - {e['department']} ({e['id']})": e for e in employees}
            selected_label = st.selectbox("选择你的账号", list(emp_options.keys()))
            emp = emp_options[selected_label]
            password = st.text_input("密码", type="password", placeholder="默认密码：123456")

            if st.button("登录", type="primary", use_container_width=True):
                if password == emp.get("password", "123456"):
                    st.session_state.logged_in = True
                    st.session_state.user_role = "employee"
                    st.session_state.current_employee = emp
                    st.query_params["user"] = _encode_login_token(emp["id"], "employee")
                    st.rerun()
                else:
                    st.error("密码错误，请重试")

        else:
            st.subheader("管理员登录")
            admin_pwd = st.text_input("管理员密码", type="password", placeholder="请输入管理员密码")

            if st.button("登录", type="primary", use_container_width=True):
                if admin_pwd == ADMIN_PASSWORD:
                    st.session_state.logged_in = True
                    st.session_state.user_role = "admin"
                    st.session_state.current_employee = None
                    st.query_params["user"] = _encode_login_token("admin", "admin")
                    st.rerun()
                else:
                    st.error("密码错误")

    st.stop()  # 不执行后续代码


# ==================== 已登录 → 侧边栏 ====================
with st.sidebar:
    if st.session_state.user_role == "employee":
        emp = st.session_state.current_employee
        st.title(f"👤 {emp['name']}")
        st.caption(f"{emp['department']} | {emp['id']}")
        st.divider()

        st.subheader("📊 我的假期余额")
        for lt, bal in emp["leave_balance"].items():
            if lt == "年假":
                available = _get_available_annual_leave(bal)
                st.metric("年假", f"{available} 天",
                         delta=f"全年额度 {bal} 天", delta_color="off")
            else:
                st.metric(lt, f"{bal} 天")

        st.divider()

        # 修改密码
        with st.expander("🔒 修改密码"):
            old_pwd = st.text_input("当前密码", type="password", key="emp_old_pwd")
            new_pwd = st.text_input("新密码", type="password", key="emp_new_pwd")
            new_pwd2 = st.text_input("确认新密码", type="password", key="emp_new_pwd2")
            if st.button("修改密码", key="emp_change_pwd_btn", use_container_width=True):
                if old_pwd != emp.get("password", "123456"):
                    st.error("当前密码错误")
                elif not new_pwd:
                    st.error("新密码不能为空")
                elif new_pwd != new_pwd2:
                    st.error("两次密码不一致")
                else:
                    from config import EMPLOYEES_FILE
                    all_emps = get_all_employees()
                    for e in all_emps:
                        if e["id"] == emp["id"]:
                            e["password"] = new_pwd
                            break
                    with open(EMPLOYEES_FILE, "w", encoding="utf-8") as f:
                        json.dump(all_emps, f, ensure_ascii=False, indent=2)
                    st.session_state.current_employee["password"] = new_pwd
                    st.success("密码修改成功")

        st.divider()
        if st.button("🚪 退出登录", use_container_width=True):
            for key in list(st.session_state.keys()):
                del st.session_state[key]
            st.query_params.clear()
            st.rerun()

    else:
        st.title("🔑 管理员")
        st.caption("审批管理中心")

        st.divider()
        if st.button("🚪 退出登录", use_container_width=True):
            for key in list(st.session_state.keys()):
                del st.session_state[key]
            st.query_params.clear()
            st.rerun()


# ==================== 设置对话框 ====================
@st.dialog("⚙️ 设置")
def settings_dialog():
    is_admin = st.session_state.user_role == "admin"

    st.subheader("🤖 AI 接口配置")
    if is_admin:
        api_key = st.text_input(
            "DeepSeek API Key",
            type="password",
            value=st.session_state.api_key,
            placeholder="sk-...（在 platform.deepseek.com 获取）",
            help="设置后全员共用，员工端无需填写"
        )
        if api_key and api_key != st.session_state.api_key:
            _save_api_key(api_key)
            st.session_state.api_key = api_key
            st.success("API Key 已保存")
    else:
        if st.session_state.api_key:
            st.success("✅ 已配置（由管理员设置）")
        else:
            st.warning("⚠️ 管理员尚未配置 API Key，AI 功能暂不可用")

    if is_admin:
        st.divider()
        st.subheader("🔐 管理员密码")
        adm_old = st.text_input("当前密码", type="password", key="sett_adm_old")
        adm_new = st.text_input("新密码", type="password", key="sett_adm_new")
        adm_new2 = st.text_input("确认新密码", type="password", key="sett_adm_new2")
        if st.button("修改密码", key="sett_change_pwd", use_container_width=True):
            if adm_old != _load_admin_password():
                st.error("当前密码错误")
            elif not adm_new:
                st.error("新密码不能为空")
            elif adm_new != adm_new2:
                st.error("两次密码不一致")
            else:
                _save_admin_password(adm_new)
                globals()["ADMIN_PASSWORD"] = adm_new
                st.success("密码修改成功，立即生效")

    st.divider()
    st.caption("请假单智能处理助手 v1.0")


# ==================== 已登录 → 顶部工具栏 ====================
_col_title, _col_set = st.columns([15, 1])
with _col_set:
    if st.button("⚙️", help="设置", key="top_settings_btn"):
        settings_dialog()


# ============================================================
#  员工视图
# ============================================================
def _render_employee_view():
    emp = st.session_state.current_employee
    api_key = st.session_state.api_key

    st.title(f"📝 请假申请 — {emp['name']}")
    st.caption("描述需求 → AI 解析 → 上传材料 → 提交审批")

    # 提醒：缺材料
    reminders = _check_missing_approvals(emp["id"])
    if reminders:
        for rem in reminders:
            if rem.get("remind_type") == "sick":
                # 病假缺病假单提醒
                if rem["urgent"]:
                    st.error(f"🚨 紧急：病假申请 {rem['record_id']}（{rem['start_date']}）"
                             f"缺少病假证明单，下月第3个工作日前必须补交！仅剩 {rem['days_left']} 天")
                else:
                    st.warning(f"⚠️ 提醒：病假申请 {rem['record_id']}（{rem['start_date']}）"
                               f"缺少病假证明单，请在下月前3个工作日内补交（剩 {rem['days_left']} 天）")
            else:
                if rem["urgent"]:
                    st.error(f"🚨 紧急：申请 {rem['record_id']}（{rem['start_date']} {rem['leave_type']}）"
                             f"缺审批截图，距下月仅剩 {rem['days_left']} 天，请尽快补交！")
                else:
                    st.warning(f"⚠️ 提醒：申请 {rem['record_id']}（{rem['start_date']} {rem['leave_type']}）"
                               f"缺审批截图，请在下月前补交（剩 {rem['days_left']} 天）")

    # 催交通知（仅待审批状态显示，批准后自动消失）
    my_records = [r for r in get_all_leave_records()
                  if r.get("employee_id") == emp["id"]
                  and r.get("urged")
                  and r.get("status") == "待审批"]
    for r in my_records:
        st.error(f"📢 管理员催交：申请 {r['record_id']}（{r.get('start_date','')} {r.get('leave_type','')}）"
                 f"缺少材料，请尽快补交！催交时间：{r.get('urged_time','')}")

    st.divider()
    tab1, tab2 = st.tabs(["✏️ 新建请假", "📋 我的记录"])

    # ========== 新建请假 ==========
    with tab1:
        st.subheader("第1步：描述请假需求")
        user_input = st.text_area("请假描述", placeholder="例如：「下周三到周五请年假3天，回老家」",
                                  height=80, label_visibility="collapsed")

        if not api_key:
            st.info("💡 管理员尚未配置 API Key，AI 功能暂不可用，请联系管理员")

        if st.button("🔍 AI 解析", type="primary", disabled=not api_key or not user_input):
            if api_key and user_input:
                with st.spinner("AI 正在分析..."):
                    try:
                        st.session_state.extracted_info = extract_leave_info(user_input, api_key)
                        st.session_state.check_results = None
                        st.session_state.form_md = None
                        st.session_state.app_image_path = None
                        st.session_state.app_analysis = None
                        st.session_state.approval_image_path = None
                        st.session_state.approval_analysis = None
                    except Exception as e:
                        st.error(f"AI 解析出错：{e}")

        if st.session_state.extracted_info:
            info = st.session_state.extracted_info
            st.divider()
            st.subheader("📌 请假信息确认")

            col_a, col_b = st.columns(2)
            lt_options = ["年假", "事假", "带薪病假", "调休", "婚假", "产假"]
            lt_idx = lt_options.index(info.get("leave_type")) if info.get("leave_type") in lt_options else 0

            # 解析 AI 返回的时间
            start_t = _parse_time(info.get("start_time", "09:00"))
            end_t = _parse_time(info.get("end_time", "18:00"))

            with col_a:
                leave_type = st.selectbox("请假类型", lt_options, index=lt_idx)
                start_date = st.date_input("开始日期", value=_parse_date(info.get("start_date")))
                start_hour = st.selectbox("开始时间", list(range(0, 24)),
                    index=start_t[0], format_func=lambda h: f"{h:02d}:00")
            with col_b:
                end_date = st.date_input("结束日期", value=_parse_date(info.get("end_date")))
                end_hour = st.selectbox("结束时间", list(range(0, 24)),
                    index=end_t[0], format_func=lambda h: f"{h:02d}:00")
                # 自动计算天数
                start_time_str = f"{start_hour:02d}:00"
                end_time_str = f"{end_hour:02d}:00"
                days, weekends = _calc_days(start_date, start_time_str, end_date, end_time_str)
                st.metric("自动计算天数", f"{days} 天")
                if weekends:
                    st.warning("⚠️ " + "、".join(weekends) + " 为周末，无需请假，已自动排除，不计入请假天数")

            reason = st.text_input("请假事由", value=info.get("reason", ""))

            if st.button("✅ 校验信息", type="primary"):
                results = validate_leave_request(emp["id"], leave_type,
                    start_date.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d"), days)
                st.session_state.check_results = results
                if not any(r.level == "fail" for r in results):
                    updated_info = {
                        "leave_type": leave_type, "start_date": start_date.strftime("%Y-%m-%d"),
                        "start_time": start_time_str, "end_date": end_date.strftime("%Y-%m-%d"),
                        "end_time": end_time_str, "days": days, "reason": reason
                    }
                    st.session_state.form_md = generate_approval_form(emp["id"], updated_info, results)

        if st.session_state.check_results:
            st.divider()
            st.subheader("🔍 校验结果")
            for r in st.session_state.check_results:
                if r.level == "pass": st.success(f"✅ {r.message}")
                elif r.level == "warn": st.warning(f"⚠️ {r.message}")
                else: st.error(f"❌ {r.message}")

        if st.session_state.form_md:
            st.divider()
            st.subheader("第2步：上传证明材料")

            is_sick = (leave_type == "带薪病假")

            # ===== 申请表 + 审批截图（所有假期都需要）=====
            col_u1, col_u2 = st.columns(2)
            with col_u1:
                st.markdown("**📄 请假申请表截图**")
                app_file = st.file_uploader("上传申请表", type=["png","jpg","jpeg","webp"], key="app_up")
                if app_file:
                    st.session_state.app_image_path = _save_uploaded_image(app_file, APPLICATION_DIR, emp["id"])
                    st.image(app_file, caption="申请表预览", width=300)
                    st.success("✅ 申请表已上传")
            with col_u2:
                st.markdown("**🖊️ 审批截图**（可后续补交）")
                apr_file = st.file_uploader("上传审批截图", type=["png","jpg","jpeg","webp"], key="apr_up")
                if apr_file:
                    st.session_state.approval_image_path = _save_uploaded_image(apr_file, APPROVAL_DIR, emp["id"])
                    st.image(apr_file, caption="审批截图预览", width=300)
                    st.success("✅ 审批截图已上传")

            # ===== 病假额外：病假单 =====
            if is_sick:
                st.divider()
                st.markdown("**🏥 病假证明单**（必传，将推送管理员审核）")
                sick_file = st.file_uploader("上传病假单", type=["png","jpg","jpeg","webp"], key="sick_up")
                if sick_file:
                    st.session_state.sick_cert_image_path = _save_uploaded_image(sick_file, SICK_CERT_DIR, emp["id"])
                    st.image(sick_file, caption="病假单预览", width=300)
                    st.success("✅ 病假单已上传，将推送管理员审核")

            st.divider()
            st.subheader("📄 审批单预览")
            st.markdown(st.session_state.form_md)

            st.info("📋 提交后将推送管理员审核")

            if st.button("✉️ 确认提交", type="primary"):
                record = {
                    "employee_id": emp["id"],
                    "leave_type": leave_type,
                    "start_date": start_date.strftime("%Y-%m-%d"),
                    "start_time": start_time_str,
                    "end_date": end_date.strftime("%Y-%m-%d"),
                    "end_time": end_time_str,
                    "days": days,
                    "reason": reason,
                    "application_image": st.session_state.app_image_path,
                    "approval_image": st.session_state.approval_image_path,
                    "sick_certificate_image": st.session_state.sick_cert_image_path if is_sick else None,
                }
                added = add_leave_record(record)
                update_leave_balance(emp["id"], leave_type, days)
                st.session_state.current_employee = get_employee_by_id(emp["id"])
                st.success(f"请假申请已提交！编号：{added['record_id']}")
                st.balloons()
                for key in ["extracted_info","check_results","form_md",
                           "app_image_path","approval_image_path",
                           "sick_cert_image_path"]:
                    st.session_state[key] = None
                st.rerun()

    # ========== 我的记录 ==========
    with tab2:
        st.subheader("📋 我的请假记录")
        from config import LEAVE_RECORDS_FILE
        my_records = [r for r in get_all_leave_records() if r["employee_id"] == emp["id"]]
        if my_records:
            for rec in my_records[::-1]:
                status_icon = {"已批准":"✅","待审批":"⏳","已拒绝":"❌","已复核":"✅✅"}
                st.markdown(f"""
                **{status_icon.get(rec['status'],'')} {rec['leave_type']} {rec['days']}天** | {rec['start_date']} {rec.get('start_time','')} ~ {rec['end_date']} {rec.get('end_time','')}
                📝 {rec.get('reason','')} | 🏷️ {rec['record_id']} | {rec['status']}
                """)
                ic = st.columns(2)
                if rec.get("application_image"):
                    p = Path(__file__).parent / rec["application_image"]
                    if p.exists():
                        with ic[0]: st.caption("📄 申请表"); st.image(str(p), use_container_width=True)
                if rec.get("approval_image"):
                    p = Path(__file__).parent / rec["approval_image"]
                    if p.exists():
                        with ic[1]: st.caption("🖊️ 审批截图"); st.image(str(p), use_container_width=True)
                if rec.get("sick_certificate_image"):
                    p = Path(__file__).parent / rec["sick_certificate_image"]
                    if p.exists():
                        with st.container(): st.caption("🏥 病假单"); st.image(str(p), use_container_width=True)

                # 补交材料
                is_sick_rec = (rec.get("leave_type") == "带薪病假")
                if rec.get("status") == "待审批":
                    if is_sick_rec and not rec.get("sick_certificate_image"):
                        with st.expander("📤 补交病假单"):
                            late = st.file_uploader("上传病假单", type=["png","jpg","jpeg","webp"],
                                                    key=f"late_sick_{rec['record_id']}")
                            if late:
                                img_path = _save_uploaded_image(late, SICK_CERT_DIR, emp["id"])
                                all_records = get_all_leave_records()
                                for r in all_records:
                                    if r["record_id"] == rec["record_id"]:
                                        r["sick_certificate_image"] = img_path; break
                                with open(LEAVE_RECORDS_FILE, "w", encoding="utf-8") as f:
                                    json.dump(all_records, f, ensure_ascii=False, indent=2)
                                st.success("病假单已补交")
                                st.rerun()
                    elif not is_sick_rec and not rec.get("approval_image"):
                        with st.expander("📤 补交审批截图"):
                            late = st.file_uploader("上传审批截图", type=["png","jpg","jpeg","webp"],
                                                    key=f"late_{rec['record_id']}")
                            if late:
                                img_path = _save_uploaded_image(late, APPROVAL_DIR, emp["id"])
                                all_records = get_all_leave_records()
                                for r in all_records:
                                    if r["record_id"] == rec["record_id"]:
                                        r["approval_image"] = img_path; break
                                with open(LEAVE_RECORDS_FILE, "w", encoding="utf-8") as f:
                                    json.dump(all_records, f, ensure_ascii=False, indent=2)
                                st.success("审批截图已补交")
                                st.rerun()
                st.divider()
        else:
            st.info("暂无请假记录")


# ============================================================
#  管理员视图
# ============================================================
def _render_admin_view():
    st.caption("### 🔑 管理员审批中心")

    tab1, tab2, tab3, tab4, tab5 = st.tabs(["⏳待审批", "🔍复核", "📋全部", "👥员工", "📥导出"])

    # ---- 待审批 ----
    with tab1:
        pending = get_pending_records()
        if not pending:
            st.success("暂无待审批的请假申请")
        else:
            name_map = {e["id"]: e for e in get_all_employees()}
            for req in pending:
                emp = name_map.get(req["employee_id"])
                ename = emp["name"] if emp else req["employee_id"]
                edept = emp["department"] if emp else "未知"
                is_sick = (req.get("leave_type") == "带薪病假")

                with st.container(border=True):
                    # 紧凑头部
                    icon = "🏥" if is_sick else "📋"
                    title = f"{icon} {ename} · {req['leave_type']} {req['days']}天"
                    st.caption(f"**{title}**  |  {edept}  |  {req['start_date']} {req.get('start_time','')} ~ {req['end_date']} {req.get('end_time','')}  |  {req.get('reason','')}  |  {req['record_id']}")

                    # 紧凑材料状态 + 操作按钮同行
                    col_doc1, col_doc2, col_btn1, col_btn2 = st.columns([1.3, 1.3, 0.7, 0.7])
                    with col_doc1:
                        st.caption("📄有" if req.get("application_image") else "📄缺表")
                    with col_doc2:
                        if is_sick:
                            st.caption("🏥有" if req.get("sick_certificate_image") else "🏥缺单")
                        else:
                            st.caption("🖊️有" if req.get("approval_image") else "🖊️缺批")
                    with col_btn1:
                        if st.button("❌拒绝", key=f"rej_{req['record_id']}", use_container_width=True):
                            reject_record(req['record_id']); st.rerun()
                    with col_btn2:
                        has_missing = (not req.get("application_image")) or \
                                      (not req.get("approval_image")) or \
                                      (is_sick and not req.get("sick_certificate_image"))
                        if st.button("📢催交", key=f"urge_{req['record_id']}",
                                     disabled=req.get("urged", False) or not has_missing,
                                     use_container_width=True):
                            all_records = get_all_leave_records()
                            for r in all_records:
                                if r["record_id"] == req["record_id"]:
                                    r["urged"] = True
                                    r["urged_time"] = datetime.now().strftime("%Y-%m-%d %H:%M"); break
                            from config import LEAVE_RECORDS_FILE
                            with open(LEAVE_RECORDS_FILE, "w", encoding="utf-8") as f:
                                json.dump(all_records, f, ensure_ascii=False, indent=2)
                            st.rerun()

                    # 折叠区：图片 + 编辑
                    with st.expander("🔍 查看图片 / 编辑信息"):
                        # 图片
                        ic2 = st.columns(2)
                        if req.get("application_image"):
                            p = Path(__file__).parent / req["application_image"]
                            if p.exists(): ic2[0].image(str(p), caption="申请表", use_container_width=True)
                        if req.get("approval_image"):
                            p = Path(__file__).parent / req["approval_image"]
                            if p.exists(): ic2[1].image(str(p), caption="审批截图", use_container_width=True)
                        if is_sick and req.get("sick_certificate_image"):
                            p = Path(__file__).parent / req["sick_certificate_image"]
                            if p.exists(): st.image(str(p), caption="病假单", width=350)

                        st.divider()
                        st.caption("✏️ 编辑（修改后点击批准生效）")
                        lt_opts = ["年假","事假","带薪病假","调休","婚假","产假"]
                        orig_lt = req.get("leave_type","年假")
                        new_lt = st.selectbox("类型", lt_opts,
                            index=lt_opts.index(orig_lt) if orig_lt in lt_opts else 0, key=f"elt_{req['record_id']}", label_visibility="collapsed")
                        ec1, ec2, ec3, ec4 = st.columns(4)
                        new_sd = ec1.date_input("开始日期", value=_parse_date(req.get("start_date","")), key=f"esd_{req['record_id']}", label_visibility="collapsed")
                        new_st = ec2.selectbox("开始时间", list(range(0,24)),
                            index=_parse_time(req.get("start_time","09:00"))[0],
                            format_func=lambda h: f"{h:02d}", key=f"est_{req['record_id']}", label_visibility="collapsed")
                        new_ed = ec3.date_input("结束日期", value=_parse_date(req.get("end_date","")), key=f"eed_{req['record_id']}", label_visibility="collapsed")
                        new_et = ec4.selectbox("结束时间", list(range(0,24)),
                            index=_parse_time(req.get("end_time","18:00"))[0],
                            format_func=lambda h: f"{h:02d}", key=f"eet_{req['record_id']}", label_visibility="collapsed")
                        nr1, nr2, nr3 = st.columns([1, 1, 2])
                        new_days = nr1.number_input("天数", min_value=0.5, step=0.5, value=float(req.get("days",1)), key=f"eds_{req['record_id']}", label_visibility="collapsed")
                        new_reason = nr2.text_input("事由", value=req.get("reason",""), key=f"ers_{req['record_id']}", label_visibility="collapsed")
                        edit_note = nr3.text_input("备注", placeholder="修改原因", key=f"eno_{req['record_id']}", label_visibility="collapsed")

                        if st.button("✅ 保存并批准", key=f"appr2_{req['record_id']}", type="primary"):
                            all_records = get_all_leave_records()
                            for r in all_records:
                                if r["record_id"] == req["record_id"]:
                                    r["leave_type"] = new_lt
                                    r["start_date"] = new_sd.strftime("%Y-%m-%d")
                                    r["start_time"] = f"{new_st:02d}:00"
                                    r["end_date"] = new_ed.strftime("%Y-%m-%d")
                                    r["end_time"] = f"{new_et:02d}:00"
                                    r["days"] = new_days
                                    r["reason"] = new_reason
                                    r.pop("urged", None)
                                    if edit_note: r["admin_note"] = edit_note
                                    break
                            from config import LEAVE_RECORDS_FILE
                            with open(LEAVE_RECORDS_FILE, "w", encoding="utf-8") as f:
                                json.dump(all_records, f, ensure_ascii=False, indent=2)
                            approve_record(req['record_id'])
                            st.rerun()

    # ---- 复核确认 ----
    with tab2:
        st.subheader("🔍 已批准待复核的请假申请")
        st.caption("审批通过后需管理员二次复核确认，确保假期余额和材料无误")
        to_review = get_approved_records()
        if not to_review:
            st.success("暂无待复核的请假申请")
        else:
            name_map = {e["id"]: e for e in get_all_employees()}
            for req in to_review:
                emp = name_map.get(req["employee_id"])
                ename = emp["name"] if emp else req["employee_id"]
                edept = emp["department"] if emp else "未知"

                st.markdown(f"""
                **{ename}** ({edept}) | **{req['leave_type']}** {req['days']}天
                📅 {req['start_date']} {req.get('start_time','')} ~ {req['end_date']} {req.get('end_time','')}
                📝 {req.get('reason','')} | 🏷️ {req['record_id']} | ✅ 已批准
                """)

                # 展示材料
                img_cols = []
                if req.get("application_image"):
                    p = Path(__file__).parent / req["application_image"]
                    if p.exists(): img_cols.append(("📄 申请表", str(p)))
                if req.get("approval_image"):
                    p = Path(__file__).parent / req["approval_image"]
                    if p.exists(): img_cols.append(("🖊️ 审批截图", str(p)))
                if req.get("sick_certificate_image"):
                    p = Path(__file__).parent / req["sick_certificate_image"]
                    if p.exists(): img_cols.append(("🏥 病假单", str(p)))
                if img_cols:
                    ics = st.columns(len(img_cols))
                    for i, (cap, pth) in enumerate(img_cols):
                        with ics[i]: st.caption(cap); st.image(pth, width=200)

                c1, c2, _ = st.columns([1, 1, 3])
                with c1:
                    if st.button("✅ 确认复核", key=f"review_{req['record_id']}", type="primary"):
                        review_record(req['record_id'])
                        st.success(f"{req['record_id']} 已复核确认")
                        st.rerun()
                with c2:
                    if st.button("↩️ 撤销批准", key=f"unapprove_{req['record_id']}"):
                        reject_record(req['record_id'])
                        st.warning(f"{req['record_id']} 已退回待审批")
                        st.rerun()
                st.divider()

    # ---- 全部记录 ----
    with tab3:
        st.subheader("全部请假记录")
        records = get_all_leave_records()
        if not records:
            st.info("暂无记录")
        else:
            nm = {e["id"]: e for e in get_all_employees()}
            sm = {"已批准":"✅ 已批准","待审批":"⏳ 待审批","已拒绝":"❌ 已拒绝","已复核":"✅✅ 已复核"}

            # 搜索过滤
            search = st.text_input("🔍 搜索（姓名/工号/事由）", key="record_search")
            filtered = records[::-1]
            if search:
                filtered = [r for r in filtered if
                    search.lower() in str(nm.get(r["employee_id"], {}).get("name", "")).lower() or
                    search.lower() in str(r.get("employee_id", "")).lower() or
                    search.lower() in str(r.get("reason", "")).lower()]

            for rec in filtered:
                emp = nm.get(rec["employee_id"], {})
                ename = emp.get("name", rec["employee_id"])
                edept = emp.get("department", "未知")

                # 可展开的折叠行
                with st.expander(
                    f"{sm.get(rec['status'], rec['status'])} | {rec['leave_type']} {rec['days']}天 | "
                    f"{ename}({edept}) | {rec['start_date']} {rec.get('start_time','')} ~ {rec['end_date']} {rec.get('end_time','')} | "
                    f"{rec['record_id']}"
                ):
                    # 详细信息
                    st.markdown(f"""
                    | 项目 | 内容 |
                    |------|------|
                    | 编号 | {rec['record_id']} |
                    | 姓名 | {ename} |
                    | 工号 | {rec['employee_id']} |
                    | 部门 | {edept} |
                    | 类型 | {rec['leave_type']} |
                    | 开始 | {rec['start_date']} {rec.get('start_time','')} |
                    | 结束 | {rec['end_date']} {rec.get('end_time','')} |
                    | 天数 | {rec['days']} 天 |
                    | 事由 | {rec.get('reason','')} |
                    | 状态 | {sm.get(rec['status'], rec['status'])} |
                    """)

                    # 显示上传图片（所有假期都显示申请表+审批截图）
                    is_sick = rec.get("leave_type") == "带薪病假"
                    ic = st.columns(2)
                    if rec.get("application_image"):
                        p = Path(__file__).parent / rec["application_image"]
                        if p.exists():
                            with ic[0]: st.caption("📄 申请表"); st.image(str(p), use_container_width=True)
                    if rec.get("approval_image"):
                        p = Path(__file__).parent / rec["approval_image"]
                        if p.exists():
                            with ic[1]: st.caption("🖊️ 审批截图"); st.image(str(p), use_container_width=True)
                    # 病假额外显示病假单
                    if is_sick and rec.get("sick_certificate_image"):
                        p = Path(__file__).parent / rec["sick_certificate_image"]
                        if p.exists():
                            st.caption("🏥 病假证明单")
                            st.image(str(p), width=400)

                    # 如果待审批，可在此快速审批
                    if rec.get("status") == "待审批":
                        c1, c2 = st.columns(2)
                        with c1:
                            if st.button("✅ 批准", key=f"all_appr_{rec['record_id']}"):
                                approve_record(rec['record_id'])
                                st.rerun()
                        with c2:
                            if st.button("❌ 拒绝", key=f"all_rej_{rec['record_id']}"):
                                reject_record(rec['record_id'])
                                st.rerun()

    # ---- 员工管理 ----
    with tab4:
        st.subheader("👥 员工信息总览")
        import pandas as pd
        emps = get_all_employees()
        rows = []
        for e in emps:
            row = {"姓名": e["name"], "花名": e.get("nickname",""), "工号": e["id"],
                   "性别": e.get("gender",""), "部门": e["department"],
                   "毕业时间": e.get("graduation_date",""), "入职时间": e.get("hire_date",""),
                   "密码": e.get("password","123456")}
            row.update(e["leave_balance"])
            rows.append(row)
        df = pd.DataFrame(rows)
        st.dataframe(df, use_container_width=True, hide_index=True)

        # 构建员工选项（后续多处使用）
        emp_list = get_all_employees()
        emp_opts = {f"{e['name']} ({e['id']})": e for e in emp_list}

        st.divider()

        # ===== 员工操作区 =====
        col_left, col_right = st.columns(2)

        with col_left:
            st.subheader("➕ 添加新员工")
            new_id = st.text_input("工号", placeholder="如 EMP009", key="add_id")
            new_name = st.text_input("姓名", key="add_name")
            new_nickname = st.text_input("花名", placeholder="如：小王", key="add_nickname")
            new_gender = st.selectbox("性别", ["男", "女"], key="add_gender")
            col_ad1, col_ad2 = st.columns(2)
            new_grad = col_ad1.text_input("毕业时间", value="2020-07-01", placeholder="YYYY-MM-DD", key="add_grad")
            new_hire = col_ad2.text_input("入职时间", value="2022-01-01", placeholder="YYYY-MM-DD", key="add_hire")
            new_dept = st.text_input("部门", key="add_dept")
            new_pwd = st.text_input("初始密码", value="123456", key="add_pwd")

            # 预览计算出的假期天数
            try:
                preview_balance = _calc_shanghai_leave(new_grad, new_hire, new_gender)
                st.caption(f"自动计算假期：{' | '.join([f'{k}={v}天' for k,v in preview_balance.items()])}")
            except: pass

            if st.button("添加员工", type="primary", key="add_emp_btn"):
                if new_id and new_name and new_dept:
                    all_emps = get_all_employees()
                    if any(e["id"] == new_id for e in all_emps):
                        st.error(f"工号 {new_id} 已存在")
                    else:
                        all_emps.append({
                            "id": new_id, "name": new_name, "nickname": new_nickname,
                            "gender": new_gender, "graduation_date": new_grad,
                            "hire_date": new_hire, "department": new_dept,
                            "password": new_pwd,
                            "leave_balance": preview_balance
                        })
                        from config import EMPLOYEES_FILE
                        with open(EMPLOYEES_FILE, "w", encoding="utf-8") as f:
                            json.dump(all_emps, f, ensure_ascii=False, indent=2)
                        st.success(f"员工 {new_name} ({new_id}) 已添加")
                        st.rerun()
                else:
                    st.error("工号、姓名、部门不能为空")

            st.divider()
            st.subheader("🗑️ 删除员工")
            del_emp = st.selectbox("选择要删除的员工", list(emp_opts.keys()), key="del_emp")
            if st.button("删除员工", type="primary", key="del_emp_btn"):
                target = emp_opts[del_emp]
                all_emps = get_all_employees()
                all_emps = [e for e in all_emps if e["id"] != target["id"]]
                from config import EMPLOYEES_FILE
                with open(EMPLOYEES_FILE, "w", encoding="utf-8") as f:
                    json.dump(all_emps, f, ensure_ascii=False, indent=2)
                st.warning(f"员工 {target['name']} ({target['id']}) 已删除")
                st.rerun()

        with col_right:
            st.subheader("✏️ 编辑员工信息")
            edit_sel = st.selectbox("选择员工", list(emp_opts.keys()), key="edit_emp_sel")
            edit_target = emp_opts[edit_sel]

            edit_name = st.text_input("姓名", value=edit_target.get("name",""), key="edit_name")
            edit_nickname = st.text_input("花名", value=edit_target.get("nickname",""), key="edit_nickname")
            edit_gender = st.selectbox("性别", ["男","女"],
                index=0 if edit_target.get("gender","男")=="男" else 1, key="edit_gender")
            col_ed1, col_ed2 = st.columns(2)
            edit_grad = col_ed1.text_input("毕业时间",
                value=edit_target.get("graduation_date",""), key="edit_grad")
            edit_hire = col_ed2.text_input("入职时间",
                value=edit_target.get("hire_date",""), key="edit_hire")
            edit_dept = st.text_input("部门", value=edit_target.get("department",""), key="edit_dept")
            edit_pwd = st.text_input("密码", value=edit_target.get("password","123456"), key="edit_pwd")

            col_save, col_recalc = st.columns(2)
            with col_save:
                if st.button("💾 保存信息", type="primary", key="save_edit_btn", use_container_width=True):
                    all_emps = get_all_employees()
                    for e in all_emps:
                        if e["id"] == edit_target["id"]:
                            e["name"] = edit_name
                            e["nickname"] = edit_nickname
                            e["gender"] = edit_gender
                            e["graduation_date"] = edit_grad
                            e["hire_date"] = edit_hire
                            e["department"] = edit_dept
                            e["password"] = edit_pwd
                            break
                    from config import EMPLOYEES_FILE
                    with open(EMPLOYEES_FILE, "w", encoding="utf-8") as f:
                        json.dump(all_emps, f, ensure_ascii=False, indent=2)
                    st.success(f"{edit_name} 信息已更新")
                    st.rerun()
            with col_recalc:
                if st.button("🔄 重算假期", key="recalc_leave_btn", use_container_width=True,
                             help="按上海标准重新计算该员工所有假期天数"):
                    new_balance = _calc_shanghai_leave(edit_grad, edit_hire, edit_gender)
                    all_emps = get_all_employees()
                    for e in all_emps:
                        if e["id"] == edit_target["id"]:
                            e["graduation_date"] = edit_grad
                            e["hire_date"] = edit_hire
                            e["gender"] = edit_gender
                            e["leave_balance"] = new_balance
                            break
                    from config import EMPLOYEES_FILE
                    with open(EMPLOYEES_FILE, "w", encoding="utf-8") as f:
                        json.dump(all_emps, f, ensure_ascii=False, indent=2)
                    st.success("假期天数已按上海标准重新计算")
                    st.info(f"{' | '.join([f'{k}={v}天' for k,v in new_balance.items()])}")
                    st.rerun()

        # ===== 假期类型 & 余额管理 =====
        st.divider()
        st.subheader("📊 假期类型 & 余额管理")

        balance_emp_sel = st.selectbox("选择员工", list(emp_opts.keys()), key="balance_emp_sel")
        balance_target = emp_opts[balance_emp_sel]
        cur_balance = balance_target.get("leave_balance", {})

        # 表单式余额编辑
        with st.form(key="balance_form"):
            st.markdown(f"**{balance_target['name']}** ({balance_target['id']}) — {balance_target['department']}")

            updated_balance = {}
            bal_cols = st.columns(3)
            leave_keys = list(cur_balance.keys())
            for i, lk in enumerate(leave_keys):
                with bal_cols[i % 3]:
                    updated_balance[lk] = st.number_input(
                        f"{lk}（天）", min_value=0.0, max_value=365.0, step=0.5,
                        value=float(cur_balance[lk]), key=f"bal_{balance_target['id']}_{lk}"
                    )

            # 添加/删除假期类型（表单内）
            col_add, col_del = st.columns(2)
            with col_add:
                st.caption("添加假期类型")
                col_a1, col_a2 = st.columns([2, 1])
                new_type = col_a1.text_input("假期名称", key="new_type")
                new_days = col_a2.number_input("天数", min_value=0.5, step=0.5, value=5.0, key="new_days")
            with col_del:
                st.caption("删除假期类型")
                del_type = st.selectbox("选择", leave_keys if len(leave_keys) > 1 else ["—"], key="del_type")

            if st.form_submit_button("💾 保存", type="primary", use_container_width=True):
                # 处理添加
                if new_type and new_type not in updated_balance:
                    updated_balance[new_type] = new_days
                # 处理删除
                if del_type and del_type != "—" and del_type in updated_balance and len(updated_balance) > 1:
                    del updated_balance[del_type]
                # 保存
                all_emps = get_all_employees()
                for e in all_emps:
                    if e["id"] == balance_target["id"]:
                        e["leave_balance"] = updated_balance
                        break
                from config import EMPLOYEES_FILE
                with open(EMPLOYEES_FILE, "w", encoding="utf-8") as f:
                    json.dump(all_emps, f, ensure_ascii=False, indent=2)
                st.success(f"{balance_target['name']} 假期余额已保存")
                st.rerun()

        # 该员工请假记录
        st.divider()
        st.subheader(f"📋 {balance_target['name']} 的请假记录")
        emp_records = [r for r in get_all_leave_records() if r.get("employee_id") == balance_target["id"]]
        if emp_records:
            import pandas as pd
            # 时间范围筛选
            rec_date_min = min(r.get("start_date","") for r in emp_records if r.get("start_date"))
            rec_date_max = max(r.get("start_date","") for r in emp_records if r.get("start_date"))
            try:
                dmin = datetime.strptime(rec_date_min,"%Y-%m-%d").date()
                dmax = datetime.strptime(rec_date_max,"%Y-%m-%d").date()
            except: dmin, dmax = date.today(), date.today()
            range_dates = st.date_input("时间范围", value=(dmin, dmax), key="emp_rec_dates")
            if len(range_dates) == 2:
                emp_records = [r for r in emp_records
                    if range_dates[0] <= _parse_date(r.get("start_date","")) <= range_dates[1]]

            rows = [{
                "编号": r.get("record_id",""), "类型": r.get("leave_type",""),
                "日期": f"{r.get('start_date','')}~{r.get('end_date','')}", "天数": f"{r.get('days',0)}天",
                "事由": r.get("reason",""), "状态": r.get("status","")
            } for r in emp_records[::-1]]
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

            # 统计
            type_stats = {}
            for r in emp_records:
                if r.get("status") in ("已批准","已复核"):
                    lt = r.get("leave_type","")
                    type_stats[lt] = type_stats.get(lt, 0) + r.get("days",0)
            if type_stats:
                st.caption("已批准假期统计：")
                st.text(" | ".join([f"{k}: {v}天" for k,v in type_stats.items()]))
        else:
            st.info("该员工暂无请假记录")

        st.divider()
        st.subheader("🔗 上海市员工假期标准")
        st.markdown("""
        | 假期类型 | 计算规则 |
        |------|------|
        | **年假** | 累计工龄（毕业起）1-10年:5天，10-20年:10天，20年以上:15天，每年自动更新 |
        | **婚假** | 10天（上海标准） |
        | **产假** | 128天（上海标准，仅女性） |
        | **陪产假** | 10天（上海标准，仅男性） |
        | **病假** | 5天（默认，可按需要手动调整） |
        | **事假** | 10天（默认，可按需要手动调整） |
        | **调休** | 3天（默认，可按需要手动调整） |
        """)
        st.caption("添加新员工时填写毕业时间和入职时间，系统自动按以上标准计算假期天数。编辑员工时可点击「重算假期」按钮按当前日期重新计算年假。")

        st.divider()
        st.subheader("🔧 批量更新年假")
        st.caption("每年刷新一次，按当前日期重新计算所有员工的年假天数")
        if st.button("🔄 批量重算全员年假", type="primary", key="batch_recalc"):
            all_emps = get_all_employees()
            updated_count = 0
            for e in all_emps:
                new_bal = _calc_shanghai_leave(
                    e.get("graduation_date",""), e.get("hire_date",""), e.get("gender","男"))
                e["leave_balance"] = new_bal
                updated_count += 1
            from config import EMPLOYEES_FILE
            with open(EMPLOYEES_FILE, "w", encoding="utf-8") as f:
                json.dump(all_emps, f, ensure_ascii=False, indent=2)
            st.success(f"已更新 {updated_count} 名员工的假期天数")
            st.rerun()

    # ---- 数据导出 ----
    with tab5:
        st.subheader("📥 数据导出")
        st.caption("按条件筛选并导出请假记录")

        import pandas as pd
        import csv

        all_records = get_all_leave_records()
        all_emps = {e["id"]: e for e in get_all_employees()}

        # 筛选条件
        col_f1, col_f2, col_f3 = st.columns(3)
        with col_f1:
            emp_names = ["全部员工"] + [f"{e['name']} ({e['id']})" for e in get_all_employees()]
            sel_emp = st.selectbox("员工范围", emp_names, key="export_emp")
        with col_f2:
            date_range = st.date_input("日期范围",
                value=(date.today().replace(day=1), date.today()),
                key="export_dates")
        with col_f3:
            leave_types = ["全部类型", "年假", "事假", "带薪病假", "调休", "婚假", "产假"]
            sel_type = st.selectbox("请假类型", leave_types, key="export_type")

        # 筛选数据
        filtered = []
        for r in all_records:
            # 员工筛选
            if sel_emp != "全部员工":
                emp_id = sel_emp.split("(")[-1].rstrip(")")
                if r.get("employee_id") != emp_id:
                    continue
            # 日期筛选
            if len(date_range) == 2:
                try:
                    r_start = datetime.strptime(r.get("start_date",""), "%Y-%m-%d").date()
                    if r_start < date_range[0] or r_start > date_range[1]:
                        continue
                except: pass
            # 类型筛选
            if sel_type != "全部类型" and r.get("leave_type") != sel_type:
                continue
            filtered.append(r)

        st.metric("筛选结果", f"{len(filtered)} 条记录")

        if filtered:
            # 构建导出表格
            rows = []
            for r in filtered:
                emp = all_emps.get(r.get("employee_id",""), {})
                rows.append({
                    "编号": r.get("record_id",""),
                    "姓名": emp.get("name",""),
                    "工号": r.get("employee_id",""),
                    "部门": emp.get("department",""),
                    "请假类型": r.get("leave_type",""),
                    "开始日期": r.get("start_date",""),
                    "开始时间": r.get("start_time",""),
                    "结束日期": r.get("end_date",""),
                    "结束时间": r.get("end_time",""),
                    "天数": r.get("days",0),
                    "事由": r.get("reason",""),
                    "状态": r.get("status",""),
                    "有申请表": "是" if r.get("application_image") else "否",
                    "有审批截图": "是" if r.get("approval_image") else "否",
                    "有病假单": "是" if r.get("sick_certificate_image") else "否",
                    "管理员备注": r.get("admin_note",""),
                })
            df = pd.DataFrame(rows)

            # 预览表格
            st.dataframe(df, use_container_width=True, hide_index=True)

            # 导出按钮
            export_fmt = st.radio("导出格式", ["CSV（可用Excel打开）", "Excel"], horizontal=True)
            if "Excel" in export_fmt:
                import io
                buf = io.BytesIO()
                with pd.ExcelWriter(buf, engine="openpyxl") as writer:
                    df.to_excel(writer, index=False, sheet_name="请假记录")
                st.download_button(
                    "📥 下载 Excel",
                    data=buf.getvalue(),
                    file_name=f"请假记录_{date.today().strftime('%Y%m%d')}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )
            else:
                csv_data = df.to_csv(index=False, encoding="utf-8-sig")
                st.download_button(
                    "📥 下载 CSV",
                    data=csv_data,
                    file_name=f"请假记录_{date.today().strftime('%Y%m%d')}.csv",
                    mime="text/csv"
                )
        else:
            st.info("没有符合筛选条件的记录")


# ==================== 主入口 ====================
if st.session_state.user_role == "employee":
    _render_employee_view()
else:
    _render_admin_view()
