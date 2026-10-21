# Fill with AI (Chrome extension, local only)

Open a job application, click the extension (or press **Alt+Shift+F**): it fills the page from your
profile, writes answers to open questions with AI, attaches your resume, and highlights in yellow
whatever it couldn't fill. **It never clicks Next or Submit**: you check the page and do that. On a
multi-page form, click it again on each page.

## Set up (once)

1. Your details live in the AutoApply project: `~/Desktop/Python/autoapply/config/user_profile.yaml`
   (add your LinkedIn URL there — most forms require it), resume at `autoapply/data/resume.pdf`,
   Groq key in `autoapply/.env` (`GROQ_API_KEY=…`).
2. Update the local Job Watcher service so it has the Fill-with-AI endpoints and a copy of those files
   (background services can't read Desktop): `./scripts/install-service.sh`.
   Run it again whenever you edit the profile or resume.
3. Chrome → `chrome://extensions` → turn on **Developer mode** → **Load unpacked** → pick this
   `extension/` folder. Pin it to the toolbar.
4. Right-click the icon → **Options** → **Save and test**: it should say who it's filling as.

## How it decides

Fixed rules answer name, contact, links, location, notice period, salary, work authorization,
sponsorship and relocation from the profile, decline diversity questions, and tick consent boxes.
Open questions and unfamiliar multiple-choice questions go to the AI (Groq), which answers from the
profile only. A field the profile has no value for (e.g. an empty LinkedIn) is left for you rather
than made up. Search-as-you-type dropdowns (city, country) are typed in and the matching suggestion
picked; if none appears, the field says "pick a suggestion".

Fields it fills get a green outline; ones that need you get a yellow one, and the first is scrolled
into view. Problems show in the page's developer console as "Fill with AI: …".

## Many users

On a hosted Job Watcher, anyone can open **/fill**, create an account, enter their details, upload
their resume, download this extension (already pointed at that site) and create an extension key.
The key goes in the extension's Options; each person's extension fills forms with their own profile.
