# Zmiany w forku XELTO

Funkcje i poprawki dodane w forku `jakubwilkxelto/VoiceStudio` ponad upstream
`debpalash/VoiceStudio`. Najnowsze na górze.

## Niewydane

- Docker multiuser: zapisane klucze LLM i token HF przestawały działać po
  każdym odtworzeniu kontenera (np. `up --build`). Obraz ma pusty
  `/etc/machine-id`, więc klucz szyfrowania brał się z losowej nazwy hosta;
  compose forka ustala teraz `hostname: voicestudio`. Sekrety zapisane
  wcześniej trzeba wpisać raz jeszcze.
- Tryb multiuser: admin zalogowany przez Keycloak dostawał 403 („loopback
  origin required”) na GET-ach upstreamu z efektami ubocznymi
  (`require_admin_action`: lista modeli dostawcy LLM, sprawdzanie silników).
  Przeglądarka nie wysyła `Origin` przy GET do tej samej domeny, a upstream
  go wymaga; gate forka dokłada publiczny origin, gdy przeglądarka sama
  potwierdziła `Sec-Fetch-Site: same-origin` i jest znacznik CSRF.
  Obraz trzeba przebudować.
- Build obrazu z kopii roboczej: `.dockerignore` wyklucza też
  `electron/node_modules` (wcześniej `vite: not found`).
- Jeden wygląd na serwerze multiuser: domyślny ciemny motyw VoiceStudio.
  - strony logowania Keycloaka mają motyw `voicestudio`
    (`deploy/keycloak/themes/`): logo, font Inter, kolory, pola i przyciski
    jak w aplikacji; istniejący realm wymaga jednorazowego ustawienia
    (`docs/fork/multiuser.md` → *Wygląd: jeden motyw*);
  - w aplikacji nikt, także admin, nie zmieni motywu: kontrolki motywu są
    ukryte, a zmieniony motyw wraca do domyślnego ciemnego.
- Tryb wieloużytkownikowy, poprawki po pierwszym teście w Dockerze:
  - **rozdzielenie danych działa na SQLite z obrazu Dockera (3.50).**
    Wcześniej każde połączenie z bazą dla usera kończyło się błędem
    („qualified table names are not allowed … within triggers”), bo testy
    szły tylko na nowszym SQLite. Zapisy idą teraz na tabelę bazową, a cudze
    wiersze są pomijane przez strażników. Do tego test DDL i skrypt
    `scripts/xelto/test_in_image.sh`;
  - `INSERT OR REPLACE` nie przejmuje już id wiersza demo ani wiersza bez
    właściciela;
  - kreator pierwszego uruchomienia przechodzi tylko admin, raz na serwer;
    zwykły user widzi ekran czekania, dopóki serwer nie jest gotowy;
  - powiadomienia systemowe (m.in. brak tokenu HF) widzi tylko admin;
  - konto admina aplikacji w realmie to `studio-admin`, żeby nie myliło się
    z kontem konsoli Keycloaka.
- Tryb wieloużytkownikowy, faza P4 (utwardzenie):
  - pliki bez ustalonego właściciela są domyślnie odrzucane
    (`OMNIVOICE_MULTIUSER_FILES=strict`);
  - zapis bez tożsamości daje czytelny błąd i jest logowany;
  - wersja web po zmianie usera czyści też `sessionStorage` i IndexedDB;
  - lokalna nakładka do prób `deploy/keycloak/docker-compose.local.yml`;
  - katalog tłumaczeń forka dla galicyjskiego (nowy język upstreamu).

  Naprawione wycieki między userami:
  - `POST /dub/ingest-url` z cudzym `job_id` mógł usunąć katalog cudzego dubbingu;
  - eksport paczki projektu pakował cudze pliki;
  - echo ścieżki w stanie własnego projektu dawało dostęp do cudzego pliku;
  - `emo_audio` w `/ws/tts` czytał cudze pliki głosów;
  - preferencje dyktowania były wspólne i każdy mógł je zmieniać (teraz tylko admin).

  Nowe testy: pełna macierz izolacji (każda trasa per user) i E2E w trybie
  `strict` na fałszywych modelach.
- Tryb wieloużytkownikowy, faza P3: rozdzielone są pliki (`/audio`,
  `/voice_audio`, `/preview`), zadania w tle (`/tasks/*`), joby batch, paczki
  projektów, lista wznawialnych audiobooków i zdarzenia `/ws/events`. Cudze
  zasoby dają 404. Pliki bez ustalonego właściciela tryb `lenient` serwuje
  i loguje, a `OMNIVOICE_MULTIUSER_FILES=strict` je odrzuca. Poprawki P2:
  - joby batch wykonywały się jako pierwszy user, który coś zakolejkował;
  - `DELETE /dub/history/{id}` na cudzym jobie usuwał jego katalog z dysku.
- Tryb wieloużytkownikowy: dane forka (konta, sesje, własność wierszy,
  historia migracji) w osobnym pliku `xelto.db` obok `omnivoice.db`
  (`OMNIVOICE_MULTIUSER_DB` zmienia ścieżkę). `omnivoice.db` nie zawiera już
  żadnej tabeli forka; tabele z wcześniejszych wersji są przy starcie
  przenoszone automatycznie.
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
