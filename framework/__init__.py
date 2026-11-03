"""
Framework Module - RE Framework Implementation

Contains state modules for the Robotic Enterprise Framework pattern:
- InitAllSettings: Initialize configuration and applications
- GetTransactionData: Load and get next transaction
- Process: Process a single transaction
- CloseAllApplications: Cleanup and close applications
"""

from framework import InitAllSettings
from framework import GetTransactionData
from framework import Process
from framework import CloseAllApplications

__all__ = [
    "InitAllSettings",
    "GetTransactionData",
    "Process",
    "CloseAllApplications"
]
