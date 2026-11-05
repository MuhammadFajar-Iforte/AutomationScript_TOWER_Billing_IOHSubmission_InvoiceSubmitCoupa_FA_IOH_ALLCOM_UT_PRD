"""
Framework Models - State, Context, and Exceptions

Defines the core data structures for the RE Framework pattern.
"""

from enum import Enum
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List, Dict, Any


class State(Enum):
    """State machine states for RE Framework."""
    INIT = "INIT"
    GET_TRANSACTION = "GET_TRANSACTION"
    PROCESS = "PROCESS"
    END_PROCESS = "END_PROCESS"


class BusinessRuleException(Exception):
    """
    Exception untuk error bisnis (Data tidak valid, dll). 
    Tidak di-retry, langsung lanjut ke transaksi berikutnya.
    """
    def __init__(self, message: str, error_code: str = "BRE-000"):
        super().__init__(f"[{error_code}] {message}")
        self.error_code = error_code
        self.message = message


class SystemException(Exception):
    """
    Exception untuk error sistem (Koneksi putus, web timeout, dll). 
    Akan di-retry sesuai konfigurasi.
    """
    def __init__(self, message: str, error_code: str = "SYS-000"):
        super().__init__(f"[{error_code}] {message}")
        self.error_code = error_code
        self.message = message


@dataclass
class CompanyCredentials:
    """Company credentials from master data."""
    company_code: str
    username: str
    password: Optional[str] = None


@dataclass
class TransactionItem:
    """Data for a single transaction."""
    row_index: int
    tower_provider_name: str
    po_number: str
    invoice_number: str
    invoice_line_number: str
    invoice_description: str
    status_site: str
    payment_period_start: Any  # Can be datetime or string
    ba_number: str
    tax_invoice_number: str
    tax_invoice_date: Any  # Can be datetime or string
    invoice_amount: float
    wht_code: str
    ioh_site_id: str
    site_name_ioh: str
    qty: str
    vat: str
    source_file_path: str
    duplicate_rows: List[int] = field(default_factory=list)
    
    @property
    def grouping_key(self) -> str:
        """Key for grouping transactions (Tower + PO + Invoice)."""
        return f"{self.tower_provider_name}_{self.po_number}_{self.invoice_number}"


@dataclass
class ReportData:
    """Data for summary report."""
    total_transactions: int = 0
    success_count: int = 0
    business_exception_count: int = 0
    system_exception_count: int = 0


class BotContext:
    """
    Menyimpan semua variabel global yang bisa diakses ke semua module.
    Equivalent to UiPath's in_Config dictionary.
    """
    def __init__(self):
        # State machine
        self.state = State.INIT
        
        # Configuration (loaded from Config.xlsx)
        self.config: Dict[str, Any] = {}
        self.is_config_loaded = False
        
        # Input source configuration
        # "database" (default) or "onedrive"
        self.input_source: str = "database"
        # Path to input file in OneDrive (for input_source = "onedrive")
        self.onedrive_input_file: Optional[str] = None
        # Actual OneDrive path the input file was downloaded from
        self.onedrive_input_source_path: Optional[str] = None
        
        # Transaction management
        self.transaction_number = 1
        self.transaction_item: Optional[TransactionItem] = None
        self.transaction_data: List[TransactionItem] = []
        
        # Retry management
        self.retry_number = 0
        self.max_retry_number = 3
        
        # Master data
        self.all_credentials: Dict[str, CompanyCredentials] = {}
        # Map alias company (uppercase) -> kode kanonik; alias pertama di file = kanonik
        self.company_aliases: Dict[str, str] = {}
        
        # Site-Qty mapping for SQL Server mode
        # Key: "po_number_invoice_number", Value: List[{"site_id": str, "qty": int}]
        self.site_qty_mapping: Dict[str, List[Dict[str, Any]]] = {}
        
        # Components (initialized in InitAllSettings)
        self.web_driver = None
        self.onedrive_client = None
        self.wa_notifier = None  # WhatsApp notifier
        self.current_logged_in_company: Optional[str] = None
        
        # Results tracking
        self.report = ReportData()
        self.results: List[Dict[str, Any]] = []
        
        # Data tracking
        self.total_po_from_db: int = 0  # Distinct PO count from database query
        self.skipped_count: int = 0  # Rows skipped due to validation failure
        self.skipped_transactions: List[Dict[str, Any]] = []  # Info for skipped rows to update DB
        
        # Timestamps
        self.start_time: Optional[datetime] = None
        self.run_timestamp: str = ""  # Format: ddMMyyyy_HHmmss
