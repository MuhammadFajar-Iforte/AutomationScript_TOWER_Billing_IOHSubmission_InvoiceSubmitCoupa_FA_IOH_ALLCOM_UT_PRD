# Submit Invoice Coupa Billing IOH - High Level Process

## Tujuan
Robot ini mengotomatiskan proses submit invoice ke Coupa Supplier Portal untuk tagihan billing IOH. Tujuannya mengurangi pekerjaan manual tim dalam membuat draft invoice satu per satu di portal Coupa.

## Alur Proses Secara Umum

### 1. Persiapan
Robot membaca konfigurasi dari file pengaturan, lalu mengambil daftar invoice yang perlu diproses langsung dari database. Data yang diambil hanya yang belum pernah diproses sebelumnya.

### 2. Validasi Data
Sebelum membuka browser, robot memeriksa kelengkapan setiap baris data. Baris yang datanya tidak lengkap akan dilewati (skip) dan dicatat alasannya, baik di database maupun di laporan. Jika tidak ada data yang valid untuk diproses, robot langsung berhenti.

### 3. Notifikasi Mulai
Setelah validasi selesai, robot mengirim notifikasi WhatsApp berisi ringkasan: berapa total PO, berapa yang akan diproses, dan berapa yang dilewati.

### 4. Proses Invoice di Coupa
Robot membuka browser Chrome, lalu login ke Coupa Supplier Portal menggunakan akun perusahaan yang sesuai. Setelah login, robot langsung diarahkan ke halaman **Orders**.

Untuk setiap invoice, robot melakukan langkah berikut di portal Coupa:

1. **Di halaman Orders** — robot mencari nomor PO menggunakan kolom pencarian
2. **Klik tombol "Create Invoice"** pada PO yang ditemukan untuk mulai membuat invoice
3. **Menangani popup "Choose Invoicing Details"** (jika muncul untuk perusahaan tertentu)
4. **Mengambil dokumen pendukung** dari OneDrive sesuai nomor invoice, lalu melampirkannya
5. **Mengisi form invoice** di halaman Create Invoice:
   - Invoice Number, Invoice Date, Description
   - Nomor dan tanggal faktur pajak
   - Lampiran scan invoice dan dokumen pendukung
6. **Mengisi jumlah (Qty)** pada setiap baris invoice (invoice lines), disesuaikan dengan site dan jumlah periode tagihan
7. **Mengisi VAT** pada bagian Totals & Taxes
8. **Klik tombol "Calculate"** untuk menghitung total invoice
9. **Validasi Net Total** — membandingkan total di Coupa dengan nilai yang diharapkan dari database
10. **Klik tombol "Save as Draft"** untuk menyimpan invoice sebagai draft
11. **Verifikasi** — robot masuk ke halaman daftar Invoices, mencari nomor invoice tadi untuk memastikan draft benar-benar tersimpan

Setelah selesai, robot me-refresh halaman dan lanjut ke invoice berikutnya. Jika perusahaan berikutnya berbeda, robot akan logout dan login ulang dengan akun yang sesuai.

### 5. Penanganan Masalah
Jika terjadi kendala saat proses:
- **Captcha muncul** → robot menunggu dan mengirim notifikasi WhatsApp agar diselesaikan manual
- **Dokumen tidak lengkap** → invoice dilewati, dicatat sebagai "Attachment Not Found"
- **Error sistem** → robot mencoba ulang beberapa kali sebelum menandai gagal

Setiap hasil proses (berhasil, dilewati, atau gagal) dicatat kembali ke database dan laporan.

### 6. Selesai
Setelah semua invoice diproses, robot:
- Menutup browser
- Mengirim notifikasi WhatsApp berisi ringkasan akhir (berhasil, error, dokumen tidak ditemukan, dilewati)
- Mengirim laporan via email

## Kategori Hasil
Setiap invoice akan masuk ke salah satu kategori:
- **Success** — invoice berhasil dibuat sebagai draft di Coupa
- **Error** — gagal karena masalah sistem
- **Attachment Not Found** — dokumen pendukung tidak ditemukan
- **Skipped (Validation)** — data tidak lengkap, dilewati

## Sumber Data & Integrasi
- **Database (SQL Server)** — sumber data invoice dan tempat update status hasil proses
- **OneDrive** — tempat menyimpan dokumen pendukung dan hasil laporan
- **Coupa Supplier Portal** — tempat invoice dibuat
- **WhatsApp** — notifikasi start, captcha, dan ringkasan akhir
- **Email** — laporan hasil proses
