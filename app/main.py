import sys
from pathlib import Path

# Add project root to python path to resolve config, services, and utils packages
project_root = Path(__file__).resolve().parent.parent
project_root_str = str(project_root)
if project_root_str not in sys.path:
    sys.path.insert(0, project_root_str)

# Resolve namespace collision with third-party config packages under Streamlit
modules_to_remove = []
for mod_name in list(sys.modules.keys()):
    if mod_name == "config" or mod_name.startswith("config."):
        mod = sys.modules[mod_name]
        if mod is None:
            modules_to_remove.append(mod_name)
            continue
        mod_file = getattr(mod, "__file__", None)
        if not mod_file:
            modules_to_remove.append(mod_name)
            continue
        try:
            mod_path = Path(mod_file).resolve()
            # If the file path is not within the project root directory, remove it
            if not str(mod_path).startswith(project_root_str):
                modules_to_remove.append(mod_name)
        except Exception:
            modules_to_remove.append(mod_name)

for mod_name in modules_to_remove:
    del sys.modules[mod_name]

import streamlit as st
import pandas as pd
import time
import os
import urllib.parse
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

import importlib
import config.config
import utils.helpers
import services.clipboard_parser
import services.google_drive
import services.parser
import services.ai_extractor
import services.matching
import utils.file_manager
import models.candidate

importlib.reload(config.config)
importlib.reload(utils.helpers)
importlib.reload(services.clipboard_parser)
importlib.reload(services.google_drive)
importlib.reload(services.parser)
importlib.reload(services.ai_extractor)
importlib.reload(services.matching)
importlib.reload(utils.file_manager)
importlib.reload(models.candidate)

from config.config import BASE_DIR, OUTPUT_DIR, MAX_CONCURRENCY, DEFAULT_SHORTLIST_THRESHOLD, DEFAULT_SCORING_WEIGHTS
from utils.helpers import logger, get_new_logs, format_eta
from services.clipboard_parser import parse_tsv_data, validate_tsv_columns, generate_output_tsv, generate_shortlisted_csv
from services.google_drive import download_resume
from services.parser import extract_text_from_file
from services.ai_extractor import extract_job_description, extract_resume_info
from services.matching import calculate_match_score, get_embedding, get_embeddings_batch, evaluate_candidate_recruiter
from utils.file_manager import clear_shortlisted_folder, download_and_copy_shortlisted, export_csv_results, archive_processing_logs
from models.candidate import Candidate
import streamlit.components.v1 as components

# Page configuration
st.set_page_config(
    page_title="FitCheck",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Load CSS custom styles
css_file = Path(__file__).resolve().parent / "style.css"
if css_file.exists():
    with open(css_file, "r") as f:
        st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)
else:
    st.warning("Custom CSS file not found. Falling back to default styles.")

# Session state initialization
if "processing" not in st.session_state:
    st.session_state.processing = False
if "candidates" not in st.session_state:
    st.session_state.candidates = []
if "log_history" not in st.session_state:
    st.session_state.log_history = []
if "metrics" not in st.session_state:
    st.session_state.metrics = {
        "total": 0, 
        "downloaded": 0, 
        "parsed": 0, 
        "ai_extracted": 0, 
        "failed": 0,
        "shortlisted": 0,
        "rejected": 0,
        "failed_downloads": 0,
        "failed_parsing": 0,
        "average_score": 0.0,
        "processing_time": 0.0,
        "eta": "N/A"
    }
if "output_tsv" not in st.session_state:
    st.session_state.output_tsv = ""
if "structured_jd_json" not in st.session_state:
    st.session_state.structured_jd_json = None

# Sidebar Configurations
with st.sidebar:
    st.markdown("### API Authentication")
    api_key_env = os.getenv("OPENAI_API_KEY", "")
    default_api_key = st.session_state.get("api_key_sidebar", api_key_env)
    
    api_key_input = st.text_input(
        "OpenAI API Key",
        type="password",
        placeholder="sk-...",
        value=default_api_key,
        help="Required for structured AI extraction of Job Profiles and Resumes."
    )
    st.session_state.api_key = api_key_input
    st.session_state.api_key_sidebar = api_key_input
    
    if not api_key_input:
        st.warning("Please enter your OpenAI API key to run matching.")
            
    st.markdown("---")
    st.markdown("### Match Weight Weights (%)")
    st.info("Adjust weights. Default values are loaded from config/settings.json.")
    
    # Load defaults from config
    dw = DEFAULT_SCORING_WEIGHTS
    w_req = st.slider("Required Skills Weight", 0, 100, int(dw.get("required_skills", 50)))
    w_sem = st.slider("Semantic Similarity Weight", 0, 100, int(dw.get("semantic_similarity", 20)))
    w_pref = st.slider("Preferred Skills Weight", 0, 100, int(dw.get("preferred_skills", 10)))
    w_exp = st.slider("Experience Match Weight", 0, 100, int(dw.get("experience", 10)))
    w_title = st.slider("Title Match Weight", 0, 100, int(dw.get("title", 5)))
    w_edu_cert = st.slider("Edu & Cert Match Weight", 0, 100, int(dw.get("education_certification", 5)))

    w_total = w_req + w_sem + w_pref + w_exp + w_title + w_edu_cert
    
    if w_total > 0:
        st.markdown(f"**Total Weight Sum**: `{w_total}` (Normalized to 100% internally)")
    else:
        st.error("Weights sum cannot be 0. Please increase at least one weight.")

    weights = {
        "required_skills": float(w_req),
        "preferred_skills": float(w_pref),
        "semantic_similarity": float(w_sem),
        "experience": float(w_exp),
        "title": float(w_title),
        "education_certification": float(w_edu_cert)
    }

# App Header
st.markdown('<div class="main-title">FitCheck</div>', unsafe_allow_html=True)

# Main Grid layout: 2 Columns
col1, col2 = st.columns([5, 7], gap="large")

with col1:
    st.subheader("Pipeline Setup")
    
    # Inputs
    job_desc = st.text_area(
        "Job Description Target Profile", 
        height=200, 
        placeholder="Paste the job description or target profile requirements here...",
        help="Paste the requirements you want candidates matched against."
    )
    
    tsv_input = st.text_area(
        "Candidate Data (TSV from Google Sheets)",
        height=200,
        placeholder="Name\tEmail\tResume Link\nJohn Doe\tjohn@email.com\thttps://drive.google.com/file/d/...\nJane Smith\tjane@email.com\thttps://drive.google.com/file/d/...",
        help="Copy rows directly from Google Sheets (including headers) and paste them here."
    )
    
    threshold = st.slider(
        "Shortlist Match Threshold (%)", 
        min_value=0, 
        max_value=100, 
        value=int(DEFAULT_SHORTLIST_THRESHOLD),
        help="Minimum percentage match needed to shortlist a candidate."
    )
    
    start_matching = False
    if st.session_state.processing:
        if st.button("Terminate Operation", key="terminate_btn", type="primary", use_container_width=True):
            if "pipeline_state" in st.session_state:
                st.session_state.pipeline_state["terminate"] = True
                st.warning("Termination requested. Cleaning up current tasks safely...")
    else:
        start_matching = st.button("Start Processing Pipeline", disabled=st.session_state.processing or w_total <= 0, use_container_width=True)
    st.markdown('</div>', unsafe_allow_html=True)

with col2:
    # 1. Summary Metrics Display
    metrics_placeholder = st.empty()
    
    def update_metrics_display():
        m = st.session_state.metrics
        metrics_placeholder.markdown(f"""
        <div class="metric-container" style="flex-wrap: wrap;">
            <div class="metric-card" style="min-width: 120px;">
                <div class="metric-val">{m.get("total", 0)}</div>
                <div class="metric-label">Total</div>
            </div>
            <div class="metric-card" style="border-left: 2px solid #22c55e; min-width: 120px;">
                <div class="metric-val" style="color: #22c55e;">{m.get("shortlisted", 0)}</div>
                <div class="metric-label">Shortlisted</div>
            </div>
            <div class="metric-card" style="border-left: 2px solid #94a3b8; min-width: 120px;">
                <div class="metric-val" style="color: #94a3b8;">{m.get("rejected", 0)}</div>
                <div class="metric-label">Rejected</div>
            </div>
            <div class="metric-card" style="border-left: 2px solid #38bdf8; min-width: 120px;">
                <div class="metric-val" style="color: #38bdf8;">{m.get("average_score", 0.0)}%</div>
                <div class="metric-label">Avg Score</div>
            </div>
        </div>
        <div class="metric-container" style="margin-top: 10px; flex-wrap: wrap;">
            <div class="metric-card" style="border-left: 2px solid #eab308; min-width: 120px;">
                <div class="metric-val" style="color: #eab308;">{m.get("failed_downloads", 0)}</div>
                <div class="metric-label">Fail Downloads</div>
            </div>
            <div class="metric-card" style="border-left: 2px solid #ef4444; min-width: 120px;">
                <div class="metric-val" style="color: #ef4444;">{m.get("failed_parsing", 0)}</div>
                <div class="metric-label">Fail Parsing</div>
            </div>
            <div class="metric-card" style="min-width: 120px;">
                <div class="metric-val" style="font-size: 1.4rem; padding-top: 5px;">{m.get("processing_time", 0.0)}s</div>
                <div class="metric-label">Time Elapsed</div>
            </div>
        </div>
        """, unsafe_allow_html=True)

    update_metrics_display()
    
    # 2. Progress Indicators
    progress_bar = st.progress(0.0)
    progress_text = st.empty()
    
    # 3. Terminal Logging Interface
    st.markdown("""
    <div class="terminal-title">
        <span class="terminal-dot dot-red"></span>
        <span class="terminal-dot dot-yellow"></span>
        <span class="terminal-dot dot-green"></span>
        &nbsp;&nbsp;Logs
    </div>
    """, unsafe_allow_html=True)
    log_placeholder = st.empty()
    
    def update_log_display():
        new_logs = get_new_logs()
        if new_logs:
            st.session_state.log_history.extend(new_logs)
            if len(st.session_state.log_history) > 300:
                st.session_state.log_history = st.session_state.log_history[-300:]
        log_content = "\n".join(st.session_state.log_history)
        log_placeholder.markdown(f'<div class="terminal-log">{log_content}</div>', unsafe_allow_html=True)

    update_log_display()

# 4. Results Section
table_placeholder = st.empty()

def update_table_display():
    pass

def pick_folder_subprocess() -> str:
    """Spawns a PowerShell COM folder picker to open a native Windows folder selection dialog."""
    import subprocess
    import sys
    
    cmd = [
        "powershell",
        "-NoProfile",
        "-Command",
        "$app = New-Object -ComObject Shell.Application; $folder = $app.BrowseForFolder(0, 'Select Folder to Download Resumes', 0); if ($folder) { Write-Output $folder.Self.Path }"
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120
        )
        path = result.stdout.strip()
        if path:
            return path
    except Exception as e:
        logger.warning(f"PowerShell COM folder picker failed: {str(e)}")
        
    # Fallback to Tkinter subprocess if PowerShell fails
    code = """
import tkinter as tk
from tkinter import filedialog
try:
    root = tk.Tk()
    root.withdraw()
    selected_dir = filedialog.askdirectory(title="Select Folder to Download Resumes")
    root.destroy()
    if selected_dir:
        print(selected_dir)
except Exception:
    pass
"""
    try:
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=120
        )
        return result.stdout.strip()
    except Exception as e:
        logger.warning(f"Fallback Tkinter subprocess folder picker failed: {str(e)}")
        return ""

# 5. Recruiter Export Panel & Output TSV Clipboard Section
# 5. Recruiter Export Panel
if st.session_state.candidates:
    update_table_display()
    st.markdown("---")
    st.markdown("### Recruiter Export Panel")
    
    shortlisted_candidates = [c for c in st.session_state.candidates if c.status == "Shortlisted"]
    has_shortlisted = len(shortlisted_candidates) > 0
    
    # 3 Columns for primary actions side-by-side
    col_tsv, col_csv, col_dl = st.columns(3)
    
    # Column 1: Copy TSV Results
    with col_tsv:
        if st.session_state.output_tsv:
            escaped_tsv = urllib.parse.quote(st.session_state.output_tsv)
            copy_button_html = f"""
            <script>
            function copyToClipboard() {{
                const text = decodeURIComponent('{escaped_tsv}');
                navigator.clipboard.writeText(text).then(function() {{
                    const btn = document.getElementById("copy-btn");
                    btn.innerText = "TSV Copied!";
                    btn.style.background = "linear-gradient(135deg, #059669 0%, #047857 100%)";
                    setTimeout(() => {{
                        btn.innerText = "Copy TSV Results";
                        btn.style.background = "linear-gradient(135deg, #10b981 0%, #059669 100%)";
                    }}, 3000);
                }}, function(err) {{
                    console.error('Could not copy text: ', err);
                }});
            }}
            </script>
            <button id="copy-btn" onclick="copyToClipboard()" style="
                background: linear-gradient(135deg, #10b981 0%, #059669 100%);
                color: white;
                border: none;
                font-weight: 600;
                font-size: 0.95rem;
                padding: 10px 20px;
                border-radius: 8px;
                cursor: pointer;
                width: 100%;
                box-shadow: 0 4px 12px rgba(16, 185, 129, 0.25);
                font-family: sans-serif;
                transition: all 0.3s ease;
                height: 42px;
            ">Copy TSV Results</button>
            """
            components.html(copy_button_html, height=50)
        else:
            st.button("Copy TSV Results", disabled=True, use_container_width=True)
            
    # Column 2: Export Shortlisted CSV
    with col_csv:
        csv_data = ""
        if has_shortlisted and tsv_input:
            try:
                df = parse_tsv_data(tsv_input)
                csv_data = generate_shortlisted_csv(df, st.session_state.candidates)
            except Exception as e:
                logger.error(f"Failed to generate shortlisted CSV: {str(e)}")
        st.download_button(
            label="Export Shortlisted CSV",
            data=csv_data,
            file_name="shortlisted_candidates.csv",
            mime="text/csv",
            disabled=not has_shortlisted or not csv_data,
            use_container_width=True
        )
        
    # Column 3: Download All Resumes
    with col_dl:
        if st.button("Download All Resumes", disabled=not st.session_state.candidates, use_container_width=True):
            selected_dir = pick_folder_subprocess()
            if selected_dir:
                st.session_state.download_target_dir = selected_dir
                st.session_state.trigger_download = True
                st.session_state.show_manual_path = False
                st.rerun()
            else:
                st.session_state.show_manual_path = True
                
    # Fallback path manual input field
    if st.session_state.get("show_manual_path", False):
        st.info("Please enter the destination path manually below.")
        fallback_path = st.text_input(
            "Destination Folder Path",
            value=st.session_state.get("download_target_dir", ""),
            placeholder="C:\\path\\to\\destination\\folder",
            key="fallback_path_input"
        )
        if st.button("Confirm and Download Resumes", use_container_width=True):
            if fallback_path and fallback_path.strip():
                st.session_state.download_target_dir = fallback_path.strip()
                st.session_state.trigger_download = True
                st.session_state.show_manual_path = False
                st.rerun()
            else:
                st.error("Please enter a valid path.")
                
    # Run manual download trigger
    if st.session_state.get("trigger_download", False) and st.session_state.get("download_target_dir"):
        target_path = Path(st.session_state.download_target_dir)
        with st.spinner("Downloading and copying resumes..."):
            summary = download_and_copy_shortlisted(st.session_state.candidates, target_path)
            st.session_state.download_summary = summary
            st.session_state.trigger_download = False
            st.rerun()
            
    # Download summary display
    if st.session_state.get("download_summary"):
        s = st.session_state.download_summary
        st.markdown("#### Resume Download Summary")
        st.success(f"Resumes successfully saved to: {s['destination']}")
        
        scol1, scol2, scol3 = st.columns(3)
        scol1.metric("Successfully Saved", s["downloaded"])
        scol2.metric("Failed to Save", s["failed"])
        scol3.metric("Time Taken", f"{s['time_taken']}s")
        
    # 5.1 Shortlisted Candidates Contact Details Grid
    if has_shortlisted and tsv_input:
        contact_records = []
        try:
            df = parse_tsv_data(tsv_input)
            num_cols = len(df.columns)
            # Use positional indices: col B=index 1 (Name), col C=index 2 (Email), col D=index 3 (Phone)
            # These correspond to the fixed Google Form sheet layout:
            # A=Timestamp, B=Full Name, C=Email, D=Phone, E=Resume, F=LinkedIn
            NAME_COL_IDX  = 1
            EMAIL_COL_IDX = 2
            PHONE_COL_IDX = 3

            for cand in shortlisted_candidates:
                try:
                    orig_idx = int(cand.id) - 1
                    if 0 <= orig_idx < len(df):
                        row = df.iloc[orig_idx]
                    else:
                        continue
                except Exception:
                    continue

                full_name = str(row.iloc[NAME_COL_IDX]).strip() if num_cols > NAME_COL_IDX else cand.name
                email_val = str(row.iloc[EMAIL_COL_IDX]).strip() if num_cols > EMAIL_COL_IDX else ""
                phone_val = str(row.iloc[PHONE_COL_IDX]).strip() if num_cols > PHONE_COL_IDX else ""

                first_name = ""
                last_name = ""
                if full_name:
                    parts = full_name.split(None, 1)
                    if len(parts) == 2:
                        first_name, last_name = parts[0], parts[1]
                    elif len(parts) == 1:
                        first_name = parts[0]

                contact_records.append({
                    "First Name": first_name,
                    "Last Name": last_name,
                    "Email Address": email_val,
                    "Phone Number": phone_val
                })
        except Exception as e:
            logger.error(f"Failed to parse contact details for tabular view: {str(e)}")

        if contact_records:
            st.markdown("#### Shortlisted Candidates Contact Details")

            rows_html = ""
            for rec in contact_records:
                rows_html += (
                    f'<tr onmouseover="this.style.background=\'rgba(99,102,241,0.12)\'" onmouseout="this.style.background=\'transparent\'" '
                    f'style="border-bottom:1px solid rgba(255,255,255,0.05);transition:background 0.2s;">'
                    f'<td style="padding:12px 16px;color:#f8fafc;font-size:0.95rem;">{rec["First Name"]}</td>'
                    f'<td style="padding:12px 16px;color:#f8fafc;font-size:0.95rem;">{rec["Last Name"]}</td>'
                    f'<td style="padding:12px 16px;color:#f8fafc;font-size:0.95rem;">{rec["Email Address"]}</td>'
                    f'<td style="padding:12px 16px;color:#f8fafc;font-size:0.95rem;">{rec["Phone Number"]}</td>'
                    f'</tr>'
                )

            th_style = 'style="padding:12px 16px;color:#94a3b8;font-family:\'Space Grotesk\',sans-serif;font-size:0.85rem;font-weight:600;letter-spacing:0.05em;text-transform:uppercase;"'
            table_html = (
                '<div style="overflow-x:auto;border-radius:12px;border:1px solid rgba(255,255,255,0.08);'
                'background:rgba(30,41,59,0.35);margin-top:10px;margin-bottom:20px;">'
                '<table style="width:100%;border-collapse:collapse;text-align:left;font-family:\'Outfit\',sans-serif;">'
                f'<thead><tr style="background:linear-gradient(135deg,#1e293b 0%,#0f172a 100%);border-bottom:1px solid rgba(255,255,255,0.08);">'
                f'<th {th_style}>First Name</th><th {th_style}>Last Name</th>'
                f'<th {th_style}>Email Address</th><th {th_style}>Phone Number</th>'
                f'</tr></thead><tbody>{rows_html}</tbody></table></div>'
            )
            components.html(table_html, height=max(80, 50 + len(contact_records) * 48), scrolling=False)
            
    if st.session_state.output_tsv:
        st.markdown("#### TSV Clipboard Text Viewer")
        st.text_area("Processed TSV Content", value=st.session_state.output_tsv, height=150, key="output_tsv_view")

# STAGE WORKERS FOR CONCURRENT PIPELINE
def download_and_parse_worker(cand: Candidate) -> Candidate:
    """Worker task: Download and parse resume text with retries and caching checks."""
    try:
        # Step 1: Download
        cand.status = "Downloading"
        dest_path, file_name = download_resume(cand.resume_url, cand.name)
        cand.local_path = str(dest_path)
        cand.file_name = file_name
        cand.status = "Downloaded"
        
        # Step 2: Parse
        cand.status = "Parsing"
        text = extract_text_from_file(dest_path)
        cand.extracted_text = text
        cand.status = "Parsed"
    except Exception as e:
        cand.status = "Failed"
        cand.error_message = str(e)
        logger.error(f"Download/Parse failed for candidate {cand.name}: {str(e)}")
    return cand

def extract_json_worker(cand: Candidate, api_key: str) -> Candidate:
    """Worker task: Concurrently extract candidate structured resume JSON."""
    if cand.status != "Parsed" or not cand.extracted_text:
        return cand
    try:
        cand.status = "AI Extracting"
        resume_json, warnings = extract_resume_info(cand.extracted_text, api_key)
        cand.structured_resume_json = resume_json
        cand.validation_warnings = warnings
        cand.status = "AI-Extracted"
    except Exception as e:
        cand.status = "Failed"
        cand.error_message = f"AI Extraction failed: {str(e)}"
        logger.error(f"AI Extraction failed for candidate {cand.name}: {str(e)}")
    return cand

def extract_and_score_worker(
    cand: Candidate, 
    jd_json: dict, 
    jd_text: str, 
    weights: dict, 
    api_key: str, 
    jd_emb: list, 
    res_emb: list,
    jd_title_emb: list,
    cand_title_emb: list,
    jd_edu_emb: list,
    cand_edu_emb: list,
    threshold: float
) -> Candidate:
    """Worker task: Extract JSON structures and calculate match scores concurrently."""
    if cand.status not in ["Parsed", "AI-Extracted"] or not cand.extracted_text:
        return cand
        
    try:
        # Pre-extract JSON structure if not already populated
        if not cand.structured_resume_json:
            cand.status = "AI Extracting"
            resume_json, warnings = extract_resume_info(cand.extracted_text, api_key)
            cand.structured_resume_json = resume_json
            cand.validation_warnings = warnings
            
        cand.status = "Scoring"
        
        # 1. Run holistic Recruiter AI Evaluation based on PDF guidelines
        eval_result = evaluate_candidate_recruiter(cand, jd_text, threshold, api_key)
        score = eval_result.get("overall_relevance_score", 0.0)
        cand.match_score = score
        cand.evaluation_reason = eval_result.get("reason", "")
        
        # 2. Run detailed sub-scoring for breakdown reporting/metrics (in background)
        _, sub_scores = calculate_match_score(
            cand, 
            jd_json, 
            jd_text, 
            weights, 
            api_key, 
            jd_emb=jd_emb, 
            res_emb=res_emb,
            jd_title_emb=jd_title_emb,
            cand_title_emb=cand_title_emb,
            jd_edu_emb=jd_edu_emb,
            cand_edu_emb=cand_edu_emb
        )
        cand.sub_scores = sub_scores
        
        # Classify based on AI Recruiter decision / threshold check
        decision = eval_result.get("decision", "Rejected")
        if decision == "Shortlisted" or score >= threshold:
            cand.status = "Shortlisted"
        else:
            cand.status = "Rejected"
            
    except Exception as e:
        cand.status = "Failed"
        cand.error_message = f"AI Match failed: {str(e)}"
        logger.error(f"AI Extraction/Scoring failed for candidate {cand.name}: {str(e)}")
    return cand

# PIPELINE EXECUTION ENGINE
def run_pipeline(
    pipeline_state: dict,
    job_desc: str,
    tsv_input: str,
    api_key: str,
    weights: dict,
    threshold: float
):
    import time
    from concurrent.futures import ThreadPoolExecutor
    
    pipeline_state["processing"] = True
    pipeline_state["completed"] = False
    pipeline_state["error"] = None
    pipeline_state["terminate"] = False
    pipeline_state["progress"] = 0.0
    pipeline_state["progress_text"] = "Starting matching pipeline..."
    
    logger.info("Initializing background recruiter pipeline...")
    
    try:
        start_time = time.time()
        io_workers = max(16, MAX_CONCURRENCY * 4)
        
        # Step 1: Parse Job Description
        if pipeline_state.get("terminate"):
            raise InterruptedError("Cancelled")
            
        pipeline_state["progress_text"] = "Structuring Job Description..."
        if job_desc:
            logger.info("Connecting to OpenAI to structure Job Description...")
            pipeline_state["structured_jd_json"] = extract_job_description(job_desc, api_key)
            logger.info("Job Description structured successfully.")
        else:
            pipeline_state["structured_jd_json"] = {
                "job_title": "None Specified",
                "required_skills": [],
                "preferred_skills": [],
                "required_experience": "0 years",
                "min_years_experience": 0.0,
                "education": [],
                "certifications": [],
                "employment_type": "Unknown",
                "industry": "Unknown",
                "seniority": "Unknown"
            }
            
        if pipeline_state.get("terminate"):
            raise InterruptedError("Cancelled")
            
        # Step 2: Parse Clipboard Data
        df = parse_tsv_data(tsv_input)
        name_col, resume_col = validate_tsv_columns(df)
        
        # Populate candidates
        candidates_list = []
        for idx, row in df.iterrows():
            candidates_list.append(Candidate(
                id=str(idx + 1),
                name=str(row[name_col]).strip() if name_col and name_col in df.columns else f"Row {idx+1}",
                resume_url=str(row[resume_col]).strip() if resume_col and resume_col in df.columns else "",
                status="Pending"
            ))
            
        pipeline_state["candidates"] = candidates_list
        total_count = len(candidates_list)
        
        pipeline_state["metrics"] = {
            "total": total_count,
            "downloaded": 0,
            "parsed": 0,
            "ai_extracted": 0,
            "failed": 0,
            "shortlisted": 0,
            "rejected": 0,
            "failed_downloads": 0,
            "failed_parsing": 0,
            "average_score": 0.0,
            "processing_time": 0.0,
            "eta": "Calculating..."
        }
        
        # ================= STAGE 1: CONCURRENT DOWNLOAD & PARSE =================
        if pipeline_state.get("terminate"):
            raise InterruptedError("Cancelled")
            
        logger.info("STAGE 1: Concurrent Download & Parsers starting...")
        
        with ThreadPoolExecutor(max_workers=io_workers) as executor:
            futures = {
                executor.submit(download_and_parse_worker, cand): cand 
                for cand in candidates_list
            }
            
            # Active Polling Loop
            while any(not f.done() for f in futures):
                if pipeline_state.get("terminate"):
                    logger.info("Termination requested during Stage 1. Cancelling pending tasks...")
                    for f, cand in futures.items():
                        if not f.done():
                            cancelled = f.cancel()
                            if cancelled or not f.running():
                                cand.status = "Cancelled"
                                cand.error_message = "Operation terminated by recruiter."
                    break
                
                done_count = sum(1 for f in futures if f.done())
                pipeline_state["progress"] = (done_count / max(1, total_count)) * 0.35
                pipeline_state["progress_text"] = f"Stage 1: Downloading & Parsing resumes... ({done_count}/{total_count})"
                
                # Update metrics
                parsed_so_far = len([c for c in candidates_list if c.status == "Parsed"])
                downloaded_so_far = len([c for c in candidates_list if c.status in ["Downloaded", "Parsed"]])
                failed_so_far = len([c for c in candidates_list if c.status == "Failed"])
                
                pipeline_state["metrics"]["downloaded"] = downloaded_so_far
                pipeline_state["metrics"]["parsed"] = parsed_so_far
                pipeline_state["metrics"]["failed"] = failed_so_far
                pipeline_state["metrics"]["failed_downloads"] = len([c for c in candidates_list if c.status == "Failed" and "Download" in (c.error_message or "")])
                pipeline_state["metrics"]["failed_parsing"] = len([c for c in candidates_list if c.status == "Failed" and "Parse" in (c.error_message or "")])
                pipeline_state["metrics"]["eta"] = format_eta(start_time, done_count, total_count)
                
                time.sleep(0.2)
                
        # Clean up any non-done candidates to Cancelled after executor exits
        for cand in candidates_list:
            if cand.status not in ["Downloaded", "Parsed", "Failed", "Cancelled"] and pipeline_state.get("terminate"):
                cand.status = "Cancelled"
                cand.error_message = "Operation terminated by recruiter."
                
        if pipeline_state.get("terminate"):
            raise InterruptedError("Cancelled")
            
        logger.info("Stage 1 completed successfully.")
        
        # ================= STAGE 2: CONCURRENT EXTRACTION & BATCHED EMBEDDINGS =================
        pipeline_state["progress"] = 0.40
        pipeline_state["progress_text"] = "Stage 2: Structuring profiles & batching embeddings..."
        logger.info("=========================================")
        logger.info("STAGE 2: AI Extraction & Batched Embeddings...")
        logger.info("=========================================")
        
        parsed_candidates = [c for c in candidates_list if c.status == "Parsed"]
        if parsed_candidates:
            # 2a. Concurrently structure resume JSON for all parsed candidates
            stage2_start = time.time()
            with ThreadPoolExecutor(max_workers=io_workers) as executor2:
                futures2 = {
                    executor2.submit(extract_json_worker, cand, api_key): cand
                    for cand in parsed_candidates
                }
                while any(not f.done() for f in futures2):
                    if pipeline_state.get("terminate"):
                        break
                    done_count2 = sum(1 for f in futures2 if f.done())
                    pipeline_state["progress"] = 0.40 + ((done_count2 / max(1, len(futures2))) * 0.20)
                    pipeline_state["progress_text"] = f"Stage 2: Structuring candidate resumes... ({done_count2}/{len(futures2)})"
                    pipeline_state["metrics"]["ai_extracted"] = len([c for c in candidates_list if c.status in ["AI-Extracted", "Shortlisted", "Rejected"]])
                    time.sleep(0.2)
                    
            if pipeline_state.get("terminate"):
                raise InterruptedError("Cancelled")
                
            # 2b. Batch all embeddings needed across candidates
            pipeline_state["progress"] = 0.62
            pipeline_state["progress_text"] = "Stage 2: Batch fetching embeddings..."
            
            jd_json = pipeline_state["structured_jd_json"]
            jd_title = jd_json.get("job_title", "")
            jd_edu_str = ", ".join(jd_json.get("education", []))
            
            texts_to_embed = [job_desc, jd_title, jd_edu_str]
            for cand in parsed_candidates:
                texts_to_embed.append(cand.extracted_text or "")
                res_j = cand.structured_resume_json or {}
                texts_to_embed.append(res_j.get("current_title", "") or "")
                texts_to_embed.append(", ".join(res_j.get("education", [])) or "")
                
            batch_embeddings = get_embeddings_batch(texts_to_embed, api_key)
            
            jd_emb = batch_embeddings[0] if len(batch_embeddings) > 0 else []
            jd_title_emb = batch_embeddings[1] if len(batch_embeddings) > 1 else []
            jd_edu_emb = batch_embeddings[2] if len(batch_embeddings) > 2 else []
            
            embedding_map = {}
            cand_title_emb_map = {}
            cand_edu_emb_map = {}
            idx = 3
            for cand in parsed_candidates:
                embedding_map[cand.id] = batch_embeddings[idx] if idx < len(batch_embeddings) else []
                cand_title_emb_map[cand.id] = batch_embeddings[idx+1] if idx+1 < len(batch_embeddings) else []
                cand_edu_emb_map[cand.id] = batch_embeddings[idx+2] if idx+2 < len(batch_embeddings) else []
                idx += 3
        else:
            jd_emb = []
            jd_title_emb = []
            jd_edu_emb = []
            embedding_map = {}
            cand_title_emb_map = {}
            cand_edu_emb_map = {}
            
        if pipeline_state.get("terminate"):
            raise InterruptedError("Cancelled")
            
        logger.info("Stage 2 completed successfully.")
        
        # ================= STAGE 3: CONCURRENT SCORING =================
        logger.info("=========================================")
        logger.info("STAGE 3: Concurrent Scoring...")
        logger.info("=========================================")
        
        stage3_start_time = time.time()
        with ThreadPoolExecutor(max_workers=io_workers) as executor3:
            futures3 = {
                executor3.submit(
                    extract_and_score_worker, 
                    cand, 
                    pipeline_state["structured_jd_json"], 
                    job_desc,
                    weights, 
                    api_key,
                    jd_emb,
                    embedding_map.get(cand.id),
                    jd_title_emb,
                    cand_title_emb_map.get(cand.id),
                    jd_edu_emb,
                    cand_edu_emb_map.get(cand.id),
                    threshold
                ): cand 
                for cand in candidates_list
                if cand.status in ["Parsed", "AI-Extracted"]
            }
            
            # Active Polling Loop
            while any(not f.done() for f in futures3):
                if pipeline_state.get("terminate"):
                    logger.info("Termination requested during Stage 3. Cancelling pending tasks...")
                    for f, cand in futures3.items():
                        if not f.done():
                            cancelled = f.cancel()
                            if cancelled or not f.running():
                                cand.status = "Cancelled"
                                cand.error_message = "Operation terminated by recruiter."
                    break
                
                done_count = sum(1 for f in futures3 if f.done())
                pipeline_state["progress"] = 0.65 + ((done_count / max(1, len(futures3))) * 0.30)
                pipeline_state["progress_text"] = f"Stage 3: Scoring candidate profiles... ({done_count}/{len(futures3)})"
                
                # Update metrics
                ai_extracted_so_far = len([c for c in candidates_list if c.status in ["AI-Extracted", "Shortlisted", "Rejected"]])
                failed_so_far = len([c for c in candidates_list if c.status == "Failed"])
                shortlisted_so_far = len([c for c in candidates_list if c.status == "Shortlisted"])
                rejected_so_far = len([c for c in candidates_list if c.status == "Rejected"])
                
                pipeline_state["metrics"]["ai_extracted"] = ai_extracted_so_far
                pipeline_state["metrics"]["failed"] = failed_so_far
                pipeline_state["metrics"]["shortlisted"] = shortlisted_so_far
                pipeline_state["metrics"]["rejected"] = rejected_so_far
                pipeline_state["metrics"]["eta"] = format_eta(stage3_start_time, done_count, len(futures3))
                
                time.sleep(0.2)
                
        # Clean up any non-done candidates to Cancelled after executor exits
        for cand in candidates_list:
            if cand.status not in ["Shortlisted", "Rejected", "Failed", "Cancelled"]:
                cand.status = "Cancelled"
                cand.error_message = "Operation terminated by recruiter."
                
        if pipeline_state.get("terminate"):
            raise InterruptedError("Cancelled")
            
        logger.info("Stage 3 completed successfully.")
        
    except InterruptedError:
        logger.info("Pipeline was terminated by user.")
    except Exception as e:
        logger.error(f"Pipeline encountered a critical error: {str(e)}")
        pipeline_state["error"] = str(e)
        
    # ================= STAGE 4: EXPORTS & LOGS WRITING (Runs always, even on termination) =================
    try:
        pipeline_state["progress"] = 0.95
        pipeline_state["progress_text"] = "Stage 4: Archiving and generating export files..."
        logger.info("Finalizing pipeline outputs...")
        
        # Sort UI table results descending by match score
        candidates_list = pipeline_state.get("candidates", [])
        
        def sorting_key(c):
            score_val = c.match_score if c.match_score is not None else -1.0
            return (score_val, c.status)
            
        candidates_list.sort(key=sorting_key, reverse=True)
        pipeline_state["candidates"] = candidates_list
        
        # Generate results.csv
        export_csv_results(candidates_list)
        
        # Generate Output TSV
        try:
            df = parse_tsv_data(tsv_input)
            output_tsv = generate_output_tsv(df, candidates_list)
            pipeline_state["output_tsv"] = output_tsv
        except Exception as e:
            logger.warning(f"Could not generate output TSV: {str(e)}")
            
        # Calculate final run metrics
        elapsed_time = time.time() - start_time
        shortlisted_cnt = len([c for c in candidates_list if c.status == "Shortlisted"])
        rejected_cnt = len([c for c in candidates_list if c.status == "Rejected"])
        failed_downloads = len([c for c in candidates_list if c.status == "Failed" and "Download" in (c.error_message or "")])
        failed_parsing = len([c for c in candidates_list if c.status == "Failed" and "Parse" in (c.error_message or "")])
        successful_scores = [c.match_score for c in candidates_list if c.match_score is not None]
        avg_score = sum(successful_scores) / len(successful_scores) if successful_scores else 0.0
        
        # Save logs
        import utils.helpers
        new_logs = utils.helpers.get_new_logs()
        if "log_history" not in pipeline_state:
            pipeline_state["log_history"] = []
        pipeline_state["log_history"].extend(new_logs)
        archive_processing_logs(pipeline_state["log_history"])
        
        pipeline_state["metrics"].update({
            "shortlisted": shortlisted_cnt,
            "rejected": rejected_cnt,
            "failed_downloads": failed_downloads,
            "failed_parsing": failed_parsing,
            "average_score": round(avg_score, 1),
            "processing_time": round(elapsed_time, 1),
            "eta": "Done"
        })
        
        pipeline_state["progress"] = 1.0
        if pipeline_state.get("terminate"):
            pipeline_state["progress_text"] = "Pipeline operation terminated. Partial results generated."
        else:
            pipeline_state["progress_text"] = "Pipeline processing completed successfully!"
            
    except Exception as e:
        logger.error(f"Error during Stage 4 export: {str(e)}")
        
    pipeline_state["completed"] = True
    pipeline_state["processing"] = False

if start_matching:
    if not tsv_input:
        st.error("Please paste Candidate Data in TSV format.")
    elif not st.session_state.api_key:
        st.error("Please enter an OpenAI API Key in the sidebar or configure it in your environment variables.")
    else:
        st.session_state.pipeline_state = {
            "processing": True,
            "completed": False,
            "error": None,
            "terminate": False,
            "progress": 0.0,
            "progress_text": "Starting background thread...",
            "candidates": [],
            "metrics": {
                "total": 0,
                "downloaded": 0,
                "parsed": 0,
                "ai_extracted": 0,
                "failed": 0,
                "shortlisted": 0,
                "rejected": 0,
                "failed_downloads": 0,
                "failed_parsing": 0,
                "average_score": 0.0,
                "processing_time": 0.0,
                "eta": "Calculating..."
            },
            "log_history": [],
            "output_tsv": "",
            "structured_jd_json": None
        }
        
        st.session_state.processing = True
        st.session_state.log_history = []
        
        import threading
        thread = threading.Thread(
            target=run_pipeline,
            args=(
                st.session_state.pipeline_state,
                job_desc,
                tsv_input,
                st.session_state.api_key,
                weights,
                float(threshold)
            ),
            daemon=True
        )
        thread.start()
        st.rerun()

# Progressive polling loop active when processing is True
if st.session_state.processing:
    p_state = st.session_state.get("pipeline_state", {})
    if p_state:
        # Pull updates into session state for rendering
        if "candidates" in p_state:
            st.session_state.candidates = p_state["candidates"]
        if "metrics" in p_state:
            st.session_state.metrics = p_state["metrics"]
        if "structured_jd_json" in p_state:
            st.session_state.structured_jd_json = p_state["structured_jd_json"]
        if "output_tsv" in p_state:
            st.session_state.output_tsv = p_state["output_tsv"]
            
        # Draw updated progress
        progress_val = p_state.get("progress", 0.0)
        progress_txt = p_state.get("progress_text", "Processing...")
        progress_bar.progress(progress_val)
        progress_text.text(progress_txt)
        
        update_metrics_display()
        update_table_display()
        update_log_display()
        
        # If thread completed
        if not p_state.get("processing", True):
            st.session_state.processing = False
            if p_state.get("error"):
                st.error(f"Pipeline failed: {p_state['error']}")
            elif p_state.get("terminate"):
                st.warning("Pipeline operation terminated by recruiter. Partial results are available.")
            else:
                st.success("Pipeline completed successfully!")
            st.rerun()
            
    time.sleep(0.5)
    st.rerun()
