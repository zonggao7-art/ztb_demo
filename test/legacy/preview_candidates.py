"""预览候选表的结构和样例数据"""
import os

import pymysql

DB_CONFIG = {
    "host": "192.168.10.120", "user": "iflytek", "password": os.getenv("MYSQL_PASSWORD", ""),
    "port": 3306, "charset": "utf8mb4", "connect_timeout": 30, "read_timeout": 60,
}

# 最终候选表清单（基于关键词匹配 + 人工判断）
CANDIDATES = [
    # === Category A: 市场信息/产品信息/政策信息/招标信息 ===
    ("xunfei888", "市场产品信息", "A-市场产品信息"),
    ("xunfei888", "政府招标信息", "A-政府招标信息"),
    ("xunfei888", "上海政策信息", "A-政策信息"),
    ("xunfei888", "公司信息", "A-公司信息"),
    ("xunfei_06", "product", "A-产品信息表"),
    ("dhlsw_2", "law_info", "A-政策信息表"),
    ("xunfei20-group1", "市场产品信息", "A-市场产品信息v2"),
    ("xunfei20-group1", "政府招标信息", "A-政府招标信息v2"),

    # === Category B: 政府采购/招投标项目结构化记录 ===
    ("xunfei5", "ods_tender", "B-招标公告(大表)"),
    ("xunfei_06", "tender", "B-招标记录"),
    ("xunfei_05", "t_tender", "B-招标记录v2"),
    ("ifyltek4_2", "bidding_data", "B-投标数据"),
    ("ifyltek4_2", "tenders", "B-招标(tenders)"),
    ("xunfei666", "call_bid_info", "B-招标信息"),
    ("xunfei8_one_group", "bidder_project", "B-投标人项目"),
    ("xunfei_lixunyu", "zhaobiao", "B-招标"),
    ("xunfei1801", "招标采购历史数据", "B-招标历史"),
    ("xunfei07_rag_db", "tender_records", "B-RAG招标记录"),
    ("bidding_data", "bid_project", "B-bid_project(待确认)"),
    ("lin_gang_6_ju_tou_1", "ods_tender", "B-招标(临港)"),
    ("xunfei_team3", "bidding_record", "B-投标记录"),

    # === Category C: 法律公告 ===
    ("xunfei888", "法律法规", "C-法律法规"),
    ("xunfei_06", "laws", "C-法律"),
    ("relissc_rag", "legal_articles", "C-法律条文"),
    ("relissc_rag", "legal_metadata", "C-法律元数据"),
    ("xunfei_lixunyu", "fagui_info", "C-法规信息"),
    ("xunfei5", "ods_policy_regulation_files", "C-政策法规文件"),
    ("relissc_rag", "illegal_behavior_record", "C-违规行为记录"),
]


def preview_table(db_name, table_name, label):
    """预览单张表"""
    print(f"\n{'─'*70}")
    print(f"  [{label}] {db_name}.{table_name}")
    print(f"{'─'*70}")

    conn = pymysql.connect(**DB_CONFIG, database=db_name)
    try:
        with conn.cursor() as cur:
            # 1. 表结构
            cur.execute(f"DESCRIBE `{table_name}`")
            columns = cur.fetchall()
            print(f"  字段 ({len(columns)} 个):")
            for c in columns:
                name, ctype, null, key, default, extra = c
                key_mark = " ★" if key else ""
                print(f"    {name:<35s} {str(ctype):<25s} {key_mark}")

            # 2. 样本数据
            cur.execute(f"SELECT * FROM `{table_name}` LIMIT 5")
            rows = cur.fetchall()
            col_names = [desc[0] for desc in cur.description]

            print(f"\n  样本数据 (前{len(rows)}行):")
            for idx, row in enumerate(rows):
                print(f"  ┌─ Row {idx+1}")
                for col_name, val in zip(col_names, row):
                    val_str = str(val) if val is not None else "NULL"
                    # 截断长文本
                    if len(val_str) > 120:
                        val_str = val_str[:120] + f"...[截断,总长{len(str(val))}]"
                    print(f"  │  {col_name:<35s} = {val_str}")
                print("  └─")
    except Exception as e:
        print(f"  ✗ 查询失败: {e}")
    finally:
        conn.close()


# 先获取 bidding_data 的表列表
print("=" * 70)
print("  先探查 bidding_data 库的表结构")
print("=" * 70)
conn = pymysql.connect(**DB_CONFIG, database="bidding_data")
with conn.cursor() as cur:
    cur.execute("SHOW TABLES")
    bd_tables = [r[0] for r in cur.fetchall()]
    print(f"  bidding_data 包含 {len(bd_tables)} 张表: {bd_tables}")
conn.close()

# 更新候选表
for t in bd_tables:
    if ("bidding_data", t, f"B-{t}") not in CANDIDATES:
        CANDIDATES.append(("bidding_data", t, f"B-{t}"))

# 逐表预览
print(f"\n{'='*70}")
print(f"  批量预览 {len(CANDIDATES)} 张候选表")
print(f"{'='*70}")

for db, tbl, label in CANDIDATES:
    preview_table(db, tbl, label)

print(f"\n{'='*70}")
print(f"  预览完成！共检查 {len(CANDIDATES)} 张表")
print(f"{'='*70}")
