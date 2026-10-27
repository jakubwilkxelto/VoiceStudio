# Zmiany w forku XELTO

Funkcje i poprawki dodane w forku `jakubwilkxelto/VoiceStudio` ponad upstream
`debpalash/VoiceStudio`. Najnowsze na górze.

## Niewydane

- Tryb wieloużytkownikowy, faza P1: logowanie przez Keycloak (OIDC z PKCE,
  backend jako klient, ciasteczko sesji HttpOnly), role `voicestudio-admin` /
  `voicestudio-user`, tokeny Bearer dla API, polityka dostępu dla wszystkich
  tras, menu użytkownika i ukrywanie ustawień admina w UI web, wdrożenie
  `deploy/docker-compose.keycloak.yml` (Keycloak + Postgres + Caddy z TLS).
  **Dane użytkowników są jeszcze wspólne** (rozdzielenie w P2).
- Tryb wieloużytkownikowy, faza P0: szkielet za flagą `OMNIVOICE_MULTIUSER`
  (domyślnie wyłączony, wtedy bez zmian względem upstreamu), osobny łańcuch
  migracji forka, strażnik klasyfikacji tras i spike izolacji danych w SQLite.
  Z flagą włączoną wszystko poza `/health` jest blokowane do czasu P1.
  Szczegóły: `docs/fork/multiuser.md`.
- Reguły pracy w forku (`CLAUDE.local.md`).
