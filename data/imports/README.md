## Contact Import Folder

Drop your scraped CSV files here.

The DSE Toolkit dashboard will detect them and let you import contacts
into the main database.

### Supported Formats

Any CSV with columns like:
- `First Name`, `Last Name`, `Company`
- `Title`, `Email`, `Phone`
- `Region`, `Industry`, `Source`

Files from the CRM Scraper are automatically recognised.

### How It Works

1. Run the scraper — output CSV lands here (or copy it here)
2. Open the dashboard → **Contacts** page
3. Click **Import from Folder** — all CSVs in this folder are listed
4. Select which file(s) to import
5. Contacts are parsed and added to the database
6. Imported files are moved to `data/imports/processed/`
