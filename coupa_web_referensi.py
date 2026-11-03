"""
Coupa Web Automation Module (Selenium)

Menangani otomasi browser untuk Coupa Supplier Portal menggunakan Selenium WebDriver.
Referensi: web_utils REFERENSI.py

Flow:
- Buka browser Chrome (start-maximized)
- Navigate ke Coupa Supplier Portal
- Login dengan email dan password (handle captcha manual)
- Verifikasi login berhasil
- Logout
- Close browser
"""

import time
import os
import platform
import logging
from typing import Optional
from pathlib import Path
from datetime import datetime

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    TimeoutException,
    NoSuchElementException,
    WebDriverException,
    ElementClickInterceptedException
)
from webdriver_manager.chrome import ChromeDriverManager

logger = logging.getLogger(__name__)

# ==========================================
# CONSTANTS
# ==========================================
COUPA_URL = "https://supplier.coupahost.com/invoices/"
COUPA_INVOICES_URL = "https://supplier.coupahost.com/invoices/"

# Timeouts (seconds)
BROWSER_OPEN_TIMEOUT = 30
PAGE_LOAD_TIMEOUT = 60
LOGIN_TIMEOUT = 30

# Captcha
CAPTCHA_CHECK_INTERVAL = 5  # cek setiap 5 detik
CAPTCHA_WAIT_TIMES = [30, 60, 120]  # waktu tunggu per attempt
CAPTCHA_MAX_RETRY = 3

# Login
LOGIN_MAX_RETRY = 2

# Speed multiplier (0.5 = lebih cepat)
SPEED_MULTIPLIER = 0.5


# ==========================================
# COUPA BROWSER CLASS
# ==========================================

class CoupaBrowser:
    """
    Browser handler untuk Coupa Supplier Portal menggunakan Selenium.
    Referensi pola dari: web_utils REFERENSI.py
    """

    def __init__(self, headless: bool = False, screenshot_dir: str = "Data/Screenshots"):
        self.headless = headless
        self.screenshot_dir = Path(screenshot_dir)
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)

        self.driver: Optional[webdriver.Chrome] = None
        self.wait: Optional[WebDriverWait] = None

    def _sleep(self, seconds: float) -> None:
        """Sleep dengan speed multiplier."""
        actual = max(0.3, seconds * SPEED_MULTIPLIER)
        time.sleep(actual)

    def _install_session_inactivity_handler(self) -> None:
        """Pasang watcher untuk otomatis melanjutkan sesi Coupa sebelum logout."""
        if not self.driver:
            return

        try:
            self.driver.switch_to.default_content()
            self.driver.execute_script("""
                (() => {
                    const continueSession = () => {
                        const button = document.getElementById('continue_session_btn');
                        if (button && button.offsetParent !== null && !button.disabled) {
                            button.click();
                            window.__coupaLastSessionContinue = Date.now();
                        }
                    };

                    if (!window.__coupaSessionWatcherInstalled) {
                        window.__coupaSessionWatcherInstalled = true;
                        const observer = new MutationObserver(continueSession);
                        observer.observe(document.documentElement, {
                            childList: true,
                            subtree: true,
                            attributes: true
                        });
                        window.__coupaSessionWatcher = observer;
                        window.__coupaSessionTimer = window.setInterval(continueSession, 1000);
                    }

                    continueSession();
                })();
            """)
            logger.info("Session inactivity handler aktif")
        except Exception as e:
            logger.warning(f"Gagal memasang session inactivity handler: {e}")

    # ------------------------------------------
    # BROWSER LIFECYCLE
    # ------------------------------------------

    def open_browser(self) -> None:
        """Buka Chrome browser (start-maximized)."""
        logger.info("Membuka Chrome browser...")

        options = Options()
        options.add_argument("--start-maximized")
        options.add_argument("--disable-extensions")
        options.add_argument("--disable-popup-blocking")
        options.add_argument("--disable-infobars")
        options.add_argument("--disable-notifications")

        if self.headless:
            options.add_argument("--headless=new")

        # Webdriver manager setup
        os.environ['WDM_SSL_VERIFY'] = '0'
        if platform.system() == 'Windows' and platform.machine().endswith('64'):
            os.environ['WDM_ARCHITECTURE'] = '64'

        service = Service(ChromeDriverManager().install())
        self.driver = webdriver.Chrome(service=service, options=options)
        self.driver.set_page_load_timeout(PAGE_LOAD_TIMEOUT)
        self.wait = WebDriverWait(self.driver, BROWSER_OPEN_TIMEOUT)

        logger.info("Browser berhasil dibuka")

    def close_browser(self) -> None:
        """Tutup browser dan cleanup (kill langsung, tanpa logout)."""
        logger.info("Menutup browser...")
        if self.driver:
            try:
                self.driver.quit()
            except Exception as e:
                logger.warning(f"Error saat tutup browser: {e}")
            finally:
                self.driver = None
                self.wait = None
        logger.info("Browser ditutup")

    @staticmethod
    def kill_all_chrome() -> None:
        """
        Kill semua proses ChromeDriver milik user saat ini.
        Hanya kill chromedriver, bukan chrome (agar tidak ganggu chrome user).
        """
        import subprocess
        import getpass
        
        current_user = getpass.getuser()
        logger.info(f"Kill chromedriver milik user: {current_user}")
        
        try:
            cmd = f'taskkill /F /FI "USERNAME eq {current_user}" /IM chromedriver.exe'
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=10)
            if result.returncode == 0:
                logger.info("Killed: chromedriver.exe")
            else:
                logger.debug("Tidak ada proses chromedriver yang aktif")
        except Exception as e:
            logger.warning(f"Gagal kill chromedriver: {e}")

    def navigate(self, url: str) -> None:
        """Navigate ke URL."""
        if not self.driver:
            raise Exception("Browser belum dibuka")

        logger.info(f"Navigate ke: {url}")
        self.driver.get(url)
        self.wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        logger.info("Halaman loaded")

    def take_screenshot(self, name: str) -> None:
        """Ambil screenshot untuk debugging."""
        if not self.driver:
            return
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filepath = self.screenshot_dir / f"{name}_{timestamp}.png"
            self.driver.save_screenshot(str(filepath))
            logger.info(f"Screenshot: {filepath}")
        except Exception as e:
            logger.warning(f"Gagal ambil screenshot: {e}")

    # ------------------------------------------
    # LOGIN
    # ------------------------------------------

    def login(self, username: str, password: str) -> None:
        """
        Login ke Coupa Supplier Portal dengan retry.

        Jika credential salah → raise Exception (bot STOP).
        Jika error teknis → retry maks LOGIN_MAX_RETRY kali.

        Args:
            username: Email Coupa
            password: Password Coupa
        """
        last_error = None

        for attempt in range(1, LOGIN_MAX_RETRY + 1):
            try:
                logger.info(f"Login attempt {attempt}/{LOGIN_MAX_RETRY} sebagai: {username}")

                if attempt == 2:
                    logger.info("Reload halaman sebelum retry...")
                    self.driver.refresh()
                    self._sleep(3)

                self._perform_login(username, password)
                self._install_session_inactivity_handler()
                logger.info("Login berhasil!")
                return

            except Exception as e:
                error_msg = str(e).lower()
                # Credential error → langsung stop
                if "credential" in error_msg or "invalid" in error_msg or "salah" in error_msg:
                    raise Exception(f"LOGIN GAGAL: Username atau password salah. Bot dihentikan. Detail: {e}")

                last_error = e
                logger.warning(f"Login attempt {attempt} gagal: {e}")
                if attempt < LOGIN_MAX_RETRY:
                    logger.info("Akan retry login...")
                    try:
                        self.driver.switch_to.default_content()
                    except:
                        pass
                    continue

        raise Exception(f"Login gagal setelah {LOGIN_MAX_RETRY} attempt: {last_error}")

    def _perform_login(self, username: str, password: str) -> None:
        """
        Eksekusi login flow.

        Flow Coupa:
        1. Isi email → klik Continue (atau langsung ada password)
        2. Handle captcha jika muncul
        3. Isi password (bisa di iframe identityLoginIframe)
        4. Klik Login
        5. Handle popup "too many active logins"
        6. Verify login berhasil
        """
        if not self.driver:
            raise Exception("Browser belum dibuka")

        # Tunggu halaman load
        self._sleep(3)

        # Tunggu field email
        logger.info("Menunggu field email...")
        wait_long = WebDriverWait(self.driver, 60)
        email_field = wait_long.until(
            EC.presence_of_element_located((By.ID, "email"))
        )
        wait_long.until(EC.element_to_be_clickable((By.ID, "email")))

        # Isi email
        email_field.clear()
        email_field.send_keys(username)
        logger.info(f"Email diisi: {username}")
        self._sleep(1)

        # Cek captcha sebelum Continue
        if self._is_captcha_present():
            self._handle_captcha("sebelum Continue")

        # Cek apakah password field sudah visible
        password_fields = self.driver.find_elements(By.ID, "password")
        password_visible = password_fields and password_fields[0].is_displayed()

        if password_visible:
            # Kedua field langsung visible
            logger.info("Password field langsung visible, isi password...")
            password_fields[0].clear()
            password_fields[0].send_keys(password)

            login_button = wait_long.until(EC.element_to_be_clickable((By.ID, "login-submit")))
            login_button.click()
            logger.info("Klik Login")
        else:
            # Klik Continue dulu
            logger.info("Klik Continue...")
            self._click_continue()

            # Tunggu redirect
            self._sleep(3)
            try:
                wait_long.until(lambda d: d.execute_script("return document.readyState") == "complete")
            except:
                pass
            self._sleep(2)

            # Cek captcha setelah Continue
            if self._is_captcha_present():
                self._handle_captcha("setelah Continue")
                self._sleep(2)
                # Mungkin perlu klik Continue lagi setelah captcha
                try:
                    self._click_continue()
                    self._sleep(3)
                    wait_long.until(lambda d: d.execute_script("return document.readyState") == "complete")
                    self._sleep(2)
                except:
                    pass

            # Cari password field (bisa di iframe atau main page)
            self._fill_password_and_login(password)

        # Tunggu response setelah Login
        self._sleep(3)

        # Cek captcha setelah Login
        if self._is_captcha_present():
            self._handle_captcha("setelah Login")

        # Handle popup "too many active logins"
        self._handle_too_many_logins()

        # Verify login berhasil
        self._verify_login_success()

    def _click_continue(self) -> None:
        """Klik tombol Continue."""
        continue_selectors = [
            (By.XPATH, "//button[span[text()='Continue']]"),
            (By.XPATH, "//button[contains(., 'Continue')]"),
            (By.XPATH, "//button[@type='submit' and not(@id='login-submit')]"),
            (By.CSS_SELECTOR, "button.s-login[type='submit']")
        ]

        for by, selector in continue_selectors:
            try:
                btn = WebDriverWait(self.driver, 5).until(
                    EC.element_to_be_clickable((by, selector))
                )
                if btn:
                    btn.click()
                    logger.info("Tombol Continue diklik")
                    return
            except:
                continue

        raise Exception("Tombol Continue tidak ditemukan")

    def _fill_password_and_login(self, password: str) -> None:
        """Cari field password (main page atau iframe), isi, lalu klik Login."""
        wait_long = WebDriverWait(self.driver, 30)
        password_field = None

        # Coba cari iframe dulu
        iframe_selectors = [
            (By.ID, "identityLoginIframe"),
            (By.NAME, "identityLoginIframe"),
            (By.CSS_SELECTOR, "iframe[id*='identity']"),
            (By.CSS_SELECTOR, "iframe[id*='login']"),
        ]

        iframe_found = False
        for by, selector in iframe_selectors:
            try:
                iframe = WebDriverWait(self.driver, 5).until(
                    EC.presence_of_element_located((by, selector))
                )
                if iframe:
                    self.driver.switch_to.frame(iframe)
                    logger.info(f"Switch ke iframe: {selector}")
                    iframe_found = True
                    break
            except:
                continue

        # Cari password field
        password_selectors = [
            (By.ID, "password"),
            (By.NAME, "password"),
            (By.CSS_SELECTOR, "input[type='password']"),
        ]

        for by, selector in password_selectors:
            try:
                password_field = WebDriverWait(self.driver, 10).until(
                    EC.presence_of_element_located((by, selector))
                )
                if password_field and password_field.is_displayed():
                    logger.info(f"Password field ditemukan: {selector}")
                    break
                password_field = None
            except:
                continue

        # Jika tidak ketemu di iframe, coba balik ke main
        if not password_field and iframe_found:
            self.driver.switch_to.default_content()
            logger.info("Kembali ke main content, cari ulang...")
            for by, selector in password_selectors:
                try:
                    password_field = WebDriverWait(self.driver, 5).until(
                        EC.presence_of_element_located((by, selector))
                    )
                    if password_field and password_field.is_displayed():
                        break
                    password_field = None
                except:
                    continue

        if not password_field:
            # Cek captcha menghalangi
            if self._is_captcha_present():
                self._handle_captcha("password field blocked")
                # Coba lagi
                for by, selector in password_selectors:
                    try:
                        password_field = WebDriverWait(self.driver, 10).until(
                            EC.presence_of_element_located((by, selector))
                        )
                        if password_field and password_field.is_displayed():
                            break
                        password_field = None
                    except:
                        continue

        if not password_field:
            self.take_screenshot("password_not_found")
            raise Exception("Field password tidak ditemukan")

        # Tunggu clickable lalu isi
        self._sleep(1)
        password_field.clear()
        password_field.send_keys(password)
        logger.info("Password diisi")

        # Klik Login
        login_selectors = [
            (By.ID, "login-submit"),
            (By.XPATH, "//button[span[text()='Login']]"),
            (By.XPATH, "//button[contains(., 'Login')]"),
            (By.XPATH, "//button[@type='submit']"),
            (By.CSS_SELECTOR, "button.pf-c-button.pf-m-primary"),
        ]

        for by, selector in login_selectors:
            try:
                btn = WebDriverWait(self.driver, 5).until(
                    EC.element_to_be_clickable((by, selector))
                )
                if btn:
                    btn.click()
                    logger.info("Tombol Login diklik")
                    return
            except:
                continue

        raise Exception("Tombol Login tidak ditemukan")

    # ------------------------------------------
    # CAPTCHA
    # ------------------------------------------

    def _is_captcha_present(self) -> bool:
        """Cek apakah captcha muncul dan perlu diselesaikan."""
        try:
            try:
                self.driver.switch_to.default_content()
            except:
                pass

            # Cek challenge iframe (puzzle gambar)
            challenge_selectors = [
                (By.XPATH, "//iframe[contains(@src, 'recaptcha') and contains(@src, 'bframe')]"),
                (By.XPATH, "//iframe[contains(@title, 'recaptcha challenge')]"),
                (By.CSS_SELECTOR, ".rc-imageselect"),
                (By.CSS_SELECTOR, ".rc-doscaptcha-body"),
            ]

            for by, selector in challenge_selectors:
                try:
                    elements = self.driver.find_elements(by, selector)
                    if elements and any(e.is_displayed() for e in elements):
                        return True
                except:
                    continue

            # Cek unchecked reCAPTCHA checkbox
            try:
                anchor_iframes = self.driver.find_elements(
                    By.XPATH, "//iframe[contains(@src, 'recaptcha') and contains(@src, 'anchor')]"
                )
                for iframe in anchor_iframes:
                    try:
                        if not iframe.is_displayed():
                            continue
                        self.driver.switch_to.frame(iframe)
                        unchecked = self.driver.find_elements(
                            By.CSS_SELECTOR, ".recaptcha-checkbox[aria-checked='false']"
                        )
                        if unchecked and any(e.is_displayed() for e in unchecked):
                            self.driver.switch_to.default_content()
                            return True
                        checked = self.driver.find_elements(
                            By.CSS_SELECTOR, ".recaptcha-checkbox[aria-checked='true']"
                        )
                        if checked and any(e.is_displayed() for e in checked):
                            self.driver.switch_to.default_content()
                            return False
                        self.driver.switch_to.default_content()
                    except:
                        try:
                            self.driver.switch_to.default_content()
                        except:
                            pass
            except:
                try:
                    self.driver.switch_to.default_content()
                except:
                    pass

            # Cek pesan Coupa tentang captcha
            captcha_messages = [
                (By.XPATH, "//*[contains(text(), 'complete reCAPTCHA')]"),
                (By.XPATH, "//*[contains(text(), 'For added security')]"),
                (By.XPATH, "//*[contains(text(), 'Recaptcha requires verification')]"),
            ]
            for by, selector in captcha_messages:
                try:
                    elements = self.driver.find_elements(by, selector)
                    if elements and any(e.is_displayed() for e in elements):
                        return True
                except:
                    continue

            return False

        except Exception as e:
            logger.debug(f"Error cek captcha: {e}")
            return False

    def _handle_captcha(self, context: str) -> None:
        """
        Handle captcha — tunggu user selesaikan manual.
        Cek setiap CAPTCHA_CHECK_INTERVAL detik.
        """
        for attempt in range(CAPTCHA_MAX_RETRY):
            wait_time = CAPTCHA_WAIT_TIMES[attempt]

            logger.warning(
                f"CAPTCHA terdeteksi ({context})! "
                f"Silakan selesaikan captcha secara MANUAL. "
                f"Menunggu maks {wait_time} detik... (attempt {attempt + 1}/{CAPTCHA_MAX_RETRY})"
            )

            elapsed = 0
            while elapsed < wait_time:
                time.sleep(CAPTCHA_CHECK_INTERVAL)
                elapsed += CAPTCHA_CHECK_INTERVAL

                if not self._is_captcha_present():
                    logger.info(f"Captcha selesai setelah {elapsed} detik!")
                    return

                remaining = wait_time - elapsed
                if remaining > 0:
                    logger.debug(f"Captcha masih ada, sisa {remaining} detik...")

            logger.warning(f"Captcha masih ada setelah {wait_time} detik (attempt {attempt + 1})")

        self.take_screenshot("captcha_timeout")
        raise Exception(f"Captcha tidak diselesaikan setelah {CAPTCHA_MAX_RETRY} attempt ({context})")

    # ------------------------------------------
    # POST-LOGIN HANDLING
    # ------------------------------------------

    def _handle_too_many_logins(self) -> None:
        """Handle popup 'too many active logins'."""
        try:
            popup_indicators = [
                (By.XPATH, "//*[contains(text(), 'too many active logins')]"),
                (By.XPATH, "//*[contains(text(), 'signed out from one of your other sessions')]"),
            ]

            popup_found = False
            for by, selector in popup_indicators:
                try:
                    element = self.driver.find_element(by, selector)
                    if element and element.is_displayed():
                        popup_found = True
                        logger.info("Popup 'too many active logins' terdeteksi")
                        break
                except:
                    continue

            if not popup_found:
                return

            # Klik tombol Log in / login_button
            btn_selectors = [
                (By.ID, "login_button"),
                (By.XPATH, "//button[text()='Log in']"),
                (By.XPATH, "//button[contains(text(), 'Log in')]"),
                (By.CSS_SELECTOR, "button.btn-primary"),
                (By.CSS_SELECTOR, "button[type='submit']"),
            ]

            for by, selector in btn_selectors:
                try:
                    elements = self.driver.find_elements(by, selector)
                    for elem in elements:
                        if elem.is_displayed() and elem.is_enabled():
                            try:
                                elem.click()
                            except ElementClickInterceptedException:
                                self.driver.execute_script("arguments[0].click();", elem)
                            logger.info("Popup 'too many logins' di-handle")
                            self._sleep(2)
                            return
                except:
                    continue

        except Exception as e:
            logger.debug(f"Tidak ada popup 'too many logins': {e}")

    def _verify_login_success(self) -> None:
        """Verifikasi login berhasil."""
        try:
            try:
                self.driver.switch_to.default_content()
            except:
                pass

            # Tunggu URL berubah dari login page
            WebDriverWait(self.driver, 30).until(
                EC.any_of(
                    EC.url_contains("/user/home"),
                    EC.url_contains("/dashboard"),
                    EC.url_contains("/invoices"),
                    EC.url_contains("/orders"),
                    EC.url_contains("/setup"),
                    EC.url_contains("/csp"),
                    EC.url_contains("/home"),
                    lambda d: "/sessions/new" not in d.current_url and "/login" not in d.current_url,
                    EC.presence_of_element_located((By.XPATH, "//a[contains(@href, '/logout')]")),
                    EC.presence_of_element_located((By.XPATH, "//a[contains(text(), 'Sign Out')]")),
                )
            )

            logger.info(f"Login berhasil! URL: {self.driver.current_url}")

        except TimeoutException:
            current_url = self.driver.current_url

            # Mungkin sudah login tapi element tidak ketemu
            if "/sessions/new" not in current_url and "/login" not in current_url:
                logger.info(f"Login kemungkinan berhasil (URL: {current_url})")
                return

            # Cek error message
            error_msg = self._get_login_error_message()
            if error_msg:
                raise Exception(f"Invalid credential: {error_msg}")

            self.take_screenshot("login_verify_failed")
            raise Exception("Tidak bisa verifikasi login berhasil (timeout)")

    def _get_login_error_message(self) -> Optional[str]:
        """Ambil error message dari halaman login."""
        try:
            self.driver.switch_to.default_content()
        except:
            pass

        error_selectors = [
            (By.CLASS_NAME, "alert-danger"),
            (By.CLASS_NAME, "error-message"),
            (By.CLASS_NAME, "flash-error"),
            (By.CSS_SELECTOR, ".pf-c-alert__title"),
            (By.XPATH, "//*[contains(text(), 'Invalid')]"),
            (By.XPATH, "//*[contains(text(), 'incorrect')]"),
        ]

        # Cek di main page dan di iframe
        for _ in range(2):
            for by, selector in error_selectors:
                try:
                    elements = self.driver.find_elements(by, selector)
                    if elements and elements[0].text:
                        return elements[0].text.strip()
                except:
                    continue
            # Coba di iframe
            try:
                iframe = self.driver.find_element(By.ID, "identityLoginIframe")
                self.driver.switch_to.frame(iframe)
            except:
                break

        try:
            self.driver.switch_to.default_content()
        except:
            pass

        return None

    # ------------------------------------------
    # LOGOUT (tidak dipakai, langsung kill browser)
    # ------------------------------------------


# ==========================================
# INVOICE ACTIONS
# ==========================================


def fill_invoice_general_info(browser: CoupaBrowser, item_data: dict, config: dict) -> None:
    """
    Isi form General Info di halaman Create Invoice.
    
    Fields:
    - Invoice # (invoice_header_invoice_number) ← Invoice No(3)
    - Invoice Date (invoice_header_local_invoice_date) ← Invoice Date (4) → convert ke mm/dd/yyyy
    - Currency (invoice_header_currency_id) ← Currency (5)
    - Image Scan (invoice_header_image_scan) ← Upload file dari Invoice Merge Path
    - Attachment ← Upload file dari Faktur Path
    """
    if not browser.driver:
        raise Exception("Browser belum dibuka")
    
    driver = browser.driver
    logger.info("Mengisi form General Info...")
    
    # Pastikan di default content dulu
    driver.switch_to.default_content()
    
    # Masuk ke iframe enterprise_frame (form ada di dalam iframe)
    try:
        iframe = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.ID, "enterprise_frame"))
        )
        driver.switch_to.frame(iframe)
        logger.info("Switched ke iframe enterprise_frame")
    except TimeoutException:
        logger.info("Tidak ada iframe enterprise_frame, lanjut di main content")
    
    # ------------------------------------------
    # 1. Invoice Number
    # ------------------------------------------
    invoice_num = _get_column_value(item_data, ["invoice no(3)", "invoice no", "invoice number"])
    if invoice_num:
        _fill_input_field(driver, "invoice_header_invoice_number", invoice_num)
        logger.info(f"Invoice # diisi: {invoice_num}")
    else:
        logger.warning("Kolom Invoice Number tidak ditemukan di data")
    
    # ------------------------------------------
    # 2. Invoice Date (convert to mm/dd/yyyy)
    # ------------------------------------------
    invoice_date = _get_column_value(item_data, ["invoice date (4)", "invoice date", "tanggal invoice"])
    if invoice_date:
        formatted_date = _format_date_mmddyyyy(invoice_date)
        _fill_input_field(driver, "invoice_header_local_invoice_date", formatted_date)
        logger.info(f"Invoice Date diisi: {formatted_date}")
    else:
        logger.warning("Kolom Invoice Date tidak ditemukan di data")
    
    # ------------------------------------------
    # 3. Currency (select dropdown)
    # ------------------------------------------
    currency = _get_column_value(item_data, ["currency (5)", "currency", "mata uang"])
    if currency:
        _select_dropdown(driver, "invoice_header_currency_id", str(currency).strip())
        logger.info(f"Currency dipilih: {currency}")
    else:
        logger.warning("Kolom Currency tidak ditemukan di data")
    
    # ------------------------------------------
    # 4. Requester Email
    # ------------------------------------------
    requester_email = _get_column_value(item_data, ["requester email (8)", "requester email", "email requester"])
    if requester_email:
        _fill_input_field(driver, "invoice_header_requester_attributes_email", requester_email)
        logger.info(f"Requester Email diisi: {requester_email}")
    else:
        logger.warning("Kolom Requester Email tidak ditemukan di data")
    
    # ------------------------------------------
    # 5. Requester Name
    # ------------------------------------------
    requester_name = _get_column_value(item_data, ["requester name (9)", "requester name", "nama requester"])
    if requester_name:
        _fill_input_field(driver, "invoice_header_requester_attributes_name", requester_name)
        logger.info(f"Requester Name diisi: {requester_name}")
    else:
        logger.warning("Kolom Requester Name tidak ditemukan di data")
    
    # ------------------------------------------
    # 6. Agree with Disclaimer (checkbox - harus dicentang)
    # ------------------------------------------
    _check_checkbox(driver, "invoice_header_custom_field_8")
    logger.info("Agree with Disclaimer dicentang")
    
    # ------------------------------------------
    # 7. Invoice Type (PT XL Axiata)
    # ------------------------------------------
    invoice_type = _get_column_value(item_data, ["invoice type (11)", "invoice type", "tipe invoice"])
    if invoice_type:
        _select_dropdown(driver, "invoice_header_custom_field_17", str(invoice_type).strip())
        logger.info(f"Invoice Type dipilih: {invoice_type}")
    else:
        logger.info("Kolom Invoice Type tidak ditemukan/kosong, skip")
    
    # ------------------------------------------
    # 8. Image Scan (upload Faktur Pajak / FP)
    # ------------------------------------------
    faktur_path = config.get("Faktur Path", "")
    faktur_ext = config.get("Extension Faktur Path", "pdf")
    
    if faktur_path and invoice_num:
        image_scan_file = Path(faktur_path) / f"{invoice_num}FP.{faktur_ext}"
        try:
            if image_scan_file.exists():
                _upload_file(driver, "invoice_header_image_scan", str(image_scan_file))
                logger.info(f"Image Scan (FP) uploaded: {image_scan_file}")
            else:
                logger.warning(f"File Image Scan (FP) tidak ditemukan: {image_scan_file}")
        except PermissionError as e:
            logger.warning(f"File locked, coba upload langsung: {e}")
            try:
                _upload_file(driver, "invoice_header_image_scan", str(image_scan_file))
                logger.info(f"Image Scan (FP) uploaded (ignore lock): {image_scan_file}")
            except Exception as e2:
                logger.error(f"Gagal upload Image Scan (FP): {e2}")
    
    # ------------------------------------------
    # 9. Attachment (upload Invoice)
    # ------------------------------------------
    merge_path = config.get("Invoice Merge Path", "")
    merge_ext = config.get("Extension Merge Path", "pdf")
    
    if merge_path and invoice_num:
        attachment_file = Path(merge_path) / f"{invoice_num}.{merge_ext}"
        try:
            if attachment_file.exists():
                _upload_attachment(driver, str(attachment_file))
                logger.info(f"Attachment (Invoice) uploaded: {attachment_file}")
            else:
                logger.warning(f"File Attachment (Invoice) tidak ditemukan: {attachment_file}")
        except PermissionError as e:
            logger.warning(f"File locked, coba upload langsung: {e}")
            try:
                _upload_attachment(driver, str(attachment_file))
                logger.info(f"Attachment (Invoice) uploaded (ignore lock): {attachment_file}")
            except Exception as e2:
                logger.error(f"Gagal upload Attachment (Invoice): {e2}")
    
    logger.info("Form General Info selesai diisi")


def _get_column_value(data, possible_names: list) -> Optional[str]:
    """
    Ambil value dari dict atau Series berdasarkan kemungkinan nama kolom.
    Case-insensitive, trim spasi, support suffix (x).
    
    Args:
        data: dict (item_data) atau pd.Series (row)
        possible_names: list nama kolom yang mungkin
    
    Returns:
        Value sebagai string, atau None jika tidak ditemukan
    """
    # Handle both dict and pd.Series
    if hasattr(data, 'items'):
        items = data.items()
    elif hasattr(data, 'index'):
        items = ((col, data[col]) for col in data.index)
    else:
        return None
    
    for key, value in items:
        key_clean = str(key).strip().lower()
        key_no_suffix = key_clean.split("(")[0].strip()
        
        for name in possible_names:
            name_clean = name.strip().lower()
            name_no_suffix = name_clean.split("(")[0].strip()
            
            if key_clean == name_clean or key_no_suffix == name_no_suffix:
                if pd.notna(value) and str(value).strip():
                    return str(value).strip()
    return None


# Alias untuk backward compatibility
_get_detail_column_value = _get_column_value


def _clean_number_string(value) -> str:
    """
    Bersihkan value angka dari suffix '.0' yang muncul karena pandas membaca
    angka sebagai float (misal 5100012345.0 -> "5100012345").
    """
    text = str(value).strip()
    if text.endswith(".0") and text[:-2].replace("-", "").isdigit():
        return text[:-2]
    return text


def _format_date_mmddyyyy(date_value) -> str:
    """
    Convert date ke format mm/dd/yyyy.
    Handle:
    - datetime object
    - pandas Timestamp (termasuk yang ada waktu 00:00:00)
    - Excel date serial number
    - String format "DD-MMM-YY" (misal: "19-Aug-26")
    - String format "YYYY-MM-DD"
    - String format "YYYY-MM-DD HH:MM:SS" (pandas Timestamp as string)
    """
    import pandas as pd
    from datetime import datetime
    
    if pd.isna(date_value):
        return ""
    
    # Jika sudah datetime
    if isinstance(date_value, datetime):
        return date_value.strftime("%m/%d/%Y")
    
    # Jika pandas Timestamp (punya method strftime)
    if hasattr(date_value, 'strftime'):
        try:
            return date_value.strftime("%m/%d/%Y")
        except Exception:
            pass
    
    # Jika string
    date_str = str(date_value).strip()
    
    # Coba parse berbagai format (urut dari yang paling spesifik)
    date_formats = [
        "%Y-%m-%d %H:%M:%S",  # 2026-08-19 00:00:00 (pandas Timestamp as string)
        "%Y-%m-%d",           # 2026-08-19
        "%d-%b-%y",           # 19-Aug-26
        "%d-%b-%Y",           # 19-Aug-2026
        "%d/%m/%Y",           # 19/08/2026
        "%m/%d/%Y",           # 08/19/2026
        "%d-%m-%Y",           # 19-08-2026
        "%Y/%m/%d",           # 2026/08/19
    ]
    
    for fmt in date_formats:
        try:
            parsed = datetime.strptime(date_str, fmt)
            return parsed.strftime("%m/%d/%Y")
        except ValueError:
            continue
    
    # Jika tidak bisa parse, return as-is (mungkin sudah format yang benar)
    logger.warning(f"Tidak bisa parse date: {date_str}, gunakan as-is")
    return date_str


def _find_element(driver, selectors: list, timeout: int = 10, clickable: bool = False):
    """Cari element pertama yang tersedia dari beberapa selector."""
    for by, selector in selectors:
        try:
            if clickable:
                def first_clickable(current_driver):
                    for element in current_driver.find_elements(by, selector):
                        if element.is_displayed() and element.is_enabled():
                            return element
                    return False

                return WebDriverWait(driver, timeout).until(first_clickable)

            return WebDriverWait(driver, timeout).until(
                EC.presence_of_element_located((by, selector))
            )
        except (TimeoutException, NoSuchElementException):
            continue
    return None


def _continue_session_if_visible(driver, restore_enterprise_frame: bool = True) -> bool:
    """Klik Continue Session di halaman utama dan pulihkan iframe jika diperlukan."""
    handled = False

    try:
        driver.switch_to.default_content()
        buttons = driver.find_elements(By.ID, "continue_session_btn")
        button = next((item for item in buttons if item.is_displayed() and item.is_enabled()), None)

        if button:
            button.click()
            WebDriverWait(driver, 5).until(
                EC.invisibility_of_element_located((By.ID, "sessionTimeout-modal"))
            )
            handled = True
            logger.info("Popup session inactivity ditutup, sesi dilanjutkan")
        else:
            # Watcher JavaScript mungkin sudah menutup popup tepat sebelum pengecekan ini.
            handled = bool(driver.execute_script("""
                const lastContinue = window.__coupaLastSessionContinue || 0;
                return Date.now() - lastContinue < 5000;
            """))
    finally:
        if restore_enterprise_frame:
            driver.switch_to.default_content()
            iframe = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.ID, "enterprise_frame"))
            )
            driver.switch_to.frame(iframe)

    return handled


def _click_element(
    driver,
    selectors: list,
    timeout: int = 10,
    restore_enterprise_frame: bool = True
) -> bool:
    """Klik element dan retry sekali jika terhalang popup session inactivity."""
    for by, selector in selectors:
        try:
            element = _find_element(driver, [(by, selector)], timeout, clickable=True)
            if not element:
                continue
            element.click()
            return True
        except ElementClickInterceptedException:
            if not _continue_session_if_visible(driver, restore_enterprise_frame):
                raise

            # Element harus dicari ulang setelah context iframe dipulihkan.
            element = _find_element(driver, [(by, selector)], timeout, clickable=True)
            if not element:
                continue
            element.click()
            return True

    return False


def _fill_input_field(driver, element_id: str, value: str) -> None:
    """Isi input field berdasarkan ID."""
    field = WebDriverWait(driver, 10).until(
        EC.presence_of_element_located((By.ID, element_id))
    )
    field.clear()
    field.send_keys(value)


def _check_checkbox(driver, element_id: str) -> None:
    """Centang checkbox berdasarkan ID (jika belum dicentang)."""
    checkbox = WebDriverWait(driver, 10).until(
        EC.presence_of_element_located((By.ID, element_id))
    )
    if not checkbox.is_selected() and not _click_element(driver, [(By.ID, element_id)]):
        raise NoSuchElementException(f"Checkbox tidak bisa diklik: {element_id}")


def _select_dropdown(driver, element_id: str, visible_text: str) -> None:
    """Select dropdown by visible text (exact or partial match)."""
    from selenium.webdriver.support.ui import Select
    
    select_element = WebDriverWait(driver, 10).until(
        EC.presence_of_element_located((By.ID, element_id))
    )
    select = Select(select_element)
    
    # Exact match first
    try:
        select.select_by_visible_text(visible_text)
        return
    except:
        pass
    
    # Partial match (case-insensitive)
    target = visible_text.lower()
    for option in select.options:
        if target in option.text.lower():
            select.select_by_visible_text(option.text)
            return
    
    raise Exception(f"Option '{visible_text}' tidak ditemukan di dropdown '{element_id}'")


def _upload_file(driver, element_id: str, file_path: str) -> None:
    """Upload file ke input type=file berdasarkan ID."""
    file_input = driver.find_element(By.ID, element_id)
    file_input.send_keys(file_path)
    time.sleep(2)


def _upload_attachment(driver, file_path: str) -> None:
    """
    Upload attachment via Attachments section.
    Klik 'File' link → input file → upload.
    """
    try:
        # Cari section Attachments dan klik link 'File'
        file_link_selectors = [
            (By.XPATH, "//a[contains(text(), 'File')]"),
            (By.XPATH, "//a[@data-attachment-type='file']"),
            (By.CSS_SELECTOR, "a.add-attachment[data-attachment-type='file']"),
            (By.XPATH, "//div[contains(@class, 'attachments')]//a[contains(text(), 'File')]"),
        ]
        
        if _click_element(driver, file_link_selectors):
            time.sleep(1)
        
        # Cari input file (bisa visible setelah klik, atau sudah ada)
        attachment_input_selectors = [
            (By.CSS_SELECTOR, "input[type='file'][name*='attachment']"),
            (By.CSS_SELECTOR, "input[type='file'][id*='attachment']"),
            (By.XPATH, "//input[@type='file' and contains(@name, 'attachment')]"),
            (By.XPATH, "//div[contains(@class, 'attachments')]//input[@type='file']"),
        ]
        
        for by, selector in attachment_input_selectors:
            try:
                file_inputs = driver.find_elements(by, selector)
                for inp in file_inputs:
                    try:
                        inp.send_keys(file_path)
                        time.sleep(2)
                        return
                    except:
                        continue
            except:
                continue
        
        logger.warning("Attachment input tidak ditemukan, skip upload attachment")
        
    except Exception as e:
        logger.warning(f"Gagal upload attachment: {e}")


import pandas as pd


# ==========================================
# ATTACHMENT VALIDATION
# ==========================================

def validate_attachments(item_data: dict, config: dict) -> tuple[bool, str]:
    """
    Validasi apakah file attachment (Image Scan dan Faktur) tersedia sebelum Create Blank Invoice.
    
    Args:
        item_data: Data invoice dari Summary (berisi Invoice No)
        config: Dictionary config (berisi path folder dan extension)
    
    Returns:
        tuple: (is_valid: bool, error_message: str)
        - (True, "") jika semua file tersedia
        - (False, "Attachment not found: ...") jika ada file yang tidak ditemukan
    """
    missing_files = []
    
    # Ambil Invoice Number dari data
    invoice_num = _get_column_value(item_data, ["invoice no(3)", "invoice no", "invoice number"])
    if not invoice_num:
        return False, "Invoice Number tidak ditemukan di data"
    
    invoice_num = str(invoice_num).strip()
    logger.info(f"Validasi attachment untuk invoice: {invoice_num}")
    
    # 1. Validasi Image Scan (Faktur Pajak / FP)
    faktur_path = config.get("Faktur Path", "")
    faktur_ext = config.get("Extension Faktur Path", "pdf")
    
    if faktur_path:
        image_scan_file = Path(faktur_path) / f"{invoice_num}FP.{faktur_ext}"
        try:
            if not image_scan_file.exists():
                missing_files.append(f"Faktur Pajak (FP): {image_scan_file}")
                logger.warning(f"File Faktur Pajak (FP) tidak ditemukan: {image_scan_file}")
            else:
                logger.info(f"File Faktur Pajak (FP) OK: {image_scan_file}")
        except PermissionError:
            logger.info(f"File Faktur Pajak (FP) locked (assume OK): {image_scan_file}")
        except Exception as e:
            logger.warning(f"Error cek Faktur Pajak (FP): {e}")
            missing_files.append(f"Faktur Pajak (FP): {image_scan_file} (error: {e})")
    else:
        logger.warning("Config 'Faktur Path' kosong, skip validasi Faktur Pajak")
    
    # 2. Validasi Attachment (Invoice)
    merge_path = config.get("Invoice Merge Path", "")
    merge_ext = config.get("Extension Merge Path", "pdf")
    
    if merge_path:
        attachment_file = Path(merge_path) / f"{invoice_num}.{merge_ext}"
        try:
            if not attachment_file.exists():
                missing_files.append(f"Invoice: {attachment_file}")
                logger.warning(f"File Invoice tidak ditemukan: {attachment_file}")
            else:
                logger.info(f"File Invoice OK: {attachment_file}")
        except PermissionError:
            logger.info(f"File Invoice locked (assume OK): {attachment_file}")
        except Exception as e:
            logger.warning(f"Error cek Invoice: {e}")
            missing_files.append(f"Invoice: {attachment_file} (error: {e})")
    else:
        logger.warning("Config 'Invoice Merge Path' kosong, skip validasi Invoice")
    
    # Return hasil
    if missing_files:
        error_msg = "Attachment not found: " + "; ".join(missing_files)
        logger.error(error_msg)
        return False, error_msg
    
    logger.info("Semua file attachment tersedia")
    return True, ""


def click_create_blank_invoice(browser: CoupaBrowser) -> None:
    """
    Klik tombol 'Create Blank Invoice' di dalam iframe enterprise_frame.
    """
    if not browser.driver:
        raise Exception("Browser belum dibuka")

    logger.info("Klik 'Create Blank Invoice'...")
    
    driver = browser.driver

    # Switch ke default content lalu masuk iframe enterprise_frame
    driver.switch_to.default_content()
    iframe = WebDriverWait(driver, 15).until(
        EC.presence_of_element_located((By.ID, "enterprise_frame"))
    )
    driver.switch_to.frame(iframe)

    # Klik button
    if not _click_element(driver, [(By.ID, "create_blank_invoice")], timeout=10):
        raise NoSuchElementException("Tombol 'Create Blank Invoice' tidak ditemukan")
    logger.info("Tombol 'Create Blank Invoice' diklik")
    time.sleep(3)
    
    # Handle popup "Choose Invoicing Details" jika muncul
    _handle_invoicing_details_popup(browser)


def _handle_invoicing_details_popup(browser: CoupaBrowser) -> None:
    """
    Handle popup 'Choose Invoicing Details' yang kadang muncul setelah Create Blank Invoice.
    Popup ini adalah jQuery UI Dialog dengan struktur:
    - div.ui-dialog (container)
    - h2.ui-dialog-title "Choose Invoicing Details"
    - select#invoice_from_address_select (Legal Entity dropdown)
    - button#addresses_picker_submit (Save button - hidden sampai dipilih)
    
    Flow:
    1. Pilih Legal Entity option pertama (index 1)
    2. Field lain (Remit-To, Ship From) auto terisi
    3. Klik Save
    """
    driver = browser.driver
    
    logger.info("Mencari popup 'Choose Invoicing Details'...")

    # Tunggu sebentar untuk popup muncul
    time.sleep(2)
    
    # Coba deteksi popup di berbagai context
    popup_found = False
    in_iframe = False
    
    # Method 1: Cek di default content dulu (popup jQuery UI biasanya di luar iframe)
    try:
        driver.switch_to.default_content()
    except Exception:
        pass
    
    popup_selectors = [
        (By.ID, "invoice_from_address_select"),
        (By.XPATH, "//div[contains(@class, 'ui-dialog')]//select[@id='invoice_from_address_select']"),
        (By.CSS_SELECTOR, "div.ui-dialog select#invoice_from_address_select"),
        (By.XPATH, "//h2[contains(text(), 'Choose Invoicing Details')]"),
    ]
    
    for by, selector in popup_selectors:
        try:
            elem = WebDriverWait(driver, 2).until(EC.presence_of_element_located((by, selector)))
            if elem.is_displayed():
                popup_found = True
                logger.info(f"Popup terdeteksi di default content via: {selector}")
                break
        except TimeoutException:
            continue
        except Exception:
            continue
    
    # Method 2: Cek di iframe enterprise_frame
    if not popup_found:
        try:
            driver.switch_to.default_content()
            iframe = WebDriverWait(driver, 3).until(
                EC.presence_of_element_located((By.ID, "enterprise_frame"))
            )
            driver.switch_to.frame(iframe)
            
            for by, selector in popup_selectors:
                try:
                    elem = WebDriverWait(driver, 2).until(EC.presence_of_element_located((by, selector)))
                    if elem.is_displayed():
                        popup_found = True
                        in_iframe = True
                        logger.info(f"Popup terdeteksi di iframe via: {selector}")
                        break
                except TimeoutException:
                    continue
                except Exception:
                    continue
        except Exception as e:
            logger.debug(f"Error cek iframe: {e}")
    
    if not popup_found:
        logger.info("Popup 'Choose Invoicing Details' tidak muncul, lanjut...")
        # Pastikan kembali ke iframe untuk proses selanjutnya
        try:
            driver.switch_to.default_content()
            iframe = driver.find_element(By.ID, "enterprise_frame")
            driver.switch_to.frame(iframe)
        except:
            pass
        return

    logger.info("Popup 'Choose Invoicing Details' terdeteksi - memproses...")

    # Pilih Legal Entity option pertama (index 1, karena index 0 = "Select")
    from selenium.webdriver.support.ui import Select
    
    try:
        select_element = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.ID, "invoice_from_address_select"))
        )
        
        # Scroll ke element jika perlu
        driver.execute_script("arguments[0].scrollIntoView(true);", select_element)
        time.sleep(0.5)
        
        select = Select(select_element)
        
        # Log semua options
        logger.info(f"Legal Entity options: {len(select.options)}")
        for i, opt in enumerate(select.options):
            logger.debug(f"  [{i}] {opt.text}")
        
        # Pastikan ada options selain "Select"
        if len(select.options) > 1:
            # Pilih index 1 (option pertama setelah "Select")
            select.select_by_index(1)
            time.sleep(1)
            logger.info(f"Legal Entity dipilih: {select.first_selected_option.text}")
        else:
            logger.warning("Legal Entity dropdown tidak punya options!")
            return
    except Exception as e:
        logger.error(f"Gagal pilih Legal Entity: {e}")
        browser.take_screenshot("legal_entity_error")
        return

    # Tunggu field lain auto terisi (Remit-To, Ship From di-enable setelah pilih Legal Entity)
    logger.info("Menunggu field lain auto terisi...")
    time.sleep(3)

    # Klik Save button - tombol ini awalnya hidden, muncul setelah pilih Legal Entity
    logger.info("Mencari tombol Save...")
    
    save_selectors = [
        # Button di dalam form (yang asli)
        (By.CSS_SELECTOR, "button#addresses_picker_submit.blue"),
        (By.ID, "addresses_picker_submit"),
        # Di dalam dialog buttonpane (jQuery UI clone)
        (By.XPATH, "//div[contains(@class, 'ui-dialog-buttonpane')]//button[@id='addresses_picker_submit']"),
        (By.XPATH, "//div[contains(@class, 'ui-dialog-buttonset')]//button[contains(@class, 'blue')]"),
        (By.XPATH, "//button[.//span[text()='Save']]"),
        (By.CSS_SELECTOR, "button.blue[type='submit']"),
    ]

    save_clicked = _click_element(
        driver,
        save_selectors,
        timeout=10,
        restore_enterprise_frame=in_iframe
    )
    if save_clicked:
        logger.info("Popup 'Choose Invoicing Details' — Save diklik")
        time.sleep(3)

    if not save_clicked:
        # Fallback: submit form langsung via JavaScript
        logger.warning("Save button tidak bisa diklik, coba submit form...")
        try:
            driver.execute_script("""
                var form = document.querySelector('form.addresses_picker_form');
                if (form) {
                    form.submit();
                } else {
                    var btn = document.getElementById('addresses_picker_submit');
                    if (btn) btn.click();
                }
            """)
            logger.info("Popup 'Choose Invoicing Details' — form submitted via JavaScript")
            time.sleep(3)
        except Exception as e:
            logger.error(f"Gagal submit form: {e}")
            browser.take_screenshot("save_button_error")


# ==========================================
# INVOICE LINES
# ==========================================

def fill_invoice_lines(browser: CoupaBrowser, invoice_num: str, df_detail: pd.DataFrame) -> None:
    """
    Isi form Lines di halaman Create Invoice.
    
    Looping untuk setiap line detail dari invoice yang sama.
    
    Fields per line:
    - Type (dropdown) → "Amt" (InvoiceAmountLine)
    - Description (input) ← kolom "Description (13)"
    - Price (input) ← kolom "Price (14)"
    - GR Document Number (textarea) ← kolom "GR Document Number (15)"
    - WHT Code (select) ← kolom "WHT Code (16)"
    - Invoice Line Type (select) ← kolom "Invoice Line Type (17)"
    
    Args:
        browser: CoupaBrowser instance
        invoice_num: Nomor invoice untuk filter detail
        df_detail: DataFrame invoice detail (semua invoice)
    """
    if not browser.driver:
        raise Exception("Browser belum dibuka")
    
    driver = browser.driver
    logger.info(f"Mengisi Invoice Lines untuk: {invoice_num}")
    
    # Pastikan di iframe enterprise_frame
    try:
        driver.switch_to.default_content()
        iframe = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.ID, "enterprise_frame"))
        )
        driver.switch_to.frame(iframe)
    except TimeoutException:
        logger.info("Tidak ada iframe enterprise_frame, lanjut di main content")
    
    # Filter detail untuk invoice ini
    # Cari kolom Invoice No di detail (bisa ada spasi/suffix)
    invoice_col = None
    for col in df_detail.columns:
        col_clean = str(col).strip().lower()
        if "invoice no" in col_clean or "invoice number" in col_clean:
            invoice_col = col
            break
    
    if not invoice_col:
        logger.warning("Kolom Invoice No tidak ditemukan di df_detail!")
        return
    
    # Filter rows yang match invoice_num
    invoice_num_clean = str(invoice_num).strip().lower()
    detail_rows = df_detail[
        df_detail[invoice_col].astype(str).str.strip().str.lower() == invoice_num_clean
    ]
    
    if detail_rows.empty:
        logger.warning(f"Tidak ada detail untuk invoice: {invoice_num}")
        return
    
    logger.info(f"Ditemukan {len(detail_rows)} line(s) untuk invoice: {invoice_num}")
    
    # Loop setiap line
    for idx, (_, row) in enumerate(detail_rows.iterrows()):
        line_num = idx + 1
        logger.info(f"Mengisi Line {line_num}/{len(detail_rows)}")
        
        # Jika bukan line pertama, klik "Add Line" dulu
        if idx > 0:
            _click_add_line(driver)
        
        # Isi field-field line
        _fill_single_line(driver, row, line_num)
    
    logger.info(f"Selesai mengisi {len(detail_rows)} line(s)")


def _click_add_line(driver) -> None:
    """Klik tombol 'Add Line' untuk menambah line baru."""
    selectors = [
        (By.ID, "add_invoice_line"),
        (By.XPATH, "//a[@id='add_invoice_line']")
    ]
    if not _click_element(driver, selectors, timeout=10):
        raise NoSuchElementException("Tombol Add Line tidak ditemukan")

    logger.info("Klik 'Add Line'")
    time.sleep(2)  # Tunggu line baru muncul


def _fill_single_line(driver, row: pd.Series, line_num: int) -> None:
    """
    Isi satu line invoice.
    
    Element ID pattern: invoice_header_invoice_lines_attributes_{index}_fieldname
    Index adalah dynamic (diambil dari element yang ada di DOM).
    """
    # Cari line wrapper yang terakhir (line baru ada di bawah)
    line_wrappers = driver.find_elements(
        By.CSS_SELECTOR, "div.s-invoiceLineTableRowRoot[index]"
    )
    
    if not line_wrappers:
        logger.error("Tidak ada line wrapper ditemukan!")
        return
    
    # Ambil line terakhir (yang baru ditambahkan)
    last_line = line_wrappers[-1]
    line_index = last_line.get_attribute("index")
    
    logger.info(f"Mengisi line dengan index: {line_index}")
    
    # ------------------------------------------
    # 1. Type → pilih "Amt" (InvoiceAmountLine)
    # ------------------------------------------
    type_id = f"invoice_header_invoice_lines_attributes_{line_index}_type"
    try:
        _select_dropdown_by_value(driver, type_id, "InvoiceAmountLine")
        logger.info("Type dipilih: Amt")
        time.sleep(1)  # Tunggu form update setelah ganti type
    except Exception as e:
        logger.warning(f"Gagal pilih Type: {e}")
    
    # ------------------------------------------
    # 2. Description
    # ------------------------------------------
    description = _get_detail_column_value(row, ["description (13)", "description"])
    if description:
        desc_id = f"invoice_header_invoice_lines_attributes_{line_index}_description"
        try:
            _fill_input_field(driver, desc_id, description)
            logger.info(f"Description diisi: {description[:50]}...")
        except Exception as e:
            logger.warning(f"Gagal isi Description: {e}")
    
    # ------------------------------------------
    # 3. Price
    # ------------------------------------------
    price = _get_detail_column_value(row, ["price (14)", "price", "amount"])
    if price:
        price_id = f"invoice_header_invoice_lines_attributes_{line_index}_price"
        # Format price (hapus comma jika ada, pastikan decimal point)
        price_str = str(price).replace(",", "").strip()
        try:
            _fill_input_field(driver, price_id, price_str)
            logger.info(f"Price diisi: {price_str}")
        except Exception as e:
            logger.warning(f"Gagal isi Price: {e}")
    
    # ------------------------------------------
    # 4. GR Document Number (textarea)
    # ------------------------------------------
    gr_doc = _get_detail_column_value(row, ["gr document number (15)", "gr document number", "gr doc", "gr number"])
    if gr_doc:
        gr_value = _clean_number_string(gr_doc)
        gr_id = f"invoice_header_invoice_lines_attributes_{line_index}_custom_field_13"
        try:
            textarea = driver.find_element(By.ID, gr_id)
            textarea.clear()
            textarea.send_keys(gr_value)
            logger.info(f"GR Document Number diisi: {gr_value}")
        except Exception as e:
            logger.warning(f"Gagal isi GR Document Number: {e}")
    
    # ------------------------------------------
    # 5. WHT Code (select with chosen.js)
    # ------------------------------------------
    wht_code = _get_detail_column_value(row, ["wht code (16)", "wht code", "wht"])
    if wht_code:
        wht_id = f"invoice_header_invoice_lines_attributes_{line_index}_custom_field_7_id"
        wht_code_clean = str(wht_code).strip()
        logger.info(f"Mengisi WHT Code: '{wht_code_clean}' ke element '{wht_id}'")
        
        # Tunggu element WHT Code tersedia (chosen.js mungkin butuh waktu load)
        try:
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.ID, wht_id))
            )
        except TimeoutException:
            logger.warning(f"Element WHT Code '{wht_id}' tidak ditemukan")
        
        try:
            _select_chosen_dropdown(driver, wht_id, wht_code_clean)
            logger.info(f"WHT Code dipilih: {wht_code_clean}")
        except Exception as e:
            logger.warning(f"Gagal pilih WHT Code '{wht_code_clean}': {e}")
    
    # ------------------------------------------
    # 6. Invoice Line Type (select)
    # ------------------------------------------
    line_type = _get_detail_column_value(row, ["invoice line type (17)", "invoice line type", "line type"])
    if line_type:
        line_type_id = f"invoice_header_invoice_lines_attributes_{line_index}_custom_field_10"
        try:
            _select_dropdown(driver, line_type_id, str(line_type).strip())
            logger.info(f"Invoice Line Type dipilih: {line_type}")
        except Exception as e:
            logger.warning(f"Gagal pilih Invoice Line Type: {e}")


def _select_dropdown_by_value(driver, element_id: str, value: str) -> None:
    """Select dropdown by value attribute."""
    from selenium.webdriver.support.ui import Select
    
    select_element = WebDriverWait(driver, 10).until(
        EC.presence_of_element_located((By.ID, element_id))
    )
    Select(select_element).select_by_value(value)


def _select_chosen_dropdown(driver, element_id: str, search_text: str, max_retry: int = 3) -> None:
    """
    Select option dari dropdown yang menggunakan chosen.js library.
    Chosen.js hide original select dan buat custom div.
    
    Strategy:
    1. Coba match dengan data-ext-ref-code (exact match untuk WHT Code seperti "4J")
    2. Coba match dengan text option (partial match)
    3. Set value via JavaScript dan trigger chosen:updated
    4. Verify value benar-benar ke-set, retry jika gagal
    """
    if not search_text or not search_text.strip():
        logger.debug(f"Skip chosen dropdown {element_id}: search_text kosong")
        return
    
    search_text = str(search_text).strip()
    search_lower = search_text.lower()
    
    for attempt in range(1, max_retry + 1):
        try:
            # Cari select element
            select_elem = WebDriverWait(driver, 5).until(
                EC.presence_of_element_located((By.ID, element_id))
            )
            
            # Scroll ke element untuk memastikan visible
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", select_elem)
            
            # Cari semua options
            options = select_elem.find_elements(By.TAG_NAME, "option")
            target_value = None
            matched_text = None
            
            # Strategy 1: Exact match dengan data-ext-ref-code (WHT Code)
            for opt in options:
                ext_code = (opt.get_attribute("data-ext-ref-code") or "").strip()
                if ext_code.lower() == search_lower:
                    target_value = opt.get_attribute("value")
                    matched_text = opt.text
                    break
            
            # Strategy 2: Text starts with search_text (misal "4J" matches "4J-Jasa...")
            if not target_value:
                for opt in options:
                    opt_text = opt.text.strip()
                    if opt_text.lower().startswith(search_lower + "-") or opt_text.lower().startswith(search_lower + " "):
                        target_value = opt.get_attribute("value")
                        matched_text = opt_text
                        break
            
            # Strategy 3: Exact match text
            if not target_value:
                for opt in options:
                    opt_text = opt.text.strip()
                    if opt_text.lower() == search_lower:
                        target_value = opt.get_attribute("value")
                        matched_text = opt_text
                        break
            
            # Strategy 4: Partial match (contains)
            if not target_value:
                for opt in options:
                    opt_text = opt.text.strip().lower()
                    opt_title = (opt.get_attribute("title") or "").strip().lower()
                    if search_lower in opt_text or search_lower in opt_title:
                        target_value = opt.get_attribute("value")
                        matched_text = opt.text
                        break
            
            if not target_value:
                logger.warning(f"Option '{search_text}' tidak ditemukan di dropdown '{element_id}'")
                return
            
            # Set value via JavaScript dan trigger events
            driver.execute_script(
                """
                var select = arguments[0];
                var value = arguments[1];
                
                // Set value
                select.value = value;
                
                // Trigger native change event
                var changeEvent = new Event('change', { bubbles: true });
                select.dispatchEvent(changeEvent);
                
                // Trigger input event
                var inputEvent = new Event('input', { bubbles: true });
                select.dispatchEvent(inputEvent);
                
                // Trigger chosen:updated jika menggunakan chosen.js
                if (typeof jQuery !== 'undefined') {
                    jQuery(select).trigger('chosen:updated');
                    jQuery(select).trigger('change');
                }
                """,
                select_elem,
                target_value
            )
            
            # Verify value sudah ke-set
            current_value = select_elem.get_attribute("value")
            if current_value == target_value:
                logger.debug(f"Chosen dropdown {element_id} set to: {matched_text} (attempt {attempt})")
                return
            else:
                logger.debug(f"Verify failed: expected {target_value}, got {current_value} (attempt {attempt})")
                if attempt < max_retry:
                    continue
        
        except TimeoutException:
            logger.warning(f"Dropdown '{element_id}' tidak ditemukan (attempt {attempt})")
            if attempt < max_retry:
                continue
        except Exception as e:
            logger.warning(f"Gagal select chosen dropdown {element_id}: {e} (attempt {attempt})")
            if attempt < max_retry:
                continue
    
    logger.warning(f"Gagal set chosen dropdown {element_id} setelah {max_retry} attempt")


# ==========================================
# CALCULATE & SAVE
# ==========================================

def fill_tax_and_calculate(browser: CoupaBrowser, config: dict) -> None:
    """
    Isi Tax Rate dan klik Calculate, tunggu total berubah dari 0.
    
    Steps:
    1. Isi Tax Rate dari config["Tax Rate"]
    2. Klik tombol Calculate
    3. Tunggu totalWithTaxes berubah dari 0 (max 5 menit, cek setiap 5 detik)
    
    Args:
        browser: CoupaBrowser instance
        config: Dictionary config (harus ada key "Tax Rate")
    """
    if not browser.driver:
        raise Exception("Browser belum dibuka")
    
    driver = browser.driver
    logger.info("Mengisi Tax Rate dan Calculate...")
    
    # Pastikan di iframe enterprise_frame
    try:
        driver.switch_to.default_content()
        iframe = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.ID, "enterprise_frame"))
        )
        driver.switch_to.frame(iframe)
    except TimeoutException:
        logger.info("Tidak ada iframe enterprise_frame, lanjut di main content")
    
    # ------------------------------------------
    # 1. Isi Tax Rate
    # ------------------------------------------
    tax_rate = config.get("Tax Rate", "")
    if tax_rate:
        tax_rate_str = str(tax_rate).strip()
        
        # Cari input tax_rate (ID bisa dinamis: tax_rate_1, tax_rate_2, dll)
        tax_input_selectors = [
            (By.ID, "tax_rate_1"),
            (By.CSS_SELECTOR, "input.tax_rate.s-tax_rate"),
            (By.CSS_SELECTOR, "input[id^='tax_rate_']"),
            (By.XPATH, "//input[contains(@class, 'tax_rate')]"),
        ]
        
        tax_input = None
        for by, selector in tax_input_selectors:
            try:
                tax_input = WebDriverWait(driver, 5).until(
                    EC.presence_of_element_located((by, selector))
                )
                if tax_input:
                    break
            except TimeoutException:
                continue
        
        if tax_input:
            tax_input.clear()
            tax_input.send_keys(tax_rate_str)
            logger.info(f"Tax Rate diisi: {tax_rate_str}")
        else:
            logger.warning("Input Tax Rate tidak ditemukan")
    else:
        logger.warning("Config 'Tax Rate' tidak diset, skip isi tax rate")
    
    # ------------------------------------------
    # 2. Klik Calculate
    # ------------------------------------------
    calculate_selectors = [
        (By.ID, "calculate_button"),
        (By.XPATH, "//a[@id='calculate_button']"),
        (By.XPATH, "//a[.//span[text()='Calculate']]"),
        (By.CSS_SELECTOR, "a#calculate_button"),
    ]
    
    calc_clicked = _click_element(driver, calculate_selectors, timeout=10)
    if calc_clicked:
        logger.info("Tombol 'Calculate' diklik")
    else:
        raise Exception("Tombol Calculate tidak ditemukan atau tidak bisa diklik")
    
    # ------------------------------------------
    # 3. Tunggu Total berubah dari 0
    # ------------------------------------------
    _wait_for_total_calculated(driver)
    
    logger.info("Tax Rate dan Calculate selesai!")


def _wait_for_total_calculated(driver, max_wait: int = 300, interval: int = 5) -> None:
    """
    Tunggu totalWithTaxes berubah dari 0.
    
    Args:
        driver: WebDriver instance
        max_wait: Maksimal waktu tunggu dalam detik (default 300 = 5 menit)
        interval: Interval cek dalam detik (default 5)
    """
    logger.info(f"Menunggu total dihitung (max {max_wait}s, cek setiap {interval}s)...")
    
    elapsed = 0
    while elapsed < max_wait:
        try:
            # Cari element totalWithTaxes
            total_elem = driver.find_element(By.ID, "totalWithTaxes")
            total_text = total_elem.text.strip()
            
            # Parse total (hapus comma dan spasi)
            total_clean = total_text.replace(",", "").replace(" ", "").replace(".", "")
            
            # Cek apakah bukan 0
            if total_clean and total_clean != "0" and total_clean != "0.00":
                logger.info(f"Total sudah dihitung: {total_text} (setelah {elapsed}s)")
                return
            
            logger.debug(f"Total masih 0, menunggu... ({elapsed}s)")
            
        except NoSuchElementException:
            logger.debug(f"Element totalWithTaxes belum ada, menunggu... ({elapsed}s)")
        except Exception as e:
            logger.debug(f"Error cek total: {e}")
        
        time.sleep(interval)
        elapsed += interval
    
    # Timeout - cek sekali lagi
    try:
        total_elem = driver.find_element(By.ID, "totalWithTaxes")
        total_text = total_elem.text.strip()
        logger.warning(f"Timeout menunggu total, nilai terakhir: {total_text}")
    except:
        pass
    
    raise Exception(f"Timeout: Total tidak berubah setelah {max_wait} detik")


def save_as_draft(browser: CoupaBrowser) -> None:
    """
    Klik tombol 'Save as Draft'.
    
    Args:
        browser: CoupaBrowser instance
    """
    if not browser.driver:
        raise Exception("Browser belum dibuka")
    
    driver = browser.driver
    logger.info("Menyimpan sebagai Draft...")
    
    # Pastikan di iframe enterprise_frame
    try:
        driver.switch_to.default_content()
        iframe = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.ID, "enterprise_frame"))
        )
        driver.switch_to.frame(iframe)
    except TimeoutException:
        logger.info("Tidak ada iframe enterprise_frame, lanjut di main content")
    
    # Klik Save as Draft
    save_selectors = [
        (By.ID, "submit_draft"),
        (By.XPATH, "//button[@id='submit_draft']"),
        (By.XPATH, "//button[.//span[text()='Save as Draft']]"),
        (By.CSS_SELECTOR, "button#submit_draft"),
    ]
    
    save_clicked = _click_element(driver, save_selectors, timeout=10)
    if save_clicked:
        logger.info("Tombol 'Save as Draft' diklik")

    if not save_clicked:
        raise Exception("Tombol 'Save as Draft' tidak ditemukan atau tidak bisa diklik")
    
    # Tunggu save selesai (halaman redirect atau ada notifikasi)
    time.sleep(3)
    logger.info("Save as Draft selesai!")


# ==========================================
# VERIFY DRAFT
# ==========================================

def verify_draft_amount(browser: CoupaBrowser, invoice_num: str, df_detail: pd.DataFrame, config: dict) -> str:
    """
    Verifikasi total amount setelah Save as Draft.
    
    Steps:
    1. Tunggu table muncul di halaman invoice list
    2. Cari row pertama, cek apakah Invoice # sama dengan yang diproses
    3. Ambil Total dari Coupa
    4. Hitung expected total dari df_detail: sum(Price) * (1 + Tax Rate%)
    5. Bandingkan dan return remark
    
    Args:
        browser: CoupaBrowser instance
        invoice_num: Nomor invoice yang diproses
        df_detail: DataFrame invoice detail
        config: Dictionary config (ada "Tax Rate")
    
    Returns:
        str: Remark hasil verifikasi
    """
    if not browser.driver:
        raise Exception("Browser belum dibuka")
    
    driver = browser.driver
    logger.info(f"Verifikasi Draft amount untuk: {invoice_num}")
    
    # Tunggu halaman redirect (table invoice list muncul)
    time.sleep(3)
    
    # Pastikan di iframe enterprise_frame
    try:
        driver.switch_to.default_content()
        iframe = WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.ID, "enterprise_frame"))
        )
        driver.switch_to.frame(iframe)
    except TimeoutException:
        logger.info("Tidak ada iframe enterprise_frame, lanjut di main content")
    
    # ------------------------------------------
    # 1. Tunggu table muncul
    # ------------------------------------------
    try:
        WebDriverWait(driver, 30).until(
            EC.presence_of_element_located((By.ID, "invoice_header_tbody"))
        )
    except TimeoutException:
        return "Failed - Table invoice tidak ditemukan"
    
    # ------------------------------------------
    # 2. Ambil row pertama
    # ------------------------------------------
    try:
        first_row = driver.find_element(
            By.CSS_SELECTOR, "#invoice_header_tbody tr:first-child"
        )
    except NoSuchElementException:
        return "Failed - Tidak ada row di table"
    
    # Ambil Invoice # dari row pertama
    try:
        invoice_cell = first_row.find_element(
            By.CSS_SELECTOR, "td.s-datatable-cell-invoice_number a"
        )
        coupa_invoice_num = invoice_cell.text.strip()
    except NoSuchElementException:
        coupa_invoice_num = ""
    
    logger.info(f"Invoice # di Coupa (row 1): {coupa_invoice_num}")
    
    # Cek apakah sama dengan invoice yang diproses
    invoice_num_clean = str(invoice_num).strip().lower()
    coupa_invoice_clean = str(coupa_invoice_num).strip().lower()
    
    if invoice_num_clean != coupa_invoice_clean:
        return f"Failed - Invoice tidak match: Coupa ({coupa_invoice_num}) vs Expected ({invoice_num})"
    
    # ------------------------------------------
    # 3. Ambil Total dari Coupa
    # ------------------------------------------
    try:
        total_cell = first_row.find_element(
            By.CSS_SELECTOR, "td.s-datatable-cell-total"
        )
        coupa_total_text = total_cell.text.strip()
        # Parse total: "914,162,318 IDR" -> 914162318
        coupa_total = _parse_amount(coupa_total_text)
    except NoSuchElementException:
        return "Failed - Total tidak ditemukan di table"
    
    logger.info(f"Total di Coupa: {coupa_total_text} -> {coupa_total}")
    
    # ------------------------------------------
    # 4. Hitung expected total dari df_detail
    # ------------------------------------------
    expected_total = _calculate_expected_total(invoice_num, df_detail, config)
    logger.info(f"Expected total dari Excel: {expected_total}")
    
    # ------------------------------------------
    # 5. Bandingkan
    # ------------------------------------------
    # Tolerance: bisa ada pembulatan, gunakan integer comparison
    coupa_total_int = int(round(coupa_total))
    expected_total_int = int(round(expected_total))
    
    if coupa_total_int == expected_total_int:
        remark = "Success save as draft - Amount Equals"
        logger.info(remark)
    else:
        remark = f"Success save as draft - Amount not equals Coupa ({coupa_total_int:,}) Excel ({expected_total_int:,})"
        logger.warning(remark)
    
    return remark


def _parse_amount(amount_text: str) -> float:
    """
    Parse amount text ke float.
    Format: "914,162,318 IDR" atau "914,162,318" atau "0.00"
    """
    if not amount_text:
        return 0.0
    
    # Hapus currency (IDR, USD, dll)
    import re
    cleaned = re.sub(r'[A-Za-z]', '', amount_text).strip()
    
    # Hapus comma dan spasi
    cleaned = cleaned.replace(",", "").replace(" ", "")
    
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def _calculate_expected_total(invoice_num: str, df_detail: pd.DataFrame, config: dict) -> float:
    """
    Hitung expected total dari df_detail.
    
    Formula: sum(Price) * (1 + Tax Rate / 100)
    """
    # Cari kolom Invoice No di detail
    invoice_col = None
    for col in df_detail.columns:
        col_clean = str(col).strip().lower()
        if "invoice no" in col_clean or "invoice number" in col_clean:
            invoice_col = col
            break
    
    if not invoice_col:
        logger.warning("Kolom Invoice No tidak ditemukan di df_detail!")
        return 0.0
    
    # Filter rows yang match invoice_num
    invoice_num_clean = str(invoice_num).strip().lower()
    detail_rows = df_detail[
        df_detail[invoice_col].astype(str).str.strip().str.lower() == invoice_num_clean
    ]
    
    if detail_rows.empty:
        logger.warning(f"Tidak ada detail untuk invoice: {invoice_num}")
        return 0.0
    
    # Cari kolom Price
    price_col = None
    for col in df_detail.columns:
        col_clean = str(col).strip().lower()
        if "price" in col_clean:
            price_col = col
            break
    
    if not price_col:
        logger.warning("Kolom Price tidak ditemukan di df_detail!")
        return 0.0
    
    # Sum semua Price
    total_price = 0.0
    for _, row in detail_rows.iterrows():
        price_val = row[price_col]
        if pd.notna(price_val):
            try:
                # Handle string dengan comma
                price_str = str(price_val).replace(",", "").strip()
                total_price += float(price_str)
            except ValueError:
                pass
    
    logger.info(f"Sum Price dari Excel: {total_price}")
    
    # Ambil Tax Rate
    tax_rate = config.get("Tax Rate", 0)
    try:
        tax_rate_float = float(str(tax_rate).replace("%", "").strip())
    except ValueError:
        tax_rate_float = 0.0
    
    logger.info(f"Tax Rate: {tax_rate_float}%")
    
    # Hitung total dengan tax
    expected_total = total_price * (1 + tax_rate_float / 100)
    
    return expected_total


# ==========================================
# UPDATE EXCEL SUMMARY
# ==========================================

def update_summary_status(config: dict, invoice_num: str, start_time: datetime, end_time: datetime, remark: str) -> None:
    """
    Update kolom Start Input, End Input, dan Status Input di file Excel Summary.
    
    Args:
        config: Dictionary config (harus ada "Invoice Summarry" dan "Sheet Name Invoice Summarry")
        invoice_num: Nomor invoice yang di-update
        start_time: Waktu mulai proses
        end_time: Waktu selesai proses
        remark: Hasil proses (Success/Failed)
    """
    from library.excel_utils import ExcelApplicationScope
    
    summary_path = config.get("Invoice Summarry", "")
    summary_sheet = config.get("Sheet Name Invoice Summarry", "Sheet1")
    
    if not summary_path:
        logger.warning("Config 'Invoice Summarry' kosong, skip update")
        return
    
    logger.info(f"Update Summary: {invoice_num}")
    
    # Format datetime: "8/21/2026  5:23:49 PM"
    time_format = "%-m/%-d/%Y  %-I:%M:%S %p"  # Format tanpa leading zero
    # Windows tidak support %-m, pakai strftime manual
    start_str = _format_datetime_custom(start_time)
    end_str = _format_datetime_custom(end_time)
    
    logger.info(f"  Start: {start_str}")
    logger.info(f"  End: {end_str}")
    logger.info(f"  Status: {remark}")
    
    try:
        with ExcelApplicationScope(
            file_path=summary_path,
            visible=False,
            create_new_file=False,
            save_changes=True  # SAVE perubahan
        ) as excel:
            # Baca data untuk cari row yang sesuai
            df = excel.read_range(sheet_name=summary_sheet, range_address="", has_headers=True)
            
            # Cari kolom Invoice Number
            invoice_col = _find_invoice_column_in_df(df)
            if not invoice_col:
                logger.warning("Kolom Invoice Number tidak ditemukan di Summary")
                return
            
            # Cari kolom Start Input, End Input, Status Input
            start_col = _find_column_in_df(df, "start input")
            end_col = _find_column_in_df(df, "end input")
            status_col = _find_column_in_df(df, "status input")
            
            if not all([start_col, end_col, status_col]):
                logger.warning(f"Kolom tidak lengkap - Start: {start_col}, End: {end_col}, Status: {status_col}")
                return
            
            # Cari row index yang match dengan invoice_num
            invoice_num_clean = str(invoice_num).strip().lower()
            row_idx = None
            
            for idx, row in df.iterrows():
                val = str(row[invoice_col]).strip().lower()
                if val == invoice_num_clean:
                    row_idx = idx
                    break
            
            if row_idx is None:
                logger.warning(f"Invoice {invoice_num} tidak ditemukan di Summary")
                return
            
            # Hitung posisi kolom (1-based untuk Excel)
            col_names = list(df.columns)
            start_col_idx = col_names.index(start_col) + 1
            end_col_idx = col_names.index(end_col) + 1
            status_col_idx = col_names.index(status_col) + 1
            
            # Row di Excel (1 = header, jadi data mulai dari row 2)
            excel_row = row_idx + 2
            
            # Convert column index ke letter (1=A, 2=B, dst)
            start_col_letter = _col_idx_to_letter(start_col_idx)
            end_col_letter = _col_idx_to_letter(end_col_idx)
            status_col_letter = _col_idx_to_letter(status_col_idx)
            
            # Update cell by cell using cell_address (e.g., "A2")
            excel.write_cell(
                sheet_name=summary_sheet,
                cell_address=f"{start_col_letter}{excel_row}",
                value=start_str
            )
            excel.write_cell(
                sheet_name=summary_sheet,
                cell_address=f"{end_col_letter}{excel_row}",
                value=end_str
            )
            excel.write_cell(
                sheet_name=summary_sheet,
                cell_address=f"{status_col_letter}{excel_row}",
                value=remark
            )
            
            logger.info(f"Summary updated untuk invoice: {invoice_num}")
            
    except Exception as e:
        logger.error(f"Gagal update Summary: {e}")


def _format_datetime_custom(dt: datetime) -> str:
    """
    Format datetime ke "M/D/YYYY  H:MM:SS AM/PM" tanpa leading zero.
    Contoh: "8/21/2026  5:23:49 PM"
    """
    # Ambil komponen
    month = dt.month
    day = dt.day
    year = dt.year
    hour = dt.hour
    minute = dt.minute
    second = dt.second
    
    # Convert ke 12-hour format
    if hour == 0:
        hour_12 = 12
        ampm = "AM"
    elif hour < 12:
        hour_12 = hour
        ampm = "AM"
    elif hour == 12:
        hour_12 = 12
        ampm = "PM"
    else:
        hour_12 = hour - 12
        ampm = "PM"
    
    return f"{month}/{day}/{year}  {hour_12}:{minute:02d}:{second:02d} {ampm}"


def _find_invoice_column_in_df(df: pd.DataFrame) -> str | None:
    """Cari kolom Invoice Number di DataFrame."""
    possible_names = [
        "invoice number", "invoice no", "invoicenumber", "invoice_number",
        "inv number", "inv no", "no invoice", "no. invoice", "nomor invoice",
    ]
    
    for col in df.columns:
        col_trimmed = str(col).strip().lower()
        col_clean = col_trimmed.split("(")[0].strip()
        
        if col_clean in possible_names:
            return col
        if "invoice" in col_clean and ("num" in col_clean or "no" in col_clean):
            return col
    
    return None


def _find_column_in_df(df: pd.DataFrame, target_name: str) -> str | None:
    """Cari kolom berdasarkan nama (case-insensitive, trim spasi)."""
    target = target_name.strip().lower()
    
    for col in df.columns:
        col_clean = str(col).strip().lower()
        if col_clean == target:
            return col
        col_no_suffix = col_clean.split("(")[0].strip()
        if col_no_suffix == target:
            return col
    
    return None


def _col_idx_to_letter(col_idx: int) -> str:
    """
    Convert column index (1-based) ke Excel column letter.
    1 -> A, 2 -> B, ..., 26 -> Z, 27 -> AA, dst.
    """
    result = ""
    while col_idx > 0:
        col_idx, remainder = divmod(col_idx - 1, 26)
        result = chr(65 + remainder) + result
    return result
