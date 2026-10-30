# Weapon Wishlists for DIM

[![Validate data and tests](https://github.com/rox4H/automated-dim-wishlists/actions/workflows/validate.yml/badge.svg)](https://github.com/rox4H/automated-dim-wishlists/actions/workflows/validate.yml)

Destiny 2 weapon roll wishlists for [Destiny Item Manager (DIM)](https://destinyitemmanager.com). Add a list to DIM and weapons that match a recommended roll get a thumbs-up, with a note saying whether the roll is for PvE or PvP.

## Use a wishlist

Pick a list and copy its URL:

**Curated** — rolls chosen by the maintainer.

```text
https://raw.githubusercontent.com/rox4H/automated-dim-wishlists/main/exports/d2wd_curated.txt
```

**Community** — rolls suggested by players, each credited to its author.

```text
https://raw.githubusercontent.com/rox4H/automated-dim-wishlists/main/exports/d2wd_community.txt
```

In DIM, open **Settings → Wish Lists**, paste the URL, and save. Matching items show a thumbs-up. Open an item to see its suggested perks, or search `is:wishlist` to find every match.

Both URLs always serve the current `main` branch. The files are generated from the recommendations in this repository, and CI fails if a file is out of date.

## What the lists do and don't promise

- **Curated.** Each record is tagged `verified` or `unverified`. An unverified entry is a candidate, not a tested pick.
- **Community.** Entries are *mechanically validated*: the weapon version exists in the catalogue, each of the four perks can roll in its slot on that exact version, and the same roll isn't already listed. Nobody has judged whether the roll is *good*.

Wishlist entries are player opinions, not guarantees of performance. Verify weapon details in game before relying on a recommendation.

Each roll is one weapon version and four perks: a barrel or scope/sight, a magazine, and two traits. In the file, a roll is a single line:

```text
dimwishlist:item=<weapon hash>&perks=<four perk hashes>#notes:PVE | BY @author | Roll name
```

Curated lines have no `BY @author` part.

## Suggest a roll

1. Open a [recommendation issue](https://github.com/rox4H/automated-dim-wishlists/issues/new?template=recommendation-proposal.yml). Submit one roll per issue.
2. Fill in the mode (PvE or PvP), a short name, a description, the exact weapon version ID (for example, `avalanche_s5`), and four perks.
3. Don't edit or reopen the issue afterwards. A bot checks it as submitted and comments with the result. If it passes, the roll is published to the community list automatically and credited to your GitHub username.

Don't include private information. The full guide, including what each result means, is in [CONTRIBUTING.md](CONTRIBUTING.md).

## Report a mistake

Use the [correction issue](https://github.com/rox4H/automated-dim-wishlists/issues/new?template=data-correction.yml) to report a wrong weapon, perk, or roll.

## Repository layout

| Path | Contents |
| --- | --- |
| `exports/` | The two DIM wishlist files. Generated; don't edit by hand. |
| `recommendations/curated/`, `recommendations/community/` | Source records, one JSON file per weapon version and mode. |
| `schemas/` | JSON Schemas for the records. |
| `scripts/` | Validation, export, and catalogue tooling. |
| `tests/` | Python and Node.js tests. |
| `.github/` | Issue forms, CI, and the intake and publication workflows. |

The weapon, perk, and perk-pool catalogue that recommendations are checked against isn't stored in this repository. The tooling reads it from a public, read-only database.

## Contributing and security

- Contributions are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md).
- To report a vulnerability, follow [SECURITY.md](SECURITY.md). Don't use public issues.

## Licenses

| What | License |
| --- | --- |
| Code, scripts, tests, workflows, and documentation | [MIT License](LICENSE) |
| Wishlist data in `exports/` and `recommendations/` | [CC BY 4.0](DATA-LICENSE.md), which asks you to credit the project when you reuse it |

These licenses cover only rights the project's contributors can license. They don't grant any rights to Destiny game content, names, or trademarks owned by Bungie, Inc. See Bungie's [Intellectual Property and Trademarks](https://help.bungie.net/hc/articles/360049201911) guidelines for how you may use them.

## Disclaimer

This is an unofficial community project. It is not affiliated with, endorsed by, or sponsored by Bungie, Inc. or the Destiny Item Manager (DIM) project. Destiny and Destiny 2 are trademarks of Bungie, Inc.; all other trademarks belong to their respective owners.
