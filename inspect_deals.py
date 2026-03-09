import pandas as pd
excel_file = pd.ExcelFile("/Users/wolfe/Downloads/LeadTracka.xlsx")
print("Sheets:", excel_file.sheet_names)
for sheet in excel_file.sheet_names:
    df = pd.read_excel(excel_file, sheet_name=sheet)
    print(f"--- Sheet: {sheet} ---")
    print("Columns:", df.columns.tolist())
    print("First row:", df.head(1).to_dict('records') if not df.empty else "Empty")

