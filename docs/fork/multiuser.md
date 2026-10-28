# Tryb wieloużytkownikowy (fork XELTO)

Konta przez Keycloak i rozdzielenie danych użytkowników na jednym serwerze
Docker. Pełny plan: `PLAN-26-10-2026.md`.

## Stan

**Faza P2: logowanie i role działają, wiersze bazy są rozdzielone per user.**
Każdy widzi tylko swoje głosy, historię, dubbingi, projekty, glosariusze,
eksporty i joby (patrz [Izolacja danych](#izolacja-danych-p2)). **Pliki**
(`/audio`, `/voice_audio`), strumienie zadań w pamięci, `/batch/*` i zdarzenia
`/ws/events` są jeszcze wspólne (rozdzielenie w P3). Do tego czasu udostępniaj
serwer tylko zaufanemu zespołowi.

| Flaga | Zachowanie |
|---|---|
| `OMNIVOICE_MULTIUSER` nieustawiona | identycznie jak upstream; `xelto.install()` nic nie robi |
| `OMNIVOICE_MULTIUSER=1` | logowanie przez Keycloak, role admin/user, trasy według polityki; serwer nie wystartuje z niebezpieczną konfiguracją |

## Wdrożenie (Docker)

1. Dwie nazwy DNS wskazujące na serwer: aplikacja (`studio.example.com`)
   i Keycloak (`auth.example.com`). Porty 80 i 443 otwarte (Caddy pobiera
   certyfikaty TLS).
2. `cp deploy/keycloak/.env.example deploy/keycloak/.env` i uzupełnij.
   Sekrety generuj przez `openssl rand -hex 32`.
3. Start:

   ```sh
   docker compose --env-file deploy/keycloak/.env \
     -f deploy/docker-compose.keycloak.yml up -d --build
   ```

4. Realm `voicestudio` importuje się przy pierwszym starcie Keycloaka
   (`deploy/keycloak/realm-voicestudio.json`). Później zmiany wprowadza się
   w konsoli `https://auth.example.com/admin`, bo ponowny import pomija
   istniejący realm.
5. Konta startowe `admin`, `alice` i `bob` mają hasło
   `VOICESTUDIO_INITIAL_PASSWORD`, zmieniane przy pierwszym logowaniu.
   **Na produkcji usuń `alice` i `bob`.** Nowym osobom nadaj w Keycloaku rolę
   klienta `voicestudio` → `voicestudio-user` (albo `voicestudio-admin`).
   Konto bez żadnej z tych ról nie wejdzie („not enrolled”).

Uwagi:

- Compose jest samodzielny, a nie nakładką na `deploy/docker-compose.yml`.
  Plik upstreamu wymaga `OMNIVOICE_API_KEY`, którego tryb multiuser nie przyjmuje.
- Obraz budowany jest z tego repo (`build: ..`): obraz upstreamu nie zawiera forka.
- Port 3900 nie jest publikowany. Nie wystawiaj go: cały ruch idzie przez Caddy.
- Backend rozmawia z Keycloakiem wewnątrz sieci Compose
  (`OIDC_DISCOVERY_URL`, `KC_HOSTNAME_BACKCHANNEL_DYNAMIC=true`), a tokeny
  noszą publiczny issuer `https://auth…/realms/voicestudio`.
- Konsola admina Keycloaka (`/admin`) jest dostępna z internetu. Jeśli to
  niepożądane, ogranicz ją w Caddyfile (np. `@admin path /admin*` + lista IP).
- Kopia zapasowa: wolumen `voicestudio-data` (`omnivoice.db`, `xelto.db`
  i pliki) oraz baza Keycloaka (`keycloak-db`). Obie bazy SQLite kopiuj
  razem: `xelto.db` mówi, czyje są wiersze w `omnivoice.db`.

### Zmienne środowiskowe backendu

| Zmienna | Znaczenie |
|---|---|
| `OMNIVOICE_MULTIUSER=1` | włącza tryb |
| `OMNIVOICE_PUBLIC_URL` | publiczny origin aplikacji (https) |
| `OMNIVOICE_ALLOWED_ORIGINS` | musi zawierać `OMNIVOICE_PUBLIC_URL` (CORS, CSRF i kontrola `Host`) |
| `OMNIVOICE_MULTIUSER_SECRET` | ≥ 32 znaki; szyfruje ciasteczka i refresh tokeny |
| `OIDC_ISSUER`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET` | klient Keycloaka |
| `OIDC_DISCOVERY_URL` | opcjonalnie: wewnętrzny adres metadanych OIDC |
| `OIDC_API_AUDIENCE` | opcjonalnie: wymagany `aud` tokenów Bearer (domyślnie client id) |
| `OMNIVOICE_SERVER_MODE=1` | wymagane (obraz Dockera ustawia je sam) |
| `OMNIVOICE_MULTIUSER_ALLOW_MASTER_KEY=1` | awaryjnie: `OMNIVOICE_API_KEY` jako Bearer admina |
| `OMNIVOICE_MULTIUSER_INSECURE_HTTP=1` | tylko testy lokalne: zezwala na `http://` |
| `OMNIVOICE_MULTIUSER_BOOTSTRAP_OWNER` | username albo `sub`; przy jego pierwszym logowaniu dostaje wszystkie istniejące dane bez właściciela |
| `OMNIVOICE_MULTIUSER_DB` | opcjonalnie: ścieżka bazy forka (domyślnie `xelto.db` obok `omnivoice.db`) |
| `OMNIVOICE_MULTIUSER_AUDIT=1` | loguje stos każdego połączenia z bazą otwartego bez tożsamości |

Serwer **nie wystartuje**, gdy brakuje którejś z wymaganych zmiennych, gdy
ustawiono `OMNIVOICE_TRUSTED_NETWORKS` lub `OMNIVOICE_TRUST_HOST_LOOPBACK`,
gdy ustawiono `OMNIVOICE_API_KEY` bez zgody na klucz master, oraz gdy
publiczny URL nie jest https albo nie ma go w `OMNIVOICE_ALLOWED_ORIGINS`.

Uwaga dla deweloperów: `backend/main.py` wczytuje plik `.env` znaleziony
w katalogach nadrzędnych (`dotenv.load_dotenv()`). Jeśli jest w nim
`OMNIVOICE_API_KEY`, tryb multiuser odmówi startu.

## Jak to jest zbudowane

### Backend (`backend/xelto/`)

- **Logowanie (BFF)**: backend jest klientem OIDC (`bff.py`, `oidc.py`).
  Przeglądarka dostaje wyłącznie ciasteczko sesji HttpOnly, nigdy tokena
  Keycloaka. Endpointy `/api/auth/oidc/{login,callback,logout,me}` obsługuje
  sama brama, więc snapshot tras upstreamu się nie zmienia. Przepływ: code +
  PKCE S256, `state` i `nonce` w zaszyfrowanym ciasteczku transakcji (10 min),
  `next` tylko jako ścieżka w tej samej domenie.
- **Sesje** (`sessions.py`, tabela `xelto_sessions`): w bazie tylko hash
  tokenu, refresh token zaszyfrowany Fernetem. Bezczynność 2 h, maksimum 12 h.
  Co 5 min role są sprawdzane w Keycloaku: wyłączony użytkownik albo odebrana
  rola zaczynają obowiązywać najpóźniej po 5 minutach. Gdy Keycloak nie
  odpowiada, sesja działa dalej na ostatnich znanych rolach.
- **Bearer JWT** (`authn.py`): dla skryptów i API zgodnego z OpenAI. Token
  z Keycloaka (np. konto serwisowe `voicestudio-cli`, grant `client_credentials`),
  `aud` = `voicestudio`, role z `resource_access.voicestudio.roles`.
- **Brama** (`middleware.py`) siedzi tuż wewnątrz `CORSMiddleware`, dzięki
  czemu jej odmowy zachowują nagłówki CORS i marker `x-omnivoice-backend`
  (#1385). Dla każdego żądania:
  1. ustala tożsamość (JWT albo ciasteczko); poświadczenia upstreamu
     (klucz master, `ov_session`, PIN, `?api_key=`) są ignorowane;
  2. stosuje politykę trasy, a dla ciasteczek także CSRF (nagłówek
     `x-voicestudio-csrf` + Origin) i sprawdzenie Origin dla WebSocketów;
  3. dołącza upstreamowi gotowy principal i **syntetyczny adres klienta**:
     user to `192.0.2.10` z syntetycznej sieci zaufanej
     (`OMNIVOICE_TRUSTED_NETWORKS` ustawia sam `install()`), czyli uprawnienia
     konsumenta jak klient LAN; admin to `192.0.2.20` z principalem
     `ADMIN_SESSION`. Reverse proxy ani wywołanie z loopbacka nie dostaną więc
     uprawnień „lokalnej maszyny”;
  4. uruchamia żądanie w kontekście tożsamości (`ContextVar`).
- **Polityka tras** (`policy.py`) obejmuje wszystkie 360 tras. Klasy:

  | Klasa | Przykłady |
  |---|---|
  | PUBLIC | `/health`, `/startup/progress`, SPA, `/demo_audio` |
  | USER | profile, generowanie, historia, dubbing, projekty, batch, `/v1/*`, `/ws/*` |
  | USER-odczyt / ADMIN-zapis | `/pronunciation`, recepty licencji modeli |
  | ADMIN | silniki i modele (zapis), `/setup` (zapis), `/system`, `/api/settings`, `/media-tools`, `/calls`, MCP |
  | DISABLED | udostępnianie LAN/Tailscale, cloud sync, `/system/set-env`, trasy tylko desktopowe |

  Klasa to górna granica: bramki upstreamu działają dalej i mogą odmówić więcej.
- **Strażnicy merge'y**:
  - `test_policy_completeness.py`: każda nowa trasa upstreamu musi zostać sklasyfikowana;
  - `test_policy_vs_upstream_gates.py`: czyta z AST bramki upstreamu
    (`require_admin`, `require_desktop`…) i pada, gdy polityka byłaby
    luźniejsza niż upstream, poza przejrzaną listą odczytów dla userów.
- **Tożsamość** (`identity.py`): `ContextVar`. `None` oznacza brak
  tożsamości (fail-closed), `SYSTEM` to start aplikacji i migracje.
  `asyncio.to_thread` przenosi ją sam; resztę załatwia `propagation.py` (P2).
- **Osobna baza forka** (`store.py`): wszystkie tabele `xelto_*` (konta,
  sesje, własność wierszy, metadane, historia migracji) są w pliku `xelto.db`
  obok `omnivoice.db` (albo w `OMNIVOICE_MULTIUSER_DB`). W `omnivoice.db` nie
  ma żadnej tabeli forka, więc wyłączenie trybu albo porzucenie forka zostawia
  ją dokładnie taką, jaką zrobił upstream. Połączenia, które potrzebują obu
  (widoki, bootstrap, CLI), dołączają `xelto.db` przez `ATTACH … AS xelto`.
  Transakcja obejmująca oba pliki w trybie WAL nie jest atomowa między nimi:
  po awarii w pół zapisu wiersz może zostać bez właściciela (czyli
  niewidoczny, fail-closed) albo wpis własności bez wiersza (bez skutków).
- **Migracje forka** (`migrations/`, tabela wersji `xelto_alembic_version`):
  osobny łańcuch na `xelto.db`, więc nie tworzy drugiego heada w łańcuchu
  upstreamu. Uruchamia je opakowanie `core.db.init_db`. Przy starcie tabele
  forka, które wcześniejsze wersje założyły w `omnivoice.db`, są przenoszone
  do `xelto.db` i usuwane z `omnivoice.db` (jedna transakcja, jednorazowo).

### Izolacja danych (P2)

- **Tabele per user** (`db_views.USER_TABLES`): `voice_profiles`,
  `generation_history`, `dub_history`, `studio_projects`, `export_history`,
  `glossary_terms`, `jobs`. Pozostałe są wspólne (`db_scope.GLOBAL_TABLES`:
  ustawienia, wymowa, galeria, telefonia, zdalne workery, cloud sync,
  `job_events`, dostępne tylko przez swój job).
- **Własność** w tabeli `xelto_row_owner(tbl, row_id, owner)` w `xelto.db`;
  tabele upstreamu są nietknięte. Głosy demo (`is_demo=1`) nie mają właściciela: widzą je
  wszyscy, nikt nie może ich zmienić (brama odpowiada 403).
- **Hak w `core/db.py` `get_db`** (`db_scope.py`): połączenie otwarte dla usera
  to `XeltoConnection`, w którym każda tabela per user jest przesłonięta
  widokiem TEMP filtrowanym po właścicielu, z triggerami INSTEAD OF do zapisu
  i triggerami na tabeli bazowej (stemplowanie właściciela, blokada cudzych
  wierszy). Połączenie dodatkowo:
  - zwraca poprawne `cursor.rowcount` dla zapisów przez widok;
  - przepisuje UPSERT na tabeli usera na `main.<tabela>`;
  - wykonuje `executescript` (DDL z `ensure_schema`) na osobnym połączeniu systemowym.
- **Fail-closed**: połączenie bez tożsamości widzi puste tabele per user,
  a zapis do nich się nie udaje. `SYSTEM` (lifespan, `init_db`, CLI) dostaje
  zwykłe połączenie i widzi wszystko.
- **Admin też widzi tylko swoje dane.** Operacje między userami robi CLI.
  Klucz master (`ALLOW_MASTER_KEY`) działa jako osobny pseudo-user `__master_key__`.
- **Tożsamość w pracy w tle** (`propagation.py`, `hooks.py`):
  `ThreadPoolExecutor.submit`, `threading.Thread.start` i `JobQueue.submit`
  przenoszą kontekst zgłaszającego. `TaskManager` zapamiętuje go w
  `add_task` i wchodzi w niego w `worker` (hak w `core/tasks.py`).
- **Cache jobów dubbingu**: `dub_pipeline.get_job` sprawdza właściciela przed
  odczytem `_dub_jobs` (hak). Właściciel nowych jobów pochodzi z opakowanych
  `put_job`/`put_and_save_job`, a zapisanych z `xelto_row_owner`.
- **Bootstrap**: przy pierwszym logowaniu `OMNIVOICE_MULTIUSER_BOOTSTRAP_OWNER`
  dostaje wszystkie wiersze bez właściciela (raz na bazę, znacznik w `xelto_meta`).
- **CLI** (z `backend/`, najlepiej przy zatrzymanym serwerze):

  ```sh
  python -m xelto.cli assign-unowned --to <user>   # przypisz dane bez właściciela
  python -m xelto.cli purge-user <user>            # usuń wiersze usera (pliki zostają)
  python -m xelto.cli --db /ścieżka/omnivoice.db ...  # xelto.db obok niej
  ```

  `<user>` to username albo `sub` kogoś, kto choć raz się zalogował.
- **Strażnicy merge'y**:
  - `test_table_classification.py`: każde `CREATE TABLE` upstreamu musi być w USER_TABLES albo GLOBAL_TABLES;
  - `test_sql_lint.py`: nowe `RETURNING`, UPSERT, `lastrowid`, `CREATE VIRTUAL TABLE` na tabelach per user
    albo `executescript` gdziekolwiek wymagają przeglądu i wpisu na allowlistę.

### Frontend (`electron/src/renderer/src/xelto/`, tylko build web)

- `oidc-gate.tsx`: pyta `/api/auth/oidc/me`. 404 (brak trybu multiuser)
  oznacza zachowanie jak upstream. Bez sesji przekierowuje do logowania
  (z powrotem na tę samą stronę). Po 401 (sesja wygasła) loguje ponownie.
  Przy zmianie użytkownika w tej samej przeglądarce czyści `localStorage`
  poprzedniego (klucze `xelto_*` zostają).
- `user-menu.tsx`: nazwa, odznaka admina, „Wyloguj” (kończy też sesję w Keycloaku).
- `admin-visibility.ts`: zwykły user widzi w Ustawieniach tylko białą listę
  (Ogólne, Wygląd, Wymowa, Wsparcie); nowe strony upstreamu są domyślnie ukryte.
- Tłumaczenia: `electron/src/renderer/src/i18n/xelto/*.json` (21 języków,
  przestrzeń nazw i18next `xelto`), wywoływane przez `xt('…')`, a nie `t('…')`,
  bo `locale:check` sprawdza każde `t('…')` w katalogu upstreamu. Parytet
  pilnuje `xelto/i18n.test.ts`.

### Testy

- `tests/xelto_fork/`: fałszywy Keycloak (`fake_idp.py`, prawdziwe tokeny
  RS256), przepływ logowania, brama, konfiguracja, strażnicy polityki,
  sonda na prawdziwej `main.app` w podprocesie (`real_app_probe.py`).
- P2: `test_db_scope.py` (scoping na prawdziwych ścieżkach upstreamu:
  rowcount, UPSERT, `ensure_schema`, fail-closed), `test_propagation.py`,
  `test_ownership.py`, strażnicy oraz macierz izolacji na prawdziwej
  `main.app` (`isolation_probe.py`): bob nie listuje, nie czyta, nie zmienia
  i nie usuwa danych alice w żadnej z 7 tabel.
- `electron/src/renderer/src/xelto/*.test.ts(x)`: stany bramki, zmiana
  użytkownika, filtr admina, parytet tłumaczeń.
- Smoke test 2026-10-27 na prawdziwym Keycloaku 26.4 z importem realmu:
  logowanie z wymuszoną zmianą hasła, role, CSRF, wylogowanie, konto
  serwisowe `voicestudio-cli` (Bearer). Wszystko zgodnie z oczekiwaniami.
  Wniosek: użytkownicy w imporcie muszą mieć e-mail i imię/nazwisko, inaczej
  Keycloak 26 wymusza krok VERIFY_PROFILE.

## Spike: izolacja danych przez widoki TEMP (P0)

Prototyp: `backend/xelto/db_views.py`. Testy semantyki:
`tests/xelto_fork/test_db_view_spike.py` (na prawdziwym `_BASE_SCHEMA`).

| Założenie | Wynik |
|---|---|
| SELECT przez widok widzi tylko wiersze właściciela + demo | ✅ |
| INSERT przez widok odtwarza DEFAULT-y upstreamu i stempluje właściciela | ✅ |
| `INSERT OR REPLACE` na własnym wierszu | ✅ zastępuje |
| `INSERT OR REPLACE` na cudzym id | ✅ odrzucone (`IntegrityError`) |
| UPDATE/DELETE cudzego wiersza | ✅ przez widok: 0 zmian; przez `main.t`: odrzucone; wiersze demo tylko do odczytu |
| `cursor.rowcount` przez widok | ⚠ zawsze 0; `total_changes` zawyża (liczy też księgowanie własności). **Korekta: licznik wierszy z triggerów (`xelto_count()`)**, a nie delta `total_changes` |
| UPSERT na widoku | ✅ błąd zgodnie z oczekiwaniem; przepisanie na `main.t` działa i jest strzeżone |
| `RETURNING` na widoku | ⚠ **nie rzuca błędu**, ale zwraca wartości sprzed DEFAULT-ów (np. `language=None` zamiast `'Auto'`). Cicha rozbieżność: `job_events` zostaje tabelą bazową, a lint SQL z P2 musi wykrywać `RETURNING` na tabelach usera |
| `CREATE INDEX` / `ALTER TABLE` na połączeniu z widokami | ✅ błąd; na połączeniu SYSTEM przechodzi |
| Nowa kolumna upstreamu | ✅ `PRAGMA main.schema_version` się zmienia (klucz cache); po przebudowie triggery ją obejmują |

### Koszt per połączenie

`uv run python scripts/xelto/bench_db_views.py` (Windows 11, SQLite 3.53.1,
Python 3.11, 1000 połączeń, mediana):

| Wariant | Mediana |
|---|---|
| zwykłe połączenie jak `get_db` (WAL + FK) | 2,3–2,5 ms |
| widoki dla 7 tabel, DDL wykonywany instrukcja po instrukcji (prototyp) | 7,7 ms (+5,2 ms) |
| DDL z cache i jednym `executescript` | 5,6 ms (+3,3 ms) |

Cel z planu (< 3 ms narzutu) **nie jest osiągnięty**; najlepszy wariant daje +3,3 ms.

### Decyzja po spike'u

**Widoki TEMP (2026-10-27).** Semantyka działa. Narzut ~3 ms na połączenie
jest pomijalny przy operacjach GPU trwających sekundy, więc próg 3 ms z planu
uznajemy za nieistotny. Jawne filtry `owner_id` dałyby ~146 punktów konfliktu
z upstreamem. W P2:

- DDL cache'owany po `PRAGMA main.schema_version` i wykonywany jednym `executescript`;
- korekta `rowcount` przez licznik z triggerów (`xelto_count()`), nie przez `total_changes`;
- lint SQL wyłapuje `RETURNING` na tabelach usera.
