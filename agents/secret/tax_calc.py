"""Local tax calculation — PDF extraction + deterministic computation.

Pipeline:
1. Ollama extracts numbers from PDF text (local LLM)
2. taxcalc computes federal tax (deterministic, no LLM)
3. Returns structured results

Zero network calls. Everything stays on localhost.
"""
import json
import logging
import subprocess
from pathlib import Path

log = logging.getLogger("tax_calc")


def extract_pdf_text(path: Path, max_chars: int = 6000) -> str:
    """Extract text from PDF using pdftotext (local)."""
    for cmd in [
        ["pdftotext", "-layout", str(path), "-"],
        ["textutil", "-convert", "txt", "-stdout", str(path)],
    ]:
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout[:max_chars]
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
    return ""


def _extract_w2_regex(text: str) -> dict:
    """Extract W-2 data using regex patterns. More reliable than LLM for forms."""
    import re
    data = {}

    # Common W-2 patterns: "Box N" followed by amount, or labeled fields
    # Wages (Box 1)
    for pattern in [
        r'(?:box\s*1|wages.*tips.*other\s*comp)[^\d$]*[\$]?\s*([\d,]+\.?\d*)',
        r'(?:1\s+wages)[^\d$]*[\$]?\s*([\d,]+\.?\d*)',
        r'wages[,\s]*tips[,\s]*(?:other\s+)?comp[^\d$]*[\$]?\s*([\d,]+\.?\d*)',
    ]:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            data['wages'] = float(m.group(1).replace(',', ''))
            break

    # Federal tax withheld (Box 2)
    for pattern in [
        r'(?:box\s*2|federal\s+income\s+tax\s+withheld)[^\d$]*[\$]?\s*([\d,]+\.?\d*)',
        r'(?:2\s+federal\s+income\s+tax)[^\d$]*[\$]?\s*([\d,]+\.?\d*)',
        r'federal\s+(?:income\s+)?tax\s+w(?:ith)?held[^\d$]*[\$]?\s*([\d,]+\.?\d*)',
    ]:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            data['fed_withheld'] = float(m.group(1).replace(',', ''))
            break

    # State tax withheld (Box 17)
    for pattern in [
        r'(?:box\s*17|state\s+income\s+tax)[^\d$]*[\$]?\s*([\d,]+\.?\d*)',
        r'(?:17\s+state\s+income\s+tax)[^\d$]*[\$]?\s*([\d,]+\.?\d*)',
    ]:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            data['state_withheld'] = float(m.group(1).replace(',', ''))
            break

    # State code (Box 15)
    m = re.search(r'(?:box\s*15|state\b)[^\w]*([A-Z]{2})\b', text)
    if m:
        data['state'] = m.group(1)

    # Social security wages (Box 3)
    for pattern in [
        r'(?:box\s*3|social\s+security\s+wages)[^\d$]*[\$]?\s*([\d,]+\.?\d*)',
    ]:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            data['ss_wages'] = float(m.group(1).replace(',', ''))
            break

    return data


def _extract_1099_regex(text: str) -> dict:
    """Extract 1099 data using regex patterns."""
    import re
    data = {}

    # Interest (1099-INT)
    m = re.search(r'(?:interest\s+income|box\s*1.*interest)[^\d$]*[\$]?\s*([\d,]+\.?\d*)', text, re.IGNORECASE)
    if m:
        data['interest'] = float(m.group(1).replace(',', ''))

    # Dividends (1099-DIV)
    m = re.search(r'(?:ordinary\s+dividends|total\s+ordinary\s+dividends)[^\d$]*[\$]?\s*([\d,]+\.?\d*)', text, re.IGNORECASE)
    if m:
        data['dividends'] = float(m.group(1).replace(',', ''))

    # Capital gains
    for pattern in [
        r'(?:net\s+(?:short|long)[- ]term\s+(?:capital\s+)?gain)[^\d$-]*[\$]?\s*(-?[\d,]+\.?\d*)',
        r'(?:total\s+(?:capital\s+)?gain)[^\d$-]*[\$]?\s*(-?[\d,]+\.?\d*)',
        r'(?:proceeds|net\s+gain)[^\d$-]*[\$]?\s*(-?[\d,]+\.?\d*)',
    ]:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            data['capital_gains'] = float(m.group(1).replace(',', ''))
            break

    return data


def extract_tax_data(pdf_texts: dict[str, str], ollama_model: str) -> dict:
    """Extract structured tax data from PDF text.

    Strategy: regex first (reliable for standard forms), LLM fallback for edge cases.
    """
    from sub_agent import _ollama_call

    # Step 1: Try regex extraction first — much more reliable than LLM
    regex_data = {}
    for name, text in pdf_texts.items():
        name_lower = name.lower()
        if "w2" in name_lower or "w-2" in name_lower:
            w2 = _extract_w2_regex(text)
            if w2.get("wages"):
                regex_data["wages_primary"] = w2["wages"]
            if w2.get("fed_withheld"):
                regex_data["federal_withheld_primary"] = w2["fed_withheld"]
            if w2.get("state_withheld"):
                regex_data["state_withheld_primary"] = w2["state_withheld"]
            if w2.get("state"):
                regex_data["state"] = w2["state"]
            log.info("W-2 regex extracted: %s", w2)
        elif "1099" in name_lower:
            f1099 = _extract_1099_regex(text)
            regex_data["interest_income"] = regex_data.get("interest_income", 0) + f1099.get("interest", 0)
            regex_data["dividend_income"] = regex_data.get("dividend_income", 0) + f1099.get("dividends", 0)
            regex_data["capital_gains"] = regex_data.get("capital_gains", 0) + f1099.get("capital_gains", 0)
            log.info("1099 regex extracted from %s: %s", name, f1099)

    combined = "\n\n---\n\n".join(
        f"### {name}\n{text}" for name, text in pdf_texts.items()
    )

    prompt = f"""Extract tax information from these documents. Return JSON only.

{combined}

Extract these fields (use 0 if not found):
{{
  "filing_status": "single" or "married_jointly" or "married_separately" or "head_of_household",
  "wages_primary": <W-2 Box 1 for primary filer>,
  "wages_spouse": <W-2 Box 1 for spouse, 0 if none>,
  "federal_withheld_primary": <W-2 Box 2 for primary>,
  "federal_withheld_spouse": <W-2 Box 2 for spouse, 0 if none>,
  "state_withheld_primary": <W-2 Box 17 for primary>,
  "state_withheld_spouse": <W-2 Box 17 for spouse>,
  "state": <two-letter state code from W-2 Box 15>,
  "interest_income": <1099-INT total>,
  "dividend_income": <1099-DIV ordinary dividends>,
  "capital_gains": <1099-B net gain/loss>,
  "retirement_contributions": <401k/IRA contributions from W-2 Box 12>,
  "hsa_contributions": <HSA contributions>,
  "mortgage_interest": <1098 mortgage interest>,
  "property_tax": <property tax paid>,
  "state_local_tax": <state/local income tax from W-2 Box 17 or estimated>,
  "charitable": <charitable donations>,
  "num_children": <number of qualifying children>,
  "age_primary": <age of primary filer, estimate 35 if unknown>,
  "age_spouse": <age of spouse, estimate 33 if unknown>
}}

IMPORTANT:
- Extract ACTUAL numbers from the documents. Do not guess.
- If a field is not in any document, use 0.
- For capital gains, use NET (gains minus losses).
- JSON only, no explanation."""

    # Step 2: LLM extraction as supplement (for things regex missed)
    llm_data = {}
    result = _ollama_call(ollama_model, prompt,
                         system="Extract structured data from tax documents. JSON only.",
                         timeout=120)
    if result:
        try:
            clean = result.strip().strip("```json").strip("```").strip()
            llm_data = json.loads(clean)
            log.info("LLM extracted: %s", {k: v for k, v in llm_data.items() if v})
        except (json.JSONDecodeError, ValueError) as e:
            log.warning("LLM extraction parse failed: %s", e)

    # Step 3: Merge — regex wins for numeric fields (more reliable), LLM fills gaps
    merged = {}
    merged.update(llm_data)   # LLM as base
    for k, v in regex_data.items():
        if v and v != 0:      # Regex overwrites LLM if regex found something
            merged[k] = v

    log.info("Final merged tax data: %s", {k: v for k, v in merged.items() if v})
    return merged


def compute_federal_tax(data: dict) -> dict:
    """Compute federal tax using taxcalc. Deterministic, no LLM."""
    try:
        from taxcalc import Policy, Records, Calculator
        import pandas as pd
    except ImportError:
        return {"error": "taxcalc not installed. Run: pip install taxcalc behresp"}

    filing = data.get("filing_status", "married_jointly")
    mars_map = {
        "single": 1, "married_jointly": 2, "married_separately": 3,
        "head_of_household": 4,
    }
    mars = mars_map.get(filing, 2)

    wages_p = float(data.get("wages_primary", 0))
    wages_s = float(data.get("wages_spouse", 0))
    fed_withheld = float(data.get("federal_withheld_primary", 0)) + \
                   float(data.get("federal_withheld_spouse", 0))

    num_kids = int(data.get("num_children", 0))
    xtot = (2 if mars == 2 else 1) + num_kids

    df = pd.DataFrame({
        'RECID': [1],
        'MARS': [mars],
        'FLPDYR': [2025],
        'e00200': [wages_p + wages_s],
        'e00200p': [wages_p],
        'e00200s': [wages_s],
        'e00300': [float(data.get("interest_income", 0))],
        'e00600': [float(data.get("dividend_income", 0))],
        'p23250': [float(data.get("capital_gains", 0))],
        'e19200': [float(data.get("mortgage_interest", 0))],
        'e19800': [float(data.get("charitable", 0))],
        'e18400': [float(data.get("state_local_tax", 0)) +
                   float(data.get("property_tax", 0))],
        'XTOT': [xtot],
        'n24': [num_kids],
        'age_head': [int(data.get("age_primary", 35))],
        'age_spouse': [int(data.get("age_spouse", 33))] if mars == 2 else [0],
    })

    try:
        rec = Records(data=df, start_year=2025, gfactors=None, weights=None)
        pol = Policy()
        calc = Calculator(policy=pol, records=rec)
        calc.calc_all()
    except Exception as e:
        return {"error": f"taxcalc computation failed: {e}"}

    agi = calc.array('c00100')[0]
    std_ded = calc.array('standard')[0]
    item_ded = calc.array('c04470')[0]
    taxable = calc.array('c04800')[0]
    income_tax = calc.array('iitax')[0]
    ctc = calc.array('c07220')[0]
    payroll = calc.array('payrolltax')[0]
    combined = calc.array('combined')[0]

    owed_or_refund = combined - fed_withheld

    return {
        "filing_status": filing,
        "agi": round(agi),
        "standard_deduction": round(std_ded),
        "itemized_deduction": round(item_ded),
        "deduction_used": "itemized" if item_ded > std_ded else "standard",
        "deduction_amount": round(max(std_ded, item_ded)),
        "taxable_income": round(taxable),
        "income_tax": round(income_tax),
        "child_tax_credit": round(ctc),
        "payroll_tax": round(payroll),
        "total_federal_tax": round(combined),
        "total_withheld": round(fed_withheld),
        "owed_or_refund": round(owed_or_refund),
        "result": "OWE" if owed_or_refund > 0 else "REFUND",
        "amount": abs(round(owed_or_refund)),
    }


def format_result(data: dict, result: dict) -> str:
    """Format tax result as readable text."""
    if "error" in result:
        return f"计算失败: {result['error']}"

    status_map = {
        "single": "Single",
        "married_jointly": "Married Filing Jointly",
        "married_separately": "Married Filing Separately",
        "head_of_household": "Head of Household",
    }

    lines = [
        "# 2025 Federal Tax Estimate",
        f"Filing: {status_map.get(result['filing_status'], result['filing_status'])}",
        "",
        "## Income",
        f"  Wages (primary):     ${data.get('wages_primary', 0):>12,.0f}",
    ]
    if data.get("wages_spouse"):
        lines.append(f"  Wages (spouse):      ${data['wages_spouse']:>12,.0f}")
    if data.get("interest_income"):
        lines.append(f"  Interest:            ${data['interest_income']:>12,.0f}")
    if data.get("dividend_income"):
        lines.append(f"  Dividends:           ${data['dividend_income']:>12,.0f}")
    if data.get("capital_gains"):
        lines.append(f"  Capital Gains:       ${data['capital_gains']:>12,.0f}")

    lines.extend([
        f"  **AGI:               ${result['agi']:>12,}**",
        "",
        "## Deductions",
        f"  Standard:            ${result['standard_deduction']:>12,}",
        f"  Itemized:            ${result['itemized_deduction']:>12,}",
        f"  Used: **{result['deduction_used'].upper()}** (${result['deduction_amount']:,})",
        "",
        "## Tax",
        f"  Taxable Income:      ${result['taxable_income']:>12,}",
        f"  Income Tax:          ${result['income_tax']:>12,}",
    ])
    if result.get("child_tax_credit"):
        lines.append(f"  Child Tax Credit:   -${result['child_tax_credit']:>12,}")
    lines.extend([
        f"  Payroll Tax:         ${result['payroll_tax']:>12,}",
        f"  **Total Federal Tax: ${result['total_federal_tax']:>12,}**",
        "",
        "## Withheld vs Owed",
        f"  Total Withheld:      ${result['total_withheld']:>12,}",
        f"  Total Tax:           ${result['total_federal_tax']:>12,}",
        "",
    ])

    if result["result"] == "REFUND":
        lines.append(f"  **→ REFUND: ${result['amount']:,}**")
    else:
        lines.append(f"  **→ OWE: ${result['amount']:,}**")

    lines.extend([
        "",
        "---",
        "*Computed by taxcalc (deterministic). Numbers extracted by local LLM.*",
        "*This is an estimate — consult a tax professional for filing.*",
    ])

    return "\n".join(lines)


def run_tax_pipeline(pdf_paths: list[Path], ollama_model: str) -> str:
    """Full pipeline: PDFs → extract → compute → format."""
    # Step 1: Extract text from PDFs
    pdf_texts = {}
    for path in pdf_paths:
        text = extract_pdf_text(path)
        if text and len(text.strip()) > 50:
            pdf_texts[path.name] = text
            log.info("Extracted %d chars from %s", len(text), path.name)
        else:
            log.warning("Could not extract text from %s", path.name)

    if not pdf_texts:
        return "无法从 PDF 中提取文本。请确认文件不是扫描件（需要 OCR）。"

    # Step 2: LLM extracts structured data
    data = extract_tax_data(pdf_texts, ollama_model)
    if not data:
        return "无法从文档中提取税务数据。请检查文件是否包含 W-2、1099 等税表。"

    # Step 3: Deterministic tax computation
    result = compute_federal_tax(data)

    # Step 4: Format
    return format_result(data, result)
