from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from catalog.models import ColorMode, PaperSize, PrintRate
from orders.models import Order
from orders.services import ItemSpec, quote_items


def _spec(rate, pages, copies):
    return ItemSpec(rate_id=rate.pk, file_name="a.pdf", file_url="uploads/1/a.pdf",
                    file_size_mb=Decimal("1.00"), page_count=pages, copies=copies)


class QuoteItemsTests(TestCase):
    def setUp(self):
        self.mono = PrintRate.objects.create(paper_size=PaperSize.A4, color_mode=ColorMode.MONO,
                                             price_per_page=Decimal("2.00"))
        self.color = PrintRate.objects.create(paper_size=PaperSize.A4, color_mode=ColorMode.COLOR,
                                              price_per_page=Decimal("10.00"))

    def test_prices_lines_and_total(self):
        lines, total = quote_items([_spec(self.mono, 10, 2), _spec(self.color, 5, 1)])
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0].spec.rate_id, self.mono.pk)
        self.assertEqual(lines[1].spec.rate_id, self.color.pk)
        self.assertEqual(lines[0].subtotal, Decimal("40.00"))
        self.assertEqual(lines[1].subtotal, Decimal("50.00"))
        self.assertEqual(total, Decimal("90.00"))
        self.assertEqual(lines[0].unit_price, Decimal("2.00"))
        self.assertEqual(lines[1].rate, self.color)

    def test_no_db_writes(self):
        before = Order.objects.count()
        with self.assertNumQueries(1):
            quote_items([_spec(self.mono, 10, 2), _spec(self.color, 5, 1)])
        self.assertEqual(Order.objects.count(), before)

    def test_inactive_rate_raises(self):
        self.color.is_active = False
        self.color.save()
        with self.assertRaises(ValidationError):
            quote_items([_spec(self.color, 1, 1)])

    def test_unknown_rate_raises(self):
        spec = ItemSpec(rate_id=999999, file_name="a.pdf", file_url="uploads/1/a.pdf",
                        file_size_mb=Decimal("1.00"), page_count=1)
        with self.assertRaises(ValidationError):
            quote_items([spec])

    def test_empty_list(self):
        self.assertEqual(quote_items([]), ([], Decimal("0.00")))
