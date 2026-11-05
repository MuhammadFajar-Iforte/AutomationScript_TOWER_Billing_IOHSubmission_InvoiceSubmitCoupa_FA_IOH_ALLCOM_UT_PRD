"""
GetTransactionData - Retrieve transaction data for processing

State: GET_TRANSACTION_DATA
- Load transaction data from Excel file or SQL Server
- Validate required fields
- Return transactions to process
"""

import os
import time
import logging
import urllib.parse
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple

import openpyxl
import pyodbc
import pandas as pd
import requests

from framework.models import TransactionItem, BusinessRuleException

logger = logging.getLogger(__name__)

# WHT Rate to Code mapping (loaded from Master Data.xlsx)
_wht_mapping = {}

# Database connection for status updates
_db_scope = None


def _load_wht_mapping(master_data_file: str) -> Dict[float, str]:
    """
    Load WHT rate to code mapping from Master Data.xlsx sheet 'WHT'.
    
    Returns:
        Dictionary mapping rate (float) to code (str)
        e.g., {0.0: 'QV', 0.02: 'KH', 0.1: 'VA'}
    """
    global _wht_mapping
    
    if _wht_mapping:
        return _wht_mapping
    
    if not os.path.exists(master_data_file):
        logger.warning(f"Master data file not found: {master_data_file}")
        return {}
    
    try:
        wb = openpyxl.load_workbook(master_data_file, data_only=True)
        
        if "WHT" not in wb.sheetnames:
            logger.warning("WHT sheet not found in master data file")
            wb.close()
            return {}
        
        ws = wb["WHT"]
        
        # Read WHT mapping (skip header row 1)
        # Column A = Rate, Column B = Code
        for row_num in range(2, ws.max_row + 1):
            rate = ws.cell(row=row_num, column=1).value
            code = ws.cell(row=row_num, column=2).value
            
            if rate is not None and code:
                # Convert rate to float for consistent lookup
                _wht_mapping[float(rate)] = str(code).strip()
        
        wb.close()
        logger.info(f"WHT mapping loaded: {_wht_mapping}")
        
    except Exception as e:
        logger.warning(f"Error loading WHT mapping: {e}")
    
    return _wht_mapping


def _get_wht_code(rate_value, wht_mapping: Dict[float, str]) -> Optional[str]:
    """
    Convert WHT rate to code using mapping.
    
    Args:
        rate_value: WHT rate from database (can be float, int, str, or percentage like '10%')
        wht_mapping: Dictionary mapping rate to code
        
    Returns:
        WHT code or None if not found
    """
    if rate_value is None or pd.isna(rate_value):
        return None
    
    try:
        # Handle percentage string like "10%" or "0.1"
        if isinstance(rate_value, str):
            rate_value = rate_value.strip().replace('%', '')
            rate_value = float(rate_value)
            # If value > 1, assume it's percentage (e.g., 10 means 10%)
            if rate_value > 1:
                rate_value = rate_value / 100
        else:
            rate_value = float(rate_value)
            # If value > 1, assume it's percentage
            if rate_value > 1:
                rate_value = rate_value / 100
        
        # Round to handle floating point precision
        rate_value = round(rate_value, 4)
        
        # Lookup in mapping
        return wht_mapping.get(rate_value)
        
    except (ValueError, TypeError):
        return None


def execute(context) -> List[TransactionItem]:
    """
    Execute GET_TRANSACTION_DATA state.
    
    Routes to the appropriate data source based on context.input_source:
    - "database" (default): Load from SQL Server, create input file, and validate
    - "onedrive": Download input Excel file from OneDrive and validate
    
    Returns:
        List of TransactionItem to process
    """
    input_source = (getattr(context, "input_source", "database") or "database").lower()
    
    if input_source == "onedrive":
        return _load_from_onedrive(context)
    
    return _load_from_database(context)


def _resolve_column_candidates(value) -> List[str]:
    """
    Pecah value mapping kolom ber-separator ';' menjadi daftar kandidat.
    
    Contoh: "ENTITY;NAME" -> ["ENTITY", "NAME"]
    Robot memakai kandidat pertama yang kolomnya ketemu.
    """
    if value is None or pd.isna(value):
        return []
    return [part.strip() for part in str(value).split(";") if part.strip()]


def _parse_required_flag(value, key_name: str, column: str) -> bool:
    """Parse nilai kolom Required DB/Excel. Hanya menerima 'yes'/'no' (case-insensitive)."""
    if value is None or not str(value).strip():
        raise BusinessRuleException(
            f"Lengkapi file mapping: kolom '{column}' untuk key '{key_name}' kosong (isi yes/no)",
            error_code="BRE-MAP-002"
        )
    flag = str(value).strip().lower()
    if flag not in ("yes", "no"):
        raise BusinessRuleException(
            f"Lengkapi file mapping: kolom '{column}' untuk key '{key_name}' "
            f"berisi '{value}' (harus yes/no)",
            error_code="BRE-MAP-002"
        )
    return flag == "yes"


def _load_key_field_mapping(master_data_file: str) -> Dict[str, Dict[str, Any]]:
    """
    Baca sheet 'Mapping Field' dari Master Data.xlsx.
    
    Struktur sheet: baris 1 = header (Key Name, Database, Required DB,
    Excel One Drive, Required Excel, Description), baris 2 ke bawah = mapping per key.
    Kolom Database dan Excel One Drive mendukung multi kandidat dipisah ';'.
    
    Returns:
        {key_name: {"database": [kandidat], "excel_one_drive": [kandidat],
                    "required_db": bool, "required_excel": bool}}
    
    Raises:
        BusinessRuleException: jika file/sheet tidak ada, kosong, atau
                               kolom required tidak berisi yes/no (tanpa fallback)
    """
    if not os.path.exists(master_data_file):
        raise BusinessRuleException(
            f"Lengkapi file mapping: master data file tidak ditemukan: {master_data_file}",
            error_code="BRE-MAP-001"
        )
    
    wb = openpyxl.load_workbook(master_data_file, data_only=True)
    
    if "Mapping Field" not in wb.sheetnames:
        wb.close()
        raise BusinessRuleException(
            f"Lengkapi file mapping: sheet 'Mapping Field' tidak ditemukan di {master_data_file}",
            error_code="BRE-MAP-001"
        )
    
    ws = wb["Mapping Field"]
    mapping = {}
    
    for row in ws.iter_rows(min_row=2, values_only=True):
        key_name = row[0] if len(row) > 0 else None
        if key_name is None or not str(key_name).strip():
            continue
        key_name = str(key_name).strip()
        mapping[key_name] = {
            "database": _resolve_column_candidates(row[1] if len(row) > 1 else None),
            "excel_one_drive": _resolve_column_candidates(row[3] if len(row) > 3 else None),
            "required_db": _parse_required_flag(
                row[2] if len(row) > 2 else None, key_name, "Required DB"),
            "required_excel": _parse_required_flag(
                row[4] if len(row) > 4 else None, key_name, "Required Excel")
        }
    
    wb.close()
    
    if not mapping:
        raise BusinessRuleException(
            f"Lengkapi file mapping: sheet 'Mapping Field' di {master_data_file} kosong",
            error_code="BRE-MAP-001"
        )
    
    logger.info(f"Mapping Field loaded: {len(mapping)} keys")
    return mapping


def _download_onedrive_input_file(client, upn: str, onedrive_path: str, local_path: str) -> str:
    """
    Download file input dari drive OneDrive user tertentu (app-level auth).
    
    Retry 3x dengan interval 5 detik, konsisten dengan perilaku library.
    
    Returns:
        Path lokal file yang tersimpan
    """
    encoded = urllib.parse.quote(onedrive_path.strip("/"))
    url = f"{client.GRAPH_API_BASE}/users/{upn}/drive/root:/{encoded}:/content"
    headers = client._get_headers()
    
    last_error = None
    for attempt in range(1, 4):
        try:
            response = requests.get(url, headers=headers, timeout=120, stream=True)
            if response.status_code == 200:
                os.makedirs(os.path.dirname(local_path), exist_ok=True)
                with open(local_path, "wb") as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        f.write(chunk)
                return local_path
            last_error = f"HTTP {response.status_code}: {response.text[:200]}"
            logger.warning(f"Download attempt {attempt}/3 gagal: {last_error}")
        except requests.exceptions.RequestException as e:
            last_error = str(e)
            logger.warning(f"Download attempt {attempt}/3 error: {e}")
        
        if attempt < 3:
            time.sleep(5)
    
    raise BusinessRuleException(
        f"Download file input dari OneDrive gagal ({upn} -> {onedrive_path}): {last_error}",
        error_code="BRE-OD-007"
    )


def _extract_dataframe_from_onedrive_file(local_path: str) -> pd.DataFrame:
    """
    Baca file input hasil download OneDrive menjadi DataFrame dengan header
    dinormalisasi ke Key Name (per sheet 'Key Field').
    
    Header di baris 5, data mulai baris 6. Header yang cocok dengan salah satu
    kandidat 'Excel One Drive' di-rename ke Key Name; header lain dibiarkan.
    """
    key_field_mapping = _load_key_field_mapping(_current_master_data_file)
    
    candidate_to_key = {}
    for key_name, cols in key_field_mapping.items():
        for candidate in cols["excel_one_drive"]:
            candidate_to_key[candidate.lower()] = key_name
    
    wb = openpyxl.load_workbook(local_path, data_only=True, read_only=True)
    ws = wb.active
    
    headers = []
    for col in range(1, ws.max_column + 1):
        header_value = ws.cell(row=5, column=col).value
        if header_value is None:
            headers.append(None)
            continue
        header_str = str(header_value).strip()
        headers.append(candidate_to_key.get(header_str.lower(), header_str))
    
    rows = []
    for row in ws.iter_rows(min_row=6, values_only=True):
        if any(cell is not None for cell in row):
            rows.append(row)
    
    wb.close()
    
    df = pd.DataFrame(rows, columns=headers)
    logger.info(f"File OneDrive dibaca: {len(df)} baris, kolom ternormalisasi: "
                f"{[h for h in headers if h]}")
    return df


def _load_from_onedrive(context) -> List[TransactionItem]:
    """
    Load transaction data dari file input Excel di OneDrive.
    
    Flow:
    1. Ambil template path dari config ONEDRIVE_FILE_INPUT
       (format: <pemilik-drive>/<folder>/.../{filename}), {filename} dari --filename
    2. Download file ke folder PATH_DOWNLOAD_FILE_INPUT
    3. Baca file jadi DataFrame dengan header dinormalisasi ke Key Name
    4. Ikuti pipeline yang sama dengan mode database
       (template input, site-qty mapping, validasi)
    """
    filename = getattr(context, "onedrive_input_file", None)
    if not filename:
        raise BusinessRuleException(
            "Filename tidak dispesifikkan. Gunakan --filename saat --source onedrive",
            error_code="BRE-OD-001"
        )
    
    if not context.onedrive_client:
        raise BusinessRuleException(
            "OneDrive client tidak terinisialisasi. Cek kredensial OneDrive di Config.xlsx "
            "(OneDrive_Client_ID, OneDrive_Client_Secret, OneDrive_Tenant_ID, User_Principal_Name)",
            error_code="BRE-OD-002"
        )
    
    template = str(context.config.get("ONEDRIVE_FILE_INPUT", "")).strip().strip("/")
    if not template or "{filename}" not in template:
        raise BusinessRuleException(
            "Config key 'ONEDRIVE_FILE_INPUT' tidak ada/kosong/tanpa placeholder {filename}",
            error_code="BRE-OD-005"
        )
    
    onedrive_path = template.replace("{filename}", str(filename).strip())
    parts = [p.strip() for p in onedrive_path.split("/") if p.strip()]
    if len(parts) < 2:
        raise BusinessRuleException(
            "ONEDRIVE_FILE_INPUT minimal 2 segmen: <pemilik-drive>/<folder>/.../{filename}",
            error_code="BRE-OD-006"
        )
    
    owner = parts[0]
    upn = owner if "@" in owner else f"{owner}@protelindo.net"
    path_in_drive = "/".join(parts[1:])
    
    logger.info(f"Downloading input file dari OneDrive: {upn} -> {path_in_drive}")
    
    local_folder = context.config.get("PATH_DOWNLOAD_FILE_INPUT", "RPA/File Input")
    os.makedirs(local_folder, exist_ok=True)
    base, ext = os.path.splitext(str(filename))
    local_path = os.path.abspath(os.path.join(local_folder, f"{base}_{context.run_timestamp}{ext or '.xlsx'}"))
    
    _download_onedrive_input_file(context.onedrive_client, upn, path_in_drive, local_path)
    logger.info(f"Input file tersimpan lokal: {local_path}")
    
    global _current_master_data_file
    _current_master_data_file = context.config.get("Mapping Process", "Data/Master Data.xlsx")
    df = _extract_dataframe_from_onedrive_file(local_path)
    
    if df.empty:
        logger.warning("File OneDrive tidak berisi data transaksi")
        return []
    
    df_columns_lower = {col.lower(): col for col in df.columns}
    po_col = df_columns_lower.get("po number")
    context.total_po_from_db = df[po_col].nunique() if po_col else 0
    
    master_data_file = _current_master_data_file
    wht_mapping = _load_wht_mapping(master_data_file)
    default_vat = context.config.get("Default_VAT", "ID:M1")
    
    # WHT di file OneDrive sudah berupa kode final - tanpa konversi rate
    input_file_path = _create_input_file_from_template(
        context, df, wht_mapping, default_vat, convert_wht=False)
    if not input_file_path:
        raise BusinessRuleException(
            "Gagal membuat input file dari hasil download OneDrive",
            error_code="BRE-OD-008"
        )
    
    context.config["Local_Input_File"] = input_file_path
    context.onedrive_input_source_path = path_in_drive
    context.site_qty_mapping = _build_site_qty_mapping(df)
    
    return _load_from_excel(context)


def _load_from_database(context) -> List[TransactionItem]:
    """
    Load transaction data from SQL Server.
    
    Flow:
    1. Execute query to get data
    2. Create input file from template
    3. Load transactions from the created file
    """
    db_server = context.config.get("DB_Server")
    db_database = context.config.get("DB_Database")
    db_username = context.config.get("DB_Username")
    db_password = context.config.get("DB_Password")
    query_file = context.config.get("DB_Query_File", "input_query.sql")
    
    if not db_server or not db_database:
        raise BusinessRuleException(
            "Database server and database name are required for SQL Server input",
            error_code="BRE-DB-001"
        )
    
    # Load WHT mapping from master data
    master_data_file = context.config.get("Mapping Process", "Data/Master Data.xlsx")
    wht_mapping = _load_wht_mapping(master_data_file)
    
    # Get default VAT from config
    default_vat = context.config.get("Default_VAT", "ID:M1")
    
    # Load query from file
    if query_file and os.path.exists(query_file):
        with open(query_file, 'r', encoding='utf-8') as f:
            query = f.read()
        logger.info(f"Query loaded from: {query_file}")
    else:
        raise BusinessRuleException(
            f"Query file not found: {query_file}",
            error_code="BRE-DB-002"
        )
    
    # Build connection string
    if db_username and db_password:
        conn_str = f"DRIVER={{ODBC Driver 17 for SQL Server}};SERVER={db_server};DATABASE={db_database};UID={db_username};PWD={db_password}"
    else:
        conn_str = f"DRIVER={{ODBC Driver 17 for SQL Server}};SERVER={db_server};DATABASE={db_database};Trusted_Connection=yes"
    
    try:
        logger.info(f"Connecting to {db_server}/{db_database}...")
        conn = pyodbc.connect(conn_str, timeout=30)
        
        # Execute query and load to DataFrame
        df = pd.read_sql(query, conn)
        conn.close()
        
        logger.info(f"Query returned {len(df)} rows")
        
        # Count distinct PO numbers from database for reporting
        df_columns_lower = {col.lower(): col for col in df.columns}
        po_col = df_columns_lower.get('po number')
        if po_col:
            context.total_po_from_db = df[po_col].nunique()
        else:
            context.total_po_from_db = 0
        
        if df.empty:
            logger.warning("No data returned from database")
            return []
        
        # Create input file from template
        input_file_path = _create_input_file_from_template(context, df, wht_mapping, default_vat)
        
        if not input_file_path:
            raise BusinessRuleException(
                "Failed to create input file from template",
                error_code="BRE-DB-003"
            )
        
        # Update config with the created input file path
        context.config["Local_Input_File"] = input_file_path
        
        # Build site-qty mapping from DataFrame for later use in Process.py
        context.site_qty_mapping = _build_site_qty_mapping(df)
        
        # Load transactions from the created file
        return _load_from_excel(context)
        
    except pyodbc.Error as e:
        raise BusinessRuleException(
            f"Database connection error: {str(e)}",
            error_code="BRE-DB-004"
        )


def _create_input_file_from_template(context, df: pd.DataFrame, wht_mapping: Dict[float, str],
                                     default_vat: str, convert_wht: bool = True) -> Optional[str]:
    """
    Create input file from template Excel by inserting DataFrame data.
    
    Args:
        context: Bot context
        df: DataFrame with query results
        wht_mapping: WHT rate to code mapping
        default_vat: Default VAT value from config
        
    Returns:
        Path to created input file, or None if failed
    """
    template_path = "Data/Template File Input.xlsx"
    output_folder = "RPA/File Input"
    
    if not os.path.exists(template_path):
        logger.error(f"Template file not found: {template_path}")
        return None
    
    # Create output folder if not exists
    os.makedirs(output_folder, exist_ok=True)
    
    # Generate output filename
    timestamp = datetime.now().strftime("%d%m%Y_%H%M%S")
    output_filename = f"FileInput_{timestamp}.xlsx"
    output_path = os.path.join(output_folder, output_filename)
    
    try:
        # Load template
        wb = openpyxl.load_workbook(template_path)
        ws = wb.active
        
        # Column mapping: DataFrame column name -> Excel column index (1-based)
        # Template structure (based on header row 5):
        # A(1)=Tower Provider Name, B(2)=PO Number, C(3)=Invoice Number,
        # D(4)=Invoice Line Number, E(5)=Invoice Description, F(6)=Status Site,
        # G(7)=Payment Period Start, H(8)=BA Number, I(9)=Tax Invoice Number,
        # J(10)=Tax Invoice Date, K(11)=Invoice Amount, L(12)=WHT Code,
        # M(13)=IOH Site ID Active, N(14)=IOH Site ID, O(15)=Qty, P(16)=VAT,
        # Q(17)=Invoice Date, R(18)=Status RPA, S(19)=Modified Date
        column_mapping = {
            'Tower Provider Name': 1,
            'PO Number': 2,
            'Invoice Number': 3,
            'Invoice Line Number': 4,
            'Invoice Description': 5,
            'Status Site': 6,
            'Payment Period Start': 7,
            'BA Number': 8,
            'Tax Invoice Number': 9,
            'Tax Invoice Date': 10,
            'Invoice Amount': 11,
            'WHT Code': 12,
            'IOH Site ID Active': 13,
            'IOH Site ID': 14,
            'Qty': 15,
            'VAT': 16
        }
        
        # Normalize DataFrame column names for case-insensitive matching
        df_columns_lower = {col.lower(): col for col in df.columns}
        
        # Insert data starting from row 6
        start_row = 6
        
        for row_idx, row_data in df.iterrows():
            excel_row = start_row + row_idx
            
            for col_name, col_idx in column_mapping.items():
                # Find matching column in DataFrame (case-insensitive)
                df_col = df_columns_lower.get(col_name.lower())
                
                if df_col and df_col in row_data:
                    value = row_data[df_col]
                    
                    # Handle NaN/None values
                    if pd.isna(value):
                        value = None
                    
                    # Special handling for WHT Code - convert rate to code.
                    # convert_wht=False (mode OneDrive): nilai sudah kode final, tulis apa adanya.
                    if col_name == 'WHT Code' and value is not None and convert_wht:
                        wht_code = _get_wht_code(value, wht_mapping)
                        if wht_code:
                            value = wht_code
                        else:
                            logger.warning(f"Row {excel_row}: WHT rate '{value}' not found in mapping, keeping original value")
                    
                    ws.cell(row=excel_row, column=col_idx).value = value
                
                # Special handling for VAT - always use default value
                elif col_name == 'VAT':
                    ws.cell(row=excel_row, column=col_idx).value = default_vat
        
        # Save the file
        wb.save(output_path)
        wb.close()
        
        # Convert to absolute path for reliable file operations
        output_path_abs = os.path.abspath(output_path)
        
        logger.info(f"Input file created: {output_path_abs}")
        logger.info(f"  Rows inserted: {len(df)}")
        
        return output_path_abs
        
    except Exception as e:
        logger.error(f"Error creating input file: {e}")
        return None


def _build_site_qty_mapping(df: pd.DataFrame) -> Dict[str, List[Dict[str, Any]]]:
    """
    Build site-qty mapping from DataFrame for use in Process.py.
    
    Returns:
        Dictionary with key = "PO_InvoiceNumber" and value = list of {site_id, site_id_active, qty, line_number}
    """
    site_qty_mapping = {}
    
    # Normalize column names
    df_columns_lower = {col.lower(): col for col in df.columns}
    
    po_col = df_columns_lower.get('po number')
    inv_col = df_columns_lower.get('invoice number')
    site_col = df_columns_lower.get('ioh site id')
    site_active_col = df_columns_lower.get('ioh site id active')
    qty_col = df_columns_lower.get('qty')
    line_num_col = df_columns_lower.get('invoice line number')
    period_col = df_columns_lower.get('payment period start')
    
    if not all([po_col, inv_col]):
        return site_qty_mapping
    
    # Calculate distinct Payment Period Start per PO + Invoice Number
    payment_period_count = {}
    if period_col:
        for _, row in df.iterrows():
            po = str(row[po_col]).strip() if pd.notna(row[po_col]) else ""
            inv = str(row[inv_col]).strip() if pd.notna(row[inv_col]) else ""
            period = str(row[period_col]).strip() if pd.notna(row[period_col]) else ""
            if po and inv and period:
                key = f"{po}_{inv}"
                if key not in payment_period_count:
                    payment_period_count[key] = set()
                payment_period_count[key].add(period)
        
        # Convert sets to counts
        payment_period_count = {k: len(v) for k, v in payment_period_count.items()}
        logger.info(f"Payment Period multipliers: {payment_period_count}")
    
    for _, row in df.iterrows():
        po = str(row[po_col]).strip() if pd.notna(row[po_col]) else ""
        inv = str(row[inv_col]).strip() if pd.notna(row[inv_col]) else ""
        site_id = str(row[site_col]).strip() if site_col and pd.notna(row[site_col]) else ""
        site_id_active = str(row[site_active_col]).strip() if site_active_col and pd.notna(row[site_active_col]) else ""
        qty = row[qty_col] if qty_col and pd.notna(row[qty_col]) else 0
        line_number = str(int(row[line_num_col])) if line_num_col and pd.notna(row[line_num_col]) else ""
        
        if po and inv and (site_id or site_id_active):
            key = f"{po}_{inv}"
            
            # Multiply qty by distinct payment period count
            period_multiplier = payment_period_count.get(key, 1)
            qty_final = float(qty) * period_multiplier if qty != "Used Coupa" and str(qty).strip().lower() != "used coupa" else qty
            
            if key not in site_qty_mapping:
                site_qty_mapping[key] = []
            site_qty_mapping[key].append({
                "site_id": site_id,
                "site_id_active": site_id_active,
                "qty": qty_final,
                "line_number": line_number
            })
    
    return site_qty_mapping


def _load_from_excel(context) -> List[TransactionItem]:
    """
    Load transaction data from local Excel file.
    
    Returns:
        List of TransactionItem to process
    """
    input_file = context.config.get("Local_Input_File", "")
    
    if not input_file:
        raise BusinessRuleException(
            "Local_Input_File not configured",
            error_code="BRE-010"
        )
    
    if not os.path.exists(input_file):
        raise BusinessRuleException(
            f"Input file not found: {input_file}",
            error_code="BRE-011"
        )
    
    # Convert to absolute path for reliable file operations
    input_file = os.path.abspath(input_file)
    
    try:
        wb = openpyxl.load_workbook(input_file, data_only=True)
        ws = wb.active
        
        # Build header mapping from file input row 5 (not from Master Data)
        header_mapping = {}
        for col in range(1, ws.max_column + 1):
            header_value = ws.cell(row=5, column=col).value
            if header_value:
                header_mapping[str(header_value).strip().lower()] = col
        
        # Load mapping + required fields per sumber (Mapping Field sheet, tanpa fallback)
        master_data_file = context.config.get("Mapping Process", "Data/Master Data.xlsx")
        global _current_master_data_file
        _current_master_data_file = master_data_file
        key_field_mapping = _load_key_field_mapping(master_data_file)
        
        is_onedrive = (getattr(context, "input_source", "database") or "database").lower() == "onedrive"
        required_flag = "required_excel" if is_onedrive else "required_db"
        required_attrs = {}
        for key_name, cols in key_field_mapping.items():
            if cols[required_flag]:
                required_attrs[_key_to_attr(key_name)] = key_name
        
        # Read all transactions
        transactions = []
        seen_invoices = {}
        skipped_rows = []
        
        for row_num in range(6, ws.max_row + 1):
            # Check if row has data
            po_number = _get_cell_value(ws, row_num, header_mapping, "po number")
            if not po_number:
                continue
            
            # Check status RPA - skip if already processed
            status_rpa = _get_cell_value(ws, row_num, header_mapping, "status rpa")
            if status_rpa and "success" in str(status_rpa).lower():
                continue
            
            invoice_number = _get_cell_value(ws, row_num, header_mapping, "invoice number")
            
            # Create unique key for grouping
            unique_key = f"{po_number}_{invoice_number}"
            
            if unique_key in seen_invoices:
                # Add as duplicate row
                seen_invoices[unique_key].duplicate_rows.append(row_num)
                continue
            
            # Create transaction item
            transaction = TransactionItem(
                row_index=row_num,
                tower_provider_name=_get_cell_value(ws, row_num, header_mapping, "tower provider name"),
                po_number=po_number,
                invoice_number=invoice_number,
                invoice_line_number=_get_cell_value(ws, row_num, header_mapping, "invoice line number"),
                invoice_description=_get_cell_value(ws, row_num, header_mapping, "invoice description"),
                status_site=_get_cell_value(ws, row_num, header_mapping, "status site"),
                payment_period_start=_get_cell_value(ws, row_num, header_mapping, "payment period start"),
                ba_number=_get_cell_value(ws, row_num, header_mapping, "ba number"),
                tax_invoice_number=_get_cell_value(ws, row_num, header_mapping, "tax invoice number"),
                tax_invoice_date=_get_cell_value(ws, row_num, header_mapping, "tax invoice date"),
                invoice_amount=_get_cell_value(ws, row_num, header_mapping, "invoice amount"),
                wht_code=_get_cell_value(ws, row_num, header_mapping, "wht code"),
                ioh_site_id=_get_cell_value(ws, row_num, header_mapping, "ioh site id"),
                site_name_ioh=_get_cell_value(ws, row_num, header_mapping, "ioh site id active"),
                qty=_get_cell_value(ws, row_num, header_mapping, "qty"),
                vat=_get_cell_value(ws, row_num, header_mapping, "vat"),
                source_file_path=input_file,
                duplicate_rows=[]
            )
            
            # Validate required fields - SKIP row if invalid
            missing_fields = _validate_transaction(transaction, required_attrs)
            if missing_fields:
                skip_reason = f"Skipped - Missing: {', '.join(missing_fields)}"
                logger.warning(f"Row {row_num}: {skip_reason}")
                
                # Collect skipped row - status akan ditulis batch di akhir
                skipped_rows.append((row_num, skip_reason))
                
                # Store skipped transaction info for later DB update
                context.skipped_transactions.append({
                    "po_number": po_number,
                    "invoice_number": invoice_number,
                    "site_id": transaction.ioh_site_id,
                    "site_id_active": transaction.site_name_ioh,
                    "reason": skip_reason
                })
                
                # Increment skipped count
                context.skipped_count += 1
                
                continue  # Skip this row, don't add to transactions
            
            transactions.append(transaction)
            seen_invoices[unique_key] = transaction
        
        wb.close()
        
        # Tulis status semua baris skip dalam satu operasi buka-save
        if skipped_rows:
            _update_skipped_rows_status_batch(input_file, skipped_rows, context.run_timestamp)
        
        # Grouping by entity (kode kanonik, resolve alias) sebelum masuk Process:
        # selesaikan semua transaksi satu entity dulu, dalam entity urut per PO+Invoice.
        # Dengan begitu login/logout minimal 1x per perusahaan.
        def _entity_sort_key(t: TransactionItem):
            raw = str(t.tower_provider_name or "").strip().upper()
            entity = context.company_aliases.get(raw, raw)
            return (entity, str(t.po_number or ""), str(t.invoice_number or ""))
        
        transactions.sort(key=_entity_sort_key)
        distinct_entities = {(_entity_sort_key(t)[0]) for t in transactions}
        logger.info(f"Transaction order: {len(distinct_entities)} entity -> "
                    f"{', '.join(sorted(distinct_entities))}")
        
        if not transactions:
            logger.warning("No valid transactions to process")
        else:
            logger.info(f"Valid transactions to process: {len(transactions)}")
            if context.skipped_count > 0:
                logger.info(f"Skipped (validation failed): {context.skipped_count}")
        
        return transactions
        
    except BusinessRuleException:
        raise
    except Exception as e:
        raise BusinessRuleException(
            f"Error reading Excel file: {e}",
            error_code="BRE-012"
        )


def _get_cell_value(ws, row: int, header_mapping: dict, field_name: str):
    """Get cell value by field name using header mapping."""
    col_idx = header_mapping.get(field_name.lower())
    if col_idx:
        value = ws.cell(row=row, column=col_idx).value
        if value is not None:
            if isinstance(value, str):
                return value.strip()
            return value
    return None


_ATTR_OVERRIDES = {
    "IOH Site ID Active": "site_name_ioh"
}


def _key_to_attr(key_name: str) -> str:
    """Konversi Key Name ke nama atribut TransactionItem (snake_case)."""
    if key_name in _ATTR_OVERRIDES:
        return _ATTR_OVERRIDES[key_name]
    return key_name.lower().replace(" ", "_")


def _validate_transaction(transaction: TransactionItem, required_attrs: Dict[str, str]) -> List[str]:
    """
    Validasi field required sesuai sheet Mapping Field (data-driven per sumber).
    
    Args:
        transaction: TransactionItem yang divalidasi
        required_attrs: {atribut TransactionItem: display name key} yang required
    
    Returns:
        List of missing field names
    """
    missing = []
    for attr, display_name in required_attrs.items():
        value = getattr(transaction, attr, None)
        if value is None or (isinstance(value, str) and not value.strip()):
            missing.append(display_name)
    
    return missing


def _update_skipped_rows_status_batch(file_path: str, skipped_rows: List[tuple], run_timestamp: str):
    """
    Update Status RPA and Modified Date untuk semua baris skip dalam satu operasi.
    
    Called after validation loop finishes. Updates columns:
    - Col 18 = Status RPA
    - Col 19 = Modified Date
    
    Note: Invoice Date (Col 17) is NOT updated for skipped rows since no invoice was processed.
    
    Args:
        file_path: Path file input Excel
        skipped_rows: List of (row_index, status) tuples
        run_timestamp: Run timestamp untuk Modified Date
    """
    if not skipped_rows:
        return
    
    if not file_path or not os.path.exists(file_path):
        logger.warning(f"Cannot update skipped row status - file not found: {file_path}")
        return
    
    # Fixed column indices based on Template File Input structure
    col_status_rpa = 18     # Column R
    col_modified_date = 19  # Column S
    
    try:
        wb = openpyxl.load_workbook(file_path)
        ws = wb.active
        
        for row_index, status in skipped_rows:
            ws.cell(row=row_index, column=col_status_rpa).value = status
            ws.cell(row=row_index, column=col_modified_date).value = run_timestamp
        
        wb.save(file_path)
        wb.close()
        
        logger.info(f"Excel updated (skipped): {len(skipped_rows)} rows")
        
    except Exception as e:
        logger.error(f"Failed to update skipped row status: {e}")


def init_database_connection(context):
    """
    Initialize database connection for status updates.
    Call this once at the beginning of processing.
    """
    global _db_scope
    
    db_server = context.config.get("DB_Server")
    db_database = context.config.get("DB_Database")
    db_username = context.config.get("DB_Username")
    db_password = context.config.get("DB_Password")
    
    if not db_server or not db_database:
        logger.warning("Database not configured, skipping DB status updates")
        return
    
    try:
        from library.database_utils import DatabaseScope
        
        _db_scope = DatabaseScope(
            db_type="sqlserver",
            server=db_server,
            database=db_database,
            username=db_username,
            password=db_password
        )
        _db_scope.connect()
        logger.info("Database connection initialized for status updates")
    except Exception as e:
        logger.warning(f"Failed to initialize database connection: {e}")
        _db_scope = None


def close_database_connection():
    """Close database connection."""
    global _db_scope
    
    if _db_scope:
        try:
            _db_scope.disconnect()
            logger.debug("Database connection closed")
        except Exception as e:
            logger.warning(f"Error closing database connection: {e}")
        _db_scope = None


def update_database_status(po_number: str, invoice_number: str, site_id: str, status: str):
    """
    Update RPA_COUPA_STATUS and RPA_COUPA_DATE in database.
    
    Args:
        po_number: PO Number
        invoice_number: Invoice Number (not used in WHERE - exists in detail table only)
        site_id: IOH Site ID (for matching specific row)
        status: Status message (Success, Business Exception, System Exception, etc.)
    """
    global _db_scope
    
    if not _db_scope:
        return
    
    try:
        # Get current datetime
        current_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # Update query - match by PO Number and Site ID
        # Note: INVOICE_NUMBER is in detail table (join), not in header table
        update_query = """
            UPDATE TrackerProcess_Billing_IOH_Clone 
            SET RPA_COUPA_STATUS = :status,
                RPA_COUPA_DATE = :coupa_date
            WHERE RPA_PONUMBER_IOH = :po_number 
              AND IOH_SITE_ID = :site_id
        """
        
        params = {
            "status": status,
            "coupa_date": current_date,
            "po_number": str(po_number).strip(),
            "site_id": str(site_id).strip()
        }
        
        rows_affected = _db_scope.execute_non_query(update_query, params)
        
        if rows_affected > 0:
            logger.debug(f"DB updated: PO={po_number}, Site={site_id}, Status={status[:30]}...")
        else:
            logger.debug(f"DB update: No rows matched for PO={po_number}, Site={site_id}")
            
    except Exception as e:
        logger.warning(f"Failed to update database status: {e}")


def update_database_status_bulk(po_number: str, invoice_number: str, site_ids: list, status: str):
    """
    Update RPA_COUPA_STATUS and RPA_COUPA_DATE for multiple site IDs.
    
    Args:
        po_number: PO Number
        invoice_number: Invoice Number
        site_ids: List of IOH Site IDs
        status: Status message
    """
    for site_id in site_ids:
        if site_id:
            update_database_status(po_number, invoice_number, site_id, status)


def update_skipped_transactions_to_db(context):
    """
    Update database status for all skipped transactions.
    Call this after database connection is initialized.
    """
    if not context.skipped_transactions:
        return
    
    logger.info(f"Updating {len(context.skipped_transactions)} skipped transactions to database...")
    
    for item in context.skipped_transactions:
        site_ids = []
        if item.get("site_id"):
            site_ids.append(item["site_id"])
        if item.get("site_id_active") and item.get("site_id_active") != item.get("site_id"):
            site_ids.append(item["site_id_active"])
        
        if site_ids:
            update_database_status_bulk(
                po_number=item["po_number"],
                invoice_number=item["invoice_number"],
                site_ids=site_ids,
                status=item["reason"]
            )
    
    logger.info("Skipped transactions updated to database")
