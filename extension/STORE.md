# Publishing Fill with AI (one-click install)

Once it's in a store, people get a one-click install button and the extension connects to their
account by itself the first time they open the Fill with AI website. The same package works for all three:

```bash
./scripts/package-extension.sh      # → dist/fill-with-ai-<version>.zip
```

| Store | Cost | Who it's for |
|---|---|---|
| Microsoft Edge Add-ons | **free** | Edge users |
| Firefox Add-ons | **free** | Firefox users |
| Chrome Web Store | $5 one-time | Chrome users (Chrome only installs extensions from its own store) |

Each store asks for the listing text below, a 1280×800 screenshot of the panel on an application form,
and the privacy policy URL: `https://job-watcher-w5jm.onrender.com/fill/privacy.html`.

## Microsoft Edge Add-ons (free)

1. Register at https://partner.microsoft.com/dashboard/microsoftedge (Microsoft account; individual
   developer registration is free).
2. **Create new extension** → upload the zip → fill in the listing → **Publish**. Review usually takes a few days.
3. When it's live, set `FILL_EXTENSION_EDGE_URL` to its store page in Render → job-watcher → Environment.
   Edge users then get **Get it for Edge** on the website.

## Firefox Add-ons (free)

1. Sign in at https://addons.mozilla.org/developers/ (free Mozilla account).
2. **Submit a New Add-on** → "On this site" → upload the same zip. The listing asks the same questions;
   the data it uses is declared in the manifest (personal information and page content, used only to fill your forms).
3. When it's approved, set `FILL_EXTENSION_FIREFOX_URL` to its page on addons.mozilla.org.

## Chrome Web Store ($5 one-time)

1. Create a developer account at https://chrome.google.com/webstore/devconsole.
2. **New item** → upload the zip → listing → submit for review.
3. When it's live, set `FILL_EXTENSION_STORE_URL` to its store page.

Until a store has it, the website offers the download (unzip, then "Load unpacked" in Chrome or Edge).
For updates, bump `version` in `manifest.json`, re-run the script and upload the new zip to each store.

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
