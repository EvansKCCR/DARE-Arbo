"""Read only the explicitly public local protocol; no Drive access required."""
from html import escape
from pathlib import Path
from zipfile import ZipFile
import xml.etree.ElementTree as ET

PROTOCOL = Path(__file__).resolve().parent / 'DARE-Arbo_framework_development.docx'
NS = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}


def paragraph_text(node):
    prefix = '{' + NS['w'] + '}'
    parts = []
    for child in node.iter():
        if child.tag == prefix + 't':
            parts.append(child.text or '')
        elif child.tag == prefix + 'tab':
            parts.append('\t')
        elif child.tag == prefix + 'br':
            parts.append('\n')
    return ''.join(parts)


def protocol_sections():
    with ZipFile(PROTOCOL) as archive:
        body = ET.fromstring(archive.read('word/document.xml')).find('w:body', NS)
    sections = [{'title': 'Document overview', 'html': ''}]
    for node in body:
        if node.tag.endswith('}p'):
            text = paragraph_text(node).strip()
            if not text:
                continue
            style = node.find('w:pPr/w:pStyle', NS)
            name = style.get('{' + NS['w'] + '}val', '') if style is not None else ''
            if name.lower() == 'heading1':
                sections.append({'title': text, 'html': ''})
            elif name.lower() in ('heading2', 'heading3'):
                sections[-1]['html'] += '<h3>' + escape(text) + '</h3>'
            else:
                sections[-1]['html'] += '<p>' + escape(text).replace('\n', '<br>') + '</p>'
        elif node.tag.endswith('}tbl'):
            table = '<table style="border-collapse:collapse;width:100%">'
            for row in node.findall('w:tr', NS):
                table += '<tr>'
                for cell in row.findall('w:tc', NS):
                    text = '\n'.join(paragraph_text(p) for p in cell.findall('w:p', NS))
                    table += '<td style="border:1px solid #ccc;padding:8px">' + escape(text).replace('\n', '<br>') + '</td>'
                table += '</tr>'
            sections[-1]['html'] += table + '</table>'
    return sections
