"""
Test: Check OneDrive attachment files for a specific invoice number
Run: python test_attachment.py
"""
import os, sys, logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(message)s", datefmt="%H:%M:%S", handlers=[logging.StreamHandler(sys.stdout)])
logger = logging.getLogger(__name__)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from framework.models import BotContext
from framework import InitAllSettings


def main():
    context = BotContext()
    InitAllSettings.load_config_only(context)
    
    if not context.onedrive_client:
        logger.error("OneDrive not configured! Check Config.xlsx (ONEDRIVE_CLIENT_ID, ONEDRIVE_CLIENT_SECRET, ONEDRIVE_TENANT_ID, USER_PRINCIPAL_NAME)")
        return
    
    logger.info(f"OneDrive connected (User: {context.config.get('User_Principal_Name')})")
    
    attachment_path = context.config.get("OneDrive_Attachment_Path", "")
    if not attachment_path:
        logger.error("OneDrive_Attachment_Path not configured in Config.xlsx!")
        return
    logger.info(f"Base path: {attachment_path}")
    
    # Input invoice number
    invoice_number = input("\nMasukkan Invoice Number: ").strip()
    if not invoice_number:
        logger.error("Invoice Number kosong!")
        return
    
    # Input status site
    status_site = input("Masukkan Status Site (e.g. Recurring/One Time): ").strip()
    
    folder_path = f"{attachment_path}/{invoice_number}"
    logger.info(f"\nChecking folder: {folder_path}")
    
    # Check folder exists
    if not context.onedrive_client.folder_exists(folder_path):
        logger.error(f"Folder NOT FOUND: {folder_path}")
        return
    
    # List files
    files = context.onedrive_client.list_files(folder_path)
    logger.info(f"\nFiles found ({len(files)}):")
    for f in files:
        if not f.is_folder:
            logger.info(f"  📄 {f.name} ({f.size} bytes)")
    
    # Validate using same logic as production
    logger.info(f"\n--- Validating attachments (status_site='{status_site}', lines=10) ---")
    result = context.onedrive_client.validate_and_download_attachments(
        invoice_number=invoice_number,
        status_site=status_site,
        onedrive_base_path=attachment_path,
        local_base_path="RPA/Attachment",
        total_invoice_lines=10
    )
    
    logger.info(f"\nResult:")
    logger.info(f"  Success: {result['success']}")
    logger.info(f"  Remark: {result['remark']}")
    logger.info(f"  Matched files: {result['matched_files']}")
    logger.info(f"  Missing files: {result['missing_files']}")
    logger.info(f"  Scanned Invoice: {result['scanned_invoice']}")
    logger.info(f"  Attachments: {result['attachments']}")


if __name__ == "__main__":
    main()
