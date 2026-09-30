import xml.etree.ElementTree as ET
import json
import os
from datetime import datetime

def strip_ns(tag):
    """Safely strips XML namespaces from tags (e.g., {http://...}SaleEvent -> SaleEvent)"""
    return tag.split('}')[-1] if '}' in tag else tag

def extract_xml_data(xml_path):
    print(f"📄 Parsing XML Data for: {xml_path}...")
    
    tree = ET.parse(xml_path)
    root = tree.getroot()
    
    # Strip namespaces from all elements to make querying reliable
    for elem in root.iter():
        elem.tag = strip_ns(elem.tag)

    # 1. Extract Global Store ID
    store_id = ""
    store_elem = root.find('.//StoreLocationID')
    if store_elem is not None and store_elem.text:
        store_id = store_elem.text[-4:] # Extract last 4 digits (e.g. 8330)

    receipts_data = {}

    # 2. Iterate through all Sale and Refund events
    for event in root.findall('.//SaleEvent') + root.findall('.//RefundEvent'):
        receipt_num = event.findtext('.//TransactionID')
        if not receipt_num:
            continue

        # --- HEADER ---
        date_raw = event.findtext('.//BusinessDate') or event.findtext('.//BeginDate')
        date_formatted = ""
        if date_raw:
            try:
                # Convert YYYY-MM-DD to MM/DD/YYYY to match PDF format
                dt = datetime.strptime(date_raw[:10], "%Y-%m-%d")
                date_formatted = dt.strftime("%m/%d/%Y")
            except Exception:
                date_formatted = date_raw

        cashier = event.findtext('.//CashierID') or ""
        pos = event.findtext('.//RegisterID') or ""

        # --- TOTALS ---
        total_val = event.findtext('.//TransactionTotalNetAmount') or event.findtext('.//TransactionGrossAmount') or "0.00"

        # --- LINE ITEMS ---
        line_items = []
        for t_line in event.findall('.//TransactionLine'):
            line_num = t_line.findtext('.//TransactionLineSequenceNumber') or ""
            
            # --- Handle Main Item Lines ---
            item_line = t_line.find('.//ItemLine')
            if item_line is not None:
                desc = item_line.findtext('Description') or ""
                code = item_line.findtext('.//POSCode') or item_line.findtext('.//ItemCode') or ""
                price = item_line.findtext('ActualSalesPrice') or item_line.findtext('RegularSalesUnitPrice') or "0.00"
                qty = item_line.findtext('SalesQuantity') or item_line.findtext('Quantity') or "0"
                val = item_line.findtext('SalesAmount') or item_line.findtext('ExtendedAmount') or "0.00"
                
                # Format to match PDF (e.g. $6.39)
                price_fmt = f"${float(price):.2f}" if price else "$0.00"
                val_fmt = f"${float(val):.2f}" if val else "$0.00"
                
                # Combine Description and Code
                name = f"{desc} ({code})" if code else desc
                
                line_items.append({
                    "Line Number": line_num,
                    "Item Name": name,
                    "Line Type": "Sale",
                    "Item Price": price_fmt,
                    "Quantity": qty,
                    "Value": val_fmt
                })
                
                # --- Handle SubItem Lines nested under the main item ---
                for sub_line in item_line.findall('.//SubItemLine'):
                    sub_num = sub_line.findtext('.//SubItemLineSequenceNumber') or ""
                    
                    comp = sub_line.find('.//ComponentItem')
                    s_desc = ""
                    s_code = ""
                    if comp is not None:
                        s_desc = comp.findtext('Description') or ""
                        s_code = comp.findtext('.//POSCode') or ""
                    
                    s_price = sub_line.findtext('.//ActualSalesPrice') or "0.00"
                    s_qty = sub_line.findtext('.//SalesQuantity') or "0"
                    s_val = sub_line.findtext('.//SalesAmount') or "0.00"
                    
                    s_price_fmt = f"${float(s_price):.2f}" if s_price else "$0.00"
                    s_val_fmt = f"${float(s_val):.2f}" if s_val else "$0.00"
                    s_name = f"{s_desc} ({s_code})" if s_code else s_desc
                    
                    line_items.append({
                        "Line Number": sub_num, 
                        "Item Name": s_name,
                        "Line Type": "SubItemLine",
                        "Item Price": s_price_fmt,
                        "Quantity": s_qty,
                        "Value": s_val_fmt
                    })

        # Save this receipt's data into the master dictionary
        receipts_data[receipt_num] = {
            "Header": {
                "Store": store_id,
                "Receipt_Number": receipt_num,
                "Date": date_formatted,
                "Cashier": cashier,
                "POS": pos
            },
            "Line_Items": line_items,
            "Totals": {
                "Total_Value": total_val
            }
        }

    return receipts_data

if __name__ == "__main__":
    # Point this to the XML file you uploaded
    xml_file = "NAXML-POSJournal_2026-08-15_2026-08-15T18.15.09.423_1of1.SS00480.XML    " 
    
    if not os.path.exists(xml_file):
        print(f"❌ Cannot find {xml_file}. Ensure it is in the same directory.")
    else:
        extracted_data = extract_xml_data(xml_file)
        print(f"\n✅ EXTRACTION SUCCESSFUL. FOUND {len(extracted_data)} RECEIPTS.")
        
        # Dynamically grab the first receipt to show its structure
        if extracted_data:
            first_receipt_key = list(extracted_data.keys())[0]
            print(f"\n--- RAW XML DATA FOR RECEIPT {first_receipt_key} ---")
            print(json.dumps(extracted_data[first_receipt_key], indent=4))
