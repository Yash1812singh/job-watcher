import json

import httpx
import pytest
from bs4 import BeautifulSoup

from app import scraper
from app.scraper import (ScanError, detect_ats, extract_json_ld_jobs, extract_link_jobs,
                         fetch_ats_jobs, scan)


@pytest.mark.parametrize("text, expected", [
    ("https://boards.greenhouse.io/airbnb", ("greenhouse", "airbnb")),
    ("https://job-boards.greenhouse.io/figma/jobs/123", ("greenhouse", "figma")),
    ('<script src="https://boards.greenhouse.io/embed/job_board/js?for=stripe"></script>', ("greenhouse", "stripe")),
    ("https://jobs.lever.co/palantir", ("lever", "palantir")),
    ("https://jobs.eu.lever.co/acme/abc", ("lever", "acme")),
    ("https://jobs.ashbyhq.com/openai", ("ashby", "openai")),
    ("https://apply.workable.com/huggingface/", ("workable", "huggingface")),
    ("https://careers.smartrecruiters.com/Visa", ("smartrecruiters", "Visa")),
    ("https://example.com/careers", None),
])
def test_detect_ats(text, expected):
    assert detect_ats(text) == expected


def test_json_ld_job_postings_including_graph():
    html = """
    <script type="application/ld+json">{"@context":"https://schema.org","@type":"JobPosting",
      "title":"Backend Engineer","url":"/jobs/42","identifier":{"@type":"PropertyValue","value":"42"},
      "datePosted":"2026-10-01","jobLocation":{"@type":"Place","address":{"addressLocality":"Bengaluru","addressCountry":"IN"}}}
    </script>
    <script type="application/ld+json">{"@graph":[{"@type":"Organization","name":"Acme"},
      {"@type":"JobPosting","title":"  Data   Analyst ","jobLocationType":"TELECOMMUTE"}]}</script>
    <script type="application/ld+json">not json</script>
    """
    jobs = extract_json_ld_jobs(BeautifulSoup(html, "html.parser"), "https://acme.com/careers")
    by_title = {j.title: j for j in jobs}
    assert set(by_title) == {"Backend Engineer", "Data Analyst"}
    be = by_title["Backend Engineer"]
    assert (be.external_id, be.url, be.location, be.posted_at) == ("42", "https://acme.com/jobs/42", "Bengaluru, IN", "2026-10-01")
    assert by_title["Data Analyst"].location == "Remote"


def test_link_jobs_skips_navigation_and_other_sites():
    html = """
    <a href="/careers">Careers</a>
    <a href="/careers/">View all jobs</a>
    <a href="/careers/senior-java-developer">Senior Java Developer</a>
    <a href="/careers/data-analyst">Data Analyst</a>
    <a href="/blog/how-we-hire">How we hire</a>
    <a href="https://other.com/jobs/spam-role">Spam Role</a>
    <a href="/careers/apply">Apply</a>
    """
    jobs = extract_link_jobs(BeautifulSoup(html, "html.parser"), "https://www.acme.com/careers")
    assert [j.title for j in jobs] == ["Senior Java Developer", "Data Analyst"]


def test_link_jobs_prefers_clear_posting_links_over_general_careers_pages():
    html = """
    <a href="/careers/benefits">Benefits and perks</a>
    <a href="/careers/resources/recruitment-fraud">here.</a>
    <a href="/careers/details/27694">Principal Data Scientist</a>
    <a href="/jobs/123-ml-engineer"><div><h3>ML Engineer</h3><span>Pune</span></div></a>
    <a href="/jobs/123-ml-engineer">ML Engineer</a>
    <a href="https://jobs.lever.co/acme/abc-123">Product Designer</a>
    """
    jobs = extract_link_jobs(BeautifulSoup(html, "html.parser"), "https://www.acme.com/careers")
    assert [(j.title, j.location) for j in jobs] == [
        ("Principal Data Scientist", None), ("ML Engineer", "Pune"), ("Product Designer", None)]


GOOGLE_LIKE = """
<html><head><base href="https://careers.acme.com/applications/"></head><body>
<a href="./jobs/results"><i>work_outline</i> Jobs</a>
<a href="jobs/saved">Saved jobs</a>
<a href="jobs/students"><i>noogler_hat</i></a>
<ul>
  <li><div><h3>Senior Product Manager, Search</h3><span>Bengaluru</span></div>
      <a href="jobs/results/100227769727623878-senior-product-manager" aria-label="Learn more about Senior Product Manager, Search"></a></li>
  <li><div><h3>Data Center Technician</h3></div>
      <a href="jobs/results/114395727597052614-data-center-technician">Learn more</a></li>
</ul></body></html>
"""


def test_link_jobs_respect_base_href_and_take_titles_from_labels_or_card_headings():
    jobs = extract_link_jobs(BeautifulSoup(GOOGLE_LIKE, "html.parser"), "https://careers.acme.com/applications/jobs/results")
    assert [(j.title, j.url) for j in jobs] == [
        ("Senior Product Manager, Search",
         "https://careers.acme.com/applications/jobs/results/100227769727623878-senior-product-manager"),
        ("Data Center Technician",
         "https://careers.acme.com/applications/jobs/results/114395727597052614-data-center-technician"),
    ]


def test_menu_only_page_is_retried_in_the_browser(monkeypatch):
    menu_only = '<a href="/careers/teams">Our teams</a><a href="/careers/students">Students and graduates</a>'
    client = _client({"https://acme.com/careers": menu_only})
    monkeypatch.setattr(scraper, "render_page", lambda url: (url, GOOGLE_LIKE))
    result = scan("https://acme.com/careers", client=client, render=True)
    assert result.method == "links (rendered)" and len(result.jobs) == 2

    # if the browser finds nothing better, keep the menu links but say they may not be jobs
    monkeypatch.setattr(scraper, "render_page", lambda url: (url, "<div></div>"))
    result = scan("https://acme.com/careers", client=client, render=True)
    assert result.weak and "menu" in result.note and [j.title for j in result.jobs] == ["Our teams", "Students and graduates"]


def test_link_jobs_on_hosted_boards_split_title_and_location():
    html = """
    <a href="https://acme.keka.com/careers/jobdetails/49661"><div><p>SME - Data Science</p><p>Gurugram, Haryana</p></div></a>
    <a href="https://acme.keka.com/careers/jobdetails/45726"><div><p>Senior Content Editor</p><p>Remote</p></div></a>
    <a href="https://www.instagram.com/acme">Instagram</a>
    """
    jobs = extract_link_jobs(BeautifulSoup(html, "html.parser"), "https://www.acme.com/careers/")
    assert [(j.title, j.location) for j in jobs] == [
        ("SME - Data Science", "Gurugram, Haryana"), ("Senior Content Editor", "Remote")]


def _client(routes: dict[str, object]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        body = routes.get(str(request.url))
        if body is None:
            return httpx.Response(404)
        if isinstance(body, str):
            return httpx.Response(200, text=body, headers={"content-type": "text/html"})
        return httpx.Response(200, json=body)
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)


def test_greenhouse_feed_is_mapped():
    client = _client({"https://boards-api.greenhouse.io/v1/boards/acme/jobs": {"jobs": [
        {"id": 7, "title": "SRE ", "absolute_url": "https://job-boards.greenhouse.io/acme/jobs/7",
         "location": {"name": "Remote"}, "first_published": "2026-10-02T10:00:00Z"}]}})
    [job] = fetch_ats_jobs(client, "greenhouse", "acme")
    assert (job.external_id, job.title, job.location) == ("7", "SRE", "Remote")


def test_lever_feed_is_mapped():
    client = _client({"https://api.lever.co/v0/postings/acme?mode=json": [
        {"id": "abc", "text": "QA Lead", "hostedUrl": "https://jobs.lever.co/acme/abc",
         "categories": {"location": "Delhi", "team": "Quality"}, "createdAt": 1759000000000}]})
    [job] = fetch_ats_jobs(client, "lever", "acme")
    assert (job.title, job.location, job.department) == ("QA Lead", "Delhi", "Quality")
    assert job.posted_at.startswith("2025-09-27")


def test_workday_boards_named_jobs_and_myworkdaysite_urls_are_detected():
    assert detect_ats("https://paypal.wd1.myworkdayjobs.com/jobs") == ("workday", "paypal/wd1/jobs")
    assert detect_ats("https://wd1.myworkdaysite.com/recruiting/snapchat/snap") == ("workday", "snapchat/wd1/snap")
    assert detect_ats("https://wd1.myworkdaysite.com/en-US/recruiting/wf/WellsFargoJobs") == (
        "workday", "wf/wd1/WellsFargoJobs")
    # the board's own API path in a page's source isn't a site name
    assert detect_ats('fetch("https://acme.wd5.myworkdayjobs.com/wday/cxs/acme/Careers/jobs")') is None


def test_workday_board_is_read_page_by_page():
    assert detect_ats("https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite") == (
        "workday", "nvidia/wd5/NVIDIAExternalCareerSite")
    pages = {0: [{"title": f"Engineer {i}", "externalPath": f"/job/Pune/Engineer_JR{i}", "locationsText": "Pune",
                  "bulletFields": [f"JR{i}"]} for i in range(20)],
             20: [{"title": "Analyst", "externalPath": "/job/Pune/Analyst_JR99", "locationsText": "Pune",
                   "bulletFields": ["JR99"]}]}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/wday/cxs/nvidia/NVIDIAExternalCareerSite/jobs"
        return httpx.Response(200, json={"total": 21, "jobPostings": pages[json.loads(request.content)["offset"]]})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = scan("https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite", client=client, render=False)
    assert result.method == "workday" and len(result.jobs) == 21
    assert (result.jobs[-1].external_id, result.jobs[-1].url) == (
        "JR99", "https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite/job/Pune/Analyst_JR99")


def test_amazon_feed_keeps_the_filters_from_the_added_url():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(dict(request.url.params))
        return httpx.Response(200, json={"hits": 1, "jobs": [{
            "id_icims": "10581102", "title": "SDE II ", "job_path": "/en/jobs/10581102/sde-ii",
            "normalized_location": "Bengaluru, KA, IND", "job_category": "Software Development",
            "posted_date": "October 16, 2026"}]})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = scan("https://www.amazon.jobs/en/search?base_query=data&country=IND&sort=relevant", client=client)
    assert (seen["base_query"], seen["country"], seen["sort"]) == ("data", "IND", "recent")
    [job] = result.jobs
    assert (job.title, job.url, job.posted_at) == ("SDE II", "https://www.amazon.jobs/en/jobs/10581102/sde-ii", "2026-10-16")


def test_eightfold_feed_is_mapped():
    client = _client({"https://explore.jobs.netflix.net/api/apply/v2/jobs?domain=netflix.com&start=0&num=50&sort_by=new": {
        "count": 1, "positions": [{"id": 790318978578, "name": "Senior Tax Analyst", "location": "Mexico City,Mexico",
                                   "t_create": 1792022400, "canonicalPositionUrl": "https://explore.jobs.netflix.net/careers/job/790318978578"}]}})
    result = scan("https://explore.jobs.netflix.net/careers", client=client)
    assert result.method == "eightfold"
    assert (result.jobs[0].title, len(result.jobs[0].posted_at)) == ("Senior Tax Analyst", 10)   # date only


def test_scan_follows_embedded_ats_on_a_company_page():
    client = _client({
        "https://acme.com/careers": '<div id="grnhse_app"></div><script src="https://boards.greenhouse.io/embed/job_board/js?for=acme"></script>',
        "https://boards-api.greenhouse.io/v1/boards/acme/jobs": {"jobs": [
            {"id": 1, "title": "Engineer", "absolute_url": "https://x/1", "location": {"name": "NYC"}}]},
    })
    result = scan("https://acme.com/careers", client=client, render=False)
    assert result.method == "greenhouse"
    assert [j.title for j in result.jobs] == ["Engineer"]


def test_scan_explains_an_empty_javascript_page_without_rendering():
    client = _client({"https://acme.com/careers": "<html><body><div id='root'></div></body></html>"})
    result = scan("https://acme.com/careers", client=client, render=False)
    assert result.jobs == [] and "JavaScript" in result.note


def test_scan_renders_javascript_page_when_plain_html_is_empty(monkeypatch):
    client = _client({"https://acme.com/careers": "<html><body><div id='root'></div></body></html>"})
    rendered = '<div id="root"><a href="/careers/jobs/backend-engineer-42">Backend Engineer</a></div>'
    monkeypatch.setattr(scraper, "render_page", lambda url: ("https://acme.com/careers", rendered))
    result = scan("https://acme.com/careers", client=client, render=True)
    assert result.method == "links (rendered)"
    assert [(j.title, j.url) for j in result.jobs] == [("Backend Engineer", "https://acme.com/careers/jobs/backend-engineer-42")]


def test_scan_falls_back_to_browser_when_site_blocks_plain_requests(monkeypatch):
    blocked = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(403)))
    rendered = '<script type="application/ld+json">{"@type":"JobPosting","title":"SDE II","url":"/jobs/9"}</script>'
    monkeypatch.setattr(scraper, "render_page", lambda url: (url, rendered))
    result = scan("https://acme.com/careers", client=blocked, render=True)
    assert result.method == "json-ld (rendered)" and result.jobs[0].title == "SDE II"


def test_scan_reports_http_errors():
    with pytest.raises(ScanError, match="404"):
        scan("https://acme.com/missing", client=_client({}), render=True)


def test_job_board_found_in_data_the_page_loads(monkeypatch):
    # careers.swiggy.in builds its job cards from /api/jobs.json; the jobs link to SmartRecruiters
    shell = "<html><body><div id='app'>Explore Open Roles</div></body></html>"
    loaded = shell + '\n<script type="application/x-loaded-data">\n{"jobs":[{"t":"Sales Manager I","u":"https://jobs.smartrecruiters.com/SWIGGY/6000000001495547-sales-manager-i"}]}\n</script>'
    monkeypatch.setattr(scraper, "render_page", lambda url: (url, loaded))
    client = _client({
        "https://careers.swiggy.in/explore-jobs": shell,
        "https://api.smartrecruiters.com/v1/companies/SWIGGY/postings?limit=100&offset=0": {"totalFound": 1, "content": [
            {"id": "6000000001495547", "name": "Sales Manager I", "location": {"city": "Bengaluru", "country": "in"},
             "releasedDate": "2026-10-16T07:50:10.496Z"}]},
    })
    result = scan("https://careers.swiggy.in/explore-jobs", client=client, render=True)
    assert result.method == "smartrecruiters (rendered)"
    assert [(j.title, j.location) for j in result.jobs] == [("Sales Manager I", "Bengaluru, in")]


def test_relative_posting_phrases():
    from datetime import datetime, timezone
    from app.scraper import relative_posted
    age = lambda text: (datetime.now(timezone.utc) - datetime.fromisoformat(relative_posted(text))).total_seconds() / 60
    assert 24 <= age("India, Hyderabad · Posted 25 minutes ago") <= 26
    assert 2 * 24 * 60 - 1 <= age("Posted 2 days ago") <= 2 * 24 * 60 + 1
    assert 24 * 60 - 1 <= age("Posted Yesterday") <= 24 * 60 + 1
    assert 30 * 24 * 60 - 1 <= age("Posted 30+ Days Ago") <= 30 * 24 * 60 + 1
    assert relative_posted("Posted Today") is None and relative_posted("Bengaluru") is None


def test_card_posting_time_is_read_and_removed_from_location():
    html = """<a href="/global/en/job/1890123/Applied-AI-Engineer-II"><h3>Applied AI Engineer II</h3>
              <span>India, Telangana, Hyderabad</span><span>· Posted 25 days ago</span></a>
              <a href="/global/en/job/1890124/Data-Scientist"><h3>Data Scientist</h3><span>Bengaluru · Posted 10 minutes ago</span></a>"""
    jobs = extract_link_jobs(BeautifulSoup(html, "html.parser"), "https://jobs.example.com/global/en/search")
    assert [j.location for j in jobs] == ["India, Telangana, Hyderabad", "Bengaluru"]
    assert all(j.posted_at for j in jobs)
