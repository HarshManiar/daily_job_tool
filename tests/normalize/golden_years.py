"""Golden cases for extract_years: (description text, (years_min, years_max)). Written from the rules."""

N = (None, None)

YEARS: list[tuple[str, tuple[int | None, int | None]]] = [
    # --- basic shapes
    ("3+ years of experience in analytics", (3, None)),
    ("3-5 years of experience in data analysis", (3, 5)),
    ("3 - 5 years of experience", (3, 5)),
    ("3–5 years experience with SQL", (3, 5)),
    ("minimum of 4 years of experience", (4, None)),
    ("Minimum 4 years of experience in a similar role", (4, None)),
    ("at least 5 yrs of experience", (5, None)),
    ("At least five years of relevant experience", (5, None)),
    ("2 to 4 years' experience", (2, 4)),
    ("2 to 4 years of experience in healthcare analytics", (2, 4)),
    ("5 years of relevant experience", (5, None)),
    ("7+ years of experience in SQL", (7, None)),
    ("three years of experience", (3, None)),
    ("Three to five years of experience", (3, 5)),
    ("five+ years of data engineering experience", (5, None)),
    ("3 or more years of experience with Python", (3, None)),
    ("5 plus years of experience", (5, None)),
    ("4+ yrs experience with Databricks", (4, None)),
    ("1-3 years of experience", (1, 3)),
    ("0-2 years of experience", (0, 2)),
    ("two (2) years of experience in reporting", (2, None)),
    ("Ten years of experience in data warehousing", (10, None)),
    ("8+ years experience building pipelines", (8, None)),
    ("1 year of experience with SQL", (1, None)),
    ("3+years of experience", (3, None)),
    # --- strictest required statement wins
    ("3+ years of SQL experience and 7+ years of analytics experience", (7, None)),
    ("Requirements\n- 3+ years SQL\n- 7+ years analytics", (7, None)),
    (
        "Requirements:\n- 2+ years of experience with Python\n- 5+ years of experience in analytics",
        (5, None),
    ),
    ("Requirements:\n3-5 years of experience\n4+ years of experience with SQL", (4, None)),
    # --- sections
    ("Responsibilities\nBuild dashboards.\nRequirements\n3+ years of experience in BI", (3, None)),
    (
        "Minimum Qualifications:\n- 4 years of experience\nPreferred Qualifications:\n- 8 years of experience",
        (4, None),
    ),
    (
        "Qualifications\n- 3+ years of experience\nNice to have\n- 10+ years of experience with Spark",
        (3, None),
    ),
    (
        "What you bring\n- 5+ years of experience in analytics\nBonus points\n- 9+ years of experience",
        (5, None),
    ),
    (
        "Requirements: 3+ years of SQL experience. Preferred Qualifications: 8+ years of experience in pharma",
        (3, None),
    ),
    ("Required: 2+ years of experience. Preferred: 6+ years of experience", (2, None)),
    (
        "Basic Qualifications\n3+ years of experience\nPreferred Qualifications\n5+ years of experience",
        (3, None),
    ),
    (
        "Preferred Qualifications\n- 6+ years of experience\nMinimum Qualifications\n- 2+ years of experience",
        (2, None),
    ),
    ("5+ years of experience required", (5, None)),
    ("Must have 6 years of experience", (6, None)),
    ("The ideal candidate has 8+ years of experience", (8, None)),
    # --- preferred only (used when nothing else)
    ("5+ years of experience preferred", (5, None)),
    ("Preferred: 4+ years of experience", (4, None)),
    ("Nice to have: 6+ years of experience with Snowflake", (6, None)),
    ("Experience with Tableau for 3+ years is a plus", (3, None)),
    ("Ideally 5 years of experience in pharma", (5, None)),
    # --- preferred is ignored when a required exists in the same text
    ("3+ years of experience in SQL. 8+ years of pharma experience is a plus.", (3, None)),
    ("Required: 3-5 years of experience. Preferred: 10+ years of experience.", (3, 5)),
    ("You have 2+ years of SQL experience; 7+ years preferred", (2, None)),
    # --- ranges keep their max
    ("2-4 years of experience\nBonus: 5+ years of experience", (2, 4)),
    # --- negatives: not experience
    ("We have been in business for 10 years", N),
    ("Founded 20 years ago, we are a leader in analytics", N),
    ("The company was founded over 20 years ago", N),
    ("401k match vests after 1 year", N),
    ("Employees get an extra week of PTO after 5 years", N),
    ("With 15 years in business we serve 500 clients", N),
    ("We have 25+ years of history helping pharma clients", N),
    ("Over the past 5 years we have grown 10x", N),
    ("Our team has 30 years of combined experience", N),
    ("Join a company that has been innovating for over 40 years", N),
    ("Internship for 2 years old startup", N),
    ("The role reports to a leader with 20 years at the firm", N),
    ("Annual salary review each year", N),
    ("Benefits\n401k with employer match after 1 year of service", N),
    ("About Us\nWe have 12 years of experience helping clients grow.", N),
    ("less than 2 years of experience is fine", N),
    ("up to 3 years of experience", N),
    ("", N),
    ("Great analytics role for a self starter", N),
    ("Build 3 dashboards per year", N),
    ("We are 5 years old", N),
    ("Work from home 2 days a week", N),
    ("Must be 18 years or older", N),
    # --- misc tricky positives
    (
        "Bachelor's degree and 3+ years of experience, or Master's degree and 1+ year of experience",
        (1, None),  # review round 1: degree alternatives take the minimum
    ),
    ("Proven track record (5+ years) in commercial analytics", (5, None)),
    ("Strong SQL skills (3+ years)", (3, None)),
    ("5+ years' experience in pharmaceutical analytics", (5, None)),
    ("Minimum of 3-5 years in a related field", (3, 5)),
    ("1+ years of experience in data analysis, 2+ years of experience in SQL", (2, None)),
    ("Entry level: 0-1 years of experience", (0, 1)),
    ("six years of experience in analytics", (6, None)),
    ("experience: 3 to 5 years", (3, 5)),
    # --- review round 1 (B3): degree alternatives -> minimum
    (
        "Bachelor's degree and 7 years of experience; or Master's degree and 5 years of experience",
        (5, None),
    ),
    (
        "Bachelor's degree with 8+ years of experience, or Master's degree with 6+ years, "
        "or PhD with 3+ years.",
        (3, None),
    ),
    ("8+ years with BS, 6+ with MS, 3+ with PhD", (3, None)),
    ("7+ years of experience (or MS + 5)", (5, None)),
    (
        "7+ years of experience with a Bachelor's degree, or 5+ years with a Master's degree",
        (5, None),
    ),
    ("Bachelor's plus 2 years of experience or Master's with 0 years", (0, None)),
    # --- tiered / hedged
    ("Senior level: 7-10 years of experience in data engineering; mid level: 3-5 years.", (3, 5)),
    ("Experience: 5-8 years (Senior) or 2-4 years (Analyst)", (2, 4)),
    ("Candidates with 7+ years of experience may be considered for a senior title.", (7, None)),
    ("Ideally 7+ years of experience in commercial analytics.", (7, None)),
    # --- headings
    ("Preferred: 7+ years of experience. Required: 4+ years of experience.", (4, None)),
    ("Preferred Qualifications\n- 8+ years\nRequired\n- 3+ years of experience", (3, None)),
    ("Must have 4+ years of experience", (4, None)),
    # --- company blurbs are not candidate requirements
    ("Acme brings 20+ years of experience in life sciences consulting to our clients.", N),
    ("With a 15-year track record of excellence, Acme is a leader in pharma analytics.", N),
    ("Access to 12+ years of longitudinal patient data.", N),
    ("Our analysts have an average of 8 years of experience.", N),
    ("This role reports to a leader with 15 years of experience in commercial analytics.", N),
    ("Acme has 10+ years of experience working with top 20 pharma.", N),
    ("Join our 8-year-old startup.", N),
    ("Join a company with 30 years of industry leadership.", N),
    ("5 years of data engineering experience", (5, None)),
    # --- N4: a parenthetical qualifying the field is not a preferred cue for the years
    (
        "Requirements: 8+ years in Life Sciences Consulting (Business or Management Consulting preferred)",
        (8, None),
    ),
]

# (text, pool, flags): only the REQUIRED pool may exclude; others with >= 7 are flagged
POOLS: list[tuple[str, str | None, tuple[str, ...]]] = [
    (
        "Requirements: 8+ years in Life Sciences Consulting (Business or Management Consulting preferred)",
        "R",
        (),
    ),
    ("Minimum of 8 years of experience", "R", ()),
    ("Ideally 7+ years of experience in commercial analytics.", "P", ("years:maybe_7+",)),
    (
        "Candidates with 7+ years of experience may be considered for a senior title.",
        "P",
        ("years:maybe_7+",),
    ),
    (
        "Overview: 8 years of experience in pharma analytics are valuable here.",
        "N",
        ("years:maybe_7+",),
    ),
    ("2+ years of SQL; 3+ years of experience required. 8+ years is a plus.", "R", ()),
    ("Nice to have: 10 years of experience", "P", ("years:maybe_7+",)),
    ("Nice to have: 3 years of experience", "P", ()),
    ("Required: 7+ years of experience in analytics", "R", ()),
]
