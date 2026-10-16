import pytest

from app.classify import categories_for_display, is_india, is_tech, tech_category


@pytest.mark.parametrize("location", [
    "Bengaluru", "Bangalore, Karnataka, India", "Gurugram, Haryana", "Remote - India", "Bengaluru, KA, IND",
    "Pune, in", "IN-KA-Bangalore", "India - Hyderabad", "Noida, Uttar Pradesh", "New Delhi", "Mumbai, Maharashtra, India",
])
def test_india_locations(location):
    assert is_india(location)


@pytest.mark.parametrize("location", [
    None, "", "Indianapolis, IN", "San Francisco, CA", "Remote - USA", "London, England, GBR", "2 Locations",
    "Singapore", "Indiana", "Berlin, de",
])
def test_not_india_locations(location):
    assert not is_india(location)


@pytest.mark.parametrize("title, category", [
    # Software Engineering
    ("Software Engineer", "Software Engineering"),
    ("Senior Backend Developer", "Software Engineering"),
    ("SDE II", "Software Engineering"),
    ("Full-stack Software Engineer", "Software Engineering"),
    ("Senior TypeScript Developer_EDC3", "Software Engineering"),
    ("iOS Developer", "Software Engineering"),
    ("Flutter Developer", "Software Engineering"),
    ("Java Developer", "Software Engineering"),
    ("SAP ABAP Consultant", "Software Engineering"),
    ("Salesforce Developer", "Software Engineering"),
    ("Blockchain Engineer", "Software Engineering"),
    ("Engineering Manager", "Software Engineering"),
    ("Staff Software Engineer, Backend - FinHub", "Software Engineering"),
    ("Tech Lead", "Software Engineering"),
    ("Gameplay Programmer", "Software Engineering"),
    # Data & Analytics
    ("Data Scientist", "Data & Analytics"),
    ("Data Engineer II", "Data & Analytics"),
    ("Data Analyst, Business Finance", "Data & Analytics"),
    ("Analytics Engineer", "Data & Analytics"),
    ("Power BI Developer", "Data & Analytics"),
    ("Database Administrator", "Data & Analytics"),
    ("Quantitative Researcher", "Data & Analytics"),
    ("Product Analyst", "Data & Analytics"),
    # AI & Machine Learning
    ("Machine Learning Engineer", "AI & Machine Learning"),
    ("Applied AI Engineer II", "AI & Machine Learning"),
    ("AI/ML Expert", "AI & Machine Learning"),
    ("LLM Research Engineer", "AI & Machine Learning"),
    ("Computer Vision Engineer", "AI & Machine Learning"),
    ("AI Research Scientist", "AI & Machine Learning"),
    ("Member of Technical Staff, Kubernetes and Bare Metal Engineering", "AI & Machine Learning"),
    ("Prompt Engineer", "AI & Machine Learning"),
    # Cloud, DevOps & Infrastructure
    ("DevOps Lead", "Cloud, DevOps & Infrastructure"),
    ("Site Reliability Engineer", "Cloud, DevOps & Infrastructure"),
    ("Platform Engineer", "Cloud, DevOps & Infrastructure"),
    ("AWS Cloud Architect", "Cloud, DevOps & Infrastructure"),
    ("Linux System Administrator", "Cloud, DevOps & Infrastructure"),
    ("Senior Systems Engineer (Pre Sales)", "Cloud, DevOps & Infrastructure"),
    ("Release Engineer", "Cloud, DevOps & Infrastructure"),
    # Security
    ("Security Engineer", "Security"),
    ("SOC Analyst L2", "Security"),
    ("Penetration Tester", "Security"),
    ("Cybersecurity Consultant", "Security"),
    ("Application Security Engineer", "Security"),
    # QA & Testing
    ("QA Automation Engineer", "QA & Testing"),
    ("SDET", "QA & Testing"),
    ("Manual Tester", "QA & Testing"),
    ("Performance Test Engineer", "QA & Testing"),
    # Hardware, Embedded & Semiconductor
    ("Embedded Software Engineer", "Hardware, Embedded & Semiconductor"),
    ("Firmware Engineer", "Hardware, Embedded & Semiconductor"),
    ("Electronics Hardware Design Engineer", "Hardware, Embedded & Semiconductor"),
    ("ASIC Design Verification Engineer", "Hardware, Embedded & Semiconductor"),
    ("Physical Design Engineer", "Hardware, Embedded & Semiconductor"),
    ("Senior System Performance Engineer, GPU", "Hardware, Embedded & Semiconductor"),
    ("PLC Automation Engineer-ECF2", "Hardware, Embedded & Semiconductor"),
    ("Robotics Engineer", "Hardware, Embedded & Semiconductor"),
    ("CPU Senior Floor Plan Engineer", "Hardware, Embedded & Semiconductor"),
    ("Escalation Engineer - DLP", "IT, Support & Solutions"),
    # Networking & Telecom
    ("Network Engineer", "Networking & Telecom"),
    ("5G RAN Engineer", "Networking & Telecom"),
    # IT, Support & Solutions
    ("IT Support Engineer", "IT, Support & Solutions"),
    ("Technical Support Engineer", "IT, Support & Solutions"),
    ("Sales Engineer", "IT, Support & Solutions"),
    ("Solutions Architect", "IT, Support & Solutions"),
    ("Forward Deployed Engineer", "IT, Support & Solutions"),
    ("Developer Advocate", "IT, Support & Solutions"),
    ("Technical Writer", "IT, Support & Solutions"),
    ("Lead Business Analyst", "IT, Support & Solutions"),
    ("Customer Reliability Engineer - Infrastructure", "Cloud, DevOps & Infrastructure"),
    # Product & Tech Management
    ("Technical Program Manager III", "Product & Tech Management"),
    ("Senior Product Manager, Search", "Product & Tech Management"),
    ("Scrum Master", "Product & Tech Management"),
    # Design
    ("Product Designer", "Design (UI/UX)"),
    ("UX Researcher", "Design (UI/UX)"),
    # Core Engineering
    ("Mechanical Design Engineer", "Core Engineering"),
    ("Civil Engineer", "Core Engineering"),
])
def test_technical_titles_and_categories(title, category):
    assert tech_category(title) == category


@pytest.mark.parametrize("title", [
    "Account Executive", "Sales Manager I", "Senior Manager, Safety", "Logistics Graduate", "Recruiter",
    "Technical Recruiter", "Data Center Technician", "Operations Supervisor, Quick Commerce",
    "Commercial Account Executive - Australia", "Regulatory Affairs", "Manager, Customer Care",
    "Sr. People Partner", "Lead Client Growth Manager", "Financial Analyst", "Product Marketing Manager",
    "Quantity Surveyor", "Field Service Technician (Maintenance)", "Content Writer", "HR Business Partner",
    "Executive Assistant", "Delivery Associate",
    "STEM Specialist (Fluent in Hindi) - Freelance AI Trainer Project", "Chief of Staff, AI Hive",
    "Data Annotator",
])
def test_non_technical_titles(title):
    assert not is_tech(title)


def test_team_counts_when_the_title_is_vague():
    assert tech_category("Associate", department="Engineering") == "Technology (by team)"
    assert not is_tech("Associate", department="Finance")


def test_every_category_has_roles_for_the_app():
    cats = categories_for_display()
    assert len(cats) >= 10 and all(c["roles"] for c in cats)
    assert sum(len(c["roles"]) for c in cats) >= 70
