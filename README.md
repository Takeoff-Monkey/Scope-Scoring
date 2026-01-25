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
├── main.py              # Flask backend application
├── templates/
│   └── index.html       # Web UI
├── pyproject.toml       # Python dependencies
├── uv.lock              # Dependency lock file
└── README.md            # This file
```

## Usage

1. Open the web interface in your browser
2. Drag and drop one or more Excel files (or click to browse)
3. Click "Analyze Job" to start the AI analysis
4. View the color-coded scores for each company
5. Export results as PDF for reporting

## License

Proprietary - ERW Site Solutions
