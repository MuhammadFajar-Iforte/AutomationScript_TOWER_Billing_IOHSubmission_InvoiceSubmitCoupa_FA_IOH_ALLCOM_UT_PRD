"""
Custom Exceptions for Coupa Invoice Automation

Defines specific exception types for different error scenarios.
"""


class AutomationException(Exception):
    """Base exception for all automation errors."""
    def __init__(self, message: str, details: str = None):
        self.message = message
        self.details = details
        super().__init__(f"{message}" + (f" - Details: {details}" if details else ""))


class BrowserError(AutomationException):
    """Exception for browser-related errors."""
    pass


class NavigationError(AutomationException):
    """Exception for navigation errors."""
    def __init__(self, message: str, url: str = None, timeout: int = None, details: str = None):
        self.url = url
        self.timeout = timeout
        super().__init__(message, details)


class LoginError(AutomationException):
    """Exception for login errors."""
    def __init__(self, message: str, username: str = None, details: str = None):
        self.username = username
        super().__init__(message, details)


class CaptchaError(AutomationException):
    """Exception for captcha errors."""
    def __init__(self, message: str, attempts: int = 0, details: str = None):
        self.attempts = attempts
        super().__init__(message, details)


class SubmitError(AutomationException):
    """Exception for invoice submission errors."""
    def __init__(self, message: str, invoice_number: str = None, details: str = None):
        self.invoice_number = invoice_number
        super().__init__(message, details)


class AuthenticationError(AutomationException):
    """Exception for authentication errors (OneDrive, etc.)."""
    def __init__(self, message: str, service: str = None, details: str = None):
        self.service = service
        super().__init__(message, details)


class DownloadError(AutomationException):
    """Exception for file download errors."""
    def __init__(self, message: str, file_name: str = None, timeout: int = None, details: str = None):
        self.file_name = file_name
        self.timeout = timeout
        super().__init__(message, details)


class FileValidationError(AutomationException):
    """Exception for file validation errors."""
    pass


class FileSaveError(AutomationException):
    """Exception for file save errors."""
    pass


class MasterDataError(AutomationException):
    """Exception for master data errors."""
    pass
