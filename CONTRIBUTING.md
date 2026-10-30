# Contributing

Thanks for helping improve these wishlists. The quickest ways to contribute are to **suggest a roll** or **report a mistake**. Code and documentation improvements are welcome as pull requests.

- [Suggest a roll](#suggest-a-roll)
- [Report a mistake](#report-a-mistake)
- [Contribute code or documentation](#contribute-code-or-documentation)
- [Licensing of contributions](#licensing-of-contributions)
- [Attribution and privacy](#attribution-and-privacy)
- [Conduct](#conduct)
- [Security](#security)

## Suggest a roll

Roll suggestions go through the [recommendation issue form](https://github.com/rox4H/automated-dim-wishlists/issues/new?template=recommendation-proposal.yml), not through pull requests. Submit **one roll per issue**. A bot validates the issue and, if it passes, publishes the roll to the community wishlist.

A roll is one weapon version plus four perks, one for each slot: a barrel **or** a scope/sight, a magazine, trait 1, and trait 2.

### Fill in the form

| Field | What to enter |
| --- | --- |
| Mode | PvE or PvP. |
| Name | A short, meaningful name for the roll, such as `Solar add-clear`. |
| Description | One or two sentences on what the roll is for. |
| Weapon | The exact weapon **version** ID, without `.json` and without a path. It is the weapon name in lowercase with hyphens, then `_s` and the season number, for example `avalanche_s5` or `abyss-defiant_s22`. Each seasonal version of a weapon has its own ID and its own perk pool. |
| Perk 1–4 | The four perks, by English display name or by perk ID. The order doesn't matter: the bot puts each perk in the slot where it can roll on that weapon. |

How perks are matched:

- Names are compared ignoring case, accents, and punctuation.
- If a name matches several perks, the bot asks for the exact perk ID.
- Give exactly one perk per slot. If a perk could fill more than one slot on that weapon, the bot can't place it unambiguously and rejects the roll.

### Before you submit

- Check that the roll isn't already listed. A roll with the same mode, weapon version, and four perks as an entry in either wishlist is rejected as a duplicate.
- Placeholder text such as `test`, `n/a`, or `TBD` in the name or description is rejected.
- Submit your own suggestion. Don't paste another list or other people's work. See [Licensing of contributions](#licensing-of-contributions).
- Don't include private information.
- **Don't edit or reopen the issue after you submit it.** The bot checks the issue exactly as it was created. An edit or reopen invalidates it and the bot closes it. To change anything, open a new issue.

### What happens next

The bot comments on your issue with the result and adds a label:

| Result | Label | What it means |
| --- | --- | --- |
| Ready to publish | `intake:ready-to-publish` | The weapon version, its complete perk pool, and your four perks are verified in the catalogue. The bot opens a publication pull request, waits for CI, merges it, then closes your issue as completed and adds `intake:published`. |
| Needs maintainer review | `needs-maintainer-review` | The mechanical checks passed, but the weapon, its perk pool, or a perk isn't verified in the catalogue yet. The issue stays open and nothing is published until the maintainer verifies the missing data. |
| Action required | `intake:action-required` | Something blocking was found, such as an unknown weapon ID, a perk that can't roll in that slot, a duplicate, or placeholder text. The comment lists the reasons and the bot closes the issue. Fix the problem in a **new** issue. |
| Invalidated | `intake:submission-invalidated` | The issue was edited or reopened after creation. It will not be published. Open a new issue. |
| Integration blocked | `intake:integration-blocked` | The validator or publisher hit an unexpected problem. The issue stays open for the maintainer to look at. |

"Validated" here is mechanical only. It confirms the weapon version exists and the perks can roll in their slots. It is not a review of the roll's quality, and it makes no claim that the roll is optimal or tested.

## Report a mistake

Open a [correction issue](https://github.com/rox4H/automated-dim-wishlists/issues/new?template=data-correction.yml). Give the weapon name or version ID and describe what is wrong. Don't include private information.

## Contribute code or documentation

For anything larger than a small fix, open an issue first so we can agree on the approach.

### Set up

You need Python 3.11, 3.12, or 3.13, and Node.js 22.

```bash
git clone https://github.com/rox4H/automated-dim-wishlists
cd automated-dim-wishlists
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
python -m pip install -r requirements-dev.txt
```

### Run the checks

CI runs these on every pull request, and you can run them locally. Some of them read the weapon, perk, and perk-pool catalogue, so they need internet access.

| Command | Needs internet | What it checks |
| --- | --- | --- |
| `python scripts/check_workflow_security.py` | No | Workflow security rules: pinned actions, token permissions, publisher isolation. |
| `python -m unittest discover -s tests -v` | Mostly | Python tests. `test_current_repository_is_valid` validates the repository against the live catalogue. |
| `python scripts/validate_json.py` | Yes | Schemas and cross-references of every recommendation against the catalogue. |
| `python scripts/export_dim_wishlist.py --source curated --check` | Yes | The curated export is up to date. |
| `python scripts/export_dim_wishlist.py --source community --check` | Yes | The community export is up to date. |
| `node --test tests/test_generate_weapon_perk_pools.mjs tests/test_recommendation_intake_outcome.cjs tests/test_recommendation_snapshot.cjs tests/test_enrich_perk_descriptions.mjs` | No | Node.js tests. |

To regenerate an export after changing a recommendation, run the same export command without `--check`.

### The catalogue

The catalogue isn't stored in this repository. By default the tools read it from a public, read-only Supabase project, so you don't need an account or any credentials. To point at a different project, set `SUPABASE_URL` and `SUPABASE_PUBLISHABLE_KEY`.

Writing to the catalogue (`scripts/generate_weapon_perk_pools.mjs` and `scripts/enrich_perk_descriptions.mjs`) is maintainer-only and needs a `SUPABASE_SECRET_KEY` in the local shell. Never commit a Supabase secret or service-role key, and never put one in a GitHub Actions workflow. `check_workflow_security.py` fails if one appears.

### Pull requests

- Branch from `main`, keep the change focused, and fill in the pull request template.
- CI must pass on Python 3.11, 3.12, 3.13, and Node.js. `@rox4H` reviews every change.
- Don't edit `exports/` by hand. CI fails when an export differs from what the script generates.
- Community records under `recommendations/community/` are published by the automation from validated issues, so each one carries its author and source issue. Suggest community rolls through the issue form instead of a pull request.
- Workflow changes must pin every action to a full commit SHA, must not use `pull_request_target`, and must not expose publisher credentials. `check_workflow_security.py` enforces this.
- JSON records must not contain duplicate keys. The validator rejects them.

## Licensing of contributions

- Code, scripts, tests, workflows, and documentation are licensed under the [MIT License](LICENSE).
- Everything under `exports/` and `recommendations/` is licensed under [CC BY 4.0](DATA-LICENSE.md). That includes the rolls, names, and descriptions you submit.
- By opening a recommendation issue or a pull request you confirm that the contribution is yours, or that you have the right to share it, and you license it under the terms above.
- Don't contribute content copied from other wishlists, guides, or sites, or Bungie material such as images or game text. Item and perk names and hashes are used only to identify game items.
- If a pull request needs a different arrangement, document it in the pull request. The maintainer decides whether to accept it.

## Attribution and privacy

Community suggestions are public. A published record stores your GitHub username, the issue number, and the submission time, and the wishlist note shows `BY @your-username`. Don't put private information in an issue.

To have a published suggestion corrected or removed, open a [correction issue](https://github.com/rox4H/automated-dim-wishlists/issues/new?template=data-correction.yml). This is a volunteer-run project, so handling is best effort.

## Conduct

Be respectful and constructive, and keep discussion on topic. Harassment and personal attacks aren't tolerated. The maintainer may edit, close, or lock content that breaks these rules.

## Security

Don't report vulnerabilities in public issues, discussions, or pull requests. Follow [SECURITY.md](SECURITY.md).
