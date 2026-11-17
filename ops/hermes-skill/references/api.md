# Speerpunt Bingo — HTTP API reference (validated against app/main.py)

Base: `BINGO_URL`. Session: cookie `tombala_session` from `POST /login` (form
`email`, `password`; 303 redirect). All `/api/*` writes require header
`X-Requested-With: tombala` plus `Content-Type: application/json`; without it →
`400 {"detail":"Ongeldige aanvraag"}`. Errors are `{"detail": "<nld>"}`.

Login limiter: `RateLimiter(attempts=10, window=300s)` keyed on `ip:email` → `429` means wait,
not "wrong password". Wrong credentials → `401` HTML page (not JSON).

## Read

| Method + path | Returns |
| --- | --- |
| `GET /healthz` | `{"ok":true}` |
| `GET /api/state` | player view: `me` (+`jokers_left`, +`jokers{base,awards,bonus,used,left}`), `settings` (title/kicker/subtitle/footer/**cols**/**rows**/frozen/veto_threshold/witness_limit/full_card_prize), `card[]` (`pos`, `item_id`, `text`, `status` open/pending/confirmed, `claim{id,witness,joker,note,photo,created_at,vetoes}`), `lines[]` (`key,label,prize,won` — `prize` is the **snapshot** taken at win time for won lines, current offer for open ones), `players[]`, `witness_requests[]`, `witness_load`, `leaderboard[]` (`prizes[]{label,prize}` also snapshotted), `first_bingo_at`, `feed[]`, `proposals[]` (inbreng: `visible` + `adopted` + eigen `pending`), `feedback` (`open,premod,per_player,mine_open,pending`), `vapid_public_key` |
| `GET /api/album` | photo claims: `{full,thumb,text,nickname,color,at,status}` newest first |
| `GET /uploads/<stem>.jpg` \| `<stem>_t.jpg` | photo bytes (session required) |
| `GET /api/stream` | SSE: `refresh`, `bingo`, `announce`, `witness_request`, `confirmed`, `rejected`, `push-ish` events; 20 s ping comments |

## Admin read

| Method + path | Returns |
| --- | --- |
| `GET /api/admin` | `settings` (all keys, `grid_cols`/`grid_rows` as **strings**), `cells`, `grid` (plan), `items[]` (`id,text,on_cards,confirmed`), `invites[]` (`code,created_at,used_at,used_by,used_by_name,uses`), `players[]` (`id,nickname,email,color,is_admin,created_at,jokers{base,awards,bonus,used,left}`), `lines[]` (`key,label,prize`), `claims[]` (pending+confirmed, last 200: `id,user,item,status,joker,witness,at`), `proposals[]` (all statuses, pending first: `id,kind,text,line_key,line,user_id,nickname,color,status,up,down,score,my_vote,at,can_vote,can_edit,adopted_ref`), `feedback` |
| `GET /api/admin/grid` | `cols,rows,cells,items,spare_items,suggestion{cols,rows,cells},optimal,auto,min_cells_for_claims,would_lose[{player,line,label}]` |

## Admin writes

| Method + path | Body | Notes |
| --- | --- | --- |
| `POST /api/admin/settings` | any of: `title`,`kicker`,`subtitle`,`footer`,`default_line_prize`,`full_card_prize` (str ≤200); `grid_cols` 2–6, `grid_rows` 2–8, `jokers_per_player` 0–5 (default 0 = groepsbasis uit), `joker_earn_claims` 0–20 (default 0 = uit; elke N bevestigde claims levert 1 joker op, automatisch max 3 p.p.), `veto_threshold` 0–20, `witness_limit` 0–50, `feedback_per_player` 0–10 (default 0 = geen limiet; clamped ints); `line_rows`,`line_cols`,`line_diagonals`,`grid_auto`,`frozen`,`feedback_open`,`feedback_premod` (bool) | unknown keys are ignored silently. Changing `grid_cols/rows/line_*` rebuilds cards **without** reshuffling. Changing grid while `grid_auto` is on flips `grid_auto` off. Toggling `frozen` posts an announcement in the feed. Response includes `grid` plan when autoset ran |
| `POST /api/admin/items` | `{"text":"regel per vakje"}` (each ≤160 chars) | adds items, fills empty cells only, then autoset if on. → `{"ok":true,"added":N,"grid":plan}` |
| `PUT /api/admin/items/{id}` | `{"text":...}` | rename in place |
| `DELETE /api/admin/items/{id}` | — | soft delete (`active=0`) + revokes open claims of that item |
| `POST /api/admin/auto-grid` | `{"force":true}` optional | applies optimal grid; refuses when it would cost existing bingo's unless forced. Cards never reshuffled |
| `POST /api/admin/reshuffle` | — | reshuffles all cards, keeps claims/bingos |
| `POST /api/admin/prizes` | `{"r0":"Rakı","full":"..."}` (≤200) | empty string clears back to default; keys outside the current layout are ignored |
| `POST /api/admin/invites` | `{"code":"ISTANBUL-2026"}` of `{"count":N}` 1–30 | eigen code: 3–24 tekens A-Z0-9- (kleine letters/spaties worden genegeerd, dubbel = 400). Zonder `code` worden woordcodes gegenereerd: RAKI, SIMIT, AYRAN, LOKUM, MEZE, DONER, KEBAP, CAY, BAKLAVA, BOSPHORUS + 4 tekens uit `ABCDEFGHJKLMNPQRSTUVWXYZ23456789`. **Een code is herbruikbaar**: één per evenement |
| `DELETE /api/admin/invites/{code}` | — | trekt de code in (case/spatie-insensitive), ook als hij gebruikt is; `users.invite_code` van bestaande accounts blijft staan → `{"ok":true,"removed":1}` |
| `POST /api/admin/players/{id}/nickname` | `{"nickname":…}` | rename any user, other admins included; same rules as self-rename, 409 if taken, 404 if no such player |
| `POST /api/admin/players/{id}/password` | `{"password":...}` ≥8 | also kills that player's sessions |
| `POST /api/admin/players/{id}/reset` | — | deletes that player's claims + bingos, deals a fresh card |
| `POST /api/admin/players/{id}/joker` | `{"amount": -5…5, ≠ 0}` | individueel een joker **geven** (+) of **innemen** (−). `jokers_per_player` blijft de groepsbasis; deze gift staat los (`jokers_bonus`, loopt niet onder 0). Feed-event `joker_granted` / `joker_revoked` met `by` = admin-nickname. Response: `{left, base, awards, bonus, used}`. Verdienversie: `maybe_earn_joker` bij elke bevestigde claim, event `joker_earned` (`claims`, `per`, `aantal`) |
| `DELETE /api/admin/players/{id}` | — | deletes any other account, **admins included** (self-delete is refused, and the last admin can never go); pending claims they witnessed are cancelled, cards/claims/bingos/sessions cascade |
| `POST /api/admin/claims/{id}/revoke` | — | admin override of any open claim
| `POST /api/admin/feedback/{id}/approve` | — | inbreng vrijgeven: `pending → visible`, pas daarna feed-event `feedback_new` + SSE refresh. 400 als hij al behandeld is, `already:true` als hij al vrij was |
| `POST /api/admin/feedback/{id}/reject` | — | `pending|visible → rejected` (soft: rij blijft voor de history). Feed-event `feedback_rejected` alleen als de rij eerder vrijgegeven was — wat de club nooit zag, verdwijnt stil |
| `POST /api/admin/feedback/{id}/reopen` | — | `rejected → pending` |
| `POST /api/admin/feedback/{id}/adopt` | — | inbreng wordt echt spel. `kind=item` → nieuw actief vakje + `rebuild_everything(reshuffle=False)` (claims blijven) + autoset als die aan staat; 409 als het vakje al bestaat. `kind=prize` → `full`→`full_card_prize`, `default`→`default_line_prize`, anders `line_prizes`. Status `adopted`, `adopted_ref` = `item:<id>` / `prize:<lijn>`. → `{ok,ref,grid}` | |
| `POST /api/admin/announce` | `{"text":...}` ≤300 | feed event + SSE broadcast + web push to all subscriptions |
| `POST /api/admin/new-game` | — | wipes `vetoes`, `bingos`, `claims`, `events`, unfreezes, reshuffles all cards. Destructive |

## Player-side writes (admin may act as witness/voter)

| Method + path | Body | Rules |
| --- | --- | --- |
| `POST /api/claims` | `{"item_id":N,"witness_id":M,"note":str,"photo":"data:image/...;base64,...", "joker":bool}` | blocked when frozen (409). Item must be on your card and not already claimed. `joker:true` → instantly confirmed, heeft joker-budget nodig: `jokers_per_player` (groepsbasis, standaard 0) + `joker_awards` (verdiend: elke `joker_earn_claims` bevestigde claims, max 3) + `jokers_bonus` (losse gift van de admin), min wat al op is; 409 als er niets over is. Witness ≠ self, and `witness_limit` caps how often the same witness backs the same player. Photo ≤6 MB, EXIF/GPS stripped, resized to 1600 px + 420 px thumb |
| `POST /api/claims/{id}/confirm` \| `/reject` | — | only the assigned witness, only while `pending` |
| `POST /api/claims/{id}/cancel` | — | only the claimant, while pending |
| `POST /api/profile/nickname` | `{"nickname":"…"}` | anyone may rename themselves: 2–20 chars, unique (NOCASE), no `<>&"'` → `{"ok":true,"nickname":…}`; logs a `renamed` event and SSE-refreshes everyone |
| `POST /api/claims/{id}/veto` | — | needs `veto_threshold > 0`, claim must be `confirmed`, and you may not be claimant or witness. At threshold the claim becomes `vetoed` (counts as a penalty) |
| `POST /api/feedback` | `{"kind":"item"\|"prize","text":str ≤160,"line_key":"r0\|c0\|dr0\|dl0\|full\|default"}` | de inbreng-inbox: spelers (en de admin als poll) opperen een vakje of een prijs. 400 als `feedback_open` uit staat, de tekst <3 chars is, `kind`/`line_key` onbekend is, of als het maximum `feedback_per_player` open inbrengen is bereikt; 409 als dezelfde tekst al open staat. `feedback_per_player` staat standaard op 0 = onbeperkt. Rate-limited: 8 pogingen / 5 min per speler (vaste spamwacht). Bij `feedback_premod` (aan) wordt de rij `pending` — **alleen de maker en de admin zien hem dan**, géén feed-event en géén SSE-refresh. Admin-inbreng is altijd direct `visible`. → `{ok,id,status}` |
| `POST /api/feedback/{id}/vote` | `{"vote":1\|-1\|0}` | alleen op `visible` rijen (anders 400 — een aangenomen of afgekeurde suggestie is een besluit). 1/-1 replaces, 0 = stem intrekken. SSE-refresh voor iedereen |
| `DELETE /api/feedback/{id}` | — | maker (of admin) trekt een open inbreng in: `pending|visible → rejected`; 403 als het iemand anders' is, 400 als hij al geadopteerd/af is. Bij een vrijgegeven rij verschijnt `feedback_rejected` met `ingetrokken:true` in de feed |
| `POST /api/push/subscribe` | `{"endpoint":"https://...","keys":{"p256dh":"...","auth":"..."}}` | registers this browser's push subscription |
| `POST /api/push/test` | — | sends "Şerefe! 🍻" to the current user's devices |

## Registration (there is no admin "create player")

`POST /register` is the claim wizard, form-encoded (not JSON). Sending only `code` checks the
code: valid → the same page comes back with the account fields and the code locked (readonly);
onbekend → "Die code kennen we niet…". Sending `code` + `email` + `nickname` + `password`
claims it (nickname 2–20 chars, password ≥8, unique email + nickname) and returns 303 to `/`
with a session cookie. Codes are event codes, so many players use the same one; the app
records `users.invite_code` and the first use in `invites.used_at`/`used_by`. A logged-out
visitor at `/` lands on this wizard, not on the login page. Prefill with
`GET /register?code=ISTANBUL-2026`. The admin row itself is created at boot from
`ADMIN_EMAIL`/`ADMIN_PASSWORD` (`ADMIN_RESET_PASSWORD=1` forces the password once).
`PRUNE_ADMINS=a@b.nl,c@d.nl` is a one-shot startup hatch: those **admin** accounts are deleted at boot
(never `ADMIN_EMAIL`, never plain players) — for moving the admin on a host where you can't reach the DB.
The startup log prints `data: <db-pad> — N accounts, N admin(s), oudste account <ts>, PRUNE_ADMINS=<…>`,
which is how you confirm a deploy kept its database (the same timestamp twice = the `/data` mount works).
