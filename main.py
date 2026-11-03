"""
Coupa Invoice Automation - Main Entry Point

This automation processes invoices in Coupa Portal:
1. Load configuration and master data
2. Get transaction data from Excel or SQL Server
3. Process each transaction (login, search PO, create invoice, etc.)
4. Generate report and send email
"""

import os
import sys
import logging
from datetime import datetime

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from framework.models import BotContext, SystemException, BusinessRuleException
from framework import InitAllSettings, GetTransactionData, Process, CloseAllApplications


def setup_logging():
    """Setup logging configuration."""
    log_folder = "logs"
    os.makedirs(log_folder, exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    log_file = os.path.join(log_folder, f"coupa_automation_{timestamp}.txt")
    
    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%H:%M:%S",
        handlers=[
            logging.FileHandler(log_file, encoding='utf-8'),
            logging.StreamHandler(sys.stdout)
        ]
    )
    
    # Suppress noisy loggers
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("selenium").setLevel(logging.WARNING)
    
    return log_file


def _update_db_status_system_exception(context, transaction, error_message: str):
    """Update database status for system exception (after all retries exhausted)."""
    try:
        # Get site IDs from site_qty_mapping
        site_qty_key = f"{transaction.po_number}_{transaction.invoice_number}"
        
        site_ids = []
        if context.site_qty_mapping and site_qty_key in context.site_qty_mapping:
            for item in context.site_qty_mapping[site_qty_key]:
                site_id = item.get("site_id")
                if site_id:
                    site_ids.append(site_id)
        
        # Fallback to transaction's ioh_site_id
        if not site_ids:
            if transaction.ioh_site_id:
                site_ids.append(transaction.ioh_site_id)
        
        if site_ids:
            GetTransactionData.update_database_status_bulk(
                po_number=transaction.po_number,
                invoice_number=transaction.invoice_number,
                site_ids=site_ids,
                status=f"System Exception: {error_message}"
            )
    except Exception as e:
        logging.getLogger(__name__).warning(f"Failed to update DB status for system exception: {e}")


def main():
    """Main execution function."""
    log_file = setup_logging()
    logger = logging.getLogger(__name__)
    
    # Print start time
    start_time = datetime.now()
    logger.info("=" * 60)
    logger.info("COUPA INVOICE AUTOMATION")
    logger.info("=" * 60)
    logger.info(f"Start Time: {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"Log File: {log_file}")
    logger.info("")
    
    context = BotContext()
    
    try:
        # STEP 1: Load configuration (without opening browser)
        logger.info("[1/5] Loading configuration...")
        InitAllSettings.load_config_only(context)
        logger.info("Configuration loaded")
        logger.info("")
        
        # STEP 2: Load and validate transactions FIRST (before opening browser)
        logger.info("[2/5] Loading and validating transactions...")
        context.transaction_data = GetTransactionData.execute(context)
        context.report.total_transactions = len(context.transaction_data)
        
        # Initialize database connection for status updates (SQL Server mode only)
        GetTransactionData.init_database_connection(context)
        
        # Update skipped transactions to database (after connection is ready)
        GetTransactionData.update_skipped_transactions_to_db(context)
        
        if not context.transaction_data:
            logger.warning("No valid transactions to process - ending")
            return
        
        logger.info(f"Valid transactions to process: {len(context.transaction_data)}")
        logger.info("")
        
        # Send WhatsApp START notification (after validation, before browser)
        if context.wa_notifier:
            # Get unique companies from transactions
            companies = [tx.tower_provider_name for tx in context.transaction_data if tx.tower_provider_name]
            context.wa_notifier.send_start_notification(
                total_po=context.total_po_from_db,
                total_to_process=len(context.transaction_data),
                skipped=context.skipped_count,
                companies=companies
            )
        
        # STEP 3: Initialize browser (only if there are valid transactions)
        logger.info("[3/5] Opening browser...")
        InitAllSettings.init_browser_only(context)
        logger.info("Browser ready")
        logger.info("")
        
        # STEP 4: Process each transaction
        logger.info("[4/5] Processing transactions...")
        logger.info("")
        
        for idx, transaction in enumerate(context.transaction_data, 1):
            context.transaction_number = idx
            context.transaction_item = transaction
            context.retry_number = 0
            
            while context.retry_number < context.max_retry_number:
                try:
                    Process.execute(context)
                    
                    # Success - record result
                    context.results.append({
                        "po_number": transaction.po_number,
                        "invoice_number": transaction.invoice_number,
                        "status": "Success",
                        "remark": "Invoice submitted successfully"
                    })
                    break
                    
                except BusinessRuleException as be:
                    # Business exception - no retry
                    context.results.append({
                        "po_number": transaction.po_number,
                        "invoice_number": transaction.invoice_number,
                        "status": "Business Exception",
                        "remark": be.message
                    })
                    break
                    
                except SystemException as se:
                    context.retry_number += 1
                    
                    if context.retry_number >= context.max_retry_number:
                        # Max retries reached
                        context.report.system_exception_count += 1
                        context.results.append({
                            "po_number": transaction.po_number,
                            "invoice_number": transaction.invoice_number,
                            "status": "System Exception",
                            "remark": f"{se.message} (after {context.max_retry_number} retries)"
                        })
                        
                        # Update database status for system exception (after all retries exhausted)
                        _update_db_status_system_exception(context, transaction, se.message)
                    else:
                        logger.warning(f"Retry {context.retry_number}/{context.max_retry_number}: {se.message}")
                        
                except Exception as e:
                    # Unexpected error - treat as system exception
                    context.retry_number += 1
                    
                    if context.retry_number >= context.max_retry_number:
                        context.report.system_exception_count += 1
                        context.results.append({
                            "po_number": transaction.po_number,
                            "invoice_number": transaction.invoice_number,
                            "status": "System Exception",
                            "remark": f"Unexpected: {str(e)}"
                        })
                        
                        # Update database status for unexpected error (after all retries exhausted)
                        _update_db_status_system_exception(context, transaction, f"Unexpected: {str(e)}")
                    else:
                        logger.warning(f"Retry {context.retry_number}/{context.max_retry_number}: {str(e)}")
        
        logger.info("")
        logger.info("Transaction processing complete")
        
    except BusinessRuleException as be:
        logger.error(f"Business Rule Exception: {be.message}")
        
    except SystemException as se:
        logger.error(f"System Exception: {se.message}")
        
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        
    finally:
        # STEP 5: Cleanup and report
        logger.info("")
        logger.info("[5/5] Finalizing...")
        
        # Close database connection
        GetTransactionData.close_database_connection()
        
        CloseAllApplications.execute(context)


if __name__ == "__main__":
    main()
