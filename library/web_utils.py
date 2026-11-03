"""
Web Automation Engine Module

Handles browser automation for Coupa Supplier Portal.
Implements Requirements:
- 5.1: Buka browser Chrome dalam mode incognito
- 5.2: Timeout 30 detik untuk browser open
- 5.3: Navigate ke Coupa Supplier Portal
- 5.4: Login dengan username dan password
- 5.5: Handle captcha dengan wait 25 detik
- 5.6: Retry captcha maksimal 4 kali
- 5.7: Logout dari portal
- 5.8: Deteksi username yang sedang login
- 5.9: Handle login error
- 5.10: Handle navigation error
- 5.11: Close browser dengan benar
- 5.12: Screenshot on error
"""

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Dict, Any, List
from datetime import datetime

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    TimeoutException, 
    NoSuchElementException,
    WebDriverException,
    ElementClickInterceptedException
)
from webdriver_manager.chrome import ChromeDriverManager

from library.exceptions import (
    BrowserError, 
    NavigationError, 
    LoginError, 
    CaptchaError,
    SubmitError
)

import logging
logger = logging.getLogger(__name__)


@dataclass
class POSearchResult:
    """Result of PO search in Coupa."""
    found: bool
    po_number: str
    po_data: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = None


@dataclass
class InvoiceSubmitResult:
    """Result of invoice submission."""
    success: bool
    draft_id: Optional[str] = None
    error_message: Optional[str] = None


class WebAutomationEngine:
    """
    Browser automation engine for Coupa Supplier Portal.
    
    Handles:
    - Browser lifecycle (open, close)
    - Navigation to Coupa portal
    - Login/logout with captcha handling
    - PO search
    - Invoice submission
    - Screenshot capture
    
    Implements Requirements 5.1 - 5.12, 7.1 - 7.5, 8.1 - 8.6
    """
    
    # Timeouts (in seconds)
    BROWSER_OPEN_TIMEOUT = 30
    PAGE_LOAD_TIMEOUT = 60
    LOGIN_TIMEOUT = 30
    LOGOUT_TIMEOUT = 15
    SEARCH_TIMEOUT = 15  # Reduced from 30
    SUBMIT_TIMEOUT = 60
    
    # Captcha wait times per attempt (in seconds)
    # Attempt 1: 30s, Attempt 2: 60s, Attempt 3: 120s
    CAPTCHA_WAIT_TIMES = [30, 60, 120]
    CAPTCHA_MAX_RETRY = 3
    
    # Retry configuration
    SEARCH_RETRY_COUNT = 3
    SEARCH_RETRY_INTERVAL = 5
    
    # Login retry configuration
    LOGIN_MAX_RETRY = 2

    def __init__(
        self,
        coupa_url: str,
        screenshot_dir: Optional[str | Path] = None,
        headless: bool = False,
        logger_module: str = "WEB",
        fast_mode: bool = True
    ):
        """
        Initialize WebAutomationEngine.
        
        Args:
            coupa_url: URL of Coupa Supplier Portal
            screenshot_dir: Directory for saving screenshots
            headless: Run browser in headless mode
            logger_module: Module name for logging
            fast_mode: Fast mode (reduces delays by 50%), always True
        """
        self._logger = logging.getLogger(logger_module)
        self._coupa_url = coupa_url
        self._screenshot_dir = Path(screenshot_dir) if screenshot_dir else Path("screenshots")
        self._headless = headless
        
        # Speed optimization - always fast mode (0.5 = 50% faster)
        self._speed_multiplier = 0.5
        
        self._driver: Optional[webdriver.Chrome] = None
        self._wait: Optional[WebDriverWait] = None
        self._current_username: Optional[str] = None
        self._wa_notifier = None  # WhatsApp notifier (set externally)
        
        # Ensure screenshot directory exists
        self._screenshot_dir.mkdir(parents=True, exist_ok=True)
        
        self._logger.debug(f"WebAutomationEngine initialized (url={coupa_url})")
    
    def _sleep(self, seconds: float) -> None:
        """Sleep with speed multiplier applied."""
        actual_sleep = max(0.3, seconds * self._speed_multiplier)
        time.sleep(actual_sleep)
    
    def set_wa_notifier(self, notifier) -> None:
        """Set WhatsApp notifier for captcha notifications."""
        self._wa_notifier = notifier
    
    def open_browser(self) -> None:
        """
        Open Chrome browser in incognito mode.
        
        Raises:
            BrowserError: If browser fails to open
        
        Implements Requirements 5.1, 5.2
        """
        self._logger.info("Opening Chrome browser...")
        
        try:
            options = Options()
            options.add_argument("--incognito")
            options.add_argument("--start-maximized")
            options.add_argument("--disable-extensions")
            options.add_argument("--disable-popup-blocking")
            options.add_argument("--disable-infobars")
            options.add_argument("--disable-notifications")

            if self._headless:
                options.add_argument("--headless=new")
            
            # Auto-download and setup ChromeDriver
            # Disable SSL verification for corporate networks with proxy/firewall
            import os
            import platform
            os.environ['WDM_SSL_VERIFY'] = '0'
            
            # Force correct architecture for Windows
            if platform.system() == 'Windows':
                # Check if 64-bit Windows
                if platform.machine().endswith('64'):
                    os.environ['WDM_ARCHITECTURE'] = '64'
            
            service = Service(ChromeDriverManager().install())
            
            self._driver = webdriver.Chrome(service=service, options=options)
            self._driver.set_page_load_timeout(self.PAGE_LOAD_TIMEOUT)
            self._wait = WebDriverWait(self._driver, self.BROWSER_OPEN_TIMEOUT)
            
            self._logger.info("Browser opened successfully")
            
        except WebDriverException as e:
            raise BrowserError(
                message="Failed to open browser",
                details=str(e)
            )
        except Exception as e:
            raise BrowserError(
                message="Unexpected error opening browser",
                details=str(e)
            )
    
    def close_browser(self) -> None:
        """
        Close browser and cleanup resources.
        
        Implements Requirement 5.11
        """
        self._logger.info("Closing browser...")
        
        if self._driver:
            try:
                self._driver.quit()
            except Exception as e:
                self._logger.warning(f"Error closing browser: {e}")
            finally:
                self._driver = None
                self._wait = None
                self._current_username = None
        
        self._logger.info("Browser closed")

    def navigate_to_coupa(self) -> None:
        """
        Navigate to Coupa Supplier Portal.
        
        Raises:
            NavigationError: If page fails to load
        
        Implements Requirement 5.3
        """
        self._logger.info(f"Navigating to Coupa Portal: {self._coupa_url}")
        
        if not self._driver:
            raise BrowserError(message="Browser not open")
        
        try:
            self._driver.get(self._coupa_url)
            
            # Wait for page to load (check for login form or dashboard)
            self._wait.until(
                EC.presence_of_element_located((By.TAG_NAME, "body"))
            )
            
            self._logger.info("Navigation successful")
            
        except TimeoutException:
            raise NavigationError(
                message="Page load timeout",
                url=self._coupa_url,
                timeout=self.PAGE_LOAD_TIMEOUT
            )
        except Exception as e:
            raise NavigationError(
                message="Failed to navigate to Coupa Portal",
                url=self._coupa_url,
                details=str(e)
            )
    
    # Login retry configuration
    LOGIN_MAX_RETRY = 2
    
    def login(self, username: str, password: str) -> None:
        """
        Login to Coupa Portal with retry logic.
        
        If login fails (invalid credentials), retry up to LOGIN_MAX_RETRY times.
        Before retry #2, reload the browser.
        
        Args:
            username: Coupa username (email)
            password: Coupa password
        
        Raises:
            CaptchaError: If captcha not completed after max retries
            LoginError: If credentials invalid after all retries
        
        Implements Requirements 5.4, 5.5, 5.6, 5.9
        """
        last_error = None
        
        for attempt in range(1, self.LOGIN_MAX_RETRY + 1):
            try:
                self._logger.info(f"Login attempt {attempt}/{self.LOGIN_MAX_RETRY} as: {username}")
                
                # Before retry #2, reload browser
                if attempt == 2:
                    self._logger.info("Reloading browser before retry...")
                    self._driver.refresh()
                    self._sleep(3)
                
                # Perform actual login
                self._perform_login(username, password)
                
                # Install session inactivity handler to keep session alive
                self._install_session_inactivity_handler()
                
                # Login successful
                return
                
            except LoginError as e:
                last_error = e
                self._logger.warning(f"Login attempt {attempt} failed: {e.message}")
                
                # Check if it's an invalid credentials error
                if "invalid" in str(e).lower() or "incorrect" in str(e).lower() or "failed" in str(e).lower():
                    if attempt < self.LOGIN_MAX_RETRY:
                        self._logger.info(f"Will retry login...")
                        # Switch back to default content if in iframe
                        try:
                            self._driver.switch_to.default_content()
                        except:
                            pass
                        continue
                
                # For other errors, raise immediately
                raise
            
            except CaptchaError:
                # Captcha errors should be raised immediately
                raise
        
        # All retries exhausted
        raise LoginError(
            message=f"Login failed after {self.LOGIN_MAX_RETRY} attempts",
            username=username,
            details=str(last_error) if last_error else None
        )
    
    def _perform_login(self, username: str, password: str) -> None:
        """
        Perform actual login to Coupa Portal.
        
        Coupa Login Flow:
        1. Sometimes only Email field + Continue button appears
        2. After Continue, Password field + Login button appears
        3. Sometimes both Email + Password appear directly
        
        Args:
            username: Coupa username (email)
            password: Coupa password
        
        Raises:
            CaptchaError: If captcha not completed after max retries
            LoginError: If credentials invalid or login fails
        """

        if not self._driver:
            raise BrowserError(message="Browser not open")
        
        try:
            # Wait for page to load
            self._sleep(3)
            
            # Wait for email field (id="email")
            wait_long = WebDriverWait(self._driver, 60)
            email_field = wait_long.until(
                EC.presence_of_element_located((By.ID, "email"))
            )
            wait_long.until(EC.element_to_be_clickable((By.ID, "email")))
            
            email_field.clear()
            email_field.send_keys(username)
            self._logger.debug(f"Entered username: {username}")
            self._sleep(1)
            
            # Check for captcha before Continue
            if self._is_captcha_present():
                self._handle_captcha("before Continue")
            
            # Check if password field is already visible
            password_fields = self._driver.find_elements(By.ID, "password")
            password_visible = password_fields and password_fields[0].is_displayed()
            
            if password_visible:
                # Both fields visible - enter password and click Login
                self._logger.debug("Password field visible, entering password")
                password_fields[0].clear()
                password_fields[0].send_keys(password)
                login_button = wait_long.until(EC.element_to_be_clickable((By.ID, "login-submit")))
                login_button.click()
                self._logger.debug("Clicked Login button")
            else:
                # Click Continue first
                self._logger.debug("Clicking Continue...")
                self._click_continue()
                
                # Wait for redirect
                self._sleep(3)
                try:
                    wait_long.until(lambda d: d.execute_script("return document.readyState") == "complete")
                except:
                    pass
                self._sleep(2)
                
                # Check for captcha after Continue
                if self._is_captcha_present():
                    self._handle_captcha("after Continue")
                    self._sleep(2)
                    # May need to click Continue again after captcha
                    try:
                        self._click_continue()
                        self._sleep(3)
                        wait_long.until(lambda d: d.execute_script("return document.readyState") == "complete")
                        self._sleep(2)
                    except:
                        pass
                
                # Find password field (main page or iframe), fill, and click Login
                self._fill_password_and_login(password)
            
            # Wait for response after Login
            self._sleep(3)
            
            # Check for captcha after Login
            if self._is_captcha_present():
                self._handle_captcha("after Login")
            
            # Wait for successful login
            self._wait_for_login_success()
            
            self._current_username = username
            self._logger.info("Login successful")
            return

        except (LoginError, CaptchaError):
            raise
        except TimeoutException:
            self.take_screenshot("login_timeout")
            current_url = self._driver.current_url if self._driver else "unknown"
            self._logger.error(f"Login timeout at URL: {current_url}")
            raise LoginError(
                message=f"Login timeout - page elements not found (URL: {current_url})",
                username=username
            )
        except NoSuchElementException as e:
            self.take_screenshot("login_element_not_found")
            raise LoginError(
                message="Login form elements not found",
                username=username,
                details=str(e)
            )
        except Exception as e:
            self.take_screenshot("login_error")
            raise LoginError(
                message="Login failed",
                username=username,
                details=str(e)
            )
    
    def _click_continue(self) -> None:
        """Click the Continue button using multiple selectors."""
        continue_selectors = [
            (By.XPATH, "//button[span[text()='Continue']]"),
            (By.XPATH, "//button[contains(., 'Continue')]"),
            (By.XPATH, "//button[@type='submit' and not(@id='login-submit')]"),
            (By.CSS_SELECTOR, "button.s-login[type='submit']")
        ]
        
        for by, selector in continue_selectors:
            try:
                btn = WebDriverWait(self._driver, 5).until(
                    EC.element_to_be_clickable((by, selector))
                )
                if btn:
                    btn.click()
                    self._logger.debug("Clicked Continue button")
                    return
            except:
                continue
        
        raise LoginError(message="Continue button not found")
    
    def _fill_password_and_login(self, password: str) -> None:
        """Find password field (main page or iframe), fill it, then click Login."""
        wait_long = WebDriverWait(self._driver, 30)
        password_field = None
        
        # Try to find and switch to iframe first
        iframe_selectors = [
            (By.ID, "identityLoginIframe"),
            (By.NAME, "identityLoginIframe"),
            (By.CSS_SELECTOR, "iframe[id*='identity']"),
            (By.CSS_SELECTOR, "iframe[id*='login']"),
        ]
        
        iframe_found = False
        for by, selector in iframe_selectors:
            try:
                iframe = WebDriverWait(self._driver, 5).until(
                    EC.presence_of_element_located((by, selector))
                )
                if iframe:
                    self._driver.switch_to.frame(iframe)
                    self._logger.debug(f"Switched to iframe: {selector}")
                    iframe_found = True
                    break
            except:
                continue
        
        # Find password field
        password_selectors = [
            (By.ID, "password"),
            (By.NAME, "password"),
            (By.CSS_SELECTOR, "input[type='password']"),
        ]
        
        for by, selector in password_selectors:
            try:
                password_field = WebDriverWait(self._driver, 10).until(
                    EC.presence_of_element_located((by, selector))
                )
                if password_field and password_field.is_displayed():
                    self._logger.debug(f"Found password field: {selector}")
                    break
                password_field = None
            except:
                continue
        
        # If not found in iframe, switch back to main content
        if not password_field and iframe_found:
            self._driver.switch_to.default_content()
            self._logger.debug("Back to main content, searching again...")
            for by, selector in password_selectors:
                try:
                    password_field = WebDriverWait(self._driver, 5).until(
                        EC.presence_of_element_located((by, selector))
                    )
                    if password_field and password_field.is_displayed():
                        break
                    password_field = None
                except:
                    continue
        
        # If still not found, check for captcha blocking
        if not password_field:
            if self._is_captcha_present():
                self._handle_captcha("password field blocked")
                for by, selector in password_selectors:
                    try:
                        password_field = WebDriverWait(self._driver, 10).until(
                            EC.presence_of_element_located((by, selector))
                        )
                        if password_field and password_field.is_displayed():
                            break
                        password_field = None
                    except:
                        continue
        
        if not password_field:
            self.take_screenshot("password_not_found")
            raise LoginError(message="Password field not found")
        
        # Fill password
        self._sleep(1)
        password_field.clear()
        password_field.send_keys(password)
        self._logger.debug("Entered password")
        
        # Click Login button
        login_selectors = [
            (By.ID, "login-submit"),
            (By.XPATH, "//button[span[text()='Login']]"),
            (By.XPATH, "//button[contains(., 'Login')]"),
            (By.XPATH, "//button[@type='submit']"),
            (By.CSS_SELECTOR, "button.pf-c-button.pf-m-primary"),
        ]
        
        for by, selector in login_selectors:
            try:
                btn = WebDriverWait(self._driver, 5).until(
                    EC.element_to_be_clickable((by, selector))
                )
                if btn:
                    btn.click()
                    self._logger.debug("Clicked Login button")
                    return
            except:
                continue
        
        raise LoginError(message="Login button not found")
    
    def _install_session_inactivity_handler(self) -> None:
        """
        Install a JavaScript watcher that auto-clicks the "Continue Session" button
        to keep the Coupa session alive before it times out due to inactivity.
        """
        if not self._driver:
            return
        
        try:
            self._driver.switch_to.default_content()
            self._driver.execute_script("""
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
            self._logger.info("Session inactivity handler active")
        except Exception as e:
            self._logger.warning(f"Failed to install session inactivity handler: {e}")
    
    def _handle_captcha(self, context: str) -> None:
        """
        Handle captcha with 3 attempts (30s, 60s, 120s wait times).
        
        Checks every 5 seconds if captcha is completed.
        If captcha is not completed after all attempts, raises CaptchaError immediately.
        NO RETRY - captcha failure is final.
        
        Args:
            context: Description of when captcha appeared (for logging)
        
        Raises:
            CaptchaError: If captcha not completed after maximum retries
        """
        CHECK_INTERVAL = 5  # Check every 5 seconds
        
        for attempt in range(self.CAPTCHA_MAX_RETRY):
            wait_time = self.CAPTCHA_WAIT_TIMES[attempt]
            
            self._logger.warning(
                f"Captcha detected {context}! Please complete the captcha manually. "
                f"Max wait {wait_time}s, checking every {CHECK_INTERVAL}s... (attempt {attempt + 1}/{self.CAPTCHA_MAX_RETRY})"
            )
            
            # Send WhatsApp notification for captcha warning
            if self._wa_notifier:
                try:
                    self._wa_notifier.send_captcha_warning(
                        attempt=attempt + 1,
                        max_attempt=self.CAPTCHA_MAX_RETRY
                    )
                except Exception as e:
                    self._logger.warning(f"Failed to send captcha WA notification: {e}")
            
            # Check every CHECK_INTERVAL seconds if captcha is completed
            elapsed = 0
            while elapsed < wait_time:
                time.sleep(CHECK_INTERVAL)
                elapsed += CHECK_INTERVAL
                
                # Check if captcha is still present
                if not self._is_captcha_present():
                    self._logger.info(f"Captcha completed successfully after {elapsed}s!")
                    return
                
                remaining = wait_time - elapsed
                if remaining > 0:
                    self._logger.debug(f"Captcha still present, {remaining}s remaining...")
            
            self._logger.warning(f"Captcha still present after {wait_time}s wait (attempt {attempt + 1})")
        
        # All attempts exhausted - send timeout notification and raise error
        if self._wa_notifier:
            try:
                self._wa_notifier.send_captcha_timeout()
            except Exception as e:
                self._logger.warning(f"Failed to send captcha timeout WA notification: {e}")
        
        self.take_screenshot("captcha_failed")
        raise CaptchaError(
            message=f"Captcha not completed after {self.CAPTCHA_MAX_RETRY} attempts ({context})",
            attempts=self.CAPTCHA_MAX_RETRY
        )
    
    def _is_captcha_present(self) -> bool:
        """
        Check if captcha is present AND needs to be completed.
        
        Coupa uses reCAPTCHA which can appear:
        1. After entering email (before Continue)
        2. After clicking Continue (before password field)
        3. After clicking Login
        
        Returns:
            True if captcha needs to be completed, False if no captcha or already completed
        """
        try:
            # Switch to default content first
            try:
                self._driver.switch_to.default_content()
            except:
                pass
            
            # First, check if captcha challenge is visible (image selection, etc.)
            # This is the most reliable indicator that captcha needs completion
            challenge_selectors = [
                # reCAPTCHA challenge iframe (image selection puzzle)
                (By.XPATH, "//iframe[contains(@src, 'recaptcha') and contains(@src, 'bframe')]"),
                (By.XPATH, "//iframe[contains(@title, 'recaptcha challenge')]"),
                # Challenge container
                (By.CSS_SELECTOR, ".rc-imageselect"),
                (By.CSS_SELECTOR, ".rc-doscaptcha-body"),
                (By.CSS_SELECTOR, ".rc-audiochallenge"),
            ]
            
            for by, selector in challenge_selectors:
                try:
                    elements = self._driver.find_elements(by, selector)
                    if elements and any(e.is_displayed() for e in elements):
                        self._logger.debug(f"Captcha challenge detected: {selector}")
                        return True
                except:
                    continue
            
            # Check for unchecked reCAPTCHA checkbox
            # When completed, the checkbox has class "recaptcha-checkbox-checked"
            try:
                # Find the anchor iframe first
                anchor_iframes = self._driver.find_elements(
                    By.XPATH, "//iframe[contains(@src, 'recaptcha') and contains(@src, 'anchor')]"
                )
                
                for iframe in anchor_iframes:
                    try:
                        if not iframe.is_displayed():
                            continue
                            
                        self._driver.switch_to.frame(iframe)
                        
                        # Check if checkbox is NOT checked (captcha not completed)
                        unchecked = self._driver.find_elements(
                            By.CSS_SELECTOR, ".recaptcha-checkbox[aria-checked='false']"
                        )
                        if unchecked and any(e.is_displayed() for e in unchecked):
                            self._driver.switch_to.default_content()
                            self._logger.debug("Captcha checkbox unchecked - needs completion")
                            return True
                        
                        # Check if checkbox IS checked (captcha completed)
                        checked = self._driver.find_elements(
                            By.CSS_SELECTOR, ".recaptcha-checkbox[aria-checked='true']"
                        )
                        if checked and any(e.is_displayed() for e in checked):
                            self._driver.switch_to.default_content()
                            self._logger.debug("Captcha checkbox checked - completed!")
                            return False
                        
                        # Also check for checkmark class
                        checkmark = self._driver.find_elements(
                            By.CSS_SELECTOR, ".recaptcha-checkbox-checkmark"
                        )
                        if checkmark and any(e.is_displayed() for e in checkmark):
                            self._driver.switch_to.default_content()
                            self._logger.debug("Captcha checkmark visible - completed!")
                            return False
                        
                        self._driver.switch_to.default_content()
                    except:
                        try:
                            self._driver.switch_to.default_content()
                        except:
                            pass
                        continue
            except:
                try:
                    self._driver.switch_to.default_content()
                except:
                    pass
            
            # Check for Coupa-specific captcha message that indicates captcha is needed
            coupa_captcha_messages = [
                (By.XPATH, "//*[contains(text(), 'complete reCAPTCHA')]"),
                (By.XPATH, "//*[contains(text(), 'For added security')]"),
                (By.XPATH, "//*[contains(text(), 'Recaptcha requires verification')]"),
                (By.XPATH, "//div[contains(@class, 'alert') and contains(., 'reCAPTCHA')]"),
            ]
            
            for by, selector in coupa_captcha_messages:
                try:
                    elements = self._driver.find_elements(by, selector)
                    if elements and any(e.is_displayed() for e in elements):
                        self._logger.debug(f"Coupa captcha message detected: {selector}")
                        return True
                except:
                    continue
            
            # Check for visible reCAPTCHA anchor iframe (but not if already checked)
            try:
                iframes = self._driver.find_elements(By.TAG_NAME, "iframe")
                for iframe in iframes:
                    try:
                        src = iframe.get_attribute("src") or ""
                        if "recaptcha" in src.lower() and "anchor" in src.lower():
                            if iframe.is_displayed():
                                # Need to check inside if it's checked or not
                                self._driver.switch_to.frame(iframe)
                                
                                # If we can find the unchecked state, captcha needs completion
                                unchecked = self._driver.find_elements(
                                    By.CSS_SELECTOR, ".recaptcha-checkbox:not(.recaptcha-checkbox-checked)"
                                )
                                has_unchecked = unchecked and any(e.is_displayed() for e in unchecked)
                                
                                self._driver.switch_to.default_content()
                                
                                if has_unchecked:
                                    self._logger.debug("Found unchecked reCAPTCHA in iframe")
                                    return True
                    except:
                        try:
                            self._driver.switch_to.default_content()
                        except:
                            pass
                        continue
            except:
                pass
            
            return False
        except Exception as e:
            self._logger.debug(f"Error checking captcha: {e}")
            return False

    def _wait_for_login_success(self) -> None:
        """Wait for successful login indication."""
        try:
            # Switch back to default content first (in case we're in iframe)
            try:
                self._driver.switch_to.default_content()
            except:
                pass
            
            # Log current URL for debugging
            current_url = self._driver.current_url
            self._logger.debug(f"Current URL after login: {current_url}")
            
            # Check for "too many active logins" popup and handle it
            self._handle_too_many_logins_popup()
            
            # Wait for Coupa dashboard/home page elements
            # After successful login, URL changes and dashboard elements appear
            WebDriverWait(self._driver, 30).until(
                EC.any_of(
                    # URL patterns for successful login
                    EC.url_contains("/user/home"),
                    EC.url_contains("/dashboard"),
                    EC.url_contains("/invoices"),
                    EC.url_contains("/orders"),
                    EC.url_contains("/setup"),
                    EC.url_contains("/suppliers"),
                    EC.url_contains("/csp"),
                    EC.url_contains("/home"),
                    # Check that we're NOT on login page anymore
                    lambda d: "/sessions/new" not in d.current_url and "/login" not in d.current_url,
                    # Common Coupa dashboard elements
                    EC.presence_of_element_located((By.CLASS_NAME, "csp-header")),
                    EC.presence_of_element_located((By.ID, "global-nav")),
                    EC.presence_of_element_located((By.CLASS_NAME, "user-nav")),
                    EC.presence_of_element_located((By.CSS_SELECTOR, ".main-nav")),
                    EC.presence_of_element_located((By.CSS_SELECTOR, "[data-qa='user-menu']")),
                    # Additional Coupa elements
                    EC.presence_of_element_located((By.CSS_SELECTOR, ".navbar")),
                    EC.presence_of_element_located((By.CSS_SELECTOR, ".top-nav")),
                    EC.presence_of_element_located((By.CSS_SELECTOR, ".header-nav")),
                    EC.presence_of_element_located((By.CSS_SELECTOR, "[class*='dashboard']")),
                    EC.presence_of_element_located((By.CSS_SELECTOR, "[class*='home']")),
                    EC.presence_of_element_located((By.XPATH, "//a[contains(@href, '/logout')]")),
                    EC.presence_of_element_located((By.XPATH, "//a[contains(text(), 'Logout')]")),
                    EC.presence_of_element_located((By.XPATH, "//a[contains(text(), 'Sign Out')]")),
                    EC.presence_of_element_located((By.XPATH, "//*[contains(text(), 'Welcome')]")),
                )
            )
            
            # Log success
            final_url = self._driver.current_url
            self._logger.debug(f"Login success detected. Final URL: {final_url}")
            
        except TimeoutException:
            # Log current URL for debugging
            current_url = self._driver.current_url
            self._logger.warning(f"Login verification timeout. Current URL: {current_url}")
            
            # Check if we're actually on a non-login page (might be successful but elements not found)
            if "/sessions/new" not in current_url and "/login" not in current_url:
                self._logger.info(f"URL indicates successful login: {current_url}")
                return  # Consider it successful
            
            # Check for error message
            error_msg = self._get_login_error_message()
            if error_msg:
                raise LoginError(
                    message=f"Invalid credentials: {error_msg}",
                    username=self._current_username
                )
            # Take screenshot for debugging
            self.take_screenshot("login_success_timeout")
            raise LoginError(
                message="Login failed - could not verify successful login",
                username=self._current_username
            )
    
    def _handle_too_many_logins_popup(self) -> None:
        """
        Handle "too many active logins" popup.
        
        This popup appears when the account has too many active sessions.
        Message: "Your account currently has too many active logins. 
                  If you continue to sign in here, you will be signed out 
                  from one of your other sessions."
        
        Action: Click "Log in" button to continue and sign out other sessions.
        """
        try:
            # Check for the popup message
            popup_indicators = [
                (By.XPATH, "//*[contains(text(), 'too many active logins')]"),
                (By.XPATH, "//*[contains(text(), 'signed out from one of your other sessions')]"),
                (By.XPATH, "//div[contains(text(), 'too many active')]"),
            ]
            
            popup_found = False
            for by, selector in popup_indicators:
                try:
                    element = self._driver.find_element(by, selector)
                    if element and element.is_displayed():
                        popup_found = True
                        self._logger.info("Detected 'too many active logins' popup")
                        break
                except:
                    continue
            
            if not popup_found:
                return  # No popup, continue normally
            
            # Find and click "Log in" button on the popup (all instant find_elements)
            # Button id="login_button" is the primary selector
            login_btn_selectors = [
                (By.ID, "login_button"),
                (By.XPATH, "//button[text()='Log in']"),
                (By.XPATH, "//button[contains(text(), 'Log in')]"),
                (By.CSS_SELECTOR, "button.btn-primary"),
                (By.CSS_SELECTOR, "button[type='submit']"),
            ]
            
            login_btn = None
            for by, selector in login_btn_selectors:
                try:
                    elements = self._driver.find_elements(by, selector)
                    for elem in elements:
                        if elem.is_displayed() and elem.is_enabled():
                            login_btn = elem
                            break
                    if login_btn:
                        break
                except:
                    continue
            
            if login_btn:
                try:
                    login_btn.click()
                except ElementClickInterceptedException:
                    self._driver.execute_script("arguments[0].click();", login_btn)
                
                self._logger.info("Clicked 'Log in' button on 'too many active logins' popup")
                self._sleep(2)  # Wait for redirect
            else:
                self._logger.warning("Could not find 'Log in' button on popup")
                self.take_screenshot("too_many_logins_popup")
                
        except Exception as e:
            self._logger.debug(f"No 'too many active logins' popup or error handling it: {e}")
    
    def _get_login_error_message(self) -> Optional[str]:
        """Get login error message if present."""
        try:
            # Switch back to default content first
            try:
                self._driver.switch_to.default_content()
            except:
                pass
            
            # Also check inside iframe
            iframe_checked = False
            for _ in range(2):
                # Coupa error message selectors
                error_selectors = [
                    (By.CLASS_NAME, "pf-c-alert__title"),
                    (By.CLASS_NAME, "alert-danger"),
                    (By.CLASS_NAME, "error-message"),
                    (By.CLASS_NAME, "flash-error"),
                    (By.CLASS_NAME, "login-error"),
                    (By.CSS_SELECTOR, ".pf-c-alert.pf-m-danger"),
                    (By.XPATH, "//div[contains(@class, 'alert') and contains(@class, 'error')]"),
                    (By.XPATH, "//div[contains(@class, 'pf-c-alert')]//h4"),
                    (By.XPATH, "//*[contains(text(), 'Invalid')]"),
                    (By.XPATH, "//*[contains(text(), 'incorrect')]"),
                    (By.XPATH, "//*[contains(text(), 'failed')]")
                ]
                
                for by, selector in error_selectors:
                    try:
                        elements = self._driver.find_elements(by, selector)
                        if elements and elements[0].text:
                            return elements[0].text.strip()
                    except:
                        continue
                
                # Try checking inside iframe
                if not iframe_checked:
                    try:
                        iframe = self._driver.find_element(By.ID, "identityLoginIframe")
                        self._driver.switch_to.frame(iframe)
                        iframe_checked = True
                    except:
                        break
            
            # Switch back to default
            try:
                self._driver.switch_to.default_content()
            except:
                pass
            
            return None
        except Exception:
            return None
    def logout(self) -> bool:
        """
        Logout from Coupa Portal.
        
        Returns:
            True if logout successful, False otherwise
        
        Implements Requirement 5.7, 14.2, 14.3
        """
        self._logger.info("Logging out...")
        
        if not self._driver:
            return True  # No browser, consider it logged out
        
        try:
            # Switch to default content first
            try:
                self._driver.switch_to.default_content()
            except:
                pass
            
            # Try multiple selectors for user menu / profile dropdown
            user_menu_selectors = [
                (By.ID, "user-menu"),
                (By.CSS_SELECTOR, "[data-qa='user-menu']"),
                (By.CSS_SELECTOR, ".user-nav"),
                (By.CSS_SELECTOR, ".user-menu"),
                (By.CSS_SELECTOR, ".profile-dropdown"),
                (By.XPATH, "//a[contains(@href, '/logout')]"),
                (By.XPATH, "//button[contains(@class, 'user')]"),
                (By.XPATH, "//*[contains(@class, 'user-name')]"),
                (By.XPATH, "//*[contains(@class, 'profile')]"),
            ]
            
            user_menu = None
            for by, selector in user_menu_selectors:
                try:
                    user_menu = WebDriverWait(self._driver, 5).until(
                        EC.element_to_be_clickable((by, selector))
                    )
                    if user_menu:
                        self._logger.debug(f"Found user menu with selector: {selector}")
                        break
                except:
                    continue
            
            if user_menu:
                try:
                    user_menu.click()
                    self._sleep(1)
                except:
                    pass
            
            # Try to find and click logout/sign out link
            logout_selectors = [
                (By.LINK_TEXT, "Sign Out"),
                (By.LINK_TEXT, "Logout"),
                (By.LINK_TEXT, "Log Out"),
                (By.PARTIAL_LINK_TEXT, "Sign Out"),
                (By.PARTIAL_LINK_TEXT, "Logout"),
                (By.XPATH, "//a[contains(@href, '/logout')]"),
                (By.XPATH, "//a[contains(@href, 'logout')]"),
                (By.XPATH, "//a[contains(text(), 'Sign Out')]"),
                (By.XPATH, "//a[contains(text(), 'Logout')]"),
                (By.XPATH, "//button[contains(text(), 'Sign Out')]"),
                (By.CSS_SELECTOR, "a[href*='logout']"),
            ]
            
            logout_link = None
            for by, selector in logout_selectors:
                try:
                    logout_link = WebDriverWait(self._driver, 5).until(
                        EC.element_to_be_clickable((by, selector))
                    )
                    if logout_link:
                        self._logger.debug(f"Found logout link with selector: {selector}")
                        break
                except:
                    continue
            
            if logout_link:
                logout_link.click()
                self._sleep(3)
                
                self._current_username = None
                self._logger.info("Logout successful")
                return True
            else:
                self._logger.warning("Logout link not found")
                return False
            
        except TimeoutException:
            self._logger.warning("Logout timeout")
            return False
        except Exception as e:
            self._logger.warning(f"Logout failed: {e}")
            return False
    
    def get_current_username(self) -> Optional[str]:
        """
        Get currently logged in username.
        
        Returns:
            Username if logged in, None otherwise
        
        Implements Requirement 5.8, 14.4
        """
        return self._current_username
    
    def get_displayed_username(self) -> Optional[str]:
        """
        Get the username displayed on the Coupa Portal page.
        
        This reads the username from the page element, not from internal state.
        Used to verify the actual logged-in user.
        
        Returns:
            Username displayed on page, or None if not found
        
        Implements Requirement 14.4
        """
        if not self._driver:
            return None
        
        try:
            # Switch to default content first
            try:
                self._driver.switch_to.default_content()
            except:
                pass
            
            # Try multiple selectors for username display
            username_selectors = [
                (By.CSS_SELECTOR, ".user-name"),
                (By.CSS_SELECTOR, ".username"),
                (By.CSS_SELECTOR, "[data-qa='user-name']"),
                (By.CSS_SELECTOR, ".profile-name"),
                (By.CSS_SELECTOR, ".user-email"),
                (By.XPATH, "//*[contains(@class, 'user-name')]"),
                (By.XPATH, "//*[contains(@class, 'username')]"),
                (By.XPATH, "//*[contains(@class, 'profile')]//span"),
            ]
            
            for by, selector in username_selectors:
                try:
                    element = WebDriverWait(self._driver, 3).until(
                        EC.presence_of_element_located((by, selector))
                    )
                    if element and element.text:
                        username = element.text.strip()
                        if username:
                            self._logger.debug(f"Found displayed username: {username}")
                            return username
                except:
                    continue
            
            return None
            
        except Exception as e:
            self._logger.debug(f"Error getting displayed username: {e}")
            return None
    
    def ensure_logged_in_as(self, username: str, password: str) -> bool:
        """
        Ensure the browser is logged in as the specified user.
        
        This method handles the login per company logic:
        1. If no session exists, login with the specified credentials
        2. If session exists with same username, reuse the session
        3. If session exists with different username, logout and re-login
        4. If logout fails, close browser and open new session
        
        Args:
            username: Username (email) to login as
            password: Password for the user
        
        Returns:
            True if successfully logged in as the specified user
        
        Implements Requirements 14.1, 14.2, 14.3, 14.4
        """
        self._logger.info(f"Ensuring logged in as: {username}")
        
        # Check if browser is open
        if not self.is_browser_open():
            self._logger.info("Browser not open, opening new browser...")
            self.open_browser()
            self.navigate_to_coupa()
            self.login(username, password)
            return True
        
        # Check current logged-in user
        current_user = self.get_current_username()
        
        if current_user is None:
            # Not logged in, perform login
            self._logger.info("No active session, logging in...")
            self.navigate_to_coupa()
            self.login(username, password)
            return True
        
        # Check if same user
        if current_user.lower() == username.lower():
            self._logger.info(f"Already logged in as {username}, reusing session")
            return True
        
        # Different user, need to logout and re-login
        self._logger.info(f"Different user logged in ({current_user}), switching to {username}...")
        
        # Try to logout
        logout_success = self.logout()
        
        if logout_success:
            # Logout successful, login with new user
            self._logger.info("Logout successful, logging in with new user...")
            self._sleep(2)
            self.navigate_to_coupa()
            self.login(username, password)
            return True
        else:
            # Logout failed, close browser and open new session
            self._logger.warning("Logout failed, closing browser and opening new session...")
            self.close_browser()
            self._sleep(2)
            self.open_browser()
            self.navigate_to_coupa()
            self.login(username, password)
            return True
    
    def verify_logged_in_user(self, expected_username: str) -> bool:
        """
        Verify that the currently logged-in user matches the expected username.
        
        This checks both the internal state and the displayed username on the page.
        
        Args:
            expected_username: Expected username to verify
        
        Returns:
            True if the logged-in user matches, False otherwise
        
        Implements Requirement 14.4
        """
        # Check internal state
        current_user = self.get_current_username()
        if current_user and current_user.lower() == expected_username.lower():
            return True
        
        # Check displayed username on page
        displayed_user = self.get_displayed_username()
        if displayed_user:
            # Check if displayed username contains the expected username (email)
            if expected_username.lower() in displayed_user.lower():
                return True
            # Also check if the email prefix matches
            expected_prefix = expected_username.split('@')[0].lower()
            if expected_prefix in displayed_user.lower():
                return True
        
        return False

    def select_customer(self, customer_name: str) -> bool:
        """
        Select a customer from the "Select customer" dropdown on Orders page.
        
        Used for PTI company which has multiple customers to choose from.
        The dropdown is a ComboBox (input.s-inputCustomerList) with a results list.
        Types the customer name to filter, then clicks the matching option.
        
        Note: The customer dropdown lives on the MAIN page header, NOT inside the
        enterprise_frame iframe.
        
        Args:
            customer_name: Full customer label to select
        
        Returns:
            True if selected, False otherwise
        """
        if not customer_name:
            return True
        
        self._logger.info(f"Selecting customer: {customer_name}")
        
        if not self._driver:
            raise BrowserError(message="Browser not open")
        
        try:
            # Customer dropdown is on main page (NOT inside iframe)
            try:
                self._driver.switch_to.default_content()
            except:
                pass
            
            # Locate the customer combobox input
            combo_input = WebDriverWait(self._driver, 15).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, "input.s-inputCustomerList"))
            )
            
            # Check if already selected
            try:
                current_val = combo_input.get_attribute("value") or ""
                if current_val.strip() == customer_name.strip():
                    self._logger.info("Customer already selected - skipping")
                    return True
            except:
                pass
            
            # Open dropdown and type to filter
            combo_input.click()
            self._sleep(0.5)
            combo_input.send_keys(Keys.CONTROL, "a")
            combo_input.send_keys(Keys.DELETE)
            combo_input.send_keys(customer_name)
            self._sleep(1.5)
            
            # Find matching option
            option = self._find_customer_option(customer_name)
            
            if option is None:
                self._logger.warning(f"Customer option '{customer_name}' not found")
                self.take_screenshot("select_customer_not_found")
                return False
            
            try:
                option.click()
            except ElementClickInterceptedException:
                self._driver.execute_script("arguments[0].click();", option)
            
            self._sleep(2)
            self._logger.info(f"Customer selected: {customer_name}")
            return True
            
        except Exception as e:
            self._logger.error(f"Error selecting customer: {e}")
            self.take_screenshot("select_customer_error")
            return False
    
    def _find_customer_option(self, customer_name: str):
        """
        Find the customer option element in the ComboBox results list.
        Tries exact match first, then partial match (case-insensitive).
        Skips disabled options.
        
        Returns the WebElement or None.
        """
        target = customer_name.strip().lower()
        
        try:
            WebDriverWait(self._driver, 10).until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, "ul.ComboBox__results li.ComboBox__resultItem")
                )
            )
        except TimeoutException:
            return None
        
        options = self._driver.find_elements(
            By.CSS_SELECTOR, "ul.ComboBox__results li.ComboBox__resultItem"
        )
        
        # Exact match (skip disabled)
        for opt in options:
            try:
                if "-disabled" in (opt.get_attribute("class") or ""):
                    continue
                if (opt.text or "").strip().lower() == target:
                    return opt
            except:
                continue
        
        # Partial match (skip disabled)
        for opt in options:
            try:
                if "-disabled" in (opt.get_attribute("class") or ""):
                    continue
                if target in (opt.text or "").strip().lower():
                    return opt
            except:
                continue
        
        return None
    
    def search_po(self, po_number: str) -> POSearchResult:
        """
        Search for PO Number in Coupa Orders page.
        
        Flow:
        1. Navigate to Orders page (click Orders menu)
        2. Switch to iframe 'enterprise_frame'
        3. Enter PO Number in search field 'sf_connect_order_header'
        4. Press Enter to search
        5. Extract data from result table 'connect_order_header_table_tag'
        6. Check for "Nothing found" case
        
        Args:
            po_number: PO Number to search
        
        Returns:
            POSearchResult with found status and data
        
        Implements Requirements 7.1 - 7.5
        """
        self._logger.info(f"Searching PO: {po_number}")
        
        if not self._driver:
            raise BrowserError(message="Browser not open")
        
        try:
            # Switch to default content first
            try:
                self._driver.switch_to.default_content()
            except:
                pass
            
            # Step 1: Navigate to Orders page
            self._navigate_to_orders_page()
            
            # Step 2: Switch to enterprise_frame iframe
            self._switch_to_enterprise_frame()
            
            # Step 3: Enter PO Number in search field and press Enter
            self._search_po_in_field(po_number)
            
            # Step 4: Wait for results and extract data
            result = self._extract_po_search_result(po_number)
            
            # Switch back to default content
            try:
                self._driver.switch_to.default_content()
            except:
                pass
            
            return result
                
        except TimeoutException:
            self._logger.warning(f"Search timeout for PO: {po_number}")
            self.take_screenshot("po_search_timeout")
            # Switch back to default content
            try:
                self._driver.switch_to.default_content()
            except:
                pass
            return POSearchResult(
                found=False,
                po_number=po_number,
                error_message="Search timeout"
            )
        except Exception as e:
            self._logger.error(f"Search error for PO {po_number}: {e}")
            self.take_screenshot("po_search_error")
            # Switch back to default content
            try:
                self._driver.switch_to.default_content()
            except:
                pass
            return POSearchResult(
                found=False,
                po_number=po_number,
                error_message=str(e)
            )
    
    def _navigate_to_orders_page(self) -> None:
        """
        Navigate to Orders page by clicking Orders menu.
        
        Selector from UiPath: <webctrl id='main_nav_orders' parentid='mainNavigationContainer' tag='A' aaname='Orders' />
        """
        # Check URL first - fastest check
        current_url = self._driver.current_url
        if "/orders" in current_url.lower():
            self._logger.debug("Already on Orders page (URL check)")
            # Just wait for enterprise_frame to ensure page is ready
            try:
                WebDriverWait(self._driver, 5).until(
                    EC.presence_of_element_located((By.ID, "enterprise_frame"))
                )
            except:
                pass
            return
        
        self._logger.debug("Navigating to Orders page...")
        
        # Click Orders menu - use direct find_elements for speed
        orders_selectors = [
            (By.ID, "main_nav_orders"),
            (By.XPATH, "//a[@id='main_nav_orders']"),
            (By.XPATH, "//a[contains(@href, '/orders')]"),
            (By.LINK_TEXT, "Orders"),
        ]
        
        orders_link = None
        for by, selector in orders_selectors:
            try:
                elements = self._driver.find_elements(by, selector)
                for elem in elements:
                    if elem.is_displayed():
                        orders_link = elem
                        self._logger.debug(f"Found Orders link with selector: {selector}")
                        break
                if orders_link:
                    break
            except:
                continue
        
        if not orders_link:
            raise NavigationError(
                message="Orders menu not found",
                url=self._driver.current_url
            )
        
        orders_link.click()
        self._logger.debug("Clicked Orders menu")
        
        # Wait for page to load
        self._sleep(2)
        
        # Wait for enterprise_frame to be present (indicates Orders page loaded)
        WebDriverWait(self._driver, self.SEARCH_TIMEOUT).until(
            EC.presence_of_element_located((By.ID, "enterprise_frame"))
        )
        
        self._logger.debug("Orders page loaded")
    
    def _switch_to_enterprise_frame(self) -> None:
        """
        Switch to enterprise_frame iframe where the Orders table is located.
        
        Selector from UiPath: <webctrl id='enterprise_frame' tag='IFRAME' />
        """
        self._logger.debug("Switching to enterprise_frame...")
        
        # First switch to default content
        try:
            self._driver.switch_to.default_content()
        except:
            pass
        
        # Wait for iframe and switch to it
        iframe_selectors = [
            (By.ID, "enterprise_frame"),
            (By.NAME, "enterprise_frame"),
            (By.CSS_SELECTOR, "iframe#enterprise_frame"),
            (By.XPATH, "//iframe[@id='enterprise_frame']"),
        ]
        
        iframe = None
        for by, selector in iframe_selectors:
            try:
                iframe = WebDriverWait(self._driver, self.SEARCH_TIMEOUT).until(
                    EC.presence_of_element_located((by, selector))
                )
                if iframe:
                    self._logger.debug(f"Found enterprise_frame with selector: {selector}")
                    break
            except:
                continue
        
        if not iframe:
            raise NavigationError(
                message="enterprise_frame iframe not found",
                url=self._driver.current_url
            )
        
        # Switch to iframe
        self._driver.switch_to.frame(iframe)
        self._logger.debug("Switched to enterprise_frame")
        
        # Wait for iframe content to load
        self._sleep(1)
    
    def _search_po_in_field(self, po_number: str) -> None:
        """
        Enter PO Number in search field and press Enter.
        
        Selector: id='sf_connect_order_header'
        """
        self._logger.debug(f"Entering PO Number in search field: {po_number}")
        
        # Direct selector - no looping
        search_field = WebDriverWait(self._driver, 5).until(
            EC.presence_of_element_located((By.ID, "sf_connect_order_header"))
        )
        
        # Wait for field to be interactable
        WebDriverWait(self._driver, 5).until(
            EC.element_to_be_clickable((By.ID, "sf_connect_order_header"))
        )
        
        # Clear field and enter PO number
        search_field.clear()
        self._sleep(0.5)
        search_field.send_keys(po_number)
        self._sleep(0.5)
        
        # Press Enter to search
        search_field.send_keys(Keys.RETURN)
        self._logger.debug("Pressed Enter to search")
        
        # Wait for search results to load
        self._sleep(2)
    
    def _extract_po_search_result(self, po_number: str) -> POSearchResult:
        """
        Extract PO search result from the results table.
        
        Table selector from UiPath: <webctrl id='connect_order_header_table_tag' tag='TABLE' />
        Check "Nothing found": dtResultPO(0)("PO Number").ToString.contains("Nothing")
        """
        self._logger.debug("Extracting PO search result...")
        
        # Wait a moment for DOM to stabilize
        self._sleep(0.5)
        
        # Retry logic for stale element
        max_retries = 3
        for attempt in range(max_retries):
            try:
                # Direct selector - no looping
                result_table = WebDriverWait(self._driver, 5).until(
                    EC.presence_of_element_located((By.ID, "connect_order_header_table_tag"))
                )
                
                if not result_table:
                    self._logger.warning("Result table not found")
                    return POSearchResult(
                        found=False,
                        po_number=po_number,
                        error_message="Result table not found"
                    )
                
                # Get all rows from table body
                try:
                    tbody = result_table.find_element(By.TAG_NAME, "tbody")
                    rows = tbody.find_elements(By.TAG_NAME, "tr")
                except:
                    rows = result_table.find_elements(By.TAG_NAME, "tr")
                
                if not rows:
                    self._logger.warning("No rows found in result table")
                    return POSearchResult(
                        found=False,
                        po_number=po_number,
                        error_message="No results found"
                    )
                
                # Check first row for "Nothing found" message
                first_row = rows[0]
                first_row_text = first_row.text.strip()
                
                if "Nothing" in first_row_text or "No results" in first_row_text.lower() or "not found" in first_row_text.lower():
                    self._logger.warning(f"PO not found: {po_number}")
                    return POSearchResult(
                        found=False,
                        po_number=po_number,
                        error_message="PO Number Not Found"
                    )
                
                # Extract PO data from the first matching row
                po_data = self._extract_po_data_from_row(first_row, po_number)
                
                if po_data:
                    self._logger.info(f"PO found: {po_number}")
                    return POSearchResult(
                        found=True,
                        po_number=po_number,
                        po_data=po_data
                    )
                else:
                    # Try to find row with matching PO number
                    for row in rows:
                        row_text = row.text
                        if po_number in row_text:
                            po_data = self._extract_po_data_from_row(row, po_number)
                            if po_data:
                                self._logger.info(f"PO found: {po_number}")
                                return POSearchResult(
                                    found=True,
                                    po_number=po_number,
                                    po_data=po_data
                                )
                    
                    self._logger.warning(f"PO data extraction failed for: {po_number}")
                    return POSearchResult(
                        found=False,
                        po_number=po_number,
                        error_message="PO data extraction failed"
                    )
                    
            except Exception as e:
                if "stale element" in str(e).lower() and attempt < max_retries - 1:
                    self._logger.debug(f"Stale element, retrying ({attempt + 1}/{max_retries})...")
                    self._sleep(1)
                    continue
                raise
        
        return POSearchResult(
            found=False,
            po_number=po_number,
            error_message="Failed after retries"
        )
    
    def _extract_po_data_from_row(self, row, po_number: str) -> Optional[Dict[str, Any]]:
        """Extract PO data from a table row."""
        try:
            cells = row.find_elements(By.TAG_NAME, "td")
            
            if not cells:
                # Try with th elements (header row)
                cells = row.find_elements(By.TAG_NAME, "th")
            
            if not cells:
                return None
            
            # Extract data based on column positions
            # Typical Coupa Orders table columns: PO Number, Status, Supplier, Amount, etc.
            po_data = {
                "po_number": po_number,
                "row_text": row.text.strip()
            }
            
            # Try to extract specific columns
            if len(cells) >= 1:
                # First column usually contains PO Number
                po_cell = cells[0]
                po_data["po_number_cell"] = po_cell.text.strip()
                
                # Check for link in PO cell (for clicking later)
                try:
                    po_link = po_cell.find_element(By.TAG_NAME, "a")
                    po_data["po_link_text"] = po_link.text.strip()
                except:
                    pass
            
            if len(cells) >= 2:
                po_data["column_2"] = cells[1].text.strip()
            
            if len(cells) >= 3:
                po_data["column_3"] = cells[2].text.strip()
            
            if len(cells) >= 4:
                po_data["column_4"] = cells[3].text.strip()
            
            if len(cells) >= 5:
                po_data["column_5"] = cells[4].text.strip()
            
            # Store all cell texts
            po_data["all_cells"] = [cell.text.strip() for cell in cells]
            
            return po_data
            
        except Exception as e:
            self._logger.debug(f"Error extracting PO data from row: {e}")
            return None
    
    def _is_nothing_found(self) -> bool:
        """Check if 'Nothing found' message is displayed in the result table."""
        try:
            # Check inside enterprise_frame
            nothing_found_selectors = [
                (By.XPATH, "//*[contains(text(), 'Nothing found')]"),
                (By.XPATH, "//*[contains(text(), 'No results')]"),
                (By.XPATH, "//*[contains(text(), 'not found')]"),
                (By.CSS_SELECTOR, ".no-results"),
                (By.CSS_SELECTOR, ".empty-message"),
            ]
            
            for by, selector in nothing_found_selectors:
                try:
                    elements = self._driver.find_elements(by, selector)
                    if elements and any(e.is_displayed() for e in elements):
                        return True
                except:
                    continue
            
            return False
        except Exception:
            return False
    
    def handle_invoicing_details_popup(self, legal_entity: Optional[str] = None) -> bool:
        """
        Handle the "Choose Invoicing Details" popup that appears for some companies.
        
        This popup contains:
        - Legal Entity (dropdown) - required
        - Remit-To (dropdown) - auto-filled based on Legal Entity
        - Ship From Address (dropdown) - auto-filled based on Legal Entity
        
        The method will:
        1. Check if popup is present
        2. If present, select Legal Entity (first option if not specified)
        3. Click Save button
        
        Selectors from UiPath:
        - Popup detection: <webctrl id='enterprise_frame' tag='IFRAME' /><webctrl css-selector='body>div>div' idx='3' tag='DIV' />
        - Legal Entity dropdown: id='invoice_from_address_select'
        - Save button: id='addresses_picker_submit'
        
        Args:
            legal_entity: Legal entity name to select (optional, uses first option if not specified)
        
        Returns:
            True if popup was handled (or not present), False if error
        """
        self._logger.info("Checking for 'Choose Invoicing Details' popup...")
        
        if not self._driver:
            raise BrowserError(message="Browser not open")
        
        try:
            # Switch to default content first
            try:
                self._driver.switch_to.default_content()
            except:
                pass
            
            # Switch to enterprise_frame iframe
            self._switch_to_enterprise_frame()
            
            # Wait a moment for popup to appear
            self._sleep(2)
            
            # Check if popup is present
            if not self._is_invoicing_details_popup_present():
                self._logger.info("No 'Choose Invoicing Details' popup detected")
                # Switch back to default content
                try:
                    self._driver.switch_to.default_content()
                except:
                    pass
                return True
            
            self._logger.info("'Choose Invoicing Details' popup detected, filling form...")
            
            # Get Legal Entity dropdown and select value
            self._select_legal_entity_popup(legal_entity)
            
            # Click Save button
            self._click_popup_save_button()
            
            self._logger.info("'Choose Invoicing Details' popup handled successfully")
            
            # Switch back to default content
            try:
                self._driver.switch_to.default_content()
            except:
                pass
            
            return True
            
        except Exception as e:
            self._logger.error(f"Error handling invoicing details popup: {e}")
            self.take_screenshot("invoicing_details_popup_error")
            # Switch back to default content
            try:
                self._driver.switch_to.default_content()
            except:
                pass
            return False
    
    def _is_invoicing_details_popup_present(self) -> bool:
        """
        Check if 'Choose Invoicing Details' popup is present.
        
        Detection methods:
        1. Check for popup title "Choose Invoicing Details"
        2. Check for Legal Entity dropdown in popup context
        3. Check for addresses_picker_submit button
        """
        try:
            # Method 1: Check for popup title
            popup_title_selectors = [
                (By.XPATH, "//*[contains(text(), 'Choose Invoicing Details')]"),
                (By.XPATH, "//div[contains(@class, 'ui-dialog-title') and contains(text(), 'Choose')]"),
                (By.XPATH, "//span[contains(@class, 'ui-dialog-title')]"),
            ]
            
            for by, selector in popup_title_selectors:
                try:
                    elements = self._driver.find_elements(by, selector)
                    if elements and any(e.is_displayed() for e in elements):
                        self._logger.debug(f"Popup detected via title: {selector}")
                        return True
                except:
                    continue
            
            # Method 2: Check for Legal Entity label in popup
            legal_entity_label_selectors = [
                (By.XPATH, "//label[contains(text(), 'Legal Entity')]"),
                (By.XPATH, "//*[contains(@class, 'group_label') and contains(text(), 'Legal Entity')]"),
            ]
            
            for by, selector in legal_entity_label_selectors:
                try:
                    elements = self._driver.find_elements(by, selector)
                    if elements and any(e.is_displayed() for e in elements):
                        # Also check if Save button exists (to confirm it's the popup)
                        save_btn = self._driver.find_elements(By.ID, "addresses_picker_submit")
                        if save_btn and any(b.is_displayed() for b in save_btn):
                            self._logger.debug(f"Popup detected via Legal Entity label + Save button")
                            return True
                except:
                    continue
            
            # Method 3: Check for the popup dialog container
            popup_container_selectors = [
                (By.CSS_SELECTOR, ".ui-dialog"),
                (By.CSS_SELECTOR, "div.ui-dialog-content"),
                (By.XPATH, "//div[contains(@class, 'ui-dialog')]"),
            ]
            
            for by, selector in popup_container_selectors:
                try:
                    elements = self._driver.find_elements(by, selector)
                    if elements and any(e.is_displayed() for e in elements):
                        # Verify it's the invoicing details popup by checking for dropdown
                        dropdown = self._driver.find_elements(By.ID, "invoice_from_address_select")
                        if dropdown and any(d.is_displayed() for d in dropdown):
                            self._logger.debug(f"Popup detected via dialog container + dropdown")
                            return True
                except:
                    continue
            
            return False
            
        except Exception as e:
            self._logger.debug(f"Error checking for popup: {e}")
            return False
    
    def _select_legal_entity_popup(self, legal_entity: Optional[str] = None) -> None:
        """
        Select Legal Entity in the popup dropdown.
        
        Selector from UiPath:
        <webctrl id='invoice_from_address_select' tag='SELECT' />
        
        Args:
            legal_entity: Legal entity name to select (uses first non-empty option if not specified)
        """
        self._logger.debug("Selecting Legal Entity in popup...")
        
        from selenium.webdriver.support.ui import Select
        
        # Find Legal Entity dropdown
        dropdown_selectors = [
            (By.ID, "invoice_from_address_select"),
            (By.CSS_SELECTOR, "select#invoice_from_address_select"),
            (By.XPATH, "//select[@id='invoice_from_address_select']"),
            (By.XPATH, "//label[contains(text(), 'Legal Entity')]/following::select[1]"),
        ]
        
        dropdown = None
        for by, selector in dropdown_selectors:
            try:
                dropdown = WebDriverWait(self._driver, 5).until(
                    EC.presence_of_element_located((by, selector))
                )
                if dropdown and dropdown.is_displayed():
                    self._logger.debug(f"Found Legal Entity dropdown with selector: {selector}")
                    break
                dropdown = None
            except:
                continue
        
        if not dropdown:
            raise NavigationError(
                message="Legal Entity dropdown not found in popup",
                url=self._driver.current_url
            )
        
        # Get all options
        select = Select(dropdown)
        options = select.options
        
        if legal_entity:
            # Try to select by visible text
            try:
                select.select_by_visible_text(legal_entity)
                self._logger.debug(f"Selected Legal Entity: {legal_entity}")
                return
            except:
                # Try partial match
                for option in options:
                    if legal_entity.lower() in option.text.lower():
                        select.select_by_visible_text(option.text)
                        self._logger.debug(f"Selected Legal Entity (partial match): {option.text}")
                        return
        
        # If no legal_entity specified or not found, select first non-empty option
        for option in options:
            option_text = option.text.strip()
            if option_text and option_text.lower() != "select":
                select.select_by_visible_text(option_text)
                self._logger.debug(f"Selected first available Legal Entity: {option_text}")
                
                # Press Enter to confirm selection
                dropdown.send_keys(Keys.RETURN)
                self._sleep(1)
                return
        
        raise NavigationError(
            message="No valid Legal Entity options found in dropdown",
            url=self._driver.current_url
        )
    
    def _click_popup_save_button(self) -> None:
        """
        Click the Save button in the popup.
        
        Selector from UiPath:
        <webctrl id='addresses_picker_submit' tag='BUTTON' type='button' class='blue ui-button' aaname='Save' />
        """
        self._logger.debug("Clicking Save button in popup...")
        
        save_btn_selectors = [
            (By.ID, "addresses_picker_submit"),
            (By.CSS_SELECTOR, "#addresses_picker_submit"),
            (By.CSS_SELECTOR, "button#addresses_picker_submit"),
            (By.XPATH, "//button[@id='addresses_picker_submit']"),
            (By.XPATH, "//span[text()='Save']/parent::button"),
            (By.XPATH, "//button[contains(@class, 'ui-button') and contains(., 'Save')]"),
            (By.XPATH, "//button[contains(., 'Save') and not(contains(., 'Draft'))]"),
        ]
        
        save_btn = None
        for by, selector in save_btn_selectors:
            try:
                save_btn = WebDriverWait(self._driver, 5).until(
                    EC.element_to_be_clickable((by, selector))
                )
                if save_btn:
                    self._logger.debug(f"Found Save button with selector: {selector}")
                    break
            except:
                continue
        
        if not save_btn:
            raise NavigationError(
                message="Save button not found in popup",
                url=self._driver.current_url
            )
        
        # Click Save button
        try:
            save_btn.click()
        except ElementClickInterceptedException:
            self._driver.execute_script("arguments[0].click();", save_btn)
        
        self._logger.debug("Clicked Save button")
        
        # Wait for popup to close
        self._sleep(2)
    
    def count_invoice_lines(self) -> int:
        """
        Count the number of invoice lines in the Create Invoice form.
        
        This method should be called after:
        1. click_create_invoice()
        2. handle_invoicing_details_popup() (if applicable)
        
        The line count is important for:
        - Determining attachment requirements
        - Scanned invoice handling
        
        Selector from UiPath:
        <webctrl tag='DIV' class='*s-invoiceLineTableRowRoot*' id='invoice_header_invoice_lines_attributes_*' />
        Parent: <webctrl id='inv_body' tag='DIV' />
        
        Returns:
            Number of invoice lines (0 if none found or error)
        """
        self._logger.info("Counting invoice lines...")
        
        if not self._driver:
            raise BrowserError(message="Browser not open")
        
        try:
            # Switch to default content first
            try:
                self._driver.switch_to.default_content()
            except:
                pass
            
            # Switch to enterprise_frame iframe
            self._switch_to_enterprise_frame()
            
            # Wait for page to load
            self._sleep(2)
            
            # Find all line items using multiple selectors
            line_count = 0
            
            # Method 1: Find by class 's-invoiceLineTableRowRoot'
            line_selectors = [
                (By.CSS_SELECTOR, "div.s-invoiceLineTableRowRoot"),
                (By.CSS_SELECTOR, "div[class*='s-invoiceLineTableRowRoot']"),
                (By.XPATH, "//div[contains(@class, 's-invoiceLineTableRowRoot')]"),
                (By.XPATH, "//div[contains(@id, 'invoice_header_invoice_lines_attributes_')]"),
                (By.CSS_SELECTOR, "div[id^='invoice_header_invoice_lines_attributes_']"),
            ]
            
            for by, selector in line_selectors:
                try:
                    elements = self._driver.find_elements(by, selector)
                    if elements:
                        # Filter only visible elements
                        visible_elements = [e for e in elements if e.is_displayed()]
                        if visible_elements:
                            line_count = len(visible_elements)
                            self._logger.debug(f"Found {line_count} lines with selector: {selector}")
                            break
                except:
                    continue
            
            # Method 2: If not found, try counting by line item IDs
            if line_count == 0:
                try:
                    # Find all elements with invoice_header_invoice_lines_attributes_ in ID
                    all_line_elements = self._driver.find_elements(
                        By.XPATH, 
                        "//*[starts-with(@id, 'invoice_header_invoice_lines_attributes_')]"
                    )
                    
                    # Extract unique line IDs
                    line_ids = set()
                    for element in all_line_elements:
                        element_id = element.get_attribute("id")
                        if element_id:
                            # Extract the numeric part (e.g., invoice_header_invoice_lines_attributes_1802540)
                            parts = element_id.replace("invoice_header_invoice_lines_attributes_", "").split("_")
                            if parts and parts[0].isdigit():
                                line_ids.add(parts[0])
                    
                    if line_ids:
                        line_count = len(line_ids)
                        self._logger.debug(f"Found {line_count} unique line IDs: {line_ids}")
                except Exception as e:
                    self._logger.debug(f"Error counting by line IDs: {e}")
            
            # Method 3: Try finding by the Lines section header and counting rows
            if line_count == 0:
                try:
                    # Find the Lines section
                    lines_section = self._driver.find_element(
                        By.XPATH, 
                        "//div[contains(@class, 'section') and .//span[contains(text(), 'Lines')]]"
                    )
                    if lines_section:
                        # Count rows within this section
                        rows = lines_section.find_elements(By.CSS_SELECTOR, "div.row, tr")
                        line_count = len([r for r in rows if r.is_displayed()])
                        self._logger.debug(f"Found {line_count} lines in Lines section")
                except:
                    pass
            
            self._logger.info(f"Invoice line count: {line_count}")
            
            # Switch back to default content
            try:
                self._driver.switch_to.default_content()
            except:
                pass
            
            return line_count
            
        except Exception as e:
            self._logger.error(f"Error counting invoice lines: {e}")
            # Switch back to default content
            try:
                self._driver.switch_to.default_content()
            except:
                pass
            return 0
    
    def get_invoice_line_details(self) -> List[Dict[str, Any]]:
        """
        Get details of all invoice lines.
        
        Returns a list of dictionaries containing line details:
        - line_id: The unique line ID
        - qty: Quantity
        - price: Unit price
        - amount: Total amount
        - description: Line description
        
        Returns:
            List of line detail dictionaries
        """
        self._logger.info("Getting invoice line details...")
        
        if not self._driver:
            raise BrowserError(message="Browser not open")
        
        line_details = []
        
        try:
            # Switch to default content first
            try:
                self._driver.switch_to.default_content()
            except:
                pass
            
            # Switch to enterprise_frame iframe
            self._switch_to_enterprise_frame()
            
            # Wait for page to load
            self._sleep(1)
            
            # Find all line item containers
            line_containers = self._driver.find_elements(
                By.CSS_SELECTOR, 
                "div[class*='s-invoiceLineTableRowRoot']"
            )
            
            if not line_containers:
                # Try alternative selector
                line_containers = self._driver.find_elements(
                    By.XPATH,
                    "//div[contains(@id, 'invoice_header_invoice_lines_attributes_') and contains(@class, 'Row')]"
                )
            
            for idx, container in enumerate(line_containers):
                try:
                    if not container.is_displayed():
                        continue
                    
                    line_info = {
                        "index": idx + 1,
                        "line_id": None,
                        "qty": None,
                        "price": None,
                        "amount": None,
                        "description": None
                    }
                    
                    # Get line ID from container
                    container_id = container.get_attribute("id")
                    if container_id and "invoice_header_invoice_lines_attributes_" in container_id:
                        line_info["line_id"] = container_id.replace("invoice_header_invoice_lines_attributes_", "").split("_")[0]
                    
                    # Try to get Qty
                    try:
                        qty_input = container.find_element(By.CSS_SELECTOR, "input[name*='quantity']")
                        line_info["qty"] = qty_input.get_attribute("value")
                    except:
                        pass
                    
                    # Try to get Price
                    try:
                        price_input = container.find_element(By.CSS_SELECTOR, "input[name*='price']")
                        line_info["price"] = price_input.get_attribute("value")
                    except:
                        pass
                    
                    # Try to get Amount (usually displayed as text)
                    try:
                        amount_elem = container.find_element(By.CSS_SELECTOR, ".amount, .total, [class*='amount']")
                        line_info["amount"] = amount_elem.text.strip()
                    except:
                        pass
                    
                    # Try to get Description
                    try:
                        desc_elem = container.find_element(By.CSS_SELECTOR, ".description, [class*='description']")
                        line_info["description"] = desc_elem.text.strip()
                    except:
                        pass
                    
                    line_details.append(line_info)
                    
                except Exception as e:
                    self._logger.debug(f"Error getting details for line {idx}: {e}")
                    continue
            
            self._logger.info(f"Got details for {len(line_details)} lines")
            
            # Switch back to default content
            try:
                self._driver.switch_to.default_content()
            except:
                pass
            
            return line_details
            
        except Exception as e:
            self._logger.error(f"Error getting invoice line details: {e}")
            # Switch back to default content
            try:
                self._driver.switch_to.default_content()
            except:
                pass
            return []
    
    def get_invoice_lines_with_remarks(self) -> List[Dict[str, Any]]:
        """
        Get all invoice lines with their Remarks text.
        
        This is used to match Site IDs from the input file with line remarks.
        
        Selector from UiPath:
        - Line container: <webctrl id='invoice_header_invoice_lines_attributes_{line_id}' />
        - Remarks: <webctrl class='form_element wrapper_horizontal invoiceLineCFA s-invoiceLineCFA' 
                   parentid='invoice_header_invoice_lines_attributes_{line_id}' />
        - Qty input: <webctrl name='invoice_header[invoice_lines_attributes][{line_id}][quantity]' 
                     class='s-creditNoteQuantityField quantity_field_input' />
        
        Returns:
            List of dicts with keys: line_id, remarks, qty_element_name
        """
        self._logger.info("Getting invoice lines with remarks...")
        
        if not self._driver:
            raise BrowserError(message="Browser not open")
        
        lines_data = []
        
        try:
            # Switch to default content first
            try:
                self._driver.switch_to.default_content()
            except:
                pass
            
            # Switch to enterprise_frame iframe
            self._switch_to_enterprise_frame()
            
            # Wait for page to load
            self._sleep(2)
            
            # Find all line IDs first
            line_ids = self._get_all_line_ids()
            self._logger.debug(f"Found {len(line_ids)} line IDs: {line_ids}")
            
            for line_id in line_ids:
                line_info = {
                    "line_id": line_id,
                    "remarks": "",
                    "current_qty": "",
                    "qty_input_name": f"invoice_header[invoice_lines_attributes][{line_id}][quantity]"
                }
                
                # Get Remarks text
                remarks = self._get_line_remarks(line_id)
                line_info["remarks"] = remarks
                
                # Get current Qty value
                current_qty = self._get_line_qty(line_id)
                line_info["current_qty"] = current_qty
                
                lines_data.append(line_info)
                self._logger.debug(f"Line {line_id}: remarks='{remarks[:50]}...' qty={current_qty}")
            
            self._logger.info(f"Got {len(lines_data)} lines with remarks")
            
            # Switch back to default content
            try:
                self._driver.switch_to.default_content()
            except:
                pass
            
            return lines_data
            
        except Exception as e:
            self._logger.error(f"Error getting invoice lines with remarks: {e}")
            try:
                self._driver.switch_to.default_content()
            except:
                pass
            return []
    
    def _get_all_line_ids(self) -> List[str]:
        """Extract all unique line IDs from the invoice form."""
        line_ids = []
        
        try:
            # Find all elements with invoice_header_invoice_lines_attributes_ in ID
            all_line_elements = self._driver.find_elements(
                By.XPATH, 
                "//*[starts-with(@id, 'invoice_header_invoice_lines_attributes_')]"
            )
            
            # Extract unique line IDs
            seen_ids = set()
            for element in all_line_elements:
                element_id = element.get_attribute("id")
                if element_id:
                    # Extract the numeric part (e.g., invoice_header_invoice_lines_attributes_1802540)
                    parts = element_id.replace("invoice_header_invoice_lines_attributes_", "").split("_")
                    if parts and parts[0].isdigit():
                        if parts[0] not in seen_ids:
                            seen_ids.add(parts[0])
                            line_ids.append(parts[0])
        except Exception as e:
            self._logger.debug(f"Error extracting line IDs: {e}")
        
        return line_ids
    
    def _get_line_remarks(self, line_id: str) -> str:
        """
        Get the Remarks text for a specific line.
        
        Selector from UiPath:
        <webctrl class='form_element wrapper_horizontal invoiceLineCFA s-invoiceLineCFA' 
                 parentid='invoice_header_invoice_lines_attributes_{line_id}' />
        """
        remarks = ""
        
        try:
            # Method 1: Find by parent ID and class
            remarks_selectors = [
                (By.CSS_SELECTOR, f"div[id='invoice_header_invoice_lines_attributes_{line_id}'] .invoiceLineCFA"),
                (By.CSS_SELECTOR, f"div[id='invoice_header_invoice_lines_attributes_{line_id}'] .s-invoiceLineCFA"),
                (By.XPATH, f"//div[@id='invoice_header_invoice_lines_attributes_{line_id}']//div[contains(@class, 'invoiceLineCFA')]"),
                (By.XPATH, f"//div[contains(@id, 'invoice_header_invoice_lines_attributes_{line_id}')]//div[contains(@class, 'invoiceLineCFA')]"),
                # Try finding Remarks label and get adjacent text
                (By.XPATH, f"//div[contains(@id, '{line_id}')]//span[contains(text(), 'Remarks')]/following-sibling::*"),
                (By.XPATH, f"//div[contains(@id, '{line_id}')]//*[contains(text(), 'Remarks')]/parent::*/following-sibling::*"),
            ]
            
            for by, selector in remarks_selectors:
                try:
                    elements = self._driver.find_elements(by, selector)
                    for elem in elements:
                        text = elem.text.strip()
                        # Also try aaname attribute
                        if not text:
                            text = elem.get_attribute("aaname") or ""
                        if text and len(text) > len(remarks):
                            remarks = text
                except:
                    continue
            
            # Method 2: If not found, try to find any element with Remarks-like content in the line
            if not remarks:
                try:
                    line_container = self._driver.find_element(
                        By.ID, f"invoice_header_invoice_lines_attributes_{line_id}"
                    )
                    # Get all text content and look for Remarks section
                    all_text = line_container.text
                    if "Remarks" in all_text:
                        # Extract text after "Remarks"
                        parts = all_text.split("Remarks")
                        if len(parts) > 1:
                            # Get the text after Remarks until next section
                            remarks_text = parts[1].split("\n")[0].strip()
                            if remarks_text:
                                remarks = remarks_text
                except:
                    pass
                    
        except Exception as e:
            self._logger.debug(f"Error getting remarks for line {line_id}: {e}")
        
        return remarks
    
    def _get_line_qty(self, line_id: str) -> str:
        """Get the current Qty value for a specific line."""
        qty = ""
        
        try:
            qty_selectors = [
                (By.NAME, f"invoice_header[invoice_lines_attributes][{line_id}][quantity]"),
                (By.CSS_SELECTOR, f"input[name='invoice_header[invoice_lines_attributes][{line_id}][quantity]']"),
                (By.XPATH, f"//input[@name='invoice_header[invoice_lines_attributes][{line_id}][quantity]']"),
                (By.CSS_SELECTOR, f"div[id*='{line_id}'] input.quantity_field_input"),
            ]
            
            for by, selector in qty_selectors:
                try:
                    elem = self._driver.find_element(by, selector)
                    if elem:
                        qty = elem.get_attribute("value") or ""
                        break
                except:
                    continue
                    
        except Exception as e:
            self._logger.debug(f"Error getting qty for line {line_id}: {e}")
        
        return qty
    
    def _check_qty_uniformity_per_site(self, site_qty_mapping: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        """
        Check if all qty values for each Site ID are identical.
        
        Groups by BOTH site_id AND site_id_active (both point to same data).
        This allows matching by either field when searching in remarks.
        
        Returns:
            Dict with key = site_id, value = {
                "is_uniform": bool,
                "qty_value": value if uniform else None,
                "qty_by_line": dict of line_number -> qty
            }
        """
        site_groups = {}
        
        for mapping in site_qty_mapping:
            site_id = str(mapping.get("site_id", "")).strip()
            site_id_active = str(mapping.get("site_id_active", "")).strip()
            qty = mapping.get("qty", 0)
            line_number = str(mapping.get("line_number", "")).strip()
            
            # Add to BOTH site_id and site_id_active groups (if they exist)
            site_ids_to_add = []
            if site_id:
                site_ids_to_add.append(site_id)
            if site_id_active and site_id_active != site_id:
                site_ids_to_add.append(site_id_active)
            
            for sid in site_ids_to_add:
                if sid not in site_groups:
                    site_groups[sid] = {
                        "qty_values": [],
                        "qty_by_line": {}
                    }
                
                site_groups[sid]["qty_values"].append(qty)
                if line_number:
                    site_groups[sid]["qty_by_line"][line_number] = qty
        
        # Analyze each site
        result = {}
        for site_id, data in site_groups.items():
            qty_values = data["qty_values"]
            unique_qty = set(str(q) for q in qty_values)
            
            if len(unique_qty) == 1:
                result[site_id] = {
                    "is_uniform": True,
                    "qty_value": qty_values[0],
                    "qty_by_line": data["qty_by_line"]
                }
            else:
                result[site_id] = {
                    "is_uniform": False,
                    "qty_value": None,
                    "qty_by_line": data["qty_by_line"]
                }
        
        return result
    
    def _extract_line_number_from_coupa(self, element_text: str) -> str:
        """
        Extract line number from Coupa element text.
        Format: "{PO_Number}-{LineNumber}" → extract LineNumber
        Example: "4810093431-8" → "8"
        """
        import re
        if not element_text:
            return ""
        
        match = re.search(r'-(\d+)$', element_text.strip())
        if match:
            return match.group(1)
        
        return ""
    
    def _get_po_line_reference(self, line_id: str) -> str:
        """
        Get the PO-Line reference from a Coupa line.
        
        Element example:
        <a class="hover_target" id="smash_2779400_display">4810093431-8</a>
        """
        po_line_ref = ""
        
        try:
            selectors = [
                (By.XPATH, f"//div[contains(@id, 'invoice_header_invoice_lines_attributes_{line_id}')]//a[contains(@id, 'smash_') and contains(@id, '_display')]"),
                (By.XPATH, f"//div[contains(@id, 'invoice_header_invoice_lines_attributes_{line_id}')]//a[contains(@class, 'hover_target')]"),
                (By.XPATH, f"//div[contains(@id, 'invoice_header_invoice_lines_attributes_{line_id}')]//a[contains(@href, 'supplier_order_headers')]"),
            ]
            
            for by, selector in selectors:
                try:
                    elements = self._driver.find_elements(by, selector)
                    if elements:
                        po_line_ref = elements[0].text.strip()
                        if po_line_ref:
                            break
                except:
                    continue
            
        except Exception as e:
            self._logger.debug(f"Error getting PO-Line reference for line {line_id}: {e}")
        
        return po_line_ref
    
    def fill_line_quantities(self, site_qty_mapping: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Fill Qty for each invoice line based on Site ID matching.
        
        V2 Logic with qty validation:
        1. Check qty uniformity per Site ID
        2. If uniform → Simple Mode: fill qty directly by site ID match
        3. If different → Line Number Mode: match by Invoice Line Number
        4. Lines not found in data source → use last qty for that site (fallback)
        5. If Site ID found AND Qty == "Used Coupa" → skip (leave default)
        6. If no Site ID found → fill with 0
        
        Args:
            site_qty_mapping: List of dicts with keys:
                - site_id: IOH Site ID (e.g., "11TGR0121")
                - site_id_active: IOH Site ID Active (fallback)
                - qty: Quantity value (number or "Used Coupa")
                - line_number: Invoice Line Number from data source
        
        Returns:
            Dict with:
                - success: bool
                - lines_filled: int
                - lines_skipped: int
                - lines_zero: int
                - lines_fallback: int
                - details: List of line fill results
        """
        self._logger.info(f"Filling line quantities based on {len(site_qty_mapping)} site mappings...")
        
        if not self._driver:
            raise BrowserError(message="Browser not open")
        
        result = {
            "success": True,
            "lines_filled": 0,
            "lines_skipped": 0,
            "lines_zero": 0,
            "lines_fallback": 0,
            "details": []
        }
        
        # Step 1: Analyze qty uniformity per site
        site_analysis = self._check_qty_uniformity_per_site(site_qty_mapping)
        
        for site_id, data in site_analysis.items():
            mode = "UNIFORM" if data["is_uniform"] else "DIFFERENT"
            self._logger.debug(f"Site {site_id}: {mode} (lines: {list(data['qty_by_line'].keys())})")
        
        # Track last qty used per site (for fallback)
        last_qty_used = {}
        
        try:
            # Switch to default content first
            try:
                self._driver.switch_to.default_content()
            except:
                pass
            
            # Switch to enterprise_frame iframe
            self._switch_to_enterprise_frame()
            
            # Wait for page to load
            self._sleep(2)
            
            # Get all lines with remarks
            lines = self.get_invoice_lines_with_remarks()
            
            # Need to switch back to iframe after get_invoice_lines_with_remarks
            self._switch_to_enterprise_frame()
            
            for line in lines:
                line_id = line["line_id"]
                remarks = line["remarks"].lower()
                
                line_result = {
                    "line_id": line_id,
                    "remarks_preview": line["remarks"][:100],
                    "po_line_ref": "",
                    "coupa_line_number": "",
                    "matched_site_id": None,
                    "mode": "",
                    "qty_filled": None,
                    "action": ""
                }
                
                # Get PO-Line reference and extract line number
                po_line_ref = self._get_po_line_reference(line_id)
                coupa_line_number = self._extract_line_number_from_coupa(po_line_ref)
                line_result["po_line_ref"] = po_line_ref
                line_result["coupa_line_number"] = coupa_line_number
                
                # Select Document Type for this line - always "ZOPX - General Opex"
                self._select_custom_dropdown(
                    f"invoice_header_invoice_lines_attributes_{line_id}_custom_field_4",
                    "ZOPX - General Opex",
                    f"Document Type (line {line_id})"
                )
                
                # Find matching Site ID in remarks
                matched_site = None
                match_type = None
                
                for mapping in site_qty_mapping:
                    site_id = str(mapping.get("site_id", "")).strip()
                    site_id_active = str(mapping.get("site_id_active", "")).strip()
                    
                    if site_id and site_id.lower() in remarks:
                        matched_site = site_id
                        match_type = "site_id"
                        break
                    
                    if site_id_active and site_id_active.lower() in remarks:
                        matched_site = site_id_active
                        match_type = "site_id_active"
                        break
                
                if matched_site:
                    line_result["matched_site_id"] = f"{matched_site} ({match_type})"
                    site_data = site_analysis.get(matched_site, {})
                    
                    # Check if qty is "Used Coupa"
                    mapping_qty = None
                    for m in site_qty_mapping:
                        m_site_id = str(m.get("site_id", "")).strip()
                        m_site_active = str(m.get("site_id_active", "")).strip()
                        if m_site_id == matched_site or m_site_active == matched_site:
                            mapping_qty = m.get("qty")
                            break
                    
                    if str(mapping_qty).strip().lower() == "used coupa":
                        # Skip - leave default
                        line_result["mode"] = "skip"
                        line_result["action"] = "skipped (Used Coupa)"
                        result["lines_skipped"] += 1
                        self._logger.debug(f"Line {line_id}: Skipped (Used Coupa)")
                    elif site_data.get("is_uniform"):
                        # Simple Mode - all qty values are the same
                        qty = site_data.get("qty_value", 0)
                        qty_str = str(qty).replace(",", ".")
                        self._set_line_qty(line_id, qty_str)
                        line_result["mode"] = "simple"
                        line_result["qty_filled"] = qty_str
                        line_result["action"] = f"filled with {qty_str} (uniform)"
                        result["lines_filled"] += 1
                        last_qty_used[matched_site] = qty
                        self._logger.debug(f"Line {line_id}: {qty_str} (uniform, site {matched_site})")
                    else:
                        # Line Number Mode - qty values are different
                        qty_by_line = site_data.get("qty_by_line", {})
                        
                        if coupa_line_number and coupa_line_number in qty_by_line:
                            qty = qty_by_line[coupa_line_number]
                            qty_str = str(qty).replace(",", ".")
                            self._set_line_qty(line_id, qty_str)
                            line_result["mode"] = "line_number"
                            line_result["qty_filled"] = qty_str
                            line_result["action"] = f"filled with {qty_str} (line #{coupa_line_number})"
                            result["lines_filled"] += 1
                            last_qty_used[matched_site] = qty
                            self._logger.debug(f"Line {line_id}: {qty_str} (line #{coupa_line_number}, site {matched_site})")
                        else:
                            # Fallback to last qty for this site
                            qty = last_qty_used.get(matched_site, 0)
                            qty_str = str(qty).replace(",", ".")
                            self._set_line_qty(line_id, qty_str)
                            line_result["mode"] = "fallback"
                            line_result["qty_filled"] = qty_str
                            line_result["action"] = f"filled with {qty_str} (fallback)"
                            result["lines_fallback"] += 1
                            self._logger.debug(f"Line {line_id}: {qty_str} (fallback, site {matched_site})")
                else:
                    # No Site ID found - fill with 0
                    self._set_line_qty(line_id, "0")
                    line_result["mode"] = "no_match"
                    line_result["qty_filled"] = "0"
                    line_result["action"] = "filled with 0 (no match)"
                    result["lines_zero"] += 1
                    self._logger.debug(f"Line {line_id}: 0 (no site match)")
                
                result["details"].append(line_result)
            
            self._logger.info(
                f"Line quantities: {result['lines_filled']} filled, "
                f"{result['lines_skipped']} skipped, {result['lines_fallback']} fallback, "
                f"{result['lines_zero']} zero"
            )
            
            # Switch back to default content
            try:
                self._driver.switch_to.default_content()
            except:
                pass
            
            return result
            
        except Exception as e:
            self._logger.error(f"Error filling line quantities: {e}")
            result["success"] = False
            try:
                self._driver.switch_to.default_content()
            except:
                pass
            return result
    
    def _set_line_qty(self, line_id: str, qty_value: str) -> bool:
        """
        Set the Qty value for a specific line.
        
        Selector from UiPath:
        <webctrl name='invoice_header[invoice_lines_attributes][{line_id}][quantity]' 
                 class='s-creditNoteQuantityField quantity_field_input' />
        """
        try:
            qty_selectors = [
                (By.NAME, f"invoice_header[invoice_lines_attributes][{line_id}][quantity]"),
                (By.CSS_SELECTOR, f"input[name='invoice_header[invoice_lines_attributes][{line_id}][quantity]']"),
                (By.XPATH, f"//input[@name='invoice_header[invoice_lines_attributes][{line_id}][quantity]']"),
            ]
            
            qty_input = None
            for by, selector in qty_selectors:
                try:
                    qty_input = self._driver.find_element(by, selector)
                    if qty_input:
                        break
                except:
                    continue
            
            if not qty_input:
                self._logger.warning(f"Qty input not found for line {line_id}")
                return False
            
            # Clear and set value
            qty_input.clear()
            self._sleep(0.2)
            qty_input.send_keys(qty_value)
            self._sleep(0.2)
            
            return True
            
        except Exception as e:
            self._logger.warning(f"Error setting qty for line {line_id}: {e}")
            return False

    def click_create_invoice(self, po_number: str) -> bool:
        """
        Click "Create Invoice for PO" button after PO is found.
        
        This method should be called after search_po() returns found=True.
        It will click the create invoice button (coin icon) for the specified PO.
        
        Selector from UiPath:
        <webctrl id='enterprise_frame' tag='IFRAME' />
        <webctrl id='connect_order_header_table_tag' tag='TABLE' />
        <webctrl type='' tag='IMG' class='s-invoiceButton icon_button sprite-coins' aaname='Create invoice for PO #*' />
        
        Args:
            po_number: PO Number to create invoice for
        
        Returns:
            True if click successful, False otherwise
        
        Raises:
            BrowserError: If browser not open
            NavigationError: If button not found
        """
        self._logger.info(f"Clicking Create Invoice for PO: {po_number}")
        
        if not self._driver:
            raise BrowserError(message="Browser not open")
        
        try:
            # Switch to default content first
            try:
                self._driver.switch_to.default_content()
            except:
                pass
            
            # Switch to enterprise_frame iframe
            self._switch_to_enterprise_frame()
            
            # Find and click the Create Invoice button
            # The button is an IMG with class 's-invoiceButton icon_button sprite-coins'
            # and aaname (aria-name) 'Create invoice for PO #...'
            create_invoice_selectors = [
                # Primary selector based on UiPath
                (By.XPATH, f"//img[contains(@class, 's-invoiceButton') and contains(@class, 'sprite-coins')]"),
                (By.XPATH, f"//img[contains(@title, 'Create invoice') and contains(@title, '{po_number}')]"),
                (By.XPATH, f"//img[contains(@alt, 'Create invoice') and contains(@alt, '{po_number}')]"),
                (By.XPATH, f"//img[contains(@aria-label, 'Create invoice')]"),
                # Try with aaname attribute
                (By.XPATH, f"//img[contains(@aaname, 'Create invoice for PO')]"),
                # Try finding by class only
                (By.CSS_SELECTOR, "img.s-invoiceButton.icon_button.sprite-coins"),
                (By.CSS_SELECTOR, "img.s-invoiceButton"),
                (By.CSS_SELECTOR, "img.sprite-coins"),
                # Try finding button/link with create invoice text
                (By.XPATH, "//a[contains(@title, 'Create invoice')]"),
                (By.XPATH, "//button[contains(@title, 'Create invoice')]"),
                # Try finding in the table row containing the PO number
                (By.XPATH, f"//tr[contains(., '{po_number}')]//img[contains(@class, 'invoiceButton') or contains(@class, 'sprite-coins')]"),
                (By.XPATH, f"//tr[contains(., '{po_number}')]//img"),
            ]
            
            create_invoice_btn = None
            for by, selector in create_invoice_selectors:
                try:
                    elements = self._driver.find_elements(by, selector)
                    for element in elements:
                        if element.is_displayed():
                            create_invoice_btn = element
                            self._logger.debug(f"Found Create Invoice button with selector: {selector}")
                            break
                    if create_invoice_btn:
                        break
                except:
                    continue
            
            if not create_invoice_btn:
                # Try to find any clickable element in the PO row
                self._logger.debug("Primary selectors failed, trying to find button in PO row...")
                try:
                    # Find the table
                    table = self._driver.find_element(By.ID, "connect_order_header_table_tag")
                    # Find row containing PO number
                    rows = table.find_elements(By.TAG_NAME, "tr")
                    for row in rows:
                        if po_number in row.text:
                            # Find all images in this row
                            images = row.find_elements(By.TAG_NAME, "img")
                            for img in images:
                                # Check if it looks like a create invoice button
                                img_class = img.get_attribute("class") or ""
                                img_title = img.get_attribute("title") or ""
                                img_alt = img.get_attribute("alt") or ""
                                
                                if ("invoice" in img_class.lower() or 
                                    "coins" in img_class.lower() or
                                    "create" in img_title.lower() or
                                    "invoice" in img_title.lower() or
                                    "create" in img_alt.lower() or
                                    "invoice" in img_alt.lower()):
                                    create_invoice_btn = img
                                    self._logger.debug(f"Found Create Invoice button in row (class={img_class})")
                                    break
                            
                            # If still not found, try the first clickable image
                            if not create_invoice_btn and images:
                                for img in images:
                                    if img.is_displayed():
                                        create_invoice_btn = img
                                        self._logger.debug("Using first visible image in PO row")
                                        break
                            break
                except Exception as e:
                    self._logger.debug(f"Error finding button in row: {e}")
            
            if not create_invoice_btn:
                self.take_screenshot("create_invoice_btn_not_found")
                # Switch back to default content
                try:
                    self._driver.switch_to.default_content()
                except:
                    pass
                raise NavigationError(
                    message=f"Create Invoice button not found for PO: {po_number}",
                    url=self._driver.current_url
                )
            
            # Scroll element into view if needed
            try:
                self._driver.execute_script("arguments[0].scrollIntoView(true);", create_invoice_btn)
                self._sleep(0.5)
            except:
                pass
            
            # Click the button
            try:
                create_invoice_btn.click()
            except ElementClickInterceptedException:
                # Try JavaScript click if regular click fails
                self._driver.execute_script("arguments[0].click();", create_invoice_btn)
            
            self._logger.info(f"Clicked Create Invoice button for PO: {po_number}")
            
            # Wait for invoice form to load
            self._sleep(3)
            
            # Switch back to default content (invoice form might be in different context)
            try:
                self._driver.switch_to.default_content()
            except:
                pass
            
            return True
            
        except NavigationError:
            raise
        except Exception as e:
            self._logger.error(f"Error clicking Create Invoice for PO {po_number}: {e}")
            self.take_screenshot("create_invoice_click_error")
            # Switch back to default content
            try:
                self._driver.switch_to.default_content()
            except:
                pass
            raise NavigationError(
                message=f"Failed to click Create Invoice for PO: {po_number}",
                url=self._driver.current_url,
                details=str(e)
            )
    
    def fill_invoice_form(
        self,
        invoice_number: str,
        invoice_date: Optional[str] = None,
        invoice_description: Optional[str] = None,
        currency: str = "IDR",
        scanned_invoice_path: Optional[str] = None,
        attachment_paths: Optional[List[str]] = None,
        # Additional custom fields (Form To)
        ba_number: Optional[str] = None,
        tax_invoice_number: Optional[str] = None,
        tax_invoice_date: Optional[str] = None,
        wht_code: Optional[str] = None,
        po_number_field: Optional[str] = None
    ) -> bool:
        """
        Fill the Create Invoice form fields.
        
        This method should be called after click_create_invoice().
        It fills the invoice form with the provided data.
        
        Form fields:
        - Invoice # : id='invoice_header_invoice_number'
        - Invoice Date : id='invoice_header_local_invoice_date' (format: dd/MM/yy)
        - Invoice Description : id='invoice_header_comments' (max 50 chars)
        - Currency : id='invoice_header_currency_id' (SELECT dropdown)
        - Scanned Invoice : id='invoice_header_image_scan' (file upload - single file)
        - Attachments : id='invoice_header_attachments' (file upload - multiple files)
        
        Custom fields (Form To):
        - BA Number : id='invoice_header_custom_field_10'
        - Tax Invoice Number : id='invoice_header_custom_field_4'
        - Tax Invoice Date : id='invoice_header_custom_field_5' (format: dd/MM/yy)
        - WHT Code : id='invoice_header_custom_field_7' (SELECT dropdown)
        - PO Number : id='invoice_header_custom_field_8'
        
        Args:
            invoice_number: Invoice number to fill
            invoice_date: Invoice date in dd/MM/yy format (default: today)
            invoice_description: Invoice description (max 50 chars, default: invoice_number)
            currency: Currency code (default: IDR)
            scanned_invoice_path: Path to scanned invoice file (single PDF)
            attachment_paths: List of paths to attachment files (multiple files)
            ba_number: BA Number (custom field)
            tax_invoice_number: Tax Invoice Number (custom field)
            tax_invoice_date: Tax Invoice Date in dd/MM/yy format (custom field)
            wht_code: WHT Code (custom field - dropdown)
            po_number_field: PO Number for custom field (custom field)
        
        Returns:
            True if form filled successfully
        
        Raises:
            BrowserError: If browser not open
            NavigationError: If form fields not found
        """
        self._logger.info(f"Filling invoice form: Invoice#={invoice_number}")
        
        if not self._driver:
            raise BrowserError(message="Browser not open")
        
        try:
            # Switch to default content first
            try:
                self._driver.switch_to.default_content()
            except:
                pass
            
            # Switch to enterprise_frame iframe
            self._switch_to_enterprise_frame()
            
            # Wait for form to be loaded
            self._sleep(2)
            
            # 1. Fill Invoice Number
            self._fill_invoice_number(invoice_number)
            
            # 2. Fill Invoice Date
            if invoice_date is None:
                invoice_date = datetime.now().strftime("%d/%m/%y")
            self._fill_invoice_date(invoice_date)
            
            # 3. Fill Invoice Description (max 50 chars)
            if invoice_description is None:
                # Default to invoice number if not provided
                invoice_description = invoice_number[:50] if invoice_number else "Invoice"
            self._fill_invoice_description(invoice_description)
            
            # 4. Select Currency
            self._select_currency(currency)
            
            # 5. Fill custom fields (Form To)
            # Type of Invoice (dropdown) - always "PO Backed Invoice - General"
            self._select_custom_dropdown("invoice_header_custom_field_18", "PO Backed Invoice - General", "Type of Invoice")
            
            if ba_number:
                self._fill_custom_field("invoice_header_custom_field_10", ba_number, "BA Number")
            
            if tax_invoice_number:
                self._fill_custom_field("invoice_header_custom_field_4", tax_invoice_number, "Tax Invoice Number")
            
            if tax_invoice_date:
                self._fill_custom_field_date("invoice_header_custom_field_5", tax_invoice_date, "Tax Invoice Date")
            
            if wht_code:
                self._select_custom_dropdown("invoice_header_custom_field_7", wht_code, "WHT Code")
            
            if po_number_field:
                self._fill_custom_field("invoice_header_custom_field_8", po_number_field, "PO Number")
            
            # 6. Upload Scanned Invoice if provided
            if scanned_invoice_path:
                self._upload_scanned_invoice(scanned_invoice_path)
            
            # 7. Upload Attachments if provided
            if attachment_paths:
                self._upload_attachments(attachment_paths)
            
            self._logger.info("Invoice form filled successfully")
            
            # Switch back to default content
            try:
                self._driver.switch_to.default_content()
            except:
                pass
            
            return True
            
        except Exception as e:
            self._logger.error(f"Error filling invoice form: {e}")
            self.take_screenshot("fill_invoice_form_error")
            # Switch back to default content
            try:
                self._driver.switch_to.default_content()
            except:
                pass
            raise NavigationError(
                message="Failed to fill invoice form",
                url=self._driver.current_url,
                details=str(e)
            )
    
    def _fill_invoice_number(self, invoice_number: str) -> None:
        """
        Fill Invoice Number field.
        
        Selector from UiPath:
        <webctrl id='invoice_header_invoice_number' type='text' tag='INPUT' class='s-creditNoteEnabledField' aaname='*Invoice #' />
        """
        self._logger.debug(f"Filling Invoice Number: {invoice_number}")
        
        invoice_num_selectors = [
            (By.ID, "invoice_header_invoice_number"),
            (By.CSS_SELECTOR, "input#invoice_header_invoice_number"),
            (By.XPATH, "//input[@id='invoice_header_invoice_number']"),
            (By.CSS_SELECTOR, "input.s-creditNoteEnabledField"),
            (By.XPATH, "//input[contains(@class, 's-creditNoteEnabledField')]"),
            (By.XPATH, "//label[contains(text(), 'Invoice #')]/following::input[1]"),
        ]
        
        invoice_field = None
        for by, selector in invoice_num_selectors:
            try:
                invoice_field = WebDriverWait(self._driver, 5).until(
                    EC.presence_of_element_located((by, selector))
                )
                if invoice_field and invoice_field.is_displayed():
                    self._logger.debug(f"Found Invoice # field with selector: {selector}")
                    break
                invoice_field = None
            except:
                continue
        
        if not invoice_field:
            raise NavigationError(
                message="Invoice Number field not found",
                url=self._driver.current_url
            )
        
        # Clear and fill
        invoice_field.clear()
        self._sleep(0.3)
        invoice_field.send_keys(invoice_number)
        self._logger.debug(f"Filled Invoice Number: {invoice_number}")
    
    def _fill_invoice_date(self, invoice_date: str) -> None:
        """
        Fill Invoice Date field.
        
        Selector from UiPath:
        <webctrl id='invoice_header_local_invoice_date' type='text' tag='INPUT' class='date date_picker hasDatepicker' placeholder='mm/dd/yy' aaname='*Invoice Date' />
        
        Args:
            invoice_date: Date in dd/MM/yy format
        """
        self._logger.debug(f"Filling Invoice Date: {invoice_date}")
        
        invoice_date_selectors = [
            (By.ID, "invoice_header_local_invoice_date"),
            (By.CSS_SELECTOR, "input#invoice_header_local_invoice_date"),
            (By.XPATH, "//input[@id='invoice_header_local_invoice_date']"),
            (By.CSS_SELECTOR, "input.date_picker"),
            (By.CSS_SELECTOR, "input.hasDatepicker"),
            (By.XPATH, "//input[contains(@class, 'date_picker')]"),
            (By.XPATH, "//label[contains(text(), 'Invoice Date')]/following::input[1]"),
        ]
        
        date_field = None
        for by, selector in invoice_date_selectors:
            try:
                date_field = WebDriverWait(self._driver, 5).until(
                    EC.presence_of_element_located((by, selector))
                )
                if date_field and date_field.is_displayed():
                    self._logger.debug(f"Found Invoice Date field with selector: {selector}")
                    break
                date_field = None
            except:
                continue
        
        if not date_field:
            raise NavigationError(
                message="Invoice Date field not found",
                url=self._driver.current_url
            )
        
        # Clear and fill, then press Enter to close datepicker
        date_field.clear()
        self._sleep(0.3)
        date_field.send_keys(invoice_date)
        date_field.send_keys(Keys.RETURN)
        self._sleep(0.5)
        self._logger.debug(f"Filled Invoice Date: {invoice_date}")
    
    def _fill_invoice_description(self, description: str) -> None:
        """
        Fill Invoice Description field.
        
        Selector from UiPath:
        <webctrl id='invoice_header_comments' type='text' tag='INPUT' />
        
        Args:
            description: Invoice description (max 50 characters)
        """
        # Truncate to max 50 characters
        description = description[:50] if description else ""
        self._logger.debug(f"Filling Invoice Description: {description}")
        
        description_selectors = [
            (By.ID, "invoice_header_comments"),
            (By.CSS_SELECTOR, "input#invoice_header_comments"),
            (By.XPATH, "//input[@id='invoice_header_comments']"),
            (By.CSS_SELECTOR, "textarea#invoice_header_comments"),
            (By.XPATH, "//textarea[@id='invoice_header_comments']"),
            (By.XPATH, "//label[contains(text(), 'Description')]/following::input[1]"),
            (By.XPATH, "//label[contains(text(), 'Description')]/following::textarea[1]"),
            (By.XPATH, "//label[contains(text(), 'Comments')]/following::input[1]"),
        ]
        
        desc_field = None
        for by, selector in description_selectors:
            try:
                desc_field = WebDriverWait(self._driver, 5).until(
                    EC.presence_of_element_located((by, selector))
                )
                if desc_field and desc_field.is_displayed():
                    self._logger.debug(f"Found Invoice Description field with selector: {selector}")
                    break
                desc_field = None
            except:
                continue
        
        if not desc_field:
            self._logger.warning("Invoice Description field not found, skipping")
            return
        
        # Clear and fill
        desc_field.clear()
        self._sleep(0.3)
        desc_field.send_keys(description)
        self._logger.debug(f"Filled Invoice Description: {description}")
    
    def _select_currency(self, currency: str) -> None:
        """
        Select Currency from dropdown.
        
        Selector from UiPath:
        <webctrl id='invoice_header_currency_id' type='' tag='SELECT' class='' aaname='' />
        
        Args:
            currency: Currency code (e.g., IDR, USD)
        """
        self._logger.debug(f"Selecting Currency: {currency}")
        
        from selenium.webdriver.support.ui import Select
        
        currency_selectors = [
            (By.ID, "invoice_header_currency_id"),
            (By.CSS_SELECTOR, "select#invoice_header_currency_id"),
            (By.XPATH, "//select[@id='invoice_header_currency_id']"),
            (By.XPATH, "//label[contains(text(), 'Currency')]/following::select[1]"),
        ]
        
        currency_field = None
        for by, selector in currency_selectors:
            try:
                currency_field = WebDriverWait(self._driver, 5).until(
                    EC.presence_of_element_located((by, selector))
                )
                if currency_field and currency_field.is_displayed():
                    self._logger.debug(f"Found Currency field with selector: {selector}")
                    break
                currency_field = None
            except:
                continue
        
        if not currency_field:
            raise NavigationError(
                message="Currency field not found",
                url=self._driver.current_url
            )
        
        # Select currency from dropdown
        try:
            select = Select(currency_field)
            select.select_by_value(currency)
        except:
            # Try by visible text
            try:
                select = Select(currency_field)
                select.select_by_visible_text(currency)
            except:
                # Try typing directly
                currency_field.send_keys(currency)
        
        self._logger.debug(f"Selected Currency: {currency}")
    
    def _upload_scanned_invoice(self, file_path: str) -> None:
        """
        Upload scanned invoice file (single file).
        
        Selector from UiPath:
        <webctrl id='invoice_header_image_scan' type='file' tag='INPUT' class='' aaname='' />
        
        Args:
            file_path: Full path to the scanned invoice file
        """
        self._logger.debug(f"Uploading scanned invoice: {file_path}")
        
        # Verify file exists
        if not Path(file_path).exists():
            self._logger.warning(f"Scanned invoice file not found: {file_path}")
            return
        
        file_input_selectors = [
            (By.ID, "invoice_header_image_scan"),
            (By.CSS_SELECTOR, "input#invoice_header_image_scan"),
            (By.XPATH, "//input[@id='invoice_header_image_scan']"),
            (By.XPATH, "//label[contains(text(), 'Scanned Invoice')]/following::input[@type='file'][1]"),
            (By.XPATH, "//label[contains(text(), 'Image Scan')]/following::input[@type='file'][1]"),
        ]
        
        file_input = None
        for by, selector in file_input_selectors:
            try:
                file_input = self._driver.find_element(by, selector)
                if file_input:
                    self._logger.debug(f"Found scanned invoice input with selector: {selector}")
                    break
            except:
                continue
        
        if not file_input:
            self._logger.warning("Scanned invoice upload field not found, skipping")
            return
        
        # Send file path to input
        file_input.send_keys(str(Path(file_path).absolute()))
        self._sleep(2)  # Wait for upload
        self._logger.info(f"Uploaded scanned invoice: {Path(file_path).name}")
    
    def _upload_attachments(self, file_paths: List[str]) -> None:
        """
        Upload attachment files (multiple files).
        
        Selector from UiPath:
        <webctrl id='invoice_header_attachments' type='file' tag='INPUT' />
        or
        <webctrl aaname='Add File' tag='A' />
        
        For multiple files, Coupa typically requires:
        1. Click "Add File" or find the file input
        2. Upload each file one by one
        
        Args:
            file_paths: List of full paths to attachment files
        """
        self._logger.debug(f"Uploading {len(file_paths)} attachments")
        
        if not file_paths:
            return
        
        # Filter out non-existent files
        valid_files = []
        for fp in file_paths:
            if Path(fp).exists():
                valid_files.append(fp)
            else:
                self._logger.warning(f"Attachment file not found: {fp}")
        
        if not valid_files:
            self._logger.warning("No valid attachment files to upload")
            return
        
        # Try to find attachment input field
        attachment_input_selectors = [
            (By.ID, "invoice_header_attachments"),
            (By.CSS_SELECTOR, "input#invoice_header_attachments"),
            (By.XPATH, "//input[@id='invoice_header_attachments']"),
            (By.XPATH, "//label[contains(text(), 'Attachments')]/following::input[@type='file'][1]"),
            (By.XPATH, "//div[contains(@class, 'attachment')]//input[@type='file']"),
            (By.CSS_SELECTOR, "input[name*='attachment'][type='file']"),
        ]
        
        file_input = None
        for by, selector in attachment_input_selectors:
            try:
                file_input = self._driver.find_element(by, selector)
                if file_input:
                    self._logger.debug(f"Found attachment input with selector: {selector}")
                    break
            except:
                continue
        
        if file_input:
            # Some file inputs accept multiple files at once
            # Try to upload all files at once first
            try:
                # Join all file paths with newline (works for some browsers)
                all_files = "\n".join([str(Path(fp).absolute()) for fp in valid_files])
                file_input.send_keys(all_files)
                self._sleep(2)
                self._logger.info(f"Uploaded {len(valid_files)} attachments at once")
                return
            except Exception as e:
                self._logger.debug(f"Multi-file upload failed, trying one by one: {e}")
        
        # If multi-file upload failed or no input found, try clicking "Add File" button
        # and upload one by one
        for i, fp in enumerate(valid_files):
            try:
                self._logger.debug(f"Uploading attachment {i+1}/{len(valid_files)}: {Path(fp).name}")
                
                # Try to find "Add File" link/button
                add_file_selectors = [
                    (By.XPATH, "//a[contains(text(), 'Add File')]"),
                    (By.XPATH, "//a[contains(@class, 'add-file')]"),
                    (By.XPATH, "//button[contains(text(), 'Add File')]"),
                    (By.XPATH, "//span[contains(text(), 'Add File')]/parent::a"),
                    (By.CSS_SELECTOR, "a.add-attachment"),
                    (By.CSS_SELECTOR, ".attachment-add"),
                ]
                
                add_file_btn = None
                for by, selector in add_file_selectors:
                    try:
                        add_file_btn = self._driver.find_element(by, selector)
                        if add_file_btn and add_file_btn.is_displayed():
                            break
                        add_file_btn = None
                    except:
                        continue
                
                if add_file_btn:
                    # Click Add File button
                    try:
                        add_file_btn.click()
                    except ElementClickInterceptedException:
                        self._driver.execute_script("arguments[0].click();", add_file_btn)
                    self._sleep(1)
                
                # Find file input (might be newly created after clicking Add File)
                file_input = None
                for by, selector in attachment_input_selectors:
                    try:
                        inputs = self._driver.find_elements(by, selector)
                        # Get the last one (newly added)
                        if inputs:
                            file_input = inputs[-1]
                            break
                    except:
                        continue
                
                # Also try generic file input
                if not file_input:
                    try:
                        inputs = self._driver.find_elements(By.CSS_SELECTOR, "input[type='file']")
                        # Filter to find attachment-related ones
                        for inp in inputs:
                            inp_id = inp.get_attribute("id") or ""
                            inp_name = inp.get_attribute("name") or ""
                            if "attachment" in inp_id.lower() or "attachment" in inp_name.lower():
                                file_input = inp
                                break
                        # If still not found, use the last file input
                        if not file_input and inputs:
                            file_input = inputs[-1]
                    except:
                        pass
                
                if file_input:
                    file_input.send_keys(str(Path(fp).absolute()))
                    self._sleep(2)  # Wait for upload
                    self._logger.info(f"Uploaded attachment: {Path(fp).name}")
                else:
                    self._logger.warning(f"Could not find file input for attachment: {Path(fp).name}")
                    
            except Exception as e:
                self._logger.warning(f"Failed to upload attachment {Path(fp).name}: {e}")
        
        self._logger.info(f"Finished uploading {len(valid_files)} attachments")
    
    def _fill_custom_field(self, field_id: str, value: str, field_name: str) -> None:
        """
        Fill a custom text field.
        
        Custom fields in Coupa invoice form:
        - BA Number: id='invoice_header_custom_field_10'
        - Tax Invoice Number: id='invoice_header_custom_field_4'
        - PO Number: id='invoice_header_custom_field_8'
        
        Args:
            field_id: HTML element ID of the field
            value: Value to fill
            field_name: Human-readable field name for logging
        """
        self._logger.debug(f"Filling custom field {field_name}: {value}")
        
        field_selectors = [
            (By.ID, field_id),
            (By.CSS_SELECTOR, f"input#{field_id}"),
            (By.XPATH, f"//input[@id='{field_id}']"),
            (By.CSS_SELECTOR, f"#{field_id}"),
        ]
        
        field = None
        for by, selector in field_selectors:
            try:
                field = WebDriverWait(self._driver, 5).until(
                    EC.presence_of_element_located((by, selector))
                )
                if field and field.is_displayed():
                    self._logger.debug(f"Found {field_name} field with selector: {selector}")
                    break
                field = None
            except:
                continue
        
        if not field:
            self._logger.warning(f"Custom field {field_name} (id={field_id}) not found, skipping")
            return
        
        # Clear and fill
        try:
            field.clear()
            self._sleep(0.3)
            field.send_keys(value)
            self._logger.debug(f"Filled {field_name}: {value}")
        except Exception as e:
            self._logger.warning(f"Failed to fill {field_name}: {e}")
    
    def _fill_custom_field_date(self, field_id: str, date_value: str, field_name: str) -> None:
        """
        Fill a custom date picker field.
        
        Custom date fields in Coupa invoice form:
        - Tax Invoice Date: id='invoice_header_custom_field_5' (format: mm/dd/yyyy)
        
        Args:
            field_id: HTML element ID of the field
            date_value: Date value to fill (format: mm/dd/yyyy)
            field_name: Human-readable field name for logging
        """
        self._logger.debug(f"Filling custom date field {field_name}: {date_value}")
        
        field_selectors = [
            (By.ID, field_id),
            (By.CSS_SELECTOR, f"input#{field_id}"),
            (By.XPATH, f"//input[@id='{field_id}']"),
            (By.CSS_SELECTOR, f"input.date_picker#{field_id}"),
            (By.CSS_SELECTOR, f"input.hasDatepicker#{field_id}"),
        ]
        
        field = None
        for by, selector in field_selectors:
            try:
                field = WebDriverWait(self._driver, 5).until(
                    EC.presence_of_element_located((by, selector))
                )
                if field and field.is_displayed():
                    self._logger.debug(f"Found {field_name} field with selector: {selector}")
                    break
                field = None
            except:
                continue
        
        if not field:
            self._logger.warning(f"Custom date field {field_name} (id={field_id}) not found, skipping")
            return
        
        # Clear and fill, then press Enter to close datepicker
        try:
            field.clear()
            self._sleep(0.3)
            field.send_keys(date_value)
            field.send_keys(Keys.RETURN)
            self._sleep(0.5)
            self._logger.debug(f"Filled {field_name}: {date_value}")
        except Exception as e:
            self._logger.warning(f"Failed to fill {field_name}: {e}")
    
    def _select_custom_dropdown(self, field_id: str, value: str, field_name: str) -> None:
        """
        Select a value from a custom dropdown field.
        
        Custom dropdown fields in Coupa invoice form:
        - WHT Code: id='invoice_header_custom_field_7' (SELECT dropdown)
        
        The UiPath workflow uses Type Into with the value + Enter key to select.
        We'll try both Select approach and Type Into approach.
        
        Args:
            field_id: HTML element ID of the field
            value: Value to select
            field_name: Human-readable field name for logging
        """
        self._logger.debug(f"Selecting custom dropdown {field_name}: {value}")
        
        from selenium.webdriver.support.ui import Select
        
        field_selectors = [
            (By.ID, field_id),
            (By.CSS_SELECTOR, f"select#{field_id}"),
            (By.XPATH, f"//select[@id='{field_id}']"),
            (By.CSS_SELECTOR, f"#{field_id}"),
        ]
        
        field = None
        for by, selector in field_selectors:
            try:
                field = WebDriverWait(self._driver, 5).until(
                    EC.presence_of_element_located((by, selector))
                )
                if field and field.is_displayed():
                    self._logger.debug(f"Found {field_name} field with selector: {selector}")
                    break
                field = None
            except:
                continue
        
        if not field:
            self._logger.warning(f"Custom dropdown {field_name} (id={field_id}) not found, skipping")
            return
        
        # Normalize value - remove extra whitespace
        import re
        normalized_value = re.sub(r'\s+', ' ', value).strip()
        
        # Try different selection methods
        try:
            # Method 1: Use Select class for SELECT elements
            tag_name = field.tag_name.lower()
            if tag_name == "select":
                select = Select(field)
                
                # Try exact match by visible text
                try:
                    select.select_by_visible_text(normalized_value)
                    self._logger.debug(f"Selected {field_name} by visible text: {normalized_value}")
                    return
                except:
                    pass
                
                # Try partial match
                for option in select.options:
                    option_text = option.text.strip()
                    if normalized_value.lower() in option_text.lower():
                        select.select_by_visible_text(option_text)
                        self._logger.debug(f"Selected {field_name} by partial match: {option_text}")
                        return
                
                # Try by value attribute
                try:
                    select.select_by_value(normalized_value)
                    self._logger.debug(f"Selected {field_name} by value: {normalized_value}")
                    return
                except:
                    pass
            
            # Method 2: Type Into approach (like UiPath)
            # This works for both SELECT and custom dropdowns
            field.click()
            self._sleep(0.3)
            field.send_keys(normalized_value)
            field.send_keys(Keys.RETURN)
            self._sleep(0.5)
            self._logger.debug(f"Typed into {field_name}: {normalized_value}")
            
        except Exception as e:
            self._logger.warning(f"Failed to select {field_name}: {e}")
    
    def fill_vat(self, vat_code: str) -> bool:
        """
        Fill the VAT dropdown in Totals & Taxes section.
        
        Selector from UiPath:
        <webctrl id='invoice_tax_code_selector_1' tag='SELECT' 
                 class='tax_code s-tax_code invoice_tax_code_selector s-invoice_tax_code_selector supplier_summary_select_tax_code_or_rate' />
        
        Args:
            vat_code: VAT code to select (e.g., "ID: M1")
        
        Returns:
            True if VAT filled successfully
        """
        self._logger.info(f"Filling VAT: {vat_code}")
        
        if not self._driver:
            raise BrowserError(message="Browser not open")
        
        try:
            # Switch to default content first
            try:
                self._driver.switch_to.default_content()
            except:
                pass
            
            # Switch to enterprise_frame iframe
            self._switch_to_enterprise_frame()
            
            # Wait for page to load
            self._sleep(1)
            
            from selenium.webdriver.support.ui import Select
            import re
            
            # Find VAT dropdown
            vat_selectors = [
                (By.ID, "invoice_tax_code_selector_1"),
                (By.CSS_SELECTOR, "select#invoice_tax_code_selector_1"),
                (By.CSS_SELECTOR, "select.tax_code"),
                (By.CSS_SELECTOR, "select.invoice_tax_code_selector"),
                (By.XPATH, "//select[@id='invoice_tax_code_selector_1']"),
                (By.XPATH, "//select[contains(@class, 'tax_code')]"),
            ]
            
            vat_dropdown = None
            for by, selector in vat_selectors:
                try:
                    vat_dropdown = WebDriverWait(self._driver, 5).until(
                        EC.presence_of_element_located((by, selector))
                    )
                    if vat_dropdown and vat_dropdown.is_displayed():
                        self._logger.debug(f"Found VAT dropdown with selector: {selector}")
                        break
                    vat_dropdown = None
                except:
                    continue
            
            if not vat_dropdown:
                self._logger.warning("VAT dropdown not found")
                try:
                    self._driver.switch_to.default_content()
                except:
                    pass
                return False
            
            # Normalize VAT code - remove extra whitespace
            normalized_vat = re.sub(r'\s+', ' ', vat_code).strip()
            
            # Try different selection methods
            try:
                # Method 1: Use Select class
                select = Select(vat_dropdown)
                
                # Try exact match by visible text
                try:
                    select.select_by_visible_text(normalized_vat)
                    self._logger.debug(f"Selected VAT by visible text: {normalized_vat}")
                    self._sleep(0.5)
                    try:
                        self._driver.switch_to.default_content()
                    except:
                        pass
                    return True
                except:
                    pass
                
                # Try partial match
                for option in select.options:
                    option_text = option.text.strip()
                    if normalized_vat.lower() in option_text.lower():
                        select.select_by_visible_text(option_text)
                        self._logger.debug(f"Selected VAT by partial match: {option_text}")
                        self._sleep(0.5)
                        try:
                            self._driver.switch_to.default_content()
                        except:
                            pass
                        return True
                
            except Exception as e:
                self._logger.debug(f"Select method failed: {e}")
            
            # Method 2: Type Into approach (like UiPath)
            try:
                vat_dropdown.click()
                self._sleep(0.3)
                vat_dropdown.send_keys(normalized_vat)
                vat_dropdown.send_keys(Keys.RETURN)
                self._sleep(0.5)
                self._logger.debug(f"Typed VAT: {normalized_vat}")
                try:
                    self._driver.switch_to.default_content()
                except:
                    pass
                return True
            except Exception as e:
                self._logger.debug(f"Type method failed: {e}")
            
            self._logger.warning(f"Failed to select VAT: {vat_code}")
            try:
                self._driver.switch_to.default_content()
            except:
                pass
            return False
            
        except Exception as e:
            self._logger.error(f"Error filling VAT: {e}")
            try:
                self._driver.switch_to.default_content()
            except:
                pass
            return False
    
    def click_calculate(self) -> bool:
        """
        Click the Calculate button to calculate invoice totals.
        
        Selector from UiPath:
        <webctrl parentid='calculate_button' type='' tag='SPAN' class='' aaname='Calculate' />
        
        Returns:
            True if click successful
        """
        self._logger.info("Clicking Calculate button...")
        
        if not self._driver:
            raise BrowserError(message="Browser not open")
        
        try:
            # Switch to default content first
            try:
                self._driver.switch_to.default_content()
            except:
                pass
            
            # Switch to enterprise_frame iframe
            self._switch_to_enterprise_frame()
            
            calculate_selectors = [
                (By.ID, "calculate_button"),
                (By.CSS_SELECTOR, "#calculate_button"),
                (By.CSS_SELECTOR, "button#calculate_button"),
                (By.XPATH, "//button[@id='calculate_button']"),
                (By.XPATH, "//span[text()='Calculate']/parent::button"),
                (By.XPATH, "//button[contains(., 'Calculate')]"),
                (By.XPATH, "//*[@id='calculate_button']//span"),
                (By.XPATH, "//span[text()='Calculate']"),
            ]
            
            calculate_btn = None
            for by, selector in calculate_selectors:
                try:
                    calculate_btn = WebDriverWait(self._driver, 5).until(
                        EC.element_to_be_clickable((by, selector))
                    )
                    if calculate_btn:
                        self._logger.debug(f"Found Calculate button with selector: {selector}")
                        break
                except:
                    continue
            
            if not calculate_btn:
                self.take_screenshot("calculate_btn_not_found")
                # Switch back to default content
                try:
                    self._driver.switch_to.default_content()
                except:
                    pass
                raise NavigationError(
                    message="Calculate button not found",
                    url=self._driver.current_url
                )
            
            # Scroll into view and click
            try:
                self._driver.execute_script("arguments[0].scrollIntoView(true);", calculate_btn)
                self._sleep(0.5)
            except:
                pass
            
            try:
                calculate_btn.click()
            except ElementClickInterceptedException:
                self._driver.execute_script("arguments[0].click();", calculate_btn)
            
            self._logger.info("Clicked Calculate button")
            
            # Wait for calculation to complete
            self._sleep(3)
            
            # Switch back to default content
            try:
                self._driver.switch_to.default_content()
            except:
                pass
            
            return True
            
        except NavigationError:
            raise
        except Exception as e:
            self._logger.error(f"Error clicking Calculate: {e}")
            self.take_screenshot("calculate_click_error")
            try:
                self._driver.switch_to.default_content()
            except:
                pass
            raise NavigationError(
                message="Failed to click Calculate button",
                url=self._driver.current_url,
                details=str(e)
            )
    
    def get_invoice_total(self) -> Optional[float]:
        """
        Get the Total amount from the invoice form after Calculate.
        
        This reads the "Total" value from the Totals & Taxes section.
        Should be called after click_calculate().
        
        Selector from UiPath:
        <webctrl id='totalWithTaxes' tag='SPAN' class='s-totalWithTaxes number totalVal' />
        
        Returns:
            Total amount as float, or None if not found
        """
        self._logger.info("Getting invoice total from Coupa...")
        
        if not self._driver:
            raise BrowserError(message="Browser not open")
        
        try:
            # Switch to default content first
            try:
                self._driver.switch_to.default_content()
            except:
                pass
            
            # Switch to enterprise_frame iframe
            self._switch_to_enterprise_frame()
            
            # Wait for page to load
            self._sleep(1)
            
            # Find Total value using the specific selector from UiPath
            total_selectors = [
                # Primary selector from UiPath
                (By.ID, "totalWithTaxes"),
                (By.CSS_SELECTOR, "#totalWithTaxes"),
                (By.CSS_SELECTOR, "span#totalWithTaxes"),
                (By.CSS_SELECTOR, "span.s-totalWithTaxes"),
                (By.CSS_SELECTOR, "span.totalVal"),
                (By.XPATH, "//span[@id='totalWithTaxes']"),
                (By.XPATH, "//span[contains(@class, 's-totalWithTaxes')]"),
                (By.XPATH, "//span[contains(@class, 'totalVal')]"),
            ]
            
            total_text = None
            for by, selector in total_selectors:
                try:
                    element = WebDriverWait(self._driver, 5).until(
                        EC.presence_of_element_located((by, selector))
                    )
                    if element and element.is_displayed():
                        total_text = element.text.strip()
                        if total_text:
                            self._logger.debug(f"Found total with selector {selector}: {total_text}")
                            break
                except:
                    continue
            
            if not total_text:
                self._logger.warning("Could not find Total value in invoice form")
                try:
                    self._driver.switch_to.default_content()
                except:
                    pass
                return None
            
            # Parse the total value
            # UiPath does: strNetAmountCoupa.Replace(".", "").Replace(",", "")
            # This removes all dots and commas to get raw number
            import re
            
            # Remove all non-digit characters except for the last decimal separator
            # First, let's see if it's formatted like "270,273,030" or "270.273.030"
            cleaned = total_text.strip()
            
            # Remove currency symbols and spaces
            cleaned = re.sub(r'[^\d.,]', '', cleaned)
            
            # Indonesian/European format uses dots as thousand separator
            # So "270.273.030" should become "270273030"
            # Remove all dots and commas (like UiPath does)
            cleaned = cleaned.replace(".", "").replace(",", "")
            
            if not cleaned:
                self._logger.warning(f"Could not parse total value: {total_text}")
                try:
                    self._driver.switch_to.default_content()
                except:
                    pass
                return None
            
            total_value = float(cleaned)
            self._logger.info(f"Invoice total from Coupa: {total_value:,.0f}")
            
            try:
                self._driver.switch_to.default_content()
            except:
                pass
            
            return total_value
            
        except Exception as e:
            self._logger.error(f"Error getting invoice total: {e}")
            try:
                self._driver.switch_to.default_content()
            except:
                pass
            return None
    
    def validate_invoice_amount(self, expected_amount: float, tolerance: float = 10.0) -> Dict[str, Any]:
        """
        Validate invoice total against expected amount from Excel.
        
        Should be called after click_calculate().
        
        Args:
            expected_amount: Sum of Invoice Amount from Excel (filtered by PO and Invoice Number)
            tolerance: Allowed difference between amounts (default: 10)
        
        Returns:
            Dict with:
                - valid: bool - True if amounts match within tolerance
                - coupa_total: float - Total from Coupa
                - excel_total: float - Expected amount from Excel
                - remark: str - "Amount Equals" or "Net Total berbeda antara Coupa (xxx) dan Excel (xxx)"
        """
        self._logger.info(f"Validating invoice amount. Expected: {expected_amount}, Tolerance: ±{tolerance}")
        
        result = {
            "valid": False,
            "coupa_total": None,
            "excel_total": expected_amount,
            "remark": ""
        }
        
        # Get total from Coupa
        coupa_total = self.get_invoice_total()
        result["coupa_total"] = coupa_total
        
        if coupa_total is None:
            result["remark"] = "Could not read Total from Coupa"
            return result
        
        # Compare amounts with tolerance (±10 by default)
        difference = abs(coupa_total - expected_amount)
        
        if difference <= tolerance:
            result["valid"] = True
            result["remark"] = "Amount Equals"
            self._logger.info(f"Amount validation passed: Amount Equals (difference: {difference})")
        else:
            result["valid"] = False
            result["remark"] = f"Net Total berbeda antara Coupa ({coupa_total:,.0f}) dan Excel ({expected_amount:,.0f})"
            self._logger.warning(f"Amount validation failed: {result['remark']}")
        
        return result

    def get_net_total_from_form(self) -> Optional[float]:
        """
        Get the Net Total amount from the invoice form (before save as draft).
        
        Element:
        <div class="totals__netTotal">
            <span class="s-netTotal number totalVal" id="netTotal">
                <span title="IDR">1,132,884,000</span>
            </span>
        </div>
        
        Returns:
            Net Total amount as float, or None if not found
        """
        self._logger.info("Getting Net Total from form...")
        
        if not self._driver:
            raise BrowserError(message="Browser not open")
        
        try:
            # Switch to default content first
            try:
                self._driver.switch_to.default_content()
            except:
                pass
            
            # Switch to enterprise_frame iframe
            self._switch_to_enterprise_frame()
            
            import re
            
            # Try to find Net Total element
            net_total_selectors = [
                (By.CSS_SELECTOR, "#netTotal span[title]"),
                (By.CSS_SELECTOR, ".s-netTotal span[title]"),
                (By.CSS_SELECTOR, "#netTotal"),
                (By.CSS_SELECTOR, ".totals__netTotal .totalVal"),
            ]
            
            total_text = None
            for by, selector in net_total_selectors:
                try:
                    elem = WebDriverWait(self._driver, 5).until(
                        EC.presence_of_element_located((by, selector))
                    )
                    if elem:
                        total_text = elem.text.strip()
                        if total_text:
                            self._logger.debug(f"Found Net Total with selector {selector}: {total_text}")
                            break
                except:
                    continue
            
            if not total_text:
                self._logger.warning("Net Total element not found")
                try:
                    self._driver.switch_to.default_content()
                except:
                    pass
                return None
            
            # Parse: remove all non-digit chars (Indonesian format uses dots as thousand sep)
            cleaned = re.sub(r'[^\d.,]', '', total_text)
            cleaned = cleaned.replace(".", "").replace(",", "")
            
            if not cleaned:
                self._logger.warning(f"Could not parse Net Total: {total_text}")
                try:
                    self._driver.switch_to.default_content()
                except:
                    pass
                return None
            
            net_total = float(cleaned)
            self._logger.info(f"Net Total from form: {net_total:,.0f}")
            
            try:
                self._driver.switch_to.default_content()
            except:
                pass
            
            return net_total
            
        except Exception as e:
            self._logger.error(f"Error getting Net Total: {e}")
            try:
                self._driver.switch_to.default_content()
            except:
                pass
            return None
    
    def click_save_as_draft(self) -> Optional[str]:
        """
        Click the Save as Draft button.
        
        Selector from UiPath:
        <webctrl id='submit_draft' type='submit' tag='BUTTON' class='button' aaname='Save as Draft' />
        
        Returns:
            Draft ID if successful, None otherwise
        """
        self._logger.info("Clicking Save as Draft button...")
        
        if not self._driver:
            raise BrowserError(message="Browser not open")
        
        try:
            # Switch to default content first
            try:
                self._driver.switch_to.default_content()
            except:
                pass
            
            # Switch to enterprise_frame iframe
            self._switch_to_enterprise_frame()
            
            save_draft_selectors = [
                (By.ID, "submit_draft"),
                (By.CSS_SELECTOR, "#submit_draft"),
                (By.CSS_SELECTOR, "button#submit_draft"),
                (By.XPATH, "//button[@id='submit_draft']"),
                (By.XPATH, "//span[text()='Save as Draft']/parent::button"),
                (By.XPATH, "//button[contains(., 'Save as Draft')]"),
                (By.XPATH, "//button[contains(@class, 'button') and contains(., 'Draft')]"),
            ]
            
            save_draft_btn = None
            for by, selector in save_draft_selectors:
                try:
                    save_draft_btn = WebDriverWait(self._driver, 5).until(
                        EC.element_to_be_clickable((by, selector))
                    )
                    if save_draft_btn:
                        self._logger.debug(f"Found Save as Draft button with selector: {selector}")
                        break
                except:
                    continue
            
            if not save_draft_btn:
                self.take_screenshot("save_draft_btn_not_found")
                try:
                    self._driver.switch_to.default_content()
                except:
                    pass
                raise NavigationError(
                    message="Save as Draft button not found",
                    url=self._driver.current_url
                )
            
            # Scroll into view and click
            try:
                self._driver.execute_script("arguments[0].scrollIntoView(true);", save_draft_btn)
                self._sleep(0.5)
            except:
                pass
            
            try:
                save_draft_btn.click()
            except ElementClickInterceptedException:
                self._driver.execute_script("arguments[0].click();", save_draft_btn)
            
            self._logger.info("Clicked Save as Draft button")
            
            # Wait for save to complete
            self._sleep(3)
            
            # Try to extract draft ID from page
            draft_id = self._extract_draft_id()
            
            # Switch back to default content
            try:
                self._driver.switch_to.default_content()
            except:
                pass
            
            return draft_id
            
        except NavigationError:
            raise
        except Exception as e:
            self._logger.error(f"Error clicking Save as Draft: {e}")
            self.take_screenshot("save_draft_click_error")
            try:
                self._driver.switch_to.default_content()
            except:
                pass
            raise NavigationError(
                message="Failed to click Save as Draft button",
                url=self._driver.current_url,
                details=str(e)
            )
    
    def _extract_draft_id(self) -> Optional[str]:
        """Extract draft ID from the page after saving."""
        try:
            # Try to find draft ID in URL or page content
            current_url = self._driver.current_url
            
            # Check if URL contains invoice ID
            if "/invoices/" in current_url:
                # Extract ID from URL like /invoices/12345
                import re
                match = re.search(r'/invoices/(\d+)', current_url)
                if match:
                    return match.group(1)
            
            # Try to find in page content
            draft_id_selectors = [
                (By.CSS_SELECTOR, ".invoice-id"),
                (By.CSS_SELECTOR, ".draft-id"),
                (By.XPATH, "//*[contains(text(), 'Invoice #')]/following-sibling::*"),
                (By.XPATH, "//*[contains(@class, 'invoice-number')]"),
            ]
            
            for by, selector in draft_id_selectors:
                try:
                    element = self._driver.find_element(by, selector)
                    if element and element.text:
                        return element.text.strip()
                except:
                    continue
            
            return None
        except Exception:
            return None

    def submit_invoice(
        self,
        po_number: str,
        invoice_number: str,
        invoice_date: str,
        amount: float,
        additional_data: Optional[Dict[str, Any]] = None
    ) -> InvoiceSubmitResult:
        """
        Submit invoice for a PO.
        
        Args:
            po_number: PO Number
            invoice_number: Invoice Number
            invoice_date: Invoice Date
            amount: Invoice Amount
            additional_data: Additional invoice fields
        
        Returns:
            InvoiceSubmitResult with success status
        
        Implements Requirements 8.1 - 8.6
        """
        self._logger.info(f"Submitting invoice: PO={po_number}, Invoice={invoice_number}")

        if not self._driver:
            raise BrowserError(message="Browser not open")
        
        try:
            # TODO: Update selectors based on actual Coupa invoice form
            # Click on PO to open detail
            po_link = self._wait.until(
                EC.element_to_be_clickable((By.LINK_TEXT, po_number))
            )
            po_link.click()
            
            # Wait for PO detail page
            self._sleep(2)
            
            # Click Create Invoice button
            create_invoice_btn = self._wait.until(
                EC.element_to_be_clickable((By.ID, "create_invoice"))
            )
            create_invoice_btn.click()
            
            # Fill invoice form
            self._fill_invoice_form(invoice_number, invoice_date, amount, additional_data)
            
            # Submit invoice
            submit_btn = self._wait.until(
                EC.element_to_be_clickable((By.ID, "submit_invoice"))
            )
            submit_btn.click()
            
            # Wait for confirmation
            draft_id = self._wait_for_submit_confirmation()
            
            self._logger.info(f"Invoice submitted successfully. Draft ID: {draft_id}")
            return InvoiceSubmitResult(
                success=True,
                draft_id=draft_id
            )
            
        except TimeoutException as e:
            self._logger.error(f"Submit timeout: {e}")
            return InvoiceSubmitResult(
                success=False,
                error_message="Submit timeout"
            )
        except Exception as e:
            self._logger.error(f"Submit error: {e}")
            return InvoiceSubmitResult(
                success=False,
                error_message=str(e)
            )

    def _fill_invoice_form(
        self,
        invoice_number: str,
        invoice_date: str,
        amount: float,
        additional_data: Optional[Dict[str, Any]] = None
    ) -> None:
        """Fill invoice form fields."""
        # TODO: Update selectors based on actual invoice form
        
        # Invoice Number
        inv_num_field = self._driver.find_element(By.ID, "invoice_number")
        inv_num_field.clear()
        inv_num_field.send_keys(invoice_number)
        
        # Invoice Date
        inv_date_field = self._driver.find_element(By.ID, "invoice_date")
        inv_date_field.clear()
        inv_date_field.send_keys(invoice_date)
        
        # Amount
        amount_field = self._driver.find_element(By.ID, "invoice_amount")
        amount_field.clear()
        amount_field.send_keys(str(amount))
        
        # Additional fields
        if additional_data:
            for field_id, value in additional_data.items():
                try:
                    field = self._driver.find_element(By.ID, field_id)
                    field.clear()
                    field.send_keys(str(value))
                except NoSuchElementException:
                    self._logger.warning(f"Field not found: {field_id}")
    
    def _wait_for_submit_confirmation(self) -> Optional[str]:
        """Wait for submit confirmation and extract draft ID."""
        try:
            # TODO: Update selector based on actual confirmation element
            confirmation = self._wait.until(
                EC.presence_of_element_located((By.CLASS_NAME, "success-message"))
            )
            
            # Extract draft ID from confirmation
            draft_id_element = self._driver.find_element(By.ID, "draft_id")
            return draft_id_element.text if draft_id_element else None
            
        except Exception:
            return None

    def cancel_invoice_draft(self, draft_id: str) -> bool:
        """
        Cancel an invoice draft.
        
        Args:
            draft_id: Draft ID to cancel
        
        Returns:
            True if cancelled successfully
        
        Implements Requirements 15.1 - 15.5
        """
        self._logger.info(f"Cancelling invoice draft: {draft_id}")
        
        if not self._driver:
            raise BrowserError(message="Browser not open")
        
        for attempt in range(self.SEARCH_RETRY_COUNT):
            try:
                # Navigate to Invoices page
                invoices_link = self._wait.until(
                    EC.element_to_be_clickable((By.LINK_TEXT, "Invoices"))
                )
                invoices_link.click()
                
                # Search for draft
                search_field = self._wait.until(
                    EC.presence_of_element_located((By.ID, "search_field"))
                )
                search_field.clear()
                search_field.send_keys(draft_id)
                search_field.send_keys(Keys.RETURN)
                
                self._sleep(2)
                
                # Click on draft
                draft_link = self._wait.until(
                    EC.element_to_be_clickable((By.LINK_TEXT, draft_id))
                )
                draft_link.click()
                
                # Click Cancel button
                cancel_btn = self._wait.until(
                    EC.element_to_be_clickable((By.ID, "cancel_invoice"))
                )
                cancel_btn.click()
                
                # Confirm cancellation
                confirm_btn = self._wait.until(
                    EC.element_to_be_clickable((By.ID, "confirm_cancel"))
                )
                confirm_btn.click()
                
                self._logger.info(f"Draft {draft_id} cancelled successfully")
                return True

            except Exception as e:
                self._logger.warning(
                    f"Cancel attempt {attempt + 1} failed: {e}"
                )
                if attempt < self.SEARCH_RETRY_COUNT - 1:
                    time.sleep(self.SEARCH_RETRY_INTERVAL)
        
        self._logger.error(f"Failed to cancel draft {draft_id} after {self.SEARCH_RETRY_COUNT} attempts")
        self._logger.info(f"Manual cleanup required for draft: {draft_id}")
        return False
    
    def take_screenshot(self, name: str, transaction_id: Optional[str] = None) -> Optional[Path]:
        """
        Take screenshot and save to file.
        
        Args:
            name: Screenshot name/description
            transaction_id: Optional transaction ID for filename
        
        Returns:
            Path to screenshot file, or None if failed
        
        Implements Requirement 10.5
        """
        if not self._driver:
            return None
        
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            
            if transaction_id:
                filename = f"{timestamp}_{transaction_id}_{name}.png"
            else:
                filename = f"{timestamp}_{name}.png"
            
            filepath = self._screenshot_dir / filename
            
            self._driver.save_screenshot(str(filepath))
            self._logger.info(f"Screenshot saved: {filepath.name}")
            
            return filepath
            
        except Exception as e:
            self._logger.warning(f"Failed to take screenshot: {e}")
            return None

    def set_invoices_view_all(self) -> bool:
        """
        Set the View dropdown to "All" on the Invoices list page.
        Skips if already set to "All".
        
        Returns:
            True if successful, False otherwise
        """
        self._logger.info("Setting Invoices view to 'All'...")
        
        if not self._driver:
            raise BrowserError(message="Browser not open")
        
        try:
            # Switch to default content first
            try:
                self._driver.switch_to.default_content()
            except:
                pass
            
            # Switch to enterprise_frame iframe
            self._switch_to_enterprise_frame()
            
            # Find the View dropdown - try most common selectors first with short timeout
            view_dropdown = None
            
            # Try ID selectors first (fastest)
            for selector_id in ["view_selector", "view"]:
                try:
                    view_dropdown = self._driver.find_element(By.ID, selector_id)
                    if view_dropdown and view_dropdown.is_displayed():
                        self._logger.debug(f"Found View dropdown with ID: {selector_id}")
                        break
                    view_dropdown = None
                except:
                    continue
            
            # If not found, try CSS selector with short wait
            if not view_dropdown:
                try:
                    view_dropdown = WebDriverWait(self._driver, 3).until(
                        EC.presence_of_element_located((By.CSS_SELECTOR, "select[id*='view']"))
                    )
                except:
                    pass
            
            # Last resort - find any select with "All" option
            if not view_dropdown:
                all_selects = self._driver.find_elements(By.TAG_NAME, "select")
                for select in all_selects:
                    try:
                        options = select.find_elements(By.TAG_NAME, "option")
                        for opt in options:
                            if opt.text.strip().lower() == "all":
                                view_dropdown = select
                                break
                        if view_dropdown:
                            break
                    except:
                        continue
            
            if not view_dropdown:
                self._logger.warning("Could not find View dropdown")
                try:
                    self._driver.switch_to.default_content()
                except:
                    pass
                return False
            
            # Check if already set to "All"
            from selenium.webdriver.support.ui import Select
            select = Select(view_dropdown)
            
            current_value = select.first_selected_option.text.strip().lower()
            if "all" in current_value:
                self._logger.info("View already set to 'All', skipping")
                try:
                    self._driver.switch_to.default_content()
                except:
                    pass
                return True
            
            # Select "All" option
            try:
                select.select_by_visible_text("All")
                self._logger.info("Selected 'All' in View dropdown")
            except:
                for option in select.options:
                    if "all" in option.text.lower():
                        select.select_by_visible_text(option.text)
                        self._logger.info(f"Selected '{option.text}' in View dropdown")
                        break
            
            # Wait for page to refresh
            self._sleep(1)
            
            try:
                self._driver.switch_to.default_content()
            except:
                pass
            
            return True
            
        except Exception as e:
            self._logger.error(f"Error setting View to All: {e}")
            try:
                self._driver.switch_to.default_content()
            except:
                pass
            return False
    
    def search_invoice_in_list(self, invoice_number: str) -> bool:
        """
        Search for an invoice in the Invoices list page.
        
        Args:
            invoice_number: Invoice number to search for
        
        Returns:
            True if search was performed, False otherwise
        """
        self._logger.info(f"Searching for invoice: {invoice_number}")
        
        if not self._driver:
            raise BrowserError(message="Browser not open")
        
        try:
            # Switch to default content first
            try:
                self._driver.switch_to.default_content()
            except:
                pass
            
            # Switch to enterprise_frame iframe
            self._switch_to_enterprise_frame()
            
            # Direct selector - no looping
            search_field = WebDriverWait(self._driver, 5).until(
                EC.presence_of_element_located((By.ID, "sf_invoice_header"))
            )
            
            # Clear and enter invoice number
            search_field.clear()
            search_field.send_keys(invoice_number)
            search_field.send_keys(Keys.RETURN)
            
            self._logger.info(f"Searched for invoice: {invoice_number}")
            
            try:
                self._driver.switch_to.default_content()
            except:
                pass
            
            return True
            
        except Exception as e:
            self._logger.error(f"Error searching for invoice: {e}")
            try:
                self._driver.switch_to.default_content()
            except:
                pass
            return False
    
    def wait_for_invoice_in_list(self, invoice_number: str, max_wait: int = 120, interval: int = 5) -> bool:
        """
        Wait for invoice to appear in the list after save as draft.
        
        Polls every `interval` seconds for up to `max_wait` seconds.
        
        Args:
            invoice_number: Invoice number to find
            max_wait: Maximum wait time in seconds
            interval: Check interval in seconds
        
        Returns:
            True if invoice found, False if timeout
        """
        self._logger.info(f"Waiting for invoice '{invoice_number}' in list (max {max_wait}s)...")
        
        if not self._driver:
            return False
        
        invoice_clean = str(invoice_number).strip().lower()
        elapsed = 0
        
        while elapsed < max_wait:
            time.sleep(interval)
            elapsed += interval
            
            try:
                try:
                    self._driver.switch_to.default_content()
                except:
                    pass
                self._switch_to_enterprise_frame()
                
                table = self._driver.find_element(By.ID, "invoice_header_table_tag")
                rows = table.find_elements(By.CSS_SELECTOR, "tbody tr")
                
                for row in rows:
                    if invoice_clean in row.text.lower():
                        self._logger.info(f"Invoice '{invoice_number}' found in list after {elapsed}s")
                        try:
                            self._driver.switch_to.default_content()
                        except:
                            pass
                        return True
            except:
                pass
            
            self._logger.debug(f"Invoice not found yet ({elapsed}/{max_wait}s)")
        
        self._logger.warning(f"Invoice '{invoice_number}' not found after {max_wait}s")
        try:
            self._driver.switch_to.default_content()
        except:
            pass
        return False
    
    def get_invoice_total_from_list(self, invoice_number: str = None) -> Optional[float]:
        """
        Get the Total amount from the search results matching the invoice number.
        
        This should be called after search_invoice_in_list().
        Finds the row matching the invoice number and extracts the Total column value.
        
        Args:
            invoice_number: Invoice number to match in the results
        
        Returns:
            Total amount as float, or None if not found
        """
        self._logger.info(f"Getting invoice total from search results for: {invoice_number}")
        
        if not self._driver:
            raise BrowserError(message="Browser not open")
        
        try:
            # Switch to default content first
            try:
                self._driver.switch_to.default_content()
            except:
                pass
            
            # Switch to enterprise_frame iframe
            self._switch_to_enterprise_frame()
            
            # Wait for table to be present
            table = WebDriverWait(self._driver, 10).until(
                EC.presence_of_element_located((By.ID, "invoice_header_table_tag"))
            )
            
            if not table:
                self._logger.warning("Results table not found")
                try:
                    self._driver.switch_to.default_content()
                except:
                    pass
                return None
            
            # Find the header row to identify Total column index
            headers = table.find_elements(By.CSS_SELECTOR, "thead th, thead td")
            
            total_col_index = -1
            invoice_col_index = -1
            for idx, header in enumerate(headers):
                header_text = header.text.strip().lower()
                if "total" in header_text:
                    total_col_index = idx
                if "invoice" in header_text and "#" in header_text:
                    invoice_col_index = idx
            
            self._logger.debug(f"Column indices - Invoice#: {invoice_col_index}, Total: {total_col_index}")
            
            # Wait for search results to update, then find matching row
            # Poll every 5 seconds for up to 30 seconds
            target_row = None
            invoice_number_clean = str(invoice_number).strip().lower() if invoice_number else ""
            
            max_wait = 120
            interval = 5
            elapsed = 0
            
            while elapsed < max_wait:
                time.sleep(interval)
                elapsed += interval
                
                # Re-find table and rows
                try:
                    table = self._driver.find_element(By.ID, "invoice_header_table_tag")
                    data_rows = table.find_elements(By.CSS_SELECTOR, "tbody tr")
                    
                    for row in data_rows:
                        row_text = row.text.lower()
                        if invoice_number_clean and invoice_number_clean in row_text:
                            target_row = row
                            break
                except:
                    pass
                
                if target_row:
                    self._logger.info(f"Found invoice '{invoice_number}' in list after {elapsed}s")
                    break
                
                self._logger.debug(f"Invoice not found yet, waiting... ({elapsed}/{max_wait}s)")
            
            if not target_row:
                self._logger.warning(f"Invoice '{invoice_number}' not found in list after {max_wait}s")
                try:
                    self._driver.switch_to.default_content()
                except:
                    pass
                return None
            
            cells = target_row.find_elements(By.CSS_SELECTOR, "td")
            
            if not cells:
                self._logger.warning("No cells found in target row")
                try:
                    self._driver.switch_to.default_content()
                except:
                    pass
                return None
            
            # Get Total value
            total_text = None
            
            if total_col_index >= 0 and total_col_index < len(cells):
                total_text = cells[total_col_index].text.strip()
                self._logger.debug(f"Total from column {total_col_index}: {total_text}")
            else:
                # Try to find Total by looking at cells for currency-like values
                import re
                for cell in reversed(cells):
                    cell_text = cell.text.strip()
                    if re.search(r'[\d.,]+', cell_text):
                        cleaned = re.sub(r'[^\d.,]', '', cell_text)
                        if len(cleaned) > 5:
                            total_text = cell_text
                            self._logger.debug(f"Found potential Total value: {total_text}")
                            break
            
            if not total_text:
                self._logger.warning("Could not find Total value")
                for idx, cell in enumerate(cells):
                    self._logger.debug(f"Cell {idx}: {cell.text.strip()}")
                try:
                    self._driver.switch_to.default_content()
                except:
                    pass
                return None
            
            # Parse the total value
            import re
            cleaned = total_text.strip()
            cleaned = re.sub(r'[^\d.,]', '', cleaned)
            # Indonesian format: dots as thousand separator
            cleaned = cleaned.replace(".", "").replace(",", "")
            
            if not cleaned:
                self._logger.warning(f"Could not parse total value: {total_text}")
                try:
                    self._driver.switch_to.default_content()
                except:
                    pass
                return None
            
            total_value = float(cleaned)
            self._logger.info(f"Invoice total from list: {total_value:,.0f}")
            
            try:
                self._driver.switch_to.default_content()
            except:
                pass
            
            return total_value
            
        except Exception as e:
            self._logger.error(f"Error getting invoice total from list: {e}")
            try:
                self._driver.switch_to.default_content()
            except:
                pass
            return None
    
    def validate_invoice_amount_from_list(self, invoice_number: str, expected_amount: float, tolerance: float = 10.0) -> Dict[str, Any]:
        """
        Validate invoice total from Invoices list against expected amount from Excel.
        
        This is the new validation flow that runs AFTER Save as Draft:
        1. Set View dropdown to "All"
        2. Search for invoice number
        3. Extract Total from first row of results
        4. Compare with expected amount
        
        Args:
            invoice_number: Invoice number to search for
            expected_amount: Sum of Invoice Amount from Excel (filtered by PO and Invoice Number)
            tolerance: Allowed difference between amounts (default: 10)
        
        Returns:
            Dict with:
                - valid: bool - True if amounts match within tolerance
                - coupa_total: float - Total from Coupa list
                - excel_total: float - Expected amount from Excel
                - remark: str - "Amount Equals" or "Net Total berbeda antara Coupa (xxx) dan Excel (xxx)"
        """
        # Convert expected_amount to float if it's a string
        if expected_amount is not None:
            if isinstance(expected_amount, str):
                # Remove thousand separators and convert
                cleaned = expected_amount.replace(",", "").replace(".", "").strip()
                try:
                    expected_amount = float(cleaned) if cleaned else 0.0
                except ValueError:
                    expected_amount = 0.0
            else:
                expected_amount = float(expected_amount)
        else:
            expected_amount = 0.0
        
        self._logger.info(f"Validating invoice amount from list. Invoice: {invoice_number}, Expected: {expected_amount}, Tolerance: ±{tolerance}")
        
        result = {
            "valid": False,
            "coupa_total": None,
            "excel_total": expected_amount,
            "remark": ""
        }
        
        # Step 1: Set View to All
        if not self.set_invoices_view_all():
            self._logger.warning("Could not set View to All, continuing anyway...")
        
        # Step 2: Search for invoice
        if not self.search_invoice_in_list(invoice_number):
            result["remark"] = "Could not search for invoice in list"
            return result
        
        # Step 3: Get Total from first row
        coupa_total = self.get_invoice_total_from_list(invoice_number)
        result["coupa_total"] = coupa_total
        
        if coupa_total is None:
            result["remark"] = "Failed save as draft - Invoice not found in list"
            return result
        
        # Step 4: Compare amounts with tolerance (±10 by default)
        difference = abs(float(coupa_total) - float(expected_amount))
        
        if difference <= tolerance:
            result["valid"] = True
            result["remark"] = "Success save as draft - Amount equals"
            self._logger.info(f"Amount validation passed: Amount equals (difference: {difference})")
        else:
            result["valid"] = False
            result["remark"] = f"Success save as draft - Amount not equals, Coupa ({coupa_total:,.0f}) Excel ({expected_amount:,.0f})"
            self._logger.warning(f"Amount validation failed: {result['remark']}")
        
        return result

    def reload_browser(self) -> None:
        """
        Reload/refresh the browser page.
        
        Used after completing a transaction to prepare for the next one.
        """
        self._logger.info("Reloading browser...")
        
        if not self._driver:
            raise BrowserError(message="Browser not open")
        
        try:
            self._driver.refresh()
            self._sleep(3)
            self._logger.info("Browser reloaded successfully")
        except Exception as e:
            self._logger.error(f"Error reloading browser: {e}")
            raise BrowserError(
                message="Failed to reload browser",
                details=str(e)
            )

    def is_browser_open(self) -> bool:
        """Check if browser is open and responsive."""
        if not self._driver:
            return False
        
        try:
            # Try to get current URL to check if browser is responsive
            _ = self._driver.current_url
            return True
        except Exception:
            return False
    
    def get_current_url(self) -> Optional[str]:
        """Get current page URL."""
        if not self._driver:
            return None
        
        try:
            return self._driver.current_url
        except Exception:
            return None
    
    def __enter__(self):
        """Context manager entry."""
        self.open_browser()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.close_browser()
        return False
