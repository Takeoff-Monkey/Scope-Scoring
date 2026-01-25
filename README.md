# ERW Job Scorer

An AI-powered construction project analysis platform that scores job opportunities for ERW Site Solutions, a Texas-based exterior improvements contractor. The application analyzes scope extractor output (Excel files from construction project documentation) and uses Claude AI to intelligently score opportunities across four operating divisions.

## Features

- **Multi-File Excel Upload**: Upload single or multiple Excel files containing scope extractor data
- **AI-Powered Scoring**: Claude AI analyzes scope data and scores each job on a 0-5 scale
- **Multi-Company Assessment**: Simultaneously scores opportunities for four specialized companies:
  - ERW Retaining Walls
  - Kaufman Concrete
  - Landtec Landscape
  - Ratliff Hardscape
- **Combined Job Processing**: Multiple files are automatically combined and analyzed as a single job
- **PDF Export**: Export single or batch job assessments as professional PDFs
- **Job Persistence**: Results stored in PostgreSQL for historical tracking
- **Interactive Web UI**: Drag-and-drop upload with real-time analysis feedback and color-coded scoring

## Technology Stack

**Backend:**
- Python 3.11+
- Flask 3.1.2
- Anthropic SDK (Claude AI)
- pandas & openpyxl (Excel processing)
- psycopg2 (PostgreSQL)
- ReportLab (PDF generation)

**Frontend:**
- HTML5 / CSS3 / Vanilla JavaScript

## Setup

### Prerequisites

- Python 3.11 or higher
- PostgreSQL database
- Anthropic API key

### Installation

1. Clone the repository:
   ```bash
   git clone <repository-url>
   cd Scope-Scoring
   ```

2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   # or using uv:
   uv sync
   ```

3. Set environment variables:
   ```bash
   export DATABASE_URL=postgresql://user:password@host/dbname
   export AI_INTEGRATIONS_ANTHROPIC_API_KEY=sk-ant-...
   ```

4. Run the application:
   ```bash
   python main.py
   ```

The application will be available at `http://localhost:5000`.

## Configuration

### Environment Variables

| Variable | Description | Required |
|----------|-------------|----------|
| `DATABASE_URL` | PostgreSQL connection string | Yes |
| `AI_INTEGRATIONS_ANTHROPIC_API_KEY` | Anthropic API key for Claude | Yes |
| `AI_INTEGRATIONS_ANTHROPIC_BASE_URL` | Custom API endpoint (optional) | No |

### Supported Input Files

Excel files (.xlsx, .xls) containing scope extractor data with columns for:
- Page/Sheet information
- Scope indicators (Retaining walls, Concrete flatwork, Irrigation, Pavers, etc.)
- Density ratings
- Scope summary text

## Scoring System

Jobs are scored on a 0-5 scale based on estimated scope value:

| Score | Threshold | Interpretation |
|-------|-----------|----------------|
| 0 | No scope | No meaningful scope for this company |
| 1 | <$250k | Minimal scope, package completion only |
| 2 | $100-250k | Light scope, borderline viability |
| 3 | ~$250k | Decent scope, worth pursuing |
| 4 | >$250k | Strong scope, high priority |
| 5 | >$500k | Excellent scope, top tier opportunity |

### Company-Specific Indicators

- **ERW Retaining Walls**: MSE walls, gravity walls, boulder walls, grade changes, tiered walls
- **Kaufman Concrete**: Sidewalks, curb/gutter, concrete paving, driveways, ADA ramps
- **Landtec Landscape**: Trees, shrubs, sod, planting, mulch, irrigation systems
- **Ratliff Hardscape**: Pavers, stone, decomposed granite, site furnishings, water features

## API Routes

| Route | Method | Description |
|-------|--------|-------------|
| `/` | GET | Main web interface |
| `/analyze` | POST | Upload files and trigger AI scoring |
| `/results/<job_id>` | GET | Retrieve stored job results |
| `/export-pdf/<job_id>` | GET | Export single job as PDF |
| `/export-pdf-batch` | GET | Export multiple jobs as PDF |

## Project Structure

```
Scope-Scoring/
├── main.py                  # Flask backend application
├── lambda_handler.py        # AWS Lambda handler (headless)
├── Dockerfile               # Docker image for Lambda
├── requirements-lambda.txt  # Lambda-specific dependencies
├── templates/
│   └── index.html           # Web UI
├── pyproject.toml           # Python dependencies
├── uv.lock                  # Dependency lock file
└── README.md                # This file
```

## AWS Lambda Deployment

The scorer can run as a headless AWS Lambda function in a Docker container. Files are fetched from Google Drive.

### Building the Docker Image

```bash
docker build -t erw-job-scorer .
```

### Pushing to Amazon ECR

```bash
# Authenticate with ECR
aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin <account-id>.dkr.ecr.us-east-1.amazonaws.com

# Tag and push
docker tag erw-job-scorer:latest <account-id>.dkr.ecr.us-east-1.amazonaws.com/erw-job-scorer:latest
docker push <account-id>.dkr.ecr.us-east-1.amazonaws.com/erw-job-scorer:latest
```

### Google Drive Setup

1. Create a Google Cloud project and enable the Google Drive API
2. Create a service account and download the JSON credentials
3. Share the Google Drive files/folders with the service account email
4. Base64-encode the credentials JSON for the environment variable:
   ```bash
   base64 -i service-account.json
   ```

### Lambda Configuration

**Environment Variables:**

| Variable | Description | Required |
|----------|-------------|----------|
| `ANTHROPIC_API_KEY` | Anthropic API key for Claude | Yes |
| `GOOGLE_CREDENTIALS_JSON` | Base64-encoded service account JSON | Yes |
| `GOOGLE_DRIVE_FILE_IDS` | Comma-separated Google Drive file IDs | Yes |
| `DATABASE_URL` | PostgreSQL connection string | No |
| `GENERATE_PDF` | Set to "true" to include PDF in response | No |
| `SAVE_TO_DB` | Set to "true" to persist results | No |

**Recommended Settings:**

- Memory: 512 MB minimum (1024 MB recommended for large files)
- Timeout: 60 seconds minimum (120 seconds recommended)

### Lambda Event Format (Optional Overrides)

The Lambda reads configuration from environment variables by default, but you can override via the event:

```json
{
    "file_ids": ["1ABC123...", "1DEF456..."],
    "save_to_db": false,
    "generate_pdf": true
}
```

### Lambda Response Format

```json
{
    "success": true,
    "job_id": "abc12345",
    "filename": "project.xlsx",
    "files_analyzed": ["project.xlsx"],
    "analyzed_at": "2024-01-15T10:30:00",
    "summary": {
        "total_sheets": 45,
        "sheets_with_scope": 12,
        "scope_counts": {...},
        "files_analyzed": [...]
    },
    "scores": {
        "erw_retaining_walls": {"score": 3, "reasoning": "...", "key_indicators": [...]},
        "kaufman_concrete": {"score": 4, "reasoning": "...", "key_indicators": [...]},
        "landtec_landscape": {"score": 2, "reasoning": "...", "key_indicators": [...]},
        "ratliff_hardscape": {"score": 3, "reasoning": "...", "key_indicators": [...]},
        "overall_recommendation": "...",
        "package_score": 3
    },
    "pdf_base64": "..."
}
```

### Local Testing

```bash
# Set environment variables
export ANTHROPIC_API_KEY=sk-ant-...
export GOOGLE_CREDENTIALS_JSON=$(base64 -i service-account.json)
export GOOGLE_DRIVE_FILE_IDS=1ABC123...,1DEF456...

# Run the handler
python lambda_handler.py

# Or pass file IDs as arguments
python lambda_handler.py 1ABC123... 1DEF456...
```

## Usage

1. Open the web interface in your browser
2. Drag and drop one or more Excel files (or click to browse)
3. Click "Analyze Job" to start the AI analysis
4. View the color-coded scores for each company
5. Export results as PDF for reporting

## License

Proprietary - ERW Site Solutions
