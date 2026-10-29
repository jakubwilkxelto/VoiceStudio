# Tryb wieloużytkownikowy (fork XELTO)

Konta przez Keycloak i rozdzielenie danych użytkowników na jednym serwerze
Docker. Pełny plan: `PLAN-26-10-2026.md`.

## Stan

**Faza P4: tryb gotowy produkcyjnie dla ~10 userów.** Logowanie i role
działają. Każdy widzi tylko swoje głosy, historię, dubbingi, projekty,
glosariusze, eksporty i joby (patrz [Izolacja danych](#izolacja-danych-p2)),
a także swoje pliki, zadania w tle, joby batch, paczki projektów i zdarzenia
`/ws/events` (patrz [Pliki, pamięć i zdarzenia](#pliki-pamięć-i-zdarzenia-p3)).
Domyślny jest tryb `strict`: plik, którego właściciela nie da się ustalić,
nie jest serwowany nikomu (patrz [Utwardzenie](#utwardzenie-p4)). Dostęp
programowy per user (MCP, back-channel logout) to P5.

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
5. Konta startowe `studio-admin`, `alice` i `bob` mają hasło
   `VOICESTUDIO_INITIAL_PASSWORD`, zmieniane przy pierwszym logowaniu.
   **Na produkcji usuń `alice` i `bob`.** Nowym osobom nadaj w Keycloaku rolę
   klienta `voicestudio` → `voicestudio-user` (albo `voicestudio-admin`).
   Konto bez żadnej z tych ról nie wejdzie („not enrolled”).

   Są dwa różne konta administracyjne, każde z własnym hasłem:

   | Konto | Do czego | Hasło |
   |---|---|---|
   | `studio-admin` (realm `voicestudio`) | logowanie do VoiceStudio z rolą admina | `VOICESTUDIO_INITIAL_PASSWORD`, zmieniane przy pierwszym logowaniu |
   | `KEYCLOAK_ADMIN_USER` (domyślnie `admin`, realm `master`) | konsola Keycloaka `https://auth…/admin` | `KEYCLOAK_ADMIN_PASSWORD` |

6. **Pierwszą konfigurację robi `studio-admin`**: po zalogowaniu przechodzi
   kreator (sprawdzenie systemu, pakiety modeli, prywatność) raz dla całego
   serwera. Zwykli użytkownicy kreatora nie widzą; dopóki admin go nie
   skończy (modele zainstalowane, decyzja o analityce zapisana), widzą ekran
   „Serwer jest konfigurowany”, który sam przechodzi dalej. Token Hugging
   Face jest jeden dla całego serwera: ustawia go admin w Ustawienia →
   Poświadczenia albo `HF_TOKEN` w pliku `.env` wdrożenia.

Uwagi:

- Buduj z czystego klonu repo (na serwerze: `git clone`). `.dockerignore`
  upstreamu wyklucza tylko `node_modules/` w katalogu głównym, więc
  `electron/node_modules` z kopii roboczej dewelopera trafia do obrazu
  i psuje build (`vite: not found`). Lokalnie: `git worktree add`.
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
| `OMNIVOICE_MULTIUSER_FILES` | `strict` (domyślnie): plik bez ustalonego właściciela daje 404; `lenient`: serwowany każdemu zalogowanemu i logowany (tylko do diagnozy, ostrzeżenie przy starcie) |

Serwer **nie wystartuje**, gdy brakuje którejś z wymaganych zmiennych, gdy
ustawiono `OMNIVOICE_TRUSTED_NETWORKS` lub `OMNIVOICE_TRUST_HOST_LOOPBACK`,
gdy ustawiono `OMNIVOICE_API_KEY` bez zgody na klucz master, oraz gdy
publiczny URL nie jest https albo nie ma go w `OMNIVOICE_ALLOWED_ORIGINS`.

Uwaga dla deweloperów: `backend/main.py` wczytuje plik `.env` znaleziony
w katalogach nadrzędnych (`dotenv.find_dotenv()` + `core/env_startup.py`,
bez nadpisywania już ustawionych zmiennych). Jeśli jest w nim
`OMNIVOICE_API_KEY`, tryb multiuser odmówi startu.

### Lokalny test (bez TLS)

Do próby na jednej maszynie służy nakładka `deploy/keycloak/docker-compose.local.yml`:
`http://studio.localhost` i `http://auth.localhost` (przeglądarki rozwiązują
`*.localhost` na tę maszynę), bez certyfikatów, z
`OMNIVOICE_MULTIUSER_INSECURE_HTTP=1`. **Nie do produkcji.**

```sh
cp deploy/keycloak/.env.local.example deploy/keycloak/.env.local   # uzupełnij
docker compose --env-file deploy/keycloak/.env.local \
  -f deploy/docker-compose.keycloak.yml \
  -f deploy/keycloak/docker-compose.local.yml up -d --build
```

Dwa konta naraz najłatwiej sprawdzić w dwóch profilach przeglądarki (albo
w oknie zwykłym i incognito): ciasteczko sesji jest per profil.

Nakładka wyłącza też cache motywów Keycloaka: zmiany w
`deploy/keycloak/themes/` widać po odświeżeniu strony, bez restartu.

### Wygląd: jeden motyw

Na serwerze multiuser wszystko ma domyślny ciemny motyw VoiceStudio
(paleta `default`, tryb `dark`):

- **Strony logowania Keycloaka** (logowanie, błędy, reset i zmiana hasła,
  wylogowanie) używają motywu `voicestudio` z
  `deploy/keycloak/themes/voicestudio/login/`. To `keycloak.v2` z jednym
  arkuszem CSS (`resources/css/voicestudio.css`), bez szablonów `.ftl`, więc
  aktualizacje Keycloaka go nie psują. Kolory są przepisane z bloku dark
  w `electron/src/renderer/src/styles/t3-theme.css`: zmiana tam wymaga zmiany
  tu. Font Inter i logo są w motywie, więc strona nie pobiera nic z zewnątrz.
- **Aplikacja**: nikt, także admin, nie zmieni motywu. Przełącznik
  słońce/księżyc, wybór trybu i palet w Ustawieniach → Wygląd oraz polecenie
  w palecie poleceń są ukryte, a `xelto/theme-lock.tsx` przywraca motyw, gdy
  coś go zmieni (np. przycisk „Resetuj”). Desktop i serwer bez multiuser
  zostają jak w upstreamie.

Realm importuje się tylko przy pierwszym starcie Keycloaka. Na serwerze
uruchomionym wcześniej:

1. Odtwórz kontener z wolumenem motywu: `docker compose … up -d keycloak`.
2. Ustaw motyw: konsola admina → realm `voicestudio` → Realm settings →
   Themes → Login theme = `voicestudio`, albo (pyta o hasło admina konsoli):

   ```sh
   docker exec -it voicestudio-keycloak /opt/keycloak/bin/kcadm.sh config credentials      --server http://localhost:8080 --realm master --user admin
   docker exec voicestudio-keycloak /opt/keycloak/bin/kcadm.sh update realms/voicestudio      -s loginTheme=voicestudio
   ```

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
  4. sprawdza właściciela tego, co wskazuje ścieżka (job dubbingu, zadanie,
     job batch, paczka, profil, plik) oraz id, które kilka tras bierze z ciała
     JSON (`policy.BODY_OWNERS`), i odpowiada 404, gdy nie należy do
     wołającego (P3, P4);
  5. uruchamia żądanie w kontekście tożsamości (`ContextVar`).
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
  to `XeltoConnection`, w którym każda tabela per user jest do odczytu
  przesłonięta widokiem TEMP filtrowanym po właścicielu. Każdy zapis
  (`INSERT`, `REPLACE`, `UPDATE`, `DELETE`) kursor kieruje na `main.<tabela>`
  (`db_scope.rewrite`), gdzie pilnują go triggery na tabeli bazowej:
  - nowy wiersz dostaje właściciela;
  - cudzy wiersz jest przy `UPDATE`/`DELETE` pomijany (`RAISE(IGNORE)`),
    czyli zachowuje się jak niewidoczny;
  - zapis do wiersza wspólnego (demo) albo przejęcie cudzego id przez
    `INSERT OR REPLACE` jest odrzucane.

  `cursor.rowcount` liczy tylko wiersze wołającego. `executescript` (DDL
  z `ensure_schema`) idzie osobnym połączeniem systemowym.
- **Zgodność z SQLite.** Triggery nie mają `INSTEAD OF` ani celu zapisu
  z nazwą schematu: SQLite przed 3.51 (obraz Dockera ma 3.50.3) odrzuca oba
  („qualified table names are not allowed … within triggers”). Do wersji z
  2026-10-28 rozdzielenie danych nie działało przez to w ogóle w Dockerze, bo
  testy szły tylko na Windows z SQLite 3.53. Pilnuje tego test DDL w
  `test_db_scope.py`, a pełne testy forka na SQLite z obrazu uruchamia
  `sh scripts/xelto/test_in_image.sh` (po zmianach w `backend/xelto/` i po
  merge'ach upstreamu ruszających bazę).
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

### Pliki, pamięć i zdarzenia (P3)

Bez nowych haków w upstreamie: wszystko robi brama i podmiana atrybutów modułów.

- **Kontrola właściciela po parametrach ścieżki** (`resolvers.py`,
  `policy.PATH_OWNERS`). Każda trasa USER z parametrem ma przypisany sposób
  sprawdzenia, a test kompletności pada przy nowej trasie upstreamu:
  - `db-view`: handler czyta przez widoki z P2 (profile, historia, projekty…);
  - `dub`: właściciel joba dubbingu. Obejmuje też `DELETE /dub/history/{id}`,
    który upstream wykonuje (razem z usunięciem katalogu joba) nawet wtedy,
    gdy wiersz jest niewidoczny;
  - `task`: `/tasks/stream|cancel/{id}` — tożsamość zapamiętana w
    `task_manager` albo właściciel wiersza `jobs`;
  - `job`: `/audiobook/resume/{id}`;
  - `batch`, `bundle`, `file` (niżej);
  - `shared`: katalogi wspólne (galeria, archetypy, community, modele).

  Odmowa to zawsze 404, żeby nie zdradzać, że zasób istnieje.
- **Pliki** (`file_guard.py`) — dotyczy `/audio/*`, `/voice_audio/*`,
  `/audio/{id}.ogg|.opus|/quality` i `/preview/{plik}`. Plik jest serwowany, gdy:
  1. leży we wspólnym poddrzewie `/audio` (`voice_gallery/`,
     `archetype_previews/`, `marketplace/`);
  2. należy do wołającego według rejestru `xelto_file_owner`:
     - wpisy powstają z odpowiedzi tras (`resources.ResponseTap`): nagłówki
       `X-Audio-Path` / `X-Audio-Id` oraz adresy `/audio|voice_audio|preview/…`
       i pole `"output"` w JSON i SSE;
     - rejestrowane są tylko istniejące pliki, pierwszy właściciel wygrywa;
  3. baza wiąże go z wołającym:
     - `generation_history.audio_path`;
     - job longform (`audiobook_<id>…`, `story_<id>…`);
     - plik głosu (ścieżka w profilu albo nazwa zaczynająca się od id profilu).
       Głosy demo są wspólne.

  Cudzy plik to 404. O pliku, którego nic nie wyjaśnia, decyduje
  `OMNIVOICE_MULTIUSER_FILES`. Log `xelto file guard (lenient): serving unowned …`
  pokazuje, czego jeszcze brakuje przed P4.
- **Joby batch** (trzymane tylko w pamięci upstreamu):
  - `batch._jobs` zastępuje `resources.BatchJobs`, który zapamiętuje twórcę każdego joba;
  - opakowane `_run_batch_pipeline` wykonuje job jako jego twórca, a job bez właściciela kończy się błędem.

  To naprawia błąd z P2: worker startuje przy pierwszym enqueue i bez tego
  wykonywał każdy job jako tamten pierwszy user.
- **Paczki projektów** (`outputs/project_handoff/<uuid>`): opakowane
  `project_bundles._directory` zapisuje `bundle/<uuid>` dla wołającego, gdy
  katalog jeszcze nie istnieje (eksport i import zawsze tworzą nowy).
- **Listy spoza bazy** brama przycina do elementów wołającego:
  - `GET /batch/jobs`;
  - `GET /project-bundles`;
  - `GET /audiobook/jobs` (skan dysku).

  Nieoczekiwany kształt odpowiedzi daje pustą listę (fail-closed).
- **Zdarzenia** (`events.py`): kolejka każdego słuchacza `/ws/events` staje się `ScopedQueue`.
  - Zdarzenia o danych usera (`profiles`, `generation_history`, `dub_history`,
    `export_history`, `projects`, `glossary`) trafiają tylko do słuchaczy
    zalogowanych jako emitujący. Tożsamość emitującego jest dostępna, bo
    `emit` planuje rozgłoszenie z kopią jego kontekstu.
  - Zdarzenie o danych usera wyemitowane bez usera (SYSTEM) nie trafia do nikogo.
  - Pozostałe (`model_status`, `system_error`) trafiają do wszystkich.
- Patche routerów zakłada opakowany `init_db` (faza B startu). Przy odroczonym
  starcie routery importują się dopiero po `install()`.
- `purge-user` usuwa też wpisy usera z `xelto_file_owner`. Pliki zostają na dysku.

### Utwardzenie (P4)

Bez nowych haków w upstreamie: brama, patche atrybutów modułów i triggery widoków.

- **`strict` domyślnie.** Plik, którego nie wiąże z wołającym ani baza, ani
  rejestr, daje 404. `lenient` zostaje jako przełącznik awaryjny do diagnozy.
- **Brak tożsamości.** Zapis do tabeli usera bez tożsamości kończy się
  błędem `xelto: no identity for a write to a per-user table` (trigger
  `BEFORE INSERT`). Wcześniej też się nie udawał, ale z nieczytelnym
  `NOT NULL constraint failed`. Brama zamienia go na 500 z logiem.
  Każde połączenie bez tożsamości jest logowane (WARN), a
  `OMNIVOICE_MULTIUSER_AUDIT=1` dodaje stos wywołań. Budowanie aplikacji przy
  imporcie `main` (`_phase_a_build`, odczyt ustawień) działa w zakresie
  SYSTEM, więc zwykły start nie loguje niczego.
- **Id w ciele żądania** (`policy.BODY_OWNERS`): `POST /dub/ingest-url`
  i `/dub/translate` przyjmują `job_id` od klienta. Brama czyta JSON (do
  1 MB), a cudze id daje 404. Nowe id, którego nikt nie ma (brak
  właściciela i katalogu), przechodzi. Naprawia to wyciek z P3: bob mógł
  podać `job_id` alice, a upstream przy nieudanym pobraniu usuwał cały
  katalog jej dubbingu. Pozostałe pola `*_id` tras USER są czytane przez
  widoki (`policy.BODY_IDS_VIA_VIEWS`). Strażnik w `test_real_app.py`
  czyta schemat OpenAPI i pada na każdym nowym polu `*_id`.
- **Profile** (resolver `profile`): `/profiles/{id}*`, `/personas/export/{id}`,
  `/marketplace/export/{id}`. Upstream czyta i zapisuje pliki nazwane id
  profilu (portret, audio referencyjne) także wtedy, gdy wiersz jest
  niewidoczny. Cudzy profil, albo pliki po nim bez wiersza, dają 404.
- **Rejestr plików odporny na echo.** Baza decyduje przed rejestrem,
  a `ResponseTap` rejestruje tylko pliki zapisane w trakcie żądania (mtime) i
  nigdy pliki głosów. Wcześniej bob mógł zapisać ścieżkę pliku alice w stanie
  swojego projektu, odczytać projekt i zostać właścicielem jej pliku.
- **Pliki wskazane przez klienta**:
  - eksport paczki projektu (`project_bundles._asset`): obcy plik jest
    pomijany jak brakujący (pole dokumentu) albo eksport kończy się 400
    (zasoby `handoff`);
  - `emo_audio` w `/ws/tts` (`tts_stream.resolve_emotion_clip`) przyjmuje
    tylko pliki wołającego.

  Oba sprawdza `file_guard.path_allowed`.
- **Preferencje dyktowania** (`/dictation/prefs`) są w globalnym `prefs.json`,
  więc zapis jest dla admina, a odczyt dla wszystkich.
- **Powiadomienia systemowe** (`/system/notifications`: brak tokenu HF, brak
  ffmpeg, mało miejsca, GPU, crash poprzedniej sesji) dotyczą serwera, więc
  widzi je tylko admin. Zwykły user dostaje pustą listę (filtr w bramie).
- **Kreator pierwszego uruchomienia** (`xelto/setup.tsx`, hak w
  `components/setup-gate.tsx`): o tym, czy konfiguracja jest skończona,
  decyduje serwer (`/setup/status` → `models_ready` i odpowiedź na pytanie
  o analitykę), nie localStorage przeglądarki. Admin przechodzi kreator raz,
  a user go nie widzi. Dopóki serwer nie jest gotowy, user widzi ekran
  czekania sprawdzany co 30 s. Gdy serwer nie odpowiada, user jest
  wpuszczany, a admin wraca do zachowania upstreamu.
- **Przeglądarka.** Przy zmianie usera, poza `localStorage`, czyszczone są
  też `sessionStorage` i bazy IndexedDB. Upstream trzyma w
  `voicestudio.workflow-runs` przebiegi workflow razem z audio.
- **Ryzyka przyjęte świadomie**:
  - `/dub/upload` z `job_id` istniejącego joba daje 409, więc zdradza, że
    takie id istnieje. Id to 8 znaków hex, a odpowiedź nie daje dostępu;
  - `sweep_retired_voice_files` sprawdza „czy plik jest jeszcze używany”
    tylko w profilach wołającego. Pliki głosów mają prefiks id profilu, więc
    wspólny plik praktycznie nie występuje;
  - okładki audiobooków (`outputs/audiobook_covers/<12 hex>`) nie są
    serwowane przez `/audio`. Ich ścieżkę zna tylko autor, a nazwa jest losowa;
  - joby batch i ich właściciele są tylko w pamięci, tak jak w upstreamie,
    więc restart serwera je kończy.

### Frontend (`electron/src/renderer/src/xelto/`, tylko build web)

- `oidc-gate.tsx`: pyta `/api/auth/oidc/me`. 404 (brak trybu multiuser)
  oznacza zachowanie jak upstream. Bez sesji przekierowuje do logowania
  (z powrotem na tę samą stronę). Po 401 (sesja wygasła) loguje ponownie.
  Przy zmianie użytkownika w tej samej przeglądarce, zanim wyrenderuje
  aplikację, czyści `localStorage` poprzedniego (klucze `xelto_*` zostają),
  `sessionStorage` i bazy IndexedDB.
- `theme-lock.tsx`: jeden motyw w trybie multiuser (domyślny ciemny), patrz
  *Wygląd: jeden motyw*.
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
- P3:
  - `test_file_guard.py`, `test_gate_files.py` (rejestr z odpowiedzi, lenient/strict);
  - `test_event_scope.py`, `test_router_owners.py` (worker batch, paczki, filtry list);
  - test kompletności `PATH_OWNERS`;
  - sonda `isolation_runtime_probe.py` na prawdziwej `main.app`: bob dostaje 404
    na pliki, zadania, joby batch, paczki i dubbing alice, jego listy są puste,
    a na `/ws/events` nie słyszy jej zdarzeń.
- P4:
  - `isolation_matrix_probe.py` (`test_isolation_matrix.py`): bob woła
    **każdą** trasę USER z parametrem (65 za resolverami, 15 za widokami)
    z id alice. Trasy za resolverami dają 404, żadna odpowiedź nie zawiera
    danych alice, a jej wiersze i pliki są potem identyczne. Do tego id
    w ciele, eksport paczek (zawartość zipa), echo ścieżek, `/dictation/prefs`,
    `/v1/audio/speech` z jej głosem, `emo_audio` i każdy rodzaj zdarzenia
    o danych usera;
  - `e2e_probe.py` (`test_e2e_strict.py`), tryb `strict` + audyt, fałszywe
    modele z testów upstreamu (silnik, ASR, demucs, licencje):
    generowanie, klonowanie, dubbing (upload → przygotowanie → transkrypcja
    → generowanie → pobranie), batch i audiobook. Alice dostaje swoje pliki,
    bob 404, a log nie zawiera żadnego wpisu o braku tożsamości ani pliku
    bez właściciela (kolektor ma test własny);
  - wycieki, które naprawia P4, sprawdzone na kodzie z P3: sonda macierzy
    pada tam na `ingest-url`, eksporcie paczek, echu ścieżek, preferencjach
    dyktowania i `emo_audio`.
- `electron/src/renderer/src/xelto/*.test.ts(x)`: stany bramki, zmiana
  użytkownika (`localStorage`, `sessionStorage`, IndexedDB), filtr admina,
  parytet tłumaczeń.
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
