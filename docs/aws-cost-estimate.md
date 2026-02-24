# AWS Monthly Cost Estimate

Last updated: 2026-02-24

## Assumptions

- **100 invoice processing jobs/month**
- **~100 PDF pages per job**
- **Mostly digital PDFs**
- **5-10 users** accessing the review UI
- **Region:** us-west-2

## Cost Summary

### With Haiku 4.5

| Service                                           | Monthly Cost    | % of Total |
| ------------------------------------------------- | --------------- | ---------- |
| Bedrock (Haiku 4.5)                               | ~$36.00         | 32%        |
| Textract                                          | ~$65.00         | 57%        |
| Lambda                                            | ~$5.35          | 5%         |
| CloudFront                                        | ~$4.50          | 4%         |
| CloudWatch                                        | ~$1.50          | 1%         |
| S3, DynamoDB, Step Functions, API GW, EventBridge | ~$0.35          | <1%        |
| **Total**                                         | **~$113/month** |            |

## Service-by-Service Breakdown

### Amazon Bedrock (Entity Extraction)

Every page gets entity extraction via Bedrock. 100 jobs x 100 pages = 10,000 calls/month.

| Input               | Value             |
| ------------------- | ----------------- |
| Input tokens/month  | ~30,000,000 (30M) |
| Output tokens/month | ~3,000,000 (3M)   |

| Model                                                     | Input price     | Output price    | Monthly cost |
| --------------------------------------------------------- | --------------- | --------------- | ------------ |
| Haiku 4.5 (`us.anthropic.claude-haiku-4-5-20251001-v1:0`) | $0.80/1M tokens | $4.00/1M tokens | ~$36         |

Token estimates per page: ~3,000 input (system prompt + page text), ~300 output (structured JSON).

### Amazon Textract

Textract is used as fallback when PyMuPDF text extraction is insufficient (<40 chars or no geometry).

| Input            | Value                                        |
| ---------------- | -------------------------------------------- |
| API              | AnalyzeDocument (Tables + Forms)             |
| Pages/month      | ~1,000 (10% of 10,000 total)                 |
| Price            | $65 per 1,000 pages (Tables $15 + Forms $50) |
| **Monthly cost** | **~$65**                                     |

### AWS Lambda

8 functions with varying memory and duration:

| Function               | Memory   | Avg Duration | Invocations/month |
| ---------------------- | -------- | ------------ | ----------------- |
| ParseCSV               | 512 MB   | 3s           | 100               |
| DiscoverPDFs           | 512 MB   | 3s           | 100               |
| ResolvePage            | 512 MB   | 4s           | 10,000            |
| ExtractEntities        | 512 MB   | 10s          | 10,000            |
| IndexDocument (Docker) | 2,048 MB | 240s         | 500               |
| AssembleAndMatch       | 2,048 MB | 45s          | 100               |
| UploadStart            | 128 MB   | 1s           | 100               |
| JobStatus              | 128 MB   | 1s           | 500               |

| Input                            | Value      |
| -------------------------------- | ---------- |
| Total requests/month             | ~21,400    |
| Total compute (GB-seconds)/month | ~320,000   |
| **Monthly cost**                 | **~$5.35** |

### Amazon CloudFront

| Input                   | Value      |
| ----------------------- | ---------- |
| Data transfer out/month | ~50 GB     |
| HTTPS requests/month    | ~100,000   |
| **Monthly cost**        | **~$4.50** |

### Amazon S3

| Input              | Value      |
| ------------------ | ---------- |
| Storage (average)  | ~5 GB      |
| PUT requests/month | ~15,000    |
| GET requests/month | ~25,000    |
| **Monthly cost**   | **~$0.25** |

### Amazon DynamoDB

| Input                     | Value                       |
| ------------------------- | --------------------------- |
| Billing mode              | On-Demand (PAY_PER_REQUEST) |
| Write request units/month | ~20,000                     |
| Read request units/month  | ~30,000                     |
| Storage                   | <1 GB (30-day TTL)          |
| **Monthly cost**          | **~$0.05**                  |

### AWS Step Functions

| Input                   | Value      |
| ----------------------- | ---------- |
| Workflow type           | Standard   |
| State transitions/month | ~1,500     |
| **Monthly cost**        | **~$0.04** |

### Amazon API Gateway

| Input            | Value          |
| ---------------- | -------------- |
| API type         | REST API       |
| Requests/month   | ~1,000         |
| **Monthly cost** | **negligible** |

### Amazon CloudWatch

| Input               | Value                    |
| ------------------- | ------------------------ |
| Log ingestion/month | ~2 GB                    |
| Log storage         | ~2 GB (14-day retention) |
| **Monthly cost**    | **~$1.50**               |

### Amazon EventBridge

| Input               | Value          |
| ------------------- | -------------- |
| Custom events/month | ~100           |
| **Monthly cost**    | **negligible** |

## Key Cost Sensitivities

- **Textract rate** is the biggest variable. If PDFs are nearly all clean digital, Textract drops to ~5% of pages (saving ~$30). More scanned docs could double the cost.
- **Bedrock tokens** scale linearly with page count. Dense-text pages (5,000+ tokens) increase input costs proportionally.
- **DynamoDB caching** saves money on re-runs — reprocessing the same PDFs skips both Textract and Bedrock calls entirely.
- **TABLE_DETECTION_ENABLED=false** (default) avoids an extra Nova Lite vision call per page. Enabling it adds ~$5-10/month but can reduce Textract usage for table-heavy documents.
