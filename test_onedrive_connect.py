"""
Test: OneDrive connection from Config.xlsx
Run: python test_onedrive_connect.py
"""
import os, sys, logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(message)s", datefmt="%H:%M:%S", handlers=[logging.StreamHandler(sys.stdout)])
logger = logging.getLogger(__name__)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from framework.models import BotContext
from framework import InitAllSettings

context = BotContext()
InitAllSettings.load_config_only(context)

if context.onedrive_client:
    logger.info(f"✓ OneDrive connected successfully (User: {context.config.get('User_Principal_Name')})")
else:
    logger.error("✗ OneDrive connection FAILED - check Config.xlsx: ONEDRIVE_CLIENT_ID, ONEDRIVE_CLIENT_SECRET, ONEDRIVE_TENANT_ID, USER_PRINCIPAL_NAME")
