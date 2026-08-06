# gw2profits.com archive

Fetched 2026-08-06 from the public JSON API of gw2profits.com, shortly before the site's announced shutdown (hosting expired 2026-08-15, the owner chose not to renew and publicly offered the site's assets for preservation).

Site and data by Colby M.G. ("Mystic.5934", colbymg@gmail.com, [/u/colbymg](https://www.reddit.com/user/colbymg)).

## Contents

- `recipes_v3.json`: full dump of the `/json/v3` recipe API. 8,062 recipes by discipline: 5,997 Mystic Forge, 1,376 Merchant, 348 Achievement, 147 Charge, 141 Double Click, 53 Salvage. The Mystic Forge and salvage data does not exist in the official GW2 API and was compiled from years of community research.
- `json_api_docs.html`: the `/json/` documentation page describing the v3 query parameters.
- `mystic_gold_guide.html`: Mystic's gold profiting guide.

## Field notes

- Negative `id` values are gw2profits-internal ids for recipes absent from the official API.
- `output_item_count` is the average yield; when `output_item_count_range` is present (e.g. salvage and container recipes), the count is an expected value derived from drop research.

## Credits

As listed on the site's community page: Wanze.8410 (salvage rates), Nugkill (precursor forging statistics), The Egg Baron (Mystic Forge material promotion rates), queicherius.2563 and Chokapik.3741 (Mystic Forge recipes), sutgon, Cogima, Trutichup, and squidONE (festival bag opening research).
