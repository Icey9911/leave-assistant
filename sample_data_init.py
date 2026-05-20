"""初始化示例数据"""

import json
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# ========== 员工数据 ==========
employees = [
    {
        "id": "EMP001",
        "name": "张三",
        "department": "行政部",
        "city": "上海",
        "leave_balance": {
            "年假": 5.0,
            "事假": 10.0,
            "带薪病假": 5.0,
            "调休": 3.0,
            "婚假": 10.0,
            "产假": 98.0,
            "出差": 30.0,
            "市内公出": 10.0,
            "漏打卡补卡": 10.0
        }
    },
    {
        "id": "EMP002",
        "name": "李四",
        "department": "行政部",
        "city": "上海",
        "leave_balance": {
            "年假": 2.0,
            "事假": 10.0,
            "病假": 3.0,
            "调休": 1.0,
            "婚假": 10.0,
            "产假": 98.0,
            "出差": 30.0,
            "市内公出": 10.0,
            "漏打卡补卡": 10.0
        }
    },
    {
        "id": "EMP003",
        "name": "王五",
        "department": "财务部",
        "city": "上海",
        "leave_balance": {
            "年假": 10.0,
            "事假": 10.0,
            "带薪病假": 5.0,
            "调休": 5.0,
            "婚假": 10.0,
            "产假": 98.0,
            "出差": 30.0,
            "市内公出": 10.0,
            "漏打卡补卡": 10.0
        }
    },
    {
        "id": "EMP004",
        "name": "赵六",
        "department": "财务部",
        "city": "上海",
        "leave_balance": {
            "年假": 8.0,
            "事假": 10.0,
            "带薪病假": 5.0,
            "调休": 2.0,
            "婚假": 10.0,
            "产假": 98.0,
            "出差": 30.0,
            "市内公出": 10.0,
            "漏打卡补卡": 10.0
        }
    },
    {
        "id": "EMP005",
        "name": "钱七",
        "department": "技术部",
        "city": "上海",
        "leave_balance": {
            "年假": 7.0,
            "事假": 10.0,
            "带薪病假": 5.0,
            "调休": 4.0,
            "婚假": 10.0,
            "产假": 98.0,
            "出差": 30.0,
            "市内公出": 10.0,
            "漏打卡补卡": 10.0
        }
    },
    {
        "id": "EMP006",
        "name": "孙八",
        "department": "技术部",
        "city": "上海",
        "leave_balance": {
            "年假": 3.5,
            "事假": 10.0,
            "病假": 4.0,
            "调休": 0.0,
            "婚假": 10.0,
            "产假": 98.0,
            "出差": 30.0,
            "市内公出": 10.0,
            "漏打卡补卡": 10.0
        }
    },
    {
        "id": "EMP007",
        "name": "周九",
        "department": "市场部",
        "city": "上海",
        "leave_balance": {
            "年假": 6.0,
            "事假": 10.0,
            "带薪病假": 5.0,
            "调休": 2.5,
            "婚假": 10.0,
            "产假": 98.0,
            "出差": 30.0,
            "市内公出": 10.0,
            "漏打卡补卡": 10.0
        }
    },
    {
        "id": "EMP008",
        "name": "吴十",
        "department": "市场部",
        "city": "上海",
        "leave_balance": {
            "年假": 4.0,
            "事假": 10.0,
            "带薪病假": 5.0,
            "调休": 1.5,
            "婚假": 10.0,
            "产假": 98.0,
            "出差": 30.0,
            "市内公出": 10.0,
            "漏打卡补卡": 10.0
        }
    }
]

# ========== 历史请假记录 ==========
leave_records = [
    {
        "record_id": "LR2026001",
        "employee_id": "EMP001",
        "leave_type": "年假",
        "start_date": "2026-04-10",
        "end_date": "2026-04-10",
        "days": 1.0,
        "reason": "办理个人事务",
        "status": "已批准"
    },
    {
        "record_id": "LR2026002",
        "employee_id": "EMP001",
        "leave_type": "带薪病假",
        "start_date": "2026-03-15",
        "end_date": "2026-03-16",
        "days": 2.0,
        "reason": "感冒发烧",
        "status": "已批准"
    },
    {
        "record_id": "LR2026003",
        "employee_id": "EMP003",
        "leave_type": "年假",
        "start_date": "2026-05-22",
        "end_date": "2026-05-24",
        "days": 3.0,
        "reason": "家庭旅游",
        "status": "已批准"
    },
    {
        "record_id": "LR2026004",
        "employee_id": "EMP006",
        "leave_type": "调休",
        "start_date": "2026-05-20",
        "end_date": "2026-05-20",
        "days": 1.0,
        "reason": "周末加班调休",
        "status": "已批准"
    }
]

# 写入文件
with open(DATA_DIR / "employees.json", "w", encoding="utf-8") as f:
    json.dump(employees, f, ensure_ascii=False, indent=2)

with open(DATA_DIR / "leave_records.json", "w", encoding="utf-8") as f:
    json.dump(leave_records, f, ensure_ascii=False, indent=2)

print("示例数据初始化完成！")
print(f"  - {len(employees)} 名员工")
print(f"  - {len(leave_records)} 条历史请假记录")
