import io
import unittest
import warnings
import zipfile
from unittest.mock import patch

from doctext import (extract_docx_text, read_document_file, resolve_document,
                     document_fingerprint, quote_span)
from review import handle_review
from test_doctext import DOCX_XML, make_docx


class DocumentHardeningTests(unittest.TestCase):
    def test_preserves_tabs_and_manual_breaks(self):
        xml = DOCX_XML.replace('ДОГОВОР АРЕНДЫ № 12 от 01.02.2026.',
                              'Цена</w:t><w:tab/><w:t>100</w:t><w:br/><w:t>рублей')
        self.assertTrue(extract_docx_text(make_docx(xml)).startswith('Цена\t100\nрублей'))

    def test_rejects_duplicate_archive_members(self):
        buffer = io.BytesIO()
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', UserWarning)
            with zipfile.ZipFile(buffer, 'w') as archive:
                archive.writestr('word/document.xml', DOCX_XML)
                archive.writestr('word/document.xml', DOCX_XML)
        with self.assertRaisesRegex(ValueError, 'повторяющиеся'):
            extract_docx_text(buffer.getvalue())

    def test_rejects_entity_declarations_before_xml_parser_in_both_encodings(self):
        malicious = '<!DOCTYPE x [<!ENTITY x "PRIVATE-ENTITY">]>' + DOCX_XML.split('?>', 1)[1]
        for encoding in ('utf-8', 'utf-16', 'utf-32'):
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, 'w') as archive:
                archive.writestr('word/document.xml', malicious.encode(encoding))
            with self.subTest(encoding=encoding), patch('doctext.ElementTree.fromstring', side_effect=AssertionError('parser called')):
                with self.assertRaisesRegex(ValueError, 'DTD'):
                    extract_docx_text(buffer.getvalue())

    def test_rejects_wrong_xml_root_and_excessive_elements(self):
        with self.assertRaisesRegex(ValueError, 'документ Word'):
            extract_docx_text(make_docx('<root><p>Not Word</p></root>'))
        with patch('doctext.MAX_XML_MARKUP_TOKENS', 2), self.assertRaisesRegex(ValueError, 'много элементов'):
            extract_docx_text(make_docx())

    def test_utf8_bom_and_control_characters(self):
        self.assertEqual(read_document_file('Текст'.encode('utf-8-sig'), 'example.txt'), 'Текст')
        for value in (b'TEXT\x00SECRET', b'TEXT\x01SECRET'):
            with self.assertRaises(ValueError):
                read_document_file(value, 'example.txt')
        with self.assertRaisesRegex(ValueError, 'UTF-8'):
            read_document_file('Договор'.encode('cp1251'), 'example.txt')

    def test_rejects_ambiguous_inputs_for_each_document_side(self):
        for text, encoded in [('document', 'document_base64'), ('document_a', 'document_a_base64')]:
            with self.assertRaises(ValueError):
                resolve_document({text: 'text', encoded: 'invalid'}, text, encoded)
        for value in ({'document': []}, {'document': 'text\x00secret'}, []):
            with self.assertRaises(ValueError):
                resolve_document(value)

    def test_direct_file_reader_enforces_size_limit(self):
        with patch('doctext.MAX_INPUT_FILE_BYTES', 3), self.assertRaises(ValueError):
            read_document_file(b'1234', 'sample.txt')

    def test_review_locations_refer_to_resolved_text(self):
        document = 'Цена\t100 рублей.\nОплата производится после передачи отчёта.'
        response = handle_review({'document': document, 'demo': True})
        self.assertEqual(response['document_sha256'], document_fingerprint(document))
        row = response['risks'][0]
        span = row['document_span']
        self.assertEqual(' '.join(document[span['start']:span['end']].split()), row['quote'])
        self.assertFalse(row['verified'])

    def test_quote_locations_use_first_occurrence_and_no_guessed_span(self):
        self.assertEqual(quote_span('abc abc', 'abc')['start'], 0)
        self.assertEqual(quote_span('one\t two', 'one two')['match_mode'], 'whitespace_normalized')
        self.assertIsNone(quote_span('abc', 'absent'))
