"""
CloseAllApplications - Cleanup and close all applications

State: END_PROCESS
- Save output file
- Upload output file to OneDrive
- Send email report with OneDrive link
- Close browser
- Print summary
"""

import os
import shutil
import logging
from datetime import datetime

from library.email_utils import EmailClient

logger = logging.getLogger(__name__)


def execute(context):
    """
    Execute END_PROCESS state.
    
    1. Save output file to output folder
    2. Upload output file to OneDrive
    3. Send email report with OneDrive link
    4. Close browser
    5. Print processing summary
    """
    logger.info("")
    logger.info("=" * 60)
    logger.info("END_PROCESS: Cleaning up...")
    logger.info("=" * 60)
    
    # Save output file
    output_path = _save_output_file(context)
    
    # Upload to OneDrive and get sharing link
    sharing_link = ""
    output_filename = ""
    if output_path:
        output_filename = os.path.basename(output_path)
        sharing_link = _upload_to_onedrive(context, output_path)
    
    # Send email report
    _send_email_report(context, sharing_link, output_filename)
    
    # Send WhatsApp END notification
    _send_wa_end_notification(context, sharing_link)
    
    # Close browser
    _close_browser(context)
    
    # Print summary
    _print_summary(context)
    
    # Shutdown logging
    logging.shutdown()


def _save_output_file(context):
    """
    Save processed Excel file to output folder.
    
    Returns:
        str: Path to saved output file, or empty string if failed
    """
    input_file = context.config.get("Local_Input_File", "")
    output_folder = context.config.get("Output_Folder", "RPA/Output")
    
    if not input_file or not output_folder:
        logger.warning("Input file or output folder not configured, skipping save")
        return ""
    
    if not os.path.exists(input_file):
        logger.warning(f"Input file not found: {input_file}")
        return ""
    
    try:
        # Create output folder if not exists
        os.makedirs(output_folder, exist_ok=True)
        
        # Generate output filename: SubmitInvoiceCoupa_ddMMyyyy_HHmmss.xlsx
        output_filename = f"SubmitInvoiceCoupa_{context.run_timestamp}.xlsx"
        output_path = os.path.join(output_folder, output_filename)
        
        # Copy file to output folder
        shutil.copy2(input_file, output_path)
        
        logger.info(f"Output file saved: {output_path}")
        return output_path
        
    except Exception as e:
        logger.error(f"Failed to save output file: {e}")
        return ""


def _upload_to_onedrive(context, local_output_path: str) -> str:
    """
    Upload output file to OneDrive and get sharing link.
    
    Args:
        context: Bot context
        local_output_path: Path to local output file
    
    Returns:
        str: Sharing link URL, or empty string if failed
    """
    onedrive_output_folder = context.config.get("OneDrive_Output_Folder", "")
    
    if not onedrive_output_folder:
        logger.warning("OneDrive output folder not configured, skipping upload")
        return ""
    
    if not context.onedrive_client:
        logger.warning("OneDrive client not initialized, skipping upload")
        return ""
    
    if not os.path.exists(local_output_path):
        logger.warning(f"Output file not found: {local_output_path}")
        return ""
    
    try:
        logger.info(f"Uploading output file to OneDrive: {onedrive_output_folder}")
        
        # Upload file
        uploaded_item = context.onedrive_client.upload_file(
            local_path=local_output_path,
            dest_folder_path=onedrive_output_folder
        )
        
        logger.info(f"File uploaded: {uploaded_item.name}")
        
        # Generate sharing link
        sharing_link = context.onedrive_client.get_sharing_link(
            item_id=uploaded_item.id,
            link_type="view"
        )
        
        logger.info(f"Sharing link created: {sharing_link[:60]}...")
        return sharing_link
        
    except Exception as e:
        logger.error(f"Failed to upload to OneDrive: {e}")
        return ""


def _send_email_report(context, output_file_link: str, output_filename: str):
    """
    Send email report with processing summary.
    
    Args:
        context: Bot context
        output_file_link: OneDrive sharing link to output file
        output_filename: Name of the output file
    """
    # Get email configuration
    email_enabled = str(context.config.get("Email_Enabled", "false")).lower() == "true"
    
    if not email_enabled:
        logger.info("Email report disabled, skipping")
        return
    
    sender_email = context.config.get("Email_Sender", "")
    sender_password = context.config.get("Email_Password", "")
    to_recipients = context.config.get("Email_To", "")
    cc_recipients = context.config.get("Email_Cc", "")
    
    if not sender_email or not sender_password:
        logger.warning("Email credentials not configured, skipping email")
        return
    
    if not to_recipients:
        logger.warning("No email recipients configured, skipping email")
        return
    
    # Parse recipients (comma-separated)
    to_list = [e.strip() for e in to_recipients.split(",") if e.strip()]
    cc_list = [e.strip() for e in cc_recipients.split(",") if e.strip()] if cc_recipients else []
    
    try:
        # Build report data
        end_time = datetime.now()
        duration_str = "N/A"
        if context.start_time:
            duration = end_time - context.start_time
            duration_str = str(duration).split('.')[0]
        
        report_data = {
            "total_transactions": context.report.total_transactions,
            "success_count": context.report.success_count,
            "business_exception_count": context.report.business_exception_count,
            "system_exception_count": context.report.system_exception_count,
            "start_time": context.start_time.strftime('%Y-%m-%d %H:%M:%S') if context.start_time else 'N/A',
            "end_time": end_time.strftime('%Y-%m-%d %H:%M:%S'),
            "duration": duration_str,
            "results": context.results
        }
        
        # Create email client and send
        email_client = EmailClient(
            sender_email=sender_email,
            sender_password=sender_password
        )
        
        subject = f"Coupa Invoice Automation Report - {datetime.now().strftime('%d/%m/%Y %H:%M')}"
        
        success = email_client.send_report_email(
            to_recipients=to_list,
            cc_recipients=cc_list,
            subject=subject,
            report_data=report_data,
            output_file_link=output_file_link,
            output_filename=output_filename
        )
        
        if success:
            logger.info("Email report sent successfully")
        else:
            logger.warning("Failed to send email report")
            
    except Exception as e:
        logger.error(f"Error sending email report: {e}")


def _close_browser(context):
    """Close browser if open."""
    if context.web_driver:
        try:
            context.web_driver.close_browser()
        except Exception as e:
            logger.warning(f"Error closing browser: {e}")


def _send_wa_end_notification(context, report_url: str = ""):
    """Send WhatsApp END notification with summary."""
    if not context.wa_notifier:
        return
    
    try:
        # Count attachment not found from business exceptions
        attachment_not_found = 0
        for r in context.results:
            remark = r.get("remark", "").lower()
            if "attachment" in remark and ("not found" in remark or "missing" in remark):
                attachment_not_found += 1
        
        context.wa_notifier.send_end_notification(
            total=context.report.total_transactions,
            success_count=context.report.success_count,
            error_count=context.report.system_exception_count,
            attachment_not_found=attachment_not_found,
            skipped_count=context.skipped_count,
            report_url=report_url
        )
    except Exception as e:
        logger.warning(f"Failed to send WhatsApp end notification: {e}")


def _print_summary(context):
    """Print processing summary."""
    logger.info("")
    logger.info("=" * 60)
    logger.info("PROCESSING SUMMARY")
    logger.info("=" * 60)
    
    # Calculate duration
    end_time = datetime.now()
    if context.start_time:
        duration = end_time - context.start_time
        duration_str = str(duration).split('.')[0]
    else:
        duration_str = "N/A"
    
    start_str = context.start_time.strftime('%Y-%m-%d %H:%M:%S') if context.start_time else 'N/A'
    end_str = end_time.strftime('%Y-%m-%d %H:%M:%S')
    
    logger.info(f"Start Time: {start_str}")
    logger.info(f"End Time: {end_str}")
    logger.info(f"Duration: {duration_str}")
    logger.info("")
    
    # Transaction counts
    logger.info(f"Total Transactions: {context.report.total_transactions}")
    logger.info(f"  - Success: {context.report.success_count}")
    logger.info(f"  - Business Exception: {context.report.business_exception_count}")
    logger.info(f"  - System Exception: {context.report.system_exception_count}")
    
    # Per-transaction details
    if context.results:
        logger.info("")
        logger.info("Transaction Details:")
        for i, r in enumerate(context.results, 1):
            status_icon = "[OK]" if r.get("status") == "Success" else "[FAIL]"
            logger.info(f"  {i}. {status_icon} PO={r.get('po_number')}, Invoice={r.get('invoice_number')}")
            logger.info(f"       Status: {r.get('status')}, Remark: {r.get('remark')}")
    
    logger.info("")
    logger.info("=" * 60)
    logger.info("Process completed.")
    logger.info("=" * 60)
