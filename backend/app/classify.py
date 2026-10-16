"""Tag jobs as in India and/or technical (with a role category), so the default view can show
technical jobs in India.

Both are judged from text the job boards give us (location, title, team), so they're good
guesses, not certainties: a Workday job listed as "2 Locations" can't be placed in a country.
Changing the rules below changes RULES_VERSION, and every saved job is re-tagged on startup.
"""
from __future__ import annotations

import hashlib
import re

# ── India ────────────────────────────────────────────────────────────────────

_INDIA_PLACES = [
    "india", "bengaluru", "bangalore", "mumbai", "navi mumbai", "thane", "delhi", "new delhi", "ncr",
    "gurugram", "gurgaon", "noida", "greater noida", "ghaziabad", "faridabad", "hyderabad", "secunderabad",
    "chennai", "pune", "kolkata", "ahmedabad", "gandhinagar", "jaipur", "kochi", "cochin",
    "thiruvananthapuram", "trivandrum", "coimbatore", "chandigarh", "mohali", "indore", "lucknow",
    "nagpur", "mysuru", "mysore", "vadodara", "surat", "visakhapatnam", "vizag", "vijayawada",
    "bhubaneswar", "goa", "panaji", "madurai", "mangaluru", "mangalore", "bhopal", "patna", "ranchi",
    "guwahati", "dehradun", "nashik", "aurangabad", "rajkot", "ludhiana", "kanpur", "agra", "varanasi",
    "tiruchirappalli", "trichy", "hubli", "belgaum", "udaipur", "jodhpur", "raipur", "amritsar",
    # states
    "karnataka", "maharashtra", "telangana", "tamil nadu", "haryana", "uttar pradesh", "kerala",
    "gujarat", "west bengal", "rajasthan", "andhra pradesh", "madhya pradesh", "odisha", "punjab",
    "bihar", "assam", "jharkhand", "uttarakhand", "chhattisgarh",
]
_INDIA_WORDS = re.compile(r"\b(?:" + "|".join(re.escape(p) for p in _INDIA_PLACES) + r")\b", re.I)
# Country codes as boards write them: Amazon "Bengaluru, KA, IND", SmartRecruiters "Pune, in",
# Workday "IN-KA-Bangalore". A bare uppercase "IN" is left alone: that's Indiana in the US.
_INDIA_CODES = re.compile(r"(?:\bIND\b|,\s*in\s*$|^IN[-_ ]|\bIN-[A-Z]{2}\b)")


def is_india(location: str | None) -> bool:
    if not location:
        return False
    return bool(_INDIA_WORDS.search(location) or _INDIA_CODES.search(location))


# ── Technical roles ──────────────────────────────────────────────────────────
# Each category lists (role name shown in the app, pattern matched against job titles).
# Categories are checked in this order and the first match wins, so specific ones come first
# (an "ML Engineer" is AI & Machine Learning and a "Solutions Architect" is IT, Support &
# Solutions, not Software Engineering).

TECH_CATEGORIES: list[tuple[str, list[tuple[str, str]]]] = [
    ("Security", [
        ("Security engineer / analyst / architect", r"security (?:engineer|analyst|architect|researcher|consultant|specialist|lead|manager|operations)"),
        ("Cybersecurity / information security", r"cyber ?security|information security|infosec|cyber"),
        ("Application & product security (AppSec)", r"appsec|application security|product security|devsecops"),
        ("Penetration tester / red team / ethical hacker", r"penetration|pen ?tester|red team|ethical hack|offensive security"),
        ("SOC analyst / incident response / threat intelligence", r"\bsoc\b|incident response|threat (?:intel\w*|hunt\w*|detection)|detection engineer|malware"),
        ("Identity & access (IAM) / cryptography", r"\biam\b|identity (?:and|&) access|cryptograph\w*|\bpki\b"),
        ("Cloud / network security", r"cloud security|network security|vulnerability"),
        ("CISO / security leadership", r"\bciso\b|head of security|chief information security"),
    ]),
    ("AI & Machine Learning", [
        ("Machine learning engineer / MLOps", r"machine learning|\bml ?ops\b|\bml\b"),
        ("AI engineer / Applied AI", r"\bai\b|artificial intelligence|applied ai|gen ?ai|generative"),
        ("LLM / NLP / prompt engineer", r"\bllms?\b|\bnlp\b|natural language|prompt engineer|conversational ai"),
        ("Computer vision / speech / perception", r"computer vision|\bvision engineer|speech|perception|image processing"),
        ("Deep learning / reinforcement learning", r"deep learning|reinforcement learning|neural"),
        ("Research / applied scientist", r"research scientist|applied scientist|ai researcher|research engineer|member of technical staff"),
        ("Recommendation / search / ranking", r"recommend\w* (?:system|engineer)|ranking|search (?:engineer|relevance)|relevance"),
    ]),
    ("Data & Analytics", [
        ("Data scientist / decision scientist", r"data scien\w*|decision scien\w*"),
        ("Data engineer / big data / ETL", r"data engineer\w*|big data|\betl\b|data pipeline|data platform|data infrastructure|spark|hadoop"),
        ("Data analyst / product analyst", r"data analy\w*|product analy\w*|analytics|insights analyst|reporting analyst"),
        ("Analytics engineer / BI developer", r"analytics engineer|business intelligence|\bbi\b (?:developer|engineer|analyst)|power ?bi|tableau|looker"),
        ("Data architect / data modeler / warehouse", r"data architect\w*|data model\w*|data warehouse|snowflake|databricks developer"),
        ("Database administrator / DBA / SQL developer", r"database|\bdba\b|sql developer|\bsql\b|oracle developer|postgres|mongodb"),
        ("Quant / statistician", r"quant(?!ity)\w*|statistic\w*|econometric|actuar\w* (?:data|analytics)"),
        ("Data governance / data quality", r"data (?:governance|quality|steward\w*|management|operations)"),
    ]),
    ("Cloud, DevOps & Infrastructure", [
        ("DevOps engineer", r"dev ?ops"),
        ("Site reliability engineer (SRE) / production engineer", r"\bsre\b|site reliability|production engineer|reliability engineer"),
        ("Platform / infrastructure engineer", r"platform engineer|infrastructure|infra engineer"),
        ("Cloud engineer / architect (AWS, Azure, GCP)", r"cloud|\baws\b|azure|\bgcp\b|google cloud"),
        ("Kubernetes / containers / virtualization", r"kubernetes|\bk8s\b|docker|container|virtuali[sz]ation|vmware"),
        ("Build / release engineer", r"build engineer|release engineer|ci ?/? ?cd|build and release"),
        ("Systems engineer / Linux / Unix administrator", r"systems? (?:engineer|administrator|admin|architect)|linux|unix|sysadmin"),
        ("Storage / backup / observability", r"storage engineer|backup|observability|monitoring engineer"),
        ("Data center engineer", r"data cent(?:er|re) (?:engineer|operations engineer|architect)"),
    ]),
    ("QA & Testing", [
        ("QA engineer / quality assurance", r"\bqa\b|quality assurance|quality engineer"),
        ("SDET / test automation", r"\bsdet\b|test automation|automation test\w*|qa automation"),
        ("Test engineer / tester / test lead", r"test (?:engineer|lead|manager|analyst|architect)|tester|testing"),
        ("Performance / load testing", r"performance test\w*|load test\w*"),
    ]),
    ("Hardware, Embedded & Semiconductor", [
        ("Embedded / firmware engineer", r"embedded|firmware|\brtos\b|bsp"),
        ("Hardware / electronics engineer", r"hardware|electronics?|\bpcb\b|board design|circuit"),
        ("VLSI / ASIC / FPGA / RTL", r"vlsi|\basic\b|\bfpga\b|\brtl\b|\bsoc\b design|silicon|semiconductor"),
        ("Design verification / physical design / DFT", r"design verification|\bdv\b engineer|verification engineer|physical design|floor ?plan|place and route|\bdft\b|timing|layout engineer|analog|mixed[- ]signal"),
        ("CPU / GPU / chip architect", r"\b(?:cpu|gpu|chip) (?:architect|design|engineer)|gpu|compiler|kernel|performance architect"),
        ("Signal processing / RF / wireless hardware", r"signal processing|\bdsp\b|\brf\b|radio frequency|antenna"),
        ("Robotics / automation / controls / PLC", r"robotic\w*|mechatronic\w*|controls? engineer|\bplc\b|automation engineer|scada|autonomous"),
        ("Electrical / power electronics engineer", r"electrical engineer|power electronics|battery|\bev\b engineer"),
    ]),
    ("Networking & Telecom", [
        ("Network engineer / architect / administrator", r"network(?:ing)? (?:engineer|architect|administrator|admin|specialist|consultant|operations)|\bnoc\b"),
        ("Telecom / 5G / wireless / RAN", r"telecom\w*|\b5g\b|\blte\b|wireless|\bran\b|optical|transport network"),
        ("Routing & switching / SD-WAN", r"routing|switching|sd-?wan|\bccna\b|\bccnp\b"),
    ]),
    ("IT, Support & Solutions", [
        ("IT support / helpdesk / desktop support", r"\bit (?:support|engineer|specialist|administrator|admin|analyst|manager|operations|technician)|help ?desk|service desk|desktop support|end user computing"),
        ("Technical support / application & production support", r"technical support|support engineer|escalation engineer|application support|production support|l[123] support"),
        ("Solutions / sales / pre-sales engineer", r"solutions? (?:engineer|architect|consultant)|sales engineer|pre-?sales|customer engineer|field application engineer|forward deployed"),
        ("Technical consultant / implementation engineer", r"technical consultant|implementation (?:engineer|consultant|specialist)|integration consultant|technology consultant"),
        ("Technical account manager / customer reliability", r"technical account manager|customer reliability|technical customer success"),
        ("Developer advocate / developer relations", r"developer (?:advocate|relations|evangelist)|devrel"),
        ("Technical writer / documentation engineer", r"technical writer|documentation engineer|api writer"),
        ("Systems / business / IT analyst", r"systems analyst|business systems analyst|business analyst|\bit analyst|technical analyst|functional analyst"),
    ]),
    ("Software Engineering", [
        ("Software engineer / developer / programmer", r"software|developer|programmer|\bsde\s*\d*\b|\bswe\b|coder"),
        ("Backend engineer", r"back[\s-]?end"),
        ("Frontend / UI engineer", r"front[\s-]?end|\bui (?:engineer|developer)|web developer|javascript|typescript|react|angular|vue"),
        ("Full-stack engineer", r"full[\s-]?stack"),
        ("Mobile engineer (Android, iOS, Flutter, React Native)", r"mobile|android|\bios\b|flutter|react native|swift|kotlin"),
        ("Language-specific developer (Java, Python, Go, .NET, C++, Node, PHP, Ruby, Rust, Scala)",
         r"\bjava\b|python|golang|\bgo (?:developer|engineer)|\.net|c\+\+|\bc#|node(?:\.?js)?|\bphp\b|ruby|rust|scala|elixir|haskell"),
        ("Game developer / graphics engineer", r"game (?:developer|engineer|programmer)|gameplay|graphics engineer|unity developer|unreal"),
        ("Blockchain / Web3 / smart contracts", r"blockchain|web3|smart contract|solidity|crypto engineer|protocol engineer"),
        ("Enterprise platforms (SAP, Salesforce, ServiceNow, Oracle, Workday, Dynamics)",
         r"\bsap\b|abap|salesforce (?:developer|engineer|architect|consultant|admin\w*)|servicenow|oracle (?:developer|consultant|apps)|workday (?:consultant|integration|developer)|dynamics 365|pega|mulesoft|tibco"),
        ("Mainframe / legacy (COBOL)", r"mainframe|cobol"),
        ("API / integration / microservices engineer", r"\bapi\b|integration engineer|integration developer|microservices"),
        ("Software architect / principal / staff engineer", r"architect|principal engineer|staff engineer|distinguished engineer|fellow"),
        ("Engineering manager / tech lead / CTO", r"engineering manager|manager,? engineering|tech(?:nical)? lead|head of engineering|director,? (?:of )?engineering|vp,? (?:of )?engineering|\bcto\b|engineering lead"),
    ]),
    ("Product & Tech Management", [
        ("Technical program manager (TPM)", r"technical program manager|\btpm\b|program manager,? (?:engineering|technical)"),
        ("Product manager / product owner", r"product manager|product owner|product lead|head of product|group product manager"),
        ("Scrum master / agile coach / delivery manager", r"scrum master|agile coach|delivery manager|release manager|engineering program"),
        ("IT project manager / IT manager", r"\bit project manager|technical project manager|project manager,? (?:it|software|technology)|it manager"),
    ]),
    ("Design (UI/UX)", [
        ("UX / UI / product designer", r"\bux\b|\bui\b|user experience|product designer|interaction designer|visual designer|ux researcher|user research"),
    ]),
    ("Core Engineering", [
        ("Mechanical / electrical / civil / chemical / other engineers", r"engineer(?:ing)?s?"),
    ]),
]

# Titles that contain a tech word but aren't technical jobs.
_NOT_TECH = re.compile(r"""\b(?:
    recruit\w* | talent | sourcer | headhunt\w* | technician | data entry | account executive
  | sales (?:manager|associate|representative|executive|development|director|lead|specialist)
  | business development | marketing | brand | content (?:writer|marketing) | copywriter | social media
  | accountant | accounting | finance manager | financial analyst | payroll | tax | audit\w*
  | legal | counsel | paralegal | compliance officer | nurse | physician | pharmacist | driver | warehouse
  | delivery (?:associate|executive|partner) | store (?:manager|associate) | cashier | chef | cook
  | customer (?:care|service) (?:executive|representative|associate|agent) | call cent(?:er|re)
  | people partner | hr (?:business partner|manager|generalist|executive) | human resources
  | receptionist | office (?:manager|assistant) | executive assistant | administrative assistant
  | ai\ trainer | trainer\ project | annotat\w* | data\ label\w* | labeler | chief\ of\ staff
)\b""", re.I | re.X)
# Exceptions to the list above that are technical: "Sales Engineer", "Marketing Data Scientist"…
_STILL_TECH = re.compile(r"\b(?:sales engineer|solutions? engineer|pre-?sales|data scien\w*|data engineer\w*|"
                         r"software|developer|machine learning|security engineer|marketing (?:technology|engineer))\b", re.I)
_TECH_TEAM = re.compile(r"\b(?:engineering|technology|tech|r&d|research|data|software|product development|it|information technology)\b", re.I)

_CATEGORY_PATTERNS = [
    (name, re.compile(r"\b(?:" + "|".join(f"(?:{p})" for _, p in roles) + r")", re.I))
    for name, roles in TECH_CATEGORIES
]

# Bumps automatically whenever the rules change, so saved jobs get re-tagged (see db.init).
RULES_VERSION = hashlib.sha1(
    repr((_INDIA_PLACES, _INDIA_CODES.pattern, TECH_CATEGORIES, _NOT_TECH.pattern, _STILL_TECH.pattern,
          _TECH_TEAM.pattern)).encode()).hexdigest()[:12]


def tech_category(title: str | None, department: str | None = None) -> str | None:
    """The technical category of a job title ('Software Engineering', 'Data & Analytics', …),
    or None if it isn't a technical role."""
    title = re.sub(r"[_/|]", " ", title or "")   # "Developer_EDC3", "ML/AI Engineer"
    if _NOT_TECH.search(title) and not _STILL_TECH.search(title):
        return None
    for name, pattern in _CATEGORY_PATTERNS:
        if pattern.search(title):
            return name
    if department and _TECH_TEAM.search(department) and not _NOT_TECH.search(department):
        return "Technology (by team)"
    return None


def is_tech(title: str | None, department: str | None = None) -> bool:
    return tech_category(title, department) is not None


def categories_for_display() -> list[dict]:
    """Category headings and the roles under each, for the app's 'What counts as technical?' list."""
    return [{"category": name, "roles": [role for role, _ in roles]} for name, roles in TECH_CATEGORIES]
