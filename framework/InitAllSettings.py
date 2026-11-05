"""
InitAllSettings - Initialize all settings and applications

State: INIT
- Load configuration from Config.xlsx
- Load master data (company credentials, headers)
- Initialize browser
- Navigate to Coupa Portal
"""

import os
import logging
from datetime import datetime

import openpyxl

from framework.models import CompanyCredentials, BusinessRuleException

logger = logging.getLogger(__name__)


def execute(context):
    """
    Execute INIT state (full initialization including browser).
    
    1. Load configuration from Config.xlsx
    2. Load master data headers
    3. Load company credentials
    4. Initialize OneDrive
    5. Initialize browser
    6. Navigate to Coupa Portal
    """
    load_config_only(context)
    init_browser_only(context)


def load_config_only(context):
    """
    Load configuration and master data WITHOUT opening browser.
    Call this before loading transactions to validate data first.
    """
    # Set run timestamp for consistent modified date in Excel
    context.run_timestamp = datetime.now().strftime("%d%m%Y_%H%M%S")
    context.start_time = datetime.now()
    
    # --------------------------------------
    # 1. LOAD CONFIGURATION
    # --------------------------------------
    if not context.is_config_loaded:
        _load_configuration(context)
        context.is_config_loaded = True
    
    # --------------------------------------
    # 2. LOAD COMPANY CREDENTIALS
    # --------------------------------------
    _load_company_credentials(context)
    
    # --------------------------------------
    # 3. INITIALIZE ONEDRIVE (if configured)
    # --------------------------------------
    _init_onedrive(context)
    
    # --------------------------------------
    # 4. INITIALIZE WHATSAPP NOTIFIER (if configured)
    # --------------------------------------
    _init_whatsapp(context)


def init_browser_only(context):
    """
    Initialize browser and navigate to Coupa.
    Call this AFTER validating transactions.
    """
    _init_browser(context)


def _load_configuration(context):
    """Load configuration from Data/Config.xlsx (Settings + Constants sheets)"""
    config_file = "Data/Config.xlsx"
    
    if not os.path.exists(config_file):
        raise BusinessRuleException(
            f"Config file not found: {config_file}",
            error_code="BRE-000"
        )
    
    # Mapping from common env-style names to code config keys
    # This allows Config.xlsx to use either format (e.g., DB_SERVER or DB_Server)
    name_mapping = {
        "db_server": "DB_Server",
        "db_database": "DB_Database",
        "db_username": "DB_Username",
        "db_password": "DB_Password",
        "db_query_file": "DB_Query_File",
        "db_input_query": "DB_Input_Query",
        "coupa_url": "Coupa_URL",
        "coupa_password": "Default_Password",
        "max_retry_number": "MaxRetryNumber",
        "onedrive_client_id": "OneDrive_Client_ID",
        "onedrive_client_secret": "OneDrive_Client_Secret",
        "onedrive_tenant_id": "OneDrive_Tenant_ID",
        "onedrive_attachment_path": "OneDrive_Attachment_Path",
        "onedrive_output_folder": "OneDrive_Output_Folder",
        "local_input_file": "Local_Input_File",
        "local_attachment_path": "Local_Attachment_Path",
        "output_folder": "Output_Folder",
        "mapping process": "Mapping Process",
        "master_data_file": "Master_Data_File",
        "email_enabled": "Email_Enabled",
        "email_sender": "Email_Sender",
        "email_password": "Email_Password",
        "email_to": "Email_To",
        "email_cc": "Email_Cc",
        "default_vat": "Default_VAT",
        "wa_kafka_auth": "WA_Kafka_Auth",
        "wa_app_id": "WA_App_ID",
        "wa_sent_to": "WA_Sent_To",
        "user_principal_name": "User_Principal_Name",
    }
    
    try:
        wb = openpyxl.load_workbook(config_file, data_only=True)
        
        # Read Settings sheet (main configuration)
        if "Settings" in wb.sheetnames:
            ws = wb["Settings"]
            for row in ws.iter_rows(min_row=2, values_only=True):
                if row[0] and not str(row[0]).startswith("#"):
                    name = str(row[0]).strip()
                    value = str(row[1]).strip() if row[1] is not None else ""
                    # Normalize name to code format
                    config_key = name_mapping.get(name.lower(), name)
                    context.config[config_key] = value
        
        # Read Constants sheet (application settings)
        if "Constants" in wb.sheetnames:
            ws = wb["Constants"]
            for row in ws.iter_rows(min_row=2, values_only=True):
                if row[0] and not str(row[0]).startswith("#"):
                    name = str(row[0]).strip()
                    value = str(row[1]).strip() if row[1] is not None else ""
                    # Normalize name to code format
                    config_key = name_mapping.get(name.lower(), name)
                    context.config[config_key] = value
        
        wb.close()
        logger.info(f"Config loaded from {config_file} ({len(context.config)} settings)")
        
    except BusinessRuleException:
        raise
    except Exception as e:
        raise BusinessRuleException(
            f"Error loading Config.xlsx: {e}",
            error_code="BRE-000"
        )
    
    # Set defaults
    context.config.setdefault("Coupa_URL", "https://supplier.coupahost.com/orders")
    context.config.setdefault("MaxRetryNumber", 3)
    context.config.setdefault("MaxConsecutiveSystemExceptions", 3)
    context.config.setdefault("Output_Folder", "RPA/Output")
    context.config.setdefault("Local_Attachment_Path", "RPA/Attachment")
    
    # Set max retry from config
    context.max_retry_number = int(context.config.get("MaxRetryNumber", 3))
    
    # Log summary
    logger.info(f"Input: SQL Server ({context.config.get('DB_Server')}/{context.config.get('DB_Database')})")


def _load_company_credentials(context):
    """Load company credentials from master data Excel file."""
    master_data_file = context.config.get("Master_Data_File", "Data/Account Coupa IOH.xlsx")
    
    if not os.path.exists(master_data_file):
        raise BusinessRuleException(
            f"Master data file not found: {master_data_file}",
            error_code="BRE-004"
        )
    
    try:
        wb = openpyxl.load_workbook(master_data_file, data_only=True)
        ws = wb.active
        
        for row_num in range(2, ws.max_row + 1):
            company = ws.cell(row=row_num, column=1).value
            if company:
                # Multi alias dengan separator ';' — alias pertama = kode kanonik
                aliases = [a.strip().upper() for a in str(company).split(";") if a.strip()]
                if not aliases:
                    continue
                
                canonical_code = aliases[0]
                username = ws.cell(row=row_num, column=2).value
                password = ws.cell(row=row_num, column=3).value
                
                credentials = CompanyCredentials(
                    company_code=canonical_code,
                    username=str(username).strip() if username else None,
                    password=str(password).strip() if password else None
                )
                
                # Semua alias menunjuk objek kredensial yang sama
                for alias in aliases:
                    context.all_credentials[alias] = credentials
                    context.company_aliases[alias] = canonical_code
        
        wb.close()
        
        if not context.all_credentials:
            raise BusinessRuleException(
                "No credentials found in master data",
                error_code="BRE-005"
            )
        
        canonical_companies = sorted(set(context.company_aliases.values()))
        logger.info(f"Companies: {', '.join(canonical_companies)}")
        
    except BusinessRuleException:
        raise
    except Exception as e:
        raise BusinessRuleException(
            f"Error loading company credentials: {e}",
            error_code="BRE-006"
        )


def _init_onedrive(context):
    """Initialize OneDrive client for attachments."""
    client_id = context.config.get("OneDrive_Client_ID")
    client_secret = context.config.get("OneDrive_Client_Secret")
    tenant_id = context.config.get("OneDrive_Tenant_ID")
    
    if client_id and client_secret and tenant_id:
        try:
            from library.onedrive_utils import OneDriveClient
            
            user_principal = context.config.get("User_Principal_Name", "")
            if not user_principal:
                logger.warning("User_Principal_Name not configured in Config.xlsx, OneDrive disabled")
                return
            
            context.onedrive_client = OneDriveClient(
                client_id=client_id,
                client_secret=client_secret,
                tenant_id=tenant_id,
                user_principal_name=user_principal
            )
            context.onedrive_client.authenticate()
            logger.info(f"OneDrive connected ({user_principal})")
        except Exception as e:
            logger.warning(f"OneDrive failed: {e}")


def _init_browser(context):
    """Initialize browser and navigate to Coupa."""
    from library.web_utils import WebAutomationEngine
    
    coupa_url = context.config.get("Coupa_URL", "https://supplier.coupahost.com/orders")
    
    context.web_driver = WebAutomationEngine(
        coupa_url=coupa_url,
        screenshot_dir="screenshots",
        headless=False,
        fast_mode=True
    )
    
    # Set WhatsApp notifier for captcha notifications
    if context.wa_notifier:
        context.web_driver.set_wa_notifier(context.wa_notifier)
    
    context.web_driver.open_browser()
    
    context.web_driver.navigate_to_coupa()
    logger.info("Navigated to Coupa")


def _init_whatsapp(context):
    """Initialize WhatsApp notifier."""
    kafka_auth = context.config.get("WA_Kafka_Auth")
    app_id = context.config.get("WA_App_ID")
    sent_to = context.config.get("WA_Sent_To")
    
    if not all([kafka_auth, app_id, sent_to]):
        logger.warning("WhatsApp missing configuration, disabling")
        context.wa_notifier = None
        return
    
    try:
        from library.whatsapp_utils import WhatsAppNotifier
        
        context.wa_notifier = WhatsAppNotifier(
            kafka_auth=kafka_auth,
            app_id=app_id,
            sent_to=sent_to
        )
        logger.info("WhatsApp notifier initialized")
    except Exception as e:
        logger.warning(f"WhatsApp notifier failed: {e}")
        context.wa_notifier = None
