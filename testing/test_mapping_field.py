"""
Test: Mapping Field - validasi mapping & required field per sumber data

Menguji sheet 'Mapping Field' di Data/Master Data.xlsx yang jadi sumber
validasi data-driven robot:

Tahap 1 - Inspeksi mapping (tanpa akses sumber data):
  - Sheet 'Mapping Field' terbaca, jumlah key, required DB/Excel
  - Semua Key Name punya atribut TransactionItem yang valid
  - Preview required field per sumber

Tahap 2 - Load transaksi penuh (sesuai --source, read-only):
  - database : SELECT dari SQL Server (tanpa update status DB)
  - onedrive : download file input dari OneDrive (tanpa update status DB)
  Hasilnya: jumlah transaksi valid vs skipped menurut validasi sheet.

Run:
    python testing/test_mapping_field.py
    python testing/test_mapping_field.py --source onedrive --filename "Sample CR 29102026.xlsx"
    python testing/test_mapping_field.py --source database
"""

import os
import sys
import time
import argparse
import logging

logging.basicConfig(
    level=logging.ERROR,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from framework.models import BotContext, TransactionItem
from framework import InitAllSettings, GetTransactionData

MASTER_DATA_FILE = "Data/Master Data.xlsx"


def parse_arguments():
    parser = argparse.ArgumentParser(description="Test Mapping Field & validasi data-driven")
    parser.add_argument(
        "--source",
        choices=["database", "onedrive"],
        default=None,
        help="Jika diisi, lanjut ke Tahap 2 (load transaksi penuh dari sumber ini)"
    )
    parser.add_argument(
        "--filename",
        default=None,
        help="Nama file input OneDrive (wajib saat --source onedrive)"
    )
    return parser.parse_args()


def test_mapping_inspection():
    """Tahap 1: inspeksi sheet Mapping Field + validasi atribut TransactionItem."""
    print("=" * 60)
    print("TAHAP 1: INSPEKSI MAPPING FIELD")
    print("=" * 60)

    mapping = GetTransactionData._load_key_field_mapping(MASTER_DATA_FILE)
    print(f"File        : {MASTER_DATA_FILE}")
    print(f"Jumlah key  : {len(mapping)}")

    required_db = [k for k, v in mapping.items() if v["required_db"]]
    required_excel = [k for k, v in mapping.items() if v["required_excel"]]
    print(f"Required DB    ({len(required_db):2d}) : {', '.join(required_db)}")
    print(f"Required Excel ({len(required_excel):2d}) : {', '.join(required_excel)}")

    # Semua key harus punya atribut TransactionItem yang valid
    valid_attrs = {f for f in TransactionItem.__dataclass_fields__}
    errors = []
    for key_name in mapping:
        attr = GetTransactionData._key_to_attr(key_name)
        if attr not in valid_attrs:
            errors.append(f"  - '{key_name}' -> atribut '{attr}' tidak ada di TransactionItem")

    if errors:
        print(f"\n[FAIL] Atribut tidak valid ({len(errors)}):")
        for e in errors:
            print(e)
        return mapping, False

    print(f"\n[OK] Semua {len(mapping)} key ter-mapping ke atribut TransactionItem")
    return mapping, True


def test_full_load(source: str, filename: str = None):
    """Tahap 2: load transaksi penuh dari sumber yang dipilih (read-only)."""
    print()
    print("=" * 60)
    print(f"TAHAP 2: LOAD TRANSAKSI ({source.upper()})")
    print("=" * 60)

    context = BotContext()
    InitAllSettings.load_config_only(context)

    if source == "onedrive":
        if not filename:
            print("[FAIL] --filename wajib saat --source onedrive")
            return
        if not context.onedrive_client:
            print("[FAIL] OneDrive tidak terkonfigurasi di Config.xlsx")
            return
        context.onedrive_input_file = filename

    context.input_source = source

    start = time.time()
    try:
        transactions = GetTransactionData.execute(context)
    except Exception as e:
        print(f"[FAIL] {type(e).__name__}: {e}")
        return

    elapsed = time.time() - start

    print(f"Duration         : {elapsed:.1f} detik")
    print(f"Transaksi valid  : {len(transactions)}")
    print(f"Skipped          : {context.skipped_count}")
    print(f"Total PO unik    : {context.total_po_from_db}")
    print(f"Key site-qty     : {len(context.site_qty_mapping)}")

    if context.skipped_count:
        reasons = {}
        for item in context.skipped_transactions:
            reason = item["reason"].replace("Skipped - Missing: ", "")
            reasons[reason] = reasons.get(reason, 0) + 1
        print("\nAlasan skip:")
        for reason, count in sorted(reasons.items(), key=lambda x: -x[1]):
            print(f"  {count:4d}x {reason}")

    if transactions:
        t = transactions[0]
        print(f"\nSample transaksi pertama:")
        print(f"  Company : {t.tower_provider_name}")
        print(f"  PO      : {t.po_number}")
        print(f"  Invoice : {t.invoice_number}")
        print(f"  Amount  : {t.invoice_amount}")
        print(f"  WHT     : {t.wht_code}")
        print(f"  Site    : {t.ioh_site_id} (active: {t.site_name_ioh})")
        print(f"  Qty/VAT : {t.qty} / {t.vat}")


def main():
    args = parse_arguments()

    mapping, ok = test_mapping_inspection()
    if not ok:
        return

    if args.source:
        print()
        test_full_load(args.source, args.filename)
    else:
        print("\nTahap 2 dilewati (beri --source database|onedrive untuk menjalankannya)")


if __name__ == "__main__":
    main()
