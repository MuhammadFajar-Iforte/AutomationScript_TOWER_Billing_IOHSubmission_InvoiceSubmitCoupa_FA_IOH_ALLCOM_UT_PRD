# library/whatsapp_utils.py
"""
WhatsApp Notification via Kafka Confluent Cloud.
"""

import logging
from datetime import datetime, timezone, timedelta

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger(__name__)

WIB = timezone(timedelta(hours=7))
BOT_NAME = "Submit Invoice Coupa Billing IOH"


class WhatsAppNotifier:
    """WhatsApp notification sender via Kafka Confluent Cloud."""
    
    # Hardcoded Kafka API URL
    KAFKA_URL = "https://pkc-oz2po.ap-southeast-3.aws.confluent.cloud:443/kafka/v3/clusters/lkc-d1qpnd/topics/t_ultrmsg_prd_out_sentwamsg_va2/records/"
    
    def __init__(
        self,
        kafka_auth: str,
        app_id: str,
        sent_to: str
    ):
        self.kafka_auth = kafka_auth
        self.app_id = app_id
        self.sent_to = sent_to
        
        # Parse username:password from Base64 auth string
        import base64
        try:
            decoded = base64.b64decode(kafka_auth).decode('utf-8')
            parts = decoded.split(':', 1)
            self._username = parts[0]
            self._password = parts[1] if len(parts) > 1 else ""
        except Exception:
            self._username = ""
            self._password = ""
    
    def _get_timestamp_wib(self) -> str:
        """Get current timestamp in WIB format."""
        now = datetime.now(WIB)
        return now.strftime("%d %b %Y %H:%M:%S WIB")
    
    def _get_datetime_for_api(self) -> str:
        """Get current timestamp for API payload."""
        now = datetime.now(WIB)
        return now.strftime("%Y-%m-%d %H:%M:%S")
    
    def send_message(self, message: str) -> dict | None:
        """Send message via Kafka Confluent Cloud REST API."""
        logger.info("Sending WhatsApp notification...")
        
        payload = {
            "key": {
                "type": "JSON",
                "data": "whatsapp_notification"
            },
            "value": {
                "type": "JSON",
                "data": {
                    "idapp": self.app_id,
                    "id": "0001",
                    "sent_to": self.sent_to,
                    "message": message,
                    "mentions": "+wamentions+",
                    "datetime": self._get_datetime_for_api()
                }
            }
        }
        
        headers = {
            "Content-Type": "application/json"
        }
        
        try:
            response = requests.post(
                self.KAFKA_URL,
                headers=headers,
                json=payload,
                auth=(self._username, self._password),
                timeout=30,
                verify=False
            )
            
            if response.status_code in [200, 201]:
                logger.info(f"WhatsApp notification sent (HTTP {response.status_code})")
                return {"status": "ok"}
            else:
                logger.error(f"Kafka API error: {response.status_code} - {response.text[:200]}")
                return None
                
        except Exception as e:
            logger.error(f"Failed to send WhatsApp: {e}")
            return None
    
    def send_start_notification(self, total_po: int = 0, total_to_process: int = 0, skipped: int = 0, companies: list = None) -> dict | None:
        """Send START notification with data count and companies."""
        timestamp = self._get_timestamp_wib()
        msg = f"*🟡 {BOT_NAME} - START*\n\n"
        msg += f"START - {timestamp}\n\n"
        msg += f"*Data Summary*\n"
        msg += f"Total PO: {total_po}\n"
        msg += f"To Process: {total_to_process}\n"
        msg += f"Skipped (Validation): {skipped}\n"
        
        if companies:
            unique_companies = sorted(set(companies))
            msg += f"\nCompanies: {', '.join(unique_companies)}"
        
        return self.send_message(msg)
    
    def send_captcha_warning(self, attempt: int = 1, max_attempt: int = 3) -> dict | None:
        """Send CAPTCHA warning notification."""
        msg = f"*⚠️ {BOT_NAME} - WARNING*\n\n"
        msg += f"Attempt {attempt}/{max_attempt}: Please log in to the server and complete the CAPTCHA."
        return self.send_message(msg)
    
    def send_captcha_timeout(self) -> dict | None:
        """Send CAPTCHA timeout error notification."""
        timestamp = self._get_timestamp_wib()
        msg = f"*🔴 {BOT_NAME} - ERROR*\n\n"
        msg += f"ERROR - {timestamp}\n\n"
        msg += f"Error: Failed to start process, Captcha coupa not completed"
        return self.send_message(msg)
    
    def send_error_notification(self, error_message: str) -> dict | None:
        """Send ERROR notification."""
        timestamp = self._get_timestamp_wib()
        msg = f"*🔴 {BOT_NAME} - ERROR*\n\n"
        msg += f"ERROR - {timestamp}\n\n"
        msg += f"Error: {error_message}"
        return self.send_message(msg)
    
    def send_end_notification(
        self,
        total: int = 0,
        success_count: int = 0,
        error_count: int = 0,
        attachment_not_found: int = 0,
        skipped_count: int = 0,
        report_url: str = ""
    ) -> dict | None:
        """Send END notification with summary."""
        timestamp = self._get_timestamp_wib()
        
        msg = f"*🟢 {BOT_NAME} - END*\n\n"
        msg += f"SUCCESS - {timestamp}\n\n"
        msg += f"*Summary*\n"
        msg += f"Data Processed: {total}\n"
        msg += f"Success: {success_count}\n\n"
        msg += f"*Not Processed*\n"
        msg += f"• Error: {error_count}\n"
        msg += f"• Attachment Not Found: {attachment_not_found}\n"
        msg += f"• Skipped (Validation): {skipped_count}"
        
        if report_url:
            msg += f"\n\nPath Report:\n{report_url}"
        
        return self.send_message(msg)
