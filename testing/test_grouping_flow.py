"""
Test: Grouping Flow — entity grouping sebelum login + PO+Invoice di dalam entity

Menguji alur grouping di `_load_from_excel()` dengan DATA DUMMY (file Excel
sementara), tanpa akses OneDrive / SQL Server / Coupa:

1. Grouping by entity (kode kanonik via company_aliases) — semua transaksi
   satu entity berdempet, selesai dulu sebelum entity berikutnya
2. Dalam entity, urut per PO Number + Invoice Number
3. Dedup PO+Invoice: baris duplikat (termasuk beda alias entity) masuk
   duplicate_rows, bukan transaksi baru
4. Simulasi login: hitung pergantian entity = jumlah login yang seharusnya

Run:
    python testing/test_grouping_flow.py
"""

import os
import sys
import logging
from datetime import datetime

import openpyxl

logging.basicConfig(
    level=logging.ERROR,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from framework.models import BotContext
from framework import GetTransactionData

DUMMY_FOLDER = "RPA/File Input"


def _entity_key(context, t):
    """Salin logika sort key di _load_from_excel (untuk assertion)."""
    raw = str(t.tower_provider_name or "").strip().upper()
    entity = context.company_aliases.get(raw, raw)
    return (entity, str(t.po_number or ""), str(t.invoice_number or ""))


def create_dummy_input_file() -> str:
    """Buat file Excel dummy dengan struktur template (header row 5, data row 6+)."""
    os.makedirs(DUMMY_FOLDER, exist_ok=True)
    path = os.path.abspath(os.path.join(
        DUMMY_FOLDER, f"test_dummy_grouping_{datetime.now().strftime('%d%m%Y_%H%M%S')}.xlsx"))

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "DATA"

    headers = [
        "Tower Provider Name", "PO Number", "Invoice Number", "Invoice Line Number",
        "Invoice Description", "Status Site", "Payment Period Start", "BA Number",
        "Tax Invoice Number", "Tax Invoice Date", "Invoice Amount", "WHT Code",
        "IOH Site ID Active", "IOH Site ID", "Qty", "VAT",
        "Invoice Date", "Status RPA", "Modified Date"
    ]
    for col_idx, h in enumerate(headers, start=1):
        ws.cell(row=5, column=col_idx).value = h

    # Data dummy — urutan sengaja campur antar entity + ada duplikat PO+Invoice
    # (row index Excel = 6 + index list)
    dummy_rows = [
        # company (boleh alias), PO,    Invoice,   Line, Site
        ("PTI",                                        "222", "INV-B", "1", "SITE-A"),
        ("KOH",                                        "333", "INV-C", "1", "SITE-B"),
        ("PT PROFESIONAL TELEKOMUNIKASI INDONESIA",    "111", "INV-A", "1", "SITE-A"),
        ("STP",                                        "444", "INV-D", "1", "SITE-C"),
        ("PTI",                                        "111", "INV-A", "1", "SITE-A"),  # duplikat dgn baris di atas
        ("KOH",                                        "111", "INV-E", "1", "SITE-B"),
        ("STP",                                        "222", "INV-F", "1", "SITE-C"),
    ]
    for row_offset, (company, po, inv, line, site) in enumerate(dummy_rows):
        row = 6 + row_offset
        values = {
            1: company, 2: po, 3: inv, 4: line,
            5: f"Dummy invoice {inv}", 6: "Recurring", 7: "2026-01-01",
            8: f"BA-{inv}", 9: f"TAX-{inv}", 10: "2026-01-05",
            11: 100000, 12: "VA - Sewa tanah dan bangunan - 10%",
            13: site, 14: site, 15: 12, 16: "ID: M1",
        }
        for col_idx, value in values.items():
            ws.cell(row=row, column=col_idx).value = value

    wb.save(path)
    wb.close()
    return path


def run_test():
    context = BotContext()
    context.input_source = "database"   # validasi pakai kolom Required DB
    context.run_timestamp = datetime.now().strftime("%d%m%Y_%H%M%S")
    context.config["Local_Input_File"] = create_dummy_input_file()

    # Dummy alias company — meniru isi Account Coupa IOH.xlsx multi-alias
    context.company_aliases = {
        "PTI": "PTI",
        "PT PROFESIONAL TELEKOMUNIKASI INDONESIA": "PTI",
        "KOH": "KOH",
        "STP": "STP",
    }

    transactions = GetTransactionData._load_from_excel(context)

    print("=" * 60)
    print("HASIL GROUPING FLOW")
    print("=" * 60)

    sequence = []
    for t in transactions:
        entity, po, inv = _entity_key(context, t)
        sequence.append((entity, po, inv))
        print(f"  entity={entity:4s} PO={po} Inv={inv}  row={t.row_index} "
              f"duplicates={t.duplicate_rows}")

    failures = []

    # 1. Semua transaksi valid (tidak ada skip)
    if context.skipped_count != 0:
        failures.append(f"Skipped seharusnya 0, dapat {context.skipped_count}")

    # 2. Dedup: 7 baris dummy, 1 duplikat → 6 transaksi
    if len(transactions) != 6:
        failures.append(f"Jumlah transaksi seharusnya 6, dapat {len(transactions)}")

    # 3. Entity berdempet (grouping by entity)
    entities_in_order = [s[0] for s in sequence]
    seen_entities = set()
    for entity in entities_in_order:
        if entity in seen_entities and entities_in_order[entities_in_order.index(entity) - 1] != entity:
            pass
    blocks = []
    for entity in entities_in_order:
        if not blocks or blocks[-1] != entity:
            blocks.append(entity)
    if len(blocks) != len(set(blocks)):
        failures.append(f"Entity tidak berdempet: {blocks}")

    # 4. Urutan entity ascending (sorted)
    if blocks != sorted(blocks):
        failures.append(f"Urutan entity tidak sorted: {blocks}")

    # 5. Dalam entity, PO+Invoice ascending
    for i in range(1, len(sequence)):
        prev, curr = sequence[i - 1], sequence[i]
        if prev[0] == curr[0] and (prev[1], prev[2]) > (curr[1], curr[2]):
            failures.append(f"PO+Invoice tidak urut dalam entity {curr[0]}: {prev} -> {curr}")

    # 6. Dedup PO+Invoice: transaksi PTI/111/INV-A punya 1 duplicate_rows
    dup_tx = next((t for t in transactions if _entity_key(context, t)[1] == "111"
                   and _entity_key(context, t)[2] == "INV-A"), None)
    if dup_tx is None:
        failures.append("Transaksi 111/INV-A tidak ditemukan")
    elif len(dup_tx.duplicate_rows) != 1:
        failures.append(f"Duplicate rows seharusnya 1, dapat {dup_tx.duplicate_rows}")

    # 7. Simulasi login: pergantian entity
    login_changes = sum(
        1 for i in range(1, len(sequence)) if sequence[i][0] != sequence[i - 1][0])
    expected_logins = len(set(s[0] for s in sequence)) - 1
    print(f"\nSimulasi login : {login_changes}x logout-login "
          f"({len(set(s[0] for s in sequence))} entity, {len(sequence)} transaksi)")
    if login_changes != expected_logins:
        failures.append(f"Pergantian entity seharusnya {expected_logins}, dapat {login_changes}")

    # Cleanup file dummy
    try:
        os.remove(context.config["Local_Input_File"])
    except OSError:
        pass

    print()
    if failures:
        print(f"[FAIL] {len(failures)} masalah:")
        for f in failures:
            print(f"  - {f}")
        return False

    print("[PASS] Grouping flow benar: entity berdempet, PO+Invoice urut dalam entity, "
          "duplikat ter-group, login minimal per entity")
    return True


if __name__ == "__main__":
    success = run_test()
    sys.exit(0 if success else 1)
