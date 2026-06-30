import os
import re
import sys
import hashlib
from pathlib import Path
from utils.helpers import logger
from config.config import CACHE_DIR

# Import pdf and docx libraries
try:
    import pypdf
except ImportError:
    pypdf = None

try:
    import docx
except ImportError:
    docx = None

def clean_text(text: str) -> str:
    """Normalize whitespace and clean up special characters from parsed text."""
    if not text:
        return ""
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n", "\n\n", text)
    return text.strip()

def get_file_hash(file_path: Path) -> str:
    """Compute the SHA-256 hash of a file's binary contents."""
    return hashlib.sha256(file_path.read_bytes()).hexdigest()

def parse_pdf(file_path: Path) -> str:
    """Extract plain text from a PDF file."""
    if not pypdf:
        raise ImportError("The 'pypdf' library is not installed.")
        
    logger.info(f"Parsing PDF file: {file_path.name}")
    reader = pypdf.PdfReader(file_path)
    text_content = []
    
    for i, page in enumerate(reader.pages):
        page_text = page.extract_text()
        if page_text:
            text_content.append(page_text)
            
    if not text_content:
        raise ValueError("PDF file returned no text content. The file might be scanned/image-only.")
        
    return clean_text("\n".join(text_content))

def parse_docx(file_path: Path) -> str:
    """Extract plain text from a DOCX file."""
    if not docx:
        raise ImportError("The 'python-docx' library is not installed.")
        
    logger.info(f"Parsing DOCX file: {file_path.name}")
    doc = docx.Document(str(file_path))
    text_content = []
    
    # Extract from paragraphs
    for para in doc.paragraphs:
        if para.text.strip():
            text_content.append(para.text)
            
    # Extract from tables
    for table in doc.tables:
        for row in table.rows:
            row_text = [cell.text.strip() for cell in row.cells if cell.text.strip()]
            if row_text:
                text_content.append(" | ".join(row_text))
                
    if not text_content:
        raise ValueError("DOCX file contains no readable text paragraphs or tables.")
        
    return clean_text("\n".join(text_content))

def parse_doc_fallback(file_path: Path) -> str:
    """
    Fallback parser for binary legacy DOC files.
    Reads the file as binary and extracts ASCII printable strings.
    """
    logger.warning(f"Using binary ASCII fallback parser for legacy DOC file: {file_path.name}")
    try:
        content = file_path.read_bytes()
        printables = re.findall(rb"[\x20-\x7E\x09\x0A\x0D]{4,}", content)
        text = b" ".join(printables).decode("ascii", errors="ignore")
        text = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F]", "", text)
        text = re.sub(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", "", text)
        cleaned = clean_text(text)
        if len(cleaned) < 50:
            raise ValueError("ASCII extraction yielded too little text.")
        return cleaned
    except Exception as e:
        raise RuntimeError(
            f"Failed to parse legacy DOC file. "
            f"Please convert it to DOCX or PDF format. Fallback error: {str(e)}"
        )

def parse_doc_win32(file_path: Path) -> str:
    """Extract plain text from a legacy .doc file using pywin32 and MS Word."""
    logger.info(f"Attempting to parse legacy DOC file via MS Word (pywin32): {file_path.name}")
    try:
        import win32com.client
    except ImportError:
        logger.warning("pywin32 is not installed or not supported on this platform.")
        return parse_doc_fallback(file_path)
        
    word_app = None
    doc = None
    try:
        import pythoncom
        pythoncom.CoInitialize()
        
        word_app = win32com.client.Dispatch("Word.Application")
        word_app.Visible = False
        
        abs_path = str(file_path.resolve())
        doc = word_app.Documents.Open(abs_path, ReadOnly=True)
        text = doc.Content.Text
        
        doc.Close(False)
        word_app.Quit()
        return clean_text(text)
    except Exception as e:
        logger.error(f"MS Word automation failed: {str(e)}")
        try:
            if doc:
                doc.Close(False)
        except:
            pass
        try:
            if word_app:
                word_app.Quit()
        except:
            pass
        return parse_doc_fallback(file_path)

def parse_doc(file_path: Path) -> str:
    """Routes legacy doc parsing based on OS and availability of win32com."""
    if sys.platform == "win32":
        return parse_doc_win32(file_path)
    else:
        return parse_doc_fallback(file_path)

def extract_text_from_file(file_path: Path) -> str:
    """
    Extract plain text from resume file based on extension.
    Caches parsed text locally using file content hash.
    """
    if not file_path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")
        
    # Calculate file content hash
    file_hash = get_file_hash(file_path)
    cache_file = CACHE_DIR / f"{file_hash}_parsed.txt"
    
    # 1. Check Cache
    if cache_file.exists():
        logger.info(f"Reused parsed text for {file_path.name} (found cached parsed text)")
        try:
            return cache_file.read_text(encoding="utf-8")
        except Exception as e:
            logger.warning(f"Failed to read cached text file {cache_file.name}: {str(e)}")
            
    # 2. Cache Miss - Parse File
    ext = file_path.suffix.lower()
    if ext == ".pdf":
        text = parse_pdf(file_path)
    elif ext == ".docx":
        text = parse_docx(file_path)
    elif ext == ".doc":
        text = parse_doc(file_path)
    else:
        raise ValueError(f"Unsupported file format: '{ext}'. Supported formats: .pdf, .docx, .doc")
        
    # Write parsed text to cache
    try:
        cache_file.write_text(text, encoding="utf-8")
        logger.info(f"Saved parsed text to cache: {cache_file.name}")
    except Exception as e:
        logger.warning(f"Failed to save parsed text cache: {str(e)}")
        
    return text
