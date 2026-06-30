from dataclasses import dataclass, field
from typing import Optional, Dict, List

@dataclass
class Candidate:
    id: str  # Unique ID or row index
    name: str
    resume_url: str
    local_path: Optional[str] = None
    file_name: Optional[str] = None
    extracted_text: Optional[str] = None
    status: str = "Pending"  # Statuses: Pending, Downloading, Downloaded, Parsing, Parsed, AI-Extracted, Shortlisted, Rejected, Failed
    error_message: Optional[str] = None
    match_score: Optional[float] = None  # Score between 0 and 100
    
    # Phase 2 additions
    structured_resume_json: Optional[Dict] = None
    validation_warnings: List[str] = field(default_factory=list)
    
    # Phase 3 additions
    sub_scores: Optional[Dict[str, float]] = None  # Stores break-down scores (Required, Preferred, Semantic, Experience, Title, EduCert)
    evaluation_reason: Optional[str] = None  # Recruiter explanation for the evaluation

    def to_dict(self):
        """Helper to convert candidate state for pandas representation."""
        warnings_str = ", ".join(self.validation_warnings) if self.validation_warnings else "None"
        score_val = f"{self.match_score:.1f}%" if self.match_score is not None else "N/A"
        return {
            "ID": self.id,
            "Name": self.name,
            "Resume URL": self.resume_url,
            "Match Score": score_val,
            "Status": self.status,
            "Download Status": "Downloaded" if self.local_path else ("Failed" if self.status == "Failed" and "Download" in (self.error_message or "") else "Pending"),
            "Parse Status": "Parsed" if self.extracted_text else ("Failed" if self.status == "Failed" and "Parse" in (self.error_message or "") else "Pending"),
            "AI Extraction": "Completed" if self.structured_resume_json else ("Failed" if self.status == "Failed" and "AI" in (self.error_message or "") else "Pending"),
            "Warnings": warnings_str,
            "Reason": self.evaluation_reason or "N/A",
            "Error Message": self.error_message or "N/A"
        }
