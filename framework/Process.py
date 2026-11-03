"""
Process - Process a single transaction

State: PROCESS
- Login if company changed
- Search PO
- Create Invoice
- Fill form
- Upload attachments
- Calculate
- Save as Draft
- Validate amount
"""

import os
import time
import logging
from datetime import datetime
from typing import List, Dict, Any

import openpyxl

from framework.models import BusinessRuleException, SystemException

logger = logging.getLogger(__name__)


def execute(context):
    """
    Execute PROCESS state.
    
    Process the current transaction (10 steps):
    1. Login if company changed
    2. Search PO
    3. Create Invoice
    4. Count lines
    5. Validate attachments
    6. Fill invoice form
    7. Fill line quantities
    8. Fill VAT
    9. Calculate
    10. Save as Draft
    11. Validate amount
    12. Reload browser
    """
    transaction = context.transaction_item
    
    logger.info("=" * 60)
    logger.info(f"PROCESS: Transaction {context.transaction_number}/{len(context.transaction_data)}")
    logger.info(f"Company: {transaction.tower_provider_name}")
    logger.info(f"PO: {transaction.po_number}")
    logger.info(f"Invoice: {transaction.invoice_number}")
    logger.info("=" * 60)
    
    engine = context.web_driver
    remark = ""
    
    try:
        # Step 0: Login if company changed
        if transaction.tower_provider_name != context.current_logged_in_company:
            _login_for_company(context, transaction.tower_provider_name)
        else:
            logger.info(f"Reusing session for {transaction.tower_provider_name}")
        
        # Ensure we're in default content before starting
        try:
            engine._driver.switch_to.default_content()
        except:
            pass
        
        # For PTI only: select customer every loop (customer resets per navigation)
        if transaction.tower_provider_name.upper() == "PTI":
            logger.info("Selecting customer for PTI...")
            engine.select_customer("PT Indosat Tbk - PROFESIONAL TELEKOMUNIKASI")
        
        # Step 1: Search PO
        logger.info(f"Step 1: Searching PO {transaction.po_number}...")
        po_result = engine.search_po(transaction.po_number)
        
        if not po_result.found:
            error_msg = po_result.error_message or ""
            po_not_found_messages = ["nothing found", "not found", "po number not found", "no results"]
            is_po_not_found = any(msg in error_msg.lower() for msg in po_not_found_messages)
            
            if is_po_not_found or not error_msg:
                raise BusinessRuleException(f"PO Not Found: {transaction.po_number}", error_code="BRE-020")
            else:
                raise SystemException(f"PO Search Error: {error_msg}", error_code="SYS-020")
        
        logger.info("PO found")
        
        # Step 2: Create Invoice
        logger.info("Step 2: Creating invoice...")
        engine.click_create_invoice(transaction.po_number)
        engine.handle_invoicing_details_popup()
        logger.info("Invoice created")
        
        # Step 3: Count lines
        line_count = engine.count_invoice_lines()
        logger.info(f"Step 3: Invoice has {line_count} lines")
        
        # Step 4: Validate attachments
        logger.info("Step 4: Validating attachments...")
        scanned_invoice, attachments = _validate_attachments(context, transaction, line_count)
        
        # Step 5: Fill invoice form
        logger.info("Step 5: Filling invoice form...")
        _fill_invoice_form(context, transaction, scanned_invoice, attachments)
        logger.info("Form filled")
        
        # Step 6: Fill Line Quantities
        logger.info("Step 6: Filling line quantities...")
        _fill_line_quantities(context, transaction)
        
        # Step 7: Fill VAT
        logger.info("Step 7: Filling VAT...")
        engine.fill_vat(transaction.vat or "ID: M1")
        logger.info("VAT filled")
        
        # Step 8: Calculate
        logger.info("Step 8: Calculating...")
        engine.click_calculate()
        logger.info("Calculated")
        
        # Step 9: Validate amount (before save as draft - get Net Total from form)
        logger.info("Step 9: Validating amount...")
        expected_amount = transaction.invoice_amount or 0
        net_total = engine.get_net_total_from_form()
        
        if net_total is not None:
            difference = abs(float(net_total) - float(expected_amount))
            if difference <= 10.0:
                amount_remark = "Amount equals"
            else:
                amount_remark = f"Amount not equals, Coupa ({float(net_total):,.0f}) Excel ({float(expected_amount):,.0f})"
        else:
            amount_remark = "Could not read Net Total"
        logger.info(f"Amount validation: {amount_remark}")
        
        # Step 10: Save as Draft
        logger.info("Step 10: Saving as draft...")
        draft_id = engine.click_save_as_draft()
        logger.info(f"Saved as draft (ID: {draft_id})")
        
        # Step 11: Verify save - search invoice number in list
        logger.info("Step 11: Verifying save as draft...")
        engine.set_invoices_view_all()
        engine.search_invoice_in_list(transaction.invoice_number)
        
        # Wait up to 120s for invoice to appear
        found = engine.wait_for_invoice_in_list(transaction.invoice_number, max_wait=120, interval=5)
        
        if found:
            remark = f"Success save as draft - {amount_remark}"
        else:
            remark = f"Failed save as draft - Invoice not found in list. {amount_remark}"
        logger.info(f"Verify result: {remark}")
        
        # Step 12: Reload browser
        logger.info("Step 12: Reloading browser...")
        engine.reload_browser()
        logger.info("Browser reloaded")
        
        # Success - update Excel
        _update_excel_status(context, transaction, remark)
        context.report.success_count += 1
        
        # Success - update database status
        _update_database_status(context, transaction, f"Success: {remark}")
        
        logger.info(f"Transaction successful: {remark}")
        
    except BusinessRuleException as be:
        logger.error(f"Business Rule Exception: {be.message}")
        _update_excel_status(context, transaction, be.message)
        context.report.business_exception_count += 1
        
        # Update database status for business exception
        _update_database_status(context, transaction, f"Business Exception: {be.message}")
        
        # Reload browser after business exception
        try:
            engine.reload_browser()
            logger.info("Browser reloaded after business exception")
        except:
            pass
        
        raise be
        
    except SystemException as se:
        logger.error(f"System Exception: {se.message}")
        # Don't update Excel/DB here - will be handled by retry logic in main.py
        raise se
        
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        
        # Refresh browser on unexpected error
        try:
            engine.reload_browser()
            logger.info("Browser reloaded after unexpected error")
        except:
            pass
        
        raise SystemException(f"Unexpected error: {str(e)}", error_code="SYS-099")


def _login_for_company(context, company_code: str):
    """Login for a specific company."""
    logger.info(f"Logging in for company: {company_code}")
    
    creds = context.all_credentials.get(company_code)
    if not creds or not creds.username:
        raise BusinessRuleException(f"No credentials for company: {company_code}", error_code="BRE-030")
    
    password = creds.password or context.config.get("Default_Password", "")
    
    try:
        context.web_driver.ensure_logged_in_as(creds.username, password)
        context.current_logged_in_company = company_code
        logger.info(f"Logged in as {creds.username} ({company_code})")
        time.sleep(2)
    except Exception as e:
        raise SystemException(f"Login failed for {company_code}: {e}", error_code="SYS-030")


def _validate_attachments(context, transaction, line_count: int) -> tuple:
    """Validate and download attachments from OneDrive."""
    if context.onedrive_client:
        attachment_base_path = context.config.get("OneDrive_Attachment_Path", "")
        local_attachment_path = context.config.get("Local_Attachment_Path", "")
        
        if not attachment_base_path:
            raise BusinessRuleException("OneDrive_Attachment_Path not configured in Config.xlsx", error_code="BRE-040")
        
        attachment_result = context.onedrive_client.validate_and_download_attachments(
            invoice_number=transaction.invoice_number,
            status_site=transaction.status_site,
            onedrive_base_path=attachment_base_path,
            local_base_path=local_attachment_path,
            total_invoice_lines=line_count
        )
        
        if not attachment_result['success']:
            raise BusinessRuleException(
                f"Attachment Missing: {attachment_result['remark']}",
                error_code="BRE-040"
            )
        
        scanned_invoice = attachment_result.get('scanned_invoice', '')
        attachments = attachment_result.get('attachments', [])
        logger.info("Attachments validated")
        return scanned_invoice, attachments
    else:
        logger.warning("Skipping attachment validation (OneDrive not configured)")
        return '', []


def _fill_invoice_form(context, transaction, scanned_invoice: str, attachments: list):
    """Fill the invoice form."""
    invoice_date = datetime.now().strftime("%d/%m/%Y")
    invoice_description = f"Invoice for PO {transaction.po_number}"
    ba_number = transaction.ba_number or f"BA-{transaction.invoice_number}"
    
    # Tax Invoice Number - use as-is from database
    tax_invoice_number = transaction.tax_invoice_number or ""
    
    tax_invoice_date_raw = transaction.tax_invoice_date
    if tax_invoice_date_raw:
        if isinstance(tax_invoice_date_raw, datetime):
            tax_invoice_date = tax_invoice_date_raw.strftime("%m/%d/%Y")
        else:
            # Try to parse common date formats
            date_str = str(tax_invoice_date_raw).strip()
            tax_invoice_date = None
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%m/%d/%Y %H:%M:%S", "%m/%d/%Y", "%d/%m/%Y %H:%M:%S", "%d/%m/%Y"):
                try:
                    parsed = datetime.strptime(date_str[:19], fmt)
                    tax_invoice_date = parsed.strftime("%m/%d/%Y")
                    break
                except ValueError:
                    continue
            if not tax_invoice_date:
                # Fallback: take first 10 chars
                tax_invoice_date = date_str[:10]
    else:
        tax_invoice_date = datetime.now().strftime("%m/%d/%Y")
    
    wht_code = transaction.wht_code or "VA - Sewa tanah dan bangunan - 10%"
    
    context.web_driver.fill_invoice_form(
        invoice_number=transaction.invoice_number,
        invoice_date=invoice_date,
        invoice_description=invoice_description,
        currency="IDR",
        scanned_invoice_path=scanned_invoice if scanned_invoice else None,
        attachment_paths=attachments if attachments else None,
        ba_number=ba_number,
        tax_invoice_number=tax_invoice_number,
        tax_invoice_date=tax_invoice_date,
        wht_code=wht_code,
        po_number_field=transaction.po_number
    )


def _fill_line_quantities(context, transaction):
    """Fill line quantities based on site-qty mapping."""
    # Try to get site-qty mapping from context (SQL Server mode) or from Excel file
    site_qty_key = f"{transaction.po_number}_{transaction.invoice_number}"
    
    if context.site_qty_mapping and site_qty_key in context.site_qty_mapping:
        # SQL Server mode - use pre-loaded mapping from context
        site_qty_mapping = context.site_qty_mapping[site_qty_key]
    else:
        # Excel mode - load from file
        site_qty_mapping = _load_site_qty_mapping(
            transaction.source_file_path,
            transaction.po_number,
            transaction.invoice_number
        )
    
    if site_qty_mapping:
        fill_result = context.web_driver.fill_line_quantities(site_qty_mapping)
        logger.info(f"Lines filled: {fill_result['lines_filled']}, skipped: {fill_result['lines_skipped']}, zero: {fill_result['lines_zero']}")
    else:
        logger.warning("No site-qty mapping found, skipping fill line quantities")


def _load_site_qty_mapping(file_path: str, po_number: str, invoice_number: str) -> List[Dict[str, Any]]:
    """Load site-qty mapping from Excel file."""
    site_qty_mapping = []
    
    if not file_path or not os.path.exists(file_path):
        return site_qty_mapping
    
    try:
        wb = openpyxl.load_workbook(file_path, data_only=True)
        ws = wb.active
        
        po_number_str = str(po_number).strip()
        invoice_number_str = str(invoice_number).strip()
        
        # Column indices based on new template structure:
        # Col 2 = PO Number, Col 3 = Invoice Number
        # Col 13 = IOH Site ID Active, Col 14 = IOH Site ID, Col 15 = Qty
        for row_num in range(6, ws.max_row + 1):
            row_po = ws.cell(row=row_num, column=2).value
            row_inv = ws.cell(row=row_num, column=3).value
            
            row_po_str = str(row_po).strip() if row_po else ""
            row_inv_str = str(row_inv).strip() if row_inv else ""
            
            if row_po_str == po_number_str and row_inv_str == invoice_number_str:
                site_id_active = ws.cell(row=row_num, column=13).value  # IOH Site ID Active
                site_id = ws.cell(row=row_num, column=14).value  # IOH Site ID
                qty = ws.cell(row=row_num, column=15).value  # Qty
                
                if site_id or site_id_active:
                    site_qty_mapping.append({
                        "site_id": str(site_id).strip() if site_id else "",
                        "site_id_active": str(site_id_active).strip() if site_id_active else "",
                        "qty": qty if qty is not None else 0
                    })
        
        wb.close()
        
    except Exception as e:
        logger.error(f"Error loading site-qty mapping: {e}")
    
    return site_qty_mapping


def _update_excel_status(context, transaction, status: str):
    """Update Status RPA, Invoice Date, and Modified Date columns in Excel file.
    
    Updates both main row and duplicate rows with:
    - Invoice Date: today's date (same date used in Coupa form)
    - Status RPA: success/error message
    - Modified Date: timestamp when processed
    
    Template column structure:
    Col 17 = Invoice Date, Col 18 = Status RPA, Col 19 = Modified Date
    """
    file_path = transaction.source_file_path
    logger.info(f"Updating Excel status - file: {file_path}, row: {transaction.row_index}, status: {status[:50]}...")
    
    if not file_path:
        logger.warning("Skipping Excel status update (source_file_path is empty)")
        return
    
    if not os.path.exists(file_path):
        logger.warning(f"Skipping Excel status update (file not found: {file_path})")
        return
    
    # Fixed column indices based on Template File Input structure
    col_invoice_date = 17   # Column Q
    col_status_rpa = 18     # Column R
    col_modified_date = 19  # Column S
    
    try:
        wb = openpyxl.load_workbook(file_path)
        ws = wb.active
        
        # Invoice Date = today's date (same as what robot inputs to Coupa)
        invoice_date = datetime.now().strftime("%d/%m/%Y")
        modified_date = context.run_timestamp
        
        # Update main row
        ws.cell(row=transaction.row_index, column=col_invoice_date).value = invoice_date
        ws.cell(row=transaction.row_index, column=col_status_rpa).value = status
        ws.cell(row=transaction.row_index, column=col_modified_date).value = modified_date
        
        # Update duplicate rows with same status
        for dup_row in transaction.duplicate_rows:
            ws.cell(row=dup_row, column=col_invoice_date).value = invoice_date
            ws.cell(row=dup_row, column=col_status_rpa).value = status
            ws.cell(row=dup_row, column=col_modified_date).value = modified_date
        
        wb.save(file_path)
        wb.close()
        
        logger.info(f"Excel updated: row {transaction.row_index}")
            
    except Exception as e:
        logger.error(f"Failed to update Excel status: {e}")


def _update_database_status(context, transaction, status: str):
    """
    Update RPA_COUPA_STATUS and RPA_COUPA_DATE in database for all site IDs.
    
    Args:
        context: Bot context
        transaction: TransactionItem being processed
        status: Status message (Success, Business Exception, etc.)
    """
    from framework.GetTransactionData import update_database_status_bulk
    
    # Get site IDs from site_qty_mapping in context
    site_qty_key = f"{transaction.po_number}_{transaction.invoice_number}"
    
    site_ids = []
    if context.site_qty_mapping and site_qty_key in context.site_qty_mapping:
        for item in context.site_qty_mapping[site_qty_key]:
            site_id = item.get("site_id")
            site_id_active = item.get("site_id_active")
            if site_id:
                site_ids.append(site_id)
            if site_id_active and site_id_active != site_id:
                site_ids.append(site_id_active)
    
    # Fallback to transaction's ioh_site_id if no mapping found
    if not site_ids:
        if transaction.ioh_site_id:
            site_ids.append(transaction.ioh_site_id)
        if transaction.site_name_ioh and transaction.site_name_ioh != transaction.ioh_site_id:
            site_ids.append(transaction.site_name_ioh)
    
    if site_ids:
        update_database_status_bulk(
            po_number=transaction.po_number,
            invoice_number=transaction.invoice_number,
            site_ids=site_ids,
            status=status
        )
