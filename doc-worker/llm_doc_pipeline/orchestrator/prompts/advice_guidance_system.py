"""
Advice and Guidance System Prompt for generating clinical recommendations
based on consolidated patient data from multiple documents.
"""

ADVICE_GUIDANCE_SYSTEM = """You are a clinical AI assistant specializing in generating comprehensive advice and guidance based on consolidated patient data from multiple healthcare documents.

Your task is to analyze the provided consolidated patient information and generate structured, evidence-based clinical advice and guidance.

## Input Analysis Requirements:

1. **Patient Demographics**: Consider age, sex, medical history
2. **Clinical Context**: Analyze referral reasons, symptoms, and concerns
3. **Investigation Results**: Interpret test results, imaging, and diagnostic findings
4. **Triage Assessment**: Consider urgency level and clinical reasoning
5. **Document Types**: Factor in referral letters, test results, clinical notes
6. **Medication History**: Review current medications and potential interactions

## Output Structure:

Generate advice and guidance in exactly this JSON format:

```json
{
  "clinical_advice": [
    "Primary clinical recommendations based on findings",
    "Diagnostic considerations and differential diagnoses",
    "Treatment approach recommendations"
  ],
  "next_actions": [
    "Immediate steps required",
    "Investigations to be ordered",
    "Referrals or consultations needed"
  ],
  "safety_netting": [
    "Patient safety advice and warning signs",
    "When to seek urgent medical attention",
    "Escalation pathways and contact information"
  ],
  "follow_up": [
    "Follow-up timeline and appointments",
    "Monitoring requirements",
    "Review of results and ongoing care"
  ]
}
```

## Clinical Reasoning Guidelines:

### Clinical Advice:
- Base recommendations on evidence from the documents
- Consider patient-specific factors (age, comorbidities, medications)
- Provide clear, actionable clinical guidance
- Address the primary concern from referral documents
- Incorporate test results into clinical decision-making

### Next Actions:
- Prioritize based on urgency level
- Be specific about investigations, treatments, or referrals
- Consider resource availability and clinical pathways
- Include timeframes where appropriate

### Safety Netting:
- Identify red flag symptoms specific to the clinical scenario
- Provide clear instructions for when to seek help
- Include contact information or escalation pathways
- Consider patient understanding and health literacy

### Follow-up:
- Specify timing based on clinical urgency
- Include what should be monitored or reviewed
- Plan for result communication
- Consider ongoing care coordination

## Clinical Scenarios to Consider:

1. **Normal Test Results with Concerning Symptoms**:
   - Explain limitations of normal results
   - Consider additional investigations
   - Reassure appropriately while maintaining vigilance

2. **Abnormal Results Requiring Action**:
   - Explain significance of findings
   - Provide clear management plan
   - Ensure appropriate specialist involvement

3. **Urgent vs Routine Scenarios**:
   - Tailor advice to match clinical urgency
   - Provide appropriate safety netting
   - Consider patient anxiety and concerns

## Quality Requirements:

- Evidence-based recommendations
- Clear, jargon-free language for patient-facing advice
- Specific timeframes and actions
- Appropriate clinical escalation
- Consideration of patient safety
- Professional medical tone

Analyze the provided consolidated patient data thoroughly and generate comprehensive, personalized advice and guidance that addresses all aspects of the patient's clinical scenario."""

ADVICE_GUIDANCE_USER_TEMPLATE = """Based on the following consolidated patient data, generate comprehensive clinical advice and guidance:

## Patient Information:
Name: {patient_name}
DOB: {patient_dob}
Patient ID: {patient_id}

## Clinical Summary:
Chief Complaint: {chief_complaint}
Clinical Context: {clinical_context}

## Key Findings:
{clinical_findings}

## Current Triage Assessment:
Urgency: {triage_urgency}
Disposition: {triage_disposition}
Clinical Reasoning: {triage_reasoning}

## Document Sources:
{document_summary}

## Test Results and Medications:
{entities_summary}

## Consolidation Quality:
- Clinical Consistency Score: {consistency_score}
- Documents Processed: {doc_count}
- Entity Coverage: {entity_coverage}

Please provide structured advice and guidance in the specified JSON format, ensuring all recommendations are evidence-based and appropriate for this clinical scenario."""


def format_advice_guidance_prompt(consolidated_data: dict) -> str:
    """
    Format the consolidated patient data into a prompt for LLM advice generation.
    
    Args:
        consolidated_data: Dictionary containing consolidated patient information
        
    Returns:
        Formatted prompt string for LLM
    """
    
    # Extract patient information
    patient_info = consolidated_data.get("patient_info", {})
    patient_name = patient_info.get("name", "Unknown")
    patient_dob = patient_info.get("dob", "Unknown")
    patient_id = patient_info.get("patient_id", "Unknown")
    
    # Extract clinical summary
    clinical_summary = consolidated_data.get("clinical_summary", {})
    chief_complaint = clinical_summary.get("chief_complaint", "Not specified")
    clinical_context = clinical_summary.get("clinical_context", "No clinical context available")
    findings = clinical_summary.get("findings", [])
    clinical_findings = "\n".join([f"- {finding}" for finding in findings]) if findings else "No specific findings documented"
    
    # Extract triage information
    triage = consolidated_data.get("triage", {})
    triage_urgency = triage.get("urgency", "routine")
    triage_disposition = triage.get("disposition", "routine follow-up")
    triage_reasoning = triage.get("reasoning", "No specific reasoning provided")
    
    # Summarize document sources
    doc_refs = consolidated_data.get("document_references", [])
    doc_summary = []
    for doc in doc_refs:
        doc_type = doc.get("document_type", "Unknown")
        doc_name = doc.get("document_name", "Unknown")
        doc_summary.append(f"- {doc_type}: {doc_name}")
    document_summary = "\n".join(doc_summary) if doc_summary else "No document references available"
    
    # Summarize entities (key medications, test results)
    entities = consolidated_data.get("entities", {})
    scalar_entities = entities.get("scalar", {})
    list_entities = entities.get("lists", {})
    
    entities_summary_parts = []
    
    # Add key scalar entities
    key_scalars = ["Age", "Sex", "Contact", "Address"]
    for key in key_scalars:
        for entity_name, entity_data in scalar_entities.items():
            if any(k.lower() in entity_name.lower() for k in [key.lower()]):
                value = entity_data.get("value", "") if isinstance(entity_data, dict) else str(entity_data)
                entities_summary_parts.append(f"- {entity_name}: {value}")
    
    # Add medications
    drug_entities = []
    for entity_name, entity_list in list_entities.items():
        if "drug" in entity_name.lower() and isinstance(entity_list, list):
            for item in entity_list[:5]:  # Limit to first 5 medications
                if isinstance(item, dict):
                    drug_name = item.get("value", "Unknown medication")
                    drug_entities.append(f"- Medication: {drug_name}")
    
    if drug_entities:
        entities_summary_parts.extend(drug_entities)
    
    # Add test results
    test_entities = []
    for entity_name, entity_list in list_entities.items():
        if any(term in entity_name.lower() for term in ["test", "result", "lab"]) and isinstance(entity_list, list):
            for item in entity_list[:5]:  # Limit to first 5 test results
                if isinstance(item, dict):
                    test_value = item.get("value", "Unknown test")
                    test_entities.append(f"- Test Result: {test_value}")
    
    if test_entities:
        entities_summary_parts.extend(test_entities)
    
    entities_summary = "\n".join(entities_summary_parts) if entities_summary_parts else "No specific medications or test results documented"
    
    # Extract metadata
    metadata = consolidated_data.get("consolidation_metadata", {})
    consistency_score = metadata.get("clinical_consistency_score", "Unknown")
    doc_count = len(doc_refs)
    entity_coverage = f"{len(scalar_entities)} scalar + {len(list_entities)} list entities"
    
    return ADVICE_GUIDANCE_USER_TEMPLATE.format(
        patient_name=patient_name,
        patient_dob=patient_dob,
        patient_id=patient_id,
        chief_complaint=chief_complaint,
        clinical_context=clinical_context,
        clinical_findings=clinical_findings,
        triage_urgency=triage_urgency,
        triage_disposition=triage_disposition,
        triage_reasoning=triage_reasoning,
        document_summary=document_summary,
        entities_summary=entities_summary,
        consistency_score=consistency_score,
        doc_count=doc_count,
        entity_coverage=entity_coverage
    )