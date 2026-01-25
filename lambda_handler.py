"""
AWS Lambda handler for ERW Job Scorer.

This module provides a headless version of the job scoring functionality
that can run in an AWS Lambda container. Files are fetched from Google Drive.

Environment Variables:
    ANTHROPIC_API_KEY: Anthropic API key for Claude (required)
    GOOGLE_CREDENTIALS_JSON: Base64-encoded Google service account JSON (required)
    GOOGLE_DRIVE_FILE_IDS: Comma-separated list of Google Drive file IDs to process (required)
    DATABASE_URL: PostgreSQL connection string (optional, for persistence)
    GENERATE_PDF: Set to "true" to include PDF in response (optional)

Event Format (optional overrides):
{
    "file_ids": ["file_id_1", "file_id_2"],  # Override GOOGLE_DRIVE_FILE_IDS
    "save_to_db": false,                      # Save results to database
    "generate_pdf": false                     # Return PDF as base64
}

Response Format:
{
    "success": true,
    "job_id": "abc12345",
    "filename": "project.xlsx",
    "files_analyzed": ["project.xlsx"],
    "analyzed_at": "2024-01-15T10:30:00",
    "summary": {...},
    "scores": {...},
    "pdf_base64": "..."
}
"""

import anthropic
import pandas as pd
import os
import json
import uuid
import base64
from datetime import datetime
from io import BytesIO

# Google Drive imports
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

# Optional database imports
try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
    HAS_PSYCOPG2 = True
except ImportError:
    HAS_PSYCOPG2 = False

# Optional PDF imports
try:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    HAS_REPORTLAB = True
except ImportError:
    HAS_REPORTLAB = False


# Initialize Anthropic client
anthropic_client = anthropic.Anthropic(
    api_key=os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("AI_INTEGRATIONS_ANTHROPIC_API_KEY"),
    base_url=os.environ.get("ANTHROPIC_BASE_URL") or os.environ.get("AI_INTEGRATIONS_ANTHROPIC_BASE_URL")
)


def get_google_drive_service():
    """Initialize Google Drive API service using credentials from environment."""
    credentials_json = os.environ.get("GOOGLE_CREDENTIALS_JSON")
    if not credentials_json:
        raise RuntimeError("GOOGLE_CREDENTIALS_JSON environment variable not set")

    # Decode base64 credentials
    try:
        credentials_data = json.loads(base64.b64decode(credentials_json))
    except Exception:
        # Try parsing as plain JSON if not base64
        credentials_data = json.loads(credentials_json)

    credentials = service_account.Credentials.from_service_account_info(
        credentials_data,
        scopes=['https://www.googleapis.com/auth/drive.readonly']
    )

    return build('drive', 'v3', credentials=credentials)


def download_file_from_drive(service, file_id):
    """Download a file from Google Drive and return its content and metadata."""
    # Get file metadata
    file_metadata = service.files().get(fileId=file_id, fields='name, mimeType').execute()
    filename = file_metadata.get('name', f'{file_id}.xlsx')

    # Download file content
    request = service.files().get_media(fileId=file_id)
    file_content = BytesIO()
    downloader = MediaIoBaseDownload(file_content, request)

    done = False
    while not done:
        status, done = downloader.next_chunk()

    file_content.seek(0)
    return file_content, filename


def get_db_connection():
    """Get database connection if DATABASE_URL is configured."""
    if not HAS_PSYCOPG2:
        raise RuntimeError("psycopg2 not installed")
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL not configured")
    return psycopg2.connect(database_url)


def save_job_result(job_id, filename, summary, scores):
    """Save job result to database."""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('''
        INSERT INTO job_results (job_id, filename, analyzed_at, summary, scores)
        VALUES (%s, %s, %s, %s, %s)
    ''', (job_id, filename, datetime.now(), json.dumps(summary), json.dumps(scores)))
    conn.commit()
    cur.close()
    conn.close()


def normalize_columns(df):
    """Normalize column names to standard format."""
    column_mapping = {
        'Page': 'pdf_page',
        'Sheet Number': 'sheet_number',
        'Title': 'title',
        'Scale': 'scale',
        'Scope Summary': 'scope_summary',
        'Density': 'density',
        'Est. Takeoff Time': 'estimated_takeoff_time'
    }
    df.columns = [column_mapping.get(col, col) for col in df.columns]
    return df


def prepare_scope_summary(df):
    """Extract scope summary from dataframe."""
    scope_columns = [
        'Aggregates / gravel', 'Concrete flatwork', 'Fencing', 'Furnishings',
        'Irrigation', 'Pavers', 'Retaining walls', 'Softscape (landscape planting)',
        'Synthetic turf', 'Drainage', 'Lighting', 'BMP / Environmental / Bioswales'
    ]

    existing_scope_cols = [c for c in scope_columns if c in df.columns]

    scope_counts = {}
    for col in existing_scope_cols:
        count = df[col].notna().sum()
        if count > 0:
            scope_counts[col] = int(count)

    if existing_scope_cols:
        sheets_with_scope = df[df[existing_scope_cols].notna().any(axis=1)]
    else:
        sheets_with_scope = pd.DataFrame()

    scope_summaries = []
    for _, row in sheets_with_scope.iterrows():
        sheet_info = f"Sheet {row.get('sheet_number', 'N/A')}: {row.get('title', 'N/A')}"
        summary = row.get('scope_summary', '')
        density = row.get('density', '')

        marked_items = [col for col in existing_scope_cols if pd.notna(row.get(col))]

        scope_summaries.append({
            'sheet': sheet_info,
            'summary': summary,
            'density': density,
            'marked_scope': marked_items
        })

    return {
        'total_sheets': len(df),
        'sheets_with_scope': len(sheets_with_scope),
        'scope_indicator_counts': scope_counts,
        'sheet_details': scope_summaries[:50]
    }


def combine_scope_data(scope_data_list):
    """Combine scope data from multiple files into a single summary."""
    combined = {
        'total_sheets': 0,
        'sheets_with_scope': 0,
        'scope_indicator_counts': {},
        'sheet_details': []
    }

    for scope_data in scope_data_list:
        combined['total_sheets'] += scope_data['total_sheets']
        combined['sheets_with_scope'] += scope_data['sheets_with_scope']

        for indicator, count in scope_data['scope_indicator_counts'].items():
            combined['scope_indicator_counts'][indicator] = combined['scope_indicator_counts'].get(indicator, 0) + count

        combined['sheet_details'].extend(scope_data['sheet_details'])

    combined['sheet_details'] = combined['sheet_details'][:50]

    return combined


def score_job(scope_data):
    """Score the job using Claude AI."""
    prompt = f"""You are an expert construction estimator familiar with ERW Site Solutions, a Texas-based exterior improvements contractor. Analyze this scope extractor output and score the job for each of their four companies.

## Scope Data Summary

**Total sheets analyzed:** {scope_data['total_sheets']}
**Sheets with identifiable scope:** {scope_data['sheets_with_scope']}

**Scope indicator counts across all sheets:**
{json.dumps(scope_data['scope_indicator_counts'], indent=2)}

**Detailed sheet-by-sheet scope (showing sheets with marked scope items):**
{json.dumps(scope_data['sheet_details'], indent=2)}

## Scoring Instructions

Score each company from 0-5 based on:
- **0**: No meaningful scope for this company
- **1**: Minimal scope, clearly under $250k, only useful to complete a package
- **2**: Light scope, borderline viability ($100-250k range)
- **3**: Decent scope, likely meets $250k threshold, worth pursuing
- **4**: Strong scope, clearly exceeds $250k, high priority
- **5**: Excellent scope, major opportunity ($500k+), top tier

## Company Scope Mapping

**ERW Retaining Walls**: Look for `Retaining walls` indicators and mentions of MSE walls, gravity walls, boulder walls, grade changes, tiered walls, structural walls in summaries.

**Kaufman Concrete**: Look for `Concrete flatwork` indicators and mentions of sidewalks, curb/gutter, concrete paving, driveways, ADA ramps, concrete steps, reinforced concrete in summaries.

**Landtec Landscape**: Look for `Softscape (landscape planting)`, `Irrigation`, `Synthetic turf` indicators and mentions of trees, shrubs, sod, planting, mulch, irrigation systems in summaries.

**Ratliff Hardscape**: Look for `Pavers`, `Aggregates / gravel`, `Furnishings` indicators and mentions of pavers, stone, decomposed granite, site furnishings, benches, water features, pools, outdoor amenities, pavilions, playground equipment in summaries.

## Important Considerations

1. **Sheet count matters**: More sheets with scope = larger project
2. **Density ratings**: "High" density sheets have more work than "Low" density
3. **Cross-reference summaries**: The scope_summary often contains details not captured in indicator columns
4. **Package value**: Even if one company has low scope, it might still be valuable to complete a turnkey package

Respond with ONLY a JSON object in this exact format:
{{
    "erw_retaining_walls": {{
        "score": <0-5>,
        "reasoning": "<brief explanation of score>",
        "key_indicators": ["<specific items found>"]
    }},
    "kaufman_concrete": {{
        "score": <0-5>,
        "reasoning": "<brief explanation of score>",
        "key_indicators": ["<specific items found>"]
    }},
    "landtec_landscape": {{
        "score": <0-5>,
        "reasoning": "<brief explanation of score>",
        "key_indicators": ["<specific items found>"]
    }},
    "ratliff_hardscape": {{
        "score": <0-5>,
        "reasoning": "<brief explanation of score>",
        "key_indicators": ["<specific items found>"]
    }},
    "overall_recommendation": "<1-2 sentence summary of opportunity>",
    "package_score": <0-5 overall attractiveness as turnkey package>
}}"""

    message = anthropic_client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=1024,
        messages=[
            {"role": "user", "content": prompt}
        ]
    )

    response_text = message.content[0].text
    if "```json" in response_text:
        response_text = response_text.split("```json")[1].split("```")[0]
    elif "```" in response_text:
        response_text = response_text.split("```")[1].split("```")[0]

    return json.loads(response_text.strip())


def generate_pdf(job_results_list):
    """Generate PDF report for job results."""
    if not HAS_REPORTLAB:
        raise RuntimeError("reportlab not installed")

    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter, topMargin=0.5*inch, bottomMargin=0.5*inch)
    styles = getSampleStyleSheet()

    title_style = ParagraphStyle('Title', parent=styles['Heading1'], fontSize=18, spaceAfter=20, textColor=colors.HexColor('#1a365d'))
    heading_style = ParagraphStyle('Heading', parent=styles['Heading2'], fontSize=14, spaceAfter=10, textColor=colors.HexColor('#2c5282'))
    normal_style = ParagraphStyle('Normal', parent=styles['Normal'], fontSize=10, spaceAfter=6)
    cell_style = ParagraphStyle('Cell', parent=styles['Normal'], fontSize=9, leading=12)
    header_cell_style = ParagraphStyle('HeaderCell', parent=styles['Normal'], fontSize=10, textColor=colors.white, fontName='Helvetica-Bold')

    story = []

    story.append(Paragraph("ERW Job Scoring Report", title_style))
    story.append(Paragraph(f"Generated: {datetime.now().strftime('%B %d, %Y at %I:%M %p')}", normal_style))
    story.append(Spacer(1, 20))

    companies = [
        ('erw_retaining_walls', 'ERW Retaining Walls'),
        ('kaufman_concrete', 'Kaufman Concrete'),
        ('landtec_landscape', 'Landtec Landscape'),
        ('ratliff_hardscape', 'Ratliff Hardscape')
    ]

    for job in job_results_list:
        story.append(Paragraph(f"Job: {job['filename']}", heading_style))

        summary = job['summary']
        story.append(Paragraph(f"Sheets analyzed: {summary['total_sheets']} ({summary['sheets_with_scope']} with scope)", normal_style))

        scores = job['scores']
        story.append(Paragraph(f"<b>Package Score: {scores['package_score']}/5</b>", normal_style))
        story.append(Paragraph(f"{scores['overall_recommendation']}", normal_style))
        story.append(Spacer(1, 10))

        table_data = [[
            Paragraph('Company', header_cell_style),
            Paragraph('Score', header_cell_style),
            Paragraph('Reasoning', header_cell_style)
        ]]
        for key, name in companies:
            company_data = scores[key]
            reasoning = company_data['reasoning']
            table_data.append([
                Paragraph(name, cell_style),
                Paragraph(f"{company_data['score']}/5", cell_style),
                Paragraph(reasoning, cell_style)
            ])

        table = Table(table_data, colWidths=[1.5*inch, 0.6*inch, 5*inch])
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1a365d')),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('ALIGN', (1, 0), (1, -1), 'CENTER'),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 10),
            ('TOPPADDING', (0, 0), (-1, 0), 10),
            ('TOPPADDING', (0, 1), (-1, -1), 8),
            ('BOTTOMPADDING', (0, 1), (-1, -1), 8),
            ('LEFTPADDING', (0, 0), (-1, -1), 6),
            ('RIGHTPADDING', (0, 0), (-1, -1), 6),
            ('BACKGROUND', (0, 1), (-1, -1), colors.HexColor('#f7fafc')),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e2e8f0')),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ]))
        story.append(table)
        story.append(Spacer(1, 30))

    doc.build(story)
    buffer.seek(0)
    return buffer


def lambda_handler(event, context):
    """
    AWS Lambda handler for job scoring.

    Files are downloaded from Google Drive using credentials and file IDs
    from environment variables (or event overrides).

    Args:
        event: Lambda event (optional overrides for file_ids, save_to_db, generate_pdf)
        context: Lambda context object

    Returns:
        dict: Scoring results
    """
    try:
        # Get file IDs from event or environment variable
        file_ids = event.get('file_ids') if event else None
        if not file_ids:
            file_ids_env = os.environ.get("GOOGLE_DRIVE_FILE_IDS", "")
            file_ids = [fid.strip() for fid in file_ids_env.split(",") if fid.strip()]

        if not file_ids:
            return {
                'success': False,
                'error': 'No file IDs provided. Set GOOGLE_DRIVE_FILE_IDS or pass file_ids in event.'
            }

        # Get options from event or environment
        save_to_db = (event.get('save_to_db', False) if event else False) or os.environ.get("SAVE_TO_DB", "").lower() == "true"
        generate_pdf_output = (event.get('generate_pdf', False) if event else False) or os.environ.get("GENERATE_PDF", "").lower() == "true"

        # Initialize Google Drive service
        drive_service = get_google_drive_service()

        scope_data_list = []
        filenames = []

        # Download and process each file
        for file_id in file_ids:
            print(f"Downloading file: {file_id}")
            file_content, filename = download_file_from_drive(drive_service, file_id)
            filenames.append(filename)

            # Process the Excel file
            df = pd.read_excel(file_content)
            df = normalize_columns(df)
            scope_data = prepare_scope_summary(df)
            scope_data_list.append(scope_data)
            print(f"Processed {filename}: {scope_data['total_sheets']} sheets")

        # Combine scope data if multiple files
        if len(scope_data_list) == 1:
            combined_scope = scope_data_list[0]
        else:
            combined_scope = combine_scope_data(scope_data_list)

        # Score the job
        print("Scoring job with Claude AI...")
        scores = score_job(combined_scope)

        # Generate job ID
        job_id = str(uuid.uuid4())[:8]

        # Build filename display
        if len(filenames) == 1:
            display_filename = filenames[0]
        else:
            display_filename = f"{len(filenames)} files: {', '.join(filenames)}"

        # Build summary
        summary = {
            'total_sheets': combined_scope['total_sheets'],
            'sheets_with_scope': combined_scope['sheets_with_scope'],
            'scope_counts': combined_scope['scope_indicator_counts'],
            'files_analyzed': filenames
        }

        # Save to database if requested
        if save_to_db:
            try:
                save_job_result(job_id, display_filename, summary, scores)
                print(f"Saved to database with job_id: {job_id}")
            except Exception as db_error:
                print(f"Database save failed: {db_error}")

        # Build response
        response = {
            'success': True,
            'job_id': job_id,
            'filename': display_filename,
            'files_analyzed': filenames,
            'analyzed_at': datetime.now().isoformat(),
            'summary': summary,
            'scores': scores
        }

        # Generate PDF if requested
        if generate_pdf_output:
            try:
                job_data = {
                    'filename': display_filename,
                    'summary': summary,
                    'scores': scores
                }
                pdf_buffer = generate_pdf([job_data])
                response['pdf_base64'] = base64.b64encode(pdf_buffer.getvalue()).decode('utf-8')
                print("PDF generated successfully")
            except Exception as pdf_error:
                response['pdf_error'] = str(pdf_error)

        return response

    except Exception as e:
        import traceback
        return {
            'success': False,
            'error': str(e),
            'traceback': traceback.format_exc()
        }


# For local testing
if __name__ == '__main__':
    import sys

    # Test with file IDs from command line or environment
    if len(sys.argv) > 1:
        test_file_ids = sys.argv[1:]
        test_event = {'file_ids': test_file_ids}
    else:
        test_event = {}

    result = lambda_handler(test_event, None)
    print(json.dumps(result, indent=2, default=str))
