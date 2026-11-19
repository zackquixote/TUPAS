"""Extract readable text from uploaded handouts.

PDF uses pypdf; PowerPoint and Word are OOXML zip files, so their text can be
read with the standard library (no extra dependency). This lets the Study
Buddy save and reuse a slide deck instead of rejecting it.
"""

import re
import zipfile
from html import unescape

from ..ai import AIError, extract_text_from_pdf_stream

ALLOWED_EXTENSIONS = {'pdf', 'txt', 'md', 'pptx', 'docx'}


def file_extension(upload):
    return (upload.name or '').rsplit('.', 1)[-1].lower()


def _clean_xml_text(fragment):
    text = unescape(re.sub(r'<[^>]+>', ' ', fragment))
    return re.sub(r'\s+', ' ', text).strip()


def extract_text_from_pptx_stream(upload):
    """Return (slide text, slide count) from a .pptx file."""
    try:
        upload.seek(0)
        with zipfile.ZipFile(upload) as archive:
            slide_names = [
                name for name in archive.namelist()
                if re.fullmatch(r'ppt/slides/slide\d+\.xml', name)
            ]
            slide_names.sort(
                key=lambda name: int(re.search(r'slide(\d+)\.xml', name).group(1))
            )

            sections = []
            for index, name in enumerate(slide_names, start=1):
                xml = archive.read(name).decode('utf-8', errors='replace')
                texts = [
                    _clean_xml_text(fragment)
                    for fragment in re.findall(r'<a:t[^>]*>(.*?)</a:t>', xml, flags=re.S)
                ]
                slide_text = ' '.join(text for text in texts if text)
                if slide_text:
                    sections.append(f'--- [Slide {index}] ---\n{slide_text}')

            return '\n\n'.join(sections).strip(), len(slide_names)
    except (zipfile.BadZipFile, KeyError, OSError, ValueError) as exc:
        raise AIError(f'Could not read the PowerPoint file: {exc}')


def extract_text_from_docx_stream(upload):
    """Return (document text, 1) from a .docx file."""
    try:
        upload.seek(0)
        with zipfile.ZipFile(upload) as archive:
            xml = archive.read('word/document.xml').decode('utf-8', errors='replace')
    except (zipfile.BadZipFile, KeyError, OSError) as exc:
        raise AIError(f'Could not read the Word file: {exc}')

    paragraphs = []
    for block in re.split(r'</w:p>', xml):
        texts = [
            _clean_xml_text(fragment)
            for fragment in re.findall(r'<w:t[^>]*>(.*?)</w:t>', block, flags=re.S)
        ]
        line = ' '.join(text for text in texts if text).strip()
        if line:
            paragraphs.append(line)

    return '\n'.join(paragraphs).strip(), 1


def extract_uploaded_text(upload):
    """Extract (text, page_or_slide_count) from any supported handout upload."""
    extension = file_extension(upload)

    if extension == 'pdf':
        return extract_text_from_pdf_stream(upload)
    if extension == 'pptx':
        return extract_text_from_pptx_stream(upload)
    if extension == 'docx':
        return extract_text_from_docx_stream(upload)
    if extension in ('txt', 'md'):
        upload.seek(0)
        text = upload.read().decode('utf-8', errors='replace').strip()
        return text, 1

    raise AIError('Upload a PDF, PowerPoint (.pptx), Word (.docx), or text file.')
