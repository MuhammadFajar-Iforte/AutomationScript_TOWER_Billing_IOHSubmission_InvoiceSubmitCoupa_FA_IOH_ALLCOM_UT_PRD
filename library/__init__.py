# Library utilities for Coupa Invoice Automation

from library.exceptions import (
    AutomationException,
    BrowserError,
    NavigationError,
    LoginError,
    CaptchaError,
    SubmitError,
    AuthenticationError,
    DownloadError,
    FileValidationError,
    FileSaveError,
    MasterDataError
)
from library.web_utils import WebAutomationEngine
from library.onedrive_utils import OneDriveClient, DriveItem
from library.email_utils import EmailClient

__all__ = [
    # Exceptions
    "AutomationException",
    "BrowserError",
    "NavigationError",
    "LoginError",
    "CaptchaError",
    "SubmitError",
    "AuthenticationError",
    "DownloadError",
    "FileValidationError",
    "FileSaveError",
    "MasterDataError",
    # Web automation
    "WebAutomationEngine",
    # OneDrive
    "OneDriveClient",
    "DriveItem",
    # Email
    "EmailClient",
]
