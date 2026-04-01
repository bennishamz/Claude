"""Extract filtered rows from the Excel file into input.csv."""
import csv
import openpyxl

EXCEL_PATH = "/Users/celinecheminal/Downloads/akeneo_accounts 3 - akeneo_accounts 3_enriched.xlsx"
OUTPUT_PATH = "/Users/celinecheminal/presti-qualifier/input.csv"

wb = openpyxl.load_workbook(EXCEL_PATH, read_only=True)
ws = wb.active

rows_written = 0
with open(OUTPUT_PATH, "w", newline="", encoding="utf-8") as f:
    writer = csv.writer(f)
    writer.writerow(["company", "website", "revenue_status", "reported_revenue", "employee_count"])
    for i, row in enumerate(ws.iter_rows(values_only=True)):
        if i == 0:
            continue
        status = str(row[4]).strip().lower() if row[4] else ""
        if status in ("confirmed", "employee_proxy"):
            writer.writerow([
                row[0],  # account_name
                row[1],  # website_url
                row[4],  # revenue_status
                row[5],  # reported_revenue
                row[6],  # employee_count
            ])
            rows_written += 1

wb.close()
print(f"Wrote {rows_written} rows to {OUTPUT_PATH}")
