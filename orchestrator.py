import os
import sys
import glob
import asyncio
import tkinter as tk
from tkinter import filedialog, ttk
import json
import xml.etree.ElementTree as ET
from playwright.async_api import async_playwright

try:
    from xml_parser import extract_receipt_filters 
    from xml_pipeline import process_receipt
    from batch_xml_extractor import run_targeted_extraction
    from comparator import run_comparison
    from evidence_generator import process_screenshots # NEW IMPORT
    from excel_generator import generate_excel_report
except ImportError as e:
    print(f"❌ Fatal Error: Could not import a core pipeline module. {e}")
    sys.exit(1)

def build_xml_index(xml_dir, target_receipts):
    target_mapping = {}
    targets_left = set([str(r).strip() for r in target_receipts])
    
    xml_files = glob.glob(os.path.join(xml_dir, "*.xml")) + glob.glob(os.path.join(xml_dir, "*.XML"))
    print(f"\n🔍 Scanning {len(xml_files)} XML files for {len(targets_left)} target receipts...")
    
    for file in xml_files:
        if not targets_left: 
            break 
            
        try:
            context = ET.iterparse(file, events=('start', 'end'))
            context_iter = iter(context)
            event, root = next(context_iter)
            
            for event, elem in context_iter:
                if event == 'end':
                    tag = elem.tag.split('}')[-1]
                    if tag in ('TransactionID', 'DropNumber'):
                        r_num = elem.text
                        if r_num and str(r_num).strip() in targets_left:
                            clean_num = str(r_num).strip()
                            target_mapping[clean_num] = file
                            targets_left.remove(clean_num)
                            if not targets_left: break
                                
                    elem.clear()
                    root.clear() 
        except StopIteration:
            pass 
        except Exception as e:
            print(f"⚠️ Error indexing {file}: {e}")
            
    return target_mapping


async def run_orchestrator(input_json_path, xml_dir_path, start_phase=1):
    print("\n" + "="*70)
    print(f"🚀 ENTERPRISE RECEIPT AUTOMATION SUITE STARTED (Starting at Phase {start_phase})")
    print("="*70)
    
    try:
        with open(input_json_path, 'r') as f:
            data = json.load(f)
            
        if isinstance(data, dict):
            if "receipts" in data and isinstance(data["receipts"], list):
                target_list = data["receipts"]
            else:
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

    target_mapping = {}
    if start_phase <= 3:
        target_mapping = build_xml_index(xml_dir_path, target_list)
        
        missing = set(target_list) - set(target_mapping.keys())
        if missing:
            print(f"⚠️ Could not locate {len(missing)} receipts in the XML library: {missing}")
            
        if not target_mapping:
            print("❌ No target receipts were found in the provided XML directory. Halting.")
            return
            
        print(f"✅ Successfully mapped {len(target_mapping)} receipts to their source XMLs.")

    # =========================================================================
    # PHASE 1: DATA EXTRACTION
    # =========================================================================
    if start_phase <= 1:
        print("\n[PHASE 1] Extracting Receipt Search Headers from XML...")
        payloads = []
        
        for receipt_num, file_path in target_mapping.items():
            try:
                data = extract_receipt_filters(file_path, receipt_num) 
                if data:
                    payloads.append(data)
            except Exception as e:
                print(f"❌ Failed to parse headers for {receipt_num} in {file_path}: {e}")
                
        if not payloads:
            print("❌ No valid headers extracted. Halting.")
            return
        print(f"✅ Successfully queued {len(payloads)} receipts for UI extraction.")
    else:
        print("\n⏭️ Skipping Phase 1 (Header Extraction)...")

    # =========================================================================
    # PHASE 2: UI AUTOMATION & SCRAPING
    # =========================================================================
    if start_phase <= 2:
        print("\n[PHASE 2] Launching Browser for UI Scraping & PDF Export...")
        all_ui_scraped_data = {}
        
        if start_phase == 2:
            print("⚠️ Warning: Starting directly at Phase 2 requires extracting headers first.")
            payloads = []
            for receipt_num, file_path in target_mapping.items():
                data = extract_receipt_filters(file_path, receipt_num) 
                if data: payloads.append(data)
        
        if not payloads:
            print("❌ No payloads available for UI Scraping. Halting.")
            return

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=False, args=['--ignore-certificate-errors'])
            context = await browser.new_context(ignore_https_errors=True, accept_downloads=True)
            page = await context.new_page()

            try:
                await page.goto("https://wawa-d.wai.zebra.com/idp", timeout=0, wait_until="domcontentloaded")
            except Exception as e:
                print(f"⚠️ Navigation warning (ignored): {e}")
            
            await asyncio.to_thread(input, "🛑 Log in manually. Once on the Insights Dashboard, press ENTER in this terminal...\n> ")

            is_first = True
            for index, payload in enumerate(payloads):
                receipt_num = payload.get('Receipt_Number', 'UNKNOWN')
                try:
                    success, scraped_data = await process_receipt(page, payload, is_first_run=is_first)
                    if success:
                        is_first = False 
                    if success and scraped_data:
                        all_ui_scraped_data[receipt_num] = scraped_data
                except Exception as e:
                    print(f"❌ PIPELINE ERROR on Receipt {receipt_num}: {e}")

            output_file = "ui_scraped_results.json"
            with open(output_file, "w") as f:
                json.dump(all_ui_scraped_data, f, indent=4)
                
            print(f"✅ UI Automation complete. Data saved to '{output_file}'.")
            await browser.close()
    else:
        print("\n⏭️ Skipping Phase 2 (UI Scraping)...")

    # =========================================================================
    # PHASE 3: TARGETED XML DATABASE EXTRACTION
    # =========================================================================
    if start_phase <= 3:
        await run_targeted_extraction(target_mapping)
    else:
        print("\n⏭️ Skipping Phase 3 (XML Database Extraction)...")

    # =========================================================================
    # PHASE 4: RECONCILIATION & REPORTING
    # =========================================================================
    if start_phase <= 4:
        run_comparison(input_json_path)
    else:
        print("\n⏭️ Skipping Phase 4 (Reconciliation Comparator)...")
        
    # =========================================================================
    # PHASE 4.5: XML VISUAL EVIDENCE GENERATION
    # =========================================================================
    if start_phase <= 4:
        print("\n📸 Generating XML Visual Evidence with Failure Highlights...")
        try:
            await process_screenshots()
        except Exception as e:
            print(f"⚠️ Failed to generate XML screenshots: {e}")
    else:
        print("\n⏭️ Skipping Phase 4.5 (XML Visual Evidence)...")

    # =========================================================================
    # PHASE 5: EXCEL REPORT GENERATION
    # =========================================================================
    if start_phase <= 5:
        generate_excel_report()


def launch_ui():
    root = tk.Tk()
    root.title("Receipt Automation Suite")
    root.geometry("480x320")
    try: root.eval('tk::PlaceWindow . center') 
    except: pass

    inputs = {"json": None, "dir": None, "phase": 1}

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
        inputs["phase"] = int(phase_var.get())
        root.destroy()

    tk.Label(root, text="Suite Configuration", font=("Arial", 14, "bold")).pack(pady=10)
    
    frame_phase = tk.Frame(root)
    frame_phase.pack(pady=5)
    tk.Label(frame_phase, text="Start From: ").pack(side=tk.LEFT)
    phase_var = tk.StringVar(value="1")
    combo = ttk.Combobox(frame_phase, textvariable=phase_var, values=["1", "2", "3", "4", "5"], width=5, state="readonly")
    combo.pack(side=tk.LEFT)
    tk.Label(frame_phase, text=" (1=Full, 3=XML Only, 4=Compare Only)").pack(side=tk.LEFT)

    tk.Button(root, text="📄 1. Select Target JSON", command=select_json, width=25).pack(pady=5)
    lbl_json = tk.Label(root, text="JSON: Not Selected", fg="gray")
    lbl_json.pack()
    
    tk.Button(root, text="📁 2. Select XML Library", command=select_dir, width=25).pack(pady=5)
    lbl_dir = tk.Label(root, text="DIR: Not Selected", fg="gray")
    lbl_dir.pack()

    btn_start = tk.Button(root, text="🚀 Run Pipeline", command=start_suite, width=25, bg="lightgreen", state="disabled")
    btn_start.pack(pady=15)

    root.mainloop()
    return inputs["json"], inputs["dir"], inputs["phase"]

if __name__ == '__main__':
    json_path, xml_dir, start_phase = launch_ui()
    if json_path and xml_dir:
        asyncio.run(run_orchestrator(json_path, xml_dir, start_phase))
    else:
        print("❌ Operation cancelled. Missing inputs.")
