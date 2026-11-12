"""Golden cases for classify_visa with the example-profile phrases: (text, expected status)."""

NA, OF, NS = "not_available", "offered", "not_stated"

VISA: list[tuple[str, str]] = [
    # --- not available
    ("No visa sponsorship is available for this role.", NA),
    ("No sponsorship.", NA),
    ("We will not sponsor employment visas.", NA),
    ("The company is unable to sponsor work visas at this time.", NA),
    ("We are not able to provide visa sponsorship.", NA),
    ("Sponsorship is not available for this position.", NA),
    ("Visa sponsorship: Not available", NA),
    ("Visa sponsorship: no", NA),
    ("Must be authorized to work in the US without sponsorship.", NA),
    (
        "Applicants must be legally authorized to work in the United States without visa sponsorship now or in the future.",
        NA,
    ),
    ("U.S. citizens only.", NA),
    ("US citizens only", NA),
    ("US citizenship required.", NA),
    ("Green card required.", NA),
    ("Open to U.S. citizens or permanent residents only.", NA),
    ("Candidates must be able to work without sponsorship.", NA),
    ("We do not sponsor H-1B visas.", NA),
    ("We don't provide visa sponsorship for this role.", NA),
    ("Company cannot sponsor candidates for work visas.", NA),
    ("This position is not eligible for visa sponsorship.", NA),
    ("No H-1B sponsorship available.", NA),
    ("We are not accepting applicants who require sponsorship.", NA),
    ("Applicants must not require sponsorship now or in the future.", NA),
    ("Sponsorship will not be provided.", NA),
    ("We offer equal opportunity. We do not provide visa sponsorship.", NA),
    (
        "About us\nWe provide visa sponsorship for some roles.\nThis role does not offer sponsorship.",
        NA,
    ),
    (
        "Requirements\n- 3+ years SQL\n- Must be authorized to work in the US without sponsorship",
        NA,
    ),
    ("As an equal opportunity employer, we cannot sponsor visas.", NA),
    ("Must be a US citizen.", NA),
    ("Only US citizens will be considered.", NA),
    # --- near-misses that must NOT exclude
    ("We do not discriminate on the basis of visa status.", NS),
    ("We do not discriminate against applicants who need visa sponsorship.", NS),
    ("Applicants must be authorized to work in the US.", NS),
    ("Must be legally authorized to work in the United States.", NS),
    ("Employment eligibility verification (I-9) is required upon hire.", NS),
    ("We consider all applicants regardless of visa status.", NS),
    ("No US citizenship required.", NS),
    ("US citizenship is not required.", NS),
    ("Not limited to US citizens, all qualified applicants are welcome.", NS),
    ("We are an equal opportunity employer and welcome sponsorship of ideas from our team.", NS),
    ("Sponsorship may be available for exceptional candidates.", OF),
    ("Visa sponsorship is not available except for exceptional candidates.", NS),
    ("We generally do not sponsor, but make exceptions for the right candidate.", NS),
    ("Our sponsorship of local events is a point of pride.", NS),
    ("", NS),
    ("A great analytics role with strong SQL skills.", NS),
    # --- offered
    ("We provide visa sponsorship for qualified candidates.", OF),
    ("Visa sponsorship is available.", OF),
    ("Sponsorship is offered for this position.", OF),
    ("H-1B transfer is supported.", OF),
    ("H1B sponsorship available", OF),
    ("We are open to sponsor the right candidate.", OF),
    ("We will offer sponsorship to eligible candidates.", OF),
    ("We are able to sponsor visas.", OF),
    ("Benefits\n- Visa sponsorship available\n- 401k", OF),
    # --- precedence: exclusion beats offer
    ("We provide sponsorship in some cases. This role cannot sponsor.", NA),
    (
        "Sponsorship is available. Applicants must be authorized to work in the US without sponsorship.",
        NA,
    ),
    # --- offer negated is not offer
    ("We don't offer visa sponsorship.", NA),
    ("We will not provide sponsorship.", NA),
    # --- review round 1 (B1): transfer welcome overrides refusals, never split on ';'
    ("We do not currently sponsor, but H-1B transfers are accepted.", OF),
    ("We cannot sponsor new H-1B petitions; H-1B transfers welcome.", OF),
    ("No visa sponsorship for this role (H-1B transfers considered).", OF),
    (
        "This role is open to candidates requiring a new H-1B petition (cap subject). "
        "We will consider H-1B transfers.",
        OF,
    ),
    ("Visa sponsorship is not available for this position, except for H-1B transfers.", OF),
    (
        "We are not able to sponsor at this time, however H-1B transfer candidates will be considered.",
        OF,
    ),
    ("For this role, we will not sponsor a new H-1B but can support an H-1B transfer.", OF),
    ("Candidates on F-1 OPT are not eligible; we sponsor H-1B transfers.", OF),
    ("We are unable to sponsor CPT/OPT students for this role.", NS),
    ("Note: We are not able to sponsor candidates on OPT/STEM OPT.", NS),
    ("Employer will not sponsor or transfer H-1B visas.", NA),
    # --- S1 misses
    ("Candidates must be authorized to work in the U.S. without requiring sponsorship.", NA),
    ("Must be eligible to work in the US without employer sponsorship.", NA),
    (
        "You must be legally authorized to work in the United States without the need for "
        "employment-based visa sponsorship now or in the future.",
        NA,
    ),
    (
        "Must be authorized to work in the US without the need for current or future visa sponsorship.",
        NA,
    ),
    ("Applicants must be able to work in the US without current or future sponsorship.", NA),
    ("This role does not support visa sponsorship.", NA),
    ("Candidates requiring sponsorship will not be considered.", NA),
    ("This position is not open to candidates requiring sponsorship.", NA),
    ("Not open to sponsorship.", NA),
    ("We're not able to offer relocation or visa sponsorship.", NA),
    ("Must not now, or at any time in the future, require sponsorship.", NA),
    ("We are not sponsoring visas for this position.", NA),
    ("We will not be sponsoring visas for this role.", NA),
    ("We offer H-1B visa sponsorship for this role.", OF),
    ("We sponsor visas for the right candidate.", OF),
    ("This role is eligible for visa sponsorship.", OF),
    # --- S2 non-visa sponsor
    ("This job posting is not sponsored content.", NS),
    ("Our hackathon is not sponsored by any vendor.", NS),
    ("Tuition sponsorship is not available.", NS),
    ("No sponsorship budget is provided.", NS),
    ("Citizenship required: no.", NS),
    ("Dual citizenship is required? No - any work authorization accepted.", NS),
    ("Note: we do not require sponsorship letters from previous employers.", NS),
    ("We do not sponsor events. We do sponsor visas.", OF),
    # --- S3 export control stays not_stated (flag only)
    ("Applicants must be U.S. persons as defined by ITAR.", NS),
    ("Active Secret clearance required.", NS),
]


FLAGS: list[tuple[str, str, str | None]] = [
    ("We are unable to sponsor CPT/OPT students for this role.", NS, "visa:opt_only"),
    ("We cannot sponsor new H-1B petitions.", NS, "visa:opt_only"),
    ("Applicants must be U.S. persons as defined by ITAR.", NS, "visa:export_control"),
    ("Active Secret clearance required.", NS, "visa:export_control"),
    ("A security clearance is required for this role.", NS, "visa:export_control"),
    ("We do not sponsor visas.", NA, None),
    ("Data analyst role in Edison.", NS, None),
]


# (text, flags visa:export_control?) -- lightly anonymised snippets from the Phase 1 live crawl.
EXPORT: list[tuple[str, bool]] = [
    # --- true positives
    (
        "Required: Candidates have active Top Secret / Sensitive Compartmented Information (TS/SCI) security clearance.",
        True,
    ),
    (
        "What We Require Active US Security clearance, or eligibility and willingness to obtain a US Security clearance.",
        True,
    ),
    (
        "Active security clearance is required, along with experience in highly regulated public-sector markets.",
        True,
    ),
    (
        "Due to federal contract requirements, U.S. citizenship and eligibility for a U.S. government secret clearance are required to access classified information.",
        True,
    ),
    (
        "Must haves: An active TS/SCI clearance (Full Scope Polygraph is a nice to have) 3+ years of experience.",
        True,
    ),
    (
        "Candidates must be eligible and willing to obtain and maintain a U.S. government security clearance.",
        True,
    ),
    (
        "Required: Public Trust Clearance - Candidates must be able to obtain and maintain a US public trust clearance.",
        True,
    ),
    ("Clearance: Active Secret clearance required; TS/SCI strongly preferred.", True),
    (
        "To comply with U.S. export control laws and regulations, candidates for this role may need to meet certain legal status requirements as provided in those laws and regulations.",
        True,
    ),
    (
        "To conform to US export control regulations, candidates should be eligible for any required authorizations from the US government.",
        True,
    ),
    (
        "This is a U.S. Persons-only role. Because of the export-control status of the data, all candidates must independently qualify as U.S. Persons.",
        True,
    ),
    (
        "The successful applicant will be performing work in FedRAMP High environments, and therefore, must be a U.S. Person (i.e. U.S. citizen, U.S. national, lawful permanent resident).",
        True,
    ),
    (
        "To comply with a U.S. government contract, applicants for this position must be U.S. persons (i.e., U.S. lawful permanent residents, refugees, and asylees).",
        True,
    ),
    (
        "What We Require U.S. Person Required 1+ years of relevant, post-college work experience.",
        True,
    ),
    (
        "Applicants must be eligible to work with ITAR-controlled technical data and are restricted to U.S. persons.",
        True,
    ),
    (
        "For USG: An active U.S. security clearance, or eligibility and willingness to obtain one, is required. For all other roles: a clearance is beneficial.",
        True,
    ),
    ("Ability to obtain and maintain security clearance if required.", True),
    # --- false positives (must NOT flag)
    (
        "Compliance If access to export-controlled technology or source code is required for performance of job duties, it is within Employer's discretion whether to apply for a U.S. government license.",
        False,
    ),
    (
        "Our company develops technology that is subject to U.S. and other countries' export controls and licensing requirements for individuals who are located in or are nationals of the following sanctioned countries and regions: Belarus, Cuba, Iran.",
        False,
    ),
    (
        "Certain positions may be limited to candidates that can satisfy additional scrutiny, and eligibility for such positions may hinge on verification of a candidate's U.S. person status and/or citizenship status.",
        False,
    ),
    (
        "This position may require access to information protected under U.S. export control laws, including the U.S. Export Administration Regulations.",
        False,
    ),
    (
        "If you are a curious person and your instinct when something breaks is to redesign the process, you will do well here.",
        False,
    ),
    ("You're a naturally curious person who enjoys digging into the data.", False),
    ("Obtain clearance from Management whenever this cannot be done.", False),
    (
        "Cohere is seeking a Global Public Policy Manager to lead policy engagement on compute infrastructure, export controls, and sovereign AI.",
        False,
    ),
    (
        "Familiarity with export controls and the regulatory requirements that apply to partners and counterparties.",
        False,
    ),
    (
        "Monitor global AI, privacy, copyright, and export-control developments and translate them into practical internal guidance.",
        False,
    ),
    (
        "Manage the contract lifecycle through DocuSign, run related-party and export control checks, and maintain the contract repository.",
        False,
    ),
    (
        "Answer public sector customer questions (what a boundary allows, CUI, IRS Publication 1075, CJIS, ITAR) and handle RFIs.",
        False,
    ),
    (
        "Advanced knowledge of international trade regulations, including the Export Administration Regulations (EAR) and ITAR.",
        False,
    ),
    (
        "Active US Security clearance, or eligibility and willingness to obtain a US Security clearance is beneficial, but not necessary.",
        False,
    ),
    (
        "Clearance: An active U.S. government security clearance is preferred but not required.",
        False,
    ),
    ("Active or prior U.S. security clearance is a plus, but not required.", False),
    (
        "Data classification levels, federal security control profiles, and personnel security clearances.",
        False,
    ),
    (
        "Bachelor's degree in Information Security, Computer Science, or related field required.",
        False,
    ),
    ("Everything must clear inventory; no security clearance is required for this role.", False),
    ("As a U.S. person for tax purposes you may file Form 1040.", False),
]
