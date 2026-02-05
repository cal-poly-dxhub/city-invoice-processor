"""CLI for invoice reconciliation."""

import hashlib
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, Tuple
import typer
from invoice_recon.bedrock_entities import extract_entities
from invoice_recon.budget_items import discover_pdfs_in_dir, get_budget_item_slug
from invoice_recon.config import Config
from invoice_recon.csv_parser import parse_csv
from invoice_recon.index_store import IndexStore
from invoice_recon.matching import (
    generate_candidates_for_line_item,
    select_default_evidence,
)
from invoice_recon.models import (
    CandidateEvidenceSet,
    DocumentRef,
    LineItem,
    PageRecord,
    SelectedEvidence,
)
from invoice_recon.navigation_groups import build_navigation_groups
from invoice_recon.output_contract import (
    apply_user_edits,
    load_user_edits,
    write_reconciliation_output,
)
from invoice_recon.pdf_extract import (
    compute_file_sha256,
    extract_pdf_pages,
    get_pdf_page_count,
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = typer.Typer()


def index_document(
    pdf_info: Dict[str, str],
    index_store: IndexStore,
) -> Tuple[DocumentRef, List[PageRecord]]:
    """
    Index a single PDF document (text extraction + entity extraction).

    Args:
        pdf_info: Dict with "path" and "budget_item"
        index_store: IndexStore instance

    Returns:
        Tuple of (DocumentRef, List[PageRecord])
    """
    pdf_path = Path(pdf_info["path"])
    budget_item = pdf_info["budget_item"]
    doc_id = get_budget_item_slug(budget_item)

    logger.info(f"Indexing document: {pdf_path.name} ({budget_item})")

    # Compute file hash
    file_sha256 = compute_file_sha256(pdf_path)

    # Check if document needs re-extraction
    if not index_store.should_reextract_document(doc_id, file_sha256):
        logger.info(f"Document {doc_id} unchanged, using cache")
        doc_ref = index_store.get_document(doc_id)
        pages = index_store.get_all_pages_for_document(doc_id)
        return (doc_ref, pages)

    # Extract pages
    logger.info(f"Extracting text from {pdf_path.name}")
    page_count = get_pdf_page_count(pdf_path)
    extracted_pages = extract_pdf_pages(pdf_path)

    # Create DocumentRef
    doc_ref = DocumentRef(
        doc_id=doc_id,
        budget_item=budget_item,
        path=str(pdf_path),
        file_sha256=file_sha256,
        page_count=page_count,
    )
    index_store.upsert_document(doc_ref)

    # Process each page
    pages = []
    for page_number, text, text_source, word_boxes, tables in extracted_pages:
        # Compute text hash
        text_sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()

        # Check if entities need re-extraction
        if not index_store.should_reextract_entities(doc_id, page_number, text_sha256):
            logger.debug(f"Page {doc_id}:{page_number} unchanged, using cached entities")
            page_record = index_store.get_page(doc_id, page_number)
            if page_record:
                pages.append(page_record)
                continue

        # Extract entities with table context
        logger.info(f"Extracting entities from {doc_id} page {page_number} ({text_source})")
        entities = extract_entities(
            text,
            budget_item,
            page_number,
            page_tables=tables,
            page_doc_id=doc_id
        )

        # Store in index
        index_store.upsert_page(doc_id, page_number, text_source, text, entities, word_boxes, tables)

        # Create PageRecord
        page_record = PageRecord(
            doc_id=doc_id,
            page_number=page_number,
            text_source=text_source,
            text=text,
            entities=entities,
            words=word_boxes,
        )
        pages.append(page_record)

    logger.info(
        f"Completed indexing {doc_id}: {len(pages)} pages "
        f"({sum(1 for _, _, src, _, _ in extracted_pages if src == 'textract')} via Textract)"
    )

    return (doc_ref, pages)


@app.command()
def run(
    csv: Path = typer.Option(..., help="Path to invoice CSV file"),
    pdf_dir: Path = typer.Option(..., help="Directory containing PDF files"),
    job_id: str = typer.Option(..., help="Job ID for this run"),
):
    """
    Run the invoice reconciliation pipeline.

    Steps:
    1. Parse CSV
    2. Discover PDFs and map to budget items
    3. Index PDFs (extract text + entities)
    4. Build navigation groups
    5. Match line items to evidence candidates
    6. Apply user edits if present
    7. Write reconciliation.json
    """
    logger.info(f"Starting reconciliation job: {job_id}")
    logger.info(f"CSV: {csv}")
    logger.info(f"PDF directory: {pdf_dir}")

    # Step 1: Parse CSV
    logger.info("Step 1: Parsing CSV")
    line_items = parse_csv(csv)
    logger.info(f"Parsed {len(line_items)} line items")

    # Step 2: Discover PDFs
    logger.info("Step 2: Discovering PDFs")
    pdf_mappings = discover_pdfs_in_dir(pdf_dir)
    logger.info(f"Discovered {len(pdf_mappings)} PDFs")
    for mapping in pdf_mappings:
        logger.info(f"  - {Path(mapping['path']).name} -> {mapping['budget_item']}")

    if not pdf_mappings:
        logger.warning("No PDFs found in directory!")
        typer.echo("No PDFs found. Exiting.")
        raise typer.Exit(1)

    # Step 3: Index PDFs
    logger.info("Step 3: Indexing PDFs")
    artifacts_dir = Config.get_artifacts_dir(job_id)
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    index_db_path = artifacts_dir / "index.sqlite"
    index_store = IndexStore(index_db_path)

    documents: List[DocumentRef] = []
    pages_by_doc: Dict[str, List[PageRecord]] = {}

    # Use ThreadPoolExecutor for parallel processing
    with ThreadPoolExecutor(max_workers=Config.MAX_WORKERS) as executor:
        future_to_pdf = {
            executor.submit(index_document, pdf_info, index_store): pdf_info
            for pdf_info in pdf_mappings
        }

        for future in as_completed(future_to_pdf):
            pdf_info = future_to_pdf[future]
            try:
                doc_ref, pages = future.result()
                documents.append(doc_ref)
                pages_by_doc[doc_ref.doc_id] = pages
            except Exception as e:
                logger.error(f"Failed to index {pdf_info['path']}: {e}", exc_info=True)

    logger.info(f"Indexed {len(documents)} documents")

    # Step 4: Build navigation groups
    logger.info("Step 4: Building navigation groups")
    navigation_groups = build_navigation_groups(line_items)
    logger.info(f"Created {len(navigation_groups)} navigation groups")

    # Step 5: Match line items to candidates
    logger.info("Step 5: Matching line items to evidence candidates")
    candidates_map: Dict[str, List[CandidateEvidenceSet]] = {}
    selected_evidence_map: Dict[str, SelectedEvidence] = {}

    for line_item in line_items:
        # Get pages for the relevant budget item
        budget_item_slug = get_budget_item_slug(line_item.budget_item)
        pages = pages_by_doc.get(budget_item_slug, [])

        if not pages:
            logger.warning(
                f"No PDF found for budget item: {line_item.budget_item} "
                f"(row {line_item.row_index})"
            )
            candidates_map[line_item.row_id] = []
            selected_evidence_map[line_item.row_id] = SelectedEvidence(
                doc_id=None,
                page_numbers=[],
                selection_source="auto",
            )
            continue

        # Generate candidates
        candidates = generate_candidates_for_line_item(line_item, pages)

        # Filter out low-confidence candidates (score < MIN_CANDIDATE_SCORE)
        # This prevents showing unhelpful pages and results in more "No Match" items
        filtered_candidates = [
            c for c in candidates
            if c.score >= Config.MIN_CANDIDATE_SCORE
        ]

        if filtered_candidates != candidates:
            logger.debug(
                f"Row {line_item.row_index}: Filtered out {len(candidates) - len(filtered_candidates)} "
                f"low-confidence candidates (score < {Config.MIN_CANDIDATE_SCORE})"
            )

        candidates_map[line_item.row_id] = filtered_candidates

        # Select default evidence
        # If PDF exists but all candidates filtered out, preserve doc_id to distinguish from "no PDF"
        selected = select_default_evidence(filtered_candidates)
        if not filtered_candidates and pages:
            # PDF exists but no good candidates - set doc_id to show "No Match" not "No PDF"
            selected = SelectedEvidence(
                doc_id=budget_item_slug,
                page_numbers=[],
                selection_source="auto",
            )
        selected_evidence_map[line_item.row_id] = selected

    logger.info(f"Generated candidates for {len(line_items)} line items")

    # Step 6: Apply user edits if present
    logger.info("Step 6: Checking for user edits")
    user_edits = load_user_edits(job_id)
    if user_edits:
        logger.info(f"Applying {len(user_edits.overrides)} user edit overrides")
        apply_user_edits(selected_evidence_map, user_edits)
    else:
        logger.info("No user edits found")

    # Step 7: Write reconciliation output
    logger.info("Step 7: Writing reconciliation output")
    output_path = write_reconciliation_output(
        job_id=job_id,
        csv_path=csv,
        pdf_dir=pdf_dir,
        documents=documents,
        navigation_groups=navigation_groups,
        line_items=line_items,
        candidates_map=candidates_map,
        selected_evidence_map=selected_evidence_map,
        pdf_mappings=pdf_mappings,
    )

    logger.info(f"Reconciliation complete! Output: {output_path}")
    typer.echo(f"\nSuccess! Reconciliation output written to:\n  {output_path}")


@app.command()
def validate(
    job_id: str = typer.Option(..., help="Job ID to validate"),
):
    """
    Validate a reconciliation job output.

    Checks:
    - reconciliation.json exists
    - Referenced pages exist in documents
    """
    import json

    logger.info(f"Validating job: {job_id}")

    artifacts_dir = Config.get_artifacts_dir(job_id)
    recon_path = artifacts_dir / "reconciliation.json"

    if not recon_path.exists():
        logger.error(f"Reconciliation file not found: {recon_path}")
        typer.echo(f"ERROR: {recon_path} not found")
        raise typer.Exit(1)

    # Load reconciliation
    with open(recon_path) as f:
        recon_data = json.load(f)

    # Build document page count map
    doc_page_counts = {
        doc["doc_id"]: doc["page_count"]
        for doc in recon_data.get("documents", [])
    }

    # Validate line items
    errors = []
    for line_item in recon_data.get("line_items", []):
        row_id = line_item["row_id"]
        selected = line_item.get("selected_evidence", {})
        doc_id = selected.get("doc_id")
        page_numbers = selected.get("page_numbers", [])

        if doc_id and page_numbers:
            max_pages = doc_page_counts.get(doc_id, 0)
            for page_num in page_numbers:
                if page_num < 1 or page_num > max_pages:
                    errors.append(
                        f"Row {row_id}: Invalid page {page_num} for doc {doc_id} "
                        f"(max: {max_pages})"
                    )

    if errors:
        logger.error(f"Validation failed with {len(errors)} errors:")
        for error in errors:
            logger.error(f"  - {error}")
        typer.echo(f"\nValidation FAILED with {len(errors)} errors.")
        raise typer.Exit(1)

    logger.info("Validation passed")
    typer.echo("\nValidation PASSED!")


def main():
    """Main entry point."""
    app()


if __name__ == "__main__":
    main()
