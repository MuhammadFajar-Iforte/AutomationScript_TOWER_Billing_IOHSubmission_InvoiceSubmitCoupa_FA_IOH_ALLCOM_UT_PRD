# Automation Script — TOWER Billing: IOH Submission Invoice Submit Coupa (FA_IOH_ALLCOM_UT_PRD)

Robot otomatisasi (RPA) berbasis **Python** untuk submit invoice ke **Coupa Supplier Portal** atas tagihan billing IOH (Indosat Ooredoo Hutchison). Robot menggantikan proses manual tim dalam membuat draft invoice satu per satu di portal Coupa.

> Project ini mengadopsi pola **UiPath REFramework** yang diporting ke Python (state machine: INIT → GET TRANSACTION → PROCESS → END PROCESS).

---

## 📑 Daftar Isi

- [Gambaran Umum](#-gambaran-umum)
- [Arsitektur Project](#-arsitektur-project)
- [Alur Proses](#-alur-proses)
- [Persiapan & Instalasi](#-persiapan--instalasi)
- [Konfigurasi](#-konfigurasi)
- [Struktur Data Input](#-struktur-data-input)
- [Kategori Hasil Proses](#-kategori-hasil-proses)
- [Penanganan Error & Retry](#-penanganan-error--retry)
- [Integrasi Eksternal](#-integrasi-eksternal)
- [Script Testing](#-script-testing)
- [Log & Output](#-log--output)

---

## 🎯 Gambaran Umum

| Item | Keterangan |
|------|------------|
| **Nama Proses** | Submit Invoice Coupa Billing IOH |
| **Tujuan** | Membuat draft invoice otomatis di Coupa Supplier Portal |
| **Bahasa** | Python 3.13 |
| **Web Automation** | Selenium WebDriver (Google Chrome) |
| **Sumber Data** | SQL Server (utama) atau Excel (alternatif) |
| **Integrasi** | OneDrive (MS Graph), WhatsApp (Kafka Confluent), Email (SMTP Office365) |
| **Output** | Draft invoice di Coupa, update status DB & Excel, laporan email + WhatsApp |

---

## 🏗️ Arsitektur Project

```
.
├── main.py                          # Entry point — state machine + retry loop
├── coupa_web_referensi.py           # File referensi implementasi lama (tidak dipakai main.py)
│
├── framework/                       # Lapisan proses (pola REFramework)
│   ├── models.py                    # BotContext, TransactionItem, BusinessRuleException, SystemException
│   ├── InitAllSettings.py           # State INIT — load config, credentials, OneDrive, WhatsApp, browser
│   ├── GetTransactionData.py        # State GET TRANSACTION — ambil & validasi data dari DB/Excel
│   ├── Process.py                   # State PROCESS — 12 langkah pemrosesan 1 invoice
│   ├── CloseAllApplications.py      # State END — upload report, kirim email/WA, tutup browser
│   └── __init__.py
│
├── library/                         # Lapisan utilitas
│   ├── web_utils.py                 # WebAutomationEngine (Selenium) — login, captcha, search PO, isi form
│   ├── onedrive_utils.py            # OneDriveClient (MS Graph) — download/move attachment
│   ├── database_utils.py            # DatabaseScope (SQL Server/MySQL/PostgreSQL/SQLite via SQLAlchemy)
│   ├── whatsapp_utils.py            # WhatsAppNotifier via Kafka Confluent Cloud
│   ├── email_utils.py               # EmailClient (SMTP Office 365)
│   ├── exceptions.py                # BrowserError, CaptchaError, LoginError, dll.
│   └── __init__.py
│
├── Data/
│   ├── Config.xlsx                  # Konfigurasi utama (sheet: Settings + Constants)
│   ├── Account Coupa IOH.xlsx       # Kredensial akun Coupa per perusahaan
│   ├── Master Data.xlsx             # Header mapping & WHT mapping
│   ├── Template File Input.xlsx     # Template input mode Excel
│   └── Query/
│       └── query_get_data_source.sql # Query SQL pengambilan data invoice
│
├── testing/                         # Script testing terpisah
│   ├── test_login.py                # Tes login/logout ke Coupa Portal
│   ├── test_attachment.py           # Tes validasi & download attachment OneDrive
│   ├── test_onedrive.py             # Tes operasi OneDrive (list, download, upload)
│   ├── test_onedrive_connect.py     # Tes koneksi autentikasi OneDrive
│   └── test_wa.py                   # Tes pengiriman notifikasi WhatsApp
├── requirements.txt                 # Dependencies Python
└── HIGH_LEVEL_PROCESS.md            # Dokumen alur proses high-level
```

---

## 🔄 Alur Proses

### 1. Persiapan (INIT)
- Membaca konfigurasi dari `Data/Config.xlsx`
- Memuat kredensial akun Coupa per perusahaan dari `Data/Account Coupa IOH.xlsx`
- Inisialisasi klien OneDrive dan WhatsApp (jika dikonfigurasi)

### 2. Ambil & Validasi Data (GET TRANSACTION)
Mengambil daftar invoice sesuai sumber data yang dipilih:
- **SQL Server** (mode utama, `--source database`) — query dari `Data/Query/query_get_data_source.sql`, hanya data yang belum pernah diproses (`RPA_COUPA_STATUS is null`)
- **OneDrive** (mode alternatif, `--source onedrive`) — download file input Excel dari OneDrive, lalu dibaca seperti mode Excel

Setiap baris kemudian divalidasi kelengkapannya — baris tidak lengkap di-skip dan dicatat alasannya di database & laporan.
Jika tidak ada data valid, robot berhenti.

### 3. Notifikasi Mulai
- Mengirim pesan WhatsApp berisi ringkasan: total PO, jumlah yang diproses, jumlah yang di-skip, dan daftar perusahaan

### 4. Proses Invoice di Coupa (PROCESS)
Robot membuka Chrome, login ke Coupa Supplier Portal, lalu untuk setiap invoice:

1. **Login** dengan akun perusahaan yang sesuai (logout/login otomatis jika perusahaan berubah)
2. **Cari nomor PO** di halaman Orders
3. **Klik "Create Invoice"** pada PO yang ditemukan
4. **Handle popup "Choose Invoicing Details"** (untuk perusahaan tertentu)
5. **Download dokumen pendukung** dari OneDrive sesuai nomor invoice, lalu lampirkan
6. **Isi form invoice**: Invoice Number, Invoice Date, Description, nomor & tanggal faktur pajak, BA Number, WHT Code
7. **Isi Qty** pada setiap invoice line sesuai site & periode tagihan
8. **Isi VAT** pada bagian Totals & Taxes
9. **Klik "Calculate"** untuk menghitung total
10. **Validasi Net Total** — bandingkan total Coupa dengan nilai dari database (toleransi 10)
11. **Klik "Save as Draft"**
12. **Verifikasi** — cari nomor invoice di daftar Invoices untuk memastikan draft tersimpan
13. **Update status** ke database (`RPA_COUPA_STATUS`, `RPA_COUPA_DATE`) dan file Excel

### 5. Penyelesaian (END PROCESS)
- Menutup browser
- Meng-upload file hasil ke OneDrive
- Mengirim **notifikasi WhatsApp** berisi ringkasan akhir
- Mengirim **laporan email** (hasil + link file OneDrive)

---

## ⚙️ Persiapan & Instalasi

### Prasyarat
- Python 3.13
- Google Chrome terinstall
- ODBC Driver 17/18 for SQL Server (untuk mode database)
- Akses jaringan ke Coupa Portal, OneDrive, dan SMTP Office 365

### Instalasi

```bash
# 1. Clone repository
git clone https://github.com/MuhammadFajar-Iforte/AutomationScript_TOWER_Billing_IOHSubmission_InvoiceSubmitCoupa_FA_IOH_ALLCOM_UT_PRD.git
cd AutomationScript_TOWER_Billing_IOHSubmission_InvoiceSubmitCoupa_FA_IOH_ALLCOM_UT_PRD

# 2. (Opsional) Buat virtual environment
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # Linux/Mac

# 3. Install dependencies
pip install -r requirements.txt
```

### Dependencies Utama

| Library | Fungsi |
|---------|--------|
| `selenium` + `webdriver-manager` | Otomasi browser Chrome |
| `openpyxl` + `pandas` | Baca/tulis file Excel |
| `pyodbc` + `sqlalchemy` | Koneksi & query SQL Server |
| `msal` + `requests` | Autentikasi & API Microsoft Graph (OneDrive) |
| `urllib3` | HTTP utilities (notifikasi WhatsApp) |

### Menjalankan Robot

```bash
# Data source dari SQL Server (default)
python main.py

# Data source dari file Excel di OneDrive
python main.py --source onedrive --onedrive-file "RPA/File Input/FileInput.xlsx"
```

**Argumen command line:**

| Argumen | Keterangan |
|---------|------------|
| `--source` | Sumber data transaksi: `database` (default) atau `onedrive` |
| `--onedrive-file` | Path file input Excel di OneDrive (wajib jika `--source onedrive`, kecuali `OneDrive_Input_File` sudah diisi di Config.xlsx) |

> **Catatan mode OneDrive:** File Excel di OneDrive harus mengikuti struktur yang sama dengan `Data/Template File Input.xlsx` (header di baris 5, data mulai baris 6). File akan didownload otomatis ke `RPA/File Input/`, diproses, lalu hasil (Status RPA, dll.) di-upload ke folder `OneDrive_Output_Folder` — file sumber di OneDrive tidak diubah.

---

## 🔧 Konfigurasi

Semua konfigurasi dibaca dari **`Data/Config.xlsx`** (sheet `Settings` dan `Constants`):

| Key Konfigurasi | Keterangan |
|-----------------|------------|
| `DB_Server`, `DB_Database`, `DB_Username`, `DB_Password` | Koneksi SQL Server (kosongkan user/pass untuk Windows Auth) |
| `DB_Input_Query` | Path file query SQL pengambilan data |
| `Coupa_URL` | URL Coupa Supplier Portal (default: `https://supplier.coupahost.com/orders`) |
| `Default_Password` | Password default login Coupa |
| `MaxRetryNumber` | Jumlah maksimal retry untuk System Exception (default: 3) |
| `Master_Data_File` | Path file kredensial akun Coupa |
| `Master_Data_Header_File` | Path file master data header |
| `OneDrive_Client_ID/Secret/Tenant_ID` | Kredensial Microsoft Graph (OAuth 2.0 via MSAL) |
| `User_Principal_Name` | Akun OneDrive yang digunakan |
| `OneDrive_Attachment_Path` | Folder OneDrive sumber dokumen pendukung |
| `OneDrive_Output_Folder` | Folder OneDrive tujuan file laporan |
| `Local_Attachment_Path` | Folder lokal untuk simpan attachment yang didownload |
| `Local_Input_File` | Path file input Excel (mode Excel) |
| `OneDrive_Input_File` | Path file input Excel di OneDrive (alternatif `--onedrive-file` untuk mode `--source onedrive`) |
| `Output_Folder` | Folder lokal output laporan |
| `Email_Enabled/Sender/Password/To/Cc` | Konfigurasi laporan email (SMTP Office 365) |
| `WA_Kafka_Auth`, `WA_App_ID`, `WA_Sent_To` | Kredensial notifikasi WhatsApp (Kafka Confluent Cloud) |
| `Default_VAT` | Kode VAT default |

---

## 📊 Struktur Data Input

### Transaksi (dari SQL Server / Excel)

| Field | Keterangan |
|-------|------------|
| Tower Provider Name | Kode/nama perusahaan (untuk pemilihan akun login) |
| PO Number | Nomor PO yang dicari di Coupa |
| Invoice Number | Nomor invoice |
| Invoice Description | Deskripsi invoice |
| Status Site | Tipe tagihan (Recurring / Project, dll.) |
| BA Number | Nomor BA (Recurring → pakai Invoice Number) |
| Tax Invoice Number / Date | Nomor & tanggal faktur pajak |
| Invoice Amount | Nilai invoice (dibandingkan dengan Net Total Coupa) |
| IOH Site ID / Site ID Active | ID site IOH |
| WHT Code | Kode PPh (WHT) |
| Qty | Jumlah yang diisi per invoice line |
| Payment Period Start | Periode awal tagihan |

### Kredensial (`Account Coupa IOH.xlsx`)

| Kolom 1 | Kolom 2 | Kolom 3 |
|---------|---------|---------|
| Company Code | Username | Password |

---

## 📈 Kategori Hasil Proses

| Status | Keterangan |
|--------|------------|
| **Success** | Invoice berhasil dibuat sebagai draft di Coupa |
| **Business Exception** | Gagal karena masalah data/bisnis — **tidak di-retry** |
| **System Exception** | Gagal karena masalah sistem — **di-retry** sesuai konfigurasi |
| **Skipped (Validation)** | Data tidak lengkap, dilewati sebelum diproses |
| **Attachment Not Found** | Dokumen pendukung tidak ditemukan di OneDrive |

---

## ⚠️ Penanganan Error & Retry

Robot membedakan dua jenis exception:

| Tipe | Contoh | Perlakuan |
|------|--------|-----------|
| `BusinessRuleException` (BRE-xxx) | PO not found, attachment missing, kredensial tidak ada | Tidak di-retry, langsung lanjut ke transaksi berikutnya |
| `SystemException` (SYS-xxx) | Timeout, koneksi putus, error element web | Di-retry hingga `MaxRetryNumber`, lalu ditandai gagal & diupdate ke DB |

Penanganan khusus:
- **Captcha muncul** → robot menunggu dan mengirim notifikasi WhatsApp agar diselesaikan manual (maks. 4 percobaan)
- **Pop-up "too many logins"** → dideteksi dan ditangani otomatis
- **Browser reload** otomatis setelah error untuk memulihkan state
- Semua error di-screenshot ke folder `screenshots/`

---

## 🔌 Integrasi Eksternal

| Sistem | Fungsi | Teknologi |
|--------|--------|-----------|
| **SQL Server** | Sumber data invoice & penyimpanan status hasil proses | SQLAlchemy + pyodbc |
| **OneDrive** | Dokumen pendukung invoice & penyimpanan laporan hasil | Microsoft Graph API (MSAL, OAuth 2.0) |
| **Coupa Supplier Portal** | Tempat pembuatan draft invoice | Selenium WebDriver |
| **WhatsApp** | Notifikasi start, captcha, dan ringkasan akhir | Kafka REST API (Confluent Cloud) |
| **Email** | Laporan hasil proses (HTML) | SMTP Office 365 |

---

## 🧪 Script Testing

Script test berdiri sendiri untuk menguji komponen tertentu secara terisolasi:

| File | Fungsi |
|------|--------|
| `test_login.py` | Tes login/logout ke Coupa Portal |
| `test_attachment.py` | Tes validasi & download attachment OneDrive |
| `test_onedrive.py` | Tes operasi OneDrive (list, download, upload) |
| `test_onedrive_connect.py` | Tes koneksi autentikasi OneDrive |
| `test_wa.py` | Tes pengiriman notifikasi WhatsApp |

Semua script testing ada di folder `testing/` dan dijalankan dari root project:

```bash
python testing/test_login.py
```

---

## 📝 Log & Output

- **Log eksekusi**: disimpan di folder `logs/` dengan format `coupa_automation_YYYY-MM-DD_HH-MM-SS.txt`
- **Screenshot error**: folder `screenshots/`
- **Update database**: kolom `RPA_COUPA_STATUS` (status/remark) dan `RPA_COUPA_DATE` (timestamp)
- **Update Excel**: kolom Invoice Date, Status RPA, dan Modified Date pada file input
- **Laporan akhir**: file Excel di-upload ke OneDrive + laporan ringkasan via email & WhatsApp
