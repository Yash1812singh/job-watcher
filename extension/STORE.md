# Chrome Web Store listing: Fill with AI

Publishing gives everyone a one-click **Add to Chrome** button; the extension then connects to their
account by itself the first time they open the Fill with AI website.

## Publish (once)

1. Build the package: `./scripts/package-extension.sh` → `dist/fill-with-ai-<version>.zip`.
2. Create a developer account at https://chrome.google.com/webstore/devconsole (Google account, one-time $5 fee).
3. **New item** → upload the zip.
4. Fill in the listing from this file (below), add at least one 1280×800 screenshot of the panel on an
   application form, and set the privacy policy URL to `https://job-watcher-w5jm.onrender.com/fill/privacy.html`.
5. Submit for review (usually a few days). Once it's published, copy its store URL and set
   `FILL_EXTENSION_STORE_URL` to it in Render → job-watcher → Environment. The website then shows
   **Add to Chrome** instead of the download steps.

For updates, bump `version` in `manifest.json`, re-run the script and upload the new zip.

## Listing

- **Name:** Fill with AI
- **Category:** Workflow & Planning (or Productivity)
- **Short description:** Fills job applications from your Fill with AI profile, with AI for open questions. You review and submit.
- **Description:**
  Pick a job on Fill with AI and its application opens with a panel that fills the form for you: your details,
  your resume, a cover letter for that job, and answers to open questions written from your profile. When something
  needs you (a CAPTCHA, signing in, a question only you can answer) the panel says so and carries on once you've done
  it, including on multi-page forms. It never submits anything: you check the page and press Submit.
  Works on Greenhouse, Lever, Ashby, Workday, SmartRecruiters and most careers sites.

## Why each permission is needed (the review form asks)

- **Host permission (all sites):** job applications are hosted on many different sites (company careers pages and
  hiring systems); the extension fills the form on whichever site the job you picked uses.
- **scripting:** to add the panel and fill the form on the application page.
- **activeTab:** to fill the current page when you click the toolbar button.
- **storage:** to remember your extension key and which tab is an application you started.
- **Remote code:** none. All code is in the package; the server only returns the values to fill in.
- **Data use:** personally identifiable information and website content, used only to fill the form you started;
  not sold, not used for anything else. See the privacy policy.
