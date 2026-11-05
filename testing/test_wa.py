"""
Test: WhatsApp notification
Run: python test_wa.py
"""
import os, sys, logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(message)s", datefmt="%H:%M:%S", handlers=[logging.StreamHandler(sys.stdout)])
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from framework.models import BotContext
from framework import InitAllSettings

context = BotContext()
InitAllSettings.load_config_only(context)

if context.wa_notifier:
    context.wa_notifier.send_message("*Testing* - Waworking!")
else:
    print("WA notifier not initialized! Check WA_KAFKA_AUTH, WA_APP_ID, WA_SENT_TO in Config.xlsx")
