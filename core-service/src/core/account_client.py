from account_service_client import (
    AccountServiceClient, RemoteResponse, AccountServiceError, AccountNotFoundError,
)

# Синглтон — за тим самим патерном, що proxy_queue_manager раніше.
account_client = AccountServiceClient()

__all__ = [
    "AccountServiceClient", "RemoteResponse", "AccountServiceError",
    "AccountNotFoundError", "account_client",
]