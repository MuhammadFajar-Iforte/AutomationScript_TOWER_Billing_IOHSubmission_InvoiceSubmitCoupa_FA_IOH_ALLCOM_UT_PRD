"""
Test: Coupa Login
Opens browser, logs in to Coupa for a given company, then stops (browser stays open).
Run: python test_login.py
"""
import os, sys, logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(message)s", datefmt="%H:%M:%S", handlers=[logging.StreamHandler(sys.stdout)])
logger = logging.getLogger(__name__)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from framework.models import BotContext
from framework import InitAllSettings


def main():
    logger.info("=" * 60)
    logger.info("TEST: Coupa Login")
    logger.info("=" * 60)
    
    context = BotContext()
    
    # Load config + credentials
    InitAllSettings.load_config_only(context)
    
    # Show available companies
    companies = list(context.all_credentials.keys())
    logger.info(f"Available companies: {companies}")
    
    # Input company
    company = input("\nEnter company code to test login (e.g. PTI): ").strip().upper()
    
    creds = context.all_credentials.get(company)
    if not creds or not creds.username:
        logger.error(f"No credentials for company: {company}")
        return
    
    password = creds.password or context.config.get("Default_Password", "")
    logger.info(f"Will login as: {creds.username}")
    
    # Open browser
    logger.info("\nOpening browser...")
    InitAllSettings.init_browser_only(context)
    engine = context.web_driver
    
    try:
        # Login
        logger.info(f"\nLogging in for {company}...")
        engine.ensure_logged_in_as(creds.username, password)
        logger.info("✓ LOGIN SUCCESSFUL!")
        
        # For PTI, test customer selection
        if company == "PTI":
            logger.info("\nSelecting customer for PTI...")
            result = engine.select_customer("PT Indosat Tbk - PROFESIONAL TELEKOMUNIKASI")
            logger.info(f"Customer selection result: {result}")
        
        logger.info("\n" + "=" * 60)
        logger.info("Login test complete. Press Enter to close browser...")
        logger.info("=" * 60)
        input()
        
    except Exception as e:
        logger.error(f"✗ LOGIN FAILED: {e}")
        import traceback
        traceback.print_exc()
        input("\nPress Enter to close browser...")
        
    finally:
        engine.close_browser()


if __name__ == "__main__":
    main()
