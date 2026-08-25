"""步骤1: CSV数据质量预检"""
import csv, os

csv_dir = r'd:\DEMO\zhaotoubiao_demo\raw_tables'
files = ['company_info.csv', 'company_penalty.csv', 'product_info.csv', 'bid_project.csv']

for fname in files:
    path = os.path.join(csv_dir, fname)
    size_mb = os.path.getsize(path) / (1024*1024)
    print(f'\n===== {fname} ({size_mb:.2f} MB) =====')

    # Check encoding
    with open(path, 'rb') as f:
        raw = f.read(200)
    print(f'BOM: {raw[:3].hex() if len(raw)>=3 else "N/A"} (EFBBBF=utf-8-BOM)')

    with open(path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        headers = reader.fieldnames
        print(f'Columns ({len(headers)}): {headers}')

        null_counts = {h: 0 for h in headers}
        total = 0
        for row in reader:
            total += 1
            for h in headers:
                v = row.get(h, '')
                if v is None or v.strip() == '' or v.strip() in ('N/A', '-', 'NULL', 'null'):
                    null_counts[h] += 1

        print(f'Total rows: {total}')
        print('Top null/empty fields:')
        for h, cnt in sorted(null_counts.items(), key=lambda x: -x[1])[:5]:
            rate = cnt/total*100 if total else 0
            print(f'  {h}: {cnt}/{total} ({rate:.1f}%)')

    # Check registered_capital samples
    print('registered_capital samples (first 10 non-empty):')
    with open(path, 'r', encoding='utf-8') as f:
        reader2 = csv.DictReader(f)
        count = 0
        for row in reader2:
            rc = row.get('registered_capital', '')
            if rc and rc.strip():
                print(f'  [{rc.strip()}]')
                count += 1
                if count >= 10:
                    break

print('\n===== CSV quality check complete =====')
