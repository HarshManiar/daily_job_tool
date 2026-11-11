"""Remote goldens: (workplace_raw, title, locations_raw, description, remote_type, regions).

regions None means "not asserted". Descriptions are plain text (already stripped).
"""

R = "remote"
H = "hybrid"
O = "onsite"  # noqa: E741
U = "unknown"

REMOTE: list[tuple] = [
    # structured hint wins
    ("Remote", "Software Engineer", ["San Francisco, CA"], None, R, ["US"]),
    ("remote", "Software Engineer", [], None, R, ["WORLD"]),
    ("Hybrid", "Software Engineer", ["London, UK"], None, H, []),
    ("OnSite", "Software Engineer", ["Berlin"], None, O, []),
    ("On-site", "Designer", ["Austin, TX"], "This is a fully remote role.", O, []),
    ("on_site", "Designer", ["Austin, TX"], None, O, []),
    ("hybrid", "Designer (Remote)", ["Paris"], None, H, []),
    ("Remote", "Engineer", ["Remote - US"], None, R, ["US"]),
    ("Remote", "Engineer", ["Remote (EMEA)"], None, R, ["EMEA"]),
    ("In office", "Account Executive", ["New York, NY"], None, O, []),
    # title / location tokens
    (None, "Software Engineer (Remote)", ["San Francisco, CA"], None, R, ["US"]),
    (None, "Software Engineer - Remote, US", [], None, R, None),
    (None, "Remote Software Engineer", [], None, R, ["WORLD"]),
    (None, "Software Engineer", ["Remote"], None, R, ["WORLD"]),
    (None, "Software Engineer", ["Remote - US"], None, R, ["US"]),
    (None, "Software Engineer", ["Remote, US"], None, R, ["US"]),
    (None, "Software Engineer", ["Remote (EMEA)"], None, R, ["EMEA"]),
    (None, "Software Engineer", ["Remote - Europe"], None, R, ["EUROPE"]),
    (None, "Software Engineer", ["Remote - Canada"], None, R, ["CA"]),
    (None, "Software Engineer", ["Remote in Germany"], None, R, ["DE"]),
    (None, "Software Engineer", ["Remote (US or Canada)"], None, R, ["US", "CA"]),
    (None, "Software Engineer", ["NYC | Remote"], None, R, ["US"]),
    (None, "Software Engineer", ["Anywhere"], None, R, ["WORLD"]),
    (None, "Software Engineer", ["Worldwide"], None, R, ["WORLD"]),
    (None, "Software Engineer", ["Remote - Anywhere"], None, R, ["WORLD"]),
    (None, "Software Engineer", ["Remote", "London, UK"], None, R, ["GB"]),
    (None, "Software Engineer", ["Remote"], "You must be located in the United States.", R, []),
    (None, "Software Engineer", ["Remote"], "Open to candidates within the EU only.", R, []),
    (
        None,
        "Software Engineer",
        ["Remote"],
        "We are a friendly team building great tools.",
        R,
        ["WORLD"],
    ),
    (None, "Software Engineer", ["Work from home"], None, R, ["WORLD"]),
    (None, "Software Engineer", ["Home-based, UK"], None, R, ["GB"]),
    (None, "Software Engineer", ["London (Hybrid)"], None, H, []),
    (None, "Software Engineer", ["Hybrid - London"], None, H, []),
    (None, "Software Engineer", ["San Francisco, CA (Hybrid)"], None, H, []),
    (None, "Software Engineer (Hybrid)", ["Berlin"], None, H, []),
    (None, "Software Engineer - Hybrid", [], None, H, []),
    (None, "Software Engineer", ["Remote/Hybrid"], None, H, []),
    (None, "Software Engineer", ["Berlin (On-site)"], None, O, []),
    (None, "Software Engineer (Onsite)", ["Austin, TX"], None, O, []),
    (None, "Technician, In-Office", ["Dallas, TX"], None, O, []),
    # false friends in titles
    (None, "Remote Sensing Engineer", ["Boulder, CO"], None, U, []),
    (None, "Hybrid Cloud Engineer", ["Austin, TX"], None, U, []),
    (None, "Distributed Systems Engineer", ["Seattle, WA"], None, U, []),
    (None, "Software Engineer", ["San Francisco, CA"], None, U, []),
    (None, "Software Engineer", [], None, U, []),
    # description phrases (first 2KB)
    (None, "Software Engineer", ["San Francisco, CA"], "This is a fully remote role.", R, ["US"]),
    (None, "Software Engineer", ["Berlin"], "We are remote-first and async.", R, ["DE"]),
    (None, "Software Engineer", ["Berlin"], "You can work from anywhere in Europe.", R, ["DE"]),
    (
        None,
        "Software Engineer",
        ["Austin, TX"],
        "This is a hybrid role with 3 days a week in the office.",
        H,
        [],
    ),
    (None, "Software Engineer", ["Austin, TX"], "We work two days a week in the office.", H, []),
    (None, "Software Engineer", ["Austin, TX"], "This position is on-site in Austin.", U, []),
    (None, "Software Engineer", ["Austin, TX"], "This is an on-site role.", O, []),
    (None, "Software Engineer", ["Austin, TX"], "This is not a remote role.", U, []),
    (None, "Software Engineer", ["Austin, TX"], "Five days a week in the office.", O, []),
    (None, "Software Engineer", ["Austin, TX"], "Hybrid cloud experience is a plus.", U, []),
    (
        None,
        "Software Engineer",
        ["Austin, TX"],
        "x" * 2100 + " This is a fully remote role.",
        U,
        [],
    ),
]
