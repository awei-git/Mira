"""Health data ingestion — consume Apple Health exports and checkup PDFs."""
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("health_ingest")


def ingest_apple_health(bridge_dir: Path, person_id: str, store) -> int:
    """Read apple_health_export.json from bridge, insert new metrics, return count.

    Deletes the export file after successful ingestion.
    """
    export_file = bridge_dir / "users" / person_id / "health" / "apple_health_export.json"
    if not export_file.exists():
        return 0

    try:
        data = json.loads(export_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        log.error("Failed to read health export for %s: %s", person_id, e)
        return 0

    metrics = data.get("metrics", [])
    if not metrics:
        export_file.unlink(missing_ok=True)
        return 0

    store.insert_metrics_batch(person_id, metrics, source="apple_health")
    log.info("Ingested %d Apple Health metrics for %s", len(metrics), person_id)

    # Delete processed file
    export_file.unlink(missing_ok=True)
    return len(metrics)


def ingest_all_users(bridge_dir: Path, store) -> int:
    """Scan all user directories for health exports and ingest them."""
    users_dir = bridge_dir / "users"
    if not users_dir.exists():
        return 0

    total = 0
    for user_dir in users_dir.iterdir():
        if user_dir.is_dir():
            count = ingest_apple_health(bridge_dir, user_dir.name, store)
            total += count
    return total


def parse_checkup_pdf(pdf_path: Path, person_id: str, store) -> dict:
    """Parse a checkup report PDF into structured data using local LLM.

    Returns the parsed JSON dict. Also stores results in the database.
    """
    from config import OMLX_DEFAULT_MODEL
    from sub_agent import _omlx_call

    # Extract text from PDF
    text = _extract_pdf_text(pdf_path)
    if not text:
        log.error("No text extracted from %s", pdf_path)
        return {}

    prompt = f"""Extract all test results from this medical checkup report.
For each test item, extract:
- name: test name (Chinese + English if available)
- value: numeric value
- unit: measurement unit
- ref_range: reference range (e.g., "3.5-5.5")
- flag: "normal", "high", "low", or "abnormal"

Also extract:
- report_date: the date of the checkup (YYYY-MM-DD format)
- patient_name: patient name if visible
- institution: hospital/clinic name

Return as JSON:
{{"report_date": "...", "patient_name": "...", "institution": "...", "items": [...]}}

Report text:
{text[:8000]}"""

    try:
        result = _omlx_call(OMLX_DEFAULT_MODEL, prompt, timeout=120)
        # Extract JSON
        text_result = result.strip()
        if "```" in text_result:
            text_result = text_result.split("```")[1].strip()
            if text_result.startswith("json"):
                text_result = text_result[4:].strip()
        parsed = json.loads(text_result)
    except (json.JSONDecodeError, Exception) as e:
        log.error("Failed to parse checkup PDF: %s", e)
        return {}

    # Store in database
    from datetime import date
    report_date_str = parsed.get("report_date", "")
    try:
        report_date = date.fromisoformat(report_date_str)
    except (ValueError, TypeError):
        report_date = date.today()

    summary_items = []
    for item in parsed.get("items", []):
        if item.get("flag") and item["flag"] != "normal":
            summary_items.append(f"{item['name']}: {item.get('value','?')}{item.get('unit','')} ({item['flag']})")

    summary = "异常指标: " + "; ".join(summary_items) if summary_items else "各项指标正常"

    store.insert_report(
        person_id=person_id,
        report_date=report_date,
        parsed_json=parsed,
        summary=summary,
        source_file=str(pdf_path),
    )

    # Also insert numeric metrics for trend tracking
    for item in parsed.get("items", []):
        try:
            value = float(item.get("value", ""))
            store.insert_metric(
                person_id, item["name"], value,
                unit=item.get("unit", ""),
                source="checkup",
            )
        except (ValueError, TypeError):
            continue

    log.info("Parsed checkup for %s: %d items, %d abnormal",
             person_id, len(parsed.get("items", [])), len(summary_items))
    return parsed


def parse_checkup_images(image_paths: list[Path], person_id: str, store) -> dict:
    """Parse checkup report images using local OCR + LLM.

    Extracts text via pytesseract, then parses into structured data.
    """
    if not image_paths:
        return {}

    # Try OCR on images
    combined_text = ""
    for img_path in image_paths[:10]:
        text = _extract_image_text(img_path)
        if text:
            combined_text += text + "\n---\n"

    if not combined_text.strip():
        log.warning("No text extracted from checkup images for %s", person_id)
        # Store the report record even without parsing
        from datetime import date
        store.insert_report(
            person_id=person_id,
            report_date=date.today(),
            parsed_json={"images": [p.name for p in image_paths]},
            summary="体检报告已上传，待解析",
            source_file=str(image_paths[0].parent),
        )
        return {"images": [p.name for p in image_paths]}

    # Parse extracted text into structured data
    from config import OMLX_DEFAULT_MODEL
    from sub_agent import _omlx_call

    prompt = f"""Extract all test results from this medical checkup report.
For each test item, extract:
- name: test name (Chinese + English if available)
- value: numeric value
- unit: measurement unit
- ref_range: reference range (e.g., "3.5-5.5")
- flag: "normal", "high", "low", or "abnormal"

Also extract:
- report_date: the date of the checkup (YYYY-MM-DD format)
- flagged_high: list of test names that are flagged high or abnormal

Return as JSON:
{{"report_date": "...", "flagged_high": [...], "items": [...]}}

Report text:
{combined_text[:8000]}"""

    try:
        result = _omlx_call(OMLX_DEFAULT_MODEL, prompt, timeout=120)
        text_result = result.strip()
        if "```" in text_result:
            text_result = text_result.split("```")[1].strip()
            if text_result.startswith("json"):
                text_result = text_result[4:].strip()
        parsed = json.loads(text_result)
    except (json.JSONDecodeError, Exception) as e:
        log.error("Failed to parse checkup images: %s", e)
        return {}

    # Store in database
    from datetime import date
    report_date_str = parsed.get("report_date", "")
    try:
        report_date = date.fromisoformat(report_date_str)
    except (ValueError, TypeError):
        report_date = date.today()

    summary_items = []
    for item in parsed.get("items", []):
        if item.get("flag") and item["flag"] != "normal":
            summary_items.append(
                f"{item['name']}: {item.get('value','?')}{item.get('unit','')} ({item['flag']})")

    summary = "异常指标: " + "; ".join(summary_items) if summary_items else "各项指标正常"

    store.insert_report(
        person_id=person_id,
        report_date=report_date,
        parsed_json=parsed,
        summary=summary,
        source_file=str(image_paths[0].parent),
    )

    for item in parsed.get("items", []):
        try:
            value = float(item.get("value", ""))
            store.insert_metric(
                person_id, item["name"], value,
                unit=item.get("unit", ""),
                source="checkup",
            )
        except (ValueError, TypeError):
            continue

    log.info("Parsed checkup images for %s: %d items, %d abnormal",
             person_id, len(parsed.get("items", [])), len(summary_items))
    return parsed


def _extract_image_text(image_path: Path) -> str:
    """Extract text from an image using OCR."""
    try:
        import pytesseract
        from PIL import Image
        img = Image.open(str(image_path))
        # Use Chinese + English for medical reports
        text = pytesseract.image_to_string(img, lang="chi_sim+eng")
        return text.strip()
    except ImportError:
        log.warning("pytesseract/PIL not available for OCR")
    except Exception as e:
        log.warning("OCR failed for %s: %s", image_path.name, e)
    return ""


def _extract_pdf_text(pdf_path: Path) -> str:
    """Extract text from a PDF file."""
    try:
        import pypdf
        reader = pypdf.PdfReader(str(pdf_path))
        text = ""
        for page in reader.pages:
            text += page.extract_text() or ""
        return text.strip()
    except ImportError:
        log.warning("pypdf not available, trying pdfplumber")
    try:
        import pdfplumber
        with pdfplumber.open(str(pdf_path)) as pdf:
            return "\n".join(page.extract_text() or "" for page in pdf.pages).strip()
    except ImportError:
        log.error("No PDF reader available (install pypdf or pdfplumber)")
        return ""
