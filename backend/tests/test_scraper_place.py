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
