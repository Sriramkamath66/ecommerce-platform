import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import aiosmtplib

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Inline HTML templates
# ---------------------------------------------------------------------------

_BASE_STYLE = """
  body { font-family: Arial, sans-serif; background: #f4f4f4; margin: 0; padding: 0; }
  .container { max-width: 600px; margin: 40px auto; background: #ffffff;
               border-radius: 8px; overflow: hidden; box-shadow: 0 2px 8px rgba(0,0,0,.12); }
  .header { background: #1a73e8; color: #ffffff; padding: 24px 32px; }
  .header h1 { margin: 0; font-size: 22px; }
  .body { padding: 32px; color: #333333; line-height: 1.6; }
  .body p { margin: 0 0 16px; }
  .highlight { font-size: 28px; font-weight: bold; color: #1a73e8; }
  .footer { background: #f4f4f4; text-align: center; padding: 16px;
             font-size: 12px; color: #888888; }
"""

_ORDER_CONFIRMED_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><style>{style}</style></head>
<body>
  <div class="container">
    <div class="header"><h1>Order Confirmed</h1></div>
    <div class="body">
      <p>Thank you for your purchase!</p>
      <p>Your order <strong>#{order_id}</strong> has been confirmed.</p>
      <p>Order total: <span class="highlight">${total:.2f}</span></p>
      <p>We will notify you once your order has been dispatched.</p>
    </div>
    <div class="footer">You are receiving this email because you placed an order with us.</div>
  </div>
</body>
</html>"""

_PAYMENT_RECEIPT_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><style>{style}</style></head>
<body>
  <div class="container">
    <div class="header"><h1>Payment Receipt</h1></div>
    <div class="body">
      <p>Your payment has been successfully processed.</p>
      <p>Order reference: <strong>#{order_id}</strong></p>
      <p>Amount charged: <span class="highlight">${amount:.2f}</span></p>
      <p>Please keep this email as your receipt.</p>
    </div>
    <div class="footer">You are receiving this because a payment was made on your account.</div>
  </div>
</body>
</html>"""

_PAYMENT_FAILED_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><style>{style}</style></head>
<body>
  <div class="container">
    <div class="header" style="background:#e53935;"><h1>Payment Failed</h1></div>
    <div class="body">
      <p>Unfortunately, we were unable to process your payment for order <strong>#{order_id}</strong>.</p>
      <p>Reason: <strong>{reason}</strong></p>
      <p>Please update your payment method and try again, or contact our support team for assistance.</p>
    </div>
    <div class="footer">You are receiving this because a payment attempt was made on your account.</div>
  </div>
</body>
</html>"""

_ORDER_STATUS_CHANGED_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><style>{style}</style></head>
<body>
  <div class="container">
    <div class="header"><h1>Order Status Update</h1></div>
    <div class="body">
      <p>Your order <strong>#{order_id}</strong> has been updated.</p>
      <p>Previous status: <strong>{old_status}</strong></p>
      <p>New status: <span class="highlight">{new_status}</span></p>
      <p>Thank you for shopping with us!</p>
    </div>
    <div class="footer">You are receiving this because you have an active order with us.</div>
  </div>
</body>
</html>"""

_LOW_STOCK_ALERT_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><style>{style}</style></head>
<body>
  <div class="container">
    <div class="header" style="background:#f57c00;"><h1>Low Stock Alert</h1></div>
    <div class="body">
      <p>This is an automated inventory alert.</p>
      <p>Product ID: <strong>{product_id}</strong></p>
      <p>Remaining stock: <span class="highlight">{quantity}</span> unit(s)</p>
      <p>Please restock this item to avoid stockouts.</p>
    </div>
    <div class="footer">This alert is sent to administrators only.</div>
  </div>
</body>
</html>"""


class EmailService:
    """Async email service backed by aiosmtplib."""

    def __init__(self, settings) -> None:
        self.settings = settings

    async def send(self, to: str, subject: str, html_body: str) -> bool:
        """Build a MIME message and deliver it via SMTP.

        Returns True on success, False on any error.
        """
        message = MIMEMultipart("alternative")
        message["Subject"] = subject
        message["From"] = self.settings.SMTP_FROM
        message["To"] = to
        message.attach(MIMEText(html_body, "html", "utf-8"))

        use_tls = self.settings.SMTP_PORT not in (25, 1025, 587)
        start_tls = self.settings.SMTP_PORT == 587
        username = self.settings.SMTP_USER or None
        password = self.settings.SMTP_PASSWORD or None

        try:
            await aiosmtplib.send(
                message,
                hostname=self.settings.SMTP_HOST,
                port=self.settings.SMTP_PORT,
                use_tls=use_tls,
                start_tls=start_tls,
                username=username,
                password=password,
            )
            logger.info("Email sent to %s — subject: %s", to, subject)
            return True
        except Exception as exc:
            logger.error("Failed to send email to %s: %s", to, exc)
            return False

    async def send_order_confirmed(
        self, to: str, order_id: str, total: float
    ) -> bool:
        html = _ORDER_CONFIRMED_TEMPLATE.format(
            style=_BASE_STYLE, order_id=order_id, total=total
        )
        return await self.send(to, f"Order #{order_id} Confirmed", html)

    async def send_payment_receipt(
        self, to: str, order_id: str, amount: float
    ) -> bool:
        html = _PAYMENT_RECEIPT_TEMPLATE.format(
            style=_BASE_STYLE, order_id=order_id, amount=amount
        )
        return await self.send(to, f"Payment Receipt for Order #{order_id}", html)

    async def send_payment_failed(
        self, to: str, order_id: str, reason: str
    ) -> bool:
        html = _PAYMENT_FAILED_TEMPLATE.format(
            style=_BASE_STYLE, order_id=order_id, reason=reason
        )
        return await self.send(to, f"Payment Failed for Order #{order_id}", html)

    async def send_order_status_changed(
        self, to: str, order_id: str, old_status: str, new_status: str
    ) -> bool:
        html = _ORDER_STATUS_CHANGED_TEMPLATE.format(
            style=_BASE_STYLE,
            order_id=order_id,
            old_status=old_status.capitalize(),
            new_status=new_status.capitalize(),
        )
        return await self.send(
            to,
            f"Your Order #{order_id} Is Now {new_status.capitalize()}",
            html,
        )

    async def send_low_stock_alert(
        self, to: str, product_id: str, quantity: int
    ) -> bool:
        html = _LOW_STOCK_ALERT_TEMPLATE.format(
            style=_BASE_STYLE, product_id=product_id, quantity=quantity
        )
        return await self.send(to, f"Low Stock Alert: Product {product_id}", html)
