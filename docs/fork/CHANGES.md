# Zmiany w forku XELTO

Funkcje i poprawki dodane w forku `jakubwilkxelto/VoiceStudio` ponad upstream
`debpalash/VoiceStudio`. Najnowsze na górze.

## Niewydane

- Tryb wieloużytkownikowy, faza P2: rozdzielenie danych w bazie. Głosy,
  historia, dubbingi, projekty, glosariusze, eksporty i joby są widoczne tylko
  dla właściciela (widoki TEMP w SQLite, hak w `get_db`), głosy demo są wspólne
  i tylko do odczytu. Tożsamość przechodzi do wątków i kolejek zadań.
  Bootstrap właściciela istniejących danych i CLI `python -m xelto.cli`
  (`assign-unowned`, `purge-user`). **Pliki, strumienie zadań i zdarzenia
  są jeszcze wspólne** (P3).
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
