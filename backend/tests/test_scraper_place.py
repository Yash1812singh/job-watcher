from app.classify import is_india
from app.scraper import place


def test_place_uses_every_location_the_country_and_remote():
    assert place(["Dubai", "Dubai", "Noida, Uttar Pradesh"]) == "Dubai / Noida, Uttar Pradesh"
    assert is_india(place(["Dubai", "Noida, Uttar Pradesh"]))                 # a second place in India counts
    assert place(["Remote"], "India", remote=True) == "Remote, India"
    assert is_india(place([None], "in", remote=True))                          # SmartRecruiters: remote, country "in"
    assert place(["Austin"], remote=True) == "Austin (Remote)"
    assert not is_india(place(["Remote"], remote=True))                        # remote with no country: not India
    assert place([None]) is None


def test_long_location_lists_are_shortened_but_keep_india():
    text = place(["Dubai", "London", "Paris", "Berlin", "Tokyo", "Pune, Maharashtra"])
    assert text.startswith("Dubai / London / Paris +3 more") and is_india(text)


def test_more_hiring_tools_are_recognised():
    from app.scraper import detect_ats
    assert detect_ats("https://niyo.keka.com/careers/jobdetails/1") == ("keka", "niyo")
    assert detect_ats("https://cars24.darwinbox.in/ms/candidate/careers/abc") == ("darwinbox", "cars24.darwinbox.in")
    assert detect_ats("https://kissflow.zohorecruit.in/jobs/Careers/123/x") == ("zohorecruit", "kissflow.zohorecruit.in/Careers")
    assert detect_ats("https://haptik.freshteam.com/jobs/abc") == ("freshteam", "haptik")
    assert detect_ats("https://bunq.recruitee.com/o/x") == ("recruitee", "bunq")
    assert detect_ats("https://freshprints.bamboohr.com/careers/28") == ("bamboohr", "freshprints")
    assert detect_ats("https://ishir.breezy.hr/p/1") == ("breezy", "ishir")
    assert detect_ats("https://brutindia.teamtailor.com/jobs/1-x") == ("teamtailor", "brutindia")
    assert detect_ats("https://jobs.jobvite.com/egnyte/job/oNd") == ("jobvite", "egnyte")
    assert detect_ats('<script src="https://cdn.recruitee.com/widget.js">') is None   # the tool's own files
    assert detect_ats("https://www.keka.com/careers") is None
