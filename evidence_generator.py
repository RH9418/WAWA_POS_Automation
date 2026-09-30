import json
import os
import re
from datetime import datetime
import asyncio
from playwright.async_api import async_playwright

BASE_OUTPUT_DIR = "xml_visual_evidence"

# --- FIX: JavaScript is completely removed. Python handles the syntax and failure rendering natively. ---
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
            white-space: pre-wrap; 
            word-wrap: break-word;
        }
        .json-key { color: #9cdcfe; }
        .json-string { color: #ce9178; }
        .json-number { color: #b5cea8; }
        .json-boolean { color: #569cd6; }
        .json-null { color: #569cd6; font-style: italic; }
        
        .highlight-line {
            background-color: rgba(255, 80, 80, 0.25) !important; 
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
        <pre id="json-content">{html_content}</pre>
    </div>
</body>
</html>
"""

def generate_highlighted_html(data_dict, fail_data, label):
    if not fail_data:
        fail_data = {}
        
    raw_json = json.dumps(data_dict, indent=4)
    safe_json = raw_json.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    
    # 1. Apply VS Code Style Syntax Highlighting strictly in Python
    pattern = re.compile(
        r'("[^"\\]*(?:\\.[^"\\]*)*":)'    
        r'|("[^"\\]*(?:\\.[^"\\]*)*")'    
        r'|(\btrue\b|\bfalse\b|\bnull\b)' 
        r'|(-?\d+(?:\.\d*)?(?:[eE][+\-]?\d+)?)' 
    )
    
    def replacer(match):
        if match.group(1):
            return f'<span class="json-key">{match.group(1)[:-1]}</span>:'
        elif match.group(2):
            return f'<span class="json-string">{match.group(2)}</span>'
        elif match.group(3):
            m = match.group(3)
            cls = 'json-boolean' if m in ('true', 'false') else 'json-null'
            return f'<span class="{cls}">{m}</span>'
        elif match.group(4):
            return f'<span class="json-number">{match.group(4)}</span>'
        return match.group(0)
        
    highlighted = pattern.sub(replacer, safe_json)
    
    # 2. Paint the Failure Blocks Red Line-by-Line
    lines = highlighted.split('\n')
    current_line_item = None
    
    for i in range(len(lines)):
        line = lines[i]
        
        # Track the active JSON block based on the generated Line Number tags
        ln_match = re.search(r'"Line Number"</span>:\s*<span class="json-string">"([^"]+)"</span>', line)
        if ln_match:
            current_line_item = ln_match.group(1).strip()
            
        active_fails = []
        if fail_data.get("global"):
            active_fails.extend(fail_data["global"])
        if current_line_item and fail_data.get("lineItems", {}).get(current_line_item):
            active_fails.extend(fail_data["lineItems"][current_line_item])
            
        # Hardcode the CSS wrappers if failures match
        for field in active_fails:
            target_key_html = f'<span class="json-key">"{field}"</span>:'
            if target_key_html in line:
                red_key = f'<span class="highlight-key">"{field}"</span>:'
                line = line.replace(target_key_html, red_key)
                line = f'<span class="highlight-line">{line}</span>'
                lines[i] = line
                break 
                
    html_content = HTML_TEMPLATE.replace("{label}", label).replace("{html_content}", '\n'.join(lines))
    return html_content

async def snap_json_screenshot(page, data_dict, label, output_dir, filename, fail_data=None):
    # Instead of injecting JS variables, we hand the browser a fully rendered string
    html = generate_highlighted_html(data_dict, fail_data, label)
    
    await page.set_content(html)
    box_locator = page.locator("#capture-box")
    await box_locator.wait_for(state="visible")
    
    save_path = os.path.join(output_dir, filename)
    await box_locator.screenshot(path=save_path)
    return filename

async def process_screenshots():
    """Phase 4.5 Engine: Generates XML evidence images and applies visual failures."""
    os.makedirs(BASE_OUTPUT_DIR, exist_ok=True)
    run_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    comp_results = {}
    if os.path.exists("comparison_results.json"):
        with open("comparison_results.json", "r") as f:
            comp_results = json.load(f)
            
    batch_data = {}
    if os.path.exists("xml_batch_results.json"):
        with open("xml_batch_results.json", "r") as f:
            batch_data = json.load(f)

    if not batch_data:
        print("❌ No batch data found to generate screenshots. Run Phase 3 first.")
        return

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
                if hb and "Headers" in hb:
                    for c in hb.get("Failed_Cols", []):
                        if c > 1: header_fails.append(hb["Headers"][c-1].strip())
                        
                await snap_json_screenshot(page, {"Header": receipt_data.get("Header", {})}, f"Receipt #{receipt_num} | Header", receipt_dir, "Header.png", fail_data={"global": header_fails})

                # --- 2. Map Footer Failures ---
                footer_fails = []
                footer_key_map = {"Total Value": "Total_Value", "Total Tax": "Total_Tax", "Amount": "Amount", "Tender Type": "Type", "Card Number": "Card_Number"}
                
                ab = r_comp.get("Aggregate_Block", {})
                if ab and "Headers" in ab:
                    for c in ab.get("Failed_Cols", []):
                        if c > 1: footer_fails.append(footer_key_map.get(ab["Headers"][c-1].strip(), ab["Headers"][c-1].strip()))

                for tb in r_comp.get("Tenders_Block", []):
                    if "Headers" in tb:
                        for c in tb.get("Failed_Cols", []):
                            if c > 1: footer_fails.append(footer_key_map.get(tb["Headers"][c-1].strip(), tb["Headers"][c-1].strip()))

                await snap_json_screenshot(page, {"Tenders": receipt_data.get("Tenders", []), "Totals": receipt_data.get("Totals", {})}, f"Receipt #{receipt_num} | Footer", receipt_dir, "Footer.png", fail_data={"global": footer_fails})

                # --- 3. Map Exact Line Item Failures ---
                line_fails = {}
                lib_list = r_comp.get("Line_Items_Block", [])
                
                line_headers = []
                if lib_list and "Headers" in lib_list[0]:
                    line_headers = lib_list[0]["Headers"]

                for lib in lib_list:
                    xml_row = lib.get("XML_Row", [])
                    ui_row = lib.get("UI_Row", [])
                    
                    if len(xml_row) > 1 and len(ui_row) > 1 and line_headers:
                        ln_str = str(xml_row[1]) if str(xml_row[1]) != "--" else str(ui_row[1])
                        ln = ln_str.strip()
                        
                        fails = []
                        for c in lib.get("Failed_Cols", []):
                            if c > 0 and (c-1) < len(line_headers):
                                fails.append(line_headers[c-1].strip())
                        
                        if fails:
                            if ln not in line_fails:
                                line_fails[ln] = []
                            line_fails[ln].extend(fails)
                
                if line_fails:
                    print(f"      -> Applied Line Item Highlights: {line_fails}")

                line_items = receipt_data.get("Line_Items", [])
                chunk_size = 5
                
                if line_items:
                    for i in range(0, len(line_items), chunk_size):
                        chunk = line_items[i : i + chunk_size]
                        start_idx = i + 1
                        end_idx = i + len(chunk)
                        label = f"Receipt #{receipt_num} | Items {start_idx}-{end_idx}"
                        img_filename = f"Items_{start_idx}-{end_idx}.png"
                        
                        await snap_json_screenshot(page, {f"Items_{start_idx}_to_{end_idx}": chunk}, label, receipt_dir, img_filename, fail_data={"lineItems": line_fails})
                        
                        for item in chunk:
                            line_num = item.get("Line Number", str(start_idx + chunk.index(item)))
                            mapping_data["Line_Items_Mapping"][line_num] = img_filename

                with open(os.path.join(receipt_dir, "mapping.json"), "w", encoding="utf-8") as map_file:
                    json.dump(mapping_data, map_file, indent=4)
                    
        await browser.close()
        print(f"\n✅ All XML visual evidence saved to '{BASE_OUTPUT_DIR}' directory.")

if __name__ == "__main__":
    asyncio.run(process_screenshots())
