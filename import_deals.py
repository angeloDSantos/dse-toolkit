import pandas as pd
import math
from core.database import get_db, add_contact, init_db

def parse_leadtracka(filepath):
    print(f"Parsing {filepath}...")
    xls = pd.ExcelFile(filepath)
    
    # We will look for sheets that seem to have contact data
    # (By skipping sheets like 'Lead Tracka', 'Sheet1', 'Other Summits')
    skip_sheets = ["Lead Tracka", "Sheet1"]
    
    init_db()
    db = get_db()
    total_imported = 0

    for sheet_name in xls.sheet_names:
        if sheet_name in skip_sheets:
            continue
            
        print(f"\nScanning Sheet: {sheet_name}")
        df = pd.read_excel(xls, sheet_name=sheet_name)
        
        # Verify required columns exist
        required_cols = {"Name", "Title", "Company", "Email", "Summit", "Current Stage"}
        if not required_cols.issubset(set(df.columns)):
            print(f"  -> Skipping (missing required columns)")
            continue
            
        # Iterate over contacts
        for idx, row in df.iterrows():
            name = row.get("Name", "")
            if pd.isna(name) or str(name).strip() == "":
                continue
                
            name = str(name).strip()
            parts = name.split(" ", 1)
            first_name = parts[0]
            last_name = parts[1] if len(parts) > 1 else ""
            
            title = str(row.get("Title", ""))
            company = str(row.get("Company", ""))
            
            email = row.get("Email", "")
            if pd.isna(email): email = ""
            email = str(email).strip()
            
            summit = row.get("Summit", "")
            if pd.isna(summit): summit = sheet_name
            
            stage = row.get("Current Stage", "Awaiting FP")
            if pd.isna(stage): stage = "Awaiting FP"
            
            grading = row.get("Grading", "")
            if pd.isna(grading): grading = ""
            
            future_call = row.get("Future Call Date", "")
            if pd.isna(future_call): future_call = ""
            
            reason = row.get("Reason", "")
            if pd.isna(reason): reason = ""

            # Check if contact exists
            existing = None
            if email:
                existing = db.execute("SELECT id FROM contacts WHERE email = ?", (email,)).fetchone()
            
            if not existing:
                existing = db.execute("SELECT id FROM contacts WHERE first_name = ? AND last_name = ? AND company = ?", 
                           (first_name, last_name, company)).fetchone()
                           
            if existing:
                contact_id = existing[0]
            else:
                try:
                    contact_id = add_contact(
                        first_name=first_name,
                        last_name=last_name,
                        company=company,
                        title=title if title != "nan" else "",
                        email=email if email != "nan" else "",
                        source="LeadTracka Import"
                    )
                except Exception as e:
                    print(f"Error creating contact {first_name} {last_name}: {e}")
                    continue
            
            # Upsert Deal
            d_existing = db.execute("SELECT id FROM deals WHERE contact_id = ? AND summit = ?", (contact_id, str(summit))).fetchone()
            if d_existing:
                db.execute(
                    "UPDATE deals SET stage=?, grading=?, future_call_date=?, reason=?, updated_at=datetime('now') WHERE id=?", 
                    (str(stage), str(grading), str(future_call), str(reason), d_existing[0])
                )
            else:
                db.execute(
                    "INSERT INTO deals (contact_id, summit, stage, grading, future_call_date, reason) VALUES (?, ?, ?, ?, ?, ?)",
                    (contact_id, str(summit), str(stage), str(grading), str(future_call), str(reason))
                )
            
            total_imported += 1

    db.commit()
    print(f"\nImport Details: Successfully imported/updated {total_imported} Deals!")

if __name__ == "__main__":
    import os
    target = os.path.expanduser("~/Downloads/LeadTracka.xlsx")
    parse_leadtracka(target)
