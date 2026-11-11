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
