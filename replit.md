# ERW Job Scorer

## Overview
A Flask application that analyzes scope extractor output (Excel files) and scores each job for relevance to ERW Site Solutions' four operating companies using Claude AI.

## ERW Site Solutions Companies
1. **ERW Retaining Walls** - Gravity & MSE retaining walls, engineered wall systems
2. **Kaufman Concrete** - Concrete foundations, flatwork for high-volume builders
3. **Landtec Landscape** - Landscape installation, irrigation systems, maintenance
4. **Ratliff Hardscape** - Hardscape, amenity centers, pools, pavers, outdoor living

## Scoring System (0-5)
- **0**: No meaningful scope
- **1**: Minimal scope (<$250k)
- **2**: Light scope ($100-250k range)
- **3**: Decent scope (meets $250k threshold)
- **4**: Strong scope (>$250k, high priority)
- **5**: Excellent scope (>$500k, top tier)

## Tech Stack
- Flask (Python web framework)
- pandas + openpyxl (Excel processing)
- Anthropic Claude API via Replit AI Integrations
- HTML/CSS/JavaScript frontend

## Project Structure
```
/
├── main.py              # Flask application
├── templates/
│   └── index.html       # Upload and results UI
├── pyproject.toml       # Python dependencies
└── replit.md            # This file
```

## Running the Application
The app runs on port 5000. Upload Excel files with scope extractor output to get AI-powered scoring for each ERW company.

## Multi-File Upload
When multiple files are uploaded, they are treated as belonging to the same job. The system combines all scope data from all files and provides a single consolidated assessment. This is useful when a project's scope is split across multiple Excel files (e.g., Civils and DDS extracts for the same job).

## Input File Formats
Supports both Civils-style and DDS-style scope extractor Excel formats with columns for sheet info and scope indicators like Retaining walls, Concrete flatwork, Irrigation, Pavers, etc.
