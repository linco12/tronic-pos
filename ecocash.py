"""
EcoCash Merchant Push Payment Integration (Econet Zimbabwe)

Flow:
  1. Cashier enters customer phone number + amount → hits "Pay via EcoCash"
  2. This module sends a push payment request to EcoCash API
  3. Customer receives a USSD prompt on their phone: "Do you want to pay $X to [Merchant]? Enter PIN:"
  4. Customer enters their EcoCash PIN
  5. EcoCash confirms the transaction
  6. We poll the status endpoint until confirmed (or timeout)

Test mode: simulates the flow without hitting the real API.
Production: set mode='production' and supply real merchant credentials from Econet merchant portal.
"""

import uuid
import requests
from datetime import datetime


class EcoCashAPI:
    PRODUCTION_URL = "https://api.ecocash.co.zw/merchant/v1"

    def __init__(self, merchant_code="", merchant_pin="", mode="test"):
        self.merchant_code = merchant_code
        self.merchant_pin = merchant_pin
        self.mode = mode  # 'test' or 'production'

    def initiate_push_payment(self, customer_msisdn, amount, reference, description="Purchase"):
        """Send a push payment prompt to the customer's phone."""
        msisdn = self._normalize_msisdn(customer_msisdn)
        if not self._valid_msisdn(msisdn):
            return {
                "success": False,
                "status": "failed",
                "message": "Invalid EcoCash number. Use format 077XXXXXXX or 078XXXXXXX",
            }
        if self.mode == "production" and self.merchant_code and self.merchant_pin:
            return self._real_initiate(msisdn, amount, reference, description)
        return self._mock_initiate(msisdn, amount, reference)

    def check_payment_status(self, merchant_reference):
        """Poll EcoCash for payment confirmation."""
        if self.mode == "production" and self.merchant_code:
            return self._real_status(merchant_reference)
        return self._mock_status(merchant_reference)

    # ── helpers ──────────────────────────────────────────────────────────────

    def _normalize_msisdn(self, number):
        n = str(number).strip().replace(" ", "").replace("-", "")
        if n.startswith("0"):
            n = "263" + n[1:]
        elif not n.startswith("263"):
            n = "263" + n
        return n

    def _valid_msisdn(self, msisdn):
        """Accept Econet prefixes: 2637X, 2637X"""
        if len(msisdn) != 12:
            return False
        prefix = msisdn[:5]
        return prefix in ("26377", "26378")

    # ── test/mock ─────────────────────────────────────────────────────────────

    def _mock_initiate(self, msisdn, amount, reference):
        display = "0" + msisdn[3:]  # back to local format for display
        return {
            "success": True,
            "status": "pending",
            "message": (
                f"[TEST MODE] Payment request of ${amount:.2f} sent to {display}. "
                "In production the customer will receive a USSD prompt to enter their EcoCash PIN."
            ),
            "transaction_id": f"ECO-{reference}",
            "merchant_reference": reference,
            "amount": amount,
            "customer_msisdn": msisdn,
            "initiated_at": datetime.now().isoformat(),
        }

    def _mock_status(self, merchant_reference):
        """In test mode always returns completed after first poll."""
        return {
            "success": True,
            "status": "completed",
            "merchant_reference": merchant_reference,
            "ecocash_reference": "TRN" + uuid.uuid4().hex[:8].upper(),
            "message": "[TEST MODE] Payment completed successfully",
        }

    # ── production ────────────────────────────────────────────────────────────

    def _real_initiate(self, msisdn, amount, reference, description):
        try:
            payload = {
                "merchantCode": self.merchant_code,
                "merchantPin": self.merchant_pin,
                "merchantNumber": self.merchant_code,
                "amount": f"{amount:.2f}",
                "customerMsisdn": msisdn,
                "currencyCode": "USD",
                "reference": reference,
                "description": description,
                "terminalId": "POS001",
            }
            resp = requests.post(
                f"{self.PRODUCTION_URL}/payments/initiate",
                json=payload,
                timeout=30,
            )
            if resp.status_code == 200:
                data = resp.json()
                return {
                    "success": True,
                    "status": "pending",
                    "message": "Payment request sent. Customer will receive USSD prompt.",
                    "transaction_id": data.get("transactionId", reference),
                    "merchant_reference": reference,
                }
            return {
                "success": False,
                "status": "failed",
                "message": f"EcoCash API error ({resp.status_code}): {resp.text[:200]}",
            }
        except requests.exceptions.RequestException as e:
            return {"success": False, "status": "error", "message": f"Connection error: {e}"}

    def _real_status(self, merchant_reference):
        try:
            params = {
                "merchantCode": self.merchant_code,
                "merchantPin": self.merchant_pin,
                "merchantReference": merchant_reference,
            }
            resp = requests.get(
                f"{self.PRODUCTION_URL}/payments/status/{merchant_reference}",
                params=params,
                timeout=30,
            )
            if resp.status_code == 200:
                data = resp.json()
                raw = data.get("status", "unknown").lower()
                status = "completed" if raw in ("success", "completed") else raw
                return {
                    "success": True,
                    "status": status,
                    "ecocash_reference": data.get("ecocashReference", ""),
                    "message": data.get("message", ""),
                }
            return {"success": False, "status": "error", "message": resp.text[:200]}
        except Exception as e:
            return {"success": False, "status": "error", "message": str(e)}
