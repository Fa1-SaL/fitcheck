import re
import io
import pandas as pd
from typing import List, Tuple, Dict
from utils.helpers import logger
from models.candidate import Candidate

def check_is_header(first_row: List[str]) -> bool:
    """
    Check if the first row is a header row.
    Returns True if it's a header, False if it's a data row.
    """
    import re
    
    header_keywords = {"name", "email", "phone", "contact", "mobile", "resume", "cv", "link", "url", "timestamp", "date", "status", "score"}
    
    has_email = False
    has_url = False
    has_date = False
    has_phone = False
    
    for val in first_row:
        val_str = str(val).strip()
        val_lower = val_str.lower()
        if not val_str:
            continue
            
        # Email check: typical pattern with @ and .
        if re.search(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+", val_str):
            has_email = True
        
        # URL check: starts with http, or contains drive.google.com, docs.google.com, or linkedin.com/in/
        if val_lower.startswith("http") or "drive.google.com" in val_lower or "docs.google.com" in val_lower or "linkedin.com/in/" in val_lower:
            has_url = True
            
        # Date check: matches a date-like pattern (e.g. 6/22/2026 or 2026-06-25)
        if re.search(r"\d{1,4}[-/]\d{1,2}[-/]\d{1,4}", val_str):
            has_date = True
            
        # Phone check: matches a phone-like pattern (longer than 7 digits, mostly numeric/spaces/dashes/parentheses)
        digits_only = re.sub(r"\D", "", val_str)
        if len(digits_only) >= 7 and re.match(r"^\+?[0-9\s\-()]{7,25}$", val_str):
            has_phone = True
            
    # If it contains clear data elements, it's NOT a header
    if has_email or has_url or has_date or has_phone:
        return False
        
    # Check if it contains header keywords
    for val in first_row:
        val_lower = str(val).strip().lower()
        if any(kw in val_lower for kw in header_keywords):
            return True
            
    return False

def parse_tsv_data(tsv_text: str) -> pd.DataFrame:
    """
    Parse pasted TSV data into a Pandas DataFrame.
    Automatically detects if headers are omitted, and dynamically assigns correct column headers.
    """
    if not tsv_text or not tsv_text.strip():
        raise ValueError("Clipboard data is empty. Please copy rows from your spreadsheet and paste them here.")
        
    logger.info("Parsing clipboard TSV data...")
    try:
        # First read the TSV with header=None to inspect the first row
        data_stream = io.StringIO(tsv_text.strip())
        df_raw = pd.read_csv(data_stream, sep="\t", keep_default_na=False, dtype=str, header=None)
        
        if df_raw.empty:
            raise ValueError("No rows found in the pasted data.")
            
        # Inspect first row to see if it's a header or a data row
        first_row = df_raw.iloc[0].tolist()
        
        is_header = check_is_header(first_row)
                
        if is_header:
            # Use first row as headers
            df = df_raw.copy()
            df.columns = df.iloc[0]
            df = df.iloc[1:].reset_index(drop=True)
            logger.info("TSV parsed with headers from first row.")
        else:
            # No headers, classify columns dynamically based on the first row's contents
            assigned_cols = []
            has_name = False
            for col_idx in range(len(df_raw.columns)):
                # Find the first non-empty value in this column to classify it
                val = ""
                for r_idx in range(len(df_raw)):
                    cell_val = str(df_raw.iloc[r_idx, col_idx]).strip()
                    if cell_val:
                        val = cell_val
                        break
                        
                val_lower = val.lower()
                
                # Check URL
                if val_lower.startswith("http") or "drive.google" in val_lower or "docs.google" in val_lower:
                    assigned_cols.append("Resume")
                # Check Email
                elif "@" in val_lower:
                    assigned_cols.append("Email")
                # Check Phone
                elif re.match(r"^\+?[0-9\s\-()]{7,20}$", val):
                    assigned_cols.append("Phone number")
                # Check Date/Time (Timestamp)
                elif re.search(r"\d{1,4}[-/]\d{1,2}[-/]\d{1,4}", val) or ("/" in val and ":" in val):
                    assigned_cols.append("Timestamp")
                # Check Name (first generic non-numeric text column)
                elif not has_name and val and not val.replace(" ", "").isdigit():
                    assigned_cols.append("Full Name")
                    has_name = True
                else:
                    assigned_cols.append(f"Col {col_idx}")
                    
            df = df_raw.copy()
            df.columns = assigned_cols
            logger.info(f"TSV parsed without headers. Classified columns dynamically: {assigned_cols}")
            
        logger.info(f"Successfully parsed TSV: {len(df.columns)} columns, {len(df)} rows.")
        return df
    except Exception as e:
        logger.error(f"Failed to parse TSV: {str(e)}")
        raise ValueError(f"Failed to parse pasted table. Error: {str(e)}")

def validate_tsv_columns(df: pd.DataFrame) -> Tuple[str, str]:
    """
    Validate TSV columns to identify the candidate Name and Resume link columns.
    Returns (name_column, resume_column) names.
    """
    cols = list(df.columns)
    
    # Identify Name column (skipping unnamed columns)
    name_col = None
    name_patterns = [r"name", r"candidate", r"applicant", r"full\s*name"]
    for pattern in name_patterns:
        for col in cols:
            if "unnamed" in str(col).lower():
                continue
            if re.search(pattern, col, re.IGNORECASE):
                name_col = col
                break
        if name_col:
            break
            
    # Fallback to first column if Name not found
    if not name_col and cols:
        for col in cols:
            if "unnamed" not in str(col).lower():
                name_col = col
                break
        if not name_col:
            name_col = cols[0]
        logger.warning(f"Could not automatically detect 'Name' column. Falling back to column: '{name_col}'")
        
    # Identify Resume link column (skipping unnamed columns)
    resume_col = None
    resume_patterns = [r"resume", r"cv", r"drive", r"document", r"link", r"url"]
    for pattern in resume_patterns:
        for col in cols:
            col_lower = str(col).lower()
            if "unnamed" in col_lower:
                continue
            # Skip LinkedIn profile, email, or phone columns for resume URL detection
            if "linkedin" in col_lower or "linked" in col_lower or "email" in col_lower or "phone" in col_lower:
                continue
            if re.search(pattern, col, re.IGNORECASE):
                resume_col = col
                break
        if resume_col:
            break
            
    if not resume_col:
        raise ValueError(
            f"Could not find a 'Resume' link column in the pasted columns. "
            f"Pasted columns: {cols}. Please make sure you copy a column named 'Resume', 'CV', 'Link', etc."
        )
        
    logger.info(f"Verified columns - Name: '{name_col}', Resume Link: '{resume_col}'")
    return name_col, resume_col

def generate_output_tsv(df: pd.DataFrame, candidates: List[Candidate]) -> str:
    """
    Generates the output TSV containing ONLY 'AI Score' and 'Status' columns (no headers, no other columns),
    in the original row order, with the AI Score rounded to the nearest integer.
    """
    logger.info("Generating output TSV containing only rounded AI Score and Status (no headers)...")
    
    lines = []
    for idx in range(len(df)):
        # Candidate ID is 1-indexed string representation of row index + 1
        cand_id = str(idx + 1)
        matched_cand = next((c for c in candidates if c.id == cand_id), None)
        
        score_str = ""
        status_str = "Not Processed"
        
        if matched_cand:
            if matched_cand.match_score is not None:
                # Round to nearest integer (e.g. 76.5 -> 77)
                score_str = str(int(matched_cand.match_score + 0.5))
            status_str = matched_cand.status
            
        lines.append(f"{score_str}\t{status_str}")
        
    return "\n".join(lines) + "\n"

def generate_shortlisted_csv(df: pd.DataFrame, candidates: List[Candidate]) -> str:
    """
    Generates a CSV string containing only Shortlisted candidates with columns:
    First Name, Last Name, Email Address, Phone Number.

    Uses fixed positional column indices matching the Google Form sheet layout:
      A (index 0) = Timestamp
      B (index 1) = Full Name
      C (index 2) = Email Address
      D (index 3) = Phone Number
      E (index 4) = Resume link
      F (index 5) = LinkedIn profile link
    """
    import pandas as pd
    import io

    num_cols = len(df.columns)
    logger.info(f"Generating shortlisted CSV from df columns: {list(df.columns)}")
    logger.info(f"Using positional indices: Name=col 1, Email=col 2, Phone=col 3")

    records = []
    for cand in candidates:
        if cand.status == "Shortlisted":
            try:
                orig_idx = int(cand.id) - 1
                if 0 <= orig_idx < len(df):
                    row = df.iloc[orig_idx]
                else:
                    logger.warning(f"Candidate ID {cand.id} out of bounds for df of length {len(df)}")
                    continue
            except (ValueError, TypeError) as e:
                logger.warning(f"Failed to parse candidate ID {cand.id}: {str(e)}")
                continue

            # Extract by position (B=1, C=2, D=3)
            full_name = str(row.iloc[1]).strip() if num_cols > 1 else cand.name
            email_val = str(row.iloc[2]).strip() if num_cols > 2 else ""
            phone_val = str(row.iloc[3]).strip() if num_cols > 3 else ""

            # Fallback: if name looks wrong (empty/numeric), use cand.name
            if not full_name or full_name.replace(" ", "").isdigit():
                full_name = cand.name

            # Split Full Name into First Name and Last Name
            first_name = ""
            last_name = ""
            if full_name:
                parts = full_name.split(None, 1)
                if len(parts) == 2:
                    first_name, last_name = parts[0], parts[1]
                elif len(parts) == 1:
                    first_name = parts[0]

            records.append({
                "First Name": first_name,
                "Last Name": last_name,
                "Email Address": email_val,
                "Phone Number": phone_val
            })

    logger.info(f"Exporting {len(records)} shortlisted records to CSV")
    out_df = pd.DataFrame(records, columns=["First Name", "Last Name", "Email Address", "Phone Number"])

    output = io.StringIO()
    out_df.to_csv(output, index=False)
    return output.getvalue()


