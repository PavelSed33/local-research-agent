"""Optional visual QA fixture. Requires python-docx and Pillow, not server dependencies."""
import argparse
import base64
import io
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from docx_redline import redline_docx


def build(folder):
    from docx import Document
    from docx.shared import Pt, Inches, RGBColor
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from PIL import Image
    folder.mkdir(parents=True, exist_ok=True)
    doc = Document()
    for style in ('Title', 'Normal', 'Header', 'Footer'):
        doc.styles[style].font.color.rgb = RGBColor(0, 0, 0)
        doc.styles[style].font.underline = False
    for element in list(doc.styles.element.iter(qn('w:pBdr'))):
        element.getparent().remove(element)
    doc.styles['Normal'].font.size = Pt(11)
    doc.add_paragraph('Условия оплаты и поставки', 'Title')
    paragraph = doc.add_paragraph()
    paragraph.add_run('Оплата производится в течение ')
    paragraph.add_run('10 ').bold = True
    paragraph.add_run('дней').italic = True
    paragraph.add_run(' после подписания акта.')
    table = doc.add_table(rows=3, cols=2)
    for row, values in zip(table.rows, [('Показатель', 'Условие'), ('Цена', '500 рублей'), ('Срок поставки', '30 дней')]):
        for cell, value in zip(row.cells, values):
            cell.text = value
            borders = OxmlElement('w:tcBorders')
            for edge in ('top', 'left', 'bottom', 'right'):
                item = OxmlElement('w:' + edge)
                for key, val in [('val', 'single'), ('sz', '4'), ('color', 'D9D9D9')]:
                    item.set(qn('w:' + key), val)
                borders.append(item)
            cell._tc.get_or_add_tcPr().append(borders)
    for cell in table.rows[0].cells:
        fill = OxmlElement('w:shd'); fill.set(qn('w:fill'), 'DBEAF4')
        cell._tc.get_or_add_tcPr().append(fill)
        for run in cell.paragraphs[0].runs:
            run.bold = True
    doc.sections[0].header.paragraphs[0].text = 'Договор поставки'
    doc.sections[0].footer.paragraphs[0].text = 'Проверка сохранения структуры'
    doc.add_paragraph('Остальные условия договора сохраняются в исходном документе.')
    stream = io.BytesIO(); Image.new('RGB', (120, 30), (45, 85, 120)).save(stream, format='PNG'); stream.seek(0)
    doc.add_picture(stream, width=Inches(1))
    original = folder / 'original.docx'; doc.save(original)
    result = redline_docx({'filename': 'original.docx', 'file_base64': base64.b64encode(original.read_bytes()).decode(),
        'replacements': [{'quote': '10 дней', 'replacement': '15 рабочих дней'}, {'quote': '500 рублей', 'replacement': '600 рублей'}]})
    (folder / 'redlined.docx').write_bytes(base64.b64decode(result['document_base64']))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('output_dir', type=Path)
    build(parser.parse_args().output_dir)
