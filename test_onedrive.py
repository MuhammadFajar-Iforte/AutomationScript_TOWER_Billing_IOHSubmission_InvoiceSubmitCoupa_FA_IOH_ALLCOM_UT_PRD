"""
Test: OneDrive Attachment - List files and get sharing link

Run: python test_onedrive.py
"""

import os
import sys
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from framework.models import BotContext
from framework import InitAllSettings


def main():
    logger.info("=" * 60)
    logger.info("TEST: OneDrive Attachment")
    logger.info("=" * 60)
    
    context = BotContext()
    
    # Load config
    logger.info("Loading configuration...")
    InitAllSettings.load_config_only(context)
    
    if not context.onedrive_client:
        logger.error("OneDrive not configured! Check Config.xlsx (OneDrive_Client_ID, OneDrive_Client_Secret, OneDrive_Tenant_ID)")
        return
    
    logger.info("OneDrive connected!")
    
    # Get attachment path from config
    attachment_path = context.config.get("OneDrive_Attachment_Path", "RPA/Merge File Document Billing IOH/RPA/Output RPA")
    logger.info(f"\nAttachment base path: {attachment_path}")
    
    # List folders in attachment path
    logger.info(f"\nListing folders in: {attachment_path}")
    try:
        items = context.onedrive_client.list_files(attachment_path)
        
        logger.info(f"Found {len(items)} items:")
        for item in items[:20]:  # Show first 20
            item_type = "📁" if item.is_folder else "📄"
            logger.info(f"  {item_type} {item.name} (size: {item.size})")
        
        if len(items) > 20:
            logger.info(f"  ... and {len(items) - 20} more")
            
    except Exception as e:
        logger.error(f"Failed to list files: {e}")
        return
    
    # Ask for invoice number to check
    logger.info("\n" + "-" * 40)
    invoice_number = input("Enter Invoice Number to check (or press Enter to skip): ").strip()
    
    if invoice_number:
        folder_path = f"{attachment_path}/{invoice_number}"
        logger.info(f"\nChecking folder: {folder_path}")
        
        try:
            files = context.onedrive_client.list_files(folder_path)
            logger.info(f"Found {len(files)} files:")
            
            for item in files:
                if not item.is_folder:
                    logger.info(f"  📄 {item.name} (size: {item.size}, url: {item.web_url})")
                    
        except Exception as e:
            logger.error(f"Folder not found or error: {e}")
    
    # Get sharing link for output folder
    logger.info("\n" + "-" * 40)
    output_folder = context.config.get("OneDrive_Output_Folder", "RPA/Invoice Submit Coupa Billing IOH/RPA/Output")
    logger.info(f"\nOutput folder: {output_folder}")
    
    try:
        folder_id = context.onedrive_client.get_folder_id(output_folder)
        logger.info(f"Output folder ID: {folder_id}")
        
        # Try to get sharing link
        sharing_link = context.onedrive_client.get_sharing_link(folder_id)
        logger.info(f"Sharing link: {sharing_link}")
    except Exception as e:
        logger.error(f"Failed to get output folder info: {e}")


if __name__ == "__main__":
    main()
