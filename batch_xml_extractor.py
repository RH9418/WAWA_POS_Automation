import xml.etree.ElementTree as ET
import json
import os
import glob
import tkinter as tk
from tkinter import filedialog
from datetime import datetime
import asyncio
from playwright.async_api import async_playwright

BASE_OUTPUT_DIR = "xml_visual_evidence"

# --- FIX: Word wrap added, and highlighting engine dynamically accepts failure data ---
HTML_TEMPLATE = r"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <style>
        body { 
            background-color: #1e1e1e; 
            color: #d4d4d4; 
            font-family: 'Consolas', 'Courier New', monospace; 
            padding: 20px; 
            margin: 0;
            display: inline-block;
        }
        .container {
            border: 1px solid #3c3c3c;
            border-radius: 6px;
            overflow: hidden;
            box-shadow: 0 4px 12px rgba(0,0,0,0.4);
            background-color: #1e1e1e;
        }
        .header {
            background-color: #252526;
            color: #cccccc;
            padding: 8px 15px;
            font-size: 14px;
            font-weight: bold;
            border-bottom: 1px solid #3c3c3c;
            display: flex;
            justify-content: space-between;
        }
        pre { 
            margin: 0; 
            padding: 15px;
            font-size: 15px; 
            line-height: 1.6; 
            white-space: pre-wrap; /* Fix for long item names getting cropped */
            word-wrap: break-word;
        }
        /* VS Code Dark+ Colors */
        .json-key { color: #9cdcfe; }
        .json-string { color: #ce9178; }
        .json-number { color: #b5cea8; }
        .json-boolean { color: #569cd6; }
        .json-null { color: #569cd6; font-style: italic; }
        
        /* Failure Highlighting Styles */
        .highlight-line {
            background-color: rgba(255, 80, 80, 0.2); 
            display: block; 
            margin-left: -15px; 
            margin-right: -15px;
            padding-left: 11px; 
            padding-right: 15px;
            border-left: 4px solid #ff5050;
        }
        .highlight-key {
            color: #ff5050 !important; 
            font-weight: bold;
        }
    </style>
</head>
<body>
    <div class="container" id="capture-box">
        <div class="header">
            <span>💾 XML PARSED JSON</span>
            <span id="label">{label}</span>
        </div>
        <pre id="json-content">{raw_json}</pre>
    </div>

    <script>
        function syntaxHighlight(json) {
            json = json.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
            return json.replace(/("(\\u[a-zA-Z0-9]{4}|\\[^u]|[^\\"])*"(\s*:)?|\b(true|false|null)\b|-?\d+(?:\.\d*)?(?:[eE][+\-]?\d+)?)/g, function (match) {
                var cls = 'json-number';
                if (/^"/.test(match)) {
                    if (/:$/.test(match)) {
                        return '<span class="json-key">' + match.slice(0, -1) + '</span>:';
                    } else {
                        cls = 'json-string';
                    }
                } else if (/true|false/.test(match)) {
                    cls = 'json-boolean';
                } else if (/null/.test(match)) {
                    cls = 'json-null';
                }
                return '<span class="' + cls + '">' + match + '</span>';
            });
        }

        function applyHighlights(html, failData) {
            if (!failData) return html;
            let lines = html.split('\n');
            let currentLineItem = null;

            for (let i = 0; i < lines.length; i++) {
                // Track which item we are currently evaluating in the JSON array
                if (lines[i].includes('"Line Number"')) {
                    let match = lines[i].match(/"Line Number"<\/span>:\s*"([^"]+)"/);
                    if (match) currentLineItem = match[1];
                }
                
                let activeFailures = [];
                if (failData.global) activeFailures.push(...failData.global);
                if (failData.lineItems && currentLineItem && failData.lineItems[currentLineItem]) {
                    activeFailures.push(...failData.lineItems[currentLineItem]);
                }

                for (let field of activeFailures) {
                    let searchStr = '<span class="json-key">"' + field + '"</span>:';
                    if (lines[i].includes(searchStr)) {
                        lines[i] = '<span class="highlight-line">' + 
                                   lines[i].replace(searchStr, '<span class="highlight-key">"' + field + '"</span>:') + 
                                   '</span>';
                    }
                }
            }
            return lines.join('\n');
        }

        const contentDiv = document.getElementById('json-content');
        let highlighted = syntaxHighlight(contentDiv.textContent);
        const failData = {fail_data_json};
        contentDiv.innerHTML = applyHighlights(highlighted, failData);
    </script>
</body>
</html>
"""

async def snap_json_screenshot(page, data_dict, label, output_dir, filename, fail_data=None):
    if fail_data is None: fail_data = {}
    raw_json = json.dumps(data_dict, indent=4)
    safe_json = raw_json.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    fail_data_json = json.dumps(fail_data)
    
    html_content = HTML_TEMPLATE.replace("{raw_json}", safe_json).replace("{label}", label).replace("{fail_data_json}", fail_data_json)
    await page.set_content(html_content)
    
    box_locator = page.locator("#capture-box")
    await box_locator.wait_for(state="visible")
    
    save_path = os.path.join(output_dir, filename)
    await box_locator.screenshot(path=save_path)
    return filename

async def process_screenshots(batch_data):
    """
    Called strictly in Phase 4.5. Maps comparator failures precisely to JSON payload fields
    and creates Red bounding boxes inside the images.
    """
    os.makedirs(BASE_OUTPUT_DIR, exist_ok=True)
    run_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    comp_results = {}
    try:
        if os.path.exists("comparison_results.json"):
            with open("comparison_results.json", "r") as f:
                comp_results = json.load(f)
    except:
        pass

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1200, "height": 1080})
        page = await context.new_page()

        for filename_key, receipts in batch_data.items():
            if "error" in receipts: continue
            
            print(f"\n📂 Processing Screenshots for File Group: {filename_key}")
            for receipt_num, receipt_data in receipts.items():
                print(f"   📸 Generating Evidence for Receipt: {receipt_num}")
                
                receipt_dir = os.path.join(BASE_OUTPUT_DIR, f"Receipt_{receipt_num}_{run_timestamp}")
                os.makedirs(receipt_dir, exist_ok=True)
                
                mapping_data = {
                    "Receipt_Number": receipt_num,
                    "Header_Image": "Header.png",
                    "Footer_Image": "Footer.png",
                    "Line_Items_Mapping": {}
                }
                
                r_comp = comp_results.get(receipt_num, {})
                
                # --- 1. Map Header Failures ---
                header_fails = []
                hb = r_comp.get("Header_Block", {})
                if hb:
                    for c in hb.get("Failed_Cols", []):
                        if c > 1: header_fails.append(hb["Headers"][c-1])
                        
                await snap_json_screenshot(
                    page, {"Header": receipt_data.get("Header", {})}, f"Receipt #{receipt_num} | Header",
                    receipt_dir, "Header.png", fail_data={"global": header_fails}
                )

                # --- 2. Map Footer Failures ---
                footer_fails = []
                footer_key_map = {"Total Value": "Total_Value", "Total Tax": "Total_Tax", "Amount": "Amount", "Tender Type": "Type", "Card Number": "Card_Number"}
                
                ab = r_comp.get("Aggregate_Block", {})
                if ab:
                    for c in ab.get("Failed_Cols", []):
                        if c > 1: footer_fails.append(footer_key_map.get(ab["Headers"][c-1], ab["Headers"][c-1]))

                for tb in r_comp.get("Tenders_Block", []):
                    for c in tb.get("Failed_Cols", []):
                        if c > 1: footer_fails.append(footer_key_map.get(tb["Headers"][c-1], tb["Headers"][c-1]))

                await snap_json_screenshot(
                    page, {"Tenders": receipt_data.get("Tenders", []), "Totals": receipt_data.get("Totals", {})}, 
                    f"Receipt #{receipt_num} | Footer", receipt_dir, "Footer.png", fail_data={"global": footer_fails}
                )

                # --- 3. Map Exact Line Item Failures ---
                line_fails = {}
                for lib in r_comp.get("Line_Items_Block", []):
                    xml_row = lib.get("XML_Row", [])
                    if len(xml_row) > 1:
                        ln = str(xml_row[1])
                        fails = []
                        for c in lib.get("Failed_Cols", []):
                            if c > 1: fails.append(lib["Headers"][c-1])
                        if fails: line_fails[ln] = fails

                line_items = receipt_data.get("Line_Items", [])
                chunk_size = 5
                
                if not line_items:
                    print("      -> No Line Items found.")
                else:
                    for i in range(0, len(line_items), chunk_size):
                        chunk = line_items[i : i + chunk_size]
                        start_idx = i + 1
                        end_idx = i + len(chunk)
                        
                        label = f"Receipt #{receipt_num} | Items {start_idx}-{end_idx}"
                        img_filename = f"Items_{start_idx}-{end_idx}.png"
                        
                        await snap_json_screenshot(
                            page, {f"Items_{start_idx}_to_{end_idx}": chunk}, label, receipt_dir, img_filename, 
                            fail_data={"lineItems": line_fails}
                        )
                        
                        for item in chunk:
                            line_num = item.get("Line Number", str(start_idx + chunk.index(item)))
                            mapping_data["Line_Items_Mapping"][line_num] = img_filename

                with open(os.path.join(receipt_dir, "mapping.json"), "w", encoding="utf-8") as map_file:
                    json.dump(mapping_data, map_file, indent=4)
                print(f"      ✅ Created mapping index and snapped {len(mapping_data['Line_Items_Mapping'])} items.")

        await browser.close()
        print(f"\n✅ All XML visual evidence saved to '{BASE_OUTPUT_DIR}' directory.")


def strip_ns(tag):
    return tag.split('}')[-1] if '}' in tag else tag

def extract_xml_data(xml_path, target_receipt_number=None):
    tree = ET.parse(xml_path)
    root = tree.getroot()
    
    for elem in root.iter():
        elem.tag = strip_ns(elem.tag)

    store_id = ""
    store_elem = root.find('.//StoreLocationID')
    if store_elem is not None and store_elem.text:
        store_id = store_elem.text[-4:]

    receipts_data = {}
    events = root.findall('.//SaleEvent') + root.findall('.//RefundEvent') + root.findall('.//FinancialEvent')
    
    for event in events:
        receipt_num = event.findtext('.//TransactionID') or event.findtext('.//DropNumber')
        if not receipt_num:
            continue
            
        if target_receipt_number and str(receipt_num).strip() != str(target_receipt_number).strip():
            continue

        receipt_type = "Sale"
        if event.tag == "RefundEvent":
            receipt_type = "Return"
        elif event.tag == "FinancialEvent":
            receipt_type = "Financial" 

        date_raw = event.findtext('.//BusinessDate') or event.findtext('.//BeginDate') or event.findtext('.//EventStartDate')
        date_formatted = ""
        if date_raw:
            try:
                dt = datetime.strptime(date_raw[:10], "%Y-%m-%d")
                date_formatted = dt.strftime("%m/%d/%Y")
            except Exception:
                date_formatted = date_raw

        cashier = event.findtext('.//OverridingCashierID') or event.findtext('.//CashierID') or ""
        pos = event.findtext('.//RegisterID') or ""

        total_val = event.findtext('.//TransactionTotalNetAmount') or event.findtext('.//TransactionGrossAmount') or event.findtext('.//DropAmount') or "0.00"
        total_tax = event.findtext('.//TransactionTotalTaxNetAmount') or "0.00"

        line_items = []
        tenders = []
        loyalty_card = None
        credit_cards = []
        discount_counters = {}

        for t_line in event.findall('.//TransactionLine'):
            line_num = t_line.findtext('.//TransactionLineSequenceNumber') or ""
            
            pid = t_line.find('.//PersonalID')
            if pid is not None and pid.get('idType') == 'loyaltyCard':
                loyalty_card = pid.text
                
            tender_info = t_line.find('.//TenderInfo')
            if tender_info is not None:
                t_subcode = tender_info.findtext('.//TenderSubCode') or "Unknown"
                t_amount = tender_info.findtext('.//TenderAmount') or "0.00"
                t_card = ""
                
                c_pid = tender_info.find('.//PersonalID')
                if c_pid is not None and c_pid.get('idType') == 'creditCard':
                    t_card = c_pid.text
                elif tender_info.findtext('.//AccountID'):
                    t_card = tender_info.findtext('.//AccountID')
                    
                if t_card and t_card not in credit_cards:
                    credit_cards.append(t_card)
                        
                tenders.append({
                    "Type": t_subcode,
                    "Amount": f"${float(t_amount):.2f}",
                    "Card_Number": t_card
                })

            discount_line = t_line.find('.//DiscountLine')
            if discount_line is not None:
                discount_node = discount_line.find('.//Discount')
                if discount_node is not None:
                    d_amt = discount_node.findtext('.//DiscountAmount') or "0.00"
                    d_reason = discount_node.findtext('.//DiscountReason') or "Discount"
                    
                    target_line = t_line.findtext('.//TargetLineNumber')
                    if not target_line:
                        target_line = line_num
                        
                    if target_line == '0':
                        continue
                    
                    if target_line not in discount_counters:
                        discount_counters[target_line] = 1
                    else:
                        discount_counters[target_line] += 1
                        
                    synthetic_line_num = f"{target_line}_D{discount_counters[target_line]}"
                    
                    line_items.append({
                        "Line Number": synthetic_line_num,
                        "Item Name": d_reason,
                        "Line Type": "Discount",
                        "Item Price": f"${float(d_amt):.2f}" if d_amt else "$0.00",
                        "Quantity": "1",
                        "Value": f"${float(d_amt):.2f}" if d_amt else "$0.00",
                        "Price Override Indicator": "No",
                        "Line Item Weight": "--",
                        "L Fuel Pump ID": "--"
                    })
                continue

            fuel_line = t_line.find('.//FuelLine')
            if fuel_line is not None:
                desc = fuel_line.findtext('.//Description') or ""
                code = fuel_line.findtext('.//MerchandiseCode') or ""
                price = fuel_line.findtext('.//ActualSalesPrice') or "0.00"
                val = fuel_line.findtext('.//SalesAmount') or "0.00"
                
                pump_id = fuel_line.findtext('.//FuelPositionID') or fuel_line.findtext('.//PumpNumber') or "--"
                gallons = fuel_line.findtext('.//SalesQuantity') or "--"
                if gallons != "--":
                    try: gallons = f"{float(gallons):.2f}"
                    except: pass
                
                name = f"{desc} ({code})" if code else desc
                
                line_items.append({
                    "Line Number": line_num,
                    "Item Name": name,
                    "Line Type": "Sale",
                    "Item Price": f"${float(price):.2f}" if price else "$0.00",
                    "Quantity": "1", 
                    "Value": f"${float(val):.2f}" if val else "$0.00",
                    "Price Override Indicator": "No",
                    "Line Item Weight": gallons,
                    "L Fuel Pump ID": pump_id 
                })
                continue

            item_line = t_line.find('.//ItemLine')
            if item_line is not None:
                desc = item_line.findtext('Description') or ""
                code = item_line.findtext('.//POSCode') or item_line.findtext('.//ItemCode') or ""
                
                price = item_line.findtext('RegularSellPrice') or item_line.findtext('ActualSalesPrice') or "0.00"
                qty = item_line.findtext('SalesQuantity') or item_line.findtext('Quantity') or "0"
                val = item_line.findtext('SalesAmount') or item_line.findtext('ExtendedAmount') or "0.00"
                
                actual_weight = item_line.findtext('.//ActualWeight')
                if actual_weight:
                    weight = f"{float(actual_weight):.2f}"
                else:
                    weight = f"{float(qty):.2f}" if qty else "1.00"
                
                override_flag = "Yes" if item_line.find('.//PriceOverride') is not None else "No"
                name = f"{desc} ({code})" if code else desc
                
                status_attr = t_line.get('status', 'normal').lower()
                if status_attr == 'cancel' or t_line.find('.//Cancellation') is not None:
                    line_type = "Cancel"
                elif status_attr == 'void':
                    line_type = "Void"
                elif status_attr == 'other':
                    desc_status = t_line.findtext('.//TransactionLineStatusDescription')
                    if desc_status and ('cancel' in desc_status.lower() or 'failure' in desc_status.lower()):
                        line_type = "Cancel"
                    else:
                        line_type = "Other"
                elif event.tag == "RefundEvent":
                    line_type = "Return"
                else:
                    line_type = "Sale"
                
                line_items.append({
                    "Line Number": line_num,
                    "Item Name": name,
                    "Line Type": line_type,
                    "Item Price": f"${float(price):.2f}" if price else "$0.00",
                    "Quantity": str(int(float(qty))) if qty else "0",
                    "Value": f"${float(val):.2f}" if val else "$0.00",
                    "Price Override Indicator": override_flag,
                    "Line Item Weight": weight,
                    "L Fuel Pump ID": "--"
                })
                
                for sub_line in item_line.findall('.//SubItemLine'):
                    sub_num = sub_line.findtext('.//SubItemLineSequenceNumber') or ""
                    
                    comp = sub_line.find('.//ComponentItem')
                    s_desc = ""
                    s_code = ""
                    if comp is not None:
                        s_desc = comp.findtext('Description') or ""
                        s_code = comp.findtext('.//POSCode') or ""
                    
                    s_price = sub_line.findtext('.//RegularSellPrice') or sub_line.findtext('.//ActualSalesPrice') or "0.00"
                    s_val = sub_line.findtext('.//SalesAmount') or "0.00"
                    s_qty_raw = sub_line.findtext('.//SalesQuantity') or "1"
                    
                    if s_val and float(s_val) != 0:
                        s_qty = str(int(float(s_qty_raw)))
                    else:
                        s_qty = "0"
                    
                    s_name = f"{s_desc} ({s_code})" if s_code else s_desc
                    synthetic_line_num = f"{line_num}999{sub_num}" if line_num and sub_num else sub_num
                    
                    line_items.append({
                        "Line Number": synthetic_line_num, 
                        "Item Name": s_name,
                        "Line Type": "SubItemLine",
                        "Item Price": f"${float(s_price):.2f}" if s_price else "$0.00",
                        "Quantity": s_qty,
                        "Value": f"${float(s_val):.2f}" if s_val else "$0.00",
                        "Price Override Indicator": "No",
                        "Line Item Weight": "--",
                        "L Fuel Pump ID": "--"
                    })

        def sort_by_line_num(item):
            ln = str(item.get('Line Number', ''))
            if ln.isdigit():
                return float(ln)
            if '_D' in ln:
                parts = ln.split('_D')
                if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                    return float(parts[0]) + (float(parts[1]) / 100.0)
            
            try: return float(ln)
            except: return 999999.0
            
        line_items.sort(key=sort_by_line_num)

        header_dict = {
            "Store": store_id,
            "Receipt_Number": receipt_num,
            "Date": date_formatted,
            "Cashier": cashier,
            "POS": pos,
            "Receipt_Type": receipt_type,
            "Loyalty_Card": loyalty_card,
            "Credit_Card_Number": None
        }
            
        if not credit_cards:
            pass
        elif len(credit_cards) == 1:
            header_dict["Credit_Card_Number"] = credit_cards[0]
        else:
            del header_dict["Credit_Card_Number"]
            for i, card in enumerate(credit_cards):
                header_dict[f"Credit_Card_Number-{i+1}"] = card

        receipts_data[receipt_num] = {
            "Header": header_dict,
            "Line_Items": line_items,
            "Tenders": tenders,
            "Totals": {
                "Total_Value": f"${float(total_val):.2f}",
                "Total_Tax": f"${float(total_tax):.2f}"
            }
        }
        
        if target_receipt_number:
            return receipts_data

    return receipts_data

def launch_ui():
    root = tk.Tk()
    root.title("XML Extractor")
    root.geometry("400x200")
    try: root.eval('tk::PlaceWindow . center')
    except: pass

    selected_path = None

    def select_file():
        nonlocal selected_path
        path = filedialog.askopenfilename(title="Select XML File", filetypes=[("XML Files", "*.xml")])
        if path:
            selected_path = path
            root.destroy()

    def select_folder():
        nonlocal selected_path
        path = filedialog.askdirectory(title="Select Folder containing NAXML files")
        if path:
            selected_path = path
            root.destroy()

    tk.Label(root, text="Select XML Source", font=("Arial", 14, "bold")).pack(pady=20)
    tk.Button(root, text="📄 Select Single XML File", command=select_file, width=25, height=2).pack(pady=5)
    tk.Button(root, text="📁 Select Folder of XMLs", command=select_folder, width=25, height=2).pack(pady=5)

    root.mainloop()
    return selected_path

async def run_batch_extraction(target_path=None):
    print("="*60)
    print("🗄️  PHASE 3: BATCH XML EXTRACTION STARTED (LEGACY PUSH)")
    print("="*60)
    
    if not target_path:
        target_path = launch_ui()
        
    if not target_path:
        print("❌ No file or folder selected. Exiting.")
        return
        
    xml_files = []
    if os.path.isfile(target_path):
        xml_files.append(target_path)
    elif os.path.isdir(target_path):
        xml_files = glob.glob(os.path.join(target_path, "*.xml"))
        
    if not xml_files:
        print(f"❌ No XML files found at: {target_path}")
        return
        
    print(f"📂 Found {len(xml_files)} XML file(s). Extracting full database records...\n")
    
    all_results = {}
    total_receipts_found = 0
    
    for xml_file in xml_files:
        filename = os.path.basename(xml_file)
        try:
            data = extract_xml_data(xml_file)
            all_results[filename] = data
            receipt_count = len(data)
            total_receipts_found += receipt_count
            print(f"   ✅ Parsed {filename} | Receipts: {receipt_count}")
        except Exception as e:
            print(f"   ❌ Failed to parse {filename}: {e}")
            all_results[filename] = {"error": str(e)}

    output_file = "xml_batch_results.json"
    with open(output_file, "w") as f:
        json.dump(all_results, f, indent=4)
        
    print(f"\n📁 XML database extraction complete. Saved to '{output_file}'.")
    
    # Standalone execution will generate un-highlighted screenshots
    print("\n📸 Starting XML Visual Evidence Generation...")
    await process_screenshots(all_results)
    
    return output_file

async def run_targeted_extraction(target_mapping, output_file="xml_batch_results.json"):
    print("="*60)
    print("🗄️  PHASE 3: TARGETED XML DATABASE EXTRACTION (NEW PULL)")
    print("="*60)
    
    all_results = {}
    
    for receipt_num, xml_file in target_mapping.items():
        filename = os.path.basename(xml_file)
        if filename not in all_results:
            all_results[filename] = {}
            
        try:
            data = extract_xml_data(xml_file, target_receipt_number=receipt_num)
            if data:
                all_results[filename].update(data)
                print(f"   ✅ Extracted Record for Receipt: {receipt_num}")
            else:
                print(f"   ⚠️ Could not extract Record for Receipt: {receipt_num}")
        except Exception as e:
            print(f"   ❌ Failed to parse {filename} for {receipt_num}: {e}")
            all_results[filename] = {"error": str(e)}

    with open(output_file, "w") as f:
        json.dump(all_results, f, indent=4)
        
    print(f"\n📁 Database extraction complete. Saved to '{output_file}'.")
    # Screenshots removed from Phase 3 to allow Phase 4.5 to process failures first.
    return output_file

if __name__ == "__main__":
    asyncio.run(run_batch_extraction())
