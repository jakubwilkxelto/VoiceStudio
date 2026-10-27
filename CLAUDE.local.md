# Fork XELTO — reguły nadrzędne wobec CLAUDE.md

Ten plik istnieje tylko w forku `jakubwilkxelto/VoiceStudio`; upstream
(`debpalash/VoiceStudio`) go nie zna, więc mergowanie upstreamu go nie nadpisze.
Gdy reguła stąd koliduje z `CLAUDE.md` / `AGENTS.md`, wygrywa ten plik.

## Model pracy

- Fork rozwija własne funkcje. **Nie wysyłamy PR-ów do upstreamu**, tylko pobieramy
  i mergujemy jego zmiany.
- Remote'y: `origin` = fork, `upstream` = oryginał.
- Gałęzie:
  - `main` — czyste lustro `upstream/main`, tylko fast-forward, zero własnych commitów.
  - `dev` — gałąź forka (własne funkcje + zmergowany upstream).
  - `feat/*`, `fix/*` — od `dev`, mergowane do `dev`.

## Synchronizacja z upstreamem

```sh
git fetch upstream
git checkout main && git merge --ff-only upstream/main && git push origin main
git checkout dev && git merge main
```

- Synchronizuj często (co kilka dni) — upstream ma kilkanaście merge'y dziennie.
- Konflikty w `bun.lock` / `uv.lock`: nie scalaj ręcznie — weź wersję upstreamu,
  potem `bun install` / `uv lock`, żeby dołożyć zależności forka.
- Po merge'u: testy dotkniętych obszarów (`uv run pytest tests/...`,
  `bun run test`, `bun run typecheck`).

## Jak pisać kod, żeby merge był tani

- Nowa logika w **nowych plikach** (nowy router, serwis, silnik, widok, komponent).
- W plikach upstreamu tylko **cienkie punkty zaczepienia** (rejestracja routera,
  wpis w nawigacji, import) — po kilka linii, oznaczone komentarzem `XELTO:`, żeby
  było je łatwo znaleźć (`git grep "XELTO:"`) i przenieść przy konflikcie.
- Nie refaktoryzuj ani nie formatuj kodu upstreamu przy okazji.
- Nie zmieniaj wersji w `package.json` / `pyproject.toml` / `backend/core/version.py`
  — idą za upstreamem.
- Nie edytuj `CHANGELOG.md` ani `changelog.d/` — zmiany forka opisuj w
  `docs/fork/CHANGES.md`.
- Nie usuwaj plików upstreamu (np. workflowów) — wyłączaj je w ustawieniach GitHuba.
- Każdy punkt zaczepienia w kodzie upstreamu dopisz do rejestru poniżej.

## Które reguły z CLAUDE.md nie obowiązują w forku

- Wszystko o PR-ach do upstreamu, harvestowaniu CodeRabbit/Greptile, merge queue,
  CLA i checku `commit-identity`.
- Wydania i kanały dystrybucji (GitHub Release, GHCR/Docker Hub, sklepy, beta
  channel, release notes) — fork ich nie publikuje.
- „Start from the latest main” w wersji upstreamu — zastępuje go sekcja
  *Synchronizacja z upstreamem* powyżej.

## Które reguły z CLAUDE.md nadal obowiązują

- Parytet platform (macOS / Windows / Linux) dla domyślnych funkcji.
- Local-first: żadnych nowych wywołań sieciowych bez wyraźnej zgody użytkownika.
- Lokalizacja: teksty UI przez i18n (`t('...')`), parytet katalogów locale.
- Zmiany schematu bazy wyłącznie przez alembic, z działającym upgrade.
- Kompatybilność z istniejącymi danymi `omnivoice_data/` i zainstalowanymi silnikami.
- Jakość poprawek: przyczyna źródłowa + test regresyjny.
- Electron to jedyne UI desktop/web; mobile — wspólny kontrakt `mobile/contract/SPEC.md`.

## Rejestr punktów zaczepienia w kodzie upstreamu

| Plik | Co dodano | Funkcja forka |
|---|---|---|
| `backend/main.py` (po `app.add_middleware(BackendMarkerMiddleware)`) | 2 linie: import i wywołanie `xelto.install(app)` | tryb multiuser (`backend/xelto/`, `docs/fork/multiuser.md`) |
| `pyproject.toml` (`dependencies`, po `cryptography`) | 1 linia: `pyjwt[crypto]` (+ 2 linie w `uv.lock`) | tryb multiuser: walidacja tokenów Keycloak |
| `electron/src/renderer/src/app.tsx` | import + `<XeltoOidcGate>` wokół `<WebAuthGate>` | tryb multiuser: logowanie (build web) |
| `electron/src/renderer/src/components/app-shell/account-corner.tsx` | import + `<XeltoUserMenu />` | tryb multiuser: menu użytkownika |
| `electron/src/renderer/src/features/settings/settings-page.tsx` | import + `useXeltoVisibleSettings(...)` wokół `extras` | tryb multiuser: ukrycie stron admina |
| `electron/src/renderer/src/features/settings/settings-nav.tsx` | import + `useXeltoPinned(...)` wokół `pinned` | tryb multiuser: ukrycie modeli/narzędzi/logów |
| `electron/src/renderer/src/components/command-palette.tsx` | import + `useXeltoCommands(...)` wokół `items` | tryb multiuser: ukrycie poleceń admina |
