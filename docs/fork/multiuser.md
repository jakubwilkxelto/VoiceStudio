# Tryb wieloużytkownikowy (fork XELTO)

Konta przez Keycloak i rozdzielenie danych użytkowników na jednym serwerze
Docker. Pełny plan: `PLAN-26-10-2026.md`.

## Stan

**Faza P0 (szkielet).** Nie nadaje się do użycia produkcyjnego.

| Flaga | Zachowanie |
|---|---|
| `OMNIVOICE_MULTIUSER` nieustawiona | identycznie jak upstream; `xelto.install()` nic nie robi |
| `OMNIVOICE_MULTIUSER=1` | po `init_db` uruchamiają się migracje forka; brama przepuszcza tylko trasy PUBLIC (`GET /health`), reszta dostaje `401 login required` (WebSocket: close 1008), w logu pojawia się ostrzeżenie |

Logowanie dochodzi w P1.

## Jak to jest zbudowane

- Kod w `backend/xelto/`, testy w `tests/xelto_fork/`, skrypty w `scripts/xelto/`.
  Katalog testów nie nazywa się `tests/xelto`, bo jako pakiet przesłoniłby `xelto`.
- Jedyny hak w upstreamie to `xelto.install(app)` w `backend/main.py`, oznaczony `XELTO:`.
- **Brama** (`xelto/middleware.py`) siedzi tuż wewnątrz `CORSMiddleware`, a nie
  na samym zewnątrz. Dzięki temu preflighty i jej własne 401 zachowują nagłówki
  CORS oraz marker `x-omnivoice-backend` (#1385). Bramki upstreamu
  (BrowserGuard, Bearer, PIN) działają za nią.
- **Polityka tras** (`xelto/policy.py`): reguły `(metoda, wzorzec)` w pisowni
  `tests/fixtures/api_routes.txt`. Trasa bez reguły jest ADMIN. Strażnik
  `test_policy_completeness.py` porównuje trasy bez reguły z
  `tests/xelto_fork/fixtures/unclassified_routes.txt`, więc każda nowa trasa
  z upstreamu wywala test przy merge'u. Po klasyfikacji regeneruj:
  `uv run python scripts/xelto/dump_unclassified_routes.py`.
- **Tożsamość** (`xelto/identity.py`): `ContextVar`. `None` oznacza brak
  tożsamości (fail-closed), `SYSTEM` to start aplikacji i migracje.
  `asyncio.to_thread` przenosi ją sam, `run_in_executor` nie (patch w P2).
- **Migracje forka** (`xelto/migrations/`, tabela wersji
  `xelto_alembic_version`): osobny łańcuch, więc nie tworzy drugiego heada
  w łańcuchu upstreamu. Uruchamia je opakowanie `core.db.init_db`. Ręcznie:
  `python -c "from xelto import migrate; migrate.upgrade()"` z `backend/`
  na `sys.path`. `x0001` tworzy `xelto_users`, `xelto_sessions`,
  `xelto_row_owner`, `xelto_file_owner` i `xelto_meta`.

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
