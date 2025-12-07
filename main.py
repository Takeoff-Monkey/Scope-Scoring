import anthropic
import pandas as pd
from flask import Flask, request, render_template, jsonify, Response
import os
import json
import uuid
from datetime import datetime
import psycopg2
from psycopg2.extras import RealDictCursor
from io import BytesIO
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle

app = Flask(__name__)

client = anthropic.Anthropic(
    api_key=os.environ.get("AI_INTEGRATIONS_ANTHROPIC_API_KEY"),
    base_url=os.environ.get("AI_INTEGRATIONS_ANTHROPIC_BASE_URL")
)

def get_db_connection():
    return psycopg2.connect(os.environ.get("DATABASE_URL"))

def init_db():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('''
        CREATE TABLE IF NOT EXISTS job_results (
            job_id VARCHAR(8) PRIMARY KEY,
            filename VARCHAR(255),
            analyzed_at TIMESTAMP,
            summary JSONB,
            scores JSONB
        )
    ''')
    conn.commit()
    cur.close()
    conn.close()

def save_job_result(job_id, filename, summary, scores):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('''
        INSERT INTO job_results (job_id, filename, analyzed_at, summary, scores)
        VALUES (%s, %s, %s, %s, %s)
    ''', (job_id, filename, datetime.now(), json.dumps(summary), json.dumps(scores)))
    conn.commit()
    cur.close()
    conn.close()

def get_job_result(job_id):
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute('SELECT * FROM job_results WHERE job_id = %s', (job_id,))
    result = cur.fetchone()
    cur.close()
    conn.close()
    return result

init_db()

def normalize_columns(df):
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

def score_job(scope_data):
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

    message = client.messages.create(
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

@app.route('/')
def index():
    return render_template('index.html')

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

@app.route('/analyze', methods=['POST'])
def analyze():
    files = request.files.getlist('file')
    
    if not files or all(f.filename == '' for f in files):
        return jsonify({'error': 'No files uploaded'}), 400
    
    valid_files = [f for f in files if f.filename != '']
    
    if not valid_files:
        return jsonify({'error': 'No valid files selected'}), 400
    
    try:
        scope_data_list = []
        filenames = []
        
        for file in valid_files:
            df = pd.read_excel(file)
            df = normalize_columns(df)
            scope_data = prepare_scope_summary(df)
            scope_data_list.append(scope_data)
            filenames.append(file.filename)
        
        if len(scope_data_list) == 1:
            combined_scope = scope_data_list[0]
        else:
            combined_scope = combine_scope_data(scope_data_list)
        
        scores = score_job(combined_scope)
        
        job_id = str(uuid.uuid4())[:8]
        
        if len(filenames) == 1:
            display_filename = filenames[0]
        else:
            display_filename = f"{len(filenames)} files: {', '.join(filenames)}"
        
        summary = {
            'total_sheets': combined_scope['total_sheets'],
            'sheets_with_scope': combined_scope['sheets_with_scope'],
            'scope_counts': combined_scope['scope_indicator_counts'],
            'files_analyzed': filenames
        }
        
        save_job_result(job_id, display_filename, summary, scores)
        
        return jsonify({
            'success': True,
            'job_id': job_id,
            'filename': display_filename,
            'files_analyzed': filenames,
            'analyzed_at': datetime.now().isoformat(),
            'summary': summary,
            'scores': scores
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/results/<job_id>')
def get_results(job_id):
    result = get_job_result(job_id)
    if not result:
        return jsonify({'error': 'Job not found'}), 404
    return jsonify({
        'success': True,
        'job_id': result['job_id'],
        'filename': result['filename'],
        'analyzed_at': result['analyzed_at'].isoformat() if result['analyzed_at'] else None,
        'summary': result['summary'],
        'scores': result['scores']
    })

def generate_pdf(job_results_list):
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

def sanitize_filename(filename):
    """Remove or replace characters that are problematic in filenames."""
    import re
    safe_name = re.sub(r'[<>:"/\\|?*,]', '_', filename)
    safe_name = safe_name.replace('.xlsx', '').replace('.xls', '')
    safe_name = re.sub(r'_+', '_', safe_name)
    safe_name = safe_name.strip('_')
    return safe_name[:100]

@app.route('/export-pdf/<job_id>')
def export_single_pdf(job_id):
    result = get_job_result(job_id)
    if not result:
        return jsonify({'error': 'Job not found'}), 404
    
    job_data = {
        'filename': result['filename'],
        'summary': result['summary'],
        'scores': result['scores']
    }
    
    pdf_buffer = generate_pdf([job_data])
    
    safe_filename = sanitize_filename(result['filename'])
    
    return Response(
        pdf_buffer.getvalue(),
        mimetype='application/pdf',
        headers={'Content-Disposition': f'attachment; filename="ERW_Score_{safe_filename}.pdf"'}
    )

@app.route('/export-pdf-batch')
def export_batch_pdf():
    job_ids = request.args.get('job_ids', '').split(',')
    job_ids = [jid.strip() for jid in job_ids if jid.strip()]
    
    if not job_ids:
        return jsonify({'error': 'No job IDs provided'}), 400
    
    job_results_list = []
    for job_id in job_ids:
        result = get_job_result(job_id)
        if result:
            job_results_list.append({
                'filename': result['filename'],
                'summary': result['summary'],
                'scores': result['scores']
            })
    
    if not job_results_list:
        return jsonify({'error': 'No jobs found'}), 404
    
    pdf_buffer = generate_pdf(job_results_list)
    
    return Response(
        pdf_buffer.getvalue(),
        mimetype='application/pdf',
        headers={'Content-Disposition': f'attachment; filename=ERW_Batch_Scores_{datetime.now().strftime("%Y%m%d")}.pdf'}
    )

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
