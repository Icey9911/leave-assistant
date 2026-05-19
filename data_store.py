"""数据存储模块 —— 员工信息、假期余额、请假记录的 CRUD 操作"""

import json
from datetime import date, datetime
from pathlib import Path
from config import EMPLOYEES_FILE, LEAVE_RECORDS_FILE


def _load_json(filepath: Path) -> list:
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_json(filepath: Path, data: list) -> None:
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ========== 员工相关 ==========

def get_all_employees() -> list[dict]:
    return _load_json(EMPLOYEES_FILE)


def get_employee_by_id(emp_id: str) -> dict | None:
    for emp in _load_json(EMPLOYEES_FILE):
        if emp["id"] == emp_id:
            return emp
    return None


def get_employee_by_name(name: str) -> dict | None:
    for emp in _load_json(EMPLOYEES_FILE):
        if emp["name"] == name:
            return emp
    return None


def update_leave_balance(emp_id: str, leave_type: str, used_days: float) -> bool:
    """扣减假期余额，返回是否成功"""
    employees = _load_json(EMPLOYEES_FILE)
    for emp in employees:
        if emp["id"] == emp_id:
            if emp["leave_balance"].get(leave_type, 0) >= used_days:
                emp["leave_balance"][leave_type] -= used_days
                _save_json(EMPLOYEES_FILE, employees)
                return True
    return False


# ========== 请假记录相关 ==========

def get_all_leave_records() -> list[dict]:
    return _load_json(LEAVE_RECORDS_FILE)


def get_records_by_employee(emp_id: str) -> list[dict]:
    return [r for r in _load_json(LEAVE_RECORDS_FILE) if r["employee_id"] == emp_id]


def get_approved_records_by_department(dept: str) -> list[dict]:
    """获取某部门所有已批准的请假记录"""
    employees = {e["id"]: e for e in _load_json(EMPLOYEES_FILE)}
    records = _load_json(LEAVE_RECORDS_FILE)
    result = []
    for r in records:
        emp = employees.get(r["employee_id"])
        if emp and emp["department"] == dept and r["status"] == "已批准":
            result.append(r)
    return result


def add_leave_record(record: dict) -> dict:
    """新增一条请假记录，自动生成 record_id"""
    records = _load_json(LEAVE_RECORDS_FILE)
    today_str = date.today().strftime("%Y%m")
    count = sum(1 for r in records if r["record_id"].startswith(f"LR{today_str}"))
    record["record_id"] = f"LR{today_str}{count + 1:03d}"
    record["status"] = "待审批"
    records.append(record)
    _save_json(LEAVE_RECORDS_FILE, records)
    return record


def approve_record(record_id: str) -> bool:
    """批准请假记录"""
    records = _load_json(LEAVE_RECORDS_FILE)
    for r in records:
        if r["record_id"] == record_id:
            r["status"] = "已批准"
            _save_json(LEAVE_RECORDS_FILE, records)
            return True
    return False


def review_record(record_id: str) -> bool:
    """复核请假记录（批准后二次确认）"""
    records = _load_json(LEAVE_RECORDS_FILE)
    for r in records:
        if r["record_id"] == record_id:
            r["status"] = "已复核"
            _save_json(LEAVE_RECORDS_FILE, records)
            return True
    return False


def get_approved_records() -> list[dict]:
    """获取已批准但未复核的请假记录"""
    return [r for r in _load_json(LEAVE_RECORDS_FILE) if r["status"] == "已批准"]


def reject_record(record_id: str) -> dict | None:
    """拒绝请假记录，并恢复假期余额"""
    records = _load_json(LEAVE_RECORDS_FILE)
    for r in records:
        if r["record_id"] == record_id:
            r["status"] = "已拒绝"
            _save_json(LEAVE_RECORDS_FILE, records)
            # 恢复余额
            employees = _load_json(EMPLOYEES_FILE)
            for emp in employees:
                if emp["id"] == r["employee_id"]:
                    emp["leave_balance"][r["leave_type"]] += r["days"]
                    _save_json(EMPLOYEES_FILE, employees)
                    break
            return r
    return None


def get_pending_records() -> list[dict]:
    """获取所有待审批的请假记录"""
    return [r for r in _load_json(LEAVE_RECORDS_FILE) if r["status"] == "待审批"]
