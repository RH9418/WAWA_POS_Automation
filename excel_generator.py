import json
import os
import glob
from datetime import datetime
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils.cell import column_index_from_string, get_column_letter
from openpyxl.drawing.image import Image

# --- MODERN ENTERPRISE STYLING DEFINITIONS ---
DARK_SLATE = "1F4E79"
STEEL_BLUE = "2F75B5"
LIGHT_GRAY = "F2F2F2"
PASS_BG = "E2EFDA"
PASS_TEXT = "375623"
FAIL_BG = "F8CECC"
FAIL_TEXT = "A61C00"

TITLE_FONT = Font(bold=True, color="FFFFFF", size=13)
HEADER_FONT = Font(bold=True, color="FFFFFF", size=11)
DATA_FONT = Font(size=11)
BOLD_DATA_FONT = Font(bold=True, size=11)
LINK_FONT = Font(color="0563C1", underline="single", size=11)

SECTION_FILL = PatternFill(start_color=DARK_SLATE, end_color=DARK_SLATE, fill_type="solid")
HEADER_FILL = PatternFill(start_color=STEEL_BLUE, end_color=STEEL_BLUE, fill_type="solid")
ALT_ROW_FILL = PatternFill(start_color=LIGHT_GRAY, end_color=LIGHT_GRAY, fill_type="solid")
PASS_FILL = PatternFill(start_color=PASS_BG, end_color=PASS_BG, fill_type="solid")
FAIL_FILL = PatternFill(start_color=FAIL_BG, end_color=FAIL_BG, fill_type="solid")

PASS_FONT = Font(color=PASS_TEXT, bold=True)
FAIL_FONT = Font(color=FAIL_TEXT, bold=True)

# Crisp, dark borders for the data grid and image backgrounds
THIN_BORDER = Border(left=Side(style='thin', color="000000"), 
                     right=Side(style='thin', color="000000"), 
                     top=Side(style='thin', color="000000"), 
                     bottom=Side(style='thin', color="000000"))

def write_section_title(ws, row_idx, title, start_col=1, end_col=10):
    """Draws a clean, dark slate section header."""
    ws.merge_cells(start_row=row_idx, start_column=start_col, end_row=row_idx, end_column=end_col)
    cell = ws.cell(row=row_idx, column=start_col, value=title)
    cell.fill = SECTION_FILL
    cell.font = TITLE_FONT
    cell.alignment = Alignment(horizontal="left", vertical="center")
    ws.row_dimensions[row_idx].height = 25

def write_table_headers(ws, row_idx, headers):
    """Draws column headers for a data table."""
    for col_idx, header in enumerate(headers, start=1):
        cell = ws.cell(row=row_idx, column=col_idx, value=header)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = THIN_BORDER
    ws.row_dimensions[row_idx].height = 22

def apply_data_row(ws, row_idx, data_array, failed_cols=None):
    """Writes a standard data row with crisp borders and failure highlighting."""
    if failed_cols is None: failed_cols = []
    
    for col_idx, val in enumerate(data_array, start=1):
        cell = ws.cell(row=row_idx, column=col_idx, value=val)
        
        # Base Styling
        cell.alignment = Alignment(vertical="center", wrap_text=True)
        cell.border = THIN_BORDER
        cell.font = BOLD_DATA_FONT if col_idx == 1 else DATA_FONT
        
        # Alternating background for XML rows to make them distinct
        if data_array[0] == "XML" and col_idx not in failed_cols:
            cell.fill = ALT_ROW_FILL
            
        # Failure Highlighting
        if col_idx in failed_cols:
            cell.fill = FAIL_FILL
            cell.font = Font(bold=True, color=FAIL_TEXT)
            
    ws.row_dimensions[row_idx].height = 20

def insert_floating_image(ws, col_letter, start_row, title, img_path, max_width=650):
    """
    Inserts an image, provides a native hyperlink to maximize it, and intelligently 
    draws cell borders behind the image area so the grid looks uniform.
    """
    if not img_path or not os.path.exists(img_path): return start_row
    
    try:
        col_idx = column_index_from_string(col_letter)
        
        # 1. Section Title & Maximize Link
        title_cell = ws.cell(row=start_row, column=col_idx)
        title_cell.value = f"{title} (Click to Maximize)"
        title_cell.font = Font(bold=True, color="0563C1", underline="single", size=11)
        title_cell.hyperlink = os.path.abspath(img_path)
        ws.row_dimensions[start_row].height = 20
        
        # 2. Process and Scale the Image
        img = Image(img_path)
        aspect_ratio = img.height / img.width
        img.width = max_width
        img.height = int(max_width * aspect_ratio)
        
        # 3. Floating Anchor (Starts one row below the title)
        img.anchor = f"{col_letter}{start_row + 1}"
        ws.add_image(img)
        
        # 4. Calculate Rows Spanned
        # Standard Excel row is ~15 points. Add buffer to prevent overlaps.
        rows_spanned = int(img.height / 15.0) + 2
        
        # Draw background borders behind the image area
        for r in range(start_row + 1, start_row + 1 + rows_spanned):
            bg_cell = ws.cell(row=r, column=col_idx)
            bg_cell.border = THIN_BORDER
            
        return start_row + rows_spanned + 1
    except Exception as e:
        print(f"⚠️ Could not load image {img_path}: {e}")
        return start_row

def get_latest_xml_dir(receipt_num):
    base_dir = os.path.abspath(os.getcwd())
    xml_ev_base = os.path.join(base_dir, "xml_visual_evidence")
    if not os.path.exists(xml_ev_base): return None
    dirs = glob.glob(os.path.join(xml_ev_base, f"Receipt_{receipt_num}_*"))
    if not dirs: return None
    return max(dirs, key=os.path.getmtime)

def generate_excel_report():
    print("\n" + "="*70)
    print("📊 PHASE 5: EXCEL REPORT GENERATION (DASHBOARD MODE)")
    print("="*70)

    base_dir = os.path.abspath(os.getcwd())
    json_path = os.path.join(base_dir, "comparison_results.json")
    ui_json_path = os.path.join(base_dir, "ui_scraped_results.json")
    
    if not os.path.exists(json_path):
        print(f"❌ '{json_path}' not found. Run comparator.py first.")
        return

    with open(json_path, "r") as f:
        results = json.load(f)

    ui_results = {}
    if os.path.exists(ui_json_path):
        with open(ui_json_path, "r") as f:
            ui_results = json.load(f)

    os.makedirs(os.path.join(base_dir, "test_evidence"), exist_ok=True)
    wb = openpyxl.Workbook()
    
    # -------------------------------------------------------------
    # MASTER SUMMARY TAB
    # -------------------------------------------------------------
    summary_ws = wb.active
    summary_ws.title = "Master Summary"
    summary_ws.sheet_view.showGridLines = False
    
    headers = ["Receipt Number", "Scenario", "Overall Status", "Mismatches Found"]
    summary_ws.append(headers)
    for col_idx in range(1, 5):
        cell = summary_ws.cell(row=1, column=col_idx)
        cell.fill = SECTION_FILL
        cell.font = TITLE_FONT
        cell.border = THIN_BORDER
        cell.alignment = Alignment(horizontal="center", vertical="center")
    summary_ws.row_dimensions[1].height = 30
        
    summary_ws.freeze_panes = "A2"
    for col, width in zip(["A", "B", "C", "D"], [22, 35, 20, 80]): 
        summary_ws.column_dimensions[col].width = width

    perfect_matches = 0
    failed_receipts = 0

    # -------------------------------------------------------------
    # INDIVIDUAL RECEIPT DASHBOARDS
    # -------------------------------------------------------------
    for r_num, data in results.items():
        scenario = data.get("Scenario", "Unknown")
        mismatches = data.get("Mismatches", [])
        
        # 1. Update Master Summary
        overall_status = "🟢 VERIFIED" if not mismatches else "🔴 MISMATCH"
        mismatch_str = "\n".join(mismatches) if mismatches else "None"
        
        if not mismatches: perfect_matches += 1
        else: failed_receipts += 1
        
        summary_ws.append([r_num, scenario, overall_status, mismatch_str])
        sum_row = summary_ws.max_row
        
        for col in range(1, 5):
            cell = summary_ws.cell(row=sum_row, column=col)
            cell.border = THIN_BORDER
            cell.alignment = Alignment(vertical="center", wrap_text=True)
        
        summary_ws.row_dimensions[sum_row].height = 40 if mismatches else 30
        
        if not mismatches:
            summary_ws.cell(row=sum_row, column=3).fill = PASS_FILL
            summary_ws.cell(row=sum_row, column=3).font = PASS_FONT
            summary_ws.cell(row=sum_row, column=3).alignment = Alignment(horizontal="center", vertical="center")
        else:
            summary_ws.cell(row=sum_row, column=3).fill = FAIL_FILL
            summary_ws.cell(row=sum_row, column=3).font = FAIL_FONT
            summary_ws.cell(row=sum_row, column=3).alignment = Alignment(horizontal="center", vertical="center")

        clean_scenario = "".join(c for c in scenario if c not in r'\/*?:[]').strip()
        sheet_title = f"{clean_scenario[:18]} - {r_num}"[:31]
        
        summary_ws.cell(row=sum_row, column=1).hyperlink = f"#'{sheet_title}'!A1"
        summary_ws.cell(row=sum_row, column=1).font = LINK_FONT

        # 2. Build Individual Sheet
        ws = wb.create_sheet(title=sheet_title)
        ws.sheet_view.showGridLines = False 
        
        # Freeze Panes: Lock Columns A and B for side-scrolling (C7 is after metadata)
        ws.freeze_panes = "C7"

        # Metadata Links
        ws.cell(row=1, column=1, value="Scenario:").font = BOLD_DATA_FONT
        ws.cell(row=1, column=2, value=scenario).font = DATA_FONT
        
        pdf_path = data.get("PDF_Path", "")
        if os.path.exists(pdf_path):
            ws.cell(row=2, column=1, value="Source PDF:").font = BOLD_DATA_FONT
            ws.cell(row=2, column=2, value="Open PDF").hyperlink = os.path.abspath(pdf_path)
            ws.cell(row=2, column=2).font = LINK_FONT

        # Restored JSON Links
        ui_json_link = data.get("UI_JSON_Path", "")
        xml_json_link = data.get("XML_JSON_Path", "")
        
        ws.cell(row=3, column=1, value="UI JSON:").font = BOLD_DATA_FONT
        if ui_json_link:
            ws.cell(row=3, column=2, value="Open UI Scrape").hyperlink = os.path.abspath(ui_json_link)
            ws.cell(row=3, column=2).font = LINK_FONT
            
        ws.cell(row=4, column=1, value="XML JSON:").font = BOLD_DATA_FONT
        if xml_json_link:
            ws.cell(row=4, column=2, value="Open XML Extract").hyperlink = os.path.abspath(xml_json_link)
            ws.cell(row=4, column=2).font = LINK_FONT

        ws.append([""]) # Spacer (Makes max_row = 5)
        ws.append([""]) # Spacer (Makes max_row = 6)

        # Standard Column Widths (A-J Data, L-O UI Image, Q-T XML Image)
        col_widths = {"A": 22, "B": 15, "C": 55, "D": 14, "E": 14, "F": 12, "G": 12, "H": 22, "I": 18, "J": 15, "L": 3, "M": 95, "P": 3, "Q": 95}
        for col, width in col_widths.items(): 
            ws.column_dimensions[col].width = width

        # Setup Image Data
        ui_data = ui_results.get(str(r_num), {})
        ui_evidence = ui_data.get("Evidence_Paths", {})
        xml_ev_dir = get_latest_xml_dir(r_num)
        
        xml_mapping = {}
        if xml_ev_dir:
            map_path = os.path.join(xml_ev_dir, "mapping.json")
            if os.path.exists(map_path):
                with open(map_path, "r") as map_f:
                    xml_mapping = json.load(map_f)

        # Cursors
        table_row = 7
        ui_img_row = 7
        xml_img_row = 7

        # --- HEADER BLOCK ---
        hb = data.get("Header_Block", {})
        if hb and hb.get("Headers"):
            write_section_title(ws, table_row, "📋 HEADER COMPARISON", start_col=1, end_col=9)
            table_row += 1
            write_table_headers(ws, table_row, hb["Headers"])
            table_row += 1
            
            apply_data_row(ws, table_row, hb.get("UI_Row", []), hb.get("Failed_Cols", []))
            table_row += 1
            apply_data_row(ws, table_row, hb.get("XML_Row", []), hb.get("Failed_Cols", []))
            table_row += 2
            
            # Images
            ui_hdr_img = next((v for k, v in ui_evidence.items() if k.startswith("Header_")), None)
            xml_hdr_img = os.path.join(xml_ev_dir, xml_mapping.get("Header_Image", "")) if xml_ev_dir else None
            
            ui_img_row = insert_floating_image(ws, "M", ui_img_row, "📸 UI EVIDENCE (HEADER)", ui_hdr_img, max_width=800)
            xml_img_row = insert_floating_image(ws, "Q", xml_img_row, "💾 XML EVIDENCE (HEADER)", xml_hdr_img, max_width=700)
            
            sync_row = max(table_row, ui_img_row, xml_img_row)
            # Add exact number of blank rows to catch up max_row safely
            for _ in range(sync_row - ws.max_row): ws.append([""])
            ws.append([""]) # spacer
            table_row = ui_img_row = xml_img_row = ws.max_row + 1

        # --- TENDERS BLOCK ---
        tb_list = data.get("Tenders_Block", [])
        if tb_list:
            write_section_title(ws, table_row, "💳 SPLIT TENDERS", start_col=1, end_col=4)
            table_row += 1
            write_table_headers(ws, table_row, tb_list[0]["Headers"])
            table_row += 1
            
            for t_pair in tb_list:
                apply_data_row(ws, table_row, t_pair.get("UI_Row", []), t_pair.get("Failed_Cols", []))
                table_row += 1
                apply_data_row(ws, table_row, t_pair.get("XML_Row", []), t_pair.get("Failed_Cols", []))
                table_row += 2 
                
            ui_ftr_img = next((v for k, v in ui_evidence.items() if k.startswith("Footer_") or k.startswith("Tender_")), None)
            xml_ftr_img = os.path.join(xml_ev_dir, xml_mapping.get("Footer_Image", "")) if xml_ev_dir else None
            
            ui_img_row = insert_floating_image(ws, "M", ui_img_row, "📸 UI EVIDENCE (FOOTER/TENDERS)", ui_ftr_img, max_width=800)
            xml_img_row = insert_floating_image(ws, "Q", xml_img_row, "💾 XML EVIDENCE (FOOTER/TENDERS)", xml_ftr_img, max_width=700)
            
            sync_row = max(table_row, ui_img_row, xml_img_row)
            for _ in range(sync_row - ws.max_row): ws.append([""])
            ws.append([""])
            table_row = ui_img_row = xml_img_row = ws.max_row + 1

        # --- AGGREGATE BLOCK ---
        ab = data.get("Aggregate_Block", {})
        if ab and ab.get("Headers"):
            write_section_title(ws, table_row, "📊 AGGREGATES", start_col=1, end_col=4)
            table_row += 1
            write_table_headers(ws, table_row, ab["Headers"])
            table_row += 1
            
            apply_data_row(ws, table_row, ab.get("UI_Row", []), ab.get("Failed_Cols", []))
            table_row += 1
            apply_data_row(ws, table_row, ab.get("XML_Row", []), ab.get("Failed_Cols", []))
            table_row += 2
            
            if not tb_list: 
                ui_ftr_img = next((v for k, v in ui_evidence.items() if k.startswith("Footer_")), None)
                xml_ftr_img = os.path.join(xml_ev_dir, xml_mapping.get("Footer_Image", "")) if xml_ev_dir else None
                ui_img_row = insert_floating_image(ws, "M", ui_img_row, "📸 UI EVIDENCE (FOOTER)", ui_ftr_img, max_width=800)
                xml_img_row = insert_floating_image(ws, "Q", xml_img_row, "💾 XML EVIDENCE (FOOTER)", xml_ftr_img, max_width=700)
            
            sync_row = max(table_row, ui_img_row, xml_img_row)
            for _ in range(sync_row - ws.max_row): ws.append([""])
            ws.append([""])
            table_row = ui_img_row = xml_img_row = ws.max_row + 1

        # --- LINE ITEMS BLOCK ---
        lib_list = data.get("Line_Items_Block", [])
        if lib_list:
            line_cols = lib_list[0].get("Headers", [])
            write_section_title(ws, table_row, "🛒 LINE ITEM DETAILS", start_col=1, end_col=len(line_cols))
            table_row += 1
            write_table_headers(ws, table_row, line_cols)
            table_row += 1
            
            for line_pair in lib_list:
                ui_row = line_pair.get("UI_Row", [])
                xml_row = line_pair.get("XML_Row", [])
                
                apply_data_row(ws, table_row, ui_row, line_pair.get("Failed_Cols", []))
                table_row += 1
                apply_data_row(ws, table_row, xml_row, line_pair.get("Failed_Cols", []))
                table_row += 2 
                
                ui_ln = ui_row[1] if len(ui_row) > 1 else "--"
                xml_ln = xml_row[1] if len(xml_row) > 1 else "--"
                
                ui_img_path = None
                for item in ui_data.get("Line_Items", []):
                    if str(item.get("Line Number")) == str(ui_ln):
                        ui_img_path = item.get("Screenshot")
                        break
                        
                xml_img_name = xml_mapping.get("Line_Items_Mapping", {}).get(str(xml_ln))
                xml_img_path = os.path.join(xml_ev_dir, xml_img_name) if xml_img_name and xml_ev_dir else None
                
                if ui_img_path and ui_img_path not in [c.hyperlink.target for c in ws['M'] if c.hyperlink]:
                    ui_img_row = insert_floating_image(ws, "M", ui_img_row, f"📸 UI EVIDENCE (Line {ui_ln})", ui_img_path, max_width=800)
                
                if xml_img_path and xml_img_path not in [c.hyperlink.target for c in ws['Q'] if c.hyperlink]:
                    xml_img_row = insert_floating_image(ws, "Q", xml_img_row, f"💾 XML EVIDENCE (Line {xml_ln})", xml_img_path, max_width=700)
                
                sync_row = max(table_row, ui_img_row, xml_img_row)
                for _ in range(sync_row - ws.max_row): ws.append([""])
                ws.append([""]) # spacer before next pair
                table_row = ui_img_row = xml_img_row = ws.max_row + 1

    if len(wb.sheetnames) > 1 and "Sheet" in wb.sheetnames:
        wb.remove(wb["Sheet"])
        
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    excel_file = os.path.join(base_dir, "test_evidence", f"Reconciliation_Report_{timestamp}.xlsx")
    
    try:
        wb.save(excel_file)
        print(f"✅ Dashboard Excel Report successfully generated: {excel_file}")
    except Exception as e:
        print(f"⚠️ ERROR: Could not save Excel report: {e}")

if __name__ == "__main__":
    generate_excel_report()
