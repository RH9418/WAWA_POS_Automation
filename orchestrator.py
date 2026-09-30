import os
import glob
import asyncio
import tkinter as tk
import json
import xml.etree.ElementTree as ET
from tkinter import filedialog
from playwright.async_api import async_playwright

# --- Modular Imports ---
try:
    from xml_parser import extract_receipt_filters 
    from xml_pipeline import process_receipt
    from batch_xml_extractor import run_targeted_extraction
    from comparator import run_comparison
    from excel_generator import generate_excel_report
except ImportError as e:
    print(f"⚠️ Warning: Could not import a pipeline module. {e}")

# =========================================================================
# PHASE 0: HIGH-SPEED XML INDEXER
# =========================================================================
def build_xml_index(xml_dir, target_receipts):
    """
    Uses memory-efficient iterparse to scan the XML library.
    Returns a dictionary mapping: {"ReceiptNumber": "path/to/file.xml"}
    """
    target_mapping = {}
    targets_left = set([str(r).strip() for r in target_receipts])
    
    xml_files = glob.glob(os.path.join(xml_dir, "*.xml")) + glob.glob(os.path.join(xml_dir, "*.XML"))
    print(f"\n🔍 Scanning {len(xml_files)} XML files for {len(targets_left)} target receipts...")
    
    for file in xml_files:
        if not targets_left: 
            break # Stop scanning if we found all targets
            
        try:
            # iterparse reads line-by-line, preventing RAM overflow on massive files
            for event, elem in ET.iterparse(file, events=('end',)):
                tag = elem.tag.split('}')[-1]
                if tag in ('TransactionID', 'DropNumber'):
                    r_num = elem.text
                    if r_num and str(r_num).strip() in targets_left:
                        clean_num = str(r_num).strip()
                        target_mapping[clean_num] = file
                        targets_left.remove(clean_num)
                elem.clear() # Free memory immediately
        except Exception as e:
            print(f"⚠️ Error indexing {file}: {e}")
            
    return target_mapping


async def run_orchestrator(input_json_path, xml_dir_path):
    print("\n" + "="*70)
    print("🚀 ENTERPRISE RECEIPT AUTOMATION SUITE STARTED (PULL MODEL)")
    print("="*70)
    
    # 0. Load Target JSON & Handle Categorized Scenarios
    try:
        with open(input_json_path, 'r') as f:
            data = json.load(f)
            
        # Flatten the dictionary if it's categorized by Scenario Type
        if isinstance(data, dict):
            # If the old format {"receipts": [...]} is used
            if "receipts" in data and isinstance(data["receipts"], list):
                target_list = data["receipts"]
            else:
                # Flatten the dynamic categories (e.g. {"Sale": ["123"], "Return": ["456"]})
                target_list = []
                for scenario, receipts in data.items():
                    target_list.extend(receipts)
        else:
            target_list = data
            
    except Exception as e:
        print(f"❌ Failed to load input JSON: {e}")
        return

    if not target_list:
        print("❌ Target receipt list is empty!")
        return

    # 1. Build the Index Mapping
    target_mapping = build_xml_index(xml_dir_path, target_list)
    
    missing = set(target_list) - set(target_mapping.keys())
    if missing:
        print(f"⚠️ Could not locate {len(missing)} receipts in the XML library: {missing}")
        
    if not target_mapping:
        print("❌ No target receipts were found in the provided XML directory. Halting.")
        return
        
    print(f"✅ Successfully mapped {len(target_mapping)} receipts to their source XMLs.")

    # =========================================================================
    # PHASE 1: DATA EXTRACTION (Parse Headers)
    # =========================================================================
    print("\n[PHASE 1] Extracting Receipt Search Headers from XML...")
    payloads = []
    
    for receipt_num, file_path in target_mapping.items():
        try:
            # Calling the updated Miner with specific receipt number
            data = extract_receipt_filters(file_path, receipt_num) 
            if data:
                payloads.append(data)
        except Exception as e:
            print(f"❌ Failed to parse headers for {receipt_num} in {file_path}: {e}")
            
    if not payloads:
        print("❌ No valid headers extracted. Halting.")
        return

    print(f"✅ Successfully queued {len(payloads)} receipts for UI extraction.")

    # =========================================================================
    # PHASE 2: UI AUTOMATION & SCRAPING
    # =========================================================================
    print("\n[PHASE 2] Launching Browser for UI Scraping & PDF Export...")
    all_ui_scraped_data = {}
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, args=['--ignore-certificate-errors'])
        context = await browser.new_context(ignore_https_errors=True, accept_downloads=True)
        page = await context.new_page()

        # FIXED TIMEOUT ISSUE:
        # Sets timeout=0 (infinite) and only waits for the DOM to load, skipping heavy background scripts
        try:
            await page.goto("https://wawa-d.wai.zebra.com/idp", timeout=0, wait_until="domcontentloaded")
        except Exception as e:
            print(f"⚠️ Navigation warning (ignored): {e}")
        
        # Interactive shell wait
        await asyncio.to_thread(input, "🛑 Log in manually. Once on the Insights Dashboard, press ENTER in this terminal...\n> ")

        is_first = True
        for index, payload in enumerate(payloads):
            receipt_num = payload.get('Receipt_Number', 'UNKNOWN')
            try:
                success, scraped_data = await process_receipt(page, payload, is_first_run=is_first)
                is_first = False 
                if success and scraped_data:
                    all_ui_scraped_data[receipt_num] = scraped_data
            except Exception as e:
                print(f"❌ PIPELINE ERROR on Receipt {receipt_num}: {e}")
                is_first = False 

        output_file = "ui_scraped_results.json"
        with open(output_file, "w") as f:
            json.dump(all_ui_scraped_data, f, indent=4)
            
        print(f"✅ UI Automation complete. Data saved to '{output_file}'.")
        await browser.close()

    # =========================================================================
    # PHASE 3: TARGETED XML DATABASE EXTRACTION
    # =========================================================================
    # Passing the exact mapping so the extractor knows exactly where to look
    await run_targeted_extraction(target_mapping)

    # =========================================================================
    # PHASE 4: RECONCILIATION & REPORTING
    # =========================================================================
    # Passes the dynamically selected JSON file so descriptive scenario names are maintained!
    run_comparison(input_json_path)

    # =========================================================================
    # PHASE 5: EXCEL REPORT GENERATION
    # =========================================================================
    # Converts comparison_results.json into a beautifully formatted Excel sheet
    generate_excel_report()


# --- UI LAUNCHER ---
def launch_ui():
    """Updated UI to support the new Pull Model inputs."""
    root = tk.Tk()
    root.title("Receipt Automation Suite (Pull Model)")
    root.geometry("450x250")
    try: root.eval('tk::PlaceWindow . center') 
    except: pass

    inputs = {"json": None, "dir": None}

    def select_json():
        path = filedialog.askopenfilename(title="Select Target Receipts JSON", filetypes=[("JSON Files", "*.json")])
        if path:
            inputs["json"] = path
            lbl_json.config(text=f"JSON: {os.path.basename(path)}")
            check_ready()

    def select_dir():
        path = filedialog.askdirectory(title="Select XML Library Folder")
        if path:
            inputs["dir"] = path
            lbl_dir.config(text=f"DIR: {os.path.basename(path)}")
            check_ready()
            
    def check_ready():
        if inputs["json"] and inputs["dir"]:
            btn_start.config(state="normal")
            
    def start_suite():
        root.destroy()

    tk.Label(root, text="Select Inputs", font=("Arial", 14, "bold")).pack(pady=10)
    
    tk.Button(root, text="📄 1. Select Target JSON", command=select_json, width=25).pack(pady=5)
    lbl_json = tk.Label(root, text="JSON: Not Selected", fg="gray")
    lbl_json.pack()
    
    tk.Button(root, text="📁 2. Select XML Library", command=select_dir, width=25).pack(pady=5)
    lbl_dir = tk.Label(root, text="DIR: Not Selected", fg="gray")
    lbl_dir.pack()

    btn_start = tk.Button(root, text="🚀 Run Pipeline", command=start_suite, width=25, bg="lightgreen", state="disabled")
    btn_start.pack(pady=15)

    root.mainloop()
    return inputs["json"], inputs["dir"]

if __name__ == '__main__':
    json_path, xml_dir = launch_ui()
    if json_path and xml_dir:
        asyncio.run(run_orchestrator(json_path, xml_dir))
    else:
        print("❌ Operation cancelled. Missing inputs.")
