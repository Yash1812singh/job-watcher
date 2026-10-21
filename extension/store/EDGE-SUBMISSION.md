# Submit Fill with AI to Microsoft Edge Add-ons (free)

Everything to upload is in this folder, and every text box below is ready to copy.

1. Go to https://partner.microsoft.com/dashboard/microsoftedge and sign in with any Microsoft account.
   The first time, choose **Register** as an **Individual** developer (free) and fill in your name and email.
2. **Create new extension** → upload `dist/fill-with-ai-2.2.1.zip` (make it with `./scripts/package-extension.sh`).
3. Fill in the pages as below, then **Publish**. Microsoft reviews it (usually up to 7 days) and emails you.
4. When it's live, open its page, copy the address (https://microsoftedge.microsoft.com/addons/detail/…), and set
   `FILL_EXTENSION_EDGE_URL` to it in Render → job-watcher → Environment. Edge users then get a one-click
   **Get it for Edge** button on the Fill with AI page.

## Availability
- **Visibility:** Public · **Markets:** all

## Properties
- **Category:** Productivity
- **Privacy policy:** Yes → `https://job-watcher-w5jm.onrender.com/fill/privacy.html`
- **Website:** `https://job-watcher-w5jm.onrender.com/fill/`
- **Support contact:** your email address
- **Mature content:** No

## Store listing (English)
- **Display name:** Fill with AI
- **Description:**
  > Apply to jobs without retyping your details. Pick a job on Fill with AI and its application opens with a panel that fills the form for you: your contact details, work history, salary and notice period, your resume, a cover letter written for that job, and answers to open questions drafted from your profile.
  >
  > When something needs you (a CAPTCHA, signing in to the company's site, a question only you can answer), the panel lists it and takes you there; once you've done it, it carries on by itself, including the next pages of multi-page applications. It never submits anything: you check the page and press Submit.
  >
  > Works on Greenhouse, Lever, Ashby, Workday, SmartRecruiters and most careers sites. On any job page, click the toolbar button or press Alt+Shift+F.
  >
  > You need a free Fill with AI account (https://job-watcher-w5jm.onrender.com/fill/); the extension connects to it by itself.
- **Short description:** Fills job applications from your Fill with AI profile, with AI for open questions. You review and submit.
- **Store logo (300×300):** `logo-300.png`
- **Screenshots (1280×800):** `screenshot-1-panel.png`, `screenshot-2-jobs.png`
- **Search terms:** job application, autofill, resume, cover letter, job search

## Notes for certification (paste into "Notes for certification")
> To test: open https://job-watcher-w5jm.onrender.com/fill/, create an account (any email), fill the Profile (name, email, phone, LinkedIn) and upload any PDF as the resume. With the extension installed, the Extension tab shows "The extension is connected". In Jobs, click "Apply with AI" on any job: the application opens and a panel at the top fills the form. The extension never submits forms. Host permission for all sites is needed because job applications are hosted on many different domains (company careers sites and hiring systems); it only reads and fills the page of an application the user starts.
