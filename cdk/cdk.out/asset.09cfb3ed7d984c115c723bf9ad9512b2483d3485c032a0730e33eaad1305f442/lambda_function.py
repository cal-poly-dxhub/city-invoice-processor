import json
import os
from typing import Dict, List, Tuple
from urllib.parse import urlparse
from io import BytesIO

import boto3
import fitz  # PyMuPDF


# Clients are created outside the handler for connection reuse inside Lambda
s3_client = boto3.client(
    "s3", config=boto3.session.Config(retries={"max_attempts": 3}, read_timeout=300)
)
bedrock_runtime = boto3.client(
    "bedrock-runtime",
    config=boto3.session.Config(retries={"max_attempts": 3}, read_timeout=60),
)


DEFAULT_MODEL_ID = os.environ.get(
    "MODEL_ID", "anthropic.claude-3-5-sonnet-20241022-v2:0"
)
DEFAULT_STAGE1_SYSTEM_PROMPT = (
    "You are an entity resolver. Parse each page, dynamically inferring categories and ignoring "
    "header information. Differentiate between summaries and supporting documentation. Generate, "
    "but do not send, a JSON object with key-value pairs based on the categories you determined. "
    "Combine items with the same owner or name. Then, from each object in the summaries, find all "
    "related objects from supporting documentation and return a table of each entity and its "
    "associated pages."
)
DEFAULT_STAGE2_SYSTEM_PROMPT = (
    "For each entity in this JSON object, verify that the data from the supporting documentation "
    "pages matches the data on the summary page(s), keeping in mind that data from supporting "
    "documentation may need to be aggregated to match the full scope of the summary data."
    "If data from the summary pages is lacking supporting documentation, note that separately from mismatched data."
)
# DEFAULT_STAGE3_SYSTEM_PROMPT = (
#     "Generate a concise summary containing: 1) Summary of entities that were extracted, "
#     "2) Summary of verification results."
# )


def parse_s3_uri(uri: str) -> Tuple[str, str]:
    """
    Accepts s3:// URIs or virtual-hosted–style HTTPS links and returns (bucket, key).
    """
    parsed = urlparse(uri)

    if parsed.scheme == "s3":
        return parsed.netloc, parsed.path.lstrip("/")

    if parsed.scheme in ("http", "https") and ".s3" in parsed.netloc:
        bucket = parsed.netloc.split(".")[0]
        return bucket, parsed.path.lstrip("/")

    raise ValueError(f"Unsupported S3 URI: {uri}")


def fetch_s3_pages(uri: str) -> List[str]:
    bucket, key = parse_s3_uri(uri)
    print(f"Downloading from S3: {bucket}/{key}")

    # Use streaming read to avoid memory issues with large files
    obj = s3_client.get_object(Bucket=bucket, Key=key)
    content = b""
    total_size = obj.get('ContentLength', 0)
    downloaded = 0
    
    for chunk in obj["Body"].iter_chunks(chunk_size=1024 * 1024):  # 1MB chunks
        content += chunk
        downloaded += len(chunk)
        if total_size > 0:
            print(f"Downloaded {downloaded}/{total_size} bytes ({downloaded/total_size*100:.1f}%)")

    print("Download complete, processing PDF...")

    # Check if it's a PDF file
    if key.lower().endswith(".pdf"):
        doc = fitz.open(stream=content, filetype="pdf")

        # Try to decrypt with empty password if encrypted
        if doc.needs_pass:
            doc.authenticate("")

        pages = []
        for page_num in range(doc.page_count):
            page = doc[page_num]
            pages.append(page.get_text())

        doc.close()
        return pages
    else:
        return [content.decode("utf-8")]


def build_messages(
    document_text: str,
    user_request: str,
    few_shot_examples: List[Dict[str, str]],
    system_prompt: str,
) -> Tuple[str, List[Dict]]:
    """
    Builds a Bedrock Messages API payload with optional few-shot examples.
    Returns (system_prompt, messages) for Anthropic Claude format.
    few_shot_examples: list of {"user": "...", "assistant": "..."}.
    """
    messages: List[Dict] = []

    for example in few_shot_examples:
        messages.append(
            {"role": "user", "content": [{"type": "text", "text": example["user"]}]}
        )
        messages.append(
            {
                "role": "assistant",
                "content": [{"type": "text", "text": example["assistant"]}],
            }
        )

    messages.append(
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": f"Source document:\n{document_text}\n\nRequest:\n{user_request}",
                }
            ],
        }
    )

    return system_prompt, messages


def invoke_bedrock(
    model_id: str,
    system_prompt: str,
    messages: List[Dict],
    max_tokens: int = 1000,
    temperature: float = 0.0,
) -> Dict:
    body = json.dumps(
        {
            "anthropic_version": "bedrock-2023-05-31",
            "system": system_prompt,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
    )

    response = bedrock_runtime.invoke_model(
        modelId=model_id,
        contentType="application/json",
        accept="application/json",
        body=body,
    )

    return json.loads(response["body"].read())


def lambda_handler(event, context):
    """
    event:
      s3_uri: S3 link to the document (s3://bucket/key or https://bucket.s3.amazonaws.com/key)
      question: optional request text for stage 1 (defaults to stage 1 instructions)
      few_shot_examples: optional list of {"user": "...", "assistant": "..."} examples for both stages
      max_tokens: optional override for response length
      temperature: optional override for sampling temperature
    """
    s3_uri = event.get("s3_uri")
    if not s3_uri:
        raise ValueError("Missing required field: s3_uri")

    user_request = event.get(
        "question",
        "Apply the stage 1 instructions to produce the entity JSON.",
    )
    stage1_system_prompt = DEFAULT_STAGE1_SYSTEM_PROMPT
    stage2_system_prompt = DEFAULT_STAGE2_SYSTEM_PROMPT
    # stage3_system_prompt = DEFAULT_STAGE3_SYSTEM_PROMPT
    few_shot_examples = event.get("few_shot_examples") or []

    model_id = event.get("model_id", DEFAULT_MODEL_ID)
    max_tokens = int(event.get("max_tokens", 1000))
    temperature = float(event.get("temperature", 0.0))

    pages = fetch_s3_pages(s3_uri)

    # Process each page through stage 1
    all_stage1_outputs = []
    for i, page_text in enumerate(pages):
        print(f"Processing page {i+1} of {len(pages)}...")
        stage1_system, stage1_messages = build_messages(
            page_text,
            f"{user_request} (Page {i+1} of {len(pages)})",
            few_shot_examples,
            stage1_system_prompt,
        )

        stage1_response = invoke_bedrock(
            model_id=model_id,
            system_prompt=stage1_system,
            messages=stage1_messages,
            max_tokens=max_tokens,
            temperature=temperature,
        )

        all_stage1_outputs.append(stage1_response["content"][0]["text"])
        print(f"Completed page {i+1}")

    # Combine all stage 1 outputs
    stage1_output = "\n\n".join(all_stage1_outputs)
    document_text = "\n\n".join(pages)

    stage2_request = (
        "Use the extracted entities to perform the stage 2 verification instructions.\n\n"
        f"Extracted entity JSON:\n{stage1_output}"
    )
    stage2_system, stage2_messages = build_messages(
        document_text,
        stage2_request,
        few_shot_examples,
        stage2_system_prompt,
    )

    stage2_response = invoke_bedrock(
        model_id=model_id,
        system_prompt=stage2_system,
        messages=stage2_messages,
        max_tokens=max_tokens,
        temperature=temperature,
    )

    stage2_output = stage2_response["content"][0]["text"]

    # stage3_request = (
    #     "Generate a summary based on the extraction and verification results.\n\n"
    #     f"Extracted entities:\n{stage1_output}\n\nVerification results:\n{stage2_output}"
    # )
    #
    # # Stage 3 doesn't need document_text, just the previous outputs
    # stage3_messages = []
    # for example in few_shot_examples:
    #     stage3_messages.append({"role": "user", "content": [{"type": "text", "text": example["user"]}]})
    #     stage3_messages.append({"role": "assistant", "content": [{"type": "text", "text": example["assistant"]}]})
    #
    # stage3_messages.append({"role": "user", "content": [{"type": "text", "text": stage3_request}]})

    # stage3_response = invoke_bedrock(
    #     model_id=model_id,
    #     system_prompt=stage3_system_prompt,
    #     messages=stage3_messages,
    #     max_tokens=max_tokens,
    #     temperature=temperature,
    # )

    # stage3_output = stage3_response["content"][0]["text"]

    return {
        "statusCode": 200,
        "body": {
            "answer": stage2_output,
            "stage1_answer": stage1_output,
            "stage2_answer": stage2_output,
            "stage1_usage": {"total_pages": len(pages)},
            "stage2_usage": stage2_response.get("usage", {}),
            # "stage3_usage": stage3_response.get("usage", {}),
            "stage1_stop_reason": "completed_all_pages",
            "stage2_stop_reason": stage2_response.get("stop_reason"),
            # "stage3_stop_reason": stage3_response.get("stop_reason"),
            "model_id": model_id,
        },
    }
