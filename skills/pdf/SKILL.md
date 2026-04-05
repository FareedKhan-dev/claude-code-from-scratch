---
name: pdf
description: Use when working with PDF files — reading, extracting text or tables, merging, splitting, or filling forms. Provides correct library choices and common patterns.
---

# PDF Skill

## When to use this skill
Load when the user asks you to:
- Extract text or tables from a PDF
- Merge or split PDF files
- Fill in a PDF form
- Add watermarks or annotations
- Convert PDF to another format
- Search for content inside a PDF

## Library decision tree

```
Need to extract text from a normal (not scanned) PDF?
    → pdfplumber  (best text + table extraction)

Need to manipulate pages (merge, split, rotate)?
    → pypdf  (formerly PyPDF2)

Need to fill form fields?
    → pypdf  with writer.update_page_form_field_values()

PDF is scanned (images of pages, no selectable text)?
    → pytesseract + pdf2image  (OCR pipeline)

Need to create a PDF from scratch?
    → reportlab  or  fpdf2
```

## Install

```bash
pip install pdfplumber pypdf
# For OCR:
pip install pytesseract pdf2image
# Also requires: tesseract-ocr (system package) and poppler-utils
```

## Extract text — pdfplumber

```python
import pdfplumber

with pdfplumber.open("document.pdf") as pdf:
    for page in pdf.pages:
        text = page.extract_text()
        if text:
            print(text)
```

## Extract tables — pdfplumber

```python
import pdfplumber

with pdfplumber.open("document.pdf") as pdf:
    for page in pdf.pages:
        tables = page.extract_tables()
        for table in tables:
            for row in table:
                print(row)
```

## Merge PDFs — pypdf

```python
from pypdf import PdfWriter

writer = PdfWriter()
for filename in ["part1.pdf", "part2.pdf", "part3.pdf"]:
    writer.append(filename)
with open("merged.pdf", "wb") as f:
    writer.write(f)
```

## Split PDF — pypdf

```python
from pypdf import PdfReader, PdfWriter

reader = PdfReader("document.pdf")
for i, page in enumerate(reader.pages):
    writer = PdfWriter()
    writer.add_page(page)
    with open(f"page_{i+1}.pdf", "wb") as f:
        writer.write(f)
```

## Extract specific pages — pypdf

```python
from pypdf import PdfReader, PdfWriter

reader = PdfReader("document.pdf")
writer = PdfWriter()
for page_num in [0, 2, 4]:          # 0-indexed
    writer.add_page(reader.pages[page_num])
with open("selected.pdf", "wb") as f:
    writer.write(f)
```

## Common issues

**Text comes back garbled or empty**
The PDF may use a non-standard encoding or be scanned. Try OCR:
```python
# pip install pdf2image pytesseract
from pdf2image import convert_from_path
import pytesseract

images = convert_from_path("scanned.pdf")
for img in images:
    print(pytesseract.image_to_string(img))
```

**Tables not extracting correctly**
Try adjusting the extraction strategy:
```python
page.extract_table(table_settings={"vertical_strategy": "lines",
                                    "horizontal_strategy": "lines"})
```

**Large PDF crashes memory**
Process page by page, don't load entire PDF into memory:
```python
with pdfplumber.open("large.pdf") as pdf:
    for page in pdf.pages:
        process(page.extract_text())   # one page at a time
```
