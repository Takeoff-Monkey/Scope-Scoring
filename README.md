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
├── main.py                        # Flask web application
├── scorer.py                      # ECS Fargate task entrypoint (headless)
├── Dockerfile                     # Container image definition
├── requirements.txt               # Container dependencies
├── push-to-ecr.sh                 # Local ECR deployment script
├── .github/workflows/
│   └── deploy-ecs.yml             # GitHub Action for CI/CD
├── templates/
│   └── index.html                 # Web UI
├── pyproject.toml                 # Web app dependencies
├── uv.lock                        # Dependency lock file
└── README.md                      # This file
```

## ECS Fargate Deployment

The scorer runs as a headless ECS Fargate task invoked by AWS Step Functions using the WAIT_FOR_TASK (callback) pattern. It reads Scope Extractor JSON output files from S3, scores the job with Claude AI, and reports results back to Step Functions.

### Container Environment Variables

**Configured in the ECS task definition (static):**

| Variable | Required | Description |
|----------|----------|-------------|
| `ANTHROPIC_API_KEY` | Yes | Anthropic API key for Claude |
| `DATABASE_URL` | No | PostgreSQL connection string |
| `S3_BUCKET` | No | S3 bucket for writing results JSON |

**Passed via Step Functions `containerOverrides` (per invocation):**

| Variable | Required | Description |
|----------|----------|-------------|
| `INPUT_S3_BUCKET` | Yes | S3 bucket containing the input JSON files |
| `INPUT_S3_KEYS` | Yes | Comma-separated S3 object keys for the JSON files |
| `SCOPES` | No | JSON array of scope categories used in the Scope Extractor step, e.g. `'["Softscape", "Concrete flatwork"]'` |
| `TASK_TOKEN` | No | Step Functions callback token for `SendTaskSuccess`/`SendTaskFailure` |
| `GENERATE_PDF` | No | Set to `"true"` to include PDF as base64 in result |
| `SAVE_TO_DB` | No | Set to `"true"` to persist results to PostgreSQL |

### Step Functions Integration

The container uses the WAIT_FOR_TASK callback pattern:

1. Step Functions starts the ECS task via a `Run a Job (.sync)` or `Wait for a Callback (.waitForTaskToken)` state, passing `TASK_TOKEN` via `containerOverrides`
2. The task downloads files, scores the job, and calls `SendTaskSuccess` with the result JSON
3. If an error occurs (or the container receives SIGTERM), it calls `SendTaskFailure`

### ECS Task Protection

The container enables ECS scale-in protection when processing begins and disables it on completion or failure, preventing ECS from terminating the task mid-run. Protection automatically expires after 120 minutes as a safety fallback.

### Building and Deploying

```bash
# Build locally
docker build -t erw-job-scorer .

# Push to ECR using the helper script
./push-to-ecr.sh

# Or trigger the GitHub Action (Actions → Deploy ECS Task to ECR → Run workflow)
```

### Output — SendTaskSuccess Payload

```json
{
    "status": "completed",
    "job_id": "abc12345",
    "filename": "job123.json",
    "files_analyzed": ["job123.json"],
    "analyzed_at": "2024-01-15T10:30:00",
    "processing_time_seconds": 42.1,
    "summary": {
        "total_sheets": 240,
        "sheets_with_scope": 45,
        "scope_counts": {"concrete flatwork": 18, "fence": 7, "fencing": 7},
        "files_analyzed": ["job123.json"]
    },
    "scores": {
        "erw_retaining_walls": {"score": 3, "reasoning": "...", "key_indicators": [...]},
        "kaufman_concrete": {"score": 4, "reasoning": "...", "key_indicators": [...]},
        "landtec_landscape": {"score": 2, "reasoning": "...", "key_indicators": [...]},
        "ratliff_hardscape": {"score": 3, "reasoning": "...", "key_indicators": [...]},
        "overall_recommendation": "...",
        "package_score": 3
    },
    "s3_key": "results/abc12345.json",
    "s3_bucket": "my-results-bucket"
}
```

### Local Testing

```bash
export ANTHROPIC_API_KEY=sk-ant-...
export INPUT_S3_BUCKET=my-input-bucket
export INPUT_S3_KEYS=extractions/job123.json,extractions/job456.json
export SCOPES='["Concrete flatwork", "Fencing", "Softscape"]'

# Run without TASK_TOKEN — skips Step Functions callback, prints result to stdout
python scorer.py

# Or via Docker
docker run \
  -e ANTHROPIC_API_KEY="$ANTHROPIC_API_KEY" \
  -e INPUT_S3_BUCKET="$INPUT_S3_BUCKET" \
  -e INPUT_S3_KEYS="$INPUT_S3_KEYS" \
  -e SCOPES="$SCOPES" \
  erw-job-scorer
```

## Usage

1. Open the web interface in your browser
2. Drag and drop one or more Excel files (or click to browse)
3. Click "Analyze Job" to start the AI analysis
4. View the color-coded scores for each company
5. Export results as PDF for reporting

## License

Proprietary - ERW Site Solutions
