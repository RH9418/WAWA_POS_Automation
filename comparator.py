import json
import os
import re
from datetime import datetime

# --- STRICT ID EXTRACTION HELPERS ---
def safe_display(val):
    if val is None or str(val).strip() == "": return "--"
    return str(val)

def extract_store(raw_str):
    if not raw_str or raw_str == "NONE": return "NONE"
    match = re.search(r'(\d+)$', str(raw_str).strip())
    return match.group(1) if match else str(raw_str).strip()

def extract_pos(raw_str):
    if not raw_str or raw_str == "NONE": return "NONE"
    match = re.search(r'_(\d+)$', str(raw_str).strip())
    return match.group(1) if match else str(raw_str).strip()

def extract_cashier(raw_str):
    if not raw_str or raw_str == "NONE": return "NONE"
    match = re.search(r'\(\d+_(\d+)\)', str(raw_str).strip())
    if match: return match.group(1)
    match = re.search(r'(\d+)$', str(raw_str).strip())
    return match.group(1) if match else str(raw_str).strip()

def extract_receipt_number(raw_str):
    if not raw_str or raw_str == "NONE": return "NONE"
    match = re.search(r'(\d+)$', str(raw_str).strip())
    return match.group(1) if match else str(raw_str).strip()

def extract_date(raw_str):
    if not raw_str or raw_str == "NONE": return "NONE"
    try:
        dt = datetime.strptime(str(raw_str).strip(), "%m/%d/%y")
        return dt.strftime("%m/%d/%Y")
    except: pass
    try:
        dt = datetime.strptime(str(raw_str).strip(), "%m/%d/%Y")
        return dt.strftime("%m/%d/%Y")
    except: pass
    return str(raw_str).strip()

# --- NORMALIZATION HELPERS ---
def normalize_text(text):
    if text is None: return "NONE"
    cleaned = re.sub(r'\s+', ' ', str(text)).strip().upper()
    if cleaned in ['--', '-', 'UNKNOWN', 'UNKNOWN (UNKNOWN)', 'NUMBER', 'NONE', '']:
        return "NONE"
    return cleaned

def normalize_currency(val):
    if not val or str(val).strip().upper() in ['--', '-', 'UNKNOWN (UNKNOWN)', '', 'NONE', 'NUMBER']: return 0.0
    try: 
        num = float(str(val).replace('$', '').replace(',', '').strip())
        return abs(round(num, 2))
    except ValueError: return 0.0

def normalize_date(date_str):
    date_str = str(date_str).strip()
    try:
        if '-' in date_str: return datetime.strptime(date_str[:10], "%Y-%m-%d").strftime("%m/%d/%Y")
    except: pass
    return date_str

def compare_item_names(xml_name, ui_name):
    x_code = re.search(r'\((\d+)\)$', str(xml_name).strip())
    u_code = re.search(r'\((\d+)\)$', str(ui_name).strip())
    if x_code and u_code:
        try:
            c1, c2 = int(x_code.group(1)), int(u_code.group(1))
            if c1 == c2 and c1 != 0: 
                return True 
        except: pass
    nx, nu = normalize_text(xml_name), normalize_text(ui_name)
    return nx == nu or nx in nu or nu in nx

# --- DYNAMIC FILTERS & MATCHERS ---
def filter_ghost_items(xml_items, ui_items):
    filtered_x = []
    for xl in xml_items:
        if normalize_currency(xl.get('Value')) == 0.0:
            has_match = any(compare_item_names(xl.get('Item Name'), ul.get('Item Name')) for ul in ui_items)
            if not has_match: continue 
        filtered_x.append(xl)
    return filtered_x

def match_lines(x_lines, u_lines):
    pairs = []
    matched_u = set()
    
    # Pass 1: Strict Line Number Match (SKIP DISCOUNTS SO THEY DONT CROSS-WIRE)
    for xl in x_lines:
        x_ln = str(xl.get('Line Number', '')).strip()
        found = False
        
        if '_D' not in x_ln and x_ln and x_ln != "999999":
            for i, ul in enumerate(u_lines):
                if i in matched_u: continue
                u_ln = str(ul.get('Line Number', '')).strip()
                if x_ln == u_ln and '_D' not in u_ln:
                    pairs.append([xl, ul])
                    matched_u.add(i)
                    found = True
                    break
        
        if not found:
            pairs.append([xl, None])
            
    # Pass 2: Fuzzy Match for unpaired XML lines (Including all Discounts)
    for pair in pairs:
        xl = pair[0]
        if pair[1] is None:
            best_match_idx = -1
            best_score = -1
            for i, ul in enumerate(u_lines):
                if i in matched_u: continue
                score = 0
                
                x_parent = str(xl.get('Line Number', '')).split('_')[0]
                u_parent = str(ul.get('Line Number', '')).split('_')[0]
                if x_parent == u_parent:
                    score += 20
                    
                if compare_item_names(xl.get('Item Name'), ul.get('Item Name')): score += 10
                if normalize_currency(xl.get('Value')) == normalize_currency(ul.get('Value')): score += 5
                
                if score > 0 and score > best_score:
                    best_score = score
                    best_match_idx = i
                    
            if best_match_idx != -1:
                pair[1] = u_lines[best_match_idx]
                matched_u.add(best_match_idx)
                
    # Pass 3: Orphan Handling
    for i, ul in enumerate(u_lines):
        if i not in matched_u:
            pairs.append([None, ul])
            
    def sort_key(p):
        ln = str(p[0].get('Line Number')) if p[0] else str(p[1].get('Line Number'))
        if ln.isdigit():
            return float(ln)
        if '_D' in ln:
            parts = ln.split('_D')
            if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                return float(parts[0]) + (float(parts[1]) / 100.0)
        try: return float(ln)
        except: return 999999.0
    
    pairs.sort(key=sort_key)
    return pairs

def match_tenders(x_tenders, u_tenders):
    pairs = []
    matched_u = set()
    
    for xt in x_tenders:
        found = False
        for i, ut in enumerate(u_tenders):
            if i in matched_u: continue
            
            type_match = normalize_text(xt.get("Type")) == normalize_text(ut.get("Type"))
            amt_match = normalize_currency(xt.get("Amount")) == normalize_currency(ut.get("Amount"))
            
            if type_match and amt_match:
                pairs.append([xt, ut])
                matched_u.add(i)
                found = True
                break
                
        if not found:
            pairs.append([xt, None])
            
    for i, ut in enumerate(u_tenders):
        if i not in matched_u:
            pairs.append([None, ut])
            
    return pairs

def run_comparison(input_json_path=None):
    print("\n" + "="*70)
    print("⚖️  PHASE 4: VALIDATION ENGINE STARTED (DATA LAYER ONLY)")
    print("="*70)

    base_dir = os.path.abspath(os.getcwd())
    xml_json_path = os.path.join(base_dir, "xml_batch_results.json")
    ui_json_path = os.path.join(base_dir, "ui_scraped_results.json")
    pdf_download_dir = os.path.join(base_dir, "downloaded_receipts")

    if not os.path.exists(xml_json_path) or not os.path.exists(ui_json_path):
        print("❌ Missing JSON result files. Orchestration failed.")
        return

    with open(xml_json_path, "r") as f: xml_batch = json.load(f)
    with open(ui_json_path, "r") as f: ui_receipts = json.load(f)

    # DYNAMIC SCENARIO MAPPING FIX
    receipt_to_scenario = {}
    target_json_path = input_json_path if input_json_path else os.path.join(base_dir, "target_receipts.json")
    
    if target_json_path and os.path.exists(target_json_path):
        try:
            with open(target_json_path, "r") as f:
                cat_input = json.load(f)
                if isinstance(cat_input, dict):
                    if "receipts" not in cat_input:
                        for scen, r_list in cat_input.items():
                            for r in r_list: receipt_to_scenario[str(r)] = scen
        except Exception as e: 
            print(f"⚠️ Warning: Could not parse scenario mapping from {target_json_path}: {e}")

    xml_receipts = {}
    for filename, receipts in xml_batch.items():
        if "error" in receipts: continue
        for r_num, r_data in receipts.items():
            xml_receipts[str(r_num)] = r_data

    common_receipts = set(xml_receipts.keys()).intersection(set(ui_receipts.keys()))
    if not common_receipts:
        print("❌ No common receipts found to compare. Halting.")
        return

    comparison_results = {}
    perfect_matches = 0
    failed_receipts = 0

    for r_num in common_receipts:
        xml_data, ui_data = xml_receipts[r_num], ui_receipts[r_num]
        
        base_type = xml_data.get('Header', {}).get('Receipt_Type', 'Unknown')
        scenario = receipt_to_scenario.get(r_num, base_type)
        print(f"   [{scenario}] Validating Receipt {r_num}...")
        
        mismatches, recoveries = [], []
        raw_ui_text = str(ui_data.get("Raw_UI_Text", "")).upper()
        
        receipt_result = {
            "Scenario": scenario,
            "Receipt_Number": r_num,
            "PDF_Path": os.path.join(pdf_download_dir, f"Receipt_{r_num}.pdf"),
            "UI_JSON_Path": ui_json_path,
            "XML_JSON_Path": xml_json_path,
            "Mismatches": [],
            "Recoveries": [],
            "Header_Block": {},
            "Tenders_Block": [],
            "Aggregate_Block": {},
            "Line_Items_Block": []
        }

        # -------------------------------------------------------------
        # BLOCK 1: HEADER
        # -------------------------------------------------------------
        header_cols = ["Header Comparison", "Store", "Receipt_Number", "Date", "Cashier", "POS", "Receipt_Type", "Loyalty_Card", "Credit_Card_Number"]
        xml_header, ui_header = xml_data.get('Header', {}), ui_data.get('Header', {})
        
        ui_h_display = [
            "UI", 
            safe_display(ui_header.get('Raw_Store', ui_header.get('Store'))), 
            safe_display(ui_header.get('Raw_Receipt_Number', ui_header.get('Receipt_Number'))), 
            safe_display(ui_header.get('Raw_Date', ui_header.get('Date'))), 
            safe_display(ui_header.get('Raw_Cashier', ui_header.get('Cashier'))), 
            safe_display(ui_header.get('Raw_POS', ui_header.get('POS'))), 
            safe_display(ui_header.get('Receipt_Type')), 
            safe_display(ui_header.get('Loyalty_Card')), 
            safe_display(ui_header.get('Credit_Card_Number'))
        ]
        ui_h_compare = [
            "UI", 
            extract_store(ui_header.get('Raw_Store', ui_header.get('Store'))), 
            extract_receipt_number(ui_header.get('Raw_Receipt_Number', ui_header.get('Receipt_Number'))), 
            extract_date(ui_header.get('Raw_Date', ui_header.get('Date'))), 
            extract_cashier(ui_header.get('Raw_Cashier', ui_header.get('Cashier'))), 
            extract_pos(ui_header.get('Raw_POS', ui_header.get('POS'))), 
            ui_header.get('Receipt_Type'), 
            ui_header.get('Loyalty_Card'), 
            ui_header.get('Credit_Card_Number')
        ]
        xml_h = [
            "XML", 
            safe_display(xml_header.get('Store')), 
            safe_display(xml_header.get('Receipt_Number')), 
            safe_display(xml_header.get('Date')), 
            safe_display(xml_header.get('Cashier')), 
            safe_display(xml_header.get('POS')), 
            safe_display(xml_header.get('Receipt_Type')), 
            safe_display(xml_header.get('Loyalty_Card')), 
            safe_display(xml_header.get('Credit_Card_Number'))
        ]
        
        failed_header_cols = []
        for col, (u_val, x_val) in enumerate(zip(ui_h_compare, xml_h), start=1):
            if col > 1:
                key = header_cols[col-1]
                nx_val, nu_val = normalize_text(x_val), normalize_text(u_val)
                if key == 'Date': nx_val, nu_val = normalize_date(nx_val), normalize_date(nu_val)
                if key == 'Receipt_Type' and nu_val != nx_val: continue

                # SMART ID NORMALIZATION
                if key in ['Store', 'Receipt_Number', 'Cashier', 'POS']:
                    if nx_val.isdigit(): nx_val = str(int(nx_val))
                    if nu_val.isdigit(): nu_val = str(int(nu_val))

                if nx_val != nu_val:
                    mismatches.append(f"Header [{key}] -> XML: '{nx_val}' | UI: '{nu_val}'")
                    failed_header_cols.append(col)
                    
        receipt_result["Header_Block"] = {
            "Headers": header_cols,
            "UI_Row": ui_h_display,
            "XML_Row": xml_h,
            "Failed_Cols": failed_header_cols
        }

        # -------------------------------------------------------------
        # BLOCK 1.5: TENDERS (SPLIT TENDER SUPPORT)
        # -------------------------------------------------------------
        tender_cols = ["Tender Comparison", "Tender Type", "Amount", "Card Number"]
        x_tenders, u_tenders = xml_data.get('Tenders', []), ui_data.get('Tenders', [])
        
        paired_tenders = match_tenders(x_tenders, u_tenders)
        tender_block_list = []
        
        for xt, ut in paired_tenders:
            if xt is None: xt = {}
            if ut is None: ut = {}
            
            ui_t_row = ["UI", safe_display(ut.get('Type')), safe_display(ut.get('Amount')), safe_display(ut.get('Card_Number'))]
            xml_t_row = ["XML", safe_display(xt.get('Type')), safe_display(xt.get('Amount')), safe_display(xt.get('Card_Number'))]
            
            failed_t_cols = []
            for col, (uv, xv) in enumerate(zip(ui_t_row, xml_t_row), start=1):
                if col > 1:
                    field = tender_cols[col-1]
                    match = True
                    
                    if not xt:
                        match = False
                        if col == 2: mismatches.append(f"Extra Tender in UI: {ut.get('Type')} for {ut.get('Amount')}")
                    elif not ut:
                        match = False
                        if col == 2: mismatches.append(f"Missing Tender in UI: {xt.get('Type')} for {xt.get('Amount')}")
                    else:
                        if field == 'Amount':
                            if normalize_currency(uv) != normalize_currency(xv): match = False
                        else:
                            if normalize_text(uv) != normalize_text(xv): match = False
                            
                    if not match:
                        if xt and ut and col == 2: 
                            mismatches.append(f"Tender {xt.get('Type', '?')} [{field}] -> XML: '{xv}' | UI: '{uv}'")
                        failed_t_cols.append(col)
                        
            tender_block_list.append({
                "Headers": tender_cols,
                "UI_Row": ui_t_row,
                "XML_Row": xml_t_row,
                "Failed_Cols": failed_t_cols
            })
            
        receipt_result["Tenders_Block"] = tender_block_list

        # -------------------------------------------------------------
        # BLOCK 2: AGGREGATE
        # -------------------------------------------------------------
        raw_u_lines, raw_x_lines = ui_data.get('Line_Items', []), xml_data.get('Line_Items', [])
        x_lines, u_lines = filter_ghost_items(raw_x_lines, raw_u_lines), filter_ghost_items(raw_u_lines, raw_x_lines)

        agg_cols = ["Aggregate Comparison", "Line Count", "Total Value", "Total Tax"]
        x_tot, u_tot = xml_data.get('Totals', {}), ui_data.get('Totals', {})

        ui_agg = ["UI", len(u_lines), safe_display(u_tot.get('Total_Value')), safe_display(u_tot.get('Total_Tax'))]
        xml_agg = ["XML", len(x_lines), safe_display(x_tot.get('Total_Value')), safe_display(x_tot.get('Total_Tax'))]
        
        failed_agg_cols = []
        for col, (u_val, x_val) in enumerate(zip(ui_agg, xml_agg), start=1):
            if col == 2 and x_val != u_val: failed_agg_cols.append(col)
            if col in [3, 4]:
                if normalize_currency(x_val) != normalize_currency(u_val):
                    val_clean = str(x_val).replace("$", "").replace("-", "")
                    if val_clean in raw_ui_text: 
                        recoveries.append(f"Total/Tax '{val_clean}' recovered from raw UI text.")
                        ui_agg[col-1] = f"[Recovered: {u_val}]"
                    else:
                        mismatches.append(f"Totals -> XML: {x_val} | UI: {u_val}")
                        failed_agg_cols.append(col)

        receipt_result["Aggregate_Block"] = {
            "Headers": agg_cols,
            "UI_Row": ui_agg,
            "XML_Row": xml_agg,
            "Failed_Cols": failed_agg_cols
        }

        # -------------------------------------------------------------
        # BLOCK 3: LINE-BY-LINE DETAIL
        # -------------------------------------------------------------
        line_cols = ["Line By Line Comparison", "Line Number", "Item Name", "Item Price", "Line Type", "Quantity", "Value", "Price Override Indicator", "Line Item Weight", "L Fuel Pump ID"]
        paired_lines = match_lines(x_lines, u_lines)
        
        for xl, ul in paired_lines:
            if xl is None: xl = {}
            if ul is None: ul = {}
            
            ui_row = [
                "UI", safe_display(ul.get('Line Number')), safe_display(ul.get('Item Name')), 
                safe_display(ul.get('Item Price')), safe_display(ul.get('Line Type')), 
                safe_display(ul.get('Quantity')), safe_display(ul.get('Value')), 
                safe_display(ul.get('Price Override Indicator')), safe_display(ul.get('Line Item Weight')), 
                safe_display(ul.get('L Fuel Pump ID'))
            ]
            xml_row = [
                "XML", safe_display(xl.get('Line Number')), safe_display(xl.get('Item Name')), 
                safe_display(xl.get('Item Price')), safe_display(xl.get('Line Type')), 
                safe_display(xl.get('Quantity')), safe_display(xl.get('Value')), 
                safe_display(xl.get('Price Override Indicator')), safe_display(xl.get('Line Item Weight')), 
                safe_display(xl.get('L Fuel Pump ID'))
            ]
            
            # --- DISCOUNT TOLERANCE MODE ---
            is_discount = False
            if '_D' in str(xl.get('Line Number', '')) or '_D' in str(ul.get('Line Number', '')):
                is_discount = True
            if normalize_text(xl.get('Line Type', '')) == 'DISCOUNT':
                is_discount = True

            failed_line_cols = []
            for col, (uv, xv) in enumerate(zip(ui_row, xml_row), start=1):
                if col > 1:
                    field = line_cols[col-1]
                    match = True
                    
                    if not xl:
                        match = False
                        if col == 2: mismatches.append(f"Extra in UI: {ul.get('Item Name')} (UI Line {ul.get('Line Number')})")
                    elif not ul:
                        match = False
                        if col == 2: mismatches.append(f"Missing in UI: {xl.get('Item Name')} (XML Line {xl.get('Line Number')})")
                    else:
                        # 1. Discount Parent Matching
                        if field == 'Line Number' and is_discount:
                            nx_parent = str(xv).split('_')[0]
                            nu_parent = str(uv).split('_')[0]
                            if nx_parent == nu_parent: match = True
                            else: match = False

                        # 2. Ignore hidden UI values for discounts
                        elif field in ['Item Price', 'Line Type', 'Quantity', 'Price Override Indicator', 'Line Item Weight', 'L Fuel Pump ID'] and is_discount:
                            if normalize_text(uv) in ["NONE", "--"]:
                                match = True 
                            else:
                                if normalize_currency(uv) != normalize_currency(xv): match = False
                                
                        # 3. Standard Field Matching
                        elif field in ['Quantity', 'Value']:
                            if normalize_currency(uv) != normalize_currency(xv): match = False
                        elif field == 'Item Price':
                            if normalize_currency(uv) != normalize_currency(xv):
                                if normalize_currency(ul.get('Value')) == normalize_currency(xl.get('Value')): match = True
                                else: match = False
                        elif field == 'Line Item Weight':
                            if normalize_currency(uv) != normalize_currency(xv):
                                is_fuel = str(xl.get('L Fuel Pump ID', '--')) != '--'
                                ui_qty = normalize_currency(ul.get('Quantity'))
                                ui_wt = normalize_currency(uv)
                                xml_wt = normalize_currency(xv)
                                if not is_fuel and (ui_wt == ui_qty or xml_wt == 1.0): match = True
                                else: match = False
                        elif field == 'Item Name':
                            if not compare_item_names(xv, uv): match = False
                        elif field == 'L Fuel Pump ID':
                            nx_val, nu_val = normalize_text(xv), normalize_text(uv)
                            if nx_val.isdigit() and nu_val.isdigit():
                                if int(nx_val) != int(nu_val): match = False
                            elif nx_val != nu_val: match = False
                        else:
                            if normalize_text(uv) != normalize_text(xv): match = False
                            
                    if not match:
                        if xl and ul and col == 2: mismatches.append(f"Row {xl.get('Line Number', '?')} [{field}] -> XML: '{xv}' | UI: '{uv}'")
                        failed_line_cols.append(col)
                        
            receipt_result["Line_Items_Block"].append({
                "Headers": line_cols,
                "UI_Row": ui_row,
                "XML_Row": xml_row,
                "Failed_Cols": failed_line_cols
            })

        receipt_result["Mismatches"] = list(set(mismatches))
        receipt_result["Recoveries"] = recoveries
        
        if not mismatches:
            perfect_matches += 1
            print(f"      ✅ MATCHED PERFECTLY")
        else:
            failed_receipts += 1
            print(f"      ❌ FAILED with {len(set(mismatches))} discrepancies.")
            
        comparison_results[r_num] = receipt_result

    with open("comparison_results.json", "w") as f:
        json.dump(comparison_results, f, indent=4)
        
    print("\n" + "="*70)
    print("🏁 DATA VALIDATION SUMMARY")
    print("="*70)
    print(f"✅ Perfect Matches    : {perfect_matches}")
    print(f"❌ Data Mismatches    : {failed_receipts}")
    print(f"💾 Results saved to 'comparison_results.json'. Proceeding to Excel Generator.")

if __name__ == "__main__":
    run_comparison()
