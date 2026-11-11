"""Word revision package checks; original formatting claims are bounded and explicit."""
import base64
import copy
import io
from xml.dom import minidom
import unittest
import zipfile

from document_tools import export_docx
from docx_redline import redline_docx, W


def fixture(body=None):
    base=base64.b64decode(export_docx({'document':'Условие оплаты 10 дней.'})['document_base64'])
    with zipfile.ZipFile(io.BytesIO(base)) as source:parts={name:source.read(name) for name in source.namelist()}
    if body is not None:
        parts['word/document.xml']=('<w:document xmlns:w="'+W+'" xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" xmlns:w14="http://schemas.microsoft.com/office/word/2010/wordml" mc:Ignorable="w14"><w:body>'+body+'<w:sectPr><w:pgSz w:w="11906" w:h="16838"/></w:sectPr></w:body></w:document>').encode()
    parts['word/styles.xml']=b'<styles-preserved-byte-for-byte/>'
    parts['word/media/image1.png']=b'ORIGINAL IMAGE BYTES'
    return parts


def pack(parts):
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,'w',zipfile.ZIP_DEFLATED) as archive:
        for name,data in parts.items():archive.writestr(name,data)
    return stream.getvalue()


def request(parts=None,changes=None):
    return {'filename':'original.docx','file_base64':base64.b64encode(pack(parts or fixture())).decode(),
            'replacements':changes or [{'quote':'10 дней','replacement':'15 рабочих дней'}]}


def view(raw,accept):
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:dom=minidom.parseString(archive.read('word/document.xml'))
    for tag in ('ins','del'):
        for node in list(dom.getElementsByTagNameNS(W,tag)):
            parent=node.parentNode
            keep=accept if tag=='ins' else not accept
            if keep:
                for child in list(node.childNodes):parent.insertBefore(child,node)
            parent.removeChild(node)
    values=[]
    for p in dom.getElementsByTagNameNS(W,'p'):
        values.append(''.join(''.join(child.data for child in node.childNodes if child.nodeType==child.TEXT_NODE)
                     for node in p.getElementsByTagName('*') if node.namespaceURI==W and node.localName in ('t','delText')))
    return '\n'.join(values),dom


class OriginalDocxRedlineTests(unittest.TestCase):
    def test_split_runs_table_styles_and_package_parts_are_preserved(self):
        body='<w:p><w:pPr><w:jc w:val="both"/></w:pPr><w:r><w:rPr><w:b/></w:rPr><w:t>Оплата 10 </w:t></w:r><w:r><w:rPr><w:i/></w:rPr><w:t>дней после акта.</w:t></w:r></w:p><w:tbl><w:tblPr/><w:tr><w:tc><w:tcPr/><w:p><w:r><w:t>Цена 500 рублей</w:t></w:r></w:p></w:tc></w:tr></w:tbl>'
        parts=fixture(body);payload=request(parts,[{'quote':'10 дней','replacement':'15 рабочих дней'},{'quote':'500 рублей','replacement':'600 рублей'}])
        result=redline_docx(payload);raw=base64.b64decode(result['document_base64'])
        accepted,dom=view(raw,True);rejected,_=view(raw,False)
        self.assertIn('Оплата 15 рабочих дней после акта.',accepted);self.assertIn('Цена 600 рублей',accepted)
        self.assertIn('Оплата 10 дней после акта.',rejected);self.assertIn('Цена 500 рублей',rejected)
        self.assertEqual(len(dom.getElementsByTagNameNS(W,'tbl')),1)
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            for name,original in parts.items():
                if name!='word/document.xml':self.assertEqual(archive.read(name),original)
            xml=archive.read('word/document.xml')
            self.assertIn(b'xmlns:w14=',xml);self.assertIn(b'mc:Ignorable="w14"',xml)
        self.assertTrue(result['word_track_changes']);self.assertFalse(result['anonymization_verified'])
        self.assertEqual(result['changed_parts'],['word/document.xml'])

    def test_multiple_changes_in_one_run_and_deletion_only(self):
        payload=request(changes=[{'quote':'10','replacement':'20'},{'quote':'дней','replacement':''}])
        raw=base64.b64decode(redline_docx(payload)['document_base64'])
        self.assertIn('Условие оплаты 20 .',view(raw,True)[0])
        self.assertIn('Условие оплаты 10 дней.',view(raw,False)[0])
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            dom=minidom.parseString(archive.read('word/document.xml'))
        ids=[node.getAttributeNS(W,'id') for tag in ('ins','del') for node in dom.getElementsByTagNameNS(W,tag)]
        self.assertEqual(len(ids),len(set(ids)))
        self.assertEqual(len(dom.getElementsByTagNameNS(W,'delText')),2)

    def test_ambiguous_overlapping_and_missing_quotes_are_rejected(self):
        for changes in [[{'quote':'не существует','replacement':'новое'}],
                        [{'quote':'10 дней','replacement':'20 дней'},{'quote':'дней','replacement':'недель'}]]:
            with self.assertRaises(ValueError):redline_docx(request(changes=changes))
        duplicate=fixture('<w:p><w:r><w:t>ааа</w:t></w:r></w:p>')
        with self.assertRaises(ValueError):redline_docx(request(duplicate,[{'quote':'аа','replacement':'б'}]))

    def test_complex_target_paragraph_and_existing_changes_are_rejected(self):
        for body in ['<w:p><w:hyperlink><w:r><w:t>10 дней</w:t></w:r></w:hyperlink></w:p>',
                     '<w:p><w:r><w:t>10 дней</w:t><w:br/></w:r></w:p>',
                     '<w:p><w:ins w:id="1"><w:r><w:t>10 дней</w:t></w:r></w:ins></w:p>']:
            with self.assertRaises(ValueError):redline_docx(request(fixture(body)))

    def test_unsafe_archives_macro_entities_external_templates_and_invalid_main_are_rejected(self):
        for mutate in [lambda p:p.update({'word/vbaProject.bin':b'macro'}),
                       lambda p:p.update({'word/embeddings/data.bin':b'embedded'}),
                       lambda p:p.update({'_xmlsignatures/sig1.xml':b'<signature/>'}),
                       lambda p:p.update({'../outside':b'escape'}),
                       lambda p:p.update({'word/styles.xml':b'<!DOCTYPE x [<!ENTITY x "secret">]><x>&x;</x>'}),
                       lambda p:p.update({'word/_rels/document.xml.rels':b'<Relationships><Relationship Type="attachedTemplate" TargetMode="External" Target="https://example.org/template"/></Relationships>'}),
                       lambda p:p.update({'_rels/.rels':b'<Relationships/>'})]:
            parts=fixture();mutate(parts)
            with self.assertRaises(ValueError):redline_docx(request(parts))

    def test_wrong_types_control_characters_and_noop_fail_closed(self):
        for value in [None,{},[],True]:
            with self.assertRaises(ValueError):redline_docx(value)
        for change in [{'quote':'10 дней','replacement':'\x00bad'}, {'quote':'10\nдней','replacement':'новое'}, {'quote':'10 дней','replacement':'10 дней'}]:
            with self.assertRaises(ValueError):redline_docx(request(changes=[change]))
        for name in ('../file.docx','file.docm'):
            payload=request();payload['filename']=name
            with self.assertRaises(ValueError):redline_docx(payload)
        payload=request();payload['author']='\x00'
        with self.assertRaises(ValueError):redline_docx(payload)

    def test_output_revisions_require_explicit_accept_or_reject_before_another_pass(self):
        result=redline_docx(request())
        payload={'filename':'again.docx','file_base64':result['document_base64'],'replacements':[{'quote':'15 рабочих дней','replacement':'30 дней'}]}
        with self.assertRaises(ValueError):redline_docx(payload)
