"""
Test: Download File Input dari OneDrive (drive user lain)

Mengambil file input Excel dari folder OneDrive sesuai template di
Config.xlsx (key: ONEDRIVE_FILE_INPUT), contoh value:
    finanalyst-sp/RPA/Invoice Submit Coupa Billing IOH/RPA/{filename}

- Segmen pertama  = pemilik drive (UPN prefix)
- {filename}      = diisi dari argument CLI --filename

Run:
    python testing/test_onedrive_input.py
    python testing/test_onedrive_input.py --filename "Sample CR 29102026.xlsx"
"""

import os
import sys
import time
import argparse
import logging
import urllib.parse

import requests
import openpyxl

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from framework.models import BotContext
from framework import InitAllSettings

DEFAULT_FILENAME = "Sample CR 29102026.xlsx"
DEFAULT_DOMAIN = "@protelindo.net"
DEFAULT_LOCAL_INPUT_FOLDER = "RPA/File Input"
DOWNLOAD_RETRIES = 3
DOWNLOAD_RETRY_INTERVAL = 5
DOWNLOAD_TIMEOUT = 120


def parse_arguments():
    parser = argparse.ArgumentParser(description="Test download file input dari OneDrive")
    parser.add_argument(
        "--filename",
        default=DEFAULT_FILENAME,
        help=f"Nama file input di OneDrive (default: {DEFAULT_FILENAME})"
    )
    return parser.parse_args()


def resolve_target(config: dict, filename: str) -> tuple:
    """Parse template ONEDRIVE_FILE_INPUT menjadi (upn, onedrive_path)."""
    template = str(config.get("ONEDRIVE_FILE_INPUT", "")).strip().strip("/")
    if not template:
        raise ValueError(
            "Config key 'ONEDRIVE_FILE_INPUT' tidak ditemukan/kosong di Config.xlsx"
        )
    if "{filename}" not in template:
        raise ValueError("Template ONEDRIVE_FILE_INPUT tidak mengandung placeholder {filename}")

    parts = [p.strip() for p in template.split("/") if p.strip()]
    if len(parts) < 2:
        raise ValueError(
            "Template ONEDRIVE_FILE_INPUT minimal 2 segmen: "
            "<pemilik-drive>/<folder>/.../{filename}"
        )

    owner = parts[0]
    upn = owner if "@" in owner else f"{owner}{DEFAULT_DOMAIN}"
    onedrive_path = "/".join(parts[1:]).replace("{filename}", filename)
    return upn, onedrive_path


def download_file(client, upn: str, onedrive_path: str, local_path: str) -> str:
    """Download file dari drive user target, simpan ke local_path. Return path lokal."""
    encoded = urllib.parse.quote(onedrive_path.lstrip("/"))
    endpoint = f"/users/{upn}/drive/root:/{encoded}:/content"
    url = f"{client.GRAPH_API_BASE}{endpoint}"
    headers = client._get_headers()

    last_error = None
    for attempt in range(1, DOWNLOAD_RETRIES + 1):
        try:
            response = requests.get(url, headers=headers, timeout=DOWNLOAD_TIMEOUT, stream=True)
            if response.status_code == 200:
                os.makedirs(os.path.dirname(local_path), exist_ok=True)
                with open(local_path, "wb") as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        f.write(chunk)
                return local_path

            last_error = f"HTTP {response.status_code}: {response.text[:200]}"
            logger.warning(f"Download attempt {attempt}/{DOWNLOAD_RETRIES} gagal: {last_error}")
        except requests.exceptions.RequestException as e:
            last_error = str(e)
            logger.warning(f"Download attempt {attempt}/{DOWNLOAD_RETRIES} error: {e}")

        if attempt < DOWNLOAD_RETRIES:
            time.sleep(DOWNLOAD_RETRY_INTERVAL)

    raise RuntimeError(
        f"Download gagal setelah {DOWNLOAD_RETRIES} percobaan. "
        f"Target: {upn} -> {onedrive_path}. Detail: {last_error}"
    )


def inspect_excel(local_path: str):
    """Verifikasi file bisa dibuka dan tampilkan ringkasan isinya."""
    wb = openpyxl.load_workbook(local_path, data_only=True, read_only=True)
    ws = wb.active

    headers = []
    for col in range(1, ws.max_column + 1):
        value = ws.cell(row=5, column=col).value
        if value is not None:
            headers.append(str(value).strip())

    data_rows = 0
    for row in ws.iter_rows(min_row=6, values_only=True):
        if any(cell is not None for cell in row):
            data_rows += 1

    print(f"\nSheet aktif      : {ws.title}")
    print(f"Header (baris 5) : {headers}")
    print(f"Baris data       : {data_rows}")

    if data_rows:
        print("\nPreview 3 baris pertama:")
        for row in ws.iter_rows(min_row=6, max_row=8, values_only=True):
            print(f"  {row}")

    wb.close()


def main():
    args = parse_arguments()
    context = BotContext()

    logger.info("Loading configuration...")
    InitAllSettings.load_config_only(context)

    if not context.onedrive_client:
        logger.error("OneDrive tidak terkonfigurasi — cek kredensial OneDrive di Config.xlsx")
        return

    upn, onedrive_path = resolve_target(context.config, args.filename)
    logger.info(f"Pemilik drive : {upn}")
    logger.info(f"Path OneDrive : {onedrive_path}")

    local_folder = context.config.get("PATH_DOWNLOAD_FILE_INPUT", DEFAULT_LOCAL_INPUT_FOLDER)
    logger.info(f"Folder tujuan download: {local_folder}")
    os.makedirs(local_folder, exist_ok=True)
    base, ext = os.path.splitext(args.filename)
    local_path = os.path.join(local_folder, f"{base}_{context.run_timestamp}{ext or '.xlsx'}")

    logger.info("Downloading...")
    download_file(context.onedrive_client, upn, onedrive_path, local_path)
    logger.info(f"Tersimpan: {local_path} ({os.path.getsize(local_path)} bytes)")

    inspect_excel(local_path)
    logger.info("Test selesai — file input berhasil didownload dan terbaca")


if __name__ == "__main__":
    main()
