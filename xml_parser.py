import xml.etree.ElementTree as ET
import json
import os

def strip_ns(tag):
    """Safely strips XML namespaces from tags to make parsing reliable."""
    return tag.split('}')[-1] if '}' in tag else tag

def extract_receipt_filters(xml_file_path, target_receipt_number):
    """
    Parses a NAXML POSJournal file, hunts for the specific target_receipt_number,
    and extracts fields required for UI filtering.
    """
    try:
        tree = ET.parse(xml_file_path)
        root = tree.getroot()
        
        # Strip namespaces from all elements for reliable XPath searching
        for elem in root.iter():
            elem.tag = strip_ns(elem.tag)
            
    except ET.ParseError as e:
        print(f"❌ Error parsing XML {xml_file_path}: {e}")
        return None
    except Exception as e:
        print(f"❌ Error accessing file {xml_file_path}: {e}")
        return None

    # 1. Extract Global Store ID (located in the header, applies to all events)
    store_id = ""
    store_elem = root.find('.//StoreLocationID')
    if store_elem is not None and store_elem.text:
        store_id = store_elem.text.strip()

    # 2. Iterate through all events to find the target transaction
    events = root.findall('.//SaleEvent') + root.findall('.//RefundEvent') + root.findall('.//FinancialEvent')
    
    for event in events:
        receipt_num = event.findtext('.//TransactionID') or event.findtext('.//DropNumber')
        
        # 3. If we find the target receipt, extract its specific routing data
        if receipt_num and receipt_num.strip() == str(target_receipt_number).strip():
            date_raw = event.findtext('.//BusinessDate') or event.findtext('.//EventStartDate') or ""
            from_time = event.findtext('.//EventStartTime') or ""
            to_time = event.findtext('.//EventEndTime') or ""
            cashier = event.findtext('.//OverridingCashierID') or event.findtext('.//CashierID') or ""
            pos = event.findtext('.//RegisterID') or ""
            
            ui_filters = {
                "Date": date_raw.strip(),
                "FromTime": from_time.strip(),
                "ToTime": to_time.strip(),
                "Store": store_id,
                "Cashier": cashier.strip(),
                "POS_Number": pos.strip(),
                "Receipt_Number": receipt_num.strip()
            }
            
            return ui_filters

    # If the loop finishes and we didn't return, the receipt isn't in this file
    return None

if __name__ == "__main__":
    print("=== TARGETED NAXML UI FILTER EXTRACTOR ===")
    
    raw_path = input("Drag and drop the XML file here and press Enter:\n> ")
    xml_file = raw_path.strip().strip("'").strip('"')
    
    target_num = input("Enter the EXACT Receipt Number to search for:\n> ")

    if not os.path.exists(xml_file):
        print(f"\n❌ Error: Could not find file at '{xml_file}'")
    else:
        print(f"\nHunting for Receipt {target_num} in '{os.path.basename(xml_file)}'...")
        extracted_data = extract_receipt_filters(xml_file, target_num)
        
        if extracted_data:
            output_filename = f"Receipt_{target_num}_filters.json"
            with open(output_filename, 'w') as outfile:
                json.dump(extracted_data, outfile, indent=4)
            print(f"✅ Success! Target found. Filters extracted and saved to: {output_filename}")
        else:
            print(f"⚠️ Receipt {target_num} was NOT FOUND in this XML file.")
