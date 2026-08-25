import json

with open("test/price_dbs_schema.json", "r", encoding="utf-8") as f:
    data = json.load(f)

for db_obj in data:
    if "tables" not in db_obj:
        continue
    print(f"DB: {db_obj['database']}")
    for t in db_obj["tables"]:
        print(f"  {t['name']:<40} rows={t['rows']:>10} cols={len(t['columns'])}")
    print()
