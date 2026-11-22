import unittest
from io import BytesIO
from unittest.mock import patch

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from backend.app.processing import pdf_text


DENSE_PAGE_CONTENT = b"BT /F1 12 Tf (Text) Tj ET\n" + b"0 0 m\n" * 500_000


def pdf_fixture(*page_contents: str | bytes | None, password: str | None = None) -> bytes:
    writer = PdfWriter()
    for content in page_contents:
        page = writer.add_blank_page(width=200, height=100)
        contents = DecodedStreamObject()
        if content is None:
            page[NameObject("/Resources")] = DictionaryObject()
            contents.set_data(
                b"q 200 0 0 100 0 0 cm\nBI /W 2 /H 2 /CS /G /BPC 8 ID \x00\xff\xff\x00\nEI\nQ"
            )
        else:
            font = DictionaryObject(
                {
                    NameObject("/Type"): NameObject("/Font"),
                    NameObject("/Subtype"): NameObject("/Type1"),
                    NameObject("/BaseFont"): NameObject("/Helvetica"),
                }
            )
            page[NameObject("/Resources")] = DictionaryObject(
                {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
            )
            if isinstance(content, str):
                content = f"BT /F1 12 Tf 10 40 Td ({content}) Tj ET".encode("ascii")
            contents.set_data(content)
        page.replace_contents(contents)

    if password is not None:
        writer.encrypt(password, algorithm="RC4-128")
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


class PdfTextTests(unittest.TestCase):
    def test_extracts_two_pages_at_the_limit(self) -> None:
        result = pdf_text.extract_pdf_text(pdf_fixture("First page.", "Second page."), max_pages=2)

        self.assertEqual(
            result.pages,
            (pdf_text.PageText(1, "First page."), pdf_text.PageText(2, "Second page.")),
        )
        self.assertEqual(result.skipped_pages, ())

    def test_keeps_original_page_numbers_in_a_mixed_pdf(self) -> None:
        result = pdf_text.extract_pdf_text(pdf_fixture("Before scan.", None, "After scan."), max_pages=3)

        self.assertEqual(
            result.pages,
            (pdf_text.PageText(1, "Before scan."), pdf_text.PageText(3, "After scan.")),
        )
        self.assertEqual(result.skipped_pages, (2,))

    def test_scanned_pdf_reports_no_selectable_text_and_skipped_pages(self) -> None:
        with self.assertRaises(pdf_text.PdfExtractionError) as raised:
            pdf_text.extract_pdf_text(pdf_fixture(None, None), max_pages=2)

        self.assertEqual(raised.exception.code, "pdf_no_selectable_text")
        self.assertIn("selectable text", str(raised.exception))
        self.assertEqual(raised.exception.skipped_pages, (1, 2))

    def test_unreadable_page_does_not_discard_readable_pages(self) -> None:
        corrupt_page = b"BT /F1 12 Tf (Partial text) Tj ET\nBT (unterminated"
        result = pdf_text.extract_pdf_text(pdf_fixture("Before.", corrupt_page, "After."), max_pages=3)

        self.assertEqual(result.pages, (pdf_text.PageText(1, "Before."), pdf_text.PageText(3, "After.")))
        self.assertEqual(result.skipped_pages, (2,))

    def test_password_protected_pdf_has_a_clear_error(self) -> None:
        with self.assertRaises(pdf_text.PdfExtractionError) as raised:
            pdf_text.extract_pdf_text(pdf_fixture("Protected text.", password="test-only"), max_pages=2)

        self.assertEqual(raised.exception.code, "pdf_password_protected")
        self.assertIn("Remove the password protection", str(raised.exception))

    def test_malformed_pdf_has_a_clear_error(self) -> None:
        with self.assertRaises(pdf_text.PdfExtractionError) as raised:
            pdf_text.extract_pdf_text(b"%PDF-1.7\n1 0 obj\n<<\n", max_pages=2)

        self.assertEqual(raised.exception.code, "invalid_pdf")
        self.assertIn("valid PDF", str(raised.exception))

    def test_over_limit_pdf_is_rejected_before_extracting_text(self) -> None:
        document = pdf_fixture("First page.", "Second page.", "Third page.")
        with self.assertRaises(pdf_text.PdfExtractionError) as raised:
            pdf_text.extract_pdf_text(document, max_pages=2)

        self.assertEqual(raised.exception.code, "pdf_page_limit_exceeded")
        self.assertIn("2 pages", str(raised.exception))

        # A page may be read for counting, but its contents must not be extracted.
        with patch("pypdf.PageObject.extract_text") as extract_text:
            with self.assertRaises(pdf_text.PdfExtractionError):
                pdf_text._read_pages(document, max_pages=2)
            extract_text.assert_not_called()

    def test_timeout_stops_the_parser_process(self) -> None:
        document = pdf_fixture(DENSE_PAGE_CONTENT)
        with patch.object(pdf_text, "PARSE_TIMEOUT_SECONDS", 0.25):
            with self.assertRaises(pdf_text.PdfExtractionError) as raised:
                pdf_text.extract_pdf_text(document, max_pages=1)

        self.assertEqual(raised.exception.code, "pdf_parse_timeout")

    def test_memory_limit_stops_the_parser_process(self) -> None:
        document = pdf_fixture(DENSE_PAGE_CONTENT)
        with patch.object(pdf_text, "PARSER_MEMORY_BYTES", 64 * 1024 * 1024):
            with self.assertRaises(pdf_text.PdfExtractionError) as raised:
                pdf_text.extract_pdf_text(document, max_pages=1)

        self.assertEqual(raised.exception.code, "pdf_memory_limit")
