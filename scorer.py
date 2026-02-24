"""
ECS Fargate task entrypoint for ERW Job Scorer.

Runs as a standalone container invoked by AWS Step Functions using the
WAIT_FOR_TASK (callback) pattern. Configuration is read entirely from
environment variables — static ones baked into the ECS task definition
and per-invocation ones injected via Step Functions containerOverrides.

Static environment variables (ECS task definition):
    ANTHROPIC_API_KEY:       Anthropic API key for Claude (required)
    GOOGLE_CREDENTIALS_JSON: Base64-encoded service account JSON (required)
    DATABASE_URL:            PostgreSQL connection string (optional)
    S3_BUCKET:               S3 bucket name for writing results JSON (optional)

Per-invocation environment variables (Step Functions containerOverrides):
    GOOGLE_DRIVE_FILE_IDS:   Comma-separated Google Drive file IDs (required)
    TASK_TOKEN:              Step Functions callback token (optional)
    GENERATE_PDF:            Set to "true" to include PDF in result (optional)
    SAVE_TO_DB:              Set to "true" to persist results to DB (optional)
"""

import anthropic
import boto3
import json
import os
import signal
import sys
import uuid
import base64
import time
from datetime import datetime
from io import BytesIO

import pandas as pd
import requests
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

# Optional database support
try:
    import psycopg2
    HAS_PSYCOPG2 = True
except ImportError:
    HAS_PSYCOPG2 = False

# Optional PDF support
try:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    HAS_REPORTLAB = True
except ImportError:
    HAS_REPORTLAB = False


# ---------------------------------------------------------------------------
# AWS clients
# ---------------------------------------------------------------------------

sfn_client = boto3.client('stepfunctions', region_name=os.environ.get('AWS_REGION', 'us-east-1'))
s3_client = boto3.client('s3', region_name=os.environ.get('AWS_REGION', 'us-east-1'))
ecs_client = boto3.client('ecs', region_name=os.environ.get('AWS_REGION', 'us-east-1'))

# Anthropic client
anthropic_client = anthropic.Anthropic(
    api_key=os.environ.get('ANTHROPIC_API_KEY') or os.environ.get('AI_INTEGRATIONS_ANTHROPIC_API_KEY'),
    base_url=os.environ.get('ANTHROPIC_BASE_URL') or os.environ.get('AI_INTEGRATIONS_ANTHROPIC_BASE_URL'),
)


# ---------------------------------------------------------------------------
# ECS task protection
# ---------------------------------------------------------------------------

def get_task_arn():
    """Fetch the current ECS task ARN from the metadata endpoint."""
    try:
        metadata_uri = os.environ.get('ECS_CONTAINER_METADATA_URI_V4')
        if not metadata_uri:
            return None
        response = requests.get(f'{metadata_uri}/task', timeout=5)
        data = response.json()
        return data.get('TaskARN')
    except Exception as e:
        print(f"Could not fetch task ARN: {e}")
        return None


def enable_task_protection(task_arn):
    """Enable ECS task scale-in protection to prevent premature termination."""
    if not task_arn:
        return
    try:
        cluster = task_arn.split(':task/')[1].split('/')[0] if '/task/' in task_arn else None
        if not cluster:
            # TaskARN format: arn:aws:ecs:region:account:task/cluster/taskid
            parts = task_arn.split('/')
            cluster = parts[-2] if len(parts) >= 3 else None
        if cluster:
            ecs_client.update_task_protection(
                cluster=cluster,
                tasks=[task_arn],
                protectionEnabled=True,
                expiresInMinutes=120
            )
            print(f"ECS task protection enabled (cluster={cluster})")
    except Exception as e:
        print(f"Could not enable task protection: {e}")


def disable_task_protection(task_arn):
    """Disable ECS task scale-in protection."""
    if not task_arn:
        return
    try:
        parts = task_arn.split('/')
        cluster = parts[-2] if len(parts) >= 3 else None
        if cluster:
            ecs_client.update_task_protection(
                cluster=cluster,
                tasks=[task_arn],
                protectionEnabled=False
            )
            print("ECS task protection disabled")
    except Exception as e:
        print(f"Could not disable task protection: {e}")


# ---------------------------------------------------------------------------
# Step Functions callbacks
# ---------------------------------------------------------------------------

def send_task_success(task_token, result):
    """Send SendTaskSuccess to Step Functions."""
    if not task_token:
        print("No TASK_TOKEN set — skipping SendTaskSuccess")
        return
    sfn_client.send_task_success(
        taskToken=task_token,
        output=json.dumps(result, default=str)
    )
    print("SendTaskSuccess sent")


def send_task_failure(task_token, error, cause):
    """Send SendTaskFailure to Step Functions."""
    if not task_token:
        print(f"No TASK_TOKEN set — skipping SendTaskFailure (error={error})")
        return
    sfn_client.send_task_failure(
        taskToken=task_token,
        error=error,
        cause=str(cause)[:256]
    )
    print(f"SendTaskFailure sent: {error}")


# ---------------------------------------------------------------------------
# S3 results
# ---------------------------------------------------------------------------

def write_results_to_s3(job_id, result):
    """Write full result JSON to S3. Returns the S3 key, or None if not configured."""
    bucket = os.environ.get('S3_BUCKET')
    if not bucket:
        return None
    key = f'results/{job_id}.json'
    s3_client.put_object(
        Bucket=bucket,
        Key=key,
        Body=json.dumps(result, indent=2, default=str),
        ContentType='application/json'
    )
    print(f"Results written to s3://{bucket}/{key}")
    return key


# ---------------------------------------------------------------------------
# Google Drive
# ---------------------------------------------------------------------------

def get_google_drive_service():
    """Initialize Google Drive API service using credentials from environment."""
    credentials_json = os.environ.get('GOOGLE_CREDENTIALS_JSON')
    if not credentials_json:
        raise RuntimeError("GOOGLE_CREDENTIALS_JSON environment variable not set")

    try:
        credentials_data = json.loads(base64.b64decode(credentials_json))
    except Exception:
        credentials_data = json.loads(credentials_json)

    credentials = service_account.Credentials.from_service_account_info(
        credentials_data,
        scopes=['https://www.googleapis.com/auth/drive.readonly']
    )
    return build('drive', 'v3', credentials=credentials)


def download_file_from_drive(service, file_id):
    """Download a file from Google Drive. Returns (BytesIO, filename)."""
    file_metadata = service.files().get(fileId=file_id, fields='name, mimeType').execute()
    filename = file_metadata.get('name', f'{file_id}.xlsx')

    request = service.files().get_media(fileId=file_id)
    file_content = BytesIO()
    downloader = MediaIoBaseDownload(file_content, request)

    done = False
    while not done:
        _, done = downloader.next_chunk()

    file_content.seek(0)
    return file_content, filename


# ---------------------------------------------------------------------------
# Scope processing
# ---------------------------------------------------------------------------

def normalize_columns(df):
    """Normalize column names to standard format."""
    column_mapping = {
        'Page': 'pdf_page',
        'Sheet Number': 'sheet_number',
        'Title': 'title',
        'Scale': 'scale',
        'Scope Summary': 'scope_summary',
        'Density': 'density',
        'Est. Takeoff Time': 'estimated_takeoff_time',
    }
    df.columns = [column_mapping.get(col, col) for col in df.columns]
    return df


def prepare_scope_summary(df):
    """Extract scope summary from a dataframe."""
    scope_columns = [
        'Aggregates / gravel', 'Concrete flatwork', 'Fencing', 'Furnishings',
        'Irrigation', 'Pavers', 'Retaining walls', 'Softscape (landscape planting)',
        'Synthetic turf', 'Drainage', 'Lighting', 'BMP / Environmental / Bioswales',
    ]
    existing_scope_cols = [c for c in scope_columns if c in df.columns]

    scope_counts = {
        col: int(df[col].notna().sum())
        for col in existing_scope_cols
        if df[col].notna().sum() > 0
    }

    sheets_with_scope = (
        df[df[existing_scope_cols].notna().any(axis=1)]
        if existing_scope_cols
        else pd.DataFrame()
    )

    sheet_details = []
    for _, row in sheets_with_scope.iterrows():
        marked_items = [col for col in existing_scope_cols if pd.notna(row.get(col))]
        sheet_details.append({
            'sheet': f"Sheet {row.get('sheet_number', 'N/A')}: {row.get('title', 'N/A')}",
            'summary': row.get('scope_summary', ''),
            'density': row.get('density', ''),
            'marked_scope': marked_items,
        })

    return {
        'total_sheets': len(df),
        'sheets_with_scope': len(sheets_with_scope),
        'scope_indicator_counts': scope_counts,
        'sheet_details': sheet_details[:50],
    }


def combine_scope_data(scope_data_list):
    """Merge scope data from multiple files into a single summary."""
    combined = {
        'total_sheets': 0,
        'sheets_with_scope': 0,
        'scope_indicator_counts': {},
        'sheet_details': [],
    }
    for sd in scope_data_list:
        combined['total_sheets'] += sd['total_sheets']
        combined['sheets_with_scope'] += sd['sheets_with_scope']
        for indicator, count in sd['scope_indicator_counts'].items():
            combined['scope_indicator_counts'][indicator] = (
                combined['scope_indicator_counts'].get(indicator, 0) + count
            )
        combined['sheet_details'].extend(sd['sheet_details'])

    combined['sheet_details'] = combined['sheet_details'][:50]
    return combined


# ---------------------------------------------------------------------------
# AI scoring
# ---------------------------------------------------------------------------

def score_job(scope_data):
    """Score the job using Claude AI. Returns parsed JSON dict."""
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
        model='claude-sonnet-4-5',
        max_tokens=1024,
        messages=[{'role': 'user', 'content': prompt}]
    )

    response_text = message.content[0].text
    if '```json' in response_text:
        response_text = response_text.split('```json')[1].split('```')[0]
    elif '```' in response_text:
        response_text = response_text.split('```')[1].split('```')[0]

    return json.loads(response_text.strip())


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def get_db_connection():
    if not HAS_PSYCOPG2:
        raise RuntimeError("psycopg2 not installed")
    database_url = os.environ.get('DATABASE_URL')
    if not database_url:
        raise RuntimeError("DATABASE_URL not configured")
    return psycopg2.connect(database_url)


def save_job_result(job_id, filename, summary, scores):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        'INSERT INTO job_results (job_id, filename, analyzed_at, summary, scores) '
        'VALUES (%s, %s, %s, %s, %s)',
        (job_id, filename, datetime.now(), json.dumps(summary), json.dumps(scores))
    )
    conn.commit()
    cur.close()
    conn.close()


# ---------------------------------------------------------------------------
# PDF generation
# ---------------------------------------------------------------------------

def generate_pdf(job_results_list):
    if not HAS_REPORTLAB:
        raise RuntimeError("reportlab not installed")

    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter, topMargin=0.5 * inch, bottomMargin=0.5 * inch)
    styles = getSampleStyleSheet()

    title_style = ParagraphStyle('Title', parent=styles['Heading1'], fontSize=18, spaceAfter=20, textColor=colors.HexColor('#1a365d'))
    heading_style = ParagraphStyle('Heading', parent=styles['Heading2'], fontSize=14, spaceAfter=10, textColor=colors.HexColor('#2c5282'))
    normal_style = ParagraphStyle('Normal', parent=styles['Normal'], fontSize=10, spaceAfter=6)
    cell_style = ParagraphStyle('Cell', parent=styles['Normal'], fontSize=9, leading=12)
    header_cell_style = ParagraphStyle('HeaderCell', parent=styles['Normal'], fontSize=10, textColor=colors.white, fontName='Helvetica-Bold')

    story = [
        Paragraph("ERW Job Scoring Report", title_style),
        Paragraph(f"Generated: {datetime.now().strftime('%B %d, %Y at %I:%M %p')}", normal_style),
        Spacer(1, 20),
    ]

    companies = [
        ('erw_retaining_walls', 'ERW Retaining Walls'),
        ('kaufman_concrete', 'Kaufman Concrete'),
        ('landtec_landscape', 'Landtec Landscape'),
        ('ratliff_hardscape', 'Ratliff Hardscape'),
    ]

    for job in job_results_list:
        scores = job['scores']
        story.append(Paragraph(f"Job: {job['filename']}", heading_style))
        story.append(Paragraph(
            f"Sheets analyzed: {job['summary']['total_sheets']} ({job['summary']['sheets_with_scope']} with scope)",
            normal_style
        ))
        story.append(Paragraph(f"<b>Package Score: {scores['package_score']}/5</b>", normal_style))
        story.append(Paragraph(scores['overall_recommendation'], normal_style))
        story.append(Spacer(1, 10))

        table_data = [[
            Paragraph('Company', header_cell_style),
            Paragraph('Score', header_cell_style),
            Paragraph('Reasoning', header_cell_style),
        ]]
        for key, name in companies:
            company_data = scores[key]
            table_data.append([
                Paragraph(name, cell_style),
                Paragraph(f"{company_data['score']}/5", cell_style),
                Paragraph(company_data['reasoning'], cell_style),
            ])

        table = Table(table_data, colWidths=[1.5 * inch, 0.6 * inch, 5 * inch])
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


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    start_time = time.time()
    task_token = os.environ.get('TASK_TOKEN')
    task_arn = get_task_arn()

    # Enable ECS task protection so the task isn't stopped mid-run
    enable_task_protection(task_arn)

    # Register SIGTERM handler so we report failure to Step Functions if ECS
    # stops the container unexpectedly
    def sigterm_handler(signum, frame):
        print("SIGTERM received — reporting failure to Step Functions")
        disable_task_protection(task_arn)
        send_task_failure(task_token, 'TaskTerminated', 'ECS terminated the container via SIGTERM')
        sys.exit(1)

    signal.signal(signal.SIGTERM, sigterm_handler)

    try:
        # Read per-invocation config from environment
        file_ids_env = os.environ.get('GOOGLE_DRIVE_FILE_IDS', '')
        file_ids = [fid.strip() for fid in file_ids_env.split(',') if fid.strip()]

        if not file_ids:
            raise ValueError("No file IDs provided. Set GOOGLE_DRIVE_FILE_IDS.")

        save_to_db = os.environ.get('SAVE_TO_DB', '').lower() == 'true'
        generate_pdf_output = os.environ.get('GENERATE_PDF', '').lower() == 'true'

        # Download and process files
        drive_service = get_google_drive_service()
        scope_data_list = []
        filenames = []

        for file_id in file_ids:
            print(f"Downloading file: {file_id}")
            file_content, filename = download_file_from_drive(drive_service, file_id)
            filenames.append(filename)

            df = pd.read_excel(file_content)
            df = normalize_columns(df)
            scope_data = prepare_scope_summary(df)
            scope_data_list.append(scope_data)
            print(f"Processed {filename}: {scope_data['total_sheets']} sheets ({scope_data['sheets_with_scope']} with scope)")

        combined_scope = scope_data_list[0] if len(scope_data_list) == 1 else combine_scope_data(scope_data_list)

        # Score
        print("Scoring job with Claude AI...")
        scores = score_job(combined_scope)

        job_id = str(uuid.uuid4())[:8]
        display_filename = filenames[0] if len(filenames) == 1 else f"{len(filenames)} files: {', '.join(filenames)}"

        summary = {
            'total_sheets': combined_scope['total_sheets'],
            'sheets_with_scope': combined_scope['sheets_with_scope'],
            'scope_counts': combined_scope['scope_indicator_counts'],
            'files_analyzed': filenames,
        }

        # Persist to database if requested
        if save_to_db:
            try:
                save_job_result(job_id, display_filename, summary, scores)
                print(f"Saved to database: job_id={job_id}")
            except Exception as db_error:
                print(f"Database save failed (non-fatal): {db_error}")

        # Build result payload
        result = {
            'status': 'completed',
            'job_id': job_id,
            'filename': display_filename,
            'files_analyzed': filenames,
            'analyzed_at': datetime.now().isoformat(),
            'summary': summary,
            'scores': scores,
            'processing_time_seconds': round(time.time() - start_time, 1),
        }

        # Write full result to S3 if configured
        s3_key = write_results_to_s3(job_id, result)
        if s3_key:
            result['s3_key'] = s3_key
            result['s3_bucket'] = os.environ.get('S3_BUCKET')

        # Generate PDF if requested (included inline as base64)
        if generate_pdf_output:
            try:
                pdf_buffer = generate_pdf([{'filename': display_filename, 'summary': summary, 'scores': scores}])
                result['pdf_base64'] = base64.b64encode(pdf_buffer.getvalue()).decode('utf-8')
                print("PDF generated")
            except Exception as pdf_error:
                print(f"PDF generation failed (non-fatal): {pdf_error}")
                result['pdf_error'] = str(pdf_error)

        print(f"Scoring complete in {result['processing_time_seconds']}s — package_score={scores['package_score']}")

        send_task_success(task_token, result)

    except Exception as e:
        import traceback
        cause = traceback.format_exc()
        print(f"ERROR: {e}\n{cause}")
        send_task_failure(task_token, type(e).__name__, cause)
        sys.exit(1)

    finally:
        disable_task_protection(task_arn)


if __name__ == '__main__':
    main()
