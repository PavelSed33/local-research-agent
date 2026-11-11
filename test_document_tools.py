import base64
import copy
import importlib.util
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from doctext import extract_docx_text
from document_tools import capabilities, export_docx, parse_file, redact
from primary_sources import ROOT, load_sources


class DocumentToolsTests(unittest.TestCase):
    def test_utf8_and_docx_parse_without_network(self):
        original = 'Условный договор. Цена: 12500 рублей.'
        txt = parse_file({'filename': 'test.txt', 'file_base64': base64.b64encode(original.encode()).decode()})
        self.assertEqual(txt['document'], original)
        draft = export_docx({'document': original})
        parsed = parse_file({'filename': 'draft.docx', 'file_base64': draft['document_base64']})
        self.assertIn(original, parsed['document'])
        self.assertFalse(parsed['external_requests_attempted'])

    def test_export_applies_only_unique_nonoverlapping_explicit_changes(self):
        original = 'Оплата до приёмки. Срок: 10 дней.'
        result = export_docx({'document': original, 'replacements': [{'quote': 'до приёмки', 'replacement': 'после приёмки'}]})
        text = extract_docx_text(base64.b64decode(result['document_base64']))
        self.assertIn('Оплата после приёмки.', text)
        self.assertIn('ПРОТОКОЛ ПРЕДЛОЖЕННЫХ ИЗМЕНЕНИЙ', text)
        self.assertIn('Исходный текст: до приёмки', text)
        self.assertFalse(result['word_track_changes'])
        for payload in [{'document': 'abc abc', 'replacements': [{'quote': 'abc', 'replacement': 'x'}]},
                        {'document': original, 'replacements': [{'quote': 'invented', 'replacement': 'x'}]},
                        {'document': original, 'replacements': [{'quote': 'до приёмки', 'replacement': 'x'}, {'quote': 'приёмки', 'replacement': 'y'}]},
                        {'document': 'invalid\x00text'}]:
            with self.assertRaises(ValueError):
                export_docx(payload)

    def test_redaction_removes_marked_values_but_never_claims_anonymity(self):
        result = redact({'document': 'Иванов: test@example.org, +7 (999) 123-45-67, 1234 567890.', 'terms': ['Иванов']})
        for private in ('Иванов', 'test@example.org', '123-45-67', '1234 567890'):
            self.assertNotIn(private, result['document'])
        self.assertTrue(result['review_required'])
        self.assertFalse(result['anonymization_verified'])

    @unittest.skipUnless(importlib.util.find_spec('docx'), 'Independent DOCX reader not installed')
    def test_docx_export_is_readable_by_independent_word_document_library(self):
        from docx import Document
        exported = export_docx({'document': 'Условный договор. Цена 12500 рублей.'})
        document = Document(io.BytesIO(base64.b64decode(exported['document_base64'])))
        self.assertIn('Цена 12500 рублей.', '\n'.join(row.text for row in document.paragraphs))

    def test_invalid_payload_and_missing_tools_are_explicit_errors(self):
        for payload in [{'filename': 'file.txt', 'file_base64': '!'},
                        {'filename': 'file.txt', 'file_base64': 'YQ==', 'ocr': 'true'},
                        {'filename': 'file.txt', 'file_base64': 'YQ==', 'verified': True}]:
            with self.assertRaises(ValueError):
                parse_file(payload)
        with patch('document_tools.shutil.which', return_value=None), self.assertRaisesRegex(ValueError, 'Poppler'):
            parse_file({'filename': 'file.pdf', 'file_base64': base64.b64encode(b'%PDF-1.4').decode()})

    def test_missing_russian_ocr_language_is_not_silently_replaced_with_english(self):
        with patch('document_tools.shutil.which', return_value='/synthetic'), patch('document_tools.capabilities', return_value={'ocr_languages': ['eng']}):
            with self.assertRaisesRegex(ValueError, 'tesseract-ocr-rus'):
                parse_file({'filename': 'scan.pdf', 'file_base64': base64.b64encode(b'%PDF-1.4').decode(), 'ocr': True})

    def test_original_source_integrity_and_corruption_detection(self):
        rows = load_sources()
        self.assertEqual(len(rows), 7)
        self.assertEqual(len(rows[0]['pages']), 16)
        self.assertFalse(rows[0]['current_validity_verified'])
        with tempfile.TemporaryDirectory() as directory:
            shutil.copytree(ROOT, directory, dirs_exist_ok=True)
            (Path(directory) / rows[0]['text_file']).write_text('corrupted')
            with self.assertRaises(ValueError):
                load_sources(directory)

    @unittest.skipUnless(shutil.which('pdftotext') and shutil.which('pdfinfo'), 'Poppler not installed')
    def test_real_official_pdf_all_sixteen_pages_extract_and_keep_offsets(self):
        source = load_sources()[0]
        raw = (ROOT / source['original_file']).read_bytes()
        result = parse_file({'filename': 'original.pdf', 'file_base64': base64.b64encode(raw).decode()})
        self.assertEqual(len(result['pages']), 16)
        self.assertEqual(result['document_sha256'], source['text_sha256'])
        self.assertIn('ПЛЕНУМА', result['document'])
        for page in result['pages']:
            self.assertTrue(result['document'][page['start']:page['end']].strip())

    @unittest.skipUnless(all(shutil.which(name) for name in ('pdftotext', 'pdfinfo', 'pdftoppm', 'tesseract')) and importlib.util.find_spec('reportlab') and importlib.util.find_spec('PIL'), 'OCR test dependencies missing')
    def test_real_image_only_pdf_english_ocr(self):
        self._ocr_image('PAYMENT 12500 USD', 'eng')

    @unittest.skipUnless('rus' in capabilities()['ocr_languages'], 'Russian OCR language data not installed')
    def test_real_russian_image_only_pdf_ocr(self):
        if not all(shutil.which(name) for name in ('pdftotext', 'pdfinfo', 'pdftoppm', 'tesseract')) or not importlib.util.find_spec('reportlab') or not importlib.util.find_spec('PIL'):
            self.skipTest('OCR test dependencies missing')
        self._ocr_image('ОПЛАТА 12500 РУБЛЕЙ', 'rus+eng')

    def _ocr_image(self, label, language):
        from PIL import Image, ImageDraw, ImageFont
        from reportlab.pdfgen import canvas
        from reportlab.lib.utils import ImageReader
        image = Image.new('RGB', (1200, 350), 'white')
        font_path = '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
        if not Path(font_path).exists():
            self.skipTest('Test font not installed')
        draw = ImageDraw.Draw(image)
        draw.text((40, 100), label, fill='black', font=ImageFont.truetype(font_path, 65))
        stream = io.BytesIO(); pdf = canvas.Canvas(stream)
        pdf.drawImage(ImageReader(image), 30, 500, width=530, height=155); pdf.save()
        payload = {'filename': 'scan.pdf', 'file_base64': base64.b64encode(stream.getvalue()).decode(), 'ocr': True, 'ocr_language': language}
        result = parse_file(payload)
        self.assertEqual(result['mode'], 'pdf_ocr')
        self.assertIn('12500', result['document'])
        if language == 'rus+eng':
            self.assertIn('РУБЛЕЙ', result['document'].upper())
