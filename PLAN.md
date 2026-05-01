# IP-MCP — 特許情報取得 API を MCP サーバー化する計画書

| 項目 | 値 |
|------|----|
| 作成日 | 2026-05-01 |
| 作成者 | kitepon (Claude Code 同伴) |
| GitHub | <https://github.com/kitepon-rgb/IP-MCP> |
| デプロイ先 | `<DEPLOY_HOST>`（自宅 LAN, SSH ユーザー `<SSH_USER>`） |
| 実行形態 | Docker (`docker compose up -d`) |
| 言語 | Python 3.12 (FastMCP + httpx) |
| 参考実装 | `../IP`（既存 Flask 版、JPO API クライアントが完成済み） |

---

## 1. 背景・なぜ作るのか

ユーザーは特許庁 (JPO) の **特許情報取得 API** の利用権を持っており、Claude/その他 LLM から自然言語で特許の書誌・経過情報・引用文献などを引けるようにしたい。これまでは Flask の REST API として作っていた (`Documents\Program\IP`) が、Claude Desktop / Claude Code から直接呼ぶには **MCP (Model Context Protocol)** で公開した方が体験が良い。

ローカル LAN 上の `<DEPLOY_HOST>` に Docker で常駐させ、複数台のクライアント (PC・iPad の Claude.ai 等) から共通のサーバーを参照する構成にする。

---

## 2. 重要前提（API 調査結果サマリー）

公式 API の正体を取り違えると設計を誤るので最初に整理する。

- **正式名称**: 「特許情報取得 API」（特許庁 提供）。`J-PlatPat の API` ではない（J-PlatPat は Web UI のみ）。
- **エンドポイント基底**: `https://ip-data.jpo.go.jp`
  - 認証: `POST /auth/token`（OAuth2 Resource Owner Password Grant、`grant_type=password|refresh_token`）
  - 国内特許: `GET /api/patent/v1/...`
  - OPD（五庁ファミリー）: `GET /opdapi/patent/v1/...`
- **トークン**: アクセス1時間 / リフレッシュ8時間。同一 ID 並行アクセス可。
- **レート制限（自主制御義務）**: 国内 10回/分、OPD 5回/分。日次上限はエンドポイントごとに 30〜800/日（2026年3月から国内は2倍緩和）。レスポンスの `result.remainAccessCount` で残量が分かる。
- **形式**: JSON、UTF-8 固定。出願番号は半角数字10桁。
- **statusCode（HTTP は常に 200）**: `100`=成功 / `107`=該当なし / `203`=日次上限 / `204/208`=パラメータエラー / `210`=トークン無効 / `303`=高負荷リトライ / `999`=想定外。
- **公式 API に「無い」もの（最大の落とし穴）**:
  - キーワード / 全文 / クレーム検索
  - IPC・FI・F ターム検索
  - 発明者名検索（出願人・代理人のみ）
  - 公開番号→書誌の直接取得（`case_number_reference` で出願番号に変換してから経過情報を引く2段構え）

→ キーワード検索などの「根本的な検索」は公式 API では絶対に賄えない。本プロジェクトでは**フォールバックではなく独立した別機能**として、外部検索 (Google Patents 等) を明示的に分離して実装する（後述セクション 4.2）。

---

## 2.5. 設計原則: 公式 API と外部検索の分離（重要）

ユーザー方針「**安易なフォールバックは禁止、やむを得ない場合を除く**」に従い、本サーバーは以下のルールで設計する。

1. **公式 JPO API ベースのツール群** と **外部検索ツール群** は MCP 上の別カテゴリで提供し、ツール名・description で出典を明示する。
2. **公式 API のツールが失敗したからといって、自動で外部検索に逃げない**。失敗は失敗としてエラーを返す（LLM に判断させる）。
3. 外部検索ツールは、ユーザー（または LLM）が「キーワードで探したい」と明示的に呼んだときだけ動く。
4. 各ツールの description 冒頭に **データソース** を明記する：
   - 公式: `Source: 特許庁 特許情報取得API (公式)`
   - 外部: `Source: Google Patents (非公式 XHR、参考用)`
5. レスポンスにも `source` フィールドを必ず入れ、LLM が「これは公式データか参考データか」を判別できるようにする。

唯一の例外（やむを得ないフォールバック）として認めるのは:
- アクセストークンが期限切れ → 自動再取得（同一 API 内の再試行、外部に逃げない）
- statusCode 303（一時障害）→ 指数バックオフで同一 API を再試行

これら「同じ公式 API 内での再試行」だけをフォールバックの許容範囲とする。

---

## 3. スコープ

### Phase 1A（MVP, 公式 API 部分）
公式 JPO API ベースの「番号→詳細」MCP ツール一式（後述 4.1）。

### Phase 1B（MVP, 外部検索部分）
キーワード検索専用の独立ツール（後述 4.2）。Phase 1A とはコード上も MCP カテゴリ上も分離。

### Phase 1.5（運用安定化, 完了済 2026-05-01）
- ✅ OAuth Provider の SQLite 永続化（`SqliteOAuthProvider`、`./data:/app/data`）
- ✅ ヘアピン NAT 問題回避（Windows hosts に LAN 直結エントリ）
- ✅ アクセスログ / メトリクス（`logs/access.jsonl` JSONL + `scripts/summarize_logs.py`）
- ✅ マスターパスワード rotate 手順（[OPERATIONS.md](OPERATIONS.md)）

### Phase 2（拡張, 後日）
拒絶理由通知書 PDF の構造化抽出、AI レビュー、欧州 OPS / WIPO PATENTSCOPE 補完。

### 非スコープ
- Web UI（HTML レンダリング）— Claude が UI なので不要
- 商用配布・第三者向け公開（自宅 LAN 限定）
- 実用新案（API 範囲外）
- **公式 API と外部検索を内部で繋ぐ「自動フォールバック」**（明示的な禁止）

---

## 4. 公開する MCP ツール（Phase 1）

すべて Python の MCP SDK (`mcp` + FastMCP) で `@mcp.tool()` として公開。各ツールの description 冒頭に **データソース表記** を必ず付け、「データ鮮度: 日次更新 / 対象: 2003年7月以降の特許」も併記して幻覚を抑える。

### 4.1 公式 JPO API ベースのツール（Phase 1A）

`Source: 特許庁 特許情報取得API (公式)` を description 冒頭に明記。レスポンスも `{"source": "jpo_official", ...}`。番号入力の正規化された詳細取得専用。**他の外部 API には絶対に逃げない。**

| MCP ツール名 | 役割 | 内部で叩く JPO API |
|---|---|---|
| `jpo_convert_patent_number` | 出願⇄公開⇄登録 番号変換 | `/case_number_reference/{種別}/{番号}` |
| `jpo_get_patent_progress` | 経過情報（フル / シンプル切替） | `/app_progress/{出願番号}` または `/app_progress_simple/{出願番号}` |
| `jpo_get_patent_registration` | 登録情報・権利状態 | `/registration_info/{出願番号}` |
| `jpo_get_patent_citations` | 引用文献一覧 | `/cite_doc_info/{出願番号}` |
| `jpo_get_divisional_apps` | 分割出願情報 | `/divisional_app_info/{出願番号}` |
| `jpo_get_priority_apps` | 優先基礎出願情報 | `/priority_right_app_info/{出願番号}` |
| `jpo_lookup_applicant` | 出願人名⇄コード（**完全一致のみ**、これを description に明記して LLM に伝える） | `/applicant_attorney[_cd]/{...}` |
| `jpo_get_patent_documents` | 申請書類 / 拒絶理由 / 発送書類の実体ファイル | `/app_doc_cont_*` |
| `jpo_get_opd_family` | 五庁ファミリー情報 | `/opdapi/patent/v1/family_*` |
| `jpo_get_jpp_url` | J-PlatPat 固定 URL 生成 | `/jpp_fixed_address/{出願番号}` |
| `jpo_fetch_full_record` | 同一番号で上の複数ツールを束ねて 1 回で返す高位ツール（公式 API 内部のみで完結） | 複合 |

各ツールは `result.statusCode` を解釈して以下に正規化する:
- 成功 → `{ ok: true, source: "jpo_official", data, remaining_today }`
- 該当なし → `{ ok: false, source: "jpo_official", kind: "not_found" }` ← **ここで外部検索に逃げない**
- 日次上限 → `{ ok: false, source: "jpo_official", kind: "rate_limited_daily", retry_after: "翌0時" }`
- パラメータエラー → MCP エラー（LLM に修正を促す）
- 一時障害（303 等）→ 公式 API 内で指数バックオフ再試行後、それでも駄目なら `{ ok: false, kind: "transient" }`

### 4.2 外部キーワード検索ツール（Phase 1B、独立機能）

`Source: Google Patents (非公式 XHR エンドポイント、参考用)` を description 冒頭に明記。レスポンスも `{"source": "google_patents_unofficial", ...}`。**Phase 1A とはコード階層・呼び出し元・ログが完全に分離**されており、LLM が明示的にこのツールを選んだ場合だけ動く。

| MCP ツール名 | 役割 | 内部経路 |
|---|---|---|
| `external_search_patents_by_keyword` | 自然語キーワード・出願人・IPC・日付レンジで日本特許を検索し、公開番号リストを返す | Google Patents XHR `https://patents.google.com/xhr/query` |
| `external_search_patents_by_assignee` | 出願人名（部分一致可）で検索 | 同上、`assignee:` クエリ |

**運用ルール**:
- description 冒頭に `⚠ 非公式データソース。本ツールが返した公開番号は、必ず公式ツール (jpo_*) で再取得して検証してください。` と明記する。
- レート: 3 秒間隔（既存 IP プロジェクト準拠）。503 を 3 回まで指数バックオフ再試行（同一エンドポイント内の再試行のみ）。
- 失敗時は `{ ok: false, source: "google_patents_unofficial", kind: "search_unavailable" }` を返し、**公式 API には絶対にフォールバックしない**。
- LLM への推奨フロー: 「外部検索で公開番号を見つける → `jpo_convert_patent_number` で出願番号化 → `jpo_fetch_full_record` で正式データ取得」をツール description に書いて誘導する。

---

## 5. システム構成

```
┌────────────────────────────────────────┐
│  Clients (Claude Desktop / Claude Code) │
│   on Windows / iPad over LAN            │
└──────────────────┬──────────────────────┘
                   │  MCP over HTTP+SSE (port 8765)
                   ▼
┌────────────────────────────────────────────────────┐
│  <DEPLOY_HOST>   docker compose                      │
│  ┌──────────────────────────────────────────────┐  │
│  │ ip-mcp                                       │  │
│  │  ├─ FastMCP server (uvicorn / sse transport) │  │
│  │  ├─ [official] JpoClient (httpx + token)    │  │
│  │  ├─ [official] RateLimiter (10/min, 5/min)  │  │
│  │  ├─ [official] Cache (SQLite WAL, 24h TTL)  │  │
│  │  ├─ ── 上下は独立、相互に呼ばない ──       │  │
│  │  └─ [external] GooglePatentsSearch          │  │
│  │       (3s 間隔, 503 再試行, 別ロガー)       │  │
│  │  Volumes:                                    │  │
│  │    ./cache  → /app/cache                     │  │
│  │    ./logs   → /app/logs                      │  │
│  │    ./data   → /app/data  (OAuth SQLite)      │  │
│  │  Env: JPO_USERNAME / JPO_PASSWORD            │  │
│  └──────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────┘
```

- **MCP トランスポート**: HTTP + SSE（Claude Desktop の「Remote MCP」設定で `http://<DEPLOY_HOST>:8765/sse` を指す）。stdio は LAN 越しに使えないので採用しない。
- **アクセス制御**: 自宅 LAN 内のみ。公開 IP には絶対バインドしない（Compose 側で `<DEPLOY_HOST>:8765:8765` に明示バインド）。
- **シークレット**: JPO の ID/PW は `<DEPLOY_HOST>:/home/<SSH_USER>/ip-mcp/.env` に置き、`docker compose --env-file` で読ませる。Git には絶対コミットしない（`.gitignore` で除外）。

---

## 6. リポジトリ構造（GitHub: kitepon-rgb/IP-MCP）

```
IP-MCP/
├─ PLAN.md                         (本書)
├─ README.md                       (使い方・MCP クライアント設定例)
├─ .gitignore                      (.env, cache/, logs/ を除外)
├─ .env.example                    (JPO_USERNAME=  JPO_PASSWORD= など)
├─ pyproject.toml                  (依存: mcp[cli], httpx, pydantic, anyio, hishel)
├─ Dockerfile                      (python:3.12-slim ベース)
├─ docker-compose.yml              (ip-mcp サービス + volume + LAN バインド)
├─ src/ip_mcp/
│  ├─ __init__.py
│  ├─ server.py                    (FastMCP のエントリ。@mcp.tool 群を登録)
│  ├─ jpo/
│  │  ├─ client.py                 (OAuth2 トークン管理 + httpx クライアント)
│  │  ├─ rate_limiter.py           (sliding-window 10/min, 5/min)
│  │  ├─ status_codes.py           (statusCode→例外マッピング)
│  │  ├─ normalize.py              (出願/公開/登録番号の10桁化)
│  │  └─ models.py                 (Pydantic モデル — OpenAPI から自動生成)
│  ├─ tools_official/              (4.1 公式 JPO API ベース、jpo_* tools)
│  │  ├─ convert.py                (番号変換)
│  │  ├─ progress.py               (経過情報)
│  │  ├─ registration.py           (登録情報)
│  │  ├─ citations.py              (引用文献)
│  │  ├─ documents.py              (書類実体)
│  │  ├─ applicant.py              (出願人)
│  │  ├─ opd.py                    (五庁ファミリー)
│  │  └─ fetch_full_record.py      (公式 API 内コンポジット)
│  ├─ tools_external/              (4.2 外部検索、external_* tools)
│  │  └─ google_patents_search.py  (Google Patents XHR、独立実装)
│  │  ※ tools_official とは import 関係を作らない
│  ├─ cache/
│  │  └─ sqlite_cache.py           (24h TTL, hishel + sqlite)
│  └─ logs/
│     └─ jsonl_logger.py
├─ tests/
│  ├─ test_normalize.py
│  ├─ test_status_codes.py
│  └─ test_tools_smoke.py          (録画レスポンスを使ったオフラインテスト)
└─ scripts/
   ├─ token_check.sh               (起動前にトークン取得が通るか確認)
   └─ deploy.sh                    (<DEPLOY_HOST> へ rsync + ssh で再起動)
```

---

## 7. 既存 IP プロジェクトからの再利用マップ

そのまま移植 / 軽い改造で持ち込めるもの。元ファイルは `../IP\` 配下。

| 既存ファイル:行 | 関数 | 移植先 |
|---|---|---|
| `jpo_patent_claims_fetch.py:391` | `fetch_access_token()` | `src/ip_mcp/jpo/client.py` |
| `jpo_patent_claims_fetch.py:421` | `build_headers()` | `src/ip_mcp/jpo/client.py` |
| `jpo_patent_claims_fetch.py:468` | `fetch_json()` | `src/ip_mcp/jpo/client.py`（httpx 化） |
| `jpo_patent_claims_fetch.py:93/102/111` | `normalize_publication/application/registration_number()` | `src/ip_mcp/jpo/normalize.py` |
| `jpo_patent_claims_fetch.py:2542` | `fetch_patent_documents()` | `src/ip_mcp/tools/fetch_full_record.py` |
| `jpo_patent_claims_fetch.py:2711` | `search_google_patents()` + `SearchUnavailableError` | `src/ip_mcp/tools_external/google_patents_search.py`（**独立、jpo クライアントから呼ばない**） |
| `patent_api/_helpers.py:45` | `parse_identifier()` | `src/ip_mcp/jpo/normalize.py` |
| `patent_api/_helpers.py:143` | `build_patent_status()` | `src/ip_mcp/tools/registration.py` |
| `patent_api/_shared.py:46` | `load_local_env()` | `src/ip_mcp/server.py`（起動時） |

**捨てる**: Flask Blueprint (`patent_api/*.py` の route 層)、`templates/`、`static/`、`web_app.py` の HTML / OCR / AI レビュー（Phase 2 で別途検討）。

---

## 8. Docker 構成

### `Dockerfile`
```dockerfile
FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
      curl ca-certificates && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml uv.lock ./
RUN pip install --no-cache-dir uv && uv sync --frozen --no-dev
COPY src ./src
ENV PYTHONUNBUFFERED=1
EXPOSE 8765
HEALTHCHECK --interval=60s --timeout=5s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8765/healthz || exit 1
CMD ["uv", "run", "ip-mcp", "--host", "0.0.0.0", "--port", "8765"]
```

### `docker-compose.yml`
```yaml
services:
  ip-mcp:
    build: .
    image: ip-mcp:latest
    container_name: ip-mcp
    restart: unless-stopped
    env_file: .env
    ports:
      - "<DEPLOY_HOST>:8765:8765"   # LAN 内のみ
    volumes:
      - ./cache:/app/cache
      - ./logs:/app/logs
      - ./data:/app/data           # OAuth クライアント・トークンの SQLite (再起動で消えない)
```

### `.env.example`
```
JPO_USERNAME=
JPO_PASSWORD=
JPO_API_BASE=https://ip-data.jpo.go.jp
JPO_ENABLE_OPD=1
EXTERNAL_GOOGLE_PATENTS_ENABLED=1     # 4.2 の外部検索ツールを公開するか
LOG_LEVEL=INFO
```

---

## 9. デプロイ手順（<DEPLOY_HOST> / SSH ユーザー <SSH_USER>）

### A. サーバー側 一回だけの初期セットアップ
```bash
# ローカルから
ssh <SSH_USER>@<DEPLOY_HOST>

# サーバー上で
sudo apt-get update
sudo apt-get install -y docker.io docker-compose-plugin git
sudo usermod -aG docker <SSH_USER>        # 一度ログアウトして再ログイン
mkdir -p ~/ip-mcp && cd ~/ip-mcp
git clone https://github.com/kitepon-rgb/IP-MCP.git .
cp .env.example .env
nano .env                            # JPO_USERNAME / JPO_PASSWORD を記入
chmod 600 .env
```

### B. 起動 / 更新
```bash
ssh <SSH_USER>@<DEPLOY_HOST> "cd ~/ip-mcp && git pull && docker compose up -d --build"
docker compose logs -f ip-mcp        # ログ追跡
```

### C. クライアント (Claude Desktop / Claude Code) 設定
Claude Desktop の `claude_desktop_config.json` 例:
```json
{
  "mcpServers": {
    "ip-mcp": {
      "transport": { "type": "sse", "url": "http://<DEPLOY_HOST>:8765/sse" }
    }
  }
}
```
Claude Code は `claude mcp add ip-mcp --transport sse http://<DEPLOY_HOST>:8765/sse` でも追加可能。

---

## 10. 検証手順（受け入れ基準）

1. **起動確認**: `curl http://<DEPLOY_HOST>:8765/healthz` → `{"ok": true}` が返る。
2. **トークン取得確認**: コンテナ内 `scripts/token_check.sh` が成功（statusCode 100）。
3. **番号変換**: MCP `convert_patent_number` で `特開2010-228687` → 出願番号が返る。
4. **経過情報**: `get_patent_progress` で `JP-2025-173545` 相当の出願番号 → タイトル・請求項数を含む JSON。
5. **引用文献**: `get_patent_citations` で文献リストが返る。
6. **登録情報**: `get_patent_registration` で `権利存続中` 等の status が返る。
7. **外部キーワード検索（独立機能）**: `external_search_patents_by_keyword(query="無人搬送車")` で5件以上返り、レスポンスの `source` が `google_patents_unofficial` になっている。503 時は同一エンドポイント内でのみ再試行（公式 API には絶対に繋がらない）。
8. **レート制限**: 11req/分を投げて、11発目が `rate_limited_per_minute` で待機 or エラー化される。
9. **キャッシュ**: 同一番号を2回連続呼び、2回目が `cache_hit` で外部 API を叩かない（ログで確認）。
10. **Claude Desktop からの呼び出し**: 自然言語で「特開2010-228687 の登録状況と引用文献を教えて」→ ツールが連携実行され、要約が返ってくる。

---

## 11. 段階的な進め方

| ステップ | 内容 | 完了基準 |
|---|---|---|
| S1 | 本リポジトリ初期化（`pyproject.toml`, `Dockerfile`, `compose`, `.env.example`, `.gitignore`） | `docker compose build` が通る |
| S2 | `JpoClient` 実装（トークン管理 + httpx + statusCode 解釈） | `scripts/token_check.sh` 成功 |
| S3 | `convert_patent_number`, `get_patent_progress` の 2 ツールだけ MCP 公開 | Claude Desktop から呼べる |
| S4 | 残りのツール群を順次追加（registration, citations, documents, applicant, opd） | 全ツールが smoke test 緑 |
| S5 | キャッシュ + レートリミッタ + 構造化ログ | 受け入れ基準 8/9 が通る |
| S6 | 外部キーワード検索ツール（4.2）を `tools_external/` 配下に**独立実装** | 受け入れ基準 7 が通る、かつ `tools_official/` から `tools_external/` への import が 0 |
| S7 | `<DEPLOY_HOST>` へデプロイ + Claude Desktop / Code 接続確認 | 受け入れ基準 10 が通る |
| S8 | OAuth 2.1 (DCR + PKCE + マスターパスワード) + サブドメイン公開 (Caddy + Let's Encrypt) | iPhone Claude / claude.ai から OAuth 経由で疎通 |
| S9 | OAuth Provider の SQLite 永続化 (`SqliteOAuthProvider`、`./data:/app/data` ボリューム) + 同 LAN ヘアピン NAT 回避 (Windows hosts に LAN 直結エントリ) | コンテナ再起動・再ビルド後もクライアント登録・トークンが生き残る (Phase 1.5 の OAuth 部分完了) |
| S10 | アクセスログ JSONL (`logs/access.jsonl`) + 集計スクリプト (`scripts/summarize_logs.py`) + 運用手順書 ([OPERATIONS.md](OPERATIONS.md)) | 日次クォータ消費が `--days 1` で見える、マスターパスワード rotate 手順が文書化済 (Phase 1.5 完了) |

S1〜S6 はローカルで完結。S7 以降は LAN ホストへのデプロイ・公開作業。

**進捗 (2026-05-01)**: S1〜S10 まで完了 = Phase 1A / 1B / 1.5 すべて完了。Phase 2 は未着手。

---

## 12. リスクと対処

| リスク | 影響 | 対処 |
|---|---|---|
| ~~OAuth Provider がインメモリ~~ → **解決済 (2026-05-01)**: SQLite 永続化 (`SqliteOAuthProvider`, `./data:/app/data`)。コンテナ再起動・再ビルド後もクライアント登録・アクセス・リフレッシュトークンが生き残る。 | （リスク解消） | （対応不要） |
| OPD API の新規申請が 2024-08 以降停止中 | OPD 系ツールが既存契約者しか使えない | **2026-05-01 確認済み: ユーザーは既存契約者**（旧 IP プロジェクトの cache に `global_doc_list.json` が `statusCode 203 = 1日上限超過` で記録されている = トークン有効）。OPD ツールは Phase 1 から含めて OK。 |
| Google Patents XHR が予告なく構造変更/IP ブロック | 外部検索ツール (4.2) が壊れる。**ただし公式ツール群 (4.1) には影響しない**（疎結合のため） | 失敗時は `SearchUnavailableError` を明示返却。公式 API には自動で逃げない。EPO OPS / WIPO PATENTSCOPE への切替を後日検討。 |
| JPO 認証情報の漏洩 | 利用権剥奪リスク | `.env` を Git 除外、`chmod 600`、ssh 鍵認証のみ、LAN バインド限定。 |
| LAN 内とはいえ無認証で MCP を晒す | 同 LAN 上の他端末から自由に叩かれる | Phase 1 では割り切り。Phase 2 で MCP 側に簡易トークン認証を追加。 |
| 日次上限 (`statusCode 203`) で当日不能になる | ツール呼び出しが失敗 | 24h SQLite キャッシュで重複アクセスを抑制。`remainAccessCount` をログに残し、閾値で警告。 |

---

## 13. 用語の補足（自分の説明用）

- **MCP**: Claude などの LLM クライアントから外部ツール/データを呼び出す共通プロトコル。HTTP+SSE か stdio で繋ぐ。
- **OAuth2 Password Grant**: ユーザー名+パスワードを直接 POST してアクセストークンをもらう方式（現代的な OAuth ではほぼ廃止だが、JPO API は採用）。
- **OPD**: One Portal Dossier。日米欧中韓の五庁ファミリー横断 API。
- **statusCode**: HTTP の 200/404 ではなく、JSON ボディの中にある JPO 独自の処理結果コード。

---

## 14. 次に着手するもの

S1〜S10 まで完了済み（2026-05-01 時点）= Phase 1A / 1B / 1.5 すべて完了。本リポジトリには動作する実装が入っている: `tools_official/` 12 ツール / `tools_external/` 1 ツール / OAuth 2.1 サーバー (SQLite 永続化) / アクセスログ + 集計スクリプト / Compose 構成。LAN 直結（hosts 経由）も疎通済み。

**Phase 2 (未着手)**:
1. 拒絶理由通知書 PDF の構造化抽出（旧 IP プロジェクト `../IP/web_app.py` のうち OCR / PDF パイプライン部分を移植）
2. AI レビューパイプライン（同上から移植）
3. EPO OPS / WIPO PATENTSCOPE 補完（外部キーワード検索の冗長化、`tools_external/` 内に独立追加）
