"""
OneDrive Client Module

Handles OneDrive operations via Microsoft Graph API.
Implements Requirement 4: Integrasi OneDrive

Requirements:
- 4.1: Autentikasi menggunakan OAuth 2.0 dengan MSAL library
- 4.2: Download file dari OneDrive dengan timeout 120 detik
- 4.3: Upload file ke OneDrive dengan timeout 120 detik
- 4.4: Retry logic: 3 kali retry dengan interval 5 detik
- 4.5: Generate sharing link untuk file hasil
- 4.6: List files dalam folder
- 4.7: Raise DownloadError jika download gagal setelah retry
- 4.8: Automatic token refresh sebelum expired
- 4.9: Move file ke folder lain dengan rename
"""

import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Dict, Any, BinaryIO
import re

import logging

import msal
import requests

from library.exceptions import AuthenticationError, DownloadError


@dataclass
class DriveItem:
    """
    Data class representing a file or folder in OneDrive.
    
    Attributes:
        id: Unique identifier for the item in OneDrive
        name: File or folder name
        path: Full path in OneDrive (e.g., "/Documents/Input")
        size: File size in bytes (0 for folders)
        is_folder: True if item is a folder
        web_url: URL to view the item in browser
        created_time: When the item was created
        modified_time: When the item was last modified
    """
    id: str
    name: str
    path: str
    size: int
    is_folder: bool
    web_url: str
    created_time: Optional[datetime] = None
    modified_time: Optional[datetime] = None
    
    def __str__(self) -> str:
        item_type = "Folder" if self.is_folder else "File"
        return f"{item_type}({self.name}, path={self.path})"


class OneDriveClient:
    """
    Client untuk operasi OneDrive via Microsoft Graph API.
    
    Menggunakan MSAL (Microsoft Authentication Library) untuk OAuth 2.0
    authentication dan Microsoft Graph API untuk file operations.
    
    Implements Requirements 4.1 - 4.9
    """
    
    # Microsoft Graph API endpoints
    GRAPH_API_BASE = "https://graph.microsoft.com/v1.0"
    AUTHORITY_BASE = "https://login.microsoftonline.com"
    
    # Default scopes for OneDrive access
    DEFAULT_SCOPES = ["https://graph.microsoft.com/.default"]
    
    # Retry configuration
    DEFAULT_RETRY_COUNT = 3
    DEFAULT_RETRY_INTERVAL = 5  # seconds
    
    # Timeout configuration
    DEFAULT_DOWNLOAD_TIMEOUT = 120  # seconds
    DEFAULT_UPLOAD_TIMEOUT = 120  # seconds
    
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        tenant_id: str,
        drive_id: Optional[str] = None,
        user_principal_name: Optional[str] = None,
        logger_module: str = "ONEDRIVE"
    ):
        """
        Initialize OneDriveClient with OAuth credentials.
        
        Args:
            client_id: Azure AD Application (client) ID
            client_secret: Azure AD Application client secret
            tenant_id: Azure AD Tenant ID
            drive_id: Optional specific drive ID. If None, uses user's default drive.
            user_principal_name: User's email/UPN to access their OneDrive 
                                (e.g., "user@company.com"). Required for accessing
                                another user's OneDrive with Application permissions.
            logger_module: Module name for logging
        
        Implements Requirement 4.1: Autentikasi menggunakan OAuth 2.0 dengan MSAL
        """
        self._client_id = client_id
        self._client_secret = client_secret
        self._tenant_id = tenant_id
        self._drive_id = drive_id
        self._user_principal_name = user_principal_name
        self._logger = logging.getLogger(logger_module)
        
        # MSAL confidential client application
        self._app: Optional[msal.ConfidentialClientApplication] = None
        
        # Cached access token
        self._access_token: Optional[str] = None
        self._token_expires_at: Optional[datetime] = None
        
        self._logger.debug("OneDriveClient initialized")
    
    def authenticate(self) -> None:
        """
        Authenticate with Microsoft Graph API using client credentials.
        
        Uses MSAL ConfidentialClientApplication for OAuth 2.0 client credentials flow.
        This is suitable for daemon/service applications without user interaction.
        
        Raises:
            AuthenticationError: If authentication fails
        
        Implements Requirements:
        - 4.1: Autentikasi menggunakan OAuth 2.0 dengan MSAL library
        - 4.8: Automatic token refresh sebelum expired
        """
        self._logger.info("Authenticating with Microsoft Graph API")
        
        try:
            # Create MSAL confidential client application
            authority = f"{self.AUTHORITY_BASE}/{self._tenant_id}"
            
            self._app = msal.ConfidentialClientApplication(
                client_id=self._client_id,
                client_credential=self._client_secret,
                authority=authority
            )
            
            # Acquire token using client credentials
            result = self._app.acquire_token_for_client(scopes=self.DEFAULT_SCOPES)
            
            if "access_token" not in result:
                error_desc = result.get("error_description", "Unknown error")
                self._logger.error(f"Authentication failed: {error_desc}")
                raise AuthenticationError(
                    message="Failed to acquire access token",
                    service="OneDrive",
                    details=error_desc
                )
            
            self._access_token = result["access_token"]
            
            # Calculate token expiration time
            expires_in = result.get("expires_in", 3600)
            self._token_expires_at = datetime.now().timestamp() + expires_in
            
            self._logger.info("Authentication successful")
            self._logger.debug(f"Token expires in {expires_in} seconds")
            
        except AuthenticationError:
            raise
        except Exception as e:
            self._logger.error(f"Authentication error: {e}")
            raise AuthenticationError(
                message="Authentication failed",
                service="OneDrive",
                details=str(e)
            )
    
    def _ensure_authenticated(self) -> None:
        """
        Ensure we have a valid access token, refreshing if necessary.
        
        Implements Requirement 4.8: Automatic token refresh sebelum expired
        """
        if self._access_token is None:
            raise AuthenticationError(
                message="Not authenticated. Call authenticate() first.",
                service="OneDrive"
            )
        
        # Check if token is about to expire (within 5 minutes)
        if self._token_expires_at:
            time_until_expiry = self._token_expires_at - datetime.now().timestamp()
            if time_until_expiry < 300:  # 5 minutes
                self._logger.info("Token expiring soon, refreshing...")
                self.authenticate()
    
    def _get_headers(self) -> Dict[str, str]:
        """Get HTTP headers with authorization token."""
        self._ensure_authenticated()
        return {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": "application/json"
        }
    
    def _get_drive_path(self) -> str:
        """Get the base path for drive operations."""
        if self._drive_id:
            return f"/drives/{self._drive_id}"
        if self._user_principal_name:
            return f"/users/{self._user_principal_name}/drive"
        return "/me/drive"
    
    def _make_request(
        self,
        method: str,
        endpoint: str,
        retry_count: int = DEFAULT_RETRY_COUNT,
        retry_interval: int = DEFAULT_RETRY_INTERVAL,
        timeout: int = 30,
        **kwargs
    ) -> requests.Response:
        """
        Make HTTP request with retry logic.
        
        Args:
            method: HTTP method (GET, POST, PUT, DELETE, PATCH)
            endpoint: API endpoint (relative to GRAPH_API_BASE)
            retry_count: Number of retries on failure
            retry_interval: Seconds between retries
            timeout: Request timeout in seconds
            **kwargs: Additional arguments for requests
        
        Returns:
            Response object
        
        Raises:
            DownloadError: If request fails after all retries
        
        Implements Requirement 4.4: Retry logic 3 kali dengan interval 5 detik
        """
        url = f"{self.GRAPH_API_BASE}{endpoint}"
        headers = self._get_headers()
        
        last_error = None
        
        for attempt in range(retry_count + 1):
            try:
                self._logger.debug(f"Request {method} {endpoint} (attempt {attempt + 1})")
                
                response = requests.request(
                    method=method,
                    url=url,
                    headers=headers,
                    timeout=timeout,
                    **kwargs
                )
                
                # Check for success
                if response.status_code < 400:
                    return response
                
                # Log error response
                self._logger.warning(
                    f"Request failed with status {response.status_code}: "
                    f"{response.text[:200]}"
                )
                last_error = f"HTTP {response.status_code}: {response.text[:200]}"
                
            except requests.exceptions.Timeout as e:
                self._logger.warning(f"Request timeout (attempt {attempt + 1}): {e}")
                last_error = f"Timeout: {e}"
            except requests.exceptions.RequestException as e:
                self._logger.warning(f"Request error (attempt {attempt + 1}): {e}")
                last_error = str(e)
            
            # Wait before retry (except on last attempt)
            if attempt < retry_count:
                self._logger.debug(f"Waiting {retry_interval}s before retry...")
                time.sleep(retry_interval)
        
        # All retries exhausted
        raise DownloadError(
            message=f"Request failed after {retry_count + 1} attempts",
            details=last_error
        )
    
    def _check_file_locked_error(self, error_text: str) -> bool:
        """
        Check if error indicates file is locked/open.
        
        Args:
            error_text: Error message or response text
        
        Returns:
            True if error indicates file is locked
        """
        lock_keywords = [
            "locked", "lock", "editing", "checkout",
            "resourcelocked", "activitylimitreached",
            "cannotsnapshotitem", "423"
        ]
        error_lower = error_text.lower()
        return any(keyword in error_lower for keyword in lock_keywords)
    
    def list_files(self, folder_path: str) -> List[DriveItem]:
        """
        List files and folders in a OneDrive folder.
        
        Args:
            folder_path: Path to folder (e.g., "/Documents/Input" or "root")
        
        Returns:
            List of DriveItem objects
        
        Implements Requirement 4.6: List files dalam folder
        """
        self._logger.info(f"Listing files in: {folder_path}")
        
        # Build endpoint
        drive_path = self._get_drive_path()
        if folder_path == "root" or folder_path == "/":
            endpoint = f"{drive_path}/root/children"
        else:
            # URL encode the path (handle spaces and special characters)
            import urllib.parse
            # Remove leading slash and encode
            clean_path = folder_path.lstrip("/")
            encoded_path = urllib.parse.quote(clean_path)
            endpoint = f"{drive_path}/root:/{encoded_path}:/children"
        
        response = self._make_request("GET", endpoint)
        data = response.json()
        
        items = []
        for item in data.get("value", []):
            drive_item = self._parse_drive_item(item)
            items.append(drive_item)
            self._logger.debug(f"Found: {drive_item}")
        
        self._logger.info(f"Found {len(items)} items in {folder_path}")
        return items
    
    def _parse_drive_item(self, item: Dict[str, Any]) -> DriveItem:
        """Parse Graph API response into DriveItem."""
        # Get parent path
        parent_ref = item.get("parentReference", {})
        parent_path = parent_ref.get("path", "")
        # Remove drive prefix from path
        if ":" in parent_path:
            parent_path = parent_path.split(":", 1)[1]
        
        full_path = f"{parent_path}/{item['name']}" if parent_path else f"/{item['name']}"
        
        # Parse timestamps
        created_time = None
        modified_time = None
        if "createdDateTime" in item:
            try:
                created_time = datetime.fromisoformat(
                    item["createdDateTime"].replace("Z", "+00:00")
                )
            except (ValueError, TypeError):
                pass
        if "lastModifiedDateTime" in item:
            try:
                modified_time = datetime.fromisoformat(
                    item["lastModifiedDateTime"].replace("Z", "+00:00")
                )
            except (ValueError, TypeError):
                pass
        
        return DriveItem(
            id=item["id"],
            name=item["name"],
            path=full_path,
            size=item.get("size", 0),
            is_folder="folder" in item,
            web_url=item.get("webUrl", ""),
            created_time=created_time,
            modified_time=modified_time
        )
    
    def filter_files_by_company(
        self,
        items: List[DriveItem],
        company_code: str,
        extensions: Optional[List[str]] = None
    ) -> List[DriveItem]:
        """
        Filter files by company code and extension.
        
        Args:
            items: List of DriveItem to filter
            company_code: Company code to match (case-insensitive)
            extensions: List of allowed extensions (e.g., [".xlsx", ".xls"])
                       If None, defaults to [".xlsx", ".xls"]
        
        Returns:
            Filtered list of DriveItem
        
        Implements Requirements:
        - 2.2: Filter file berdasarkan company code (case-insensitive)
        - 2.3: Filter hanya file Excel (.xlsx, .xls)
        """
        if extensions is None:
            extensions = [".xlsx", ".xls"]
        
        # Normalize extensions to lowercase
        extensions = [ext.lower() for ext in extensions]
        company_code_lower = company_code.lower()
        
        filtered = []
        for item in items:
            # Skip folders
            if item.is_folder:
                continue
            
            # Check extension
            file_ext = Path(item.name).suffix.lower()
            if file_ext not in extensions:
                continue
            
            # Check company code in filename (case-insensitive)
            if company_code_lower in item.name.lower():
                filtered.append(item)
                self._logger.debug(f"Matched: {item.name} for company {company_code}")
        
        self._logger.info(
            f"Filtered {len(filtered)} files for company {company_code} "
            f"from {len(items)} items"
        )
        return filtered
    
    def generate_timestamped_name(self, filename: str) -> str:
        """
        Generate filename with timestamp suffix.
        
        Format: {name}_{ddMMyyyy_HHmmss}.{ext}
        Example: Invoice_PTI.xlsx -> Invoice_PTI_26052026_143052.xlsx
        
        Args:
            filename: Original filename
        
        Returns:
            Filename with timestamp
        
        Implements Requirement 2.5: Tambahkan timestamp format ddMMyyyy_HHmmss
        """
        path = Path(filename)
        stem = path.stem
        ext = path.suffix
        
        timestamp = datetime.now().strftime("%d%m%Y_%H%M%S")
        new_name = f"{stem}_{timestamp}{ext}"
        
        self._logger.debug(f"Generated timestamped name: {filename} -> {new_name}")
        return new_name
    
    def move_file(
        self,
        item_id: str,
        dest_folder_id: str,
        new_name: Optional[str] = None
    ) -> DriveItem:
        """
        Move file to another folder with optional rename.
        
        Args:
            item_id: ID of the file to move
            dest_folder_id: ID of the destination folder
            new_name: Optional new name for the file
        
        Returns:
            Updated DriveItem
        
        Raises:
            DownloadError: If move fails (e.g., file is open/locked)
        
        Implements Requirement 4.9: Move file ke folder lain dengan rename
        """
        self._logger.info(f"Moving file {item_id} to folder {dest_folder_id}")
        
        drive_path = self._get_drive_path()
        endpoint = f"{drive_path}/items/{item_id}"
        
        # Build request body
        body: Dict[str, Any] = {
            "parentReference": {
                "id": dest_folder_id
            }
        }
        
        if new_name:
            body["name"] = new_name
            self._logger.debug(f"Renaming to: {new_name}")
        
        try:
            response = self._make_request(
                "PATCH",
                endpoint,
                json=body
            )
            
            item = self._parse_drive_item(response.json())
            self._logger.info(f"File moved successfully: {item.name}")
            return item
            
        except DownloadError as e:
            # Check if error is due to file being locked/open
            error_detail = str(e.details) if e.details else ""
            
            if any(keyword in error_detail.lower() for keyword in [
                "locked", "lock", "editing", "checkout", 
                "resourcelocked", "activitylimitreached",
                "cannotsnapshotitem", "itemnotfound"
            ]):
                self._logger.error(f"File is currently open or locked: {item_id}")
                raise DownloadError(
                    message="File sedang terbuka atau dikunci oleh user lain",
                    file_name=new_name or item_id,
                    details="Tutup file terlebih dahulu sebelum memindahkan"
                )
            
            # Check for 423 Locked status
            if "423" in error_detail or "locked" in error_detail.lower():
                self._logger.error(f"File is locked (HTTP 423): {item_id}")
                raise DownloadError(
                    message="File sedang terbuka atau dikunci",
                    file_name=new_name or item_id,
                    details="File sedang dibuka di aplikasi lain. Tutup file dan coba lagi."
                )
            
            # Re-raise original error if not a lock issue
            raise
    
    def get_folder_id(self, folder_path: str) -> str:
        """
        Get folder ID by path.
        
        Args:
            folder_path: Path to folder (e.g., "/Documents/History")
        
        Returns:
            Folder ID
        
        Raises:
            DownloadError: If folder not found
        """
        self._logger.debug(f"Getting folder ID for: {folder_path}")
        
        drive_path = self._get_drive_path()
        
        if folder_path == "root" or folder_path == "/":
            endpoint = f"{drive_path}/root"
        else:
            import urllib.parse
            clean_path = folder_path.lstrip("/")
            encoded_path = urllib.parse.quote(clean_path)
            endpoint = f"{drive_path}/root:/{encoded_path}"
        
        response = self._make_request("GET", endpoint)
        data = response.json()
        
        folder_id = data.get("id")
        if not folder_id:
            raise DownloadError(
                message=f"Folder not found: {folder_path}",
                details="No ID in response"
            )
        
        self._logger.debug(f"Folder ID: {folder_id}")
        return folder_id
    
    def download_file(
        self,
        item_id: str,
        local_path: str,
        timeout: int = DEFAULT_DOWNLOAD_TIMEOUT
    ) -> Path:
        """
        Download file from OneDrive to local path.
        
        Args:
            item_id: ID of the file to download
            local_path: Local path to save the file
            timeout: Download timeout in seconds (default: 120)
        
        Returns:
            Path to downloaded file
        
        Raises:
            DownloadError: If download fails after retries
        
        Implements Requirements:
        - 4.2: Download file dengan timeout 120 detik
        - 4.4: Retry logic 3 kali dengan interval 5 detik
        - 4.7: Raise DownloadError jika gagal setelah retry
        """
        self._logger.info(f"Downloading file {item_id} to {local_path}")
        
        drive_path = self._get_drive_path()
        endpoint = f"{drive_path}/items/{item_id}/content"
        
        # Get download URL (Graph API returns redirect)
        url = f"{self.GRAPH_API_BASE}{endpoint}"
        headers = self._get_headers()
        
        last_error = None
        local_file = Path(local_path)
        
        # Ensure parent directory exists
        local_file.parent.mkdir(parents=True, exist_ok=True)
        
        for attempt in range(self.DEFAULT_RETRY_COUNT + 1):
            try:
                self._logger.debug(f"Download attempt {attempt + 1}")
                
                response = requests.get(
                    url,
                    headers=headers,
                    timeout=timeout,
                    stream=True
                )
                
                if response.status_code == 200:
                    # Write content to file
                    with open(local_file, "wb") as f:
                        for chunk in response.iter_content(chunk_size=8192):
                            f.write(chunk)
                    
                    self._logger.info(f"File downloaded successfully: {local_file}")
                    return local_file
                
                last_error = f"HTTP {response.status_code}: {response.text[:200]}"
                self._logger.warning(f"Download failed: {last_error}")
                
            except requests.exceptions.Timeout as e:
                last_error = f"Timeout after {timeout}s: {e}"
                self._logger.warning(f"Download timeout: {e}")
            except Exception as e:
                last_error = str(e)
                self._logger.warning(f"Download error: {e}")
            
            # Wait before retry
            if attempt < self.DEFAULT_RETRY_COUNT:
                self._logger.debug(f"Waiting {self.DEFAULT_RETRY_INTERVAL}s before retry...")
                time.sleep(self.DEFAULT_RETRY_INTERVAL)
        
        raise DownloadError(
            message="File download failed after retries",
            file_name=str(local_path),
            timeout=timeout,
            details=last_error
        )
    
    def download_file_content(
        self,
        item_id_or_path: str,
        timeout: int = DEFAULT_DOWNLOAD_TIMEOUT
    ) -> bytes:
        """
        Download file content as bytes (for in-memory processing).
        
        Args:
            item_id_or_path: Item ID or file path
            timeout: Download timeout in seconds
        
        Returns:
            File content as bytes
        
        Raises:
            DownloadError: If download fails
        """
        self._logger.info(f"Downloading file content: {item_id_or_path}")
        
        drive_path = self._get_drive_path()
        
        # Determine if input is ID or path
        if "/" in item_id_or_path or item_id_or_path.endswith((".xlsx", ".xls")):
            # It's a path
            import urllib.parse
            clean_path = item_id_or_path.lstrip("/")
            encoded_path = urllib.parse.quote(clean_path)
            endpoint = f"{drive_path}/root:/{encoded_path}:/content"
        else:
            # It's an ID
            endpoint = f"{drive_path}/items/{item_id_or_path}/content"
        
        url = f"{self.GRAPH_API_BASE}{endpoint}"
        headers = self._get_headers()
        
        last_error = None
        
        for attempt in range(self.DEFAULT_RETRY_COUNT + 1):
            try:
                response = requests.get(
                    url,
                    headers=headers,
                    timeout=timeout
                )
                
                if response.status_code == 200:
                    self._logger.info("File content downloaded successfully")
                    return response.content
                
                last_error = f"HTTP {response.status_code}"
                
            except Exception as e:
                last_error = str(e)
            
            if attempt < self.DEFAULT_RETRY_COUNT:
                time.sleep(self.DEFAULT_RETRY_INTERVAL)
        
        raise DownloadError(
            message="Failed to download file content",
            details=last_error
        )
    
    def upload_file(
        self,
        local_path: str,
        dest_folder_path: str,
        dest_filename: Optional[str] = None,
        timeout: int = DEFAULT_UPLOAD_TIMEOUT
    ) -> DriveItem:
        """
        Upload file to OneDrive.
        
        Args:
            local_path: Path to local file
            dest_folder_path: Destination folder path in OneDrive
            dest_filename: Optional destination filename (uses local filename if None)
            timeout: Upload timeout in seconds (default: 120)
        
        Returns:
            DriveItem of uploaded file
        
        Raises:
            DownloadError: If upload fails after retries
        
        Implements Requirements:
        - 4.3: Upload file dengan timeout 120 detik
        - 4.4: Retry logic 3 kali dengan interval 5 detik
        """
        local_file = Path(local_path)
        
        if not local_file.exists():
            raise DownloadError(
                message="Local file not found",
                file_name=str(local_path)
            )
        
        filename = dest_filename or local_file.name
        self._logger.info(f"Uploading {local_file.name} to {dest_folder_path}/{filename}")
        
        drive_path = self._get_drive_path()
        dest_path = f"{dest_folder_path}/{filename}".replace("//", "/")
        
        import urllib.parse
        clean_path = dest_path.lstrip("/")
        encoded_path = urllib.parse.quote(clean_path)
        endpoint = f"{drive_path}/root:/{encoded_path}:/content"
        
        url = f"{self.GRAPH_API_BASE}{endpoint}"
        headers = self._get_headers()
        headers["Content-Type"] = "application/octet-stream"
        
        last_error = None
        
        for attempt in range(self.DEFAULT_RETRY_COUNT + 1):
            try:
                self._logger.debug(f"Upload attempt {attempt + 1}")
                
                with open(local_file, "rb") as f:
                    response = requests.put(
                        url,
                        headers=headers,
                        data=f,
                        timeout=timeout
                    )
                
                if response.status_code in [200, 201]:
                    item = self._parse_drive_item(response.json())
                    self._logger.info(f"File uploaded successfully: {item.name}")
                    return item
                
                last_error = f"HTTP {response.status_code}: {response.text[:200]}"
                self._logger.warning(f"Upload failed: {last_error}")
                
            except requests.exceptions.Timeout as e:
                last_error = f"Timeout after {timeout}s: {e}"
                self._logger.warning(f"Upload timeout: {e}")
            except Exception as e:
                last_error = str(e)
                self._logger.warning(f"Upload error: {e}")
            
            if attempt < self.DEFAULT_RETRY_COUNT:
                self._logger.debug(f"Waiting {self.DEFAULT_RETRY_INTERVAL}s before retry...")
                time.sleep(self.DEFAULT_RETRY_INTERVAL)
        
        raise DownloadError(
            message="File upload failed after retries",
            file_name=str(local_path),
            timeout=timeout,
            details=last_error
        )
    
    def get_sharing_link(
        self,
        item_id: str,
        link_type: str = "view"
    ) -> str:
        """
        Generate sharing link for a file.
        
        Args:
            item_id: ID of the file
            link_type: Type of link ("view" or "edit")
        
        Returns:
            Sharing link URL
        
        Implements Requirement 4.5: Generate sharing link untuk file hasil
        """
        self._logger.info(f"Creating {link_type} sharing link for {item_id}")
        
        drive_path = self._get_drive_path()
        endpoint = f"{drive_path}/items/{item_id}/createLink"
        
        body = {
            "type": link_type,
            "scope": "organization"  # or "anonymous" for external sharing
        }
        
        response = self._make_request("POST", endpoint, json=body)
        data = response.json()
        
        link = data.get("link", {}).get("webUrl", "")
        self._logger.info(f"Sharing link created: {link[:50]}...")
        return link
    
    def create_folder(self, parent_path: str, folder_name: str) -> DriveItem:
        """
        Create a new folder in OneDrive.
        
        Args:
            parent_path: Path to parent folder
            folder_name: Name of new folder
        
        Returns:
            DriveItem of created folder
        """
        self._logger.info(f"Creating folder: {parent_path}/{folder_name}")
        
        drive_path = self._get_drive_path()
        
        if parent_path == "root" or parent_path == "/":
            endpoint = f"{drive_path}/root/children"
        else:
            encoded_path = parent_path.lstrip("/")
            endpoint = f"{drive_path}/root:/{encoded_path}:/children"
        
        body = {
            "name": folder_name,
            "folder": {},
            "@microsoft.graph.conflictBehavior": "fail"
        }
        
        response = self._make_request("POST", endpoint, json=body)
        item = self._parse_drive_item(response.json())
        
        self._logger.info(f"Folder created: {item.name}")
        return item
    
    def file_exists(self, file_path: str) -> bool:
        """
        Check if a file exists in OneDrive.
        
        Args:
            file_path: Path to file
        
        Returns:
            True if file exists, False otherwise
        """
        try:
            drive_path = self._get_drive_path()
            encoded_path = file_path.lstrip("/")
            endpoint = f"{drive_path}/root:/{encoded_path}"
            
            self._make_request("GET", endpoint, retry_count=0)
            return True
        except DownloadError:
            return False
    
    def folder_exists(self, folder_path: str) -> bool:
        """
        Check if a folder exists in OneDrive.
        
        Args:
            folder_path: Path to folder
        
        Returns:
            True if folder exists, False otherwise
        """
        try:
            drive_path = self._get_drive_path()
            import urllib.parse
            clean_path = folder_path.lstrip("/")
            encoded_path = urllib.parse.quote(clean_path)
            endpoint = f"{drive_path}/root:/{encoded_path}"
            
            response = self._make_request("GET", endpoint, retry_count=0)
            data = response.json()
            
            # Check if it's a folder
            return "folder" in data
        except DownloadError:
            return False
    
    def validate_and_download_attachments(
        self,
        invoice_number: str,
        status_site: str,
        onedrive_base_path: str,
        local_base_path: str,
        total_invoice_lines: int = 0
    ) -> Dict[str, Any]:
        """
        Validate attachment folder exists and download ALL required files.
        
        ALL 4 FILES ARE REQUIRED:
        1. File name contains {status_site} AND extension .pdf
        2. File name contains "fp" AND extension .pdf
        3. File name contains "cover invoice" AND extension .pdf
        4. File name contains "format invoice" AND extension .xlsx
        
        File categorization based on total_invoice_lines:
        - If total_invoice_lines > 50:
            - scanned_invoice: cover invoice.pdf
            - attachments: status_site.pdf, fp.pdf, format invoice.xlsx
        - If total_invoice_lines <= 50:
            - scanned_invoice: status_site.pdf
            - attachments: fp.pdf, format invoice.xlsx
            - (cover invoice.pdf is still required but not used in form)
        
        Args:
            invoice_number: Invoice number (used as folder name)
            status_site: Status site string to match in filename
            onedrive_base_path: Base path in OneDrive
            local_base_path: Local base path for downloads
            total_invoice_lines: Number of invoice lines (determines file categorization)
        
        Returns:
            Dict with keys:
                - success: bool - True if ALL 4 files found and downloaded
                - remark: str - Error message listing missing files if failed
                - downloaded_files: List[str] - List of downloaded file paths
                - matched_files: Dict[str, str] - Dict of file type -> filename
                - missing_files: List[str] - List of missing file types
                - folder_path: str - OneDrive folder path checked
                - scanned_invoice: str - Local path to scanned invoice file
                - attachments: List[str] - List of local paths to attachment files
        """
        result = {
            "success": False,
            "remark": "",
            "downloaded_files": [],
            "matched_files": {},  # file_type -> filename
            "missing_files": [],
            "folder_path": "",
            "scanned_invoice": "",  # Single file path for scanned invoice
            "attachments": []  # List of file paths for attachments
        }
        
        # Define required file types
        REQUIRED_FILES = {
            "status_site": f"{status_site}.pdf" if status_site else "status_site.pdf",
            "fp": "fp.pdf",
            "cover_invoice": "cover invoice.pdf",
            "format_invoice": "format invoice.xlsx"
        }
        
        # Build OneDrive folder path
        onedrive_folder = f"{onedrive_base_path.rstrip('/')}/{invoice_number.strip()}"
        result["folder_path"] = onedrive_folder
        
        # Trim status_site
        status_site = status_site.strip() if status_site else ""
        
        self._logger.info(f"Validating attachment folder: {onedrive_folder}")
        self._logger.info(f"Status site: '{status_site}'")
        self._logger.info(f"Total invoice lines: {total_invoice_lines}")
        
        # Step 1: Check if folder exists
        if not self.folder_exists(onedrive_folder):
            result["remark"] = "Folder attachment not found"
            result["missing_files"] = list(REQUIRED_FILES.keys())
            self._logger.warning(f"Attachment folder not found: {onedrive_folder}")
            return result
        
        self._logger.info(f"Attachment folder found: {onedrive_folder}")
        
        # Step 2: List files in folder
        try:
            files = self.list_files(onedrive_folder)
        except DownloadError as e:
            result["remark"] = f"Failed to list files in attachment folder: {e.message}"
            result["missing_files"] = list(REQUIRED_FILES.keys())
            self._logger.error(f"Failed to list files: {e}")
            return result
        
        # Step 3: Find each required file
        status_site_lower = status_site.lower() if status_site else ""
        
        # Track which files are found
        found_files = {
            "status_site": None,
            "fp": None,
            "cover_invoice": None,
            "format_invoice": None
        }
        
        for item in files:
            if item.is_folder:
                continue
            
            file_name_lower = item.name.lower()
            file_ext_lower = Path(item.name).suffix.lower()
            
            # Check each required file type
            if file_ext_lower == ".pdf":
                # 2. FP PDF (check first to avoid matching with status_site)
                if " fp" in file_name_lower or "_fp" in file_name_lower or file_name_lower.endswith(" fp.pdf"):
                    if found_files["fp"] is None:
                        found_files["fp"] = item
                        result["matched_files"]["fp"] = item.name
                        self._logger.debug(f"Found fp file: {item.name}")
                        continue
                
                # 3. Cover Invoice PDF
                if "cover invoice" in file_name_lower or "cover_invoice" in file_name_lower:
                    if found_files["cover_invoice"] is None:
                        found_files["cover_invoice"] = item
                        result["matched_files"]["cover_invoice"] = item.name
                        self._logger.debug(f"Found cover_invoice file: {item.name}")
                        continue
                
                # 1. Status site PDF - match by status_site value OR if it's the remaining PDF
                if status_site_lower and status_site_lower in file_name_lower:
                    if found_files["status_site"] is None:
                        found_files["status_site"] = item
                        result["matched_files"]["status_site"] = item.name
                        self._logger.debug(f"Found status_site file: {item.name}")
                elif not status_site_lower:
                    # If status_site is empty, take any remaining PDF that's not FP or Cover
                    if found_files["status_site"] is None:
                        found_files["status_site"] = item
                        result["matched_files"]["status_site"] = item.name
                        self._logger.debug(f"Found status_site file (fallback): {item.name}")
            
            elif file_ext_lower == ".xlsx":
                # 4. Format Invoice XLSX
                if "format invoice" in file_name_lower and found_files["format_invoice"] is None:
                    found_files["format_invoice"] = item
                    result["matched_files"]["format_invoice"] = item.name
                    self._logger.debug(f"Found format_invoice file: {item.name}")
        
        # Step 4: Check for missing files
        missing = []
        for file_type, item in found_files.items():
            if item is None:
                missing.append(file_type)
                result["missing_files"].append(file_type)
        
        if missing:
            # Build descriptive remark for missing files
            missing_descriptions = []
            for m in missing:
                if m == "status_site":
                    missing_descriptions.append(f"'{status_site}.pdf' (status_site)")
                elif m == "fp":
                    missing_descriptions.append("'fp.pdf'")
                elif m == "cover_invoice":
                    missing_descriptions.append("'cover invoice.pdf'")
                elif m == "format_invoice":
                    missing_descriptions.append("'format invoice.xlsx'")
            
            result["remark"] = f"Missing required files: {', '.join(missing_descriptions)}"
            self._logger.warning(f"Missing files: {missing}")
            return result
        
        self._logger.info("All 4 required files found")
        
        # Step 5: Create local folder if not exists
        local_folder = Path(local_base_path) / invoice_number
        try:
            local_folder.mkdir(parents=True, exist_ok=True)
            self._logger.debug(f"Local folder ready: {local_folder}")
        except Exception as e:
            result["remark"] = f"Failed to create local folder: {e}"
            self._logger.error(f"Failed to create local folder {local_folder}: {e}")
            return result
        
        # Step 6: Download all required files in PARALLEL and track local paths
        from concurrent.futures import ThreadPoolExecutor, as_completed
        
        download_errors = []
        local_paths = {}  # file_type -> local_path
        
        def _download_one(file_type, item):
            """Download a single file. Returns (file_type, local_path, error)."""
            local_file_path = local_folder / item.name
            try:
                self._logger.info(f"Downloading {file_type}: {item.name}")
                downloaded_path = self.download_file(
                    item_id=item.id,
                    local_path=str(local_file_path)
                )
                return (file_type, str(downloaded_path), None)
            except DownloadError as e:
                return (file_type, None, f"Failed to download {file_type} ({item.name}): {e.message}")
        
        # Download all files concurrently (max 4 threads)
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = [
                executor.submit(_download_one, file_type, item)
                for file_type, item in found_files.items()
            ]
            for future in as_completed(futures):
                file_type, local_path, error = future.result()
                if error:
                    download_errors.append(error)
                    self._logger.error(error)
                else:
                    result["downloaded_files"].append(local_path)
                    local_paths[file_type] = local_path
                    self._logger.info(f"Downloaded: {file_type} -> {local_path}")
        
        # Check download results
        if download_errors:
            result["remark"] = f"Download failed: {'; '.join(download_errors)}"
            self._logger.error(f"Download errors: {download_errors}")
            return result
        
        # Step 7: Categorize files based on total_invoice_lines
        # If total_invoice_lines > 50:
        #   - scanned_invoice: cover invoice.pdf
        #   - attachments: status_site.pdf, fp.pdf, format invoice.xlsx
        # If total_invoice_lines <= 50:
        #   - scanned_invoice: status_site.pdf
        #   - attachments: fp.pdf, format invoice.xlsx (cover invoice not used)
        
        if total_invoice_lines > 50:
            result["scanned_invoice"] = local_paths.get("cover_invoice", "")
            result["attachments"] = [
                local_paths.get("status_site", ""),
                local_paths.get("fp", ""),
                local_paths.get("format_invoice", "")
            ]
            self._logger.info(f"Lines > 50: scanned_invoice=cover_invoice, attachments=[status_site, fp, format_invoice]")
        else:
            result["scanned_invoice"] = local_paths.get("status_site", "")
            result["attachments"] = [
                local_paths.get("fp", ""),
                local_paths.get("format_invoice", "")
            ]
            self._logger.info(f"Lines <= 50: scanned_invoice=status_site, attachments=[fp, format_invoice]")
        
        # Remove empty strings from attachments
        result["attachments"] = [a for a in result["attachments"] if a]
        
        # All files found and downloaded successfully
        result["success"] = True
        self._logger.info("All 4 required files downloaded and categorized successfully")
        
        return result
