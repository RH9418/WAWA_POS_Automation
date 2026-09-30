import os
import re
import json
import asyncio
from datetime import datetime, timedelta
from playwright.async_api import async_playwright

DOWNLOAD_DIR = os.path.join(os.getcwd(), "downloaded_receipts")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

def format_time(time_str, offset_minutes=0):
    dt = datetime.strptime(time_str, "%H:%M:%S")
    dt = dt + timedelta(minutes=offset_minutes)
    return dt.strftime("%I:%M %p") 

async def wait_for_network(page, max_timeout=0):
    """
    Waits indefinitely (timeout=0) for the network to reach an idle state.
    Once idle, waits an additional 2 seconds to allow the DOM to fully render.
    """
    try:
        await page.wait_for_load_state("networkidle", timeout=max_timeout)
    except Exception:
        pass 
    await page.wait_for_timeout(2000) # Extra buffer for Angular/Kendo rendering

async def safe_check(page, regex_pattern, max_retries=5):
    """
    Waits for network idle, then polls for the checkbox up to max_retries times.
    Prevents infinite deadlocks if the filter data doesn't exist in the DB.
    """
    await wait_for_network(page)
    checkbox = page.get_by_role("checkbox", name=regex_pattern).first
    
    for _ in range(max_retries):
        if await checkbox.is_visible():
            await checkbox.check(timeout=3000)
            return
        await page.wait_for_timeout(1000)
        
    raise Exception(f"Dynamic checkbox matching '{regex_pattern.pattern}' not found after {max_retries} retries.")

# --- DOM HIGHLIGHTING ENGINE (FULL PAGE) ---
async def highlight_and_screenshot(page, target_locator, save_path, scroll_position="none"):
    """
    Injects a thick red bounding box over the target, takes a FULL PAGE screenshot, 
    and then cleans up the CSS. Resilient against DOM errors and centering conflicts.
    """
    try:
        # 1. Force the target row into the viewport FIRST
        try:
            await target_locator.scroll_into_view_if_needed(timeout=2000)
            await page.wait_for_timeout(300) # Give the grid a split second to render
        except:
            pass

        # 2. Inject Highlight with !important to override Kendo/Angular styling
        highlight_success = False
        try:
            if await target_locator.is_visible():
                await page.evaluate("""(element) => {
                    if (element) {
                        element.setAttribute('data-original-outline', element.style.outline || '');
                        element.setAttribute('data-original-bg', element.style.backgroundColor || '');
                        element.style.setProperty('outline', '4px solid red', 'important');
                        element.style.setProperty('background-color', 'rgba(255, 0, 0, 0.2)', 'important');
                    }
                }""", await target_locator.element_handle(timeout=2000))
                highlight_success = True
                await page.wait_for_timeout(200)
        except Exception as e:
            print(f"      ⚠️ Highlight failed (taking clean screenshot instead): {e}")

        # 3. BRUTE-FORCE GLOBAL SCROLL (Done right before screenshot to override any centering from focus)
        if scroll_position == "bottom":
            try:
                await page.evaluate("""() => {
                    window.scrollTo(0, document.body.scrollHeight);
                    const scrollables = document.querySelectorAll('*');
                    for (let s of scrollables) {
                        const style = window.getComputedStyle(s);
                        if (style.overflowY === 'auto' || style.overflowY === 'scroll' || style.overflow === 'auto') {
                            s.scrollTop = s.scrollHeight;
                        }
                    }
                }""")
                await page.wait_for_timeout(500)
            except:
                pass
        elif scroll_position == "top":
            try:
                await page.evaluate("""() => {
                    window.scrollTo(0, 0);
                    const scrollables = document.querySelectorAll('*');
                    for (let s of scrollables) {
                        const style = window.getComputedStyle(s);
                        if (style.overflowY === 'auto' || style.overflowY === 'scroll' || style.overflow === 'auto') {
                            s.scrollTop = 0;
                        }
                    }
                }""")
                await page.wait_for_timeout(500)
            except:
                pass

        # 4. Screenshot the FULL PAGE
        await page.screenshot(path=save_path)
        
        # 5. Cleanup Highlight
        if highlight_success:
            try:
                await page.evaluate("""(element) => {
                    if (element) {
                        element.style.setProperty('outline', element.getAttribute('data-original-outline'), 'important');
                        element.style.setProperty('background-color', element.getAttribute('data-original-bg'), 'important');
                    }
                }""", await target_locator.element_handle(timeout=2000))
            except:
                pass
        
        return save_path
    except Exception as e:
        print(f"      ⚠️ Failed to capture screenshot for {save_path}: {e}")
        # Final fallback: just take the screenshot
        try:
            await page.screenshot(path=save_path)
            return save_path
        except:
            return None

async def process_receipt(page, data, is_first_run=True):
    receipt_num = data['Receipt_Number']
    print(f"\n🔄 Processing Receipt: {receipt_num}")
    
    # Generate Evidence Directory
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    evidence_dir = os.path.join(DOWNLOAD_DIR, f"Receipt_{receipt_num}_{timestamp}")
    os.makedirs(evidence_dir, exist_ok=True)
    evidence_paths = {}
    
    receipt_found = False
    
    # =========================================================================
    # PROGRESSIVE FALLBACK SEARCH LOOP (3 ATTEMPTS)
    # =========================================================================
    for search_attempt in range(1, 4):
        print(f"\n      --- Search Attempt {search_attempt}/3 ---")
        if search_attempt == 3:
            print("      ⚠️ Applying CRITICAL FILTERS ONLY (Date, Store, Receipt Number)...")
            
        # WRAP THE ENTIRE SEARCH ATTEMPT IN A SAFETY BLOCK
        try:
            for attempt in range(3):
                try:
                    if is_first_run and search_attempt == 1:
                        print("   -> Navigating to Receipt Search...")
                        await page.get_by_role("link", name="Receipt search").click(timeout=5000)
                    else:
                        print("   -> Clearing previous filters for new search...")
                        collapse_btn = page.get_by_role("button", name="Collapse receipt view").first
                        if await collapse_btn.is_visible():
                            await collapse_btn.click()
                            await page.wait_for_timeout(500)
                            
                        clear_btn = page.get_by_role("button", name="Clear").first
                        if not await clear_btn.is_visible():
                            search_form_btn = page.get_by_role("button", name="Receipt search form").first
                            if await search_form_btn.is_visible():
                                await search_form_btn.click()
                                await clear_btn.wait_for(state="visible", timeout=5000)
                                
                        if await clear_btn.is_visible():
                            await clear_btn.click()
                            
                    await wait_for_network(page, 2000)
                    break
                except Exception as e:
                    if attempt == 2: raise e
                    print(f"      ⚠️ Navigation/Clear failed. Retrying (Attempt {attempt+2}/3)...")
                    await page.wait_for_timeout(1500)

            print(f"   -> Setting Date: {data['Date']}")
            target_dt = datetime.strptime(data["Date"], "%Y-%m-%d")
            target_date_name = f"{target_dt.strftime('%B')} {target_dt.day}," 
            
            for attempt in range(3):
                try:
                    calendar_btn = page.locator("#search-form").get_by_role("button", name="Open calendar").first
                    await calendar_btn.wait_for(state="attached", timeout=5000)
                    
                    if await calendar_btn.is_disabled():
                        print("      * Date filter inactive. Clicking 'Make active'...")
                        await page.locator("[data-qa-id='e2e-dates_filter_from_to_date_active']").click()
                    
                    await calendar_btn.click()
                    
                    for _ in range(12): 
                        await page.wait_for_timeout(400) 
                        date_btn = page.get_by_role("button", name=re.compile(target_date_name)).first
                        if await date_btn.is_visible():
                            await date_btn.click() 
                            await page.wait_for_timeout(200)
                            await date_btn.click() 
                            break
                        await page.get_by_role("button", name="Previous month").first.click()
                    break
                except Exception as e:
                    if attempt == 2: raise e
                    print(f"      ⚠️ Date filter failed. Retrying (Attempt {attempt+2}/3)...")
                    try: await page.keyboard.press("Escape")
                    except: pass
                    await page.wait_for_timeout(1000)

            if search_attempt < 3:
                from_time = format_time(data['FromTime'], offset_minutes=-1)
                to_time = format_time(data['ToTime'], offset_minutes=1)
                print(f"   -> Setting Time: {from_time} to {to_time}")
                
                for attempt in range(3):
                    try:
                        from_combo = page.locator("kendo-formfield").filter(has_text="From time").get_by_role("combobox")
                        await from_combo.click(timeout=3000)
                        await page.keyboard.press("Home") 
                        await from_combo.press_sequentially(from_time, delay=50) 
                        await from_combo.press("Enter")
                        
                        to_combo = page.locator("#search-form").get_by_role("combobox", name="To time")
                        await to_combo.click(timeout=3000)
                        await page.keyboard.press("Home") 
                        await to_combo.press_sequentially(to_time, delay=50)
                        await to_combo.press("Enter")
                        break
                    except Exception as e:
                        if attempt == 2: raise e
                        print(f"      ⚠️ Time filter failed. Retrying (Attempt {attempt+2}/3)...")
                        await page.wait_for_timeout(1000)

            store_id = str(int(data["Store"])) 
            print(f"   -> Setting Store: {store_id}")
            
            for attempt in range(3):
                try:
                    # STRICT STORE FIX: Try 'WAWA STORE {id}' on Attempt 1, fallback to raw '{id}' on Attempt 2
                    search_term = f"WAWA STORE {store_id}" if attempt == 0 else store_id
                    
                    await page.get_by_role("textbox", name="Store").click(timeout=5000)
                    await page.locator("mat-tree-node").filter(has_text="Store").get_by_role("button").click(timeout=5000)
                    await page.get_by_role("textbox", name="Search").fill(search_term)
                    await safe_check(page, re.compile(search_term, re.IGNORECASE))
                    await page.locator(".cdk-overlay-backdrop").click(timeout=5000)
                    break
                except Exception as e:
                    if attempt == 2: raise e
                    print(f"      ⚠️ Store filter failed. Retrying (Attempt {attempt+2}/3)...")
                    try: await page.locator(".cdk-overlay-backdrop").click(timeout=2000)
                    except: pass
                    await page.wait_for_timeout(1500)

            if search_attempt < 3:
                cashier_id = data["Cashier"]
                print(f"   -> Setting Cashier: {cashier_id}")
                
                for attempt in range(3):
                    try:
                        await page.locator("mat-form-field").filter(has_text=re.compile("Cashier")).locator("mat-icon").click(timeout=5000)
                        await page.get_by_role("textbox", name="Search").click(timeout=5000)
                        await page.get_by_role("textbox", name="Search").fill(cashier_id)
                        await safe_check(page, re.compile(cashier_id))
                        await page.locator(".cdk-overlay-backdrop").click(timeout=5000)
                        break
                    except Exception as e:
                        if attempt == 2: raise e
                        print(f"      ⚠️ Cashier filter failed. Retrying (Attempt {attempt+2}/3)...")
                        try: await page.locator(".cdk-overlay-backdrop").click(timeout=2000)
                        except: pass
                        await page.wait_for_timeout(1500)

            if search_attempt < 3:
                pos_id = data["POS_Number"]
                print(f"   -> Setting POS: {pos_id}")
                
                for attempt in range(3):
                    try:
                        await page.locator("mat-form-field").filter(has_text=re.compile("POS #")).locator("mat-icon").click(timeout=5000)
                        await page.get_by_role("textbox", name="Search").fill(pos_id)
                        await safe_check(page, re.compile(f"{store_id}_{pos_id}"))
                        await page.locator(".cdk-overlay-backdrop").click(timeout=5000)
                        break
                    except Exception as e:
                        if attempt == 2: raise e
                        print(f"      ⚠️ POS filter failed. Retrying (Attempt {attempt+2}/3)...")
                        try: await page.locator(".cdk-overlay-backdrop").click(timeout=2000)
                        except: pass
                        await page.wait_for_timeout(1500)

            print(f"   -> Setting Receipt Number: {receipt_num}")
            await page.get_by_role("spinbutton", name="Receipt Number").fill(receipt_num)

            print("   -> Clicking Search!")
            await page.get_by_role("button", name="Search").click(timeout=5000)
            
            print("   -> Waiting for network API and search results to settle...")
            await wait_for_network(page, max_timeout=15000)
            
            try:
                receipt_indicator = page.get_by_text(re.compile(f"Receipt view #{receipt_num}")).first
                await receipt_indicator.wait_for(state="visible", timeout=5000)
                receipt_found = True
            except Exception:
                try:
                    expand_btn_check = page.get_by_role("button", name="Expand receipt view").first
                    await expand_btn_check.wait_for(state="visible", timeout=3000)
                    receipt_found = True
                except Exception:
                    receipt_found = False

            if receipt_found:
                print("      * Receipt found successfully!")
                break 
            else:
                print(f"      ⚠️ Receipt {receipt_num} NOT FOUND on Attempt {search_attempt}.")
                
        except Exception as attempt_err:
            print(f"      ⚠️ Search Attempt {search_attempt} aborted due to pipeline error: {attempt_err}")
            # Ensure the screen is clear before attempting the next fallback attempt
            try: await page.locator(".cdk-overlay-backdrop").click(timeout=1000)
            except: pass
            await page.wait_for_timeout(1000)

    if not receipt_found:
        print(f"      ❌ ALERT: Receipt {receipt_num} was completely NOT FOUND in the UI after 3 attempts.")
        print(f"      ⚠️ Skipping PDF download and moving to next receipt.")
        return False, None 

    for attempt in range(3):
        try:
            show_receipt_switch = page.get_by_role("switch", name="Show receipt").first
            if await show_receipt_switch.is_visible() and not await show_receipt_switch.is_checked():
                print("      * Toggling 'Show receipt' ON...")
                await show_receipt_switch.click(timeout=5000)
                await wait_for_network(page, 2000) 
            break
        except Exception:
            if attempt == 2: pass
            await page.wait_for_timeout(1000)

    print("   -> Expanding receipt view...")
    expand_btn = page.get_by_role("button", name="Expand receipt view").first
    await expand_btn.wait_for(state="visible", timeout=5000)
    await expand_btn.click(timeout=5000)
    
    await wait_for_network(page, 3000)
    await page.wait_for_timeout(1500) 
    
    # =========================================================================
    # UI SCRAPING AND VISUAL EVIDENCE LOGIC
    # =========================================================================
    print("   -> 🕵️‍♂️ Waiting for grid to populate and scraping line items...")
    
    scraped_line_items_dict = {} 
    previous_page_data = None
    page_number = 1
    
    # DYNAMIC DISCOUNT TRACKERS
    confirmed_last_line = None
    confirmed_discount_counters = {}
    
    def safe_get(lst, idx, default=""):
        if idx is not None and idx < len(lst):
            val = lst[idx].strip()
            return val if val else default
        return default

    # --- 1. Find Header Anchor Row & Grid Container ---
    col_map = {}
    grid_container = None
    for poll_attempt in range(10):
        rows = page.locator("tr, .mat-mdc-row, .k-table-row, [role='row']")
        row_count = await rows.count()
        for i in range(row_count):
            row = rows.nth(i)
            cells = row.locator("th, td, .mat-mdc-cell, .k-table-td, [role='columnheader'], [role='gridcell'], [role='cell']")
            cell_count = await cells.count()
            if cell_count >= 6:
                cell_texts = [(await cells.nth(c).inner_text()).strip() for c in range(cell_count)]
                if "Line Number" in cell_texts and "Item Name" in cell_texts:
                    for idx, header in enumerate(cell_texts):
                        col_map[header.strip()] = idx
                    
                    header_row = page.locator("tr, .mat-mdc-row, .k-table-row, [role='row']").filter(has_text="Line Number").filter(has_text="Item Name").first
                    grid_container = header_row.locator("xpath=ancestor::kendo-grid | ancestor::table").first
                    break
        if col_map:
            break
        await page.wait_for_timeout(1000)

    if not col_map:
        print("      ⚠️ Warning: Could not locate dynamic column headers.")
    else:
        print(f"      * Dynamic Headers Mapped: {col_map}")

    # --- 2. Data Row Extraction & Bounded Screenshots ---
    while True:
        # --- NEW VISUAL EVIDENCE LOGIC (FULL PAGE FOR LINE ITEMS) ---
        img_path = os.path.join(evidence_dir, f"LineItems_Page_{page_number}.png")
        if grid_container:
            try:
                await grid_container.scroll_into_view_if_needed(timeout=2000)
                await page.wait_for_timeout(500)
                
                # Take a full page screenshot. No cropping or DOM hiding.
                await page.screenshot(path=img_path)
                evidence_paths[f"LineItems_Page_{page_number}"] = img_path
                print(f"      * Captured Full Page Line Item Grid for Page {page_number}")
            except Exception as e:
                print(f"      ⚠️ Failed to capture grid screenshot for Page {page_number}: {e}")
                img_path = None
        # ------------------------------------------------------------

        current_page_data = []
        
        for poll_attempt in range(10):
            current_page_data = []
            
            # Temporary state for this poll attempt to prevent overlapping page counters
            poll_last_line = confirmed_last_line
            poll_discount_counters = dict(confirmed_discount_counters)
            
            rows = page.locator("tr, .mat-mdc-row, .k-table-row, [role='row']")
            row_count = await rows.count()
            
            for i in range(row_count):
                row = rows.nth(i)
                cells = row.locator("td, .mat-mdc-cell, .k-table-td, [role='gridcell'], [role='cell']")
                cell_count = await cells.count()
                
                if cell_count >= max(col_map.values(), default=6) and not (await cells.nth(col_map.get("Item Name", 1)).inner_text()).strip() == "Item Name":
                    cell_texts = [(await cells.nth(c).inner_text()).strip() for c in range(cell_count)]
                    
                    line_num_text = safe_get(cell_texts, col_map.get("Line Number"))
                    item_name_text = safe_get(cell_texts, col_map.get("Item Name")).replace('\n', ' ')
                    
                    assigned_line_num = None
                    
                    # 1. Standard Item Line
                    if line_num_text.isdigit() and line_num_text != str(receipt_num):
                        assigned_line_num = line_num_text
                        poll_last_line = line_num_text
                        poll_discount_counters[poll_last_line] = 0
                        
                    # 2. Discount / Sub-Line (Blank line number, but valid data under a parent)
                    elif (not line_num_text or line_num_text.strip() == "") and poll_last_line:
                        if item_name_text and item_name_text not in ["", "--", "Unknown"]:
                            poll_discount_counters[poll_last_line] = poll_discount_counters.get(poll_last_line, 0) + 1
                            assigned_line_num = f"{poll_last_line}_D{poll_discount_counters[poll_last_line]}"
                            
                    if assigned_line_num:
                        if not item_name_text or item_name_text == "--":
                            item_name_text = "Unknown"
                            
                        row_dict = {
                            "Line Number": assigned_line_num,
                            "Item Name": item_name_text,
                            "Line Type": safe_get(cell_texts, col_map.get("Line Type")),
                            "Item Price": safe_get(cell_texts, col_map.get("Item Price")),
                            "Line Item Quantity": safe_get(cell_texts, col_map.get("Line Item Quantity")),
                            "Quantity": safe_get(cell_texts, col_map.get("Line Item Quantity")),
                            "Value": safe_get(cell_texts, col_map.get("Line Item Value")),
                            "Price Override Indicator": safe_get(cell_texts, col_map.get("Price Override Indicator")),
                            "Line Item Weight": safe_get(cell_texts, col_map.get("Line Item Weight")),
                            "L Fuel Pump ID": safe_get(cell_texts, col_map.get("L Fuel Pump ID")),
                            "Screenshot": img_path  # TRACKING ADDED HERE
                        }
                        
                        if row_dict not in current_page_data:
                            current_page_data.append(row_dict)
            
            if len(current_page_data) > 0 and current_page_data != previous_page_data:
                # Successfully polled a unique page. Commit the trackers to global state.
                confirmed_last_line = poll_last_line
                confirmed_discount_counters = poll_discount_counters
                break
            await page.wait_for_timeout(1000)
            
        if current_page_data == previous_page_data or len(current_page_data) == 0:
            break
            
        for row in current_page_data:
            l_num = row["Line Number"]
            if l_num not in scraped_line_items_dict:
                scraped_line_items_dict[l_num] = row
                
        previous_page_data = current_page_data
        
        next_btn = page.get_by_role("button", name="Go to the next page").first
        await page.wait_for_timeout(500)
        
        if not await next_btn.is_visible() or await next_btn.is_disabled():
            break
            
        await next_btn.click(timeout=5000)
        page_number += 1
        
        await wait_for_network(page, 2000)
        await page.wait_for_timeout(1000) 

    scraped_line_items = list(scraped_line_items_dict.values())
    
    # Custom sort to handle synthetic discounts (e.g., 1_D1 comes immediately after 1)
    def sort_by_synthetic_num(item):
        ln = str(item.get('Line Number', ''))
        if ln.isdigit():
            return float(ln)
        if '_D' in ln:
            parts = ln.split('_D')
            if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                return float(parts[0]) + (float(parts[1]) / 100.0)
        try: return float(ln)
        except: return 999999.0
        
    scraped_line_items.sort(key=sort_by_synthetic_num)
    
    print(f"      * Successfully scraped {len(scraped_line_items)} unique line items across {page_number} page(s).")
    
    # =========================================================================
    # DEEP SCRAPE: TOTALS, HEADERS, LOYALTY, AND TENDERS (AND SCREENSHOTS)
    # =========================================================================
    def extract_multiple_regex(pattern, text, base_key):
        matches = [m.group(1).strip() for m in re.finditer(pattern, text, re.IGNORECASE) if m.group(1).strip()]
        if not matches: return {base_key: None}
        if len(matches) == 1: return {base_key: matches[0]}
        return {f"{base_key}-{i+1}": match for i, match in enumerate(matches)}

    header_extras = {}
    totals_extras = {"Total_Value": "0.00", "Total_Tax": "0.00"}
    scraped_tenders = []
    modal_text = ""

    try:
        dialog = page.locator("[role='dialog'], mat-dialog-container, kendo-dialog, .cdk-dialog-container").first
        modal_text = await dialog.inner_text() if await dialog.is_visible() else await page.inner_text()
            
        # 1. Extract Headers (ALL RAW DISPLAY STRINGS FOR COMPARATOR VALIDATION)
        header_extras.update(extract_multiple_regex(r'Receipt Type[\s\n]*([A-Za-z]+)', modal_text, 'Receipt_Type'))
        header_extras.update(extract_multiple_regex(r'Loyalty Card(?: Number)?[\s\n]*([^\n]+)', modal_text, 'Loyalty_Card'))
        
        header_extras.update(extract_multiple_regex(r'Store\s*\n\s*([^\n]+)', modal_text, 'Raw_Store'))
        header_extras.update(extract_multiple_regex(r'Cashier\s*\n\s*([^\n]+)', modal_text, 'Raw_Cashier'))
        header_extras.update(extract_multiple_regex(r'POS #\s*\n\s*([^\n]+)', modal_text, 'Raw_POS'))
        header_extras.update(extract_multiple_regex(r'Receipt Number\s*\n\s*([^\n]+)', modal_text, 'Raw_Receipt_Number'))
        header_extras.update(extract_multiple_regex(r'Date\s*\n\s*([^\n]+)', modal_text, 'Raw_Date'))
        
        # Ensure we only grab actual hashes (length > 10) to prevent grabbing the literal word "Number"
        cc_matches = re.finditer(r'Credit Card Number[\s\n]+([A-Za-z0-9]{10,})', modal_text, re.IGNORECASE)
        cc_list = []
        for m in cc_matches:
            val = m.group(1).strip()
            if val and val not in cc_list:
                cc_list.append(val)
                
        if cc_list:
            if len(cc_list) == 1: header_extras['Credit_Card_Number'] = cc_list[0]
            else:
                for i, c in enumerate(cc_list): header_extras[f'Credit_Card_Number-{i+1}'] = c

        # VISUAL EVIDENCE: Header Block Screenshot
        try:
            header_container = dialog.locator("div").filter(has_text=re.compile(r"Store")).filter(has_text=re.compile(r"Receipt Type")).last
            if not await header_container.is_visible():
                header_container = dialog 
                
            for key, val in header_extras.items():
                if val and key.startswith("Raw_"):
                    clean_key = key.replace("Raw_", "")
                    
                    target_elem = header_container.get_by_text(val, exact=True).first
                    if not await target_elem.is_visible():
                        target_elem = header_container.get_by_text(val).first
                    
                    if await target_elem.is_visible():
                        img_path = os.path.join(evidence_dir, f"Header_{clean_key}.png")
                        await highlight_and_screenshot(page, target_elem, img_path, scroll_position="top")
                        evidence_paths[f"Header_{clean_key}"] = img_path
        except Exception as e:
            print(f"      ⚠️ Header screenshot failed: {e}")

        # 2. Extract EXACT Totals 
        strict_totals_match = re.search(r'Header Total Tax Value[\s\n]*\$?([\d\.\-]+)[\s\n]*Total Value[\s\n]*\$?([\d\.\-]+)', modal_text, re.IGNORECASE)
        if strict_totals_match:
            totals_extras['Total_Tax'] = strict_totals_match.group(1)
            totals_extras['Total_Value'] = strict_totals_match.group(2)

        # 3. Extract Tenders via Vertical Block Regex
        tender_matches = re.finditer(
            r'Tender Type[\s\n]+([A-Za-z0-9]+)[\s\n]+(?:Credit Card Number[\s\n]+([A-Za-z0-9\-]+)[\s\n]+)?Total Value[\s\n]+([\-\d\.]+)',
            modal_text, 
            re.IGNORECASE
        )
        for t_match in tender_matches:
            t_type = t_match.group(1).strip()
            t_card = t_match.group(2).strip() if t_match.group(2) else ""
            t_amount = t_match.group(3).strip()
            
            if t_card in ["--", "-"]:
                t_card = ""
                
            try:
                formatted_amount = f"${float(t_amount):.2f}"
            except:
                formatted_amount = t_amount
                
            tender_dict = {
                "Type": t_type,
                "Amount": formatted_amount,
                "Card_Number": t_card
            }
            if tender_dict not in scraped_tenders:
                scraped_tenders.append(tender_dict)
                print(f"      * Scraped Tender: {tender_dict['Type']} -> {tender_dict['Amount']}")

        # VISUAL EVIDENCE: Footers & Tenders Screenshots
        try:
            footer_container = dialog.locator("div").filter(has_text=re.compile(r"Total Value")).filter(has_text=re.compile(r"Totals")).last
            if not await footer_container.is_visible():
                footer_container = dialog
                
            for key, val in totals_extras.items():
                if val and val != "0.00":
                    target_elem = footer_container.get_by_text(val).first
                    if await target_elem.is_visible():
                        img_path = os.path.join(evidence_dir, f"Footer_{key}.png")
                        await highlight_and_screenshot(page, target_elem, img_path, scroll_position="bottom")
                        evidence_paths[f"Footer_{key}"] = img_path
                        
            for t in scraped_tenders:
                t_type = t["Type"]
                target_elem = footer_container.get_by_text(t_type).first
                if await target_elem.is_visible():
                    img_path = os.path.join(evidence_dir, f"Tender_{t_type}.png")
                    await highlight_and_screenshot(page, target_elem, img_path, scroll_position="bottom")
                    evidence_paths[f"Tender_{t_type}"] = img_path
        except Exception as e:
            print(f"      ⚠️ Footer screenshot failed: {e}")
            
        # Reset scroll to top to cleanly prepare the viewport for next actions
        try:
            if await dialog.is_visible():
                await dialog.evaluate("node => { node.scrollTop = 0; }")
                await page.wait_for_timeout(500)
        except:
            pass

    except Exception as e:
        print(f"      ⚠️ Error during Deep Scrape: {e}")

    target_dt = datetime.strptime(data["Date"], "%Y-%m-%d")
    formatted_date = target_dt.strftime("%m/%d/%Y")

    scraped_receipt_data = {
        "Header": {
            "Store": str(int(data["Store"])),
            "Receipt_Number": receipt_num,
            "Date": formatted_date,
            "Cashier": data["Cashier"],
            "POS": data["POS_Number"],
            **header_extras  
        },
        "Line_Items": scraped_line_items,
        "Tenders": scraped_tenders,
        "Totals": {
            **totals_extras  
        },
        "Raw_UI_Text": modal_text,
        "Evidence_Paths": evidence_paths 
    }
    
    # 9c. Download the PDF (WRAPPED IN ERROR HANDLING)
    print("   -> Downloading PDF...")
    try:
        async with page.expect_download(timeout=15000) as download_info:
            await page.get_by_role("button", name="Download as pdf").click(timeout=5000)
        
        download = await download_info.value
        file_path = os.path.join(DOWNLOAD_DIR, f"Receipt_{receipt_num}.pdf")
        
        await download.save_as(file_path)
        print(f"      * PDF successfully saved to: {file_path}")
    except Exception as pdf_err:
        print(f"      ⚠️ PDF Download skipped/failed: {pdf_err}")

    print(f"✅ Workflow complete for {receipt_num}\n")
    return True, scraped_receipt_data
