# AI-Powered Invoice Reconciliation for the City of San Francisco

AWS-powered invoice reconciliation system that automates the matching of invoice line items to supporting PDF documentation. Uses AI to extract entities from invoices and evidence pages, then scores and ranks potential matches so reviewers can quickly validate budget expenditures.

## Core Features

### 1. Automated PDF-to-Invoice Matching
- Upload a CSV invoice and supporting PDFs (paystubs, timecards, receipts, utility bills, etc.)
- AI extracts names, amounts, dates, organizations, and document types from each page
- Matching engine scores and ranks evidence candidates for every line item
- Supports 12 standard budget categories: Salary, Fringe, Contractual Service, Equipment, Insurance, Travel and Conferences, Space Rental/Occupancy Costs, Telecommunications, Utilities, Supplies, Other, Indirect Costs

### 2. Intelligent Text & Table Extraction
- Three-tier extraction strategy optimized for cost and accuracy:
  - **PyMuPDF** (free, fast) for digital PDFs with selectable text
  - **PyMuPDF Table Extraction** (free, fast) for digital spreadsheets and structured tables
  - **AWS Textract** (paid, OCR) as fallback for scanned documents and complex layouts
- Automatic quality validation determines when to escalate to a more capable extractor

### 3. Interactive Review UI
- Split-pane interface with line items on the left and PDF viewer on the right
- Confidence slider for dynamic real-time filtering of match quality
- PDF page rendering with text highlighting on matched evidence
- Page rotation, zoom controls, and multi-file document support
- Manual page selection and override for reviewer corrections
- Sub-item extraction for detailed line-item breakdowns
- Persistent user edits and annotations

### 4. Serverless Processing Pipeline
- Upload files through the web UI to trigger fully automated processing
- AWS Step Functions orchestrates: CSV parsing, PDF discovery, text extraction, entity extraction, and matching
- Job history with status tracking, timestamps, and inline renaming
- Results viewable immediately in the review interface

# Collaboration
Thanks for your interest in our solution. Having specific examples of replication and cloning allows us to continue to grow and scale our work. If you clone or download this repository, kindly shoot us a quick email to let us know you are interested in this work!

[wwps-cic@amazon.com]

# Disclaimers

**Customers are responsible for making their own independent assessment of the information in this document.**

**This document:**

(a) is for informational purposes only,

(b) represents current AWS product offerings and practices, which are subject to change without notice, and

(c) does not create any commitments or assurances from AWS and its affiliates, suppliers or licensors. AWS products or services are provided "as is" without warranties, representations, or conditions of any kind, whether express or implied. The responsibilities and liabilities of AWS to its customers are controlled by AWS agreements, and this document is not part of, nor does it modify, any agreement between AWS and its customers.

(d) is not to be considered a recommendation or viewpoint of AWS

**Additionally, all prototype code and associated assets should be considered:**

(a) as-is and without warranties

(b) not suitable for production environments

(d) to include shortcuts in order to support rapid prototyping such as, but not limitted to, relaxed authentication and authorization and a lack of strict adherence to security best practices

**All work produced is open source. More information can be found in the GitHub repo.**

## Setup

### Prerequisites
- AWS Account with appropriate permissions
- Python 3.13+
- Node.js 18+ and npm
- AWS CDK CLI (`npm install -g aws-cdk`)
- AWS CLI configured with credentials

### Installation

1. **Clone the repository**
   ```bash
   git clone https://github.com/cal-poly-dxhub/city-invoice-processor
   cd city-invoice-processor
   ```

2. **Install backend dependencies**
   ```bash
   cd backend
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   cd ..
   ```

3. **Install frontend dependencies** (only needed for local development)
   ```bash
   cd frontend
   npm install
   cd ..
   ```

4. **Deploy AWS infrastructure**
   ```bash
   scripts/deploy.sh
   ```
   This script creates a Python virtual environment for CDK, bootstraps your AWS account, builds the frontend, and deploys all three CloudFormation stacks. Look for the CloudFront URL in the deployment output.

## Usage

### Uploading an Invoice for Processing

1. Open the application at the CloudFront URL from deployment output
2. On the **Upload** page, drag and drop your invoice CSV and supporting PDF files
3. PDFs are automatically classified into budget categories based on filename
4. Use the assignment modal to manually map any unclassified files
5. Submit to trigger the processing pipeline

### Reviewing Results

1. Navigate to the **Job History** page to see all processing jobs
2. Click a completed job to open the **Review** page
3. Browse line items on the left panel; click one to see its matched evidence
4. Use the **confidence slider** to adjust the minimum match threshold (default 50%)
5. View highlighted text on the PDF page showing why a match was scored
6. Override matches by manually selecting different pages
7. Extract sub-items for detailed breakdowns of supporting documents

### Running Tests

```bash
# Backend
cd backend && pytest tests/

# Frontend
cd frontend && npm test
```

## Configuration

### Backend Environment Variables

| Variable | Default | Description |
|---|---|---|
| `AWS_REGION` | `us-west-2` | AWS region for service calls |
| `BEDROCK_MODEL_ID` | `us.anthropic.claude-haiku-4-5-20251001-v1:0` | Claude model for entity extraction |
| `TEXT_MIN_CHARS` | `40` | Minimum chars for PyMuPDF to be considered sufficient |
| `TEXTRACT_MODE` | `auto` | Text extraction mode: `auto`, `always`, or `never` |
| `TABLE_DETECTION_ENABLED` | `false` | Use Bedrock vision to pre-detect table pages |
| `MIN_CANDIDATE_SCORE` | `0.1` | Backend floor for garbage match filtering |
| `MAX_WORKERS` | `3` | Parallel processing workers |

## Architecture

### AWS Services Used
- **Amazon Bedrock (Claude Haiku 4.5)**: AI entity extraction from page text (names, amounts, dates, organizations)
- **Amazon Textract**: OCR fallback for scanned PDFs and complex table extraction
- **AWS Step Functions**: Orchestrates the multi-step processing pipeline
- **AWS Lambda**: Serverless compute for each pipeline stage and API handlers
- **Amazon S3**: Storage for uploaded files, processing artifacts, and frontend assets
- **Amazon DynamoDB**: Extraction cache for incremental processing
- **Amazon API Gateway**: REST API endpoints for the frontend
- **Amazon EventBridge**: Triggers pipeline on file upload completion
- **Amazon CloudFront**: CDN for frontend delivery, proxies API requests

### System Components

```
Frontend (React + Vite)
    ↓
CloudFront
    ├── Static assets (S3)
    └── /api/* → API Gateway
                    ↓
              Lambda Functions
                    ↓
         Step Functions Pipeline
              ├── Parse CSV
              ├── Discover PDFs
              ├── Index Documents (parallel)
              │     ├── PyMuPDF text extraction
              │     └── Textract fallback
              ├── Extract Entities (parallel, Bedrock)
              └── Assemble & Match
                    ↓
              S3 (reconciliation.json)
```

### CDK Stacks
- **StorageStack**: S3 data bucket and DynamoDB cache table
- **ProcessingStack**: Lambda functions, Step Functions state machine, API Gateway, EventBridge rule
- **FrontendStack**: S3 static hosting bucket, CloudFront distribution (auto-builds frontend during synthesis)

## Project Structure

```
city-invoice-processor/
├── backend/                # Python reconciliation engine
│   ├── invoice_recon/      # Core modules (cli, matching, extraction, etc.)
│   └── requirements.txt
├── frontend/               # Production React UI (deployed via CDK)
│   └── src/
│       ├── pages/          # Upload, Review, JobHistory
│       ├── components/     # PDFViewer, confidence slider, etc.
│       └── services/       # API client
├── infra/                  # AWS CDK infrastructure (Python)
│   ├── stacks/             # StorageStack, ProcessingStack, FrontendStack
│   ├── lambda/             # Lambda handler entry points
│   └── app.py              # CDK app entry point
├── scripts/                # Deployment and utility scripts
```

## Support
For queries or issues:
- Darren Kraker, Sr Solutions Architect - dkraker@amazon.com
- Jonah Chan, Software Engineering Intern - jchan332@calpoly.edu
