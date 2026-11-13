class PaymentSteps:
    """Paying workers and invoicing the program manager."""

    def pay(self, opportunity, workers=None, *, fraction=1.0, payment_date=None):
        """Pay each worker ``fraction`` of what they have accrued, through the payment import.

        ``workers`` defaults to everyone with an accrued balance. Returns the created ``Payment``s.
        """
        raise NotImplementedError

    def invoice(self, opportunity, *, end_date=None):
        """Bill approved work not yet invoiced, up to ``end_date``. Returns the ``PaymentInvoice``."""
        raise NotImplementedError

    def invoice_status(self, invoice, status):
        """Move ``invoice`` to ``status`` as the role allowed to make that transition."""
        raise NotImplementedError

    def invoice_pay(self, invoice):
        """Mark ``invoice`` paid, as ``pm_admin``."""
        raise NotImplementedError
