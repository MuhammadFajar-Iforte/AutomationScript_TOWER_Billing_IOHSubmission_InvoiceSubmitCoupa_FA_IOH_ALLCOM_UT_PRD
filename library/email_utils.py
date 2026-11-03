"""
Email Utility Module

Handles sending email reports via SMTP (Outlook/Office 365).

Features:
- Send HTML email with summary report
- Support for multiple recipients (TO, CC)
- Include OneDrive link to output file
"""

import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import List, Optional, Dict, Any
from datetime import datetime

logger = logging.getLogger(__name__)


class EmailClient:
    """
    Client untuk mengirim email via SMTP.
    
    Supports Office 365 / Outlook SMTP.
    """
    
    # Office 365 SMTP settings
    SMTP_SERVER = "smtp.office365.com"
    SMTP_PORT = 587
    
    def __init__(
        self,
        sender_email: str,
        sender_password: str,
        smtp_server: str = None,
        smtp_port: int = None
    ):
        """
        Initialize EmailClient.
        
        Args:
            sender_email: Email address to send from
            sender_password: Email password or app password
            smtp_server: SMTP server (default: smtp.office365.com)
            smtp_port: SMTP port (default: 587)
        """
        self._sender_email = sender_email
        self._sender_password = sender_password
        self._smtp_server = smtp_server or self.SMTP_SERVER
        self._smtp_port = smtp_port or self.SMTP_PORT
        
        logger.debug(f"EmailClient initialized for {sender_email}")
    
    def send_report_email(
        self,
        to_recipients: List[str],
        cc_recipients: Optional[List[str]] = None,
        subject: str = "Coupa Invoice Automation Report",
        report_data: Dict[str, Any] = None,
        output_file_link: str = "",
        output_filename: str = ""
    ) -> bool:
        """
        Send email report with processing summary.
        
        Args:
            to_recipients: List of TO email addresses
            cc_recipients: List of CC email addresses (optional)
            subject: Email subject
            report_data: Dictionary containing report data
            output_file_link: OneDrive sharing link to output file
            output_filename: Name of the output file
        
        Returns:
            True if email sent successfully, False otherwise
        """
        if not to_recipients:
            logger.warning("No recipients specified, skipping email")
            return False
        
        logger.info(f"Sending report email to: {', '.join(to_recipients)}")
        
        try:
            # Create message
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = self._sender_email
            msg["To"] = ", ".join(to_recipients)
            
            if cc_recipients:
                msg["Cc"] = ", ".join(cc_recipients)
            
            # Build HTML content
            html_content = self._build_html_report(
                report_data=report_data,
                output_file_link=output_file_link,
                output_filename=output_filename
            )
            
            # Attach HTML content
            html_part = MIMEText(html_content, "html")
            msg.attach(html_part)
            
            # Send email
            all_recipients = to_recipients + (cc_recipients or [])
            
            with smtplib.SMTP(self._smtp_server, self._smtp_port) as server:
                server.starttls()
                server.login(self._sender_email, self._sender_password)
                server.sendmail(
                    self._sender_email,
                    all_recipients,
                    msg.as_string()
                )
            
            logger.info("Report email sent successfully")
            return True
            
        except smtplib.SMTPAuthenticationError as e:
            logger.error(f"Email authentication failed: {e}")
            return False
        except smtplib.SMTPException as e:
            logger.error(f"SMTP error: {e}")
            return False
        except Exception as e:
            logger.error(f"Failed to send email: {e}")
            return False
    
    def _build_html_report(
        self,
        report_data: Dict[str, Any],
        output_file_link: str,
        output_filename: str
    ) -> str:
        """
        Build HTML content for report email.
        
        Args:
            report_data: Dictionary containing report data
            output_file_link: OneDrive sharing link
            output_filename: Name of output file
        
        Returns:
            HTML string
        """
        if report_data is None:
            report_data = {}
        
        # Extract data
        total = report_data.get("total_transactions", 0)
        success = report_data.get("success_count", 0)
        business_exception = report_data.get("business_exception_count", 0)
        system_exception = report_data.get("system_exception_count", 0)
        start_time = report_data.get("start_time", "N/A")
        end_time = report_data.get("end_time", "N/A")
        duration = report_data.get("duration", "N/A")
        results = report_data.get("results", [])
        
        # Count attachment not found
        attachment_not_found = 0
        for r in results:
            remark = r.get("remark", "").lower()
            if "attachment" in remark and ("not found" in remark or "missing" in remark):
                attachment_not_found += 1
        
        # Build transaction details table rows
        transaction_rows = ""
        for i, r in enumerate(results, 1):
            status = r.get("status", "Unknown")
            
            transaction_rows += f"""
            <tr>
                <td style="padding: 8px; border: 1px solid #ddd;">{i}</td>
                <td style="padding: 8px; border: 1px solid #ddd;">{r.get('po_number', '-')}</td>
                <td style="padding: 8px; border: 1px solid #ddd;">{r.get('invoice_number', '-')}</td>
                <td style="padding: 8px; border: 1px solid #ddd;">{status}</td>
                <td style="padding: 8px; border: 1px solid #ddd;">{r.get('remark', '-')}</td>
            </tr>
            """
        
        # Build file link section
        file_link_section = ""
        if output_file_link:
            file_link_section = f"""
            <p><strong>Output File:</strong> <a href="{output_file_link}">{output_filename or 'Download'}</a></p>
            """
        
        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
        </head>
        <body style="font-family: Arial, sans-serif; font-size: 14px; color: #333;">
            <p>Dear Team,</p>
            
            <p>Berikut adalah laporan hasil proses Submit Invoice Coupa Billing IOH:</p>
            
            <p><strong>Execution Time</strong><br>
            Start: {start_time}<br>
            End: {end_time}<br>
            Duration: {duration}</p>
            
            <p><strong>Summary</strong><br>
            Data Processed: {total}<br>
            Success: {success}</p>
            
            <p><strong>Not Processed</strong><br>
            Error: {system_exception}<br>
            Attachment Not Found: {attachment_not_found}<br>
            Skipped (Validation): {business_exception - attachment_not_found if business_exception > attachment_not_found else 0}</p>
            
            {file_link_section}
            
            <p>Regards,<br>
            RPA Bot</p>
        </body>
        </html>
        """
        
        return html
