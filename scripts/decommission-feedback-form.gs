/**
 * One-shot Google Forms importer for the Azure RBAC Catalog decommission survey.
 *
 * HOW TO USE
 * ──────────
 * 1. Open https://script.google.com → New project.
 * 2. Replace the default ``Code.gs`` content with everything in this file.
 * 3. Click Save (any project name is fine; nothing leaves your Google account).
 * 4. Select the function ``createForm`` from the dropdown next to Run.
 * 5. Click Run. The first time you'll be prompted to authorize the script —
 *    grant access (it only needs Forms scope, nothing else).
 * 6. After ~2 seconds the script logs two URLs:
 *      • Editor URL → use this to tweak questions if you want.
 *      • Public URL → paste this into ``DECOMMISSION_BANNER_FEEDBACK_URL``
 *                     in ``azurerbac/web/constants.py``.
 *
 * The form ships with:
 *   • collectEmail = false       (fully anonymous)
 *   • limitOneResponsePerUser = false (no sign-in required)
 *   • showLinkToRespondAgain = false  (don't nudge for more data)
 *   • acceptingResponses = true
 *
 * NOTHING in this script collects identifying information.
 */

function createForm() {
  var form = FormApp.create('Azure RBAC Catalog — quick feedback before sunset')
    .setDescription(
      "Thanks for using https://rbac-catalog.dev. The site is being decommissioned in mid-June 2026.\n\n" +
      "This form is **anonymous** — we don't collect your name, email, IP address, or any sign-in info. " +
      "Responses are used only to understand how the site was used. Takes about 30 seconds."
    )
    .setCollectEmail(false)
    .setLimitOneResponsePerUser(false)
    .setShowLinkToRespondAgain(false)
    .setAllowResponseEdits(false)
    .setAcceptingResponses(true);

  // ── 1. Primary use ──────────────────────────────────────────────────────
  form.addCheckboxItem()
    .setTitle('How did you use this site? (pick all that apply)')
    .setRequired(false)
    .setChoiceValues([
      'Browse / look up specific built-in roles',
      'Compare two roles side by side',
      'Find a least-privilege role for a set of operations',
      'Use the AI role recommender (LLM, ColBERT, semantic, etc.)',
      'Track when Microsoft adds, updates, or deprecates roles',
      'Search the resource-provider operations catalog',
      'Connect an AI assistant via the MCP server',
      'Other (please add below)',
    ]);

  // ── 2. Frequency ────────────────────────────────────────────────────────
  form.addMultipleChoiceItem()
    .setTitle('How often did you use the site?')
    .setRequired(false)
    .setChoiceValues([
      'Daily',
      'Weekly',
      'Monthly',
      'A few times',
      'Once or twice',
    ]);

  // ── 3. Most-valuable feature ────────────────────────────────────────────
  form.addMultipleChoiceItem()
    .setTitle('What single feature was most valuable to you?')
    .setRequired(false)
    .setChoiceValues([
      'Browsable role list with full permission details',
      'Recent role changes / history tracking',
      'Side-by-side role comparison',
      'Least-privilege role suggestions',
      'AI-powered role recommendations',
      'Operation search across resource providers',
      'MCP server / AI-assistant integration',
      'Other (please add in the free-text question below)',
    ]);

  // ── 4. Replacement plan ─────────────────────────────────────────────────
  form.addMultipleChoiceItem()
    .setTitle('After this site goes away, where will you look for the same information?')
    .setRequired(false)
    .setChoiceValues([
      'Microsoft Learn docs (learn.microsoft.com)',
      'The Azure portal directly',
      "I'd self-host the catalog if the deployment templates were public",
      'A different third-party tool',
      "I don't know yet",
    ])
    .showOtherOption(true);

  // ── 5. One thing to keep ────────────────────────────────────────────────
  form.addParagraphTextItem()
    .setTitle('If you could keep one thing from this site, what would it be?')
    .setHelpText('Free text. One sentence is plenty.')
    .setRequired(false);

  // ── 6. Open feedback ────────────────────────────────────────────────────
  form.addParagraphTextItem()
    .setTitle('Anything else you’d like to share? Suggestions, frustrations, things that worked well…')
    .setRequired(false);

  // Log the URLs the operator needs.
  Logger.log('────────────────────────────────────────────────────────────');
  Logger.log('Editor URL  → ' + form.getEditUrl());
  Logger.log('Public URL  → ' + form.getPublishedUrl());
  Logger.log('Short URL   → ' + form.shortenFormUrl(form.getPublishedUrl()));
  Logger.log('────────────────────────────────────────────────────────────');
  Logger.log('Paste the Public URL (or Short URL) into:');
  Logger.log('  azurerbac/web/constants.py → DECOMMISSION_BANNER_FEEDBACK_URL');
}
