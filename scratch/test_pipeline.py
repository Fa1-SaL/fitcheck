import sys
import time
from pathlib import Path

# Add project root to python path
project_root = Path("C:/Users/faisa_2u122zn/Desktop/chigga")
sys.path.insert(0, str(project_root))

from services.google_drive import parse_google_drive_link
from services.clipboard_parser import parse_tsv_data, validate_tsv_columns, generate_output_tsv, generate_shortlisted_csv
from services.parser import extract_text_from_file
from services.ai_extractor import get_text_hash, read_from_cache, write_to_cache, validate_resume_json
from services.matching import calculate_match_score, cosine_similarity, get_embeddings_batch
from utils.file_manager import clear_shortlisted_folder, export_csv_results, archive_processing_logs
from utils.retries import retry
from config.config import app_settings, SETTINGS_FILE
from models.candidate import Candidate
import pandas as pd

def test_drive_link_parsing():
    print("Testing Google Drive link parsing...")
    links = [
        ("https://drive.google.com/file/d/1A2B3C4D5E6F7G8H9I0J/view?usp=sharing", ("1A2B3C4D5E6F7G8H9I0J", "binary")),
        ("https://drive.google.com/open?id=9H8G7F6E5D4C3B2A1", ("9H8G7F6E5D4C3B2A1", "binary")),
        ("https://docs.google.com/document/d/1gdocID12345/edit?usp=sharing", ("1gdocID12345", "document")),
        ("1A2B3C4D5E6F7G8H9I0J", ("1A2B3C4D5E6F7G8H9I0J", "binary"))
    ]
    for url, expected in links:
        res = parse_google_drive_link(url)
        assert res == expected, f"Failed on URL: {url}. Got {res}, expected {expected}"
    print("[OK] Google Drive link parsing passed!")

def test_tsv_parsing_and_generation():
    print("Testing TSV parsing, generation, and output formatting...")
    # Case 1: Columns do not exist
    tsv_text = "Candidate Name\tEmail\tResume Link\nJohn Doe\tjohn@email.com\thttps://drive.google.com/file/d/12345/view\nJane Smith\tjane@email.com\thttps://drive.google.com/file/d/67890/view\nJack Black\tjack@email.com\thttps://drive.google.com/file/d/abcde/view\n"
    df = parse_tsv_data(tsv_text)
    assert len(df) == 3
    
    candidates = [
        Candidate(id="1", name="John Doe", resume_url="https://drive.google.com/file/d/12345/view", status="Rejected", match_score=42.1),
        Candidate(id="2", name="Jane Smith", resume_url="https://drive.google.com/file/d/67890/view", status="Shortlisted", match_score=85.2),
        Candidate(id="3", name="Jack Black", resume_url="https://drive.google.com/file/d/abcde/view", status="Failed", error_message="Network Error")
    ]
    
    out_tsv = generate_output_tsv(df, candidates)
    lines = out_tsv.strip().split("\n")
    assert len(lines) == 3
    # Check rounded integer score, status, and original row order preserved
    assert lines[0] == "42\tRejected"
    assert lines[1] == "85\tShortlisted"
    assert lines[2] == "\tFailed"
    
    # Case 2: Columns already exist (e.g. AI Score, Status)
    tsv_text_with_cols = "Timestamp\tFull Name\tEmail\tPhone number\tResume\tLinkedIn profile link\tAI Score\tStatus\n6/22/2026 12:01:05\tChirag Raj\trajchirag2004@gmail.com\t9473356202\thttps://drive.google.com/file/d/12345/view\twww.linkedin.com/in/chirag\t\t\n6/22/2026 12:12:25\tVidhi Larokar\tlarokarvidhi@gmail.com\t7634995243\thttps://drive.google.com/file/d/67890/view\tlinkedin.com/in/vidhi-larol\t\t\n"
    df_with_cols = parse_tsv_data(tsv_text_with_cols)
    assert len(df_with_cols.columns) == 8
    
    # Assert validate_tsv_columns correctly picks 'Resume' as the resume column, NOT 'LinkedIn profile link'
    name_col, resume_col = validate_tsv_columns(df_with_cols)
    assert name_col == "Full Name"
    assert resume_col == "Resume"
    
    cands_with_cols = [
        Candidate(id="1", name="Chirag Raj", resume_url="https://drive.google.com/file/d/12345/view", status="Rejected", match_score=42.1),
        Candidate(id="2", name="Vidhi Larokar", resume_url="https://drive.google.com/file/d/67890/view", status="Shortlisted", match_score=85.2)
    ]
    
    out_tsv_cols = generate_output_tsv(df_with_cols, cands_with_cols)
    lines_cols = out_tsv_cols.strip().split("\n")
    assert len(lines_cols) == 2
    # Chirag Raj (index 0) -> 42, Rejected
    assert lines_cols[0] == "42\tRejected"
    # Vidhi Larokar (index 1) -> 85, Shortlisted
    assert lines_cols[1] == "85\tShortlisted"
    
    print("[OK] TSV parsing, generation, and output formatting tests passed!")

def io_string_helper(text):
    import io
    return io.StringIO(text)

def test_cosine_similarity():
    print("Testing cosine similarity math...")
    v1 = [1.0, 0.0, 0.0]
    v2 = [1.0, 0.0, 0.0]
    assert abs(cosine_similarity(v1, v2) - 1.0) < 1e-6
    print("[OK] Cosine similarity calculations passed!")

def test_file_manager_operations():
    print("Testing file manager...")
    candidates = [
        Candidate(id="1", name="John Doe", resume_url="http://drive/123", status="Rejected", match_score=42.1),
        Candidate(id="2", name="Jane Smith", resume_url="http://drive/456", status="Shortlisted", match_score=85.2)
    ]
    csv_path = export_csv_results(candidates)
    log_path = archive_processing_logs(["Line 1 log", "Line 2 log"])
    clear_shortlisted_folder()
    
    assert csv_path.exists()
    assert log_path.exists()
    
    if csv_path.exists():
        csv_path.unlink()
    if log_path.exists():
        log_path.unlink()
    print("[OK] File manager operations passed!")

def test_settings_loading():
    print("Testing settings loading from file...")
    assert SETTINGS_FILE.exists(), f"Settings file not found: {SETTINGS_FILE}"
    assert app_settings is not None
    assert "openai_model" in app_settings
    assert "max_concurrency" in app_settings
    print("[OK] Settings loading passed!")

def test_retry_decorator():
    print("Testing retry decorator...")
    call_count = 0
    
    @retry(max_retries=2, backoff_factor=1.5, exceptions=(ValueError,))
    def fail_twice():
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise ValueError("Temporary failure")
        return "success"
        
    res = fail_twice()
    assert res == "success"
    assert call_count == 3
    print("[OK] Retry decorator passed!")

def test_embeddings_batching():
    print("Testing embeddings batching logic...")
    # Bypassing OpenAI with empty/null input check
    texts = ["", "  "]
    embeddings = get_embeddings_batch(texts, "dummy_key")
    assert len(embeddings) == 2
    assert len(embeddings[0]) == 1536
    print("[OK] Embeddings batching logic passed!")

def test_generate_shortlisted_csv():
    print("Testing shortlisted CSV generation...")
    # Case 1: Full Google Form sheet layout (A=Timestamp, B=Name, C=Email, D=Phone, E=Resume)
    tsv_text = "Timestamp\tFull Name\tEmail\tPhone number\tResume\n6/22/2026 12:00:00\tJohn Doe\tjohn@email.com\t1234567890\thttps://drive.google.com/file/d/123/view\n6/22/2026 12:01:00\tJane Mary Smith\tjane@email.com\t0987654321\thttps://drive.google.com/file/d/456/view\n"
    df = parse_tsv_data(tsv_text)
    candidates = [
        Candidate(id="1", name="John Doe", resume_url="https://drive.google.com/file/d/123/view", status="Rejected", match_score=42.1),
        Candidate(id="2", name="Jane Mary Smith", resume_url="https://drive.google.com/file/d/456/view", status="Shortlisted", match_score=85.2)
    ]

    csv_str = generate_shortlisted_csv(df, candidates)
    lines = [line.replace("\r", "").strip() for line in csv_str.strip().split("\n")]
    assert len(lines) == 2, f"Expected 2 lines, got {len(lines)}: {lines}"
    assert lines[0] == "First Name,Last Name,Email Address,Phone Number"
    # Jane Mary Smith: First=Jane, Last=Mary Smith, email=jane@email.com, phone=0987654321
    assert lines[1] == "Jane,Mary Smith,jane@email.com,0987654321", f"Got: {lines[1]}"

    # Case 2: Real-world sheet with Unnamed: columns (Timestamp, Full Name, Email, Phone, Resume, LinkedIn, Unnamed: 6, ...)
    tsv_unnamed = "Timestamp\tFull Name\tEmail\tPhone number\tResume\tLinkedIn profile link\tUnnamed: 6\tUnnamed: 7\n6/22/2026 12:01:05\tChirag Raj\trajchirag2004@gmail.com\t9473356202\thttps://drive.google.com/file/d/12345/view\twww.linkedin.com/in/chirag\t\t\n"
    df_unnamed = parse_tsv_data(tsv_unnamed)
    candidates_unnamed = [
        Candidate(id="1", name="Chirag Raj", resume_url="https://drive.google.com/file/d/12345/view", status="Shortlisted", match_score=75.0)
    ]
    csv_unnamed_str = generate_shortlisted_csv(df_unnamed, candidates_unnamed)
    lines_unnamed = [line.replace("\r", "").strip() for line in csv_unnamed_str.strip().split("\n")]
    assert len(lines_unnamed) == 2, f"Expected 2 lines, got {len(lines_unnamed)}: {lines_unnamed}"
    assert lines_unnamed[0] == "First Name,Last Name,Email Address,Phone Number"
    # Chirag Raj: First=Chirag, Last=Raj, email=rajchirag2004@gmail.com, phone=9473356202
    assert lines_unnamed[1] == "Chirag,Raj,rajchirag2004@gmail.com,9473356202", f"Got: {lines_unnamed[1]}"
    print("[OK] Shortlisted CSV generation tests passed!")

def test_get_non_conflicting_path():
    print("Testing get_non_conflicting_path...")
    from utils.file_manager import get_non_conflicting_path
    import tempfile
    
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        f1 = tmp_path / "test.txt"
        f1.touch()
        
        # When file doesn't exist under the conflict name
        p2 = get_non_conflicting_path(tmp_path, "test.txt")
        assert p2 == tmp_path / "test_1.txt"
        
        # Create test_1.txt and verify it increments again
        p2.touch()
        p3 = get_non_conflicting_path(tmp_path, "test.txt")
        assert p3 == tmp_path / "test_2.txt"
    print("[OK] Non-conflicting path resolution passed!")

if __name__ == "__main__":
    print("Running pipeline verification tests...")
    test_drive_link_parsing()
    test_tsv_parsing_and_generation()
    test_cosine_similarity()
    test_file_manager_operations()
    test_settings_loading()
    test_retry_decorator()
    test_embeddings_batching()
    test_generate_shortlisted_csv()
    test_get_non_conflicting_path()
    print("All tests completed successfully!")
