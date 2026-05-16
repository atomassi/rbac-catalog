# Decommission feedback form — questions

> Use this if you'd rather build the Google Form by hand instead of running
> [`decommission-feedback-form.gs`](decommission-feedback-form.gs).
> Either way, the published URL goes into
> `azurerbac/web/constants.py → DECOMMISSION_BANNER_FEEDBACK_URL`.

## Form settings

- **Title**: `Azure RBAC Catalog — quick feedback before sunset`
- **Description**:
  > Thanks for using https://rbac-catalog.dev. The site is being decommissioned in mid-June 2026.
  >
  > This form is **anonymous** — we don't collect your name, email, IP address, or any sign-in info. Responses are used only to understand how the site was used. Takes about 30 seconds.
- **Collect email addresses**: **Do not collect** (anything else makes the form non-anonymous).
- **Limit to 1 response**: off (don't require sign-in).
- **Show link to submit another response**: off.
- **Allow response editing after submit**: off.

## Questions

### 1. How did you use this site? (pick all that apply)

Type: **Checkboxes** · Required: no

- Browse / look up specific built-in roles
- Compare two roles side by side
- Find a least-privilege role for a set of operations
- Use the AI role recommender (LLM, ColBERT, semantic, etc.)
- Track when Microsoft adds, updates, or deprecates roles
- Search the resource-provider operations catalog
- Connect an AI assistant via the MCP server
- Other (please add below)

### 2. How often did you use the site?

Type: **Multiple choice** · Required: no

- Daily
- Weekly
- Monthly
- A few times
- Once or twice

### 3. What single feature was most valuable to you?

Type: **Multiple choice** · Required: no

- Browsable role list with full permission details
- Recent role changes / history tracking
- Side-by-side role comparison
- Least-privilege role suggestions
- AI-powered role recommendations
- Operation search across resource providers
- MCP server / AI-assistant integration
- Other (please add in the free-text question below)

### 4. After this site goes away, where will you look for the same information?

Type: **Multiple choice** · Required: no · **"Other" option enabled**

- Microsoft Learn docs (learn.microsoft.com)
- The Azure portal directly
- I'd self-host the catalog if the deployment templates were public
- A different third-party tool
- I don't know yet
- *Other (free text)*

### 5. If you could keep one thing from this site, what would it be?

Type: **Paragraph (long answer)** · Required: no
Help text: *Free text. One sentence is plenty.*

### 6. Anything else you'd like to share?

Type: **Paragraph (long answer)** · Required: no
Title: *Anything else you'd like to share? Suggestions, frustrations, things that worked well…*

## What NOT to add

To keep this form fully GDPR-anonymous, do **not** add any of:

- Name, email, company, job title, country
- "How can we reach you?" / mailing-list opt-in
- Google Analytics / linked Google Sheet shared publicly
- "Required" flag on any question (let people skip what doesn't apply)
- An iframe embed on your own site (the link should open
  `docs.google.com` in a new tab; that keeps Google's session cookies
  on Google's domain, not yours)
