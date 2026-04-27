"""Admin/auth/payment integration boundary.

The default implementation lives in this repo. A private shared package can be
plugged in later without changing app-domain services.
"""

from .integration import PaymentIntegrationStatus, build_auth_service, payment_integration_status

__all__ = ["PaymentIntegrationStatus", "build_auth_service", "payment_integration_status"]
